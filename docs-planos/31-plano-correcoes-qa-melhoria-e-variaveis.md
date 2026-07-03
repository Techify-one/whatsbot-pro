# Plano 31 — Correções de QA: card da "Gerar melhoria" no lugar certo (+ análise multi‑agente), variáveis (validação/limpeza) e robustez do motor (mudez silenciosa, anti‑repetição)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-03 · **Escopo:** médio (7 correções em 3 frentes: feature "Gerar melhoria", feature Variáveis, robustez do motor; 1 migration nova)
>
> **Origem:** relatórios de QA do Thiago ([QA-motor-agentes.md](QA-motor-agentes.md), [30-resultados-teste-variaveis.md] resumido em sessão, [checklist-testar-tool-dependencias.md](checklist-testar-tool-dependencias.md)) + investigação read‑only concluída nesta sessão (workflow de 5 verificadores paralelos, nenhum código alterado). Todas as decisões de escopo já foram tomadas pelo usuário (ver §0).
> **Método:** leitura do código real no branch `developer` + verificação `arquivo:linha` de cada achado. Head Alembic hoje = `0036_atend_open_unique` (plano 30 **não** adiciona migration) → a migration nova deste plano encadeia em `0037` (**a confirmar o head no momento da execução**).
>
> **Como usar este plano:** ao executar cada fase, preencha o bloco **"Status de execução"** dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro. Verde a cada fase; **caracterização ANTES** de mexer no fluxo de "Gerar melhoria" (F2/F3, golden já existe) e no `model_factory` (F4/F5); **um refactor por commit**.
>
> Legenda de estado de execução: `⬜ Não iniciada` · `🟡 Em andamento` · `✅ Concluída` · `⛔ Bloqueada`.
> Legenda de paralelização: `🟢 PODE AGRUPAR` (sem dependência) · `🔴 FAÇA SOZINHA` (sequencial/bloqueante).

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | ✅ **D.A é bug real e vai ser corrigido.** O card da "Gerar melhoria" deve ser salvo na conversa da resposta marcada, não no inbox default. | F1 (frontend) + F2 (backend), espelhando o padrão `private_note` que já está no mesmo arquivo. |
| D2 | ✅ **C1 aprovado no nível C1+ ("incluir todos os agentes do turno").** A análise deve mostrar o prompt de CADA agente que participou do turno (router + spoke) e as tools atribuídas por agente. | F3 reconstrói a cadeia via `executions.routing_steps` + `execution_steps.agent_key`. |
| D3 | ✅ **A5 (mudez silenciosa) vai ser corrigido** com piso de `max_tokens` + log de reply vazio + aviso na UI. | F4. É robustez, não bug de lógica — o código faz o que mandaram; o risco é a mudez ser indetectável. |
| D4 | ✅ **A4, A1 corrigidos; A2 REMOVIDO por completo** (drop da coluna via migration, decisão explícita do usuário). | F6 (A1+A4) + F7 (A2 com migration `0037`). |
| D5 | ✅ **12.2 (anti‑repetição) entra**: expor `frequency_penalty`/`presence_penalty` + dedup opcional de contexto. | F5. Dedup só na camada de contexto do LLM (não altera histórico persistido nem o painel). |
| D6 | ✅ **GAP‑1 (retorno automático à triagem) FORA DE ESCOPO** — o usuário vai resolver instruindo no prompt dos sub‑agentes a sempre voltar ao router. | Nenhuma fase. Documentado como não‑feito. |
| D7 | ✅ **GAP‑2 (escopo por‑conversa de `requires_prior_call`/`call_limit`) FORA DE ESCOPO** — mantém por‑mensagem. | Nenhuma fase. |
| D8 | ✅ **Não está em produção distribuída** ⇒ refactor direto, sem stopgap de compatibilidade (mesma premissa dos planos 29/30). | A2 pode dropar a coluna sem camada de compat; migration reversível mesmo assim. |
| D9 | ✅ **Fallback single‑channel obrigatório** em D.A/C1+: sem `conversation_id` no payload, o comportamento atual é preservado. | `_channel_for(phone, None)` → `"default"` (caminho feliz atual intacto). |

---

## 1. Resumo executivo

Sete correções, agrupadas em três frentes independentes:

