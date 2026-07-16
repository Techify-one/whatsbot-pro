# Plano 51 - Core: trace, captura de contexto e versionamento de variáveis (sub-plano 01)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-16 · **Escopo:** médio (3 mudanças de schema no core + 1 extração de módulo; toca fluxo crítico de save de resposta)
> **Origem:** Plano 51 — "Melhoria agêntica". Este sub-plano cobre as mudanças **no core** que habilitam o plugin `melhorias` evoluído (sub-planos 02+) a reconstruir COM PRECISÃO o que a IA viu/fez por mensagem selecionada e a **reverter** qualquer entidade que a IA editar (paridade da D4).
> **Método:** dois levantamentos `arquivo:linha` verificados (rastro de execução + infra de versionamento) reconferidos contra o repo real neste checkout. As mudanças são cirúrgicas e aditivas: 1 coluna nullable em `messages`, 1 flag ligada, versionamento de `ai_variables` no molde já existente de `ai_agents`, e a extração de 3 helpers puros que hoje vivem dentro do plugin.
> **Escopo travado:** v1 liga o **link preciso** `messages → executions` (D3), a **captura exata** de prompt+histórico (GAP 2) e o **versionamento de variáveis** (ponto cego). Fica **adiável**: ligar `usage` à execução/agente, destruncar `tool_executed.result`, patch parcial de agente. Ver §5 e §6.
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência |
|---|---------|--------------|
| **D3** ✅ | Link **preciso** `messages.execution_id` (coluna nova, nullable, FK lógica para `executions.id`), NÃO `executions.response_msg_ids`. | Fase 1. Lookup O(1) `msg_id (resposta) → execution`; cada parte de um split herda o mesmo `execution_id`. Molde: `sent_by_user_id` (FK lógica sem constraint). |
| **D4** ✅ | A IA (com aprovação humana) precisa poder **reverter** qualquer entidade que editar. `ai_variables` é hoje o **único** ponto cego (sem `version`/history/rollback). | Fase 3. Paridade com `ai_agents`/`ai_tools`/`ai_agent_prompt_history`. |
| **DL1** ✅ | Captura EXATA de contexto (`llm_context`) passa a ser **ligada por default** (`execution_capture_context=True`), aproveitando a truncagem já existente (2000/msg, 20000 total, scrub de base64) — NÃO removemos o kill-switch. | Fase 2. Só o que for capturado **a partir de agora** tem fidelidade; mensagens antigas continuam caindo na aproximação viva. |
| **DL2** ✅ | Fallback fuzzy por janela de `ts` (`_find_execution_around`) **permanece** como plano B quando `execution_id` é NULL (linhas legadas / caminhos sem tracking). | Fases 1 e 4. Nunca derrubar a reconstrução por falta do link. |
| **DL3** ✅ | Os helpers de reconstrução saem do plugin para um **módulo de core reutilizável** (`app/services/execution_trace.py`), consumido pelo gateway do plugin (sub-planos 02+) e pela feature "Gerar melhoria" já existente. | Fase 4. Uma fonte de verdade para "dado um msg_id → agente/tools/prompt/histórico/contexto exato". |

---

## 1. Como funciona hoje (mapa) — verificado

### 1a. Rastro de execução (o que é persistido)

