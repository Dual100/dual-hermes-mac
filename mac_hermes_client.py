"""
Mac-side HTTPS client for Hermes Data API (on Hetzner).

Used by MCP tools to query Hetzner's smart money, creators, and Virtuals data
WITHOUT having Postgres exposed or replicated. Pure HTTPS + bearer auth.

ALL outbound — Mac doesn't open any port. HTTPS calls go out through user's
internet connection and return encrypted data from Hetzner.

Env vars required (in Mac's .env.hermes):
  HERMES_DATA_API_URL=https://dualzero.duckdns.org/hermes
  HERMES_DATA_API_KEY=<bearer token generated with secrets.token_urlsafe(32)>

Usage:
    from mac_hermes_client import HermesDataClient
    client = HermesDataClient()
    result = await client.is_smart_money("0xabc...")
    result = await client.creator_by_wallet("0xabc...")
"""

import logging
import os
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger("mac_hermes_client")


class HermesDataClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_seconds: int = 10,
    ):
        self.base_url = (base_url or os.environ.get("HERMES_DATA_API_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("HERMES_DATA_API_KEY", "")
        self.timeout = aiohttp.ClientTimeout(total=timeout_seconds)

        if not self.base_url:
            raise ValueError("HERMES_DATA_API_URL env var not set")
        if not self.api_key:
            raise ValueError("HERMES_DATA_API_KEY env var not set")

        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self.timeout,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        session = await self._get_session()
        url = f"{self.base_url}{path}"
        try:
            async with session.get(url, params=params) as resp:
                if resp.status == 401 or resp.status == 403:
                    logger.error(f"Auth error on {path}: {resp.status}")
                    return {"error": "auth", "status": resp.status}
                if resp.status == 429:
                    logger.warning(f"Rate limited on {path}")
                    return {"error": "rate_limit"}
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(f"API {path} returned {resp.status}: {body[:200]}")
                    return {"error": "http", "status": resp.status, "body": body[:500]}
                return await resp.json()
        except aiohttp.ClientError as e:
            logger.warning(f"Client error on {path}: {e}")
            return {"error": "client", "message": str(e)}
        except Exception as e:
            logger.exception(f"Unexpected error on {path}: {e}")
            return {"error": "exception", "message": str(e)}

    # =========================================================================
    # SMART MONEY
    # =========================================================================
    async def is_smart_money(self, wallet: str) -> Dict[str, Any]:
        return await self._get("/smart-money/is-smart", {"wallet": wallet})

    async def smart_money_wallets(self) -> Dict[str, Any]:
        return await self._get("/smart-money/wallets")

    async def smart_money_recent_buys(
        self, hours: int = 24, tier: Optional[str] = None
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"hours": hours}
        if tier:
            params["tier"] = tier
        return await self._get("/smart-money/recent-buys", params)

    async def smart_money_on_token(self, address: str) -> Dict[str, Any]:
        return await self._get("/smart-money/token", {"address": address})

    # =========================================================================
    # CREATORS
    # =========================================================================
    async def creator_by_wallet(self, wallet: str) -> Dict[str, Any]:
        return await self._get("/creators/by-wallet", {"wallet": wallet})

    async def creator_is_farmer(self, wallet: str) -> Dict[str, Any]:
        return await self._get("/creators/is-farmer", {"wallet": wallet})

    async def creator_history(self, identifier: str) -> Dict[str, Any]:
        return await self._get("/creators/history", {"identifier": identifier})

    # =========================================================================
    # VIRTUALS
    # =========================================================================
    async def virtuals_token(self, address: str) -> Dict[str, Any]:
        return await self._get("/virtuals/token", {"address": address})

    async def virtuals_by_wallet(self, wallet: str) -> Dict[str, Any]:
        return await self._get("/virtuals/by-wallet", {"wallet": wallet})

    # =========================================================================
    # PATTERNS / CHAIN HOTNESS
    # =========================================================================
    async def patterns_recent_pumps(
        self, days: int = 30, chain: Optional[str] = None
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"days": days}
        if chain:
            params["chain"] = chain
        return await self._get("/patterns/recent-pumps", params)

    async def chain_hotness(self) -> Dict[str, Any]:
        return await self._get("/chain/hotness")

    async def health(self) -> Dict[str, Any]:
        return await self._get("/health")
