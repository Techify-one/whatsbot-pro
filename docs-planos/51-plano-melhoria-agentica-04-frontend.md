# Plano 51 - Frontend: multi-seleção de mensagens e chat interativo de aprovação (sub-plano 04)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-16 · **Escopo:** grande (1 costura no core + reescrita do frontend do plugin `melhorias`)
> **Origem:** Plano 51 "Melhoria agêntica" — evoluir o "Gerar melhoria" (hoje análise one-shot atrás de um painel de aprovação) para um **fluxo agêntico**: operador seleciona MÚLTIPLAS mensagens → painel de aprovação com **chat interativo** contra o executor Claude Code externo, com dois gates humanos (aprovar p/ iniciar; V/X por mutação).
> **Método:** mapeamento `arquivo:linha` verificado do core (`ContactDetail.js`, `useMessageActions.js`, `registry.js`, `MessageBubble.js`, `MediaContent.js`, `plugins/api.js`, `plugins/ModalHost.js`, `components/shell/App.js`) e do plugin (`assets/plugin_examples/melhorias/static/{extends.js,panel.js}`); contrato SSE/HMAC/relogin portado do ai-server do nexus (`use-ai-chat.ts`, `ai-chat-relogin-modal.tsx`, `ai-chat-approval.tsx`, `types.ts` — ver relatório `nexus-protocolo.md` §2/§5/§E).
> Este sub-plano cobre **só o frontend**: a multi-seleção no core (o seam nasce aqui), a UI do plugin (dialog multi-mensagem, painel-chat, cards de streaming/tool/aprovação, modal de relogin, imagens) e o tema escuro. O **gateway/backend** (endpoints `/suggestions/{sid}/start|stream|messages|approve-tool`, relogin, HMAC, persistência de conversa) é dos sub-planos de backend; aqui ele é **consumido** como contrato.
> **Como usar este plano:** ao executar cada fase, preencha o Status de execução dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões travadas (herdadas do mestre — não reabrir)

| # | Decisão | Consequência no frontend |
|---|---------|--------------------------|
| **D1** | Entrada intacta: operador seleciona **múltiplas** mensagens no botão-direito → painel de aprovação. Dois gates humanos: (a) aprovar p/ a IA começar (+observação); (b) cada mutação exige V/X. | Fases 1–4. O único ponto de entrada continua sendo o menu de contexto; a multi-seleção é a novidade. |
| **D2** | Executor = servidor CLAUDE-CODE-AUTOMAÇÕES (`203.0.113.10:64777`), pasta nova da aplicação WhatsBot. | Frontend não fala com o executor — só com o gateway do plugin (`/api/plugins/melhorias/...`). |
| **D5** | Padrão de referência = ai-server do nexus (HMAC + **SSE** + aprovação humana + resume + relogin OAuth). | Fase 4 (SSE via `fetch`+`ReadableStream`), Fase 5 (relogin). **Não** usar `EventSource` (ver Falsos positivos). |
| **D6** | A feature vive no plugin `melhorias` EVOLUÍDO. Fonte git em `assets/plugin_examples/melhorias/`; cópia instalada é gitignored; **mudanças chegam via `.zip` re-importado**. | Toda edição de plugin abaixo é na fonte `assets/plugin_examples/melhorias/`; validar exige re-empacotar/re-importar o `.zip`. |

**Travas específicas do frontend (deste sub-plano):**

| # | Trava | Motivo |
|---|-------|--------|
| **F1** | O seam single-message existente (`filter.message.contextMenu.items`) **NÃO muda de assinatura** — a multi-seleção nasce como **seam NOVO** (`filter.selection.batchActions`). | Não quebrar o menu single (usado hoje por `melhorias` e potenciais terceiros). |
| **F2** | Consumo do stream = `fetch` + `ReadableStream` + parser `\n\n`/`event:`/`data:`, com `Authorization: Bearer` do `localStorage`. | `EventSource` não envia header `Authorization` (auth escopada) — mesmo motivo do nexus. |
| **F3** | Todo overlay/tela nova usa `wa-*`/`.wa-field` e é testado com `.dark` ligado. | Regra do CLAUDE.md; o plugin usa o mesmo runtime Tailwind + `custom.css`. |
| **F4** | O painel-chat vive na screen `config:false` `/melhorias` (`panel.js`); a lista de pendências (já existe) ganha um modo detalhe-chat, **sem** virar screen nova. | Mantém a rota e as permissões (`view`/`approve`) já registradas no `plugin.yaml`. |

---

## 1. O que é CORE vs PLUGIN

