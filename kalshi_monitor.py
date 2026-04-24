"""
Kalshi Monitor — US regulated prediction market (complements Polymarket).

Kalshi is CFTC-regulated, covers politics, economy, crypto, weather, sports.
Gives LEADING INDICATOR alpha: crowd wisdom before Twitter viralizes.

Architecture:
- WSS subscription to market ticker updates (sub-second)
- REST discovery every 5min for new crypto-tagged markets
- Emits hunter_signals on significant odds changes (>5pp move)

Run: python3 kalshi_monitor.py
Systemd: kalshi_monitor.service

Auth:
- Free account at kalshi.com → Settings → API keys
- KALSHI_EMAIL and KALSHI_API_KEY in .env
- Free tier: 1000 REST req/day, WSS unlimited
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    import aiohttp
    import asyncpg
    import websockets
except ImportError:
    sys.stderr.write("Missing deps. Run: pip install aiohttp asyncpg websockets\n")
    sys.exit(1)

BOT_PATH = Path("/home/ubuntu/creator-bid-bot")
sys.path.insert(0, str(BOT_PATH))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("kalshi_monitor")

# =============================================================================
# CONFIG
# =============================================================================

POSTGRES_DSN = os.environ.get("POSTGRES_DSN")
KALSHI_EMAIL = os.environ.get("KALSHI_EMAIL")
KALSHI_API_KEY = os.environ.get("KALSHI_API_KEY")
KALSHI_API_BASE = "https://trading-api.kalshi.com/trade-api/v2"
KALSHI_WSS_URL = "wss://trading-api.kalshi.com/trade-api/ws/v2"

DISCOVERY_POLL_SECONDS = 300  # 5 min
ODDS_CHANGE_THRESHOLD = 0.05  # 5 percentage points

CRYPTO_KEYWORDS = [
    "bitcoin", "btc", "ethereum", "eth", "crypto", "token", "coin",
    "solana", "sol", "doge", "pepe", "memecoin",
    "trump", "election", "president",
    "musk", "spacex", "tesla", "x platform",
    "ai", "openai", "anthropic",
    "binance", "coinbase", "kraken",
    "fed", "rate cut", "rate hike", "inflation",
]


# =============================================================================
# SCHEMA (mirror from schema.sql)
# =============================================================================

DDL = """
CREATE TABLE IF NOT EXISTS kalshi_events (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT UNIQUE NOT NULL,            -- e.g. 'PRESPARTY-24-DEM'
    event_ticker TEXT,                       -- parent event
    title TEXT,
    category TEXT,
    status TEXT,                             -- 'open', 'closed', 'settled'
    volume_usd NUMERIC,
    open_interest NUMERIC,
    yes_price REAL,                          -- current YES price (0-1)
    no_price REAL,                           -- current NO price (0-1)
    is_crypto_related INT DEFAULT 0,
    matched_keywords TEXT[],
    extracted_tickers TEXT[],                -- crypto tickers in title
    expected_expiration TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ DEFAULT NOW(),
    last_updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_kalshi_ticker ON kalshi_events (ticker);
CREATE INDEX IF NOT EXISTS idx_kalshi_crypto ON kalshi_events (is_crypto_related, last_updated_at DESC)
    WHERE is_crypto_related = 1;
CREATE INDEX IF NOT EXISTS idx_kalshi_tickers ON kalshi_events USING GIN (extracted_tickers);
"""


# =============================================================================
# EXTRACTION
# =============================================================================

TICKER_PATTERNS = [
    r"\$([A-Z]{2,10})\b",
    r"\(([A-Z]{2,10})\)",
]


def extract_tickers(title: str) -> List[str]:
    tickers: Set[str] = set()
    for pat in TICKER_PATTERNS:
        for m in re.finditer(pat, title or ""):
            t = m.group(1).upper()
            if 2 <= len(t) <= 10:
                tickers.add(t)
    return sorted(tickers)


def is_crypto_related(title: str, subtitle: str = "") -> tuple[bool, List[str]]:
    text = ((title or "") + " " + (subtitle or "")).lower()
    matched = [kw for kw in CRYPTO_KEYWORDS if kw in text]
    return bool(matched), matched


# =============================================================================
# POSTGRES
# =============================================================================

async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


async def upsert_market(
    conn: asyncpg.Connection,
    mkt: Dict[str, Any],
) -> tuple[bool, Optional[float]]:
    """Returns (is_crypto, previous_yes_price)."""
    title = mkt.get("title") or mkt.get("question") or ""
    subtitle = mkt.get("subtitle") or ""
    is_crypto, matched = is_crypto_related(title, subtitle)
    extracted = extract_tickers(title)

    yes_price = float(mkt.get("yes_price") or mkt.get("last_price") or 0) / 100.0
    no_price = 1.0 - yes_price if yes_price else None

    prev = await conn.fetchrow(
        "SELECT yes_price FROM kalshi_events WHERE ticker = $1",
        mkt.get("ticker"),
    )
    prev_yes = prev["yes_price"] if prev else None

    await conn.execute(
        """
        INSERT INTO kalshi_events (
            ticker, event_ticker, title, category, status, volume_usd,
            open_interest, yes_price, no_price, is_crypto_related,
            matched_keywords, extracted_tickers, expected_expiration
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        ON CONFLICT (ticker) DO UPDATE SET
            title = EXCLUDED.title,
            status = EXCLUDED.status,
            volume_usd = EXCLUDED.volume_usd,
            open_interest = EXCLUDED.open_interest,
            yes_price = EXCLUDED.yes_price,
            no_price = EXCLUDED.no_price,
            is_crypto_related = EXCLUDED.is_crypto_related,
            matched_keywords = EXCLUDED.matched_keywords,
            extracted_tickers = EXCLUDED.extracted_tickers,
            last_updated_at = NOW()
        """,
        mkt.get("ticker"),
        mkt.get("event_ticker"),
        title,
        mkt.get("category"),
        mkt.get("status"),
        float(mkt.get("volume") or 0),
        float(mkt.get("open_interest") or 0),
        yes_price,
        no_price,
        1 if is_crypto else 0,
        matched,
        extracted,
        mkt.get("expected_expiration_time"),
    )
    return is_crypto, prev_yes


async def emit_hunter_signal(
    conn: asyncpg.Connection,
    mkt: Dict[str, Any],
    event_type: str,
    prev_price: Optional[float],
    new_price: float,
    tickers: List[str],
) -> None:
    await conn.execute(
        """
        INSERT INTO hunter_signals (source, source_weight, token_address, chain, event_type, raw_data)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        "kalshi",
        2.0,
        None,
        "prediction_market",
        event_type,
        json.dumps({
            "ticker": mkt.get("ticker"),
            "title": mkt.get("title"),
            "tickers": tickers,
            "new_yes_price": new_price,
            "previous_yes_price": prev_price,
            "price_delta": (new_price - prev_price) if prev_price is not None else None,
            "volume_usd": mkt.get("volume"),
            "url": f"https://kalshi.com/markets/{mkt.get('event_ticker')}",
        }),
    )


# =============================================================================
# HTTP (REST — discovery)
# =============================================================================

async def make_auth_headers(method: str, path: str) -> Dict[str, str]:
    """Kalshi auth: HMAC-SHA256 signature over timestamp+method+path."""
    if not (KALSHI_EMAIL and KALSHI_API_KEY):
        return {}
    ts = str(int(time.time() * 1000))
    message = ts + method + path
    signature = base64.b64encode(
        hmac.new(
            KALSHI_API_KEY.encode(),
            message.encode(),
            hashlib.sha256,
        ).digest()
    ).decode()
    return {
        "KALSHI-ACCESS-KEY": KALSHI_EMAIL,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "accept": "application/json",
    }


async def discover_markets(
    session: aiohttp.ClientSession, pool: asyncpg.Pool
) -> List[str]:
    """Fetch active markets, filter crypto-tagged, return ticker list."""
    path = "/trade-api/v2/markets"
    headers = await make_auth_headers("GET", path)
    url = f"https://trading-api.kalshi.com{path}"
    params = {"status": "open", "limit": 1000}

    try:
        async with session.get(url, headers=headers, params=params,
                                timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                logger.warning(f"Kalshi markets API returned {resp.status}")
                return []
            data = await resp.json()
            markets = data.get("markets", [])
    except Exception as e:
        logger.exception(f"Kalshi discovery failed: {e}")
        return []

    crypto_tickers: List[str] = []
    async with pool.acquire() as conn:
        for mkt in markets:
            try:
                is_crypto, prev = await upsert_market(conn, mkt)
                if is_crypto:
                    crypto_tickers.append(mkt.get("ticker"))
                    yes_price = float(mkt.get("yes_price") or 0) / 100.0
                    tickers = extract_tickers(mkt.get("title", ""))
                    if prev is None:
                        await emit_hunter_signal(
                            conn, mkt, "new_crypto_market", None, yes_price, tickers,
                        )
            except Exception as e:
                logger.exception(f"Market {mkt.get('ticker')} failed: {e}")

    logger.info(f"Discovery: {len(markets)} markets, {len(crypto_tickers)} crypto-related")
    return crypto_tickers


# =============================================================================
# WSS (sub-second ticker updates)
# =============================================================================

async def wss_listen(pool: asyncpg.Pool, subscribed_tickers: Set[str]) -> None:
    while True:
        try:
            async with websockets.connect(KALSHI_WSS_URL) as ws:
                # Auth via first message
                if KALSHI_EMAIL and KALSHI_API_KEY:
                    ts = str(int(time.time() * 1000))
                    message = ts + "GET/trade-api/ws/v2"
                    signature = base64.b64encode(
                        hmac.new(
                            KALSHI_API_KEY.encode(),
                            message.encode(),
                            hashlib.sha256,
                        ).digest()
                    ).decode()
                    auth_msg = {
                        "id": 1,
                        "cmd": "auth",
                        "params": {
                            "key": KALSHI_EMAIL,
                            "signature": signature,
                            "timestamp": ts,
                        },
                    }
                    await ws.send(json.dumps(auth_msg))

                subscribe_msg = {
                    "id": 2,
                    "cmd": "subscribe",
                    "params": {
                        "channels": ["ticker"],
                        "market_tickers": list(subscribed_tickers),
                    },
                }
                await ws.send(json.dumps(subscribe_msg))
                logger.info(f"Kalshi WSS subscribed to {len(subscribed_tickers)} markets")

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        await handle_wss_message(pool, msg)
                    except json.JSONDecodeError:
                        continue
                    except Exception as e:
                        logger.exception(f"WSS message handler: {e}")

        except (websockets.ConnectionClosed, OSError) as e:
            logger.warning(f"Kalshi WSS disconnected: {e} — reconnecting 5s")
            await asyncio.sleep(5)
        except Exception as e:
            logger.exception(f"Kalshi WSS loop error: {e}")
            await asyncio.sleep(10)


async def handle_wss_message(pool: asyncpg.Pool, msg: Dict[str, Any]) -> None:
    msg_type = msg.get("type")
    if msg_type != "ticker":
        return

    data = msg.get("msg") or {}
    ticker = data.get("market_ticker") or data.get("ticker")
    yes_price_cents = data.get("yes_price") or data.get("price")
    if not ticker or yes_price_cents is None:
        return

    try:
        new_yes = float(yes_price_cents) / 100.0
    except (ValueError, TypeError):
        return

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT yes_price, title, extracted_tickers, event_ticker, is_crypto_related
            FROM kalshi_events WHERE ticker = $1
            """,
            ticker,
        )
        if not row or not row["is_crypto_related"]:
            return
        prev_yes = row["yes_price"]

        await conn.execute(
            """
            UPDATE kalshi_events
            SET yes_price = $1, no_price = $2, last_updated_at = NOW()
            WHERE ticker = $3
            """,
            new_yes,
            1.0 - new_yes,
            ticker,
        )

        if prev_yes is not None and abs(new_yes - prev_yes) >= ODDS_CHANGE_THRESHOLD:
            mkt_stub = {
                "ticker": ticker,
                "title": row["title"],
                "event_ticker": row["event_ticker"],
                "volume": None,
            }
            tickers = list(row["extracted_tickers"] or [])
            await emit_hunter_signal(
                conn, mkt_stub, "wss_odds_change", prev_yes, new_yes, tickers,
            )
            logger.info(
                f"ODDS CHANGE: {row['title'][:60]} "
                f"{prev_yes:.3f} → {new_yes:.3f}"
            )


# =============================================================================
# ORCHESTRATOR
# =============================================================================

async def discovery_loop(pool: asyncpg.Pool, subscribed: Set[str]) -> None:
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                new = await discover_markets(session, pool)
                subscribed.update(new)
            except Exception as e:
                logger.exception(f"Discovery iteration failed: {e}")
            await asyncio.sleep(DISCOVERY_POLL_SECONDS)


async def main() -> None:
    if not POSTGRES_DSN:
        logger.error("POSTGRES_DSN must be set")
        sys.exit(1)
    if not (KALSHI_EMAIL and KALSHI_API_KEY):
        logger.warning(
            "KALSHI_EMAIL or KALSHI_API_KEY not set — running in discovery-only mode "
            "(no WSS, public-only endpoints)"
        )

    pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=1, max_size=3)
    async with pool.acquire() as conn:
        await ensure_schema(conn)
    logger.info("Schema ready.")

    subscribed: Set[str] = set()

    async with aiohttp.ClientSession() as session:
        initial = await discover_markets(session, pool)
        subscribed.update(initial)

    tasks = [discovery_loop(pool, subscribed)]
    if KALSHI_EMAIL and KALSHI_API_KEY:
        tasks.append(wss_listen(pool, subscribed))

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
