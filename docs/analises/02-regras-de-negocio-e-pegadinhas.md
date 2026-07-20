# WhatsBot — Regras de negócio e pegadinhas para análises

> Documento de **contexto para uma IA analista** (e para o dev). Aqui estão as pegadinhas semânticas do banco que fazem uma conta certa virar uma conta errada. Cada seção tem **Regra**, **Como consultar** e **⚠️ Implicação para análise**.
>
> Convenções deste doc (CANON):
> - Prosa em PT-BR; nomes de tabela/coluna em inglês (como no banco).
> - SQL é **Postgres**. Timestamps são **epoch float em UTC** — todo bucket "no dia/hoje" ancora em `America/Sao_Paulo` com `(to_timestamp(ts) AT TIME ZONE 'America/Sao_Paulo')::date`.
> - Glossário: **Atendimento** = tabela `atendimentos` (a `conversations` do Chatwoot renomeada; alias Python `conversations = atendimentos`, `db/tables.py:853`). **Protocolo** = ticket do plugin `protocolos` (`plugin_protocolos_protocolos`), NÃO confundir com `atendimentos`. **Atendente** = linha em `users`. **IA/agente** = `messages.agent_key IS NOT NULL`.

---

## 1. `status` (open/closed) vs `is_archived` — ortogonais (arquivado ≠ fechado)

**Regra.** O status do atendimento tem **exatamente dois valores**: `open` | `closed` (`atendimentos.status`, `db/tables.py:435`; enum de filtro `frozenset({"open","closed"})` em `db/filters/registry.py:39-41`). NÃO existe `resolved`/`pending`/`snoozed`/`reopened` persistido (a chave `"reopened"` em `conversation_service.py:63-67` é reservada e nunca é gravada). O **arquivamento** é uma flag **separada e independente**: `atendimentos.is_archived` (Integer 0/1, `db/tables.py:436` — "ortogonal (P10)"), gravada só por `set_archived` (`conversation_repo.py:648-649`), que **não toca em `status`**. Um atendimento pode estar `open`+arquivado, `closed`+arquivado, `open`+não-arquivado, `closed`+não-arquivado — as quatro combinações são válidas.

Nota: `is_archived` existe em DUAS tabelas — `contacts.is_archived` (`db/tables.py:73`, flag chat-level legada, atualizada pelo webhook) e `atendimentos.is_archived` (`db/tables.py:436`, por-conversa, plano 54). Para análise **por atendimento**, use `atendimentos.is_archived`.

**Como consultar.**
```sql
-- Abertos (não confundir com "não arquivados")
SELECT count(*) FROM atendimentos WHERE status = 'open';

-- Arquivados que continuam ABERTOS (existem!)
SELECT count(*) FROM atendimentos WHERE is_archived = 1 AND status = 'open';

-- A matriz correta status × arquivo
SELECT status, is_archived, count(*)
FROM atendimentos
GROUP BY status, is_archived;
```

**⚠️ Implicação para análise.** NUNCA trate "arquivado" como "fechado", nem "não-arquivado" como "aberto". São dimensões cruzadas. Se o relatório é "atendimentos abertos/fechados", filtre por `status` e ignore `is_archived` (a menos que a pergunta seja explicitamente sobre arquivo). Não há coluna de timestamp de arquivamento — não existe `archived_at`; "arquivado no dia" **não é computável**.

---

## 2. `resolved_at` é volátil e `assignee_user_id` é zerado no fechamento — o buraco do "fechado por atendente"

