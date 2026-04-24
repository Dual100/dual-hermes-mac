"""
Alert Formatter — Hermes edition.

Reuses padrão do creator-bid-bot alert_formatter.py com adaptações pra Hermes:
- Multi-chain buttons (ETH via Maestro, Base via BasedBot, Sol via BonkBot)
- Single message with Primary + Cascade + Long-tail plays
- HTML format pro Telegram
"""

import html as html_mod
from typing import Any, Dict, List, Optional


def _fmt_usd(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value/1e9:.2f}B"
    if value >= 1_000_000:
        return f"${value/1e6:.2f}M"
    if value >= 1_000:
        return f"${value/1e3:.1f}K"
    return f"${value:.0f}"


def _fmt_pct(value: float) -> str:
    return f"{value:+.1f}%"


def build_multi_chain_keyboard(
    primary_address: str, primary_chain: str,
    cascade_address: Optional[str] = None, cascade_chain: Optional[str] = None,
    tertiary_address: Optional[str] = None, tertiary_chain: Optional[str] = None,
) -> Dict:
    """Inline keyboard with chain-specific trading bot buttons."""
    rows = []

    # Primary row
    primary_row = []
    primary_addr = primary_address.lower()
    if primary_chain == "ethereum" or primary_chain == "eth":
        primary_row.append({
            "text": "💰 Buy (Maestro)",
            "url": f"https://t.me/MaestroSniperBot?start={primary_addr}",
        })
        primary_row.append({
            "text": "📈 DexScreener",
            "url": f"https://dexscreener.com/ethereum/{primary_addr}",
        })
    elif primary_chain == "base":
        primary_row.append({
            "text": "💰 Buy (BasedBot)",
            "url": f"https://t.me/based_trading_bot?start={primary_addr}",
        })
        primary_row.append({
            "text": "📈 DexScreener",
            "url": f"https://dexscreener.com/base/{primary_addr}",
        })
    elif primary_chain == "solana":
        primary_row.append({
            "text": "💰 Buy (BonkBot)",
            "url": f"https://t.me/bonkbot_bot?start=ref_x_{primary_addr}",
        })
        primary_row.append({
            "text": "📈 DexScreener",
            "url": f"https://dexscreener.com/solana/{primary_addr}",
        })
    elif primary_chain == "bsc":
        primary_row.append({
            "text": "💰 Buy (Maestro)",
            "url": f"https://t.me/MaestroSniperBot?start={primary_addr}",
        })
        primary_row.append({
            "text": "📈 DexScreener",
            "url": f"https://dexscreener.com/bsc/{primary_addr}",
        })
    rows.append(primary_row)

    # Cascade row (if exists)
    if cascade_address and cascade_chain:
        cascade_addr = cascade_address.lower()
        cascade_row = []
        label_prefix = "🌊 Cascade"
        if cascade_chain in ("ethereum", "eth"):
            cascade_row.append({
                "text": f"{label_prefix} ETH",
                "url": f"https://t.me/MaestroSniperBot?start={cascade_addr}",
            })
        elif cascade_chain == "base":
            cascade_row.append({
                "text": f"{label_prefix} Base",
                "url": f"https://t.me/based_trading_bot?start={cascade_addr}",
            })
        elif cascade_chain == "solana":
            cascade_row.append({
                "text": f"{label_prefix} SOL",
                "url": f"https://t.me/bonkbot_bot?start=ref_x_{cascade_addr}",
            })
        rows.append(cascade_row)

    # Tertiary row
    if tertiary_address and tertiary_chain:
        tertiary_addr = tertiary_address.lower()
        t_row = []
        label_prefix = "🎲 Long-tail"
        if tertiary_chain in ("ethereum", "eth"):
            t_row.append({
                "text": f"{label_prefix} ETH",
                "url": f"https://t.me/MaestroSniperBot?start={tertiary_addr}",
            })
        elif tertiary_chain == "base":
            t_row.append({
                "text": f"{label_prefix} Base",
                "url": f"https://t.me/based_trading_bot?start={tertiary_addr}",
            })
        elif tertiary_chain == "solana":
            t_row.append({
                "text": f"{label_prefix} SOL",
                "url": f"https://t.me/bonkbot_bot?start=ref_x_{tertiary_addr}",
            })
        rows.append(t_row)

    # Actions row
    rows.append([
        {"text": "🔍 Info deep", "callback_data": f"info:{primary_addr}"},
        {"text": "❌ Skip", "callback_data": f"skip:{primary_addr}"},
        {"text": "🔇 Mute narrative", "callback_data": f"mute:{primary_addr}"},
    ])

    return {"inline_keyboard": rows}


