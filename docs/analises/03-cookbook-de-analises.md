# WhatsBot — Cookbook de análises e relatórios

Receitas SQL (Postgres) prontas para cada relatório/análise pedido. Este documento é **contexto consumível por IA**: cada receita tem um selo de viabilidade, a intenção, o SQL executável e as ressalvas. Onde a métrica não sai limpa do schema de hoje, mostro o **melhor caminho atual** e aponto a **doc 04 (instrumentação recomendada)**.

## Convenções (leia antes de rodar)

- **Timestamps são epoch float em UTC** (`Float`, ex.: `atendimentos.opened_at`, `messages.ts`, `executions.started_at`). Não são `TIMESTAMP` SQL. Para bucketizar "no dia" ancoramos SEMPRE em `America/Sao_Paulo`:

  ```sql
  (to_timestamp(ts) AT TIME ZONE 'America/Sao_Paulo')::date
  ```

  `to_timestamp(ts)` devolve um `timestamptz`; `AT TIME ZONE 'America/Sao_Paulo'` traz para a parede local de SP; `::date` dá o dia-calendário paulista. Todo filtro/bucket "por dia" deste cookbook usa essa forma.

- **Variante index-friendly** (evita o cast por linha em tabelas grandes — usa o índice em `opened_at`/`ts`/`started_at`): converta o dia paulista em limites epoch:

  ```sql
  WHERE opened_at >= extract(epoch from (DATE '2026-07-17')::timestamp AT TIME ZONE 'America/Sao_Paulo')
    AND opened_at <  extract(epoch from (DATE '2026-07-18')::timestamp AT TIME ZONE 'America/Sao_Paulo')
  ```

  (`(DATE 'D')::timestamp AT TIME ZONE 'America/Sao_Paulo'` interpreta a meia-noite paulista como `timestamptz` → epoch.) As receitas usam a forma legível `::date = DATE '...'`; troque pela range quando o volume exigir.

- **Discriminadores canônicos de remetente** (tabela `messages`), usados o cookbook inteiro:
  - **Cliente** → `role='user'`.
  - **IA** → `role='assistant' AND agent_key IS NOT NULL`.
  - **Atendente humano (envio pelo painel)** → `role='assistant' AND status='operator' AND sent_by_user_id IS NOT NULL`.
  - **Echo (operador digitou no próprio celular)** → `role='assistant' AND status='operator' AND sent_by_user_id IS NULL AND agent_key IS NULL`.
  - **Cards painel-only (NÃO vão ao WhatsApp)** → `role IN ('tool_call','system_notice','transcription','private_note','error','conversation_event','system')`.

- **Nome físico da tabela de conversas é `atendimentos`** (renomeada de `conversations`; `db/tables.py:426`, alias Python `conversations = atendimentos` em `db/tables.py:854`). Nas queries use `atendimentos`.

- Nas receitas, `DATE '2026-07-17'` é o dia-alvo (troque livremente; `CURRENT_DATE` para "hoje").

---

## A. Atendimentos abertos no dia

### A.1 GERAL — **PRONTO**

Intenção: quantas conversas nasceram (foram abertas) em cada dia paulista.

```sql
SELECT
  (to_timestamp(opened_at) AT TIME ZONE 'America/Sao_Paulo')::date AS dia,
  COUNT(*) AS abertos
FROM atendimentos
WHERE (to_timestamp(opened_at) AT TIME ZONE 'America/Sao_Paulo')::date = DATE '2026-07-17'
GROUP BY 1
ORDER BY 1;
```

### A.2 POR ATENDENTE — **PARCIAL**

Intenção: abertos hoje, quebrados por atendente. Não existe "quem abriu" — só "quem está atribuído".

```sql
SELECT
  COALESCE(u.name, '(não atribuído)') AS atendente,
  COUNT(*) AS abertos_atribuidos
FROM atendimentos a
LEFT JOIN users u ON u.id = a.assignee_user_id
WHERE (to_timestamp(a.opened_at) AT TIME ZONE 'America/Sao_Paulo')::date = DATE '2026-07-17'
GROUP BY 1
ORDER BY abertos_atribuidos DESC;
```

> ⚠️ **Ressalvas / o que falta**
> - `assignee_user_id` é **atribuição posterior**, gravada por uma ação explícita de atribuir; **nunca no create** (`_insert_conversation` não carimba assignee). Logo isto é "abertas hoje agrupadas por quem está atribuído AGORA", não "quem abriu".
> - `assignee_user_id` é **zerado ao fechar** (`conversation_repo.py:641`): um atendimento aberto e já fechado hoje some do atendente e cai em `(não atribuído)`.
> - `opened_at` **não** é resetado na reabertura — uma conversa reaberta hoje ainda mostra o `opened_at` original (não conta como "aberta hoje").
> - Para "**iniciada por atendente X**" (o sinal real de proatividade), veja a **receita F**, que deriva do remetente da 1ª mensagem — não do assignee.
> - Solução limpa (coluna `opened_by_user_id`/`created_by_user_id`): **doc 04**.

