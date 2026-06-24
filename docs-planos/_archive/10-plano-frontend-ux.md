# 10 — Plano consolidado de Frontend / UX (WhatsBot Pro)

> **Status:** PLANO CONSOLIDADO de frontend. Une os fragmentos de UI espalhados pelos planos
> [`01`](01-plano-inbox-e-conversas.md) (conversas/abas/header), [`02`](02-plano-canais-e-providers.md)
> (Canais), [`03`](03-plano-rbac-usuarios.md) (login multi-user + admin), [`04`](04-plano-respostas-rapidas.md)
> (respostas rápidas no composer), [`05`](05-plano-atributos-personalizados.md) (atributos no painel de
> info) e [`08`](08-plano-filtros.md) (filtros na lista) num único roteiro de frontend.
>
> **Sincronizado com [`DECISOES.md`](DECISOES.md) (Lote 2, 2026-06-19)** e com o
> [`00-plano-mestre.md`](00-plano-mestre.md) (ondas). As 74 decisões funcionais estão fechadas; este
> documento **não re-litiga** nenhuma — só descreve **como o frontend evolui** para entregá-las.
>
> **Restrição:** este documento é roteiro. **Nenhum código de produção foi alterado** para escrevê-lo.
>
> **Princípio fundamental do projeto (CLAUDE.md):** Preact + HTM + Tailwind, **sem build step**, ES
> modules, libs vendorizadas. **O Pro EVOLUI os componentes existentes — não reescreve do zero.** Toda
> tela nova é legível no **modo escuro** (classes `wa-*` e `.wa-field`, testado com `.dark` ligado).

---

## 1. Visão geral e princípios de UX

### 1.1 O que muda na cabeça do usuário

Hoje o painel é um clone do **WhatsApp Web**: lista de contatos à esquerda, chat no centro, painel de
info à direita, com uma única caixa de entrada e uma única IA. O Pro reposiciona o produto como um
**sistema de atendimento multi-usuário, multi-canal e multi-agente** — a IA passa a ser *um dos
atendentes*. A metáfora mental migra de **"contato = thread infinita"** para **"conversa = unidade de
trabalho com ciclo de vida (aberta/resolvida), dono (assignee) e canal de origem"**.

O modelo de referência de UX é o **Chatwoot** (rail de inboxes, abas de status, fila de
não-atribuídas, painel lateral com status/assignee/atributos). **Não copiamos o Chatwoot ao pé da
letra** — preservamos a estética WhatsApp-Web que os usuários já conhecem (bolhas, doodle, headers
teal, badges de não-lidas) e *acrescentamos* as camadas de atendimento por cima.

### 1.2 Princípios

1. **Evoluir, não reescrever.** `Contacts.js`, `ContactList.js`, `ContactDetail.js`,
   `ContactInfoPanel.js`, `app.js`, `LoginScreen.js` continuam sendo a base. O trabalho é estendê-los
   (props novas, seções novas, estados novos), não jogá-los fora. O único rename estrutural sugerido é
   `Contacts.js → Conversations.js` (plano 01 é o dono), mantendo a árvore de subcomponentes.
2. **A conversa é a unidade.** Onde hoje a UI fala "contato" (`phone`), o Pro fala "conversa"
   (`conversation_id` + `display_id`). O `phone` continua existindo (chave do contato e do GOWA), mas
   a seleção, a URL e os eventos WS passam a girar em torno de `conversation_id`.
3. **Esconder o que não pode, não desabilitar (P48).** Permissões controlam *visibilidade*: um
   atendente simplesmente não vê o menu de Usuários, Canais, Custos, etc. — não vê botões cinzas.
4. **Tempo real sempre.** Toda mudança de estado (atribuição, resolução, reabertura, nova conversa na
   fila) chega por WebSocket e re-renderiza sem reload, como já acontece com mensagens.
5. **Modo escuro é requisito, não enfeite.** Cada tela/card/modal novo nasce legível no tema escuro.
6. **Boa experiência e funcional.** Densidade de informação alta (é uma ferramenta de trabalho), mas
   sem poluir: chips removíveis para filtros, dropdowns rápidos para ações frequentes, drawer para o
   avançado.

### 1.3 Estado atual mapeado (fonte das decisões deste plano)

- **Orquestração e rotas:** [`app.js`](../web/static/js/app.js) — `CORE_ROUTES`/`CORE_TAB_PATHS`
  (linhas 39-56), `tabFromPath`/`pathForTab` (62-78), `GearMenu` (105-219), merge de telas de plugin
  via `/api/plugins/manifest` filtrando `!s.config` (280-295), roteamento por `pushState` +
  `popstate` (297-316), `<main>` com switch gigante de `tab` (492-553).
- **3 painéis:** [`Contacts.js`](../web/static/js/components/contacts/Contacts.js) — layout flex
  `sidebar(400px) | botão-toggle | chat | info-panel(overlay)` (linhas 746-872); orquestra `selected`
  (phone), `contactData`, `showArchived` (28), `selectionMode`/`selectedPhones` (bulk, 32-33), e
  **todos os ~15 handlers de eventos WS** (428-734).
- **Lista (esquerda):** [`ContactList.js`](../web/static/js/components/contacts/ContactList.js) —
  header teal com toggle archive + toggle IA global (223-278), header de seleção (107-222), busca
  (280-292), linhas de contato com avatar/nome/tags/preview/badges (323-428). Já tem `is_group` →
  `GroupAvatar` (344-347) e badge `IA/IA OFF` (363-366).
