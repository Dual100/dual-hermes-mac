"""Virtuals Community Twitter monitor — gated alpha source.

Twitter Communities are member-only feeds (like Telegram private groups but on
Twitter). The Virtuals official community (~500 members) discusses launches,
agents, and integrations BEFORE they reach public timelines.

This monitor polls Sorsa /community-tweets every 90s, detects new posts
containing $TICKER or 0xADDRESS, and routes through the same investigate_token
pipeline as the Telegram group listener.

Source label in alerts: `community/virtuals`.

Cost: 60 polls/h × 24h = 1,440 calls/day per community.

Run: integrated as asyncio task in hermes main.py via run_virtuals_community_monitor()
"""
import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Set

import aiohttp

logger = logging.getLogger("hermes.community_monitor")

VIRTUALS_COMMUNITY_ID = os.environ.get(
    "HERMES_VIRTUALS_COMMUNITY_ID", "1925691137571820005"
)
# 5min poll — empirical analysis showed 0% of community posts contain 0xADDRESS
# and 29% contain only already-known tickers. 90s poll wastes quota on chatter.
POLL_INTERVAL_SEC = int(os.environ.get("HERMES_COMMUNITY_POLL_SEC", "300"))
SORSA = "https://api.sorsa.io/v3"

EVM_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
TICKER_RE = re.compile(r"\$([A-Z][A-Z0-9]{1,9})\b")

# Tickers we already monitor heavily through other paths (Butler, virtuals_twitter_monitor,
# auto-discovery). If a community post ONLY mentions these, it's chatter, not alpha.
KNOWN_TICKERS_BLACKLIST = {
    "VIRTUAL", "FACY", "OTTO", "SR", "AIXBT", "TIBBIR", "VADER",
    "LINK", "HYPE", "XRP", "AAVE", "GOLD", "ETH", "BTC", "USDC", "USDT",
    "SOL", "BASE", "DOGE", "PEPE", "WIF", "BONK",
}

STATE_FILE = Path(os.environ.get("HERMES_HOME", str(Path.home() / "DualHermes"))) / "data" / "virtuals_community_state.json"


def _load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        logger.debug(f"save state failed: {e}")


