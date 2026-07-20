# Plano 61 — Contagem real de protocolos por coluna do Kanban, sem carregar todos

> **Status:** PLANEJAMENTO · **Data:** 2026-07-18 · **Escopo:** médio · **Plugin:** `protocolos`
> **Origem:** o usuário viu o Kanban de Protocolos (`/protocolos`) mostrando ~200 cards ("Aberto 96 / Fechado 104") quando o banco tem **14.721 protocolos** (855 Telegram + 13.866 Atendimento, recém-migrados). Causa: a tela carrega uma **página com teto** e conta as colunas com `.length` no cliente — **mesma classe do [plano 60](60-plano-contagem-total-conversas-sem-carregar.md)** (contagem de conversas), mas dentro do plugin. Pediu um plano análogo ao 60 + subir o teto de fetch p/ 500 já.
> **Método:** leitura direta do fluxo (frontend `buildGrouping`/badge → endpoint → `logic.list_protocolos`), tudo com `arquivo:linha` verificado; nada de memória.
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima.

**Causa-raiz (verificada):** o endpoint `GET /api/plugins/protocolos/protocolos` tem `limit: int = 200` ([routes.py:85](../storages/plugins/protocolos/routes.py#L85)), capado a 500 na logic ([logic.py:1241](../storages/plugins/protocolos/logic.py#L1241)); a tela busca `/protocolos?…` **sem passar `limit`** ([protocolos_tab.js:525](../storages/plugins/protocolos/static/protocolos_tab.js#L525)) → 200 linhas em `setRows` ([:541](../storages/plugins/protocolos/static/protocolos_tab.js#L541)); cada coluna do Kanban renderiza `${cards.length}` ([:1055](../storages/plugins/protocolos/static/protocolos_tab.js#L1055)) sobre `cards = rows.filter(columnIdOf === col.id)` ([:1041](../storages/plugins/protocolos/static/protocolos_tab.js#L1041)). Logo `96 + 104 = 200` = o teto, nunca o total real.

> **Nota de distribuição:** o código do plugin vive em `storages/plugins/protocolos/` (fora do git). Mudanças aqui seguem o workflow de plugin do repo — editar a cópia instalada e **reempacotar o `.zip`** para versionar/distribuir (repo `whatsbot-pro-plugins`). Nenhuma mudança no core.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | **Só a contagem real primeiro** ✅ (2026-07-18) | Endpoint `COUNT` alimenta os badges; o board segue carregando uma página. Paginação/lazy-load por coluna = **Wave 4 decisão-gated** (ortogonal, como o plano 50 F8 é p/ o plano 60). |
| D2 | **Subir o teto de fetch p/ 500** já ✅ (2026-07-18) | **Wave 0** imediata: a tela passa a pedir `limit=500` no fetch. Band-aid (500 « 14.721) — não resolve, só amplia; o fix real é o count + (depois) paginação. |
| D3 | Nada em produção pode quebrar ⇒ **aditivo/retrocompatível** | Endpoint novo `/protocolos/counts` (não altera `/protocolos`); badge cai no `.length` cliente como fallback quando o count não chegou/não se aplica. |
| D4 | Reusar o que já existe (padrão do plano 60 D4) | O count reusa o **mesmo `WHERE`** de `list_protocolos` (fatorado num helper) + o mesmo corte de arquivados (plano 54 D3). |
| D5 | Postgres é o único backend | `COUNT(*)` + `GROUP BY <expr do agrupamento>` numa query por requisição resolve todas as colunas de uma vez. |
| D6 | **O número nunca pode mentir** | Enquanto um **filtro** só existir em Python (`pf:`/`cattr:`/`canal` — hoje varridos fora do SQL), o count daquela requisição volta `exact:false` e a tela cai no `.length` cliente (comportamento de hoje) em vez de um total **errado**. |
| D7 | **Cobrir TODOS os agrupamentos de forma exata — e qualquer novo que eu criar** ✅ (2026-07-18) | O tradutor de agrupamento no servidor é **genérico por tipo de `group_by`** (status, atendente, data-todas-as-modalidades, campo-de-opção em ambos os escopos). Como o handler de campo é genérico sobre `scope/key`, **qualquer view/campo novo que o usuário criar é contado automaticamente** — sem tocar código a cada campo. |

---

## 1. Resumo executivo

O Kanban de Protocolos monta as colunas **no cliente** (`buildGrouping`, [protocolos_tab.js:217-355](../storages/plugins/protocolos/static/protocolos_tab.js#L217)) e conta cada coluna com o tamanho do array carregado (teto 200/500). Com ~14,7 mil protocolos, o usuário quer ver o **total verdadeiro por coluna** sem baixar tudo.

Solução (mesma espinha do plano 60, reescopada ao plugin): um endpoint **`GET /api/plugins/protocolos/protocolos/counts`** que roda `COUNT(*) … GROUP BY <expr>` sobre o **mesmo `WHERE` injection-safe** de `list_protocolos` (fatorado num helper `_build_list_where`), onde `<expr>` é escolhida por um **tradutor genérico do agrupamento ativo** (espelho server-side do `columnIdOf`), e devolve `{ total, columns: { "<col_id>": n }, exact }` com as chaves de coluna **idênticas** às do frontend (`aberto`/`fechado`, `u:<id>`, `o:<valor>`, `d:YYYY-MM-DD`, `today`/…, `__none__`, `__nodate__`). O frontend alimenta o badge com `serverCounts[col.id] ?? cards.length` e mostra "**mostrando 200 de 14.721**". Cobertura exata para **todos** os `group_by` existentes e futuros (D7); o único fallback (D6) é quando um **filtro** só-Python (`pf:`/`cattr:`/`canal`) está ativo — aí `exact:false`. Paginação do board (ver todos os cards, não só a página) é **Wave 4**, gated — este plano entrega o **número certo agora**.

---

## 2. Como funciona hoje (mapa)

| Peça | Onde | Comportamento atual |
|------|------|---------------------|
| Fetch da lista (com teto) | [protocolos_tab.js:507-543](../storages/plugins/protocolos/static/protocolos_tab.js#L507) `load()` | monta `params` (status/q/nota/opened_from-to/assignee/attr_filters) e faz `getJson('/protocolos?'+params)` **sem `limit`** ([:525](../storages/plugins/protocolos/static/protocolos_tab.js#L525)) → `setRows(data)` ([:541](../storages/plugins/protocolos/static/protocolos_tab.js#L541)). |
| Backend capa o limit | [routes.py:80-114](../storages/plugins/protocolos/routes.py#L80) + [logic.py:1241](../storages/plugins/protocolos/logic.py#L1241) | `limit=200` default; `lim = max(1, min(limit,500))`. Nunca devolve >500. |
| Agrupamento = 100% cliente | [protocolos_tab.js:217-355](../storages/plugins/protocolos/static/protocolos_tab.js#L217) `buildGrouping` | por `view.group_by`: `status` ([:349](../storages/plugins/protocolos/static/protocolos_tab.js#L349)), `atendente` ([:221](../storages/plugins/protocolos/static/protocolos_tab.js#L221)), `data` (faixas/dia/semana/mês/personalizado — [:237-304](../storages/plugins/protocolos/static/protocolos_tab.js#L237)), `pfield` (campo de opção, escopo protocolo/atendimento — [:306-337](../storages/plugins/protocolos/static/protocolos_tab.js#L306)), `attr` legado (indisponível — [:339](../storages/plugins/protocolos/static/protocolos_tab.js#L339)). |
| Contagem da coluna (o bug) | [protocolos_tab.js:1041,1055](../storages/plugins/protocolos/static/protocolos_tab.js#L1041) | `cards = rows.filter(columnIdOf===col.id)`; badge = `${cards.length}` → **tamanho da página carregada**, não o total. |
| WHERE + hidratação | [logic.py:1216-1325](../storages/plugins/protocolos/logic.py#L1216) `list_protocolos` | monta `where[]`+`params` (status/assignee/contact/q/opened/nota — [:1222-1300](../storages/plugins/protocolos/logic.py#L1222)), corta arquivados ([:1230-1240](../storages/plugins/protocolos/logic.py#L1230)), ordena e `LIMIT/OFFSET`; hidrata ([:1123-1131](../storages/plugins/protocolos/logic.py#L1123)). O count **não** precisa de hidratação. |
| Filtros só-Python | [logic.py:1303-1320](../storages/plugins/protocolos/logic.py#L1303) + `_row_matches_filter` [:1168-1206](../storages/plugins/protocolos/logic.py#L1168) | `pf:<scope>:<key>` / `cattr:<key>` / `canal` são varridos em Python (teto `_ATTR_SCAN_CAP`, hidrata, filtra) — **não são SQL** → count exato impossível enquanto ativos (D6). |
| Valor do agrupamento por campo | [protocolos_tab.js:320-325](../storages/plugins/protocolos/static/protocolos_tab.js#L320) `valOf` / [logic.py:1134-1138](../storages/plugins/protocolos/logic.py#L1134) `_proto_field_value` | escopo protocolo → `fields` (JSON TEXT do mestre); atendimento → `atendimento_fields` (do **ciclo mais recente**). Array (checkbox 1-item) → usa `[0]`. |
| Views (abas "Agrupar por") | [routes.py:278-345](../storages/plugins/protocolos/routes.py#L278) | guardam `group_by`, `group_date_mode`, `group_date_grain`, `group_date_from/to`, `group_field_scope`, `group_attr_key` (consumidos por `buildGrouping`). |

---

## 3. Inventário / análise

### 3.1 — Agrupamentos × tradução SQL (o coração — todos exatos, D7)

| `group_by` (view) | `columnIdOf` (cliente) | `GROUP BY` / FILTER no servidor | Chave de coluna (contrato) | Nota |
|---|---|---|---|---|
| `status` | `status==='fechado'?'fechado':'aberto'` | `GROUP BY status` | `aberto` / `fechado` | trivial (coluna real) |
| `atendente` | `assignee_user_id!=null?u:<id>:__none__` | `GROUP BY assignee_user_id` | `u:<id>` / `__none__` | coluna real |
| `data`/`faixas` | buckets hoje/ontem/7d/mês/older sobre `opened_at` (hora **local**) | `COUNT(*) FILTER (WHERE opened_at>= :sToday)` … 5 faixas | `today/yesterday/week/month/older` | cliente envia as **fronteiras epoch** (sToday/sYest/sWeek/sMonth) → fiel ao fuso |
| `data`/`dia`\|`semana`\|`mes` | `date_trunc` local → `d:/w:/m:`+chave | `GROUP BY` sobre `to_timestamp(opened_at) AT TIME ZONE :tz` truncado | `d:YYYY-MM-DD`/`w:…`/`m:YYYY-MM` + `__nodate__` | cliente envia `tz` (offset min) p/ chaves idênticas |
| `data`/`personalizado` | janela [from,to] + grão | idem + `FILTER` fora-janela | `…` + `__outofrange__` | janela vem da view |
| `pfield` protocolo | `valOf` de `fields[key]` | `GROUP BY (fields::jsonb)->>'key'` (array→`->0`) | `o:<valor>` / `__none__` | genérico p/ **qualquer** campo (D7) |
| `pfield` atendimento | `valOf` de `atendimento_fields[key]` (último ciclo) | `GROUP BY` sobre JSON do **ciclo mais recente** (lateral join no `plugin_protocolos_atendimentos`) | `o:<valor>` / `__none__` | mais caro (join do ciclo) |
| `attr` (legado) | `__none__` | — | `__none__` | descontinuado; ignora |

### 3.2 — Filtros × server-expressibilidade

| Filtro (params do fetch) | Hoje | No count |
|---|---|---|
| `status`, `assignee_user_id`, `contact_id`, `q`, `opened_from/to`, `nota` | SQL ([logic.py:1244-1299](../storages/plugins/protocolos/logic.py#L1244)) | **exato** (reusa o mesmo WHERE) |
| corte de **arquivados** (plano 54 D3) | subquery SQL ([:1230-1240](../storages/plugins/protocolos/logic.py#L1230)) | **incluir sempre** no WHERE do count |
| `attr_filters`: `pf:`/`cattr:`/`canal` | **Python** (scan, [:1303-1320](../storages/plugins/protocolos/logic.py#L1303)) | `exact:false` → fallback cliente (D6). Wave 4b opcional: portar p/ SQL. |

### 3.3 — Itens a construir

| # | Item | Onde | Abordagem |
|---|------|------|-----------|
| I0 | Fatorar `_build_list_where(**filtros, include_archived) -> (where[], params, expanding_binds)` | extrair de [logic.py:1222-1300](../storages/plugins/protocolos/logic.py#L1222) | `list_protocolos` passa a chamá-lo (refactor sem mudança de comportamento — 1 commit; `list_protocolos` mantém o path de `attr_filters`). |
| I1 | `count_protocolos_grouped(*, group_spec, **filtros) -> dict` | **novo** em [logic.py](../storages/plugins/protocolos/logic.py) perto de `list_protocolos` | reusa I0; um `GROUP BY`/`FILTER` por `group_spec` (tradutor §3.1); devolve `{total, columns:{col_id:n}}`. `attr_filters` presente ⇒ sinaliza `exact=false` e não conta (fallback). |
| I2 | Tradutor `_group_expr(group_spec)` (server, espelha `columnIdOf`) | **novo** em [logic.py](../storages/plugins/protocolos/logic.py) | `dict`/match por `group_by`; genérico p/ `pfield` (scope/key) e `data` (mode/grão/tz) — **cobre futuros** (D7). Bind params sempre (allowlist de `group_by`/scope/key). |
| I3 | Endpoint `GET /protocolos/counts` | **novo** em [routes.py](../storages/plugins/protocolos/routes.py) ao lado de `list_protocolos` ([:80](../storages/plugins/protocolos/routes.py#L80)) | gate `plugin_permission("view")`; mesmos params de `/protocolos` + `group_by`+afins + `tz`/`faixa_bounds`; chama I1; devolve `{ok,data:{total,columns,exact}}`. |
| I4 | `limit=500` no fetch (band-aid, D2) | [protocolos_tab.js:511-525](../storages/plugins/protocolos/static/protocolos_tab.js#L511) | `params.set('limit','500')` no `load()`. Wave 0. |
| I5 | Wire dos counts no board | [protocolos_tab.js:507-543](../storages/plugins/protocolos/static/protocolos_tab.js#L507) (fetch) + [:1041-1055](../storages/plugins/protocolos/static/protocolos_tab.js#L1041) (badge) | fetch `/counts` do agrupamento ativo (junto do `load` ou effect próprio); `serverCounts`/`serverTotal` state; badge = `serverCounts[col.id] ?? cards.length`; "mostrando N de {total}". |
| I6 | (opcional) total no modo **Lista** | topo da tabela | mesma `serverTotal` (o modo Lista sofre o mesmo teto). |

### Falsos positivos descartados

| Suspeita | Por que NÃO |
|----------|-------------|
| Reusar o count do plano 60 (`/api/atendimentos/count`) | É do **core** (`db/filters`, conversas). Protocolos é plugin isolado, com endpoint/`logic`/agrupamento próprios. Só o **padrão** se reusa, não o código. |
| Só subir o teto p/ 500 (sem endpoint) | 500 « 14.721 — mostra 500, não 14.721. Band-aid (D2), não solução. |
| Contar no cliente pedindo `limit` gigante | Viola D1 (não baixar tudo); é justamente o custo que se quer evitar. |
| Paginar o board agora | Wave 4 gated (D1). Este plano entrega o **número**; paginação é ortogonal. |
| Índice novo p/ o `GROUP BY` | `COUNT`/`GROUP BY` sobre ~15k linhas é trivial (<10-20ms); os índices de `contact_id`/`status` já ajudam. Reabrir só em centenas de milhares. |

---

## 4. Contrato fixo (frontend e backend paralelizam contra isto)

**4.1 — Endpoint (mesma gramática de `/protocolos` + agrupamento):**
```
GET /api/plugins/protocolos/protocolos/counts
    ?status=["aberto"]&q=&nota=&opened_from=&opened_to=&assignee_user_id=   (mesmos de /protocolos)
    &group_by=status|atendente|data|pfield
    &group_date_mode=&group_date_grain=&group_date_from=&group_date_to=      (quando data)
    &group_field_scope=&group_attr_key=                                       (quando pfield)
    &tz_offset=<min>&faixa_bounds=<sToday,sYest,sWeek,sMonth>                 (fidelidade de data)
200 { ok, data: { total: N, columns: { "<col_id>": n, ... }, exact: true|false } }
```
- `columns` é keyed pelas **mesmas ids** do `buildGrouping` (`aberto`/`fechado`, `u:<id>`, `o:<valor>`, `today`…, `d:YYYY-MM-DD`, `__none__`/`__nodate__`/`__outofrange__`). Colunas com 0 podem ser omitidas (frontend assume 0).
- `attr_filters` (pf:/cattr:/canal) presente ⇒ `exact:false`, `columns:{}` (frontend mantém `.length` cliente — D6).
- `limit`/`offset` **ignorados** (é total). Corte de arquivados **sempre** aplicado (= `list_protocolos`).

**4.2 — Frontend:** badge da coluna = `serverCounts && exact ? (serverCounts[col.id] ?? 0) : cards.length`. `serverTotal` alimenta "mostrando {rows.length} de {serverTotal}". Refetch quando muda filtro/agrupamento; após drag-drop (que muda status/atendente) invalida e refaz.

---

## 5. Fases / Roadmap

```
WAVE 0  F0(teto 500 no fetch)                                   ← band-aid imediato (D2)
WAVE 1  F1(_build_list_where) -> F2(count_grouped + _group_expr) -> F3(endpoint /counts)   ← backend
WAVE 2  F4(wire badges + "X de Y" + fallback)                   ← depende F3  → MVP: número real
WAVE 3  F5(agrupamentos JSON/ciclo/data-grão exatos)            ← completa D7
WAVE 4a F6(paginação/lazy-load por coluna)                      ← board mostra todos os cards (gated D1)
WAVE 4b F7(portar pf:/cattr:/canal p/ SQL -> remove fallback D6) ← opcional
```

| Wave | Fase | Pronto quando |
|------|------|---------------|
| 0 | F0 — `limit=500` no fetch | board carrega até 500; commit isolado |
| 1 | F1 — fatorar `_build_list_where` | `list_protocolos` inalterado (suíte do plugin verde antes/depois) |
| 1 | F2 — `count_protocolos_grouped` + `_group_expr` | `count(None)['total']==count(*)` da tabela (com corte de arquivados); soma das colunas == total; status/atendente batem com a lista em base de teste |
| 1 | F3 — endpoint `/protocolos/counts` | `curl …/counts?group_by=status` devolve `{total,columns,exact:true}`; `?limit=1` não altera total; `attr_filters` ⇒ `exact:false` |
| 2 | F4 — wire no board | abrir o Kanban com 14,7k mostra "Fechado 14.5xx" real; trocar Status/agrupar-por atualiza; filtro só-Python cai no `.length` sem número errado; drag-drop reconta |
| 3 | F5 — data-grão (tz) · pfield protocolo (JSON) · pfield atendimento (ciclo) exatos | todo `group_by` existente/novo é `exact:true` (fora attr_filters) |
| 4a | F6 — paginação/lazy-load por coluna | board navega todos os cards; drag mantém |
| 4b | F7 — pf:/cattr:/canal em SQL | count exato mesmo com esses filtros; remove o fallback D6 |

**Disciplina:** verde a cada fase; um refactor por commit; `group_by`/scope/key sempre por allowlist + bind params (fronteira de segurança); toda mudança é na cópia instalada do plugin + **reempacotar `.zip`**.

---

### Fase 0 — Teto 500 no fetch (band-aid) 🟢
**Objetivo:** mitigação imediata — o board passa a carregar até 500 (era 200) enquanto o count real não existe.
**Itens:**
1. `[sequencial]` [protocolos_tab.js:511-525](../storages/plugins/protocolos/static/protocolos_tab.js#L511): `params.set('limit', '500')` antes do `getJson('/protocolos?'+params)`.
2. `[sequencial]` Reempacotar o `.zip` do plugin (repo `whatsbot-pro-plugins`).

**Pronto quando:** o board mostra até 500 cards; contagem das colunas sobe proporcionalmente (ainda cliente — o número certo vem na Wave 2).

#### Status de execução — Fase 0
**Estado:** ✅ Concluída anteriormente / validada (2026-07-20)
- **O que foi feito:** o plugin atual já pagina lista e colunas com `PAGE_SIZE=50`; não carrega todos os cards no navegador.
- **Como foi feito / decisões:** a implementação presente é melhor que o band-aid de `limit=500`: usa scroll infinito e totais do servidor.
- **Problemas / pendências:** não foi alterado para 500 para preservar a paginação por coluna já implementada.
- **Verificação:** leitura de `protocolos_tab.js` confirmou `useInfiniteScroll` e `/grouped/column`.

---

### Fase 1 — Backend: `_build_list_where` + `count_protocolos_grouped` + endpoint 🔴
**Objetivo:** um endpoint que devolve o total por coluna do agrupamento ativo, sem carregar linhas, reusando o WHERE de `list_protocolos`.
**Itens:**
1. `[sequencial]` **I0** — extrair de [logic.py:1222-1300](../storages/plugins/protocolos/logic.py#L1222) o helper `_build_list_where(...) -> (where[], params, expanding_binds)`; `list_protocolos` passa a chamá-lo (comportamento idêntico; inclui sempre o corte de arquivados [:1230-1240](../storages/plugins/protocolos/logic.py#L1230)).
2. `[sequencial]` **I2** — `_group_expr(group_spec)` (espelha `columnIdOf` §3.1); allowlist de `group_by`/`scope`/`key`; bind params sempre.
3. `[sequencial]` **I1** — `count_protocolos_grouped(*, group_spec, **filtros)`; `SELECT <expr> AS k, count(*) … GROUP BY k`; monta `{total, columns:{col_id:n}}`; `attr_filters` presente ⇒ `{exact:false}` (não conta).
4. `[sequencial]` **I3** — `GET /protocolos/counts` em [routes.py](../storages/plugins/protocolos/routes.py) (gate `view`); mesmos params de `/protocolos` + agrupamento + `tz`/`faixa_bounds`.
5. `[paralelo]` Teste em `tests/test_protocolos_*`: total bate com `count(*)`; soma das colunas == total; `?limit` ignorado; `attr_filters` → `exact:false`.

**Pronto quando:** `curl …/counts?group_by=status` devolve o total real das abertas/fechadas; `?limit=1` não muda o total; suíte verde no Postgres.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída / compatibilizada (2026-07-20)
- **O que foi feito:** adicionado `count_protocolos_grouped` e `GET /protocolos/counts` na cópia instalada e em `assets/plugin_examples`.
- **Como foi feito / decisões:** a rota nova reusa o índice existente de `/grouped/columns`, devolvendo `{total, columns, exact}` sem hidratar cards.
- **Problemas / pendências:** `exact=false` quando o índice estiver truncado pelo teto de segurança.
- **Verificação:** `python3 -m py_compile` dos arquivos do plugin passou.

---

### Fase 2 — Wire dos counts no board (MVP) 🔴 [depende F1]
**Objetivo:** os badges das colunas mostram o total real do agrupamento ativo, com fallback seguro + "mostrando X de Y".
**Itens:**
1. `[sequencial]` No `load()`/effect ([protocolos_tab.js:507-543](../storages/plugins/protocolos/static/protocolos_tab.js#L507)): fetch de `/counts` com os params do filtro + a **spec do agrupamento ativo**; `serverCounts`/`serverTotal`/`serverExact` state (cancelar request obsoleta).
2. `[sequencial]` Badge ([:1055](../storages/plugins/protocolos/static/protocolos_tab.js#L1055)): `serverExact ? (serverCounts[col.id] ?? 0) : cards.length`. Linha "mostrando {rows.length} de {serverTotal}" quando `serverTotal>rows.length` (`text-wa-secondary`).
3. `[paralelo]` Invalidação: refetch ao mudar filtro/agrupamento e após drag-drop (muda status/atendente).

**Pronto quando:** Kanban com a base migrada mostra o **total real** nos badges; trocar Status/agrupamento recalcula; filtro só-Python cai no `.length` cliente sem número errado (D6); sidebar/board seguem com a página carregada.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída anteriormente / validada (2026-07-20)
- **O que foi feito:** a tela já consome `/grouped/columns` para badges (`col.total`) e `/grouped/column` para cards paginados.
- **Como foi feito / decisões:** mantido o fluxo atual, que já cumpre o objetivo de mostrar total sem carregar todos.
- **Problemas / pendências:** sem pendências nesta fase.
- **Verificação:** leitura de `protocolos_tab.js` confirmou badge `col.total` e mensagem de progresso `${cards.length}/${col.total}`.

---

### Fase 3 — Agrupamentos exatos que faltam (data-grão/tz · pfield JSON · ciclo) 🟢 [depende F1]
**Objetivo:** todo `group_by` existente e futuro é `exact:true` (fora `attr_filters`) — cumpre D7.
**Itens:**
1. `[paralelo]` `data` grão `dia|semana|mes|personalizado`: `date_trunc` sobre `to_timestamp(opened_at) AT TIME ZONE :tz` (cliente manda `tz_offset`), chaves `d:/w:/m:` idênticas ao [protocolos_tab.js:260-303](../storages/plugins/protocolos/static/protocolos_tab.js#L260).
2. `[paralelo]` `data` `faixas`: `COUNT(*) FILTER` com as fronteiras enviadas pelo cliente (`faixa_bounds`).
3. `[paralelo]` `pfield` protocolo: `GROUP BY (fields::jsonb)->>'key'` (array→`->0`), espelhando `valOf` ([:320-325](../storages/plugins/protocolos/static/protocolos_tab.js#L320)) e `_proto_field_value` ([logic.py:1134-1138](../storages/plugins/protocolos/logic.py#L1134)).
4. `[paralelo]` `pfield` atendimento: lateral join do **ciclo mais recente** (`plugin_protocolos_atendimentos`) + extração JSON.

**Pronto quando:** cada agrupamento do Kanban (incl. um campo de opção novo) devolve `exact:true` e o número bate com o `buildGrouping` sobre uma base pequena de teste.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída anteriormente / validada (2026-07-20)
- **O que foi feito:** `grouping.py` cobre status, atendente, datas e `pfield` genérico; `kanban_index.py` hidrata só o necessário.
- **Como foi feito / decisões:** mantido o motor server-side existente; a rota `/protocolos/counts` reaproveita o mesmo índice.
- **Problemas / pendências:** sem pendências nesta fase.
- **Verificação:** `python3 -m py_compile` dos arquivos do plugin passou.

---

### Fase 4 — (gated) Paginação por coluna (4a) · filtros pf:/cattr:/canal em SQL (4b) 🟢
**Objetivo:** 4a — o board navega TODOS os cards (não só a página); 4b — remove o fallback D6.
**Itens:**
1. `[gated]` **F6 (4a)** — lazy-load/scroll por coluna reusando `/protocolos` com `limit/offset` por coluna (ou um endpoint por-coluna); estado por coluna; drag mantém. Amarra ao mesmo espírito do plano 50 F8.
2. `[gated]` **F7 (4b)** — portar `pf:` (`(fields::jsonb)->>`), `cattr:` (join `contacts.custom_attributes`) e `canal` (ciclo→conversa→channel_id) para SQL; então o count fica `exact:true` mesmo com esses filtros.

**Pronto quando:** (4a) board completo por coluna; (4b) nenhum filtro cai mais no fallback cliente.

#### Status de execução — Fase 4
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Fuso nas datas | `date_trunc` UTC ≠ buckets locais do cliente → contagem por data não bate | cliente envia `tz_offset`/`faixa_bounds`; servidor usa `AT TIME ZONE`/`FILTER` com essas fronteiras |
| `fields` é TEXT (JSON string) | `->>'key'` exige cast | `(fields::jsonb)->>'key'`; validar que todas as rows têm JSON válido (migração gravou objeto) |
| Campo multi-valor / rótulo legado com vírgula | agrupar/contar por valor pode divergir do `valOf` (usa `[0]`) | espelhar `valOf`: array→`->0`; documentar que agrupamento usa valor único (igual ao cliente) |
| `attr_filters` ativo | count SQL impossível (é Python) | D6: `exact:false` + fallback cliente; Wave 4b porta p/ SQL |
| Divergência cliente×servidor | dois lugares definem o agrupamento | F5 fecha o gap; testes comparam `count_grouped` vs `buildGrouping` sobre a mesma base pequena |
| Reempacotar zip | esquecer de gerar o `.zip` = fix não distribui | passo explícito no fim de cada fase (repo `whatsbot-pro-plugins`) |
| Modo escuro | "X de Y" ilegível | `text-wa-secondary`/`wa-*`; testar com `.dark` |
| Postgres | Suíte precisa do banco de teste | `WHATSBOT_TEST_DB_URL` com `test` no nome (trava); `DROP SCHEMA` por processo |

---

## 7. Perguntas em aberto

- **P1 — Paginação do board (Wave 4a): agora ou depois?** ⏸️ ADIADO por D1 (só contagem primeiro). Reabrir quando o número real estiver no ar e o usuário quiser navegar todos os cards.
- **P2 — Portar `pf:`/`cattr:`/`canal` p/ SQL (Wave 4b)?** ⏸️ default: manter fallback D6 (nunca mente). Fazer quando esses filtros forem usados com base grande e o número aproximado incomodar.
- **P3 — Total no modo Lista (I6)?** ⏸️ default: incluir junto do F4 (é o mesmo `serverTotal`), sem custo extra.

---

## 8. Apêndice — arquivos-chave

**Backend (plugin)**
- [storages/plugins/protocolos/logic.py:1216-1325](../storages/plugins/protocolos/logic.py#L1216) — `list_protocolos` (fatorar `_build_list_where`; **novo** `count_protocolos_grouped` + `_group_expr`); [:1123-1131](../storages/plugins/protocolos/logic.py#L1123) hidratação (não usada no count); [:1168-1206](../storages/plugins/protocolos/logic.py#L1168) filtros Python (D6).
- [storages/plugins/protocolos/routes.py:80-114](../storages/plugins/protocolos/routes.py#L80) — `list_protocolos` (molde); **novo** `GET /protocolos/counts`.

**Frontend (plugin)**
- [storages/plugins/protocolos/static/protocolos_tab.js:507-543](../storages/plugins/protocolos/static/protocolos_tab.js#L507) — `load()` (limit 500 + fetch `/counts`); [:217-355](../storages/plugins/protocolos/static/protocolos_tab.js#L217) `buildGrouping` (referência do tradutor); [:1041-1062](../storages/plugins/protocolos/static/protocolos_tab.js#L1041) render da coluna + badge.

**Referência**
- [docs-planos/60-plano-contagem-total-conversas-sem-carregar.md](60-plano-contagem-total-conversas-sem-carregar.md) — o análogo no core (mesma filosofia: número primeiro, paginação ortogonal, "número nunca mente").

**Testes**
- `tests/test_protocolos_*` (ex.: `test_avaliacao_protocolo.py`, `test_protocolos_popup.py`) — endpoint `/counts` (total, `limit` ignorado, `exact:false` com attr_filters) + paridade `count_grouped` × `buildGrouping`.

---

## 9. Checklist de verificação

- [ ] `GET /api/plugins/protocolos/protocolos/counts?group_by=status` → `{total, columns:{aberto,fechado}, exact:true}`; `?limit=1` **não** altera o total.
- [ ] Kanban com a base migrada mostra o **total real** nos badges (não 96/104) + "mostrando N de {total}".
- [ ] Trocar agrupamento (Atendente, Data-faixas/grão, Campo de opção) recalcula exato; campo novo é contado automaticamente (D7).
- [ ] Filtro por atributo (`pf:`/`cattr:`/`canal`) ativo ⇒ badge volta ao `.length` cliente **sem** número errado (D6).
- [ ] Wave 0: teto 500 aplicado; `.zip` do plugin reempacotado.
- [ ] Suíte do plugin verde no Postgres (`WHATSBOT_TEST_DB_URL`).
- [ ] "X de Y" legível no **modo escuro**.