- **Chat + composer (centro):** [`ContactDetail.js`](../web/static/js/components/contacts/ContactDetail.js)
  — header (756-783), área de mensagens com todos os tipos de bolha (786-…), e o **autocomplete de
  @menção** (estado `mentionMenu` linha 53; `getMentionCandidates` 191-200; `updateMentionMenu`
  detecta `@token` 203-209; `applyMention` 212-230; navegação por teclado em `handleKeyDown`
  385-405; dropdown renderizado ~linha 1265). **Este é o mecanismo que o `/` de respostas rápidas
  clona.** O `<textarea>` está na linha ~1290 (`inputRef`, classe `bg-wa-inputBg`).
- **Painel de info (direita):** [`ContactInfoPanel.js`](../web/static/js/components/contacts/ContactInfoPanel.js)
  — slide-in overlay (134-…), array fixo de `fields` name/email/profession/company/address
  (124-130), editor de tags inline (178-302), observações (304-341), botão Salvar (345-354).
- **Login:** [`LoginScreen.js`](../web/static/js/components/LoginScreen.js) — hoje campo único de
  senha → `login(password)` → guarda `whatsbot_token` no localStorage (21-24).
- **Tags:** [`TagPicker.js`](../web/static/js/components/contacts/TagPicker.js) (reusado no bulk e no
  context menu), [`Header.js`], [`PluginScreen.js`] (`import()` dinâmico + `apiBase`).

---

## 2. Layout-alvo

A grande mudança visual é a **introdução de um rail de ícones de inboxes na extrema esquerda** (estilo
Chatwoot), empurrando o layout atual de 3 colunas para 4 zonas.

### 2.1 Mockup — atendente (membro de 2 inboxes)

```
┌────┬──────────────────────────┬───────────────────────────┬──────────────────────┐
│RAIL│  LISTA DE CONVERSAS       │  CONVERSA (chat)          │  PAINEL DE INFO      │
│    │                          │                           │  (overlay/coluna)    │
│ ▣  │ ┌──────────────────────┐ │ ┌───────────────────────┐ │ ┌──────────────────┐ │
│Tdas│ │🔎 Pesquisar…         │ │ │ 👤 João  [grupo] [WA] │ │ │  [avatar grande] │ │
│    │ ├──────────────────────┤ │ │ #1042 · WhatsApp Vendas│ │ │  João Silva      │ │
│ 🟢 │ │ Abertas Minhas Não-at│ │ ├───────────────────────┤ │ │  +55 11 9xxxx    │ │
│Vend│ │ Resolvidas    [🗄]    │ │ │ [Atribuir a mim]      │ │ ├──────────────────┤ │
│    │ ├──────────────────────┤ │ │ [Transferir ▾][Resolv]│ │ │ Status: Aberta ▾ │ │
│ 🔵 │ │[Status:Aberto ✕][eu ✕]│ │ │ [Arquivar] [IA ●on]   │ │ │ Atendente: eu  ▾ │ │
│Supt│ ├──────────────────────┤ │ ├───────────────────────┤ │ │ Inbox: Vendas    │ │
│    │ │●João  Aberta  12:04 ▸│ │ │ ░░ doodle / bolhas ░░ │ │ ├──────────────────┤ │
│    │ │ 🤖 atendendo…  (2)    │ │ │                       │ │ │ Etiquetas: [lead]│ │
│    │ │──────────────────────│ │ │  «mensagens»          │ │ │ + atributos      │ │
│    │ │○Maria Resolv. ontem ▸│ │ │                       │ │ │   plano: premium │ │
│    │ │ você: ok                │ │ ├───────────────────────┤ │ │   (contato)      │ │
│    │ │──────────────────────│ │ │ [+] [😀] Digite…  [/] │ │ ├──────────────────┤ │
│    │ │○Grupo X [grupo] 09:10│ │ │              [↑ enviar]│ │ │ + atributos      │ │
│    │ └──────────────────────┘ │ └───────────────────────┘ │ │   da CONVERSA    │ │
│ ⚙  │                          │                           │ │ Observações…     │ │
└────┴──────────────────────────┴───────────────────────────┴──────────────────────┘
  ↑ rail (56px)   ↑ lista (400px)         ↑ chat (flex)            ↑ info (400px, overlay)
```

- **Rail (≈56px):** ícone por inbox/canal em que o usuário é membro + um ícone **"Todas"** no topo.
  Cada ícone mostra um **badge de não-lidas** da inbox. A engrenagem (`⚙`, GearMenu) ancora no pé do
  rail. O rail substitui a posição do botão de archive/IA-global que hoje vive no header teal — esses
  controles migram (ver §3).
- **Lista:** ganha **abas de status** (Abertas / Minhas / Não atribuídas / Resolvidas) logo abaixo da
  busca, uma **barra de chips de filtro** (plano 08) e o toggle de **Arquivadas** (🗄, dimensão
  independente — P10/P81). Cada linha ganha **badge de status**, **badge de grupo** (P8) e **avatar do
  assignee** quando atribuída.
- **Chat:** o header ganha a **barra de ações da conversa** (Atribuir/Transferir/Resolver/Reabrir/
  Arquivar/IA) e mostra `display_id` + nome do canal. Composer ganha o gatilho `/` (respostas rápidas).
- **Info:** ganha **seletor de status**, **atribuir agente**, **atributos de contato E de conversa**,
  e o **toggle de IA da conversa** (P5, cascata global→inbox→conversa, sem nível de contato).

### 2.2 Mockup — admin (vê tudo)

```
┌────┬──────────────────────────┬─────────────── …
│RAIL│  (idêntico, porém:)      │
│ ▣  │  - rail lista TODAS as   │   Admin enxerga todas as inboxes no rail; nas abas vê
│Tdas│    inboxes, não só as     │   "Não atribuídas" globais; no GearMenu vê itens extras:
│ 🟢 │    suas                   │     • Usuários (users.manage)
│ 🔵 │  - aba "Não atribuídas"  │     • Canais (channel.manage)
│ 🟣 │    mostra fila global     │     • Respostas rápidas (quickreply.manage)
│ ⚙  │                          │     • Atributos personalizados (admin)
└────┴── …                      │     • Custos / Plugins / Tools / Configurações
```

