# DualHermes Hunter — MASTER FINAL (tudo consolidado)

**Este doc substitui os 4 anteriores.** Mantém tudo que você pediu, organizado.

---

## 1. O QUE VOCÊ QUER (suas palavras, em ordem que falou)

1. ✅ **Hermes Agent rodando paralelo ao OpenClaw** (não substitui)
2. ✅ **Segurança máxima** — crypto bot não pode ser afetado
3. ✅ **Research autônomo de tokens** — como um jornalista investigativo, não só reage
4. ✅ **Multi-fonte de alpha**:
   - Telegram groups (Telethon, sua conta)
   - Twitter/X (KOLs + plataformas oficiais)
   - DexScreener (trending, all chains)
   - Smart wallets (42 atuais + auto-descoberta)
   - On-chain events (já tem: genesis, clanker, etc)
5. ✅ **Pegar ANTES do smart money** (não esperar eles entrarem)
6. ✅ **Detectar narrativa emergindo** (ex: "America Is Back" → AIB)
7. ✅ **Pegar token MAIS VELHO com atividade real** quando narrativa matcha múltiplos tokens
8. ✅ **Cross-chain cascade** — ETH pumpa → Base/BSC/Sol mesmo nome pumpa depois
9. ✅ **MEGA accounts** (Elon, CZ) = tier especial, reação imediata
10. ✅ **Platform signals** (FLORK: @X oficial tweetou → pumpa)
11. ✅ **Narrativa tem FASES** — early/growing/peak/cooling = estratégias diferentes
12. ✅ **Backtest learning** — estuda top pumps pra aprender anatomia
13. ✅ **Speed <30s** no hot path
14. ✅ **Agente melhor que você** — aprende, adapta, supera
15. ✅ **Rodar múltiplos agentes Hermes** no Mac Mini pra outros projetos também
16. ✅ **Chain hotness** — qual chain tá bombando agora

**Nada perdido.** Tudo dos seus insights está incorporado.

---

## 2. ARQUITETURA EM 1 FRASE

**Um agente Hermes que escuta TUDO (9 fontes), detecta NARRATIVAS emergindo, encontra o TOKEN certo (mais velho com atividade), investiga em PARALELO (<30s), alerta ANTES do smart money, APRENDE dos outcomes.**

---

## 3. DIAGRAMA ÚNICO

