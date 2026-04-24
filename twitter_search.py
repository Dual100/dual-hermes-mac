"""
Twitter/X Search — Multi-strategy mention discovery.

CRITICAL USE CASE (user insight):
"As vezes n temos isso de X linkado ao token... como vai pesquisar no X sobre
 quem marcou o endereço do token ou o nome para pesquisar sobre o token?"

Most alpha memecoins DO NOT have an official Twitter. But people ARE talking
about them. This module finds those mentions so Hermes can:
1. Identify WHO is shilling/posting (KOL or random?)
2. Detect catalyst timing (which tweet triggered the pump?)
3. Assess shill coordination (same accounts? bot-like?)
4. Extract narrative context (what story are people telling?)

Strategies (tried in order, first that works wins):

1. **Brave Search API** (we have key) — `site:x.com "{address}"`
   - Free tier: 2000/mo (enough for ~100 tokens/day)
   - Fast, reliable
   - Best first choice

2. **Sorsa v3 search-tweets** (pago, need to verify endpoint exists)
   - Purpose-built for Twitter
   - Returns structured data (author, engagement, etc)

3. **Nitter public instances** (free, fragile)
   - Multiple nitter instances with failover
   - Scrapes HTML — breaks when instance goes down

4. **fxtwitter.com search** — doesn't have search (only direct lookup)

Output format (unified across strategies):
{
    "query": str,                # what we searched
    "source": str,                # "brave" / "sorsa" / "nitter"
    "mentions": [
        {
            "tweet_id": str,
            "author": str,        # @handle
            "content": str,       # tweet text excerpt
            "posted_at": str,     # timestamp
            "engagement": int,     # likes+RTs if available
            "url": str,           # link to tweet
        }
    ],
    "total_found": int,
}
"""

import asyncio
import logging
import os
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

BOT_PATH = Path("/home/ubuntu/creator-bid-bot")
sys.path.insert(0, str(BOT_PATH))

logger = logging.getLogger("twitter_search")

# =============================================================================
# CONFIG
# =============================================================================

BRAVE_SEARCH_API_KEY = os.environ.get("BRAVE_SEARCH_API_KEY", "")
SORSA_API_KEY = os.environ.get("TWEETSCOUT_API_KEY", "")
SORSA_BASE = "https://api.sorsa.io/v3"

NITTER_INSTANCES = [
    "nitter.poast.org",
    "nitter.privacydev.net",
    "nitter.net",
    "nitter.1d4.us",
    "nitter.fdn.fr",
]

# =============================================================================
# STRATEGY 1: BRAVE SEARCH (recommended primary)
# =============================================================================

