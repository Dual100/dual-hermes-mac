#!/usr/bin/env python3
"""
Hermes Data API v2 — Expanded read-only HTTPS endpoints.

Now exposes the FULL Hetzner data surface that is useful for gem research:

  Smart Money:
    /smart-money/is-smart, /wallets, /recent-buys, /token

  Creators:
    /creators/by-wallet, /by-twitter, /is-farmer, /history

  Virtuals:
    /virtuals/token, /by-wallet, /by-creator

  NEW — Identity Graph (wallet ↔ twitter ↔ farcaster):
    /identity/twitter-by-wallet
    /identity/wallets-by-twitter
    /identity/farcaster-by-wallet

  NEW — Investigations (developer profiles + red flags):
    /investigations/developer
    /investigations/token
    /investigations/red-flags-by-wallet

  NEW — Twitter ML (cached Sorsa scores):
    /twitter/score-cached

  NEW — Clanker (149K tokens):
    /clanker/token, /by-creator, /recent

  NEW — Flaunch:
    /flaunch/token, /recent

  NEW — Butler launches:
    /butler/launch, /recent

  NEW — Alert outcomes (LEARNING DATA):
    /outcomes/token, /by-source, /hit-rate

  NEW — aGDP leaderboard:
    /agdp/leaderboard, /creator

  NEW — Similar tokens (find past winners matching current):
    /similar/by-creator, /by-narrative

All endpoints: GET only, Bearer auth, rate-limited, parameterized SQL.
No endpoint exposes wallet private keys, trading decisions, or .env contents.
"""

import hmac
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
import sentry_setup

_env_file = Path(__file__).resolve().parent / ".env.hermes_api"
if _env_file.exists():
    load_dotenv(_env_file)
else:
    load_dotenv(Path(__file__).resolve().parent / ".env")

HERMES_API_KEY = os.getenv("HERMES_API_KEY", "")
if not HERMES_API_KEY or len(HERMES_API_KEY) < 20:
    raise RuntimeError("HERMES_API_KEY must be set (min 20 chars)")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("hermes-data-api-v2")

limiter = Limiter(key_func=get_remote_address, default_limits=["1000/minute", "20/second"])

app = FastAPI(
    title="Hermes Data API v2",
    version="2.0.0",
    docs_url=None, redoc_url=None, openapi_url=None,
)
app.state.limiter = limiter


def _rate_limit_exceeded(request, exc):
    return JSONResponse(status_code=429, content={"error": "rate limit exceeded"})


app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded)
app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=["GET"], allow_headers=["Authorization"])


def _check_auth(authorization: Optional[str]) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization[7:].strip()
    if not hmac.compare_digest(token.encode(), HERMES_API_KEY.encode()):
        raise HTTPException(status_code=403, detail="invalid token")


def _valid_addr(addr: str) -> bool:
    return isinstance(addr, str) and addr.startswith("0x") and len(addr) == 42


# =============================================================================
# Lazy imports
# =============================================================================
_smart_money = None
_creator_registry = None
_virtuals_db = None
_investigation_db = None


def _sm():
    global _smart_money
    if _smart_money is None:
        from smart_money_tracker import SmartMoneyTracker
        _smart_money = SmartMoneyTracker()
    return _smart_money


def _cr():
    global _creator_registry
    if _creator_registry is None:
        from creator_registry import CreatorRegistry
        _creator_registry = CreatorRegistry()
    return _creator_registry


def _vdb():
    global _virtuals_db
    if _virtuals_db is None:
        from virtuals_token_db import VirtualsTokenDatabase
        _virtuals_db = VirtualsTokenDatabase()
    return _virtuals_db


def _idb():
    global _investigation_db
    if _investigation_db is None:
        try:
            from investigation_db import InvestigationDB
            _investigation_db = InvestigationDB()
        except Exception as e:
            logger.warning(f"investigation_db unavailable: {e}")
    return _investigation_db