- **Frente MELHORIA (D.A + C1+).** Hoje a tool "Gerar melhoria" salva o card `system` sempre no inbox **default** ([contacts.py:966‑969](../server/routes/contacts.py#L966)) → em multi‑canal cria uma **conversa fantasma**. E a análise em si é **cega a canal**: usa o prompt do agente do canal default e o histórico do contato inteiro (mistura canais), mostrando **um só** prompt mesmo num fluxo router→spoke. Fix: frontend manda `conversation_id`; backend salva na conversa certa (espelhando `private_note`) e a análise passa a reconstruir a **cadeia de agentes do turno** e escopar o histórico por conversa.
- **Frente MOTOR (A5 + 12.2).** `max_tokens` baixo + modelo de raciocínio = **bot mudo silencioso** (reply vazio, nada enviado, nada logado — [model_factory.py:76](../ai_engine/model_factory.py#L76), [agno_engine.py:366‑392](../agent/agno_engine.py#L366), [messaging_service.py:835](../app/services/messaging_service.py#L835)). E não há como configurar `frequency_penalty`/`presence_penalty` nem dedup de contexto (degeneração por repetição). Fix: piso de `max_tokens` + log/aviso de reply vazio + expor os dois penalties + dedup opcional.
- **Frente VARIÁVEIS (A1 + A4 + A2).** O backend não valida o nome da variável (cria "peso morto" via API — [ai_engine.py:301‑306](../server/routes/ai_engine.py#L301)); um nome reservado (`temperature`/`max_tokens`/…) tem **efeito duplo** sem aviso ([model_factory.py:22](../ai_engine/model_factory.py#L22)); e a coluna `category` é morta ([db/tables.py:605‑613](../db/tables.py#L605)). Fix: validar no backend, avisar na UI, remover `category` (migration).

**Uma migration nova** (`0037`, só para o A2 dropar `ai_variables.category`). O resto é código.

---

## 2. Como funciona hoje (mapa) — `arquivo:linha` verificado

### 2.1 D.A — card salvo no inbox errado
- **Rota:** `improve_message` ([contacts.py:941‑975](../server/routes/contacts.py#L941)). Gera a análise (`generate_improvement`, [:958‑959](../server/routes/contacts.py#L958)) e salva via `_save()` ([:966‑969](../server/routes/contacts.py#L966)):
  ```python
  def _save():
      contact = agent_handler._get_contact(phone)          # ⚠️ SEM channel_id → default
      contact.add_message("system", note_text)             # ⚠️ cria conversa no inbox default
      return message_repo.get_last(contact.id)             # ⚠️ racy: por contact.id+ts
  ```
- **⚠️ Causa‑raiz:** `_get_contact(phone)` cai em `channel_id="default"` ([handler.py:224](../agent/handler.py#L224)); a `ContactMemory` nasce com o inbox do default ([memory.py:96‑97](../agent/memory.py#L96)); `add_message` resolve/cria a conversa **escopada a esse inbox** ([memory.py:190‑193](../agent/memory.py#L190) → `resolve_for_contact_ex` → [conversation_repo.py:221‑256](../db/repositories/conversation_repo.py#L221)). Se o contato não tem conversa aberta no inbox default, **materializa uma nova** (a fantasma).
- **A rota é a ÚNICA escrita do arquivo sem `_channel_for`:** compare com `send` ([:759](../server/routes/contacts.py#L759)), `react` ([:869/889](../server/routes/contacts.py#L869)), `delete` ([:925](../server/routes/contacts.py#L925)), `private_note` ([:1036‑1057](../server/routes/contacts.py#L1036)), image/audio ([:1134](../server/routes/contacts.py#L1134)), document ([:1201](../server/routes/contacts.py#L1201)) — **todas** usam `_channel_for(phone, body.get("conversation_id"))`.
- **Padrão de referência (a copiar):** `private_note` ([contacts.py:1036‑1057](../server/routes/contacts.py#L1036)) — `note_channel = _channel_for(phone, conversation_id)`; `_get_contact(phone, channel_id=note_channel)`; `saved_note = contact.add_message(...)`; monta `note_msg` de `saved_note["conversation_id"]/["ts"]/["id"]`; `broadcast("new_message", {phone, channel_id, message})`.
- **Frontend não envia o id:** `submitImprovement` posta só `{message, feedback}` ([useMessageActions.js:106‑113](../web/static/js/components/contacts/hooks/useMessageActions.js#L106)); `generateImprovement` idem ([api.js:251‑254](../web/static/js/services/api.js#L251)). O próprio arquivo já tem o padrão `convId` pronto na linha ~81 (`message.conversation_id != null ? … : conversationId`).
- **`message_repo.add` já aceita `conversation_id`** e devolve a linha ([message_repo.py:15](../db/repositories/message_repo.py#L15)); `add_message` (memory) também **retorna** a linha salva.

### 2.2 C1+ — análise cega a canal e a agentes
- `generate_improvement(handler, phone, target_message, feedback)` ([improvement_service.py:69](../app/services/improvement_service.py#L69)) **não recebe** canal/conversa.
  - Prompt: `_get_contact(phone)` (default, [:78](../app/services/improvement_service.py#L78)) + `build_for_contact` (**um** agente ativo, [:81](../app/services/improvement_service.py#L81)) — em fluxo router→spoke mostra **só o prompt do agente ativo agora**, nunca o do router.
  - Histórico: `message_repo.get_context(contact.id, …)` ([:108](../app/services/improvement_service.py#L108)) — por **contact_id**, mistura todos os canais.
  - Tools disponíveis: `handler.list_tools()` (todas as globais habilitadas, [:86](../app/services/improvement_service.py#L86)) — **não** a allowlist do agente.
  - Tools usadas: `_find_tools_used_around(phone, ts)` ([:31‑66](../app/services/improvement_service.py#L31)) já acha a **execução** por janela de tempo e devolve **todos** os `tool_executed` dela → cobre os dois hops (bom).
- **Dados para reconstruir a cadeia já existem:** `execution_repo.get_by_id` devolve `routing_steps` (JSON `[{from,to,depth,reason}]`, gravado por `set_routing_steps` [:84‑91](../db/repositories/execution_repo.py#L84)) + `steps`, cada um com `agent_key` ([execution_repo.py:23‑43,94‑117](../db/repositories/execution_repo.py#L23)). O `agent_key` por passo é setado por hop em [agent_run_service.py:188‑189](../app/services/agent_run_service.py#L188).
- **Como renderizar o prompt de um agente arbitrário:** `agent_repo.get(key)["prompt"]` → `agent_factory.render_template(body, dynamic_registry.variables_map())` (mesmo caminho de `build_for_contact` [:220‑227](../agent/agent_factory.py#L220)). Fallback `DEFAULT_SYSTEM_PROMPT` quando vazio.
- **⚠️ Limitação conhecida (aceita, D2):** `messages` **não tem** `agent_key` — não dá pra atribuir cada **mensagem** do histórico a um agente. Só as **tools** e a **cadeia** são atribuíveis. Não mudar schema por isso.

### 2.3 A5 — mudez silenciosa
- **Coerção sem piso:** `kwargs["max_tokens"] = int(params.get("max_tokens") or default_max_tokens)` ([model_factory.py:76](../ai_engine/model_factory.py#L76)) — não há `max(n, …)`. `default_max_tokens` só vale quando nada foi resolvido (é `_DEFAULT_MAX_TOKENS=1024`, [agno_engine.py:57]); um valor explícito baixo passa intacto. `reasoning_effort` coexiste ([:79‑80](../ai_engine/model_factory.py#L79)) → em modelo de raciocínio o teto é o orçamento TOTAL (reasoning+completion), então baixo = completion vazio.
- **Extração silenciosa:** `_extract_reply` devolve `""` sem logar quando nenhuma msg assistant tem content ([agno_engine.py:366‑392](../agent/agno_engine.py#L366)).
- **Envio silencioso:** `if result.reply:` sem `else`, sem log, no caminho texto ([messaging_service.py:835](../app/services/messaging_service.py#L835)) e mídia ([:978](../app/services/messaging_service.py#L978)). Reply vazio simplesmente não envia nada e não registra nada; `agent_run_service.py:370` ainda loga `"Processed message"` (info) mesmo vazio.
- **UI sem aviso:** input `max_tokens` em `AgentsManager.js` ([:338‑341](../web/static/js/components/ai/AgentsManager.js#L338)) tem `min="1"` e nenhum alerta; `VariablesEditor.js` idem.

### 2.4 A4 — nome reservado com efeito duplo
- Params reservados: `_TUNING_KEYS = ("temperature","top_p","max_tokens","reasoning_effort")` + `_ALIASES` (`max_output_tokens`/`max_completion_tokens`→`max_tokens`, `thinking_level`→`reasoning_effort`) ([model_factory.py:22‑27](../ai_engine/model_factory.py#L22)).
- A **mesma** `variables_map()` alimenta `build_kwargs` (tuning) e `render_template` (texto) — uma variável de texto chamada `temperature` (ou `{param}_{agent_key}`) faz as duas coisas ao mesmo tempo. `VariablesEditor.js` só valida **formato** (`NAME_RE`), nunca compara com a lista reservada.
- **Nuance:** a colisão de param só atinge os **4 canônicos** e as formas `{param}_{agent_key}` (os aliases só são normalizados dentro de `model_config`, não na busca em `variables`).

### 2.5 A1 — backend não valida nome
- `PUT /api/ai/variables/{name}` grava o `name` cru ([ai_engine.py:301‑306](../server/routes/ai_engine.py#L301)); `variable_repo.save` só faz upsert. A regex de render é `\{([a-zA-Z_][a-zA-Z0-9_]*)\}` ([agent_factory.py:51](../agent/agent_factory.py#L51)) — nome com hífen/ponto/espaço **nunca** casa (peso morto). O `NAME_RE = /^[a-zA-Z][a-zA-Z0-9_]{0,63}$/` só existe no front ([VariablesEditor.js:16](../web/static/js/components/ai/VariablesEditor.js#L16)) e só na criação ([:23](../web/static/js/components/ai/VariablesEditor.js#L23)).

### 2.6 A2 — coluna `category` morta
- Coluna existe (`Text NOT NULL server_default ""`, [db/tables.py:605‑613](../db/tables.py#L605)); `variable_repo.save(name, value, category="")` aceita e grava ([:45‑53](../db/repositories/variable_repo.py#L45)); endpoint encaminha `body.get("category","")` ([ai_engine.py:299‑306](../server/routes/ai_engine.py#L299)). Mas `VariableForm` só tem `name`/`value` e `handleSave` chama `saveVariable(name, value)` — `category` fica sempre `""`.

### 2.7 12.2 — sem anti‑repetição
- `frequency_penalty`/`presence_penalty` **não** estão em `_TUNING_KEYS`; `build_kwargs` só emite `id` + os 4 keys ([model_factory.py:56‑81](../ai_engine/model_factory.py#L56)) — qualquer key extra é descartada. Zero ocorrências no repo.
- `get_context` ([message_repo.py:87‑101](../db/repositories/message_repo.py#L87)) e `get_context_messages` ([memory.py:393‑432](../agent/memory.py#L393)) **não** deduplicam mensagens assistant idênticas.

### 2.8 Falsos positivos / já resolvido — NÃO mexer

| Suspeita | Por que NÃO é ponto de mudança |
|----------|-------------------------------|
| split_messages vaza `["…` truncado ou `[]` | **Já corrigido**: `_salvage_split_array` + `parse_split_reply` ([server/helpers.py:38,74](../server/helpers.py#L38)). |
| Mensagem de bloqueio diz "nesta conversa" | **Já corrigido**: diz "nesta mensagem" ([ai_engine/hooks.py:120‑124](../ai_engine/hooks.py#L120)). |
| Sem teto de iterações de tool‑call | **Já corrigido**: `tool_call_limit=25` (env>config>default) no `_build_single_agent` ([agno_engine.py:326‑331](../agent/agno_engine.py#L326)). |
| Falta retorno automático à triagem (GAP‑1) | Fora de escopo (D6) — resolvido via prompt dos sub‑agentes. |
| `requires_prior_call`/`call_limit` por‑conversa (GAP‑2) | Fora de escopo (D7) — mantém por‑mensagem. |
| "A análise (`generate_improvement`) está errada" | Não — a qualidade é ótima (validada D1‑D11 no QA). O escopo aqui é **onde salva** (D.A) e **o que a análise enxerga** em multi‑canal (C1+). |

---

## 3. Inventário / análise

| # | Correção | Onde (arquivo:linha) | Abordagem | Risco | Esforço |
|---|----------|----------------------|-----------|-------|---------|
| D.A | Card no inbox errado | `contacts.py:966‑969` (+ front `useMessageActions.js:106`, `api.js:251`) | Frontend envia `conversation_id`; `_save` usa `_channel_for`+`_get_contact(channel_id=…)`+row de `add_message`; valida posse; fallback default | baixo | S |
| C1+ | Análise cega a canal/agentes | `improvement_service.py:31‑154` (+ `contacts.py:958`) | Threadar `conversation_id`; histórico por `get_by_conversation`; reconstruir cadeia via `routing_steps`; render do prompt de cada agente; tools por `agent_key`; allowlist por agente | médio | M |
| A5 | Mudez silenciosa | `model_factory.py:76`, `agno_engine.py:366‑392`, `messaging_service.py:835,978`, `AgentsManager.js:338`, `VariablesEditor.js` | Piso `max_tokens`; `logger.warning` de reply vazio (+ card `error` opcional); aviso na UI | médio | M |
| A4 | Nome reservado efeito duplo | `VariablesEditor.js:16‑23` (+ opcional backend) | Aviso/bloqueio quando o nome ∈ reservados (`_TUNING_KEYS`+aliases+`{param}_{key}`) | baixo | S |
| A1 | Backend não valida nome | `ai_engine.py:301‑306` | Regex `^[a-zA-Z][a-zA-Z0-9_]{0,63}$` → `_err(400)` | baixo | S |
| A2 | Coluna `category` morta | `ai_engine.py:299‑306`, `variable_repo.py:45‑53`, `db/tables.py:605‑613`, migration `0037` | Remover param do endpoint/repo; `DROP COLUMN`; migration guardada+reversível | médio | S |
| 12.2 | Sem anti‑repetição | `model_factory.py:22,28,56‑81`; `message_repo.py:87` **ou** `memory.py:393` | Add `frequency_penalty`/`presence_penalty` a `_TUNING_KEYS`+`_FLOAT_KEYS`; dedup opcional na camada de contexto | baixo | M |

---

## 4. Fases / Roadmap

Três workstreams independentes (arquivos disjuntos entre si, exceto coordenação interna citada). Diagrama:

```
WAVE 0  (3 workstreams em paralelo)
  ── MELHORIA ──  F1 (frontend: envia conversation_id)     🔴 bloqueia F2, F3
  ── MOTOR ─────  F4 (A5) ─► F5 (12.2)                      mesmo model_factory.py → sequência interna
  ── VARIÁVEIS ─  F6 (A1+A4) ─► F7 (A2)                     mesmo ai_engine.py → sequência interna
        (barreira só dentro da MELHORIA: F1 libera F2/F3)
WAVE 1
  ── MELHORIA ──  F2 (D.A backend) · F3 (C1+ backend)       🟢 ambos dependem de F1, entre si independentes
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|------|------|-----------|-------|-------|---------------|
| 0 | **F1** | MELHORIA · frontend | 🔴 [bloqueia F2, F3] | baixo | Request de `/improve` inclui `conversation_id` (Network); msg sem id manda `null` |
| 0 | **F4** | MOTOR · A5 | 🔴 [coordena model_factory com F5] | médio | `max_tokens` baixo não emudece; reply vazio loga aviso; UI avisa |
| 0 | **F5** | MOTOR · 12.2 | 🟢 [depende de F4 no mesmo arquivo] | baixo | `frequency_penalty`/`presence_penalty` configuráveis chegam ao modelo |
| 0 | **F6** | VARIÁVEIS · A1+A4 | 🔴 [coordena ai_engine com F7] | baixo | Nome inválido → 400; nome reservado → aviso na UI |
| 0 | **F7** | VARIÁVEIS · A2 | 🟢 [depende de F6 no mesmo arquivo] | médio | `category` some da API/UI/schema; migration round‑trip verde |
| 1 | **F2** | MELHORIA · D.A backend | 🟢 [depende de F1] | baixo | Card na conversa real (multi‑canal), sem conversa fantasma |
| 1 | **F3** | MELHORIA · C1+ backend | 🟢 [depende de F1] | médio | Análise mostra prompt de router+spoke e tools por agente |

> **Nota de paralelização:** F1, F4→F5 e F6→F7 podem ser despachados **juntos** (workstreams disjuntos). Só F2/F3 esperam F1. Dentro de MOTOR e VARIÁVEIS a sequência é por **coincidência de arquivo** (evitar conflito de merge), não por dependência lógica.

---

### Fase F1 — Frontend: enviar o `conversation_id` da resposta marcada
**Objetivo:** o payload de "Gerar melhoria" passa a carregar o `conversation_id` da mensagem marcada (pré‑requisito de D.A **e** C1+).

**Itens:**
1. `[sequencial]` `submitImprovement` ([useMessageActions.js:106‑113](../web/static/js/components/contacts/hooks/useMessageActions.js#L106)): incluir `conversation_id` no corpo, reusando o padrão da linha ~81 (`const convId = improveDialog.message.conversation_id != null ? improveDialog.message.conversation_id : conversationId;`).
2. `[sequencial]` `generateImprovement` ([api.js:251‑254](../web/static/js/services/api.js#L251)): repassar `conversation_id` no POST.

**Pronto quando:** o request de `/improve` inclui `conversation_id` (visível no Network). Visão mesclada antiga (msg sem id) manda `null` → cai no fallback do backend.

#### Status de execução — Fase F1
**Estado:** ✅ Concluída
- **O que foi feito:** `submitImprovement` (useMessageActions.js) agora resolve `convId` (mesmo padrão do `messagePermalink`: `message.conversation_id != null ? message.conversation_id : conversationId`) e o passa como `conversationId` a `generateImprovement`; `generateImprovement` (api.js) inclui `conversation_id` no body do POST quando não-nulo (mesmo padrão de `deleteMessage`/`reactToMessage`).
- **Como foi feito / decisões:** quando `convId` é `null` (visão mesclada antiga), o campo é omitido do body — `body.get("conversation_id")` no backend devolve `None`, caindo no fallback D9.
- **Problemas / pendências:** nenhum.
- **Verificação:** syntax-check `node --input-type=module --check` OK nos dois arquivos; `tests/frontend/check_imports.mjs` verde (317 imports / 139 arquivos).

---

### Fase F2 — Backend D.A: salvar o card na conversa da resposta marcada
**Objetivo:** o card `system` é gravado na conversa do `conversation_id` recebido, com validação de posse e fallback.

**Itens** (em `improve_message`/`_save`, [contacts.py:941‑975](../server/routes/contacts.py#L941)):
1. `[sequencial]` Ler o alvo: `conv_id = (target.get("conversation_id")) or body.get("conversation_id")`.
2. `[sequencial]` **Validar posse** (defensivo): se `conv_id` vier, confirmar `conversation_repo.get(conv_id)` existe e `conv["contact_id"] == contact.id` — senão ignora o `conv_id` (cai no fallback). Evita injetar card em conversa de outro contato. (**a confirmar** o nome exato do repo pós‑rename — hoje `conversation_repo`.)
3. `[sequencial]` Reescrever `_save()` espelhando `private_note` ([:1036‑1057](../server/routes/contacts.py#L1036)):
   ```python
   note_channel = _channel_for(phone, conv_id)
   def _save():
       contact = agent_handler._get_contact(phone, channel_id=note_channel)
       return contact.add_message("system", note_text)   # usa a ROW retornada
   saved = await asyncio.to_thread(_save)
   note_msg = { "role": "system", "content": note_text,
                "ts": (saved or {}).get("ts", time.time()),
                "conversation_id": (saved or {}).get("conversation_id") }
   if saved and saved.get("id"): note_msg["_id"] = saved["id"]
   await ws_manager.broadcast("new_message",
       {"phone": phone, "channel_id": note_channel, "message": note_msg})
   ```
   — não usar `get_last` (racy por `contact.id`+ts).
4. `[sequencial]` **Fallback (D9):** sem `conv_id`/inválido → `_channel_for(phone, None)` devolve `"default"` → comportamento idêntico ao atual (single‑channel intacto).

**Pronto quando:** um `/improve` com `conversation_id` de uma conversa de inbox ≠ default grava o card **naquela** conversa (nenhuma conversa nova criada); sem `conversation_id`, comportamento idêntico ao atual. Golden de caracterização atualizado.

#### Status de execução — Fase F2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — nome real do repo de conversa pós‑rename)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(golden + repro multi‑canal)_

---

### Fase F3 — Backend C1+: análise multi‑agente + escopo por conversa
**Objetivo:** `generate_improvement` deixa de ser cega a canal: usa o canal certo, o histórico da conversa marcada, e mostra o prompt de **todos** os agentes do turno com as tools atribuídas por agente.

**Itens** (em [improvement_service.py](../app/services/improvement_service.py)):
1. `[sequencial]` **Assinatura:** `generate_improvement(handler, phone, target_message, feedback, *, conversation_id=None)`; a rota ([contacts.py:958‑959](../server/routes/contacts.py#L958)) repassa `conversation_id` (o mesmo `conv_id` de F2). Derivar `channel_id = _channel_for(phone, conversation_id)` (ou o handler resolve internamente).
2. `[sequencial]` **Canal certo:** `contact = handler._get_contact(phone, channel_id=channel_id)` ([:78](../app/services/improvement_service.py#L78)).
3. `[sequencial]` **Histórico por conversa:** trocar `message_repo.get_context(contact.id, …)` ([:108](../app/services/improvement_service.py#L108)) por `message_repo.get_by_conversation(conversation_id)` (existe, [message_repo.py:71](../db/repositories/message_repo.py#L71)). **A confirmar:** que `get_by_conversation` exclui os roles painel‑only como `get_context` faz — se não, aplicar o mesmo filtro/limite (`handler.max_context_messages`). Fallback para `get_context` quando `conversation_id` ausente (single‑channel).
4. `[sequencial]` **Cadeia de agentes (C1+):** estender `_find_tools_used_around` (ou uma função irmã) para, além dos `tool_executed`, retornar `full["routing_steps"]` (parse JSON) e os `steps` com `agent_key`. Reconstruir a ordem: `chain = [routing_steps[0]["from"]] + [s["to"] for s in routing_steps]` (dedupe preservando ordem), ou `[execution.agent_key]` quando não há routing.
5. `[sequencial]` **Prompt por agente:** para cada `agent_key` da cadeia, `agent = agent_repo.get(agent_key)` → `render_template(agent["prompt"] or DEFAULT_SYSTEM_PROMPT, dynamic_registry.variables_map())`. Montar a seção "## Prompts dos agentes do turno" com um sub‑bloco por agente (ex.: `### Agente: <display_name> (<key>)<– ROTEADOR se is_router>`). Ordem = cadeia.
6. `[sequencial]` **Tools por agente:** agrupar os `tool_executed` por `step["agent_key"]` e listar sob cada agente ("usou: `tool(args)`").
7. `[sequencial]` **Allowlist por agente (item d):** no bloco "Ferramentas disponíveis", usar a allowlist de cada agente (`spec.tool_names` via `_select_active_tools`/`agent_repo.get(...).tool_names`) em vez de `handler.list_tools()` global. Quando `tool_names is None` = "todas".
8. `[sequencial]` **Modelo da análise:** manter a resolução atual (`improvement_model` → modelo do agente → `DEFAULT_MODEL`, [:152‑154](../app/services/improvement_service.py#L152)); preferir o modelo do agente **ativo** (último da cadeia).
9. `[sequencial]` Atualizar o `analysis_system`/`analysis_user` ([:125‑148](../app/services/improvement_service.py#L125)) para descrever a nova estrutura (múltiplos prompts + tools por agente). **Limitação explícita no texto:** as mensagens do histórico não são atribuíveis a um agente específico (sem `agent_key` em `messages`).

**Pronto quando:** no fluxo router→spoke, a análise cita o prompt do **router e** do spoke, lista as tools por agente, e o histórico é o da conversa marcada (não mistura canais). Single‑channel: idêntico em conteúdo ao atual (um agente só, fallback `get_context`).

#### Status de execução — Fase F3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(confirmar semântica de `get_by_conversation`; forma da reconstrução de cadeia)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(repro router→spoke; golden)_

---

### Fase F4 — A5: acabar com a mudez silenciosa
**Objetivo:** `max_tokens` baixo não emudece o bot em silêncio; quando o reply sair vazio, há sinal (log/UI).

**Itens:**
1. `[sequencial]` **Piso em `build_kwargs`** ([model_factory.py:75‑78](../ai_engine/model_factory.py#L75)): `kwargs["max_tokens"] = max(int(params.get("max_tokens") or default_max_tokens), MIN_MAX_TOKENS)`. Constante nova `MIN_MAX_TOKENS` (ver P1 — valor + se é maior quando `reasoning_effort` presente). Manter o `try/except` (valor inválido → `default_max_tokens`).
2. `[paralelo]` **Log de reply vazio** ([agno_engine.py:366‑392](../agent/agno_engine.py#L366)): `logger.warning("empty reply for %s (completion_tokens=%s) — possível max_tokens baixo demais", …)` antes de retornar `""` (ambos os pontos de retorno vazio). (`_extract_reply` não tem `phone`; logar no call site do `run_async`/`run_sync` quando `result.reply == ""` também serve.)
3. `[paralelo]` **`else` nos call sites** `if result.reply:` ([messaging_service.py:835](../app/services/messaging_service.py#L835) e [:978](../app/services/messaging_service.py#L978)): adicionar `else: logger.warning("[Batch] IA não produziu resposta para %s (nada enviado)", phone)`.
4. `[opcional]` **Card painel‑only** `role="error"` "⚠️ A IA não produziu resposta (verifique max_tokens)" via `contact.add_message("error", …)` + `broadcast("new_message")`, para dar sinal visível ao operador (ver P2).
5. `[paralelo]` **Aviso na UI:** input `max_tokens` em `AgentsManager.js` ([:338‑341](../web/static/js/components/ai/AgentsManager.js#L338)) — texto de ajuda "modelos de raciocínio precisam de orçamento alto; valores baixos podem zerar a resposta". Mesmo aviso quando o nome/valor de `max_tokens` aparecer no `VariablesEditor.js` (combina com F6/A4).

**Pronto quando:** com `max_tokens` baixo, o bot **responde** (piso aplicado) OU, se ainda vazio, aparece **warning no log** (e card, se P2=sim). UI mostra o aviso. `tests/test_model_factory.py` continua verde (ajustar expectativas se o piso alterar algum caso).

#### Status de execução — Fase F4
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(valor do piso; se card painel‑only entrou)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(test_model_factory verde; teste manual de max_tokens baixo)_

---

### Fase F5 — 12.2: expor penalties (+ dedup opcional)
**Objetivo:** dá pra configurar `frequency_penalty`/`presence_penalty`; opcionalmente o contexto do LLM não repassa respostas assistant idênticas repetidas.

**Itens** (em [model_factory.py](../ai_engine/model_factory.py)):
1. `[sequencial]` Add `"frequency_penalty"`, `"presence_penalty"` a `_TUNING_KEYS` ([:22](../ai_engine/model_factory.py#L22)) e a `_FLOAT_KEYS` ([:28](../ai_engine/model_factory.py#L28)) — `build_kwargs` já coage `_FLOAT_KEYS` e emite ([:69‑74](../ai_engine/model_factory.py#L69)); assim ficam configuráveis por `model_config`, `{param}_{agent_key}` e global (mesma cascata dos outros).
2. `[sequencial]` **A confirmar:** que `OpenAILike(**kwargs)` aceita `frequency_penalty`/`presence_penalty` (padrão OpenAI; se o wrapper AGNO não os expuser como campo, passar via `request_params`/equivalente). Testar contra o proxy Techify.
3. `[opcional]` **Dedup de contexto** (ver P2): colapsar mensagens `assistant` textualmente idênticas **adjacentes** SÓ na montagem do contexto do LLM — em `get_context` ([message_repo.py:87‑101](../db/repositories/message_repo.py#L87)) **ou** `get_context_messages` ([memory.py:393‑432](../agent/memory.py#L393)). **Não** alterar o histórico persistido nem o render do painel.

**Pronto quando:** setar `frequency_penalty=0.5` (config/variável/agente) reflete no kwargs do modelo (unit test) e no comportamento ao vivo. Se o dedup entrar: histórico com 3 respostas idênticas manda 1 ao LLM, painel inalterado.

#### Status de execução — Fase F5
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(dedup entrou ou não; confirmação do OpenAILike)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(test_model_factory + teste ao vivo do penalty)_

---

### Fase F6 — A1 + A4: validação e aviso de nome de variável
**Objetivo:** o backend rejeita nomes que nunca renderizam; a UI avisa quando o nome colide com um parâmetro reservado.

**Itens:**
1. `[sequencial]` **A1 (backend):** em `save_variable` ([ai_engine.py:301‑306](../server/routes/ai_engine.py#L301)), validar `name` contra `^[a-zA-Z][a-zA-Z0-9_]{0,63}$` → `_err("Nome inválido…", 400)` quando não casar (paridade com `NAME_RE` do front).
2. `[paralelo]` **A4 (UI):** em `VariablesEditor.js` ([:16‑23](../web/static/js/components/ai/VariablesEditor.js#L16)), avisar/bloquear quando o nome ∈ reservados: os 4 canônicos + aliases + (após F5) `frequency_penalty`/`presence_penalty`, e a forma `<reservado>_<agent_key>`. Aviso do tipo "esse nome também altera um parâmetro do modelo". (Bloquear vs só avisar → P3.)
3. `[opcional]` Espelhar o aviso de reservado no backend (retornar warning não‑fatal ou 400), se P3 = bloquear.

**Pronto quando:** `PUT /api/ai/variables/nome-invalido` → 400; criar `temperature` na UI mostra o aviso. `tests/test_endpoints.py` cobre o 400 (novo check).

#### Status de execução — Fase F6
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(bloquear ou avisar em A4)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(test_endpoints + teste manual UI)_

---

### Fase F7 — A2: remover a coluna `category` (migration)
**Objetivo:** `category` some do endpoint, do repo, do schema e do banco.

**Itens:**
1. `[sequencial]` **Endpoint** ([ai_engine.py:299‑306](../server/routes/ai_engine.py#L299)): remover a leitura/encaminhamento de `category`.
2. `[sequencial]` **Repo** ([variable_repo.py:45‑53](../db/repositories/variable_repo.py#L45)): remover o param `category` de `save` e a coluna do upsert (`update_cols`); conferir `as_map()`/`list_all()` (não dependem de `category`).
3. `[sequencial]` **Schema** ([db/tables.py:605‑613](../db/tables.py#L605)): remover a `Column("category", …)` de `ai_variables`.
4. `[sequencial]` **Migration `0037`** (`db/alembic/versions/20260703_0037_drop_ai_variables_category.py`): `revision="0037_drop_ai_variables_category"`, `down_revision="0036_atend_open_unique"` (**a confirmar** o head no momento). `upgrade`: guardado (`sa.inspect(conn)` — se a coluna existir, `op.drop_column("ai_variables","category")`). `downgrade`: re‑adiciona `Column("category", Text, nullable=False, server_default="")`. Idempotente + reversível, no estilo de [20260702_0036](../db/alembic/versions/20260702_0036_atend_open_unique.py).
5. `[paralelo]` Confirmar que `VariablesEditor.js` não **lê** `category` em nenhum lugar (o `VariableForm` só usa `name`/`value`).

**Pronto quando:** migration `upgrade`/`downgrade` round‑trip verde no Postgres de teste; CRUD de variáveis funciona sem `category`; `tests/test_endpoints.py` verde.

#### Status de execução — Fase F7
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(head Alembic confirmado; nome do arquivo)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(migration round‑trip + test_endpoints)_

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| `conversation_id` de outro contato (F2) | Card injetado em conversa alheia | Validar `conv.contact_id == contact.id` antes de salvar (F2 item 2). |
| Frontend antigo / msg sem `conversation_id` | `null` no payload | Fallback `_channel_for(phone, None)`→`default` (D9). |
| `get_last` pega thread errada (F2) | Broadcast do card errado | Usar a **row** de `add_message`, não `get_last`. |
| `get_by_conversation` inclui roles painel‑only (F3) | Histórico da análise poluído por `tool_call`/`conversation_event` | Confirmar/replicar o filtro de `get_context` (exclui painel‑only). |
| Piso `max_tokens` (F4) | Mudar o valor que o usuário setou de propósito | É intencional (evita mudez); documentar no aviso da UI; escolher piso conservador (P1). |
| `OpenAILike` não aceita os penalties (F5) | Kwarg ignorado/erro | Confirmar via teste ao vivo; se preciso, passar por `request_params`. |
| Dedup de contexto agressivo (F5) | Perder contexto legítimo | Só colapsar **assistant idênticas adjacentes**; nunca tocar histórico persistido. |
| Migration `0037` fora de ordem (F7) | Falha no `alembic upgrade` | `down_revision` = head real no momento (hoje `0036`); guardada+idempotente. |
| `ai_engine/*` fora do `--reload-dir` | Mudança em `model_factory.py`/`ai_engine.py` não pega em dev sem restart | Reiniciar o worker após F4/F5/F6 (`pkill -f "uvicorn server.dev"` + relançar). |
| Modo escuro (F4/F6 UI) | Aviso novo ilegível no dark | Usar classes `wa-*`/`.wa-field` (regra do CLAUDE.md). |
| Caracterização do improve (F2/F3) | Golden vermelho | Atualizar `test_sandbox_improve_characterization.py` (aditivo). |

---

## 6. Perguntas em aberto

**P1 — Valor do piso de `max_tokens` (F4).** ⏸️ A DECIDIR na execução. Opções: (a) piso único (ex.: `256`); (b) piso condicional — maior (ex.: `1024`) quando `reasoning_effort` presente, menor (ex.: `256`) senão. **Recomendação:** (b) — modelos de raciocínio são o caso que emudece; `deepseek-v4-pro` é o default. Documentar o número escolhido no aviso da UI.

**P2 — Card painel‑only de "IA não produziu resposta" (F4) e dedup de contexto (F5) são opcionais.** ⏸️ A DECIDIR. **Recomendação:** F4 card = **sim** (sinal visível barato); F5 dedup = **sim, leve** (só idênticas adjacentes) — ataca diretamente a degeneração do §12.2. Ambos podem ficar para um follow‑up se o tempo apertar; o núcleo (piso + log; penalties) já resolve o essencial.

**P3 — A4: bloquear ou só avisar nome reservado (F6).** ⏸️ A DECIDIR. **Recomendação:** **avisar** (não bloquear) — `{param}_{agent_key}` é justamente o mecanismo legítimo de tuning por agente; bloquear quebraria o uso documentado. Aviso claro "esse nome também altera o modelo" basta.

**P4 — `get_by_conversation` exclui roles painel‑only? (F3).** ⏸️ A CONFIRMAR no código antes de trocar `get_context` por ele. Se não excluir, aplicar o mesmo filtro + limite `max_context_messages`.

**P5 — `OpenAILike` aceita `frequency_penalty`/`presence_penalty`? (F5).** ⏸️ A CONFIRMAR ao vivo contra o proxy Techify. Se não expuser como campo, rotear via `request_params`.

---

## 7. Checklist de verificação

- [ ] `python -m pytest tests/characterization/test_sandbox_improve_characterization.py` verde (golden do improve atualizado)
- [ ] `python tests/test_endpoints.py` verde (improve single‑channel inalterado; novo 400 de nome inválido; CRUD de variáveis sem `category`)
- [ ] `WHATSBOT_TEST=1 venv/bin/python tests/test_model_factory.py` verde (piso + penalties)
- [ ] **Repro D.A (F2):** contato com conversa ativa em inbox ≠ default → "Gerar melhoria" → card na conversa real, **sem** conversa fantasma no inbox 1
- [ ] **Regressão single‑channel (F2):** contato no inbox default → card cai na conversa dele como antes
- [ ] **Repro C1+ (F3):** fluxo router→spoke → análise cita o prompt do router **e** do spoke + tools por agente; histórico só da conversa marcada
- [ ] **A5 (F4):** `max_tokens` baixo → bot responde (piso) ou warning no log + (se P2) card painel‑only; UI mostra aviso
- [ ] **12.2 (F5):** `frequency_penalty` setado chega ao modelo; (se P2) dedup manda 1 assistant idêntica ao LLM, painel inalterado
- [ ] **A1/A4 (F6):** `PUT` de nome inválido → 400; UI avisa nome reservado
- [ ] **A2 (F7):** migration `0037` `upgrade`/`downgrade` round‑trip verde no Postgres (`WHATSBOT_TEST_DB_URL`); `category` sumiu de API/UI/schema
- [ ] Modo escuro legível nos avisos novos (F4/F6)
- [ ] Worker reiniciado após mudanças em `ai_engine/*` (dev)
- [ ] Sem segredo em log/URL; card `system`/`error` continua painel‑only (não vai ao WhatsApp)

---

## Apêndice — arquivos‑chave (por fase)

**MELHORIA (F1/F2/F3):**
- [web/static/js/components/contacts/hooks/useMessageActions.js:106‑113](../web/static/js/components/contacts/hooks/useMessageActions.js#L106) — `submitImprovement` (enviar `conversation_id`)
- [web/static/js/services/api.js:251‑254](../web/static/js/services/api.js#L251) — `generateImprovement`
- [server/routes/contacts.py:941‑975](../server/routes/contacts.py#L941) — `improve_message`/`_save` (D.A) + repasse do `conversation_id` ao serviço (C1+)
- [server/routes/contacts.py:1036‑1057](../server/routes/contacts.py#L1036) — `private_note` (padrão de referência)
- [app/services/improvement_service.py](../app/services/improvement_service.py) — `generate_improvement` + `_find_tools_used_around` (C1+)
- [db/repositories/execution_repo.py:84‑117](../db/repositories/execution_repo.py#L84) — `routing_steps` + `steps.agent_key`
- [agent/agent_factory.py:76‑90,205‑248](../agent/agent_factory.py#L76) — `render_template` / `build_for_contact`
- [db/repositories/message_repo.py:71](../db/repositories/message_repo.py#L71) — `get_by_conversation`

**MOTOR (F4/F5):**
- [ai_engine/model_factory.py:22,28,56‑81](../ai_engine/model_factory.py#L22) — `_TUNING_KEYS`/`_FLOAT_KEYS`/`build_kwargs`
- [agent/agno_engine.py:57,366‑392](../agent/agno_engine.py#L366) — `_DEFAULT_MAX_TOKENS` / `_extract_reply`
- [app/services/messaging_service.py:835,978](../app/services/messaging_service.py#L835) — call sites `if result.reply:`
- [web/static/js/components/ai/AgentsManager.js:338‑341](../web/static/js/components/ai/AgentsManager.js#L338) — input `max_tokens`
- [db/repositories/message_repo.py:87‑101](../db/repositories/message_repo.py#L87) / [agent/memory.py:393‑432](../agent/memory.py#L393) — dedup opcional

**VARIÁVEIS (F6/F7):**
- [server/routes/ai_engine.py:293‑315](../server/routes/ai_engine.py#L293) — endpoints de variáveis (A1 validação + A2 remove `category`)
- [db/repositories/variable_repo.py:45‑53](../db/repositories/variable_repo.py#L45) — `save`/`as_map` (A2)
- [db/tables.py:605‑613](../db/tables.py#L605) — `ai_variables` (A2 drop coluna)
- [web/static/js/components/ai/VariablesEditor.js:16‑23](../web/static/js/components/ai/VariablesEditor.js#L16) — `NAME_RE` + aviso reservado (A1/A4)
- `db/alembic/versions/20260703_0037_drop_ai_variables_category.py` — **novo** (A2)