**Regra.** O ÚNICO lugar que grava as colunas derivadas de status é `conversation_repo.set_status` (`conversation_repo.py:623-645`). No fechamento (`status='closed'`) ele grava **três** coisas: `resolved_at = now()`, **`assignee_user_id = NULL`** e **`active_agent_key = NULL`** (`conversation_repo.py:639-642`). Na reabertura (`status='open'`) ele grava **`resolved_at = NULL`** (`conversation_repo.py:643-644`). Consequências:
- Um atendimento **fechado perde o dono**: `assignee_user_id` volta a NULL. Não existe `closed_by_user_id`.
- `resolved_at` guarda **só o último fechamento**. Fechou dia 1 → reabriu → fechou dia 3: sobra só o dia 3. Fechou → reabriu (e ficou aberto): `resolved_at = NULL`, como se nunca tivesse fechado.
- Não há tabela de histórico de transições. A única trilha durável por transição é o card `messages(role='conversation_event')` (ver §5) — mas o ator está só no texto PT-BR, sem `user_id`.
- `audit_log` **NÃO** registra ciclo de vida de conversa (não há `conversation.close`/`assign` no catálogo `db/audit_actions.py:30-58`).

**Como consultar.**
```sql
-- "Fechados no dia" (GERAL) — funciona, mas SUBCONTA (reabertos somem, closes intermediários perdidos)
SELECT count(*)
FROM atendimentos
WHERE status = 'closed'
  AND resolved_at IS NOT NULL
  AND (to_timestamp(resolved_at) AT TIME ZONE 'America/Sao_Paulo')::date = current_date;

-- "Fechados no dia POR ATENDENTE" a partir de atendimentos → TUDO cai em NULL (não faça):
-- SELECT assignee_user_id, count(*) ... WHERE status='closed'  ❌ (assignee foi zerado no close)

-- Reconstrução do "quem fechou": trilha conversation_event (ator só no texto PT-BR)
SELECT conversation_id, content, ts
FROM messages
WHERE role = 'conversation_event'
  AND content LIKE '✅%'   -- status_closed: '✅ <nome> resolveu…' OU '✅ Conversa resolvida.' (impessoal/automático, sem ator). Ver §5
  AND (to_timestamp(ts) AT TIME ZONE 'America/Sao_Paulo')::date = current_date;
```

**⚠️ Implicação para análise.** "**Atendimentos fechados no dia POR ATENDENTE**" **NÃO é computável** do jeito confiável a partir de `atendimentos` — o dono é apagado no fechamento. As saídas parciais são: (a) parse do texto PT-BR do card `conversation_event status_closed` (só o nome de exibição, sem `user_id`); ou (b) correlação por `ts` com `audit_log` (que também não tem o evento de close). "**Fechados no dia GERAL**" via `resolved_at` **subconta** (reabertos viram `resolved_at=NULL`). Para "quem fechou" confiável hoje, use **Protocolos** (§10), não atendimentos. Marque qualquer métrica de "fechado por atendente (core)" como **PARCIAL/BLOQUEADA**.

---

## 3. `origin` (inbound/outbound/manual/imported) — e a mistura operador+IA em `outbound`

**Regra.** `atendimentos.origin` (`db/tables.py:442-447`, NULLABLE) é o único sinal de "quem iniciou": `inbound` = cliente iniciou · `outbound`/`manual` = operador OU IA · `imported` = import de chats (⚠️ **valor morto**: nenhum código de runtime escreve `imported`) · NULL = tratado como não-inbound (após a migration `0034`, legado virou `manual`, não NULL). Ou seja, na prática só verá `inbound`, `outbound` e `manual`. É carimbado no CREATE pelo **role da 1ª mensagem** que materializa o atendimento: `origin = "inbound" if role == "user" else "outbound"` (`agent/memory.py:307`). A migration `0051` re-derivou o histórico do mesmo jeito (`assistant` → outbound). O problema: **`outbound` funde o operador humano E a IA** — ambos produzem uma primeira mensagem `role='assistant'`, e `origin` não os separa.

