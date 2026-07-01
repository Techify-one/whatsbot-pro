# Plano: Corrigir deep-linking de URLs no WhatsBot

## Contexto

O WhatsBot tem deep-linking parcial: várias telas refletem seu estado abrível na URL (recarregar/compartilhar/voltar reabre o mesmo estado), mas muito do **estado de tela continua preso em `useState`/`localStorage`** e nunca toca `window.location`. O commit `8a20d58` ("implement deep linking") cobriu um conjunto de telas via o hook `useDeepLink` + o registry `ENTITY_ROUTES` + rotas SPA no backend, e a decomposição do Plano 23 **não regrediu** nenhuma dessas (verificado tela a tela). O problema é o que ficou de fora — estado novo e estado que `8a20d58` deliberadamente não cobriu.

O exemplo trazido pelo usuário é o caso mais visível: no **kanban/lista de Atendimentos do plugin `atendimentos`** (override da rota `/attendances`), clicar num card/linha abre o **modal de detalhe do atendimento** mas a URL continua `/attendances`, **sem o id** — recarregar, compartilhar ou clicar "voltar" fecha o modal e perde o atendimento. Pior: a infra atual estruturalmente **impede** o fix porque o `ScreenRouter` não entrega nada da URL ao componente de override do plugin (`ScreenRouter.js:43-52` passa só `apiBase/tab/setTab`), e `attendances` não está em `ENTITY_ROUTES`.

Este documento **audita e prioriza** os gaps confirmados (cada um com `file:line`) e propõe o esquema de URL coerente com o padrão estabelecido. Não inclui código de implementação.

---

## O padrão a seguir

Qualquer fix deve respeitar o mecanismo do `8a20d58`. As três camadas:

1. **Hook `useDeepLink`** — `web/static/js/hooks/useDeepLink.js`. Registry `ENTITY_ROUTES` (cada entrada `{tab, base, parse(rest), build(sel)}`); `entityFromPath()` resolve `{tab, sub?, id?}` a partir do **pathname**; `useDeepLink({tab, resolve, ready, open})` faz `history.pushState` ao selecionar e reabre no mount/popstate. Modelo de seleção: `{sub?, id?}` (sub = sub-aba fixa, id = identificador natural). **Limitação atual confirmada**: o hook só lê o pathname — `window.location.search` é descartado em todo o fluxo (`selKey` l.104, `push` l.133).

2. **Roteamento puro** — `web/static/js/components/shell/routing.js`. `CORE_ROUTES`, `CORE_TAB_PATHS`, `tabFromPathPure`, parsers path-based (`contactIdFromPathname`, `conversationIdFromPathname`) e o único query-param hoje lido: `scrollMsgFromSearchStr` (permalink `?message=<id>`, l.111-116).

3. **Wiring** — `web/static/js/components/shell/App.js`: `initialEntity` + `entFor(tab)` (l.367) injeta o deep-link na tela dona; handler de `popstate` re-resolve tudo; `setTab` faz `pushState` + `dispatchEvent(popstate)`.

4. **Backend SPA** — `server/app.py` função `index()` registra rotas por entidade (todas servem o mesmo `index.html`); `auth_middleware` (l.467-470) isenta os **prefixos** dessas rotas; `_SPA_PATHS` (l.450-457) lista as **bases exatas**. **Query string nunca afeta o roteamento** (o middleware compara `request.url.path`, l.461) — logo qualquer `?foo=` numa base já servida funciona **sem mudança de backend**. Já um **novo segmento de path** (`/x/<id>`) exige rota + isenção de auth novas, senão hard-reload dá 404/401.

**Regra de decisão por gap:** identidade natural de entidade → `useDeepLink` (path); filtro/busca/range/paginação/aba-secundária → **query-param**; sub-estado dentro de tela de plugin → infra nova (ver §4).

---

## Inventário de gaps

Todos os gaps abaixo foram **verificados** contra o código. Ordenados por prioridade.

### Prioridade ALTA

