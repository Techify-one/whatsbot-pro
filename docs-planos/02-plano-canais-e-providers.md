# Plano de Implementação — 02: Canais e Providers (abstração, capacidades de runtime, GOWA-plugin, multi-número)

> Plano de execução derivado de [`docs-pesquisa/02-canais-e-providers.md`](../docs-pesquisa/02-canais-e-providers.md).
> Escopo deste plano: **abstração genérica de canal/provider** (contrato + registry no core), as **3
> capacidades de runtime CORE** (i lifecycle de plugin aguardado, ii supervisor de tasks de fundo, iii
> serviço de subprocesso gerenciado), **extração do GOWA para provider-plugin** (`storages/plugins/gowa/`),
> **suporte a multi-número**, e os **primeiros consumidores** (provider de teste + WhatsApp Cloud API
> webhook-only). Respeita o sequenciamento decidido: (i)+(ii) primeiro com provider barato sem
> subprocesso → (iii) + GOWA por último.
>
> **NÃO está no escopo deste plano** (vive em outros planos): o modelo de inbox/conversa de 3 níveis
> (Contact → ContactInbox → Conversation) — ver `01-inbox-e-conversas.md`; RBAC/usuários — ver
> `03-rbac-usuarios-permissoes.md`; motor multiagente/code-in-DB — ver `06`. Este plano referencia esses
> pontos de integração mas não os implementa.

---

## 0. Estado atual do código (baseline real, verificado)

Pontos cravados que este plano vai mexer:

- **`main.py:48-71`** — instancia `GOWAManager(port, data_dir, webhook_url)`, `GOWAClient(port)`,
  `AgentHandler(...)` e chama `create_app(settings, gowa_manager, gowa_client, agent_handler)`. Tudo
  singleton de 1 número.
- **`gowa/manager.py`** — 1 `subprocess.Popen`, porta fixa, watchdog em thread daemon com rate-limit
  (3 restarts/60s, `_max_restarts`/`_restart_window_sec` em `__init__`, linhas 45-48). `start()` monta
  `--webhook <url>` único (linhas 68-90). `stop()` faz terminate→kill (sem process group, sem
  die-with-parent). Limpeza de órfãos é externa (`pkill -f bin/gowa` no `linux_start.sh`).
- **`gowa/client.py:12,52,57`** — `_DEFAULT_DEVICE_NAME = "whatsbot"`, `self.device_id` fixo,
  `_headers` injeta `X-Device-Id` sempre o mesmo. `ensure_device()` (linhas 112-141) pega
  `devices[0]` se houver — singleton de 1 device.
- **`server/app.py`** — `ServerDeps` (linhas 46-60) carrega `gowa_manager`/`gowa_client` globais.
  `create_app` faz discovery de plugins (linhas 84-101) e registra tools/prompts/events/filters. O
  **lifespan** (linhas 142-183) cria 4 tasks HARDCODED numa lista local
  (`start_gowa_task`, `status_poll_loop`, `qr_poll_loop`, `avatar_fetch_task` — linhas 163-168) e no
  shutdown faz `task.cancel()` + `gowa_manager.stop()`. `group_mentions.init(gowa_client)` na linha 128.
- **`server/routes/webhook.py:1065-1066`** — endpoint único `@app.post("/api/webhook")`. Usa
  `gowa_client = deps.gowa_client` (linha 421) para responder — sempre o mesmo número. Parsing não lê
  `device_id`.
- **`plugins/loader.py`** — `_load_plugin_module` (linhas 166-260) reconhece `entry.tools`,
  `entry.prompts`, `entry.events`, `entry.filters`, `entry.routes`, `entry.settings`. **Não existe**
  `entry.channels`. **Não existe** gancho `setup()`/`teardown()`. O loader só importa o módulo.
- **`plugins/manifest.py`** — `PluginManifest` (linhas 25-46) e `_build_manifest`. `entry` é
  `dict[str,str]`. Não há campo de canais.
- **`plugins/context.py`** — `set_runtime(ws_manager, loop)` (linha 34) + `broadcast`. `ToolContext`,
  `PromptContext`, `EventContext`, `FilterContext`. **Não expõe** loop nem `stop_event` nem registro de
  cleanup ao plugin.
- **`plugins/restart.py`** — `schedule_restart()` toca trigger + `os._exit(0)` após 1.5s (linha 78).
  `os._exit` **pula** finalizers → subprocesso de plugin viraria órfão.
- **`plugins/events.py`** — `emit()` (linha 129) usa `run_coroutine_threadsafe(_fanout())` **sem**
  `.result()` → fire-and-forget. `app.startup`/`app.shutdown` em `emit_with_filter` BYPASS lists
  (linhas 53,79). Shutdown não aguarda handlers.
- **`server/background.py`** — `start_gowa_task`, `status_poll_loop`, `qr_poll_loop` (loop em
  `while not state.stop_event.is_set()`), `avatar_fetch_task` (`AVATAR_REFRESH_INTERVAL=1800`).
- **`db/tables.py`** — 13 `Table` objects; última migration `20260603_0006_contact_mention.py`.

---

## Visão geral das fases

| Fase | Entrega | Capacidade de runtime | Critério de pronto (resumo) |
|---|---|---|---|
| **0** | Contrato `Channel` + `ChannelRegistry` + tabelas `channels`/`channel_credentials` + ponto de extensão `entry.channels` no loader + migração "1 canal default" | — | Core fala com a interface; instalação atual roda como 1 canal GOWA via registry; um plugin pode registrar um provider |
| **1** | Capacidade (i) lifecycle aguardado + (ii) supervisor de tasks; **provider de teste** valida ambos | (i)+(ii) | Provider de teste (plugin) sobe um loop gerenciado, é cancelado limpo no disable/shutdown |
| **2** | WhatsApp Cloud API como provider-plugin webhook-only | consome (i) | Cloud API conecta por token, recebe e responde dentro da janela 24h; tokens em **texto puro** (sem cifragem — P15) |
| **3** | Capacidade (iii) subprocesso gerenciado + **GOWA extraído para `storages/plugins/gowa/`** + multi-número | (iii) | GOWA roda como plugin; N devices/N números; quem não usa GOWA não o roda |
| **4** | (esboço) Telegram e demais providers | usa (i)+(ii) | Fora do escopo de implementação deste plano; preparado pelo ponto de extensão |

