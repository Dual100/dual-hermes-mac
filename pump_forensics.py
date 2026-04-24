#!/usr/bin/env python3
"""
Pump Forensics — reverse engineer past pumps to learn patterns.

Instead of waiting 30 days for forward testing, TAKE TOKENS THAT ALREADY PUMPED
and analyze what signals were available BEFORE the pump. Extract patterns.
Calibrate Hermes decision weights.

Input: list of tokens that pumped >200% in last N days
Output: pattern library + calibrated weights + realistic precision estimate

Run:
    python3 pump_forensics.py --days 7
    python3 pump_forensics.py --tokens 0xabc,0xdef
    python3 pump_forensics.py --dexscreener-trending
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

BOT_PATH = Path("/home/ubuntu/creator-bid-bot")
sys.path.insert(0, str(BOT_PATH))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("pump_forensics")


# =============================================================================
# PATTERN FEATURES — what we extract per token
# =============================================================================

async def extract_pump_anatomy(
    token_address: str,
    chain: str,
    session: aiohttp.ClientSession,
) -> Dict[str, Any]:
    """
    For a given token that pumped, reconstruct the 'anatomy':
      - When did pump start (first significant price move)
      - What signals were available BEFORE the pump
      - Who was first to mention
      - What narrative was active
      - Platform of origin
      - Smart money presence before pump
    """
    anatomy = {
        "token_address": token_address,
        "chain": chain,
    }

    # 1. Current state (DexScreener)
    dex = await _fetch_dex(token_address, session)
    anatomy["current_mcap"] = dex.get("mcap_usd")
    anatomy["current_price"] = dex.get("price_usd")
    anatomy["price_change_24h"] = dex.get("price_change_24h")
    anatomy["volume_24h"] = dex.get("volume_24h")
    anatomy["pair_created_at"] = dex.get("pair_created_at")
    anatomy["symbol"] = dex.get("symbol")
    anatomy["name"] = dex.get("name")
    anatomy["twitter_handle"] = dex.get("twitter_handle")
    anatomy["telegram_group"] = dex.get("telegram")

    # 2. Safety (GoPlus)
    safety = await _fetch_goplus(token_address, chain, session)
    anatomy["is_honeypot"] = safety.get("is_honeypot")
    anatomy["lp_holders"] = safety.get("lp_holders")
    anatomy["holder_count"] = safety.get("holder_count")
    anatomy["creator_percent"] = safety.get("creator_percent")
    anatomy["buy_tax"] = safety.get("buy_tax")
    anatomy["sell_tax"] = safety.get("sell_tax")

    # 3. Social signals from X
    mentions_by_address = await _sorsa_search(token_address, session, limit=50)
    mentions_by_symbol = await _sorsa_search(
        f"${anatomy.get('symbol', '')}", session, limit=30,
    ) if anatomy.get("symbol") else []

    all_mentions = mentions_by_address + mentions_by_symbol
    # Dedup
    seen = set()
    unique_mentions = []
    for m in all_mentions:
        tid = m.get("id")
        if tid and tid not in seen:
            seen.add(tid)
            unique_mentions.append(m)

    anatomy["total_mentions"] = len(unique_mentions)
    anatomy["unique_authors"] = len({
        (m.get("user") or {}).get("username") for m in unique_mentions
    })

    # 4. First mention / catalyst
    if unique_mentions:
        sorted_mentions = sorted(
            unique_mentions,
            key=lambda m: m.get("created_at", ""),
        )
        first = sorted_mentions[0] if sorted_mentions else None
        if first:
            anatomy["first_mention_author"] = (first.get("user") or {}).get("username")
            anatomy["first_mention_at"] = first.get("created_at")
            anatomy["first_mention_text"] = (first.get("full_text") or "")[:200]

    # 5. Top shillers / promoters
    author_counts: Dict[str, int] = {}
    for m in unique_mentions:
        a = (m.get("user") or {}).get("username")
        if a:
            author_counts[a] = author_counts.get(a, 0) + 1
    anatomy["top_shillers"] = sorted(
        author_counts.items(), key=lambda x: -x[1]
    )[:10]

    # 6. Platform detection
    try:
        from platform_origin_detector import detect_token_origin
        origin = await detect_token_origin(token_address, chain=chain)
        anatomy["platform"] = origin.get("platform")
        anatomy["platform_trusted"] = origin.get("is_trusted")
    except Exception:
        anatomy["platform"] = None

    # 7. Classify pump type (from features)
    anatomy["pump_type"] = _classify_pump(anatomy)

    # 8. Hermes decision simulation
    anatomy["hermes_would_alert"] = _simulate_hermes_decision(anatomy)

    return anatomy


def _classify_pump(anatomy: Dict[str, Any]) -> str:
    """Heuristic classification of pump type."""
    mentions = anatomy.get("total_mentions", 0)
    unique = anatomy.get("unique_authors", 0)
    platform = anatomy.get("platform")

    if platform in ("Virtuals_Protocol", "Clanker_V4", "Flaunch"):
        return "platform_launch"
    if mentions > 100 and unique > 30:
        return "viral_narrative"
    if mentions < 10:
        return "stealth_or_smart_money"
    if anatomy.get("current_mcap", 0) > 1_000_000 and anatomy.get("price_change_24h", 0) > 500:
        return "momentum"
    return "organic"


def _simulate_hermes_decision(anatomy: Dict[str, Any]) -> Dict[str, Any]:
    """
    Simulate what Hermes WOULD have decided if it had seen this signal.
    Applies the scoring/filter logic from main pipeline.
    """
    # Critical fails
    if anatomy.get("is_honeypot"):
        return {"action": "SKIP", "reason": "honeypot", "score": 0}
    if (anatomy.get("creator_percent") or 0) > 0.30:
        return {"action": "SKIP", "reason": "creator holds > 30%", "score": 5}

    # Score composition
    score = 40  # base
    reasons = []

    # Platform trust
    if anatomy.get("platform_trusted"):
        score += 10
        reasons.append(f"+10 trusted platform ({anatomy.get('platform')})")

    # Social presence
    if anatomy.get("twitter_handle"):
        score += 8
        reasons.append("+8 has twitter")
    if anatomy.get("telegram_group"):
        score += 5
        reasons.append("+5 has telegram")

    # Mention density
    mentions = anatomy.get("total_mentions", 0)
    unique = anatomy.get("unique_authors", 0)
    if mentions >= 20 and unique >= 10:
        score += 15
        reasons.append(f"+15 active narrative ({unique} unique authors)")
    elif mentions >= 5:
        score += 8
        reasons.append(f"+8 some mentions ({mentions})")

    # Safety
    lp_holders = anatomy.get("lp_holders") or 0
    if lp_holders >= 3:
        score += 5
        reasons.append("+5 LP distributed")
    elif lp_holders == 1:
        score -= 5
        reasons.append("-5 single LP holder")

    # Holder count
    holders = anatomy.get("holder_count", 0) or 0
    if holders >= 500:
        score += 10
        reasons.append(f"+10 {holders} holders")
    elif holders < 50:
        score -= 10
        reasons.append(f"-10 only {holders} holders")

    # MOMENTUM analysis (not just "did it pump")
    # Keep going = 1h growth same or higher velocity than 24h/6h
    # Slowing = 1h much less than 24h average
    pct_24h = anatomy.get("price_change_24h", 0) or 0
    pct_1h = anatomy.get("price_change_1h", 0) or 0
    vol_24h = anatomy.get("volume_24h", 0) or 0
    mcap = anatomy.get("current_mcap", 0) or 0

    # Momentum indicator: 1h rate / avg 24h rate
    # If avg 24h rate (pct_24h/24) is X%/h and 1h is 2X%/h → accelerating
    # If 1h is 0.5X%/h → decelerating
    if pct_24h > 0:
        expected_1h_rate = pct_24h / 24
        actual_1h_rate = pct_1h
        if expected_1h_rate > 0:
            momentum_ratio = actual_1h_rate / expected_1h_rate
        else:
            momentum_ratio = 1.0

        if momentum_ratio > 1.5:
            # Accelerating — still early in pump
            score += 10
            reasons.append(f"+10 MOMENTUM ACCELERATING (1h={pct_1h}%, 24h={pct_24h}%)")
        elif momentum_ratio > 0.5:
            # Steady — ok
            reasons.append(f"± steady momentum (ratio {momentum_ratio:.2f})")
        else:
            # Decelerating — probably peaked
            score -= 10
            reasons.append(f"-10 DECELERATING (1h={pct_1h}% vs 24h avg {expected_1h_rate:.1f}%/h)")

    # Reversal check: if 1h is NEGATIVE while 24h positive = dumping
    if pct_24h > 50 and pct_1h < -5:
        score -= 20
        reasons.append(f"-20 REVERSING (24h +{pct_24h}%, 1h {pct_1h}%)")

    # Volume/mcap ratio (liquidity turnover — still hot?)
    if mcap > 0 and vol_24h > 0:
        turnover = vol_24h / mcap
        if turnover > 1.0:
            score += 8
            reasons.append(f"+8 high volume/mcap turnover ({turnover:.2f}x)")
        elif turnover < 0.1:
            score -= 5
            reasons.append(f"-5 low turnover ({turnover:.2f}x) — liquidity drying")

    # Entry window based on mcap tier
    if mcap < 100_000:
        score += 5
        reasons.append("+5 micro cap (<$100K) — if legit, big upside")
    elif mcap < 500_000:
        score += 3
        reasons.append("+3 small cap ($100K-$500K) — sweet spot")
    elif mcap > 10_000_000:
        # Only alert big caps with CURRENT momentum
        if momentum_ratio < 1.0 if 'momentum_ratio' in dir() else True:
            score -= 8
            reasons.append(f"-8 large cap (${mcap:,.0f}) without accelerating momentum")

    score = max(0, min(100, score))

    if score >= 60:
        action = "ALERT"
    elif score >= 30:
        action = "WATCH"
    else:
        action = "SKIP"

    return {"action": action, "score": score, "reasons": reasons}


# =============================================================================
# FETCHERS
# =============================================================================

async def _fetch_dex(token: str, session: aiohttp.ClientSession) -> Dict[str, Any]:
    try:
        async with session.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{token}",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            if resp.status != 200:
                return {}
            data = await resp.json()
            pairs = data.get("pairs") or []
            if not pairs:
                return {}
            p = pairs[0]
            base = p.get("baseToken", {})
            info = p.get("info") or {}
            socials = info.get("socials", [])
            twitter = next((s.get("url") for s in socials if s.get("type") == "twitter"), None)
            telegram = next((s.get("url") for s in socials if s.get("type") == "telegram"), None)
            return {
                "name": base.get("name"),
                "symbol": base.get("symbol"),
                "chain": p.get("chainId"),
                "price_usd": float(p.get("priceUsd") or 0),
                "mcap_usd": float(p.get("marketCap") or p.get("fdv") or 0),
                "liquidity_usd": float((p.get("liquidity") or {}).get("usd", 0) or 0),
                "volume_24h": float((p.get("volume") or {}).get("h24", 0) or 0),
                "price_change_24h": float((p.get("priceChange") or {}).get("h24", 0) or 0),
                "price_change_1h": float((p.get("priceChange") or {}).get("h1", 0) or 0),
                "pair_created_at": p.get("pairCreatedAt"),
                "twitter_handle": (twitter or "").replace("https://x.com/", "").replace("https://twitter.com/", "").strip("/"),
                "telegram": telegram,
            }
    except Exception as e:
        logger.debug(f"dex fetch failed {token}: {e}")
        return {}


async def _fetch_goplus(
    token: str, chain: str, session: aiohttp.ClientSession
) -> Dict[str, Any]:
    chain_id = {"ethereum": "1", "base": "8453", "bsc": "56"}.get(chain, "1")
    try:
        async with session.get(
            f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}?contract_addresses={token}",
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            data = await resp.json()
            result = data.get("result") or {}
            if not result:
                return {}
            info = list(result.values())[0]
            return {
                "is_honeypot": info.get("is_honeypot") == "1",
                "buy_tax": float(info.get("buy_tax") or 0),
                "sell_tax": float(info.get("sell_tax") or 0),
                "lp_holders": int(info.get("lp_holder_count") or 0),
                "holder_count": int(info.get("holder_count") or 0),
                "creator_percent": float(info.get("creator_percent") or 0),
            }
    except Exception as e:
        logger.debug(f"goplus failed {token}: {e}")
        return {}


async def _sorsa_search(
    query: str, session: aiohttp.ClientSession, limit: int = 30
) -> List[Dict[str, Any]]:
    key = os.environ.get("TWEETSCOUT_API_KEY")
    if not key:
        return []
    try:
        async with session.post(
            "https://api.sorsa.io/v3/search-tweets",
            json={"query": query, "limit": limit},
            headers={"ApiKey": key, "Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            return data.get("tweets", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    except Exception as e:
        logger.debug(f"sorsa failed {query}: {e}")
        return []


# =============================================================================
# DISCOVERY OF PUMP CANDIDATES (DexScreener trending / gainers)
# =============================================================================

async def fetch_trending_pumps(
    session: aiohttp.ClientSession,
    chain: str = "ethereum",
    min_pump_pct: float = 200,
) -> List[Dict[str, Any]]:
    """Get tokens pumping now — use as seed list for forensics."""
    # DexScreener doesn't have a pure "gainers" endpoint but /token-boosts
    # plus search with filters works.
    try:
        url = f"https://api.dexscreener.com/token-boosts/top/v1"
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            if not isinstance(data, list):
                return []
            results = []
            for item in data[:50]:
                if item.get("chainId") != chain:
                    continue
                results.append({
                    "address": item.get("tokenAddress"),
                    "chain": item.get("chainId"),
                    "description": item.get("description", ""),
                })
            return results
    except Exception as e:
        logger.warning(f"trending fetch failed: {e}")
        return []


# =============================================================================
# MAIN
# =============================================================================

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tokens", type=str, help="Comma-separated addresses")
    parser.add_argument("--dexscreener-trending", action="store_true")
    parser.add_argument("--chain", type=str, default="ethereum")
    parser.add_argument("--output", type=str, default="/tmp/pump_forensics_results.json")
    args = parser.parse_args()

    targets: List[Dict[str, str]] = []
    async with aiohttp.ClientSession() as session:
        if args.tokens:
            for addr in args.tokens.split(","):
                targets.append({"address": addr.strip(), "chain": args.chain})
        elif args.dexscreener_trending:
            trending = await fetch_trending_pumps(session, chain=args.chain)
            targets = trending
            logger.info(f"Found {len(trending)} trending tokens on {args.chain}")
        else:
            logger.error("Provide --tokens or --dexscreener-trending")
            return

        # Parallel execution with semaphore limiting concurrent investigations
        # to 15 (matches Hermes sweet spot + respects Sorsa rate limit 10/s)
        sem = asyncio.Semaphore(15)

        async def _analyze_one(t):
            async with sem:
                logger.info(f"Analyzing {t['address'][:20]}...")
                return await extract_pump_anatomy(t["address"], t["chain"], session)

        logger.info(f"Dispatching {len(targets)} investigations in parallel (max 15 concurrent)")
        import time
        t0 = time.time()
        results = await asyncio.gather(*[_analyze_one(t) for t in targets])
        elapsed = time.time() - t0
        logger.info(f"All {len(targets)} investigations done in {elapsed:.1f}s")

    # Save
    with open(args.output, "w") as f:
        json.dump(results, f, indent=2, default=str)

    # Summary
    print("\n" + "=" * 70)
    print(f"FORENSICS COMPLETE — {len(results)} tokens analyzed")
    print("=" * 70)
    alert_count = sum(1 for r in results if r.get("hermes_would_alert", {}).get("action") == "ALERT")
    watch_count = sum(1 for r in results if r.get("hermes_would_alert", {}).get("action") == "WATCH")
    skip_count = sum(1 for r in results if r.get("hermes_would_alert", {}).get("action") == "SKIP")
    print(f"  ALERT: {alert_count}")
    print(f"  WATCH: {watch_count}")
    print(f"  SKIP: {skip_count}")
    print(f"\nDetails saved to: {args.output}")

    # Per-token summary
    for r in results:
        d = r.get("hermes_would_alert", {})
        print(f"\n{r.get('symbol', '?')} ({r.get('token_address', '')[:10]}...) "
              f"mcap=${r.get('current_mcap', 0):,.0f} 24h={r.get('price_change_24h', 0):.1f}%")
        print(f"  Decision: {d.get('action')} score={d.get('score')}")
        print(f"  Pump type: {r.get('pump_type')}")
        print(f"  Mentions: {r.get('total_mentions', 0)} from {r.get('unique_authors', 0)} authors")
        if r.get("first_mention_author"):
            print(f"  First mention: @{r['first_mention_author']} at {r.get('first_mention_at')}")


if __name__ == "__main__":
    asyncio.run(main())
