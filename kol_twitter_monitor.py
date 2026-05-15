"""KOL Twitter Monitor — polls 158 KOL twitter handles from kolscanbrasil.io
via FxTwitter (free, no Sorsa quota dependency).

Separate from virtuals_twitter_monitor — own source tag `kol_twitter/<handle>`
so the channel_quality tracker grades these KOLs independently.

On new tweet: extract CAs/tickers, classify intent via Hermes LLM, register
into convergence_engine. Alert when actionable.

Cycle: 158 handles in batches of 20, parallel within batch, 2s sleep between
batches → ~16s per full sweep, repeats every 5min.
"""
import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("hermes.kol_twitter")

HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / "hermes-mac")))
KOLS_FILE = HOME / "data" / "kolscan_all_158_kols.json"
STATE_FILE = HOME / "data" / "kol_twitter_state.json"
HERMES_DATA_API_URL = os.environ.get("HERMES_DATA_API_URL", "").rstrip("/")
HERMES_DATA_API_KEY = os.environ.get("HERMES_DATA_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "llama-4-maverick")

BOT_TOKEN = os.environ.get("HERMES_TELEGRAM_BOT_TOKEN", "")
USER_CHAT_ID = os.environ.get("HERMES_USER_CHAT_ID", "")

POLL_INTERVAL = 300        # 5min between full Nitter sweeps (slower fallback)
BATCH_SIZE = 6            # handles fetched in parallel (Nitter batch)
BATCH_SLEEP = 4.0          # sleep between batches (Nitter)
FETCH_TIMEOUT = 8
LLM_TIMEOUT = 10

# FAST detect via FxTwitter — checks user.tweets counter every FAST_INTERVAL.
# When counter increments → fetch tweet via Nitter (or wait for next Nitter
# sweep). Gives ~5s detection latency for top KOLs vs 5min for full sweep.
FAST_INTERVAL = 2          # seconds between fast polls
FAST_HANDLES_LIMIT = 200   # ALL handles (was 20 → all 158 polled together)
FAST_BATCH = 200           # full parallel (no batching)
FAST_BATCH_SLEEP = 0.0     # unused
FAST_TCP_LIMIT = 80        # max concurrent sockets

EVM_RE = re.compile(r"0x[a-fA-F0-9]{40}")
SOL_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
TICKER_RE = re.compile(r"\$([A-Z][A-Z0-9_]{1,12})\b")

# Cycle budget: max alerts/hour to avoid spam
_alerts_log: list = []
MAX_ALERTS_HOUR = 30


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"last_tweet_id": {}}


def _save_state(state: dict):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _budget_ok() -> bool:
    cutoff = time.time() - 3600
    while _alerts_log and _alerts_log[0] < cutoff:
        _alerts_log.pop(0)
    return len(_alerts_log) < MAX_ALERTS_HOUR


def _budget_register():
    _alerts_log.append(time.time())


def _is_solana_ca(ca: str) -> bool:
    if not ca:
        return False
    ca = ca.strip()
    return not ca.startswith("0x") and 32 <= len(ca) <= 44


# Hardcoded dead handles (HTTP 404 = user deleted/renamed). Skip to save requests.
_DEAD_HANDLES = {"artcrypto_br", "rotzak_"}



MIN_KOL_PNL = float(os.getenv("HERMES_MIN_KOL_PNL_USD", "3000"))
# WR is noisy for KOLs with few trades (median is 50%, with many 0/100% outliers).
# Default off — PnL is the real signal. Bump via env if needed.
MIN_KOL_WR = float(os.getenv("HERMES_MIN_KOL_WR_PCT", "0"))


def load_handles() -> List[Dict]:
    """Load KOLs and filter to performers only.

    Without this gate the listener polls 158 KOLs and fires on ANY post, even
    accounts with negative monthly PnL (median of the roster is -$0.43). Keep
    only accounts with both PnL >= MIN_KOL_PNL and WR >= MIN_KOL_WR.
    """
    if not KOLS_FILE.exists():
        logger.error(f"KOLs file missing: {KOLS_FILE}")
        return []
    data = json.loads(KOLS_FILE.read_text())
    base = [k for k in data if k.get("twitter")
            and k["twitter"].lower() not in _DEAD_HANDLES]
    qualified = [k for k in base
                 if (k.get("pnl_usd") or 0) >= MIN_KOL_PNL
                 and (k.get("wr") or 0) >= MIN_KOL_WR]
    qualified.sort(key=lambda k: -(k.get("pnl_usd") or 0))
    logger.info(f"KOL roster: {len(base)} total → {len(qualified)} qualified "
                f"(min ${MIN_KOL_PNL:.0f} PnL + {MIN_KOL_WR:.0f}% WR)")
    return qualified


NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.tiekoetter.com",
    "https://nitter.privacyredirect.com",
]


# State for fast-detect: handle → last known user.tweets count
_FAST_COUNTERS: Dict[str, int] = {}


async def fast_check_tweet_count(session: aiohttp.ClientSession,
                                    handle: str) -> Optional[int]:
    """Returns current tweet count via FxTwitter /username (~200ms).
    user.tweets increments when KOL posts. Use as cheap polling signal.
    """
    try:
        async with session.get(
            f"https://api.fxtwitter.com/{handle}",
            timeout=aiohttp.ClientTimeout(total=4),
            headers={"User-Agent": "Mozilla/5.0 HermesFastDetect"},
        ) as r:
            if r.status != 200:
                return None
            d = await r.json()
        u = d.get("user") or {}
        return u.get("tweets")
    except Exception:
        return None


async def fast_detect_loop(handles: List[Dict], state: dict, alert_queue: asyncio.Queue):
    """Background loop: poll top KOLs via FxTwitter every FAST_INTERVAL seconds,
    fire Nitter fetch when tweet count increments.
    """
    top = handles[:FAST_HANDLES_LIMIT]
    logger.info(f"fast-detect loop ON — {len(top)} KOLs every {FAST_INTERVAL}s "
                f"(connection pool: {FAST_TCP_LIMIT} sockets)")
    # Persistent session with connection pooling — reuse sockets across cycles
    connector = aiohttp.TCPConnector(limit=FAST_TCP_LIMIT, limit_per_host=FAST_TCP_LIMIT,
                                       keepalive_timeout=30)
    async with aiohttp.ClientSession(connector=connector) as sess:
        # Warm-up
        results = await asyncio.gather(
            *[fast_check_tweet_count(sess, k["twitter"]) for k in top],
            return_exceptions=True,
        )
        for kol, count in zip(top, results):
            if isinstance(count, int):
                _FAST_COUNTERS[kol["twitter"]] = count
        logger.info(f"fast-detect warm-up done — {len(_FAST_COUNTERS)} handles tracked")

        # Main fast loop
        while True:
            try:
                cycle_start = time.time()
                results = await asyncio.gather(
                    *[fast_check_tweet_count(sess, k["twitter"]) for k in top],
                    return_exceptions=True,
                )
                for kol, count in zip(top, results):
                    if not isinstance(count, int):
                        continue
                    h = kol["twitter"]
                    prev = _FAST_COUNTERS.get(h)
                    if prev is None:
                        _FAST_COUNTERS[h] = count
                        continue
                    if count > prev:
                        # New tweet detected! Trigger Nitter fetch
                        logger.info(
                            f"⚡ FAST DETECT @{h}: count {prev}→{count} "
                            f"(+{count - prev} tweet)"
                            )
                        _FAST_COUNTERS[h] = count
                        await alert_queue.put(kol)
                # Sleep remainder of FAST_INTERVAL (subtract cycle time)
                elapsed = time.time() - cycle_start
                wait = max(0.3, FAST_INTERVAL - elapsed)
                await asyncio.sleep(wait)
            except Exception as e:
                logger.warning(f"fast loop error: {e}")
                await asyncio.sleep(FAST_INTERVAL)


async def _resolve_latest_tweet_id(handle: str) -> Optional[str]:
    """Resolve handle → latest tweet_id via Twitter syndication (free, no auth).

    Returns tweet_id or None on rate limit / no tweets / error.
    """
    url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{handle}?showReplies=false"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=3),
                             headers={"User-Agent": "Mozilla/5.0"}) as r:
                if r.status != 200:
                    return None
                html = await r.text()
        # Tweet IDs embedded as "rest_id":"1234567890..."
        import re
        m = re.search(r'"rest_id"\s*:\s*"(\d{15,20})"', html)
        return m.group(1) if m else None
    except Exception:
        return None


