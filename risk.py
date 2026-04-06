"""
risk.py — PROSPER v2 Smart Risk Manager.
Balance-aware: adjusts risk/threshold/behavior based on account tier.
DD-aware: shifts mode from NORMAL → CAUTION → RECOVERY → PRESERVATION → KILL.
Per-volatility BE and trailing stop calculations.
"""
import asyncio
import logging
import math
from datetime import date, datetime, timezone
from typing import Optional, Tuple, Dict

from config import (
    INSTRUMENTS, ACCOUNT_TIERS, DD_MODES, MIN_LOT, MAX_LOT,
    MAX_CONCURRENT_TRADES, MAX_DAILY_LOSS_PCT, MAX_WEEKLY_LOSS_PCT,
    MAX_DRAWDOWN_PCT, GLOBAL_COOLDOWN_SECS, MIN_RR,
    ROUND_NUMBER_BUFFER, POINT_VALUE, get_account_tier, get_dd_mode,
    MIN_CONFLUENCE,
)

log = logging.getLogger(__name__)


class RiskState:
    def __init__(self, starting_balance: float):
        self.starting_balance    = starting_balance
        self.current_balance     = starting_balance
        self.daily_pnl           = 0.0
        self.weekly_pnl          = 0.0
        self.daily_reset_date    = date.today()
        self.weekly_reset_week   = datetime.now(timezone.utc).isocalendar()[1]
        self.recovery_mode       = False
        self.all_paused          = False
        self.pause_reason        = ""
        self.open_trades_count   = 0
        self.last_close_ts       = 0.0
        self.locked_symbol: Optional[str] = None

    def reset_daily(self):
        self.daily_pnl       = 0.0
        self.recovery_mode   = False
        self.daily_reset_date = date.today()

    def reset_weekly(self):
        self.weekly_pnl      = 0.0
        self.weekly_reset_week = datetime.now(timezone.utc).isocalendar()[1]

    @property
    def drawdown_pct(self) -> float:
        if self.starting_balance <= 0:
            return 0.0
        return max(0.0, (self.starting_balance - self.current_balance) / self.starting_balance)

    @property
    def tier(self) -> dict:
        return get_account_tier(self.current_balance)

    @property
    def dd_mode(self) -> dict:
        return get_dd_mode(self.drawdown_pct)


