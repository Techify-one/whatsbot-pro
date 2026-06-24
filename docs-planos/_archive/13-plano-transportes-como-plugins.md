# Plano de Implementação — 13: Transportes como Plugins (núcleo agnóstico de canal)

> **Plano de execução autocontido.** Outra IA deve conseguir executar lendo SÓ este
> arquivo + o repositório. Conclui o que os planos [`02-canais-e-providers`](02-plano-canais-e-providers.md)
> (Fase 3) e [`09-fundacao-runtime`](09-plano-fundacao-runtime.md) deixaram pendente, em cima do
> runtime multi-canal já entregue pelo [`11-multicanal-runtime`](11-plano-multicanal-runtime.md).
>
> **Objetivo de produto (palavras do Thiago):**
> 1. O **WhatsApp oficial (Cloud) já é plugin**. O **GOWA** e o **Telegram** também devem virar plugins
>    e aparecer na tela **Plugins** (`/plugins`), ao lado do Cloud.
> 2. **Desinstalar um plugin de canal faz suas funcionalidades sumirem** do sistema (subprocesso,
>    conexão, envio, QR) — e o sistema continua funcionando com as caixas de entrada restantes.
> 3. O **núcleo não depende de nenhuma caixa de entrada**: precisa subir e operar com **zero** canais
>    (ex.: instalação só-Cloud, ou só-Telegram, ou nenhuma).
> 4. **Sistema multi-canal dinâmico:** se um terceiro desenvolver uma caixa de entrada compatível, basta
>    instalar o plugin e ela funciona — **sem tocar no core**.
>
> **Decisões já tomadas (NÃO reabrir):**
> - **D-A — GOWA vem bundled + ATIVO por padrão, e é DESINSTALÁVEL.** Download novo tem WhatsApp
>   funcionando de imediato (preserva a experiência atual); o usuário pode desinstalar pela tela de
>   Plugins e o subprocesso some.
> - **D-B — Telegram entra neste plano** (plugin novo `telegram`).
> - **D-C — Remover os endpoints/UI legados** single-channel GOWA-only (`/api/whatsapp/*`, `/api/qr*`,
>   QR do Dashboard) em vez de mantê-los como shim.
> - **D-D — Manter o SetupWizard** com o fluxo atual (1º boot = conectar um número via QR do GOWA), só
>   **religando** a tela nos endpoints por-canal (`/api/channels/{id}/qr|status`) já que `/api/qr*` some.
> - **D-E — FORA deste plano:** o "contrato público formal" (SDK versionado + gerador
>   `/new-channel-plugin` + `/api/providers` para metadados de provider data-driven). Consequência
>   honesta: um provider **de terceiro** vai funcionar ponta-a-ponta (registrar, receber, enviar), mas
>   aparecerá com **rótulo/cor genéricos** na UI até os metadados virarem data-driven (ver §8 "Dívida
>   aceita"). Os três canais de 1ª parte (GOWA/Cloud/Telegram) já têm metadados no frontend.

---

## 0. Estado atual VERIFICADO (working tree de 2026-06-20)

> ⚠️ **Os planos 01/02 marcam coisas como `nao_feito`, mas o CÓDIGO está MUITO à frente.** Tudo abaixo
> foi confirmado por leitura com âncora `arquivo:linha`. **Na execução, re-ancore SEMPRE por `grep`
> (nome de função/rota/símbolo), nunca por número de linha fixo** — o código muda.

### 0.1 O que JÁ existe (NÃO reconstruir)

- **Abstração de canal completa e estável** (plano 02 Fase 0):
  - [`channels/base.py`](../channels/base.py): `Channel` (ABC) com `start/stop/status/get_qr`,
    `send_text/send_media/mark_read/send_presence/react/revoke`, `send_template/list_templates/...`,
    `parse_inbound(raw) -> list[InboundEvent]`; `ChannelCapabilities`
    (`qr/templates/groups/presence/reactions/media/inbound_route/session_window_hours`); `SendResult`.
  - [`channels/events.py`](../channels/events.py): `InboundEvent`
    (`channel_id, provider, kind, direction, external_msg_id, chat_id, sender_id, sender_name,
    is_group, text, media_type, media_path, media_extras, ts, raw`).
  - [`channels/registry.py`](../channels/registry.py): `ChannelRegistry` — Camada 1
    (`register_provider(cls)` por `cls.provider`), Camada 2 (`add_channel/get/remove_channel`),
    e API de DB para providers (`get_credential/set_credential/get_channel/list_channels/set_status`).
  - [`channels/outbound.py`](../channels/outbound.py): `OutboundRouter` — superfície única de envio,
    **capability-gated** (`send_text/send_media/send_template/send_presence/react/revoke/mark_read`,
    `session_open(channel_id, last_inbound_ts)`).
- **Ponto de extensão de plugin para canal:** loader reconhece `entry.channels` →
  `CHANNEL_PROVIDERS = [Cls, ...]` e registra no `ChannelRegistry`
  ([`plugins/loader.py:281-293`](../plugins/loader.py#L281)). Também reconhece
  `entry.lifecycle` → `setup(ctx)`/`teardown(ctx)` ([`loader.py:295-304`](../plugins/loader.py#L295)).
- **Lifecycle de plugin aguardado** (plano 09 Fase 1): [`plugins/lifecycle.py`](../plugins/lifecycle.py)
  — `run_setup`/`run_teardown` chamados/awaited pela lifespan
  ([`server/app.py:281-298`](../server/app.py#L281)). Teardown roda **mesmo no toggle**: `disable`/`delete`
  passam `on_before_exit=lambda: _lifecycle_manager.run_teardown(plugin_id)` ao `schedule_restart`
  ([`server/routes/plugins.py:115-117,146-148`](../server/routes/plugins.py#L115)).
- **Runtime de subprocesso gerenciado** (plano 09 Fase 4 — PRONTO):
  [`runtime/subprocess_service.py`](../runtime/subprocess_service.py) — `SubprocessService.spawn(spec)`,
  `SubprocessSpec` (cmd/env/readiness/signature/stale-kill/process-group/`PR_SET_PDEATHSIG`
  die-with-parent/watchdog), `stop_owner(owner)`. Exposto a plugins via
  `PluginContext.spawn_subprocess(spec)` e `spawn_task(...)`
  ([`plugins/context.py:141-173`](../plugins/context.py#L141)); ambos auto-parados no teardown
  (`_stop_owned` em [`lifecycle.py:119-133`](../plugins/lifecycle.py#L119)).
- **Webhook genérico por-canal + ingresso agêntico** (plano 11 — PRONTO):
  `GET/POST /api/webhook/{provider}/{channel_id}` ([`server/routes/channel_webhook.py`](../server/routes/channel_webhook.py)):
  GET faz handshake (Cloud `hub.challenge`); POST chama `registry.get(channel_id).parse_inbound(raw)` e
  **`_dispatch_events`** roteia `message → deps.ingest_event` (mesmo orquestrador do GOWA),
  `reaction → set_reaction + bus`, `receipt → update_status + bus`. **O Cloud já responde ponta-a-ponta.**
- **`ingest_event(InboundEvent)`** ([`webhook.py:1195-1294`](../server/routes/webhook.py#L1195)): funil
  provider-agnóstico — idempotência por `(channel_id, external_msg_id)`, resolve contato, aplica
  `filter.message.before_save`, batcheia por `(channel_id, chat_id)` e agenda o orquestrador.
- **Inbox por canal** (plano 11, migration `0018_inbox_per_channel`): `resolve_inbox_id(channel_id)`
  ([`agent/memory.py:19-33`](../agent/memory.py#L19)) usa `inbox_repo.get_or_create_for_channel`. Saída já
  roteada por canal: `_send_reply` usa `OutboundRouter` ([`webhook.py:518-647`](../server/routes/webhook.py#L518)),
  batch/orquestrador chaveados por `(channel_id, phone)`.
- **Plugin Cloud de referência** ([`storages/plugins/whatsapp_cloud/`](../storages/plugins/whatsapp_cloud/)):
  `plugin.yaml` (`entry.channels/routes/settings`, screen `config:true`), `channels.py`
  (`WhatsAppCloudChannel(Channel)` com `parse_inbound`/`send_*`/`status`, `CHANNEL_PROVIDERS=[...]`),
  `settings.py`, `static/whatsapp_cloud.js`. **É o molde do plugin GOWA e do Telegram.**
- **Adapter GOWA interno** ([`channels/providers/gowa_channel.py`](../channels/providers/gowa_channel.py)):
  `GOWAChannel(Channel)` (delega ao `GOWAClient`), `build_gowa_channel(channel_id, row, gowa_client, gowa_manager)`
  (canal `default` reusa o client singleton; outros ganham client por-device). `parse_inbound` é um **stub
  que retorna `[]`** — o parsing real ainda é inline no webhook.

### 0.2 O GAP (o que falta para o objetivo) — e onde está o acoplamento GOWA

| # | Acoplamento | Local (re-grep!) | Severidade |
|---|---|---|---|
| G1 | GOWA instanciado incondicionalmente | [`main.py:48-58`](../main.py#L48) (`GOWAManager`/`GOWAClient`), passados a `create_app` | bloqueador |
| G2 | Core registra o provider GOWA e materializa com `if provider=="gowa"` | [`server/app.py:113`](../server/app.py#L113) (`register_provider(GOWAChannel)`), [`:138-143`](../server/app.py#L138) (`build_gowa_channel`) | bloqueador |
| G3 | 4 tasks GOWA registradas incondicionalmente | [`server/app.py:262-268`](../server/app.py#L262) (`gowa_start/status_poll/qr_poll/avatar_fetch`); loops em [`server/background.py`](../server/background.py) | bloqueador |
| G4 | `group_mentions.init(gowa_client)` incondicional | [`server/app.py:218`](../server/app.py#L218); usos espalhados em `webhook.py`/`background.py` | significativo |
| G5 | Parsing inbound do GOWA inline (não via `parse_inbound`) | handler `webhook()` em [`webhook.py:1299`](../server/routes/webhook.py#L1299); `_extract_media` [`:62-359`](../server/routes/webhook.py#L62); batch key fixo `("default", phone)` [`:2085`](../server/routes/webhook.py#L2085) | bloqueador |
| G6 | Chamadas diretas a `gowa_client` no inbound | `webhook.py`: `get_message_filename` [`:1689`](../server/routes/webhook.py#L1689), `get_group_name` [`:1862`](../server/routes/webhook.py#L1862), `can_bot_send_in_group` [`:1876`](../server/routes/webhook.py#L1876), `is_chat_archived` [`:1931`](../server/routes/webhook.py#L1931) | significativo |
| G7 | Endpoint `/api/webhook` (GOWA) é exato e dono do core | [`server/app.py:371`](../server/app.py#L371) (`_AUTH_EXEMPT_EXACT`), handler em `webhook.py` | significativo |
| G8 | Canal `default` semeado como `gowa` por migration | [`db/alembic/versions/20260620_0011_channels.py:66-71`](../db/alembic/versions/20260620_0011_channels.py#L66) | médio |
| G9 | Endpoints/UI legados single-channel GOWA-only | [`server/routes/whatsapp.py`](../server/routes/whatsapp.py) (`/api/qr`, `/api/qr/refresh`, `/api/whatsapp/reconnect`, `/api/whatsapp/logout`); frontend [`web/static/js/services/api.js:101-119`](../web/static/js/services/api.js#L101), [`QRCode.js`](../web/static/js/components/QRCode.js), [`Dashboard.js`](../web/static/js/components/Dashboard.js), [`SetupWizard.js`](../web/static/js/components/SetupWizard.js) | médio (D-C/D-D) |
| G10 | Constantes GOWA no core | [`config/settings.py:46,75`](../config/settings.py#L46) (`gowa_port` default 64999) | menor |
| G11 | `ToolContext`/`PluginContext` NÃO expõem `channel_registry`/`outbound_router`/`ingest_event` | [`plugins/context.py`](../plugins/context.py) | bloqueador p/ providers-plugin ricos |

### 0.3 Cadeia Alembic (P82 — LEIA antes de gerar migration)

Head atual = **`0021_template_permissions`** (ver `db/alembic/versions/`, ordenado). Se este plano
precisar de migration (ver §3 — provavelmente **não** precisa de DDL), o número é **`0022_*`** com
`down_revision = "0021_template_permissions"`. **P82: cadeia linear, um único head.** Re-cheque o head
real no momento de implementar (`ls db/alembic/versions/ | sort | tail`).

---

## 1. Princípio de arquitetura (alvo)

```
GOWA webhook  ─┐  (POST /api/webhook/gowa/{channel_id})
Cloud webhook ─┼─→ registry.get(channel_id).parse_inbound(raw) → [InboundEvent]
Telegram      ─┘        │
                        ├─ kind=message  → ingest_event(ev)        → orquestrador (já existe)
                        ├─ kind=reaction → set_reaction + bus       (já existe)
                        ├─ kind=receipt  → update_status + bus      (já existe)
                        └─ kind=presence/edited/revoked/group/...   (estender _dispatch_events)
                                       │
                          OutboundRouter.send(conv.channel_id, ...) → registry.get().send_text()
```

**Regras invioláveis:**
- **Um `Channel` novo = `parse_inbound` + `send_*` + `capabilities`. ZERO mudança no core.** Nenhum
  `if provider == "..."` no handler/pipeline.
- **O core sobe e funciona com 0 canais.** Nenhum import de `gowa.*` no caminho de boot do core; nenhuma
  task/serviço GOWA registrado pelo core.
- **GOWA é um plugin como o Cloud** (`storages/plugins/gowa/`), bundled + ativo por padrão, desinstalável.
- **Capacidades dirigem o comportamento** (presença, grupos, @menção, templates, janela 24h) — nunca o
  nome do provider.

---

## 2. Fases (ordem de execução)

> Regra geral: **`tests/test_endpoints.py` verde ao fim de CADA fase.** Trabalhe incremental, commitando
> por fase. As Fases 0–2 mexem no caminho mais quente (webhook) — blinde com os testes existentes ANTES
> de mover lógica.

### Fase 0 — GOWA inbound atrás do contrato (`parse_inbound` → `ingest_event`), sem mudar comportamento
> A maior e mais arriscada. Objetivo: o GOWA passa a ingressar pelo MESMO funil do Cloud, **com
> comportamento idêntico**. Ainda **no core** (a mudança física pro plugin é a Fase 2).

**0.1 — Extrair o parsing GOWA para função pura.** Crie `parse_gowa_inbound(raw, *, client, bot_phone) ->
list[InboundEvent]` (sugestão: `gowa/inbound.py`, ou método de `GOWAChannel`). Mova para ela TODA a
tradução de payload hoje inline no handler `webhook()` ([`webhook.py:1299+`](../server/routes/webhook.py#L1299))
e em `_extract_media` ([`:62-359`](../server/routes/webhook.py#L62)). Ela deve emitir `InboundEvent` para
**todos os `kind` que o GOWA produz** (ver checklist §5):
- `message` (texto + mídia: image/audio/video/sticker/document/location/poll/contact/...; `media_type` +
  `media_path` + `media_extras` idênticos ao atual);
- `reaction`, `receipt` (ack delivered/read), `edited`, `revoked`, `presence` (composing/paused),
  `group_participants` (join/leave/promote/demote), `connection` (connect/disconnect/QR);
- echo do próprio celular → `direction="out"` (`message.sent` source `echo`).

`GOWAChannel.parse_inbound` ([`gowa_channel.py:137-141`](../channels/providers/gowa_channel.py#L137)) passa a
chamar `parse_gowa_inbound(raw, client=self._client, ...)` em vez de retornar `[]`.

**0.2 — Estender `_dispatch_events`** ([`channel_webhook.py:44-94`](../server/routes/channel_webhook.py#L44))
para tratar os `kind` adicionais que o GOWA precisa (presence/edited/revoked/group_participants/connection/
echo-out), **reusando as MESMAS emissões de bus e atualizações de estado** que hoje vivem no handler
`webhook()` (mover, não duplicar). Os eventos do bus (`message.edited`, `message.revoked`,
`presence.changed`, `group.participants_changed`, `connection.changed`, `message.sent`, etc.) devem sair
**idênticos** — plugins de terceiros (event_logger etc.) dependem deles.

**0.3 — Migrar o webhook do GOWA para a rota genérica.** O subprocesso GOWA hoje é iniciado com
`--webhook http://127.0.0.1:{web_port}/api/webhook` (ver `gowa/manager.py`). Passe a iniciá-lo com
`--webhook .../api/webhook/gowa/default` (a rota genérica que já existe). Assim o handler `webhook()`
gigante do core deixa de ser necessário. **Compat durante a transição:** mantenha o `/api/webhook` exato
como **alias fino temporário** que resolve o canal GOWA `default` e faz `parse_inbound → _dispatch_events`
(idêntico à rota genérica), para não quebrar um GOWA já rodando com a URL antiga; **remova** o handler
inline antigo. (Remoção do alias é cleanup pós-migração.)

**0.4 — Capability-gate o que sobrou GOWA-only no inbound (G6).** As chamadas
`get_message_filename`/`get_group_name`/`can_bot_send_in_group`/`is_chat_archived` viram parte do
`parse_gowa_inbound` (via `self._client`), ou são guardadas por capability. `group_mentions` (G4): no inbound
genérico, só resolver @menção quando `capabilities.groups` for `True` (Cloud/Telegram pulam). A presença
("digitando") já é capability-gated no `_send_reply` via `outbound.capabilities` — manter.

**Pronto (Fase 0):** GOWA recebe/responde **idêntico**, agora via `parse_gowa_inbound → InboundEvent →
ingest_event/_dispatch_events`. Cloud inalterado. `tests/test_endpoints.py` verde (inclusive os testes de
webhook: presence, echo, ack, reaction, reply/quoted, revoke).

### Fase 1 — Núcleo agnóstico de canal + runtime exposto a plugins
> Objetivo: core sobe e funciona com 0 GOWA; plugins de canal têm o que precisam no contexto.

**1.1 — Expor runtime ao contexto do plugin (G11).** Em [`plugins/context.py`](../plugins/context.py):
adicione `channel_registry`, `outbound_router` e `ingest_event` ao `PluginContext` (e, se útil a tools,
ao `ToolContext`). Wire na lifespan ([`server/app.py`](../server/app.py)) e no
`PluginLifecycleManager._ensure_context` ([`lifecycle.py:44-53`](../plugins/lifecycle.py#L44)). Isso permite
o plugin GOWA: registrar/desregistrar seu provider e canais vivos no registry, e o plugin Telegram empurrar
inbound. (Hoje `register_provider` dos plugins roda no `create_app` ANTES da lifespan — ver
[`app.py:124-125`](../server/app.py#L124); mas materializar canais vivos e spawnar subprocesso acontece no
`setup(ctx)`, que precisa do registry no `ctx`.)

**1.2 — Tornar o boot do core livre de GOWA.** Remova de [`main.py`](../main.py) a instanciação de
`GOWAManager`/`GOWAClient` (G1) e pare de passá-los a `create_app`. Em [`server/app.py`](../server/app.py):
remova `register_provider(GOWAChannel)` e o ramo `if provider=="gowa": build_gowa_channel(...)` (G2) — a
materialização vira **genérica** para todos os providers (`provider_cls(cid, registry=...)`). Remova as
TaskSpec `gowa_start/status_poll/qr_poll/avatar_fetch` (G3) e `group_mentions.init(gowa_client)` (G4) do
core. `_on_gowa_restart`/`gowa_manager.stop()` da lifespan saem. **`audit_purge` permanece no core** (não é
GOWA). `ServerDeps.gowa_manager/gowa_client` podem virar `None` (mantidos só até a Fase 2 remover os
últimos usos) — ou removidos já, ajustando `channels.py`/`setup.py`/`contacts.py` (ver G6/§5).

**1.3 — Materialização de canal genérica e resiliente.** O loop de
[`app.py:133-161`](../server/app.py#L133) deve: para CADA row de `channel_repo.list_all()`, achar
`provider_cls = registry.get_provider(provider)`; se `None` (provider não instalado), **pular logando**
(nunca fatal). O canal `default` (provider `gowa`) só materializa se o **plugin GOWA estiver instalado**
(que registra o provider). Numa instalação só-Cloud sem GOWA, o `default` simplesmente não materializa e o
core sobe normal.

**1.4 — `default` não pode quebrar sem GOWA (G8).** `agent/memory.py` usa `DEFAULT_CHANNEL_ID="default"` e
`resolve_inbox_id` com fallback ([`memory.py:11,19-33`](../agent/memory.py#L11)) — já é resiliente (cria
inbox sob demanda). Garanta que NENHUM caminho de boot do core **exija** a existência do canal `default`
nem do provider `gowa`. O seed da migration `0011` continua criando o row `default` (ok — ele só vira canal
vivo quando o plugin GOWA materializa).

**Pronto (Fase 1):** com o plugin GOWA **desabilitado**, o core sobe, `/plugins` lista, Cloud funciona, e
não há subprocesso GOWA nem tasks GOWA. Nenhum `import gowa.*` no caminho de boot do core.

### Fase 2 — Extrair o GOWA para `storages/plugins/gowa/` (bundled, ativo, desinstalável)
> Objetivo: o GOWA roda como plugin igual ao Cloud, dono do próprio subprocesso e polling.

**2.1 — Criar o plugin** em `assets/plugin_examples/gowa/` (bundled; copiado em `storages/plugins/gowa/`).
Layout (espelha o `whatsapp_cloud`):
```
assets/plugin_examples/gowa/
├── plugin.yaml          # id: gowa; entry.channels: channels; entry.lifecycle: lifecycle; entry.settings: settings
├── __init__.py
├── channels.py          # GOWAChannel(Channel) + parse_gowa_inbound + CHANNEL_PROVIDERS=[GOWAChannel]
├── client.py            # <- mover gowa/client.py
├── inbound.py           # <- parse_gowa_inbound (Fase 0), se separado
├── group_mentions.py    # <- mover agent/group_mentions.py (é GOWA-específico: JID, /user/my/contacts)
├── lifecycle.py         # setup(ctx): spawn subprocess + spawn polling tasks + wire group_mentions; teardown(ctx)
├── settings.py          # class Settings(BaseModel): gowa_port, gowa_isolation, ...
├── bin/                 # opcional; ou referência ao bin/gowa.exe do core (ver 2.5)
└── static/gowa.js       # tela config:true — status + QR do(s) canal(is) GOWA
```
`plugin.yaml` exemplo:
```yaml
id: gowa
name: WhatsApp (GOWA)
version: 1.0.0
whatsbot_api_version: ">=1.0,<2.0"
description: >-
  Caixa de entrada WhatsApp via GOWA (go-whatsapp-web-multidevice), conectada por QR.
  Roda como subprocesso gerenciado; desinstalar derruba a conexão.
author: WhatsBot
entry:
  channels: channels
  lifecycle: lifecycle
  settings: settings
screens:
  - id: gowa-config
    title: WhatsApp (GOWA)
    path: /gowa/config
    icon: whatsapp
    config: true
    component: /plugins/gowa/static/gowa.js
permissions: [channel.provider, process.spawn, net.outbound]
dependencies: []
```

**2.2 — Subprocesso via serviço gerenciado (substitui `gowa/manager.py`).** No `lifecycle.setup(ctx)`:
construa um `SubprocessSpec` (de `runtime.subprocess_service`) com
`cmd=[gowa_bin, "rest", "--port", str(port), "--webhook", f"http://127.0.0.1:{web_port}/api/webhook/gowa/default"]`,
`signature="gowa"` (stale-kill), `readiness=lambda: client.health_check()`, `owner="gowa"`, e chame
`ctx.spawn_subprocess(spec)`. **Apague `gowa/manager.py`** (o `ManagedProcess` do core já faz process-group +
die-with-parent + stale-kill + watchdog — não reimplementar). O `teardown(ctx)`/`_stop_owned` para o
subprocesso automaticamente (owner=`gowa`).

**2.3 — Polling como tasks do plugin.** Mova `status_poll_loop`/`qr_poll_loop`/`avatar_fetch_task` de
[`server/background.py`](../server/background.py) para o plugin e registre-as via `ctx.spawn_task("status_poll",
...)` etc. no `setup`. Elas devem **broadcastar status por-canal** (ver 2.6) via `ctx.broadcast`. `start_gowa_task`
(boot) é substituído pelo spawn do subprocesso no setup. **`audit_purge_loop` fica no core.**

**2.4 — Provider e materialização.** `GOWAChannel` (de `channels/providers/gowa_channel.py`) e
`build_gowa_channel` movem para `gowa/channels.py` do plugin; exporte `CHANNEL_PROVIDERS=[GOWAChannel]`. O
`__init__` do provider deve aceitar `(channel_id, registry=...)` (contrato do loader,
[`app.py:153`](../server/app.py#L153)) e construir/escolher seu `GOWAClient` por-device a partir do row
(`registry.get_channel(channel_id)["gowa_device_id"]`). No `setup`, materialize os canais GOWA existentes
(`registry.list_channels()` filtrando `provider=="gowa"`) com `registry.add_channel(...)`.

**2.5 — Binário GOWA.** Hoje em `bin/gowa.exe`. Decida (e documente no plugin): (a) o plugin referencia o
`bin/` do core; ou (b) o binário vira asset do plugin. Para o EXE/PyInstaller e Docker, manter o bin no
core e o plugin apenas referenciá-lo é o menor risco. Resolva o caminho como `gowa/manager.py` fazia
(trata `sys._MEIPASS`).

**2.6 — Status/QR por-canal (substitui o estado global).** O polling do plugin atualiza
`registry.set_status(channel_id, connected=..., logged_in=..., own_phone=...)` e broadcasta `status`/`qr_update`
(ou eventos por-canal) via `ctx.broadcast`, para a UI da Fase 4. A identidade do bot (`bot_phone/bot_name`,
hoje `state.bot_phone`) passa a ser **por-canal GOWA** e alimenta o `group_mentions` do plugin
(`set_bot_identity`). Onde o core hoje lê `state.bot_phone` no inbound, isso vira parte do
`parse_gowa_inbound`/contexto do canal.

**2.7 — Bundle + ativo por padrão (D-A) + bootstrap especial (P23).**
- **Fresh install:** `bootstrap_initial_plugins` ([`loader.py:79-102`](../plugins/loader.py#L79)) copia
  `assets/plugin_examples/*` quando `storages/plugins/` está vazio. O `gowa` precisa nascer **enabled=1**.
  Como `discover_and_load` registra plugins novos com `enabled=0` por padrão, adicione um passo: **o plugin
  `gowa` é habilitado por padrão** (ex.: `bootstrap_initial_plugins` marca `plugin_repo.set_enabled("gowa",
  True)` ao copiá-lo; ou um default na descoberta para a lista de "bundled enabled"). O `whatsapp_cloud`
  continua `enabled=0` (instalado pela tela).
- **Instalações EXISTENTES (já têm `storages/plugins/` populado e um canal `default` GOWA):**
  `bootstrap_initial_plugins` NÃO copia (pasta não-vazia). Adicione um **bootstrap especial idempotente** no
  boot: se existir um canal `default` com provider `gowa` E a pasta `storages/plugins/gowa/` não existir,
  copie de `assets/plugin_examples/gowa/` e `set_enabled("gowa", True)`. Assim quem já usava WhatsApp
  continua conectado após o upgrade, agora via plugin.

**2.8 — Settings/constantes (G10).** Mova `gowa_port`/isolamento para `settings.py` do plugin
(persistidas como `plugin.gowa.*`). Remova as constantes GOWA de `config/settings.py` que não forem
genéricas.

**Pronto (Fase 2):** GOWA aparece em `/plugins` (ativo). Desativar/desinstalar → `teardown` derruba o
subprocesso (owner=`gowa`) e o provider some; o core segue rodando (Cloud/Telegram/nenhum). Instalação
só-Cloud nunca sobe o binário GOWA. Conexão existente preservada no upgrade.

### Fase 3 — Plugin Telegram (`storages/plugins/telegram/`)
> Objetivo: 1ª caixa de entrada construída 100% sobre o ponto de extensão público — prova do multi-canal
> dinâmico. **NÃO toca no core.**

Ver §4 para a spec detalhada. Resumo: `TelegramChannel(Channel)` (`parse_inbound` do update do Telegram;
`send_text`/`send_media` via Bot API; `capabilities`: `qr=False, groups=True, presence=False,
session_window_hours=0`), credencial `bot_token`, inbound por **long-poll** (`getUpdates` via
`ctx.spawn_task`) OU **webhook** (`/api/webhook/telegram/{channel_id}` — rota genérica já existe). Form de
`bot_token` na tela Canais.

### Fase 4 — Remover legados (D-C) + religar SetupWizard/UX (D-D)
> Objetivo: tirar o single-channel GOWA-only; tudo via endpoints por-canal.

**4.1 — Remover endpoints legados (G9):** apague de [`server/routes/whatsapp.py`](../server/routes/whatsapp.py)
`/api/qr`, `/api/qr/refresh`, `/api/whatsapp/reconnect`, `/api/whatsapp/logout` (e o
`whatsapp.register_routes` em [`app.py:517`](../server/app.py#L517)). Reconectar/logout/QR agora são
por-canal: já existem `GET /api/channels/{id}/status`, `GET /api/channels/{id}/qr`
([`channels.py:221-271`](../server/routes/channels.py#L221)); adicione `POST /api/channels/{id}/reconnect` e
`POST /api/channels/{id}/logout` (delegando a `inst._client.reconnect()/logout()` do canal GOWA, ou a
métodos do `Channel`). Remova de [`web/static/js/services/api.js:101-119`](../web/static/js/services/api.js#L101)
`reconnect/logout/fetchQrBlob/refreshQr` (ou re-aponte para os endpoints por-canal).

**4.2 — Religar o SetupWizard (D-D):** [`SetupWizard.js`](../web/static/js/components/SetupWizard.js) usa
`fetchQrBlob`/`refreshQr` ([:4,76,247]). Re-aponte o passo "Conectar WhatsApp" para
`GET /api/channels/default/qr` + `GET /api/channels/default/status` (polling de status para auto-avançar).
**Manter o fluxo/UX** (conectar um número via QR do GOWA no 1º boot). Reúse o componente `QRConnect` do
[`ChannelsManager.js:217-326`](../web/static/js/components/ChannelsManager.js#L217) se possível.

**4.3 — Dashboard/ConnectionStatus:** o QR/status single-channel do [`Dashboard.js`](../web/static/js/components/Dashboard.js)
+ [`QRCode.js`](../web/static/js/components/QRCode.js) viram a visão por-canal (status por canal vindo de
`/api/channels/connected` / `/api/channels/{id}/status`). Modo escuro: usar `wa-*`/`.wa-field` (regra do
CLAUDE.md), testar.

**Pronto (Fase 4):** nenhum endpoint/UI single-channel GOWA-only. Conectar GOWA = um canal entre vários,
pela tela Canais; wizard idêntico ao usuário, sobre a API por-canal.

---

## 3. Migration (Alembic)

**Provavelmente NÃO é necessária migration de DDL.** As tabelas `channels`/`channel_credentials`
(`0011`), `inboxes`/`conversations`/`contact_inboxes` (`0013`/`0018`) já existem; o canal `default` GOWA já
é semeado (`0011`). A "ativação do plugin GOWA" e o "bootstrap especial" (2.7) são lógica de **plugin/boot**,
não Alembic.

Se optar por limpar metadados GOWA-específicos das colunas top-level (`channels.gowa_device_id`,
`channels.gowa_isolation` → mover pra `channels.config` JSON), seria uma migration `0022_*`
(`down_revision="0021_template_permissions"`, P82 linear) — **opcional, não-bloqueador**; recomendado
**adiar** (a dívida é cosmética e o `build_gowa_channel`/provider lê `gowa_device_id` direto hoje).

---

## 4. Spec do plugin Telegram (Fase 3)

`storages/plugins/telegram/` (bundled em `assets/plugin_examples/telegram/`, **enabled=0** — instala pela
tela como o Cloud). Layout espelha o `whatsapp_cloud`.

- **`plugin.yaml`:** `id: telegram`; `entry.channels: channels`; `entry.settings: settings`; e — se usar
  long-poll — `entry.lifecycle: lifecycle`; screen `config:true` (`/plugins/telegram/static/telegram.js`);
  `permissions: [channel.provider, net.outbound]`.
- **`channels.py` → `TelegramChannel(Channel)`** (`provider="telegram"`):
  - `capabilities = ChannelCapabilities(qr=False, templates=False, groups=True, presence=False,
    reactions=True, media=True, inbound_route="poll" ou "path", session_window_hours=0)`.
  - `parse_inbound(raw)`: traduz o objeto **Update** do Bot API → `InboundEvent`
    (`message`/`edited_message`/`callback_query`/`message_reaction`...): `chat_id = str(message.chat.id)`,
    `sender_id`, `text` ou `caption`, mídia (`photo`/`voice`/`audio`/`document`/`video`/`sticker`/`location`)
    → `media_type`/`media_extras` (guardar `file_id`; download = método próprio, análogo ao P16 do Cloud).
    `external_msg_id = str(message.message_id)`; `is_group = chat.type in {group, supergroup}`.
  - `send_text(chat_id, text, ...)` → `POST https://api.telegram.org/bot<token>/sendMessage`;
    `send_media(...)` → `sendPhoto`/`sendVoice`/`sendDocument` (`file` ou URL). Devolver `SendResult`
    com `external_msg_id`.
  - `status()` → `getMe` (`{connected: ok, logged_in: ok, needs_qr: False}`); `get_qr()` → `None`.
  - Credenciais via `self.registry.get_credential(self.channel_id, "bot_token")`.
  - `CHANNEL_PROVIDERS = [TelegramChannel]`.
- **Inbound — escolha:**
  - **Webhook (mais simples):** o usuário registra `https://<host>/api/webhook/telegram/<channel_id>` via
    `setWebhook` (a tela config pode mostrar a URL e ter um botão "registrar webhook"). A rota genérica já
    chama `parse_inbound → _dispatch_events`. **Preferir esta** se há host público (Coolify).
  - **Long-poll:** `lifecycle.setup(ctx)` faz `ctx.spawn_task("poll", getUpdates-loop)`; o loop chama
    `ctx.ingest_event(ev)` para cada `InboundEvent` (precisa do 1.1). Para EXE/desktop sem host público.
- **`settings.py`:** `class Settings(BaseModel)` (ex.: `api_base="https://api.telegram.org"`,
  `poll_interval=2`). O `bot_token` é **credencial de canal** (channel_credentials), não setting.
- **UI:** adicionar o campo `bot_token` ao `buildPayload`/form de criação de canal em
  [`ChannelsManager.js`](../web/static/js/components/ChannelsManager.js) (o provider `telegram` já está no
  dropdown e no `_ALLOWED_PROVIDERS`).

**Pronto:** criar um canal `telegram` (token do BotFather), mandar msg ao bot → vira conversa na inbox do
canal → o agente responde pelo bot. Zero mudança no core.

---

## 5. Checklist de comportamentos a PRESERVAR (Fases 0/2 — não regredir)

A extração do `webhook()` GOWA é a parte sensível. Garanta que cada um destes continue idêntico (há teste
para a maioria em `tests/test_endpoints.py`; adicione onde faltar):

- [ ] **Batch/merge** por `message_batch_delay` por `(channel_id, chat_id)`; texto concatenado com `\n`.
- [ ] **Echo do próprio celular** (msg enviada fora do app) → salva + `message.sent` source `echo`;
      supressão de loop via `recently_sent` por `(channel_id, ...)`.
- [ ] **Idempotência** por `(channel_id, external_msg_id)` (re-entrega).
- [ ] **Mídia**: todos os `media_type` (image/audio/video/video_note/sticker/document/location/live_location/
      poll/contact/contacts/buttons_response/list_response/order/product) com `media_path`/`media_extras`
      iguais; player do histórico não quebra.
- [ ] **Grupos**: prefixo `[Nome]:` do remetente; resolução de @menção via `group_mentions`
      (`apply_incoming` na entrada, `resolve_outgoing` na saída) **só quando `capabilities.groups`**;
      `group_reply_mode` (mention_only) respeitado; `group.participants_changed`/`group.joined`.
- [ ] **Identidade do bot** (`bot_phone`/`bot_name`) capturada (echo + `get_own_display_name`) e usada em
      `set_bot_identity`.
- [ ] **Reação/edição/revogação/receipt** → mesmos eventos de bus + updates de painel
      (`message_reaction`/`message_revoked`/`message_status`).
- [ ] **Presença/"digitando"** (orquestrador `_wait_typing_paused`/`_send_with_typing_guard`) só onde
      `capabilities.presence`.
- [ ] **Filtros**: `filter.webhook.payload`, `filter.message.before_save`, `filter.message.outgoing`,
      `filter.media.unknown`, `filter.transcription.*` continuam disparando nos mesmos pontos.
- [ ] **Archive status** (`is_chat_archived`) e **filename** de documento resolvidos pelo canal GOWA.
- [ ] **Transcrição/descrição** de áudio/imagem continua (chamadas diretas ao OpenAI no handler, não
      agênticas) — disparada no caminho de inbound de qualquer provider que entregue `media_path`.
- [ ] **avisos `conversation_event`** (plano 12) e **`ai_typing`** inalterados.

---

## 6. Riscos / cuidados

- **Regressão no webhook GOWA (Fase 0)** é o maior risco. Blinde com os testes de webhook ANTES de mover; faça
  por tipo de evento (texto → mídia → grupo → reaction/receipt → presence/connection), rodando os testes a cada
  passo. Mantenha o alias `/api/webhook` até a paridade estar verde.
- **`os._exit` no toggle**: o teardown JÁ roda via `on_before_exit` ([`plugins.py:115-117`](../server/routes/plugins.py#L115)),
  e o subprocesso tem die-with-parent (`PR_SET_PDEATHSIG`) + stale-kill no boot — então mesmo um exit duro não
  deixa GOWA órfão e ele não volta com o plugin desabilitado. Validar no Linux.
- **Binário GOWA no PyInstaller/Docker**: o caminho do `bin/gowa.exe` muda (`sys._MEIPASS`); preserve a
  resolução atual de `gowa/manager.py` ao mover (2.5). Em Docker, `WHATSBOT_DOCKER=1` → restart por container.
- **Bootstrap de upgrade (2.7)**: idempotente; só copia/habilita o `gowa` se há canal `default` GOWA e a pasta
  não existe. Não sobrescrever um `storages/plugins/gowa/` editado pelo usuário.
- **`group_mentions` movido pro plugin**: hoje é importado por `webhook.py`/`background.py` (core). Esses usos
  saem do core junto com o parsing (Fase 0/2). Em provider sem grupos, @menção é no-op.
- **Multi-número GOWA** (N devices/N canais) é evolução (plano 02 §3.3) — **fora do MVP** deste plano (Thiago
  mantém 1 GOWA bundled). O webhook por-canal `/api/webhook/gowa/{channel_id}` já deixa a porta aberta.
- **`statics/` em deploy** (CLAUDE.md): mídia/avatar dependem de volume persistente; não regredir.
- **Credenciais em texto puro (P15)**: manter mascaramento na borda; não logar `raw` com token.

---

## 7. Critério de pronto (do plano todo)

- [ ] **GOWA, WhatsApp Cloud e Telegram aparecem em `/plugins`** como plugins (GOWA ativo por padrão).
- [ ] **Desinstalar o GOWA** derruba o subprocesso e remove a conexão WhatsApp; o core segue no ar com os
      demais canais (ou nenhum). Reinstalar/ativar reconecta.
- [ ] **Instalação só-Cloud (GOWA desabilitado)** sobe sem subprocesso GOWA, sem tasks GOWA, sem `import gowa.*`
      no boot do core.
- [ ] **Nenhum `if provider == "..."`** no handler/pipeline; comportamento dirigido por `ChannelCapabilities`.
- [ ] **Adicionar um 3º/4º canal** exige só um `Channel` novo (parse+send+capabilities) — provado pelo Telegram.
- [ ] **Telegram** recebe e responde ponta-a-ponta por um canal criado na tela Canais.
- [ ] **Wizard** conecta um número via QR do GOWA (UX igual), sobre os endpoints por-canal; legados removidos.
- [ ] **Upgrade** de instalação GOWA existente continua conectado (bootstrap especial).
- [ ] `tests/test_endpoints.py` **verde** + novos testes: ingest GOWA via `parse_inbound`; boot sem GOWA;
      disable do GOWA derruba o subprocesso; Telegram parse/send (mock do Bot API).

---

## 8. Referências e dívida aceita

- **Planos:** [`02-canais-e-providers`](02-plano-canais-e-providers.md) (Fase 3 = este trabalho),
  [`09-fundacao-runtime`](09-plano-fundacao-runtime.md) (subprocesso/supervisor/lifecycle — consumidos),
  [`11-multicanal-runtime`](11-plano-multicanal-runtime.md) (ingresso/saída por canal — base pronta),
  [`docs-pesquisa/02-canais-e-providers.md`](../docs-pesquisa/02-canais-e-providers.md) (contrato/§3.4).
- **Arquivos-âncora:** `channels/{base,registry,events,outbound}.py`,
  `channels/providers/gowa_channel.py`, `plugins/{loader,lifecycle,context,restart}.py`,
  `runtime/subprocess_service.py`, `server/app.py`, `server/routes/{webhook,channel_webhook,channels,whatsapp}.py`,
  `server/background.py`, `agent/{memory,group_mentions}.py`, `storages/plugins/whatsapp_cloud/*` (molde),
  `web/static/js/components/{ChannelsManager,SetupWizard,QRCode,Dashboard}.js`.
- **Dívida aceita (D-E):** sem o "contrato público formal" (SDK versionado, gerador `/new-channel-plugin`,
  `/api/providers` com metadados data-driven), um provider de **terceiro** funciona ponta-a-ponta mas aparece
  com **rótulo/cor genéricos** na UI (metadados de provider hoje hardcoded em
  `Conversations.js`/`ChannelsManager.js`/`ChannelPickerModal.js`/`NewConversationModal.js`/`ContactList.js`).
  Fechar isso (~½ dia) é o follow-up que torna o sistema **plenamente** turnkey para terceiros — fora do
  escopo deste plano por decisão do Thiago.