async def _send_minimal_alert(kol: dict) -> bool:
    """KOL posted alert. Tries to resolve tweet_id via syndication for real
    Telegram preview; falls back to profile preview if rate-limited.
    """
    handle = kol["twitter"]
    name = kol.get("displayName") or handle
    cabal = kol.get("cabal") or "-"
    pnl = kol.get("pnl_usd") or 0
    wr = kol.get("wr") or 0

    tweet_id = await _resolve_latest_tweet_id(handle)
    if tweet_id:
        preview_url = f"https://fxtwitter.com/{handle}/status/{tweet_id}"
        preview_label = "ver tweet"
    else:
        preview_url = f"https://fxtwitter.com/{handle}"
        preview_label = "perfil"

    # Preview URL FIRST so Telegram embeds it
    msg = (
        f'<a href="{preview_url}">⚡ KOL POSTED — @{handle}</a>\n'
        f"\n"
        f"🏷 {name} · cabal: {cabal}\n"
        f"📊 Monthly PnL: ${pnl:,.0f} · WR: {wr:.0f}%\n"
        f"\n"
        f'<a href="{preview_url}">{preview_label}</a> · '
        f'<a href="https://x.com/{handle}">x.com</a>'
    )
    return await send_telegram(msg, disable_preview=False)


async def fast_processor(alert_queue: asyncio.Queue, state: dict):
    """Consumes queue (handle, kol_data) and sends minimal alert.

    Note (2026-05-12): All public Nitter instances are dead/blocked, so we
    skip the fetch entirely and send the minimal alert directly. This saves
    ~24s of timeouts per alert (3 Nitter URLs × 8s each). De-dup is done at
    the count level (fast_detect only queues on count change), so we don't
    need tweet_id tracking here.
    """
    while True:
        kol = await alert_queue.get()
        handle = kol["twitter"]
        try:
            if await _send_minimal_alert(kol):
                logger.info(f"⚡ alert (minimal) for @{handle}")
        except Exception as e:
            logger.debug(f"fast processor err: {e}")


