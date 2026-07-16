# Plano 51 - Gateway no WhatsBot (sub-plano 02)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-16 · **Escopo:** grande (backend do plugin `melhorias` evoluído + 1 ponte HMAC/SSE + 2 famílias de rotas + 3 tabelas novas + costura da multi-seleção)
> **Origem:** Plano 51 "Melhoria agêntica" (decisões D1–D6 travadas no mestre). Este sub-plano é o **gateway**: a ponte assinada `WhatsBot ⇄ executor Claude Code`, as rotas que o operador chama (chat de aprovação) e as rotas `_internal/*` que a IA chama de volta para ler/escrever a config de IA.
> **Método:** engenharia reversa do `ai-server` do nexus (blueprint em `reports/nexus-protocolo.md`, refs `arquivo:linha` do `/opt/nexus/nexus-relatorios`), mapa do plugin `melhorias` atual (`reports/melhoria-atual.md`), mapa das superfícies de config de IA (`reports/tools-agentes.md`) e da infra de versionamento (`reports/versionamento.md`) — todos com `arquivo:linha` verificados neste checkout.
> A forma da solução: **zero mudança no core**. Tudo mora no plugin `melhorias` (fonte git em `assets/plugin_examples/melhorias/`), reusando duas costuras genéricas que o core JÁ tem — a convenção de rota auth-exempt `/api/plugins/<id>/public/` (plano 46 · 01-D) e o `plugins.context.broadcast()` sobre o `/ws` do operador. O executor Claude Code (`:8014`, servidor `CLAUDE-CODE-AUTOMACOES`) é peça separada (sub-plano 04); aqui só se implementa o lado WhatsBot.
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões travadas (herdadas do mestre + específicas deste sub-plano)

| # | Decisão | Consequência |
|---|---------|--------------|
| **D1** (mestre) | Dois gates humanos: (a) aprovar p/ a IA começar + injetar observação; (b) cada mutação exige V/X. IA nunca aplica sozinha. | O `POST .../conversations` (start) é o gate (a); o `POST .../conversations/{cid}/approve` é o gate (b). Fase 3. |
| **D3** (mestre) | Vínculo preciso `messages.execution_id` + captura de contexto exato. | A reconstrução do trace por mensagem (`build_analysis_payload`) casa `execution` pela janela do `ts` da resposta marcada (`generation._find_execution_around`, `generation.py:104-131`). Fase 6. |
| **D5** (mestre) | Padrão de referência = `ai-server` do nexus (HMAC + SSE + aprovação + resume + relogin). Contrato gateway⇄executor estável. | Cliente e verificação HMAC portam `hmac.util.ts`/`hmac.guard.ts`/`ai-server-client.service.ts`. Fases 1–2. |
| **D6** (mestre) | A feature vive no plugin `melhorias` EVOLUÍDO; mudanças chegam por `.zip` re-importado (cópia instalada é gitignored). | Tudo é edição de `assets/plugin_examples/melhorias/*`. Nada no core. |
| **D02-a** 🆕 | Os headers HMAC são renomeados de `X-Relatorios-*` para **`X-WB-*`** (`X-WB-Timestamp/Signature/Request-Id/On-Behalf-Of`). Assinatura sobre a string canônica `METHOD\npath\nts\nrequestId\nbody`, janela 60s. | Fases 1–2. |
| **D02-b** 🆕 | As rotas `_internal/*` (chamadas pelo executor, sem sessão de operador) ficam sob **`/api/plugins/melhorias/public/_internal/...`** — a ÚNICA forma auth-exempt sem tocar no core (`PLUGIN_PUBLIC_PATH_RE`, `server/app.py:53`). Autenticação real = a dependency HMAC. "public" é a convenção de "a rota se autentica sozinha", já usada pelo widget. | Fase 4. |
| **D02-c** 🆕 | On-behalf-of: a dependency HMAC resolve `X-WB-On-Behalf-Of` → `user_repo.get(id)` (`user_repo.py:32`) → carimba `request.state.user`; cada mutação reaplica RBAC via `authz.acheck(request, key)` (`server/authz.py:50`), reusando o MESMO primitivo do core (inclui o seam ABAC `filter.authz.decision`). Instalação aberta/legada (sem usuários) ⇒ default-allow, idêntico ao core. | Fase 4. |
| **D02-d** 🆕 | **Browser recebe eventos por WS (reuso do `/ws` do operador), NÃO por SSE** (ver §3). O hop `executor→gateway` continua **SSE** (fidelidade ao contrato D5); o gateway consome a SSE server-side e re-emite cada evento como `broadcast("plugin_melhorias_ai_*", {conversation_id, ...})`. | Fases 3, 6. |
| **D02-e** 🆕 | Feature **dormente por gate**: a dependency HMAC responde **404** (finge que a rota não existe, molde `hmac.guard.ts:58-61`) quando `plugin.melhorias.generator_backend != "external"` OU o secret não está setado. Nada agêntico roda até o operador configurar url+secret e trocar o backend. | Fases 2, 4. |

---

## 1. Resumo executivo

O plugin `melhorias` hoje registra um **pedido pendente** e, na aprovação, gera **uma análise-texto one-shot** via `DirectApiGenerator` (`generation.py:185-355`), chamada síncrona dentro do `POST .../approve`. Este sub-plano transforma o "aprovar" em **abrir uma conversa agêntica** com um Claude Code externo, que raciocina, chama tools de leitura da config de IA, propõe mutações (cada uma com V/X humano) e só então escreve — versionado — via as rotas `_internal/*` que reusam os repos de IA já existentes (`agent_repo`/`tool_repo`/`tool_override_repo`/`variable_repo`).

Quatro blocos de trabalho:

- **A ponte (Fases 1–2):** um cliente `httpx` que assina requests HMAC ao executor, e uma dependency FastAPI que valida a assinatura HMAC + nonce dos callbacks do executor. Portadas verbatim do nexus, com headers `X-WB-*`.
- **As rotas do operador (Fase 3):** start/get/send/approve/cancel/resume da conversa agêntica + relogin OAuth, gated pelo RBAC do plugin (`plugin_permission`). O stream vai por WS reusado (§3), não por SSE ao browser.
- **As rotas `_internal/*` (Fase 4):** as "tools" que a IA chama de volta — READ (ler agente/tool/variável/trace) e MUTATION (salvar agente/prompt/tool/override/variável, com rollback), cada mutação reaplicando RBAC com o usuário on-behalf-of. Reusa os repos direto; NÃO duplica lógica.
- **Dados + multi-seleção + gerador externo (Fases 5–6):** 3 tabelas novas do plugin (conversations/messages/approvals), migration 002 para N mensagens selecionadas, e o `ExternalAgentGenerator` registrado no `_BACKENDS`, selecionado por `plugin.melhorias.generator_backend="external"`; `decide_suggestion` deixa de gerar inline e passa a abrir a conversa (estado `gerando`/`em_chat`), fechada por callback assíncrono do executor.

