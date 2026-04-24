# 🧠 Hermes Context Transfer — Mac Mini

**Lê este doc inteiro antes de começar. Depois segue as instruções.**

Você é Claude Code rodando no Mac Mini do usuário Jackson. Este é um
handoff de uma conversa de ~20h que rodou num Claude Code remoto.
Tudo que precisa saber está aqui.

---

## CONTEXTO DO USUÁRIO

- **Nome**: Jackson
- **Fala**: Português BR (responda em PT-BR)
- **Perfil**: Trader crypto sério, tem múltiplas infras (Hetzner, Mac Mini)
- **Estilo**: Pragmático, questiona afirmações, odeia fluff. Pede código em vez de diagrama quando possível.
- **Preocupação principal**: SEGURANÇA. Mac Mini deve ficar ISOLADO do Hetzner.
- **Medo justificado**: Atacantes + erros de IA comprometerem crypto bot no Hetzner.

---

## MISSÃO: DualHermes Hunter

Construir agente autônomo que detecta memecoin gems ANTES de viralizar:
- Monitora múltiplas fontes (Telegram groups, X/Twitter, blockchain, prediction markets)
- Detecta narrativas emergindo
- Correlaciona com tokens em múltiplas chains
- Escolhe token "primary" (mais velho, mais ativo) entre copycats
- Alerta em <30s do catalyst inicial
- Roda 100% no Mac Mini, ISOLADO do Hetzner

---

## DECISÕES DE ARQUITETURA (FECHADAS)

| Decisão | Valor | Motivo |
|---|---|---|
| **Host** | Mac Mini (não Hetzner) | Isolamento de segurança |
| **LLM** | Ollama local + Hermes 4 14B Q4 | Grátis, privacy, Mac aguenta |
| **LLM fallback** | OpenRouter (Hermes 4 70B) — opcional | Se qualidade local insuficiente |
| **Framework agente** | Hermes Agent (NousResearch) | Long-horizon, subagents, auto-skills |
| **Bot Telegram** | @dual_hermes_bot (token já criado) | Separado dos bots existentes |
| **Chains v1** | ETH + Base (NÃO Solana ainda) | User rejeitou Solana por enquanto |
| **Isolamento** | Mac Mini NUNCA conecta Hetzner em runtime | Keys copiadas 1x, depois independente |
| **Database** | Postgres LOCAL no Mac | Não replica Hetzner |
| **X search order** | Sorsa → Nitter → Brave (Brave só emergência) | Sorsa paid, Brave limit 2000/mo |
| **Alert format** | Único msg com Primary + Cascade + Long-tail | User quer tudo junto, não 3 msgs |
| **Trading bot buttons** | Maestro (ETH), BasedBot (Base), BonkBot (Sol) | Padrão que user já usa |

---

## ARQUITETURA FINAL

