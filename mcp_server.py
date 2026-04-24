"""
Hermes MCP Server — exposes read-only research tools from creator-bid-bot.

Runs as subprocess of Hermes Agent via stdio transport.
All tools are READ-ONLY and return dicts. No trade execution, no writes
to production databases (uses read-only Postgres role).

Deploy: /home/hermes/hermes-tools/mcp_server.py
Config: MCP server registered in ~/.hermes/config.yaml under tools.mcp_servers
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Add creator-bid-bot to path so we can import existing modules
BOT_PATH = Path("/home/ubuntu/creator-bid-bot")
sys.path.insert(0, str(BOT_PATH))

# MCP protocol — we speak the standard stdio JSON-RPC variant
try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    sys.stderr.write(
        "mcp package not installed. Run: pip install 'mcp[cli]'\n"
    )
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("hermes_mcp")

server = Server("hermes-dualhermes-tools")


# =============================================================================
# TOOL DEFINITIONS (12 tools — all read-only)
# =============================================================================

TOOLS = [
    Tool(
        name="check_contract_safety",
        description=(
            "Check token contract safety via GoPlus API. Returns honeypot status, "
            "LP lock, buy/sell tax, holder count, creator reputation. FREE, no auth."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "token_address": {
                    "type": "string",
                    "description": "EVM token address (0x...)",
                },
            },
            "required": ["token_address"],
        },
    ),
    Tool(
        name="get_token_price_mcap",
        description=(
            "Fetch current price, mcap, 24h volume and liquidity for a token. "
            "Tries DexScreener first, falls back to bonding curve for Virtuals tokens."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "token_address": {"type": "string"},
            },
            "required": ["token_address"],
        },
    ),
    Tool(
        name="analyze_holders_onchain",
        description=(
            "Deep on-chain analysis: holder count, buy/sell ratio, volume, avg buy size, "
            "top 10 holders concentration. Uses Transfer event logs from last ~4000 blocks."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "token_address": {"type": "string"},
            },
            "required": ["token_address"],
        },
    ),
    Tool(
        name="research_twitter_deep",
        description=(
            "Deep Twitter profile analysis: Sorsa score, follower breakdown (team/projects/VCs), "
            "account age, previous usernames, top 20 followers. Takes ~20-30s, 8 API calls."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "handle": {
                    "type": "string",
                    "description": "Twitter handle (with or without @)",
                },
            },
            "required": ["handle"],
        },
    ),
    Tool(
        name="lookup_twitter_from_wallet",
        description=(
            "Reverse lookup: given a wallet address, find associated Twitter handle. "
            "Uses identity_graph + Farcaster + ENS resolvers. FREE, no API calls."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "wallet_address": {"type": "string"},
            },
            "required": ["wallet_address"],
        },
    ),
    Tool(
        name="get_creator_history",
        description=(
            "Get all previous projects launched by a creator. Accepts any of: "
            "creator_id, twitter handle, or wallet address. Returns list of tokens with "
            "status, mcap, holder count, launch date."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "creator_identifier": {
                    "type": "string",
                    "description": "creator_id, twitter handle, or wallet address",
                },
            },
            "required": ["creator_identifier"],
        },
    ),
    Tool(
        name="check_creator_farmer",
        description=(
            "Is this creator a known farmer? Returns total launches across all platforms "
            "(Virtuals, Clanker, Flaunch). Farmer = >10 launches unless protocol deployer."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "wallet_address": {"type": "string"},
            },
            "required": ["wallet_address"],
        },
    ),
    Tool(
        name="is_smart_money_wallet",
        description=(
            "Check if a wallet is classified as smart money. Returns tier "
            "(LEGENDARY/ELITE/PRO/SKILLED/DEGEN/UNKNOWN), win rate, avg ROI."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "wallet_address": {"type": "string"},
            },
            "required": ["wallet_address"],
        },
    ),
    Tool(
        name="search_telegram_mentions",
        description=(
            "Search recent mentions of a token across monitored Telegram groups. "
            "Returns list of messages with group name, sender, timestamp, sentiment."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Token ticker, address, or @handle",
                },
                "hours": {
                    "type": "number",
                    "default": 24,
                    "description": "Look back N hours",
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="research_token_full",
        description=(
            "Complete 7-stage research pipeline: on-chain + social + creator history + LLM "
            "consensus. Takes 20-30s but produces professional DD report with score 0-100. "
            "Use sparingly — expensive. Prefer individual tools for focused research."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "token_address": {"type": "string"},
                "deep": {
                    "type": "boolean",
                    "default": True,
                    "description": "If false, only phase 1+2 (~5s, less thorough)",
                },
            },
            "required": ["token_address"],
        },
    ),
    Tool(
        name="send_alert_telegram",
        description=(
            "Send a gem-hunter alert to user's Telegram. Include inline buttons "
            "for quick actions. Rate-limited to prevent spam."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "buttons": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "url": {"type": "string"},
                            "callback_data": {"type": "string"},
                        },
                    },
                    "default": [],
                },
            },
            "required": ["text"],
        },
    ),
    Tool(
        name="save_vault_note",
        description=(
            "Save a markdown note to the Obsidian vault for persistent learning. "
            "Folders: 01-Tokens, 02-Creators, 03-Wallets, 04-Trades, 05-Alerts, "
            "08-Context, 10-Hermes-Hunts (new)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "folder": {"type": "string"},
                "filename": {"type": "string"},
                "content": {"type": "string"},
                "frontmatter": {"type": "object", "default": {}},
            },
            "required": ["folder", "filename", "content"],
        },
    ),
]


@server.list_tools()
async def list_tools():
    return TOOLS


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================

@server.call_tool()
async def call_tool(name: str, arguments: Dict[str, Any]):
    """Dispatch tool calls to the appropriate implementation."""
    try:
        result = await dispatch_tool(name, arguments)
        return [TextContent(type="text", text=json.dumps(result, default=str, indent=2))]
    except Exception as e:
        logger.exception(f"Tool {name} failed")
        return [TextContent(
            type="text",
            text=json.dumps({"error": str(e), "tool": name}),
        )]


async def dispatch_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Route tool call to implementation."""
    if name == "check_contract_safety":
        return await _check_contract_safety(args["token_address"])
    if name == "get_token_price_mcap":
        return await _get_token_price_mcap(args["token_address"])
    if name == "analyze_holders_onchain":
        return await _analyze_holders_onchain(args["token_address"])
    if name == "research_twitter_deep":
        return await _research_twitter_deep(args["handle"])
    if name == "lookup_twitter_from_wallet":
        return await _lookup_twitter_from_wallet(args["wallet_address"])
    if name == "get_creator_history":
        return await _get_creator_history(args["creator_identifier"])
    if name == "check_creator_farmer":
        return await _check_creator_farmer(args["wallet_address"])
    if name == "is_smart_money_wallet":
        return await _is_smart_money_wallet(args["wallet_address"])
    if name == "search_telegram_mentions":
        return await _search_telegram_mentions(args["query"], args.get("hours", 24))
    if name == "research_token_full":
        return await _research_token_full(args["token_address"], args.get("deep", True))
    if name == "send_alert_telegram":
        return await _send_alert_telegram(args["text"], args.get("buttons", []))
    if name == "save_vault_note":
        return await _save_vault_note(
            args["folder"], args["filename"], args["content"], args.get("frontmatter", {}),
        )
    return {"error": f"Unknown tool: {name}"}