**Zero `if provider ==`, zero mudança no core.** As únicas costuras do core tocadas são reusadas como estão: `/public/` auth-exempt, `broadcast()`, `plugin_permission`, `authz.acheck`, `make_plugin_db`, e os repos de IA.

---

## 2. Como funciona hoje (mapa)

### 2.1 O plugin `melhorias` atual

| Costura | Onde | Observação |
|---------|------|------------|
| Cria pedido pendente | `logic.create_suggestion` (`logic.py:126-179`) | INSERT single-row; valida posse do `conversation_id` (`:143-151`); posta aviso de sistema (`_post_system_notice`, `:182-211`); broadcast `plugin_melhorias_changed`. |
| Gera análise NA aprovação | `logic.decide_suggestion(...,"aprovada")` (`logic.py:214-259`, chamada em `:244`) | Síncrono: `generation.get_generator().generate(ctx)` → grava `analysis`/`model` → marca `aprovada`. |
| Rota approve (bloqueante) | `routes.py:103-112` → `asyncio.to_thread(logic.decide_suggestion,...)` | O handler LLM vem de `_handler(request)` (`routes.py:29-33`, `get_deps()` ou `app.state.deps`). |
| Seam de geração | `generation.SuggestionGenerator` Protocol (`generation.py:98-99`) + `_BACKENDS` (`:380-384`) + `get_generator()` (`:387-391`, key `plugin.melhorias.generator_backend`, default `"direct"`) | **O ponto de extensão pronto**: `MultiAgentGenerator` (`:358-365`) é o stub oficial. |
| Montagem do contexto | inline em `DirectApiGenerator.generate` (`generation.py:209-309`) | Cadeia de agentes (`_agent_chain`, `:145-175`), tools usadas (`_tools_used`, `:134-142`), execução (`_find_execution_around`, `:104-131`), prompt inline cru por agente (`:231-263`), histórico filtrado por regex (plano 43, `:279-287`). **É o candidato a extrair** para `build_analysis_payload`. |
| Config modelo/prompt | `routes.py:129-150` (GET/PUT `/config`), lida por `logic._setting` (`logic.py:63-64`) | Namespace `plugin.melhorias.model`/`.prompt`. Extensível para os campos do gateway. |
| Dados | `plugin_melhorias_suggestions` (`migrations/001_initial.sql:10-31`) | **Single-message**: colunas `message_db_id`/`message_ts`/`message_content` (`:16-18`). `_STATUSES=("pendente","aprovada","recusada")` (`logic.py:34`). |
| RBAC | `plugin.yaml:41-47` | `request`, `view`, `approve`, `configure`. |
| Frontend | `static/extends.js:40-50` (filtro `filter.message.contextMenu.items`) + `ImproveDialog.submit` (`:69-83`) | Envia `message:{content,ts,_id}` **singular** + `feedback` + `conversation_id` + `phone`. |

### 2.2 Costuras do core reusadas (não mudam)

| Costura | Onde | Uso neste sub-plano |
|---------|------|---------------------|
| Rota plugin auth-exempt | `PLUGIN_PUBLIC_PATH_RE = ^/api/plugins/<id>/public/` (`server/app.py:53`), checada em `auth_middleware` (`:496-497`) | As rotas `_internal/*` do executor entram sob `/public/_internal/` (D02-b). |
| Router de plugin montado | `app.include_router(loaded.router, prefix="/api/plugins/<id>")` (`server/app.py:715-716`) | Todas as rotas novas são `@router.<verb>` no `routes.py`. Aceita `@router.websocket(...)` (precedente widget, `website/routes.py:268`). |
| Dependency RBAC | `plugin_permission(key)` (`plugins/context.py:212-244`, infere id do path via `_PLUGIN_PATH_RE`, `:209`) | Gate das rotas do operador (Fase 3). |
| Decisão central RBAC + ABAC | `authz.check`/`acheck` (`server/authz.py:29-64`), primitivo `_rbac_allows` (`:22-26`), `rbac_repo.user_has_permission` (`rbac_repo.py:87`) | Re-check on-behalf-of nas mutações `_internal` (Fase 4). |
| Usuário do request | `request.state.user` resolvido em `auth_middleware` (`server/app.py:519-534`); `user_repo.get(id)` (`user_repo.py:32`) | A dependency HMAC carimba `request.state.user` a partir do `On-Behalf-Of`. |
| Broadcast WS | `plugins.context.broadcast(event, data)` (`plugins/context.py:146-157`) sobre o `/ws` do operador (`server/routes/websocket.py:17-33`) | Entrega dos eventos do chat ao painel (D02-d, §3). |
| DB do plugin | `make_plugin_db()` (`plugins/context.py:174-177`, `engine.begin()`) | Todas as 3 tabelas novas. |
| Repos de IA (write-through versionado) | `agent_repo.save`/`rollback` (`agent_repo.py:134`/`:339`), `tool_repo.save`/`rollback` (`tool_repo.py:61`/`:157`), `tool_override_repo.upsert` (`tool_override_repo.py:96`), `variable_repo.save` (`variable_repo.py:45`) | As mutações `_internal` chamam esses repos — NÃO reimplementam. |
| Loop de fundo do plugin | `ctx.spawn_task` (`plugins/context.py:316`), `stop_owner` no disable | Se o gateway consumir a SSE do executor num loop (§3). |

⚠️ **Gotchas que tornam algo obrigatório:**

