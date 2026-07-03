# Plano 30 — Motor de IA: tool `transferir_agente` (OFF-default · excluível · spoke→router) · descrição do agente no roteador · bug dos cards de tool ao vivo

> **Status:** EXECUTADO (branch feat/plano-30, 2026-07-03) · **Data:** 2026-07-03 · **Escopo:** médio (5 workstreams: 1 bug de UI real-time + 4 melhorias no hub-and-spoke; 1 migration nova)
>
> **Origem:** pedido do Thiago nesta sessão, em cima de uma investigação read-only concluída (nenhum código alterado) + workflow de 4 investigadores paralelos. Continuação natural do [Plano 29](29-plano-motor-agentes-guardrails-e-postgres-only.md) (que montou o hub-and-spoke). Aqui: (WS1) card painel-only `tool_call` some ao vivo e só volta no F5; (WS2) `transferir_agente` deve **nascer desligada**; (WS3) a tool deve ter **botão Excluir de verdade**; (WS4) **subagente só devolve pro roteador**; (WS5) a **descrição** de cada agente deve chegar ao roteador.
> **Método:** leitura do código real no branch `developer` + workflow multi-agente. Todos os `arquivo:linha` abaixo foram verificados (head Alembic = `0036_atend_open_unique`; nova migration encadeia em `0037`).
>
> **Como usar este plano:** ao executar cada fase, preencha o bloco **"Status de execução"** dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro. Verde a cada fase; **caracterização ANTES** de mexer no broadcast de mensagens (WS1) e no seed de tools (WS2/WS3); **um refactor por commit**.
>
> Legenda de estado de execução: `⬜ Não iniciada` · `🟡 Em andamento` · `✅ Concluída` · `⛔ Bloqueada`.
> Legenda de paralelização: `🟢 PODE AGRUPAR` (sem dependência) · `🔴 FAÇA SOZINHA` (sequencial/bloqueante).

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | **"Selecionar apenas um roteador" fica como está.** O backend já garante *no-máximo-um* (índice único parcial `ux_ai_agents_single_router` + semântica radio em `agent_repo.save` + `_demote_other_routers`) e a UI tem checkbox "É roteador" com aviso de rebaixamento. | **Fora de escopo.** Nenhuma fase mexe nisso (só é usado como leitura via `get_router()` no WS4). Não forçamos "exatamente-um" (zero routers continua permitido). |
| D2 | **"Excluível" = DELETE REAL**, não apenas desabilitar. O Thiago aceita o risco de apagar uma tool core por engano ("por enquanto não é problema"). | WS3 precisa de **tombstone** para o delete sobreviver ao re-seed no boot, e de **relaxar o guard** que hoje bloqueia excluir builtin. |
| D3 | **`transferir_agente` nasce DESLIGADA** (só ela, por ora). As demais core tools (`save_contact_info`, `transfer_to_human`, `set_custom_attribute`) continuam nascendo ligadas. | WS2 introduz um conjunto `OFF_BY_DEFAULT_TOOLS = {"transferir_agente"}` consultado nos DOIS seeds. **Só afeta instalações NOVAS** (P2 = B); bancos existentes ficam intocados — **sem migration** para WS2. |
| D7 | **P2 = B: só instalações novas.** Bancos existentes (com a tool já ligada) NÃO são desligados. | WS2/F3 perde a migration one-time; vira só mudança de default nos seeds. |
| D8 | **P3 = B: sem botão de reinstalar** (mais simples). Quem apagar `transferir_agente` recria pela UI (como tool code-in-DB) ou mexe no `config` direto. | WS3/F4 não implementa reinstalação; o tombstone é definitivo pela UI. |
| D4 | **Subagente (spoke) só pode devolver pro roteador.** Roteador continua podendo mandar pra qualquer destino da sua allowlist. | WS4 é puramente server-side em `transferir_agente.py::execute` (cobre IA e operador). |
| D5 | **Bug do card (WS1) é prioridade 1** — isolado, alto impacto, correção de ~1 função com padrão de referência já validado (`private_note`). | WS1 vai sozinho na frente; não depende de nenhum outro WS. |
| D6 | **Não está em produção distribuída** ⇒ refactor direto, sem stopgap de compatibilidade (mesma premissa do Plano 29 D4). | Migration one-time pode desligar a tool sem camada de compat. |

---

## 1. Resumo executivo

Cinco frentes independentes, uma delas um bug e quatro melhorias no motor hub-and-spoke:

- **WS1 (bug, prioridade 1).** O card painel-only `tool_call` ("🔧 nome_da_tool …") é broadcastado ao vivo com um `conversation_id` **resolvido pela função errada** (`get_open_for_contact`, que não filtra por canal). Quando o contato tem mais de uma conversa aberta, o id diverge do que o painel tem aberto e o frontend descarta o card silenciosamente — ele só reaparece no F5 (que lê do banco). Fix: usar o `conversation_id` da **linha salva** (`add_message` já o devolve correto), espelhando o `private_note`. Hardening opcional no frontend (OR-fallback).
- **WS2.** `transferir_agente` nasce **ligada** hoje (dois seeds põem `enabled=1`/`True`). Fazer nascer **OFF** — consistente nos dois seeds. **Só afeta instalações novas** (D7); bancos existentes ficam como estão (sem migration).
- **WS3.** Dar à `transferir_agente` um botão **Excluir** real, como as tools code-in-DB. Exige tombstone (senão o boot re-seeda) + relaxar o guard da rota DELETE + liberar o botão no frontend.
- **WS4.** Hoje um spoke pode transferir pra **qualquer** agente; a restrição "devolve pro roteador" é só texto na UI. Enforçar server-side em `execute()`.
- **WS5.** A coluna `ai_agents.description` existe, é editável e versionada, mas **nunca chega ao LLM**. Injetá-la no system prompt do roteador (seção "Agentes disponíveis para transferência").

**Nenhuma migration nova** (D7 tirou o one-time disable do WS2; o tombstone do WS3 reusa a tabela `config`, sem coluna nova). Tudo é código + seed defaults.

---

## 2. Como funciona hoje (mapa) — `arquivo:linha` verificado

