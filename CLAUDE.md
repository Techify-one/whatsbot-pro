# WhatsBot

Bot de WhatsApp com IA para uso em servidor/cloud (Coolify/Docker) — **decisão de distribuição (plano 29 P1)**: o produto é server/cloud-first; o empacotamento EXE Windows ficou suspenso quando o banco virou Postgres-only (não há PG em máquina de usuário final). Os launchers dev de Windows/macOS continuam funcionando apontando para um Postgres remoto.

## Índice de documentação — leia ANTES de mexer

Este arquivo carrega a **regra**; o **porquê** (histórico, medições, o que enganava) mora nos guias abaixo. Todo ⚠️/🚫 daqui tem o caso completo no guia da área — **abra o guia antes de "consertar" o que um aviso manda não mexer**.

| Vai mexer em… | Leia antes |
|---|---|
| evento/filtro de plugin, payload do bus, media type | [docs/PLUGIN_BUS.md](docs/PLUGIN_BUS.md) + [plugins/events.py](plugins/events.py) (catálogo executável) |
| sistema de plugins, RBAC, `entry.services`, override de UI, regra core-vs-plugin | [docs/PLUGINS.md](docs/PLUGINS.md) |
| provider de canal, descriptor, dedup de conta, proxy, JID, limites de mídia | [docs/CANAIS.md](docs/CANAIS.md) |
| Messenger/Instagram, janelas de 24h/7d/IA, alertas da conta Meta | [docs/CANAIS_META.md](docs/CANAIS_META.md) |
| motor AGNO, agentes, roteamento, gate do humano, transcrição | [docs/IA.md](docs/IA.md) |
| compositor, bandeja de anexo, thread, rascunho, sidebar | [docs/UI_CONVERSA.md](docs/UI_CONVERSA.md) |
| endpoint REST, evento WebSocket, chave de API (`X-Api-Key`), fachada `/api/v1`, webhook de saída | [docs/API_REST.md](docs/API_REST.md) |
| tema, contraste, modo escuro, **largura/transbordo de coluna flex (`min-w-0`)** | [docs/FRONTEND.md](docs/FRONTEND.md) |
| deploy, persistência de disco, IP atrás de proxy, echo do provider | [docs/OPERACAO.md](docs/OPERACAO.md) · [docs/DEPLOY_COOLIFY.md](docs/DEPLOY_COOLIFY.md) |
| rodar ou escrever teste | [docs/TESTES.md](docs/TESTES.md) |
| auditoria de plugin · versão da API de plugins · modelo de dados | [docs/PLUGINS_AUDITAVEIS.md](docs/PLUGINS_AUDITAVEIS.md) · [docs/PLUGIN_API_CHANGELOG.md](docs/PLUGIN_API_CHANGELOG.md) · [docs/MODELO_DE_DADOS.md](docs/MODELO_DE_DADOS.md) |

⚠️ **Plano executado se documenta no guia, não aqui.** Este arquivo entra em TODA requisição de TODA sessão; ele passou o teto de 150k chars do Claude Code crescendo ~2.500 chars/dia, um parágrafo por plano (plano 139). Orçamento: até ~2 linhas aqui — a regra e o aviso —, o resto no guia da área. [tests/contracts/test_docs_hygiene.py](tests/contracts/test_docs_hygiene.py) trava o tamanho e prova que nada sumiu.

## Stack

- **Python 3.11+** — linguagem principal
- **SQLAlchemy 2.0 Core + Alembic** — camada de dados (Core, sem ORM declarativo)
- **PostgreSQL** — banco único via `psycopg[binary]` (plano 29 Eixo C — Postgres-only). A env `DATABASE_URL` é obrigatória; sem ela o boot falha com erro acionável. SQLite foi removido
- **GOWA** (go-whatsapp-web-multidevice v8.11.0) — bridge WhatsApp via REST, roda como subprocess
- **Proxy LLM da Techify** (`https://llm.techify.one/api/v1`) — provider de LLM, API **compatível com OpenRouter/OpenAI**. Substituiu o OpenRouter direto: a chave é provisionada pelo wizard de 1ª execução e o crédito/recarga é gerido pela Techify. O base URL é configurável via env `LLM_API_BASE_URL`. A chave continua sendo persistida na config key `openrouter_api_key` (nome legado mantido por compatibilidade)
- **AGNO** (`agno` 2.x) — framework de agentes usado como **motor de LLM** do agente. O loop de raciocínio + tool calling roda via `agno.agent.Agent`, apontado ao proxy Techify pelo model `OpenAILike`. Encapsulado em [agent/agno_engine.py](agent/agno_engine.py); o `AgentHandler` delega a ele preservando todos os hooks de plugin (filters/events), usage e execution tracking. Transcrição de áudio/descrição de imagem continuam em chamadas diretas ao cliente OpenAI (não são agênticas)
- **FastAPI + uvicorn** — backend web (REST API + WebSocket)
- **Preact + HTM + Tailwind CSS** — frontend web (sem build step, vendorizado local)
- **PyInstaller** — empacotamento como EXE (suspenso pós-Postgres-only — ver decisão de distribuição no topo; o tooling continua no repo)

## Arquitetura

```
main.py              → entry point, inicia uvicorn + abre browser
server/app.py        → FastAPI app (endpoints REST, WebSocket, webhook, background tasks)
gowa/manager.py      → lifecycle do subprocess GOWA (start/stop/watchdog)
gowa/client.py       → HTTP client para REST API do GOWA (localhost:3000)
agent/handler.py     → orquestra o processamento de mensagens (system prompt, filters/events, usage, save); delega o loop de LLM ao motor AGNO
agent/agno_engine.py → motor AGNO: monta OpenAILike + Agent único, envolve cada tool em agno Function (filters/events preservados), extrai reply/usage
agent/memory.py      → ContactMemory e TagRegistry (leitura/escrita no banco via repos)
agent/group_mentions.py → resolução de @menções em grupos (número ↔ nome, lista de membros, @todos)
agent/tools/         → tools core do LLM (uma tool por arquivo, agregadas em CORE_TOOLS)
config/settings.py   → load/save config + constantes do provider/Techify (LLM_API_BASE_URL, TECHIFY_*)
server/avatars.py    → cache de fotos de perfil em disco (statics/avatars/<phone>.jpg) + broadcast avatar_updated
server/balance_monitor.py → consulta saldo de crédito do proxy (/credits) e emite low_balance via WS
db/                  → módulo de banco de dados (SQLAlchemy 2.0 Core)
  engine.py          → factory do Engine; URL exclusivamente da env DATABASE_URL (fail-fast Postgres-only)
  tables.py          → MetaData + 20 Table objects (Core, sem mapper/Session): 13 core + 7 ai_* (motor AGNO)
  upsert.py          → helper de INSERT ... ON CONFLICT (dialect postgresql)
  connection.py      → init_db(): cria engine + roda Alembic upgrade
  pg_maintenance.py  → repair_postgres_sequences (re-ancora sequences em MAX(pk))
  alembic/           → migrations Alembic (env.py + versions/)
  repositories/      → data access layer (um arquivo por domínio)
    config_repo.py   → get_all(), get(), set(), set_many(), delete_prefix()
    contact_repo.py  → get_or_create(), update(), list_contacts(), get_full_contact()
    message_repo.py  → add(), get_all(), get_context(), get_last(), delete_all()
    usage_repo.py    → add(), global_summary(), by_contact(), detail()
    tag_repo.py      → get_all(), create(), update(), delete(), set_contact_tags()
    plugin_repo.py   → list_all(), upsert(), set_enabled(), applied_migrations()
plugins/             → sistema de plugins (core, não confundir com storages/plugins)
  loader.py          → PluginRegistry, descoberta + importlib + bootstrap
  manifest.py        → parser plugin.yaml + validação semver
  migrator.py        → runner SQL com prefixo plugin_<id>_ obrigatório
  context.py         → ToolContext, PromptContext (passados aos plugins)
  restart.py         → schedule_restart() — touch sentinela + os._exit
assets/              → recursos e bancada versionada
  plugin_examples/   → fontes de plugin para diff/teste/build; só GOWA é semeado no boot
storages/plugins/    → user-writable, ignorado por .gitignore (preservado em updates)
web/index.html       → entry point do frontend (HTML + import map)
web/static/js/       → componentes Preact + HTM (sem build step)
web/static/vendor/   → libs JS vendorizadas (preact, htm, tailwind)
bin/gowa.exe         → binário GOWA pré-compilado (não editar)
```

## Comandos

Escolha o launcher pelo ambiente onde está rodando:

| Ambiente | Comando | Modo | Hot-reload | Quando usar |
|---|---|---|---|---|
| Linux dev nativo | `./linux_start.sh` | Python local + uvicorn `--reload` | Sim (core + plugins) | Dia-a-dia de desenvolvimento — edita `.py` e o worker reinicia sozinho |
| macOS dev nativo | `macos_start.command` | Python local + uvicorn `--reload` | Sim (core + plugins) | Dia-a-dia em macOS; baixa Python e o binário GOWA automaticamente na 1ª execução |
| Windows dev nativo | `windows_start.bat` | Python local + uvicorn `--reload` | Sim (core + plugins) | Dia-a-dia em Windows; baixa Python automaticamente na 1ª execução |
| Linux/macOS prod-like | `./docker_start.sh` | `docker compose up --build -d` | Não | Validar o build Docker localmente antes de push pro Coolify |
| Coolify / servidor remoto | `git push` → deploy automático | Container do [Dockerfile](Dockerfile), `CMD python main.py` | Não | Produção — Coolify clona o repo e roda o Dockerfile |

Parar o servidor:
- Linux dev: `Ctrl+C` no terminal do `linux_start.sh` (ou `pkill -f "uvicorn server.dev"` se desanexado)
- macOS dev: `Ctrl+C` na janela do `macos_start.command` (ou rode `macos_stop.command`)
- Windows dev: `windows_stop.bat`
- Docker local: `docker compose down`

