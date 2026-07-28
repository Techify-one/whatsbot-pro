# WhatsBot — Instrumentação recomendada (não implementada)

> **Escopo atual = só documentação. Isto é um cardápio pra depois.**
> Nada aqui está implementado. Cada item é uma proposta priorizada por *leverage* (quantas análises "PARCIAL/BLOQUEADO" da doc 03 ela transforma em caminho limpo). Nenhuma migration abaixo existe no repo — são **esboços plausíveis** pro projeto (Alembic, Postgres, sem batch-mode), prontos pra virar `alembic revision` quando/se a decisão for tomada.

---

## Como ler cada recomendação

Cada uma traz cinco campos fixos:

- **O quê** — a coluna/tabela nova.
- **Por quê / qual análise destrava** — o "PARCIAL/BLOQUEADO" da doc 03 que vira "READY".
- **Esboço de migration Alembic** — arquivo plausível (nomes/tipos/índices reais do projeto).
- **Onde gravar no código** — o call site que passaria a popular a coluna (`arquivo:linha` do checkout atual).
- **Esforço / Risco** — baixo/médio/alto.

**Convenções do projeto que os esboços respeitam** (confirmadas no código):
- Timestamps são **epoch float UTC** (`Float`, não `TIMESTAMP`) — `db/tables.py:11-13`. Toda coluna de tempo nova é `sa.Float`.
- FK para `users.id` é **lógica** (sem constraint), igual a `messages.sent_by_user_id` (`db/tables.py:133`) e `audit_log.actor_user_id` (`db/tables.py:811`) — a trilha sobrevive à exclusão do usuário. Onde precisar do nome pra exibir, guarda-se um **snapshot** (`*_name`) igual a `sent_by_name`.
- Colunas core aditivas nascem `nullable=True` (ou `NOT NULL` + `server_default`), estilo migration `0050`/`0057`.
- A cabeça atual do Alembic é **`0057_atend_is_pinned`** (`db/alembic/versions/20260717_0057_atendimento_is_pinned.py:16`). Os esboços encadeiam a partir dela; se mais de um for adotado, renumere em sequência (`0058`, `0059`, …).
- Tabelas core de atendimento seguem o prefixo `atendimento_*` (ex.: `atendimento_labels`, `atendimento_counters` — `db/tables.py:482,471`). **Só tabelas de plugin** usam `plugin_<id>_*`; nada aqui é de plugin.

---

## Ranking por leverage

| # | Recomendação | Destrava | Esforço | Risco |
|---|---|---|---|---|
| 1 | `atendimentos.closed_by_user_id` (+ snapshot, escrito no fechamento) | "Fechados no dia **por atendente**" no core | Baixo | Baixo |
| 2 | Tabela `atendimento_status_events` (histórico de close/reopen) | Reaberturas, fechados-por-atendente **com histórico**, IA×humano no fechamento | Médio | Baixo |
| 3 | `atendimentos.first_response_at` / `first_human_response_at` | Tempo de 1ª resposta / SLA / diligência sem minerar `messages` | Médio | Médio |
| 4 | Sinal de conversão canônico (label `venda` **ou** `outcome` + `produto`/`valor`) | Toda análise de conversão / "venda que deu certo" | Baixo–Médio | Baixo |
| 5 | "Iniciado por atendente" + classificação de re-engajamento (colunas + config N) | "Novos contatos iniciados por atendente" incl. regra 15/30 dias | Médio–Alto | Médio |
| 6 | `usage.conversation_id` + `usage.agent_key` | Custo/token fatiável por conversa **e** por agente | Médio | Baixo |
| 7 | `messages.source`/`direction` (ou reviver `waiting_since`) | Source fino histórico (`private_ai` × `ai`, `echo` × `operator`) | Alto | Médio |

> Os itens **1–2** atacam o maior gargalo único da doc 03 (§2.1 do decision-brief: "no durable per-user closed/resolved-by attribution"). O **4** é a única coisa que desbloqueia análise de conversão — sem ele, "estratégia que converte" não tem variável-alvo. **6** é barato e libera relatório de custo por agente hoje impossível de `usage`.