> **Princípio transversal:** o core ganha **contratos + registries + capacidades**; core e plugins
> **registram implementações** nos mesmos. As 3 capacidades de runtime são CORE — infraestrutura que os
> plugins consomem (não podem ser fornecidas por plugin).

---

## Fase 0 — Abstração de canal + registry + tabelas + ponto de extensão

Objetivo: pagar a dívida de acoplamento cedo. Introduzir o contrato e o registry, criar as tabelas
core de canal, adicionar o ponto de extensão `entry.channels` ao loader, e migrar a instalação atual
para "1 canal default" — **sem** ainda extrair o GOWA (ele continua rodando como hoje, mas acessível
**através do registry**, atrás de um adapter `GOWAChannel` interno que envolve o `GOWAClient` existente).

### 0.1 Novo pacote `channels/` (core)

Criar:

- **`channels/__init__.py`** — exporta `Channel`, `SendResult`, `InboundEvent`, `ChannelCapabilities`.
- **`channels/base.py`** — contrato `Channel` (ABC) conforme §3.2 da pesquisa. Importável de forma
  **estável** por plugins (caminho `channels.base`). Assinaturas:
  - `provider: str`, `channel_id: str`, `capabilities: ChannelCapabilities` (dataclass:
    `qr/templates/groups/presence/reactions/media: bool`, `inbound_route: "path"|"poll"|"none"`).
  - Lifecycle: `start()`, `stop()`, `status() -> dict` (`{connected, logged_in, needs_qr, error}`).
  - Conexão opcional: `get_qr() -> bytes|None` (default `None`).
  - Saída: `send_text(chat_id, text, *, reply_to=None, mentions=None) -> SendResult`;
    `send_media(chat_id, kind, path_or_url, *, caption="", filename=None) -> SendResult`.
  - Opcionais default no-op: `mark_read`, `send_presence`, `react`, `revoke`.
  - Entrada: `parse_inbound(raw: dict) -> list[InboundEvent]` (abstrato).
  - Templates: `send_template(...)` → `NotImplementedError`.
- **`channels/registry.py`** — `ChannelRegistry` com **duas camadas** (§3.4.2 da pesquisa):
  1. `provider_name -> Provider class` (`register_provider(cls)` / `unregister_provider(name)`),
     populado por core e plugins.
  2. `channel_id -> Channel instance` (`get(channel_id)`, `all_channels()`, `add_channel(row)`,
     `remove_channel(channel_id)`), instâncias vivas.
  - Expõe ao core/providers a **API de leitura/escrita** de `channels`/`channel_credentials`
    (§3.4.6): `get_channel(channel_id)`, `list_channels()`, `set_status(channel_id, **fields)`,
    `get_credential(channel_id, key)`, `set_credential(channel_id, key, value)`. Essa API encapsula
    `channel_repo`/`channel_credential_repo` — o provider-plugin **não** acessa as tabelas por SQL
    direto (regra de §3.4.6; **P24** — provider lê/grava via `ctx.channel_registry`). No MVP a API só
    centraliza acesso e mascaramento (sem cifragem — P15).
- **`channels/events.py`** (ou reusar tipo do filtro de mensagem) — definição do `InboundEvent`
  (TypedDict/dataclass) com os campos do §3.1: `channel_id, provider, kind, direction,
  external_msg_id, chat_id, sender_id, sender_name, is_group, text, media_type, media_path,
  media_extras, ts, raw`. Reaproveitar o mesmo formato que `filter.message.before_save` já manipula.

### 0.2 Adapter GOWA interno (temporário-de-fase, ainda no core)

Nesta fase o GOWA **ainda não é plugin**. Para validar o contrato sem reescrever tudo, criar
**`channels/providers/gowa_channel.py`** (no core, removido na Fase 3) que implementa `Channel`
envolvendo o `GOWAClient` existente:

- `GOWAChannel(Channel)` com `provider="gowa"`, recebe `gowa_client` + `gowa_manager` + `channel_id`.
- `send_text/send_media/...` delegam ao client.
- `parse_inbound(raw)` reaproveita a lógica de parsing já em `webhook.py` (extrair para função pura
  reutilizável — ver 0.5).
- `status()` lê do `GOWAManager`/`/app/status`.

> Isto é deliberadamente descartável: na Fase 3 ele vira `storages/plugins/gowa/channels.py`. O ponto é
> exercitar o registry desde já com um provider real.

### 0.3 Tabelas core (migration Alembic)

Nova migration `db/alembic/versions/20260618_0007_channels.py` + 2 `Table` em `db/tables.py`:

```
channels (
  id            TEXT PRIMARY KEY,        -- "comercial" (snake_case)
  provider      TEXT NOT NULL,           -- gowa | whatsapp_cloud | telegram | test
  display_name  TEXT NOT NULL,
  enabled       INTEGER NOT NULL DEFAULT 1,
  gowa_device_id TEXT,                    -- X-Device-Id (só gowa)
  gowa_isolation TEXT DEFAULT 'shared',  -- shared | dedicated_process (Opção B, fallback)
  config        TEXT,                    -- JSON: prefs não-secretas por canal (modo polling/webhook etc.)
  connected     INTEGER NOT NULL DEFAULT 0,
  logged_in     INTEGER NOT NULL DEFAULT 0,
  own_phone     TEXT,
  last_error    TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
)

channel_credentials (
  channel_id  TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
  key         TEXT NOT NULL,             -- access_token | phone_number_id | verify_token | bot_token | ...
  value       TEXT NOT NULL,             -- TEXTO PURO no MVP (P15 — sem cifragem; ver "Dívida/risco" abaixo)
  PRIMARY KEY (channel_id, key)
)
```

