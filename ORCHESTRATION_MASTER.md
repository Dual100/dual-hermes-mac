# DualHermes Hunter — Master Orchestration Design

**Status:** DESIGN DOC — aguardando aprovação antes de codar
**Version:** 1.0
**Date:** 2026-04-24

---

## 1. FILOSOFIA CENTRAL: CONVERGÊNCIA É O ALPHA

A insight mais importante do stack 2026 de gem-hunting (Photon, GMGN, BullX, 100x_memes):

> **Um sinal isolado = ruído. Múltiplos sinais independentes no mesmo token em janela curta = alpha.**

Sua infra atual tem **fontes poderosas mas desacopladas** — cada monitor alerta sozinho, sem correlação. Isso gera:
- Duplicação (mesmo token alerta de 3+ fontes em 10s)
- Ruído (1 KOL comprou não é alpha, 4 KOLs + menção Telegram + smart money = SIM)
- Score inflacionado/deflacionado (não agrega evidência)

**O orquestrador resolve isso.**

---

## 2. FONTES DE SINAL — MAPA COMPLETO

### 2.1 Fontes EXISTENTES (já no creator-bid-bot, reaproveitar)

| # | Fonte | Tipo | Latência | Qualidade | Status |
|---|---|---|---|---|---|
| 1 | `genesis_websocket_monitor` | On-chain Virtuals factories (8 factories) | ~1s | ⭐⭐⭐ | ATIVO |
| 2 | `bankrbot_blockchain_monitor` | On-chain Clanker V4 factory | ~1s | ⭐⭐⭐ | ATIVO |
| 3 | `virtuals_auto_discovery` | DexScreener polling + 8 Virtuals factories | ~5s | ⭐⭐ | ATIVO |
| 4 | `smart_money_tracker --mode=monitor` | WebSocket em **42 KOL wallets** | ~2s | ⭐⭐⭐ | ATIVO |
| 5 | `virtuals_twitter_monitor` | @virtuals_io tweets | 1-5min | ⭐⭐ | ATIVO |
| 6 | `agdp_leaderboard_monitor` | aGDP leaderboard ranking | polling | ⭐⭐⭐ | ATIVO |
| 7 | `flaunch_monitor` | Flaunch GraphQL | ~1min | ⭐⭐ | ATIVO |
| 8 | `dgclaw_monitor` | DGCLAW leaderboard | 5s | ⭐⭐⭐ | ATIVO |
| 9 | `wallet_tracker` | **SUA wallet** (portfolio) | ~2s | ⭐⭐⭐ | ATIVO |

### 2.2 Fontes NOVAS (precisa construir)

| # | Fonte | Tipo | Latência alvo | Qualidade esperada | Esforço |
|---|---|---|---|---|---|
| 10 | **Telegram Group Monitor** | Telethon user client, N grupos | instant | ⭐⭐ (com triage) | MÉDIO (código existe) |
| 11 | **DexScreener Trending Feed** | Polling `/token-boosts/top`, `/latest/dex/search` | 60s | ⭐⭐ | PEQUENO |
| 12 | **DexScreener New Pairs** | Polling `/token-profiles/latest/v1` | 60s | ⭐⭐ | PEQUENO |
| 13 | **Twitter/X Realtime Listener** | Nitter/Sorsa polling em handles alpha | 2-5min | ⭐⭐⭐ | MÉDIO |
| 14 | **Smart Wallet Expansion** | Auto-descobrir novos KOLs via backfill | on-demand | ⭐⭐⭐ | MÉDIO |
| 15 | **Cross-chain scanner** (opcional, v2) | Solana, Ethereum via Helius/Alchemy | - | ⭐⭐ | GRANDE |

### 2.3 Por que NÃO usar tudo

- **Twitter API oficial**: cara, rate-limited. Sorsa já tem.
- **DexScreener trending**: sim, útil pra confirmar, mas delay 60s então é sinal SECUNDÁRIO
- **Cross-chain**: fora do v1. Base é foco.

---

## 3. ARQUITETURA — O ORQUESTRADOR CENTRAL