def format_gem_alert(
    score: int,
    narrative: str,
    narrative_velocity: float,
    catalyst_author: str,
    catalyst_followers: int,
    catalyst_text: str,
    catalyst_seconds_ago: int,

    primary: Dict[str, Any],            # {address, chain, symbol, name, mcap, liquidity, holders, volume_1h, buys, sells, age_days}
    cascade: Optional[Dict[str, Any]] = None,
    tertiary: Optional[Dict[str, Any]] = None,
    skipped_copycats: int = 0,

    safety: Optional[Dict[str, Any]] = None,   # {honeypot, lp_locked, creator_pct, buy_tax, sell_tax}
    reasoning: Optional[str] = None,
) -> str:
    """Build a single HTML message with all 3 plays."""

    # Header
    if score >= 80:
        emoji = "🔥🔥"
        intensity = "CRITICAL GEM"
    elif score >= 60:
        emoji = "🔥"
        intensity = "GEM ALERT"
    else:
        emoji = "📈"
        intensity = "WATCH"

    msg = f"<b>{emoji} HERMES — {intensity}</b> (score {score}/100)\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    # Primary token identity
    sym = html_mod.escape(primary.get("symbol", "?"))
    name = html_mod.escape(primary.get("name", ""))
    addr = primary.get("address", "")
    chain = primary.get("chain", "?")
    msg += f"<b>🎯 PRIMARY — ${sym}</b>"
    if name:
        msg += f" — {name}"
    msg += f"\n<code>{addr}</code>\n<i>Chain: {chain.upper()} • Age: {primary.get('age_days', '?')} days</i>\n\n"

    # On-chain metrics
    msg += "<b>📊 On-chain</b>\n"
    mcap = primary.get("mcap", 0)
    liq = primary.get("liquidity", 0)
    holders = primary.get("holders", 0)
    msg += f"   MCap: {_fmt_usd(mcap)} • Liq: {_fmt_usd(liq)} • Holders: {holders}\n"
    if primary.get("volume_1h"):
        vol = primary["volume_1h"]
        spike = primary.get("volume_spike_multiplier", 0)
        msg += f"   Vol 1h: {_fmt_usd(vol)}"
        if spike > 1:
            msg += f" ({spike:.0f}× spike!)"
        msg += "\n"
    if primary.get("buys") or primary.get("sells"):
        msg += f"   Buys: {primary.get('buys', 0)} • Sells: {primary.get('sells', 0)}\n"

    # Catalyst
    msg += "\n<b>⚡ Catalyst</b>\n"
    msg += f"   @{html_mod.escape(catalyst_author)} ({_fmt_num(catalyst_followers)})\n"
    msg += f'   <i>"{html_mod.escape(catalyst_text[:150])}"</i>\n'
    msg += f"   {_fmt_time_ago(catalyst_seconds_ago)} ago\n"

    # Narrative
    msg += f'\n<b>🌊 Narrative:</b> "{html_mod.escape(narrative)}" '
    msg += f'velocity {narrative_velocity:.1f}×\n'

    # Safety
    if safety:
        msg += "\n<b>🛡️ Safety</b>\n"
        checks = []
        if not safety.get("honeypot"):
            checks.append("no honeypot")
        if safety.get("lp_locked"):
            checks.append("LP locked")
        if safety.get("creator_pct", 0) < 0.10:
            checks.append(f"creator {safety['creator_pct']*100:.1f}%")
        if safety.get("buy_tax", 0) == 0 and safety.get("sell_tax", 0) == 0:
            checks.append("0/0 tax")
        msg += f"   ✓ {' • '.join(checks)}\n"

    # Cascade play
    if cascade:
        msg += f"\n<b>🌊 CASCADE (secondary play)</b>\n"
        msg += f"   ${html_mod.escape(cascade.get('symbol', '?'))} on {cascade.get('chain', '?').upper()}"
        msg += f" • MCap {_fmt_usd(cascade.get('mcap', 0))}"
        msg += f" • age {cascade.get('age_days', '?')}d\n"
        msg += f"   <code>{cascade.get('address', '')[:20]}...</code>\n"
        msg += "   <i>Entry menor, pega se primary corre</i>\n"

    # Long-tail play
    if tertiary:
        msg += f"\n<b>🎲 LONG-TAIL (bet pequeno)</b>\n"
        msg += f"   ${html_mod.escape(tertiary.get('symbol', '?'))} on {tertiary.get('chain', '?').upper()}"
        msg += f" • MCap {_fmt_usd(tertiary.get('mcap', 0))}\n"
        msg += f"   <code>{tertiary.get('address', '')[:20]}...</code>\n"
        msg += "   <i>Surpresa possível se pump continuar</i>\n"

    # Skipped copycats
    if skipped_copycats > 0:
        msg += f"\n⚠️ <i>Hermes ignorou {skipped_copycats} copycat(s) novo(s) (farmers)</i>\n"

    # Reasoning
    if reasoning:
        msg += f"\n<b>🤖 Hermes:</b> <i>{html_mod.escape(reasoning)}</i>\n"

    return msg


