# Plano de Implementação — 12: Avisos de Sistema no Chat (eventos do ciclo de vida da conversa)

> Plano para **registrar no fio da conversa**, como uma "mensagem de sistema" (igual ao card que aparece
> quando a IA executa uma tool), os eventos do ciclo de vida do atendimento: atribuição a um operador,
> assumir para mim, adicionar/remover tag, IA ligada/desligada, IA assumiu o atendimento, atributo
> personalizado definido, agente ativo trocado, conversa aberta/fechada/reaberta/arquivada — **inclusive
> as transições automáticas** (cliente reabre conversa fechada ao mandar mensagem, IA assume sozinha).
>
> O usuário escolhe **por tipo de evento** quais avisos aparecem, num ajuste **global no servidor** (na
> tela Configurações): quando um grupo está desligado, o aviso **não é gerado** (sem gravação, sem
> broadcast) para ninguém. Arquitetura **extensível** — adicionar um tipo novo no futuro é registrar um
> formatter + uma chave de config + um toggle na UI.
>
> **Escopo:** (1) mecanismo central de emissão de aviso de sistema (registry + helper + gate de config);
> (2) hooks nas rotas de conversa/tags/contato; (3) hooks nas transições automáticas; (4) renderização no
> chat; (5) seção de toggles em Configurações; (6) testes.
>
> **Fora de escopo:** auditoria persistente (já coberta pelo plano [07-auditoria](07-plano-auditoria.md),
> que escreve em `audit_log` a partir do mesmo bus — este plano é **visual no chat**, não substitui o
> trilho de auditoria); notificações push/browser/som (preferências per-device do plugin `notifications`);
> avisos por **usuário** ou por **conversa** (decisão foi config **global** — ver §2 D2); envio desses
> avisos ao WhatsApp (são **painel-only**, nunca saem pelo GOWA, igual a `tool_call`/`system_notice` hoje).

---

## 0. Estado atual VERIFICADO (2026-06-20, working tree pós-`ee2d963`)

> Tudo abaixo foi confirmado por leitura com âncora `arquivo:linha`. **Na implementação, re-ancore por
> `grep` (nome de função/rota/role), nunca por número fixo.**

### O que JÁ existe (não reconstruir)