| Tela / Estado | Arquivo | O que falta | URL proposta | Padrão | Backend | Esforço |
|---|---|---|---|---|---|---|
| **Infra: util de query-param compartilhado** (hoje só `?message=` hard-coded) | `routing.js:106-116`; `screenRegistry.js:24`; `App.js:104,172` | `getQueryParam/getQueryInt` + `setQueryParams` (merge preservando pathname; `replaceState` p/ filtro, `pushState` p/ aba) | — (habilitador) | query-param | nenhuma | S |
| **Infra: query-params em `ENTITY_ROUTES`** | `useDeepLink.js:22-141` | Estender contrato p/ `parse(rest, query)`/`build(sel)->{path, query?}`; `selKey()` incluir query canonicalizada | — (habilitador) | useDeepLink | nenhuma | M |
| **Infra: tripla-sincronização backend** (auth_middleware ∪ `_SPA_PATHS` ∪ decorators `index()`) | `server/app.py:444-470,553-623` | Derivar prefixos isentos + rotas SPA de UMA tabela declarativa; hoje 3 listas à mão; `/attendances/` falta na isenção | N/A | new-infra | refactor `server/app.py` | M |
| **Atendimentos (plugin) — modal de detalhe** (GAP PRINCIPAL do usuário) | `atendimentos_tab.js:112,183-186,276,354,386-424` | `at.id` do atendimento aberto não vai à URL | `/attendances?atendimento=<atid>` (ou `/attendances/<atid>` via entidade) | query-param | nenhuma p/ query; rota+isenção `/attendances/` p/ path | M |
| **CostsDashboard — período** (24h/3d/7d/30d/all/custom) | `CostsDashboard.js:38,116-126,59-77` | `period` reseta a 'all' no reload | `/costs?period=7d` | query-param | nenhuma | S |
| **CostsDashboard — range custom (De/Até)** | `CostsDashboard.js:39-42,62-64,128-157` | `customStart/EndDate/Time` perdidos no reload | `/costs?period=custom&from=<epoch>&to=<epoch>` | query-param | nenhuma | M |
| **AuditLog — filtros aplicados** (recurso/ação/ator/resource_id/range) | `AuditLog.js:124-132,156-165,184-195,50-58` | snapshot `applied` não vai à URL | `/audit?resource_type=&action=&actor_type=&resource_id=&from=&to=` | query-param | nenhuma | M |

### Prioridade MÉDIA

| Tela / Estado | Arquivo | O que falta | URL proposta | Padrão | Backend | Esforço |
|---|---|---|---|---|---|---|
| **Executions — filtro de status** | `Executions.js:168,263-272,177,221-236` | `filterStatus` (+ popstate só lê pathname) | `/executions?status=failed` | query-param | nenhuma | S |
| **Executions — migrar p/ `useDeepLink`** (consolidar popstate inline) | `Executions.js:157-236` | lógica própria fora de `ENTITY_ROUTES` | `/executions/{id}` (ok) + `?page&phone&status` na lista | useDeepLink | nenhuma | M |
| **AI (agents/prompts/tools) — modal de Histórico/rollback** | `AgentsManager.js:324-326,397-411`; `PromptsEditor.js:144-146`; `ToolsUnified.js:68-70,239-243,382-390` | `historyFor` (entidade + versão) | `/ai/<sub>/<id>/history` (3º segmento) ou `?history=<key>&version=<n>` | new-infra | rota `/ai/{sub}/{id}/{action}` p/ path; nenhuma p/ query | M |
| **AuditLog — paginação** | `AuditLog.js:139,156-165,225-226,337-347` | `offset` reseta a 0 | `/audit?...&offset=100` | query-param | nenhuma | S |
| **ChannelsManager — painel QR Conectar** (canal GOWA) | `ChannelsManager.js:57-58,170-172,197-199,273-276` | `connectFor` (qual canal pareando) | `/channels/<id>/connect` (sub-path) | useDeepLink | rota 2 segmentos `/channels/{id}/{action}` | M |
| **Hub de conversas — painel lateral de info** (contact/conversation/null) | `useConversationSelection.js:49,166-171,234` | `openPanel` reseta a null na troca | `/conversations/<id>?info=conversation\|contact` | query-param | nenhuma | M |
| **Hub de conversas — filtro salvo (preset)** | `useConversationFilters.js:24,46,92-104,133-142,192-199` | `activeFilterId` só em localStorage | `/?filter=<presetId>` (na lista) | query-param | nenhuma | M |
| **Atendimentos (plugin) — view/group-by** (kanban/lista, status/atendente) | `atendimentos_tab.js:16-19,106-107,113-114,289-291,339-342` | `mode`/`kgroup` só em localStorage | `/attendances?view=kanban&group=atendente` | query-param | nenhuma | S |
| **Atendimentos (plugin) — filtros** (status/busca/datas/preset) | `atendimentos_tab.js:99-105,133-152,176-181,324-348` | `status/q/from/to/preset` só na chamada REST | `/attendances?status=&q=&from=&to=&preset=` | query-param | nenhuma | M |
| **Atendimentos CORE — view (board/list)** | `attendances/Attendances.js:33,260,265-277` | `view` só em localStorage | `/attendances?view=list` | query-param | nenhuma | S |
| **Atendimentos CORE — group-by** (assignee/stage/label/status) | `attendances/Attendances.js:34,151-154,255-261,279-282`; `grouping.js:13-18` | `mode` só em localStorage | `/attendances?group=label` | query-param | nenhuma | S |
| **Atendimentos CORE — filtro "Só abertos"** | `attendances/Attendances.js:35,104-117,283-286` | `onlyOpen` nem localStorage (filtro de servidor real) | `/attendances?open=1` | query-param | nenhuma | S |
| **Deep-link DENTRO de tela de plugin (sub-path)** | `server/app.py:444-449,614-623` | `_PLUGIN_SPA_PATHS` só registra path exato | `/<screen>?item=5` (recom.) ou `/<screen>/items/5` (wildcard) | plugin-subpath | nenhuma p/ query; rota wildcard + isenção p/ path | M |