**Como consultar.**
```sql
-- Distribuição de origem
SELECT coalesce(origin, '(null)') AS origin, count(*)
FROM atendimentos
GROUP BY origin;

-- "Iniciado por HUMANO" exige descer ao grão de mensagem (a IA tem sent_by_user_id NULL):
SELECT a.id
FROM atendimentos a
WHERE a.origin IN ('outbound','manual')   -- inclui 'manual' (backfill legado 0034); gate real do humano = sent_by_user_id
  AND EXISTS (
    SELECT 1 FROM messages m
    WHERE m.conversation_id = a.id
      AND m.role = 'assistant'
      AND m.sent_by_user_id IS NOT NULL   -- humano; a IA usa agent_key, sent_by_user_id NULL
      AND m.ts = (SELECT min(ts) FROM messages m2
                  WHERE m2.conversation_id = a.id AND m2.role = 'assistant')
  );
```

**⚠️ Implicação para análise.** `origin='outbound'` **não** é "iniciado por atendente" — pode ser a IA. Para isolar humano, cheque a PRIMEIRA mensagem `role='assistant'` do atendimento e exija `sent_by_user_id IS NOT NULL` (discriminador canônico, §4). Ver §7 para a definição completa de "iniciado por atendente" + regra de re-engajamento.

---

## 4. Identidade do remetente de uma mensagem — discriminadores CANÔNICOS

**Regra.** NÃO existe coluna `direction` nem `source` em `messages` (`db/tables.py:108-149`). O remetente é derivado de `role` + `status` + `agent_key` + `sent_by_user_id`. Discriminadores canônicos (do banco):

| Quem | Discriminador |
|---|---|
| **Cliente** | `role='user'` |
| **IA** | `role='assistant' AND agent_key IS NOT NULL` (tipicamente `status='sent'`, `execution_id` setado). `agent_key` é o discriminador confiável da IA — só é gravado em linhas produzidas pela IA (`db/tables.py:135-139`) |
| **Atendente humano (envio pelo painel)** | `role='assistant' AND status='operator' AND sent_by_user_id IS NOT NULL` (`sent_by_name` = snapshot do nome). `sent_by_user_id` é o ÚNICO lugar com o atendente específico |
| **Echo (operador digitou no próprio celular)** | `role='assistant' AND status='operator' AND sent_by_user_id IS NULL AND agent_key IS NULL` |

Ou seja: `status='operator'` cobre **painel E echo**; o que os separa é `sent_by_name`/`sent_by_user_id` (echo deixa NULL — `message_ingest_service.py:298-301`). `agent_key` isola a IA de forma limpa.

**Como consultar.**
```sql
SELECT
  count(*) FILTER (WHERE role='user')                                              AS cliente,
  count(*) FILTER (WHERE role='assistant' AND agent_key IS NOT NULL)               AS ia,
  count(*) FILTER (WHERE role='assistant' AND status='operator'
                        AND sent_by_user_id IS NOT NULL)                           AS atendente_painel,
  count(*) FILTER (WHERE role='assistant' AND status='operator'
                        AND sent_by_user_id IS NULL AND agent_key IS NULL)         AS echo_celular
FROM messages;
```

**⚠️ Implicação para análise.**
- O enum `source` (`ai`/`operator`/`private_ai`/`retry`/`echo`/`template`) existe **só no event-bus**, NÃO é persistido. `private_ai` é **indistinguível** de `ai` no banco (ambos `role='assistant'`, `agent_key` setado); `echo` só se separa de `operator` pela ausência de `sent_by_name`.
- Delivery status (`sent → delivered → read`) é sobrescrito **no lugar** (`message_repo.py:316-368`) — `ts` é o único timestamp por mensagem; não há `delivered_at`/`read_at`. Latência de entrega/leitura é **irrecuperável**; só first-response latency é computável (§ tempos derivam de `ts` + role).
- Para "volume de mensagens do atendente X": `count(*) WHERE role='assistant' AND status='operator' AND sent_by_user_id = X`.

---

## 5. Roles painel-only e a trilha `conversation_event`

