# Plano 41 — Desacoplar o fechamento do `vendas_ia` e mover os campos de conversa para o plugin `protocolos` (com API de escrita para tools da IA)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-09 · **Escopo:** médio
> **Origem:** pedido do usuário (prints: 7 atributos de fechamento aparecendo na tela **Atributos Personalizados** nativa; discussão de design nesta sessão sobre o que pertence ao `protocolos` vs ao `vendas_ia`). **Método:** leitura do código real dos dois plugins + `grep` (`arquivo:linha` verificados abaixo).
> O plugin `vendas_ia` cria, **no boot** (`app.startup`), 7 atributos personalizados NATIVOS editáveis (`curso_de_interesse`, `codigo_oferta`, `motivo_de_abertura`, `status_de_fechamento`, `status_conversa`, `tipo_de_atendimento`, `observacao`) — resquício da sua camada de **fechamento genérica** ("placeholder até existir o plugin de atendimento"). Mas o plugin `protocolos` **já é** esse plugin, com seu próprio field-builder e fluxo de resolução humano. O plano (1) **enxuga o `vendas_ia`** removendo a criação desses atributos e aposentando a camada de fechamento (tools + agente, desligados por ora), (2) dá ao `protocolos` uma **API de escrita por telefone** para uma tool da IA setar campos comerciais, e (3) entrega **documentação + script externo** para o usuário criar à mão os `field_defs` `codigo_oferta`/`curso_de_interesse` e a tool que os grava. Sem seed.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|-----------------------|
| D1 | ✅ (2026-07-09) Os campos de disposição/fechamento **não** pertencem aos atributos nativos — pertencem ao plugin `protocolos`. | O `vendas_ia` para de criar `custom_attribute_definitions` de fechamento no startup. Fase A. |
| D2 | ✅ (2026-07-09) A camada de **fechamento** do `vendas_ia` é aposentada: `atendimento.py` + as 4 tools de fechamento (`buscar_dados_protocolo`, `buscar_opcoes_atributo_personalizado`, `executar_fechamento`, `finalizar_atendimento`) + o **agente `fechamento`** ficam **desligados por enquanto**. A IA **não** fecha protocolo/atendimento — isso é humano no popup do `protocolos`. | Fase A. Remoção do `SPOKES[fechamento]`/`FECHAMENTO_TOOLS`/`FECHAMENTO_HOOKS` do seed e do `TOOLS` de `tools_seed`. |
| D3 | ✅ (2026-07-09) O `vendas_ia` fica **enxuto**: mantém só runtime — triagem por palavra-chave→oferta (`filter.agent.resolve`), fixar oferta no `tool.after`, injeção "OFERTA EM FOCO" no prompt, busca no Nexus + cache, espelho de `oferta_atual`. | Fase A preserva `filters.py`, `nexus_db.py`, `search.py`, `embeddings.py`, `triage.py`, `prompts.py`, `state.set_offer`/`_mirror_offer`, e os atributos de sistema `oferta_atual`/`perfil_cliente`. |
| D4 | ✅ (2026-07-09) **Todos** os campos pertencem ao `protocolos`. O `protocolos` deve expor uma **API programática** (função pública + endpoint REST) para uma tool da IA **setar** campos nele, resolvendo o alvo por **telefone** (a ai_tool `kind=code` só recebe `ctx.phone`). | Fase B: nova função pública em `logic.py` + endpoint. |
| D5 | ✅ (2026-07-09) **Sem seed.** O usuário cria os `field_defs` `codigo_oferta`/`curso_de_interesse` **à mão** na tela de config do `protocolos` e cria a **ai_tool à mão**. Ele quer OU um **script externo** OU **documentação** para uma IA fazer isso. | Fase C entrega ambos (doc + script idempotente opcional), fora do fluxo automático do plugin. |
| D6 | ✅ (2026-07-09) Escopo AGORA: apenas `codigo_oferta` e `curso_de_interesse` (campos comerciais que a IA preenche). Os demais 5 de fechamento saem de cena (não migram para o `protocolos` agora — o usuário decide depois quais recriar no field-builder). | Fase C documenta só os 2. A API da Fase B é genérica (serve qualquer `field_def`). |
| D7 | ✅ (2026-07-09) **Não apagar dados do usuário automaticamente.** Os 7 atributos nativos já criados **não** são deletados por migration/código — a remoção é **manual** (documentada) na tela Atributos, porque podem já conter valores. | Fase A não mexe em `custom_attribute_definitions` existentes; Fase C documenta a limpeza manual. |
| D8 | ✅ (2026-07-09) Princípio fixo: plugins de venda **ainda em bring-up**, sem produção estável a proteger ⇒ **remover** o código morto de fechamento, não empilhar flags/stopgap. | Fase A é remoção real, não feature-flag. |

---

## 1. Resumo executivo

O `vendas_ia` carrega uma **camada de fechamento genérica** que foi escrita como *placeholder* antes do `protocolos` existir. Ela: (a) cria 7 atributos nativos editáveis no boot (poluindo a tela **Atributos Personalizados**), (b) semeia um **agente `fechamento`** e **4 tools** que gravam a disposição **direto** em `conversations.custom_attributes` e dão `set_status("closed")` — **por fora** do `protocolos`, criando um segundo pipeline de fechamento concorrente ao popup humano do `protocolos`.