| Camada | Arquivo | Muda? | Natureza |
|--------|---------|-------|----------|
| **CORE** (frontend compartilhado) | `web/static/js/components/contacts/ContactDetail.js` | SIM | Modo de seleção + barra de ação em lote + novo seam. **Toca fluxo crítico compartilhado → caracterização.** |
| **CORE** | `web/static/js/components/contacts/hooks/useMessageActions.js` | SIM | Estado `selection:Set` + toggle de modo. |
| **CORE** | `web/static/js/components/contacts/MessageBubble.js` | SIM | Checkbox/realce por bolha em modo de seleção. |
| **CORE** | `web/static/js/plugins/registry.js` | SIM (doc) | Documentar o novo seam `filter.selection.batchActions`. |
| **CORE** | `web/static/js/plugins/api.js` | NÃO | `api.http`/`api.ui.openModal`/`api.services` já suficientes. |
| **PLUGIN** | `assets/plugin_examples/melhorias/static/extends.js` | SIM | Item "Gerar melhoria" no modo lote; dialog multi-mensagem; submit `messages:[...]`. |
| **PLUGIN** | `assets/plugin_examples/melhorias/static/panel.js` | SIM | Painel-chat (gate a, SSE, cards, gate b), estado idle/starting/streaming/awaiting-approval. |
| **PLUGIN** | `assets/plugin_examples/melhorias/static/chat.js` *(novo)* | CRIAR | Hook de consumo SSE + parser + máquina de estados (porta de `use-ai-chat.ts`). |
| **PLUGIN** | `assets/plugin_examples/melhorias/static/relogin.js` *(novo)* | CRIAR | Modal de relogin OAuth (porta de `ai-chat-relogin-modal.tsx`). |
| **BACKEND** (outros sub-planos) | gateway `/api/plugins/melhorias/...` | — | Consumido como contrato (ver §2, tabela de endpoints). |

---

## 2. Como funciona hoje (mapa — verificado `arquivo:linha`)

### Core — menu de contexto e render de mensagens

| Costura | Onde | Observação |
|---------|------|------------|
| Itens-base do menu | `ContactDetail.js:238-255` (`buildBaseItems`) | Responder/Copiar/Copiar link/Apagar, por **1** `message`. |
| Abertura do menu (single) | `ContactDetail.js:261-274` (`openMsgMenu`) | `applyFilter('filter.message.contextMenu.items', base, { message, isFromMe, phone, conversationId, sandbox })` em `:269-270`. Passa **UMA** `message`. |
| Estado do menu | `useMessageActions.js:33-54` | `const [msgMenu, setMsgMenu] = useState(null)` (`:37`); `{x, y, message, isFromMe, items}` — single. Retorna em `:133-137`. |
| Render das mensagens | `ContactDetail.js:338-368` | `messages.map`; `MessageBubble` em `:361-366`, `SystemMessageCard` em `:356-358`. |
| Bolha | `MessageBubble.js:53-70` | `data-mid=${m._id}` (`:54`); `onContextMenu=${(e)=>openMsgMenu(e,m,isFromMe)}` (`:56`); botão hover ⋁ (`:62-70`). Chave = `m._localId || i`. |
| Render do menu | `ContactDetail.js:393-405` | `MessageContextMenu` com `items=${actions.msgMenu.items || []}`. |
| Contrato do seam | `registry.js:36-41` (comentário) | `filter.message.contextMenu.items` documentado como **single** `{message}`. `addFilter` `:81`, `applyFilter` `:97-111` (`null` aborta a cadeia). |

⚠️ **Gotchas do core:**
- ⚠️ O core **não tem modo de seleção de mensagens** — só o hover-menu por bolha. O único precedente de "modo de seleção + barra em lote + `Set`" é `hooks/useBulkSelection.js`, mas ele é **por CONVERSA** (linhas da sidebar, keyed por `rowKeyFor`), não por mensagem dentro do chat — serve de molde, **não** de reuso (ver Falsos positivos).
- ⚠️ `applyFilter` retorna `null` ⇒ **aborta** a cadeia inteira (`registry.js:104`). O item de melhoria hoje nunca retorna `null` de propósito (`extends.js:44` sempre devolve `items`). O seam novo precisa da mesma disciplina.
- ⚠️ A chave de seleção precisa ser estável: respostas da IA salvas têm `m._id` (db id); otimistas têm `m._localId`. Usar `_id ?? _localId`.

### Plugin — dialog e painel de aprovação (single, síncrono)

| Costura | Onde | Observação |
|---------|------|------------|
| Item no menu | `extends.js:40-50` | Registra `filter.message.contextMenu.items`; só em resposta da IA (`m.role==='assistant' && m.status!=='operator' && !m.revoked && !ctx.sandbox`) com permissão `request`. Abre `ImproveDialog` via `api.ui.openModal` (`:47-49`). |
| Dialog | `extends.js:63-112` | Mostra **1** resposta (`ctx.message`); submit `POST /suggestions` com `message:{content,ts,_id}` **singular** (`:73-78`) + `feedback` + `conversation_id` + `phone`. |
| Painel (lista) | `panel.js:126-382` | Tabela filtrável de pendências; `load()` em `GET /suggestions?limit=100` (`:142-150`); recarrega no WS `plugin_melhorias_changed` (`:171-179`). |
| Aprovar (síncrono) | `panel.js:258-273` (`doDecide`) | `POST /suggestions/{id}/approve` bloqueia; overlay `GeneratingModal` (`:380`, `:386-397`) — "a resposta já traz a análise". |
| Detalhe | `panel.js:399-431` (`DetailModal`) | Campos read-only; a `analysis` markdown aparece como texto num `<pre>`. |