---

## 1. `atendimentos.closed_by_user_id` — quem fechou o atendimento

**O quê.** Uma coluna `closed_by_user_id` (Integer, nullable, FK lógica → `users.id`) em `atendimentos`, mais um snapshot `closed_by_name` (Text, nullable), **populados no fechamento** e mantidos junto de `resolved_at`.

**Por quê / qual análise destrava.** Hoje "atendimentos **fechados no dia por atendente**" é **MISSING** (decision-brief §1; atendimentos-model §5.1): `set_status("closed")` zera `assignee_user_id` (`conversation_repo.py:641`), então `GROUP BY assignee_user_id` sobre linhas fechadas joga tudo em `NULL`. Não existe `closed_by_user_id`. O único vestígio do ator é o texto PT-BR do card `conversation_event` `status_closed` (só nome, sem `user_id` — messages-timing §3). Com a coluna, o relatório diário "fechados por atendente" vira SQL direto:

```sql
-- Fechados HOJE, por atendente (America/Sao_Paulo)
SELECT closed_by_user_id,
       COUNT(*) AS fechados
FROM atendimentos
WHERE resolved_at IS NOT NULL
  AND (to_timestamp(resolved_at) AT TIME ZONE 'America/Sao_Paulo')::date
      = (now() AT TIME ZONE 'America/Sao_Paulo')::date
GROUP BY closed_by_user_id;
```

> Limitação que permanece: como `resolved_at` só guarda o **último** fechamento (é zerado no reopen — `conversation_repo.py:644`), esta coluna também reflete apenas o último. Para histórico completo de quem fechou em cada ciclo, ver a recomendação **2** (as duas se complementam: a coluna é a conveniência "último fechador", a tabela é a verdade histórica).

**Esboço de migration Alembic.**

```python
"""atendimentos: closed_by_user_id + snapshot (quem fechou).

Revision ID: 0058_atend_closed_by
Revises: 0057_atend_is_pinned
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0058_atend_closed_by"
down_revision: Union[str, Sequence[str], None] = "0057_atend_is_pinned"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # FK LÓGICA (sem constraint), igual a assignee_user_id / audit_log.actor_user_id:
    # a trilha sobrevive à exclusão do usuário. closed_by_name é snapshot p/ exibir.
    op.add_column("atendimentos", sa.Column("closed_by_user_id", sa.Integer(), nullable=True))
    op.add_column("atendimentos", sa.Column("closed_by_name", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("atendimentos", "closed_by_name")
    op.drop_column("atendimentos", "closed_by_user_id")
```

**Onde gravar no código.** O ponto de escrita status-derivado é **`conversation_repo.set_status`** (`db/repositories/conversation_repo.py:623-645`) — hoje `set_status(conv_id, status)` não recebe ator. Duas opções:
- **Preferida (mínima cirurgia):** estampar no orquestrador **`conversation_service.set_status`**, que já conhece o `current_user` (resolve o autor pro card `_emit_notice`) e já chama o repo pra escrever. Após a escrita de status, um `_update` com `closed_by_user_id`/`closed_by_name` quando `status == "closed"`.
- Alternativa: adicionar `closed_by: dict | None = None` a `set_status` e gravar dentro do bloco `if status == "closed":` (`conversation_repo.py:639-642`). Cuidado: `set_status` também é chamado pelo auto-reopen inbound (`resolve_for_contact_ex`) — esse caminho só faz `open`, então nunca toca `closed_by` (ok).
- No **reopen** (`open`), deixar `closed_by_*` como está (espelha `resolved_at`, que é zerado; se quiser paridade exata, zere junto — decisão de gosto).

**Esforço:** baixo. **Risco:** baixo (aditivo, nullable; um único call site novo de escrita).

---

## 2. `atendimento_status_events` — histórico durável de transições de status

