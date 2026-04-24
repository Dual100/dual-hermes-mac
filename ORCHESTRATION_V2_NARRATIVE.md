# DualHermes Hunter — UPGRADE V2: Narrative Engine + Proactive Investigator

**Supplemental to:** `ORCHESTRATION_MASTER.md`
**Trigger:** User insight that convergence alone misses alpha — need narrative detection + proactive investigation

---

## O QUE FALTOU NO V1

V1 era **reativo**: espera múltiplas fontes convergirem → aí investiga.

Problema concreto que você levantou (caso AIB):
- Token AIB teve volume spike 30× ($1.3K → $37.9K)
- **Smart money: 0 compras, 0 vendas**
- Monitor marcou como WEAK, teria sido ignorado
- **MAS** se cruzássemos com narrativa do dia (ex: tweet RT_com sobre geopolítica), teríamos descoberto que AIB = "America Is Back" matching narrativa política quente = ALPHA ANTES do smart money chegar

**A regra que V1 quebra:** quando smart money compra, já é tarde. Eles são o piso, não o teto. **O alpha real é descobrir ANTES deles.**

V2 resolve isso adicionando **2 novas camadas**:
1. **NARRATIVE ENGINE** — escaneia narrativas emergindo no Twitter/news
2. **PROACTIVE INVESTIGATOR** — cruza narrativa com tokens e investiga weak signals

---

## CAMADAS COMPLETAS (V1 + V2)

```
┌──────────────────────────────────────────────────────────────────────┐
│  L1: FONTES (multi-source event ingestion — V1)                      │
│  Genesis, Clanker, Smart Money, Telegram, Auto-Discovery, aGDP, etc  │
└────────────────────────┬─────────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│  L2: CONVERGENCE ENGINE (reactive — V1)                              │
│  Agrupa sinais por token, score baseado em source_weight             │
└────────────────────────┬─────────────────────────────────────────────┘
                         │
                         ▼
┌══════════════════════════════════════════════════════════════════════┐
║  L3: NARRATIVE ENGINE (proactive — V2 NOVO)                          ║
║  Escaneia X/news em loop, extrai narrativas emergindo                ║
║  Matcheia narrativas com tokens em observação                        ║
└════════════════════════┬═════════════════════════════════════════════┘
                         │
                         ▼
┌══════════════════════════════════════════════════════════════════════┐
║  L4: PROACTIVE INVESTIGATOR (V2 NOVO)                                ║
║  Triggered por: convergência (V1) OU narrativa (V2) OU weak signal   ║
║  Hermes age como jornalista: "por que esse volume? qual a história?" ║
└────────────────────────┬─────────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│  L5: DELIVERY + FEEDBACK (V1)                                        │
│  Alerta → outcome tracking → auto-tuning                             │
└──────────────────────────────────────────────────────────────────────┘
```

---

## L3: NARRATIVE ENGINE (novo)

### 3.1 O que faz

Roda continuamente em background:
1. Coleta **narrativas ativas** do mercado
2. Rankeia por **velocidade** (quão rápido tá subindo)
3. Persiste em tabela `narratives` com scoring
4. Emite evento `NARRATIVE_EMERGING` quando algo esquenta

### 3.2 Fontes de narrativa

| Fonte | O que extrai | Latência | Qualidade |
|---|---|---|---|
| **Twitter/X trending**                 | Hashtags + topics em trending global e US | 5min | ⭐⭐⭐ |
| **KOL tweets cluster**                 | O que 50 KOLs crypto estão tweetando agora | 2min | ⭐⭐⭐ |
| **@virtuals_io + partners**           | Anúncios oficiais do ecossistema | 5min | ⭐⭐⭐ |
| **Telegram grupos tier 1**             | Temas recorrentes nos grupos alpha | 5min | ⭐⭐ |
| **News sites (CoinDesk, RT, etc)**    | Eventos geopolíticos/macro que viram narrativa | 15min | ⭐⭐ |
| **Google Trends crypto**              | Termos crypto em alta de busca | 30min | ⭐ |
| **Reddit r/cryptocurrency**           | Threads com crescimento rápido | 30min | ⭐ |

### 3.3 Como detecta "narrativa emergindo"

Pseudocódigo:
```python
for each new piece of content (tweet, news, telegram msg):
    # LLM extrai "temas" com kimi-k2 (grátis)
    themes = llm.extract_themes(content)
    # ex: "AI", "geopolitics_US_politics", "memecoin_season", "restaking"
    
    for theme in themes:
        persist_mention(theme, source, timestamp, engagement)

# Cron a cada 5min:
for theme in all_themes:
    mentions_last_1h = count(theme, last=1h)
    mentions_prev_1h = count(theme, prev_hour)
    velocity = mentions_last_1h / max(mentions_prev_1h, 1)
    
    if velocity >= 3.0 AND mentions_last_1h >= 10:
        emit_event("NARRATIVE_EMERGING", theme, velocity)
```