### Prioridade BAIXA

| Tela / Estado | Arquivo | O que falta | URL proposta | Padrão | Backend | Esforço |
|---|---|---|---|---|---|---|
| **CostsDashboard — busca + ordenação** | `CostsDashboard.js:47-49,83-107,219-259` | `search/sortField/sortAsc` (mesmo PR do período) | `/costs?q=&sort=cost_usd&dir=desc` | query-param | nenhuma | M |
| **AuditLog — linha expandida (diff)** | `AuditLog.js:142,322-327,76-120` | `expandedId` (permalink frágil: sem GET de registro único) | `/audit?...&row=<id>` | query-param | nenhuma (permalink robusto exigiria endpoint) | M |
| **Executions — filtro por telefone** | `Executions.js:167,256-262,176` | `filterPhone` | `/executions?phone=5511...` | query-param | nenhuma | S |
| **Executions — paginação** | `Executions.js:166,171,175,247,320-334` | `page` (lista auto-refresh 5s) | `/executions?page=2` | query-param | nenhuma | S |
| **Hub de conversas — filtros ad-hoc + busca + arquivados** | `useConversationFilters.js:36-40`; `useConversationList.js:27-28,97-104`; `Contacts.js:113-118` | `statusFilter/assignmentTab/sortBy/tagFilter/search/showArchived` (advFilters fora) | `/?q=&status=&assign=&sort=&tags=&archived=1` | query-param | nenhuma | L |
| **Atendimentos (plugin) — sub-aba do painel do chat** | `panel.js:36,140-152`; `ConversationInfoPanel.js:258` | `tab` ('atual'/'historico') no slot | `/conversations/<id>?atendimento_tab=historico` | query-param | nenhuma (slot não propaga URL hoje) | S |
| **Atendimentos CORE — stageAttrKey** (eixo do kanban por etapa) | `attendances/Attendances.js:27,45,86-100,262,279-282` | `stageAttrKey` só em localStorage | `/attendances?view=board&group=stage&stage=<key>` | query-param | nenhuma | S |
| **ContactsListScreen — busca + página da lista** | `ContactsListScreen.js:192-193,331-350,423-429` | `search/page` (detalhe `/contacts/<id>` já coberto) | `/contacts?q=&page=2` | query-param | nenhuma | M |
| **Sandbox — telefone do chat de teste** | `Sandbox.js:107-108,116-133,188-194` | `phone/activePhone` hardcoded | `/sandbox?phone=5511...` | query-param | nenhuma | S |
| **AI Tools — busca/filtro** | `ToolsUnified.js:61,134-143,288-295` | `query` local | `/ai/tools?q=<termo>` | query-param | nenhuma | S |
| **Forms "Novo" (6 telas)** — channels/custom-attributes/quick-replies/roles/users/tools | `ChannelsManager.js:47`; `CustomAttributesManager.js:202`; `QuickReplies.js:90`; `RolesManager.js:151`; `UsersManager.js:240`; `ToolsUnified.js:65` | `creating=true` não vai à URL (reabre só form vazio) | `/<base>?new=1` (`/custom-attributes/<scope>?new=1`) | query-param | nenhuma | S (cada) |
| **AI — form criar/duplicar** | `AgentsManager.js:321,377-382`; `PromptsEditor.js:142`; `VariablesEditor.js:69`; `ToolsUnified.js:65` | `creating` (e seed de duplicar) | `/ai/<sub>?new=1&from=<key>` | query-param | nenhuma | M |
| **Configurações — seção ativa** (Marcar/Avisos/Avançado/Banco) | `ConfigPanel.js:127,187,254,331` | scroll-to-section não identificável | `/dashboard#secao` (hash-anchor) | query-param* | nenhuma | S |
| **Telegram (config) — canal selecionado** | `telegram.js:36-67,131-148` | `channelId` (useState puro) | `/plugins/telegram?channel=<id>` | new-infra | nenhuma (rota); falta infra no core (§4) | M |
| **GOWA (config) — canal selecionado** | `gowa.js:34-101,124-150` | `channelId` (useState puro) | `/plugins/gowa?channel=<id>` | new-infra | nenhuma (rota); falta infra no core (§4) | M |
| **WhatsApp Cloud (config) — channel ID** | `whatsapp_cloud.js:38-86,110-122` | `channelId` (localStorage; nunca segredos) | `/plugins/whatsapp_cloud?channel=<id>` | new-infra | nenhuma (rota); falta infra no core (§4) | M |
| **PluginScreen não entrega/observa deep-link** | `PluginScreen.js:14-63` | só passa `apiBase/screen/can/currentUser` | contrato `{deepLink, setDeepLink}` | plugin-subpath | nenhuma p/ query | M |
| **Channels arquivados expandido** | `ChannelsManager.js:52-53,311-336` | `showArchived` (disclosure de UI) | `/channels?archived=1` | query-param | nenhuma | S |
| **AI Tools — modal de Histórico** | `ToolsUnified.js:68,239-243,354-356,382-390` | `historyFor` (3º segmento em `/ai`) | `/ai/tools/<name>/history` | new-infra | rota `/ai/{sub}/{id}/{action}` | M |

