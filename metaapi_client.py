"""
metaapi_client.py — PROSPER v2 MetaAPI Cloud trade execution.
MT5 login: 40963774 | Server: Deriv-Demo | Password: Otierics12@
"""
import asyncio
import logging
from typing import Optional, Dict, Callable
from datetime import datetime, timezone

from config import (
    METAAPI_TOKEN, METAAPI_ACCOUNT_ID,
    INSTRUMENTS, MIN_LOT, POINT_VALUE,
)

log = logging.getLogger(__name__)

try:
    from metaapi_cloud_sdk import MetaApi
    SDK_OK = True
except ImportError:
    SDK_OK = False
    log.warning("metaapi-cloud-sdk not installed — run: pip install metaapi-cloud-sdk")


class MetaAPIClient:
    def __init__(self, send_fn: Callable = None):
        self._api        = None
        self._account    = None
        self._conn       = None
        self._send       = send_fn
        self.connected   = False
        self.balance     = 0.0
        self.equity      = 0.0
        self._positions: Dict[str, dict] = {}
        self._lock       = asyncio.Lock()
        self._reconnect_attempts = 0

    # ─── CONNECTION ───────────────────────────────────────────────────────────
    async def connect(self) -> bool:
        if not SDK_OK:
            log.error("metaapi-cloud-sdk missing. Auto-execution disabled.")
            return False
        if not METAAPI_TOKEN:
            log.error("METAAPI_TOKEN empty. Check .env")
            return False
        try:
            log.info("Connecting to MetaAPI...")
            self._api     = MetaApi(METAAPI_TOKEN)
            self._account = await self._api.metatrader_account_api.get_account(
                METAAPI_ACCOUNT_ID
            )
            state = self._account.state
            log.info(f"Account state: {state}")
            if state not in ("DEPLOYED", "DEPLOYING"):
                log.info("Deploying MetaAPI terminal...")
                await self._account.deploy()

            log.info("Waiting for MT5 broker connection...")
            await self._account.wait_connected(timeout_in_seconds=120)

            self._conn = self._account.get_rpc_connection()
            await self._conn.connect()
            await self._conn.wait_synchronized(timeout_in_seconds=120)

            self.connected = True
            self._reconnect_attempts = 0
            await self._refresh()
            log.info(f"MetaAPI connected ✅ Balance=${self.balance:.2f}")
            return True

        except Exception as e:
            log.error(f"MetaAPI connect failed: {e}", exc_info=True)
            self.connected = False
            return False

    async def reconnect(self):
        self._reconnect_attempts += 1
        delays = [10, 30, 60, 120]
        delay  = delays[min(self._reconnect_attempts - 1, len(delays) - 1)]
        log.info(f"MetaAPI reconnecting in {delay}s (attempt {self._reconnect_attempts})")
        await asyncio.sleep(delay)
        ok = await self.connect()
        if ok and self._send:
            await self._send("✅ MetaAPI reconnected — trading resumed.")
        elif not ok and self._send:
            await self._send(
                f"🔴 MetaAPI reconnect attempt {self._reconnect_attempts} failed.\n"
                f"Check token/account. Will retry."
            )

    async def _refresh(self):
        try:
            info = await self._conn.get_account_information()
            self.balance = float(info.get("balance", 0))
            self.equity  = float(info.get("equity", self.balance))
        except Exception as e:
            log.debug(f"Balance refresh failed: {e}")

    # ─── SPREAD CHECK ─────────────────────────────────────────────────────────
    async def get_spread(self, mt5_sym: str) -> float:
        if not self.connected or not self._conn:
            return 0.0
        try:
            p = await self._conn.get_symbol_price(mt5_sym)
            if p:
                return float(p.get("ask", 0)) - float(p.get("bid", 0))
        except Exception:
            pass
        return 0.0

    async def get_price(self, mt5_sym: str) -> Optional[float]:
        if not self.connected:
            return None
        try:
            p = await self._conn.get_symbol_price(mt5_sym)
            if p:
                return (float(p.get("ask", 0)) + float(p.get("bid", 0))) / 2
        except Exception:
            pass
        return None

    # ─── PLACE MARKET ORDER ───────────────────────────────────────────────────
    async def place_market_order(
        self, sym: str, direction: str,
        lot: float, sl: float, tp1: float,
        signal_id: str,
    ) -> Optional[str]:
        if not self.connected or not self._conn:
            log.warning("MetaAPI not connected — order skipped")
            return None

        inst    = INSTRUMENTS.get(sym, {})
        mt5_sym = inst.get("mt5", sym)
        lot     = round(max(MIN_LOT, lot), 2)
        comment = f"PROSPER_{signal_id}"

        # Spread gate
        spread     = await self.get_spread(mt5_sym)
        max_spread = inst.get("spread_pips", 1.0) * 3
        if spread > max_spread > 0:
            log.warning(f"[{sym}] Spread {spread:.5f} > {max_spread:.5f} — order skipped")
            return None

        try:
            async with self._lock:
                if direction == "BUY":
                    result = await self._conn.create_market_buy_order(
                        symbol=mt5_sym, volume=lot,
                        stop_loss=sl, take_profit=tp1,
                        options={"comment": comment},
                    )
                else:
                    result = await self._conn.create_market_sell_order(
                        symbol=mt5_sym, volume=lot,
                        stop_loss=sl, take_profit=tp1,
                        options={"comment": comment},
                    )

            ticket = str(result.get("orderId") or result.get("positionId") or "")
            if ticket and ticket != "None":
                self._positions[ticket] = {
                    "sym": sym, "direction": direction, "lot": lot,
                    "sl": sl, "tp1": tp1, "signal_id": signal_id,
                    "opened_at": datetime.now(timezone.utc).isoformat(),
                }
                log.info(f"[{sym}] Order placed: {direction} {lot}lot @ ticket={ticket}")
                await self._refresh()
                return ticket
            else:
                log.error(f"[{sym}] No ticket in response: {result}")
                return None

        except Exception as e:
            log.error(f"[{sym}] Order failed: {e}", exc_info=True)
            return None

    # ─── PARTIAL CLOSE ────────────────────────────────────────────────────────
    async def partial_close(self, ticket: str, pct: float, signal_id: str) -> bool:
        if not self.connected:
            return False
        try:
            async with self._lock:
                positions = await self._conn.get_positions()
                pos = next((p for p in positions if str(p.get("id","")) == ticket), None)
                if pos is None:
                    log.warning(f"Position {ticket} not found for partial close")
                    return False
                vol  = float(pos.get("volume", 0))
                cvol = round(max(MIN_LOT, vol * pct), 2)
                await self._conn.close_position_partially(
                    position_id=ticket, volume=cvol,
                    options={"comment": f"PROSPER_PC_{signal_id}"},
                )
            log.info(f"Partial close {ticket}: {pct:.0%} ({cvol}lot)")
            await self._refresh()
            return True
        except Exception as e:
            log.error(f"Partial close {ticket} failed: {e}", exc_info=True)
            return False

    # ─── MODIFY SL / TP ───────────────────────────────────────────────────────
    async def modify_sl(self, ticket: str, new_sl: float) -> bool:
        if not self.connected:
            return False
        try:
            async with self._lock:
                await self._conn.modify_position(position_id=ticket, stop_loss=new_sl)
            log.info(f"SL modified {ticket} → {new_sl:.5f}")
            return True
        except Exception as e:
            log.error(f"Modify SL {ticket}: {e}")
            return False

    async def modify_tp(self, ticket: str, new_tp: float) -> bool:
        if not self.connected:
            return False
        try:
            async with self._lock:
                await self._conn.modify_position(position_id=ticket, take_profit=new_tp)
            return True
        except Exception as e:
            log.error(f"Modify TP {ticket}: {e}")
            return False

    # ─── CLOSE POSITION ───────────────────────────────────────────────────────
    async def close_position(self, ticket: str, signal_id: str = "") -> bool:
        if not self.connected:
            return False
        try:
            async with self._lock:
                await self._conn.close_position(
                    position_id=ticket,
                    options={"comment": f"PROSPER_CLOSE_{signal_id}"},
                )
            self._positions.pop(ticket, None)
            log.info(f"Position {ticket} closed")
            await self._refresh()
            return True
        except Exception as e:
            log.error(f"Close {ticket}: {e}", exc_info=True)
            return False

    # ─── STATUS ───────────────────────────────────────────────────────────────
    async def get_open_positions(self) -> list:
        if not self.connected:
            return []
        try:
            return await self._conn.get_positions()
        except Exception as e:
            log.warning(f"Get positions failed: {e}")
            return []

    async def position_exists(self, ticket: str) -> bool:
        positions = await self.get_open_positions()
        return any(str(p.get("id","")) == ticket for p in positions)

    async def get_balance(self) -> float:
        await self._refresh()
        return self.balance

    async def get_equity(self) -> float:
        await self._refresh()
        return self.equity