---

## B. Atendimentos fechados no dia

### B.1 GERAL — **PARCIAL** (volatilidade do `resolved_at`)

Intenção: quantas conversas foram resolvidas em cada dia.

```sql
SELECT
  (to_timestamp(resolved_at) AT TIME ZONE 'America/Sao_Paulo')::date AS dia,
  COUNT(*) AS fechados
FROM atendimentos
WHERE resolved_at IS NOT NULL
  AND (to_timestamp(resolved_at) AT TIME ZONE 'America/Sao_Paulo')::date = DATE '2026-07-17'
GROUP BY 1
ORDER BY 1;
```

Alternativa **auditável** (conta todos os fechamentos, inclusive de conversas depois reabertas) via card `conversation_event`:

```sql
SELECT
  (to_timestamp(ts) AT TIME ZONE 'America/Sao_Paulo')::date AS dia,
  COUNT(*) AS eventos_fechamento
FROM messages
WHERE role = 'conversation_event'
  AND content LIKE '✅%'            -- '✅ <nome> resolveu a conversa.' ou '✅ Conversa resolvida.'
  AND (to_timestamp(ts) AT TIME ZONE 'America/Sao_Paulo')::date = DATE '2026-07-17'
GROUP BY 1
ORDER BY 1;
```

> ⚠️ **Ressalvas / o que falta**
> - `resolved_at` guarda **só o último fechamento**: reabrir **zera** a coluna (`conversation_repo.py:644`) e refechar **sobrescreve**. Uma conversa fechada→reaberta→fechada em dias diferentes mostra só o dia do último fechamento; uma fechada e reaberta hoje aparece com `resolved_at = NULL` (parece nunca fechada) → **a query B.1 subconta**.
> - O `create → closed` da regra "ignorar abertura" **não** grava `resolved_at` (só `set_status('closed')` grava) — essas conversas nascem fechadas sem timestamp de fechamento.
> - O card `conversation_event` (variante auditável) preserva o histórico completo de cada fechamento — mas só se o grupo de avisos `status` estiver ligado (`system_notice_status`, default ON). Grupo desligado ⇒ o card não é gravado.
> - Fonte limpa (tabela de histórico de status): **doc 04**.

### B.2 POR ATENDENTE — **BLOQUEADO** no core

Intenção: fechados hoje por atendente. **Não é computável a partir das linhas de `atendimentos`**: o fechamento faz `assignee_user_id = NULL`, não existe `closed_by_user_id`, e `audit_log` não registra ciclo de vida de conversa.

Melhor caminho hoje — reconstruir o ator do texto PT-BR do card `status_closed` (o ator é o **nome de exibição**, sem `user_id`):

```sql
SELECT
  COALESCE(
    substring(content FROM '^✅ (.+) resolveu a conversa\.$'),
    '(sem ator / automático)'
  ) AS atendente_nome,
  COUNT(*) AS fechamentos
FROM messages
WHERE role = 'conversation_event'
  AND content LIKE '✅%'
  AND (to_timestamp(ts) AT TIME ZONE 'America/Sao_Paulo')::date = DATE '2026-07-17'
GROUP BY 1
ORDER BY fechamentos DESC;
```

> ⚠️ **Ressalvas / o que falta**
> - O ator vem **só do texto** (`'✅ João resolveu a conversa.'`) — é o **nome snapshot**, não o `user_id`. Homônimos colapsam; renomear o usuário depois não afeta o card antigo; fechamentos impessoais/automáticos (`'✅ Conversa resolvida.'`) caem em `(sem ator / automático)`.
> - Depende do grupo `system_notice_status` estar ON (senão o card nem existe).
> - Não há como cruzar de volta para `users.id` com confiança (colisão de nomes). Para relatório fiel "fechados por atendente", a solução é **instrumentar** `closed_by_user_id` na conversa (e/ou uma tabela de eventos de status com `actor_user_id`) — **doc 04**.
> - Contraste: no **protocolo** isso É resolvível hoje (receita E, via assignee do ciclo).

---

## C. Atendimentos por IA vs humano vs misto — **PRONTO**

Intenção: classificar cada atendimento pela composição das respostas, usando a convenção canônica derivada de `messages` (mais confiável que `executions` para o split).

