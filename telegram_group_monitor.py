"""
Telegram Group Monitor — user-client based (Telethon).

Monitors alpha Telegram groups 24/7, extracts token mentions (contract
addresses, tickers, X handles), triages with LLM, persists to Postgres
table `telegram_signals` for Hermes Agent to consume.

This is a USER client (not bot), using your own Telegram account via MTProto.
Requires API_ID + API_HASH from https://my.telegram.org (free, 2min setup).

Run: python3 telegram_group_monitor.py
Systemd: telegram_group_monitor.service

Storage: Postgres table `telegram_signals` (new schema at bottom of file)

Security:
- Session file (telethon.session) has full login — protect it with chmod 600
- Runs as `hermes` user, no sudo
- Does NOT send messages from your account — read-only listener
"""

import asyncio
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from telethon import TelegramClient, events
except ImportError:
    sys.stderr.write("telethon not installed. Run: pip install telethon\n")
    sys.exit(1)

try:
    import asyncpg
except ImportError:
    sys.stderr.write("asyncpg not installed. Run: pip install asyncpg\n")
    sys.exit(1)

# Add creator-bid-bot to path for LLM helpers
BOT_PATH = Path("/home/ubuntu/creator-bid-bot")
sys.path.insert(0, str(BOT_PATH))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("telegram_monitor")

# =============================================================================
# CONFIGURATION
# =============================================================================

API_ID = int(os.environ.get("TELEGRAM_API_ID", "0"))
API_HASH = os.environ.get("TELEGRAM_API_HASH", "")
SESSION_PATH = os.environ.get("TELETHON_SESSION", "/home/hermes/.hermes/telethon.session")
POSTGRES_DSN = os.environ.get("POSTGRES_DSN")

# Monitored groups (get these IDs from @username_to_id_bot in Telegram)
# Tier 1 = high-signal alpha, Tier 2 = degen/noise
MONITORED_GROUPS: Dict[str, Dict[str, Any]] = {
    # Replace with actual group IDs / usernames once configured
    # "-1001234567890": {"name": "Base Alpha", "tier": 1},
    # "@some_public_group": {"name": "Virtuals Traders", "tier": 2},
}

# =============================================================================
# EXTRACTORS — regex-first triage, LLM for ambiguity
# =============================================================================

EVM_ADDRESS_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
SOLANA_ADDRESS_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
TICKER_RE = re.compile(r"\$([A-Z][A-Z0-9]{1,10})\b")
HANDLE_RE = re.compile(r"@([a-zA-Z0-9_]{4,15})\b")


def extract_mentions(text: str) -> Dict[str, List[str]]:
    """First-pass regex extraction. Fast, no API calls."""
    return {
        "evm_addresses": list(set(EVM_ADDRESS_RE.findall(text))),
        "solana_addresses": [
            a for a in SOLANA_ADDRESS_RE.findall(text)
            # Heuristic: Solana addresses are 32-44 chars and aren't all alphanumeric EVM fragments
            if len(a) >= 32 and not a.startswith("0x")
        ],
        "tickers": list(set(TICKER_RE.findall(text))),
        "handles": list(set(HANDLE_RE.findall(text))),
    }


def has_signal(mentions: Dict[str, List[str]]) -> bool:
    """Is there anything worth recording?"""
    return bool(
        mentions["evm_addresses"]
        or mentions["solana_addresses"]
        or mentions["tickers"]
    )


# =============================================================================
# POSTGRES
# =============================================================================

DDL = """
CREATE TABLE IF NOT EXISTS telegram_signals (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    group_id TEXT NOT NULL,
    group_name TEXT NOT NULL,
    tier INT NOT NULL DEFAULT 2,
    sender_id TEXT,
    sender_handle TEXT,
    message_id BIGINT,
    message_text TEXT NOT NULL,
    reply_to_id BIGINT,
    forward_from TEXT,
    token_address TEXT,         -- canonical address if one detected
    ticker TEXT,                -- canonical ticker if one detected
    chain TEXT,                 -- 'base', 'solana', 'ethereum', 'unknown'
    mentions_json JSONB,        -- full extraction: addresses, tickers, handles
    sentiment TEXT,             -- 'bullish', 'bearish', 'neutral' (LLM)
    urgency_score REAL,         -- 0-1 from LLM triage
    is_shill INT DEFAULT 0      -- 1 if LLM flags as shill/spam
);

CREATE INDEX IF NOT EXISTS idx_tg_signals_created ON telegram_signals (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tg_signals_token ON telegram_signals (token_address) WHERE token_address IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tg_signals_ticker ON telegram_signals (ticker) WHERE ticker IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tg_signals_group ON telegram_signals (group_id, created_at DESC);
"""


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


async def persist_signal(conn: asyncpg.Connection, signal: Dict[str, Any]) -> None:
    await conn.execute(
        """
        INSERT INTO telegram_signals (
            group_id, group_name, tier, sender_id, sender_handle,
            message_id, message_text, reply_to_id, forward_from,
            token_address, ticker, chain, mentions_json,
            sentiment, urgency_score, is_shill
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16
        )
        """,
        signal["group_id"],
        signal["group_name"],
        signal["tier"],
        signal.get("sender_id"),
        signal.get("sender_handle"),
        signal.get("message_id"),
        signal["message_text"],
        signal.get("reply_to_id"),
        signal.get("forward_from"),
        signal.get("token_address"),
        signal.get("ticker"),
        signal.get("chain"),
        signal["mentions_json"],
        signal.get("sentiment"),
        signal.get("urgency_score"),
        signal.get("is_shill", 0),
    )