\* `pattern` mais exato seria **hash-anchor** (`#secao`); o enum não tem essa opção, então fica como `query-param` (refinamento intra-tab, sem `pushState`, sem backend). É polimento de navegação — candidato legítimo a **não fazer**.

**Falsos positivos descartados** (não são gaps de deep-link): customizador de Colunas da ConversasTable (`conversas_table.js` — preferência per-device em localStorage + dropdown efêmero); step/anchor da timeline de Executions (renderiza todos de uma vez, sem seleção); ConfirmModal de exclusão + overlay "Reiniciar worker" em AI (modais efêmeros); paginação "ver mais" por coluna do kanban (`BoardColumn.js:13`, efêmero); PasswordModal de reset de senha, modais Novo contato/Importar, modal de Logs do Sandbox (efêmeros).

---

## Mudanças de infraestrutura

### Frontend — `web/static/js/hooks/useDeepLink.js`
- **Estender o contrato de `ENTITY_ROUTES`**: `parse(rest, query) -> {sub?, id?, query?}` e `build(sel) -> {path, query?}` onde `query` é serializado via `URLSearchParams`. `selKey()` (l.104) passa a incluir a query **canonicalizada** (chaves ordenadas) para o diff URL↔estado funcionar; `push()` (l.133) escreve `path + '?' + qs`. Retrocompat: entradas sem `query` permanecem `{sub,id}`-only. Cuidado: o `useEffect` em `[k, ready]` (l.131) precisa que `k` inclua a query e `push()` deve atualizar `appliedRef` com a query (senão loop de re-push).
- **Novas entradas potenciais**: `attendances` (se optar pelo esquema de entidade `/attendances/<atid>`); `executions` (consolidar a lógica inline em `Executions.js:157-236`); extensão de `ai` para 3 segmentos (`/ai/<sub>/<id>/history`).

### Frontend — `web/static/js/components/shell/routing.js`
- Criar **util genérico de query-params** (hoje só `scrollMsgFromSearchStr` l.111 existe, hard-coded para `message`): `getQueryParam(search, key)` / `getQueryInt(search, key)` (leitura pura, testável via `node --test` — `routing.test.js:91-93` já cobre o `message`); e um writer `setQueryParams(updates)` que mescla params preservando o pathname. **Decisão por param**: `replaceState` para filtro/busca/paginação (não polui histórico); `pushState` para aba/painel/seleção (merece entry de histórico).
- `App.js` re-resolve no `popstate` exatamente como já faz com `initialScrollMsgId` (l.172).

