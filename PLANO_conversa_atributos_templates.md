# Plano — Informações da Conversa, Atributos por Escopo e Templates Cloud API

> Gerado em 2026-06-20. Três frentes independentes que podem ser entregues em ondas separadas.
> Decisões já confirmadas com o Thiago:
> - **Tags**: etiquetas **próprias da conversa** (novas, separadas das tags do contato — estilo Chatwoot labels).
> - **Layout**: **dois painéis separados** (botão "Informações da conversa" no topo; foto/nome abre "Informações do contato").
> - **Templates**: suporte **completo** (variáveis de texto no corpo + mídia no cabeçalho + botões).
> - **Acionar template**: **ícone no compositor** (barra de digitação), só em canais Cloud API.

---

## 0. Resumo executivo (o que já existe vs. o que falta)

A boa notícia: **a maior parte do backend já está pronta**. O esforço é principalmente de fiação (wiring) + frontend + uma feature nova (etiquetas de conversa).

| Frente | Já existe | Falta |
|---|---|---|
| **A. Atributos por escopo** | Tabela `custom_attribute_definitions` com coluna `applies_to` (`contact`/`conversation`), storage em `contacts.custom_attributes` e `conversations.custom_attributes`, repo, validação, endpoints `PUT /info` de ambos | Expor o seletor de escopo na tela admin; listar os dois escopos |
| **A. Dois painéis** | Painel único `ContactInfoPanel.js` (mistura contato + conversa) | Quebrar em **dois** componentes/drawers + botão no header |
| **B. Etiquetas de conversa** | Padrão de tags do contato (`tags` + `contact_tags`), dimensão de filtro `labels` (hoje aponta p/ tags do contato), grupo de notices `tags` | **Tudo novo**: tabela, repo, endpoints, UI, filtro, notices |
| **C. Templates Cloud API** | `WhatsAppCloudChannel.send_template()` (com `components`), `OutboundRouter.send_template()`, capability `templates=True`, `session_window_hours=24`, credencial `waba_id` já declarada | Listar templates da Graph API (hoje é stub `[]`); endpoint core p/ listar+enviar; UI do compositor (picker + preenchimento de variáveis/mídia/botões) |

Arquivos-âncora:
- Atributos: [db/tables.py](db/tables.py) (`custom_attribute_definitions`), [db/repositories/custom_attribute_repo.py](db/repositories/custom_attribute_repo.py), [server/routes/custom_attributes.py](server/routes/custom_attributes.py), [web/static/js/components/CustomAttributesManager.js](web/static/js/components/CustomAttributesManager.js)
- Painel: [web/static/js/components/contacts/ContactInfoPanel.js](web/static/js/components/contacts/ContactInfoPanel.js), [web/static/js/components/contacts/ConversationHeaderActions.js](web/static/js/components/contacts/ConversationHeaderActions.js), [web/static/js/components/contacts/ContactDetail.js](web/static/js/components/contacts/ContactDetail.js)
- Canais/Templates: [channels/base.py](channels/base.py), [channels/outbound.py](channels/outbound.py), [assets/plugin_examples/whatsapp_cloud/channels.py](assets/plugin_examples/whatsapp_cloud/channels.py), [assets/plugin_examples/whatsapp_cloud/routes.py](assets/plugin_examples/whatsapp_cloud/routes.py)

---

## Frente A — Separar "Informações do contato" × "Informações da conversa"

### A.1 Objetivo
1. Atributos personalizados passam a ter **escolha de escopo** na criação (contato ou conversa) — backend já suporta, falta só a UI admin.
2. O painel lateral atual (que mistura tudo) vira **dois painéis distintos**:
   - **Informações do contato** (abre ao clicar na foto/nome): foto, nome, email, profissão, empresa, endereço, **tags do contato**, **atributos do contato**, observações.
   - **Informações da conversa** (botão novo no topo — a caixa vermelha do print): status, agente atribuído (humano/IA), **etiquetas da conversa** (Frente B), **atributos da conversa**, metadados (canal, nº da conversa `#display_id`, datas).

### A.2 Estado atual (importante)
- `custom_attribute_definitions.applies_to` já existe com unique `(attribute_key, applies_to)` — o **mesmo backend** serve os dois escopos. Seed já cria atributos de conversa (`prioridade`, `canal_origem`) em [seed_demo.py](seed_demo.py).
- A tela admin [CustomAttributesManager.js](web/static/js/components/CustomAttributesManager.js):
  - linha 168: só carrega `getCustomAttributes('contact')` → **não mostra** atributos de conversa.
  - linha 77: hardcoda `applies_to: 'contact'` no create → **não dá pra escolher escopo**.
