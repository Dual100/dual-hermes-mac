"""
Polymarket Monitor — WebSocket-first prediction market listener.

Prediction markets are LEADING INDICATORS: crowd wisdom reflects odds
before mainstream media notices. Paying attention to POLYMARKET ODDS
CHANGES gives alpha before Twitter viralizes.

Architecture:
- Primary: WebSocket subscription to polymarket CLOB event updates (sub-second)
- Secondary: REST poll every 5min for new crypto-tagged events (to subscribe)
- Storage: Postgres polymarket_events + emits hunter_signals on changes

Run: python3 polymarket_monitor.py
Systemd: polymarket_monitor.service
"""

import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    import aiohttp
    import asyncpg
    import websockets
except ImportError:
    sys.stderr.write(
        "Missing deps. Run: pip install aiohttp asyncpg websockets\n"
    )
    sys.exit(1)

BOT_PATH = Path("/home/ubuntu/creator-bid-bot")
sys.path.insert(0, str(BOT_PATH))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("polymarket_monitor")

# =============================================================================
# CONFIG
# =============================================================================

POSTGRES_DSN = os.environ.get("POSTGRES_DSN")

GAMMA_API_BASE = "https://gamma-api.polymarket.com"
CLOB_WSS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
DISCOVERY_POLL_SECONDS = 300  # 5 min — only for DISCOVERING new markets to subscribe to

# Keywords that flag a market as crypto-relevant
CRYPTO_KEYWORDS = [
    "token", "ticker", "coin", "crypto",
    "bitcoin", "btc", "ethereum", "eth",
    "solana", "sol", "doge", "pepe",
    "memecoin", "memecoins", "shitcoin",
    "launch", "public", "ipo", "listing",
    "exchange", "binance", "coinbase",
    "trump", "musk", "elon", "spacex",
    "ai", "agi", "singularity",
    "dogecoin", "shiba", "flork",
]

# Significant odds change threshold (emit signal if top_outcome_odds moves > X)
ODDS_CHANGE_THRESHOLD = 0.05  # 5 pp change

# =============================================================================
# SCHEMA (mirrored from schema.sql — for standalone ops)
# =============================================================================

DDL = """
CREATE TABLE IF NOT EXISTS polymarket_events (
    id BIGSERIAL PRIMARY KEY,
    polymarket_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    slug TEXT,
    category TEXT,
    volume_usd NUMERIC,
    liquidity_usd NUMERIC,
    open_interest_usd NUMERIC,
    is_crypto_related INT DEFAULT 0,
    matched_keywords TEXT[],
    extracted_tickers TEXT[],
    outcomes JSONB,
    top_outcome TEXT,
    top_outcome_odds REAL,
    first_seen_at TIMESTAMPTZ DEFAULT NOW(),
    last_updated_at TIMESTAMPTZ DEFAULT NOW(),
    end_date TIMESTAMPTZ,
    trending_rank INT,
    trending_velocity REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pm_id ON polymarket_events (polymarket_id);
CREATE INDEX IF NOT EXISTS idx_pm_crypto ON polymarket_events (is_crypto_related, last_updated_at DESC)
    WHERE is_crypto_related = 1;
"""

# =============================================================================
# TICKER EXTRACTION
# =============================================================================

TICKER_PATTERNS = [
    r"\$([A-Z]{2,10})\b",
    r"\(([A-Z]{2,10})\)",
    r"(?:named|called)\s+([A-Z]{2,10})\b",
]


def extract_tickers_from_title(title: str) -> List[str]:
    tickers: Set[str] = set()
    for pat in TICKER_PATTERNS:
        for m in re.finditer(pat, title):
            t = m.group(1).upper()
            if 2 <= len(t) <= 10:
                tickers.add(t)
    return sorted(tickers)


def is_crypto_related(title: str, description: str = "") -> tuple[bool, List[str]]:
    text = (title + " " + (description or "")).lower()
    matched = [kw for kw in CRYPTO_KEYWORDS if kw in text]
    return bool(matched), matched


def extract_outcomes(event: Dict[str, Any]) -> tuple[List[Dict], Optional[str], Optional[float]]:
    markets = event.get("markets") or []
    outcomes = []
    for mkt in markets:
        option = mkt.get("question") or mkt.get("groupItemTitle") or ""
        try:
            price = float(
                mkt.get("lastTradePrice")
                or (mkt.get("outcomePrices") or ["0"])[0]
                or 0
            )
        except (ValueError, TypeError, IndexError):
            price = 0.0
        if option:
            outcomes.append({
                "option": option,
                "price": price,
                "odds_pct": round(price * 100, 1),
                "market_id": mkt.get("id") or mkt.get("conditionId"),
                "clob_token_id": (mkt.get("clobTokenIds") or [None])[0],
            })
    if not outcomes:
        return [], None, None
    top = max(outcomes, key=lambda x: x["price"])
    return outcomes, top["option"], top["price"]


# =============================================================================
# POSTGRES
# =============================================================================

async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


