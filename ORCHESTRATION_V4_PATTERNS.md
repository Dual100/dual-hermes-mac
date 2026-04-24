# DualHermes Hunter — V4: Patterns, Cascade, Speed

**Supplemental to:** V1 + V2 + V3
**Trigger:** User insights:
1. FLORK (0xce82213c...) — plataforma X tweetou → token pumpa (nem era KOL normal)
2. Cross-chain cascade (ETH → Base pumpa dps)
3. Backtest learning (ver top pumps atuais, estudar como começaram)
4. Speed crítico: <30s no hot path
5. Agente precisa ser MELHOR que o usuário — entender chain hotness, mercado

---

## FIX 6: PLATFORM SIGNALS (além de KOL)

### Caso FLORK

X (Twitter) postou "new update on X, flork as memes" → token $FLORK (`0xce82213c4bae42e1c04880ea64a53eef73e195de`) pumpa.

**Isso NÃO é KOL tweet.** É a **plataforma oficial** destacando algo. Diferente:

| Tipo | Exemplo | Latência esperada pro pump | Impacto |
|---|---|---|---|
| **MEGA KOL tweet** | @elonmusk menciona DOGE | 2-10min | 20-100% |
| **Platform signal** | X destaca flork em memes | 5-30min | 50-300% |
| **Integration release** | Binance lista token X | imediato | 30-80% |
| **App update** | Telegram novo feature + menciona token | 30min-2h | 20-60% |

**Platform signals são SUPER alpha** porque retail demora pra ver, mas instituições e devs atentos pegam.

### Novas fontes pra monitorar

```yaml
platform_accounts:
  mega:
    - "@X"                    # Twitter official
    - "@xfactor"              # X creator program
    - "@cz_binance"           # mas sinal binance oficial tbm
    - "@binance"              # exchange oficial
    - "@coinbase"             # exchange oficial
    - "@OKX"                  # idem
    - "@HTX_Global"
  
  platforms:
    - "@solana"               # Solana oficial
    - "@base"                 # Base oficial
    - "@virtuals_io"          # ecossistema
    - "@PumpDotFun"           # pump.fun
    - "@DexScreener"          # destaques trending
    - "@MoralisWeb3"          # dev tools
  
  media:
    - "@CoinDesk"
    - "@cointelegraph"
    - "@TheBlock__"
    - "@decryptmedia"
```

### Como processar diferente

Platform signal tem regras próprias:

```python
on_platform_signal(tweet):
    # 1. Extract entities fast (kimi-k2, 1s)
    entities = llm.extract_entities(tweet)
    # Returns: tokens_mentioned, memes_mentioned, features_mentioned
    
    # 2. Se menciona token explicitamente (e.g. ticker ou address):
    if entities.tokens:
        # HOT PATH: <10s investigation
        for token in entities.tokens:
            alert_via_hot_path(token, reason="PLATFORM_EXPLICIT")
    
    # 3. Se menciona meme/tema sem token:
    elif entities.memes or entities.themes:
        # Busca tokens com esse tema em 4 chains
        candidates = find_tokens_by_narrative(
            narrative=entities.memes[0],
            keywords=entities.themes,
        )
        # Top candidate pelo age+activity
        if candidates:
            alert_via_hot_path(candidates[0], reason="PLATFORM_THEME_MATCH")
    
    # 4. Se menciona feature/app update:
    elif entities.features:
        # Tokens relacionados à feature?
        # ex: "new X premium feature" → tokens do X ecosystem
        alert_as_WATCH(entities.features)
```

### Weight boost

```
source_weight:
  platform_explicit (token mencionado direto): 6.0  # maior peso ainda que MEGA
  platform_theme (tema sem token): 4.0
  platform_feature: 2.5
```

**Exemplo FLORK com V4:**
```
T=0s:   @X tweeta "new update on X, flork as memes"
T=1s:   LLM extrai: memes=["flork"], tokens=[], features=["X update"]
T=2s:   find_tokens_by_narrative(narrative="flork meme", chains=[eth,base,sol,bsc])
        → encontra: FLORK (ETH, 1y, $400k vol, primary), floki (mais velho mas diferente), etc.
T=5s:   HOT PATH investigation em FLORK ETH
        → contract safe, narrative match perfeito, platform_explicit signal
T=12s:  ALERT sent

⚡ Total: 12 segundos.
```

