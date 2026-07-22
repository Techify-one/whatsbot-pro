# Plano 72 — Corrigir vazamento de conversas em abas filtradas (serverMode em tempo real)

**Status:** F0–F8 **implementados** no worktree `wt-plano72` (branch `plano-72-vazamento-abas`), **sem commit** — validado por `node --test` (conversationRows.test.js) + suíte de endpoints (paridade com `developer`). §10 registra as extensões F6–F8 achadas por auditoria adversarial.
**Origem:** bug de produção reportado pela Atendente (instância Empresa Exemplo) — na aba **Minhas + Abertas** aparecem conversas de **outros atendentes**, e mensagens novas que chegam "invadem" a aba. Reporte adicional (busca por número): conversas que não casam o termo "sobem" no resultado a cada mensagem (coberto por **F6**, §10).
**Regressão de:** plano 69 (F2/F3, commit `58aee24` — lista/contagem server-side).

---

## 1. Sintoma

Operadora na aba **Minhas** (`assignmentTab='mine'`) com o chip **Abertas** (`statusFilter='open'`):

- Conversas atribuídas a **outros atendentes** (ou **não atribuídas**) aparecem na lista dela.
- Cada mensagem nova que chega para uma dessas conversas a (re)insere na aba.
- O **badge de contagem continua correto** (ex.: "Minhas 2") — só a **lista** está poluída. Lista e contagem **divergem**.

## 2. Causa-raiz (confirmada por revisão adversarial)

O plano 69 introduziu o **`serverMode`**: quando o spec inteiro (status + aba + tags + avançado) é 100% expressável no servidor, a **lista** passa a ser servida por `/api/atendimentos/filter` (a MESMA `WHERE` da contagem) e o cliente **para de re-filtrar**:

