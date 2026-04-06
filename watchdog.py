"""
watchdog.py — PROSPER v2 Uptime Monitor + Flask Heartbeat.
Flask runs on port 8080 for UptimeRobot / KiloClav health pings.
Scheduled: daily heartbeat 00:05 UTC, daily brief 07:05 UTC, weekly Sunday 22:00 UTC.
Equity snapshot logged every 5 minutes.
"""
import asyncio
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

log = logging.getLogger(__name__)

_start_time = datetime.now(timezone.utc)


# ─── FLASK HEARTBEAT (runs in background thread) ─────────────────────────────
def start_heartbeat_server(port: int = 8080):
    """
    Lightweight Flask HTTP server for external uptime monitoring.
    KiloClav can ping http://localhost:8080/health to confirm PROSPER is alive.
    Runs in a daemon thread — does not block the async event loop.
    """
    try:
        from flask import Flask, jsonify
        app = Flask("prosper_heartbeat")

        # Silence Flask/Werkzeug logging noise
        logging.getLogger("werkzeug").setLevel(logging.ERROR)

        @app.route("/")
        @app.route("/health")
        def health():
            uptime = (datetime.now(timezone.utc) - _start_time).total_seconds() / 3600
            return jsonify({
                "status":     "online",
                "bot":        "PROSPER",
                "version":    "2.0.0",
                "uptime_hrs": round(uptime, 2),
                "timestamp":  datetime.now(timezone.utc).isoformat(),
            })

        @app.route("/ping")
        def ping():
            return "pong", 200

        t = threading.Thread(
            target=lambda: app.run(
                host="0.0.0.0", port=port, debug=False, use_reloader=False
            ),
            daemon=True,
            name="prosper-heartbeat",
        )
        t.start()
        log.info(f"Heartbeat server started on port {port}")

    except ImportError:
        log.warning("Flask not found — heartbeat server disabled. pip install flask")
    except OSError as e:
        log.warning(f"Heartbeat server port {port} unavailable: {e}")
    except Exception as e:
        log.warning(f"Heartbeat server error: {e}")


# ─── WATCHDOG CLASS ───────────────────────────────────────────────────────────
class Watchdog:
    def __init__(self, send_fn: Callable, get_stats_fn: Callable = None):
        self.send      = send_fn
        self.get_stats = get_stats_fn
        self._pulse    = datetime.now(timezone.utc).timestamp()
        self._alive    = True

    def pulse(self):
        """Call every main loop cycle to confirm liveness."""
        self._pulse = datetime.now(timezone.utc).timestamp()

    # ─── TASKS ────────────────────────────────────────────────────────────────
    async def run_monitor(self, timeout_secs: int = 120):
        """Alert if no pulse received for timeout_secs."""
        while self._alive:
            await asyncio.sleep(30)
            age = datetime.now(timezone.utc).timestamp() - self._pulse
            if age > timeout_secs:
                log.error(f"Watchdog: no pulse for {age:.0f}s!")
                try:
                    await self.send(
                        f"🔴 PROSPER WATCHDOG\n"
                        f"No heartbeat for {age:.0f}s.\n"
                        f"System may be frozen — check server."
                    )
                except Exception:
                    pass

    async def run_daily_heartbeat(self):
        """Send daily status at 00:05 UTC."""
        while self._alive:
            now    = datetime.now(timezone.utc)
            target = now.replace(hour=0, minute=5, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())
            await self._send_heartbeat()

    async def _send_heartbeat(self):
        uptime = (datetime.now(timezone.utc) - _start_time).total_seconds() / 3600
        stats  = {}
        if self.get_stats:
            try:
                stats = self.get_stats(days=1)
            except Exception:
                pass
        await self.send(
            f"💚 PROSPER HEARTBEAT\n"
            f"Date:    {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC')}\n"
            f"Uptime:  {uptime:.1f}hrs\n"
            f"Yest:    {stats.get('total',0)} signals | "
            f"{stats.get('wins',0)}W | ${stats.get('net_pnl',0):+.2f}\n"
            f"Status:  ✅ Nominal"
        )

    async def run_daily_brief(self, brief_fn: Callable):
        """Send market brief at 07:05 UTC (London open)."""
        while self._alive:
            now    = datetime.now(timezone.utc)
            target = now.replace(hour=7, minute=5, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            await asyncio.sleep((target - now).total_seconds())
            try:
                brief = await brief_fn()
                await self.send(brief)
            except Exception as e:
                log.error(f"Daily brief error: {e}")

    async def run_weekly_report(self, report_fn: Callable):
        """Send weekly report every Sunday at 22:00 UTC."""
        while self._alive:
            now     = datetime.now(timezone.utc)
            days_to = (6 - now.weekday()) % 7
            target  = now.replace(hour=22, minute=0, second=0, microsecond=0)
            if days_to == 0 and now.hour >= 22:
                days_to = 7
            target += timedelta(days=days_to)
            await asyncio.sleep((target - now).total_seconds())
            try:
                report = await report_fn()
                await self.send(report)
            except Exception as e:
                log.error(f"Weekly report error: {e}")

    async def run_equity_log(self, equity_fn: Callable):
        """Snapshot equity every 5 minutes."""
        while self._alive:
            await asyncio.sleep(300)
            try:
                await equity_fn()
            except Exception as e:
                log.debug(f"Equity snapshot error: {e}")

    def stop(self):
        self._alive = False
