# Plano 51 — Melhoria agêntica: chat com IA que configura a própria IA do WhatsBot (mestre)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-16 · **Escopo:** grande (plugin evoluído + executor externo + mudanças no core + frontend)
> **Origem:** pedido do usuário — tornar o plugin `melhorias` **agêntico** (hoje gera uma análise de texto única) e capaz de **criar/editar agentes, tools, prompts e variáveis** do WhatsBot por conversa com uma IA (Claude Code num servidor externo), com **versionamento/revert** e **aprovação humana**; permitir **selecionar várias mensagens** de um atendimento; e **enviar imagens** no chat. Alvo final: pessoas não-técnicas pedirem melhorias sozinhas, sem depender de dev.
> **Método:** leitura do padrão de referência (o `ai-server` do nexus-relatorios — gateway lido em `/opt/nexus/nexus-relatorios`, **executor lido direto no servidor** `203.0.113.10:64777` em `/root/opt/ai-server`) + mapeamento do WhatsBot por 5 sub-agentes paralelos (relatórios com `arquivo:linha` verificado). Todas as afirmações abaixo vêm de código real lido.
> A forma da solução replica o padrão nexus: o **navegador fala só com o WhatsBot (gateway)**; o gateway chama o **executor Claude Code** via **HMAC + SSE**; o executor chama de volta endpoints `_internal/*` do gateway (assinados, `On-Behalf-Of`) para **ler** o rastro/config e **escrever** mudanças — cada mutação **bloqueada por aprovação humana**. Toda escrita passa pelos repos versionados (`agent_repo`/`tool_repo`/`variable_repo`), então **reverter é um clique**.
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro. Este mestre indexa **4 sub-planos**; execute pelas Waves (§7).

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** ✅ | **Entrada intacta + chat no painel.** O operador/admin continua clicando com o botão direito e **selecionando mensagens** (agora **múltiplas**) para mandar ao **painel de aprovação** do plugin `melhorias`. No painel: lista de pendências + **chat interativo** por item. **Dois gates humanos:** (a) aprovar para a IA *começar* a rodar (+ injetar observação extra); (b) **cada mutação** que a IA quer aplicar exige **V/X humano**. A IA **nunca** aplica sozinha. | Sub-planos 02 (fase 6) + 04 (fases 1–4). O fluxo de "suggestion" existente vira o container do chat; a geração deixa de ser inline e passa a ser conversacional. |
| **D2** ✅ | **Executor: mesmo servidor + mesma assinatura, pasta nova por aplicação.** Reusa o `CLAUDE-CODE-AUTOMACOES` (`203.0.113.10:64777`, root) e o mesmo OAuth `~/.claude`. O WhatsBot ganha a **própria pasta de trabalho** (guides/tools/system-prompt/client próprios), **sem quebrar o relatorios** (`:8014`). | Sub-plano 03 (fase 1). Recomendação: multi-app `apps/<id>` ou processo separado em nova porta — decidido em P1. |
| **D3** ✅ | **Vínculo preciso no core — SIM.** Adicionar `messages.execution_id` (+ propagação) e ligar a **captura de contexto exato** (prompt/histórico que a IA realmente viu). | Sub-plano 01 (fases 1–2). Torna a multi-seleção robusta (hoje o match msg→execution é fuzzy por timestamp) e a análise fiel (hoje usa aproximação "viva" que deriva). |
| **D4** ✅ | **Escopo v1 = TUDO, inclusive código de tools.** A IA cria/edita agentes (prompt, descrição, modelo, `tool_names`, roteamento), prompts, descrições de tool, variáveis **e escreve código Python de tools novas** (`ai_tools` code-in-DB). | Sub-planos 02 (fase 4) + 03 (fase 3). Código de tool exige **aprovação + versionamento + subprocesso isolado + restart + kill-switch** (ver Riscos §8). |
| **D5** ✅ | **Contrato gateway↔executor estável** (HMAC + SSE + `_internal` + aprovação + resume + relogin OAuth). O executor pode trocar de Claude Code para outra coisa depois **sem** o WhatsBot mudar. | Barreira de Wave 0 (§7): "congelar contrato" antes de 02 e 03 andarem em paralelo. |
| **D6** ✅ | **A feature vive no plugin `melhorias` EVOLUÍDO**, não no core nem em plugin novo. | Todos os sub-planos. Preserva RBAC/tabelas/entry-seam existentes. Fonte git em `assets/plugin_examples/melhorias/`; mudanças chegam à instância via **`.zip` re-importado** (a cópia em `storages/plugins/melhorias/` é gitignored). |
| **D7** ✅ | **Aprovação humana simples no painel** (V/X), sem granularidade por-campo no MVP. IA propõe → humano aprova cada mutação → aplica. | Sub-planos 03 (fase 3, `waitForApproval`) + 04 (fase 4, card de approval). |
| **D8** ✅ | **Documentação/boas-práticas por aplicação** mora no executor (`guides/*.md`), lida pela IA sob demanda via tool `read_guide` — inclui um **índice de capacidades** (o resumo do que a IA pode implementar). | Sub-plano 03 (fase 2). Espelha o `src/guides/` do relatorios. |

