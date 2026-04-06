"""
signals.py — PROSPER v2 Signal dataclass + card builder + tracker.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List

from config import SIGNAL_EXPIRY_MINS, INSTRUMENTS, BOT_NAME, BOT_VERSION, POINT_VALUE

log = logging.getLogger(__name__)

_counter = 0

def next_signal_id(sym: str) -> str:
    global _counter
    _counter += 1
    prefix = sym.replace("R_", "V")
    return f"{prefix}_{_counter:04d}"


@dataclass
class Signal:
    signal_id:   str
    sym:         str
    direction:   str
    entry:       float
    sl:          float
    tp1:         float
    tp2:         float
    tp3:         float
    sl_pts:      float
    tp1_pts:     float
    tp2_pts:     float
    tp3_pts:     float
    lot:         float
    risk_usd:    float
    score:       float
    grade:       str
    strategy:    str
    supporting:  Optional[str]
    regime:      str
    atr14:       float
    session:     str
    reasons:     List[str]
    balance:     float
    rr_tp2:      float
    rr_tp3:      float
    trade_type:  str = "INTRADAY"   # SCALP|INTRADAY|SWING
    macro_context: str = ""

    created_at:  datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expiry_at:   Optional[datetime] = None
    extended:    bool  = False
    status:      str   = "PENDING"

    mt5_ticket:    Optional[str]   = None
    entry_actual:  Optional[float] = None
    sl_current:    Optional[float] = None

    tp1_hit: bool  = False
    tp2_hit: bool  = False
    pnl_pts: float = 0.0
    pnl_usd: float = 0.0
    r_mult:  float = 0.0

    def __post_init__(self):
        self.sl_current = self.sl
        if self.expiry_at is None:
            self.expiry_at = self.created_at + timedelta(minutes=SIGNAL_EXPIRY_MINS)

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expiry_at and self.status == "PENDING"

    @property
    def label(self) -> str:
        return INSTRUMENTS.get(self.sym, {}).get("label", self.sym)

    def close_pcts(self) -> tuple:
        inst = INSTRUMENTS.get(self.sym, {})
        return (
            inst.get("tp1_close_pct", 0.35),
            inst.get("tp2_close_pct", 0.35),
            inst.get("tp3_close_pct", 0.30),
        )


class SignalCardBuilder:

    def build(self, sig: Signal) -> str:
        d = sig.direction; grade = sig.grade
        now = datetime.now(timezone.utc)

        if grade == "APEX PRIME": hdr = "🔥 APEX PRIME 🔥"
        elif grade == "S":  hdr = f"⭐ GRADE S {'🟢' if d=='BUY' else '🔴'}"
        elif grade == "A":  hdr = f"🟢 GRADE A" if d == "BUY" else "🔴 GRADE A"
        elif grade == "B":  hdr = f"{'🟢' if d=='BUY' else '🔴'} GRADE B"
        elif grade == "C":  hdr = f"👁️ GRADE C"
        else:               hdr = f"📋 GRADE {grade}"

        dir_e = "🟢 BUY ↑" if d == "BUY" else "🔴 SELL ↓"
        regime_e = {"LOW":"🔵 Low Vol","MEDIUM":"🟡 Normal","HIGH":"🔴 High Vol ⚠️"}.get(sig.regime,"🟡")
        tp1_pct, tp2_pct, tp3_pct = sig.close_pcts()
        strat_line = sig.strategy + (f" + {sig.supporting}" if sig.supporting else "")
        reasons_str = "\n".join(f"  {r}" for r in sig.reasons[:16])

        return (
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🤖 PROSPER AUTO-TRADE | {sig.label}\n"
            f"{hdr}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 {now.strftime('%d/%m/%Y %H:%M')} UTC | #{sig.signal_id}\n"
            f"Type:  {sig.trade_type} | Grade: {grade}\n\n"
            f"─── 📊 MARKET ───\n"
            f"Direction: {dir_e}\n"
            f"Session:   {sig.session.replace('_',' ').title()}\n"
            f"Regime:    {regime_e} | ATR: {sig.atr14:.4f}\n\n"
            f"─── 🎯 LEVELS ───\n"
            f"Entry:     {sig.entry:.5f}\n"
            f"SL:        {sig.sl:.5f}  ({sig.sl_pts:.4f} pts)\n"
            f"TP1({tp1_pct:.0%}): {sig.tp1:.5f} (+{sig.tp1_pts:.4f} | 1:{sig.rr_tp2:.1f}≈)\n"
            f"TP2({tp2_pct:.0%}): {sig.tp2:.5f} (+{sig.tp2_pts:.4f} | 1:{sig.rr_tp2:.1f})\n"
            f"TP3({tp3_pct:.0%}): {sig.tp3:.5f} (+{sig.tp3_pts:.4f} | 1:{sig.rr_tp3:.1f})\n"
            f"Expiry:    {sig.expiry_at.strftime('%H:%M')} UTC\n\n"
            f"─── 💰 RISK ───\n"
            f"Risk:      -${sig.risk_usd:.2f} max | Lot: {sig.lot:.3f}\n"
            f"R:R TP2:   1:{sig.rr_tp2:.1f} | TP3: 1:{sig.rr_tp3:.1f}\n"
            f"Balance:   ${sig.balance:.2f}\n\n"
            f"─── 📈 CONFLUENCE {sig.score:.1f}% ({grade}) ───\n"
            f"{reasons_str}\n\n"
            f"─── ⚙️ MANAGEMENT ───\n"
            f"TP1 → Close {tp1_pct:.0%} | SL→BE\n"
            f"TP2 → Close {tp2_pct:.0%} | Trail SL\n"
            f"TP3 → Trail {tp3_pct:.0%} (parabolic)\n\n"
            f"─── 🚀 STRATEGY ───\n"
            f"  {strat_line}\n\n"
            + (f"─── 🌐 MACRO ───\n{sig.macro_context}\n\n" if sig.macro_context else "")
            + f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ {BOT_NAME} v{BOT_VERSION} | MT5 AUTO-EXEC\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    def entry_alert(self, sig: Signal, price: float) -> str:
        return (
            f"✅ TRADE OPEN — #{sig.signal_id} {sig.label}\n"
            f"{'BUY 🟢' if sig.direction=='BUY' else 'SELL 🔴'} @ {price:.5f}\n"
            f"Lot: {sig.lot:.3f} | Risk: -${sig.risk_usd:.2f}\n"
            f"SL: {sig.sl:.5f} | TP1: {sig.tp1:.5f}\n"
            f"Ticket: {sig.mt5_ticket or 'pending'}"
        )

    def tp1_alert(self, sig: Signal, price: float, pnl: float) -> str:
        tp1_pct, _, _ = sig.close_pcts()
        return (
            f"🎯 TP1 HIT — #{sig.signal_id} {sig.label}\n"
            f"@ {price:.5f}\n"
            f"✅ Closed {tp1_pct:.0%} → +${pnl:.2f}\n"
            f"⚡ SL moved to BREAKEVEN\n"
            f"Holding {1-tp1_pct:.0%} for TP2/TP3"
        )

    def tp2_alert(self, sig: Signal, price: float, pnl: float) -> str:
        _, tp2_pct, _ = sig.close_pcts()
        return (
            f"🎯🎯 TP2 HIT — #{sig.signal_id} {sig.label}\n"
            f"@ {price:.5f}\n"
            f"✅ Closed {tp2_pct:.0%} → +${pnl:.2f} | +{sig.rr_tp2:.1f}R\n"
            f"🔒 Parabolic trail active\n"
            f"Running last 30% to TP3"
        )

    def win_alert(self, sig: Signal, price: float, pts: float, usd: float, dur: float) -> str:
        return (
            f"🏆 FULL WIN — #{sig.signal_id} {sig.label}\n"
            f"@ {price:.5f} | Entry {sig.entry:.5f}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"P&L: +{pts:.4f}pts | +${usd:.2f} | +{sig.rr_tp3:.1f}R\n"
            f"Duration: {dur:.0f}min | {sig.strategy}\n"
            f"✅TP1 ✅TP2 ✅TP3 🏅"
        )

    def trail_alert(self, sig: Signal, price: float, usd: float, r: float) -> str:
        return (
            f"💰 TRAIL STOP — #{sig.signal_id} {sig.label}\n"
            f"@ {price:.5f}\n"
            f"+${usd:.2f} | +{r:.1f}R locked\n"
            f"✅TP1 ✅TP2 → Trail took profit 💪"
        )

    def sl_alert(self, sig: Signal, price: float) -> str:
        return (
            f"❌ SL HIT — #{sig.signal_id} {sig.label}\n"
            f"@ {price:.5f}\n"
            f"Loss: -${sig.risk_usd:.2f} (-{sig.sl_pts:.4f}pts)\n"
            f"Strategy: {sig.strategy} | Score: {sig.score:.0f}%\n"
            f"5-min cooldown active. Next scan ready. 📈"
        )

    def expired_alert(self, sig: Signal, price: float) -> str:
        return (
            f"⏰ EXPIRED — #{sig.signal_id} {sig.label}\n"
            f"Entry {sig.entry:.5f} not triggered.\n"
            f"Price: {price:.5f} | Scanning next."
        )

    def be_alert(self, sig: Signal) -> str:
        return (
            f"🔒 SL→BE — #{sig.signal_id} {sig.label}\n"
            f"SL moved to {sig.entry_actual or sig.entry:.5f}\n"
            f"Trade is RISK-FREE ✅"
        )

    def live_update(self, sig: Signal, price: float) -> str:
        entry    = sig.entry_actual or sig.entry
        float_pts= (price-entry) if sig.direction=="BUY" else (entry-price)
        float_usd= round(sig.lot * float_pts * POINT_VALUE, 2)
        r_now    = float_pts/sig.sl_pts if sig.sl_pts>0 else 0
        next_tp  = sig.tp1 if not sig.tp1_hit else (sig.tp2 if not sig.tp2_hit else sig.tp3)
        open_m   = (datetime.now(timezone.utc)-sig.created_at).total_seconds()/60
        return (
            f"📡 LIVE #{sig.signal_id} {sig.label}\n"
            f"Price: {price:.5f} | {float_pts:+.4f}pts\n"
            f"Float: ${float_usd:+.2f} | {r_now:+.2f}R\n"
            f"Next TP: {abs(next_tp-price):.4f} pts away\n"
            f"Open: {open_m:.0f}min"
        )


class SignalTracker:
    def __init__(self):
        self._sigs: Dict[str, Signal] = {}
        self._lock = asyncio.Lock()

    async def add(self, s: Signal):
        async with self._lock: self._sigs[s.signal_id] = s

    async def get(self, sid: str) -> Optional[Signal]:
        async with self._lock: return self._sigs.get(sid)

    async def get_active(self) -> List[Signal]:
        async with self._lock:
            return [s for s in self._sigs.values() if s.status in ("PENDING","LIVE","TP1","TP2")]

    async def get_by_sym(self, sym: str) -> Optional[Signal]:
        async with self._lock:
            for s in self._sigs.values():
                if s.sym == sym and s.status in ("PENDING","LIVE","TP1","TP2"):
                    return s
            return None

    async def update(self, sid: str, **kw):
        async with self._lock:
            s = self._sigs.get(sid)
            if s:
                for k, v in kw.items(): setattr(s, k, v)

    async def count_active(self) -> int:
        async with self._lock:
            return sum(1 for s in self._sigs.values() if s.status in ("PENDING","LIVE","TP1","TP2"))

    async def get_all(self) -> List[Signal]:
        async with self._lock: return list(self._sigs.values())
