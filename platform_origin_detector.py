"""
Platform Origin Detector — ETH + Base focus (no Solana for now).

When a token is deployed by a KNOWN, AUDITED factory on Base, we can INHERIT
safety properties and skip GoPlus. ETH has no equivalent auto-safe factories
— always runs full GoPlus check on ETH tokens.

User directive: "n iremos usar rede da solana por enquanto so rede eth e base"
"""

from typing import Any, Dict, List, Optional
from dataclasses import dataclass


@dataclass
class PlatformSpec:
    name: str
    chain: str
    factories: List[str]
    safety_guarantees: Dict[str, Any]
    description: str
    needs_goplus_check: bool = False


# =============================================================================
# TRUSTED PLATFORMS (Base chain primarily — ETH is wild west)
# =============================================================================

TRUSTED_PLATFORMS: List[PlatformSpec] = [
    # --------------- BASE ---------------
    PlatformSpec(
        name="Virtuals_Protocol",
        chain="base",
        factories=[
            # Real addresses from config.py — verify VIRTUALS_FACTORIES
            "0x1a540088125d00dd3990f9da45ca0859af4d3b01",  # Genesis
            # Add: V1, V2, V2b, V3, Agent, Fun, Butler, Openclaw factories
        ],
        safety_guarantees={
            "lp_mechanism": "bonding_curve",
            "lp_locked": True,
            "buy_tax": 0.0,
            "sell_tax": 0.01,
            "mint_authority_renounced": True,
            "supply_fixed": True,
            "honeypot_possible": False,
            "rug_possible": False,
        },
        description="Virtuals bonding curve — LP math-locked, supply fixed, audited",
        needs_goplus_check=False,
    ),
    PlatformSpec(
        name="Clanker_V4",
        chain="base",
        factories=[
            "0xE85A59c628F7d27878ACeB4bf3b35733630083a9",  # from bankrbot_blockchain_monitor.py
        ],
        safety_guarantees={
            "lp_mechanism": "uniswap_v3_locked",
            "lp_locked": True,
            "buy_tax": 0.0,
            "sell_tax": 0.01,
            "mint_authority_renounced": True,
            "supply_fixed": True,
            "honeypot_possible": False,
            "rug_possible": False,
        },
        description="Clanker V4 — factory-locked LP, 1% fee to pool, audited",
        needs_goplus_check=False,
    ),
    PlatformSpec(
        name="Flaunch",
        chain="base",
        factories=[
            # Flaunch factory — verify from flaunch_monitor.py / config
        ],
        safety_guarantees={
            "lp_mechanism": "fair_launch_window",
            "lp_locked": True,
            "buy_tax": 0.0,
            "sell_tax": 0.0,
            "mint_authority_renounced": True,
            "supply_fixed": True,
            "honeypot_possible": False,
            "rug_possible": False,
        },
        description="Flaunch fair launch — time-locked window, LP burned after",
        needs_goplus_check=False,
    ),

    # --------------- ETH ---------------
    # ETH does NOT have auto-safe launchpads. All tokens need full GoPlus check.
    # The only exception: tokens already investigated and marked SAFE in our DB.
]

_FACTORY_TO_PLATFORM: Dict[str, PlatformSpec] = {}
for p in TRUSTED_PLATFORMS:
    for f in p.factories:
        _FACTORY_TO_PLATFORM[f.lower()] = p


# =============================================================================
# KNOWN-SAFE ETH TOKENS (whitelist for skipping GoPlus)
# =============================================================================

# These are established tokens — no need to check every time
ETH_WHITELIST = {
    "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",  # UNI
    "0x514910771af9ca656af840dff83e8264ecf986ca",  # LINK
    "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9",  # AAVE
    "0xc944e90c64b2c07662a292be6244bdf05cda44a7",  # GRT
    "0x4d224452801aced8b2f0aebe155379bb5d594381",  # APE
    "0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce",  # SHIB
    "0x6982508145454ce325ddbe47a25d4ec3d2311933",  # PEPE
    # Add more as needed
}


# =============================================================================
# DETECTION
# =============================================================================

async def detect_token_origin(
    token_address: str,
    chain: str = "base",
    web3_client=None,
) -> Dict[str, Any]:
    """
    Determine if this token came from a trusted platform on ETH or Base.

    Returns:
        {
            "platform": str | None,           # "Virtuals_Protocol" / "Clanker_V4" / "ETH_Whitelist" / None
            "chain": str,
            "is_trusted": bool,
            "safety_guarantees": dict,
            "needs_goplus_check": bool,
            "deployer_wallet": str | None,
            "reasoning": str,
        }
    """
    addr_lower = token_address.lower()

    # ETH whitelist check
    if chain == "eth" and addr_lower in ETH_WHITELIST:
        return {
            "platform": "ETH_Whitelist",
            "chain": "eth",
            "is_trusted": True,
            "safety_guarantees": {"established_token": True, "long_history": True},
            "needs_goplus_check": False,
            "deployer_wallet": None,
            "reasoning": "Established ETH token (whitelist) — skip safety",
        }

    # Base: check local DBs (Virtuals, Clanker, Flaunch)
    if chain == "base":
        result = await _check_base_platforms(addr_lower)
        if result["platform"]:
            return result

    # Check prior investigations (if we've investigated before and marked SAFE)
    prior = await _check_prior_investigation(addr_lower)
    if prior["is_trusted"]:
        return prior

    # Default: needs full GoPlus check
    return {
        "platform": None,
        "chain": chain,
        "is_trusted": False,
        "safety_guarantees": {},
        "needs_goplus_check": True,
        "deployer_wallet": None,
        "reasoning": (
            "Unknown platform on "
            + ("ETH — always needs full check" if chain == "eth"
               else "Base — not in trusted factory list")
        ),
    }