> **P15 (⚠️ MUDANÇA, decidido 2026-06-19) — sem cifragem no MVP.** As credenciais (`channel_credentials.value`)
> ficam em **texto puro** no banco. **Não** há chave mestra (`WHATSBOT_SECRET_KEY`), **não** há módulo
> de cifragem (`channels/secrets.py` foi removido deste plano) e **não** há dependência `cryptography`.
> A única proteção ainda exigida é **mascaramento na API** (`••••1234`) — segredos nunca voltam em claro
> no `GET /api/channels` nem em logs. **Dívida/risco aceito conscientemente:** revisitar e cifrar em
> repouso (Fernet ou pgcrypto) **antes de produção séria** — anotado em "Perguntas em aberto" §3.

> `messages.channel_id` e `conversation.channel_id`/idempotência por `(channel_id, external_msg_id)` são
> **denormalizações de domínio de inbox** — pertencem ao plano 01. Aqui só criamos `channels`/
> `channel_credentials`. (Ver "Dependências de outros planos".)

Repos novos em `db/repositories/`:
- **`channel_repo.py`** — `list_all()`, `get(id)`, `create(row)`, `update(id, **fields)`,
  `set_status(id, **)`, `delete(id)`.
- **`channel_credential_repo.py`** — `get(channel_id, key)`, `set(channel_id, key, value)`,
  `get_all(channel_id)`, `delete_all(channel_id)`. **Sem cifragem (P15):** grava/lê o valor em texto
  puro. O **mascaramento** para a API é aplicado na camada de serialização (`server/routes/channels.py`),
  não no repo.

### 0.4 Ponto de extensão `entry.channels` no loader

- **`plugins/manifest.py`** — nenhuma mudança de schema obrigatória (`entry` já é `dict[str,str]`);
  `entry.channels` cai automaticamente em `entry_str`. Opcional: documentar a chave. Acrescentar
  `channels: list[str]` informativo em `to_public_dict` se quisermos exibir na UI de plugins.
- **`plugins/loader.py`** — em `_load_plugin_module` (após o bloco `entry.routes`, ~linha 247), ler
  `entry.channels`, importar o submódulo e coletar `CHANNEL_PROVIDERS = [cls, ...]` para um novo campo
  `LoadedPlugin.channel_providers: list[type]` (espelhando `loaded.tools`).
- **`server/app.py`** — no loop de wiring de plugins (linhas 93-101), para cada
  `loaded.channel_providers`, chamar `channel_registry.register_provider(cls)`.

### 0.5 Webhook: extrair parsing e introduzir roteamento por canal

- **Refatorar `server/routes/webhook.py`**: extrair a lógica de parsing inbound (hoje inline no handler
  `webhook`, linhas ~1289-1480) para uma função pura `parse_gowa_inbound(raw) -> list[InboundEvent]`
  reutilizada pelo `GOWAChannel.parse_inbound`. **Sem mudar comportamento** nesta fase.
- Manter o endpoint legado `POST /api/webhook` funcionando (compat), mas internamente: ler
  `body.get("device_id")` → resolver `channel_id` via `channel_repo` → `registry.get(channel_id)`.
  Se não houver mapeamento, cair no **canal default** (migração 0.6).
- Adicionar a **rota genérica** `POST/GET /api/webhook/{provider}/{channel_id}` (já preparando Cloud
  API e Telegram): resolve `registry.get(channel_id)`, chama `parse_inbound(raw)` → pipeline comum.
  GET é para handshakes (Cloud API `hub.challenge`, Fase 2).
- **`server/app.py`** — adicionar `/api/webhook/` ao `_AUTH_EXEMPT_PREFIXES` (linha 207) — os webhooks
  de provider não são autenticados por Bearer (validação é por verify_token/assinatura do provider).

### 0.6 Migração da instalação atual → "1 canal default"

Na migration `0007` (data migration, parte Python do `upgrade()`):
- Inserir 1 row em `channels`: `id="default"`, `provider="gowa"`, `gowa_device_id="whatsbot"`,
  `display_name="WhatsApp"`, `enabled=1`, `created_at/updated_at=now`.
- (A propagação de `channel_id` para conversas/mensagens é do plano 01.)

### 0.7 Wiring no `main.py` / `server/app.py`

- **`main.py`** — construir o `ChannelRegistry`, registrar o provider `gowa` (classe interna), instanciar
  o canal default a partir da row, e passá-lo em `create_app`. `gowa_client`/`gowa_manager` continuam
  existindo nesta fase (o `GOWAChannel` os envolve).
- **`server/app.py`** — adicionar `channel_registry` ao `ServerDeps`. Substituir
  `group_mentions.init(gowa_client)` por algo que continue funcionando (o gowa_client do canal default
  por enquanto). Onde o webhook respondia via `deps.gowa_client`, passar a resolver via
  `registry.get(channel_id).send_text(...)` (refactor incremental: começar pelo caminho de resposta da
  IA; manter fallback).

### Critério de pronto — Fase 0
- `channels`/`channel_credentials` existem (Alembic upgrade aplica do zero e em DB legado).
- A instalação atual de 1 número continua recebendo e respondendo mensagens, agora **passando pelo
  `ChannelRegistry`** (canal `default`, provider `gowa`) — nenhum `if provider == "gowa"` no handler.
- Um plugin de teste com `entry.channels` + `CHANNEL_PROVIDERS=[X]` é descoberto e
  `register_provider` é chamado (verificável por log).
- Testes de endpoint (`tests/test_endpoints.py`) passam; novo teste cobre `GET /api/webhook/...`
  resolvendo um canal mock.

---

## Fase 1 — Capacidades de runtime CORE (i) lifecycle aguardado + (ii) supervisor de tasks

Objetivo: construir e **validar** as duas capacidades baratas com um **provider de teste** (loop
trivial, sem subprocesso). Destrava providers webhook-only e polling-leve como plugin.

### 1.1 (i) Lifecycle de plugin aguardado — `setup(ctx)` / `teardown(ctx)`

- **`plugins/loader.py`** — em `_load_plugin_module`, reconhecer `entry.lifecycle` (módulo opcional)
  exportando `async def setup(ctx)` / `async def teardown(ctx)`; guardá-los em
  `LoadedPlugin.setup_fn`/`LoadedPlugin.teardown_fn`. **Contrato SÓ declarativo (P21):** via
  `entry.channels`/`entry.lifecycle` no manifest — **sem** registro imperativo (`register(registry)`)
  no MVP (menos superfície de erro, consistente com `CORE_TOOLS`).