**O quê.** Uma tabela append-only de transições de status (close/reopen/archive), com `actor_user_id` + `ts` estruturados — a versão consultável do que hoje só existe como card PT-BR e como `resolved_at` volátil.

**Por quê / qual análise destrava.** `resolved_at` guarda **só o último fechamento** e é **zerado no reopen** (`conversation_repo.py:644`); um atendimento fechado→reaberto→refechado só mostra o último, e um fechado-depois-reaberto some (`resolved_at IS NULL`) — "fechados no dia" a partir de `resolved_at` **subconta** (atendimentos-model §5.2). Não existe tabela de histórico de status; as transições vivem só como emissões WS/bus e cards `messages(role='conversation_event')`, cujo ator está **só no texto** (messages-timing §3/§4.4). A tabela destrava, com precisão e por `user_id`:
- **reaberturas no dia** (impossível hoje como coluna);
- **fechados no dia por atendente** robusto a churn de reopen;
- **IA×humano no fechamento** (via `actor_type`), que hoje é MISSING (ia-tracking §4.2).

```sql
-- Fechamentos HOJE por atendente, contando TODO fechamento (não só o último)
SELECT actor_user_id, COUNT(*) AS fechamentos
FROM atendimento_status_events
WHERE to_status = 'closed'
  AND (to_timestamp(ts) AT TIME ZONE 'America/Sao_Paulo')::date
      = (now() AT TIME ZONE 'America/Sao_Paulo')::date
GROUP BY actor_user_id;
```

**Esboço de migration Alembic.**

```python
"""atendimento_status_events: histórico append-only de transições de status.

Revision ID: 0059_atend_status_events
Revises: 0058_atend_closed_by
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0059_atend_status_events"
down_revision: Union[str, Sequence[str], None] = "0058_atend_closed_by"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "atendimento_status_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        # FK REAL p/ a conversa (cascade: some com o atendimento). É log da conversa.
        sa.Column("conversation_id", sa.Integer(),
                  sa.ForeignKey("atendimentos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=True),   # NULL no create
        sa.Column("to_status", sa.Text(), nullable=False),    # open | closed | archived | unarchived
        # ator: FK LÓGICA (sem constraint) + snapshot de nome + tipo (system|user|ai)
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_type", sa.Text(), nullable=False, server_default="system"),
        sa.Column("actor_name", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),        # ex.: 'auto_reopen_inbound'
        sa.Column("ts", sa.Float(), nullable=False),          # epoch float UTC
    )
    op.create_index("idx_atend_status_events_conv",
                    "atendimento_status_events", ["conversation_id", "ts"])
    op.create_index("idx_atend_status_events_actor",
                    "atendimento_status_events", ["actor_user_id", "ts"])
    op.create_index("idx_atend_status_events_to",
                    "atendimento_status_events", ["to_status", "ts"])


def downgrade() -> None:
    op.drop_index("idx_atend_status_events_to", table_name="atendimento_status_events")
    op.drop_index("idx_atend_status_events_actor", table_name="atendimento_status_events")
    op.drop_index("idx_atend_status_events_conv", table_name="atendimento_status_events")
    op.drop_table("atendimento_status_events")
```

**Onde gravar no código.** Um único ponto de orquestração já centraliza toda transição: **`conversation_service.set_status`** (aplica `filter.conversation.before_status`, escreve via repo, faz broadcast `conversation.status_changed`, emite o card `status_open`/`status_closed` — atendimentos-model §2). Um `INSERT` na nova tabela ali dentro cobre close/reopen; `conversation_service.archive` cobre archive/unarchive. O `actor_user_id`/`actor_name` saem do `current_user` (já resolvido pro `_emit_notice`); `actor_type` = `user` no painel, `ai`/`system` no auto-reopen inbound (`resolve_for_contact_ex`, `conversation_repo.py:346-347`, que hoje já sinaliza `event="reopened"`). Nada some do fluxo atual — é um insert append-only ao lado do card.

