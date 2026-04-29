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
        query = " ".join(context.args).strip()
        await update.message.reply_text(f"🔍 Investigating {query}... (full pipeline, ≤15s)")
        try:
            from telegram_group_listener import (
                investigate_token, _format_alert, _build_keyboard,
                resolve_ticker_to_address, send_alert,
            )
            import aiohttp

            # Resolve ticker → address + auto-detect chain via DexScreener
            address = query
            chain = "ethereum"
            ticker_src = None
            if not query.startswith("0x"):
                clean = query.lstrip("$").lstrip("#")
                async with aiohttp.ClientSession() as s:
                    addr, ch = await resolve_ticker_to_address(clean, s)
                if not addr:
                    await update.message.reply_text(
                        f"❌ Could not resolve {query} to a token address."
                    )
                    return
                address, chain = addr, ch
                ticker_src = clean
            else:
                # Autodetect chain from DexScreener (token may be on base/sol/bsc, not eth)
                try:
                    async with aiohttp.ClientSession() as s:
                        async with s.get(f"https://api.dexscreener.com/latest/dex/tokens/{address}",
                                         timeout=aiohttp.ClientTimeout(total=8)) as r:
                            if r.status == 200:
                                data = await r.json()
                                pairs = data.get("pairs") or []
                                if pairs:
                                    pairs.sort(key=lambda p: -(p.get("liquidity", {}).get("usd") or 0))
                                    chain = pairs[0].get("chainId") or "ethereum"
                except Exception:
                    pass

            # Full investigation
            anatomy = await investigate_token(address, chain_hint=chain,
                                              group_name="manual /hunt",
                                              msg_text=query)
            if not anatomy:
                await update.message.reply_text(f"❌ Investigation returned empty for {address}")
                return

            text = _format_alert("manual /hunt", "user", "", anatomy)
            kb = _build_keyboard(address, anatomy.get("chain") or chain)
            bot_token = HERMES_BOT_TOKEN
            chat_id = update.effective_chat.id
            await send_alert(bot_token, chat_id, text, keyboard=kb)
        except Exception as e:
            logger.exception("hunt failed")
            await update.message.reply_text(f"❌ Investigation failed: {type(e).__name__}: {e}")

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
        try:
            hours = int(period.rstrip("h"))
        except ValueError:
            hours = 24
        try:
            from outcome_tracker import stats
            s = stats(window_hours=hours)
            if s.get("alerts_count", 0) == 0:
                await update.message.reply_text(
                    f"📈 <b>Stats últimas {hours}h</b>\n\nNenhum alerta ainda nesta janela.",
                    parse_mode="HTML"
                )
                return
            top_lines = "\n".join([f"  ${sym}: {roi} ({act})"
                                   for sym, roi, act in s.get("top_3_24h", [])])
            text = (
                f"📈 <b>Hermes Stats — últimas {hours}h</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📨 Total alertas: <b>{s['alerts_count']}</b>\n\n"
                f"🎯 Hit rate (≥+20% em 1h):  {s.get('hit_rate_1h_gt20pct', '—')}\n"
                f"🎯 Hit rate (≥+50% em 6h):  {s.get('hit_rate_6h_gt50pct', '—')}\n"
                f"🎯 Hit rate (≥+100% em 24h): {s.get('hit_rate_24h_gt100pct', '—')}\n\n"
                f"📊 Avg ROI 1h:  {s.get('avg_roi_1h_pct', '—')}\n"
                f"📊 Avg ROI 24h: {s.get('avg_roi_24h_pct', '—')}\n\n"
                f"🏆 Top 3 (24h):\n{top_lines or '  —'}"
            )
            await update.message.reply_text(text, parse_mode="HTML")
        except Exception as e:
            logger.exception("stats failed")
            await update.message.reply_text(f"❌ Stats failed: {type(e).__name__}: {e}")

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

async def run_twitter_mega_monitor():
    """Twitter MEGA listener — Elon, CZ, etc. with sub-second reaction."""
    try:
        from twitter_mega_listener import run_listener
        bot_token = os.environ["HERMES_TELEGRAM_BOT_TOKEN"]
        user_chat_id = int(os.environ.get("HERMES_USER_CHAT_ID", "750774735"))
        logger.info("Twitter MEGA monitor: starting")
        await run_listener(bot_token, user_chat_id)
    except Exception as e:
        logger.exception(f"twitter_mega_monitor failed: {e}")
        await asyncio.sleep(30)