- **`plugins/context.py`** — estender `set_runtime` para receber e guardar o **loop** e um **registro de
  cleanups** (modelo Disposable do VS Code / `async_on_unload` do Home Assistant — §3.4.4). Criar um
  `PluginRuntimeContext` (novo dataclass) passado a `setup/teardown` com: `plugin_id`, `loop`,
  `stop_event` (por-plugin), `register_cleanup(callable)`, `register_task(coro_factory, *, restart=...)`
  (delega ao supervisor 1.2), `channel_registry`, `plugin_db`, `broadcast`.
- **`server/app.py` (lifespan)** — após emitir `plugin.loaded`/`app.startup` (linhas 150-162):
  `await` o `setup(ctx)` de cada plugin carregado (sequencial, com try/except por plugin que registra
  `load_error` sem derrubar o app). No **shutdown** (linhas 170-183): **antes** de `gowa_manager.stop()`,
  `await` o `teardown(ctx)` de cada plugin (com **timeout fixo ~10s por plugin — P31**, depois segue),
  executando os cleanups registrados — **mesmo se o setup falhou** (padrão HA).
- **`plugins/restart.py`** — o ponto crítico: hoje `schedule_restart` faz `os._exit(0)` (linha 78) que
  **pula** o teardown. Mudança: no caminho de **disable/enable de plugin**, rodar `teardown` do(s)
  plugin(s) afetado(s) **antes** do `os._exit`. Implementar `schedule_restart` para, opcionalmente,
  receber um callback async de teardown a ser aguardado (com timeout) antes do exit. **P22/P25 (decidido):
  teardown aguardado antes do `os._exit` — restart-do-processo no MVP, sem hot-unload.** (Em dev com
  `--reload`, o teardown roda no shutdown do worker antigo via lifespan; o `os._exit` é o
  belt-and-suspenders + die-with-parent `PR_SET_PDEATHSIG` como rede de segurança.)
- **`plugins/events.py`** — os eventos `app.shutdown` continuam fire-and-forget para handlers de
  evento; o **lifecycle de provider** usa o caminho aguardado novo (não o bus de eventos). Documentar a
  distinção.

### 1.2 (ii) Supervisor de tasks de fundo

- **Novo módulo `runtime/supervisor.py`** (P26 — supervisor e subprocesso vivem em **novo pacote
  `runtime/`**; atualizar a árvore no CLAUDE.md) — `TaskSupervisor`:
  - `register(name, coro_factory, *, restart="transient"|"permanent"|"temporary",
    max_restarts=3, window_sec=60, backoff=...)` — registra uma corrotina de longa duração.
  - `start_all()` / `cancel_all()` — usados pelo lifespan.
  - Cada task roda em `asyncio.create_task`; ao terminar/excecionar, aplica a política de restart
    classificado + rate-limit (padrão OTP — generaliza o que `gowa/manager._watchdog` já faz:
    3 restarts/60s, linhas 180-209).
  - **Cancelamento via `task.cancel()` nativo (P27)** — `state.stop_event` global mantido **só por
    compat** durante a transição (loops legados ainda checam), mas o caminho canônico é o cancel nativo.
  - **Emite eventos no bus (P28, só na transição):** `task.crashed`, `subprocess.crashed`,
    `subprocess.restarted` — para observabilidade enquanto migramos. Pode sair depois.
  - **Health = só memória (P30):** estado de tasks/subprocessos vive em memória no MVP (sem
    persistência/healthcheck externo).
- **`server/app.py` (lifespan)** — **generalizar as 4 tasks hardcoded** (linhas 163-168) para
  registro no supervisor: `start_gowa_task`, `status_poll_loop`, `qr_poll_loop`, `avatar_fetch_task`
  passam a `supervisor.register(...)`; `supervisor.start_all()` no startup; `supervisor.cancel_all()`
  no shutdown (substitui o `for task in tasks: task.cancel()` — P27).
- **`plugins/context.py`** — o `PluginRuntimeContext.register_task(...)` delega ao supervisor. Um
  provider de polling registra seu loop por aí; o disable do plugin cancela suas tasks
  (rastreadas por `plugin_id` no supervisor).

### 1.3 Provider de teste (primeiro consumidor — valida i+ii)

- **Plugin `assets/plugin_examples/channel_test/`** (bundled, `enabled=0` por default):
  - `plugin.yaml` com `entry: { channels: channels, lifecycle: lifecycle }`.
  - `channels.py` — `TestChannel(Channel)` com `provider="test"`,
    `capabilities.inbound_route="poll"`; `start()` registra um loop trivial que a cada N s injeta um
    `InboundEvent` sintético (eco) no pipeline; `send_text` loga/ecoa.
  - `lifecycle.py` — `setup(ctx)` registra a task via `ctx.register_task(...)`; `teardown` confirma
    cancelamento.
  - Serve de **fixture viva** e de exemplo de referência para Telegram/Email.

### Critério de pronto — Fase 1
- Habilitar o `channel_test` faz o `setup` rodar, o loop ser supervisionado, e o eco aparecer no
  pipeline (verificável por WS/log).
- Desabilitar o plugin (ou shutdown do server) **aguarda** o `teardown` e cancela a task limpa — sem
  task órfã (verificável: nenhuma exceção de "Task was destroyed but it is pending").
- Uma task que lança exceção é reiniciada conforme a política (`transient`) e desiste após o rate-limit
  (log de "giving up"), análogo ao watchdog do GOWA.
- As 4 tasks core agora rodam **pelo supervisor** sem regressão (status/QR/avatar continuam
  funcionando).

---

## Fase 2 — WhatsApp Cloud API (provider-plugin webhook-only)

Objetivo: primeiro provider de produção como plugin, validando o caminho webhook-only + as peças de
segurança (handshake, janela 24h/templates). Tokens ficam em **texto puro** no MVP (P15 — sem cifragem).
Também serve de 2º caso barato que exercita (i) sem subprocesso.

### 2.1 Plugin `storages/plugins/whatsapp_cloud/` (bundled em `assets/plugin_examples/`)

