# 🚀 Setup Hermes no Mac Mini — passo a passo testável

**Tempo total: 30-60 minutos** (dependendo de velocidade da internet)

Cada passo tem um **teste** pra você confirmar que deu certo antes de seguir.

---

## PASSO 0 — Pré-requisitos (5 min)

Abre o Terminal no Mac (⌘+Espaço → "Terminal").

### Verifica Homebrew
```bash
brew --version
```

Se disser "command not found", instala:
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### TESTE: Deve mostrar "Homebrew X.Y.Z"
```bash
brew --version
```

---

## PASSO 1 — Baixar arquivos do Hetzner (3 min)

Você precisa saber o IP ou hostname do Hetzner. Se normalmente conecta com:
```bash
ssh ubuntu@SEU_HETZNER_IP
```

Use esse IP abaixo:

```bash
# Cria estrutura no Mac
mkdir -p ~/hermes-mac/src
cd ~/hermes-mac

# Baixa TODOS os arquivos (substitua IP)
scp -r ubuntu@SEU_HETZNER_IP:/home/ubuntu/hermes_prep/*.py ~/hermes-mac/src/
scp -r ubuntu@SEU_HETZNER_IP:/home/ubuntu/hermes_prep/*.yaml ~/hermes-mac/src/
scp -r ubuntu@SEU_HETZNER_IP:/home/ubuntu/hermes_prep/*.sql ~/hermes-mac/src/
scp -r ubuntu@SEU_HETZNER_IP:/home/ubuntu/hermes_prep/skills ~/hermes-mac/
scp ubuntu@SEU_HETZNER_IP:/home/ubuntu/hermes_prep/install_mac.sh ~/hermes-mac/
scp ubuntu@SEU_HETZNER_IP:/home/ubuntu/hermes_prep/.env.hermes.template ~/hermes-mac/.env
```

### TESTE:
```bash
ls -la ~/hermes-mac/src/ | head -10
ls ~/hermes-mac/skills/
```

Deve listar `main.py`, `mcp_server.py`, `twitter_search.py`, etc. E ~7 skill files `.md`.

---

## PASSO 2 — Instalar Homebrew packages (10 min)

```bash
# Instala o básico
brew install python@3.12 postgresql@16 git

# Inicia Postgres como serviço
brew services start postgresql@16

# Adiciona ao PATH se necessário
echo 'export PATH="/opt/homebrew/opt/postgresql@16/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### TESTE:
```bash
python3 --version       # Deve mostrar 3.12.x
psql --version          # Deve mostrar 16.x
```

---

## PASSO 3 — Criar banco Postgres local (2 min)

```bash
# Cria usuário e database
createuser -s hermes 2>/dev/null || echo "user hermes já existe"
createdb -O hermes hermes 2>/dev/null || echo "db hermes já existe"

# Aplica schema (18 tabelas)
psql -U hermes -d hermes -f ~/hermes-mac/src/schema.sql
```

### TESTE:
```bash
psql -U hermes -d hermes -c "\dt" | head -20
```

Deve listar tabelas: `hunter_signals`, `narratives`, `polymarket_events`, etc.

---

## PASSO 4 — Python virtual env + deps (5 min)

```bash
cd ~/hermes-mac
python3 -m venv venv
source venv/bin/activate

# Instala dependências
pip install --upgrade pip wheel setuptools
pip install \
    aiohttp \
    asyncpg \
    websockets \
    telethon \
    python-dotenv \
    fastapi \
    uvicorn \
    redis \
    slowapi \
    "mcp[cli]" \
    python-telegram-bot
```

### TESTE:
```bash
python3 -c "import telethon, asyncpg, aiohttp, websockets; print('✅ todas as deps instaladas')"
```

---

## PASSO 5 — Configurar .env (5 min)

Abra o `.env` num editor (VSCode, nano, ou TextEdit):

```bash
open -a TextEdit ~/hermes-mac/.env
# ou
nano ~/hermes-mac/.env
```

**Preencha os valores (copie do .env do Hetzner):**

```bash
# === Bot Telegram (token do @dual_hermes_bot que você me deu) ===
HERMES_TELEGRAM_BOT_TOKEN=8378591595:AAHpfIxuPmjZTstQEHYbj83y5T8E5OIcZcA
HERMES_USER_CHAT_ID=750774735

