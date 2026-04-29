"""Hermes Twitter MEGA listener — polls FxTwitter for high-impact accounts.

Tiers (set in mega_handles.json):
  HOT:    Elon, CZ, @X, @binance, @virtuals_io  → poll every 1-2s
  ALPHA:  KOLs/influencers                      → poll every 5-10s

When new tweet detected, extracts $TICKER and 0x... and runs Hermes
investigation. If investigation score >= ALERT_THRESHOLD, sends alert via
the same Hermes Telegram bot used for group alerts.
"""
import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, Set

import aiohttp

logger = logging.getLogger("hermes.twitter")

EVM_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
TICKER_RE = re.compile(r"\$([A-Z][A-Z0-9]{1,10})\b")

FXTWITTER = "https://api.fxtwitter.com"
SORSA = "https://api.sorsa.io/v3"
TWEETSCOUT_API_KEY = os.environ.get("TWEETSCOUT_API_KEY", "").strip('"')

HANDLES_FILE = Path("data/mega_handles.json")
COOLDOWN_FILE = Path("data/twitter_cooldowns.json")
STATE_FILE = Path("data/twitter_state.json")

ALERT_THRESHOLD = int(os.environ.get("HERMES_ALERT_THRESHOLD", "60"))
TOKEN_COOLDOWN = int(os.environ.get("HERMES_TOKEN_COOLDOWN_SEC", "300"))
HOT_POLL = int(os.environ.get("HERMES_TWITTER_HOT_POLL", "2"))
ALPHA_POLL = int(os.environ.get("HERMES_TWITTER_ALPHA_POLL", "8"))


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text()) if path.exists() else default
    except (OSError, json.JSONDecodeError):
        return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def load_handles() -> Dict[str, str]:
    """Returns {handle_lower: tier}."""
    raw = _load_json(HANDLES_FILE, {})
    out = {}
    for tier in ("HOT", "ALPHA"):
        for h in raw.get(tier, []) or []:
            out[h.lstrip("@").lower()] = tier
    return out