| Costura | Onde | Observação |
|---------|------|------------|
| Tabela `messages` | `db/tables.py:108-143` | Tem `msg_id` (indexado `idx_msg_id` :142), `agent_key` (:139), `conversation_id`, `sent_by_user_id/name`. **NÃO tem `execution_id`.** |
| Tabela `executions` | `db/tables.py:533-569` | `agent_key` (só o ÚLTIMO hop, :546), `total_tokens`/`total_cost_usd` (populados, :547-548), `routing_steps` JSON (:549), `input_text`/`output_text`/`msg_id`/`has_ai` (:562-565). |
| Tabela `execution_steps` | `db/tables.py:572-583` | `step_type`, `data` JSON, `agent_key` por-passo (:581). Cascade `ON DELETE` da execução. |
| Tabela `usage` | `db/tables.py:146-160` | Só `contact_id`+`call_type`+`model`+tokens+`ts`. **Sem `execution_id`/`agent_key`/`msg_id`.** |
| Save da resposta da IA | `app/services/messaging_service.py:434-438` | `save_assistant_message(phone, part, msg_id=part_msg_id, status="sent", channel_id=…, agent_key=agent_key)` — POR parte do split. |
| `save_assistant_message` | `agent/handler.py:364-376` | → `contact.add_message("assistant", text, msg_id=…, status=…, agent_key=…)`. |
| `add_message` | `agent/memory.py:367-400` | → `message_repo.add(self.id, role, content, …, agent_key=…)` (:393-400). |
| `message_repo.add` | `db/repositories/message_repo.py:15-47` | Assinatura por kwargs; INSERT em `messages`. Ponto final da cadeia. |
| execution_id corrente | `agent/execution.py:185-187` | `get_current_execution_id()` já exportado — lê o contextvar `_current_execution`. |
| Denormalização do output | `app/services/messaging_service.py:456` | `aset_execution_texts(output_text=full_reply[:2000])`. **NÃO grava o msg_id da resposta** em `executions`. |
| Captura EXATA (prompt+histórico) | `agent/agno_engine.py:114-141` (`_capture_llm_context`) | Único passo que guarda system prompt EXATO + array de mensagens EXATO. **Gated por `_context_capture_enabled()` (:92-98), default OFF.** Um step `llm_context` POR hop. Trunca 2000/msg, 20000 total, scrub base64 (:86-89, :101-111). |
| Reconstrução (plugin) | `assets/plugin_examples/melhorias/generation.py:104-175` | `_find_execution_around` (match fuzzy por `[started-5, completed+15]`, :104-131), `_tools_used` (:134-142), `_agent_chain` (lê `routing_steps` ou cai em `execution.agent_key`, :145-175). Prompt re-derivado de `agent_repo.get(...)["prompt"]` renderizado (:239-240); histórico re-derivado de `message_repo.get_context_by_conversation(conv_id, N)` (:271). Alvo identificado em `logic.py:126-177` (`content`+`ts`+`_id`=`messages.id`+`conversation_id`). |

⚠️ **Gotchas que tornam o trabalho necessário:**
- ⚠️ **Não há ligação persistida `messages → executions`.** `executions.msg_id` (`:564`) é o msg_id **inbound (do cliente)**, não o da resposta — logo NÃO casa com `messages.msg_id` (resposta). A única ponte é o match por telefone+janela-de-ts, **ambíguo** com turnos próximos (batches, respostas rápidas) — a janela de execuções vizinhas se sobrepõe e `_find_execution_around` pega a **primeira** que casa (pode errar o turno). Selecionar VÁRIAS mensagens (o fluxo do plano 51) multiplica esse risco.
- ⚠️ **Prompt/histórico só são EXATOS com `execution_capture_context=True` (default OFF).** Sem a flag, a reconstrução é uma *aproximação viva* (prompt ATUAL do agente + últimas N mensagens ATUAIS) que **deriva no tempo**.
- ⚠️ `executions.agent_key` é só o agente FINAL. A cadeia real está em `routing_steps` + `execution_steps.agent_key` — o consumidor novo DEVE usar esses, como o `melhorias` já faz.

### 1b. Infra de versionamento (o que existe e o buraco)

| Alvo | Escrita c/ history | Rollback | Onde |
|------|--------------------|----------|------|
| Prompt inline do agente | ✅ | ✅ | `agent_repo.save` (`db/repositories/agent_repo.py:134-228`), trilha dedicada `agent_prompt_repo.record/amend/restore` (`agent_prompt_repo.py:33-222`), tabela `ai_agent_prompt_history` (`db/tables.py:765-776`) |
| Agente inteiro (config) | ✅ | ✅ | `agent_repo.save/rollback/get_snapshot/list_history` + `ai_agents_history` (`db/tables.py:724-733`) |
| Tool code-in-DB | ✅ | ✅ | `tool_repo.save/rollback` + `ai_tools_history` (`db/tables.py:748-757`) |
| **Variável (`ai_variables`)** | ❌ **só upsert** | ❌ **inexistente** | `variable_repo.save` (`db/repositories/variable_repo.py:45-53`) — upsert puro, sem `version`/dedup/history/rollback. Tabela `ai_variables` (`db/tables.py:686-693`) sem coluna `version`. |
| Rotas de variável | GET/PUT/DELETE, **sem history/rollback** | — | `server/routes/ai_engine.py:352-381` |

