"""
data_store.py — Thread-safe multi-symbol OHLC + tick store.
Uses ONLY closed candles (non-repainting guarantee).
Separate locks per symbol/TF for maximum concurrency.
"""
import asyncio
import logging
from typing import Dict, Optional
import pandas as pd
import numpy as np

from config import TIMEFRAMES, CANDLE_COUNT, ALL_SYMBOLS

log = logging.getLogger(__name__)


class DataStore:
    def __init__(self):
        # candles[sym][tf] = DataFrame
        self._candles: Dict[str, Dict[str, pd.DataFrame]] = {
            sym: {tf: pd.DataFrame() for tf in TIMEFRAMES}
            for sym in ALL_SYMBOLS
        }
        # Per symbol+TF locks
        self._locks: Dict[str, Dict[str, asyncio.Lock]] = {
            sym: {tf: asyncio.Lock() for tf in TIMEFRAMES}
            for sym in ALL_SYMBOLS
        }
        self._tick_lock   = asyncio.Lock()
        self._ticks:      Dict[str, float] = {s: 0.0 for s in ALL_SYMBOLS}
        self._tick_hist:  Dict[str, list]  = {s: []  for s in ALL_SYMBOLS}
        self._balance_lock = asyncio.Lock()
        self._balance: float  = 0.0
        self._equity:  float  = 0.0
        # Track which TFs have been initialized per symbol
        self._initialized: Dict[str, set] = {s: set() for s in ALL_SYMBOLS}

    # ─── TICK ─────────────────────────────────────────────────────────────────
    async def update_tick(self, sym: str, price: float) -> bool:
        """
        Store tick. Spike filter: reject if >3% move from last tick.
        Returns True if valid, False if spike.
        """
        if price <= 0 or sym not in ALL_SYMBOLS:
            return False
        async with self._tick_lock:
            hist = self._tick_hist.get(sym, [])
            if hist:
                last = hist[-1]
                if last > 0:
                    pct = abs(price - last) / last
                    if pct > 0.03:     # >3% = spike
                        log.warning(f"[{sym}] Spike rejected: {last:.5f}→{price:.5f} ({pct:.1%})")
                        return False
            self._ticks[sym] = price
            hist.append(price)
            if len(hist) > 100:
                hist.pop(0)
            self._tick_hist[sym] = hist
            return True

    async def get_tick(self, sym: str) -> float:
        async with self._tick_lock:
            return self._ticks.get(sym, 0.0)

    async def get_all_ticks(self) -> Dict[str, float]:
        async with self._tick_lock:
            return dict(self._ticks)

    # ─── CANDLES ──────────────────────────────────────────────────────────────
    async def init_candles(self, sym: str, tf: str, candles: list):
        """Load initial batch from Deriv API (ticks_history response)."""
        if sym not in ALL_SYMBOLS or tf not in TIMEFRAMES:
            return
        async with self._locks[sym][tf]:
            rows = []
            for c in candles:
                try:
                    rows.append({
                        "epoch":  int(c["epoch"]),
                        "open":   float(c["open"]),
                        "high":   float(c["high"]),
                        "low":    float(c["low"]),
                        "close":  float(c["close"]),
                        "volume": float(c.get("volume", 1)),
                    })
                except (KeyError, ValueError, TypeError):
                    continue
            if not rows:
                return
            df = pd.DataFrame(rows)
            df["dt"] = pd.to_datetime(df["epoch"], unit="s", utc=True)
            df = df.drop_duplicates("epoch").sort_values("dt").set_index("dt")
            self._candles[sym][tf] = df.tail(CANDLE_COUNT).copy()
            self._initialized[sym].add(tf)
            log.info(f"[{sym}][{tf}] Init: {len(self._candles[sym][tf])} candles")

    async def update_candle(self, sym: str, tf: str, ohlc: dict) -> bool:
        """
        Update or append an OHLC candle from a live 'ohlc' WebSocket message.
        Returns True ONLY if this is a brand-new closed candle (epoch changed).
        Anomaly filter: extreme wicks rejected.
        """
        if sym not in ALL_SYMBOLS or tf not in TIMEFRAMES:
            return False
        try:
            epoch = int(ohlc["epoch"])
            o = float(ohlc["open"]); h = float(ohlc["high"])
            lo = float(ohlc["low"]); c = float(ohlc["close"])
        except (KeyError, ValueError, TypeError):
            return False

        async with self._locks[sym][tf]:
            df = self._candles[sym][tf]
            dt = pd.to_datetime(epoch, unit="s", utc=True)

            # Anomaly check: reject extreme wick candles with no body
            wick = h - lo
            body = abs(c - o)
            atr_proxy = 200.0
            if len(df) >= 14:
                try:
                    atr_proxy = float(
                        (df["high"].iloc[-14:] - df["low"].iloc[-14:]).mean()
                    )
                except Exception:
                    pass
            if wick > 6 * max(atr_proxy, 5) and body < atr_proxy * 0.05:
                log.warning(f"[{sym}][{tf}] Anomalous candle skipped (wick={wick:.4f})")
                return False

            is_new = dt not in df.index
            new_row = pd.DataFrame([{
                "epoch": epoch, "open": o, "high": h,
                "low": lo, "close": c, "volume": float(ohlc.get("volume", 1)),
            }], index=[dt])

            if is_new:
                self._candles[sym][tf] = pd.concat([df, new_row]).tail(CANDLE_COUNT)
                if tf not in self._initialized.get(sym, set()):
                    self._initialized[sym].add(tf)
            else:
                # Update in-progress candle in-place
                for col in ["open", "high", "low", "close", "volume"]:
                    self._candles[sym][tf].at[dt, col] = new_row[col].iloc[0]
            return is_new

    async def get_candles(self, sym: str, tf: str, n: int = 200) -> pd.DataFrame:
        """
        Returns last N CLOSED candles only (excludes the current in-progress candle).
        This is the non-repainting guarantee.
        """
        if sym not in ALL_SYMBOLS or tf not in TIMEFRAMES:
            return pd.DataFrame()
        async with self._locks[sym][tf]:
            df = self._candles[sym][tf]
            if len(df) <= 1:
                return df.copy()
            # Exclude last row (may be open/partial)
            return df.iloc[:-1].tail(n).copy()

    async def has_enough(self, sym: str, tf: str, minimum: int = 50) -> bool:
        if sym not in ALL_SYMBOLS or tf not in TIMEFRAMES:
            return False
        async with self._locks[sym][tf]:
            return (
                tf in self._initialized.get(sym, set()) and
                len(self._candles[sym][tf]) >= minimum + 1
            )

    async def is_initialized(self, sym: str) -> bool:
        """True if M5 and M15 candles are loaded."""
        return (
            "M5"  in self._initialized.get(sym, set()) and
            "M15" in self._initialized.get(sym, set())
        )

    # ─── BALANCE / EQUITY ─────────────────────────────────────────────────────
    async def update_balance(self, balance: float, equity: float = 0.0):
        async with self._balance_lock:
            self._balance = balance
            self._equity  = equity if equity > 0 else balance

    async def get_balance(self) -> float:
        async with self._balance_lock:
            return self._balance

    async def get_equity(self) -> float:
        async with self._balance_lock:
            return self._equity

    # ─── DAILY RESET ──────────────────────────────────────────────────────────
    async def reset_daily(self):
        log.info("DataStore daily reset.")
