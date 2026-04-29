"""Daily Hermes health report — parses hermes.log to compile activity stats.

Runs daily at 23:00 UTC. Reports to @dualnewbot:
  - Messages received per group
  - Investigations triggered (with cache hit ratio)
  - Alerts sent (with score distribution)
  - Skips by reason (gem-mode, threshold, mcap cap, daily cap)
  - Comparison to previous day (delta)
  - LLM extraction stats
  - Sorsa quota used (estimate)

Designed to give the user feedback whether Hermes optimizations are paying off.

Run: cd ~/hermes-mac && venv/bin/python hermes_health_report.py [--date YYYY-MM-DD]
"""
import asyncio
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

import aiohttp
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass


# Auto-detect log location: Mac uses ~/hermes-mac/logs, dev uses ~/DualHermes
HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / "DualHermes")))
HERMES_MAC = Path.home() / "hermes-mac"
if (HERMES_MAC / "logs" / "hermes.log").exists():
    LOG_FILE = HERMES_MAC / "logs" / "hermes.log"
    STATE_FILE = HERMES_MAC / "data" / "health_report_history.json"
else:
    LOG_FILE = HERMES_HOME / "logs" / "hermes.log"
    STATE_FILE = HERMES_HOME / "data" / "health_report_history.json"

GROUP_TAG_RE = re.compile(r"\[([A-Za-z][^\]]*)\]\s+@([a-z_0-9]+)")
INVESTIGATE_RE = re.compile(r"investigate (0x[a-fA-F0-9]+|[1-9A-HJ-NP-Za-km-z]{32,44})")
SCORE_RE = re.compile(r"score=(\d+)")
ALERTED_RE = re.compile(r"ALERTED (\S+) score=(\d+)")
SKIP_THRESHOLD_RE = re.compile(r"score=(\d+) < threshold (\d+)")
SKIP_GEM_RE = re.compile(r"gem-mode SKIP \(([^)]+)\)")
SKIP_MCAP_RE = re.compile(r"mcap=\$([\d,]+)\.?\d* > \$([\d,]+) \((\w+) cap\)")
DAILY_CAP_RE = re.compile(r"daily cap.*reached")
LLM_TERMS_RE = re.compile(r"(\d+) llm_terms")
BATCH_HERMES_RE = re.compile(r"batch_hermes=(\d+\.\d+)s")


def parse_log_for_date(target_date: str) -> dict:
    """Parse hermes.log entries dated `target_date` (YYYY-MM-DD)."""
    if not LOG_FILE.exists():
        return {"error": f"log file missing: {LOG_FILE}"}

    stats = {
        "date": target_date,
        "groups": Counter(),
        "investigations": 0,
        "alerts": [],
        "skips_threshold": 0,
        "skips_gem_reasons": Counter(),
        "skips_mcap": 0,
        "skips_daily_cap": 0,
        "scores": [],
        "alert_scores": [],
        "llm_invocations": 0,
        "llm_with_terms": 0,
        "batch_hermes_times": [],
        "telegram_messages": 0,
    }

    with open(LOG_FILE, "r", errors="replace") as f:
        for line in f:
            if not line.startswith(target_date):
                continue
            # Group activity
            m = GROUP_TAG_RE.search(line)
            if m:
                gname = m.group(1)
                stats["groups"][gname] += 1
                stats["telegram_messages"] += 1
                # Track LLM extraction
                lm = LLM_TERMS_RE.search(line)
                if lm:
                    stats["llm_invocations"] += 1
                    if int(lm.group(1)) > 0:
                        stats["llm_with_terms"] += 1
            # Investigations
            if "investigate 0x" in line or re.search(r"investigate [1-9A-HJ-NP-Za-km-z]{32,44}", line):
                stats["investigations"] += 1
                bm = BATCH_HERMES_RE.search(line)
                if bm:
                    stats["batch_hermes_times"].append(float(bm.group(1)))
            # Alerts
            am = ALERTED_RE.search(line)
            if am:
                stats["alerts"].append((am.group(1), int(am.group(2))))
                stats["alert_scores"].append(int(am.group(2)))
            # Skip reasons
            if "< threshold" in line:
                stats["skips_threshold"] += 1
            sg = SKIP_GEM_RE.search(line)
            if sg:
                stats["skips_gem_reasons"][sg.group(1)] += 1
            if SKIP_MCAP_RE.search(line):
                stats["skips_mcap"] += 1
            if DAILY_CAP_RE.search(line):
                stats["skips_daily_cap"] += 1
            sm_score = SCORE_RE.search(line)
            if sm_score and "score=" in line:
                stats["scores"].append(int(sm_score.group(1)))

    # Cache hit ratio (batch_hermes <1s = cache hit)
    if stats["batch_hermes_times"]:
        hits = sum(1 for t in stats["batch_hermes_times"] if t < 1.0)
        stats["cache_hit_pct"] = round(100 * hits / len(stats["batch_hermes_times"]), 1)
    else:
        stats["cache_hit_pct"] = None

    return stats