⚠️ **`ai_variables` é a única entidade de IA sem versionamento de nenhum tipo** (`versionamento.md` §1). Uma melhoria agêntica que altere uma variável **não consegue reverter** — quebra a paridade da D4.

---

## 2. Falsos positivos descartados

| "Parece necessário" | Por que NÃO é (v1) |
|---------------------|---------------------|
| "Precisa ligar `usage` à execução/agente/mensagem para atribuir custo" | **Não no v1.** `usage` não tem `execution_id`/`agent_key`/`msg_id` (`db/tables.py:146-160`), mas o custo do turno já está agregado em `executions.total_tokens/total_cost_usd`. "Custo por agente/por mensagem" é GAP 5 — **adiável** (§6). Não bloqueia a melhoria. |
| "Precisa mudar o funil `ingest_event` para ligar msg_id à execução" | Não. O link se planta no **save de saída** (`messaging_service.py:436` → `message_repo.add`), não no inbound. |
| "Precisa destruncar `tool_executed.result` (hoje 4000 chars, `agno_engine.py:62`)" | **Adiável** (GAP 4). Só relevante se a melhoria precisar do result íntegro de um JSON grande; args ficam completos. |
| "`executions.agent_key` está errado (só o último)" | Não é bug — é agregado. A cadeia está em `routing_steps`/`execution_steps.agent_key`. Só documentar para o consumidor novo. |
| "Reaproveitar `executions.msg_id` para a resposta" | Impossível — está ocupado pelo **inbound** (`db/tables.py:557-560`). Por isso o link novo é coluna em `messages`, não reuso. |
| "Snapshot de variável precisa de JSON de linha inteira" | Não — `ai_variables` só tem `name`/`value`/`updated_at`. Basta o `value` (modelo "blob" de `ai_agent_prompt_history`); mantemos `snapshot` JSON só por consistência com as outras `*_history`. |
| "Patch parcial de agente (só-description) neste plano" | **Adiável.** Não é pré-requisito da melhoria de variáveis; o save inteiro do agente já versiona description/config. |

---

## 3. Fases

### 🔴 Fase 1 — `messages.execution_id` (GAP 1: link preciso) — [bloqueia: gateway de reconstrução do plugin, sub-planos 02+]

**Objetivo:** dado um `msg_id` de resposta da IA, recuperar a execução que a produziu em O(1) e sem ambiguidade, mantendo o fuzzy como plano B.

**Itens:**
1. **[sequencial — PRIMEIRO] Caracterização OBRIGATÓRIA** do fluxo crítico de save de resposta ANTES de tocar: teste que envia uma resposta da IA (com split em 2 partes) e afirma que ambas as `messages` rows são gravadas com `agent_key`, `msg_id`, `status="sent"` e (após a mudança) o mesmo `execution_id`. Base: caminho `messaging_service.py:434-444`. Reutilizar o harness de `tests/` (webhook + LLM mockados). Marcar verde ANTES.
2. **[sequencial] Migration Alembic** nova `db/alembic/versions/20260716_0053_message_execution_id.py`: `ADD COLUMN execution_id INTEGER NULL` em `messages` + índice `CREATE INDEX idx_msg_execution ON messages(execution_id)`. FK **lógica** (sem constraint, igual a `sent_by_user_id`) — execução é log histórico e não deve cascatear/travar o INSERT da mensagem. ⚠️ revision id ≤ 32 chars (`alembic_version.version_num` é varchar(32)); usar `revision = "0053_message_execution_id"` (25 chars, OK).
3. **[sequencial] Coluna no schema Core**: adicionar `Column("execution_id", Integer)` em `messages` (`db/tables.py:108-140`, junto do bloco `sent_by_user_id`) + `Index("idx_msg_execution", messages.c.execution_id)` (perto de `db/tables.py:141-143`).
4. **[sequencial] Propagar o kwarg** pela cadeia de 3 saltos, todos aditivos e nullable:
   - `db/repositories/message_repo.py:15-47` — `add(..., execution_id: int | None = None)` no INSERT (`.values(... execution_id=execution_id)`) e no dict de retorno.
   - `agent/memory.py:367-400` — `add_message(..., execution_id=None)` repassando a `message_repo.add(... execution_id=execution_id)`.
   - `agent/handler.py:364-376` — `save_assistant_message(..., execution_id: int | None = None)` repassando a `contact.add_message(...)`.
