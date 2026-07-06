# Plano 34 — Hardening da resolução de agentes de IA contra JSON corrompido/duplo-codificado + saneamento estrutural das colunas JSON

> **Status:** PLANEJAMENTO · **Data:** 2026-07-06 · **Escopo:** médio (Track A: hardening puro, sem migration, ~3 pontos de código + testes; Track B opcional: 1 migration Alembic JSONB + repo). **Origem:** Achado E.A do consolidado de QA (Ezequiel, 02–03/07/2026) — "IA não responde: Não foi possível resolver o agente". Refinado por investigação nesta sessão: leitura de código (`arquivo:linha` abaixo) + inspeção **read-only** dos bancos vivos (`whatsbot` @203.0.113.60 e `whatsbot_test`).
> **Método:** grep + leitura dos arquivos reais + `SELECT` (somente leitura) nos dois bancos e em `information_schema.columns`. Toda afirmação de `arquivo:linha` foi verificada nesta sessão.
> **O quê/por quê:** uma **única** linha de `ai_agents` com JSON malformado (dupla-codificação: uma string JSON dentro de outra) derruba **100%** das conversas de IA (todos os canais + sandbox), silenciosamente para o cliente. A parte que é **bug de código** é a **fragilidade**: não há decode tolerante nem AgentSpec de emergência, e um `dict("...string...")` estoura e é reclassificado como "banco quebrado". O dado sujo em si tem outra origem (script de manutenção / `UPDATE` manual) e **hoje já foi limpo nos dois bancos** — então este plano é **preventivo/hardening**, não recuperação de emergência.
>
> **Como usar este plano:** ao executar cada fase, preencha o bloco **"Status de execução"** dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro. **Verde a cada fase.** **Caracterização ANTES** de mexer no fluxo crítico de resolução. **Um refactor por commit.** Track A (F0–F4) é a parte urgente e é totalmente independente do Track B (F5–F6).

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** ✅ (2026-07-06) | O plano é **hardening preventivo**, não recuperação de emergência: os dois bancos (`whatsbot` e `whatsbot_test`) estão **limpos agora** (sem dupla-codificação viva — confirmado por `SELECT`). | Nenhum passo de "recuperar dados" no caminho crítico. Recuperação, se reaparecer, é via `POST /api/ai/agents/{key}/rollback/{version}` (histórico íntegro) ou `UPDATE` pontual — **fora do escopo deste plano**. |
| **D2** ✅ (2026-07-06) | Solução em **dois tracks**: **Track A** (fragilidade do app — o único bug de código, URGENTE, **sem migration**) e **Track B** (saneamento estrutural TEXT→JSONB — **opcional**, fase 2, só depois do A). | O merge do Track A resolve o apagão sozinho. Track B elimina a *classe* de dupla-codificação na origem, mas não é pré-requisito de nada em A. |
| **D3** ✅ (2026-07-06) | Defesa em profundidade: **(1)** decode tolerante em `coerce_json` (desembrulha N-camadas), **(2)** piso de emergência (`AgentSpec` default) em `build_for_contact`, **(3)** guarda `isinstance(dict)` em `save_agent_prompt`. As três camadas são complementares — nenhuma sozinha basta. | F1/F2/F3 entregam as três camadas. Mesmo que uma linha suja escape do decode, o piso responde; mesmo que o piso não montasse, o decode já degradou pro default. |
| **D4** ✅ (2026-07-06) | Distinção de gravidade: as colunas **`ai_agents`** são as ÚNICAS cujo consumidor faz `dict()`/itera direto e **estoura duro**. As outras colunas TEXT-JSON (`channels.config`, `executions.routing_steps`, `messages.reactions`, `ai_variables.value`, `execution_steps.data`, `config.value`) já usam `coerce_json` com **fallback** e **degradam suave**. | Track B migra **primeiro** (e talvez só) as 3 colunas JSON de `ai_agents`. As demais viram fase 2-opcional (F6), sem urgência. |
| **Princípio fixo** | Mudança **aditiva e best-effort**: nenhum decode a mais pode derrubar o turno; o piso de emergência sempre **loga em ERROR** (alto e claro) para o dado sujo nunca passar despercebido. Sem tocar em plugins. `agent_key`/tool name é identidade — não renomear. | Todos os `try/except` defensivos; sem regressão de comportamento no caminho feliz; Postgres-only. |

---

## 1. Resumo executivo

O motor de IA resolve, por requisição, qual agente responde a cada conversa (`build_for_contact`). Ele lê a linha do agente do banco (colunas JSON serializadas à mão em campos **TEXT**), decodifica com `coerce_json` e monta um `AgentSpec`. Três fraquezas se somam para transformar "um campo malformado em uma linha" em "nenhuma conversa é respondida":