### 3.4 Nova tabela `narratives`

```sql
CREATE TABLE narratives (
    id BIGSERIAL PRIMARY KEY,
    theme TEXT NOT NULL,            -- 'america_is_back', 'ai_agents', 'btc_new_ath'
    description TEXT,
    first_seen_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    mention_count INT,
    unique_sources INT,
    velocity REAL,                   -- mentions_per_hour growth rate
    peak_velocity REAL,
    related_handles TEXT[],          -- @kol1, @kol2 tweeting this
    related_keywords TEXT[],         -- 'AIB', 'MAGA', 'america'
    status TEXT,                     -- 'emerging', 'peaked', 'cooling', 'dead'
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE narrative_mentions (
    id BIGSERIAL PRIMARY KEY,
    narrative_id BIGINT REFERENCES narratives(id),
    source TEXT,                     -- 'twitter', 'telegram', 'news'
    source_id TEXT,                  -- tweet_id, msg_id, url
    author TEXT,
    engagement INT,
    content_excerpt TEXT,
    created_at TIMESTAMPTZ
);

CREATE INDEX idx_narr_emerging ON narratives (velocity DESC) WHERE status = 'emerging';
CREATE INDEX idx_narr_mentions_created ON narrative_mentions (narrative_id, created_at DESC);
```

### 3.5 Token ↔ Narrative matching

Crítico: quando narrativa `america_is_back` emerge, precisa encontrar tokens que se encaixam.

**3 estratégias rodando em paralelo:**

**Estratégia A: Keyword match direto (rápido)**
- Pega narrativa.related_keywords = `['AIB', 'MAGA', 'america', 'trump']`
- Busca em `virtuals_all_tokens`: tokens com symbol/name/description matching
- Output: lista de candidatos

**Estratégia B: Embedding similarity (médio)**
- Cada token tem embedding do seu `description + name + symbol` (precomputado semanalmente)
- Cada narrativa tem embedding do seu `description + keywords + recent_tweets`
- Cosine similarity > 0.7 = match

**Estratégia C: LLM reasoning (profundo, só pra top matches)**
- Top 20 candidatos de A+B → LLM (kimi-k2) decide
- Prompt: "Token X tem tema [desc]. Narrativa Y é sobre [desc]. Match 0-10?"

Output: Nova tabela `token_narrative_matches`:

```sql
CREATE TABLE token_narrative_matches (
    id BIGSERIAL PRIMARY KEY,
    token_address TEXT NOT NULL,
    narrative_id BIGINT REFERENCES narratives(id),
    match_strength REAL,             -- 0-1
    match_method TEXT,               -- 'keyword', 'embedding', 'llm'
    matched_at TIMESTAMPTZ DEFAULT NOW(),
    reasoning TEXT                   -- LLM explanation se método='llm'
);

CREATE INDEX idx_tnm_token ON token_narrative_matches (token_address);
CREATE INDEX idx_tnm_narrative ON token_narrative_matches (narrative_id, match_strength DESC);
```

### 3.6 O que emite

Evento `NARRATIVE_MATCH`:
```json
{
  "token_address": "0xb3a0f70c...",
  "token_symbol": "AIB",
  "narrative": "america_is_back",
  "velocity": 5.2,
  "match_strength": 0.92,
  "match_method": "llm",
  "suggested_action": "INVESTIGATE"
}
```

Vai pro Hermes via `hunter_signals` com `source = 'narrative_engine'`, `source_weight = 2.5`.

---

## L4: PROACTIVE INVESTIGATOR (Hermes skill upgrade)

### 4.1 Os 3 triggers

Hermes é ativado por:

**Trigger 1: Convergence (V1)**
- Múltiplas fontes já apitaram
- `convergence_score >= 60`
- Mais fácil — sinal forte

**Trigger 2: Narrative match (V2 NOVO)**
- Uma narrativa esquentou + token matchea
- Investiga pra descobrir se é alpha antes de convergir
- **É aqui que você ganha dos outros**

**Trigger 3: Weak signal + context (V2 NOVO)**
- Monitor apitou WEAK (como AIB: volume spike sem smart money)
- Em vez de ignorar, Hermes investiga: **"por que esse spike?"**
- Se investigação revela narrativa quente → vira ALERT
- Se não revela nada → marca como `noise_investigated` (não repete)