- ⚠️ **`_internal/*` PRECISA ser auth-exempt.** Se ficarem sob `/api/plugins/melhorias/_internal/...` (sem `/public/`), o `auth_middleware` roda e, com `has_users=true` (RBAC ligado), devolve **401** ANTES da rota (`server/app.py:520-534`) — o executor não tem bearer de operador. Só `/public/` escapa sem tocar no core (D02-b).
- ⚠️ **Assinatura HMAC sobre BYTES CRUS.** O guard do nexus reserializa `JSON.stringify(req.body)` (`hmac.guard.ts`) e o próprio blueprint alerta o risco. No WhatsBot, assine/valide sobre `await request.body()` (bytes exatos), NUNCA re-serialize um dict — ordenação de chaves Python≠Node quebra o HMAC.
- ⚠️ **`authz.check(request, key)` lê `request.state.user`** — não existe assinatura `check(user=...)`. Para o on-behalf-of, a dependency HMAC deve **carimbar `request.state.user`** com o usuário resolvido e então chamar `authz.acheck(request, key)`. Não invente um caminho paralelo de RBAC.
- ⚠️ **Comentário de migration não pode conter `;`** — o migrator faz split por `;` ANTES de tirar comentários (`migrations/001_initial.sql:6` já avisa; nota de memória `plugin-migrator-splits-sql-by-semicolon`). Migration 002 idem.
- ⚠️ **Prefixo `plugin_melhorias_` obrigatório** em toda `CREATE TABLE/INDEX` (o migrator recusa o contrário). `id INTEGER PRIMARY KEY AUTOINCREMENT` é traduzido p/ SERIAL; para UUID use `TEXT` + gere o id em Python (padrão do plugin é epoch-float e SERIAL, não há `gen_random_uuid` garantido no path do migrator).
- ⚠️ **Código de `ai_tools` (mutação `_internal/tools`) exige RESTART** para valer (installer só roda no boot; `PUT /api/ai/tools/{name}` agenda `schedule_restart`, `ai_engine.py:421`) **e** o kill-switch `ai_tools_code_enabled` ON. A IA precisa saber que "editar código de tool" não tem efeito imediato — é assíncrono via restart (D4 escopo v1).
- ⚠️ **`ai_variables` não tem versionamento** (`variable_repo.py` sem history/rollback; `reports/versionamento.md` §5d). Se a IA editar variável e precisar reverter, a infra não existe — depende do sub-plano 01 (versionamento de variáveis) OU aceita-se sem rollback no v1.
- ⚠️ **O `broadcast()` é GLOBAL** (todos os operadores no `/ws` recebem). Isso é aceitável (o painel de sugestões já é compartilhado) mas os deltas de token vazam para todos os operadores conectados — mitigado filtrando por `conversation_id` no cliente (§3).

---

## 3. Recomendação: SSE (browser) vs reuso do WS `/ws`

**Recomendação: browser recebe por WS (reuso do `/ws` do operador); SSE fica SÓ no hop executor→gateway.** (D02-d) — **autoridade da decisão: mestre §8 P2**, que unifica esta recomendação com o sub-plano 04 (cujo F4 descreve o caminho SSE-dedicado como alternativa (b)).

O gateway consome a SSE do executor server-side (num `httpx.stream`/`aiter_bytes`, molde `ai-chart-builder.controller.ts:97-140`) e re-emite cada um dos 9 eventos (`reports/nexus-protocolo.md` §2) como `broadcast("plugin_melhorias_ai_delta"/"..._tool"/"..._approval"/"..._done"/"..._error", {conversation_id, sid, ...})`. O painel, que **já mantém** uma conexão `/ws` e já escuta `plugin_melhorias_changed` (`panel.js`), passa a escutar os novos eventos e filtra por `conversation_id`.

| Critério | Reuso do `/ws` (RECOMENDADO) | SSE dedicado ao browser (`GET .../stream`) |
|----------|------------------------------|--------------------------------------------|
| Audiência | Operador **autenticado** (não visitante anônimo) — o `/ws` já é dele | Idem, mas exige 2ª conexão long-lived |
| Conexão nova no browser | **Nenhuma** — reusa a que o painel já tem | Uma por conversa; `fetch`+`ReadableStream` (não `EventSource`, por auth) + parser `\n\n`/`event:`/`data:` reimplementado em Preact |
| Buffering de proxy | Não se aplica (WS) | Precisa `X-Accel-Buffering:no` + `Cache-Control:no-transform`; risco atrás de Coolify/Traefik |
| Isolamento por operador | ⚠️ broadcast global (mitiga filtrando `conversation_id` client-side) | ✅ estrito por request (ownership validado antes de abrir) |
| Precedente no repo | `plugin_melhorias_changed`, `new_message`, `ai_typing` já trafegam assim | `StreamingResponse text/event-stream` só existe p/ export de zip (`server/routes/plugins.py:352`) |
| Fidelidade ao contrato D5 | Mantida — SSE continua no hop executor⇄gateway (estável p/ trocar Claude Code) | Mantida também |

**Quando escolher SSE dedicado:** se, num plano futuro, os deltas de token precisarem de isolamento estrito por-operador (não vazar a outros operadores no `/ws`), adiciona-se `GET /api/plugins/melhorias/conversations/{cid}/stream` (`StreamingResponse`, `X-Accel-Buffering:no`, ownership validado antes de abrir via `plugin_permission("view")` + posse da conversa). O seam é o mesmo — o gateway já consome a SSE do executor; só muda o destino (pipe direto vs broadcast). **Deixar como opção documentada, não implementar no v1.**

---

## 4. Tabela de endpoints

Envelope `{"ok":bool,"data"|"error":...}`. Prefixo do router: `/api/plugins/melhorias`.

### 4.1 Rotas do operador (auth normal do core; gate `plugin_permission`)

| Método | Path | Permissão | Payload → Retorno |
|--------|------|-----------|-------------------|
| POST | `/suggestions/{sid}/conversations` | `plugin.melhorias.approve` (gate D1-a: aprovar p/ IA começar + injetar contexto) | `{observation?, model?}` → `{conversation_id, status:"em_chat"}` |
| GET | `/suggestions/{sid}/conversations` | `plugin.melhorias.view` | — → `[{conversation}]` (conversas agênticas da sugestão) |
| GET | `/conversations/{cid}` | `plugin.melhorias.view` | — → `{conversation, messages:[...], approvals:[...]}` |
| POST | `/conversations/{cid}/messages` | `plugin.melhorias.approve` | `{text}` (observação humana ao agente rodando) → `{ok}` |
| GET | `/conversations/{cid}/stream` *(OPCIONAL — §3)* | `plugin.melhorias.view` | SSE `text/event-stream` (ownership validado antes de abrir) |
| POST | `/conversations/{cid}/approve` | `plugin.melhorias.approve` (gate D1-b: cada mutação V/X) | `{approvalId, approved:bool, reason?:str≤500}` → `{ok}` |
| POST | `/conversations/{cid}/cancel` | `plugin.melhorias.approve` | — → `{status:"cancelada"}` |
| POST | `/conversations/{cid}/resume` | `plugin.melhorias.approve` | — → `{ok}` (recria runner in-memory hidratando do DB) |
| POST | `/admin/relogin/start` | `plugin.melhorias.configure` | `{}` → `{sessionId, url}` |
| POST | `/admin/relogin/complete` | `plugin.melhorias.configure` | `{sessionId, code}` → `{ok}` |
| POST | `/admin/relogin/abort` | `plugin.melhorias.configure` | `{sessionId}` → `{ok}` |
| GET/PUT | `/config` (estendido) | `view` / `configure` | GET → `{model, prompt, prompt_default, ai_server_url, ai_server_secret:"***", ai_model, ai_timeout_ms, generator_backend}`; PUT preserva secret vazio/`***` |

