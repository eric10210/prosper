"""
trade_manager.py — PROSPER v2 Auto-Execution Trade Lifecycle Manager.
Per-volatility BE/trail calculations. Partial closes at TP1/TP2.
Parabolic trail after TP2. Smart DD management.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

from signals import Signal, SignalTracker, SignalCardBuilder
from risk import RiskManager
from config import SIGNAL_EXPIRY_MINS, SIGNAL_EXPIRY_EXT_MINS, ENTRY_BUFFER_PTS, POINT_VALUE

log = logging.getLogger(__name__)


class TradeManager:
    def __init__(self, tracker, risk_mgr, meta, send_fn, journal_fn):
        self.tracker = tracker
        self.risk    = risk_mgr
        self.meta    = meta
        self.send    = send_fn
        self.journal = journal_fn
        self.builder = SignalCardBuilder()
        self._live_update_ts: dict = {}
        self._live_interval  = 900   # 15 min

    # ─── MAIN PRICE HOOK ──────────────────────────────────────────────────────
    async def on_price(self, sym: str, price: float, atr: float = 200.0):
        """Called every significant tick for symbol."""
        sig = await self.tracker.get_by_sym(sym)
        if not sig:
            return
        try:
            if sig.status == "PENDING":
                await self._check_entry(sig, price, atr)
            elif sig.status in ("LIVE", "TP1", "TP2"):
                await self._check_levels(sig, price, atr)
                await self._maybe_live_update(sig, price)
        except Exception as e:
            log.error(f"[{sig.signal_id}] on_price error: {e}", exc_info=True)

    # ─── ENTRY ────────────────────────────────────────────────────────────────
    async def _check_entry(self, sig: Signal, price: float, atr: float):
        if sig.is_expired:
            await self._on_expired(sig, price); return

        # Entry extension: within ENTRY_BUFFER_PTS and not yet extended
        dist = abs(price - sig.entry)
        if dist <= ENTRY_BUFFER_PTS and not sig.extended:
            from datetime import timedelta
            new_exp = sig.expiry_at + timedelta(minutes=SIGNAL_EXPIRY_EXT_MINS)
            await self.tracker.update(sig.signal_id, extended=True, expiry_at=new_exp)

        # Entry triggered (market order — entry = current price on fill)
        hit = (
            (sig.direction == "BUY"  and price <= sig.entry * 1.0003) or
            (sig.direction == "SELL" and price >= sig.entry * 0.9997)
        )
        if hit:
            await self._execute_entry(sig, price)

    async def _execute_entry(self, sig: Signal, price: float):
        """Place market order via MetaAPI."""
        ticket = await self.meta.place_market_order(
            sym=sig.sym, direction=sig.direction,
            lot=sig.lot, sl=sig.sl, tp1=sig.tp1,
            signal_id=sig.signal_id,
        )
        if ticket:
            await self.tracker.update(sig.signal_id, status="LIVE",
                                       entry_actual=price, mt5_ticket=ticket)
            self.risk.open_trade(sig.sym)
            await self.send(self.builder.entry_alert(sig, price))
            log.info(f"[{sig.signal_id}] LIVE @ {price:.5f} ticket={ticket}")
        else:
            await self.tracker.update(sig.signal_id, status="EXPIRED")
            if self.risk.state.locked_symbol == sig.sym:
                self.risk.state.locked_symbol = None
            await self.send(
                f"⚠️ ENTRY FAILED — #{sig.signal_id} {sig.label}\n"
                f"MetaAPI rejected. Lock released. Check spread."
            )

    # ─── LEVEL MONITORING ─────────────────────────────────────────────────────
    async def _check_levels(self, sig: Signal, price: float, atr: float):
        sl = sig.sl_current or sig.sl

        # SL check (priority)
        sl_hit = (
            (sig.direction == "BUY"  and price <= sl) or
            (sig.direction == "SELL" and price >= sl)
        )
        if sl_hit:
            await (_on_trail_stop if sig.tp1_hit else self._on_sl_hit)(sig, price)
            return

        if not sig.tp1_hit:
            tp1_hit = (
                (sig.direction == "BUY"  and price >= sig.tp1) or
                (sig.direction == "SELL" and price <= sig.tp1)
            )
            if tp1_hit:
                await self._on_tp1(sig, price, atr)
            else:
                # Not yet TP1 — wait
                pass
            return

        if not sig.tp2_hit:
            tp2_hit = (
                (sig.direction == "BUY"  and price >= sig.tp2) or
                (sig.direction == "SELL" and price <= sig.tp2)
            )
            if tp2_hit:
                await self._on_tp2(sig, price, atr)
            else:
                await self._update_be_stop(sig, price, atr)
            return

        # After TP2 — trail to TP3
        tp3_hit = (
            (sig.direction == "BUY"  and price >= sig.tp3) or
            (sig.direction == "SELL" and price <= sig.tp3)
        )
        if tp3_hit:
            await self._on_tp3(sig, price)
        else:
            await self._update_trail_stop(sig, price, atr)

    # ─── BE MANAGEMENT (per-volatility) ──────────────────────────────────────
    async def _update_be_stop(self, sig: Signal, price: float, atr: float):
        """
        Per-volatility BE trigger:
        V10: +0.3 ATR, V25: +0.4 ATR, V50: +0.5 ATR, V75: +0.7 ATR
        """
        entry   = sig.entry_actual or sig.entry
        sl      = sig.sl_current or sig.sl
        be_dist = self.risk.get_be_trigger(sig.sym, atr)

        # BE trigger: price has moved be_dist in profit direction
        if sig.direction == "BUY":
            trigger = entry + be_dist
            moved   = price > trigger and sl < entry
        else:
            trigger = entry - be_dist
            moved   = price < trigger and sl > entry

        if moved and sig.mt5_ticket:
            be_price = entry  # BE = entry price
            ok = await self.meta.modify_sl(sig.mt5_ticket, be_price)
            if ok:
                await self.tracker.update(sig.signal_id, sl_current=be_price)
                await self.send(self.builder.be_alert(sig))
                log.info(f"[{sig.signal_id}] SL → BE @ {be_price:.5f}")

    # ─── TRAIL STOP (per-volatility) ─────────────────────────────────────────
    async def _update_trail_stop(self, sig: Signal, price: float, atr: float):
        """
        Parabolic trail after TP2.
        V75: 1.1 ATR trail, V10: 0.5 ATR trail.
        Trail only TIGHTENS — never loosens.
        """
        sl      = sig.sl_current or sig.sl
        trail_d = self.risk.get_trail_atr(sig.sym, atr)
        new_sl  = sl

        if sig.direction == "BUY":
            candidate = price - trail_d
            if candidate > sl + (atr * 0.05):   # Min 5% ATR improvement
                new_sl = candidate
        else:
            candidate = price + trail_d
            if candidate < sl - (atr * 0.05):
                new_sl = candidate

        if new_sl != sl and sig.mt5_ticket:
            ok = await self.meta.modify_sl(sig.mt5_ticket, new_sl)
            if ok:
                await self.tracker.update(sig.signal_id, sl_current=new_sl)

    # ─── TP EVENTS ────────────────────────────────────────────────────────────
    async def _on_tp1(self, sig: Signal, price: float, atr: float):
        tp1_pct, _, _ = sig.close_pcts()
        pnl_usd = round(sig.lot * sig.tp1_pts * POINT_VALUE * tp1_pct, 2)
        if sig.mt5_ticket:
            await self.meta.partial_close(sig.mt5_ticket, tp1_pct, sig.signal_id)
        await self.tracker.update(sig.signal_id, tp1_hit=True, status="TP1")
        await self.send(self.builder.tp1_alert(sig, price, pnl_usd))
        # Start BE management immediately after TP1
        await self._update_be_stop(sig, price, atr)

    async def _on_tp2(self, sig: Signal, price: float, atr: float):
        _, tp2_pct, _ = sig.close_pcts()
        pnl_usd = round(sig.lot * sig.tp2_pts * POINT_VALUE * tp2_pct, 2)
        if sig.mt5_ticket:
            await self.meta.partial_close(sig.mt5_ticket, tp2_pct, sig.signal_id)
            await self.meta.modify_tp(sig.mt5_ticket, sig.tp3)
        await self.tracker.update(sig.signal_id, tp2_hit=True, status="TP2")
        await self.send(self.builder.tp2_alert(sig, price, pnl_usd))

    async def _on_tp3(self, sig: Signal, price: float):
        pnl_pts = sig.tp3_pts
        pnl_usd = round(sig.lot * pnl_pts * POINT_VALUE, 2)
        dur     = (datetime.now(timezone.utc) - sig.created_at).total_seconds() / 60
        if sig.mt5_ticket:
            await self.meta.close_position(sig.mt5_ticket, sig.signal_id)
        await self.tracker.update(sig.signal_id, status="CLOSED", pnl_pts=pnl_pts, pnl_usd=pnl_usd)
        self.risk.close_trade(pnl_usd)
        self.journal(sig.signal_id, "win", pnl_pts, pnl_usd, sig.rr_tp3, dur, "TP3")
        await self.send(self.builder.win_alert(sig, price, pnl_pts, pnl_usd, dur))

    async def _on_sl_hit(self, sig: Signal, price: float):
        pnl_usd  = -sig.risk_usd
        dur      = (datetime.now(timezone.utc) - sig.created_at).total_seconds() / 60
        if sig.mt5_ticket:
            exists = await self.meta.position_exists(sig.mt5_ticket)
            if exists: await self.meta.close_position(sig.mt5_ticket, sig.signal_id)
        await self.tracker.update(sig.signal_id, status="CLOSED",
                                   pnl_pts=-sig.sl_pts, pnl_usd=pnl_usd)
        self.risk.close_trade(pnl_usd)
        self.journal(sig.signal_id, "loss", -sig.sl_pts, pnl_usd, -1.0, dur, "SL hit")
        await self.send(self.builder.sl_alert(sig, price))

    async def _on_trail_stop(self, sig: Signal, price: float):
        entry   = sig.entry_actual or sig.entry
        pts     = abs(price - entry)
        usd     = round(sig.lot * pts * POINT_VALUE, 2)
        r       = pts / sig.sl_pts if sig.sl_pts > 0 else 0
        dur     = (datetime.now(timezone.utc) - sig.created_at).total_seconds() / 60
        if sig.mt5_ticket:
            await self.meta.close_position(sig.mt5_ticket, sig.signal_id)
        await self.tracker.update(sig.signal_id, status="CLOSED")
        self.risk.close_trade(usd)
        self.journal(sig.signal_id, "partial", pts, usd, r, dur, "Trail stop after TP2")
        await self.send(self.builder.trail_alert(sig, price, usd, r))

    async def _on_expired(self, sig: Signal, price: float):
        await self.tracker.update(sig.signal_id, status="EXPIRED")
        if self.risk.state.locked_symbol == sig.sym:
            self.risk.state.locked_symbol = None
        self.journal(sig.signal_id, "expired", 0, 0, 0, SIGNAL_EXPIRY_MINS, "Not triggered")
        await self.send(self.builder.expired_alert(sig, price))

    async def _maybe_live_update(self, sig: Signal, price: float):
        now  = datetime.now(timezone.utc).timestamp()
        last = self._live_update_ts.get(sig.signal_id, 0)
        if now - last >= self._live_interval:
            self._live_update_ts[sig.signal_id] = now
            await self.send(self.builder.live_update(sig, price))

    async def reconcile(self):
        """On reconnect: reconcile open positions vs broker."""
        try:
            broker_pos = await self.meta.get_open_positions()
            broker_tix = {str(p.get("id","")) for p in broker_pos}
            for sig in await self.tracker.get_active():
                if sig.mt5_ticket and sig.status == "LIVE":
                    if sig.mt5_ticket not in broker_tix:
                        log.warning(f"[{sig.signal_id}] Not on broker — recording as closed")
                        await self.tracker.update(sig.signal_id, status="CLOSED")
                        self.risk.close_trade(-sig.risk_usd)
                        await self.send(
                            f"⚠️ RECONCILE: #{sig.signal_id} gone from broker.\n"
                            f"Recorded as closed. Risk released."
                        )
        except Exception as e:
            log.error(f"Reconcile error: {e}", exc_info=True)


# Closure for partial close callback reference
async def _on_trail_stop(self, sig, price):
    await TradeManager._on_trail_stop(self, sig, price)