---

## 1. Resumo executivo

O WhatsBot já tem **quase toda a matéria-prima**: (1) a feature de melhoria **já é o plugin `melhorias`** com um **seam pronto** para geração agêntica (`SuggestionGenerator` Protocol + `_BACKENDS`, stub `ExternalAgentGenerator`); (2) a config da IA é **config-in-DB versionada** (`ai_agents`/`ai_tools` com `*_history` + `rollback`, mais a trilha git-like de prompt); (3) o **rastro por mensagem** já reconstrói agente+tools+args+results+roteamento; (4) há um **padrão de referência completo e testado em produção** (o `ai-server` do nexus) que resolve HMAC, SSE, aprovação humana, resume e relogin OAuth sem SSH.

A solução tem **quatro frentes**:

- **Core (sub-plano 01):** 3 mudanças pequenas e cirúrgicas — `messages.execution_id` (link O(1) msg→execution), captura de contexto exato (o que a IA viu), e versionamento de `ai_variables` (hoje o único ponto cego). Habilitam a multi-seleção robusta e a paridade de revert.
- **Gateway (sub-plano 02):** o backend do `melhorias` evoluído — cliente/verificador HMAC, rotas públicas (start/message/stream SSE/approve/resume/relogin), rotas `_internal/*` (as tools que a IA chama de volta, reusando os repos versionados com RBAC `On-Behalf-Of`), as 3 tabelas do chat, a config runtime e o backend da multi-seleção.
- **Executor (sub-plano 03):** a **pasta da aplicação WhatsBot** no servidor — guides próprios, tool-registry (read + mutation com aprovação), system-prompt de escopo, client HMAC, resume e relogin. É o único artefato **fora** do repo do WhatsBot.
- **Frontend (sub-plano 04):** a multi-seleção de mensagens (seam novo no core + dialog no plugin) e o **chat interativo no painel** (deltas em tempo real; cards de mensagem/tool/approval/erro, modal de relogin, imagens, modo escuro). O transporte browser↔gateway está em aberto (P2, §8) — SSE fica sempre no hop executor→gateway.

**Wave 0** congela o contrato e faz os habilitadores do core + a plumbing dos dois lados. **Wave 1** constrói as duas metades em paralelo. **Wave 2** integra ponta-a-ponta e adiciona imagens/relogin.

---

## 2. Como funciona hoje (mapa — verificado)

### 2.1 O padrão de referência (o que vamos replicar)

| Peça | Onde (referência) | Observação |
|------|-------------------|------------|
| Executor Claude Code | `/root/opt/ai-server` (servidor, `:8014`), Fastify + `@anthropic-ai/claude-agent-sdk` | 1 runner in-memory por conversa; `query({prompt: inputStream(), options})`; OAuth `~/.claude/.credentials.json` relido por query. |
| Tools da IA | `src/core/tool-registry.ts` (`createSdkMcpServer`) | READ chamam `client.get` (assinado); MUTATION chamam `waitForApproval` **antes** de executar → só então `client.post`. Built-ins do Claude Code desligados (`tools:[]` + `allowedTools`). |
| Aprovação humana | `src/core/pending-approvals.ts` + SSE `approval_needed` | Cada mutação bloqueia numa Promise com timeout até `POST .../approve`. |
| Guides (doc por-app) | `src/guides/*.md` lidos via tool `read_chart_creation_guide` | É o "documentação/boas práticas por aplicação" (D8). |
| HMAC | `src/utils/hmac.ts` | Payload = `METHOD\npath\nts\nrequestId\nbody`; janela 60s; nonce LRU 5000/5min; header `On-Behalf-Of`. |
| Resume | `conversation-runner.ts` (`pendingHistory`) | Prepende blob de até 20 turnos / 4000 chars na próxima mensagem. |
| Relogin OAuth sem SSH | `src/core/relogin-session.ts` | `spawn npx @anthropic-ai/claude-code auth login --claudeai`, captura a URL, pipe do código. |
| Gateway (lado app) | nexus `server/modules/ai-chart-builder/*` | Browser só fala com o gateway; SSE é **pipe cru** de bytes; `_internal/*` reaplica RBAC. |