Layout:
```
whatsapp_cloud/
├── plugin.yaml          # entry: { channels: channels, routes: routes, settings: settings }
├── channels.py          # WhatsAppCloudChannel(Channel) + CHANNEL_PROVIDERS
├── routes.py            # rotas de UI/config sob /api/plugins/whatsapp_cloud (NÃO o webhook)
├── settings.py          # prefs não-secretas (ex.: graph_api_version)
├── static/whatsapp_cloud.js  # tela de configuração (config:true) — formulário de token + templates
└── migrations/          # plugin_whatsapp_cloud_* se precisar (ex.: cache de templates) — opcional
```

### 2.2 `WhatsAppCloudChannel`

- `provider="whatsapp_cloud"`, `capabilities`: `qr=False`, `templates=True`, `groups=False`,
  `inbound_route="path"`.
- `start()`/`stop()` praticamente no-op (webhook-only, stateless); `status()` faz um ping ao Graph
  (`GET /{phone_number_id}` com token) → `{connected, logged_in}`.
- `send_text`/`send_media` → `POST https://graph.facebook.com/v{ver}/{phone_number_id}/messages`
  (token via `channel_registry.get_credential(channel_id, "access_token")`).
- `send_template(...)` implementado (HSM) — usado fora da janela 24h.
- `parse_inbound(raw)` percorre `entry[].changes[].value` (§5.3): `metadata.phone_number_id` →
  `channel_id`; `messages[].from` → `chat_id`; `contacts[].profile.name` → `sender_name`;
  `messages[].type` → `media_type`; **mídia por media-ID** → `GET /{media_id}` → URL temporária →
  **baixar e cachear** em `statics/media/` no mesmo esquema de `media_path` do GOWA (**P16** —
  baixar/cachear, não referenciar a URL temporária que expira); `statuses[]` → eventos `receipt`.

### 2.3 Webhook (core é dono do endpoint, plugin não abre rota de webhook)

- **`GET /api/webhook/whatsapp_cloud/{channel_id}`** (no core, Fase 0) — handshake:
  ler `hub.mode/hub.verify_token/hub.challenge`; comparar `verify_token` via
  `channel_registry.get_credential(channel_id, "verify_token")`; responder `hub.challenge` em texto puro
  200, senão 403 (§5.2).
- **`POST /api/webhook/whatsapp_cloud/{channel_id}`** — opcional: validar `X-Hub-Signature-256` com o
  `app_secret` do canal; chamar `registry.get(channel_id).parse_inbound(raw)` → pipeline.

### 2.4 Tratamento de segredos no MVP (sem cifragem — P15)

- **Não há módulo de cifragem.** Credenciais são gravadas em **texto puro** via
  `channel_credential_repo` (decisão P15, ⚠️ MUDANÇA). **Não** adicionar `cryptography` ao
  `requirements.txt`; **não** criar `channels/secrets.py`; **não** existe `WHATSBOT_SECRET_KEY`.
- A única proteção exigida é **mascaramento na borda da API**: ao serializar canais/credenciais em
  `GET /api/channels` (e em qualquer log), os segredos voltam como `••••1234` (espelha o que
  `/api/config` já faz com a API key do LLM). O segredo em claro só transita ao salvar (`PUT`) e quando
  o provider o usa internamente.
- **Dívida/risco (revisitar antes de produção séria):** cifrar em repouso. Caminho previsto quando for
  feito — Fernet (`cryptography`) com chave de env no Docker/Coolify, **ou** `pgcrypto` no Postgres
  (decisão global de banco permite exigir Postgres para a feature). A API do registry já centraliza o
  acesso, então a cifragem entra num único ponto sem espalhar pelo código. Registrado em "Perguntas em
  aberto" §3.

### 2.5 Tela de Canais (frontend) — primeira versão

- **`web/static/js/components/ChannelsManager.js`** — listagem de canais (cards: `display_name`,
  provider, status, `own_phone`); ações adicionar/desativar/remover.
- **Adicionar canal Cloud API** — formulário: Phone Number ID, WABA ID, Access Token (mascarado),
  Verify Token (sugerido pela UI), App Secret opcional; exibe a **URL de webhook** a colar na Meta
  (`https://<host>/api/webhook/whatsapp_cloud/{channel_id}`). **Aba de templates (P19):** **upload pelo
  painel** + botão "sincronizar" que busca os templates aprovados do WABA **sob demanda** (ao abrir a
  aba / clicar). **Sem** sync periódico em segundo plano (submissão de novos templates para aprovação
  fica fora de escopo).
- Rota SPA `/channels` registrada em `server/app.py` (lista `_SPA_PATHS`, linha 216) + handler `index`.
- **Endpoints REST core** (novo `server/routes/channels.py`):
  `GET/POST /api/channels`, `GET/PUT/DELETE /api/channels/{id}`,
  `GET /api/channels/{id}/status`, `GET /api/channels/{id}/qr` (204 se não-aplicável),
  `GET /api/channels/{id}/templates` (Cloud). Registrar em `create_app` (após `admin_routes`,
  linha 319). Permissões: admin (gancho do plano 03; por ora `auth_required`).
- **Modo escuro**: telas novas usam classes `wa-*`/`.wa-field` (regra obrigatória do CLAUDE.md).

### Critério de pronto — Fase 2
- Adicionar um canal Cloud API pela UI persiste credenciais em **texto puro** (P15 — sem cifragem no
  MVP), mas **mascaradas** em toda saída de API/log.
- O handshake `GET .../whatsapp_cloud/{id}` ecoa o `hub.challenge` quando o verify_token bate.
- Uma mensagem de teste (POST simulando payload Meta) é normalizada e responde **pelo mesmo canal**,
  dentro da janela de 24h; **mídia recebida é baixada e cacheada** em `statics/media/` (P16).
- Templates são carregáveis por **upload** e sincronizáveis **sob demanda** do WABA (P19).
- O GOWA continua funcionando inalterado (ainda como adapter core da Fase 0).
- Tokens nunca aparecem em logs nem em `/api/channels` (mascarados).

---

## Fase 3 — Capacidade (iii) subprocesso gerenciado + GOWA extraído para provider-plugin + multi-número