### Plugin — infra de extensão disponível

| Recurso | Onde | Uso no chat |
|---------|------|-------------|
| Modal host (await) | `plugins/ModalHost.js` (`openModal`), montado 1× em `App.js:457` (`PluginModalHost`) | `api.ui.openModal((close)=>vnode)` — dialog multi-mensagem. |
| HTTP escopado | `plugins/api.js:122-150` (`buildPluginHttp`) | `api.http.post('/suggestions', …)`; **path `/api/…` absoluto passa AS-IS** (`:125`) — dá pra bater qualquer rota do plugin. `authHeaders()` embute `Bearer`. |
| Superfície do `api` | `api.js:161-198` | `http` (`:177`), `ui:{openModal,notifyPermissionDenied}` (`:180`), `services:{hasPermission,…}` (`:192-196`). |
| Loader de extends | `App.js:49-67` | `import(p.frontend_extends)` → `register(buildPluginApi(id))` a cada (re)fetch do manifest. |
| Mídia/imagem | `MediaContent.js:45-56` | Imagem = `<img src=${m.media_path}>` com prefixo `/` quando não é blob local (`:51`); URL de statics = `'/' + m.media_path`. |
| Upload de imagem (molde) | `hooks/useMediaUpload.js:77` (`URL.createObjectURL`), `Composer.js:57` (`accept="image/*"`) | Molde do `<input type=file>` + preview p/ anexar imagem no input do chat. |

### Contrato de endpoints que o frontend vai CONSUMIR (definidos nos sub-planos de backend)

| Fase | Método/Path (sob `/api/plugins/melhorias`) | Papel |
|------|--------------------------------------------|-------|
| 2 | `POST /suggestions` `{phone, messages:[{content,ts,_id}], feedback?, conversation_id?}` | Criar pedido multi-mensagem (hoje `message` singular). |
| 3 | `POST /suggestions/{sid}/start` `{observation?}` → `{ok, data:{conversation_id}}` | **Gate (a)**: humano libera a IA + injeta observação. |
| 4 | `GET /suggestions/{sid}/stream` (SSE, `Accept: text/event-stream`) | Fluxo de eventos do executor (via gateway). |
| 4/6 | `POST /suggestions/{sid}/messages` `{parts:[{type:'text'|'image',…}]}` | Mensagem do humano no chat (texto/imagem). |
| 4 | `POST /suggestions/{sid}/approve-tool` `{approvalId, approved, reason?}` | **Gate (b)**: V/X por mutação. |
| 5 | `POST /admin/relogin/{start,complete,abort}` | Relogin OAuth do executor. |

---

## 3. Falsos positivos descartados

| "Parece que serve / é problema" | Por que NÃO |
|---------------------------------|-------------|
| Reusar `EventSource` p/ o SSE | ❌ Não envia header `Authorization` — a auth do WhatsBot é `Bearer` no `localStorage`; escopo restrito exige `fetch`+`ReadableStream` (F2, igual ao nexus `use-ai-chat.ts:186-248`). |
| Reusar `useBulkSelection.js` p/ selecionar mensagens | ❌ É seleção **por conversa** (linhas da sidebar, `rowKeyFor`); não conhece bolhas nem `_id` de mensagem. Serve de **molde de UX** (modo + `Set` + barra), não de código. |
| Ampliar `filter.message.contextMenu.items` p/ `{messages}` | ❌ Quebraria o contrato single já documentado (`registry.js:36-41`) e usado. Criar seam NOVO `filter.selection.batchActions` (F1). |
| Reusar o `/ws` do painel p/ os eventos do chat | ⚠️ **REABERTO por P2 (mestre §8)** — o sub-plano 02 §3 recomenda JUSTAMENTE reusar o `/ws` (o painel já o mantém; filtra por `conversation_id`), evitando 2ª conexão e buffering de SSE atrás de Coolify. NÃO é falso-positivo no v1; é a opção recomendada. O `plugin_melhorias_changed` continua no `/ws` só p/ recarregar a **lista**. A SSE segue no hop executor→gateway em qualquer caso (D5). |
| Reusar o overlay `GeneratingModal` (`panel.js:386-397`) | ❌ Ele assume geração **síncrona** ("fecha ao concluir"). O fluxo agêntico é streaming + gates; o overlay bloqueante some, dá lugar à máquina de estados. |
| Postar a mensagem de imagem re-uploadando o arquivo | ❌ Imagens SELECIONADAS já estão em disco (`media_path` sob `statics/`); o gateway lê o arquivo. O upload manual (Fase 6) é só p/ imagens NOVAS coladas no input. |

---

## 4. Fases

### Fase 1 — Multi-seleção no CORE (o seam nasce) 🔴
**[toca-core · caracterização] [bloqueia: 2, 6]**

**Objetivo:** dar ao chat um modo de seleção de mensagens (checkbox/long-press por bolha) + barra de ação em lote, e expor um seam novo `filter.selection.batchActions`, sem alterar o menu single.