**Regra.** Alguns roles de `messages` **nunca vão ao WhatsApp** — renderizam como card centralizado no painel: `tool_call`, `system_notice`, `transcription`, `private_note`, `error`, `conversation_event`, `system`. Eles **não são mensagens reais do WhatsApp** — para análise, exclua-os de qualquer contagem de "mensagens trocadas". (Precisão: a blacklist de contexto do LLM em `message_repo.py:156-157,188-189` cobre **6** desses roles — `transcription`, `tool_call`, `system_notice`, `conversation_event`, `system`, `error` — e **não** inclui `private_note`, que pode entrar no contexto como "cutucada" do operador.) O `conversation_event` também sai do preview da sidebar e não conta como não-lida. O `conversation_event` (`ROLE` em `system_notices.py:33`) é a **trilha do ciclo de vida do atendimento**, gravado por `emit_conversation_notice` via `message_repo.add(contact_id, 'conversation_event', content, conversation_id=…)` (`system_notices.py:464`).

Catálogo de `event_type` (registrado em `_seed_core_notices`, `system_notices.py:273-300`), agrupado por **grupo de config** (gate global default ON — grupo OFF ⇒ o card **não é gravado**, `system_notices.py:448-449`):

| Grupo (config key `system_notice_<grupo>`) | event_type |
|---|---|
| `status` | `created`, `status_closed`, `status_open`, `status_reopened_auto`, `status_reopened_auto_agent`, `archived`, `unarchived` |
| `assignment` | `assigned`, `assigned_me`, `unassigned` |
| `tags` | `tag_added`, `tag_removed` |
| `conv_labels` | `conv_label_added`, `conv_label_removed` |
| `ai` | `ai_on`, `ai_off`, `ai_takeover`, `agent_changed`, `attribute_set` |

O **ator NÃO é dado estruturado**: `emit_conversation_notice` grava o card **sem** `sent_by_user_id`, `sent_by_name` ou `agent_key` (`system_notices.py:464`). O autor existe **só dentro do texto PT-BR** do `content` (nome de exibição, ex.: `"🧑‍💼 João assumiu a conversa."`), nunca o `user_id`. Ações automáticas passam `actor=None` → aparecem como `SISTEMA` (`system_notices.py:37-38`). `ai_takeover` é dedupado 1×/conversa (`has_event`, `system_notices.py:354-375`).

**Como consultar.**
```sql
-- Trilha completa de um atendimento (histórico de close/reopen que resolved_at perde)
SELECT ts, content
FROM messages
WHERE role = 'conversation_event' AND conversation_id = :conv_id
ORDER BY ts;

-- Fechamentos "no dia" pela trilha (ator só no texto — sem user_id estruturado)
SELECT content, ts
FROM messages
WHERE role = 'conversation_event'
  AND content LIKE '✅%'   -- captura o close impessoal '✅ Conversa resolvida.' também
  AND (to_timestamp(ts) AT TIME ZONE 'America/Sao_Paulo')::date = current_date;
```

**⚠️ Implicação para análise.** (1) Para atribuir um evento a um `user_id`, é preciso **parsear o texto PT-BR** (frágil, só nome) ou **correlacionar por `ts` com `audit_log`**. (2) **Eventos podem FALTAR**: se o grupo de config estiver OFF, o card nunca é gravado — a trilha fica incompleta. Não assuma que a ausência de um `conversation_event` significa que a ação não ocorreu; pode ser o gate desligado. (3) `conversation_event` (e os demais painel-only) devem ser **excluídos** de qualquer contagem de "mensagens trocadas com o cliente".

---

## 6. IA vs humano no nível do atendimento — convenção canônica (derive de messages)

**Regra.** NÃO existe campo autoritativo "resolvido pela IA/humano". A convenção **recomendada** deriva das MESSAGES (mais confiável que `executions`), por `messages.conversation_id`:
- `tem_ia` = EXISTS mensagem `role='assistant' AND agent_key IS NOT NULL`.
- `tem_humano` = EXISTS mensagem `role='assistant' AND status='operator' AND sent_by_user_id IS NOT NULL`.
- Classificação: **IA-only** (tem_ia ∧ ¬tem_humano) · **Humano-only** (tem_humano ∧ ¬tem_ia) · **Misto** (ambos) · **Sem resposta** (nenhum).

