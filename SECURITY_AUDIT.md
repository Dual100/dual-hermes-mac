# Hermes Agent — Security Audit Report

**Date:** 2026-04-24
**Script audited:** `https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh`
**SHA256:** `251c1b97dda5db092d152d34afa315612fe27329e821c5414130f2a7e0c011e2`
**Lines:** 1436

## Verdict: SAFE TO INSTALL (with hardening)

No malicious patterns detected. Script is legitimate and follows standard Python dev tooling conventions.

## What the script does

1. Downloads `uv` (Astral's Python package manager) from `astral.sh/uv/install.sh`
2. Clones `https://github.com/NousResearch/hermes-agent.git` into `$HOME/.hermes/hermes-agent`
3. Runs `uv pip install -e ".[all]"` to install ~200 Python dependencies
4. Optionally downloads Node.js from `nodejs.org` for terminal UI components
5. Optionally installs ripgrep via system package manager (asks sudo)
6. Creates data dirs: `~/.hermes/{cron,sessions,logs,pairing,hooks,image_cache,audio_cache,memories,skills}`
7. Optionally installs systemd service (asks before doing)
8. Optionally modifies `~/.bashrc` / `~/.zshrc` to add PATH

## Network endpoints contacted

| Domain | Purpose | Trust |
|---|---|---|
| `github.com/NousResearch/*` | Source code | HIGH (official org) |
| `astral.sh/uv/install.sh` | Python package manager | HIGH (reputable) |
| `nodejs.org/dist/*` | Node.js binary | HIGH (official) |

All HTTPS, no HTTP.

## Security checks performed

| Check | Result |
|---|---|
| Malicious patterns (`eval`, `exec`, obfuscated code) | ✅ None |
| Secret exfiltration (reading `.env`, SSH keys, etc.) | ✅ None |
| Unnecessary sudo | ✅ Only for optional system packages, asks first |
| Cryptominers / reverse shells | ✅ None |
| Hardcoded backdoors | ✅ None |
| Supply chain (pinned deps) | ⚠️ Uses `main` branch — not pinned to SHA |
| Install location | ✅ `$HOME/.hermes/` (user-scoped) |

## Risks identified & mitigations

### R1: Unpinned version (MEDIUM)
**Risk:** Script pulls `main` branch — if Nous Research repo is compromised, you get compromised code.
**Mitigation:** Modify install to checkout a specific tag/SHA. Implementation plan:
```bash
HERMES_BRANCH="v1.0.0"   # or specific SHA
./install.sh --branch "$HERMES_BRANCH"
```

### R2: Transitive Python dependencies (MEDIUM)
**Risk:** `uv pip install ".[all]"` pulls ~200 packages from PyPI. Any could have malicious update.
**Mitigation:**
- Install to isolated user (`hermes`) — can't reach crypto bot files
- systemd sandboxing (ProtectHome, ReadWritePaths)
- Lockfile: use `uv.lock` if provided (pinned versions) instead of `.[all]`

### R3: Auto-install of systemd service (LOW)
**Risk:** Script offers to install systemd service that starts on boot. Could re-enable itself.
**Mitigation:** Install with `--no-service` flag or answer "no" to the prompt. We install our own hardened service instead.

### R4: PATH modification in shell rc (LOW)
**Risk:** Script adds `$HOME/.local/bin` to PATH via `.bashrc`. Standard practice but pollutes shell.
**Mitigation:** Run with `--no-path` or run as `hermes` user whose shell rc is disposable.

## Recommended install command

For maximum safety, after creating isolated `hermes` user:

```bash
# As hermes user
export HERMES_HOME=/home/hermes/.hermes
export HERMES_INSTALL_DIR=/home/hermes/.hermes/hermes-agent

# Pin to specific commit (check latest release tag first)
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh \
  -o /tmp/install.sh

# Verify SHA256 matches expected
sha256sum /tmp/install.sh

# Review any changes since last audit
diff /tmp/install.sh /home/ubuntu/hermes_prep/install.sh

# Run with our flags
bash /tmp/install.sh --branch v1.0.0 --no-path --skip-setup
```

## What NOT to do

- ❌ Don't run as root — script doesn't need it, running as root expands blast radius
- ❌ Don't `curl | bash` blindly — always download + review first
- ❌ Don't install on user `ubuntu` — no isolation from crypto bot
- ❌ Don't accept the shell rc modification if running as hermes user (we set PATH explicitly in systemd)

## Next steps

1. Create `hermes` user with locked password, no sudo
2. Install to `/home/hermes/.hermes/` with the flags above
3. Deploy hardened `hermes.service` (see `hermes.service` in this dir)
4. Configure allowed network egress (IPAddressAllow whitelist)
5. Smoke test in isolated mode before exposing Telegram
