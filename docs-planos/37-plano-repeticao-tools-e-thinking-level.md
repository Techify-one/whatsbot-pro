# Plano 37 — Parar repetição de tools entre turnos (memória de tool + oferta em foco) e campo `thinking_level` no formulário de agente

> **Status:** PLANEJAMENTO · **Data:** 2026-07-07 · **Escopo:** médio (2 frentes independentes: A=backend/motor + plugin `vendas_ia`; B=frontend do formulário de agente). Sem migration. Postgres-only.
> **Origem:** investigação da conversa 39 (`whatsbot-thiago.teste.techify.run/conversations/39`) — a IA repete `pesquisar_ofertas` e `set_custom_attribute` a cada turno (lentidão/custo), e o usuário quer um seletor de nível de pensamento por agente igual ao Nexus. **Método:** leitura do código real + inspeção do banco vivo (execs 129–132) nesta sessão; todo `arquivo:linha` abaixo foi conferido.
> **O quê/por quê:** (A) o modelo **não enxerga** as tool calls/resultados dos turnos anteriores (o role `tool_call` é excluído do contexto do LLM), então re-executa tudo; e a oferta **nunca fica "em foco"** no plugin, então o fragmento que diz "não pesquise de novo" fica mudo. (B) o backend já suporta `thinking_level`→`reasoning_effort`, mas o formulário de agente não expõe o campo.
>
> **Como usar este plano:** ao executar cada fase, preencha o bloco **"Status de execução"** dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro. **Verde a cada fase. Um refactor por commit.** As frentes A e B são 100% independentes (podem ser feitas por pessoas/sessões diferentes, em paralelo).

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** ✅ (2026-07-07) | A lentidão/repetição **NÃO** será resolvida com instrução de prompt ("chame a tool uma vez só"). O Nexus não depende disso e funciona. | Frente A ataca as **causas estruturais** (contexto de tool invisível + oferta não fixada), não o texto do prompt. Proibido "resolver" adicionando frase no prompt do agente. |
| **D2** ✅ (2026-07-07) | O usuário **concorda com as duas causas-raiz** (itens 1 e 2 da investigação): (1) tool calls/resultados fora do contexto do modelo; (2) oferta nunca fixada → `{oferta_em_foco}` mudo. | Frente A = Fase A1 (memória de tool no contexto) + Fase A2 (fixar oferta no plugin). |
| **D3** ✅ (2026-07-07) | Nível de pensamento: por enquanto **só um campo de texto livre** no formulário do agente (igual ao Nexus), onde o usuário digita o valor conforme o modelo (`low`/`medium`/`high`/`minimal`/…). **SEM** auto-mapeamento por modelo. | Frente B é puramente **frontend** (o backend já traduz `thinking_level`→`reasoning_effort`). Nada de tabela de tradução por família de modelo neste plano. |
| **Princípio fixo** | Mudança **aditiva e best-effort**: nenhuma captura/injeção nova pode derromper o turno. `step_type`/`role`/nomes de tool são identidade — não renomear. Design "WhatsBot é dono do contexto" (CLAUDE.md) **preservado** — não ligar `add_history_to_context` do AGNO. Design de canais/plugins plugável preservado (sem `if provider ==`, plugin dono do seu estado). Modo escuro obrigatório em campo novo. | Toda escrita/serialização nova em `try/except`; truncamento obrigatório; base64 nunca entra no contexto. |

---

## 1. Resumo executivo

