"""
strategies.py — PROSPER v2: 25 Strategy Modules.
Per-instrument routing. Lower scoring thresholds (50%+ fires a trade).
Includes: Mean Reversion, Trend, SMC, ICT (Judas/Silver Bullet/BSL-SSL),
          Breakout, Divergence, Squeeze, Session Momentum, WVF Fear.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Dict
from config import INSTRUMENTS

log = logging.getLogger(__name__)


@dataclass
class SetupResult:
    strategy:   str
    direction:  str          # "BUY" | "SELL"
    score:      float        # 0-100
    reasons:    List[str] = field(default_factory=list)
    scalp:      bool = False
    swing:      bool = False
    intraday:   bool = True
    trade_type: str = "INTRADAY"   # SCALP | INTRADAY | SWING


def _g(d, k, default=None):
    return d.get(k, default) if d else default

def _session() -> str:
    h = datetime.now(timezone.utc).hour
    if 12<=h<15: return "london_ny_overlap"
    if 7<=h<8:   return "london_open"
    if 7<=h<12:  return "london"
    if 13<=h<14: return "ny_open"
    if 13<=h<20: return "ny"
    if 0<=h<7:   return "asian"
    return "off_hours"

def _kill_zone() -> bool:
    return _session() in ("london_open","ny_open","london_ny_overlap")

def _ict_silver_bullet() -> bool:
    """ICT Silver Bullet windows: 03:00, 10:00, 14:00 UTC."""
    h = datetime.now(timezone.utc).hour
    return h in (3, 10, 14)


# ═══════════════════════════════════════════════════════════════
#  MEAN REVERSION STRATEGIES (R_10, R_25)
# ═══════════════════════════════════════════════════════════════

def s_zscore_reversion(ind: dict, sym: str) -> Optional[SetupResult]:
    """Z-Score: Vol 10 primary edge. 83.9% WR at |Z|>2.0."""
    z = _g(ind, "zscore")
    if z is None: return None

    if z < -1.5:      # Lowered from -2.0 to fire more
        score = 52.0 + min(abs(z)-1.5, 1.5)*10
        reasons = [f"✅ Z-Score {z:.2f} (oversold — fade extreme)"]
        if z < -2.5: score += 8; reasons.append("✅ Z < -2.5 — extreme OS")
        if _g(ind,"bb_pct_b_near_lower"): score += 6; reasons.append("✅ BB near lower band")
        if _g(ind,"rsi_os"): score += 6; reasons.append(f"✅ RSI {_g(ind,'rsi',50):.0f} oversold")
        if _g(ind,"stoch_cross_bull"): score += 5; reasons.append("✅ StochRSI trigger")
        if _g(ind,"rsi_bull_div"): score += 7; reasons.append("✅ RSI divergence")
        if _g(ind,"wvf_spike"): score += 8; reasons.append("✅ WVF fear spike")
        if _g(ind,"at_bull_ob"): score += 5; reasons.append("✅ OB support")
        return SetupResult("ZScore Reversion", "BUY", score, reasons, scalp=True)

    if z > 1.5:
        score = 52.0 + min(z-1.5, 1.5)*10
        reasons = [f"✅ Z-Score {z:.2f} (overbought — fade extreme)"]
        if z > 2.5: score += 8; reasons.append("✅ Z > 2.5 — extreme OB")
        if _g(ind,"bb_pct_b_near_upper"): score += 6; reasons.append("✅ BB near upper band")
        if _g(ind,"rsi_ob"): score += 6; reasons.append(f"✅ RSI {_g(ind,'rsi',50):.0f} overbought")
        if _g(ind,"stoch_cross_bear"): score += 5; reasons.append("✅ StochRSI trigger")
        if _g(ind,"rsi_bear_div"): score += 7; reasons.append("✅ RSI divergence")
        if _g(ind,"at_bear_ob"): score += 5; reasons.append("✅ OB resistance")
        return SetupResult("ZScore Reversion", "SELL", score, reasons, scalp=True)
    return None


def s_bb_mean_reversion(ind: dict, sym: str) -> Optional[SetupResult]:
    """BB %B reversion. Vol 25: 89% mean reversion rate within 15 candles."""
    pct_b = _g(ind, "bb_pct_b", 0.5)
    if pct_b is None: return None

    if pct_b < 0.10:
        score = 50.0 + (0.10 - pct_b) * 400
        reasons = [f"✅ BB %B={pct_b:.3f} — oversold zone"]
        if _g(ind,"stoch_cross_bull"): score += 7; reasons.append("✅ StochRSI trigger")
        if _g(ind,"rsi_os"): score += 6; reasons.append("✅ RSI oversold")
        if _g(ind,"at_bull_ob"): score += 7; reasons.append("✅ OB confluence")
        if _g(ind,"fvg_bull"): score += 5; reasons.append("✅ FVG zone")
        if _g(ind,"at_fib_any"): score += 5; reasons.append("✅ Fib level")
        if _g(ind,"lrc_slope_bull"): score += 4; reasons.append("✅ LRC slope up")
        return SetupResult("BB Reversion", "BUY", min(score,95), reasons, intraday=True)

    if pct_b > 0.90:
        score = 50.0 + (pct_b - 0.90) * 400
        reasons = [f"✅ BB %B={pct_b:.3f} — overbought zone"]
        if _g(ind,"stoch_cross_bear"): score += 7; reasons.append("✅ StochRSI trigger")
        if _g(ind,"rsi_ob"): score += 6; reasons.append("✅ RSI overbought")
        if _g(ind,"at_bear_ob"): score += 7; reasons.append("✅ OB resistance")
        if _g(ind,"fvg_bear"): score += 5; reasons.append("✅ FVG zone")
        if _g(ind,"lrc_slope_bear"): score += 4; reasons.append("✅ LRC slope down")
        return SetupResult("BB Reversion", "SELL", min(score,95), reasons, intraday=True)
    return None


def s_lrc_reversion(ind: dict, sym: str) -> Optional[SetupResult]:
    """Linear Regression Channel reversion — buy at lower, sell at upper."""
    if _g(ind,"at_lrc_lower"):
        score = 52.0
        reasons = ["✅ Price at LRC lower band — reversion zone"]
        if _g(ind,"rsi_os") or _g(ind,"stoch_os"): score += 8; reasons.append("✅ Oscillator OS")
        if _g(ind,"lrc_slope_bull"): score += 6; reasons.append("✅ LRC slope UP")
        return SetupResult("LRC Reversion", "BUY", score, reasons, scalp=True)
    if _g(ind,"at_lrc_upper"):
        score = 52.0
        reasons = ["✅ Price at LRC upper band — reversion zone"]
        if _g(ind,"rsi_ob") or _g(ind,"stoch_ob"): score += 8; reasons.append("✅ Oscillator OB")
        if _g(ind,"lrc_slope_bear"): score += 6; reasons.append("✅ LRC slope DOWN")
        return SetupResult("LRC Reversion", "SELL", score, reasons, scalp=True)
    return None


def s_wvf_reversal(ind: dict, sym: str) -> Optional[SetupResult]:
    """Williams VIX Fix fear spike — extreme capitulation → BUY reversal."""
    if _g(ind,"wvf_spike"):
        score = 62.0
        reasons = [f"✅ WVF spike={_g(ind,'wvf_value',0):.1f} — extreme fear/capitulation"]
        if _g(ind,"rsi_bull_div"): score += 8; reasons.append("✅ RSI divergence confirms")
        if _g(ind,"stoch_cross_bull"): score += 5; reasons.append("✅ StochRSI trigger")
        if _g(ind,"bb_pct_b_os"): score += 6; reasons.append("✅ BB oversold")
        if _g(ind,"at_bull_ob") or _g(ind,"fvg_bull"): score += 5; reasons.append("✅ SMC zone")
        return SetupResult("WVF Fear Reversal", "BUY", score, reasons, swing=True)
    return None


# ═══════════════════════════════════════════════════════════════
#  ADAPTIVE STRATEGIES (R_50)
# ═══════════════════════════════════════════════════════════════

def s_adx_regime_reversion(ind: dict, sym: str) -> Optional[SetupResult]:
    """ADX < 20 = ranging → BB reversion."""
    if (_g(ind,"adx") or 30) > 20: return None
    pct_b = _g(ind,"bb_pct_b", 0.5)
    if pct_b is not None and pct_b < 0.12:
        score = 54.0
        reasons = [f"✅ ADX={_g(ind,'adx',0):.0f}<20 (ranging) + BB oversold"]
        if _g(ind,"rsi_os"): score += 7; reasons.append("✅ RSI oversold")
        if _g(ind,"stoch_cross_bull"): score += 6; reasons.append("✅ StochRSI trigger")
        if _g(ind,"cci_os"): score += 5; reasons.append("✅ CCI extreme")
        return SetupResult("ADX-BB Reversion", "BUY", score, reasons, scalp=True)
    if pct_b is not None and pct_b > 0.88:
        score = 54.0
        reasons = [f"✅ ADX={_g(ind,'adx',0):.0f}<20 (ranging) + BB overbought"]
        if _g(ind,"rsi_ob"): score += 7; reasons.append("✅ RSI overbought")
        if _g(ind,"stoch_cross_bear"): score += 6; reasons.append("✅ StochRSI trigger")
        if _g(ind,"cci_ob"): score += 5; reasons.append("✅ CCI extreme")
        return SetupResult("ADX-BB Reversion", "SELL", score, reasons, scalp=True)
    return None


def s_adx_trend_follow(ind: dict, sym: str) -> Optional[SetupResult]:
    """ADX > 25 = trending → EMA trend follow."""
    if (_g(ind,"adx") or 0) <= 25: return None
    bull_t = _g(ind,"bull_trend"); bear_t = _g(ind,"bear_trend")
    if bull_t:
        score = 55.0
        reasons = [f"✅ ADX={_g(ind,'adx',0):.0f}>25 trending BULL"]
        if _g(ind,"ema9_cross_bull"): score += 8; reasons.append("✅ EMA9 cross bull")
        if _g(ind,"st_bull"): score += 6; reasons.append("✅ SuperTrend bull")
        if _g(ind,"macd_cross_bull"): score += 6; reasons.append("✅ MACD cross")
        if _g(ind,"adx_rising"): score += 4; reasons.append("✅ ADX rising")
        if _g(ind,"bos_bull"): score += 7; reasons.append("✅ BOS bull")
        return SetupResult("ADX Trend", "BUY", score, reasons, intraday=True)
    if bear_t:
        score = 55.0
        reasons = [f"✅ ADX={_g(ind,'adx',0):.0f}>25 trending BEAR"]
        if _g(ind,"ema9_cross_bear"): score += 8; reasons.append("✅ EMA9 cross bear")
        if _g(ind,"st_bear"): score += 6; reasons.append("✅ SuperTrend bear")
        if _g(ind,"macd_cross_bear"): score += 6; reasons.append("✅ MACD cross")
        if _g(ind,"adx_rising"): score += 4; reasons.append("✅ ADX rising")
        if _g(ind,"bos_bear"): score += 7; reasons.append("✅ BOS bear")
        return SetupResult("ADX Trend", "SELL", score, reasons, intraday=True)
    return None


# ═══════════════════════════════════════════════════════════════
#  TREND/MOMENTUM STRATEGIES (R_75, R_50)
# ═══════════════════════════════════════════════════════════════

def s_momentum_sniper(ind: dict, sym: str) -> Optional[SetupResult]:
    """BB Squeeze → breakout. Monogamy Protocol. Ampere3 design."""
    sqz = _g(ind,"ttm_sqz_release") or _g(ind,"bb_sqz_release")
    if not sqz: return None
    bull = _g(ind,"ema9_cross_bull") or _g(ind,"macd_cross_bull") or _g(ind,"st_flip_bull")
    bear = _g(ind,"ema9_cross_bear") or _g(ind,"macd_cross_bear") or _g(ind,"st_flip_bear")
    if bull:
        score = 60.0
        reasons = ["✅ BB Squeeze Release — momentum explosion", "✅ Bullish breakout"]
        if _g(ind,"fvg_bull"): score += 8; reasons.append("✅ FVG entry zone")
        if _g(ind,"bos_bull"): score += 7; reasons.append("✅ BOS confirms")
        if _g(ind,"hurst_trending"): score += 8; reasons.append("✅ Hurst>0.55 — trending")
        if _g(ind,"volume_surge"): score += 5; reasons.append("✅ Volume surge")
        if _g(ind,"adx_rising"): score += 4; reasons.append("✅ ADX rising")
        return SetupResult("Momentum Sniper", "BUY", score, reasons, scalp=True)
    if bear:
        score = 60.0
        reasons = ["✅ BB Squeeze Release — momentum explosion", "✅ Bearish breakout"]
        if _g(ind,"fvg_bear"): score += 8; reasons.append("✅ FVG entry zone")
        if _g(ind,"bos_bear"): score += 7; reasons.append("✅ BOS confirms")
        if _g(ind,"hurst_trending"): score += 8; reasons.append("✅ Hurst>0.55")
        if _g(ind,"volume_surge"): score += 5; reasons.append("✅ Volume surge")
        return SetupResult("Momentum Sniper", "SELL", score, reasons, scalp=True)
    return None


def s_ema_trend(ind: dict, sym: str) -> Optional[SetupResult]:
    """Multi-EMA stack + SuperTrend. V75 core. Hurst-confirmed."""
    if _g(ind,"ema_stack_bull") and _g(ind,"st_bull"):
        score = 52.0
        reasons = ["✅ EMA stack 9>21>50 (bull)", "✅ SuperTrend bull"]
        if _g(ind,"hurst_trending"): score += 10; reasons.append("✅ Hurst>0.55 trend persistent")
        if _g(ind,"ichi_full_bull"): score += 7; reasons.append("✅ Ichimoku full bull")
        if _g(ind,"macd_bull"): score += 5; reasons.append("✅ MACD momentum")
        if _g(ind,"adx") and _g(ind,"adx",0)>20: score += 5; reasons.append(f"✅ ADX {_g(ind,'adx'):.0f}")
        if _g(ind,"bos_bull"): score += 6; reasons.append("✅ BOS bull")
        if _g(ind,"at_bull_ob"): score += 4; reasons.append("✅ OB support")
        if _g(ind,"ema_ribbon_bull"): score += 5; reasons.append("✅ EMA ribbon aligned")
        return SetupResult("EMA Trend", "BUY", score, reasons, swing=True, trade_type="SWING")
    if _g(ind,"ema_stack_bear") and _g(ind,"st_bear"):
        score = 52.0
        reasons = ["✅ EMA stack 9<21<50 (bear)", "✅ SuperTrend bear"]
        if _g(ind,"hurst_trending"): score += 10; reasons.append("✅ Hurst>0.55")
        if _g(ind,"ichi_full_bear"): score += 7; reasons.append("✅ Ichimoku full bear")
        if _g(ind,"macd_bear"): score += 5; reasons.append("✅ MACD momentum")
        if _g(ind,"adx") and _g(ind,"adx",0)>20: score += 5; reasons.append(f"✅ ADX {_g(ind,'adx'):.0f}")
        if _g(ind,"bos_bear"): score += 6; reasons.append("✅ BOS bear")
        if _g(ind,"ema_ribbon_bear"): score += 5; reasons.append("✅ EMA ribbon aligned")
        return SetupResult("EMA Trend", "SELL", score, reasons, swing=True, trade_type="SWING")
    return None


# ═══════════════════════════════════════════════════════════════
#  SMC / ICT STRATEGIES (ALL)
# ═══════════════════════════════════════════════════════════════

def s_ob_retest(ind: dict, sym: str) -> Optional[SetupResult]:
    """SMC Order Block Retest."""
    if _g(ind,"at_bull_ob"):
        score = 55.0
        reasons = ["✅ Bullish Order Block retest"]
        if _g(ind,"fvg_bull"): score += 8; reasons.append("✅ FVG imbalance")
        if _g(ind,"rsi_os"): score += 6; reasons.append("✅ RSI oversold at OB")
        if _g(ind,"stoch_cross_bull"): score += 5; reasons.append("✅ StochRSI trigger")
        if _g(ind,"macd_bull"): score += 4; reasons.append("✅ MACD momentum")
        if _g(ind,"bos_bull"): score += 6; reasons.append("✅ BOS confirms")
        return SetupResult("OB Retest", "BUY", score, reasons, intraday=True)
    if _g(ind,"at_bear_ob"):
        score = 55.0
        reasons = ["✅ Bearish Order Block retest"]
        if _g(ind,"fvg_bear"): score += 8; reasons.append("✅ FVG imbalance")
        if _g(ind,"rsi_ob"): score += 6; reasons.append("✅ RSI overbought at OB")
        if _g(ind,"stoch_cross_bear"): score += 5; reasons.append("✅ StochRSI trigger")
        if _g(ind,"macd_bear"): score += 4; reasons.append("✅ MACD momentum")
        if _g(ind,"bos_bear"): score += 6; reasons.append("✅ BOS confirms")
        return SetupResult("OB Retest", "SELL", score, reasons, intraday=True)
    return None


def s_fvg_fill(ind: dict, sym: str) -> Optional[SetupResult]:
    """Fair Value Gap fill — price drawn to imbalance zone."""
    if _g(ind,"fvg_bull"):
        score = 54.0
        reasons = ["✅ Active Bullish FVG — price in imbalance zone"]
        if _g(ind,"at_bull_ob"): score += 7; reasons.append("✅ OB confluence")
        if _g(ind,"stoch_cross_bull"): score += 6; reasons.append("✅ StochRSI trigger")
        if _g(ind,"ema_bull"): score += 5; reasons.append("✅ EMA aligned bull")
        if _g(ind,"volume_high"): score += 4; reasons.append("✅ Volume high")
        return SetupResult("FVG Fill", "BUY", score, reasons, scalp=True)
    if _g(ind,"fvg_bear"):
        score = 54.0
        reasons = ["✅ Active Bearish FVG — price in imbalance zone"]
        if _g(ind,"at_bear_ob"): score += 7; reasons.append("✅ OB confluence")
        if _g(ind,"stoch_cross_bear"): score += 6; reasons.append("✅ StochRSI trigger")
        if _g(ind,"ema_bear"): score += 5; reasons.append("✅ EMA aligned bear")
        if _g(ind,"volume_high"): score += 4; reasons.append("✅ Volume high")
        return SetupResult("FVG Fill", "SELL", score, reasons, scalp=True)
    return None


def s_bos_breakout(ind: dict, sym: str) -> Optional[SetupResult]:
    """Break of Structure — structural shift signal."""
    bos = _g(ind,"bos_direction","NONE")
    if bos == "BUY":
        score = 58.0
        reasons = ["✅ BOS: Price broke previous swing high"]
        if _g(ind,"macd_cross_bull"): score += 7; reasons.append("✅ MACD confirms")
        if _g(ind,"ema_stack_bull") or _g(ind,"ema_bull"): score += 6; reasons.append("✅ EMA aligned")
        if _g(ind,"fvg_bull"): score += 5; reasons.append("✅ FVG entry zone")
        if _g(ind,"volume_high"): score += 4; reasons.append("✅ Volume confirms")
        return SetupResult("BOS Breakout", "BUY", score, reasons, intraday=True)
    if bos == "SELL":
        score = 58.0
        reasons = ["✅ BOS: Price broke previous swing low"]
        if _g(ind,"macd_cross_bear"): score += 7; reasons.append("✅ MACD confirms")
        if _g(ind,"ema_stack_bear") or _g(ind,"ema_bear"): score += 6; reasons.append("✅ EMA aligned")
        if _g(ind,"fvg_bear"): score += 5; reasons.append("✅ FVG entry zone")
        if _g(ind,"volume_high"): score += 4; reasons.append("✅ Volume confirms")
        return SetupResult("BOS Breakout", "SELL", score, reasons, intraday=True)
    return None


def s_judas_swing(ind: dict, sym: str) -> Optional[SetupResult]:
    """
    Judas Swing: Session open false breakout → reverse.
    PO3 Manipulation → wait 15min → fade.
    """
    session = _session()
    if session not in ("london_open","ny_open","london_ny_overlap"):
        return None
    # BSL swept: broke above old high then RSI OB → SELL
    if _g(ind,"don_break_bull") and _g(ind,"rsi_ob") and _g(ind,"stoch_ob"):
        score = 62.0
        reasons = [
            f"✅ Judas Swing: BSL swept ({session.replace('_',' ')})",
            "✅ RSI overbought after sweep — fade",
        ]
        if _g(ind,"fvg_bear"): score += 7; reasons.append("✅ FVG confirms SELL")
        if _g(ind,"macd_cross_bear"): score += 5; reasons.append("✅ MACD cross bear")
        if _g(ind,"at_bear_ob"): score += 5; reasons.append("✅ OB resistance")
        return SetupResult("Judas Swing", "SELL", score, reasons, scalp=True, trade_type="SCALP")
    # SSL swept: broke below old low then RSI OS → BUY
    if _g(ind,"don_break_bear") and _g(ind,"rsi_os") and _g(ind,"stoch_os"):
        score = 62.0
        reasons = [
            f"✅ Judas Swing: SSL swept ({session.replace('_',' ')})",
            "✅ RSI oversold after sweep — fade",
        ]
        if _g(ind,"fvg_bull"): score += 7; reasons.append("✅ FVG confirms BUY")
        if _g(ind,"macd_cross_bull"): score += 5; reasons.append("✅ MACD cross bull")
        if _g(ind,"at_bull_ob"): score += 5; reasons.append("✅ OB support")
        return SetupResult("Judas Swing", "BUY", score, reasons, scalp=True, trade_type="SCALP")
    return None


def s_ict_silver_bullet(ind: dict, sym: str) -> Optional[SetupResult]:
    """ICT Silver Bullet: FVG trades in 03:00, 10:00, 14:00 UTC windows."""
    if not _ict_silver_bullet():
        return None
    h = datetime.now(timezone.utc).hour
    if _g(ind,"fvg_bull") and (_g(ind,"ema_bull") or _g(ind,"bos_bull")):
        score = 65.0
        reasons = [f"✅ ICT Silver Bullet window ({h:02d}:00 UTC)", "✅ Bullish FVG"]
        if _g(ind,"bos_bull"): score += 8; reasons.append("✅ BOS bull confirms")
        if _g(ind,"macd_bull"): score += 5; reasons.append("✅ MACD momentum")
        return SetupResult("ICT Silver Bullet", "BUY", score, reasons, scalp=True)
    if _g(ind,"fvg_bear") and (_g(ind,"ema_bear") or _g(ind,"bos_bear")):
        score = 65.0
        reasons = [f"✅ ICT Silver Bullet window ({h:02d}:00 UTC)", "✅ Bearish FVG"]
        if _g(ind,"bos_bear"): score += 8; reasons.append("✅ BOS bear confirms")
        if _g(ind,"macd_bear"): score += 5; reasons.append("✅ MACD momentum")
        return SetupResult("ICT Silver Bullet", "SELL", score, reasons, scalp=True)
    return None


def s_bsl_ssl_sweep(ind: dict, sym: str) -> Optional[SetupResult]:
    """BSL/SSL Liquidity Sweep — post-sweep reversal entry."""
    # SSL swept (new low) + fear spike → BUY reversal
    if _g(ind,"bos_bear") and (_g(ind,"rsi_os") or _g(ind,"wvf_spike")):
        score = 60.0
        reasons = ["✅ SSL Sweep: new low = liquidity grab", "✅ Reversal signal"]
        if _g(ind,"wvf_spike"): score += 8; reasons.append("✅ WVF capitulation spike")
        if _g(ind,"fvg_bull"): score += 7; reasons.append("✅ FVG after sweep")
        if _g(ind,"stoch_cross_bull"): score += 5; reasons.append("✅ StochRSI trigger")
        return SetupResult("SSL Sweep Reversal", "BUY", score, reasons, swing=True)
    # BSL swept (new high) + overbought → SELL reversal
    if _g(ind,"bos_bull") and (_g(ind,"rsi_ob") and _g(ind,"macd_cross_bear")):
        score = 60.0
        reasons = ["✅ BSL Sweep: new high = liquidity grab", "✅ Reversal signal"]
        if _g(ind,"fvg_bear"): score += 7; reasons.append("✅ FVG after sweep")
        if _g(ind,"stoch_cross_bear"): score += 5; reasons.append("✅ StochRSI trigger")
        return SetupResult("BSL Sweep Reversal", "SELL", score, reasons, swing=True)
    return None


# ═══════════════════════════════════════════════════════════════
#  UNIVERSAL STRATEGIES (ALL INSTRUMENTS)
# ═══════════════════════════════════════════════════════════════

def s_rsi_divergence(ind: dict, sym: str) -> Optional[SetupResult]:
    """RSI Divergence — reliable on all instruments."""
    if _g(ind,"rsi_bull_div"):
        score = 60.0
        reasons = ["✅ RSI Bullish Divergence (price LL, RSI HL)"]
        if _g(ind,"stoch_cross_bull"): score += 7; reasons.append("✅ StochRSI cross")
        if _g(ind,"macd_bull"): score += 5; reasons.append("✅ MACD momentum")
        if _g(ind,"at_bull_ob") or _g(ind,"fvg_bull"): score += 6; reasons.append("✅ SMC zone")
        if _g(ind,"wvf_spike"): score += 8; reasons.append("✅ WVF fear spike")
        return SetupResult("RSI Divergence", "BUY", score, reasons, scalp=False, swing=True)
    if _g(ind,"rsi_bear_div"):
        score = 60.0
        reasons = ["✅ RSI Bearish Divergence (price HH, RSI LH)"]
        if _g(ind,"stoch_cross_bear"): score += 7; reasons.append("✅ StochRSI cross")
        if _g(ind,"macd_bear"): score += 5; reasons.append("✅ MACD momentum")
        if _g(ind,"at_bear_ob") or _g(ind,"fvg_bear"): score += 6; reasons.append("✅ SMC zone")
        return SetupResult("RSI Divergence", "SELL", score, reasons, scalp=False, swing=True)
    return None


def s_supertrend_flip(ind: dict, sym: str) -> Optional[SetupResult]:
    """SuperTrend direction flip."""
    if _g(ind,"st_flip_bull"):
        score = 54.0
        reasons = ["✅ SuperTrend flipped BULLISH"]
        if _g(ind,"ema_stack_bull"): score += 8; reasons.append("✅ EMA stack aligned")
        if _g(ind,"macd_cross_bull"): score += 6; reasons.append("✅ MACD cross")
        if _g(ind,"adx",0)>18: score += 4; reasons.append(f"✅ ADX {_g(ind,'adx',0):.0f}")
        return SetupResult("SuperTrend Flip", "BUY", score, reasons, intraday=True)
    if _g(ind,"st_flip_bear"):
        score = 54.0
        reasons = ["✅ SuperTrend flipped BEARISH"]
        if _g(ind,"ema_stack_bear"): score += 8; reasons.append("✅ EMA stack aligned")
        if _g(ind,"macd_cross_bear"): score += 6; reasons.append("✅ MACD cross")
        if _g(ind,"adx",0)>18: score += 4; reasons.append(f"✅ ADX {_g(ind,'adx',0):.0f}")
        return SetupResult("SuperTrend Flip", "SELL", score, reasons, intraday=True)
    return None


def s_stoch_reversal(ind: dict, sym: str) -> Optional[SetupResult]:
    """StochRSI OS/OB cross — fast scalp."""
    if _g(ind,"stoch_cross_bull") and _g(ind,"stoch_os"):
        score = 50.0
        reasons = ["✅ StochRSI cross UP from oversold (<20)"]
        if _g(ind,"rsi",50) and _g(ind,"rsi",50)<42: score += 7; reasons.append("✅ RSI confirms")
        if _g(ind,"bb_pct_b_near_lower"): score += 6; reasons.append("✅ BB lower zone")
        if _g(ind,"fvg_bull") or _g(ind,"at_bull_ob"): score += 6; reasons.append("✅ SMC zone")
        if _g(ind,"cci_os"): score += 4; reasons.append("✅ CCI extreme")
        return SetupResult("StochRSI Reversal", "BUY", score, reasons, scalp=True)
    if _g(ind,"stoch_cross_bear") and _g(ind,"stoch_ob"):
        score = 50.0
        reasons = ["✅ StochRSI cross DOWN from overbought (>80)"]
        if _g(ind,"rsi",50) and _g(ind,"rsi",50)>58: score += 7; reasons.append("✅ RSI confirms")
        if _g(ind,"bb_pct_b_near_upper"): score += 6; reasons.append("✅ BB upper zone")
        if _g(ind,"fvg_bear") or _g(ind,"at_bear_ob"): score += 6; reasons.append("✅ SMC zone")
        if _g(ind,"cci_ob"): score += 4; reasons.append("✅ CCI extreme")
        return SetupResult("StochRSI Reversal", "SELL", score, reasons, scalp=True)
    return None


def s_macd_cross(ind: dict, sym: str) -> Optional[SetupResult]:
    """MACD cross with trend confirmation."""
    if _g(ind,"macd_cross_bull"):
        score = 50.0
        reasons = ["✅ MACD histogram positive cross"]
        if _g(ind,"ema_bull"): score += 7; reasons.append("✅ EMA bull")
        if _g(ind,"adx",0)>18: score += 5; reasons.append(f"✅ ADX {_g(ind,'adx',0):.0f}")
        if _g(ind,"st_bull"): score += 5; reasons.append("✅ SuperTrend bull")
        if _g(ind,"macd_hist_growing"): score += 4; reasons.append("✅ Histogram growing")
        return SetupResult("MACD Cross", "BUY", score, reasons, intraday=True)
    if _g(ind,"macd_cross_bear"):
        score = 50.0
        reasons = ["✅ MACD histogram negative cross"]
        if _g(ind,"ema_bear"): score += 7; reasons.append("✅ EMA bear")
        if _g(ind,"adx",0)>18: score += 5; reasons.append(f"✅ ADX {_g(ind,'adx',0):.0f}")
        if _g(ind,"st_bear"): score += 5; reasons.append("✅ SuperTrend bear")
        if _g(ind,"macd_hist_growing"): score += 4; reasons.append("✅ Histogram growing")
        return SetupResult("MACD Cross", "SELL", score, reasons, intraday=True)
    return None


def s_cci_extreme(ind: dict, sym: str) -> Optional[SetupResult]:
    """CCI extreme reversion."""
    if _g(ind,"cci_bull") or (_g(ind,"cci_extreme_os") and _g(ind,"stoch_cross_bull")):
        score = 52.0
        reasons = ["✅ CCI crossed from extreme oversold (<-100)"]
        if _g(ind,"rsi_os"): score += 6; reasons.append("✅ RSI confirms")
        if _g(ind,"bb_pct_b_os"): score += 5; reasons.append("✅ BB oversold")
        return SetupResult("CCI Extreme", "BUY", score, reasons, scalp=True)
    if _g(ind,"cci_bear") or (_g(ind,"cci_extreme_ob") and _g(ind,"stoch_cross_bear")):
        score = 52.0
        reasons = ["✅ CCI crossed from extreme overbought (>100)"]
        if _g(ind,"rsi_ob"): score += 6; reasons.append("✅ RSI confirms")
        if _g(ind,"bb_pct_b_ob"): score += 5; reasons.append("✅ BB overbought")
        return SetupResult("CCI Extreme", "SELL", score, reasons, scalp=True)
    return None


def s_williams_r(ind: dict, sym: str) -> Optional[SetupResult]:
    """Williams %R extreme reversion."""
    if _g(ind,"wr_cross_bull"):
        score = 50.0
        reasons = ["✅ Williams %R cross UP from oversold"]
        if _g(ind,"stoch_cross_bull"): score += 6; reasons.append("✅ StochRSI confirms")
        if _g(ind,"rsi_os"): score += 5; reasons.append("✅ RSI confirms")
        return SetupResult("Williams R", "BUY", score, reasons, scalp=True)
    if _g(ind,"wr_cross_bear"):
        score = 50.0
        reasons = ["✅ Williams %R cross DOWN from overbought"]
        if _g(ind,"stoch_cross_bear"): score += 6; reasons.append("✅ StochRSI confirms")
        if _g(ind,"rsi_ob"): score += 5; reasons.append("✅ RSI confirms")
        return SetupResult("Williams R", "SELL", score, reasons, scalp=True)
    return None


def s_pivot_bounce(ind: dict, sym: str) -> Optional[SetupResult]:
    """Pivot S1/R1/S2/R2 bounce."""
    if _g(ind,"at_s1") or _g(ind,"at_s2"):
        level = "S1" if _g(ind,"at_s1") else "S2"
        score = 54.0
        reasons = [f"✅ Price at Pivot {level} — support zone"]
        if _g(ind,"stoch_cross_bull"): score += 7; reasons.append("✅ StochRSI trigger")
        if _g(ind,"rsi_os"): score += 5; reasons.append("✅ RSI oversold")
        if _g(ind,"fvg_bull"): score += 5; reasons.append("✅ FVG zone")
        return SetupResult("Pivot Bounce", "BUY", score, reasons, intraday=True)
    if _g(ind,"at_r1") or _g(ind,"at_r2"):
        level = "R1" if _g(ind,"at_r1") else "R2"
        score = 54.0
        reasons = [f"✅ Price at Pivot {level} — resistance zone"]
        if _g(ind,"stoch_cross_bear"): score += 7; reasons.append("✅ StochRSI trigger")
        if _g(ind,"rsi_ob"): score += 5; reasons.append("✅ RSI overbought")
        if _g(ind,"fvg_bear"): score += 5; reasons.append("✅ FVG zone")
        return SetupResult("Pivot Bounce", "SELL", score, reasons, intraday=True)
    return None


def s_donchian_breakout(ind: dict, sym: str) -> Optional[SetupResult]:
    """Donchian Channel breakout — momentum continuation."""
    if _g(ind,"don_break_bull"):
        score = 52.0
        reasons = ["✅ Donchian Channel bullish breakout"]
        if _g(ind,"macd_bull"): score += 7; reasons.append("✅ MACD momentum")
        if _g(ind,"adx",0)>18: score += 5; reasons.append("✅ ADX trend")
        if _g(ind,"bos_bull"): score += 6; reasons.append("✅ BOS confirms")
        if _g(ind,"volume_high"): score += 4; reasons.append("✅ Volume high")
        return SetupResult("Donchian Breakout", "BUY", score, reasons, intraday=True)
    if _g(ind,"don_break_bear"):
        score = 52.0
        reasons = ["✅ Donchian Channel bearish breakout"]
        if _g(ind,"macd_bear"): score += 7; reasons.append("✅ MACD momentum")
        if _g(ind,"adx",0)>18: score += 5; reasons.append("✅ ADX trend")
        if _g(ind,"bos_bear"): score += 6; reasons.append("✅ BOS confirms")
        if _g(ind,"volume_high"): score += 4; reasons.append("✅ Volume high")
        return SetupResult("Donchian Breakout", "SELL", score, reasons, intraday=True)
    return None


def s_psar_flip(ind: dict, sym: str) -> Optional[SetupResult]:
    """PSAR direction flip."""
    if _g(ind,"psar_flip_bull"):
        score = 50.0
        reasons = ["✅ PSAR flipped bullish"]
        if _g(ind,"ema_bull"): score += 7; reasons.append("✅ EMA aligned")
        if _g(ind,"macd_bull"): score += 5; reasons.append("✅ MACD confirms")
        return SetupResult("PSAR Flip", "BUY", score, reasons, intraday=True)
    if _g(ind,"psar_flip_bear"):
        score = 50.0
        reasons = ["✅ PSAR flipped bearish"]
        if _g(ind,"ema_bear"): score += 7; reasons.append("✅ EMA aligned")
        if _g(ind,"macd_bear"): score += 5; reasons.append("✅ MACD confirms")
        return SetupResult("PSAR Flip", "SELL", score, reasons, intraday=True)
    return None


def s_heikin_ashi(ind: dict, sym: str) -> Optional[SetupResult]:
    """Heikin Ashi candle flip."""
    if _g(ind,"ha_flip_bull"):
        score = 50.0
        reasons = ["✅ Heikin Ashi flipped bullish"]
        if _g(ind,"ema_bull"): score += 7; reasons.append("✅ EMA aligned")
        if _g(ind,"macd_bull"): score += 5; reasons.append("✅ MACD")
        if _g(ind,"st_bull"): score += 4; reasons.append("✅ SuperTrend bull")
        return SetupResult("HA Flip", "BUY", score, reasons, scalp=True)
    if _g(ind,"ha_flip_bear"):
        score = 50.0
        reasons = ["✅ Heikin Ashi flipped bearish"]
        if _g(ind,"ema_bear"): score += 7; reasons.append("✅ EMA aligned")
        if _g(ind,"macd_bear"): score += 5; reasons.append("✅ MACD")
        if _g(ind,"st_bear"): score += 4; reasons.append("✅ SuperTrend bear")
        return SetupResult("HA Flip", "SELL", score, reasons, scalp=True)
    return None


def s_session_kill_zone(ind: dict, sym: str) -> Optional[SetupResult]:
    """Session kill zone momentum entry — best confluence in kill zones."""
    if not _kill_zone():
        return None
    session = _session()
    bull = _g(ind,"ema_bull") and _g(ind,"macd_bull") and not _g(ind,"rsi_ob")
    bear = _g(ind,"ema_bear") and _g(ind,"macd_bear") and not _g(ind,"rsi_os")
    if bull:
        score = 56.0
        reasons = [f"✅ Kill zone entry: {session.replace('_',' ').title()}"]
        if _g(ind,"ichi_full_bull"): score += 8; reasons.append("✅ Ichimoku full bull")
        if _g(ind,"fvg_bull"): score += 6; reasons.append("✅ FVG zone")
        if _g(ind,"at_bull_ob"): score += 5; reasons.append("✅ OB support")
        return SetupResult("Kill Zone Momentum", "BUY", score, reasons, scalp=True)
    if bear:
        score = 56.0
        reasons = [f"✅ Kill zone entry: {session.replace('_',' ').title()}"]
        if _g(ind,"ichi_full_bear"): score += 8; reasons.append("✅ Ichimoku full bear")
        if _g(ind,"fvg_bear"): score += 6; reasons.append("✅ FVG zone")
        if _g(ind,"at_bear_ob"): score += 5; reasons.append("✅ OB resistance")
        return SetupResult("Kill Zone Momentum", "SELL", score, reasons, scalp=True)
    return None


def s_momentum_rsi_cross(ind: dict, sym: str) -> Optional[SetupResult]:
    """RSI cross 50 with momentum confirmation — simple but effective."""
    if _g(ind,"rsi_cross_50_bull") and _g(ind,"momentum_bull"):
        score = 50.0
        reasons = ["✅ RSI crossed above 50 — momentum shift"]
        if _g(ind,"ema_bull"): score += 6; reasons.append("✅ EMA bull")
        if _g(ind,"macd_bull"): score += 5; reasons.append("✅ MACD bull")
        return SetupResult("RSI-Momentum", "BUY", score, reasons, scalp=True)
    if _g(ind,"rsi_cross_50_bear") and _g(ind,"momentum_bear"):
        score = 50.0
        reasons = ["✅ RSI crossed below 50 — momentum shift"]
        if _g(ind,"ema_bear"): score += 6; reasons.append("✅ EMA bear")
        if _g(ind,"macd_bear"): score += 5; reasons.append("✅ MACD bear")
        return SetupResult("RSI-Momentum", "SELL", score, reasons, scalp=True)
    return None


def s_fib_confluence(ind: dict, sym: str) -> Optional[SetupResult]:
    """Fibonacci level bounce with indicator confirmation."""
    if _g(ind,"at_fib_618") or _g(ind,"at_fib_382"):
        fib = "0.618" if _g(ind,"at_fib_618") else "0.382"
        # Determine direction from trend context
        if _g(ind,"ema_bull") or _g(ind,"ema_stack_bull"):
            score = 56.0
            reasons = [f"✅ Fibonacci {fib} level — key retracement zone (BUY)"]
            if _g(ind,"stoch_cross_bull"): score += 8; reasons.append("✅ StochRSI trigger")
            if _g(ind,"rsi_os") or _g(ind,"rsi_bull"): score += 6; reasons.append("✅ RSI zone")
            if _g(ind,"at_bull_ob"): score += 6; reasons.append("✅ OB confluence")
            return SetupResult("Fib Confluence", "BUY", score, reasons, intraday=True)
        if _g(ind,"ema_bear") or _g(ind,"ema_stack_bear"):
            score = 56.0
            reasons = [f"✅ Fibonacci {fib} level — key retracement zone (SELL)"]
            if _g(ind,"stoch_cross_bear"): score += 8; reasons.append("✅ StochRSI trigger")
            if _g(ind,"rsi_ob") or _g(ind,"rsi_bear"): score += 6; reasons.append("✅ RSI zone")
            if _g(ind,"at_bear_ob"): score += 6; reasons.append("✅ OB confluence")
            return SetupResult("Fib Confluence", "SELL", score, reasons, intraday=True)
    return None


# ═══════════════════════════════════════════════════════════════
#  STRATEGY ROUTING MAP
# ═══════════════════════════════════════════════════════════════

STRATEGY_MAP: Dict[str, list] = {
    "MEAN_REVERSION": [
        s_zscore_reversion,     # Primary: Z-Score
        s_bb_mean_reversion,    # BB %B reversion
        s_lrc_reversion,        # LRC channel
        s_wvf_reversal,         # Fear spike
        s_rsi_divergence,       # Divergence
        s_stoch_reversal,       # StochRSI OS/OB
        s_cci_extreme,          # CCI extreme
        s_williams_r,           # Williams %R
        s_ob_retest,            # SMC OB
        s_fvg_fill,             # FVG
        s_pivot_bounce,         # Pivots
        s_heikin_ashi,          # HA flip
        s_psar_flip,            # PSAR
        s_fib_confluence,       # Fibonacci
        s_momentum_rsi_cross,   # RSI x50
    ],
    "ADAPTIVE": [
        s_adx_regime_reversion, # ADX ranging → BB
        s_adx_trend_follow,     # ADX trending → EMA
        s_rsi_divergence,
        s_ob_retest,
        s_fvg_fill,
        s_bos_breakout,
        s_supertrend_flip,
        s_stoch_reversal,
        s_macd_cross,
        s_donchian_breakout,
        s_judas_swing,          # Session trap
        s_bsl_ssl_sweep,        # Liquidity sweep
        s_cci_extreme,
        s_session_kill_zone,
        s_williams_r,
        s_fib_confluence,
    ],
    "TREND_MOMENTUM": [
        s_momentum_sniper,      # BB squeeze breakout
        s_ema_trend,            # Multi-EMA + Hurst
        s_bos_breakout,         # Structure break
        s_judas_swing,          # Session trap
        s_ict_silver_bullet,    # ICT time window
        s_bsl_ssl_sweep,        # Liquidity sweep
        s_supertrend_flip,
        s_macd_cross,
        s_rsi_divergence,
        s_ob_retest,
        s_fvg_fill,
        s_donchian_breakout,
        s_psar_flip,
        s_heikin_ashi,
        s_session_kill_zone,
        s_fib_confluence,
        s_momentum_rsi_cross,
    ],
}


def run_strategies(ind: dict, sym: str) -> List[SetupResult]:
    """
    Run all strategies for given symbol.
    Returns all valid SetupResult objects (score > 0).
    """
    from config import INSTRUMENTS
    mode   = INSTRUMENTS.get(sym, {}).get("strategy_mode", "ADAPTIVE")
    fns    = STRATEGY_MAP.get(mode, STRATEGY_MAP["ADAPTIVE"])
    results = []
    for fn in fns:
        try:
            res = fn(ind, sym)
            if res is not None and res.score > 0:
                results.append(res)
        except Exception as e:
            log.debug(f"[{sym}] Strategy {fn.__name__} error: {e}")
    return results