```
╔════════════════════════════════════════════════════════════════════╗
║  FONTES DE SINAL (event-driven, pub/sub Redis)                     ║
╠════════════════════════════════════════════════════════════════════╣
║  1. genesis_websocket (Virtuals factories) ← JÁ TEM                ║
║  2. bankrbot (Clanker) ← JÁ TEM                                    ║
║  3. smart_money_tracker (42 KOL wallets) ← JÁ TEM                  ║
║  4. virtuals_auto_discovery ← JÁ TEM                               ║
║  5. agdp/dgclaw/flaunch ← JÁ TEM                                   ║
║  6. wallet_tracker (sua wallet) ← JÁ TEM                           ║
║  7. telegram_group_monitor (Telethon, N grupos alpha) ← NOVO       ║
║  8. twitter_listener (MEGA + ALPHA + Platform) ← NOVO              ║
║  9. dexscreener_trending_poll (60s) ← NOVO                         ║
║  10. news_aggregator (RSS: CoinDesk, RT, Decrypt) ← NOVO           ║
╚════════════════════════════╤═══════════════════════════════════════╝
                             │
                             ▼
╔════════════════════════════════════════════════════════════════════╗
║  ANÁLISE PARALELA                                                   ║
╠════════════════════════════════════════════════════════════════════╣
║  A. narrative_engine — detecta temas emergindo                     ║
║  B. convergence_engine — agrupa sinais por token                   ║
║  C. cascade_detector — ETH→Base/Sol cascade                        ║
║  D. token_narrative_matcher — com age ranking                      ║
║  E. pattern_library_matcher — reconhece padrões históricos         ║
║  F. platform_signal_listener — @X, @Binance, @Coinbase             ║
╚════════════════════════════╤═══════════════════════════════════════╝
                             │
                             ▼
╔════════════════════════════════════════════════════════════════════╗
║  ROUTING                                                            ║
╠════════════════════════════════════════════════════════════════════╣
║  MEGA/Platform tweet → HOT PATH (<15s)                             ║
║  Narrative emerging → FAST PATH (<30s)                             ║
║  Convergence high → STANDARD (<60s)                                ║
║  Weak signal + narrative → FAST PATH                               ║
╚════════════════════════════╤═══════════════════════════════════════╝
                             │
                             ▼
╔════════════════════════════════════════════════════════════════════╗
║  HERMES INVESTIGATION (5 subagents paralelos)                      ║
╠════════════════════════════════════════════════════════════════════╣
║  🔍 Subagent A: contract + price + safety                          ║
║  🔍 Subagent B: creator + history + farmer                         ║
║  🔍 Subagent C: narrative + platform + cascade context             ║
║  🔍 Subagent D: smart money + whales + top holders                 ║
║  🔍 Subagent E: pattern library + chain hotness + cross-chain      ║
║  ──────────────────────────────────────────────────────────        ║
║  Main agent consolida → decide: ALERT | WATCH | SKIP               ║
╚════════════════════════════╤═══════════════════════════════════════╝
                             │
                             ▼
╔════════════════════════════════════════════════════════════════════╗
║  DELIVERY                                                           ║
╠════════════════════════════════════════════════════════════════════╣
║  📱 Telegram @dualhermes_bot (HOT: <15s, NORMAL: <90s)             ║
║  📝 Obsidian vault (persistent learning)                            ║
║  🔔 Redis pub/sub (integração futura)                               ║
╚════════════════════════════╤═══════════════════════════════════════╝
                             │
                             ▼
╔════════════════════════════════════════════════════════════════════╗
║  FEEDBACK LOOP                                                      ║
╠════════════════════════════════════════════════════════════════════╣
║  outcome_tracker (30min: 1h/6h/24h/7d ROI)                         ║
║  postmortem (weekly: hits vs misses)                                ║
║  backtest_learner (nightly: top pumps forensics)                    ║
║  benchmark_vs_user (monthly: você vs Hermes)                        ║
║  auto-tune weights (contínuo, 20% experimental)                     ║
╚════════════════════════════════════════════════════════════════════╝
```

---

## 4. REGRAS DE DECISÃO (core da inteligência)

### Quando um sinal chega:

```python
if source == 'mega_platform' (Elon, X, Binance, CZ, etc.):
    → HOT PATH (<15s), weight 6.0, investigação imediata

elif narrative_matched and stage == 'EMERGING':
    → FAST PATH, weight 4.0, pega o OLDEST matching token (cross-chain)
    → "este é o momento de ouro — entra agora"

elif narrative_matched and stage == 'GROWING':
    → STANDARD PATH, weight 2.5, ainda bom mas tarde
    → "entra com ressalva"

elif narrative_stage == 'PEAK':
    → NO NEW ENTRY
    → se você tem posição no token: EXIT signal

elif weak_signal + narrative_match (>0.6):
    → FAST PATH investigation (isso é o caso AIB)
    → Hermes verifica: "por que esse volume? tem narrativa?"

elif convergence_score >= 60 (múltiplas fontes já apitaram):
    → STANDARD PATH (é confirmação, não edge)

elif cascade_detected (ETH pumpou, copycat em Base parado):
    → FAST PATH no copycat mais antigo/ativo
    → "cascade play, delay histórico 30-90min"

else:
    → WATCH ou NOISE
```

---

## 5. TOKEN AGE RANKING (quando narrativa matcha múltiplos)

**Regra que você apontou**: mercado converge no MAIS VELHO com atividade.

Fórmula:
```
score = age_score × 3 + activity_score × 2 + holder_score × 1.5 + narrative_fit × 2

age_score = log10(age_days + 1) / 3        # 365d = 0.86, 30d = 0.5, 1d = 0.1
activity_score = volume_24h / 50000         # $50k = 1.0
holder_score = log10(holders + 1) / 4       # 1000 = 1.0, 100 = 0.5
narrative_fit = similaridade semantic (0-1)
```

**Exemplo "America Is Back":**

| Token | Chain | Age | Vol 24h | Holders | Score | Ação |
|---|---|---|---|---|---|---|
| AIB | ETH | 730d | $120K | 3.2K | **9.1** | **PRIMARY** |
| AIB | Base | 90d | $37K | 562 | 6.2 | Secondary |
| AMERICA | Sol | 7d | $8K | 120 | 3.8 | Skip |
| FREEDOM | Base | 0.04d | $2K | 20 | 1.2 | Skip |