**Itens:**
- [sequencial] Caracterizar o comportamento atual do menu single ANTES de mexer: abrir menu numa resposta da IA, confirmar item "Gerar melhoria" (plugin ativo) e itens-base. Registrar como baseline manual (não há teste JS do menu hoje).
- [sequencial] `useMessageActions.js:33-54` — adicionar estado `selectionMode:boolean` + `selection:Set` (chaves `m._id ?? m._localId`) + `toggleSelect(key)`, `clearSelection()`, `enterSelection(firstMsg)`; expor no retorno (`:133-137`). Manter `msgMenu` intacto.
- [paralelo] `MessageBubble.js:53-70` — quando `selectionMode`: renderizar checkbox (ou realce da bolha via `ring`/`bg-wa-teal/10`) e trocar o `onClick` da bolha para `toggleSelect`; **desabilitar** `onContextMenu`/botão-⋁ nesse modo. Passar `selectionMode`/`selected`/`onToggleSelect` como props novos (mantendo o componente presentacional).
- [sequencial] `ContactDetail.js:238-255` — no `buildBaseItems`, acrescentar um item "Selecionar mensagens" (só quando há ≥1 item de lote registrado no seam novo) que chama `enterSelection(message)`.
- [sequencial] `ContactDetail.js` (novo bloco, perto de `:369`) — **barra de ação em lote** (aparece quando `selectionMode`): contador "N selecionadas", botão "Cancelar" (`clearSelection`), e os itens vindos de `await applyFilter('filter.selection.batchActions', [], { messages: selectedMessages, phone, conversationId, sandbox })`. `messages` = as mensagens completas (`{content, ts, _id, role, media_type, media_path,…}`) resolvidas do `Set`.
- [sequencial] `registry.js:36-47` — documentar o seam novo no bloco de contratos: `filter.selection.batchActions` — value = array de `{label, icon, onClick, disabled?}`; ctx `{messages, phone, conversationId, sandbox}`; `null` aborta (cai em array vazio → barra sem ações de plugin).
- [paralelo] Extrair o parser/normalização em módulo puro se surgir lógica testável; caso contrário, deixar em componentes.

**Pronto quando:** com o plugin `melhorias` DESATIVADO, o botão-direito e o chat ficam **byte-idênticos** ao baseline (nenhuma barra, nenhum checkbox — array de lote vazio). Ativando um filtro dummy de teste em `filter.selection.batchActions`, "Selecionar mensagens" aparece, entra no modo, marca 3 bolhas (o contador vira "3 selecionadas"), "Cancelar" limpa. Modo escuro (`.dark`) legível. `venv/bin/python -m pytest tests/ -q` verde (o core não tem lógica de servidor nova; garante que nada quebrou no boot do SPA servido).

```
#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-07-16, commit f8eff2b)
- **O que foi feito:** useMessageActions ganhou selectionMode/selection(Set)/enterSelection/toggleSelect/clearSelection + selectionKey exportado (_id ?? _localId); MessageBubble com checkbox/realce (props presentacionais); ContactDetail com item "Selecionar mensagens" (só quando há filtro de lote registrado), barra de lote (contador+Cancelar+itens do applyFilter async→estado), limpeza ao trocar de conversa; seam documentado em registry.js. ctx do seam inclui clearSelection.
- **Como foi feito / decisoes:** F1 respeitada (contextMenu single intacto — seam NOVO). Baseline: sem plugin registrado, getFilters()=[] ⇒ item não existe e o render interpola strings vazias (byte-idêntico).
- **Problemas / pendencias:** caracterização é manual (não há teste JS do menu) — validação visual em claro/escuro pendente de uso real.
- **Verificacao:** node --input-type=module --check nos 4 arquivos; tests/frontend/check_imports.mjs (356 imports) verde; suíte pytest verde.
```

---

### Fase 2 — Multi-seleção no PLUGIN (dialog) 🟢
**[depende de: 1; backend `POST /suggestions` aceitar `messages:[]`] [bloqueia: 3]**

**Objetivo:** o item "Gerar melhoria" passa a existir também no modo lote e o `ImproveDialog` submete N respostas.

**Itens:**
- [sequencial] `extends.js:31-58` — no `register(api)`, além do `addFilter('filter.message.contextMenu.items', …)` existente (`:40-50`, **mantido** p/ o caso 1 mensagem), registrar `api.addFilter('filter.selection.batchActions', …)`: se todas as `ctx.messages` selecionadas forem respostas da IA elegíveis e `can('request')`, acrescentar o item "Gerar melhoria (N)" que abre `ImproveDialog` com a lista.
- [sequencial] `extends.js:63-112` (`ImproveDialog`) — aceitar `messages:[]` (fallback: `[ctx.message]` p/ o caminho single). Renderizar a **lista** das respostas marcadas (cada uma num bloco `bg-wa-bg border`, com scroll) em vez de uma só.
- [sequencial] `extends.js:69-83` (`submit`) — trocar `message:{…}` singular por `messages: messages.map(m => ({ content:m.content, ts:m.ts, _id:m._id }))`; manter `feedback`, `conversation_id`, `phone`. O `feedback` (observação) continua um textarea único aplicável ao conjunto.
- [paralelo] Ao sucesso, `onClose(true)` e limpar a seleção do chat (via callback exposto no ctx do seam, ou deixar o core limpar ao fechar o modal).