### Backend — `server/app.py`
- **Eliminar a tripla-sincronização manual**: hoje a tupla de prefixos isentos do `auth_middleware` (l.467-470), o set `_SPA_PATHS` (l.450-457) e os decorators de `index()` (l.553-594) são três listas independentes. Derivar prefixos isentos + rotas SPA de **uma constante declarativa** (`SPA_ENTITY_PREFIXES`, espelhando `ENTITY_ROUTES` do frontend) — adicionar uma entidade vira **1 edição**.
- **Rotas novas necessárias** (apenas para esquemas de **path**, não para query-param): `/attendances/{id:int}` + `/attendances/` na isenção de auth (hoje **ausente**, l.467-470); `/channels/{id}/{action}` (2 segmentos, hoje só 1 em l.584); `/ai/{sub}/{entity_id}/{action}` (3 segmentos, hoje 2 em l.582). Para todos os esquemas **query-param**, nenhuma mudança de backend.

### Estratégia para deep-link DENTRO de telas de plugin
A limitação é dupla: (1) `_PLUGIN_SPA_PATHS` (l.444-449) registra apenas o **path exato** da screen — `/<screen>/items/5` dá 404; (2) `PluginScreen.js:62` entrega só `apiBase/screen/can/currentUser`, sem nenhum sinal de URL ao componente.

- **Recomendado: query-param** (`/<screen>?item=5`). A rota base **já** serve `index.html` com qualquer `?` — **zero backend**. Falta só `PluginScreen` entregar um contrato `{ deepLink: {query, rest}, setDeepLink(updates) }` ao componente, usando o util de query (§routing.js), assinando `popstate` e repassando a query atualizada como prop. Esse contrato faz parte da plugin frontend API — **versionar via `frontend_api_version`**. Resolve os 3 plugins de canal com um **único `?channel=`** reaproveitável.
- **Sub-path (`/<screen>/items/5`)** só se um plugin precisar de hierarquia real: registrar `f'{path}/{{rest:path}}'` no loop (l.622) + isentar o prefixo `screen_path + '/'` no `auth_middleware` (a isenção `/plugins/` atual cobre só os assets estáticos, **não** o path da screen).
- Para o **override de rota core** (caso Atendimentos), o `ScreenRouter` (l.43-52) precisa passar uma prop nova (param lido de `window.location.search` ou `initialEntity`) ao `activeRouteOverride.component` — hoje `entFor` (passado em l.412) é ignorado nesse ramo.

---

## Fases de implementação

### Fase 1 — Infra base + exemplo do usuário
**Objetivo:** destravar query-params e resolver o GAP PRINCIPAL (modal de Atendimentos).
**Itens:**
- Util de query-param em `routing.js` + casos em `routing.test.js`.
- Extensão de `ENTITY_ROUTES` para `query` no `useDeepLink`.
- Refactor declarativo da tripla-sincronização em `server/app.py` (constante única) — incluindo `/attendances/`.
- `ScreenRouter` passar sinal de URL ao componente de override; plugin `atendimentos` ler `?atendimento=<atid>` no mount/popstate, `pushState` em `openDetail`/`onClose`.
**Pronto quando:** recarregar `/attendances?atendimento=<id>` reabre o modal; voltar/avançar abre/fecha; compartilhar o link funciona em outro device; hard-reload não dá 401/404.

### Fase 2 — Telas core de alto valor
**Objetivo:** filtros analíticos compartilháveis.
**Itens:** CostsDashboard (período + range custom; busca/ordenação no mesmo PR); AuditLog (filtros aplicados + paginação); Executions (status + migrar para `useDeepLink`, consolidando o popstate inline).
**Pronto quando:** "link para custo dos últimos 7 dias", "link das ações do usuário X no período" e "link das execuções que falharam" reabrem com os filtros aplicados; auto-refresh de Executions (5s) não sobrescreve filtro vindo da URL.

### Fase 3 — Sub-estados / filtros de visão
**Objetivo:** painéis e visões de trabalho compartilháveis.
**Itens:** Hub de conversas (`?info=`, `?filter=<preset>`, e — se desejado — `?q/status/assign/sort/tags/archived`); Atendimentos CORE (`view/group/open/stage`); Atendimentos plugin (`view/group` + filtros); AI Histórico/rollback (decidir 3º segmento vs `?history=`); restante de Executions (`phone/page`); ContactsListScreen (`q/page`); Sandbox (`?phone=`); AI Tools busca.
**Cuidado escopo:** filtros de lista vivem na URL `/` (não em `/conversations/<id>`, que **descarta** a query no `pushState`, `useConversationSelection.js:103`); `advFilters` ficam fora da query (usar preset → `?filter=`).
**Pronto quando:** cada filtro/painel sobrevive a reload e back/forward, escopado à URL certa.

