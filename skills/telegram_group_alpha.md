---
name: telegram_group_alpha
description: |
  Quando grupo Telegram tier 1 menciona token + LLM triage não marca shill,
  DISPARA investigação completa mesmo com 1 fonte só. Regra especial.
trigger: telegram_signal from group tier=1 AND is_shill=0
---

# Telegram Group Alpha — dispara investigação imediata

## Contexto

Grupos tier 1 = alpha que você curou manualmente. Quando alguém lá posta
um token, é sinal de QUALIDADE (não ruído genérico). Tratar com prioridade.

## Processo

1. Telegram group monitor detecta mensagem mencionando 0x... ou $TICKER
2. LLM triage imediato (kimi-k2, <1s):
   - sentiment: bullish/neutral/bearish
   - urgency_score: 0-1
   - is_shill: 0/1 (red flag)
3. Se group.tier == 1 AND is_shill == 0:
   → Dispara investigação mesmo sem convergência
4. Investigação completa (5 subagents paralelo):
   - A: contract safety (GoPlus)
   - B: creator + history
   - C: X mentions + catalyst analysis
   - D: smart money + holders
   - E: pattern match
5. Consolida → decisão

## Priority queue

```
Prioridade base: 2 (HIGH)
Se sender é ALPHA tier: priority 1 (CRITICAL)
Se mensagem contém urgência ("now", "ape", "x10"): +1 priority
Se narrative match detectado em paralelo: +1 priority
```

## Rate limit proteção

- Max 3 investigações por mensagem (se menciona múltiplos tokens)
- Cooldown 5min por mesmo (group + token) pra evitar spam
- Se grupo tier 1 fala do mesmo token 3x em 15min → ups priority

## Alert output

Inclui contexto do grupo:
```
🔔 ALERT from @ethvolumespike Telegram
Token: $AIB (0xb3a0f70c...)
MCap: $140K | +629% 24h
Sender: @alpha_caller (group admin)
Catalyst: @degenApe22 há 47min (10.7K followers, score 384)

Score: 78
Narrative: america_is_back (velocity 4.2x)
[BUY] [RESEARCH] [SKIP]
```