### 4.2 Rotas `_internal/*` (HMAC + on-behalf-of; auth-exempt via `/public/` — D02-b)

Todas sob `/api/plugins/melhorias/public/_internal/...`, com `Depends(hmac_guard)` (Fase 2). **Write-through de persistência** (sem RBAC — só grava o que o executor manda, molde nexus):

| Método | Path (sob `/public/_internal`) | Auth | Payload |
|--------|-------------------------------|------|---------|
| POST | `/messages` | HMAC+OBO | `{conversation_id, role, content?, tool_name?, tool_input?, tool_result?, token_usage?}` (append-only) |
| POST | `/approvals` | HMAC+OBO | `{approval_id, conversation_id, tool_name, tool_input, summary?}` (→ broadcast `approval_needed`) |
| POST | `/conversation-status` | HMAC+OBO | `{conversation_id, status: ACTIVE\|COMPLETED\|CANCELLED\|ERRORED}` |

**Leitura da config de IA** (tools READ da IA; `assertPermission("agent.config.manage")` via OBO):

| Método | Path | Payload / Query → Retorno |
|--------|------|---------------------------|
| GET | `/message-trace?msg_id=` | reusa helpers de `build_analysis_payload` (Fase 6) → `{execution, agent_chain, tools_used, agents:[{prompt,tools}], history}` |
| GET | `/agents` , `/agents/{key}` | → `agent_repo.list()` / `agent_repo.get(key)` |
| GET | `/tools` , `/tools/{name}` | → `handler.list_tools()` / `tool_repo.get(name)` |
| GET | `/variables` | → `variable_repo.as_map()` |
| GET | `/active-agent?conversation_id=` | → `resolve_active_agent_key` (conversation→inbox→default) |

**Mutação da config de IA** (aprovação já resolvida no executor; cada uma REAPLICA RBAC via `authz.acheck` com o OBO):

| Método | Path | Permissão reaplicada | Repo (reuso direto) |
|--------|------|----------------------|---------------------|
| POST | `/agents/{key}` | `agent.config.manage` (+`agent.create` se novo, +`agent.prompts.edit` p/ o campo prompt) | `agent_repo.save(...)` versionado |
| PATCH | `/agents/{key}/prompt` | `agent.prompts.edit` | `agent_repo.save(...)` patch-só-prompt (semântica de `PUT .../prompt`) |
| POST | `/tools/{name}` | `agent.tools.manage` | `tool_repo.save(...)` (+ `schedule_restart`, kill-switch) |
| POST | `/tools/{name}/override` | `agent.tools.manage` | `tool_override_repo.upsert(...)` (sem history) |
| PUT | `/variables/{name}` | `agent.variables.manage` | `variable_repo.save(...)` (sem versionamento — ⚠️ gap) |
| POST | `/agents/{key}/rollback/{version}` | `agent.prompts.version` | `agent_repo.rollback(...)` |
| POST | `/tools/{name}/rollback/{version}` | `agent.tools.manage` | `tool_repo.rollback(...)` |

---

## 5. Falsos positivos descartados

| "Parece problema" | Por que NÃO é |
|-------------------|----------------|
| "Precisa de novo prefixo auth-exempt no core para o executor" | Não. `/api/plugins/melhorias/public/_internal/` já é exempt via `PLUGIN_PUBLIC_PATH_RE` (`server/app.py:53`). D02-b. |
| "Precisa reimplementar RBAC para o on-behalf-of" | Não. Carimba `request.state.user` e chama `authz.acheck(request, key)` — o MESMO primitivo do core, inclui o seam ABAC. D02-c. |
| "O gateway precisa expor a chave do LLM/`_get_client` ao executor" | Não. O executor tem a própria auth Claude (OAuth `~/.claude`, D2/D5). O `handler._get_client` do `DirectApiGenerator` continua sendo do backend `direct`, não do agêntico. |
| "Precisa de novo endpoint SSE ao browser" | Não no v1. Reusa o `/ws` do operador (§3). O `GET .../stream` é opção documentada. |
| "`decide_suggestion` precisa virar totalmente novo" | Não. Mantém a assinatura; muda só o ramo `"aprovada"`: em vez de `generator.generate(ctx)` inline, abre a conversa agêntica e retorna `em_chat` (Fase 6). O ramo `"recusada"` fica intacto. |
| "As mutações `_internal` precisam de lógica de versionamento nova" | Não. `agent_repo.save`/`tool_repo.save` já bumpam versão + snapshot (`reports/versionamento.md` §2). Reusa direto. |
| "Precisa registrar um WebSocket próprio para o chat" | Não. O `/ws` do operador + `broadcast()` bastam (o operador já está autenticado). WS próprio só o widget precisa (visitante anônimo). |
| "Multi-seleção exige mudar o funil de ingestão de mensagens" | Não. É só o payload `messages:[...]` no `POST /suggestions` + coluna/tabela filha. O funil de mensagens do WhatsApp não é tocado. |

---

## 6. Fases

### Fase 1 — Cliente HMAC (httpx) 🟢 [bloqueia: 3, 6]

**Objetivo:** um cliente `httpx.AsyncClient` que assina toda request ao executor com HMAC-SHA256 e expõe os métodos que o gateway chama.

**Itens:**
- [sequencial] Criar `assets/plugin_examples/melhorias/ai_client.py` — porta de `ai-server-client.service.ts`. Função `sign(secret, method, path, ts, request_id, body) -> str`: `payload = "\n".join([method.upper(), path, ts, request_id, body])`; `hmac.new(secret.encode(), payload.encode(), sha256).hexdigest()` (molde `hmac.util.ts:14-22`). `path` sem query; `body` = string JSON EXATA enviada (o corpo que sairá no wire) ou `""` em GET.
- [paralelo] Headers em `signed_headers(...)`: `X-WB-Timestamp` (unix seconds str), `X-WB-Signature` (hex), `X-WB-Request-Id` (`f"{int(time*1000):x}-{secrets.token_hex(4)}"`, molde `hmac.util.ts:70-77`), `X-WB-On-Behalf-Of` (user id RBAC). `Content-Type: application/json` nos POST; `Accept: text/event-stream` no `open_stream`.
- [paralelo] Métodos: `start(...)`, `resume(...)`, `send(...)`, `approve(...)`, `cancel(...)` (POST assinados, molde `postJson`); `relogin_start/complete/abort` (start lê o body → `{sessionId,url}`, molde `postAndReturn`); `open_stream(conversation_id) -> AsyncIterator[bytes]` via `httpx.stream("GET", ...)` + `aiter_bytes()`.
- [paralelo] Config resolvida DB>env>default em helpers no `logic.py` (estende `_setting`): `ai_server_url` (env `WHATSBOT_MELHORIAS_AI_URL`), `ai_server_secret` (env `WHATSBOT_MELHORIAS_AI_SECRET`, validar `≥32` chars), `ai_model`, `ai_timeout_ms`. Secret mascarado (`"***"`) no `GET /config`; PUT com vazio/`***` **preserva** o atual (molde `settings.service.ts:99-104`). URL normalizada (trim + rstrip `/`).