```sql
WITH conv_flags AS (
  SELECT
    m.conversation_id,
    bool_or(m.role = 'assistant' AND m.agent_key IS NOT NULL)                                   AS tem_ia,
    bool_or(m.role = 'assistant' AND m.status = 'operator' AND m.sent_by_user_id IS NOT NULL)   AS tem_humano
  FROM messages m
  WHERE m.conversation_id IS NOT NULL
  GROUP BY m.conversation_id
)
SELECT
  CASE
    WHEN cf.tem_ia AND NOT cf.tem_humano THEN 'IA-only'
    WHEN cf.tem_humano AND NOT cf.tem_ia THEN 'Humano-only'
    WHEN cf.tem_ia AND cf.tem_humano     THEN 'Misto'
    ELSE 'Sem resposta'
  END AS categoria,
  COUNT(*) AS atendimentos
FROM atendimentos a
LEFT JOIN conv_flags cf ON cf.conversation_id = a.id
WHERE (to_timestamp(a.opened_at) AT TIME ZONE 'America/Sao_Paulo')::date = DATE '2026-07-17'
GROUP BY 1
ORDER BY atendimentos DESC;
```

Complemento de tokens/custo por atendimento (use `executions` só como fonte de custo, NÃO para o split):

```sql
SELECT
  e.conversation_id,
  SUM(e.total_tokens)   AS tokens,
  SUM(e.total_cost_usd) AS custo_usd
FROM executions e
WHERE e.has_ai = 1 AND e.conversation_id IS NOT NULL
GROUP BY e.conversation_id;
```

> ⚠️ **Ressalvas / o que falta**
> - Split derivado de `messages` é a fonte recomendada: independe de retenção e de `execution_id`. `bool_or` sobre os discriminadores canônicos é exato.
> - Um atendimento cujas respostas foram todas painel-**echo** (operador no próprio celular: `sent_by_user_id IS NULL`) NÃO conta como humano aqui — cai em "Sem resposta". Se quiser contar echo como humano, troque a flag `tem_humano` por `role='assistant' AND status='operator'` (perde a separação echo × painel).
> - `executions.has_ai` é complemento para custo/tokens: menos confiável para o split (execuções são podadas por idade/contagem, `conversation_id` é nullable em linhas legadas, e turnos só-mídia podem não marcar `has_ai=1`).

---

## D. Conversas nativas por dia e por canal — **PRONTO**

Intenção: volume de atendimentos (conversas nativas) por dia, quebrado por canal, via join `atendimentos → inboxes → channels`.

```sql
SELECT
  (to_timestamp(a.opened_at) AT TIME ZONE 'America/Sao_Paulo')::date AS dia,
  COALESCE(c.display_name, i.name, '(sem canal)') AS canal,
  c.provider,
  COUNT(*) AS conversas
FROM atendimentos a
JOIN inboxes i        ON i.id = a.inbox_id
LEFT JOIN channels c  ON c.id = i.channel_id
WHERE (to_timestamp(a.opened_at) AT TIME ZONE 'America/Sao_Paulo')::date = DATE '2026-07-17'
GROUP BY 1, 2, 3
ORDER BY 1, conversas DESC;
```

Só o total do dia (sem quebra por canal):

```sql
SELECT
  (to_timestamp(opened_at) AT TIME ZONE 'America/Sao_Paulo')::date AS dia,
  COUNT(*) AS conversas
FROM atendimentos
GROUP BY 1
ORDER BY 1;
```

> ⚠️ **Ressalvas / o que falta**
> - `inboxes.channel_id` é `Text` FK → `channels.id`; fica `NULL` quando o canal foi removido (inbox órfã) → `LEFT JOIN` + `COALESCE` cobre isso como `(sem canal)`.
> - O rótulo amigável é `channels.display_name`; `channels.id` é um slug (ex.: `default`) e `provider` é `gowa|whatsapp_cloud|telegram|test`.
> - "Conversas nativas" = linhas de `atendimentos` (NÃO confundir com protocolos — receita E). Múltiplas conversas **fechadas** por (contato, inbox) são permitidas; só há no máx. 1 **aberta** por par.
> - `is_archived` é ortogonal ao status — inclua `WHERE a.is_archived = 0` se quiser excluir arquivadas.

---

## E. Protocolos abertos/fechados no dia (plugin `protocolos`)

Tabelas do plugin: `plugin_protocolos_protocolos` (a entidade Protocolo) e `plugin_protocolos_atendimentos` (o **ciclo** que liga uma conversa do core a um protocolo — NÃO é a tabela de atendimentos do core). Status do protocolo: `'aberto' | 'fechado'`.

### E.1 Abertos no dia — GERAL — **PRONTO**

```sql
SELECT
  (to_timestamp(opened_at) AT TIME ZONE 'America/Sao_Paulo')::date AS dia,
  COUNT(*) AS protocolos_abertos
FROM plugin_protocolos_protocolos
WHERE (to_timestamp(opened_at) AT TIME ZONE 'America/Sao_Paulo')::date = DATE '2026-07-17'
GROUP BY 1
ORDER BY 1;
```

### E.2 Fechados no dia — GERAL — **PARCIAL**

```sql
SELECT
  (to_timestamp(closed_at) AT TIME ZONE 'America/Sao_Paulo')::date AS dia,
  COUNT(*) AS protocolos_fechados
FROM plugin_protocolos_protocolos
WHERE status = 'fechado' AND closed_at IS NOT NULL
  AND (to_timestamp(closed_at) AT TIME ZONE 'America/Sao_Paulo')::date = DATE '2026-07-17'
GROUP BY 1
ORDER BY 1;
```

