# DualHermes Hunter — V3: Intelligence Layer

**Supplemental to:** V1 master + V2 narrative
**Trigger:** User insights that V2 missed:
1. Multi-token narrative disambiguation (pick OLDEST with activity)
2. Latency: 6min é lento — precisa <90s
3. MEGA accounts (Elon, CZ) são tier especial
4. Narrativa tem FASES — early/growth/peak exigem estratégias diferentes
5. Sistema precisa APRENDER continuamente

---

## FIX 1: TOKEN AGE DISAMBIGUATION (multi-token narrative)

### O problema

Narrativa "America Is Back" emerge. ETH + Base + Solana + BSC têm múltiplos tokens:
- AIB em ETH (2 anos, tem histórico)
- AIB em Base (3 meses)
- AIB em Solana (1 semana, 50 holders)
- AMERICA em Base (1 hora, farmeiro)

**V2 errava** pegando o primeiro match. **V3 precisa escolher certo.**

### Regra do mercado (sua intuição confirmada):

> Mercado converge no TOKEN MAIS VELHO com atividade real. Novos tokens da mesma narrativa são copycats, distribuem volume mas o "original" é onde o dinheiro vai.

### Nova skill: `narrative_candidate_ranker`

```yaml
input: narrative + keywords + emerging velocity
process:
  1. Search ALL chains (ETH, Base, Solana, BSC) via:
     - DexScreener search API: /latest/dex/search?q={keyword}
     - virtuals_all_tokens.db (local)
     - Flaunch, Clanker DBs
  2. For each candidate, collect:
     - age_days (creation timestamp)
     - current_volume_24h
     - holder_count
     - mcap
     - price_change_24h
  3. Filter out:
     - Honeypots (GoPlus)
     - Liquidity < $5k
     - 0 holders growth last 6h (dead)
     - Extreme farmer creator (>20 launches)
  4. Rank by composite score:
     age_score = log(age_days) / 10         # older = higher (cap at 1.0)
     activity_score = volume_growth_24h     # pumping = higher
     holder_score = log(holders) / 5        # more holders = legit
     narrative_fit = semantic_match_score   # V2 match
     
     final = age_score × 3 + activity_score × 2 + holder_score × 1.5 + narrative_fit × 2
  5. Return TOP 3 candidates with reasoning
```

**Exemplo:** "America Is Back" narrative:

| Token | Chain | Age | Volume 24h | Holders | Score | Action |
|---|---|---|---|---|---|---|
| AIB | ETH | 2y | $120K | 3.2K | **9.1** | **PRIMARY** |
| AIB | Base | 3mo | $37K | 562 | 6.2 | Secondary (farmer copy) |
| AMERICA | Solana | 1w | $8K | 120 | 3.8 | Scam probable, skip |
| FREEDOM | Base | 1h | $2K | 20 | 1.2 | Farmer — skip |

Alert destaca **AIB ETH** com note "2 copycats detectados — liquidity se concentra no original"

### Nova tool MCP: `find_tokens_by_narrative`

```python
Tool(
    name="find_tokens_by_narrative",
    description=(
        "Given a narrative or keyword, find ALL matching tokens across chains "
        "(ETH, Base, Solana, BSC). Returns list ranked by age + activity + "
        "holder quality. Use this when narrative emerges to pick the RIGHT "
        "token (usually oldest with real activity)."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "narrative": {"type": "string"},
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
            },
            "chains": {
                "type": "array",
                "default": ["eth", "base", "solana", "bsc"]
            },
            "min_liquidity_usd": {"type": "number", "default": 5000},
        },
        "required": ["narrative", "keywords"]
    }
)
```

### Cross-chain lookup: como fazer sem reinventar

**ETH, Base, BSC**: DexScreener search API pega tudo (`/latest/dex/search?q={keyword}`)
**Solana**: DexScreener também cobre via mesmo endpoint
**Filtering por age**: DexScreener retorna `pairCreatedAt` unix timestamp

Sem precisar adicionar RPCs novos. Uma chamada HTTP cobre 4 chains.

---

## FIX 2: LATENCY DROP — 6min → <90s

### Onde o tempo vai no V2

```
T=0:  volume_spike detected         (instantaneo)
T=0:  narrative_emerging detected   (rodava 5min cron — era o gargalo)
T=0:  token-narrative match         (rodava 5min cron — outro gargalo)
T=5:  Hermes investigate            (5min de budget — demais)
T=6:  alert                         (formatação ~1s)
```

### Alvo V3

```
T=0s:   sinal chega
T=10s:  narrative engine re-processa (event-driven, não cron)
T=30s:  token-narrative match (fast path)
T=60s:  Hermes fast-path investigation (paralelo agressivo)
T=75s:  alert delivered
```

