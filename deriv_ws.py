"""
deriv_ws.py — Deriv WebSocket handler for PROSPER v2.
Fixes: proper multi-symbol subscriptions, rate-limited sends,
candle-close callbacks per symbol/TF, robust reconnection.
"""
import asyncio
import json
import logging
import time
from typing import Callable, Dict, Optional, Set

import websockets
from websockets.exceptions import (
    ConnectionClosed, WebSocketException, ConnectionClosedError
)

from config import (
    DERIV_WS_URL, DERIV_API_KEY, ALL_SYMBOLS, TIMEFRAMES, CANDLE_COUNT
)
from data_store import DataStore

log = logging.getLogger(__name__)

RECONNECT_DELAYS   = [3, 5, 10, 20, 40, 60]
SEND_RATE_LIMIT_MS = 250       # 250ms between sends to avoid Deriv rate limits
SUBSCRIBE_TFS      = ["M1", "M5", "M15", "H1", "H4"]   # TFs to subscribe


class DerivWS:
    def __init__(self, store: DataStore):
        self.store         = store
        self.ws            = None
        self.connected     = False
        self.authenticated = False
        self._req_id       = 0
        self._send_lock    = asyncio.Lock()
        self._last_send_ts = 0.0
        self._retry        = 0
        self._subs_sent:   Set[str] = set()    # Track what we've subscribed to

        # Callbacks — set by main engine
        self.on_candle_close: Optional[Callable] = None   # async(sym, tf)
        self.on_tick:         Optional[Callable] = None   # async(sym, price)
        self.on_balance:      Optional[Callable] = None   # async(balance)
        self.on_connected:    Optional[Callable] = None   # async()

        # Track last epoch per sym/tf to detect new candles
        self._last_epoch: Dict[str, Dict[str, int]] = {
            sym: {tf: 0 for tf in SUBSCRIBE_TFS} for sym in ALL_SYMBOLS
        }

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _send(self, payload: dict):
        """Rate-limited send."""
        async with self._send_lock:
            now = time.monotonic()
            elapsed = (now - self._last_send_ts) * 1000
            if elapsed < SEND_RATE_LIMIT_MS:
                await asyncio.sleep((SEND_RATE_LIMIT_MS - elapsed) / 1000)
            if self.ws and not self.ws.closed:
                try:
                    await self.ws.send(json.dumps(payload))
                    self._last_send_ts = time.monotonic()
                except Exception as e:
                    log.warning(f"WS send failed: {e}")

    # ─── MAIN LOOP ────────────────────────────────────────────────────────────
    async def run(self, alert_fn: Callable = None):
        """Persistent connection with exponential backoff."""
        while True:
            try:
                log.info(f"DerivWS connecting (attempt {self._retry+1})...")
                async with websockets.connect(
                    DERIV_WS_URL,
                    ping_interval=25,
                    ping_timeout=15,
                    close_timeout=10,
                    max_size=2**22,         # 4MB message limit
                    open_timeout=30,
                ) as ws:
                    self.ws          = ws
                    self.connected   = True
                    self.authenticated = False
                    self._subs_sent.clear()
                    self._retry      = 0
                    log.info("DerivWS connected ✅")

                    await self._authenticate()
                    await self._subscribe_all()

                    if self.on_connected:
                        await self.on_connected()

                    await self._receive_loop()

            except (ConnectionClosed, ConnectionClosedError, WebSocketException) as e:
                self.connected     = False
                self.authenticated = False
                delay = RECONNECT_DELAYS[min(self._retry, len(RECONNECT_DELAYS) - 1)]
                self._retry += 1
                log.warning(f"DerivWS closed ({type(e).__name__}: {e}). Retry {self._retry} in {delay}s")
                if alert_fn and self._retry in (3, 6):
                    try:
                        await alert_fn(
                            f"⚠️ PROSPER: Market data reconnecting...\n"
                            f"Attempt {self._retry} — {delay}s wait"
                        )
                    except Exception:
                        pass
                await asyncio.sleep(delay)

            except asyncio.CancelledError:
                log.info("DerivWS task cancelled.")
                self.connected = False
                break
            except OSError as e:
                log.error(f"DerivWS OS error: {e}")
                await asyncio.sleep(10)
            except Exception as e:
                log.error(f"DerivWS unexpected: {e}", exc_info=True)
                await asyncio.sleep(5)

    # ─── AUTHENTICATION ───────────────────────────────────────────────────────
    async def _authenticate(self):
        await self._send({"authorize": DERIV_API_KEY, "req_id": self._next_id()})
        for _ in range(60):          # 30s timeout
            if self.authenticated:
                log.info("Deriv API authenticated ✅")
                return
            await asyncio.sleep(0.5)
        raise ConnectionError("Deriv authentication timed out after 30s")

    # ─── SUBSCRIPTIONS ────────────────────────────────────────────────────────
    async def _subscribe_all(self):
        """
        Subscribe to ticks + OHLC for all symbols × all TFs.
        Rate-limited with 250ms between sends.
        """
        # Balance subscription first
        await self._send({"balance": 1, "subscribe": 1, "req_id": self._next_id()})
        await asyncio.sleep(0.3)

        # Per symbol subscriptions
        for sym in ALL_SYMBOLS:
            # Live tick subscription
            await self._send({
                "ticks": sym,
                "subscribe": 1,
                "req_id": self._next_id(),
            })
            self._subs_sent.add(f"tick_{sym}")

            # OHLC subscriptions per timeframe
            for tf in SUBSCRIBE_TFS:
                gran = TIMEFRAMES[tf]
                req_id = self._next_id()
                await self._send({
                    "ticks_history": sym,
                    "adjust_start_time": 1,
                    "count":       CANDLE_COUNT,
                    "end":         "latest",
                    "granularity": gran,
                    "start":       1,
                    "style":       "candles",
                    "subscribe":   1,
                    "req_id":      req_id,
                })
                self._subs_sent.add(f"ohlc_{sym}_{tf}")

        log.info(
            f"Subscriptions sent: {len(ALL_SYMBOLS)} symbols × "
            f"{len(SUBSCRIBE_TFS)} TFs + ticks + balance"
        )

    async def resubscribe(self, sym: str, tf: str):
        """Re-subscribe to a specific symbol/TF (used after reconnect)."""
        gran = TIMEFRAMES.get(tf)
        if not gran:
            return
        await self._send({
            "ticks_history": sym,
            "adjust_start_time": 1,
            "count": CANDLE_COUNT,
            "end": "latest",
            "granularity": gran,
            "start": 1,
            "style": "candles",
            "subscribe": 1,
            "req_id": self._next_id(),
        })

    # ─── RECEIVE LOOP ─────────────────────────────────────────────────────────
    async def _receive_loop(self):
        async for raw in self.ws:
            try:
                msg = json.loads(raw)
                await self._dispatch(msg)
            except json.JSONDecodeError:
                log.debug("Invalid JSON from Deriv API")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error(f"Dispatch error: {e}", exc_info=True)

    async def _dispatch(self, msg: dict):
        mtype = msg.get("msg_type", "")

        # Error handling
        if "error" in msg:
            err   = msg["error"]
            code  = err.get("code", "UNKNOWN")
            text  = err.get("message", "unknown error")
            req_id = msg.get("req_id", "?")
            log.error(f"API error [{code}] req#{req_id}: {text}")
            if code in ("InvalidToken", "AuthorizationRequired"):
                raise ConnectionError(f"Auth failed: {text}")
            if code == "RateLimit":
                log.warning("Rate limited — pausing 2s")
                await asyncio.sleep(2)
            return

        if mtype == "authorize":
            self.authenticated = True

        elif mtype == "balance":
            bal_data = msg.get("balance", {})
            balance  = float(bal_data.get("balance", 0))
            equity   = float(bal_data.get("total_assets", balance))
            await self.store.update_balance(balance, equity)
            if self.on_balance:
                try:
                    await self.on_balance(balance)
                except Exception as e:
                    log.debug(f"on_balance error: {e}")

        elif mtype == "tick":
            tick  = msg.get("tick", {})
            sym   = tick.get("symbol", "")
            price = float(tick.get("quote", 0))
            if sym in ALL_SYMBOLS and price > 0:
                valid = await self.store.update_tick(sym, price)
                if valid and self.on_tick:
                    try:
                        await self.on_tick(sym, price)
                    except Exception as e:
                        log.debug(f"on_tick error [{sym}]: {e}")

        elif mtype == "candles":
            # Initial history load
            candles  = msg.get("candles", [])
            echo     = msg.get("echo_req", {})
            sym      = echo.get("ticks_history", "")
            gran     = int(echo.get("granularity", 300))
            tf       = self._gran_to_tf(gran)
            if sym in ALL_SYMBOLS and tf and candles:
                await self.store.init_candles(sym, tf, candles)
                # Track last epoch
                if candles:
                    self._last_epoch[sym][tf] = int(candles[-1]["epoch"])

        elif mtype == "ohlc":
            # Live candle update from subscription
            ohlc = msg.get("ohlc", {})
            sym  = ohlc.get("symbol", "")
            gran = int(ohlc.get("granularity", 300))
            tf   = self._gran_to_tf(gran)
            if not sym or sym not in ALL_SYMBOLS or not tf:
                return

            epoch  = int(ohlc.get("epoch", 0))
            is_new = await self.store.update_candle(sym, tf, ohlc)

            # Fire candle-close only for genuinely new candles on trading TFs
            if is_new and epoch != self._last_epoch[sym].get(tf, 0):
                self._last_epoch[sym][tf] = epoch
                if tf in ("M1", "M5", "M15") and self.on_candle_close:
                    try:
                        await self.on_candle_close(sym, tf)
                    except Exception as e:
                        log.debug(f"on_candle_close error [{sym}][{tf}]: {e}")

    def _gran_to_tf(self, gran: int) -> Optional[str]:
        return {v: k for k, v in TIMEFRAMES.items()}.get(gran)

    def get_status(self) -> dict:
        return {
            "connected":     self.connected,
            "authenticated": self.authenticated,
            "retry_count":   self._retry,
            "subs_count":    len(self._subs_sent),
        }
