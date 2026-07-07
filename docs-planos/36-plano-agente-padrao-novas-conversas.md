# Plano 36 — Agente padrão para novas conversas (nativo, na tela Agentes)

## Objetivo

Permitir escolher, **na tela nativa de IA (Configurações → Agentes)**, qual agente é
o **padrão para novas conversas** — um seletor em cada agente. Hoje toda conversa nova
nasce presa ao agente de chave `"default"`, sem opção de UI para trocar.

**Restrição dura:** conversas **em andamento não podem mudar**. A alteração vale
**somente para novas conversas**.

Não fica no plugin Vendas IA — é mudança **no core**, na tela de Agentes.

## Diagnóstico (estado atual)

Resolução do agente de uma conversa (`agent/agent_factory.py:204-239`,
`build_for_contact`), precedência:

```
conversa.active_agent_key  →  inbox.default_agent_key  →  agente global "default"
```

- O último degrau (`dynamic_registry.get_default_agent()` → `agent_repo.get_default()`)
  retorna **sempre** o agente de chave literal `"default"` (`agent_repo.DEFAULT_AGENT_KEY`),
  **não** o roteador. `is_router=1` só serve à allowlist do `transferir_agente` e à seção
  de destinos no prompt — não torna o roteador o agente de entrada.
- **Carimbo na criação:** ao criar a conversa, `active_agent_key` já é gravado
  (`conversation_repo.py:86-87` → `default_agent_key_for_inbox`, linhas 43-57): pega
  `inbox.default_agent_key` e, se `None`, cai em `"default"`. Por isso toda conversa nova
  nasce grudada no `"default"`.

Confirmado no banco (10.8.200.13/whatsbot):
- Todos os inboxes com `default_agent_key = None` → `"default"` é carimbado sempre.
- Conversas abertas: 4 em `"default"`, 7 em `None`, 1 em `matheus`.
- Agentes: `roteador` (BIA Router, único `is_router=1`), `comercial`, `suporte`,
  `fechamento`, `default` — todos `enabled`.

Existe endpoint backend `PUT /api/inboxes/{inbox_id}/default-agent` (gated `inbox.manage`),
mas **sem UI** e é per-inbox — não é o que o usuário quer (quer per-agente, global, na tela
Agentes).

## Decisão de arquitetura

- Nova coluna **`ai_agents.is_default`** com semântica **radio** (no máximo um agente
  marcado), **espelhando** a máquina já existente do `is_router` (demote-do-anterior em
  `agent_repo.save` + índice único parcial — precedente: migration 0035
  `ux_ai_agents_single_router`). A "verdade" viaja na própria linha do agente
  (`list_all` já a devolve → o front só lê `a.is_default`).

- **Único ponto de comportamento que muda = o carimbo de criação**
  (`default_agent_key_for_inbox`). Nova precedência:
  ```
  inbox.default_agent_key  →  agente is_default=1  →  "default" (piso)
  ```

- **Runtime NÃO muda:** `agent_factory.build_for_contact` e
  `dynamic_registry.get_default_agent` continuam caindo no agente-chave `"default"`.
  Consequência: as 7 conversas abertas com `active_agent_key=None` **seguem no `"default"`**;
  as 4 em `"default"` e a 1 em `matheus` resolvem pela própria `active_agent_key`.
  **Nenhuma conversa em andamento se altera.** ✅ (restrição honrada por construção).

- Nomeclatura: mantém-se `agent_repo.get_default()` (piso de emergência, chave `"default"`).
  A nova consulta é `get_new_conversation_default()` (linha com `is_default=1`), para não
  confundir os dois conceitos.

## Fases

### F1 — Schema

- `db/tables.py` (bloco `ai_agents`, junto de `is_router`): adicionar
  `Column("is_default", Integer, nullable=False, server_default="0")`.
