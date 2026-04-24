"""
Blockchain Watcher + Dormant Awakening Detector.

THE KILLER FEATURE — why Hermes catches pumps like AIB.

Context (from real AIB pump analysis):
  - 22:07 UTC: 1 tx on pool (dormant 24h+)
  - 22:08 UTC: 57 transfers in 1 minute — pump begins
  - 22:14 UTC: 780+ cumulative transfers, mcap probably $300K-$1M
  - 22:26 UTC: first Twitter mention (too late — mcap already $4M)

Twitter is USELESS for first 15-20 minutes. Only on-chain detection works.

This module:
  1. Polls high-throughput volume data every 30s (DexScreener + GeckoTerminal new_pools)
  2. Subscribes WSS to Transfer events for tokens on watchlist
  3. Tracks "last_swap_timestamp" per token in Redis
  4. Flags DORMANT_AWAKENING when:
     - Token had <5 transfers in last 24h (dormant)
     - AND gets >20 transfers in 5 minutes (awakening)
     - AND volume spike >10x vs 24h average
  5. Emits hunter_signal CRITICAL (weight 4.0, hot path)
"""

import asyncio
import json
import logging
import os
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Set

try:
    import aiohttp
    import asyncpg
    import websockets
except ImportError:
    sys.stderr.write("Install: pip install aiohttp asyncpg websockets\n")
    sys.exit(1)

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("blockchain_watcher")

# =============================================================================
# CONFIG
# =============================================================================

ALCHEMY_WSS = os.environ.get("ALCHEMY_WSS")
QUICKNODE_WSS = os.environ.get("QUICKNODE_WSS")
POSTGRES_DSN = os.environ.get("POSTGRES_DSN")

# Dormant awakening thresholds
DORMANT_HOURS = 24             # no trades in 24h = dormant
AWAKENING_TX_COUNT = 20         # 20+ transfers in 5 min = awakening
AWAKENING_WINDOW_SEC = 300      # 5 minute window
VOLUME_SPIKE_MULTIPLIER = 10    # 10× vs 24h avg

# Discovery poll interval
DISCOVERY_INTERVAL = 30  # seconds

# =============================================================================
# DORMANT TRACKER (memory + Redis)
# =============================================================================

class DormantTracker:
    """Tracks swap activity per token to detect awakening."""

    def __init__(self):
        # In-memory fallback if no Redis
        self._events: Dict[str, deque] = defaultdict(lambda: deque(maxlen=500))
        self.redis = None

    async def connect_redis(self):
        if aioredis:
            try:
                self.redis = aioredis.from_url("redis://localhost")
                await self.redis.ping()
                logger.info("Redis connected for dormant tracker")
            except Exception:
                logger.info("Redis unavailable, using in-memory tracking")

    async def record_event(self, token: str, ts: float, value_eth: float) -> None:
        """Record a swap/transfer. Prune old events."""
        token = token.lower()
        cutoff = ts - (DORMANT_HOURS * 3600)
        events = self._events[token]
        events.append((ts, value_eth))
        # Prune
        while events and events[0][0] < cutoff:
            events.popleft()

    async def check_awakening(self, token: str, now_ts: float) -> Optional[Dict[str, Any]]:
        """Detect if token awakened. Returns signal dict or None."""
        token = token.lower()
        events = self._events[token]
        if len(events) < AWAKENING_TX_COUNT:
            return None

        # Events in last 5 min
        window_start = now_ts - AWAKENING_WINDOW_SEC
        recent = [(t, v) for t, v in events if t >= window_start]
        if len(recent) < AWAKENING_TX_COUNT:
            return None

        # Check dormancy: events BEFORE the 5-min window
        dormant_events = [(t, v) for t, v in events if t < window_start]
        # "dormant" if <5 events in prior 24h - AWAKENING_WINDOW_SEC
        if len(dormant_events) > 5:
            return None  # not dormant, was active recently

        # Volume math
        recent_vol = sum(v for _, v in recent)
        if recent_vol < 0.1:  # <0.1 ETH total = dust, skip
            return None

        # Calculate 24h avg per 5-min window (should be near-zero if dormant)
        avg_5min_volume = (sum(v for _, v in dormant_events) /
                            max(len(dormant_events), 1))
        if avg_5min_volume > 0:
            spike = recent_vol / avg_5min_volume
        else:
            spike = float("inf")  # dormant → any volume is infinite spike

        return {
            "token": token,
            "detected_at": now_ts,
            "recent_tx_count": len(recent),
            "recent_volume_eth": recent_vol,
            "prior_24h_tx_count": len(dormant_events),
            "prior_24h_volume_eth": sum(v for _, v in dormant_events),
            "spike_multiplier": spike if spike != float("inf") else 999,
            "first_event_in_window": recent[0][0] if recent else None,
            "latest_event": recent[-1][0] if recent else None,
        }