- [useConversationFilters.js:99](../web/static/js/components/contacts/hooks/useConversationFilters.js#L99) — `if (serverMode) return activeContacts;` (pula status/tags/avançado).
- [useConversationFilters.js:175-177](../web/static/js/components/contacts/hooks/useConversationFilters.js#L175-L177) — em serverMode pula `matchesAssignment` (a aba).

Só que a camada de **tempo real** nunca soube da `WHERE` do servidor. Dois vetores (ambos **blocker**):

- **V1 — `conversation_upsert` insere linha filtro-cega.** [useConversationWsEvents.js:224](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L224) → `upsertConversationRow`. O único gate pré-inserção é o de **arquivo** ([L183-188](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L183-L188)). Nada checa status/atribuição/tags/avançado. Como o `conversation_upsert` é emitido **a cada mensagem visível**, qualquer conversa (de outro atendente, não atribuída, status errado) que recebe mensagem é inserida — e em serverMode **não é re-filtrada**. → **é exatamente o bug reportado.**
- **V2 — `applyConversationEvent` muta status/atribuição in-place sem remover a linha.** [useConversationWsEvents.js:297](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L297). Ao reatribuir a outro atendente / resolver, a linha é **patchada** mas **permanece** na aba (serverMode não re-filtra). A rede de segurança de [L304-312](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L304-L312) só **traz linhas PRA DENTRO** (refetch quando `status` muda e a linha não está presente); nunca remove uma linha presente que saiu da view.

**O servidor está certo.** [conversation_repo.py](../db/repositories/conversation_repo.py) `list_filtered` (L471-488) e `count_tab_counts` (L491-536) compartilham a mesma `WHERE` (`db/filters`) + escopo de inbox; `assignee=me`, `agent=none` (ambos NULL), `has_mention` resolvem server-side ([translate.py](../db/filters/translate.py)). Ou seja, `/api/atendimentos/filter` **não** devolveria conversas de outro atendente — a poluição é **100% do cliente**.

## 3. Por que o conserto "óbvio" (admission gate ingênuo) NÃO basta

A ideia inicial — reaplicar os matchers puros (`matchesStatus`/`matchesAssignment`/`matchesTags`/`matchesAdvFilters`) como um "admission gate" no insert — **introduz NOVAS divergências `lista < contagem`**, porque o payload do `conversation_upsert` é uma projeção **mais fina** que as linhas do servidor. Achados da revisão:

- **A1 — o payload do broadcast NÃO tem `contact_tags`.** [get_row_for_broadcast](../db/repositories/conversation_repo.py#L601) roda `_attach_labels` (etiquetas do **atendimento**) mas **esqueceu** `_attach_contact_tags` (tags do **contato**) — que `list_filtered`/`list_conversations` rodam ([L468](../db/repositories/conversation_repo.py#L468)/[L488](../db/repositories/conversation_repo.py#L488)). Então `convRowToSidebarRow` faz `tags: p.contact_tags || []` → **`[]`**. Um gate com `matchesTags` **removeria** conversas etiquetadas a cada mensagem → lista fica **abaixo** da contagem (bug invertido). *(A própria docstring de `get_row_for_broadcast` promete "byte-for-byte what a refetch would return" — promessa já violada para `contact_tags`.)*
- **A2 — a aba Menções não é carregável por broadcast global.** `has_user_mention` é **por usuário**; `get_row_for_broadcast(conv_id)` chama `get_with_channel` **sem `current_user_id`** → `has_user_mention = literal(False)` para todos ([conversation_query.py](../db/repositories/conversation_query.py)), e o broadcast é **único para todos os clientes** ([message_listeners.py:59](../agent/message_listeners.py#L59)). Além disso `convRowToSidebarRow` nem copia `has_user_mention` (só `has_unread_mention`, sinal diferente). Um gate com `matchesAssignment(...,'mentions')` **removeria toda** conversa mencionada.
- **A3 — status/atribuição do upsert são justamente os campos que o plano-28 D4 trata como STALE.** `upsertConversationRow` de propósito faz merge só de preview/unread (`UPSERT_MSG_FIELDS`) e **não** de status/assignee "so a stale upsert snapshot can never revert an assign/resolve" ([conversationRows.js:560-567](../web/static/js/services/conversationRows.js#L560-L567)). Um gate que **lê** esses campos do upsert para decidir admissão reabre a corrida: mensagem inbound + reatribuição-para-mim quase simultâneas → o upsert pode trazer o snapshot **pré-atribuição** → o gate **descarta minha própria conversa**, e o evento de assign seguinte só patcha linhas **presentes** (não re-insere) → some até um refetch.
- **A4 — `cattr` e `activity` divergem por lógica de 3 valores / coluna / relógio.** `cattr ne`/`does_not_contain` sobre atributo ausente: cliente **admite**, servidor **exclui** (`NULL != v` → NULL → false). `cattr gt/lt`: cliente compara **numérico/data**, servidor compara **string** (`'10' < '9'`). `activity`: cliente usa `last_message_ts`, servidor usa `last_activity_at` + `now` do servidor → divergência de fronteira/skew. (Reachability baixa, mas real em filtro salvo.)

**Conclusão de design:** o cliente **não consegue** reproduzir fielmente a decisão do servidor a partir do payload fino em **todas** as dimensões. O conserto tem que (a) **consertar a paridade na fonte** onde é barato (tags), (b) **gatear só nas dimensões confiáveis** do payload, e (c) **delegar ao servidor (refetch)** as dimensões irreproduzíveis (menções, cattr, activity) — nunca inserir/descartar às cegas.

## 4. Design escolhido

Princípio: **cada decisão usa a fonte autoritativa dela.**

- **`conversation_upsert` = "insert-gate" (só decide INSERÇÃO de linha ausente).** Nunca descarta linha presente (respeita A3/D4).
- **Eventos `conversation_*` dedicados (assign/resolve/status/tags) = "drop-gate" (decidem REMOÇÃO).** É aqui que a membership muda de verdade.
- **Paridade na fonte:** o payload do broadcast passa a carregar `contact_tags` (torna tags uma dimensão confiável no cliente **e** conserta um bug latente do não-serverMode).
- **Fallback por refetch server-filtrado** (`scheduleListRefetch`, que já lê `serverFilterRef` = mesma `WHERE` da contagem) para as dimensões que o payload não decide com fidelidade: **aba Menções, cláusulas `cattr:*`, cláusula `activity`**.
- **Sem exceção "always-admit-open".** Uma conversa aberta que não casa a view (deep-link/busca para a conversa de outro atendente) **não** ganha linha na sidebar — é o que o servidor faz; o painel direito segue aberto via `contactData`. (Manter a exceção reintroduziria o próprio sintoma "conversa de outro invade minha aba" + flicker no refetch.)

Resultado: em serverMode, a lista fica **em paridade exata** com `/api/atendimentos/filter` para tudo que o cliente decide, e o **servidor decide o resto** — **zero divergência visível** ao usuário. Fora de serverMode, comportamento **byte-idêntico** ao atual (o `displayedContacts` client-side continua filtrando o display).

---

## 5. Fases

### F0 — Paridade na fonte: `contact_tags` no payload do broadcast (backend)

**Arquivo:** [db/repositories/conversation_repo.py:612](../db/repositories/conversation_repo.py#L612)

```python
# antes
return _attach_labels([row])[0]
# depois (espelha list_filtered/list_conversations em L468/L488)
return _attach_contact_tags(_attach_labels([row]))[0]
```

- Cumpre a promessa da própria docstring ("byte-for-byte what a refetch would return").
- Torna o **funil de tags** e a cláusula avançada `tag`/`conv_label` **confiáveis** no gate do cliente.
- **Bônus:** conserta um mis-filtro **pré-existente** também no não-serverMode (linhas empurradas por WS já entravam com `tags:[]` e eram mal-avaliadas por `matchesTags`/`clientTabCounts`).
- Custo: 1 query batch a mais por broadcast (`tags_by_contact`, já usada nas listas). Baixo.
- **Teste:** estender um teste de `get_row_for_broadcast` para asserir `contact_tags` presente (ver `tests/endpoints/test_p25_unread_badge_and_ingest.py:307`, que já checa `labels`).

> Alternativa frontend-only (se não quiser tocar backend): tratar `tags` como dimensão **não-confiável** → refetch sempre que houver filtro de tag ativo. Pior UX (refetch por mensagem com filtro de tag, que é comum). **F0 é recomendado.**

### F1 — Helpers puros: `rowMatchesView` + `specNeedsServer` (frontend, testável)

**Arquivo:** [web/static/js/services/conversationRows.js](../web/static/js/services/conversationRows.js)

```js
// Compõe os matchers existentes EXATAMENTE como statusTagFiltered + displayedContacts
// faziam antes do serverMode (inclusive o override: cláusula de status no avançado
// tem precedência sobre o chip). `now` em SEGUNDOS (= Date.now()/1000).
export function rowMatchesView(c, spec, now) {
  const { statusFilter, assignmentTab, tagFilter, advFilters, currentUserId } = spec;
  const hasStatusClause = (advFilters || []).some(
    cl => cl.dim === 'status' && cl.value !== '' && cl.value != null);
  if (!hasStatusClause && !matchesStatus(c, statusFilter)) return false;
  if (!matchesTags(c, tagFilter)) return false;
  if (!matchesAdvFilters(c, advFilters, now)) return false;
  if (!matchesAssignment(c, assignmentTab, currentUserId)) return false;
  return true;
}

// Dimensões que o payload do conversation_upsert NÃO decide com fidelidade →
// delegar ao servidor via refetch. (Após F0, `tag` é confiável.)
export function specNeedsServer(spec) {
  if (spec.assignmentTab === 'mentions') return true;              // A2: por-usuário, broadcast global
  return (spec.advFilters || []).some(cl =>
    (typeof cl.dim === 'string' && cl.dim.startsWith('cattr:'))    // A4: 3-valued / lexical
    || cl.dim === 'activity');                                     // A4: last_message_ts vs last_activity_at + skew
}
```

- Também mapear `has_user_mention` em `convRowToSidebarRow` (por completude do display; ciente de que no broadcast global vem `false` — por isso menções continua dimensão de servidor).
- **Testes** em [conversationRows.test.js](../web/static/js/services/conversationRows.test.js): `rowMatchesView` por dimensão (mine/unassigned, status, override de status-clause, tag após F0, channel/agent/ai/starter/contact_type) + `specNeedsServer` (mentions/cattr/activity → `true`; mine+open puro → `false`).

### F2 — Wiring: `viewSpecRef` (frontend)

- Criar `const viewSpecRef = useRef(null)` em [Contacts.js](../web/static/js/components/contacts/Contacts.js) (junto de `serverFilterRef`).
- [useConversationFilters.js](../web/static/js/components/contacts/hooks/useConversationFilters.js): receber `viewSpecRef` e escrevê-lo **em render** (mesmo padrão do `serverFilterRef`, L74-80):
  ```js
  if (viewSpecRef) viewSpecRef.current = {
    serverMode, statusFilter, assignmentTab, tagFilter, advFilters, currentUserId,
  };
  ```
- [useConversationWsEvents.js](../web/static/js/components/contacts/hooks/useConversationWsEvents.js): receber `viewSpecRef` (os handlers são `useCallback([],)` e leem **refs**, nunca state — por isso um ref, não valor).

### F3 — Insert-gate no `conversation_upsert` (frontend)

**Arquivo:** [useConversationWsEvents.js:175-225](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L175-L225), logo **após** o guard de arquivo (L183-188):

```js
const vs = viewSpecRef.current;
if (vs && vs.serverMode) {
  const present = contactsRef.current.some(c => c.conversation_id === row.conversation_id);
  if (!present) {
    // Linha AUSENTE: decidir inserção.
    if (specNeedsServer(vs)) { scheduleListRefetch(); return; }        // servidor decide
    if (!rowMatchesView(row, vs, Date.now() / 1000)) return;           // forasteira → não insere (o BUG)
    // casa → cai no upsert normal abaixo
  }
  // Linha PRESENTE: NUNCA descartar aqui (A3/D4). Segue no merge normal (preview/unread).
}
setContacts(prev => upsertConversationRow(prev, row));
return;
```

- Não há exceção para a conversa aberta (decisão da §4).
- Custo do gate: `O(advFilters)` por evento — trivial. `scheduleListRefetch` (250ms) só dispara quando um filtro de servidor está ativo (menções/cattr/activity) — raro.

### F4 — Drop-gate nos eventos de membership (frontend)

Onde uma linha **presente** legitimamente muda de membership. Em serverMode:

- **`applyConversationEvent`** ([L297](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L297) — assign/resolve/status/ai/labels/attr):
  ```js
  setContacts(prev => {
    let next = applyConversationEvent(prev, data);
    if (vs && vs.serverMode) {
      if (specNeedsServer(vs)) scheduleListRefetch();
      else {
        const now = Date.now() / 1000;
        next = next.filter(c => !eventTargetsRow(c, data) || rowMatchesView(c, vs, now));
      }
    }
    return next;
  });
  ```
  A rede de segurança existente (L304-312, traz linhas PRA DENTRO) permanece; o filtro acima remove as que **saíram**.
- **`contactTagsUpdated`** ([L420-430](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L420-L430) — achado A1/finding #3): após atualizar `tags`, se houver filtro de tag ativo e a linha não casar mais → remover (após F0, `tag` é confiável, então `rowMatchesView` decide; senão `scheduleListRefetch`).

### F5 — Paths otimistas de ação/bulk (frontend) — **secundário (important, não blocker)**

`handleToggleAI` ([useConversationActions.js:57](../web/static/js/components/contacts/hooks/useConversationActions.js#L57)), `handleBulkAI`/`handleBulkAssign` ([useBulkSelection.js](../web/static/js/components/contacts/hooks/useBulkSelection.js)) patcham `assignee_user_id`/`active_agent_key`/`conv_ai_active` otimisticamente e, em serverMode, deixam linhas stale na aba. Como são **iniciados pelo operador** e já fazem POST ao backend, o mais simples é **agendar um refetch server-filtrado** após o patch otimista quando em serverMode (o servidor reconcilia a membership). Requer expor `serverMode` + `fetchContacts` (ou um callback `reconcileAfterMembershipChange`) a esses hooks.

*(Fora de escopo do bug passivo reportado, mas mesma classe — fechar junto evita reincidência.)*

---

## 6. Invariantes preservadas

- **Plano 69 (D4 — "nunca metade server, metade client"):** a contagem segue server-side; a lista fica em paridade com a `WHERE` do servidor. O conserto **reforça** a invariante "lista = contagem", não a reverte — F0..F4 são **aditivos** ao trabalho do plano 69 (não tocam a lógica de `serverMode`/`buildListParams`/count).
- **Plano 28 (D4 — snapshot do upsert é stale p/ status/assignee):** o insert-gate decide **só inserção de linha ausente**; nunca descarta linha presente com base no snapshot do upsert. A remoção vem dos eventos dedicados (F4).
- **Não-serverMode:** **byte-idêntico** — o gate só roda quando `vs.serverMode`; o `displayedContacts` client-side continua filtrando o display.

## 7. Riscos e limitações conhecidas (todas neutralizadas)

| Risco | Mitigação |
|---|---|
| A1 tags ausentes no payload | **F0** (backend) — paridade na fonte |
| A2 menções não carregáveis por broadcast global | `specNeedsServer` → **refetch** (servidor decide) |
| A3 corrida assign+mensagem revertendo a própria conversa | insert-gate só decide **ausente→inserir**; nunca descarta presente pelo upsert |
| A4 `cattr ne/gt`, `activity` (3-valued/lexical/skew) | `specNeedsServer` → **refetch** |
| `active_agent_key = ''` (vs `IS NULL`) | não ocorre hoje (coluna só NULL ou key real); `rowMatchesView` herda a mesma coalescência dos matchers atuais |
| Frequência de refetch com filtro de menções/cattr/activity ativo | `scheduleListRefetch` é **debounced (250ms)** e coalesce rajadas; esses filtros são de nicho |

## 8. Ordem de rollout

1. **F0** (backend) — deploy primeiro (aditivo; melhora paridade mesmo sem o resto).
2. **F1 + F2 + F3 + F4** (frontend) — o conserto do bug passivo reportado.
3. **F5** (frontend, secundário) — paths otimistas de ação/bulk.

Sem migration. Sem mudança de contrato de API. F0 é o único toque no backend e é uma linha.

## 9. Critérios de aceite

- Em **Minhas + Abertas** (serverMode), uma mensagem inbound para uma conversa **de outro atendente**/**não atribuída** **não** aparece na lista; a contagem permanece igual à lista.
- Reatribuir/resolver a **minha** conversa (ou por outro cliente) a **remove** da minha aba ao vivo.
- Com **funil de tag** ativo: uma conversa que **perde** a tag sai da lista ao vivo; nenhuma conversa etiquetada some indevidamente a cada mensagem (regressão que o gate ingênuo causaria — coberta por F0).
- Aba **Menções**, filtros **`cattr:*`** e **`activity`**: lista converge com o servidor (via refetch), sem sumiço/duplicação persistente.
- **Busca** e **não-serverMode**: comportamento inalterado. *(Atualizado por F6 — ver §10: a BUSCA passou a ser gateada também.)*
- Suíte `node --test` (conversationRows.test.js) verde; teste backend de `get_row_for_broadcast` com `contact_tags`.

---

## 10. Extensões pós-implementação (auditoria adversarial — F6–F8)

Depois de F0–F5, uma **auditoria adversarial** (workflow de 8 mapeadores + verificação que tentou *refutar* cada candidato) varreu **todos** os pontos que mutam a lista da sidebar (`setContacts`) + o backend + o plugin de atendimentos, à procura do MESMO padrão de bug em outros lugares. Resultado: **42 achados, 38 sítios explicitamente limpos, 1 candidato refutado (auto-cura), 3 vazamentos confirmados** — todos no ciclo de vida da **aba Menções**. Além disso o reporte de campo sobre **busca por número** revelou um vetor não coberto (F6).

### F6 — Insert-gate no modo BUSCA (frontend) — **IMPLEMENTADO**

**Causa:** a §6 prometia "fora de serverMode, byte-idêntico". Mas **a busca desliga o serverMode** (`serverMode` retorna `false` quando `searching` — [useConversationFilters.js:63](../web/static/js/components/contacts/hooks/useConversationFilters.js#L63)), e a lista da busca é filtrada **só pelo backend** (pelo termo: nome/telefone/conteúdo), sem re-filtro no cliente. Logo o `conversation_upsert` de qualquer conversa que **não casa o termo** era inserido às cegas → "subia" no resultado a cada mensagem (o sintoma reportado).

**Fix:** o insert-gate do F3 passou a valer também quando `searchRef.current` está setado ([useConversationWsEvents.js](../web/static/js/components/contacts/hooks/useConversationWsEvents.js), branch `conversation_upsert`): em busca, **linha AUSENTE não é inserida** (o cliente não reproduz a busca do servidor); **linha PRESENTE** segue no merge (preview/unread ao vivo).
- **Decisão:** durante a busca, ausente → **skip** (não `scheduleListRefetch`), para **não resetar a rolagem** das páginas de busca já carregadas (plano 62 F6). Custo mínimo: uma conversa *totalmente nova* que passe a casar o termo aparece só na próxima busca/refresh — irrelevante para busca por número (as conversas do número já estão presentes e atualizam ao vivo). Se preferir "aparecer na hora" (com reset de rolagem), trocar o `return` por `scheduleListRefetch()`.

### F7 — Menções: insert-gate no `mention_created` (frontend) — **IMPLEMENTADO** — *confirmed (important)*

**Causa:** na aba **Menções** em serverMode a lista vem server-filtrada por `has_mention=true`. Quando chega uma **nova menção** para uma conversa **ausente** da lista, o handler `mention_created` ([useConversationWsEvents.js](../web/static/js/components/contacts/hooks/useConversationWsEvents.js)) só faz `prev.map` (patcha `has_user_mention` em linhas **presentes**) — não insere a ausente. E **não há `conversation_upsert`** para nota privada (papel painel-only ⇒ `broadcast_conversation_upsert` retorna cedo; o force-emit só roda com `notify_private_messages` ON, default OFF), então o F3 nunca dispara. **Sintoma:** o operador ouve o toast "você foi mencionado" mas **a menção fica invisível na própria aba Menções** até trocar de aba/F5 (lista N × badge N+1 — a contagem reconcilia, a lista não).

**Fix:** no branch `mention_created`, após confirmar que EU fui mencionado, se `viewSpecRef.serverMode && assignmentTab==='mentions'` **e a conversa está ausente**, chamar `scheduleListRefetch()` (menção é dimensão de servidor). O `prev.map` (presentes) e o toast/som seguem inalterados; em qualquer outra aba/busca o handler continua no-op.

### F8 — Menções: drop-gate ao LER a menção (frontend) — **IMPLEMENTADO** — *confirmed (minor→important)*

**Causa:** na aba **Menções** em serverMode, **ler** a menção da conversa aberta a tira da view do servidor (`has_mention` → false), mas o **clear otimista** do badge só zera o flag `has_user_mention` **sem remover a linha** — e o serverMode não re-filtra no cliente. **Ler a menção não emite nenhum evento WS** (o backend só grava `mentions.read_at`), então nada auto-cura. A linha fica **presa** na aba (lista N × badge N-1; lendo todas → lista N, badge 0). Dois sítios de leitura: o **primário** [useConversationSelection.js](../web/static/js/components/contacts/hooks/useConversationSelection.js) (abrir a conversa com a aba visível) e o **secundário** [useConversationWsEvents.js:155](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L155) (`visibilitychange` — deep-link em aba de fundo).

**Fix:** refetch server-filtrado quando `serverMode && assignmentTab==='mentions'`, nos **dois** sítios:
- `visibilitychange` (ws-hook) — `scheduleListRefetch()` inline (já tem `viewSpecRef`/`scheduleListRefetch` em escopo).
- selection-hook — via callback `reconcileMentionsOnRead` ([Contacts.js](../web/static/js/components/contacts/Contacts.js), `fetchContacts(searchRef.current)` escopado à aba Menções) passado ao hook, chamado logo após o clear do badge. Escopado à aba Menções: **no-op** em qualquer outra view (não refaz a lista a cada conversa aberta).

### Auditados e LIMPOS (não são vazamento — não mexer)

- **Otimista de etiqueta** — `applyTagResults` / `handleBulkTag` / `handleBulkRemoveAllTags` / o patch do menu de contexto / o `onSave` do painel do contato **NÃO** dropam a linha que sai de um funil de tag, MAS **auto-curam**: o `PUT /api/contacts/{phone}/tags` faz `broadcast("contact_tags_updated", …)` ([tags.py](../server/routes/tags.py)); como a mutação chega por **HTTP** (não por frame WS), o broadcast **não tem sender a excluir** → o cliente originador **recebe o echo** → cai no **drop-gate do F4** (`contactTagsUpdated`). *(Refutado pela verificação adversarial — não precisa de step.)*
- **Paginação** (`loadMore`/`loadSearchPage`/`fetchContacts`) — sempre server-filtrada (serverMode via `filterConversations`; busca via `getContacts(q)+listConversations(contact_ids)`). Não anexa linha forasteira.
- **Nova conversa** (`openInChannel`) — limpa a busca + `fetchContacts` (refetch server-filtrado); **não** força linha na sidebar (só abre a thread). Deep-link para conversa fora da view idem (abre `contactData`, sem inserir linha).
- **Handlers patch-only** (`contact_info_updated` nome, `contact_ai_toggled` `ai_enabled`, `message_status`, `avatar_updated`, `message_reaction`, `conversation_pinned`) — campos que **não** são dimensão de filtro serverMode; `new_message` é append-only na thread (plano 28). **Drops** (`archive`/`delete`/`delete_conversation`) e o ctx-menu **assign/resolve** (só mexem em `ctxConv`; a sidebar reconcilia pelo echo WS `conversation_*` → F4) — OK.
- **Plugin `protocolos`** (override da rota `attendances`) — auditado: **não** mantém uma lista própria de conversas em tempo real que vaze pelo filtro; sem step de core.

### Rollout atualizado

1. **F0** (backend) — 1ª (aditivo).
2. **F1–F4** (frontend) — conserto do bug serverMode reportado.
3. **F6** (frontend) — gate da BUSCA.
4. **F7 + F8** (frontend) — ciclo da aba Menções (insert + read-drop).
5. **F5** (frontend, secundário) — paths otimistas de ação/bulk.

### Critérios de aceite adicionais (F6–F8)

- **Busca por número:** com a busca ativa, mensagens de conversas que **não casam** o termo **não** entram na lista (não "sobem no filtro"); as que casam atualizam preview/não-lidas ao vivo.
- **Aba Menções — nova menção:** uma menção recém-chegada para uma conversa fora da lista **materializa** na aba ao vivo (via refetch); lista = badge.
- **Aba Menções — leitura:** ao ler (abrir) a menção, a conversa **sai** da aba Menções ao vivo; lista = badge (não fica linha "presa" sem o chip @).