### 2.2 O WhatsBot hoje (pontos de mudança)

| Área | Onde | Observação |
|------|------|------------|
| Feature de melhoria | plugin `melhorias` — `assets/plugin_examples/melhorias/{generation.py,logic.py,routes.py,static/{extends.js,panel.js}}` | **Já é plugin** (commit `9c5a59f`; `improvement_service.py` deletado). Fluxo: botão direito → `filter.message.contextMenu.items` (1 message) → `POST /suggestions` (pendente) → `POST /suggestions/{sid}/approve` gera a análise inline. |
| Seam agêntico pronto | `melhorias/generation.py:98-99,380-391` | `SuggestionGenerator` Protocol + `_BACKENDS` + `get_generator()` por `plugin.melhorias.generator_backend`. Stub `ExternalAgentGenerator`/`MultiAgentGenerator` esperando. Reconstrução de contexto em `generation.py:104-175,209-309`. |
| Versionamento agente | `db/repositories/agent_repo.py:134` (`save`→snapshot `ai_agents_history` + trilha `ai_agent_prompt_history`), `:339` (`rollback`) | Endpoints `PUT /api/ai/agents/{key}` + `/prompt` + history/rollback/diff/restore (`server/routes/ai_engine.py`). Dedup no-op. |
| Versionamento tool | `db/repositories/tool_repo.py:61` (`save`→`ai_tools_history`), `:157` (`rollback`) | `ai_tools` code-in-DB roda em subprocesso isolado, gated por `ai_tools_code_enabled` (default OFF, P62), nasce `enabled=False` (P63), `PUT` agenda `schedule_restart`. |
| Ponto cego | `db/repositories/variable_repo.py:45` (`save` upsert puro) | `ai_variables` **sem** version/history/rollback → a IA não consegue reverter variável. |
| Trace | `db/tables.py` (`messages.agent_key`, `executions.routing_steps`, `execution_steps` `tool_executed`/`llm_context`) | `messages` **NÃO** tem `execution_id`; link atual é fuzzy por `phone`+janela `ts` (`melhorias/generation.py:104-131`). `llm_context` (prompt+histórico exato) gated por `execution_capture_context` default OFF (`agno_engine.py:92-98`). |
| Multi-seleção | `web/static/js/components/contacts/ContactDetail.js:261-274`, `hooks/useMessageActions.js:33-54`, `plugins/registry.js:36-41` | Tudo single-message. O seam `filter.message.contextMenu.items` passa **1** `message`. |
| Tools do "Hotspot" | `storages/ai_tools/*.py` (materializadas) + `ai_tools.code` no DB | São `ai_tools` `kind='code'` (`consultar_debitos`, `gerar_boleto`, `pesquisar_ofertas`…). "Hotspot" é vocabulário do vertical do usuário, não um módulo. |

⚠️ **Gotchas que tornam algo obrigatório:**
- **HMAC sobre body cru:** validar/assinar sobre `await request.body()` (bytes), **nunca** re-serializar o dict — Python e o executor produziriam JSON com ordenação diferente e o HMAC não bate.
- **Rede bidirecional:** gateway→executor (mensagens) **e** executor→gateway (`_internal/*`). Reverse-only "funciona" no chat mas **não persiste nem aplica** — falha silenciosa.
- **Código de `ai_tools` exige restart** para instalar (installer só roda no boot) + `ai_tools_code_enabled=ON` + nasce `enabled=False`. A IA escrevendo tool nova ⇒ o painel precisa lidar com "salvo, reiniciando para instalar".
- **`/api/plugins/<id>/…` NÃO é auth-exempt** — as rotas públicas do chat usam o RBAC do WhatsBot; as `_internal/*` usam HMAC (não cookie). Os callbacks assíncronos do executor entram por HMAC, não por sessão.
- **Mudança de plugin chega por `.zip`** re-importado (memória `plugin-changes-distributed-via-zip`); a cópia instalada é gitignored.
- **Comentário de migration não pode ter `;`** — o migrator do plugin splita por `;` antes de tirar comentários (memória `plugin-migrator-splits-sql-by-semicolon`).