Setup inicial (1ª vez no Linux):

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
# criar o .env na raiz com DATABASE_URL=postgresql+psycopg://user:senha@host:5432/whatsbot
./linux_start.sh
```

O `windows_start.bat` e o `macos_start.command` fazem o setup sozinhos (baixam Python 3.12, criam a venv, instalam as deps; o de macOS também baixa o binário GOWA).

## Banco de dados

A camada de dados usa **SQLAlchemy 2.0 Core** (sem ORM declarativo). Cada tabela é um `Table` em [db/tables.py](db/tables.py) e os repositórios constroem statements via `select()/insert()/update()/delete()`. Repos rodam síncronos e são chamados das rotas via `asyncio.to_thread`.

### URL do banco (Postgres-only — plano 29)

O banco é **exclusivamente PostgreSQL** e a URL vem **exclusivamente da env `DATABASE_URL`** (`postgresql+psycopg://user:senha@host:5432/whatsbot`). Sem a env, ou com URL de outro dialeto, `resolve_database_url()` levanta `RuntimeError` com mensagem acionável no boot — não existe mais fallback SQLite nem o override `storages/database.json` (a tela Settings → Banco e o endpoint de migração SQLite→Postgres foram removidos).

- **Dev local**: o `.env` na raiz (gitignored) define `DATABASE_URL`; `linux_start.sh` carrega automaticamente.
- **Docker/Coolify**: setar `DATABASE_URL` nas envs do container.
- O engine usa `pool_pre_ping=True` (sobrevive a quedas idle) e `prepare_threshold=None` no psycopg (compatível com PgBouncer em transaction mode — Neon/Supabase).
- `POST /api/admin/repair-sequences` re-ancora as sequences em `MAX(pk)` (útil após import manual de dados).

### Tabelas

20 `Table` objects em [db/tables.py](db/tables.py) — 13 core + 7 `ai_*` (motor AGNO). As notas de coluna completas (em especial as de `messages`) estão em [docs/MODELO_DE_DADOS.md](docs/MODELO_DE_DADOS.md). A análise de negócio do banco (`docs/analises/`) fica **fora do versionamento** — descreve regras internas com detalhe demais para um repositório público, e existe só no checkout local.

| Tabela | Descrição |
|--------|-----------|
| `config` | Configurações do app (key-value, JSON-encoded). Config de plugin usa prefixo `plugin.<id>.` |
| `contacts` | Contatos/grupos (phone, name, flags). Inclui `is_pinned`, `has_unread_mention` e `contact_type` (tipo herdado do canal de origem) |
| `observations` | Notas/observações por contato |
| `messages` | Histórico completo (role, content, ts, media, `status`, `msg_id`), mais `revoked`, `reactions`, `reply_to_msg_id`, `edited_ts` e `media_caption`. ⚠️ **`content` é COMPOSTO** — a descrição de imagem / extração de documento o reescreve, então a legenda VERBATIM do cliente vive em `media_caption` (NULL = mídia sem legenda ou linha legada). Roles especiais painel-only (não vão ao WhatsApp, renderizam como card centralizado): `tool_call`, `system_notice`, `transcription`, `private_note`, `error`, `conversation_event` |
| `usage` | Registros de uso da API (tokens, custo, modelo) |
| `tags` / `contact_tags` | Tags globais e a relação N:N contato ↔ tag |
| `unread_msg_ids` | IDs de mensagens não lidas por contato |
| `executions` / `execution_steps` | Tracking de execução (webhook → resposta) e seus passos; inclui `agent_key`, `total_tokens`, `total_cost_usd` |
| `ai_agents` / `ai_variables` / `ai_tools` | Motor AGNO config-in-DB; `ai_tools.kind` separa as três procedências (`builtin`/`plugin`/`code`) e `ai_tools.plugin_id` guarda o dono quando a tool vem de plugin. O **prompt é inline** em `ai_agents.prompt` (não há mais template compartilhado); `ai_tools` só roda com `ai_tools_code_enabled=True` (kill-switch, default OFF) |
| `ai_prompts` / `ai_prompts_history` | **Legado** — não participam mais da resolução do agente; mantidas por compat |
| `ai_agents_history` / `ai_tools_history` | Snapshot por versão (Histórico/Reverter); o do agente inclui o `prompt` inline |
| `plugins` / `plugin_migrations` | Plugins descobertos no filesystem e versões de SQL já aplicadas |
| `plugin_<id>_*` | Tabelas criadas por plugins via migrations (prefixo obrigatório) |
| `tool_overrides` | Override por-tool (enabled, description, display_label), criado para toda tool registrada |

### Padrão de acesso

Repos usam o padrão baseado em `Table` objects:

```python
from sqlalchemy import select
from db.engine import get_engine
from db.tables import contacts

def get_by_phone(phone: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(contacts).where(contacts.c.phone == phone)
        ).mappings().first()
    return dict(row) if row else None
```

Regras:

- Leitura: `with get_engine().connect() as conn:` (sem transação implícita).
- Escrita: `with get_engine().begin() as conn:` (auto-commit no exit, rollback em exceção).
- UPSERT: usar `db.upsert.upsert()` / `db.upsert.upsert_ignore()` (`INSERT ... ON CONFLICT` do dialect postgresql).
- Nunca usar `?` ou `%s` direto — bind params nomeados (`:phone`) via `sqlalchemy.text()` ou expressões Core.
- Migrations: Alembic ([db/alembic/versions](db/alembic/versions)), sem batch-mode (Postgres tem `ALTER TABLE` completo). Para um schema change, rode `alembic revision --autogenerate -m "msg"` e revise. `init_db()` aplica `alembic upgrade head` no boot.

`db.connection.get_db()` ainda existe como shim deprecated retornando `engine.raw_connection()`, mas é apenas para plugins de terceiros não migrados. Código novo (core ou plugin oficial) usa `get_engine()`.

## Fluxo de mensagens (webhook)

Mensagens recebidas no WhatsApp são entregues em tempo real via webhook do GOWA:

1. GOWA inicia com `--webhook http://127.0.0.1:{web_port}/api/webhook`
2. Mensagem chega → GOWA faz POST em `/api/webhook` com payload contendo `body`, `from`, `id`, `is_from_me`
3. Webhook acumula mensagens do mesmo contato por `message_batch_delay` segundos (padrão: 3s) — se o contato enviar várias mensagens em sequência, são juntadas em uma só
4. Após o delay, `_process_batch()` junta os textos com `\n` e chama `agent_handler.process_message()`
5. O AgentHandler faz a chamada ao LLM com tool calling — se o LLM detectar dados pessoais (nome, email, profissão, empresa), chama `save_contact_info` automaticamente
6. Resposta é enviada via `gowa_client.send_message()`

**NÃO usa polling** — o auto-reply por polling foi removido. Toda recepção de mensagens é via webhook.

## Canais → [docs/CANAIS.md](docs/CANAIS.md)

Canais são **plugins de 1ª classe**: cada provider se autodescreve por `provider_descriptor()` ([channels/base.py](channels/base.py)) e as superfícies do core — formulário, pós-criação, chips/filtros, card, mascaramento de credencial, catálogo do cliente — **não o conhecem por nome**. Adicionar provider = shipar um plugin; a UI não muda. Use `/new-channel`, que gera um provider correto por construção.

- ⚠️ **Regra de ouro: não existe `if provider ==` no core.** O provider **declara** (descriptor, `ChannelCapabilities`, `MediaLimits`, `TemplateSpec`, `AccountIdentity`, `contact_type()`), o core **avalia**.
- **Dedup de conta** (plano 32): dois canais do MESMO provider não podem apontar para a mesma conta — bloqueio na origem (**409**), com o índice único parcial `ux_channels_account_identity` como cinto de segurança. Mesma conta em providers **diferentes** NÃO é duplicata; arquivados/desabilitados não contam.
- **Só `gowa` vem auto-instalado** (`BUNDLED_AUTO_INSTALL`); telegram/whatsapp_cloud/facebook_messenger/instagram/website entram por `Importar (.zip)` a partir do repositório `whatsbot-pro-plugins`.
- **Mascaramento de credencial** é derivado do descriptor: sai em claro só o que o provider declarou `type: "text"`, e mesmo assim uma chave cujo nome case `/(token|secret|password|senha|key)/i` é mascarada.
- **IA por canal** (plano 21): overrides em `channels.config["ai"]` ([channels/ai_settings.py](channels/ai_settings.py), cache 30s). O gate é **global `auto_reply` → `ai_enabled` do canal → `ai_active` da conversa**. ⚠️ **Transcrição NÃO depende da IA do canal** — canal com a IA desligada continua descrevendo/transcrevendo, e `image_transcription_mode` **não é seedada** no config global (seedar faria uma instalação que desligou a descrição voltar a descrever).
- **Limites de mídia**: anexo incompatível é **bloqueado no compositor com popup**, não vira bolha "falhou". Os números moram no plugin (`_MEDIA_LIMITS`), o core só avalia ([channels/media_limits.py](channels/media_limits.py)).
- ⚠️ **O `ts` do provedor é COAGIDO, nunca repassado cru** (plano 141): o GOWA manda `timestamp` como string RFC 3339, e `messages.ts` é `double precision` — o valor cru derrubava o INSERT e a exceção era engolida **depois** de a fila do batch já ter sido consumida, **destruindo a mensagem do cliente em silêncio** (6 dias, ~zero inbound GOWA em produção). Três camadas: `_epoch()` no parser, `InboundEvent.__post_init__` no contrato e o guard de tipo em `message_repo.add`. ⚠️ **`ts = ts or time.time()` NÃO é guard de tipo** — string não-vazia é truthy e atravessa; e string ISO **naive** tem de ser lida como UTC (`.timestamp()` de naive assume hora local ⇒ −3h em BRT).
- **Tipo de contato**: `contacts.contact_type` é gravado **só no INSERT**, a partir de `Channel.contact_type()`; contato já existente não é re-tipado.
- ⚠️ **Campo `type: "secret"` de canal bloqueia o autofill do navegador** (plano 104) com `autocomplete="new-password"` — o `off` é **IGNORADO** pelo Chrome em campo de senha, não "limpe" isso achando que é resquício. Sem ele o gerenciador injeta a senha do painel e o número para.
- ⚠️ **Proxy de saída** (plano 52) põe o canal num **processo GOWA dedicado** e **exige re-parear por QR** nas duas transições (ligar e desligar). `WHATSAPP_PROXY` vai por **env, nunca argv** (o cmd é visível em `ps`). Recomende IP fixo e dedicado — nunca endpoint rotativo.

### Filtro de tipos de JID (canal GOWA)