Objetivo: construir o serviço de subprocesso gerenciado no core e **extrair** o GOWA de
`gowa/manager.py`+`gowa/client.py` para `storages/plugins/gowa/`, ganhando multi-número. É o caso mais
difícil — feito por último.

### 3.1 (iii) Serviço de subprocesso gerenciado (core)

- **Novo módulo `runtime/subprocess_service.py`** (P26 — pacote `runtime/`) — `ManagedSubprocess`:
  - **Só Linux/Docker no MVP (P29, ⚠️ MUDANÇA).** Windows está **fora do escopo imediato** do Pro —
    nada de Job Object / `CREATE_NEW_PROCESS_GROUP`. (Reintroduzir apenas se voltar a empacotar EXE.)
  - `Popen` em **process group** (`start_new_session=True` no POSIX) para `os.killpg`/matar a árvore.
  - **die-with-parent**: Linux **`PR_SET_PDEATHSIG`** (via `preexec_fn`/`ctypes`) — defesa contra o
    `os._exit` do toggle (§3.4.4 iii). O **Job Object do Windows fica adiado** (P29) — só seria
    necessário num empacotamento EXE futuro.
  - Parada graciosa **SIGTERM → timeout → SIGKILL** (endurece o terminate→kill atual do
    `gowa/manager.stop()`).
  - **PID file + stale-kill no boot** — matar instância órfã antes de subir (resolve o conflito de
    sessão WhatsApp de forma nativa; hoje é `pkill`/`taskkill` externo).
  - **Watchdog com rate-limit** (reaproveitar a lógica de `gowa/manager._watchdog`) + **readiness
    probe** (esperar `/app/status` responder antes de declarar "pronto" — padrão pytest-xprocess).
  - Integra-se com o supervisor de tasks (1.2) para o watchdog assíncrono.
- O **core passa a usar esse serviço para o próprio GOWA** (consumido pelo plugin via `ctx`).

### 3.2 Extrair o GOWA para `storages/plugins/gowa/`

Layout:
```
gowa/                       # plugin (bundled em assets/plugin_examples/gowa/)
├── plugin.yaml             # entry: { channels: channels, lifecycle: lifecycle, routes: routes }
├── channels.py            # GOWAChannel(Channel) + CHANNEL_PROVIDERS=[GOWAChannel]
├── client.py              # GOWAClient PARAMETRIZADO por device_id (movido de gowa/client.py)
├── lifecycle.py           # setup: sobe o subprocesso via ctx (serviço iii); teardown: derruba
├── routes.py              # rotas de UI sob /api/plugins/gowa (QR por canal, etc.) — opcional
└── static/gowa.js         # tela de adicionar canal GOWA (QR por device)
```

Mudanças de código ao mover:
- **`GOWAClient`** — remover `_DEFAULT_DEVICE_NAME` fixo (client.py:12,52); receber `device_id` no
  construtor (ou por chamada). `_headers` usa o `device_id` da instância. `ensure_device()` deixa de
  pegar `devices[0]` (linhas 126-131): garante **o device daquele canal** (`POST /devices` com o
  `gowa_device_id` do canal se não existir).
- **`GOWAChannel(Channel)`** — um por canal/número; envolve um `GOWAClient(device_id=...)`. Consome o
  **serviço de subprocesso gerenciado do core** (não gerencia `Popen`/watchdog próprio).
- **Subprocesso compartilhado**: Opção A do §4 (1 processo GOWA, N devices). O `setup` do plugin sobe
  **1** `ManagedSubprocess` GOWA (na 1ª inicialização) e cada `GOWAChannel` adiciona seu device via
  `POST /devices` (**P14** — MVP só Opção A, 1 processo N devices; coluna `gowa_isolation` já no schema
  para habilitar dedicated depois). O `--webhook` aponta para o endpoint genérico; o **roteamento é por
  `body["device_id"]` do payload + path por canal** (**P13** — opção a; GOWA não dá webhook por device,
  §11.2). Confirmar nos testes que `device_id` vem em **todos** os tipos de evento (não só `message`).
- **Remover** `gowa/manager.py` e `gowa/client.py` do core e `channels/providers/gowa_channel.py` (o
  adapter temporário da Fase 0). Ajustar `main.py:48-71` (não instanciar `GOWAManager`/`GOWAClient`) e
  `server/app.py` (remover `gowa_manager`/`gowa_client` de `ServerDeps`; `group_mentions.init` passa a
  receber um client resolvido do canal). Limpar `start_gowa_task`/`qr_poll_loop`/`status_poll_loop` do
  `server/background.py` (viram parte do plugin GOWA / do supervisor). `avatar_fetch_task` precisa
  resolver o client por canal.
- **Bootstrap (P23 — bootstrap especial no upgrade):** o GOWA entra na lista de plugins bundled
  (`assets/plugin_examples/gowa/`). Para instalações **existentes** que migraram na Fase 0 (canal
  default GOWA), o `bootstrap_initial_plugins` (que só copia em pasta vazia) **não basta**. A
  migration/upgrade da Fase 3 roda um **bootstrap especial**: se existir um canal default GOWA, copia o
  plugin `gowa` de `assets/plugin_examples/gowa/` para `storages/plugins/gowa/` e o ativa
  (`enabled=1`), **preservando a sessão** WhatsApp já conectada. Documentar no release.

### 3.3 Multi-número (UI + roteamento)

- **`channel_repo`** já suporta N rows. A tela de Canais (Fase 2) ganha "Adicionar canal GOWA":
  cria o device (`gowa_device_id`), mostra **QR por device** (reusa o componente de QR atual, agora
  por canal) e faz polling de status até logar. **Captura o número após o login (P20)** e persiste em
  `channels.own_phone` (aceitar vazio até o 1º login bem-sucedido); atualiza na varredura de status.
- **Roteamento de entrada = `device_id` do payload + path por canal** (P13, §11.3): rota
  `/api/webhook/gowa/{channel_id}` **e** leitura de `body["device_id"]` para resolver o canal.
- **Roteamento de saída**: o handler responde via `registry.get(conversa.channel_id).send_text(...)`
  (já preparado na Fase 0; agora cada canal tem device próprio).