5. **[sequencial] Estampar no call site**: `app/services/messaging_service.py:436-438` — passar `execution_id=get_current_execution_id()` (já disponível via `from agent.execution import get_current_execution_id`, `agent/execution.py:185`). Isso cobre o split (todas as partes no mesmo turno herdam o mesmo id).
6. **[paralelo] Helper de leitura O(1)**: em `db/repositories/message_repo.py` (ou `execution_repo.py`) um `get_execution_id_for_msg(msg_id: str) -> int | None` e/ou `find_execution_for_message(msg_row) -> dict | None` que **prefere** `messages.execution_id` e só cai no fuzzy (`_find_execution_around`) quando NULL. (A implementação do fuzzy vira parte da Fase 4.)

**Pronto quando:**
- `alembic upgrade head` aplica a 0053 e `alembic downgrade -1` reverte (round-trip verde).
- O teste de caracterização (item 1), reexecutado, mostra as 2 partes do split com o **mesmo** `execution_id` não-nulo apontando para a execução do turno.
- Dado um `msg_id` de resposta gravado após a mudança, `find_execution_for_message` devolve a execução certa **sem** consultar a janela de ts (verificável por log/branch).
- `venv/bin/python -m pytest tests/test_endpoints.py -q` verde; caminho legado (mensagens sem `execution_id`) continua funcionando via fuzzy.

```
#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-07-16, commit 2853ee2)
- **O que foi feito:** migration 0053 (`messages.execution_id` + `idx_msg_execution`), coluna em `db/tables.py`, kwarg aditivo propagado por `message_repo.add` → `memory.add_message` → `handler.save_assistant_message` → call site `messaging_service` (contextvar lido no contexto async, passado por valor ao to_thread), helper O(1) `message_repo.find_execution_for_message`, `_row_to_dict` expõe o campo.
- **Como foi feito / decisoes:** conforme o plano (FK lógica sem constraint, molde sent_by_user_id). Caracterização em `tests/test_p51_execution_link.py` (split em 2 partes) verde ANTES e DEPOIS.
- **Problemas / pendencias:** nenhum.
- **Verificacao:** 4 testes verdes (partes compartilham o mesmo execution_id; legado NULL degrada p/ None); alembic upgrade/downgrade round-trip verde; suíte de endpoints verde.
```

---

### 🟢 Fase 2 — Captura de contexto exato (GAP 2) — [depende de: —] [bloqueia: —]

**Objetivo:** persistir o system prompt EXATO + o array de mensagens EXATO que o LLM viu, para a melhoria não trabalhar com aproximação que deriva.

**Itens:**
1. **[sequencial] Decisão de default (DL1)**: ligar `execution_capture_context` por default. Duas variantes avaliadas — escolher **(a)**:
   - **(a) default-ON completo (recomendado)**: `_context_capture_enabled()` (`agent/agno_engine.py:92-98`) passa a retornar `True` quando a config está ausente (`config_repo.get("execution_capture_context", True)`). Aproveita a truncagem já existente (2000/msg, 20000 total, scrub base64 — `:86-89`, `:101-111`) e o cascade-prune de `execution_steps`. Mantém o kill-switch para desligar.
   - **(b) variante leve sempre-on**: capturar só o `system_message` (sem o histórico, que é o que mais deriva) num modo separado. **Descartada em v1** — mais código para menos fidelidade; a truncagem de (a) já limita o custo.
2. **[paralelo] Config default + toggle na UI**: garantir que a chave `execution_capture_context` apareça no `allowed_keys`/`DEFAULT_CONFIG` (onde as demais flags de execução moram) com default `True`, e um toggle em Configurações → IA (mesma seção das flags de execução/retenção). Sem mudar a truncagem.
3. **[paralelo] Confirmar per-hop**: a captura já emite um `llm_context` POR hop (`agno_engine.py:139`, chamada em `:570`/`:631`); num turno multi-agente cada agente terá seu contexto exato. Sem mudança — só documentar para o consumidor (Fase 4) casar `llm_context.agent_key` com o hop.
4. **[paralelo] Retrocompatibilidade**: mensagens/execuções ANTERIORES à mudança não têm `llm_context` — a reconstrução (Fase 4) deve detectar a ausência e cair na aproximação viva (prompt atual + histórico atual), sinalizando "aproximado" vs "exato".