```
╔══════════════════════════════════════════════════════════════╗
║  MAC MINI (ISOLADO — zero conexão runtime com Hetzner)        ║
╠══════════════════════════════════════════════════════════════╣
║                                                                ║
║  ┌─ Monitors (all OUTBOUND) ──────────────────────────────┐   ║
║  │ • telegram_group_monitor.py (Telethon user client)     │   ║
║  │ • twitter_listener.py (FxTwitter polling MEGA tier 1s) │   ║
║  │ • polymarket_monitor.py (WSS)                          │   ║
║  │ • kalshi_monitor.py (WSS, if user creates acct)        │   ║
║  │ • blockchain_watcher.py (Alchemy + QuickNode WSS)      │   ║
║  │ • top_gainers_fetcher.py (DexScreener + GeckoTerminal) │   ║
║  └────────────────────────────────────────────────────────┘   ║
║            ↓ hunter_signals (append-only table)                ║
║  ┌─ Analysis ────────────────────────────────────────────┐   ║
║  │ • narrative_engine (extract themes from tweets/news)  │   ║
║  │ • convergence_engine (agrupa sinais por token)        │   ║
║  │ • platform_origin_detector (Virtuals/Clanker/Flaunch) │   ║
║  │ • catalyst_analysis (quem começou, 5 padrões)         │   ║
║  │ • cascade_detector (ETH→Base same ticker)             │   ║
║  │ • pump_forensics (backtest learning)                  │   ║
║  └───────────────────────────────────────────────────────┘   ║
║            ↓                                                   ║
║  ┌─ Hermes Agent (5 subagents parallel) ─────────────────┐   ║
║  │ A: contract safety (GoPlus)                           │   ║
║  │ B: creator + history + farmer                         │   ║
║  │ C: X mentions (address + ticker + name multi-query)   │   ║
║  │ D: smart money + holders                              │   ║
║  │ E: pattern library + chain hotness                    │   ║
║  └───────────────────────────────────────────────────────┘   ║
║            ↓ LLM consolidation (kimi via Ollama)               ║
║  ┌─ Delivery ────────────────────────────────────────────┐   ║
║  │ • @dual_hermes_bot (Telegram)                         │   ║
║  │ • Alerts format: Primary + Cascade + Long-tail       │   ║
║  │ • Buttons: Maestro/BasedBot/BonkBot per chain         │   ║
║  └───────────────────────────────────────────────────────┘   ║
╚══════════════════════════════════════════════════════════════╝
```

---

## ARQUIVOS JÁ ESCRITOS (em Hetzner, precisam ser baixados)

**Total: 26 arquivos, ~7000 linhas Python + docs, 564KB**

Localização no Hetzner: `/home/ubuntu/hermes_prep/`

Transfer via SCP 1 vez:
```bash
mkdir -p ~/hermes-mac
scp -r ubuntu@HETZNER_IP:/home/ubuntu/hermes_prep/* ~/hermes-mac/
```

### Arquivos core:
- `main.py` — entry point (bot Telegram + orchestrator)
- `mcp_server.py` — 12 base tools
- `twitter_search.py` — Sorsa v3 search (TESTED working)
- `pump_forensics.py` — reverse-engineer past pumps (TESTED: 7 tokens em 6.6s)
- `polymarket_monitor.py` — WSS
- `kalshi_monitor.py` — WSS (user creates account to activate)
- `telegram_group_monitor.py` — Telethon
- `blockchain_watcher.py` — Alchemy WSS + factory events + watchlist
- `platform_origin_detector.py` — ETH + Base trusted platforms
- `alert_formatter_hermes.py` — HTML message format + multi-chain keyboard (TESTED)
- `top_gainers_fetcher.py` — DexScreener + GeckoTerminal combined
- `x_browser_search.py` — Playwright fallback (optional, for later)
- `smart_money_wallets.json` — 36 KOLs extraídas do Hetzner (public addresses)
- `hermes_data_api_v2.py` — NOT NEEDED on Mac (was for Hetzner read-only API, Mac standalone)
- `hermes_config.yaml` — Hermes Agent config
- `schema.sql` — 18 tables Postgres
- `.env.hermes.template` — template de env vars
- `install_mac.sh` — installer script

### Skills (7):
- `skills/catalyst_analysis.md` — 5 padrões (trending/narrative/mega_kol/creative/shill)
- `skills/narrative_primary_token.md` — age × activity × holders ranking
- `skills/cross_chain_cascade.md` — ETH→Base/Sol cascade detection
- `skills/mega_account_reaction.md` — Elon/CZ/@ethereum hot path <15s
- `skills/telegram_group_alpha.md` — tier 1 group → auto investigate
- `skills/weekly_postmortem.md` — auto-learning from hits/misses
- `skills/benchmark_vs_user.md` — monthly comparison user vs Hermes

---

## VALIDAÇÕES COM DADOS REAIS (TESTES FEITOS)

