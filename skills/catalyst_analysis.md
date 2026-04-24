---
name: catalyst_analysis
description: |
  Analisa POR QUE um token está pumpando. Identifica o catalyst específico
  (quem começou, qual narrativa, qual padrão) e classifica em 1 de 5 tipos.
  Use DURANTE investigação pra entender se pump é sustentável ou dump iminente.
activation:
  - always_run_during_investigation: true
  - trigger_on: ['price_change_24h > 50', 'mentions > 10']
---

# Catalyst Analysis Skill

## Output esperado

Pra cada token, produza:

```json
{
  "catalyst_author": "@handle",       // quem iniciou o pump
  "catalyst_author_tier": "MEGA|ALPHA|RISING|UNKNOWN",
  "catalyst_time": "2026-04-24T09:25:00Z",
  "catalyst_tweet_id": "1234567890",
  "pattern_type": "trending|narrative|mega_kol|creative|shill|organic",
  "narrative_reference": "ETH season, Matt Furie, Vitalik alien...",
  "sustainability_score": 0.0-1.0,    // vai continuar pumpando?
  "peak_estimate": "already_peaked|mid_pump|early",
  "recommended_action": "ALERT|WATCH|SKIP",
  "reasoning": "1-2 sentence explanation"
}
```

## Os 5 padrões

### 1. TRENDING momentum
- Keywords: "trending", "pumping now", "2X in 2min"
- Sinais: mentions crescendo 5x+/hora, multiple low-tier shillers
- Sustainability: BAIXA (dura horas, dump rápido)
- Decision: ALERT só se MICRO cap ($10-100K) + mentions ainda acelerando

### 2. NARRATIVE-DRIVEN
- Keywords: referências culturais (Matt Furie, ETH season, AI agents, BTC ATH)
- Sinais: KOLs médios + narrativa broad + builds slow
- Sustainability: MÉDIA-ALTA (dias-semanas se narrativa pega)
- Decision: ALERT se early stage + narrative velocity > 3×

### 3. MEGA KOL endorsement
- Keywords: conta 50K+ followers nomeando o token
- Sinais: @fluffycrypt, @elonmusk, @cz_binance, @cottonxbt (28K+ contrarian)
- Sustainability: MUITO ALTA (se KOL é respeitado)
- Decision: ALERT CRITICAL, hot path <15s

### 4. CREATIVE catalyst
- Keywords: tweet viral → meme coin → token
- Sinais: tweet original tem 10K+ likes + tokens nascendo em sequência
- Sustainability: VARIÁVEL (pump rápido, pode morrer ou virar franchise)
- Decision: ALERT se token é OLDEST match + narrative velocity > 5×

### 5. SHILL coordenado (RED FLAG)
- Keywords: 10+ contas pequenas (<500 followers) postando igual
- Sinais: contas criadas mesmo dia, textos copy-paste, zero engagement
- Sustainability: ZERO (dump certeiro)
- Decision: SKIP + mark token_blacklist

## Como classificar

Pra cada tweet que menciona o token:

1. Profile do autor (Sorsa):
   - Followers > 50K → MEGA
   - 10K-50K → ALPHA
   - 1K-10K → RISING  
   - <1K → NOISE

2. Análise linguística (LLM kimi-k2):
   - Narrative references? (Matt Furie, ETH season, etc)
   - Urgency words? ("now", "pumping", "don't miss")
   - Conviction? ("loaded bag", "long-term")
   - Shill markers? (copy-paste, "moon", excessive emojis)

3. Temporal pattern:
   - Authors únicos crescendo? (bom)
   - Mesmos 3 autores repetindo? (suspeito)
   - Timing coordenado (5 posts em 1min)? (shill)

4. Cross-reference:
   - Autor já chamou tokens hit histórico? (check user-tweets)
   - Autor está postando só esse token ou spammando? (focus = quality)

## Output final

Catalyst analysis integra ao scoring principal:

```python
if catalyst_author_tier == "MEGA" and pattern_type in ("mega_kol", "narrative"):
    score += 25  # alpha puro
if pattern_type == "shill":
    score = 0  # skip
if sustainability_score > 0.7 and peak_estimate == "early":
    score += 15
if sustainability_score < 0.3:
    score -= 15
```
