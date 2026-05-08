"""Hermes Telegram intel BACKFILL — populate tg_messages + project_mentions
for the last N hours of monitored group history.

Reuses analyze_message_intel + save_message_with_intel from telegram_group_listener.
Idempotent: UNIQUE(chat_id, msg_id) means re-runs skip already-saved messages.

Run on Mac Mini:
    HOURS_BACK=24 CONCURRENCY=5 python3 backfill_tg_intel.py

Uses a COPIED Telethon session ("backfill" suffix) so the live listener keeps running.
"""
import asyncio
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Set

import aiohttp
from telethon import TelegramClient

# Re-use logic from listener (LLM call, save fn, regexes, group list)
from telegram_group_listener import (
    EVM_RE, SOL_RE, TICKER_RE,
    analyze_message_intel, save_message_with_intel,
    load_monitored_groups,
)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("backfill")

API_ID = int(os.environ.get("TELEGRAM_API_ID") or os.environ.get("TG_API_ID") or 0)
API_HASH = os.environ.get("TELEGRAM_API_HASH") or os.environ.get("TG_API_HASH") or ""

HOURS_BACK = int(os.environ.get("HOURS_BACK", "24"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "5"))
PER_GROUP_LIMIT = int(os.environ.get("PER_GROUP_LIMIT", "5000"))

SOURCE_SESSION = Path("data/telethon.session")
BACKFILL_SESSION = Path("data/telethon_backfill.session")


def _ensure_session() -> None:
    """Copy the live session so we can connect a 2nd client without locking it."""
    if not SOURCE_SESSION.exists():
        logger.error(f"source session not found: {SOURCE_SESSION}")
        sys.exit(1)
    # Always copy fresh (live listener keeps writing — copy gives us a snapshot)
    shutil.copy2(str(SOURCE_SESSION), str(BACKFILL_SESSION))
    logger.info(f"session copied: {BACKFILL_SESSION}")


async def _process_message(msg, chat_obj, chat_name: str,
                            http: aiohttp.ClientSession,
                            sem: asyncio.Semaphore,
                            stats: dict) -> None:
    text = msg.message or ""
    if not text:
        return
    text_stripped = text.strip()
    if len(text_stripped) < 20:
        return

    evm = list(set(EVM_RE.findall(text)))
    sol_raw = SOL_RE.findall(text)
    sol = [s for s in sol_raw if not s.startswith("0x")]
    tickers = list(set(TICKER_RE.findall(text)))

    try:
        sender = await msg.get_sender()
    except Exception:
        sender = None
    sender_name = (getattr(sender, "username", None)
                    or getattr(sender, "first_name", None)
                    or "?")
    sender_id = getattr(sender, "id", 0) or 0

    chat_username = getattr(chat_obj, "username", None)
    msg_url = f"https://t.me/{chat_username}/{msg.id}" if chat_username else ""

    async with sem:
        intel = await analyze_message_intel(text, sender_name, http)
        stats["llm_calls"] += 1

    msg_data = {
        "chat_id": int(chat_obj.id), "chat_name": chat_name,
        "sender_id": int(sender_id), "sender": sender_name,
        "msg_id": int(msg.id), "msg_url": msg_url,
        "text": text[:4000], "ts": int(msg.date.timestamp()),
        "evm_addrs": evm, "sol_addrs": sol, "tickers": tickers,
    }
    db_id, first_ever, _ = save_message_with_intel(msg_data, intel)
    if db_id:
        stats["saved"] += 1
    if first_ever:
        stats["first_ever"] += len(first_ever)


async def backfill() -> None:
    if not API_ID or not API_HASH:
        logger.error("TELEGRAM_API_ID / TELEGRAM_API_HASH missing in env")
        sys.exit(1)

    _ensure_session()
    monitored: Set[int] = load_monitored_groups()
    if not monitored:
        logger.error("no monitored groups in data/monitored_groups.json")
        sys.exit(1)

    cutoff = int(time.time()) - HOURS_BACK * 3600
    logger.info(
        f"backfill: {len(monitored)} group(s), last {HOURS_BACK}h, "
        f"concurrency={CONCURRENCY}, cutoff_ts={cutoff}"
    )

    client = TelegramClient(str(BACKFILL_SESSION).replace(".session", ""),
                             API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        logger.error("backfill session not authorized — re-auth listener first")
        sys.exit(1)

    sem = asyncio.Semaphore(CONCURRENCY)
    stats = {"saved": 0, "first_ever": 0, "llm_calls": 0, "groups_done": 0}

    async with aiohttp.ClientSession() as http:
        for gid in monitored:
            try:
                chat = await client.get_entity(gid)
            except Exception as e:
                logger.warning(f"  cannot resolve group {gid}: {e}")
                continue
            cname = getattr(chat, "title", str(gid))
            logger.info(f"→ [{cname}] ({gid})")

            tasks = []
            count_in_window = 0
            async for msg in client.iter_messages(gid, limit=PER_GROUP_LIMIT):
                if msg.date.timestamp() < cutoff:
                    break
                count_in_window += 1
                tasks.append(_process_message(msg, chat, cname, http, sem, stats))
                if len(tasks) >= 100:
                    await asyncio.gather(*tasks, return_exceptions=True)
                    tasks = []
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            stats["groups_done"] += 1
            logger.info(
                f"   ↳ {count_in_window} msgs in window  "
                f"(running totals: saved={stats['saved']} "
                f"first_ever={stats['first_ever']} "
                f"llm_calls={stats['llm_calls']})"
            )

    await client.disconnect()
    logger.info("=" * 60)
    logger.info(
        f"BACKFILL DONE — groups={stats['groups_done']}/{len(monitored)} "
        f"saved={stats['saved']} first_ever_projects={stats['first_ever']} "
        f"llm_calls={stats['llm_calls']}"
    )


if __name__ == "__main__":
    asyncio.run(backfill())