**Esforço:** médio. **Risco:** baixo (tabela nova, aditiva; um writer; defensivo — falha no insert não pode quebrar o fechamento, igual ao padrão de `emit_conversation_notice`).

---

## 3. `atendimentos.first_response_at` / `first_human_response_at` — 1ª resposta materializada

**O quê.** Duas colunas Float nullable em `atendimentos`: `first_response_at` (ts da 1ª resposta ao cliente, IA **ou** humano) e `first_human_response_at` (ts da 1ª resposta de operador humano). Escritas **uma única vez** (a primeira vence).

**Por quê / qual análise destrava.** Não existe **nenhuma** coluna de 1ª resposta (atendimentos-model §5.5; grep por `first_reply`/`first_response`/`primeira_resposta` = zero hits). `atendimentos.waiting_since` **parece** ser esse campo mas é **coluna morta** — declarada em `db/tables.py:450` e na migration de criação, **nunca escrita** por código (messages-timing §4.2). Hoje todo tempo-de-resposta é minerado de `messages.ts` + `(role,status,agent_key,sent_by_name)` por `conversation_id`. Materializar destrava tempo de 1ª resposta / SLA / diligência do atendente como leitura direta:

```sql
-- Tempo mediano de 1ª resposta HUMANA (segundos), atendimentos abertos hoje
SELECT percentile_cont(0.5) WITHIN GROUP (
         ORDER BY first_human_response_at - opened_at
       ) AS mediana_seg
FROM atendimentos
WHERE first_human_response_at IS NOT NULL
  AND (to_timestamp(opened_at) AT TIME ZONE 'America/Sao_Paulo')::date
      = (now() AT TIME ZONE 'America/Sao_Paulo')::date;
```

> Escopo honesto: latência de **entrega/leitura** (`sent→delivered→read`) continua irrecuperável — o `status` é sobrescrito no lugar (messages-timing §4.3). Isto materializa só a **1ª resposta**, que é o que as métricas de diligência pedem.

**Esboço de migration Alembic.** (Com backfill opcional a partir de `messages` — barato e deixa o histórico útil no dia 1.)

```python
"""atendimentos: first_response_at / first_human_response_at.

Revision ID: 0060_atend_first_response
Revises: 0059_atend_status_events
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0060_atend_first_response"
down_revision: Union[str, Sequence[str], None] = "0059_atend_status_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("atendimentos", sa.Column("first_response_at", sa.Float(), nullable=True))
    op.add_column("atendimentos", sa.Column("first_human_response_at", sa.Float(), nullable=True))

    # Backfill a partir de messages (mesmos discriminadores canônicos do CANON):
    #   1ª resposta qualquer = 1º role='assistant' status != 'failed'
    #   1ª resposta humana    = 1º role='assistant' status='operator' sent_by_user_id NOT NULL
    op.execute("""
        UPDATE atendimentos a SET first_response_at = sub.ts FROM (
            SELECT conversation_id, MIN(ts) AS ts FROM messages
            WHERE role = 'assistant' AND conversation_id IS NOT NULL
              AND (status IS NULL OR status <> 'failed')
            GROUP BY conversation_id
        ) sub WHERE sub.conversation_id = a.id;
    """)
    op.execute("""
        UPDATE atendimentos a SET first_human_response_at = sub.ts FROM (
            SELECT conversation_id, MIN(ts) AS ts FROM messages
            WHERE role = 'assistant' AND status = 'operator'
              AND sent_by_user_id IS NOT NULL AND conversation_id IS NOT NULL
            GROUP BY conversation_id
        ) sub WHERE sub.conversation_id = a.id;
    """)


def downgrade() -> None:
    op.drop_column("atendimentos", "first_human_response_at")
    op.drop_column("atendimentos", "first_response_at")
```