O tipo de um chat é o **sufixo do JID** (depois do `@`), não o número — o prefixo `120363…` é compartilhado por grupo, canal e comunidade. [channels/jid.py](channels/jid.py) (`classify_jid`) mapeia para `person` (`@s.whatsapp.net`), `person_lid` (`@lid`), `group` (`@g.us`), `newsletter`, `broadcast`, `bot`, `unknown`. O webhook descarta o tipo não permitido **antes de materializar qualquer contato**; `unknown` nunca é bloqueado.

⚠️ **São DOIS defaults diferentes, em arquivos diferentes** (plano 103) — não "conserte" um achando que é o outro:

| | Default de **CRIAÇÃO** | Fallback de **RUNTIME** |
|---|---|---|
| Constante | `GOWA_DEFAULT_JID_TYPES` — [channels/providers/gowa_channel.py:63](channels/providers/gowa_channel.py#L63) | `DEFAULT_ALLOWED_JID_TYPES` — [channels/jid.py:38](channels/jid.py#L38) |
| Valor | `person` + `person_lid` (**sem `group`**) | `person` + `person_lid` + `group` |
| Quando vale | semeia o formulário de um canal **novo** (via `config_fields[].default` do descriptor) | quando o canal **não tem** a chave salva (ou salvou lixo) |
| Alcance | só canal criado dali pra frente | **todo canal legado** sem a chave |

Canal GOWA novo nasce, portanto, **sem grupo marcado** — mexer no fallback de runtime seria **retroativo** e calaria grupos em canais antigos. A opção fica a um clique no `JidTypePicker` e vale **apenas para canais GOWA**. Incidente e detalhes: [docs/CANAIS.md](docs/CANAIS.md).

## Canais Meta → [docs/CANAIS_META.md](docs/CANAIS_META.md)

Messenger, Instagram e WhatsApp Cloud. Cada plugin Meta carrega a **própria cópia** da base `MetaGraphChannel` — não há base compartilhada no core (dois canais Meta, duas cópias: preço do zip autossuficiente). ⚠️ Messenger/Instagram caminham `entry[].messaging[]`; o WhatsApp Cloud caminha `entry[].changes[].value` e sobe mídia em `/media`. Não confundir.

- **Assinatura `X-Hub-Signature-256`**: o seam seguro é `verify_inbound_signature_result(...) -> (accepted, authenticated)`, executado **antes** do `filter.webhook.payload`, sobre os **mesmos bytes** do corpo (re-serializar quebra o HMAC). Veredito negativo responde **200 `{"status":"bad_signature"}`** — um 4xx faria a Meta re-tentar em loop. Só o WhatsApp Cloud expõe `authenticated=True`.
- ⚠️ **O Instagram diverge do Messenger no TRANSPORTE, e a divergência é declarada por flag de classe** (`auth_in_header`, `send_appsecret_proof=False`, `default_messaging_type=None`) — nunca `if provider ==`. **"Tem `app_secret`" NÃO é proxy para "manda o proof"**: o segredo continua existindo para validar a assinatura do webhook.
- ⚠️ **São TRÊS janelas, não duas — e a da IA fecha primeiro**: `session_open` (envio; 24h, ou 7 dias com `human_agent_tag` e **só para o atendente**), `human_window_hours` e `ai_window_hours` (24h — é o que o painel lê para parar de oferecer os toggles da nota privada). `session_open` e `ai_window_open` **divergem de propósito** no 2º–7º dia; derivar uma da outra reintroduz o bug.
- ⚠️ **`human_window_hours` é PROPERTY, não valor de `__init__`** — devolve `0` com o toggle desligado e `168` com ele ligado. Declará-la fixa em `24*7` faz o core **liberar** envio fora das 24h com o toggle desligado. **Não "conserte" pondo `24*7` no `__init__`.**
- ⚠️ **Quem cala a IA fora das 24h é o `filters.py` do plugin** (`filter.llm.messages` → `None`, abortando o turno antes do LLM), não o `send_text` — que recebe o mesmo argumento venha do atendente ou da IA. Exige `entry.filters` no manifesto; sem essa linha o módulo nunca é importado e a falha é **silenciosa**.
- **Mídia por URL pública**: a Send API **busca** o arquivo numa URL (`public_base_url` + `statics/`). O tipo do anexo sai do **MIME real**, não do `kind` pedido pelo core.
- **Refresh de token**: o core não agenda nada; o plugin registra `ctx.spawn_task` no `lifecycle.setup`. O único consumidor é o `instagram` (token de 60 dias, renovado aos ~45) — sem `entry.lifecycle` no manifest o token morre calado.
- **Alertas da conta Meta** (plano 84): motor 100% no plugin sobre `filter.webhook.payload`, com agregação e cooldown. **Fail-closed** sem procedência autenticada — não há fallback "canal único" numa rota pública.

## IA, agentes e transcrição → [docs/IA.md](docs/IA.md)

O loop de raciocínio + tool calling roda no **AGNO** ([agent/agno_engine.py](agent/agno_engine.py)), **sempre um `Agent` único** por mensagem. O `AgentHandler` continua dono de tudo em volta (system prompt + `filter.system_prompt`, histórico + `filter.llm.messages`, tools + `filter.llm.tools`, eventos `llm.before`/`llm.after`, usage, `track_step`, save, `split_messages`) e só delega o miolo a `agno_engine.run_async`/`run_sync`.

- **Stateless por requisição** — um `Agent` novo por mensagem, para os closures de tool não cruzarem contatos concorrentes.
- **WhatsBot é dono do contexto**: `build_context=False`, `add_history_to_context=False`, sem `db`. O prompt já filtrado vira `system_message`; o histórico já filtrado vira `input`.
- ⚠️ **Reply**: `_extract_reply` pega a ÚLTIMA mensagem `assistant` sem tool calls — senão o AGNO concatena um "chatter" pré-tool na resposta final, o que quebra `split_messages`.
- **Transcrição de áudio / descrição de imagem** são chamadas diretas ao cliente OpenAI (não são agênticas).
- **Provider de LLM**: proxy Techify (`https://llm.techify.one/api/v1`), API compatível OpenRouter/OpenAI, base URL na env `LLM_API_BASE_URL`. A chave é provisionada pelo wizard de 1ª execução (`/wizard`) e persiste na config key `openrouter_api_key` (nome legado). O destino do provisionamento é o par **número + frase**, resolvido CAMPO A CAMPO (`/service_number` → env → literal em [config/settings.py](config/settings.py) → os seams `filter.provisioning.number`/`.message`). ⚠️ A frase é o **gatilho** que o destino reconhece por casamento EXATO: trocar o número sem trocar a frase entrega um texto que o outro lado ignora **em silêncio** — é por isso que os dois seams existem em par, e por isso o literal do código foi restaurado na API 1.8.0 (sem ele, o endpoint fora do ar para o provisionamento de todo cliente novo). O monitor de saldo emite o WS `low_balance` abaixo do threshold.

**Agente padrão / fallback unificado**: a marcação "Padrão para novas conversas" (`ai_agents.is_default`) é TAMBÉM o fallback de runtime — `agent_repo.get_default()` resolve `is_default` → chave literal `default` (piso legado) → `None`. ⚠️ Guards de exclusão/desativação são **semânticos** (`agent_repo.get_fallback_key()`), nunca por nome. Conversa que nasce `ai_active=0` **não** carimba agente (fica de fato "não atribuída").

**Routing hub-and-spoke** (plano 29): um único roteador (`is_router`, enforced por índice único parcial), spokes devolvem ao roteador com motivo, **só o roteador tem `transfer_to_human`**. Revisita é permitida; barra só `A→A` imediato. Profundidade em `ai_max_route_depth` (default 5). `run_with_routing` ([ai_engine/routing.py](ai_engine/routing.py)) é **puro** (sem DB).

⚠️ **Humano no comando cala a IA, e a ORDEM importa** (plano 96): o veredito (`MessagingService.ai_may_speak`) é reconsultado **na hora de falar**, não só antes do LLM — antes disso a resposta indevida saía de 1,6s a 75s depois do clique. Toda tomada humana passa por `abort_ai_cycle`, que incrementa `state.ai_abort_epochs` **antes de qualquer outra coisa**. Fail-open em erro. ⚠️ **Desatribuir (`assignee_user_id=None`) NÃO religa a IA** — soltar uma conversa não pode devolvê-la ao bot sem ninguém pedir. Atendente digitando **segura**, não cancela.

⚠️ **A IA pode se despedir ao transferir** (plano 122): `transfer_to_human` fecha o gate DENTRO do turno, e o guard descartava a despedida que aquele mesmo turno acabou de escrever — foram **226 transferências mudas** em produção. O perdão é o kwarg `allow_self_handoff`, derivado pelo call site do próprio turno. **A época vem PRIMEIRO e o perdão nunca a alcança** — inverter as duas linhas devolve o bug do plano 96 em silêncio. O card "🤖 A IA assumiu a conversa" fica no predicado **ESTRITO** (não recebe o perdão): turno que terminou em transferência não é takeover.

**Filtro de histórico por regex** (plano 43): lista-negra GLOBAL em `ai_history_exclude_patterns` (default `[]`), cada linha testada como `f"{role}\t{content}"` com `re.search`. [agent/history_filter.py](agent/history_filter.py) é **fail-open** em todo nível. `message_repo.get_context(..., exclude=...)` faz over-fetch (cap 200) — cortar linhas **não encolhe** a janela abaixo de `max_context_messages`.

## Memória por contato

Cada contato é armazenado na tabela `contacts` com campos normalizados:

- **Info** (name, email, profession, company, address) — colunas diretas na tabela `contacts`
- **Observações** — tabela `observations` (uma linha por observação)
- **Mensagens** — tabela `messages` com colunas `role`, `content`, `ts`, `media_type`, `media_path`, `status`, `msg_id`
- **Usage** — tabela `usage` com tokens, custo e modelo por chamada
- **Tags** — relação N:N via `contact_tags`

`ContactMemory` em `agent/memory.py` é o wrapper que encapsula o acesso via repos. Mensagens são lazy-loaded do DB (não mantidas em memória). Apenas as últimas N (configurável) são enviadas ao LLM.

Info é salva automaticamente via tool calling do LLM e injetada no system prompt. Histórico persiste entre reinícios do app.

## Painel de conversa → [docs/UI_CONVERSA.md](docs/UI_CONVERSA.md)

- **Avisos de sistema no chat** (plano 12): eventos de ciclo de vida do atendimento viram card centralizado painel-only, role **`conversation_event`**, emitido por `emit_conversation_notice` ([server/system_notices.py](server/system_notices.py)). Gate GLOBAL por grupo (4 chaves de config, default ON) — grupo desligado **não gera** o aviso. Excluído do contexto do LLM, do preview da sidebar e das não-lidas.
- **Rascunho por conversa**: pessoal e por-dispositivo ([web/static/js/services/drafts.js](web/static/js/services/drafts.js), `localStorage` namespaceado pelo usuário logado). ⚠️ **A conversa ABERTA é a exceção de tudo** — enquanto está na tela não vira "Rascunho: …" nem muda de lugar na lista.
- **Bandeja de anexo** (plano 124): a fila de mídia é uma **faixa acima do `<form>`** e o compositor **nunca desmonta** (só a gravação de áudio o substitui). O texto do compositor **é a legenda**, vai num item só — o **ÚLTIMO que a aceita** (`captionTargetIndex`), e áudio é **pulado, não bloqueia**. A decisão do gesto é PURA (`submitPlan`: `noop`/`text`/`media`/`text_then_media`/`template`) e botão e tecla Enter consomem a MESMA função.
  - ⚠️ **`setPendingAudio` SUBSTITUI a fila** — só é seguro porque o microfone some do compositor quando há algo pendente. Mexer nessa condição reintroduz perda silenciosa de anexos.
  - ⚠️ **A bolha otimista de mídia adota o `msg_id` do ACK** — sem isso ela não tem identidade nenhuma e a reconciliação cai na heurística de 30s, produzindo uma segunda bolha que só some no F5.
- **Janela ancorada** (plano 99): a thread pode ficar ancorada no passado (salto da busca global, citação antiga, deep-link `?message=`, busca na conversa, "ir para data"). ⚠️ **O cursor real é o composto `(ts, id)`** — nunca compare só `id <`/`id >` nem use `min/max(id)` no cliente. Qualquer âncora força `mark_read=False` **no servidor**. ⚠️ **Com a janela ancorada um `new_message` NÃO é anexado** (criaria buraco no histórico): vira o contador "N novas" do botão "Voltar ao fim" — a exceção ao contrato append-only do plano 28.
- **Player de áudio** (plano 138): a barra é um **scrubber por Pointer Events** (`pointerdown/move/up` + `setPointerCapture`), com a aritmética no módulo puro [audioScrub.js](web/static/js/services/audioScrub.js) (`node --test`). Nasceu com só um `onClick` numa faixa de **4px** e por isso **nunca** teve arraste: no gesto o `click` cai no ancestral comum, que não tem handler. ⚠️ **`touch-action:none` é obrigatório** (o móvel lê o arraste como rolagem e rouba o gesto) e **não pode existir `onClick` de seek** junto com o `pointerdown` — buscaria duas vezes. ⚠️ **`scrubRatio` compara com `null`, nunca por truthiness** (arrastar ao início é `0`, falsy) e durante o arraste o `timeupdate` **não** manda na posição exibida.
- **Rótulo do remetente** (plano 143): quem decide é `isOperatorMessage` ([messageView.js](web/static/js/services/messageView.js)), **nunca** `status === 'operator'` — a falha de envio sobrescreve `operator`→`failed` e fazia toda mensagem manual falhada assinar "IA". ⚠️ O predicado exige a marca de autoria (`sent_by_name`/`sent_by_user_id`): sem ela a resposta da IA que falha passaria a assinar "Manual".
- **Quem falou no grupo** (autor da bolha): não há coluna de remetente — o autor viaja como o prefixo `"[Fulano]: "` dentro do próprio `content`, carimbado no inbound e extraído por `stripGroupPrefix`. ⚠️ **Ele tem de ser o PRIMEIRO elemento do `content`**: a imagem era a única mídia sem placeholder (foto sem legenda ⇒ `content=''` ⇒ a bolha assinava com o **nome do grupo**) e a única com junção prefix-first na descrição da IA (o autor ia para a 2ª linha e a bolha assinava **"Descrição da imagem"**). Quem lê o `content` cru desconta o carimbo antes de decidir o que é legenda.
- **Digitação entre atendentes**: a rota de presença reemite `operator_typing` no WS (heartbeat de 10s, auto-limpeza em 15s no cliente), para dois atendentes não responderem por cima um do outro.

## Fotos de perfil (avatars)

[server/avatars.py](server/avatars.py) cacheia as fotos de perfil em disco em `statics/avatars/<phone>.jpg` (servidas pelo mount estático). Como o WhatsApp não emite evento de "foto mudou", a atualização é por re-fetch do GOWA (ao abrir a conversa e numa varredura periódica de fundo — `AVATAR_REFRESH_INTERVAL = 1800s` em [server/background.py](server/background.py)), sobrescrevendo o arquivo só quando os bytes diferem. O frontend faz cache-bust pelo mtime (`avatar_v`); uma mudança dispara o WS `avatar_updated` `{phone, v}` pra atualizar ao vivo sem reload.

## @menções em grupos

[agent/group_mentions.py](agent/group_mentions.py) é o serviço central que conhece os participantes de um grupo e converte menções entre o formato de fio do WhatsApp (`@<número>`) e nomes humanos:

- **Entrada**: `resolve_incoming()` troca `@<dígitos>` numa mensagem recebida por `@<Nome>` (o painel/LLM veem nomes, não números).
- **Saída**: `resolve_outgoing()` transforma `@Nome` / `@todos` (escritos pelo operador ou pela IA) em menção real — `@<número>` inline no texto + a lista `mentions` que o `/send/message` do GOWA aceita. `@todos`/`@geral`/`@all`/etc. viram `@everyone`.

Nomes não vêm do GOWA (`DisplayName` volta vazio): são resolvidos de contatos salvos → pushName capturado de mensagens recebidas → catálogo do device (`/user/my/contacts`) → `/user/info` (cap de 20 lookups por chamada). Participantes são indexados por dígitos do phone **e** do `lid`. Cache de membros por grupo (TTL 300s), invalidado em mudança de roster (join/leave/promote/demote, via webhook `group.participants_changed`). O serviço é inicializado em `create_app` (`group_mentions.init(gowa_client)`) e a identidade do bot é registrada via `set_bot_identity`. A config `group_reply_mode` (default `mention_only`) controla quando a IA responde em grupos.

## API REST e WebSocket → [docs/API_REST.md](docs/API_REST.md)

As rotas vivem em `server/routes/`; o índice completo (≈50 endpoints) e o catálogo de eventos WebSocket estão no guia.

- Formato de resposta REST: `{"ok": bool, "data": ..., "error": ...}`. Eventos WS: `{"event": "...", "data": {...}}`.
- Endpoints de plugin ficam sob `/api/plugins/<id>/...`; o prefixo `/public/` é isento de autenticação.
- 🚫 Nenhuma tela (core ou plugin) abre `new WebSocket('/ws')` na mão — o transporte é o barramento único e autenticado (`subscribe` do [wsBus.js](web/static/js/services/wsBus.js)). Ver [docs/PLUGINS.md](docs/PLUGINS.md).
- `POST /api/admin/repair-sequences` re-ancora as sequences do Postgres em `MAX(pk)` (recovery pós-import manual).

### Chave por usuário (`X-Api-Key`), fachada `/api/v1` e webhooks de saída

Integração externa entra por `X-Api-Key: wsk_live_<prefix>.<secret>`. **A chave é só um CRACHÁ novo que resolve para o mesmo `request.state.user` que uma sessão resolve** — feito isso no middleware, RBAC, auditoria, escopo por inbox e o gating de rota de plugin funcionam sem alteração ([server/authz.py](server/authz.py) não foi tocado). O detalhe todo (guardrails de emissão, DTO da v1, `MessagingService.send_text`, webhooks de saída) está no guia.

- ⚠️ **O separador é `.` e o prefixo é HEXADECIMAL** — `secrets.token_urlsafe` usa base64url, que inclui `_`; enquanto `_` separava os campos, ~1 em 3 chaves nascia "malformada" e era recusada aleatoriamente.
- ⚠️ **Sem escopo POR CHAVE**: ela herda TODAS as permissões do dono. O controle é usuário dedicado por integração (`custom_permissions=1` + membresia de inbox, que já vira o escopo de dados). Por isso **emitir no nome de outro exige `users.manage`** (403) e chave de `admin` exige `confirm: true` (409) — sem o primeiro, `apikey.manage` seria escalada silenciosa.
- ⚠️ **Só o compare do Argon2 é cacheado (60s), nunca a autorização** — a linha é relida a cada request, então revogar vale na hora. Rate-limit em **bucket próprio por chave**: nunca o do login, nunca derivado de `audit_ip` (autodeclarado ⇒ forjável).
- ⚠️ **`"*"` no webhook de saída cobre só `EXPORTABLE_EVENTS`**, não o barramento inteiro — é o que impede `llm.after` (leva histórico e prompt) e `presence/receipt.changed` (altíssimo volume) de saírem da instalação por descuido. Estado em TABELA, nunca em memória: um toggle de plugin derruba o processo. Não confundir com `/api/webhook/{provider}/{channel_id}`, que é o de ENTRADA.
- ⚠️ **Mídia na v1 (`POST /api/v1/messages/media[/link]`): o `kind` é do CHAMADOR, NUNCA inferido do MIME** — é o que entrega "imagem como arquivo" (`.png` com `kind=document` sai por `documentMessage`, sem recompressão). Preparo unificado em `MessagingService.send_media_upload` + `_MEDIA_KIND_SPEC` (R-media): as 4 rotas do painel e as 2 da v1 são CHAMADORAS, não cópias — e a rota `/link` busca a URL com guard de SSRF ([app/services/remote_media.py](app/services/remote_media.py)), sem o qual `conversation.reply` viraria scanner da rede interna.
- ⚠️ **Rota de upload nova exige entrada em `_UPLOAD_PATH_RE`** ([server/upload_limits.py](server/upload_limits.py)): o teto de 50 MB é por LISTA DE CAMINHOS e fora dela o corpo inteiro vai para a RAM. O caminho `content_base64` não passa pelo middleware e tem teto próprio (`base64_exceeds`, medido no comprimento da string — decodificar para medir já é ter o arquivo na memória).

## GOWA REST API (endpoints reais — v8.11.0 multi-device)

IMPORTANTE: O GOWA v8.11.0 é multi-device. Antes de usar qualquer endpoint, é necessário criar um device via `POST /devices`. Após criação, todas as requests (exceto `/devices`) exigem header `X-Device-Id`.

| Operação | Método | Endpoint | Notas |
|---|---|---|---|
| Listar devices | GET | `/devices` | Sem header obrigatório |
| Criar device | POST | `/devices` body: `{device_id?}` | Sem header, retorna device_id |
| Login/QR | GET | `/app/login` | Retorna JSON com `results.qr_link` (URL do PNG) |
| Status | GET | `/app/status` | Retorna `results.is_connected`, `results.is_logged_in` |
| Logout | GET | `/app/logout` | |
| Reconectar | GET | `/app/reconnect` | |
| Enviar msg | POST | `/send/message` body: `{phone, message, mentions?, reply_message_id?}` | `mentions`: lista de números (ou `@everyone`); `reply_message_id`: citar/responder |
| Revogar msg | POST | `/message/{id}/revoke` | Apagar mensagem pra todos |
| Reagir | POST | `/message/{id}/reaction` body: `{phone, emoji}` | Emoji vazio remove a reação |
| Listar chats | GET | `/chats?limit=N` | Resposta aninhada: `results.data[]` |
| Msgs do chat | GET | `/chat/{jid}/messages?limit=N` | Resposta aninhada: `results.data[]` |
| Info de grupo | GET | `/group/info?group_id={jid}` | Participantes (phone/lid/admin) — usado por `group_mentions` |
| Info de usuário | GET | `/user/info?phone={jid}` | pushName ("default name") — só business retorna |
| Contatos do device | GET | `/user/my/contacts` | Catálogo do celular (digits → nome salvo) |
| Foto de perfil | GET | `/user/avatar` | Bytes do avatar (cacheado em `statics/avatars/`) |

Binário iniciado com: `gowa.exe rest --port 3000 --webhook http://127.0.0.1:{web_port}/api/webhook`

Campos do payload do webhook GOWA: `body`, `from`, `sender_jid`, `chat_id`, `id`, `is_from_me`, `timestamp`, `from_name`

## Convenções de código

- Python com type hints nas assinaturas de função
- Logging via `logging` stdlib (nunca print)
- Operações bloqueantes (GOWA, LLM/proxy Techify, banco) usam `asyncio.to_thread()` no backend FastAPI
- Nomes de variáveis e comentários em inglês; textos exibidos ao usuário em português BR
- Tratar respostas da API GOWA com fallback para nomes de campo alternativos (a API não é 100% consistente nos nomes)
- Frontend: ES modules, componentes Preact em PascalCase, services/hooks em camelCase
- **Tools do LLM (core)**: criar em `agent/tools/<name>.py` com (a) o schema dict (`<NAME>_TOOL = {"type": "function", ...}`) e (b) função `execute(ctx, args) -> str | None`. Adicionar a tupla `(SCHEMA, execute)` em `CORE_TOOLS` em `agent/tools/__init__.py`. O dispatch é genérico via registry em `AgentHandler` — nunca adicionar `if/elif` por nome de tool
- **Tools de plugin**: viver em `storages/plugins/<id>/tools.py` no formato `CORE_TOOLS = [(schema, executor), ...]` e ser declaradas no manifest. NÃO mexer em `agent/tools/` ou no handler. ⚠️ O módulo de `entry.tools` **tem de ser livre de efeito colateral em import**: ele é semeado em `ai_tools` e, quando o operador edita o código pela tela, é **re-executado** in-process — um módulo que sobe thread ou muta global no import faria isso duas vezes
- **Contrato de tool (core OU plugin)**: toda tool registrada vira row em `tool_overrides` automaticamente (via `tool_override_repo.ensure` no `_register_tool`). O usuário pode customizar `description` e `display_label` na tela `/tools`. O `name` da tool é IDENTIDADE e NÃO deve ser renomeado depois de release — quebra histórico de `usage` (`call_type=<name>`) e overrides do usuário. Description em código é o **default**: escreva como instrução clara pro LLM, deve funcionar sem customização. O schema também aceita `"display_label": "..."` no dict raiz (fora de `function`) — o handler retira antes de mandar pro LLM, e o valor vira o default mostrado na UI
- **Acesso a dados**: sempre via SQLAlchemy Core. Repos em `db/repositories/` usam `with get_engine().begin() as conn:` + statements de `db/tables`. Nunca usar `sqlite3` diretamente. Plugins acessam o banco via `from plugins.context import make_plugin_db` + `from sqlalchemy import text`
- **Documentação**: a **regra** e o aviso ⚠️ vão no `CLAUDE.md` (até ~2 linhas); o **mecanismo, a história, a medição e os números de produção** vão no guia temático de `docs/` — ver o Índice no topo. `docs-planos/` **não** é destino de documentação durável (é podado depois que o plano executa). Nunca use `@arquivo.md` no `CLAUDE.md`: o import é inlinado no contexto e a economia é fictícia.

## Tema e modo escuro → [docs/FRONTEND.md](docs/FRONTEND.md)

O painel suporta modo claro e escuro: a classe `.dark` no `<html>` (toggle na engrenagem, persistido em `localStorage["whatsbot_theme"]`, aplicado por script inline no `<head>` antes do 1º paint). As cores são **variáveis CSS (canais RGB)** em [web/static/css/custom.css](web/static/css/custom.css) — a paleta `wa-*` do Tailwind resolve para `rgb(var(--wa-*) / <alpha-value>)`, então alternar a classe re-tematiza o app inteiro e `bg-wa-teal/10` continua funcionando.

**REGRA — toda área nova (tela core, card, modal, tela de plugin) tem de ser legível no modo escuro:**

- **Prefira as classes semânticas `wa-*`** para superfícies/textos/bordas (`bg-wa-bg`, `bg-wa-panel`, `text-wa-text`, `text-wa-secondary`, `border-wa-border`, `bg-wa-hover`, `bg-wa-teal`) — trocam de cor sozinhas nos dois temas.
- **Campos de formulário**: use `.wa-field` em `<input>`/`<textarea>`/`<select>`. Sem cor de fundo cai no branco padrão do navegador + texto claro do tema = ilegível.
- **Não dependa de cores cruas do Tailwind**. Há overrides `html.dark` no `custom.css` como rede de segurança para as mais comuns, mas **hex inline e cores fora dessa lista NÃO são cobertos**.
- Acentos (`text-white` em botão colorido, vermelho de "excluir") podem ficar como estão. Controles nativos seguem o tema via `color-scheme`.
- **Sempre teste** com o modo escuro ligado. Telas de plugin seguem as MESMAS regras.

## Dados do projeto

Dados de banco vivem no Postgres apontado por `DATABASE_URL`; no filesystem (raiz do projeto em dev, bind mounts no Docker) ficam:
- `storages/` — dados do GOWA (sessão WhatsApp) + plugins do usuário
- `logs/` — logs com rotação
- `statics/outbox/` — mídia enviada pelo operador
- **Webhook payloads (debug)**: últimos 50 payloads raw do GOWA em memória, acessíveis via `GET /api/webhook-payloads`
- **Contatos arquivados**: ao receber mensagem de um contato, o webhook consulta `gowa_client.is_chat_archived(jid)` e persiste `is_archived` na tabela `contacts`. A sidebar filtra por `?archived=true/false`. O status de archive é atualizado on-demand (não por polling)

## Sistema de plugins → [docs/PLUGINS.md](docs/PLUGINS.md)

Plugins são extensões opcionais isoladas em `storages/plugins/<id>/` (volume Docker, ignorado por updates). Um plugin pode agregar **tools** do agente, **prompt fragments**, **endpoints REST** sob `/api/plugins/<id>/...`, **tela Preact** via `import()` dinâmico, **migrations SQL** com prefixo `plugin_<id>_`, **settings declarativas** (Pydantic), **canais** (provider), **event handlers/filters** do bus e **broadcast WebSocket** (`from plugins.context import broadcast`).

### Layout de um plugin

```
storages/plugins/<id>/
├── plugin.yaml              # manifest (id, name, version, whatsbot_api_version, entry, screens)
├── __init__.py
├── tools.py                 # CORE_TOOLS = [(schema, executor), ...]   (opcional)
├── prompts.py               # PROMPT_FRAGMENTS = [callable, ...]        (opcional)
├── routes.py                # router = APIRouter()                       (opcional)
├── settings.py              # class Settings(BaseModel) — Pydantic       (opcional)
│                            #   (config do plugin: settings OU screen config:true)
├── migrations/
│   └── 001_initial.sql      # tabelas com prefixo plugin_<id>_
└── static/
    └── <id>.js              # default-export componente Preact
```

### Lifecycle

1. **Bootstrap** — com `storages/plugins/` vazio, semeia **somente `gowa`** a partir de `assets/plugin_examples/gowa/` (`BUNDLED_AUTO_INSTALL`). Qualquer outro entra por `Importar (.zip)`. O `gowa` bundled tem upgrade **version-aware**: versão maior em `assets/` substitui a instalada (edições manuais lá são perdidas).
2. **Discovery** — `discover_and_load(plugins_dir)` escaneia, parseia o manifest e faz `upsert` na tabela `plugins`.
3. **Migrations** — `run_pending_migrations` aplica os `NNN_descricao.sql` em ordem; o migrator **recusa** `CREATE/ALTER TABLE`, `CREATE/DROP INDEX` fora do prefixo `plugin_<id>_`.
4. **Import** — `importlib.spec_from_file_location` registra o pacote como `whatsbot_plugins.<id>`; submódulos declarados em `entry:` são importados sob demanda.
5. **Wiring** — tools/prompts entram no registry, `include_router` monta `/api/plugins/<id>`, `mount` serve `static/` em `/plugins/<id>/static`, `screens[].path` vira rota SPA.
6. **Toggle** — enable/disable atualiza a tabela e dispara `schedule_restart` (`os._exit(0)`; o supervisor relança).

**Settings declarativas**: `class Settings(BaseModel)` em `settings.py`; `GET/PUT /api/plugins/<id>/settings` valida via Pydantic e persiste em `config_repo` com prefixo `plugin.<id>.<field>`. O form é gerado pelo `PluginSettingsForm`.

### Convenções obrigatórias

- **`id`**: snake_case, regex `^[a-z][a-z0-9_]{0,31}$`. Vira o prefixo de tabela e o nome do pacote Python.
- **Tabelas**: SEMPRE `plugin_<id>_<nome>`. O migrator rejeita o contrário com erro claro.
- **`whatsbot_api_version`**: range semver no manifest. **Use sempre comparadores** (`">=1.0,<2.0"`) — o parser do backend ([plugins/semver.py](plugins/semver.py)) REJEITA `"1.1"`, `"^1.1"`, `"~1.1"` e `"1"`, e range rejeitado significa plugin que **não carrega**; o do frontend aceita essas formas, então não copie a sintaxe de um campo para o outro. Versão atual em [plugins/semver.py](plugins/semver.py) (`plugins.manifest` é re-export) — ver "Versionamento da API de plugins".
- **`plugin_services_version`**: obrigatório em plugin novo que declare `frontend_extends` (use `">=2.1,<3.0"` hoje, se depender do `subscribe`; `">=2.0,<3.0"` continua válido). Omissão significa legado 1.x; é independente de `frontend_api_version`.
- **`entry.services` / `uses_services`**: a API interna plugin→plugin (ver a seção própria). **Nunca** exponha essa superfície por HTTP; `services.py` do provedor é FOLHA; consumidor importa `plugins.services` de forma defensiva. ⚠️ `uses_services` é INDEPENDENTE de `plugin_services_version` (que é frontend) — a colisão de nome é a armadilha aqui.
- **Tempo real**: `api.services.subscribe` / `wsBus`, **nunca** `new WebSocket('/ws')` — ver "Frontend dinâmico".
- **Permissions**: declaradas no manifest mas **não enforced no MVP** — informativo apenas.
- **Configuração no próprio plugin**: opções de um plugin vão SEMPRE na aba de configuração dele (settings declarativas e/ou screen `config: true`), NUNCA numa aba nova do painel de Configurações do core. Ver "Onde fica a configuração de um plugin".
- **Settings**: chaves persistem com prefixo `plugin.<id>.`. Plugin nunca grava direto na tabela `config` sem esse prefixo.
- **Cores / modo escuro**: a tela do plugin (`static/<id>.js`) DEVE ser legível no tema escuro. Use as classes semânticas `wa-*` (`bg-wa-bg`, `bg-wa-panel`, `text-wa-text`, `border-wa-border`, …) e `.wa-field` em inputs. Cores cruas (`bg-white`, `bg-green-50`, …) têm fallback no `custom.css`, mas hex inline e cores fora da lista coberta NÃO — teste com o modo escuro ligado. Ver "Tema e modo escuro" e [docs/FRONTEND.md](docs/FRONTEND.md).
- **Auditoria**: toda rota do plugin que MUDA configuração ou estado com dono chama `plugins.context.audit(...)`. Ver "Auditoria e RBAC" abaixo e o guia [docs/PLUGINS_AUDITAVEIS.md](docs/PLUGINS_AUDITAVEIS.md).

**Tool editável pela tela** ([docs/IA.md](docs/IA.md)): tool com linha em `ai_tools` ganha Editar código / Histórico / Excluir em `/ai/tools`. ⚠️ **A baseline confiável é o DISCO** — `version <= 1` (não editada) ⇒ o disco manda e o código do banco é só o que a tela mostra; `version > 1` ⇒ o do banco é compilado e roda in-process. **O módulo é a unidade de edição** (um `tools.py` declara N tools e salvar propaga para as irmãs), e Excluir grava tombstone — sem ele o loader recontribuiria a tool a todo boot.

### O que fica no core e o que vai pro plugin (REGRA DE DECISÃO)

**Tudo que puder ir para o plugin vai SÓ para o plugin, com o mínimo possível no core.** Um recurso só merece **ramo, evento ou campo novo no core** quando os **três** critérios forem verdade ao mesmo tempo:

1. **≥ 2 consumidores previstos** — reais, não hipotéticos;
2. **nenhum gancho existente enxerga o sinal**;
3. **usar o gancho existente custaria caro no caminho quente**.

Falhando **qualquer um**, o comportamento de negócio vai inteiro para o plugin — mesmo que o gancho disponível seja mais tosco que o ramo que você desenharia. Precedente conta como evidência: se outro plugin já resolve o mesmo problema pelo mesmo gancho em produção, o gancho está provado.

A exceção é a **fronteira de confiança**: identidade/resolução da rota, veredito de assinatura e autorização permanecem no core — mesmo com um único consumidor — e entregam ao plugin apenas o contexto já validado. Plugin não deve autenticar a si próprio a partir de uma rota pública.

**Critério de aceite:** o plugin carrega num core da release anterior; feature que exija seam novo **degrada explicitamente** e documenta "core antes do zip".

⚠️ **"Não muda o core" ≠ "não depende do core".** Um plugin extraído continua importando `db.repositories`, `plugins.context`, `runtime.supervisor`, `server.message_errors` — e **nenhum desses é API declarada**. Por isso **todo import além do mínimo é defensivo** (`try/except` que degrada): import não-defensivo de módulo que mudou = o plugin **nem carrega**, falha muda no boot.

Casos que fixaram a regra, a lista de candidatos **refutados** e o contrato completo do observador de `filter.webhook.payload`: [docs/PLUGINS.md](docs/PLUGINS.md).

### Onde fica a configuração de um plugin (REGRA)

**Toda configuração de um plugin vive na aba de configuração DO PRÓPRIO plugin** — o botão **Configurar** no card em `/plugins`. **Nunca** adicione seção/aba nova ao [ConfigPanel.js](web/static/js/components/ConfigPanel.js) do core para algo que pertence a um plugin. Dois jeitos (escolha um ou combine): **settings declarativas** (`settings.py`, form auto-gerado) e/ou **screen `config: true`** (componente Preact próprio, renderizado no mesmo modal — quando existe, substitui o form declarativo).

**Largura do modal: o plugin DECLARA, o core traduz** (API 1.4.0) — `screens[].width` ∈ `normal`/`wide`/`full`, avaliado por um mapa fechado; valor desconhecido cai no default e a string do manifest **nunca** é interpolada numa classe. ⚠️ Não suba o piso de `whatsbot_api_version` por causa disso: a chave degrada sozinha num core anterior.

⚠️ **`custom_sounds` e `notifications` NÃO são mais plugins** — o subsistema de som foi absorvido pelo core ([server/sound_catalog.py](server/sound_catalog.py), [SoundSettings.js](web/static/js/components/SoundSettings.js), tabela `custom_sounds`).

### Frontend dinâmico e override de componente

`/api/plugins/manifest` devolve os plugins carregados com seus `screens[]`. Screen com `config: false` (default) vira página no menu da engrenagem; com `config: true` é filtrada de lá e renderizada no modal **Configurar**. `PluginScreen` faz `import(screen.component)` e passa `apiBase = "/api/plugins/<id>"`.

🚫 **Tela de plugin NUNCA abre `new WebSocket('/ws')`** (plano 107). O socket cru não leva o `?token=` e o servidor o fecha com **4401** assim que existe ≥1 usuário — falha **silenciosa e permanente**: a tela simplesmente para de atualizar. Use `api.services.subscribe(handlers)` (plugin services ≥ 2.1) ou `subscribe` de [wsBus.js](web/static/js/services/wsBus.js), que entregam **qualquer** nome de evento — ao contrário de `api.services.useWebSocket`, cujo mapa é fixo nos nomes do core. Se o handler dispara refetch caro, ponha **debounce com jitter**.

O registry tem três semânticas: **slots** (aditivos, ex. `channel.card.rows`), **route override** (exclusivo) e **`overrideComponent(name, C)`** — substituir peça de UI que não é rota, **primeiro que registra ganha**. O Host **congela o componente na montagem**. Dono atual: `template.picker` pertence ao `whatsapp_cloud`; a cópia do core está **congelada** como fallback de transição — **não corrija bug nela**.

### Versionamento da API de plugins (`WHATSBOT_API_VERSION`)

**Versão atual: `1.8.0`** ([plugins/semver.py](plugins/semver.py) — fonte única). Changelog e política completa: [docs/PLUGIN_API_CHANGELOG.md](docs/PLUGIN_API_CHANGELOG.md). Guard: [tests/contracts/test_plugin_api_surface.py](tests/contracts/test_plugin_api_surface.py) + `tests/goldens/plugin_api_surface.json`.

| Nível | Gatilho |
|---|---|
| **MAJOR** | remover/renomear nome de catálogo **com produtor vivo**; mudar tipo do valor de um filtro ou a semântica do `None`; remover símbolo público, campo de dataclass, chave de `entry` ou convenção de host. **Derruba os 36 manifests do parque de uma vez** — é tranche com ordem de deploy, não decisão de commit |
| **MINOR** | acrescentar nome ao catálogo (**no mesmo commit do call site**), símbolo, campo com default, chave de `entry`, capability, método com default; alargar `ctx.extras` |
| **PATCH** | correção que não muda a forma; retirar nome de catálogo **sem produtor vivo** |

⚠️ **A constante ficou congelada em `1.0.0` por 93 dias** enquanto a superfície crescia de 35 para 75 eventos — o guard nunca rejeitou nada e nenhum plugin conseguia declarar de que core precisava. Por isso a disciplina agora tem dente: a regeneração do golden **se recusa a rodar** enquanto a constante não tiver andado. Fluxo quando o guard fica vermelho: (1) bump em `plugins/semver.py`; (2) entrada no topo do changelog; (3) `UPDATE_PLUGIN_API_SURFACE=1 venv/bin/python -m pytest tests/contracts/test_plugin_api_surface.py`.

**Está dentro da superfície versionada** se existe um snapshot que falha quando aquilo muda. `db.repositories` e companhia ficam **de fora de propósito** — são dependência real, não API declarada. O frontend tem números próprios (`FRONTEND_API_VERSION`, `PLUGIN_SERVICES_VERSION`) e falha de forma **assimétrica**: lá, incompatível pula o `frontend_extends`; aqui, incompatível faz o plugin **deixar de existir**. **Nunca sincronize os valores.**

### Auditoria e RBAC → [docs/PLUGINS_AUDITAVEIS.md](docs/PLUGINS_AUDITAVEIS.md)

A trilha (`audit_log`, tela `/audit`) é dirigida pelo bus; a allowlist `AUDITABLE_EVENTS` é o vocabulário do **core** e plugin não a edita. O plugin registra as próprias ações pelo seam `audit("<id>", "<recurso>.<verbo>", before=…, after=…)` ([plugins/context.py](plugins/context.py)) — ação namespaceada, validada contra `PLUGIN_ACTION_RE`, fire-and-forget, nunca levanta.

- **Segredo nunca entra**: registre `{"secret_definido": True}`, não o valor. Conteúdo já versionado entra como **ponteiro** (`{key, version}`).
- ⚠️ **A denylist do `audit_repo` casa nome EXATO de chave, nunca substring** — foi assim que `nexus_dsn` (com a senha do banco de produção) ficou em claro na tela `/audit` por semanas, ao lado de um `openrouter_api_key` mascarado que fazia a trilha parecer limpa. Segredo novo ⇒ acrescente o nome lá (conserta todos os plugins de uma vez) **e** não o mande no payload.
- **Plugin de CANAL grava no CANAL** (`resource_type="channel"`, `resource_id=<channel_id>`) — assim um filtro por canal devolve a história inteira.
- ⚠️ **CONVERSA NUNCA ENTRA NA TRILHA** (regra dura): nem envio do operador, nem resposta da IA, nem inbound, nem reação/edição/recibo/presença. O histórico de `messages` já é esse registro.
- **RBAC**: permissões no bloco `rbac:` do manifest viram `plugin.<id>.<key>`; enforce **sempre** pela dependency `plugin_permission("<key>")` (nunca na mão), e esconda a screen com `screens[].requires`. **Default-allow** quando não há identidade de usuário. Disable **esconde** as chaves do picker mas **preserva** os grants.

### API interna plugin→plugin (`entry.services`) — nunca HTTP

Terceiro canal entre plugins, ao lado do **barramento** ("aconteceu algo") e dos **filtros** ("reescreva este valor"): **request/response**. Motor em [plugins/services.py](plugins/services.py).

- **Provedor** exporta `SERVICES = {"op": callable, ...}` (+ `SERVICES_VERSION`, `SERVICES_ALLOW`) do módulo em `entry.services`. A versão mora no **código**, não no manifesto. ⚠️ Não confundir com `plugin_services_version`, que é a superfície de **frontend** e não tem relação nenhuma com este campo.
- **Consumidor** declara `uses_services: [{plugin, version}]` e chama `services.call("<id>", "op", _as="<meu_id>", **kwargs)`.
- **Envelope**: o dispatch **NUNCA levanta** — `ok` · `unavailable` · `unknown_op` · `incompatible` · `disabled` · `wrong_context` · `error`. `get()` é **null-object** (nunca `None`): feature detection é `if services.get("x"):`.
- **Invisível ao HTTP** (requisito central): o módulo não importa `fastapi` e nenhum provedor expõe `/rpc`. Travado por teste.
- ⚠️ O registro acontece em `create_app`, **antes** do `setup()` — uma op não pode depender de estado criado no `setup()`; se depender, devolve `DISABLED`, nunca quebra.
- ⚠️ O `services.py` do provedor tem de ser **FOLHA** (nenhum outro módulo dele o importa) e o import no consumidor é sempre **defensivo** — senão o plugin não carrega num core anterior.
- **`as_plugin` é FALSIFICÁVEL**: é contabilidade de alcance, não fronteira de segurança. A fronteira real é "nada sai do processo". **Auditoria é do PROVEDOR**, por operação com efeito externo.

### Events e Filters (bus do plugin) → [docs/PLUGIN_BUS.md](docs/PLUGIN_BUS.md)

Dois mecanismos complementares (padrão WordPress: actions + filters). **Events** — broadcast fire-and-forget, paralelo, não bloqueia o pipeline (`EVENT_HANDLERS` em `events.py`, `entry.events`). **Filters** — interceptivos, síncronos no pipeline, recebem `(ctx, value)` e devolvem o valor modificado **ou `None` para abortar a ação** (`FILTERS` em `filters.py`, `entry.filters`). Handler e filter podem ser sync ou async; exceção em um é isolada. Toggle do plugin é tudo-ou-nada.

⚠️ **A fonte de verdade é executável**: [plugins/events.py](plugins/events.py) (`KNOWN_EVENTS`, `KNOWN_FILTERS`, `EXPERIMENTAL_FILTERS`) traz a semântica de cada filtro em comentário ao lado do nome. As tabelas do guia são referência de **payload** — se divergirem, o código vence. Acrescentar nome ao catálogo é MINOR **no mesmo commit do call site** (travado por `test_bus_catalogue_matches_producers`).

Famílias de evento: `message.*` (`received`/`saved`/`sent`/`any`/`reaction`/`edited`/`revoked`/`deleted`/`failed`), `presence.changed`, `receipt.changed`, `group.*`, `call.received`, `chat.archived`, `connection.changed`, `contact.*`, `tag.*`, `conversation*.*`, `channel.*`, `plugin.*`, `execution.*`, `llm.before`/`after`, `tool.before`/`after`, `ai.config.changed`, `config.changed`, `app.startup`/`shutdown`. Chave especial **`*`** recebe todo evento emitido.

Famílias de filtro: `filter.webhook.payload`, `filter.message.*` (`before_save`/`outgoing`/`notify`), `filter.transcription.*`, `filter.contact.tags`, `filter.event.before_emit`, `filter.system_prompt`, `filter.llm.*`, `filter.tool.*`, `filter.reply.*`, `filter.outbound.text`, `filter.authz.decision`, `filter.provisioning.number`/`.message`, `filter.conversation.*`, `filter.agent.resolve`.

**Regras que quebram produção se ignoradas:**

- ⚠️ **Para reagir a mensagem JÁ salva assine `message.saved`, não `message.received`** — o segundo é emitido ANTES do INSERT e quem lê do DB dá race.
- ⚠️ **`message.saved`/`message.sent` carregam `channel_id` (e `conversation_id` quando resolvido)** desde a API 1.3.0. Resolver a conversa por telefone escreve na thread errada quando o mesmo cliente fala em dois canais — foi o fechamento em cascata do `protocolos`. **Trate `conversation_id=None`**: `retry` e a resposta da IA mandam só `channel_id`.
- ⚠️ **Em `filter.webhook.payload`, devolver `None` DESCARTA a mensagem inbound.** Observador usa prioridade **9000**, devolve o valor intacto, engole exceções, sai na **primeira comparação** e faz banco/rede **fora** do request. `ctx.extras` traz `{provider, channel_id, signature_authenticated}` — sem esse contexto, **fail-closed**.
- ⚠️ **NÃO chame `send_message` dentro de handler de `message.sent`** → loop infinito.
- **Lifecycle bypassa `filter.event.before_emit`** — plugin não bloqueia o próprio carregamento.
- Persista estado em `plugin_<id>_*`, nunca em globals (o toggle derruba o processo). `payload["raw"]` pode ser enorme (base64 de áudio) — corte antes de logar.

### Media types suportados

O inbound é convertido em `parsed_msg` (`media_type` + `media_path` + `media_extras`) para 13 tipos: `image`, `audio`, `video`, `sticker`, `document`, `location`, `live_location`, `poll`, `interactive`, `order`, `product`, `contact`, `contacts`. Payloads e `media_extras` de cada um: [docs/PLUGIN_BUS.md](docs/PLUGIN_BUS.md).

🚫 **Registrar um media type NOVO não é possível hoje.** O antigo `filter.media.unknown` foi retirado de `KNOWN_FILTERS` no plano 100 por não ter call site — registrar esse nome agora gera WARNING de filtro desconhecido. O dispatch de inbound é fechado (12 `kind` literais, sem `else`), então **o provider deve normalizar o payload para um `kind` suportado dentro do próprio `Channel.parse_inbound()`**.

### Criar, importar e exportar

Use o slash command **`/new-plugin`** (ou **`/new-channel`** para um provider). Ele gera a estrutura em `../whatsbot-pro-plugins/plugins/<id>/`, com fonte em `src/` e testes fora do artefato.

- **Export**: `GET /api/plugins/<id>/export` devolve `.zip` da pasta (sem `__pycache__/` e `.db`), nomeado `<id>-<versao>-plugin.zip` — cai em `<id>-plugin.zip` se o manifesto não for legível (inclusive quando o `whatsbot_api_version` é incompatível com o core rodando). O nome do arquivo NÃO participa do import, que lê o `id` do manifesto de dentro do zip.
- **Import**: `POST /api/plugins/import` exige manifest único na raiz, checa colisão de `id` e path traversal, extrai em `storages/plugins/<id>/` e deixa `enabled=0` até o usuário ativar.
- **Build publicado**: no repositório `whatsbot-pro-plugins`, `python3 scripts/build_plugins.py <id>|--all`; `--check` valida que o `.zip` corresponde byte a byte a `src/`.

### Onde vive o código de um plugin (NÃO confundir)

Nada sincroniza estes pontos automaticamente. Um mesmo plugin pode ter conteúdos diferentes em cada um, inclusive **com o mesmo número de versão** — já aconteceu com o `protocolos` (duas cópias distintas ambas marcadas `1.17.0`). Ao comparar versões, compare o CONTEÚDO, nunca só o número.

| # | Lugar | O que é | Como o código chega/sai |
|---|---|---|---|
| 1 | **Repositório de plugins do Pro** — [Techify-one/whatsbot-pro-plugins](https://github.com/Techify-one/whatsbot-pro-plugins) | Fonte de desenvolvimento em `plugins/<id>/src/`, testes em `plugins/<id>/tests/`, metadados e ZIP publicado no mesmo diretório | editar `src/`, testar e gerar com os scripts desse repositório; commit/push ali |
| 2 | `assets/plugin_examples/gowa/` | Fonte do **único** plugin bundled. Não há mais espelho dos outros | editado aqui; o boot copia para `storages/plugins/gowa/` (upgrade version-aware) |
| 3 | `storages/plugins/<id>/` | Cópia **instalada e rodando** (gitignored), tanto em desenvolvimento quanto em produção | `Importar (.zip)` na UI ou bootstrap do GOWA; nunca é fonte de verdade de desenvolvimento |
| 4 | `plugins/<id>/<id>.zip` no repositório externo | Artefato sem testes entregue ao cliente | gerado deterministicamente a partir de `src/`; instalação por `Importar (.zip)` |

**Cuidado com o termo "Loja de Plugins"**: ele designa EXCLUSIVAMENTE o repositório **community** [Techify-one/whatsbot-plugins](https://github.com/Techify-one/whatsbot-plugins) (publicado em https://whatsbot.techify.one/plugins) — outro repositório, outro produto. **Não** é o repositório de plugins do Pro (#3 acima). Não use "loja" para se referir ao `whatsbot-pro-plugins`.

## Testes → [docs/TESTES.md](docs/TESTES.md)

Os testes do core estão separados por responsabilidade: `tests/core/` (unidades, caracterização, runner das suítes legadas), `tests/contracts/` (contratos públicos que qualquer plugin consome) e `tests/integration/` (API, Postgres, costuras).

```bash
venv/bin/python -m pytest              # core inteiro (pyproject limita a coleta às 3 árvores)
venv/bin/python -m pytest tests/contracts
```

- A suíte roda **contra um Postgres de teste**: `WHATSBOT_TEST_DB_URL` (env ou `.env`); [tests/pg.py](tests/pg.py) recria o schema uma vez por processo e exige `test` no nome do banco.
- ⚠️ **Não rode duas suítes PostgreSQL em paralelo** — cada processo recria o mesmo schema `public`.
- O pytest do core **não descobre** testes em `storages/plugins` e não modifica plugins instalados. Os testes dos plugins rodam no repositório externo: `python3 scripts/test_plugins.py <id>|--all`.
- ⚠️ **Um teste do core NUNCA deve fixar `assets/plugin_examples/<id>` para um `<id>` que não seja `gowa`** — use `resolve_plugin_source` ([tests/plugin_test_utils.py](tests/plugin_test_utils.py)) e **pule** em vez de falhar (`plugin_source_or_skip`). Armadilha: o resolvedor também cai em `storages/plugins/<id>`, então uma máquina com o plugin instalado fica verde enquanto um clone limpo fica vermelho — valide num worktree limpo.
- Para contratos genéricos prefira [tests/fake_provider.py](tests/fake_provider.py); costura usa [tests/support.py](tests/support.py) e o namespace canônico `whatsbot_plugins.<id>.*`.
- Teste manual de ponta a ponta com a **Evolution API** (opcional, recomendado ao mexer em webhook/agent/handler/batching): receita em [docs/TESTES.md](docs/TESTES.md).

## Gotchas

- O GOWA demora ~5s para iniciar e aceitar conexões — o polling de QR/status deve tolerar falhas silenciosamente
- **Device obrigatório**: `POST /devices` deve ser chamado antes de qualquer outro endpoint; sem device registrado, tudo retorna 404 `DEVICE_NOT_FOUND`
- **Login quando já conectado**: `GET /app/login` retorna erro `ALREADY_LOGGED_IN` se o device já está autenticado — verificar `is_connected()` antes de pedir QR
- **Respostas aninhadas**: listas de chats/mensagens vêm em `results.data[]`, não direto em `results`
- JIDs do WhatsApp seguem formato `5511999999999@s.whatsapp.net` — extrair phone com `.split("@")[0]`
- PyInstaller no Windows: paths de binários e web/ mudam (`sys._MEIPASS`), tratado em `gowa/manager.py` e `server/app.py`
- `subprocess.CREATE_NO_WINDOW` é necessário no Windows para não abrir janela de console do GOWA
- GOWA usa `stdout=subprocess.DEVNULL` — NUNCA usar `subprocess.PIPE` sem consumir, causa deadlock no Windows
- Config auto-salva no shutdown do server (lifespan) e na primeira execução (`Settings.load`)
- Frontend vendorizado: libs JS em `web/static/vendor/` — sem dependência de CDN em runtime
- **Sockets fantasma no Windows**: ao reiniciar frequentemente, portas podem ficar presas em LISTENING com PIDs inexistentes. Use porta alternativa ou reinicie o PC
- **`windows_start.bat` mata processos**: o bat já executa `taskkill` para gowa.exe e uvicorn.exe antes de iniciar. No Linux, o `linux_start.sh` faz `pkill -f bin/gowa` no fim de cada iteração do loop pra liberar a porta antes de relançar; pra parar manualmente, `pkill -f "uvicorn server.dev"` + `pkill -f bin/gowa`
- **GOWA `/chats` limit máximo**: `GET /chats?limit=N` retorna HTTP 400 para valores acima de ~200. Usar `limit=100` como máximo seguro
- **Archive status é chat-level**: o webhook do GOWA **não** inclui campo de archive no payload. Para saber se um chat é arquivado, consultar `GET /chats` e verificar o campo `archived` no item com o `jid` correspondente
- **Debug do subprocess GOWA**: por padrão stdout/stderr vão para `DEVNULL`. Para diagnosticar mensagem descartada, setar `WHATSBOT_GOWA_DEBUG=1` e reiniciar — o GOWA sobe com `--debug=true` e grava em `logs/gowa.log` (truncado em ~10 MB), acessível por `GET /api/gowa-logs?limit=N`.
- **Mensagens HSM via Cloud API**: template de conta Business (`<hsm tag="…"/>`) **não é entregue com conteúdo para linked devices** — só para o device primário. O GOWA recebe um `placeholderMessage` com `type: MASK_LINKED_DEVICES` e o webhook chega só com metadata. **Não é bug, é limitação estrutural** do WhatsApp; confirmar procurando `placeholderMessage` em `/api/gowa-logs`.
- **Bootstrap do banco**: na inicialização, `init_db()` exige `DATABASE_URL` Postgres na env (fail-fast com mensagem acionável se ausente/inválida), cria o engine e roda `alembic upgrade head`. Banco vazio nasce direto via Alembic — não há recriação destrutiva
- ⚠️ **`statics/` e `storages/` precisam de pasta persistente no deploy** — [docs/DEPLOY_COOLIFY.md](docs/DEPLOY_COOLIFY.md), gotcha completo em [docs/OPERACAO.md](docs/OPERACAO.md). O Dockerfile **NÃO** declara `VOLUME` de propósito (volume anônimo é **descartado no redeploy** e a leitura "parece persistente"). Use bind mount (`./data/...` no compose) ou **Persistent Storage** do Coolify em `/app/storages` e `/app/statics`. Sem isso: mídia vira 404 e **os plugins somem da interface** (só `gowa` é re-semeado; configs e dados sobrevivem no Postgres, re-importar o `.zip` recupera). Storage de mídia é **per-instância por design** — duas instâncias com o MESMO banco e `statics/` separados dão "Imagem indisponível". A salvaguarda de boot ([server/persistence_check.py](server/persistence_check.py)) grita no log e expõe `storage_persistent` em `GET /api/admin/database`.
- **Bootstrap de plugins**: `BUNDLED_AUTO_INSTALL` copia **somente `gowa`**; os demais são import-only e **não voltam** no próximo boot se o usuário deletar. Exceção: o `gowa` bundled tem upgrade version-aware (edições manuais em `storages/plugins/gowa/` são perdidas no bump).
- ⚠️ **IP do cliente atrás de proxy reverso**: o ponto ÚNICO é [server/client_ip.py](server/client_ip.py) `client_ip(request)`, que caminha o `X-Forwarded-For` **da direita para a esquerda** pulando hops confiáveis. **Nunca `xff.split(",")[0]`** — a parte esquerda é escrita pelo chamador, é **forjável**, envenena a auditoria e fura o rate-limit. Navegador legítimo com IP privado (VPN/LAN): use `WHATSBOT_TRUSTED_PROXY_HOPS=<n>`.
- ⚠️ **IP público autodeclarado pelo painel** (`X-Client-Public-IP`, plano 86): quando o IP morre num hop antes do proxy, quem informa é o navegador — injetado por `authHeaders()` ([httpClient.js](web/static/js/services/httpClient.js)), o seam único de montagem de cabeçalho. `audit_ip(request)` o prefere e cai em `client_ip()`. É **autodeclarado, logo forjável**: serve **só à auditoria**, e o bucket de rate-limit do login continua em `client_ip()` — **nunca** migre para `audit_ip()`, bastaria variar o cabeçalho para anular o limite (travado por teste). Detalhes e limitações em [docs/OPERACAO.md](docs/OPERACAO.md).
- ⚠️ **Echo do próprio envio: quem cala é o PROVIDER, não o core.** Messenger/Instagram reentregam como `message_echoes` tudo que a Página envia, inclusive o que saiu pela Send API — cada envio virava **duas** bolhas. O filtro mora no plugin (só ele sabe que ecoa a si mesmo). No core sobrou a **chave**: `state.processed_messages` é lido como `"<channel_id>:<msg_id>"`, então todo produtor grava com o prefixo — antes gravavam o id CRU e o guard nunca casava. `state.recently_sent` é **heurístico**, rede secundária, não a trava. GOWA e WhatsApp Cloud não ecoam.
- **Restart de plugin requer supervisor**: `enable`/`disable` chama `os._exit(0)` após um delay curto. Em Docker, `restart: unless-stopped` (compose) faz o container relançar; em dev, `restart.py` toca `server/_reload_trigger.py` (`.py` dentro de um `--reload-dir`, casa com o include default `*.py` do uvicorn) — o watchfiles reinicia o worker antes do `os._exit` rodar. O arquivo é regenerado em runtime e está no `.gitignore`. Em EXE Windows, o `update.py` relança. Sem supervisor, o servidor cai e não volta sozinho.
- **Prefixo de tabela enforced**: o migrator usa regex em `CREATE TABLE`/`ALTER TABLE`/`CREATE INDEX`/`DROP TABLE`/`DROP INDEX` e RECUSA migration que tente criar objeto fora do prefixo `plugin_<id>_`. Erro mostra qual nome violou. Usar comentários SQL `--` ou `/* */` é OK; o migrator os strip-a antes da validação.
- **Tool name é global**: se um plugin registra uma tool com nome já existente (core ou outro plugin), o registry loga warning e ignora a duplicata. Convenção: nomes específicos como `<id>_<verbo>` (ex: `orders_create`).
- **Import dinâmico de plugin JS**: o componente é carregado via `import(screen.component)` ES nativo. O path no manifest precisa começar com `/plugins/<id>/static/...` (servido pelo mount estático). CSP em `server/app.py` permite `'self'`, então funciona sem mudança.
- **Plugin com erro de carga**: se importação falha, o erro vai pra coluna `load_error` na tabela `plugins`, aparece no card da UI, e o plugin é pulado — o app sobe normalmente. Não há crash em cascata.
