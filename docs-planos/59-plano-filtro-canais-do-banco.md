# Plano 59 — Opções do filtro "Canais" vêm do banco, não das linhas carregadas

> **Status:** ✅ IMPLEMENTADO (2026-07-18) · **Data:** 2026-07-18 · **Escopo:** pequeno
> **Execução:** P1=(a) endpoint novo `GET /api/channels/for-filter`; P2=(a) fetch em `useConversationList`. Suíte: +5 checks verdes (1275 passed / 8 failed — as 8 falhas são pré-existentes de busca acento/collation do servidor de teste, sem relação com este plano). `node --test` verde (56 tests).
> **Origem:** bug reportado pelo usuário no hub de atendimentos ("quando tem muitas conversas, o filtro de Canais não mostra os outros canais"). **Método:** leitura direta do caminho `channelOptions → ConversationFilterDialog` + `grep`, tudo com `arquivo:linha` verificado.
> As opções do dropdown "Canais" (em *Filtrar conversas*) são **derivadas das linhas de conversa carregadas na sidebar** ([useConversationList.js:61-73](../web/static/js/components/contacts/hooks/useConversationList.js#L61)), e a sidebar é capada em **200 conversas mais recentes** pelo backend ([conversations.py:104](../server/routes/conversations.py#L104)). Um canal cujas conversas caem fora dessa janela — ou que só tem conversa na *outra* view (caixa × arquivadas) — **some do filtro**. O objetivo é trocar a fonte das opções para o **banco** (lista de canais), mantendo o casamento `id`↔`channel_id` e **sem tocar** no badge por-linha (`showChannel`/`distinctChannelCount`, plano 56).
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | As opções de filtro **não** podem depender das linhas visíveis — vêm do banco ✅ (2026-07-18) | `channelOptions` deixa de ser derivado de `contacts`; passa a vir de um fetch de canais. |
| D2 | **NÃO** mexer no badge de canal por-linha (`showChannel`/`distinctChannelCount`, plano 56) | Aquilo é uma heurística proposital sobre as linhas (latch do máximo visto). Só a **opção de filtro** migra pro banco. |
| D3 | Só o filtro de **Canais** é linha-derivado — os demais já vêm do banco (agentes/tags/etiquetas/atributos) ou são enums fixos | Escopo mínimo: não reabrir os outros filtros (ver §3 "Falsos positivos"). |
| D4 | Mudança **aditiva** e retrocompatível; o hub em produção não pode quebrar | Manter o shape `{id,label}` que o dialog já consome; `showChannel` intacto; degradação silenciosa se o fetch falhar. |
| D5 | Casamento de valor é por `id` textual — a opção precisa bater com `(c.channel_id \|\| 'default')` | O `id` da tabela `channels` é `Text` PK (`"default"`, etc.), mesmo id-space de `conversations.channel_id`. Nenhum mapeamento novo. |

---

## 1. Resumo executivo

No hub de atendimentos, o construtor *Filtrar conversas* recebe a lista de canais via prop `channels`, que hoje é o `channelOptions` **montado varrendo o array `contacts`** ([useConversationList.js:61-73](../web/static/js/components/contacts/hooks/useConversationList.js#L61)). Como `contacts` é o resultado de `listConversations({limit:200})` ([useConversationList.js:84](../web/static/js/components/contacts/hooks/useConversationList.js#L84)) e o backend trava em `min(limit,200)` ([conversations.py:104](../server/routes/conversations.py#L104)), as opções refletem só os canais presentes nas 200 conversas mais recentes da view atual. A correção é pontual: **trocar a fonte de `channelOptions` por um fetch de canais do banco** (mapeado para `{id,label}`), preservando o casamento `id`↔`channel_id` ([conversationRows.js:65](../web/static/js/services/conversationRows.js#L65)) e deixando `showChannel`/`distinctChannelCount` como estão. O padrão já existe no próprio construtor — as etiquetas de conversa (`convLabelNames`) são carregadas exatamente assim, por fetch ([ConversationFilterBar.js:257-268](../web/static/js/components/contacts/ConversationFilterBar.js#L257)).

O único ponto de projeto é **qual endpoint** alimenta o filtro (§4 + P1): `/api/channels` é completo mas exige `channel.manage`; `/api/channels/connected` é de baixo privilégio mas só devolve canais logados/iniciáveis (perde canal desconectado/arquivado que ainda tem histórico). A recomendação é um **endpoint irmão de baixo privilégio** que devolve TODOS os canais (id/provider/display_name), espelhando o precedente do `/connected`.

---

## 2. Como funciona hoje (mapa)

| Peça | Onde | Comportamento atual |
|------|------|---------------------|
| ⚠️ **Origem do bug** — opções de canal | [useConversationList.js:61-73](../web/static/js/components/contacts/hooks/useConversationList.js#L61) | `channelOptions = useMemo(...)` varre `contacts` e monta 1 opção por `channel_id` visto. Comentário assume: "Derivado das próprias linhas… sem fetch extra". |
| ⚠️ Teto que causa a perda | [useConversationList.js:84](../web/static/js/components/contacts/hooks/useConversationList.js#L84) → [conversations.py:104](../server/routes/conversations.py#L104) | `listConversations({archived, limit:200})`; backend `limit = max(1, min(limit, 200))`. `contacts` nunca tem mais que as 200 conversas mais recentes por view. |
| Badge por-linha (NÃO mexer) | [useConversationList.js:54-57](../web/static/js/components/contacts/hooks/useConversationList.js#L54), [conversationRows.js:343-346](../web/static/js/services/conversationRows.js#L343) | `showChannel` = latch de `distinctChannelCount(contacts)` (por `channel_provider`). Heurística de exibição do chip por linha — plano 56. Independente do filtro. |
| Fluxo da prop `channels` | [Contacts.js:82](../web/static/js/components/contacts/Contacts.js#L82),[:269](../web/static/js/components/contacts/Contacts.js#L269) → [ContactList.js:130](../web/static/js/components/contacts/ContactList.js#L130),[:480](../web/static/js/components/contacts/ContactList.js#L480) → [ConversationFilterBar.js:237](../web/static/js/components/contacts/ConversationFilterBar.js#L237),[:537](../web/static/js/components/contacts/ConversationFilterBar.js#L537) | `channelOptions` sobe como `channels` até o construtor e os chips. |
| Consumo no construtor | [ConversationFilterDialog.js:173-176](../web/static/js/components/contacts/ConversationFilterDialog.js#L173) | `channels.map(c => ({value: c.id, label: c.label}))` → checkboxes multi-select. **Espera `{id,label}`**. |
| Consumo nos chips aplicados | [ConversationFilterBar.js:53-55](../web/static/js/components/contacts/ConversationFilterBar.js#L53),[:306](../web/static/js/components/contacts/ConversationFilterBar.js#L306) | `_channelLabel(channels, value)` acha o label do canal p/ a "pílula" do filtro ativo. Mesma lista incompleta ⇒ chip cai no `id` cru quando o canal não está nas linhas. |
| ✅ Casamento do valor | [conversationRows.js:65](../web/static/js/services/conversationRows.js#L65) | `if (dim === 'channel') return (c.channel_id \|\| 'default') === value;` — o valor da opção precisa ser o `id` textual do canal. |
| ✅ Origem do `channel_id` da linha | [conversationRows.js:409-411](../web/static/js/services/conversationRows.js#L409),[:462](../web/static/js/services/conversationRows.js#L462),[:496](../web/static/js/services/conversationRows.js#L496) | `channel_id: cv.channel_id \|\| 'default'`. Mesmo id-space da coluna `channels.id`. |
| ✅ Padrão de fetch já usado no construtor | [ConversationFilterBar.js:257-268](../web/static/js/components/contacts/ConversationFilterBar.js#L257) | `convLabelNames` carrega via `getConversationLabels()` num `useEffect` (load + refresh ao abrir + evento global). É o **molde** a copiar para os canais. |
| Endpoint completo (privilégio alto) | [channels.py:35-40](../server/routes/channels.py#L35) → [channel_service.py:226-233](../app/services/channel_service.py#L226) | `GET /api/channels` gate `channel.manage`; `list_all(include_archived=False)` (enabled **e** disabled, sem arquivados) + `serialize` (id/provider/display_name/enabled/archived + creds mascaradas). Arquivados via `?archived=true` → `list_archived` ([channel_repo.py:41](../db/repositories/channel_repo.py#L41)). |
| Endpoint leve (baixo privilégio) | [channels.py:42-54](../server/routes/channels.py#L42) → [channel_service.py:236-288](../app/services/channel_service.py#L236) | `GET /api/channels/connected` gate `conversation.reply`; devolve `{id,provider,display_name,own_phone,contact_type}` **só** de canais `enabled` + `logged_in` + `can_initiate` (exclui disabled, desconectado, widget, arquivado). |
| API client (frontend) | [api.js:640-646](../web/static/js/services/api.js#L640) | `listChannels()` → `/api/channels`; `listArchivedChannels()` → `/api/channels?archived=true`. (Não há client p/ `/connected` de lista genérica — só o do picker.) |

**Diagnóstico:** o par (opções derivadas das linhas) × (linhas capadas em 200 + split caixa/arquivadas) explica exatamente os prints: menos conversas visíveis ⇒ menos opções de canal.

---

## 3. Inventário / análise

| # | Item | Ponto de mudança (`arquivo:linha`) | O que falta | Abordagem | Risco | Esforço |
|---|------|-----------------------------------|-------------|-----------|-------|---------|
| I1 | Fonte de baixo privilégio dos canais (backend) | [channels.py:42](../server/routes/channels.py#L42), [channel_service.py:236](../app/services/channel_service.py#L236) | Não há endpoint leve que liste **todos** os canais (incl. disabled/arquivado) p/ o filtro | Endpoint irmão do `/connected`, gate `conversation.reply`, devolve `{id,provider,display_name}` de **todos** os canais — ver §4 e **P1** | Baixo | S |
| I2 | Trocar a fonte de `channelOptions` (frontend) | [useConversationList.js:61-73](../web/static/js/components/contacts/hooks/useConversationList.js#L61) | `useMemo` sobre `contacts` → fetch do banco | `useState`+`useEffect` (molde `convLabelNames`) mapeando cada canal para `{id, label}`; manter o **nome exportado** `channelOptions` p/ zero ripple downstream | Baixo | S |
| I3 | Rótulo estável dos chips do filtro | [ConversationFilterBar.js:53-55](../web/static/js/components/contacts/ConversationFilterBar.js#L53) | Chip cai no `id` cru p/ canal fora das linhas | Resolvido **de graça** por I2 (a mesma lista completa alimenta `_channelLabel`) | Baixo | — |
| I4 | Cobertura do teste | [tests/test_endpoints.py:1154](../tests/test_endpoints.py#L1154) | Não há teste do novo endpoint nem asserção "filtro independe das linhas" | Teste do endpoint (shape + gate) espelhando o de `/api/channels`; asserção pura em `conversationRows.test.js` de que o `clauseMatches('channel')` casa por `id` | Baixo | S |

**Regra de rótulo (preservar):** ao mapear, `label = display_name || provider || (id === 'default' ? 'Padrão' : id)` — é exatamente o que a derivação atual faz ([useConversationList.js:66-68](../web/static/js/components/contacts/hooks/useConversationList.js#L66)); manter para não mudar os textos já vistos.

### Falsos positivos descartados

| Suspeita | Por que NÃO é o caminho |
|----------|-------------------------|
| "Todos os filtros pegam das linhas" | **Não.** Só Canais. Agentes vêm de `getAssignableAgents()` ([useConversationActions.js:263](../web/static/js/components/contacts/hooks/useConversationActions.js#L263)); tags de `getTags()` ([:250](../web/static/js/components/contacts/hooks/useConversationActions.js#L250)); etiquetas de conversa de `getConversationLabels()` ([ConversationFilterBar.js:263](../web/static/js/components/contacts/ConversationFilterBar.js#L263)); atributos de `getCustomAttributes()` ([:261-262](../web/static/js/components/contacts/ConversationFilterBar.js#L261)); Status/Tipo/IA/Início são enums fixos ([ConversationFilterDialog.js:29-39](../web/static/js/components/contacts/ConversationFilterDialog.js#L29)). |
| Mexer no badge `showChannel`/`distinctChannelCount` | D2 — é heurística proposital sobre as linhas (plano 56). O badge por-linha continua correto lendo as linhas; não é o bug. |
| Subir o `limit:200` da sidebar p/ "caber todos os canais" | Trata o sintoma, não a causa; e conflita com o plano 50 (teto em toda leitura). A fonte das **opções** é que está errada, não o teto das **linhas**. |
| Tela **Contatos** (`ContactFilterDialog`) | Não tem dimensão de canal — só `tag`/`contact_type`/atributos ([ContactFilterDialog.js:24-27](../web/static/js/components/contacts/ContactFilterDialog.js#L24)). Fora de escopo. |
| Derivar as opções de `displayedContacts`/`statusTagFiltered` (linhas filtradas) | Pioraria (ainda menos linhas). A fonte tem que ser o banco, não outra projeção das linhas. |

---

## 4. Contrato do endpoint (fixo — frontend e backend paralelizam contra este)

Fonte de baixo privilégio para as **opções** do filtro (recomendação P1 = novo endpoint irmão do `/connected`):

```
GET /api/channels/for-filter          # nome a confirmar em P1
gate: conversation.reply              # o operador que lê o inbox já o tem
200 { ok, data: [ { id, provider, display_name }, ... ] }
```

- Devolve **TODOS** os canais que podem aparecer como `channel_id` numa conversa: `enabled` + `disabled` + **arquivados** (`channel_repo.list_all(include_archived=True)` — union de [channel_repo.py:31](../db/repositories/channel_repo.py#L31) e [:41](../db/repositories/channel_repo.py#L41)).
- **Sem credenciais** (diferente de `serialize`): só `id/provider/display_name`. Sem segredo cruzando a borda.
- Frontend mapeia cada item para `{ id, label }` com `label = display_name || provider || (id === 'default' ? 'Padrão' : id)`.

Fallback sem backend novo (se P1 decidir reusar o existente): frontend chama `listChannels()` (+ `listArchivedChannels()` p/ cobrir arquivados) — funciona hoje em modo aberto, mas exige `channel.manage` (atendente restrito em RBAC recebe 403 → filtro vazio). Ver P1.

---

## 5. Fases / Roadmap

```
WAVE 0  F0(caracterização) 🟢     ·     F1(backend: endpoint leve) 🔴
              │                              │ (barreira: F2 precisa do endpoint)
              ▼                              ▼
WAVE 1                         F2(frontend: fonte de channelOptions) 🔴 [depende: F1]
                                             │
WAVE 2                         F3(testes + verificação) 🟢 [depende: F2]
```

> **Paralelização:** F0 (fixar o casamento por `id` no módulo puro) roda em paralelo com F1 (backend). F2 é a única troca de comportamento e depende do endpoint (F1). Se P1 decidir **reusar `/api/channels`**, F1 encolhe a ~zero e F2 passa a depender só de F0.

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|------|------|-----------|-------|-------|---------------|
| 0 | F0 | Caracterização do casamento por `id` (módulo puro) | 🟢 [independe] | Baixo | `node --test` de `conversationRows` verde cobrindo `clauseMatches('channel')` |
| 0 | F1 | Backend: endpoint leve de canais p/ filtro | 🔴 [bloqueia: F2] | Baixo | `GET /api/channels/for-filter` devolve `[{id,provider,display_name}]` de TODOS os canais; gate `conversation.reply` |
| 1 | F2 | Frontend: `channelOptions` vem do fetch | 🔴 [depende: F1] | Baixo | Filtro lista **todos** os canais mesmo com só 2 nas linhas visíveis |
| 2 | F3 | Testes + verificação manual | 🟢 [depende: F2] | Baixo | Suíte verde; repro do bug não reproduz mais |

**Disciplina (regras do repo):** verde a cada fase; um refactor por commit; nunca avançar com teste vermelho não explicado.

---

### Fase 0 — Caracterização do casamento por `id` 🟢 [independe]
**Objetivo:** fixar, num teste puro, que o filtro de canal casa por `id` textual (`(c.channel_id||'default')===value`), para F2 não introduzir regressão de matching.
**Itens:**
1. `[sequencial]` Em [web/static/js/services/conversationRows.test.js](../web/static/js/services/conversationRows.test.js), garantir/adicionar casos para `clauseMatches` com `dim:'channel'`: linha com `channel_id:'x'` casa `value:'x'`; linha sem `channel_id` casa `value:'default'`; multi-select (lista) casa por OR. Referência da lógica: [conversationRows.js:57-66](../web/static/js/services/conversationRows.js#L57),[:120-134](../web/static/js/services/conversationRows.js#L120).
2. `[paralelo]` Anotar no teste o invariante: **a opção do filtro é o `id` do canal** — é isso que F2 tem que preservar ao trocar a fonte.

**Pronto quando:** `node --test web/static/js/services/conversationRows.test.js` verde, com os casos de canal explícitos.

#### Status de execução — Fase 0
**Estado:** ✅ Concluída
- **O que foi feito:** Novo teste `clauseMatches: opção de canal do banco casa por 'id' (contrato plano 59)` em [conversationRows.test.js](../web/static/js/services/conversationRows.test.js) fixando que o `value` da opção (= `channel.id` do endpoint) casa o `channel_id` da conversa. O casamento eq/ne, o fallback `default` e o multi-select OR/ne já estavam cobertos (linhas 176-243).
- **Como foi feito / decisões:** Em vez de duplicar os casos existentes, adicionei um teste que documenta explicitamente o **acoplamento** entre o campo `id` do endpoint `/for-filter` e o match — é o invariante que F2 tinha que preservar.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `node --test web/static/js/services/conversationRows.test.js` → 56 tests, 0 fail.

---

### Fase 1 — Backend: endpoint leve de canais p/ o filtro 🔴 [bloqueia: F2]
**Objetivo:** uma fonte de baixo privilégio que devolve **todos** os canais (id/provider/display_name), sem credenciais, para alimentar as opções do filtro.
**Itens:** (executar conforme P1)
1. `[sequencial]` Em [app/services/channel_service.py](../app/services/channel_service.py#L236) (ao lado de `list_connected`): função que lê `channel_repo.list_all(include_archived=True)` e projeta `{id, provider, display_name}` de **cada** canal (enabled, disabled e arquivado). Puro de rede (só DB); **não** chama `serialize` (sem creds).
2. `[sequencial]` Em [server/routes/channels.py](../server/routes/channels.py#L42) (ao lado do `/connected`): rota `GET /api/channels/for-filter` (nome em P1) com `permission_denied(request, "conversation.reply")` e `return _ok(...)`. ⚠️ **Ordem de rota**: registrar ANTES de `GET /api/channels/{channel_id}` ([channels.py:218](../server/routes/channels.py#L218)) para o path fixo não cair no param dinâmico (mesmo cuidado que `/connected` já tem).
3. `[paralelo]` Em [web/static/js/services/api.js](../web/static/js/services/api.js#L640): client `listChannelsForFilter()` → `GET /api/channels/for-filter`, retornando `res.data` (`[]` no erro — degradação silenciosa).

**Pronto quando:** `curl -s /api/channels/for-filter` devolve `[{id,provider,display_name}]` incluindo canais desabilitados/arquivados; sem `channel.manage`; sem campo `credentials` no payload.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (P1 = opção **(a)**, endpoint novo)
- **O que foi feito:**
  - `channel_service.list_for_filter(deps)` ([channel_service.py](../app/services/channel_service.py)) — lê `channel_repo.list_all(True)` (include_archived) e projeta `{id, provider, display_name}` de TODOS os canais. Não chama `serialize` (sem creds).
  - Rota `GET /api/channels/for-filter` ([channels.py](../server/routes/channels.py)) gated por `conversation.reply`, registrada **logo após** `/connected` e ANTES de `/api/channels/{channel_id}` (path fixo vence o dinâmico).
  - Client `listChannelsForFilter()` ([api.js](../web/static/js/services/api.js)).
- **Como foi feito / decisões:** Segui a recomendação P1=(a): endpoint irmão de baixo privilégio, espelhando o precedente do `/connected`. Amplitude maior (inclui disabled + arquivados) porque qualquer um pode ser o `channel_id` de uma conversa histórica.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** teste no endpoint (§F3) — 200, lista, inclui `default`, chaves exatamente `{id,provider,display_name}`, sem `credentials`. Todos verdes.

---

### Fase 2 — Frontend: `channelOptions` vem do banco 🔴 [depende: F1]
**Objetivo:** o filtro de Canais lista todos os canais do banco, independente das conversas carregadas.
**Itens:**
1. `[sequencial]` [useConversationList.js:59-73](../web/static/js/components/contacts/hooks/useConversationList.js#L59): substituir o `useMemo` que varre `contacts` por `const [channelOptions, setChannelOptions] = useState([])` + `useEffect` de mount que chama `listChannelsForFilter()` (F1) e faz `setChannelOptions(data.map(c => ({ id: c.id, label: c.display_name || c.provider || (c.id === 'default' ? 'Padrão' : c.id) })))`. Manter o **mesmo nome exportado** ([useConversationList.js:127](../web/static/js/components/contacts/hooks/useConversationList.js#L127)) ⇒ zero mudança em `Contacts.js`/`ContactList.js`/`ConversationFilterBar.js`.
   - **Alternativa (P2):** fazer o fetch dentro de [ConversationFilterBar.js:257](../web/static/js/components/contacts/ConversationFilterBar.js#L257) (molde `convLabelNames`), desacoplando de vez o filtro da lista — trocar `channels=${channels||[]}` por um estado local. Mais localizado, porém muda a origem da prop usada também nos chips.
2. `[sequencial]` **NÃO** tocar em `showChannel`/`maxChannelsSeenRef` ([useConversationList.js:53-57](../web/static/js/components/contacts/hooks/useConversationList.js#L53)) nem em `distinctChannelCount` (D2). O badge por-linha continua lendo `contacts`.
3. `[paralelo]` (Opcional) Re-fetch das opções ao ouvir um evento de mudança de canais, se houver um broadcast/WS de canal — senão, fetch único no mount basta (canais mudam raramente).

**Pronto quando:** numa base com >200 conversas concentradas em 2 canais, abrir *Filtrar conversas* → **Canais** lista **todos** os canais cadastrados (não só os 2 visíveis); alternar caixa × arquivadas **não** muda a lista de opções; o chip do filtro aplicado mostra o nome do canal mesmo p/ canal sem conversa recente.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (P2 = opção **(a)**, fetch em `useConversationList`)
- **O que foi feito:** Em [useConversationList.js](../web/static/js/components/contacts/hooks/useConversationList.js) troquei o `useMemo` que varria `contacts` por `useState([])` + `useEffect` de mount que chama `listChannelsForFilter()` e mapeia cada canal para `{id, label}` com `label = display_name || provider || (id==='default' ? 'Padrão' : id)`. Nome exportado `channelOptions` mantido ⇒ zero ripple em `Contacts.js`/`ContactList.js`/`ConversationFilterBar.js`.
- **Como foi feito / decisões:** Guard `alive` no cleanup do effect (evita setState após unmount); `.catch(() => {})` + guard `res.ok` ⇒ degradação silenciosa (filtro vazio) se o fetch falhar. `showChannel`/`maxChannelsSeenRef`/`distinctChannelCount` **intactos** (D2 — badge por-linha continua lendo `contacts`). Regra de label preservada idêntica à derivação antiga.
- **Problemas / pendências:** Fetch único no mount (item 3 opcional — re-fetch por evento de canal — não implementado; canais mudam raramente e o hub recarrega no F5/reabrir).
- **Verificação:** manual — ver F3.

---

### Fase 3 — Testes + verificação 🟢 [depende: F2]
**Objetivo:** travar o comportamento novo e garantir zero regressão.
**Itens:**
1. `[paralelo]` [tests/test_endpoints.py:1154](../tests/test_endpoints.py#L1154) (perto do teste de `/api/channels`): caso p/ `GET /api/channels/for-filter` — 200, `data` é lista de `{id,provider,display_name}`, inclui o canal `default`, e **não** vaza `credentials`.
2. `[paralelo]` Confirmar F0 verde (matching por `id` inalterado).
3. `[sequencial]` Verificação manual (repro do bug): reproduzir o cenário dos prints (muitas conversas, poucos canais visíveis) e confirmar que o dropdown de Canais agora mostra todos; aplicar o filtro por um canal **fora** das 200 linhas e confirmar que as conversas daquele canal aparecem (o match roda sobre as linhas já carregadas — ⚠️ ver Riscos: as linhas ainda são capadas em 200, então "filtrar por canal raro" só mostra o que estiver dentro da janela até o plano 50 paginar a sidebar).

**Pronto quando:** `venv/bin/python -m pytest tests/test_endpoints.py -q` verde no Postgres; `node --test` verde; repro manual do bug não reproduz mais.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída
- **O que foi feito:** 5 checks p/ `GET /api/channels/for-filter` em [test_endpoints.py](../tests/test_endpoints.py) (perto do teste de `/api/channels`): 200, é lista, inclui `default`, chaves exatamente `{id,provider,display_name}`, e nenhum item vaza `credentials`. F0 confirmado verde.
- **Como foi feito / decisões:** Coloquei o teste ANTES do bloco que seta credencial em `default` — mas é irrelevante, o endpoint nunca projeta creds.
- **Problemas / pendências:** Verificação manual no navegador (repro dos prints) **pendente do usuário** — os testes automatizados cobrem o contrato do endpoint + o invariante de match. Lembrete de escopo (P3): o filtro agora OFERECE todos os canais; ver *todas* as conversas de um canal raro além das 200 linhas depende da paginação da sidebar (plano 50).
- **Verificação:** `venv/bin/python tests/test_endpoints.py` → 1275 passed, 8 failed (as 8 são pré-existentes de busca acento/collation — confirmado via `git stash`: baseline 1270/8; delta = exatamente meus +5 checks). `node --test conversationRows.test.js` → 56/0.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Privilégio do endpoint | `/api/channels` exige `channel.manage` → atendente sem essa permissão veria filtro vazio em RBAC | Endpoint leve gated por `conversation.reply` (F1). Em modo aberto (sem RBAC writer — ver memória) qualquer um responde, mas o endpoint leve é o correto a longo prazo. |
| Ordem das rotas | `GET /api/channels/for-filter` pode cair no `GET /api/channels/{channel_id}` | Registrar o path fixo ANTES do dinâmico ([channels.py:218](../server/routes/channels.py#L218)) — mesmo cuidado do `/connected`. |
| Segredo na borda | `serialize` inclui `credentials` (mascaradas) | O endpoint do filtro projeta só `{id,provider,display_name}` — **não** usa `serialize`. Nada de credencial cruza. |
| Cobertura de arquivados/disabled | Omitir arquivados/disabled reintroduz o "canal some" p/ histórico | F1 usa `list_all(include_archived=True)` — TODOS os canais que podem ser um `channel_id`. |
| Casamento `id` | Se o `id` retornado divergir de `conversations.channel_id`, o filtro "não acha nada" | Ambos são o `channels.id` textual (`"default"` inclusive) — [tables.py:236](../db/tables.py#L236) × [conversationRows.js:65](../web/static/js/services/conversationRows.js#L65). F0 fixa o invariante. |
| Escopo do match ainda capado (200) | O filtro agora OFERECE todos os canais, mas o match roda sobre as ≤200 linhas carregadas | Fora de escopo deste plano (é o plano 50 — paginação da sidebar). Documentar em P3: oferecer a opção já corrige o bug reportado; ver **todas** as conversas de um canal raro depende da sidebar paginada. |
| Postgres | Suíte precisa do banco de teste | `WHATSBOT_TEST_DB_URL` com `test` no nome (trava de segurança). |
| Modo escuro | — | Sem UI nova (só troca de fonte de dados); nada a re-tematizar. |

---

## 7. Perguntas em aberto

- **P1 — Qual endpoint alimenta o filtro?** ✅ DECIDIDO (2026-07-18): opção **(a)** — novo `GET /api/channels/for-filter` leve, gate `conversation.reply`, TODOS os canais, sem creds. Opções: (a) **novo** `GET /api/channels/for-filter` leve, gate `conversation.reply`, TODOS os canais (id/provider/display_name), sem creds; (b) reusar `GET /api/channels` (+ `?archived=true`) — sem backend novo, mas exige `channel.manage` e 2 chamadas; (c) estender `/api/channels/connected` com `?all=1` que dropa os gates `logged_in`/`can_initiate`. **Recomendação:** (a) — espelha o precedente do `/connected` (endpoint operador-facing, baixo privilégio, leve) e resolve cobertura + privilégio de uma vez. (b) é aceitável como stopgap em instâncias em modo aberto.
- **P2 — Onde mora o fetch no frontend?** ✅ DECIDIDO (2026-07-18): opção **(a)** — em `useConversationList`, mantendo o nome `channelOptions`. (a) Em `useConversationList`, mantendo o nome `channelOptions` (ripple zero downstream); (b) em `ConversationFilterBar` (molde `convLabelNames`), desacoplando o filtro da lista. **Recomendação:** (a) — menor superfície e a mesma lista alimenta dialog + chips.
- **P3 — Match sobre linhas capadas.** ⏸️ ADIADO (fora de escopo). Oferecer a opção de canal corrige o bug relatado (opções faltando). Filtrar por um canal cujas conversas estão além das 200 carregadas só mostrará o que já está na janela — isso é resolvido pela **paginação da sidebar do plano 50**, não aqui. Registrar a dependência.

---

## 8. Apêndice — arquivos-chave

**Backend**
- [server/routes/channels.py:42](../server/routes/channels.py#L42) — rota nova `/for-filter` (ao lado de `/connected`); cuidado de ordem vs. [:218](../server/routes/channels.py#L218).
- [app/services/channel_service.py:236](../app/services/channel_service.py#L236) — função de projeção leve (ao lado de `list_connected`).
- [db/repositories/channel_repo.py:31](../db/repositories/channel_repo.py#L31),[:41](../db/repositories/channel_repo.py#L41) — `list_all(include_archived=True)`.

**Frontend**
- [web/static/js/components/contacts/hooks/useConversationList.js:59-73](../web/static/js/components/contacts/hooks/useConversationList.js#L59) — trocar a fonte de `channelOptions` (I2); **não** tocar em [:53-57](../web/static/js/components/contacts/hooks/useConversationList.js#L53).
- [web/static/js/services/api.js:640](../web/static/js/services/api.js#L640) — `listChannelsForFilter()`.
- [web/static/js/components/contacts/ConversationFilterBar.js:257](../web/static/js/components/contacts/ConversationFilterBar.js#L257) — alternativa P2 (molde `convLabelNames`).
- Consumidores (não mudam se P2=a): [Contacts.js:269](../web/static/js/components/contacts/Contacts.js#L269), [ContactList.js:480](../web/static/js/components/contacts/ContactList.js#L480), [ConversationFilterDialog.js:173](../web/static/js/components/contacts/ConversationFilterDialog.js#L173).

**Testes**
- [tests/test_endpoints.py:1154](../tests/test_endpoints.py#L1154) — teste do endpoint novo (mirror do `/api/channels`).
- [web/static/js/services/conversationRows.test.js](../web/static/js/services/conversationRows.test.js) — invariante de match por `id` (F0).

---

## 9. Checklist de verificação

- [ ] Filtro **Canais** lista todos os canais do banco mesmo com só 2 canais nas 200 conversas visíveis (repro dos prints não reproduz mais). _(verificação manual no navegador — pendente do usuário)_
- [ ] Alternar caixa × arquivadas **não** muda a lista de opções de canal. _(manual — pendente)_
- [ ] Chip do filtro aplicado mostra o **nome** do canal (não o `id` cru) mesmo p/ canal sem conversa recente. _(manual — pendente)_
- [x] Badge por-linha (`showChannel`) inalterado: some com 1 canal, aparece com ≥2 (plano 56 intacto). _(código não tocado — D2)_
- [x] `GET /api/channels/for-filter` devolve `{id,provider,display_name}` de enabled+disabled+arquivado; gate `conversation.reply`; **sem** `credentials`. _(teste verde)_
- [x] Rota fixa registrada antes de `/api/channels/{channel_id}` (não cai no param dinâmico). _(registrada logo após `/connected`; teste 200)_
- [x] `venv/bin/python tests/test_endpoints.py` **verde** no Postgres (`WHATSBOT_TEST_DB_URL`) — +5 checks; 8 falhas pré-existentes de busca acento/collation, sem relação.
- [x] `node --test web/static/js/services/conversationRows.test.js` **verde** (match de canal por `id`) — 56/0.
- [x] Fetch falho degrada silencioso (filtro vazio, sem quebrar o hub); nenhum segredo em URL. _(`res.ok` guard + `.catch`; endpoint sem creds)_