async def search_brave(
    query: str,
    session: aiohttp.ClientSession,
    count: int = 20,
) -> Dict[str, Any]:
    """Search X via Brave with site:x.com filter."""
    if not BRAVE_SEARCH_API_KEY:
        return {"error": "BRAVE_SEARCH_API_KEY not set"}

    q = f'site:x.com "{query}"'
    url = "https://api.search.brave.com/res/v1/web/search"
    params = {
        "q": q,
        "count": min(count, 20),
        "freshness": "pd",  # past day
    }
    headers = {
        "X-Subscription-Token": BRAVE_SEARCH_API_KEY,
        "Accept": "application/json",
    }

    try:
        async with session.get(url, params=params, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.warning(f"Brave search {resp.status}: {body[:200]}")
                return {"error": f"brave {resp.status}"}
            data = await resp.json()
    except Exception as e:
        logger.warning(f"Brave search failed: {e}")
        return {"error": str(e)}

    web_results = (data.get("web", {}) or {}).get("results", [])
    mentions = []
    for r in web_results:
        url_r = r.get("url", "")
        # Extract tweet_id and author from URL like https://x.com/user/status/1234567890
        m = re.search(r"x\.com/([^/]+)/status/(\d+)", url_r)
        if not m:
            continue
        mentions.append({
            "tweet_id": m.group(2),
            "author": m.group(1),
            "content": (r.get("description") or r.get("title") or "")[:500],
            "posted_at": r.get("age") or r.get("page_age"),
            "engagement": None,
            "url": url_r,
        })

    return {
        "query": query,
        "source": "brave",
        "mentions": mentions,
        "total_found": len(mentions),
    }


# =============================================================================
# STRATEGY 2: SORSA v3 search-tweets (needs verification)
# =============================================================================

async def search_sorsa(
    query: str,
    session: aiohttp.ClientSession,
    count: int = 20,
) -> Dict[str, Any]:
    """
    Sorsa v3 /search-tweets — TESTED WORKING (2026-04-24).

    POST endpoint, JSON body, ApiKey header. Returns up to 20 tweets per call
    with next_cursor for pagination. 20x rate limit vs X official.

    Response format confirmed:
      {"tweets": [{"id": "...", "full_text": "...", "created_at": "...",
                   "user": {"username": "...", "display_name": "...", "verified": bool},
                   "likes_count": int, "retweet_count": int, ...}]}
    """
    if not SORSA_API_KEY:
        return {"error": "TWEETSCOUT_API_KEY not set"}

    url = f"{SORSA_BASE}/search-tweets"
    headers = {"ApiKey": SORSA_API_KEY, "Content-Type": "application/json"}
    payload = {"query": query, "limit": min(count, 50)}

    try:
        async with session.post(url, json=payload, headers=headers,
                                 timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 404:
                return await search_sorsa_v2(query, session, count)
            if resp.status == 429:
                return {"error": "sorsa_rate_limit"}
            if resp.status != 200:
                body = await resp.text()
                return {"error": f"sorsa {resp.status}", "detail": body[:200]}
            data = await resp.json()
    except Exception as e:
        logger.warning(f"Sorsa search failed: {e}")
        return {"error": str(e)}

    # Handle both response shapes
    tweets = data.get("tweets", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

    mentions = []
    for t in tweets:
        user = t.get("user") or {}
        mentions.append({
            "tweet_id": t.get("id") or t.get("tweet_id"),
            "author": user.get("username") or t.get("author"),
            "display_name": user.get("display_name"),
            "verified": user.get("verified"),
            "content": t.get("full_text") or t.get("text", ""),
            "posted_at": t.get("created_at") or t.get("date"),
            "engagement": (t.get("likes_count") or 0) + (t.get("retweet_count") or 0),
            "likes": t.get("likes_count"),
            "retweets": t.get("retweet_count"),
            "url": f"https://x.com/{user.get('username', 'i')}/status/{t.get('id')}",
        })
    return {
        "query": query, "source": "sorsa",
        "mentions": mentions,
        "total_found": len(mentions),
        "next_cursor": data.get("next_cursor") if isinstance(data, dict) else None,
    }


async def search_sorsa_v2(
    query: str, session: aiohttp.ClientSession, count: int
) -> Dict[str, Any]:
    """Fallback to v2 search-tweets endpoint (known to work from dead_code)."""
    url = "https://api.sorsa.io/v2/search-tweets"
    params = {"query": query, "limit": count}
    headers = {"ApiKey": SORSA_API_KEY}
    try:
        async with session.get(url, params=params, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return {"error": f"sorsa_v2 {resp.status}"}
            data = await resp.json()
    except Exception as e:
        return {"error": str(e)}

    tweets = data if isinstance(data, list) else data.get("tweets", [])
    mentions = []
    for t in tweets:
        mentions.append({
            "tweet_id": t.get("id"),
            "author": (t.get("user") or {}).get("screen_name") or t.get("author"),
            "content": t.get("text", ""),
            "posted_at": t.get("created_at"),
            "engagement": (t.get("favorite_count") or 0) + (t.get("retweet_count") or 0),
            "url": f"https://x.com/i/status/{t.get('id')}",
        })
    return {
        "query": query, "source": "sorsa_v2",
        "mentions": mentions, "total_found": len(mentions),
    }


# =============================================================================
# STRATEGY 3: NITTER (free fallback, fragile)
# =============================================================================

async def search_nitter(
    query: str,
    session: aiohttp.ClientSession,
    count: int = 20,
) -> Dict[str, Any]:
    """Try multiple nitter instances. Scrapes HTML — fragile."""
    encoded = urllib.parse.quote(query)
    for instance in NITTER_INSTANCES:
        url = f"https://{instance}/search?q={encoded}&f=tweets"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    continue
                html = await resp.text()
                mentions = _parse_nitter_html(html, instance)
                if mentions:
                    return {
                        "query": query, "source": f"nitter_{instance}",
                        "mentions": mentions[:count],
                        "total_found": len(mentions),
                    }
        except Exception as e:
            logger.debug(f"Nitter {instance} failed: {e}")
            continue
    return {"error": "all nitter instances failed"}


def _parse_nitter_html(html: str, instance: str) -> List[Dict[str, Any]]:
    """Minimal HTML scraper for nitter tweet pages."""
    mentions = []
    # Pattern: /username/status/123456
    pattern = re.compile(
        r'class="tweet-link"\s+href="(/([^/"]+)/status/(\d+)#[^"]*)"',
        re.MULTILINE,
    )
    for m in pattern.finditer(html):
        username = m.group(2)
        tweet_id = m.group(3)
        mentions.append({
            "tweet_id": tweet_id,
            "author": username,
            "content": "",  # would need deeper scraping
            "posted_at": None,
            "engagement": None,
            "url": f"https://x.com/{username}/status/{tweet_id}",
        })
    # Dedup by tweet_id
    seen = set()
    out = []
    for mm in mentions:
        if mm["tweet_id"] not in seen:
            seen.add(mm["tweet_id"])
            out.append(mm)
    return out


# =============================================================================
# ORCHESTRATOR — PARALLEL execution, priority-aware Brave
# =============================================================================

async def search_x_mentions(
    query: str,
    session: Optional[aiohttp.ClientSession] = None,
    count: int = 20,
    priority: str = "normal",
) -> Dict[str, Any]:
    """
    Search X for mentions of a query (token address, ticker, project name).

    EXECUTION: strategies run in PARALLEL (not sequential) for max coverage +
    min latency. Results are merged and deduplicated by tweet_id.

    PRIORITY controls whether Brave (limited 2000/mo) is included:
      - 'normal': Sorsa + Nitter in parallel (free combo, always)
      - 'high':   Sorsa + Nitter + Brave in parallel (MEGA/platform/convergence>80)
      - 'critical': All strategies + extended count

    Rationale:
      - Free tier Brave quota: 2000/mo = ~65/day.
      - If 100 tokens/day × 3 searches, normal use must stay free.
      - High-priority alerts (~20/day × 3 searches = 60) still fit within quota.

    Args:
        query: string to search
        session: reuse aiohttp session or None
        count: max mentions per strategy
        priority: 'normal' | 'high' | 'critical'

    Returns:
        dict with 'query', 'sources_used', 'mentions' (deduped), 'total_found'.
    """
    owned_session = False
    if session is None:
        session = aiohttp.ClientSession()
        owned_session = True

    try:
        # Decide strategy set based on priority
        strategies: List[str] = ["sorsa", "nitter"]
        if priority in ("high", "critical"):
            strategies.append("brave")

        # Fire all in parallel
        tasks = []
        for strat in strategies:
            if strat == "brave":
                tasks.append(("brave", search_brave(query, session, count)))
            elif strat == "sorsa":
                tasks.append(("sorsa", search_sorsa(query, session, count)))
            elif strat == "nitter":
                tasks.append(("nitter", search_nitter(query, session, count)))

        results = await asyncio.gather(
            *[t[1] for t in tasks],
            return_exceptions=True,
        )

        # Merge + dedup
        all_mentions: Dict[str, Dict[str, Any]] = {}
        sources_ok: List[str] = []
        errors: List[str] = []

        for (strat_name, _task), result in zip(tasks, results):
            if isinstance(result, Exception):
                errors.append(f"{strat_name}: {str(result)[:100]}")
                continue
            if not isinstance(result, dict):
                errors.append(f"{strat_name}: bad response")
                continue
            if "error" in result:
                errors.append(f"{strat_name}: {result['error']}")
                continue
            mentions = result.get("mentions", [])
            if mentions:
                sources_ok.append(strat_name)
            for m in mentions:
                tid = m.get("tweet_id")
                if not tid:
                    continue
                # Keep richest version (highest engagement or most fields)
                existing = all_mentions.get(tid)
                if not existing:
                    m["_sources"] = [strat_name]
                    all_mentions[tid] = m
                else:
                    existing["_sources"].append(strat_name)
                    # Upgrade fields if new source has more data
                    if m.get("engagement") and not existing.get("engagement"):
                        existing["engagement"] = m["engagement"]
                    if m.get("posted_at") and not existing.get("posted_at"):
                        existing["posted_at"] = m["posted_at"]
                    if len(m.get("content", "")) > len(existing.get("content", "")):
                        existing["content"] = m["content"]

        deduped = list(all_mentions.values())
        # Sort by engagement if available, else by posted_at
        deduped.sort(
            key=lambda m: (m.get("engagement") or 0, m.get("posted_at") or ""),
            reverse=True,
        )

        return {
            "query": query,
            "sources_used": sources_ok,
            "mentions": deduped[:count * 2],  # allow bigger cap since parallel
            "total_found": len(deduped),
            "priority": priority,
            "errors": errors if errors else None,
        }
    finally:
        if owned_session:
            await session.close()


# =============================================================================
# HELPERS FOR HERMES INVESTIGATION
# =============================================================================

async def find_first_mention(
    query: str,
    session: Optional[aiohttp.ClientSession] = None,
    hours: int = 6,
) -> Optional[Dict[str, Any]]:
    """
    Find who was FIRST to mention this token. Critical for catalyst detection.
    Sorts by age, returns oldest mention in window.
    """
    result = await search_x_mentions(query, session, count=100)
    mentions = result.get("mentions", [])
    if not mentions:
        return None
    # Sort by posted_at ascending (oldest first) — if timestamp available
    sortable = [m for m in mentions if m.get("posted_at")]
    if sortable:
        sortable.sort(key=lambda m: m["posted_at"])
        return sortable[0]
    return mentions[-1]  # last in list = usually oldest


async def search_token_exhaustive(
    token_address: str,
    ticker: Optional[str] = None,
    name: Optional[str] = None,
    priority: str = "normal",
    session: Optional[aiohttp.ClientSession] = None,
) -> Dict[str, Any]:
    """
    Exhaustive search: address + ticker + name + variations, all in parallel.
    Dedupes by tweet_id, returns unified mentions.

    Critical for catalyst detection — some tweets mention only address,
    some only ticker, some only name. Must check ALL.

    Args:
        token_address: '0xabc...'
        ticker: 'AIB' (without $)
        name: 'America is Back'
        priority: 'normal'|'high'|'critical' — controls Brave inclusion
    """
    owned = False
    if session is None:
        session = aiohttp.ClientSession()
        owned = True

    try:
        queries = [token_address]
        if ticker:
            queries.append(f"${ticker}")
        if name:
            queries.append(f'"{name}"')
            queries.append(f'{name} token')

        # Fire all in parallel, each with its own Sorsa+Nitter(+Brave) parallel dispatch
        results = await asyncio.gather(
            *[search_x_mentions(q, session, count=30, priority=priority) for q in queries],
            return_exceptions=True,
        )

        # Merge by tweet_id
        all_mentions: Dict[str, Dict[str, Any]] = {}
        queries_that_matched: List[str] = []
        errors: List[str] = []

        for query, result in zip(queries, results):
            if isinstance(result, Exception):
                errors.append(f"{query}: {str(result)[:80]}")
                continue
            mentions = result.get("mentions", [])
            if mentions:
                queries_that_matched.append(query)
            for m in mentions:
                tid = m.get("tweet_id")
                if not tid:
                    continue
                existing = all_mentions.get(tid)
                if not existing:
                    m.setdefault("_matched_queries", [])
                    m["_matched_queries"].append(query)
                    all_mentions[tid] = m
                else:
                    existing.setdefault("_matched_queries", []).append(query)

        deduped = list(all_mentions.values())
        # Sort oldest first (for catalyst detection)
        deduped.sort(key=lambda m: m.get("posted_at", ""))

        first_mention = deduped[0] if deduped else None

        # Top shillers (by engagement + frequency)
        from collections import Counter
        author_freq = Counter()
        author_total_engagement = {}
        for m in deduped:
            a = m.get("author")
            if a:
                author_freq[a] += 1
                author_total_engagement[a] = author_total_engagement.get(a, 0) + (m.get("engagement") or 0)

        top_shillers = [
            {"author": a, "mention_count": cnt,
             "total_engagement": author_total_engagement.get(a, 0)}
            for a, cnt in author_freq.most_common(10)
        ]

        return {
            "queries_used": queries,
            "queries_that_matched": queries_that_matched,
            "total_mentions_deduped": len(deduped),
            "unique_authors": len(author_freq),
            "first_mention": first_mention,
            "catalyst_author": first_mention.get("author") if first_mention else None,
            "catalyst_time": first_mention.get("posted_at") if first_mention else None,
            "top_shillers": top_shillers,
            "mentions": deduped[:50],  # cap
            "errors": errors or None,
        }
    finally:
        if owned:
            await session.close()


async def count_mentions(
    query: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> Dict[str, Any]:
    """Just count, for quick velocity assessment."""
    result = await search_x_mentions(query, session, count=100)
    return {
        "query": query,
        "total": result.get("total_found", 0),
        "source": result.get("source"),
    }