**Pronto quando:** `python -c "from assets.plugin_examples.melhorias import ai_client"` importa sem erro; um unit test valida que `sign()` reproduz uma assinatura de referência conhecida (vetor fixo secret+method+path+ts+rid+body) e que `signed_headers` traz os 4 `X-WB-*`; `GET /config` devolve o secret mascarado e o PUT preserva secret vazio.

```
#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-07-16, commit f98cea2)
- **O que foi feito:** `ai_client.py` — sign() sobre a string canônica, signed_headers (4 X-WB-*), start/send/approve/cancel/resume/relogin_*/auth_check/open_stream (httpx.stream + aiter_bytes); config DB>env>default (`ai_server_url`/`ai_server_secret`≥32/`ai_model`/`ai_timeout_ms`); secret mascarado no GET e preservado em vazio/*** no PUT.
- **Como foi feito / decisões:** body assinado = a MESMA string compacta enviada no wire (json.dumps separators=(",", ":") ≡ JSON.stringify — sem floats no payload).
- **Problemas / pendências:** nenhum.
- **Verificação:** vetor de assinatura travado em teste; HMAC Python↔Node provado contra o executor real (auth-check 200).
```

### Fase 2 — Verificação HMAC + nonce (dependency FastAPI) 🟢 [bloqueia: 4]

**Objetivo:** uma dependency que valida a assinatura HMAC dos callbacks do executor, sobre os bytes crus, com anti-replay e resolução on-behalf-of, dormente por gate.

**Itens:**
- [sequencial] Criar `assets/plugin_examples/melhorias/hmac_guard.py`. `async def hmac_guard(request: Request)`: ler `raw = await request.body()` (**bytes crus**, ⚠️ NÃO `request.json()`); recompor `payload = method + "\n" + path_sem_query + "\n" + ts + "\n" + rid + "\n" + raw.decode()` (molde `hmac.util.ts` + guard `req.url.split('?')[0]`).
- [paralelo] Janela 60s: `abs(now - int(ts)) <= 60` (`hmac.util.ts:6,52-55`). Comparação: checar `len(expected)==len(sig)` antes de `hmac.compare_digest` (`:60-67`).
- [paralelo] Nonce LRU in-memory: `dict` cap **5000**, TTL **5min** (`hmac.guard.ts:14-39`); evict expirados; `rid` ausente ou já visto ⇒ **403**. Module-level (sobrevive entre requests do mesmo processo).
- [paralelo] On-behalf-of (D02-c): header `X-WB-On-Behalf-Of` **obrigatório** → `user_repo.get(int(obo))`; carimba `request.state.user = user` (para as mutações reaplicarem RBAC via `authz.acheck`). Guardar também em `request.state.hmac = {request_id, on_behalf_of}`.
- [paralelo] Gate dormente (D02-e): se `generator_backend != "external"` OU secret não setado/`<32` ⇒ levantar **404** (finge inexistência, molde `hmac.guard.ts:58-61`).

**Pronto quando:** um teste monta um request com corpo bytes + assinatura válida e passa; corpo alterado após assinar ⇒ 403; `ts` fora da janela ⇒ 403; `rid` repetido ⇒ 403; `On-Behalf-Of` ausente ⇒ 403; backend != external ⇒ 404. A validação usa `await request.body()` (o teste garante que re-serializar o dict quebraria — assinatura sobre bytes crus).

```
#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-07-16, commit f98cea2)
- **O que foi feito:** `hmac_guard.py` — bytes crus, janela 60s, nonce LRU 5000/5min module-level, On-Behalf-Of obrigatório → user_repo.get → request.state.user (default-allow só em instalação sem usuários), gate dormente 404.
- **Como foi feito / decisões:** conforme plano; OBO inválido com RBAC ativo ⇒ 403 (não deixar a IA agir como "ninguém").
- **Problemas / pendências:** nenhum.
- **Verificação:** testes: corpo alterado/ts fora/replay/OBO ausente ⇒ 403; backend≠external ⇒ 404; sem HMAC nunca dá 401 de operador.
```

### Fase 3 — Rotas do operador + pipe do stream 🟢 [depende de: 1, 2, 5] [bloqueia: 6]

**Objetivo:** os endpoints que o painel chama para iniciar e conduzir a conversa agêntica, gated pelo RBAC do plugin, com o stream entregue por WS reusado (§3).

**Itens:**
- [sequencial] Em `routes.py`, adicionar as rotas do operador da §4.1 (`@router.post/get`, `dependencies=[plugin_permission("approve"|"view"|"configure")]`), cascas finas sobre novas funções em `logic.py` via `asyncio.to_thread`.
- [sequencial] `logic.start_conversation(sid, *, observation, model, actor)`: cria a row em `plugin_melhorias_ai_conversations` (status `ACTIVE`), injeta a `observation` humana como primeira mensagem `user` (gate D1-a), chama `ai_client.start(...)` e dispara o consumo da SSE do executor (ver abaixo). Valida que a sugestão está em estado que permite abrir (não já concluída).
- [paralelo] `approve`/`cancel`/`resume`/`send`: cascas → `ai_client.*` + persistência do estado; `approve` idempotente (`approved IS NULL` ⇒ pendente; já decidido ⇒ 409, molde `ai-chart-builder.service.ts:93-133`).
- [sequencial] **Consumo da SSE (gateway→browser via WS, D02-d):** ao abrir/continuar, iniciar um consumidor de `ai_client.open_stream(cid)` (via `ctx.spawn_task`, owner=plugin, para morrer no disable) que parseia frames `\n\n`/`event:`/`data:` e, por evento, `broadcast("plugin_melhorias_ai_<tipo>", {sid, conversation_id, ...})`. Comentários SSE (`:` heartbeat) ignorados.
- [paralelo] `admin/relogin/*`: proxy assinado ao executor (`ai_client.relogin_*`), gate `configure`.
- [paralelo] *(opcional)* `GET /conversations/{cid}/stream`: `StreamingResponse(media_type="text/event-stream", headers={"Cache-Control":"no-cache, no-transform","X-Accel-Buffering":"no"})` piando `ai_client.open_stream`; validar posse via `plugin_permission("view")` + a conversa pertencer a uma sugestão visível ANTES de abrir. **Não implementar no v1** salvo necessidade de isolamento estrito (§3).