# === Credenciais Telegram (copia do Hetzner .env) ===
TELEGRAM_API_ID=<valor do TELEGRAM_API_ID no Hetzner>
TELEGRAM_API_HASH=<valor do TELEGRAM_API_HASH no Hetzner>
TELEGRAM_PHONE=<seu número com +código>

# === API keys (copia do Hetzner .env) ===
TWEETSCOUT_API_KEY=<valor do Hetzner>
BRAVE_SEARCH_API_KEY=<valor do Hetzner>
MORALIS_API_KEY=<valor do Hetzner>
ALCHEMY_KEY=<valor do Hetzner>
ALCHEMY_ETH_URL=<valor do Hetzner>
ALCHEMY_WSS=<valor do Hetzner>
QUICKNODE_HTTP=<valor do Hetzner>
QUICKNODE_WSS=<valor do Hetzner>
QUICKNODE_BSC_URL=<valor do Hetzner>
QUICKNODE_BSC_WSS=<valor do Hetzner>
ETHERSCAN_API_KEY=<valor do Hetzner>
BASESCAN_API_KEY=<valor do Hetzner>
NEYNAR_API_KEY=<valor do Hetzner>
DEBANK_API_KEY=<valor do Hetzner>

# === Postgres local ===
POSTGRES_DSN=postgresql://hermes:@localhost:5432/hermes

# === LLM — COMEÇA via Hetzner nvidia-proxy (depois podemos trocar) ===
# Você precisa instalar Tailscale primeiro (PASSO 6)
LLM_BASE_URL=http://<tailscale-ip-hetzner>:8000/v1
LLM_API_KEY=local-proxy
LLM_MODEL=kimi-k2.5

# === Chains ===
CHAINS_ENABLED=eth,base

# === Logging ===
LOG_LEVEL=INFO
```

### Copiar rápido do Hetzner:
```bash
# No Terminal do Mac, cria script pra copiar keys específicas do Hetzner
ssh ubuntu@SEU_HETZNER_IP "grep -E '^(TELEGRAM_API_ID|TELEGRAM_API_HASH|TELEGRAM_PHONE|TWEETSCOUT|BRAVE_SEARCH|MORALIS|ALCHEMY|QUICKNODE|ETHERSCAN|BASESCAN|NEYNAR|DEBANK)_.*=' /home/ubuntu/creator-bid-bot/.env"
```

Isso mostra as keys formatadas. Cole no `.env` do Mac.

### TESTE:
```bash
chmod 600 ~/hermes-mac/.env
# Valida que não tem campo vazio
grep "=$\|=<" ~/hermes-mac/.env
```

Se não mostrar nada, tá OK. Se mostrar linhas, são keys que ainda precisam ser preenchidas.

---

## PASSO 6 — Tailscale (5 min)

Pra Hermes no Mac falar com nvidia-proxy (LLM) no Hetzner:

### Mac:
```bash
brew install --cask tailscale
open -a Tailscale
```

Faz login com Google (mesma conta do Hetzner).

### Hetzner (SSH):
```bash
ssh ubuntu@SEU_HETZNER_IP

# Dentro do Hetzner:
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
# Abre URL, login mesmo Google
tailscale ip -4   # mostra o IP tailscale, ex: 100.64.0.5
```

Copia esse IP e coloca no `.env` do Mac:
```
LLM_BASE_URL=http://100.64.0.5:8000/v1
```

### TESTE (no Mac):
```bash
# Ping Hetzner via Tailscale
tailscale status
curl -s http://100.64.0.5:8000/v1/models  # deve responder
```

Se `curl` responder com JSON, Tailscale tá funcionando e nvidia-proxy acessível.

---

## PASSO 7 — Autenticar Telethon (1x, interativo) (3 min)

```bash
cd ~/hermes-mac
source venv/bin/activate

python3 -c "
from telethon import TelegramClient
import os
from dotenv import load_dotenv
load_dotenv()

api_id = int(os.environ['TELEGRAM_API_ID'])
api_hash = os.environ['TELEGRAM_API_HASH']
phone = os.environ['TELEGRAM_PHONE']

