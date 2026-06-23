# Plano de Implementação — 16: Apagar conversa (menu de contexto) e apagar contato (painel de dados)

> O WhatsBot virou **conversa-cêntrico** (1 linha por conversa/canal; um contato pode ter N conversas), mas o
> único caminho destrutivo na UI ainda é contato-cêntrico: o menu de contexto da sidebar só tem **"Apagar
> Contato"** (`DELETE /api/contacts/{phone}`), que apaga o contato inteiro. Faltam duas ações distintas:
> **(A)** no menu de contexto de uma **CONVERSA** → "Apagar Conversa" (apaga só aquela thread/mensagens,
> **mantém** o contato e suas outras conversas); **(B)** no painel **Dados do contato** → "Apagar contato"
> (apaga o contato **e todas** as conversas vinculadas, via cascade).
>
> **Escopo:** (1) nova `conversation_repo.delete(conv_id)` + rota `DELETE /api/conversations/{conv_id}` com
> limpeza correta de `unread_msg_ids` e do contador denormalizado `contacts.unread_count`; (2) reuso da rota
> `DELETE /api/contacts/{phone}` já existente para o caso (B); (3) frontend — item "Apagar Conversa" no
> `ContextMenu` operando por `conversation_id`, botão "Apagar contato" no rodapé do `ContactInfoPanel`,
> `deleteConversation` em `api.js`, e handler do WS `conversation_deleted`; (4) cobertura em
> `tests/test_endpoints.py`.
>
> **Fora de escopo:** não mexer na semântica de **cascade/RBAC** existente (reusar a permissão
> `conversation.resolve`, não inventar `conversation.delete`); não redesenhar a sidebar nem a montagem de
> rows; **soft-delete** (esta entrega é hard-delete, como o `DELETE /api/contacts` atual); **cleanup de mídia
> em disco** órfã (`statics/media`, `statics/senditems`, `statics/avatars`) — fica como dívida opcional
> (justificado na §1); o marcador de pushName e qualquer recriação de conversa vazia pós-delete.

---

## 0. Estado atual VERIFICADO (2026-06-21, working tree pós-`7fda567`)

> Tudo abaixo foi confirmado por leitura com âncora `arquivo:linha` + verificação adversarial (12 veredictos
> `confirmed`, 0 refutados). **Na implementação, re-ancore por `grep` (nome de função/rota/campo/evento),
> nunca por número de linha fixo** — os arquivos de `contacts`/`conversations` e os componentes do painel são
> grandes e mudam.

### Frontend — sidebar de conversas + menu de contexto