Cada turno hoje re-executa `pesquisar_ofertas` (roundtrip de embedding no OpenRouter + SQL vetorial no Nexus remoto) e regrava os mesmos `set_custom_attribute`, porque o histórico que o WhatsBot monta e passa ao AGNO **exclui** as mensagens `tool_call` ([message_repo.py:92,119](../db/repositories/message_repo.py#L92)) e o motor roda com `add_history_to_context=False` (o AGNO não vê runs anteriores). O Nexus não repete porque usa `add_history_to_context=True`+`num_history_runs=20` ([comercial.py:123-125](file:///opt/nexus/gerenciamento-ia/ai/src/agents/comercial.py)) — o modelo enxerga as tools que já rodaram. **Frente A** replica esse efeito **dentro do design do WhatsBot**: (A1) injeta uma **memória compacta de tool** no contexto (ofertas já pesquisadas / atributos já definidos nesta conversa) e (A2) faz a oferta ficar **realmente em foco** no plugin `vendas_ia`, ligando o fragmento "OFERTA EM FOCO" que já manda não repesquisar. **Frente B** adiciona o input `thinking_level` no formulário de agente — o backend já resolve `thinking_level`→`reasoning_effort` ([model_factory.py:22-28,90-91](../ai_engine/model_factory.py#L22)); falta só a UI ([AgentsManager.js:211-222](../web/static/js/components/ai/AgentsManager.js#L211)).

---

## 2. Como funciona hoje (mapa) — `arquivo:linha` verificado

### 2.1 Montagem do contexto e chamada ao motor (Frente A)
- **História puxada do DB, SEM tools:** [agent_run_service.py:280](../app/services/agent_run_service.py#L280) `context_messages = contact.get_context_messages(eff_max_context)` → `ContactMemory.get_context_messages` → `message_repo.get_context` ([message_repo.py:87-106](../db/repositories/message_repo.py#L87)), que filtra `~role.in_(excluded)` com `excluded = ("transcription","tool_call","system_notice","conversation_event","system","error")` ([message_repo.py:92-93](../db/repositories/message_repo.py#L92)). Mesmo filtro em `get_context_by_conversation` ([message_repo.py:119-120](../db/repositories/message_repo.py#L119)). ⇒ **as mensagens `tool_call` (que carregam tool + args + resultado no card) nunca entram no contexto do modelo.**
- **Montagem final das messages do hop:** [agent_run_service.py:134-142](../app/services/agent_run_service.py#L134) `messages = [{"role":"system",...}, *context_messages]` (+ nota de handoff opcional), depois `filter.llm.messages`.
- **Chamada ao motor:** [agent_run_service.py:149-150](../app/services/agent_run_service.py#L149) `agno_engine.run_async(handler, contact, sender, messages, active_tools, model_config=spec.model_config)`.
- **Conversão para agno Message:** [agno_engine.py:105-128](../agent/agno_engine.py#L105) `split_messages()` — concatena `system` no prompt e converte o resto em `Message(role, content)`. ⚠️ **Não modela `tool_calls`/role `tool`** — só `role`+`content` texto. (Consequência de projeto para P1.)
- **AGNO stateless por request, sem history próprio:** CLAUDE.md ("o engine NÃO recebe `db`… `add_history_to_context=False`"); confirmado por `runner.arun(input=convo)` ([agno_engine.py:481](../agent/agno_engine.py#L481)) sem sessão/DB.
- **Onde os `tool_call` são gravados (fonte da memória):** o card é persistido como `role="tool_call"` no fluxo do handler/motor (mesma trilha do `track_step("tool_executed")` [agno_engine.py:178,224](../agent/agno_engine.py#L178)); o texto do card já traz `tool`, `args` e `→ resultado`. Também existe `executed_tools` em memória no retorno do motor ([agno_engine.py:67](../agent/agno_engine.py#L67) `EngineResult.executed_tools`).

### 2.2 Plugin `vendas_ia` — oferta em foco (Frente A2)
- **Fragmento "OFERTA EM FOCO":** [prompts.py:39-60](../storages/plugins/vendas_ia/prompts.py#L39) `oferta_em_foco_fragment`; texto em [prompts.py:63-68](../storages/plugins/vendas_ia/prompts.py#L63) inclui *"NÃO precisa chamar `pesquisar_ofertas` de novo para esta oferta"*.
- **Resolução do offercode em foco:** [prompts.py:24-36](../storages/plugins/vendas_ia/prompts.py#L24) `_resolve_offercode` = `state.get_state` (tabela `plugin_vendas_ia_conversa`) **OU** `custom_attributes['oferta_atual']` (`OFFER_ATTR_KEY` = `"oferta_atual"`, [state.py:25](../storages/plugins/vendas_ia/state.py#L25)).
- **Espelho `oferta_atual`:** [state.py:168-176](../storages/plugins/vendas_ia/state.py#L168) `_mirror` grava `oferta_atual` em `custom_attributes` (respeita `mirror_offer_attribute`).
- **Quem fixa a oferta hoje:** só a triagem por keyword — [events.py:54-108](../storages/plugins/vendas_ia/events.py#L54) `on_message_saved` (registrado em `EVENT_HANDLERS`, [events.py:117](../storages/plugins/vendas_ia/events.py#L117)) casa palavra-chave → `set_state` + força agente comercial. **Se a keyword não casar (ou o admin/o próprio modelo trocar de agente), a oferta NÃO é fixada.**
- **Bug observado na conv 39:** `custom_attributes = {codigo_oferta: O06C57F42, curso_de_interesse: Failover, …}` — o modelo gravou **`codigo_oferta`** (atributo livre), **não** `oferta_atual`; `plugin_vendas_ia_conversa` vazia. ⇒ `_resolve_offercode` devolve `""` ⇒ fragmento mudo ⇒ repesquisa.
- **Tools do plugin rodam em subprocesso** (ai_tools code, `ai_tools_code_enabled=true`): editar `.py` do plugin vale no próximo call, sem restart. Mudanças de plugin vivem só em `storages/` (gitignored) → precisam ser reexportadas para o `.zip` canônico (ver Riscos).

### 2.3 Nível de pensamento (Frente B)
- **Backend já suporta:** [model_factory.py:22-28](../ai_engine/model_factory.py#L22) `_TUNING_KEYS` inclui `reasoning_effort`; `_ALIASES` mapeia `"thinking_level" → "reasoning_effort"`. `build_kwargs` resolve pela cascata `model_config[...]` > `variables["{param}_{agent}"]` > `variables[param]` ([model_factory.py:48-92](../ai_engine/model_factory.py#L48)); passa `reasoning_effort=str(...)` ao `OpenAILike` ([:90-91](../ai_engine/model_factory.py#L90)); piso de `max_tokens` sobe para 1024 quando há `reasoning_effort` (`MIN_MAX_TOKENS_REASONING`, [:37,87-89](../ai_engine/model_factory.py#L37)).
- **Motor repassa `model_config`:** [agno_engine.py:74-102](../agent/agno_engine.py#L74) `build_model` → `model_factory.build_kwargs(...)` → `OpenAILike(**kwargs)`.
- **Formulário do agente (o gap):** [AgentsManager.js:211-222](../web/static/js/components/ai/AgentsManager.js#L211) `buildModelConfig()` só emite `model`/`temperature`/`top_p`/`max_tokens` e **preserva** chaves extras (loop [:218-219](../web/static/js/components/ai/AgentsManager.js#L218) exclui essas 4 do preserve). Inputs "Avançado" (Temperature/top_p) por volta de [AgentsManager.js:347-353](../web/static/js/components/ai/AgentsManager.js#L347). **Não há input para `thinking_level`.** Estados lidos de `mc` em [:138,146-147,174-179](../web/static/js/components/ai/AgentsManager.js#L138).
- **`ai_variables` hoje:** só `nome_empresa` (nenhum `thinking_level` global). Agentes `comercial/roteador/suporte/fechamento` = `openai/gpt-5.2`; `model_config` deles = `{"model":"openai/gpt-5.2"}` (sem tuning). Prompt do comercial = 28.912 chars.

### 2.4 Falsos positivos descartados
| Hipótese | Veredito | Razão (verificada) |
|---|---|---|
| "Basta instruir no prompt a chamar a tool uma vez" | ❌ | D1: rejeitado. A `tool_code/pesquisar_ofertas.py` **já** diz "NÃO chame se as ofertas já foram retornadas nesta conversa" — inócuo porque o modelo não vê o histórico de tool. |
| "Ligar `add_history_to_context`/dar `db` ao AGNO (igual Nexus)" | ❌ (fora de escopo) | Quebra o design "WhatsBot é dono do contexto" (CLAUDE.md) e os hooks de plugin (filters de messages). Replicamos o **efeito** via injeção controlada, sem ceder o contexto ao AGNO. |
| "Reconstruir `assistant.tool_calls` + role `tool` reais no input" | ⚠️ possível, mas custoso | `split_messages` ([agno_engine.py:105-128](../agent/agno_engine.py#L105)) não modela `tool_calls`/role `tool`; exigiria estender a conversão + arriscar validação do AGNO. Preferir memória compacta (P1). |
| "Backend não sabe `reasoning_effort` — precisa mudar o motor" | ❌ | Já sabe ([model_factory.py:27,90](../ai_engine/model_factory.py#L27)). Frente B é só UI. |
| "Precisa de migration" | ❌ | `model_config` é JSON existente; memória de tool é derivada de dados já persistidos. Zero DDL. |
| "Remover `codigo_oferta`" | ❌ | É atributo útil no painel; A2 só o **liga** ao foco, não o remove. |

---

## 3. Fases / Roadmap

### Diagrama de dependências (waves)

```
WAVE 0   A1(memória de tool no contexto)   ·   A2(fixar oferta no plugin)   ·   B1(campo thinking_level UI)
            🟢 core/motor                       🟢 plugin vendas_ia              🟢 frontend
            └── independentes entre si; podem ser despachadas juntas ──────────────────────┘
                         │                              │                          │
WAVE 1   A3(verificação integrada A1+A2 na conversa real)   🔴          B2(verificação round-trip thinking_level)  🔴
```

### Tabela de fases

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando (resumo) |
|---|---|---|---|---|---|
| 0 | **A1** Memória compacta de tool no contexto | `agent_run_service.py` (+ helper novo), `agent/memory.py`/`message_repo.py` (leitura), `config/settings.py` (kill-switch) | 🟢 | médio | contexto do turno ganha um bloco "já pesquisado/atributos já definidos"; sem base64; truncado; desligável |
| 0 | **A2** Fixar oferta em foco no `vendas_ia` | `storages/plugins/vendas_ia/prompts.py` e/ou `events.py`/`filters.py`, `state.py` | 🟢 | baixo | gravar `codigo_oferta` (ou keyword) passa a ligar `{oferta_em_foco}` → fragmento anti-repesquisa aparece |
| 0 | **B1** Input `thinking_level` no formulário | `web/static/js/components/ai/AgentsManager.js` | 🟢 | baixo | campo de texto salva em `model_config.thinking_level`; carrega valor existente; dark mode |
| 1 | **A3** Verificação integrada (repetição) | conversa real / sandbox | 🔴 `[depende de: A1, A2]` | baixo | numa conversa multi-turno, `pesquisar_ofertas` não repete após o 1º turno; atributos não regravam |
| 1 | **B2** Verificação round-trip do nível | manual + (opcional) unit `node --test` | 🔴 `[depende de: B1]` | baixo | `thinking_level=low` no form chega como `reasoning_effort=low` ao `OpenAILike` |

> **Paralelização:** as 3 fases da Wave 0 (**A1, A2, B1**) são independentes — despache juntas. A3 espera A1+A2; B2 espera B1.

---

### Fase A1 — Memória compacta de tool no contexto (item 1)
**Objetivo:** o modelo passar a "lembrar", dentro do MESMO design (WhatsBot dono do contexto), o que já foi pesquisado/definido nesta conversa — sem reintroduzir as mensagens `tool_call` cruas nem ceder o contexto ao AGNO.

**Itens:**
- `[sequencial]` **Fonte dos dados** — ler as interações de tool da conversa atual a partir das mensagens `role="tool_call"` já persistidas (via um novo `message_repo`/`ContactMemory` que NÃO reusa o filtro de `get_context`), OU do `executed_tools` do turno + histórico. Preferir `tool_call` do DB (sobrevive entre turnos). Cada card já traz `tool`, `args` e o `→ resultado`.
- `[sequencial]` **Sumarização compacta** (P1 = memória compacta, recomendado) — montar um bloco curto, ex.:
  `Contexto desta conversa (não repita ações já feitas): ofertas já pesquisadas: [SCRIPTS DE FAILOVER E LOADBALANCE (O06C57F42)]; atributos já definidos: {codigo_oferta=O06C57F42, curso_de_interesse=Failover, …}.`
  Derivar "ofertas já pesquisadas" dos resultados de `pesquisar_ofertas` (só nomes+offercode, **nunca** o JSON inteiro) e "atributos já definidos" de `custom_attributes` da conversa. **Truncar** por item e por total (constante nova, ex. `TOOL_MEMORY_MAX_CHARS`), remover qualquer base64/campo grande.
- `[sequencial]` **Ponto de injeção** — anexar o bloco ao contexto em [agent_run_service.py:280](../app/services/agent_run_service.py#L280) (logo após montar `context_messages`) OU como mensagem `system` adicional na montagem [agent_run_service.py:134](../app/services/agent_run_service.py#L134). Injetar como `system` (não como `tool_call`) para atravessar `split_messages` sem exigir modelagem de `tool_calls`.
- `[sequencial]` **Kill-switch** — nova chave em [config/settings.py](../config/settings.py) `DEFAULT_CONFIG` (ex. `ai_tool_memory_enabled`, default **ON** — é o fix pedido; decidir em P3) + allow-list se houver validação de chaves. OFF ⇒ comportamento atual.
- `[paralelo]` **Best-effort** — toda a leitura/serialização em `try/except`; falha ⇒ segue sem o bloco (nunca derruba o turno).
- `[paralelo]` **Escopo por conversa** — usar `conversation_id` quando disponível (a conversa é materializada no ingest) para não misturar ofertas de conversas diferentes do mesmo contato.

**Pronto quando:** num turno subsequente, o array de messages enviado ao motor contém o bloco de memória (verificável via log/`llm_context` se o plano 36 F3 estiver ligado, ou via um teste que inspeciona o retorno de `_run_routing_hop`); o bloco não contém JSON gigante nem base64; com o kill-switch OFF o bloco some.

#### Status de execução — Fase A1
**Estado:** ✅ Concluída (commit desta fase)
- **O que foi feito:**
  - `config/settings.py`: novo `ConfigKey("ai_tool_memory_enabled", default=True, exposed=True, writable=True)` (kill-switch, **ON** por decisão do usuário — P3).
  - `db/repositories/message_repo.py`: novo leitor `get_tool_calls_by_conversation(conversation_id, limit=50)` — lê os cards `role="tool_call"` de UMA conversa (NÃO reusa o filtro de `get_context`, que os exclui), oldest→newest, escopado por conversa.
  - `agent/tool_memory.py` (**novo**): `build_block(contact) -> str | None` + `_tool_signature`/`_scrub` puros. Deriva (1) "ferramentas já executadas" dos cards tool_call (nome + args, **descartando a linha `→ resultado`**, deduped, cap `MAX_TOOL_LINES=12`) e (2) "atributos já definidos" do merge `custom_attributes` contato+conversa. Base64 scrubbado (`_BASE64_RE`), truncado por item (`ITEM_MAX_CHARS=160`) e total (`TOOL_MEMORY_MAX_CHARS=1200`). Tudo em `try/except` (best-effort). Escopo por conversa via `conversation_repo.get_open_for_contact_scoped(contact)` (herda o fix multicanal do plano 37a).
  - `app/services/agent_run_service.py`: injeta o bloco como `{"role":"system", ...}` no fim de `context_messages` (logo após o `_encode_history_for_split`), então atravessa `split_messages` (concatena no system prompt) e é herdado por TODOS os hops de routing (`_run_routing_hop` faz `[system, *context_messages]`). Best-effort + kill-switch.
- **Como foi feito / decisões:**
  - **Memória compacta (P1), não reconstrução de `tool_calls`** — bloco `system` que atravessa `split_messages` sem estender a conversão nem ceder contexto ao AGNO (`add_history_to_context` continua OFF; design "WhatsBot é dono do contexto" preservado).
  - **Fonte = cards `tool_call` do DB** (sobrevive entre turnos), não `executed_tools`/`execution_steps` — e sem tocar em `execution_steps` (escopo da outra IA).
  - **Geral por design**: NÃO parseia o JSON de resultado de nenhuma tool de plugin (a linha `→ resultado` é sempre descartada); lista só nome+args e os atributos já persistidos → zero acoplamento a `pesquisar_ofertas`. A oferta com nome/código vem via A2 (fragmento OFERTA EM FOCO).
  - **Kill-switch default ON** (P3, decisão do usuário) — é exatamente o fix pedido.
- **Problemas / pendências:** nenhuma. (A verificação integrada multi-turno é a Fase A3.)
- **Verificação:** `pytest tests/test_tool_memory.py` → 5 passed (lista tools+atributos, kill-switch OFF remove o bloco, sem conversa→None, base64 removido, `_tool_signature` ignora resultado gigante). Sem regressão: `tests/test_agent_routing.py` 29 passed · `test_routing_motivo.py`+`test_human_gate.py` verdes · `tests/test_endpoints.py` 1086 passed.

---

### Fase A2 — Fixar a oferta em foco no `vendas_ia` (item 2)
**Objetivo:** quando a oferta é identificada (por keyword OU porque o modelo gravou `codigo_oferta`), ela fica realmente "em foco" e o fragmento [prompts.py:63-68](../storages/plugins/vendas_ia/prompts.py#L63) passa a ser injetado — matando a repesquisa por outra via, independente do item 1.

**Itens (escolher a abordagem em P2):**
- `[sequencial]` **Opção (b) — recomendada (menor):** em `_resolve_offercode` ([prompts.py:24-36](../storages/plugins/vendas_ia/prompts.py#L24)) aceitar também o atributo **`codigo_oferta`** como fonte de fallback (além de `state.get_state` e `oferta_atual`). Assim, o `codigo_oferta` que o modelo já grava passa a ligar o foco imediatamente. Validar o offercode contra o Nexus (`fetch_oferta_by_offercode`) antes de considerar em foco.
- `[sequencial]` **Opção (a) — complementar (mais robusta):** um handler/filter no plugin que, ao ver `set_custom_attribute(key="codigo_oferta")` (via `EVENT_HANDLERS["tool.after"]` ou `FILTERS["filter.tool.args"]`), **espelhe** para `oferta_atual`/`state.set_state` ([state.py:168](../storages/plugins/vendas_ia/state.py#L168)) — fixando de verdade (sobrevive a `mirror_offer_attribute`, aparece no painel). Filtrar por `tool_name` no início do handler; best-effort.
- `[paralelo]` **Não** duplicar a lógica de keyword ([events.py:54](../storages/plugins/vendas_ia/events.py#L54)); reusar `state.set_state`/`_mirror`.
- `[paralelo]` **Sem `if provider ==` / sem tocar no core** — tudo dentro de `storages/plugins/vendas_ia/`.

**Pronto quando:** numa conversa onde o modelo gravou `codigo_oferta` (sem keyword), o fragmento "OFERTA EM FOCO" passa a ser injetado no system prompt do próximo turno (verificável via `oferta_em_foco_fragment` retornando não-vazio) e `pesquisar_ofertas` não é mais chamado para a mesma oferta.

#### Status de execução — Fase A2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(opção a/b/ambas)_
- **Problemas / pendências:** _(reexportar o .zip canônico do plugin — ver Riscos)_
- **Verificação:** _(preencher)_

---

### Fase B1 — Campo `thinking_level` no formulário de agente (Frente B)
**Objetivo:** expor um input de texto livre `thinking_level` na seção "Avançado" do formulário do agente, persistido em `model_config.thinking_level`.

**Itens:**
- `[sequencial]` **Estado** — adicionar `thinkingLevel`/`setThinkingLevel` inicializado de `mc.thinking_level` (padrão `''`), espelhando o padrão de `temperature`/`topP` ([AgentsManager.js:146-147,174-179](../web/static/js/components/ai/AgentsManager.js#L146)).
- `[sequencial]` **Input** — na área "Avançado" ([~AgentsManager.js:347-353](../web/static/js/components/ai/AgentsManager.js#L347)) adicionar um `<input type="text" class="wa-field" .../>` rotulado `thinking_level`, com dica curta (ex.: "conforme o modelo: openai `minimal/low/medium/high`; deixe vazio p/ padrão"). **Classe `.wa-field`** (modo escuro).
- `[sequencial]` **Persistência** — em `buildModelConfig()` ([:211-222](../web/static/js/components/ai/AgentsManager.js#L211)): `if (thinkingLevel.trim()) out.thinking_level = thinkingLevel.trim();` e **incluir `'thinking_level'`** na lista de exclusão do loop de preserve ([:218-219](../web/static/js/components/ai/AgentsManager.js#L218)) — assim o campo passa a ser a fonte única (evita valor fantasma preservado quando o usuário limpa).
- `[paralelo]` **Sem backend** — não mexer em rota/repo; `model_config` já trafega inteiro (`agent_repo.save` persiste JSON, [agent_repo.py:110,172](../db/repositories/agent_repo.py#L110)).

**Pronto quando:** abrir um agente → seção Avançado mostra `thinking_level` com o valor atual; salvar com `low` grava `model_config.thinking_level="low"`; limpar o campo remove a chave; legível no modo escuro.

#### Status de execução — Fase B1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase A3 — Verificação integrada da repetição (barreira)
**Objetivo:** provar, numa conversa real/sandbox multi-turno, que a repetição parou.

**Itens:**
- `[sequencial]` Rodar 3–4 turnos (ex.: "quero curso de failover" → "começando agora" → "provedor"). Conferir nos `execution_steps`/mensagens que `pesquisar_ofertas` roda **1×** (no 1º turno) e não se repete; `set_custom_attribute` não regrava valores idênticos.
- `[paralelo]` Medir a queda de `total_tokens`/duração por turno vs baseline (execs 129–132: 40k/54k/64k tokens, 25–41s).

**Pronto quando:** `pesquisar_ofertas` não reaparece nos turnos 2+ para a mesma oferta; tokens/latência por turno caem visivelmente.

#### Status de execução — Fase A3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_ · **Verificação:** _(preencher)_

---

### Fase B2 — Verificação round-trip do nível de pensamento (barreira)
**Objetivo:** confirmar `thinking_level` (form) → `reasoning_effort` (OpenAILike).

**Itens:**
- `[sequencial]` Salvar `thinking_level=low` no comercial; disparar um turno; confirmar (via `llm_request`/log do motor ou um unit de `model_factory.build_kwargs` com `model_config={"thinking_level":"low"}`) que `reasoning_effort="low"` chega ao modelo e o piso de `max_tokens` sobe para ≥1024.
- `[paralelo]` (Opcional) teste puro `node`/`pytest` cobrindo o alias.

**Pronto quando:** `build_kwargs({"model":"openai/gpt-5.2","thinking_level":"low"})` → `{"id":...,"reasoning_effort":"low","max_tokens":≥1024}`; e um turno real reflete o efeito.

#### Status de execução — Fase B2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_ · **Verificação:** _(preencher)_

---

## 4. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Injeção da memória de tool | Bloco inchar o prompt / vazar JSON de `pesquisar_ofertas` ou base64 de mídia | Só nomes+offercode das ofertas + dict de atributos; truncar por item e total; remover base64; kill-switch |
| Escopo da memória | Misturar ofertas de conversas diferentes do mesmo contato | Filtrar por `conversation_id` (materializado no ingest) |
| Não ceder contexto ao AGNO | Tentação de ligar `add_history_to_context` (quebra design + filters) | Injetar como `system` no input; `split_messages` inalterado; AGNO segue stateless |
| A2 valida offercode | `codigo_oferta` inválido/alucinado ligar foco errado | `fetch_oferta_by_offercode` antes de considerar em foco; se não existir, ignora |
| Plugin em `storages/` (gitignored) | Fix de A2 se perde ao reinstalar o `.zip` | Reexportar o plugin (`GET /api/plugins/vendas_ia/export`) e atualizar o `.zip` canônico após A2 |
| Campo `thinking_level` livre | Valor inválido para o modelo (ex.: `max` no Gemini) | D3: aceito por ora (texto livre, responsabilidade do usuário); dica no label; auto-mapeamento fica p/ plano futuro |
| Limpar o campo no form | Chave fantasma preservada pelo loop de `buildModelConfig` | Incluir `thinking_level` na exclusão do preserve |
| Best-effort | Exceção na captura derrubar o turno | Tudo em `try/except`; sem bloco em caso de falha |
| Postgres-only / dark mode | Regressão de leitura ou input ilegível no `.dark` | Reusar repos Core existentes; `.wa-field` no input novo |

---

## 5. Perguntas em aberto

**P1 — Memória de tool: bloco compacto (system) vs reconstrução de `assistant.tool_calls`+role `tool` reais?**
✅ DECIDIDO (2026-07-07): **bloco compacto injetado como `system`** (recomendado). Atravessa `split_messages` sem estender a conversão, é barato e controlável (truncável). (b) reconstrução fiel — descartada por ora: exige modelar `tool_calls` no `split_messages` ([agno_engine.py:105-128](../agent/agno_engine.py#L105)) e arrisca validação do AGNO.

**P2 — A2: opção (a) espelho via evento, (b) `_resolve_offercode` aceita `codigo_oferta`, ou ambas?**
⏸️ ADIADO para a execução de A2. Recomendação: começar por **(b)** (menor, resolve o sintoma já) e, se quiser fixar de verdade (painel/persistência), somar **(a)**. Decidir ao abrir o código do plugin.

**P3 — Default do kill-switch `ai_tool_memory_enabled`: ON ou OFF?**
⏸️ ADIADO — decisão do usuário. Recomendação: **ON** (é exatamente o comportamento pedido; sem ele o fix não age). Deixar OFF só se preferir validar em uma conversa antes de ligar global.

**P4 — Mapa de `thinking_level` por modelo (Gemini ≠ OpenAI)?**
⏸️ ADIADO (D3 tira do escopo deste plano). Hoje é texto livre. Se depois o proxy Techify recusar `reasoning_effort` para `google/*`, abrir plano dedicado com uma camada de tradução/omissão por família de modelo.

---

## 6. Checklist de verificação

- [ ] A1: contexto do turno ganha o bloco de memória (ofertas já pesquisadas + atributos), **sem** JSON gigante/base64, truncado; kill-switch OFF remove o bloco.
- [ ] A2: gravar `codigo_oferta` (ou keyword) liga `oferta_em_foco_fragment` (retorna não-vazio) no próximo turno.
- [ ] A3: em conversa multi-turno, `pesquisar_ofertas` roda 1× e não repete; atributos idênticos não regravam; tokens/latência por turno caem vs baseline.
- [ ] B1: input `thinking_level` aparece/salva em `model_config.thinking_level`; limpar remove a chave; `.wa-field` legível no modo escuro.
- [ ] B2: `build_kwargs` traduz `thinking_level`→`reasoning_effort` e sobe o piso de `max_tokens`; turno real reflete.
- [ ] `tests/test_endpoints.py` verde (sem regressão) e, se houver unit puro do `model_factory`, verde.
- [ ] Nenhum segredo/base64 em log ou contexto; nada de `if provider ==` no core; plugin reexportado para o `.zip` canônico.

---

## 7. Apêndice — arquivos-chave (por camada)

**Backend / motor (A1)**
- [app/services/agent_run_service.py](../app/services/agent_run_service.py) — `context_messages` (:280), montagem das messages (:134-142), chamada ao motor (:149-150).
- [db/repositories/message_repo.py](../db/repositories/message_repo.py) — `get_context` (:87-106, filtro :92-93), `get_context_by_conversation` (:108-126) — **fonte** dos `tool_call`; provável novo leitor sem o filtro.
- [agent/memory.py](../agent/memory.py) — `get_context_messages` (wrapper); possível helper de memória de tool.
- [agent/agno_engine.py](../agent/agno_engine.py) — `split_messages` (:105-128) — confirmar que o bloco `system` extra é absorvido.
- [config/settings.py](../config/settings.py) — kill-switch `ai_tool_memory_enabled`.

**Plugin `vendas_ia` (A2)**
- [storages/plugins/vendas_ia/prompts.py](../storages/plugins/vendas_ia/prompts.py) — `_resolve_offercode` (:24-36), fragmento (:39-68).
- [storages/plugins/vendas_ia/state.py](../storages/plugins/vendas_ia/state.py) — `OFFER_ATTR_KEY` (:25), `_mirror`/`set_state` (:168-176).
- [storages/plugins/vendas_ia/events.py](../storages/plugins/vendas_ia/events.py) — `on_message_saved` (:54-108), `EVENT_HANDLERS` (:117) — reuso para opção (a).

**Frontend (B1)**
- [web/static/js/components/ai/AgentsManager.js](../web/static/js/components/ai/AgentsManager.js) — estados (:138,146-147,174-179), `buildModelConfig` (:211-222), Avançado (:347-353).

**Backend já pronto (B — referência, não alterar)**
- [ai_engine/model_factory.py](../ai_engine/model_factory.py) — `_ALIASES`/`_TUNING_KEYS` (:22-28), `build_kwargs` (:64-92).
- [agent/agno_engine.py](../agent/agno_engine.py) — `build_model` (:74-102).
