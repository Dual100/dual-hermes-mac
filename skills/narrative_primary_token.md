---
name: narrative_primary_token
description: |
  Quando uma narrativa matcha MÚLTIPLOS tokens (mesmo nome em chains diferentes
  ou variações), identifica o PRIMARY (mais velho + atividade real) e alerta só
  nele. Secondary tokens só se cascade later.
trigger: narrative_match.count > 1
---

# Narrative Primary Token Selection

## Regra

Mercado converge no token MAIS VELHO com atividade real. Copycats
distribuem volume mas "original" vence na média.

## Processo

1. Pegar candidatos: all tokens matching narrative across chains
2. Pra cada candidato, coletar:
   - age_days (pair_created_at)
   - volume_24h
   - holder_count
   - liquidity_usd
   - narrative_fit (embedding similarity)
3. Filtros hard:
   - liquidity >= $5000
   - holders >= 20
   - not_honeypot
   - not_in_blacklist
4. Score:
   ```
   age_score = log10(age_days + 1) / 3       # weight: 3.0
   activity = volume_24h / 50000              # weight: 2.0
   holders = log10(holders + 1) / 4           # weight: 1.5
   fit = narrative_fit                        # weight: 2.0
   
   total = age * 3 + activity * 2 + holders * 1.5 + fit * 2
   ```
5. Return top 3 candidates:
   - TOP 1 = PRIMARY (alert now)
   - TOP 2 = SECONDARY (watch, cascade candidate)
   - TOP 3 = TERTIARY (info only)

## Exemplo real

Narrativa "America Is Back":
- AIB ETH  | 730 days | $120K vol | 3.2K holders → score 9.1 → **PRIMARY**
- AIB Base | 90 days  | $37K vol  | 562 holders  → score 6.2 → secondary  
- AMERICA Sol | 7 days | $8K vol | 120 holders → score 3.8 → skip (scam probable)

Alert emitida apenas pro AIB ETH. AIB Base fica em cascade watch.