async def upsert_event(
    conn: asyncpg.Connection,
    event: Dict[str, Any],
) -> tuple[bool, Optional[float]]:
    """Returns (is_crypto, previous_top_odds) — previous is None if new event."""
    title = event.get("title") or ""
    description = event.get("description") or ""
    is_crypto, matched_kw = is_crypto_related(title, description)
    extracted = extract_tickers_from_title(title)
    outcomes, top_option, top_odds = extract_outcomes(event)
    for out in outcomes:
        for t in extract_tickers_from_title(out["option"]):
            if t not in extracted:
                extracted.append(t)

    prev_row = await conn.fetchrow(
        "SELECT top_outcome_odds FROM polymarket_events WHERE polymarket_id = $1",
        event.get("id"),
    )
    prev_odds = prev_row["top_outcome_odds"] if prev_row else None

    await conn.execute(
        """
        INSERT INTO polymarket_events (
            polymarket_id, title, slug, category, volume_usd, liquidity_usd,
            open_interest_usd, is_crypto_related, matched_keywords, extracted_tickers,
            outcomes, top_outcome, top_outcome_odds, end_date, trending_rank, trending_velocity
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
        ON CONFLICT (polymarket_id) DO UPDATE SET
            volume_usd = EXCLUDED.volume_usd,
            liquidity_usd = EXCLUDED.liquidity_usd,
            open_interest_usd = EXCLUDED.open_interest_usd,
            is_crypto_related = EXCLUDED.is_crypto_related,
            extracted_tickers = EXCLUDED.extracted_tickers,
            outcomes = EXCLUDED.outcomes,
            top_outcome = EXCLUDED.top_outcome,
            top_outcome_odds = EXCLUDED.top_outcome_odds,
            trending_rank = EXCLUDED.trending_rank,
            trending_velocity = EXCLUDED.trending_velocity,
            last_updated_at = NOW()
        """,
        event.get("id"),
        title,
        event.get("slug"),
        event.get("category"),
        float(event.get("volume") or 0),
        float(event.get("liquidity") or 0),
        float(event.get("openInterest") or 0),
        1 if is_crypto else 0,
        matched_kw,
        extracted,
        json.dumps(outcomes),
        top_option,
        top_odds,
        event.get("endDate"),
        event.get("trendingRank"),
        float(event.get("volume24hr") or 0),
    )
    return is_crypto, prev_odds


async def emit_hunter_signal(
    conn: asyncpg.Connection,
    event: Dict[str, Any],
    reason: str,
    prev_odds: Optional[float] = None,
    top_option: Optional[str] = None,
    top_odds: Optional[float] = None,
    tickers: Optional[List[str]] = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO hunter_signals (source, source_weight, token_address, chain, event_type, raw_data)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        "polymarket",
        2.0,
        None,
        "prediction_market",
        reason,
        json.dumps({
            "polymarket_id": event.get("id"),
            "title": event.get("title"),
            "tickers": tickers or [],
            "top_outcome": top_option,
            "top_outcome_odds": top_odds,
            "previous_odds": prev_odds,
            "odds_delta": (top_odds - prev_odds) if prev_odds is not None and top_odds is not None else None,
            "volume_usd": event.get("volume"),
            "url": f"https://polymarket.com/event/{event.get('slug')}" if event.get("slug") else None,
        }),
    )


# =============================================================================
# DISCOVERY (REST every 5min)
# =============================================================================

async def discover_crypto_markets(
    session: aiohttp.ClientSession, pool: asyncpg.Pool
) -> List[str]:
    """Fetch top trending markets, filter crypto-related, return polymarket_ids."""
    url = f"{GAMMA_API_BASE}/events"
    params = {
        "limit": 200,
        "active": "true",
        "closed": "false",
        "order": "volume24hr",
        "ascending": "false",
    }
    try:
        async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                logger.warning(f"Discovery API returned {resp.status}")
                return []
            data = await resp.json()
            events = data if isinstance(data, list) else data.get("events", [])
    except Exception as e:
        logger.exception(f"Discovery fetch failed: {e}")
        return []

    crypto_ids: List[str] = []
    async with pool.acquire() as conn:
        for idx, event in enumerate(events):
            event["trendingRank"] = idx + 1
            is_crypto, prev_odds = await upsert_event(conn, event)
            if is_crypto:
                crypto_ids.append(event.get("id"))
                tickers = extract_tickers_from_title(event.get("title", ""))
                outcomes, top_option, top_odds = extract_outcomes(event)
                # Emit signal on DISCOVERY (new crypto event) or odds change
                if prev_odds is None:
                    await emit_hunter_signal(
                        conn, event, "new_crypto_market",
                        None, top_option, top_odds, tickers,
                    )
                elif (
                    top_odds is not None
                    and prev_odds is not None
                    and abs(top_odds - prev_odds) >= ODDS_CHANGE_THRESHOLD
                ):
                    await emit_hunter_signal(
                        conn, event, "odds_change",
                        prev_odds, top_option, top_odds, tickers,
                    )

    logger.info(f"Discovery: {len(events)} events, {len(crypto_ids)} crypto-related")
    return crypto_ids