- **`gowa_isolation`** (P14): default `shared` (Opção A — única no MVP). A coluna já existe no schema
  (custo zero) para habilitar `dedicated_process` (Opção B — isolamento/IP-proxy dedicado por número,
  §11.4) **depois sem migration**. Não implementar dedicated no MVP.
- **Sinais de saúde por canal** na UI: último erro, reconexões, ban temporário (`events.TemporaryBan`
  exposto pelo GOWA como status) — supre a lacuna de observabilidade-por-device (§11.2/§11.5).

### 3.4 `config/settings.py`

- Mover constantes GOWA-específicas (porta, isolamento) para settings do plugin GOWA; manter no core
  só o que for genérico. Constantes Cloud (Graph URL base, versão) já no plugin Cloud (Fase 2).

### Critério de pronto — Fase 3
- O GOWA roda como **plugin** (`storages/plugins/gowa/`); desabilitá-lo derruba o subprocesso limpo
  (sem órfão — verificável por ausência de processo `gowa` após disable + `os._exit`).
- Uma instalação que **não** instala o plugin GOWA **não sobe** o binário (objetivo de produto).
- Dois canais GOWA (2 números) coexistem: cada um conecta por QR próprio, recebe roteado por
  `device_id`, e responde pelo número de origem.
- Instalação existente (canal default da Fase 0) continua conectada após a migração para plugin.
- `tests/test_endpoints.py` + novos testes de canais/webhook-por-provider passam (GOWA mockado).

---

## Fase 4 — Telegram e demais providers (esboço, fora do escopo de implementação)

Preparado pelo ponto de extensão (`entry.channels`) + supervisor de tasks (polling) + webhook por path.
Telegram entra como plugin webhook **ou** long-poll (`getUpdates` via `ctx.register_task`), validando
(a) e (b) de uma vez. Instagram/Messenger (webhook-only, família Meta Graph) e Email (IMAP polling)
seguem o mesmo molde. **Não implementado neste plano** — citado para garantir que nenhuma decisão das
Fases 0-3 feche a porta.

---

## Resumo de artefatos por categoria

### Migrations Alembic
- `20260618_0007_channels.py` — cria `channels` + `channel_credentials` + data migration "1 canal
  default" (Fase 0). (Idempotência `(channel_id, external_msg_id)` e `channel_id` em conversas/mensagens
  = plano 01.)

### Tabelas novas (`db/tables.py`)
- `channels`, `channel_credentials` (Fase 0).

### Repos novos (`db/repositories/`)
- `channel_repo.py`, `channel_credential_repo.py` (Fase 0).

### Pacotes/módulos core novos
- `channels/` (`base.py`, `registry.py`, `events.py`, `providers/gowa_channel.py` temporário) —
  Fases 0/2. **Sem `secrets.py`** (P15 — cifragem removida do MVP).
- `runtime/supervisor.py` (Fase 1), `runtime/subprocess_service.py` (Fase 3) — novo pacote `runtime/`
  (P26).

### Endpoints REST novos (core)
- `GET/POST /api/channels`, `GET/PUT/DELETE /api/channels/{id}`,
  `GET /api/channels/{id}/status|qr|templates` (Fase 2; `server/routes/channels.py`).
- `GET/POST /api/webhook/{provider}/{channel_id}` (Fase 0; handshake Cloud na Fase 2).

### Frontend (`web/static/js/components/`)
- `ChannelsManager.js` + rota SPA `/channels` (Fase 2); QR por device (Fase 3). Tema `wa-*`/`.wa-field`.

### Plugins (bundled em `assets/plugin_examples/`)
- `channel_test/` (Fase 1), `whatsapp_cloud/` (Fase 2), `gowa/` (Fase 3).

### Dependências novas
- pip: **nenhuma no MVP.** (`cryptography` foi **removido** — P15 elimina a cifragem do MVP; reintroduzir
  só quando a dívida de cifrar em repouso for paga.) `pyyaml` já é opcional.
- JS: nenhuma (frontend sem build step, vendorizado).

### Pontos de integração (arquivo:linha)
- `main.py:48-71` (wiring registry), `server/app.py:46-60` (ServerDeps), `:93-101` (wiring plugins),
  `:142-183` (lifespan/supervisor/lifecycle), `:207,216` (auth exempt + SPA path), `:319` (rotas
  channels), `:128` (group_mentions).
- `plugins/loader.py:166-260` (entry.channels + lifecycle), `plugins/context.py:34` (runtime ctx),
  `plugins/restart.py:78` (teardown antes do os._exit), `plugins/events.py:129` (distinção bus vs
  lifecycle aguardado).
- `gowa/manager.py:180-209` (lógica de watchdog reaproveitada no supervisor/subprocess service),
  `gowa/client.py:12,52,57,126-131` (parametrizar device_id), `server/routes/webhook.py:1065,421,1289-1480`
  (parsing extraído + roteamento por canal), `server/background.py` (4 tasks → supervisor).

---

## Dependências de outros planos

1. **Plano 01 (Inbox e Conversas)** — o modelo de 3 níveis (Contact → ContactInbox → Conversation) e a
   coluna `channel_id` em conversas/mensagens + idempotência `(channel_id, external_msg_id)` são **dele**.
   Este plano cria as tabelas `channels`/`channel_credentials` e o roteamento; o "fechamento do ciclo
   entrada→conversa→saída" depende do `channel_id` na conversa. **Coordenar a ordem das migrations**
   (a FK `conversation.channel_id → channels.id` exige `channels` já criada — esta migration 0007 deve
   vir antes da migration de conversas do plano 01).
2. **Plano 03 (RBAC/Usuários)** — gerenciar canais (criar/editar, ver tokens, conectar QR) é ação
   privilegiada (admin). Os endpoints `/api/channels/*` deste plano usam `auth_required` por ora; o
   gating por papel (admin vs atendente) entra quando o RBAC existir. O modelo "dois níveis de chave"
   da Evolution (global gerencia ciclo de vida; token por canal opera) casa com o plano 03.
3. **Plano 06 (Motor multiagente / code-in-DB)** — não bloqueia este plano, mas o padrão híbrido
   (contratos/capacidades no core, implementações em core e/ou plugin) é o mesmo; a capacidade (ii)
   supervisor de tasks pode ser reusada por jobs do motor de IA.