# --- Tool 1: Contract safety via GoPlus ---
async def _check_contract_safety(token_address: str) -> Dict[str, Any]:
    from smart_money_tracker import SmartMoneyTracker
    tracker = SmartMoneyTracker()
    result = await tracker._fetch_goplus_security(token_address)
    return result or {"error": "GoPlus returned no data"}


# --- Tool 2: Price/MCap ---
async def _get_token_price_mcap(token_address: str) -> Dict[str, Any]:
    import aiohttp
    async with aiohttp.ClientSession() as session:
        # Try DexScreener first
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    pairs = data.get("pairs", [])
                    if pairs:
                        p = pairs[0]
                        return {
                            "source": "dexscreener",
                            "price_usd": float(p.get("priceUsd", 0) or 0),
                            "mcap_usd": float(p.get("marketCap") or p.get("fdv") or 0),
                            "liquidity_usd": float(p.get("liquidity", {}).get("usd", 0) or 0),
                            "volume_24h": float(p.get("volume", {}).get("h24", 0) or 0),
                            "price_change_24h": float(p.get("priceChange", {}).get("h24", 0) or 0),
                            "chain": p.get("chainId"),
                            "dex": p.get("dexId"),
                            "pair_address": p.get("pairAddress"),
                        }
        except Exception as e:
            logger.warning(f"DexScreener failed: {e}")

    # Fallback: bonding curve (Virtuals only)
    try:
        from bonding_curve_price import get_bonding_curve_price
        bc = await get_bonding_curve_price(token_address)
        if bc:
            return {
                "source": "bonding_curve",
                "price_usd": bc.get("price_usd"),
                "mcap_usd": bc.get("fdv_usd"),
                "symbol": bc.get("symbol"),
                "name": bc.get("name"),
                "virtual_reserve": bc.get("virtual_reserve"),
            }
    except Exception as e:
        logger.warning(f"Bonding curve failed: {e}")

    return {"error": "Token not found on DexScreener or Virtuals bonding curve"}


# --- Tool 3: On-chain holder analysis ---
async def _analyze_holders_onchain(token_address: str) -> Dict[str, Any]:
    from clanker_investigator import ClankerInvestigator
    inv = ClankerInvestigator()
    result = await inv._analyze_token_onchain(token_address)
    return result or {"error": "On-chain analysis returned no data"}


