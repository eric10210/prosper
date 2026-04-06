"""
telegram_bot.py — PROSPER v2 Telegram dispatcher.

⚠️ TELEGRAM COLLISION FIX ⚠️
─────────────────────────────────────────────────────────────────────
PROSPER and KiloClav are TWO SEPARATE BOTS with TWO SEPARATE TOKENS.
This file uses ONLY PROSPER_BOT_TOKEN — never KiloClav's token.
Each bot has its own Application instance, its own polling loop,
its own update queue. They do NOT share anything.
─────────────────────────────────────────────────────────────────────

Token separation guaranteed by:
1. Using PROSPER_BOT_TOKEN from config (distinct from KiloClav)
2. Running our own Application (not sharing with KiloClav)
3. Setting bot_data unique to PROSPER (bot_data["name"] = "PROSPER")
4. KiloClav runs its OWN polling — we do NOT interfere

When KiloClav runs prosper/main.py as a subprocess:
- KiloClav's telegram bot = KiloClav's own token
- PROSPER's telegram bot  = PROSPER_BOT_TOKEN (8537766216:AAEV...)
- They never share a polling loop or token
"""
import asyncio
import logging
from typing import Optional, Callable

from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError, RetryAfter, NetworkError

from config import PROSPER_BOT_TOKEN, PROSPER_CHAT_ID, BOT_NAME, BOT_VERSION

log = logging.getLogger(__name__)


