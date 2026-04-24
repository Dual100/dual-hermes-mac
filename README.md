# DualHermes Hunter — Installation Prep

Todos os arquivos pra instalar o Hermes Agent como gem-hunter autônomo no seu servidor.
**Nada aqui foi deployado.** São rascunhos pra você revisar antes de qualquer instalação.

## Arquivos

| Arquivo | Propósito |
|---|---|
| `SECURITY_AUDIT.md` | Auditoria do install script oficial do Hermes — SAFE com hardening |
| `install.sh` | Cópia local do instalador oficial (pinned SHA256 no audit) |
| `hermes.service` | Unit systemd com full hardening (ProtectSystem, MemoryLimit, etc.) |
| `hermes_config.yaml` | Config do Hermes: provider kimi-k2 local, tools whitelist, kill switch |
| `mcp_server.py` | MCP server expondo 12 tools read-only ao Hermes |
| `telegram_group_monitor.py` | Monitor Telethon de grupos alpha → Postgres |
| `INSTALL_STEPS.md` | Passo-a-passo de instalação (SÓ QUANDO VOCÊ AUTORIZAR) |

## Arquitetura final

```
Hetzner VPS
├── openclaw.service        (intocado — @dualvirtual_bot, kimi-k2)
├── nvidia-proxy.service    (intocado — :8000 tradutor NVIDIA NIM)
│
├── hermes.service          (NOVO — user hermes, isolado)
│   └→ kimi-k2 via nvidia-proxy (grátis)
│   └→ @dualhermes_bot (novo)
│   └→ chama MCP server pra tools
│
├── hermes-mcp.service      (NOVO — subprocess do hermes)
│   └→ Expõe 12 tools read-only
│
└── telegram-group-monitor.service  (NOVO)
    └→ Telethon listener (sua conta)
    └→ Postgres telegram_signals
```

## O que ainda preciso de você

1. **Token de bot Telegram** (@BotFather, 30s)
2. **API_ID + API_HASH** do Telegram (https://my.telegram.org, 2min)
3. **Especs do Mac Mini** (pra decidir Hetzner vs Mac)
4. **Lista dos grupos Telegram** pra monitorar (podemos começar com 3-5)

## Segurança — camadas aplicadas

- ✅ Usuário `hermes` isolado (sem sudo, sem acesso a `/home/ubuntu` ou `/home/openclaw`)
- ✅ systemd: `ProtectHome`, `ReadWritePaths=/home/hermes`, `MemoryLimit=2G`
- ✅ Kernel: `NoNewPrivileges`, `MemoryDenyWriteExecute`, `CapabilityBoundingSet=`
- ✅ Tools: whitelist explícito — sem `execute_code`, sem `shell`, sem `browser`
- ✅ Network egress: comentado por padrão, ativar após smoke test
- ✅ Telegram: `allow_from` no user_id do jackson apenas
- ✅ Install script: SHA256 pinado, auditado, pin a release tag (não `main`)
- ✅ MCP server: read-only nas queries (Postgres role `hermes_readonly`)
- ✅ Logs: redação de secrets, rotação 7 dias

## Próximos passos

Quando você me der GO + as 4 coisas que preciso:

1. Criar usuário `hermes` + diretórios
2. Rodar install script (pinado)
3. Copiar `mcp_server.py` pra `/home/hermes/hermes-tools/`
4. Copiar `hermes.service` pra `/etc/systemd/system/`
5. Copiar `telegram_group_monitor.py` pra `/home/hermes/`
6. Configurar Postgres `hermes_readonly` role
7. Smoke test: `/hunt 0xabc...` no Telegram deve responder
8. Ativar cron 3x/dia

**Tempo total de instalação:** ~2h com você presente pro bot token e API.