**Pronto quando:**
- Numa instalação fresh (config sem a chave), um turno de IA grava um step `llm_context` com `messages[0].role=="system"` e o array do histórico; verificável via `GET` de execução / `execution_repo.get_by_id`.
- Desligar `execution_capture_context` (PUT config) faz o próximo turno NÃO gravar `llm_context` (kill-switch intacto).
- Turno multi-agente (roteador→spoke) grava um `llm_context` por hop, cada um com seu `agent_key`.
- Suíte verde; nenhuma regressão no tamanho de payload (truncagem inalterada).

```
#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-07-16, commit 04af9e9)
- **O que foi feito:** `execution_capture_context` default ON nos dois pontos (`agno_engine._context_capture_enabled` fallback True + `config/settings.py` ConfigKey default True).
- **Como foi feito / decisoes:** variante (a) default-ON completo (DL1); truncagem/scrub intactos; kill-switch preservado (toggle já existia no painel Execuções — nenhuma UI nova).
- **Problemas / pendencias:** instalações existentes que já persistiram a chave como False continuam OFF até o operador ligar (comportamento esperado do config seed).
- **Verificacao:** teste de default (chave ausente ⇒ True; False explícito ⇒ False) + tests/endpoints/test_p36_executions.py + test_context_dedup.py verdes.
```

---

### 🟢 Fase 3 — Versionamento de `ai_variables` (ponto cego / D4) — [depende de: —] [bloqueia: —]

**Objetivo:** dar a `ai_variables` a mesma infra de history+rollback que `ai_agents`/`ai_tools`/prompt já têm, para a IA poder editar e **reverter** uma variável.

**Itens:**
1. **[sequencial] Migration Alembic** `db/alembic/versions/20260716_0054_ai_variables_versioning.py`:
   - `ADD COLUMN version INTEGER NOT NULL DEFAULT 1` em `ai_variables`.
   - `CREATE TABLE ai_variables_history (id PK autoinc, name TEXT NOT NULL, version INTEGER NOT NULL, snapshot TEXT NOT NULL, created_at FLOAT NOT NULL)` + `CREATE INDEX idx_ai_variables_hist ON ai_variables_history(name, version)`. Molde exato: `ai_tools_history`/`ai_agents_history` (`db/tables.py:724-757`). ⚠️ revision id `"0054_ai_variables_versioning"` (28 chars, OK).
2. **[paralelo] Schema Core**: `Column("version", Integer, nullable=False, server_default="1")` em `ai_variables` (`db/tables.py:686-693`) + novo `Table("ai_variables_history", …)` + `Index` (junto ao bloco de history, após `db/tables.py:757`).
3. **[sequencial] Repo `db/repositories/variable_repo.py:45-53`** — evoluir no molde de `tool_repo`/`agent_repo`:
   - `save(name, value)` — ler a row atual; **dedup** (se `value` idêntico, no-op, sem bump/history); senão `version = existing+1`, upsert + INSERT em `ai_variables_history` (`snapshot=json.dumps({"name": name, "value": value})`), numa transação.
   - `list_history(name) -> [{version, created_at}]` (newest-first), `get_snapshot(name, version) -> dict|None`, `rollback(name, version)` (lê snapshot e **re-aplica via `save()`** como nova versão — forward, não-destrutivo, igual a `agent_repo.rollback`/`tool_repo.rollback`).
   - `delete(name)` (`variable_repo.py:56-59`) mantém — decidir se apaga history junto (recomendado: sim, como `agent_repo.delete`).
4. **[sequencial] Rotas** em `server/routes/ai_engine.py` perto de `:352-381` (bloco Variables), permissão `agent.variables.manage`, cada mutação chamando `_emit_changed("variable", name)`:
   - `GET /api/ai/variables/{name}/history`
   - `POST /api/ai/variables/{name}/rollback/{version}`
   - (opcional) `GET /api/ai/variables/{name}/history/{version}` (snapshot) e `.../diff` — paridade com prompt; pode ser adiado.
   - `PUT /api/ai/variables/{name}` (`:359-372`) passa a bumpar versão via o novo `save` (transparente — mesma assinatura).