- **Infra de "mensagem de sistema no chat" já existe e é o padrão a seguir.** Há roles especiais que
  renderizam como **card centralizado** (não bolha de conversa) e são **painel-only**:
  - `tool_call` — "Ferramenta IA" (card laranja), criado em `_broadcast_tool_calls`
    ([`server/routes/webhook.py:628-650`](../server/routes/webhook.py#L628-L650)): faz
    `contact.add_message("tool_call", content)` + `ws_manager.broadcast("new_message", {...})`. **É
    exatamente o "igual a mensagem de quando a IA executa uma tool" que o pedido cita.**
  - `system_notice` — "Mensagem do Sistema" (card azul), já usado para alertas da IA/transfer/sandbox
    (`server/routes/webhook.py:920,937,1046,1079,1418`, `server/routes/sandbox.py:74`).
  - `transcription`, `private_note`, `error` — outros cards centralizados.
- **Renderização desses roles** em [`web/static/js/components/contacts/ContactDetail.js:876-996`](../web/static/js/components/contacts/ContactDetail.js#L876)
  (`isToolCall` 962-978; `isSystemNotice` 944-960). Cada um é um `<div class="flex justify-center">` com
  ícone + label + conteúdo + hora.
- **Esses roles já são excluídos** de:
  - Contexto do LLM — [`db/repositories/message_repo.py:67`](../db/repositories/message_repo.py#L67)
    (`excluded = ("transcription", "tool_call", "system_notice")`).
  - Preview/contagem da sidebar — [`web/static/js/components/contacts/Contacts.js:688-689`](../web/static/js/components/contacts/Contacts.js#L688).
  - Contagem de não-lidas / "última mensagem" — [`db/repositories/contact_repo.py:345`](../db/repositories/contact_repo.py#L345)
    e [`:383`](../db/repositories/contact_repo.py#L383).
- **Todas as ações de ciclo de vida já existem como endpoints e já emitem WS + bus** (mas **não** criam
  mensagem no chat) — [`server/routes/conversations.py`](../server/routes/conversations.py):
  - `_broadcast(deps, ws_event, bus_event, conv, **extra)` ([`:27-51`](../server/routes/conversations.py#L27))
    é o **choke point único** de toda mutação de conversa via rota. Payload já tem `conversation_id`,
    `display_id`, `status`, `assignee_user_id`, `ai_active`, `is_archived`, `inbox_id`. **NÃO tem** `phone`
    nem o **autor** (`current_user`).
  - `POST /status` ([`:121`](../server/routes/conversations.py#L121)) → `conversation_status_changed` / `conversation.status_changed`.
  - `POST /assign` ([`:135`](../server/routes/conversations.py#L135)) e `POST /assign-me` ([`:147`](../server/routes/conversations.py#L147), carrega `by_user_id`) → `conversation_assigned` / `conversation.assigned`.
  - `POST /ai` ([`:175`](../server/routes/conversations.py#L175)) → `conversation_ai_toggled` / `conversation.ai_toggled`.
  - `POST /agent` ([`:162`](../server/routes/conversations.py#L162)) e `PUT /info` ([`:199`](../server/routes/conversations.py#L199)) → `conversation_updated` / `conversation.updated`.
  - `POST /archive` ([`:187`](../server/routes/conversations.py#L187)) → `conversation_archived` / `conversation.archived`.
- **Tags são por contato** (não por conversa, decisão do plano 01) — `PUT /api/contacts/{phone}/tags`
  ([`server/routes/tags.py:87-132`](../server/routes/tags.py#L87)): calcula `added`/`removed`, emite
  `contact_tags_updated` (WS) + `contact.tagged` e um `contact.untagged` por tag removida. **Tem `phone`
  e tem o autor** (request).
- **IA por contato** (gate nível 2) — `POST /api/contacts/{phone}/toggle-ai`
  ([`server/routes/contacts.py:932-950`](../server/routes/contacts.py#L932)) → `contact_ai_toggled` /
  `contact.ai_toggled`. (É distinto do `ai_active` por conversa — gate nível 3.)
- **Transições automáticas existentes:**
  - **Auto-reabertura:** mensagem inbound do cliente reabre conversa `closed` dentro de
    `ContactMemory.add_message` → `conversation_repo.resolve_for_contact(..., reopen_if_closed=(role=="user"))`
    ([`agent/memory.py:170-195`](../agent/memory.py#L170), [`conversation_repo.py:133-134`](../db/repositories/conversation_repo.py#L133)).
    **Hoje não há nenhum sinal** de que reabriu — `resolve_for_contact` retorna a conversa sem flag de
    "was_reopened".
  - **Criação de conversa:** `conversation_repo.create` ([`:37`](../db/repositories/conversation_repo.py#L37)), `ai_active=1` por default.
- **Padrões de config booleana (global):** `DEFAULT_CONFIG` em
  [`config/settings.py:51-119`](../config/settings.py#L51) (ex.: `low_balance_enabled`,
  `image_transcription_enabled`, `split_messages`); leitura/escrita JSON-encoded via
  [`db/repositories/config_repo.py:15-50`](../db/repositories/config_repo.py#L15);
  `GET /api/config` ([`server/routes/config.py:35-66`](../server/routes/config.py#L35)) devolve as chaves;
  `PUT /api/config` ([`:68-130`](../server/routes/config.py#L68)) salva pelas chaves do **allowlist**
  (`allowed_keys`, `:70-84`) e emite `config.changed`.
- **Toggles na UI:** padrão de checkbox em
  [`web/static/js/components/ConfigPanel.js`](../web/static/js/components/ConfigPanel.js) (ex.:
  `lowBalanceEnabled` `:557-583`, `imageTranscriptionEnabled` `:321-345`); estado local em `useState`,
  populado de `config` num `useEffect`, e `handleSave()` monta o objeto e chama `onSave(data)`.
- **Broadcast fora de rota:** `from plugins.context import broadcast` — thread-safe, fire-and-forget,
  disponível em qualquer contexto (CLAUDE.md "Broadcast WebSocket"). É o caminho para emitir `new_message`
  de dentro do webhook/repo sem precisar do `deps.ws_manager`.

### O GAP (o que falta)

1. **🔴 Nenhum evento de ciclo de vida vira mensagem no chat.** As rotas emitem WS/bus para atualizar a
   **lista** de conversas, mas o **fio** da conversa não ganha nada.
2. **🟠 Falta contexto nos payloads de conversa.** `_broadcast` não carrega `phone` (necessário para o
   `new_message`, que é keyed por phone, e para resolver o `contact_id`) nem o **autor** da ação
   (para escrever "Fulano atribuiu…").
3. **🟠 Auto-reabertura é silenciosa.** `resolve_for_contact` não sinaliza que reabriu uma conversa
   `closed`, então não dá para emitir o aviso "Conversa reaberta automaticamente".
4. **🟠 "IA assumiu o atendimento" não é um evento.** Não há ponto único marcando a 1ª resposta da IA numa
   conversa; hoje só existe o toggle de `ai_active`.
5. **🟢 Não há controle de exibição.** Falta a seção de toggles em Configurações + as chaves de config.

---

## 1. Arquitetura alvo

Um **módulo central** (core, não plugin) concentra: o **registry** de tipos de evento, o **gate** de
config por grupo, a **formatação** PT-BR (com autor) e a **emissão** (gravar `messages` + broadcast
`new_message`). Os call sites só chamam um helper de alto nível e passam o contexto que já têm.

```
          AÇÃO (rota OU automática)
                  │
                  ▼
   server/system_notices.py  ──────────────┐
   ┌─────────────────────────────────────┐ │
   │ EVENT_GROUPS = registry              │ │  1. gate: grupo habilitado? (config global)
   │   group -> {config_key, default,     │ │     settings.get("system_notice_<group>", True)
   │             label, ...}              │ │     → off ⇒ no-op total (nada grava/emite)
   │ FORMATTERS = {event_type: fn(ctx)}   │ │  2. content = FORMATTERS[event_type](ctx)  (PT-BR + autor)
   │                                      │ │  3. message_repo.add(contact_id, ROLE, content,
   │ emit_conversation_notice(...)        │─┼─►    conversation_id=conv_id)   (painel-only)
   └─────────────────────────────────────┘ │  4. broadcast("new_message", {phone, message:{role,content,ts}})
                  │                          │
                  ▼                          │
        messages (role=conversation_event) ─┘  ──►  ContactDetail.js renderiza card centralizado
        ws "new_message"                              (igual a tool_call/system_notice)
```

Princípios:

- **Painel-only.** O aviso é um `message` com role especial; **nunca** é enviado ao WhatsApp (não passa
  por `gowa_client.send_message`). Mesmo contrato dos `tool_call`/`system_notice` atuais.
- **Gate na geração, não na exibição** (decisão do usuário: config **global** controla geração). Grupo
  desligado ⇒ o helper retorna sem gravar nem emitir. Sem lixo no banco quando desligado.
- **Registry extensível.** Adicionar um tipo futuro = registrar `event_type → (group, formatter)` +
  (se for grupo novo) uma chave de config + um toggle. Zero `if/elif` espalhado.
- **Idempotência de contexto.** Onde já existe `phone` + autor (rotas de tag/contato), passa direto. Onde
  só existe `conversation_id` (rotas de conversa), o helper resolve `phone`/`contact_id` via
  `conversation_repo.get` + `contact_repo`.

---

## 2. Decisões (defaults propostos — confirmar o que divergir)

| # | Questão | Default proposto | Impacto |
|---|---|---|---|
| **D1** | Role da mensagem: reusar `system_notice` ou criar role dedicado? | **Role novo `conversation_event`** ("Evento da conversa"), com ícone por categoria. | Distingue evento de ciclo de vida dos avisos da IA; exige tocar 4 sites de exclusão/render (§3 Fase 4). Alternativa leve: reusar `system_notice` (0 mudanças de render/exclusão, mas mistura com alertas da IA). |
| **D2** | Granularidade dos toggles | **Por grupo** (4 chaves), conforme escolha "por tipo": `system_notice_assignment`, `system_notice_tags`, `system_notice_status`, `system_notice_ai`. Todas default `True`. | Sem interruptor mestre (usuário escolheu "por tipo", não "mestre + por tipo"). Grupos mapeiam aos 4 escolhidos no Q3. |
| **D3** | Atribuição de autor | **Sim, nomear o autor** quando houver (`current_user` → nome). Automático ⇒ "automaticamente"/"cliente"/"IA". | Exige enriquecer `_broadcast`/helper com autor. Mensagens ficam "Fulano fez X" / "Você assumiu…". |
| **D4** | Linkar ao `conversation_id` correto | **Sim, explícito.** O helper grava com o `conversation_id` da ação (não via `resolve_for_contact`), porque eventos como "fechada" deixam a conversa `closed` e `get_open_for_contact` não a acharia. | `add_message` hoje resolve sozinho e **não aceita** `conversation_id`; o helper chama `message_repo.add` direto (que já aceita `conversation_id`). |
| **D5** | last_activity / ordem | Aviso **não** bumpa `last_activity_at` da conversa (não é atividade do cliente), mas entra no fio com `ts=now` (aparece na posição cronológica). | Helper não chama `touch_activity`. |
| **D6** | "IA assumiu o atendimento" automático | Emitir **uma vez por conversa** na 1ª resposta da IA (hook no path de resposta do webhook), dedupe por conversa. Toggle = grupo `ai`. | Ver Fase 3.3; marcar como sub-item confirmável (pode ficar só com on/off manual se preferir minimizar). |
| **D7** | Grupos do WhatsApp | Mesmo mecanismo (keyed por phone/jid). Avisos aparecem no fio do grupo, painel-only. | Sem tratamento especial. |

---

## 3. Fases

### Fase 0 — Mecanismo central (registry + helper + config)
> Objetivo: ter `emit_conversation_notice(...)` funcionando isolado, com gate de config, antes de plugar nos call sites.

- **0.1** Criar [`server/system_notices.py`](../server/system_notices.py) com:
  - `EVENT_GROUPS`: dict `group -> {config_key, default, label_pt}` para os 4 grupos (D2).
  - `_group_enabled(group) -> bool`: lê `settings.get(config_key, default)`.
  - `FORMATTERS`: dict `event_type -> fn(**ctx) -> str` (todas as variações da §8, com autor/automático).
  - `EVENT_GROUP_OF`: dict `event_type -> group`.
  - `emit_conversation_notice(*, event_type, contact_id, phone, conversation_id, **ctx)`:
    1. `group = EVENT_GROUP_OF[event_type]`; se `not _group_enabled(group)`: `return` (no-op).
    2. `content = FORMATTERS[event_type](**ctx)`; se vazio: `return`.
    3. `msg = message_repo.add(contact_id, ROLE, content, conversation_id=conversation_id)` (D4).
    4. `broadcast("new_message", {"phone": phone, "message": {"role": ROLE, "content": content, "ts": msg["ts"], "conversation_id": conversation_id}})`.
    - Defensivo: qualquer exceção é logada e engolida (um aviso que falha **nunca** quebra a ação principal).
  - `ROLE = "conversation_event"` (D1).
  - Helper de resolução `_resolve_target(conversation_id) -> (contact_id, phone)` via `conversation_repo.get` + `contact_repo` (para call sites que só têm `conversation_id`).
- **0.2** Adicionar as 4 chaves a `DEFAULT_CONFIG` em [`config/settings.py`](../config/settings.py) (todas `True`).
- **0.3** Expor as 4 chaves no `GET /api/config` e adicioná-las ao `allowed_keys` do `PUT /api/config`
  em [`server/routes/config.py`](../server/routes/config.py).

**Pronto:** chamar `emit_conversation_notice(event_type="status_closed", ...)` num teste cria 1 `message`
role `conversation_event` e dispara `new_message`; com o grupo `status` desligado, não cria nada.

### Fase 1 — Hooks nas rotas de conversa
> Objetivo: cobrir status (abrir/fechar), assign, assign-me, ai (conversa), agent, archive, atributos.

- **1.1** Enriquecer `_broadcast` (ou um wrapper ao lado dele) em
  [`server/routes/conversations.py:27`](../server/routes/conversations.py#L27) para resolver `phone` e
  receber o **autor** (`current_user(request)`), e chamar `emit_conversation_notice` mapeando
  `ws_event → event_type`. **Centraliza** todos os eventos de conversa num só ponto.
  - Mapeamento: `conversation_status_changed` → `status_open`/`status_closed` (pelo `conv["status"]`);
    `conversation_assigned` → `assigned`/`assigned_me`/`unassigned`; `conversation_ai_toggled` →
    `ai_on`/`ai_off`; `conversation_archived` → `archived`/`unarchived`; `conversation_updated` →
    `agent_changed` (quando muda `active_agent_key`) e/ou `attribute_set` (quando muda `custom_attributes`).
  - Para `attribute_set`: usar o diff entre `custom_attributes` anterior e novo (a rota `PUT /info` já lê o
    `conv` anterior — [`:206-219`](../server/routes/conversations.py#L206)) para nomear chave/valor.
- **1.2** Passar `current_user(request)` para o ponto de emissão em cada handler (o `_broadcast` é chamado
  de dentro dos handlers, que têm `request`). Para `assign-me`, autor = ator e alvo = ele mesmo ⇒
  "Você assumiu a conversa." (event_type `assigned_me`).
- **1.3** Resolver nome do **operador alvo** da atribuição (`assignee_user_id` → nome) via `user_repo`
  para "Fulano atribuiu a conversa para **Beltrano**".

**Pronto:** fechar/reabrir/atribuir/assumir/ligar-desligar IA/arquivar/trocar agente/definir atributo numa
conversa cria o card correspondente no fio, com autor, respeitando o toggle do grupo.

### Fase 2 — Hooks de tags e IA por contato
> Objetivo: cobrir o grupo Tags e o liga/desliga de IA por contato (gate nível 2).

- **2.1** Em `PUT /api/contacts/{phone}/tags` ([`server/routes/tags.py:87`](../server/routes/tags.py#L87)):
  já calcula `added`/`removed` e tem `phone` + autor. Emitir `tag_added` por tag adicionada e
  `tag_removed` por tag removida (grupo `tags`). Resolver `contact_id`/`conversation_id` da conversa
  aberta do contato (`conversation_repo.get_open_for_contact`).
- **2.2** Em `POST /api/contacts/{phone}/toggle-ai`
  ([`server/routes/contacts.py:932`](../server/routes/contacts.py#L932)): emitir `ai_on`/`ai_off`
  (grupo `ai`) com autor.

**Pronto:** adicionar/remover tag e ligar/desligar IA por contato geram cards, respeitando o toggle.

### Fase 3 — Transições automáticas
> Objetivo: cobrir o que acontece **sem** um operador clicar (escolha "incluir automáticos").

- **3.1** **Auto-reabertura.** Fazer `conversation_repo.resolve_for_contact` sinalizar reabertura — opções
  (escolher uma): (a) retornar `(conv, was_reopened: bool)`; (b) novo `resolve_for_contact_ex` que devolve
  o flag; (c) o webhook compara o `status` da conversa **antes** de processar o batch. Preferência:
  **(a)** com ajuste dos call sites (`agent/memory.py:179`). No site do webhook que processa inbound
  (onde há `phone` e `ws_manager`/`broadcast`), emitir `status_reopened_auto` (grupo `status`, autor =
  "cliente"/automático).
- **3.2** **Criação de conversa** (opcional, default ligado no grupo `status`): emitir `created` quando
  `conversation_repo.create` roda para um inbound novo. Como `create` é repo-layer (sem broadcast), emitir
  no mesmo ponto do webhook que detecta conversa nova. (Pode ficar de fora se poluir — confirmar.)
- **3.3** **"IA assumiu o atendimento"** (D6): no path onde a IA envia a resposta no webhook
  (perto de `_broadcast_tool_calls` / envio da reply), emitir `ai_takeover` **uma vez por conversa**
  (dedupe: checar se já existe um `conversation_event` de `ai_takeover` na conversa, ou marcar em memória/
  coluna). Grupo `ai`.

**Pronto:** cliente mandando msg numa conversa fechada gera "Conversa reaberta automaticamente"; a 1ª
resposta da IA gera "IA assumiu o atendimento" (uma vez), ambos respeitando o toggle.

### Fase 4 — Renderização no chat (frontend)
> Objetivo: o role `conversation_event` aparece como card centralizado e não polui sidebar/LLM/contadores.

- **4.1** Adicionar bloco de render `isConversationEvent` em
  [`ContactDetail.js`](../web/static/js/components/contacts/ContactDetail.js#L876) (clonar o de
  `system_notice`, ícone por categoria a partir de um prefixo/emoji no `content`, ou estilo único). Card
  centralizado, cores via `wa-*`/inline legíveis no dark (testar modo escuro — regra do CLAUDE.md).
- **4.2** Adicionar `conversation_event` à lista de skip de preview/contagem em
  [`Contacts.js:689`](../web/static/js/components/contacts/Contacts.js#L689).
- **4.3** Adicionar `conversation_event` às exclusões de **backend**:
  - LLM context — [`message_repo.py:67`](../db/repositories/message_repo.py#L67).
  - "última mensagem"/não-lidas — [`contact_repo.py:345`](../db/repositories/contact_repo.py#L345) e
    [`:383`](../db/repositories/contact_repo.py#L383).
- **4.4** Confirmar que o handler de `new_message` no frontend
  ([`Contacts.js:615-734`](../web/static/js/components/contacts/Contacts.js#L615)) anexa o card ao fio
  aberto (dedupe por `ts+role`/`content+role`) — deve funcionar sem mudança além do 4.2.

> **Se D1 = reusar `system_notice`:** Fase 4 inteira cai (já renderiza e já está excluído) — só restaria
> escolher se quer ícone distinto. Esse é o caminho de menor risco.

### Fase 5 — Configurações (UI) + testes + docs
> Objetivo: o usuário liga/desliga por grupo; cobertura de teste; CLAUDE.md atualizado.

- **5.1** Nova seção "Avisos de sistema no chat" em
  [`ConfigPanel.js`](../web/static/js/components/ConfigPanel.js) com 4 toggles (padrão `lowBalanceEnabled`):
  `useState` por chave, popular no `useEffect`, incluir no objeto do `handleSave`. Textos PT-BR + descrição
  curta de cada grupo (ex.: "Atribuição, transferência e 'assumir para mim'").
- **5.2** Testes em [`tests/test_endpoints.py`](../tests/test_endpoints.py): para cada grupo, (a) executar a
  ação (close/assign/tag/ai) e checar que um `message` role `conversation_event` foi criado na conversa;
  (b) desligar a chave de config e checar que **nenhum** é criado; (c) checar que `conversation_event` não
  aparece na contagem de não-lidas nem no contexto do LLM.
- **5.3** Atualizar `CLAUDE.md`: novo role `conversation_event` na tabela de roles/mensagens; nova seção
  curta "Avisos de sistema no chat"; novas chaves de config; (se virar evento de bus) registrar.

**Pronto:** toggles funcionam ponta-a-ponta, testes verdes, docs refletem o recurso.

---

## 4. Artefatos por categoria

- **Módulos novos:** `server/system_notices.py` (registry + formatters + helper).
- **Migrations:** **nenhuma** — `role` é coluna `Text` livre; `conversation_event` não exige schema.
  (Exceção: só se D6 optar por uma coluna de dedupe para `ai_takeover` — preferir dedupe por query, sem migration.)
- **Mudanças core (backend):**
  - `config/settings.py` (+4 chaves em `DEFAULT_CONFIG`).
  - `server/routes/config.py` (GET expõe; PUT allowlist).
  - `server/routes/conversations.py` (`_broadcast` enriquecido com phone+autor+emit).
  - `server/routes/tags.py`, `server/routes/contacts.py` (toggle-ai) — emits.
  - `server/routes/webhook.py` (auto-reabertura, created, ai_takeover).
  - `db/repositories/conversation_repo.py` (`resolve_for_contact` sinaliza reabertura) + call site
    `agent/memory.py`.
  - `db/repositories/message_repo.py`, `db/repositories/contact_repo.py` (+`conversation_event` nas exclusões).
- **Frontend:** `ContactDetail.js` (render), `Contacts.js` (skip preview), `ConfigPanel.js` (seção de toggles).
- **Testes:** `tests/test_endpoints.py`.
- **Deps novas:** nenhuma.

---

## 5. Dependências e sequência

- **Depende de:** modelo de conversa/inbox (plano 01 — pronto), endpoints de conversa (plano 01 Fase 1 —
  prontos), RBAC/`current_user` (plano 03 — `current_user`/`user_repo` disponíveis para nome do autor).
- **Complementa:** plano [07-auditoria](07-plano-auditoria.md) — mesmos eventos de bus, mas auditoria
  persiste em `audit_log` (trilho permanente, mascarado) enquanto este plano é a camada **visual no chat**.
  Não duplicar: o aviso é UX; o audit é registro.
- **Ordem interna:** Fase 0 → 1 → 2 → 4 (render/UI mínima para validar) → 3 (automáticos) → 5 (config UI + testes + docs).
  (Pode-se fazer 5.1 junto da 0 para testar os toggles cedo.)

---

## 6. Riscos / cuidados

- **Loop/ruído de eventos.** Não emitir aviso a partir de handler que reaja a `message.sent`/`new_message`
  (evitar realimentar). O helper grava `message` mas com role `conversation_event` (excluído de tudo) — não
  reentra no pipeline da IA.
- **Conversa fechada e o card.** O aviso de "fechada" precisa do `conversation_id` **explícito** (D4):
  depois do close, `get_open_for_contact` retorna `None`. Sempre passar `conv["id"]` da ação.
- **Excluir de TODOS os lugares.** Se esquecer uma das exclusões (LLM/sidebar/não-lidas), o card vaza para
  o contexto do agente ou infla contadores. Checklist na Fase 4.3/4.2.
- **Spam em ações em lote.** Ex.: salvar 5 atributos de uma vez ⇒ 5 cards. Mitigar agrupando por requisição
  (1 card "3 atributos atualizados") OU aceitar N cards. Decidir na 1.1 (default: 1 card por mudança, mas
  agregação simples para atributos).
- **Dedupe de `ai_takeover`** (D6): sem dedupe, todo turno da IA viraria card. Garantir 1×/conversa.
- **Performance do gate.** `settings.get` lê o `config` (sem cache, write-through). Eventos de ciclo de vida
  são baixa frequência ⇒ ok; se virar gargalo, cachear as 4 chaves no boot/`config.changed`.
- **Modo escuro.** Card novo deve passar no teste de contraste no tema dark (regra do CLAUDE.md).
- **Migração SQLite→Postgres / multi-réplica.** `broadcast` é por-processo; em múltiplas réplicas, só os
  clientes conectados àquela réplica recebem o `new_message` ao vivo — mas o `message` persistido aparece no
  próximo load para todos. Consistente com o comportamento atual de `tool_call`/`system_notice`.

---

## 7. Critério de pronto (do plano todo)

- [ ] Atribuir / assumir / fechar / reabrir / arquivar / ligar-desligar IA / trocar agente / definir
      atributo / adicionar-remover tag gera um card no fio da conversa, com autor quando houver.
- [ ] Transições automáticas (auto-reabertura por mensagem do cliente; "IA assumiu") geram card.
- [ ] Cada grupo (Atribuição, Tags, Status & arquivo, IA & atributos) tem um toggle em Configurações;
      desligar o grupo **impede a geração** do aviso (nada no banco, nada ao vivo).
- [ ] O role `conversation_event` **não** entra no contexto do LLM, **não** conta como não-lida e **não**
      vira preview na sidebar.
- [ ] Cards legíveis em modo claro e escuro.
- [ ] Testes cobrindo geração ligada/desligada e exclusões; suíte verde.
- [ ] CLAUDE.md atualizado (role novo + chaves de config + seção).

---

## 8. Apêndice — Catálogo de eventos e textos (PT-BR)

> `{ator}` = nome do operador (ou "Você" quando é o próprio); automáticos usam "cliente"/"a IA"/"automaticamente".
> Emojis seguem o estilo dos cards atuais (🔧 da tool). Ajustar wording final na implementação.

| Grupo (config) | `event_type` | Gatilho | Texto exemplo |
|---|---|---|---|
| **assignment** (`system_notice_assignment`) | `assigned` | `POST /assign` | 🧑‍💼 {ator} atribuiu a conversa para {alvo}. |
| | `assigned_me` | `POST /assign-me` | 🧑‍💼 {ator} assumiu a conversa. |
| | `unassigned` | `POST /assign` com `null` | 🧑‍💼 {ator} removeu a atribuição da conversa. |
| **tags** (`system_notice_tags`) | `tag_added` | `PUT /tags` (added) | 🏷️ {ator} adicionou a tag "{tag}". |
| | `tag_removed` | `PUT /tags` (removed) | 🏷️ {ator} removeu a tag "{tag}". |
| **status** (`system_notice_status`) | `status_closed` | `POST /status` closed | ✅ {ator} resolveu a conversa. |
| | `status_open` (reabrir manual) | `POST /status` open | 🔄 {ator} reabriu a conversa. |
| | `status_reopened_auto` | inbound em conversa closed | 🔄 Conversa reaberta automaticamente (cliente enviou mensagem). |
| | `archived` / `unarchived` | `POST /archive` | 🗄️ {ator} arquivou a conversa. / 🗄️ {ator} desarquivou a conversa. |
| | `created` (opcional) | nova conversa | 💬 Conversa #{display_id} iniciada. |
| **ai** (`system_notice_ai`) | `ai_on` / `ai_off` | `POST /ai` ou `/toggle-ai` | 🤖 {ator} reativou a IA. / 🤖 {ator} pausou a IA. |
| | `ai_takeover` | 1ª resposta da IA (1×/conversa) | 🤖 A IA assumiu o atendimento. |
| | `agent_changed` | `POST /agent` | 🤖 {ator} mudou o agente ativo para "{agente}". |
| | `attribute_set` | `PUT /info` (diff) | 📋 {ator} definiu "{atributo}" como "{valor}". |

> **Extensão futura** ("podem ter mais"): novo evento = nova entrada em `FORMATTERS` + `EVENT_GROUP_OF`;
> grupo novo = + chave em `DEFAULT_CONFIG`/`allowed_keys`/`GET config` + toggle no `ConfigPanel`.
