# Reavaliação do Roadmap WhatsBot Pro — Relatório Executivo

> Consolidação pós-`fe39af2`. Estado real verificado direto nos blobs de `origin/main` (4 commits à frente do HEAD local `c2b5a03`). **Atenção: o código novo ainda NÃO está no working tree local — exige `git pull`.** Toda verificação abaixo foi feita contra `origin/main`.

---

> ## ⚠️ ADENDO pós-`git pull` (2026-06-19, HEAD = `58586e1`)
> Depois deste relatório, o código foi puxado e vieram **dois commits a mais** que ajustam o §3:
> - **`58586e1` — multi-agente Team REMOVIDO.** O engine roda sempre um Agent único; flags
>   `multi_agent_enabled`/`agent_team_mode`/`agents` saíram. A infra `ai_agents`/`ai_engine_enabled`
>   (single-agent config-in-DB) permanece. → **Divergência 3.2 (P60) DISSOLVIDA por remoção:** sumiu o
>   "dois caminhos de config desconexos"; sobra um só (ai_agents single-agent). Multi-agente volta a ser
>   "não implementado/futuro", sem dívida. A linha "4 — multi-agente Team" da tabela do §2 vira ❌ removido.
> - **`71ed713` (PR #8) — split_messages reenvia o histórico do assistant em JSON.** Endurece muito o
>   caminho de split com tools (1/10 → 15/15). **Não** é `output_schema` Pydantic → **Divergência 3.3
>   (P64) continua de pé**, mas o split legado ficou robusto o suficiente para o MVP.
> - **Inalterados:** 3.1 (RCE in-process 🔴), 3.5 (`dev.py` ainda não passa `ai_engine_enabled`),
>   3.6 (`agno` sem pin), 3.7 (`executions` não populadas). O P0/P1 do checklist segue valendo.

---

## 1. TL;DR

- **A Onda 5 (motor multi-agente, Plano 06) foi parcialmente antecipada e já está em produção** (`fe39af2`), ANTES das Ondas 0–4 das quais o plano-mestre dizia que ela dependia. O motor AGNO nasceu standalone, operando **por `phone`** — sem inbox, sem conversation, sem RBAC, sem runtime/subprocesso.
- **O loop manual de tool-calling do OpenAI foi REMOVIDO do handler.** Toda mensagem agora roda por `agno_engine` (AGNO 2.x real). Não há flag de rollback ao motor legado — P65 ("ir direto pro Agno") está, de fato, **cumprida**, mas sem rota de retorno.
- **DIVERGÊNCIA CRÍTICA DE SEGURANÇA (P62):** o code-in-DB (`agent/ai_tool_installer.py`) executa código Python arbitrário vindo do banco **IN-PROCESS** via `exec_module` (verificado: zero `subprocess`/`RLIMIT`/`timeout`/`seccomp`), com privilégios totais (DB, FS, rede, chave do LLM). A decisão dia-1 era subprocess+RLIMIT+timeout. **Isso é RCE para qualquer usuário autenticado.**
- **O motor é Agno-only, mas o legado NÃO foi aposentado:** o handler legado ainda orquestra prompt, histórico, usage e save; o AGNO só faz o núcleo de raciocínio dentro dele.
- **Duas fontes de config de agente desconexas:** o Team multi-agente lê de `config["agents"]` (settings), enquanto o `agent_factory` lê das tabelas `ai_agents` (banco) e só o agente `default` single. As tabelas `ai_agents` **NÃO** dirigem o Team. Precisa reconciliação.
- **Vários "schema à frente do código":** colunas `executions.agent_key/total_tokens/total_cost_usd` criadas mas o writer não popula; tabelas `*_history` populadas mas sem endpoint de leitura/rollback; structured output (P64) não implementado.
- **Bugs de integração confirmados:** `server/dev.py` não passa `ai_engine_enabled` ao handler (config-in-DB nunca liga no dev); `agno` e `openai` sem pin no requirements; `ai_tools.enabled` nasce `1` (não `0`), enfraquecendo o gate humano P63.
- **Planos 01/03/04/05/08 estão intactos e independentes** do motor de IA — único impacto é mecânico: os slots de migration 0007/0008 foram consumidos, então as migrations desses planos precisam ser **renumeradas para 0009+** encadeando a partir do head real (`0008_plugin_installed_deps`), senão `alembic upgrade head` quebra no boot.
- **CLAUDE.md está desatualizado** (ainda lista 8 plugins bundled e "11 tabelas"; o real é só `lembretes` e 15 tabelas).

---

## 2. Plano 06 — Estado Real (fase a fase)

| Fase | Estado | Evidência (origin/main) |
|---|---|---|
| **0** — Spike AGNO | ✅ **FEITO** (superado) | `agent/agno_engine.py` usa `agno.agent.Agent`, `agno.team.Team/TeamMode`, `agno.models.openai.OpenAILike` → Techify. AGNO 2.x em produção. |
| **1** — Fundação de dados | ✅ **FEITO** (forma reduzida) | Migration `0007` cria `ai_agents/ai_prompts/ai_variables/ai_tools` + 3 `*_history` + ALTER em `executions`. Repos com versionamento (`version+=1` + snapshot). Schema mais enxuto que o planejado (sem `description/hooks_config/routing_targets/is_router`). |
| **2** — Agente configurável do banco | ✅ **FEITO** | `agent_factory.build_for_contact()` resolve prompt+model+tools por request, sem restart. `seed_default_agent` idempotente. Filters `filter.system_prompt/llm.*/tool.*` preservados dentro do engine. |
| **2** — telemetria executions | ⚠️ **PARCIAL** | Colunas existem; **`execution_repo` NÃO as popula** (verificado: zero refs a `agent_key/total_tokens/total_cost_usd`). |
| **2** — structured output (P64) | ❌ **NÃO FEITO** | Zero `output_schema`/`response_model`/`LLMResponse`. `_extract_reply` pega o último assistant message como texto; split continua legado. |
| **3.1** — tool installer (code-in-DB) | ✅ **FEITO** | `ai_tool_installer.py` materializa `ai_tools.code` → importa via `exec_module`, fail-closed, valida nome `^[a-z][a-z0-9_]{0,63}$`. |
| **3.2** — runner isolado (P62) | ❌ **NÃO FEITO / DIVERGE** | **IN-PROCESS.** Zero `subprocess`/`RLIMIT`/`timeout`/`tool_worker`. Barreira de segurança principal do plano ausente. |
| **3.3** — gate humano P63 + AST | ⚠️ **PARCIAL/FRACO** | `install_status` nasce `pending`, mas `enabled` nasce **`1`** — tool criada por PUT já fica ativa. Sem fluxo "IA propõe → ADM aprova". Sem validação AST de imports. |
| **4** — multi-agente Team | ⚠️ **PARCIAL / desconexo** | `_build_team` itera `handler.agents` (config/settings), **não** `ai_agents`. Sem binding agente↔inbox (inboxes não existem). Dois mundos paralelos. |
| **4** — UI / frontend ai/* | ❌ **NÃO FEITO** | Sem `AgentsManager/PromptsEditor/...` nem rota SPA. Só rotas REST CRUD. |
| **5** — roteamento por handoff | ❌ **NÃO FEITO** | Sem `transferir_para_outro_agente`, sem `run_with_routing`, sem depth≤5/loop-guard. |
| **6** — history/rollback API | ⚠️ **PARCIAL** | Tabelas `*_history` populadas; **sem endpoint** `GET .../history` nem `POST .../rollback`. |
| **6** — hot-reload de dado | ✅ **FEITO** (de facto) | Leitura por request = editar prompt/modelo/tools reflete na próxima msg. **Código de tool ainda exige restart.** |
| **6** — aposentar legado | ❌ **NÃO FEITO** | Handler legado ainda orquestra todo o resto. |

**Divergências críticas vs decisões:** P62 (in-process, contra subprocess dia-1), P64 (structured output não existe), P60 (Team config-driven, não `ai_agents`/inbox), P65 (cumprida, mas sem fallback). Layout também divergiu: não há pacote `ai_engine/` nem `run_conversation()` — vive em `agent/` e o entrypoint é `handler.aprocess_message(phone, ...)`.

---

## 3. Divergências que Exigem Decisão do Thiago AGORA

### 3.1 — Code-in-DB IN-PROCESS vs subprocesso isolado (P62) 🔴 BLOQUEADOR DE SEGURANÇA
**Fato:** `ai_tool_installer.py` faz `exec_module` no mesmo processo do webhook. Qualquer `PUT /api/ai/tools/{name}` grava código Python que roda com acesso total a DB, filesystem, rede e à chave do LLM. **Sem RBAC, qualquer usuário autenticado (senha única) tem RCE no servidor de produção.**
**Trade-off:** subprocesso+RLIMIT (P62 original) é a barreira correta, mas depende do `SubprocessService` do Plano 09 (não existe). Construí-lo do zero atrasa.
**Recomendação:** decisão em duas camadas, imediata:
1. **Curto prazo (antes de qualquer exposição a usuário):** gate a edição de `ai_tools.code` a **admin-only** (curto-circuito no middleware), NÃO `agent.manage` genérico. Documentar o in-process como dívida técnica explícita com nota de risco.
2. **Médio prazo:** retrofitar isolamento sobre o `SubprocessService` quando o Plano 09 Fase 4 chegar — co-decidir P62+P67 juntos.
> **Não habilitar `ai_tools` para usuários finais até (1) estar resolvido.**

### 3.2 — Team config-driven vs `ai_agents`/inbox-driven (P60) 🟡
**Fato:** dois caminhos desconexos. O Team lê `config["agents"]`; o `agent_factory` lê `ai_agents` (só single default). A decisão P60 era "agente↔inbox via `default_agent_key`" — coluna não existe, inboxes não existem.
**Trade-off:** unificar agora é prematuro (inboxes só chegam no Plano 01). Manter dois mundos acumula dívida e confunde o usuário.
**Recomendação:** **definir `ai_agents` como fonte única de verdade** dos agentes e fazer o Team ler dela. Marcar o binding a inbox como bloqueado pelo Plano 01. Não construir o binding agora — só registrar a direção para não cristalizar o caminho `config["agents"]`.

### 3.3 — Structured output via `output_schema` (P64) 🟡
**Fato:** não implementado; `_extract_reply` declara explicitamente que não usa schema; split continua legado.
**Trade-off:** o split legado funciona. O `output_schema` Pydantic daria controle de `mensagens_para_usuario[]`/`private_message`, mas é re-trabalho do caminho de saída.
**Recomendação:** **rebaixar P64 a fase futura opcional** (não dia-1). O split atual basta para o MVP. Re-planejar só se/quando precisar de saída estruturada para handoff/roteamento.

### 3.4 — Allowlist de deps aberta (P66) 🟢
**Fato:** `is_dep_allowed()` retorna `True` para tudo (verificado). Bate com a decisão P66 (sem allowlist no MVP).
**Recomendação:** **manter como está** — está conforme decidido. É um choke-point único já pronto para fechar depois. Apenas registrar que, somado ao 3.1 (in-process), um usuário pode instalar QUALQUER pacote pip E executar código — o gating ADM do 3.1 cobre os dois.

### 3.5 — `server/dev.py` não passa `ai_engine_enabled` 🟡 (bug)
**Fato verificado:** `dev.py:52-62` passa `multi_agent_enabled`/`agent_team_mode`/`agents`, mas **não** `ai_engine_enabled`; `config.py:118` passa. No launcher de dev/hot-reload o config-in-DB nunca liga no boot (só após um PUT manual).
**Recomendação:** **corrigir** (paridade dev/prod) — adicionar `ai_engine_enabled=settings.get("ai_engine_enabled", False)` em `dev.py`. Bug de 1 linha.

### 3.6 — `agno` (e `openai`) sem pin de versão 🟡
**Fato verificado:** `requirements.txt` lista `agno` e `openai` sem `==`/range. O plano pedia `agno>=2.6,<3`.
**Trade-off:** AGNO 2.x→3.x pode quebrar a API (`Team`, `OpenAILike`). Sem pin, um build futuro pega uma versão incompatível silenciosamente.
**Recomendação:** **pinnar** `agno>=2.6,<3` (ou exato) e `openai` num range conhecido. Baixo esforço, alto valor para reprodutibilidade do EXE/Docker.

### 3.7 — `executions.agent_key/total_tokens/total_cost_usd` não populados 🟢
**Fato verificado:** colunas na migration 0007; `execution_repo` sem nenhuma referência a elas.
**Recomendação:** **fechar a telemetria** — fazer o writer popular as colunas (schema já existe). Baixo esforço.

### 3.8 — CLAUDE.md desatualizado 🟢
**Fato:** lista 8 plugins bundled (real: só `lembretes`) e "11 tabelas" (real: 15+, com `ai_*` e `tool_overrides`). Não menciona o motor AGNO nem `ai_engine_enabled`.
**Recomendação:** **atualizar** — seção de tabelas, lista de bundled, e adicionar uma seção descrevendo o motor AGNO/AI engine. Evita que futuros agentes/devs ajam sobre premissas falsas.

---

## 4. Re-sequenciamento das Ondas

A premissa "06 depende de 01+02+03+09" **não bate mais com a realidade**: as Fases 0–3 do 06 nasceram standalone, operando por `phone`, e já estão prontas.

**O que o código novo já de-risca / destrava:**
- **Spike AGNO (Fase 0 do 06) — resolvido.** AGNO 2.x está em produção; o risco "será que o framework serve?" foi eliminado.
- **Config-in-DB + code-in-DB — provados** (mesmo que o último seja inseguro). O padrão "resolver prompt/modelo/tools do banco por request sem restart" funciona.
- **pip-deps compartilhado (`pkg_deps`) — pronto.** Destrava a Fase 3.2 do Plano 02 (extração do GOWA para plugin pode declarar `dependencies:` no manifest) e a instalação de deps de AI tools.

**Nova ordem recomendada:**

| Onda | Conteúdo | Justificativa |
|---|---|---|
| **0 (agora)** | **Endurecimento de segurança do que já shippou:** gate ADM no `/api/ai/tools` (mitigação imediata de P62), pin do `agno`, fix `dev.py`, popular `executions`, atualizar CLAUDE.md | O motor está em produção com um buraco de RCE. Fechar isso vem antes de qualquer feature nova. |
| **1** | **Plano 09 Fases 1–4** (lifecycle, supervisor de tasks, **`SubprocessService`**) | O `SubprocessService` é a base para retrofitar o isolamento do code-in-DB (P62/P67) e para extrair o GOWA. Maior alavanca de de-risk. |
| **2** | **Retrofit P62:** migrar `ai_tool_installer` de `exec_module` para subprocesso gerenciado | Só possível após a Onda 1. Fecha o buraco de segurança de forma definitiva e remove o gate ADM temporário como única proteção. |
| **3** | **Planos 03 (RBAC) + 01 (inbox/conversas)** | RBAC dá o modelo de privilégio que o gate ADM temporário aproxima. Inbox/conversa destrava o binding agente↔inbox (P60) e o handoff (Fase 5 do 06). |
| **4** | **Completar 06:** reconciliar Team↔`ai_agents`, binding por inbox, handoff/routing, history/rollback API, UI ai/* | Agora com inbox e RBAC disponíveis. |
| **5+** | Planos 02 (canais/multi-número), 04, 05, 08 | Independentes; seguem a ordem original. |

**Por quê:** a Onda 5 foi "puxada pra frente" só no núcleo de raciocínio (por `phone`). O que **falta** do 06 (inbox, handoff, isolamento) continua dependente de 09/03/01 — então essas dependências viram a prioridade real, não o motor.

---

## 5. Trabalho de Integração Novo (não previsto pelos planos)

1. **Unificar as duas fontes de config de agente** — `config["agents"]` (Team) vs `ai_agents` (factory). Definir `ai_agents` como fonte única; fazer `_build_team` ler dela. Não estava em nenhum plano porque os dois caminhos foram criados juntos no mesmo commit.
2. **Ligar `ai_agents` ao Team e a inboxes quando o Plano 01 chegar** — introduzir `default_agent_key` (inbox) e `active_agent_key` (conversation), e fazer a seleção de agente fluir da cascata inbox→conversa. Hoje não há nenhum ponto onde inbox/conversa selecione agente.
3. **API de history/rollback** — as tabelas `*_history` já gravam snapshots a cada save, mas não há endpoint. Falta `GET /api/ai/{kind}/{key}/history` e `POST .../rollback/{version}`. Dados prontos, API ausente.
4. **Retrofit do `ai_tool_installer` sobre o `SubprocessService`** — dependência cruzada explícita entre Planos 06 e 09 que nenhum dos dois modelou (o 09 assumia que o tool_runner ainda não existia; o 06 assumia que o 09 já existiria).
5. **Posicionar `pkg_deps` na fundação de runtime** — é o único subprocesso pip de longa duração (até 600s), roda no boot fora de qualquer supervisor. Decidir se o `SubprocessService` (Plano 09 Fase 4) o abraça ou se fica explicitamente à parte.
6. **Reconciliar a cascata de gate de IA (Plano 01) com o motor stateless** — o gate `contact.ai_enabled`/`auto_reply` continua no webhook (correto: o engine é stateless e só raciocina). Mas quando conversas existirem, a cascata global→inbox→conversa precisa coexistir com a seleção de agente. Ponto de inserção permanece o webhook, não o engine.
7. **Adicionar `/api/ai/*` à matriz de autorização do RBAC (Plano 03)** — módulo de rotas inteiro novo que não existia quando o plano foi escrito. `/api/ai/agents|prompts|variables` → `agent.manage`; `/api/ai/tools` (code-in-DB) → **admin-only** ou permissão dedicada `ai.code.manage`.

---

## 6. Ações Imediatas (checklist priorizado)

**P0 — Segurança (fazer antes de expor `ai_tools` a qualquer usuário):**
- [ ] Gate `PUT/DELETE /api/ai/tools/*` e `/api/ai/restart` a **admin-only** (curto-circuito no `auth_middleware`). Mitigação imediata do RCE in-process.
- [ ] Registrar formalmente a decisão P62: aceitar in-process como **dívida técnica documentada** com nota de risco, OU agendar o retrofit subprocesso. (Recomendação: aceitar temporariamente + agendar retrofit na Onda 1/2.)
- [ ] Decidir: tornar `ai_tools.enabled` default **`0`** (gate P63 real) em vez de `1`.

**P1 — Bugs de integração (baixo esforço, alto valor):**
- [ ] Corrigir `server/dev.py` para passar `ai_engine_enabled` ao `AgentHandler` (paridade dev/prod).
- [ ] Pinnar `agno>=2.6,<3` e `openai` no `requirements.txt`.
- [ ] Fazer `execution_repo` popular `agent_key/total_tokens/total_cost_usd` (fecha telemetria da Fase 2).

**P2 — Documentação e roadmap:**
- [ ] Atualizar **CLAUDE.md**: tabelas (15+), bundled (só `lembretes`), seção do motor AGNO/AI engine, flag `ai_engine_enabled`.
- [ ] Reescrever **Plano 06** como plano de COMPLETUDE+ENDURECIMENTO (não greenfield): marcar Fases 0–2 e 3.1 como FEITAS; trocar `ai_engine/`→`agent/`, `run_conversation`→`aprocess_message`; rebaixar 3.2 a prioridade de segurança.
- [ ] Reescrever **Plano 00**: seção 3 (ondas) e §2.3 (grafo) — remover dependência "06 ⇐ 01/02/03/09" das Fases 0–3; manter só para acoplamento a inbox/conversa.
- [ ] Reabrir e re-decidir no **DECISOES.md**: P62 (texto diz "subprocess dia-1", entregue foi in-process), P64 (re-especificar ou descartar), P67 (sair de ADIADO — virou "retrofit"), P60 (fonte única de verdade).

**P3 — Renumeração de migrations (antes de codar qualquer plano de dados):**
- [ ] Renumerar as migrations reservadas em **todos** os planos que assumiam 0007/0008: Plano 01 (`inbox_conversations`→0009, `backfill`→0010), Plano 02 (`channels`→0009+), Plano 03 (RBAC→0009+), Plano 05 (`custom_attributes`→0009+), Plano 04 (quick_replies). Todas com `down_revision` apontando para o head real **`0008_plugin_installed_deps`**. Sem isso, `alembic upgrade head` ramifica e quebra o boot.

**P4 — Completude do 06 (depende das ondas):**
- [ ] Adicionar API de history/rollback (dados já existem).
- [ ] Reconciliar Team↔`ai_agents` (fonte única).
- [ ] Decidir destino do structured output (P64).

---

**Incertezas honestas:** (a) Não inspecionei o frontend para confirmar 100% a ausência da UI ai/* — baseio-me na ausência de rotas SPA e na reavaliação; (b) o grau de "gracioso" do fallback do `agent_factory` quando a flag está OFF foi reportado mas não exercitei em runtime; (c) o comportamento exato do `_build_team` com `handler.agents` vazio (defaults `vendas`+`suporte`) vem da reavaliação, não de execução. Tudo o mais neste relatório foi **verificado diretamente nos blobs de `origin/main`**.