- Nova migration `db/alembic/versions/20260707_0042_agent_is_default.py`
  (revises `0041_seed_audit_manage`), espelhando a 0035:
  - `add_column` `is_default` (guardado/idempotente).
  - Índice único parcial `ux_ai_agents_single_default` em `is_default`
    `WHERE is_default = 1` (`postgresql_where` + `sqlite_where`).
  - Seed idempotente: `UPDATE ai_agents SET is_default=1 WHERE agent_key='default'`
    **apenas se ninguém já tiver** `is_default=1` — a UI passa a mostrar o `"default"`
    como padrão atual e o fallback não muda em instalações existentes.
  - `downgrade`: drop index + column.

### F2 — Repositório (`db/repositories/agent_repo.py`)

- `_SNAPSHOT_COLS` (linha 31): incluir `"is_default"`.
- `_row_to_dict` (linha 56): `d["is_default"] = bool(d.get("is_default", 0))`.
- `save()`:
  - novo param `is_default: bool = False`;
  - entra no **dedup** (linha 153), em `values` (linha 167) e em `update_cols` (linha 190);
  - antes do upsert, se `is_default` → `_demote_other_defaults(conn, agent_key, now)`.
- Novo `_demote_other_defaults()` — cópia de `_demote_other_routers` (linha 210) trocando
  `is_router`→`is_default` (bump de versão + snapshot dos rebaixados, para o trail/rollback).
- Novo `get_new_conversation_default() -> dict | None` — `select where is_default==1`
  (espelha `get_router`, linha 242).
- `rollback()` (linha 277): repassar `is_default=bool(snap.get("is_default", 0))`.
- `delete()`: sem mudança — apagar o agente padrão só solta a linha do índice; o fallback
  volta ao `"default"`.

### F3 — API (`server/routes/ai_engine.py`)

- `save_agent` (linhas 120-134): ler `is_default=bool(body.get("is_default", False))` e
  repassar ao `agent_repo.save`.
- `save_agent_prompt` (linhas 155-168): **preservar**
  `is_default=bool(existing.get("is_default", False))` (igual já faz com `is_router`) — patch
  só-prompt não pode derrubar a flag.
- `list`/`get`/`history` já expõem o campo via `_row_to_dict`.
- `web/static/js/services/api.js:784` (`saveAgent`) já encaminha o body inteiro →
  **sem mudança de service**.

### F4 — Carimbo de criação (`db/repositories/conversation_repo.py`)

- `default_agent_key_for_inbox` (linhas 43-57): trocar o fallback para
  `inbox.default_agent_key` → `agent_repo.get_new_conversation_default()?.agent_key`
  (se existir e `enabled`) → `agent_repo.DEFAULT_AGENT_KEY`.
- **Não tocar** em `agent_factory.build_for_contact` nem `dynamic_registry.get_default_agent`.
- Nota: `app/services/conversation_service.py:465` também usa `default_agent_key_for_inbox`
  no fluxo de **reabertura** de conversa fechada — ao reabrir (ciclo novo) ela adota o padrão
  novo. Isso **não** afeta conversas abertas/em andamento; é coerente com "nova conversa".
  Fica assim (aceito).

### F5 — Frontend (`web/static/js/components/ai/AgentsManager.js`)

- `AgentForm`:
  - estado `isDefault` (init `!!agent.is_default`; resetar no `useEffect` de troca de agente,
    linha ~175);
  - checkbox **"Padrão para novas conversas"** ao lado do "É roteador" (linhas 415-419);
  - aviso "o padrão atual (X) deixará de ser" quando outro agente já for `is_default`
    (espelha o aviso do roteador, linhas 421-426);
  - incluir `is_default: isDefault` no payload `onSave` (linhas 245-256).
- Card da lista: pill **"padrão"** quando `a.is_default` (ao lado do pill "router", linha 717).
- Legibilidade dark: usar classes `wa-*` (as pills/checkboxes atuais já seguem).

### F6 — Testes

- `agent_repo`: salvar com `is_default=1` rebaixa os outros; índice único;
  `get_new_conversation_default`.
- `default_agent_key_for_inbox`: com um `is_default` definido, uma **conversa nova** carimba
  esse agente; **conversa existente `None`-bound continua resolvendo `"default"`** (garantia
  do "não muda em andamento").
- `tests/test_endpoints.py` verde (save/get agent expõe `is_default`).

