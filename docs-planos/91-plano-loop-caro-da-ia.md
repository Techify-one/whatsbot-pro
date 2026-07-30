# Plano 91 — A IA para de entrar em loop caro: timeout do embedding, limites de tool e o ruído do atributo

> **Status:** PLANEJAMENTO · **Data:** 2026-07-29 · **Escopo:** médio (1 arquivo do core com bug latente + 3 arquivos do plugin `vendas_ia` + 1 campo novo na UI + testes + zip)
> **Origem:** investigação de produção nesta sessão (instância **Redes Brasil**, conversas 15147, 15144 e 7706). Um único "Bom dia. Comprei o script de load balance…" custou **167.292 tokens e US$ 0,32**, com `pesquisar_ofertas` chamada 7 vezes — 4 delas mortas por timeout — terminando em transferência para humano.
> **Método:** leitura do código com `arquivo:linha` conferido, `EXPLAIN ANALYZE` no Nexus de produção, consultas ao Postgres de produção do WhatsBot, medição local do bootstrap do subprocesso, e 3 rastreamentos em paralelo com verificação adversarial (6 agentes) que **refutou 4 conclusões intermediárias** — as sobreviventes estão marcadas abaixo.
> Este plano corrige a CAUSA (a chamada de embedding estoura o teto de 10 s do subprocesso), instala as duas redes de segurança que hoje estão desligadas, e limpa dois bugs latentes do core que a própria correção ativaria.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 1. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| **D1** ✅ 2026-07-29 | **Os limites de chamada de tool precisam de campo na interface**, para o usuário aumentar sem mexer em banco. | F4 ganha um bloco novo em [GeneralSettings.js](../web/static/js/components/ai/GeneralSettings.js). O backend **já serve** as chaves (`exposed=True, writable=True`) — não muda nada lá. |
| **D2** ✅ 2026-07-29 | **Botão para escolher entre "só léxica" e "léxica + semântica"**, para quando o catálogo crescer. | F2 vira um seletor de **modo de busca**, não um liga/desliga booleano. |
| **D3** ✅ 2026-07-29 | **NÃO encolher o prompt do BIA Comercial.** O usuário precisa do comportamento que está lá. | O custo do prompt é atacado por **menos iterações** (F1/F2/F4) e por **prompt caching** (F7 — investigação), nunca por corte de conteúdo. |
| **D4** ✅ 2026-07-29 | **Não remover a instrução `codigo_oferta` do prompt isoladamente** — o plugin escuta essa chamada para fixar a oferta. | F6 muda prompt **e** plugin juntos, ou não muda nenhum dos dois. Ver P2. |
| **P1** (princípio) | **Correção da causa antes de rede de segurança.** Limite de tool sem consertar o timeout só troca "loop caro" por "resposta pobre". | F1/F2 vêm ANTES de F4 nas waves. |
| **P2** (princípio) | **Fail-open preservado.** Nenhuma mudança pode fazer a IA calar ou o turno abortar por erro de busca. | Todo caminho novo mantém o `try/except` largo que já existe em `search.py` e `hooks.py`. |

---

## 2. Resumo executivo

