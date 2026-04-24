---
name: benchmark_vs_user
description: |
  Todo mês compara os trades que VOCÊ fez vs os alertas que Hermes emitiu.
  Mede se Hermes está ficando melhor/pior que você. Identifica gaps.
schedule: "0 4 1 * *"  # Primeiro dia do mês, 04:00 UTC
---

# Benchmark vs User

## Fontes de dados

- **Seus trades**: wallet_learning.db (via Hermes Data API)
- **Alertas Hermes**: hunter_alerts (Mac Postgres local)
- **Outcomes**: hunter_outcomes (Mac) + alert_outcomes.db (Hetzner)

## Análise

1. Lista trades do mês:
   - Tokens comprados
   - Entry price / exit price
   - ROI final
2. Lista alertas Hermes do mês
3. Join por token_address, cria 4 categorias:
   - **Overlap HIT**: ambos (você + Hermes) acertaram → concordância boa
   - **Overlap MISS**: ambos erraram → revisar critério
   - **User-only HIT**: você acertou, Hermes não alertou → GAP detecção
   - **User-only MISS**: você errou, Hermes tinha alertado SKIP? → Hermes > você aqui
   - **Hermes-only HIT**: Hermes acertou, você perdeu → você perdeu alpha
   - **Hermes-only MISS**: Hermes alertou, você ignorou → Hermes errou
4. Calcula:
   - Seu hit rate
   - Hit rate do Hermes
   - Gain médio ROI
   - Perda evitada por Hermes

## Output

```markdown
# Benchmark — Abril 2026

## Stats
- Você: 42 trades, 18 hits (43%), ROI médio +34%
- Hermes: 28 alertas, 14 hits (50%), ROI médio +58%

## Overlap
- Ambos acertaram: 11 tokens (happy path)
- Ambos erraram: 4 tokens (edge cases)
- User-only hits: 7 (Hermes não pegou — melhorar detection)
- User-only misses: 20 (skip seria melhor — Hermes evitou certo)
- Hermes-only hits: 3 (você perdeu alpha ali)

## Gaps detectados
Seus hits que Hermes perdeu:
  - $XYZ +180% — Hermes não pegou porque mentions < 10 threshold
  - $ABC +120% — Hermes ignorou Telegram tier 2 mention

Recommendation: ajustar thresholds mentions (8) e Telegram tier 2 weight (+20%)

## Learnings
- Você tem bias a entrar late (62% dos seus entries eram já +50%)
- Hermes tende a segurar melhor (avg exit timing +23% melhor)
- Overlap em tokens narrativos: Hermes achou catalyst 38min antes (média)

## Ações
- Hermes vai ajustar thresholds automaticamente (proposed change < 25%)
- Recomendação pro usuário: aguardar alertas Hermes em vez de FOMO groups

```

## Integration

- Salva em Obsidian vault `08-Context/benchmark_{month}.md`
- Usado no prompt system do Hermes pra contextualizar recomendações
- Atualiza user memory com padrões
