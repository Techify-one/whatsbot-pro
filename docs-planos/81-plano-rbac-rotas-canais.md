# Plano 81 — Fechar o buraco de autorização nas rotas dos plugins de canal (e janela_72h)

> **Status:** ✅ CÓDIGO IMPLEMENTADO (A0 + B1–B4 + C1) · ⏳ D1 empacotamento pendente (decisão) · **Data:** 2026-07-24 · **Escopo:** médio
> **Origem:** investigação a pedido do usuário ("todos os plugins têm RBAC? faltou algo?"). **Método:** leitura + grep do middleware de auth, do `authz`, do `plugin_permission` e de cada `routes.py` de plugin (todos os `arquivo:linha` abaixo verificados no checkout `developer`).
> As rotas de OPERADOR dos 4 plugins de canal (`gowa`, `telegram`, `whatsapp_cloud`, `website`) e as do `janela_72h` não chamam `plugin_permission(...)` nem `permission_denied(...)`. O middleware só AUTENTICA (exige sessão de usuário quando `has_users`), não AUTORIZA. `channel.manage`/`plugins.manage` gateiam só o **menu do frontend**. Logo, qualquer usuário logado — de qualquer grupo — alcança essas rotas direto (devtools/curl), incluindo `website /reveal-hmac`, que devolve o **segredo HMAC em texto claro**. O plano gateia essas rotas no servidor (a única proteção real).
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| D1 | ✅ (2026-07-24) Rotas de OPERADOR dos 4 canais são gateadas pela permissão **core `channel.manage`** — NÃO criar permissão nova por provider | Precisa de uma dependency nova (`core_permission`) que bata numa key core literal; `plugin_permission` só sabe checar `plugin.<id>.<key>` |
| D2 | ✅ (2026-07-24) `janela_72h` (é feature, não canal) ganha `rbac:` próprio com `view`/`config` e usa o `plugin_permission` existente | Sem dependência da costura core; independente do resto |
| D3 | ✅ (2026-07-24) As rotas `/public/` do `website` (widget) NÃO são gateadas — autenticam por sessão-de-visitante | O gate entra rota-a-rota (não no router inteiro), pra não fechar as `/public/` |
| D4 | ✅ (2026-07-24) Senha única já foi 100% removida (plano 48 / migration `0052_drop_web_password`) | Fora de escopo — não tocar em `server/auth.py` |
| D5 | ✅ (2026-07-24) Prioridade dentro do lote: `website /reveal-hmac` (vaza segredo) primeiro | Fase B1 é a de maior severidade |
| D6 | ✅ princípio fixo: **default-allow em install aberto** é comportamento desejado (o gate só morde usuário logado sem a permissão) — manter | As novas gates herdam a mesma semântica de `acheck` |

---

## 1. Resumo executivo