A diferença atendente↔admin é **inteiramente dirigida por `currentUser.permissions[]`** (plano 03):
o rail filtra inboxes por membership; o GearMenu filtra itens por permissão; as ações do header da
conversa aparecem só com a permissão correspondente.

### 2.3 Responsivo

O layout atual já colapsa para mobile (`hidden lg:flex`, sidebar full-width quando nada selecionado —
`Contacts.js:749,794`). Regras adicionais:

- **Rail** vira uma **linha horizontal de chips no topo** (ou um drawer) em telas `< lg`.
- **Painel de info** continua overlay full-screen em mobile (como hoje).
- **Abas de status** podem virar um `<select>` em telas estreitas se não couberem.

---

## 3. Mapa componente-a-componente

Legenda: **[EDITA]** = evolui arquivo existente · **[NOVO]** = arquivo novo.

### 3.1 Shell e roteamento

| Componente | Caminho | O que muda |
|---|---|---|
| **`app.js`** [EDITA] | `web/static/js/app.js` | (1) Buscar `getMe()` no boot → estado `currentUser` `{id,name,email,roles[],permissions[]}` e passá-lo via contexto/props. (2) `GearMenu` filtra itens por permissão (além do `!s.config` que já existe). (3) Registrar **rotas SPA novas**: `/conversations/:id` (substitui `/contacts/:id`, mantendo compat), `/channels`, `/usuarios`, `/quick-replies`, `/atributos`, `/filtros-salvos`. (4) Novos handlers WS de conversa (ver §5) e roteamento de `new_message` por `conversation_id`. (5) Montar o `InboxRail` ao lado do `<main>`. |
| **`GearMenu`** (em `app.js`) [EDITA] | idem | Cada `MenuItem` recebe um gate `perm`; renderiza só se `currentUser.permissions.includes(perm)`. Itens: Usuários→`users.manage`, Canais→`channel.manage`, Respostas rápidas→`quickreply.manage`, Atributos→admin, Custos→`billing.manage`, Configurações→`settings.manage`, Plugins→`plugins.manage`. Sair → `logout()` real (plano 03). |
| **`AuthGate`** (em `app.js`) [EDITA] | idem | `checkAuth()` → trata `needs_bootstrap`: se não há usuários, renderiza o fluxo "criar primeiro admin" (reusa o padrão do `/wizard`). |

### 3.2 Rail de inboxes (novo)

| Componente | Caminho | Papel |
|---|---|---|
| **`InboxRail.js`** [NOVO] | `web/static/js/components/InboxRail.js` | Coluna fina (~56px). Props: `inboxes` (lista filtrada por membership), `activeInboxId` (`null` = "Todas"), `onSelect(inboxId|null)`, `unreadByInbox` (map). Renderiza ícone "Todas" + um ícone por inbox + badge de não-lidas por ícone. Âncora o `GearMenu` no rodapé. Usa `bg-wa-panel`/`text-wa-text`/`bg-wa-teal` para o ativo. Em `< lg`, vira faixa horizontal. |
| **`useInboxes.js`** [NOVO] (hook) | `web/static/js/hooks/useInboxes.js` | Busca as inboxes do usuário (de `GET /api/conversations/filter-schema` ou endpoint dedicado de inboxes do plano 02), expõe `inboxes` + `unreadByInbox`, e reage ao WS `inbox_membership_changed` para re-buscar. |

### 3.3 Lista de conversas