**Pronto quando:** seleciono 2 respostas da IA → barra mostra "Gerar melhoria (2)" → dialog lista as duas + textarea → "Enviar ao painel" faz `POST /suggestions` com `messages:[{…},{…}]` (verificável no Network) → aviso de sistema com CTA aparece na conversa (WS `new_message`). O caminho single (1 mensagem via `filter.message.contextMenu.items`) continua funcionando. Re-empacotar o `.zip` e re-importar (D6) reflete a mudança.

```
#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-07-16, commit 0a1fdab)
- **O que foi feito:** extends.js registra filter.selection.batchActions ("Gerar melhoria (N)", só quando TODAS as selecionadas são respostas da IA elegíveis + can(request)); ImproveDialog aceita messages:[] com lista rolável e envia messages:[{content,ts,_id,media_type,media_path}] (payload singular preservado p/ 1 msg); sucesso limpa a seleção via ctx.clearSelection.
- **Como foi feito / decisoes:** media_type/media_path incluídos no payload (habilita auto-incluir imagens selecionadas — F6).
- **Problemas / pendencias:** nenhum.
- **Verificacao:** backend cobre messages:[] (teste de gateway); sintaxe/zip ok.
```

---

### Fase 3 — Painel de aprovação com chat interativo + gate (a) 🔴
**[depende de: 2; backend `POST /suggestions/{sid}/start`] [bloqueia: 4]**

**Objetivo:** ao abrir um item pendente no painel, mostrar um **painel de chat**; antes de a IA rodar, o humano aprova ("Aprovar p/ iniciar") e injeta uma observação extra; só então o stream abre.

**Itens:**
- [sequencial] `panel.js:399-431` (`DetailModal`) — evoluir p/ um layout com duas zonas: cabeçalho (metadados da sugestão, mensagens-alvo) + **área de chat**. Manter os campos read-only quando `status !== 'pendente'`/já concluída.
- [sequencial] `panel.js` (novo) — **máquina de estados** `idle | starting | streaming | awaiting-approval` (state local do detalhe). Molde: `use-ai-chat.ts` do nexus.
- [sequencial] Gate (a): no estado `idle`, mostrar textarea "Observação extra p/ a IA (opcional)" + botão **"Aprovar p/ iniciar"**. Ao clicar: `state='starting'` → `POST /suggestions/{sid}/start` `{observation}` → guarda `conversation_id` → `state='streaming'` e abre o stream (Fase 4).
- [sequencial] `panel.js:258-273` (`doDecide`) — o approve antigo síncrono some para pendências agênticas; recusar (`reject`) permanece (não abre chat). Remover a dependência do `GeneratingModal` bloqueante (`:380,386-397`) neste caminho.
- [paralelo] Manter a lista (`panel.js:126-382`) e o WS `plugin_melhorias_changed` (`:171-179`) recarregando as linhas; o **chat** é por-detalhe (não recarrega a lista a cada token).

**Pronto quando:** abro uma sugestão pendente no painel → vejo as mensagens-alvo + textarea de observação + "Aprovar p/ iniciar" → clico → o estado vai `idle→starting→streaming` e a área de chat aparece pronta pro stream (mesmo que o backend ainda devolva stub). Recusar continua fechando a pendência sem abrir chat. `.dark` legível.

```
#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-07-16, commit 0a1fdab)
- **O que foi feito:** panel.js: status em_chat (label/cor/filtro); coluna Ações por backend (external ⇒ Abrir chat / Continuar chat); DetailModal com N mensagens marcadas + gate D1-a (textarea de observação + "Aprovar p/ iniciar" → POST /suggestions/{sid}/conversations) + carregamento da conversa mais recente p/ em_chat; Recusar preservado; GeneratingModal só no caminho direct.
- **Como foi feito / decisoes:** endpoints reconciliados com o sub-plano 02 §4.1 (start = POST /suggestions/{sid}/conversations; approve-tool = POST /conversations/{cid}/approve) — a tabela de contrato desta página (§2) refletia um rascunho anterior.
- **Problemas / pendencias:** validação visual .dark pendente de uso real.
- **Verificacao:** fluxo coberto pelos testes de backend; sintaxe verde.
```

---

### Fase 4 — Consumo SSE + cards + gate (b) 🔴
**[depende de: 3; backend `GET /suggestions/{sid}/stream`, `POST .../messages`, `POST .../approve-tool`]**

> ⚠️ **Transporte browser↔gateway — ver mestre §8 P2 (autoridade).** O v1 RECOMENDADO é **reusar o `/ws`** que o painel já mantém (o gateway re-emite os 9 eventos do executor como `broadcast(...)`; ver sub-plano 02 §3). Nesse caminho, o `chat.js` **escuta o `/ws`** e filtra por `conversation_id` — o parser/máquina-de-estados abaixo continua válido, só muda a **fonte de bytes** (evento WS em vez de chunk SSE). O `fetch`+`ReadableStream` descrito nesta fase é o caminho **(b) alternativo** (SSE dedicado), a implementar só se P2 escolher isolamento estrito por-operador. A linha de "Falsos positivos" sobre o `/ws` fica subordinada a esta decisão.