### E.3 Fechados/resolvidos no dia — POR ATENDENTE — **PRONTO** (via assignee do CICLO)

Intenção: quem **executou a resolução**. Use o assignee do ciclo (`plugin_protocolos_atendimentos.assignee_user_id` + `ended_at`, gravado do `current_user` no `/resolve`) — não o dono do protocolo.

```sql
SELECT
  COALESCE(u.name, '(sem atendente)') AS atendente,
  COUNT(*) AS ciclos_resolvidos
FROM plugin_protocolos_atendimentos pa
LEFT JOIN users u ON u.id = pa.assignee_user_id
WHERE pa.ended_at IS NOT NULL
  AND (to_timestamp(pa.ended_at) AT TIME ZONE 'America/Sao_Paulo')::date = DATE '2026-07-17'
GROUP BY 1
ORDER BY ciclos_resolvidos DESC;
```

Variante "por DONO do protocolo" (fechamentos da entidade Protocolo):

```sql
SELECT
  COALESCE(u.name, '(sem atendente)') AS atendente,
  COUNT(*) AS protocolos_fechados
FROM plugin_protocolos_protocolos p
LEFT JOIN users u ON u.id = p.assignee_user_id
WHERE p.status = 'fechado' AND p.closed_at IS NOT NULL
  AND (to_timestamp(p.closed_at) AT TIME ZONE 'America/Sao_Paulo')::date = DATE '2026-07-17'
GROUP BY 1
ORDER BY protocolos_fechados DESC;
```

### E.4 Recorte por IA (protocolos IA vs humano vs misto) — **PARCIAL** (join cross-plugin)

Intenção: classificar cada protocolo pela composição das respostas das conversas dos seus ciclos. Herda a convenção da receita C sobre `messages`.

```sql
WITH proto_conv AS (   -- conversas de cada protocolo (via ciclos)
  SELECT DISTINCT pa.protocolo_id, pa.conversation_id
  FROM plugin_protocolos_atendimentos pa
  WHERE pa.conversation_id IS NOT NULL
),
proto_ia AS (
  SELECT
    pc.protocolo_id,
    bool_or(m.role='assistant' AND m.agent_key IS NOT NULL)                                 AS tem_ia,
    bool_or(m.role='assistant' AND m.status='operator' AND m.sent_by_user_id IS NOT NULL)   AS tem_humano
  FROM proto_conv pc
  JOIN messages m ON m.conversation_id = pc.conversation_id
  GROUP BY pc.protocolo_id
)
SELECT
  CASE
    WHEN pi.tem_ia AND NOT pi.tem_humano THEN 'IA-only'
    WHEN pi.tem_humano AND NOT pi.tem_ia THEN 'Humano-only'
    WHEN pi.tem_ia AND pi.tem_humano     THEN 'Misto'
    ELSE 'Sem resposta'
  END AS categoria,
  COUNT(*) AS protocolos
FROM plugin_protocolos_protocolos p
LEFT JOIN proto_ia pi ON pi.protocolo_id = p.id
WHERE p.status = 'fechado' AND p.closed_at IS NOT NULL
  AND (to_timestamp(p.closed_at) AT TIME ZONE 'America/Sao_Paulo')::date = DATE '2026-07-17'
GROUP BY 1
ORDER BY protocolos DESC;
```

> ⚠️ **Ressalvas / o que falta**
> - **Reabrir zera `closed_at`** (`reopen_protocolo`, `logic.py:952`) e refechar grava novo — a tabela guarda só o **último** fechamento (mesma volatilidade da receita B). Para contar fechamentos com fidelidade ao longo do tempo, use `plugin_protocolos_avaliacoes` (1 linha por fechamento, com `assignee_user_id`/`created_at`) — **mas ela só existe se o envio de avaliação estiver configurado no fechamento**, então pode faltar linha para alguns fechamentos.
> - **Assignee do protocolo ≠ quem fechou**: no close, se já havia atendente no protocolo, ele é **preservado**; só marca o finalizador quando não havia (`logic.py:910`). Por isso E.3 recomenda o assignee do **ciclo** (`plugin_protocolos_atendimentos`) para "quem resolveu".
> - `assignee_user_id` é nullable nos dois níveis → protocolos/ciclos sem atendente contam só no geral.
> - A API do plugin **não** filtra por `closed_at` nem tem endpoint de agregação — as receitas E.2–E.4 exigem SQL direto (feito aqui).
> - O recorte por IA (E.4) herda todas as ressalvas da receita C (echo não conta como humano; execuções podadas se optar pelo caminho `executions`).

---

## F. Novos contatos iniciados por atendente (regra 15/30 dias) — **PARCIAL**

Intenção: identificar quando **um atendente humano** foi atrás do cliente (proativo/outbound) E isso configura um "novo contato" por re-engajamento. Definição canônica:

- **Atendente humano iniciou** = `atendimentos.origin IN ('outbound','manual')` E a **primeira** mensagem `role='assistant'` do atendimento tem `sent_by_user_id IS NOT NULL` (isola humano da IA — a IA tem `sent_by_user_id=NULL` + `agent_key`). O atendente = esse `sent_by_user_id`; o quando = o `ts` dessa mensagem.
- **"Novo contato" por re-engajamento** = o outbound do atendente ocorre quando **(o contato NUNCA teve inbound antes)** OU **(≥ N dias desde a última `role='user'` do contato)**. `N` configurável, **default 30** (documente também **15**).

Query completa, com o **gap por contato** (detalhe por atendimento outbound):

```sql
WITH params AS (
  SELECT 30::int AS n_dias                     -- troque para 15 conforme a política
),
primeiro_outbound AS (                          -- 1ª msg assistant de cada atendimento outbound
  SELECT DISTINCT ON (a.id)
    a.id                AS conversation_id,
    a.contact_id,
    m.ts                AS t_outbound,
    m.sent_by_user_id,
    m.sent_by_name
  FROM atendimentos a
  JOIN messages m
    ON m.conversation_id = a.id
   AND m.role = 'assistant'
  WHERE a.origin IN ('outbound','manual')   -- 'manual' = backfill legado (migration 0034); o gate real do humano é sent_by_user_id abaixo
  ORDER BY a.id, m.ts ASC
),
humano_iniciou AS (                             -- só os iniciados por HUMANO (não IA)
  SELECT * FROM primeiro_outbound
  WHERE sent_by_user_id IS NOT NULL
),
com_gap AS (                                    -- último inbound do contato ANTES do outbound
  SELECT
    h.*,
    ct.created_at AS contato_desde,
    (SELECT MAX(m2.ts)
       FROM messages m2
      WHERE m2.contact_id = h.contact_id
        AND m2.role = 'user'
        AND m2.ts < h.t_outbound)  AS last_inbound_ts
  FROM humano_iniciou h
  JOIN contacts ct ON ct.id = h.contact_id
)
SELECT
  cg.conversation_id,
  cg.contact_id,
  COALESCE(u.name, cg.sent_by_name)                                   AS atendente,
  (to_timestamp(cg.t_outbound) AT TIME ZONE 'America/Sao_Paulo')::date AS dia_outbound,
  cg.last_inbound_ts,
  CASE WHEN cg.last_inbound_ts IS NULL
       THEN NULL
       ELSE round((cg.t_outbound - cg.last_inbound_ts) / 86400.0, 1)
  END                                                                  AS gap_dias,
  CASE
    WHEN cg.last_inbound_ts IS NULL                                              THEN 'nunca_teve_inbound'
    WHEN (cg.t_outbound - cg.last_inbound_ts) >= (SELECT n_dias FROM params) * 86400 THEN 'reengajamento'
    ELSE 'continuacao'
  END                                                                  AS classificacao
FROM com_gap cg
LEFT JOIN users u ON u.id = cg.sent_by_user_id
WHERE (to_timestamp(cg.t_outbound) AT TIME ZONE 'America/Sao_Paulo')::date = DATE '2026-07-17'
ORDER BY dia_outbound, atendente;
```

Agregado "novos contatos iniciados por atendente no dia" (conta só `nunca_teve_inbound` + `reengajamento`):

```sql
-- envolva a query acima como CTE `detalhe` e agregue:
SELECT
  dia_outbound,
  atendente,
  COUNT(*) FILTER (WHERE classificacao IN ('nunca_teve_inbound','reengajamento')) AS novos_contatos,
  COUNT(*)                                                                        AS outbounds_totais
FROM detalhe
GROUP BY dia_outbound, atendente
ORDER BY dia_outbound, novos_contatos DESC;
```

> ⚠️ **Ressalvas / o que falta**
> - `origin IN ('outbound','manual')` **conflaciona operador humano E IA** — por isso o filtro `sent_by_user_id IS NOT NULL` na 1ª assistant é obrigatório para isolar o humano.
> - A regra 15/30 dias **não existe** em lugar nenhum (sem coluna/config/tabela) — esta é uma query **calculada**, não um fato materializado. `N` é parâmetro do CTE.
> - Primitivas usadas: `contacts.created_at` (1º contato), `messages.ts WHERE role='user'` (último inbound). Se quiser ancorar "novo" em `contacts.created_at` em vez do gap de inbound, troque a subquery `last_inbound_ts` por comparação com `contato_desde`.
> - Linhas legadas: a migration `0034` converteu `origin IS NULL → 'manual'`, e a `0051` só reconverte `manual→inbound` quando a 1ª role real é `user`. Por isso o filtro inclui `'manual'` (senão perde outbound legado). O valor `imported` **não** é escrito por nenhum código de runtime (valor de schema sem dado).
> - Materializar isto como fato de 1ª classe (uma tabela/coluna de "novo contato iniciado por atendente") seria instrumentação — **doc 04**.