**Pronto quando:**
- `alembic upgrade head`/`downgrade -1` round-trip verde para a 0054.
- `PUT /api/ai/variables/{name}` duas vezes com valores diferentes cria 2 versões; com o mesmo valor NÃO cria versão-lixo (dedup).
- `GET .../history` lista as versões; `POST .../rollback/{v}` restaura o valor antigo como uma **nova** versão (forward) e `dynamic_registry.invalidate()` faz a próxima mensagem render com o valor revertido (sem restart).
- Teste de repo cobrindo save→history→rollback→dedup verde; suíte de endpoints verde.

```
#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-07-16, commit a8f4eae)
- **O que foi feito:** migration 0054 (`ai_variables.version` + tabela `ai_variables_history` + índice), `variable_repo` com save dedup+bump+snapshot numa transação, `list_history`/`get_snapshot`/`rollback` (forward), `delete` limpa a trilha; rotas GET history / GET snapshot / POST rollback com `agent.variables.manage` + `_emit_changed`.
- **Como foi feito / decisoes:** molde exato de tool_repo/ai_tools_history; snapshot JSON `{name, value}`.
- **Problemas / pendencias:** nenhum.
- **Verificacao:** 5 testes (repo save/dedup/rollback/delete + endpoints) verdes; round-trip alembic 0054 verde.
```

---

### 🟢 Fase 4 — Extrair helpers de trace para módulo reutilizável (DL3) — [depende de: 1 (opcional — usa o link; cai no fuzzy se ausente)] [bloqueia: gateway do plugin, sub-planos 02+]

**Objetivo:** uma fonte de verdade no core para "dado um msg_id/row → agente, cadeia, tools, prompt, histórico, contexto exato", consumida pelo gateway do plugin e pela feature "Gerar melhoria" já existente, em vez de duplicar a lógica no plugin.

**Itens:**
1. **[sequencial] Criar `app/services/execution_trace.py`** (ou `agent/execution_trace.py`) com funções **puras** portadas de `assets/plugin_examples/melhorias/generation.py`:
   - `find_execution_for_message(msg_row) -> dict | None` — **prefere** `messages.execution_id` (Fase 1) → `execution_repo.get_by_id`; fallback `_find_execution_around(phone, ts)` (fuzzy `[started-5, completed+15]`, `generation.py:104-131`) quando NULL.
   - `tools_used(execution) -> [{tool, args, agent_key}]` (portar `generation.py:134-142`).
   - `agent_chain(execution) -> [agent_key,…]` (portar `generation.py:145-175`, lê `routing_steps` ou cai em `execution.agent_key`).
   - `exact_context(execution) -> {system, messages} | None` — ler o step `llm_context` (Fase 2) por hop; `None` quando não capturado (sinaliza "aproximado").
   - `approx_context(agent_key, conversation_id, n) -> {prompt, history}` — a aproximação viva: prompt inline via `agent_repo.get(agent_key)["prompt"]` renderizado com `variable_repo.as_map()` (molde `generation.py:239-240`) + `message_repo.get_context_by_conversation(conv_id, N)` (`generation.py:271`). Reutiliza `render_template` de `agent/agent_factory.py:113-127`.
2. **[paralelo] Afinar o plugin** `melhorias/generation.py` para **importar** do módulo do core (via `from app.services import execution_trace`) em vez de manter cópias locais — mantendo a assinatura pública do plugin intacta. ⚠️ o plugin instalado é gitignored (chega por `.zip` re-importado, D6); a **fonte** git a alterar é `assets/plugin_examples/melhorias/generation.py`.
3. **[paralelo] Bundle de trace tipado** (opcional, recomendado): `reconstruct_for_message(msg_row) -> TraceBundle` reunindo agente(s) + tools + contexto (exato OU aproximado, com flag `exact: bool`) + tokens/custo (`executions.total_*`) — a API única que o gateway consome. Documentar que `agent_key` por mensagem já vem de `messages.agent_key` (zero core), e o resto vem da execução.

**Pronto quando:**
- Um teste unit do módulo (fixtures de execução com/sem `execution_id` e com/sem `llm_context`) mostra: link preciso quando presente, fuzzy quando ausente; `exact_context` retorna o capturado quando há `llm_context` e `None` senão; `agent_chain` reconstrói roteador→spoke a partir de `routing_steps`.
- A feature "Gerar melhoria" existente (`app/services/improvement_service.py`) e o plugin `melhorias` continuam produzindo a mesma reconstrução (sem regressão), agora chamando o módulo compartilhado.
- Nenhum `if provider ==` / lógica de negócio nova no core além da reconstrução; suíte verde.

