# Install Steps — NOT YET EXECUTED

**Nada aqui foi rodado.** Guia de referência pra quando você autorizar.

## Pre-requisitos do usuário

1. Token de @dualhermes_bot via @BotFather
2. API_ID + API_HASH em https://my.telegram.org/apps
3. Decisão: Hetzner ou Mac Mini?

---

## Passo 1 — Criar usuário hermes (Hetzner)

```bash
# SSH no Hetzner
sudo useradd -m -s /bin/bash -c "Hermes Agent" hermes
sudo passwd -l hermes  # lock password (só entra via sudo su)
sudo mkdir -p /home/hermes/.hermes/{logs,skills,sessions,cron}
sudo chown -R hermes:hermes /home/hermes
sudo chmod 700 /home/hermes/.hermes
```

## Passo 2 — Verificar install script (já auditado)

```bash
# Já temos em /home/ubuntu/hermes_prep/install.sh
# Verificar integridade com hash do audit
sha256sum /home/ubuntu/hermes_prep/install.sh
# Esperado: 251c1b97dda5db092d152d34afa315612fe27329e821c5414130f2a7e0c011e2

sudo cp /home/ubuntu/hermes_prep/install.sh /home/hermes/install.sh
sudo chown hermes:hermes /home/hermes/install.sh
sudo chmod 755 /home/hermes/install.sh
```

## Passo 3 — Instalar Hermes como user hermes

```bash
sudo -u hermes bash -c '
  export HERMES_HOME=/home/hermes/.hermes
  export HERMES_INSTALL_DIR=/home/hermes/.hermes/hermes-agent
  # Pinar a tag estável (verificar latest em github.com/NousResearch/hermes-agent/releases)
  bash /home/hermes/install.sh --branch v1.0.0 --no-path --skip-setup
'
```

## Passo 4 — Criar Postgres role read-only

```sql
-- Executar como postgres admin
CREATE USER hermes_readonly WITH PASSWORD '<senha-forte>';
GRANT CONNECT ON DATABASE dual_creator_bot TO hermes_readonly;
GRANT USAGE ON SCHEMA public TO hermes_readonly;
GRANT USAGE ON SCHEMA virtuals_tokens TO hermes_readonly;
GRANT USAGE ON SCHEMA clanker TO hermes_readonly;
-- Somente SELECT, nunca INSERT/UPDATE/DELETE
GRANT SELECT ON ALL TABLES IN SCHEMA public TO hermes_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA virtuals_tokens TO hermes_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA clanker TO hermes_readonly;
-- Nunca: wallet_learning, claude_decisions (trading data)
REVOKE ALL ON SCHEMA wallet_learning FROM hermes_readonly;
REVOKE ALL ON SCHEMA claude_decisions FROM hermes_readonly;
```

## Passo 5 — Copiar arquivos do prep

```bash
sudo cp /home/ubuntu/hermes_prep/hermes.service /etc/systemd/system/hermes.service
sudo cp /home/ubuntu/hermes_prep/mcp_server.py /home/hermes/hermes-tools/mcp_server.py
sudo cp /home/ubuntu/hermes_prep/telegram_group_monitor.py /home/hermes/telegram_group_monitor.py
sudo cp /home/ubuntu/hermes_prep/hermes_config.yaml /home/hermes/.hermes/config.yaml

sudo chown -R hermes:hermes /home/hermes/
sudo chmod 600 /home/hermes/.hermes/config.yaml
```

## Passo 6 — Criar .env do hermes

```bash
sudo -u hermes tee /home/hermes/.hermes/.env > /dev/null <<EOF
HERMES_TELEGRAM_BOT_TOKEN=<token do @BotFather>
HERMES_USER_CHAT_ID=750774735
TELEGRAM_API_ID=<seu api_id de my.telegram.org>
TELEGRAM_API_HASH=<seu api_hash>
POSTGRES_DSN=postgresql://hermes_readonly:<senha>@localhost:5432/dual_creator_bot
POSTGRES_DSN_READONLY=postgresql://hermes_readonly:<senha>@localhost:5432/dual_creator_bot
# Reusa tweetscout key existente (read-only access)
TWEETSCOUT_API_KEY=<from creator-bid-bot .env>
QUICKNODE_RPC=<from creator-bid-bot .env>
EOF

sudo chmod 600 /home/hermes/.hermes/.env
```

## Passo 7 — Instalar deps Python do MCP server

```bash
sudo -u hermes bash -c '
  cd /home/hermes/.hermes/hermes-agent
  source venv/bin/activate
  pip install "mcp[cli]" telethon asyncpg aiohttp
'
```

## Passo 8 — Primeira autenticação Telethon (INTERATIVA)

```bash
# Rodar uma vez pra criar session file
sudo -u hermes bash -c '
  cd /home/hermes
  source /home/hermes/.hermes/hermes-agent/venv/bin/activate
  python3 -c "
from telethon import TelegramClient
import os
api_id = int(os.environ[\"TELEGRAM_API_ID\"])
api_hash = os.environ[\"TELEGRAM_API_HASH\"]
client = TelegramClient(\"/home/hermes/.hermes/telethon.session\", api_id, api_hash)
client.start()  # pede seu phone + código do SMS
print(\"Authenticated.\")
client.disconnect()
"
'
sudo chmod 600 /home/hermes/.hermes/telethon.session
```

## Passo 9 — Smoke test MCP server

```bash
sudo -u hermes bash -c '
  cd /home/hermes/.hermes/hermes-agent
  source venv/bin/activate
  python3 /home/hermes/hermes-tools/mcp_server.py &
  SERVER_PID=$!
  sleep 2
  # Test list_tools via stdio JSON-RPC
  echo "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}" | \
    python3 -c "import json,sys; print(json.loads(input()))"
  kill $SERVER_PID
'
```

## Passo 10 — Ativar services

```bash
sudo systemctl daemon-reload
sudo systemctl enable hermes.service
sudo systemctl start hermes.service

# Após 30s, verificar logs
sudo journalctl -u hermes.service --since "1 min ago" -n 50
```

## Passo 11 — Teste final

No Telegram, abra conversa com @dualhermes_bot:

```
/start
/hunt 0x....  (qualquer token Base)
```

Deve responder com análise completa em ~30-60s.

## Rollback se der ruim

```bash
sudo systemctl stop hermes.service
sudo systemctl disable hermes.service
sudo rm -rf /home/hermes  # full wipe
sudo userdel -r hermes 2>/dev/null
sudo rm /etc/systemd/system/hermes.service
sudo systemctl daemon-reload
# OpenClaw e crypto bot continuam intocados
```