**Objetivo:** consumir o stream do executor (via `/ws` re-emitido — recomendado — ou via `fetch`+`ReadableStream` SSE dedicado), renderizar os cards (assistant/tool/aprovação/erro) e resolver o gate (b) por V/X.

**Itens:**
- [sequencial] `static/chat.js` *(novo)* — hook/módulo de streaming. Porta de `use-ai-chat.ts:186-248`:
  - `fetch(url, { method:'GET', headers:{ ...authHeaders(), Accept:'text/event-stream' }, signal })` → `response.body.getReader()`; acumular `buffer`, `split('\n\n')`; `parseSseFrame` lê linhas `event:`/`data:`, ignora `:` (heartbeat), `JSON.parse` do data.
  - **Módulo puro** `parseSseFrame(frame)` exportado p/ teste `node --test`.
  - `AbortController` cancelado ao fechar o detalhe/desmontar.
- [sequencial] `chat.js` `handleEvent` — os 9 tipos de SSE (porta de `types.ts:100-110`):

  | Evento | `data` | Efeito |
  |--------|--------|--------|
  | `conversation_started` | `{conversationId}` | no-op |
  | `message_start` | `{messageId}` | inicia bolha assistant (`streaming`) |
  | `message_chunk` | `{messageId, delta}` | append de token |
  | `message_end` | `{messageId}` | finaliza; **detecta auth-error no texto** (Fase 5) |
  | `tool_call_start` | `{toolCallId, name, input}` | card de tool `running` |
  | `tool_call_end` | `{toolCallId, output?, error?}` | tool `done`/`error` |
  | `approval_needed` | `{approvalId, toolName, toolInput, summary?}` | card de aprovação → `state='awaiting-approval'` |
  | `done` | `{}` | `state='idle'` |
  | `error` | `{message, retryable}` | card de erro; se auth-error → modal relogin (Fase 5) |
- [paralelo] `panel.js` — componentes de card (usar `wa-*`): `AssistantCard` (markdown streaming), `ToolCard` (nome + input/output colapsável), `ApprovalCard` (resumo + botões **V/X**), `ErrorCard`.
- [sequencial] Gate (b): `ApprovalCard` V → `POST /suggestions/{sid}/approve-tool` `{approvalId, approved:true}`; X → abre um mini-prompt de motivo opcional → `{approved:false, reason}` (recusa sem motivo injeta reason default, molde `use-ai-chat.ts:82-91`). Após decidir, `state='streaming'`.
- [sequencial] Input do chat (texto): textarea + enviar → `POST /suggestions/{sid}/messages` `{parts:[{type:'text',text}]}` (parts já preparado p/ imagem na Fase 6).
- [paralelo] Idempotência de UI: card de aprovação já decidido some/trava; reconexão do stream não duplica bolhas (dedupe por `messageId`/`toolCallId`).

**Pronto quando:** `venv/bin/python -m pytest tests/ -q` verde + `node --test` no `static/chat.js` (parser SSE) verde. Manual (mesmo com executor mockado devolvendo eventos): aprovar p/ iniciar → tokens aparecem na bolha assistant; um `approval_needed` mostra card com V/X; clicar V destrava e a tool roda (card `done`); `done` volta ao `idle`. `.dark` legível em todos os cards.

```
#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-07-16, commit 0a1fdab)
- **O que foi feito:** chat_core.js (reducer PURO dos eventos + isAuthError + persistedToItems) + chat.js (AgenticChat: hidrata do DB, useAiChatEvents escuta plugin_melhorias_ai_event no /ws filtrando conversation_id, cards Assistant/User/Tool/Approval/Error, gate D1-b com V/X + motivo opcional, dedupe por messageId/toolCallId, input Enter-envia, resume).
- **Como foi feito / decisoes:** **caminho (a) do P2 implementado — WS reuso**; o parser SSE dedicado (fetch+ReadableStream) NÃO foi implementado (fica como upgrade documentado). Evento extra approval_registered (write-through) tratado com dedupe.
- **Problemas / pendencias:** nenhum.
- **Verificacao:** node --test chat_core.test.js — 7 casos verdes (start/chunk/end, chunk-sem-start, tool dedupe, approval→awaiting, done→idle, error, hidratação).
```

---

### Fase 5 — Modal de relogin OAuth 🟢
**[depende de: 4 (detecção de auth-error); backend `POST /admin/relogin/{start,complete,abort}`]**

**Objetivo:** quando o executor perder a sessão Claude.ai, o operador renova sem SSH, via modal.

**Itens:**
- [sequencial] `static/relogin.js` *(novo)* — porta de `ai-chat-relogin-modal.tsx`. Fases `starting → awaiting-code → completing → success` (+ `error`).
  - `starting`: `POST /admin/relogin/start {}` → recebe `{sessionId, url}`; mostrar a URL (link + botão "Abrir").
  - `awaiting-code`: input p/ colar o **código** da página do Claude.ai.
  - `completing`: `POST /admin/relogin/complete {sessionId, code}`.
  - `success`: fecha; **força nova conversa** no detalhe (limpa `conversation_id`) p/ o próximo start pegar token fresco.
  - Cancelar/fechar → `POST /admin/relogin/abort {sessionId}`.