client = TelegramClient('data/telethon', api_id, api_hash)
client.start(phone=phone)
print('✅ Autenticado')
# Lista grupos
print()
print('Grupos/canais que você é membro:')
for dialog in client.iter_dialogs():
    if dialog.is_group or dialog.is_channel:
        print(f'  {dialog.id:>15} — {dialog.name}')
client.disconnect()
"
```

Vai pedir seu número e código SMS (enviado pro seu Telegram). Digite cada um.

### TESTE:
- ✅ No final aparece "✅ Autenticado"
- ✅ Lista seus grupos (incluindo @ethvolumespike se você entrou)

---

## PASSO 8 — Primeiro teste com /hunt (2 min)

```bash
cd ~/hermes-mac
source venv/bin/activate
python3 src/main.py
```

No Telegram, abre @dual_hermes_bot, manda `/start`.

### TESTE:
Bot responde:
```
🧠 Dual Hermes Hunter online.

Commands:
  /hunt 0x...     — investigate a token
  ...
```

Se sim, TUDO FUNCIONANDO. 🎉

Se não, logs em outro terminal:
```bash
tail -f ~/hermes-mac/logs/hermes.log
```

Me cola o erro.

---

## PASSO 9 — Testar investigação real (1 min)

No Telegram, manda:
```
/hunt 0xb3a0f70c913aa04404bd177be9e20b47613830b6
```

Deve responder em ~10-30s com:
- Mcap, volume, liquidez
- Catalyst identificado
- Decisão (ALERT/WATCH/SKIP)
- Reasoning

---

## PASSO 10 — Autostart (opcional, 1 min)

Pra Hermes subir sozinho quando Mac liga:

```bash
# Copia launchd plist (criado pelo install_mac.sh)
launchctl load ~/Library/LaunchAgents/com.dual.hermes.plist
```

Pra desligar:
```bash
launchctl unload ~/Library/LaunchAgents/com.dual.hermes.plist
```

---

## Troubleshooting rápido

| Problema | Solução |
|---|---|
| `scp` pede senha | Normal, digita a senha do Hetzner |
| `brew install` falha | `xcode-select --install` primeiro |
| Postgres não conecta | `brew services restart postgresql@16` |
| Python venv erro | Usa Python 3.12 explícito: `python3.12 -m venv` |
| Telethon pede código 2x | Normal (1× phone, 1× 2FA se tiver) |
| Bot não responde | Verifica token no .env (sem espaços) |
| LLM erro | `curl http://tailscale-ip:8000/v1/models` no Mac testa |

---

## Estrutura final no seu Mac

```
~/hermes-mac/
├── .env                        ← suas keys (chmod 600)
├── venv/                       ← python env isolado
├── src/
│   ├── main.py                 ← entry point
│   ├── mcp_server.py          
│   ├── twitter_search.py
│   ├── pump_forensics.py
│   ├── polymarket_monitor.py
│   ├── kalshi_monitor.py
│   ├── telegram_group_monitor.py
│   ├── blockchain_watcher.py
│   ├── platform_origin_detector.py
│   ├── top_gainers_fetcher.py
│   ├── x_browser_search.py
│   ├── mac_hermes_client.py
│   ├── hermes_config.yaml
│   └── schema.sql
├── skills/
│   ├── catalyst_analysis.md
│   ├── narrative_primary_token.md
│   ├── cross_chain_cascade.md
│   ├── mega_account_reaction.md
│   ├── telegram_group_alpha.md
│   ├── weekly_postmortem.md
│   └── benchmark_vs_user.md
├── data/
│   ├── telethon.session
│   └── hermes_local_cache.db
└── logs/
    ├── hermes.out
    └── hermes.err
```

---

## Próximos passos (depois que tá rodando)

1. **Adiciona grupos Telegram** que você tá dentro como alpha sources (config dos monitors)
2. **Roda pump_forensics** em top 100 gainers pra calibrar pesos
3. **Ativa launchd** pra sempre on
4. **Opcional**: Deploy API read-only no Hetzner (pro Hermes acessar smart money history)

Me responde qualquer problema ou quando chegar em qual passo.

**Começa pelo PASSO 1.** Me fala o IP do Hetzner (ou `ssh alias`) pra eu te ajudar a baixar os arquivos.
