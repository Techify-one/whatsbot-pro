# Plano 41 — Desacoplar o fechamento do `vendas_ia` e mover os campos de conversa para o plugin `protocolos` (com API de escrita para tools da IA)

> **Status:** ✅ IMPLEMENTADO (2026-07-09) · **Data:** 2026-07-09 · **Escopo:** médio
> **Origem:** pedido do usuário (prints: 7 atributos personalizados nativos criados pelo `vendas_ia` no lugar errado; discussão de design nesta sessão). **Método:** leitura do código real + `grep`/`sed` (`arquivo:linha` verificados abaixo).
> O plugin `vendas_ia` cria, no boot, 7 atributos personalizados **nativos** (via `state.ensure_attribute_defs` no evento `app.startup`) que na verdade pertencem ao domínio do plugin `protocolos` — que já tem o próprio field-builder e fluxo de resolução. Isso gera dois pipelines de fechamento concorrentes. Este plano (A) **enxuga o `vendas_ia`** removendo a criação desses atributos e aposentando a camada de fechamento (agente + 4 tools + `atendimento.py`, mantidos DESLIGADOS por enquanto), (B) dá ao `protocolos` uma **API de escrita por telefone** para que uma tool da IA consiga setar campos (`codigo_oferta`/`curso_de_interesse`) no atendimento aberto do contato, e (C) entrega **documentação + script externo** para o usuário criar os `field_defs` e a `ai_tool` na mão (sem seed).
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|-----------------------|
| D1 | ✅ (2026-07-09) Os campos de disposição/fechamento **NÃO** devem ser criados como atributos nativos pelo `vendas_ia`. | Fase A remove `DISPOSITION_DEFS` + a chamada `ensure_attribute_defs` do `app.startup` e do `/seed`. |
| D2 | ✅ (2026-07-09) A camada de fechamento do `vendas_ia` (agente `fechamento` + 4 tools + `atendimento.py`) é **aposentada e fica DESLIGADA por enquanto**. A IA **não** fecha protocolo/atendimento — isso é humano, no popup do `protocolos`. | Fase A: `agents_seed`/`tools_seed` deixam de semear fechamento; `executar_fechamento`/`finalizar_atendimento`/`buscar_dados_protocolo`/`buscar_opcoes_atributo_personalizado` saem do fluxo. |
| D3 | ✅ (2026-07-09) **Todos** os campos de conversa pertencem ao `protocolos`. O `protocolos` deve expor uma **API programática** (código alcançável + endpoint) para uma tool da IA **setar** atributos nele. | Fase B: nova função pública em `logic.py` (resolve por phone → protocolo aberto → grava validado) + endpoint REST. |
| D4 | ✅ (2026-07-09) Escopo **agora**: apenas `codigo_oferta` e `curso_de_interesse` (campos comerciais que a IA preenche). O resto (disposição de fechamento) fica para o futuro. | Fase C documenta só esses 2. A API (Fase B) é genérica (serve qualquer campo). |
| D5 | ✅ (2026-07-09) **SEM SEED**: o usuário cria os `field_defs` na mão (tela de config do `protocolos`) e cria a `ai_tool` na mão. Ele quer **script externo OU documentação** para isso. | Fase C entrega doc + script `scripts/` opcional; nenhum código de seed no plugin. |
| D6 | ✅ (2026-07-09) Princípio fixo: plugin sem produção estável a proteger ⇒ **substituir/remover**, não empilhar stopgap. Atributos nativos JÁ criados **não** são apagados automaticamente (dado do usuário) — a remoção é **manual/documentada**. | Fase A não roda migration destrutiva; documenta como limpar as defs órfãs pela tela de Atributos. |
| D7 | ✅ (2026-07-09) A opção (c) — a futura tool de fechamento escrever **via a API do `protocolos`**, não direto em `custom_attributes` — é o alvo de arquitetura, mas **futuro**. Agora só se constrói o **canal de escrita**. | Fase B entrega o canal; o fechamento por IA não é reativado neste plano. |

---

## 1. Resumo executivo

O `vendas_ia` foi portado do Nexus quando ainda não existia um plugin de atendimento/protocolo; por isso ele reproduziu os campos de fechamento do Nexus como **atributos personalizados nativos** e trouxe um agente + tools de fechamento. Hoje o plugin `protocolos` **já é** esse plugin de atendimento (field-builder próprio, popup de resolução, espelho em `custom_attributes`). O resultado é duplicação: 7 atributos nativos criados no boot pelo `vendas_ia` e um segundo pipeline de fechamento por IA que grava por fora do `protocolos`.

A solução tem três frentes independentes na origem, com uma barreira: **(A)** enxugar o `vendas_ia` — parar de criar os atributos de disposição e aposentar (desligar) a camada de fechamento, mantendo intactos triagem por palavra-chave, fixação de oferta, "OFERTA EM FOCO" e busca no Nexus; **(B)** dar ao `protocolos` uma função pública + endpoint que resolvem o **atendimento aberto do contato pelo telefone** e gravam campos validados (via `normalize_values`/`upsert_extra`), para uma `ai_tool` conseguir escrever `codigo_oferta`/`curso_de_interesse`; **(C)** documentar (e opcionalmente um script) para o usuário criar os `field_defs` e a `ai_tool` na mão. Ponto de atenção central (P1): `codigo_oferta` hoje alimenta dois fallbacks de "oferta em foco" — a solução preserva a fixação de oferta mesmo com o campo migrando para o `protocolos`.