- O painel [ContactInfoPanel.js](web/static/js/components/contacts/ContactInfoPanel.js) já tem, no mesmo drawer: "Ações da conversa" (AssigneePicker, linha 238), "Dados desta conversa" (atributos de conversa, linha 412) e os dados do contato. Ou seja, a lógica de dados já está toda lá — é **reorganização**, não reescrita.

### A.3 Backend
Quase nada a fazer. Itens menores:
- `PUT /api/conversations/{conv_id}/info` ([server/routes/conversations.py](server/routes/conversations.py)) hoje **não revalida valores** de atributos de conversa (só checa que a key existe). Igualar ao contato: chamar `custom_attribute_validate.validate_value` por key (paridade com [server/routes/contacts.py](server/routes/contacts.py) `PUT /info`). _Nice-to-have, não bloqueante._
- Garantir que `GET /api/conversations/{conv_id}` retorne tudo que o painel de conversa precisa: `status`, `assignee_user_id`/label, `active_agent_key`, `ai_active`, `custom_attributes`, `display_id`, `channel_id`/provider, datas. Conferir `conversation_repo.get_with_channel`.

### A.4 Tela admin de atributos (escolha de escopo)
Em [CustomAttributesManager.js](web/static/js/components/CustomAttributesManager.js):
- Adicionar `<select>` **"Aplica-se a"** (`contact` | `conversation`) no `AttributeForm`, **somente no create** (imutável na edição, igual `type`/`attribute_key`). Incluir no payload em vez do `applies_to: 'contact'` fixo (linha 77).
- `load()`: buscar **os dois escopos** (`getCustomAttributes('contact')` + `getCustomAttributes('conversation')`) e exibir numa lista única com um badge "Contato"/"Conversa", ou duas seções. Recomendado: duas seções com cabeçalho.
- Texto do topo (linha 205) deixa de dizer só "do contato".
- `notifyChanged()` já dispara `whatsbot:custom-attributes-changed`; ambos os painéis (contato e conversa) recarregam definições ao ouvir esse evento (já implementado nos `useEffect`).

### A.5 Frontend — quebrar em dois painéis
Estratégia: **extrair** do atual `ContactInfoPanel.js` em dois componentes, reaproveitando o que já existe.

1. **`ContactInfoPanel.js`** (refatorado → só contato):
   - Mantém: avatar, nome/email/profissão/empresa/endereço, **tags do contato**, **atributos do contato** (`applies_to=contact`), observações.
   - **Remove** daqui: "Ações da conversa" (AssigneePicker) e "Dados desta conversa" → vão pro novo painel.
   - Header do drawer: "Dados do contato" (mantém).

2. **`ConversationInfoPanel.js`** (novo):
   - Header: "Informações da conversa" + `#display_id`.
   - Seções:
     - **Status** (Aberta/Fechada) — reusar a ação de `ConversationHeaderActions` (resolver/reabrir).
     - **Atribuição** — `AssigneePicker` (humano + IA), já existe ([AssigneePicker.js](web/static/js/components/contacts/AssigneePicker.js)).
     - **Etiquetas da conversa** (Frente B) — componente espelhado do editor de tags.
     - **Atributos da conversa** (`applies_to=conversation`) — reusar `CustomAttributeField`.
     - **Metadados** (read-only): canal/provider, criada em, última atividade, resolvida em.
   - Salvar: `PUT /api/conversations/{conv_id}/info` (atributos) + chamadas de etiqueta/atribuição/status já têm seus próprios endpoints.

3. **Botões/gatilhos** (dois painéis separados, conforme escolhido):
   - **Header da conversa** ([ConversationHeaderActions.js](web/static/js/components/contacts/ConversationHeaderActions.js) ou [ContactDetail.js](web/static/js/components/contacts/ContactDetail.js) por volta da linha 868): adicionar um botão/ícone **"Informações da conversa"** (ícone ℹ️/painel) onde está a caixa vermelha do print → abre `ConversationInfoPanel`.
   - **Foto/nome no topo do chat**: clique abre `ContactInfoPanel` (provavelmente já é o gatilho atual; manter).
   - Gerenciar a abertura/fechamento dos dois drawers em [Contacts.js](web/static/js/components/contacts/Contacts.js) (estado tipo `openPanel: 'contact' | 'conversation' | null`, abrir um fecha o outro).