| Componente | Caminho | O que muda |
|---|---|---|
| **`Conversations.js`** (rename de `Contacts.js`) [EDITA] | `web/static/js/components/contacts/Conversations.js` | Plano 01 é o dono do rename. Orquestra: `activeInboxId` (do rail), `statusTab` (`open`/`mine`/`unassigned`/`resolved`), `filters` (plano 08, `useState({})`), `showArchived` (toggle dedicado — P81). Substitui `getContacts(q, archived)` por `getConversations(params)` montando `?status=&assignee=&inbox_id=&labels=&since=&cursor=`. Mantém **todos** os handlers WS atuais e adiciona os de conversa (§5). Seleção passa a ser por `conversation_id` (URL `/conversations/:id`). |
| **`ConversationList.js`** (evolui `ContactList.js`) [EDITA] | `web/static/js/components/contacts/ContactList.js` | (1) **Abas de status** abaixo da busca: Abertas / Minhas / Não atribuídas / Resolvidas. (2) **Toggle Arquivadas** continua (já existe, `onToggleArchived`). (3) Cada linha ganha **badge de status** (Aberta/Resolvida) e **avatar do assignee** quando houver. O **badge de grupo** já existe via `GroupAvatar`; reforçar com um selo "grupo" textual (P8). (4) O **toggle de IA global** e o **archive** que hoje moram no header teal migram: IA global vai para Configurações; o rail assume a navegação. (5) Preview/ordenação por `last_activity_at` (ver Pergunta em aberto #4). |
| **`StatusTabs.js`** [NOVO] | `web/static/js/components/contacts/StatusTabs.js` | Barra de abas controlada: `value`, `onChange`, `counts` (badge por aba, ex.: "Não atribuídas (5)"). Mapeia cada aba para params de `GET /api/conversations`. |
| **`FilterBar.js`** [NOVO] (plano 08) | `web/static/js/components/contacts/FilterBar.js` | Barra de **chips removíveis** dos filtros ativos (`Status: Aberto ✕`, `Atendente: eu ✕`, `Tag: lead ✕`) + dropdowns rápidos de Status/Assignee/Inbox alimentados por `filter-schema`. Botão "Filtro avançado" abre o drawer; botão "Salvar filtro". |
| **`FilterChip.js`** [NOVO] (plano 08) | `web/static/js/components/contacts/FilterChip.js` | Chip individual com label + `✕`. |
| **`AdvancedFilterDrawer.js`** [NOVO] (plano 08, fase 2) | `web/static/js/components/contacts/AdvancedFilterDrawer.js` | Linhas `[atributo ▾][operador ▾][valor][AND/OR ▾]` (AND/OR plano — P78). Submete via `POST /api/conversations/filter`. |
| **`SavedFilters.js`** [NOVO] (plano 08, fase 3) | `web/static/js/components/contacts/SavedFilters.js` | Lista de views salvas (escopo `user`/`global` — P79) no topo da lista / no GearMenu, aplicáveis em 1 clique. |

### 3.4 Conversa (chat + composer)

| Componente | Caminho | O que muda |
|---|---|---|
| **`ContactDetail.js`** [EDITA] | `web/static/js/components/contacts/ContactDetail.js` | (1) **Barra de ações da conversa** no header (ou logo abaixo dele): `Atribuir a mim` (`POST /api/conversations/{id}/assign-me`), `Transferir ▾` (abre `AssigneePicker`, gate `conversation.assign`), `Resolver`/`Reabrir` (`PATCH …{status:'closed'|'open'}`, gate `conversation.resolve`), `Arquivar`/`Desarquivar` (`PATCH …{is_archived}`), e o **toggle de IA da conversa** (`PATCH …{ai_active}` — substitui o `toggle-ai` por contato, P5). (2) Header mostra `#display_id` + nome do canal de origem. (3) **Composer ganha o gatilho `/`** (respostas rápidas — ver `QuickReplyMenu` abaixo). (4) Plano 02 fase 2 (depois): fora da janela 24h, **bloquear texto livre + oferecer seletor de template** (P17). |
| **`ConversationActions.js`** [NOVO] | `web/static/js/components/contacts/ConversationActions.js` | Extrai a barra de ações do header para um componente próprio. Props: `conversation`, `currentUser`, `onPatch(fields)`, `onAssignMe`. Renderiza cada botão só com a permissão correspondente (P48). |
| **`AssigneePicker.js`** [NOVO] | `web/static/js/components/contacts/AssigneePicker.js` | Dropdown de agentes (membros da inbox) com busca, reusando a estética do `TagPicker`/dropdown de @menção. Seleciona um `assignee_user_id` → `PATCH`. Inclui "Não atribuída". |
| **`StatusSelect.js`** [NOVO] | `web/static/js/components/contacts/StatusSelect.js` | Seletor Resolver/Reabrir (só `open`/`closed` — P3). Usado tanto no header quanto no painel de info. |
| **`QuickReplyMenu.js`** [NOVO] (plano 04) | `web/static/js/components/contacts/QuickReplyMenu.js` | **Clona o mecanismo de @menção do `ContactDetail.js`.** Estado `quickReplyMenu = {query, start, index}` (espelha `mentionMenu`, linha 53). Detecção: `/(?:^|\s)\/([\w-]*)$/` (espelha `updateMentionMenu`, 203-209). Candidatos = `quickReplies.filter(qr => qr.short_code.includes(q))` slice 8 (espelha `getMentionCandidates`, 191-200). Aplicação insere `cand.content` literal e reposiciona o caret, **sem enviar** (espelha `applyMention`, 212-230). Navegação ArrowUp/Down/Enter/Tab/Escape reusa a lógica de `handleKeyDown` (385-405). Dropdown mostra `/short_code` + preview do `content`, classes `wa-*`. **NÃO restrito a grupos** (diferente do @menção). Cache no client + listener do evento DOM `whatsbot:quick-replies-changed` (P44). |

> **Nota de implementação do `/`:** o `ContactDetail.js` já tem todo o andaime (caret tracking,
> teclado, dropdown posicionado). A forma de menor risco é generalizar o handler de teclado para
> tratar `mentionMenu` **ou** `quickReplyMenu`, e extrair o dropdown num componente parametrizável.
> Manter o @menção funcionando idêntico.

### 3.5 Painel de info

| Componente | Caminho | O que muda |
|---|---|---|
| **`ContactInfoPanel.js`** [EDITA] | `web/static/js/components/contacts/ContactInfoPanel.js` | (1) Cabeçalho ganha **seção de conversa**: `StatusSelect` (Resolver/Reabrir), `AssigneePicker` (atribuir), e o **toggle de IA da conversa**. (2) Etiquetas: reusa o editor de tags atual (tags do contato servem à conversa — P77). (3) **Seção dinâmica de atributos de CONTATO** (plano 05): após o array fixo `fields`, busca `getCustomAttributes('contact')` e renderiza um `CustomAttributeField` por definição. (4) **Seção dinâmica de atributos da CONVERSA** (plano 05 fase 5): `getCustomAttributes('conversation')`. `form` ganha sub-objetos `customAttributes`; `onSave` envia `custom_attributes` em `PUT /api/contacts/{phone}/info` (contato) e `PATCH /api/conversations/{id}` (conversa). |
| **`CustomAttributeField.js`** [NOVO] (plano 05) | `web/static/js/components/contacts/CustomAttributeField.js` | Renderiza por `type`: text→`input.wa-field`, number→`input[type=number].wa-field`, date→`input[type=date]`, list→`select.wa-field`, checkbox→`input[type=checkbox]`, link→`input[type=url]` (leitura mostra `<a>`). Reusável por contato e conversa. |

### 3.6 Telas de gestão (GearMenu / SPA, gateadas por permissão)

| Componente | Caminho | Papel | Gate |
|---|---|---|---|
| **`LoginScreen.js`** [EDITA] | `web/static/js/components/LoginScreen.js` | Campo único de senha → **email + senha** → `login(email,password)`; guarda `token` + `currentUser`. Trata `needs_bootstrap` (criar 1º admin). | — |
| **`UsersManager.js`** [NOVO] (plano 03) | `web/static/js/components/UsersManager.js` | CRUD de usuários, **seletor de papel único** (1 papel/usuário no MVP — P40), reset de senha pelo admin (temporária, sem SMTP — P37), "encerrar todas as sessões", ativar/desativar. SPA `/usuarios`. | `users.manage` |
| **`ChannelsManager.js`** [NOVO] (plano 02) | `web/static/js/components/ChannelsManager.js` | Lista de canais como cards (`display_name`, provider, status, `own_phone`); adicionar/desabilitar/remover. Form Cloud API (Phone Number ID, WABA ID, token mascarado, verify token, app secret) exibindo a **webhook URL** para colar na Meta. Aba de templates (upload + "sincronizar" sob demanda — P19). QR por device para GOWA multi-número (reusa o componente de QR atual). SPA `/channels`. | `channel.manage` |
| **`QuickReplies.js`** [NOVO] (plano 04) | `web/static/js/components/QuickReplies.js` | Lista (`/short_code` + preview), form criar/editar com `short_code` validado no front (`^[a-z0-9][a-z0-9_-]*$`, minúsculas, sem espaços/acentos, sem `/` inicial — P45) e `content` (texto puro, sem `{{}}` — P47). Dispara `whatsbot:quick-replies-changed` ao salvar/excluir. SPA `/quick-replies`. | `quickreply.manage` |
| **`CustomAttributesManager.js`** [NOVO] (plano 05) | `web/static/js/components/CustomAttributesManager.js` | CRUD de definições: `display_name`, `attribute_key` (slug snake_case, imutável após criar), `type`, `applies_to` (contact/conversation — P51/P54), `options` (editor de lista quando type=list), `required`, `description`, `regex`. SPA `/atributos`. | admin |
| **`PluginScreen.js`** [SEM MUDANÇA] | `web/static/js/components/PluginScreen.js` | Continua igual (`import()` dinâmico + `apiBase`). Telas de plugin de canal (`whatsapp_cloud.js`) carregam por aqui via `config:true`. | — |

---

## 4. Navegação e roteamento

### 4.1 Como rail + abas + GearMenu convivem

- **Rail (eixo: inbox)** — escolhe *de qual caixa* vêm as conversas. `activeInboxId` (`null` = Todas).
- **Abas de status (eixo: ciclo de vida)** — dentro da inbox ativa, filtra por Abertas/Minhas/
  Não-atribuídas/Resolvidas. `statusTab`.
- **FilterBar / chips (eixo: ad-hoc)** — refina por tag, atributo, data, etc. (plano 08).
- **GearMenu (eixo: navegação para fora do inbox)** — telas de gestão e configuração full-page.

Os três eixos de filtro (rail + abas + chips) **compõem** uma única query a `GET /api/conversations`.
O GearMenu é ortogonal: troca a `tab` global do `app.js` (sai da tela de conversas).

### 4.2 Rotas SPA

Estender `CORE_ROUTES`/`CORE_TAB_PATHS` em `app.js` (39-56) e o `_SPA_PATHS` no backend:

| Rota | Tela | Observação |
|---|---|---|
| `/` | conversas (Todas, Abertas) | default |
| `/conversations/:id` | conversa aberta | **substitui** `/contacts/:id`; manter regex antiga por compat/deep-link |
| `/inbox/:inboxId` | conversas de uma inbox | deep-link do rail (opcional; pode ser query `?inbox=`) |
| `/usuarios` | `UsersManager` | gate `users.manage` |
| `/channels` | `ChannelsManager` | gate `channel.manage` |
| `/quick-replies` | `QuickReplies` | gate `quickreply.manage` |
| `/atributos` | `CustomAttributesManager` | gate admin |
| `/wizard` | SetupWizard / bootstrap | já existe; reusa para "criar 1º admin" |

**Deep-link:** abrir `/conversations/123` diretamente seleciona aquela conversa; se o usuário não for
membro da inbox dela (atendente), mostrar "conversa indisponível" em vez de 403 cru. A resolução
`initialContactId → phone` de hoje (`Contacts.js:347-365`) vira `initialConversationId → conversation`.

### 4.3 Compat de URL

`/contacts/:id` (id de contato) continua resolvendo: redireciona para a conversa ativa daquele
contato. Garante que links antigos/bookmarks não quebrem.

---

## 5. Tempo real (WebSocket)

A UI já consome ~15 eventos via [`useWebSocket`](../web/static/js/hooks/useWebSocket.js) e os
distribui em `app.js` → `Contacts.js`. O Pro **adiciona** os eventos de conversa abaixo (plano 01).

### 5.1 Eventos novos a consumir

| Evento | `data` | Reação na UI |
|---|---|---|
| `conversation_created` | `{conversation_id, display_id, contact_phone, inbox_id, status}` | Se pertence à inbox/aba ativa, **inserir no topo da lista**; incrementar badge de "Não atribuídas" e da inbox no rail. |
| `conversation_status_changed` | `{conversation_id, status, prev_status, by_user_id?}` | Atualizar badge da linha; se mudou para `closed` e a aba ativa é "Abertas", **remover da lista**; se reabriu, reinserir. Atualizar `StatusSelect` na conversa aberta. |
| `conversation_assigned` | `{conversation_id, assignee_user_id, team_id?, by_user_id?}` | Atualizar avatar do assignee na linha e no painel de info; se a aba é "Não atribuídas" e foi atribuída, removê-la; se é "Minhas" e virou minha, inseri-la. |
| `conversation_archived` | `{conversation_id, is_archived, by_user_id?}` | Remover/inserir conforme o toggle de Arquivadas ativo. |
| `conversation_updated` | `{conversation_id, fields:{...}}` | Atualizar campos avulsos (ex.: custom_attributes) na conversa aberta. |
| `conversation_ai_toggled` | `{conversation_id, ai_active}` | Atualizar o toggle de IA da conversa (estende o atual `contact_ai_toggled`). |
| `inbox_membership_changed` | `{user_id, inbox_id, action}` | Se for o `currentUser`, **re-buscar o rail** (`useInboxes`) — entrou/saiu de uma inbox. |

Além disso: `new_message` agora carrega `conversation_id` → rotear a mensagem para a conversa certa
(hoje roteia por `phone`). `human_transfer_alert` também carrega `conversation_id`.

### 5.2 Estratégia de reconciliação

- O **roteamento por `conversation_id`** substitui o casamento por `phone` nos handlers de
  `Conversations.js` (hoje em `Contacts.js:615-734`). Manter a mesma estratégia de buffer
  (`pendingWsMessages`) e dedupe por `msg_id`/`ts`.
- **Re-avaliação de filtros (plano 08):** quando um evento WS toca uma conversa que pode ter mudado de
  match (ex.: foi resolvida e a aba é "Abertas"), o MVP faz **refetch debounced** da lista; a fase 4
  avalia o `FilterSpec` no client para decidir inserir/remover sem refetch.
- **Auth do WS (plano 03):** o handshake do `/ws` precisa do Bearer token via query (`?token=`) ou
  subprotocol — `Authorization` header não é confiável em WS no browser. Ajustar `useWebSocket`.
- **P57 (1 worker):** a invalidação de cache do client é **por evento WS** (não há fan-out
  multi-worker). Sem implicação de UI além de confiar nos eventos para atualizar.

### 5.3 Invalidações que NÃO são WS

- **Respostas rápidas (P44):** evento DOM client-side `whatsbot:quick-replies-changed` (não passa pelo
  servidor). O composer escuta; a tela de gestão dispara.
- **Atributos personalizados:** o bus emite `custom_attribute.created/updated/deleted` (servidor); a UI
  pode re-buscar as definições ao abrir o painel de info (sem precisar de WS dedicado no MVP).

---

## 6. Permissões na UI

Tudo gira em torno de `currentUser.permissions[]` (catálogo de 16 chaves do plano 03). **Regra P48:
esconder, não desabilitar.**

| Elemento de UI | Visível quando |
|---|---|
| Rail: ícone de uma inbox | usuário é membro da inbox (atendente) **ou** admin (vê todas) |
| Aba "Não atribuídas" | sempre (escopo = inboxes visíveis); admin vê a fila global |
| Header da conversa: `Atribuir a mim` | `conversation.assign` (ou ser membro com permissão de pegar) |
| Header da conversa: `Transferir` | `conversation.assign` |
| Header da conversa: `Resolver`/`Reabrir` | `conversation.resolve` |
| Header da conversa: enviar mensagem | `conversation.reply` |
| Toggle de IA da conversa | `conversation.reply` (operar a conversa) |
| Painel de info: editar dados do contato | `contact.write` |
| GearMenu: Usuários | `users.manage` (admin exclusivo — P33) |
| GearMenu: Canais | `channel.manage` |
| GearMenu: Respostas rápidas | `quickreply.manage` (atendente também — P43) |
| GearMenu: Atributos personalizados | admin |
| GearMenu: Custos | `billing.manage` |
| GearMenu: Configurações | `settings.manage` |
| GearMenu: Plugins / Tools | `plugins.manage` |

**Tela inicial por papel (plano 03):** um usuário só com `conversation.*` cai direto na caixa de
entrada (sem ver Painel/Configurações). Defesa em profundidade: o backend continua aplicando RBAC; a
UI só evita mostrar o que não adianta clicar.

---

## 7. Fases de implementação (alinhadas às ondas do plano-mestre)

O frontend deste plano **segue as ondas** do `00-plano-mestre.md`. Cada fase abaixo é a *parte de
frontend* de uma onda, com critério de pronto verificável.

### FF1 — Login multi-user + permissões na UI (Onda 1, depende de plano 03 fases 1-3)

- `LoginScreen.js` email+senha; bootstrap do 1º admin; `getMe()` no boot; `currentUser` em contexto;
  GearMenu gateado por permissão; `useWebSocket` autentica via `?token=`.
- **Pronto quando:** login real funciona; itens do GearMenu somem conforme a permissão; um atendente
  não vê Usuários/Canais; reload preserva sessão (token no localStorage).

### FF2 — Domínio de conversas: rail, abas, ações, fila (Onda 2, depende de plano 01 + 03 fase 4)

- `InboxRail.js` + `useInboxes`; `Contacts.js → Conversations.js`; `StatusTabs.js`; ações de conversa
  no header (`ConversationActions`, `AssigneePicker`, `StatusSelect`); toggle de IA da conversa (P5);
  badge de grupo (P8); novos handlers WS de conversa; roteamento por `conversation_id`.
- **Pronto quando:** pegar conversa da fila → resolver → reabrir tudo via WS sem reload, modo escuro
  OK; atendente vê só as inboxes em que é membro; admin vê todas; lista atualiza em tempo real ao
  criar/atribuir/resolver.

### FF3 — Operação do atendente: respostas rápidas + atributos de contato (Onda 3, planos 04 + 05 f1-4)

- `QuickReplyMenu.js` (gatilho `/` no composer) + `QuickReplies.js` (gestão); `CustomAttributeField.js`
  + `CustomAttributesManager.js` + seção dinâmica de atributos de **contato** no `ContactInfoPanel`.
- **Pronto quando:** `/` abre dropdown filtrado (lista global), Enter/Tab expande o `content` sem
  enviar, Escape fecha; admin cria atributo `plano` (list) e `vip` (checkbox) e eles aparecem/editam no
  painel de contato; valor inválido rejeitado.

### FF4 — Multi-canal: tela de Canais (Onda 4, planos 02 f1-3 + 09 f4-6)

- `ChannelsManager.js` (cards, add Cloud API com webhook URL + templates, QR por device GOWA).
- **Pronto quando:** adicionar canal Cloud API mostra a webhook URL para colar na Meta; 2 números GOWA
  conectam por QR próprio; status/own_phone refletem por canal; tokens mascarados na UI (P15).

### FF5 — Atributos de conversa (Onda 5, plano 05 fase 5)

- Habilitar `applies_to=conversation` no `CustomAttributesManager` e renderizar a seção de atributos de
  **conversa** no painel de info.
- **Pronto quando:** admin cria atributo de conversa; ele aparece no painel da conversa selecionada e
  salva via `PATCH /api/conversations/{id}`.

### FF6 — Filtros e views salvas (Onda 6, plano 08)

- `FilterBar.js` + `FilterChip.js` (fase 1, query params + chips) → `AdvancedFilterDrawer.js` (fase 2,
  `POST /filter`) → `SavedFilters.js` (fase 3, views `user`/`global`); scroll infinito com cursor
  opaco, página 30, teto ~100 (P80).
- **Pronto quando:** filtrar por status+assignee+tag via chips funciona; drawer avançado monta payload
  Chatwoot-style; salvar/aplicar view em 1 clique; scroll infinito carrega páginas; escopo global só
  para admin.

> **Dependências cruzadas (resumo):** FF1⟸plano03 · FF2⟸planos01+03 · FF3⟸planos04+05 · FF4⟸planos02+09 ·
> FF5⟸plano05f5(⟸01) · FF6⟸planos08+05f6(índices). O rename `Contacts.js→Conversations.js` (FF2) é
> pré-requisito de FF3/FF5/FF6 mexerem nos mesmos arquivos — coordenar para não haver conflito de merge.

---

## 8. Modo escuro / acessibilidade — checklist (obrigatório por CLAUDE.md)

Para **toda** tela/card/modal novo (`InboxRail`, `StatusTabs`, `FilterBar`, `ConversationActions`,
`AssigneePicker`, `StatusSelect`, `QuickReplyMenu`, `QuickReplies`, `UsersManager`, `ChannelsManager`,
`CustomAttributesManager`, `CustomAttributeField`):

- [ ] Superfícies/textos/bordas via classes semânticas `wa-*` (`bg-wa-bg`, `bg-wa-panel`,
      `text-wa-text`, `text-wa-secondary`, `border-wa-border`, `bg-wa-hover`, `bg-wa-teal`) — nunca
      `bg-white`/`text-gray-*` cru sem fallback.
- [ ] **Campos de formulário** com `.wa-field` (input/textarea/select) — não deixar no branco padrão do
      navegador.
- [ ] Controles nativos (date/checkbox/select do `CustomAttributeField`) herdam `color-scheme` — OK.
- [ ] **Testar com `.dark` ligado** cada tela antes de marcar pronto; conferir contraste de badges de
      status, chips de filtro e avatar de assignee.
- [ ] Cores cruas só onde há fallback em `custom.css`; hex inline (ex.: cor de tag) já é tratado com
      alpha — manter o padrão atual (`${color}20` fundo, `${color}40` borda).
- [ ] Navegação por teclado no `QuickReplyMenu` e `AssigneePicker` (ArrowUp/Down/Enter/Esc), espelhando
      o @menção.
- [ ] Foco visível e `aria-label` nos botões de ação da conversa (Resolver/Transferir/etc.).
- [ ] Badges de não-lidas no rail com `title`/`aria-label` ("3 não lidas em Vendas").

---

## 9. Perguntas em aberto (para o Thiago decidir depois)

> Numeradas FQ1.. (Frontend Question). Cada uma traz contexto + opções + recomendação.
>
> **✅ TODAS DECIDIDAS (2026-06-19):** Thiago aceitou as recomendações. FQ1=b (rail só com ≥2
> inboxes), FQ2=a (IA: global→Config, inbox→Canais, conversa→header), FQ3=a (4 abas), **FQ4=ordenar
> por última atividade, mais recente no topo (encerra P81)**, FQ5=a (grupos rotulados), FQ6=a
> (full-page), FQ7=c (nome no header + ícone na lista). Registro canônico em `DECISOES.md`.

**FQ1. Posição do rail vs. layout WhatsApp-Web atual.**
- *Contexto:* o rail (extrema esquerda) empurra todo o layout 56px para a direita. Hoje o header teal da
  lista hospeda o toggle de IA global e o archive.
- *Opções:* (a) rail vertical permanente à esquerda (Chatwoot puro); (b) rail só aparece quando há >1
  inbox (instalação single-inbox migrada fica idêntica ao hoje); (c) integrar as inboxes como um
  dropdown no header da lista, sem rail.
- *Recomendação:* **(b)** — uma instalação migrada do single-número tem 1 inbox; mostrar o rail só com
  ≥2 inboxes evita estranhar o usuário existente, e ele aparece naturalmente quando o admin adiciona o
  2º canal. Reduz o atrito da migração.

**FQ2. Para onde vai o toggle de "IA global" que hoje vive no header da lista.**
- *Contexto:* `ContactList.js:234-248` tem o botão "IA Ativada/Desativada" global. Com a cascata P5
  (global→inbox→conversa), o nível global continua existindo, mas o header da lista vai ficar ocupado
  com abas+filtros.
- *Opções:* (a) mover para Configurações; (b) mover para o rodapé do rail (ao lado da engrenagem); (c)
  manter no header da lista.
- *Recomendação:* **(a) Configurações** para o nível global; o nível **inbox** vira uma opção na tela de
  Canais/Inbox; o nível **conversa** é o toggle no header/painel da conversa. Mantém cada nível da
  cascata no lugar conceitualmente correto.

**FQ3. Granularidade visual das abas de status.**
- *Contexto:* P3 define só `open`/`closed`. As abas propostas são Abertas/Minhas/Não-atribuídas/
  Resolvidas — "Minhas" e "Não-atribuídas" são recortes de "Abertas" por assignee.
- *Opções:* (a) 4 abas como acima; (b) 2 abas (Abertas/Resolvidas) + filtro de assignee nos chips; (c)
  abas configuráveis (views salvas viram abas).
- *Recomendação:* **(a)** no MVP (alinha com o pedido explícito do prompt e com o Chatwoot), evoluindo
  para (c) quando as views salvas (FF6) existirem.

**FQ4. Ordenação da lista — reordenar ao chegar mensagem? (eco do P81, ainda sem resposta)**
- *Contexto:* o `DECISOES.md` (nota P81) e o plano 08 anotam um comentário seu: "as conversas mais
  novas que mensagem forem chegando não subir nas conversas". O padrão de inbox é
  `ORDER BY last_activity_at DESC` (mais recente no topo), que **reordena** ao chegar mensagem.
- *Opções:* (a) reordenar por última atividade (padrão WhatsApp/Chatwoot — a conversa "pula" pro topo);
  (b) ordem estável por chegada/criação (não reordena ao chegar msg); (c) ordenar por aba (fila
  não-atribuída = FIFO por `waiting_since`; abertas = por última atividade).
- *Recomendação:* **(c)** — "Não atribuídas" usa FIFO (`waiting_since`, como manda o plano 01:537);
  "Abertas/Minhas" usam última atividade. Se você confirmar que NÃO quer o "pulo pro topo", trocamos
  "Abertas" para ordem estável. **Precisa da sua confirmação** (mesma pendência do P81).

**FQ5. Atributos de contato vs. de conversa no mesmo painel — como separar visualmente.**
- *Contexto:* P51/P54 permitem a mesma `key` em contato e conversa; o painel mostrará as duas seções.
- *Opções:* (a) dois grupos rotulados ("Dados do contato" / "Dados desta conversa") empilhados; (b)
  duas abas no painel de info; (c) só contato no MVP, conversa numa 2ª iteração (já é o faseamento
  FF3→FF5).
- *Recomendação:* **(a)** com rótulos claros (Chatwoot faz assim), respeitando o faseamento FF3/FF5.

**FQ6. Onde abrir as telas de gestão: full-page (como hoje) ou modal/drawer.**
- *Contexto:* hoje Painel/Plugins/Tools abrem full-page com `PageHeader` + botão voltar (`app.js`
  492-553). Usuários/Canais/Atributos podem seguir o mesmo padrão.
- *Opções:* (a) full-page como as telas atuais (consistente); (b) drawer lateral; (c) modal.
- *Recomendação:* **(a) full-page** — consistência com Plugins/Tools/Custos, zero invenção de padrão
  novo. (Exceção: o "Configurar" de plugin continua em modal, como já é.)

**FQ7. Indicador de canal na conversa (multi-canal).**
- *Contexto:* com vários canais, é útil saber por qual número/canal a conversa chega.
- *Opções:* (a) badge textual do canal no header da conversa (`WhatsApp Vendas`); (b) cor/ícone do
  canal na linha da lista; (c) ambos.
- *Recomendação:* **(c)** — header mostra o nome do canal; a linha da lista mostra um pequeno ícone/cor
  do provider, útil quando o filtro está em "Todas".

---

## Apêndice — Inventário de arquivos de frontend

**Editar (existem hoje):**
`web/static/js/app.js` · `web/static/js/components/LoginScreen.js` ·
`web/static/js/components/contacts/Contacts.js` (→ `Conversations.js`) ·
`web/static/js/components/contacts/ContactList.js` (→ evolui p/ `ConversationList`) ·
`web/static/js/components/contacts/ContactDetail.js` ·
`web/static/js/components/contacts/ContactInfoPanel.js` · `web/static/js/services/api.js`
(novos métodos) · `web/static/js/hooks/useWebSocket.js` (eventos de conversa + auth `?token=`).

**Criar (novos) — 16 componentes/hooks:**
`InboxRail.js` · `hooks/useInboxes.js` · `contacts/StatusTabs.js` · `contacts/FilterBar.js` ·
`contacts/FilterChip.js` · `contacts/AdvancedFilterDrawer.js` · `contacts/SavedFilters.js` ·
`contacts/ConversationActions.js` · `contacts/AssigneePicker.js` · `contacts/StatusSelect.js` ·
`contacts/QuickReplyMenu.js` · `contacts/CustomAttributeField.js` · `QuickReplies.js` ·
`UsersManager.js` · `ChannelsManager.js` · `CustomAttributesManager.js`.

**Sem mudança:** `PluginScreen.js`, `TagPicker.js`, `Header.js`, `EmojiPicker.js`, `AudioPlayer.js`,
demais utilitários de `contacts/`.
