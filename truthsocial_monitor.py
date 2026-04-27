#!/usr/bin/env python3
"""truthsocial_monitor.py — Polls Trump's Truth Social via trumpstruth.org RSS.

Detects new posts, extracts keywords/tickers/hashtags, alerts to Telegram.
Critical for political memecoin signals (FIGHT, NICE, MAGA-themed tokens).

Polls every 30s. Detection latency: ~30-60s after Trump posts.
"""

import asyncio
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Set

import aiohttp
from dotenv import load_dotenv

load_dotenv()

# Mac standalone — no creator-bid-bot deps
# (replaced with direct Telegram bot API call below)
# heartbeat handled via systemd Watchdog

try:
    import sdnotify
    _sd = sdnotify.SystemdNotifier()
except ImportError:
    _sd = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("/home/ubuntu/creator-bid-bot/truthsocial_monitor.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("truthsocial")

FEED_URL = "https://trumpstruth.org/feed"
POLL_INTERVAL = 30  # seconds
SEEN_FILE = "/home/ubuntu/.truthsocial_seen.txt"
MAX_SEEN = 200  # cap memory of seen IDs

# Keywords that signal potential memecoin pump catalysts
NARRATIVE_KEYWORDS = {
    "MAGA": ["maga", "make america great", "america first"],
    "FIGHT": ["fight", "fight fight fight"],
    "ICE": ["ice", "immigration", "deport", "border", "wall"],
    "TARIFF": ["tariff", "china", "trade war"],
    "MEDIA": ["fake news", "msnbc", "cnn", "msm"],
    "RUSSIA": ["putin", "russia", "ukraine"],
    "ELECTION": ["election", "vote", "ballot", "fraud"],
    "DOGE": ["doge", "elon", "musk"],
}




# ─── Direct Telegram send (no notification_service dep) ─────────────────────
HERMES_BOT_TOKEN = os.getenv("HERMES_TELEGRAM_BOT_TOKEN", "")
HERMES_CHAT_ID = os.getenv("HERMES_USER_CHAT_ID", "")

async def _send_tg(session: aiohttp.ClientSession, msg: str,
                    keyboard=None, parse_mode="HTML") -> bool:
    if not HERMES_BOT_TOKEN or not HERMES_CHAT_ID:
        return False
    payload = {"chat_id": HERMES_CHAT_ID, "text": msg,
               "parse_mode": parse_mode, "disable_web_page_preview": False}
    if keyboard:
        payload["reply_markup"] = keyboard
    url = f"https://api.telegram.org/bot{HERMES_BOT_TOKEN}/sendMessage"
    try:
        async with session.post(url, json=payload,
                                 timeout=aiohttp.ClientTimeout(total=10)) as r:
            return r.status == 200
    except Exception as e:
        logger.warning(f"send_tg failed: {e}")
        return False

def load_seen() -> Set[str]:
    try:
        with open(SEEN_FILE) as f:
            return set(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        return set()


def save_seen(seen: Set[str]) -> None:
    try:
        items = list(seen)[-MAX_SEEN:]
        with open(SEEN_FILE, "w") as f:
            f.write("\n".join(items))
    except OSError as e:
        logger.warning(f"save_seen failed: {e}")


_TICKER_RE = re.compile(r"\$([A-Z]{2,10})\b")
_HASHTAG_RE = re.compile(r"#(\w+)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(s: str) -> str:
    return _HTML_TAG_RE.sub(" ", s).strip()


def detect_narratives(text: str) -> List[str]:
    text_l = text.lower()
    hits = []
    for narrative, keywords in NARRATIVE_KEYWORDS.items():
        if any(kw in text_l for kw in keywords):
            hits.append(narrative)
    return hits


# ─── Cross-reference: search candidate tokens for matching ticker/narrative ──
async def search_candidate_tokens(session: aiohttp.ClientSession,
                                    query: str, max_results: int = 5) -> List[Dict]:
    """DexScreener search → return tokens matching the ticker/narrative.

    Sorted by: liquidity DESC (most liquid first), then age DESC (older first).
    Older + liquid = the dormant-mega-narrative pattern target.
    """
    url = f"https://api.dexscreener.com/latest/dex/search?q={query}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
            data = await r.json()
    except Exception as e:
        logger.debug(f"DexScreener search failed: {e}")
        return []
    pairs = data.get("pairs") or []
    # Filter: exact symbol match, chain in [eth, base, solana]
    qu = query.upper().lstrip("$")
    candidates = []
    for p in pairs:
        sym = (p.get("baseToken", {}).get("symbol") or "").upper()
        chain = p.get("chainId", "")
        if sym != qu:
            continue
        if chain not in ("ethereum", "base", "solana", "bsc"):
            continue
        candidates.append({
            "address": p.get("baseToken", {}).get("address"),
            "symbol": sym,
            "name": p.get("baseToken", {}).get("name"),
            "chain": chain,
            "mcap": float(p.get("marketCap") or p.get("fdv") or 0),
            "liquidity": float(p.get("liquidity", {}).get("usd") or 0),
            "volume_24h": float(p.get("volume", {}).get("h24") or 0),
            "price_change_24h": float(p.get("priceChange", {}).get("h24") or 0),
            "pair_created_at": int(p.get("pairCreatedAt", 0)),
            "url": f"https://dexscreener.com/{chain}/{p.get('baseToken', {}).get('address')}",
        })
    # Sort: highest liquidity first; for equal liquidity, oldest first (dormant pattern)
    candidates.sort(key=lambda c: (-c["liquidity"], c["pair_created_at"]))
    return candidates[:max_results]


def parse_feed(xml_text: str) -> List[Dict]:
    """Returns list of {id, title, text, link, original_url, pub_date}."""
    out = []
    try:
        root = ET.fromstring(xml_text)
        for item in root.iter("item"):
            entry = {}
            for child in item:
                tag = child.tag.split("}")[-1]
                text = (child.text or "").strip() if child.text else ""
                if tag == "title":
                    entry["title"] = text
                elif tag == "link":
                    entry["link"] = text
                elif tag == "description":
                    entry["text"] = strip_html(text)
                elif tag == "guid":
                    entry["id"] = text
                elif tag == "pubDate":
                    entry["pub_date"] = text
                elif tag == "originalUrl":
                    entry["original_url"] = text
                elif tag == "originalId":
                    entry["original_id"] = text
            if entry.get("id"):
                out.append(entry)
    except ET.ParseError as e:
        logger.warning(f"feed parse failed: {e}")
    return out


async def alert_post(post: Dict) -> None:
    text = post.get("text", "")[:400]
    title = post.get("title", "")[:120]
    narratives = detect_narratives(text + " " + title)
    tickers = list(set(_TICKER_RE.findall(text + " " + title)))
    hashtags = list(set(_HASHTAG_RE.findall(text + " " + title)))

    lines = ["🇺🇸 <b>TRUMP POSTED ON TRUTH SOCIAL</b>", ""]
    lines.append(f"💬 {title}" if title else "")
    if text and text != title:
        lines.append(f"📄 {text}")
    if narratives:
        lines.append("")
        lines.append(f"🎯 <b>Narratives:</b> {', '.join(narratives)}")
    if tickers:
        lines.append(f"🪙 <b>Tickers:</b> ${', $'.join(tickers)}")
    if hashtags:
        lines.append(f"#️⃣ {' '.join(['#' + h for h in hashtags])}")

    # Build search candidates list (tickers + narrative names if no explicit tickers)
    search_terms = list(tickers)
    if not tickers and narratives:
        search_terms = narratives[:2]  # search top 2 narratives (e.g. "MAGA", "FIGHT")

    # Cross-reference: search DexScreener for matching tokens, run pump_forensics on best
    auto_match = None
    async with aiohttp.ClientSession() as s:
        for term in search_terms[:3]:
            candidates = await search_candidate_tokens(s, term)
            if candidates:
                top = candidates[0]
                # Run pump_forensics on top candidate (oldest+liquid)
                try:
                    from pump_forensics import extract_pump_anatomy, _simulate_hermes_decision
                    anatomy = await extract_pump_anatomy(top["address"], top["chain"], s)
                    decision = anatomy.get("hermes_would_alert") or _simulate_hermes_decision(anatomy)
                    auto_match = {"term": term, "candidate": top, "decision": decision}
                    break
                except Exception as e:
                    logger.debug(f"pump_forensics failed for {top['address']}: {e}")

    if auto_match:
        c = auto_match["candidate"]
        d = auto_match["decision"]
        action_emoji = {"ALERT": "🚨", "WATCH": "👀", "SKIP": "🚫"}.get(d.get("action"), "❓")
        lines.append("")
        lines.append(f"━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"🤖 <b>AUTO-MATCH</b> for '{auto_match['term']}'")
        lines.append(f"{action_emoji} <b>{d.get('action')}</b> score={d.get('score')}/100")
        lines.append(f"🪙 <b>${c['symbol']}</b> ({c['chain']})")
        lines.append(f"<code>{c['address']}</code>")
        lines.append(f"💰 mcap=${c['mcap']:,.0f}  liq=${c['liquidity']:,.0f}  vol24h=${c['volume_24h']:,.0f}")
        lines.append(f"📈 24h: {c['price_change_24h']:.1f}%")

    lines.append("")
    lines.append(f"⏰ {post.get('pub_date', '')}")
    if post.get("original_url"):
        lines.append(f'🔗 <a href="{post["original_url"]}">Open Truth Social</a>')

    msg = "\n".join(filter(None, lines))

    keyboard = None
    if auto_match:
        c = auto_match["candidate"]
        addr_l = c["address"].lower()
        keyboard = {"inline_keyboard": [
            [
                {"text": "Fred", "url": f"https://t.me/alertfriendbot?start={addr_l}"},
                {"text": "📈 DexScreener", "url": c["url"]},
            ],
            [
                {"text": "🔍 Truth Social", "url": post.get("original_url", FEED_URL)},
            ],
        ]}
    elif tickers:
        keyboard = {"inline_keyboard": [[
            {"text": f"📈 ${tickers[0]} on DexScreener",
             "url": f"https://dexscreener.com/search?q=${tickers[0]}"},
            {"text": "🔍 Truth Social", "url": post.get("original_url", FEED_URL)},
        ]]}

    async with aiohttp.ClientSession() as s:
        ok = await _send_tg(s, msg, keyboard=keyboard)
    log_extras = []
    if narratives: log_extras.append(f"narratives={narratives}")
    if tickers: log_extras.append(f"tickers={tickers}")
    logger.info(f"ALERT sent: id={post.get('id')[:50]}... {' '.join(log_extras)}" if ok
                else f"alert FAILED for {post.get('id')}")


async def watchdog_pulse() -> None:
    """systemd Watchdog + heartbeat file."""
    if _sd:
        _sd.notify("READY=1")
    while True:
        if _sd:
            _sd.notify("WATCHDOG=1")
        # heartbeat via systemd Watchdog only
        await asyncio.sleep(20)


async def run_monitor() -> None:
    """Poll feed, detect new posts, alert."""
    asyncio.create_task(watchdog_pulse())
    seen = load_seen()
    logger.info(f"Loaded {len(seen)} seen IDs from disk")
    logger.info(f"Polling {FEED_URL} every {POLL_INTERVAL}s")

    backoff = 1
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(FEED_URL,
                                        headers={"User-Agent": "TruthSocialMonitor/1.0"},
                                        timeout=aiohttp.ClientTimeout(total=15)) as r:
                    text = await r.text()
                posts = parse_feed(text)
                new_posts = [p for p in posts if p["id"] not in seen]
                if new_posts:
                    # Process oldest first so chronological order
                    for post in reversed(new_posts):
                        logger.info(f"NEW post: {post.get('title', '')[:80]}")
                        await alert_post(post)
                        seen.add(post["id"])
                    save_seen(seen)
                backoff = 1
            except Exception as e:
                logger.warning(f"poll error: {e} — retry in {backoff}s")
                await asyncio.sleep(backoff)
                backoff = min(60, backoff * 2)
                continue
            await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    logger.info("Truth Social monitor starting (Trump-only via trumpstruth.org)")
    asyncio.run(run_monitor())