4. **Modo escuro**: usar classes `wa-*` e `.wa-field` (regra do projeto). O painel atual já segue isso — copiar o padrão.

### A.6 Testes (A)
- `tests/test_endpoints.py`: criar definição com `applies_to=conversation`; `PUT /conversations/{id}/info` com valor válido/ inválido; garantir que valor de conversa não vaza pro contato e vice-versa.

---

## Frente B — Etiquetas (tags) por conversa

> Decisão: etiquetas **próprias da conversa**, separadas das tags de contato.

### B.1 Modelo de dados (novo)
Espelhar o par `tags` + `contact_tags`, mas isolado da conversa. Em [db/tables.py](db/tables.py):

```python
conversation_labels = Table(           # registro global de etiquetas de conversa
    "conversation_labels", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False, unique=True),
    Column("color", Text, nullable=False, server_default="#6b7280"),
    Column("position", Integer, server_default="0"),
    Column("created_at", Float),
)

conversation_label_links = Table(      # N:N conversa ↔ etiqueta
    "conversation_label_links", metadata,
    Column("conversation_id", Integer, ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True),
    Column("label_id", Integer, ForeignKey("conversation_labels.id", ondelete="CASCADE"), primary_key=True),
)
```

> **Alternativa mais barata** (se quiser menos código no futuro): reaproveitar a tabela global `tags` como vocabulário compartilhado e criar só `conversation_tags(conversation_id, tag_id)`. Aplicação fica separada, mas o nome/cor é o mesmo das tags de contato. **Não recomendado aqui** porque o pedido foi "separados das tags do contato" — fico com o registro dedicado acima.

- Migration Alembic nova (seguir numeração linear vigente).

### B.2 Repositório
Novo `db/repositories/conversation_label_repo.py` espelhando [tag_repo.py](db/repositories/tag_repo.py):
`get_all()`, `create(name, color)`, `update(id, ...)`, `delete(id)`, `get_for_conversation(conv_id)`, `set_for_conversation(conv_id, names|ids)`.

### B.3 Endpoints
Em [server/routes/conversations.py](server/routes/conversations.py) (ou um `conversation_labels.py` novo):
- `GET /api/conversation-labels` — lista o registro global.
- `POST /api/conversation-labels` — cria `{name, color}`.
- `PUT /api/conversation-labels/{id}` / `DELETE` — editar/remover.
- `PUT /api/conversations/{conv_id}/labels` — define as etiquetas da conversa `{labels: [...]}` (snapshot).
- Broadcast WS `conversation_labels_changed` + evento de plugin (paridade com `contact.tagged`).

### B.4 Filtro da sidebar (plano 08)
Hoje a dimensão `labels` ([db/filters/registry.py](db/filters/registry.py) + [db/filters/translate.py](db/filters/translate.py) `_labels_clause`) filtra por **tags do contato**. Adicionar dimensão nova:
- `conv_labels` em `DIMENSIONS` (kind `labels`, op `in`, label "Etiquetas da conversa").
- `_conv_labels_clause` em `translate.py`: subselect em `conversation_label_links` + `conversation_labels` por nome.
- Expor em `GET /api/conversations/filter-schema`.

### B.5 Avisos de sistema (plano 12)
Em [server/system_notices.py](server/system_notices.py): novo grupo (ex.: `system_notice_conv_labels`) **ou** reusar o grupo `tags` existente. Recomendado: grupo próprio para não confundir com tags de contato. Adicionar `FORMATTERS` p/ `conv_label_added`/`conv_label_removed` + `EVENT_GROUP_OF` + toggle no [ConfigPanel.js](web/static/js/components/ConfigPanel.js) (seção "Avisos de sistema no chat") + chave em `DEFAULT_CONFIG`/`allowed_keys`/`GET config`.

### B.6 Frontend
- Editor de etiquetas no novo `ConversationInfoPanel` (espelhar o editor de tags do contato: chips removíveis, busca, criar nova, paleta de cor).
- Chips de etiqueta na linha da conversa em [ContactList.js](web/static/js/components/contacts/ContactList.js) (opcional; cuidar pra não confundir visualmente com tags do contato — usar ícone/estilo distinto).
- Filtro na sidebar (dropdown de etiquetas da conversa).
- `services/api.js`: `getConversationLabels`, `createConversationLabel`, `updateConversationLabel`, `deleteConversationLabel`, `updateConversationLabels(convId, labels)`.

