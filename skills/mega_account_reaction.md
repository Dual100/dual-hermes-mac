---
name: mega_account_reaction
description: |
  Qualquer tweet de MEGA tier account (Elon, CZ, @ethereum, @binance,
  @fluffycrypt) → HOT PATH investigation em <15s. Prioridade máxima.
trigger: tweet from tier=MEGA OR platform_account
---

# MEGA Account Reaction

## Contas MEGA monitoradas

### Individual MEGA KOLs
- @elonmusk (200M followers)
- @cz_binance (9M)
- @VitalikButerin (5M)
- @aeyakovenko (900K)
- @saylor (3M)
- @brian_armstrong (1M)

### Platform accounts (trata como MEGA)
- @ethereum (3M) ← official
- @X (60M) ← platform
- @binance (10M)
- @coinbase (6M)
- @solana (3M)
- @base (500K)
- @virtuals_io (200K)
- @PumpDotFun
- @DexScreener

### Crypto mega callers (100K+)
- @fluffycrypt (102K) — comprovado calls ASTEROID
- @cottonxbt (29K) — comprovado calls WOJAK
- outros descobertos via benchmark

## Hot path (<15s total)

```
T=0ms:    tweet detectado (FxTwitter polling 1s)
T=1s:     LLM extrai entities (kimi-k2): tokens, memes, features
T=2s:     Parallel dispatch:
            A: find_tokens_by_narrative (se meme/theme sem token direto)
            B: check_contract_safety (se token explícito)
            C: price_mcap_lookup
            D: cross_chain_search (mesma ticker outras chains)
T=10s:    Consolidate
T=12s:    Alert
```

## Decision logic

```python
if tweet.has_explicit_token_address:
    # Direct — investigate that exact token
    investigate(tweet.token_address, priority="critical")
    
elif tweet.has_ticker_mention:
    # Search across chains, prefer primary
    candidates = find_tokens_by_ticker(tweet.ticker)
    primary = rank_by_narrative(candidates)
    investigate(primary, priority="critical")
    
elif tweet.has_theme_keyword:
    # Emerging narrative, scan tokens
    narrative = extract_narrative(tweet)
    matching_tokens = find_tokens_by_narrative(narrative, limit=5)
    for token in matching_tokens:
        investigate(token, priority="high")
        
elif tweet.is_generic_bullish:
    # Just vibes — WATCH only
    log_as_watch(tweet)
```

## Weight boost

Source weight boost pra qualquer sinal que correlaciona com MEGA tweet
nos últimos 10 minutos:

```python
if signal.token in mega_recent_mentions:
    signal.weight *= 1.5  # amplifica outros sinais que corroboram
```

## Alert format

```
⚡ MEGA ACCOUNT SIGNAL
━━━━━━━━━━━━━━━━━━━━
@fluffycrypt (102K followers) tweetou:
"Bags with 4-5x inevitable gains: $PUNCH $TROLL $WOJAK..."

Top match: $WOJAK (age 14mo, $25.8M mcap, active narrative)
Score: 82
Chain: ETH
Entry: $25.8M may seem late but narrative just starting

[BUY] [INFO] [SKIP]
```
