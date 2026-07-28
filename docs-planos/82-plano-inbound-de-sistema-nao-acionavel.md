# Plano 82 — Inbound de SISTEMA não-acionável: card no fio sem abrir conversa, sem IA, sem automação (genérico p/ todos os canais)

> **Status:** IMPLEMENTADO (F0–F4 ✅, F5 aguardando deploy manual) · **Data:** 2026-07-24 · **Escopo:** médio (1 contrato + 1 costura no core + 1 provider fino + testes + distribuição)
> **Origem:** incidente real em produção (instância **Redes Brasil**, canal **Atendimento** `whatsapp_cloud_bc081279`, conversa **15033**, 24/07 13:58). A Meta entregou um webhook `type: system` / `user_changed_number` (o cliente 556881215248 trocou para 556992412393). Hoje o WhatsBot trata isso como **mensagem normal do cliente**: cria contato-fantasma do número **antigo** (contato **14787**, `created_at` = 13:58:27), abre **protocolo PROT-20260724-165831-15040**, a **IA (BIA) responde** ao número morto → **erro 131026 "Message undeliverable"**. **Método:** leitura do `debug_bus` de produção (reconstrução da timeline do `exec_id 2350`), consulta ao banco de produção via Vault (canal, credenciais, contato, `messages`), Meta Graph API (`subscribed_apps` = só o campo `messages`; número GREEN/CONNECTED; BSUID `user_id` já ativo), documentação oficial da Meta (apêndice §11) e investigação do código com 8 frentes paralelas retornando `arquivo:linha` verificado.
> Um evento de sistema deixa de virar mensagem acionável: passa a ser um **card painel-only** (role já excluído do LLM/sidebar/badge) anexado à conversa **existente**, **sem** criar/reabrir conversa, **sem** acionar a IA e **sem** disparar plugins de automação (protocolos). O mecanismo é **estrutural e genérico no core** (novo `kind="system"` no `InboundEvent` + um ramo no dispatch), **fino em cada provider** (só o `whatsapp_cloud` muda hoje) — mesmo padrão policy-vs-mechanism dos outros ganchos de canal.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 1. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| **D1** ✅ 2026-07-24 | **Histórico partido fica quieto, duplicando.** NÃO migrar/mesclar o contato do número antigo para o novo (`system.wa_id`). "Pensamos nisso depois." | **Fora de escopo** a migração de contato. O número novo, quando mandar mensagem, vira contato separado (comportamento atual). O plano só evita a IA/protocolo indevidos. Ver F.P.#1. |
| **D2** ✅ 2026-07-24 | **Um evento de sistema deve APARECER como mensagem de sistema no fio, mas sem abrir protocolo, sem abrir/reabrir conversa e sem acionar a IA.** | Núcleo do plano: card painel-only anexado à conversa existente + gate de não-acionabilidade no dispatch. |
| **D3** ✅ 2026-07-24 | **Estrutural, para QUALQUER canal — não só o WhatsApp oficial.** | O gate mora no **core** (contrato `InboundEvent.kind="system"` + ramo genérico em `_dispatch_events`), não no plugin. Cada provider só **declara** o kind. Zero `if provider ==` no core. |
| **D4** ✅ 2026-07-24 | **Reaproveitar a estrutura de "filtro de rejeitos" da IA** e fazer com que **outros plugins de automação saibam quando agir ou não.** | Reuso em DOIS eixos: (a) o **role painel-only** (`conversation_event`) já é a lista-negra do contexto do LLM/sidebar/badge — o card herda isso de graça; (b) o inbound de sistema emite um **evento de bus distinto** (`channel.system_event`), NÃO `message.saved`/`message.received` — plugins de automação (protocolos) simplesmente não disparam; quem quiser reagir **opta** por escutar o evento novo. Ver §7. |
| **D5** ✅ 2026-07-24 | O único caso concreto hoje é a Cloud API da Meta (`type: system`). GOWA/Telegram/website **não emitem** nada análogo hoje. | Só o provider `whatsapp_cloud` muda. GOWA já discrimina eventos de sistema em kinds próprios (modelo); Telegram (`migrate_to_chat_id`, `my_chat_member`) é o análogo futuro → **P5 (adiado)**. |
| **P** (princípio) | Padrão do repo: **o provider declara, o core só avalia** — nenhum `if provider ==` no core. Nada em produção depende deste caminho hoje ⇒ pode-se costurar direto, sem stopgap. | O `kind` é o contrato; o dispatch é o avaliador genérico. |

---

## 2. Resumo executivo