- [sequencial] `chat.js` — heurística `isAuthError(text)` (porta de `use-ai-chat.ts:352-360`): `/\b401\b/`, `authentication_error`, `invalid authentication credentials`, `please run /login`. Aplicar TANTO no evento SSE `error` QUANTO no **texto** do `message_end` → abrir o modal de relogin.

**Pronto quando:** simulando um `error` com `authentication_error` (ou um `message_end` com "please run /login"), o modal de relogin abre; o fluxo `start → colar código → complete → success` fecha o modal e o próximo "Aprovar p/ iniciar" abre uma conversa nova. `.dark` legível.

```
#### Status de execução — Fase 5
**Estado:** ✅ Concluída (2026-07-16, commit 0a1fdab)
- **O que foi feito:** relogin.js (fases starting→awaiting-code→completing→success, abort no fechar); isAuthError roda no evento error E no texto do message_end (AgenticChat.onAuthError abre o modal); sucesso limpa a conversation (próximo start pega token fresco).
- **Como foi feito / decisoes:** porta fiel do modal do nexus, classes wa-*.
- **Problemas / pendencias:** exercício real do relogin pendente (OAuth atual válido).
- **Verificacao:** isAuthError coberto por node --test; sintaxe verde.
```

---

### Fase 6 — Imagens no chat 🟢
**[depende de: 4; e de 1 p/ auto-incluir imagens selecionadas]**

**Objetivo:** anexar imagem no input do chat e auto-incluir as **mensagens-imagem selecionadas** (o WhatsBot já tem `media_type`/`media_path` sob `statics/`).

**Itens:**
- [paralelo] Input do chat — `<input type="file" accept="image/*">` (molde `Composer.js:57`) + preview (`URL.createObjectURL`, molde `useMediaUpload.js:77`). Enviar como `parts:[{type:'text',text?},{type:'image', data:<base64>|url}]` no `POST /suggestions/{sid}/messages`.
- [sequencial] Fase 2/3 — quando a seleção (Fase 1) inclui respostas/mensagens com `media_type==='image'`, incluir automaticamente no payload da sugestão como `{type:'image', media_path}` (o gateway lê o arquivo de `statics/`). Não re-uploadar o arquivo (ver Falsos positivos).
- [paralelo] Render de imagem nas bolhas do chat — `<img src=${p.type==='image' ? ('/' + p.media_path) : p.dataUrl}>` (molde `MediaContent.js:45-56`), com `max-w-full` e fallback de imagem quebrada.

**Pronto quando:** anexo uma imagem no input → aparece na minha bolha e é enviada; seleciono uma mensagem-imagem existente + peço melhoria → o gateway recebe a referência `media_path` (verificável no payload); a IA "vê" a imagem (quando o executor responde). Bolhas de imagem legíveis no `.dark`.

```
#### Status de execução — Fase 6
**Estado:** ✅ Concluída (2026-07-16, commit 0a1fdab)
- **O que foi feito:** input do chat com anexo de imagem (FileReader→base64, cap 5MB, preview + remover) enviando parts:[text?,image]; bolha do usuário renderiza a imagem; mensagens-imagem SELECIONADAS vão por media_path no payload da sugestão (gateway converte pra base64 — resolve_image_parts, confinado a statics/).
- **Como foi feito / decisoes:** upload manual = base64 direto; selecionadas = referência de disco (sem re-upload), conforme falsos-positivos.
- **Problemas / pendencias:** visão real da imagem pelo Claude a validar no 1º uso.
- **Verificacao:** caminho de resolução coberto no gateway; sintaxe verde.
```

---

### Fase 7 — Modo escuro + tema (transversal) 🟢
**[depende de: 3, 4, 5, 6]**

**Objetivo:** garantir contraste/legibilidade de todas as telas novas nos dois temas.

**Itens:**
- [paralelo] Revisar barra de seleção (Fase 1), dialog multi-mensagem (Fase 2), painel-chat + cards (Fases 3–4), modal de relogin (Fase 5) e bolhas de imagem (Fase 6): trocar qualquer cor crua fora da lista coberta por `wa-*`; inputs/textarea usam `.wa-field`; badges/acentos usam tintas com fallback (`text-amber-600`, `text-red-500` já usados no `panel.js`).
- [sequencial] Abrir cada tela com `.dark` ligado (toggle da engrenagem → "Modo escuro") e conferir: superfícies (`bg-wa-panel`/`bg-wa-bg`), textos (`text-wa-text`/`text-wa-secondary`), bordas (`border-wa-border`), hover (`bg-wa-hover`), spinner/realce de seleção (`bg-wa-teal/10`).

**Pronto quando:** checklist de tema (§6) todo marcado; nenhuma superfície branca/cinza-clara ilegível no `.dark`; screenshots claro+escuro de cada tela nova conferidos.

