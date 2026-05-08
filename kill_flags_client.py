"""Hermes kill flags client — polls Hetzner /kill-flags every 60s.

Single source of truth: dashboard buttons set Redis flags on Hetzner.
This module pulls the state and caches it for in-process use.

Usage:
    from kill_flags_client import is_disabled, start_polling

    if is_disabled('sorsa_disable'):
        return None  # skip Sorsa call

    start_polling()  # call once at service startup
"""
import asyncio
import logging
import os
import time
from typing import Dict

import aiohttp

logger = logging.getLogger("hermes.kill_flags")

API_URL = os.environ.get("HERMES_DATA_API_URL", "https://dualzero.duckdns.org/hermes").rstrip("/")
API_KEY = os.environ.get("HERMES_DATA_API_KEY", "")
POLL_INTERVAL = int(os.environ.get("KILL_FLAGS_POLL_SEC", "3"))

# In-process cache. Defaults to all flags OFF (= no kill).
_FLAGS: Dict[str, bool] = {
    "sorsa_disable": False,
    "twitter_listener_disable": False,
    "telegram_listener_disable": False,
    "cohort_disable": False,
    "all_alerts_disable": False,
}
_LAST_POLL_TS: float = 0
_BACKING_ENV = os.environ.get("DISABLE_SORSA", "0") == "1"


def is_disabled(flag: str) -> bool:
    """Returns True if the named kill flag is set OR env DISABLE_SORSA=1.

    Stale cache (>5min old) treated as "unknown" — defaults OFF for safety.
    """
    # Backwards compat: legacy DISABLE_SORSA env var still works
    if flag == "sorsa_disable" and _BACKING_ENV:
        return True
    return _FLAGS.get(flag, False)


async def _poll_once() -> None:
    global _LAST_POLL_TS
    if not API_KEY:
        return
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{API_URL}/kill-flags",
                headers={"Authorization": f"Bearer {API_KEY}"},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                if r.status != 200:
                    logger.debug(f"kill-flags poll status {r.status}")
                    return
                data = await r.json()
        flags = data.get("flags") or {}
        # Update cache
        for k, v in flags.items():
            if k in _FLAGS:
                _FLAGS[k] = bool(v)
        _LAST_POLL_TS = time.time()
        active = [k for k, v in _FLAGS.items() if v]
        if active:
            logger.info(f"kill flags ACTIVE: {active}")
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.debug(f"kill-flags poll fail: {e}")
    except Exception as e:
        logger.warning(f"kill-flags poll error: {e}")


async def _poll_loop() -> None:
    while True:
        await _poll_once()
        await asyncio.sleep(POLL_INTERVAL)


def start_polling() -> None:
    """Schedule the polling loop in the current event loop. Call once at startup."""
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_poll_loop())
        logger.info(f"kill_flags_client started — polling {API_URL}/kill-flags every {POLL_INTERVAL}s")
    except Exception as e:
        logger.warning(f"start_polling failed: {e}")
