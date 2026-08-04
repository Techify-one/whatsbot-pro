# Plano 99 — Buscar mensagens dentro da conversa e pular para uma data (e destravar o salto que hoje falha em silêncio)

> **Status:** ✅ **IMPLEMENTADO** (2026-07-31) · **Data do plano:** 2026-07-31 · **Escopo:** grande
> **Origem:** pedido do usuário (2026-07-31) — "no WhatsApp eu consigo pesquisar dentro da conversa, e pesquisar por data; queria isso aqui". **Método:** leitura do repo de mensagens, dos dois endpoints de thread, da camada de busca e do hook de scroll, com `arquivo:linha` verificado por `sed`/`grep` nesta sessão.
> A infra de busca textual **já existe e é rápida** (índice GIN trigram + `unaccent` sobre `messages.content`), mas está cabeada só à busca GLOBAL da sidebar, que devolve **um hit por contato**. O que **não existe** é (a) busca escopada a uma conversa devolvendo a lista de ocorrências, (b) qualquer filtro por data de calendário, e (c) — o bloqueador real — **carregamento ancorado**: a paginação do histórico é só para trás (`before_id`), então "pular para 3 de janeiro" não tem como funcionar. De quebra, o salto para uma mensagem fora da janela carregada **já está quebrado hoje, em silêncio**.
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| D1 ✅ (2026-07-31) | **Nada muda na busca GLOBAL da sidebar.** A feature é dentro da conversa aberta | [contact_search.py](../db/search/contact_search.py) e [contact_repo.list_contacts_page](../db/repositories/contact_repo.py#L369) ficam intocados. Reusamos os **helpers** (`f_unaccent`, `SEARCH_EXCLUDED_ROLES`, `match_snippet`), não a query |
| D2 ✅ (2026-07-31) | Duas capacidades no mesmo plano: **buscar texto** na conversa e **ir para uma data** | Compartilham a mesma espinha (F0). Poderiam ser entregues em releases separadas — ver §5 (a F0 sozinha já conserta um bug de produção) |
| D3 ✅ (2026-07-31) | A **Fase 0 é bloqueante**: sem carregamento ancorado nada disso funciona | Nenhuma UI antes da F0 verde. Tentativa de atalho = feature que "acha" a mensagem e não consegue mostrá-la |
| D4 ✅ (2026-07-31) | O usuário reconheceu que a sidebar **não** filtra por dia de calendário (o filtro "Última atividade" é em **dias relativos**) e mesmo assim **não quer mexer nela** | Nenhuma dimensão nova em [db/filters/registry.py](../db/filters/registry.py) |

---

## 1. Resumo executivo

Quatro constatações medidas no código:

1. **Busca dentro da conversa não existe.** Nenhum dos dois endpoints de thread aceita texto ([conversations.py:323](../server/routes/conversations.py#L323), [contacts.py:712](../server/routes/contacts.py#L712)) e [message_repo.py](../db/repositories/message_repo.py) não tem função de busca.
2. **A infra de busca existe e é boa.** Índice GIN trigram parcial `idx_msg_content_trgm` + função `f_unaccent` IMMUTABLE (migration [0060](../db/alembic/versions/20260720_0060_trgm_unaccent_search.py)), predicado padronizado em [contact_search.py:222-230](../db/search/contact_search.py#L222-L230), roles nunca pesquisáveis em [:119](../db/search/contact_search.py#L119), snippet em [:61-88](../db/search/contact_search.py#L61-L88). O que falta é uma query **escopada por conversa** devolvendo **N ocorrências** — a global é `DISTINCT ON (contact_id)`, ou seja, **um** hit por contato ([:292-307](../db/search/contact_search.py#L292-L307)).
3. **Filtro por data de calendário não existe em lugar nenhum.** A dimensão `activity` recebe **dias relativos** ([registry.py:51](../db/filters/registry.py#L51) → [translate.py:204](../db/filters/translate.py#L204): `ctx.now - days * 86400`) e opera sobre `conversations.last_activity_at`, não sobre mensagens.
4. **O bloqueador: a paginação é unidirecional.** `before_id` e nada mais — sem `after_id`, sem `around`, sem `offset` ([message_repo.py:96](../db/repositories/message_repo.py#L96), [:112](../db/repositories/message_repo.py#L112), [:130](../db/repositories/message_repo.py#L130)); `has_more` é um booleano que só significa "há mais antigas".

E um **bug já em produção** que este plano conserta de passagem: quando o alvo de um salto não está na janela carregada, `focusMessage` devolve `false` sem disparar carregamento nenhum ([ContactDetail.js:286-289](../web/static/js/components/contacts/ContactDetail.js#L286-L289)), e a flag `justPrepended` faz o efeito de scroll **retornar antes de tentar o foco** ([ContactDetail.js:322-329](../web/static/js/components/contacts/ContactDetail.js#L322-L329)) justamente na atualização em que o alvo chegaria. Se a mensagem vier na última página possível, **o salto nunca acontece e nada avisa o operador**.

A forma da solução: **F0** constrói a janela ancorada (backend bidirecional + merge no cliente + "voltar ao fim" + conserto do salto); **F1/F2** acrescentam a busca escopada (backend + UI); **F3/F4** acrescentam o "ir para data" (resolução dia → âncora + calendário).

---

## 2. Como funciona hoje (mapa)

### 2.1 Os dois endpoints de thread

| Endpoint | `arquivo:linha` | Params | Repo |
|---|---|---|---|
| `GET /api/atendimentos/{conv_id}/messages` | [conversations.py:323-327](../server/routes/conversations.py#L323-L327) | `mark_read`, `limit` (default `PAGE_MSGS=50`, cap `CAP_MSGS=200`), `before_id` | `message_repo.get_by_conversation` ([:385](../server/routes/conversations.py#L385)) |
| `GET /api/contacts/{phone}` | [contacts.py:712-715](../server/routes/contacts.py#L712-L715) | `mark_read`, `channel_id`, `limit`, `before_id` | `get_by_conversation` ([:780](../server/routes/contacts.py#L780)) ou `get_all` no ramo legado all-channels ([:808](../server/routes/contacts.py#L808)) |

Truque comum do `has_more`: over-fetch de **+1** e descarte do índice 0 (a mais antiga da janela) — [conversations.py:383-388](../server/routes/conversations.py#L383-L388). Constantes em [server/pagination.py:21](../server/pagination.py#L21).

### 2.2 O repo (o que existe e o que falta)

[message_repo.py:130-148](../db/repositories/message_repo.py#L130-L148) — `_select_messages(cond, limit)`:

```
limit=None  → SELECT … ORDER BY ts                    (thread inteira, cronológica)
limit=N     → SELECT … ORDER BY ts DESC, id DESC LIMIT N   → reversed()   (as N MAIS RECENTES)
```

`before_id` entra como `messages.c.id < before_id` em `get_all` ([:107](../db/repositories/message_repo.py#L107)) e `get_by_conversation` ([:126](../db/repositories/message_repo.py#L126)).

⚠️ **Não existe** `after_id`, `around_id`, `before_ts`, `offset` nem cursor opaco para mensagens. Confirmado por `grep`.

### 2.3 O cliente da thread

| Peça | `arquivo:linha` | Papel |
|---|---|---|
| `loadOlder` | [useConversationSelection.js:359-390](../web/static/js/components/contacts/hooks/useConversationSelection.js#L359-L390) | `beforeId = Math.min(...ids)` ([:363](../web/static/js/components/contacts/hooks/useConversationSelection.js#L363)), guarda `loadingOlderRef`, token `detailSeqRef` contra troca de conversa |
| `prependOlder` | [threadData.js:63-69](../web/static/js/services/threadData.js#L63-L69) | Merge puro, dedup por `_id`, atualiza `has_more`. **Não tem irmão `appendNewer`** |
| `applyThreadResponse` | [threadData.js:44-51](../web/static/js/services/threadData.js#L44-L51) | Aplica a resposta + buffer de WS + carimbo `_threadKey` |
| `useReverseInfiniteScroll` | [useInfiniteScroll.js:124-151](../web/static/js/hooks/useInfiniteScroll.js#L124-L151) | Sentinela **só no topo**; restaura o scroll no prepend; expõe `justPrependedRef` |
| `new_message` (WS) | [useConversationWsEvents.js:848-853](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L848-L853) | ⚠️ **APPEND-ONLY** na thread aberta (plano 28) — não sabe que a janela pode estar ancorada no passado |

### 2.4 ⚠️ O salto que falha em silêncio (bug atual)

Cadeia: [ContactList.js:645](../web/static/js/components/contacts/ContactList.js#L645) (`onSelect(c, c.match_msg_id)`) → `selectContact` ([useConversationSelection.js:124](../web/static/js/components/contacts/hooks/useConversationSelection.js#L124)) → prop `scrollToMsg` ([Contacts.js:517](../web/static/js/components/contacts/Contacts.js#L517)) → `pendingScrollRef` ([ContactDetail.js:279-281](../web/static/js/components/contacts/ContactDetail.js#L279-L281)).

```
focusMessage(mid)                          ContactDetail.js:286-297
  el = chatRef.querySelector([data-mid])
  if (!el) return false        ← NÃO dispara nenhum carregamento

efeito [messages]                          ContactDetail.js:322-355
  if (justPrependedRef.current) { …; return }   ← retorna ANTES de tentar o foco
  …
  if (focusMessage(target)) { limpa pendingScrollRef }
  return                                   ← e nem rola pro fim
```

**Consequências observáveis hoje:** a conversa abre no topo da janela de 50 (parece "aberta no lugar errado"); o sentinela dispara `loadOlder` em cascata de 50 em 50; e na atualização em que o alvo finalmente chega, a flag `justPrepended` **come** a tentativa de foco. Se o alvo veio na última página (`has_more=false`), nada mais dispara — **o salto nunca acontece**.

Afeta **três** entradas já existentes: resultado da busca global ([ContactList.js:645](../web/static/js/components/contacts/ContactList.js#L645), onde `match_msg_id` é o **PK `messages.id`**, decorado em [contact_repo.py:342](../db/repositories/contact_repo.py#L342)), clique numa citação de mensagem antiga ([MessageBubble.js:93](../web/static/js/components/contacts/MessageBubble.js#L93)) e o deep-link `?message=<id>` ([routing.js:116-121](../web/static/js/components/shell/routing.js#L116-L121)).

### 2.5 A camada de busca que vamos reusar (sem alterar)

| Peça | `arquivo:linha` | Reuso |
|---|---|---|
| `SEARCH_EXCLUDED_ROLES` | [contact_search.py:119](../db/search/contact_search.py#L119) | `tool_call`, `system_notice`, `conversation_event`, `system` nunca entram na busca |
| `message_content_predicate` | [:222-230](../db/search/contact_search.py#L222-L230) | `content <> ''` + role permitido + `f_unaccent(lower(content)) ILIKE pattern` |
| `_folded` / `_folded_match` | [:179-219](../db/search/contact_search.py#L179-L219) | ⚠️ A expressão tem de casar **byte a byte** com o índice; `ILIKE` é deliberado (sob `lc_collate='C'` o `LIKE` perde acentuada em maiúscula) |
| `TRIGRAM_MIN_LEN = 3` | [:112](../db/search/contact_search.py#L112) | Piso de caracteres para o ramo de conteúdo |
| `match_snippet` | [:61-88](../db/search/contact_search.py#L61-L88) | Janela de ±40 chars com "…", preservando os acentos originais |
| Índice `idx_msg_content_trgm` | [migration 0060:42-59](../db/alembic/versions/20260720_0060_trgm_unaccent_search.py#L42-L59), espelhado em [tables.py:181-185](../db/tables.py#L181-L185) | GIN trigram **parcial** (mesmo predicado dos 4 roles) |
| Índice `idx_msg_conversation_ts` | [tables.py](../db/tables.py) (migration 0059) | Já serve o filtro `conversation_id` |

---

## 3. Inventário

### 3.1 Backend

| # | Item | Onde | O que falta | Risco | Esforço |
|---|---|---|---|---|---|
| B1 | Paginação bidirecional no repo | [message_repo.py:96-127](../db/repositories/message_repo.py#L96-L127) | `after_id` (mais **recentes** que X, em ordem ASC) e `around_id` (janela centrada) | médio | M |
| B2 | `has_more` nos dois sentidos | [conversations.py:383-388](../server/routes/conversations.py#L383-L388), [contacts.py:781-783](../server/routes/contacts.py#L781-L783) | hoje é um booleano só de "mais antigas"; vira `has_more_older` + `has_more_newer` (mantendo `has_more` como alias de compat) | médio | M |
| B3 | Params novos nos 2 endpoints | [conversations.py:323](../server/routes/conversations.py#L323), [contacts.py:712](../server/routes/contacts.py#L712) | `after_id`, `around_id` — mutuamente exclusivos com `before_id`, validados e capados por `clamp_limit` ([pagination.py:28](../server/pagination.py#L28)) | baixo | S |
| B4 | Busca escopada à conversa | novo em [message_repo.py](../db/repositories/message_repo.py) + `db/search/` | `search_in_conversation(conv_id, q, limit, offset)` → lista de `{id, ts, role, snippet}`, reusando §2.5 | médio | M |
| B5 | Endpoint de busca | novo, `server/routes/conversations.py` | `GET /api/atendimentos/{id}/messages/search?q=&limit=&offset=`, gate `conversation.read` (mesmo de [:390](../server/routes/conversations.py#L390)) | baixo | S |
| B6 | Resolver "primeiro id do dia" | novo em [message_repo.py](../db/repositories/message_repo.py) | `first_id_on_or_after(conv_id, ts)` → `messages.id` ou `None` | baixo | S |
| B7 | Endpoint "ir para data" | novo | `GET /api/atendimentos/{id}/messages/at?ts=<epoch>` → `{message_id}` (ou 204). Alternativa: parâmetro `at_ts` no próprio endpoint de mensagens (ver P2) | baixo | S |
| B8 | Testes de endpoint | [tests/test_endpoints.py](../tests/test_endpoints.py) | cobrir `after_id`/`around_id`/busca/`at`; a suíte roda em Postgres (`WHATSBOT_TEST_DB_URL`) | baixo | M |

### 3.2 Frontend

| # | Item | Onde | O que falta | Risco | Esforço |
|---|---|---|---|---|---|
| F1 | `appendNewer` | [threadData.js:63](../web/static/js/services/threadData.js#L63) | irmão puro de `prependOlder`, com teste em [threadData.test.js](../web/static/js/services/threadData.test.js) | baixo | S |
| F2 | Estado "janela ancorada" | [useConversationSelection.js](../web/static/js/components/contacts/hooks/useConversationSelection.js) | `loadNewer` + flag `anchored` (a janela **não** contém a última mensagem) | alto | L |
| F3 | Sentinela de baixo | [useInfiniteScroll.js:124](../web/static/js/hooks/useInfiniteScroll.js#L124) | hoje só há sentinela no topo; falta a de baixo (carregar mais recentes) sem quebrar a ancoragem de scroll | alto | M |
| F4 | `new_message` com janela ancorada | [useConversationWsEvents.js:848](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L848) | append-only cego: com a janela no passado, anexar uma mensagem de hoje cria um buraco silencioso no histórico | alto | M |
| F5 | "Voltar ao fim" | [ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js) | botão flutuante (com contador de novas, se P5=sim) que recarrega a página mais recente e sai do modo ancorado | médio | M |
| F6 | Conserto do salto silencioso | [ContactDetail.js:286-297](../web/static/js/components/contacts/ContactDetail.js#L286-L297) e [:322-355](../web/static/js/components/contacts/ContactDetail.js#L322-L355) | alvo ausente ⇒ pedir a janela ancorada nele (em vez de esperar cascata); e a flag `justPrepended` não pode engolir a tentativa de foco | médio | M |
| F7 | UI de busca no header | [ContactDetail.js:551](../web/static/js/components/contacts/ContactDetail.js#L551) / ao lado de [ConversationHeaderActions](../web/static/js/components/contacts/ConversationHeaderActions.js) | campo, contador "3 de 12", ⌃/⌄, `Esc` fecha, highlight das ocorrências | médio | L |
| F8 | UI "ir para data" | idem | calendário mês a mês (⚠️ **nada de `<input type=date>` sem tema** — usar `.wa-field` ou componente próprio) | médio | M |
| F9 | Cliente HTTP | [api.js:220-241](../web/static/js/services/api.js#L220-L241) | `afterId`/`aroundId` em `getContact`/`getConversationMessages` + `searchInConversation` + `messageAtDate` | baixo | S |

### 3.3 Falsos positivos descartados

| Descartado | Por quê |
|---|---|
| **Estender a busca global para devolver N hits por contato** | D1 trava: nada muda na sidebar. Além disso o `DISTINCT ON (contact_id)` ([contact_search.py:292](../db/search/contact_search.py#L292)) é **de propósito** — a sidebar mostra uma linha por contato |
| **Criar dimensão de filtro por data em [db/filters/registry.py](../db/filters/registry.py)** | O motor de filtros opera sobre **conversas**, não mensagens; e D4 trava a sidebar. A busca na conversa não passa por lá |
| **Índice novo para a busca por conversa** | `idx_msg_content_trgm` (conteúdo) + `idx_msg_conversation_ts` (escopo) já existem. Medir com `EXPLAIN ANALYZE` **antes** de propor DDL (ver P3) |
| **Virtualizar a lista de mensagens** | Tentador com 14 mil mensagens, mas é um refactor do render inteiro e não é pré-requisito de nada aqui. Fora de escopo |
| **`offset` na paginação da thread** | Instável sob inserção concorrente (mensagem nova desloca a janela). O keyset por `id` já é o padrão do repo (plano 50) |
| **Buscar dentro de `media_caption`/transcrição num primeiro momento** | `content` já é composto (a descrição de imagem reescreve o `content` — ver [CLAUDE.md](../CLAUDE.md), tabela `messages`), então boa parte já é alcançável. Ampliar o alvo é P4 |

---

## 4. Mudanças de infraestrutura

**Backend / DB**
- `_select_messages` ([message_repo.py:130](../db/repositories/message_repo.py#L130)) ganha direção. Hoje ele **sempre** pega as mais recentes (`ORDER BY ts DESC` + `reversed()`); para `after_id` a ordenação natural é ASC. Manter a devolução **sempre cronológica** para os chamadores.
- **Nenhuma migration prevista.** Qualquer DDL só entra se o `EXPLAIN ANALYZE` da F2 provar necessidade (P3).
- Nenhum evento WS novo; nenhum campo novo em `messages`.

**Frontend**
- `threadData.js` deixa de ser "merge só para trás": ganha `appendNewer` e o conceito de **janela** (`has_more_older`/`has_more_newer`), o que muda o contrato de `contactData`.
- `useInfiniteScroll.js` ganha o gêmeo "para baixo" — **sem** mexer na restauração de scroll do prepend, que é delicada ([:139-148](../web/static/js/hooks/useInfiniteScroll.js#L139-L148)).

**Plugins**
- Nenhum contrato de plugin muda. ⚠️ Conferir que os slots do chat (`chat.header.banner`) e o override `template.picker` continuam intactos com o header em **modo busca**.

---

## 5. Fases / Roadmap

```
WAVE 0   F0a (caracterização do salto quebrado)  ·  F0b (repo bidirecional)   ← paralelo
              │                                        │
              └────────────── barreira (F0b bloqueia tudo) ──────────────┐
WAVE 1   F0c (endpoints) → F0d (cliente: janela ancorada + WS + voltar ao fim)
              │
              └── F0e (conserto do salto) ──── ENTREGÁVEL 1: bug de produção morto
                       │
WAVE 2   F1 (busca backend) 🟢   ·   F3 (data backend) 🟢         ← paralelo
              │                           │
WAVE 3   F2 (UI de busca) 🟢     ·   F4 (UI calendário) 🟢         ← paralelo
              │                           │
WAVE 4   F5 (medição + polimento)                                  ← sozinha
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0a** | Caracterização | 🟢 | baixo | Teste que **falha hoje** provando o salto quebrado |
| 0 | **F0b** | Repo bidirecional | 🟢 [bloqueia: tudo] | médio | `after_id`/`around_id` verdes no repo |
| 1 | **F0c** | Endpoints | 🔴 [depende de: F0b] | médio | Params novos + `has_more_*` |
| 1 | **F0d** | Cliente: janela ancorada | 🔴 [depende de: F0c] | **alto** | Abrir ancorado, rolar nos dois sentidos, voltar ao fim |
| 1 | **F0e** | Conserto do salto | 🔴 [depende de: F0d] | médio | F0a fica verde — **entregável independente** |
| 2 | **F1** | Busca backend | 🟢 [depende de: F0e] | médio | Endpoint devolve ocorrências com snippet |
| 2 | **F3** | Data backend | 🟢 [depende de: F0e] | baixo | Dia → `message_id` |
| 3 | **F2** | UI de busca | 🟢 [depende de: F1] | médio | Buscar, navegar, saltar, destacar |
| 3 | **F4** | UI calendário | 🟢 [depende de: F3] | médio | Escolher dia e aterrissar nele |
| 4 | **F5** | Medição + polimento | 🔴 | médio | `EXPLAIN ANALYZE` em thread de 14 mil msgs; tema escuro |

> 💡 **A F0 sozinha já vale uma release.** Ela conserta um bug que hoje afeta três caminhos existentes (busca global, citação antiga, deep-link) — ver §2.4. Se o tempo apertar, entregue a Wave 1 e pare.

---

### Fase F0a — Caracterização do salto quebrado

**Objetivo:** provar o bug com um teste **antes** de mexer (disciplina "caracterização ANTES" do repo).

**Itens:**
1. `[sequencial]` Teste que cria uma conversa com > 2 páginas (≥ 120 mensagens), abre a thread e pede foco numa mensagem da **primeira** página cronológica (fora da janela inicial). Deve falhar hoje.
2. `[paralelo]` Registrar no teste os três caminhos afetados (§2.4) para nenhum ficar órfão depois.

**Pronto quando:** o teste **falha** de forma determinística e a mensagem de falha descreve o sintoma real.

#### Status de execução — Fase F0a
**Estado:** ✅ Concluída (2026-07-31)
- **O que foi feito:** [tests/test_plano99_conversa_ancorada.py](../tests/test_plano99_conversa_ancorada.py) — fixture `long_thread` (130 mensagens, > 2 páginas) + 16 testes asseverando o lado NOVO. Rodando contra o código anterior ao plano, **os 16 falhavam**; o de `around_id` falhava com a mensagem que descreve o sintoma real ("a janela ancorada NÃO contém o alvo"). O lado de UI do bug ficou em [web/static/js/services/threadJump.test.js](../web/static/js/services/threadJump.test.js) (7 testes).
- **Como foi feito / decisões:** a caracterização foi feita **no backend**, não no DOM. A causa raiz do salto silencioso é a ausência de paginação bidirecional — sem `around_id` o cliente não tem como pedir a janela, e testar o efeito de scroll exigiria um DOM que a suíte não tem. A decisão de UI (focar / pedir / desistir) virou o módulo puro `threadJump.js`, testável com `node --test`.
- **Problemas / pendências:** o `_row_to_dict` do repo expõe o PK como **`_id`** (não `id`) — os primeiros asserts erraram a chave. A busca (F1) usa `id` de propósito: é uma forma nova, não a da thread.
- **Verificação:** a caracterização inicial tinha 16 casos vermelhos antes das fases seguintes. A suíte final cresceu para **25/25 verdes** (inclui repo, endpoints e regressão de timestamps fora da ordem), rodada em banco Postgres isolado `whatsbot_test_p99`.

---

### Fase F0b — Repo bidirecional 🔴 bloqueante

**Objetivo:** `message_repo` sabe buscar para frente e em torno de uma âncora.

**Itens:**
1. `[sequencial]` `_select_messages` ([message_repo.py:130](../db/repositories/message_repo.py#L130)) ganha direção: `desc` (atual, para `before_id`/página recente) e `asc` (para `after_id`). Devolução **sempre cronológica**.
2. `[sequencial]` `get_by_conversation` / `get_all` ([:96](../db/repositories/message_repo.py#L96), [:112](../db/repositories/message_repo.py#L112)) aceitam `after_id`. A resposta de `before_id` no histórico normal permanece igual; o cursor interno precisa ser o composto **`(ts, id)`**, porque backfill/importação pode produzir PK fora da ordem cronológica.
3. `[sequencial]` `around_id`: metade antes + metade depois, numa transação de leitura só, devolvendo `has_more_older`/`has_more_newer` pelo mesmo truque de over-fetch (+1 de cada lado).
4. `[paralelo]` `first_id_on_or_after(conv_id, ts)` (B6) — a primeira mensagem cronológica com `ts >= X` na conversa. Usa `idx_msg_conversation_ts`.
5. `[paralelo]` Testes de repo: `before_id` inalterado; `after_id` devolve ASC; `around_id` centra; âncora inexistente; âncora na primeira/última mensagem; conversa vazia.

**Pronto quando:** testes do repo verdes **e** os testes existentes de paginação (plano 50) continuam verdes sem alteração.

#### Status de execução — Fase F0b
**Estado:** ✅ Concluída (2026-07-31)
- **O que foi feito:** em [db/repositories/message_repo.py](../db/repositories/message_repo.py): `_select_messages(..., forward=)` ganhou direção; `_keyset` escolhe a direção a partir do cursor; `get_all`/`get_by_conversation` aceitam `after_id`; e três funções novas — `window_around` (janela centrada), `first_id_on_or_after` (dia → id) e `read_window` (qual janela ler).
- **Como foi feito / decisões:** (1) **`read_window` mora no REPO, não na rota** — as duas rotas de thread precisam da mesma regra, e um import cruzado entre módulos de rota seria pior que a duplicação que ele evita. (2) **`window_around` divide o orçamento ao meio** (`older_take = (limit+1)//2`, incluindo a âncora) e faz o over-fetch de +1 de **cada lado**, então os dois `has_more_*` saem sem 2ª query. (3) **Âncora que não pertence à thread não é erro**: degrada para a página mais recente com `anchor_id=None` — quem chamou decide o que dizer ao operador (é o que sustenta o `give_up` da F0e). (4) A saída é **sempre cronológica**, então nenhum chamador precisa saber a direção usada. (5) `before_id`, `after_id` e os dois lados de `window_around` resolvem o id escopado à thread e comparam o par **`(ts, id)`**; a UI usa a primeira/última mensagem renderizada como cursor, nunca `min/max(id)`.
- **Problemas / pendências:** nenhum conhecido. A resposta do caminho quente monotônico permanece igual; a implementação deixou deliberadamente de usar apenas `id <`/`id >`, que era incorreto quando timestamp e PK divergiam.
- **Verificação:** os 8 testes originais de repo continuam verdes, mais `test_cursores_compostos_respeitam_timestamp_fora_da_ordem`, que cobre `before_id`, `after_id` e `around_id` no repo **e** nos endpoints, inclusive empate de timestamp.

---

### Fase F0c — Endpoints

**Objetivo:** os dois endpoints de thread expõem a janela ancorada.

**Itens:**
1. `[sequencial]` `after_id` e `around_id` em [conversations.py:323](../server/routes/conversations.py#L323) e [contacts.py:712](../server/routes/contacts.py#L712). Mutuamente exclusivos com `before_id` (400 quando combinados); `limit` capado por `clamp_limit(..., PAGE_MSGS, CAP_MSGS)`.
2. `[sequencial]` Resposta ganha `has_more_older` + `has_more_newer`. **Manter `has_more`** como alias de `has_more_older` durante toda a transição (o cliente antigo e [threadData.js:69](../web/static/js/services/threadData.js#L69) o leem).
3. `[sequencial]` ⚠️ `mark_read`: abrir **ancorado no passado** não pode marcar a conversa como lida por inteiro. Decidir e documentar (ver P6) — a hipótese do plano é `mark_read=False` implícito quando há âncora.
4. `[paralelo]` Preservar o que já roda no caminho: `_hydrate_quoted` ([conversations.py:391](../server/routes/conversations.py#L391)) e a resolução de `agent_key` → nome também valem para a janela ancorada.

**Pronto quando:** `curl` com `around_id` devolve janela centrada e os dois `has_more_*`; [tests/test_endpoints.py](../tests/test_endpoints.py) verde no Postgres.

#### Status de execução — Fase F0c
**Estado:** ✅ Concluída (2026-07-31)
- **O que foi feito:** `after_id`, `around_id` e **`at_ts`** nos DOIS endpoints ([conversations.py](../server/routes/conversations.py) `conversation_messages`, [contacts.py](../server/routes/contacts.py) `get_contact` — nos dois ramos, escopado por canal e legado all-channels). Resposta ganhou `has_more_older`, `has_more_newer`, `anchor_id` e `marked_read`; `has_more` **permanece** como alias de `has_more_older`.
- **Como foi feito / decisões:** (1) **P2 resolvida como (b)**: `at_ts` é PARÂMETRO do endpoint de mensagens, não endpoint próprio — resolve o dia e já devolve a janela numa ida só, com menos superfície de API. (2) **As quatro âncoras são mutuamente exclusivas** → 400 explícito quando combinadas, nos dois endpoints. (3) **P6 resolvida**: qualquer âncora força `mark_read=False` no servidor — o cliente não precisa lembrar de pedir, e pular para janeiro não zera o badge de hoje. `marked_read` na resposta declara o que aconteceu. (4) `_hydrate_quoted` e a resolução `agent_key → nome` continuam rodando sobre a janela ancorada (item 4).
- **Problemas / pendências:** nenhuma. O helper `_apply_window` em `contacts.py` evita repetir a cópia dos quatro campos nos dois ramos.
- **Verificação:** `test_pagina_recente_inalterada`, `test_before_id_byte_identico`, `test_around_id_*`, `test_after_id_*`, `test_ancoras_mutuamente_exclusivas`, `test_ancora_inexistente_nao_quebra`, `test_abertura_ancorada_nao_marca_como_lida`.

---

### Fase F0d — Cliente: janela ancorada 🔴 a fase mais arriscada

**Objetivo:** o painel sabe viver com uma janela que **não** termina na última mensagem.

**Itens:**
1. `[sequencial]` `appendNewer(prev, newer, hasMoreNewer)` em [threadData.js](../web/static/js/services/threadData.js) — espelho puro de `prependOlder` ([:63](../web/static/js/services/threadData.js#L63)), com teste em [threadData.test.js](../web/static/js/services/threadData.test.js).
2. `[sequencial]` `useConversationSelection`: `loadNewer` (irmão de [loadOlder:359](../web/static/js/components/contacts/hooks/useConversationSelection.js#L359)) **com a mesma guarda de troca de conversa** (`detailSeqRef`, [:376](../web/static/js/components/contacts/hooks/useConversationSelection.js#L376)) e o flag `anchored = has_more_newer === true`.
3. `[sequencial]` Sentinela **de baixo** em [useInfiniteScroll.js](../web/static/js/hooks/useInfiniteScroll.js), só ativa com `has_more_newer`. **Não tocar** na restauração de scroll do prepend ([:139-148](../web/static/js/hooks/useInfiniteScroll.js#L139-L148)).
4. `[sequencial]` ⚠️ `new_message` ([useConversationWsEvents.js:848](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L848)): com `anchored`, **não anexar** (criaria um buraco entre a janela e a mensagem nova). O comportamento previsto é ignorar o append e sinalizar "há mensagens novas" ao botão do item 5 — ver P5.
5. `[sequencial]` Botão flutuante "voltar ao fim" em [ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js): visível só com `anchored`, recarrega a página mais recente e limpa o estado ancorado. Padrão visual: `h-0` + `absolute` + `z-10`, igual ao chip de digitação ([:673](../web/static/js/components/contacts/ContactDetail.js#L673)).
6. `[sequencial]` Auto-scroll: o efeito `[messages]` ([ContactDetail.js:354](../web/static/js/components/contacts/ContactDetail.js#L354)) rola para o fim por padrão — **isso não pode acontecer** com a janela ancorada.

**Pronto quando:** abrir a conversa com `?message=<id antigo>` aterrissa na mensagem certa; rolar para cima e para baixo carrega nos dois sentidos; chegar mensagem nova não bagunça a janela; "voltar ao fim" recupera o comportamento normal; trocar de conversa no meio de um `loadNewer` não vaza dados da anterior.

#### Status de execução — Fase F0d
**Estado:** ✅ Concluída (2026-07-31)
- **O que foi feito:** `appendNewer` + `isAnchored` em [threadData.js](../web/static/js/services/threadData.js); `loadNewer` + `loadWindow`/`jumpToMessage`/`jumpToDate`/`backToBottom` em [useConversationSelection.js](../web/static/js/components/contacts/hooks/useConversationSelection.js); sentinela de baixo, supressão do auto-scroll e botão flutuante "Voltar ao fim" com contador em [ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js); guarda de `new_message` em [useConversationWsEvents.js](../web/static/js/components/contacts/hooks/useConversationWsEvents.js); `has_more_newer`/`anchor_id` em `shapeConvData`; params novos em [api.js](../web/static/js/services/api.js).
- **Como foi feito / decisões:** (1) **A sentinela de baixo é um `useScrollSentinel` SEPARADO**, não uma extensão do `useReverseInfiniteScroll`: o hook de cima carrega e restaura uma âncora de scroll num `useLayoutEffect` (código delicado), e anexar ABAIXO da viewport não desloca nada — não precisa de ancoragem alguma. O `useLayoutEffect` do prepend ficou intocado, como manda o §6. (2) **`appendNewer` só mexe no lado novo** e conserva o contador que chegou durante um GET: ao atingir um snapshot que dizia “fim”, só desconta as mensagens já conhecidas no início da leitura; um delta de WS força mais uma página e evita perder a corrida. (3) **P5 resolvida**: com a janela ancorada o `new_message` NÃO é anexado (criaria buraco silencioso) e vira `_newWhileAnchored`. (4) **`loadWindow` INCREMENTA o token `detailSeqRef`**; paginações apenas o capturam. `loadOlder`/`loadNewer` liberam seus locks em `finally`, inclusive em rejeição de rede. (5) Uma janela ancorada **descarta o buffer de WS**. (6) Uma ref síncrona de janela ancorada bloqueia `markAsRead`, clear otimista de badge, resync por `conversation_upsert`, reload de reconnect e append de `new_message` desde o início do GET — não só depois que `contactData` chega. (7) **Toda saída** (texto/retry, mídia e template) espera seu ACK e só então compartilha uma única transição para o fim; o GET não corre mais em paralelo com o POST.
- **Problemas / pendências:** a bolha otimista ainda pode aparecer no meio da janela durante o POST e o GET subsequente; ao terminar, a janela autoritativa recente a substitui. A interação visual precisa de navegador para validação final.
- **Verificação:** `node --test web/static/js/services/*.test.js` — **415/415 verdes**. Regressões específicas: `threadData.test.js` (cursor cronológico + mensagem durante o primeiro GET ancorado/`loadNewer`) e `outputTransition.test.js` (ACK antes da transição + coalescência de confirmações).

---

### Fase F0e — Conserto do salto silencioso ✅ entregável independente

**Objetivo:** um salto para mensagem fora da janela **sempre** funciona, ou avisa.

**Itens:**
1. `[sequencial]` `focusMessage` ([ContactDetail.js:286](../web/static/js/components/contacts/ContactDetail.js#L286)) devolvendo `false` deixa de ser beco sem saída: o dono dos dados pede a janela **ancorada no alvo** (`around_id`), em vez de esperar a cascata de `loadOlder`.
2. `[sequencial]` Corrigir a ordem em [ContactDetail.js:322-329](../web/static/js/components/contacts/ContactDetail.js#L322-L329): a flag `justPrepended` não pode engolir a tentativa de foco — ela deve suprimir **só** o auto-scroll para o fim.
3. `[sequencial]` Alvo inexistente (mensagem apagada / id inválido): mensagem clara ao operador, nunca falha muda.
4. `[paralelo]` Os três caminhos da §2.4 passam a usar o mesmo mecanismo.

**Pronto quando:** a F0a fica **verde**; clicar num resultado da busca global de uma mensagem de meses atrás aterrissa nela com o flash de highlight; clicar numa citação antiga idem.

#### Status de execução — Fase F0e
**Estado:** ✅ Concluída (2026-07-31)
- **O que foi feito:** a decisão do salto virou o módulo puro [threadJump.js](../web/static/js/services/threadJump.js) (`planJump` / `isRendered`), consumido pelo efeito `[messages]` do [ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js). Alvo fora da janela agora **pede a janela ancorada** (`around_id`) em vez de esperar a cascata; a flag `justPrepended` deixou de engolir a tentativa de foco; alvo inexistente avisa o operador com um toast. Os quatro caminhos passaram a usar o mesmo mecanismo — busca global da sidebar, deep-link `?message=`, citação de mensagem antiga e a busca dentro da conversa.
- **Como foi feito / decisões:** (1) A flag `justPrepended` agora suprime **só o auto-scroll para o fim**, que é a única coisa com que ela tem a ver — era o segundo tempo do bug. (2) `requestedJumpRef` guarda para QUAL alvo já pedimos a janela: sem isso, um alvo que não existe mais faria o pedido em laço. (3) `jumping` entrou nas deps do efeito para que a virada "em voo → chegou" reavalie o plano sozinha, mesmo que a lista de mensagens não mude. (4) **A citação `_hydrated` voltou a ser clicável** (`canJumpOutsideWindow` no [MessageBubble.js](../web/static/js/components/contacts/MessageBubble.js)): ela era desligada porque não dava para rolar até uma linha ausente do DOM — agora dá.
- **Problemas / pendências:** a viagem dupla foi removida: seleção por hit/deep-link inicia o **primeiro GET já com `around_id` e `mark_read=false`**, sem abrir/zerar a ponta recente antes. O foco DOM completo ainda requer validação manual no navegador.
- **Verificação:** [threadJump.test.js](../web/static/js/services/threadJump.test.js) — 7 testes cobrindo focar / pedir / esperar / desistir, mais a comparação por string (o alvo chega do DOM, da query string e da API).

---

### Fase F1 — Busca backend (escopada à conversa)

**Objetivo:** o servidor devolve a **lista** de ocorrências dentro de uma conversa.

**Itens:**
1. `[sequencial]` `search_in_conversation(conv_id, q, *, limit, offset)` reusando os helpers de §2.5 (`_folded_match`, `SEARCH_EXCLUDED_ROLES`, `TRIGRAM_MIN_LEN`, `match_snippet`). Ordem **mais recente primeiro** (é o que o WhatsApp faz). Devolve `{id, ts, role, snippet}` + `total`.
2. `[sequencial]` `GET /api/atendimentos/{id}/messages/search`, gate `conversation.read` (mesmo de [conversations.py:390](../server/routes/conversations.py#L390)). `q` abaixo de `TRIGRAM_MIN_LEN` ⇒ resposta vazia explícita, não erro.
3. `[paralelo]` Testes: acento/caixa (`joao` acha `João`), roles excluídos ausentes do resultado, `total` correto, paginação, conversa de outro usuário barrada pela permissão.

**Pronto quando:** busca por termo acentuado em caixa alta encontra; `tool_call`/`system_notice` nunca aparecem; [tests/test_endpoints.py](../tests/test_endpoints.py) verde.

#### Status de execução — Fase F1
**Estado:** ✅ Concluída (2026-07-31)
- **O que foi feito:** [db/search/message_search.py](../db/search/message_search.py) (`search_in_conversation`) + `GET /api/atendimentos/{id}/messages/search?q=&limit=&offset=` em [conversations.py](../server/routes/conversations.py).
- **Como foi feito / decisões:** (1) **Nada de `contact_search.py` foi alterado** (D1): `_folded_pattern`, `_folded_match` (via `message_content_predicate`), `SEARCH_EXCLUDED_ROLES`, `TRIGRAM_MIN_LEN`, `fold` e `match_snippet` são **importados**. A expressão dobrada precisa casar byte a byte com `idx_msg_content_trgm` — reescrevê-la tornaria o índice inaplicável e a busca viraria seq scan em silêncio. (2) **P1 resolvida como (a)**: escopo por `conversation_id`. O módulo aceita `contact_id` também, para o dia em que alguém quiser a busca por contato, mas a rota só expõe conversa. (3) **P4 resolvida como v1**: só `content` — ele já é COMPOSTO (a descrição de imagem o reescreve), então legenda e descrição já são alcançáveis. (4) `q` abaixo de 3 caracteres devolve **vazio com 200**, não erro: é o piso do trigrama, não um erro do operador. (5) Gate `conversation.read` **+ `_guard_conv`** — a busca não pode ser a porta lateral para uma conversa fora da caixa de entrada do operador. (6) O `snippet` é recortado só para as linhas da página, nunca para a thread inteira.
- **Problemas / pendências:** nenhum.
- **Verificação:** `test_busca_na_conversa_acha_ocorrencias` (ordem mais-recente-primeiro + shape), `test_busca_acento_e_caixa` (`joao`→João, `ORÇAMENTO`→orçamento), `test_busca_ignora_roles_internos` (`tool_call`/`system_notice` fora), `test_busca_curta_devolve_vazio_sem_erro`.

---

### Fase F2 — UI de busca no chat

**Objetivo:** o "Pesquisar mensagens" dentro da conversa.

**Itens:**
1. `[sequencial]` Botão de lupa no header do chat ([ContactDetail.js:551](../web/static/js/components/contacts/ContactDetail.js#L551)), perto de [ConversationHeaderActions](../web/static/js/components/contacts/ConversationHeaderActions.js). ⚠️ O header é `h-[59px]` com `pr-[56px]` e já está cheio — decidir se o modo busca **substitui** a barra (padrão WhatsApp) em vez de espremer mais um ícone.
2. `[sequencial]` Modo busca: campo `.wa-field`, debounce ~300ms (mesmo da sidebar, [useConversationList.js:352](../web/static/js/components/contacts/hooks/useConversationList.js#L352)), contador "3 de 12", ⌃/⌄, `Esc` fecha e restaura a janela anterior.
3. `[sequencial]` Navegar entre ocorrências ⇒ salto pela infra da F0e (`around_id` + highlight). Reusar a classe `wa-msg-highlight` já existente ([ContactDetail.js:293](../web/static/js/components/contacts/ContactDetail.js#L293)).
4. `[paralelo]` Destacar o termo **dentro** da bolha (não só o flash) — reaproveitar o padrão de highlight do snippet da sidebar ([ContactList.js:746-750](../web/static/js/components/contacts/ContactList.js#L746-L750)).
5. `[paralelo]` Sandbox e conversa sem `conversationId`: esconder a lupa (a busca é por conversa).

**Pronto quando:** buscar "boleto" numa thread de meses lista as ocorrências, ⌃/⌄ navega, cada salto aterrissa com highlight, `Esc` volta ao estado anterior sem recarregar a página.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída (2026-07-31)
- **O que foi feito:** botão de lupa no header + [ConversationSearchBar.js](../web/static/js/components/contacts/ConversationSearchBar.js) (campo `.wa-field`, debounce 300 ms, contador "3 de 12", ⌃/⌄, Enter/Shift+Enter, `Esc` fecha) + destaque do termo dentro da bolha via [searchHighlight.js](../web/static/js/services/searchHighlight.js) + `searchInConversation` em `api.js`.
- **Como foi feito / decisões:** (1) **O modo busca SUBSTITUI a barra do header** (item 1, padrão WhatsApp) em vez de espremer mais um controle num header `h-[59px]`/`pr-[56px]` já cheio. O slot `chat.header.banner` e o override `template.picker` ficam ABAIXO do header e não são tocados. (2) **O destaque na bolha é um módulo puro**, não um `replace` na string: o texto já passou por `formatWhatsApp` e é HTML — um replace ingênuo casaria "code" dentro de `<code style=…>` e produziria markup quebrado, que aqui é injetado como HTML. `highlightHtml` só entra nos segmentos de TEXTO e devolve a MESMA string sem termo (caminho normal do chat byte-idêntico). (3) A cor do `<mark>` é própria (`wa-search-hit` em `custom.css`). (4) Navegar além da página carregada busca a seguinte por `offset`; uma ref bloqueia dois fetches da mesma página. (5) Cada termo/conversa tem token **e AbortController**; fechamento/troca invalidam a resposta, resultados antigos são limpos já no debounce e também em erro, e `onJump` nunca roda depois do unmount. (6) A lupa **não aparece** sem atendimento nem no sandbox.
- **⚠️ Defeito na 1ª entrega (corrigido em 2026-07-31):** clicar na lupa fazia o **header inteiro sumir** e a barra nunca aparecia. Causa: uma **crase num comentário HTML dentro do template** ``html`…` `` da `ConversationSearchBar` — a crase FECHA o template literal, o resto do JSX vira JavaScript solto e o componente lança em runtime (`ReferenceError: field is not defined`); como o Preact não renderiza a subárvore de um componente que lança, a peça some **sem erro visível na tela**. `node --input-type=module --check` **aprovou** o arquivo quebrado (um par de crases o deixa sintaticamente válido), ou seja, a verificação que eu usei era um falso negativo. Correção: o comentário saiu de dentro do template. Rede de segurança: [htmTemplates.test.js](../web/static/js/services/htmTemplates.test.js) varre todo `web/static/js` e falha em qualquer template cortado no meio de um comentário — verificado vermelho com a crase, verde sem.
- **Problemas / pendências:** **desvio consciente do item 2** — `Esc` fecha a busca e apaga o destaque, mas **não devolve** o operador à janela em que ele estava antes de saltar. Levá-lo de volta ao fim desfaria justamente o salto que ele acabou de pedir (é comum fechar a busca para ler o entorno da ocorrência); é também o que o WhatsApp faz. Voltar continua a um clique de distância, no botão "Voltar ao fim".
- **Verificação:** [searchHighlight.test.js](../web/static/js/services/searchHighlight.test.js) — 7 testes; [apiSearch.test.js](../web/static/js/services/apiSearch.test.js) prova o encaminhamento de `AbortSignal` e paginação. O ciclo de vida do componente foi revisado estaticamente e ainda pede smoke visual no navegador.

---

### Fase F3 — "Ir para data" (backend)

**Objetivo:** dado um dia, o servidor diz **onde** aterrissar.

**Itens:**
1. `[sequencial]` Endpoint `at` (B7) sobre `first_id_on_or_after` (F0b·4). Sem mensagem naquele dia ⇒ a **próxima** mensagem cronológica (o WhatsApp aterrissa no dia seguinte com conteúdo), ou 204 se não houver nenhuma depois.
2. `[sequencial]` ⚠️ **Fuso**: o cliente manda `ts` em **epoch**, calculado no fuso do NAVEGADOR (`new Date(ano, mês, dia).getTime()/1000`). O servidor nunca interpreta "dia" — só compara epoch. Isso mantém a coerência com `formatDateSeparator` ([utils.js:47](../web/static/js/components/contacts/utils.js#L47)), que também resolve no fuso do navegador. **Documentar no docstring.**
3. `[paralelo]` Testes: dia com mensagens; dia vazio no meio; data anterior à primeira mensagem; data futura.

**Pronto quando:** `GET …/messages/at?ts=<epoch de 1/1>` devolve o id esperado nos quatro casos.

#### Status de execução — Fase F3
**Estado:** ✅ Concluída (2026-07-31)
- **O que foi feito:** `message_repo.first_id_on_or_after(ts, conversation_id=|contact_id=)` + o parâmetro `at_ts` nos dois endpoints de thread (ver F0c).
- **Como foi feito / decisões:** (1) **P2 = (b)**: parâmetro, não endpoint próprio — resolve o dia e devolve a janela numa ida só. (2) **Fuso (item 2)**: o cliente converte o dia em epoch com `new Date(ano, mês, dia)` (fuso do NAVEGADOR, em `chatCalendar.dayStartTs`) e o servidor **só compara epoch** — ele nunca interpreta "dia". Documentado no docstring da função e no cabeçalho do módulo de calendário. É o que mantém a coerência com `formatDateSeparator`, que também resolve no fuso do navegador. (3) Dia vazio ⇒ a **próxima** mensagem cronológica (o WhatsApp aterrissa no dia seguinte com conteúdo). (4) Sem nada depois da data ⇒ `anchor_id: null` e a **página mais recente**, não tela vazia — o painel avisa em vez de esvaziar o chat.
- **Problemas / pendências:** nenhum.
- **Verificação:** `test_at_ts_aterrissa_no_dia`, `test_at_ts_antes_do_inicio_cai_na_primeira`, `test_at_ts_depois_do_fim_nao_acha`, `test_repo_first_id_on_or_after` (os quatro casos), `test_repo_conversa_vazia`.

---

### Fase F4 — "Ir para data" (UI)

**Objetivo:** o calendário do WhatsApp.

**Itens:**
1. `[sequencial]` Calendário mês a mês, aberto pelo modo busca (como no WhatsApp, o ícone de calendário fica ao lado do campo). ⚠️ **Não** usar `<input type="date">` cru — `color-scheme` cobre o controle nativo, mas o layout não fica igual ao do print; avaliar componente próprio (P7).
2. `[sequencial]` Escolher um dia ⇒ chamar o endpoint da F3 ⇒ salto pela infra da F0e.
3. `[paralelo]` Dia sem mensagens: aterrissar no próximo dia com conteúdo e **dizer isso** (toast curto), em vez de parecer que ignorou o clique.
4. `[paralelo]` Limitar a navegação do calendário ao intervalo real da conversa (evita o operador vagar por meses vazios) — a confirmar se vale o custo de um endpoint de intervalo.

**Pronto quando:** escolher 1 de janeiro leva ao 1 de janeiro; dia vazio explica para onde foi; **modo escuro legível**.

#### Status de execução — Fase F4
**Estado:** ✅ Concluída (2026-07-31)
- **O que foi feito:** [chatCalendar.js](../web/static/js/services/chatCalendar.js) (aritmética pura: grade, virada de mês, dia→epoch) + [DatePickerPopover.js](../web/static/js/components/contacts/DatePickerPopover.js) (popover mês a mês, aberto pelo ícone de calendário ao lado do campo de busca) + o aviso de dia vazio (`handlePickDate` em `ContactDetail.js`). O rodapé **“Ir para o fim” chama `backToBottom` diretamente**; ele não simula escolher “hoje”, que podia falhar quando não havia mensagem posterior ao início do dia.
- **Como foi feito / decisões:** (1) **P7 resolvida como componente próprio**: o `<input type="date">` até segue o tema pelo `color-scheme`, mas o layout é do sistema operacional e não dá para apagar os dias que não levam a lugar nenhum — que é a informação útil aqui. (2) Toda a aritmética está num módulo PURO porque o que erra em calendário é a conta (mês que começa no domingo, fevereiro bissexto, virada de dezembro), não o desenho. (3) **Célula fora do mês fica vazia** em vez de mostrar o dia do mês vizinho: clicar ali é sempre engano do dedo, e apagar é mais honesto do que aceitar o clique e saltar para longe. (4) **Dias futuros desabilitados** e o botão "próximo mês" trava no mês corrente (`atLastMonth`) — não existe conversa no futuro. (5) O calendário **abre no mês da última mensagem carregada**, não em hoje: numa conversa antiga, abrir em hoje obrigaria a navegar meses para trás toda vez. (6) **Item 3**: dia sem mensagens aterrissa no próximo dia com conteúdo **e diz isso** num toast ("Sem mensagens nesse dia — abrimos em 5 de janeiro"), comparando o dia pedido com o `anchorTs` devolvido pelo `loadWindow`.
- **Problemas / pendências:** o **item 4** (limitar a navegação ao intervalo real da conversa) ficou parcial: o teto (hoje) está implementado, o **piso** (a primeira mensagem da conversa) não — exigiria um endpoint de intervalo, e o plano já marcava isso como "a confirmar se vale o custo". Na prática o operador que passa do começo recebe o aviso de "nenhuma mensagem nessa data ou depois dela"… na verdade cai na primeira mensagem, que é o comportamento certo.
- **Verificação:** [chatCalendar.test.js](../web/static/js/services/chatCalendar.test.js) — 9 testes (fuso do navegador, alinhamento do 1º dia, fevereiro bissexto, dias futuros, virada de ano nos dois sentidos, cursor inicial, trava do "próximo mês").

---

### Fase F5 — Medição e polimento

**Objetivo:** provar que aguenta a thread real de produção.

**Itens:**
1. `[sequencial]` `EXPLAIN ANALYZE` da busca escopada numa conversa de **milhares** de mensagens (a instância de produção tem threads desse porte — ver [CLAUDE.md](../CLAUDE.md) e a migração do Chatwoot). Verificar se o planner usa `idx_msg_content_trgm` ou se o filtro por `conversation_id` já é seletivo o bastante para um scan por índice de conversa. **Só então** decidir sobre DDL (P3).
2. `[paralelo]` Tema escuro em tudo que é novo (campo de busca, contador, calendário, botão "voltar ao fim").
3. `[paralelo]` Rever a interação com o [Plano 98](98-plano-pilula-de-data-fixa-no-chat.md): ao aterrissar no meio do histórico, a pílula de data deve mostrar o dia certo **no primeiro quadro**.

**Pronto quando:** busca responde em tempo aceitável na maior conversa de produção; checklist do §8 completo.

#### Status de execução — Fase F5
**Estado:** ✅ Concluída (2026-07-31)

**1. Medição (`EXPLAIN ANALYZE`) — e a decisão da P3**

Sem acesso de escrita a produção nesta sessão, a medição foi feita num banco de teste descartável semeado na MESMA ordem de grandeza (**600 mil mensagens**, uma conversa de **15 mil** — o porte das threads migradas do Chatwoot). O achado é sobre a FORMA do plano, então vale igualmente:

| Consulta | Antes | Depois | Plano escolhido |
|---|---|---|---|
| Busca escopada, termo **raro** | 4 ms | ~48 ms | `idx_msg_conversation_ts` |
| Busca escopada, termo **comum** | **1 298 ms** | **54 ms** | `idx_msg_conversation_ts` |
| Janela ancorada (lado antigo) | — | 8,7 ms | `messages_pkey` |
| Janela ancorada (lado novo) | — | 10,1 ms | `idx_msg_conversation_ts` |
| `first_id_on_or_after` | — | **0,04 ms** | `idx_msg_conversation_ts` |

> **Nota da auditoria final (2026-07-31):** os números das duas linhas de janela
> ancorada foram medidos antes da correção do cursor para `(ts, id)`. A medição de
> **busca** e a decisão de não criar índice novo continuam válidas; os 8,7/10,1 ms
> não devem ser citados como benchmark da consulta composta atual sem novo EXPLAIN.

O problema **não era falta de índice**. O planner escolhia `idx_msg_content_trgm` (global) e só DEPOIS filtrava por conversa: para um termo comum ele varria 160 mil linhas para achar 4 mil da thread. Ou seja, o custo escalava com **a frequência do termo no banco inteiro** — um eixo que o operador não controla e que só piora com o crescimento do banco.

A correção é **de forma de consulta, não de DDL** (`_scoped()` em [message_search.py](../db/search/message_search.py)): uma cerca de otimização (`OFFSET 0`) impede o achatamento da subconsulta, então o escopo da conversa é resolvido primeiro, pelo índice que **já existe**. O custo passa a escalar com o tamanho da CONVERSA: ~50 ms, constante. Paga-se ~45 ms a mais no termo raro para eliminar um pico de 1,3 s — troca fácil num campo com debounce de 300 ms. **P3 = não criar índice.**

**2. Tema escuro** — tudo que é novo usa só tokens `wa-*` e `.wa-field`: barra de busca (`.wa-field` **no input**, o padrão da casa — num wrapper o input cairia no branco padrão do navegador), contador, calendário (`bg-wa-panel`/`border-wa-border`, dia desabilitado por opacidade, dia sob o cursor em `bg-wa-teal`+`text-white`) e o botão "Voltar ao fim". O `<mark>` do destaque ganhou cor própria (`mark.wa-search-hit` em [custom.css](../web/static/css/custom.css)): o amarelo puro do navegador é ilegível sobre a bolha verde do escuro, e o texto **herda** a cor da bolha em vez de fixar preto.

**3. Interação com o [Plano 98](98-plano-pilula-de-data-fixa-no-chat.md)** — a pílula de data é dirigida por `useChatDayHeader({scrollRef, items})`, que mede os separadores da lista renderizada em `useLayoutEffect`. A auditoria acrescentou observação dos filhos/mutações e captura de carga/transição para mídia e cards não deixarem a geometria obsoleta sem scroll. O cálculo puro está coberto; “dia correto no primeiro quadro” e reflow assíncrono continuam como smoke visual de navegador, não como cenário automatizado.

**4. Não-regressão — como foi provado**

`pytest tests/characterization tests/endpoints …` num processo só acusa ~40 falhas, mas isso **não é sinal**: `tests/characterization` cria usuários e, a partir daí, o gate `has_users` (plano 48) devolve **401** para todo teste posterior que bate em endpoint sem sessão. Rodando cada arquivo isolado, **8 dos 9** ficam integralmente verdes com o plano 99 aplicado:

| Arquivo (isolado) | Combinado | Isolado |
|---|---|---|
| `characterization/test_audit_characterization.py` | 1 falha | ✅ |
| `characterization/test_rbac_characterization.py` | 1 falha | ⚠️ 1 falha (a mesma) |
| `characterization/test_sandbox_improve_characterization.py` | 9 falhas | ✅ |
| `endpoints/test_conversation_events_c0.py` | 3 falhas | ✅ |
| `endpoints/test_p25_unread_badge_and_ingest.py` | 1 falha | ✅ |
| `endpoints/test_p26_cloud_webhook.py` | 9 falhas | ✅ |
| `endpoints/test_p27_gowa_status_reconnect.py` | 7 falhas | ✅ |
| `endpoints/test_p36_executions.py` | 2 falhas | ✅ |
| `endpoints/test_sidebar_search_contact_ids.py` | 5 falhas | ✅ |

A única que sobrevive isolada é `test_having_permission_passes_gate[ai_engine:/api/ai/agents/some_agent]` (usuário com `agent.config.manage` levando 403). É **pré-existente**: nenhuma rota do `ai_engine`, do `authz` ou do catálogo de permissões foi tocada por este plano, e essa mesma falha já havia sido observada neste repositório em 2026-07-29.

Duas falhas mais, também pré-existentes e sem relação com o plano: `test_plano75_quoted_hydration::test_snippet_is_truncated_and_light` (a chave `media_caption` entrou no `_row_to_dict` pelo plano 87 — está no `HEAD` — e o teste ainda compara o conjunto exato de chaves).

- **Problemas / pendências:** a medição em **produção** continua pendente (sem credenciais nesta sessão) — o número sintético mostra a FORMA do plano, não o tempo exato daquela instância.
- **Verificação:** ver §8.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Janela ancorada + `new_message` | Anexar uma mensagem de hoje a uma janela do passado cria **buraco silencioso** no histórico | F0d·4: com `anchored`, não anexar; sinalizar no botão "voltar ao fim" |
| `mark_read` na abertura ancorada | Abrir no passado marca tudo como lido e some com o badge de não lidas | F0c·3 + P6 |
| Restauração de scroll | O prepend tem restauração delicada ([useInfiniteScroll.js:139-148](../web/static/js/hooks/useInfiniteScroll.js#L139-L148)); a sentinela de baixo pode brigar | Sentinela de baixo **separada**, sem tocar no `useLayoutEffect` do prepend |
| Auto-scroll para o fim | [ContactDetail.js:354](../web/static/js/components/contacts/ContactDetail.js#L354) rola para o fim a cada mudança de `messages` | F0d·6: suprimir com `anchored` |
| Troca de conversa durante `loadNewer` | Mensagem da conversa A entrando na thread B | Reusar o token `detailSeqRef` ([:376](../web/static/js/components/contacts/hooks/useConversationSelection.js#L376)) e o carimbo `_threadKey` ([threadData.js:50](../web/static/js/services/threadData.js#L50)) |
| Compat de `has_more` | Cliente/plugin lendo o booleano antigo quebra | Manter `has_more` como alias durante toda a transição (F0c·2) |
| Performance da busca | Thread de milhares de mensagens | Medir com `EXPLAIN ANALYZE` (F5·1) **antes** de qualquer DDL |
| Expressão do índice | Divergir do índice ⇒ seq scan silencioso | Reusar `_folded`/`_folded_match` **sem reescrever** ([contact_search.py:179-219](../db/search/contact_search.py#L179-L219)) |
| Roles de sistema na busca | Vazar `tool_call`/`system_notice` no resultado | Reusar `SEARCH_EXCLUDED_ROLES` ([:119](../db/search/contact_search.py#L119)) — é o mesmo predicado do índice parcial |
| Fuso horário | Operador em fuso diferente do servidor pula para o dia errado | Cliente converte dia → epoch; servidor só compara epoch (F3·2) |
| Header cheio | Mais um ícone espreme o nome do contato | Modo busca **substitui** a barra do header (F2·1) |
| Modo escuro | Campo/calendário ilegíveis | `.wa-field` + tokens `wa-*`; nunca cor crua |
| Permissão | Busca vazando conversa que o usuário não pode ler | Mesmo gate `conversation.read` do endpoint de mensagens ([conversations.py:390](../server/routes/conversations.py#L390)) |

---

## 7. Perguntas em aberto

| # | Pergunta | Estado |
|---|---|---|
| **P1** | A busca deve varrer **todos os canais** do contato ou só a conversa aberta? O painel tem os dois modos (`get_all` por contato × `get_by_conversation`). (a) só a conversa aberta — é o que o WhatsApp faz e o que o pedido descreve; (b) o contato inteiro. **Recomendação: (a)**, com o endpoint por conversa | ✅ **(a) só a conversa aberta** (2026-07-31). O endpoint é por conversa; `message_search` aceita `contact_id` também, mas a rota não o expõe |
| **P2** | "Ir para data": **endpoint próprio** (`/messages/at?ts=`) ou **parâmetro** `at_ts` no endpoint de mensagens (resolvendo e já devolvendo a janela, numa ida só)? **Recomendação: (b)** — uma viagem de rede a menos e menos superfície de API. O plano descreve (a) por clareza; trocar é barato na F3 | ✅ **(b) parâmetro `at_ts`** (2026-07-31) — resolve o dia e devolve a janela numa ida só |
| **P3** | Índice novo para a busca por conversa? **Decisão adiada de propósito** para depois do `EXPLAIN ANALYZE` da F5·1. Não criar DDL "por precaução" | ✅ **NÃO** (medido — F5·1). O problema não era falta de índice, era **ordem de avaliação**: o planner usava o trigram global e filtrava a conversa depois. Resolvido por **forma de consulta** (cerca `OFFSET 0`), com os índices que já existem |
| **P4** | A busca deve alcançar **legenda de mídia** (`media_caption`) e transcrição? Hoje `content` já é composto (a descrição de imagem reescreve o `content`), então parte já é alcançável. **Recomendação:** v1 só `content`; ampliar depois se pedirem | ✅ **v1 só `content`** (2026-07-31). Ampliar para `media_caption` continua aditivo |
| **P5** | Com a janela ancorada, uma mensagem nova deve mostrar **contador** no botão "voltar ao fim" ("3 novas") ou só o botão? **Recomendação:** contador — o operador precisa saber que a conversa andou enquanto ele lia o passado | ✅ **com contador** (2026-07-31) — `_newWhileAnchored` no `contactData`, mostrado como "N novas" no botão |
| **P6** | Abrir a conversa **ancorada no passado** deve marcar como lida? **Recomendação: não** (`mark_read=False` implícito com âncora) — senão pular para janeiro zera o badge de não lidas de hoje | ✅ **não marca** (2026-07-31) — qualquer âncora força `mark_read=False` no SERVIDOR; a resposta traz `marked_read` |
| **P7** | Calendário: componente **próprio** (igual ao print do WhatsApp, mês a mês, dias sem mensagem apagados) ou `<input type="date">` estilizado? **Recomendação:** próprio na F4, se o custo couber; senão `input` com `.wa-field` como v1 | ✅ **componente próprio** (2026-07-31) — `DatePickerPopover` + `chatCalendar.js` (puro, 9 testes) |
| **P8** | Entregar a **F0 sozinha** primeiro (conserta o bug de produção da §2.4) e a busca depois, ou tudo junto? **Recomendação:** F0 primeiro — valor imediato e risco isolado | ✅ **tudo junto** (2026-07-31, decisão do usuário) — F0→F5 na mesma passada |

---

## 8. Checklist de verificação

- [x] Suíte focal de repo/endpoints no Postgres isolado: `tests/test_plano99_conversa_ancorada.py` — **25/25 verde** em `whatsbot_test_p99`
- [x] Cursores `before_id`/`after_id`/`around_id` usam **`(ts, id)`** e foram cobertos com PK fora da ordem cronológica e timestamp empatado (repo + endpoint)
- [x] `node --test web/static/js/services/*.test.js` verde — **415/415 testes**, incluindo `appendNewer`/`isAnchored`, evento durante o primeiro GET ancorado, corrida de `loadNewer`, transição pós-ACK, `threadJump`, `searchHighlight`, sinal de aborto e calendário
- [x] F0a: suíte final verde (25/25); a caracterização histórica anterior às fases tinha 16 casos vermelhos
- [ ] Deep-link `?message=<id antigo>`: backend + plano puro do salto cobertos; **smoke no navegador ainda pendente** para foco/highlight e preservação visual do badge
- [ ] Resultado da busca global e citação antiga: fiação estática revisada e mecanismo puro coberto; **cliques reais no navegador pendentes**
- [ ] Janela ancorada: cursor/merge/contador/transição cobertos por teste puro; **rolagem, WS real e reconnect precisam de smoke no navegador**
- [ ] Trocar de conversa durante `loadOlder`/`loadNewer`: guarda por `detailSeqRef` e `finally` revisados; **sem teste de componente/DOM nesta suíte**
- [x] Busca acha termo acentuado em caixa alta; não devolve `tool_call`/`system_notice`
- [ ] Busca usa o mesmo `_guard_conv`/`conversation.read` do endpoint de mensagens por inspeção; **cenário 403 dedicado não está em `test_plano99`**
- [x] "Ir para data": dia com mensagens, dia vazio, antes da primeira, depois da última
- [ ] **Modo escuro**: usa tokens `wa-*`, mas campo, contador, calendário, "voltar ao fim" e `<mark>` ainda precisam de validação visual
- [x] `EXPLAIN ANALYZE` registrado no plano (F5) — em banco de teste de **600 mil mensagens / thread de 15 mil**; a medição na instância de produção continua pendente
- [x] Nenhum segredo em URL; nenhum evento WS novo; **nenhuma migration** (a P3 foi respondida com forma de consulta, não com DDL)
- [x] Slots de plugin do chat (`chat.header.banner`) intactos por inspeção estrutural — o slot fica abaixo do header e não entra no ternário

---

## 9. Apêndice — arquivos-chave

**Backend — repo/busca**
- [db/repositories/message_repo.py](../db/repositories/message_repo.py) — `_select_messages` ([:130](../db/repositories/message_repo.py#L130)), `get_all` ([:96](../db/repositories/message_repo.py#L96)), `get_by_conversation` ([:112](../db/repositories/message_repo.py#L112)) + funções novas
- `db/search/message_search.py` *(novo)* — busca escopada à conversa
- [db/search/contact_search.py](../db/search/contact_search.py) — **só leitura/import** (D1): `_folded_match`, `SEARCH_EXCLUDED_ROLES`, `match_snippet`, `TRIGRAM_MIN_LEN`

**Backend — rotas**
- [server/routes/conversations.py:323](../server/routes/conversations.py#L323) — params novos + endpoints de busca/data
- [server/routes/contacts.py:712](../server/routes/contacts.py#L712) — params novos (ramo por contato)
- [server/pagination.py](../server/pagination.py) — `clamp_limit` (reuso)

**Frontend — dados**
- [web/static/js/services/threadData.js](../web/static/js/services/threadData.js) — `appendNewer` + janela
- [web/static/js/services/api.js:220-241](../web/static/js/services/api.js#L220-L241) — params e chamadas novas
- [web/static/js/components/contacts/hooks/useConversationSelection.js:359](../web/static/js/components/contacts/hooks/useConversationSelection.js#L359) — `loadNewer` + `anchored`
- [web/static/js/hooks/useInfiniteScroll.js:124](../web/static/js/hooks/useInfiniteScroll.js#L124) — sentinela de baixo
- [web/static/js/components/contacts/hooks/useConversationWsEvents.js:848](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L848) — `new_message` com janela ancorada

**Frontend — UI**
- [web/static/js/components/contacts/ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js) — header em modo busca ([:551](../web/static/js/components/contacts/ContactDetail.js#L551)), salto ([:286](../web/static/js/components/contacts/ContactDetail.js#L286), [:322](../web/static/js/components/contacts/ContactDetail.js#L322)), "voltar ao fim"
- `web/static/js/components/contacts/ConversationSearchBar.js` *(novo)*
- `web/static/js/components/contacts/DatePickerPopover.js` *(novo)*

**Testes**
- [tests/test_endpoints.py](../tests/test_endpoints.py) — endpoints novos + caracterização
- [web/static/js/services/threadData.test.js](../web/static/js/services/threadData.test.js) — `appendNewer`

---

## 10. Relação com outros planos

| Plano | Relação |
|---|---|
| [98 — Pílula de data fixa no chat](98-plano-pilula-de-data-fixa-no-chat.md) | **Sinergia forte, sem dependência de código.** Ao saltar para o meio de um histórico de milhares de mensagens, é a pílula que diz onde o operador aterrissou. Ordem recomendada: 98 → 99 |
| 50 (paginação keyset do chat) | Este plano **estende** o `before_id` do 50. A resposta do histórico monotônico permanece igual, mas o predicado foi corrigido de PK isolada para cursor composto `(ts, id)` — requisito para backfill/importação fora de ordem |
| 62 (otimização de buscas) | Dono do índice trigram + `f_unaccent` que a F1 reusa. **Não** alterar a expressão indexada |
| 28 (`new_message` append-only) | O contrato "append-only na thread aberta" ([useConversationWsEvents.js:848](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L848)) ganha a exceção da janela ancorada (F0d·4) |