**Onde gravar no código.** Nos dois sites de save da resposta, com guarda "só se ainda NULL":
- `first_response_at` — quando a resposta da IA é salva (`agent_run_service.py:393` `contact.add_message("assistant", …)`) **e** quando o operador salva pelo painel (`handler.save_operator_message`, `agent/handler.py:381-402`). Como ambos podem ser "a primeira", o carimbo é: `UPDATE atendimentos SET first_response_at = :ts WHERE id = :conv AND first_response_at IS NULL` (idempotente, resolve corrida — a primeira escrita vence).
- `first_human_response_at` — só no site de operador (`save_operator_message` / rotas de send em `server/routes/contacts.py:779,839`), mesmo `WHERE … IS NULL`. Excluir echo exigindo `sent_by_user_id IS NOT NULL` (echo deixa NULL — message-ingest §2).

**Esforço:** médio. **Risco:** médio (dois call sites; depende de `conversation_id` já resolvido no momento do save — em linhas legadas/sem conversa o carimbo simplesmente não ocorre. O `UPDATE … WHERE IS NULL` neutraliza corrida de batches concorrentes).

---

## 4. Sinal de conversão canônico — label `venda` **ou** `atendimentos.outcome` + `produto`/`valor`

**O quê.** Um *ground-truth* de resultado, que **hoje não existe em lugar nenhum** (decision-brief §2.5; messages-timing §1). Duas formas (escolher uma; a A é a mais barata):
- **(A) Etiqueta de conversa `venda`** — seed de uma linha em `atendimento_labels` (a máquina de etiquetas de conversa já existe: `conversation_label_repo` + rotas `server/routes/conversation_labels.py`). Operador/IA marca a conversa; `produto`/`valor` vão em `atendimentos.custom_attributes` (JSONB já existente, `db/tables.py:452`).
- **(B) Coluna `atendimentos.outcome`** (Text nullable: `won`/`lost`/`sem_resultado`/…) + opcionalmente `custom_attributes` pra `produto`/`valor`. Mais explícito, mais código (endpoint/tool pra setar).

**Por quê / qual análise destrava.** Sem sinal de resultado, "estratégia que converte" e "padrões de conversão" **não têm variável-alvo** — são BLOQUEADAS, não parciais (decision-brief §2.5; protocolo-fechado ≠ venda; mídia `order`/`product` é `media_extras` efêmero, não persistido estruturado). Definido o sinal, a análise de conversão vira leitura:

```sql
-- (A) Conversas marcadas 'venda' hoje, por agente ativo
SELECT a.active_agent_key, COUNT(*) AS vendas
FROM atendimentos a
JOIN atendimento_label_links ll ON ll.conversation_id = a.id
JOIN atendimento_labels l ON l.id = ll.label_id AND l.name = 'venda'
WHERE (to_timestamp(a.last_activity_at) AT TIME ZONE 'America/Sao_Paulo')::date
      = (now() AT TIME ZONE 'America/Sao_Paulo')::date
GROUP BY a.active_agent_key;
```

**Esboço de migration Alembic.**

```python
# (A) — seed de dados, sem mudança de schema (usa atendimento_labels existente)
"""seed da etiqueta de conversa 'venda' (ground-truth de conversão).

Revision ID: 0061_seed_label_venda
Revises: 0060_atend_first_response
"""
from alembic import op

revision = "0061_seed_label_venda"
down_revision = "0060_atend_first_response"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotente: a coluna name é UNIQUE (uq em atendimento_labels).
    op.execute("""
        INSERT INTO atendimento_labels (name, color, position)
        VALUES ('venda', '#16a34a', 0)
        ON CONFLICT (name) DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("DELETE FROM atendimento_labels WHERE name = 'venda';")
```

```python
# (B) — coluna outcome (alternativa mais explícita)
def upgrade() -> None:
    import sqlalchemy as sa
    from alembic import op
    op.add_column("atendimentos", sa.Column("outcome", sa.Text(), nullable=True))
    # produto/valor podem morar em custom_attributes (JSONB) — sem colunas novas.
```