class RiskManager:
    def __init__(self, state: RiskState):
        self.state = state
        self._lock = asyncio.Lock()

    # ─── EFFECTIVE THRESHOLDS ────────────────────────────────────────────────
    def get_effective_min_score(self, sym: str) -> float:
        """
        Compute effective minimum score considering:
        - Per-instrument base threshold
        - Account tier adjustment
        - DD mode adjustment
        """
        base = float(INSTRUMENTS.get(sym, {}).get("min_score", MIN_CONFLUENCE))
        tier_adj = self.state.tier.get("score_bonus", 0)
        dd_adj   = self.state.dd_mode.get("score_add", 0)
        result   = base + dd_adj - tier_adj   # Small accounts get lower threshold
        return max(45.0, min(85.0, result))

    # ─── REGIME ───────────────────────────────────────────────────────────────
    def get_regime(self, atr14: float) -> str:
        from config import ATR_LOW_THRESHOLD, ATR_HIGH_THRESHOLD
        if atr14 < ATR_LOW_THRESHOLD: return "LOW"
        if atr14 > ATR_HIGH_THRESHOLD: return "HIGH"
        return "MEDIUM"

    # ─── POSITION SIZING ─────────────────────────────────────────────────────
    def calculate_lot(self, balance: float, sl_pts: float,
                      sym: str = "R_75") -> float:
        """
        Smart balance-aware position sizing.
        - Uses tier-based risk percentage
        - Scales down in DD mode
        - Never risks more than available balance can handle
        - Minimum viable lot size for small accounts
        """
        if sl_pts <= 0 or balance <= 0:
            return MIN_LOT

        # Base risk from tier
        base_risk_pct = self.state.tier.get("risk_pct", 0.02)
        # DD mode multiplier
        dd_mult = self.state.dd_mode.get("risk_mult", 1.0)
        effective_risk_pct = base_risk_pct * dd_mult

        risk_usd = balance * effective_risk_pct
        pv       = POINT_VALUE  # $1 per point per 1.0 lot

        lot = risk_usd / (sl_pts * pv)

        # Instrument-specific limits
        inst_max = INSTRUMENTS.get(sym, {}).get("max_lot", MAX_LOT)
        inst_min = INSTRUMENTS.get(sym, {}).get("min_lot", MIN_LOT)

        lot = max(inst_min, min(inst_max, math.floor(lot * 1000) / 1000))

        # Safety: verify actual risk doesn't exceed budget
        actual_risk = lot * sl_pts * pv
        if actual_risk > risk_usd * 1.05:
            lot = max(inst_min, lot - 0.001)

        # Small account safety: if single trade would risk >5% of balance
        if lot * sl_pts * pv > balance * 0.05:
            lot = max(inst_min, math.floor(balance * 0.04 / (sl_pts * pv) * 1000) / 1000)

        return round(max(MIN_LOT, lot), 3)

    # ─── SL/TP CALCULATION (PER VOLATILITY) ──────────────────────────────────
    def calculate_sl_tp(self, direction: str, entry: float,
                         atr14: float, regime: str, sym: str) -> Dict:
        """
        Per-volatility calibrated SL/TP.
        Incorporates spread + slippage budget.
        Enforces minimum R:R.
        Round-number avoidance.
        """
        inst = INSTRUMENTS.get(sym, INSTRUMENTS["R_75"])
        mult = inst["atr_mult"].get(regime, inst["atr_mult"]["MEDIUM"])

        spread  = inst.get("spread_pips", 0.5)
        slip    = inst.get("slippage_pips", 0.3)
        adj     = spread + slip   # Real-account cost buffer

        sl_dist  = atr14 * mult["sl"]  + adj
        tp1_dist = atr14 * mult["tp1"] - adj
        tp2_dist = atr14 * mult["tp2"] - adj
        tp3_dist = atr14 * mult["tp3"] - adj

        # Ensure positive distances
        tp1_dist = max(tp1_dist, sl_dist * 0.8)
        tp2_dist = max(tp2_dist, sl_dist * MIN_RR)
        tp3_dist = max(tp3_dist, sl_dist * (MIN_RR + 1.0))

        if direction == "BUY":
            sl=entry-sl_dist; tp1=entry+tp1_dist; tp2=entry+tp2_dist; tp3=entry+tp3_dist
        else:
            sl=entry+sl_dist; tp1=entry-tp1_dist; tp2=entry-tp2_dist; tp3=entry-tp3_dist

        # Round-number avoidance
        sl  = self._avoid_round(sl,  direction, "sl")
        tp1 = self._avoid_round(tp1, direction, "tp")
        tp2 = self._avoid_round(tp2, direction, "tp")
        tp3 = self._avoid_round(tp3, direction, "tp")

        sl_pts  = abs(entry - sl)
        tp1_pts = abs(tp1  - entry)
        tp2_pts = abs(tp2  - entry)
        tp3_pts = abs(tp3  - entry)
        rr2     = tp2_pts / sl_pts if sl_pts > 0 else 0
        rr3     = tp3_pts / sl_pts if sl_pts > 0 else 0

        return {
            "sl":sl,"tp1":tp1,"tp2":tp2,"tp3":tp3,
            "sl_pts":round(sl_pts,5),"tp1_pts":round(tp1_pts,5),
            "tp2_pts":round(tp2_pts,5),"tp3_pts":round(tp3_pts,5),
            "rr_tp2":round(rr2,2),"rr_tp3":round(rr3,2),
        }

    def _avoid_round(self, price: float, direction: str, tp_or_sl: str) -> float:
        """Avoid psychological round-number levels."""
        for denom in [1000, 500, 200, 100]:
            mag = round(price / denom) * denom
            if abs(price - mag) < ROUND_NUMBER_BUFFER:
                buf = ROUND_NUMBER_BUFFER * 1.5
                if direction == "BUY":
                    price = (mag + buf) if tp_or_sl == "tp" else (mag - buf)
                else:
                    price = (mag - buf) if tp_or_sl == "tp" else (mag + buf)
        return price

    # ─── PER-VOLATILITY BE / TRAIL ────────────────────────────────────────────
    def get_be_trigger(self, sym: str, atr14: float) -> float:
        """
        Break-even trigger distance (how far price must travel before BE).
        Per instrument calibration.
        V10: 0.3 ATR (tight — mean reverts fast)
        V25: 0.4 ATR
        V50: 0.5 ATR (regime-dependent)
        V75: 0.7 ATR (needs room to breathe — trending index)
        """
        mult = INSTRUMENTS.get(sym, {}).get("be_trigger_atr", 0.5)
        return atr14 * mult

    def get_trail_atr(self, sym: str, atr14: float) -> float:
        """Trail stop distance after TP2."""
        mult = INSTRUMENTS.get(sym, {}).get("trail_atr", 1.0)
        return atr14 * mult

    # ─── TRADING GATE ─────────────────────────────────────────────────────────
    def can_trade(self, sym: str) -> Tuple[bool, str]:
        """
        Master trading gate. All conditions must pass.
        Returns (allowed, reason_if_blocked).
        """
        s = self.state

        if s.all_paused:
            return False, f"⛔ Paused: {s.pause_reason}"

        # DD kill switch
        if s.dd_mode["label"] == "☠️ Kill Switch":
            return False, f"☠️ {MAX_DRAWDOWN_PCT:.0%} drawdown kill switch — all trading halted"

        # Monogamy protocol
        if s.locked_symbol and s.locked_symbol != sym:
            return False, f"🔒 Monogamy lock on {s.locked_symbol} — waiting"

        # Max concurrent
        if s.open_trades_count >= MAX_CONCURRENT_TRADES:
            return False, f"⏸ {MAX_CONCURRENT_TRADES} trade limit (Monogamy Protocol)"

        # Cooldown
        now = datetime.now(timezone.utc).timestamp()
        elapsed = now - s.last_close_ts
        if s.last_close_ts > 0 and elapsed < GLOBAL_COOLDOWN_SECS:
            remaining = int(GLOBAL_COOLDOWN_SECS - elapsed)
            return False, f"⏱ Cooldown: {remaining}s (anti-chop)"

        # Daily loss limit
        if s.current_balance > 0 and s.daily_pnl < 0:
            dloss = abs(s.daily_pnl) / s.current_balance
            if dloss >= MAX_DAILY_LOSS_PCT:
                s.all_paused   = True
                s.pause_reason = f"Daily loss {dloss:.1%} limit"
                return False, f"🛑 Daily loss limit {dloss:.1%} — stop trading today"

        # Weekly loss
        if s.current_balance > 0 and s.weekly_pnl < 0:
            wloss = abs(s.weekly_pnl) / s.current_balance
            if wloss >= MAX_WEEKLY_LOSS_PCT:
                return False, f"🛑 Weekly loss {wloss:.1%} limit"

        return True, ""

    def validate_rr(self, sl_pts: float, tp2_pts: float) -> bool:
        return (tp2_pts / sl_pts) >= MIN_RR if sl_pts > 0 else False

    def get_risk_usd(self, lot: float, sl_pts: float) -> float:
        return round(lot * sl_pts * POINT_VALUE, 2)

    def open_trade(self, sym: str):
        self.state.open_trades_count = min(self.state.open_trades_count + 1, MAX_CONCURRENT_TRADES)
        self.state.locked_symbol     = sym

    def close_trade(self, pnl: float):
        s = self.state
        s.open_trades_count   = max(0, s.open_trades_count - 1)
        if s.open_trades_count == 0:
            s.locked_symbol   = None
        s.daily_pnl           += pnl
        s.weekly_pnl          += pnl
        s.current_balance     += pnl
        s.last_close_ts        = datetime.now(timezone.utc).timestamp()
        # Auto-enter recovery if 3% daily loss
        if s.current_balance > 0 and s.daily_pnl < 0:
            if abs(s.daily_pnl) / s.current_balance >= 0.03 and not s.recovery_mode:
                s.recovery_mode = True
                log.warning("Recovery mode: daily loss ≥ 3% — halving risk")

    def pause(self, reason: str = "Manual"):
        self.state.all_paused   = True
        self.state.pause_reason = reason

    def resume(self):
        self.state.all_paused   = False
        self.state.pause_reason = ""

    def get_summary(self) -> dict:
        s = self.state
        tier = s.tier
        ddm  = s.dd_mode
        return {
            "balance":      s.current_balance,
            "daily_pnl":    s.daily_pnl,
            "weekly_pnl":   s.weekly_pnl,
            "drawdown_pct": s.drawdown_pct,
            "tier":         tier["label"],
            "risk_pct":     tier["risk_pct"] * ddm["risk_mult"],
            "dd_mode":      ddm["label"],
            "recovery":     s.recovery_mode,
            "paused":       s.all_paused,
            "locked":       s.locked_symbol,
            "open_trades":  s.open_trades_count,
        }