---

## 3. Inventário — workstreams

| WS | Sub-plano | Cobre | Toca core? | Risco | Esforço |
|----|-----------|-------|-----------|-------|---------|
| **A** | 01 — Core | `messages.execution_id`; captura de contexto exato; versionamento `ai_variables`; helpers de trace reutilizáveis | **Sim** (migrations + writer + agno_engine) | médio | M |
| **B** | 02 — Gateway | HMAC client/verify; rotas públicas (SSE); rotas `_internal/*` (reusam repos + trace); 3 tabelas do chat; config runtime; backend multi-seleção + gerador externo | Não (plugin) | médio-alto | L |
| **C** | 03 — Executor | Pasta WhatsBot no servidor: guides, tool-registry (read+mutation), system-prompt, client/persistence/SSE/resume/relogin, imagens, deploy | **Fora do repo** (servidor) | médio-alto | L |
| **D** | 04 — Frontend | Multi-seleção (seam core + dialog plugin); chat no painel; SSE consume; cards; modal relogin; imagens; modo escuro | **Parcial** (seam de seleção no core) | médio | L |

### Falsos positivos descartados

| "Parece problema" | Por que NÃO é |
|-------------------|---------------|
| "Precisa reescrever a feature no core" | Não. `melhorias` já é plugin com o seam de gerador trocável (`generation.py:380-391`). Evoluir o plugin basta (D6). |
| "Precisa `usage.execution_id`/`agent_key` para custo por agente" | Não no v1. `executions.total_tokens/total_cost_usd` cobrem o custo do turno; custo por-agente é v2. |
| "A IA precisa de acesso ao banco / SSH para editar config" | Não. Toda escrita passa pelos endpoints `_internal/*` → repos versionados, com RBAC `On-Behalf-Of`. A IA nunca toca o DB direto. |
| "Precisa de um plugin novo para o chat" | Não. Vive no `melhorias` evoluído (D6) — preserva RBAC/tabelas/entry. |
| "Reusar `EventSource` no browser" | Não — `EventSource` não manda header/escopo de auth; usa `fetch`+`ReadableStream` (padrão nexus, sub-plano 04). |
| "O executor precisa ir no repo do WhatsBot" | Não — é serviço separado no servidor (D2); só o **contrato** é compartilhado (D5). |

---

## 4. Habilitadores / arquitetura (Wave 0)

| Habilitador | Onde | Por quê |
|-------------|------|---------|
| **Contrato congelado** (headers HMAC `X-WB-*`, 9 eventos SSE, endpoints públicos + `_internal/*`) | doc curto no sub-plano 02 §0 | Barreira leve: B e C implementam os dois lados **do mesmo contrato** em paralelo (D5). |
| **`messages.execution_id`** + propagação | migration + `messaging_service.py:434-438` → `message_repo.add` | Link O(1) msg→execution (sub-plano 01 F1). |
| **Captura de contexto exato** | `agno_engine.py:92-98,114-141` (`execution_capture_context`) | Fidelidade do que a IA viu (sub-plano 01 F2). |
| **Versionamento `ai_variables`** | `variable_repo.py` + tabela nova + rotas | Paridade de revert (sub-plano 01 F3). |
| **Plumbing HMAC (client + verify + nonce) + config runtime + 3 tabelas** | plugin `melhorias` | Base dos dois lados (sub-plano 02 F1–F2, F5). |
| **Seam de multi-seleção no core** | `registry.js:36-41` + `ContactDetail.js` + `useMessageActions.js` | Nasce o modo de seleção em lote (sub-plano 04 F1). |
| **Pasta/topologia do executor** | `/root/opt/…` no servidor | Scaffold da app WhatsBot sem quebrar relatorios (sub-plano 03 F1). |

---

## 5. Índice dos sub-planos

| Sub-plano | Arquivo | Cobre | Depende de |
|-----------|---------|-------|------------|
| **01 — Core** | `51-plano-melhoria-agentica-01-core.md` | `messages.execution_id`, captura de contexto exato, versionamento `ai_variables`, helpers de trace | — |
| **02 — Gateway** | `51-plano-melhoria-agentica-02-gateway.md` | HMAC client/verify, rotas públicas (SSE) + `_internal/*`, tabelas do chat, config runtime, backend multi-seleção + gerador externo | contrato; 01 (helpers/trace) para robustez |
| **03 — Executor** | `51-plano-melhoria-agentica-03-executor.md` | Pasta WhatsBot no servidor: topologia, guides, tool-registry, system-prompt, client/SSE/resume/relogin, imagens, deploy | contrato |
| **04 — Frontend** | `51-plano-melhoria-agentica-04-frontend.md` | Multi-seleção (core+plugin), chat no painel, SSE consume, cards, relogin modal, imagens, modo escuro | 02 (contrato SSE); 01 (schema N-msgs via 02) |