async def fetch_community_tweets(community_id: str,
                                   session: aiohttp.ClientSession) -> List[dict]:
    key = os.environ.get("TWEETSCOUT_API_KEY", "").strip('"')
    if not key:
        return []
    try:
        async with session.post(
            f"{SORSA}/community-tweets",
            headers={"ApiKey": key, "Content-Type": "application/json"},
            json={"community_id": community_id, "order": "latest"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            if r.status != 200:
                logger.debug(f"community-tweets {r.status}: {await r.text()}")
                return []
            data = await r.json()
            return data.get("tweets", []) or []
    except Exception as e:
        logger.debug(f"fetch_community_tweets {community_id}: {e}")
        return []


async def _process_community_tweet(tweet: dict, bot_token: str, user_chat_id: int,
                                     cooldowns: dict, investigate_token,
                                     _format_alert, _build_keyboard, send_alert) -> None:
    text = tweet.get("full_text") or tweet.get("text") or ""
    if not text or len(text.strip()) < 10:
        return

    user = tweet.get("user") or {}
    author = user.get("username", "?")
    fc = user.get("followers_count", 0)
    tweet_id = tweet.get("id", "")
    msg_url = f"https://x.com/{author}/status/{tweet_id}"

    evm_addrs = list(set(EVM_RE.findall(text)))
    tickers = list(set(TICKER_RE.findall(text)))
    if not evm_addrs and not tickers:
        return

    # Filter: if NO EVM and ALL tickers are already known/monitored, skip.
    # Empirical: 29% of community tweets had only already-known tickers (chatter).
    if not evm_addrs:
        unknown_tickers = [t for t in tickers if t.upper() not in KNOWN_TICKERS_BLACKLIST]
        if not unknown_tickers:
            logger.debug(f"  @{author}: all tickers known ({tickers}) — skip")
            return
        tickers = unknown_tickers

    logger.info(f"[Virtuals Community] @{author} ({fc:,} fol): "
                f"{len(evm_addrs)} evm + {len(tickers)} tickers (filtered)")

    # Resolve tickers if no EVM
    targets: List[tuple] = [(a, "ethereum", None) for a in evm_addrs]
    if not evm_addrs and tickers:
        try:
            from telegram_group_listener import resolve_ticker_to_address
            for tk in tickers[:3]:
                a, c = await resolve_ticker_to_address(tk, None)
                if a:
                    targets.append((a, c or "base", tk))
        except Exception as e:
            logger.debug(f"resolve_ticker failed: {e}")

    now = time.time()
    for addr, chain, ticker_src in targets:
        key = f"comm:{addr.lower()}"
        if cooldowns.get(key, 0) > now:
            continue
        # Use same cooldown as TG groups — 30min
        cooldowns[key] = now + 1800

        try:
            anatomy = await investigate_token(
                addr, chain_hint=chain,
                group_name="Virtuals Community", msg_text=text,
            )
            if not anatomy:
                continue
            d = anatomy.get("_decision", {})
            score = d.get("score", 0)
            mcap = anatomy.get("current_mcap", 0)

            # Community source has high baseline trust — lower threshold
            COMMUNITY_THRESHOLD = 50  # vs 60 for tg_group
            if score < COMMUNITY_THRESHOLD:
                logger.info(f"  {addr} score={score} < {COMMUNITY_THRESHOLD} — skip")
                continue

            # mcap cap ($30M for community vs $20M tg) — community signal is higher quality
            cap = 30_000_000
            if cap and mcap > cap:
                logger.info(f"  {addr} score={score} mcap=${mcap:,.0f} > ${cap:,} — skip")
                continue

            alert_text = _format_alert("Virtuals Community", f"@{author}",
                                          msg_url, anatomy)
            kb = _build_keyboard(addr, chain)
            await send_alert(bot_token, user_chat_id, alert_text, kb)
            logger.info(f"  ALERTED {addr} score={score}")
        except Exception as e:
            logger.exception(f"investigate {addr}: {e}")


async def run_community_monitor(bot_token: str, user_chat_id: int) -> None:
    """Main loop — polls Virtuals Community every POLL_INTERVAL_SEC."""
    state = _load_state()
    cooldowns = state.get("cooldowns", {})
    seen_tweet_ids: Set[str] = set(state.get("seen_tweet_ids", [])[-1000:])

    # Lazy imports — avoid circular at module load
    from telegram_group_listener import (
        investigate_token, _format_alert, _build_keyboard, send_alert,
    )

    logger.info(f"Virtuals Community monitor: starting (community_id={VIRTUALS_COMMUNITY_ID}, "
                f"poll={POLL_INTERVAL_SEC}s)")

    async with aiohttp.ClientSession() as session:
        # Warm-up: mark all current tweets as seen so we only alert on NEW
        warmup = await fetch_community_tweets(VIRTUALS_COMMUNITY_ID, session)
        if warmup:
            for t in warmup:
                tid = str(t.get("id", ""))
                if tid:
                    seen_tweet_ids.add(tid)
            logger.info(f"  warmup: marked {len(warmup)} existing tweets as seen")

        while True:
            try:
                tweets = await fetch_community_tweets(VIRTUALS_COMMUNITY_ID, session)
                new_tweets = []
                for t in tweets:
                    tid = str(t.get("id", ""))
                    if tid and tid not in seen_tweet_ids:
                        new_tweets.append(t)
                        seen_tweet_ids.add(tid)
                if new_tweets:
                    logger.info(f"Virtuals Community: {len(new_tweets)} NEW tweets")
                    for t in new_tweets:
                        try:
                            await _process_community_tweet(
                                t, bot_token, user_chat_id, cooldowns,
                                investigate_token, _format_alert,
                                _build_keyboard, send_alert,
                            )
                        except Exception as e:
                            logger.exception(f"process community tweet: {e}")
                # Persist state every cycle
                state["cooldowns"] = {k: v for k, v in cooldowns.items() if v > time.time()}
                state["seen_tweet_ids"] = list(seen_tweet_ids)[-1000:]
                _save_state(state)
            except Exception as e:
                logger.exception(f"community monitor loop: {e}")
            await asyncio.sleep(POLL_INTERVAL_SEC)