### 3.1 Topologia

```
┌────────────────────────────────────────────────────────────────────────────┐
│  FONTES (todas escrevem em UMA fila)                                       │
│                                                                              │
│  [genesis] [clanker] [auto_disc] [smart_money] [agdp] [flaunch] [dgclaw]   │
│       │        │          │            │          │        │        │        │
│  [telegram_groups] [x_listener] [dexscreener_trending] [wallet_tracker]    │
│       │        │          │                                                  │
│       └────────┴──────────┴──────────────────────────────────────────────┐  │
│                                                                           │  │
│                        Raw events emitted ↓                               │  │
└───────────────────────────────────────────────────────────────────────────┼──┘
                                                                            │
                                                                            ▼
                    ┌───────────────────────────────────────────────┐
                    │   SIGNAL BUS — Postgres `hunter_signals`      │
                    │   Fila persistente de TODOS os sinais         │
                    │   (INSERT-only, imutável, auditável)          │
                    └───────────────────┬───────────────────────────┘
                                        │
                                        ▼
                    ┌───────────────────────────────────────────────┐
                    │   CONVERGENCE ENGINE (novo, coração do sys)   │
                    │                                                │
                    │   A cada 30s:                                  │
                    │   1. Pega sinais últimos N min                 │
                    │   2. Agrupa por token_address                  │
                    │   3. Calcula CONVERGENCE_SCORE                 │
                    │   4. Filtra por threshold (ex: 50+)            │
                    │   5. Emite eventos HIGH_SIGNAL                 │
                    └───────────────────┬───────────────────────────┘
                                        │
                                        ▼
                    ┌───────────────────────────────────────────────┐
                    │   DEDUP + RATE LIMIT                          │
                    │   - Não alertar mesmo token 2x em 30min       │
                    │   - Max 5 alertas/hora pro usuário            │
                    └───────────────────┬───────────────────────────┘
                                        │
                                        ▼
                    ┌───────────────────────────────────────────────┐
                    │   HERMES AGENT (gem_hunter skill)             │
                    │   Recebe HIGH_SIGNAL → faz research deep      │
                    │   Usa 3 subagentes paralelos:                 │
                    │     A) contract safety + holders              │
                    │     B) creator + twitter + history            │
                    │     C) smart money + price context            │
                    │   Consolida → decisão final                   │
                    └───────────────────┬───────────────────────────┘
                                        │
                                        ▼
                    ┌───────────────────────────────────────────────┐
                    │   DELIVERY                                    │
                    │   - Telegram @dualhermes_bot                  │
                    │   - Obsidian vault (04-Hunts/)                │
                    │   - Feedback loop: tracking_events            │
                    └────────────────────────────────────────────────┘
```

### 3.2 Tabelas Postgres novas