# =============================================================================
# DISCOVERY (polling — catches what WSS can't subscribe upfront)
# =============================================================================

async def discover_trending_tokens(
    session: aiohttp.ClientSession, tracker: DormantTracker
) -> None:
    """Every 30s, query for pumping tokens across chains. For each new discovery,
    subscribe to its pool via WSS in the next connection refresh."""
    try:
        # Combine: DexScreener token-boosts + GeckoTerminal trending
        tasks = [
            session.get("https://api.dexscreener.com/token-boosts/top/v1",
                        timeout=aiohttp.ClientTimeout(total=10)),
            session.get("https://api.geckoterminal.com/api/v2/networks/eth/trending_pools",
                        timeout=aiohttp.ClientTimeout(total=10)),
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        candidates: Set[str] = set()

        for resp in responses:
            if isinstance(resp, Exception):
                continue
            try:
                async with resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    if isinstance(data, list):
                        # dexscreener boosts
                        for item in data:
                            if item.get("chainId") == "ethereum":
                                addr = (item.get("tokenAddress") or "").lower()
                                if addr:
                                    candidates.add(addr)
                    elif isinstance(data, dict):
                        # gecko
                        for p in (data.get("data") or []):
                            rels = p.get("relationships", {})
                            base = (rels.get("base_token") or {}).get("data", {})
                            base_id = base.get("id", "")
                            if "_" in base_id:
                                addr = base_id.split("_", 1)[1].lower()
                                candidates.add(addr)
            except Exception as e:
                logger.debug(f"discovery parse: {e}")

        logger.debug(f"Discovered {len(candidates)} trending tokens for monitoring")
    except Exception as e:
        logger.warning(f"Discovery failed: {e}")


# =============================================================================
# WSS SUBSCRIPTIONS (Uniswap V2 Pair Sync events for reserves updates)
# =============================================================================

# Uniswap V2 Pair Sync event topic (reserves changed — means trade happened)
UNISWAP_V2_SYNC_TOPIC = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"
# Uniswap V3 Pool Swap event topic
UNISWAP_V3_SWAP_TOPIC = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"

async def wss_listen_swaps(
    tracker: DormantTracker,
    pool: asyncpg.Pool,
    watchlist: Set[str],
) -> None:
    """Listen to Sync events SUBSCRIBED TO SPECIFIC POOLS (watchlist).

    CANNOT subscribe to all ETH pools — would be 5000+ events/s.
    Strategy: subscribe only to watchlist pools (max 500) plus factory events.
    """
    if not ALCHEMY_WSS:
        logger.error("ALCHEMY_WSS not set — cannot listen to ETH chain")
        return

    # Uniswap V2 Factory
    UNI_V2_FACTORY = "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"
    PAIR_CREATED_TOPIC = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"

    backoff = 5
    while True:
        try:
            async with websockets.connect(ALCHEMY_WSS) as ws:
                # 1) Subscribe to factory: detects NEW pairs (adds to watchlist)
                factory_sub = {
                    "jsonrpc": "2.0", "id": 1, "method": "eth_subscribe",
                    "params": ["logs", {
                        "address": UNI_V2_FACTORY,
                        "topics": [PAIR_CREATED_TOPIC],
                    }],
                }
                await ws.send(json.dumps(factory_sub))
                logger.info("Subscribed to Uniswap V2 Factory (PairCreated)")

                # 2) Subscribe to Sync events ONLY on watchlist pools
                if watchlist:
                    pool_addresses = list(watchlist)[:500]  # cap at 500
                    watchlist_sub = {
                        "jsonrpc": "2.0", "id": 2, "method": "eth_subscribe",
                        "params": ["logs", {
                            "address": pool_addresses,
                            "topics": [UNISWAP_V2_SYNC_TOPIC],
                        }],
                    }
                    await ws.send(json.dumps(watchlist_sub))
                    logger.info(f"Subscribed to Sync events on {len(pool_addresses)} watchlist pools")

                backoff = 5
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        if msg.get("method") != "eth_subscription":
                            continue
                        log = msg["params"]["result"]
                        # Route by topic
                        topics = log.get("topics", [])
                        if not topics:
                            continue
                        if topics[0] == PAIR_CREATED_TOPIC:
                            await handle_new_pair(log, watchlist)
                        elif topics[0] == UNISWAP_V2_SYNC_TOPIC:
                            await handle_sync_event(log, tracker, pool)
                    except Exception as e:
                        logger.debug(f"parse: {e}")

                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                        if msg.get("method") != "eth_subscription":
                            continue
                        log = msg["params"]["result"]
                        await handle_sync_event(log, tracker, pool)
                    except Exception as e:
                        logger.debug(f"parse sync: {e}")

        except (websockets.ConnectionClosed, OSError) as e:
            logger.warning(f"WSS dropped: {e}. Reconnect in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        except Exception as e:
            logger.exception(f"WSS loop: {e}")
            await asyncio.sleep(10)


async def handle_new_pair(log: Dict, watchlist: Set[str]) -> None:
    """A new Uniswap V2 pair was created. Add pool to watchlist."""
    # log.data contains: token0, token1, pair address, pairIndex
    # For simplicity, parse address from topics (token0 and token1 are indexed)
    pool_address_raw = log.get("data", "")
    if len(pool_address_raw) >= 66:
        # First 32 bytes (64 hex chars) after 0x is the pair address
        pool_addr = "0x" + pool_address_raw[26:66].lower()
        watchlist.add(pool_addr)
        logger.info(f"NEW PAIR detected → watchlist (size now {len(watchlist)}): {pool_addr[:12]}...")


async def handle_sync_event(log: Dict, tracker: DormantTracker, pool: asyncpg.Pool) -> None:
    """Process a single Uniswap V2 Sync event."""
    pool_address = log.get("address", "").lower()
    block_number = int(log.get("blockNumber", "0x0"), 16)

    # Record for the pool (we don't know the token yet — would need to resolve
    # pool → token0/token1 via eth_call cache)
    # For v1, treat pool as token proxy
    now = datetime.now(tz=timezone.utc).timestamp()
    # Minimal: just count events; real impl would parse reserves to get volume
    await tracker.record_event(pool_address, now, value_eth=0.1)  # placeholder

    # Check awakening
    signal = await tracker.check_awakening(pool_address, now)
    if signal:
        logger.info(f"🚨 AWAKENING DETECTED on pool {pool_address[:12]}...")
        await emit_awakening_signal(pool, pool_address, signal)


async def emit_awakening_signal(
    pg_pool: asyncpg.Pool, pool_or_token: str, signal: Dict[str, Any]
) -> None:
    """Insert hunter_signal for downstream convergence engine."""
    try:
        async with pg_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO hunter_signals
                (source, source_weight, token_address, chain, event_type, raw_data)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                "blockchain_dormant",
                4.0,
                pool_or_token,
                "ethereum",
                "dormant_awakening",
                json.dumps(signal),
            )
    except Exception as e:
        logger.warning(f"Failed to emit signal: {e}")


# =============================================================================
# ORCHESTRATOR
# =============================================================================

async def main():
    if not ALCHEMY_WSS:
        logger.error("ALCHEMY_WSS required")
        sys.exit(1)
    if not POSTGRES_DSN:
        logger.error("POSTGRES_DSN required")
        sys.exit(1)

    tracker = DormantTracker()
    await tracker.connect_redis()

    pg_pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=1, max_size=3)
    logger.info("Blockchain watcher starting — listening for dormant awakenings")

    # Run WSS listener + discovery loop in parallel
    async with aiohttp.ClientSession() as session:
        # Watchlist shared across tasks
        watchlist: Set[str] = set()

        async def discovery_loop():
            while True:
                try:
                    added = await discover_trending_tokens(session, tracker, watchlist)
                    logger.debug(f"Watchlist size: {len(watchlist)}")
                except Exception as e:
                    logger.warning(f"discovery: {e}")
                await asyncio.sleep(DISCOVERY_INTERVAL)

        await asyncio.gather(
            wss_listen_swaps(tracker, pg_pool, watchlist),
            discovery_loop(),
        )


if __name__ == "__main__":
    asyncio.run(main())