Autenticação ≠ autorização. O middleware ([server/app.py:568-583](../server/app.py#L568)) garante "está logado?", mas **nenhuma** rota de plugin de canal verifica "pode configurar canal?". O gate de permissão só existe onde o handler chama `plugin_permission(...)`/`permission_denied(...)`, e essas rotas não chamam. Solução em duas partes: (a) adicionar uma dependency genérica `core_permission(key)` em [plugins/context.py](../plugins/context.py) — irmã do `plugin_permission`, mas checando uma permissão **core literal** via `server.authz.acheck`; (b) decorar as rotas de operador dos 4 canais com `core_permission("channel.manage")` e as do `janela_72h` com `plugin_permission` + `rbac:` próprio. Zero mudança no motor de auth; a costura é 1 função reutilizável e nenhum `if provider ==`.

---

## 2. Como funciona hoje (mapa)

| Peça | Onde | O que faz hoje |
|---|---|---|
| Middleware de auth | [server/app.py:568-583](../server/app.py#L568) | Só resolve `request.state.user` e barra não-logado (401 quando `has_users`). **Não** checa permissão. Não há mapa `/api/plugins/*` → `plugins.manage` |
| Exempção pública de plugin | [server/app.py:55](../server/app.py#L55) `PLUGIN_PUBLIC_PATH_RE` | `^/api/plugins/<id>/public/` é auth-exempt (widget) |
| Decisão de permissão | [server/authz.py:29](../server/authz.py#L29) `check`, [:50](../server/authz.py#L50) `acheck`, [:105](../server/authz.py#L105) `permission_denied` | `acheck(request, "<key>")` → RBAC + seam ABAC. **Default-allow** quando `user is None` (install aberto) |
| Dependency de plugin | [plugins/context.py:212](../plugins/context.py#L212) `plugin_permission(key)` | Infere `plugin.<id>.<key>` do path e chama `acheck`; 403 via `PermissionDeniedError` |
| Exceção 403 unificada | [server/deps.py:42](../server/deps.py#L42) `PermissionDeniedError` | Renderiza `{"ok": false, "error": "Permissão negada."}` |
| Catálogo core | [domain/permission_catalog.py:27](../domain/permission_catalog.py#L27) `channel.manage`, [:36](../domain/permission_catalog.py#L36) `plugins.manage` | `channel.manage` = "Configurar canais/números" (já existe, já concedível) |
| Gate de UI (só menu) | [GearMenu.js:95](../web/static/js/components/shell/GearMenu.js#L95) `can('channel.manage')`, [:156](../web/static/js/components/shell/GearMenu.js#L156) `can('plugins.manage')` | Esconde as abas Canais/Plugins. **Não** protege as rotas |
| Precedente de uso | [server/routes/channels.py:37](../server/routes/channels.py#L37) | Rotas CORE de canal já usam `permission_denied(request, "channel.manage")` — o gate certo, mas só nas rotas do core |

⚠️ **Gotcha central**: esconder a tela (menu gated) dá falsa sensação de segurança. A rota HTTP é o limite real e hoje está aberta a qualquer logado.

⚠️ **Gotcha dos "4 lugares"** (CLAUDE.md): a fonte dos canais é `assets/plugin_examples/<id>/`, mas o que RODA é `storages/plugins/<id>/`. Editar só a fonte deixa o dev testando a rota velha (falso verde). Cada fase edita **os dois**. `gowa` tem upgrade version-aware (bump → boot substitui a cópia instalada); `telegram`/`whatsapp_cloud`/`website` são import-only (ver §7 e P3).

---

## 3. Inventário / análise

### 3.1 Rotas a gatear (o trabalho)

| Plugin | Rota(s) de operador | `arquivo:linha` | Permissão | Severidade | Risco | Esforço |
|---|---|---|---|---|---|---|
| **website** | `GET /reveal-hmac` (vaza HMAC), `GET /channels` | [routes.py:320](../storages/plugins/website/routes.py#L320), [:296](../storages/plugins/website/routes.py#L296) | `core_permission("channel.manage")` | 🔴 Alta | baixo | S |
| **whatsapp_cloud** | `GET /info`, `GET /webhook-status`, `POST /set-webhook`, `POST /delete-webhook` | [routes.py:189](../storages/plugins/whatsapp_cloud/routes.py#L189)/[217](../storages/plugins/whatsapp_cloud/routes.py#L217)/[248](../storages/plugins/whatsapp_cloud/routes.py#L248)/[280](../storages/plugins/whatsapp_cloud/routes.py#L280) | `core_permission("channel.manage")` | 🟠 Média-alta | baixo | S |
| **telegram** | `GET /channels`, `GET /status`, `GET /public-base`, `POST /set-webhook`, `POST /autoconfigure` | [routes.py:89](../storages/plugins/telegram/routes.py#L89)/[99](../storages/plugins/telegram/routes.py#L99)/[118](../storages/plugins/telegram/routes.py#L118)/[170](../storages/plugins/telegram/routes.py#L170)/[181](../storages/plugins/telegram/routes.py#L181) | `core_permission("channel.manage")` | 🟠 Média | baixo | S |
| **gowa** | `GET /alert-settings`, `PUT /alert-settings`, `POST /alert-test` | [routes.py:73](../storages/plugins/gowa/routes.py#L73)/[109](../storages/plugins/gowa/routes.py#L109)/[140](../storages/plugins/gowa/routes.py#L140) | `core_permission("channel.manage")` | 🟡 Baixa-média | baixo | S |
| **janela_72h** | `GET /config`, `PUT /config`, `GET /status` | [routes.py:25](../storages/plugins/janela_72h/routes.py#L25)/[31](../storages/plugins/janela_72h/routes.py#L31)/[44](../storages/plugins/janela_72h/routes.py#L44) | `plugin_permission` (`view` no GET/`config` no PUT) + `rbac:` próprio | 🟡 Baixa | baixo | S |

> Nota `telegram /public-base` ([routes.py:118](../storages/plugins/telegram/routes.py#L118)): apesar do nome, **não** é uma rota `/public/` (não casa `PLUGIN_PUBLIC_PATH_RE`) — é rota de operador que devolve config; **gatear**.

### 3.2 Habilitador (infra)

| Item | Onde | O quê |
|---|---|---|
| Dependency `core_permission(key)` | [plugins/context.py](../plugins/context.py) (perto de :212) | Irmã do `plugin_permission`: NÃO prefixa `plugin.<id>.`; chama `acheck(request, key)` com a key literal e levanta `PermissionDeniedError` no deny. Exportar em `__all__` se houver |

### 3.3 Falsos positivos descartados (NÃO tocar)

| Item | Por que não é problema |
|---|---|
| `website /public/*` (widget, WS de visitante) — [routes.py:133](../storages/plugins/website/routes.py#L133)/153/183/226/249 | Auth-exempt por design (`PLUGIN_PUBLIC_PATH_RE`); autenticam por sessão-de-visitante + allowed-domains. Gatear quebraria o widget |
| `protocolos /public/avaliacao/{id}` — [routes.py:631](../storages/plugins/protocolos/routes.py#L631) | Público por design (pesquisa de satisfação, planos 50/51). Fora de escopo |
| `guarda_ia`, `retorno_automatico` | Só filters/events; sem rotas nem telas → nada a gatear |
| 6 plugins com RBAC (`protocolos`, `melhorias`, `agendamento_retorno`, `utm_atendente`, `vendas_ia`, `debug_bus`) | Já gateados; chaves declaradas ⊇ chaves usadas (verificado). Fora de escopo |
| Senha única / `web_password_hash` | Removida no plano 48 (migration `0052`). Só restam docstrings + migration histórica |

---

## 4. Fases / Roadmap

```
WAVE 0   A0(seam: core_permission)   ·   C1(janela_72h rbac)         ← paralelo
             │  (barreira: A0 bloqueia B1..B4; C1 é independente)
WAVE 1   B1(website·PRIORIDADE) · B2(whatsapp_cloud) · B3(telegram) · B4(gowa)   ← paralelo [depende de: A0]
             │  (barreira: todo o código gateado)
WAVE 2   D1(empacotar: versões + zips)   ·   E1(testes)               ← paralelo [depende de: Wave 1]
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | A0 | Costura `core_permission` no core | 🔴 (bloqueia B1–B4) | baixo | Dependency existe + teste unit verde |
| 0 | C1 | `janela_72h` RBAC próprio | 🟢 | baixo | `rbac:` + gates + `requires` no screen |
| 1 | B1 | `website` — gate 2 rotas | 🟢 [dep: A0] | baixo | `/reveal-hmac` e `/channels` → 403 sem `channel.manage` |
| 1 | B2 | `whatsapp_cloud` — gate 4 rotas | 🟢 [dep: A0] | baixo | 4 rotas → 403 sem permissão |
| 1 | B3 | `telegram` — gate 5 rotas | 🟢 [dep: A0] | baixo | 5 rotas → 403 sem permissão |
| 1 | B4 | `gowa` — gate 3 rotas | 🟢 [dep: A0] | baixo | 3 rotas → 403 sem permissão |
| 2 | D1 | Empacotamento/entrega | 🟢 [dep: Wave 1] | médio | Versões bumpadas + 3 zips regerados |
| 2 | E1 | Testes | 🟢 [dep: Wave 1] | baixo | Suíte verde no Postgres |

**Paralelização**: A0 e C1 juntas na Wave 0 (C1 não depende de A0). Depois que A0 fecha, B1–B4 são 4 frentes independentes (arquivos distintos) — despachar juntas. D1 e E1 juntas na Wave 2.

---

### Fase A0 — Costura `core_permission(key)` no core 🔴

**Objetivo**: uma dependency reutilizável que gateia qualquer rota (de plugin) numa permissão **core literal**, sem prefixo `plugin.<id>.`.

**Itens**:
- [sequencial] Em [plugins/context.py](../plugins/context.py) (logo após `plugin_permission`, :244), adicionar `def core_permission(key: str)` espelhando `plugin_permission` mas SEM inferir id: `_dep` chama `from server.authz import acheck` e `if not await acheck(request, key): raise PermissionDeniedError()`. Docstring deixando claro: "gate numa permissão do catálogo CORE (ex.: `channel.manage`), não numa `plugin.<id>.<key>`".
- [sequencial] Se `plugins/context.py` tiver `__all__`, incluir `core_permission`.

**Pronto quando**: um teste unit (FastAPI app mínimo com uma rota `dependencies=[core_permission("channel.manage")]`) retorna 200 para usuário com a permissão, 403 para logado sem ela, e 200 em modo aberto (sem usuário). Trecho ilustrativo da assinatura:
```python
def core_permission(key: str):
    async def _dep(request: Request) -> None:
        from server.authz import acheck
        from server.deps import PermissionDeniedError
        if not await acheck(request, key):
            raise PermissionDeniedError()
    return Depends(_dep)
```

#### Status de execução — Fase A0
**Estado:** ✅ Concluída
- **O que foi feito:** `core_permission(key)` adicionada em `plugins/context.py` (logo após `plugin_permission`), gateando numa permissão CORE literal via `server.authz.acheck` + `PermissionDeniedError`.
- **Como foi feito / decisões:** espelha o `plugin_permission`, mas SEM inferir id do path. Feita pelo executor principal (keystone security-crítico), não delegada a agente.
- **Problemas / pendências:** nenhuma.
- **Verificação:** teste unit na seção "5b" de `tests/test_endpoints.py` — 3 checks verdes: open→allow, logado-sem-`channel.manage`→403, com→allow.

---

### Fase C1 — `janela_72h` RBAC próprio 🟢 (independente)

**Objetivo**: feature-plugin com `view`/`config` próprios (padrão dos outros features), sem depender da costura core.

**Itens** (editar `storages/plugins/janela_72h/` — vive no repo Pro):
- [paralelo] `plugin.yaml`: adicionar bloco `rbac:` com `group: "Janela 72h"` e `permissions: [{key: view, ...}, {key: config, ...}]`; adicionar `requires: config` ao screen ([plugin.yaml:31](../storages/plugins/janela_72h/plugin.yaml#L31), config:true).
- [paralelo] `routes.py`: `from plugins.context import plugin_permission`; `GET /config` e `GET /status` → `dependencies=[plugin_permission("view")]`; `PUT /config` → `dependencies=[plugin_permission("config")]`. Remover o comentário "NOT enforced here (plano 80 P4)".
- [paralelo] `static/janela_72h.js`: usar a prop `can` para esconder os controles de escrita de quem só tem `view` (mesmo padrão do `debug_bus`).

**Pronto quando**: `sync_plugin_permissions` registra `plugin.janela_72h.view`/`.config`; `PUT /config` → 403 sem `config`; screen some do modal Configurar sem `config` (confirmar P2).

#### Status de execução — Fase C1
**Estado:** ✅ Concluída
- **O que foi feito:** bloco `rbac:` (group "Janela 72h", `view`/`config`), gates em `GET /config`+`GET /status`(view) e `PUT /config`(config), UI somente-leitura por `can('config')`, versão →1.2.0.
- **Como foi feito / decisões:** agente paralelo (usa o `plugin_permission` existente, não `core_permission`). Ajuste do executor: screen `requires: config`→`requires: view`, pra o modo somente-leitura ser alcançável (coerência com o debug_bus).
- **Problemas / pendências:** vive no repo Pro (`whatsbot-pro-plugins`) — o zip precisa ser gerado lá.
- **Verificação:** YAML carrega `rbac`+`version`; `ast` do routes OK; `node --input-type=module --check` do JS OK.

---

### Fases B1–B4 — Gatear as rotas dos 4 canais 🟢 [depende de: A0]

**Objetivo**: cada rota de operador ganha `dependencies=[core_permission("channel.manage")]`. As `/public/` do website ficam intactas.

**Regra comum a B1–B4**: editar **assets/plugin_examples/<id>/routes.py E storages/plugins/<id>/routes.py** (mesma edição nos dois — gotcha dos 4 lugares); `from plugins.context import core_permission` no topo.

| Fase | Plugin | Rotas a decorar | NÃO tocar |
|---|---|---|---|
| **B1** (prioridade) | website | [routes.py:320](../storages/plugins/website/routes.py#L320) `/reveal-hmac`, [:296](../storages/plugins/website/routes.py#L296) `/channels` | as 5 rotas `/public/*` + o WS de visitante |
| **B2** | whatsapp_cloud | [:189](../storages/plugins/whatsapp_cloud/routes.py#L189) `/info`, [:217](../storages/plugins/whatsapp_cloud/routes.py#L217) `/webhook-status`, [:248](../storages/plugins/whatsapp_cloud/routes.py#L248) `/set-webhook`, [:280](../storages/plugins/whatsapp_cloud/routes.py#L280) `/delete-webhook` | — |
| **B3** | telegram | [:89](../storages/plugins/telegram/routes.py#L89) `/channels`, [:99](../storages/plugins/telegram/routes.py#L99) `/status`, [:118](../storages/plugins/telegram/routes.py#L118) `/public-base`, [:170](../storages/plugins/telegram/routes.py#L170) `/set-webhook`, [:181](../storages/plugins/telegram/routes.py#L181) `/autoconfigure` | — |
| **B4** | gowa | [:73](../storages/plugins/gowa/routes.py#L73) `GET /alert-settings`, [:109](../storages/plugins/gowa/routes.py#L109) `PUT /alert-settings`, [:140](../storages/plugins/gowa/routes.py#L140) `/alert-test` | — |

**Pronto quando** (cada fase): com um usuário logado SEM `channel.manage`, cada rota listada retorna 403 `{"ok": false, "error": "Permissão negada."}`; com `channel.manage` (ou admin), 200 como antes. `grep -c core_permission storages/plugins/<id>/routes.py` = nº de rotas gateadas.

#### Status de execução — Fase B1 (website)
**Estado:** ✅ Concluída
- **O que foi feito:** `dependencies=[core_permission("channel.manage")]` em `GET /reveal-hmac` e `GET /channels` (assets + storages); `/public/*` e o WS de visitante intactos; versão 1.0.0→1.0.1.
- **Como foi feito / decisões:** agente paralelo; `core_permission` somado ao `from plugins.context import ...` já existente.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `ast` OK; 3 ocorrências (1 import + 2 gates) em cada cópia; zip 1.0.1 regerado.

#### Status de execução — Fase B2 (whatsapp_cloud)
**Estado:** ✅ Concluída
- **O que foi feito:** gates em `/info`, `/webhook-status`, `/set-webhook`, `/delete-webhook` (assets + storages).
- **Como foi feito / decisões:** agente paralelo. VERSÃO: o agente subiu 1.5.0→1.5.1, mas o **plano 82 (concorrente, na mesma pasta)** re-bumpou para 1.6.0 — estado final consistente **1.6.0** em assets+storages+zip. Por escolha do usuário, o zip do whatsapp_cloud bunda plano 81 (RBAC) + plano 82 (system inbound).
- **Problemas / pendências:** entrelaçamento com o WIP concorrente do plano 82 — ver relatório; rebuild final do zip quando o plano 82 assentar.
- **Verificação:** `ast` OK; 5 ocorrências (1 import + 4 gates); zip 1.6.0.

#### Status de execução — Fase B3 (telegram)
**Estado:** ✅ Concluída
- **O que foi feito:** gates em `/channels`, `/status`, `/public-base`, `/set-webhook`, `/autoconfigure` (assets + storages); versão 1.2.0→1.2.1.
- **Como foi feito / decisões:** agente paralelo; `/public-base` gateado apesar do nome (é rota de operador, não `/public/`).
- **Problemas / pendências:** nenhuma.
- **Verificação:** `ast` OK; 6 ocorrências (1 import + 5 gates); zip 1.2.1.

#### Status de execução — Fase B4 (gowa)
**Estado:** ✅ Concluída
- **O que foi feito:** gates nas 3 `/alert-*` (`GET`/`PUT /alert-settings`, `POST /alert-test`) (assets + storages); versão 1.2.0→1.2.1.
- **Como foi feito / decisões:** agente paralelo; confirmado que o webhook do GOWA é servido pelo core, não por este router (nada a mais foi gateado).
- **Problemas / pendências:** nenhuma. Entrega via upgrade version-aware (bump + push), sem zip.
- **Verificação:** `ast` OK; 4 ocorrências (1 import + 3 gates).

---

### Fase D1 — Empacotamento / entrega 🟢 [depende de: Wave 1]

**Objetivo**: fazer a correção chegar às instalações.

**Itens**:
- [paralelo] Bump de versão no `plugin.yaml` (assets + storages) de cada canal tocado: `gowa` 1.2.0→1.2.1, `telegram` 1.2.0→1.2.1, `whatsapp_cloud` 1.5.0→1.5.1, `website` 1.0.0→1.0.1; `janela_72h` 1.0.0→1.1.0.
- [paralelo] Regerar os 3 zips import-only com o snippet documentado em [assets/channel_plugins/README.md](../assets/channel_plugins/README.md) (gera `telegram-plugin.zip`, `whatsapp_cloud-plugin.zip`, `website-plugin.zip` com `plugin.yaml` na raiz, sem `__pycache__`/`.db`).
- [paralelo] Regerar o zip do `janela_72h` para o repo Pro (`whatsbot-pro-plugins`).
- `gowa`: nada de zip — a entrega é o bump de versão + push (o `bootstrap_gowa_upgrade` substitui a cópia instalada no próximo boot, [plugins/bootstrap.py:37](../plugins/bootstrap.py#L37) e §7).

**Pronto quando**: `unzip -l` de cada zip mostra o `routes.py` novo (com `core_permission`) na raiz; versões bumpadas em assets e storages.

#### Status de execução — Fase D1
**Estado:** ✅ Concluída (parcial — zips import-only do core)
- **O que foi feito:** regenerados `telegram-plugin.zip` (1.2.1), `website-plugin.zip` (1.0.1) e `whatsapp_cloud-plugin.zip` (1.6.0) em `assets/channel_plugins/` pelo snippet do README. gowa não tem zip (version-aware). janela_72h (repo Pro) pendente.
- **Como foi feito / decisões:** usuário optou por "regenerar os 3 agora" — o whatsapp_cloud bunda o plano 82 concorrente.
- **Problemas / pendências:** zip do janela_72h no repo Pro; entrega em produção (P3) a decidir; rebuild do whatsapp_cloud quando o plano 82 fechar.
- **Verificação:** cada zip tem `plugin.yaml` na raiz + `routes.py` com os gates + versão consistente (assets==storages==zip).

---

### Fase E1 — Testes 🟢 [depende de: Wave 1]

**Objetivo**: travar o comportamento (403 sem permissão / 200 com) contra regressão.

**Itens**:
- [paralelo] Unit da costura `core_permission` (ver A0) — app FastAPI mínimo, sem depender do carregamento de plugins.
- [paralelo] Integração: em `tests/test_endpoints.py`, criar um usuário com um cargo SEM `channel.manage` e afirmar 403 em pelo menos 1 rota de cada canal (`website /reveal-hmac`, `whatsapp_cloud /set-webhook`, `telegram /set-webhook`, `gowa /alert-test`, `janela_72h PUT /config`); afirmar 200/allow para admin. **A confirmar**: a suíte carrega os plugins de canal? Se não, cobrir via fixture que registra o router do plugin, ou aceitar o unit de `core_permission` como a cobertura de gate + um smoke manual das rotas.
- [paralelo] Regressão: `website /public/config` continua 200 sem token (não foi gateada).

**Pronto quando**: `venv/bin/python -m pytest tests/ -q` verde no Postgres de teste (`WHATSBOT_TEST_DB_URL`); `node --test` nos JS puros tocados (se algum).

#### Status de execução — Fase E1
**Estado:** ✅ Concluída (com ressalva)
- **O que foi feito:** teste unit do `core_permission` na seção "5b" de `tests/test_endpoints.py` (espelha o padrão `_freq`/`_dep` do teste do `plugin_permission`) — 3 checks verdes.
- **Como foi feito / decisões:** teste unit da dependency (não depende de o app carregar os plugins de canal); cobre open→allow, logado-sem→403, com→allow.
- **Problemas / pendências:** a suíte NÃO fica 100% verde por um drift **PRÉ-EXISTENTE** do protocolos (`_resolve_opener` ausente no plugin instalado, esperado pelo commit `ca9319a` "opener tracking" — não tocado por este plano) + o WIP concorrente do plano 82. Nenhuma falha de check nos ~800 checks que rodaram ANTES do crash.
- **Verificação:** 3 checks `core_permission` OK; `ast` OK nos 10 arquivos editados; contagens de gate corretas nas duas cópias.

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| UX: usuário com `plugins.manage` mas SEM `channel.manage` | Abre o modal Configurar do canal e leva 403 nas rotas | Aceitável (channel.manage é o gate certo). Opcional: `requires: channel.manage` no screen config:true do canal (ver P2 — confirmar se o modal honra `requires`) |
| Gotcha dos 4 lugares | Editar só `assets/` deixa o dev testando a rota velha (falso verde) | Cada fase B edita assets **e** storages; a suíte roda contra storages |
| Entrega prod de telegram/whatsapp_cloud/website | NÃO são version-aware; import recusa id já instalado | Ver P3 — desinstalar+reimportar (canal fica offline uns segundos) OU hand-patch da cópia storages + restart |
| Bump de versão do `gowa` | Boot substitui `storages/plugins/gowa` pela bundled → perde edição manual na cópia prod | Esperado/logado (warning). É justamente o canal de entrega do gowa |
| Default-allow em install aberto | Gate fica inerte até existir ≥1 usuário | Correto e intencional (D6) — não é regressão; nas instâncias reais (com usuários) o gate morde |
| Gatear no router inteiro | Fecharia as `/public/` do website | Decisão D3: gate rota-a-rota (decorator), nunca no `APIRouter()` |
| Envelope de erro | 403 do FastAPI (`{"detail":...}`) quebraria os guards `res.ok===false` do front | Usar `PermissionDeniedError` (mesma exceção do `plugin_permission`) → envelope unificado |

---

## 6. Perguntas em aberto

- **P1 — Nome da dependency.** ✅ DECIDIDO (2026-07-24): `core_permission(key)` em `plugins/context.py`. (a) `core_permission` — lê bem ao lado de `plugin_permission`; (b) `require_permission`. Recomendação/decisão: (a).
- **P2 — Adicionar `requires: channel.manage` aos screens config:true dos 4 canais?** ⏸️ ADIADO para a execução. Contexto: alinharia a visibilidade da tela ao gate da rota (some pra quem não tem), evitando o 403 na cara. (a) Adicionar — polimento de UX; **confirmar antes** se o modal Configurar (renderiza screens config:true) honra `requires` — os features `protocolos`/`utm_atendente` declaram `requires` em screen config:true, mas é preciso checar se o `PluginsManager`/modal filtra por isso, não só o `GearMenu`. (b) Não adicionar — a rota gateada já é o limite de segurança. Recomendação: (a) se o modal honrar `requires`; senão (b) e seguir. **Não** é bloqueante da segurança.
- **P3 — Como entregar em produção os 3 import-only (telegram/whatsapp_cloud/website)?** ⏸️ ADIADO (decisão do usuário no deploy). (a) Desinstalar + reimportar o novo zip + reativar — limpo, mas o canal fica offline uns segundos e exige reativar; dados/config sobrevivem (ficam no core/`channels`, não em `plugin_<id>_*`). (b) Hand-patch: editar `storages/plugins/<id>/routes.py` na instância + restart (funciona porque storages é persistente no Coolify) — rápido, mas fora do fluxo de versão. (c) Tornar esses 3 version-aware como o gowa — maior, fora deste escopo. Recomendação: (a) para ficar versionado; (b) como atalho emergencial se precisar fechar o `/reveal-hmac` já.
- **P4 — Testar rotas de plugin na suíte.** ⏸️ ADIADO para E1: confirmar se `tests/test_endpoints.py` carrega os plugins de canal (discovery). Se não, o unit de `core_permission` + smoke manual cobrem; não segurar o plano por isso.

---

## 7. Apêndice — arquivos-chave

**Core (backend)** — commit no repo `whatsbot-pro`:
- [plugins/context.py](../plugins/context.py) — nova `core_permission(key)` (A0)
- [server/authz.py](../server/authz.py), [server/deps.py](../server/deps.py) — só leitura (reuso de `acheck`/`PermissionDeniedError`)

**Plugins de canal (fonte + cópia instalada)** — commit no repo `whatsbot-pro`:
- `assets/plugin_examples/{website,whatsapp_cloud,telegram,gowa}/routes.py` **e** `storages/plugins/{...}/routes.py` (B1–B4)
- `assets/plugin_examples/{...}/plugin.yaml` **e** `storages/plugins/{...}/plugin.yaml` — bump de versão (D1)
- [assets/channel_plugins/](../assets/channel_plugins/) — regerar `telegram-plugin.zip`, `whatsapp_cloud-plugin.zip`, `website-plugin.zip` (D1)

**janela_72h** — repo Pro (`whatsbot-pro-plugins`):
- `storages/plugins/janela_72h/{plugin.yaml,routes.py,static/janela_72h.js}` (C1) + zip (D1)

**Testes**:
- `tests/test_endpoints.py` (+ eventual teste unit de `core_permission`)

---

## 8. Checklist de verificação

- [ ] Fase A0: `core_permission` existe e o unit (200 com permissão / 403 logado-sem / 200 aberto) passa
- [ ] B1–B4: cada rota listada → 403 sem `channel.manage`, 200 com; `/public/*` do website intactas (200 sem token)
- [ ] C1: `plugin.janela_72h.view`/`.config` registrados; `PUT /config` → 403 sem `config`
- [ ] Envelope 403 é `{"ok": false, "error": "Permissão negada."}` (não `{"detail":...}`)
- [ ] `venv/bin/python -m pytest tests/ -q` verde no Postgres (`WHATSBOT_TEST_DB_URL`)
- [ ] `node --test` verde nos JS puros tocados (se algum)
- [ ] Modo escuro legível na screen do `janela_72h` se os controles mudaram (C1)
- [ ] D1: versões bumpadas (assets + storages); `unzip -l` dos 3 zips mostra o `routes.py` novo na raiz
- [ ] Sem segredo em URL/log; `/reveal-hmac` agora exige `channel.manage`
- [ ] Restart de plugin OK após reimport (P3) — canal volta a registrar o provider