### Mudanças arquiteturais

#### A) Narrative engine: cron → event-driven

Em vez de cron 5min, usa **fila de prioridade** com workers:

```python
# Qualquer ingestão nova (tweet, news, telegram) → push pra fila
narrative_queue.push({
    content: ...,
    source: ...,
    priority: calculate_priority(source, author)
})

# 3 workers consumindo paralelo, latency ~1-3s por item
```

**Priority rules:**
- Tweet de MEGA account (Elon, CZ, Vitalik) → priority 10 (processado em <5s)
- Tweet de ALPHA tier → priority 5
- News de fonte confiável → priority 4
- Tweet normal → priority 1

#### B) Token-narrative matcher: cron → event-driven

Quando `NARRATIVE_EMERGING` é emitido, dispara matcher IMEDIATAMENTE:

```python
on_event("NARRATIVE_EMERGING"):
    # Só roda matcher pra essa narrativa, não pra todas
    candidates = match_narrative_to_tokens(narrative)
    for candidate in candidates:
        emit_event("NARRATIVE_MATCH", candidate)
```

Tempo: ~3-5s por narrativa.

#### C) Hermes fast-path investigation

V2 gastava 5min em subagents. V3 tem DOIS MODOS:

**FAST PATH (60s)** — pra casos urgentes (narrativa quente + weak signal):
```yaml
- Skip deep creator research
- Use cached contract safety (GoPlus 5min cache)
- Only 1 subagent: narrative confirm + top 3 tokens ranking
- Decision: ALERT if matched_token is_primary AND contract ok
```

**DEEP PATH (3-5min)** — pra casos menos urgentes:
- Full V2 pipeline (creator, holders, history, social)
- Usado pra convergence alerts (menos tempo-crítico)

**Decisão fast vs deep:**
```
if narrative.velocity > 3.0 AND time_since_first_mention < 20min:
    use FAST_PATH  # catalyst é recente, precisa ser rápido
elif weak_signal + narrative_match:
    use FAST_PATH
else:
    use DEEP_PATH
```

#### D) Pre-caching

Cache de 5min pra queries comuns:
- GoPlus safety por token
- DexScreener price
- Sorsa score por handle

Redis já existe — só adicionar `hunter_cache:*` keys.

#### E) Paralelismo agressivo

Hermes config: `subagents.max_concurrent: 5` (era 3 no V2).

Para narrative urgent:
- Subagent 1: find_tokens_by_narrative (novo tool)
- Subagent 2: check_contract_safety em top 3 candidates PARALELO
- Subagent 3: search_narrative_context

Todos em 30s max.

**Resultado: alert em ~75s (antes 6min).**

---

## FIX 3: MEGA ACCOUNTS TIER SYSTEM

### O problema

Elon Musk tweeta "Doge to the moon" → 2 min depois DOGE pumpa 30%.
Você pode ganhar MUITO se pegar esse tweet em tempo real.

V2 tratava Elon igual qualquer KOL. Perdia oportunidade.

### Tier system novo

```sql
CREATE TABLE monitored_accounts (
    handle TEXT PRIMARY KEY,
    platform TEXT NOT NULL,           -- 'twitter', 'telegram'
    tier TEXT NOT NULL,                -- 'MEGA', 'ALPHA', 'RISING', 'NOISE'
    follower_count INT,
    verified INT,
    specialty TEXT[],                  -- ['crypto', 'ai', 'memecoins']
    avg_pump_when_posts REAL,          -- backtest: quando tweeta sobre cripto, avg pump
    signal_quality_score REAL,         -- 0-1
    added_at TIMESTAMPTZ DEFAULT NOW(),
    last_checked_at TIMESTAMPTZ
);
```

### 4 tiers com comportamentos diferentes

**TIER MEGA** (5-20 accounts — top market movers)
Exemplos: @elonmusk, @cz_binance, @VitalikButerin, @SBF_FTX (morto mas exemplo), @aeyakovenko (Solana), @justinsuntron

Handling:
- Tweet processado em **<5s**
- **Qualquer menção a ticker/token** → FAST_PATH investigation imediata
- Source_weight = **5.0** (mais alto)
- `find_tokens_by_narrative` disparada se tweet não menciona token específico
- Alerta "MEGA ACCOUNT SIGNAL" com destaque visual

**TIER ALPHA** (50-100 accounts — top alpha callers)
Exemplos: @ansemtrades, @beaniemaxi, @CryptoKaleo, etc.