def _load_history() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_history(history: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(history, indent=2))


def _delta(today_v: int, yest_v: int) -> str:
    if yest_v == 0:
        return "—" if today_v == 0 else f"(↑{today_v})"
    diff = today_v - yest_v
    if diff == 0:
        return ""
    pct = (diff / yest_v) * 100
    arrow = "⬆️" if diff > 0 else "⬇️"
    return f"({arrow}{abs(pct):.0f}%)"


def format_report(stats: dict, prev: dict = None) -> str:
    prev = prev or {}
    lines = [f"📊 Hermes Health — {stats['date']}", ""]

    # Activity
    lines.append("Activity:")
    lines.append(f"  Telegram msgs: `{stats['telegram_messages']}` "
                 f"{_delta(stats['telegram_messages'], prev.get('telegram_messages', 0))}")
    lines.append(f"  Investigations: `{stats['investigations']}` "
                 f"{_delta(stats['investigations'], prev.get('investigations', 0))}")
    alerts_n = len(stats["alerts"])
    lines.append(f"  Alerts sent: `{alerts_n}` "
                 f"{_delta(alerts_n, len(prev.get('alerts', [])))}")
    if stats["cache_hit_pct"] is not None:
        lines.append(f"  Cache hit ratio: `{stats['cache_hit_pct']}%` "
                     f"(deep_profile: {sum(1 for t in stats['batch_hermes_times'] if t < 1.0)}/{len(stats['batch_hermes_times'])})")

    # Conversion rate
    if stats["investigations"] > 0:
        conv = 100 * alerts_n / stats["investigations"]
        lines.append(f"  Conversion rate: `{conv:.1f}%` (alerts / investigations)")

    # Top groups — escape group names for Markdown safety
    def _md_safe(s: str) -> str:
        return s.replace("_", " ").replace("*", "").replace("[", "(").replace("]", ")")
    lines.append("")
    lines.append("Top Telegram groups:")
    for gname, cnt in stats["groups"].most_common(5):
        lines.append(f"  • `{_md_safe(gname)}`: {cnt} msgs")

    # Score distribution of alerts
    if stats["alert_scores"]:
        lines.append("")
        lines.append("Alert score distribution:")
        a_scores = stats["alert_scores"]
        a_scores.sort()
        avg = sum(a_scores) / len(a_scores)
        bands = Counter()
        for s in a_scores:
            if s >= 90: bands["90+"] += 1
            elif s >= 80: bands["80-89"] += 1
            elif s >= 70: bands["70-79"] += 1
            else: bands["<70"] += 1
        lines.append(f"  Avg: `{avg:.1f}` · Min: `{a_scores[0]}` · Max: `{a_scores[-1]}`")
        lines.append(f"  90+: `{bands['90+']}`  80-89: `{bands['80-89']}`  "
                     f"70-79: `{bands['70-79']}`  <70: `{bands['<70']}`")

    # Skip reasons
    lines.append("")
    lines.append("Skips:")
    lines.append(f"  Below threshold: `{stats['skips_threshold']}`")
    if stats["skips_mcap"]:
        lines.append(f"  Mcap cap exceeded: `{stats['skips_mcap']}`")
    if stats["skips_daily_cap"]:
        lines.append(f"  ⚠️ Daily cap reached: `{stats['skips_daily_cap']}` "
                     f"(consider raising HERMES_GEM_DAILY_CAP)")
    if stats["skips_gem_reasons"]:
        lines.append(f"  Gem-mode skips:")
        for reason, cnt in stats["skips_gem_reasons"].most_common(3):
            lines.append(f"    • `{_md_safe(reason)}`: {cnt}×")

    # LLM stats
    if stats["llm_invocations"]:
        hit_rate = 100 * stats["llm_with_terms"] / stats["llm_invocations"]
        lines.append("")
        lines.append("LLM extraction:")
        lines.append(f"  Runs: `{stats['llm_invocations']}` · "
                     f"With terms: `{stats['llm_with_terms']}` ({hit_rate:.0f}%)")

    # Sorsa quota estimate
    sorsa_estimate = (
        stats["investigations"] * 12 +  # ~12 Sorsa calls per investigation (with cache)
        len(stats["alerts"]) * 2  # +2 for VIP follow-up per Butler alert
    )
    lines.append("")
    lines.append(f"Sorsa estimate: ~{sorsa_estimate} calls "
                 f"(at current rate, runway ~{95000 // max(sorsa_estimate, 1)} days)")

    return "\n".join(lines)