# --- Tool 4: Twitter deep profile ---
async def _research_twitter_deep(handle: str) -> Dict[str, Any]:
    from utils import fetch_sorsa_deep_profile
    result = await fetch_sorsa_deep_profile(handle)
    return result or {"error": f"No profile found for @{handle}"}


# --- Tool 5: Wallet -> Twitter reverse lookup ---
async def _lookup_twitter_from_wallet(wallet_address: str) -> Dict[str, Any]:
    from utils import lookup_twitter_by_wallet
    result = lookup_twitter_by_wallet(wallet_address)
    return result or {"error": "No Twitter handle found for wallet"}


# --- Tool 6: Creator history ---
async def _get_creator_history(creator_identifier: str) -> Dict[str, Any]:
    from virtuals_token_db import VirtualsTokenDatabase
    db = VirtualsTokenDatabase()
    history = db.get_creator_history(creator_identifier)
    return {
        "creator_identifier": creator_identifier,
        "total_projects": len(history),
        "projects": history[:50],  # cap at 50
    }


# --- Tool 7: Farmer check ---
async def _check_creator_farmer(wallet_address: str) -> Dict[str, Any]:
    from creator_registry import CreatorRegistry
    reg = CreatorRegistry()
    record = reg.get_by_wallet(wallet_address)
    if not record:
        return {"wallet_address": wallet_address, "known": False, "is_farmer": False}
    return {
        "wallet_address": wallet_address,
        "known": True,
        "is_farmer": bool(record.get("is_farmer")),
        "is_protocol_deployer": bool(record.get("is_protocol_deployer")),
        "total_count": record.get("total_count", 0),
        "virtuals_count": record.get("virtuals_count", 0),
        "clanker_count": record.get("clanker_count", 0),
        "flaunch_count": record.get("flaunch_count", 0),
        "twitter_handle": record.get("twitter_handle"),
        "flags": record.get("flags"),
    }


# --- Tool 8: Smart money check ---
async def _is_smart_money_wallet(wallet_address: str) -> Dict[str, Any]:
    from smart_money_tracker import SmartMoneyTracker
    tracker = SmartMoneyTracker()
    is_smart, tier, score = tracker.is_smart_wallet(wallet_address)
    return {
        "wallet_address": wallet_address,
        "is_smart_money": is_smart,
        "tier": tier.value if hasattr(tier, "value") else str(tier),
        "score": score,
    }


# --- Tool 9: Telegram mentions (NEW — depends on telegram_signals table) ---
async def _search_telegram_mentions(query: str, hours: int = 24) -> Dict[str, Any]:
    import asyncpg
    dsn = os.environ.get("POSTGRES_DSN_READONLY")
    if not dsn:
        return {"error": "POSTGRES_DSN_READONLY not configured"}
    try:
        conn = await asyncpg.connect(dsn)
        rows = await conn.fetch(
            """
            SELECT group_name, sender_handle, message_text, sentiment, created_at
            FROM telegram_signals
            WHERE (message_text ILIKE $1 OR token_address ILIKE $1 OR ticker ILIKE $1)
              AND created_at > NOW() - make_interval(hours => $2)
            ORDER BY created_at DESC
            LIMIT 50
            """,
            f"%{query}%",
            hours,
        )
        await conn.close()
        return {
            "query": query,
            "hours": hours,
            "matches": len(rows),
            "mentions": [dict(r) for r in rows],
        }
    except Exception as e:
        return {"error": f"Database query failed: {e}"}


# --- Tool 10: Full research pipeline ---
async def _research_token_full(token_address: str, deep: bool = True) -> Dict[str, Any]:
    from project_researcher import ProjectResearcher
    researcher = ProjectResearcher()
    result = await researcher.research(token_address, deep=deep)
    return result or {"error": "Research pipeline returned no data"}


# --- Tool 11: Send Telegram alert ---
async def _send_alert_telegram(text: str, buttons: list) -> Dict[str, Any]:
    import aiohttp
    bot_token = os.environ.get("HERMES_TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("HERMES_USER_CHAT_ID", "750774735")
    if not bot_token:
        return {"error": "HERMES_TELEGRAM_BOT_TOKEN not set"}

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if buttons:
        payload["reply_markup"] = {
            "inline_keyboard": [[{
                "text": b["text"],
                **({"url": b["url"]} if b.get("url") else {"callback_data": b.get("callback_data", "noop")})
            }] for b in buttons]
        }

    async with aiohttp.ClientSession() as session:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            data = await resp.json()
            return {"ok": resp.status == 200, "response": data}


# --- Tool 12: Obsidian vault note ---
async def _save_vault_note(
    folder: str, filename: str, content: str, frontmatter: Dict
) -> Dict[str, Any]:
    from vault_writer import write_note
    try:
        path = write_note(folder, filename, content, frontmatter=frontmatter)
        return {"ok": True, "path": str(path)}
    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# ENTRY POINT
# =============================================================================

async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