---

## 2. Como funciona hoje (mapa)

### 2.1 `vendas_ia` cria os atributos nativos (o problema)

| Etapa | Local (`arquivo:linha`) | O que acontece |
|-------|-------------------------|----------------|
| Defs de disposição | [storages/plugins/vendas_ia/state.py:36-56](../storages/plugins/vendas_ia/state.py#L36-L56) | `DISPOSITION_DEFS` = 7 campos: `curso_de_interesse`, `codigo_oferta`, `motivo_de_abertura`, `status_de_fechamento`, `status_conversa`, `tipo_de_atendimento`, `observacao`. |
| Registro no boot | [storages/plugins/vendas_ia/state.py:59-72](../storages/plugins/vendas_ia/state.py#L59-L72) | `ensure_attribute_defs()` cria `oferta_atual`/`perfil_cliente` (sistema) **e** os 7 de disposição via `_ensure_editable` → `custom_attribute_repo.create_definition(is_system=0)`. |
| Gatilho `app.startup` | [storages/plugins/vendas_ia/events.py:68-77](../storages/plugins/vendas_ia/events.py#L68-L77) | `on_startup` → `state.ensure_attribute_defs()`. Roda **toda vez** que o servidor sobe → os campos "reaparecem" na tela nativa. |
| Gatilho `/seed` | [storages/plugins/vendas_ia/routes.py:73-79](../storages/plugins/vendas_ia/routes.py#L73-L79) | O endpoint de seed também chama `state.ensure_attribute_defs` antes de semear agentes/tools. |

### 2.2 Camada de fechamento do `vendas_ia` (a aposentar)

| Peça | Local (`arquivo:linha`) | Papel |
|------|-------------------------|-------|
| Módulo genérico | [storages/plugins/vendas_ia/atendimento.py:1-14](../storages/plugins/vendas_ia/atendimento.py#L1-L14) | Docstring declara-se "placeholder **até** o plugin de atendimento/protocolo" — grava disposição em `conversations.custom_attributes` e faz `set_status("closed")` por fora do `protocolos`. |
| Tools de fechamento (código no DB) | [storages/plugins/vendas_ia/tool_code/](../storages/plugins/vendas_ia/tool_code/) | `executar_fechamento.py`, `finalizar_atendimento.py`, `buscar_dados_protocolo.py`, `buscar_opcoes_atributo_personalizado.py`. Importam `vendas_ia.atendimento` em subprocesso. |
| Seed das tools | [storages/plugins/vendas_ia/tools_seed.py:25-38](../storages/plugins/vendas_ia/tools_seed.py#L25-L38) | `TOOLS` mistura as 3 de **busca** (Nexus, manter) com as 4 de **fechamento** (remover). |
| Agente `fechamento` | [storages/plugins/vendas_ia/agents_seed.py:38-71](../storages/plugins/vendas_ia/agents_seed.py#L38-L71) | `SPOKES` inclui `fechamento` (`FECHAMENTO_TOOLS`/`FECHAMENTO_HOOKS`); `ROUTER.routing_targets` inclui `"fechamento"`. |
| Prompts que citam fechamento | [storages/plugins/vendas_ia/seed_prompts/fechamento.md](../storages/plugins/vendas_ia/seed_prompts/fechamento.md), [.../comercial.md:247](../storages/plugins/vendas_ia/seed_prompts/comercial.md#L247), [.../roteador.md](../storages/plugins/vendas_ia/seed_prompts/roteador.md) | Referências textuais às tools de fechamento e ao `set_custom_attribute(codigo_oferta)`. |

### 2.3 O que MANTER no `vendas_ia` (runtime, não mexer)

| Peça | Local | Papel |
|------|-------|-------|
| Triagem palavra-chave→comercial | [storages/plugins/vendas_ia/filters.py:32-127](../storages/plugins/vendas_ia/filters.py#L32-L127) | `filter.agent.resolve` (plano 39): fixa oferta + força comercial no mesmo turno. |
| Fixar oferta quando IA grava `codigo_oferta` | [storages/plugins/vendas_ia/events.py:27-65](../storages/plugins/vendas_ia/events.py#L27-L65) | `on_tool_after` (`tool.after`): observa `set_custom_attribute` com `codigo_oferta` → `state.set_offer`. ⚠️ **acoplado a `codigo_oferta` em `custom_attributes`** — ver P1. |
| "OFERTA EM FOCO" no prompt | [storages/plugins/vendas_ia/prompts.py:24-46](../storages/plugins/vendas_ia/prompts.py#L24-L46) | `_resolve_offercode`: tabela do plugin → **fallback** em `custom_attributes` (`oferta_atual` OU `codigo_oferta`). ⚠️ ver P1. |
| Estado + espelho `oferta_atual` | [storages/plugins/vendas_ia/state.py:105-181](../storages/plugins/vendas_ia/state.py#L105-L181) | `plugin_vendas_ia_conversa` (fonte da verdade) + espelho `oferta_atual` em `custom_attributes`. |
| Busca Nexus + cache | [storages/plugins/vendas_ia/nexus_db.py](../storages/plugins/vendas_ia/nexus_db.py), [.../search.py](../storages/plugins/vendas_ia/search.py) | 3 tools de busca híbrida. |

### 2.4 API de escrita que o `protocolos` JÁ tem (base da Fase B)

| Função/endpoint | Local (`arquivo:linha`) | Cobre? |
|-----------------|-------------------------|--------|
| `update_protocolo_fields(atid, values, ...)` | [storages/plugins/protocolos/logic.py:755-793](../storages/plugins/protocolos/logic.py#L755-L793) | ✅ grava **todos os tipos** no scope `protocolo` (merge + `normalize_values` + `upsert_extra`). **Recebe `atid`, não phone.** |
| `set_protocolo_field(atid, scope, key, value)` | [storages/plugins/protocolos/logic.py:1643-1673](../storages/plugins/protocolos/logic.py#L1643-L1673) | ⚠️ **só campos de opção** (`_option_field_def`) — NÃO serve `codigo_oferta`/`curso_de_interesse` (texto). |
| `get_open_protocolo_for_contact(contact_id)` | [storages/plugins/protocolos/logic.py:584-586](../storages/plugins/protocolos/logic.py#L584-L586) | ✅ resolve o protocolo aberto do contato (por `contact_id`). |
| `get_field_defs(scope)` / `normalize_values(scope, values)` | [storages/plugins/protocolos/logic.py:206](../storages/plugins/protocolos/logic.py#L206), [:374](../storages/plugins/protocolos/logic.py#L374) | ✅ defs + validação por tipo/regex/opções. |
| REST `PUT /protocolos/{atid}/fields` · `POST /protocolos/{atid}/set-field` | [storages/plugins/protocolos/routes.py:140](../storages/plugins/protocolos/routes.py#L140), [:190](../storages/plugins/protocolos/routes.py#L190) | ⚠️ recebem `atid` e exigem `plugin_permission("edit")` (usuário logado) — **não** servem uma tool que só tem `ctx.phone`. |

**Gap da Fase B:** falta um resolvedor **por telefone** + função pública fina que grava campos de **texto** (não só opção) validados, retornando o protocolo re-hidratado — o análogo de `update_protocolo_fields` mas com entrada `phone` + `scope` arbitrário.

### 2.5 Padrão de `ai_tool` code (base da Fase C)

`tool_code/*.py` roda em **subprocesso isolado**, faz `_bootstrap()` (injeta `storages/plugins` no `sys.path` + inicializa o engine) e `from vendas_ia import atendimento` — ver [storages/plugins/vendas_ia/tool_code/executar_fechamento.py:44-72](../storages/plugins/vendas_ia/tool_code/executar_fechamento.py#L44-L72). A tool nova do usuário fará `from protocolos import logic` e chamará a função da Fase B. Só `ctx.phone` está disponível (o subprocesso não recebe `conversation_id`).

### 2.6 Falsos positivos descartados

| Candidato | Por que NÃO é problema |
|-----------|------------------------|
| `oferta_atual` / `perfil_cliente` (defs de sistema em `state._ensure`) | São atributos do **domínio do `vendas_ia`** (oferta em foco + perfil comercial), não de fechamento. **Mantidos.** D4 restringe a mudança a `codigo_oferta`/`curso_de_interesse`. |
| `set_protocolo_field` (só opção) | Não é bug — é do drag-and-drop do kanban por campo de opção. Não vamos reusar para texto; a Fase B cria um caminho próprio. Fica intacto. |
| Espelho `mirror_atendimento_to_core` do `protocolos` | Continua válido (grava valores no `custom_attributes` **ao resolver**, `is_system=1`, read-only). Não conflita com a Fase B. |
| `custom_attribute_repo.create_definition` (core) | A API nativa de atributos está correta; o problema é **quem** a chama (o `vendas_ia`), não a API. Core não muda. |
| Migration destrutiva para apagar as defs órfãs | Descartado por D6 (dado do usuário). Remoção é manual/documentada. |

---

## 3. Fases / Roadmap

```
WAVE 0   A0 · B1 · C1(rascunho)          ← 3 workstreams independentes na origem
            │
            │  (barreira: A0 decide P1; B1 entrega a API que C1 documenta)
            ▼
WAVE 1   A1 → A2 · B2 · C2               ← A2 depende de A1; B2 e C2 dependem das WAVE 0
            │
            ▼
WAVE 2   V (verificação integrada)       ← sozinha, depois de tudo verde
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando / Nota |
|------|------|-----------|-------|-------|----------------------|
| 0 | **A0** | Decisão P1 (oferta-em-foco) | 🔴 | médio | P1 resolvido e registrado — bloqueia A1. |
| 0 | **B1** | `protocolos`: função pública `set_fields_for_contact` | 🟢 | médio | função existe + testada isolada. |
| 0 | **C1** | Doc: rascunho do guia (campos + tool) | 🟢 | baixo | esqueleto do `.md` criado. |
| 1 | **A1** | `vendas_ia`: remover defs de disposição | 🔴 | baixo | [depende de: A0] boot não recria os 7. |
| 1 | **A2** | `vendas_ia`: aposentar fechamento (agente+tools) | 🟢 | médio | [depende de: A1] seed não traz fechamento. |
| 1 | **B2** | `protocolos`: endpoint REST opcional | 🟢 | baixo | [depende de: B1] `POST .../contacts-by-phone/set-fields`. |
| 1 | **C2** | Doc final + script externo | 🟢 | baixo | [depende de: B1] guia + `scripts/*.py`. |
| 2 | **V** | Verificação integrada | 🔴 | — | suíte verde + validação manual. |

Disciplina do repo a seguir: **verde a cada fase**; **um refactor por commit**; nunca avançar com teste vermelho não-explicado. Como o grosso é dentro de plugins (`storages/plugins/`), lembre que o worktree limpo pode não ter os plugins instalados — validar contra a instalação real (ver [Plugin changes via zip] na memória).

---

### Fase A0 — Resolver P1: como preservar "oferta em foco" ao migrar `codigo_oferta`

**Objetivo:** decidir e registrar como a fixação de oferta continua funcionando quando `codigo_oferta` deixa de ser um atributo nativo escrito por `set_custom_attribute` e passa a ser um `field_def` do `protocolos`.

**Itens:**
1. [sequencial] Revisar os dois acoplamentos: `events.on_tool_after` ([events.py:34](../storages/plugins/vendas_ia/events.py#L34), filtra `tool_name == "set_custom_attribute"`) e `prompts._resolve_offercode` fallback ([prompts.py:33-36](../storages/plugins/vendas_ia/prompts.py#L33-L36), lê `codigo_oferta` de `custom_attributes`).
2. [sequencial] Escolher a estratégia (ver P1 em §5). **Recomendada:** a `ai_tool` nova (Fase C) que grava no `protocolos` **também** chama `state.set_offer(...)` do `vendas_ia` (fonte primária do foco), e `on_tool_after` passa a observar **o nome da tool nova** (além de/em vez de `set_custom_attribute`). Assim o foco continua vindo de `plugin_vendas_ia_conversa` (fonte primária) e o fallback em `custom_attributes` deixa de ser necessário.
3. [sequencial] Registrar a decisão no bloco de status de execução desta fase.

**Pronto quando:** P1 está com `✅ DECIDIDO` e A1/A2/C2 sabem exatamente o que ajustar em `events.py`/`prompts.py`/`comercial.md`.

#### Status de execução — Fase A0
**Estado:** ✅ Concluída
- **O que foi feito:** P1 resolvido opção (a): a ai_tool comercial grava no protocolos E o vendas_ia fixa a oferta via `events.on_tool_after` (observa a tool `set_atributos_comerciais` pelo nome, config `commercial_tool_name`), fonte primária `plugin_vendas_ia_conversa`; fallback `codigo_oferta` removido do prompt.
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase A1 — `vendas_ia`: parar de criar os atributos de disposição

**Objetivo:** o boot e o `/seed` não criam mais os 7 campos de disposição como atributos nativos; `oferta_atual`/`perfil_cliente` (sistema) permanecem.

**Itens:**
1. [sequencial] Em [state.py:36-56](../storages/plugins/vendas_ia/state.py#L36-L56): remover `DISPOSITION_DEFS` (ou esvaziar) e o laço em `ensure_attribute_defs` que chama `_ensure_editable` ([state.py:71-72](../storages/plugins/vendas_ia/state.py#L71-L72)). Manter `_ensure(OFFER_ATTR_KEY)` e `_ensure(PROFILE_ATTR_KEY)`.
2. [sequencial] Avaliar `_ensure_editable` ([state.py:75-88](../storages/plugins/vendas_ia/state.py#L75-L88)) → remover se ficar sem uso.
3. [sequencial] Conforme P1 (A0): ajustar `prompts._resolve_offercode` — se a decisão for depender só de `state.get_state`/`oferta_atual`, retirar o fallback em `CODE_ATTR_KEY` ([prompts.py:37-38](../storages/plugins/vendas_ia/prompts.py#L37-L38)); senão, manter e documentar.
4. [sequencial] `on_startup` ([events.py:68-72](../storages/plugins/vendas_ia/events.py#L68-L72)) passa a garantir só `oferta_atual`/`perfil_cliente` (herda de A1.1 sem mudança de assinatura).

**Pronto quando:** reiniciar o servidor com o `vendas_ia` ativo **não** recria `codigo_oferta`/`curso_de_interesse`/`motivo_de_abertura`/`status_de_fechamento`/`status_conversa`/`tipo_de_atendimento`/`observacao` na tela **Atributos Personalizados**; `oferta_atual`/`perfil_cliente` continuam presentes; a triagem por palavra-chave e o bloco "OFERTA EM FOCO" continuam funcionando (mensagem com keyword ainda cai no comercial com a oferta injetada).

#### Status de execução — Fase A1
**Estado:** ✅ Concluída
- **O que foi feito:** Removido `DISPOSITION_DEFS` + `_ensure_editable` de state.py (mantidos `oferta_atual`/`perfil_cliente`); `_resolve_offercode` em prompts.py caiu para `state.get_state` → espelho `oferta_atual` (sem fallback `codigo_oferta`).
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase A2 — `vendas_ia`: aposentar (desligar) a camada de fechamento

**Objetivo:** o agente `fechamento` e as 4 tools de fechamento deixam de ser semeados e de participar do fluxo; `atendimento.py` fica órfão (aposentado, não mais referenciado).

**Itens:**
1. [paralelo] `tools_seed.TOOLS` ([tools_seed.py:27-38](../storages/plugins/vendas_ia/tools_seed.py#L27-L38)): remover as 4 linhas de fechamento (`buscar_dados_protocolo`, `buscar_opcoes_atributo_personalizado`, `executar_fechamento`, `finalizar_atendimento`), mantendo as 3 de busca.
2. [paralelo] `agents_seed`: remover o spoke `fechamento` de `SPOKES` ([agents_seed.py:62-70](../storages/plugins/vendas_ia/agents_seed.py#L62-L70)), tirar `"fechamento"` de `ROUTER.routing_targets` ([agents_seed.py:82](../storages/plugins/vendas_ia/agents_seed.py#L82)) e remover `FECHAMENTO_TOOLS`/`FECHAMENTO_HOOKS` ([agents_seed.py:35-40](../storages/plugins/vendas_ia/agents_seed.py#L35-L40)).
3. [paralelo] `atendimento.py`: aposentar — remover o arquivo **ou** marcar como deprecated e garantir que nada o importa (os `tool_code/*.py` de fechamento que o importavam são removidos junto). Decidir em P2.
4. [paralelo] `tool_code/`: remover os 4 arquivos de fechamento (ou movê-los para um `tool_code/_deprecated/`). Ajustar `_CODE_DIR`/`_read_code` se necessário ([tools_seed.py:41-42](../storages/plugins/vendas_ia/tools_seed.py#L41-L42)).
5. [paralelo] `routes.status._AGENT_KEYS` e o painel de diagnóstico: remover `fechamento` da lista de agentes esperados (senão o diagnóstico mostra "faltando"). Verificar `_AGENT_KEYS` (grep em `routes.py`).
6. [paralelo] Prompts seed: `comercial.md`/`roteador.md` — remover as referências textuais às tools de fechamento e ao `set_custom_attribute(codigo_oferta)` conforme P1; `fechamento.md` fica sem uso (remover ou manter como referência histórica — P2). ⚠️ Prompts já semeados no DB **não** mudam com a edição do `.md` (o seed é não-destrutivo); documentar que o usuário reedita o prompt do comercial na tela Agentes se quiser.
7. [paralelo] `state.ensure_attribute_defs` já não cria disposição (A1); confirmar que nenhum resíduo de fechamento sobrou em `_config`/`settings`.

**Pronto quando:** um `/seed` novo (instalação limpa de teste) cria **só** `comercial`/`suporte`/`roteador` e **3** `ai_tools` de busca (sem fechamento); a tela de diagnóstico do `vendas_ia` não acusa agente/tool de fechamento faltando; nenhum import de `vendas_ia.atendimento` permanece no código ativo (`grep -rn "import atendimento\|from vendas_ia import atendimento" storages/plugins/vendas_ia` → só resíduo aposentado, se houver).

#### Status de execução — Fase A2
**Estado:** ✅ Concluída
- **O que foi feito:** tools_seed: só as 3 tools de busca; agents_seed: removido spoke `fechamento`/`FECHAMENTO_*` e o target do roteador; routes: `_AGENT_KEYS` sem fechamento + comentário do /seed; removidos atendimento.py, 4 tool_code de fechamento e seed_prompts/fechamento.md; comercial.md/roteador.md atualizados (nova tool comercial, sem fechamento).
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase B1 — `protocolos`: função pública de escrita por telefone

**Objetivo:** uma função pública em `logic.py` que uma tool da IA (só `ctx.phone`) usa para gravar campos validados no atendimento aberto do contato.

**Itens:**
1. [sequencial] Criar `set_fields_for_contact(phone: str, values: dict, scope: str = "protocolo") -> dict` em [storages/plugins/protocolos/logic.py](../storages/plugins/protocolos/logic.py). Fluxo (reusando o que existe): `contact_repo.get_by_phone(phone)` → `get_open_protocolo_for_contact(contact["id"])` (ou `ensure_protocolo_for_contact` se a política for criar quando não há — decidir em P3) → filtrar `values` pelas chaves definidas em `get_field_defs(scope)` (rejeitar chave desconhecida) → `normalize_values(scope, ...)` (validação por tipo/regex/opções) → gravar via o mesmo caminho de `update_protocolo_fields` (`upsert_extra`, roteando protocolo vs último ciclo de atendimento conforme o `scope`) → retornar dict de resultado `{ok, protocolo_id, gravado, erros}`.
2. [sequencial] Cobrir **campos de texto** (o gap do `set_protocolo_field`): não usar `_option_field_def`; validar via `normalize_values` do scope inteiro (que já cobre texto/área/número/data/opção).
3. [sequencial] Best-effort e defensivo (nunca levanta para o subprocesso da tool): retorna `{ok: False, erro: ...}` em vez de exceção quando não há contato/protocolo aberto.
4. [sequencial] Decidir o **scope padrão** dos campos comerciais (`codigo_oferta`/`curso_de_interesse`): `protocolo` (vive no protocolo, 1 por contato) vs `atendimento` (por ciclo). Ver P4 — recomendação `protocolo` (persiste pela vida do protocolo, alinhado a "oferta/curso do lead").

**Pronto quando:** chamando `logic.set_fields_for_contact(phone, {"codigo_oferta": "X"})` (com o `field_def` criado à mão no scope escolhido) grava e re-hidrata o protocolo; chave inexistente é rejeitada com erro claro; contato/protocolo ausente devolve `{ok: False}` sem exceção. Teste isolado (pode ser um `tests/` do plugin ou um script manual contra o Postgres de teste).

#### Status de execução — Fase B1
**Estado:** ✅ Concluída
- **O que foi feito:** `set_fields_for_contact(phone, values, scope='protocolo')` em logic.py: resolve contato por telefone → protocolo aberto → filtra chaves por `get_field_defs` → grava via `update_protocolo_fields`; rejeita chave desconhecida; sem protocolo aberto → {ok:False}; nunca levanta.
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase B2 — `protocolos`: endpoint REST opcional (por telefone)

**Objetivo:** expor a função da B1 via REST, para integrações externas além da `ai_tool` in-process.

**Itens:**
1. [paralelo] Adicionar em [storages/plugins/protocolos/routes.py](../storages/plugins/protocolos/routes.py) um `POST /contacts-by-phone/set-fields` (nome a confirmar) com body `{phone, values, scope?}`, gated por `plugin_permission("edit")`, delegando para `logic.set_fields_for_contact` via `asyncio.to_thread`. Seguir o formato `{"ok", "data"|"error"}`.
2. [paralelo] Nota: a `ai_tool` code roda em subprocesso e importa `logic` **direto** (não via HTTP) — o endpoint é para integrações externas/testes, não é o caminho da tool. Documentar isso em C2.

**Pronto quando:** `curl` no endpoint (autenticado) grava o campo; sem permissão retorna 403; body inválido retorna erro tratado.

#### Status de execução — Fase B2
**Estado:** ✅ Concluída
- **O que foi feito:** `POST /contacts-by-phone/set-fields` em routes.py (`{phone, values, scope?}`, gated por `edit`), delega em `asyncio.to_thread` para a função B1.
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase C1/C2 — Documentação + script externo (criar campos + tool na mão)

**Objetivo:** entregar ao usuário um guia (e opcionalmente um script) para: (1) criar os `field_defs` `codigo_oferta` e `curso_de_interesse` no `protocolos` (tela de config ou `set_field_defs`), e (2) criar a `ai_tool` code que chama `logic.set_fields_for_contact`.

**Itens:**
1. [C1, paralelo] Rascunhar `docs-planos/` ou `docs/` guia (P5 decide o local exato — sugestão: `docs/vendas-ia-protocolos-campos.md`) com: contexto, decisões, passo-a-passo.
2. [C2] Documentar a criação dos `field_defs`: pela **tela de config do `protocolos`** (`/protocolos/config`, field-builder — ver [assets/plugin_examples/protocolos](../assets/plugin_examples/protocolos)) escolhendo tipo Texto e o scope da B1/P4; **ou** por script externo que chama `logic.set_field_defs(scope, defs)` ([logic.py:252](../storages/plugins/protocolos/logic.py#L252)).
3. [C2] Documentar a criação da `ai_tool` code (padrão `tool_code/*.py`): `_bootstrap()` + `from protocolos import logic` + `logic.set_fields_for_contact(ctx.phone, {...})`. Fornecer o **SCHEMA** de exemplo (`set_atributos_comerciais` ou similar, com `codigo_oferta`/`curso_de_interesse`) e o `execute(ctx, args)`. Incluir trecho ilustrativo curto (não patch grande).
4. [C2] Script externo opcional em `scripts/` (P5): cria os 2 `field_defs` + insere a `ai_tool` via `tool_repo.save` (mesmo caminho de `tools_seed`), idempotente e não-destrutivo.
5. [C2] Documentar a **remoção manual** das 7 defs órfãs criadas pelo boot antigo (D6): pela tela **Atributos Personalizados** (ícone lixeira) — o dado já gravado nas conversas permanece recuperável pelo banco.
6. [C2] Documentar P1: a `ai_tool` que grava `codigo_oferta` deve **também** fixar a oferta (`state.set_offer`) para preservar "OFERTA EM FOCO" — conforme a decisão de A0.

**Pronto quando:** seguindo só o guia, é possível criar os 2 campos + a tool e ver a IA gravar `codigo_oferta`/`curso_de_interesse` no protocolo aberto do contato (aparecendo no popup/painel do `protocolos`), sem nenhum seed no plugin.

#### Status de execução — Fase C
**Estado:** ✅ Concluída
- **O que foi feito:** docs/vendas-ia-protocolos-campos-comerciais.md (guia: criar field_defs, ai_tool exemplo, limpeza manual, validação) + scripts/protocolos_setup_campos_comerciais.py (idempotente: cria os 2 field_defs faltantes + a ai_tool `set_atributos_comerciais`).
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase V — Verificação integrada

**Objetivo:** provar que o sistema ficou coerente: `vendas_ia` enxuto, `protocolos` recebendo escrita da IA, nada quebrado.

**Itens:**
1. [sequencial] Boot com ambos os plugins ativos → nenhum atributo de disposição recriado; `oferta_atual`/`perfil_cliente` presentes.
2. [sequencial] Fluxo comercial: mensagem com palavra-chave → cai no comercial com oferta fixada (regressão da triagem + prompt).
3. [sequencial] IA grava `codigo_oferta`/`curso_de_interesse` via a nova tool → valores aparecem no protocolo do contato; a oferta continua "em foco".
4. [sequencial] Suíte de testes verde no Postgres.

**Pronto quando:** todos os itens do Checklist (§7) marcados.

#### Status de execução — Fase V
**Estado:** ✅ Concluída
- **O que foi feito:** py_compile OK em todos os arquivos; import real dos módulos + sanity (offercode extraction dos dois caminhos, set_fields_for_contact defensivo, seed sem fechamento) contra o Postgres de teste: SANITY OK. Fase que exige DB vivo/restart (rodar o script, ver campo no popup) fica para o usuário.
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

## 4. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| `codigo_oferta` migra de `custom_attributes` p/ `protocolos` | "OFERTA EM FOCO" para de funcionar (fallback em `prompts._resolve_offercode` + `on_tool_after` observam `codigo_oferta` em `custom_attributes`) | P1/A0: a nova tool também chama `state.set_offer`; fonte primária do foco é `plugin_vendas_ia_conversa`, não o atributo. |
| Prompts já semeados no DB | Editar os `.md` não muda o que já está no banco (seed não-destrutivo) | Documentar (C2) que o usuário reedita o prompt do comercial na tela Agentes; não tentar "corrigir" via migration. |
| Atributos órfãos na tela nativa | Remover no código não apaga as defs já criadas | D6: remoção manual documentada (C2); nada destrutivo automático. |
| Agente `fechamento` já semeado no ambiente do usuário | Removê-lo do seed não o apaga do DB; ele fica "solto" | Documentar (C2) como desativar/excluir o agente `fechamento` na tela Agentes; o índice de roteador único não é afetado (fechamento é spoke). |
| Tools de fechamento já em `ai_tools` | Idem — removê-las do seed não as apaga | Documentar remoção na tela Tools; ou nota de que ficam inertes se o agente não as referencia. |
| Restart de plugin | Alterações em `storages/plugins/` exigem restart do worker | Após editar, disparar restart (toggle do plugin ou `restart.py`); validar no processo novo. |
| Worktree limpo sem plugins instalados | Testes/validação podem divergir do que está instalado (ver memória) | Validar contra a instalação real; empacotar via `.zip` se for distribuir. |
| Scope errado do campo (protocolo vs atendimento) | `update_protocolo_fields` só itera `get_extra_defs("protocolo")` | P4: fixar o scope; a B1 deve rotear `upsert_extra` para o dono certo conforme o scope escolhido. |
| Segredos | `nexus_db` DSN + chave OpenRouter | Não logar DSN/segredo; nada muda aqui, só garantir que a limpeza não exponha. |

---

## 5. Perguntas em aberto

**P1 — Como preservar "oferta em foco" quando `codigo_oferta` deixa de ser atributo nativo?**
Contexto: `events.on_tool_after` ([events.py:34](../storages/plugins/vendas_ia/events.py#L34)) e `prompts._resolve_offercode` ([prompts.py:33-38](../storages/plugins/vendas_ia/prompts.py#L33-L38)) dependem de `codigo_oferta` estar em `custom_attributes`, escrito por `set_custom_attribute`. Se a IA passa a gravar via a tool do `protocolos`, esses fallbacks silenciam.
- (a) A nova `ai_tool` grava no `protocolos` **e** chama `state.set_offer` (fixa o foco na fonte primária); `on_tool_after` passa a observar o **nome da nova tool**; remove-se o fallback `CODE_ATTR_KEY` do prompt. **← recomendada** (foco robusto, sem depender de atributo nativo).
- (b) Manter `codigo_oferta` **também** como atributo nativo (a IA grava nos dois) — rejeitado: é justamente a duplicação que o plano elimina.
`⏸️ A DECIDIR em A0.`

**P2 — `atendimento.py` e `fechamento.md`: remover ou aposentar-deprecated?**
- (a) Remover os arquivos (código morto fora). **← recomendada** (D6: sem produção a proteger).
- (b) Mover para `_deprecated/` como referência do futuro plugin de fechamento.
`⏸️ A DECIDIR em A2.`

**P3 — A escrita da IA deve criar o protocolo se não houver aberto?**
- (a) Só grava se já existe protocolo aberto (`get_open_protocolo_for_contact`) — se não, no-op com aviso. **← recomendada** (a criação do protocolo é do fluxo do `protocolos`, não da tool comercial).
- (b) `ensure_protocolo_for_contact` cria on-demand — acopla a criação a uma tool comercial.
`⏸️ A DECIDIR em B1.`

**P4 — Scope dos campos comerciais (`codigo_oferta`/`curso_de_interesse`)?**
- (a) `protocolo` (1 por contato, persiste pela vida do protocolo). **← recomendada** (é "o curso/oferta que o lead quer", não por-ciclo).
- (b) `atendimento` (por ciclo de atendimento).
`⏸️ A DECIDIR em B1.`

**P5 — Local do guia + entregar script externo?**
- (a) Guia em `docs/` + script idempotente em `scripts/`. **← recomendada** (o usuário pediu "código externo OU documentação" — entregar os dois).
- (b) Só documentação.
`⏸️ A DECIDIR em C1.`

---

## 6. Apêndice — arquivos-chave

**`vendas_ia` (Fase A — enxugar):**
- [storages/plugins/vendas_ia/state.py](../storages/plugins/vendas_ia/state.py) — remover `DISPOSITION_DEFS` + laço de disposição.
- [storages/plugins/vendas_ia/events.py](../storages/plugins/vendas_ia/events.py) — `on_startup` (herda) + `on_tool_after` (P1).
- [storages/plugins/vendas_ia/prompts.py](../storages/plugins/vendas_ia/prompts.py) — fallback `codigo_oferta` (P1).
- [storages/plugins/vendas_ia/tools_seed.py](../storages/plugins/vendas_ia/tools_seed.py) — tirar as 4 tools de fechamento.
- [storages/plugins/vendas_ia/agents_seed.py](../storages/plugins/vendas_ia/agents_seed.py) — tirar spoke `fechamento` + target.
- [storages/plugins/vendas_ia/atendimento.py](../storages/plugins/vendas_ia/atendimento.py) — aposentar (P2).
- [storages/plugins/vendas_ia/tool_code/](../storages/plugins/vendas_ia/tool_code/) — remover os 4 de fechamento.
- [storages/plugins/vendas_ia/routes.py](../storages/plugins/vendas_ia/routes.py) — `_AGENT_KEYS` + `/seed` (tirar `ensure_attribute_defs` de disposição).
- [storages/plugins/vendas_ia/seed_prompts/](../storages/plugins/vendas_ia/seed_prompts/) — `comercial.md`/`roteador.md`/`fechamento.md`.

**`protocolos` (Fase B — API de escrita):**
- [storages/plugins/protocolos/logic.py](../storages/plugins/protocolos/logic.py) — nova `set_fields_for_contact` (reusa `get_open_protocolo_for_contact`, `get_field_defs`, `normalize_values`, `upsert_extra`).
- [storages/plugins/protocolos/routes.py](../storages/plugins/protocolos/routes.py) — endpoint REST opcional.
- ⚠️ A fonte versionada dos plugins de exemplo fica em [assets/plugin_examples/protocolos](../assets/plugin_examples/protocolos) — confirmar se a edição deve ir para `storages/plugins/` (instalado) e/ou `assets/` (versionado) conforme [Plugin changes via zip] na memória.

**Doc/script (Fase C):**
- `docs/` (guia) + `scripts/` (script idempotente) — a criar (P5).

---

## 7. Checklist de verificação

- [ ] Reiniciar o servidor com `vendas_ia` ativo **não** recria os 7 atributos de disposição na tela Atributos Personalizados.
- [ ] `oferta_atual` e `perfil_cliente` continuam presentes; triagem por palavra-chave ainda leva ao comercial com "OFERTA EM FOCO".
- [ ] `/seed` numa instalação limpa cria só `comercial`/`suporte`/`roteador` + 3 `ai_tools` de busca (sem fechamento).
- [ ] Nenhum import ativo de `vendas_ia.atendimento` (`grep`).
- [ ] `logic.set_fields_for_contact(phone, {"codigo_oferta": ...})` grava no protocolo aberto; chave desconhecida rejeitada; contato/protocolo ausente → `{ok: False}` sem exceção.
- [ ] Campo de **texto** (`codigo_oferta`/`curso_de_interesse`) grava (não só campo de opção).
- [ ] Endpoint REST (se feito): grava autenticado; 403 sem permissão.
- [ ] Guia permite criar os 2 `field_defs` + a `ai_tool` na mão; IA grava e o valor aparece no popup/painel do `protocolos`.
- [ ] A tool que grava `codigo_oferta` também fixa a oferta (`state.set_offer`) — foco preservado (P1).
- [ ] Suíte verde no Postgres (`WHATSBOT_TEST_DB_URL`): `venv/bin/python -m pytest tests/ -q`.
- [ ] `node --test` nos módulos JS puros tocados (se houver — não previsto neste plano).
- [ ] Restart de plugin validado no processo novo (não no antigo).
- [ ] Sem segredo (DSN Nexus / chave) em log ou URL.
