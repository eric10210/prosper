# 🤖 PROSPER v2 — Autonomous Volatility Trading Bot

Auto-executes live MT5 trades via MetaAPI on Deriv Volatility 10/25/50/75.

---

## ⚡ KiloClav Quick Start

Paste this prompt into KiloClav, then upload all `.py` files + `.env`:

```
You are now running PROSPER v2 — an autonomous trading bot.
Load all attached Python files, install dependencies, and start the bot.

Steps:
1. pip install -r requirements.txt --break-system-packages
2. Confirm .env is present with all credentials
3. python main.py

CRITICAL — Telegram separation:
- PROSPER uses token: 8537766216:AAEV05Ednwz6s9MFh0ulL-A2ZK8RYmEgqIA
- You (KiloClav) use YOUR OWN token — NEVER use PROSPER's token
- They run as completely separate bots on separate polling loops
- PROSPER sends its own messages to chat 8746686966

Confirm startup with:
"✅ PROSPER v2 STARTED | Balance: $X | MT5: Connected | Scanning..."
```

---

## Architecture

```
Deriv WS (market data)
    ↓ M1/M5/M15/H1/H4 candles per symbol
DataStore (thread-safe OHLC)
    ↓ closed candles only (non-repainting)
Indicators (30+ indicators per symbol/TF)
    ↓ EMA, RSI, MACD, BB, ATR, Stoch, ADX, ZScore, Hurst,
      SuperTrend, Ichimoku, PSAR, CCI, Williams%R, Donchian,
      OB, BOS, FVG, Pivots, Fib, WVF, WVI, LRC, HA, Volume
Strategies (25 modules, per-instrument routing)
    ↓ Mean Reversion (V10/V25), Adaptive (V50), Trend (V75)
      + ICT (Silver Bullet, Judas Swing, BSL/SSL)
Confluence Scorer (multi-TF cross-check, SMC bonuses)
    ↓ Min score: 50% (active trading) — per-instrument calibrated
Governor Gate (soft kill, PO3 filter, macro bias)
    ↓ 3-tier HTF macro context (reference only)
Risk Manager (balance-aware, DD-mode, per-volatility sizing)
    ↓ NANO/MICRO/SMALL/MEDIUM/LARGE tiers
    ↓ DD modes: NORMAL→CAUTION→RECOVERY→PRESERVATION→KILL
Signal Card → Telegram (PROSPER's own bot)
    ↓
MetaAPI → Deriv MT5 (market order, auto-execution)
    ↓
Trade Manager (partial closes, BE, trail stop per volatility)
    ↓ V10: 0.3 ATR BE, 0.5 ATR trail
    ↓ V25: 0.4 ATR BE, 0.7 ATR trail
    ↓ V50: 0.5 ATR BE, 0.9 ATR trail
    ↓ V75: 0.7 ATR BE, 1.1 ATR trail (trending index)
Journal (SQLite + CSV + equity log every 5min)
```

---

## Instrument Strategy Routing

| Vol | Strategy Mode | Core Edge |
|-----|--------------|-----------|
| V10 | MEAN_REVERSION | Z-Score ±1.5σ (backtest 83.9% WR) |
| V25 | MEAN_REVERSION | BB %B reversion (89% mean reversion rate) |
| V50 | ADAPTIVE | ADX regime switch (ranging/trending) |
| V75 | TREND_MOMENTUM | EMA+Hurst>0.55, Momentum Sniper |

---

## Safety System

| Layer | Feature | Trigger |
|-------|---------|---------|
| Monogamy | 1 trade globally at a time | Always |
| Cooldown | 3min after any trade close | After every close |
| Soft Kill | Pause 4hrs | 3 consecutive losses |
| PO3 Filter | Block first 15min session open | London/NY open |
| Daily Limit | Stop trading | 5% daily loss |
| Weekly Limit | Pause | 10% weekly loss |
| DD Kill | Full halt | 20% total drawdown |
| Recovery | Halve position size | 3% daily loss |
| NANO tier | Highest risk% for small accounts | Balance < $20 |

---

## Telegram Commands

```
/status      Full state: prices, DD mode, session, gates
/signals     Open trades with floating P&L
/balance     Balance, equity, tier, DD mode
/pause       Pause all new signals
/resume      Resume scanning
/weekly      Full weekly report with per-symbol stats
/journal N   Last N trades
/stats D     Stats for D days
/equity      Equity curve, Sharpe, PF, streaks
/governor    Governor safety status
/kill ID     Close a specific trade
/closeall    Close all open MT5 positions
/risk PCT    Override risk % (e.g. /risk 2)
/compound    Toggle compound sizing mode
/help        All commands
```

---

## File Structure

```
prosper_v2/
├── main.py             Orchestrator — entry point
├── config.py           All settings + per-instrument calibration
├── deriv_ws.py         Deriv WebSocket (fixed: rate-limited, per-symbol)
├── data_store.py       Thread-safe OHLC candle store (non-repainting)
├── indicators.py       30+ technical indicators
├── strategies.py       25 strategy modules (ICT, SMC, Mean Rev, Trend)
├── scorer.py           Confluence scorer (50% threshold)
├── risk.py             Balance-aware risk manager (5 DD modes, 5 tiers)
├── signals.py          Signal dataclass + Telegram card builder
├── trade_manager.py    Auto-execution + per-vol BE/trail
├── metaapi_client.py   MetaAPI MT5 bridge
├── governor.py         Soft kill, PO3, macro bias, compound, equity
├── telegram_bot.py     PROSPER-only bot (no KiloClav collision)
├── journal.py          SQLite + CSV + equity log
├── watchdog.py         Flask heartbeat + scheduled tasks
├── .env                Credentials
├── requirements.txt    Dependencies
└── logs/               Rotating log files
```

---

## Telegram Collision Fix

**Problem:** KiloClav and PROSPER both trying to poll Telegram = conflict.

**Solution implemented in `telegram_bot.py`:**
1. PROSPER uses `PROSPER_BOT_TOKEN` — a completely separate bot
2. KiloClav uses its own token (never stored in PROSPER's code)
3. `drop_pending_updates=True` clears stale commands on startup
4. `allowed_updates=["message"]` — PROSPER only listens to messages
5. Chat ID guard — PROSPER ignores any chat not matching `PROSPER_CHAT_ID`
6. Each has its own `Application` instance — no shared state

---

## Recommendations & Additions Still to Add

1. **ForexFactory calendar API** — replace hardcoded news windows with live feed
2. **SMT cross-asset divergence** — check V75 vs V50 for liquidity traps
3. **D1 candle subscription** — add daily candle for proper D1 macro bias
4. **Walk-forward backtesting module** — validate strategies on historical data
5. **Web dashboard** — Flask UI showing equity curve, open trades, signal log
6. **Telegram inline buttons** — confirm/kill trade with button taps
7. **Session-specific lot scaling** — larger lots in kill zones (proven edge)
8. **Volatility-adjusted cooldown** — shorter cooldown in HIGH regime
9. **Correlation filter** — don't trade same direction on 2 instruments at once
10. **News sentiment feed** — skip V75 trades during major news surprises

---

*PROSPER v2 — Real money auto-execution. Know and accept all trading risks.*
