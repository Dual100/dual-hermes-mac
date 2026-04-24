---
name: cross_chain_cascade
description: |
  Quando token pumpa em uma chain, mesmo nome/ticker em OUTRAS chains
  tende a pumpar 30min-2h depois. Detecta cascade opportunity e alerta
  no copycat ANTES dele pumpar.
trigger: primary_token pumped > 50% in < 2h
---

# Cross-Chain Cascade Detection

## Padrão

ETH AIB pumpou 500% em 45min → traders notam → buscam AIB em outras chains →
AIB Base (dormente) começa a pumpar 30-90min depois → cascade.

## Processo

1. Quando primary_token (resultado de narrative_primary_token) pumpa:
   - price_change_1h > 50 AND volume_growing
2. Buscar mesmo ticker em outras chains:
   - DexScreener /search?q={ticker}
   - Filtrar chains ≠ primary_chain
   - Filtrar liquidity > $1k (evita shitcoin zero)
3. Rankear copycats:
   - age_days (older = more legit)
   - current_volume_1h (já começou a mover?)
   - holder_growth_last_1h
4. Classificar:
   - VERIFIED_CASCADE: copycat ainda flat (<10% pump) + holders começando
     → ALERT CASCADE_OPPORTUNITY
   - ALREADY_STARTED: copycat já +20-50% mas não picou → ALERT SECONDARY_PLAY
   - ALREADY_PUMPED: copycat +100% = late → IGNORE

## Histórico esperado

De `cascade_patterns` table:
- ETH→Base cascade success rate: ~68%
- ETH→Sol cascade success rate: ~52%  
- ETH→BSC cascade success rate: ~45%
- Avg delay: ETH→Base 47min, ETH→Sol 73min

## Alert format

```
🌊 CASCADE OPPORTUNITY
Primary: AIB on ETH pumped +85% (45min ago)
Copycat: AIB on Base (age 3mo, mcap $40K, +0%)
Expected delay: 30-90min (from 12 similar patterns)
Historical success: 68%

[BUY] [WATCH] [SKIP]
```