---

## FIX 7: CROSS-CHAIN CASCADE

### O padrão

Token com tema/nome comum pumpa em uma chain. Traders notam. Procuram o mesmo nome em outras chains menos cobertas. **Copycats em outras chains pumpam 30min-2h depois.**

Você apontou isso direto: **"quando bomba na rede ETH, a rede base deve bombar depois também o token de mesmo nome"**.

### Skill: `cascade_detector`

Nova skill Hermes, roda continuamente:

```yaml
process:
  1. Detecta pump primário (token em ETH ou Sol subiu >50% em <1h)
  2. Extrai: nome, ticker, narrative
  3. Busca mesmo nome/ticker em outras chains:
     - DexScreener /search?q={ticker}
     - Filtra por chains != chain_primary
     - Filtra por liquidity > $1k (evita shitcoin zero)
  4. Rank copycats:
     - age_days (older = more legit)
     - current_volume (atividade)
     - holder_growth_last_1h (já começou a subir?)
  5. Para cada copycat:
     - Se AINDA não pumpou mas tem atividade: ALERT CASCADE_OPPORTUNITY
     - Se JÁ pumpou (já virou +30%): ALERT CASCADE_CONFIRMED (menor alpha)
     - Se sem atividade: WATCH 30min depois
```

### Alert format

```
🌊 CASCADE OPPORTUNITY
━━━━━━━━━━━━━━━━━━━━
Primary: AIB on ETH pumped +85% in 45min
Copycats detected:
  🎯 AIB on Base | age 3mo | mcap $40K | +0% (NOT PUMPED YET — alpha window)
  ⚠️ AIB on BSC | age 1w | farmer creator — SKIP
  ✅ AIB on Solana | age 5mo | +12% (early rise — secondary play)

Recommended: Base primary play, Sol secondary
Expected cascade delay: 30-90min (based on 12 previous patterns)
```

### Histórico de cascades (aprendizado)

Tabela nova:

```sql
CREATE TABLE cascade_patterns (
    id BIGSERIAL PRIMARY KEY,
    primary_chain TEXT,
    secondary_chain TEXT,
    ticker TEXT,
    primary_pump_start TIMESTAMPTZ,
    primary_peak_pct REAL,
    secondary_pump_start TIMESTAMPTZ,
    secondary_peak_pct REAL,
    cascade_delay_minutes INT,
    cascade_success INT,              -- 1 if secondary pumped >30%
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

Hermes aprende: "ETH → Base cascade succeeds 68% of time with avg delay 47min, peak 82% of primary pump"

Usa esse conhecimento pra setar confidence em futuros alerts.

---

## FIX 8: BACKTEST PATTERN LEARNING

### O que você pediu

> "podemos pesquisar também para aprendizagem pq x token tá bombando no dexscreener e pesquisar como isso começou vendo quando começou a subir"

### Skill: `backtest_pump_anatomy`

Roda toda noite (ou on-demand):

```yaml
process:
  1. Pega top 100 pumps dos últimos 30 dias:
     - Query DexScreener: tokens com pump >100% em 24h
     - Cruza com tokens que já estão em virtuals_all_tokens.db
  2. Pra cada pump, faz forensics:
     - Quando começou a subir? (primeira hora de pump)
     - Qual foi o PRIMEIRO volume spike meaningful?
     - Que KOLs/contas tuitaram antes ou durante?
     - Que narrativa estava ativa?
     - Quem foram os PRIMEIROS 20 buyers? (smart money? bots?)
     - Tempo entre primeira menção e pump?
     - Foi catalyst externo (notícia) ou orgânico?
  3. Classifica o pump:
     - TYPE A: Platform-driven (X, Binance, Coinbase posted)
     - TYPE B: KOL-driven (MEGA tweet)
     - TYPE C: Narrative-driven (organic tema)
     - TYPE D: Cascade (copycat de outro pump)
     - TYPE E: Smart money first (eles descobriram)
     - TYPE F: Unknown/organic
  4. Extrai "assinatura" de cada tipo
  5. Popula tabela pump_patterns com padrões reconhecíveis