**Alert**: destaca AIB ETH, menciona AIB Base como cascade secundário.

---

## 6. SEGURANÇA (o seu maior medo — 9 camadas)

| # | Camada | O que faz |
|---|---|---|
| L0 | Usuário `hermes` isolado | Sem sudo, sem acesso a ubuntu/openclaw |
| L1 | systemd hardening | ProtectSystem, ProtectHome, MemoryLimit=2G |
| L2 | Postgres role read-only | Zero acesso a wallet_learning/claude_decisions |
| L3 | Tools whitelist | SEM execute_code, SEM shell, SEM browser |
| L4 | Network egress control | IPAddressAllow só domains necessários |
| L5 | Telegram allowlist | Só seu user_id responde |
| L6 | Kernel hardening | NoNewPrivileges, MemoryDenyWriteExecute |
| L7 | Install pinado | SHA256 verificado, version pinned |
| L8 | Kill switch | `/hermes_stop` no Telegram mata em 1s |
| L9 | Rollback simples | `systemctl stop` + `userdel -r` = limpa tudo, OpenClaw intocado |

**Se der MUITO bo**: 2 comandos apagam o Hermes sem afetar nada do crypto bot.

---

## 7. CUSTO REAL

| Item | Custo mensal |
|---|---|
| **LLM (kimi-k2 via nvidia-proxy)** | $0 (já paga NVIDIA NIM) |
| **Servidor** | $0 (Hetzner ou Mac já seus) |
| **Telegram API** | $0 (grátis) |
| **Sorsa** | $0 (já paga) |
| **DexScreener** | $0 (tier grátis) |
| **OpenRouter (fallback se kimi falhar)** | ~$5-10 se usar |
| **TOTAL adicional** | **$0-10/mês** |

---

## 8. TIMELINE (o que entrega quando)

### Dias 1-3 — Você já vê coisa
- Hermes rodando, bot responde no Telegram
- `/hunt 0xabc` funciona — research de token por demanda

### Dias 4-7 — Integração base
- Todas as 9 fontes emitindo pra `hunter_signals`
- Convergence engine rodando
- Primeiro alerta consolidado no @dualhermes_bot

### Dias 8-14 — Inteligência
- Narrative engine online
- Token age ranking + cross-chain search
- MEGA tier com priority routing
- Platform signals listener
- Cascade detector

### Dias 15-22 — Aprendizado
- Backtest learner completo (padrões de 30 dias)
- Pattern library popular
- Weekly postmortem
- Benchmark vs você
- Chain hotness tracker

### Depois — Evolução contínua
- Hermes autoajusta, auto-descobre KOLs, auto-gera skills
- Você pode adicionar novos grupos, accounts, via Telegram commands
- Multi-agent pro outros projetos (Mac Mini)

---

## 9. DECISÕES PENDENTES (consolidado — o que você precisa me dar)

### 🔴 CRÍTICAS (bloqueiam qualquer começo)

**1. Onde rodar: Hetzner ou Mac Mini?**
- Se Mac Mini, qual modelo + RAM? (M1/M2/M4, 8/16/32/64GB)
- Se Hetzner, autorizo criar user `hermes` isolado?

**2. Token de bot Telegram** (@BotFather no Telegram, 30s):
- `/newbot`
- Nome: "Dual Hermes Hunter"
- Me passa o token (formato `1234:ABC...`)

**3. API Telegram** (my.telegram.org/apps, 2min):
- Login com seu número
- "Create application"
- Me passa `api_id` + `api_hash`

### 🟡 IMPORTANTES (começo sem, mas melhoram muito)

**4. 3-5 grupos Telegram tier 1** iniciais (alpha groups que você confia)
- Username ou ID de cada

**5. 30-50 handles Twitter/X ALPHA** que você respeita
- Lista de @handles, um por linha

**6. MEGA accounts** — confirma começar com:
`@elonmusk, @cz_binance, @VitalikButerin, @aeyakovenko, @saylor, @brian_armstrong, @X, @binance, @coinbase, @solana, @base, @virtuals_io, @PumpDotFun, @DexScreener`?

### 🟢 DEFAULTS (posso definir sozinho se você quiser)

**7. Chains v1**: sugiro ETH + Base + Solana + BSC (4 principais). OK?