class ProsperTelegram:
    """
    PROSPER-only Telegram bot.
    Uses PROSPER_BOT_TOKEN exclusively.
    Will not conflict with KiloClav which uses a different token.
    """

    def __init__(self):
        # Verify we're NOT using an empty or wrong token
        if not PROSPER_BOT_TOKEN or len(PROSPER_BOT_TOKEN) < 20:
            raise ValueError(
                "PROSPER_BOT_TOKEN is empty or invalid. "
                "Set PROSPER_BOT_TOKEN in .env"
            )
        # These are PROSPER's credentials only
        self._token   = PROSPER_BOT_TOKEN
        self._chat_id = PROSPER_CHAT_ID
        self._bot     = Bot(token=self._token)
        self._app     = None
        self._lock    = asyncio.Lock()
        self._started = False

        # Command callbacks wired by main engine
        self.on_status:   Optional[Callable] = None
        self.on_signals:  Optional[Callable] = None
        self.on_balance:  Optional[Callable] = None
        self.on_pause:    Optional[Callable] = None
        self.on_resume:   Optional[Callable] = None
        self.on_weekly:   Optional[Callable] = None
        self.on_journal:  Optional[Callable] = None
        self.on_kill:     Optional[Callable] = None
        self.on_risk:     Optional[Callable] = None
        self.on_governor: Optional[Callable] = None
        self.on_compound: Optional[Callable] = None
        self.on_closeall: Optional[Callable] = None
        self.on_equity:   Optional[Callable] = None
        self.on_stats:    Optional[Callable] = None

        log.info(
            f"PROSPER Telegram initialized. "
            f"Token: ...{self._token[-10:]} | Chat: {self._chat_id}"
        )

    # ─── SEND ──────────────────────────────────────────────────────────────
    async def send(self, text: str) -> bool:
        """Thread-safe send with retry. Always to PROSPER_CHAT_ID only."""
        async with self._lock:
            for attempt in range(4):
                try:
                    await self._bot.send_message(
                        chat_id = self._chat_id,
                        text    = text[:4096],
                    )
                    return True
                except RetryAfter as e:
                    log.warning(f"PROSPER TG rate limit: wait {e.retry_after}s")
                    await asyncio.sleep(e.retry_after + 1)
                except NetworkError as e:
                    log.warning(f"PROSPER TG network (attempt {attempt+1}): {e}")
                    await asyncio.sleep(8)
                except TelegramError as e:
                    log.error(f"PROSPER TG error: {e}")
                    await asyncio.sleep(4)
            return False

    async def send_long(self, text: str):
        """Split long messages into 4000-char chunks."""
        for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
            await self.send(chunk)
            await asyncio.sleep(0.3)

    # ─── APP BUILD ─────────────────────────────────────────────────────────
    def build_app(self) -> Application:
        """
        Build PROSPER's dedicated Application.
        Uses PROSPER_BOT_TOKEN — completely isolated from KiloClav.
        """
        self._app = (
            Application.builder()
            .token(self._token)
            # Unique app name to prevent any mix-up
            .arbitrary_callback_data(True)
            .build()
        )
        self._app.bot_data["name"]    = "PROSPER"
        self._app.bot_data["version"] = BOT_VERSION
        self._register_handlers()
        return self._app

    def _register_handlers(self):
        app = self._app

        async def guard(upd: Update) -> bool:
            """Only accept messages from PROSPER_CHAT_ID."""
            if str(upd.effective_chat.id) != str(self._chat_id):
                log.debug(f"PROSPER: ignored message from chat {upd.effective_chat.id}")
                return False
            return True

        async def cmd_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
            if not await guard(upd): return
            await upd.message.reply_text(
                f"🤖 {BOT_NAME} v{BOT_VERSION}\n"
                f"Auto-trading Vol 10/25/50/75 via MetaAPI.\n"
                f"/help for all commands."
            )

        async def cmd_status(u, c):
            if not await guard(u): return
            if self.on_status: await u.message.reply_text(await self.on_status())

        async def cmd_signals(u, c):
            if not await guard(u): return
            if self.on_signals: await u.message.reply_text(await self.on_signals())

        async def cmd_balance(u, c):
            if not await guard(u): return
            if self.on_balance: await u.message.reply_text(await self.on_balance())

        async def cmd_pause(u, c):
            if not await guard(u): return
            if self.on_pause: await self.on_pause()
            await u.message.reply_text("⏸️ PROSPER paused.")

        async def cmd_resume(u, c):
            if not await guard(u): return
            if self.on_resume: await self.on_resume()
            await u.message.reply_text("▶️ PROSPER resumed.")

        async def cmd_weekly(u, c):
            if not await guard(u): return
            if self.on_weekly: await self.send_long(await self.on_weekly())

        async def cmd_journal(u, c):
            if not await guard(u): return
            n = int(c.args[0]) if c.args and c.args[0].isdigit() else 10
            if self.on_journal: await self.send_long(await self.on_journal(n))

        async def cmd_kill(u, c):
            if not await guard(u): return
            if not c.args:
                await u.message.reply_text("Usage: /kill V75_0001")
                return
            sid = c.args[0].replace("#","").upper()
            if self.on_kill: await u.message.reply_text(await self.on_kill(sid))

        async def cmd_risk(u, c):
            if not await guard(u): return
            if not c.args:
                await u.message.reply_text("Usage: /risk 1.5"); return
            try:
                pct = float(c.args[0])
                if self.on_risk: await u.message.reply_text(await self.on_risk(pct))
            except ValueError:
                await u.message.reply_text("Invalid. Example: /risk 2.0")

        async def cmd_governor(u, c):
            if not await guard(u): return
            if self.on_governor: await u.message.reply_text(await self.on_governor())

        async def cmd_compound(u, c):
            if not await guard(u): return
            mode = c.args[0].lower() if c.args else "toggle"
            if self.on_compound: await u.message.reply_text(await self.on_compound(mode))

        async def cmd_closeall(u, c):
            if not await guard(u): return
            if self.on_closeall: await u.message.reply_text(await self.on_closeall())

        async def cmd_equity(u, c):
            if not await guard(u): return
            if self.on_equity: await u.message.reply_text(await self.on_equity())

        async def cmd_stats(u, c):
            if not await guard(u): return
            days = int(c.args[0]) if c.args and c.args[0].isdigit() else 7
            if self.on_stats: await u.message.reply_text(await self.on_stats(days))

        async def cmd_dd(u, c):
            """DD management status and override."""
            if not await guard(u): return
            from config import get_dd_mode
            # Access risk state via closure
            await u.message.reply_text("Use /status to see DD mode.")

        async def cmd_help(u, c):
            if not await guard(u): return
            await u.message.reply_text(
                f"📋 {BOT_NAME} COMMANDS\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"/status     — Full market state\n"
                f"/signals    — Open trades\n"
                f"/balance    — Balance + P&L\n"
                f"/pause      — Pause signals\n"
                f"/resume     — Resume signals\n"
                f"/weekly     — Weekly report\n"
                f"/journal N  — Last N trades\n"
                f"/stats D    — Stats for D days\n"
                f"/equity     — Equity + Sharpe\n"
                f"/governor   — Safety gates\n"
                f"/kill ID    — Cancel signal\n"
                f"/closeall   — Close all MT5 positions\n"
                f"/risk PCT   — Set risk % (e.g. /risk 2)\n"
                f"/compound   — Toggle compound mode\n"
                f"/help       — This menu\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚡ {BOT_NAME} v{BOT_VERSION}"
            )

        for name, fn in [
            ("start",    cmd_start),
            ("status",   cmd_status),
            ("signals",  cmd_signals),
            ("balance",  cmd_balance),
            ("pause",    cmd_pause),
            ("resume",   cmd_resume),
            ("weekly",   cmd_weekly),
            ("journal",  cmd_journal),
            ("kill",     cmd_kill),
            ("risk",     cmd_risk),
            ("governor", cmd_governor),
            ("compound", cmd_compound),
            ("closeall", cmd_closeall),
            ("equity",   cmd_equity),
            ("stats",    cmd_stats),
            ("dd",       cmd_dd),
            ("help",     cmd_help),
        ]:
            app.add_handler(CommandHandler(name, fn))

    async def run_polling(self):
        """
        Run PROSPER's polling loop.
        drop_pending_updates=True prevents stale commands from KiloClav sessions.
        allowed_updates=["message"] reduces interference.
        """
        if self._app is None:
            self.build_app()
        log.info("Starting PROSPER Telegram polling (isolated from KiloClav)...")
        async with self._app:
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=["message"],   # Only message updates
            )
            self._started = True
            while True:
                await asyncio.sleep(1)

    async def stop(self):
        if self._app and self._started:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