Handling:
- Tweet processado em <30s
- Menção a token → verifica contract + narrative, alerta se ok
- Source_weight = 2.5
- Rastreia track record (quantos picks deram hit)

**TIER RISING** (200-500 accounts — growing callers)
Selecionados automaticamente: contas com follow growth >20%/mês + crypto-focused

Handling:
- Tweet processado em <2min
- Só alerta se múltiplas contas RISING convergem
- Source_weight = 1.0
- Promoção automática pra ALPHA se hit rate > 40% em 30 dias

**TIER NOISE** (blocklist)
Shillers conhecidos, promoters pagos, contas com histórico de rug

Handling:
- Processado mas filtrado (não entra em convergence)
- Serve pra detectar "shill coordination" (mesmo token em X contas TIER NOISE = red flag)

### Auto-discovery de novos accounts

Skill `discover_alpha_accounts` (1×/semana):
1. Analisa hits dos últimos 30 dias
2. Pra cada hit: quem foi PRIMEIRO a mencionar o token?
3. Se conta desconhecida + primeiro a mencionar token + token virou hit = candidato
4. Se conta aparece em 3+ hits como primeiro = promove pra RISING
5. Hermes documenta em `obsidian-vault/08-Context/alpha_accounts.md`

**Começa com:**
- MEGA: Elon, CZ, Vitalik, Aeyakovenko, Brian Armstrong, Saylor, pudgypenguins, Punk6529, Hsaka (Toly), Raoul Pal
- ALPHA: você me passa 30-50 que você segue (quem é suas referências?)
- RISING: descoberto automaticamente

---

## FIX 4: NARRATIVE STAGES (fases da narrativa)

### O problema

Narrativa tem ciclo. Entrar no estágio errado = prejuízo mesmo com narrativa certa.

### 4 estágios

```
       mentions/hour
           │
       ┌───┤ PEAK
       │   │  (sell zone)
       │   │
GROWTH │   │  ┌─ COOLING
  (buy)│   │  │  (exit)
       │   │  │
       │   │  │
   ────┘   │  └──────
           │          ────
  early    │               └─── dead
(best buy)
```

**STAGE 1: EMERGING** (velocity 1-2×, <20 mentions/h)
- Acabou de começar
- Risco: pode morrer rápido, pode explodir
- Ação: **INVESTIGATE with caution**, alerta se token passa checks
- **ALPHA MAX** — entra aqui, ganha mais

**STAGE 2: GROWING** (velocity 2-5×, 20-100 mentions/h)
- Narrativa confirmada, subindo
- Risco médio
- Ação: **ALERT aggressively**, melhor tempo pra entrar
- **GOOD ALPHA**

**STAGE 3: PEAK** (velocity >5× OR mentions >100/h, engagement estabiliza)
- Todo mundo já tá falando
- Smart money tá SAINDO
- Ação: **NO NEW ENTRY**, alerta pra quem já tá dentro "considera exit"
- Se você tem posição: SELL SIGNAL

**STAGE 4: COOLING** (velocity <1×, mentions caindo)
- Rally acabou
- Ação: **EXIT se ainda dentro**, não entra
- Monitora pra segundo pump improvável

### Detecção de stage

```python
def detect_narrative_stage(narrative: dict) -> str:
    hourly_mentions = narrative.last_hour_mentions
    prev_hour_mentions = narrative.prev_hour_mentions
    velocity = hourly_mentions / max(prev_hour_mentions, 1)
    
    # Also check engagement (likes/RTs per mention)
    engagement_trend = narrative.engagement_last_hour / narrative.engagement_prev_hour
    
    if hourly_mentions < 20 AND velocity > 1.0:
        return "EMERGING"
    elif hourly_mentions < 100 AND velocity > 2.0:
        return "GROWING"
    elif hourly_mentions >= 100 OR velocity > 5.0:
        if engagement_trend < 1.0:  # engagement caindo = already peaked
            return "COOLING"
        return "PEAK"
    elif velocity < 1.0:
        return "COOLING"
    return "UNKNOWN"
```

### Alertas por stage

```
EMERGING + token match:
  📊 EARLY NARRATIVE ALERT
  ⚡ Narrative "{X}" just emerging (velocity {v}×)
  🎯 Primary token: {symbol} ({age}) — {reasoning}
  🛡️ Risk: medium (early stage, narrative may fizzle)
  ✅ BUY window: NOW (biggest upside if narrative sticks)

GROWING + token match:
  📈 NARRATIVE ALERT
  🔥 Narrative "{X}" growing ({v}× growth, {count} mentions/h)
  🎯 Primary: {symbol} | Secondary: {secondary}
  ✅ BUY window: still good, getting late

PEAK + existing position:
  ⚠️ NARRATIVE PEAK — EXIT SIGNAL
  Narrative "{X}" has peaked ({count} mentions/h, engagement flat)
  Your position in {symbol}: consider taking profit

PEAK + no position:
  ❌ TOO LATE — narrative "{X}" at peak
  No new entries. Watch for next narrative.
```