def _fmt_num(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1e6:.1f}M"
    if n >= 1_000:
        return f"{n/1e3:.1f}K"
    return str(n)


def _fmt_time_ago(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds//60}min"
    if seconds < 86400:
        return f"{seconds//3600}h"
    return f"{seconds//86400}d"


# Exemplo de uso
if __name__ == "__main__":
    example = format_gem_alert(
        score=82,
        narrative="trump_america_is_back",
        narrative_velocity=4.2,
        catalyst_author="RT_com",
        catalyst_followers=3_500_000,
        catalyst_text="'AMERICA IS BACK' — Trump on NEW POLITICAL SLOGAN",
        catalyst_seconds_ago=15,
        primary={
            "address": "0xb3a0f70c913aa04404bd177be9e20b47613830b6",
            "chain": "ethereum",
            "symbol": "AIB",
            "name": "America is Back",
            "mcap": 45000,
            "liquidity": 8000,
            "holders": 12,
            "volume_1h": 23000,
            "volume_spike_multiplier": 60,
            "buys": 48,
            "sells": 2,
            "age_days": 273,
        },
        cascade={
            "address": "2EqXwLbVHe83FUbnpNcyxXR4QtKKiPiVe3tXSGp9pump",
            "chain": "solana",
            "symbol": "AIB",
            "mcap": 4000,
            "age_days": 273,
        },
        tertiary={
            "address": "0x8b441bCe3B22xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
            "chain": "ethereum",
            "symbol": "AIB",
            "mcap": 2000,
            "age_days": 273,
        },
        skipped_copycats=4,
        safety={
            "honeypot": False,
            "lp_locked": True,
            "creator_pct": 0.001,
            "buy_tax": 0.0,
            "sell_tax": 0.0,
        },
        reasoning="MEGA mídia tweet sobre narrativa exata do token. Token dormente 9 meses, name matches 100%. Narrative-driven pre-pump opportunity — janela estimada: 3-5 min antes do mass viral.",
    )
    print(example)
    keyboard = build_multi_chain_keyboard(
        primary_address="0xb3a0f70c913aa04404bd177be9e20b47613830b6",
        primary_chain="ethereum",
        cascade_address="2EqXwLbVHe83FUbnpNcyxXR4QtKKiPiVe3tXSGp9pump",
        cascade_chain="solana",
        tertiary_address="0x8b441bCe3B22xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        tertiary_chain="ethereum",
    )
    import json
    print()
    print("Keyboard:")
    print(json.dumps(keyboard, indent=2))