---

## G. Tempo de 1ª resposta e latência de IA — **PARCIAL**

Intenção: quanto o cliente esperou pela 1ª resposta (IA e humano separadamente) e quanto a IA "pensou". Não há coluna de 1ª resposta (`atendimentos.waiting_since` é **coluna morta**, nunca escrita) — deriva-se de `messages.ts`.

### G.1 1ª resposta ao cliente (IA e humano) por atendimento

```sql
WITH anchor AS (                                 -- 1º inbound do cliente na conversa
  SELECT conversation_id, MIN(ts) AS t_user
  FROM messages
  WHERE role = 'user' AND conversation_id IS NOT NULL
  GROUP BY conversation_id
),
first_ia AS (
  SELECT m.conversation_id, MIN(m.ts) AS t_ia
  FROM messages m
  JOIN anchor a ON a.conversation_id = m.conversation_id
  WHERE m.role = 'assistant' AND m.agent_key IS NOT NULL
    AND m.status IS DISTINCT FROM 'failed'
    AND m.ts >= a.t_user
  GROUP BY m.conversation_id
),
first_human AS (
  SELECT m.conversation_id, MIN(m.ts) AS t_human
  FROM messages m
  JOIN anchor a ON a.conversation_id = m.conversation_id
  WHERE m.role = 'assistant' AND m.status = 'operator' AND m.sent_by_user_id IS NOT NULL
    AND m.ts >= a.t_user
  GROUP BY m.conversation_id
)
SELECT
  a.conversation_id,
  (fi.t_ia    - a.t_user) AS resp_ia_seg,
  (fh.t_human - a.t_user) AS resp_humano_seg
FROM anchor a
LEFT JOIN first_ia    fi ON fi.conversation_id = a.conversation_id
LEFT JOIN first_human fh ON fh.conversation_id = a.conversation_id;
```

### G.2 Medianas do dia (IA e humano)

```sql
-- envolva G.1 como CTE `fr` e cruze com atendimentos para bucketizar por dia de abertura:
SELECT
  (to_timestamp(a.opened_at) AT TIME ZONE 'America/Sao_Paulo')::date AS dia,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY fr.resp_ia_seg)     AS mediana_ia_seg,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY fr.resp_humano_seg) AS mediana_humano_seg,
  COUNT(*) FILTER (WHERE fr.resp_humano_seg IS NOT NULL)          AS convs_com_resp_humana
FROM atendimentos a
JOIN fr ON fr.conversation_id = a.id
WHERE (to_timestamp(a.opened_at) AT TIME ZONE 'America/Sao_Paulo')::date = DATE '2026-07-17'
GROUP BY 1;
```

### G.3 Latência de compute da IA (via `executions`)

```sql
SELECT
  (to_timestamp(started_at) AT TIME ZONE 'America/Sao_Paulo')::date AS dia,
  agent_key,
  COUNT(*)                                                             AS turnos,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY completed_at - started_at) AS mediana_seg,
  AVG(completed_at - started_at)                                        AS media_seg
FROM executions
WHERE has_ai = 1 AND status = 'completed' AND completed_at IS NOT NULL
  AND (to_timestamp(started_at) AT TIME ZONE 'America/Sao_Paulo')::date = DATE '2026-07-17'
GROUP BY 1, 2
ORDER BY 1, turnos DESC;
```

> ⚠️ **Ressalvas / o que falta**
> - **Latência de entrega/leitura é irrecuperável**: `status` é sobrescrito no lugar (`sent → delivered → read`); não há `delivered_at`/`read_at`. Só o `ts` de criação existe por mensagem. G.1 mede tempo até a resposta **existir**, não até ser entregue/lida.
> - `atendimentos.waiting_since` é declarada mas **nunca escrita** — não use.
> - "1ª resposta humana" exige `sent_by_user_id IS NOT NULL` para excluir **echo** (operador no próprio celular deixa `sent_by_user_id=NULL`).
> - G.3 (executions) mede só o **compute** do turno da IA, não o tempo de fila/batch (o webhook acumula por `message_batch_delay`). Execuções são **podadas** por idade/contagem → G.3 não cobre histórico longo; G.1/G.2 (sobre `messages`) sobrevivem.
> - Coluna limpa de 1ª resposta (`first_response_at`) e um `waiting_since` vivo seriam instrumentação — **doc 04**.

---

## H. Diligência / força de vontade do vendedor — **PARCIAL**

Intenção: medir esforço do atendente humano. Sem métrica pré-agregada — tudo deriva de `messages` (envios de operador via painel).

### H.1 Volume, atendimentos tocados e janela ativa por atendente/dia

