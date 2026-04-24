"""
Top Gainers Fetcher — combina múltiplas fontes pra achar tokens pumping.

DexScreener /token-boosts/top retorna poucos (7-10). Combino:
  1. DexScreener token-boosts (trending)
  2. DexScreener token-profiles latest (profiles novos)
  3. DexScreener search com filtros (high volume)
  4. GeckoTerminal /networks/eth/new_pools (pools novos)

Deduplica por address, retorna top N com biggest recent pumps.
"""

import asyncio
import logging
from typing import Any, Dict, List, Set

import aiohttp

logger = logging.getLogger("top_gainers")


async def fetch_dex_boosts(
    session: aiohttp.ClientSession, chain: str = "ethereum"
) -> List[Dict[str, Any]]:
    try:
        async with session.get(
            "https://api.dexscreener.com/token-boosts/top/v1",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            if not isinstance(data, list):
                return []
            return [
                {"address": d.get("tokenAddress"), "chain": d.get("chainId"), "source": "boosts"}
                for d in data
                if d.get("chainId") == chain
            ]
    except Exception as e:
        logger.debug(f"boosts failed: {e}")
        return []


async def fetch_dex_profiles(
    session: aiohttp.ClientSession, chain: str = "ethereum"
) -> List[Dict[str, Any]]:
    try:
        async with session.get(
            "https://api.dexscreener.com/token-profiles/latest/v1",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            if not isinstance(data, list):
                return []
            return [
                {"address": d.get("tokenAddress"), "chain": d.get("chainId"), "source": "profiles"}
                for d in data
                if d.get("chainId") == chain
            ]
    except Exception as e:
        logger.debug(f"profiles failed: {e}")
        return []


async def fetch_gecko_trending(
    session: aiohttp.ClientSession, network: str = "eth"
) -> List[Dict[str, Any]]:
    """GeckoTerminal trending pools."""
    try:
        async with session.get(
            f"https://api.geckoterminal.com/api/v2/networks/{network}/trending_pools",
            params={"page": 1},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            pools = data.get("data", [])
            out = []
            for p in pools[:50]:
                attrs = p.get("attributes", {})
                # Extract base token (vs quote)
                base_token_addr = None
                rels = p.get("relationships", {})
                base_rel = (rels.get("base_token") or {}).get("data", {})
                base_id = base_rel.get("id", "")
                if "_" in base_id:
                    base_token_addr = base_id.split("_", 1)[1]
                if base_token_addr:
                    out.append({
                        "address": base_token_addr,
                        "chain": "ethereum" if network == "eth" else network,
                        "source": "gecko_trending",
                        "price_change_24h": float(
                            (attrs.get("price_change_percentage") or {}).get("h24", 0) or 0
                        ),
                        "volume_24h": float(attrs.get("volume_usd", {}).get("h24", 0) or 0),
                    })
            return out
    except Exception as e:
        logger.debug(f"gecko failed: {e}")
        return []


async def fetch_gecko_new_pools(
    session: aiohttp.ClientSession, network: str = "eth"
) -> List[Dict[str, Any]]:
    """GeckoTerminal new pools (recently launched)."""
    try:
        async with session.get(
            f"https://api.geckoterminal.com/api/v2/networks/{network}/new_pools",
            params={"page": 1},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            pools = data.get("data", [])
            out = []
            for p in pools[:50]:
                attrs = p.get("attributes", {})
                rels = p.get("relationships", {})
                base_rel = (rels.get("base_token") or {}).get("data", {})
                base_id = base_rel.get("id", "")
                if "_" in base_id:
                    base_token_addr = base_id.split("_", 1)[1]
                    out.append({
                        "address": base_token_addr,
                        "chain": "ethereum" if network == "eth" else network,
                        "source": "gecko_new",
                        "price_change_24h": float(
                            (attrs.get("price_change_percentage") or {}).get("h24", 0) or 0
                        ),
                        "volume_24h": float(attrs.get("volume_usd", {}).get("h24", 0) or 0),
                    })
            return out
    except Exception as e:
        logger.debug(f"new_pools failed: {e}")
        return []


async def fetch_dex_search_high_volume(
    session: aiohttp.ClientSession, chain: str = "ethereum"
) -> List[Dict[str, Any]]:
    """Use search with common queries to find active tokens."""
    out: List[Dict[str, Any]] = []
    # Queries that typically return active pairs
    for query in ["eth", "uni", "pepe"]:
        try:
            async with session.get(
                f"https://api.dexscreener.com/latest/dex/search?q={query}",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()
                pairs = data.get("pairs", []) or []
                for p in pairs[:20]:
                    if p.get("chainId") != chain:
                        continue
                    chg_24h = float((p.get("priceChange") or {}).get("h24", 0) or 0)
                    if chg_24h > 50:  # only pumping
                        out.append({
                            "address": (p.get("baseToken") or {}).get("address"),
                            "chain": chain,
                            "source": "search",
                            "price_change_24h": chg_24h,
                            "volume_24h": float((p.get("volume") or {}).get("h24", 0) or 0),
                        })
        except Exception:
            continue
    return out


async def get_top_gainers(chain: str = "ethereum", limit: int = 100) -> List[Dict[str, Any]]:
    """Combine all sources, dedup, sort by pump magnitude."""
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            fetch_dex_boosts(session, chain),
            fetch_dex_profiles(session, chain),
            fetch_gecko_trending(session, "eth" if chain == "ethereum" else chain),
            fetch_gecko_new_pools(session, "eth" if chain == "ethereum" else chain),
            fetch_dex_search_high_volume(session, chain),
            return_exceptions=True,
        )

        seen: Set[str] = set()
        merged: List[Dict[str, Any]] = []
        for source_results in results:
            if isinstance(source_results, Exception):
                continue
            for item in source_results:
                addr = (item.get("address") or "").lower()
                if not addr or addr in seen:
                    continue
                seen.add(addr)
                merged.append(item)

        # Sort: highest 24h change first, fallback volume
        def sort_key(x):
            return (
                x.get("price_change_24h", 0) or 0,
                x.get("volume_24h", 0) or 0,
            )
        merged.sort(key=sort_key, reverse=True)
        return merged[:limit]


if __name__ == "__main__":
    import sys, json
    async def main():
        chain = sys.argv[1] if len(sys.argv) > 1 else "ethereum"
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 100
        results = await get_top_gainers(chain, limit)
        print(f"Found {len(results)} tokens on {chain}")
        for r in results[:20]:
            print(f"  {r['address'][:12]}... source={r['source']} "
                  f"24h={r.get('price_change_24h', 0):.1f}% vol=${r.get('volume_24h', 0):,.0f}")
        with open(f"/tmp/top_gainers_{chain}.json", "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nSaved to /tmp/top_gainers_{chain}.json")
    asyncio.run(main())