# =============================================================================
# LLM TRIAGE — only for messages with signals, to save tokens
# =============================================================================

async def llm_triage(text: str, mentions: Dict[str, List[str]]) -> Dict[str, Any]:
    """
    Classify a Telegram message using existing call_llm helper from utils.
    Returns {sentiment, urgency_score, is_shill}.
    Falls back to defaults if LLM fails.
    """
    try:
        import aiohttp
        from utils import call_llm

        prompt = f"""Classify this Telegram alpha message. Be strict — reject shills.

Message: {text[:500]}

Detected: {mentions}

Output JSON only:
{{
  "sentiment": "bullish" | "bearish" | "neutral",
  "urgency_score": 0.0 to 1.0,  # 1.0 = immediate action suggested
  "is_shill": 0 or 1,           # 1 if message reads like a paid shill
  "reason": "short explanation"
}}"""

        async with aiohttp.ClientSession() as session:
            result = await call_llm(
                session,
                prompt,
                nvidia_model="moonshotai/kimi-k2-instruct",
                nvidia_fallbacks=["meta/llama-4-maverick-17b-128e-instruct"],
                max_tokens=200,
                parse_json=True,
            )
            if isinstance(result, dict):
                return {
                    "sentiment": result.get("sentiment", "neutral"),
                    "urgency_score": float(result.get("urgency_score", 0.5)),
                    "is_shill": int(result.get("is_shill", 0)),
                }
    except Exception as e:
        logger.warning(f"LLM triage failed: {e}")

    return {"sentiment": "neutral", "urgency_score": 0.5, "is_shill": 0}


# =============================================================================
# MAIN LOOP
# =============================================================================

class TelegramMonitor:
    def __init__(self) -> None:
        self.client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
        self.pool: Optional[asyncpg.Pool] = None

    async def start(self) -> None:
        if not (API_ID and API_HASH):
            logger.error("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set")
            sys.exit(1)
        if not POSTGRES_DSN:
            logger.error("POSTGRES_DSN must be set")
            sys.exit(1)

        self.pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=1, max_size=3)
        async with self.pool.acquire() as conn:
            await ensure_schema(conn)

        await self.client.start()
        logger.info(
            f"Telethon connected. Monitoring {len(MONITORED_GROUPS)} groups."
        )

        # Register event handler
        self.client.add_event_handler(
            self.on_message,
            events.NewMessage(chats=list(MONITORED_GROUPS.keys())),
        )

        logger.info("Listening for messages...")
        await self.client.run_until_disconnected()

    async def on_message(self, event: events.NewMessage.Event) -> None:
        try:
            await self._handle_message(event)
        except Exception as e:
            logger.exception(f"Error handling message: {e}")

    async def _handle_message(self, event: events.NewMessage.Event) -> None:
        text = event.message.message or ""
        if not text or len(text) < 5:
            return

        chat = await event.get_chat()
        chat_id = str(event.chat_id)
        group_cfg = MONITORED_GROUPS.get(chat_id) or MONITORED_GROUPS.get(
            f"@{getattr(chat, 'username', '')}"
        )
        if not group_cfg:
            return

        mentions = extract_mentions(text)
        if not has_signal(mentions):
            return  # skip chit-chat

        sender = await event.get_sender()
        sender_handle = getattr(sender, "username", None)

        # Decide canonical token for index columns
        token_address = None
        chain = "unknown"
        if mentions["evm_addresses"]:
            token_address = mentions["evm_addresses"][0].lower()
            chain = "base"  # default assumption; could refine with chain detection later
        elif mentions["solana_addresses"]:
            token_address = mentions["solana_addresses"][0]
            chain = "solana"

        ticker = mentions["tickers"][0] if mentions["tickers"] else None

        # LLM triage (only for signal-bearing messages)
        triage = await llm_triage(text, mentions)

        signal = {
            "group_id": chat_id,
            "group_name": group_cfg["name"],
            "tier": group_cfg["tier"],
            "sender_id": str(getattr(sender, "id", "")),
            "sender_handle": sender_handle,
            "message_id": event.message.id,
            "message_text": text[:4000],
            "reply_to_id": event.message.reply_to_msg_id,
            "forward_from": None,  # TODO extract if forwarded
            "token_address": token_address,
            "ticker": ticker,
            "chain": chain,
            "mentions_json": mentions,
            "sentiment": triage["sentiment"],
            "urgency_score": triage["urgency_score"],
            "is_shill": triage["is_shill"],
        }

        async with self.pool.acquire() as conn:
            await persist_signal(conn, signal)

        logger.info(
            f"Signal stored: group={group_cfg['name']} "
            f"ticker={ticker} addr={token_address} "
            f"urgency={triage['urgency_score']:.2f} shill={triage['is_shill']}"
        )


async def main() -> None:
    monitor = TelegramMonitor()
    await monitor.start()


if __name__ == "__main__":
    asyncio.run(main())