A decisão é **encerrar esse pipeline**: enxugar o `vendas_ia` para só o que precisa de runtime (triagem/oferta/prompt/Nexus) e devolver **toda a responsabilidade de campos de conversa/fechamento ao `protocolos`**. Como a IA comercial ainda precisa gravar `codigo_oferta`/`curso_de_interesse`, o `protocolos` ganha uma **API de escrita por telefone** (função pública + endpoint) que uma **ai_tool `kind=code`** chama — validando contra os `field_defs` do plugin. Nada disso é semeado: o usuário cria os campos e a tool à mão, com **documentação + script externo** entregues aqui.

---

## 2. Como funciona hoje (mapa)

### 2.1 O que o `vendas_ia` cria/roda no boot e no seed

| Etapa | Local (`arquivo:linha`) | O que acontece |
|-------|-------------------------|----------------|
| `app.startup` → atributos | [storages/plugins/vendas_ia/events.py:68-77](../storages/plugins/vendas_ia/events.py#L68-L77) | `on_startup` chama `state.ensure_attribute_defs()`. |
| Defs de disposição (os 7) | [storages/plugins/vendas_ia/state.py:36-56](../storages/plugins/vendas_ia/state.py#L36-L56) | `DISPOSITION_DEFS` — cria `curso_de_interesse`, `codigo_oferta`, `motivo_de_abertura`, `status_de_fechamento`, `status_conversa`, `tipo_de_atendimento`, `observacao` como `custom_attribute_definitions` **`is_system=0` (editáveis)** via `create_definition`. ⚠️ É o que aparece no 1º print. |
| Defs de sistema (manter) | [storages/plugins/vendas_ia/state.py:67-72](../storages/plugins/vendas_ia/state.py#L67-L72) | `oferta_atual` + `perfil_cliente` como `is_system=1` — **legítimos do `vendas_ia`** (oferta em foco / perfil). |
| Seed do agente `fechamento` | [storages/plugins/vendas_ia/agents_seed.py:38-71](../storages/plugins/vendas_ia/agents_seed.py#L38-L71) | `FECHAMENTO_TOOLS`, `FECHAMENTO_HOOKS`, `SPOKES[fechamento]`, e `ROUTER.routing_targets` inclui `"fechamento"`. |
| Seed das tools de fechamento | [storages/plugins/vendas_ia/tools_seed.py:26-34](../storages/plugins/vendas_ia/tools_seed.py#L26-L34) | `TOOLS` inclui `buscar_dados_protocolo`, `buscar_opcoes_atributo_personalizado`, `executar_fechamento`, `finalizar_atendimento` (ai_tools `kind=code`). |
| Endpoint `/seed` | [storages/plugins/vendas_ia/routes.py:61-83](../storages/plugins/vendas_ia/routes.py#L61-L83) | Chama `state.ensure_attribute_defs` + `agents_seed.seed_agents` + `tools_seed.seed_tools`. |
| Camada de fechamento | [storages/plugins/vendas_ia/atendimento.py:1-140](../storages/plugins/vendas_ia/atendimento.py#L1-L140) | Docstring: *"placeholder até o plugin de atendimento"*. Grava disposição em `custom_attributes` + `set_status("closed")`. |
| Código das tools de fechamento | [storages/plugins/vendas_ia/tool_code/executar_fechamento.py](../storages/plugins/vendas_ia/tool_code/executar_fechamento.py) (+ `buscar_dados_protocolo.py`, `buscar_opcoes_atributo_personalizado.py`, `finalizar_atendimento.py`) | Subprocesso; `from vendas_ia import atendimento`. |

### 2.2 O que o `vendas_ia` precisa MANTER (runtime — D3)

| Peça | Local | Papel |
|------|-------|-------|
| Triagem palavra-chave→oferta | [storages/plugins/vendas_ia/filters.py](../storages/plugins/vendas_ia/filters.py) (`filter.agent.resolve`) | Fixa oferta + força comercial, síncrono (plano 39). |
| Fixar oferta pós-hoc | [storages/plugins/vendas_ia/events.py:28-64](../storages/plugins/vendas_ia/events.py#L28-L64) (`on_tool_after`) | Quando a IA grava `codigo_oferta` via `set_custom_attribute`, fixa a oferta de verdade. ⚠️ **Depende de `codigo_oferta` chegar como `set_custom_attribute`** — ver P1. |
| "OFERTA EM FOCO" no prompt | [storages/plugins/vendas_ia/prompts.py:16-36](../storages/plugins/vendas_ia/prompts.py#L16-L36) | Lê `oferta_atual` (espelho) OU `codigo_oferta` (fallback livre) de `custom_attributes`. ⚠️ Fallback lê `codigo_oferta` — ver P1. |
| Busca Nexus + cache | [storages/plugins/vendas_ia/nexus_db.py](../storages/plugins/vendas_ia/nexus_db.py), [search.py](../storages/plugins/vendas_ia/search.py), [embeddings.py](../storages/plugins/vendas_ia/embeddings.py) | 3 tools de busca (`pesquisar_ofertas`/`_informacoes_cursos`/`_perguntas_frequentes`). |
| Espelho `oferta_atual` | [storages/plugins/vendas_ia/state.py:171-180](../storages/plugins/vendas_ia/state.py#L171-L180) (`_mirror_offer`) | Espelha oferta em foco no `custom_attributes`. |

### 2.3 O que o `protocolos` já expõe (base da Fase B)

| Função/rota | Local (`arquivo:linha`) | Cobre |
|-------------|-------------------------|-------|
| `get_open_protocolo_for_contact(contact_id)` | [storages/plugins/protocolos/logic.py:584-586](../storages/plugins/protocolos/logic.py#L584-L586) | Protocolo **aberto** do contato (`status='aberto'`). |
| `update_protocolo_fields(atid, values, ...)` | [storages/plugins/protocolos/logic.py:755-793](../storages/plugins/protocolos/logic.py#L755-L793) | Grava campos do scope **`protocolo`** (todos os tipos, via `normalize_values`); merge parcial; roteia rótulo "atendente" p/ assignee nativo. |
| `set_protocolo_field(atid, scope, key, value)` | [storages/plugins/protocolos/logic.py:1643-1675](../storages/plugins/protocolos/logic.py#L1643-L1675) | ⚠️ **Só campos de OPÇÃO** (`_option_field_def`) — **não** serve p/ `text` (`codigo_oferta`/`curso`). |
| `get_field_defs(scope)` / `normalize_values(scope, values)` | [logic.py:206](../storages/plugins/protocolos/logic.py#L206), [logic.py:374](../storages/plugins/protocolos/logic.py#L374) | Fonte dos campos + validação/coerção por tipo. |
| `upsert_extra(conn, scope, owner_id, d, value)` | [logic.py:446](../storages/plugins/protocolos/logic.py#L446) | Escreve o extra no dono (protocolo ou atendimento). |
| REST `PUT /protocolos/{atid}/fields` | [storages/plugins/protocolos/routes.py:140](../storages/plugins/protocolos/routes.py#L140) | `plugin_permission("edit")`. Por `atid`, não por phone. |
| REST `POST /protocolos/{atid}/set-field` | [storages/plugins/protocolos/routes.py:190](../storages/plugins/protocolos/routes.py#L190) | idem, só opção. |

**Gap confirmado:** não há resolvedor **por telefone** nem função de escrita **por `{chave:valor}` para tipos `text`** que uma ai_tool `kind=code` (que só tem `ctx.phone`) possa chamar. É o que a Fase B cria.

### 2.4 Precedente do padrão de tool `kind=code` que importa o módulo do plugin

[storages/plugins/vendas_ia/tool_code/executar_fechamento.py:43-73](../storages/plugins/vendas_ia/tool_code/executar_fechamento.py#L43-L73): `_bootstrap()` insere `storages/plugins` no `sys.path`, inicializa o engine e faz `from vendas_ia import atendimento`. A tool nova da Fase C segue o mesmo padrão (`from protocolos import logic`).

---

## 3. Inventário / análise

### 3.1 Itens a fazer

| # | Item | Local | O que falta / mudança | Risco | Esforço |
|---|------|-------|-----------------------|-------|---------|
| A1 | Parar de criar os 7 atributos no boot | `vendas_ia/state.py`, `events.py`, `routes.py` | Remover `DISPOSITION_DEFS` + `_ensure_editable` + o loop em `ensure_attribute_defs`; `on_startup`/`/seed` passam a garantir só `oferta_atual`/`perfil_cliente`. | baixo | S |
| A2 | Aposentar a camada de fechamento | `vendas_ia/atendimento.py`, `tool_code/{4}` | Remover `atendimento.py` e os 4 `tool_code/*.py` de fechamento. | baixo | S |
| A3 | Tirar `fechamento` do seed de agentes/tools | `vendas_ia/agents_seed.py`, `tools_seed.py`, `routes.py` (`_AGENT_KEYS`) | Remover `SPOKES[fechamento]`, `FECHAMENTO_TOOLS`, `FECHAMENTO_HOOKS`, `"fechamento"` de `ROUTER.routing_targets`, e as 4 tools de `tools_seed.TOOLS`. | médio | M |
| A4 | Limpar prompts que citam fechamento | `vendas_ia/seed_prompts/{roteador,comercial}.md`, remover `fechamento.md`? | Roteador não roteia mais p/ `fechamento`; comercial não conduz "ao fechamento" via tool. Ajustar texto (não recriar agente). | baixo | S |
| B1 | Resolver protocolo por telefone | `protocolos/logic.py` | Nova função `get_open_protocolo_for_phone(phone)` (via `contact_repo.get_by_phone` → `get_open_protocolo_for_contact`). | baixo | S |
| B2 | Escrita genérica por `{chave:valor}` | `protocolos/logic.py` | Nova `set_fields_for_contact(phone, values, scope="protocolo", create_if_missing=False)`: resolve → valida contra `get_field_defs(scope)` (rejeita chave desconhecida) → grava (reusa caminho de `update_protocolo_fields`/`upsert_extra`) → retorna resultado. | médio | M |
| B3 | Endpoint REST opcional | `protocolos/routes.py` | `POST /contacts/by-phone/{phone}/fields` (ou similar), `plugin_permission("edit")`, chama B2. Opcional (a tool importa `logic` direto). | baixo | S |
| C1 | Documentação de criação manual | `docs-planos/` ou `storages/plugins/protocolos/README*` | Passo-a-passo: criar `codigo_oferta`/`curso_de_interesse` no field-builder + criar a ai_tool + remover os 7 atributos nativos antigos. | baixo | S |
| C2 | Script externo idempotente (opcional) | `scripts/` (novo, fora do plugin) | Cria os 2 `field_defs` via `config_repo`/`logic.set_field_defs` + registra a ai_tool via `tool_repo.save`, lendo `DATABASE_URL`. Idempotente. | médio | M |
| C3 | `tool_code` de exemplo da tool de escrita | entregue como texto no doc/script (não no plugin) | Segue o padrão §2.4, faz `from protocolos import logic; logic.set_fields_for_contact(ctx.phone, {...})`. | baixo | S |

### 3.2 Falsos positivos descartados

| Suspeita | Por que NÃO é problema |
|----------|------------------------|
| "`oferta_atual`/`perfil_cliente` também são atributos do `vendas_ia` no nativo — remover" | São `is_system=1` (read-only) e **legítimos** do plugin de vendas (oferta em foco / perfil). D3 manda manter. Só os 7 de disposição saem. |
| "O `protocolos` precisa de uma tool de IA registrada no manifest" | Não. A tool é uma **ai_tool `kind=code`** criada pelo usuário na tela Tools (D5), que **importa** `protocolos.logic`. O `protocolos` não declara `entry.tools`. |
| "Precisa migration para apagar os `custom_attribute_definitions` antigos" | D7: **não** apagar automaticamente (podem ter valores). Remoção é manual/documentada. |
| "Mexer no core (`agent/`, `server/`, `db/`)" | Nada no core muda. Tudo em `storages/plugins/{vendas_ia,protocolos}/` + doc/script. `custom_attribute_repo`/`tool_repo`/`agent_repo` são consumidos, não alterados. |
| "O `set_protocolo_field` já resolve a escrita da tool" | Não — só campos de **opção**. `codigo_oferta`/`curso` são `text`. Precisa do caminho `update_protocolo_fields`/`normalize_values` (B2). |

---

## 4. Fases / Roadmap

### 4.1 Waves e dependências

```
WAVE 0   A1 · A2 · A3 · A4 · B1          ← todos 🟢 independentes (A* no vendas_ia; B1 no protocolos)
            │ (barreira: B1 e B2 antes de C)
WAVE 1   B2 · B3                          ← B2 [depende de: B1]; B3 [depende de: B2]
            │ (barreira: B2 pronta habilita a doc/tool)
WAVE 2   C1 · C2 · C3                     ← C1 🟢; C3 🟢; C2 [depende de: B2] 🔴 (grava no DB)
```

### 4.2 Tabela de fases

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|------|------|-----------|-------|-------|---------------|
| 0 | A1 | vendas_ia — atributos | 🟢 | baixo | Boot não recria os 7 atributos; `oferta_atual`/`perfil_cliente` intactos. |
| 0 | A2 | vendas_ia — fechamento code | 🟢 | baixo | `atendimento.py` + 4 `tool_code` removidos; nada mais importa `vendas_ia.atendimento`. |
| 0 | A3 | vendas_ia — seed | 🟢 [bloqueia: A4] | médio | `/seed` não cria mais agente/tools de fechamento; roteador sem alvo `fechamento`. |
| 0 | A4 | vendas_ia — prompts | 🟢 [depende de: A3] | baixo | Prompts do roteador/comercial sem menção a fechar via tool. |
| 0 | B1 | protocolos — resolver por phone | 🟢 [bloqueia: B2] | baixo | `get_open_protocolo_for_phone("55…")` retorna o protocolo aberto. |
| 1 | B2 | protocolos — escrita genérica | 🔴 [depende de: B1] | médio | `set_fields_for_contact(phone, {"codigo_oferta":"X"})` grava e rejeita chave desconhecida. |
| 1 | B3 | protocolos — endpoint | 🔴 [depende de: B2] | baixo | `POST .../fields` grava com `edit`; 403 sem permissão. |
| 2 | C1 | doc manual | 🟢 | baixo | Doc reproduzível ponta-a-ponta (campos + tool + limpeza). |
| 2 | C2 | script externo | 🔴 [depende de: B2] | médio | `python scripts/seed_protocolos_campos.py` cria 2 field_defs + 1 ai_tool, idempotente. |
| 2 | C3 | tool_code exemplo | 🟢 | baixo | Snippet válido que importa `protocolos.logic`. |

---

### Fase A1 — Parar de criar os atributos de disposição no boot

**Objetivo:** o `vendas_ia` deixa de materializar os 7 `custom_attribute_definitions` na tela nativa.

**Itens:**
- `[sequencial]` Em [state.py:36-56](../storages/plugins/vendas_ia/state.py#L36-L56): remover `DISPOSITION_DEFS` e `_ensure_editable` ([state.py:75-88](../storages/plugins/vendas_ia/state.py#L75-L88)).
- `[sequencial]` Em `ensure_attribute_defs` ([state.py:59-72](../storages/plugins/vendas_ia/state.py#L59-L72)): remover o loop `for … in DISPOSITION_DEFS`; manter só `_ensure(OFFER_ATTR_KEY…)` e `_ensure(PROFILE_ATTR_KEY…)`.
- `[paralelo]` `on_startup` ([events.py:68-77](../storages/plugins/vendas_ia/events.py#L68-L77)) e `/seed` ([routes.py:77](../storages/plugins/vendas_ia/routes.py#L77)) continuam chamando `ensure_attribute_defs` — agora reduzido. Sem mudança de assinatura.
- Grep de guarda: `DISPOSITION_KEYS`/`DISPOSITION_DEFS` em `atendimento.py` some junto na A2.

**Pronto quando:** subir o servidor com o plugin ativo e **não** aparecerem os 7 atributos novos na tela Atributos (os já existentes permanecem — D7); `oferta_atual`/`perfil_cliente` seguem presentes.

#### Status de execução — Fase A1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase A2 — Aposentar a camada de fechamento (código)

**Objetivo:** remover o pipeline de fechamento paralelo do `vendas_ia`.

**Itens:**
- `[paralelo]` Remover [storages/plugins/vendas_ia/atendimento.py](../storages/plugins/vendas_ia/atendimento.py).
- `[paralelo]` Remover `tool_code/executar_fechamento.py`, `tool_code/finalizar_atendimento.py`, `tool_code/buscar_dados_protocolo.py`, `tool_code/buscar_opcoes_atributo_personalizado.py`.
- `[sequencial]` Grep `from vendas_ia import atendimento` / `atendimento.` / `DISPOSITION_KEYS` — garantir zero referências restantes.
- ⚠️ **Não** remover `tool_code/pesquisar_*.py` (busca Nexus — D3).

**Pronto quando:** `grep -rn "atendimento" storages/plugins/vendas_ia/` só retorna comentários/strings sem import quebrado; `python -c "import ast; ast.parse(open('storages/plugins/vendas_ia/state.py').read())"` (e demais tocados) ok.

#### Status de execução — Fase A2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase A3 — Tirar o agente/tools de fechamento do seed

**Objetivo:** `/seed` não cria mais o agente `fechamento` nem as 4 tools de fechamento; o roteador não roteia para `fechamento`.

**Itens:**
- `[sequencial]` [agents_seed.py:33-71](../storages/plugins/vendas_ia/agents_seed.py#L33-L71): remover `FECHAMENTO_TOOLS`, `FECHAMENTO_HOOKS`, o dict `SPOKES[fechamento]`, e `"fechamento"` de `ROUTER["routing_targets"]` ([agents_seed.py:74](../storages/plugins/vendas_ia/agents_seed.py#L74)).
- `[sequencial]` [tools_seed.py:26-34](../storages/plugins/vendas_ia/tools_seed.py#L26-L34): remover as 4 tuplas de fechamento de `TOOLS`; manter as 3 de busca.
- `[paralelo]` [routes.py](../storages/plugins/vendas_ia/routes.py) `_AGENT_KEYS` (grep): remover `"fechamento"` para o diagnóstico não reportar um agente inexistente.
- ⚠️ **Não-destrutivo para instalações existentes:** um agente `fechamento` já semeado NÃO é apagado por código (segue o princípio de D7). Documentar a remoção manual na tela Agentes (Fase C1). A confirmar: se convém o `/seed` **avisar** que o `fechamento` legado deveria ser removido.

**Pronto quando:** rodar `/seed` numa base limpa cria só `roteador`/`comercial`/`suporte` + 3 tools de busca; `GET /api/plugins/vendas_ia/status` não lista `fechamento` em `agents.present`.

#### Status de execução — Fase A3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase A4 — Limpar prompts que citam fechamento

**Objetivo:** os prompts semeados não instruem mais a IA a fechar via tool.

**Itens:**
- `[paralelo]` [seed_prompts/roteador.md](../storages/plugins/vendas_ia/seed_prompts/roteador.md): remover `fechamento` da lista de destinos de `transferir_agente`.
- `[paralelo]` [seed_prompts/comercial.md](../storages/plugins/vendas_ia/seed_prompts/comercial.md): remover instruções de "conduzir ao fechamento" / tools de fechamento; manter `set_custom_attribute` (`perfil_cliente`, `curso_de_interesse`, `codigo_oferta`) — **ou** trocar por a nova tool do `protocolos` (ver P1).
- `[paralelo]` Remover `seed_prompts/fechamento.md` (agente aposentado).
- ⚠️ Prompts **já semeados** no banco não mudam (seed é não-destrutivo) — a edição do `.md` só afeta seeds futuros. Documentar edição manual na tela Agentes.

**Pronto quando:** grep em `seed_prompts/` sem menção a tools de fechamento; roteador só cita `comercial`/`suporte`.

#### Status de execução — Fase A4
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase B1 — Resolver protocolo aberto por telefone

**Objetivo:** dar ao `protocolos` um resolvedor `phone → protocolo aberto` (a ai_tool só tem `ctx.phone`).

**Itens:**
- `[sequencial]` Nova função pública em [logic.py](../storages/plugins/protocolos/logic.py) perto de `get_open_protocolo_for_contact` ([logic.py:584](../storages/plugins/protocolos/logic.py#L584)):
  ```python
  def get_open_protocolo_for_phone(phone: str) -> dict | None:
      c = contact_repo.get_by_phone(str(phone or ""))
      return get_open_protocolo_for_contact(c["id"]) if c else None
  ```
  `contact_repo` já é importado em `logic.py` ([logic.py:41](../storages/plugins/protocolos/logic.py#L41)).

**Pronto quando:** num REPL/teste, `logic.get_open_protocolo_for_phone("<phone com protocolo aberto>")` retorna o dict; telefone inexistente → `None`.

#### Status de execução — Fase B1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase B2 — Escrita genérica de campos por `{chave:valor}`

**Objetivo:** uma função pública que a ai_tool chama para gravar campos comerciais (`text`/opção) no protocolo aberto do contato, validando contra os `field_defs`.

**Itens:**
- `[sequencial]` Nova função em [logic.py](../storages/plugins/protocolos/logic.py):
  ```python
  def set_fields_for_contact(phone: str, values: dict, scope: str = "protocolo",
                             create_if_missing: bool = False) -> tuple[dict | None, str | None]:
      # 1) resolve contato + protocolo aberto (B1); create_if_missing → ensure_protocolo_for_contact
      # 2) rejeita chaves não definidas em get_field_defs(scope) (retorna erro claro, não grava)
      # 3) grava:
      #    - scope="protocolo": reusa update_protocolo_fields(atid, filtered_values)
      #    - scope="atendimento": upsert_extra no ÚLTIMO ciclo (ver set_protocolo_field:1662-1668)
      # 4) retorna (protocolo_rehidratado, None) ou (None, erro)
  ```
- `[sequencial]` Validação de chave desconhecida é **obrigatória** (a IA não deve criar campo novo): comparar contra `{d["key"] for d in get_field_defs(scope)}`.
- `[paralelo]` Reuso máximo: `update_protocolo_fields` já normaliza/mescla/broadcasta para scope `protocolo`; preferir delegar a ele em vez de reescrever o upsert.
- ⚠️ **Escopo do campo:** `codigo_oferta`/`curso_de_interesse` são campos **comerciais preenchidos durante a conversa**, não no resolver. Recomendação: criá-los no scope **`protocolo`** (default), que `update_protocolo_fields` cobre nativamente. Ver P2.
- ⚠️ **Espelho no core:** decidir se a escrita comercial também espelha em `conversations.custom_attributes` (como o resolve faz via `mirror_atendimento_to_core` — [logic.py:553](../storages/plugins/protocolos/logic.py#L553)). Ver P3.

**Pronto quando:** `set_fields_for_contact("<phone>", {"codigo_oferta": "COMBO26RB"})` grava no protocolo e aparece no card; `{"chave_inexistente": "x"}` retorna erro e não grava; telefone sem protocolo aberto → erro (ou cria, se `create_if_missing`).

#### Status de execução — Fase B2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase B3 — Endpoint REST (opcional)

**Objetivo:** expor B2 por HTTP (para debug/integrações; a ai_tool `kind=code` importa `logic` direto e **não** precisa dele).

**Itens:**
- `[sequencial]` Em [routes.py](../storages/plugins/protocolos/routes.py) (padrão dos handlers existentes, [routes.py:140-203](../storages/plugins/protocolos/routes.py#L140-L203)): `POST /contacts/by-phone/{phone}/fields`, body `{scope?, values}`, `dependencies=[plugin_permission("edit")]`, chama `logic.set_fields_for_contact` em `asyncio.to_thread`, resposta `{ok, data|error}`.

**Pronto quando:** `curl -X POST .../contacts/by-phone/<phone>/fields -d '{"values":{"codigo_oferta":"X"}}'` retorna `ok:true`; sem permissão → 403.

#### Status de execução — Fase B3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase C1 — Documentação de criação manual

**Objetivo:** guia reproduzível para o usuário (ou uma IA) montar tudo à mão.

**Itens (conteúdo do doc):**
- `[paralelo]` **Criar os `field_defs`** `codigo_oferta` e `curso_de_interesse` (tipo texto, scope `protocolo`) na tela **Configurar → Protocolos** (field-builder do 2º print).
- `[paralelo]` **Criar a ai_tool `kind=code`** na tela Tools nativa, colando o `tool_code` da C3 (schema com `codigo_oferta`/`curso_de_interesse` + `execute` que chama `protocolos.logic.set_fields_for_contact`). Ligar `ai_tools_code_enabled` se ainda off.
- `[paralelo]` **Vincular a tool ao agente `comercial`** (tela Agentes → `tool_names`).
- `[paralelo]` **Limpeza manual (D7):** remover os 7 atributos antigos na tela **Atributos Personalizados** e o agente `fechamento` na tela Agentes, se existirem — avisando que valores gravados serão perdidos.

**Pronto quando:** seguindo o doc numa instância limpa, a IA comercial consegue gravar `codigo_oferta` no protocolo (via a tool) e o valor aparece no card do `protocolos`.

#### Status de execução — Fase C1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase C2 — Script externo idempotente (opcional)

**Objetivo:** alternativa "código" à doc — um script fora do plugin que cria os 2 `field_defs` + registra a ai_tool.

**Itens:**
- `[sequencial]` Novo `scripts/seed_protocolos_campos.py` (fora de `storages/plugins/`): lê `DATABASE_URL`, inicializa o engine, insere `storages/plugins` no `sys.path`, e:
  - Cria/atualiza os `field_defs` via `protocolos.logic.set_field_defs("protocolo", [...])` (merge idempotente — não duplicar chaves existentes).
  - Registra a ai_tool via `tool_repo.save(name, code=…, kind="code", …)` só se `tool_repo.get(name) is None` (não-destrutivo, padrão de [tools_seed.py:52-60](../storages/plugins/vendas_ia/tools_seed.py#L52-L60)).
  - Opcional: liga `ai_tools_code_enabled` e avisa `restart_required`.
- ⚠️ Idempotência obrigatória (rodar 2× não duplica campo nem sobrescreve tool editada).

**Pronto quando:** `python scripts/seed_protocolos_campos.py` numa base sem os campos cria os 2 + a tool; rodar de novo é no-op logado.

#### Status de execução — Fase C2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase C3 — `tool_code` de exemplo da tool de escrita

**Objetivo:** o corpo da ai_tool que a doc/script instala.

**Itens (esboço, entregue como texto — não vai para dentro do plugin):**
- `[paralelo]` Schema `{"type":"function","function":{"name":"registrar_atributos_atendimento", "parameters":{codigo_oferta?, curso_de_interesse?}}}`.
- `[paralelo]` `execute(ctx, args)`: `_bootstrap()` (padrão §2.4) → `from protocolos import logic` → `logic.set_fields_for_contact(ctx.phone, {k:v for k,v in args.items() if v}, scope="protocolo")` → retorna JSON com o resultado/erro.
- ⚠️ Nome da tool ≠ `set_custom_attribute` — ver P1 (impacto no `on_tool_after`).

**Pronto quando:** o snippet parseia e, colado como ai_tool + com `set_fields_for_contact` pronta (B2), grava de ponta-a-ponta.

#### Status de execução — Fase C3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| `on_tool_after` fixa oferta ao ver `set_custom_attribute(codigo_oferta)` ([events.py:34-62](../storages/plugins/vendas_ia/events.py#L34-L62)) | Se a IA passar a gravar `codigo_oferta` via a **nova tool do `protocolos`** em vez de `set_custom_attribute`, o hook não dispara e a oferta pode não fixar. | P1: manter a triagem por palavra-chave (`filter.agent.resolve`) como caminho primário; e/ou fazer `on_tool_after` também observar a nova tool. |
| `prompts.py` lê `codigo_oferta` de `custom_attributes` como fallback do foco ([prompts.py:33-36](../storages/plugins/vendas_ia/prompts.py#L33-L36)) | Se `codigo_oferta` sair do `custom_attributes` (virar campo do `protocolos`), o fallback deixa de achar a oferta. | P1: manter `oferta_atual` (espelho) como fonte principal; decidir se a nova tool também espelha em `custom_attributes` (P3). |
| Instalações existentes com agente `fechamento` + 4 ai_tools + 7 atributos já no banco | Remover o seed não remove o que já existe (D7) → resíduo. | Documentar limpeza manual (C1); opcionalmente o `/status` sinaliza resíduo. |
| Prompts já semeados no banco não mudam ao editar `.md` | O roteador em produção pode continuar tentando `transferir_agente → fechamento` (agente removido). | Editar o agente na tela Agentes (C1); `transferir_agente` para alvo inexistente já é tratado pelo motor (a confirmar comportamento). |
| `set_fields_for_contact` sem validação de chave | A IA inventa um campo e ele "some" (não é `field_def`). | B2 rejeita chave desconhecida com erro claro (não grava). |
| Scope errado do campo | `update_protocolo_fields` só cobre scope `protocolo`; se o usuário criar no scope `atendimento`, a escrita precisa do caminho do ciclo. | B2 trata os dois scopes; doc (C1) recomenda scope `protocolo` (P2). |
| Tool `kind=code` desligada | `ai_tools_code_enabled` default OFF (kill-switch) → a tool não roda. | Doc/script (C1/C2) instrui ligar o switch + restart. |
| Segredos | Nenhum segredo novo; script lê `DATABASE_URL` do ambiente (não logar). | Padrão do repo. |

---

## 6. Perguntas em aberto

- **P1 — Como a oferta continua sendo fixada depois que `codigo_oferta` deixa de ser `set_custom_attribute`?**
  ✅ DECIDIDO (2026-07-09, direção): a **triagem por palavra-chave** (`filter.agent.resolve`, plano 39/40) continua sendo o caminho **primário** de fixar oferta — independe de `codigo_oferta`. Opções para o fallback: (a) atualizar `on_tool_after` para observar também a nova tool do `protocolos` e chamar `state.set_offer`; (b) aceitar que o fallback via `codigo_oferta` degrada e confiar só na keyword + `set_offer`. **Recomendação:** (a) — barato e preserva o comportamento. Confirmar na execução da Fase A/B.

- **P2 — Em qual scope criar `codigo_oferta`/`curso_de_interesse` no `protocolos`?**
  ⏸️ A confirmar pelo usuário na criação manual. (a) `protocolo` (default; `update_protocolo_fields` cobre; vive no protocolo, não no ciclo); (b) `atendimento` (por ciclo). **Recomendação:** (a) `protocolo` — são atributos do lead/negociação, estáveis entre ciclos.

- **P3 — A escrita comercial deve espelhar em `conversations.custom_attributes`?**
  ⏸️ ADIADO. O resolve já espelha via `mirror_atendimento_to_core` (respeitando `mirror_custom_attributes`). Espelhar também na escrita comercial faria `oferta_atual`/foco continuarem visíveis no painel de info do core e manteria o fallback de `prompts.py`. **Recomendação:** avaliar junto de P1 — se a nova tool espelhar `codigo_oferta` no core, o fallback de `prompts.py` sobrevive sem mudança.

- **P4 — O `protocolos` precisa do endpoint REST (B3) ou só da função (B2)?**
  ✅ DECIDIDO (2026-07-09): a ai_tool `kind=code` importa `logic` direto (padrão §2.4), então **B3 é opcional** (debug/integração). Implementar se sobrar tempo.

---

## 7. Apêndice — arquivos-chave

**vendas_ia (Fase A — remoção/limpeza):**
- `storages/plugins/vendas_ia/state.py` — remover `DISPOSITION_DEFS`/`_ensure_editable`; enxugar `ensure_attribute_defs`.
- `storages/plugins/vendas_ia/atendimento.py` — **remover**.
- `storages/plugins/vendas_ia/tool_code/{executar_fechamento,finalizar_atendimento,buscar_dados_protocolo,buscar_opcoes_atributo_personalizado}.py` — **remover**.
- `storages/plugins/vendas_ia/agents_seed.py` — remover spoke/tools/hooks de `fechamento` + alvo do roteador.
- `storages/plugins/vendas_ia/tools_seed.py` — remover as 4 tuplas de fechamento de `TOOLS`.
- `storages/plugins/vendas_ia/routes.py` — `_AGENT_KEYS` sem `fechamento`.
- `storages/plugins/vendas_ia/seed_prompts/{roteador,comercial}.md` — limpar; remover `fechamento.md`.

**protocolos (Fase B — API de escrita):**
- `storages/plugins/protocolos/logic.py` — `get_open_protocolo_for_phone` (B1) + `set_fields_for_contact` (B2).
- `storages/plugins/protocolos/routes.py` — endpoint opcional (B3).

**Fora do plugin (Fase C — entregáveis):**
- `docs-planos/` ou `storages/plugins/protocolos/` README de criação manual (C1).
- `scripts/seed_protocolos_campos.py` (C2, opcional).
- `tool_code` de exemplo entregue no doc (C3).

---

## 8. Checklist de verificação

- [ ] Servidor sobe com `vendas_ia` ativo e **não** recria os 7 atributos na tela Atributos (D7).
- [ ] `oferta_atual`/`perfil_cliente` seguem presentes; busca Nexus e "OFERTA EM FOCO" intactos.
- [ ] `grep -rn "vendas_ia import atendimento\|DISPOSITION" storages/plugins/vendas_ia/` → vazio.
- [ ] `/seed` numa base limpa cria só `roteador`/`comercial`/`suporte` + 3 tools de busca.
- [ ] `logic.set_fields_for_contact(phone, {"codigo_oferta":"X"})` grava; chave desconhecida → erro sem gravar.
- [ ] (se B3) `POST .../contacts/by-phone/{phone}/fields` grava com `edit`; 403 sem permissão.
- [ ] Fluxo ponta-a-ponta da doc (C1): IA comercial grava `codigo_oferta` via a nova tool → aparece no card do `protocolos`.
- [ ] (se C2) script idempotente: 2ª execução é no-op.
- [ ] Suíte verde no Postgres (`WHATSBOT_TEST_DB_URL`) — `venv/bin/python -m pytest tests/test_endpoints.py -q`.
- [ ] Restart de plugin funciona após ligar `ai_tools_code_enabled` (kill-switch).
- [ ] Nenhum segredo em URL/log (script lê `DATABASE_URL` do ambiente).
- [ ] Modo escuro: N/A (sem tela nova neste plano).