```

### Tabela pattern library

```sql
CREATE TABLE pump_patterns (
    id BIGSERIAL PRIMARY KEY,
    pattern_type TEXT,                -- 'platform_driven', 'kol_driven', etc.
    signature_features JSONB,          -- features que caracterizam o padrão
    occurrence_count INT,
    avg_roi_24h REAL,
    avg_time_to_peak_min INT,
    precision_rate REAL,               -- % de matches que deram hit
    last_seen_at TIMESTAMPTZ
);
```

Exemplo de assinatura TYPE A (platform-driven):
```json
{
  "pattern_type": "platform_driven",
  "signature_features": {
    "has_mega_platform_mention": true,
    "time_from_mention_to_pump_min": [0, 45],
    "avg_volume_spike_at_mention": 8.5,
    "smart_money_enters_after_min": [15, 60],
    "peak_reached_within_min": [60, 240]
  },
  "occurrence_count": 14,
  "avg_roi_24h": 85.3,
  "precision_rate": 0.71
}
```

### Como usa em real-time

Quando sinal novo chega:
1. Hermes extrai features do sinal atual (same feature set como pattern library)
2. Busca no `pump_patterns` qual padrão matcha
3. Se match encontrado com precision > 60%:
   - Alerta com contexto: "Matches TYPE A (platform-driven), historical precision 71%, avg ROI 85%"
   - Ajusta confidence
4. Se padrão não encontrado: marca como NEW_PATTERN, investiga, depois adiciona à library

**Efeito**: Hermes fica progressivamente mais esperto. Depois de 3 meses de backtesting, ele conhece os padrões do mercado melhor que você.

### Chain hotness tracker (subset do learning)

Dashboard interno calculado diariamente:

```python
def calculate_chain_hotness():
    for chain in ['eth', 'base', 'solana', 'bsc']:
        metrics = {
            'total_volume_24h': ...,
            'pump_count_24h': count(tokens pumped > 50%),
            'new_tokens_24h': ...,
            'unique_traders_24h': ...,
            'avg_pump_magnitude': ...,
        }
        
        # Comparison vs last week
        hotness_score = weighted_change_vs_baseline(metrics)
        
        save_to_chain_hotness(chain, hotness_score, metrics)
```

Hermes consulta hotness quando decide:
- "Solana tá 2.5x mais quente que semana passada → prioriza Solana em scans"
- "ETH cooling → cascade de ETH pra Base pode dar menos"

Tabela:
```sql
CREATE TABLE chain_hotness (
    date DATE,
    chain TEXT,
    volume_24h REAL,
    pump_count_24h INT,
    hotness_score REAL,             -- 0-100, comparison vs baseline
    rank INT,                        -- 1-N entre chains
    PRIMARY KEY (date, chain)
);
```

---

## FIX 9: SPEED MAX — <30s HOT PATH

### Budget de tempo

```
                HOT PATH (MEGA/Platform)   STANDARD   CONVERGENCE
                ---------------------      --------   -----------
Event ingest       50ms                     50ms       50ms
Classification      1s                      1s         1s
Fetch context       5s  (parallel 3x)       15s        30s
Analysis/score      3s                      10s        30s
Alert formatting    1s                      1s         1s
DELIVERY total    ~10s                     ~25-30s    ~60-90s
```

### Paralelismo máximo

Todo hot path executa isto em paralelo:

```python
async def hot_path_investigation(token_address, source, tweet_context):
    # Fires ALL parallel, max 5s budget each
    results = await asyncio.gather(
        fetch_contract_safety(token_address),           # GoPlus ~2s
        fetch_price_mcap(token_address),                # DexScreener ~1s
        find_cross_chain_copycats(token_address),       # DexScreener ~2s
        check_smart_money_on_token(token_address),      # DB lookup ~0.3s
        fetch_creator_quick(token_address),             # DB lookup ~0.3s
        check_pattern_library_match(tweet_context),     # DB lookup ~0.5s
        return_exceptions=True,
    )
    
    # Main agent consolida em 2-3s com kimi-k2
    decision = await llm.decide(results, tweet_context)
    
    return decision  # total ~7-10s