```sql
-- Fila central de sinais (append-only)
CREATE TABLE hunter_signals (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source TEXT NOT NULL,              -- 'genesis', 'smart_money', 'telegram', etc
    source_weight REAL NOT NULL,       -- multiplicador peso da fonte (0.1 a 2.0)
    token_address TEXT NOT NULL,
    chain TEXT NOT NULL DEFAULT 'base',
    event_type TEXT NOT NULL,          -- 'new_launch', 'kol_buy', 'mention', etc
    raw_data JSONB NOT NULL,
    processed INT DEFAULT 0,
    convergence_batch_id BIGINT        -- FK pra convergence runs
);
CREATE INDEX idx_hs_token_created ON hunter_signals (token_address, created_at DESC);
CREATE INDEX idx_hs_source_created ON hunter_signals (source, created_at DESC);
CREATE INDEX idx_hs_unprocessed ON hunter_signals (processed, created_at) WHERE processed = 0;

-- Runs do convergence engine (batch analysis)
CREATE TABLE convergence_runs (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ DEFAULT NOW(),
    window_minutes INT NOT NULL,
    tokens_analyzed INT,
    high_signals_emitted INT
);

-- Resultado por token dentro de um run
CREATE TABLE convergence_tokens (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT REFERENCES convergence_runs(id),
    token_address TEXT NOT NULL,
    signal_count INT NOT NULL,
    unique_sources INT NOT NULL,
    convergence_score REAL NOT NULL,
    sources_list TEXT[],               -- ['genesis', 'smart_money', 'telegram']
    first_signal_at TIMESTAMPTZ,
    last_signal_at TIMESTAMPTZ,
    emitted_alert INT DEFAULT 0
);
CREATE INDEX idx_ct_run_score ON convergence_tokens (run_id, convergence_score DESC);
CREATE INDEX idx_ct_token ON convergence_tokens (token_address, run_id DESC);

-- Alertas efetivamente enviados
CREATE TABLE hunter_alerts (
    id BIGSERIAL PRIMARY KEY,
    alerted_at TIMESTAMPTZ DEFAULT NOW(),
    token_address TEXT NOT NULL,
    convergence_score REAL,
    hermes_final_score REAL,           -- decisão do agente (0-100)
    action TEXT,                       -- 'ALERT', 'WATCH', 'SKIP'
    message_text TEXT,
    telegram_message_id BIGINT,
    user_action TEXT,                  -- 'BUY', 'SKIP', 'RESEARCH_MORE', null
    user_action_at TIMESTAMPTZ
);

-- Outcome tracking (feedback loop)
CREATE TABLE hunter_outcomes (
    id BIGSERIAL PRIMARY KEY,
    alert_id BIGINT REFERENCES hunter_alerts(id),
    interval_label TEXT,               -- '1h', '6h', '24h', '7d'
    checked_at TIMESTAMPTZ DEFAULT NOW(),
    price_at_alert REAL,
    price_now REAL,
    mcap_at_alert REAL,
    mcap_now REAL,
    roi_pct REAL,
    max_roi_interval REAL              -- peak during this interval
);
```

---

## 4. CONVERGENCE_SCORE — A FÓRMULA

Pra cada token em cada batch (roda 30s), calcula:

```
convergence_score = SUM(source_weight × source_quality_multiplier) × time_decay × diversity_bonus
```

### 4.1 Source weights (config tunável)

| Fonte | Weight base | Justificativa |
|---|---|---|
| `smart_money` (KOL buy) | **3.0** | Prova on-chain, wallet verificada, é o alpha puro |
| `genesis` (novo Virtuals) | 2.0 | Oportunidade de entrada early |
| `clanker` (novo Clanker) | 1.8 | Similar, maior ruído |
| `telegram_groups` tier 1 | 2.5 | Grupos alpha curados manualmente |
| `telegram_groups` tier 2 | 1.0 | Ruído maior, peso menor |
| `x_listener` KOL | 2.5 | Handle de alta reputação tweetou |
| `x_listener` geral | 1.0 | Menção qualquer |
| `dexscreener_trending` | 1.5 | Indicador de momentum (secundário) |
| `agdp_leaderboard` | 2.0 | Ranking oficial, sinal forte |
| `flaunch` | 1.5 | Launch window |
| `wallet_tracker` (você comprou) | 3.0 | Seu próprio sinal — auto-research |

### 4.2 Quality multipliers

Dependendo do detalhe do evento:

- **Smart money buy**:
  - 1 KOL ELITE+ → 1.0×
  - 2-3 KOLs → 1.5×
  - 4+ KOLs → 2.0× (CRITICAL signal)
- **Telegram mention**:
  - Shill detectado pelo LLM triage → 0.1×
  - Neutral → 1.0×
  - Bullish + urgency > 0.7 → 1.5×
- **Genesis**:
  - Creator sem history → 0.8×
  - Creator com 1 hit anterior (>$100k mcap) → 1.5×
  - Creator verified team → 2.0×

### 4.3 Time decay

Sinais mais recentes pesam mais:
```
time_decay = exp(-minutes_since_signal / 30)
# 0 min = 1.0
# 30 min = 0.37
# 60 min = 0.14
# 120 min = 0.02
```

### 4.4 Diversity bonus