# =============================================================================
# WSS SUBSCRIPTION (sub-second updates on subscribed markets)
# =============================================================================

async def wss_listen(
    pool: asyncpg.Pool,
    subscribed_market_ids: Set[str],
) -> None:
    """Subscribe to CLOB WSS and emit signals on price_change messages."""
    while True:
        try:
            async with websockets.connect(CLOB_WSS_URL) as ws:
                subscribe_msg = {
                    "type": "MARKET",
                    "markets": list(subscribed_market_ids),
                }
                await ws.send(json.dumps(subscribe_msg))
                logger.info(f"WSS subscribed to {len(subscribed_market_ids)} markets")

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        await handle_wss_message(pool, msg)
                    except json.JSONDecodeError:
                        continue
                    except Exception as e:
                        logger.exception(f"WSS message handler failed: {e}")
        except (websockets.ConnectionClosed, OSError) as e:
            logger.warning(f"WSS disconnected: {e} — reconnecting in 5s")
            await asyncio.sleep(5)
        except Exception as e:
            logger.exception(f"WSS loop error: {e}")
            await asyncio.sleep(10)


async def handle_wss_message(pool: asyncpg.Pool, msg: Dict[str, Any]) -> None:
    """Process a single WSS message — typically price_change or book_update."""
    event_type = msg.get("event_type") or msg.get("type")
    if event_type not in ("price_change", "last_trade_price"):
        return

    asset_id = msg.get("asset_id") or msg.get("market")
    new_price = msg.get("price") or msg.get("last_trade_price")
    if not asset_id or new_price is None:
        return

    try:
        new_price = float(new_price)
    except (ValueError, TypeError):
        return

    # Fetch current event state from DB
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT polymarket_id, title, slug, outcomes, top_outcome, top_outcome_odds,
                   is_crypto_related, extracted_tickers
            FROM polymarket_events
            WHERE outcomes::text LIKE '%' || $1 || '%'
            LIMIT 1
            """,
            asset_id,
        )
        if not row or not row["is_crypto_related"]:
            return

        prev_top_odds = row["top_outcome_odds"]

        # Update outcomes in place
        try:
            outcomes = json.loads(row["outcomes"]) if isinstance(row["outcomes"], str) else row["outcomes"]
        except Exception:
            outcomes = []
        for out in outcomes:
            if out.get("clob_token_id") == asset_id or out.get("market_id") == asset_id:
                out["price"] = new_price
                out["odds_pct"] = round(new_price * 100, 1)

        if outcomes:
            top = max(outcomes, key=lambda x: x.get("price", 0))
            new_top_odds = top["price"]
            new_top_option = top["option"]
        else:
            return

        await conn.execute(
            """
            UPDATE polymarket_events
            SET outcomes = $1::jsonb, top_outcome = $2, top_outcome_odds = $3, last_updated_at = NOW()
            WHERE polymarket_id = $4
            """,
            json.dumps(outcomes),
            new_top_option,
            new_top_odds,
            row["polymarket_id"],
        )

        if (
            prev_top_odds is not None
            and abs(new_top_odds - prev_top_odds) >= ODDS_CHANGE_THRESHOLD
        ):
            await emit_hunter_signal(
                conn,
                {
                    "id": row["polymarket_id"],
                    "title": row["title"],
                    "slug": row["slug"],
                },
                "wss_odds_change",
                prev_top_odds,
                new_top_option,
                new_top_odds,
                list(row["extracted_tickers"] or []),
            )
            logger.info(
                f"ODDS CHANGE: {row['title'][:60]} "
                f"{prev_top_odds:.3f} → {new_top_odds:.3f} ({new_top_option})"
            )


# =============================================================================
# ORCHESTRATOR (discovery + WSS in parallel)
# =============================================================================

async def run_discovery_loop(pool: asyncpg.Pool, subscribed_ids: Set[str]) -> None:
    """Discover new crypto markets every 5min, add to WSS subscription."""
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                new_ids = await discover_crypto_markets(session, pool)
                for id_ in new_ids:
                    subscribed_ids.add(id_)
            except Exception as e:
                logger.exception(f"Discovery iteration failed: {e}")
            await asyncio.sleep(DISCOVERY_POLL_SECONDS)


async def main() -> None:
    if not POSTGRES_DSN:
        logger.error("POSTGRES_DSN must be set")
        sys.exit(1)

    pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=1, max_size=3)
    async with pool.acquire() as conn:
        await ensure_schema(conn)
    logger.info("Schema ready.")

    subscribed_ids: Set[str] = set()

    # Initial discovery before WSS
    async with aiohttp.ClientSession() as session:
        initial = await discover_crypto_markets(session, pool)
        subscribed_ids.update(initial)

    # Run discovery loop + WSS listener in parallel
    await asyncio.gather(
        run_discovery_loop(pool, subscribed_ids),
        wss_listen(pool, subscribed_ids),
    )


if __name__ == "__main__":
    asyncio.run(main())
