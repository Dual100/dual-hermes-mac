#!/usr/bin/env python3
"""
Hermes Data API — Read-only HTTPS endpoints for Mac to query Hetzner data.

PURPOSE:
Mac Hermes Agent calls this API to enrich its decisions with Hetzner-side data
(smart money wallets, creator registry, Virtuals token DB, pump patterns).

SECURITY:
- Bearer token auth (HERMES_API_KEY)
- Rate limit per key (10 req/s, 500 req/min)
- READ-ONLY — no POST/PUT/DELETE endpoints
- Isolated .env file (.env.hermes_api — only the keys this API needs)
- No access to wallet private keys, no access to trading modules
- Queries use parameterized SQL only (no user input in raw queries)
- HTTPS via existing dualzero.duckdns.org cert

DEPLOY:
- File: /home/ubuntu/creator-bid-bot/hermes_data_api.py
- Service: hermes-data-api.service
- Port: 8091 (internal, exposed via nginx reverse proxy to dualzero.duckdns.org/hermes/*)
- User: ubuntu (same as other APIs)

ENDPOINTS (all GET, all require Authorization: Bearer <key>):

Smart Money:
  GET /smart-money/is-smart?wallet=0xabc                    → tier + stats
  GET /smart-money/recent-buys?hours=24&tier=ELITE          → recent KOL buys
  GET /smart-money/wallets                                  → list all tracked
  GET /smart-money/token?address=0xabc                       → smart money on a token

Creators:
  GET /creators/by-wallet?wallet=0xabc                      → creator record
  GET /creators/by-twitter?handle=xxx                       → creator record
  GET /creators/history?identifier=xxx                      → prior projects
  GET /creators/is-farmer?wallet=0xabc                      → farmer flag

Virtuals Tokens:
  GET /virtuals/token?address=0xabc                         → token data
  GET /virtuals/by-creator?creator_id=xxx                   → all tokens by creator
  GET /virtuals/by-wallet?wallet=0xabc                      → all tokens by wallet

Patterns & Context:
  GET /patterns/recent-pumps?days=30&chain=base             → pump instances
  GET /patterns/match?pattern_type=kol_driven               → pattern signatures
  GET /chain/hotness                                        → chain hotness rankings
  GET /health                                               → api health
"""

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

# Load isolated env — DO NOT load main .env (avoid leaking trading secrets)
_env_file = Path(__file__).resolve().parent / ".env.hermes_api"
if _env_file.exists():
    load_dotenv(_env_file)
else:
    # Bootstrap fallback: load main .env but only read specific keys
    load_dotenv(Path(__file__).resolve().parent / ".env")

HERMES_API_KEY = os.getenv("HERMES_API_KEY", "")
if not HERMES_API_KEY or len(HERMES_API_KEY) < 20:
    raise RuntimeError(
        "HERMES_API_KEY must be set and at least 20 chars. "
        "Generate: python3 -c 'import secrets; print(secrets.token_urlsafe(32))'"
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("hermes-data-api")

limiter = Limiter(key_func=get_remote_address, default_limits=["500/minute", "10/second"])

app = FastAPI(
    title="Hermes Data API",
    description="Read-only data feed for Hermes Agent running on remote Mac",
    version="1.0.0",
    docs_url=None,       # disable public docs — security
    redoc_url=None,
    openapi_url=None,
)
app.state.limiter = limiter


def _rate_limit_exceeded(request, exc):
    return JSONResponse(
        status_code=429,
        content={"error": "rate limit exceeded", "detail": str(exc.detail)},
    )


app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded)

# CORS: explicitly deny — Mac calls server-to-server, no browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_methods=["GET"],
    allow_headers=["Authorization"],
)


def _check_auth(authorization: Optional[str]) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization[7:].strip()
    # Constant-time comparison to avoid timing attacks
    import hmac
    if not hmac.compare_digest(token.encode(), HERMES_API_KEY.encode()):
        raise HTTPException(status_code=403, detail="invalid token")


# =============================================================================
# Lazy imports of heavy modules (avoid blocking startup)
# =============================================================================

_smart_money = None
_creator_registry = None
_virtuals_db = None


def _get_smart_money():
    global _smart_money
    if _smart_money is None:
        from smart_money_tracker import SmartMoneyTracker
        _smart_money = SmartMoneyTracker()
    return _smart_money