Converge de MÚLTIPLAS fontes = bonus:
```
diversity_bonus = 1.0 + 0.3 × (unique_sources - 1)
# 1 source = 1.0
# 2 sources = 1.3
# 3 sources = 1.6
# 4 sources = 1.9
# 5+ sources = 2.2 (cap)
```

### 4.5 Thresholds de ação

```python
if convergence_score >= 100:
    action = "CRITICAL_ALERT"     # research imediato, alerta vermelho
elif convergence_score >= 60:
    action = "HIGH_ALERT"          # research + alerta
elif convergence_score >= 30:
    action = "WATCH"               # adiciona ao watchlist, sem alerta
else:
    action = "NOISE"               # descarta
```

---

## 5. O AGENT HERMES — COMO EXECUTA O RESEARCH

Quando convergence_score ≥ 60, o orquestrador chama o Hermes:

```yaml
trigger: HIGH_ALERT emitted
hermes_prompt: |
  Token {address} had convergence_score {score} from sources {sources_list}.
  Recent signals:
  {last_10_signals_summarized}
  
  Decide: is this a GEM to alert, or noise?
  
  Use subagents in parallel:
    - subagent_A: contract_safety + holders
    - subagent_B: creator_identity + twitter + history + farmer_check
    - subagent_C: smart_money_wallets + price_context + similar_winners
  
  Consolidate → return final verdict:
    action: ALERT / WATCH / SKIP
    score_0_100: <number>
    reasoning: <1-2 sentences>
    risks: <list>
    tags: <list>
```

### 5.1 Subagentes em paralelo (killer feature)

**Subagent A — Risk & Contract (5-10s):**
- `check_contract_safety(address)` → GoPlus honeypot, LP lock, taxes
- `analyze_holders_onchain(address)` → holder count, top 10%, concentration
- `get_token_price_mcap(address)` → mcap, liquidity agora
- Output: risk_score 0-100, risk_flags

**Subagent B — Creator & Social (15-25s):**
- `lookup_twitter_from_wallet(creator)` → handle
- `research_twitter_deep(handle)` → Sorsa score, team followers, tenure
- `get_creator_history(creator_id)` → prior projects + performance
- `check_creator_farmer(wallet)` → farmer? protocol?
- Output: creator_tier (VERIFIED/TRUSTED/NEUTRAL/SUSPICIOUS), track_record

**Subagent C — Smart Money & Context (8-12s):**
- Para cada wallet recente que comprou: `is_smart_money_wallet()`
- `search_telegram_mentions(ticker or address)` → contexto dos grupos
- `analyze_holders_onchain` → quem são os top 10 holders? algum smart?
- Output: smart_money_presence, mention_density, momentum_direction

### 5.2 Consolidação (main agent)

Main agent recebe os 3 outputs e decide:

```
Se risk_score > 70 OU is_honeypot: SKIP (mesmo com convergence alto)
Se creator_tier == SUSPICIOUS E !smart_money: SKIP  
Se convergence >= 100 E risk_score < 50: ALERT (vermelho)
Se convergence >= 60 E creator_tier in (VERIFIED, TRUSTED): ALERT
Se convergence >= 60 E smart_money_count >= 2: ALERT
Senão: WATCH (sem alerta, mas entra na lista)
```

---

## 6. NOISE FILTERING — MULTI-LAYER

Ruído quebra o sistema. 4 camadas de filtro:

### Camada 1: Origem (no monitor)
- Telegram: regex + `is_shill` flag pelo LLM triage
- Smart money: `min_value_eth = 0.01` (dust filter)
- X listener: filtra RTs, replies de low-rep accounts

### Camada 2: Deduplicação por janela
- Mesmo (token, source) em 5min → colapsado em 1 sinal com count
- Mesmo token + mesma wallet KOL em 30min → 1 sinal

### Camada 3: Blacklist
- Tokens já rugados (marca em `token_blacklist`)
- Creators farmers extremos (>30 tokens)
- Wallets flagadas como bot/sybil

### Camada 4: Quality floor
- Se liquidez < $1k → descarta
- Se holder count < 10 → descarta
- Se convergence < 30 → nunca vira alerta

---