`executions.has_ai=1` (`db/tables.py:571`) serve só como **complemento** (tokens/custo), não como split primário.

**Como consultar.**
```sql
SELECT
  CASE
    WHEN tem_ia AND tem_humano THEN 'misto'
    WHEN tem_ia               THEN 'ia_only'
    WHEN tem_humano           THEN 'humano_only'
    ELSE 'sem_resposta'
  END AS classe,
  count(*)
FROM (
  SELECT a.id,
    EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=a.id
            AND m.role='assistant' AND m.agent_key IS NOT NULL) AS tem_ia,
    EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id=a.id
            AND m.role='assistant' AND m.status='operator'
            AND m.sent_by_user_id IS NOT NULL) AS tem_humano
  FROM atendimentos a
) t
GROUP BY 1;
```

**⚠️ Implicação para análise.** Use `executions.has_ai` **só** para tokens/custo, nunca para contar quantos atendimentos a IA "fez" — execuções são podadas (§12), `executions.conversation_id` é nullable em linhas legadas, e turnos só-mídia (transcrição) enviesam `has_ai`. O split IA×humano **não é mutuamente exclusivo**: reporte a 4ª classe **misto** explicitamente. Derive de `messages`, não de `executions`.

---

## 7. Iniciado por atendente + regra 15/30 dias — definição canônica

**Regra.** A regra **não está implementada em lugar nenhum** (grep completo do core + plugins); é **calculada**, não armazenada. Definições recomendadas:
- **Atendente humano iniciou** = `atendimentos.origin IN ('outbound','manual')` **E** a PRIMEIRA mensagem `role='assistant'` do atendimento tem `sent_by_user_id IS NOT NULL` (isola humano da IA — a IA tem `sent_by_user_id=NULL`+`agent_key`). O atendente = esse `sent_by_user_id`; quando = o `ts` dessa mensagem.
- **"Novo contato" por re-engajamento** = o outbound do atendente ocorre quando **(o contato NUNCA teve inbound antes)** OU **(≥ N dias desde a última `role='user'` do contato)**. Primitivas: `contacts.created_at` (1º contato, `db/tables.py:87`), `messages.ts WHERE role='user'` (último inbound — `message_repo.last_inbound_ts`, `message_repo.py:239-264`). **N configurável, default 30** (documente 15 e 30).

**Como consultar.**
```sql
-- Contatos "iniciados por atendente" hoje, aplicando a regra de re-engajamento (N dias)
WITH primeiro_assistant AS (
  SELECT DISTINCT ON (m.conversation_id)
         m.conversation_id, m.sent_by_user_id, m.ts
  FROM messages m
  WHERE m.role = 'assistant'
  ORDER BY m.conversation_id, m.ts
)
SELECT a.id AS atendimento, a.contact_id, p.sent_by_user_id AS atendente, p.ts AS quando
FROM atendimentos a
JOIN primeiro_assistant p ON p.conversation_id = a.id
JOIN contacts c ON c.id = a.contact_id
WHERE a.origin IN ('outbound','manual')   -- inclui 'manual' (backfill legado 0034); gate real do humano = sent_by_user_id
  AND p.sent_by_user_id IS NOT NULL                         -- humano, não IA
  AND (to_timestamp(p.ts) AT TIME ZONE 'America/Sao_Paulo')::date = current_date
  AND (
        -- nunca teve inbound antes do outbound do atendente
        NOT EXISTS (SELECT 1 FROM messages u
                    WHERE u.contact_id = a.contact_id AND u.role='user' AND u.ts < p.ts)
        -- OU ≥ N dias desde o último inbound (N = 30; troque por 15 conforme a política)
        OR p.ts - (SELECT max(u.ts) FROM messages u
                   WHERE u.contact_id = a.contact_id AND u.role='user' AND u.ts < p.ts)
           >= 30 * 86400
      );
```