1. **`coerce_json` só desembrulha uma camada** ([_mapping.py:33-39](../db/repositories/_mapping.py#L33-L39)): num valor duplo-codificado (`"{\"model\": …}"`) ele devolve a **string** interna, não o dict.
2. **`build_for_contact` não tolera formato errado** ([agent_factory.py:293](../agent/agent_factory.py#L293)): `dict(agent.get("model_config") or {})` cobre `None`/vazio mas **não** uma string — `dict("...string...")` estoura `ValueError`, que o `except Exception` ([:310-312](../agent/agent_factory.py#L310-L312)) reclassifica como `AgentResolutionError` (que significa "banco genuinamente quebrado"). **Não há AgentSpec de emergência** — as constantes `DEFAULT_SYSTEM_PROMPT`/`DEFAULT_MODEL` existem ([:36-40](../agent/agent_factory.py#L36-L40)) mas nunca são usadas como spec inteiro de last-resort.
3. **`save_agent_prompt` não tem a guarda `isinstance(dict)`** ([ai_engine.py:126](../server/routes/ai_engine.py#L126)) que a rota `save_agent` completa tem ([:70-81](../server/routes/ai_engine.py#L70-L81)): sobre uma linha já suja, `existing.get("model_config")` vem string → novo `json.dumps` → **tripla** codificação (piora o estrago).

Os consertos são pequenos e independentes (Track A, sem migration). Opcionalmente, **Track B** elimina a classe de bug na origem: migrar as colunas JSON de `ai_agents` de TEXT para **JSONB nativo** (helper `_json_type()` já existe) e parar de `json.dumps` à mão no `agent_repo`, deixando o SQLAlchemy serializar **uma** vez.

---

## 2. Como funciona hoje (mapa) — `arquivo:linha` verificado

### 2.1 A cadeia da falha (o "apagão")
- `build_for_contact(handler, contact)` é o único ponto de resolução ([agent_factory.py:253-312](../agent/agent_factory.py#L253-L312)). Cascata: agente vinculado → default → constantes seed.
- `_resolve_active_agent` ([:193-202](../agent/agent_factory.py#L193-L202)) cai no `dynamic_registry.get_default_agent()` quando não há vínculo — **o agente `default` é o fallback de toda conversa**, então uma corrupção nele atinge 100% dos contatos.
- ⚠️ **O ponto exato que estoura** ([:293](../agent/agent_factory.py#L293)): `model_config = dict(agent.get("model_config") or {})`. Note a **incoerência**: a mesma função **já** tolera `prompt` vazio (cai em `DEFAULT_SYSTEM_PROMPT`, [:268-272](../agent/agent_factory.py#L268-L272)) e `model` ausente (cai em `DEFAULT_MODEL`, [:294-295](../agent/agent_factory.py#L294-L295)) — só **não** tolera o *container* `model_config` malformado. O mesmo `dict(...)` roda sobre `hooks_config` ([:300](../agent/agent_factory.py#L300)).
- ⚠️ **A reclassificação** ([:308-312](../agent/agent_factory.py#L308-L312)): `except AgentResolutionError: raise` / `except Exception as e: raise AgentResolutionError(str(e))`. Um erro de **formato de campo** vira "nenhum agente pôde ser resolvido".
- O caller trata `AgentResolutionError` como **parada dura**: `agent_run_service` chama `handler._emit_resolution_error(contact, sender, e)` ([agent_run_service.py:286-291](../app/services/agent_run_service.py#L286-L291)) → card de erro painel-only, **nada é enviado ao cliente**. Também usado em [:174-175](../app/services/agent_run_service.py#L174) e [:183-184](../app/services/agent_run_service.py#L183).

### 2.2 O decode que não desembrulha o suficiente
- `coerce_json(value, default)` ([_mapping.py:24-40](../db/repositories/_mapping.py#L24-L40)): se `str`, faz **um** `json.loads` e retorna; num valor duplo-codificado devolve a string interna (que ainda parece JSON), não o dict. Docstring assume no máximo uma camada ("Postgres devolve dict/list; SQLite guarda TEXT").
- `agent_repo._row_to_dict` ([agent_repo.py:37-42](../db/repositories/agent_repo.py#L37-L42)) aplica `coerce_json` a `model_config` (default `{}`), `tool_names` (default `None`), `routing_targets`, `hooks_config` (default `{}`). Se o valor era duplo, o dict do agente carrega **strings** nesses campos → estoura lá no `build_for_contact`.

### 2.3 As gravações (onde o `json.dumps` manual acontece)
- `agent_repo.ensure` ([:88-89](../db/repositories/agent_repo.py#L88-L89)) e `agent_repo.save` ([:150-157](../db/repositories/agent_repo.py#L150-L157)) serializam `model_config`/`tool_names`/`routing_targets`/`hooks_config` com `json.dumps(... or {})`. Isso está **correto** desde que a entrada seja sempre dict/list — a dupla-codificação vem de passar uma **string** aqui.
- A rota `save_agent` (PUT completo) tem guarda: rejeita se `model_config`/`hooks_config` não forem dict e `tool_names`/`routing_targets` não forem list/None ([ai_engine.py:70-81](../server/routes/ai_engine.py#L70-L81)).
- ⚠️ A rota `save_agent_prompt` (patch só-prompt, usada pelo wizard) **NÃO** tem guarda: repassa `existing.get("model_config") or {}`, `existing.get("hooks_config") or {}`, `existing.get("tool_names")`, `existing.get("routing_targets")` direto ao `save()` ([ai_engine.py:118-134](../server/routes/ai_engine.py#L118-L134)). Se `existing` veio de uma linha suja, `existing.get(...)` é string → `json.dumps` de novo → tripla codificação.
- `seed_default_agent` ([agent_factory.py:93-113](../agent/agent_factory.py#L93-L113)) e `migrate_legacy_config_to_default_agent` ([:116-164](../agent/agent_factory.py#L116-L164)) sempre passam dict/list nativos — corretos.

### 2.4 Estrutura das colunas (o achado "de estrutura")
Inspeção do banco vivo (`information_schema.columns`, prod):

| Padrão | Colunas | `pg_typeof` | Consumidor | Risco |
|---|---|---|---|---|
| ✅ **JSONB nativo** (`_json_type()`, [tables.py:38-49](../db/tables.py#L38-L49)) | `contacts.custom_attributes`, `atendimentos.custom_attributes`, `custom_attribute_definitions.options`, `saved_atendimento_filters.spec` | `jsonb` | SQLAlchemy serializa 1× | **imune** |
| ⚠️ **TEXT + `json.dumps` manual** — **estoura duro** | `ai_agents.model_config` · `ai_agents.routing_targets` · `ai_agents.hooks_config` (+ `tool_names`) | `text` | `dict()`/itera direto | **ALTO** |
| 🟡 **TEXT + `json.dumps` manual** — degrada suave | `ai_variables.value`, `channels.config`, `config.value`, `channel_credentials.value`, `executions.routing_steps`, `execution_steps.data`, `messages.reactions`, snapshots (`ai_*_history.snapshot`) | `text` | `coerce_json(..., {}/[])` com fallback | baixo |

### 2.5 Falsos positivos descartados
| Hipótese | Veredito | Razão (verificada) |
|---|---|---|
| "É bug do plugin `prompts_dinamicos` / palavras-chave" | ❌ Descartado | A resolução do agente roda **antes** de qualquer filtro de plugin; QA reproduziu com "Oi" no Sandbox sem palavra-chave. |
| "As colunas JSONB nativas também estão vulneráveis" | ❌ Descartado | `pg_typeof=jsonb` + SQLAlchemy serializa 1×; não passam por `json.dumps` manual. |
| "Precisa recuperar dados agora (emergência)" | ❌ Descartado | `SELECT` nos dois bancos hoje: **nenhuma** linha duplo-codificada viva. É prevenção. |
| "Migrar JSONB sozinho conserta o apagão" | ❌ Parcial | JSONB reduz a *entrada* de sujeira, mas uma linha JSONB pode guardar uma **string** JSON (JSONB aceita string como valor) → o app ainda estouraria sem o Track A. Por isso **A vem primeiro e é o que conserta**. |
| "As demais colunas TEXT-JSON são urgentes" | ❌ Descartado (D4) | Todas usam `coerce_json` com default → degradam para `{}`/`[]`, nunca derrubam o serviço. |

---

## 3. Fases / Roadmap

### Diagrama de dependências (waves)

```
TRACK A (hardening — URGENTE, sem migration)
WAVE 0   F0(caracterização) ─┐
                             │ (barreira: F0 documenta o crash ANTES de consertar)
WAVE 1   F1 · F2 · F3        │   ← F1/F3 independentes (🟢); F2 usa o helper de F1 (soft)
            │ (F1→F2 soft)   │
WAVE 2   F4(testes regressão) [depende de: F1,F2,F3]

TRACK B (saneamento estrutural — OPCIONAL, fase 2, só depois de A mergeado)
WAVE 3   F5(ai_agents TEXT→JSONB + repo)   🔴 sozinha (migration)
WAVE 4   F6(demais colunas TEXT→JSONB)      🟢 opcional, adiável
```

### Tabela de fases

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando (resumo) |
|---|---|---|---|---|---|
| 0 | **F0** Caracterização | testes | 🔴 sozinha (barreira) | baixo | teste captura o crash atual (`build_for_contact` levanta em linha duplo-codificada) |
| 1 | **F1** `coerce_json` N-camadas | `db/repositories/_mapping.py` | 🟢 | baixo | valor duplo/triplo desembrulha até dict/list; loga warning se sobrar `str` |
| 1 | **F2** Piso de emergência + coerção tolerante | `agent/agent_factory.py` | 🟢 `[depende de: F1 soft]` | médio | linha suja → `AgentSpec` default (não `AgentResolutionError`); log ERROR |
| 1 | **F3** Guarda em `save_agent_prompt` | `server/routes/ai_engine.py` | 🟢 | baixo | patch-de-prompt sobre linha suja não gera tripla codificação |
| 2 | **F4** Testes de regressão | `tests/` | 🔴 `[depende de: F1,F2,F3]` | baixo | F0 vira verde (comportamento invertido: IA responde no default) |
| 3 | **F5** `ai_agents` TEXT→JSONB | `db/tables.py`, `db/alembic/`, `agent_repo.py` | 🔴 sozinha (migration) | alto | migration round-trip; repo passa dict/list nativo; suíte verde |
| 4 | **F6** Demais colunas TEXT→JSONB | `db/tables.py`, `db/alembic/`, repos | 🟢 opcional | médio | idem, por coluna; adiável indefinidamente |

---

### Fase F0 — Caracterização do apagão (barreira, ANTES de consertar)
**Objetivo:** provar o comportamento atual com um teste que hoje passa (documenta o crash) e que F4 vai inverter.

**Itens:**
- `[sequencial]` Novo teste (sugestão: `tests/test_agent_json_hardening.py` ou dentro de `tests/test_model_factory.py`): gravar uma linha `ai_agents` com `model_config` **duplo-codificado** — inserir via SQL direto o texto `'"{\\"model\\": \\"x/y\\"}"'` (uma string JSON dentro de outra), simulando a corrupção. Rodar contra o Postgres de teste (`WHATSBOT_TEST_DB_URL`).
- `[sequencial]` Asserção do estado ATUAL: `build_for_contact(handler, contact)` **levanta `AgentResolutionError`** (o crash de hoje). Marcar com um comentário `# F0: caracteriza o bug; F4 inverte para NÃO levantar`.
- `[paralelo]` Registrar em prosa no teste o repro read-only do QA (`SELECT model_config … WHERE agent_key='default'` → `repr` começa com `"` = duplo).

**Pronto quando:** o teste roda verde **descrevendo o crash** (levanta `AgentResolutionError`) — capturando o baseline antes de qualquer mudança de produção.

#### Status de execução — Fase F0
**Estado:** ✅ Concluída (2026-07-06)
- **O que foi feito:** Novo `tests/test_agent_json_hardening.py` (script standalone, convenção de `test_agent_routing.py`): semeia o agente `default` limpo, grava `model_config` **duplo-codificado** via SQL direto (`json.dumps(json.dumps({"model":"x/y"}))`), invalida o cache do `dynamic_registry` e asserta o comportamento ATUAL — `build_for_contact` **levanta `AgentResolutionError`**. Marca com `# F0: caracteriza o bug; F4 inverte`.
- **Como foi feito / decisões:** Seguido o padrão standalone (`init_test_engine(reset=True)` + `check()` + `sys.exit`) porque é a convenção dos testes DB-backed de agente aqui; `pytest tests/` inteiro não roda esses 8 scripts (têm `sys.exit` no topo → `SystemExit` quebra a coleção). Rodado individualmente via `venv/bin/python tests/test_agent_json_hardening.py`. Repro read-only do QA codificado como asserção (`raw.startswith('"')`) + asserção intermediária de que `agent_repo` devolve `str` (decode de 1 camada).
- **Problemas / pendências:** Nenhum. Banco de teste da lane: `whatsbot_test_34` (UTF8/template0) via `WHATSBOT_TEST_DB_URL`.
- **Verificação:** `venv/bin/python tests/test_agent_json_hardening.py` → `RESULTS: 4 passed, 0 failed`; log `ERROR ... build_for_contact failed (dictionary update sequence element #0 has length 1; 2 is required)` confirma o `dict(str)` estourando. Baseline `test_agent_routing.py` = 29 passed.

---

### Fase F1 — `coerce_json` desembrulha N-camadas
**Objetivo:** que um valor duplo/triplo-codificado seja reduzido ao objeto real, com aviso quando não estabilizar.

**Itens:**
- `[sequencial]` Em [_mapping.py:24-40](../db/repositories/_mapping.py#L24-L40): manter a assinatura `coerce_json(value, default=None)`. Trocar o `json.loads` único por um **laço limitado** (ex.: até ~5 iterações — guarda contra loop patológico): enquanto o resultado for `str` **e** parecer JSON (`json.loads` sucede), continuar desembrulhando; parar quando virar dict/list/número/bool/None ou quando `json.loads` falhar.
- `[sequencial]` Se, ao fim do laço, o resultado **ainda** for `str`, `logger.warning(...)` (uma linha, citando um trecho curto) e retornar `default` — nunca propagar a string crua para consumidores que esperam dict/list. **Cuidado:** preservar o caso legítimo de valor que É uma string JSON de conteúdo (ex.: um campo cujo valor final deve ser texto) — hoje nenhum consumidor de `coerce_json` espera string final; confirmar via grep dos call sites (`plugin_repo`, `tool_repo`, `message_repo`, `contact_query`, `agent_repo`) antes de decidir o default por call site.
- `[paralelo]` Testes unitários puros do helper: `'{"a":1}'`→`{'a':1}`; `'"{\\"a\\":1}"'`→`{'a':1}`; triplo→`{'a':1}`; `'"texto solto"'`→ `default` + warning; `None`/`''`→`default`; dict já pronto→passa intacto.

**Pronto quando:** os testes do helper passam; `venv/bin/python -m pytest tests/ -q` (Postgres) segue verde; nenhum call site existente regride (os que hoje recebem dict/list continuam idênticos).

#### Status de execução — Fase F1
**Estado:** ✅ Concluída (2026-07-06)
- **O que foi feito:** `db/repositories/_mapping.py` — `coerce_json` trocou o `json.loads` único por um laço limitado (`_MAX_JSON_UNWRAP=5`) que desembrulha enquanto o resultado for `str` reparseável; se ao fim ainda for `str`, `logger.warning` (com trecho ≤80 chars) + retorna `default`. Assinatura preservada. Novo `tests/test_coerce_json.py` (14 checks, puro).
- **Como foi feito / decisões:** Refatorado o corpo para `if not isinstance(str): return value` cedo (passthrough de dict/list/número/bool intacto), depois o laço. Decisão P2 aplicada: nenhum call site espera string final (grep confirmou — `tool_repo` `[]`, `plugin_repo` `[]`, `contact_query` `{}`, `message_repo.reactions` `{}`/`None`, `agent_repo` `{}`/`None`); `ai_variables.value` (polimórfica) **não** usa `coerce_json` hoje, então número/bool via `'42'`/`'true'` seguem decodificando corretamente. Warning novo só dispara em dado genuinamente corrompido — o que é desejável (torna a sujeira visível).
- **Problemas / pendências:** Nenhum. Comportamento mudado para `'"hi"'`/`'texto'`: antes `coerce_json` devolvia a `str`; agora devolve `default`+warning — sem consumidor afetado (P2). **Interação importante descoberta:** como `agent_repo._row_to_dict` já aplica `coerce_json` a `model_config`, **F1 sozinho conserta o crash de `model_config`** — o duplo-encoding recuperável volta ao objeto real (modelo real restaurado, melhor que o default) e o irrecuperável cai em `{}`→`DEFAULT_MODEL`. Por isso a caracterização F0 (que assertava "levanta") deixou de valer após F1: o `tests/test_agent_json_hardening.py` foi atualizado **neste mesmo commit** para asseverar o comportamento pós-conserto (degrada, não levanta). O commit F0 segue como snapshot verde do crash no código antigo. F2 cobre os caminhos de raise que sobram (agente ausente/desativado + `except` genérico) e `hooks_config`/`routing_targets`.
- **Verificação:** `tests/test_coerce_json.py` → 14 passed. Regressões verdes: `test_agent_routing` (29), `test_dynamic_registry`, `test_hooks`, `test_routing_engine`, `test_model_factory`, `test_quick_replies_edge`, `test_audit`, e `pytest tests/endpoints` (34 passed — consumidores message/contact/tool/plugin de `coerce_json`).

---

### Fase F2 — Piso de emergência + coerção tolerante em `build_for_contact`
**Objetivo:** uma linha suja **degrada** para um `AgentSpec` default utilizável e loga ERROR — em vez de derrubar o serviço.

**Itens:**
- `[sequencial]` **Coerção tolerante do container** em [agent_factory.py:293](../agent/agent_factory.py#L293) e [:300](../agent/agent_factory.py#L300): trocar `dict(agent.get("model_config") or {})` por uma coerção que aceite **dict pronto OU string JSON** — reaproveitar `coerce_json` (agora N-camadas, F1) e, se o resultado ainda não for dict, cair em `{}`. Idem para `hooks_config`. Para `routing_targets`, coerção análoga com fallback `None`/`[]`. Assim valor sujo degrada em vez de estourar no próprio ponto.
- `[sequencial]` **Piso de emergência**: extrair um helper `_emergency_spec()` que monta um `AgentSpec(agent_key="default", base_prompt=DEFAULT_SYSTEM_PROMPT, model_config={"model": DEFAULT_MODEL}, tool_names=None)` a partir das constantes ([:36-40](../agent/agent_factory.py#L36-L40)). No `except Exception as e` ([:310-312](../agent/agent_factory.py#L310-L312)) e no caso "agente ausente/desativado" ([:263-266](../agent/agent_factory.py#L263-L266)): **em vez de** só `raise AgentResolutionError`, `logger.error(...)` (alto e claro, com `agent_key` e a exceção) e **retornar `_emergency_spec()`**. Levantar `AgentResolutionError` **somente** se nem o piso for construível (falha ao instanciar o próprio `AgentSpec` — praticamente impossível, mas mantém a porta de "banco genuinamente quebrado").
- `[sequencial]` ⚠️ Rever a docstring do módulo ([:7-11](../agent/agent_factory.py#L7-L11)) e da função ([:254-259](../agent/agent_factory.py#L254-L259)): já dizem "sempre resolve um AgentSpec / só levanta quando o banco está quebrado" — o comportamento novo alinha o código à docstring (que hoje mente). Ajustar o texto para refletir o piso de emergência.
- `[paralelo]` Confirmar que `agent_run_service` continua tratando `AgentResolutionError` como antes ([agent_run_service.py:290-291](../app/services/agent_run_service.py#L290-L291)) — o piso reduz a frequência desse caminho, não muda o contrato dele.

**Pronto quando:** com uma linha `default` duplo-codificada no banco de teste, `build_for_contact` **retorna um `AgentSpec` com `model="deepseek/deepseek-v4-pro"`** (não levanta); um `logger.error` é emitido. Manualmente: com a linha suja, enviar "Oi" no Sandbox → a IA **responde** (degradada) em vez de gravar o card de erro.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída (2026-07-06)
- **O que foi feito:** `agent/agent_factory.py`: (1) helper `_emergency_spec()` monta um `AgentSpec` default a partir das constantes seed (espelha o shape `model_config` com `_agent_key`/`_hooks_config`); (2) helper `_coerce_dict()` (reusa `coerce_json` F1) troca `dict(agent.get("model_config") or {})` em `:293`/`:300` por coerção tolerante (dict pronto OU string JSON → dict, senão `{}`), + coerção de `tool_names` para lista-ou-None; (3) branch "agente ausente/desativado" e `except Exception` genérico agora `logger.error` + **retornam `_emergency_spec()`** em vez de só `raise` — `AgentResolutionError` só se nem o piso montar; (4) docstrings do módulo, da função e da exceção realinhadas ao piso. `tests/test_agent_json_hardening.py` estendido (hooks_config/routing_targets duplo + piso via agente desativado com captura de `logger.error`).
- **Como foi feito / decisões:** `_coerce_dict` faz cópia (`dict(coerced)`) para nunca mutar o dict cacheado no `dynamic_registry`. Piso inclui `_agent_key`/`_hooks_config` porque o engine os lê (`agno_engine.py:468,527` via `.get()` — não obrigatórios, mas espelhar é mais seguro). Contrato de `AgentResolutionError` preservado: `agent_run_service` continua tratando-o como parada dura (só que a frequência cai — o piso cobre a maioria).
- **Problemas / pendências:** Corrigido no teste: o setter do agente vinculado é `conversation_repo.set_agent` (não `set_active_agent`). Nenhuma pendência.
- **Verificação:** `tests/test_agent_json_hardening.py` → 16 passed. Regressões verdes: `test_agent_routing` (29), `test_routing_engine`, `test_model_factory`, `test_hooks`; `pytest` de `test_spoke_router_enforcement` + `test_router_prompt_description` + `characterization/test_agent_turn_characterization` (20 passed).

---

### Fase F3 — Guarda em `save_agent_prompt`
**Objetivo:** salvar só o prompt (wizard) sobre uma linha suja **não** pode piorar para tripla codificação.

**Itens:**
- `[sequencial]` Em [ai_engine.py:118-134](../server/routes/ai_engine.py#L118-L134): antes de repassar ao `save()`, **normalizar** `existing.get("model_config")`, `existing.get("hooks_config")`, `existing.get("tool_names")`, `existing.get("routing_targets")` — coagir via `coerce_json` (N-camadas, F1) e garantir os tipos (`dict`/`dict`/`list|None`/`list|None`), caindo em `{}`/`{}`/`None`/`None` se destoar. Reaproveitar a mesma validação da rota `save_agent` ([:70-81](../server/routes/ai_engine.py#L70-L81)) — considerar extrair um helper compartilhado `_coerce_agent_json_fields(row)` para as duas rotas não divergirem.
- `[paralelo]` (defensivo, opcional) Espelhar a guarda `isinstance` dentro de `agent_repo.save`/`ensure` antes do `json.dumps` ([agent_repo.py:88,150-157](../db/repositories/agent_repo.py#L150-L157)): se o argumento chegar como `str`, coagir/`{}` em vez de `json.dumps` sobre string. Fecha o buraco para **qualquer** caller (inclusive plugins de terceiros não migrados).

**Pronto quando:** teste — gravar linha suja, chamar o endpoint `POST /api/ai/agents/default/prompt` com um prompt novo, reler a linha: `model_config` volta **single-encoded** (`{"model": …}`), não `"\"{...}\""`. Suíte verde.

#### Status de execução — Fase F3
**Estado:** ✅ Concluída (2026-07-06)
- **O que foi feito:** (a) `db/repositories/agent_repo.py`: helper `_dump_json_field(value, default)` (reusa `coerce_json`) substitui os `json.dumps` manuais em `ensure` e `save` — coage uma string crua ao objeto antes de serializar, então **nenhum caller** (inclui plugin de terceiro) gera dupla/tripla codificação; saída byte-idêntica no caminho feliz. (b) `server/routes/ai_engine.py`: helper compartilhado `_coerce_agent_json_fields(row)` (decisão P1-a) usado em `save_agent_prompt` para sanear `model_config`/`hooks_config`/`tool_names`/`routing_targets` da linha existente antes do `save()`. `tests/test_agent_json_hardening.py` estendido (guarda defensiva do repo com string crua + helper da rota + patch sobre linha suja → releitura single-encoded).
- **Como foi feito / decisões:** `save_agent` mantém a validação de borda (rejeita input de usuário inválido com 400 — contrato correto); o helper compartilhado sana a linha do DB no `save_agent_prompt` (que NÃO pode rejeitar, senão o wizard não consegue corrigir uma linha suja). A divergência que abriu o buraco (save_agent guardava, save_agent_prompt não) some. Nota: F1 já limpava `existing` (via `agent_repo.get`→`coerce_json`), então F3 é defesa em profundidade — a guarda no `_dump_json_field` é a última linha para callers que não passam por `get`.
- **Problemas / pendências:** `venv/bin/python tests/test_endpoints.py` falha em `create_kanban_view() got an unexpected keyword argument 'group_field_scope'` — **drift PRÉ-EXISTENTE e alheio ao Track A**: a função vive em `assets/plugin_examples/protocolos/logic.py` (plugin protocolos bundled, versão antiga vs. o `.zip` instalado que o teste espera). O crash ocorre na linha ~1583, **antes** de qualquer código deste plano (ai/agents PUT está em ~2225). **Reportar ao coordenador; não corrigir (fora do escopo).**
- **Verificação:** `tests/test_agent_json_hardening.py` → 25 passed. Regressões verdes: `test_agent_routing` (29), `test_dynamic_registry` (6), `test_model_factory` (24), `test_hooks` (32), `test_routing_engine` (26); `pytest tests/endpoints` (34). `test_endpoints` bloqueado no drift do protocolos (acima), não pela minha mudança.

---

### Fase F4 — Testes de regressão (inverte F0)
**Objetivo:** travar o comportamento novo para sempre.

**Itens:**
- `[sequencial]` Inverter o teste de F0: com `model_config` duplo-codificado na linha `default`, `build_for_contact` **retorna `AgentSpec` usável** (default), **não** levanta `AgentResolutionError`.
- `[paralelo]` Cobrir `hooks_config` e `routing_targets` duplo-codificados (mesma degradação).
- `[paralelo]` Cobrir o caminho end-to-end quando viável: um turno de IA com a linha suja **envia** resposta (degradada) em vez de emitir card de erro — reaproveitar mocks de GOWA/LLM de `tests/test_endpoints.py` / caracterizações em `tests/characterization/`.
- `[paralelo]` Regressão do caminho feliz: linha limpa continua resolvendo o modelo/prompt corretos (sem tocar em `test_model_factory.py` / `test_agent_routing.py` — só garantir que ficam verdes).

**Pronto quando:** `venv/bin/python -m pytest tests/ -q` verde no Postgres de teste; os novos testes cobrem duplo/triplo em `model_config`/`hooks_config`/`routing_targets`.

#### Status de execução — Fase F4
**Estado:** ✅ Concluída (2026-07-06)
- **O que foi feito:** A inversão de F0 e a cobertura de duplo/triplo em `model_config`/`hooks_config`/`routing_targets` + piso de emergência já vivem em `tests/test_agent_json_hardening.py` (25 checks, construídas em F1-F3). F4 adiciona o **e2e**: novo `tests/test_agent_json_hardening_e2e.py` (pytest, 2 testes) dirige um turno REAL do handler (AGNO stubado em `agno_engine.build_runner`, mesmo seam da caracterização — `build_for_contact` executa de verdade) com (a) `model_config` duplo-codificado e (b) agente default desativado, asseverando que a IA **envia a resposta** (`result.reply` + linha `assistant` persistida) e **nenhum card `role='error'`** é gravado.
- **Como foi feito / decisões:** Reaproveitei `_patch_agno_turn`/`_run_turn`/`_project_messages` de `tests/characterization/test_agent_turn_characterization.py` (o plano sugeria reaproveitar mocks de caracterização). Não usei `fake_agent_reply` porque ele stuba `aprocess_message` e pularia a resolução do agente — o ponto do teste. Card de erro confirmado como `role='error'` em `handler._emit_resolution_error` (:327).
- **Problemas / pendências:** **`pytest tests/ -q` inteiro NÃO é executável verde neste worktree** por dois motivos alheios ao Track A: (1) **design** — 8 scripts standalone (`test_agent_routing`, `test_endpoints`, `test_model_factory`, `test_hooks`, `test_routing_engine`, `test_dynamic_registry`, `test_quick_replies_edge`, `test_audit`) e os 2 novos (`test_agent_json_hardening`, `test_coerce_json`) têm `sys.exit()` no corpo → `SystemExit` aborta a coleção do pytest; rodam via `venv/bin/python tests/<f>.py`. (2) **pré-existente** — o plugin `protocolos` bundled está defasado (`create_kanban_view` sem `group_field_scope`) travando `test_endpoints`, e alguns testes de caracterização de lifecycle/subprocess penduram neste ambiente. Ambos independem desta lane. **Reportar (1) e (2) ao coordenador.**
- **Verificação:** e2e `test_agent_json_hardening_e2e.py` → 2 passed. `test_agent_json_hardening.py` → 25 passed; `test_coerce_json.py` → 14 passed. Regressões verdes: standalone `test_agent_routing` (29), `test_dynamic_registry` (6), `test_model_factory` (24), `test_hooks` (32), `test_routing_engine` (26); pytest `tests/endpoints` (34), `tests/test_postgres_roundtrip.py` (5), `characterization/{test_agent_turn,test_agno_reply_extraction,test_execution,test_webhook}` (todos verdes), `test_spoke_router_enforcement`+`test_router_prompt_description` (20). Caminho feliz intacto (nenhuma edição em `test_model_factory.py`/`test_agent_routing.py`).

---

### Fase F5 — (Track B, opcional) `ai_agents` TEXT→JSONB + repo passa nativo
**Objetivo:** eliminar a classe de dupla-codificação na origem para as 3 colunas que estouram duro.

**Itens:**
- `[sequencial]` Em [db/tables.py:575-583](../db/tables.py#L575-L583): trocar `Text` por `_json_type()` em `model_config`, `routing_targets`, `hooks_config` (e avaliar `tool_names`). Manter `server_default="{}"`/nullable como hoje.
- `[sequencial]` Migration Alembic (`alembic revision -m "ai_agents json columns to jsonb"`, revisar à mão — **sem** batch-mode, Postgres tem `ALTER TABLE` completo): `ALTER COLUMN model_config TYPE jsonb USING model_config::jsonb`, idem para as outras. ⚠️ **Higienização no USING**: uma linha eventualmente duplo-codificada viraria um JSONB **string** (não objeto). Como os bancos estão limpos hoje (D1), um `::jsonb` direto basta; para robustez, considerar um `USING` que detecte string e reaplique `->>0`/`::jsonb` (a confirmar — testar o SQL num dump antes). Downgrade: `ALTER … TYPE text USING model_config::text`.
- `[sequencial]` Em [agent_repo.py:88,150-157](../db/repositories/agent_repo.py#L150-L157): **remover o `json.dumps` manual** — passar `model_config`/`tool_names`/`routing_targets`/`hooks_config` como **dict/list nativos**; o SQLAlchemy (coluna JSONB) serializa 1×. Em `_row_to_dict` ([:37-42](../db/repositories/agent_repo.py#L37-L42)): com JSONB, o psycopg já devolve dict/list — `coerce_json` vira no-op (passa intacto), então pode **manter** (defensivo) ou simplificar. Manter é mais seguro.
- `[sequencial]` Rever consumidores que comparam valores no dedup de `save` ([agent_repo.py:131-142](../db/repositories/agent_repo.py#L131-L142)) — comparação de dict/list nativo continua correta (compara objetos, não texto).
- `[paralelo]` Atualizar `env.py`/drift-check do Alembic se houver checagem de metadados (o tipo muda; o autogenerate deve refletir).

**Pronto quando:** `alembic upgrade head` + `downgrade` round-trip num banco de teste; `pg_typeof(model_config)=jsonb`; suíte inteira verde; um `save`/`get` round-trip devolve dict idêntico; impossível gravar string crua (o SQLAlchemy serializa o dict).

#### Status de execução — Fase F5
**Estado:** ✅ Concluída (2026-07-06 · branch `feat/plano-34-f5`)
- **O que foi feito:** As 4 colunas JSON de `ai_agents` (`model_config`, `tool_names`, `routing_targets`, `hooks_config`) viraram **JSONB nativo** (`_json_type()` em [db/tables.py](../db/tables.py) — `JSON().with_variant(JSONB())`). Migration **0039** (`20260706_0039_ai_agents_jsonb.py`) faz `ALTER COLUMN … TYPE jsonb` com `USING` robusto + downgrade reversível. O `agent_repo` parou de `json.dumps` à mão: `_dump_json_field` virou `_native_json_field` (só `coerce_json` → devolve **dict/list nativo**; o SQLAlchemy serializa 1×). Leitura mantém `coerce_json` como no-op defensivo. Novo `tests/test_ai_agents_jsonb.py` (26 checks); `tests/test_agent_json_hardening.py` teve as 3 assertivas de formato bruto atualizadas de "TEXT single-encoded" para "objeto/array JSONB nativo".
- **Como foi feito / decisões:** (1) `USING` **robusto** (não só `::jsonb` direto): `NULL`/vazio → `'{}'::jsonb` (cols NOT NULL) ou `NULL` (nullable); valor eventualmente **duplo-codificado** → desembrulha 1 camada via `#>> '{}'` antes do cast; caso normal → `::jsonb`. Testado com dados sujos reais (downgrade→0038, semeia TEXT limpo/vazio/NULL/duplo, upgrade→0039). (2) Postgres-only: a conversão só roda no dialeto `postgresql` (guard), no-op em sqlite. (3) `tool_names` incluído (grep confirmou que nunca guarda o sentinel raw `"all"` — sempre array/NULL). (4) `ai_agents_history.snapshot` fica **TEXT** (blob versionado, fora de escopo — D4); com `values` agora nativo, `json.dumps(values)` do snapshot passa a embutir objetos aninhados (mais limpo) e o rollback de snapshots **antigos** (string) segue funcionando via `coerce_json`. (5) Dedup do `save` inalterado (já compara objetos nativos). **Track B fez só F5** (não F6): F6 tocaria `channels.config`/repos de canal, território da lane de canais (plano 33) rodando em paralelo — evitado de propósito.
- **Problemas / pendências:** Uma assertiva do `test_agent_json_hardening.py` (Track A, já mergeado) quebrou porque inspecionava a **representação TEXT crua** da coluna (`raw.lstrip().startswith("{")`) — inválido sob JSONB (a leitura devolve dict). Atualizada para `isinstance(dict/list)` preservando a intenção (nenhuma dupla-codificação persiste); a resiliência (build_for_contact degrada/recupera) permaneceu idêntica e verde. Nenhuma outra regressão.
- **Verificação:** Banco de teste dedicado `whatsbot_test_34f5` (UTF8/template0). `tests/test_ai_agents_jsonb.py` → 26 passed (pg_typeof=jsonb, jsonb_typeof=object/array, round-trip nativo, string crua normalizada, USING robusto com dados sujos, up/down round-trip). **Sem drift:** `tests/test_schema_drift.py` + `tests/test_alembic_hygiene.py` verdes (metadata JSONB ≡ DB jsonb; 0039 sem prefixo duplicado). Regressões verdes: `test_agent_json_hardening` (25), `test_agent_json_hardening_e2e`, `test_coerce_json` (14), `test_agent_routing` (29), `test_model_factory` (24), `test_dynamic_registry` (6), `test_hooks` (32), `test_routing_engine` (26), `test_postgres_roundtrip`, `test_router_prompt_description`, `test_routing_motivo`, `test_spoke_router_enforcement`, `test_builtin_tool_delete`, `test_improve_conversation_scope`, `tests/endpoints`, e caracterização `agent_turn`/`execution`.

---

### Fase F6 — (Track B, opcional, adiável) demais colunas TEXT→JSONB
**Objetivo:** uniformizar o resto das colunas JSON-em-texto de baixo risco (uma por vez, sem pressa).

**Itens:**
- `[paralelo, por coluna]` Candidatas (D4 — degradam suave hoje, então baixa urgência): `channels.config`, `executions.routing_steps`, `execution_steps.data`, `messages.reactions`, `ai_variables.value` (⚠️ `value` é **polimórfica** — pode ser string/número/bool/dict; JSONB aceita todos, mas revisar consumidores). Cada uma: `Text`→`_json_type()` + migration `::jsonb` + parar de `json.dumps` no repo correspondente ([config_repo.py:42,56](../db/repositories/config_repo.py#L42), [execution_repo.py:31,90](../db/repositories/execution_repo.py#L31), [message_repo.py:370](../db/repositories/message_repo.py#L370), etc.).
- `[sequencial]` **NÃO** migrar `channel_credentials.value` (P15 — mascarado no edge, tratado como TEXT opaco) nem os `*_history.snapshot` (blob versionado, comparado como texto) sem análise dedicada — ficam de fora por ora.

**Pronto quando:** cada coluna migrada tem round-trip verde e a suíte segue verde; nada além do tipo muda de comportamento observável.

#### Status de execução — Fase F6
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

## 4. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| `coerce_json` N-camadas (F1) | Desembrulhar demais um valor que **deveria** ser uma string JSON de conteúdo | Grep dos call sites antes; nenhum consumidor atual espera string final. Laço **limitado** (~5) + warning. Se algum call site precisar de string, tratar por default próprio. |
| Piso de emergência (F2) | Mascarar corrupção silenciosamente ("por que a IA respondeu genérico?") | **Sempre** `logger.error` alto e claro com `agent_key`; considerar métrica/alerta futuro. O piso é "degradado", não "silencioso". |
| Piso engolir erro legítimo | Um bug real de resolução passar como "default" e não ser notado | Levantar `AgentResolutionError` ainda existe para o caso "nem o piso monta"; o log ERROR dá o rastro. |
| Ordem de migration (F5) | `ALTER TYPE ... USING` falhar se houver linha duplo-codificada viva | D1: bancos limpos hoje. Ainda assim, **testar o SQL num dump** antes; `USING` defensivo que trate string. Rodar `POST /api/admin/repair-sequences` não é afetado. |
| Postgres-only / PgBouncer | `ALTER TABLE` sob PgBouncer transaction-mode | Migration roda no boot via `init_db` (`alembic upgrade head`), fora do pool de request; sem `prepare` problemático. |
| `ai_variables.value` polimórfica (F6) | JSONB muda a forma como número/bool voltam | Por isso F6 é adiável e por-coluna; revisar consumidores de `value` antes. |
| Segredos | `channel_credentials.value` migrar por engano | Explicitamente **excluída** (F6): TEXT opaco mascarado no edge. |
| Compat single-channel | Fallback do card fantasma / conversation_id | Fora do escopo (é o Achado D.A, outro plano); este plano não toca em `message_repo.add(conversation_id=)`. |
| Plugins | Regressão em plugin de terceiro que use `agent_repo` | F3 defensivo em `agent_repo.save`/`ensure` protege qualquer caller; nada em `storages/plugins/` é tocado. |

---

## 5. Perguntas em aberto

- **P1** — Extrair um helper compartilhado de coerção/validação de campos JSON do agente para `save_agent` **e** `save_agent_prompt` (F3), ou duplicar a guarda? · ✅ **DECIDIDO (2026-07-06):** extrair `_coerce_agent_json_fields` — as duas rotas divergirem foi justamente o que abriu o buraco. (a) helper compartilhado **[recomendado]**; (b) duplicar. Recomendação: (a).
- **P2** — `coerce_json` que sobra `str` deve retornar `default` **sempre** ou preservar a string em call sites que a esperem? · ✅ **DECIDIDO (2026-07-06):** retornar `default` + `warning`; nenhum consumidor atual espera string final (confirmar no grep de F1). Reabrir só se o grep achar exceção.
- **P3** — Track B (F5/F6) entra **neste** ciclo ou vira plano separado? · ✅ **DECIDIDO (2026-07-06):** **F5 executado** (branch `feat/plano-34-f5`) depois que o plano 32 (lane de canais) mergeou e liberou `db/tables.py`/Alembic. **F6 NÃO** — tocaria `channels.config`/repos de canal, território da lane de canais (plano 33) rodando em paralelo; fica adiável/opcional (D4). Track A já resolvia o apagão sozinho; F5 fechou a *classe* do bug na origem (colunas `ai_agents` viram JSONB nativo).
- **P4** — Vale um endpoint/admin "detectar linhas JSON duplo-codificadas" (diagnóstico read-only) para varredura periódica? · ⏸️ **ADIADO:** fora do escopo; o repro read-only do QA (§2.5) já serve para checagem manual. Reavaliar se a corrupção reaparecer.

---

## 6. Checklist de verificação

- [~] `venv/bin/python -m pytest tests/ -q` **verde no Postgres de teste** (`WHATSBOT_TEST_DB_URL`) após cada fase. → **Parcial por motivos alheios ao Track A** (ver Status F4): a suíte inteira não coleta no pytest (8+2 scripts standalone com `sys.exit`) e há drift pré-existente do plugin `protocolos` + travas de lifecycle/subprocess. Verificado via subconjuntos relevantes (todos verdes) + scripts standalone rodados individualmente.
- [x] F0 captura o crash atual (levanta) **antes** de qualquer mudança de produção (commit F0); F4 o inverte (não levanta, responde) — `test_agent_json_hardening.py` + e2e.
- [x] `coerce_json`: duplo/triplo → objeto; string-que-sobra → `default` + warning; dict pronto → intacto (`test_coerce_json.py`, 14 checks).
- [x] Com linha `default` duplo-codificada no banco de teste: `build_for_contact` **degrada** (não `AgentResolutionError`) — recupera o modelo real ou cai no default; agente desativado → piso de emergência com `logger.error`.
- [x] Manual (Sandbox) coberto por **e2e automatizado**: `test_agent_json_hardening_e2e.py` dirige um turno real com a linha suja → **IA responde** (degradada), sem card `role='error'`.
- [x] `save_agent_prompt` sobre linha suja → releitura volta **single-encoded** (sem tripla codificação) — guarda em `agent_repo._dump_json_field` + helper de rota (F3).
- [x] Caminho feliz intacto: linha limpa resolve modelo/prompt corretos; `test_model_factory.py` / `test_agent_routing.py` verdes sem edição.
- [x] **(F5, executada — branch `feat/plano-34-f5`)** migration 0039 `upgrade`/`downgrade` round-trip verde; `pg_typeof=jsonb` nas 4 colunas; `USING` robusto testado com dados sujos; `test_ai_agents_jsonb.py` (26) + `test_schema_drift`/`test_alembic_hygiene` verdes. **F6 NÃO** (adiável — território da lane de canais).
- [x] Sem segredo em URL/log; `channel_credentials.value` e `*_history.snapshot` **não** migrados (Track B fora de escopo aqui).
- [x] Um refactor por commit; cada fase com seu bloco "Status de execução" preenchido.

---

## 7. Apêndice — arquivos-chave

**Backend — resolução do agente (Track A):**
- [agent/agent_factory.py](../agent/agent_factory.py) — `build_for_contact` (:253-312), ponto do crash (:293,:300), `except` (:308-312), constantes seed (:36-40), `AgentSpec` (:63-73). **F2.**
- [db/repositories/_mapping.py](../db/repositories/_mapping.py) — `coerce_json` (:24-40). **F1.**
- [server/routes/ai_engine.py](../server/routes/ai_engine.py) — `save_agent` guard (:70-81), `save_agent_prompt` sem guard (:118-134). **F3.**
- [db/repositories/agent_repo.py](../db/repositories/agent_repo.py) — `_row_to_dict` (:37-42), `ensure` (:88-89), `save` (:150-157), dedup (:131-142). **F3 defensivo / F5.**
- [app/services/agent_run_service.py](../app/services/agent_run_service.py) — tratamento de `AgentResolutionError` (:174-184,:286-291). **Contrato — não muda.**

**DB / migrations (Track B):**
- [db/tables.py](../db/tables.py) — `_json_type()` (:38-49), colunas `ai_agents` (:575-583). **F5/F6.**
- [db/alembic/versions/](../db/alembic/versions/) — nova revision `ALTER … TYPE jsonb`. **F5/F6.**
- Repos com `json.dumps` manual (F6): [config_repo.py](../db/repositories/config_repo.py), [execution_repo.py](../db/repositories/execution_repo.py), [message_repo.py](../db/repositories/message_repo.py).

**Testes:**
- Novo `tests/test_agent_json_hardening.py` (ou dentro de `tests/test_model_factory.py`). **F0/F1/F4.**
- Reaproveitar mocks de `tests/test_endpoints.py` e `tests/characterization/`. **F4.**
