# Arquitetura Final — Mac Hermes + Hetzner Read-Only API

## Visão geral

```
┌─────────────────────────────────────────────────────────┐
│  MAC MINI (seu, em casa)                                 │
│                                                          │
│  ▶ Hermes Agent + MCP server                             │
│  ▶ Postgres LOCAL (dados próprios do Hermes)             │
│  ▶ Monitores outbound:                                   │
│     • Telethon (grupos Telegram)                         │
│     • FxTwitter polling (275 contas)                     │
│     • Polymarket WSS                                     │
│     • Kalshi WSS                                         │
│     • Blockchain WSS (ETH/Base/Sol/BSC)                  │
│     • DexScreener, GoPlus, Sorsa (REST on-demand)        │
│  ▶ LLM: kimi-k2 local via Ollama OU OpenRouter           │
│                                                          │
│  Nenhuma porta aberta. TUDO outbound.                    │
└──────┬──────────────────────────────────────────────────┘
       │
       │  HTTPS (bearer token auth)
       │  GET only — nunca POST/PUT
       │
       ▼
┌─────────────────────────────────────────────────────────┐
│  HETZNER (crypto bot rodando intocado)                   │
│                                                          │
│  ▶ openclaw.service (intocado)                           │
│  ▶ creator-bid-bot (intocado)                            │
│  ▶ Postgres interno (intocado)                           │
│                                                          │
│  ✨ NOVO: hermes-data-api.service                        │
│     • Porta interna 8091                                 │
│     • Exposto via nginx /hermes/*                        │
│     • Bearer token auth                                  │
│     • Rate limit 10r/s, 500r/min                         │
│     • READ-ONLY — só endpoints GET                       │
│     • Isolado via .env.hermes_api                        │
│                                                          │
│  Mac pode consultar:                                     │
│   • smart-money data (42 KOLs)                           │
│   • creator registry (farmer flags)                      │
│   • Virtuals DB (527K tokens)                            │
│   • pump patterns, chain hotness                         │
│                                                          │
│  Mac NÃO pode:                                           │
│   ❌ Executar trades                                      │
│   ❌ Alterar config                                       │
│   ❌ Ler .env principal                                  │
│   ❌ Ver wallet_learning, claude_decisions                │
└─────────────────────────────────────────────────────────┘

Também:
┌─────────────────────┐
│ Telegram API        │ ← Mac envia alertas pra você
└─────────────────────┘
```

## Fluxo de dados

### Mac iniciando
1. Mac instala Hermes + dependências
2. Mac copia keys READ-ONLY do Hetzner (via SCP manual, 1 vez): Sorsa, Moralis, Alchemy, etc.
3. Mac gera seu próprio `HERMES_API_KEY` e envia pro Hetzner setup
4. Mac conecta em @dualhermes_bot e testa comunicação

### Hetzner iniciando
1. Adiciona `hermes_data_api.py` ao creator-bid-bot
2. Cria `.env.hermes_api` com apenas `HERMES_API_KEY`
3. Cria `hermes-data-api.service` (systemd) — 512MB cap
4. Adiciona bloco nginx `/hermes/*` → porta 8091
5. Reload nginx, start service

### Runtime

```
Evento chega no grupo Telegram:
  "@caller postou 0xabc..."

Mac (Telethon):
  1. Detecta em <1s
  2. Extrai endereço 0xabc
  3. INSERT hunter_signals (LOCAL, Postgres Mac)

Mac (convergence engine):
  4. Score baixo ainda (1 fonte)
  5. Mas telegram tier 1 + não shill → dispara Hermes

Mac (Hermes Agent + 5 subagents paralelos):
  6. Subagent A: check_contract_safety(0xabc) → GoPlus REST direto
  7. Subagent B: lookup_twitter_by_wallet → consulta Hetzner API:
       GET https://dualzero.duckdns.org/hermes/creators/by-wallet?wallet=0xabc
       Bearer HERMES_API_KEY
     → Hetzner responde com creator data em ~100ms
  8. Subagent C: get_creator_history → consulta Hetzner API
  9. Subagent D: is_smart_money_wallet para top 20 buyers:
       GET /hermes/smart-money/is-smart?wallet=...
  10. Subagent E: narrative + pattern lookup (Mac Postgres local + Hetzner patterns)

Mac (main agent):
  11. Consolida resultados em ~15s
  12. Decide ALERT (score 78)
  13. POST https://api.telegram.org/bot.../sendMessage
      → Chega no seu celular

Você clica "BUY":
  14. Telegram webhook recebido no Mac
  15. Mac registra intenção, agenda exit alert (2h)
  16. Outcome tracking começa (1h/6h/24h/7d)
```