### 4.2 O investigador como "jornalista crypto"

Em vez de só somar scores, Hermes age como jornalista investigativo:

```yaml
skill: proactive_investigation
prompt: |
  Token {address} teve sinal {type}. Investigue COMPLETAMENTE.
  
  Perguntas a responder (use subagents paralelos):
  
  1. O QUE É o token?
     - Nome, ticker, descrição, site, twitter, telegram
     - Quando foi launched? Bonding curve ou DEX?
     - Holder count, mcap, liquidity agora
  
  2. QUAL A HISTÓRIA por trás?
     - Creator é quem? Histórico de launches?
     - Token tem lore? (AI agent? meme? utility?)
     - Tem comunidade ativa ou deserto?
  
  3. POR QUE o sinal veio AGORA?
     - O que mudou nas últimas 2h?
     - Tweets recentes mencionando?
     - Volume spike é de 1 whale ou muitos holders?
     - Há narrativa macro que se encaixa com o token?
     - Tem notícia/tweet que pode ter disparado?
  
  4. QUEM tá dentro?
     - Top 10 holders são quem? Smart money? Dev bag?
     - Last 50 buyers — algum KOL?
     - Algum bot de sniping?
  
  5. QUAL O RISCO?
     - Contract safe? (GoPlus)
     - LP lock?
     - Taxas?
     - Creator farmeiro?
  
  Subagent A: Identidade + métricas (tools 1,2,3,4,5,7)
  Subagent B: História + creator (tools 4,6,8)
  Subagent C: Contexto + narrativa (tools 9,10 + web_search)
  
  Main consolida:
     ALERT se: narrativa match E contract ok E não obvious scam
     WATCH se: ambiguo, volta a olhar daqui 30min
     SKIP se: scam, rug potential, zero narrativa
     DEEP_DIG se: interessante mas falta info — agenda re-check
```

### 4.3 Nova tool essencial: `search_narrative_context`

Faltou no V1. Adiciona:

```python
Tool(
    name="search_narrative_context",
    description=(
        "Busca em todas as fontes (Twitter via Sorsa, Telegram groups, news, "
        "narratives table) por contexto ao redor de um token/ticker. "
        "Retorna: narrativas ativas relacionadas, menções recentes, timing "
        "de pumps anteriores vs eventos narrativos."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "lookback_hours": {"type": "number", "default": 6},
            "include_news": {"type": "boolean", "default": true}
        }
    }
)
```

### 4.4 Nova tool: `check_twitter_mentions_recent`

```python
Tool(
    name="check_twitter_mentions_recent",
    description=(
        "Quem tá tuitando sobre esse token/ticker/handle nas últimas X horas? "
        "Retorna tweets + autores + engagement. Usa Sorsa search + "
        "scrape de Nitter pros últimos 2h (mais fresco que Sorsa)."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "hours": {"type": "number", "default": 2}
        }
    }
)
```

---

## EXEMPLO WALKTHROUGH — CASO AIB

Simulação: como V2 teria pego AIB ANTES do smart money.

### T=0min: Volume spike alert chega no `hunter_signals`
```
source: 'ultra_monitor'
event_type: 'volume_spike'
token_address: '0xb3a0f70c...'
raw_data: { symbol: 'AIB', mcap: 231K, spike_ratio: 30.0, smart_money: 0 }
source_weight: 1.5
```

**V1 decision**: convergence_score ~= 15 (só 1 fonte, weak). **IGNORA.**

### T=0min (paralelamente): Narrative engine processa tweet RT_com
```
Narrative engine detectou nas últimas 4h:
  - 15 tweets de KOLs mencionando "America is Back"
  - 3 notícias RT sobre política americana
  - velocity = 4.2 (3× crescimento)
  
→ Emite NARRATIVE_EMERGING(theme='america_is_back', velocity=4.2)
```

### T=0min: Token-narrative matcher roda
```
Narrativa: america_is_back
  related_keywords = ['MAGA', 'america', 'AIB', 'trump', 'patriot']

Match estratégia A (keyword): token com symbol='AIB' → 1 match
Match estratégia B (embedding): similarity = 0.89
Match estratégia C (LLM): kimi-k2 decide "Token AIB (America Is Back) matches perfectly narrative america_is_back — strength 0.95"

→ Emite NARRATIVE_MATCH(token='0xb3a0f70c...', narrative='america_is_back', strength=0.95)
  source: 'narrative_engine'
  source_weight: 2.5
```

