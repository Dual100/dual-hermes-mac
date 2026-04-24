# Setup Mac Mini — passo a passo

**Tempo estimado: 30-40 minutos**

Você só precisa seguir as instruções. Eu preparei tudo.

---

## 0. Antes de começar

Abra o **Terminal** no Mac (⌘ + Espaço → "Terminal").

Todos os comandos são rodados aí. Copia/cola um por vez.

---

## 1. Baixar arquivos do Hetzner (30s)

No Terminal do Mac:

```bash
# Criar diretório local
mkdir -p ~/hermes-mac/src
cd ~/hermes-mac/src

# Baixar TODOS arquivos do Hetzner via SCP (substitua USER e IP se diferente)
scp -r ubuntu@<hetzner-ip>:/home/ubuntu/hermes_prep/*.py ./
scp -r ubuntu@<hetzner-ip>:/home/ubuntu/hermes_prep/*.yaml ./
scp -r ubuntu@<hetzner-ip>:/home/ubuntu/hermes_prep/*.sql ./
scp -r ubuntu@<hetzner-ip>:/home/ubuntu/hermes_prep/install_mac.sh ~/hermes-mac/
scp -r ubuntu@<hetzner-ip>:/home/ubuntu/hermes_prep/.env.hermes.template ~/hermes-mac/.env
```

**Alternativa simples**: eu posso criar um tarball e te passar o link.

---

## 2. Rodar o instalador (15min)

```bash
cd ~/hermes-mac
chmod +x install_mac.sh
./install_mac.sh
```

O script vai:
- ✅ Instalar Homebrew (se não tiver)
- ✅ Instalar Python 3.12
- ✅ Instalar PostgreSQL 16
- ✅ Instalar Ollama (opcional)
- ✅ Instalar Tailscale
- ✅ Criar Postgres DB `hermes`
- ✅ Aplicar schema (18 tabelas)
- ✅ Criar Python venv
- ✅ Instalar deps Python

Se travar em alguma coisa, me manda o erro.

---

## 3. Configurar .env (5min)

Edite `~/hermes-mac/.env`:

```bash
nano ~/hermes-mac/.env    # ou use VSCode: code ~/hermes-mac/.env
```

**Campos que você precisa preencher**:

```bash
# ===== JÁ TEM (te passo após tudo instalado) =====
HERMES_TELEGRAM_BOT_TOKEN=<o token do @dual_hermes_bot que você me deu>
HERMES_USER_CHAT_ID=750774735

# ===== VOCÊ COPIA DO HETZNER .env =====
# (vou te mandar um comando que pega tudo auto)
TELEGRAM_API_ID=<do seu .env Hetzner>
TELEGRAM_API_HASH=<do seu .env Hetzner>
TELEGRAM_PHONE=<seu número+código>
TWEETSCOUT_API_KEY=<do .env>
BRAVE_SEARCH_API_KEY=<do .env>
ALCHEMY_KEY=<do .env>
ALCHEMY_ETH_URL=<do .env>
QUICKNODE_HTTP=<do .env>
QUICKNODE_WSS=<do .env>
ETHERSCAN_API_KEY=<do .env>
BASESCAN_API_KEY=<do .env>
MORALIS_API_KEY=<do .env>
NEYNAR_API_KEY=<do .env>
DEBANK_API_KEY=<do .env>

# ===== GERAR =====
HERMES_DATA_API_KEY=<rodar: python3 -c "import secrets; print(secrets.token_urlsafe(32))">

# ===== LLM escolhe 1 das 3 =====
# A: Hetzner kimi-k2 (recomendado) — precisa Tailscale
LLM_BASE_URL=http://<tailscale-ip-hetzner>:8000/v1
LLM_API_KEY=local-proxy
LLM_MODEL=kimi-k2.5

# B: Ollama local (depois que fizer: ollama pull nous-hermes2:Q4_K_M)
# LLM_BASE_URL=http://localhost:11434/v1
# LLM_MODEL=nous-hermes2

# C: OpenRouter ($10 crédito)
# OPENROUTER_API_KEY=<criar em openrouter.ai>
# OPENROUTER_MODEL=nousresearch/hermes-4-70b
```

---

## 4. Setup Tailscale (5min)

**No Mac**:
```bash
# Abre Tailscale
open -a Tailscale

# Login com Google (mesmo email do Hetzner)
# Aparece um ícone na barra superior quando conecta
```

**No Hetzner** (se ainda não tem):
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
# Login com mesmo Google no browser
```

**Descobrir IP Tailscale do Hetzner**:
```bash
# No Hetzner:
tailscale ip -4
# Output: 100.X.Y.Z
```

Cola esse IP no `.env` do Mac em `LLM_BASE_URL`:
```
LLM_BASE_URL=http://100.X.Y.Z:8000/v1
```

---

## 5. Autenticar Telethon (uma vez)

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
client = TelegramClient('data/telethon.session', api_id, api_hash)
client.start()
print('✅ Autenticado')
client.disconnect()
"
```

Vai pedir seu número e código SMS. Faça uma vez.

---

## 6. Testar

```bash
cd ~/hermes-mac
source venv/bin/activate
python3 src/main.py
```

Abre Telegram, busca **@dual_hermes_bot**, manda `/start`.

Deve responder:
```
🧠 Dual Hermes Hunter online.

Commands:
  /hunt 0x...     — investigate a token
  ...
```

Se NÃO respondeu: verifica logs:
```bash
tail -f ~/hermes-mac/logs/hermes.log
```

---

## 7. Ativar autostart (roda sozinho ao ligar Mac)

```bash
launchctl load ~/Library/LaunchAgents/com.dual.hermes.plist
```

Agora Hermes sobe automaticamente sempre que Mac liga.

**Desativar**:
```bash
launchctl unload ~/Library/LaunchAgents/com.dual.hermes.plist
```

---

## Troubleshooting

| Problema | Solução |
|---|---|
| `brew: command not found` | Reiniciar terminal ou `eval "$(/opt/homebrew/bin/brew shellenv)"` |
| Postgres não inicia | `brew services restart postgresql@16` |
| Telethon pede código 2x | Normal — 1x pra phone, 1x pra 2FA se tiver |
| Bot não responde | Verifica token em `.env` (sem aspas) |
| LLM não conecta | Testa `curl http://<tailscale-ip>:8000/v1/models` |
| Erros Python | `source venv/bin/activate && pip install -r requirements.txt` |

---

## Próximos passos (eu faço ou te guio)

1. Deployar Hetzner Data API (eu faço depois que você confirmar)
2. Configurar grupos Telegram pra monitorar (você me passa usernames/IDs)
3. Ajustar handles ALPHA que você respeita
4. Cria conta Kalshi (opcional)

---

**IMPORTANTE:** Quando tudo tiver funcionando, **regenera o bot token** no @BotFather (`/revoke` `@dual_hermes_bot`). O token atual tá no histórico dessa conversa.