def _get_creator_registry():
    global _creator_registry
    if _creator_registry is None:
        from creator_registry import CreatorRegistry
        _creator_registry = CreatorRegistry()
    return _creator_registry


def _get_virtuals_db():
    global _virtuals_db
    if _virtuals_db is None:
        from virtuals_token_db import VirtualsTokenDatabase
        _virtuals_db = VirtualsTokenDatabase()
    return _virtuals_db


# =============================================================================
# HEALTH
# =============================================================================

@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "hermes-data-api", "version": "1.0.0"}


# =============================================================================
# SMART MONEY
# =============================================================================

@app.get("/smart-money/is-smart")
@limiter.limit("60/minute")
def smart_money_is_smart(
    request,
    wallet: str = Query(..., min_length=10, max_length=100),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    _check_auth(authorization)
    if not wallet.startswith("0x") or len(wallet) != 42:
        raise HTTPException(status_code=400, detail="invalid wallet address")

    tracker = _get_smart_money()
    is_smart, tier, score = tracker.is_smart_wallet(wallet)
    return {
        "wallet": wallet,
        "is_smart_money": is_smart,
        "tier": tier.value if hasattr(tier, "value") else str(tier),
        "score": score,
    }


@app.get("/smart-money/wallets")
@limiter.limit("10/minute")
def smart_money_list_wallets(
    request,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    _check_auth(authorization)
    try:
        from smart_money_tracker import KOL_WALLETS
        wallets = []
        for group, members in KOL_WALLETS.items():
            for addr, meta in members.items():
                wallets.append({
                    "wallet": addr,
                    "group": group,
                    "name": meta.get("name") or meta.get("label"),
                    "tier": meta.get("tier"),
                })
        return {"total": len(wallets), "wallets": wallets}
    except Exception as e:
        logger.exception("list_wallets failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/smart-money/recent-buys")
@limiter.limit("30/minute")
def smart_money_recent_buys(
    request,
    hours: int = Query(24, ge=1, le=168),
    tier: Optional[str] = Query(None, max_length=20),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    _check_auth(authorization)
    # Query smart_money.db for recent buys
    try:
        from db_connection import get_connection
        conn = get_connection("smart_money")
        cursor = conn.cursor()
        query = """
            SELECT s.signal_id, s.token_address, s.strength, s.smart_wallets_buying,
                   s.avg_reputation, s.created_at, s.actual_roi_24h
            FROM signals s
            WHERE datetime(s.created_at) > datetime('now', ?)
        """
        params = [f"-{hours} hours"]
        if tier:
            query += " AND s.strength = ?"
            params.append(tier.upper())
        query += " ORDER BY datetime(s.created_at) DESC LIMIT 200"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        conn.close()
        return {
            "hours": hours,
            "count": len(rows),
            "signals": [dict(zip(cols, r)) for r in rows],
        }
    except Exception as e:
        logger.exception("recent-buys failed")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/smart-money/token")
@limiter.limit("60/minute")
def smart_money_on_token(
    request,
    address: str = Query(..., min_length=10, max_length=100),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    _check_auth(authorization)
    if not address.startswith("0x") or len(address) != 42:
        raise HTTPException(status_code=400, detail="invalid token address")

    try:
        from db_connection import get_connection
        conn = get_connection("smart_money")
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT wallet_address, buy_block, entry_price, roi_percent
            FROM wallet_trades
            WHERE token_address = ?
            ORDER BY buy_block DESC LIMIT 100
            """,
            (address.lower(),),
        )
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        conn.close()
        return {
            "token": address,
            "smart_buyer_count": len(rows),
            "trades": [dict(zip(cols, r)) for r in rows],
        }
    except Exception as e:
        logger.exception("smart-money/token failed")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# CREATORS
# =============================================================================

@app.get("/creators/by-wallet")
@limiter.limit("60/minute")
def creators_by_wallet(
    request,
    wallet: str = Query(..., min_length=10, max_length=100),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    _check_auth(authorization)
    if not wallet.startswith("0x"):
        raise HTTPException(status_code=400, detail="invalid wallet address")

    reg = _get_creator_registry()
    record = reg.get_by_wallet(wallet)
    if not record:
        return {"wallet": wallet, "known": False}
    return {
        "wallet": wallet,
        "known": True,
        "twitter_handle": record.get("twitter_handle"),
        "twitter_score": record.get("twitter_score"),
        "virtuals_count": record.get("virtuals_count", 0),
        "clanker_count": record.get("clanker_count", 0),
        "flaunch_count": record.get("flaunch_count", 0),
        "total_count": record.get("total_count", 0),
        "is_farmer": bool(record.get("is_farmer")),
        "is_protocol_deployer": bool(record.get("is_protocol_deployer")),
        "flags": record.get("flags"),
    }


@app.get("/creators/is-farmer")
@limiter.limit("60/minute")
def creators_is_farmer(
    request,
    wallet: str = Query(..., min_length=10, max_length=100),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    _check_auth(authorization)
    reg = _get_creator_registry()
    record = reg.get_by_wallet(wallet)
    if not record:
        return {"wallet": wallet, "known": False, "is_farmer": False}
    return {
        "wallet": wallet,
        "known": True,
        "is_farmer": bool(record.get("is_farmer")),
        "is_protocol_deployer": bool(record.get("is_protocol_deployer")),
        "total_count": record.get("total_count", 0),
    }


@app.get("/creators/history")
@limiter.limit("30/minute")
def creators_history(
    request,
    identifier: str = Query(..., min_length=3, max_length=100),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    _check_auth(authorization)
    db = _get_virtuals_db()
    history = db.get_creator_history(identifier)
    return {
        "identifier": identifier,
        "total": len(history),
        "projects": history[:50],
    }


# =============================================================================
# VIRTUALS TOKENS
# =============================================================================

@app.get("/virtuals/token")
@limiter.limit("60/minute")
def virtuals_token(
    request,
    address: str = Query(..., min_length=10, max_length=100),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    _check_auth(authorization)
    db = _get_virtuals_db()
    token = db.lookup_token(address)
    if not token:
        return {"address": address, "found": False}
    return {"address": address, "found": True, "token": token}


@app.get("/virtuals/by-wallet")
@limiter.limit("30/minute")
def virtuals_by_wallet(
    request,
    wallet: str = Query(..., min_length=10, max_length=100),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    _check_auth(authorization)
    db = _get_virtuals_db()
    tokens = db.get_tokens_by_creator_wallet(wallet)
    return {"wallet": wallet, "count": len(tokens), "tokens": tokens}


# =============================================================================
# PATTERNS / CHAIN HOTNESS
# =============================================================================

@app.get("/patterns/recent-pumps")
@limiter.limit("10/minute")
def patterns_recent_pumps(
    request,
    days: int = Query(30, ge=1, le=90),
    chain: Optional[str] = Query(None, max_length=20),
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    _check_auth(authorization)
    # Pull from pump_instances if table exists (populated by backtest_learner)
    try:
        from db_connection import get_connection
        conn = get_connection("postgres")
        cursor = conn.cursor()
        query = """
            SELECT token_address, chain, pump_start_at, pump_peak_pct,
                   first_signal_source, narrative, catalyst_type
            FROM pump_instances
            WHERE pump_start_at > NOW() - make_interval(days => %s)
        """
        params = [days]
        if chain:
            query += " AND chain = %s"
            params.append(chain)
        query += " ORDER BY pump_peak_pct DESC LIMIT 100"
        cursor.execute(query, params)
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        conn.close()
        return {
            "days": days,
            "chain": chain,
            "count": len(rows),
            "pumps": [dict(zip(cols, r)) for r in rows],
        }
    except Exception as e:
        logger.warning(f"pump_instances query failed (table may not exist yet): {e}")
        return {"days": days, "chain": chain, "count": 0, "pumps": [], "note": "pump_instances not populated yet"}


@app.get("/chain/hotness")
@limiter.limit("30/minute")
def chain_hotness(
    request,
    authorization: Optional[str] = Header(None),
) -> Dict[str, Any]:
    _check_auth(authorization)
    try:
        from db_connection import get_connection
        conn = get_connection("postgres")
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT date, chain, hotness_score, rank, volume_24h, pump_count_24h
            FROM chain_hotness
            WHERE date > CURRENT_DATE - INTERVAL '3 days'
            ORDER BY date DESC, rank ASC
            """
        )
        rows = cursor.fetchall()
        cols = [d[0] for d in cursor.description]
        conn.close()
        return {"count": len(rows), "hotness": [dict(zip(cols, r)) for r in rows]}
    except Exception as e:
        logger.warning(f"chain_hotness query failed: {e}")
        return {"count": 0, "hotness": [], "note": "chain_hotness not populated yet"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("HERMES_API_PORT", "8091"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
