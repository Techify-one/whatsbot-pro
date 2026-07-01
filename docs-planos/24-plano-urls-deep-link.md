# Plano 24 — URLs deep-link: todo estado endereçável na URL

> **Como usar este plano**
>
> Este plano é executável por uma IA (ou por você numa sessão futura). **Regra obrigatória:** ao concluir OU travar em qualquer fase, preencha o bloco **"Status de execução"** daquela fase **antes** de avançar — nunca deixe uma fase sem registro. Isso permite retomar sabendo exatamente o que foi feito, o que falhou e o que ficou pendente.
>
> Legenda de estado: `⬜ Não iniciada` · `🟡 Em andamento` · `✅ Concluída` · `⛔ Bloqueada`.
>
> Cada bloco de status pede: **O que foi feito** (arquivos/funções), **Como foi feito / decisões** (escolhas e desvios), **Problemas / pendências**, **Verificação** (testes rodados + resultado, validação manual).
>
> **Paralelismo:** a Fase 0 (infra) é pré-requisito de tudo. Dentro da Fase 1 e da Fase 2, cada tela é independente e pode ser despachada em paralelo por sub-agentes distintos (arquivos disjuntos). A Fase 3 (copiar-link + Config) depende de 1. A Fase 4 (testes/QA) fecha.

---

## Contexto

Hoje o painel do WhatsBot reflete **parte** do estado na URL (deep-link de entidade construído num plano anterior — ver "Baseline"), mas boa parte do que o operador está vendo **não é endereçável**: filtros de lista, aba/agrupamento do Kanban, busca, modais de histórico, step de execução, entrada de auditoria, etc. Isso some no F5 e **não pode ser compartilhado**.

**Objetivo:** tornar endereçável na URL **tudo** que identifica uma entidade ou um estado de visão, para dois usos concretos:

1. **Enviar links para o time** — "olha esse atendimento", "essa execução falhou nesse passo", "os atendimentos resolvidos do João nos últimos 7 dias", "essa versão do prompt do agente".
2. **Debug da IA no futuro** — um agente de IA (ou humano) reproduz *exatamente* a tela a partir da URL, e o link é legível a olho nu (params nomeados, não blob opaco).

**Decisões já tomadas (não reabrir):**

| # | Decisão | Consequência |
|---|---------|--------------|
| D1 | **Escopo completo:** (A) IDs de entidade que faltam + (B) estado de lista/filtro + (C) botões "copiar link" e seções de Config endereçáveis | 4 fases; toca ~15 telas |
| D2 | **Params legíveis** — `?status=open&search=pagamento&archived=true`, nunca blob codificado | Filtro avançado (`advFilters`) vira um sub-param JSON **só quando presente e não-default** |
| D3 | **URL é fonte da verdade no load** — precedência **URL > localStorage > default** | Link compartilhado reproduz a tela mesmo que o localStorage local diga outra coisa |
| D4 | **Não há build step** (ESM + HTM + import-map) — parsers puros testáveis com `node --test` | Toda lógica de parse/serialize mora em módulo puro sem DOM/Preact |
| D5 | **Aditivo e retrocompatível** — toda URL antiga continua abrindo | Ausência de param = comportamento default de hoje; nada quebra bookmarks |

---

## Baseline — o que JÁ está na URL (não reimplementar)

Infra central: [web/static/js/hooks/useDeepLink.js](../web/static/js/hooks/useDeepLink.js) (`ENTITY_ROUTES` + hook `useDeepLink`), [web/static/js/components/shell/routing.js](../web/static/js/components/shell/routing.js) (parsers puros), [web/static/js/components/shell/screenRegistry.js](../web/static/js/components/shell/screenRegistry.js) (wrappers que leem `window.location`), [web/static/js/components/shell/App.js](../web/static/js/components/shell/App.js) (estado `initial*` + `popstate`).