### T=0min: Convergence engine re-calcula AIB
```
Sinais do AIB nos últimos 30min:
  - volume_spike (ultra_monitor): weight 1.5, quality 1.0 = 1.5
  - narrative_match (narrative_engine): weight 2.5, quality 0.95 = 2.375

Sources únicas: 2 → diversity_bonus = 1.3
Time decay: 1.0 (recente)

convergence_score = (1.5 + 2.375) × 1.0 × 1.3 = 5.04

# Baixo... MAS narrative_match SOZINHA tem regra especial:
if narrative_match.strength > 0.85:
    force_trigger = True  # dispara investigator mesmo com convergence baixa
```

### T=5min: Hermes investigator ativado
```
Main agent recebe: "AIB (0xb3a0f70c...) — volume spike 30x + narrative match america_is_back 0.95"

Spawna 3 subagentes paralelos:

Subagent A (contract): GoPlus + holders + price = "safe, 562 holders, no honeypot, LP 50% locked"

Subagent B (creator): lookup wallet → twitter @aib_token → Sorsa score 42, account 1mo old, 3200 followers

Subagent C (narrative context):
  search_twitter_mentions("AIB", hours=2) → encontra 8 tweets de KOLs mid-tier
    - @crypto_maga (52K followers) tweetou há 45min "AIB finally moving" 
    - @patriot_trader (18K) "Got my bag of $AIB, narrative gonna explode"
  search_narrative_context("AIB america_is_back") → narrativa subindo desde 2h atrás
  timing: volume spike começou 30min DEPOIS primeira menção @crypto_maga

Main consolidates:
  - Narrativa real e subindo
  - Catalyst identificado (tweet @crypto_maga às XX:XX)
  - Contract OK, creator médio (não ideal mas ok)
  - Smart money AINDA não entrou — você pode entrar antes
  
  DECISION: ALERT
  final_score: 72/100
  confidence: 0.75
  reasoning: "Narrative-driven volume spike on political memecoin matching emerging 
              'america_is_back' narrative (velocity 4.2). Catalyst: @crypto_maga tweet 30min ago. 
              Smart money not in yet — early alpha window."
```

### T=6min: Alerta chega no @dualhermes_bot
```
🧠 HERMES NARRATIVE ALERT
━━━━━━━━━━━━━━━━━━━━━━━━
🪙 AIB (America Is Back) | Score 72
0xb3a0f70c...

📖 Narrative match: "america_is_back" (velocity 4.2×)
⚡ Catalyst detected: @crypto_maga tweet 30min ago (52K followers)
📈 Volume: 30× spike ($1.3K → $37.9K)
💰 MCap: $231K | Liq: $42.7K | 562 holders
🛡️ Contract: ✅ safe, no honeypot, 50% LP locked
🎯 Smart money: not in yet — EARLY WINDOW

⚠️ Risk: creator account only 1mo old, mid-tier

[RESEARCH] [BUY] [SKIP] [IGNORE narrative]
```

### T=45min: Smart money finalmente chega
```
smart_money_tracker detecta: 3 KOLs compraram AIB
V1 teria alertado AGORA
V2 já te avisou 39min antes

Outcome 24h: +280% (hit)
→ Marca AIB na hunter_outcomes
→ Narrative engine aprende: "america_is_back → AIB pattern working"
→ Skill autogerada: "when political narrative emerges, check political memecoins first"
```

**Isso é o alpha que V1 perderia e V2 pega.**

---

## NOVOS ARQUIVOS/COMPONENTES

Adiciono ao stack:

### Componente: `narrative_engine.py`
Service 24/7 que escaneia narrativas. Estrutura similar a `smart_money_tracker.py`.

### Componente: `token_narrative_matcher.py`
Cron 5min que cruza narrativas com tokens.

### Componente: `proactive_investigator.py` (é uma skill do Hermes, não script standalone)
Skill YAML em `/home/hermes/.hermes/skills/proactive_investigation.md`