### B.7 Testes (B)
- CRUD do registro de etiquetas; set/get por conversa; filtro `conv_labels:in`; notice ligado/desligado; cascade ao apagar conversa.

---

## Frente C — Enviar Templates do WhatsApp (Cloud API)

> Decisão: suporte **completo** (corpo com variáveis + mídia no header + botões), ícone no **compositor**, só em canais com capability `templates`.

### C.1 Estado atual
- `WhatsAppCloudChannel.send_template(chat_id, template_name, lang, components)` já envia ([channels.py:240](assets/plugin_examples/whatsapp_cloud/channels.py#L240)).
- `OutboundRouter.send_template(...)` já roteia por `channel_id` ([outbound.py:88](channels/outbound.py#L88)), com erros `templates_not_supported` / `channel_not_registered`.
- Capability `templates=True` e `session_window_hours=24` declaradas; `OutboundRouter.session_open()` já decide se texto livre é permitido (fora da janela ⇒ exige template).
- Credencial `waba_id` já está na lista de `credential_keys` do provider ([routes.py:37](assets/plugin_examples/whatsapp_cloud/routes.py#L37)).
- **Falta**: listar templates (hoje `GET /api/plugins/whatsapp_cloud/templates` é stub `[]` — [routes.py:49](assets/plugin_examples/whatsapp_cloud/routes.py#L49)); endpoint core de listar+enviar; UI.

### C.2 Listar templates da Graph API
Adicionar método **`list_templates()`** ao contrato `Channel` ([channels/base.py](channels/base.py)) — opcional, default `raise NotImplementedError` (como `send_template`), e implementar em `WhatsAppCloudChannel`:

```python
def list_templates(self) -> list[dict]:
    """GET /{waba_id}/message_templates?fields=name,language,status,category,components&limit=...
    Retorna só status == APPROVED. Cacheável (ex.: 5–10 min)."""
```

- Lê `waba_id` + `access_token` das credenciais do canal (mesmo padrão de `_phone_number_id`).
- Normaliza cada template para a UI: `{name, language, category, status, components:[{type, format, text, example, buttons}]}`.
- Tratar paginação (`paging.next`) e cache curto (evitar bater na Meta a cada abertura do picker).
- Atualizar o stub `GET /api/plugins/whatsapp_cloud/templates` para chamar `inst.list_templates()` **ou** (preferível) expor no core (ver C.3).

### C.3 Endpoints core (channel-aware)
Como o picker fica no **compositor do core**, expor no core via `OutboundRouter`/registry (não depender do router do plugin):
- `GET /api/conversations/{conv_id}/templates` — resolve o `channel_id` da conversa (`conversation_repo.get_with_channel`), checa `outbound.supports(channel_id, "templates")`; se não suporta, retorna `[]`/flag. Senão, `registry.get(channel_id).list_templates()`.
- `POST /api/conversations/{conv_id}/send-template` — body `{template_name, language, components}` (components = parâmetros preenchidos). Monta o payload Graph e chama `outbound.send_template(...)`. Persiste a mensagem enviada (role `assistant`/operator, `source` adequado), faz broadcast `new_message` e emite `message.sent` (igual aos outros sends operator em [server/routes/contacts.py](server/routes/contacts.py)).
- Adicionar `OutboundRouter.list_templates(channel_id)` (capability-gated, espelhando `send_template`).

> Onde mora o código de Cloud API: o canal continua sendo **plugin** (`whatsapp_cloud`); o core só fala com ele via `registry`/`outbound` (capability-gated). Isso respeita a regra "core não cresce com opção de plugin" — o picker é uma **ação de operador** genérica gated por capability, não config de plugin.

### C.4 Montagem de `components` (suporte completo)
A Graph API espera `components` no envio espelhando a definição do template:
- **header**: `{type:"header", parameters:[{type:"image"|"document"|"video", image:{link:...}}]}` (mídia por URL pública — usar `statics/` servido, ou link da própria Meta).
- **body**: `{type:"body", parameters:[{type:"text", text:"valor da {{1}}"}, ...]}` na ordem dos `{{n}}`.
- **button**: para botões dinâmicos (url/quick_reply com variável) `{type:"button", sub_type:"url"|"quick_reply", index:"0", parameters:[...]}`.
- A UI lê a **definição** (`components` retornados em C.2), descobre quantas variáveis o corpo tem (`{{1}}..{{n}}`), se o header é de mídia, e quais botões aceitam parâmetro → gera os campos.

### C.5 Frontend — picker no compositor
Em [ContactDetail.js](web/static/js/components/contacts/ContactDetail.js) (compositor, perto de `AttachIcon`/`EmojiIcon` ~linha 1338–1351):
- Novo **ícone "Template"** na barra de digitação. Renderizar **somente** quando o canal da conversa suporta templates (flag vinda de `GET /conversations/{id}` ou do schema do canal) — fora disso, esconder.
- Ao clicar: abrir modal **`TemplatePicker.js`** (novo):
  1. Lista templates aprovados (`GET /conversations/{id}/templates`), com busca por nome/categoria.
  2. Ao escolher um: renderiza um **formulário dinâmico** — campos de texto para cada `{{n}}` do corpo, upload/URL p/ mídia do header, valores p/ botões dinâmicos; e um **preview** do texto final.
  3. "Enviar" → `POST /conversations/{id}/send-template` com `components` montados.
- **UX da janela de 24h**: quando `session_open=false` (fora da janela), exibir aviso no compositor de que só templates podem ser enviados, destacando o ícone de template. (Opcional, mas é o caso de uso real do recurso.)
- Modo escuro: `wa-*` + `.wa-field`.

### C.6 Credenciais / config
- Garantir que o canal Cloud API tenha `waba_id` preenchido (tela de config do plugin). Sem `waba_id`, `list_templates` retorna vazio com mensagem clara ("Configure o WABA ID do canal").

### C.7 Testes (C)
- Mock da Graph API: `list_templates` parseando `message_templates` (paginação, filtro APPROVED).
- `send-template`: monta `components` corretamente p/ body+header+button; persiste msg + broadcast; erro quando canal não suporta (`templates_not_supported`) e quando `session_open` exige template.

---

## Ordem de implementação sugerida (ondas)

1. **Onda 1 — Atributos por escopo (A.4)** — menor esforço, alto valor, backend pronto. Só UI admin (seletor `applies_to` + listar os dois escopos).
2. **Onda 2 — Dois painéis (A.5)** — refator de `ContactInfoPanel` → `ContactInfoPanel` + `ConversationInfoPanel` + botão no header. Sem backend novo.
3. **Onda 3 — Etiquetas de conversa (Frente B)** — tabela + repo + endpoints + filtro + notices + UI (encaixa no `ConversationInfoPanel` da Onda 2).
4. **Onda 4 — Templates Cloud API (Frente C)** — `list_templates` no canal + endpoints core + `TemplatePicker` no compositor.

Ondas 1–2 destravam o "separar os dois" pedido; 3 completa a paridade Chatwoot; 4 é a feature de Cloud API. Cada onda é entregável sozinha.

---

## Riscos e gotchas

- **Atributos JSON**: sempre reatribuir o dict inteiro no UPDATE (JSON/JSONB não rastreiam mutação in-place) — já é o padrão do repo, manter.
- **Identidade imutável**: `attribute_key`, `type` e `applies_to` não mudam após criar (a UI já trava `key`/`type`; travar `applies_to` também).
- **Filtro `labels` ambíguo**: hoje `labels` = tags do **contato**. Não sobrescrever; criar `conv_labels` separado para não quebrar filtros salvos.
- **Templates exigem `waba_id`**: `send_template` usa `phone_number_id`, mas **listar** usa `waba_id` — credencial diferente, fácil de esquecer.
- **Mídia de header em template**: a Meta exige URL pública acessível (ou media handle). Se usar arquivo local, servir via `statics/` com URL absoluta alcançável pela Meta.
- **Janela de 24h**: o auto-reply da IA está sempre dentro da janela (responde a um inbound recém-chegado); o gating de template vale para sends **proativos/operador**. Não bloquear o fluxo agêntico por engano.
- **Core × plugin**: manter o canal Cloud API como plugin; o core só conversa via `registry`/`outbound` (capability-gated). Não criar `if provider == "whatsapp_cloud"` no pipeline.
- **Dois drawers**: ao abrir um painel, fechar o outro (evitar dois drawers sobrepostos). Centralizar o estado em [Contacts.js](web/static/js/components/contacts/Contacts.js).
- **Testes**: o suite [tests/test_endpoints.py](tests/test_endpoints.py) mocka GOWA e LLM; mockar também a Graph API para os testes de template.