async def run_truthsocial_monitor():
    """Polls Trump's TruthSocial via trumpstruth.org and alerts on memecoin keywords."""
    try:
        from truthsocial_monitor import run_monitor
        logger.info("TruthSocial monitor: starting (polls trumpstruth.org every 15s)")
        await run_monitor()
    except Exception as e:
        logger.exception(f"truthsocial_monitor failed: {e}")
        await asyncio.sleep(30)


async def run_smart_money_active():
    """Smart Money active tracker — alerts immediately when KOL wallets buy."""
    try:
        from smart_money_active import run_listener
        bot_token = os.environ["HERMES_TELEGRAM_BOT_TOKEN"]
        user_chat_id = int(os.environ.get("HERMES_USER_CHAT_ID", "750774735"))
        logger.info("Smart Money active: starting (poll Hetzner /smart-money/recent-buys every 30s)")
        await run_listener(bot_token, user_chat_id)
    except Exception as e:
        logger.exception(f"smart_money_active failed: {e}")
        await asyncio.sleep(30)


async def run_outcome_tracker_loop():
    """Background outcome checker — measures ROI of every alert at 1h/6h/24h."""
    try:
        from outcome_tracker import run_outcome_loop
        logger.info("Outcome tracker: starting (checks every 30min)")
        await run_outcome_loop()
    except Exception as e:
        logger.exception(f"outcome tracker failed: {e}")
        await asyncio.sleep(60)


async def run_virtuals_community_monitor():
    """Polls Virtuals Twitter Community for ticker/contract mentions."""
    try:
        from virtuals_community_monitor import run_community_monitor
        bot_token = os.environ["HERMES_TELEGRAM_BOT_TOKEN"]
        user_chat_id = int(os.environ.get("HERMES_USER_CHAT_ID", "750774735"))
        logger.info("Virtuals Community monitor: starting")
        await run_community_monitor(bot_token, user_chat_id)
    except Exception as e:
        logger.exception(f"virtuals_community_monitor failed: {e}")
        await asyncio.sleep(60)


async def run_polymarket_monitor():
    logger.info("Polymarket monitor: TODO wire up")
    while True:
        await asyncio.sleep(60)


async def run_kalshi_monitor():
    logger.info("Kalshi monitor: TODO wire up")
    while True:
        await asyncio.sleep(60)


async def run_telegram_groups_monitor():
    """Listen to monitored Telegram groups via Telethon and alert on token signals."""
    try:
        from telethon import TelegramClient
        from telegram_group_listener import run_listener
        api_id = int(os.environ["TELEGRAM_API_ID"])
        api_hash = os.environ["TELEGRAM_API_HASH"]
        client = TelegramClient(str(HERMES_HOME / "data" / "telethon"), api_id, api_hash)
        await client.connect()
        if not await client.is_user_authorized():
            logger.error("Telethon not authorized — run login_telegram_v3.py first")
            return
        bot_token = os.environ["HERMES_TELEGRAM_BOT_TOKEN"]
        user_chat_id = int(os.environ.get("HERMES_USER_CHAT_ID", "750774735"))
        logger.info("Telegram groups monitor: starting Telethon listener")
        await run_listener(client, bot_token, user_chat_id)
    except Exception as e:
        logger.exception(f"telegram_groups_monitor failed: {e}")
        await asyncio.sleep(30)


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
        asyncio.create_task(run_twitter_mega_monitor(), name="twitter-mega"),
        asyncio.create_task(run_truthsocial_monitor(), name="truthsocial"),
        asyncio.create_task(run_telegram_groups_monitor(), name="telegram-groups"),
        asyncio.create_task(run_smart_money_active(), name="smart-money-active"),
        asyncio.create_task(run_outcome_tracker_loop(), name="outcome-tracker"),
        asyncio.create_task(run_virtuals_community_monitor(), name="virtuals-community"),
        asyncio.create_task(run_polymarket_monitor(), name="polymarket"),
        asyncio.create_task(run_kalshi_monitor(), name="kalshi"),
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