```

### Pre-warming (latency trick)

Certos dados ficam pre-warmed em cache Redis:

- `hotness:*` — chain hotness (atualizado 1×/hora)
- `active_narratives:*` — narrativas ativas (atualizado 30s)
- `smart_wallets:lookup` — set de wallets smart (atualizado 1×/dia)
- `blacklist:tokens` — tokens já marcados scam (load on startup)
- `pattern_signatures:*` — padrões conhecidos

Hermes lê cache antes de fazer query → zera latency do lookup.

### Event-driven subscriptions

Em vez de cron, tudo é pub/sub:

```
Redis pub/sub channels:
- hunter:new_signal
- hunter:narrative_emerging
- hunter:platform_signal
- hunter:cascade_detected
- hunter:mega_tweet
- hunter:high_alert
```

Cada componente subscreve nos canais que importa. Latency pub→sub: <50ms.

---

## FIX 10: "SER MELHOR QUE VOCÊ"

Você disse: **"o agente tem q aprender e ser melhor do que eu"**.

Isso é ambicioso. Pra chegar lá:

### 10.1 Benchmark contra seu histórico

Skill `benchmark_vs_user` (mensal):

```yaml
process:
  1. Lista tudo que você comprou nos últimos 30 dias (wallet_tracker)
  2. Lista tudo que Hermes ALERTOU nos últimos 30 dias
  3. Compara:
     - Você comprou 42 tokens, 18 hits (43%)
     - Hermes alertou 28, 14 hits (50%)
     - Overlap: 15 tokens (ambos pegaram)
     - User-only: 27 (você pegou, Hermes não alertou)
     - Hermes-only: 13 (Hermes alertou, você não pegou)
  4. Analisa gaps:
     - User-only que deram hit: por que Hermes não pegou? (melhorar detection)
     - User-only que deram miss: Hermes evitou certo? (Hermes > você aí)
     - Hermes-only hits: você perdeu alpha (Hermes > você)
     - Hermes-only misses: Hermes errou (bad precision)
  5. Learnings salvos em vault e Hermes ajusta
```

### 10.2 Market awareness

Tools novas:
- `get_chain_hotness()` — qual chain tá quente agora
- `get_active_narratives_top(n)` — top N narrativas ativas
- `get_megakol_recent_picks(n)` — o que os big accounts tuitaram últimas N horas
- `get_market_sentiment()` — fear/greed index + crypto market cap change
- `get_dominant_rotation()` — de que tipo pra que tipo (meme→AI? etc.)

Hermes consulta ANTES de qualquer decisão:
"Before I decide on this alert, what's the market context?"

### 10.3 Risk management automático

Hermes não só pega alpha — gerencia risco:
- Sabe seu portfolio total (wallet_tracker)
- Calcula exposure per narrative (não quer 80% em AI se AI cooling)
- Alerta concentration risk
- Sugere exits quando narrative PEAK

### 10.4 Teaching mode

Você pode usar comandos:
- `/explain <token>` — Hermes explica por que pegou ou não pegou
- `/why-miss <token>` — se você viu em outro lugar, Hermes investiga por que ele não pegou
- `/feedback <token> <hit|miss>` — você ensina manualmente o outcome

Esse feedback vai pro learning loop.

---

## NOVA TABELA RESUMO V1+V2+V3+V4

### Componentes rodando

| Componente | Status | Rodou em | RAM |
|---|---|---|---|
| hermes.service | V1 | systemd | 500MB |
| hermes_mcp_server (stdio) | V1 | subprocess | 200MB |
| telegram_group_monitor | V1 | systemd | 200MB |
| narrative_engine | V2 | systemd | 300MB |
| token_narrative_matcher | V2 | cron 5min | 200MB burst |
| platform_signal_listener (NEW V4) | V4 | systemd | 150MB |
| cascade_detector (NEW V4) | V4 | cron 1min | 100MB burst |
| chain_hotness_tracker (NEW V4) | V4 | cron 1h | minimal |
| backtest_learner (NEW V4) | V4 | cron daily | 500MB burst |
| postmortem (V3) | V3 | cron weekly | 300MB burst |
| outcome_tracker | V1 | cron 30min | minimal |

**Total RAM estável**: ~1.4GB, **cabe no hermes.service (limite 2G)**.

### Tabelas Postgres totais

```
V1:
- hunter_signals
- convergence_runs
- convergence_tokens
- hunter_alerts
- hunter_outcomes
- telegram_signals (de telegram_group_monitor)

