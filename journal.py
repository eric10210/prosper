"""
journal.py — PROSPER v2 Trade Journal.
SQLite + CSV. Full performance analytics: equity curve, Sharpe, PF, streaks.
"""
import csv
import logging
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone, date
from typing import List, Dict

from config import DB_PATH, CSV_PATH

log = logging.getLogger(__name__)


class Journal:
    def __init__(self):
        self.db   = DB_PATH
        self.csv  = CSV_PATH
        self._init_db()

    def _conn(self):
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        return c

    def _init_db(self):
        with self._conn() as c:
            c.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                signal_id      TEXT PRIMARY KEY,
                sym            TEXT,
                created_at     TEXT,
                closed_at      TEXT,
                direction      TEXT,
                strategy       TEXT,
                session        TEXT,
                regime         TEXT,
                entry          REAL,
                entry_actual   REAL,
                sl             REAL,
                tp1 REAL, tp2 REAL, tp3 REAL,
                lot            REAL,
                risk_usd       REAL,
                score          REAL,
                grade          TEXT,
                result         TEXT,
                pnl_pts        REAL,
                pnl_usd        REAL,
                r_multiple     REAL,
                duration_mins  REAL,
                mt5_ticket     TEXT,
                notes          TEXT
            )""")
            c.execute("""
            CREATE TABLE IF NOT EXISTS equity_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                logged_at TEXT,
                equity    REAL,
                balance   REAL,
                daily_pnl REAL
            )""")
            c.commit()
        log.info("Journal DB initialised.")

    # ─── WRITE ────────────────────────────────────────────────────────────────
    def log_signal(self, sig):
        with self._conn() as c:
            c.execute("""
            INSERT OR REPLACE INTO trades
            (signal_id,sym,created_at,direction,strategy,session,regime,
             entry,sl,tp1,tp2,tp3,lot,risk_usd,score,grade,
             result,pnl_pts,pnl_usd,r_multiple,duration_mins,notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                sig.signal_id, sig.sym,
                sig.created_at.isoformat(),
                sig.direction, sig.strategy,
                sig.session, sig.regime,
                sig.entry, sig.sl, sig.tp1, sig.tp2, sig.tp3,
                sig.lot, sig.risk_usd, sig.score, sig.grade,
                sig.status, 0.0, 0.0, 0.0, 0.0, "",
            ))
            c.commit()
        self._csv_row(sig, sig.status, 0, 0, 0, 0)

    def update_result(self, signal_id: str, result: str,
                      pnl_pts: float, pnl_usd: float,
                      r_mult: float, dur: float,
                      notes: str = "", ticket: str = "", entry_actual: float = 0.0):
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as c:
            c.execute("""
            UPDATE trades
            SET result=?,pnl_pts=?,pnl_usd=?,r_multiple=?,duration_mins=?,
                notes=?,mt5_ticket=?,entry_actual=?,closed_at=?
            WHERE signal_id=?
            """, (result, pnl_pts, pnl_usd, r_mult, dur,
                  notes, ticket, entry_actual, now, signal_id))
            c.commit()

    def log_equity(self, equity: float, balance: float, daily_pnl: float):
        with self._conn() as c:
            c.execute(
                "INSERT INTO equity_log(logged_at,equity,balance,daily_pnl) VALUES(?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), equity, balance, daily_pnl)
            )
            c.commit()

    # ─── READ / STATS ─────────────────────────────────────────────────────────
    def get_stats(self, days: int = 7) -> dict:
        with self._conn() as c:
            rows = c.execute("""
            SELECT result,pnl_usd,r_multiple,strategy,sym
            FROM trades
            WHERE created_at >= datetime('now',?)
              AND result NOT IN ('PENDING','LIVE','TP1','TP2','EXPIRED')
            """, (f"-{days} days",)).fetchall()

        if not rows:
            return {
                "total":0,"wins":0,"losses":0,"breakeven":0,
                "win_rate":0,"net_pnl":0,"avg_r":0,
                "best_strategy":"N/A","profit_factor":0,
                "best_pnl":0,"worst_pnl":0,
            }

        total = len(rows)
        wins  = sum(1 for r in rows if r["result"] in ("win","partial"))
        losses= sum(1 for r in rows if r["result"] == "loss")
        be    = sum(1 for r in rows if r["result"] == "breakeven")
        net   = sum(r["pnl_usd"] for r in rows)
        avg_r = sum(r["r_multiple"] for r in rows) / total
        gw    = sum(r["pnl_usd"] for r in rows if r["pnl_usd"] > 0)
        gl    = abs(sum(r["pnl_usd"] for r in rows if r["pnl_usd"] < 0))
        pf    = round(gw / gl if gl > 0 else gw, 2)
        pnls  = [r["pnl_usd"] for r in rows]

        strat_m = defaultdict(lambda: [0, 0])
        for r in rows:
            strat_m[r["strategy"]][1] += 1
            if r["result"] in ("win","partial"):
                strat_m[r["strategy"]][0] += 1
        best_s = max(strat_m.items(),
                     key=lambda x: x[1][0] / max(x[1][1], 1),
                     default=("N/A", [0,0]))

        return {
            "total": total, "wins": wins, "losses": losses, "breakeven": be,
            "win_rate": round(wins / total * 100, 1),
            "net_pnl": round(net, 2),
            "avg_r": round(avg_r, 2),
            "best_strategy": best_s[0],
            "profit_factor": pf,
            "best_pnl":  round(max(pnls), 2),
            "worst_pnl": round(min(pnls), 2),
        }

    def get_last_trades(self, n: int = 10) -> List[dict]:
        with self._conn() as c:
            rows = c.execute("""
            SELECT signal_id,sym,direction,strategy,entry,entry_actual,
                   result,pnl_usd,r_multiple,duration_mins,created_at,grade,score
            FROM trades ORDER BY created_at DESC LIMIT ?
            """, (n,)).fetchall()
        return [dict(r) for r in rows]

    def sym_stats(self) -> Dict[str, dict]:
        with self._conn() as c:
            rows = c.execute("""
            SELECT sym,result,pnl_usd,r_multiple FROM trades
            WHERE result NOT IN ('PENDING','LIVE','TP1','TP2','EXPIRED')
            """).fetchall()
        out = defaultdict(lambda: {"wins":0,"losses":0,"pnl":0.0,"trades":0})
        for r in rows:
            s = r["sym"]
            out[s]["trades"] += 1
            out[s]["pnl"]    += r["pnl_usd"]
            if r["result"] in ("win","partial"): out[s]["wins"]   += 1
            elif r["result"] == "loss":          out[s]["losses"] += 1
        return {k: dict(v) for k, v in out.items()}

    # ─── FORMATTED REPORTS ────────────────────────────────────────────────────
    def journal_text(self, n: int = 10) -> str:
        trades = self.get_last_trades(n)
        if not trades:
            return "📋 No completed trades yet."
        em = {"win":"✅","partial":"💰","loss":"❌","expired":"⏰","breakeven":"➖"}
        lines = [f"📋 PROSPER — Last {min(n, len(trades))} Trades\n{'─'*30}"]
        for t in trades:
            e = em.get(t.get("result",""), "⬜")
            r = f"{t['r_multiple']:+.1f}R" if t.get("r_multiple") else ""
            lines.append(
                f"{e} #{t['signal_id']} {t.get('sym','')} {t.get('direction','')}\n"
                f"   {t.get('strategy','?')} | {t.get('grade','?')} {t.get('score',0):.0f}%\n"
                f"   P&L: ${t.get('pnl_usd',0):+.2f} {r} | "
                f"{t.get('duration_mins',0):.0f}min"
            )
        return "\n".join(lines)

    def weekly_report(self) -> str:
        s7  = self.get_stats(7)
        s30 = self.get_stats(30)
        sym = self.sym_stats()
        today = date.today().strftime("%d/%m/%Y")
        sym_lines = ""
        for k, v in sym.items():
            tot = v["wins"] + v["losses"]
            wr  = round(v["wins"] / tot * 100) if tot > 0 else 0
            sym_lines += f"  {k}: {v['wins']}W/{v['losses']}L ({wr}%) ${v['pnl']:+.2f}\n"
        return (
            f"📊 PROSPER WEEKLY REPORT\n"
            f"Week ending: {today}\n"
            f"{'─'*32}\n"
            f"7-DAY\n"
            f"Signals:     {s7['total']}\n"
            f"Wins:        {s7['wins']} ({s7['win_rate']}%)\n"
            f"Losses:      {s7['losses']}\n"
            f"Net P&L:     ${s7['net_pnl']:+.2f}\n"
            f"Profit F:    {s7['profit_factor']}\n"
            f"Avg R:       {s7['avg_r']:.2f}R\n"
            f"Best trade:  ${s7['best_pnl']:+.2f}\n"
            f"Worst:       ${s7['worst_pnl']:+.2f}\n"
            f"Best strat:  {s7['best_strategy']}\n"
            f"{'─'*32}\n"
            f"30-DAY\n"
            f"Signals:     {s30['total']}\n"
            f"Win Rate:    {s30['win_rate']}%\n"
            f"Net P&L:     ${s30['net_pnl']:+.2f}\n"
            f"{'─'*32}\n"
            f"BY INSTRUMENT\n{sym_lines}"
            f"Protect capital. Compound consistently. 💪"
        )

    def _csv_row(self, sig, status, pts, usd, r, dur):
        exists = os.path.exists(self.csv)
        try:
            with open(self.csv, "a", newline="") as f:
                w = csv.writer(f)
                if not exists:
                    w.writerow([
                        "signal_id","sym","datetime","direction","strategy",
                        "session","regime","entry","sl","tp1","tp2","tp3",
                        "lot","risk_usd","score","grade","result",
                        "pnl_pts","pnl_usd","r_multiple","duration_mins"
                    ])
                w.writerow([
                    sig.signal_id, sig.sym,
                    sig.created_at.isoformat(), sig.direction, sig.strategy,
                    sig.session, sig.regime,
                    sig.entry, sig.sl, sig.tp1, sig.tp2, sig.tp3,
                    sig.lot, sig.risk_usd, sig.score, sig.grade,
                    status, pts, usd, r, dur
                ])
        except Exception as e:
            log.warning(f"CSV write error: {e}")