---

## 6. Diagrama de dependências

```
WAVE 0 (fundação — congela contrato + habilitadores, tudo em paralelo exceto onde 🔴)
  ┌──────────────────────────────────────────────────────────────────────────┐
  │ CONTRATO  Congelar headers HMAC + 9 eventos SSE + endpoints        🔴  [bloqueia integração B↔C] │
  │ A1  messages.execution_id (core, caracterização ANTES)            🟢  [habilita B4 trace] │
  │ A2  captura de contexto exato (core)                             🟢                        │
  │ A3  versionamento ai_variables (core)                            🟢  [habilita set_variable revert] │
  │ B1+B2+B5  HMAC client/verify + config runtime + 3 tabelas        🟢                        │
  │ C1  topologia/pasta do executor no servidor                     🟢                        │
  │ D1  seam de multi-seleção no core (caracterização ANTES)         🔴  [toca core compartilhado; bloqueia D2] │
  └──────────────────────────────────────────────────────────────────────────┘
            │ barreira: CONTRATO congelado
            ▼
WAVE 1 (construção — as duas metades do contrato, em paralelo)
  B3 rotas públicas (SSE pipe) · B4 rotas _internal/* [usa A1/A3 helpers] · B6 backend N-msgs
  C2 guides · C3 tool-registry (read+mutation) · C4 system-prompt · C5 client/SSE/resume/relogin
  D2 dialog multi-seleção [depende de: D1] · D3 painel+chat · D4 SSE consume+cards [depende de: contrato]
            │ barreira: B e C prontos (as duas pontas do contrato)
            ▼
WAVE 2 (integração + fidelidade)
  INT  integração ponta-a-ponta (start→msg→tool→approval→apply→resume→revert)
  C6+D6 imagens · C5+D5 relogin OAuth · hardening de código-de-tool (D4/restart)
```

**Tabela de fases**

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|------|------|-----------|-------|-------|----------------|
| 0 | Contrato | doc 02 §0 | 🔴 | baixo | Headers/eventos/endpoints escritos e aceitos por B e C. **[bloqueia integração]** |
| 0 | A1 | 01 core | 🟢 | médio | `messages.execution_id` populado; `msg_id→execution` O(1); caracterização verde. **[habilita B4]** |
| 0 | A2 | 01 core | 🟢 | médio | `execution_capture_context` grava prompt+histórico exato; tamanho controlado no PG. |
| 0 | A3 | 01 core | 🟢 | baixo | `ai_variables` versionada + rollback + rotas; testes verdes. |
| 0 | B1/B2/B5 | 02 gateway | 🟢 | médio | HMAC round-trip (assina↔valida) + nonce rejeita replay; 3 tabelas migradas; config DB>env>default. |
| 0 | C1 | 03 executor | 🟢 | médio | Pasta WhatsBot sobe (`/health` OK) sem afetar o relatorios (`:8014`). |
| 0 | D1 | 04 frontend | 🔴 | médio | Modo de seleção em lote no core; menu single intacto; caracterização verde. **[bloqueia D2]** |
| 1 | B3/B4/B6 | 02 gateway | 🟢 | alto | SSE faz pipe; `_internal/*` lê trace e escreve via repos com RBAC; N-msgs persistidas. **[depende de: contrato, A1, A3]** |
| 1 | C2–C5 | 03 executor | 🟢 | alto | Runner conversa, lê guides, propõe mutação, bloqueia em aprovação. **[depende de: contrato]** |
| 1 | D2 | 04 frontend | 🟢 | baixo | Dialog envia N mensagens. **[depende de: D1]** |
| 1 | D3/D4 | 04 frontend | 🟢 | médio | Chat renderiza SSE + cards + approval V/X. **[depende de: contrato]** |
| 2 | INT | todos | 🔴 | alto | Fluxo completo: seleciona→painel→aprova-iniciar→chat→IA propõe→V/X→aplica versionado→revert. |
| 2 | C6/D6 | 03/04 | 🟢 | médio | Imagem anexada/selecionada chega multimodal ao Claude e renderiza. |
| 2 | C5/D5 | 03/04 | 🟢 | médio | Relogin OAuth pelo modal, sem SSH. |