async def send_telegram(text: str) -> bool:
    bot_token = os.environ.get("AGDP_TELEGRAM_BOT_TOKEN", "").strip('"')
    chat_id = os.environ.get("HERMES_USER_CHAT_ID", "750774735")
    if not bot_token:
        print(f"[no AGDP token, would send]\n{text}")
        return False
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={"chat_id": int(chat_id), "text": text,
                      "disable_web_page_preview": True},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status == 200:
                    return True
                body = await r.text()
                print(f"telegram failed status={r.status}: {body[:200]}")
                # Retry without markdown if parsing failed
                if r.status == 400 and "parse" in body.lower():
                    async with s.post(
                        f"https://api.telegram.org/bot{bot_token}/sendMessage",
                        json={"chat_id": int(chat_id), "text": text,
                              "disable_web_page_preview": True},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as r2:
                        return r2.status == 200
                return False
    except Exception as e:
        print(f"telegram send failed: {e}")
        return False


async def main():
    target_date = sys.argv[1] if len(sys.argv) > 1 else None
    if target_date and target_date.startswith("--date="):
        target_date = target_date.split("=", 1)[1]
    if not target_date:
        target_date = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d")

    stats = parse_log_for_date(target_date)
    if "error" in stats:
        print(stats["error"])
        return

    history = _load_history()
    prev_date = (datetime.fromisoformat(target_date) - timedelta(days=1)).strftime("%Y-%m-%d")
    prev = history.get(prev_date) or {}

    report = format_report(stats, prev=prev)
    print(report)
    print()
    sent = await send_telegram(report)
    print(f"sent={sent}")

    # Persist for next-day comparison
    # Don't store all alerts (just count) to keep file small
    history[target_date] = {
        "telegram_messages": stats["telegram_messages"],
        "investigations": stats["investigations"],
        "alerts": stats["alerts"][:50],  # cap
        "skips_threshold": stats["skips_threshold"],
        "skips_mcap": stats["skips_mcap"],
        "skips_daily_cap": stats["skips_daily_cap"],
        "alert_scores": stats["alert_scores"],
        "cache_hit_pct": stats["cache_hit_pct"],
        "llm_invocations": stats["llm_invocations"],
    }
    # Cap history at 30 days
    keep = sorted(history.keys())[-30:]
    history = {k: v for k, v in history.items() if k in keep}
    _save_history(history)


if __name__ == "__main__":
    asyncio.run(main())