**8. News sources**: CoinDesk, Decrypt, The Block, RT. OK?

**9. Alertas/dia cap**: 8/dia (qualidade sobre quantidade). OK?

**10. Hot path alvo**: <30s. OK?

**11. Auto-tune weights**: Hermes pode ajustar até 20%/semana. OK?

**12. Backtest inicial**: 30 dias de histórico na estreia. OK?

**13. Benchmark vs você**: autorizado mensalmente. OK?

---

## 10. O QUE EU JÁ CONSTRUÍ (review)

Em `/home/ubuntu/hermes_prep/` — **nada deployado ainda**:

| Arquivo | O que é |
|---|---|
| `MASTER_FINAL.md` | **ESTE DOC** — substitui os 4 anteriores |
| `SECURITY_AUDIT.md` | Auditoria completa do install script Hermes (SAFE) |
| `install.sh` | Cópia local do instalador oficial, SHA256 pinado |
| `hermes.service` | systemd com 9 camadas de hardening |
| `hermes_config.yaml` | Config base do Hermes (kimi-k2, tools whitelist, kill switch) |
| `mcp_server.py` | MCP server com 12 tools base (V1) — será expandido pra 28 |
| `telegram_group_monitor.py` | Monitor Telethon (Fase 3) |
| `INSTALL_STEPS.md` | Passo-a-passo de instalação (11 passos) |
| `ORCHESTRATION_MASTER.md` | Design V1 (convergência) |
| `ORCHESTRATION_V2_NARRATIVE.md` | Design V2 (narrativas proativas) |
| `ORCHESTRATION_V3_INTELLIGENCE.md` | Design V3 (MEGA tier, stages, learning) |
| `ORCHESTRATION_V4_PATTERNS.md` | Design V4 (platform, cascade, backtest, speed) |

---

## 11. COMO VOCÊ EXECUTA — PASSO SIMPLES

**Você não precisa saber "como" fazer tudo. Eu faço. Você só me destrava.**

### Passo 1: Decide e me fala 3 coisas (15 min de trabalho seu)
```
a) Hetzner OU Mac Mini (se Mac: modelo+RAM)
b) Token @dualhermes_bot (do @BotFather)
c) API_ID + API_HASH (de my.telegram.org)
```

### Passo 2: Me passa lista inicial de grupos/handles (30 min de trabalho seu)
```
- 3-5 grupos Telegram (@username ou ID)
- 20-50 handles Twitter que você respeita
```

### Passo 3: Eu construo Fase 1-2 (1-2 dias meus)
```
- Instalação com segurança
- 9 fontes ingestando
- Primeiro alerta no bot em ~48h
```

### Passo 4: Você valida por 1 semana
```
- Vê alertas chegando
- Dá /feedback pros que são bons/ruins
- Ajustes rápidos
```

### Passo 5: Eu adiciono inteligência (Fases 3-6, ~2 semanas)
```
- Narrative engine
- Cross-chain cascade
- Pattern learning
- MEGA tier
- Benchmark vs você
```

### Passo 6: Sistema autoevolui
```
- Skills autogeradas
- Auto-tuning
- Auto-discovery de KOLs
- Você mantém só observando
```

---

## 12. SÍNTESE FINAL

Em 1 parágrafo:

> **Vamos construir um agente Hermes autônomo que escuta 10 fontes de sinal (existentes + novas: Telegram groups, Twitter, X oficial, Binance/Coinbase, DexScreener, news), detecta narrativas emergindo em tempo real (America Is Back, Flork memes, etc), cruza com tokens em 4 chains escolhendo o MAIS VELHO com atividade real, reage a MEGA accounts (Elon, CZ) em <15s e plataformas em <30s, investiga com 5 subagents paralelos, alerta ANTES do smart money entrar, detecta cascade de ETH pra Base/Sol, aprende diariamente dos padrões históricos, faz postmortem semanal, se compara com você mensalmente, e melhora sozinho. Rodando isolado por segurança. Custo: ~$0/mês.**

---

## 13. QUANDO VOCÊ RESPONDER

Me responde SÓ as 3 CRÍTICAS (item 9, blocos 🔴):
1. Hetzner ou Mac (+ specs)
2. Bot token
3. API Telegram

Os outros eu posso setar defaults ou preencher depois. Não precisa pensar em tudo agora.

**Com essas 3 eu começo a construir IMEDIATAMENTE.**