---

## FIX 5: CONTINUOUS LEARNING SYSTEM

### O problema

Sistema estático não melhora. Mercado muda toda semana (narrativas novas, KOLs novos, padrões novos).

### 3 mecanismos de aprendizado

#### A) Postmortem semanal (automático)

Skill `weekly_postmortem` (domingo 03:00 UTC):

```yaml
process:
  1. Lista todos alertas da semana (hunter_alerts)
  2. Calcula outcome em 24h e 7d (hunter_outcomes)
  3. Categoriza:
     - HITS (ROI >= 50% em 24h): qual padrão funcionou?
     - MISSES (ROI <= -30% em 24h): qual padrão falhou?
     - UNRESOLVED (-30% a 50%): ambíguo
  4. LLM analisa:
     "Dos 15 hits, 12 tinham narrative_stage=EMERGING e token_age > 6mo"
     "Dos 8 misses, 6 tinham creator_reputation=SUSPICIOUS que ignoramos"
  5. Propõe ajustes:
     - Aumentar weight de narrative_stage=EMERGING de 1.5× para 2.0×
     - Bloquear tokens de creator_reputation=SUSPICIOUS incondicionalmente
  6. Aprova auto ajustes < 20% change
     Pra ajustes maiores, pede confirmação no Telegram
  7. Salva analise em obsidian-vault/08-Context/weekly_postmortem_{date}.md
```

#### B) Hermes auto-generated skills

Hermes Agent já tem auto-skill-generation. Vamos priorizar:

Padrões recorrentes → viram skills:
- "narrative ETH + age>1y token + velocity 2-5×" → virou skill `eth_narrative_play`
- "Elon tweets about X" → virou skill `megakol_reaction`
- "3+ RISING KOLs posting same ticker" → virou skill `rising_convergence`

Skills rodam automaticamente quando padrão detectado.

Storage: `/home/hermes/.hermes/skills/auto_*.md` (prefix `auto_` pra marcar autogerado)

#### C) Memory persistente do usuário

Hermes mantém `/home/hermes/.hermes/user_jackson.md`:
```markdown
# Jackson — Trading Profile

## Preferences (aprendido de comportamento)
- Prefere tokens com mcap $100k-$2M (sweet spot)
- Sai em +100% normalmente (base de 30 trades)
- Evita projetos brancos sem equipe
- Tem bias pra narrativas políticas/geopolíticas (AIB, MAGA)

## Wallets que confia (adicionadas manualmente)
- 0xabc... (Jeet vault)
- ...

## Narrativas que performou bem comigo
- america_is_back: 4 plays, 3 hits, avg 140% ROI
- ai_agents: 6 plays, 2 hits, avg 30% ROI (não é seu setor)

## Lessons learned
- "Evitar entrar em PEAK, perdi $X no {token} por entrar tarde"
- "Narrativas políticas tendem a pumpar durante eleições US"
```

Hermes consulta isso antes de alertar — ajusta mensagem ao seu perfil.

#### D) A/B testing de source weights

Weights não são estáticos. Hermes mantém 2 conjuntos:
- `weights_stable` (usado pra 80% das decisões)
- `weights_experimental` (usado pra 20%, testando variação)

Se experimental supera stable em 30 alertas → promove, começa novo experimento.

---

## NOVO DECISION FLOW (V3)

```
Sinal/tweet/evento chega
           │
           ▼
   Priority routing
   ├─ MEGA tweet → 5s FAST_PATH
   ├─ Narrative EMERGING → 60s FAST_PATH
   ├─ Convergence ≥ 60 → standard path
   └─ Weak signal → investigate if narrative match
           │
           ▼
   Narrative stage detection
   ├─ EMERGING → prefer OLDEST matching token, BUY signal
   ├─ GROWING → alert normal, still good entry
   ├─ PEAK → NO new alerts, EXIT signal to positions
   └─ COOLING → skip
           │
           ▼
   Token candidate ranking
   (if multiple tokens match narrative)
   → rank by: age × activity × holders × narrative_fit
   → return TOP 3, alert on PRIMARY
           │
           ▼
   Safety + creator checks
   ├─ GoPlus safe?
   ├─ Creator reputation > SUSPICIOUS?
   ├─ Liquidity > $5k?
   └─ Not in blacklist?
           │
           ▼
   Deliver alert (with stage + candidate info)
           │
           ▼
   Track outcome → feedback to learning system
```