---

## Perguntas em aberto

> As perguntas funcionais deste plano foram **todas decididas** (ver `DECISOES.md`, P13–P31). Mantidas
> abaixo com o carimbo **✅ DECIDIDO (2026-06-19)** para preservar o rastro. Não há pergunta aberta
> remanescente neste plano — a única dívida explícita é a **cifragem em repouso** (item 3), aceita
> como risco e adiada por P15.

1. **Webhook por device no GOWA (confirmar na build empacotada).**
   - **✅ DECIDIDO (2026-06-19): opção (a) — `device_id` do payload + path por canal (P13).** O path do
     canal (`/api/webhook/gowa/{channel_id}`) é a fonte, com `body["device_id"]` resolvendo/confirmando
     o canal (combinação validada pela Evolution, §11.3). **Ação de verificação** (não bloqueia a
     decisão): confirmar empiricamente que `device_id` vem em **todos** os tipos de evento (não só
     `message`) antes de fechar a Fase 3.

2. **Suportar `dedicated_process` (Opção B) já no MVP?**
   - **✅ DECIDIDO (2026-06-19): opção (a) — só Opção A no MVP (P14).** 1 processo, N devices. A coluna
     `gowa_isolation` já entra no schema (custo zero) para habilitar `dedicated_process` depois **sem
     migration**. Processo dedicado só com demanda real de anti-ban/isolamento.

3. **Cifragem de credenciais em repouso.**
   - **✅ DECIDIDO (2026-06-19): SEM cifragem no MVP (P15, ⚠️ MUDANÇA).** Tokens/credenciais em **texto
     puro**, sem chave mestra (`WHATSBOT_SECRET_KEY` removido) e sem módulo `channels/secrets.py`.
     Única proteção: mascaramento na borda da API. **Dívida/risco aceito conscientemente — revisitar e
     cifrar antes de produção séria.** Quando for feito: Fernet (`cryptography`) com chave de env no
     Docker/Coolify **ou** `pgcrypto` no Postgres (a decisão global de banco permite exigir Postgres
     para esta feature). A API do registry já centraliza o acesso, então a cifragem entra num único
     ponto. (A questão da origem da chave em Windows está **fora de escopo** — P29: só Linux/Docker.)

4. **Mídia da Cloud API: baixar/cachear como `media_path` ou referenciar URL temporária?**
   - **✅ DECIDIDO (2026-06-19): opção (a) — baixar e cachear em `statics/media/` (P16).** Mantém o
     player do inbox idêntico ao GOWA e evita links quebrados (a URL da Meta expira). Custa o download
     síncrono no `parse_inbound`.

5. **Janela de 24h na UI (sinalização ao operador).**
   - **✅ DECIDIDO (2026-06-19): opção (a) — bloquear texto livre + oferecer template fora da janela
     (P17), mas DEPOIS que o principal funcionar.** Rastrear "último inbound" por conversa no adapter
     Cloud; quando fechada, bloquear texto livre e abrir seletor de template. Detalhe de UI alinhado com
     o plano 01 (inbox). Não é entrega da 1ª iteração da Fase 2.

6. **Idempotência de webhook por `(channel_id, external_msg_id)`.**
   - **✅ DECIDIDO (2026-06-19): opção (a) — índice único `(channel_id, external_msg_id)` (P18),
     implementado no plano 01** (tabela de mensagens). Aqui só garantimos que o `external_msg_id` é
     normalizado por todo provider.

7. **Sincronização de templates Cloud API.**
   - **✅ DECIDIDO (2026-06-19): upload pelo painel + sync SOB DEMANDA (P19).** Botão "sincronizar" puxa
     os templates aprovados do WABA quando alguém abre/busca; **sem** sync periódico em segundo plano.
     Tokens do WABA em **texto puro** (ripple P15). Submissão de novos templates para aprovação fica
     fora de escopo.

8. **Como descobrir/exibir o número real de um device GOWA de forma confiável.**
   - **✅ DECIDIDO (2026-06-19): opção (a) — capturar o número após o login e persistir em
     `channels.own_phone` (P20).** Atualizar na varredura de status; exibir na tela de Canais. Aceitar
     que pode ficar vazio até o 1º login bem-sucedido.

9. **Forma exata do contrato de export de provider e do lifecycle.**
   - **✅ DECIDIDO (2026-06-19): opção (a) — contrato SÓ declarativo via `entry.channels`/
     `entry.lifecycle` (P21).** `CHANNEL_PROVIDERS = [cls, ...]` espelha `CORE_TOOLS`; `setup/teardown`
     no módulo `entry.lifecycle`. Plugins importam a base de `channels.base`. **Sem** registro
     imperativo (`register(registry)`) no MVP.

10. **Disable de plugin sem matar o processo todo (teardown vs `os._exit`).**
    - **✅ DECIDIDO (2026-06-19): opção (a) — teardown aguardado antes do `os._exit` (P22/P25,
      restart-do-processo).** `teardown` (aguardado, com timeout ~10s — P31) **antes** do `os._exit` +
      die-with-parent `PR_SET_PDEATHSIG` como rede de segurança (P29, só Linux). Hot-unload sem restart
      (modelo Home Assistant) fica como evolução futura.

11. **Bootstrap do GOWA-plugin em instalações existentes.**
    - **✅ DECIDIDO (2026-06-19): opção (a) — bootstrap especial no upgrade (P23).** Na migration/upgrade
      da Fase 3, se existir canal default GOWA, copiar o plugin `gowa` de `assets/plugin_examples/gowa/`
      e ativá-lo (`enabled=1`), preservando a sessão. Documentar no release.

12. **API do core para o provider ler/gravar tabelas de canal (superfície e passagem).**
    - **✅ DECIDIDO (2026-06-19): opção (a) — métodos no `ChannelRegistry` passados via
      `ctx.channel_registry` (P24).** `get_channel`, `get_credential`, `set_status`, … centralizam o
      acesso e o mascaramento; o provider-plugin **não** toca `channels`/`channel_credentials` por SQL
      direto.