### Caso AIB (America is Back)
- Token: `0xb3a0f70c913aa04404bd177be9e20b47613830b6` on ETH
- Dormente 9 meses ($5-20K mcap)
- Apr 23 22:06 UTC: @RT_com (3.5M followers, MEGA) tweetou "AMERICA IS BACK" Trump slogan
- Apr 23 22:07 UTC: primeiros trades (pump começa)
- Apr 23 22:08 UTC: 57 transfers em 1 min (explosão)
- Apr 24: mcap $4.3M (+67,699% em 6 dias)
- **Multi tokens**: 10+ tokens AIB em ETH/Base/SOL/BSC (user insight "tem mais de 1 mesmo nome")
- **Vencedor**: OLDEST com atividade real (ETH 0xb3a0f70c, age Jan 2025)
- **Copycats novos (Apr 23-24)**: farmers, SKIP automático

### Hermes would have alerted:
- T=22:06 @RT_com catalyst detectado via FxTwitter polling MEGA tier
- T=22:06+15s: `find_tokens_by_narrative("america is back")` → AIB ETH match
- T=22:06+30s: 5 subagents paralelos investigam
- T=22:07+00s: ALERT enviado no @dual_hermes_bot
- Mcap no alerta: ~$6-20K
- Target: $4.4M = **200-700× upside**

### Sorsa v3 search-tweets TESTED (working)
- Endpoint: `POST https://api.sorsa.io/v3/search-tweets`
- Header: `ApiKey: <tweetscout_key>`
- Body: `{"query": "...", "limit": 20}`
- Returns tweets array with user info, engagement
- 20× rate limit vs X official, 50× cheaper

### Pump forensics TESTED
- Rodou em 20 tokens ETH em 8.2s (paralelo, semáforo 15)
- 5 ALERTs detectados automaticamente
- Padrões: platform_driven (@ethereum → w🍖), mega_kol (@fluffycrypt → ASTEROID)

---

## VARIÁVEIS DE AMBIENTE NECESSÁRIAS

Jackson vai fornecer/copiar do Hetzner `.env`:

```bash
# Telegram bot (ele já criou @dual_hermes_bot)
HERMES_TELEGRAM_BOT_TOKEN=8378591595:AAHpfIxuPmjZTstQEHYbj83y5T8E5OIcZcA  # USER PROVIDED
HERMES_USER_CHAT_ID=750774735

# Telethon user client (copia do Hetzner .env)
TELEGRAM_API_ID=<from Hetzner .env>
TELEGRAM_API_HASH=<from Hetzner .env>
TELEGRAM_PHONE=<Jackson's phone>

# APIs — todas já no Hetzner .env, copia:
TWEETSCOUT_API_KEY=<Sorsa>
BRAVE_SEARCH_API_KEY=<limited 2000/mo — use sparingly>
MORALIS_API_KEY=
ALCHEMY_KEY=
ALCHEMY_ETH_URL=
ALCHEMY_WSS=
QUICKNODE_HTTP=
QUICKNODE_WSS=
QUICKNODE_BSC_URL=
QUICKNODE_BSC_WSS=
ETHERSCAN_API_KEY=
BASESCAN_API_KEY=
NEYNAR_API_KEY=
DEBANK_API_KEY=

# Postgres local
POSTGRES_DSN=postgresql://hermes:@localhost:5432/hermes

# LLM (Ollama local)
LLM_BASE_URL=http://localhost:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=nous-hermes2:Q4_K_M  # ou nous-hermes-2-mixtral

# Chains enabled
CHAINS_ENABLED=eth,base
```

---

## SETUP STEPS PRO MAC MINI

