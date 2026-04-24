#!/usr/bin/env python3
"""
Hermes Mac — main entry point.

Orchestrates all monitors and the Hermes Agent.
Runs on the Mac, sends alerts outbound to Telegram.
Queries Hetzner Data API for enrichment (read-only).

Launches (in parallel):
  1. Telegram bot handler (for user commands + sending alerts)
  2. Monitors (Telegram groups, Polymarket, Kalshi, blockchain, Twitter)
  3. Convergence engine (processes hunter_signals)
  4. Hermes Agent investigation worker pool
  5. Outcome tracker

Single process, async I/O. Safe to restart.
"""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / "hermes-mac"))
sys.path.insert(0, str(HERMES_HOME / "src"))

load_dotenv(HERMES_HOME / ".env")

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(HERMES_HOME / "logs" / "hermes.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("hermes_main")

# =============================================================================
# Bot allowlist — ONLY the user can command the bot
# =============================================================================

HERMES_USER_CHAT_ID = os.environ.get("HERMES_USER_CHAT_ID", "750774735")
HERMES_BOT_TOKEN = os.environ.get("HERMES_TELEGRAM_BOT_TOKEN")

if not HERMES_BOT_TOKEN:
    logger.error("HERMES_TELEGRAM_BOT_TOKEN not set — edit .env")
    sys.exit(1)

ALLOWED_USER_IDS = {int(HERMES_USER_CHAT_ID)}


# =============================================================================
# Telegram bot (commands + alerts)
# =============================================================================

async def run_telegram_bot() -> None:
    """Telegram bot that handles /hunt, /status, /stats, etc."""
    from telegram import Update
    from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

    async def require_auth(update: Update) -> bool:
        """Only allow commands from whitelisted user."""
        user_id = update.effective_user.id if update.effective_user else None
        if user_id not in ALLOWED_USER_IDS:
            logger.warning(f"Denied command from unauthorized user {user_id}")
            if update.message:
                await update.message.reply_text("⛔ Unauthorized.")
            return False
        return True

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await require_auth(update):
            return
        await update.message.reply_text(
            "🧠 Dual Hermes Hunter online.\n\n"
            "Commands:\n"
            "  /hunt 0x...     — investigate a token\n"
            "  /hunt $TICKER   — search cross-chain by ticker\n"
            "  /status          — system health\n"
            "  /stats 24h       — hit rate\n"
            "  /narratives      — active narratives\n"
            "  /hermes_stop     — emergency kill switch"
        )

    async def hunt_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await require_auth(update):
            return
        if not context.args:
            await update.message.reply_text("Usage: /hunt 0xabc... or /hunt $TICKER")
            return
        query = " ".join(context.args)
        await update.message.reply_text(f"🔍 Investigating {query}... (≤30s)")
        # TODO: trigger investigation pipeline
        # For now, stub
        await update.message.reply_text("⚠️ Investigation pipeline not wired yet — stub response")

    async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await require_auth(update):
            return
        # TODO: report on monitor health
        await update.message.reply_text(
            "📊 Status\n"
            "━━━━━━━━━━━━━━━\n"
            "Monitors: (TODO)\n"
            "Queue: (TODO)\n"
            "Alerts today: (TODO)"
        )

    async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await require_auth(update):
            return
        period = context.args[0] if context.args else "24h"
        await update.message.reply_text(f"📈 Stats {period}: (TODO)")

    async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await require_auth(update):
            return
        await update.message.reply_text("🛑 Kill switch activated. Stopping...")
        # Trigger graceful shutdown
        os.kill(os.getpid(), signal.SIGTERM)

    async def deny_anyone_else(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Catch-all — silently deny messages from non-allowed users."""
        if update.effective_user and update.effective_user.id not in ALLOWED_USER_IDS:
            logger.warning(f"Denied message from {update.effective_user.id}")
            # Don't respond — they don't exist to us

    app = Application.builder().token(HERMES_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("hunt", hunt_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("hermes_stop", stop_cmd))
    app.add_handler(MessageHandler(filters.ALL, deny_anyone_else))

    logger.info(f"Telegram bot starting (allowed user: {HERMES_USER_CHAT_ID})")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    # Keep running until cancelled
    try:
        await asyncio.Event().wait()
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


# =============================================================================
# Monitor tasks (placeholder stubs — full versions in individual modules)
# =============================================================================

async def run_polymarket_monitor():
    logger.info("Polymarket monitor: TODO wire up")
    while True:
        await asyncio.sleep(60)


async def run_kalshi_monitor():
    logger.info("Kalshi monitor: TODO wire up")
    while True:
        await asyncio.sleep(60)


async def run_telegram_groups_monitor():
    logger.info("Telegram groups monitor: TODO wire up")
    while True:
        await asyncio.sleep(60)


async def run_convergence_engine():
    logger.info("Convergence engine: TODO wire up")
    while True:
        await asyncio.sleep(30)


# =============================================================================
# Main orchestrator
# =============================================================================

async def main():
    logger.info("=" * 50)
    logger.info("DualHermes Hunter — Mac process starting")
    logger.info(f"HERMES_HOME: {HERMES_HOME}")
    logger.info(f"User chat: {HERMES_USER_CHAT_ID}")
    logger.info("=" * 50)

    # Graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(shutdown(s)))

    # Run all monitors + bot in parallel
    tasks = [
        asyncio.create_task(run_telegram_bot(), name="telegram-bot"),
        asyncio.create_task(run_polymarket_monitor(), name="polymarket"),
        asyncio.create_task(run_kalshi_monitor(), name="kalshi"),
        asyncio.create_task(run_telegram_groups_monitor(), name="telegram-groups"),
        asyncio.create_task(run_convergence_engine(), name="convergence"),
    ]

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Main tasks cancelled, shutting down")


async def shutdown(sig):
    logger.info(f"Received {sig.name}, shutting down...")
    for task in asyncio.all_tasks():
        if task is not asyncio.current_task():
            task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted")