async def fetch_latest_tweet(session: aiohttp.ClientSession,
                              handle: str) -> Optional[Dict]:
    """Fetch most recent tweet from Nitter RSS. Tries fallback instances on 403."""
    for base in NITTER_INSTANCES:
        try:
            async with session.get(
                f"{base}/{handle}/rss",
                timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT),
                headers={"User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )},
            ) as r:
                if r.status != 200:
                    continue
                xml = await r.text()
        except Exception:
            continue
        # Parse RSS — extract first <item>
        m = re.search(r"<item>(.*?)</item>", xml, re.S)
        if not m:
            return None
        item = m.group(1)
        # title is the tweet text in Nitter RSS
        title_m = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", item, re.S)
        link_m = re.search(r"<link>(.*?)</link>", item, re.S)
        guid_m = re.search(r"<guid[^>]*>(.*?)</guid>", item, re.S)
        date_m = re.search(r"<pubDate>(.*?)</pubDate>", item, re.S)
        desc_m = re.search(r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", item, re.S)
        if not (title_m and link_m):
            return None
        url = link_m.group(1).strip()
        # tweet_id = last path segment of URL like /handle/status/12345
        tid_m = re.search(r"/status/(\d+)", url)
        tid = tid_m.group(1) if tid_m else (guid_m.group(1) if guid_m else url)
        # Strip HTML from description for richer text
        text = (title_m.group(1) or "").strip()
        if desc_m:
            desc_text = re.sub(r"<[^>]+>", " ", desc_m.group(1) or "")
            desc_text = re.sub(r"\s+", " ", desc_text).strip()
            if desc_text and len(desc_text) > len(text):
                text = desc_text
        # Replace Nitter URL with x.com URL for user friendliness
        url_clean = re.sub(r"https?://nitter\.[^/]+", "https://x.com", url)
        return {
            "handle": handle,
            "tweet_id": tid,
            "text": text[:800],
            "created_at": (date_m.group(1) if date_m else "").strip(),
            "url": url_clean,

        }
    return None


async def classify_intent(session: aiohttp.ClientSession,
                            handle: str, text: str) -> Dict:
    """LLM classifies tweet intent (announcement/shill/info_share/chat)."""
    if not (HERMES_DATA_API_URL and HERMES_DATA_API_KEY) or not text.strip():
        return {}
    prompt = (
        "Classify this crypto KOL's tweet:\n"
        f"AUTHOR: @{handle}\n"
        f"TWEET: {text[:600]}\n\n"
        "Return ONLY JSON: "
        '{"intent": "announcement|shill|info_share|chat|question", '
        '"is_actionable": bool, "urgency": int 0-10, "summary": "short"}\n'
        "actionable = tweet recommends a specific token to buy NOW."
    )
    try:
        async with session.post(
            f"{HERMES_DATA_API_URL}/llm/chat",
            json={
                "model": LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 120, "temperature": 0.1,
            },
            headers={"Authorization": f"Bearer {HERMES_DATA_API_KEY}"},
            timeout=aiohttp.ClientTimeout(total=LLM_TIMEOUT),
        ) as r:
            if r.status != 200:
                return {}
            data = await r.json()
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            return {}
        # Normalize Python-style bools
        raw = re.sub(r"\bTrue\b", "true", m.group(0))
        raw = re.sub(r"\bFalse\b", "false", raw)
        return json.loads(raw)
    except Exception as e:
        logger.debug(f"classify failed: {e}")
        return {}


async def send_telegram(text: str, kb: Optional[dict] = None, disable_preview: bool = True) -> bool:
    if not (BOT_TOKEN and USER_CHAT_ID):
        return False
    payload = {"chat_id": USER_CHAT_ID, "text": text,
               "parse_mode": "HTML", "disable_web_page_preview": disable_preview}
    if kb:
        payload["reply_markup"] = kb
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json=payload, timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                return r.status == 200
    except Exception:
        return False


def _build_keyboard(ca: str, chain: str) -> dict:
    """Reuse pattern from telegram_group_listener."""
    if not ca:
        return {}
    addr_lc = ca.lower() if ca.startswith("0x") else ca
    chain_lc = (chain or "base").lower()
    if not chain_lc:
        chain_lc = "solana" if not ca.startswith("0x") else "base"
    if chain_lc in ("sol", "solana"):
        bot_user = "bonkbot_bot"
        ref = os.environ.get("BONK_BOT_REF", "")
        buy_url = (f"https://t.me/{bot_user}?start=ref_{ref}_ca_{ca}"
                   if ref else f"https://t.me/{bot_user}?start={ca}")
        buy_label = "💰 Buy (BonkBot)"
        dex_chain = "solana"
    else:
        ref = os.environ.get("BASED_BOT_REF", "agentzero")
        bot_user = os.environ.get("BASED_BOT_USER", "based_eth_bot")
        buy_url = f"https://t.me/{bot_user}?start=r_{ref}_b_{addr_lc}"
        buy_label = "💰 Buy (BasedBot)"
        dex_chain = "base"
    return {"inline_keyboard": [[
        {"text": buy_label, "url": buy_url},
        {"text": "📊 DexScreener", "url": f"https://dexscreener.com/{dex_chain}/{addr_lc if dex_chain != 'solana' else ca}"},
        {"text": "🐦 Tweet", "url": f"https://x.com/{ca}"} if False else
        {"text": "📊 DexScreener", "url": f"https://dexscreener.com/{dex_chain}/{addr_lc if dex_chain != 'solana' else ca}"},
    ]]}


async def process_tweet(session: aiohttp.ClientSession,
                          kol: Dict, tweet: Dict) -> bool:
    """Extract signals from a new tweet. Returns True if alert was fired."""
    handle = kol["twitter"]
    text = tweet.get("text") or ""
    evm = list(set(EVM_RE.findall(text)))
    sol = [a for a in set(SOL_RE.findall(text)) if _is_solana_ca(a)]
    tickers = list(set(TICKER_RE.findall(text)))


    # Skip pure chatter without any token signal
    if not (evm or sol or tickers):
        return False

    intel = await classify_intent(session, handle, text)
    intent = (intel.get("intent") or "").lower()
    actionable = bool(intel.get("is_actionable"))
    urgency = int(intel.get("urgency") or 0)
    summary = (intel.get("summary") or "")[:120]

    # Register in convergence engine — separate source per KOL
    cas = evm + sol
    if cas:
        try:
            from convergence_engine import register_signal
            for ca in cas[:2]:
                register_signal(ca, source=f"kol_twitter/{handle}",
                                score=60 + urgency, symbol=(tickers[0] if tickers else ""))
        except Exception as e:
            logger.debug(f"register_signal: {e}")

    # Only alert on actionable + (urgency>=6 or strong KOL signal)
    if not (actionable and urgency >= 6 and _budget_ok()):
        return False

    # Pick best CA (prefer EVM if both)
    ca = (evm or sol)[0] if (evm or sol) else None
    chain = "solana" if (sol and not evm) else "base"

    pnl = kol.get("pnl_usd") or 0
    wr = kol.get("wr") or 0
    cabal = kol.get("cabal") or "-"
    name = kol.get("displayName") or handle

    msg = (
        f"🎯 <b>KOL TWITTER SIGNAL</b>\n"
        f"\n"
        f"🏷 <b>@{handle}</b> ({name}) · cabal: {cabal}\n"
        f"📊 Monthly PnL: ${pnl:,.0f} · wr={wr:.0f}%\n"
        f"\n"
        f"💬 <i>{text[:300]}</i>\n"
        f"\n"
        f"intent: {intent} · urgency: {urgency}/10\n"
        f"💡 {summary}\n"
    )
    if ca:
        msg += f"\n<code>{ca}</code>"
    msg += f'\n\n<a href="{tweet.get("url") or ""}">View tweet</a>'

    kb = None
    if ca:
        kb = _build_keyboard(ca, chain)
    if await send_telegram(msg, kb):
        _budget_register()
        logger.info(f"📣 alert: @{handle} → {ca[:12] if ca else 'no-ca'} (urg={urgency})")
        return True
    return False


async def run_cycle():
    handles = load_handles()
    state = _load_state()
    last_seen = state.get("last_tweet_id", {})
    cycle_start = time.time()
    new_tweets = 0
    alerts = 0

    async with aiohttp.ClientSession() as session:
        for i in range(0, len(handles), BATCH_SIZE):
            batch = handles[i:i + BATCH_SIZE]
            results = await asyncio.gather(
                *[fetch_latest_tweet(session, k["twitter"]) for k in batch],
                return_exceptions=True,
            )
            for kol, tweet in zip(batch, results):
                if isinstance(tweet, Exception) or not tweet:
                    continue
                tid = tweet.get("tweet_id")
                if not tid:
                    continue
                prev = last_seen.get(kol["twitter"])
                if prev == tid:
                    continue
                # New tweet for this handle
                new_tweets += 1
                last_seen[kol["twitter"]] = tid
                # Skip processing if first-ever scan (warm-up)
                if not prev:
                    continue
                try:
                    fired = await process_tweet(session, kol, tweet)
                    if fired:
                        alerts += 1
                except Exception as e:
                    logger.debug(f"process {kol['twitter']}: {e}")
            await asyncio.sleep(BATCH_SLEEP)

    state["last_tweet_id"] = last_seen
    state["last_cycle"] = time.time()

    _save_state(state)
    elapsed = time.time() - cycle_start
    logger.info(f"cycle: {len(handles)} handles, {new_tweets} new tweets, "
                f"{alerts} alerts fired in {elapsed:.1f}s")


async def main():
    logger.info(
        f"KOL Twitter Monitor starting — "
        f"fast={FAST_INTERVAL}s ({FAST_HANDLES_LIMIT} top KOLs via FxTwitter), "
        f"slow={POLL_INTERVAL}s ({MAX_HANDLES if 'MAX_HANDLES' in globals() else 158} via Nitter)"
    )
    state = _load_state()
    handles = load_handles()
    alert_queue: asyncio.Queue = asyncio.Queue(maxsize=200)

    # Run fast-detect + processor + slow Nitter sweep concurrently
    async def slow_loop():
        while True:
            try:
                await run_cycle()
            except Exception as e:
                logger.error(f"slow cycle error: {e}")
            await asyncio.sleep(POLL_INTERVAL)

    await asyncio.gather(
        fast_detect_loop(handles, state, alert_queue),
        fast_processor(alert_queue, state),
        slow_loop(),
    )


if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:
        asyncio.run(run_cycle())
    else:
        asyncio.run(main())