```sql
SELECT
  (to_timestamp(m.ts) AT TIME ZONE 'America/Sao_Paulo')::date AS dia,
  u.name                                             AS atendente,
  COUNT(*)                                           AS msgs_enviadas,
  COUNT(DISTINCT m.conversation_id)                  AS atendimentos_tocados,
  MIN(to_timestamp(m.ts) AT TIME ZONE 'America/Sao_Paulo') AS primeiro_envio,
  MAX(to_timestamp(m.ts) AT TIME ZONE 'America/Sao_Paulo') AS ultimo_envio,
  COUNT(DISTINCT extract(hour FROM to_timestamp(m.ts) AT TIME ZONE 'America/Sao_Paulo')) AS horas_ativas
FROM messages m
JOIN users u ON u.id = m.sent_by_user_id
WHERE m.role = 'assistant' AND m.status = 'operator' AND m.sent_by_user_id IS NOT NULL
  AND (to_timestamp(m.ts) AT TIME ZONE 'America/Sao_Paulo')::date = DATE '2026-07-17'
GROUP BY 1, 2
ORDER BY 1, msgs_enviadas DESC;
```

### H.2 Mediana da 1ª resposta humana, por atendente

```sql
WITH anchor AS (
  SELECT conversation_id, MIN(ts) AS t_user
  FROM messages
  WHERE role = 'user' AND conversation_id IS NOT NULL
  GROUP BY conversation_id
),
first_human AS (
  SELECT DISTINCT ON (m.conversation_id)
    m.conversation_id, m.ts AS t_human, m.sent_by_user_id
  FROM messages m
  JOIN anchor a ON a.conversation_id = m.conversation_id
  WHERE m.role = 'assistant' AND m.status = 'operator' AND m.sent_by_user_id IS NOT NULL
    AND m.ts >= a.t_user
  ORDER BY m.conversation_id, m.ts ASC
)
SELECT
  u.name AS atendente,
  COUNT(*) AS respostas,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY fh.t_human - a.t_user) AS mediana_1a_resp_seg
FROM first_human fh
JOIN anchor a ON a.conversation_id = fh.conversation_id
JOIN users  u ON u.id = fh.sent_by_user_id
GROUP BY u.name
ORDER BY mediana_1a_resp_seg;
```

### H.3 Histograma de horário ativo (envios por hora paulista)

```sql
SELECT
  u.name AS atendente,
  extract(hour FROM to_timestamp(m.ts) AT TIME ZONE 'America/Sao_Paulo')::int AS hora,
  COUNT(*) AS envios
FROM messages m
JOIN users u ON u.id = m.sent_by_user_id
WHERE m.role = 'assistant' AND m.status = 'operator' AND m.sent_by_user_id IS NOT NULL
GROUP BY 1, 2
ORDER BY 1, 2;
```

> ⚠️ **Ressalvas / o que falta**
> - Só conta **envios pelo painel** (`sent_by_user_id NOT NULL`). Envios por **echo** (celular do operador) são anônimos — não entram por atendente. Notas privadas (`role='private_note'`) e cards não contam.
> - "Atendimentos tocados" usa `COUNT(DISTINCT conversation_id)`; mensagens de operador com `conversation_id NULL` (linhas legadas) escapam.
> - "Quem fechou/resolveu" continua não confiável no core (assignee zerado no close) — para atribuir resoluções use a receita B (workaround) ou os protocolos (receita E.3).
> - Tudo em `America/Sao_Paulo`; sem filtro de horário comercial (adicione `WHERE extract(hour ...) BETWEEN 8 AND 18` se necessário).

---

## I. Estratégias que convertem / padrões de conversa — **BLOQUEADO** (sem sinal de conversão)

Intenção: descobrir o que "dá venda" e padrões como "cliente some depois do preço". **Não existe coluna de resultado/conversão** no schema — sem a variável-alvo, correlação de estratégia é heurística sem chão.

### Passo 1 (pré-requisito): firmar o sinal de conversão

Recomendação: uma **etiqueta de conversa** `venda` (tabelas nativas `atendimento_labels` + `atendimento_label_links`), aplicada pela equipe quando o atendimento vira venda. Opcionalmente `atendimentos.custom_attributes` para `produto`/`valor`.

```sql
-- atendimentos marcados como venda (ground-truth, quando a etiqueta existir):
SELECT a.id AS conversation_id, a.contact_id
FROM atendimentos a
JOIN atendimento_label_links ll ON ll.conversation_id = a.id
JOIN atendimento_labels l       ON l.id = ll.label_id AND l.name = 'venda';
```

### Passo 2 (com o sinal): correlacionar padrões com o desfecho

Exemplo — comparar tempo de resposta e volume de mensagens entre convertidas e não convertidas (junte com a CTE `fr` da receita G.1):

