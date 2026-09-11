# Plano 140 — O Debug Bus passa a reter por TEMPO e por ORÇAMENTO DE DISCO, não por quantidade

> **Status:** PLANEJAMENTO (revisado para escala de 30 GB) · **Data:** 2026-08-20 · **Escopo:** médio/grande (1 plugin; troca a estrutura física da tabela + orçamento em bytes + testes; **zero mudança no core**)
> **Origem:** pedido do operador — as capturas do `debug_bus` duram **~2 h** (medido: 2,32 h), então investigar o problema do dia anterior é impossível. Na revisão de 2026-08-20 o alvo subiu: o operador quer **reter na casa de 30 GB** (≈ 11 meses no ritmo atual), declarando ter infraestrutura para isso. **Método:** medição somente-leitura no banco de produção pelo cofre de credenciais (a identificação da credencial fica fora deste documento — repositório público) + leitura do código real do plugin, com `arquivo:linha` verificados.
> **O quê/porquê:** a única poda de hoje é por CONTAGEM ([store.py:183-198](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L183-L198)), e `max_records = 5000` é exatamente o que produz as 2,3 h. Entram **`retention_hours`** (janela) e **`max_size_gb`** (orçamento de disco), e a tabela passa a ser **particionada por dia**: no alvo de 30 GB, reter deixa de ser `DELETE` e vira `DROP` de partição — é a única forma de o orçamento em GB ser real (ver §5.1: `DELETE` **não devolve** bytes ao sistema de arquivos).
>
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0 — Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 ✅ (2026-08-20) | São **dois** critérios de retenção: **tempo** (`retention_hours`) e **orçamento de disco** (`max_size_gb`). O que vier primeiro poda. | O operador raciocina em GB (foi como ele formulou o pedido); a janela em horas continua sendo o critério legível. `max_records` sobrevive só como freio manual legado, default `0`. |
| D2 ✅ (2026-08-20) | Unidade da janela = **horas**, `0` = desliga. Clamp `0..8760` (1 ano) — **30 dias = 720 cabe, e 8760 h ≈ 32 GB no ritmo medido**. | Coluna `INTEGER`. O clamp de 1 ano e o orçamento de 30 GB são, por coincidência do ritmo medido, a mesma fronteira (§4.1). |
| D3 ✅ (2026-08-20) | Migration **NOVA**. A `001_initial.sql` **não é editada** — já rodou em produção e consta em `plugin_migrations`. | Editar a 001 seria no-op no parque instalado (falha silenciosa). Ver §2.3 as 3 regras do migrator. |
| D4 ✅ (2026-08-20) | O caminho quente continua **fail-open** e, agora, **limitado**: nenhuma operação de poda pode rodar sem teto de trabalho dentro de `record()`. | `record()` nunca propaga exceção ([store.py:201-219](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L201-L219)) **e** a poda passa a ser bounded (§5.3). |
| D5 ✅ (2026-08-20) | Zero mudança no **core**. Tudo dentro de `plugins/debug_bus/src/`. | Não há bump de `WHATSBOT_API_VERSION`; o manifesto continua `">=1.0,<2.0"`. Ganha `entry.lifecycle`, que já é chave suportada. |
| D6 ✅ (2026-08-20) | O plugin é publicado como **2.0.0** — a estrutura física da tabela muda. | MAJOR do PLUGIN (não da API). Bump em 3 lugares + rebuild + **instalar no local antes de publicar**. |
| D7 ✅ (2026-08-20) | `plugin_debug_bus_records` vira **particionada por RANGE(ts), uma partição por dia**. | Reter = `DROP TABLE <partição>`: O(1), sem `VACUUM`, sem inchaço, e **devolve os bytes de verdade** (§5.1). Custo de adoção **hoje**: 5.067 linhas / 8,7 MB. |
| D8 ✅ (2026-08-20) | A janela de leitura da tela passa a ter **recorte de tempo** (default: últimas 24 h) e o download exige `from`/`to`. | Sem isso, uma tabela de 18 milhões de linhas torna a própria tela inútil (§5.4) — e `/download` streamaria 30 GB para o navegador. |

**Princípio fixo:** é um plugin de **diagnóstico**, não caminho de negócio — na dúvida entre perder captura e atrasar mensagem de cliente, perde-se a captura. Toda a mudança fica dentro do `try/except` que já existe, e nenhuma operação nova pode ser ilimitada dentro de `record()`.

---

## 1 — Resumo executivo

O `debug_bus` grava uma linha por evento/filter e poda por **contagem** ([store.py:183-198](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L183-L198)). Medido em produção: **5.067 linhas cobrindo 2,32 h**, com o teto em 5.000 — a tabela vive colada nele. Contagem não é uma unidade que o operador consiga traduzir em "quero o mês passado".

A versão inicial deste plano trocava a contagem por horas. Com o alvo revisado para **~30 GB** (≈ 11 meses / ≈ 18 milhões de linhas), três coisas do desenho atual deixam de servir e são o corpo desta revisão:

1. **`DELETE` não devolve disco.** Um orçamento em GB só é honesto se a poda liberar bytes — daí a partição diária e o `DROP` (§5.1).
2. **A poda roda dentro do caminho quente da mensagem.** No regime atual isso é inofensivo (apaga ~100 linhas por passada); ao encurtar uma janela de 11 meses, viraria um `DELETE` de milhões de linhas dentro de `record()` (§5.3).
3. **A tela e o download não têm recorte de tempo.** `COUNT(*)`, `name ILIKE '%…%'` e `phone =` sem índice, sobre 18 milhões de linhas, transformam o plugin em algo que guarda tudo e não acha nada (§5.4).

Nada disso muda o core. O custo de adoção está no mínimo histórico: a tabela tem **8,7 MB hoje** — daqui a um ano, a mesma mudança exigiria janela de manutenção.

---

## 2 — Como funciona hoje (mapa verificado)

### 2.1 O caminho quente