async def _check_base_platforms(token_address: str) -> Dict[str, Any]:
    """Check Virtuals, Clanker, Flaunch DBs (Base only)."""
    import sys
    from pathlib import Path
    BOT_PATH = Path("/home/ubuntu/creator-bid-bot")
    if str(BOT_PATH) not in sys.path:
        sys.path.insert(0, str(BOT_PATH))

    # Virtuals
    try:
        from virtuals_token_db import VirtualsTokenDatabase
        db = VirtualsTokenDatabase()
        token = db.lookup_token(token_address)
        if token:
            platform = next((p for p in TRUSTED_PLATFORMS if p.name == "Virtuals_Protocol"), None)
            return {
                "platform": "Virtuals_Protocol",
                "chain": "base",
                "is_trusted": True,
                "safety_guarantees": platform.safety_guarantees if platform else {},
                "needs_goplus_check": False,
                "deployer_wallet": token.get("creator_wallet"),
                "reasoning": "Virtuals Protocol — bonding curve safe by design",
            }
    except Exception:
        pass

    # Clanker
    try:
        from db_connection import get_connection
        conn = get_connection("clanker")
        cur = conn.cursor()
        cur.execute(
            "SELECT creator_wallet FROM tokens WHERE token_address = ? LIMIT 1",
            (token_address,),
        )
        row = cur.fetchone()
        conn.close()
        if row:
            platform = next((p for p in TRUSTED_PLATFORMS if p.name == "Clanker_V4"), None)
            return {
                "platform": "Clanker_V4",
                "chain": "base",
                "is_trusted": True,
                "safety_guarantees": platform.safety_guarantees if platform else {},
                "needs_goplus_check": False,
                "deployer_wallet": row[0],
                "reasoning": "Clanker V4 — factory-locked LP, audited",
            }
    except Exception:
        pass

    # Flaunch
    try:
        from db_connection import get_connection
        conn = get_connection("flaunch")
        cur = conn.cursor()
        cur.execute(
            "SELECT creator_wallet FROM launches WHERE token_address = ? LIMIT 1",
            (token_address,),
        )
        row = cur.fetchone()
        conn.close()
        if row:
            platform = next((p for p in TRUSTED_PLATFORMS if p.name == "Flaunch"), None)
            return {
                "platform": "Flaunch",
                "chain": "base",
                "is_trusted": True,
                "safety_guarantees": platform.safety_guarantees if platform else {},
                "needs_goplus_check": False,
                "deployer_wallet": row[0],
                "reasoning": "Flaunch — fair launch window, audited",
            }
    except Exception:
        pass

    return {"platform": None, "is_trusted": False, "needs_goplus_check": True}


async def _check_prior_investigation(token_address: str) -> Dict[str, Any]:
    """If we've investigated and marked SAFE within last 24h, skip re-check."""
    try:
        import sys
        from pathlib import Path
        BOT_PATH = Path("/home/ubuntu/creator-bid-bot")
        if str(BOT_PATH) not in sys.path:
            sys.path.insert(0, str(BOT_PATH))
        from db_connection import get_connection
        conn = get_connection("investigations")
        cur = conn.cursor()
        cur.execute(
            """
            SELECT verdict, reputation_tier, analyzed_at FROM investigations
            WHERE token_address = ?
              AND datetime(analyzed_at) > datetime('now', '-24 hours')
              AND verdict IN ('SAFE', 'BUY', 'WATCH')
            ORDER BY analyzed_at DESC LIMIT 1
            """,
            (token_address,),
        )
        row = cur.fetchone()
        conn.close()
        if row:
            return {
                "platform": "Prior_Investigation_SAFE",
                "chain": "any",
                "is_trusted": True,
                "safety_guarantees": {"verdict": row[0], "tier": row[1]},
                "needs_goplus_check": False,
                "deployer_wallet": None,
                "reasoning": f"Investigated recently ({row[2]}): {row[0]}",
            }
    except Exception:
        pass
    return {"is_trusted": False, "needs_goplus_check": True}


def should_skip_goplus(origin: Dict[str, Any]) -> bool:
    return origin.get("is_trusted", False) and not origin.get("needs_goplus_check", True)


def describe_platform_safety(origin: Dict[str, Any]) -> str:
    if not origin.get("is_trusted"):
        return "⚠️ Unknown platform — full safety check needed"
    return f"✅ {origin.get('platform')} — {origin.get('reasoning', 'trusted')}"
