"""
governor.py — PROSPER v2 Governor Module.
Soft Kill Switch (3 consecutive losses → 4h pause).
PO3 / session-open 15-min filter.
Macro 3-tier HTF bias (D1+H4+H1) — reference only, never blocks.
Equity curve, streak tracking, Sharpe ratio, compound mode.
DD-aware trade gate (feeds into risk.py DD modes).
"""
import logging
import math
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List

log = logging.getLogger(__name__)

# ICT Silver Bullet windows (UTC hours)
SILVER_BULLET_HOURS = (3, 10, 14)

# Session open hours for PO3 filter
SESSION_OPENS = {"london_open": 7, "ny_open": 13, "asian": 0}
PO3_BLOCK_MINS = {"london_open": 15, "ny_open": 15, "asian": 10}


class Governor:
    def __init__(self):
        # ── Streak & performance ─────────────────────────────────────────────
        self.consecutive_losses:  int   = 0
        self.consecutive_wins:    int   = 0
        self.total_wins:          int   = 0
        self.total_losses:        int   = 0
        self.total_trades:        int   = 0
        self.trade_r_log:         List[float] = []   # last 50 R-multiples
        self.win_rate_last_20:    float = 0.0
        self.win_rate_last_50:    float = 0.0
        self.best_trade_r:        float = 0.0
        self.worst_trade_r:       float = 0.0

        # ── Soft Kill Switch ─────────────────────────────────────────────────
        self.soft_kill_active:    bool            = False
        self.soft_kill_until:     Optional[datetime] = None
        self.SOFT_KILL_LOSSES:    int             = 3
        self.SOFT_KILL_HOURS:     float           = 4.0

        # ── Macro 3-tier bias ────────────────────────────────────────────────
        self.macro_score:         int   = 5
        self.macro_bias:          str   = "NEUTRAL"
        self.d1_bias:             str   = "NEUTRAL"
        self.h4_bias:             str   = "NEUTRAL"
        self.h1_bias:             str   = "NEUTRAL"

        # ── Equity tracking ──────────────────────────────────────────────────
        self.equity_snapshots:    List[Tuple[str, float]] = []
        self.peak_equity:         float = 0.0

        # ── Compound mode ────────────────────────────────────────────────────
        self.compound_enabled:    bool  = False

    # ─── SESSION DETECTION ───────────────────────────────────────────────────
    @staticmethod
    def get_session() -> str:
        h = datetime.now(timezone.utc).hour
        if 12 <= h < 15: return "london_ny_overlap"
        if h == 7:        return "london_open"
        if 7 < h < 12:   return "london"
        if h == 13:       return "ny_open"
        if 13 < h < 20:  return "ny"
        if 0 <= h < 7:   return "asian"
        return "off_hours"

    @staticmethod
    def is_kill_zone() -> bool:
        return Governor.get_session() in (
            "london_open", "ny_open", "london_ny_overlap"
        )

    # ─── PO3 FILTER ──────────────────────────────────────────────────────────
    def is_po3_avoid(self) -> Tuple[bool, str]:
        """
        Block first N minutes of key session opens (PO3 Manipulation phase).
        Synthetic algos trap breakout traders in first 15 min of London/NY open.
        """
        now     = datetime.now(timezone.utc)
        session = self.get_session()
        block   = PO3_BLOCK_MINS.get(session, 0)
        if block == 0:
            return False, ""
        open_h = SESSION_OPENS.get(session, -1)
        if open_h < 0:
            return False, ""
        session_start = now.replace(
            hour=open_h, minute=0, second=0, microsecond=0
        )
        if now < session_start:
            session_start -= timedelta(days=1)
        elapsed = (now - session_start).total_seconds() / 60
        if 0 <= elapsed < block:
            rem = block - elapsed
            return True, (
                f"🕐 PO3: First {block}min of {session.replace('_', ' ').title()}. "
                f"Manipulation phase — wait {rem:.0f}min."
            )
        return False, ""

    # ─── SOFT KILL SWITCH ────────────────────────────────────────────────────
    def record_result(self, result: str, r_multiple: float, equity: float):
        """Update streaks, equity log, win rates. Trigger soft kill if needed."""
        self.total_trades += 1
        ts = datetime.now(timezone.utc).isoformat()
        self.equity_snapshots.append((ts, equity))
        if len(self.equity_snapshots) > 2000:
            self.equity_snapshots.pop(0)
        self.peak_equity = max(self.peak_equity, equity)
        self.trade_r_log.append(r_multiple)
        if len(self.trade_r_log) > 50:
            self.trade_r_log.pop(0)

        if result in ("win", "partial"):
            self.total_wins          += 1
            self.consecutive_wins    += 1
            self.consecutive_losses   = 0
            self.best_trade_r         = max(self.best_trade_r, r_multiple)
            if self.soft_kill_active:
                log.info("Soft kill deactivated — win recorded.")
                self.soft_kill_active = False
                self.soft_kill_until  = None
        elif result == "loss":
            self.total_losses        += 1
            self.consecutive_losses  += 1
            self.consecutive_wins     = 0
            self.worst_trade_r        = min(self.worst_trade_r, r_multiple)
            if (self.consecutive_losses >= self.SOFT_KILL_LOSSES
                    and not self.soft_kill_active):
                self.soft_kill_active = True
                self.soft_kill_until  = (
                    datetime.now(timezone.utc)
                    + timedelta(hours=self.SOFT_KILL_HOURS)
                )
                log.warning(
                    f"SOFT KILL: {self.consecutive_losses} consecutive losses. "
                    f"Paused until {self.soft_kill_until.strftime('%H:%M UTC')}"
                )

        # Rolling win rates
        rs = self.trade_r_log
        if len(rs) >= 20:
            self.win_rate_last_20 = sum(1 for r in rs[-20:] if r > 0) / 20 * 100
        if len(rs) >= 50:
            self.win_rate_last_50 = sum(1 for r in rs[-50:] if r > 0) / 50 * 100

    def is_soft_kill_active(self) -> Tuple[bool, str]:
        if not self.soft_kill_active:
            return False, ""
        now = datetime.now(timezone.utc)
        if self.soft_kill_until and now >= self.soft_kill_until:
            self.soft_kill_active    = False
            self.soft_kill_until     = None
            self.consecutive_losses  = 0
            log.info("Soft kill expired — resuming.")
            return False, ""
        rem = (self.soft_kill_until - now).total_seconds() / 3600
        return True, (
            f"🛑 SOFT KILL: {self.consecutive_losses} consecutive losses. "
            f"{rem:.1f}h remaining."
        )

    # ─── MASTER GATE ─────────────────────────────────────────────────────────
    def can_fire(self, sym: str) -> Tuple[bool, str]:
        """All Governor pre-signal checks. Returns (allowed, reason)."""
        kill_on, kill_msg = self.is_soft_kill_active()
        if kill_on:
            return False, kill_msg
        po3_on, po3_msg = self.is_po3_avoid()
        if po3_on:
            return False, po3_msg
        return True, ""

    # ─── MACRO 3-TIER BIAS ───────────────────────────────────────────────────
    def update_macro(self, d1: dict, h4: dict, h1: dict):
        """
        3-tier HTF voting: D1 + H4 + H1.
        Reference ONLY — never blocks a signal. Annotates card.
        """
        def bias(ind: dict) -> str:
            if not ind or not ind.get("ready"):
                return "NEUTRAL"
            bull = sum([
                bool(ind.get("ema_stack_bull")),
                bool(ind.get("st_bull")),
                bool(ind.get("macd_bull")),
                bool(ind.get("ichi_above_cloud")),
            ])
            bear = sum([
                bool(ind.get("ema_stack_bear")),
                bool(ind.get("st_bear")),
                bool(ind.get("macd_bear")),
                bool(ind.get("ichi_below_cloud")),
            ])
            if bull >= bear + 2: return "BULL"
            if bear >= bull + 2: return "BEAR"
            return "NEUTRAL"

        self.d1_bias = bias(d1)
        self.h4_bias = bias(h4)
        self.h1_bias = bias(h1)
        votes = [self.d1_bias, self.h4_bias, self.h1_bias]
        bv = votes.count("BULL"); rv = votes.count("BEAR")
        if bv >= 2:
            self.macro_bias  = "BULL"; self.macro_score = 6 + bv
        elif rv >= 2:
            self.macro_bias  = "BEAR"; self.macro_score = 6 + rv
        else:
            self.macro_bias  = "NEUTRAL"; self.macro_score = 5

    def macro_context(self, direction: str) -> str:
        aligned = (
            (direction == "BUY"  and self.macro_bias == "BULL") or
            (direction == "SELL" and self.macro_bias == "BEAR")
        )
        conflict = (
            (direction == "BUY"  and self.macro_bias == "BEAR") or
            (direction == "SELL" and self.macro_bias == "BULL")
        )
        tag = ("✅ MACRO ALIGNED" if aligned
               else ("⚠️ MACRO CONFLICT — Low Conviction" if conflict
                     else "🔵 MACRO NEUTRAL"))
        return (
            f"D1:{self.d1_bias} H4:{self.h4_bias} H1:{self.h1_bias} "
            f"({self.macro_score}/10) | {tag}"
        )

    # ─── COMPOUND MODE ───────────────────────────────────────────────────────
    def compound_multiplier(self, start_bal: float, cur_bal: float) -> float:
        if not self.compound_enabled or start_bal <= 0:
            return 1.0
        growth = cur_bal / start_bal
        return min(round(math.sqrt(growth), 2), 2.0)

    # ─── EQUITY ANALYTICS ────────────────────────────────────────────────────
    def drawdown_from_peak(self, cur: float) -> float:
        return (self.peak_equity - cur) / self.peak_equity if self.peak_equity > 0 else 0.0

    def sharpe(self) -> float:
        rs = self.trade_r_log[-30:] if len(self.trade_r_log) >= 5 else self.trade_r_log
        if len(rs) < 3:
            return 0.0
        arr = np.array(rs, dtype=float)
        std = np.std(arr)
        return round(float(np.mean(arr) / std) if std > 0 else 0.0, 2)

    def profit_factor(self) -> float:
        gross_win  = sum(r for r in self.trade_r_log if r > 0)
        gross_loss = abs(sum(r for r in self.trade_r_log if r < 0))
        return round(gross_win / gross_loss if gross_loss > 0 else gross_win, 2)

    # ─── STATUS TEXT ─────────────────────────────────────────────────────────
    def status_text(self) -> str:
        kill_on, kill_msg = self.is_soft_kill_active()
        po3_on,  po3_msg  = self.is_po3_avoid()
        wl  = self.total_wins + self.total_losses
        wr  = round(self.total_wins / wl * 100, 1) if wl > 0 else 0
        streak = (f"🔴 {self.consecutive_losses}L streak"
                  if self.consecutive_losses > 0
                  else f"🟢 {self.consecutive_wins}W streak")
        return (
            f"🏛️ GOVERNOR STATUS\n"
            f"{'─'*28}\n"
            f"Session:    {self.get_session().replace('_', ' ').title()}\n"
            f"Kill Zone:  {'✅ YES' if self.is_kill_zone() else '❌ No'}\n"
            f"PO3 Filter: {'🔴 HOLD — ' + po3_msg[:40] if po3_on else '✅ Clear'}\n"
            f"Soft Kill:  {'🔴 ' + kill_msg[:40] if kill_on else '✅ Clear'}\n"
            f"Macro:      {self.macro_bias} ({self.macro_score}/10)\n"
            f"HTF:        D1:{self.d1_bias} H4:{self.h4_bias} H1:{self.h1_bias}\n"
            f"{'─'*28}\n"
            f"Streak:     {streak}\n"
            f"Win Rate:   {wr}% ({self.total_wins}W/{self.total_losses}L)\n"
            f"WR 20:      {self.win_rate_last_20:.1f}%\n"
            f"WR 50:      {self.win_rate_last_50:.1f}%\n"
            f"Sharpe:     {self.sharpe():.2f}\n"
            f"PF:         {self.profit_factor():.2f}\n"
            f"Best R:     +{self.best_trade_r:.1f}R\n"
            f"Worst R:    {self.worst_trade_r:.1f}R\n"
            f"Compound:   {'✅ ON' if self.compound_enabled else '❌ OFF'}\n"
        )

    def soft_kill_alert(self) -> str:
        return (
            f"🛑 SOFT KILL ACTIVATED — PROSPER\n"
            f"{'─'*28}\n"
            f"Reason:  {self.SOFT_KILL_LOSSES} consecutive losses\n"
            f"Pause:   {self.SOFT_KILL_HOURS:.0f} hours\n"
            f"Until:   {self.soft_kill_until.strftime('%d/%m %H:%M UTC') if self.soft_kill_until else 'N/A'}\n"
            f"{'─'*28}\n"
            f"Revenge trading prevented. Capital protected. 🛡️\n"
            f"Bot auto-resumes after pause."
        )
