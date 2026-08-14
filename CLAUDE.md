# WhatsBot

Bot de WhatsApp com IA para uso em servidor/cloud (Coolify/Docker) — **decisão de distribuição (plano 29 P1)**: o produto é server/cloud-first; o empacotamento EXE Windows ficou suspenso quando o banco virou Postgres-only (não há PG em máquina de usuário final). Os launchers dev de Windows/macOS continuam funcionando apontando para um Postgres remoto.

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

| Tabela | Descrição |
|--------|-----------|
| `config` | Configurações do app (key-value, valores JSON-encoded). Configs de plugin usam prefixo `plugin.<id>.` |
| `contacts` | Contatos/grupos (phone, name, email, profissão, empresa, flags). Inclui `is_pinned` (fixar conversa no topo), `has_unread_mention` (@menção não lida em grupo) e `contact_type` (tipo herdado do canal de origem — `whatsapp`/`telegram`/`outros`; ver "Tipo de contato por canal") |
| `observations` | Notas/observações por contato (texto livre) |
| `messages` | Histórico completo de mensagens (role, content, ts, media). Inclui `revoked` (apagada pra todos), `reactions` (JSON `{emoji: [reactor,...]}`), `reply_to_msg_id` (msg_id GOWA da mensagem citada), `edited_ts` (epoch da última edição de uma msg de saída; NULL = nunca editada → o painel mostra "editada") e `media_caption` (plano 87 — a legenda que o cliente digitou junto da mídia, VERBATIM, gravada no INSERT; existe porque `content` é COMPOSTO: a descrição de imagem / extração de documento o reescreve para `"[Descrição da imagem]: <desc>\n<legenda>"` e o painel não consegue separar os dois de forma confiável. NULL = mídia sem legenda ou linha legada → o painel cai no fallback por prefixo). Roles especiais painel-only (não vão ao WhatsApp, renderizam como card centralizado): `tool_call`, `system_notice`, `transcription`, `private_note`, `error`, `conversation_event` (avisos de ciclo de vida da conversa — plano 12) |
| `usage` | Registros de uso da API (tokens, custo, modelo) |
| `tags` | Tags globais (name, color) |
| `contact_tags` | Relação N:N contato ↔ tag |
| `unread_msg_ids` | IDs de mensagens não lidas por contato |
| `executions` | Tracking de execuções (webhook → resposta). Inclui `agent_key`, `total_tokens`, `total_cost_usd` (populados pelo writer a cada chamada de LLM) |
| `execution_steps` | Passos de cada execução (tool calls, llm_request, etc.) |
| `ai_agents` / `ai_variables` / `ai_tools` | Motor AGNO config-in-DB: agente, variáveis e tools com código Python no banco. O **prompt é inline em cada agente** (coluna `ai_agents.prompt`, texto livre próprio do agente — não reutilizável; `{placeholder}` resolvidos por `ai_variables`); editado no formulário do agente, não há mais aba/tabela de prompts compartilhados. `ai_tools` só é instalada/executada com `ai_tools_code_enabled=True` (kill-switch P62, default OFF) |
| `ai_prompts` / `ai_prompts_history` | **Legado** — eram templates de prompt reutilizáveis referenciados por `ai_agents.prompt_key`. Não são mais lidas na resolução do agente (o prompt agora é inline). Mantidas (não destrutivo) por compat; os endpoints `/api/ai/prompts*` continuam existindo mas não alimentam o motor |
| `ai_agents_history` / `ai_tools_history` | Snapshot por versão (save) de cada agente/tool. O snapshot do agente inclui o `prompt` inline, então Histórico/Reverter cobrem o prompt |
| `plugins` | Plugins descobertos no filesystem (id, version, enabled, load_error) |
| `plugin_migrations` | Versões de SQL migrations já aplicadas, por plugin |
| `plugin_<id>_*` | Tabelas criadas por plugins via suas migrations (prefixo obrigatório) |
| `tool_overrides` | Override por-tool (enabled, description, display_label). Row criada automaticamente para cada tool registrada (core + plugin) |

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

### Filtro de tipos de JID (canal GOWA)

O tipo de um chat do WhatsApp é definido pelo **sufixo do JID** (depois do `@`), não pelo número — o prefixo `120363…` é compartilhado por grupo, canal e comunidade. [channels/jid.py](channels/jid.py) (`classify_jid`) mapeia o sufixo para um tipo lógico: `person` (`@s.whatsapp.net`), `person_lid` (`@lid`), `group` (`@g.us`), `newsletter` (Canal, `@newsletter`), `broadcast` (Status/transmissão, `@broadcast`), `bot` (`@bot`), `unknown`.

No webhook GOWA ([server/routes/webhook.py](server/routes/webhook.py)), logo após resolver o `chat_jid`, a mensagem é classificada e **descartada antes de materializar qualquer contato** se o tipo não estiver na lista permitida — corrige o bug em que tudo que não era `@g.us` caía no ramo "pessoa" (um post de Canal virava "contato fantasma"). A lista permitida vem de `config.allowed_jid_types` do canal GOWA (lida do canal `default`, que é single-channel no inbound; cache de 30s, invalidado ao editar a config do canal). Tipos `unknown` nunca são bloqueados (preserva comportamento legado).

⚠️ **São DOIS defaults diferentes, em arquivos diferentes** (plano 103) — não "conserte" um achando que é o outro:

| | Default de **CRIAÇÃO** | Fallback de **RUNTIME** |
|---|---|---|
| Constante | `GOWA_DEFAULT_JID_TYPES` — [channels/providers/gowa_channel.py:63](channels/providers/gowa_channel.py#L63) | `DEFAULT_ALLOWED_JID_TYPES` — [channels/jid.py:38](channels/jid.py#L38) |
| Valor | `person` + `person_lid` (**sem `group`**) | `person` + `person_lid` + `group` |
| Quando vale | semeia o formulário de um canal **novo** (via `config_fields[].default` do descriptor) | quando o canal **não tem** a chave salva (ou salvou lixo) |
| Alcance | só canal criado dali pra frente | **todo canal legado** sem a chave |

Canal GOWA novo nasce, portanto, **sem grupo marcado**: um número de atendimento individual materializava todo grupo de que participa (foram 118 contatos no incidente do plano 102). A opção continua visível e a um clique. Mexer no fallback de runtime seria **retroativo** — calaria grupos em canais antigos. A UI fica na criação/edição do canal GOWA em [web/static/js/components/ChannelsManager.js](web/static/js/components/ChannelsManager.js) (`JidTypePicker`) — o usuário escolhe pelos rótulos amigáveis, sem ver o JID. Vale **apenas para canais GOWA**.

## Contrato de identidade de conta / dedup de canais (plano 32)

Dois canais **do mesmo provider** não podem apontar para a **mesma conta** (o mesmo número no GOWA, o mesmo `phone_number_id` na Cloud API, o mesmo bot no Telegram). A prevenção é **na origem** (bloqueio, não aviso) e a arquitetura é **genérica no core, fina no provider** — igual ao precedente `required_credentials`: o **plugin declara a identidade**, o **core faz todo o dedup** (comparação, storage, índice único, enforcement). Adicionar um provider novo (Instagram, Messenger, widget…) = implementar 1–2 métodos; **o core não muda** e **nunca tem `if provider ==`**.

- **Contrato** ([channels/base.py](channels/base.py)): `AccountIdentity(kind, value)` é a chave de dedup — `kind` = namespace (`phone`/`phone_number_id`/`bot_id`/…), `value` = forma **canônica** não-vazia. Três ganchos no `Channel` (todos default no-op, então `test`/providers que não aderem simplesmente não deduplicam):
  - `identity_from_credentials(creds)` (classmethod) — identidade conhecível **no create** (está na credencial: Cloud `phone_number_id`, Telegram `bot_token`→`bot_id`). O core chama no create/update → **409** antes de persistir.
  - `account_identity()` (instância) — identidade que só aparece **pós-conexão** (GOWA `own_phone` pós-QR). O sweep chama e, num conflito, recusa.
  - `reject_duplicate()` — desfaz a conexão duplicada (default: `logout()`/`stop()`). O provider pode sobrescrever.
  Um provider pode implementar os dois, mas **com `kind` consistente** (Telegram usa `bot_id` nos dois — derivado do token `{bot_id}:{hash}`, sem rede — pra deduplicarem entre si, plano 32 P1).
- **Motor** ([channels/dedup.py](channels/dedup.py)): `same(a, b)` (igualdade exata de `kind`+`value`; `None`/`""` nunca casa) e `find_conflict(provider, identity, exclude_channel_id)` (varre `channel_repo.list_all()` — só `enabled=1`/`archived=0`, mesmo provider). Puro de rede (só DB).
- **Storage** ([db/tables.py](db/tables.py) + migration `0038`): colunas `channels.account_identity` + `account_identity_kind` e o índice único parcial `ux_channels_account_identity (provider, account_identity) WHERE enabled=1 AND archived=0 AND account_identity IS NOT NULL AND <> ''` — cinto de segurança de banco (serializa 2 QRs simultâneos: o 2º leva `IntegrityError`).
- **Enforcement** ([app/services/channel_service.py](app/services/channel_service.py)): create/update resolvem `identity_from_credentials` e, num conflito, levantam `DuplicateChannelError` → **409** (update escapa a própria row via `exclude_channel_id` e checa as creds **efetivas** = armazenadas + edição, barrando editar-pra-colidir).
- **Sweep pós-conexão** ([app/services/channel_identity.py](app/services/channel_identity.py) + loop `channel_identity_sweep_loop` em [server/background.py](server/background.py), owner = plugin `gowa`): por canal vivo, lê `status()`+`account_identity()`, grava `own_phone`/`connected`/`logged_in`/`account_identity` **só quando muda**, e num conflito recusa via `reject_duplicate()` + `last_error` + `logged_in=0` (mantém `enabled=1` — não-destrutivo). Efeito colateral bom: persistir `own_phone` destrava o roteamento inbound `by_phone` (antes coluna morta).
- **Regras**: mesma conta em **providers diferentes** NÃO é duplicata (canais separados — plano 11 D1/D2); arquivados/desabilitados **não** contam; identidade GOWA usa `get_own_number` **device-scoped** (plano 32 F1 — nunca o número de outro device) e canônico BR (12↔13 dígitos colapsam numa forma). Implementar um provider novo: só leia [channels/base.py](channels/base.py) + esta seção (ver `whatsapp_cloud`/`telegram`/`gowa` como exemplos).

## Canais Meta (Messenger/Instagram) — assinatura, base Graph e URL pública (plano 46)

Os canais da Meta que falam a **Messenger Platform** (Facebook Messenger e Instagram) carregam uma cópia autossuficiente da base em cada plugin e compartilham somente a costura genérica de segurança do webhook no core. Mesmo padrão de sempre: **o provider declara, o core executa**, sem `if provider ==`.

- **Assinatura `X-Hub-Signature-256`** (01-A): `Channel.verify_inbound_signature(raw_body, headers) -> bool` ([channels/base.py](channels/base.py), default `True`) segue como compatibilidade; o seam seguro é `verify_inbound_signature_result(...) -> (accepted, authenticated)`, cujo default delega ao hook antigo e devolve `authenticated=False`. O POST `/api/webhook/{provider}/{channel_id}` ([server/routes/channel_webhook.py](server/routes/channel_webhook.py)) lê `await request.body()` e deriva o dict com `json.loads` dos **mesmos bytes** (re-serializar quebraria o HMAC), resolve a rota (inclusive device GOWA antes de exigir a identidade da URL), valida canal+provider e executa o veredito atômico em worker **antes** do `filter.webhook.payload` e dos buffers de debug. Um veredito negativo responde **200 `{"status":"bad_signature"}`** sem ingerir nada (um 4xx faria a Meta re-tentar em loop). O WhatsApp Cloud lê o App Secret UMA vez e deriva os dois booleans do mesmo snapshot; só ele expõe `authenticated=True`, porque o filtro de alerta exige essa procedência. Messenger/Instagram continuam verificando o HMAC normalmente pelo hook antigo, mas nenhum filtro deles consome o sinal. GOWA/Telegram aceitam pelo default sem se declararem autenticados. Canais Cloud **novos exigem App Secret**; legados sem segredo continuam recebendo mensagens com WARNING uma vez por instância, porém nunca disparam alerta de conta.
- **Base `MetaGraphChannel`** (**dentro de cada plugin**; veja `facebook_messenger/meta_graph.py` e `instagram/meta_graph.py`, plano 76·F9; **não** no core): classe **abstrata** (não registra provider nenhum) com `_graph_base()` em `graph.facebook.com`, `_cred`/`_channel_config` (config do canal, cache 30s), `appsecret_proof` (`HMAC_SHA256(access_token, app_secret)` em toda chamada Graph), `_post_message` em `/me/messages`, `send_text`/`send_media`/`react`/`mark_read`/`send_presence`, `resolve_sender_name` (cache 6h), `download_media` e o `parse_inbound` de **`entry[].messaging[]`** (texto, anexos, `is_echo` → `direction="out"`, reaction, delivery/read, postback, location). Ambos importam sua base RELATIVAMENTE (`from .meta_graph import …`): dois canais Meta, duas cópias, preço do zip autossuficiente. O Instagram atual usa o caminho **Instagram-via-Facebook** (Página conectada + Page Access Token), não o antigo `graph.instagram.com`. ⚠️ NÃO confundir com o WhatsApp Cloud, que caminha `entry[].changes[].value` e sobe mídia em `/media`.
- **Mídia por URL pública** (**dentro do plugin** `facebook_messenger/media_urls.py` — plano 76·F9, irmão de `meta_graph`, importado `from .media_urls`; a config key global `public_base_url` continua no core, lida via `config_repo`): a Send API da Meta **busca** o arquivo numa URL — `public_media_url("statics/outbox/x.jpg")` ancora no componente `statics/` e prefixa a config global `public_base_url`. Sem ela (ou com o arquivo fora de `statics/`) o envio falha com mensagem acionável em vez de mandar link quebrado. Anexo **inbound** carrega a URL do CDN em `media_extras["media_id"]`, então o resolver de mídia do core baixa via `download_media` e a URL (que expira) nunca é persistida. O **tipo do anexo** (`image`/`video`/`audio`/`file`) sai do MIME real do arquivo (`attachment_type_for`), não do `kind` pedido pelo core: a Meta valida o tipo declarado contra o Content-Type que ela baixa e recusa um `.mp4` mandado como `file` (o mapeamento de "documento") com `(#100) … code 100/2018007`. Como a Meta baixa o arquivo DENTRO da chamada de envio, o POST de mídia usa `MEDIA_TIMEOUT` (120s) em vez dos 20s padrão.
- **Janela de 24h + agente humano** (02.2): `ChannelCapabilities.human_window_hours` é a janela ESTENDIDA que vale **só para envio humano**; `OutboundRouter.session_open(..., by_human=True)` a considera e só as rotas de envio do OPERADOR passam esse flag (a resposta agêntica nunca). No Messenger, fora das 24h o provider reenvia UMA vez como `messaging_type=MESSAGE_TAG` + `tag=HUMAN_AGENT` **apenas** se o toggle `human_agent_tag` do canal estiver ligado E a conversa estiver com humano (tag `transferido_atendente`) — usar a tag pela IA é tripwire de compliance da Meta. ⚠️ Desde que a `transfer_to_human` parou de aplicar a tag, esse segundo teste (`_conversation_with_human`, dentro do plugin) fica **fail-closed na prática**: com `human_agent_tag` ligado, o reenvio fora das 24h nunca acontece. Religá-lo exige trocar o sinal do plugin pelo gate real da conversa (`ai_active=0` / `assignee_user_id`).
- **Refresh de token** (01-C/D8): `ChannelCapabilities.token_refresh` + `Channel.refresh_token_if_needed()` (no-op). O core **não** agenda nada: um plugin que precise registra `ctx.spawn_task("token_refresh", loop)` no `lifecycle.setup(ctx)` e o supervisor cancela no disable. O Instagram atual **não é consumidor**: usa Page Access Token, sem expiração temporal/loop de refresh.
- **Plugin `facebook_messenger`** (fonte publicada em `whatsbot-pro-plugins/plugins/facebook_messenger/src/`; espelho transitório em `facebook_messenger/`): não é auto-instalado; o ZIP é gerado no repositório externo. Credenciais `page_id`/`page_access_token`/`app_secret`/`verify_token`; configs `graph_api_version` (v25.0) e `human_agent_tag`; dedup por `page_id`; `contact_type` = `facebook`; screen `config:true` para copiar a URL de callback e assinar o webhook da Página (`POST /{page_id}/subscribed_apps`). Identidade do contato = **PSID page-scoped** (a chave `(channel_id, phone)` do core já cobre).

## Alertas da conta Meta num grupo do Telegram (plano 84) — motor no plugin + seam mínimo seguro

O painel era **cego** para tudo que a Meta diz sobre a *conta*: template pausado por baixa qualidade, número caindo de qualidade, tier de mensagens cortado, conta restrita — o operador só descobria quando um envio começava a falhar. A causa: a Meta manda dois tipos de coisa no MESMO webhook, separados pelo `change["field"]`, e o WhatsApp Cloud só olhava `messages` (o `parse_inbound` caminha `value.messages[]`/`value.statuses[]`). Um aviso da conta produzia zero eventos e sumia sem log.

O motor continua **100% no plugin**: não existe `kind="account"`, ramo de dispatch nem contato/conversa para esse sinal. A revisão de segurança da 1.10.2 acrescentou apenas um seam genérico no core: o filtro cru roda depois de resolver e validar a rota, recebendo `provider`, `channel_id` e `signature_authenticated`. Sem esse contexto (core anterior), a fonte de webhook degrada fechada; polling de qualidade e `message.failed` continuam funcionando.

- **Captura** (`filters.py`): `filter.webhook.payload` enxerga o payload antes do parse. `account_changes()` (PURA) devolve um item por `change` com `field != "messages"`; o guard sai na primeira comparação (`raw["object"] != "whatsapp_business_account"`) para outros providers e para o inbound normal. ⚠️ **Devolver `None` DESCARTA a mensagem** — o observador sempre devolve o valor intacto, engole exceções e roda com prioridade 9000. Banco/rede ficam fora do request.
- **Autorização/resolução fail-closed** (`filters.py` `_authenticated_channel`): exige simultaneamente `provider == whatsapp_cloud`, canal existente/ativo desse provider, `signature_authenticated is True` e `entry[].id` igual ao `waba_id` configurado **naquele canal**. Não há fallback para canal único nem alerta sem etiqueta. App Secret é obrigatório ao criar canal novo e valida o HMAC; legados sem ele continuam recebendo mensagens por compatibilidade (WARNING uma vez por instância), mas avisos de conta são bloqueados até a migração.
- **Motor de alerta** (`alerts.py`, **port** de [gowa/alerts.py](assets/plugin_examples/gowa/alerts.py) — plugin não importa de plugin): Bot API do Telegram direta (**não** é a caixa de entrada Telegram do sistema — um alerta não é atendimento), com **agregação e cooldown** (`should_alert`) — a 1ª ocorrência manda a mensagem, repetições idênticas dentro da janela só EDITAM o contador, e a dedupe é **por valor**. `last_alert_ts`/cooldown só são persistidos depois que o Telegram devolve `message_id`; falha de transporte retorna `failed` e a próxima ocorrência tenta de novo. Estado em `plugin_whatsapp_cloud_alert_state` (sobrevive a restart).
- **Três fontes**: o webhook cru (acima), o **polling de `quality_rating`** (task supervisionada em `lifecycle.py`, cadência em minutos — o cursor `quality_seen` só avança após envio/edição confirmado no Telegram) e o bus **`message.failed`**, que **já existia** no core desde o plano 75. O core agora carrega `is_redelivery`, snapshot tirado no receipt antes do fan-out; `is_new=False` sozinho também significa “status chegou antes da row” e não pode ser descartado. O texto continua vindo do `describe_failure` do core.
- **Catálogo de grupos** (`ALERT_GROUPS`, liga/desliga por grupo): template caiu/voltou/recategorizado, qualidade do número, limite de mensagens, conta restrita, falha de envio relevante, **falha por janela de 24h (`131047`) — OFF por padrão** (é erro de operação, não saúde da conta) e `unknown` (campo novo da Meta nunca fica invisível).
- **Configuração** na aba **Configurar** do próprio plugin (`GET`/`PUT /alert-settings`, `POST /alert-test`, gateadas por `core_permission("channel.manage")`) — token do bot **mascarado no GET** (só `bot_token_set` + últimos 4) e `PUT` sem token não apaga o salvo.
- ⚠️ **Migração/pré-requisito da fonte webhook**: canais novos já exigem **App Secret**. Para cada canal legado, editar e preencher **WABA ID + App Secret** (a UI passa a pedi-lo), depois assinar os campos (`message_template_status_update`, `phone_number_quality_update`, `account_update`, …) no App Dashboard da Meta. Sem isso a fonte fica muda por segurança — as mensagens normais do legado continuam fail-open para não causar outage; o polling de qualidade e falhas de envio seguem ativos.

## Provider de canal (plugin) — canais 100% plugáveis (plano 33)

Canais são **plugins de 1ª classe**: cada provider **se autodescreve** e as superfícies de oferta/renderização do core não o conhecem por nome — não há `if provider ==` no formulário, no pós-criação, nos chips/filtros das telas (catálogo único, plano 76), no card (slot `channel.card.rows`) nem no mascaramento de credencial. Ainda existem seams de compatibilidade específicos do GOWA fora dessas superfícies; removê-los depende do plano 100 F2. Adicionar os demais providers = shipar um plugin cuja subclasse de `Channel` implementa os hooks necessários, sem alterar a UI/form de Canais. Só **GOWA** vem auto-instalado; telegram/whatsapp_cloud/facebook_messenger/instagram/website são **importáveis** pelos ZIPs publicados no repositório `whatsbot-pro-plugins`.

- **Descriptor** ([channels/base.py](channels/base.py) `provider_descriptor()`, classmethod): a fonte única do que o core precisa pra **oferecer + renderizar** o provider. Forma: `{provider, label, color, credential_fields:[{key,label,type,required,placeholder?,help?}], config_fields:[{key,label,type,options?,default?,...}], capabilities:{needs_qr,templates}, ai_sequential_default, contact_type, post_create, form_component}` (`contact_type` = o tipo que o canal marca nos contatos — garantido pelo `channel_service` mesmo se o provider sobrescrever o descriptor sem re-adicionar a chave; ver "Tipo de contato por canal"). Tipos de campo que o form genérico entende: `text`, `secret`, `token_suggest` (input + botão "Sugerir"), `multiselect` (checkbox group sobre `options`, seed de `default`), `generated` (read-only auto-preenchido por `prefix`). O default da base deriva um descriptor mínimo; os providers sobrescrevem ([gowa_channel.py](channels/providers/gowa_channel.py), `telegram`, `whatsapp_cloud`, `facebook_messenger`, `instagram`, `website`). O JID-type catalog (que era hardcoded no frontend) agora é um `multiselect` no descriptor do GOWA — o provider é dono dele.
- **Endpoint** ([channel_service.py](app/services/channel_service.py) `providers()` + `provider_descriptor(deps, p)`): `GET /api/channels/providers` devolve `{providers:[descriptor,...], required_credentials:{provider:[key,...]}}` só dos providers **registrados** (plugin ativo). Há dois contratos deliberadamente separados: `credential_fields[].required` valida a **criação nova**; o mapa `required_credentials`, derivado de `ChannelCapabilities.required_credentials`, descreve a **saúde operacional** de rows existentes e alimenta o aviso anti-zombie do card. O serviço garante que todo requisito operacional também apareça como obrigatório no descriptor, mas um provider pode apertar apenas a criação durante uma migração (Cloud exige `app_secret` novo sem chamar o legado fail-open de desconectado). Oferta = instalado; `ALLOWED_PROVIDERS` deixou de ser o gate (sobra só como allow-list de compat no create). Criar canal em `server/routes/channels.py` valida `provider ∈ (registrados ∪ ALLOWED_PROVIDERS)` e os campos obrigatórios do descriptor.
- **Mascaramento de credencial derivado do descriptor (plano 76 · H4/V9)** ([channel_service.py](app/services/channel_service.py) `serialize`/`_public_cred_keys`): na borda da API, credencial sai em CLARO **só** se o provider a declarou `type: "text"` (identificador público — ex.: `phone_number_id`, `waba_id`, `page_id`), o resto é mascarado (`••••` + últimos 4). Sem a antiga lista `NON_SECRET_CRED_KEYS`. **Guarda de nome obrigatória**: mesmo `type:text`, uma chave cujo nome case `/(token|secret|password|senha|key)/i` é mascarada (+ WARNING) — um plugin que erre o `type` não vira vazamento. Default (provider não registrado / descriptor quebrado) = tudo mascarado.
- **Frontend genérico** ([web/static/js/components/channels/](web/static/js/components/channels/)): `constants.js` tem os builders **puros** `buildCreatePayload`/`buildEditPayload` (montam credentials/config a partir do descriptor + valores coletados, sem branch de provider), `providerMeta`/`tintForColor` (badge por `color`), `initialConfigValues`, `missingCredsFor`, `buildEmbedSnippet` (interpolação PURA do `post_create.snippet_template`). `DescriptorFields.js` renderiza `CredentialFields`/`ConfigFields`/`MultiSelect` por `type`, e `FormComponentLoader` importa um `form_component` opcional via `import()` (seam pra provider rico; nenhum built-in usa). `ChannelForm`/`ChannelEditForm` são inteiramente dirigidos pelo descriptor. Testes puros: [constants.test.js](web/static/js/components/channels/constants.test.js) (`node --test`).
- **Catálogo único de providers no cliente (plano 76 · H1)** ([web/static/js/services/providerCatalog.js](web/static/js/services/providerCatalog.js)): a FONTE ÚNICA de "rótulo, cor, tint, bolinha e tipo de contato" de um provider fora da tela Canais. Faz UM fetch de `GET /api/channels/providers`, cacheia, e expõe `providerLabel/Color/Tint/Dot(p)`, `channelPickerMeta(p)`, `contactTypeFor(p)`, `contactTypeColorTokens()`, `fetchedProviders()`/`requiredCredentials()` (usados pelo `ChannelsManager` — sem fetch próprio) e `subscribe()`. Fallback estático mínimo (`gowa`/`test`) até o fetch chegar; provider desconhecido degrada para o próprio id em cinza (D3). Componentes re-renderizam via o hook [useProviderCatalog.js](web/static/js/hooks/useProviderCatalog.js). **Substituiu os 5 mapas estáticos** (ChannelChip `CHANNEL_META`, ConversationInfoPanel `PROVIDER_LABELS`, ChannelPickerModal/NewConversationModal `PROVIDER_META`, contactTypes `CONTACT_TYPE_META` — este virou base curada + descoberta do catálogo). Regra: **nenhuma tela do core mapeia nome de provider → rótulo/cor**; tudo vem do descriptor.
- **Pós-criação dirigido pelo descriptor** ([ChannelsManager.js](web/static/js/components/ChannelsManager.js)): `capabilities.needs_qr` → abre o QR ([QRConnect.js](web/static/js/components/channels/QRConnect.js), genérico); `post_create.kind == "webhook_url"` → `WebhookNotice` com a URL de callback (`post_create.path` com `{channel_id}` substituído); `post_create.kind == "autoconfigure"` → POST em `post_create.endpoint` (`providerPostCreateAction`) e `AutoconfigureNotice` com o resultado (fallback long-poll via `webhook_path`); `post_create.kind == "embed_snippet"` → `EmbedSnippetNotice` com o snippet montado do `post_create.snippet_template` (o core interpola `{base_url}`/`{token}`; a chave do token vem de `token_config_key` — o core não conhece o path `/plugins/website/`). As ações de sessão do card (Conectar/Reconectar/Desconectar) são gated por `needs_qr`, não por nome. As flags de deep-link de modal são `?connect|?webhook|?autoconfig` (capability/post_create, nunca nome de provider).
- **Slot `channel.card.rows` (plano 76 · H2)** ([registry.js](web/static/js/plugins/registry.js)): ponto de extensão aditivo no corpo do card de canal ([ChannelCard.js](web/static/js/components/channels/ChannelCard.js), ctx `{channel, descriptor}`). O provider injeta a própria linha via `frontend_extends` — o `whatsapp_cloud` registra aqui o `WebhookHealthRow` (que vive no PLUGIN, em `static/` dentro do próprio `whatsapp_cloud`, e filtra por `ctx.channel.provider` internamente); usa o `http` de `buildPluginHttp` (o core não chama mais endpoint de plugin). Vazio ⇒ card byte-idêntico; desabilitar o plugin some a linha sem erro.
- **Bundling** ([plugins/bootstrap.py](plugins/bootstrap.py) `BUNDLED_AUTO_INSTALL = ("gowa",)`): fresh install copia **só GOWA**, e `assets/plugin_examples/` contém **apenas o `gowa`** — a fonte, os testes e o ZIP dos demais vivem no repositório `whatsbot-pro-plugins` e chegam ao usuário pela loja de plugins (`Importar (.zip)`). Instalações existentes em `storages/plugins/` ficam intactas.
- **Config do provider mora no plugin**: status/config específicos (ex: webhook vs long-poll do Telegram) vivem na screen `config:true` do próprio plugin (`/telegram/config`), NÃO no form de edição do core — o core edita só nome + campos do descriptor + IA + agentes.
- **Comando**: `/new-channel` ([.claude/commands/new-channel.md](.claude/commands/new-channel.md)) gera um provider correto por construção — subclasse `Channel` + capabilities + ganchos de identidade (plano 32) + `provider_descriptor()` + `contact_type()` (tipo do contato, ver "Tipo de contato por canal") + `entry.channels` + stubs `status`/`send`/`parse_inbound` (+ `lifecycle`/`routes`/`form_component` quando aplicável), sem tocar no core.

## Proxy de saída por número (plano 52)

Cada **canal GOWA** pode rotear a conexão do WhatsApp por um proxy de saída próprio (1 IP por número — ex.: IPs dedicados do webshare.io). O campo "Proxy de saída (opcional)" fica no form do canal (credencial `proxy_url`, tipo `secret` — mascarada na borda da API; formatos `socks5://user:pass@ip:porta` ou `http(s)://…`). **Arquitetura híbrida**: canal SEM proxy segue no processo GOWA compartilhado (inalterado); canal COM proxy ganha um **processo GOWA dedicado** — porta própria (persistida em `config.gowa_dedicated_port`), `cwd` próprio em `storages/gowa_ch_<id>/` (isola `whatsapp.db`/`chatstorage.db`; um symlink `statics` aponta de volta pra raiz pra mídia continuar servível), env **`WHATSAPP_PROXY`** (nunca argv — o cmd é logado/visível em `ps`) e webhook próprio em `/api/webhook/gowa/<id>`. O canal `default` (singleton legado) nunca é dedicado.

- **Orquestração** ([storages/plugins/gowa/processes.py](assets/plugin_examples/gowa/processes.py), fonte em `assets/`): reconcile loop declarativo (task `gowa:process_reconcile`, ~15s) — `plan_reconcile` (puro) diffa desejado×rodando e aplica spawn/stop/restart; auto-cura claims órfãos (proxy removido com o servidor desligado). Transições: **ligar** proxy = evict do device no processo compartilhado (`logout` + `DELETE /devices/{id}`) ANTES do spawn dedicado → **re-parear por QR** (esperado, avisado no help do campo); **desligar** = para o processo, limpa porta/`gowa_isolation`, volta ao compartilhado (novo QR). `storages/gowa_ch_<id>/` é preservado ao desligar (a sessão sobrevive a um re-enable). Proxy inválido/proxy no `default` ⇒ `last_error` no canal, processo não sobe.
- **`channels.gowa_isolation`** (`shared|dedicated_process`, coluna da migration 0011) é atualizada pelo reconcile — observabilidade; a fonte de decisão é a credencial `proxy_url`.
- **Upgrade do plugin bundled** (P7): `bootstrap_gowa_upgrade` ([plugins/bootstrap.py](plugins/bootstrap.py)) é **version-aware** — quando a versão do `plugin.yaml` bundled em `assets/` é MAIOR que a instalada em `storages/plugins/gowa`, o boot substitui a cópia instalada (swap atômico via temp+rename; tombstone de uninstall respeitado; nunca re-habilita plugin desabilitado; instalado mais NOVO que o bundled é deixado em paz). Edições manuais na cópia instalada são perdidas no bump (logado alto).
- **Recomendação de proxy**: IP **fixo e dedicado** por número (datacenter dedicado ou static residential) — NUNCA endpoint rotativo (IP muda por conexão = padrão de ban).
- ⚠️ **O campo bloqueia o autofill do navegador (plano 104)** — todo campo `type: "secret"` de canal (não só o proxy: `bot_token`, `access_token`, `app_secret`, `hmac_token`…) é `<input type="password">`, e o gerenciador de senha injetava nele a **senha do painel** por heurística (não é preciso haver `<form>`); o operador salvava sem olhar e o número parava — pior, um valor que *parecesse* URL subia processo dedicado e exigia QR novo. `secretInputProps(key)` ([constants.js](web/static/js/components/channels/constants.js), aplicado em [DescriptorFields.js](web/static/js/components/channels/DescriptorFields.js)) carrega **`autocomplete="new-password"`** — o `off` é **IGNORADO** pelo Chrome em campo de senha, então não "limpe" isso achando que é resquício — mais `name` estável **sem** as palavras `password`/`senha`/`token`/`secret` (a chave crua as contém e reativaria a heurística) e os opt-outs `data-lpignore`/`data-1p-ignore`/`data-bwignore`/`data-form-type`. Ao lado do input há um botão de mostrar/ocultar (começa sempre oculto, nunca persistido).
- **Formato recusado no save** (plano 104 F3, defesa em profundidade): o provider declara `credential_fields[].pattern` + `pattern_error` no descriptor e o core **só avalia** — `validateCredentials` no formulário ([constants.js](web/static/js/components/channels/constants.js)) e `credential_format_errors` na criação/edição ([channel_service.py](app/services/channel_service.py) → 400 nas rotas), regex **ancorada** e **case-insensitive** nos dois lados, sem `if provider ==`. Valor vazio e o placeholder `••••` nunca são validados (na edição vazio = "manter a atual", então row legada fora do formato continua editável), regex quebrada passa (fail-open) e a mensagem cita **o campo, nunca o valor** (seria a senha do operador em log). O `pattern` do `proxy_url` ([gowa_channel.py](channels/providers/gowa_channel.py)) espelha o `validate_proxy_url` do plugin — que **continua** sendo a rede final pós-save para rows legadas. Travado por [test_channel_credential_pattern.py](tests/integration/test_channel_credential_pattern.py) e por `node --test web/static/js/components/channels/constants.test.js`.

## Limites de mídia por canal (anexo incompatível é bloqueado, não falha)

Anexo (imagem/áudio/documento/vídeo) que não atende às regras do canal é **bloqueado no compositor com um popup** antes do envio — em vez de virar uma bolha "falhou" depois que o provedor recusa. Mesmo padrão policy-vs-mechanism dos outros ganchos: **o provider declara os números, o core só avalia**, sem `if provider ==`.

- **Contrato** ([channels/base.py](channels/base.py)): `MediaLimits(max_bytes, extensions)` — o irmão genérico de `VideoLimits` (que ainda acrescenta regras de codec). Declarados em `ChannelCapabilities.media_limits` como `{kind: limits}` (`image`/`audio`/`document`/`video`/`sticker`). Kind sem declaração = nunca bloqueia (GOWA/Telegram).
- **Números da Meta moram no plugin** (`whatsapp_cloud/channels.py` `_MEDIA_LIMITS`): imagem 5 MB JPEG/PNG · áudio 16 MB AAC/AMR/MP3/M4A/OGG · vídeo 16 MB MP4/3GP H.264+AAC · documento 100 MB PDF/TXT/DOC(X)/XLS(X)/PPT(X) · figurinha 500 KB WebP. Import defensivo (core antigo sem `MediaLimits` continua carregando o plugin).
- **Core** ([channels/media_limits.py](channels/media_limits.py)): `limits_for(caps, kind)`, `validate_upload(filename, size, caps, kind)` → `MediaVerdict(reason ∈ ok/too_big/bad_format, message PT-BR)` e `describe(caps, video_transcode_available=…)` → o dict JSON que vai pro painel. O fallback legado de VÍDEO segue em [channels/video_validate.py](channels/video_validate.py) (plugins anteriores ao plano 65).
- **Backend**: as rotas `/send-image`, `/send-audio` e `/send-document` chamam `_media_limits_block` **antes de gravar o upload** (413 `too_big` / 415 `bad_format`, sem arquivo órfão); `/send-video` mantém o caminho próprio (valida codec → recomprime com ffmpeg → só então bloqueia). O payload de conversa/contato carrega `media_limits` ao lado de `revoke_supported`/`edit_supported`.
- **Painel**: [web/static/js/services/mediaLimits.js](web/static/js/services/mediaLimits.js) (`checkMediaFile`, puro, espelha o backend; testes `node --test`) roda na SELEÇÃO do arquivo; recusado ⇒ [MediaRejectedModal.js](web/static/js/components/contacts/MediaRejectedModal.js) e o anexo nem entra na fila. Vídeo com `transcode: true` (ffmpeg presente no servidor) NÃO é bloqueado no cliente — o servidor recomprime; sobra só o teto de entrada de 200 MB. Recusa que só aparece no servidor (codec) remove a bolha otimista e abre o mesmo popup. Sandbox e nota privada não são validados (não saem para o provedor).

## Tipo de contato por canal (plano tipos-de-contato)

Cada contato registra o **tipo herdado do canal que o materializou**, gravado em `contacts.contact_type` (migration 0050, `server_default='outros'`; rows legadas foram backfilladas para `whatsapp` porque antecedem o Telegram). O **provider declara** o tipo, o **core grava e exibe** — mesmo padrão genérico dos outros hooks de canal (nenhum `if provider ==` no core).

- **Contrato** ([channels/base.py](channels/base.py)): `Channel.contact_type()` (classmethod, default `"outros"`). GOWA ([gowa_channel.py](channels/providers/gowa_channel.py)) e WhatsApp Cloud (`whatsapp_cloud/channels.py`) retornam `"whatsapp"` (mesmo tipo); Telegram (`telegram/channels.py`) retorna `"telegram"` (não guarda telefone — o `phone` é o chat_id numérico do Telegram). O descriptor (`provider_descriptor`) também expõe `contact_type`.
- **Gravação**: só no INSERT do contato. `ContactMemory._resolve_contact_type()` ([agent/memory.py](agent/memory.py)) resolve a classe do provider do canal (via `_resolve_provider_class`, mesmo helper do source_id) e passa a `contact_repo.get_or_create(..., contact_type=...)`. Fail-open para `"outros"` quando o provider não resolve (registry não cabeado em testes, canal sem provider). Um contato já existente **não** é re-tipado (o tipo é do 1º canal que o criou).
- **Exibição**: marca (chip colorido) abaixo do nome/telefone no painel do contato ([ContactInfoPanel.js](web/static/js/components/contacts/ContactInfoPanel.js)) e em cada linha da tela Contatos ([ContactsListScreen.js](web/static/js/components/ContactsListScreen.js)). O catálogo de rótulo/cor por tipo mora em [web/static/js/services/contactTypes.js](web/static/js/services/contactTypes.js) (`contactTypeMeta`), tolerante a tipos novos/desconhecidos.
- **Filtro**: dimensão `contact_type` ("Tipo de contato", multi-select eq/ne) nos dois construtores de filtro — [ConversationFilterDialog.js](web/static/js/components/contacts/ConversationFilterDialog.js) (hub de atendimentos) e [ContactFilterDialog.js](web/static/js/components/contacts/ContactFilterDialog.js) (tela Contatos). Avaliação client-side via `clauseMatches` ([conversationRows.js](web/static/js/services/conversationRows.js)) sobre as rows já carregadas (o campo `contact_type` vem no payload de `list_contacts` e no detalhe).
- **Provider novo**: implemente `contact_type()` (ver `/new-channel`); sem override os contatos herdam `"outros"`.

## Configuração de IA por canal (plano 21)

O comportamento da IA é configurado **por canal**, não globalmente. Cada canal guarda seus overrides em `channels.config["ai"]` (sub-objeto JSON), editáveis na criação/edição do canal em [web/static/js/components/ChannelsManager.js](web/static/js/components/ChannelsManager.js) (`AiSettingsFields`, vale para todos os providers). As chaves por canal (`PER_CHANNEL_AI_KEYS` em [channels/ai_settings.py](channels/ai_settings.py)): `ai_enabled` (master do canal, channel-only), `default_ai_enabled`, `group_reply_mode`, `image_transcription_enabled`, `document_transcription_enabled`, `audio_transcription_mode`/`_target`/`_chat_prefix`, `max_context_messages`, `message_batch_delay`, `split_messages`/`split_message_delay`, `transfer_alert_enabled`/`_duration`.

- **Resolução**: [channels/ai_settings.py](channels/ai_settings.py) `value(channel_id, key, default)` lê o override do canal (cache de 30s, invalidado no PUT do canal) e cai no `default` (o valor global de `config`, que vira o fallback herdado). `view(channel_id, settings)` devolve um shim `.get()` que sobrepõe os overrides do canal sobre o `settings` global (usado no helper de transcrição). O handler resolve `split_messages`/`max_context_messages`/`default_ai_enabled` por canal; o webhook resolve os demais nos call sites (cada um tem `channel_id`).
- **Gate global → canal → conversa**: a IA só responde se (1) o **interruptor GLOBAL** `auto_reply` estiver ligado — checado PRIMEIRO, é o "botão global" no painel da IA; (2) o canal tiver `ai_enabled`; (3) a conversa tiver `ai_active`. `_channel_ai_enabled(channel_id)` no webhook cobre (1)+(2); `_conversation_ai_active` cobre (3).
- **Painel da IA** (aba Configurações, [web/static/js/components/ai/GeneralSettings.js](web/static/js/components/ai/GeneralSettings.js)): mantém **apenas** o interruptor global (`auto_reply`), a chave de API e o aviso de saldo. O resto migrou para a tela Canais. Os valores globais legados em `config` continuam existindo só como fallback de canais que ainda não overridaram (canais novos "herdam" os valores globais atuais ao serem criados).
- **Alerta de transferência** é per-canal: o broadcast `human_transfer_alert` carrega `{enabled, duration}` resolvidos pelo canal, e o frontend ([web/static/js/app.js](web/static/js/app.js)) respeita o payload (fallback no config global para payloads antigos).

## Memória por contato

Cada contato é armazenado na tabela `contacts` com campos normalizados:

- **Info** (name, email, profession, company, address) — colunas diretas na tabela `contacts`
- **Observações** — tabela `observations` (uma linha por observação)
- **Mensagens** — tabela `messages` com colunas `role`, `content`, `ts`, `media_type`, `media_path`, `status`, `msg_id`
- **Usage** — tabela `usage` com tokens, custo e modelo por chamada
- **Tags** — relação N:N via `contact_tags`

`ContactMemory` em `agent/memory.py` é o wrapper que encapsula o acesso via repos. Mensagens são lazy-loaded do DB (não mantidas em memória). Apenas as últimas N (configurável) são enviadas ao LLM.

Info é salva automaticamente via tool calling do LLM e injetada no system prompt. Histórico persiste entre reinícios do app.

### Filtro de histórico por regex (lista-negra — plano 43)

Além da lista-negra de ROLES fixa do repo (`transcription`, `tool_call`, `system_notice`, `conversation_event`, `system`, `error` + `status='failed'`), há um **filtro genérico por regex** que corta linhas do histórico ANTES de virarem contexto do LLM. É uma **lista GLOBAL** de padrões na config key `ai_history_exclude_patterns` (default `[]` = nada cortado, retrocompatível), editável em **Configurações → IA** (textarea, uma regex por linha). Cada mensagem é testada como `f"{role}\t{content}"` com `re.search` — ancora por tipo (`^private_note\t`), por conteúdo (`Protocolo aberto`, `PROT-\d{8}`) ou ambos. Uso típico: cortar notas de automação (ex.: `🔖 Protocolo aberto · PROT-…` gravadas por plugins como `protocolos`) que senão entram no contexto e duplicam com o bloco `tool_memory`.

- **Módulo**: [agent/history_filter.py](agent/history_filter.py) — `load_compiled()` (lê a config, compila, cache TTL 30s), `matches()`, `filter_rows()`. **Fail-open** em todo nível (config ruim, regex inválida, erro no filtro ⇒ histórico passa intacto; regex inválida individual é ignorada + logada). O PUT `/api/config` reseta o cache ao salvar a chave (edição vale na hora).
- **Hook no repo**: `message_repo.get_context(..., *, exclude=None)` e `get_context_by_conversation(..., *, exclude=None)`. Com `exclude` setado, faz **over-fetch** (até `HISTORY_FETCH_CAP=200` linhas), filtra em Python e devolve as N mais recentes sobreviventes — cortar linhas **não encolhe** a janela abaixo de `max_context_messages`. `exclude=None`/`[]` ⇒ caminho byte-idêntico ao antigo (SQL `LIMIT N`). O motor de IA passa `exclude` via `memory.get_context_messages`; a análise "Gerar melhoria" do plugin (`generation.py`) aplica o mesmo corte **preservando a resposta-alvo marcada** (nunca cortada, mesmo se casar um padrão).

## Avisos de sistema no chat (plano 12)

Eventos do ciclo de vida do atendimento são registrados **no fio da conversa** como um card centralizado painel-only — role **`conversation_event`** — igual aos `tool_call`/`system_notice`. Cobre: atribuir/assumir/remover atribuição, adicionar/remover tag, resolver/reabrir/arquivar, ligar/desligar IA (conversa **e** contato), trocar agente ativo, definir atributo — e as transições **automáticas** (cliente reabre conversa fechada ao mandar mensagem → `status_reopened_auto`; conversa nova → `created`; 1ª resposta da IA numa conversa → `ai_takeover`, 1×/conversa via dedupe).

- **Módulo central**: [server/system_notices.py](server/system_notices.py) — `EVENT_GROUPS` (registry dos 4 grupos + chave de config), `EVENT_GROUP_OF` (event_type → grupo), `FORMATTERS` (texto PT-BR com autor), `emit_conversation_notice(*, event_type, conversation_id, contact_id=None, phone=None, **ctx)` (gate → formata → `message_repo.add(role="conversation_event", conversation_id=…)` → `broadcast("new_message")`). Defensivo: um aviso que falha nunca quebra a ação. Extensível: novo tipo = entrada em `FORMATTERS` + `EVENT_GROUP_OF`; grupo novo = + chave em `DEFAULT_CONFIG`/`allowed_keys`/`GET config` + toggle no `ConfigPanel`.
- **Gate GLOBAL por grupo** (config, default ON): `system_notice_assignment`, `system_notice_tags`, `system_notice_status`, `system_notice_ai`. Grupo desligado ⇒ o aviso **não é gerado** (nada grava, nada emite). Toggles na seção "Avisos de sistema no chat" em Configurações.
- **Call sites**: rotas de conversa via `_emit_notice` em [server/routes/conversations.py](server/routes/conversations.py) (resolve o autor do `current_user`); tags em [server/routes/tags.py](server/routes/tags.py); toggle-ai por contato em [server/routes/contacts.py](server/routes/contacts.py); automáticos no `add_message` ([agent/memory.py](agent/memory.py), via `conversation_repo.resolve_for_contact_ex` que sinaliza `created`/`reopened`) e no envio da resposta da IA ([server/routes/webhook.py](server/routes/webhook.py), `_maybe_emit_ai_takeover`).
- **Painel-only**: `conversation_event` é excluído do contexto do LLM ([message_repo.py](db/repositories/message_repo.py)), do preview/última-mensagem da sidebar ([contact_repo.py](db/repositories/contact_repo.py) e [conversation_repo.py](db/repositories/conversation_repo.py) `_PREVIEW_EXCLUDED`) e não conta como não-lida (não entra em `unread_msg_ids`). Render como chip centralizado em [ContactDetail.js](web/static/js/components/contacts/ContactDetail.js); skip de preview em [Contacts.js](web/static/js/components/contacts/Contacts.js).

## Rascunho de mensagem por conversa (compositor)

O texto digitado no compositor **pertence à conversa** e sobrevive à troca de conversa, como no WhatsApp/Chatwoot: digitou "Oi" na `/conversations/123`, foi atender a `/124` e voltou → o "Oi" continua lá. É **pessoal e por-dispositivo**: nada vai para o servidor.

**A conversa ABERTA é a exceção de tudo**: enquanto o chat está na tela, a linha dela segue normal (preview da última mensagem) e **não muda de lugar** — o operador já vê o que escreveu no compositor, e a lista não pode reorganizar embaixo do cursor a cada tecla. Ao **sair** deixando texto, a linha passa a mostrar **"Rascunho: …"** e a conversa **sobe na lista** (o rascunho conta como atividade recente). Reabrir não a faz despencar de volta: a posição da conversa aberta é a congelada na abertura (`frozenOpenRef`).

- **Store** ([web/static/js/services/drafts.js](web/static/js/services/drafts.js)): módulo PURO (sem Preact). Mapa no localStorage namespaceado pelo usuário logado (`whatsbot_drafts_v1:<userId>`, ou `:anon` no modo aberto), então dois operadores no MESMO navegador não veem o rascunho um do outro e o logout não vaza para o próximo login. `setDraftUser(id)` troca o namespace (chamado pelo [AuthGate.js](web/static/js/components/shell/AuthGate.js) a cada mudança de sessão; o namespace inicial sai do `whatsbot_user` já guardado, para o F5 achar os rascunhos antes do shell montar). Escrita e notificação são debounced (400ms) para digitar não re-renderizar a sidebar a cada tecla; `flushDrafts()` no `beforeunload` e no envio. Cap de 300 rascunhos por usuário (cai o escrito há mais tempo — a ordem de inserção do mapa É a ordem de escrita). Evento `storage` sincroniza abas.
- **Chave** = `draftKeyFor({conversationId, phone})`, idêntica ao `rowKeyFor` da sidebar (`conv:<id>`, ou `phone:<n>` para linha sem atendimento). Conversa que nasce migra sozinha `phone:` → `conv:` no 1º envio (`migrateDraft`) em vez de perder o texto.
- **Compositor** ([useComposer.js](web/static/js/components/contacts/hooks/useComposer.js)): o efeito de troca de conversa **hidrata** o input do rascunho (era `setInput('')`); todo caminho que muda o texto (digitação, emoji, @menção, /atalho) passa pelo `setInput` do hook, que salva. O envio limpa (`clearDraft`) — mídia e nota privada não mexem no rascunho de texto. **Sandbox não guarda rascunho** (tela de teste, sem linha na sidebar).
- **Sidebar** ([ContactList.js](web/static/js/components/contacts/ContactList.js)): o hook [useDrafts.js](web/static/js/hooks/useDrafts.js) re-renderiza a lista quando o mapa muda — recebe a chave da conversa aberta e **ignora** as mudanças dela (digitar no chat aberto não re-renderiza a sidebar a cada 400ms; o `subscribe` entrega as chaves alteradas). Precedência do preview: "IA respondendo…" → "digitando…" → trecho da busca → **rascunho** → última mensagem.
- **Ordem** ([conversationRows.js](web/static/js/services/conversationRows.js) `sortContactsBy(list, sortBy, draftTsFor)`): o 3º parâmetro (opcional, mantém a função pura) devolve em SEGUNDOS quando o rascunho foi escrito, e a ordenação usa `max(last_message_ts, rascunho)`. Quem o fornece é [useConversationFilters.js](web/static/js/components/contacts/hooks/useConversationFilters.js), que devolve 0 para a conversa aberta. A **hora exibida** na linha continua sendo a da última mensagem — só a ordenação olha o rascunho. Em `serverMode` reordena só a página carregada (o rascunho é local; o servidor não sabe dele).
- **Cor**: o rótulo usa o token próprio `--wa-draft` (vermelho escuro no claro, red-300 no escuro) — nenhum acento existente passava no piso de 4,5:1 nos DOIS temas sobre `--wa-selected` (a linha da conversa aberta, verde no escuro, é justamente a que costuma ter rascunho). Travado por regra em [themeContrast.js](web/static/js/services/themeContrast.js).

## Janela ancorada, busca na conversa e "ir para data" (plano 99)

A thread do chat deixou de ser uma janela que **sempre termina na última mensagem**. Ela pode ficar **ancorada** no passado — por um salto (resultado da busca global, citação antiga, deep-link `?message=`), por uma ocorrência da busca dentro da conversa ou por uma data escolhida no calendário. Isso destravou a busca/data e, de quebra, matou um bug de produção: pular para uma mensagem fora da janela carregada **falhava em silêncio** (o `focusMessage` devolvia `false` e ninguém pedia nada; se o alvo estivesse na última página, o salto nunca acontecia).

- **Paginação bidirecional** ([message_repo.py](db/repositories/message_repo.py)): além do `before_id` (plano 50), há `after_id` (as N seguintes), `window_around(around_id, limit)` (janela CENTRADA, metade de cada lado, over-fetch de +1 nos dois sentidos) e `first_id_on_or_after(ts)` (dia → id). ⚠️ O id da API apenas identifica a linha: o cursor real é o composto **`(ts, id)`**. Backfill/importação pode gerar PK maior com timestamp antigo; por isso nunca compare só `id <`/`id >` nem use `min/max(id)` no cliente — use a primeira/última mensagem da lista cronológica. `read_window()` é a regra ÚNICA de "qual janela ler", e mora no repo porque as DUAS rotas de thread precisam dela. Devolução **sempre cronológica**. Âncora que não pertence à thread não é erro: degrada para a página recente com `anchor_id=None`.
- **Endpoints** (`GET /api/atendimentos/{id}/messages` e `GET /api/contacts/{phone}`): `after_id` / `around_id` / `at_ts` (epoch), **mutuamente exclusivos** com `before_id` (400 se combinados). A resposta ganhou `has_more_older`, `has_more_newer`, `anchor_id` e `marked_read`; **`has_more` continua** como alias de `has_more_older`. ⚠️ Qualquer âncora força `mark_read=False` **no servidor** — pular para janeiro não pode zerar o badge das não-lidas de hoje.
- **Fuso**: o cliente converte o DIA em epoch no fuso do **navegador** (`chatCalendar.dayStartTs`) e o servidor **só compara epoch** — ele nunca interpreta "dia". É o que mantém a coerência com `formatDateSeparator`.
- **Busca escopada** ([db/search/message_search.py](db/search/message_search.py), `GET /api/atendimentos/{id}/messages/search?q=`): a LISTA de ocorrências da thread, mais recente primeiro, com snippet. **Não confundir** com a busca global da sidebar ([contact_search.py](db/search/contact_search.py)), que é `DISTINCT ON (contact_id)` — um hit por contato — e ficou **intocada**. Os helpers de casamento (`_folded_match`, `SEARCH_EXCLUDED_ROLES`, `match_snippet`, `TRIGRAM_MIN_LEN`) são IMPORTADOS de lá, nunca reescritos: a expressão dobrada precisa casar byte a byte com `idx_msg_content_trgm`. ⚠️ **`_scoped()` usa uma cerca de otimização (`OFFSET 0`)** — medido em 600 mil mensagens, sem ela o planner escolhia o trigram global e filtrava a conversa depois: 4 ms num termo raro, **1,3 s** num termo comum. Com a cerca, ~50 ms constantes, limitados pelo tamanho da CONVERSA. Nenhuma migration foi necessária.
- **Cliente**: `appendNewer`/`isAnchored` ([threadData.js](web/static/js/services/threadData.js)); `loadNewer` + `loadWindow`/`jumpToMessage`/`jumpToDate`/`backToBottom` ([useConversationSelection.js](web/static/js/components/contacts/hooks/useConversationSelection.js)); a decisão do salto no módulo puro [threadJump.js](web/static/js/services/threadJump.js) (`focus` / `fetch` / `give_up` — nunca falha muda, nunca em laço); sentinela de baixo **separada** do `useReverseInfiniteScroll`; modo busca no header ([ConversationSearchBar.js](web/static/js/components/contacts/ConversationSearchBar.js)) que **substitui** a barra; calendário próprio ([DatePickerPopover.js](web/static/js/components/contacts/DatePickerPopover.js) + [chatCalendar.js](web/static/js/services/chatCalendar.js), puro). A busca cancela/invalida requests ao trocar termo/conversa ou desmontar, limpa hits antigos durante debounce/erro e serializa a paginação para não repetir offsets. O rodapé do calendário chama `backToBottom` diretamente — não finge que “hoje” é uma âncora de data.
- ⚠️ **Com a janela ancorada, um `new_message` NÃO é anexado** — colar "hoje" logo depois de "3 de janeiro" criaria um buraco silencioso no histórico. Ele vira o contador `_newWhileAnchored`, mostrado como "N novas" no botão flutuante **"Voltar ao fim"**. É a exceção ao contrato append-only do plano 28. Uma ref síncrona fica ligada desde o início do GET ancorado: visibilidade, `conversation_upsert`, reconnect e resync **não** podem marcar como lido, zerar badge nem substituir a janela pela ponta. Deep-link/hit global faz o primeiro GET já com `around_id` + `mark_read=false`. Toda saída (texto/retry, mídia/template) espera o ACK e **só depois** coalesce uma transição autoritativa para o fim; POST e GET nunca correm em paralelo.
- **Pílula de data (plano 98)**: [useChatDayHeader.js](web/static/js/components/contacts/hooks/useChatDayHeader.js) mede só `[data-day]`, coalescido por rAF. Não basta observar o scrollport: mídia e cards mudam altura interna sem alterar a altura dele. Por isso o hook observa também os filhos e mutações e captura `load`/`loadedmetadata`/`transitionend`; qualquer novo caminho de conteúdo assíncrono deve continuar disparando essa re-medição.

## Indicador de digitação entre atendentes (multi-operador)

Com dois atendentes logados (ex.: Luisa e Teste), cada um vê na **linha da conversa** quando o OUTRO está digitando ali — "**Teste está digitando…**" — para não responderem por cima um do outro. É o terceiro indicador da sidebar, ao lado de "IA respondendo…" (a IA) e "digitando…" (o cliente).

- **Sinal reaproveitado, nada novo no banco**: o compositor já mandava presença ao provedor a cada digitação (`POST /api/contacts/{phone}/presence`). A rota ([server/routes/contacts.py](server/routes/contacts.py)) agora reemite esse mesmo sinal como o broadcast WS **`operator_typing`** `{phone, channel_id, conversation_id, user_id, user_name, active}`, carimbado com o `current_user`. Emitido **antes** da ida ao provedor (canal offline ou sem capability de presence não pode calar o aviso interno) e **só com identidade de usuário** — em instalação aberta (sem login) o painel não teria como filtrar o próprio autor. Efêmero: nada é persistido.
- **Heartbeat** ([useComposer.js](web/static/js/components/contacts/hooks/useComposer.js) `PRESENCE_REFRESH_MS = 10s`): o `start` é reemitido a cada 10s enquanto o texto continua (antes ia UM só por rajada de digitação, e o indicador dos outros expiraria no meio de um texto longo). O `stop` continua no debounce de 3s de inatividade e no envio.
- **Cliente** ([useConversationWsEvents.js](web/static/js/components/contacts/hooks/useConversationWsEvents.js)): `operatorTypingState` `{chave: {userId, name}}` na MESMA `typingKey` da presença do cliente (`conv:<id>`, ou `canal::telefone` sem conversa). Ignora o **próprio** `user_id` (duas abas do mesmo login não acusam a si mesmas) e auto-limpa em **15s** (> heartbeat) caso o `stop` nunca chegue (aba fechada, queda de rede).
- **Sidebar** ([ContactList.js](web/static/js/components/contacts/ContactList.js)): precedência do preview — "IA respondendo…" → "digitando…" (cliente) → **"Fulano está digitando…"** → trecho da busca → rascunho → última mensagem.
- **Balão no chat aberto** ([ContactDetail.js](web/static/js/components/contacts/ContactDetail.js)): chip flutuante estilo Chatwoot (nome + três pontinhos pulsando) logo acima do compositor, só quando a conversa está ABERTA e o colega está digitando nela. Container de altura zero + `absolute` (`pointer-events-none`): flutua sobre o fim da conversa sem empurrar a rolagem nem o compositor.
- **Cor**: nome em `wa-text`, resto em `wa-secondary` — nas DUAS superfícies. `wa-teal` (o acento do "digitando" do cliente) mede **2,3:1** sobre `--wa-selected` no tema escuro, e a conversa aberta é justamente a que costuma ter um colega digitando; ver [themeContrast.js](web/static/js/services/themeContrast.js).

## Provider de LLM e onboarding (Techify)

O WhatsBot usa o **proxy LLM da Techify** (`https://llm.techify.one/api/v1`) como provider — API compatível com OpenRouter/OpenAI, então o cliente OpenAI (`base_url=LLM_API_BASE_URL`) e os endpoints `/models` e `/credits` funcionam sem mudança. As constantes vivem em [config/settings.py](config/settings.py) (`LLM_API_BASE_URL`, `TECHIFY_SERVICE_NUMBER_URL`, `TECHIFY_PROVISION_NUMBER`, `TECHIFY_REQUEST_APIKEY_URL`, `TECHIFY_PROVISION_MESSAGE`), todas com override por env.

**Wizard de 1ª execução** ([web/static/js/components/SetupWizard.js](web/static/js/components/SetupWizard.js), rota `/wizard`): em 3 passos —
1. **Conectar WhatsApp** (QR; auto-avança ao conectar).
2. **Provisionar chave de API**: o WhatsBot consulta `/service_number` da Techify, manda uma mensagem WhatsApp ao número de provisionamento pedindo a conta+chave (`POST /api/config/request-apikey`), faz polling até a chave chegar (com TTL) e já credita ~US$1. O contato do número de provisionamento tem a IA desativada automaticamente.
3. **Prompt do agente**: o usuário escreve a personalidade da IA. Pode pular o wizard e ir direto pro chat.

O wizard só aparece em instalações ainda não configuradas. A chave é persistida em `config["openrouter_api_key"]` (nome legado).

**Monitor de saldo** ([server/balance_monitor.py](server/balance_monitor.py)): consulta `/credits` do proxy, cacheia o resultado e, após chamadas ao LLM, emite o evento WS `low_balance` quando `remaining < low_balance_threshold` (default US$0,50, configurável; `low_balance_enabled` liga/desliga). O frontend (`LowBalanceModal.js`) abre um modal de recarga apontando para `account_url` (URL da conta Techify, salva junto com `access_token` na config). `GET /api/balance` retorna o snapshot inicial no boot.

## Motor de agente (AGNO)

O loop de raciocínio + tool calling roda no **AGNO** ([agent/agno_engine.py](agent/agno_engine.py)). O motor roda **sempre um `Agent` único** por mensagem. O `AgentHandler` continua dono de TUDO em volta (system prompt + `filter.system_prompt`, montagem do histórico + `filter.llm.messages`, lista de tools + `filter.llm.tools`, eventos `llm.before`/`llm.after`, usage, `track_step`, save da resposta, `split_messages`) e só delega o miolo a `agno_engine.run_async` / `run_sync`.

Pontos-chave da integração:

- **Stateless por requisição**: um `Agent` novo é montado por mensagem, para os closures de tool capturarem o coletor `executed` daquela request sem cross-talk entre contatos concorrentes.
- **WhatsBot é dono do contexto**: o engine NÃO recebe `db` nem deixa o AGNO montar contexto próprio (`build_context=False`, `add_history_to_context=False`, etc.). O system prompt (já filtrado) vira `system_message`; o histórico (já filtrado) é convertido em `agno.models.message.Message` e passado como `input`.
- **Tools**: cada schema OpenAI registrado é embrulhado num `agno.tools.function.Function` (`skip_entrypoint_processing=True`) cujo entrypoint reaplica `filter.tool.args`/`filter.tool.result` e emite `tool.before`/`tool.after` — mesma semântica do dispatch antigo. Async path usa entrypoint assíncrono; sync path usa síncrono.
- **Usage**: lido de `run_output.metrics` (`RunMetrics.input_tokens/output_tokens`) e gravado via `AgentHandler._record_usage_tokens` (em vez de `response.usage`).
- **Reply**: `_extract_reply` pega a ÚLTIMA mensagem `assistant` sem tool calls de `run_output.messages` (fallback: `run_output.content`). Isso evita que o AGNO concatene um "chatter" pré-tool com a resposta final — crítico com `split_messages` (saída JSON array) ligado.
- **Transcrição/descrição de mídia** continuam em chamadas diretas ao cliente OpenAI no handler (não são agênticas) — o cliente OpenAI segue vivo só para isso e para `test_api_key`.

O motor roda **sempre um `Agent` único**. A base extensível para configurar agentes via banco (prompt/modelo/tools lidos do DB) é a infra `ai_agents` + [agent/agent_factory.py](agent/agent_factory.py) — também single-agent. O **prompt de cada agente é inline** (coluna `ai_agents.prompt`): cada agente escreve o próprio prompt no formulário do agente (tela Configurações de IA → Agentes), sem seleção de template compartilhado. `build_for_contact` lê `agent["prompt"]` direto (fallback para o seed `DEFAULT_SYSTEM_PROMPT` quando vazio) e resolve `{placeholder}` via `ai_variables`. A coluna `prompt_key` e a tabela `ai_prompts` são legado e não participam mais da resolução.

**Agente padrão / fallback unificado (fix 2026-07)**: a marcação "Padrão para novas conversas" (`ai_agents.is_default`, radio — plano 36) é TAMBÉM o fallback de runtime. `agent_repo.get_default()` resolve: `is_default` habilitado → agente de chave literal `default` (piso legado) → `None` (piso de emergência in-code do plano 34). Vale para o carimbo de criação (`default_agent_key_for_inbox`), o religar da IA (`set_ai` ON) e a cascata `build_for_contact` — uma conversa sem `active_agent_key` (ex.: pós fechar→reabrir automático, que limpa o vínculo e não re-vincula) cai no agente MARCADO, não mais na chave `default` hardcoded (era o bug: fechar+reabrir voltava ao "Agente padrão" ignorando a marcação). Guards de exclusão/desativação são **semânticos** (`agent_repo.get_fallback_key()`), não por nome: o fallback ATUAL não pode ser excluído nem desativado; o `default` legado É excluível/desativável quando outro agente carrega a marcação, e a exclusão limpa os vínculos (`atendimentos.active_agent_key`/`inboxes.default_agent_key` → NULL, mesma transação). O seed do boot só cria o `default` em instalação nova (tabela `ai_agents` vazia — gate em `server/app.py`); um `default` excluído não ressuscita. Migration `0055` desvinculou conversas presas na chave `default`. **Nascimento com IA desligada** (fix atribuição-IA-off, 2026-07): o carimbo do agente na criação segue o mesmo princípio do gate global `auto_reply` — uma conversa que nasce `ai_active=0` NÃO carimba agente (fica de fato "Não atribuída": sem humano e sem agente); religar a IA da conversa re-vincula o padrão. O seed do `ai_active` é resolvido na HORA do create ([agent/memory.py](agent/memory.py) `_resolve_ai_seed`, cache 30s do `ai_settings` — não mais congelado no construtor) e exige o master do canal (`ai_enabled`) E o `default_ai_enabled` per-canal. Migration `0056` desvinculou as rows já inconsistentes (IA off + sem humano + agente).

### Guardrails e routing hub-and-spoke (plano 29 Eixo A/B)

Multi-agente segue o padrão **hub-and-spoke** (portado do nexus `gerenciamento-ia`): **um único roteador** (`is_router`, enforced por semântica radio em `agent_repo.save` + índice único parcial `ux_ai_agents_single_router`, migration 0035) roteia via `transferir_agente`; spokes devolvem ao roteador com motivo; **só o roteador tem `transfer_to_human`** (convenção via `tool_names`, com aviso na UI quando um spoke a seleciona).

- **Routing within-turn** ([ai_engine/routing.py](ai_engine/routing.py)): `run_with_routing` é puro (sem DB) e retorna `(result, steps, halted)`. `steps` = `{from, to, depth, reason}` — o `reason` é o motivo real da `transferir_agente`, threadado ao próximo hop como msg sintética `[REDIRECIONAMENTO de {agente}]\nMotivo: …` ([app/services/agent_run_service.py](app/services/agent_run_service.py)). **Revisita é permitida** (`roteador→comercial→roteador` no mesmo turno; `is_reinvoke` sinaliza re-invocação), barrando só `A→A` imediato; profundidade limitada por `ai_max_route_depth` (config, default 5). Cap estourado com handoff pendente → o caller força `transfer_to_human` (motivo "Limite de roteamento atingido…") + `track_step("routing_halted")`.
- **Guardrails de tool** ([ai_engine/hooks.py](ai_engine/hooks.py)): `requires_prior_call` (str ou lista) é **success-aware** — prior que retornou falha (`_FAILURE_MARKERS`/prefixo "erro") não libera; mensagens de bloqueio citam a rota de escape (`transferir_agente`); `ai_tool_call_limit_per_tool` (config, 0=off) aplica um `call_limit` default a toda tool sem limite próprio.
- **Teto global**: `Agent(tool_call_limit=…)` do AGNO (overflow gracioso) — env `WHATSBOT_TOOL_CALL_LIMIT` > config `ai_tool_call_limit_total` (default 25; 0 desliga).
- **Gate de humano** ([app/services/messaging_service.py](app/services/messaging_service.py) `_conversation_ai_active`): a IA cala quando `ai_active=0` OU a conversa tem `assignee_user_id` humano (plano 96 D2 — **independe** de `active_agent_key`; a condição antiga "sem agente vinculado" era cara ou coroa, já que o único escritor de `active_agent_key` é a tool `transferir_agente`). Sem `assignee`, nada muda: "IA ativa sem subagente" continua válida. A tag `transferido_atendente` **não é mais aplicada**: deixou de ser lida como trava no plano 37 (era contact-global — transferir num canal silenciava o irmão) e sobrou como rótulo visual até ser removida da `transfer_to_human`. A constante `TRANSFER_TAG` continua exportada porque ainda é CONSUMIDA (o `_clear_transfer_tag` em `conversation_service` a remove quando a IA reassume, drenando as linhas antigas; e os plugins `retornos`/`vendas_ia`/Meta a importam) — **ninguém mais a escreve**.

## Humano no comando cala a IA (plano 96)

Atribuir uma conversa a um atendente, desligar a IA ou simplesmente **enviar** uma mensagem interrompem a resposta da IA **que já está a caminho** — antes o gate era lido UMA vez, antes do LLM, e nada no painel tinha alavanca sobre o ciclo (a resposta indevida saía de 1,6s a 75s depois do clique).

- **Veredito único** ([messaging_service.py](app/services/messaging_service.py) `MessagingService.ai_may_speak`): compõe as três camadas (global `auto_reply` + `ai_enabled` do canal + gate por-conversa) numa pergunta só, consultada nos 3 call sites do ciclo **e de novo na hora de falar**. `_ai_may_speak_now(channel_id, phone)` reresolve o contato do zero; **fail-open** em qualquer erro (falso negativo calaria uma conversa saudável).
- **Guards de envio**: `_send_with_typing_guard` reconsulta DEPOIS da espera de digitação; e o laço do split reconsulta no último ponto antes de **cada** parte, inclusive a primeira (depois do delay humanizado e dos filtros). Partes já entregues ficam, o resto é abortado num limite limpo. O retorno é `bool` (alguma parte chegou ao wire), então `ai_takeover` só é emitido após envio real. Se nada saiu, não incrementa `msg_count` nem cria `response_sent`; se houve envio parcial, `response_sent`/`output_text` registram só `sent_parts`.
- **Seam de aborto por geração** (`abort_ai_cycle(deps, channel_id, phone)` / `abort_ai_cycle_for_conversation(deps, conv)`): toda chamada incrementa `state.ai_abort_epochs[(channel_id, phone)]`. O ciclo carrega o snapshot obtido no instante do agendamento; mismatch cala seus próximos wire sends. A task só é cancelada nas fases seguras — com `state.sending`/`state.processing` ligados ela continua para não perder o batch já removido de `pending_messages`, mas ficou invalidada e não fala. Best-effort, nunca levanta. Chamado por `conversation_service` (`assign`/`assign_me`/`assign_unified kind=user`/`set_ai(0)`) e pelas rotas de envio do operador (`_operator_took_over` em [contacts.py](server/routes/contacts.py)).
- **Todo caminho de atribuição cala**: `assign` (tela Atendimentos, plugin `agendamento_retorno`) e `assign_me` passaram a rotear pelo mesmo `_transfer` que o `assign_unified` já usava — `active_agent_key=None`, `ai_active=0`, `mirror_contact_ai=None` (o gate do CONTATO não é tocado: mudaria o contrato de evento). ⚠️ **Desatribuir (`assignee_user_id=None`) continua não tocando na IA** — soltar uma conversa não pode devolvê-la ao bot sem ninguém pedir.
- **Atendente digitando SEGURA** (não cancela — ele pode desistir do texto): a rota de presença escreve `state.operator_typing_state[(channel_id, phone)]` e `_wait_typing_paused` espera por **cliente OU operador**. Obsolescência de 15s (o painel reemite `start` a cada 10s), contra os 25s do cliente; o teto de 30s vale para os dois.
- **IA da nota privada**: `_run_private_ai` consulta o veredito **só** quando `reply_in_chat=True` (era o único caminho de saída sem gate nenhum), no fim do LLM e novamente antes de cada parte; também carrega o epoch capturado quando a task foi agendada. Bloqueado, grava um card `system_notice` explicando. Com `reply_in_chat=False` a resposta vira nota privada e nunca é gateada.
- **O selo conta a verdade** ([conversationRows.js](web/static/js/services/conversationRows.js) `aiEffectivelyOn(row, {autoReply})`): helper PURO que espelha o gate — global off, `conv_ai_active` 0/false, **ou** `assignee_user_id` preenchido ⇒ "IA OFF". Consumido pelo selo da sidebar ([ContactList.js](web/static/js/components/contacts/ContactList.js)) e pela dimensão `ai` do filtro (selo e filtro divergirem seria o mesmo bug duas vezes). Dois estados só — não há "IA pausada".

### A IA pode se despedir ao transferir (plano 122) — o perdão de escopo mínimo

⚠️ **`transfer_to_human` fecha o gate DENTRO do turno** ([transfer_to_human.py:87-89](agent/tools/transfer_to_human.py) grava `ai_active=0`), e o guard acima, ao reconsultar, descartava a **despedida que aquele mesmo turno acabou de escrever** — a IA calando a si mesma. Não era só bloqueio: como o save só roda para `sent_parts`, a mensagem sumia sem rastro e nem o operador via no painel o que a IA queria dizer. Foram **226 transferências mudas** em produção entre 31/07 e 14/08 (de 178/178 com despedida na semana de 20/07 para 0/115 na de 03/08).

O conserto é um kwarg **`allow_self_handoff`** em `_cycle_may_continue` / `_send_with_typing_guard` / `send_reply`, derivado pelo call site do próprio turno via `_turn_handed_off(result.tool_calls)` (predicado de módulo, exige `not skipped`). Regras que **não** podem ser mexidas:

- **A época vem PRIMEIRO e o perdão nunca a alcança.** É o que preserva o plano 96 inteiro: toda tomada humana passa por `abort_ai_cycle`, que incrementa `state.ai_abort_epochs` **antes de qualquer outra coisa** — inclusive quando se recusa a cancelar a task por estar em `sending`/`processing` —, enquanto `transfer_to_human` não toca na época. Inverter as duas linhas devolve o bug do plano 96 **em silêncio**; travado por `test_perdao_nao_sobrevive_a_atribuicao_humana`.
- **O perdão é parâmetro de chamada**, não `ContextVar` nem flag em `AppState`: morre no `return` e não vaza para o turno seguinte.
- **O card "🤖 A IA assumiu a conversa" fica no predicado ESTRITO** — `maybe_emit_ai_takeover` NÃO recebe o perdão. Um turno que terminou em transferência não é um takeover; o fio ficaria absurdo (*"SISTEMA pausou a IA"* seguido de *"A IA assumiu a conversa"*). A assimetria é proposital e está comentada nos dois call sites.
- Vale nos **três** caminhos de saída — batch de texto, batch de mídia e a IA da nota privada (que, sem isso, ainda gravava um card **falso** dizendo que *"um atendente assumiu a conversa"*).
- O log do guard agora diz **qual** dos dois motivos cortou (`_guard_reason`: época × gate) — antes fundia os dois numa frase só.

## Fotos de perfil (avatars)

[server/avatars.py](server/avatars.py) cacheia as fotos de perfil em disco em `statics/avatars/<phone>.jpg` (servidas pelo mount estático). Como o WhatsApp não emite evento de "foto mudou", a atualização é por re-fetch do GOWA (ao abrir a conversa e numa varredura periódica de fundo — `AVATAR_REFRESH_INTERVAL = 1800s` em [server/background.py](server/background.py)), sobrescrevendo o arquivo só quando os bytes diferem. O frontend faz cache-bust pelo mtime (`avatar_v`); uma mudança dispara o WS `avatar_updated` `{phone, v}` pra atualizar ao vivo sem reload.

## @menções em grupos

[agent/group_mentions.py](agent/group_mentions.py) é o serviço central que conhece os participantes de um grupo e converte menções entre o formato de fio do WhatsApp (`@<número>`) e nomes humanos:

- **Entrada**: `resolve_incoming()` troca `@<dígitos>` numa mensagem recebida por `@<Nome>` (o painel/LLM veem nomes, não números).
- **Saída**: `resolve_outgoing()` transforma `@Nome` / `@todos` (escritos pelo operador ou pela IA) em menção real — `@<número>` inline no texto + a lista `mentions` que o `/send/message` do GOWA aceita. `@todos`/`@geral`/`@all`/etc. viram `@everyone`.

Nomes não vêm do GOWA (`DisplayName` volta vazio): são resolvidos de contatos salvos → pushName capturado de mensagens recebidas → catálogo do device (`/user/my/contacts`) → `/user/info` (cap de 20 lookups por chamada). Participantes são indexados por dígitos do phone **e** do `lid`. Cache de membros por grupo (TTL 300s), invalidado em mudança de roster (join/leave/promote/demote, via webhook `group.participants_changed`). O serviço é inicializado em `create_app` (`group_mentions.init(gowa_client)`) e a identidade do bot é registrada via `set_bot_identity`. A config `group_reply_mode` (default `mention_only`) controla quando a IA responde em grupos.

## API REST do WhatsBot (backend FastAPI)

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| GET | `/` | Serve o frontend (web/index.html) |
| GET | `/wizard` | Serve o frontend forçando o wizard de 1ª execução (onboarding) |
| GET | `/api/config` | Retorna config (API key mascarada) + `account_url` + `public_base_url`. **Efeito colateral**: captura e persiste `public_base_url` (config key global — a URL que o operador usa pra acessar o painel, honrando headers de proxy reverso) no 1º acesso, com self-heal se o salvo era localhost. Variável reutilizável por qualquer feature (ex.: o alerta de desconexão do plugin gowa monta o link de reconexão a partir dela) |
| PUT | `/api/config` | Salva config + atualiza AgentHandler |
| POST | `/api/config/test-key` | Testa API key no proxy Techify (compatível OpenRouter); auto-salva se válida |
| POST | `/api/config/request-apikey` | Provisiona uma chave via Techify (manda msg ao número de provisionamento; usado pelo wizard) |
| GET | `/api/models` | Lista de modelos do proxy (cache 10 min) |
| GET | `/api/balance` | Saldo de crédito atual + threshold + `account_url` (recarga). Updates live via WS `low_balance` |
| GET | `/api/status` | Status de conexão + contagem de msgs |
| GET | `/api/qr` | QR code como PNG (204 se indisponível) |
| POST | `/api/whatsapp/reconnect` | Reconectar GOWA |
| POST | `/api/whatsapp/logout` | Logout GOWA |
| POST | `/api/webhook` | Recebe mensagens do GOWA (webhook) |
| GET | `/api/contacts?archived=true` | Lista apenas contatos/grupos arquivados |
| GET | `/api/contacts/unread-count` | Total de mensagens não lidas (badge global) |
| POST | `/api/contacts/{phone}/pin` | Fixa/desafixa a conversa (`{pinned}`). Fixadas vão pro topo da lista. WS `contact_pinned` |
| POST | `/api/contacts/{phone}/unread` | Marca a conversa como não lida (manual) |
| POST | `/api/contacts/mark-all-read` | Zera não lidas de todas as conversas |
| POST | `/api/contacts/mark-all-unread` | Marca todas as conversas como não lidas |
| POST | `/api/contacts/{phone}/messages/react` | Reage a uma mensagem com emoji (string vazia remove). WS `message_reaction` |
| POST | `/api/contacts/{phone}/messages/delete` | Apaga mensagem (revoke pra todos). WS `message_revoked`/`message_deleted`. Só aparece na UI em canais com a capability `revoke` (GOWA + Telegram sim; WhatsApp Cloud não) |
| POST | `/api/contacts/{phone}/messages/edit` | Edita o texto de uma mensagem de SAÍDA (operador/IA) já enviada. Body `{msg_id, text, conversation_id?}`. Só texto, roteado pelo canal via `outbound.edit_text` (capability `edit_message`); grava `edited_ts` + WS `message_edited`. Suportado em GOWA (`/message/{id}/update`) + Telegram (`editMessageText`); **WhatsApp Cloud NÃO** edita nem apaga (ambas as capabilities off — único canal sem as duas opções no menu) |
| GET | `/api/contacts/{phone}/members` | Lista participantes do grupo com nomes resolvidos (autocomplete de @menção) |
| GET | `/api/atendimentos/{id}/messages` | Thread de UMA conversa. Paginação keyset `before_id` + **janela ancorada** `after_id`/`around_id`/`at_ts` (plano 99, mutuamente exclusivos). Resposta: `has_more`(=`has_more_older`), `has_more_newer`, `anchor_id`, `marked_read` |
| GET | `/api/atendimentos/{id}/messages/search?q=` | Busca de texto DENTRO da conversa → `{matches:[{id,ts,role,snippet}], total}`, mais recente primeiro. `q` < 3 chars ⇒ vazio com 200. Gate `conversation.read` |
| GET | `/api/webhook-payloads?limit=N` | Últimos N payloads raw do webhook (debug, max 50) |
| GET | `/api/gowa-logs?limit=N` | Tail do `logs/gowa.log` (stdout/stderr do subprocess GOWA, só populado com `WHATSBOT_GOWA_DEBUG=1`) |
| GET | `/api/tools` | Lista todas as tools registradas (core + plugin) com estado de override |
| PUT | `/api/tools/{name}` | Atualiza override `{enabled?, description?, display_label?}`; `description=null` reseta |
| GET | `/api/plugins` | Lista todos os plugins descobertos com status (ativo/inativo/erro) |
| GET | `/api/plugins/manifest` | Manifest público dos plugins ativos (pro frontend dinâmico) |
| POST | `/api/plugins/{id}/enable` | Ativa o plugin e dispara restart |
| POST | `/api/plugins/{id}/disable` | Desativa o plugin e dispara restart |
| GET/PUT | `/api/plugins/{id}/settings` | Schema Pydantic + values do plugin (settings declarativas) |
| GET | `/api/plugins/{id}/export` | Baixa o plugin como `.zip` |
| POST | `/api/plugins/import` | Importa um plugin via upload de `.zip` |
| DELETE | `/api/plugins/{id}` | Remove a pasta + tabelas `plugin_<id>_*` + settings namespaceadas |
| POST | `/api/plugins/restart` | Restart manual do servidor |
| `*` | `/api/plugins/{id}/*` | Endpoints REST mountados pelo plugin (router próprio) |
| GET | `/api/admin/database` | Info do backend atual (dialect, URL redacted, caminho do config) |
| POST | `/api/admin/repair-sequences` | Re-ancora as sequences do Postgres em MAX(pk) (recovery pós-import manual) |
| WS | `/ws` | WebSocket para eventos real-time |

Formato de resposta REST: `{"ok": bool, "data": ..., "error": ...}`

Eventos WebSocket (frontend): `{"event": "...", "data": {...}}` — inclui `status`, `qr_update`, `gowa_status`, `config_saved`, `new_message`, `message_reaction`, `message_revoked`, `message_deleted`, `message_edited` (`{phone, msg_id, db_id, content, edited_ts}` — mensagem editada, seja de SAÍDA pelo operador/IA OU INBOUND quando o cliente edita a própria mensagem no WhatsApp/Telegram; o painel troca o conteúdo in-place e mostra "editada". WhatsApp Cloud não emite edição inbound), `contact_pinned`, `group_participants_changed`, `avatar_updated` (`{phone, v}` — `v` = mtime do arquivo, usado pra cache-bust da foto), `low_balance` (saldo abaixo do threshold → abre o modal de recarga), `ai_typing` (`{phone, channel_id, active}` — a IA está processando uma resposta para a conversa; o painel mostra "IA respondendo…" no header para o operador não responder por cima), `operator_typing` (`{phone, channel_id, conversation_id, user_id, user_name, active}` — OUTRO atendente está digitando naquela conversa; a linha da sidebar mostra "Fulano está digitando…" para os demais operadores logados. Ver "Indicador de digitação entre atendentes").

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
- **Tools de plugin**: viver em `storages/plugins/<id>/tools.py` no formato `CORE_TOOLS = [(schema, executor), ...]` e ser declaradas no manifest. NÃO mexer em `agent/tools/` ou no handler
- **Contrato de tool (core OU plugin)**: toda tool registrada vira row em `tool_overrides` automaticamente (via `tool_override_repo.ensure` no `_register_tool`). O usuário pode customizar `description` e `display_label` na tela `/tools`. O `name` da tool é IDENTIDADE e NÃO deve ser renomeado depois de release — quebra histórico de `usage` (`call_type=<name>`) e overrides do usuário. Description em código é o **default**: escreva como instrução clara pro LLM, deve funcionar sem customização. O schema também aceita `"display_label": "..."` no dict raiz (fora de `function`) — o handler retira antes de mandar pro LLM, e o valor vira o default mostrado na UI
- **Acesso a dados**: sempre via SQLAlchemy Core. Repos em `db/repositories/` usam `with get_engine().begin() as conn:` + statements de `db/tables`. Nunca usar `sqlite3` diretamente. Plugins acessam o banco via `from plugins.context import make_plugin_db` + `from sqlalchemy import text`

## Tema e modo escuro (legibilidade)

O painel suporta **modo claro e escuro**. O tema é a classe `.dark` no `<html>` (toggle no menu da engrenagem → "Modo escuro", persistido em `localStorage["whatsbot_theme"]`; um script inline no `<head>` do `web/index.html` aplica antes do 1º paint pra não piscar). As cores são dirigidas por **variáveis CSS (canais RGB)** em [web/static/css/custom.css](web/static/css/custom.css): a paleta `wa-*` do Tailwind (`bg-wa-panel`, `text-wa-text`, `border-wa-border`, …) resolve para `rgb(var(--wa-*) / <alpha-value>)` (config em `web/index.html`), então alternar a classe re-tematiza o app inteiro e os modificadores de opacidade (`bg-wa-teal/10`) continuam funcionando.

**REGRA — ao adicionar QUALQUER área nova (tela core, card, modal, tela de plugin), garanta que as cores sejam legíveis no modo escuro.** Na prática:

- **Prefira as classes semânticas `wa-*`** para superfícies/textos/bordas (`bg-wa-bg`, `bg-wa-panel`, `text-wa-text`, `text-wa-secondary`, `border-wa-border`, `bg-wa-hover`, `bg-wa-teal`). Elas trocam de cor sozinhas nos dois temas — é o caminho recomendado e à prova de futuro.
- **Não dependa de cores cruas do Tailwind** (`bg-white`, `text-gray-*`, `bg-green-50`…) nem do fundo padrão do navegador em inputs. Como rede de segurança, `custom.css` tem overrides `html.dark` que re-tematizam as cruas mais comuns (brancos, cinzas `50–300`, e as tintas de acento green/red/amber/yellow/blue/orange/purple/pink em `-50/100/200` + textos `-600/700/800`). Isso é **fallback**, não substitui usar `wa-*` — cores fora dessa lista (ex.: um hex inline, um `bg-*-300` de fundo, uma cor nova) NÃO são cobertas e ficarão ilegíveis.
- **Campos de formulário**: use a classe `.wa-field` (fundo cinza + texto preto, legível nos dois temas) em `<input>`/`<textarea>`/`<select>`. Deixar sem cor de fundo cai no branco padrão do navegador + texto claro do tema = ilegível.
- **Controles nativos** (date/time/range/checkbox/scrollbar) seguem o tema via `color-scheme` (já setado em `:root`/`html.dark`).
- **Acentos** (`text-white` em botão colorido, vermelho de "excluir") podem ficar como estão.
- **Sempre teste**: abra a tela, ligue o modo escuro e confira o contraste. Se uma cor crua não estiver coberta, ou troque por `wa-*`/`.wa-field`, ou adicione o override `html.dark` correspondente em `custom.css`.

Telas de plugin (`storages/plugins/<id>/static/*.js`) seguem as MESMAS regras — usam o mesmo runtime do Tailwind e o mesmo `custom.css`.

## Dados do projeto

Dados de banco vivem no Postgres apontado por `DATABASE_URL`; no filesystem (raiz do projeto em dev, bind mounts no Docker) ficam:
- `storages/` — dados do GOWA (sessão WhatsApp) + plugins do usuário
- `logs/` — logs com rotação
- `statics/outbox/` — mídia enviada pelo operador
- **Webhook payloads (debug)**: últimos 50 payloads raw do GOWA em memória, acessíveis via `GET /api/webhook-payloads`
- **Contatos arquivados**: ao receber mensagem de um contato, o webhook consulta `gowa_client.is_chat_archived(jid)` e persiste `is_archived` na tabela `contacts`. A sidebar filtra por `?archived=true/false`. O status de archive é atualizado on-demand (não por polling)

## Sistema de plugins

Plugins são extensões opcionais isoladas em `storages/plugins/<id>/` (volume Docker / pasta separada no Windows, ignorada por updates). Um plugin pode agregar:

- **Tools** para o agente LLM (registradas no mesmo registry das tools core)
- **Prompt fragments** injetados dinamicamente no system prompt
- **Endpoints REST** sob `/api/plugins/<id>/...`
- **Tela Preact** carregada via `import()` ES dinâmico
- **Migrations SQL** com prefixo `plugin_<id>_` obrigatório
- **Settings declarativas** via Pydantic (form auto-gerado pela UI)
- **Broadcast WebSocket** via `from plugins.context import broadcast; broadcast("evento", {...})` — thread-safe, fire-and-forget, ws_manager + loop são injetados no startup do server. Use pra empurrar atualizações em tempo real à tela do plugin (a tela escuta `/ws` e filtra pelo nome do evento).

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

1. **Bootstrap**: com `storages/plugins/` vazio, `plugins.bootstrap.bootstrap_initial_plugins()` semeia **somente `gowa`** a partir de `assets/plugin_examples/gowa/` (`BUNDLED_AUTO_INSTALL`) — que é o **único** diretório ali. Qualquer outro plugin entra por `Importar (.zip)`.
2. **Discovery**: `discover_and_load(plugins_dir)` escaneia o filesystem, parseia cada manifest, faz `upsert` na tabela `plugins`.
3. **Migrations**: para plugins com `enabled=1`, `run_pending_migrations` aplica SQL files em ordem numérica. Naming `NNN_descricao.sql`. O migrator valida regex que toda `CREATE TABLE`/`ALTER TABLE`/`CREATE INDEX` use prefixo `plugin_<id>_`.
4. **Import**: `importlib.spec_from_file_location` registra o pacote como `whatsbot_plugins.<id>`. Submódulos declarados no `entry:` são importados sob demanda.
5. **Wiring**: `agent_handler.register_plugin_tools/prompts` adicionam ao registry. `app.include_router` monta o router em `/api/plugins/<id>`. `app.mount` serve `static/` em `/plugins/<id>/static`. `screens[].path` é registrado como rota SPA dinâmica.
6. **Toggle**: enable/disable atualiza a tabela `plugins` e dispara `schedule_restart` (`os._exit(0)` após delay; supervisor relança — Coolify/Docker `restart: unless-stopped` ou launcher do EXE).

### Settings declarativas (Pydantic Valves)

Plugin declara `class Settings(BaseModel)` em `settings.py`. O endpoint `GET /api/plugins/<id>/settings` retorna `model_json_schema()` + valores atuais; `PUT` valida via Pydantic e persiste em `config_repo` com prefixo `plugin.<id>.<field>`. Frontend (`PluginSettingsForm.js`) renderiza form genérico para string/int/float/bool/enum.

### O que fica no core e o que vai pro plugin (REGRA DE DECISÃO)

**Tudo que puder ir para o plugin vai SÓ para o plugin, com o mínimo possível no core.** Um recurso só merece **ramo, evento ou campo novo no core** quando os **três** critérios forem verdade ao mesmo tempo:

1. **≥ 2 consumidores previstos** — reais, não hipotéticos;
2. **nenhum gancho existente enxerga o sinal**;
3. **usar o gancho existente custaria caro no caminho quente**.

Falhando **qualquer um**, o comportamento de negócio vai inteiro para o plugin — mesmo que o gancho disponível seja mais tosco que o ramo que você desenharia. A exceção é a **fronteira de confiança**: identidade/resolução da rota, veredito de assinatura e autorização permanecem no core e entregam ao plugin apenas o contexto já validado. Precedente conta como evidência: se outro plugin já resolve o mesmo problema pelo mesmo gancho em produção, o gancho está provado e o ônus da prova é de quem quer o ramo no core.

**Por que a regra existe (não é estética):** reduzir seams reduz acoplamento e ordem de deploy, mas não autoriza esconder dependências. A fronteira de confiança (identidade da rota, veredito de assinatura, autorização) continua no core mesmo com um único consumidor de negócio: plugin não deve autenticar a si próprio a partir de uma rota pública. Critério de aceite: **o plugin carrega num core da release anterior; qualquer feature que exija seam novo degrada explicitamente e documenta “core antes do zip”**. No plano 84, por exemplo, o alerta de webhook da Meta degrada fechado sem `ctx.extras`, enquanto polling e `message.failed` continuam funcionando.

**Casos que fixaram a regra:** plano 84 (o motor dos avisos da conta Meta cabe em `filter.webhook.payload`; o ramo por `kind` foi revertido, e ficou no core só o seam genérico de procedência/autenticação necessário para o filtro não confiar em uma rota pública); plano 76 · F9 (`MetaGraphChannel` desceu para dentro do plugin, e o Instagram carrega a **própria cópia** em vez de importar a do Messenger — *dois canais Meta, duas cópias, preço do zip autossuficiente*); plano 92 · B1 (o modal "Enviar template" virou do plugin via `overrideComponent`).

⚠️ **"Não muda o core" ≠ "não depende do core".** Um plugin extraído continua importando `db.repositories`, `plugins.context`, `runtime.supervisor`, `server.message_errors` — e **nenhum desses é API declarada**. A superfície declarada (catálogos do bus, `plugins.context`, schema do manifest + `entry`, `channels.base`/`channels.events`, convenções de host) é a versionada por `WHATSBOT_API_VERSION` e travada por [tests/contracts/test_plugin_api_surface.py](tests/contracts/test_plugin_api_surface.py) — ver "Versionamento da API de plugins"; `db.repositories` e companhia ficam **de fora de propósito** (snapshotar a camada de dados inteira tornaria o número inútil por ruído). Até 2026-08-11 a constante esteve congelada em `1.0.0` e o guard nunca rejeitou nada; da `1.1.0` em diante ela anda, mas isso **não** promove esses módulos a API. Por isso **todo import além do mínimo continua defensivo**: `try/except` que degrada em vez de quebrar. Import não-defensivo de módulo que mudou = o plugin **nem carrega**, falha muda no boot.

**Contrato do observador de `filter.webhook.payload`** (o gancho que torna plugin de canal viável fora do core — mesmo padrão em `janela_72h`, `debug_bus` e `whatsapp_cloud`):

- **devolver `None` DESCARTA a mensagem inbound** — o core responde 200 sem processar; um observador que erre isso derruba a caixa de entrada;
- **`ctx.extras` traz procedência resolvida**: `{provider, channel_id, signature_authenticated}`. O core só chama o filtro depois de confirmar que o canal existe, pertence ao provider da rota, está materializado no registry e passou o veredito atômico `verify_inbound_signature_result`; para GOWA multi-device, `channel_id` já é o device/canal final mesmo se a antiga URL `default` sumiu. `signature_authenticated=True` só quando o MESMO snapshot que aceitou o corpo também confirmou o HMAC — hoje, no WhatsApp Cloud; aceitar por compatibilidade não conta como autenticação;
- observador com efeito externo deve validar de novo provider/canal e casar a identidade do payload (`entry[].id`/WABA id, `page_id`, `bot_id`) com a credencial **exata daquele canal**. Sem contexto/assinatura/identidade, **fail-closed**; nunca usar fallback “único canal” numa rota pública;
- **prioridade: número MENOR roda ANTES** — observador usa 9000 para nunca disputar com filtro que de fato transforma;
- roda para **todos** os providers em **todo** inbound (call site único) — o guard tem de sair na **primeira comparação**;
- trabalho de banco/rede é **offloaded** para fora do request (`loop.create_task` guardando **referência forte** da task).

Um plugin autônomo persiste estado em `plugin_<id>_*` (nunca em memória — o toggle derruba o processo), agenda com `ctx.spawn_task` + `RestartPolicy.PERMANENT`, e nasce com agregação/cooldown se emite alerta. E leva **ao menos um teste que sobe o app pelo loader real e bate no endpoint real**: teste que carrega o módulo por caminho continua **verde com a costura arrancada**.

Ver [docs-planos/100-plano-devolver-ao-plugin-o-que-e-do-plugin.md](docs-planos/100-plano-devolver-ao-plugin-o-que-e-do-plugin.md) — inclui a lista dos candidatos já auditados e **refutados** (parecem acoplamento e não são).

### Onde fica a configuração de um plugin (REGRA)

**Toda configuração de um plugin vive na aba de configuração DO PRÓPRIO plugin** — o botão **Configurar** no card em *Gerenciar Plugins* (`/plugins`). **Nunca** adicione uma seção/aba nova ao painel de Configurações padrão do WhatsBot ([web/static/js/components/ConfigPanel.js](web/static/js/components/ConfigPanel.js)) para algo que pertence a um plugin. O core não deve crescer com opções de plugin.

Há dois jeitos (escolha um, ou combine) de preencher o modal "Configurar":

1. **Settings declarativas** (`settings.py` → `class Settings(BaseModel)`): form auto-gerado pelo `PluginSettingsForm`. Use quando as opções são campos simples (string/int/float/bool/enum) persistidos no servidor (`plugin.<id>.<field>`).
2. **Tela de configuração custom** (`screen` com `config: true`): um componente Preact próprio, renderizado dentro do mesmo modal "Configurar" via `PluginScreen`. Use quando precisa de UI rica (toggles que aplicam na hora, preferências em `localStorage` per-device, upload, preview de som, etc.). Quando o plugin tem uma screen `config: true`, o modal renderiza ela **no lugar** do form declarativo.

Referências: `auto_signature` (settings declarativas, na Loja de Plugins — repo *community*) e as screens `config: true` de `gowa`/`telegram`/`whatsapp_cloud`, que combinam as duas coisas.

⚠️ **`custom_sounds` e `notifications` NÃO são mais plugins** (medido em 2026-07-31): o subsistema de som foi absorvido pelo core na direção CONTRÁRIA (plugin → core) e hoje vive em [server/sound_catalog.py](server/sound_catalog.py), [server/routes/sound_prefs.py](server/routes/sound_prefs.py), [db/repositories/custom_sound_repo.py](db/repositories/custom_sound_repo.py), a tabela `custom_sounds` e o componente core [SoundSettings.js](web/static/js/components/SoundSettings.js). Nenhum dos dois está instalado em `storages/plugins/`.

### Frontend dinâmico

`/api/plugins/manifest` retorna apenas plugins carregados com seus `screens[]`. `app.js` faz fetch no boot e separa as screens por flag `config`:

- **`config: false`** (default) — screen "de funcionalidade": aparece como página no `GearMenu` (menu da engrenagem) e é renderizada full-page via `PluginScreen`. Ex: uma tela de listagem/operação do plugin.
- **`config: true`** — screen "de configuração": **filtrada fora do GearMenu** (`app.js` faz `.filter(s => !s.config)`) e renderizada dentro do modal **Configurar** do card em `/plugins` (`PluginsManager.js`). É a aba de configuração do próprio plugin.

`PluginScreen` faz `import(screen.component)` dinâmico e passa `apiBase = "/api/plugins/<id>"` como prop. Importmap em `web/index.html` cobre `preact`, `preact/hooks`, `htm` — plugin usa os mesmos sem bundle. Screen custom pode importar utilitários do core por URL absoluta (ex: `import { playNotificationSound } from '/static/js/utils/notifications.js'`).

🚫 **Tela de plugin NUNCA abre `new WebSocket('/ws')`** (plano 107). O socket cru não leva o `?token=` e o servidor o fecha com **4401** assim que existe ≥1 usuário ([websocket.py](server/routes/websocket.py) — o gate do plano 48 F0). É uma falha **silenciosa e permanente**: sem `onerror`/`onclose` nada é logado, e a tela simplesmente para de atualizar sozinha (foi assim que `protocolos`, `agendamento_retorno`, `lembretes` e a tela core `/tools` passaram meses sem tempo real, cada um com o mesmo bug). O transporte é sempre o **barramento único e autenticado** do core — `api.services.subscribe(handlers)` (plugin services **≥ 2.1**) ou, equivalente, `import { subscribe } from '/static/js/services/wsBus.js'`. Ele entrega **qualquer** nome de evento, inclusive o `plugin_<id>_*` que o próprio plugin emite pelo `plugins.context.broadcast` — ao contrário do `api.services.useWebSocket`, cujo mapa de eventos é fixo nos nomes do CORE. Devolve a função de unsubscribe; o efeito a retorna direto. ⚠️ Se o handler dispara refetch caro, ponha **debounce com jitter**: um evento costuma significar cache invalidado em todas as réplicas, e N operadores recarregando no mesmo instante trocam um bug de UX por um de carga.

Um `frontend_extends` recebe `buildPluginApi(id)` e negocia duas superfícies separadas no manifest: `frontend_api_version` (registry/slots/overrides) e `plugin_services_version` (`api.services`). A allowlist atual de serviços é 2.x (2.1 acrescentou `subscribe`); manifest legado sem o segundo campo recebe o adapter 1.x. O objeto expõe `api.pluginServicesVersion` (superfície negociada) e `api.pluginServicesHostVersion` (mais nova do host); ainda assim, faça feature detection da função antes de chamar. Range incompatível ou malformado faz o core pular o módulo (fail-closed). O parser de frontend aceita `*`, comparadores AND (`>=2.0,<3.0`), `^` e `~`; uma declaração numérica como `"2.0"` significa compatibilidade por MAJOR, não igualdade exata, e `||` não é aceito.

### Override de componente (plano 92 · B1)

Terceira semântica do registry, ao lado de **slots** (aditivos) e **route override** (exclusivo): `overrideComponent(name, C)` ([registry.js](web/static/js/plugins/registry.js)) deixa um plugin **substituir uma peça de UI que não é rota**. Contrato igual ao `overrideRoute` — **primeiro que registra ganha**, reivindicação posterior é logada e ignorada (nunca silenciosa). Use quando a tela inteira pertence ao domínio do plugin; um slot resolve quando é só acrescentar.

O core renderiza um **Host** que resolve o override e cai no próprio componente enquanto existir fallback — nenhum arquivo do core sabe qual plugin reivindicou o quê. O Host **congela o componente na montagem** (só a transição "nada → algo" é aceita): re-resolver a cada `bump()` do registry trocaria o tipo do vnode com o modal aberto e descartaria o formulário do operador, porque `loadPluginExtensions` limpa o registry de forma síncrona a cada toggle de plugin.

| Nome | Host | Dono hoje | ctx |
|---|---|---|---|
| `template.picker` | [TemplatePickerHost.js](web/static/js/components/contacts/TemplatePickerHost.js) | `whatsapp_cloud` | `{conversationId, channelId, phone, onClose, onSent}` |

**O modal "Enviar template" é do plugin.** O formato de um template é ditado pela API do provedor, então quem o desenha é o plugin de canal — o `static/TemplatePicker.js` do `whatsapp_cloud` (fonte no repositório de plugins), com favoritos por usuário, arquivar global (permissão `plugin.whatsapp_cloud.template_archive`, que **nasce sem dono**) e busca por conteúdo. A cópia do core ([TemplatePicker.js](web/static/js/components/contacts/TemplatePicker.js)) está **congelada** como fallback de transição e some na release seguinte — as duas já divergiram de propósito, **não corrija bug nela**. Quem chama o picker deve gatear com `templatePickerAvailable()` (sem plugin e sem fallback, o botão não aparece e o aviso de 24h degrada para texto).

O vocabulário da Meta que o core carregava (categorias, formatos de cabeçalho, tipos/limites de botão, MIMEs de upload) virou `TemplateSpec` em [channels/base.py](channels/base.py), declarado pelo provider em `ChannelCapabilities.template_spec` e apenas **avaliado** pelo core ([template_service.py](app/services/template_service.py) `spec_for`) — mesmo padrão de `MediaLimits`/`VideoLimits`.

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
- **Cores / modo escuro**: a tela do plugin (`static/<id>.js`) DEVE ser legível no tema escuro. Use as classes semânticas `wa-*` (`bg-wa-bg`, `bg-wa-panel`, `text-wa-text`, `border-wa-border`, …) e `.wa-field` em inputs. Cores cruas (`bg-white`, `bg-green-50`, …) têm fallback no `custom.css`, mas hex inline e cores fora da lista coberta NÃO — teste com o modo escuro ligado. Ver "Tema e modo escuro (legibilidade)".
- **Auditoria**: toda rota do plugin que MUDA configuração ou estado com dono chama `plugins.context.audit(...)`. Ver "Auditoria de plugins" abaixo e o guia [docs/PLUGINS_AUDITAVEIS.md](docs/PLUGINS_AUDITAVEIS.md).

### Versionamento da API de plugins (`WHATSBOT_API_VERSION`)

**Versão atual: `1.2.0`** ([plugins/semver.py](plugins/semver.py) — fonte única; `plugins/manifest.py` é re-export por valor). Changelog: [docs/PLUGIN_API_CHANGELOG.md](docs/PLUGIN_API_CHANGELOG.md). Guard: [tests/contracts/test_plugin_api_surface.py](tests/contracts/test_plugin_api_surface.py) + `tests/goldens/plugin_api_surface.json`.

⚠️ **A constante ficou congelada em `1.0.0` por 93 dias** (2026-05-10 → 2026-08-11) enquanto a superfície crescia de 35 para 75 eventos e de 0 para 24 filtros. Consequência: o guard de compat nunca rejeitou nada e **nenhum plugin conseguia declarar de que core ele precisa** — o `whatsapp_cloud` teve de degradar fechado em runtime porque não tinha como exigir o `ctx.extras.signature_authenticated` do plano 84. A regra em prosa existia desde 2026-06-29 e foi violada 8 dias depois, em silêncio. Por isso a disciplina agora tem dente, não só texto.

**Dentro da superfície versionada** (mudou ⇒ bump): os catálogos do bus (`KNOWN_EVENTS`, `KNOWN_FILTERS`, `EXPERIMENTAL_FILTERS`, `_LIFECYCLE_EVENTS`, `_DISPATCH_ONLY_KEYS`) e a semântica de cada filtro (tipo do valor, o que `None` faz, `ctx.extras`) · os símbolos públicos de [plugins/context.py](plugins/context.py) e os campos dos contextos · o schema do manifest, as regexes de validação e as 9 chaves de `_ENTRY_SPECS` **em ordem** · [channels/base.py](channels/base.py) + [channels/events.py](channels/events.py) · as convenções de host (prefixo `plugin_<id>_`, namespace `whatsbot_plugins.<id>`, mounts `/api/plugins/<id>` e `/plugins/<id>/static`, isenção `/public/`, prefixo `plugin.<id>.` de config, chave RBAC, `PLUGIN_ACTION_RE`, `TEARDOWN_TIMEOUT_SEC`).

**Fora**: `db.repositories` e os demais módulos do core que plugins importam — são dependência real, **não API declarada**, e a proteção deles continua sendo o import defensivo (ver o aviso em "O que fica no core e o que vai pro plugin"); e o frontend, que tem números próprios (`FRONTEND_API_VERSION`, `PLUGIN_SERVICES_VERSION` em [web/static/js/plugins/api.js](web/static/js/plugins/api.js)) e falha de forma **assimétrica** — lá, incompatível pula o `frontend_extends`; aqui, incompatível faz o plugin **deixar de existir**. **Nunca sincronize os valores.** Seam que um plugin publica para outro (`filter.retornos.*`, `protocolos.*`) é versionado pelo `version` do plugin publicador.

O teste de pertinência é mecânico: **está dentro se existe um snapshot que falha quando aquilo muda**. Querer mover algo para dentro custa escrever o snapshot.

| Nível | Gatilho |
|---|---|
| **MAJOR** | remover/renomear nome de catálogo **com produtor vivo**; mudar o tipo do valor de um filtro ou a semântica do `None`; remover/renomear símbolo público, campo de dataclass, chave de `entry` ou convenção de host; tornar obrigatório campo opcional do manifest; tornar abstrato método de `Channel` que tinha default. **Derruba os 36 manifests do parque de uma vez** (todos declaram `">=1.0,<2.0"`), inclusive o `gowa` bundled — é tranche que republica os ZIPs com ordem de deploy, não decisão de commit |
| **MINOR** | acrescentar nome ao catálogo (**no mesmo commit do call site**), símbolo, campo com default, chave de `entry`, capability, método com default; alargar `ctx.extras`; ampliar quando um evento existente é emitido |
| **PATCH** | correção que não muda a forma; retirar nome de catálogo **sem produtor vivo** (exige varredura repo-wide + changelog + teste de WARNING) |

Exceção: seam em `EXPERIMENTAL_FILTERS` pode sair sem MAJOR — o contrato em [plugins/events.py](plugins/events.py) já diz que ele pode se mover até se formar.

**Fluxo quando o guard fica vermelho** (ele imprime estes 3 passos na falha):
1. bump em [plugins/semver.py](plugins/semver.py);
2. entrada no topo de `docs/PLUGIN_API_CHANGELOG.md` — o heading `## X.Y.Z — data` precisa ser o **primeiro** heading de versão do arquivo (o apêndice histórico usa `###` de propósito);
3. `UPDATE_PLUGIN_API_SURFACE=1 venv/bin/python -m pytest tests/contracts/test_plugin_api_surface.py`.

A regeneração **se recusa a rodar** enquanto a constante não tiver andado — é isso que torna a disciplina aplicável em vez de documentada. A env é deliberadamente separada do `UPDATE_GOLDENS` usado em massa nos goldens de caracterização, que varreria a superfície da API junto.

### Auditoria de plugins

A trilha (`audit_log`, tela `/audit`) é dirigida pelo bus: o listener `*` ([server/audit_listener.py](server/audit_listener.py)) confere cada evento contra a allowlist `AUDITABLE_EVENTS` ([db/audit_actions.py](db/audit_actions.py)). Essa allowlist é a **vocabulário do CORE** — plugin não a edita (o core não conhece plugin por nome, mesmo princípio dos canais/RBAC). O plugin registra as próprias ações pelo seam `audit()`:

```python
from plugins.context import audit
audit("protocolos", "config.geral", before=antes, after=depois)
# → ação  protocolos.config.geral   recurso  plugin:protocolos   ator: usuário logado
```

- **Contrato** ([plugins/context.py](plugins/context.py) `audit()`): a ação é namespaceada com o id do plugin (`namespaced_action`) e validada contra `PLUGIN_ACTION_RE` (`<plugin_id>.<recurso>.<verbo>`; fora do formato ⇒ WARNING e a linha é descartada, a rota segue). `resource_type` default `"plugin"`, `resource_id` default = id do plugin (o filtro "ID do recurso" lista tudo daquele plugin). Fire-and-forget, nunca levanta, respeita o master `audit_enabled`. O ator sai do `ContextVar` da request (o usuário logado) — `actor_type="ai"/"system"` só para autor não-humano (executor externo, job).
- **Write path** ([server/audit_listener.py](server/audit_listener.py) `record()`): o ÚNICO caminho de escrita fora do listener. Aplica o gate global + resolução de ator; um ator forçado (`ai`) não herda id/rótulo do humano da request.
- **Segredo nunca entra**: o `audit_repo` mascara por NOME de chave (rede de segurança, não licença) — o plugin registra `{"secret_definido": True}`, não o valor. Conteúdo já versionado (prompt de agente, código de tool) entra como PONTEIRO (`{key, version}`), não como cópia.
- **Plugin de CANAL grava no CANAL**: um provider (gowa/telegram/whatsapp_cloud/website/facebook_messenger/instagram) passa `resource_type="channel", resource_id=<channel_id>` — as ações dele são sobre um canal, e assim caem no MESMO recurso dos eventos `channel.*` do core: **um filtro por canal devolve a história inteira** (criado/editado/desconectado pelo core + webhook redirecionado/Página assinada pelo plugin). Config que não é por canal (ex.: o alerta de desconexão do `gowa`, global) mantém o default `plugin:<id>`.
- **Settings declarativas já são auditadas** pelo core (`plugin.settings.changed` → `plugin.settings_update`, com diff): plugin que só tem `settings.py` (ex.: `guarda_ia`) não precisa de nada.
- **O que auditar**: configuração, mudança de estado com dono (fechar/atribuir/aprovar), escrita em recurso do core, ação com efeito externo. **O que não**: GET/listagem, teste de conexão, preferência pessoal por-usuário, evento de alto volume.
- **CONVERSA NUNCA ENTRA NA TRILHA** (regra dura): enviar/receber mensagem num canal não gera linha nenhuma — nem envio do operador, nem resposta da IA, nem inbound do cliente, nem reação/edição/recibo/presença. O histórico de `messages` já é esse registro. Ficam fora da allowlist de propósito: `message.*`, `presence.changed`, `receipt.changed` e `channel.status_changed` (read que roda a cada poll). Travado por `test_audit_ignores_message_traffic` (webhook inbound + envio do operador ⇒ `audit_log` intacta) e `test_audit_message_events_stay_out_of_allowlist`.
- Guia completo + checklist: [docs/PLUGINS_AUDITAVEIS.md](docs/PLUGINS_AUDITAVEIS.md). Plugins que já usam: `protocolos` (config + operação), `melhorias` (aprovações + executor com ator `ai`), `vendas_ia` (`/seed`), e os 6 providers de canal (`gowa` alerta de desconexão; `telegram`/`whatsapp_cloud`/`facebook_messenger`/`instagram` webhook+assinatura; `website` revelação do segredo HMAC).

### RBAC de plugins

Um plugin declara permissões de usuário no bloco `rbac:` do `plugin.yaml` (distinto do `permissions:` de capability `llm.tool`/`db.write`). Cada permissão vira a chave `plugin.<id>.<key>` registrada (upsert) na tabela `permissions` no load do plugin ([plugins/rbac.py](plugins/rbac.py)), aparecendo no `PermissionPicker` na área **Plugins** agrupada por `rbac.group` (default = nome do plugin) **enquanto o plugin estiver ativo** (desativar esconde do picker; ver "Disable" abaixo). Convenção forte de chaves: `view`/`edit`/`delete` (chaves livres são aceitas — regex `^[a-z][a-z0-9_.]{0,48}$`).

```yaml
rbac:
  group: "Lembretes"          # opcional; default = name do plugin
  permissions:
    - { key: view,   label: "Ver lembretes" }
    - { key: delete, label: "Excluir lembretes" }
```

- **Enforce nas rotas** com a dependency `plugin_permission("<key>")` ([plugins/context.py](plugins/context.py)): infere o `<id>` do path `/api/plugins/<id>/...`, monta `plugin.<id>.<key>` e retorna 403 quando o usuário logado não tem a permissão. **Default-allow** quando open (sem identidade de usuário, instalação sem admin ainda) — não quebra o modo aberto. Nunca cheque permissão na mão; use a dependency.
  ```python
  from plugins.context import plugin_permission
  @router.delete("/items/{id}", dependencies=[plugin_permission("delete")])
  async def delete_item(id: int): ...
  ```
- **Esconda a screen** sem permissão com `requires: <key>` no manifest da screen (`screens[].requires`) — o GearMenu filtra (padrão "hide, don't disable"). O componente da screen recebe a prop `can(key)` (= `hasPermission(user, 'plugin.<id>.<key>')`).
- **Decisão central**: [server/authz.py](server/authz.py) `check()`/`acheck()` resolvem RBAC e então aplicam o seam ABAC `filter.authz.decision` (`{user, permission_key, allow}` → pode rebaixar allow→deny). **Nenhum avaliador é embarcado no core (v1)** — regras por atributo (ex: horário) viram um plugin de filtro depois, sem tocar nos call sites.
- **Catálogo**: `rbac_repo.list_catalog()` = core (`PERMISSION_CATALOG` estático, com `tier`/`group` de exibição via `domain.permission_catalog.PERMISSION_GROUPS`) + linhas de plugins **ATIVOS** (`plugin_id IS NOT NULL` ∧ `plugins.enabled=1`). O `PermissionPicker` renderiza dois tiers (**Sistema** × **Plugins**). `/api/roles` e a validação de criação de role/usuário usam o catálogo/keys efetivos.
- **Disable** mantém as linhas mas as **ESCONDE do picker** (`list_catalog` filtra por plugin ativo); os grants sobrevivem ao toggle e voltam a aparecer ao reativar. Para não perder um grant escondido ao editar cargo/usuário com o plugin off, `_replace_role_permissions`/`set_custom_permissions` **preservam** as chaves em `hidden_plugin_permission_keys()`. **Delete** do plugin remove `WHERE plugin_id = <id>` (grants em `role_permissions`/`user_permissions` caem por FK cascade).

### API interna plugin→plugin (`entry.services`) — nunca HTTP

Terceiro canal entre plugins, ao lado do **barramento** (broadcast, "aconteceu algo") e dos **filtros** (interceptivo, "reescreva este valor"): **request/response**. Um plugin publica uma superfície nomeada de operações e outro chama e LÊ a resposta. Motor em [plugins/services.py](plugins/services.py) — irmão Python do seam que o frontend já tem em [web/static/js/plugins/api.js](web/static/js/plugins/api.js) (`buildPluginApi`), com allowlist e negociação de versão.

- **Provedor**: exporta `SERVICES = {"op": callable, ...}` (opcionalmente `SERVICES_VERSION` e `SERVICES_ALLOW`) do módulo declarado em `entry.services` do manifesto. A versão mora no CÓDIGO (`SERVICES_VERSION`), não no manifesto — código e versão no mesmo arquivo, sem drift. ⚠️ **Não confundir com `plugin_services_version`**, que é a superfície de FRONTEND (`api.services`) e não tem relação nenhuma com este campo.
- **Consumidor**: declara `uses_services: [{plugin: <id>, version: ">=1.0,<2.0"}]` no manifesto e chama `services.call("<id>", "op", _as="<meu_id>", **kwargs)`. O range do manifesto é o default das chamadas feitas com `_as`.
- **Envelope**: toda chamada devolve um `ServiceResult` — **dispatch NUNCA levanta**. Status: `ok` · `unavailable` (plugin ausente/sem superfície/bloqueado por allowlist) · `unknown_op` · `incompatible` (range) · `disabled` (carregado mas desligado — a op levanta `ServiceDisabled`) · `wrong_context` (op async chamada de forma síncrona NA THREAD DO LOOP) · `error` (a implementação levantou).
- **`get()` é null-object**: nunca devolve `None`. Feature detection é `if services.get("trackify"):`; um proxy indisponível é falsy e o `.call()` dele ainda responde com o status certo, em vez de `AttributeError`. Ops **não** viram atributos do proxy de propósito.
- **Sync/async**: `await proxy.acall()` roda impl sync em `asyncio.to_thread` e impl async direto; `proxy.call()` de uma worker thread faz a ponte para o loop com `run_coroutine_threadsafe`; `proxy.call()` DA thread do loop com impl async devolve `WRONG_CONTEXT` + WARNING — nunca bloqueia o loop (mesma degradação de `apply_filter_sync`).
- **Registro em `create_app`**, antes do lifespan e do `run_setup`. Isso impõe uma linha de contrato ao provedor: **uma op não pode depender de estado criado no `setup()`** — se depender, devolve `DISABLED`/`ERROR` até ficar pronta, nunca quebra e nunca bloqueia. O desregistro é no shutdown do app (não em `plugins.lifecycle`, que sai cedo para plugin sem `entry.lifecycle`).
- **Invisível ao HTTP — o requisito central**: o módulo não importa `fastapi`, `_entry_services` nunca toca `loaded.router`, e nenhum provedor expõe `/rpc` ou `/service/{op}`. Travado por teste ([tests/contracts/test_plugin_services.py](tests/contracts/test_plugin_services.py) e [tests/integration/test_plugin_services_wiring.py](tests/integration/test_plugin_services_wiring.py), que compara a tabela de rotas com e sem `entry.services`).
- **`as_plugin` é FALSIFICÁVEL** (é o chamador que o informa): serve de contabilidade de raio de alcance, **não** é fronteira de segurança. A fronteira real é "nada sai do processo".
- **Auditoria é do PROVEDOR**, por operação com efeito externo (`plugins.context.audit`) — o registro não audita nada (não conhece semântica e algumas ops são de alto volume). Ver §"Auditoria de plugins".
- **Compatibilidade com core anterior**: um core sem a linha `"services"` em `_ENTRY_SPECS` nunca consulta `entry.services` e nunca importa o módulo — por isso o `services.py` do provedor tem de ser **FOLHA** (nenhum outro módulo dele o importa; helper compartilhado vai para os módulos vizinhos). No consumidor, o import é sempre defensivo: `try: from plugins import services / except: _services = None` — import duro no topo de um módulo que o loader importa = o plugin não carrega, falha muda no boot.
- **Quem usa hoje**: `trackify` publica a superfície completa do CDP (`SERVICES_VERSION = "1.0.0"`, 18 ops: status, eventos, jornada, compras, assinaturas, identidade, campos, escrita, cadastro, consentimento) e é o **único ponto que fala com o CDP**; `protocolos` a consome para entregar `track_protocolo_*` (antes era assinatura de barramento). O `_emit_bus` do `protocolos` **continua emitindo** — o emit sobra como sinal de observabilidade para quem assina `"*"` (`debug_bus`); o que mudou foi só o caminho de ENTREGA.

### Events e Filters (bus do plugin)

Plugins podem reagir a tudo que acontece no WhatsBot e modificar dados em trânsito sem editar o core. Dois mecanismos complementares (padrão WordPress: actions + filters; referências validadas em Baileys / WAHA / Home Assistant):

- **Events** — broadcast fire-and-forget, paralelo. Plugin exporta `EVENT_HANDLERS` em `<plugin>/events.py` e declara `entry.events: events` no manifest. Não bloqueia o pipeline principal; exceção em um handler nunca afeta outros.
- **Filters** — interceptive, síncrono no pipeline. Plugin exporta `FILTERS` em `<plugin>/filters.py` e declara `entry.filters: filters` no manifest. Recebe `(ctx, value)` e retorna valor modificado ou `None` pra abortar a ação envolvida. Exceção em um filter é isolada (loga + valor passa intacto ao próximo).

Toggle do plugin = tudo-ou-nada: enable liga handlers e filters; disable derruba ambos no próximo restart.

**Eventos comuns de mensagem/provider** (o catálogo executável completo é `plugins.events.KNOWN_EVENTS`):

| Evento | Quando dispara | Payload chave |
|--------|---------------|---------------|
| `message.received` | Inbound user msg (inclui group sem @mention). **Emitido ANTES do save** — listener que precisa ler do DB deve usar `message.saved` | `phone, name, text, raw_text, msg_id, media_type, media_path, media_extras, is_group, group_jid, individual_phone, raw` |
| `message.saved` | **NOVO** — emitido DEPOIS do INSERT no DB, em todos os 3 sites de save inbound (text batch, media batch, group_no_mention) | `phone, text, msg_id, media_type, media_path, media_extras, is_group, group_jid, source` — `source ∈ {batch_text, batch_media, group_no_mention}` |
| `message.sent` | Resposta IA, operator send, image/audio panel, retry, private @ia, echo do próprio celular | `phone, text, msg_id, media_type, media_path, media_extras, source, status` — `source ∈ {ai, operator, private_ai, retry, echo}` |
| `message.any` *(alias)* | Re-dispatch de `received` + `sent` com `direction: "in"\|"out"` | igual ao original + `direction` |
| `message.reaction` | Reação emoji em mensagem | `id, phone, reaction, reacted_message_id, is_from_me` |
| `message.edited` | Mensagem editada (inbound: cliente editou a própria) — o core JÁ atualiza `messages.content` + `edited_ts` e faz broadcast `message_edited` (GOWA/Telegram; Cloud não emite) | `id, phone, original_message_id, body` |
| `message.revoked` | Mensagem apagada pra todos | `id, phone, revoked_message_id, revoked_from_me, revoked_chat` |
| `message.deleted` | Mensagem deletada do histórico | `deleted_message_id, original_content, original_sender, was_from_me` |
| `message.failed` | **NOVO (plano 75)** — o provedor avisou que NÃO entregou a mensagem (`statuses[].status = "failed"` da Meta). O core já marcou a msg como `failed` quando a row existia e fez broadcast `message_status`. `is_redelivery=True` é o guard de dedupe snapshotado no receipt; `is_new=False` também pode ser a primeira falha que chegou antes do writer e, sozinho, NÃO deve ser descartado. | `phone, channel_id, msg_id, error_code, error_title, error_details, conversation_id, is_new, is_redelivery, ts, raw` |
| `presence.changed` | Digitando / gravando | `phone, state` (`composing`/`paused`), `media` (`text`/`audio`) |
| `receipt.changed` | Ack de entrega — **desde o plano 75 cobre TODOS os status** (`sent`, `delivered`, `read`, `failed`, `played`), não só `delivered`/`read`; o emit saiu de dentro do `if`. Sempre emitido DEPOIS da escrita no banco | `phone, msg_ids, status, errors, channel_id, ts` (`errors` = array cru do provedor, só em `failed`) |
| `group.participants_changed` | Join/leave/promote/demote | `chat_id, phone, type, jids` |
| `group.joined` | Bot adicionado ao grupo | `chat_id, phone` |
| `call.received` | Chamada recebida (offer) | `call_id, phone, auto_rejected` |
| `newsletter.event` | Eventos de newsletter | `subtype, raw` |
| `chat.archived` | Arquivamento detectado no GOWA | `phone, archived` |
| `connection.changed` | GOWA connect/disconnect/QR | `is_connected, is_logged_in, qr_required` |

**Eventos internos**:

| Evento | Source |
|--------|--------|
| `llm.before` / `llm.after` | `aprocess_message`/`process_message` antes/depois de `chat.completions.create`. `after`: `reply, tool_calls, usage, latency_ms` |
| `tool.before` / `tool.after` | `_dispatch_tool`. `after`: `result, error, latency_ms` |
| `contact.updated` | PUT `/api/contacts/{phone}/info` |
| `contact.ai_toggled` | POST `/api/contacts/{phone}/toggle-ai` |
| `contact.tagged` | PUT `/api/contacts/{phone}/tags` (snapshot completo da lista de tags) |
| `contact.untagged` | **NOVO** — um emit POR tag removida em PUT `/api/contacts/{phone}/tags` (`{phone, tag, ts}`) |
| `tag.created` / `tag.updated` / `tag.deleted` | tag endpoints |
| `execution.started` / `execution.ended` | **NOVO** — wrappers async `astart_execution`/`aend_execution`. `ended` carrega `error: str\|None` e `duration_ms` |
| `config.changed` | PUT `/api/config` (com `keys_changed`) |
| `tool_override.changed` | PUT `/api/tools/{name}` |
| `plugin.loaded` / `plugin.enabled` / `plugin.disabled` / `plugin.settings.changed` | lifecycle do plugin |
| `plugin.imported` / `plugin.deleted` | POST `/api/plugins/import` (upload de `.zip`) e DELETE `/api/plugins/{id}` — auditados como `plugin.install`/`plugin.uninstall` |
| `channel.created` / `channel.updated` / `channel.deleted` / `channel.restored` | `channel_service` create/update/delete/restore. `updated` cobre TODOS os campos editáveis (nome, enabled, config incl. IA por canal, credenciais) — antes valia só para `config`; payload `{channel_id, provider, keys_changed, credential_keys_changed, ts}` |
| `channel.members_changed` | PUT `/api/channels/{id}/members` (`before/after` = ids dos membros do inbox) |
| `channel.session_action` | POST `/api/channels/{id}/reconnect` \| `/logout` (só quando a ação de fato rodou — sentinelas `not_gowa`/`unavailable` não emitem) |
| `channel.duplicate_refused` | sweep de identidade recusou um canal duplicado ([channel_identity.py](app/services/channel_identity.py)) — ator `system` |
| `channel.status_changed` | leitura de status ao vivo. **Fora da auditoria de propósito** (é read, roda a cada poll) |
| `conversation.pinned` | `conversation_service.pin` — fixar/desafixar a conversa |
| `conversation.labeled` | PUT das etiquetas de UMA conversa (`{conversation_id, contact_id, labels, ts}`) |
| `conversation_label.created` / `.updated` / `.deleted` | CRUD do registro GLOBAL de etiquetas (`/api/conversation-labels`) |
| `custom_attribute.created` / `.updated` / `.deleted` | CRUD da definição de atributo customizado (`{definition, ts}`) |
| `ai.config.changed` | qualquer save/rollback de agente, tool, variável ou prompt no motor de IA (`{kind, key, ts}`) — o cache do `dynamic_registry` já foi invalidado quando o evento sai |
| `channel.system_event` | inbound de **SISTEMA** de um canal (plano 82) — o ÚNICO gancho de bus para o que não é mensagem. `{phone, channel_id, system_type, wa_id, body, conversation_id, ts, raw}` |
| `app.startup` / `app.shutdown` | lifespan do server |

Chave especial `*` — subscrever via `EVENT_HANDLERS = {"*": fn}` recebe todo evento emitido (após os subscribers específicos). `ctx.event_name` traz o nome real.

**Filters disponíveis** (ponto de modificação/cancelamento):

| Filter | Local | Tipo do valor | `None` faz | `ctx.extras` |
|--------|-------|---------------|------------|--------------|
| `filter.webhook.payload` | `/api/webhook/{provider}/{channel_id}` depois de resolver canal/provider e verificar assinatura, antes do parse | `dict` (body bruto de qualquer provider) | Webhook responde 200 sem processar | `provider, channel_id, signature_authenticated` |
| `filter.message.before_save` | inbound depois do parse | `dict` (mensagem tipada com `raw`, inclui `media_extras`) | Mensagem ignorada (nem salva nem responde) | `phone` |
| `filter.message.outgoing` | **NOVO** — antes de salvar/emitir um `message.sent` de echo (mensagem enviada do celular fora do app) | `dict` (mensagem tipada, `is_from_me=True`, `source="echo"`) | Echo ignorado (não salva nem emite) | `phone` |
| `filter.message.notify` | ingest do inbound, antes de incrementar as não-lidas | `bool` (default `True`) | Mensagem SILENCIOSA — salva e exibida, mas sem badge de não-lida nem som | `phone, role, text` |
| `filter.transcription.should_run` | **NOVO** — wrapper `_maybe_transcribe`, ANTES de chamar `transcribe_audio`/`describe_image` (cobre os 4 call sites) | `bool` (default `True`) | Pula a transcrição (mesmo efeito de `False`) | `phone, media_kind ∈ {audio,image}, media_path, is_group, group_jid, source ∈ {batch,echo,group_no_mention}` |
| `filter.transcription.result` | **NOVO** — depois da chamada de transcribe/describe, antes de a string ser usada | `str` | Trata como se a transcrição fosse vazia | igual ao `should_run` + `model` |
| ~~`filter.media.unknown`~~ | 🚫 **INEXISTENTE** — o nome legado foi retirado de `KNOWN_FILTERS` no plano 100 porque não havia produtor desde o refactor do webhook. Registrar esse nome agora gera WARNING de filtro desconhecido. **Não há como um plugin reivindicar um media type novo**; o provider deve normalizar o payload em `Channel.parse_inbound()` para um `InboundEvent.kind` suportado | — | — | — |
| `filter.contact.tags` | **NOVO** — `PUT /api/contacts/{phone}/tags` antes de `contact.set_tags` | `list[str]` (tags pretendidas) | Mantém tags atuais | `phone, previous_tags` |
| `filter.event.before_emit` | **NOVO** — wrap interno do `emit_with_filter`. Recebe o payload de QUALQUER evento prestes a sair (exceto lifecycle) | `dict` (payload) | Cancela o emit | `event_name` |
| `filter.system_prompt` | antes do LLM | `str` | System prompt vira vazio | `phone` |
| `filter.llm.messages` | antes do LLM | `list[dict]` (formato OpenAI) | LLM não é chamado | `phone, model` |
| `filter.llm.tools` | antes do LLM | `list[dict]` (schemas) | LLM chamado sem tools | `phone, model` |
| `filter.tool.args` | `_dispatch_tool` antes do executor | `{tool_name, args}` | Tool pulada | `phone` |
| `filter.tool.result` | `_dispatch_tool` depois do executor | `str` (feedback pro LLM) | LLM recebe string vazia | `phone, tool_name` |
| `filter.reply.raw` | `_send_reply` antes do split | `str` | Nada é enviado | `phone` |
| `filter.reply.parts` | depois do split | `list[str]` | Nada é enviado | `phone` |
| `filter.reply.part` | cada parte antes do envio ao provider (vale para envio manual também) | `str` | Aquela parte é pulada | `phone` |
| `filter.outbound.text` | texto wire-only, depois da cópia exibida/salva e antes do provider | `str` | Mantém o texto anterior | `phone, channel_id, source, sent_by_name, index, total` |
| `filter.authz.decision` | `authz.check`/`acheck` DEPOIS do RBAC | `dict {user, permission_key, allow}` | trata como `allow=False` (nega) | `permission_key` |
| `filter.conversation.before_status` | antes de fechar a conversa | `dict {conversation_id, new_status}` | Aborta o fechamento | `user_id` |
| `filter.conversation.before_assign` | antes de atribuir/desatribuir | `dict {conversation_id, assignee_user_id}` | Aborta a atribuição | `user_id` |
| `filter.conversation.clear_assignee_on_close` | **NOVO (plano 67)** — `conversation_service.set_status` no fechamento, DEPOIS do `before_status` | `bool` (default `True` = limpar o atendente humano) | `None`/ausente ⇒ default seguro (limpa) | `conversation_id, user_id` |
| `filter.conversation.before_reopen` | 4 call sites: inbound e envio do operador (texto e mídia), antes de reabrir uma conversa fechada | `bool` (default `True`) | A mensagem NÃO reabre a conversa — aparece normalmente e a conversa segue resolvida | `phone, role, text` |
| `filter.agent.resolve` | depois de resolver o `AgentSpec` do turno | `AgentSpec` | Mantém o agente default | `phone, contact_id, channel_id` |
| `filter.conversation.assignment` | destino proposto pela tool `transfer_to_human` | `dict` de atribuição | Mantém a atribuição default | `phone` e contexto da tool |

**Lifecycle events bypassam `filter.event.before_emit`** — `plugin.loaded/enabled/disabled/settings.changed` e `app.startup/shutdown` chamam `emit()` direto. Plugin não pode bloquear seu próprio carregamento.

**Assinaturas**:

```python
# events.py
def on_event(ctx: EventContext, payload: dict) -> None: ...
async def on_event_async(ctx: EventContext, payload: dict) -> None: ...

EVENT_HANDLERS = {"message.received": on_event, "llm.after": on_event_async}

# filters.py
def fn(ctx: FilterContext, value: T) -> T | None: ...
async def fn_async(ctx: FilterContext, value: T) -> T | None: ...

FILTERS = {
    "filter.reply.part": fn,                    # priority default 100
    "filter.message.before_save": (fn, 50),     # priority 50 — roda antes
}
```

`ctx` expõe `handler` (AgentHandler), `plugin_id`, `plugin_db`, `event_name`/`filter_name`, `emitted_at`. Sync vai pra `asyncio.to_thread`; async é `await`-ado direto. Filter pode ser sync ou async — em paths sync (process_message) o WhatsBot usa `apply_filter_sync` que delega ao loop com `run_coroutine_threadsafe`.

**Padrões de uso comuns**:

- **Observador / auditor / analytics** — `EVENT_HANDLERS = {"*": log_handler}` ou eventos específicos.
- **Anonimizar / traduzir / sanitizar inbound** — `FILTERS = {"filter.message.before_save": fn}` modifica o dict.
- **Adicionar assinatura / formatar / mascarar PII na saída** — `FILTERS = {"filter.reply.part": fn}` modifica cada parte.
- **Bloquear contato / palavra-chave / horário** — qualquer filter retornando `None`. Veja o plugin `blacklist` na Loja de Plugins (repo *community*).
- **Injetar contexto extra no LLM** — `FILTERS = {"filter.system_prompt": fn}` ou `filter.llm.messages` pra reescrever o histórico antes do call.
- **Reagir a tool call específica** — `EVENT_HANDLERS = {"tool.after": fn}` com `if payload["tool_name"] == "x"`.
- **Push em tempo real pra tela do plugin** — `plugins.context.broadcast("evento", {...})` do dentro do handler.

**Boas práticas**:

- Filter síncrono trava o pipeline — mantenha rápido. Persistência pesada/network vai num event handler.
- **Para reagir a mensagem JÁ salva**: assine `message.saved`, não `message.received` — o último é emitido ANTES do INSERT no DB e listener que leia do DB pode race.
- **Pra controlar transcrição** (decisão "transcrever ou não, e como"): use `filter.transcription.should_run` + `filter.transcription.result`, nunca remova o campo `audio`/`image` no `filter.webhook.payload` — fazer isso quebra o player no histórico.
- NÃO chamar `gowa_client.send_message` dentro de handler de `message.sent` → loop infinito (a send produz outro `message.sent`).
- Filtre por `media_type` / `source` / `is_group` no INÍCIO do handler. O bus entrega tudo.
- Persista estado entre eventos em tabelas `plugin_<id>_*` (via `ctx.plugin_db` + `from sqlalchemy import text`), nunca em globals — não sobrevivem ao restart.
- `payload["raw"]` carrega o payload bruto do GOWA (potencialmente grande, com base64 de áudio). Plugins que logam tudo devem cortar `raw` antes de serializar.
- Restart obrigatório no toggle do plugin: `plugin.enabled`/`plugin.disabled` emitem ANTES do `os._exit`; o novo processo emite `plugin.loaded` no boot.

Plugin **auto-instalado** no fresh install (plano 33 D3, [plugins/bootstrap.py](plugins/bootstrap.py) `BUNDLED_AUTO_INSTALL`): **apenas `gowa`** (canal WhatsApp via GOWA, ATIVO por padrão). Os outros providers e extensões são instalados sob demanda por **Importar `.zip`**. A fonte de desenvolvimento, os testes e os artefatos publicados desses plugins vivem no repositório irmão `whatsbot-pro-plugins`, na forma `plugins/<id>/{src,tests,<id>.json,<id>.zip}`. O diretório `tests/` nunca entra no ZIP e nunca é executado durante instalação, atualização ou boot de produção.

`assets/plugin_examples/` contém **exclusivamente o `gowa`** — é a fonte do único plugin bundled, não uma bancada. Nenhum outro plugin é desenvolvido aqui: a fonte deles é o repositório externo, e mudanças no GOWA devem manter a cópia publicada sincronizada.

⚠️ **Um teste do core NUNCA deve fixar `assets/plugin_examples/<id>` para um `<id>` que não seja `gowa`.** A fonte se resolve por [tests/plugin_test_utils.py](tests/plugin_test_utils.py) (`resolve_plugin_source`), que prefere `WHATSBOT_PLUGIN_SOURCE_ROOT/<id>/src`. Um bloco que assere comportamento específico de provider ausente deve **pular**, não falhar — é o que o helper `plugin_source_or_skip` faz em [tests/core/legacy/legacy_endpoints.py](tests/core/legacy/legacy_endpoints.py). Cuidado com a armadilha: o resolvedor também cai em `storages/plugins/<id>`, então uma máquina de desenvolvimento com o plugin **instalado** fica verde enquanto um clone limpo fica vermelho — valide num worktree limpo.

### Onde vive o código de um plugin (NÃO confundir)

Nada sincroniza estes pontos automaticamente. Um mesmo plugin pode ter conteúdos diferentes em cada um, inclusive **com o mesmo número de versão** — já aconteceu com o `protocolos` (duas cópias distintas ambas marcadas `1.17.0`). Ao comparar versões, compare o CONTEÚDO, nunca só o número.

| # | Lugar | O que é | Como o código chega/sai |
|---|---|---|---|
| 1 | **Repositório de plugins do Pro** — [Techify-one/whatsbot-pro-plugins](https://github.com/Techify-one/whatsbot-pro-plugins) | Fonte de desenvolvimento em `plugins/<id>/src/`, testes em `plugins/<id>/tests/`, metadados e ZIP publicado no mesmo diretório | editar `src/`, testar e gerar com os scripts desse repositório; commit/push ali |
| 2 | `assets/plugin_examples/gowa/` | Fonte do **único** plugin bundled. Não há mais espelho dos outros | editado aqui; o boot copia para `storages/plugins/gowa/` (upgrade version-aware) |
| 3 | `storages/plugins/<id>/` | Cópia **instalada e rodando** (gitignored), tanto em desenvolvimento quanto em produção | `Importar (.zip)` na UI ou bootstrap do GOWA; nunca é fonte de verdade de desenvolvimento |
| 4 | `plugins/<id>/<id>.zip` no repositório externo | Artefato sem testes entregue ao cliente | gerado deterministicamente a partir de `src/`; instalação por `Importar (.zip)` |

**Cuidado com o termo "Loja de Plugins"**: ele designa EXCLUSIVAMENTE o repositório **community** [Techify-one/whatsbot-plugins](https://github.com/Techify-one/whatsbot-plugins) (publicado em https://whatsbot.techify.one/plugins) — outro repositório, outro produto. **Não** é o repositório de plugins do Pro (#3 acima). Não use "loja" para se referir ao `whatsbot-pro-plugins`.

Os plugins de exemplo abaixo vivem na **Loja de Plugins** (o repo community, não o do Pro) e são instalados via `Importar (.zip)` na tela Gerenciar Plugins: `event_logger` (assina `*`), `auto_signature` (`filter.reply.part`), `blacklist` (`filter.message.before_save` → `None`), `transcricao_grupos` (`filter.transcription.should_run` — controle de transcrição por grupo via UI + DB), `horario_funcionamento` (settings declarativas + `filter.system_prompt`/`filter.llm.tools`/`filter.llm.messages` + migrations — horário de funcionamento por dia da semana, com mensagem de ausência fora do expediente e cooldown por contato). ⚠️ `custom_sounds` e `notifications` saíram desta lista: viraram **core** (ver o aviso em "Onde fica a configuração de um plugin").

### Media types suportados

O webhook detecta os tipos abaixo e os converte em `parsed_msg` (`media_type` + `media_path` + `media_extras`):

| `media_type` | Campo no payload GOWA | `media_path` | `media_extras` típico |
|---|---|---|---|
| `image` | `image` (str ou `{path, caption, mimetype}`) | path do arquivo | `{caption, mimetype}` |
| `audio` | `audio` ou `video_note` (str ou `{path, duration, mimetype}`) | path do arquivo | `{duration_ms, mimetype, is_voice_note}` |
| `video` | `video` (str ou `{path, caption, duration, mimetype}`) | path do arquivo | `{caption, duration_ms, mimetype}` |
| `sticker` | `sticker` (str ou `{path, is_animated, mimetype}`) | path do arquivo | `{is_animated, mimetype}` |
| `document` | `document` (str ou `{path, file_name, mimetype, caption}`) | path do arquivo | `{file_name, mimetype}` |
| `location` | `location` (`{latitude, longitude, name, address}`) | `geo:lat,lng` | `{lat, lng, name, address}` |
| `live_location` | `live_location` | `geo:lat,lng` | `{lat, lng}` |
| `poll` | `poll` (`{name, options[]}`) | `None` | `{name, options}` |
| `interactive` | `buttons_response` / `list_response` | `None` | `{button_id, title}` ou `{row_id, title}` |
| `order` | `order` | `None` | `{item_count, total, currency}` |
| `product` | `product` | `None` | `{product_id, title}` |
| `contact` | `contact` (single vCard) | `None` | `{contacts: [{name, phone}]}` |
| `contacts` | `contacts_array` (lista) | `None` | `{contacts: [...]}` |

🚫 **Registrar um media type NOVO não é possível hoje.** O antigo `filter.media.unknown` foi retirado de `KNOWN_FILTERS` no plano 100 porque não tinha call site; quem ainda o registra recebe WARNING em vez de falhar em silêncio. O dispatch de inbound continua fechado (12 `kind` literais, sem `else` e sem log de "kind não reconhecido"), então o provider deve converter o payload para um kind suportado dentro do próprio `parse_inbound`.

Regra de versão do catálogo — caso particular da regra geral em "Versionamento da API de plugins": remover/renomear um filtro **com produtor vivo** exige MAJOR em `WHATSBOT_API_VERSION` (que hoje derrubaria os 36 manifests do parque de uma vez). Retirar um nome apenas documentado, sem `apply_filter` no core suportado, é PATCH — nenhum comportamento executável deixa de existir —, mas exige varredura, entrada em [docs/PLUGIN_API_CHANGELOG.md](docs/PLUGIN_API_CHANGELOG.md) e teste de WARNING como o caso acima. **Acrescentar** nome é MINOR, no MESMO commit do call site — travado por `test_bus_catalogue_matches_producers`, que compara o catálogo com os produtores reais nas duas direções.

### Criar um plugin novo

Use o slash command `/new-plugin` no Claude Code. O comando lê os arquivos de referência, pergunta requisitos (id, telas, tools, tabelas, settings) e gera a estrutura de desenvolvimento em `../whatsbot-pro-plugins/plugins/<id>/`, com fonte em `src/` e testes fora do artefato. Veja `.claude/commands/new-plugin.md`.

### Importar/exportar

- Export: `GET /api/plugins/<id>/export` retorna um `.zip` da pasta (excluindo `__pycache__/` e arquivos `.db`).
- Import: `POST /api/plugins/import` (multipart) exige um único manifest na raiz, checa colisão de `id` e path traversal e extrai em `storages/plugins/<id>/`. A validação completa/compatibilidade do manifest ocorre no discovery/load; o importado fica `enabled=0` até o usuário ativar pela UI.
- Build publicado: no repositório `whatsbot-pro-plugins`, rode `python3 scripts/build_plugins.py <id>` ou `--all`; `--check` valida que cada `<id>.zip` corresponde byte a byte a `src/`. O builder rejeita testes, caches, bancos, segredos e traversal dentro da fonte instalável.

## Testes automatizados

Os testes do core estão separados por responsabilidade:

- `tests/core/`: unidades, caracterização interna e runner das suítes legadas;
- `tests/contracts/`: contratos públicos que qualquer plugin pode consumir;
- `tests/integration/`: API, Postgres e costuras entre componentes do core.

A suíte roda **contra um Postgres de teste** quando necessário. A URL vem de `WHATSBOT_TEST_DB_URL` (env ou `.env`) e [tests/pg.py](tests/pg.py) recria o schema uma vez por processo. A trava exige que o nome do banco contenha `test`, salvo override explícito.

```bash
# Core inteiro; pyproject.toml limita a coleta às três árvores acima
venv/bin/python -m pytest

# Uma camada isolada
venv/bin/python -m pytest tests/contracts
```

Não rode duas suítes PostgreSQL em paralelo: cada processo recria o mesmo schema `public`. O pytest do core **não descobre** testes em `storages/plugins` e não modifica plugins instalados.

Os testes dos plugins rodam somente no repositório externo, por comando explícito:

```bash
cd ../whatsbot-pro-plugins
python3 scripts/test_plugins.py protocolos
python3 scripts/test_plugins.py --all
```

O runner injeta `WHATSBOT_CORE_ROOT` e `WHATSBOT_PLUGIN_SOURCE_ROOT`, reaproveita as fixtures públicas de [tests/plugin_fixtures.py](tests/plugin_fixtures.py) e executa cada plugin separadamente. Instalar, atualizar, ativar ou iniciar um plugin em produção **nunca executa esses testes**.

Testes do core que ainda precisam de uma fonte real usam [tests/plugin_test_utils.py](tests/plugin_test_utils.py): a resolução prefere `WHATSBOT_PLUGIN_SOURCE_ROOT/<id>/src`, cai em `assets/plugin_examples/<id>/` (hoje só o `gowa`) e só depois na instalação. Para contratos genéricos, prefira [tests/fake_provider.py](tests/fake_provider.py). Teste de costura deve usar [tests/support.py](tests/support.py) e o namespace canônico `whatsbot_plugins.<id>.*`.

Os testes inserem dados de teste (contatos, mensagens, tags, usage); o runner `tests/core/test_legacy_scripts.py` ainda executa a suíte histórica de endpoints como subprocesso durante a transição, além dos testes pytest nativos, cobrindo:
- Health, Auth (com e sem senha), Config (GET/PUT/test-key, `group_reply_mode`), Status, Balance
- Contacts (list, detail, search, archived, send, retry, image, audio, presence, read, toggle-ai, update info, **pin/unpin**, **unread/mark-all-read/mark-all-unread**, **unread-count**, **@menção em grupo / has_unread_mention**, **react/delete de mensagem**, **members** de grupo)
- Tags (CRUD + contact tags)
- Usage (summary, by-contact, detail)
- Logs, Webhook payloads, Webhook (presence, echo, ack, reaction, reply/quoted, revoke)
- WhatsApp/QR (get, refresh, reconnect, logout)
- Sandbox (send, clear)
- Frontend SPA routes (inclui `/wizard`)
- Auth middleware (proteção de endpoints, exemptions)

## Teste opcional com Evolution API

Se você tiver acesso a uma instância da Evolution API, pode testar o fluxo de mensagens de ponta a ponta. Isso é opcional, mas recomendado ao alterar webhook, agent, handler ou batching.

Variáveis de teste devem ser configuradas no arquivo `.env`:
- `EVOLUTION_API_URL` — URL base da Evolution API
- `EVOLUTION_API_KEY` — API key de autenticação
- `EVOLUTION_INSTANCE_ID` — ID da instância Evolution
- `EVOLUTION_TEST_NUMBER` — número WhatsApp para receber a mensagem de teste

### Como testar

1. Garanta que o servidor está rodando e conectado (`curl /api/status` → `connected: true`)
2. Envie mensagem de teste via Evolution API:
```bash
source .env
curl -X POST "${EVOLUTION_API_URL}/message/sendText/${EVOLUTION_INSTANCE_ID}" \
  -H "Content-Type: application/json" \
  -H "apikey: ${EVOLUTION_API_KEY}" \
  -d "{\"number\": \"${EVOLUTION_TEST_NUMBER}\", \"text\": \"mensagem de teste\"}"
```
3. Aguarde ~10 segundos e verifique os logs:
```bash
curl -s http://127.0.0.1:{web_port}/api/logs?limit=10
```
4. Confirme nos logs que aparece:
   - `[Webhook] Message from ...` — mensagem recebida
   - `[Batch] Processing N messages ...` — batch processado
   - `[Batch] Replied to ...` — resposta enviada

### Processo de teste para kill/restart

```bash
# Matar processos anteriores
taskkill //F //IM gowa.exe 2>&1; taskkill //F //IM python.exe 2>&1

# Iniciar servidor
source venv/Scripts/activate
python -c "import uvicorn; from server.dev import app; uvicorn.run(app, host='127.0.0.1', port=8080, log_level='info')"
```

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
- **Debug do subprocess GOWA**: por padrão o stdout/stderr do GOWA vão para `DEVNULL` (sem custo). Para diagnosticar mensagens descartadas (payloads vazios, tipos não decodificados, templates HSM da Cloud API, etc.), setar a env `WHATSBOT_GOWA_DEBUG=1` (no Coolify ou outro ambiente) e reiniciar o container. Com a flag ativa, o GOWA é iniciado com `--debug=true` e os logs são gravados em `logs/gowa.log` (truncado quando passa de ~10 MB). Acessível via `GET /api/gowa-logs?limit=N` (default 500, max 5000). A resposta inclui `debug_enabled`, `log_path`, `size` e `lines[]`. Desligar setando `WHATSBOT_GOWA_DEBUG=0` ou removendo a variável + reiniciando
- **Mensagens HSM via Cloud API (linked device limitation)**: contas Business via WhatsApp Cloud API enviam mensagens template (`<hsm tag="..."/>`, ex: Mercado Livre, OTP, notificações). Por design do WhatsApp, esses templates **não são entregues com conteúdo para linked devices** — só para o device primário. O GOWA recebe um `placeholderMessage` com `type: MASK_LINKED_DEVICES` (sem body/media), e o webhook chega só com metadata (`chat_id`, `from`, `id`, `timestamp`). Não é bug — é limitação estrutural. Para confirmar, ativar `WHATSBOT_GOWA_DEBUG=1` e procurar `placeholderMessage` ou `<hsm tag=` em `/api/gowa-logs`
- **Bootstrap do banco**: na inicialização, `init_db()` exige `DATABASE_URL` Postgres na env (fail-fast com mensagem acionável se ausente/inválida), cria o engine e roda `alembic upgrade head`. Banco vazio nasce direto via Alembic — não há recriação destrutiva
- **`statics/` precisa de pasta persistente no deploy (estilo Chatwoot)**: `statics/` está no `.gitignore` E `.dockerignore`, e é criada vazia em runtime dentro do container. A mídia enviada pelo operador (`statics/outbox/`), o cache de avatares (`statics/avatars/`) e os sons importados na aba "Sons" (`statics/sounds/`, cuja linha em `custom_sounds` sobrevive no Postgres mesmo se o arquivo sumir) vivem no **disco local da instância** — não no banco. Por isso o [Dockerfile](Dockerfile) **NÃO** declara `VOLUME` (um `VOLUME` no Dockerfile cria volume **anônimo**, que o Coolify/`docker run` sem `-v` **descarta ao recriar o container num redeploy** — daria 404 `{"detail":"Not Found"}` nas imagens já enviadas, e a leitura "parece persistente" engana). A persistência é feita por **bind mount de pasta real da instalação**: `docker-compose.yaml` mapeia `./data/{storages,statics,logs}` → `/app/...` (pastas visíveis no host, pré-criadas pelo `docker_start.sh`); no **Coolify**, configurar **Persistent Storage** mapeando `/app/storages` e `/app/statics` (ou ao menos `/app/statics/outbox`) para host path/volume. Em dev (`linux_start.sh`, processo direto, sem container) os arquivos já ficam em `statics/` da raiz do checkout. Avatares são cache auto-recuperável (re-baixados do GOWA), então o painel cai num placeholder 200 em vez de 404 (rota `GET /statics/avatars/{name}` em [server/app.py](server/app.py)); imagem perdida no chat renderiza placeholder "indisponível". **Atenção (DB compartilhado + disco local):** se duas instâncias dividem o MESMO banco (ex.: Postgres remoto) mas têm `statics/` separados, a mídia enviada por uma não aparece na outra (o `media_path` está no banco compartilhado, mas o arquivo só existe no disco de quem enviou) → "Imagem indisponível". Storage de mídia é **per-instância** por design; multi-réplica com mídia compartilhada exigiria storage de rede de verdade (volume por-nó não basta). **O mesmo vale para `storages/`** (código dos plugins em `storages/plugins/` + sessão do WhatsApp/GOWA): sem Persistent Storage em `/app/storages` no Coolify, um redeploy zera o disco e **os plugins somem da interface** (a listagem varre o disco; só `gowa` é re-semeado) — mas as configs (`config` `plugin.<id>.*`), dados (`plugin_<id>_*`) e migrations sobrevivem no Postgres, então re-importar o mesmo `.zip` recupera tudo. Uma **salvaguarda de boot** ([server/persistence_check.py](server/persistence_check.py), chamada em `create_app`) detecta esse disco-zerado-banco-vivo via token-sentinela (arquivo em `storages/` vs. config key `storages_persistence_token`) e grita no log (`persistence-check: storages/ NÃO é persistente!`); o veredito também aparece em `GET /api/admin/database` (`storage_persistent`). Passo a passo em [docs/DEPLOY_COOLIFY.md](docs/DEPLOY_COOLIFY.md)
- **Bootstrap de plugins**: `BUNDLED_AUTO_INSTALL` copia automaticamente **somente `gowa`** de `assets/plugin_examples/gowa/` para `storages/plugins/gowa/`; não existe cópia genérica quando a pasta está vazia. `telegram`, `whatsapp_cloud` e os demais são import-only pelos ZIPs versionados no repositório `whatsbot-pro-plugins`, onde cada plugin mantém `src/`, `tests/`, JSON e ZIP. Se o usuário deletar um plugin import-only, ele não volta no próximo boot. **Exceção (plano 52)**: o `gowa` bundled tem upgrade **version-aware** — se a versão em `assets/plugin_examples/gowa/plugin.yaml` for maior que a instalada, o boot substitui `storages/plugins/gowa/` pela cópia bundled (edições manuais nessa pasta são perdidas; logado como warning).
- **IP do cliente atrás de proxy reverso**: quem decide "de que IP veio esta request" é [server/client_ip.py](server/client_ip.py) `client_ip(request)` — ponto ÚNICO do core, consumido pelo carimbo de ator da auditoria (`ip_address` na `audit_log`) e pelo bucket de rate-limit do login. Ele caminha o `X-Forwarded-For` da **direita para a esquerda** pulando hops confiáveis (loopback + faixas privadas + link-local + CGNAT por padrão; `WHATSBOT_TRUSTED_PROXIES` substitui a lista) e devolve o primeiro não-confiável. Nunca pegue `xff.split(",")[0]`: a parte esquerda do XFF é escrita pelo chamador e é **forjável** (envenena a auditoria e fura o rate-limit). Quando o navegador legítimo tem IP privado (acesso por VPN/LAN), "privado" não distingue proxy de cliente — nesse caso use `WHATSBOT_TRUSTED_PROXY_HOPS=<n>` (número exato de proxies), que é à prova de forja. Se TODO hop for confiável, cai no mais à esquerda. **Nenhum código do app recupera um IP que o proxy não mandou**: se o `X-Forwarded-For` que chega já é o do proxy, a perda é de infra (hop que faz SNAT / L7 sem repassar XFF) e o conserto é lá.
- **IP público autodeclarado pelo painel (`X-Client-Public-IP`, plano 86)**: quando o IP de origem morre num hop **antes** do proxy reverso (medido na instância: LAN, internet pública e o painel real chegam todos com o mesmo XFF privado), nenhuma resolução de rede o recupera — então quem informa é o **navegador**. [web/static/js/services/publicIp.js](web/static/js/services/publicIp.js) busca o IP público UMA vez por carregamento de página (`https://www.cloudflare.com/cdn-cgi/trace`, disparado em [app.js](web/static/js/app.js) antes do 1º render, com timeout de 3s) e `authHeaders()` ([httpClient.js](web/static/js/services/httpClient.js)) — o **seam único** por onde passam as ~47 montagens de cabeçalho do core **e** o transporte de plugin ([plugins/api.js](web/static/js/plugins/api.js)) — o injeta em toda chamada à API. No core, `audit_ip(request)` ([server/client_ip.py](server/client_ip.py)) prefere esse valor e cai em `client_ip()` quando ele falta. Pontos que **não** mudam: a coluna é a mesma `audit_log.ip_address` (sem migration), a tela `/audit` não muda, e **plugins não fazem nada** (herdam pelo `ActorCtx`) — exceto uma screen que use `fetch()` cru **sem** `authHeaders()`, que não enviará o cabeçalho. ⚠️ O valor é **autodeclarado, logo forjável** (decisão aceita): serve **só à auditoria** e é validado apenas contra lixo (exige `is_global` — privado/loopback/CGNAT/texto caem no IP de rede). O bucket de rate-limit do login continua em `client_ip()` e **nunca** deve migrar para `audit_ip()`: bastaria variar o cabeçalho a cada tentativa para anular o limite (travado pela suíte legada em `tests/core/legacy/legacy_endpoints.py`). Limitações conhecidas: o `POST /api/auth/login` costuma sair **sem** o cabeçalho (a busca ainda não voltou) e um IP que muda no meio da sessão (VPN) só é revisto no próximo reload. Falha da consulta (offline, bloqueio, CSP) degrada em silêncio. A CSP do core libera **só** o host exato `https://www.cloudflare.com` em `connect-src` ([server/app.py](server/app.py)); cada carregamento do painel revela a esse terceiro o IP do operador (`credentials: 'omit'`, sem outro dado).
- **Restart de plugin requer supervisor**: `enable`/`disable` chama `os._exit(0)` após um delay curto. Em Docker, `restart: unless-stopped` (compose) faz o container relançar; em dev, `restart.py` toca `server/_reload_trigger.py` (`.py` dentro de um `--reload-dir`, casa com o include default `*.py` do uvicorn) — o watchfiles reinicia o worker antes do `os._exit` rodar. O arquivo é regenerado em runtime e está no `.gitignore`. Em EXE Windows, o `update.py` relança. Sem supervisor, o servidor cai e não volta sozinho.
- **Prefixo de tabela enforced**: o migrator usa regex em `CREATE TABLE`/`ALTER TABLE`/`CREATE INDEX`/`DROP TABLE`/`DROP INDEX` e RECUSA migration que tente criar objeto fora do prefixo `plugin_<id>_`. Erro mostra qual nome violou. Usar comentários SQL `--` ou `/* */` é OK; o migrator os strip-a antes da validação.
- **Tool name é global**: se um plugin registra uma tool com nome já existente (core ou outro plugin), o registry loga warning e ignora a duplicata. Convenção: nomes específicos como `<id>_<verbo>` (ex: `orders_create`).
- **Import dinâmico de plugin JS**: o componente é carregado via `import(screen.component)` ES nativo. O path no manifest precisa começar com `/plugins/<id>/static/...` (servido pelo mount estático). CSP em `server/app.py` permite `'self'`, então funciona sem mudança.
- **Plugin com erro de carga**: se importação falha, o erro vai pra coluna `load_error` na tabela `plugins`, aparece no card da UI, e o plugin é pulado — o app sobe normalmente. Não há crash em cascata.