**O que pode ser paralelizado:** Wave 0 roda 7 frentes juntas (só CONTRATO e D1 são 🔴 — CONTRATO por ser barreira de integração, D1 por tocar core compartilhado). Wave 1 roda **as duas metades do contrato (B e C) totalmente em paralelo** + a fatia de multi-seleção (D2) + o chat (D3/D4). Só a Wave 2 (integração) precisa das duas pontas prontas.

**Disciplina do repo:** verde a cada fase; **caracterização ANTES** de A1 (fluxo de save de resposta) e de D1 (menu de contexto compartilhado); um refactor por commit; nunca avançar com teste vermelho não-explicado.

---

## 7. Riscos e cuidados (transversais)

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| HMAC sobre body cru | Re-serializar o JSON quebra a assinatura entre Node e Python | Assinar/validar sobre `await request.body()`; `hmac.compare_digest`. |
| Rede bidirecional | Executor→gateway (`_internal`) bloqueado = chat "funciona" mas nada persiste/aplica | Monitorar; `/auth-check` (HMAC) além do `/health`; documentar no deploy (sub-plano 03 F7). |
| **Código de tool gerado por IA** | `ai_tools` code-in-DB roda Python; instalar exige restart + kill-switch | Aprovação humana obrigatória (D7) + subprocesso isolado (RLIMIT/timeout) + `enabled=False` ao nascer + `ai_tools_code_enabled` explícito + versionado (`ai_tools_history`) → revert. Painel avisa "reiniciando para instalar". |
| Segredo compartilhado | `AI_SERVER_SHARED_SECRET` pre-shared nos dois lados; trocar num só = 403 | Gerar `openssl rand -hex 32`, colar idêntico; secret mascarado na UI; nunca em log/URL. |
| Aprovação obrigatória | Uma tool de mutação sem gate = IA aplica sozinha (viola D1/D7) | Padrão do executor: **toda** mutation chama `waitForApproval` antes de `client.post`; revisar o registry para não deixar mutation sem gate. |
| RBAC On-Behalf-Of | A IA agindo "como" um usuário sem permissão poderia escalar | Cada `_internal/*` reaplica `authz.check(user=onBehalfOf, permission)`; default-deny nas mutations. |
| Sessões in-memory no executor | Restart do executor perde runners ativos | "Continuar" (resume) recria do histórico persistido no gateway (sub-plano 02/03). |
| Multi-seleção no core | D1 toca `ContactDetail`/`registry.js` (compartilhado por todos os plugins) | Caracterização ANTES; ampliar o seam sem quebrar o single-message existente. |
| Postgres (único backend) | Migrations core (execution_id, ai_variables_history) e plugin (3 tabelas + N-msgs) | Alembic round-trip; prefixo `plugin_melhorias_` obrigatório; comentário sem `;`. |
| Modo escuro | Telas novas (chat, cards, modal relogin, config do servidor IA) | Classes `wa-*`/`.wa-field`; testar com `.dark` (regra do CLAUDE.md). |
| Restart de plugin | Loops/estado do chat têm que sobreviver ao toggle | Estado do chat mora no executor (in-memory) + gateway (DB); nada em globals do plugin. |
| Tamanho do contexto capturado | `execution_capture_context` ON cresce o DB | Truncagem já existe (2000/msg, 20000 total, scrub base64); avaliar variante "só system prompt" (sub-plano 01 F2). |

---

## 8. Perguntas em aberto

