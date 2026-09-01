# Barramento de plugins — eventos, filtros e media types

> Guia de eventos e filtros que um plugin pode observar ou interceptar. O [`CLAUDE.md`](../CLAUDE.md) carrega a **regra curta** e os avisos ⚠️;
> aqui está o **porquê**, o histórico e o detalhe. Texto migrado do `CLAUDE.md` no plano 139
> — nada foi reescrito na migração, só realocado.

⚠️ A fonte de verdade **executável** é [`plugins/events.py`](../plugins/events.py)
(`KNOWN_EVENTS`, `KNOWN_FILTERS`, `EXPERIMENTAL_FILTERS`), que traz a semântica de cada
filtro em comentário ao lado do nome. As tabelas abaixo são guia de **payload**, não
catálogo autoritativo — se divergirem, o código vence.

---

### Events e Filters (bus do plugin)

Plugins podem reagir a tudo que acontece no WhatsBot e modificar dados em trânsito sem editar o core. Dois mecanismos complementares (padrão WordPress: actions + filters; referências validadas em Baileys / WAHA / Home Assistant):

- **Events** — broadcast fire-and-forget, paralelo. Plugin exporta `EVENT_HANDLERS` em `<plugin>/events.py` e declara `entry.events: events` no manifest. Não bloqueia o pipeline principal; exceção em um handler nunca afeta outros.
- **Filters** — interceptive, síncrono no pipeline. Plugin exporta `FILTERS` em `<plugin>/filters.py` e declara `entry.filters: filters` no manifest. Recebe `(ctx, value)` e retorna valor modificado ou `None` pra abortar a ação envolvida. Exceção em um filter é isolada (loga + valor passa intacto ao próximo).

Toggle do plugin = tudo-ou-nada: enable liga handlers e filters; disable derruba ambos no próximo restart.

**Eventos comuns de mensagem/provider** (o catálogo executável completo é `plugins.events.KNOWN_EVENTS`):

| Evento | Quando dispara | Payload chave |
|--------|---------------|---------------|
| `message.received` | Inbound user msg (inclui group sem @mention). **Emitido ANTES do save** — listener que precisa ler do DB deve usar `message.saved` | `phone, name, text, raw_text, msg_id, media_type, media_path, media_extras, is_group, group_jid, individual_phone, raw` |
| `message.saved` | Emitido DEPOIS do INSERT no DB, nos 3 sites de save inbound (text batch, media batch, group_no_mention). **Um evento por MENSAGEM**, nas três procedências (plano 146) | `phone, channel_id, conversation_id, text, msg_id, media_type, media_path, media_extras, is_group, group_jid, source` — `source ∈ {batch_text, batch_media, group_no_mention}` |
| `message.sent` | Resposta IA, operator send, image/audio panel, retry, private @ia, template, echo do próprio celular | `phone, channel_id, conversation_id, text, msg_id, media_type, media_path, media_extras, source, status` — `source ∈ {ai, operator, private_ai, retry, template, echo}` |
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

⚠️ **A cardinalidade de `message.saved` com `source=batch_text` mudou (plano 146)** — passou de **1 evento por batch** para **1 evento por mensagem**, alinhada ao `batch_media`, que sempre foi assim. Antes, N mensagens que o cliente mandasse em poucos segundos viravam UMA linha no banco e UM evento levando o texto **combinado**; hoje cada mensagem tem a sua linha, o seu `msg_id` e o seu evento com o **seu** texto. Não é mudança de catálogo (nenhum nome novo, nenhum campo removido) e por isso **não há bump de `WHATSBOT_API_VERSION`** — mas é mudança de comportamento, e um assinante que assumisse "um evento por turno" precisa saber.

**O que revisar no seu handler:** (a) ação **idempotente** por conversa (get-or-create, upsert) continua correta e só paga N× o custo; (b) ação **alternante** — cancelar num evento e re-armar no seguinte — é o padrão que quebra, e é preciso um guard; (c) regra por **regex sobre o texto**: o `re.search` deixa de ver o bloco inteiro. Antes, uma frase que casasse suprimia o batch todo; agora cada mensagem responde por si. Os quatro assinantes reais do parque (`protocolos`, `retornos`, `telegram`, `debug_bus`) foram auditados no plano — nenhum quebra, e o caso (b) do `retornos` já era alcançável pelo `batch_media` no core anterior.