```bash
# 1. Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 2. Dependências
brew install python@3.12 postgresql@16 git
brew install --cask ollama
brew services start postgresql@16

# 3. Pasta + download arquivos
mkdir -p ~/hermes-mac && cd ~/hermes-mac
scp -r ubuntu@HETZNER_IP:/home/ubuntu/hermes_prep/* ./

# 4. Postgres
createuser -s hermes
createdb -O hermes hermes
psql -U hermes -d hermes -f schema.sql

# 5. Python venv
python3.12 -m venv venv
source venv/bin/activate
pip install aiohttp asyncpg websockets telethon python-dotenv fastapi uvicorn \
    redis slowapi "mcp[cli]" python-telegram-bot

# 6. Ollama model
ollama pull nous-hermes2:Q4_K_M   # ~9GB, demora

# 7. .env (copia do template e preenche)
cp .env.hermes.template .env
chmod 600 .env
# edita com os values acima

# 8. Telethon login (1x, interativo)
python3 -c "
from telethon import TelegramClient
import os
from dotenv import load_dotenv
load_dotenv()
c = TelegramClient('data/telethon', int(os.environ['TELEGRAM_API_ID']), os.environ['TELEGRAM_API_HASH'])
c.start(phone=os.environ['TELEGRAM_PHONE'])
print('✅ Authenticated')
c.disconnect()
"

# 9. Test
python3 main.py
# No Telegram: abre @dual_hermes_bot, /start, depois /hunt 0xb3a0f70c913aa04404bd177be9e20b47613830b6
```

---

## COMPORTAMENTOS DO AGENTE A IMPLEMENTAR

### Regras de alerta

1. **Catalyst detection ALWAYS** — a cada investigação, roda catalyst_analysis skill
2. **Multi-query search** — address + ticker + name + "name token" em paralelo
3. **Primary token ranking** — quando múltiplos matches, oldest × activity × holders
4. **Single message alert** — Primary + Cascade + Long-tail num só msg
5. **Max 8-10 alerts/dia** — quality over quantity
6. **Hot path <30s** — MEGA/platform tweets dispatch rapid investigation
7. **Fast path <60s** — narrative + weak signal investigation
8. **Standard <90s** — convergence-based

### 5 padrões de pump

1. **Platform-driven** — conta oficial (@ethereum, @binance, @X) tweeta
2. **Narrative-driven** — tema emerge (ETH season, Matt Furie reference)
3. **MEGA KOL** — conta 50K+ endorsa
4. **Creative catalyst** — quote viral vira meme
5. **Shill coordenado** — RED FLAG, skip

### Buttons por chain

- ETH: `https://t.me/MaestroSniperBot?start={addr}`
- Base: `https://t.me/based_trading_bot?start={addr}`
- Solana: `https://t.me/bonkbot_bot?start=ref_x_{addr}`
- BSC: `https://t.me/MaestroSniperBot?start={addr}`

---

## O QUE O JACKSON ESPERA VOCÊ FAZER

Ao ler este doc:

1. **Confirme pra ele** que entendeu contexto (resume em 3-5 linhas)
2. **Pergunte se tem Homebrew já instalado** no Mac Mini
3. **Siga setup steps acima** um bloco por vez
4. **Teste integração** no final (`/hunt 0xb3a0f70c...`)
5. **Seja PT-BR, direto, questione se fizer sentido**

---

## IMPORTANTE — NÃO FAZER

- ❌ NÃO criar conexão com Hetzner em runtime (só SCP 1 vez pra copiar arquivos)
- ❌ NÃO instalar Tailscale (Jackson recusou infraestrutura compartilhada)
- ❌ NÃO expor SSH do Mac Mini pra internet
- ❌ NÃO executar trades (Hermes é read-only + alert-only)
- ❌ NÃO usar wallet private keys (jamais)
- ❌ NÃO criar .md files novos sem pedir (Jackson detesta file sprawl)

## O QUE FAZER

- ✅ Setup Mac Mini blindado
- ✅ Ollama + Hermes 14B local
- ✅ 36 smart money wallets monitored via Alchemy WSS
- ✅ Telegram @dual_hermes_bot trancado no user_id 750774735
- ✅ /hunt responde em <30s
- ✅ Outcomes tracking (1h/6h/24h/7d ROI)

---

Boa sorte, Claude irmão do outro lado.