**⚠️ Implicação para análise.** Nada materializa isto como fato de 1ª classe — é sempre um JOIN calculado. `origin='outbound'` sozinho **não** basta (funde IA). `audit_log` não registra iniciação de conversa/contato. Deixe **N** explícito no relatório (15 vs 30) — o número muda a contagem.

---

## 8. Conversão / "venda que deu certo" — convenção flexível

**Regra.** **NÃO existe** coluna de resultado/conversão em lugar nenhum (nem `won`, nem `sale`, nem status de venda). `order`/`product` chegam como `media_extras` efêmeros (não persistidos estruturados). Protocolo fechado **≠** venda. Convenção recomendada (a firmar com o uso): uma **etiqueta de conversa** `venda` (`atendimento_labels` + `atendimento_label_links`, `db/tables.py:482-499`) como ground-truth, opcionalmente `custom_attributes` da conversa/contato para `produto`/`valor`.

**Como consultar.**
```sql
-- Se a equipe adotar a etiqueta 'venda' nas conversas:
SELECT count(DISTINCT l.conversation_id)
FROM atendimento_label_links l
JOIN atendimento_labels lab ON lab.id = l.label_id
WHERE lab.name = 'venda';
```

**⚠️ Implicação para análise.** Enquanto NÃO houver um sinal firmado (tag, atributo personalizado, ou campo no protocolo), qualquer análise de "estratégia que converte" / "padrão de conversão" está **sem variável-alvo** → marque como **PARCIAL/BLOQUEADA**. A IA analista deve ler QUALQUER sinal que a equipe adotar, mas não inventar conversão a partir de "protocolo fechado" ou "atendimento closed".

---

## 9. Timezone — epoch float UTC → America/Sao_Paulo

**Regra.** TODOS os timestamps (`messages.ts`, `atendimentos.opened_at/resolved_at/last_activity_at/created_at`, `usage.ts`, `executions.started_at/completed_at`, `plugin_protocolos_*.opened_at/closed_at/...`) são **Unix epoch float**, tipo SQL `Float`/`DOUBLE PRECISION` — **não** `TIMESTAMP` (`db/tables.py:11-13,115,448-453`). São **naive UTC**: nenhum metadado de fuso é armazenado.

**Como consultar.**
```sql
-- SEMPRE converta assim ao bucketizar "no dia":
SELECT (to_timestamp(ts) AT TIME ZONE 'America/Sao_Paulo')::date AS dia, count(*)
FROM messages
GROUP BY 1
ORDER BY 1;
```

**⚠️ Implicação para análise.** "Hoje"/"no dia"/"ontem" DEVE ancorar em `America/Sao_Paulo` via `to_timestamp(...) AT TIME ZONE 'America/Sao_Paulo'` — deixe isso **explícito em TODA query de bucket diário**. Comparar `ts` cru com `current_date` (que é do fuso da sessão) ou esquecer a conversão joga eventos da madrugada para o dia errado. Para "diferença em dias" use aritmética de epoch (`(ts2 - ts1) / 86400`), como na regra 15/30 (§7).

---

## 10. Protocolo vs atendimento — falso amigo

**Regra.** Dois conceitos distintos, e um nome que engana:
- **Atendimento (core)** = tabela `atendimentos` (a `conversations` renomeada). Para "quantidade de conversas nativas", conte `atendimentos`.
- **Protocolo** = ticket do plugin `protocolos`, tabela `plugin_protocolos_protocolos` (status próprio `'aberto'`|`'fechado'`, `opened_at`/`closed_at`, `assignee_user_id`). Máx. 1 aberto por contato.
- **FALSO AMIGO:** `plugin_protocolos_atendimentos` **NÃO** é a lista de atendimentos do core. É a tabela de **vínculo/ciclo** que liga uma `conversation` do core a um protocolo — **N linhas por conversa** (uma por ida-e-volta; o unique em `conversation_id` foi dropado, migration `002`). Para contar protocolos use `plugin_protocolos_protocolos`; para contar conversas nativas use `atendimentos`.