**Onde gravar no código.** (A) A etiqueta é aplicada pela máquina existente: `conversation_label_repo.set_for_conversation` via `server/routes/conversation_labels.py:118-120` (operador na UI) ou por uma tool de IA que chame a mesma rota; `produto`/`valor` via `conversation_repo.set_custom_attributes` (`conversation_repo.py:674-676`). (B) exigiria um endpoint/tool novo pra setar `outcome`. **Recomendação:** começar por **(A)** — zero schema além do seed, reusa etiquetas + `custom_attributes`, e o analista lê `atendimento_label_links`.

**Esforço:** baixo (A) / médio (B). **Risco:** baixo. Observação: isto é mais uma **decisão de processo** (a equipe precisa efetivamente marcar) do que de schema — sem alguém marcando, a coluna/label fica vazia e a análise segue bloqueada.

---

## 5. "Iniciado por atendente" + classificação de re-engajamento (com N configurável)

**O quê.** Materializar, no CREATE do atendimento: `initiated_by_user_id` (Integer, FK lógica → `users.id`) e `initiation_kind` (Text: `inbound` | `attendant` | `ai` | `reengagement`). O limiar de dias da regra de re-engajamento vira **config key** `analytics_reengagement_days` (default 30; documentar 15 e 30 como valores usuais) — não é migration, é runtime.

**Por quê / qual análise destrava.** "Novos contatos iniciados por atendente" é PARCIAL e a regra 15/30 dias é MISSING (users-attribution §4). `atendimentos.origin='outbound'` **conflaciona operador humano E IA** (ambos são 1ª mensagem não-`user` — `agent/memory.py:307`); o atendente específico só existe no grão de mensagem (`sent_by_user_id` da 1ª `assistant`); não há `conversation.created_by`; a regra de re-engajamento não está implementada em lugar nenhum (grep completo, users-attribution §4). Materializar destrava o relatório diário direto:

```sql
-- Novos contatos ABORDADOS por atendente hoje (re-engajamento), por atendente
SELECT initiated_by_user_id, COUNT(*) AS abordagens
FROM atendimentos
WHERE initiation_kind = 'reengagement'
  AND (to_timestamp(opened_at) AT TIME ZONE 'America/Sao_Paulo')::date
      = (now() AT TIME ZONE 'America/Sao_Paulo')::date
GROUP BY initiated_by_user_id;
```

A classificação no CREATE combina as primitivas que já existem (users-attribution §4 "onde computar"): `contacts.created_at` (1º contato — `db/tables.py:87`), último inbound `messages.ts WHERE role='user'` (`message_repo.last_inbound_ts`, `message_repo.py:239-264`) e o `sent_by_user_id` da 1ª `assistant`. Regra: `initiation_kind='attendant'` se a 1ª mensagem é de operador humano; sobe pra `'reengagement'` se, além disso, (o contato nunca teve inbound antes) OU (≥ N dias desde o último `role='user'`).

**Esboço de migration Alembic.**

```python
"""atendimentos: initiated_by_user_id + initiation_kind (abordagem/re-engajamento).

Revision ID: 0062_atend_initiation
Revises: 0061_seed_label_venda
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0062_atend_initiation"
down_revision: Union[str, Sequence[str], None] = "0061_seed_label_venda"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("atendimentos", sa.Column("initiated_by_user_id", sa.Integer(), nullable=True))
    op.add_column("atendimentos", sa.Column("initiation_kind", sa.Text(), nullable=True))
    op.create_index("idx_atend_initiation",
                    "atendimentos", ["initiation_kind", "initiated_by_user_id"])
    # N (dias) NÃO é schema — vive em config['analytics_reengagement_days'] (default 30).


def downgrade() -> None:
    op.drop_index("idx_atend_initiation", table_name="atendimentos")
    op.drop_column("atendimentos", "initiation_kind")
    op.drop_column("atendimentos", "initiated_by_user_id")
```