V2:
- narratives
- narrative_mentions
- token_narrative_matches

V3:
- monitored_accounts

V4:
- cascade_patterns
- pump_patterns
- chain_hotness
```

14 tabelas novas. Todas com indexes apropriados. Roda fácil em Postgres.

### Fluxos completos

```
INGESTÃO (event-driven, pub/sub Redis)
  ├── genesis_websocket
  ├── bankrbot_blockchain_monitor
  ├── smart_money_tracker (42 wallets)
  ├── telegram_group_monitor (Telethon, N groups)
  ├── twitter_listener (MEGA + ALPHA + Platform accounts)
  ├── platform_signal_listener (X official, Binance, etc.)
  ├── news_aggregator (RSS CoinDesk, RT, etc.)
  ├── dexscreener_trending_poll (every 60s)
  └── wallet_tracker (sua wallet)
      │
      ▼
hunter_signals (append-only)
      │
      ▼
ANÁLISE (paralela)
  ├── narrative_engine (detecta temas)
  ├── convergence_engine (agrupa por token)
  ├── cascade_detector (ETH→Base etc.)
  ├── token_narrative_matcher (com age ranking)
  └── pattern_library_matcher (reconhece padrões históricos)
      │
      ▼
ROUTING (decision tree)
  ├── MEGA/Platform signal → HOT PATH (<15s)
  ├── Narrative emerging → FAST PATH (<60s)
  ├── Convergence high → STANDARD PATH (<90s)
  └── Weak signal + context → FAST PATH
      │
      ▼
HERMES INVESTIGATION
  Subagents parallel (up to 5):
  A) Contract + price + safety
  B) Creator + history + farmer
  C) Narrative + platform + cascade context
  D) Smart money + whales + top holders
  E) Pattern library match + chain hotness
      │
      ▼
DECISION
  Consolidação LLM (kimi-k2 grátis)
  Decide: ALERT | WATCH | SKIP
  Com: score, reasoning, risks, confidence
      │
      ▼
DELIVERY
  ├── Telegram @dualhermes_bot (MEGA: <15s, NORMAL: <90s)
  ├── Obsidian vault (persistent learning)
  └── Redis pub/sub (pra você integrar com algo futuro)
      │
      ▼
FEEDBACK LOOP
  ├── outcome_tracker (cron 30min: 1h/6h/24h/7d ROI)
  ├── postmortem (weekly: hits/misses analysis)
  ├── backtest_learner (nightly: new patterns)
  ├── benchmark_vs_user (monthly: are we beating you?)
  └── auto-tune weights (continuous, 20% experimental)