- **P1 — Topologia do executor: multi-app (`apps/<id>`) num processo ou processo separado por porta?** ⏸️ A DECIDIR no sub-plano 03 F1. (a) multi-app 1 processo/1 OAuth — casa com "pasta por app", mas mexe no relatorios vivo; (b) processo separado `whatsbot-ai-server` em nova porta (ex. 8015), mesmo `~/.claude` — zero risco ao relatorios, leve duplicação. **Recomendação: (b) para o MVP** (isola o risco), com refactor para (a) depois se surgir uma 3ª app.
- **P2 — Transporte browser↔gateway: reuso do WebSocket `/ws` ou SSE dedicado?** ⚠️ **Os sub-planos 02 e 04 divergiram na redação e precisam ser unificados por esta decisão** (02 §3 recomenda WS; 04 F4 descreve SSE e lista o WS como falso-positivo). **A SSE fica sempre no hop executor→gateway** (contrato D5) — a decisão é só do último hop até o browser. (a) **Reuso do `/ws`** (RECOMENDADO v1): o gateway consome a SSE do executor server-side e **re-emite** os 9 eventos como `broadcast(...)` no `/ws` que o painel já mantém; o painel filtra por `conversation_id`. Prós: **nenhuma conexão nova**, sem risco de buffering de SSE atrás de Coolify/Traefik, reusa transporte já provado; contras: `/ws` é broadcast a todos os operadores (mitiga filtrando client-side). (b) **SSE dedicado** (`GET .../stream`, `fetch`+`ReadableStream`): isolamento estrito por-conversa/operador (ownership antes de abrir), fiel ao nexus; contras: 2ª conexão long-lived + risco de buffering de proxy + parser reimplementado. **Recomendação: (a) reuso do `/ws` no v1**, com (b) documentado como upgrade se surgir necessidade de isolamento estrito por-operador. **Ação:** ao executar, o sub-plano 04 F4 passa a consumir os eventos re-emitidos no `/ws` (o parser/máquina-de-estados do `chat.js` continua válido, só muda a fonte de bytes); o material de `fetch`+`ReadableStream` vira o caminho (b).
- **P3 — Captura de contexto: ligar `execution_capture_context` por default ou variante leve?** ⏸️ A DECIDIR no sub-plano 01 F2. **Recomendação:** ligar a captura mas avaliar guardar por default só o **system prompt** (o que mais deriva) + histórico sob flag, para conter tamanho.
- **P4 — Gate (a) "aprovar para iniciar": bloqueia o start ou só injeta contexto?** ✅ DECIDIDO (D1): o humano **libera o start** e opcionalmente injeta observação; a IA só roda após esse clique. Gate (b) é por-mutação.
- **P5 — Escopo das tools de mutação no v1: inclui `transfer`/roteamento e `is_router`?** ✅ DECIDIDO (D4): sim — a IA edita `tool_names`, `routing_targets`, `is_router` via `agent_repo.save` (respeitando a semântica radio do roteador único). Mandar mensagem a cliente / mexer em contatos-conversas fica **fora** do v1 (sub-plano 03 F4).
- **P6 — Auth Claude no executor: OAuth ou API key?** ✅ DECIDIDO (D2): mesma assinatura/OAuth do servidor atual; contrato permite trocar por API key depois sem mudar o gateway.

---

## 9. Apêndice — arquivos-chave por camada

**Core (sub-plano 01):**
- `db/tables.py:108-143` (messages), `:533-583` (executions/steps), `:686-693` (ai_variables) — migrations.
- `app/services/messaging_service.py:434-438` — propagar `execution_id` no save da resposta.
- `db/repositories/message_repo.py:15-47` — `add(...)` aceita `execution_id`.
- `agent/agno_engine.py:92-98,114-141` — `execution_capture_context`.
- `db/repositories/variable_repo.py:45-59` + `server/routes/ai_engine.py:353-381` — versionamento de variável.

**Gateway (sub-plano 02) — plugin `assets/plugin_examples/melhorias/`:**
- `generation.py:98-99,209-309,380-391` (gerador externo + `build_analysis_payload`), `logic.py:126-179,214-259` (N-msgs + abrir chat), `routes.py:57-138` (rotas), `migrations/002_*.sql` (N-msgs + 3 tabelas do chat), novos `ai_client.py`/`hmac.py`/`internal_routes.py`/`chat_routes.py`.

**Executor (sub-plano 03) — no servidor `/root/opt/…`:**
- referência lida em `scratchpad/aiserver-ref/ai-server/src/` (conversation-runner, tool-registry, system-prompt, client, persistence, auth.plugin, routes, hmac, relogin-session, session-bus, env, main, guides/).

**Frontend (sub-plano 04):**
- `web/static/js/components/contacts/ContactDetail.js:261-274`, `hooks/useMessageActions.js:33-54`, `MessageContextMenu.js`, `plugins/registry.js:36-41` (seam multi-seleção, core).
- `assets/plugin_examples/melhorias/static/{extends.js:40-112,panel.js}` (dialog + chat).

**Referência (ler antes de implementar):**
- `docs-planos/46-plano-canais-meta-email-widget-00-mestre.md` (padrão de plano).
- `scratchpad/reports/{melhoria-atual,versionamento,execucao-trace,tools-agentes,nexus-protocolo}.md` (investigação com `arquivo:linha`).
- `scratchpad/aiserver-ref/` (código do executor de referência).