**Onde gravar no código.** No CREATE do atendimento, ao lado de onde `origin` é resolvido (`agent/memory.py:307`, que já conhece o `role` da 1ª mensagem materializadora; o INSERT é `conversation_repo._insert_conversation`, `conversation_repo.py:111-154`). Quando a 1ª mensagem é de operador humano (`role='assistant'` + `sent_by_user_id` presente), carimba `initiated_by_user_id` e computa `initiation_kind` consultando `contacts.created_at` + `message_repo.last_inbound_ts` contra `now()` e o N de config (cache 30s no estilo `ai_settings`). Deve ser **fail-open**: qualquer erro na classificação cai em `initiation_kind` NULL sem quebrar o create (caminho quente).

**Esforço:** médio–alto (lógica de classificação + config + plumbing no create). **Risco:** médio (o create é hot path; a leitura extra precisa ser barata e defensiva). Alternativa de menor risco: deixar isto como **query derivada** no plugin `analises` (sem tocar o core), e só materializar se o custo da query recorrente incomodar.

---

## 6. `usage.conversation_id` + `usage.agent_key` — custo/token fatiável por conversa e por agente

**O quê.** Duas colunas nullable em `usage`: `conversation_id` (Integer, FK lógica → `atendimentos.id`) e `agent_key` (Text → `ai_agents.agent_key`).

**Por quê / qual análise destrava.** `usage` tem **só** `contact_id` como link relacional (`db/tables.py:156`) — não dá pra fatiar token/custo por conversa nem por agente (ia-tracking §4.1). Hoje "custo por agente" só sai de `executions.total_cost_usd/total_tokens`, que são **agregados por execução do agente FINAL** (perde o breakdown por modelo/`call_type` que `usage` mantém, e `executions` é **podado** por idade/contagem — ia-tracking §4.3/§4.8). As duas colunas dão o corte fino e durável:

```sql
-- Custo por agente HOJE, com breakdown por call_type preservado
SELECT agent_key, call_type, SUM(total_tokens) AS tokens, SUM(cost_usd) AS custo
FROM usage
WHERE agent_key IS NOT NULL
  AND (to_timestamp(ts) AT TIME ZONE 'America/Sao_Paulo')::date
      = (now() AT TIME ZONE 'America/Sao_Paulo')::date
GROUP BY agent_key, call_type;
```

**Esboço de migration Alembic.**

```python
"""usage: conversation_id + agent_key (fatiar token/custo por conversa e agente).

Revision ID: 0063_usage_conv_agent
Revises: 0062_atend_initiation
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0063_usage_conv_agent"
down_revision: Union[str, Sequence[str], None] = "0062_atend_initiation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # FKs LÓGICAS (sem constraint): usage é log, não deve cascatear/travar o INSERT.
    op.add_column("usage", sa.Column("conversation_id", sa.Integer(), nullable=True))
    op.add_column("usage", sa.Column("agent_key", sa.Text(), nullable=True))
    op.create_index("idx_usage_conversation", "usage", ["conversation_id", "ts"])


def downgrade() -> None:
    op.drop_index("idx_usage_conversation", table_name="usage")
    op.drop_column("usage", "agent_key")
    op.drop_column("usage", "conversation_id")
```

**Onde gravar no código.** Encadear os dois valores pela cadeia de escrita do `usage`, que hoje ignora conversa/agente:
- `usage_repo.add(...)` (`db/repositories/usage_repo.py:13-27`) — adicionar params `conversation_id`/`agent_key`.
- `ContactMemory.add_usage` (`agent/memory.py:684-688`) — repassar.
- `record_usage_tokens` / `record_usage` (`agent/llm.py:131-153` / `108-128`) — repassar.
- No call site da resposta principal (`agent_run_service.py:371-377`), o `agent_key` está disponível (`agent_spec.agent_key`, e o `final_agent_key` após routing em `:390`); o `conversation_id` sai do contextvar do turno (`get_current_execution_id`/a conversa resolvida do `contact`). Nos `call_type` de mídia (`audio`/`image`/`document`, `agent/llm.py:202,251,387`) não há agente → `agent_key` NULL (correto).