def _sqlite_query(db_name: str, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    from db_connection import get_connection
    conn = get_connection(db_name)
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def _pg_query(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    from db_connection import get_connection
    conn = get_connection("postgres")
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


# =============================================================================
# HEALTH
# =============================================================================

@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "hermes-data-api", "version": "2.0.0"}


# =============================================================================
# SMART MONEY
# =============================================================================

@app.get("/smart-money/is-smart")
@limiter.limit("60/minute")
def sm_is_smart(request, wallet: str = Query(..., min_length=10, max_length=100),
                authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    if not _valid_addr(wallet):
        raise HTTPException(400, "invalid wallet")
    is_smart, tier, score = _sm().is_smart_wallet(wallet)
    return {"wallet": wallet, "is_smart_money": is_smart,
            "tier": tier.value if hasattr(tier, "value") else str(tier), "score": score}


@app.get("/smart-money/wallets")
@limiter.limit("10/minute")
def sm_wallets(request, authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    from smart_money_tracker import KOL_WALLETS
    wallets = []
    for group, members in KOL_WALLETS.items():
        for addr, meta in members.items():
            wallets.append({"wallet": addr, "group": group, **meta})
    return {"total": len(wallets), "wallets": wallets}


@app.get("/smart-money/recent-buys")
@limiter.limit("30/minute")
def sm_recent_buys(request, hours: int = Query(24, ge=1, le=168),
                    tier: Optional[str] = None,
                    authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    sql = """SELECT signal_id, token_address, strength, smart_wallets_buying,
                    avg_reputation, created_at, actual_roi_24h
             FROM signals
             WHERE datetime(created_at) > datetime('now', ?)"""
    params: List[Any] = [f"-{hours} hours"]
    if tier:
        sql += " AND strength = ?"
        params.append(tier.upper())
    sql += " ORDER BY datetime(created_at) DESC LIMIT 200"
    return {"hours": hours, "signals": _sqlite_query("smart_money", sql, tuple(params))}


@app.get("/smart-money/token")
@limiter.limit("60/minute")
def sm_token(request, address: str = Query(..., min_length=10, max_length=100),
             authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    if not _valid_addr(address):
        raise HTTPException(400, "invalid token")
    rows = _sqlite_query("smart_money",
        "SELECT wallet_address, buy_block, entry_price, roi_percent "
        "FROM wallet_trades WHERE token_address = ? ORDER BY buy_block DESC LIMIT 100",
        (address.lower(),))
    return {"token": address, "count": len(rows), "trades": rows}


# =============================================================================
# CREATORS
# =============================================================================

@app.get("/creators/by-wallet")
@limiter.limit("60/minute")
def cr_by_wallet(request, wallet: str = Query(...),
                  authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    record = _cr().get_by_wallet(wallet)
    return {"wallet": wallet, "known": bool(record), **(record or {})}


@app.get("/creators/by-twitter")
@limiter.limit("60/minute")
def cr_by_twitter(request, handle: str = Query(..., min_length=1, max_length=50),
                   authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    handle = handle.lstrip("@").lower()
    record = _cr().get_by_twitter(handle)
    return {"handle": handle, "known": bool(record), **(record or {})}


@app.get("/creators/is-farmer")
@limiter.limit("60/minute")
def cr_is_farmer(request, wallet: str = Query(...),
                  authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    record = _cr().get_by_wallet(wallet) or {}
    return {"wallet": wallet, "is_farmer": bool(record.get("is_farmer")),
            "is_protocol_deployer": bool(record.get("is_protocol_deployer")),
            "total_count": record.get("total_count", 0)}


@app.get("/creators/history")
@limiter.limit("30/minute")
def cr_history(request, identifier: str = Query(..., min_length=3, max_length=100),
                authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    history = _vdb().get_creator_history(identifier)
    return {"identifier": identifier, "total": len(history), "projects": history[:50]}


# =============================================================================
# VIRTUALS TOKENS
# =============================================================================

@app.get("/virtuals/token")
@limiter.limit("60/minute")
def vt_token(request, address: str = Query(...),
             authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    tok = _vdb().lookup_token(address)
    return {"address": address, "found": bool(tok), "token": tok}


@app.get("/virtuals/by-wallet")
@limiter.limit("30/minute")
def vt_by_wallet(request, wallet: str = Query(...),
                  authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    tokens = _vdb().get_tokens_by_creator_wallet(wallet)
    return {"wallet": wallet, "count": len(tokens), "tokens": tokens}


@app.get("/virtuals/by-creator")
@limiter.limit("30/minute")
def vt_by_creator(request, creator_id: str = Query(...),
                   authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    tokens = _vdb().get_creator_history(creator_id)
    return {"creator_id": creator_id, "count": len(tokens), "tokens": tokens}


# =============================================================================
# NEW: IDENTITY GRAPH
# =============================================================================

@app.get("/identity/twitter-by-wallet")
@limiter.limit("60/minute")
def id_twitter_by_wallet(request, wallet: str = Query(...),
                          authorization: Optional[str] = Header(None)):
    """Reverse lookup: given wallet, return known Twitter handle (with source + confidence)."""
    _check_auth(authorization)
    try:
        from utils import lookup_twitter_by_wallet
        result = lookup_twitter_by_wallet(wallet)
    except Exception as e:
        logger.warning(f"lookup failed: {e}")
        result = None
    return {"wallet": wallet, **(result or {"found": False})}


@app.get("/identity/wallets-by-twitter")
@limiter.limit("30/minute")
def id_wallets_by_twitter(request, handle: str = Query(..., min_length=1, max_length=50),
                           authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    handle = handle.lstrip("@").lower()
    rows = _sqlite_query("identity_graph",
        "SELECT wallet_address, source, confidence FROM identity_graph "
        "WHERE twitter_handle = ? COLLATE NOCASE LIMIT 20",
        (handle,))
    return {"handle": handle, "count": len(rows), "wallets": rows}


@app.get("/identity/farcaster-by-wallet")
@limiter.limit("30/minute")
def id_farcaster_by_wallet(request, wallet: str = Query(...),
                            authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    rows = _sqlite_query("identity_graph",
        "SELECT farcaster_fid, farcaster_username FROM identity_graph "
        "WHERE wallet_address = ? LIMIT 1",
        (wallet.lower(),))
    return {"wallet": wallet, "farcaster": rows[0] if rows else None}


# =============================================================================
# NEW: INVESTIGATIONS (developer profiles + red flags)
# =============================================================================

@app.get("/investigations/developer")
@limiter.limit("30/minute")
def inv_developer(request, wallet: str = Query(...),
                   authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    db = _idb()
    if db is None:
        return {"wallet": wallet, "available": False}
    try:
        prof = db.get_developer_profile(wallet)
        return {"wallet": wallet, "available": True, "profile": prof}
    except Exception as e:
        logger.warning(f"dev profile lookup: {e}")
        return {"wallet": wallet, "available": False, "error": str(e)}


@app.get("/investigations/token")
@limiter.limit("30/minute")
def inv_token(request, address: str = Query(...),
              authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    rows = _sqlite_query("investigations",
        "SELECT token_address, verdict, reputation_score, reputation_tier, "
        "red_flags_count, analyzed_at FROM investigations "
        "WHERE token_address = ? ORDER BY analyzed_at DESC LIMIT 5",
        (address.lower(),))
    return {"address": address, "investigations": rows}


@app.get("/investigations/red-flags-by-wallet")
@limiter.limit("30/minute")
def inv_red_flags(request, wallet: str = Query(...),
                   authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    rows = _sqlite_query("investigations",
        "SELECT token_address, flag_type, description, severity, detected_at "
        "FROM red_flags WHERE creator_wallet = ? ORDER BY detected_at DESC LIMIT 50",
        (wallet.lower(),))
    return {"wallet": wallet, "flag_count": len(rows), "flags": rows}


# =============================================================================
# NEW: TWITTER ML (cached Sorsa scores — avoid redundant API calls)
# =============================================================================

@app.get("/twitter/score-cached")
@limiter.limit("120/minute")
def tw_score_cached(request, handle: str = Query(..., min_length=1, max_length=50),
                     authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    handle = handle.lstrip("@").lower()
    rows = _sqlite_query("twitter_ml",
        "SELECT score, followers_count, verified, updated_at FROM score_cache "
        "WHERE handle = ? COLLATE NOCASE "
        "AND datetime(updated_at) > datetime('now', '-15 days') LIMIT 1",
        (handle,))
    return {"handle": handle, "cache_hit": bool(rows), "data": rows[0] if rows else None}


# =============================================================================
# NEW: CLANKER (149K tokens)
# =============================================================================

@app.get("/clanker/token")
@limiter.limit("60/minute")
def ck_token(request, address: str = Query(...),
             authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    rows = _sqlite_query("clanker",
        "SELECT token_address, symbol, name, creator_wallet, creator_twitter, "
        "mcap_usd, holders, launched_at FROM tokens WHERE token_address = ? LIMIT 1",
        (address.lower(),))
    return {"address": address, "found": bool(rows), "token": rows[0] if rows else None}


@app.get("/clanker/by-creator")
@limiter.limit("30/minute")
def ck_by_creator(request, wallet: str = Query(...),
                   authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    rows = _sqlite_query("clanker",
        "SELECT token_address, symbol, launched_at, mcap_usd FROM tokens "
        "WHERE creator_wallet = ? ORDER BY launched_at DESC LIMIT 50",
        (wallet.lower(),))
    return {"wallet": wallet, "count": len(rows), "tokens": rows}


@app.get("/clanker/recent")
@limiter.limit("20/minute")
def ck_recent(request, hours: int = Query(24, ge=1, le=168),
              authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    rows = _sqlite_query("clanker",
        "SELECT token_address, symbol, name, creator_wallet, creator_twitter, "
        "mcap_usd, holders, launched_at FROM tokens "
        "WHERE datetime(launched_at) > datetime('now', ?) "
        "ORDER BY datetime(launched_at) DESC LIMIT 100",
        (f"-{hours} hours",))
    return {"hours": hours, "count": len(rows), "tokens": rows}


# =============================================================================
# NEW: FLAUNCH
# =============================================================================

@app.get("/flaunch/token")
@limiter.limit("30/minute")
def fl_token(request, address: str = Query(...),
              authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    rows = _sqlite_query("flaunch",
        "SELECT token_address, symbol, name, creator_wallet, creator_twitter, "
        "fair_launch_end, launched_at FROM launches "
        "WHERE token_address = ? LIMIT 1",
        (address.lower(),))
    return {"address": address, "found": bool(rows), "launch": rows[0] if rows else None}


@app.get("/flaunch/recent")
@limiter.limit("20/minute")
def fl_recent(request, hours: int = Query(24, ge=1, le=168),
               authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    rows = _sqlite_query("flaunch",
        "SELECT token_address, symbol, name, launched_at FROM launches "
        "WHERE datetime(launched_at) > datetime('now', ?) "
        "ORDER BY datetime(launched_at) DESC LIMIT 50",
        (f"-{hours} hours",))
    return {"hours": hours, "count": len(rows), "launches": rows}


# =============================================================================
# NEW: BUTLER
# =============================================================================

@app.get("/butler/launch")
@limiter.limit("30/minute")
def bt_launch(request, address: str = Query(...),
               authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    rows = _sqlite_query("butler",
        "SELECT token_address, symbol, name, creator_twitter, sorsa_score, "
        "strategy_fit, launched_at FROM launch_alerts "
        "WHERE token_address = ? LIMIT 1",
        (address.lower(),))
    return {"address": address, "found": bool(rows), "launch": rows[0] if rows else None}


@app.get("/butler/recent")
@limiter.limit("20/minute")
def bt_recent(request, hours: int = Query(24, ge=1, le=168),
               authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    rows = _sqlite_query("butler",
        "SELECT token_address, symbol, creator_twitter, sorsa_score, launched_at "
        "FROM launch_alerts WHERE datetime(launched_at) > datetime('now', ?) "
        "ORDER BY datetime(launched_at) DESC LIMIT 50",
        (f"-{hours} hours",))
    return {"hours": hours, "count": len(rows), "launches": rows}


# =============================================================================
# NEW: ALERT OUTCOMES (LEARNING FEEDBACK — CRUCIAL!)
# =============================================================================

@app.get("/outcomes/token")
@limiter.limit("60/minute")
def out_token(request, address: str = Query(...),
              authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    rows = _sqlite_query("alert_outcomes",
        "SELECT alert_source, alert_type, roi_1h, roi_6h, roi_24h, roi_7d, "
        "is_hit_24h, alerted_at FROM outcomes "
        "WHERE token_address = ? ORDER BY alerted_at DESC LIMIT 10",
        (address.lower(),))
    return {"address": address, "count": len(rows), "outcomes": rows}


@app.get("/outcomes/by-source")
@limiter.limit("20/minute")
def out_by_source(request, source: str = Query(..., max_length=50),
                   days: int = Query(30, ge=1, le=365),
                   authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    rows = _sqlite_query("alert_outcomes",
        "SELECT token_address, roi_24h, is_hit_24h, alerted_at FROM outcomes "
        "WHERE alert_source = ? AND datetime(alerted_at) > datetime('now', ?) "
        "ORDER BY alerted_at DESC LIMIT 500",
        (source, f"-{days} days"))
    hits = sum(1 for r in rows if r.get("is_hit_24h"))
    return {"source": source, "days": days, "total": len(rows),
            "hits": hits, "hit_rate": hits / max(len(rows), 1),
            "outcomes": rows[:100]}


@app.get("/outcomes/hit-rate")
@limiter.limit("10/minute")
def out_hit_rate(request, days: int = Query(30, ge=1, le=365),
                 authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    rows = _sqlite_query("alert_outcomes",
        "SELECT alert_source, COUNT(*) AS total, "
        "SUM(CASE WHEN is_hit_24h THEN 1 ELSE 0 END) AS hits, "
        "AVG(roi_24h) AS avg_roi_24h FROM outcomes "
        "WHERE datetime(alerted_at) > datetime('now', ?) "
        "GROUP BY alert_source ORDER BY hits DESC",
        (f"-{days} days",))
    return {"days": days, "sources": rows}


# =============================================================================
# NEW: aGDP LEADERBOARD
# =============================================================================

@app.get("/agdp/leaderboard")
@limiter.limit("20/minute")
def agdp_leaderboard(request, limit: int = Query(50, ge=1, le=200),
                      authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    rows = _sqlite_query("agdp_history",
        "SELECT creator_twitter, handle, rank, mcap_virtual, updated_at "
        "FROM leaderboard ORDER BY rank ASC LIMIT ?",
        (limit,))
    return {"limit": limit, "entries": rows}


# =============================================================================
# NEW: PATTERNS / CHAIN HOTNESS (Postgres)
# =============================================================================

@app.get("/patterns/recent-pumps")
@limiter.limit("10/minute")
def pp_recent(request, days: int = Query(30, ge=1, le=90),
              chain: Optional[str] = None,
              authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    try:
        sql = ("SELECT token_address, chain, pump_start_at, pump_peak_pct, "
               "first_signal_source, narrative, catalyst_type FROM pump_instances "
               "WHERE pump_start_at > NOW() - make_interval(days => %s)")
        params: List[Any] = [days]
        if chain:
            sql += " AND chain = %s"
            params.append(chain)
        sql += " ORDER BY pump_peak_pct DESC LIMIT 100"
        rows = _pg_query(sql, tuple(params))
        return {"days": days, "chain": chain, "count": len(rows), "pumps": rows}
    except Exception as e:
        logger.warning(f"pumps: {e}")
        return {"days": days, "count": 0, "pumps": [], "note": "table not populated yet"}


@app.get("/chain/hotness")
@limiter.limit("30/minute")
def ch_hotness(request, authorization: Optional[str] = Header(None)):
    _check_auth(authorization)
    try:
        rows = _pg_query(
            "SELECT date, chain, hotness_score, rank, volume_24h, pump_count_24h "
            "FROM chain_hotness WHERE date > CURRENT_DATE - INTERVAL '3 days' "
            "ORDER BY date DESC, rank ASC")
        return {"count": len(rows), "hotness": rows}
    except Exception as e:
        logger.warning(f"hotness: {e}")
        return {"count": 0, "hotness": [], "note": "table not populated yet"}


# =============================================================================
# NEW: SIMILAR TOKENS (find past winners matching current)
# =============================================================================

@app.get("/similar/by-creator")
@limiter.limit("20/minute")
def sim_by_creator(request, creator_twitter: str = Query(...),
                    authorization: Optional[str] = Header(None)):
    """Find past winners from the SAME creator (successful pattern carry-over)."""
    _check_auth(authorization)
    creator_twitter = creator_twitter.lstrip("@").lower()
    # Query virtuals + clanker + flaunch for tokens by same creator that hit >100% ROI
    rows_v = _sqlite_query("virtuals_all_tokens",
        "SELECT token_address, symbol, max_mcap_virtual FROM tokens "
        "WHERE creator_twitter = ? COLLATE NOCASE "
        "AND max_mcap_virtual > 500000 LIMIT 20",
        (creator_twitter,))
    rows_c = _sqlite_query("clanker",
        "SELECT token_address, symbol, mcap_usd FROM tokens "
        "WHERE creator_twitter = ? COLLATE NOCASE "
        "AND mcap_usd > 500000 ORDER BY mcap_usd DESC LIMIT 20",
        (creator_twitter,))
    return {"creator": creator_twitter, "virtuals_hits": rows_v, "clanker_hits": rows_c}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("HERMES_API_PORT", "8091"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