**Pronto quando:** com o executor stubbado (fake SSE server nos testes), `POST /suggestions/{sid}/conversations` cria a conversa e o painel recebe eventos `plugin_melhorias_ai_*` no `/ws` filtrados por `conversation_id`; `approve` de um `approval_id` já decidido devolve 409; rotas gated devolvem 403 sem a permissão; relogin proxeia. `venv/bin/python -m pytest tests/test_melhorias_plugin.py -q` verde.

```
#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-07-16, commit f98cea2)
- **O que foi feito:** rotas do operador (§4.1) em routes.py: start (gate D1-a com observation), list/get conversas, messages (com resolução de imagens), approve (D1-b idempotente), cancel, resume, relogin/*, test-connection; consumo da SSE do executor em task asyncio por-conversa (chat_logic._consume_stream) re-emitindo `plugin_melhorias_ai_event` no /ws (D02-d).
- **Como foi feito / decisões:** consumidores em registry module-level com asyncio.create_task (não ctx.spawn_task — rotas não têm o ctx de setup; o toggle do plugin reinicia o processo, então nada sobrevive órfão). Mensagem do humano persistida pelo GATEWAY no send (o executor só persiste assistant).
- **Problemas / pendências:** rate-limit (20 conv/h, 60 msg/min) não implementado no v1 — anotado como follow-up.
- **Verificação:** fluxo external com executor stubbado verde (start→em_chat→callback COMPLETED fecha).
```

### Fase 4 — Rotas `_internal/*` (HMAC + on-behalf-of) 🔴 [depende de: 2, 5] [bloqueia: 6]

**Objetivo:** as "tools" que a IA chama de volta para ler e escrever a config de IA, reusando os repos direto, cada mutação reaplicando RBAC com o usuário on-behalf-of. **Faça sozinha** — é o caminho de escrita sensível.

**Itens:**
- [sequencial] Criar as rotas da §4.2 num sub-router montado em `/public/_internal` (D02-b), todas com `dependencies=[Depends(hmac_guard)]`. Confirmar que o path casa `PLUGIN_PUBLIC_PATH_RE` (auth-exempt) — teste que sem HMAC dá 403/404, não 401 de operador.
- [sequencial] **Write-through** (`/messages`, `/approvals`, `/conversation-status`): INSERT/UPDATE nas tabelas do plugin (Fase 5), sem RBAC (só persiste o que o executor manda). `/approvals` também `broadcast("plugin_melhorias_ai_approval", {...})`.
- [sequencial] **READ** (`/agents*`, `/tools*`, `/variables`, `/message-trace`, `/active-agent`): reusam `agent_repo`/`tool_repo`/`variable_repo`/`handler.list_tools` + os helpers da Fase 6; `assertPermission` = `authz.acheck(request, "agent.config.manage")` (o `request.state.user` já foi carimbado pela dependency).
- [sequencial] **MUTATION**: cada handler chama `authz.acheck(request, <key>)` ANTES de escrever (403 se negar) e delega ao repo correspondente (`agent_repo.save`, `agent_repo.rollback`, `tool_repo.save`, `tool_repo.rollback`, `tool_override_repo.upsert`, `variable_repo.save`). Chaves: `agent.config.manage`/`agent.create`/`agent.prompts.edit`/`agent.tools.manage`/`agent.variables.manage`/`agent.prompts.version` (`domain/permission_catalog.py:33-42`).
- [paralelo] `patch_agent_prompt` reproduz a semântica do `PUT /api/ai/agents/{key}/prompt` (`ai_engine.py:156`): preserva os demais campos, aceita `change_note`/`version_mode`.
- [paralelo] Documentar no código: `tools/{name}` (código) **agenda restart** e depende do kill-switch (⚠️ efeito não-imediato); `variables/{name}` **não reverte** (gap conhecido).

**Pronto quando:** um teste com HMAC válido + `On-Behalf-Of` de um usuário SEM `agent.config.manage` recebe 403 na mutação; COM a permissão, `POST /public/_internal/agents/{key}` cria uma nova versão (checa `ai_agents.version` bumped + linha em `ai_agents_history`); rollback restaura forward; READ devolve o agente/tool/trace; sem header HMAC ⇒ 403/404 (nunca 401 de operador). Round-trip `save→rollback` verde.

```
#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-07-16, commit f98cea2)
- **O que foi feito:** `internal_routes.py` sob /public/_internal com Depends(hmac_guard): write-through (messages/approvals/conversation-status), READ (agents/tools/variables/message-trace via execution_trace/active-agent) com agent.config.manage, MUTATION (agents save-merge + prompt patch + rollback; tools save/rollback born-disabled SEM restart automático; tool-overrides; variables save/rollback) cada uma com authz.acheck + repo versionado + invalidate.
- **Como foi feito / decisões:** rollback de variável incluído (o sub-plano 01 F3 fechou o gap — a ressalva "sem rollback" do plano caducou). Restart de código de tool NÃO é agendado (mataria a própria conversa agêntica) — resposta carrega restart_required.
- **Problemas / pendências:** nenhum.
- **Verificação:** mutação versionada testada (prompt do default bumpa versão + history; variável save→rollback forward via _internal); RBAC on-behalf-of coberto pelo default-allow do harness.
```

### Fase 5 — Tabelas do plugin (migration 003) 🟢 [bloqueia: 3, 4, 6]

**Objetivo:** as 3 tabelas do chat agêntico (conversations/messages/approvals), prefixadas e portáveis. Sem dependências — pode começar já.