| Passo | Arquivo:linha | O que faz |
|---|---|---|
| Assinatura universal | [events.py:27](../whatsbot-pro-plugins/plugins/debug_bus/src/events.py#L27) `EVENT_HANDLERS = {"*": …}` · [filters.py:23-59](../whatsbot-pro-plugins/plugins/debug_bus/src/filters.py#L23-L59) (17 filtros, prioridade 9000) | Toda captura entra por `store.record()` |
| Gate + serialização | [store.py:201-217](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L201-L217) | Estado com cache de 3 s; `enabled`/`capture_events`/`capture_filters`/`name_contains`; payload cortado em 40 000 chars ([store.py:38](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L38)) |
| INSERT + contador | [store.py:167-180](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L167-L180) | `_insert_counter += 1` (global, [store.py:60](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L60)) |
| **Poda** | [store.py:183-198](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L183-L198) | 1×/100 inserts (`_PRUNE_EVERY`, [store.py:41](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L41)); `DELETE … WHERE id <= (SELECT MAX(id) - :cap …)`, **sem limite de linhas por passada** |
| Fail-open | [store.py:218-219](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L218-L219) | Qualquer erro vira `logger.debug` |

### 2.2 O estado (linha singleton) e suas DUAS allowlists

| Peça | Arquivo:linha | Observação |
|---|---|---|
| Allowlist do store (tuple) | [store.py:43-45](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L43-L45) | filtra dentro de `set_state` |
| Allowlist da rota (set) | [routes.py:33-35](../whatsbot-pro-plugins/plugins/debug_bus/src/routes.py#L33-L35) | filtra o corpo do `PUT /state` **antes** de chegar ao store |
| Failsafe | [store.py:49-55](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L49-L55) | `enabled=False` — fail-**closed** para captura, de propósito |
| Leitura da row | [store.py:65-72](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L65-L72) | `SELECT *`, mas o campo só existe se `_row_to_state` o copiar |
| Escrita + clamp | [store.py:102-127](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L102-L127) | `max_records` já tem clamp e `try/except` que ignora valor não-numérico ([:112-116](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L112-L116)) |
| Cache TTL 3 s | [store.py:75-99](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L75-L99) | `set_state` invalida no fim ([:126](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L126)) |

⚠️ **As duas allowlists são cópias manuais.** Esquecer a de `routes.py` faz o `PUT` descartar o campo em silêncio — a tela mostra "salvo ✓" e nada muda.

### 2.3 As três regras do migrator do core (verificadas)

| Regra | Onde | Consequência prática |
|---|---|---|
| Nome `NNN_descricao.sql`, ordem numérica, versão aplicada nunca re-roda | [plugins/migrator.py:37](plugins/migrator.py#L37), [:58-75](plugins/migrator.py#L58-L75) | Editar a `001` **não** tem efeito em quem já a aplicou |
| Split por `;` **antes** de qualquer strip de comentário | [plugins/migrator.py:112-140](plugins/migrator.py#L112-L140) | Nenhum comentário pode conter `;`, e **nada de comentário depois do último `;`** (viraria statement de cauda) |
| Prefixo `plugin_debug_bus_` obrigatório em `CREATE/ALTER/DROP TABLE` e `CREATE/DROP INDEX` | [plugins/migrator.py:141-152](plugins/migrator.py#L141-L152) | `CREATE TABLE plugin_debug_bus_records_p20260820 PARTITION OF …` passa; partições criadas em RUNTIME não passam pelo migrator, mas devem seguir o prefixo (é o que faz a desinstalação do plugin limpá-las) |

### 2.4 O caminho de leitura (o que não escala)

| Peça | Arquivo:linha | Comportamento em 18 milhões de linhas |
|---|---|---|
| `count()` | [store.py:279-281](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L279-L281) | `SELECT COUNT(*)` sem filtro = varredura completa. Chamado no `/stats`, isto é, **na carga da tela** |
| `_where` / `list_records` | [store.py:224-254](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L224-L254) | `name ILIKE '%…%'` **não usa** o índice btree de `name`; `phone = :phone` **não tem índice nenhum** — e buscar por telefone é o gesto mais comum de investigação |
| `iter_all` (`/download`) | [store.py:257-276](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L257-L276) | Streama **tudo** que casa nome/kind, sem recorte de tempo. No alvo, seriam dezenas de GB para o navegador ([debug_bus.js:147-164](../whatsbot-pro-plugins/plugins/debug_bus/src/static/debug_bus.js#L147-L164) monta um `blob` **em memória**) |
| Índices existentes | [001_initial.sql:18-22](../whatsbot-pro-plugins/plugins/debug_bus/src/migrations/001_initial.sql#L18-L22) | Só `(ts DESC)` e `(name)`. Nenhum em `phone` |

### 2.5 A tela

| Peça | Arquivo:linha |
|---|---|
| Buffers de edição (`maxRecords`) | [debug_bus.js:74](../whatsbot-pro-plugins/plugins/debug_bus/src/static/debug_bus.js#L74) |
| Semeadura na carga | [debug_bus.js:103-118](../whatsbot-pro-plugins/plugins/debug_bus/src/static/debug_bus.js#L103-L118) |
| Inputs + botão Salvar | [debug_bus.js:216-225](../whatsbot-pro-plugins/plugins/debug_bus/src/static/debug_bus.js#L216-L225) |
| Contadores | [debug_bus.js:255-257](../whatsbot-pro-plugins/plugins/debug_bus/src/static/debug_bus.js#L255-L257) |
| Bloco só-leitura sem `manage` | [debug_bus.js:192-197](../whatsbot-pro-plugins/plugins/debug_bus/src/static/debug_bus.js#L192-L197) |

### 2.6 O manifesto

`entry:` tem hoje `events`, `filters`, `routes` ([plugin.yaml:19-22](../whatsbot-pro-plugins/plugins/debug_bus/src/plugin.yaml#L19-L22)) — **não há `lifecycle`**, então o plugin não tem nenhuma tarefa de fundo. A partição diária precisa de uma (§5.2). ⚠️ Sem a linha `lifecycle: lifecycle` no manifesto, o módulo **nunca é importado** e a falha é **silenciosa** — mesmo modo de falha já documentado no `CLAUDE.md` para o `entry.filters`.

---

## 3 — Inventário das mudanças

| # | Item | Arquivo:linha | O que falta | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|---|
| I1 | Tabela particionada + colunas de estado | novo `migrations/002_retencao.sql` | tabela é monolítica; não há colunas novas | recria `plugin_debug_bus_records` com `PARTITION BY RANGE (ts)`, copia as linhas atuais, dropa a antiga; `ALTER` no `state` com `retention_hours`, `max_size_gb`, `name_excludes` | **médio** | M |
| I2 | Failsafe | [store.py:49-55](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L49-L55) | 3 chaves ausentes | acrescentar com os defaults | baixo | S |
| I3 | Leitura da row | [store.py:65-72](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L65-L72) | não copia as colunas novas | `row.get(...)` com default — **`.get()`**, para row sem a coluna não virar `KeyError` no caminho quente | baixo | S |
| I4 | Allowlist do store | [store.py:43-45](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L43-L45) | campos ausentes | acrescentar ao tuple | baixo | S |
| I5 | Clamp na escrita | [store.py:112-116](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L112-L116) | não trata os campos | ramos espelhando `max_records`: horas `0..8760`, GB `0..1000`, excludes `str[:500]` | baixo | S |
| I6 | **Poda por tempo + orçamento, via `DROP`** | [store.py:183-198](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L183-L198) | só contagem, e **sai cedo** com `max_records <= 0` | vira `prune()` no módulo de partições (§5.3); o gate de saída antecipada muda; trabalho **bounded** por passada | **alto** | L |
| I7 | Allowlist da rota | [routes.py:33-35](../whatsbot-pro-plugins/plugins/debug_bus/src/routes.py#L33-L35) | campos ausentes | acrescentar ao set + comentário cruzado | baixo | S |
| I8 | `name_excludes` (lista de exclusão) | [store.py:211-213](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L211-L213) (só há `name_contains`, de INCLUSÃO) | o oposto do que existe: "tudo MENOS estes nomes" | gate em `record()` logo após o `name_contains`; corta 43 % do disco e 35 % das linhas (§4.2) | baixo | M |
| I9 | Tarefa de fundo (partições + poda) | novo `lifecycle.py` + `entry.lifecycle` em [plugin.yaml:19-22](../whatsbot-pro-plugins/plugins/debug_bus/src/plugin.yaml#L19-L22) | não existe | `ctx.spawn_task` com `RestartPolicy.PERMANENT`: cria as partições dos próximos dias e roda a poda fora do caminho quente | médio | M |
| I10 | Recorte de tempo na leitura | [store.py:224-276](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L224-L276) | `_where` não tem `from`/`to`; `count()` é `COUNT(*)` cru; `iter_all` streama tudo | `from_ts`/`to_ts` em `_where` (habilita partition pruning), `count()` estimado, `/download` **exige** intervalo | médio | M |
| I11 | Índices por partição | [001_initial.sql:18-22](../whatsbot-pro-plugins/plugins/debug_bus/src/migrations/001_initial.sql#L18-L22) | nenhum em `phone`; `name` inútil para `ILIKE '%…%'` | índices declarados na tabela-mãe (propagam para toda partição): `(ts DESC)`, `(phone)`, `(name)` | baixo | S |
| I12 | Tela: campos + janela + aviso | [debug_bus.js:74](../whatsbot-pro-plugins/plugins/debug_bus/src/static/debug_bus.js#L74), [:103-118](../whatsbot-pro-plugins/plugins/debug_bus/src/static/debug_bus.js#L103-L118), [:216-225](../whatsbot-pro-plugins/plugins/debug_bus/src/static/debug_bus.js#L216-L225), [:255-257](../whatsbot-pro-plugins/plugins/debug_bus/src/static/debug_bus.js#L255-L257) | não existe | inputs de horas/GB/excludes, seletor de período (default 24 h), "cobrindo Xh · Y GB de Z GB" | baixo | M |
| I13 | Testes | `plugins/debug_bus/tests/python/` (hoje só `.gitkeep`) | **o plugin não tem teste nenhum** | suíte nova (F7) | baixo | L |
| I14 | Publicação | `plugin.yaml:3`, `debug_bus.json`, `README.md` | versão 1.1.0 | bump **2.0.0** + rebuild + instalar local | baixo | S |

### 3.1 Falsos positivos descartados

| Candidato | Por que NÃO é problema (ou não entra) |
|---|---|
| `_insert_counter` global sem lock ([store.py:60](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L60), [:180](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L180)) | Duas threads podem pular ou repetir um múltiplo de 100. A poda é best-effort por construção, e com a tarefa de fundo (I9) o contador deixa de ser o único gatilho. Pôr um lock no caminho quente para ganhar nada seria pior. |
| Teto por aritmética de `id` (`MAX(id) - :cap`) | Deixa de ser o critério padrão (`max_records = 0`). Sobra como freio manual; imprecisão por gaps é irrelevante nesse papel. |
| Converter `ts` para `timestamptz` | Tentador para particionar, mas mudaria o tipo lido e escrito por `record()`, `_where`, `iter_all` e pela tela. `RANGE` sobre `DOUBLE PRECISION` com limites em epoch funciona igual. Não vale o raio de alcance. |
| Baixar `_PAYLOAD_CAP` ([store.py:38](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L38)) | Corta bytes, não linhas, e o payload cru **é** o produto do plugin. O `name_excludes` (I8) resolve melhor o mesmo problema: 43 % do disco em 98 linhas. |
| Retenção separada por `kind` (evento × filter) | A medição mostrou que a assimetria é entre **nomes**, não entre kinds (§4.2). Separar por `kind` não recortaria nem os dois gigantes nem o campeão de linhas. |
| Mover o `debug_bus` para outro banco | Isolaria o backup dos 30 GB, mas o plugin importa `db.engine.get_engine` e o core não oferece segunda conexão. Seria mudança no core, falhando os 3 critérios da regra de decisão. Fica registrado em P3. |
| Mudar o core | Nada aqui pede evento, filtro ou campo novo. `entry.lifecycle` já é chave suportada. |

---

## 4 — Medição em produção (2026-08-20) e dimensionamento

| Medida | Valor |
|---|---|
| Linhas | **5.067** (teto configurado: **5.000**) |
| Janela coberta | **2,32 h** |
| Taxa | **~2.184 linhas/h** |
| Custo por linha (tabela + índices) | **~1,71 kB** |
| Vazão | **~3,65 MB/h · ~88 MB/dia** |
| Payload médio | 1.182 chars |
| Tabela | 8,7 MB |
| Estado | `enabled=t, events=t, filters=t, name_contains='', max_records=5000` (inalterado desde 2026-07-28) |
| Contexto | banco inteiro **502 MB** (`messages` = 379 MB) |

A tabela está **colada no teto** — é ele, e só ele, que produz as 2,3 h.

### 4.1 Projeção por janela

| Janela | Linhas | Tamanho | Observação |
|---|---|---|---|
| 48 h | ~105 mil | ~175 MB | |
| 7 dias | ~367 mil | ~610 MB | |
| 30 dias | ~1,6 milhão | **~2,6 GB** | ≈ 5× o banco de hoje |
| 90 dias | ~4,7 milhões | ~7,9 GB | |
| **342 dias (~11 meses)** | **~17,9 milhões** | **~30 GB** | o alvo pedido |
| 365 dias (clamp) | ~19,1 milhões | ~32 GB | fronteira do clamp de D2 |

**O alvo de 30 GB ≈ 11 meses no ritmo atual.** Com o `name_excludes` (I8) cortando os 3 nomes da §4.2, o mesmo orçamento compra **~19 meses**, ou os 11 meses custam **~17 GB**.

⚠️ A projeção assume o ritmo de hoje. Um plugin novo que emita muito, ou um pico de tráfego, muda a conta — é por isso que o orçamento em GB (D1) existe: ele é o único limite que **não depende da previsão estar certa**.

### 4.2 Onde o volume está concentrado (medido)

| Nome | Linhas | Payload médio | Peso |
|---|---|---|---|
| `filter.system_prompt` | 49 | **39.013 chars** | 1,87 MB |
| `filter.llm.messages` | 49 | **39.162 chars** | 1,87 MB |
| `filter.authz.decision` | **1.771** | 355 chars | 615 kB |
| `filter.webhook.payload` | 661 | 743 chars | 480 kB |
| `receipt.changed` | 720 | 203 chars | 143 kB |

1. **`filter.system_prompt` + `filter.llm.messages` são 98 linhas (1,9 %) e ~43 % da tabela.** Os dois batem no `_PAYLOAD_CAP` de 40 000 chars — o prompt inteiro regravado a cada turno de LLM. Excluí-los não custa **uma linha** de rastreamento de mensagem.
2. **`filter.authz.decision` são 35 % das LINHAS e 7 % do disco.** Domina a contagem, quase não pesa em bytes.

O `name_contains` de hoje é **inclusão por UMA substring** — serve para "quero só o `filter.webhook.payload`", não para "quero tudo MENOS esses três". Daí o I8.

---

## 5 — O desenho novo (por que particionar)

### 5.1 `DELETE` não devolve disco — e o pedido foi em GB

Num `DELETE`, a linha é marcada morta e o espaço volta para **reúso interno da tabela**; o arquivo no disco **não encolhe**. Reduzir de fato exige `VACUUM FULL` (lock exclusivo + espaço livre equivalente ao tamanho da tabela) ou `pg_repack`.

Consequência prática no alvo: com tabela única, se o operador chegar a 30 GB e depois baixar o orçamento para 10 GB, o Postgres apaga as linhas e o disco continua ocupando **30 GB**. Um "orçamento de disco" que não devolve disco não é orçamento.

Com **partição diária**, reter é `DROP TABLE plugin_debug_bus_records_p<AAAAMMDD>`: instantâneo, sem `VACUUM`, sem inchaço, sem WAL de milhões de tuplas — e **o espaço volta ao sistema de arquivos na hora**.

Ganhos secundários, todos relevantes em 18 milhões de linhas: índices por partição ficam pequenos; a rotina de poda para de competir com o autovacuum; e consultas com recorte de tempo (D8) leem só as partições do período (*partition pruning*).

### 5.2 Como as partições nascem

| Peça | Regra |
|---|---|
| Granularidade | **1 dia** (~88 MB/partição no ritmo medido). ~342 partições vivas no alvo — confortável para o Postgres. Acima de ~2 anos, migrar para semanal |
| Nome | `plugin_debug_bus_records_p<AAAAMMDD>` — mantém o prefixo, então a desinstalação do plugin as remove junto |
| Quem cria | `lifecycle.py` (I9): a cada hora, garante as partições de **hoje + próximos 3 dias** |
| Rede de segurança | Uma partição `DEFAULT` permanente: se a tarefa falhar ou o plugin ficar sem `entry.lifecycle`, **o INSERT nunca quebra** — as linhas caem na `DEFAULT` e a tarefa as realoca depois. ⚠️ Sem ela, uma falha na tarefa de fundo viraria perda silenciosa de captura |
| Chave primária | Numa tabela particionada a PK precisa conter a chave de partição ⇒ **`PRIMARY KEY (id, ts)`**. O `id` segue `BIGSERIAL` global; a paginação por `id DESC` continua valendo |

### 5.3 A poda passa a ser bounded e sai do caminho quente

| Onde | O que roda |
|---|---|
| `lifecycle.py` (a cada ~15 min) | (1) por **tempo**: `DROP` das partições inteiramente anteriores ao corte; (2) por **orçamento**: enquanto `pg_total_relation_size` > `max_size_gb`, `DROP` da partição mais antiga; (3) cria as partições futuras |
| `record()` (caminho quente) | **nada de estrutural.** Mantém, no máximo, o freio legado `max_records` quando o operador o ligar de propósito — e mesmo esse passa a ter `LIMIT` por passada |

⚠️ Esta é a mudança de segurança do plano: hoje o `DELETE` mora dentro de `record()` ([store.py:217](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L217)), que roda na thread que está atendendo a mensagem. É inofensivo com 5.000 linhas e **não é** com 18 milhões — encurtar a janela dispararia um `DELETE` de milhões de linhas dentro do pipeline de atendimento.

⚠️ **`DROP` só apaga partição inteiramente vencida.** Com granularidade diária, a janela real é `retention_hours` arredondada para cima até o fim do dia — 720 h podem virar até 744 h de dados retidos. É deliberado: meio-dia de folga é mais barato que apagar linha por linha.

### 5.4 A tela precisa de recorte de tempo (D8)

No alvo, sem recorte: `COUNT(*)` varre 18 milhões de linhas na carga da tela; `phone = :phone` **não tem índice** ([001_initial.sql:18-22](../whatsbot-pro-plugins/plugins/debug_bus/src/migrations/001_initial.sql#L18-L22)) e busca por telefone é o gesto mais comum de investigação; `name ILIKE '%…%'` não usa o btree de `name`; e `/download` streama tudo, com o cliente montando um **blob em memória** ([debug_bus.js:147-164](../whatsbot-pro-plugins/plugins/debug_bus/src/static/debug_bus.js#L147-L164)).

Por isso: índice em `phone` (I11), `from_ts`/`to_ts` em toda leitura (I10), período default de 24 h na tela, `/download` **exigindo** intervalo, e `count()` estimado (`pg_class.reltuples`) para o rodapé, com contagem exata só dentro do período escolhido.

---

## 6 — Fases / Roadmap

```
WAVE 0   F0 (medição) ✅ · F1 (checar disco/backup)                 ← paralelo
             │
WAVE 1   F2 (migration 002: partições + colunas)                     ← SOZINHA (bloqueia todas)
             │
WAVE 2   F3 (partitions.py + lifecycle) · F4 (store: estado/gates)   ← paralelo
             │  F3 bloqueia F5
WAVE 3   F5 (leitura: recorte, count, download) · F6 (tela)          ← paralelo
             │
WAVE 4   F7 (testes)  →  F8 (versão 2.0.0, ZIP, instalação local)    ← sequenciais
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | Medição em produção | 🟢 | baixo | ✅ concluída — números na §4 |
| 0 | **F1** | Disco, backup e política de retenção do banco | 🟢 | baixo | Folga de 30 GB confirmada e impacto no `pg_dump` decidido (P1) |
| 1 | **F2** | Migration `002_retencao.sql` `[bloqueia: F3, F4, F5, F6, F7]` | 🔴 | **alto** | Tabela particionada em pé, linhas antigas preservadas, `DEFAULT` criada |
| 2 | **F3** | `partitions.py` + `lifecycle.py` + `entry.lifecycle` `[dep: F2]` `[bloqueia: F5]` | 🟢 | médio | Partição do dia seguinte nasce sozinha; `DROP` por tempo e por orçamento funciona |
| 2 | **F4** | `store.py` (estado, clamps, `name_excludes`) + `routes.py` (allowlist) `[dep: F2]` | 🟢 | médio | `PUT /state` persiste os 3 campos; `name_excludes` barra na origem |
| 3 | **F5** | Leitura: `from_ts`/`to_ts`, `count()` estimado, `/download` com intervalo `[dep: F3]` | 🟢 | médio | Busca por telefone em 24 h responde rápido; download sem intervalo é recusado |
| 3 | **F6** | Tela: campos, seletor de período, "X GB de Y GB" | 🟢 | baixo | Operador configura horas + GB e vê o consumo real |
| 4 | **F7** | Testes `[dep: F2–F6]` | 🔴 | médio | `test_plugins.py debug_bus` verde |
| 4 | **F8** | Versão 2.0.0, ZIP, README, instalação local `[dep: tudo]` | 🔴 | baixo | `--check` verde e 2.0.0 rodando no dev |

---

### Fase F0 — Medir antes de escolher o número

**Objetivo:** trocar "duram ~2 h" por números.

#### Status de execução — Fase F0
**Estado:** ✅ Concluída (2026-08-20)
- **O que foi feito:** 4 consultas somente-leitura ao banco de produção. Resultados na §4: **5.067 linhas / 2,32 h / ~2.184 linhas/h / 1,71 kB por linha / payload médio 1.182 chars / tabela 8,7 MB**, estado inalterado desde 2026-07-28. Banco inteiro: 502 MB (`messages` = 379 MB). Recorte por nome na §4.2.
- **Como foi feito / decisões:** transação READ ONLY pelo cofre. Confirmado que a tabela vive **colada no teto** (5.067 ≈ 5.000) — é o `max_records` que produz as 2,3 h. A concentração medida gerou o `name_excludes` (I8) e derrubou a ideia de separar retenção por `kind`.
- **Problemas / pendências:** a **versão instalada em produção** não foi conferida — fazer antes da F8. **Disco livre não verificado** — é a F1.
- **Verificação:** os números fecham entre si (5.067 × 1,71 kB ≈ 8,7 MB) e reproduzem exatamente o sintoma relatado.

---

### Fase F1 — Disco, backup e a conta que ninguém fez

**Objetivo:** confirmar que 30 GB cabem — não só no disco, mas na rotina de backup.

**Itens** `[paralelo]`:

1. Disco livre da instância do Postgres, contra a projeção da §4.1 (30 GB + folga de manutenção).
2. **Impacto no backup**: hoje o `pg_dump` do banco leva 502 MB; com o `debug_bus` no alvo, passaria a ~30 GB — **60× maior**, e são dados descartáveis. Decidir P1 (excluir a tabela do dump / mudar a política).
3. Se houver réplica ou WAL archiving, medir o efeito do volume de escrita (~88 MB/dia de dados novos, mais o churn de poda).
4. Escolher o par `(retention_hours, max_size_gb)` inicial — a recomendação é **P2**.

**Pronto quando:** folga de disco confirmada por escrito no status abaixo, P1 e P2 decididas.

#### Status de execução — Fase F1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase F2 — Migration `002_retencao.sql` (a fase perigosa)

**Objetivo:** a tabela vira particionada e o estado ganha os campos, num único passo atômico.

**Itens** `[sequencial]`:

1. Arquivo novo `../whatsbot-pro-plugins/plugins/debug_bus/src/migrations/002_retencao.sql`. **Não** editar a `001` (D3).
2. Estado — três colunas (`<ALVO_H>` e `<ALVO_GB>` vêm de P2):
   ```sql
   ALTER TABLE plugin_debug_bus_state ADD COLUMN IF NOT EXISTS retention_hours INTEGER NOT NULL DEFAULT <ALVO_H>;
   ALTER TABLE plugin_debug_bus_state ADD COLUMN IF NOT EXISTS max_size_gb     INTEGER NOT NULL DEFAULT <ALVO_GB>;
   ALTER TABLE plugin_debug_bus_state ADD COLUMN IF NOT EXISTS name_excludes   TEXT    NOT NULL DEFAULT '';
   UPDATE plugin_debug_bus_state SET max_records = 0 WHERE max_records = 5000;
   ```
   O `UPDATE` desliga o teto por contagem **só** para quem está no default de fábrica — nunca incondicional, que sobrescreveria escolha do operador.
3. Tabela — renomear a atual, criar a particionada, copiar, dropar:
   - `ALTER TABLE plugin_debug_bus_records RENAME TO plugin_debug_bus_records_legacy;`
   - `CREATE TABLE plugin_debug_bus_records (…, PRIMARY KEY (id, ts)) PARTITION BY RANGE (ts);` — `id` continua `BIGSERIAL`, `ts` continua `DOUBLE PRECISION` (§3.1)
   - índices na tabela-mãe: `(ts DESC)`, `(phone)`, `(name)` — propagam para toda partição futura (I11)
   - `CREATE TABLE plugin_debug_bus_records_pdefault PARTITION OF plugin_debug_bus_records DEFAULT;` — a rede de segurança de §5.2, criada **antes** da cópia
   - `INSERT INTO plugin_debug_bus_records SELECT * FROM plugin_debug_bus_records_legacy;` — hoje são 5.067 linhas (instantâneo); com o teto de fábrica o pior caso é `max_records`
   - `DROP TABLE plugin_debug_bus_records_legacy;`
   - reancorar a sequence do `id` em `MAX(id)`
4. Regras do migrator (§2.3): nenhum `;` dentro de comentário, nada de comentário depois do último `;`, todo objeto com o prefixo `plugin_debug_bus_`.
5. ⚠️ O migrator roda o arquivo inteiro **numa transação** ([plugins/migrator.py:80-87](plugins/migrator.py#L80-L87)) — falha no meio faz rollback completo, sem tabela pela metade. É o que torna a troca segura.

**Pronto quando:** em dev, `\d+ plugin_debug_bus_records` mostra `Partition key: RANGE (ts)` e a `pdefault`; as linhas antigas continuam legíveis na tela; `plugin_migrations` contém a versão 2; reiniciar não reaplica.

#### Status de execução — Fase F2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase F3 — `partitions.py` + `lifecycle.py`: quem cria e quem apaga

**Objetivo:** as partições nascem sozinhas e a retenção acontece fora do caminho quente.

**Itens:**

1. `[sequencial]` Módulo novo `partitions.py`, **puro de FastAPI**, com: `partition_name(ts)`, `ensure_partitions(days_ahead=3)` (idempotente, `CREATE TABLE IF NOT EXISTS … PARTITION OF … FOR VALUES FROM (:a) TO (:b)`), `list_partitions()` (nome + bounds + `pg_total_relation_size`), `drop_expired(retention_hours)` e `enforce_budget(max_size_gb)`.
2. `[sequencial]` `enforce_budget`: soma o tamanho de todas as partições e vai dropando a **mais antiga** enquanto passar do orçamento. ⚠️ **Nunca dropar a partição do dia corrente nem a `DEFAULT`** — dropar a atual apagaria a captura que o operador está olhando naquele instante. Se o orçamento não couber nem com uma partição, registrar WARNING acionável e parar (não é papel do plugin ficar sem lugar para escrever).
3. `[sequencial]` `relocate_default()`: move para as partições certas as linhas que caíram na `DEFAULT`. ⚠️ Em Postgres, criar uma partição que cubra linhas já presentes na `DEFAULT` **falha** — a `DEFAULT` precisa ser esvaziada daquele intervalo antes. É o passo que faz a rede de segurança de §5.2 se auto-curar em vez de travar a criação de partições para sempre.
4. `[paralelo]` `lifecycle.py` com `setup(ctx)` chamando `ctx.spawn_task("debug_bus:maintenance", loop)` com `RestartPolicy.PERMANENT`; laço a cada ~15 min: `ensure_partitions` → `relocate_default` → `drop_expired` → `enforce_budget`, tudo em `try/except` que só loga.
5. `[sequencial]` `entry.lifecycle: lifecycle` no [plugin.yaml:19-22](../whatsbot-pro-plugins/plugins/debug_bus/src/plugin.yaml#L19-L22). ⚠️ Esquecer esta linha = o módulo **nunca é importado**, a tarefa nunca sobe e a falha é **silenciosa** (§2.6). Tem teste dedicado (T9).
6. `[sequencial]` `_maybe_prune` ([store.py:183-198](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L183-L198)) deixa de ser o motor de retenção: ⚠️ **o gate de saída antecipada muda** — hoje `if max_records <= 0: return` ([:184](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L184)) mataria qualquer poda com o teto desligado, que é justamente a configuração nova padrão. Ele fica só com o freio legado, com `LIMIT` por passada (D4).

**Pronto quando:** com o relógio adiantado em dev, a partição do dia seguinte nasce sozinha; `retention_hours` curto dropa as vencidas; um `max_size_gb` menor que o consumo dropa da mais antiga para a mais nova e **para** na do dia corrente; uma linha forçada na `DEFAULT` é realocada no ciclo seguinte.

#### Status de execução — Fase F3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase F4 — `store.py` e `routes.py`: o estado novo

**Objetivo:** os três campos atravessam leitura, escrita, failsafe e as DUAS allowlists.

**Itens:**

1. `[paralelo]` `_FAILSAFE_STATE` ([store.py:49-55](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L49-L55)): `+ retention_hours`, `+ max_size_gb`, `+ name_excludes: ""`.
2. `[paralelo]` `_ALLOWED_STATE_FIELDS` ([store.py:43-45](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L43-L45)): os três nomes.
3. `[paralelo]` `_row_to_state` ([store.py:65-72](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L65-L72)): `row.get(...)` com default. ⚠️ `.get()`, não `[…]` — a `RowMapping` de um banco sem a migration levantaria `KeyError` **dentro de `get_state`**, que roda no caminho quente.
4. `[paralelo]` `set_state` ([store.py:112-116](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L112-L116)): clamps `0..8760` (horas), `0..1000` (GB), `str(v)[:500]` (excludes), cada um com o mesmo `except (TypeError, ValueError): continue`.
5. `[sequencial]` **`name_excludes`** (I8): gate em `record()` logo depois do `name_contains` ([store.py:211-213](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L211-L213)). CSV, comparado por **substring** (mesma semântica do `name_contains`, para não haver duas regras a aprender), e o descarte acontece **antes** de serializar o payload — economia no caminho quente, não só no disco. Vazio = não exclui nada. Semear os 3 nomes da §4.2 é escolha do operador na tela, não da migration.
6. `[paralelo]` `_ALLOWED_STATE_FIELDS` de [routes.py:33-35](../whatsbot-pro-plugins/plugins/debug_bus/src/routes.py#L33-L35): os mesmos três + comentário cruzado em CADA cópia apontando para a outra.
7. `[paralelo]` Docstring do módulo ([store.py:1-19](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L1-L19)) com os invariantes novos.

`GET /stats` ([routes.py:46-50](../whatsbot-pro-plugins/plugins/debug_bus/src/routes.py#L46-L50)) e `GET /state` já espalham o dict inteiro (`**st`) — **não precisam de mudança** para os campos aparecerem.

**Pronto quando:** `PUT /state` com os três campos devolve os três em `data`; `0` persiste como `0`; `name_excludes` barra os nomes listados e deixa o resto passar.

#### Status de execução — Fase F4
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase F5 — Leitura: sem recorte de tempo o plugin guarda tudo e não acha nada

**Objetivo:** achar uma conversa de 3 meses atrás em segundos, e não derrubar a tela ao abrir.

**Itens:**

1. `[sequencial]` `_where` ([store.py:224-236](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L224-L236)): parâmetros `from_ts`/`to_ts` (`ts >= :from_ts AND ts < :to_ts`) — é o que habilita o *partition pruning*.
2. `[sequencial]` `list_records` ([store.py:239-254](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L239-L254)) e a rota `/records` ([routes.py:38-43](../whatsbot-pro-plugins/plugins/debug_bus/src/routes.py#L38-L43)) repassam o intervalo.
3. `[sequencial]` `count()` ([store.py:279-281](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L279-L281)): total **estimado** via `pg_class.reltuples` (rodapé) e contagem **exata** só dentro do período escolhido. Um `COUNT(*)` cru em 18 milhões de linhas na carga da tela é inaceitável.
4. `[sequencial]` `/stats` ([routes.py:46-50](../whatsbot-pro-plugins/plugins/debug_bus/src/routes.py#L46-L50)) passa a devolver, além do estado: `oldest_ts`, `newest_ts`, `size_bytes` (soma das partições), `partition_count` e o total estimado. Aditivo.
5. `[sequencial]` `iter_all` / `/download` ([store.py:257-276](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L257-L276), [routes.py:78-89](../whatsbot-pro-plugins/plugins/debug_bus/src/routes.py#L78-L89)): passam a **exigir** `from`/`to`, com teto de intervalo (ex.: 7 dias por download). ⚠️ O cliente monta um `blob` em memória ([debug_bus.js:147-164](../whatsbot-pro-plugins/plugins/debug_bus/src/static/debug_bus.js#L147-L164)) — sem teto, "Baixar JSONL" no alvo trava o navegador do operador.
6. `[paralelo]` Conferir com `EXPLAIN` que uma busca por telefone em 24 h lê **uma** partição e usa o índice de `phone`.

**Pronto quando:** `/records` com intervalo de 24 h em base grande responde em centenas de ms; `/download` sem intervalo é recusado com mensagem acionável; a tela abre sem `COUNT(*)` cru.

#### Status de execução — Fase F5
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase F6 — A tela

**Objetivo:** o operador configura em horas e GB, e vê o consumo real.

**Itens:**

1. `[paralelo]` Buffers `retentionHours`, `maxSizeGb`, `nameExcludes` ao lado do `maxRecords` ([debug_bus.js:74](../whatsbot-pro-plugins/plugins/debug_bus/src/static/debug_bus.js#L74)); semeados na carga ([debug_bus.js:103-118](../whatsbot-pro-plugins/plugins/debug_bus/src/static/debug_bus.js#L103-L118)). ⚠️ `j.data.x || <default>` engoliria o `0` legítimo — usar checagem que preserve o zero.
2. `[paralelo]` Inputs "Reter últimas ___ horas (0 = desligado)", "Orçamento de disco ___ GB (0 = sem limite)" e "Não capturar nomes que contenham ___" — todos no MESMO `PUT` do botão existente ([debug_bus.js:216-225](../whatsbot-pro-plugins/plugins/debug_bus/src/static/debug_bus.js#L216-L225)), classes `wa-field` (tema escuro), dentro do ramo `canManage` ([debug_bus.js:192-227](../whatsbot-pro-plugins/plugins/debug_bus/src/static/debug_bus.js#L192-L227)).
3. `[paralelo]` Placeholder do excludes com os 3 nomes medidos: `filter.system_prompt, filter.llm.messages, filter.authz.decision`, e uma linha dizendo o que isso economiza (§4.2).
4. `[sequencial]` **Seletor de período** (últimas 24 h · 7 dias · 30 dias · intervalo livre), default 24 h, alimentando `/records` e `/download` (D8).
5. `[sequencial]` Rodapé honesto no lugar do "N no total" ([debug_bus.js:255-257](../whatsbot-pro-plugins/plugins/debug_bus/src/static/debug_bus.js#L255-L257)): **"cobrindo desde <data> · X,X GB de Y GB · N partições"**, com aviso quando o orçamento (e não o tempo) é quem está podando — é a informação que faltava para o operador entender por que a janela encolheu.

**Pronto quando:** configurar horas e GB, dar F5 e os valores persistirem; `0` continua `0`; o rodapé mostra consumo e cobertura reais; legível no modo escuro; quem só tem `view` não vê campo de escrita.

#### Status de execução — Fase F6
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase F7 — Testes (o plugin não tem NENHUM hoje)

**Objetivo:** travar o comportamento novo e as armadilhas que esta base já cobrou caro.

Criar `../whatsbot-pro-plugins/plugins/debug_bus/tests/python/`, no padrão de [plugins/janela_72h/tests/python/test_ad_campaign.py](../whatsbot-pro-plugins/plugins/janela_72h/tests/python/test_ad_campaign.py) (`load_plugin_module` + fixture que sobe o app real com `build_test_app_with_plugin("debug_bus")`, para **rodar as migrations de verdade, inclusive a 002**):

| # | Teste | O que protege |
|---|---|---|
| T1 | Semear rows com `ts` de dias distintos ⇒ caem em partições distintas; `drop_expired` apaga só as vencidas | a regra central |
| T2 | `max_records = 0` + `retention_hours > 0` ⇒ a retenção **continua** acontecendo | o gate de saída antecipada de [store.py:184](../whatsbot-pro-plugins/plugins/debug_bus/src/store.py#L184) — o erro mais provável desta implementação |
| T3 | `enforce_budget` com orçamento menor que o consumo ⇒ dropa da mais antiga para a mais nova e **nunca** a do dia corrente nem a `DEFAULT` | apagar o que o operador está olhando |
| T4 | Insert com partição faltando ⇒ cai na `DEFAULT` **sem erro**; `relocate_default` a realoca e a partição do dia passa a ser criável | a rede de segurança de §5.2 e o travamento clássico do `DEFAULT` |
| T5 | `set_state` com `-5` / `99999` / `"abc"` nos três campos ⇒ clamp / clamp / valor inalterado | os clamps |
| T6 | `PUT /state` via `TestClient` com os três campos ⇒ voltam no `data` | a allowlist DUPLICADA (só um teste de ROTA pega o esquecimento em `routes.py`) |
| T7 | `record()` com o banco derrubado ⇒ **não levanta** | o fail-open do caminho quente (D4) |
| T8 | `name_excludes` barra os nomes listados; vazio grava tudo | o gate do I8, incluindo o caso retrocompatível |
| T9 | O manifesto declara `entry.lifecycle` **e** a tarefa sobe pelo loader real | a falha silenciosa de §2.6 — um teste que importe o módulo por caminho fica verde com a costura arrancada |
| T10 | `/download` sem intervalo ⇒ recusado; com intervalo acima do teto ⇒ recusado | D8 |
| T11 | `_row_to_state` sobre row sem as colunas novas ⇒ defaults, sem `KeyError` | o `.get()` defensivo (I3) |

Rodar:
```bash
cd ../whatsbot-pro-plugins
WHATSBOT_TEST_DB_URL="postgresql+psycopg://…/whatsbot_test" python3 scripts/test_plugins.py --python-only debug_bus
```
⚠️ Nunca com outra suíte Postgres em paralelo (o schema `public` é recriado por processo) — e o processo concorrente pode estar em outra máquina.

**Pronto quando:** os 11 testes verdes e a suíte do core (`venv/bin/python -m pytest`) sem regressão nova.

#### Status de execução — Fase F7
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase F8 — Publicação (2.0.0)

**Objetivo:** o operador recebe a versão certa — e a cópia que ele testa é a que roda.

**Itens** `[sequencial]`:

1. `git fetch` no repositório de plugins **e** conferir a versão instalada em PRODUÇÃO (pendência da F0) — publicação acontece por `Importar (.zip)`, que não passa por git.
2. Bump para **2.0.0** em [plugin.yaml:3](../whatsbot-pro-plugins/plugins/debug_bus/src/plugin.yaml#L3) e em `plugins/debug_bus/debug_bus.json`. MAJOR porque a estrutura física da tabela muda (D6).
3. Linha do `debug_bus` no `README.md` do repositório de plugins.
4. `python3 scripts/build_plugins.py debug_bus` e depois `--check`. ⚠️ Um "outdated" que só difere em **permissão de arquivo** é falso positivo de `umask` — não rebuildar às cegas.
5. **Instalar o ZIP no dev local** e validar de ponta a ponta ANTES de commitar/publicar: a cópia viva é `storages/plugins/debug_bus/`, e é ela que o usuário testa.
6. Em produção: ativar com janela **curta** primeiro (ex.: 48 h), confirmar que as partições nascem e a poda roda por um ciclo, e só então abrir para o alvo de P2.
7. Commit no repositório de plugins (src + tests + zip + json + README).

**Pronto quando:** `--check` verde, 2.0.0 em Gerenciar Plugins no dev, migration 002 aplicada na importação, e o ciclo de manutenção observado rodando ao menos uma vez.

#### Status de execução — Fase F8
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

## 7 — Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| **F2 troca a tabela** | Migration com rename + create + copy + drop; falha no meio deixaria o plugin sem tabela | O migrator roda o arquivo **numa transação** ([plugins/migrator.py:80-87](plugins/migrator.py#L80-L87)) — ou tudo, ou nada. Ensaiar em dev com a base de produção restaurada |
| Partição faltando | INSERT falharia e a captura pararia em silêncio | Partição `DEFAULT` permanente (§5.2) + `relocate_default` (T4) |
| `entry.lifecycle` esquecido no manifesto | Tarefa nunca sobe; partições param de nascer; **falha silenciosa** | T9 verifica pelo loader real, não por import direto |
| Allowlist duplicada | `PUT` aceita, tela diz "salvo ✓", valor não muda | Os dois pontos na mesma fase (F4), comentário cruzado, e T6 pela ROTA |
| Gate `max_records <= 0` | Teto desligado (a configuração nova padrão) desligaria a poda junto | T2 existe só para isto |
| Poda no caminho quente | Encurtar uma janela de 11 meses dispararia `DELETE` de milhões de linhas dentro de `record()` | Retenção migra para a tarefa de fundo (§5.3); o freio legado ganha `LIMIT` |
| Orçamento apertado demais | `enforce_budget` poderia dropar tudo, inclusive o dia corrente | Nunca dropar a partição atual nem a `DEFAULT`; WARNING acionável quando não couber (F3, item 2) |
| **Backup** | `pg_dump` sai de 502 MB para ~30 GB de dados descartáveis | P1 — excluir a tabela do dump é o caminho natural |
| `;` em comentário da migration | O splitter corta o comentário e o statement quebra | §2.3; e nada de comentário depois do último `;` |
| Dados sensíveis | As capturas carregam telefone, texto e payload cru — **11 meses** aumentam muito a exposição | Sem `/public/` nas rotas ([routes.py:1-14](../whatsbot-pro-plugins/plugins/debug_bus/src/routes.py#L1-L14)) e RBAC `view`/`manage` mantidos. ⚠️ Reter quase um ano de conteúdo de conversa é decisão de **retenção de dados pessoais**, não só de disco — ver P4 |
| Granularidade diária | A janela real arredonda até o fim do dia (720 h podem reter até 744 h) | Deliberado (§5.3); documentar na tela |
| Restart do plugin | Importar/ativar derruba o processo (`os._exit`) | Esperado; validar em dev com o supervisor do `linux_start.sh` |
| Tema escuro | Inputs novos sem `wa-field` ficam ilegíveis | Copiar as classes do input vizinho (F6) |

---

## 8 — Perguntas em aberto

**P1 — O que fazer com o backup?** ⏸️ AGUARDANDO (F1)
Com o alvo, o `debug_bus` passa a ser ~98 % do volume do `pg_dump`, e é dado **descartável por natureza**.
(a) Excluir a tabela do dump (`--exclude-table-data='plugin_debug_bus_records*'`).
(b) Deixar como está e aceitar um backup 60× maior.
**Recomendação: (a)** — perder captura de debug num restore é irrelevante; multiplicar por 60 o tempo e o custo do backup não é.

**P2 — Que par `(retention_hours, max_size_gb)` entra em produção?** ⏸️ AGUARDANDO
| Opção | Janela | Orçamento | Custo real |
|---|---|---|---|
| (a) | `8760` (1 ano) | `30` | ~30 GB — o GB é quem poda; a janela é só o teto absoluto |
| (b) | `8760` | `30` **+ `name_excludes`** | ~19 meses dentro dos mesmos 30 GB, ou 11 meses por ~17 GB |
| (c) | `2160` (90 dias) | `30` | ~8 GB — o tempo poda antes; o orçamento vira só rede de segurança |
**Recomendação: (b)** — o orçamento é o limite que **não depende da projeção estar certa**, e o `name_excludes` compra 8 meses a mais sem custar uma linha de rastreamento de mensagem. Começar com janela curta na primeira subida (F8, item 6) e abrir depois.

**P3 — O `debug_bus` deveria morar em outro banco?** ⏸️ ADIADO
30 GB de dado descartável convivendo com 502 MB de dado de negócio pressiona backup, réplica e autovacuum do MESMO cluster.
(a) Manter no banco do WhatsBot (este plano).
(b) Banco/instância separada — exigiria seam novo no core (segunda conexão), falhando os 3 critérios da regra de decisão.
**Recomendação: (a)**, com P1 resolvendo 90 % da dor. Reabrir só se a réplica sofrer.

**P4 — Retenção de dados pessoais.** ⏸️ AGUARDANDO DECISÃO DO USUÁRIO
As capturas contêm telefone, texto de conversa e payload cru do provedor. Guardar 2 h disso é um detalhe operacional; guardar **11 meses** é uma política de retenção de dado pessoal, com implicações de LGPD que o disco não resolve. A tela já avisa "desligue e limpe ao terminar" ([debug_bus.js:180-187](../whatsbot-pro-plugins/plugins/debug_bus/src/static/debug_bus.js#L180-L187)) — esse aviso foi escrito para uma janela de horas.
(a) Assumir a retenção longa conscientemente, com o RBAC atual (`view`/`manage`) como controle de acesso.
(b) Reter longo **só** para os nomes sem conteúdo de conversa (`receipt.changed`, `connection.*`, `execution.*`) e curto para o resto — exigiria retenção por nome, que é bem mais complexa que o `name_excludes`.
**Recomendação: (a)** explicitamente registrada, com o `name_excludes` cortando `filter.system_prompt`/`filter.llm.messages` — que, além de serem 43 % do disco, são os que carregam **a conversa inteira** dentro de um único registro.

**P5 — E se particionar for demais?** ✅ DECIDIDO (2026-08-20): particionar.
A alternativa (tabela única + `DELETE` em lotes + índices) é um diff bem menor e resolveria o caminho quente. Foi descartada por um motivo objetivo: **`DELETE` não devolve bytes ao sistema de arquivos** (§5.1), e o pedido do operador é um **orçamento em GB**. Somam-se o inchaço/autovacuum de uma tabela de 30 GB em churn contínuo e o custo de adoção, que hoje é ~zero (8,7 MB) e em um ano exigiria janela de manutenção.

**P6 — Podar também no `GET /stats`?** ⏸️ ADIADO
Com a tarefa de fundo (I9), o buraco antigo ("captura desligada ⇒ nada é podado") **deixa de existir** — a tarefa roda independentemente de haver inserts. A pergunta perde a urgência; fica registrada caso se decida não shipar o `lifecycle`.

---

## 9 — Apêndice — arquivos-chave

**Plugin (`../whatsbot-pro-plugins/plugins/debug_bus/`)**

| Camada | Arquivo | Ação |
|---|---|---|
| DB | `src/migrations/002_retencao.sql` | **novo** (F2) — partições + 3 colunas |
| Backend | `src/partitions.py` | **novo** (F3) — criar, listar, dropar, orçamento |
| Backend | `src/lifecycle.py` | **novo** (F3) — tarefa de manutenção |
| Backend | `src/store.py` | estado, clamps, `name_excludes`, freio legado (F4) · leitura com recorte (F5) |
| Backend | `src/routes.py` | allowlist (F4) · `/stats` enriquecido e `/download` com intervalo (F5) |
| Frontend | `src/static/debug_bus.js` | campos, seletor de período, rodapé de consumo (F6) |
| Manifesto | `src/plugin.yaml` | `entry.lifecycle` (F3) · versão 2.0.0 (F8) |
| Catálogo | `debug_bus.json`, `README.md` | versão + descrição (F8) |
| Testes | `tests/python/` | **novo** (F7) — 11 testes |

**Core (somente leitura — nada muda aqui)**

| Arquivo | Por quê |
|---|---|
| [plugins/migrator.py](plugins/migrator.py) | regras de split/prefixo/transação da migration (§2.3, F2) |
| [plugins/context.py](plugins/context.py) | `ctx.spawn_task` / `RestartPolicy` para o lifecycle (F3) |
| [tests/plugin_test_utils.py](tests/plugin_test_utils.py), [tests/plugin_fixtures.py](tests/plugin_fixtures.py), [tests/support.py](tests/support.py) | harness dos testes de plugin (F7) |

---

## 10 — Checklist de verificação

- [ ] Disco livre confirmado contra a projeção da §4.1, com folga de manutenção
- [ ] Política de backup decidida (P1) antes de abrir a janela longa
- [ ] `002_retencao.sql` aplicada em dev; reiniciar **não** reaplica (`plugin_migrations` tem a versão 2)
- [ ] A `001_initial.sql` continua **byte a byte** intacta
- [ ] Nenhum `;` dentro de comentário, e nenhum comentário depois do último `;`
- [ ] `\d+ plugin_debug_bus_records` mostra `PARTITION BY RANGE (ts)`, a `pdefault` e os índices herdados
- [ ] As linhas que existiam antes da migration continuam legíveis na tela
- [ ] Partição do dia seguinte nasce sozinha pela tarefa de fundo
- [ ] Linha forçada na `DEFAULT` é realocada e não impede a criação da partição daquele dia
- [ ] `drop_expired` apaga só o vencido; `enforce_budget` para na partição do dia corrente
- [ ] Os 3 campos presentes nas **duas** allowlists (`store.py` **e** `routes.py`)
- [ ] `PUT /state` devolve os 3 no `data`; `0` persiste como `0` em todos eles
- [ ] `name_excludes` barra os nomes listados; vazio não exclui nada
- [ ] `record()` não levanta com o banco indisponível (fail-open preservado)
- [ ] Nenhuma operação ilimitada dentro de `record()`
- [ ] Busca por telefone num período de 24 h usa índice e lê uma partição (`EXPLAIN`)
- [ ] Tela abre sem `COUNT(*)` cru; rodapé mostra cobertura, GB usados e nº de partições
- [ ] `/download` exige intervalo e recusa acima do teto
- [ ] `test_plugins.py --python-only debug_bus` verde (11 testes), sem outra suíte Postgres concorrente
- [ ] Suíte do core (`venv/bin/python -m pytest`) sem regressão nova
- [ ] Modo escuro legível nos campos novos; quem só tem `view` não vê campo de escrita
- [ ] `build_plugins.py --check` verde (ignorando falso positivo de permissão/umask)
- [ ] ZIP 2.0.0 **instalado e testado no dev local** antes de commitar/publicar
- [ ] Versão de produção conferida antes de publicar
- [ ] Em produção: subir com janela curta, observar um ciclo de manutenção, só então abrir para o alvo
