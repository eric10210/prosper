"""
config.py — PROSPER v2 Master Configuration
Per-volatility calibration, account tiers, smart thresholds.
All secrets loaded from .env — never hardcode in production.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ─── IDENTITY ─────────────────────────────────────────────────────────────────
BOT_NAME    = "PROSPER"
BOT_VERSION = "2.0.0"

# ─── DERIV WebSocket ──────────────────────────────────────────────────────────
DERIV_APP_ID  = os.getenv("DERIV_APP_ID",  "128713")
DERIV_API_KEY = os.getenv("DERIV_API_KEY", "nZGnlY35GGdxCNN")
DERIV_WS_URL  = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}"

# ─── METAAPI ──────────────────────────────────────────────────────────────────
METAAPI_TOKEN      = os.getenv("METAAPI_TOKEN", "")      # Full JWT from .env
METAAPI_ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID", "c7703209-79ca-4c55-96a0-da3e60e0bf04")
MT5_LOGIN          = os.getenv("MT5_LOGIN",    "40963774")
MT5_PASSWORD       = os.getenv("MT5_PASSWORD", "Otierics12@")
MT5_SERVER         = os.getenv("MT5_SERVER",   "Deriv-Demo")

# ─── TELEGRAM — TWO COMPLETELY SEPARATE BOTS ─────────────────────────────────
# PROSPER's own bot — dedicated for trading alerts ONLY
# KiloClav uses a DIFFERENT token — never use KiloClav's token here
PROSPER_BOT_TOKEN  = os.getenv("PROSPER_BOT_TOKEN",
                                "8537766216:AAEV05Ednwz6s9MFh0ulL-A2ZK8RYmEgqIA")
PROSPER_CHAT_ID    = os.getenv("PROSPER_CHAT_ID", "8746686966")

# KiloClav's token is intentionally NOT stored here to prevent collision.
# KiloClav manages its own Telegram application separately.
# PROSPER sends messages via its own bot token to PROSPER_CHAT_ID.

# ─── INSTRUMENTS — Volatility 10/25/50/75 ONLY ───────────────────────────────
INSTRUMENTS = {
    "R_10": {
        "mt5":         "Volatility 10 Index",
        "label":       "Vol 10",
        "decimals":    3,
        "vol_pct":     10,
        "pip_size":    0.001,
        "min_lot":     0.01,
        "max_lot":     2.0,
        "strategy_mode": "MEAN_REVERSION",
        "min_score":   48,      # Lower for Vol 10 — very predictable
        # ATR-based SL/TP multipliers (per-instrument calibration)
        "atr_mult": {
            "LOW":    {"sl":0.7,  "tp1":0.7,  "tp2":1.5, "tp3":2.5},
            "MEDIUM": {"sl":0.9,  "tp1":0.9,  "tp2":1.8, "tp3":3.0},
            "HIGH":   {"sl":1.2,  "tp1":1.0,  "tp2":2.0, "tp3":3.2},
        },
        # BE and trail (fraction of ATR)
        "be_trigger_atr":   0.3,    # Move SL to BE after +0.3 ATR profit
        "trail_atr":        0.5,    # Trail at 0.5 ATR after TP1
        "tp1_close_pct":    0.50,   # Close 50% at TP1 (mean reversion — take early)
        "tp2_close_pct":    0.30,
        "tp3_close_pct":    0.20,
        "spread_pips":      1.0,
        "slippage_pips":    0.3,
    },
    "R_25": {
        "mt5":         "Volatility 25 Index",
        "label":       "Vol 25",
        "decimals":    3,
        "vol_pct":     25,
        "pip_size":    0.001,
        "min_lot":     0.01,
        "max_lot":     3.0,
        "strategy_mode": "MEAN_REVERSION",
        "min_score":   50,
        "atr_mult": {
            "LOW":    {"sl":0.8,  "tp1":0.8,  "tp2":1.7, "tp3":2.8},
            "MEDIUM": {"sl":1.0,  "tp1":1.0,  "tp2":2.0, "tp3":3.3},
            "HIGH":   {"sl":1.3,  "tp1":1.1,  "tp2":2.1, "tp3":3.5},
        },
        "be_trigger_atr":   0.4,
        "trail_atr":        0.7,
        "tp1_close_pct":    0.40,
        "tp2_close_pct":    0.35,
        "tp3_close_pct":    0.25,
        "spread_pips":      0.8,
        "slippage_pips":    0.3,
    },
    "R_50": {
        "mt5":         "Volatility 50 Index",
        "label":       "Vol 50",
        "decimals":    4,
        "vol_pct":     50,
        "pip_size":    0.0001,
        "min_lot":     0.01,
        "max_lot":     5.0,
        "strategy_mode": "ADAPTIVE",
        "min_score":   52,
        "atr_mult": {
            "LOW":    {"sl":0.8,  "tp1":0.8,  "tp2":1.8, "tp3":3.0},
            "MEDIUM": {"sl":1.0,  "tp1":1.0,  "tp2":2.0, "tp3":3.5},
            "HIGH":   {"sl":1.4,  "tp1":1.2,  "tp2":2.2, "tp3":3.8},
        },
        "be_trigger_atr":   0.5,
        "trail_atr":        0.9,
        "tp1_close_pct":    0.35,
        "tp2_close_pct":    0.35,
        "tp3_close_pct":    0.30,
        "spread_pips":      0.5,
        "slippage_pips":    0.2,
    },
    "R_75": {
        "mt5":         "Volatility 75 Index",
        "label":       "Vol 75",
        "decimals":    4,
        "vol_pct":     75,
        "pip_size":    0.0001,
        "min_lot":     0.01,
        "max_lot":     5.0,
        "strategy_mode": "TREND_MOMENTUM",
        "min_score":   52,
        "atr_mult": {
            "LOW":    {"sl":1.0,  "tp1":0.9,  "tp2":2.0, "tp3":3.5},
            "MEDIUM": {"sl":1.2,  "tp1":1.0,  "tp2":2.2, "tp3":4.0},
            "HIGH":   {"sl":1.6,  "tp1":1.3,  "tp2":2.5, "tp3":4.5},
        },
        "be_trigger_atr":   0.7,    # V75 needs room — wider BE trigger
        "trail_atr":        1.1,    # Wider trail for trending index
        "tp1_close_pct":    0.30,   # Let V75 trends run — smaller early close
        "tp2_close_pct":    0.35,
        "tp3_close_pct":    0.35,
        "spread_pips":      0.5,
        "slippage_pips":    0.3,
    },
}
ALL_SYMBOLS = list(INSTRUMENTS.keys())

# ─── GLOBAL SIGNAL THRESHOLD ─────────────────────────────────────────────────
# Reduced to 50% to fire more trades and test the system
MIN_CONFLUENCE = 50       # Was 70 — now 50 for active trading

# Grade thresholds  
GRADE_THRESHOLDS = [
    ("APEX PRIME", 88),
    ("S",  78),
    ("A",  68),
    ("B",  60),
    ("C",  54),
    ("D",  50),
    ("F",   0),
]

# ─── ACCOUNT TIERS — Smart balance-aware trading ─────────────────────────────
# Bot adjusts risk, threshold, and behavior based on balance
ACCOUNT_TIERS = {
    "NANO":   {"min": 0,    "max": 20,   "risk_pct": 0.03, "score_bonus": -5, "label": "Nano"},
    "MICRO":  {"min": 20,   "max": 100,  "risk_pct": 0.025,"score_bonus": -3, "label": "Micro"},
    "SMALL":  {"min": 100,  "max": 500,  "risk_pct": 0.02, "score_bonus":  0, "label": "Small"},
    "MEDIUM": {"min": 500,  "max": 5000, "risk_pct": 0.02, "score_bonus":  2, "label": "Medium"},
    "LARGE":  {"min": 5000, "max": 1e9,  "risk_pct": 0.015,"score_bonus":  5, "label": "Large"},
}

def get_account_tier(balance: float) -> dict:
    for tier in ACCOUNT_TIERS.values():
        if tier["min"] <= balance < tier["max"]:
            return tier
    return ACCOUNT_TIERS["SMALL"]

# ─── DD MANAGEMENT MODES ─────────────────────────────────────────────────────
DD_MODES = {
    "NORMAL":       {"max_dd": 0.05,  "risk_mult": 1.0,   "score_add": 0,   "label": "Normal"},
    "CAUTION":      {"max_dd": 0.10,  "risk_mult": 0.75,  "score_add": 5,   "label": "⚠️ Caution"},
    "RECOVERY":     {"max_dd": 0.15,  "risk_mult": 0.50,  "score_add": 8,   "label": "🔴 Recovery"},
    "PRESERVATION": {"max_dd": 0.20,  "risk_mult": 0.25,  "score_add": 15,  "label": "🚨 Preservation"},
    "KILL":         {"max_dd": 1.00,  "risk_mult": 0.0,   "score_add": 999, "label": "☠️ Kill Switch"},
}

def get_dd_mode(current_dd_pct: float) -> dict:
    if current_dd_pct >= 0.20: return DD_MODES["KILL"]
    if current_dd_pct >= 0.15: return DD_MODES["PRESERVATION"]
    if current_dd_pct >= 0.10: return DD_MODES["RECOVERY"]
    if current_dd_pct >= 0.05: return DD_MODES["CAUTION"]
    return DD_MODES["NORMAL"]

# ─── RISK CONSTANTS ───────────────────────────────────────────────────────────
ACCOUNT_BALANCE         = float(os.getenv("ACCOUNT_BALANCE", "100"))
MIN_LOT                 = 0.01
MAX_LOT                 = 5.0
POINT_VALUE             = 1.0
MAX_CONCURRENT_TRADES   = 1           # Monogamy protocol
MAX_DAILY_LOSS_PCT      = 0.05
MAX_WEEKLY_LOSS_PCT     = 0.10
MAX_DRAWDOWN_PCT        = 0.20
GLOBAL_COOLDOWN_SECS    = 180         # 3 min cooldown (was 5)
MIN_RR                  = 1.3         # Minimum R:R (was 1.5, relaxed)

# ─── ATR REGIME ───────────────────────────────────────────────────────────────
ATR_LOW_THRESHOLD  = 100   # Below = LOW regime
ATR_HIGH_THRESHOLD = 350   # Above = HIGH regime

# ─── TIMEFRAMES ───────────────────────────────────────────────────────────────
TIMEFRAMES = {"M1":60,"M5":300,"M15":900,"M30":1800,"H1":3600,"H4":14400,"D1":86400}
CANDLE_COUNT   = 300

# ─── SIGNAL EXPIRY ────────────────────────────────────────────────────────────
SIGNAL_EXPIRY_MINS     = 25
SIGNAL_EXPIRY_EXT_MINS = 10
ENTRY_BUFFER_PTS       = 8

# ─── ROUND NUMBER AVOIDANCE ───────────────────────────────────────────────────
ROUND_NUMBER_BUFFER = 20

# ─── SESSIONS (UTC) ───────────────────────────────────────────────────────────
SESSION_HOURS = {
    "asian":             (0, 7),
    "london_open":       (7, 8),
    "london":            (8, 12),
    "london_ny_overlap": (12, 15),
    "ny_open":           (13, 14),
    "ny":                (14, 20),
    "off_hours":         (20, 24),
}

# ─── LOGGING ──────────────────────────────────────────────────────────────────
LOG_DIR          = "logs"
LOG_MAX_BYTES    = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5
DB_PATH          = "prosper.db"
CSV_PATH         = "prosper_trades.csv"

# ─── WATCHDOG ─────────────────────────────────────────────────────────────────
HEARTBEAT_PORT    = 8080
WATCHDOG_TIMEOUT  = 120