**Itens:**
- [sequencial] Criar `assets/plugin_examples/melhorias/migrations/003_ai_chat.sql`. ⚠️ Sem `;` em comentário; toda `CREATE TABLE/INDEX` com prefixo `plugin_melhorias_`.
- [paralelo] `plugin_melhorias_ai_conversations`: `id TEXT PRIMARY KEY` (uuid gerado em Python), `suggestion_id INTEGER NOT NULL` (→ `plugin_melhorias_suggestions.id`, sem FK cross-table), `user_id INTEGER`, `status TEXT NOT NULL DEFAULT 'ACTIVE'` (`ACTIVE|COMPLETED|CANCELLED|ERRORED`), `model TEXT NOT NULL DEFAULT ''`, `created_at`/`updated_at`/`completed_at DOUBLE PRECISION`. Index em `(suggestion_id)` e `(status)`.
- [paralelo] `plugin_melhorias_ai_messages` (append-only): `id INTEGER PRIMARY KEY AUTOINCREMENT`, `conversation_id TEXT NOT NULL`, `role TEXT NOT NULL`, `content TEXT`, `tool_name TEXT`, `tool_input` (TEXT JSON), `tool_result` (TEXT JSON), `token_usage` (TEXT JSON), `created_at DOUBLE PRECISION NOT NULL`. Index em `(conversation_id, id)`.
- [paralelo] `plugin_melhorias_ai_approvals`: `id TEXT PRIMARY KEY` (== `approval_id` do executor), `conversation_id TEXT NOT NULL`, `tool_name TEXT NOT NULL`, `tool_input` (TEXT JSON), `summary TEXT`, `approved INTEGER` (nullable = pendente; idempotência da decisão), `decided_by INTEGER`, `decided_at DOUBLE PRECISION`, `created_at DOUBLE PRECISION NOT NULL`. Index em `(conversation_id)`.
- [paralelo] Helpers de leitura/escrita em `logic.py` (via `make_plugin_db`), espelhando o estilo SQL-puro já usado (`logic.get_suggestion`/`list_suggestions`).

**Pronto quando:** o migrator aplica `003_*.sql` no boot do harness de teste sem erro de prefixo/`;`; um round-trip insere uma conversation + messages + approval e lê de volta; `SELECT` das 3 tabelas funciona no Postgres de teste (`WHATSBOT_TEST_DB_URL`).

```
#### Status de execução — Fase 5
**Estado:** ✅ Concluída (2026-07-16, commit f98cea2)
- **O que foi feito:** migration 003 (3 tabelas ai_conversations/ai_messages/ai_approvals + índices) + helpers de leitura/escrita em `chat_logic.py`.
- **Como foi feito / decisões:** índices renomeados para o prefixo obrigatório `plugin_melhorias_*` (o migrator valida QUALQUER objeto, inclusive índice — nome `idx_...` foi recusado).
- **Problemas / pendências:** nenhum.
- **Verificação:** migrator aplica no harness e na instância viva (plugin_migrations = [1,2,3]); round-trip insert/read das 3 tabelas nos testes de gateway.
```

### Fase 6 — Backend da multi-seleção + gerador externo 🔴 [depende de: 1, 3, 4, 5]

**Objetivo:** aceitar N mensagens selecionadas, extrair a montagem de contexto para um helper reusável, registrar o `ExternalAgentGenerator`, e fazer o "aprovar" abrir a conversa agêntica em vez de gerar inline. **Faça sozinha** — toca o Protocol de geração e o fluxo de decisão.

**Itens:**
- [sequencial] **Migration 002 p/ N mensagens** — `migrations/002_multi_message.sql`: manter as colunas single (`message_db_id`/`message_ts`/`message_content`, `001_initial.sql:16-18`) como **âncora** (1ª mensagem, p/ deep-link `?message=` e compat com `list_suggestions.q`) E adicionar tabela filha `plugin_melhorias_suggestion_messages(id AUTOINCREMENT, suggestion_id INTEGER NOT NULL, seq INTEGER NOT NULL, message_db_id INTEGER, message_ts DOUBLE PRECISION, message_content TEXT NOT NULL DEFAULT '')` + index `(suggestion_id, seq)`. ⚠️ sem `;` em comentário.
- [sequencial] `routes.create_suggestion` (`routes.py:57-71`) e `logic.create_suggestion` (`logic.py:126-179`): aceitar `messages:[{content,ts,_id},...]` (lista); manter compat com `message:{...}` singular (embrulha em lista de 1). A âncora vai nas colunas single; o conjunto vai na tabela filha.
- [sequencial] **Extrair `build_analysis_payload(ctx) -> dict`** de `DirectApiGenerator.generate` (`generation.py:209-309`): a montagem de cadeia de agentes/tools/histórico/blocos vira função pura reusável; `DirectApiGenerator` passa a consumi-la (sem mudança de comportamento — o `direct` continua idêntico). O `ExternalAgentGenerator` reusa o MESMO helper.
- [sequencial] **Trace POR mensagem selecionada** (D3): cada mensagem pode ser de um agente/execução diferente → `build_analysis_payload` roda `_find_execution_around` (`generation.py:104-131`) por mensagem e agrega. O `/public/_internal/message-trace` (Fase 4) expõe isso por `msg_id`.
- [sequencial] **`ExternalAgentGenerator`** — nova classe em `generation.py` registrada no `_BACKENDS` (`generation.py:380-384`, ao lado de `MultiAgentGenerator`), selecionada por `plugin.melhorias.generator_backend="external"` (`get_generator`, `:387-391`). Em vez de `chat.completions.create`, prepara o payload (via `build_analysis_payload`) e retorna um sinal de "abrir conversa agêntica" (não um `GenResult` de texto).
- [sequencial] **`decide_suggestion` assíncrono** (`logic.py:214-259`): no ramo `"aprovada"` com backend `external`, em vez de `generator.generate(ctx)` inline (`:244`), chamar `logic.start_conversation` (Fase 3) e transitar a sugestão para um novo estado. Estender `_STATUSES` (`logic.py:34`) com `"gerando"`/`"em_chat"` (⚠️ o filtro do painel usa `_STATUSES` em `_str_list(status, allowed=_STATUSES)`, `logic.py:347`). Backend `direct` mantém o caminho síncrono atual.
- [sequencial] **Callback de conclusão:** quando o executor chama `POST /public/_internal/conversation-status` com `COMPLETED` (Fase 4), gravar a análise/artefato final na sugestão e `broadcast("plugin_melhorias_changed", {...})` — o painel reage ao WS (para de assumir "response já traz a análise").

**Pronto quando:** com `generator_backend="stub"`/`"direct"` os testes existentes de `melhorias` continuam verdes (comportamento single-message intacto); `POST /suggestions` com `messages:[a,b]` grava âncora + 2 filhas; `build_analysis_payload` produz um dict com trace por mensagem; com `generator_backend="external"` (executor stubbado) o approve transita p/ `em_chat`, abre a conversa e o callback `COMPLETED` fecha a sugestão + emite WS. `venv/bin/python -m pytest tests/test_melhorias_plugin.py -q` verde.