- **A sidebar é conversa-cêntrica.** `buildRows(contacts, conversations)` em
  [`web/static/js/components/contacts/Contacts.js`](../web/static/js/components/contacts/Contacts.js#L137)
  empurra **1 row por conversa** (`conversation_id = cv.id`); contatos sem conversa caem numa row única com
  `conversation_id: null, channel_id: 'default'`. Identidade da row =
  `conv:${conversation_id}` ou `phone:${phone}` (`ContactList.js` `rowKeyFor`, ~`:42`). **Um número em 2
  canais vira 2 rows distintas.**
- **O clique-direito já carrega `conversationId`.** `ContactList.js` (`onContextMenu`, ~`:406`) chama
  `onContextMenu({ x, y, phone, conversationId: c.conversation_id ?? null, ... })`; a prop é
  `onContextMenu=${setCtxMenu}` (`Contacts.js` ~`:1397`) → vira o state `ctxMenu` direto.
- **O `conv` (com `conv.id`) é resolvido lazily** num `useEffect([ctxMenu])` (`Contacts.js` ~`:853-870`):
  `getConversation(convId)` quando `conversationId != null`. Em
  [`ContextMenu.js`](../web/static/js/components/contacts/ContextMenu.js#L22) há
  `canAct = !!(conv && conv.id != null) && !convLoading`, e `conv.id` já é usado em
  `onAssignConversation(conv.id, …)` e `onResolveConversation(conv.id, …)`. **"Apagar Conversa" usa o mesmo
  `conv.id` com o mesmo guard `canAct`.**
- **O destrutivo atual é contato-cêntrico.** `ContextMenu.js` (~`:232-247`) tem o botão "Apagar Contato" com
  confirmação inline em 2 cliques (`useState(confirmDelete)`, `text-red-400 / bg-red-500/10`) → `onDelete(phone)`.
  `Contacts.js` (~`:1545`) liga `onDelete=${handleDelete}`; `handleDelete(phone)` (~`:362-373`) chama
  `deleteContact(phone)` e faz **remoção otimista por phone** (`setContacts(prev => prev.filter(c => c.phone !== phone))`).
- **`ContextMenu` é montado em `Contacts.js`** (~`:1506-1549`), não em `ContactList.js` (que só renderiza as
  rows + dispara `onContextMenu`).
- **`deleteConversation` NÃO existe** em
  [`web/static/js/services/api.js`](../web/static/js/services/api.js) — há `getConversation`,
  `listConversations`, `assignConversation`, `setConversationStatus`, `archiveConversation`,
  `setConversationAi`, mas nenhum `DELETE /api/conversations/{id}`. `deleteContact(phone)` existe (~`:202-204`).
- **Pipeline WS de conversa pronto.** `useWebSocket.js` (~`:8-9`, `:34-39`) mapeia
  `conversation_created/status_changed/assigned/archived/ai_toggled/updated` via `conv=(name)=>(d)=>onConversationChanged(name,d)`;
  `Contacts.js` `onConversationChanged(name,data)` (~`:783-800`) casa rows por `conversation_id`.
- **`contact_deleted` é emitido mas não escutado.** Backend
  [`server/routes/contacts.py`](../server/routes/contacts.py#L262) faz
  `ws_manager.broadcast("contact_deleted", {"phone": phone})`, mas `grep contact_deleted|conversation_deleted`
  em `web/static/js/` = **0 ocorrências**. Hoje a remoção é puramente otimista no cliente que disparou.

### Frontend — painel "Dados do contato"

- **`ContactInfoPanel`** ([`ContactInfoPanel.js`](../web/static/js/components/contacts/ContactInfoPanel.js#L21))
  recebe `{ phone, info, contactTags, globalTags, onGlobalTagsChange, isGroup, groupName, avatarV, onClose,
  onSave }` — **nenhuma prop de delete**. É um slide-in com header `bg-wa-teal`, conteúdo scrollável e
  **rodapé fixo** (`<div class="px-6 py-4 bg-wa-panel border-t border-wa-border shrink-0">` ~`:441-455`)
  contendo o botão **Salvar**. Montado em `Contacts.js` (~`:1460-1478`) quando `openPanel === 'contact'`.
- **`handleDelete` já faz tudo** (`Contacts.js` ~`:362-373`): `deleteContact(phone)` → em sucesso remove da
  sidebar, zera `selected/selectedConvId/contactData` e navega para `/`.

### Backend — rotas de delete

- **`delete_contact` cobre o caso (B) inteiro.**
  [`contacts.py`](../server/routes/contacts.py#L244) `@app.delete("/api/contacts/{phone}")`, gated
  `permission_denied(request, "contact.write")` (~`:247`), chama `contact_repo.delete(data["id"])`,
  `agent_handler.drop_cached_contact(phone)` e `broadcast("contact_deleted", {phone})`.
  [`contact_repo.delete`](../db/repositories/contact_repo.py#L87) só faz `sa_delete(contacts).where(id==…)`
  e confia no **CASCADE**.
- **NÃO existe delete de conversa.** Em
  [`conversations.py`](../server/routes/conversations.py) o único `@app.delete` é
  `/api/conversations/{conv_id}/templates/{name}` (~`:595`, template, não a conversa). `grep "def delete"`
  em [`conversation_repo.py`](../db/repositories/conversation_repo.py) = **0**.
- **Padrão canônico de rota de conversa** (`set_status`, `conversations.py` ~`:255-266`):
  `permission_denied(request, "conversation.resolve")` → resolver conv → 404 se `None` →
  `conversation_repo.set_*` via `asyncio.to_thread` → `_broadcast(deps, ws_event, bus_event, conv)` →
  `_emit_notice(...)` → `_ok({...})`. Helpers reaproveitáveis: `_broadcast` (~`:31-57`, monta payload do row
  + WS + `emit_with_filter`, ambos defensivos), `_inbox_hidden(request, inbox_id)` (~`:83`, usado em
  `get_conversation` ~`:195` para 404 por scoping), `get_with_channel` (~`:314`).
- **Permissão correta = `conversation.resolve`.**
  [`server/permissions.py`](../server/permissions.py) (catálogo ~`:9-15`) tem apenas
  `conversation.read/read_all/reply/assign/resolve` + `contact.read/write`. **Não existe**
  `conversation.write/manage/delete`. `conversation.resolve` já é tida por gestor e atendente em
  `ROLE_DEFAULTS` (~`:39-47`) → reusar não exige migration de re-seed RBAC.

### Cascade / FK / órfãos (o que NÃO cascateia)

- **Apagar CONTATO cascateia para tudo** — confirmado no schema real. Em
  [`db/tables.py`](../db/tables.py): `observations.contact_id`, `messages.contact_id`, `usage.contact_id`,
  `contact_tags.contact_id`, `contact_inboxes.contact_id`, `conversations.contact_id`,
  `unread_msg_ids.contact_id` — **todos `ondelete=CASCADE`**. `PRAGMA foreign_keys=ON` é aplicado em runtime
  ([`db/engine.py`](../db/engine.py#L150) dentro do `event.listens_for("connect")`). Logo (B) funciona só com
  `contact_repo.delete`.
- **CRÍTICO — `messages.conversation_id` NÃO tem FK no SQLite real.**
  [`db/tables.py`](../db/tables.py#L117) **declara** `ForeignKey("conversations.id", ondelete="CASCADE")`, mas
  a migration
  [`20260620_0013_inbox_conversations.py`](../db/alembic/versions/20260620_0013_inbox_conversations.py#L100)
  adicionou a coluna via `op.add_column(... nullable=True)` **sem FK** (nota explícita: "plain column without
  inline FK — SQLite não suporta ADD COLUMN com constraint de FK"). `PRAGMA foreign_key_list(messages)` na DB
  viva só retorna `contact_id`. **Conclusão: o delete de conversa DEVE apagar `messages` explicitamente
  `WHERE conversation_id=:id`; não confiar no CASCADE declarativo.** (Em Postgres o FK pode existir via
  metadata e cascatear → o DELETE explícito vira no-op idempotente; o código roda nos dois backends.)
- **`unread_msg_ids` não tem `conversation_id`.**
  [`db/tables.py`](../db/tables.py#L430) = `(id, contact_id FK→contacts CASCADE, msg_id Text)`. O unread
  por-conversa é **derivado** via join `unread_msg_ids ⋈ messages.msg_id` filtrando `messages.conversation_id`
  ([`conversation_repo.mark_conversation_read`](../db/repositories/conversation_repo.py#L326)). `contacts.unread_count`
  é **denormalizado** (coluna real). **Apagar a conversa sem limpar antes deixa linhas órfãs em
  `unread_msg_ids` e badge fantasma no contador** — precisa espelhar `mark_conversation_read`.
- **Mensagens com `conversation_id` NULL** (raras): backfill da 0013 (~`:152-154`) stampou todo contato
  existente; saves novos passam por `ContactMemory.add_message` que resolve a conversa, mas em falha
  transitória o stamp pode ficar NULL. `delete(conv_id)` por definição só apaga `WHERE conversation_id=conv_id`
  → órfãs NULL persistem (não corrompem o delete da conversa). Risco baixo, documentado.
- **Mídia em disco fica órfã.** `messages.media_path` guarda só caminho relativo (`statics/media/…`,
  `statics/senditems/…`); nenhum `os.remove` no path de delete; `statics/avatars/<phone>.jpg` é cache
  auto-recuperável. Decisão: **não limpar nesta entrega** (ver §1).

### `system_notices` e bus de plugin

- **Apagar conversa NÃO deve emitir `conversation_event`.** [`server/system_notices.py`](../server/system_notices.py)
  não tem `event_type` "deleted"/"removed" em `EVENT_GROUP_OF`/`FORMATTERS`; `emit_conversation_notice` gravaria
  `message_repo.add(role="conversation_event", conversation_id=…)` — a linha cairia justamente na conversa que
  está sendo apagada. A rota DELETE **não** chama `_emit_notice`.
- **`conversation.deleted` não está no allowlist** `_KNOWN_EVENTS`
  ([`plugins/events.py`](../plugins/events.py#L64), lista `conversation.created/status_changed/assigned/archived/ai_toggled/updated`).
  `emit()` não valida o emissor (o evento já dispararia), mas **subscrever** a um evento fora da lista loga
  warning → adicionar à tupla é recomendado (não bloqueante).

---

## 1. Decisões de design

1. **Conversa vs contato — duas ações distintas, dois hosts de UI.**
   - **(A) Apagar Conversa** → vive no **menu de contexto da linha da sidebar** (que já é uma conversa).
     Opera por `conversation_id`. Mantém o contato e suas outras conversas.
   - **(B) Apagar contato** → vive no **rodapé do `ContactInfoPanel`** (painel "Dados do contato"). Apaga o
     contato e **todas** as conversas/mensagens via cascade.
   - Os dois botões coexistem em telas diferentes para não confundir. **Não** colocar "Apagar Contato" e
     "Apagar Conversa" lado a lado no mesmo menu — o destrutivo de contato sai do `ContextMenu` (passa a
     viver só no painel) e o `ContextMenu` ganha "Apagar Conversa" no lugar.

2. **O que cascateia em cada caso.**
   - **(B) contato:** `contact_repo.delete(contact_id)` → CASCADE de `contact_id` remove
     `observations`, `messages`, `usage`, `contact_tags`, `contact_inboxes`, `conversations`,
     `unread_msg_ids`. **Zero backend novo.**
   - **(A) conversa:** **não** há cascade utilizável em SQLite (`messages.conversation_id` sem FK real). O
     repo apaga manualmente: `messages WHERE conversation_id`, `conversation_label_links WHERE conversation_id`
     (defensivo), `conversations WHERE id`.

3. **Por que limpar `unread_msg_ids` + ajustar `unread_count` ANTES do delete (badge fantasma).**
   `unread_msg_ids` não conhece `conversation_id` e `contacts.unread_count` é denormalizado. Se a conversa
   (e suas `messages`) sumir antes de limpar, as linhas de `unread_msg_ids` viram órfãs (apontam para msg_ids
   inexistentes) e o contador fica inflado → badge na sidebar e na aba do browser. **`conversation_repo.delete`
   espelha `mark_conversation_read`**: derivar `n` via join `unread_msg_ids ⋈ messages` filtrando
   `conversation_id`, deletar essas linhas e decrementar `unread_count` com clamp em 0, **na mesma transação**.

4. **Mensagens com `conversation_id` NULL:** aceitas como não-removidas por `delete(conv_id)` (raras; só
   surgem em falha transitória de resolução). Não tentar "varrer tudo do contato" — isso é o caso (B).

5. **Mídia em disco órfã: fora de escopo.** Mídia é per-instância e avatar é cache auto-recuperável; órfãos
   não corrompem dados nem badges, só acumulam lixo. Limpá-los exigiria coleta de `media_path` + guarda de
   path-traversal (só dentro de `statics/`). Fica como dívida opcional, não-bloqueante (§8).

6. **Hard-delete, com dupla confirmação na UI.** Espelha o `DELETE /api/contacts/{phone}` atual (sem
   soft-delete). Por ser irreversível, ambos os botões usam o **padrão de confirmação inline em 2 cliques** já
   existente no `ContextMenu` (`useState(confirmDelete)` → rótulo "Confirmar exclusão?"). O botão de **contato**
   avisa que apaga **TODAS** as conversas.

7. **Permissão:** rota DELETE de conversa usa **`conversation.resolve`** (já existe, gestor + atendente
   têm; semântica de "encerrar conversa"). **Não inventar** `conversation.delete` (exigiria
   `PERMISSION_CATALOG` + `ROLE_DEFAULTS` + migration de re-seed). O caso (B) continua gated por `contact.write`.

---

## 2. Backend — apagar conversa

### 2.1. `conversation_repo.delete(conv_id)`

Em [`db/repositories/conversation_repo.py`](../db/repositories/conversation_repo.py) (o `delete as sa_delete`
já está importado ~`:12`). Adicionar uma função que **lê o row antes**, faz toda a limpeza numa **única
transação** `get_engine().begin()` e **retorna o dict do row lido** (ou `None` se a conversa não existe):

Passos exatos, **nesta ordem**:

1. `SELECT conversations.contact_id WHERE id == conv_id` → se `None`, retornar `None`.
2. **Limpar unread (espelha `mark_conversation_read`, ~`:341-356`):** `SELECT unread_msg_ids.id` via
   `join(messages, messages.c.msg_id == unread_msg_ids.c.msg_id)` filtrando
   `unread_msg_ids.contact_id == contact_id AND messages.conversation_id == conv_id`; `n = len`; se `n`:
   `sa_delete(unread_msg_ids).where(id.in_(row_ids))` e
   `update(contacts).where(id==contact_id).values(unread_count=case((unread_count<=n,0), else_=unread_count-n), updated_at=time.time())`.
3. `sa_delete(messages).where(conversation_id == conv_id)` — **explícito**, não confiar no CASCADE (FK ausente
   no SQLite, ver §0).
4. `sa_delete(conversation_label_links).where(conversation_id == conv_id)` — defensivo (em SQLite legado o FK
   pode faltar; idempotente onde já cascateia).
5. `sa_delete(conversations).where(id == conv_id)`.
6. Retornar o dict do row (pelo menos `id`, `contact_id`; idealmente reusar o que `get_with_channel` traz —
   ver 2.2 sobre ler o payload do WS na rota antes de apagar).

> A função pode retornar só `{id, contact_id}` lido no passo 1; a rota é quem lê `get_with_channel` para o
> payload do WS (phone/inbox_id/display_id). Escolher um dos dois e ser consistente — recomendado: **rota lê
> `get_with_channel` antes; repo retorna `bool`/`contact_id`**.

### 2.2. Rota `DELETE /api/conversations/{conv_id}`

Em [`server/routes/conversations.py`](../server/routes/conversations.py), seguindo o molde de `set_status`/`archive`:

```python
@app.delete("/api/conversations/{conv_id}")
async def delete_conversation(conv_id: int, request: Request):
    denied = permission_denied(request, "conversation.resolve")
    if denied:
        return denied
    conv = await asyncio.to_thread(conversation_repo.get_with_channel, conv_id)
    if not conv:
        return _err("Conversa não encontrada.", status=404)
    if _inbox_hidden(request, conv.get("inbox_id")):
        return _err("Conversa não encontrada.", status=404)   # scoping por inbox (espelha get_conversation)
    await asyncio.to_thread(conversation_repo.delete, conv_id)
    # cache RAM: mensagens da conversa sumiram → invalidar contato.
    # ATENÇÃO: get_with_channel expõe o telefone como `contact_phone` (label em
    # conversation_repo._enriched_columns ~:214), NÃO `phone` — usar a chave certa.
    try:
        deps.agent_handler.drop_cached_contact(conv.get("contact_phone"))
    except Exception:
        pass
    await _broadcast(deps, "conversation_deleted", "conversation.deleted", conv)
    # NÃO chamar _emit_notice — o fio é destruído
    return _ok({"message": "Conversa apagada.", "conversation_id": conv_id, "contact_id": conv.get("contact_id")})
```

Notas:
- **Ler `conv` ANTES de apagar** (depois, `get` retorna `None` e o payload do WS perde `contact_id/inbox_id/phone`).
- `_broadcast` já é defensivo (try/except internos) e dispara **WS `conversation_deleted`** (para a sidebar) +
  **bus `conversation.deleted`** (para plugins).
- **Não** emitir `_emit_notice` (decisão §0/§1).
- Método `DELETE` distinto não colide com os `GET/POST` de `/api/conversations/{conv_id}` nem com rotas
  literais (`/assignable-agents` etc., que são GET).

### 2.3. Bus de plugin

Adicionar `"conversation.deleted"` à tupla de eventos de conversa em
[`plugins/events.py`](../plugins/events.py#L64) (`_KNOWN_EVENTS`). Permite plugins se inscreverem sem warning;
não-bloqueante (o `emit` já dispara mesmo sem registro). Recomendado.

---

## 3. Backend — apagar contato (cobrir múltiplas conversas)

**Nada novo no backend.** [`DELETE /api/contacts/{phone}`](../server/routes/contacts.py#L244) já:
- gated `contact.write`;
- `contact_repo.delete(contact_id)` → CASCADE de `contact_id` remove **todas** as conversas, mensagens,
  `contact_inboxes`, `unread_msg_ids`, `observations`, `usage`, `contact_tags` (confirmado no schema real +
  `PRAGMA foreign_keys=ON` em runtime);
- `agent_handler.drop_cached_contact(phone)`;
- `ws_manager.broadcast("contact_deleted", {"phone": phone})`.

O único gap é de **frontend**: o painel precisa chamar `deleteContact(phone)` (que já existe) e o WS
`contact_deleted` opcionalmente passar a ser escutado para sync cross-cliente (§4.4).

---

## 4. Frontend — menu de contexto: "Apagar Conversa"

### 4.1. `services/api.js`

Adicionar, junto às demais funções de conversa (após `archiveConversation`):

```js
export async function deleteConversation(id) {
  return request('DELETE', `/api/conversations/${id}`);
}
```

### 4.2. `ContextMenu.js`

Em [`ContextMenu.js`](../web/static/js/components/contacts/ContextMenu.js):
- **Remover** o botão "Apagar Contato" do menu (migra para o painel, §5) **ou** mantê-lo apenas se a decisão de
  UX for explícita; recomendação §1: tirar do menu para não confundir.
- **Adicionar** "Apagar conversa" no bloco destrutivo, reusando o padrão do `confirmDelete` (novo state
  `confirmDeleteConv`): 1º clique → rótulo "Confirmar exclusão?" em vermelho; 2º clique → `onDeleteConversation(conv.id)`.
- **Guard**: renderizar/habilitar só quando `canAct` (`conv && conv.id != null && !convLoading`) — rows legadas
  (`conversation_id === null`) não têm conversa para apagar; nesse caso ocultar o item (ou cair no fluxo de
  apagar contato via painel).
- **Cores dark-safe**: `text-red-400` + `hover:bg-wa-hover` e `bg-red-500/10` no estado de confirmação (mesmo
  do botão atual). Resetar `confirmDeleteConv` ao fechar/reabrir o menu.

### 4.3. `Contacts.js`

- Novo handler:

```js
const handleDeleteConversation = useCallback(async (convId) => {
  if (convId == null) return;
  const res = await deleteConversation(convId);
  if (res.ok) {
    setContacts(prev => prev.filter(c => c.conversation_id !== convId));   // por conversation_id, NÃO por phone
    if (selectedConvIdRef.current === convId) {
      setSelected(null); setSelectedConvId(null); setContactData(null);
      history.pushState(null, '', '/');
    }
  }
}, []);
```
  > **Crítico:** filtrar por `conversation_id`, **nunca** por `phone` (um phone tem N rows). Limpar a thread
  > aberta só se `selectedConvIdRef.current === convId`.
- Passar `onDeleteConversation=${handleDeleteConversation}` ao `<ContextMenu/>` (junto de `onDelete`, ~`:1545`).

### 4.4. WS `conversation_deleted`

- [`useWebSocket.js`](../web/static/js/hooks/useWebSocket.js): mapear
  `conversation_deleted: conv ? conv('conversation_deleted') : undefined` (junto de
  `conversation_created/...`, ~`:34-39`). Opcionalmente registrar `contact_deleted` (hoje ignorado) para
  sincronizar (B) entre operadores.
- Em `onConversationChanged(name, data)` (`Contacts.js` ~`:783-800`): se `name === 'conversation_deleted'`,
  `setContacts(prev => prev.filter(c => c.conversation_id !== data.conversation_id))` + limpar thread se
  `selectedConvIdRef.current === data.conversation_id`. **Shape esperado do payload:**
  `{conversation_id, contact_id, phone?}`.

---

## 5. Frontend — painel de dados do contato: "Apagar contato"

### 5.1. `ContactInfoPanel.js`

Em [`ContactInfoPanel.js`](../web/static/js/components/contacts/ContactInfoPanel.js):
- Adicionar prop **`onDeleteContact = null`** à assinatura (~`:21`) — com default para não quebrar call-sites
  futuros; renderizar o botão **só quando a prop existe**.
- No **rodapé fixo** (`<div ... shrink-0>` do Salvar, ~`:441-455`), abaixo do botão Salvar, adicionar o botão
  destrutivo "Apagar contato" com **confirmação inline** (mesmo padrão do `ContextMenu`):
  `const [confirmDelete, setConfirmDelete] = useState(false)` → 1º clique troca o rótulo para
  **"Confirmar exclusão? Apaga TODAS as conversas"** (vermelho); 2º clique chama `onDeleteContact()`.
- **Resetar `confirmDelete` ao trocar de contato** (`useEffect([phone])`) para não confirmar exclusão do
  contato errado.
- **Cores dark-safe**: `text-red-400` + `hover:bg-wa-hover` (semântico/coberto), evitar `red-50/red-300`
  inline. Testar no modo escuro (regra do CLAUDE.md).
- Para **grupos** (`isGroup`): manter (o backend trata grupo como contato); opcionalmente ajustar o rótulo.

### 5.2. `Contacts.js` (call-site)

No `<ContactInfoPanel/>` (~`:1460-1478`), passar:

```js
onDeleteContact=${() => { handleDelete(selected); setOpenPanel(null); }}
```

Reusa o `handleDelete` existente (~`:362-373`), que já cobre `DELETE /api/contacts/{phone}` + CASCADE +
limpeza de seleção/navegação. O painel fecha após disparar.

---

## 6. Testes — `tests/test_endpoints.py`

Hoje **não existe** teste de delete de contato nem de conversa. Reusar o padrão de fixtures da seção
**Conversations** (semeia 2 conversas do mesmo contato e mensagens com `conversation_id`) e os helpers
`check(...)` / `section(...)`. Asserir via `SELECT` no engine (asserir só `200` é fraco). Casos:

6.1. **Apagar 1 de N conversas mantém o resto.** Semear contato com **2 conversas** (`convA`, `convB`) + mensagens
em cada. `DELETE /api/conversations/{convA}` → 200. Via `select`: `conversations` ainda tem `convB`; `messages`
de `convA` **sumiram** e de `convB` **permanecem**; o **contato existe**.

6.2. **Badge fantasma.** Semear unread em `convA` (incrementa `contacts.unread_count` + `unread_msg_ids`).
Apagar `convA` → asserir que `unread_msg_ids` da conversa sumiu e `contacts.unread_count` foi **decrementado**
(clamp 0), e que o unread de `convB` não foi afetado.

6.3. **Apagar contato remove tudo.** Contato com 2 conversas → `DELETE /api/contacts/{phone}` → 200. Via
`select`: contato, **ambas** as conversas, mensagens, `unread_msg_ids`, `contact_tags` sumiram. (Primeiro
teste do `delete_contact`.)

6.4. **Gating.** `DELETE /api/conversations/{id}` sem `conversation.resolve` → **403** (`ok is False`).

6.5. **404.** Conversa inexistente → 404; conversa de **inbox não-visível** (`_inbox_hidden`) → 404.

6.6. **Broadcast** (best-effort): confirmar que `contact_deleted` é emitido no delete de contato (se o harness
captura WS) — opcional.

> Se rodar contra Postgres (`WHATSBOT_TEST_DB_URL`), garantir paridade: lá o FK `conversation_id` pode
> cascatear `messages`; o `DELETE FROM messages WHERE conversation_id` explícito vira no-op idempotente.

---

## 7. Checklist de implementação

Ordem: **repo → rota → bus → api.js → menu → painel → testes**.

- [ ] `conversation_repo.delete(conv_id)` — transação: ler `contact_id` → limpar `unread_msg_ids` + decrementar
      `unread_count` (espelha `mark_conversation_read`) → `DELETE messages WHERE conversation_id` (explícito) →
      `DELETE conversation_label_links` (defensivo) → `DELETE conversations`. (§2.1)
- [ ] Rota `DELETE /api/conversations/{conv_id}` — gate `conversation.resolve`, `get_with_channel` antes,
      `_inbox_hidden`→404, `delete` via `to_thread`, `drop_cached_contact`, `_broadcast("conversation_deleted",
      "conversation.deleted", conv)`, **sem `_emit_notice`**. (§2.2)
- [ ] `"conversation.deleted"` em `_KNOWN_EVENTS` (`plugins/events.py`). (§2.3)
- [ ] `deleteConversation(id)` em `services/api.js`. (§4.1)
- [ ] `ContextMenu.js` — "Apagar conversa" (`conv.id`, guard `canAct`, confirmação inline); remover/realocar
      "Apagar Contato". (§4.2)
- [ ] `Contacts.js` — `handleDeleteConversation` (filtra por `conversation_id`), prop `onDeleteConversation`,
      handler WS `conversation_deleted`. (§4.3/§4.4)
- [ ] `useWebSocket.js` — mapear `conversation_deleted` (e opcionalmente `contact_deleted`). (§4.4)
- [ ] `ContactInfoPanel.js` — prop `onDeleteContact`, botão "Apagar contato" no rodapé com confirmação +
      aviso "apaga TODAS as conversas", reset em `[phone]`. (§5.1)
- [ ] `Contacts.js` — ligar `onDeleteContact` ao `handleDelete` + fechar painel. (§5.2)
- [ ] Testes 6.1–6.5 (+6.6 opcional). Rodar `python tests/test_endpoints.py`. (§6)
- [ ] Conferir contraste no **modo escuro** dos dois botões destrutivos.

---

## 8. Riscos e fora de escopo

- **Remoção otimista por `phone` é ERRADA para conversa** (apagaria todas as rows do número). O handler de
  (A) **deve** filtrar por `conversation_id` e limpar a thread só por `selectedConvIdRef.current === convId`. (§4.3)
- **`messages.conversation_id` sem FK em SQLite** → o `DELETE` da conversa **precisa** apagar `messages`
  explicitamente; confiar no CASCADE declarativo deixaria órfãs em SQLite. (§0/§2.1)
- **Badge fantasma** se o delete não espelhar `mark_conversation_read` (`unread_msg_ids` órfão +
  `unread_count` inflado). Ajustar também o **cache RAM** (`drop_cached_contact`) senão o badge volta ao reabrir. (§2)
- **Rows legadas** (`conversation_id === null`) não têm conversa → "Apagar conversa" desabilitado/oculto
  (`canAct`), senão chamaria `deleteConversation(null)`. (§4.2)
- **Sem handler WS** registrado, a exclusão feita por outro operador não atualiza a sidebar deste cliente
  (hoje `contact_deleted` já sofre disso). Registrar os handlers resolve. (§4.4)
- **Mensagens com `conversation_id` NULL** (raras) não são removidas por `delete(conv_id)` — aceito,
  documentado. (§0/§1)
- **Mídia em disco órfã** (`statics/media`, `statics/senditems`, `statics/avatars`): **fora de escopo** —
  cleanup opcional exigiria coleta de `media_path` + guarda de path-traversal (só dentro de `statics/`). Não
  corrompe dados nem badges. (§1)
- **Multi-réplica / Postgres compartilhado:** apagar a conversa numa instância remove as `messages` do DB
  compartilhado, mas arquivos de mídia ficam no disco local de quem enviou — comportamento pré-existente (mídia
  é per-instância), não introduzido por este plano.
- **Não inventar `conversation.delete`** no RBAC (reuso de `conversation.resolve`) — criar exigiria
  `PERMISSION_CATALOG` + `ROLE_DEFAULTS` + migration de re-seed. (§1/§0)
