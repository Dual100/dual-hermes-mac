"""Hermes Telegram group listener — Mac-side, real-time via Telethon.

Listens to messages in groups listed in monitored_groups.json. For each message:
  - Extract tickers ($SYMBOL), contract addresses (EVM 0x..., Solana base58)
  - For each contract found, run pump_forensics investigation
  - Score >= ALERT_THRESHOLD → send alert to user via Hermes Telegram bot
  - Cooldown per token to avoid spam

Config: monitored_groups.json — list of dialog IDs to listen to.
"""
import asyncio
import html
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, Set

import aiohttp
from telethon import TelegramClient, events

logger = logging.getLogger("hermes.tg_listener")

EVM_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
SOL_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
TICKER_RE = re.compile(r"\$([A-Z][A-Z0-9]{1,10})\b")

ALERT_THRESHOLD = int(os.environ.get("HERMES_ALERT_THRESHOLD", "75"))  # GEM ONLY mode (was 45)
GEM_MODE = os.environ.get("HERMES_GEM_MODE", "1") == "1"
GEM_DAILY_CAP = int(os.environ.get("HERMES_GEM_DAILY_CAP", "10"))
COOLDOWN_SEC = int(os.environ.get("HERMES_TOKEN_COOLDOWN_SEC", "1800"))
# Per-source mcap caps. Source label decides limit. None = unlimited.
SOURCE_MCAP_CAPS = {
    "tg_group": int(os.environ.get("HERMES_TG_GROUP_MAX_MCAP", "5000000")),  # $5M
    "twitter_mega": None,         # Elon/CZ — any cap
    "smart_money": None,          # KOL buy — any cap
    "truthsocial": None,           # Trump — any cap
    "convergence": None,           # 2+ sources — any cap
}
DEFAULT_MCAP_CAP = int(os.environ.get("HERMES_MAX_MCAP_FOR_ALERT", "500000000"))
HERMES_DATA_API_URL = os.environ.get("HERMES_DATA_API_URL", "").rstrip("/")
HERMES_DATA_API_KEY = os.environ.get("HERMES_DATA_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "kimi-k2.5")

GROUPS_FILE = Path("data/monitored_groups.json")
COOLDOWN_FILE = Path("data/listener_cooldowns.json")

_last_seen: Dict[str, float] = {}

# Gem-mode daily counter
_gem_alerts_today: Dict[str, int] = {"date": "", "count": 0}


def _gem_quality_check(anatomy: dict, source: str = "") -> tuple:
    """Returns (passes, reason).

    Different bar for MEMECOIN vs PROJECT:
      - MEMECOIN: needs HYPE (MEGA tweet, celebrity post, smart money buy, narrative explosion)
      - PROJECT (Virtuals/Butler-tier): needs trust signals (team follows, dev history, fundamentals)
    """
    sig_strs = []
    score = (anatomy.get("_decision") or {}).get("score", 0)
    mcap = anatomy.get("current_mcap") or 0
    holders = anatomy.get("holder_count") or 0
    pct1 = anatomy.get("price_change_1h") or 0
    pct24 = anatomy.get("price_change_24h") or 0
    token_type = anatomy.get("_token_type", "memecoin")

    # (a) Top-tier score auto-qualifies — exceptional quality
    if score >= 85:
        sig_strs.append(f"TOP_SCORE({score})")

    # MEMECOIN HYPE PATH — celebrity/MEGA posted = explosive setup
    mega_search = (anatomy.get("_twitter_search") or {}).get("mega_mentions", []) or []
    mega_mention = anatomy.get("_mega_mention") or {}
    has_mega = bool(mega_search) or bool(mega_mention.get("found"))
    if token_type == "memecoin":
        if has_mega:
            mega_handles = ([m["handle"] for m in mega_search[:2]]
                            + [m["handle"] for m in (mega_mention.get("handles") or [])[:2]])
            sig_strs.append(f"🔥 MEMECOIN+HYPE (MEGA: {','.join(set(mega_handles))[:40]})")
        # Pumping memecoin (>50% in 1h or >300% in 24h) = explosive momentum
        if pct1 > 50 or pct24 > 300:
            sig_strs.append(f"📈 MEMECOIN_PUMPING (1h={pct1:.0f}%, 24h={pct24:.0f}%)")
    # PROJECT trust path (Butler) — Virtuals ecosystem with team backing
    deep = anatomy.get("_deep_profile") or {}
    if token_type == "project":
        tw = deep.get("trust_weight", 0)
        if tw >= 30:
            sig_strs.append(f"🏗️ PROJECT_TRUST({tw}/100)")
        v_d = anatomy.get("_virtuals_deep") or {}
        if v_d.get("followed_by_virtuals"):
            sig_strs.append("✓ followed by @virtuals_io")

    sm = anatomy.get("_smart_money") or {}
    if sm.get("smart_buyers") or sm.get("smart_count", 0) > 0:
        sig_strs.append(f"smart_money({sm.get('smart_count', 1)})")
    if "twitter/HOT" in source or "smart_money_active" in source:
        sig_strs.append("MEGA_source")
    tw = anatomy.get("_twitter_context") or {}
    if tw.get("verified") and (tw.get("followers") or 0) >= 100_000:
        sig_strs.append(f"verified+{tw['followers']//1000}K")
    primary = anatomy.get("_primary") or {}
    narrative = anatomy.get("_narrative") or {}
    if primary.get("is_primary") and narrative.get("stage") in ("EMERGING", "GROWING"):
        sig_strs.append(f"PRIMARY+{narrative['stage']}")
    mega_m = anatomy.get("_mega_mention") or {}
    if mega_m.get("found"):
        handles = [m["handle"] for m in mega_m.get("handles", [])[:2]]
        sig_strs.append(f"MEGA_TWITTER({','.join(handles)})")

    # (c) Decent score + healthy fundamentals (early gem signal)
    if not sig_strs and score >= 75 and mcap < 1_000_000 and holders >= 100 and pct1 > 10:
        sig_strs.append(f"HEALTHY_GEM(score={score}, mcap={mcap:,.0f}, holders={holders}, 1h={pct1:.0f}%)")

    if sig_strs:
        return True, " + ".join(sig_strs)
    return False, "no_big_signal"


def _gem_daily_check() -> bool:
    """Returns True if under daily cap."""
    import datetime as _dt
    today = _dt.date.today().isoformat()
    if _gem_alerts_today["date"] != today:
        _gem_alerts_today["date"] = today
        _gem_alerts_today["count"] = 0
    return _gem_alerts_today["count"] < GEM_DAILY_CAP


def _gem_register_alert():
    _gem_alerts_today["count"] += 1


def _load_cooldowns() -> Dict[str, float]:
    if COOLDOWN_FILE.exists():
        try:
            return json.loads(COOLDOWN_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _save_cooldowns(d: Dict[str, float]) -> None:
    COOLDOWN_FILE.parent.mkdir(exist_ok=True)
    COOLDOWN_FILE.write_text(json.dumps(d))


def load_monitored_groups() -> Set[int]:
    if not GROUPS_FILE.exists():
        return set()
    try:
        data = json.loads(GROUPS_FILE.read_text())
        return {int(x) for x in data.get("group_ids", [])}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"Could not load {GROUPS_FILE}: {e}")
        return set()


async def _send_convergence_alert(addr: str, anatomy: dict, conv: dict,
                                    bot_token: str, user_chat_id: int) -> None:
    """Send the special CONVERGENCE alert when 2+ unique sources fire on same token."""
    sym = anatomy.get("symbol") or "?"
    name = anatomy.get("name") or sym
    chain = (anatomy.get("chain") or "ethereum").upper()
    mcap = anatomy.get("current_mcap") or 0
    pct1 = anatomy.get("price_change_1h") or 0
    pct24 = anatomy.get("price_change_24h") or 0
    tier = conv.get("tier", "DOUBLE")
    sources = conv.get("sources", [])
    boost = conv.get("boost", 20)
    emoji = "🎯🎯🎯" if tier == "TRIPLE" else "🎯🎯"
    bar = "═" * 28
    lines = [
        f"{emoji} <b>{tier} CONVERGENCE</b>  +{boost} score boost",
        bar,
        f"<b>{html.escape(name)}</b>",
        f"<b>${html.escape(sym)}</b>  ·  <i>{chain}</i>",
        f"<code>{addr}</code>",
        "",
        f"📡 Visto em <b>{conv['source_count']} fontes</b> nas últimas 1h:",
    ]
    for s in sources:
        emoji_src = {"tg": "💬", "twitter": "🐦", "truthsocial": "🇺🇸",
                     "smart_money_active": "🐋"}.get(s, "📡")
        lines.append(f"  {emoji_src} {html.escape(s)}")
    lines += [
        "",
        f"MCap: {_fmt_usd(mcap)}",
        f"📊 1h: {_fmt_pct(pct1)}  ·  24h: {_fmt_pct(pct24)}",
    ]
    kb = _build_keyboard(addr, anatomy.get("chain") or "ethereum")
    await send_alert(bot_token, user_chat_id, "\n".join(lines), keyboard=kb)
    logger.info(f"  🎯 CONVERGENCE {tier} alert: {addr} sources={sources}")
    try:
        from outcome_tracker import record_alert
        record_alert(token=addr, chain=anatomy.get("chain") or "ethereum",
                     symbol=sym, score=anatomy.get("_decision", {}).get("score", 0) + boost,
                     action=f"CONVERGENCE_{tier}",
                     source=f"convergence/{tier}/{','.join(sources)}",
                     mcap=mcap, price=anatomy.get("current_price") or 0)
    except Exception:
        pass


async def send_alert(bot_token: str, chat_id: int, text: str, keyboard: dict | None = None) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    if keyboard:
        payload["reply_markup"] = json.dumps(keyboard)
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, data=payload, timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status != 200:
                    logger.warning(f"Telegram alert failed status={r.status}: {await r.text()}")
        except Exception as e:
            logger.error(f"send_alert: {e}")


def _build_keyboard(addr: str, chain: str) -> dict:
    addr = (addr or "").lower()
    if not addr:
        return {}
    chain_lc = (chain or "ethereum").lower()
    dex_chain = {"ethereum": "ethereum", "eth": "ethereum", "base": "base",
                 "solana": "solana", "sol": "solana", "bsc": "bsc"}.get(chain_lc, chain_lc)
    BASED_REF = os.environ.get("BASED_BOT_REF", "agentzero")
    BASED_BOT_USER = os.environ.get("BASED_BOT_USER", "based_eth_bot")
    buy = {"text": "💰 Buy (BasedBot)",
           "url": f"https://t.me/{BASED_BOT_USER}?start=r_{BASED_REF}_b_{addr}"}
    dex = {"text": "📈 DexScreener", "url": f"https://dexscreener.com/{dex_chain}/{addr}"}
    return {"inline_keyboard": [[buy, dex]]}


def _classify_token_type(anatomy: dict) -> str:
    """Classify token as 'project' or 'memecoin' based on signals.

    PROJECT (real product, dev team, utility):
      - Has website AND it's not just landing page (>1 website OR website with docs)
      - Twitter account >180d old AND >5K followers
      - Multi-word descriptive name (not just emoji/meme word)
      - Listed on legit platform (Virtuals/OpenClaw with active dev)

    MEMECOIN (narrative-driven, no real product):
      - Single word/emoji name
      - Twitter <30d old or no twitter
      - No website OR landing only
      - Pumped on narrative (mentions, hype)

    For PROJECTS: same ticker on diff chain = likely DIFFERENT PROJECT (no penalty)
    For MEMECOINS: same ticker = likely COPYCAT (penalty)
    """
    websites = anatomy.get("_websites") or []
    twitter = anatomy.get("twitter_handle") or ""
    tw_ctx = anatomy.get("_twitter_context") or {}
    name = (anatomy.get("name") or "").strip()
    is_virtuals = (anatomy.get("_virtuals_deep") or {}).get("is_virtuals", False)
    v_d = anatomy.get("_virtuals_deep") or {}

    project_score = 0
    if len(websites) >= 1:
        project_score += 2
    if len(websites) >= 2:
        project_score += 2
    if tw_ctx.get("age_days", 0) > 180:
        project_score += 2
    if tw_ctx.get("followers", 0) >= 10_000:
        project_score += 2
    if tw_ctx.get("verified"):
        project_score += 2
    # Multi-word descriptive name (not "PEPE" or "DOGE2")
    if name and len(name.split()) >= 2 and len(name) >= 6:
        project_score += 1
    # Virtuals/OpenClaw with established dev
    if is_virtuals and (v_d.get("dev_history_count") or 0) >= 2:
        project_score += 3
    # Butler deep profile trust_weight is the strongest signal
    deep = anatomy.get("_deep_profile") or {}
    tw = deep.get("trust_weight", 0)
    if tw >= 50:
        project_score += 8  # Elite creator → real project
    elif tw >= 30:
        project_score += 5
    elif tw >= 15:
        project_score += 3
    if deep.get("followed_by_virtuals"):
        project_score += 3
    if (deep.get("team_followers") or []) and len(deep.get("team_followers", [])) >= 1:
        project_score += 5  # Followed by Virtuals core team

    return "project" if project_score >= 5 else "memecoin"


async def _check_primary_token(symbol: str, current_addr: str, current_chain: str,
                                  current_pair_created_at: int, current_name: str,
                                  current_twitter: str,
                                  session: aiohttp.ClientSession) -> dict:
    """Search DexScreener for tokens with same symbol — but only count as COPYCAT
    if name OR twitter handle ALSO matches. Different projects sharing a ticker
    are NOT copycats (e.g. $CAS Caspius on Base != $CAS some other token on BSC).

    Returns {
        is_primary, copycats_count (true copycats only),
        same_symbol_diff_project_count (separate metric),
        oldest_addr, oldest_chain, oldest_age_days,
    }
    """
    try:
        async with session.get(
            "https://api.dexscreener.com/latest/dex/search",
            params={"q": symbol.lstrip("$").strip()},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            if r.status != 200:
                return {}
            data = await r.json()
        pairs = data.get("pairs") or []
        matches = [
            p for p in pairs
            if (p.get("baseToken", {}).get("symbol") or "").upper() == symbol.upper()
            and p.get("pairCreatedAt")
        ]
        if not matches:
            return {}
        by_token: Dict[str, dict] = {}
        for p in matches:
            key = f"{p.get('chainId','')}_{(p.get('baseToken',{}).get('address') or '').lower()}"
            existing = by_token.get(key)
            if not existing or (p.get("liquidity", {}).get("usd") or 0) > (existing.get("liquidity", {}).get("usd") or 0):
                by_token[key] = p
        unique = list(by_token.values())

        # Distinguish: same name OR same twitter = same project (copycat detection valid)
        # Different name AND different twitter = different project (just shares ticker)
        cur_name_norm = (current_name or "").strip().lower()
        cur_tw_norm = (current_twitter or "").lstrip("@").lower()

        def _same_project(p) -> bool:
            other_name = (p.get("baseToken", {}).get("name") or "").strip().lower()
            other_tw = ""
            for s in (p.get("info", {}).get("socials") or []):
                if s.get("type") == "twitter":
                    other_tw = (s.get("url") or "").rstrip("/").split("/")[-1].lstrip("@").lower()
            if cur_name_norm and cur_name_norm == other_name:
                return True
            if cur_tw_norm and cur_tw_norm == other_tw:
                return True
            return False

        same_project = [p for p in unique if _same_project(p)]
        diff_project = [p for p in unique if not _same_project(p)]

        # Primary check: only against SAME PROJECT versions
        if same_project:
            same_project.sort(key=lambda p: p.get("pairCreatedAt", 0))
            oldest = same_project[0]
            oldest_addr = (oldest.get("baseToken", {}).get("address") or "").lower()
            oldest_chain = oldest.get("chainId", "")
            oldest_created = oldest.get("pairCreatedAt", 0)
            is_primary = (oldest_addr == (current_addr or "").lower()
                          and oldest_chain.lower() == (current_chain or "").lower())
            oldest_age_days = (time.time() * 1000 - oldest_created) / 86400000 if oldest_created else 0
            return {
                "is_primary": is_primary,
                "copycats_count": max(0, len(same_project) - 1),
                "same_symbol_diff_project_count": len(diff_project),
                "oldest_addr": oldest_addr,
                "oldest_chain": oldest_chain,
                "oldest_age_days": oldest_age_days,
                "current_age_days": (time.time() * 1000 - (current_pair_created_at or 0)) / 86400000 if current_pair_created_at else 0,
            }
        # No same-project matches found — current is unique within its project
        return {
            "is_primary": True,
            "copycats_count": 0,
            "same_symbol_diff_project_count": len(diff_project),
            "current_age_days": (time.time() * 1000 - (current_pair_created_at or 0)) / 86400000 if current_pair_created_at else 0,
        }
    except Exception as e:
        logger.debug(f"primary check failed for ${symbol}: {e}")
        return {}


_DEEP_PROFILE_CACHE: Dict[str, tuple] = {}  # handle -> (expires, profile)


async def _twitter_deep_profile(handle: str, session: aiohttp.ClientSession,
                                  is_virtuals: bool = True) -> dict:
    """Fetch Butler's full trust_weight (0-100) via Hermes Data API.

    Skip entirely if not Virtuals (saves Sorsa quota — Butler is virtuals-specific).
    Cache 24h locally per handle to reduce duplicate calls.
    """
    if not handle or not is_virtuals:
        return {}
    if not _sorsa_available():
        return {}
    cache = _DEEP_PROFILE_CACHE.get(handle.lower())
    if cache and cache[0] > time.time():
        return cache[1] or {}
    api_url = os.environ.get("HERMES_DATA_API_URL", "").rstrip("/")
    api_key = os.environ.get("HERMES_DATA_API_KEY", "")
    if not (api_url and api_key):
        return {}
    try:
        async with session.get(f"{api_url}/twitter/deep-profile",
                               params={"handle": handle},
                               headers={"Authorization": f"Bearer {api_key}"},
                               timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                return {}
            data = await r.json()
            if data.get("trust_weight") is not None:
                _DEEP_PROFILE_CACHE[handle.lower()] = (time.time() + 86400, data)
            return data
    except Exception as e:
        logger.debug(f"deep_profile {handle}: {e}")
        return {}


async def _virtuals_deep_check(address: str, session: aiohttp.ClientSession) -> dict:
    """For Virtuals tokens — fetch creator/dev wallet, team check, history.

    Pulls signals from Hermes Data API (same data Butler monitor uses on Hetzner):
      - /virtuals/token: token metadata, creator wallet, twitter
      - /investigations/developer: red flags, past projects
      - /creators/by-wallet: history of past launches by this dev
      - /creators/is-farmer: known farmer/spammer flag
      - /butler/launch: existing Butler-side analysis if any
    """
    api_url = os.environ.get("HERMES_DATA_API_URL", "").rstrip("/")
    api_key = os.environ.get("HERMES_DATA_API_KEY", "")
    if not (api_url and api_key):
        return {}
    headers = {"Authorization": f"Bearer {api_key}"}
    out = {}
    try:
        async with session.get(f"{api_url}/virtuals/token", params={"address": address},
                               headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status == 200:
                data = await r.json()
                # /virtuals/token returns {address, found, token} — `address` is the
                # echoed input, NOT proof of Virtuals membership. Use `found` + `token`,
                # and exclude Clanker tokens (same DB, different platform).
                tok = data.get("token") or {}
                factory = (tok.get("factory") or "").lower()
                is_clanker = factory == "clanker"
                if data.get("found") and tok and not is_clanker:
                    out["is_virtuals"] = True
                    out["creator_wallet"] = tok.get("creator_wallet") or tok.get("ownerAddress")
                    out["twitter"] = tok.get("twitter") or tok.get("twitterHandle") or tok.get("creator_twitter")
                    out["is_openclaw"] = (tok.get("cluster") or "").upper() == "OPENCLAW"
                    out["holder_count"] = tok.get("holder_count") or tok.get("holderCount")
    except Exception:
        pass
    if not out.get("is_virtuals"):
        return {}

    boost, reasons = 0, []
    creator = out.get("creator_wallet")
    if creator:
        try:
            async with session.get(f"{api_url}/creators/is-farmer",
                                   params={"wallet": creator},
                                   headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    d = await r.json()
                    if d.get("is_farmer"):
                        boost -= 25
                        reasons.append(f"-25 dev is FARMER ({d.get('farm_count', '?')} prior launches)")
                    out["dev_is_farmer"] = d.get("is_farmer", False)
                    out["dev_farm_count"] = d.get("farm_count", 0)
        except Exception:
            pass
        try:
            async with session.get(f"{api_url}/creators/history",
                                   params={"wallet": creator},
                                   headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    d = await r.json()
                    history = d.get("projects") or []
                    out["dev_history_count"] = len(history)
                    if len(history) >= 3:
                        boost += 8
                        reasons.append(f"+8 dev has {len(history)} past projects")
                    elif len(history) >= 1:
                        boost += 3
                        reasons.append(f"+3 dev has {len(history)} prior project")
        except Exception:
            pass
        try:
            async with session.get(f"{api_url}/investigations/red-flags-by-wallet",
                                   params={"wallet": creator},
                                   headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    d = await r.json()
                    flags = d.get("red_flags") or []
                    if flags:
                        boost -= 15
                        reasons.append(f"-15 dev has {len(flags)} red flags: {', '.join(f.get('flag','?') for f in flags[:3])}")
        except Exception:
            pass

    twitter = out.get("twitter")
    if twitter:
        # Cross-ref Sorsa: is twitter followed by Virtuals team?
        try:
            sorsa_key = os.environ.get("TWEETSCOUT_API_KEY", "").strip('"')
            if sorsa_key:
                # check-follow against virtuals_io
                async with session.post("https://api.sorsa.io/v3/check-follow",
                                        headers={"ApiKey": sorsa_key, "Content-Type": "application/json"},
                                        json={"username_1": "virtuals_io", "username_2": twitter},
                                        timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        d = await r.json()
                        if d.get("follow"):
                            boost += 12
                            reasons.append(f"+12 @{twitter} is followed by @virtuals_io official")
                            out["followed_by_virtuals"] = True
        except Exception:
            pass

    out["score_boost"] = boost
    out["reasons"] = reasons
    return out


async def _detect_uniswap_version(address: str, chain: str, session: aiohttp.ClientSession) -> dict:
    """Detect which Uniswap version the token's main pool runs on.

    V2 = good (no perpetual fees to deployer/platform)
    V3/V4 = bad (deployer earns fees forever via concentrated liquidity)
    Returns {version: 'v2'|'v3'|'v4'|'other', score_boost, reasons}
    """
    if chain.lower() not in ("ethereum", "eth", "base"):
        return {}
    try:
        async with session.get(f"https://api.dexscreener.com/latest/dex/tokens/{address}",
                               timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status != 200:
                return {}
            data = await r.json()
        pairs = data.get("pairs") or []
        if not pairs:
            return {}
        # Pick highest-liquidity pair
        pairs.sort(key=lambda p: -(p.get("liquidity", {}).get("usd") or 0))
        p = pairs[0]
        dex_id = (p.get("dexId") or "").lower()
        labels = [str(l).lower() for l in (p.get("labels") or [])]
        version = "other"
        if "uniswap" in dex_id:
            if any("v4" in l for l in labels):
                version = "v4"
            elif any("v3" in l for l in labels):
                version = "v3"
            elif any("v2" in l for l in labels) or "v2" in dex_id:
                version = "v2"
            else:
                version = "v3"  # default for uniswap if unspecified
        boost, reasons = 0, []
        if version == "v2":
            boost = 12
            reasons.append("+12 Uniswap V2 (no perpetual fees)")
        elif version == "v4":
            boost = -15
            reasons.append("-15 Uniswap V4 (perpetual fees to deployer)")
        elif version == "v3":
            boost = -8
            reasons.append("-8 Uniswap V3 (perpetual fees to deployer)")
        return {
            "version": version, "dex_id": dex_id,
            "score_boost": boost, "reasons": reasons,
        }
    except Exception as e:
        logger.debug(f"uniswap_version {address}: {e}")
        return {}


_MEGA_MENTION_CACHE: Dict[str, tuple] = {}  # key -> (expires, result)


_TWITTER_SEARCH_CACHE: Dict[str, tuple] = {}  # key -> (expires, result)

# Global Sorsa rate-limit state — backs off when 403 hit
_SORSA_BACKOFF_UNTIL: float = 0.0


def _sorsa_available() -> bool:
    return time.time() >= _SORSA_BACKOFF_UNTIL


def _mark_sorsa_rate_limited(seconds: int = 1800):
    global _SORSA_BACKOFF_UNTIL
    _SORSA_BACKOFF_UNTIL = time.time() + seconds
    logger.warning(f"Sorsa rate limited — backing off for {seconds}s")


async def _twitter_full_search(symbol: str, address: str, name: str,
                                 dev_wallet: str, session: aiohttp.ClientSession) -> dict:
    """Comprehensive Twitter search: address + ticker + name + dev wallet.

    Returns:
      total_mentions, unique_authors, top_shillers, sample_tweets,
      mega_mentions, score_boost, reasons
    """
    api_key = os.environ.get("TWEETSCOUT_API_KEY", "").strip('"')
    if not api_key:
        return {}
    if not _sorsa_available():
        return {"rate_limited": True, "score_boost": 0, "reasons": [], "sources_hit": []}
    cache_key = f"{(address or '').lower()}|{(symbol or '').upper()}|{(name or '').lower()}"
    cached = _TWITTER_SEARCH_CACHE.get(cache_key)
    if cached and cached[0] > time.time():
        return cached[1] or {}

    queries = []
    if address:
        queries.append(("address", address))
    if symbol:
        queries.append(("ticker", f"${symbol}"))
    # Search by name when distinct (any length 4+) — user wants ALL searches enabled
    if name and name.lower() != (symbol or "").lower() and len(name) >= 4:
        queries.append(("name", name))
    if dev_wallet:
        queries.append(("dev", dev_wallet))
    if not queries:
        return {}

    mega_handles = _load_mega_handles()
    all_tweets = []
    seen_ids = set()
    sources_hit = []

    rate_limited = False
    async def _search(label, q):
        nonlocal rate_limited
        # Use /mentions with min_likes=5 for ticker/name (high bot spam).
        # For address/dev queries, every organic mention matters — keep /search-tweets.
        use_mentions = label in ("ticker", "name")
        endpoint = "/mentions" if use_mentions else "/search-tweets"
        body = {"query": q, "order": "latest"}
        if use_mentions:
            body["min_likes"] = 5
        try:
            async with session.post(f"https://api.sorsa.io/v3{endpoint}",
                                    headers={"ApiKey": api_key,
                                             "Content-Type": "application/json"},
                                    json=body,
                                    timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status == 403 or r.status == 429:
                    rate_limited = True
                    _mark_sorsa_rate_limited(1800)  # 30min backoff
                    return []
                if r.status != 200:
                    return []
                data = await r.json()
                tweets = data if isinstance(data, list) else data.get("tweets", [])
                return [(label, t) for t in tweets]
        except Exception:
            return []

    results = await asyncio.gather(*[_search(label, q) for label, q in queries],
                                     return_exceptions=False)
    for label, tweets in zip([q[0] for q in queries], results):
        if tweets:
            sources_hit.append(label)
        for src_label, t in tweets:
            tid = t.get("id") or t.get("id_str")
            if tid and tid in seen_ids:
                continue
            seen_ids.add(tid)
            all_tweets.append((src_label, t))

    if not all_tweets:
        empty = {"total": 0, "unique_authors": 0, "score_boost": 0, "reasons": [],
                 "rate_limited": rate_limited, "sources_hit": []}
        if rate_limited:
            empty["reasons"] = ["⚠️ Sorsa rate limited — Twitter buzz unknown"]
        # Cache empty result briefly to avoid hammering when rate-limited
        _TWITTER_SEARCH_CACHE[cache_key] = (
            time.time() + (300 if rate_limited else 600), empty,
        )
        return empty

    authors = {}
    mega_hits = []
    for src, t in all_tweets:
        u = (t.get("user") or {})
        username = u.get("username") or u.get("screen_name") or "?"
        followers = u.get("followers_count", 0)
        verified = u.get("verified", False)
        authors[username.lower()] = max(
            authors.get(username.lower(), 0), followers
        )
        if username.lower() in mega_handles:
            mega_hits.append({
                "handle": username, "tier": mega_handles[username.lower()],
                "tweet_id": t.get("id") or t.get("id_str"),
                "text": (t.get("full_text") or t.get("text", ""))[:200],
                "followers": followers,
            })

    # Top shillers (by author followers)
    top_shillers = sorted(authors.items(), key=lambda x: -x[1])[:5]

    # Sample tweets — top 3 from non-bots
    sample_tweets = []
    for src, t in all_tweets[:20]:
        u = t.get("user") or {}
        username = u.get("username") or u.get("screen_name") or "?"
        if (u.get("followers_count") or 0) < 100:
            continue
        sample_tweets.append({
            "handle": username,
            "followers": u.get("followers_count", 0),
            "text": (t.get("full_text") or t.get("text", ""))[:200],
            "tweet_id": t.get("id") or t.get("id_str"),
            "search_via": src,
        })
        if len(sample_tweets) >= 5:
            break

    boost = 0
    reasons = []
    n_authors = len(authors)
    if n_authors >= 30:
        boost += 18
        reasons.append(f"+18 strong buzz ({n_authors} unique authors)")
    elif n_authors >= 10:
        boost += 10
        reasons.append(f"+10 buzz ({n_authors} authors)")
    elif n_authors >= 3:
        boost += 5
        reasons.append(f"+5 some mentions ({n_authors} authors)")
    if mega_hits:
        boost += 25 if any(m["tier"] == "HOT" for m in mega_hits) else 12
        reasons.append(f"+{boost} MEGA mention via search ({', '.join('@'+m['handle'] for m in mega_hits[:2])})")
    if "name" in sources_hit and len(sources_hit) >= 3:
        boost += 5
        reasons.append("+5 mentions via name AND ticker AND address (organic discovery)")

    result = {
        "total_mentions": len(all_tweets),
        "unique_authors": n_authors,
        "top_shillers": top_shillers,
        "mega_mentions": mega_hits,
        "sample_tweets": sample_tweets,
        "sources_hit": sources_hit,
        "score_boost": boost,
        "reasons": reasons,
        "rate_limited": rate_limited,
    }
    # Cache successful result for 10min — same token in multiple sources won't re-search
    _TWITTER_SEARCH_CACHE[cache_key] = (time.time() + 600, result)
    if len(_TWITTER_SEARCH_CACHE) > 5000:
        _TWITTER_SEARCH_CACHE.clear()
    return result


async def _mega_mention_check(symbol: str, address: str, session: aiohttp.ClientSession) -> dict:
    """Search Twitter for recent MEGA tier mentions of $symbol or 0x address.

    Catches: 'Elon tweeted $X 30min ago — we missed it for some reason but a
    TG group mentioned it now, so we backfill the signal.'
    Returns {found: bool, handles: [], latest_tweet: {...}, score_boost, reason}

    Cached 5min — reduces Sorsa load when same token appears in multiple sources.
    """
    api_key = os.environ.get("TWEETSCOUT_API_KEY", "").strip('"')
    if not api_key or not (symbol or address):
        return {}
    if not _sorsa_available():
        return {}
    cache_key = f"{(address or '').lower()}|{(symbol or '').upper()}"
    cached = _MEGA_MENTION_CACHE.get(cache_key)
    if cached and cached[0] > time.time():
        return cached[1] or {}
    # Load MEGA handles list (cached at module level)
    mega_lower = _load_mega_handles()
    if not mega_lower:
        return {}
    matches = []
    for query in [f"${symbol}" if symbol else None, address if address else None]:
        if not query:
            continue
        try:
            async with session.post("https://api.sorsa.io/v3/search-tweets",
                                    headers={"ApiKey": api_key, "Content-Type": "application/json"},
                                    json={"query": query, "count": 30},
                                    timeout=aiohttp.ClientTimeout(total=10)) as r:
                if r.status in (403, 429):
                    _mark_sorsa_rate_limited(1800)
                    break
                if r.status != 200:
                    continue
                data = await r.json()
                tweets = data if isinstance(data, list) else data.get("tweets", [])
                for t in tweets:
                    user = (t.get("user") or {}).get("username", "")
                    if user.lower() in mega_lower:
                        matches.append({
                            "handle": user,
                            "tier": mega_lower[user.lower()],
                            "tweet_id": t.get("id") or t.get("id_str"),
                            "text": (t.get("full_text") or t.get("text", ""))[:200],
                            "created_at": t.get("created_at"),
                        })
        except Exception as e:
            logger.debug(f"mega_mention_check {query}: {e}")
    if not matches:
        # Cache empty result for 5min (avoid re-searching same null)
        _MEGA_MENTION_CACHE[cache_key] = (time.time() + 300, {})
        if len(_MEGA_MENTION_CACHE) > 5000:
            _MEGA_MENTION_CACHE.clear()
        return {}
    # Dedup by handle
    seen = set()
    uniq = []
    for m in matches:
        h = m["handle"].lower()
        if h not in seen:
            seen.add(h)
            uniq.append(m)
    has_hot = any(m["tier"] == "HOT" for m in uniq)
    boost = 25 if has_hot else 12
    handles_str = ", ".join(f"@{m['handle']}" for m in uniq[:3])
    result = {
        "found": True, "handles": uniq, "has_hot": has_hot,
        "score_boost": boost,
        "reason": f"+{boost} MEGA TWITTER MENTION ({handles_str})",
    }
    _MEGA_MENTION_CACHE[cache_key] = (time.time() + 300, result)
    return result


_MEGA_HANDLES_CACHE: Dict[str, str] = {}


def _load_mega_handles() -> Dict[str, str]:
    """Returns {handle_lower: tier}. Lazy-loaded once."""
    global _MEGA_HANDLES_CACHE
    if _MEGA_HANDLES_CACHE:
        return _MEGA_HANDLES_CACHE
    try:
        path = Path("data/mega_handles.json")
        if not path.exists():
            return {}
        raw = json.loads(path.read_text())
        out = {}
        for tier in ("HOT", "ALPHA"):
            for h in raw.get(tier, []) or []:
                out[h.lower().lstrip("@")] = tier
        _MEGA_HANDLES_CACHE = out
        return out
    except Exception:
        return {}


async def _twitter_context_score(handle: str, session: aiohttp.ClientSession) -> dict:
    """Fetch Twitter handle context to weight credibility/origin signals.

    Returns dict with:
      followers_count, friends_count, account_age_days, verified, score_boost, reasons
    """
    if not handle:
        return {}
    api_key = os.environ.get("TWEETSCOUT_API_KEY", "").strip('"')
    if not api_key or not _sorsa_available():
        return {}
    try:
        async with session.get("https://api.sorsa.io/v3/info",
                               params={"username": handle},
                               headers={"ApiKey": api_key},
                               timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status in (403, 429):
                _mark_sorsa_rate_limited(1800)
                return {}
            if r.status != 200:
                return {}
            d = await r.json()
    except Exception as e:
        logger.debug(f"twitter_context {handle}: {e}")
        return {}

    followers = d.get("followers_count") or 0
    friends = d.get("followings_count") or 0
    verified = bool(d.get("verified"))
    created_at = d.get("created_at") or ""
    age_days = 0
    try:
        from datetime import datetime
        # Format: "Tue Jun 02 20:12:29 +0000 2009"
        dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
        age_days = (datetime.now(dt.tzinfo) - dt).days
    except Exception:
        pass

    boost = 0
    reasons = []
    if verified:
        boost += 10
        reasons.append("+10 verified twitter")
    if followers >= 100_000:
        boost += 12
        reasons.append(f"+12 large followers ({followers:,})")
    elif followers >= 10_000:
        boost += 6
        reasons.append(f"+6 mid followers ({followers:,})")
    elif followers < 100:
        boost -= 8
        reasons.append(f"-8 tiny followers ({followers})")
    if age_days >= 730:
        boost += 8
        reasons.append(f"+8 mature account ({age_days}d)")
    elif age_days >= 365:
        boost += 4
        reasons.append(f"+4 1y+ account")
    elif age_days < 30 and age_days > 0:
        boost -= 12
        reasons.append(f"-12 NEW burner account ({age_days}d)")
    # Follower quality: if account has very high follower/following ratio, decent signal
    if followers > 1000 and friends > 0:
        ratio = followers / max(1, friends)
        if ratio > 100:
            boost += 5
            reasons.append(f"+5 high follower/following ratio ({ratio:.0f}x)")
    return {
        "followers": followers,
        "friends": friends,
        "verified": verified,
        "age_days": age_days,
        "score_boost": boost,
        "reasons": reasons,
    }


async def _batch_hermes_data(address: str, chain: str, twitter: str,
                                session: aiohttp.ClientSession) -> dict:
    """One-call aggregation: smart_money + virtuals + creator + butler + outcomes + deep_profile.

    Replaces 4-5 separate HTTPS calls with 1 round-trip. Server-side parallel queries.
    """
    if not (HERMES_DATA_API_URL and HERMES_DATA_API_KEY):
        return {}
    try:
        async with session.get(f"{HERMES_DATA_API_URL}/batch/token",
                               params={"address": address, "twitter": twitter or "", "chain": chain or "base"},
                               headers={"Authorization": f"Bearer {HERMES_DATA_API_KEY}"},
                               timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status != 200:
                return {}
            return await r.json()
    except Exception as e:
        logger.debug(f"batch_hermes_data: {e}")
        return {}


async def _smart_money_check(address: str, session: aiohttp.ClientSession) -> dict:
    """Query Hermes Data API for smart money signals on this token."""
    if not (HERMES_DATA_API_URL and HERMES_DATA_API_KEY):
        return {}
    try:
        url = f"{HERMES_DATA_API_URL}/smart-money/token"
        async with session.get(url, params={"address": address},
                               headers={"Authorization": f"Bearer {HERMES_DATA_API_KEY}"},
                               timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status != 200:
                return {}
            return await r.json()
    except Exception as e:
        logger.debug(f"smart_money_check failed: {e}")
        return {}


async def _llm_take(anatomy: dict, group_name: str, msg_text: str, session: aiohttp.ClientSession) -> str:
    """Use kimi-k2.5 via Hermes Data API to give a contextual interpretation."""
    if not (HERMES_DATA_API_URL and HERMES_DATA_API_KEY):
        return ""
    sym = anatomy.get("symbol") or "?"
    name = anatomy.get("name") or sym
    addr = anatomy.get("address") or ""
    mcap = anatomy.get("current_mcap") or 0
    pct1 = anatomy.get("price_change_1h") or 0
    pct24 = anatomy.get("price_change_24h") or 0
    twh = anatomy.get("twitter_handle") or ""
    holders = anatomy.get("holder_count") or 0
    primary = anatomy.get("_primary") or {}
    sm = anatomy.get("_smart_money") or {}

    sm_summary = ""
    if sm:
        buyers = sm.get("smart_buyers", []) or []
        if buyers:
            sm_summary = f"Smart money buyers: {len(buyers)} (top: {', '.join(b.get('label','?') for b in buyers[:3])})"

    prompt = f"""You are Hermes, a crypto alpha hunter. Analyze this token signal in <100 words.

Token: ${sym} ({name})
Address: {addr}
MCap: ${mcap:,.0f} | 1h: {pct1:+.1f}% | 24h: {pct24:+.1f}%
Twitter: @{twh} | Holders: {holders}
Primary token: {primary.get("is_primary", "unknown")} | Copycats: {primary.get("copycats_count", 0)}
{sm_summary}

Source: Telegram group "{group_name}"
Original msg snippet: "{(msg_text or '')[:300]}"

Tasks:
1. Is the twitter handle linked to a known meme/personality (e.g. Pepe creator, MAGA, etc)? If yes, name them.
2. What narrative is this riding?
3. Bull case in 1 sentence.
4. Risk in 1 sentence.

Answer concise, max 4 short lines."""
    try:
        url = f"{HERMES_DATA_API_URL}/llm/chat"
        payload = {
            "model": LLM_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 200,
            "temperature": 0.3,
        }
        async with session.post(url, json=payload,
                                headers={"Authorization": f"Bearer {HERMES_DATA_API_KEY}"},
                                timeout=aiohttp.ClientTimeout(total=15)) as r:
            if r.status != 200:
                body = await r.text()
                logger.warning(f"LLM call status={r.status} body={body[:200]}")
                return ""
            data = await r.json()
            choices = data.get("choices") or []
            if not choices:
                logger.warning(f"LLM no choices: {str(data)[:200]}")
                return ""
            msg = choices[0].get("message") or {}
            content = (msg.get("content") or "").strip()
            if not content:
                # kimi-k2.5 reasoning model puts content in reasoning_content
                content = (msg.get("reasoning_content") or "").strip()
            return content
    except asyncio.TimeoutError:
        logger.warning(f"LLM timeout after 15s (model={LLM_MODEL})")
        return ""
    except Exception as e:
        logger.warning(f"LLM take exception: {type(e).__name__}: {e}")
        return ""


async def llm_extract_terms(text: str, session: aiohttp.ClientSession) -> list:
    """LLM extracts memecoin-worthy search terms from any text."""
    if not (HERMES_DATA_API_URL and HERMES_DATA_API_KEY) or not text.strip():
        return []
    prompt = (
        "You are a crypto memecoin scout. Extract up to 6 short search terms "
        "(1-2 words each, no $ or #) likely used as memecoin tickers/themes "
        "from this text. Include slogans, named people/places, hashtags, "
        "tickers, cultural refs. JSON array only.\n\n"
        f"TEXT:\n{text[:1200]}\n\nJSON ARRAY:"
    )
    try:
        async with session.post(f"{HERMES_DATA_API_URL}/llm/chat",
                                json={"model": LLM_MODEL,
                                      "messages": [{"role": "user", "content": prompt}],
                                      "max_tokens": 150, "temperature": 0.2},
                                headers={"Authorization": f"Bearer {HERMES_DATA_API_KEY}"},
                                timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status != 200:
                return []
            data = await r.json()
            content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
            if not content:
                return []
            m = re.search(r"\[[^\[\]]+\]", content, re.S)
            if not m:
                return []
            try:
                arr = json.loads(m.group(0))
            except json.JSONDecodeError:
                return []
            return [t.strip().lstrip("$").lstrip("#")
                    for t in arr if isinstance(t, str) and 2 <= len(t.strip()) <= 40][:6]
    except Exception:
        return []


async def resolve_ticker_to_address(ticker: str, session: aiohttp.ClientSession) -> tuple:
    """Search DexScreener for ticker, return (address, chain) of most-liquid match.

    Strips $/# prefix. Uses fuzzy match: exact symbol OR symbol contains ticker.
    Filters out pairs with liquidity <$5K and skips honeypot-style scams.
    """
    clean = ticker.strip().lstrip("$").lstrip("#").strip()
    if not clean or len(clean) < 2:
        return None, None
    try:
        # DexScreener treats $ as literal; query without $
        async with session.get("https://api.dexscreener.com/latest/dex/search",
                               params={"q": clean},
                               timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status != 200:
                return None, None
            data = await r.json()
        pairs = data.get("pairs") or []
        target = clean.upper()
        # Tier 1: exact symbol match
        matches = [p for p in pairs
                   if (p.get("baseToken", {}).get("symbol") or "").upper() == target]
        # Tier 2: name contains query (for phrase searches like "Scam Altman")
        if not matches and len(clean) >= 4:
            matches = [p for p in pairs
                       if target in (p.get("baseToken", {}).get("name") or "").upper()
                       or target in (p.get("baseToken", {}).get("symbol") or "").upper()]
        if not matches:
            return None, None
        # Filter honeypots/dead pairs
        matches = [m for m in matches
                   if (m.get("liquidity", {}).get("usd") or 0) >= 5000]
        if not matches:
            return None, None
        # Sort by HEAT: 24h volume + 24h % gain (pumping NOW = the right token)
        # Falls back to liquidity for tokens with similar heat.
        def heat(p):
            vol = float((p.get("volume") or {}).get("h24") or 0)
            pct = float((p.get("priceChange") or {}).get("h24") or 0)
            liq = float((p.get("liquidity") or {}).get("usd") or 0)
            # Pumping tokens dominate; ties broken by liquidity
            return -(vol * (1 + max(0, pct) / 100) + liq * 0.1)
        matches.sort(key=heat)
        top = matches[0]
        addr = (top.get("baseToken", {}).get("address") or "").lower()
        chain = top.get("chainId", "ethereum")
        return addr, chain
    except Exception as e:
        logger.debug(f"resolve_ticker {ticker} failed: {e}")
        return None, None


async def investigate_token(address: str, chain_hint: str = "ethereum", group_name: str = "", msg_text: str = "") -> dict:
    """Reuse pump_forensics + add primary-token check + smart money + LLM take + narrative."""
    try:
        from pump_forensics import extract_pump_anatomy, _simulate_hermes_decision
        timings = {}
        t0 = time.time()
        async with aiohttp.ClientSession() as session:
            t = time.time()
            anatomy = await extract_pump_anatomy(address, chain_hint, session)
            timings["pump_forensics"] = time.time() - t

            sym = anatomy.get("symbol")
            if sym:
                t = time.time()
                primary = await _check_primary_token(
                    sym, address, anatomy.get("chain") or chain_hint,
                    anatomy.get("pair_created_at") or 0,
                    anatomy.get("name") or "",
                    anatomy.get("twitter_handle") or "",
                    session,
                )
                timings["primary_check"] = time.time() - t
                anatomy["_primary"] = primary

            # 🚀 BATCH: smart_money + virtuals + creator + outcomes + deep_profile in 1 call
            t = time.time()
            batch = await _batch_hermes_data(
                address, anatomy.get("chain") or chain_hint,
                anatomy.get("twitter_handle", ""), session,
            )
            timings["batch_hermes"] = time.time() - t
            if batch:
                # Smart money
                if batch.get("smart_money"):
                    anatomy["_smart_money"] = batch["smart_money"]
                # Virtuals deep (overlaps with _virtuals_deep_check below — prefer batch)
                # The batch endpoint returns the raw DB row, which includes Clanker
                # tokens in the same DB. Filter by factory to exclude them, and use
                # token_address (DB field) instead of address (which is never set).
                v = batch.get("virtuals") or {}
                factory_b = (v.get("factory") or "").lower()
                if v.get("token_address") and factory_b != "clanker":
                    anatomy["_virtuals_deep"] = {
                        "is_virtuals": True,
                        "creator_wallet": v.get("creator_wallet") or v.get("ownerAddress"),
                        "twitter": v.get("twitter") or v.get("twitterHandle") or v.get("creator_twitter"),
                        "is_openclaw": (v.get("cluster") or "").upper() == "OPENCLAW",
                        "holder_count": v.get("holder_count") or v.get("holderCount"),
                        "dev_history_count": (batch.get("creator_history") or {}).get("history_count", 0),
                    }
                # Deep profile (Butler trust_weight)
                deep = batch.get("deep_profile") or {}
                if deep:
                    anatomy["_deep_profile"] = {**deep, "available": True}
                # Butler alerts existing
                if batch.get("butler_alerts"):
                    anatomy["_butler_alerts"] = batch["butler_alerts"]
                # Past outcomes for this token
                if batch.get("outcomes"):
                    anatomy["_outcomes"] = batch["outcomes"]

            # twitter_context (Sorsa /info — separate, since it's not in batch)
            t = time.time()
            tw_ctx = await _twitter_context_score(anatomy.get("twitter_handle", ""), session)
            timings["twitter_ctx"] = time.time() - t
            if tw_ctx:
                anatomy["_twitter_context"] = tw_ctx

            t = time.time()
            uni = await _detect_uniswap_version(address, anatomy.get("chain") or chain_hint, session)
            timings["uniswap_v"] = time.time() - t
            if uni:
                anatomy["_uniswap"] = uni

            t = time.time()
            # Comprehensive Twitter search — by address, ticker, name, dev wallet
            dev_wallet = ""
            if anatomy.get("_virtuals_deep"):
                dev_wallet = anatomy["_virtuals_deep"].get("creator_wallet", "")
            tw_search = await _twitter_full_search(
                anatomy.get("symbol", ""), address,
                anatomy.get("name", ""), dev_wallet, session,
            )
            timings["twitter_search"] = time.time() - t
            if tw_search.get("total_mentions"):
                anatomy["_twitter_search"] = tw_search

            t = time.time()
            mega_mention = await _mega_mention_check(anatomy.get("symbol", ""), address, session)
            timings["mega_mention"] = time.time() - t
            if mega_mention.get("found"):
                anatomy["_mega_mention"] = mega_mention

            # Virtuals deep check — only run if NOT already populated by batch
            if not anatomy.get("_virtuals_deep"):
                t = time.time()
                v_deep = await _virtuals_deep_check(address, session)
                timings["virtuals_deep"] = time.time() - t
                if v_deep:
                    anatomy["_virtuals_deep"] = v_deep
            else:
                # Augment batch result with farmer/red_flags via separate call (rare path)
                pass

            t = time.time()
            try:
                from top_holders import analyze_top_holders
                # Compute token age from pair_created_at (ms timestamp)
                pair_ts = anatomy.get("pair_created_at") or 0
                token_age_days = ((time.time() * 1000 - pair_ts) / 86400000) if pair_ts else 0
                holders_info = await analyze_top_holders(
                    address, anatomy.get("chain") or chain_hint,
                    total_supply=0, session=session,
                    token_age_days=token_age_days,
                )
                if holders_info:
                    anatomy["_top_holders"] = holders_info
                    anatomy["_token_age_days"] = token_age_days
            except Exception as e:
                logger.debug(f"top holders failed: {e}")
            timings["top_holders"] = time.time() - t

            # OG image / social card from DexScreener pair info
            try:
                async with session.get(f"https://api.dexscreener.com/latest/dex/tokens/{address}",
                                       timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status == 200:
                        data = await r.json()
                        pairs = data.get("pairs") or []
                        if pairs:
                            info = pairs[0].get("info") or {}
                            anatomy["_og_image"] = info.get("imageUrl") or pairs[0].get("imageUrl") or ""
                            anatomy["_og_header"] = info.get("header") or ""
                            anatomy["_websites"] = [w.get("url") for w in (info.get("websites") or []) if w.get("url")]
            except Exception:
                pass

            t = time.time()
            llm = await _llm_take(anatomy, group_name, msg_text, session)
            timings["llm"] = time.time() - t
            if llm:
                anatomy["_llm_take"] = llm
        anatomy["_timings"] = timings
        anatomy["_total_ms"] = int((time.time() - t0) * 1000)
        breakdown = " ".join(f"{k}={v:.2f}s" for k, v in timings.items())
        logger.info(f"  investigate {address[:10]}... total={anatomy['_total_ms']}ms | {breakdown}"
                    + (" [LLM ok]" if llm else " [LLM none]"))
        decision = anatomy.get("hermes_would_alert") or _simulate_hermes_decision(anatomy)
        # Narrative classification + tracking
        try:
            from narrative_engine import (tracker as narr_tracker,
                                          classify_narrative, boost_for_narrative)
            narrs = classify_narrative(
                anatomy.get("symbol", ""), anatomy.get("name", ""),
                anatomy.get("twitter_handle", ""), msg_text,
            )
            for narr in narrs:
                narr_tracker.add(narr, address, group_name,
                                 anatomy.get("symbol", ""), anatomy.get("name", ""),
                                 anatomy.get("current_mcap") or 0)
            top_narr = None
            top_stage = "NEW"
            if narrs:
                # Pick narrative with highest velocity
                top_narr = max(narrs, key=lambda n: narr_tracker.velocity(n))
                top_stage = narr_tracker.stage(top_narr)
                anatomy["_narrative"] = {
                    "names": narrs, "top": top_narr, "stage": top_stage,
                    "velocity_1h": narr_tracker.velocity(top_narr),
                    "unique_tokens_1h": narr_tracker.unique_tokens(top_narr),
                }
        except Exception as e:
            logger.debug(f"narrative classification failed: {e}")
            anatomy["_narrative"] = None
            top_narr = None
            top_stage = "NEW"

        # Classify token type FIRST — affects how we treat copycat
        token_type = _classify_token_type(anatomy)
        anatomy["_token_type"] = token_type

        # Adjust score with primary bonus/penalty (MEMECOINS only — projects shouldn't be penalized for shared tickers)
        primary = anatomy.get("_primary") or {}
        if primary and token_type == "memecoin":
            if primary.get("is_primary") and primary.get("copycats_count", 0) > 0:
                decision["score"] = min(100, decision.get("score", 0) + 8)
                decision.setdefault("reasons", []).append(f"+8 PRIMARY memecoin ({primary['copycats_count']} copycats exist)")
            elif not primary.get("is_primary") and primary.get("copycats_count", 0) >= 2:
                decision["score"] = max(0, decision.get("score", 0) - 12)
                decision.setdefault("reasons", []).append(
                    f"-12 COPYCAT memecoin — original on {primary.get('oldest_chain','?')} is {primary.get('oldest_age_days',0):.0f}d old"
                )
            elif not primary.get("is_primary") and primary.get("copycats_count", 0) == 1:
                decision.setdefault("reasons", []).append(
                    f"⚠ NOT primary memecoin — older {primary.get('oldest_chain','?')} version exists"
                )
        elif primary and token_type == "project":
            # For real projects, just informational note
            diff_count = primary.get("same_symbol_diff_project_count", 0)
            if diff_count > 0:
                decision.setdefault("reasons", []).append(
                    f"ℹ️ shares ticker with {diff_count} unrelated projects on other chains (no penalty)"
                )
            # Narrative boost
            if top_narr:
                from narrative_engine import boost_for_narrative
                boost, reason = boost_for_narrative(top_stage)
                if boost:
                    decision["score"] = max(0, min(100, decision.get("score", 0) + boost))
                    decision.setdefault("reasons", []).append(f"{reason} [{top_narr}]")
            # Twitter context boost (verified, age, followers, etc)
            tw_ctx = anatomy.get("_twitter_context") or {}
            if tw_ctx and tw_ctx.get("score_boost"):
                decision["score"] = max(0, min(100, decision.get("score", 0) + tw_ctx["score_boost"]))
                for r in tw_ctx.get("reasons", []):
                    decision.setdefault("reasons", []).append(r + " [twitter ctx]")
            # Butler deep profile boost — ONLY for Virtuals tokens (system is virtuals-specific)
            deep = anatomy.get("_deep_profile") or {}
            v_d_for_tw = anatomy.get("_virtuals_deep") or {}
            tw = deep.get("trust_weight", 0) if v_d_for_tw.get("is_virtuals") else 0
            if tw >= 50:
                decision["score"] = min(100, decision.get("score", 0) + 30)
                decision.setdefault("reasons", []).insert(0, f"+30 🔥 ELITE Virtuals creator (trust_weight {tw}/100)")
            elif tw >= 30:
                decision["score"] = min(100, decision.get("score", 0) + 18)
                decision.setdefault("reasons", []).insert(0, f"+18 ✅ STRONG Virtuals signal (trust_weight {tw}/100)")
            elif tw >= 15:
                decision["score"] = min(100, decision.get("score", 0) + 10)
                decision.setdefault("reasons", []).insert(0, f"+10 ✅ Virtuals trust_weight {tw}/100")
            elif tw > 0:
                decision["score"] = min(100, decision.get("score", 0) + 4)
                decision.setdefault("reasons", []).append(f"+4 weak Virtuals signal (trust_weight {tw}/100)")
            # Penalty for red flags
            red_flags = deep.get("red_flags", []) or []
            if "frequent_username_changes" in str(red_flags):
                decision["score"] = max(0, decision.get("score", 0) - 10)
                decision.setdefault("reasons", []).append("-10 frequent username changes (rug history?)")
            # Uniswap version boost (V2 good, V3/V4 bad — perpetual fees)
            # Skip for Virtuals/OpenClaw tokens (use platform bonding curve, Uniswap fees irrelevant)
            uni = anatomy.get("_uniswap") or {}
            v_d_check = anatomy.get("_virtuals_deep") or {}
            if uni and uni.get("score_boost") and not v_d_check.get("is_virtuals"):
                decision["score"] = max(0, min(100, decision.get("score", 0) + uni["score_boost"]))
                for r in uni.get("reasons", []):
                    decision.setdefault("reasons", []).append(r)
            # MEGA Twitter mention boost (we missed live, but search caught it)
            mega = anatomy.get("_mega_mention") or {}
            if mega.get("score_boost"):
                decision["score"] = max(0, min(100, decision.get("score", 0) + mega["score_boost"]))
                decision.setdefault("reasons", []).insert(0, mega["reason"])
            # Comprehensive Twitter search boost (buzz + MEGA via search)
            tws = anatomy.get("_twitter_search") or {}
            if tws.get("score_boost"):
                decision["score"] = max(0, min(100, decision.get("score", 0) + tws["score_boost"]))
                for r in tws.get("reasons", []):
                    decision.setdefault("reasons", []).append(r)
            # Top holders boost (smart money in top 10, distribution health)
            th = anatomy.get("_top_holders") or {}
            if th.get("score_boost"):
                decision["score"] = max(0, min(100, decision.get("score", 0) + th["score_boost"]))
                for r in th.get("reasons", []):
                    decision.setdefault("reasons", []).append(r)
                for risk in th.get("risks", []):
                    decision.setdefault("reasons", []).append(risk)
            # Virtuals deep check boost (farmer, history, Virtuals team follow)
            v_d = anatomy.get("_virtuals_deep") or {}
            if v_d.get("score_boost"):
                decision["score"] = max(0, min(100, decision.get("score", 0) + v_d["score_boost"]))
                for r in v_d.get("reasons", []):
                    decision.setdefault("reasons", []).append(r)
            # Re-evaluate action threshold (5-tier)
            s = decision["score"]
            if s >= 80:
                decision["action"] = "STRONG_ALERT"
            elif s >= 60:
                decision["action"] = "ALERT"
            elif s >= 45:
                decision["action"] = "WEAK_ALERT"
            elif s >= 25:
                decision["action"] = "WATCH"
            else:
                decision["action"] = "SKIP"
        anatomy["_decision"] = decision
        return anatomy
    except Exception as e:
        logger.warning(f"investigate_token({address}) failed: {e}")
        return {}


def _fmt_usd(v: float) -> str:
    if not v:
        return "—"
    if v >= 1_000_000_000:
        return f"${v/1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v/1_000:.1f}K"
    return f"${v:.0f}"


def _fmt_pct(v) -> str:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.1f}%"


def _format_alert(group_name: str, sender: str, msg_url: str, anatomy: dict) -> str:
    def esc(s):
        return html.escape(str(s)) if s is not None else ""
    d = anatomy.get("_decision", {})
    sym = esc(anatomy.get("symbol") or "?")
    name = esc(anatomy.get("name") or anatomy.get("symbol") or "?")
    addr = anatomy.get("address") or anatomy.get("contract") or ""
    chain = esc((anatomy.get("chain") or "ethereum").upper())
    group_name = esc(group_name)
    sender = esc(sender)
    mcap = anatomy.get("current_mcap", 0)
    vol = anatomy.get("volume_24h", 0)
    liq = anatomy.get("liquidity_usd", 0)
    holders = anatomy.get("holder_count")
    pct_5m = anatomy.get("price_change_5m", 0)
    pct_1h = anatomy.get("price_change_1h", 0)
    pct_6h = anatomy.get("price_change_6h", 0)
    pct_24h = anatomy.get("price_change_24h", 0)
    tw = anatomy.get("twitter_handle") or ""
    tg = anatomy.get("telegram") or ""
    action = d.get("action", "WATCH")
    score = d.get("score", 0)

    emoji = {"STRONG_ALERT": "🔥🚨", "ALERT": "🚨", "WEAK_ALERT": "⚡",
             "WATCH": "👀", "SKIP": "🚫"}.get(action, "❓")
    bar = "━" * 24
    lines = [
        f"{emoji} <b>HERMES TG SIGNAL — {action}</b>  score={score}/100",
        bar,
        f"<b>{name}</b>",
        f"<b>${sym}</b>  ·  <i>{chain}</i>",
        f"<code>{addr}</code>",
        "",
        f"MCap: <b>{_fmt_usd(mcap)}</b>  ·  Liq: {_fmt_usd(liq)}  ·  Vol24h: {_fmt_usd(vol)}",
        f"📊 5m: {_fmt_pct(pct_5m)}  |  1h: {_fmt_pct(pct_1h)}  |  6h: {_fmt_pct(pct_6h)}  |  24h: {_fmt_pct(pct_24h)}",
    ]
    if holders is not None:
        lines.append(f"👥 Holders: {holders:,}" if isinstance(holders, int) else f"👥 Holders: {holders}")
    token_type = anatomy.get("_token_type", "")
    primary = anatomy.get("_primary") or {}
    if primary and token_type:
        type_emoji = "🎨" if token_type == "memecoin" else "🏗️"
        type_label = "MEMECOIN" if token_type == "memecoin" else "PROJECT"
        diff = primary.get("same_symbol_diff_project_count", 0)
        if token_type == "memecoin":
            copycats = primary.get("copycats_count", 0)
            if primary.get("is_primary") and copycats > 0:
                lines.append(f"{type_emoji} <b>{type_label}</b> · ✅ PRIMARY ({copycats} copycats em outras chains)")
            elif not primary.get("is_primary"):
                other_chain = (primary.get("oldest_chain") or "?").upper()
                other_age = primary.get("oldest_age_days", 0)
                lines.append(f"{type_emoji} <b>{type_label}</b> · ⚠️ NOT primary — original em {other_chain} ({other_age:.0f}d antes)")
            else:
                lines.append(f"{type_emoji} <b>{type_label}</b> · 🆔 unique")
        else:
            # PROJECT — different chains likely different projects, just note
            note = f"({diff} unrelated tokens share ticker)" if diff > 0 else ""
            lines.append(f"{type_emoji} <b>{type_label}</b>  {note}")
    narrative = anatomy.get("_narrative")
    if narrative and narrative.get("top"):
        stage = narrative["stage"]
        emoji = {"EMERGING": "🔥", "GROWING": "📈", "PEAK": "⚠️", "NEW": "💡"}.get(stage, "")
        lines.append(f"{emoji} <b>Narrativa: {esc(narrative['top'])}</b> · stage={stage} · {narrative['unique_tokens_1h']} tokens/1h")
    uni = anatomy.get("_uniswap") or {}
    v_d = anatomy.get("_virtuals_deep") or {}
    # Hide Uniswap version for Virtuals/OpenClaw tokens (use platform's bonding curve, not Uniswap fees)
    if uni and uni.get("version") and not v_d.get("is_virtuals"):
        v = uni["version"].upper()
        v_emoji = {"V2": "✅", "V3": "⚠️", "V4": "❌"}.get(v, "")
        v_note = {"V2": "no perpetual fees", "V3": "perpetual fees to deployer", "V4": "perpetual fees to deployer"}.get(v, "")
        lines.append(f"{v_emoji} <b>Uniswap {v}</b> ({v_note})")
    mega_m = anatomy.get("_mega_mention") or {}
    if mega_m.get("found"):
        handles = mega_m.get("handles", [])
        for m in handles[:3]:
            tw_emoji = "🔥" if m.get("tier") == "HOT" else "⚡"
            tweet_link = f"https://x.com/{m['handle']}/status/{m.get('tweet_id','')}"
            lines.append(f'{tw_emoji} <b>MEGA mention</b>: <a href="{tweet_link}">@{esc(m["handle"])}</a> [{m.get("tier","")}]')
    v_d = anatomy.get("_virtuals_deep") or {}
    if v_d.get("is_virtuals"):
        v_emoji = "🤖" if v_d.get("is_openclaw") else "🎭"
        v_type = "OpenClaw" if v_d.get("is_openclaw") else "Virtuals"
        v_bits = [f"{v_emoji} <b>{v_type}</b>"]
        if v_d.get("dev_history_count"):
            v_bits.append(f"dev: {v_d['dev_history_count']} past projects")
        if v_d.get("dev_is_farmer"):
            v_bits.append(f"⚠️ FARMER ({v_d.get('dev_farm_count','?')})")
        if v_d.get("followed_by_virtuals"):
            v_bits.append("✓ followed by @virtuals_io")
        lines.append(" · ".join(v_bits))
    # Butler deep profile (the BIG signal — trust_weight 0-100, Virtuals-only)
    deep = anatomy.get("_deep_profile") or {}
    tw_w = deep.get("trust_weight", 0) if v_d.get("is_virtuals") else 0
    if tw_w > 0:
        if tw_w >= 50:
            tw_emoji = "🔥"
            tw_label = f"ELITE ({tw_w}/100)"
        elif tw_w >= 30:
            tw_emoji = "✅"
            tw_label = f"STRONG ({tw_w}/100)"
        elif tw_w >= 15:
            tw_emoji = "👌"
            tw_label = f"good ({tw_w}/100)"
        else:
            tw_emoji = "🟡"
            tw_label = f"weak ({tw_w}/100)"
        signals_line = f"{tw_emoji} <b>Butler trust: {tw_label}</b>"
        # Trend from /score-changes (v3): show week delta when meaningful
        wk = deep.get("score_week_delta", 0) or 0
        if abs(wk) >= 1:
            trend_emoji = "⬆️" if wk > 0 else "⬇️"
            signals_line += f" {trend_emoji}{abs(wk):.1f}/wk"
        team_followers = deep.get("team_followers") or []
        if team_followers:
            signals_line += f" · 👑 core team: {', '.join('@' + h for h in team_followers[:2])}"
        if deep.get("followed_by_virtuals"):
            signals_line += " · 🔥 @virtuals_io follows"
        lines.append(signals_line)
        # Username change red-flag (sybil/scam pattern)
        uc = deep.get("username_changes", 0) or 0
        if uc >= 3:
            lines.append(f"⚠️ <b>Twitter renamed {uc}×</b> — possible recycled account")
    th = anatomy.get("_top_holders") or {}
    if th and th.get("top_holders"):
        sm_count = th.get("smart_money_count", 0)
        top1 = th.get("top1_pct", 0)
        top10 = th.get("top10_pct", 0)
        sm_str = f" · 🐋 {sm_count} smart money" if sm_count else ""
        lines.append(f"📊 Top1: {top1:.0f}% · Top10: {top10:.0f}%{sm_str}")
        cohort = th.get("cohort") or {}
        for tag in cohort.get("tags", []):
            tag_emoji = {"SYBIL_FARM": "🚩", "INSIDER_COHORT": "🚩", "ALPHA_COHORT": "✅"}.get(tag, "🏷️")
            lines.append(f"   {tag_emoji} <b>{tag.replace('_', ' ')}</b>")
        if cohort.get("dominant_cex"):
            cex = cohort['dominant_cex']
            n = cohort.get("funding_distribution", {}).get(cex, 0)
            lines.append(f"   💱 {n}/10 funded via {cex}")
        for h in th.get("top_holders", [])[:5]:
            label = h.get("label", "")
            cex = h.get("funding_cex", "")
            cex_str = f" 💱 {cex}" if cex else ""
            if label or cex:
                lines.append(f"   {h['address'][:14]}... {h['pct']:.1f}%  {label}{cex_str}")
    lines.append("")
    socials = []
    if tw:
        tw_e = esc(tw)
        ctx = anatomy.get("_twitter_context") or {}
        ctx_bits = []
        if ctx.get("verified"):
            ctx_bits.append("✓")
        f = ctx.get("followers")
        if f:
            if f >= 1_000_000:
                ctx_bits.append(f"{f/1_000_000:.1f}M")
            elif f >= 1000:
                ctx_bits.append(f"{f/1000:.1f}K")
            else:
                ctx_bits.append(str(f))
        age = ctx.get("age_days")
        if age:
            if age >= 365:
                ctx_bits.append(f"{age//365}y old")
            elif age < 30:
                ctx_bits.append(f"⚠️ {age}d new")
        ctx_str = " | ".join(ctx_bits)
        socials.append(f'🐦 <a href="https://x.com/{tw_e}">@{tw_e}</a>'
                       + (f" ({ctx_str})" if ctx_str else ""))
    if tg:
        socials.append(f"💬 {esc(tg)}")
    if socials:
        lines.append("  ·  ".join(socials))
    lines.append(f"📡 <i>{group_name}</i>  ·  {sender}")
    if msg_url:
        lines.append(f'🔗 <a href="{msg_url}">source msg</a>')

    tws = anatomy.get("_twitter_search") or {}
    if tws.get("sample_tweets"):
        lines.append("")
        lines.append(f"<b>🐦 Top Twitter mentions ({tws.get('unique_authors', 0)} authors, sources: {', '.join(tws.get('sources_hit', []))}):</b>")
        for s in tws.get("sample_tweets", [])[:3]:
            f_str = f"{s['followers']/1000:.0f}K" if s["followers"] >= 1000 else str(s["followers"])
            tweet_url = f"https://x.com/{s['handle']}/status/{s.get('tweet_id','')}"
            lines.append(f'  <a href="{tweet_url}">@{esc(s["handle"])}</a> ({f_str}f via {s.get("search_via","?")}): {esc(s["text"][:120])}')
    reasons = (d.get("reasons") or [])[:8]
    if reasons:
        lines.append("")
        lines.append("<b>Reasons:</b>")
        for r in reasons:
            lines.append(f"  {esc(r)}")
    llm_take = anatomy.get("_llm_take")
    if llm_take:
        lines.append("")
        lines.append("<b>🧠 LLM take:</b>")
        for chunk in llm_take.split("\n"):
            chunk = chunk.strip()
            if chunk:
                lines.append(f"  {esc(chunk)}")
    return "\n".join(lines)


async def run_listener(client: TelegramClient, bot_token: str, user_chat_id: int) -> None:
    """Main loop — registers Telethon NewMessage handler and waits forever."""
    monitored = load_monitored_groups()
    if not monitored:
        logger.warning("No monitored groups in data/monitored_groups.json — listener idle")
        await asyncio.Event().wait()
        return

    logger.info(f"Listener watching {len(monitored)} group(s): {sorted(monitored)}")

    cooldowns = _load_cooldowns()
    seen_msg_ids: Set[int] = set()

    @client.on(events.NewMessage(chats=list(monitored)))
    async def handler(event):
        try:
            msg_id = event.id
            if msg_id in seen_msg_ids:
                return
            seen_msg_ids.add(msg_id)
            if len(seen_msg_ids) > 5000:
                seen_msg_ids.clear()

            text = event.message.message or ""
            if not text:
                return

            evm_addrs = list(set(EVM_RE.findall(text)))
            tickers = list(set(TICKER_RE.findall(text)))

            if not evm_addrs and not tickers:
                return

            chat = await event.get_chat()
            sender = await event.get_sender()
            group_name = getattr(chat, "title", str(chat.id))
            sender_name = getattr(sender, "username", None) or getattr(sender, "first_name", "?") or "?"
            chat_username = getattr(chat, "username", None)
            msg_url = f"https://t.me/{chat_username}/{msg_id}" if chat_username else ""

            logger.info(f"[{group_name}] @{sender_name}: {len(evm_addrs)} evm + {len(tickers)} tickers")

            now = time.time()
            # Resolve ticker-only mentions (no EVM in msg) to addresses via DexScreener
            ticker_addrs = []
            if not evm_addrs and tickers:
                async with aiohttp.ClientSession() as session:
                    for tk in tickers[:3]:  # cap to avoid spam
                        a, c = await resolve_ticker_to_address(tk, session)
                        if a:
                            ticker_addrs.append((a, c, tk))
                logger.info(f"  resolved {len(ticker_addrs)} ticker(s) to addresses")

            targets = [(a, "ethereum", None) for a in evm_addrs] + ticker_addrs
            for addr, chain, ticker_src in targets:
                key = addr.lower()
                if cooldowns.get(key, 0) > now:
                    continue
                cooldowns[key] = now + COOLDOWN_SEC

                anatomy = await investigate_token(addr, chain_hint=chain, group_name=group_name, msg_text=text)
                if not anatomy:
                    continue
                d = anatomy.get("_decision", {})
                score = d.get("score", 0)
                mcap = anatomy.get("current_mcap", 0)

                if score < ALERT_THRESHOLD:
                    logger.info(f"  {addr} score={score} < threshold {ALERT_THRESHOLD} — skip")
                    continue
                # Apply tg_group mcap cap
                cap = SOURCE_MCAP_CAPS.get("tg_group", DEFAULT_MCAP_CAP)
                if cap and mcap > cap:
                    logger.info(f"  {addr} score={score} but mcap=${mcap:,.0f} > ${cap:,} (tg_group cap) — skip")
                    continue
                # GEM ONLY MODE — require at least one "big signal" + daily cap
                if GEM_MODE:
                    passes, why = _gem_quality_check(anatomy, source=f"tg/{group_name}")
                    if not passes:
                        logger.info(f"  {addr} score={score} — gem-mode SKIP ({why})")
                        continue
                    if not _gem_daily_check():
                        logger.info(f"  {addr} — gem-mode daily cap {GEM_DAILY_CAP} reached, skip")
                        continue
                    logger.info(f"  {addr} score={score} — gem-mode PASS ({why})")

                alert_text = _format_alert(group_name, f"@{sender_name}", msg_url, anatomy)
                kb = _build_keyboard(addr, anatomy.get("chain") or chain or "ethereum")
                await send_alert(bot_token, user_chat_id, alert_text, keyboard=kb)
                # Record for outcome tracking
                try:
                    from outcome_tracker import record_alert
                    record_alert(
                        token=addr, chain=anatomy.get("chain") or chain or "ethereum",
                        symbol=anatomy.get("symbol") or "",
                        score=score, action=d.get("action", "ALERT"),
                        source=f"tg/{group_name}",
                        mcap=anatomy.get("current_mcap") or 0,
                        price=anatomy.get("current_price") or 0,
                    )
                except Exception as e:
                    logger.debug(f"record_alert failed: {e}")
                if GEM_MODE:
                    _gem_register_alert()
                logger.info(f"  ALERTED {addr} score={score}{' (from ticker $' + ticker_src + ')' if ticker_src else ''}")
                # Register for convergence
                try:
                    from convergence_engine import register_signal
                    conv = register_signal(addr, source=f"tg/{group_name}",
                                           score=score, symbol=anatomy.get("symbol") or "")
                    if conv.get("triggered"):
                        await _send_convergence_alert(addr, anatomy, conv,
                                                      bot_token, user_chat_id)
                except Exception as e:
                    logger.debug(f"convergence register failed: {e}")
            _save_cooldowns(cooldowns)
        except Exception as e:
            logger.exception(f"handler error: {e}")

    logger.info("Telegram group listener active")
    await asyncio.Event().wait()