```
#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-07-16, commit 760f336)
- **O que foi feito:** `app/services/execution_trace.py` (find_execution_around fuzzy, find_execution_for_message link→fuzzy, tools_used, agent_chain, exact_context por hop, approx_context, reconstruct_for_message com flags linked/exact); plugin `melhorias/generation.py` importa do core via aliases finos; `message_repo.get_by_db_id` adicionado (leitura por id interno).
- **Como foi feito / decisoes:** tools_used passou a incluir `result` (aditivo); bundle TraceBundle implementado como dict documentado.
- **Problemas / pendencias:** nenhum.
- **Verificacao:** 5 testes unit (link vs fuzzy; exato por hop roteador→spoke; degradação) + test_melhorias_plugin.py sem regressão.
```

---

## 4. Ordem, paralelização e dependências

```
Fase 1  messages.execution_id        🔴 (caracterização ANTES; toca save crítico)  [bloqueia 02+ e enriquece 4]
Fase 2  captura de contexto exato    🟢 (independente)
Fase 3  versionamento ai_variables   🟢 (independente)
Fase 4  extrair módulo de trace      🟢 (consome 1 e 2; cai no fuzzy/aproximação se ausentes)
```

- **Fase 1 é a única 🔴** — mexe no fluxo compartilhado de save de resposta (`messaging_service` → `message_repo`), exige caracterização e um refactor por commit. Faça-a sozinha.
- **Fases 2 e 3 são 🟢 e totalmente independentes entre si e da 1** — podem ir em paralelo (2 é 1 flag + toggle; 3 é versionamento isolado no molde existente).
- **Fase 4 é 🟢 mas rende mais depois de 1 e 2** (link preciso + contexto exato); funciona mesmo antes delas via fallback (DL2) — priorize-a por último para consumir o que 1/2 plantam.

---

## 5. O que é v1 vs. adiável

| Item | v1? | Nota |
|------|-----|------|
| `messages.execution_id` + propagação + helper O(1) (Fase 1) | ✅ v1 | Mudança de core mais valiosa e barata; desambigua a seleção de múltiplas mensagens. |
| Ligar `execution_capture_context` por default (Fase 2) | ✅ v1 | Fidelidade do prompt/histórico; só vale para o que for capturado a partir de agora. |
| Versionamento de `ai_variables` (Fase 3) | ✅ v1 | Paridade da D4 (a IA precisa reverter variável). |
| Extrair `execution_trace` (Fase 4) | ✅ v1 | Fonte única para o gateway; evita duplicar no plugin. Pode ser reduzida a portar só os 3 helpers se o cronograma apertar. |
| `usage.execution_id`/`agent_key`/`msg_id` (custo por agente/mensagem) — GAP 5 | ⏸️ adiável | Custo do turno já em `executions.total_*`; desdobrar por agente é feature futura. |
| Destruncar `tool_executed.result` (>4000 chars) — GAP 4 | ⏸️ adiável | Só se a melhoria precisar do result íntegro; args já completos. |
| Patch parcial de agente (só-description) + snapshot/diff de tool e agente-inteiro por versão | ⏸️ adiável | O save inteiro já versiona; não bloqueia variáveis. |
| `change_note`/autor no snapshot de `ai_agents_history`/`ai_tools_history` (atribuir a mudança ao "agente de melhoria") | ⏸️ adiável | Bom para auditoria; hoje o `note` só existe na trilha de prompt. |