**Tempo total: <20s do Telegram group → alerta no seu celular.**

## Segurança

### Por que é mais seguro que Postgres exposto

1. **HTTPS sempre** (cert Let's Encrypt já existe em dualzero.duckdns.org)
2. **Auth com bearer token** (não senha) — Mac gera token aleatório 32 bytes
3. **Rate limit** no nginx + no slowapi (duas camadas)
4. **CORS bloqueado** — só server-to-server, não browser
5. **Endpoint surface tiny** — ~15 GET endpoints específicos, não Postgres inteiro
6. **Queries parametrizadas** — zero chance de SQL injection
7. **Sem credentials do Postgres no Mac** — Mac só sabe do bearer token
8. **Lazy imports** — API não carrega módulos de trading nem acessa wallets
9. **Isolated .env** — `.env.hermes_api` tem só HERMES_API_KEY, nada mais
10. **Logs separados** — audit trail completo de cada request

### Se token vazar
- Atacante pode **ler** dados (smart money, creators) — INFORMAÇÃO PÚBLICA na maioria dos casos
- Atacante NÃO pode escrever, não pode executar trades, não pode acessar wallets
- Rotate token: edita `.env.hermes_api`, restart service, atualiza Mac `.env.hermes`

### Se Mac for comprometido
- Atacante tem o token, mesma situação acima
- Pode pedir refresh de token via sua linha direta (eu rotaciono tudo)
- Crypto bot continua intacto

## Custo adicional

- Hetzner: +1 processo 512MB RAM, ~0.1% CPU. Impacto zero.
- Rede: <50KB por request, <100 requests/minuto típico. Impacto zero.
- Mac: HTTPS outbound normal, consumo desprezível.

**Custo $$ adicional: $0/mês.**

## Setup checklist

### No Hetzner (eu faço se você autorizar):
- [ ] Copiar `hermes_data_api.py` pra `/home/ubuntu/creator-bid-bot/`
- [ ] Criar `.env.hermes_api` com `HERMES_API_KEY` gerado aleatoriamente
- [ ] Criar `/etc/systemd/system/hermes-data-api.service`
- [ ] Adicionar bloco nginx em `/etc/nginx/sites-available/dualzero`
- [ ] `sudo systemctl daemon-reload && sudo systemctl enable --now hermes-data-api`
- [ ] `sudo nginx -t && sudo systemctl reload nginx`
- [ ] Testar: `curl -H "Authorization: Bearer <key>" https://dualzero.duckdns.org/hermes/health`

### No Mac (você faz, te guio):
- [ ] Instalar Homebrew (se não tiver)
- [ ] `brew install python@3.12 postgresql@16 ollama`
- [ ] `brew services start postgresql@16`
- [ ] Clonar repo hermes-mac (novo, só as coisas do Mac)
- [ ] `pip install -r requirements.txt`
- [ ] Criar `.env.hermes` com as keys (te passo exatamente quais)
- [ ] `createdb hermes && psql hermes < schema.sql`
- [ ] Criar @dualhermes_bot via @BotFather, token em `.env.hermes`
- [ ] Rodar `python3 mcp_server.py` → teste `/hunt 0xabc` no Telegram

**Total setup: 1-2 horas.**

## Quando e como começar

Me responda:

**1. Confirma essa arquitetura?** (Mac isolado + API read-only no Hetzner)

**2. Autoriza eu:**
   - a) Deployar `hermes_data_api.py` no Hetzner (sem afetar nada existente)
   - b) Editar nginx config pra expor `/hermes/*`
   - c) Criar systemd service `hermes-data-api`

Se sim, faço isso enquanto você abre o bot no BotFather.

**3. Prefere setup no Mac:**
   - a) Eu escrevo um `install_mac.sh` que faz tudo automático
   - b) Você segue passo-a-passo manual (mais controle)