## 7. DEDUP ENTRE SISTEMAS EXISTENTES

**Problema atual:** butler_alert, smart_money_tracker, genesis_monitor todos alertam separadamente.

**Solução:**
1. Manter sistemas atuais rodando **normais** (você já tá acostumado)
2. Adicionar OUTRO bot `@dualhermes_bot` que só emite alertas **consolidados** de convergência
3. Os sinais originais continuam indo pro bot principal, mas **o Hermes emite uma visão sintetizada**
4. Usuário recebe: "Sinais dispersos no @dualvirtual_bot" + "Alerta consolidado no @dualhermes_bot quando converge"

---

## 8. SMART WALLET EXPANSION — TRATAMENTO ESPECIAL

Você pediu "pegar as smart wallet". Duas interpretações, vou atender as duas:

### 8.1 Usar as 42 existentes no monitor
Já funciona. Hermes lê eventos do `smart_money_tracker` via `hunter_signals`.

### 8.2 Descobrir NOVAS smart wallets automaticamente
**Skill nova:** `discover_new_smart_wallets`

Roda 1x/dia:
1. Pega top 100 tokens que pumparam >3x nos últimos 30 dias
2. Pra cada, busca os **primeiros 50 buyers**
3. Cruza: wallets que aparecem em 3+ pumps = candidato a smart
4. Calcula win_rate + avg_roi histórico
5. Se tier ≥ SMART, adiciona ao `smart_wallets` DB
6. Notifica: "Descobri 5 novas smart wallets: ..."

Código base já existe — é o `smart_money_backfill.py` (linha 659). Só expor como skill.

### 8.3 Watchlist personalizada
Você adiciona wallets manualmente via comando `/watch 0xwallet [label]` no Telegram. Hermes começa a monitorar imediatamente.

---

## 9. FEEDBACK LOOP — HERMES APRENDE

Essencial pra melhorar com o tempo:

### 9.1 Outcomes automáticos
- Cron */30min checa preço de todo token que alertou
- Registra ROI em 1h, 6h, 24h, 7d em `hunter_outcomes`
- Se ROI > 50% em 24h = hit
- Se ROI < -30% = miss

### 9.2 Aprendizado
- **Skill autogerada do Hermes:** analisa hits vs misses toda semana
- Identifica padrões: "Sinais com smart_money ≥ 3 + convergence ≥ 80 hit 73%"
- Ajusta **source_weights** ao longo do tempo (config YAML)
- Documenta padrões no Obsidian vault `08-Context/hunter-patterns.md`

### 9.3 Tuning manual
Comando Telegram `/tune source_weight smart_money 3.5` permite você ajustar na hora sem deploy.

---

## 10. COMANDOS TELEGRAM DISPONÍVEIS

No @dualhermes_bot:

| Comando | Função |
|---|---|
| `/hunt` | Dispara scan manual agora |
| `/hunt 0xabc...` | Research profundo de um token específico |
| `/watch 0xwallet label` | Adiciona wallet ao monitoramento |
| `/unwatch 0xwallet` | Remove |
| `/watchlist` | Lista wallets monitoradas |
| `/scores` | Mostra source_weights atuais |
| `/tune source weight` | Ajusta source weight |
| `/outcomes 24h` | Stats de hits/misses nas últimas 24h |
| `/status` | Health check de todos os monitors |
| `/hermes_stop` | 🛑 Kill switch emergencial |
| `/convergence 60` | Muda threshold mínimo de alerta |

---

## 11. MÉTRICAS DE SUCESSO

Como medir se o Hermes está sendo útil:

- **Precision**: % de alertas que deram hit (ROI > 50% em 24h)
  - Alvo: >= 40% (média indústria ~25-35%)
- **Recall**: % de gemas reais que foram detectadas
  - Medido post-hoc: dos top 20 pumps do dia, quantos alertamos?
  - Alvo: >= 60%
- **Noise ratio**: alertas/dia
  - Alvo: 3-10/dia (não 50)
- **Latency**: signal → alert
  - Alvo: < 60s
