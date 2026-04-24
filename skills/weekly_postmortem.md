---
name: weekly_postmortem
description: |
  Toda semana analisa hits vs misses dos alertas passados, identifica padrões
  que funcionaram vs falharam, propõe ajustes de weights. Hermes auto-tuning.
schedule: "0 3 * * 0"  # Domingo 03:00 UTC
---

# Weekly Postmortem

## Processo

1. Query hunter_alerts da última semana
2. Pra cada alerta, buscar outcome em hunter_outcomes:
   - ROI em 1h, 6h, 24h, 7d
3. Classificar:
   - HIT: ROI >= 50% em 24h
   - MISS: ROI <= -30% em 24h
   - NEUTRAL: entre os dois
4. Extrair features de cada alerta:
   - source (monitor que triggou)
   - narrative_stage (EMERGING/GROWING/PEAK)
   - catalyst_tier (MEGA/ALPHA/RISING/NONE)
   - convergence_score
   - pattern_type (trending/narrative/mega_kol/creative/shill)
   - catalyst_author_tier
   - chain
5. Análise estatística:
   - "De N hits esta semana, X% tinham narrative_stage=EMERGING"
   - "De N misses, X% tinham single LP holder"
   - "Padrão 'mega_kol' teve hit rate 73%"
6. Propor ajustes:
   - Se pattern X tem hit rate > 60% → aumentar weight 10-20%
   - Se pattern Y tem hit rate < 30% → diminuir weight 20-30%
7. Aplicar auto-ajuste (se change < 25%) OU notificar usuário (se maior)
8. Salvar relatório em Obsidian vault:
   `08-Context/weekly_postmortem_{YYYY-MM-DD}.md`

## Output template

```markdown
# Weekly Postmortem — 2026-XX-XX

## Stats
- Alertas enviados: 34
- HITs: 15 (44%)
- MISSes: 8 (24%)
- Neutral: 11 (32%)

## Top padrões
1. mega_kol_endorsement: 73% hit rate (N=8) ← melhor
2. narrative_emerging: 55% hit rate (N=12)
3. convergence_high: 50% hit rate (N=10)
4. trending_momentum: 31% hit rate (N=4) ← pior

## Ajustes propostos
- mega_kol source_weight: 5.0 → 5.5 (+10%)
- trending_momentum weight: 1.5 → 1.2 (-20%)

## Missed opportunities
- XYZ token pumped +180% em 6h, Hermes não alertou porque mentions < 10
- Ajustar threshold mentions para 8

## Lessons learned
- (gerado por LLM analisando patterns)
```

## Integration

Resultado feeds back em:
- `source_weights` config (auto-adjust)
- `pattern_patterns` table (atualiza precision_rate)
- User memory file (learnings ficam na conversa longa de Hermes com user)