Protocolo **reabre** (ciclos): `reopen_protocolo` faz `status='aberto', closed_at=NULL` (`logic.py:951-953`) — igual ao core, o `closed_at` guarda **só o último fechamento**. "Quem fechou": o `assignee_user_id` do PROTOCOLO é o **dono** (preservado no close se já existia — `logic.py:910-917`), não necessariamente quem fechou; para o **executor** da resolução, use o `assignee_user_id` do **ciclo** (`plugin_protocolos_atendimentos.assignee_user_id`, migration `005:7`).

**Como consultar.**
```sql
-- Protocolos abertos no dia (GERAL)
SELECT count(*) FROM plugin_protocolos_protocolos
WHERE status = 'aberto'
  AND (to_timestamp(opened_at) AT TIME ZONE 'America/Sao_Paulo')::date = current_date;

-- Protocolos fechados no dia POR ATENDENTE (dono do protocolo)
SELECT assignee_user_id, count(*)
FROM plugin_protocolos_protocolos
WHERE status = 'fechado' AND closed_at IS NOT NULL
  AND (to_timestamp(closed_at) AT TIME ZONE 'America/Sao_Paulo')::date = current_date
GROUP BY assignee_user_id;

-- "Quem RESOLVEU" (executor do ciclo), mais fiel que o dono do protocolo:
SELECT assignee_user_id, count(*)
FROM plugin_protocolos_atendimentos
WHERE ended_at IS NOT NULL
  AND (to_timestamp(ended_at) AT TIME ZONE 'America/Sao_Paulo')::date = current_date
GROUP BY assignee_user_id;
```

**⚠️ Implicação para análise.** (1) Diferente do core, **protocolo tem `assignee_user_id` que SOBREVIVE ao fechamento** (não é zerado) — por isso "fechado por atendente" é confiável em protocolos e não em atendimentos. (2) Mesma pegadinha de `resolved_at`/`closed_at`: reabrir zera; só o último fechamento fica. Para histórico fiel de fechamentos, `plugin_protocolos_avaliacoes` grava 1 linha por fechamento (`015`) — mas só se o envio de avaliação estiver configurado. (3) NÃO agregue por `plugin_protocolos_atendimentos` achando que é "atendimentos do core" — é ciclo, com múltiplas linhas por conversa.

---

## 11. `usage` não liga conversa/agente (só `contact_id`) — custo por agente vem de `executions`

**Regra.** `usage` é uma linha por chamada de LLM cobrável (`db/tables.py:152-166`). O **único** vínculo relacional é `usage.contact_id` — **não há** `conversation_id`, `execution_id` nem `agent_key`. Ele mantém `call_type` (`text`/`audio`/`image`/`document`), `model`, tokens e `cost_usd` por chamada. Já `executions` (`db/tables.py:539-575`) tem `agent_key`, `total_tokens`, `total_cost_usd`, `conversation_id` **por execução** — mas como **agregado do turno**, perdendo o breakdown por modelo/`call_type`.

**Como consultar.**
```sql
-- Tokens/custo por dia (GERAL) — via usage (tem model e call_type, não tem agente)
SELECT (to_timestamp(ts) AT TIME ZONE 'America/Sao_Paulo')::date AS dia,
       call_type, model, sum(total_tokens) AS tokens, sum(cost_usd) AS custo
FROM usage
GROUP BY 1,2,3 ORDER BY 1;

-- Tokens/custo POR AGENTE — obrigatoriamente via executions (usage não tem agent_key)
SELECT agent_key,
       sum(total_tokens) AS tokens, sum(total_cost_usd) AS custo
FROM executions
GROUP BY agent_key;
```