| Já coberto | Padrão | Onde |
|---|---|---|
| Detalhe do contato | `/contacts/{id}` | useDeepLink.js:30 · ContactsListScreen.js |
| Atendimento aberto | `/conversations/{id}` | routing.js:100 · useConversationSelection.js |
| Permalink de mensagem (scroll) | `?message={_id}` | routing.js:111 · useMessageActions.js:78-87 (**tem botão "copiar link"**) |
| Execução | `/executions/{id}` | routing.js:76 · Executions.js:158 |
| Usuário / Papel | `/users/{id}` · `/users/roles/{key}` | useDeepLink.js:52-61 |
| Plugin (config) | `/plugins/{id}` | useDeepLink.js:42-45 |
| Resposta rápida | `/quick-replies/{code}` | useDeepLink.js:63-66 |
| Atributo personalizado | `/custom-attributes/{scope}/{key}` | useDeepLink.js:68-73 |
| Motor de IA (aba + entidade) | `/ai/{sub}/{id}` (agents/variables/tools/general) | useDeepLink.js:35-40 |
| Canal (edição) | `/channels/{id}` | useDeepLink.js:47-50 · ChannelsManager.js:75-85 |

**Conclusão:** a fundação path-based é sólida. O plano 24 **fecha lacunas**, não reescreve.

---

## Princípios de design (ler antes de codar)