**Caracterização OBRIGATÓRIA:** apenas a **Fase 1** (fluxo crítico de save de resposta). Fases 2–4 são cobertas por testes de unidade/endpoint novos, sem caracterização de fluxo compartilhado.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Save de resposta compartilhado (Fase 1) | Um kwarg mal propagado quebra a persistência de TODA resposta da IA | Caracterização ANTES; kwarg **aditivo e nullable** em cada salto; um refactor por commit; verde antes/depois. |
| Migration tocada com `--reload` no dev | ⚠️ `whatsbot.service` (uvicorn `--reload` observando `db/`) roda `alembic upgrade head` na **DB VIVA** ao salvar a migration; renomear uma migration já aplicada quebra o boot | Criar as migrations 0053/0054 com revision **novo** (nunca renomear aplicada); se o dev server estiver rodando, esperar ele aplicar ou pausar o serviço ao editar. |
| Revision id > 32 chars | `alembic_version.version_num` é varchar(32) — id longo compila mas estoura no upgrade | Usar `0053_message_execution_id` (25) e `0054_ai_variables_versioning` (28); nome descritivo longo vai só no arquivo. |
| `execution_id` NULL em massa (legado) | Reconstrução via link falharia para mensagens antigas | Fallback fuzzy mantido (DL2); `find_execution_for_message` sempre degrada, nunca lança. |
| Tamanho de `llm_context` no Postgres (Fase 2) | Contexto exato por-hop pode inchar `execution_steps` | Truncagem já existe (2000/msg, 20000 total, scrub base64); `execution_steps` cascateiam no prune de execuções (`agent/execution.py:190-199`). |
| Ambiguidade residual do fuzzy | Turnos muito próximos ainda casam a execução errada quando `execution_id` é NULL | O link da Fase 1 resolve para tudo gravado a partir de agora; documentar que seleções de mensagens antigas podem ser aproximadas. |
| Dedup de variável (Fase 3) | Save repetido com mesmo valor criaria versões-lixo | Dedup no `variable_repo.save` (molde `tool_repo.py:80-99` / `agent_prompt_repo.record` dedup). |
| Plugin instalado é gitignored (Fase 4) | Editar a cópia instalada não versiona; worktree "limpo" engana | Alterar a **fonte** `assets/plugin_examples/melhorias/`; distribuir via `.zip` re-importado (D6). Import do core no plugin exige que `app.services.execution_trace` esteja no path do processo (está — mesmo processo). |
| Teste em Postgres | ⚠️ `CREATE DATABASE` herda `SQL_ASCII` em alguns servidores; 2 pytest no mesmo banco colidem (DROP SCHEMA por processo) | `WHATSBOT_TEST_DB_URL` com banco `*test*` UTF8 (`ENCODING 'UTF8' TEMPLATE template0`); um pytest por banco. |

---

## 7. Checklist de verificação

- [ ] `venv/bin/python -m pytest tests/test_endpoints.py -q` verde no **Postgres** (`WHATSBOT_TEST_DB_URL`, banco com `test` no nome, UTF8).
- [ ] Caracterização do save de resposta da IA (split em 2 partes) verde ANTES e DEPOIS da Fase 1.
- [ ] `alembic upgrade head` + `alembic downgrade -1` round-trip para 0053 (execution_id) e 0054 (ai_variables).
- [ ] Resposta da IA gravada com `messages.execution_id` não-nulo; as N partes de um split compartilham o mesmo id.
- [ ] `find_execution_for_message` usa o link quando presente e cai no fuzzy quando `execution_id` é NULL (verificável).
- [ ] Com `execution_capture_context` default ON, um turno grava `llm_context` (system + array exato); kill-switch OFF suprime; multi-agente grava 1 por hop.
- [ ] `PUT /api/ai/variables/{name}` versiona; valor idêntico é no-op (dedup); `GET .../history` lista; `POST .../rollback/{v}` reverte forward e vale na próxima mensagem sem restart.
- [ ] Módulo `execution_trace` unit-testado (link vs fuzzy; exato vs aproximado; cadeia roteador→spoke); "Gerar melhoria" e o plugin `melhorias` sem regressão consumindo o módulo compartilhado.
- [ ] Nenhum `if provider ==`/lógica de canal nova no core; permissões (`agent.variables.manage`) respeitadas nas rotas novas.

---

## 8. Status de execução — Sub-plano 01

```
#### Status de execucao - Sub-plano 01
**Estado:** ✅ Concluído (2026-07-16 — 4 fases, commits 2853ee2/04af9e9/a8f4eae/760f336)
- **O que foi feito:** as 4 fases (execution_id, captura exata ON, versionamento de variáveis, módulo execution_trace).
- **Como foi feito / decisoes:** DL1 (default-ON completo), DL2 (fuzzy mantido), DL3 (módulo em app/services) confirmadas sem desvio.
- **Problemas / pendencias:** GAPs 4/5 (usage por agente, destruncar result) seguem adiáveis como planejado.
- **Verificacao:** suítes tests/test_p51_* + endpoints + characterization afetadas verdes; migrations 0053/0054 round-trip.
```