---

## 10. Checklist de verificação (todo o esforço)

- [ ] `venv/bin/python -m pytest tests/ -q` verde no **Postgres** (`WHATSBOT_TEST_DB_URL`, banco com `test` no nome).
- [ ] Caracterização ANTES/DEPOIS de A1 (save de resposta) e de D1 (menu de contexto) verde.
- [ ] HMAC: assina↔valida bate; requestId reusado → 403; `On-Behalf-Of` ausente → 403.
- [ ] Cada `_internal/*` de mutação reaplica RBAC (usuário sem permissão → negado) e escreve **via repo versionado** (aparece no history + revert funciona).
- [ ] `messages.execution_id` preenchido no caminho real; `msg_id→execution` O(1); multi-seleção reconstrói o trace certo por mensagem.
- [ ] `ai_variables`: editar → history; `rollback` restaura.
- [ ] Código de tool: aprovação obrigatória; nasce `enabled=False`; instala só com kill-switch ON + restart; revert via `ai_tools_history`.
- [ ] SSE: chat renderiza `message_chunk`/`tool_call_*`/`approval_needed`/`done`/`error`; V/X aplica/recusa a mutação.
- [ ] Relogin OAuth pelo modal (sem SSH); `isAuthError` detectado no evento **e** no texto.
- [ ] Imagem anexada/selecionada chega multimodal ao Claude e renderiza na bolha.
- [ ] Modo escuro legível (chat, cards, modal, config do servidor IA); `node --test` nos módulos JS puros tocados.
- [ ] Migration round-trip (core + plugin `plugin_melhorias_*`); comentário de migration sem `;`.
- [ ] Nenhum segredo em URL/log; `AI_SERVER_SHARED_SECRET` idêntico nos dois lados.
- [ ] Rede bidirecional confirmada (executor alcança o gateway em `_internal/*`).

---

## 11. Status de execução — Mestre

**Estado:** ✅ Concluído (2026-07-16) — v1 implementado ponta-a-ponta; feature DORMENTE até o operador ligar
- **O que foi feito:** os 4 sub-planos executados nas 3 waves. Core (01): execution_id + captura exata ON + versionamento de variáveis + módulo execution_trace (commits 2853ee2, 04af9e9, a8f4eae, 760f336). Frontend core (04 F1): seam `filter.selection.batchActions` (f8eff2b). Gateway (02): plugin `melhorias` evoluído — HMAC, chat, `_internal`, multi-seleção, ExternalAgentGenerator (f98cea2). Frontend do plugin (04 F2–F7): dialog multi, painel-chat, cards, relogin, imagens (0a1fdab). Executor (03): `/root/opt/whatsbot-ai-server` (:8015) buildado, systemd, secret gerado e colado nos dois lados, rede bidirecional + HMAC Python↔Node validados; `:8014` intacto. Plugin instalado sincronizado na instância dev (migrations 1–3 aplicadas) + zip de distribuição gerado (scratchpad).
- **Como foi feito / decisões:** P1 = (b) processo separado :8015 ✅; **P2 = (a) reuso do `/ws`** ✅ (gateway consome a SSE do executor e re-emite `plugin_melhorias_ai_event`; SSE dedicado documentado como upgrade); P3 = (a) default-ON completo ✅. Extras decididos na execução: código de tool via `_internal` NÃO agenda restart (mataria a conversa — responde `restart_required`); mensagem do humano persistida pelo gateway (executor persiste só assistant); rollback de variável entrou no v1 (01 F3 fechou o gap).
- **Problemas / pendências:** (1) conversa end-to-end com o Claude REAL ainda não exercitada — ativar trocando `generator_backend` para "external" na seção do plugin em Configurações de IA (URL+secret já configurados); (2) rate-limit do gateway (20 conv/h, 60 msg/min) fica para v2; (3) validação visual `.dark` das telas novas no primeiro uso; (4) FAILs pré-existentes de busca acento/case-insensível no test_endpoints (ambiente do DB de teste — confirmado no baseline 8ff5989, não é regressão do plano).
- **Verificação:** 31 testes pytest novos/afetados verdes (p51 execution_link/trace/variables/gateway + melhorias) + suíte de endpoints com apenas os FAILs pré-existentes; `node --test` chat_core 7/7; migrations 0053/0054 round-trip; migrator do plugin aplica 002/003 (harness + instância viva); `/health`+`/auth-check` assinado 200 no :8015; `:8014` respondendo.