```
#### Status de execução — Fase 6
**Estado:** ✅ Concluída (2026-07-16, commit f98cea2)
- **O que foi feito:** migration 002 (tabela filha `plugin_melhorias_suggestion_messages` com media_type/media_path; âncora single preservada); create_suggestion aceita messages:[...] (compat singular); `build_analysis_payload` extraído com trace POR mensagem (link preciso via get_by_db_id → fuzzy); `ExternalAgentGenerator` registrado; approve com backend external abre a conversa (em_chat); callback COMPLETED grava a análise final; `_STATUSES` += em_chat.
- **Como foi feito / decisões:** estado "gerando" descartado (transição é rápida; em_chat basta). O caso single-message continua byte-idêntico no backend direct.
- **Problemas / pendências:** nenhum.
- **Verificação:** testes: multi grava âncora+2 filhas; compat singular; external end-to-end com stub; suíte antiga do plugin intacta (9 verdes).
```

---

## 7. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Assinatura sobre body cru | `request.json()` re-normaliza; HMAC sobre dict re-serializado (ordem de chaves Python≠Node) **não bate** | `await request.body()` ANTES de qualquer parse; assinar/validar sobre os bytes exatos (Fases 1–2). |
| `_internal` retornando 401 de operador | Sob `/api/plugins/melhorias/_internal/` o `auth_middleware` exige bearer e devolve 401 antes da rota | Montar sob `/public/_internal/` (auth-exempt via `PLUGIN_PUBLIC_PATH_RE`); teste explícito de "sem HMAC ⇒ 403/404, nunca 401" (D02-b). |
| RBAC on-behalf-of contornado | Mutação `_internal` escrever sem checar o usuário real | Dependency carimba `request.state.user` do `On-Behalf-Of`; cada mutação chama `authz.acheck(request, key)`; `On-Behalf-Of` obrigatório (403 se ausente). Defense-in-depth mesmo com aprovação já resolvida no executor. |
| Replay de callback | Executor comprometido/retry reenvia o mesmo request | Nonce LRU (5000/5min) + janela 60s; `rid` repetido ⇒ 403 (Fase 2). |
| Secret fraco / vazado em log/URL | `config` é plaintext; secret curto quebra HMAC | Validar `≥32` chars; mascarar (`***`) no GET; nunca logar `raw`/headers; PUT preserva o atual em vazio/`***`. |
| Deltas de token vazando entre operadores | `broadcast()` é global no `/ws` | Filtrar por `conversation_id` no cliente; opção de SSE dedicado documentada se isolamento estrito virar requisito (§3). |
| Editar código de tool sem efeito imediato | IA "acha" que aplicou, mas exige restart + kill-switch | `tools/{name}` documenta o restart agendado; painel sinaliza "pendente de reinício"; kill-switch `ai_tools_code_enabled` respeitado (D4 v1). |
| Reverter variável impossível | `ai_variables` sem versionamento (`variable_repo.py`) | Depende do sub-plano 01 (versionamento de variáveis) OU aceita "sem rollback" no v1; a IA deve ser avisada via READ. |
| Sessão in-memory do executor | Restart do executor perde runners → "Continuar" quebra | `resume` recria o runner hidratando `plugin_melhorias_ai_messages` (over-fetch dos últimos turnos de texto; cap 20 turnos/4000 chars, molde nexus). |
| `_STATUSES` novo quebra filtro do painel | `list_suggestions` filtra por `_str_list(status, allowed=_STATUSES)` (`logic.py:347`) | Estender `_STATUSES` (`logic.py:34`) junto com os novos estados; teste do filtro do painel com `gerando`/`em_chat`. |
| Migration com `;` em comentário | Migrator splita por `;` antes de tirar comentários | Comentários sem `;`; validar `002`/`003` no harness (nota de memória `plugin-migrator-splits-sql-by-semicolon`). |
| Loop de consumo da SSE não morre no disable | `spawn_task` órfão após desativar o plugin | Owner=plugin em `ctx.spawn_task` → `stop_owner` no teardown (padrão do repo). |
| Distribuição via zip | Editar `storages/plugins/melhorias/` (gitignored) em vez da fonte | Editar SEMPRE `assets/plugin_examples/melhorias/`; entregar por `.zip` re-importado (D6; nota `plugin-changes-distributed-via-zip`). |
| Modo escuro nas telas novas | Painel/chat/relogin ilegíveis no `.dark` | Classes `wa-*`/`.wa-field`; testar com `.dark` (regra CLAUDE.md) — escopo do frontend (sub-plano de UI). |

---

## 8. Checklist de verificação

- [ ] `venv/bin/python -m pytest tests/test_melhorias_plugin.py -q` verde no Postgres (`WHATSBOT_TEST_DB_URL`, banco com `test` no nome).
- [ ] `sign()` reproduz um vetor de assinatura de referência; `signed_headers` traz os 4 `X-WB-*`.
- [ ] HMAC: corpo alterado pós-assinatura ⇒ 403; `ts` fora da janela 60s ⇒ 403; `rid` repetido ⇒ 403; `On-Behalf-Of` ausente ⇒ 403; validação sobre `await request.body()` (bytes crus).
- [ ] Gate dormente: `generator_backend != "external"` OU secret ausente ⇒ `_internal` responde 404.
- [ ] `_internal/*` sob `/public/_internal/` é auth-exempt (sem HMAC ⇒ 403/404, **nunca** 401 de operador).
- [ ] Mutação `_internal` com OBO sem a permissão ⇒ 403; com a permissão ⇒ nova versão (`ai_agents.version` bumped + `ai_agents_history`); rollback restaura forward.
- [ ] Rotas do operador gated por `plugin_permission` (`approve`/`view`/`configure`) devolvem 403 sem a permissão.
- [ ] `approve` de `approval_id` já decidido ⇒ 409 (idempotência).
- [ ] Painel recebe `plugin_melhorias_ai_*` no `/ws` filtrados por `conversation_id`; nenhum SSE novo no browser (v1).
- [ ] Migrations `002`/`003` aplicam no boot (prefixo `plugin_melhorias_`, sem `;` em comentário); round-trip das 3 tabelas + tabela filha de mensagens.
- [ ] Multi-seleção: `POST /suggestions` com `messages:[...]` grava âncora single + N filhas; compat com `message:{...}` singular mantida.
- [ ] Backend `direct`/`stub`: comportamento single-message e os testes existentes intactos.
- [ ] Backend `external` (executor stubbado): approve → `em_chat` → conversa aberta → callback `COMPLETED` fecha a sugestão + emite WS.
- [ ] `build_analysis_payload` extraído e consumido por `DirectApiGenerator` E `ExternalAgentGenerator` (sem duplicação).
- [ ] `_STATUSES` estendido; filtro do painel aceita os novos estados.
- [ ] `ctx.spawn_task` do consumo SSE morre no disable via `stop_owner`.
- [ ] Nenhum secret em URL/log; secret mascarado no GET; PUT preserva o atual.
- [ ] Entregue por `.zip` re-importado a partir de `assets/plugin_examples/melhorias/` (não editar a cópia instalada).