```

---

## NOVAS TOOLS MCP (V4 adds #21-28)

- `find_tokens_by_narrative` (V3 #13)
- `check_twitter_mentions_recent` (V3 #14)
- `list_emerging_narratives` (V3 #15)
- `check_token_narrative_matches` (V3 #16)
- `get_account_tier` (V3 #17)
- `detect_narrative_stage` (V3 #18)
- `get_user_preferences` (V3 #19)
- `log_learning` (V3 #20)
- **`find_cross_chain_copycats`** (V4 #21) — same ticker em outras chains
- **`get_chain_hotness`** (V4 #22) — qual chain tá hot agora
- **`match_pump_pattern`** (V4 #23) — achar padrão histórico
- **`extract_tweet_entities`** (V4 #24) — tokens/memes/features do tweet
- **`get_platform_signals_recent`** (V4 #25) — últimos platform signals
- **`explain_decision`** (V4 #26) — por que Hermes alertou/skipped
- **`benchmark_vs_user`** (V4 #27) — compara com histórico seu
- **`query_pattern_library`** (V4 #28) — padrões aprendidos

---

## TIMELINE COMPLETA

| Fase | V | Dias | O que entrega |
|---|---|---|---|
| 1 | V1 | 1d | Hermes base + systemd + kimi-k2 |
| 2 | V1 | 1-2d | MCP server + 12 tools base |
| 3 | V1 | 1-2d | Signal ingestion adapters |
| 4 | V1 | 1d | Convergence engine |
| 5 | V1 | 1d | Delivery + feedback |
| 6 | V2 | 2d | Narrative engine |
| 7 | V2 | 1-2d | Token-narrative matcher |
| 8 | V2 | 1-2d | Proactive investigator + tools 13-16 |
| 9 | V3 | 2d | Token age ranker + multi-chain |
| 10 | V3 | 1-2d | MEGA tier + priority routing |
| 11 | V3 | 1d | Narrative stages |
| 12 | V3 | 2d | Postmortem + learning |
| 13 | **V4** | 1-2d | Platform signal listener + tools 21,24,25 |
| 14 | **V4** | 1-2d | Cascade detector + tool 21 |
| 15 | **V4** | 2d | Backtest pattern learner + pattern_library |
| 16 | **V4** | 1d | Chain hotness + tool 22 |
| 17 | **V4** | 1-2d | Benchmark + teaching + tools 26,27,28 |

**Grand total: ~22-30 dias** de trabalho faseado. Você vê resultado semana 1, full system semana 4-5.

---

## DECISÕES (última lista consolidada)

### Infraestrutura (ainda faltam)
1. **Mac Mini model + RAM** (ou confirma Hetzner)
2. **Token @dualhermes_bot** (BotFather)
3. **API_ID + API_HASH** (my.telegram.org)

### Conteúdo/Config
4. **3-5 grupos Telegram tier 1** iniciais
5. **30-50 handles ALPHA** que você respeita
6. **5-8 news sources** (CoinDesk, RT, Decrypt?)
7. **MEGA accounts** — confirmo começar com: Elon, CZ, Vitalik, Aeyakovenko, Saylor, Brian Armstrong, X_oficial, Binance_oficial, Coinbase, OKX, Solana, Base, Virtuals_io, PumpDotFun, DexScreener — OK?
8. **Platform accounts** — inclui @X, @solana, @base, @coinbase etc?

### Comportamentos
9. **Alertas/dia cap**: 5, 8, 10 (qualidade)
10. **Fast path alvo: <30s? <60s? <90s?** — tradeoff precisão
11. **Auto-tune weights**: autorizado até 20%/semana?
12. **Multi-chain v1**: ETH+Base+Solana+BSC já ou só ETH+Base primeiro?

### Aprendizado
13. **Backtest inicial**: rodar em 30 dias de histórico ao inicial? (custa ~20M tokens LLM = $0 no kimi-k2)
14. **Benchmark vs você**: autorizo a comparar seus trades vs alertas?

### Chave/segredos
15. Você me passa acesso aos .env já existentes? Ou cria `.env` novo só com o subset pro Hermes?

---

## RESUMO EXECUTIVO V4

V4 adiciona **3 pedaços chave:**

1. **Platform signals** (FLORK, X atualizações, Binance listings) — source_weight 6.0, hot path <15s
2. **Cross-chain cascade** (ETH→Base/Sol) — auto-detect + alerta secundário com delay histórico
3. **Pattern library** (backtest learning) — Hermes estuda histórico, reconhece padrões, usa em tempo real
4. **Speed <30s hot path** — paralelismo agressivo, pre-warming Redis, event-driven
5. **Benchmark vs você** — mensal, pra medir se Hermes tá ficando melhor

Com V4 completo, o Hermes **realmente chega a ser melhor que você** em:
- Velocidade (reage em <30s, você em minutos)
- Abrangência (monitora 4 chains, 500+ contas, 50+ grupos simultâneos)
- Memória (padrão histórico de 30-90 dias disponível)
- Consistência (não se cansa, não ignora por viés, não perde signal por dormir)

**Mas você ainda ganha em:**
- Julgamento contextual ambíguo (quando 2 narrativas colidem)
- Relacionamentos (conhece pessoas, reputações)
- Risk tolerance (decide tamanho de posição)

**Isso é saudável.** Hermes automatiza o trabalho pesado, você mantém as decisões finais.

---

**Próximo passo:** me responde as 15 decisões acima. Quando fechar as 5 primeiras críticas (infra + bot + grupos), começo Fase 1 imediatamente.