Um webhook `type: system` da Cloud API (mudança de número / mudança de identidade) é hoje classificado pelo plugin como **`kind="message"`** ([whatsapp_cloud/channels.py:1107](../assets/plugin_examples/whatsapp_cloud/channels.py#L1107), fundido com `contacts`/`order`), entra no **funil agêntico** `ingest_event` ([message_ingest_service.py:334](../app/services/message_ingest_service.py#L334)) e roda tudo: materializa contato, **cria/reabre a conversa** ([:481](../app/services/message_ingest_service.py#L481)), emite `message.received`/`message.saved` (que **abrem protocolo** — [protocolos/events.py:18](../storages/plugins/protocolos/events.py#L18)) e **aciona a IA** ([messaging_service.py:941](../app/services/messaging_service.py#L941)). Como o `chat_id` é o número **antigo** (morto), a resposta da IA falha com **131026**.

A solução é **estrutural**: (A) um novo valor de contrato **`InboundEvent.kind = "system"`** — o campo já aceita qualquer string ([channels/events.py:18](../channels/events.py#L18)) e o funil **já ignora** `kind != "message"` ([message_ingest_service.py:350](../app/services/message_ingest_service.py#L350)), então nenhum provider aciona IA/conversa/plugin ao emiti-lo; (B) **um ramo genérico** `elif kind == "system":` no dispatch central ([channel_webhook.py:543](../server/routes/channel_webhook.py#L543)) que grava um **card painel-only** (`conversation_event`) na conversa **já existente** (via [get_open_for_contact_scoped](../db/repositories/conversation_repo.py#L304) — get-sem-criar), faz broadcast e emite o bus **`channel.system_event`** — **sem** chamar `ingest_event`; (C) o provider `whatsapp_cloud` **fino**: separar `system` num ramo próprio com `kind="system"` ([channels.py:1107](../assets/plugin_examples/whatsapp_cloud/channels.py#L1107)). O texto do card já existe (`describe_system` — [inbound_text.py:265](../assets/plugin_examples/whatsapp_cloud/inbound_text.py#L265)).

O card fica **invisível para a IA** (o role `conversation_event` já está na lista-negra do contexto — [message_repo.py:184](../db/repositories/message_repo.py#L184)), **fora da sidebar/preview** ([_mapping.py:103](../db/repositories/_mapping.py#L103)) e **não conta como não-lida** (salvo com `msg_id=None` — unread é opt-in por `msg_id`, [unread_repo.py:41](../db/repositories/unread_repo.py#L41)). Reusa o role **existente** ⇒ **zero migration**.

---

## 3. Como funciona hoje (mapa)

### 3.1 Timeline do incidente (reconstruída do `debug_bus`, `exec_id 2350`, 24/07)

| hora | evento | o que aconteceu |
|---|---|---|
| 13:58:27 | `filter.message.before_save` | passou (`media_type: "system"`) |
| 13:58:27 | contato **14787** criado | **contato-fantasma do número antigo** 556881215248 (`created_at` verificado no banco) |
| 13:58:27 | `message.received` / `message.saved` | plugin `protocolos` abriu **PROT-20260724-165831-15040** |
| 13:58:31 | `llm.before`/`llm.after` (gpt-5.2) | **IA (BIA) respondeu** "Oi! 😊 Eu sou a BIA da Redes Brasil…" |
| 13:58:42 | `message.failed` / `receipt.changed` | Meta devolveu **131026 "Message undeliverable"** (número morto) |

Payload da Meta (verbatim): `messages[].system = {body:"User A changed from 556881215248 to 556992412393", wa_id:"556992412393", type:"user_changed_number"}`, `from: "556881215248"` (o número **antigo**). Estado de produção medido: **1 única** linha `media_type='system'` em todo o histórico (este incidente); o número novo **não existe** como contato.

### 3.2 Caminho de um inbound `type: system` hoje (o bug)

| # | Etapa | Arquivo:linha | O que acontece com `type: system` |
|---|---|---|---|
| 1 | Webhook chega no funil único | [channel_webhook.py:276](../server/routes/channel_webhook.py#L276) | `_dispatch_events` itera os `InboundEvent` |
| 2 | `parse_inbound` (provider) | [whatsapp_cloud/channels.py:971](../assets/plugin_examples/whatsapp_cloud/channels.py#L971) | itera `messages[]`/`statuses[]` |
| 3 | `_parse_message` | [channels.py:1107](../assets/plugin_examples/whatsapp_cloud/channels.py#L1107) | `elif msg_type in ("contacts","order","system"):` → `media_type="system"`, `text=describe_message(msg)` |
| 4 | **Retorno** | [channels.py:1137-1150](../assets/plugin_examples/whatsapp_cloud/channels.py#L1137) | `InboundEvent(kind="message", direction="in", …)` — **kind literal "message"**, `chat_id = msg["from"]` (nº antigo) |
| 5 | Dispatch | [channel_webhook.py:292](../server/routes/channel_webhook.py#L292) | `if kind == "message": await ingest(ev)` — **único ramo agêntico** |
| 6 | Ingest: contato | [message_ingest_service.py:410](../app/services/message_ingest_service.py#L410) | materializa o **contato-fantasma** do nº antigo |
| 7 | Ingest: **conversa** | [message_ingest_service.py:481](../app/services/message_ingest_service.py#L481) | `ensure_conversation_live("user", _reopen)` → **cria/reabre** |
| 8 | Ingest: bus | [message_ingest_service.py:519](../app/services/message_ingest_service.py#L519) | emite `message.received`; o batch emite `message.saved` ([messaging_service.py:919](../app/services/messaging_service.py#L919)) → **protocolos abre ciclo** |
| 9 | IA | [messaging_service.py:941](../app/services/messaging_service.py#L941) | `aprocess_message(...)` responde ao **número morto** → **131026** |

⚠️ **Gotcha decisivo (etapa 4→5):** o `kind` é o único discriminador de rota. Como o provider devolve `"message"`, o evento entra no funil agêntico. **Mudar o kind na origem (etapa 4) redireciona as etapas 5–9 de uma vez.**

### 3.3 O contrato e o dispatch (o que já existe a favor)

| Fato | Arquivo:linha | Consequência |
|---|---|---|
| `InboundEvent.kind` é **string livre**, default `"message"`, sem enum | [channels/events.py:18](../channels/events.py#L18) | adicionar `"system"` **não** exige mudança de schema — só produtor (provider) + consumidor (dispatch) |
| Único gate por-evento hoje é `trigger_ai: bool` | [channels/events.py:42](../channels/events.py#L42) | **insuficiente**: `trigger_ai=False` ainda salva, faz broadcast e emite `message.saved` ([message_ingest_service.py:523](../app/services/message_ingest_service.py#L523)) ⇒ protocolos ainda dispararia |
| `ingest_event` faz `if kind != "message": return` | [message_ingest_service.py:350](../app/services/message_ingest_service.py#L350) | **defesa em profundidade grátis**: um `kind="system"` nunca aciona IA/conversa/plugin mesmo se vazar para o funil |
| Dispatch roteia por kind; **não há `else`** | [channel_webhook.py:288-543](../server/routes/channel_webhook.py#L288) | kind desconhecido é **silenciosamente descartado** (sem card). Precisa de um `elif kind == "system":` novo no boundary [543/544](../server/routes/channel_webhook.py#L543) |
| Precedente de card painel-only **inbound** já no dispatch | [channel_webhook.py:491-518](../server/routes/channel_webhook.py#L491) | ramo `group_participants` grava card `system_notice` **só se o contato existe** ([:501-502](../server/routes/channel_webhook.py#L501)) — o modelo mais próximo (mas ainda usa `add_message`, que **cria conversa** se não houver — cuidado, ver R2) |

### 3.4 Role painel-only já é a "lista de rejeitos" (D4-a)

Uma linha salva com role **`conversation_event`** já é invisível às três superfícies — **sem código novo**:

| Superfície | Onde exclui | Prova |
|---|---|---|
| Contexto do LLM | [message_repo.py:184-185](../db/repositories/message_repo.py#L184) e [:216-217](../db/repositories/message_repo.py#L216) | `excluded = ("transcription","tool_call","system_notice","conversation_event","system","error")` (DUAS cópias literais) |
| Sidebar / preview / upsert de lista | [_mapping.py:103](../db/repositories/_mapping.py#L103) (`LIST_PANEL_ONLY_ROLES`), usado por [conversation_query.py:20](../db/repositories/conversation_query.py#L20) e [message_listeners.py:54](../agent/message_listeners.py#L54) | `conversation_event` no set |
| Badge de não-lidas | [unread_repo.py:41](../db/repositories/unread_repo.py#L41) | unread é **opt-in por `msg_id`**; card salvo com `msg_id=None` nunca sobe o contador |
| Frontend (skip de preview) | [useConversationWsEvents.js:36](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L36) | 3ª cópia do set, inclui `conversation_event` |
| Não vai ao WhatsApp | [db/tables.py:168,177](../db/tables.py#L168) (CHECK) + docstring [system_notices.py:16](../server/system_notices.py#L16) | role já permitido pelo CHECK ⇒ **reuso = zero migration** |

### 3.5 O sink de card que já resolve "conversa existente sem criar"

`system_notices.emit_for_contact(*, event_type, contact_id, inbox_id, **ctx)` ([system_notices.py:406](../server/system_notices.py#L406)) resolve a conversa **existente** (`open → latest`, [:417](../server/system_notices.py#L417)) e **retorna `None` se não houver** (nunca cria), gravando `conversation_event` via `message_repo.add(..., conversation_id=…)` ([:464](../server/system_notices.py#L464)). É exatamente o padrão de "anexar sem criar". O get-sem-criar puro é [conversation_repo.get_open_for_contact_scoped](../db/repositories/conversation_repo.py#L304) (usado também por `maybe_emit_ai_takeover`, [messaging_service.py:777](../app/services/messaging_service.py#L777)).

---

## 4. Inventário / análise

| Item | Arquivo:linha | O que falta | Abordagem | Risco | Esf. |
|---|---|---|---|---|---|
| Contrato `kind="system"` | [channels/events.py:18](../channels/events.py#L18) | documentar o valor + (opcional) subtipo estruturado em `media_extras` | comentar o vocabulário; provider seta `kind="system"` + `media_extras={system_type, wa_id, body}` | baixo | S |
| Ramo genérico no dispatch | [channel_webhook.py:543](../server/routes/channel_webhook.py#L543) | novo `elif kind == "system":` | resolve conversa existente → grava `conversation_event` → broadcast → bus `channel.system_event`; **não** chama `ingest` | médio | M |
| Provider `whatsapp_cloud` | [channels.py:1107](../assets/plugin_examples/whatsapp_cloud/channels.py#L1107) | separar `system` de `contacts`/`order` → early-return `kind="system"` | espelhar o ramo `reaction` ([:1082](../assets/plugin_examples/whatsapp_cloud/channels.py#L1082)); texto de `describe_system` | baixo | S |
| Evento de bus não-acionável | [channel_webhook.py](../server/routes/channel_webhook.py) (novo) | um nome de evento novo (`channel.system_event`) | emitir no ramo novo; **nunca** reusar `message.saved`/`message.received` | baixo | S |
| Testes | [tests/test_plano75_parse_inbound.py:204](../tests/test_plano75_parse_inbound.py#L204) + novos | asserts que quebram + cobertura de não-acionabilidade | atualizar 3 asserts; novo teste dirigindo o dispatch; teste protocolos-não-abre | médio | M |
| Distribuição | [assets/channel_plugins/whatsapp_cloud-plugin.zip](../assets/channel_plugins/) | regenerar zip + importar em prod | snippet do README; **core atualizado ANTES** do zip (R4) | médio | S |
| (Opcional) Limpeza do fantasma | prod: contato 14787 + PROT-…-15040 | remover o contato-fantasma e resolver o protocolo espúrio | script pontual read→confirm→write (backup antes) | baixo | S |

### Falsos positivos descartados

| # | Hipótese | Por que NÃO é problema |
|---|---|---|
| **1** | "Precisa migrar o contato para o número novo (`system.wa_id`)." | ❌ **D1**: o usuário decidiu deixar duplicando. Migração/merge é plano futuro. O escopo aqui é **parar a IA/protocolo indevidos**, não reconciliar identidades. (A Meta recomenda migrar; o BSUID `user_id` já ativo seria a âncora — mas fica para depois.) |
| **2** | "Basta setar `trigger_ai=False` no provider." | ❌ [message_ingest_service.py:523](../app/services/message_ingest_service.py#L523): `trigger_ai=False` **ainda** materializa contato, **cria/reabre conversa** ([:481](../app/services/message_ingest_service.py#L481)) e emite `message.saved` ([:536](../app/services/message_ingest_service.py#L536)) → protocolos abre. Confirmado pela caracterização (`test_classify_group_no_mention` assere `message.saved` disparado). Precisa de um **kind próprio**, não do flag. |
| **3** | "Criar um role novo `system` na tabela `messages`." | ❌ Desnecessário e caro: o role `conversation_event` **já** é painel-only em todas as superfícies e já passa no CHECK ([db/tables.py:168](../db/tables.py#L168)). Reusar = **zero migration**. (O role `system` também existe e é painel-only, mas `conversation_event` é o que `system_notices` já escreve — menos superfície nova.) |
| **4** | "Reusar `contact_obj.add_message('system_notice', …)` como faz o `group_participants`." | ⚠️ **Meio**: `add_message` ([memory.py:451](../agent/memory.py#L451)) chama `_resolve_conversation`, que para um contato **sem** conversa cai em `_create_open_atomic` e **cria uma conversa aberta** (create_closed só quando `reopen is False`). Ou seja, reusar cru **violaria D2** ("não abrir conversa"). Tem que ir por `get_open_for_contact_scoped` (get-sem-criar) + `message_repo.add(conversation_id=…)`. |
| **5** | "O `filter.message.before_save` retornando `None` já resolveria." | ❌ Ele faz a mensagem **sumir** (nem card, nem nada — [message_ingest_service.py:454](../app/services/message_ingest_service.py#L454)). Viola D2 ("aparecer como mensagem de sistema"). É tudo-ou-nada, não o meio-termo pedido. |
| **6** | "Isso é bug do WhatsApp Cloud, então conserta no plugin." | ⚠️ **Não** (D3): o **gate** é genérico (contrato + dispatch, no core); só a **declaração** do kind é do provider. Telegram tem o análogo (`migrate_to_chat_id`) e um dia entra sem tocar o core. |
| **7** | "GOWA/website também precisam mudar." | ❌ GOWA já discrimina eventos de sistema em kinds próprios (`group_participants`/`group_joined` — [gowa/inbound.py:547](../gowa/inbound.py#L547)); não há hoje um aviso whatsmeow de troca-de-número. Website nunca tem inbound de sistema. **Nenhuma mudança** neles. |
| **8** | "Registrar o card via `system_notices` obriga a criar grupo/config + toggle." | ⚠️ Só **se** for pelo `emit_conversation_notice` (que usa FORMATTERS + gate de grupo). O plano grava o card **direto** no ramo do dispatch com o texto que o provider já formatou (`describe_system`), sem passar pelo registry de notices — evita o config-gate e o registro de grupo. `system_notices` fica como referência de padrão, não dependência. (Ver P4.) |

---

## 5. Mudanças de infraestrutura (por camada)

**Contrato (channels/):**
- `InboundEvent.kind` ganha o valor documentado **`"system"`** ([channels/events.py:18](../channels/events.py#L18)); opcionalmente um subtipo estruturado em `media_extras` (`{system_type, wa_id, body}`) para os plugins que quiserem reagir.

**Core (server/ + app/):**
- Novo ramo `elif kind == "system":` em `_dispatch_events` ([channel_webhook.py:543](../server/routes/channel_webhook.py#L543)) — **genérico, sem `if provider ==`**. Fluxo do ramo:
  1. `existing = contact_repo.get_by_phone(ev.chat_id)` — **só age se o contato já existe** (espelha o guard do `group_participants`, [:501-502](../server/routes/channel_webhook.py#L501)); senão apenas `log` + `handled += 1` e segue (D2: não materializa contato/conversa novos).
  2. `conv = conversation_repo.get_latest_for_contact(existing["id"])` — pega a conversa **aberta OU fechada** mais recente (P2). Anexa o card **sem** `set_status`/reabrir (conversa fechada continua fechada). Se `None` ⇒ log + segue (sem card, sem criar conversa).
  3. `message_repo.add(existing["id"], "conversation_event", ev.text or ev.display_text, conversation_id=conv["id"], msg_id=None)` — card painel-only, sem unread.
  4. `ws_manager.broadcast("new_message", {phone, message:{role:"conversation_event", …}})`.
  5. `emit_with_filter("channel.system_event", {phone, channel_id, system_type, wa_id, body, conversation_id, ts, raw})` — **evento novo, não-acionável**.
  6. **NÃO** chama `deps.ingest_event` ⇒ sem IA, sem conversa nova, sem `message.saved`/`message.received`.
- Invariante mantida: o guard `kind != "message" → return` em [message_ingest_service.py:350](../app/services/message_ingest_service.py#L350) continua sendo a rede de segurança (se um `kind="system"` vazar para o funil, não faz nada).

**Provider (assets/plugin_examples/whatsapp_cloud/):**
- `_parse_message` ([channels.py:1107](../assets/plugin_examples/whatsapp_cloud/channels.py#L1107)): tirar `"system"` da tupla `("contacts","order","system")` e criar um **early-return** `kind="system"` (espelhando `reaction`, [:1082](../assets/plugin_examples/whatsapp_cloud/channels.py#L1082)) com `media_extras = {"system_type": sys.get("type"), "wa_id": sys.get("wa_id"), "body": sys.get("body")}` e `text = describe_message(msg)`.
- Replicar em `storages/plugins/whatsapp_cloud/channels.py:1107` (cópia rodando; hoje byte-idêntica) + regenerar o zip.

**DB:** **nenhuma** migration (reuso do role `conversation_event`).

**Frontend:** **nenhuma** mudança obrigatória — `conversation_event` já renderiza como card centralizado e já é pulado no preview ([useConversationWsEvents.js:36](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L36)). (O texto do card vem do provider com prefixo `ℹ️`.)

---

## 6. Waves e paralelização

```
WAVE 0   F0 (caracterização do bug atual + confirmar paridade das 3 cópias)     🔴 barreira
             │  (F0 trava o golden do comportamento que estamos mudando)
             ▼
WAVE 1   F1 (contrato kind="system")  ·  F2 (ramo genérico no dispatch)
             └── F1 e F2 tocam arquivos DISJUNTOS (events.py × channel_webhook.py) ⇒ 🟢
             │       (F2 referencia o valor de F1, mas não depende do commit — a string "system" é acordada no plano)
             ▼
WAVE 2   F3 (provider whatsapp_cloud) [dep F1+F2]   ·   F4 (testes) [dep F2+F3]
             ▼
WAVE 3   F5 (distribuição do zip)  🔴   ·   F6 (limpeza do fantasma em prod, opcional)  🟢
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | Baseline | 🔴 sozinha | baixo | golden do comportamento atual verde + 3 cópias do plugin confirmadas em sincronia |
| 1 | **F1** | Contrato | 🟢 | baixo | `kind="system"` documentado; testes de contrato passam |
| 1 | **F2** | Core | 🟢 `[dep: F1 doc]` | médio | um `InboundEvent(kind="system")` injetado vira card, sem IA/conversa/protocolo |
| 2 | **F3** | Provider | 🟢 `[dep: F1+F2]` | baixo | `type: system` da Meta produz `kind="system"` no `parse_inbound` |
| 2 | **F4** | Testes | 🟢 `[dep: F2+F3]` | médio | suíte verde no Postgres; asserts antigos atualizados; não-acionabilidade coberta |
| 3 | **F5** | Deploy | 🔴 sozinha | médio | zip novo importado em prod **após** core atualizado; canal vivo |
| 3 | ~~**F6**~~ | Dados (opcional) | ⛔ | baixo | **Não nesta rodada** (P7 ✅ deixar como está) |

**Despache junto:** `F1 · F2` (wave 1) e depois `F3 · F4` (wave 2). F0, F5 sequenciais; **F6 não executa** nesta rodada (P7 ✅ deixar como está).

---

## 7. Fases

### F0 — Caracterização do bug atual + paridade das 3 cópias 🔴

**Objetivo:** travar, num golden, o comportamento que estamos prestes a mudar (system → mensagem acionável) e garantir que se edita o código que roda em produção.

**Itens** `[sequencial]`:
1. Confirmar paridade `assets/plugin_examples/whatsapp_cloud/` × `storages/plugins/whatsapp_cloud/` × `assets/channel_plugins/whatsapp_cloud-plugin.zip` (a investigação viu **idênticos** — reconfirmar com `diff`).
2. Golden do comportamento atual: dirigir `deps.ingest_event(InboundEvent(kind="message", media_type="system", text="ℹ️ …"))` (espelhar o padrão de [tests/test_plano75_safety_net.py:155](../tests/test_plano75_safety_net.py#L155)) e registrar que hoje **cria conversa + emite `message.saved`** — é o que a F2/F4 vão inverter.
3. Rodar `tests/test_plano75_parse_inbound.py` e `tests/test_plano75_cloud_inbound_text.py` para saber exatamente quais asserts falarão sobre `system` (a F4 vai atualizá-los).

**Pronto quando:** as 3 cópias batem, o golden atual está verde e a lista de asserts a mudar está registrada no Status de execução.

#### Status de execução — Fase 0
**Estado:** ✅ Concluída (2026-07-24)
- **O que foi feito:** Paridade das 3 cópias confirmada por `diff`: `assets/plugin_examples/whatsapp_cloud/{channels.py,inbound_text.py}` × `storages/plugins/whatsapp_cloud/*` = **idênticos**; `assets/channel_plugins/whatsapp_cloud-plugin.zip` extraído → `channels.py` **byte-idêntico** ao de assets; todos `plugin.yaml` em **v1.5.0**. Caracterizado o caminho atual do bug: `_parse_message` ([channels.py:1107](../assets/plugin_examples/whatsapp_cloud/channels.py#L1107)) funde `system` em `("contacts","order","system")` → `media_type="system"`, retorno `kind="message"` ([:1137](../assets/plugin_examples/whatsapp_cloud/channels.py#L1137)) → dispatch `if kind=="message": ingest` ([channel_webhook.py:292](../server/routes/channel_webhook.py#L292)) → cria conversa + `message.saved` + IA respondendo ao número morto (131026). Confirmadas as funções-alvo do ramo F2: `conversation_repo.get_latest_for_contact(contact_id)` ([conversation_repo.py:264](../db/repositories/conversation_repo.py#L264), aberta OU fechada) e `message_repo.add(..., conversation_id=…, msg_id=None)` ([message_repo.py:15](../db/repositories/message_repo.py#L15)); `contact_repo`/`conversation_repo`/`message_repo` já importados no dispatch ([channel_webhook.py:28-29](../server/routes/channel_webhook.py#L28)).
- **Como foi feito / decisões:** Baseline foi verificado pelos testes existentes (não escrevi golden novo do comportamento antigo — seria descartado na F2/F4; a caracterização por leitura + testes verdes é suficiente e evita lixo). Asserts a atualizar na F4: `test_system_usa_o_body_pronto_da_meta` ([:204](../tests/test_plano75_parse_inbound.py#L204)) ganha `ev.kind == "system"`; `test_system_nao_traz_contacts…` ([:210](../tests/test_plano75_parse_inbound.py#L210)) e `test_todos_os_tipos_do_apendice…` ([:256](../tests/test_plano75_parse_inbound.py#L256)) **seguem verdes** porque o novo early-return mantém `media_type="system"` e `text` preenchido.
- **Problemas / pendências:** Nenhuma. `WHATSBOT_TEST_DB_URL` apontando para `whatsbot_test@10.8.200.13`.
- **Verificação:** `pytest tests/test_plano75_parse_inbound.py` = **42 passed**; `pytest tests/test_plano75_cloud_inbound_text.py` = **119 passed**. Verde.

---

### F1 — Contrato: `InboundEvent.kind = "system"` 🟢 `[wave 1]`

**Objetivo:** tornar `"system"` um valor de kind de 1ª classe, documentado, com subtipo estruturado opcional — sem tocar em nenhum consumidor ainda.

**Itens** `[paralelo entre si]`:
1. [channels/events.py:18](../channels/events.py#L18) — acrescentar `"system"` ao comentário do vocabulário (`message | reaction | receipt | presence | system | …`) e documentar no bloco de comentário ([:31-40](../channels/events.py#L31)) o contrato do inbound de sistema: **card painel-only, sem conversa nova, sem IA, sem automação**.
2. Convencionar o `media_extras` do system: `{"system_type": str, "wa_id": str|None, "body": str}` — para o `channel.system_event` carregar dado estruturado além do texto.
3. (Sem código de consumidor nesta fase — é só contrato/documentação.)

**Pronto quando:** `channels/events.py` documenta `kind="system"` e o formato do `media_extras`; nada quebra (mudança só de comentário/contrato).

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-07-24)
- **O que foi feito:** [channels/events.py:18](../channels/events.py#L18) — `"system"` acrescentado ao vocabulário do comentário de `kind`. Adicionado bloco de documentação do contrato do inbound de sistema logo após `is_archived` ([events.py:50+](../channels/events.py#L50)): descreve o `kind="system"` como card painel-only na conversa existente, sem criar/reabrir conversa, sem materializar contato, sem IA, sem `message.saved`/`message.received` (emite `channel.system_event`), e convenciona `media_extras = {"system_type", "wa_id", "body"}` + semântica de `chat_id` (identificador antigo) × `wa_id` (nova identidade).
- **Como foi feito / decisões:** Só contrato/documentação — nenhum consumidor tocado nesta fase (o dataclass já aceita string livre em `kind`, então zero mudança de schema). A convenção do `media_extras` ficou no docstring do dataclass (fonte única) em vez de um TypedDict novo, para não introduzir tipo que os providers teriam de importar (mantém o import-defensivo dos plugins).
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `python -c "import channels.events"` OK (mudança é só comentário/docstring); import do dataclass intacto.

---

### F2 — Núcleo genérico: ramo `kind == "system"` no dispatch 🟢 `[wave 1, dep: F1 doc]`

**Objetivo:** um inbound `kind="system"`, de **qualquer** provider, vira card painel-only na conversa existente — sem IA, sem conversa nova, sem automação. Zero `if provider ==`.

**Itens** `[sequencial dentro da fase]`:
1. Inserir `elif kind == "system":` como **último ramo** de `_dispatch_events`, no boundary [channel_webhook.py:543/544](../server/routes/channel_webhook.py#L543) (herda `message_repo`, `ws_manager`, `emit_with_filter`, `contact_repo`, `agent_handler` já em escopo; o `try/except` de [:544](../server/routes/channel_webhook.py#L544) isola falhas — mesmo contrato dos outros ramos).
2. Corpo (ver §5): guard "só se contato existe" → `get_latest_for_contact` (aberta OU fechada, **sem reabrir** — P2) → `message_repo.add(role="conversation_event", conversation_id=…, msg_id=None)` → `broadcast("new_message", …)` → `emit_with_filter("channel.system_event", …)`. **Não** chamar `ingest`. Se não houver conversa nem contato ⇒ log + segue (nada gravado).
3. **Nunca** emitir `message.saved`/`message.received` neste ramo (é o que impede protocolos — D4-b).
4. Manter o guard de rede em [message_ingest_service.py:350](../app/services/message_ingest_service.py#L350) como invariante (não remover).

**Pronto quando:** um teste que injeta `InboundEvent(kind="system", …)` no dispatch cobre os 3 casos de P2 — (a) contato com conversa **aberta** → **1 card** `conversation_event`, **0** `ingest_event`, **0** `message.saved`/`message.received`, conversa segue aberta; (b) contato com conversa **fechada** → card gravado e conversa **continua fechada** (sem reabrir); (c) contato **sem** conversa (ou sem contato) → **nada** gravado, contato/conversa **não** criados.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-07-24) — verificado por teste na F4
- **O que foi feito:** Novo ramo `elif kind == "system":` como último de `_dispatch_events` ([channel_webhook.py:544](../server/routes/channel_webhook.py#L544), boundary 543/544, antes do `except`). Fluxo: (1) dedup por `{channel_id}:{external_msg_id}` contra `state.processed_messages` (espelha o funil, [message_ingest_service.py:384](../app/services/message_ingest_service.py#L384)) → reentrega da Meta = `continue` (sem card, sem bus, sem `handled`); (2) resolve contato via `contact_repo.get_by_phone` — **não materializa** se ausente; (3) `conversation_repo.get_latest_for_contact` (aberta OU fechada); (4) card `conversation_event` via `message_repo.add(..., conversation_id=conv_id, msg_id=None)` **só se** `conv and card_text` — **sem** `set_status` (conversa fechada segue fechada, P2); (5) `broadcast("new_message", …)`; (6) `emit_with_filter("channel.system_event", {phone, channel_id, system_type, wa_id, body, conversation_id, ts, raw})` **sempre** (hook opt-in — carrega `wa_id` p/ futura migração D1/P6). **Nunca** chama `ingest`, **nunca** emite `message.saved`/`message.received`.
- **Como foi feito / decisões:** (a) **P3 resolvido** — dedup por `external_msg_id` no `state.processed_messages` compartilhado com o ingest; reentrega não duplica card nem re-emite o bus. (b) `channel.system_event` é emitido **mesmo sem conversa/contato** (é fire-and-forget, opt-in; não é "gravar" — respeita P2 (c) "nada gravado" = nenhuma escrita no DB) e carrega `conversation_id=None` nesse caso. (c) Guard extra `card_text` não-vazio evita card em branco quando `describe_system` devolve "". (d) Reuso do role `conversation_event` ⇒ **zero migration** (já passa no CHECK e já é painel-only em LLM/sidebar/unread). `state`/`contact_repo`/`conversation_repo`/`message_repo`/`ws_manager`/`emit_with_filter` já estavam no escopo da closure.
- **Problemas / pendências:** O guard `kind != "message" → return` do funil ([message_ingest_service.py:350](../app/services/message_ingest_service.py#L350)) foi mantido como rede de segurança (invariante, não removido).
- **Verificação:** `ast.parse` OK. Cobertura observável na F4 (teste que injeta `InboundEvent(kind="system")` no dispatch e valida os 3 casos de P2 + não-acionabilidade).

---

### F3 — Provider fino: `whatsapp_cloud` emite `kind="system"` 🟢 `[wave 2, dep: F1+F2]`

**Objetivo:** `type: system` da Cloud API deixa de virar `kind="message"`.

**Itens:**
1. [channels.py:1107](../assets/plugin_examples/whatsapp_cloud/channels.py#L1107) — separar `"system"` da tupla `("contacts","order","system")`; criar um ramo próprio com **early-return** `InboundEvent(kind="system", …)` (espelhar o `reaction` de [:1082](../assets/plugin_examples/whatsapp_cloud/channels.py#L1082)), carregando `media_extras={system_type, wa_id, body}` e `text=describe_message(msg)` (o `describe_system` de [inbound_text.py:265](../assets/plugin_examples/whatsapp_cloud/inbound_text.py#L265) já entrega o texto).
2. `contacts`/`order` **permanecem** `kind="message"` (são falas reais do cliente — não mexer).
3. Replicar a mudança em `storages/plugins/whatsapp_cloud/channels.py:1107` (dev-server) — a cópia rodando.
4. **Não** importar nada do core no plugin (mantém o import-defensivo — [tests/test_plano75_cloud_inbound_text.py:488](../tests/test_plano75_cloud_inbound_text.py#L488)).

**Pronto quando:** `_parse(cloud, SYSTEM_MSG)` retorna `ev.kind == "system"` com `media_extras["system_type"]` preenchido e texto legível; `contacts`/`order` seguem `kind="message"`.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-07-24)
- **O que foi feito:** [channels.py:1107](../assets/plugin_examples/whatsapp_cloud/channels.py#L1107) — `"system"` separado da tupla `("contacts","order","system")`. Novo ramo `elif msg_type == "system":` com **early-return** `InboundEvent(kind="system", direction="in", media_type="system", text=describe_message(msg), media_extras={system_type, wa_id, body}, ...)` espelhando o `reaction` ([:1082](../assets/plugin_examples/whatsapp_cloud/channels.py#L1082)); `chat_id=sender` (número antigo, `from`). `contacts`/`order` ficaram num `elif msg_type in ("contacts","order"):` próprio, **mantendo `kind="message"`** (falas reais). Cópia rodando replicada: `cp assets/... storages/plugins/whatsapp_cloud/channels.py` (paridade `diff` = idênticos).
- **Como foi feito / decisões:** (a) `text=describe_message(msg)` **sem** o fallback `_unsupported_text("system")` — se `describe_system` devolver "" (degenerado), o dispatch do core simplesmente não grava card (guard `card_text`), melhor que injetar um "⚠️ tipo não suportado". `describe_message` já roteia `system`→`describe_system` ([inbound_text.py:421](../assets/plugin_examples/whatsapp_cloud/inbound_text.py#L421)). (b) Nenhum import do core adicionado (mantém o import-defensivo do plugin). (c) `sender_name` fica vazio (o webhook de `system` não traz `contacts[]`).
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `pytest tests/test_plano75_parse_inbound.py` = 42 passed (com os asserts novos `ev.kind=="system"` + `media_extras`; regressão `contacts`/`order` `kind=="message"` também travada). `ast.parse` OK nas duas cópias.

---

### F4 — Testes: atualizar os que quebram + provar a não-acionabilidade 🟢 `[wave 2, dep: F2+F3]`

**Objetivo:** travar o novo contrato e o motivador (número morto não abre protocolo nem aciona IA → sem 131026).

**Itens** `[paralelo entre si]`:
1. **Atualizar** os asserts que mudam de propósito em [tests/test_plano75_parse_inbound.py](../tests/test_plano75_parse_inbound.py): `test_system_usa_o_body_pronto_da_meta` ([:204](../tests/test_plano75_parse_inbound.py#L204)) passa a esperar `ev.kind == "system"`; revisar `test_system_nao_traz_contacts` ([:210](../tests/test_plano75_parse_inbound.py#L210)) e `test_todos_os_tipos_do_apendice` ([:256](../tests/test_plano75_parse_inbound.py#L256)) para não exigir `kind="message"` no system. `test_plano75_cloud_inbound_text.py` **não muda** (o texto do formatter continua igual).
2. **Novo** teste de não-acionabilidade (espelhar [test_plano75_safety_net.py](../tests/test_plano75_safety_net.py) que dirige `deps.ingest_event`/dispatch direto): injeta `kind="system"` e assere — card `conversation_event` salvo; **nenhum** `message.received`/`message.saved`/`message.persisted` no `EventRecorder` ([tests/characterization/golden.py](../tests/characterization/golden.py)); agente **não** invocado; conversa **não** criada/reaberta; contato **não** materializado quando não existia.
3. **Novo** teste protocolos: passar um evento de sistema e assere que `logic.on_inbound` ([protocolos/logic.py:2599](../assets/plugin_examples/protocolos/logic.py#L2599)) **não** abre ciclo (na prática: o `channel.system_event` não é `message.saved`, então `EVENT_HANDLERS` nem chama).
4. (Opcional) Golden de webhook Cloud: postar o envelope §11.1 (`user_changed_number`) no webhook real do canal e travar "card painel-only, sem send, sem AI".

**Pronto quando:** `venv/bin/python -m pytest tests/ -q` verde no Postgres de teste; os 3 asserts antigos atualizados; a não-acionabilidade coberta.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-07-24)
- **O que foi feito:** (1) Asserts de parse atualizados em [test_plano75_parse_inbound.py](../tests/test_plano75_parse_inbound.py): `test_system_usa_o_body_pronto_da_meta` ganhou `ev.kind=="system"` + `media_extras[system_type/wa_id/body]`; `test_contacts…`/`test_order…` ganharam `ev.kind=="message"` (regressão). (2) **Novo arquivo** [tests/test_plano82_system_inbound.py](../tests/test_plano82_system_inbound.py) — 5 testes end-to-end via POST no webhook Cloud real (`/api/webhook/whatsapp_cloud/{id}`), modelados no `test_plano75_bus_events.py` (canal+contato+conversa semeados por repo; captura de bus por `EventRecorder`): conversa aberta (card + sem `message.saved`/`received` + sem `assistant` + `channel.system_event` com `system_type`/`wa_id`/`conversation_id` + segue aberta), conversa fechada (card + **não reabre**), contato sem conversa (nada gravado, `channel.system_event` com `conversation_id=None`), sem contato (não materializa fantasma), reentrega (dedup: 1 card, 1 evento).
- **Como foi feito / decisões:** A não-acionabilidade do `protocolos` é provada pelo **mecanismo** (F4 item 3, como o próprio plano sanciona): assere-se que **nenhum** `message.saved`/`message.received` sai no bus para o número — e o `protocolos` só escuta `message.saved` ([events.py:18](../assets/plugin_examples/protocolos/events.py#L18)), logo `on_inbound` nunca é chamado. Optei por não carregar o plugin `protocolos` no `build_app` (traz migrations/estado próprios → flaky) — a asserção de ausência do gatilho é robusta e determinística. O golden de webhook (item 4, opcional) ficou coberto pelos 5 e2e reais.
- **Problemas / pendências:** As falhas no run do diretório `tests/endpoints/` inteiro (`test_p27_gowa`, `test_p36_executions`, `test_sidebar_search`) são **pré-existentes** — reproduzidas com minhas mudanças **em stash** (12 falhas sem elas × 8 com; nondeterminismo de estado no DB compartilhado do processo). Passam **em isolamento** (28/28). Nenhum desses testes envia evento `system` ⇒ o ramo novo nem executa. O erro de coleção de `tests/test_endpoints.py` (`AttributeError` no plugin `protocolos`) também é pré-existente (arquivo alterado por trabalho não-commitado do plano 81; não toquei `protocolos`).
- **Verificação:** Conjunto relacionado **verde**: `pytest tests/test_plano75_parse_inbound.py tests/test_plano75_cloud_inbound_text.py tests/test_plano75_bus_events.py tests/test_plano75_safety_net.py tests/test_plano82_system_inbound.py tests/endpoints/test_p26_cloud_webhook.py tests/characterization/test_webhook_characterization.py` = **229 passed**. `tests/test_plano82_system_inbound.py` isolado = **5 passed**.

---

### F5 — Distribuição do plugin `whatsapp_cloud` 🔴 `[wave 3]`

**Objetivo:** publicar o provider novo **sem** quebrar produção pela ordem errada.

**Itens** `[sequencial]`:
1. Regenerar `assets/channel_plugins/whatsapp_cloud-plugin.zip` pelo snippet do [assets/channel_plugins/README.md](../assets/channel_plugins/README.md).
2. Bump da versão em `plugin.yaml` (e, se o novo kind exigir core novo, apertar `whatsbot_api_version`).
3. **Ordem obrigatória (R4):** primeiro **deploy do core** com F2 (o ramo de dispatch), depois **importar o zip** novo. Se o zip subir antes do core, o `kind="system"` cai no vazio do dispatch (sem `else`) e o card **some** — pior que o estado atual visível.
4. Importar o `.zip` na tela Plugins de produção (não há auto-import) e validar com um evento sintético / o próximo `type: system` real.

**Pronto quando:** o canal Atendimento está vivo com o plugin novo e um evento de sistema vira card sem acionar IA/protocolo.

#### Status de execução — Fase 5
**Estado:** 🟡 Artefato de código pronto; regeneração do zip + import em prod **deixados para o usuário** (deploy manual)
- **O que foi feito:** Bump de versão `whatsapp_cloud` **1.5.1 → 1.6.0** em `assets/plugin_examples/whatsapp_cloud/plugin.yaml` (+ cópia `storages/`) — marcador do plano 82. `whatsbot_api_version` **não** foi apertado: o novo `kind="system"` é contrato de runtime (o core precisa do ramo de dispatch da F2), não uma API Python que o plugin importe; a proteção é **procedural** (R4, ordem de deploy), não semver.
- **Como foi feito / decisões:** ⚠️ **NÃO regenerei `assets/channel_plugins/whatsapp_cloud-plugin.zip`** de propósito. A árvore de trabalho contém trabalho **não-commitado do plano 81** (RBAC de rotas) que também altera `whatsapp_cloud/routes.py` (adiciona `dependencies=[core_permission("channel.manage")]`) e depende de `plugins.context.core_permission` (função nova, igualmente não-commitada). Regenerar o zip agora **empacotaria o plano 81 junto** — e um core de produção sem `core_permission` faria o `routes.py` **falhar no import** (plugin com `load_error`). Isso está fora do escopo do plano 82. Quando o usuário for publicar, deve primeiro decidir/commitar o plano 81 (ou isolá-lo), garantir o **core no ar (F2) ANTES** e só então regenerar + importar.
- **Problemas / pendências (ação do usuário, em ordem — R4):**
  1. Deploy do **core** com a F2 (ramo `kind=="system"` no dispatch) **primeiro**. Sem ele, o `kind="system"` cai no vazio do dispatch (sem `else`) e o card **some**.
  2. Regenerar o zip pelo snippet do [assets/channel_plugins/README.md](../assets/channel_plugins/) (`venv/bin/python - <<'PY' … PY`, loop sobre `("telegram","whatsapp_cloud","website")`) — **conferindo** o que a árvore vai empacotar (plano 81 incluso ou não).
  3. Importar o `.zip` na tela Plugins de produção (não há auto-import) e validar com o próximo `type: system` real / evento sintético: card `ℹ️ …` no fio, **sem** IA, **sem** protocolo.
- **Verificação:** Bump aplicado e conferido nas duas cópias (`grep ^version` = 1.6.0). Regeneração do zip e import: **pendentes de ação manual do usuário** (deploy).

---

### F6 — (Opcional, NÃO nesta rodada) Limpeza do fantasma em produção 🟢 `[wave 3]`

> **P7 ✅ 2026-07-24: deixar como está** — o usuário decidiu **não** executar F6 agora. A fase fica documentada como opção futura; o resíduo (contato 14787 + PROT-…-15040) permanece.

**Objetivo:** remover o resíduo do incidente (contato-fantasma 14787 + protocolo espúrio PROT-…-15040).

**Itens** `[sequencial]`:
1. **Backup** antes (mesmo padrão da auditoria de protocolos — dump da região afetada).
2. Confirmar via `read` que 14787 (556881215248) não tem histórico legítimo além do card de sistema + a saudação falhada; resolver/fechar PROT-20260724-165831-15040 e (a critério) remover o contato-fantasma.
3. Registrar que **não há backfill** do card de sistema retroativo (é 1 evento; D1 mantém a duplicação).

**Pronto quando:** o protocolo espúrio está resolvido e o fantasma não polui mais a fila "Não atribuídas".

#### Status de execução — Fase 6
**Estado:** ⛔ Não será executada nesta rodada (decisão do usuário P7, 2026-07-24)
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

## 8. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| **R1 — kind desconhecido é descartado** | Um `kind="system"` sem ramo no dispatch some (não há `else` — [channel_webhook.py:543](../server/routes/channel_webhook.py#L543)). | F2 adiciona o ramo **antes** de qualquer provider emitir o kind (ordem das waves). |
| **R2 — `add_message` cria conversa** | Reusar `contact_obj.add_message` (como o `group_participants`) **cria** conversa aberta se não houver ([memory.py:451](../agent/memory.py#L451) → `_create_open_atomic`), violando D2. | O ramo usa `get_open_for_contact_scoped` (get-sem-criar) + `message_repo.add(conversation_id=…)` — **nunca** `add_message`/`ensure_conversation_live`. |
| **R3 — plugins de automação disparam** | Se o ramo emitir `message.saved`/`message.received`, protocolos abre ciclo ([protocolos/events.py:18](../storages/plugins/protocolos/events.py#L18)). | Emitir **só** `channel.system_event` (nome novo, não-acionável). Teste F4 item 3 trava isso. |
| **R4 — ordem de deploy** | Zip novo (provider emite `kind="system"`) importado **antes** do core com o ramo ⇒ card some. | F5: **core primeiro, zip depois**. Apertar `whatsbot_api_version` se necessário. |
| **R5 — 3 cópias do plugin divergem** | assets × storages × zip fora de sincronia (lição do `protocolos`/`telegram` — drift real). | F0 confirma paridade; F3 replica nas 3; F5 regenera o zip. |
| **R6 — role painel-only em 3-4 listas** | As listas de exclusão do LLM (2 cópias), sidebar e frontend já divergem (`private_note`). Reusar role novo exigiria mexer em todas. | **Reusar `conversation_event`** (já em todas) ⇒ nenhuma lista muda. |
| **R7 — card sem conversa** | Número que trocou nunca teve conversa (contato novo) ⇒ nada onde anexar. | **P2**: anexar à última conversa (aberta ou fechada, **sem reabrir**); se não houver, apenas logar (coerente com D2 "não criar conversa"). |
| **R8 — Postgres/idempotência** | Reentrega do webhook (a Meta reentrega de rotina) duplicaria o card. | Dedup por `(channel_id, external_msg_id)` já existe no funil; para o ramo system, considerar o mesmo `state.processed_messages` ou `msg_id` do evento como guard (**P3**). |

---

## 9. Perguntas em aberto

- **P1 — Role do card.** (a) **Reusar `conversation_event`** (recomendado — já painel-only em tudo, zero migration) · (b) reusar `system_notice` (é o que o `group_participants` inbound usa) · (c) role novo `system_inbound`. **✅ DECIDIDO (2026-07-24): (a)** — `conversation_event`, pelo reuso total das listas e por já ser o "aviso no fio" do plano 12.
- **P2 — Sem conversa existente.** **✅ DECIDIDO (2026-07-24):** conversa **aberta** → card aparece; conversa **existente porém fechada** → card é adicionado e a conversa **permanece fechada** (não reabre); **sem conversa** (só contato, ou nem contato) → **não cria conversa e não grava card** (só loga). Ou seja: resolve por `get_latest_for_contact` (aberta ou fechada), anexa via `message_repo.add(conversation_id=…)` **sem** `set_status`/reabertura; `None` ⇒ drop. Nunca materializa contato nem conversa a partir do evento.
- **P3 — Nome e dedup do evento de bus.** `channel.system_event` vs `message.system`. Dedup por `external_msg_id`? **Recomendação:** `channel.system_event` (deixa claro que **não** é `message.*` acionável) + guard por `external_msg_id` (R8). **⏸️ Confirmar** na F2.
- **P4 — Gate de config / silenciável?** O card de sistema deve poder ser desligado por um toggle (estilo `system_notice_*`)? **Recomendação:** **não** gatear nesta rodada (é informacional/crítico; gravar direto no dispatch com o texto do provider, sem passar pelo registry de notices). Se quiser toggle depois, registrar um grupo `canal` via `register_notice_group` ([system_notices.py:255](../server/system_notices.py#L255)). **⏸️ ADIADO.**
- **P5 — Telegram (`migrate_to_chat_id`, `my_chat_member`).** É o análogo direto (grupo→supergrupo troca o `chat_id`; bot bloqueado/removido). Hoje some silenciosamente ([telegram/channels.py:383](../assets/plugin_examples/telegram/channels.py#L383) → `kind="message"` vazio; [:381](../assets/plugin_examples/telegram/channels.py#L381) `return []`). **⏸️ ADIADO** — vira mudança fina no provider telegram depois que o core (F2) existir, **sem** tocar o core. Registrado como follow-up.
- **P6 — Migração de contato (número novo).** **✅ DECIDIDO (D1): fora de escopo.** O `system.wa_id`/BSUID `user_id` ancoraria um merge futuro; não neste plano.
- **P7 — Limpeza do fantasma 14787.** **✅ DECIDIDO (2026-07-24): deixar como está.** F6 **não** será executada nesta rodada — o resíduo (contato 14787 + PROT-…-15040) fica; o plano segue só com a correção de código. (F6 permanece documentada como opção futura.)

---

## 10. Checklist de verificação

- [ ] `venv/bin/python -m pytest tests/ -q` **verde no Postgres de teste** (`WHATSBOT_TEST_DB_URL`).
- [ ] Asserts de `test_plano75_parse_inbound.py` sobre `system` atualizados para `kind="system"`; `test_plano75_cloud_inbound_text.py` **inalterado** e verde.
- [ ] Novo teste prova: `kind="system"` → card `conversation_event`, **0** `message.saved`/`message.received`/`message.persisted`, agente **não** invocado, conversa **não** criada/reaberta.
- [ ] Novo teste prova: protocolos **não** abre ciclo para um evento de sistema.
- [ ] `contacts`/`order` seguem `kind="message"` (regressão) — falas reais do cliente intactas.
- [ ] Paridade das 3 cópias do `whatsapp_cloud` (assets × storages × zip) após F3/F5.
- [ ] Modo escuro: o card de sistema renderiza legível (herda o card `conversation_event` — nada novo, mas conferir).
- [ ] Reentrega do webhook não duplica o card (dedup — R8).
- [ ] Deploy na ordem **core → zip** (R4); canal Atendimento vivo pós-import.
- [ ] Nenhum segredo em URL/log (o `channel.system_event` carrega `raw` — cortar base64 grande antes de logar).

---

## 11. Apêndice — payloads oficiais da Meta

### 11.1 `system` (`user_changed_number` / `customer_identity_changed`) — [doc](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/system)
```json
{ "from": "556881215248", "id": "wamid.HBgMNTU2ODgxMjE1MjQ4…",
  "timestamp": "1784912305", "type": "system",
  "system": { "body": "User A changed from 556881215248 to 556992412393",
              "wa_id": "556992412393", "type": "user_changed_number" } }
```
⚠️ Na doc **atual**: `system.type ∈ {user_changed_number, customer_identity_changed}`; o número novo vem em **`wa_id`** (não `new_wa_id` — isso era On-Premises). **Não há** `contacts[]` neste webhook. `system.body` já vem pronto ⇒ o card usa `describe_system` (prefixo `ℹ️`). `from` é o número **antigo**.

### 11.2 Erro 131026 (o sintoma) — [doc](https://developers.facebook.com/documentation/business-messaging/whatsapp/support/error-codes)
> *Unable to deliver message. Reasons can include: the recipient phone number is not a WhatsApp number; the recipient hasn't accepted the new Terms of Service; an outdated WhatsApp version…* — bucket error, **não cobrado**. É o que a IA recebe ao responder o número morto. O plano elimina a causa (a IA nunca responde a um evento de sistema).

### 11.3 Assinatura de webhook do canal Atendimento (medido na Graph API, 24/07)
- `GET /{waba_id}/subscribed_apps` → app `API_OFICIAL_WHATS_REDES_BRASIL` (1066318035289433), `override_callback_uri` = `…/api/webhook/whatsapp_cloud/whatsapp_cloud_bc081279`.
- Campo assinado **efetivo** (empírico, 973 payloads no `debug_bus`): **apenas `messages`** — que empacota `messages[]` (text/reaction/image/document/**system**) **+** `statuses[]` (sent/delivered/read/**failed**) **+** `contacts[]`. O `system` chega pelo **mesmo** campo obrigatório; não dá para desinscrever dele.
- Número: `quality_rating: GREEN`, `status: CONNECTED`, BSUID `user_id` já ativo (`from_user_id`/`recipient_user_id` nos payloads) — relevante para a migração futura (D1/P6).

---

## 12. Apêndice — arquivos-chave

**Contrato:**
- [channels/events.py:18,42](../channels/events.py#L18) — `InboundEvent.kind`/`trigger_ai` (F1)

**Core (genérico):**
- [server/routes/channel_webhook.py:276,491,543](../server/routes/channel_webhook.py#L276) — `_dispatch_events`, precedente `group_participants`, boundary de inserção (F2)
- [app/services/message_ingest_service.py:350,481,519](../app/services/message_ingest_service.py#L350) — guard de kind, criação de conversa, `message.received` (invariante)
- [db/repositories/conversation_repo.py:304](../db/repositories/conversation_repo.py#L304) — `get_open_for_contact_scoped` (get-sem-criar)
- [db/repositories/message_repo.py:15,184,216](../db/repositories/message_repo.py#L15) — `add(...)`, listas de exclusão do LLM
- [db/repositories/_mapping.py:103](../db/repositories/_mapping.py#L103) — `LIST_PANEL_ONLY_ROLES`
- [db/repositories/unread_repo.py:41](../db/repositories/unread_repo.py#L41) — unread opt-in por `msg_id`
- [server/system_notices.py:406,464](../server/system_notices.py#L406) — `emit_for_contact` (referência de padrão de sink)

**Provider (fino):**
- [assets/plugin_examples/whatsapp_cloud/channels.py:1082,1107,1137](../assets/plugin_examples/whatsapp_cloud/channels.py#L1107) — ramo `reaction` (modelo), ramo `system` (alvo), retorno (F3)
- [assets/plugin_examples/whatsapp_cloud/inbound_text.py:265](../assets/plugin_examples/whatsapp_cloud/inbound_text.py#L265) — `describe_system` (texto do card, já pronto)
- `storages/plugins/whatsapp_cloud/channels.py:1107` — cópia rodando (replicar)
- [assets/channel_plugins/README.md](../assets/channel_plugins/) — snippet de regeneração do zip (F5)

**Testes:**
- [tests/test_plano75_parse_inbound.py:204,210,256](../tests/test_plano75_parse_inbound.py#L204) — asserts a atualizar
- [tests/test_plano75_cloud_inbound_text.py:208](../tests/test_plano75_cloud_inbound_text.py#L208) — formatter (inalterado)
- [tests/test_plano75_safety_net.py:155](../tests/test_plano75_safety_net.py#L155) — padrão de teste que dirige o ingest/dispatch
- [tests/characterization/test_webhook_characterization.py:164](../tests/characterization/test_webhook_characterization.py#L164) + [golden.py](../tests/characterization/golden.py) — `EventRecorder` para a não-acionabilidade
- [tests/manual_plano75_inject.py:108](../tests/manual_plano75_inject.py#L108) — payload `user_changed_number` real

**Automação (o que NÃO pode disparar):**
- [assets/plugin_examples/protocolos/events.py:18](../assets/plugin_examples/protocolos/events.py#L18) + [logic.py:2599](../assets/plugin_examples/protocolos/logic.py#L2599) — `message.saved` → `on_inbound`