**Esforço:** médio (cadeia com ~4 pontos, mas mecânica). **Risco:** baixo (aditivo nullable; linhas antigas ficam NULL; `usage` nunca é podado no código visto, então o histórico novo acumula limpo).

---

## 7. `messages.source`/`direction` (ou reviver `waiting_since`) — só se precisar do source fino histórico

**O quê.** Persistir em `messages` o enum `source` do event-bus (`ai`/`operator`/`private_ai`/`retry`/`echo`/`template`) e/ou um `direction` (`in`/`out`). Alternativa correlata: **reviver** `atendimentos.waiting_since` (coluna morta) com um writer vivo.

**Por quê / qual análise destrava.** O `source` fino é **só do event-bus, não persistido** (messages-timing §2): no banco, `private_ai` é indistinguível de `ai` (ambos `role='assistant'`, `status='sent'`, `agent_key` setado), e `echo` só se separa de `operator` pela ausência de `sent_by_name`. O tuple canônico `(role, status, agent_key, sent_by_name)` já cobre **quase tudo** que os relatórios pedem — por isso esta é a **menor prioridade**: só vale se alguma análise exigir explicitamente distinguir `private_ai` de `ai` ou `retry` de send normal no histórico.

**Esboço de migration Alembic.**

```python
"""messages: source (+ direction) — persistir o enum do event-bus.

Revision ID: 0064_messages_source
Revises: 0063_usage_conv_agent
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0064_messages_source"
down_revision: Union[str, Sequence[str], None] = "0063_usage_conv_agent"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("source", sa.Text(), nullable=True))
    op.add_column("messages", sa.Column("direction", sa.Text(), nullable=True))  # in|out (opcional)


def downgrade() -> None:
    op.drop_column("messages", "direction")
    op.drop_column("messages", "source")
```

**Onde gravar no código.** `message_repo.add` (`db/repositories/message_repo.py:15-67`) ganharia um param `source`, e **cada save site** passaria o mesmo valor que já emite ao bus — são muitos: `save_assistant_message`/`save_operator_message` (`agent/handler.py:364-402`), echo (`app/services/message_ingest_service.py:298-314`), private_ai/retry/template (`server/routes/contacts.py:1117,1590`, `server/routes/conversations.py:660`). É essa dispersão que torna o esforço **alto**. Para `waiting_since`: um writer em `conversation_repo` que estampa no inbound `role='user'` e zera na 1ª resposta outbound (esforço **baixo**, isolado) — mas note que a recomendação **3** (`first_response_at`) já entrega a métrica de espera de forma mais direta e imutável.

**Esforço:** alto (muitos save sites) / baixo (só o writer de `waiting_since`). **Risco:** médio (superfície ampla de escrita; fácil deixar um site sem passar `source` → coluna parcial e enganosa). **Recomendação:** só encarar se uma análise concreta exigir o source fino; senão, ficar no tuple canônico.

---

## Ordem sugerida de adoção

Se for pra shipar em ondas, o custo/benefício aponta para:

1. **Onda 1 (barata, alto leverage):** #1 (`closed_by_user_id`) + #6 (`usage.conversation_id`/`agent_key`) + #4-A (seed label `venda`). Três aditivos de baixo risco que destravam "fechados por atendente", "custo por agente" e o *ground-truth* de conversão.
2. **Onda 2 (histórico e SLA):** #2 (`atendimento_status_events`) + #3 (`first_response_at`). Resolvem reaberturas, fechados-por-atendente com histórico e tempo de 1ª resposta.
3. **Onda 3 (só se necessário):** #5 (re-engajamento — talvez melhor como query no plugin `analises` antes de materializar) e #7 (source fino — só sob demanda).

> Regra de ouro herdada do CANON: **escrita de volta no core sempre via repos/API REST** (aplicam display_id, índice único, `conversation_event`, broadcasts, RBAC), nunca SQL cru. Estas colunas seriam populadas **dentro** dos call sites indicados, não por UPDATE externo.