---

## MÉTRICAS ATUALIZADAS

| Métrica | V1 alvo | V2 alvo | V3 alvo |
|---|---|---|---|
| Signal → alert latency | N/A | 5-10min | **<90s** |
| Precision (24h hits %) | 30% | 40% | **50%+** |
| Recall (% top pumps detected) | 40% | 60% | **70%+** |
| Early alpha % (antes smart money) | 10% | 30% | **50%+** |
| Alerts/dia | 20 | 10 | **5-8 (quality over quantity)** |
| Learning cycle | manual | weekly | **daily postmortem + weekly deep** |

---

## UPGRADES EM ARQUIVOS EXISTENTES

### `hermes_config.yaml` — novos campos
```yaml
# tier system
mega_accounts_file: /home/hermes/.hermes/accounts_mega.yaml
alpha_accounts_file: /home/hermes/.hermes/accounts_alpha.yaml

# urgency routing
urgent_processing_latency_ms: 5000  # <5s pro MEGA tier
fast_path_budget_seconds: 60

# narrative stages
narrative_stages_config:
  emerging_velocity_max: 2.0
  growing_velocity_min: 2.0
  peak_velocity_min: 5.0
  peak_mentions_min: 100

# learning
postmortem_schedule: "0 3 * * 0"  # Sundays 03:00 UTC
learning_weights_exp_ratio: 0.2   # 20% experimental

# multi-chain narrative search
narrative_scan_chains:
  - eth
  - base
  - solana
  - bsc
```

### `mcp_server.py` — novas tools (13-20)

- `find_tokens_by_narrative` (13) — multi-chain search + rank por idade
- `check_twitter_mentions_recent` (14) — quem tuitou recente
- `list_emerging_narratives` (15) — narrativas ativas agora
- `check_token_narrative_matches` (16) — matches pra um token
- `get_account_tier` (17) — MEGA/ALPHA/RISING/NOISE de um handle
- `detect_narrative_stage` (18) — EMERGING/GROWING/PEAK/COOLING
- `get_user_preferences` (19) — memória pessoal do Jackson
- `log_learning` (20) — registrar insights pro postmortem

### `hermes_config.yaml` skills auto-carregadas
```yaml
skills:
  autoload:
    - research_token_converged
    - proactive_investigation
    - find_narrative_primary_token       # NOVO
    - weekly_postmortem                  # NOVO
    - discover_alpha_accounts            # NOVO
    - update_user_profile                # NOVO
  auto_generate: true                    # Hermes gera novas skills dos padrões
```

---

## TIMELINE V3 (adicional ao V1+V2)

V3 Fases (+5-7 dias sobre V1+V2):

**Fase 10**: Token age ranker + multi-chain search (2 dias)
**Fase 11**: MEGA tier + priority routing (1-2 dias)
**Fase 12**: Narrative stages (1 dia)
**Fase 13**: Postmortem + learning loop (2 dias)

**Grand total V1+V2+V3: ~18-22 dias**. Faseado pra você ver resultado desde semana 1.

---

## DECISÕES FINAIS REQUERIDAS

Repito as pendências + novas:

**Ainda preciso (fundamentais):**
1. Mac Mini vs Hetzner — SPECS ou decisão
2. Token @dualhermes_bot
3. API_ID + API_HASH (my.telegram.org)
4. 3-5 grupos Telegram iniciais

**Novas pro V3:**
5. **30-50 handles ALPHA tier** pra você confiar (me passa quem você segue como alpha caller)
6. **5-8 news sources** quer incluir: CoinDesk, RT, Decrypt, The Block, Cointelegraph, outras?
7. **Tolerância de latência**: fast-path alvo <90s tá bom ou quer mais agressivo?
8. **Alertas/dia cap**: 5, 8, 10? (V3 foca qualidade)
9. **Auto-ajuste de weights**: autorizar Hermes a ajustar sozinho até X%/semana? (sugiro 20%)
10. **Multi-chain na v1**: Solana + BSC desde o início ou só ETH+Base?

---

## RESUMO: V1 → V2 → V3

- **V1**: orquestração + convergência (base sólida)
- **V2**: + narrativa + investigação proativa (pega alpha antes)
- **V3**: + multi-token ranking + MEGA tier + stages + learning (pega melhor, mais rápido, aprende)

V3 é o que você quer. V1 e V2 são partes necessárias pro V3 funcionar. Não é trocar — é construir em cima.

**Próximo passo**: me responde as 10 decisões acima que eu consolido num plano executivo e começo a construir.