### Tools novas no MCP:
- `search_narrative_context` (#13)
- `check_twitter_mentions_recent` (#14)
- `list_emerging_narratives` (#15) — lista narrativas ativas agora
- `check_token_narrative_matches` (#16) — quais narrativas matcham um token

### Tabelas Postgres novas:
- `narratives`
- `narrative_mentions`
- `token_narrative_matches`

### Configs novas:
- `KOL_HANDLES_TO_MONITOR` — 50 handles pra escanear tweets
- `NEWS_SOURCES` — RSS feeds (CoinDesk, RT, etc.)
- `NARRATIVE_VELOCITY_THRESHOLD` — quando emitir EMERGING

---

## DECISION TREE ATUALIZADA

```
Sinal chega → qual caminho?

┌─ convergence_score >= 60 → HERMES_DEEP_INVESTIGATE
├─ narrative_match.strength >= 0.85 → HERMES_DEEP_INVESTIGATE (V2)
├─ weak_signal (volume spike, etc) + narrative_match.strength >= 0.6 → HERMES_DEEP_INVESTIGATE (V2)
├─ narrative_emerging_no_token_match → HERMES_SCAN_TOKENS (V2 — encontra matches)
├─ convergence 30-60 → WATCH (adiciona lista, recheck em 30min)
└─ else → NOISE (descarta)
```

---

## RECURSOS CONSUMIDOS

### Custo LLM adicional (V2)
- Narrative extraction: ~50K tokens/hora (kimi-k2 grátis) = zero custo $
- Token matching (estratégia C): ~5K tokens por narrativa emergente × 5 narrativas/dia = 25K/dia
- Proactive investigation: ~30K tokens por invest × 20 invests/dia = 600K/dia

**Total**: ~800K tokens/dia extras. Kimi-k2 via nvidia-proxy = **$0**.

### Throughput
- Narrative engine: 1 processo Python, 100-200MB RAM
- Token matcher: cron, 200MB RAM por 30s a cada 5min
- Tudo dentro do budget de 2GB RAM do systemd

### Latência
- Narrativa detectada → match com token: 1-5min
- Match → investigação Hermes: 30s-2min
- **Total: signal → alert em ~2-7min**

---

## RISCOS DO V2 + MITIGAÇÕES

### R1: Falso positivo de narrativa
**Risco:** LLM marca narrativa onde não tem (halucinação)
**Mitigação:** requer 10+ mentions em 1h AND 3+ sources antes de emitir EMERGING

### R2: Narrativa manipulada (coordinated shill)
**Risco:** Pessoas pagam/coordenam pra trending, não é orgânico
**Mitigação:**
- Filtra mentions de contas <30 dias ou <500 followers
- LLM triage: is_shill flag (já existe na Telegram ingestion)
- Engagement real check (likes + RTs × follower baseline)

### R3: Sobrecarga de alertas
**Risco:** Toda narrativa dispara alerta
**Mitigação:**
- Max 10 alertas narrativos/dia (hard cap)
- Prioriza narrativas com velocity mais alta
- Usuário pode mutar narrativa específica

### R4: Embeddings desatualizados
**Risco:** Token novo não tem embedding ainda
**Mitigação:**
- Novos tokens: gera embedding na hora (delay 2s)
- Weekly rebuild de embeddings de top 10K tokens

---

## FASES DE ROLLOUT (ATUALIZADAS)

### Fase 0: Design aprovado (este doc + V1)
### Fase 1-5: V1 base (inalterado)
### Fase 6 (NOVO, 2 dias): Narrative Engine
- `narrative_engine.py` com Twitter + news ingestion
- Tabelas `narratives`, `narrative_mentions`
- LLM extração de temas rodando

### Fase 7 (NOVO, 1-2 dias): Token-Narrative Matcher
- Gera embeddings iniciais
- `token_narrative_matcher.py` cron 5min
- Tabela `token_narrative_matches`

### Fase 8 (NOVO, 1-2 dias): Proactive Investigator skill
- Skill YAML do Hermes
- Tools novas 13-16 no MCP server
- Integração com subagentes paralelos

### Fase 9: Treinar + tune
- Backfill: aplica V2 em 30 dias de histórico
- Mede: dos hits dos últimos 30 dias, quantos V2 teria pego antes do smart money?
- Ajusta weights

**Total V2: +5-6 dias sobre V1**. Grand total: **~12-15 dias** de trabalho.

---

## RESUMO FINAL

**V1 te dá**: consolidação de sinais existentes, score inteligente, menos spam
**V2 te dá**: detecção proativa de narrativas, investigação profunda antes de smart money, **ganha dos outros**

V2 não substitui V1 — ele **adiciona camadas de inteligência**. Convergência ainda importa (quando smart money entra, confirma). Mas o alpha real é antes.

**Próximas decisões:**

1. Aprovar V2 ou ficar só V1?
2. Se V2: quais handles KOL monitorar (me passa 30-50 pra começar)?
3. Quais fontes de news confiar? (CoinDesk, RT, Decrypt, etc — sugiro 5-8)
4. Threshold velocity inicial (sugiro 3.0× crescimento)
5. Aprovar custos? (R$0 adicional em API, só tempo de dev)

Se aprovar V2, atualizo `mcp_server.py` com tools 13-16 e escrevo os 3 novos scripts.