A IA não está "confusa" — ela está **presa num mecanismo**. `pesquisar_ofertas` roda num subprocesso com teto **duro de 10 s** ([tool_isolation.py:54](../agent/tool_isolation.py#L54)), e dentro dele faz uma chamada HTTP ao OpenRouter para gerar o embedding da query. O cliente OpenAI é criado **sem `timeout`** ([embeddings.py:38](../storages/plugins/vendas_ia/embeddings.py#L38)), então nada desiste antes: a tool é **morta**, o modelo recebe "excedeu o tempo limite", reformula e tenta de novo. Cada tentativa custa ~15k tokens de contexto reenviado.

Três agravantes tornam isso pior do que precisaria ser:

1. **O corpus é minúsculo** — 4 ofertas ativas (2.188 tokens), 17 cursos ativos (2.423), 35 FAQ (3.540). Busca semântica para escolher entre 4 itens é canhão para mosquito.
2. **O ranking semântico já é descartado hoje.** As três queries híbridas terminam em `ORDER BY COALESCE(<id/nome>), score DESC LIMIT :lim` ([search.py:118](../storages/plugins/vendas_ia/search.py#L118), [:212](../storages/plugins/vendas_ia/search.py#L212), [:289](../storages/plugins/vendas_ia/search.py#L289)) — a chave primária de ordenação é o **id/nome**, então o `LIMIT` corta por uuid, não por relevância. Paga-se 10 s de embedding por um score que é jogado fora.
3. **As duas redes de segurança estão desligadas** — `ai_tool_call_limit_per_tool = 0` e teto global 25 **por hop**.

A solução tem quatro peças pequenas e uma investigação: dar timeout ao cliente de embedding, tornar o modo de busca configurável (com o padrão certo para o tamanho de hoje), consertar a ordenação, ligar os limites com campo na UI, e verificar prompt caching com a Techify.

⚠️ **Um bug latente do core seria ATIVADO por este plano:** [agent_run_service.py:397](../app/services/agent_run_service.py#L397) detecta `save_contact_info` sem filtrar `skipped` — com o limite por-tool ligado, uma chamada **bloqueada** faria o turno reportar dados de contato como se tivessem sido salvos. F5 conserta isso **antes** de F4 valer.

---

## 3. Como funciona hoje (mapa)

### 3.1 A anatomia dos 10 segundos

| etapa | tempo | evidência |
|---|---|---|
| consulta pgvector no Nexus | **0,528 ms** | `EXPLAIN ANALYZE` no banco de produção |
| bootstrap do subprocesso (spawn + engine + imports) | **~1,4 s** | 3 medições locais consistentes |
| **HTTP ao OpenRouter (`qwen/qwen3-embedding-8b`)** | **o restante** | por eliminação — ver 3.2 |
| **teto de parede** | **10 s** | [tool_isolation.py:54](../agent/tool_isolation.py#L54) `DEFAULT_TIMEOUT = _env_float("WHATSBOT_AI_TOOL_TIMEOUT", 10.0)` |

⚠️ **O banco está descartado por três ordens de grandeza.** E o tempo da IA "pensando" NÃO conta nesse teto — ele cobre só o subprocesso da tool.

### 3.2 A prova de que é o embedding

Execução 3737 (conv 15147), passos reais em `execution_steps`:

| hora | modo | desfecho |
|---|---|---|
| 08:06:42 | `descricao_desejada` (híbrida, **com** embedding) | ⛔ timeout |
| 08:06:49 | `descricao_desejada` (híbrida) | ✅ ok |
| 08:07:05 | `descricao_desejada` (híbrida) | ⛔ timeout |
| 08:07:12 | `offer_name` (**ILIKE puro, sem** embedding) | ✅ ok |
| 08:07:27 | `descricao_desejada` (híbrida) | ⛔ timeout |
| 08:07:42 | `descricao_desejada` (híbrida) | ⛔ timeout |
| 08:07:51 | `course_name` (**ILIKE puro, sem** embedding) | ✅ ok |

**Os 4 timeouts são todos do modo com embedding; os 2 modos que o pulam passaram os dois.** Alcance medido em 7 dias: **6 timeouts em 15 chamadas — 40 %**.

### 3.3 O custo, e o que realmente o causa

Distribuição medida em produção (`executions`, tokens ≠ 0):

| chamadas de tool no turno | 0 | 1 | 2 | 4 | 5 | 10 |
|---|---|---|---|---|---|---|
| **média de tokens** | 3.848 | 17.619 | 37.949 | 63.535 | 73.670 | 167.292 |

**~15k tokens por chamada de tool**, aproximadamente linear — e essa constante é o **system prompt reenviado a cada iteração** (o BIA Comercial tem 35.074 chars ≈ 8,8k tokens). A prova cruzada: o turno com **12** chamadas custou só 61k tokens, porque era o **roteador** (prompt de 15.256 chars ≈ 3,8k).

Em 7 dias: 51 turnos com IA, 991.859 tokens, **US$ 1,86**. Os **10 turnos gigantes (>30k) consomem US$ 1,29 — 69 % do custo**.

### 3.4 O que acontece hoje ao estourar um limite (nenhum está ligado)

| | limite **por-tool** (WhatsBot) | teto **global** (AGNO) |
|---|---|---|
| config | `ai_tool_call_limit_per_tool` = **0 (desligado)** | `ai_tool_call_limit_total` = **25** |
| onde roda | [agno_engine.py:244](../agent/agno_engine.py#L244), antes do dispatch | `Agent(tool_call_limit=…)`, [agno_engine.py:425](../agent/agno_engine.py#L425) |
| o que o LLM recebe | string **PT-BR** como resultado **bem-sucedido** ([hooks.py:120](../ai_engine/hooks.py#L120)) | mensagem em **inglês** com `tool_call_error=True` |
| aborta o turno? | **não** | **não** ("overflow gracioso") |
| escala para humano? | **não** — só *sugere* `transferir_agente` ([hooks.py:87](../ai_engine/hooks.py#L87)) | **não** |
| escopo do contador | por **run do AGNO = por hop de roteamento** ([agno_engine.py:561](../agent/agno_engine.py#L561)) | idem |

⚠️ **Três gotchas que mudam o desenho:**
- **Chamada bloqueada não gasta a própria cota** — `_ran_count` ignora `skipped` ([hooks.py:67](../ai_engine/hooks.py#L67)). O modelo pode reinvocar para sempre recebendo a mesma mensagem; quem freia é o teto **global**, porque cada bloqueio produz um `function_call_result`.
- **Ao estourar o global, `transferir_agente` e `transfer_to_human` também são recusados** — teto global baixo demais tranca a rota de escape.
- A mensagem diz *"reseta na próxima mensagem do cliente"*, mas o contador **reseta a cada hop** — o texto está impreciso.

### 3.5 O ruído do `codigo_oferta`

O prompt do comercial, no caractere ~12.238 (seção "8.2. Registro de offercode"), manda:

```
set_custom_attribute(key="codigo_oferta", value="<código exato>", scope="conversation")
```

Mas `codigo_oferta` **não existe** em `custom_attribute_definitions` (13 linhas em produção, conferidas: só `oferta_atual`, `perfil_cliente`, `obs`, `resultado`, `motivo`, `teste`, `atendimento_ia`, `teste_atendimento` + 5 de contato). É resíduo do **plano 41**.

⚠️ **O erro é cosmético — o mecanismo funciona assim mesmo.** `tool.after` é emitido com os `args` originais mesmo quando a tool devolve erro ([agno_engine.py:255](../agent/agno_engine.py#L255)), e `on_tool_after` do plugin lê `args['key'] == 'codigo_oferta'` → valida no Nexus → **fixa a oferta** ([events.py:59](../storages/plugins/vendas_ia/events.py#L59)). **Apagar a instrução do prompt quebraria a fixação de oferta.** Daí D4.

Custo do ruído: 1 chamada desperdiçada (~15k tokens) + uma mensagem de erro no contexto que ensina o modelo a tentar de novo (na conv 15144 gerou uma 2ª chamada com `oferta_atual`).

### 3.6 O que já existe a favor

| peça pronta | onde | reuso |
|---|---|---|
| Gargalo único de embedding | [search.py:47](../storages/plugins/vendas_ia/search.py#L47) `_query_vector` | **1 call site funcional** para o toggle — as 3 tools passam por ele |
| Degradação silenciosa já implementada | [search.py:47-53](../storages/plugins/vendas_ia/search.py#L47) | Sem chave ⇒ `None` ⇒ fallback. O toggle reusa o MESMO caminho |
| Chaves de limite já expostas na API | [config/settings.py:208,214](../config/settings.py#L208) `exposed=True, writable=True` | F4 é **só frontend** — `GET/PUT /api/config` já as serve genericamente |
| Aba de IA com PUT parcial | [GeneralSettings.js](../web/static/js/components/ai/GeneralSettings.js) (354 linhas) — `getConfig()`/`saveConfig()` | Bloco novo entra sem tocar no resto |
| Filtro de `skipped` feito certo | [messaging_service.py:563](../app/services/messaging_service.py#L563) | Molde para o conserto de F5 |

---

## 4. Inventário / análise

| # | Item | Onde | O que falta | Risco | Esforço |
|---|---|---|---|---|---|
| **I1** | Timeout no cliente de embeddings | [embeddings.py:38](../storages/plugins/vendas_ia/embeddings.py#L38) | `timeout=` no construtor `OpenAI(...)` | baixo | S |
| **I2** | Modo de busca configurável (D2) | [search.py:47](../storages/plugins/vendas_ia/search.py#L47) + `settings.py` + `_config.py` | enum de 3 modos lido no gargalo único | baixo | M |
| **I3** | Ordenação descarta o score | [search.py:118](../storages/plugins/vendas_ia/search.py#L118), [:212](../storages/plugins/vendas_ia/search.py#L212), [:289](../storages/plugins/vendas_ia/search.py#L289) | `ORDER BY score DESC` de verdade | médio | S |
| **I4** | Limites de tool desligados | config `ai_tool_call_limit_per_tool` / `_total` | definir valores | médio | S |
| **I5** | Campo na UI para os limites (D1) | [GeneralSettings.js](../web/static/js/components/ai/GeneralSettings.js) | bloco novo; **backend não muda** | baixo | M |
| **I6** | `save_contact_info` bloqueado reporta sucesso | [agent_run_service.py:397](../app/services/agent_run_service.py#L397) | `and not tc.get("skipped")` | **alto** | S |
| **I7** | Ruído do `codigo_oferta` | prompt do comercial + [state.py:31](../storages/plugins/vendas_ia/state.py#L31) | decidir P2 e mover os dois lados juntos | médio | M |
| **I8** | Lista de atributos some sem log | [memory.py:763-811](../agent/memory.py#L763) | 4 caminhos de saída vazia, nenhum loga | baixo | S |
| **I9** | Prompt caching (D3) | proxy Techify | confirmar se repassa `cached_tokens` | baixo | M |
| **I10** | Testes | `tests/` + `storages/plugins/vendas_ia/tests/` | `search.py`/`embeddings.py` **sem nenhum teste** | médio | M |
| **I11** | Distribuição | zip + repo Pro | bump + `Atualizar (.zip)` | baixo | S |

### Falsos positivos descartados

| # | Parecia problema | Por que não é |
|---|---|---|
| **1** | "O blob gigante de JSON da tool estoura o contexto" | O payload real das 4 ofertas é **~2,2k tokens**. O que o painel mostra já vem cortado em 4.000 chars (`TOOL_RESULT_MAX_CHARS`, [agno_engine.py:62](../agent/agno_engine.py#L62)). O driver do custo é o **prompt reenviado**, não o resultado. |
| **2** | "O Nexus está lento / falta índice" | `EXPLAIN ANALYZE`: **0,528 ms**. Nem o plano nem o volume são problema. |
| **3** | "Estourar o limite manda para humano / aborta o turno" | Nenhum dos dois. Ver §3.4 — é suave nos dois casos, e nada lê o campo `blocked`. |
| **4** | "Basta apagar a chave OpenRouter para desligar o embedding" | Funciona (cai no fallback), mas é **destrutivo** (perde o segredo), pinta badge vermelho de "defeito" no diagnóstico, e derruba junto a CTE de **full-text português**, que não usa chave nenhuma. |
| **5** | "Batch no `set_custom_attribute` dá para fazer só editando o builtin pela tela de Tools" | **Não.** O core tem consumidor acoplado pelo NOME da tool lendo `args['scope']` como escalar ([messaging_service.py:560-576](../app/services/messaging_service.py#L560)); um schema com array degradaria o refresh do painel **em silêncio**. |
| **6** | "Existe a chave órfã `plugin.vendas_ia.search_enabled` no banco" | Alegado por um agente verificador; **conferi em produção e não existe**. As únicas chaves de busca são `embedding_model`, `embedding_dims`, `hybrid_limit` e os dois pesos. |
| **7** | "As tools instaladas divergem do `tool_code/` do plugin" | Conferido: as 3 rows em `ai_tools` são **byte-idênticas** ao `tool_code/` e delegam a `vendas_ia.search`. |
| **8** | "`run_sync` também precisa do mesmo conserto" | `agno_engine.run_sync` / `_make_sync_entrypoint` são **código morto** — sem caller fora de teste; o handler só expõe `aprocess_message`. |

---

## 5. Mudanças de infraestrutura (por camada)

### 5.1 Plugin `vendas_ia` (o grosso)

- **`embeddings.py`** — `timeout` explícito no cliente OpenAI. Constante nomeada, não literal solto.
- **`search.py`** — leitura do modo de busca no gargalo `_query_vector`; correção do `ORDER BY` nas três híbridas.
- **`settings.py` / `_config.py`** — o enum do modo + default. ⚠️ `settings.py` é importado **uma vez no load do plugin**: o CAMPO novo no formulário só aparece após restart; a LEITURA da flag vale na hora (as settings são lidas do banco a cada chamada).

### 5.2 Core (mínimo, cirúrgico)

- **`app/services/agent_run_service.py:397`** — filtrar `skipped` na detecção de `save_contact_info` (I6). **Um bug de correção, não de feature.**
- **`agent/memory.py:763-811`** — um `logger.debug` em cada um dos 4 caminhos de lista vazia (I8). Diagnóstico, sem mudança de comportamento.
- **`web/static/js/components/ai/GeneralSettings.js`** — bloco novo com os limites (D1).

⚠️ **Nenhuma migration.** Nenhuma tabela nova. Nenhum endpoint novo.

### 5.3 O que NÃO muda

O prompt do BIA Comercial (D3), o schema do `set_custom_attribute` (F.P.#5), e o `messaging_service.py` (já filtra `skipped` corretamente).

---

## 6. Waves e paralelização

```
WAVE 0   F0 (caracterização: travar o comportamento atual)          🔴 barreira
             │  [sem isto, não dá pra provar que nada regrediu]
             ├──────────────────┬──────────────────┐
             ▼                  ▼                  ▼
WAVE 1   F1 (timeout do     F2+F3 (modo de     F5 (bug do          🟢
          embedding)         busca + ORDER BY)   save_contact_info)
          embeddings.py      search.py           core
             │                  │                  │
             └────────┬─────────┘                  │
                      ▼                            ▼
WAVE 2            F4 (limites + campo na UI)  🔴  [dep: F1,F2,F5]
                      │
             ├────────┴────────┐
             ▼                 ▼
WAVE 3   F6 (codigo_oferta)  F7 (prompt caching  🟢  [F6 dep: P2]
          🔴 [dep: P2]        — investigação)
                      ▼
WAVE 4   F8 (testes)  🔴  [dep: F1..F6]
                      ▼
WAVE 5   F9 (zip + deploy)  🔴  [dep: F8]
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | caracterização | 🔴 | baixo | testes atuais verdes + golden do comportamento de hoje |
| 1 | **F1** | plugin/embeddings | 🟢 | baixo | busca lenta cai no léxico em N s, não morre em 10 s |
| 1 | **F2+F3** | plugin/search | 🟢 | médio | modo configurável + `LIMIT` corta por score |
| 1 | **F5** | core | 🟢 | **alto** | `save_contact_info` bloqueado não reporta info salva |
| 2 | **F4** | config + UI | 🔴 | médio | campo salva e o limite pega no turno seguinte |
| 3 | **F6** | prompt + plugin | 🔴 | médio | erro some SEM perder a fixação de oferta |
| 3 | **F7** | investigação | 🟢 | baixo | resposta da Techify sobre `cached_tokens` |
| 4 | **F8** | testes | 🔴 | médio | suíte verde no Postgres |
| 5 | **F9** | distribuição | 🔴 | médio | zip atualizado em produção via **Atualizar**, não Importar |

**Paralelizável de verdade:** F1 + F2/F3 + F5 na Wave 1 (arquivos disjuntos: `embeddings.py` × `search.py` × core). F2 e F3 tocam o MESMO arquivo — despache como uma fase só, nunca como duas em paralelo.

---

## 7. Fases

### F0 — Caracterização: travar o que existe hoje 🔴

**Objetivo:** poder provar depois que nada regrediu — `search.py` e `embeddings.py` **não têm nenhum teste** hoje.

**Itens** `[sequencial]`:
1. Rodar a suíte atual e registrar o baseline: `venv/bin/python tests/test_endpoints.py` e `venv/bin/python -m pytest storages/plugins/vendas_ia/tests/ -q`.
2. ⚠️ **Usar um banco de teste isolado.** Nesta sessão, um `pytest` concorrente contra o mesmo `WHATSBOT_TEST_DB_URL` fez a suíte falhar de formas diferentes a cada execução (cada processo faz `DROP SCHEMA` no bootstrap). Com banco próprio: **1626 checagens, 0 falhas**.
3. Escrever teste de caracterização do `_ofertas_hybrid` **atual** (com o `ORDER BY` errado) para que F3 mostre a mudança de resultado de forma explícita, não acidental.

**Pronto quando:** baseline registrado (nº de checagens e falhas) num banco isolado, e o teste de caracterização passa contra o código atual.

#### Status de execução — Fase 0
**Estado:** ✅ Concluída (2026-07-30)
- **O que foi feito:**
  - Criado o banco de teste **exclusivo** `whatsbot_test_91` (`ENCODING 'UTF8' TEMPLATE template0`, host 10.8.200.13) — nenhuma outra IA/sessão o usa.
  - Baseline registrado: `tests/test_endpoints.py` → **1633 checagens, 2 falhas**; `pytest storages/plugins/vendas_ia/tests/ -q` → **74 passed, 0 falhas**.
  - Criado `storages/plugins/vendas_ia/tests/test_search.py` (6 testes) — **primeiro teste que `search.py` tem**.
- **Como foi feito / decisões:**
  - A caracterização do `ORDER BY` é feita **capturando o SQL emitido** (`nexus_db.run_read` monkeypatchado por um `_Recorder`), não executando contra o Nexus: é determinístico, DB-free e mostra a mudança de F3 como diff de teste explícito. Os 3 testes afirmam a cláusula de HOJE (`COALESCE(<id/nome>), score DESC`) e trazem um comentário mandando **atualizar, não apagar** em F3.
  - Fixture `_no_db` (autouse) troca `_config.setting` pelos `DEFAULTS` — sem ela `_weights()` bate no engine do core, que não existe em teste DB-free.
  - Já entraram os 3 testes de fallback (sem chave / embedding levanta / `_ofertas_hybrid` cai no ILIKE) que F1 e F8 vão reusar.
- **Problemas / pendências:**
  - As **2 falhas** do baseline (`skip_attrs round-trip na protocol-config`, `sanitize aceita scope protocolo`) são **pré-existentes e do plugin `protocolos`** — fora do escopo deste plano (e há outra IA no plano 93 nesse plugin). Não foram tocadas; servem de linha de base: F8 tem de terminar com as MESMAS 2, nem mais nem menos.
- **Verificação:** `venv/bin/python -m pytest storages/plugins/vendas_ia/tests/test_search.py -q` → 6 passed contra o código **atual** (sem nenhuma mudança de produção ainda).

---

### F1 — Timeout no cliente de embeddings 🟢

**Objetivo:** a busca lenta **degradar**, em vez de morrer.

**Itens** `[paralelo com F2/F3 e F5]`:
1. [embeddings.py:38](../storages/plugins/vendas_ia/embeddings.py#L38) — `OpenAI(api_key=key, base_url=_OPENROUTER_BASE_URL)` ganha `timeout=<constante>`. Valor sugerido: **3 s** (as chamadas boas levam 7–9 s no total, das quais ~1,4 s é bootstrap — um teto de 3 s no HTTP deixa folga confortável dentro dos 10 s).
2. O `except Exception` de [search.py:52](../storages/plugins/vendas_ia/search.py#L52) **já** captura o timeout e devolve `None` → fallback. **Nenhuma outra mudança é necessária.**
3. Conferir se o `logger.warning` existente já diz o suficiente para diagnosticar (hoje: `"embedding falhou (%s), fallback ILIKE: %s"`).

⚠️ Hoje **nada dentro da tool desiste antes** dos 10 s — o SDK da OpenAI usa um default de vários minutos. Por isso a tool é morta em vez de cair no fallback.

**Pronto quando:** com o OpenRouter artificialmente lento (ou o timeout baixado a 0,1 s para o teste), `pesquisar_ofertas` retorna resultado do fallback léxico **dentro do orçamento**, e o log mostra a linha de fallback — nunca `[tool … excedeu o tempo limite]`.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-07-30)
- **O que foi feito:**
  - `embeddings.py` — `OpenAI(..., timeout=http_timeout())`, com a constante nomeada `_DEFAULT_TIMEOUT = 3.0` (valor sugerido pelo plano) e override por env `VENDAS_IA_EMBEDDING_TIMEOUT` para diagnóstico. Docstring do módulo explica **por que** o timeout é obrigatório (teto duro de 10 s do subprocesso).
  - O cache do cliente passou a considerar o timeout além da chave (`_client_timeout`) — sem isso, mudar a env não teria efeito enquanto o processo vivesse.
  - `search.py:52` — o `logger.warning` agora nomeia a exceção **e o teto vigente** (`embedding falhou (ofertas: APITimeoutError, teto 3.0s), fallback léxico`), para não confundir "OpenRouter lento" com "teto apertado demais".
- **Como foi feito / decisões:**
  - **Env em vez de setting do plugin** para o teto: é botão de diagnóstico, não de operação; setting nova exige restart para aparecer no formulário (R7) e poluiria a tela com um número que ninguém deve mexer no dia a dia.
  - Entrada inválida (`0`, negativo, texto) cai no default — nunca em "sem teto", que é justamente o bug.
  - Nenhuma outra mudança: o `except Exception` de `search.py` já capturava e caía no fallback, como o plano previa (F1·2).
- **Problemas / pendências:** nenhuma.
- **Verificação:** 4 testes novos, incluindo um **ponta a ponta real** (`test_openrouter_lento_degrada_para_o_lexico_dentro_do_orcamento`): sobe um servidor HTTP local que nunca responde, aponta o base URL do OpenRouter para ele e afirma que `_ofertas_hybrid` volta com resultado do **léxico em 3,6 s** (teto de 0,5 s × retries do SDK) em vez de pendurar. O log da execução mostra `APITimeoutError, teto 0.5s` — exatamente a linha de fallback que o plano pede, e nenhum `[tool … excedeu o tempo limite]`. `pytest storages/plugins/vendas_ia/tests/test_search.py -q` → 10 passed.

---

### F2 + F3 — Modo de busca configurável + ordenação por score 🟢

**Objetivo:** dar ao usuário o botão de D2 e parar de jogar fora o ranking.

⚠️ **Uma fase só** — F2 e F3 editam o mesmo arquivo (`search.py`). Não paralelizar entre si.

**Itens** `[sequencial dentro da fase]`:

**F2 — o seletor (D2):**
1. `settings.py` + `_config.DEFAULTS` — campo novo, enum de **três** valores:
   - `catalogo` — devolve o conjunto ativo inteiro, sem busca (adequado ao tamanho de hoje);
   - `lexica` — full-text português + ILIKE, **sem** embedding;
   - `hibrida` — o de hoje (léxica + semântica).
2. Ler no gargalo único [search.py:47](../storages/plugins/vendas_ia/search.py#L47) `_query_vector` — modo ≠ `hibrida` ⇒ devolve `None` **antes** de tocar a rede, reusando o caminho de fallback que já existe.
3. ⚠️ **Modo `lexica` não pode cair no `_ofertas_fallback` atual.** O fallback de hoje descarta a CTE de **full-text inteira** (`plainto_tsquery('portuguese', …)`, que não precisa de chave) e roda um ILIKE ingênuo de frase inteira, sem ranking. Para `lexica` valer a pena, a variante deve **manter a CTE full-text e dropar só a `semantic`** — sobe de 1 para 3 call sites, mas é o que entrega o que o nome promete.
4. Refletir o modo no diagnóstico ([routes.py](../storages/plugins/vendas_ia/routes.py) `/status` + `static/config.js`): hoje um embedding desligado aparece como badge VERMELHO "chave ausente", que lê como defeito, não como escolha.

**F3 — a ordenação:**
5. Corrigir as três queries: [search.py:118](../storages/plugins/vendas_ia/search.py#L118), [:212](../storages/plugins/vendas_ia/search.py#L212), [:289](../storages/plugins/vendas_ia/search.py#L289). Hoje: `ORDER BY COALESCE(s.id, ft.id), score DESC` — a chave primária é o id, então o `LIMIT` corta por uuid. Deve ordenar por `score DESC` (com o id só como desempate estável).
6. Conferir que `hybrid_limit` (5) volta a significar "os 5 melhores", não "5 quaisquer".

**Pronto quando:** o modo `catalogo` devolve as 4 ofertas ativas sem nenhuma chamada de rede; `hibrida` devolve as mesmas ofertas **em ordem de score** (teste de caracterização de F0 muda de resultado, deliberadamente); trocar o modo vale na leitura seguinte sem restart.

#### Status de execução — Fases 2+3
**Estado:** ✅ Concluída (2026-07-30)

**Decisão do usuário (P3, 2026-07-30): padrão = `lexica`.** O usuário pediu explicitamente que o embedding saísse do caminho por padrão, com botão para religar — que é o modo `hibrida` no mesmo seletor.

- **O que foi feito:**
  - **F2 · seletor** — `settings.py` ganhou `search_mode: Literal["lexica","catalogo","hibrida"]` (default `lexica`; o schema sai com `enum`, então o `PluginSettingsForm` renderiza um `<select>`) e `_config.DEFAULTS` o espelha. `search.search_mode()` lê a cada busca (trocar vale na seguinte, sem restart) e **valor inválido cai no default com WARNING** — fail-open (P2).
  - **F2 · gargalo único** — `_query_vector` recusa gerar embedding quando o modo ≠ `hibrida`, **antes** de olhar a chave e antes de qualquer rede, reusando o caminho de degradação que já existia.
  - **F2 · modo `catalogo`** — `_ofertas_catalogo()` / `_cursos_catalogo()` e o caminho já existente da FAQ devolvem o conjunto ativo inteiro. Os recortes **estruturais** (`offercode` → cursos daquela oferta; `id_oferta` → FAQ daquela oferta) continuam valendo em todos os modos: são relacionais, não busca textual.
  - **F3 · ordenação** — as três buscas passaram a `SELECT * FROM ranked ORDER BY score DESC, <id|nome> LIMIT :lim`, com o `DISTINCT ON` (que obriga o `ORDER BY` a começar pelo id) isolado dentro da CTE `ranked`. A dedup continua; o `LIMIT` passa a cortar pelos melhores. Na FAQ o `id` desempata mas **não é projetado** — o payload da tool não muda de forma.
- **Como foi feito / decisões:**
  - **A híbrida e a léxica compartilham o corpo da query.** Só a CTE de 1ª posição muda (semântica × ILIKE), montada por `_ofertas_sql()/_cursos_sql()/_faq_sql()`. É o que garante, por construção, que a léxica **mantenha a CTE full-text** em vez de degradar para o ILIKE ingênuo (R6) — e mantém uma cópia só do corpo, em vez de seis.
  - ⚠️ **Achado ao testar contra o catálogo real — a léxica "óbvia" não funcionaria.** Com o CTE ILIKE de frase inteira que o plano descrevia, `'script de load balance'` devolvia **0 linhas**: `plainto_tsquery` exige TODAS as palavras e a oferta se chama "SCRIPTS DE FAILOVER E LOADBALANCE" (uma palavra), e o ILIKE da frase inteira também não casa. Ou seja, o padrão escolhido seria pior que o de hoje justamente na consulta que motivou o plano. Corrigido com `tokens_for()`: o ILIKE casa por **palavra** (descartando conectivos de 1–2 letras) e o `sem_score` da léxica é a **fração de palavras casadas** — ranking de verdade, não um zero constante. Medido depois: `'script de load balance'` → SCRIPTS DE FAILOVER E LOADBALANCE em 1º (0,70).
  - **Teto do modo `catalogo`** (`_CATALOGO_MAX_ROWS = 100`, com WARNING ao truncar): "mandar tudo" é adequado a 4 ofertas, não a um catálogo que cresceu. Truncar em silêncio seria pior que truncar avisando.
  - O fallback de qualquer modo sem vetor passa a ser a **variante léxica ranqueada**, não o ILIKE puro — que sobrou só para o caso de a própria SQL quebrar. Isso melhora também a degradação da F1.
  - **F2·4 · diagnóstico honesto** — `/status` devolve `search_mode` + `embedding_used`, e a tela mostra uma linha "Modo de busca"; a linha da chave OpenRouter só é **vermelha** quando o modo é `hibrida` — nos outros dois vira um badge **neutro** "não usada neste modo". Antes, um embedding desligado por escolha aparecia como defeito.
- **Problemas / pendências:**
  - `unnest(:tokens)` exigiu `CAST(... AS text[])` explícito (`function unnest(unknown) is not unique`) — pego só no teste contra o Postgres real, não pelos testes de forma de SQL.
  - Recomendação registrada para o pós-deploy: experimentar `catalogo` numa conversa controlada. Com 4 ofertas ele é imbatível em custo e nunca "não acha"; `lexica` é a escolha conservadora enquanto isso.
- **Verificação:**
  - **Contra o Nexus de produção (somente leitura)**: `EXPLAIN` nas **6** variantes de SQL (3 domínios × semântica/léxica) — todas aceitas pelo planner. Depois, execução real das léxicas: `'script de load balance'` → SCRIPTS DE FAILOVER E LOADBALANCE (0,70) > Zabbix+Grafana (0,23); `'quanto custa o combo de roteamento'` → Combo de Roteamento (0,35) > COMBO DE MONITORAMENTO (0,17); FAQ `'tem certificado?'` → "Tem certificado?" (0,72) em 1º. **Ordenação por score confirmada em dados reais.**
  - Testes: os 3 de caracterização de F0 foram **atualizados** (não apagados) para a cláusula nova, com o comentário explicando a transição; mais 11 testes novos (modo padrão, modo inválido, léxica não toca a rede, léxica mantém o full-text, catálogo nos 3 domínios, filtro estrutural preservado, tokenização). Suíte do plugin: **94 passed**.

---

### F5 — Bug latente: `save_contact_info` bloqueado reporta sucesso 🟢

**Objetivo:** não deixar F4 ativar um bug que hoje dorme.

**Itens** `[paralelo com F1 e F2/F3]`:
1. [agent_run_service.py:397](../app/services/agent_run_service.py#L397) — `if any(tc.get("tool") == "save_contact_info" for tc in executed_tools)` não filtra `skipped`. Com o limite por-tool ligado, uma chamada **bloqueada** faria `updated_info` ser populado como se tivesse salvo, e isso vai para o payload de `llm.after` (plugins veem).
2. Molde do conserto certo já está no repo: [messaging_service.py:563](../app/services/messaging_service.py#L563) filtra `and not tc.get("skipped")`.
3. `[opcional, I8]` [memory.py:763-811](../agent/memory.py#L763) — os **4** caminhos de lista de atributos vazia (sem definições / sem conversa aberta naquele inbox / lista vazia / `try/except`) não emitem log nenhum. Um `logger.debug` em cada um torna diagnosticável o caso "a IA não sabia os atributos".

**Pronto quando:** teste que simula uma chamada bloqueada de `save_contact_info` e verifica que `updated_info` fica `None`; suíte do core verde.

#### Status de execução — Fase 5
**Estado:** ✅ Concluída (2026-07-30)
- **O que foi feito:**
  - `app/services/agent_run_service.py:397` — a detecção de `save_contact_info` passou a filtrar `and not tc.get("skipped")`, com comentário explicando de onde vem o marcador (os 2 pontos de bloqueio de `agno_engine`) e por que importa (o payload de `llm.after` que os plugins leem).
  - **I8 (opcional, feito)** — `agent/memory.py` `_custom_attr_lines`: `logger.debug` nos **4** caminhos de lista vazia, cada um dizendo QUAL deles foi (sem definições × contato sem id × sem conversa aberta *naquele inbox* × nada renderizado × exceção com `exc_info`). Nenhuma mudança de comportamento.
  - Teste novo `tests/test_plano91_skipped_save_contact_info.py` (3 casos).
- **Como foi feito / decisões:**
  - Os 3 casos cobrem os três desfechos que importam: bloqueada ⇒ `contact_info is None`; executada ⇒ continua reportando (regressão do caminho feliz); **bloqueada + executada no mesmo turno** ⇒ reporta — o modelo pode reinvocar depois de um bloqueio, e se uma rodou houve save de verdade.
  - O contato do teste nasce com `name` preenchido de propósito: com o bug, `contact_info` volta **populado** (não vazio), então o falso-positivo é inequívoco.
- **Problemas / pendências:** nenhuma.
- **Verificação:** 3 passed. **Prova de que o teste pega o bug**: revertendo o filtro no fonte, `test_save_contact_info_bloqueado_nao_reporta_info_salva` **falha**; com o filtro, passa (fix restaurado e reconferido na linha 402).

---

### F4 — Limites de tool + campo na interface (D1) 🔴

**Objetivo:** ligar as redes de segurança, com o usuário podendo afrouxá-las sem abrir o banco.

⚠️ **Depende de F1/F2 (causa corrigida) e F5 (bug do `skipped`).** Ligar limite antes disso troca "loop caro" por "resposta pobre".

**Itens** `[sequencial]`:
1. Valores sugeridos (ver P1): `ai_tool_call_limit_per_tool = 2`, `ai_tool_call_limit_total = 12`.
   - **2 por tool** — nos dados, quando a busca funciona 1 chamada resolve e a 2ª cobre um refinamento legítimo; da 3ª em diante nenhuma trouxe informação nova.
   - **12 global** (não 25) — cada chamada custa ~15k tokens; 25 autoriza ~375k num turno. ⚠️ **Não baixar muito**: ao estourar o global, `transferir_agente` e `transfer_to_human` também são recusados, trancando a rota de escape.
2. **Campo na UI** — bloco novo em [GeneralSettings.js](../web/static/js/components/ai/GeneralSettings.js) (354 linhas, usa `getConfig()`/`saveConfig()` com PUT parcial). **O backend não muda**: as chaves já são `exposed=True, writable=True` em [config/settings.py:208,214](../config/settings.py#L208) e o `GET/PUT /api/config` as serve genericamente via `exposed_config_keys()`/`writable_config_keys()`.
3. Incluir `ai_max_route_depth` (5) no mesmo bloco — é o terceiro guardrail da mesma família, já exposto ([config/settings.py:211](../config/settings.py#L211)).
4. Texto de ajuda honesto em cada campo: que o limite é **por hop de roteamento**, não por mensagem (a mensagem que o LLM recebe diz "mensagem", mas o contador reseta a cada hop), e que 0 desliga.
5. Modo escuro: classes `wa-*` e `.wa-field`.

**Pronto quando:** salvar 2/12 na tela e, no turno seguinte, uma 3ª chamada de `pesquisar_ofertas` receber a mensagem de bloqueio em vez de rodar; subir para 5 pela tela volta a permitir; nada de restart necessário.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-07-30)

**Decisão do usuário (P1, 2026-07-30): `per_tool = 5`, `total = 15` "por enquanto"** — mais folgado que os 2/12 sugeridos, o que é coerente com o campo existir para apertar depois.

- **O que foi feito:**
  - Bloco novo **"Limites de chamadas de ferramenta"** em [GeneralSettings.js](../web/static/js/components/ai/GeneralSettings.js) com os três guardrails da mesma família: **Por ferramenta** (`ai_tool_call_limit_per_tool`), **Total** (`ai_tool_call_limit_total`) e **Trocas de agente** (`ai_max_route_depth`). Carrega no `populate()` e vai no PUT parcial do `handleSave()`.
  - **Backend: zero mudança**, como o plano previa — as três chaves já eram `exposed=True, writable=True` (conferido em `config/settings.py:208,211,214`) e o `GET/PUT /api/config` as serve genericamente.
- **Como foi feito / decisões:**
  - `intOrDefault()` em vez do idiomático `parseInt(x) || default`: com `|| `, digitar **0** (que é justamente "desligado") ressuscitaria o default e o usuário não conseguiria DESLIGAR o freio pela tela. Travado por teste.
  - **Texto de ajuda honesto (F4·4)**, em linguagem de operador: o rótulo diz que o limite conta **"por etapa do atendimento — cada vez que a conversa passa para outro agente, a contagem recomeça"**, que é a verdade (o contador é por run do AGNO = por hop), e que **0 desliga**. O campo *Total* carrega o aviso do **R2** em destaque: ao estourar, a IA perde junto as ferramentas de transferir, ficando **sem rota de saída** — por isso não baixar demais.
  - Modo escuro (R11): só classes `wa-*` e `.wa-field` nos inputs.
  - Os **valores** não foram cravados como default do core de propósito: o escopo autorizado desta execução são 3 arquivos do core, e `config/settings.py` não é um deles. Aplicar 5/15 é o passo **F9·6**, pela tela.
- **Problemas / pendências:**
  - ⏳ **Aplicar 5 / 15 pela tela após o deploy** (F9·6). Hoje a instalação segue em `per_tool = 0` (desligado) e `total = 25`.
  - A **mensagem que o LLM recebe** ao ser bloqueado ainda diz "o limite é por mensagem e reseta na próxima mensagem do cliente", o que é impreciso (§3.4). Corrigir exigiria editar `ai_engine/hooks.py`, **fora do escopo de arquivos** desta execução — anotado para um plano futuro. A imprecisão é só para o modelo; o texto que o **operador** lê já está correto.
- **Verificação:** `tests/test_plano91_limites_pela_ui.py` — 5 casos, todos verdes: o GET expõe as 3 chaves; o PUT com 5/15 persiste e **vale sem restart** (`_resolve_tool_call_limit() == 15` logo após o PUT, e a 6ª chamada da mesma tool é bloqueada enquanto a 5ª ainda passa); `0` desliga de verdade nos dois níveis; a mensagem de bloqueio **cita `transferir_agente`** (R2); chamada `skipped` **não** consome cota (o gotcha do §3.4 que justifica ligar os dois limites juntos). `node --check` no componente.

---

### F6 — Ruído do `codigo_oferta` 🔴

**Objetivo:** matar o erro vermelho **sem** perder a fixação de oferta (D4).

⚠️ **Bloqueada até P2 ser decidida.** As duas saídas são mutuamente exclusivas.

**Itens** `[sequencial, depois de P2]`:
1. **Caminho (a) — recriar a definição:** cadastrar `codigo_oferta` como atributo de conversa. Não toca em código nem no prompt; o `on_tool_after` continua funcionando igual. Contra: fica redundante com `oferta_atual` (guardam o mesmo offercode) e reintroduz o que o plano 41 tirou de propósito.
2. **Caminho (b) — migrar os dois lados juntos:** trocar a instrução do prompt para `oferta_atual` **e** ajustar `state.CODE_ATTR_KEY` ([state.py:31](../storages/plugins/vendas_ia/state.py#L31)) + `_offercode_from_payload` ([events.py:42](../storages/plugins/vendas_ia/events.py#L42)) para casar a chave nova. Mais limpo; exige release do plugin.
3. Em qualquer caminho: confirmar por uma conversa real que a oferta continua sendo fixada (`plugin_vendas_ia_conversa.offercode` preenchido + atributo `oferta_atual` no painel).

**Pronto quando:** uma conversa em que o comercial identifica a oferta grava `oferta_atual` **sem** card de erro no fio, e `plugin_vendas_ia_conversa` recebe o offercode.

#### Status de execução — Fase 6
**Estado:** ⏸️ **ADIADA por decisão do usuário** (P2, 2026-07-30) — não executada.
- **O que foi feito:** nada, deliberadamente. Perguntado entre (a) recriar a definição `codigo_oferta`, (b) migrar prompt + plugin juntos e (c) adiar, o usuário escolheu **adiar**.
- **Como foi feito / decisões:** nem o prompt do BIA Comercial nem `state.CODE_ATTR_KEY`/`events._offercode_from_payload` foram tocados — D4 exige mover os dois lados juntos ou nenhum, e adiar é a única opção que respeita isso sem trabalho pela metade.
- **Problemas / pendências:**
  - O ruído **continua**: a instrução manda `set_custom_attribute(key="codigo_oferta", …)`, a definição não existe, a tool devolve erro e o card vermelho aparece no fio — custo de ~1 chamada desperdiçada (≈15k tokens) nos turnos em que o comercial identifica a oferta.
  - ⚠️ **A fixação de oferta segue funcionando** e depende justamente dessa chamada com erro (`tool.after` é emitido com os `args` originais mesmo quando a tool falha). Quem for retomar esta fase: **não apague só a instrução do prompt** — isso quebraria a fixação. Ver D4 e §3.5.
  - Com a F4, a chamada com erro passa a consumir cota do limite por-tool. Como `set_custom_attribute` é chamada 1–2× por turno e o valor acordado é 5, não há conflito prático hoje.
- **Verificação:** n/a (nada mudou).

---

### F7 — Prompt caching (investigação, D3) 🟢

**Objetivo:** baratear os 8,8k tokens de prompt **sem** cortar comportamento.

**Itens** `[paralelo com F6]`:
1. Perguntar à Techify se o proxy repassa `cached_tokens` / `prompt_tokens_details` do provedor upstream (`openai/gpt-5.2`).
2. ⚠️ A tabela `usage` do WhatsBot **não tem** coluna de tokens cacheados (`id, contact_id, call_type, model, prompt_tokens, completion_tokens, total_tokens, cost_usd, ts`) — hoje é impossível medir cache hit pelos dados locais. Se a Techify repassar, avaliar uma coluna nova.
3. Se houver caching: verificar que a parte estável do prompt vem **primeiro** — cache funciona por prefixo, e qualquer trecho dinâmico no topo invalida tudo. ⚠️ [prompt_builder.py](../agent/prompt_builder.py) **layeriza** seções (ex.: a lista de atributos) sobre o `base_prompt` do banco; conferir a ordem resultante.

**Pronto quando:** resposta documentada da Techify + decisão registrada (usar / não usar / adiar).

#### Status de execução — Fase 7
**Estado:** ✅ Concluída (2026-07-30) — **investigação respondida por medição, não por pergunta.**

> 🟢 **Resultado: o proxy da Techify repassa `cached_tokens` E o prompt caching JÁ ESTÁ FUNCIONANDO hoje**, sem nenhuma mudança de código. Nada a implementar.

- **O que foi feito:**
  - Em vez de esperar resposta da Techify (F7·1), **medi**: duas chamadas idênticas ao proxy com um prefixo estável de ~2k tokens, inspecionando o `usage` cru devolvido.
  - Conferido o schema de `usage` (F7·2) e a ordem de montagem do prompt (F7·3).
- **Como foi feito / decisões:**
  - Medição com a chave da instalação, `max_tokens=5`, prompt sintético (nenhum dado de cliente saiu). Custo: frações de centavo.
  - **`openai/gpt-5.2`** (o de produção): 1ª chamada `cached_tokens: 0`; 2ª chamada **`cached_tokens: 1792`** de 1932 (**93 %**).
  - **`deepseek/deepseek-v4-pro`** (o configurado neste checkout): 2ª chamada **`cached_tokens: 2048`** de 2287 (**90 %**).
  - O campo vem em `prompt_tokens_details` (junto de `cache_write_tokens`), exatamente no formato OpenAI — o proxy é transparente.
  - **F7·3 · a ordem já é a certa**: `prompt_builder.build_system_prompt` começa pelo `base_prompt` do agente (os 35k chars estáveis do BIA Comercial) e **só depois** acrescenta o dinâmico — contexto de grupo, info do contato, tags, fragmentos de plugin e, por último, a seção "Data e hora atual" (a que muda a cada minuto). Como o cache é por PREFIXO, o trecho caro é justamente o que fica cacheável. Nada a reordenar.
- **Problemas / pendências:**
  - **F7·2 confirmado**: `usage` (`id, contact_id, call_type, model, prompt_tokens, completion_tokens, total_tokens, cost_usd, ts`) **não tem** coluna de tokens cacheados, então o cache hit é invisível nos relatórios locais. Acrescentá-la exigiria migration + `agent/llm.py`, ambos **fora do escopo de arquivos** desta execução. Recomendação para um plano futuro, agora com a justificativa medida.
  - ⚠️ **Pergunta que sobra para a Techify — a COBRANÇA.** O `cost_usd` do WhatsBot é calculado como `prompt_tokens × preço` ([agent/llm.py:120](../agent/llm.py#L120)), **sem** desconto de cache. Se a Techify cobra o token cacheado mais barato (como o provedor upstream), o custo real é **menor** que o registrado no painel e os US$ 1,86/7 dias do §3.3 são teto, não conta fechada. Se cobra cheio, o benefício do cache é só de latência. Isso é política comercial — só a Techify responde.
- **Verificação:** medição reproduzível acima (duas execuções seguidas, `cached_tokens` 0 → 1792/2048). Ordem do prompt conferida em `agent/prompt_builder.py:59` (base primeiro) e na seção de data/hora (última).

---

### F8 — Testes 🔴

**Objetivo:** travar o que hoje **nenhum teste cobre**.

**Itens**:
1. ⚠️ `search.py` e `embeddings.py` **não têm nenhum teste**. E a string de erro `"Atributos disponíveis"` também não é coberta por teste nenhum.
2. Testes puros (sem DB): seleção de modo em `_query_vector`; ordenação por score; fallback quando o embedding levanta.
3. Teste do core: `save_contact_info` bloqueado ⇒ `updated_info is None` (F5).
4. Regressão: com modo `hibrida` e embedding disponível, o comportamento é o de antes **exceto** pela ordenação (que muda de propósito — o teste de caracterização de F0 é atualizado, não apagado).
5. Fail-open: OpenRouter fora, Nexus fora, modo inválido ⇒ turno normal.
6. ⚠️ Rodar num **banco de teste isolado** (§F0·2).

**Pronto quando:** testes do plugin verdes + `tests/test_endpoints.py` verde no Postgres (`WHATSBOT_TEST_DB_URL`).

#### Status de execução — Fase 8
**Estado:** ✅ Concluída (2026-07-30)
- **O que foi feito:** consolidação e execução de tudo no banco isolado `whatsbot_test_91`.
  - `storages/plugins/vendas_ia/tests/test_search.py` — **21 testes novos** onde antes não havia nenhum: ordenação por score nos 3 domínios (F3), modo padrão / modo inválido / léxica sem rede / léxica com full-text preservado / catálogo nos 3 domínios / filtro estrutural por `offercode` (F2), tokenização (acento, pontuação, query curta, teto), timeout do cliente + recriação ao mudar o teto + **degradação ponta a ponta com servidor HTTP lento** (F1), e degradação da SQL ranqueada quebrada para o ILIKE puro.
  - `tests/test_plano91_skipped_save_contact_info.py` (3) e `tests/test_plano91_limites_pela_ui.py` (5) no core.
- **Como foi feito / decisões:**
  - **Fail-open (F8·5) coberto nos três eixos**: OpenRouter fora (o teste com servidor lento + o de exceção no gerador), modo inválido (cai no default com WARNING) e SQL quebrada (cai no ILIKE puro sem levantar).
  - Os testes de F1 que dependem do caminho semântico foram forçados a `modo("hibrida")`: com o novo padrão `lexica` eles passariam **por engano** (o gate de modo devolveria `None` antes de o embedding sequer ser tentado).
  - `tests/test_hooks.py`, `test_routing_engine.py` e `test_agent_routing.py` são scripts standalone (`sys.exit` no fim quebra a coleta do pytest) — rodados direto pelo interpretador, como manda a prática do repo.
- **Problemas / pendências:**
  - ⚠️ **Interferência de suíte pré-existente, NÃO causada por este plano**: rodar `pytest storages/plugins/vendas_ia/tests/ tests/test_melhorias_plugin.py` na MESMA invocação faz 5 testes do `melhorias` falharem com `ModuleNotFoundError` (o `conftest.py` do plugin insere `storages/plugins` no `sys.path`). Reproduzido usando **apenas testes antigos** do plugin (`test_triage_filter.py`), sem nenhum arquivo deste plano. Separadamente ambos passam. Anotado para um plano de higiene de testes.
- **Verificação (banco isolado `whatsbot_test_91`, nenhum outro `pytest` no mesmo banco):**
  - `tests/test_endpoints.py` → **1633 passed, 2 failed** — **idêntico ao baseline de F0**, e as 2 falhas são as mesmas pré-existentes do `protocolos` (fora do escopo). **Zero regressão.**
  - `pytest storages/plugins/vendas_ia/tests/` → **95 passed** (74 no baseline + 21 novos).
  - `pytest` do bloco do core (plano 91 + memória de tool + routing + melhorias + ad_store) → **58 passed**.
  - Standalone: `test_hooks` 32 · `test_routing_engine` 26 · `test_agent_routing` 29 — todos 0 falhas.

---

### F9 — Distribuição e deploy 🔴

**Objetivo:** chegar em produção sem perder dado nem configuração.

**Itens** `[sequencial]`:
1. Bump do `plugin.yaml` do `vendas_ia`.
2. Zip **sem `tests/`** — convenção do repo de plugins do Pro (conferido: `protocolos`, `melhorias`, `telegram`, `gowa` não distribuem testes).
3. Commit em `Techify-one/whatsbot-pro-plugins` (`plugins/vendas_ia/vendas_ia.zip` + `.json` + `catalog.json`).
4. ⚠️ **Em produção use "Atualizar", NUNCA "Importar".** O import **recusa** plugin existente (`plugin 'vendas_ia' já instalado — desinstale antes`) e o delete derrubaria as tabelas `plugin_vendas_ia_*` e as settings `plugin.vendas_ia.*` (DSN do Nexus, chave OpenRouter, janelas de silêncio armadas).
5. As mudanças do **core** (F5, I8, F4-UI) vão por `git push` normal — são arquivos versionados.
6. Após o deploy: aplicar os valores de limite pela tela (F4) e escolher o modo de busca (F2).

**Pronto quando:** um turno real com pergunta de preço resolve em ≤2 chamadas de tool, sem timeout, e o custo do turno cai para a faixa de 1 chamada (~17k tokens) em vez de 167k.

#### Status de execução — Fase 9
**Estado:** 🟡 **Parcial** — F9·1 e F9·2 feitos (artefato pronto); F9·3 a F9·6 **aguardando autorização** (são ações fora deste repo / em produção).
- **O que foi feito:**
  - **F9·1 · bump** — `plugin.yaml` do `vendas_ia`: **1.5.1 → 1.6.0** (feature nova + correção de comportamento). A `description` ganhou o parágrafo da 1.6.0 em linguagem de operador: o loop caro, o teto de tempo do embedding, os três modos de busca e a correção da ordenação. A frase "sem o DSN **e a chave OpenRouter** é no-op" foi corrigida — a chave só é necessária no modo `hibrida`.
  - **F9·2 · zip** — gerado **sem `tests/`**, sem `__pycache__`, sem `.pyc`/`.db`, e com o manifest **na raiz** (não dentro de uma pasta `vendas_ia/`, que o import recusaria): **32 arquivos, 222 KB**.
    `/tmp/claude-1000/-home-thiago-whatsbot-pro-whatsbot-pro/96b6ff9c-e367-4fc7-9dbc-da6b4c368945/scratchpad/vendas_ia.zip`
- **Como foi feito / decisões:**
  - O zip foi validado **pelo próprio código de import do core** (`_read_zip_manifest` + `_reject_unsafe_zip_paths` de `server/routes/plugins.py`), não só por inspeção: devolve `id=vendas_ia, version=1.6.0` e passa na checagem de path traversal. Na primeira tentativa o zip saiu com tudo sob `vendas_ia/` e **teria sido recusado** ("manifest ausente na raiz do zip") — conferido contra o formato dos zips de canal já publicados.
- **Problemas / pendências (todas dependem de você):**
  - ⏳ **F9·3 — publicar no repo de plugins do Pro** (`Techify-one/whatsbot-pro-plugins`: `plugins/vendas_ia/vendas_ia.zip` + `.json` + `catalog.json`). **Não há checkout local** deste repo nesta máquina, e publicar é ação externa — não foi feito.
  - ⏳ **F9·4 — deploy em produção pela tela: "Atualizar (.zip)", NUNCA "Importar"**. O import recusa plugin já instalado, e desinstalar derrubaria `plugin_vendas_ia_*` + as settings `plugin.vendas_ia.*` (DSN do Nexus, chave OpenRouter, token da Meta, janelas armadas).
  - ⏳ **F9·5 — core por `git push`**: as mudanças versionadas deste plano são `app/services/agent_run_service.py`, `agent/memory.py`, `web/static/js/components/ai/GeneralSettings.js`, os 2 testes novos em `tests/` e este plano. **Nada foi commitado** (há outras IAs trabalhando neste checkout — um commit meu levaria arquivos dos planos 93/94/95/97 junto).
  - ⏳ **F9·6 — pós-deploy**: aplicar **5 / 15** nos limites e confirmar o **modo de busca** (nasce em `lexica`) pela tela.
- **Verificação:** artefato validado como importável (acima). O "pronto quando" da fase — turno real de pergunta de preço resolvendo em ≤2 chamadas e saindo da faixa de 60k tokens — **só pode ser medido depois do deploy**; ver o checklist §10.

---

## 8. Riscos e cuidados

| # | Ponto | Risco | Mitigação |
|---|---|---|---|
| **R1** | Ligar limite antes de corrigir a causa | troca "loop caro" por "resposta pobre" — o modelo responde com informação parcial | F4 depende de F1/F2 nas waves; ordem é obrigatória |
| **R2** | Teto global baixo demais | ao estourar, `transferir_agente` **e** `transfer_to_human` são recusados — agente sem rota de escape | 12, não 8; campo na UI (D1) para afrouxar na hora |
| **R3** | Limite por-tool ativa o bug do `save_contact_info` | turno reporta dados salvos que não foram | F5 é pré-requisito de F4 |
| **R4** | Chamada bloqueada não gasta cota própria | modelo pode reinvocar para sempre recebendo a mesma mensagem | o freio é o teto global (cada bloqueio consome 1); por isso os dois limites juntos, nunca só o por-tool |
| **R5** | Mudar o `ORDER BY` muda resultado de busca | a IA pode passar a citar outra oferta | é correção de bug (o score era descartado); caracterização em F0 torna a mudança visível e revisável |
| **R6** | Modo `lexica` cair no fallback ingênuo | perde o full-text português junto e vira ILIKE de frase inteira | F2·3 exige variante que **mantém a CTE full-text** |
| **R7** | Campo novo em `settings.py` do plugin | o CAMPO só aparece após restart (import único no load) | documentar; a LEITURA da flag vale na hora |
| **R8** | Apagar a instrução do prompt (F6) | quebra a fixação de oferta via `on_tool_after` | D4 — mover prompt e plugin juntos, ou nenhum |
| **R9** | Import vs Atualizar no deploy | delete derruba tabelas + settings do plugin | F9·4 |
| **R10** | Suíte instável | dois `pytest` no mesmo banco de teste (`DROP SCHEMA` por processo) dão falhas diferentes a cada run | banco isolado (F0·2) |
| **R11** | Modo escuro do campo novo | ilegível no tema escuro | classes `wa-*` / `.wa-field` (F4·5) |
| **R12** | `run_sync` | tentar "consertar junto" código morto | não tocar — sem caller fora de teste |

---

## 9. Perguntas em aberto

**P1 — Valores dos limites.**
Recomendação: `per_tool = 2`, `total = 12`, com campo na UI (D1) para ajustar. Base: distribuição medida (1 chamada resolve; da 3ª nada novo) e o custo de ~15k tokens/chamada.
⏸️ **A CONFIRMAR na F4** — o usuário pode preferir começar mais folgado (3 / 15) e apertar depois, já que a tela permite.

**P2 — `codigo_oferta`: recriar ou migrar?**
(a) **Recriar a definição** — zero código, resolve hoje; contra: redundante com `oferta_atual`, reintroduz o que o plano 41 removeu.
(b) **Migrar prompt + plugin juntos** — uma chave só, mais limpo; contra: exige release do plugin e mexer no prompt de 35k chars.
**Recomendação: (b)** se F9 for acontecer de qualquer forma (o zip já vai subir); **(a)** se quiser o ruído resolvido antes do próximo deploy. ⏸️ **A CONFIRMAR antes da F6.**

**P3 — Modo de busca padrão após o deploy.**
Com 4 ofertas / 17 cursos / 35 FAQ, `catalogo` é o mais rápido e provavelmente o de melhor qualidade (quem casa por sentido passa a ser o GPT-5.2, não um embedding de 8B). Mas é a mudança de comportamento mais visível.
**Recomendação:** subir em `lexica` (conservador, sem rede) e testar `catalogo` numa conversa controlada antes de adotar. ⏸️ **A CONFIRMAR na F2.**

**P4 — Batch no `set_custom_attribute`?**
Medido: a tool é chamada 1–2× por turno, em 4 turnos no período. O ganho hoje é ~1 chamada em 2 turnos — **não paga** a mudança, que exige tocar o consumidor acoplado do core (F.P.#5).
**Recomendação: ⏸️ ADIADO.** Revisitar se o prompt passar a preencher vários campos por virada.

**P5 — Instrumentar de onde vêm os 10 s?**
A composição (bootstrap × embedding × consulta) foi inferida por eliminação, não medida ponta a ponta — medir consumiria uma chamada real de embedding.
**Recomendação: ⏸️ ADIADO.** F1 corrige o sintoma independentemente de onde exatamente estão os segundos; se depois do timeout ainda houver lentidão, aí sim instrumentar.

---

## 10. Checklist de verificação

- [x] Baseline de F0 registrado num **banco de teste isolado** (`whatsbot_test_91`, exclusivo — nunca dois `pytest` no mesmo banco)
- [x] Busca lenta **degrada** para léxico em vez de morrer aos 10 s — provado ponta a ponta com servidor HTTP que nunca responde (3,6 s, log `APITimeoutError, teto 0.5s`)
- [x] Modo de busca configurável pela tela, com os 3 estados funcionando e o diagnóstico refletindo o modo (badge **neutro** "não usada neste modo", não mais vermelho "ausente")
- [x] `LIMIT` das três buscas corta por **score**, não por id/nome — conferido em SQL e **executado contra o Nexus real**
- [x] `save_contact_info` bloqueado ⇒ `updated_info is None` (e o teste falha se o filtro for revertido)
- [x] Campo dos limites salva pela UI e pega no turno seguinte, **sem restart** (PUT → `_resolve_tool_call_limit()` já devolve o valor novo)
- [x] Teto global permite escapar: a mensagem de bloqueio cita `transferir_agente` (travado por teste)
- [ ] ⏸️ Oferta continua sendo **fixada** depois da F6 — **n/a: F6 adiada** por decisão do usuário; a fixação segue como está hoje
- [x] Nexus fora / OpenRouter fora / modo inválido ⇒ turno normal (fail-open) nos três
- [x] Testes do plugin verdes (**95**) + `tests/test_endpoints.py` no **Postgres**: 1633 passed / as mesmas 2 falhas pré-existentes do baseline
- [x] Campo novo legível no **modo escuro** (só `wa-*` / `.wa-field`)
- [ ] ⏳ Deploy do plugin por **Atualizar (.zip)**, nunca Importar; core por `git push` — **aguardando autorização** (F9·3–F9·5)
- [ ] ⏳ Turno real de pergunta de preço resolve em ≤2 chamadas e sai da faixa de 60k+ tokens — **só medível após o deploy**

---

## 11. Apêndice — arquivos-chave

**Plugin `vendas_ia`** (`storages/plugins/vendas_ia/`, **fora do git** — distribuído por zip)
- `embeddings.py` — `timeout` no cliente (F1) · linha 38
- `search.py` — modo de busca (F2) linha 47 · `ORDER BY` (F3) linhas 118, 212, 289
- `settings.py` / `_config.py` — enum do modo (F2·1)
- `routes.py` / `static/config.js` — diagnóstico honesto do modo (F2·4)
- `state.py` (linha 31) / `events.py` (linhas 42, 59) — só no caminho (b) da F6
- `plugin.yaml` — bump (F9·1)

**Core (versionado)**
- [app/services/agent_run_service.py:397](../app/services/agent_run_service.py#L397) — filtro `skipped` (F5·1)
- [agent/memory.py:763-811](../agent/memory.py#L763) — logs nos 4 caminhos vazios (F5·3, opcional)
- [web/static/js/components/ai/GeneralSettings.js](../web/static/js/components/ai/GeneralSettings.js) — campos dos limites (F4·2)

**Somente leitura (referência, não tocar)**
- [agent/tool_isolation.py:54](../agent/tool_isolation.py#L54) — `DEFAULT_TIMEOUT = 10.0`
- [ai_engine/hooks.py:32,67,87,114-125](../ai_engine/hooks.py#L32) — limites, `_ran_count`, escape
- [agent/agno_engine.py:244,255,389-425,561](../agent/agno_engine.py#L244) — bloqueio, `tool.after`, teto global, escopo do contador
- [app/services/messaging_service.py:560-576](../app/services/messaging_service.py#L560) — molde do filtro `skipped` feito certo
- [config/settings.py:208,211,214](../config/settings.py#L208) — as três chaves já expostas
