"""Hermes-side command executor — polls Hetzner queue and executes locally.

This is the OUT-OF-BAND control channel. When Tailscale dies but the rest of
Hermes runtime is alive, we still poll Hetzner via HTTPS public for commands
to execute. Lets us recover Tailscale, restart services, or pull updated
code WITHOUT inbound SSH access.

Whitelist-only commands. No arbitrary shell.

Runs as one of the supervised tasks (auto-restart on crash via main.py).
"""
import asyncio
import json
import logging
import os
import subprocess
import time
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger("hermes.cmd_executor")

API_URL = os.getenv(
    "HERMES_DATA_API_URL", "https://dualzero.duckdns.org/hermes/"
).rstrip("/")
API_KEY = os.getenv("HERMES_API_KEY") or os.getenv("HERMES_DATA_API_KEY", "")
POLL_INTERVAL_SEC = 60


def _exec(cmd: List[str], timeout: int = 60) -> Dict[str, Any]:
    """Run a shell command, capture output. Never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "exit_code": r.returncode,
            "stdout": r.stdout[:8000],
            "stderr": r.stderr[:4000],
        }
    except subprocess.TimeoutExpired:
        return {"exit_code": 124, "stdout": "", "stderr": f"timeout after {timeout}s"}
    except Exception as e:
        return {"exit_code": 1, "stdout": "", "stderr": str(e)[:4000]}


def _handle_restart_hermes(args: Dict) -> Dict:
    # Bouncing the launchd service — process exits, launchd respawns.
    # We do this from a child process so the result can be posted before exit.
    subprocess.Popen(
        ["bash", "-c",
         "(sleep 2 && launchctl kickstart -k gui/$(id -u)/com.dual.hermes) &"],
        start_new_session=True,
    )
    return {"exit_code": 0, "stdout": "scheduled launchctl kickstart in 2s", "stderr": ""}


def _handle_restart_tailscale(args: Dict) -> Dict:
    return _exec(
        ["sudo", "launchctl", "kickstart", "-k", "system/com.tailscale.tailscaled"],
        timeout=15,
    )


def _handle_tailscale_up(args: Dict) -> Dict:
    return _exec(["sudo", "tailscale", "up", "--reset", "--accept-routes"], timeout=30)


def _handle_diagnose(args: Dict) -> Dict:
    parts = []
    parts.append("=== uptime ===\n" + _exec(["uptime"])["stdout"])
    parts.append("=== tailscale status ===\n" + _exec(["tailscale", "status"])["stdout"])
    parts.append("=== launchctl hermes ===\n" + _exec(["launchctl", "print", "gui/" + str(os.getuid()) + "/com.dual.hermes"], timeout=10)["stdout"][:2000])
    parts.append("=== disk ===\n" + _exec(["df", "-h", os.path.expanduser("~")])["stdout"])
    return {"exit_code": 0, "stdout": "\n\n".join(parts), "stderr": ""}


def _handle_git_pull(args: Dict) -> Dict:
    repo = os.path.expanduser("~/hermes-mac")
    if not os.path.isdir(repo):
        return {"exit_code": 1, "stdout": "", "stderr": f"no repo at {repo}"}
    return _exec(["git", "-C", repo, "pull", "--ff-only"], timeout=60)


def _handle_rsync_from_hetzner(args: Dict) -> Dict:
    src = args.get("src", "agent@hetzner:/home/ubuntu/hermes_prep/")
    dest = args.get("dest", os.path.expanduser("~/hermes-mac/"))
    return _exec(
        ["rsync", "-avz", "--delete",
         "--exclude=data/telethon.session*", "--exclude=logs/", src, dest],
        timeout=120,
    )


def _handle_reboot(args: Dict) -> Dict:
    if not args.get("confirm") == "REBOOT_NOW":
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": "reboot requires args.confirm == 'REBOOT_NOW'",
        }
    subprocess.Popen(
        ["bash", "-c", "(sleep 5 && sudo reboot) &"],
        start_new_session=True,
    )
    return {"exit_code": 0, "stdout": "reboot scheduled in 5s", "stderr": ""}


def _handle_noop(args: Dict) -> Dict:
    return {"exit_code": 0, "stdout": "noop OK at " + str(int(time.time())), "stderr": ""}


def _handle_download_hermes_files(args: Dict) -> Dict:
    """HTTP-based deploy — pulls files from Hetzner /hermes-files API.

    No SSH key required. Uses HERMES_DATA_API_URL + HERMES_DATA_API_KEY env.
    Args:
      dest: destination dir (default ~/hermes-mac/)
      paths: optional list of specific files (default: all)
    """
    import urllib.request
    api_url = os.environ.get("HERMES_DATA_API_URL", "").rstrip("/")
    api_key = os.environ.get("HERMES_DATA_API_KEY", "")
    if not (api_url and api_key):
        return {"exit_code": 1, "stdout": "",
                "stderr": "HERMES_DATA_API_URL or HERMES_DATA_API_KEY missing"}
    dest = os.path.expanduser(args.get("dest", "~/hermes-mac/"))
    os.makedirs(dest, exist_ok=True)
    headers = {"Authorization": f"Bearer {api_key}"}
    # 1) List files
    try:
        req = urllib.request.Request(f"{api_url}/hermes-files/list", headers=headers)
        with urllib.request.urlopen(req, timeout=30) as r:
            import json as _j
            files = _j.loads(r.read().decode()).get("files", [])
    except Exception as e:
        return {"exit_code": 1, "stdout": "", "stderr": f"list failed: {e}"}
    # 2) Filter — only files with kill_flags or listener references (low risk)
    requested = args.get("paths") or []
    if requested:
        files = [f for f in files if f["path"] in requested]
    # 3) Download each
    written = []
    errors = []
    for f in files:
        rel = f["path"]
        try:
            url = f"{api_url}/hermes-files/get?path={urllib.parse.quote(rel)}"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as r:
                content = r.read()
            target = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
            with open(target, "wb") as wf:
                wf.write(content)
            written.append(rel)
        except Exception as e:
            errors.append(f"{rel}: {e}")
    return {
        "exit_code": 0 if not errors else 2,
        "stdout": f"downloaded {len(written)} files to {dest}\n" + "\n".join(written[:10]),
        "stderr": "\n".join(errors[:10]) if errors else "",
    }


HANDLERS = {
    "restart_hermes": _handle_restart_hermes,
    "restart_tailscale": _handle_restart_tailscale,
    "tailscale_up": _handle_tailscale_up,
    "diagnose": _handle_diagnose,
    "git_pull": _handle_git_pull,
    "rsync_from_hetzner": _handle_rsync_from_hetzner,
    "download_hermes_files": _handle_download_hermes_files,
    "reboot": _handle_reboot,
    "noop": _handle_noop,
}


async def _fetch_pending(session: aiohttp.ClientSession) -> List[Dict]:
    try:
        async with session.get(
            f"{API_URL}/commands/pending?limit=10",
            headers={"Authorization": f"Bearer {API_KEY}"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            if r.status != 200:
                logger.warning(f"pending fetch returned {r.status}")
                return []
            data = await r.json()
            return data.get("commands", []) or []
    except Exception as e:
        logger.warning(f"pending fetch failed: {e}")
        return []


async def _post_result(
    session: aiohttp.ClientSession, cmd_id: int, result: Dict,
) -> None:
    try:
        async with session.post(
            f"{API_URL}/commands/result",
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "id": cmd_id,
                "exit_code": result.get("exit_code", 1),
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""),
            },
            timeout=aiohttp.ClientTimeout(total=15),
        ) as r:
            if r.status != 200:
                logger.warning(f"post_result {cmd_id} returned {r.status}")
    except Exception as e:
        logger.warning(f"post_result {cmd_id} failed: {e}")


async def run_command_executor() -> None:
    """Main loop — poll Hetzner queue every 60s, execute, report back."""
    if not API_KEY:
        logger.error("no HERMES_API_KEY — command executor disabled")
        return
    logger.info(f"command_executor started — polling {API_URL} every {POLL_INTERVAL_SEC}s")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                cmds = await _fetch_pending(session)
                for cmd in cmds:
                    name = cmd.get("command")
                    handler = HANDLERS.get(name)
                    if not handler:
                        logger.warning(f"no handler for command: {name}")
                        await _post_result(session, cmd["id"], {
                            "exit_code": 1, "stdout": "",
                            "stderr": f"unknown command: {name}",
                        })
                        continue
                    logger.info(f"executing #{cmd['id']} {name}")
                    try:
                        result = handler(cmd.get("args") or {})
                    except Exception as e:
                        result = {"exit_code": 1, "stdout": "", "stderr": str(e)[:4000]}
                    await _post_result(session, cmd["id"], result)
                    logger.info(
                        f"#{cmd['id']} {name} → exit {result.get('exit_code')}"
                    )
            except Exception as e:
                logger.exception(f"executor loop error: {e}")
            await asyncio.sleep(POLL_INTERVAL_SEC)