async def fetch_count(session: aiohttp.ClientSession, handle: str) -> int | None:
    try:
        async with session.get(f"{FXTWITTER}/{handle}",
                               timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                d = await r.json()
                return (d.get("user") or {}).get("tweets")
    except (aiohttp.ClientError, asyncio.TimeoutError):
        pass
    return None


async def fetch_count_and_latest(session: aiohttp.ClientSession, handle: str):
    """Returns (tweet_count, latest_tweet_id, latest_text) or (None, None, None).

    Strategy: FxTwitter (FREE) primary for count check + latest tweet ID detection.
    Only fall back to Sorsa /user-tweets when FxTwitter fails AND we need rich text.
    Sorsa is paid w/ tight quota — was burning 1M+ calls/day before this fix.
    """
    # PRIMARY: FxTwitter (free, no quota)
    try:
        async with session.get(f"{FXTWITTER}/{handle}",
                               timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                d = await r.json()
                user = d.get("user") or {}
                tweet = d.get("tweet") or {}
                tweet_id = tweet.get("id")
                text = (tweet.get("text") or "").strip()
                q = tweet.get("quote") or {}
                if q:
                    qt = (q.get("text") or "").strip()
                    if qt:
                        text = f"{text}\n[QUOTED]: {qt}".strip()
                if tweet_id or user.get("tweets"):
                    return user.get("tweets") or 0, tweet_id, text
    except (aiohttp.ClientError, asyncio.TimeoutError):
        pass
    # FALLBACK: Sorsa /user-tweets (paid — only when FxTwitter fails)
    if TWEETSCOUT_API_KEY:
        try:
            async with session.post(f"{SORSA}/user-tweets",
                                    headers={"ApiKey": TWEETSCOUT_API_KEY,
                                             "Content-Type": "application/json"},
                                    json={"username": handle, "count": 1},
                                    timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    data = await r.json()
                    tweets = data if isinstance(data, list) else data.get("tweets", [])
                    if tweets:
                        t = tweets[0]
                        tweet_id = t.get("id") or t.get("id_str")
                        text = (t.get("full_text") or t.get("text", "") or "").strip()
                        for key in ("quoted_status", "retweeted_status", "quote", "quoted"):
                            inner = t.get(key)
                            if isinstance(inner, dict):
                                inner_text = (inner.get("full_text") or inner.get("text", "") or "").strip()
                                if inner_text and inner_text not in text:
                                    text += f"\n[QUOTED]: {inner_text}"
                        return 0, tweet_id, text
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
    return None, None, None


async def enrich_tweet_with_quote(session: aiohttp.ClientSession, tweet_id: str) -> str:
    """Given a tweet ID, fetch full text + quote via FxTwitter /status/<id>."""
    if not tweet_id:
        return ""
    try:
        async with session.get(f"{FXTWITTER}/elonmusk/status/{tweet_id}",
                               timeout=aiohttp.ClientTimeout(total=6)) as r:
            if r.status != 200:
                return ""
            data = await r.json()
            t = data.get("tweet") or {}
            text = (t.get("text") or "").strip()
            q = t.get("quote") or {}
            if q:
                qt = (q.get("text") or "").strip()
                if qt:
                    text = f"{text}\n[QUOTED]: {qt}".strip()
            return text
    except Exception:
        return ""


async def fetch_article_body(session: aiohttp.ClientSession, handle: str,
                              tweet_id: str) -> str:
    """Fetch Twitter article (long-form) body via Sorsa v3 /article.

    Triggered when tweet text is short (<100 chars) on HOT tier — KOLs sometimes
    post articles where contracts/tickers are hidden in the article body, not
    the 280-char tweet. Returns concatenated title+preview+body or empty string.
    """
    if not TWEETSCOUT_API_KEY or not tweet_id:
        return ""
    try:
        url = f"https://twitter.com/{handle}/status/{tweet_id}"
        async with session.post(
            f"{SORSA}/article",
            headers={"ApiKey": TWEETSCOUT_API_KEY, "Content-Type": "application/json"},
            json={"tweet_link": url},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            if r.status != 200:
                return ""
            data = await r.json()
            # Article shape: {title, preview_text, cover_image, ...}
            parts = []
            for k in ("title", "preview_text", "text", "body"):
                v = data.get(k)
                if isinstance(v, str) and v.strip():
                    parts.append(v.strip())
            return "\n".join(parts)
    except Exception as e:
        logger.debug(f"fetch_article_body {handle}/{tweet_id}: {e}")
    return ""


async def fetch_quote_tweets(session: aiohttp.ClientSession, handle: str,
                              tweet_id: str) -> list[dict]:
    """Fetch quote-tweets for a given tweet via Sorsa v3 /quotes.

    Returns list of {username, followers_count, likes_count, full_text, ...}.
    Quote-tweets are stronger engagement signal than retweets (KOL wrote opinion).
    """
    if not TWEETSCOUT_API_KEY or not tweet_id:
        return []
    try:
        async with session.post(
            f"{SORSA}/quotes",
            headers={"ApiKey": TWEETSCOUT_API_KEY, "Content-Type": "application/json"},
            json={"tweet_link": f"https://twitter.com/{handle}/status/{tweet_id}"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            if r.status != 200:
                return []
            data = await r.json()
            return data.get("tweets", []) or []
    except Exception as e:
        logger.debug(f"fetch_quote_tweets {handle}/{tweet_id}: {e}")
    return []


async def check_kol_quote_engagement(session: aiohttp.ClientSession, handle: str,
                                       tweet_id: str, bot_token: str,
                                       user_chat_id: int,
                                       delay_seconds: int = 60) -> None:
    """Background task: wait `delay_seconds`, fetch quotes, alert on notable KOL engagement.

    "Notable" = likes >= 50 OR follower_count >= 50K. Skips quote-tweets with empty/short text.
    """
    try:
        await asyncio.sleep(delay_seconds)
        quotes = await fetch_quote_tweets(session, handle, tweet_id)
        if not quotes:
            return
        notable = []
        for q in quotes:
            user = q.get("user") or {}
            likes = q.get("likes_count", 0) or 0
            fc = user.get("followers_count", 0) or 0
            full_text = (q.get("full_text") or "").strip()
            if len(full_text) < 20:
                continue
            if likes >= 50 or fc >= 50_000:
                notable.append({
                    "username": user.get("username", "?"),
                    "followers": fc,
                    "likes": likes,
                    "text": full_text[:200],
                })
        if not notable:
            logger.info(f"  quotes @{handle}/{tweet_id}: {len(quotes)} total, 0 notable")
            return
        notable.sort(key=lambda x: -x["likes"])
        lines = [f"🔁 *KOL QUOTES on @{handle}'s tweet*",
                 f"  https://x.com/{handle}/status/{tweet_id}",
                 ""]
        for q in notable[:5]:
            fc_s = (f"{q['followers']/1_000_000:.1f}M" if q['followers'] >= 1_000_000
                    else f"{q['followers']/1000:.0f}K")
            lines.append(f"@{q['username']} ({fc_s} fol, {q['likes']}♥): _{q['text'][:140]}_")
        msg = "\n".join(lines)
        try:
            async with aiohttp.ClientSession() as s2:
                await s2.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={"chat_id": user_chat_id, "text": msg,
                          "parse_mode": "Markdown",
                          "disable_web_page_preview": True},
                    timeout=aiohttp.ClientTimeout(total=10),
                )
        except Exception as e:
            logger.warning(f"send kol quotes alert failed: {e}")
        logger.info(f"  quotes @{handle}/{tweet_id}: alerted {len(notable)} notable KOL quotes")
    except Exception as e:
        logger.debug(f"check_kol_quote_engagement {handle}/{tweet_id}: {e}")


async def fetch_latest_tweet(session: aiohttp.ClientSession, handle: str) -> dict | None:
    """Get latest tweet text. Tries Sorsa first (richer), falls back to FxTwitter profile."""
    if TWEETSCOUT_API_KEY:
        try:
            async with session.post(f"{SORSA}/user-tweets",
                                    headers={"ApiKey": TWEETSCOUT_API_KEY,
                                             "Content-Type": "application/json"},
                                    json={"username": handle, "count": 1},
                                    timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 200:
                    data = await r.json()
                    tweets = data if isinstance(data, list) else data.get("tweets", [])
                    if tweets:
                        t = tweets[0]
                        return {
                            "id": t.get("id") or t.get("id_str"),
                            "text": t.get("full_text") or t.get("text", ""),
                        }
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
    try:
        async with session.get(f"{FXTWITTER}/{handle}",
                               timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                data = await r.json()
                tweet = data.get("tweet") or {}
                return {"id": tweet.get("id"), "text": tweet.get("text", "")}
    except (aiohttp.ClientError, asyncio.TimeoutError):
        pass
    return None


async def run_listener(bot_token: str, user_chat_id: int) -> None:
    handles = load_handles()
    if not handles:
        logger.warning("No mega handles configured (data/mega_handles.json) — twitter idle")
        await asyncio.Event().wait()
        return

    logger.info(f"Twitter MEGA listener: {len(handles)} handles "
                f"(HOT poll {HOT_POLL}s, ALPHA poll {ALPHA_POLL}s)")

    state = _load_json(STATE_FILE, {})  # {handle: {tweet_count, last_tweet_id}}
    cooldowns = _load_json(COOLDOWN_FILE, {})
    last_poll: Dict[str, float] = {}

    from telegram_group_listener import (
        investigate_token, _format_alert, _build_keyboard, send_alert,
    )

    async with aiohttp.ClientSession() as session:
        # warm up — use Sorsa to get latest tweet ID for each handle
        for h in handles:
            count, latest_id, _ = await fetch_count_and_latest(session, h)
            state[h] = {"tweet_count": count or 0, "last_tweet_id": latest_id}
            logger.info(f"  @{h} ({handles[h]}): warmed (latest_id={latest_id})")
        _save_json(STATE_FILE, state)

        while True:
            now = time.time()
            handles_to_poll = [
                h for h, tier in handles.items()
                if (now - last_poll.get(h, 0)) >= (HOT_POLL if tier == "HOT" else ALPHA_POLL)
            ]
            for h in handles_to_poll:
                last_poll[h] = now
            if not handles_to_poll:
                await asyncio.sleep(0.5)
                continue
            # parallel fetch — returns (count, latest_id, latest_text)
            results = await asyncio.gather(
                *[fetch_count_and_latest(session, h) for h in handles_to_poll],
                return_exceptions=True,
            )
            for h, res in zip(handles_to_poll, results):
                if isinstance(res, Exception) or not res or res[0] is None:
                    continue
                count, latest_id, latest_text = res
                prev_id = state.get(h, {}).get("last_tweet_id")
                # NEW TWEET if latest tweet ID changed (most reliable)
                id_changed = latest_id and latest_id != prev_id
                # Skip first cycle (no prior id to compare)
                first_cycle = prev_id is None
                if first_cycle:
                    state.setdefault(h, {})["last_tweet_id"] = latest_id
                    continue
                if not id_changed:
                    continue
                state.setdefault(h, {})["last_tweet_id"] = latest_id
                logger.info(f"@{h} NEW TWEET (id {prev_id} → {latest_id})")
                # If FxTwitter latest text is too short, try Sorsa for richer
                text = latest_text or ""
                if len(text) < 50:
                    sorsa = await fetch_latest_tweet(session, h)
                    if sorsa and sorsa.get("text"):
                        text = sorsa["text"]
                        if sorsa.get("id"):
                            latest_id = sorsa["id"]
                            state[h]["last_tweet_id"] = latest_id
                await _process_tweet(h, handles[h], latest_id, text, session,
                                     bot_token, user_chat_id, cooldowns,
                                     investigate_token, _format_alert,
                                     _build_keyboard, send_alert)
            _save_json(STATE_FILE, state)
            _save_json(COOLDOWN_FILE, cooldowns)


async def _process_tweet(handle, tier, tweet_id, text, session, bot_token,
                         user_chat_id, cooldowns, investigate_token,
                         _format_alert, _build_keyboard, send_alert):
    if not text or len(text.strip()) < 5:
        return

    # For HOT tier, enrich with quote tweet text if not already present
    if tier == "HOT" and "[QUOTED]" not in text and tweet_id:
        extra = await enrich_tweet_with_quote(session, tweet_id)
        if extra and len(extra) > len(text):
            text = extra
            logger.info(f"  @{handle}: enriched with quote text ({len(extra)} chars)")

    # HOT tier: if tweet is short (likely a long-form article link), fetch article body.
    # Long-form articles often contain $TICKER or 0x... that the headline drops.
    if tier == "HOT" and tweet_id and len(text.strip()) < 100:
        article_body = await fetch_article_body(session, handle, tweet_id)
        if article_body and len(article_body) > 50:
            text = f"{text}\n[ARTICLE]: {article_body}"
            logger.info(f"  @{handle}: enriched with article body ({len(article_body)} chars)")

    # HOT tier only: schedule a 60s-delayed KOL quote-engagement check.
    # Quote-tweets are stronger engagement signal than retweets.
    if tier == "HOT" and tweet_id:
        asyncio.create_task(
            check_kol_quote_engagement(session, handle, tweet_id, bot_token, user_chat_id),
            name=f"kol-quotes-{handle}-{tweet_id}",
        )

    evm_addrs = list(set(EVM_RE.findall(text)))
    tickers = list(set(TICKER_RE.findall(text)))

    # HOT tier: ALWAYS run LLM (catches narrative tweets like "Scam Altman")
    # ALPHA tier: only when no $TICKER/0x detected (cost-saving)
    llm_terms = []
    run_llm = (tier == "HOT") or (not evm_addrs and not tickers)
    if run_llm:
        from telegram_group_listener import llm_extract_terms
        try:
            raw = await llm_extract_terms(text, session)
            if raw:
                # Anti-hallucination: terms must appear in source text
                src_lower = text.lower()
                import re as _re
                src_words = set(_re.findall(r"\b[a-z0-9]{2,}\b", src_lower))
                for t in raw:
                    t_clean = t.strip()
                    if not t_clean:
                        continue
                    if t_clean.lower() in src_lower:
                        llm_terms.append(t_clean)
                        continue
                    words = _re.findall(r"[a-z0-9]+", t_clean.lower())
                    if words and all(w in src_words for w in words):
                        llm_terms.append(t_clean)
                if llm_terms:
                    logger.info(f"  @{handle}: LLM extracted {llm_terms} from narrative tweet")
        except Exception as e:
            logger.debug(f"LLM extract for @{handle}: {e}")

    if not evm_addrs and not tickers and not llm_terms:
        return
    logger.info(f"  @{handle}: {len(evm_addrs)} evm + {len(tickers)} tickers + {len(llm_terms)} llm terms")

    # Resolve tickers + LLM terms → addrs
    targets: list[tuple] = [(a, "ethereum", None) for a in evm_addrs]
    if not evm_addrs:
        from telegram_group_listener import resolve_ticker_to_address
        for tk in tickers[:3]:
            a, c = await resolve_ticker_to_address(tk, session)
            if a:
                targets.append((a, c or "ethereum", tk))
        # Expand LLM terms (phrase + acronym + concat) and search each
        import re as _re
        seen_terms = set()
        for term in llm_terms[:5]:
            expanded = [term]
            words = _re.findall(r"[A-Za-z0-9]+", term)
            if len(words) >= 2:
                acronym = "".join(w[0] for w in words).upper()
                if 2 <= len(acronym) <= 8:
                    expanded.append(acronym)
                concat = "".join(words).upper()
                if 3 <= len(concat) <= 20:
                    expanded.append(concat)
            for ex in expanded:
                ex_up = ex.upper()
                if ex_up in seen_terms:
                    continue
                seen_terms.add(ex_up)
                a, c = await resolve_ticker_to_address(ex, session)
                if a:
                    targets.append((a, c or "ethereum", f"llm:{ex}"))
                    break  # one match per term is enough

    now = time.time()
    tweet_url = f"https://x.com/{handle}/status/{tweet_id}" if tweet_id else f"https://x.com/{handle}"
    src_label = f"X / @{handle} [{tier}]"
    for addr, chain, ticker_src in targets:
        key = addr.lower()
        if cooldowns.get(key, 0) > now:
            continue
        cooldowns[key] = now + TOKEN_COOLDOWN

        anatomy = await investigate_token(addr, chain_hint=chain,
                                          group_name=src_label, msg_text=text)
        if not anatomy:
            continue
        d = anatomy.get("_decision", {})
        score = d.get("score", 0)
        # MEGA tier: bonus +10 to score (auto-promote)
        if tier == "HOT":
            score = min(100, score + 10)
            d["score"] = score
            d.setdefault("reasons", []).insert(0, f"+10 MEGA tweet ({handle})")

        # MEGA tier gets LOWER threshold (50 vs 75) — the source IS the signal
        effective_threshold = 50 if tier == "HOT" else ALERT_THRESHOLD
        if score < effective_threshold:
            continue
        # GEM ONLY MODE — HOT tier auto-qualifies (Elon, CZ, etc).
        # ALPHA tier needs additional signal validation.
        from telegram_group_listener import GEM_MODE, _gem_quality_check, _gem_daily_check, _gem_register_alert
        if GEM_MODE:
            if tier != "HOT":
                passes, why = _gem_quality_check(anatomy, source=f"twitter/{tier}/@{handle}")
                if not passes:
                    logger.info(f"  {addr} ALPHA tier — gem-mode SKIP ({why})")
                    continue
            if not _gem_daily_check():
                logger.info(f"  {addr} — gem-mode daily cap reached, skip")
                continue
        # NO mcap cap for Twitter MEGA — Elon can pump $100M token
        alert_text = _format_alert(src_label, f"@{handle}", tweet_url, anatomy)
        kb = _build_keyboard(addr, anatomy.get("chain") or chain or "ethereum")
        await send_alert(bot_token, user_chat_id, alert_text, keyboard=kb)
        try:
            from outcome_tracker import record_alert
            record_alert(
                token=addr, chain=anatomy.get("chain") or chain or "ethereum",
                symbol=anatomy.get("symbol") or "",
                score=score, action=d.get("action", "ALERT"),
                source=f"twitter/{tier}/@{handle}",
                mcap=anatomy.get("current_mcap") or 0,
                price=anatomy.get("current_price") or 0,
            )
        except Exception as e:
            logger.debug(f"record_alert failed: {e}")
        if GEM_MODE: _gem_register_alert()
        logger.info(f"  ALERTED {addr} score={score} mcap=${(anatomy.get('current_mcap') or 0):,.0f} via @{handle}"
                    f"{' (ticker $' + ticker_src + ')' if ticker_src else ''}")
        try:
            from convergence_engine import register_signal
            from telegram_group_listener import _send_convergence_alert
            conv = register_signal(addr, source=f"twitter/{tier}/@{handle}",
                                   score=score, symbol=anatomy.get("symbol") or "")
            if conv.get("triggered"):
                await _send_convergence_alert(addr, anatomy, conv, bot_token, user_chat_id)
        except Exception as e:
            logger.debug(f"convergence register failed: {e}")