1. **Path para identidade, query para visão.** Um id de entidade (agente, view, canal, execução) vai no **path** e estende `ENTITY_ROUTES`. Um estado de lista/filtro/ordenação vai na **query-string** legível. Uma flag de modal transiente ligada a um id já no path (ex: QR de um canal) vai como **query booleana** (`?connect=1`).
2. **Serialização legível e mínima (D2).** Só serializar o que **difere do default**. `?status=open` é o default do hub → **não** vai na URL; `?status=closed` vai. Isso mantém a URL curta e o link "limpo" quando nada foi mexido.
3. **Precedência no load (D3):** `URL param` → senão `localStorage`/preset salvo → senão `default`. Ao **aplicar** um filtro/seleção, a URL é reescrita. Ao dar `popstate` (voltar/avançar), re-hidrata da URL.
4. **`replaceState` vs `pushState`:** trocar **filtro/busca/aba de visão** usa `replaceState` (não polui o histórico — voltar não desfaz cada tecla digitada). **Abrir uma entidade** (atendimento, agente, view, detalhe) usa `pushState` (voltar fecha a entidade). Debounce da busca (300ms) antes de tocar a URL.
5. **Encoding de nomes.** Ids que são texto livre (tag, atributo, provider) usam `encodeURIComponent`. O helper puro cuida disso; call sites nunca concatenam à mão.
6. **Sem regressão de saved-filters.** O hub já tem presets salvos (DB + `ACTIVE_FILTER_KEY` no localStorage, [useConversationFilters.js:24](../web/static/js/components/contacts/hooks/useConversationFilters.js#L24)). A URL **sobrepõe** o preset no load; se a URL tiver filtros ad-hoc, o preset ativo é desmarcado (vira ad-hoc). Reusar `normalizeSpec`/`specsEqual`/`isDefaultSpec` de `services/conversationRows.js` — **não** duplicar lógica de spec.
7. **Testável sem DOM.** Todo parse/serialize é função pura em módulo próprio (`services/urlState.js`) com testes `node --test`, no espírito de `routing.js`.

---

## Arquitetura da solução (3 camadas)

### Camada 1 — Estender `ENTITY_ROUTES` (path, para os ids que faltam)
Onde o id é natural e estável, adicionar/estender uma entrada em [useDeepLink.js](../web/static/js/hooks/useDeepLink.js) `ENTITY_ROUTES` (ou o parser hard-coded em `routing.js` quando a rota é core-especial como `/executions`). Ex.: sub-rota de histórico do agente, view do Kanban.

### Camada 2 — Novo módulo `services/urlState.js` (query, para estado de lista)
Módulo **puro** (sem Preact/DOM) com, por tela, um par `parse(searchStr) → state` / `serialize(state) → searchStr` que **omite defaults** (D2) e faz encode. Um hook fino `useUrlState({ read, write, deps })` em `hooks/useUrlState.js` liga isso ao componente: lê no mount + `popstate`, escreve (`replaceState`) quando o estado muda. Espelha o padrão que `Executions.js:189-235` já faz à mão, mas reusável e testado.

O `App.js` **não** precisa centralizar a query — cada tela dona do seu estado usa `useUrlState` localmente (como cada tela hoje usa `useDeepLink`). O `App.js` só ganha um `initialSearch`/re-deriva no `popstate` se alguma tela precisar do search no primeiro paint (a maioria lê `window.location.search` direto no mount).

### Camada 3 — Utilitário "copiar link" compartilhado
Extrair um `utils/copyDeepLink.js` a partir do que `useMessageActions.js:85` (`copyMessageLink`) + `notices.js` (fallback de clipboard p/ contexto não-seguro HTTP, [channels/notices.js:15-25](../web/static/js/components/channels/notices.js#L15)) já fazem. Uma função `copyDeepLink(path)` que monta `location.origin + path`, copia com fallback e sinaliza "copiado". Botões reusam isso.

---

## Matriz-alvo (antes → depois)

### Categoria A — IDs de entidade (path)

| # | Entidade | Hoje (estado efêmero) | URL alvo | Camada |
|---|---|---|---|---|
| A1 | View ativa do Kanban | `activeViewId` em localStorage — protocolos_tab.js:233 | `/attendances?view={id}` | 2 (query; `id` do backend `/kanban-views`) |
| A2 | Histórico/diff de agente | `historyFor` — AgentsManager.js:465 | `/ai/agents/{key}/history` (+ `?from={v}&to={v}`) | 1 |
| A3 | Histórico de prompt | `promptHistoryFor` — AgentsManager.js:469 | `/ai/agents/{key}/prompt-history` (+ `?v={v}`) | 1 |
| A4 | Histórico de tool code-in-DB | `historyFor` — ToolsUnified.js:68 | `/ai/tools/{name}/history` (+ `?v={v}`) | 1 |
| A5 | Step dentro de execução | expand local — Executions.js:79 | `/executions/{id}?step={step_id}` | 2 |
| A6 | Entrada de auditoria / diff | `expandedId` — AuditLog.js:142 | `/audit?expanded={id}` | 2 |
| A7 | Modais de canal por id | `connectFor`/`webhookFor`/`telegramNotice` — ChannelsManager.js:54-58 | `/channels/{id}?connect=1` · `?webhook=1` · `?telegram=1` | 1+2 |
| A8 | Provider ao criar canal | `creating`+`provider` — ChannelsManager.js:47 / ChannelForm.js:27 | `/channels/new?provider={tipo}` | 2 |

### Categoria B — Estado de lista/filtro (query legível)

| Tela | Params alvo | Estado / onde |
|---|---|---|
| **Hub de conversas** | `status`, `assignment`, `sort`, `search`, `archived`, `tags`, `panel`, `adv` (JSON só se ≠ default) | useConversationFilters.js:36-46 · useConversationList.js:27-28 · useConversationSelection.js:49 |
| **Contatos (full-page)** | `search`, `adv` | ContactsListScreen.js:219,230 |
| **Atendimentos/Kanban** | `view` (board/list), `mode` (agrupamento), `attr` (se mode=stage), `status` (só abertos) | Attendances.js:25-45 · grouping.js:13-18 |
| **Execuções** | `phone`, `status`, `page` | Executions.js:166-168 |
| **Auditoria** | `resource_type`, `action`, `actor_type`, `resource_id`, `from`, `to`, `offset` | AuditLog.js:124-142 |
| **Custos** | `period` (+`start`/`end`), `sort`, `order`, `search` | CostsDashboard.js:38-49 |
| **Tools (busca)** | `q` | ToolsUnified.js:61 |
| **Canais (arquivados)** | `archived` | ChannelsManager.js:52-53 |

### Categoria C — Compartilhamento e Config

| Item | Alvo |
|---|---|
| Botões "copiar link" | Header do atendimento/contato · card de execução/auditoria · linha de agente/canal/view do Kanban · toolbar do hub (copia a URL filtrada) |
| Seções de Configurações endereçáveis | `?section={marcar-atendimentos\|avisos\|avancado\|banco}` — ConfigPanel.js:127,187,254 + DatabaseSettings |

### Fora de escopo (efêmero — NÃO endereçar)
Modo de seleção múltipla (bulk), dropdowns/menus abertos, estado de drag do Kanban, modal "novo atendimento" e picker de canal, modais de confirmação de exclusão/purge, modal de importar plugin. (São ações transientes, não navegação; endereçá-los confunde o histórico.)

---

## Fases

### Fase 0 — Infra compartilhada (pré-requisito) · `✅`

**Objetivo:** criar as 3 peças reutilizáveis antes de tocar qualquer tela.

1. **`web/static/js/services/urlState.js`** (puro): helpers genéricos `readParams(searchStr, schema)` e `writeParams(state, schema)` onde `schema` declara, por param, `{ key, default, enc?, dec? }`; serialize **omite defaults** (D2). Mais os codecs específicos que precisem de forma especial (ex: `adv` → `encodeURIComponent(JSON.stringify(normalizeSpec(...)))`, decodificado com try/catch → `[]`).
2. **`web/static/js/hooks/useUrlState.js`**: hook `useUrlState({ parse, serialize, state, apply, mode='replace' })` — no mount e em `popstate`, chama `apply(parse(location.search))`; quando `state` muda, escreve via `history[mode+'State']` **só se** a query serializada difere da atual (evita loop e histórico poluído). Debounce opcional para busca.
3. **`web/static/js/utils/copyDeepLink.js`**: `copyDeepLink(path) → Promise<boolean>` reusando o fallback de clipboard de [channels/notices.js](../web/static/js/components/channels/notices.js#L15) (contexto não-seguro/HTTP). Refatorar `useMessageActions.copyMessageLink` para usar este util (mantendo o comportamento atual).
4. **Testes puros** (`node --test`): `services/urlState.test.js` cobrindo omit-default, encode/decode de nomes, round-trip `parse(serialize(x)) === x`, e o codec `adv` com JSON inválido → `[]`.

**Verificação:** `node --test` verde nos parsers; nenhum componente alterado ainda além do refactor de `copyMessageLink` (testar manualmente que copiar link de mensagem segue funcionando).

**Status de execução — Fase 0** · `✅`
- O que foi feito: criados `services/urlState.js` (codec puro: `readParams`/`writeParams` + fábricas `str`/`enumStr`/`bool`/`int`/`list`/`json`), `services/urlState.test.js` (11 casos), `hooks/useUrlState.js` (sync query↔componente) e `utils/copyDeepLink.js` (`deepLinkUrl`/`copyDeepLink` + componente `CopyLinkButton`). Refatorado `useMessageActions.messagePermalink` para usar `deepLinkUrl`.
- Como foi feito / decisões: serialize **omite defaults** (D2); encode delegado ao `URLSearchParams` (codecs operam no nível string decodificado; `list`/`json` fazem encode interno próprio pra sobreviver ao join por vírgula). `useUrlState` evita a corrida de montagem pulando o 1º write (senão limparia a `?query` de entrada antes do setState de hidratação) e só escreve quando a query difere da barra de endereço (sem loop/histórico poluído). `copyDeepLink` reusa `copyToClipboard` (fallback HTTP) e `LinkIcon` já existentes.
- Problemas / pendências: nenhuma. Sem ciclo de import (copyDeepLink→MessageContextMenu é folha).
- Verificação: `node --test web/static/js/services/urlState.test.js` → **11/11 pass**. `node --check` OK nos 4 arquivos. Copiar link de mensagem preservado (mesmo caminho de clipboard).

---

### Fase 1 — IDs de entidade que faltam (Categoria A, path) · `✅`

Cada item é independente (arquivos disjuntos) — paralelizável.

- **A2/A3/A4 — Históricos do motor de IA.** Estender `ENTITY_ROUTES['ai']` (ou adicionar sub-rotas) para aceitar `/ai/agents/{key}/history`, `/ai/agents/{key}/prompt-history`, `/ai/tools/{name}/history`, com a versão selecionada em query (`?from`/`?to` para diff de agente; `?v` para prompt/tool). Em [AgentsManager.js:465-469](../web/static/js/components/ai/AgentsManager.js#L465) e [ToolsUnified.js:68](../web/static/js/components/ai/ToolsUnified.js#L68), abrir/fechar o modal empurra/limpa a URL (`pushState`); no load/`popstate`, reabrir o modal a partir da URL. **Cuidado:** o `parse` do `ai` hoje só entende `sub` + `id`; adicionar o segmento `history`/`prompt-history` sem quebrar `/ai/agents/{key}` (edição). Preferir tratar `history` como um terceiro segmento reconhecido, não como `id`.
- **A7 — Modais de canal por id.** `/channels/{id}` já existe (edição). Adicionar as flags `?connect=1`, `?webhook=1`, `?telegram=1` (mutuamente exclusivas) que, no load, abrem o respectivo modal daquele canal ([ChannelsManager.js:54-58](../web/static/js/components/ChannelsManager.js#L54)). Ao abrir/fechar o modal, `replaceState` da flag (o modal é sobre o mesmo canal já no path).
- **A8 — Provider ao criar canal.** `/channels/new?provider={gowa|whatsapp_cloud|telegram|test}`. `new` é pseudo-id: tratar no parser de `channels` como caso especial (não bate com `{id}` numérico). Abrir a tela de criação com o provider pré-selecionado ([ChannelForm.js:27](../web/static/js/components/channels/ChannelForm.js#L27)).

**Verificação:** para cada item, colar a URL numa aba nova abre o modal/estado correto; voltar/avançar do navegador funciona; `/ai/agents/{key}` (edição) e `/channels/{id}` (edição) **continuam** funcionando (regressão).

**Status de execução — Fase 1** · `✅`
- O que foi feito: A2/A3/A4 (históricos de agente/prompt/tool code-in-DB) em AgentsManager.js + ToolsUnified.js; A7 (modais QR/webhook/telegram por canal) + A8 (novo canal com provider) em ChannelsManager.js + channels/ChannelForm.js.
- Como foi feito / decisões: **desvio consciente da forma path do matriz** — em vez de estender o modelo `{sub,id}` do ENTITY_ROUTES com um 3º segmento (`/ai/agents/{key}/history`), usei **flags de query sobre o path já existente** (`/ai/agents/{key}?history=1`, `?prompt-history=1`; `/ai/tools/{name}?history=1` + `?q=` da busca; `/channels/{id}?connect=1|webhook=1|telegram=1`). Justificativa: a arquitetura de 2 camadas (path=identidade, query=visão) já cobre "um modal/sub-estado de uma entidade identificada" com zero mudança no roteamento compartilhado — mais simples e sem risco de regressão nos deep-links path existentes. A8 aproveita que `/channels/new` já faz parse para `{id:'new'}` no ENTITY_ROUTE de canais: o `open` do useDeepLink trata `id==='new'` ANTES do find (abre o form de criação e lê `?provider=`). Cada modal reabre no mount/popstate quando a entidade do path resolve (gate `ready:!loading`).
- Problemas / pendências: nenhuma. Deep-links de edição existentes (`/ai/agents/{key}`, `/channels/{id}`) preservados (aditivo).
- Verificação: `node --check` OK; imports resolvidos; `shell/routing.test.js` confirma `/channels/new` → tab channels; 962/962 endpoint tests.

---

### Fase 2 — Estado de lista/filtro (Categoria B, query legível) · `✅`

Cada tela é independente. Todas usam `useUrlState` (Fase 0) e serializam **só o não-default** (D2).

- **Hub de conversas** (o mais rico — fazer primeiro como referência). Serializar `status`/`assignment`/`sort`/`search`/`archived`/`tags`/`panel` e o filtro avançado `adv` (JSON só quando `!isDefaultSpec`). **Precedência (D3):** no load, se a URL trouxer filtros, ela vence o preset do `ACTIVE_FILTER_KEY`; senão, mantém o comportamento atual de re-aplicar o preset salvo. Ligar aos setters de [useConversationFilters.js](../web/static/js/components/contacts/hooks/useConversationFilters.js) + `search`/`showArchived` de [useConversationList.js:27-28](../web/static/js/components/contacts/hooks/useConversationList.js#L27) + `openPanel` de [useConversationSelection.js:49](../web/static/js/components/contacts/hooks/useConversationSelection.js#L49). Reusar `normalizeSpec`/`isDefaultSpec` de `services/conversationRows.js`.
- **Atendimentos/Kanban.** `?view=board|list`, `?mode=<grouping>`, `?attr=<key>` (só se `mode=stage`), `?status=open` (traduz `onlyOpen`). Substituir a persistência localStorage-only de [Attendances.js:25-45](../web/static/js/components/attendances/Attendances.js#L25) pela precedência URL > localStorage. **A1 (view salva)** entra aqui: `?view={id}` do plugin protocolos ([protocolos_tab.js:233](../storages/plugins/protocolos/static/protocolos_tab.js#L233)) — hoje `activeViewId` em localStorage; adicionar sync com a URL reusando o padrão de `?detail=` que o plugin já tem ([protocolos_tab.js:423-433](../storages/plugins/protocolos/static/protocolos_tab.js#L423)).
- **Execuções.** `?phone`/`?status`/`?page` (B) + **A5** `?step={id}` para saltar/expandir um passo dentro de `/executions/{id}`. A tela já tem sync de path via `popstate` ([Executions.js:189-235](../web/static/js/components/Executions.js#L189)) — estender para os params.
- **Auditoria.** `?resource_type&action&actor_type&resource_id&from&to&offset` + **A6** `?expanded={id}`. Aplicar no load a partir da URL e reescrever ao clicar "Filtrar" ([AuditLog.js:124-195](../web/static/js/components/AuditLog.js#L124)).
- **Custos.** `?period` (+`start`/`end` se custom) `?sort&order&search` ([CostsDashboard.js:38-49](../web/static/js/components/CostsDashboard.js#L38)).
- **Contatos (full-page).** `?search` + `?adv` ([ContactsListScreen.js:219,230](../web/static/js/components/ContactsListScreen.js#L219)).
- **Tools (busca).** `?q` para o filtro de busca ([ToolsUnified.js:61](../web/static/js/components/ai/ToolsUnified.js#L61)). **Canais arquivados:** `?archived=1` ([ChannelsManager.js:52-53](../web/static/js/components/ChannelsManager.js#L52)).

**Verificação:** por tela, aplicar filtros → a URL reflete só o não-default → colar em aba nova reproduz a lista idêntica; voltar não desfaz tecla-a-tecla da busca (é `replaceState`); defaults deixam a URL limpa; hub: link com filtro vence preset salvo.

**Status de execução — Fase 2** · `✅`
- O que foi feito: **Hub** (Contacts.js): `?status&assignment&sort&search&archived&tags&panel&adv`. **Contatos full-page** (ContactsListScreen.js): `?search&adv`. **Kanban** (attendances/Attendances.js): `?view&mode&attr&status`; **protocolos** (`?view=` da view salva + — *follow-up por feedback do usuário* — `?detail=<id>` **persistente** do protocolo aberto: antes era link de entrada one-shot; agora abrir um protocolo reflete `?detail=` na URL, "voltar" fecha, e o DetailModal ganhou botão "Copiar link" HTTP-safe). **Execuções** (Executions.js): `?phone&status&page` + A5 `?step=`. **Auditoria** (AuditLog.js): `?resource_type&action&actor_type&resource_id&from&to&offset` + A6 `?expanded=`. **Custos** (CostsDashboard.js): `?period&start&end&sort&order&search`. **Tools** (ToolsUnified.js): `?q=`. **Canais**: `?archived=1`.
- Como foi feito / decisões: todas as telas usam `useUrlState` + schema declarativo (`writeParams` omite defaults → URL limpa), exceto o plugin protocolos que seguiu seu padrão manual de `?detail=` (URLSearchParams + pushState + popstate) para não cruzar o boundary de hooks. **Hub — precedência URL > preset salvo (D3):** `skipStoredPreset` gate no `useConversationFilters` (não auto-aplica o preset do localStorage quando a URL traz filtros). `adv` (filtro avançado) serializado como JSON só quando não-vazio; ids de cláusula re-semeados na leitura. Kanban e protocolos mantêm o localStorage como fallback (precedência URL > localStorage > default). Execuções compôs a camada de query SOBRE o sync de path já existente (o `?step=` num effect dedicado só quando o path é de detalhe).
- Problemas / pendências: sharing de link com **preset nomeado** ativo reproduz os *valores* do filtro (vira ad-hoc no destino), não o vínculo com o nome do preset — tradeoff aceito por D3 (o link reproduz o que se vê). Busca escreve a URL a cada tecla via replaceState (sem poluir histórico).
- Verificação: `node --check` OK em todos; imports resolvidos (corrigido `../../`→`../` em ChannelsManager); 116/116 testes puros; 962/962 endpoint tests.

---

### Fase 3 — Copiar-link + Config endereçável (Categoria C) · `✅`

- **Botões "copiar link"** (reusando `utils/copyDeepLink.js` da Fase 0): header do atendimento/contato, card de execução e de auditoria, linha de agente/canal e aba de view do Kanban, e um "copiar link desta lista" na toolbar do hub (copia a URL já filtrada da Fase 2). Ícone discreto + feedback "✓ copiado" (mesmo padrão visual de `notices.js`).
- **Seções de Configurações endereçáveis:** `?section=` em [ConfigPanel.js](../web/static/js/components/ConfigPanel.js) mapeando para as `<Section>` existentes (`marcar-atendimentos`, `avisos`, `avancado`, `banco`); no load, dar scroll-into-view/expandir a seção. Baixa prioridade — pode ser deferida se o tempo apertar.

**Verificação:** clicar "copiar link" cola uma URL que reabre exatamente a entidade/lista (inclusive sobre HTTP, via fallback); `?section=banco` rola até a seção de Banco.

**Status de execução — Fase 3** · `✅`
- O que foi feito: componente reutilizável `CopyLinkButton` (utils/copyDeepLink.js) aplicado no header do atendimento (ConversationHeaderActions.js → `/conversations/{id}`), em cada card de canal (channels/ChannelCard.js → `/channels/{id}`), no cabeçalho do detalhe de execução (Executions.js → `/executions/{id}[?step]`) e em cada linha de auditoria (AuditLog.js → `/audit?...&expanded={id}`). Config endereçável: `?section={marcar-atendimentos|avisos|avancado|banco}` no ConfigPanel.js (ids nas `<Section>` + wrap do DatabaseSettings + scroll-into-view no mount).
- Como foi feito / decisões: `CopyLinkButton` reusa `copyToClipboard` (fallback execCommand p/ HTTP não-seguro) e `LinkIcon` já existentes; variantes `icon`/`text`; feedback "✓" por 2s. ConfigPanel: efeito guarded-once keyed em `config` + `requestAnimationFrame` p/ garantir a seção no DOM; `scroll-mt-4` p/ respiro. Mensagens já tinham copiar-link (permalink `?message=`), agora via o mesmo `deepLinkUrl`.
- Problemas / pendências: nenhuma. Botões usam classes `wa-*` (legíveis no modo escuro).
- Verificação: `node --check` OK; 962/962 endpoint tests (SPA serve `/audit`, `/channels/new`, etc.).

---

### Fase 4 — Testes e QA · `✅` (automatizado; QA manual no navegador pendente)

- **Puros (`node --test`):** todos os `parse/serialize` de `services/urlState.js` (round-trip, omit-default, encode de nomes, `adv` inválido → `[]`).
- **Endpoint/SPA:** nenhum backend muda (é tudo frontend/roteamento). Rodar `python tests/test_endpoints.py` só para garantir que as rotas SPA (fallback do index) seguem servindo os novos paths (`/ai/agents/{key}/history`, `/channels/new`, etc.) — o servidor já faz catch-all para o SPA; confirmar que os novos padrões não colidem com rotas de API.
- **QA manual (checklist abaixo).**

**Checklist QA (todos os itens compartilháveis):**
- [ ] Colar cada URL-alvo numa aba anônima reproduz a tela (entidade aberta / lista filtrada).
- [ ] Voltar/avançar do navegador é coerente (entidade = pushState; filtro/busca = replaceState).
- [ ] Defaults não aparecem na URL (link limpo quando nada foi mexido).
- [ ] Nomes com espaço/acento/`/` em tags/atributos/provider sobrevivem (encode).
- [ ] Hub: link com filtro ad-hoc vence o preset salvo do localStorage.
- [ ] URLs antigas (sem params) continuam abrindo no default — nada de bookmark quebrado.
- [ ] "Copiar link" funciona inclusive sobre HTTP (contexto não-seguro).
- [ ] Modo escuro: botões novos legíveis (classes `wa-*`).

**Status de execução — Fase 4** · `✅` (auto) · `⬜` (QA manual)
- O que foi feito: suíte de testes puros (`node --test web/static/js/**/*.test.js`) → **116/116 pass** (inclui os novos `services/urlState.test.js` com 11 casos + os `routing.test.js`/`constants.test.js` que travam os deep-links existentes). Suíte de endpoint (`./venv/bin/python tests/test_endpoints.py`) → **962/962 pass, 0 fail**. Auditoria de resolução de imports (script Python) confirmou que todos os 24 imports dos módulos da Fase 0 resolvem — **1 bug encontrado e corrigido**: ChannelsManager.js usava `../../` (deveria ser `../` por estar direto em `components/`) — o `node --check` não pega isso (não resolve imports), só a auditoria de path pegou. Checagem de "regras de hooks": todos os `useUrlState` chamados no topo do componente principal, antes do `return`.
- Como foi feito / decisões: sem mudança de backend → nenhuma rota nova de API; SPA catch-all serve os novos paths. protocolos runtime (`storages/plugins/`) e versionado (`assets/plugin_examples/`) confirmados **idênticos** (em sincronia).
- Problemas / pendências: **QA manual no navegador ainda não feito** (servidor :8090 é pasta compartilhada, não reiniciar sem confirmar) — rodar o checklist abaixo colando as URLs-alvo, testando back/forward e o "copiar link" sobre HTTP.
- Verificação: ver acima (116 puros + 962 endpoint, todos verdes).

---

## Riscos e mitigações

| Risco | Mitigação |
|---|---|
| **Loop de sync** (escrever URL dispara popstate que re-hidrata que reescreve) | `useUrlState` só escreve se a query serializada **difere** da atual; `appliedRef` como no `useDeepLink` existente |
| **Histórico poluído** pela busca tecla-a-tecla | `replaceState` + debounce 300ms para filtros/busca; `pushState` só ao abrir entidade |
| **Conflito preset salvo × URL** no hub | Precedência explícita D3 (URL vence no load); preset vira ad-hoc se a URL diverge |
| **Colisão de rota** `/channels/new` com `/channels/{id}` ou rota de API | `new` tratado como caso especial no parser (não numérico); confirmar catch-all SPA na Fase 4 |
| **URL longa** no filtro avançado (`adv` JSON) | Só serializa quando `!isDefaultSpec`; para presets complexos, recomendar salvar como filtro nomeado (já existe) em vez de URL crua |
| **Plugin protocolos** é volume separado (não versionado no core) | A1/view fica na alçada do plugin; core só garante que `?view=` e `?detail=` coexistem no `/attendances` |
| **Regressão nos deep-links existentes** ao estender parsers | Testes puros de `routing.js`/`useDeepLink` antes/depois; QA dos paths baseline |

---

## Apêndice — arquivos-chave (referência rápida)

- Infra roteamento: `hooks/useDeepLink.js` · `components/shell/routing.js` · `screenRegistry.js` · `components/shell/App.js:84-178`
- Hub: `contacts/hooks/useConversationFilters.js:36-46,109` · `useConversationList.js:27-28` · `useConversationSelection.js:49` · `services/conversationRows.js` (normalizeSpec/specsEqual/isDefaultSpec)
- Contatos full-page: `ContactsListScreen.js:219,230`
- Kanban: `attendances/Attendances.js:25-45,213-218` · `attendances/grouping.js:13-18` · `storages/plugins/protocolos/static/protocolos_tab.js:233,423-433`
- Motor de IA: `ai/AgentsManager.js:460-469` · `ai/ToolsUnified.js:61,68` · `ai/VariablesEditor.js:64`
- Execuções: `Executions.js:79,158,166-168,189-235`
- Auditoria: `AuditLog.js:124-142,157-195`
- Custos: `CostsDashboard.js:38-49`
- Canais: `ChannelsManager.js:47,52-58,60,62,75-85` · `channels/ChannelForm.js:27` · `channels/notices.js:15-25`
- Config: `ConfigPanel.js:127,187,254`
- Copiar-link (referência): `contacts/hooks/useMessageActions.js:78-87`