### Fase 4 — Plugins + baixa prioridade
**Objetivo:** contrato de deep-link para telas de plugin + polimentos.
**Itens:** contrato `{deepLink, setDeepLink}` em `PluginScreen` (versionado); `?channel=` único nos 3 plugins de canal; ChannelsManager `/channels/<id>/connect`; forms "Novo" (`?new=1`) das 7 telas; AI criar/duplicar; AuditLog linha expandida; Configurações seção ativa (avaliar fazer); Channels arquivados; AI Tools histórico.
**Pronto quando:** o contrato de plugin está documentado e versionado; canais/telas de setup reabrem o canal/registro certo; itens explicitamente marcados "não implementar" (channels arquivados, AI Tools histórico, seção de Configurações, WhatsApp Cloud `?channel=`) ficam decididos.

---

## Riscos e cuidados

- **Colisão de rota:** todos os `?new=/?archived=/?q=/?status=` etc. são query-params em bases existentes — não colidem com `CORE_ROUTES`, `ENTITY_ROUTES` nem decorators de `index()` (verificado; único query-param em uso é `?message=`, escopo contatos/conversas). **Evitar `/<base>/new` como segmento de path**: `new` colidiria com parse de id (`/channels/new`, `/quick-replies/new` via `:path`, `/ai/tools/new` → `entity_id='new'` no-op, `/users/new` → 404 por `:int`). Usar sempre query-param para "Novo".
- **Popstate loops:** ao estender `selKey()`/`push()` com query, o `appliedRef` precisa incluir a query, senão o `useEffect [k,ready]` re-empurra em loop. Em telas que semeiam estado da URL (ex.: `openPanel` a partir de `?info=`), semear só no mount/popstate — o `pushState` de seleção client-side (`useConversationSelection.js:103`) **descarta** a query.
- **Plugins:** override de rota core (`ScreenRouter.js:43-52`) e `PluginScreen.js:62` não entregam URL hoje — exigem prop nova; sem isso, o sub-estado do plugin não é deep-linkável. Slot `conversation.info.panel` (`ConversationInfoPanel.js:258`) não propaga query — sub-aba do painel é baixa prioridade.
- **SSR/reload:** todo esquema de **path** novo precisa das 3 edições no backend (isenção + base + decorator) ou hard-reload dá 401/404. Query-param não precisa. Hash-anchor (`#secao`) nunca chega ao servidor.
- **Encoding de ids:** `attribute_key` valida `^[a-z][a-z0-9_]*$` (`custom_attributes.py:26`) — seguro sem encoding; telefones/short_codes podem precisar de `encodeURIComponent`; epoch em segundos mantém URL curta (Costs/Audit).
- **Auto-refresh:** Executions re-busca a cada 5s do closure de `fetchList` — a migração não pode sobrescrever filtro vindo da URL; ordem por recência faz `?page=N` mudar de conteúdo (baixo valor).
- **Segredos:** nunca colocar `access_token`/`app_secret`/credenciais na URL (WhatsApp Cloud) — só o `channel_id`; o rascunho fica no localStorage.

---

## Checklist de verificação

Para **cada** fix aplicado:

- [ ] **Reload reabre o estado?** Abrir o estado (modal/filtro/aba), copiar a URL, F5 → o mesmo estado reabre.
- [ ] **Back/forward?** Abrir A → abrir B → "voltar" volta a A → "avançar" volta a B, sem loop e sem entradas espúrias (filtro = `replaceState`, aba/seleção = `pushState`).
- [ ] **Compartilhar link?** Colar a URL em outra aba/janela anônima/device → reabre o mesmo estado (não cai no default local).
- [ ] **Auth middleware serve a rota?** Hard-reload da URL profunda **deslogado e logado** → serve `index.html`, não 401/404 (verificar prefixo na isenção `server/app.py:467-470` para esquemas de path).
- [ ] **Sem colisão?** A URL não casa por engano outra rota (`/x/new` vs `/x/<id>`); query-param novo não conflita com `?message=`.
- [ ] **localStorage como fallback:** quando o param está ausente, mantém a preferência per-device (URL vence quando presente).
- [ ] **Backend tests:** estender `tests/test_endpoints.py` (196 checagens, cobre rotas SPA) ao adicionar prefixo/rota nova; `routing.test.js` para os utils de query (`node --test`).
- [ ] **Plugin:** override/`PluginScreen` recebe e observa a URL; contrato versionado (`frontend_api_version`).