- **Cost**: LLM tokens/dia
  - Alvo: < 2M tokens/dia com kimi-k2 (grátis na sua infra)

Tudo isso fica num dashboard `/stats` no Telegram, atualizado toda hora.

---

## 12. ROLLOUT PLAN — FASES

### Fase 0: Design approved ← VOCÊ ESTÁ AQUI

### Fase 1: Infraestrutura (1 dia)
- Criar usuário hermes + systemd + Postgres schemas
- Instalar Hermes Agent com config kimi-k2
- MCP server com 12 tools
- Smoke test: `/hunt 0xabc` retorna análise

### Fase 2: Signal ingestion (1-2 dias)
- Adapter em cada monitor existente pra escrever em `hunter_signals`
- Telegram group monitor rodando (com os grupos que você fornecer)
- Schema `hunter_signals` populando

### Fase 3: Convergence engine (1 dia)
- Cron 30s roda `convergence_engine.py`
- Calcula score, preenche `convergence_tokens`
- Emite HIGH_ALERT → trigger Hermes

### Fase 4: Agent skills (2-3 dias)
- Skill `research_token_converged` com 3 subagentes
- Skill `discover_smart_wallets` (1x/dia)
- Skill `learn_from_outcomes` (1x/semana)
- Hermes testado com 20 tokens históricos (hit/miss sabidos)

### Fase 5: Delivery + feedback (1 dia)
- Alertas formatados no @dualhermes_bot
- Obsidian vault logging
- Outcome tracker cron

### Fase 6: Tuning (contínuo)
- Primeira semana: ajustar weights diariamente
- Depois: Hermes autoajusta, você só acompanha

**Total: ~7-9 dias de trabalho**, rodando em fases pra você ver resultado desde dia 3.

---

## 13. QUESTÕES EM ABERTO (pra você decidir)

1. **Threshold mínimo inicial pra alertar**: sugiro 60. Muito alto = perde gems. Muito baixo = spam.
2. **Grupos Telegram**: você me passa 3-5 pra começar
3. **Wallets KOL custom**: quer adicionar alguns além dos 42 atuais?
4. **Frequência limite de alertas**: max 10/dia? 20/dia? (importa pra não cansar você)
5. **Auto-discovery de smart wallets**: ativar desde dia 1 ou só depois que estabilizar?
6. **Cross-chain v2**: Solana depois? (não agora, mas planejamento)
7. **Mac Mini vs Hetzner**: ainda preciso saber — muda onde hospedar

---

## 14. POR QUE ISSO É DIFERENTE DE PHOTON/BULLX/GMGN

Esses são **front-ends de trading** com análise integrada. Eles rodam no browser dos usuários.

**Seu sistema é diferente:**
- **Personalizado**: conhece SEU estilo (wallet_learning já aprende), SUAS smart wallets
- **Multi-fonte**: agrega Telegram + X + on-chain + seu próprio trading history
- **Autônomo**: não precisa você abrir dashboard. Alerta no Telegram.
- **Autoaprendente**: Hermes gera skills automaticamente dos hits/misses
- **Read-only**: não executa trades (segurança). Você decide sempre.
- **Open source** (seu próprio código): não depende de plataforma externa que pode fechar

Isso é um **alpha agent pessoal** — o que Photon/GMGN faz pra milhões, o Hermes faz **especificamente pra você**.

---

## 15. PRÓXIMOS PASSOS

Leia este doc. Responda:

1. ✅ **Design aprovado?** (ok, ou tem alguma coisa que mudaria?)
2. ✅ **Source weights iniciais** (seção 4.1) fazem sentido? (pode ajustar)
3. ✅ **Threshold HIGH_ALERT = 60** tá bom? (pode começar maior/menor e ajustar)
4. ✅ **Mac Mini specs + decisão host** (ainda falta)
5. ✅ **Bot Telegram token** (@dualhermes_bot via BotFather)
6. ✅ **API_ID + API_HASH** (my.telegram.org)
7. ✅ **3-5 grupos Telegram** pra começar a monitorar (usernames ou IDs)

Com isso eu inicio Fase 1.
