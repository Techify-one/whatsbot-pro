# Plano 130 — Contador das abas do hub: parar de oscilar entre o total real e o tamanho da página

> **Status:** F0–F2 IMPLEMENTADAS · F3 aguardando validação ao vivo · **Data:** 2026-08-19 · **Escopo:** pequeno/médio (frontend only, sem backend, sem migration)
> **Origem:** relato do usuário — o número da aba **Todas** alterna entre `50` e `252` a cada ~1 s no hub de conversas de produção (`atendimento.coolify.redesbrasil.com.br/?assignment=all`). **Método:** leitura do código real (`arquivo:linha` verificados) + consulta ao banco de PRODUÇÃO via VAULT (`banco-nexus-redes-brasil-3c66e7`, database `whatsbot`).
> **O quê/porquê:** o badge sai de `tabCounts = serverCounts || clientTabCounts` ([useConversationFilters.js:138](web/static/js/components/contacts/hooks/useConversationFilters.js#L138)). São **duas fontes de significado diferente**: o total real do servidor (**252**) e um fallback que conta **só as linhas já carregadas na sidebar** (a página de **50**). O efeito que busca o total tem `contacts` no array de dependências e **zera `serverCounts` logo na primeira linha** — então todo evento de WebSocket que realoca a lista derruba o badge para o fallback por ≥300 ms. O conserto é: nunca regredir para o fallback depois de ter um total, parar de realocar a lista em patches no-op, e pôr um teto de frequência no refetch.
>
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0 — Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 ✅ (2026-08-19) | A contagem **nunca regride** para o fallback de página depois de já ter um total do servidor. `serverCounts` só é limpo quando o **spec de filtro muda**. | Some o `setServerCounts(null)` incondicional de [useConversationFilters.js:151](web/static/js/components/contacts/hooks/useConversationFilters.js#L151); o reset passa a ser condicionado a uma **chave de spec**. |
| D2 ✅ (2026-08-19) | O fallback client-side **continua existindo** — mas só para o **primeiro paint** (antes do 1º total chegar) e para `!serverMode` (onde a lista é de fato filtrada no cliente e o número está certo). | `clientTabCounts` ([:132-137](web/static/js/components/contacts/hooks/useConversationFilters.js#L132-L137)) não é removido; muda **quando** ele é escolhido. |
| D3 ✅ (2026-08-19) | O refetch da contagem ganha **teto de frequência** quando o gatilho é "a lista mudou". Mudança de **filtro** continua imediata (o usuário está esperando). | Hoje, numa rajada contínua, o debounce de 300 ms reinicia sem parar e a contagem **nunca chega a ser buscada** — o teto conserta isso também. |
| D4 ✅ (2026-08-19) | Patch no-op **não realoca** o array de linhas: `setContacts` devolve `prev` quando nada mudou. | Ataca a causa na origem e reduz re-render da sidebar inteira. Workstream **independente** (arquivos diferentes) ⇒ paraleliza com o resto. |
| D5 ✅ (2026-08-19) | **Zero mudança de backend.** | `count_tab_counts` está correto e custa **2,9 ms** medidos em produção (`EXPLAIN ANALYZE`) — não é gargalo nem fonte da instabilidade. |
| D6 ✅ (2026-08-19) | **Não** migrar a contagem para push por WebSocket nesta correção. | Seria a arquitetura "certa", mas o `/ws` não tem escopo por canal/usuário (plano 90) — vira P2, adiado. |

**Princípio fixo:** o número exibido é **estado derivado**, não estado de carregamento. Enquanto um total válido existir para o filtro atual, ele é o que aparece — atualizado, nunca substituído por outra métrica.

---

## 1 — Resumo executivo

O badge das abas mistura duas grandezas na mesma variável. Quando `serverCounts` é `null`, o hub mostra `statusTagFiltered.length` — que em `serverMode` é literalmente **o número de linhas carregadas** (página de 50), não um total. E `serverCounts` volta a `null` a cada evento de WS, porque o efeito que o busca depende de `contacts` e começa zerando o valor anterior.

Como todo handler de WS faz `setContacts(prev => prev.map(...))` (array novo mesmo em patch no-op) e o `conversation_upsert` sai **a cada mensagem visível salva**, a identidade de `contacts` muda cerca de uma vez por segundo em produção. Resultado: `252 → 50 → 252 → 50…`.

A solução tem três camadas, nesta ordem de valor: **(1)** manter o último total durante o refetch, resetando só quando o filtro muda; **(2)** não realocar a lista em patch no-op; **(3)** teto de frequência no refetch, que de quebra conserta o caso em que a contagem nunca é buscada durante uma rajada.

---

## 2 — Como funciona hoje (mapa verificado)

### 2.1 A linha do bug

```js
// web/static/js/components/contacts/hooks/useConversationFilters.js:138
const tabCounts = serverCounts || clientTabCounts;
```

| Fonte | Onde | O que é | Valor medido |
|---|---|---|---|
| `serverCounts` | [:62](web/static/js/components/contacts/hooks/useConversationFilters.js#L62), preenchido em [:154-163](web/static/js/components/contacts/hooks/useConversationFilters.js#L154-L163) | total real de `GET /api/atendimentos/count` | **252** |
| `clientTabCounts` | [:132-137](web/static/js/components/contacts/hooks/useConversationFilters.js#L132-L137) | `statusTagFiltered.length` etc. — em `serverMode`, o que está **carregado** | **50** |

Em `serverMode`, `statusTagFiltered` devolve `activeContacts` **sem re-filtrar** ([:114-118](web/static/js/components/contacts/hooks/useConversationFilters.js#L114-L118)) — ou seja, o fallback é o tamanho da página, e a página é `SIDEBAR_PAGE = 50` ([useConversationList.js:27](web/static/js/components/contacts/hooks/useConversationList.js#L27)).

### 2.2 O efeito que invalida o total a cada evento

```js
// :141-171 (resumido)
if (!serverMode) { setServerCounts(null); return () => {}; }   // :148
setServerCounts(null);                                          // :151  ← zera SEMPRE
const timer = setTimeout(() => { countConversations(...) }, 300);  // :153
return () => { alive = false; clearTimeout(timer); };
}, [search, searching, statusFilter, tagFilter, advFilters, showArchived, serverMode, contacts]);  // :171
```

⚠️ Três consequências, todas verificadas:

1. **`contacts` na dependência** ([:171](web/static/js/components/contacts/hooks/useConversationFilters.js#L171)) ⇒ qualquer troca de identidade do array re-roda o efeito.
2. **`setServerCounts(null)` incondicional** ([:151](web/static/js/components/contacts/hooks/useConversationFilters.js#L151)) ⇒ o badge cai no fallback **antes** de qualquer request.
3. **Debounce de 300 ms que reinicia** ⇒ numa rajada contínua o `setTimeout` é cancelado repetidamente e **a contagem nunca é buscada**. O número fica travado no fallback enquanto durar o movimento.

### 2.3 Por que `contacts` muda o tempo todo

Todos os handlers de WS realocam o array **mesmo quando o patch é no-op**:

| Site | Arquivo:linha | Padrão |
|---|---|---|
| `conversation_upsert` (o de maior volume) | [useConversationWsEvents.js:283](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L283) | `upsertConversationRow` → `[...prev]` + `sortContacts` ([conversationRows.js:769-771](web/static/js/services/conversationRows.js#L769-L771)) |
| membership (assign/resolve/label/attr) | [:380](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L380) | `applyConversationEvent` → `rows.map(...)` ([conversationPatch.js:94-101](web/static/js/services/conversationPatch.js#L94-L101)) |
| `message_status` (sent→delivered→read) | [:620](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L620) | `prev.map(...)` |
| `messages_read` | [:591](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L591) | `prev.map(...)` |
| `avatar_updated` | [:677](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L677) | `prev.map(...)` |
| `contact_info_updated` / `contact_ai_toggled` | [:520](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L520), [:551](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L551) | `prev.map(...)` |
| visibilitychange (marcar lida) | [:172](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L172) | `prev.map(...)` |
| demais | [:211](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L211), [:296](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L296), [:323](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L323), [:337](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L337), [:352](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L352), [:573](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L573), [:709](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L709) | idem |

⚠️ **Contraste que prova a intenção:** os handlers de `setContactData` na mesma vizinhança ([:639-650](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L639-L650), [:686-697](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L686-L697)) usam uma flag `changed` e devolvem `prev` intacto quando nada mudou. Os de `setContacts` **não** — é uma assimetria acidental, não uma decisão.

Some-se o `scheduleListRefetch` (debounce de 250 ms, [:103-108](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L103-L108)), que refaz a 1ª página inteira e também troca o array.

O emissor de maior volume é o `conversation_upsert`, disparado **a cada mensagem visível salva** ([agent/message_listeners.py:36-63](agent/message_listeners.py#L36-L63)) — e o `/ws` **não tem escopo por canal/usuário**, então cada navegador recebe os eventos das 252 conversas da instância.

### 2.4 O fallback também é instável (o "50" anda)

Em `serverMode`, um `conversation_upsert` de conversa **ausente** da página que **casa a view** é **inserido** na lista ([useConversationWsEvents.js:242](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L242) — "casa → cai no upsert normal abaixo (INSERT)"). Ou seja, o valor baixo não é um `50` fixo: ele **cresce** conforme a sessão envelhece. A oscilação é entre um número que muda e outro que muda.

### 2.5 Evidência de produção (VAULT · database `whatsbot`)

| Medição | SQL | Resultado |
|---|---|---|
| Conversas abertas, não arquivadas | `SELECT status, is_archived, count(*) FROM public.atendimentos GROUP BY 1,2` | `open/0` = **251** (+4 `open/1` arquivadas, 15.275 `closed`) — bate com o **252** do print |
| Página de 50 mais recentes: atribuídas a humano / não atribuídas | CTE `ORDER BY is_pinned DESC, last_ts DESC LIMIT 50` | **2 / 0** |
| Totais reais: Minhas / Não atribuídas | `count_tab_counts` | **1 / 2** |
| Mensagens visíveis no pico (últ. 60 min) | agregação por minuto em `public.messages` | **~26/min** em 14 conversas |
| Custo da query de contagem | `EXPLAIN (ANALYZE, BUFFERS)` | **2,972 ms** (`Hash Join`, 517 buffers) |

Os dois estados dos prints são exatamente **`50/0/0`** (a página) e **`252/1/2`** (o banco). ⚠️ Note que `Não atribuídas` = **0** na página e **2** no total: o mesmo bug, na mesma barra, em três badges ao mesmo tempo.

⚠️ **A tabela chama-se `atendimentos`**, não `conversations` (o objeto `Table` do Python é que se chama `conversations`). Ver a memória "atendimentos = conversations" — não caia no falso-amigo ao consultar produção.

### 2.6 Sintomas colaterais do MESMO bug (não são outros bugs)

| Sintoma | Onde | Mecanismo |
|---|---|---|
| Rodapé **"Mostrando X de Y"** pisca / some | [ContactList.js:826-834](web/static/js/components/contacts/ContactList.js#L826-L834) | com o fallback, `total (50) <= loaded (50)` ⇒ `return null` ⇒ o rodapé desaparece e reaparece |
| Aba **"Menções"** aparece e some | [ConversationFilterBar.js:483](web/static/js/components/contacts/ConversationFilterBar.js#L483) | renderizada só se `counts.mentions > 0`; o fallback conta menções **da página** ⇒ layout shift na barra de abas |
| Badges **Minhas** e **Não atribuídas** caem para 0 | [ConversationFilterBar.js:482,484](web/static/js/components/contacts/ConversationFilterBar.js#L482-L484) | mesma variável `counts` |

Todos os três somem junto com a correção da Fase F1 — **não** precisam de tratamento próprio.

---

## 3 — Inventário de mudanças

| # | Arquivo:linha | O que falta | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|
| M1 | `web/static/js/services/tabCounts.js` (**novo**) | não existe módulo puro com a decisão | Criar `countSpecKey(spec)`, `resolveTabCounts({...})`, `planCountFetch({...})` — mesmo padrão de [threadJump.js](web/static/js/services/threadJump.js) / [hubDefaults.js](web/static/js/services/hubDefaults.js) | baixo | M |
| M2 | `web/static/js/services/tabCounts.test.js` (**novo**) | — | Testes `node --test` cobrindo reset por spec, manutenção do total, teto de frequência, `!serverMode` | baixo | M |
| M3 | [useConversationFilters.js:141-171](web/static/js/components/contacts/hooks/useConversationFilters.js#L141-L171) | zera o total a cada evento; debounce que nunca resolve | Consultar `planCountFetch`; remover o `setServerCounts(null)` de [:151](web/static/js/components/contacts/hooks/useConversationFilters.js#L151); manter o de [:148](web/static/js/components/contacts/hooks/useConversationFilters.js#L148) (saída de `serverMode`) | médio | M |
| M4 | [useConversationFilters.js:138](web/static/js/components/contacts/hooks/useConversationFilters.js#L138) | `||` cru mistura as duas grandezas | Trocar por `resolveTabCounts({ serverCounts, clientCounts: clientTabCounts, serverMode })` | baixo | S |
| M5 | [useConversationFilters.js:154-167](web/static/js/components/contacts/hooks/useConversationFilters.js#L154-L167) | guard `alive` é por **execução do efeito** | Descartar resposta cuja **chave de spec** ≠ a atual (o efeito deixa de re-rodar a cada evento, então o guard precisa mudar de eixo) | médio | S |
| M6 | [conversationRows.js:726-771](web/static/js/services/conversationRows.js#L726-L771) | `upsertConversationRow` sempre devolve array novo | Short-circuit: se a linha mesclada for **igual campo a campo** à existente, devolver `prev` | médio | M |
| M7 | [conversationPatch.js:94-101](web/static/js/services/conversationPatch.js#L94-L101) | `applyConversationEvent` usa `rows.map` (array novo sempre) | Devolver `rows` quando **nenhuma** linha mudou de identidade | baixo | S |
| M8 | [conversationRows.js](web/static/js/services/conversationRows.js) (novo export) | não há helper de patch que preserve identidade | `patchRows(rows, matches, patch)` → devolve `rows` no no-op; reutilizado pelos ~10 `prev.map(...)` do hook de WS | baixo | M |
| M9 | [useConversationWsEvents.js:172,296,520,551,591,620,677,709](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L172) | `prev.map(...)` cru | Trocar pelo `patchRows` de M8 | baixo | M |
| M10 | [conversationRows.test.js](web/static/js/services/conversationRows.test.js) + `conversationPatch.test.js` | — | Testes de "no-op devolve a MESMA referência" | baixo | S |

### 3.1 Falsos positivos descartados

| Candidato | Por que NÃO mexer | Evidência |
|---|---|---|
| `count_tab_counts` (backend) | O número está **certo** e é estável; `all=251` bate com o print | [conversation_repo.py:544-589](db/repositories/conversation_repo.py#L544-L589) + consulta VAULT |
| Rota `/api/atendimentos/count` | Correta; `_run_count` só delega | [server/routes/conversations.py:245-269](server/routes/conversations.py#L245-L269) |
| Desempenho da query de contagem | **2,972 ms** medidos em produção — o `Seq Scan on contacts` (15k linhas) não é gargalo nesta escala | `EXPLAIN (ANALYZE, BUFFERS)` via VAULT |
| Volume do `conversation_upsert` (backend) | O evento está correto (Event-Carried State Transfer, plano 28); o problema é o **consumidor** realocar em no-op | [agent/message_listeners.py:36-63](agent/message_listeners.py#L36-L63) |
| Escopo do `/ws` por canal (plano 90) | Reduziria o volume, mas **não** conserta o bug: 1 evento já basta para zerar o badge | plano 90; memória "/ws sem escopo por canal" |
| `SIDEBAR_PAGE = 50` | Aumentar a página só muda o valor do estado errado | [useConversationList.js:27](web/static/js/components/contacts/hooks/useConversationList.js#L27) |
| Insert-gate do `conversation_upsert` (plano 72 F3) | Está correto; ele explica por que o fallback "anda", mas não é a causa | [useConversationWsEvents.js:225-245](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L225-L245) |
| `clientTabCounts` | Continua necessário no 1º paint e fora de `serverMode` (D2) | [useConversationFilters.js:132-137](web/static/js/components/contacts/hooks/useConversationFilters.js#L132-L137) |
| `buildCountParams` | Já monta o spec certo (aba fica **fora** de propósito — a contagem cobre as 4 abas) | [conversationFilterSpec.js:118-140](web/static/js/services/conversationFilterSpec.js#L118-L140) |
| `setContactData` handlers | **Já** devolvem `prev` no no-op — são o modelo a imitar, não o alvo | [useConversationWsEvents.js:639-650](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L639-L650) |
| "Mostrando X de Y" / aba "Menções" | Sintomas do mesmo bug; somem com F1 sem código próprio | [ContactList.js:826-834](web/static/js/components/contacts/ContactList.js#L826-L834), [ConversationFilterBar.js:483](web/static/js/components/contacts/ConversationFilterBar.js#L483) |
| Plugins / migration / modo escuro | Nada disso é tocado — mudança 100% de lógica de estado no frontend | — |

---

## 4 — Contrato do módulo puro (M1)

Assinaturas propostas (a implementação é da fase F0; aqui só a forma):

```js
// web/static/js/services/tabCounts.js
export const EMPTY_COUNTS = { all: 0, mine: 0, unassigned: 0, mentions: 0 };

/** Chave estável do spec de CONTAGEM: muda quando (e só quando) o filtro muda. */
export function countSpecKey(spec) -> string

/** Qual contagem exibir. serverMode + total conhecido ⇒ o total; senão, o do cliente. */
export function resolveTabCounts({ serverCounts, clientCounts, serverMode }) -> counts

/** Decide o que fazer neste tick. Puro: recebe o relógio, não o lê. */
export function planCountFetch({
  specKey, lastSpecKey, lastFetchAt, now, debounceMs = 300, minIntervalMs = 4000,
}) -> { action: 'reset_and_fetch' | 'fetch' | 'wait' | 'idle', delayMs: number }
```

Regras que os testes de M2 devem fixar:

| Situação | `action` | Limpa `serverCounts`? |
|---|---|---|
| Spec mudou (usuário mexeu no filtro) | `reset_and_fetch` (delay = `debounceMs`) | **Sim** — o total antigo é de outro filtro |
| Só a lista mudou, último fetch há ≥ `minIntervalMs` | `fetch` (delay = `debounceMs`) | **Não** (D1) |
| Só a lista mudou, último fetch recente | `wait` (delay = tempo restante) | **Não** |
| `serverMode` desligado | `idle` | **Sim** — o cliente é autoritativo |

---

## 5 — Fases / Roadmap

### 5.1 Dependências (waves)

```
WAVE 0   F0 (módulo puro tabCounts.js + node --test)     🔴 FAÇA SOZINHA  [bloqueia: F1]
         F2 (patch no-op não realoca o array)            🟢 PODE AGRUPAR  [arquivos disjuntos de F0/F1]
            │  (barreira: F1 só começa com F0 verde)
WAVE 1   F1 (ligar o hook ao módulo puro)                🔴 depende de F0
            │
WAVE 2   F3 (validação ao vivo + regressão)              🔴 depende de F1 E F2
```

### 5.2 Tabela de fases

| Wave | Fase | Workstream | Paraleliz. | Risco | Pronto quando / obs |
|---|---|---|---|---|---|
| 0 | F0 | Módulo puro `tabCounts.js` + testes | 🔴 FAÇA SOZINHA | baixo | `node --test` verde; nenhum arquivo existente tocado [bloqueia: F1] |
| 0 | F2 | No-op não realoca (`patchRows`, `upsertConversationRow`, `applyConversationEvent` + call sites) | 🟢 PODE AGRUPAR | médio | `node --test` verde; nenhum comportamento visível muda [independente de F0/F1] |
| 1 | F1 | `useConversationFilters` passa a usar o módulo | 🔴 depende de F0 | médio | Badge para de oscilar com o filtro parado |
| 2 | F3 | Validação ao vivo + regressão | 🔴 depende de F1 e F2 | baixo | 60 s de tráfego real sem oscilação; suíte verde |

> **Observação honesta de paralelização:** só existem **duas** frentes de verdade — F0→F1 (a máquina de estado da contagem) e F2 (identidade do array). Elas tocam arquivos disjuntos (`services/tabCounts.js` + `hooks/useConversationFilters.js` × `services/conversationRows.js` + `services/conversationPatch.js` + `hooks/useConversationWsEvents.js`) e podem ser despachadas juntas na Wave 0. F1 sozinha já elimina a oscilação visível; **F2 é o conserto da causa** e derruba re-renders desnecessários da sidebar — não é opcional, é a segunda metade.

---

### Fase F0 — Módulo puro da contagem (🔴 antes de tocar o hook)

**Objetivo:** tirar a decisão de "qual número mostrar e quando buscar" de dentro de um hook (intestável) e pô-la num módulo puro com testes, como manda o precedente do repo ([threadJump.js](web/static/js/services/threadJump.js), [hubDefaults.js](web/static/js/services/hubDefaults.js), [composerSubmit.js](web/static/js/services/composerSubmit.js)).

**Itens:**
1. [sequencial] Criar `web/static/js/services/tabCounts.js` com as três funções do §4. **Puro**: sem Preact, sem `fetch`, sem `Date.now()` interno (o `now` entra por parâmetro — é o que torna o teto de frequência testável).
2. [sequencial] `countSpecKey` deve derivar de `buildCountParams(spec)` ([conversationFilterSpec.js:118](web/static/js/services/conversationFilterSpec.js#L118)) com as chaves **ordenadas** — dois specs equivalentes precisam produzir a mesma chave, ou o reset dispararia à toa.
3. [paralelo] Criar `web/static/js/services/tabCounts.test.js` cobrindo a tabela do §4, mais: contagem parcial/ausente vira `EMPTY_COUNTS`; `serverCounts` com campo faltando não vira `NaN`; troca de `serverMode` limpa.
4. [paralelo] Teste de **não-regressão do fallback**: com `serverCounts = null` e `serverMode = true` (1º paint), `resolveTabCounts` devolve o do cliente — D2.

**Pronto quando:** `node --test web/static/js/services/tabCounts.test.js` verde e **nenhum arquivo existente foi modificado** (a fase é puramente aditiva).

#### Status de execução — Fase F0
**Estado:** ✅ Concluída (2026-08-19)
- **O que foi feito:** criados `web/static/js/services/tabCounts.js` (`EMPTY_COUNTS`, `DEFAULT_DEBOUNCE_MS=300`, `DEFAULT_MIN_INTERVAL_MS=4000`, `countSpecKey`, `resolveTabCounts`, `planCountFetch`) e `tabCounts.test.js`. Nenhum arquivo existente tocado (fase puramente aditiva, como previsto).
- **Como foi feito / decisões:**
  - `planCountFetch` ganhou um parâmetro **`pendingSince`** que o §4 não previa. É a peça central: o prazo é ancorado no PRIMEIRO gatilho da rajada, não em `now`. Sem isso, o chamador recriar o timer a cada evento empurraria o vencimento para sempre — exatamente o 2º bug do §2.2 (numa rajada a contagem nunca era buscada), que a versão ingênua reintroduziria.
  - `resolveTabCounts` devolve a **mesma referência** de entrada quando ela já é válida (não recria objeto por render — `tabCounts` desce como prop); só normaliza no caso degenerado (campo faltando/não numérico ⇒ 0, nunca `NaN`).
  - `countSpecKey` ordena chaves **e** valores de lista: reordenar etiquetas não pode disparar reset.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --test web/static/js/services/tabCounts.test.js` → **24/24 verde**. Inclui o teste que trava a ancoragem do prazo (`o prazo é ancorado no INÍCIO da rajada`) e o de `resolveTabCounts` devolvendo a mesma referência.

---

### Fase F1 — Ligar o hook ao módulo (🔴 depende de F0)

**Objetivo:** o badge para de oscilar. É a fase que o usuário enxerga.

**Itens:**
1. [sequencial] **M4** — trocar [useConversationFilters.js:138](web/static/js/components/contacts/hooks/useConversationFilters.js#L138) por `resolveTabCounts({ serverCounts, clientCounts: clientTabCounts, serverMode })`.
2. [sequencial] **M3** — reescrever o efeito [:141-171](web/static/js/components/contacts/hooks/useConversationFilters.js#L141-L171) para consultar `planCountFetch`:
   - manter o `setServerCounts(null)` de [:148](web/static/js/components/contacts/hooks/useConversationFilters.js#L148) (saída de `serverMode` — D2);
   - **remover** o de [:151](web/static/js/components/contacts/hooks/useConversationFilters.js#L151) e limpar **só** quando `action === 'reset_and_fetch'` (D1);
   - guardar `lastSpecKey` e `lastFetchAt` em `useRef` (não em estado — não devem provocar render).
3. [sequencial] **M5** — trocar o guard: hoje o `alive` é por execução do efeito ([:155,164,167](web/static/js/components/contacts/hooks/useConversationFilters.js#L155)); como o efeito deixa de re-rodar a cada evento, a resposta passa a ser descartada quando **a chave de spec da requisição** ≠ a atual. ⚠️ Sem isso, uma resposta em voo de um filtro antigo sobrescreve o total do filtro novo.
4. [paralelo] Manter `contacts` como **gatilho** (é o sinal de "algo mudou"), mas com o teto de D3 — a alternativa "tirar `contacts` das deps" perderia o refresh quando uma conversa nova entra na lista.
5. [paralelo] Comentário no topo do efeito explicando **por que** o total não é limpo (o próximo a ler vai querer "simplificar" de volta).

**Pronto quando:**
- com o filtro parado e tráfego chegando, o badge **não muda de valor** exceto quando o total real muda;
- trocar "Abertas → Todas" atualiza o número em ≤ ~1 s (reset + fetch imediato), sem passar pelo número da página;
- "Mostrando X de Y" ([ContactList.js:826](web/static/js/components/contacts/ContactList.js#L826)) para de piscar e a aba "Menções" para de aparecer/sumir;
- `node --test` verde nos módulos de serviço.

#### Status de execução — Fase F1
**Estado:** ✅ Concluída (2026-08-19) — falta só a validação ao vivo (F3)
- **O que foi feito:** [useConversationFilters.js](web/static/js/components/contacts/hooks/useConversationFilters.js) — import do módulo novo; **M4** (`tabCounts` passa por `resolveTabCounts`); **M3** (efeito da contagem reescrito sobre `planCountFetch`, com `countSpec`/`countKey` memoizados e os refs `countSpecRef`/`countPendingRef`/`countFetchedAtRef`/`countMountedRef`); **M5** (guard por chave de spec no lugar do `alive`).
- **Como foi feito / decisões:**
  - O `setServerCounts(null)` de `:151` saiu; sobrou **um único** ponto de limpeza, dentro de `action === 'reset_and_fetch'` (D1). O de saída de `serverMode` virou o ramo `idle`.
  - **Erro de rede deixou de limpar o total** (o `.catch` fazia `setServerCounts(null)`): uma piscada de rede derrubaria o badge para o número da página, que é justamente o sintoma que este plano existe para matar.
  - Deps do efeito: `[countKey, countSpec, contacts]`. `countKey` é uma STRING, então recriar `tagFilter`/`advFilters` com o mesmo conteúdo deixou de invalidar a contagem — ganho colateral que não estava no plano.
  - **P1 decidido: `minIntervalMs = 4000`** (opção (a)). Não tratei "ação do próprio operador" como spec-like: 4 s de defasagem no pior caso não justifica um segundo caminho de invalidação.
- **Problemas / pendências:** os critérios observáveis do "Pronto quando" (badge parado com tráfego, troca de filtro sem passar pela página, rodapé/aba Menções) só podem ser conferidos numa instância com tráfego — ver F3.
- **Verificação:** sintaxe (`node --input-type=module --check`) OK; suíte do frontend inteira **537/537 verde**. Além disso, simulei o ciclo real do hook (refs + timer) contra o módulo puro: 20 eventos a 1/s com filtro parado ⇒ **1 reset** (o do mount, invisível) e 6 fetches, contra 20 resets hoje; rajada densa de 60 eventos em 3 s ⇒ **2 fetches**, contra **zero** hoje; troca de filtro no meio da rajada ⇒ reset único no instante da troca, total novo 300 ms depois.

---

### Fase F2 — Patch no-op não realoca o array (🟢 paralela a F0/F1)

**Objetivo:** atacar a causa: parar de trocar a identidade de `contacts` quando **nada mudou**. Além de estabilizar a contagem, corta re-render da sidebar inteira a cada recibo de entrega de qualquer conversa da instância.

**Itens:**
1. [sequencial] **M8** — `patchRows(rows, matches, patch)` em [conversationRows.js](web/static/js/services/conversationRows.js): aplica o patch nas linhas que casam e devolve **a mesma referência** quando nenhuma linha mudou de valor. Mesmo espírito do `changed` já usado nos handlers de `setContactData`.
2. [paralelo] **M6** — `upsertConversationRow` ([conversationRows.js:742-771](web/static/js/services/conversationRows.js#L742-L771)) devolve `prev` quando a linha mesclada é igual à existente. ⚠️ **Armadilha:** `patch.updated_at = Math.max(...)` é atribuído **sempre** ([:768](web/static/js/services/conversationRows.js#L768)), então o patch nunca é literalmente vazio — a comparação tem de ser sobre os **valores resultantes**, não sobre `Object.keys(patch).length`.
3. [paralelo] **M7** — `applyConversationEvent` ([conversationPatch.js:94-101](web/static/js/services/conversationPatch.js#L94-L101)) devolve `rows` quando nenhuma linha trocou de identidade (ele já preserva identidade **por linha**; falta preservar a do array).
4. [paralelo] **M9** — trocar os `prev.map(...)` crus pelos helpers: [:172](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L172), [:296](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L296), [:520](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L520), [:551](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L551), [:591](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L591), [:620](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L620), [:677](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L677), [:709](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L709). ⚠️ **Não** mexer nos sites que **filtram/inserem** ([:211](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L211), [:323](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L323), [:352](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L352), [:380](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L380), [:573](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L573)) — ali a mudança de tamanho é real e os gates dos planos 72 F3/F4 dependem da forma atual.
5. [sequencial] **M10** — testes em [conversationRows.test.js](web/static/js/services/conversationRows.test.js) e `conversationPatch.test.js`: (a) patch no-op ⇒ `assert.strictEqual(out, input)` (mesma referência); (b) patch real ⇒ referência nova **e** valores certos; (c) a ordenação do `upsertConversationRow` continua correta quando **há** mudança.

**Pronto quando:** `node --test` verde nos três arquivos; um `conversation_upsert` idêntico ao estado atual devolve a mesma referência; nada muda na tela (é refactor de identidade, não de comportamento).

#### Status de execução — Fase F2
**Estado:** ✅ Concluída (2026-08-19)
- **O que foi feito:** **M8** `patchRows(rows, matches, patch)` + o privado `rowNeedsPatch` em [conversationRows.js](web/static/js/services/conversationRows.js); **M6** short-circuit no MERGE do `upsertConversationRow`; **M7** `applyConversationEvent` devolve `rows` no no-op ([conversationPatch.js](web/static/js/services/conversationPatch.js)); **M9** os 8 `prev.map(...)` crus do hook de WS trocados por `patchRows`; **M10** testes de identidade nos dois arquivos.
- **Como foi feito / decisões:**
  - `patch` aceita objeto **ou função** `(row) => partial` — o site do nome do contato (`updatedInfo.name || c.name`) depende da linha.
  - **M7 ficou mais forte que o planejado:** além da identidade do array, `applyConversationEvent` compara os VALORES, então um evento que reafirma o estado atual (mesmo assignee, mesmo status) também é no-op. `conv_labels` é array e compara por referência — lado seguro do erro.
  - **Dois sites da lista "não tocar" ganharam um guard mínimo:** em `:380` e `:569`, `Array.filter` devolvia array novo mesmo sem remover ninguém. Adotei o resultado só quando `kept.length !== next.length`. Os gates dos planos 72 F3/F4 ficaram **literalmente intactos** — a mudança é só qual referência retornar.
  - Em `:703` a condição `m.status !== c.last_message_status` saiu do predicado: `patchRows` já faz exatamente essa comparação. Comportamento idêntico, com a verificação num lugar só.
  - Não toquei `:336` (`conversation_pinned`, ordena de propósito) nem os `filter`/insert de `:210`/`:322`/`:351`.
- **Problemas / pendências:** dois testes novos falharam na primeira rodada — o fixture omitia `last_activity_at`, então a 1ª mesclagem normalizava `updated_at: undefined → 0` e ERA uma mudança real. O fixture estava errado, não o código; corrigido e acrescentado um 3º upsert provando que estabiliza.
- **Verificação:** `node --test conversationRows.test.js` **93/93** e `conversationPatch.test.js` **14/14** (9 casos novos de "mesma referência no no-op"); suíte do frontend **537/537 verde**.

---

### Fase F3 — Validação ao vivo + regressão (🔴 depende de F1 e F2)

**Objetivo:** provar no ambiente real, com tráfego real, que o número parou de oscilar — e que nada mais quebrou.

**Itens:**
1. [sequencial] Abrir o hub numa instância com tráfego (a de produção tem ~26 mensagens visíveis/min no pico) e **observar 60 s** com o filtro parado: o badge de **Todas** não pode mudar de valor a não ser que uma conversa realmente abra/feche.
2. [sequencial] Exercitar as transições: trocar status (Abertas ↔ Resolvidas ↔ Todas), aplicar/remover funil de etiqueta, aplicar filtro avançado, alternar Arquivadas. Em cada troca o número tem de **saltar direto para o novo total**, sem passar pelo número da página.
3. [paralelo] Conferir os três sintomas colaterais do §2.6: rodapé "Mostrando X de Y" estável, aba "Menções" sem piscar, badges de "Minhas"/"Não atribuídas" estáveis.
4. [paralelo] Rajada: enviar várias mensagens seguidas e confirmar que a contagem **continua sendo atualizada** dentro do teto (hoje, numa rajada, ela simplesmente não é buscada).
5. [sequencial] Suíte: `node --test` nos módulos puros afetados + `venv/bin/python -m pytest tests/integration -k "conversation or count"` (nenhuma mudança de backend é esperada — o objetivo é só provar que continua verde).

**Pronto quando:** os 5 itens acima conferidos, nenhuma regressão em busca / scroll infinito / abas / aba Arquivadas.

#### Status de execução — Fase F3
**Estado:** 🟡 Em andamento — parte automatizada verde; a validação ao vivo é do usuário
- **O que foi feito:** rodada a suíte `node --test` inteira do frontend e conferido o escopo do diff.
- **Como foi feito / decisões:** —
- **Problemas / pendências:**
  - **Itens 1–4 (validação ao vivo) NÃO foram executados:** exigem o hub aberto numa instância com tráfego real; não há como dirigir o navegador daqui.
  - **Item 5 (pytest) NÃO foi executado:** este checkout não tem `venv/` nem `pytest` instalado. Como contraprova de D5, `git diff --stat` mostra **zero** arquivos em `server/`, `db/`, `agent/` e `storages/` — só os 6 do frontend mais os 2 novos.
- **Verificação:** `node --test` sobre todos os `*.test.js` de `web/static/js` → **537/537 verde**. Simulação do ciclo do hook (ver F1) cobrindo rajada esparsa, rajada densa e troca de filtro no meio.

---

## 6 — Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Resposta em voo × troca de filtro | Um total do filtro ANTIGO chega depois e sobrescreve o novo — hoje o `alive` cobre porque o efeito re-roda; depois de F1 ele não re-roda mais a cada evento | M5: descartar por **chave de spec**, não por execução do efeito. Teste dedicado |
| Manter o total durante o refetch (D1) | Se o reset por spec falhar, o usuário vê o total do filtro anterior | `countSpecKey` derivado de `buildCountParams` com chaves ordenadas; teste de "spec muda ⇒ reset" |
| Teto de frequência (D3) | Um total que mudou de verdade demora até `minIntervalMs` para aparecer | Teto na casa de poucos segundos (ver P1); a **lista** continua ao vivo pelo `conversation_upsert` — só o número agrega |
| Short-circuit do `upsertConversationRow` (M6) | Comparação rasa demais engole uma atualização real (preview/unread travados) | Comparar os **valores resultantes** de todos os campos do patch, incluindo `updated_at` ([:768](web/static/js/services/conversationRows.js#L768)); testes de merge já existentes em [conversationRows.test.js](web/static/js/services/conversationRows.test.js) precisam continuar verdes |
| Call sites que mudam o TAMANHO da lista | Trocar um `filter`/`insert` por `patchRows` quebraria os gates dos planos 72 F3/F4 | F2 item 4: lista explícita do que **não** tocar ([:211](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L211), [:323](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L323), [:352](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L352), [:380](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L380), [:573](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L573)) |
| Identidade estável demais | Um consumidor que dependia de `contacts` **sempre** mudar para re-renderizar pode parar de atualizar | Os consumidores derivam por `useMemo` sobre `contacts` — se o conteúdo não mudou, o derivado também não. Cobrir no F3 (item 3) |
| `serverMode` alternando | Sair de `serverMode` com total antigo em memória mostraria número de outro universo | [:148](web/static/js/components/contacts/hooks/useConversationFilters.js#L148) já limpa; teste `action === 'idle'` |
| Modo escuro | Nenhuma cor/superfície nova | Só lógica de estado — nada a validar visualmente além do §2.6 |
| Backend / migration / plugin | Nenhum | D5: `count_tab_counts` e a rota ficam intactos; sem DDL, sem restart de plugin |

---

## 7 — Perguntas em aberto

**P1 — Qual o teto de frequência (`minIntervalMs`) do refetch por "a lista mudou"?**
✅ DECIDIDO (2026-08-19): **4000 ms** — opção (a), em `DEFAULT_MIN_INTERVAL_MS` ([tabCounts.js](web/static/js/services/tabCounts.js)). A ressalva do fim deste parágrafo foi avaliada na F1 e **recusada**: não vale um segundo caminho de invalidação para a ação do próprio operador, já que a defasagem máxima é de 4 s. Contexto original: o total muda devagar (conversa nova / resolvida), mas o gatilho é altíssimo (~1 evento/s). (a) **4000 ms** — badge no máximo 4 s defasado, ~15 requisições/min por operador no pior caso, cada uma de 3 ms; (b) 10000 ms — mais econômico, defasagem perceptível quando o operador resolve uma conversa e olha o número; (c) 1000 ms — praticamente ao vivo, mas volta a ser 1 requisição/s por aba aberta. **Recomendação:** (a). ⚠️ Independentemente do valor, uma mudança **do próprio operador** (resolver/atribuir) deve poder furar o teto — avaliar na F1 se vale tratar esses eventos como "spec-like" (fetch imediato).

**P2 — Migrar a contagem para push por WebSocket?**
⏸️ ADIADO. Seria a arquitetura correta (o servidor já sabe quando um total muda), mas o `/ws` entrega tudo para todos, sem escopo por canal/usuário — e a contagem é **por usuário** (`mine`, `mentions`). Depende do plano 90. (a) manter pull com teto; (b) push. **Recomendação:** (a) agora; reabrir junto do plano 90.

**P3 — Mostrar um estado de carregamento no 1º paint em vez do número da página?**
⏸️ A DECIDIR. Hoje, no primeiro paint, o badge mostra `50` por ~300 ms antes do total chegar. (a) manter (D2) — é um número plausível e evita badge vazio piscando; (b) mostrar `—`/skeleton até o total chegar. **Recomendação:** (a); reavaliar só se a F3 mostrar que o primeiro paint incomoda.

**P4 — Limitar o crescimento da lista pelo insert-gate?**
⏸️ ADIADO. O `conversation_upsert` insere conversas ausentes que casam a view ([:242](web/static/js/components/contacts/hooks/useConversationWsEvents.js#L242)), então a lista carregada cresce indefinidamente numa aba deixada aberta (custo de render e de memória, não de correção). Fora do escopo desta correção — depois de F1 isso deixa de afetar o badge.

---

## 8 — Checklist de verificação

- [ ] `node --test web/static/js/services/tabCounts.test.js` verde (F0).
- [ ] `node --test web/static/js/services/conversationRows.test.js` e `conversationPatch.test.js` verdes, incluindo os novos casos de "mesma referência no no-op" (F2).
- [ ] Badge de **Todas** estável por 60 s com tráfego real e filtro parado (F3.1).
- [ ] Troca de filtro (status / etiqueta / avançado / arquivadas) salta direto para o novo total, sem exibir o número da página (F3.2).
- [ ] Rodapé "Mostrando X de Y" não pisca; aba "Menções" não aparece/some (F3.3).
- [ ] Durante uma rajada de mensagens, a contagem **continua sendo atualizada** dentro do teto (F3.4) — comportamento novo, hoje ela nem é buscada.
- [ ] Resposta de contagem em voo é descartada ao trocar de filtro (M5) — verificar no DevTools que o número final é o do filtro atual.
- [ ] Busca, scroll infinito e aba Arquivadas continuam funcionando (sem regressão dos planos 50 F8 / 62 F6 / 72 F3-F4).
- [ ] Suíte do core verde no Postgres (`WHATSBOT_TEST_DB_URL`): `venv/bin/python -m pytest tests/integration -k "conversation or count"` — esperado inalterado (D5).
- [ ] `git diff` confirma: **nenhum** arquivo de `server/`, `db/` ou `storages/plugins/` tocado.
- [ ] Reload / back-forward do hub mantém o número correto.

---

## 9 — Apêndice: arquivos-chave

**Frontend — a tocar:**
- `web/static/js/services/tabCounts.js` (**novo**, M1) + `tabCounts.test.js` (**novo**, M2).
- [web/static/js/components/contacts/hooks/useConversationFilters.js:138](web/static/js/components/contacts/hooks/useConversationFilters.js#L138), [:141-171](web/static/js/components/contacts/hooks/useConversationFilters.js#L141-L171) — M3/M4/M5.
- [web/static/js/services/conversationRows.js:726-771](web/static/js/services/conversationRows.js#L726-L771) — M6/M8.
- [web/static/js/services/conversationPatch.js:94-101](web/static/js/services/conversationPatch.js#L94-L101) — M7.
- [web/static/js/components/contacts/hooks/useConversationWsEvents.js](web/static/js/components/contacts/hooks/useConversationWsEvents.js) — M9 (8 sites; ver F2 item 4 para os que **não** mudam).
- [web/static/js/services/conversationRows.test.js](web/static/js/services/conversationRows.test.js) — M10.

**Frontend — NÃO tocar (só referência):**
- [web/static/js/components/contacts/ConversationFilterBar.js:482-485](web/static/js/components/contacts/ConversationFilterBar.js#L482-L485) — render das abas (o gate de "Menções" em [:483](web/static/js/components/contacts/ConversationFilterBar.js#L483)).
- [web/static/js/components/contacts/ContactList.js:826-834](web/static/js/components/contacts/ContactList.js#L826-L834) — "Mostrando X de Y".
- [web/static/js/components/contacts/hooks/useConversationList.js:27](web/static/js/components/contacts/hooks/useConversationList.js#L27) — `SIDEBAR_PAGE = 50`.
- [web/static/js/services/conversationFilterSpec.js:118-140](web/static/js/services/conversationFilterSpec.js#L118-L140) — `buildCountParams` (base do `countSpecKey`).
- [web/static/js/services/api.js:555](web/static/js/services/api.js#L555) — `countConversations`.

**Backend — NÃO tocar (só referência, D5):**
- [server/routes/conversations.py:245-269](server/routes/conversations.py#L245-L269) — `/api/atendimentos/count` + `_run_count`.
- [db/repositories/conversation_repo.py:544-589](db/repositories/conversation_repo.py#L544-L589) — `count_tab_counts`.
- [db/repositories/conversation_query.py:115-119](db/repositories/conversation_query.py#L115-L119) — `enriched_from` (o FROM do COUNT).
- [agent/message_listeners.py:36-63](agent/message_listeners.py#L36-L63) — `broadcast_conversation_upsert` (o emissor de maior volume).

**Planos relacionados:**
- plano 69 (F2/F3/F4) — origem do `serverMode`, da contagem server-side e do "Mostrando X de Y".
- plano 72 (F3/F4/F7/F8) — insert-gate e drop-gate do `conversation_upsert` (não mexer).
- plano 28 — `conversation_upsert` como Event-Carried State Transfer.
- plano 90 — escopo do WebSocket por canal (pré-requisito de P2).