### 2.1 WS1 — broadcast do card `tool_call`
- **Save + broadcast (único site):** `MessagingService.broadcast_tool_calls` ([app/services/messaging_service.py:448-492](../app/services/messaging_service.py#L448-L492)). Resolve `conv_id` **uma vez** no topo via `conversation_repo.get_open_for_contact(contact.id)` ([:463-468](../app/services/messaging_service.py#L463-L468)); por tool: `contact.add_message("tool_call", content)` ([:484](../app/services/messaging_service.py#L484), **retorno descartado**) + monta `tc_message` com `ts=time.time()` e `conversation_id=conv_id` ([:485-487](../app/services/messaging_service.py#L485-L487)) + `broadcast("new_message", …)` ([:488-492](../app/services/messaging_service.py#L488-L492)).
- **Chamadas:** texto ([:834](../app/services/messaging_service.py#L834)) e mídia ([:977](../app/services/messaging_service.py#L977)), antes do envio da resposta ([:843](../app/services/messaging_service.py#L843)).
- ⚠️ **`get_open_for_contact` NÃO é inbox/canal-aware:** existe um `get_open_for_contact_inbox(contact_id, inbox_id)` **separado** ([db/repositories/conversation_repo.py](../db/repositories/conversation_repo.py) — as duas assinaturas coexistem) justamente porque a versão sem inbox devolve a conversa aberta mais recente **de qualquer canal**.
- **`add_message` devolve o id correto:** ([agent/memory.py](../agent/memory.py) `add_message`) resolve `conversation_id` **inbox-aware** (`resolve_for_contact_ex(inbox_id=self.inbox_id)`) e **retorna a linha salva** (`saved` com `conversation_id`, `ts`, `id`). Hoje `broadcast_tool_calls` ignora esse retorno.
- **Padrão correto de referência:** `private_note` em [server/routes/contacts.py:1043-1058](../server/routes/contacts.py#L1043-L1058) — captura `saved_note = contact.add_message("private_note", p)` e monta o payload com `saved_note.get("conversation_id")`, `saved_note.get("ts")` e `_id=saved_note["id"]`. **É exatamente o que o WS1 deve replicar.**
- **Frontend descarta por id divergente:** `useConversationWsEvents.js` ([:387-459](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L387-L459)). O roteamento ([:393-401](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L393-L401)) é um **ternário exclusivo**: com `selectedConvId != null` **e** `msgConvId != null`, casa **só** por `msgConvId === selectedConvIdRef.current` — **sem** fallback phone+channel. Id divergente ⇒ `belongsToOpen=false` ⇒ card nunca anexado. Dedupe do append por `ts+role`/`content+role` (30s) em [:430-444](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L430-L444) via `sameMessage` ([web/static/js/services/messages.js:41-46](../web/static/js/services/messages.js#L41-L46)).
- **Assimetria que confirma o diagnóstico:** a resposta da IA ([messaging_service.py:400-405](../app/services/messaging_service.py#L400-L405)) broadcasta **SEM** `conversation_id` → cai no ramo phone+channel → sempre aparece. Idem outros cards que usam o id da linha salva. Só o `tool_call` usa o id "errado". Regressão introduzida no commit **89e41a4** (que adicionou `conversation_id` ao card pra consertar o roteamento no Telegram — a intenção estava certa, a fonte do id estava errada).

### 2.2 WS2 — `transferir_agente` nasce ligada
- **Registro core:** `TRANSFERIR_AGENTE_TOOL` em [agent/tools/transferir_agente.py:19-46](../agent/tools/transferir_agente.py#L19-L46), listada em `CORE_TOOLS` ([agent/tools/__init__.py:35-40](../agent/tools/__init__.py#L35-L40)). Ao registrar, `AgentHandler.register_tool` chama `tool_override_repo.ensure(name, None)` ([agent/tool_registry.py:94](../agent/tool_registry.py#L94)).
- **`ensure` cria enabled=1:** [db/repositories/tool_override_repo.py:51-72](../db/repositories/tool_override_repo.py#L51-L72) (upsert com `"enabled": 1`, `update_cols=['plugin_id']` → **não regride** rows já existentes). Coluna `tool_overrides.enabled` tem `server_default="1"` ([db/tables.py](../db/tables.py) `tool_overrides`).
- **Seed builtin:** `seed_builtin_tools()` ([agent/ai_builtin_tools.py:101-128](../agent/ai_builtin_tools.py#L101-L128)) insere a row em `ai_tools` com `enabled=True`, `kind="builtin"` — só quando `tool_repo.get(name) is None` ([:110-111](../agent/ai_builtin_tools.py#L110-L111)). `transferir_agente` está em `BUILTIN_MODULES` ([:47-52](../agent/ai_builtin_tools.py#L47-L52)).
- **Dois gates precisam concordar:** `register_builtin_overrides` ([agent/ai_builtin_tools.py:129-168](../agent/ai_builtin_tools.py#L129-L168)) desregistra do handler se `ai_tools.enabled=0`; `refresh_tool_overrides` ([agent/tool_registry.py:178](../agent/tool_registry.py#L178)) remove do schema se `tool_overrides.enabled=0`. Se um seed disser OFF e o outro ON, o comportamento fica inconsistente.
- **Agente `default` vê todas:** `agent_repo.ensure` seeda `default` com `tool_names=None` (= todas as tools registradas); `tool_registry.select_active_tools` trata `None` como "todas". Então basta a tool estar registrada pra entrar no schema do LLM.

### 2.3 WS3 — delete real de builtin
- **Rota já existe, mas bloqueia builtin:** `DELETE /api/ai/tools/{name}` ([server/routes/ai_engine.py:360-371](../server/routes/ai_engine.py#L360-L371)) — **guard explícito**: `if existing.get("kind") == "builtin": return _err("Tools core não podem ser excluídas…", 400)` ([:363-364](../server/routes/ai_engine.py#L363-L364)). Fora isso chama `tool_repo.delete(name)` ([:366](../server/routes/ai_engine.py#L366)) + `schedule_restart`.
- **`tool_repo.delete` só apaga a row `ai_tools`:** [db/repositories/tool_repo.py:181-185](../db/repositories/tool_repo.py#L181-L185) — não toca `tool_overrides` nem impede re-seed.
- **Re-seed no boot (dois caminhos):** (1) `seed_builtin_tools` re-insere a row `ai_tools` (name em `BUILTIN_MODULES`); (2) `register_tool` (via `CORE_TOOLS`) chama `tool_override_repo.ensure` ([agent/tool_registry.py:94](../agent/tool_registry.py#L94)) recriando a row `tool_overrides`. **Um delete simples volta no próximo boot pelos dois lados.**
- **`tool_override_repo` não tem delete-by-name:** só `delete_for_plugin` / `delete_orphans(known_names)` ([db/repositories/tool_override_repo.py:108-125](../db/repositories/tool_override_repo.py#L108-L125)).
- **Frontend gate do botão Excluir:** `ToolsUnified.js` — `kind = code ? (code.kind||'code') : (reg.plugin_id ? 'plugin' : 'core')` ([:135-137](../web/static/js/components/ai/ToolsUnified.js#L135-L137)); botão Excluir só renderiza para `r.kind === 'code'` ([:406-411](../web/static/js/components/ai/ToolsUnified.js#L406-L411)). `transferir_agente` cai em `kind='builtin'` → hoje sem botão (bate com o screenshot). Serviço `deleteTool` já existe ([web/static/js/services/api.js:875-877](../web/static/js/services/api.js#L875-L877)).

### 2.4 WS4 — spoke→router não é enforçado
- **`execute` só valida allowlist quando o atual é router:** [agent/tools/transferir_agente.py:70-77](../agent/tools/transferir_agente.py#L70-L77) — `if current and current.get("is_router") and targets and target not in targets: return "Erro…"`. Um spoke (`is_router=False`) **pula toda validação** e transfere pra qualquer agente enabled.
- **`get_router()` existe:** [db/repositories/agent_repo.py:221-227](../db/repositories/agent_repo.py#L221-L227) (retorna o único router ou `None`).
- **Freios de loop já presentes:** `_DEFAULT_HOOKS = {"transferir_agente": {"call_limit": 1}}` ([ai_engine/hooks.py:32](../ai_engine/hooks.py#L32)), `ESCAPE_TOOL="transferir_agente"` ([:37](../ai_engine/hooks.py#L37)); revisita controlada + cap de depth em `run_with_routing` ([ai_engine/routing.py:39-86](../ai_engine/routing.py#L39-L86)).
- **Convenção é só UI:** aviso textual em `AgentsManager.js` ([:368-371](../web/static/js/components/ai/AgentsManager.js#L368-L371)) — zero enforcement.

### 2.5 WS5 — descrição não chega ao roteador
- **Coluna existe e é inerte:** `ai_agents.description` (`Text NOT NULL server_default=""`) em [db/tables.py:580](../db/tables.py#L580) — comentário literal "consumido pelo motor de routing **futuro**". Persistida/versionada/snapshotada em `agent_repo.save`.
- **Form já tem o campo:** `AgentsManager.js` — state [:148](../web/static/js/components/ai/AgentsManager.js#L148), textarea [:377-380](../web/static/js/components/ai/AgentsManager.js#L377-L380), envio [:242](../web/static/js/components/ai/AgentsManager.js#L242), exibição no card [:676](../web/static/js/components/ai/AgentsManager.js#L676).
- **Nunca lido na montagem do prompt:** `agent_factory.build_for_contact` ([agent/agent_factory.py:205-300](../agent/agent_factory.py#L205)) lê `prompt`/`model_config`/`tool_names`, e passa `description`/`is_router`/`routing_targets` ao `AgentSpec` ([:155-159](../agent/agent_factory.py#L155-L159)) **mas ninguém consome depois**. `prompt_builder` ([agent/prompt_builder.py](../agent/prompt_builder.py)) monta seções dinâmicas mas **não lista agentes-destino**.
- **Tool sem enum:** o param `agente` é string livre ([agent/tools/transferir_agente.py:34-37](../agent/tools/transferir_agente.py#L34-L37)); o LLM só descobre chaves válidas pela mensagem de erro pós-chamada ([:57-61](../agent/tools/transferir_agente.py#L57-L61)).

---

## 3. Inventário / análise

| WS | O que falta | Onde (arquivo:linha) | Abordagem | Risco | Esforço |
|----|-------------|----------------------|-----------|-------|---------|
| WS1 | Card `tool_call` usa `conversation_id` errado | `messaging_service.py:463-492` | Capturar `saved = add_message(...)` e usar `saved["conversation_id"]/["ts"]/["id"]`; dropar o `get_open_for_contact` do topo | baixo | S |
| WS1b | Frontend descarta card por id divergente (defensivo) | `useConversationWsEvents.js:393-401` | Ternário exclusivo → OR-fallback (conv_id **ou** phone+channel) | baixo | S |
| WS2 | Tool nasce ligada nos dois seeds | `tool_override_repo.py:51-72`, `ai_builtin_tools.py:101-128` | `OFF_BY_DEFAULT_TOOLS = {"transferir_agente"}` consultado em ambos; **sem migration** (só instalações novas, D7) | baixo | S |
| WS3 | Delete real de builtin (tombstone + guard + UI + skip re-seed) | `ai_engine.py:360-371`, `ai_builtin_tools.py:101-128`, `tool_registry.py:94`, `tool_override_repo.py`, `ToolsUnified.js:406-411` | Tombstone em `config` (`deleted_builtin_tools`), checado por `seed_builtin_tools` + `ensure`; relaxar guard; add `delete(name)` no override_repo; liberar botão | **alto** | L |
| WS4 | Spoke transfere pra qualquer agente | `transferir_agente.py:70-77` | Ramo novo: atual não-router ⇒ destino permitido = só `get_router()` | médio | S |
| WS5 | Descrição não chega ao roteador | `agent_factory.py:205-300` / `prompt_builder.py` | Injetar seção "Agentes disponíveis para transferência" no prompt quando `is_router` | médio | M |

### 3.1 Falsos positivos descartados

| Suspeita | Por que NÃO é o problema |
|----------|--------------------------|
| "Backend não broadcasta o card ao vivo" | Broadcasta sim ([messaging_service.py:488](../app/services/messaging_service.py#L488)); o problema é o `conversation_id` do payload. |
| "Frontend filtra role `tool_call` no append" | Não filtra por role — anexa qualquer role ([useConversationWsEvents.js:445-449](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L445-L449)); render OK (`SYSTEM_CARD_VARIANTS.tool_call`). |
| "Dedupe `ts+role` come o card" | Cards de tool têm conteúdo distinto e ts do banco; risco só se usar `time.time()` no lugar de `saved.ts` — por isso o WS1 usa `saved.ts`. |
| "Selecionar um roteador está quebrado" | Backend garante no-máximo-um em 3 camadas (D1). Fora de escopo. |
| "Basta desabilitar a tool (WS3)" | Decisão D2 é delete real; disable já existe e não atende o pedido. |
| "`register_builtin_overrides` é gated pelo kill-switch `ai_tools_code_enabled`" | Não — builtins são core, rodam sempre; o kill-switch só vale para `kind='code'`. |

---

## 4. Fases / Roadmap

```
WAVE 0 (paralelo)   F1 → F1b        ← WS1: backend primeiro, frontend complementa
                    F3 · F4          ← WS2 e WS3 TOCAM o mesmo arquivo (ai_builtin_tools.py) → coordenar (mesma PR ou sequência)
                    F5               ← WS4 independente
        (barreira: F5 fixa a semântica de allowlist que F6 espelha)
WAVE 1              F6               ← WS5 depende da fonte de destinos definida em F5
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|------|------|-----------|-------|-------|---------------|
| 0 | **F1** | WS1 backend | 🔴 [bloqueia F1b] | baixo | Card aparece ao vivo com multi-conversa |
| 0 | **F1b** | WS1 frontend | 🟢 [depende de F1] | baixo | Card robusto mesmo com id divergente |
| 0 | **F3** | WS2 default OFF | 🟢 [coord. arquivo c/ F4] | baixo | Instalação nova nasce com tool OFF; existente inalterada |
| 0 | **F4** | WS3 delete real | 🔴 [coord. arquivo c/ F3] | alto | Excluir some a tool e ela NÃO volta no boot |
| 0 | **F5** | WS4 spoke→router | 🟢 [bloqueia F6 (semântica)] | médio | Spoke só consegue transferir pro roteador |
| 1 | **F6** | WS5 descrição no prompt | 🟢 [depende de F5] | médio | Roteador enxerga descrições e roteia melhor |

> **Nota de coordenação F3×F4:** ambas editam `agent/ai_builtin_tools.py` (`seed_builtin_tools`). Fazer na **mesma branch/sequência** (F3 antes de F4) para evitar conflito no mesmo hunk. As duas juntas fecham o ciclo "OFF-default + excluível".

> **Caracterização recomendada antes de F1/F3/F4** (rede de segurança, sem código de produção): (a) reproduzir o bug WS1 — abrir uma conversa de um contato que tenha 2 conversas abertas em canais diferentes, disparar uma resposta da IA que use tool, confirmar que o card só aparece no F5; (b) snapshot do estado atual das tools (`GET /api/tools` + `GET /api/ai/tools`) antes de mexer nos seeds.

---

### Fase F1 — WS1 backend: card `tool_call` com `conversation_id` correto  🔴 [bloqueia F1b]
**Objetivo:** o card de tool broadcastado ao vivo carrega o `conversation_id` da linha realmente salva (inbox-aware), não o de `get_open_for_contact`.
**Itens:**
- `broadcast_tool_calls` ([messaging_service.py:484-492](../app/services/messaging_service.py#L484-L492)): capturar `saved = contact.add_message("tool_call", content)` e montar `tc_message` com `conversation_id=saved.get("conversation_id")`, `ts=saved.get("ts", time.time())`, e `_id=saved["id"]` (quando houver) — espelhando `private_note` ([contacts.py:1043-1058](../server/routes/contacts.py#L1043-L1058)). `[sequencial]`
- Remover a resolução `conv_id` do topo ([:463-468](../app/services/messaging_service.py#L463-L468)) **se** confirmado que `conv_id` não é usado além do `tc_message` — o bloco `attr_scopes` ([:502-513](../app/services/messaging_service.py#L502-L513)) reconsulta a conversa por conta própria (verificar antes de remover). Se for arriscado, deixar o bloco e só trocar a fonte do id no `tc_message`. `[sequencial]`
- Um fix cobre os DOIS call sites (texto [:834](../app/services/messaging_service.py#L834) e mídia [:977](../app/services/messaging_service.py#L977)) — é a mesma função.
**Pronto quando:** com o contato tendo ≥2 conversas abertas em canais diferentes, a resposta da IA com tool mostra o card "🔧 …" **na hora** (sem F5), na thread certa; single-channel continua funcionando.

#### Status de execução — Fase F1
**Estado:** ✅ Concluída (2026-07-03)
- **O que foi feito:** `broadcast_tool_calls` ([app/services/messaging_service.py](../app/services/messaging_service.py)) agora captura `saved = await asyncio.to_thread(contact.add_message, "tool_call", content)` e monta o `tc_message` com `conversation_id=saved["conversation_id"]`, `ts=saved["ts"]` e `_id=saved["id"]` — espelhando o padrão `private_note`. O save ganhou `try/except` defensivo (falha no save não derruba o broadcast; card sai sem `conversation_id` e cai no match phone+channel) e foi movido pra `asyncio.to_thread` (antes bloqueava o event loop). Teste de regressão novo: [tests/test_tool_call_broadcast.py](../tests/test_tool_call_broadcast.py) (2 testes: multi-conversa e single-channel).
- **Como foi feito / decisões:** o bloco `get_open_for_contact` do topo **foi removido** — confirmado que `conv_id` só era usado no `tc_message`; o bloco `attr_scopes` (mais abaixo na mesma função) reconsulta a conversa por conta própria e ficou intacto. Caracterização primeiro: o teste multi-conversa foi escrito ANTES do fix e reproduziu o bug exato (payload com `conversation_id=2` da conversa de outro canal, row salva na conversa 1); depois do fix, verde.
- **Problemas / pendências:** nenhuma. Goldens de caracterização do sandbox (`test_sandbox_improve_characterization.py`, 15 passed) confirmam que o efeito colateral persistido (card `tool_call` antes das rows assistant) não mudou.
- **Verificação:** `pytest tests/test_tool_call_broadcast.py` (red→green) + `pytest tests/characterization/test_sandbox_improve_characterization.py` (15 passed). Reprodução manual multi-conversa coberta pelo teste (2 conversas abertas em canais diferentes).

---

### Fase F1b — WS1 frontend: OR-fallback no roteamento do `new_message`  🟢 [depende de F1]
**Objetivo:** um card painel-only nunca mais é descartado por `conversation_id` divergente — defesa em profundidade.
**Itens:**
- `useConversationWsEvents.js` ([:393-401](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L393-L401)): trocar o ternário exclusivo por OR-fallback — `belongsToOpen = (msgConvId != null && msgConvId === selectedConvIdRef.current) || (phone === selectedRef.current && msgChannel === selectedChannelIdRef.current)`. Preserva o objetivo do commit 89e41a4 (id correto continua roteando certo) e adiciona a rede phone+channel. `[sequencial]`
- Conferir que o dedupe ([:430-444](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L430-L444)) não colapsa cards distintos (conteúdo difere; `ts` vem do banco após F1).
**Pronto quando:** mesmo forçando um `conversation_id` divergente no payload, o card aparece na thread aberta; `node --test` dos módulos puros (`messages.js`) verde.

#### Status de execução — Fase F1b
**Estado:** ✅ Concluída (2026-07-03)
- **O que foi feito:** o ternário exclusivo do roteamento do `new_message` em [useConversationWsEvents.js](../web/static/js/components/contacts/hooks/useConversationWsEvents.js) virou OR-fallback: `belongsToOpen = (msgConvId != null && msgConvId === selectedConvIdRef.current) || (phone === selectedRef.current && msgChannel === selectedChannelIdRef.current)`. O ramo legado (sem conversa selecionada → match por phone) ficou intacto.
- **Como foi feito / decisões:** exatamente a expressão prescrita no plano — preserva o objetivo do commit 89e41a4 (id correto continua roteando primeiro) e adiciona a rede (phone, channel), que identifica unicamente a conversa aberta (índice único parcial `uq_atend_open_contact_inbox`). Não extraí o predicado pra módulo puro (mudança mínima e defensiva; o hook não tem harness de teste próprio).
- **Problemas / pendências:** ~~trade-off do OR-fallback amplo~~ **revisado no review final**: o OR-fallback puro do plano vazava mensagens com `conversation_id` divergente pra thread aberta sempre que (phone, canal) casassem — incluindo payloads SEM `channel_id` (ex.: `system_notices` emite `conversation_id` sem canal → `msgChannel` caía em `'default'` e casava com a thread default por acidente) e mensagens reais de outra conversa (disparando `markAsRead` errado). Endurecido: com id divergente, só **cards painel-only** (`PANEL_CARD_ROLES`) **com canal explícito no payload** caem na rede (phone, channel); mensagem real com id divergente volta a ser descartada (como no developer). A classe de bug original (card `tool_call` com id mal resolvido) continua coberta — o broadcast de tool_call sempre carrega `channel_id` explícito.
- **Verificação:** dedupe conferido — `sameMessage` colapsa por `ts+role` ou `content+role` (janela 30s); cards de tool têm conteúdo distinto e, após F1, `ts` do banco (cada INSERT tem `time.time()` próprio) → não colapsam. `node --test` de todos os módulos puros do frontend: 153 passed; `check_imports.mjs`: 317 imports OK; `node --check` no hook: sintaxe OK.

---

### Fase F3 — WS2: `transferir_agente` nasce DESLIGADA (só instalações novas)  🟢 [coordenar arquivo com F4]
**Objetivo:** instalação **nova** nasce com `transferir_agente` OFF nos dois seeds. Bancos existentes ficam intocados (D7 / P2 = B) — **sem migration**.
**Itens:**
- Definir `OFF_BY_DEFAULT_TOOLS = {"transferir_agente"}` num ponto único (ex.: `agent/ai_builtin_tools.py` ou `agent/tool_registry.py`). `[sequencial]`
- `tool_override_repo.ensure` ([:51-72](../db/repositories/tool_override_repo.py#L51-L72)): aceitar `default_enabled` (param) e usar `0` quando `name ∈ OFF_BY_DEFAULT_TOOLS`. Chamador em `tool_registry.register_tool` ([:94](../agent/tool_registry.py#L94)) passa o default. **Como `ensure` usa `update_cols=['plugin_id']`, rows existentes não regridem** (é justamente o que garante "só instalações novas" — bancos que já têm a row ligada não são tocados). `[paralelo]`
- `seed_builtin_tools` ([ai_builtin_tools.py:108-128](../agent/ai_builtin_tools.py#L108-L128)): seedar com `enabled=False` quando `name ∈ OFF_BY_DEFAULT_TOOLS`. Só insere quando a row não existe ([:110-111](../agent/ai_builtin_tools.py#L110-L111)) → também só afeta banco novo. `[paralelo]`
- **Sem migration** (D7). Bancos existentes mantêm a tool como estiver; para desligar, o operador usa o toggle da tela `/tools`.
**Pronto quando:** banco limpo (novo) → `GET /api/tools` mostra `transferir_agente` desabilitada e ela **não** entra no schema do LLM; banco existente → **inalterado** (tool continua como estava); ligar/desligar manualmente na UI persiste no restart.

#### Status de execução — Fase F3
**Estado:** ✅ Concluída (2026-07-03)
- **O que foi feito:** (1) `OFF_BY_DEFAULT_TOOLS = {"transferir_agente"}` + helper `default_override_enabled(name)` em [agent/ai_builtin_tools.py](../agent/ai_builtin_tools.py) (ponto único da política, consultado pelos DOIS seeds); (2) `seed_builtin_tools` seeda `enabled=name not in OFF_BY_DEFAULT_TOOLS`; (3) `tool_override_repo.ensure` ganhou `default_enabled: bool = True` (keyword-only) — `update_cols=['plugin_id']` inalterado, rows existentes nunca regridem; (4) os dois call sites de `ensure` em [agent/tool_registry.py](../agent/tool_registry.py) (`register_tool`/`override_tool`) passam o default via `_default_enabled(name)` (import lazy + defensivo → falha cai no legado "nasce ligada"). Testes novos: [tests/test_builtin_tool_defaults.py](../tests/test_builtin_tool_defaults.py) (3 testes).
- **Como foi feito / decisões:** ponto único ficou em `ai_builtin_tools` (dono da semântica builtin). **Refinamento além do plano** (necessário pro "Pronto quando: ligar/desligar na UI persiste no restart"): pra tool off-by-default, `default_override_enabled` espelha `ai_tools.enabled` do momento em vez de retornar False fixo — cobre o ciclo *nasce OFF → operador liga na UI unificada (branch `row.code` → `ai_tools.enabled=1` + restart) → a row `tool_overrides` (limpa pelo `delete_orphans` enquanto a tool esteve desregistrada) é recriada no boot JÁ ligada*. Com False fixo, os dois gates divergiriam e o "ligar" nunca sobreviveria ao restart. Instalação nova de verdade (sem row `ai_tools` ainda no momento do register) continua nascendo OFF.
- **Problemas / pendências:** sem migration (D7); bancos existentes intocados (seed pula rows presentes; ensure não regride). Consequência esperada nos testes: 8 goldens de caracterização (killswitch + execution) capturavam `transferir_agente` na lista de tools de um app fresh — regeneradas com `UPDATE_GOLDENS=1` e diff auditado (só a remoção de `transferir_agente` + vírgula). `test_agent_turn_routing_hop` (caracteriza a MÁQUINA de routing, que pressupõe a tool ligada) ganhou setup explícito ligando os dois gates antes do boot. **Pré-existente (não meu):** `test_legacy_suite[test_endpoints.py]` falha em `developer` limpo com `create_kanban_view() got an unexpected keyword argument 'group_field_scope'` (plugin protocolos, seção Kanban Views) — confirmado rodando o baseline 711fa21 num worktree descartável com o mesmo banco.
- **Verificação:** `pytest tests/test_builtin_tool_defaults.py` (3 passed: nasce OFF nos dois seeds; demais builtins nascem ON; intenção do operador respeitada na recriação). Killswitch+execution+agent_turn+defaults: 18 passed. `test_agent_routing.py` (legacy) conferido: usa `ToolRegistry` cru sem `refresh_tool_overrides` → check do spoke não depende do default. Suíte completa re-rodada ao final da wave (ver F4/F6).

---

### Fase F4 — WS3: botão Excluir real para `transferir_agente` (tombstone)  🔴 [coordenar arquivo com F3]
**Objetivo:** o operador consegue apagar `transferir_agente` pela UI e ela **não volta** no próximo boot.
**Itens:**
- **Tombstone:** persistir a lista de builtins deletados em `config` (ex.: chave `deleted_builtin_tools` = JSON array). `[sequencial]`
- `seed_builtin_tools` ([ai_builtin_tools.py:108-111](../agent/ai_builtin_tools.py#L108-L111)): **pular** `name ∈ deleted_builtin_tools`. `[sequencial]`
- `tool_registry.register_tool` / `ensure` ([:94](../agent/tool_registry.py#L94)): **não** re-registrar/re-`ensure` builtin tombado (senão a row `tool_overrides` volta e a tool reaparece no schema). `[sequencial]`
- Rota `DELETE /api/ai/tools/{name}` ([ai_engine.py:360-371](../server/routes/ai_engine.py#L360-L371)): **relaxar o guard** — em vez de bloquear todo `kind=='builtin'`, permitir o delete gravando o tombstone; opcionalmente restringir a `name ∈ BUILTIN_MODULES` deletáveis. Após delete: gravar tombstone + `tool_repo.delete(name)` + remover a row `tool_overrides` (add `tool_override_repo.delete(name)` — hoje não existe, só `delete_for_plugin`/`delete_orphans`) + `schedule_restart`. `[sequencial]`
- Frontend `ToolsUnified.js` ([:406-411](../web/static/js/components/ai/ToolsUnified.js#L406-L411)): liberar o botão Excluir para builtins deletáveis (ex.: `r.kind === 'code' || r.name === 'transferir_agente'`, ou um flag `deletable` vindo do backend). Manter o `ConfirmDialog` de "agenda restart". `[paralelo]`
- **Sem reinstalação** (D8 / P3 = B): não há botão nem rota de "reinstalar". Se o operador quiser a tool de volta, recria pela UI como tool code-in-DB (colando o código) ou remove a chave `deleted_builtin_tools` do `config` na mão. Documentar isso no texto do `ConfirmDialog` ("esta ação é definitiva pela interface").
**Pronto quando:** clicar Excluir em `transferir_agente` → some da lista; após restart do worker **continua sumida**; a tool não aparece no schema do LLM; nenhuma outra tool é afetada.

#### Status de execução — Fase F4
**Estado:** ✅ Concluída (2026-07-03)
- **O que foi feito:** (1) tombstone em [agent/ai_builtin_tools.py](../agent/ai_builtin_tools.py): `TOMBSTONE_CONFIG_KEY="deleted_builtin_tools"` (JSON array na tabela `config`), `deleted_builtin_tools()`, `tombstone_builtin(name)` (idempotente) e `DELETABLE_BUILTINS = {"transferir_agente"}`; (2) skip nos DOIS caminhos de re-seed: `seed_builtin_tools` pula nomes tombados e `ToolRegistry.register_tool` ([agent/tool_registry.py](../agent/tool_registry.py), helper `_builtin_tombstoned` com import lazy) não registra nem recria a row de override — o `delete_orphans` do boot limpa o resto; `register_builtin_overrides` também pula (defensivo); (3) `tool_override_repo.delete(name)` novo; (4) rota `DELETE /api/ai/tools/{name}` ([server/routes/ai_engine.py](../server/routes/ai_engine.py)): builtin em `DELETABLE_BUILTINS` → tombstone + delete `ai_tools` + delete `tool_overrides` + unregister/refresh no processo vivo + `schedule_restart`; builtin fora da allowlist → 400 como antes; (5) frontend [ToolsUnified.js](../web/static/js/components/ai/ToolsUnified.js): botão Excluir liberado via `DELETABLE_BUILTINS` (Set espelhando o backend) e `ConfirmModal` com texto específico pra builtin ("definitiva pela interface" + como recriar — D8).
- **Como foi feito / decisões:** tombstone na `config` (sem migration, D7/D8); escopo restrito a `transferir_agente` via allowlist `DELETABLE_BUILTINS` (infra generaliza depois); sem botão/rota de reinstalar (D8) — documentado no ConfirmDialog. Imports da rota são lazy dentro da função pra não tocar o cabeçalho compartilhado do arquivo (fronteira com o plano 31, que edita os endpoints de variáveis no mesmo arquivo).
- **Problemas / pendências:** risco alto do plano coberto por teste: agente com `tool_names` referenciando a tool deletada não quebra o boot (`select_active_tools` filtra por interseção) nem o turno; app novo com tombstone presente sobe sem erro e sem a tool.
- **Verificação:** [tests/test_builtin_tool_delete.py](../tests/test_builtin_tool_delete.py) (5 testes: roundtrip do tombstone; os dois re-seeds pulam; endpoint deletável 200 com efeito imediato; `save_contact_info` segue bloqueada 400; boot com agente referenciando tool tombada). 8 passed junto com os defaults do F3. Botão Excluir usa as mesmas classes dark-safe do delete de tool code (`text-red-500`/`hover:bg-wa-hover`); modal 100% `wa-*`.

---

### Fase F5 — WS4: subagente só devolve para o roteador  🟢 [bloqueia F6 (semântica)]
**Objetivo:** um spoke só pode transferir de volta pro roteador; o roteador mantém sua allowlist.
**Itens:**
- `transferir_agente.py::execute` ([:70-77](../agent/tools/transferir_agente.py#L70-L77)): generalizar o bloco de validação. Ramo novo — **se o agente atual NÃO é router** (spoke): `router = agent_repo.get_router()`; se `router` e `target != router["agent_key"]` → retornar mensagem de bloqueio orientando "devolva a conversa ao roteador '{router}' usando `transferir_agente`". Manter o ramo router→allowlist existente. `[sequencial]`
- Política quando `get_router()` é `None` (nenhum router): decidir (ver P4) — recomendação: **não bloquear** (degrada pro comportamento atual) para não travar quem não usa hub-and-spoke.
- Não mexer em `call_limit=1` nem no cap de depth (já evitam ping-pong).
**Pronto quando:** com um spoke ativo, uma `transferir_agente` para um agente que **não** é o roteador é recusada com mensagem clara; transferir para o roteador funciona; roteador→spoke continua igual.

#### Status de execução — Fase F5
**Estado:** ✅ Concluída (2026-07-03)
- **O que foi feito:** o bloco de validação de `execute()` em [agent/tools/transferir_agente.py](../agent/tools/transferir_agente.py) foi generalizado por papel do agente ATUAL: router → allowlist própria (comportamento existente, intacto); **não-router (spoke) → único destino válido é o roteador** (`agent_repo.get_router()`), com mensagem de bloqueio citando a rota de escape (`agente='<router>'`) e prefixo "Erro:" (success-aware pros guardrails de `requires_prior_call`). Testes novos: [tests/test_spoke_router_enforcement.py](../tests/test_spoke_router_enforcement.py) (6 testes).
- **Como foi feito / decisões:** P4 conforme recomendação — `get_router()==None` NÃO bloqueia (degrada pro legado; não trava instalação sem hub-and-spoke). Conversa sem `active_agent_key` também não é bloqueada (sem como classificar o atual). **Nota de semântica importante:** a conversa nasce carimbada com o agente default da inbox (`default_agent_key_for_inbox`), então na prática o agente default (não-router) também é tratado como spoke — com roteador configurado, default só transfere PRO roteador. É a leitura estrita de D4 ("um único roteador roteia") e o item do plano ("se o agente atual NÃO é router").
- **Problemas / pendências:** o script legado `test_agent_routing.py` caracterizava o comportamento pré-F5 (default→suporte livre) — atualizado para a semântica nova (bloqueio documentado + handoff persistente agora via roteador), 29/29 verde. Fragilidade pré-existente anotada: `test_routing_motivo.py` deixa um roteador no banco compartilhado do processo (sem teardown de agentes); a ordem de coleta atual é segura e o reset de schema é por processo, mas seleções `-k` fora de ordem podem expor interações — não alterei fixtures de outros arquivos.
- **Verificação:** `pytest tests/test_spoke_router_enforcement.py` (6 passed: router→allowlist ok/bloqueado, spoke→router ok, spoke→spoke bloqueado com escape + handoff não persistido, sem-router não bloqueia, conversa-sem-agente segue legado); `tests/test_agent_routing.py` 29/29; `tests/endpoints/test_conversation_events_c0.py` + `tests/test_routing_motivo.py` 14 passed.

---

### Fase F6 — WS5: descrição de cada agente injetada no prompt do roteador  🟢 [depende de F5]
**Objetivo:** o roteador recebe, no system prompt, a lista de destinos com suas descrições — e essa lista **bate** com o que `execute()` aceita (coordenada com F5).
**Itens:**
- Ao montar o agente em `agent_factory.build_for_contact` ([:205-300](../agent/agent_factory.py#L205)) / `prompt_builder`: quando o agente é `is_router`, anexar seção **"Agentes disponíveis para transferência"** listando, por destino, `display_name (agent_key) — description`. `[sequencial]`
- **Fonte dos destinos (coordenar com F5):** `routing_targets` do roteador se preenchido; senão todos os agentes `enabled` exceto o próprio (via `agent_repo.list_all()`). A MESMA fonte que F5 usa como allowlist — senão o prompt induz transferências que `execute()` barra. `[sequencial]`
- Isolar em `try/except` (falha ao buscar agentes **não** derruba o turno) — mesmo padrão dos fragments de plugin.
- Idempotência com prompt inline: o humano pode já ter listado agentes à mão no prompt; a seção automática é adicional. Documentar (não tentar deduplicar).
- **Frontend (opcional):** help text no campo Descrição ([AgentsManager.js:377-380](../web/static/js/components/ai/AgentsManager.js#L377-L380)) — "usada pelo roteador para decidir o destino; escreva curto e objetivo".
- **Alternativa registrada (não implementar agora):** enum dinâmico do param `agente` no schema por-request (Opção B) — mais invasivo (precisa reescrever o schema estático antes do `filter.llm.tools`). Ver P5.
**Pronto quando:** com ≥2 agentes tendo descrição, o roteador transfere pro agente certo citando o motivo; a lista exposta no prompt é subconjunto/igual à allowlist aceita por `execute()`.

#### Status de execução — Fase F6
**Estado:** ✅ Concluída (2026-07-03)
- **O que foi feito:** (1) helper `router_destinations(router)` em [agent/tools/transferir_agente.py](../agent/tools/transferir_agente.py) — **fonte ÚNICA da allowlist** (F5×F6): agentes enabled, exceto o próprio, restritos a `routing_targets` quando preenchida; (2) `build_for_contact` ([agent/agent_factory.py](../agent/agent_factory.py)) injeta, quando `is_router`, a seção `--- Agentes disponíveis para transferência ---` (uma linha `display_name (agent_key) — description` por destino) via `_router_destinations_section`, dentro de `try/except` (falha nunca derruba o turno); roteador sem destinos não ganha seção vazia; (3) help text no campo Descrição do form de agente ([AgentsManager.js](../web/static/js/components/ai/AgentsManager.js)) — "usada pelo roteador para decidir o destino…". Testes novos: [tests/test_router_prompt_description.py](../tests/test_router_prompt_description.py) (6 testes).
- **Como foi feito / decisões:** injeção no `build_for_contact` (região liberada pro plano 30 no contrato de fronteira), depois do `render_template` — a seção é dinâmica e NÃO participa do prompt inline cru (convenção com o plano 31: a análise do C1+ mostra o prompt inline; a seção é problema do runtime do roteador). Aditiva e sem dedupe com listagem manual no prompt (documentado no comentário). Opção A (prompt injection) conforme P5; enum dinâmico no schema fica registrado como melhoria futura.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `pytest tests/test_router_prompt_description.py` (6 passed) — inclui o teste de coerência F5×F6 (`test_lista_do_prompt_igual_allowlist_do_execute`: todo destino listado no prompt é aceito por `execute()`), restrição por `routing_targets`, agente desligado fora da lista, não-router sem seção, falha isolada, e roteador sem destinos sem seção vazia. Help text usa `text-wa-secondary` (dark-safe).

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| WS1 remover `get_open_for_contact` do topo | `conv_id` usado em outro lugar da função | Conferir `attr_scopes` ([:502-513](../app/services/messaging_service.py#L502-L513)) reconsulta sozinho; se em dúvida, só trocar a fonte do id no `tc_message` e manter o bloco |
| WS1 `ts` no dedupe | Card colapsado por `sameMessage` (ts+role) | Usar `saved.ts` (do banco), não `time.time()` |
| WS2 dois seeds divergentes | `tool_overrides.enabled=0` mas `ai_tools.enabled=1` (ou vice-versa) → estado inconsistente | Mudar os DOIS seeds no mesmo passo (mesmo conjunto `OFF_BY_DEFAULT_TOOLS`) |
| WS2 só instalações novas (D7) | Bancos existentes seguem com a tool ligada — pode surpreender quem esperava desligada | Decisão consciente (P2 = B); operador desliga na tela `/tools` se quiser |
| WS3 delete de builtin | Boot re-seeda e a tool "ressuscita" | Tombstone checado por `seed_builtin_tools` **e** `register_tool`/`ensure` (dois caminhos) |
| WS3 tool sumida com agente referenciando-a | Agente com `tool_names` incluindo `transferir_agente` deletada → schema quebrado? | `select_active_tools` deve ignorar nomes não-registrados (verificar); testar delete com agente que a referencia |
| WS3 restart em cascata | `schedule_restart` no delete derruba worker | Comportamento esperado (mesma UX das tools code); confirmar supervisor (dev `_reload_trigger`, Docker `restart:unless-stopped`) |
| WS4 `get_router()==None` | Bloqueio total travaria instalações sem hub-and-spoke | Não bloquear quando não há router (P4) |
| WS4/WS5 allowlist divergente | Prompt (F6) oferece destino que `execute` (F5) barra → LLM insiste e falha | Fonte ÚNICA de destinos compartilhada entre F5 e F6 |
| WS5 falha ao buscar agentes | Exceção derruba o turno da IA | `try/except` defensivo em volta da injeção |
| Migration Postgres-only | Backend é só Postgres (Plano 29) | `UPDATE` simples, dialect-agnóstico; round-trip up/down |
| Modo escuro (F4/F6 UI) | Botão/help novo ilegível no dark | Usar classes `wa-*`; testar dark (regra CLAUDE.md) |

---

## 6. Perguntas em aberto

- **P1 — WS3: escopo do delete** ✅ DECIDIDO (2026-07-03): delete **real** (D2), aceitando o risco. Escopo inicial = `transferir_agente`; a infra de tombstone pode generalizar para qualquer builtin depois (não implementar já).
- **P2 — WS2: mexer ou não em instalações existentes.** ✅ DECIDIDO (2026-07-03, Thiago): **só instalações novas** (opção B). Sem migration one-time; bancos existentes mantêm a tool como estiver. Ver D7.
- **P3 — WS3: reinstalar um builtin tombado.** ✅ DECIDIDO (2026-07-03, Thiago): **sem botão de reinstalar** (opção B), para manter simples. Recriação fica por conta do operador (UI como tool code-in-DB ou editar o `config`). Ver D8.
- **P4 — WS4: política quando não há roteador (`get_router()==None`).** ✅ DECIDIDO (recomendado): **não bloquear** o spoke (degrada pro comportamento atual) — evita travar quem não usa hub-and-spoke. Reavaliar se D1 virar "exatamente-um".
- **P5 — WS5: prompt injection (Opção A) vs enum dinâmico no schema (Opção B).** ✅ DECIDIDO: começar por **A** (prompt injection), mais simples e alinhado ao prompt inline. B fica como melhoria futura registrada.

---

## 7. Apêndice — arquivos-chave por camada

**Backend — mensageria / real-time (WS1)**
- [app/services/messaging_service.py](../app/services/messaging_service.py) — `broadcast_tool_calls` (448-492), call sites 834/977, referência de assimetria 400-405
- [server/routes/contacts.py](../server/routes/contacts.py) — padrão `private_note` de referência (1043-1058)
- [agent/memory.py](../agent/memory.py) — `add_message` retorna a linha salva com id inbox-aware
- [db/repositories/conversation_repo.py](../db/repositories/conversation_repo.py) — `get_open_for_contact` vs `get_open_for_contact_inbox`

**Frontend — conversa (WS1b, WS3 UI, WS5 UI)**
- [web/static/js/components/contacts/hooks/useConversationWsEvents.js](../web/static/js/components/contacts/hooks/useConversationWsEvents.js) — roteamento do `new_message` (393-401), dedupe (430-444)
- [web/static/js/services/messages.js](../web/static/js/services/messages.js) — `sameMessage` (41-46)
- [web/static/js/components/ai/ToolsUnified.js](../web/static/js/components/ai/ToolsUnified.js) — gate do botão Excluir (135-137, 406-411)
- [web/static/js/components/ai/AgentsManager.js](../web/static/js/components/ai/AgentsManager.js) — form de agente (148, 242, 377-380, 676)
- [web/static/js/services/api.js](../web/static/js/services/api.js) — `deleteTool` (875-877)

**Backend — tools / seeds / registry (WS2, WS3)**
- [agent/ai_builtin_tools.py](../agent/ai_builtin_tools.py) — `BUILTIN_MODULES` (47-52), `seed_builtin_tools` (101-128), `register_builtin_overrides` (129-168)
- [agent/tool_registry.py](../agent/tool_registry.py) — `register_tool`/`ensure` (94), `refresh_tool_overrides` (178)
- [db/repositories/tool_override_repo.py](../db/repositories/tool_override_repo.py) — `ensure` (51-72), sem delete-by-name (108-125)
- [db/repositories/tool_repo.py](../db/repositories/tool_repo.py) — `delete` (181-185)
- [server/routes/ai_engine.py](../server/routes/ai_engine.py) — rota DELETE + guard (360-371)
- `agent/tools/__init__.py` — `CORE_TOOLS` (35-40)

**Backend — routing / handoff (WS4, WS5)**
- [agent/tools/transferir_agente.py](../agent/tools/transferir_agente.py) — `execute` allowlist (70-77), schema param `agente` (34-37)
- [db/repositories/agent_repo.py](../db/repositories/agent_repo.py) — `get_router` (221-227)
- [agent/agent_factory.py](../agent/agent_factory.py) — `build_for_contact` (205-300), `AgentSpec` (155-159)
- [agent/prompt_builder.py](../agent/prompt_builder.py) — montagem de seções dinâmicas
- [ai_engine/hooks.py](../ai_engine/hooks.py) — `_DEFAULT_HOOKS`/`ESCAPE_TOOL` (32,37); [ai_engine/routing.py](../ai_engine/routing.py) — `run_with_routing` (39-86)

**DB**
- **Sem migration nova** (D7/D8). Tombstone do WS3 = chave `deleted_builtin_tools` na tabela `config` (já existente), gravada via `config_repo`.

---

## 7.5 Review final multi-agente (2026-07-03) — correções aplicadas pós-fases

Workflow de review adversarial (5 dimensões × refutação) sobre o diff do branch. Findings **corrigidos** no commit de review:

1. **(high, confirmado c/ reprodução) Tombstone bloqueava a via de recuperação documentada** — recriar `transferir_agente` como tool code-in-DB (prometido no ConfirmModal, D8) era pulado silenciosamente pelo gate por-nome em `register_tool`, com `install_status='ok'` enganoso. Fix: `register_ai_tools` registra com `tombstone_exempt=True` (o tombstone barra só a baseline on-disk). Teste: `test_recriacao_como_code_in_db_registra_apesar_do_tombstone`.
2. **(high) Toggle na UI unificada bumpava a versão** — ligar o builtin nasce-OFF via `PUT /api/ai/tools` (branch `row.code`) fazia `tool_repo.save` bumpar pra v2 ⇒ `register_builtin_overrides` tratava como "editado" e passava a **executar o código do banco in-process** (e a cópia congelada deixava de seguir updates do disco). Fix: `tool_repo.save` com dedup — save idêntico exceto `enabled` não bumpa versão nem grava history. Teste: `test_toggle_enabled_nao_bumpa_versao_nem_vira_editado`.
3. **(medium) `get_router()` não filtra `enabled`** — roteador desabilitado deixava o spoke em deadlock (bloqueado pra todos os destinos, e o próprio roteador rejeitado pelo check de enabled). Fix: spoke rule só com roteador `enabled` (senão degrada pro P4). Teste: `test_roteador_desabilitado_nao_trava_spoke`.
4. **(medium) Seção F6 injetada com a tool indisponível** — o prompt anunciava destinos que o LLM não conseguia acionar (tool nasce OFF/desabilitada/deletada/fora do `tool_names`). Fix: gate `_transfer_tool_available` (novo `is_tool_active` no registry/handler). Testes: `test_secao_so_injeta_com_transferir_agente_acionavel`, `test_secao_respeita_tool_names_do_roteador`.
5. **(medium, confirmado) OR-fallback F1b vazava mensagens entre threads/canais** — ver status F1b (fix cirúrgico com `PANEL_CARD_ROLES` + canal explícito).
6. **(low) DELETE de builtin deletável sem row `ai_tools`** caía no 404 genérico — agora entra no fluxo de tombstone mesmo com a row ausente; row `kind='code'` recriada segue o fluxo genérico de tool de código.

**Findings avaliados e NÃO corrigidos (com racional):**
- *(high) O bloqueio F5 consome o `call_limit=1` da `transferir_agente`* — spoke que tenta destino proibido gasta a única chamada do turno e só devolve ao roteador na PRÓXIMA mensagem. Real, mas o plano trava "Não mexer em call_limit=1 nem no cap de depth" (F5). Mitigado pela F6 (o roteador conhece os destinos) e pelo prompt do spoke (a UI orienta "devolva ao roteador"). Registrado como melhoria futura (ex.: não contar chamada bloqueada no limite).
- *(low) Lista "Agentes disponíveis" do erro destino-inexistente é global (ignora allowlist/spoke)* — exigiria reordenar o `execute`; baixo impacto com a F6 no prompt do roteador.
- *(low) Classificação do "atual" via `conv.active_agent_key` pode divergir do agente que realmente roda* — simplificação já documentada no status F5.
- *(low) Fail-open na leitura do tombstone durante o seed pode ressuscitar a row numa falha transitória de config* — aceito (config e ai_tools compartilham o mesmo banco; falha de um com o outro saudável é improvável).
- *(low) "Instalação nova" inferida pela ausência da row* — é a própria definição do D7; instalação que nunca teve a tool ganha o default novo por design.

## 8. Checklist de verificação (aplicar a cada mudança)

- [x] **WS1:** card `tool_call` aparece ao vivo (sem F5) com contato multi-conversa; single-channel intacto — `tests/test_tool_call_broadcast.py` (reproduziu o bug antes do fix)
- [x] **WS1b:** `node --test` verde (153 passed, inclui `messages.js`); card painel-only sobrevive a id divergente (com canal explícito no payload — ver review §7.5 item 5)
- [x] **WS2:** banco novo nasce com `transferir_agente` OFF (dois seeds); banco existente **inalterado** (ensure não regride, seed pula rows presentes); ligar/desligar na UI persiste no restart (default espelha `ai_tools` + toggle sem bump de versão)
- [x] **WS3:** Excluir some a tool; após restart **não volta** (tombstone nos dois caminhos de re-seed); nenhuma outra tool afetada; agente que referenciava não quebra o boot (testado); sem botão de reinstalar — recriação via tool code-in-DB documentada no confirm E funcional (`tombstone_exempt`, review §7.5 item 1)
- [x] **WS3:** botão Excluir legível no modo escuro (mesmas classes do delete de tool code; modal 100% `wa-*`)
- [x] **WS4:** `execute()` — router→allowlist ok, spoke→router ok, spoke→outro bloqueado com mensagem citando a rota de escape; sem router não bloqueia; roteador DESABILITADO também não bloqueia (review §7.5 item 3)
- [x] **WS5:** prompt do roteador contém a seção de destinos; lista = allowlist aceita por `execute()` (teste de coerência F5×F6); falha ao buscar agentes não derruba o turno; seção só injeta com a tool acionável (review §7.5 item 4)
- [x] **Geral:** `pytest tests/ -q` no Postgres (`whatsbot_test_p30`): **verde exceto 1 falha PRÉ-EXISTENTE** — `test_legacy_suite[test_endpoints.py]` quebra em `developer` limpo (711fa21) com `create_kanban_view() got an unexpected keyword argument 'group_field_scope'` (plugin protocolos/kanban, alheio a este plano; provável dependência do zip do plugin — ver memória do projeto)
- [x] **Geral:** nenhum segredo em URL; mudanças de UI aditivas (help text, botão, modal) — sem estado novo de navegação
```
