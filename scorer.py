"""
scorer.py — PROSPER v2 Confluence Scorer.
Aggregates strategy results + cross-TF + bonus points → final score.
Balance-aware threshold. 50% minimum for active trading.
"""
import logging
from typing import List, Dict
from strategies import SetupResult
from config import INSTRUMENTS, GRADE_THRESHOLDS, MIN_CONFLUENCE

log = logging.getLogger(__name__)


class ConfluenceScorer:

    def score(
        self,
        setups:   List[SetupResult],
        m5:       dict,
        m15:      dict,
        h1:       dict,
        h4:       dict,
        session:  str,
        regime:   str,
        sym:      str,
        min_score: float = None,
    ) -> Dict:

        if not setups:
            return self._empty(sym)

        buy_score  = sum(s.score for s in setups if s.direction == "BUY")
        sell_score = sum(s.score for s in setups if s.direction == "SELL")
        buy_count  = sum(1 for s in setups if s.direction == "BUY")
        sell_count = sum(1 for s in setups if s.direction == "SELL")

        if buy_score == 0 and sell_score == 0:
            return self._empty(sym)

        direction  = "BUY" if buy_score >= sell_score else "SELL"
        is_bull    = direction == "BUY"
        dir_setups = [s for s in setups if s.direction == direction]

        if not dir_setups:
            return self._empty(sym)

        # Base: best strategy score
        best     = max(dir_setups, key=lambda s: s.score)
        score    = best.score
        reasons  = list(best.reasons)

        # ── Multi-strategy alignment bonus ───────────────────────────────────
        aligned = buy_count if is_bull else sell_count
        if aligned >= 2:
            b = min((aligned - 1) * 4, 12)
            score += b; reasons.append(f"✅ {aligned} strategies aligned (+{b})")

        # ── Cross-timeframe confirmation ──────────────────────────────────────
        def htf_add(tf_ind: dict, label: str, pts: int):
            nonlocal score
            if not tf_ind or not tf_ind.get("ready"): return
            if is_bull:
                if (tf_ind.get("ema_stack_bull") or tf_ind.get("st_bull") or
                        tf_ind.get("ema_bull") or tf_ind.get("macd_bull")):
                    score += pts; reasons.append(f"✅ {label} bull aligned (+{pts})")
            else:
                if (tf_ind.get("ema_stack_bear") or tf_ind.get("st_bear") or
                        tf_ind.get("ema_bear") or tf_ind.get("macd_bear")):
                    score += pts; reasons.append(f"✅ {label} bear aligned (+{pts})")

        htf_add(m15, "M15", 4)
        htf_add(h1,  "H1",  5)
        htf_add(h4,  "H4",  6)

        # ── Session bonus ─────────────────────────────────────────────────────
        sp = {
            "london_ny_overlap": 6, "london_open": 5, "ny_open": 5,
            "london": 3, "ny": 3, "asian": 1,
        }.get(session, 1)
        score += sp; reasons.append(f"✅ Session: {session.replace('_',' ').title()} (+{sp})")

        # ── SMC triple confluence ─────────────────────────────────────────────
        smc = sum([
            bool(m5.get("at_bull_ob" if is_bull else "at_bear_ob")),
            bool(m5.get("fvg_bull"   if is_bull else "fvg_bear")),
            bool(m5.get("bos_bull"   if is_bull else "bos_bear")),
            bool(m5.get("fvg_bull"   if is_bull else "fvg_bear")),
        ])
        if smc >= 3: score += 8; reasons.append("✅ SMC triple (+8)")
        elif smc == 2: score += 4; reasons.append("✅ SMC double (+4)")
        elif smc == 1: score += 2; reasons.append("✅ SMC single (+2)")

        # ── Divergence stack bonus ─────────────────────────────────────────────
        div_stack = (
            m5.get("rsi_bull_div") and m5.get("stoch_bull_div") and m5.get("macd_bull")
        ) if is_bull else (
            m5.get("rsi_bear_div") and m5.get("stoch_bear_div") and m5.get("macd_bear")
        )
        if div_stack:
            score += 7; reasons.append("✅ Multi-indicator divergence stack (+7)")

        # ── WVF fear spike bonus ───────────────────────────────────────────────
        if is_bull and m5.get("wvf_spike"):
            score += 6; reasons.append("✅ WVF fear spike — capitulation (+6)")

        # ── Volume confirmation ───────────────────────────────────────────────
        if m5.get("volume_high") or m5.get("volume_surge"):
            score += 3; reasons.append("✅ Volume confirms (+3)")

        # ── Kill zone bonus ───────────────────────────────────────────────────
        from strategies import _kill_zone
        if _kill_zone():
            score += 4; reasons.append("✅ Kill zone — high probability window (+4)")

        # ── Grade and validity ────────────────────────────────────────────────
        inst_min  = min_score if min_score else INSTRUMENTS.get(sym, {}).get("min_score", MIN_CONFLUENCE)
        grade     = self._grade(score)
        valid     = score >= inst_min

        strats    = [s.strategy for s in dir_setups]
        return {
            "direction":          direction,
            "score":              round(score, 1),
            "grade":              grade,
            "reasons":            reasons,
            "signal_valid":       valid,
            "strategy":           strats[0] if strats else "Unknown",
            "supporting":         strats[1] if len(strats) > 1 else None,
            "aligned_strategies": aligned,
            "regime_threshold":   inst_min,
            "all_strategies":     strats,
        }

    def _grade(self, score: float) -> str:
        for grade, thresh in GRADE_THRESHOLDS:
            if score >= thresh:
                return grade
        return "F"

    def _empty(self, sym: str = "") -> dict:
        return {
            "direction": "NONE", "score": 0, "grade": "F",
            "reasons": [], "signal_valid": False,
            "strategy": "", "supporting": None,
            "aligned_strategies": 0,
            "regime_threshold": INSTRUMENTS.get(sym, {}).get("min_score", MIN_CONFLUENCE),
            "all_strategies": [],
        }