```
#### Status de execução — Fase 7
**Estado:** ✅ Concluída em código (2026-07-16) — validação visual pendente
- **O que foi feito:** todas as superfícies novas usam wa-*/.wa-field; acentos com tintas cobertas pelo fallback (amber-50/blue-600/red-50 etc.).
- **Como foi feito / decisoes:** regra do CLAUDE.md seguida por construção.
- **Problemas / pendencias:** conferência com .dark ligado (screenshots claro/escuro) fica para o primeiro uso no painel — anotar contraste do card de approval (amber-50) se necessário.
- **Verificacao:** revisão estática das classes; sem hex inline fora de acentos.
```

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Toca-core (Fase 1) | Alterar `ContactDetail.js`/`MessageBubble.js` pode regredir o menu single / render de mensagens usado por todo o app | Seam NOVO (F1), não altera `filter.message.contextMenu.items`; caracterização manual do menu antes/depois; sem barra quando nenhum plugin registra lote (array vazio = core byte-idêntico). |
| `applyFilter` `null` aborta | Um filtro de lote que devolva `null` derruba a barra inteira | Documentar (registry) e no `extends.js` sempre devolver array (nunca `null`), igual ao caso single (`extends.js:44`). |
| Chave de seleção instável | `_localId` de mensagens otimistas muda ao reconciliar via WS | Chave = `_id ?? _localId`; respostas da IA alvo já têm `_id` (salvas). |
| SSE via `fetch` (F2) | Reconexão/abort mal-feitos vazam readers ou duplicam bolhas | `AbortController` no unmount/close; dedupe por `messageId`/`toolCallId`; heartbeat `:` ignorado no parser. |
| Auth do stream | `EventSource` não manda `Bearer` | `fetch` com `authHeaders()` (`Authorization: Bearer` do `localStorage`) — F2. |
| Gate (b) ignorado | Operador aprova mutação sem ler o `toolInput` | Card mostra `summary`/`toolInput` legível; recusa aceita `reason`; toda mutação bloqueia até V/X (contrato do executor). |
| Auth-error escondido no texto | O SDK às vezes devolve erro de auth como conteúdo de `message_end`, não como `error` | `isAuthError` roda nos DOIS pontos (evento `error` + texto do `message_end`) — Fase 5. |
| Imagens grandes em base64 | Colar imagem enorme incha o payload | Imagens SELECIONADAS vão por `media_path` (gateway lê do disco), não base64; só o upload manual do input vira base64, com limite de tamanho/mime. |
| Distribuição por `.zip` (D6) | Editar `assets/plugin_examples/melhorias/` não reflete na instância até re-empacotar/re-importar | Após cada fase de plugin: gerar o `.zip` (`GET /api/plugins/melhorias/export` ou zip da fonte) e re-importar; a cópia `storages/plugins/melhorias/` é gitignored. |
| Modo escuro | Cards/chat/modal novos com cor crua fora da lista coberta | `wa-*`/`.wa-field` + teste `.dark` (Fase 7). |
| WS da lista vs SSE do chat | Confundir `plugin_melhorias_changed` (lista) com o stream (chat) | `plugin_melhorias_changed` só recarrega a **lista** (`panel.js:171-179`); o chat é SSE por-sugestão, isolado. |

---

## 6. Checklist de verificação

- [ ] Com o plugin `melhorias` **desativado**, chat + menu de contexto byte-idênticos ao baseline (sem barra de seleção, sem checkbox).
- [ ] `filter.message.contextMenu.items` (single) intacto — item "Gerar melhoria" numa resposta da IA continua funcionando.
- [ ] Novo seam `filter.selection.batchActions` documentado em `registry.js` (value, ctx, semântica de `null`).
- [ ] Modo de seleção: entrar, marcar múltiplas bolhas (contador correto), cancelar limpa; chave estável `_id ?? _localId`.
- [ ] `POST /suggestions` envia `messages:[…]` (multi) e o caminho single ainda envia (fallback).
- [ ] Gate (a): "Aprovar p/ iniciar" + observação → `POST /start` → estado `idle→starting→streaming`.
- [ ] SSE consumido via `fetch`+`ReadableStream` (não `EventSource`); `Authorization: Bearer` presente; heartbeat `:` ignorado.
- [ ] `node --test` no parser SSE (`static/chat.js`) verde.
- [ ] Cards: assistant (streaming), tool (running/done/error), approval (V/X = gate b), error — todos renderizam.
- [ ] Gate (b): V executa a tool; X com/sem motivo recusa; card decidido trava.
- [ ] Relogin: `isAuthError` dispara no evento `error` E no texto do `message_end`; modal `start→código→complete→success` força conversa nova.
- [ ] Imagens: anexar no input (base64) e auto-incluir imagens selecionadas (`media_path`); render nas bolhas.
- [ ] `venv/bin/python -m pytest tests/ -q` verde (Postgres `WHATSBOT_TEST_DB_URL`).
- [ ] Modo escuro (`.dark`) legível: barra de seleção, dialog multi, painel-chat, cards, modal relogin, bolhas de imagem.
- [ ] `.zip` re-empacotado e re-importado reflete todas as mudanças de plugin.