**⚠️ Implicação para análise.** "Custo/tokens **por agente**" **só** sai de `executions` (usage não tem `agent_key`). "Custo **por modelo/call_type**" **só** sai de `usage` (executions agrega e perde o modelo). As duas fontes **não têm chave em comum** e **podem divergir** — não as junte por join. Cuidados extras: `executions.agent_key` é só o agente **FINAL** do turno (turnos router→spoke atribuem tudo ao último; `execution_steps.agent_key` tem o por-hop, mas **sem** tokens no step); e `cost_usd` é congelado no write a partir do pricing vivo — vira **`0.0`** se o modelo não estava no cache `/models` (`server/routes/usage.py:44,49`), sem re-precificação. Custos históricos podem estar subnotificados (zeros).

---

## 12. Execuções são podadas (retenção) — não conte "quantos atendimentos a IA fez" por executions

**Regra.** `executions` (e `execution_steps`, que cascateiam junto) são **podadas por contagem e idade** (`execution_repo.prune`/`delete_older_than`, `execution_repo.py:392-409`; `agent/execution.py:190-214`). `usage` não tem poda no código visto — mas `usage` não tem dimensão de conversa/agente (§11). O detalhe de step (tool calls, routing, `llm_context`) é **lossy** no longo prazo; só os agregados de `executions` e o `usage` sobrevivem, cada um com sua limitação.

**Como consultar.**
```sql
-- ❌ NÃO conte "atendimentos que a IA fez" assim — executions é podada e conversation_id é nullable:
-- SELECT count(DISTINCT conversation_id) FROM executions WHERE has_ai = 1;

-- ✅ Conte pela trilha durável em messages (não sofre poda):
SELECT count(DISTINCT conversation_id)
FROM messages
WHERE role = 'assistant' AND agent_key IS NOT NULL
  AND conversation_id IS NOT NULL;
```

**⚠️ Implicação para análise.** Para "**quantos atendimentos a IA atendeu**", conte por `messages` (`role='assistant' AND agent_key IS NOT NULL`), NÃO por `executions` — janelas antigas de execução já foram apagadas e `executions.conversation_id` é nullable em linhas legadas, o que **subconta**. Use `executions` só para métricas do turno recente (tokens/custo/latência/tool-steps), sempre ciente de que a janela histórica é limitada pela retenção.

---

## Resumo das pegadinhas (colar na cabeça antes de qualquer conta)

| # | Pegadinha | O que quebra se ignorar |
|---|---|---|
| 1 | `is_archived` ⟂ `status` | Contar arquivado como fechado |
| 2 | Close zera `assignee_user_id`; `resolved_at` volátil | "Fechado por atendente (core)" e "fechados no dia" |
| 3 | `origin='outbound'` funde operador+IA | "Iniciado por atendente" |
| 4 | Sem `direction`/`source`; discriminadores são `role`+`status`+`agent_key`+`sent_by_user_id` | Split cliente/IA/atendente/echo; `private_ai`≡`ai` |
| 5 | `conversation_event`: ator só no texto PT-BR; some se grupo OFF | Atribuir ação a `user_id`; assumir completude da trilha |
| 6 | IA×humano derive de `messages`, não `executions`; há classe **misto** | Split IA×humano |
| 7 | Regra 15/30 dias é calculada, não existe no banco | "Novo contato por re-engajamento" |
| 8 | Sem coluna de conversão | "Estratégia que converte" |
| 9 | Epoch float UTC → sempre `AT TIME ZONE 'America/Sao_Paulo'` | Todo bucket diário |
| 10 | `plugin_protocolos_atendimentos` ≠ atendimentos do core (N por conversa) | Contagem de protocolos/conversas |
| 11 | `usage` só tem `contact_id`; custo/agente vem de `executions` | Tokens/custo por agente vs por modelo |
| 12 | `executions` é podada | "Quantos atendimentos a IA fez" |