⚠️ **`channel_id` e `conversation_id` em `message.saved`/`message.sent` (API 1.3.0, plano 123)** — antes disso o bus dizia *de quem* veio a mensagem e nunca *por onde*, e um plugin que precisasse da conversa tinha de resolvê-la por telefone: `contact_repo.get_by_phone` (que casa as variantes BR de 12↔13 dígitos e devolve `.first()` **sem `ORDER BY`**) seguido de `conversation_repo.get_open_for_contact`, que **ignora o inbox**. Com o mesmo cliente em dois canais, o plugin escrevia na thread errada — foi o fechamento em cascata do `protocolos`. O dado sempre esteve no escopo (o `ws_manager.broadcast` logo acima de cada emit já o usava), só não era publicado. **`conversation_id` pode faltar**: só é publicado onde o id está de fato resolvido, então o `retry` (que só faz `UPDATE` de status) e a resposta da IA (`source="ai"`, cujo save é posterior ao envio) mandam apenas `channel_id` — trate `None`. Um plugin que EXIJA os campos declara `">=1.3,<2.0"` e falha duro num core anterior; quem precisa carregar nos dois fica em `">=1.0,<2.0"` e degrada para o caminho por telefone.

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
| `channel.duplicate_refused` | sweep de identidade recusou um canal duplicado ([channel_identity.py](../app/services/channel_identity.py)) — ator `system` |
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
| `filter.transcription.should_run` | wrapper `maybe_transcribe`, ANTES de chamar `transcribe_audio`/`describe_image`/`transcribe_document` (cobre TODOS os call sites, sandbox incluído desde o plano 118) | `bool` (default `True`) | Pula a transcrição (mesmo efeito de `False`) | `phone, media_kind ∈ {audio,image,document}, media_path, is_group, group_jid, source ∈ {batch,echo,operator,private}` |
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
| `filter.provisioning.number` | `provisioning_service.fetch_provision_target` (ex-`provisioning_service.fetch_provision_number`, que segue como atalho) DEPOIS de o core resolver o destino (`/service_number` → env `TECHIFY_PROVISION_NUMBER` → literal do código) | `str` | **Aborta o envio**: `None`/`""` ⇒ sem destino, o wizard recusa com erro acionável em vez de mandar a frase para um número que ninguém escolheu. O core não valida formato — normalizar é de quem responde | `source ∈ {service_number, fallback}, message` (a frase JÁ resolvida) |
| `filter.provisioning.message` | mesmo produtor, logo DEPOIS do de número — e **só se houver destino** | `str` | **Aborta o envio** (`no_message`): mensagem vazia queima a única abertura de conversa com um contato novo | `source, number` (o destino já decidido) |
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

Plugin **auto-instalado** no fresh install (plano 33 D3, [plugins/bootstrap.py](../plugins/bootstrap.py) `BUNDLED_AUTO_INSTALL`): **apenas `gowa`** (canal WhatsApp via GOWA, ATIVO por padrão). Os outros providers e extensões são instalados sob demanda por **Importar `.zip`**. A fonte de desenvolvimento, os testes e os artefatos publicados desses plugins vivem no repositório irmão `whatsbot-pro-plugins`, na forma `plugins/<id>/{src,tests,<id>.json,<id>.zip}`. O diretório `tests/` nunca entra no ZIP e nunca é executado durante instalação, atualização ou boot de produção.

`assets/plugin_examples/` contém **exclusivamente o `gowa`** — é a fonte do único plugin bundled, não uma bancada. Nenhum outro plugin é desenvolvido aqui: a fonte deles é o repositório externo, e mudanças no GOWA devem manter a cópia publicada sincronizada.

⚠️ **Um teste do core NUNCA deve fixar `assets/plugin_examples/<id>` para um `<id>` que não seja `gowa`.** A fonte se resolve por [tests/plugin_test_utils.py](../tests/plugin_test_utils.py) (`resolve_plugin_source`), que prefere `WHATSBOT_PLUGIN_SOURCE_ROOT/<id>/src`. Um bloco que assere comportamento específico de provider ausente deve **pular**, não falhar — é o que o helper `plugin_source_or_skip` faz em [tests/core/legacy/legacy_endpoints.py](../tests/core/legacy/legacy_endpoints.py). Cuidado com a armadilha: o resolvedor também cai em `storages/plugins/<id>`, então uma máquina de desenvolvimento com o plugin **instalado** fica verde enquanto um clone limpo fica vermelho — valide num worktree limpo.

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

Regra de versão do catálogo — caso particular da regra geral em "Versionamento da API de plugins": remover/renomear um filtro **com produtor vivo** exige MAJOR em `WHATSBOT_API_VERSION` (que hoje derrubaria os 36 manifests do parque de uma vez). Retirar um nome apenas documentado, sem `apply_filter` no core suportado, é PATCH — nenhum comportamento executável deixa de existir —, mas exige varredura, entrada em [docs/PLUGIN_API_CHANGELOG.md](../docs/PLUGIN_API_CHANGELOG.md) e teste de WARNING como o caso acima. **Acrescentar** nome é MINOR, no MESMO commit do call site — travado por `test_bus_catalogue_matches_producers`, que compara o catálogo com os produtores reais nas duas direções.