```sql
WITH venda AS (
  SELECT ll.conversation_id
  FROM atendimento_label_links ll
  JOIN atendimento_labels l ON l.id = ll.label_id AND l.name = 'venda'
),
vol AS (
  SELECT conversation_id, COUNT(*) AS msgs
  FROM messages
  WHERE conversation_id IS NOT NULL AND role IN ('user','assistant')
  GROUP BY conversation_id
)
SELECT
  (v.conversation_id IS NOT NULL) AS converteu,
  COUNT(*)                        AS atendimentos,
  AVG(vol.msgs)                   AS media_msgs,
  percentile_cont(0.5) WITHIN GROUP (ORDER BY fr.resp_humano_seg) AS mediana_1a_resp_humana_seg
FROM atendimentos a
LEFT JOIN venda v ON v.conversation_id = a.id
LEFT JOIN vol      ON vol.conversation_id = a.id
LEFT JOIN fr       ON fr.conversation_id = a.id      -- fr = CTE da receita G.1
GROUP BY 1;
```

### Passo 3: o que a IA agêntica faria (lendo `messages.content`)

Sem sinal firmado, a análise de padrões é **texto-derivada** e deve ser marcada como PARCIAL/heurística:

- **"Cliente some depois do preço"**: para cada atendimento, achar a última mensagem antes de um silêncio prolongado do cliente (gap grande entre a última `role='assistant'` e a próxima `role='user'`, ou ausência de nova `role='user'`), e classificar se essa última mensagem outbound continha preço (regex tipo `R\$\s*\d` sobre `content`). Ex. de detecção do sinal de preço:

  ```sql
  SELECT id, conversation_id, ts, content
  FROM messages
  WHERE role = 'assistant'
    AND content ~ 'R\$\s*[0-9]';   -- menção de preço na resposta
  ```

  A IA então correlaciona "respondeu preço → cliente não voltou em N dias" contra o ground-truth `venda`.
- **Etapas/estratégia**: a IA lê `messages.content` em ordem de `ts` por `conversation_id`, rotula turnos (saudação, qualificação, oferta, objeção, preço, fechamento) e mede quais sequências correlacionam com a etiqueta `venda`.

> ⚠️ **Ressalvas / o que falta**
> - **Bloqueado sem a variável-alvo**: enquanto não houver etiqueta `venda` (ou atributo/campo equivalente) preenchida, "estratégia que converte" não tem desfecho contra o qual correlacionar. Protocolo-fechado ≠ venda; `order`/`product` chegam como `media_extras` efêmeros, não persistidos estruturados.
> - Depois de firmado o sinal, o Passo 2/3 vira PARCIAL (heurístico sobre texto), não PRONTO.
> - Detecção de preço/etapa por regex é aproximada; a leitura semântica de `messages.content` é trabalho da IA agêntica (`analises`), não de SQL puro.
> - Instrumentação recomendada (evento/atributo de conversão de 1ª classe): **doc 04**.

---

## Contexto: automações de relatório no Telegram (fora de escopo)

**Só contexto — não construir aqui.** Automações externas rodam periodicamente lendo este banco e empurram relatórios diários a grupos do Telegram. Elas são, na prática, as receitas A–F agrupadas por atendente / geral / com recorte de IA, ancoradas no dia paulista. O que cada relatório diário consumiria:

| Relatório diário (Telegram) | Receita(s) deste cookbook | Grão | Selo herdado |
|---|---|---|---|
| Atendimentos abertos no dia (geral + por atendente) | A.1 / A.2 | geral + atendente | PRONTO / PARCIAL (assignee ≠ quem abriu) |
| Atendimentos fechados no dia (geral + por atendente) | B.1 / B.2 | geral + atendente | PARCIAL (volatilidade) / BLOQUEADO no core |
| Split IA vs humano vs misto | C | geral (recorte IA) | PRONTO |
| Contagem de conversas nativas | D | dia + canal | PRONTO |
| Protocolos abertos/fechados no dia (geral + por atendente, recorte IA) | E.1–E.4 | geral + atendente + IA | PRONTO/PARCIAL |
| Novos contatos iniciados por atendente (regra 15/30 dias) | F | atendente | PARCIAL (calculado) |

Notas para quem construir as automações externas (fora deste doc):

- **Fonte de dados**: o mesmo Postgres apontado por `DATABASE_URL`. Para automação externa, o caminho seguro é um **role somente-leitura** (`SELECT`) ou o gateway HMAC do padrão `melhorias` (ver decision-brief §3) — nunca credencial de escrita.
- **Timezone**: todo bucket "no dia" ancora em `America/Sao_Paulo` (as receitas já fazem isso). "Hoje" = `CURRENT_DATE` no fuso do servidor pode divergir do dia paulista — prefira calcular o dia-alvo explicitamente em SP.
- **Limitações que o relatório precisa comunicar** (não são bugs a corrigir na automação): "fechados por atendente" no core é reconstruído do texto do card (B.2, sem `user_id`); "abertos por atendente" é por assignee atual, não por quem abriu (A.2); a regra 15/30 dias é calculada em query (F), não armazenada. As soluções limpas dessas três lacunas estão na **doc 04 (instrumentação recomendada)**.
