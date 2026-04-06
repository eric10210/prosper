"""
main.py — PROSPER v2 Complete Autonomous Trading Engine.

Architecture:
  Deriv WS → DataStore → Indicators → Strategies → Scorer
  → Governor Gate → Risk Gate → Signal → MetaAPI Execution
  → Trade Manager → Journal → Telegram

Telegram collision safety:
  PROSPER uses PROSPER_BOT_TOKEN (its own bot).
  KiloClav uses its own separate token.
  They never share a polling loop, token, or update queue.
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

# ─── LOGGING ──────────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        RotatingFileHandler(
            "logs/prosper.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
# Silence noisy third-party loggers
for noisy in ("httpx", "telegram", "apscheduler", "hpack", "websockets"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

log = logging.getLogger("prosper.main")

# ─── IMPORTS ──────────────────────────────────────────────────────────────────
from config import (
    BOT_NAME, BOT_VERSION, ALL_SYMBOLS, INSTRUMENTS,
    ACCOUNT_BALANCE, HEARTBEAT_PORT, POINT_VALUE, MIN_LOT,
)
from data_store      import DataStore
from deriv_ws        import DerivWS
from indicators      import compute_all_indicators
from strategies      import run_strategies
from scorer          import ConfluenceScorer
from risk            import RiskManager, RiskState
from signals         import Signal, SignalTracker, SignalCardBuilder, next_signal_id
from trade_manager   import TradeManager
from metaapi_client  import MetaAPIClient
from telegram_bot    import ProsperTelegram
from journal         import Journal
from governor        import Governor
from watchdog        import Watchdog, start_heartbeat_server


class ProsperBot:
    def __init__(self):
        self.store      = DataStore()
        self.journal    = Journal()
        self.tracker    = SignalTracker()
        self.scorer     = ConfluenceScorer()
        self.builder    = SignalCardBuilder()
        self.governor   = Governor()
        self.tg         = ProsperTelegram()
        self.risk_state = RiskState(ACCOUNT_BALANCE)
        self.risk_mgr   = RiskManager(self.risk_state)
        self.meta       = MetaAPIClient(send_fn=self._send)
        self.deriv_ws   = DerivWS(self.store)
        self.trade_mgr  = TradeManager(
            tracker    = self.tracker,
            risk_mgr   = self.risk_mgr,
            meta       = self.meta,
            send_fn    = self._send,
            journal_fn = self._record_result,
        )
        self.watchdog   = Watchdog(
            send_fn      = self._send,
            get_stats_fn = self.journal.get_stats,
        )
        # State
        self._paused         = False
        self._risk_pct_override: float = 0.0   # 0 = use tier default
        self._last_scan_ts:  dict = {}   # sym → float (last scan timestamp)
        self._scan_cooldown  = 60        # Minimum seconds between scans per symbol

        self._wire_commands()

    # ─── INTERNAL SEND ────────────────────────────────────────────────────────
    async def _send(self, text: str) -> bool:
        return await self.tg.send(text)

    # ─── RESULT RECORDER ──────────────────────────────────────────────────────
    def _record_result(self, signal_id: str, result: str,
                       pnl_pts: float, pnl_usd: float,
                       r_mult: float, dur: float, notes: str = ""):
        self.journal.update_result(signal_id, result, pnl_pts, pnl_usd, r_mult, dur, notes)
        self.governor.record_result(result, r_mult, self.risk_state.current_balance)
        # Soft kill alert
        kill_on, _ = self.governor.is_soft_kill_active()
        if kill_on and self.governor.consecutive_losses == self.governor.SOFT_KILL_LOSSES:
            asyncio.create_task(self._send(self.governor.soft_kill_alert()))

    # ─── CANDLE CLOSE PIPELINE ────────────────────────────────────────────────
    async def _on_candle_close(self, sym: str, tf: str):
        """
        Main signal pipeline. Fires on every new closed M1/M5/M15 candle.
        Gates: paused → initialized → governor → risk → scan cooldown
        → compute indicators → run strategies → score → fire signal.
        """
        if self._paused:
            return
        # Only run full pipeline on M5 and M15 (not M1 — too noisy)
        if tf not in ("M5", "M15"):
            return

        self.watchdog.pulse()

        # Scan cooldown per symbol (prevent duplicate scans in same minute)
        now = datetime.now(timezone.utc).timestamp()
        if now - self._last_scan_ts.get(sym, 0) < self._scan_cooldown:
            return
        self._last_scan_ts[sym] = now

        # Skip if this symbol already has an active trade
        if self.risk_state.locked_symbol == sym:
            return

        # Wait for data to be ready
        if not await self.store.is_initialized(sym):
            return
        if not await self.store.has_enough(sym, "M5", 60):
            return

        # Governor gate
        gov_ok, gov_reason = self.governor.can_fire(sym)
        if not gov_ok:
            log.debug(f"[{sym}] Governor: {gov_reason}")
            return

        # Risk gate
        risk_ok, risk_reason = self.risk_mgr.can_trade(sym)
        if not risk_ok:
            log.debug(f"[{sym}] Risk: {risk_reason}")
            return

        # ── Fetch candles for all TFs ─────────────────────────────────────────
        m5_df  = await self.store.get_candles(sym, "M5",  200)
        m15_df = await self.store.get_candles(sym, "M15", 150)
        h1_df  = await self.store.get_candles(sym, "H1",  100)
        h4_df  = await self.store.get_candles(sym, "H4",  80)

        if m5_df is None or len(m5_df) < 60:
            return

        # ── Compute indicators ────────────────────────────────────────────────
        m5_ind  = compute_all_indicators(m5_df,  sym)
        m15_ind = compute_all_indicators(m15_df, sym) if len(m15_df) >= 60 else {"ready": False}
        h1_ind  = compute_all_indicators(h1_df,  sym) if len(h1_df)  >= 60 else {"ready": False}
        h4_ind  = compute_all_indicators(h4_df,  sym) if len(h4_df)  >= 60 else {"ready": False}

        if not m5_ind.get("ready"):
            return

        # ── Macro bias update ─────────────────────────────────────────────────
        self.governor.update_macro(h4_ind, h4_ind, h1_ind)

        # ── Session + regime ──────────────────────────────────────────────────
        session  = self.governor.get_session()
        atr14    = float(m5_ind.get("atr14") or 200)
        regime   = self.risk_mgr.get_regime(atr14)

        # ── Run strategies ────────────────────────────────────────────────────
        setups = run_strategies(m5_ind, sym)
        if not setups:
            return

        # ── Score ─────────────────────────────────────────────────────────────
        min_score  = self.risk_mgr.get_effective_min_score(sym)
        score_data = self.scorer.score(
            setups=setups, m5=m5_ind, m15=m15_ind,
            h1=h1_ind, h4=h4_ind,
            session=session, regime=regime,
            sym=sym, min_score=min_score,
        )

        if not score_data["signal_valid"]:
            return

        # ── Fire signal ───────────────────────────────────────────────────────
        await self._fire(sym, score_data, m5_ind, session, regime, atr14)

    # ─── FIRE SIGNAL ──────────────────────────────────────────────────────────
    async def _fire(self, sym: str, score_data: dict,
                    m5_ind: dict, session: str, regime: str, atr14: float):
        price   = await self.store.get_tick(sym)
        if price <= 0:
            return

        balance = self.risk_state.current_balance or ACCOUNT_BALANCE
        direction = score_data["direction"]

        # Build SL/TP levels
        levels = self.risk_mgr.calculate_sl_tp(direction, price, atr14, regime, sym)

        # Validate R:R
        if not self.risk_mgr.validate_rr(levels["sl_pts"], levels["tp2_pts"]):
            log.debug(f"[{sym}] R:R too low — skipped")
            return

        # Calculate lot size
        lot = self.risk_mgr.calculate_lot(balance, levels["sl_pts"], sym)

        # Apply compound multiplier if enabled
        mult = self.governor.compound_multiplier(
            self.risk_state.starting_balance, balance
        )
        if mult > 1.0:
            inst_max = INSTRUMENTS.get(sym, {}).get("max_lot", 5.0)
            lot = round(min(lot * mult, inst_max), 3)
            lot = max(MIN_LOT, lot)

        # Risk USD
        risk_usd = self.risk_mgr.get_risk_usd(lot, levels["sl_pts"])

        # Determine trade type from best strategy
        best_setup = max(
            [s for s in run_strategies(m5_ind, sym) if s.direction == direction],
            key=lambda s: s.score,
            default=None,
        )
        trade_type = best_setup.trade_type if best_setup else "INTRADAY"

        sig_id = next_signal_id(sym)
        sig    = Signal(
            signal_id  = sig_id,
            sym        = sym,
            direction  = direction,
            entry      = round(price, 5),
            sl         = levels["sl"],
            tp1        = levels["tp1"],
            tp2        = levels["tp2"],
            tp3        = levels["tp3"],
            sl_pts     = levels["sl_pts"],
            tp1_pts    = levels["tp1_pts"],
            tp2_pts    = levels["tp2_pts"],
            tp3_pts    = levels["tp3_pts"],
            lot        = lot,
            risk_usd   = risk_usd,
            score      = score_data["score"],
            grade      = score_data["grade"],
            strategy   = score_data["strategy"],
            supporting = score_data.get("supporting"),
            regime     = regime,
            atr14      = atr14,
            session    = session,
            reasons    = score_data["reasons"],
            balance    = balance,
            rr_tp2     = levels["rr_tp2"],
            rr_tp3     = levels["rr_tp3"],
            trade_type = trade_type,
            macro_context = self.governor.macro_context(direction),
        )

        await self.tracker.add(sig)
        self.journal.log_signal(sig)

        card = self.builder.build(sig)
        await self._send(card)
        log.info(
            f"[{sym}] SIGNAL {sig_id}: {direction} {score_data['grade']} "
            f"{score_data['score']:.1f}% | {score_data['strategy']}"
        )

    # ─── TICK HOOK ────────────────────────────────────────────────────────────
    async def _on_tick(self, sym: str, price: float):
        """Route ticks to trade manager for level monitoring."""
        atr = 200.0
        try:
            df = await self.store.get_candles(sym, "M5", 20)
            if df is not None and len(df) >= 14:
                from indicators import calc_atr
                a = calc_atr(df)
                atr = float(a.get("atr14") or 200)
        except Exception:
            pass
        await self.trade_mgr.on_price(sym, price, atr)

    # ─── BALANCE UPDATE ───────────────────────────────────────────────────────
    async def _on_balance(self, balance: float):
        self.risk_state.current_balance = balance
        if self.risk_state.starting_balance <= 0:
            self.risk_state.starting_balance = balance
        if self.governor.peak_equity <= 0:
            self.governor.peak_equity = balance
        self.governor.peak_equity = max(self.governor.peak_equity, balance)
        await self.store.update_balance(balance)

    # ─── EQUITY LOGGER ────────────────────────────────────────────────────────
    async def _log_equity(self):
        bal = self.risk_state.current_balance
        self.journal.log_equity(bal, bal, self.risk_state.daily_pnl)

    # ─── DAILY BRIEF ─────────────────────────────────────────────────────────
    async def _daily_brief(self) -> str:
        stats   = self.journal.get_stats(days=1)
        session = self.governor.get_session()
        bal     = self.risk_state.current_balance
        dd_mode = self.risk_state.dd_mode
        return (
            f"🌅 PROSPER DAILY BRIEF\n"
            f"{datetime.now(timezone.utc).strftime('%d/%m/%Y 07:05 UTC')}\n"
            f"{'─'*30}\n"
            f"Balance:    ${bal:.2f}\n"
            f"DD Mode:    {dd_mode['label']}\n"
            f"Macro:      {self.governor.macro_bias} ({self.governor.macro_score}/10)\n"
            f"Session:    {session.replace('_', ' ').title()}\n"
            f"{'─'*30}\n"
            f"Yesterday:  {stats.get('total',0)} signals | "
            f"{stats.get('wins',0)}W | ${stats.get('net_pnl',0):+.2f}\n"
            f"{'─'*30}\n"
            f"High-probability setups only. Stay patient. 🎯"
        )

    # ─── DAILY RESET ─────────────────────────────────────────────────────────
    async def _daily_reset(self):
        self.risk_state.reset_daily()
        await self.store.reset_daily()
        log.info("Daily reset complete.")

    # ─── TELEGRAM COMMAND WIRING ─────────────────────────────────────────────
    def _wire_commands(self):
        tg = self.tg

        async def status():
            actives   = await self.tracker.get_active()
            prices    = await self.store.get_all_ticks()
            bal       = self.risk_state.current_balance
            dd_mode   = self.risk_state.dd_mode
            tier      = self.risk_state.tier
            session   = self.governor.get_session()
            kz        = self.governor.is_kill_zone()
            soft_k, _  = self.governor.is_soft_kill_active()
            po3, _     = self.governor.is_po3_avoid()
            price_lines = "\n".join(
                f"  {INSTRUMENTS[s]['label']}: {p:.5f}"
                for s, p in prices.items() if p > 0
            )
            return (
                f"📡 PROSPER STATUS\n"
                f"{'─'*28}\n"
                f"Time:      {datetime.now(timezone.utc).strftime('%H:%M UTC')}\n"
                f"Session:   {session.replace('_',' ').title()}\n"
                f"Kill Zone: {'✅ YES' if kz else '❌ No'}\n"
                f"PO3:       {'🔴 HOLD' if po3 else '✅ Clear'}\n"
                f"Soft Kill: {'🔴 ACTIVE' if soft_k else '✅ Clear'}\n"
                f"{'─'*28}\n"
                f"Balance:   ${bal:.2f}\n"
                f"Daily P&L: ${self.risk_state.daily_pnl:+.2f}\n"
                f"DD Mode:   {dd_mode['label']}\n"
                f"Tier:      {tier['label']} | Risk: {tier['risk_pct']*dd_mode['risk_mult']*100:.1f}%\n"
                f"Locked:    {self.risk_state.locked_symbol or 'None'}\n"
                f"Open:      {len(actives)} trade(s)\n"
                f"Macro:     {self.governor.macro_bias} ({self.governor.macro_score}/10)\n"
                f"Paused:    {'Yes ⏸' if self._paused else 'No ▶️'}\n"
                f"{'─'*28}\n"
                f"Prices:\n{price_lines}"
            )

        async def signals():
            actives = await self.tracker.get_active()
            if not actives:
                return "No active signals/trades."
            out = []
            for s in actives:
                p = await self.store.get_tick(s.sym)
                float_pts = (p - (s.entry_actual or s.entry)) if s.direction == "BUY" \
                            else ((s.entry_actual or s.entry) - p)
                float_usd = round(s.lot * float_pts * POINT_VALUE, 2) if s.lot else 0
                out.append(
                    f"#{s.signal_id} {s.label} {s.direction} {s.grade} {s.score:.0f}%\n"
                    f"Status: {s.status} | Float: ${float_usd:+.2f}\n"
                    f"SL: {s.sl:.5f} | TP1: {s.tp1:.5f}"
                )
            return "\n\n".join(out)

        async def balance():
            meta_bal = await self.meta.get_balance() if self.meta.connected else 0
            meta_eq  = await self.meta.get_equity()  if self.meta.connected else 0
            tier     = self.risk_state.tier
            dd_mode  = self.risk_state.dd_mode
            return (
                f"💰 BALANCE\n"
                f"MT5 Balance:  ${meta_bal:.2f}\n"
                f"MT5 Equity:   ${meta_eq:.2f}\n"
                f"PROSPER Track:${self.risk_state.current_balance:.2f}\n"
                f"Today P&L:    ${self.risk_state.daily_pnl:+.2f}\n"
                f"Week P&L:     ${self.risk_state.weekly_pnl:+.2f}\n"
                f"Drawdown:     {self.risk_state.drawdown_pct:.1%}\n"
                f"DD Mode:      {dd_mode['label']}\n"
                f"Tier:         {tier['label']}\n"
                f"Eff. Risk:    {tier['risk_pct']*dd_mode['risk_mult']*100:.1f}%"
            )

        async def pause():
            self._paused = True
            self.risk_mgr.pause("Manual /pause")

        async def resume():
            self._paused = False
            self.risk_mgr.resume()

        async def weekly():
            return self.journal.weekly_report()

        async def journal_cmd(n):
            return self.journal.journal_text(n)

        async def kill(sid):
            sig = await self.tracker.get(sid)
            if not sig:
                return f"Signal #{sid} not found."
            if sig.mt5_ticket and sig.status in ("LIVE", "TP1", "TP2"):
                await self.meta.close_position(sig.mt5_ticket, sid)
                self.risk_mgr.close_trade(0)
            await self.tracker.update(sid, status="CLOSED")
            return f"✅ #{sid} killed."

        async def risk_cmd(pct):
            pct = max(0.5, min(5.0, pct))
            self._risk_pct_override = pct / 100
            return f"✅ Risk override: {pct:.1f}%"

        async def governor_cmd():
            return self.governor.status_text()

        async def compound_cmd(mode):
            if mode in ("on", "enable"):
                self.governor.compound_enabled = True
                return "✅ Compound mode ON."
            elif mode in ("off", "disable"):
                self.governor.compound_enabled = False
                return "❌ Compound mode OFF."
            else:
                self.governor.compound_enabled = not self.governor.compound_enabled
                return f"Compound {'ON' if self.governor.compound_enabled else 'OFF'}."

        async def closeall():
            positions = await self.meta.get_open_positions()
            closed = 0
            for pos in positions:
                ticket = str(pos.get("id", ""))
                if ticket:
                    await self.meta.close_position(ticket, "closeall")
                    closed += 1
            for sig in await self.tracker.get_active():
                await self.tracker.update(sig.signal_id, status="CLOSED")
                self.risk_mgr.close_trade(0)
            return f"✅ Closed {closed} MT5 position(s). All signals cleared."

        async def equity():
            bal   = self.risk_state.current_balance
            peak  = self.governor.peak_equity or bal
            dd    = self.governor.drawdown_from_peak(bal)
            return (
                f"📈 EQUITY OVERVIEW\n"
                f"Balance:     ${bal:.2f}\n"
                f"Peak Equity: ${peak:.2f}\n"
                f"DD vs Peak:  {dd:.1%}\n"
                f"DD Mode:     {self.risk_state.dd_mode['label']}\n"
                f"Sharpe(30):  {self.governor.sharpe():.2f}\n"
                f"Profit F:    {self.governor.profit_factor():.2f}\n"
                f"WR 20:       {self.governor.win_rate_last_20:.1f}%\n"
                f"WR 50:       {self.governor.win_rate_last_50:.1f}%\n"
                f"Best R:      +{self.governor.best_trade_r:.1f}R\n"
                f"Worst R:     {self.governor.worst_trade_r:.1f}R\n"
                f"Trades:      {self.governor.total_trades}"
            )

        async def stats_cmd(days):
            s = self.journal.get_stats(days)
            return (
                f"📊 STATS ({days}d)\n"
                f"Signals:  {s['total']}\n"
                f"Wins:     {s['wins']} ({s['win_rate']}%)\n"
                f"Losses:   {s['losses']}\n"
                f"Net P&L:  ${s['net_pnl']:+.2f}\n"
                f"PF:       {s['profit_factor']}\n"
                f"Avg R:    {s['avg_r']:.2f}R\n"
                f"Strategy: {s['best_strategy']}"
            )

        tg.on_status   = status
        tg.on_signals  = signals
        tg.on_balance  = balance
        tg.on_pause    = pause
        tg.on_resume   = resume
        tg.on_weekly   = weekly
        tg.on_journal  = journal_cmd
        tg.on_kill     = kill
        tg.on_risk     = risk_cmd
        tg.on_governor = governor_cmd
        tg.on_compound = compound_cmd
        tg.on_closeall = closeall
        tg.on_equity   = equity
        tg.on_stats    = stats_cmd

    # ─── STARTUP ──────────────────────────────────────────────────────────────
    async def start(self):
        # 1. Flask heartbeat server (background thread)
        start_heartbeat_server(port=HEARTBEAT_PORT)

        # 2. Send startup notification
        await self._send(
            f"🚀 {BOT_NAME} v{BOT_VERSION} STARTING\n"
            f"{'─'*30}\n"
            f"Markets: {' | '.join(v['label'] for v in INSTRUMENTS.values())}\n"
            f"Account: ${self.risk_state.current_balance:.2f}\n"
            f"Min Score: 50% (active trading mode)\n"
            f"Execution: MetaAPI → MT5\n"
            f"Time: {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')}\n"
            f"{'─'*30}\n"
            f"Connecting to Deriv & MetaAPI... ⏳"
        )

        # 3. Wire Deriv WS callbacks
        self.deriv_ws.on_candle_close = self._on_candle_close
        self.deriv_ws.on_tick         = self._on_tick
        self.deriv_ws.on_balance      = self._on_balance

        async def on_connected():
            log.info("Deriv WS fully connected — reconciling positions")
            await self.trade_mgr.reconcile()

        self.deriv_ws.on_connected = on_connected

        # 4. Connect MetaAPI
        meta_ok = await self.meta.connect()
        if meta_ok:
            balance = await self.meta.get_balance()
            equity  = await self.meta.get_equity()
            self.risk_state.current_balance  = balance
            self.risk_state.starting_balance = balance
            self.governor.peak_equity        = balance
            await self.store.update_balance(balance, equity)
            await self._send(
                f"✅ MetaAPI Connected\n"
                f"Login:   {40963774}\n"
                f"Server:  Deriv-Demo\n"
                f"Balance: ${balance:.2f}\n"
                f"Equity:  ${equity:.2f}\n"
                f"Status:  LIVE ✅"
            )
        else:
            await self._send(
                "⚠️ MetaAPI connection failed.\n"
                "Running in SIGNAL-ONLY mode.\n"
                "Trades will NOT execute automatically.\n"
                "Check METAAPI_TOKEN / METAAPI_ACCOUNT_ID in .env"
            )

        # 5. Build Telegram app
        self.tg.build_app()

        # 6. MetaAPI heartbeat (keep connection alive)
        async def meta_keepalive():
            while True:
                await asyncio.sleep(300)   # Every 5 min
                if self.meta.connected:
                    try:
                        await self.meta._refresh()
                        bal = self.meta.balance
                        if bal > 0:
                            self.risk_state.current_balance = bal
                    except Exception:
                        pass
                else:
                    await self.meta.reconnect()

        # 7. Daily reset task
        async def daily_reset_loop():
            while True:
                from datetime import timedelta
                now    = datetime.now(timezone.utc)
                target = now.replace(hour=0, minute=1, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                await asyncio.sleep((target - now).total_seconds())
                await self._daily_reset()

        # 8. Launch all tasks
        tasks = [
            asyncio.create_task(
                self.deriv_ws.run(alert_fn=self._send), name="deriv_ws"
            ),
            asyncio.create_task(
                self.tg.run_polling(), name="telegram"
            ),
            asyncio.create_task(
                self.watchdog.run_monitor(), name="watchdog"
            ),
            asyncio.create_task(
                self.watchdog.run_daily_heartbeat(), name="daily_hb"
            ),
            asyncio.create_task(
                self.watchdog.run_daily_brief(self._daily_brief), name="daily_brief"
            ),
            asyncio.create_task(
                self.watchdog.run_weekly_report(
                    lambda: asyncio.coroutine(
                        lambda: self.journal.weekly_report()
                    )()
                ), name="weekly"
            ),
            asyncio.create_task(
                self.watchdog.run_equity_log(self._log_equity), name="equity_log"
            ),
            asyncio.create_task(meta_keepalive(), name="meta_keepalive"),
            asyncio.create_task(daily_reset_loop(), name="daily_reset"),
        ]

        log.info(f"{'='*40}")
        log.info(f"  {BOT_NAME} v{BOT_VERSION} FULLY STARTED")
        log.info(f"  {len(ALL_SYMBOLS)} symbols | {len(tasks)} tasks")
        log.info(f"  Min score: 50% | Monogamy protocol active")
        log.info(f"{'='*40}")

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            log.info("PROSPER shutdown initiated.")
        except Exception as e:
            log.error(f"PROSPER fatal error: {e}", exc_info=True)
            try:
                await self._send(f"🔴 PROSPER FATAL ERROR\n{e}\nRestart required.")
            except Exception:
                pass
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            log.info("PROSPER shutdown complete.")


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info(f"Starting {BOT_NAME} v{BOT_VERSION}...")
    bot = ProsperBot()
    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt — shutting down PROSPER.")