### F7 — Aplicar + usar

- A migration roda no **boot** (`alembic upgrade head`) → **reiniciar o servidor** uma vez.
- Depois: **Configurações de IA → Agentes → BIA Router → marcar "Padrão para novas
  conversas" → Salvar**. O `"default"` é rebaixado; toda conversa **nova** nasce no BIA
  Router. Abertas atuais: inalteradas.

## Status de execução (2026-07-07)

Implementado na ordem F1→F7, um commit por fase, verde a cada uma. Base: branch
`developer`, herdando o `AgentsManager.js` já atualizado pelo plano 37b (37b mergeado
antes — confirmado). **Correção vs. o doc**: a migration encadeia depois do head **real**
`0042_exec_conversation_channel` (o doc citava `0041`/número `0042`, desatualizado) →
revisão **`0043_agent_is_default`**.

- **F1** (`db/tables.py` + `db/alembic/versions/20260707_0043_agent_is_default.py`):
  coluna `ai_agents.is_default` + índice único parcial `ux_ai_agents_single_default`.
  Migration guardada/idempotente/reversível (add_column → rebaixa duplicatas → seed
  condicional do `"default"` só se ninguém já for padrão → índice). Validada no test DB:
  `upgrade`/`downgrade`/`upgrade` roundtrip limpo; coluna+índice presentes.
- **F2** (`agent_repo.py`): `save()` aceita `is_default` (dedup/values/update_cols),
  rebaixa o anterior via `_demote_other_defaults` (bump+snapshot); `get_new_conversation_default()`;
  `rollback()` repassa a flag; `_row_to_dict`/`_SNAPSHOT_COLS` incluem o campo.
- **F3** (`server/routes/ai_engine.py`): `save_agent` lê `is_default` do body; `save_agent_prompt`
  preserva a flag da linha existente (patch só-prompt não derruba).
- **F4** (`conversation_repo.py`): `default_agent_key_for_inbox` → `inbox.default_agent_key`
  → `get_new_conversation_default()` (se `enabled`) → piso `"default"`. Runtime intocado.
- **F5** (`AgentsManager.js`): checkbox "Padrão para novas conversas" (estado `isNewConvDefault`,
  distinto do `isDefault`=chave literal), hint, aviso de rebaixamento (espelha o do roteador),
  `is_default` no payload; pill "novas conversas" no card. Dark-safe (tints de precedente).
- **F6** (`tests/test_agent_default.py` +8, `tests/test_endpoints.py` +4): radio/índice/carimbo/
  **não-muda-em-andamento** + round-trip pelo endpoint. Suíte de endpoints: **1090 passed**.
  Regressão: ai_agents_jsonb 26 ✓ · agent_json_hardening 25 ✓ · agent_routing 29 ✓ ·
  conversation_race + improve_scope 6 ✓.
- **F7** (operacional — do usuário): a migration roda no boot (`alembic upgrade head`) →
  **reiniciar o servidor uma vez**; depois **Configurações de IA → Agentes → \<agente\> →
  marcar "Padrão para novas conversas" → Salvar**. O padrão anterior é rebaixado; toda
  conversa **nova** nasce nesse agente. Conversas abertas atuais: inalteradas.

**Pontos em aberto resolvidos**: nome da flag = `is_default` (mantido, espelha `is_router`);
reabertura de conversa fechada adota o padrão novo (aceito — é ciclo novo).

Commits: F1 schema · F2 agent_repo · F3 routes · F4 carimbo · F5 frontend · F6 testes.

## Fora de escopo

- Padrão **por inbox** na UI (a coluna `inbox.default_agent_key` e o endpoint já existem;
  falta só uma tela) — fica para outro plano se necessário.
- Rebind das conversas abertas atuais (proibido por requisito: não mudar em andamento).

## Pontos em aberto (decidir antes de implementar)

- Nome da flag: `is_default` (proposto) vs. algo como `is_entry`/`is_default_for_new`.
- Comportamento na reabertura de conversa fechada (F4, nota): adotar o padrão novo (proposto)
  vs. manter estritamente só na criação.
