# Plano 40 — Corrigir o gate do `filter.agent.resolve` (nunca dispara) + match de keyword mais específica no `vendas_ia`

> **Status:** PLANEJAMENTO · **Data:** 2026-07-09 · **Escopo:** pequeno
> **Origem:** validação manual do plano 39 (usuário mandou "quero informações sobre o combo de monitoramento" e o **roteador** rodou + `pesquisar_ofertas` repetiu — o filter não trocou o agente). **Método:** diagnóstico ao vivo nesta sessão (leitura do código real + queries no banco de produção + replay de `on_resolve_agent` com dados reais). Todos os `arquivo:linha` abaixo foram verificados.
> O plano 39 portou a triagem por palavra-chave para o filter síncrono `filter.agent.resolve` — a lógica funciona (comprovado por replay), mas o **gate D2 nunca passa** neste deploy: conversas novas nascem carimbadas com `active_agent_key = 'roteador'` (o agente `is_default`, plano 36), então o gate `if conv.get("active_agent_key"): return spec` sempre barra e a triagem nunca roda. Correção raiz: o gate passa a considerar "ainda no roteador/default" como **não atribuída** (só faz no-op num spoke real). Correção secundária: `match_keyword` passa a eleger a **keyword mais específica (mais longa)**, não a "primeira da lista" — hoje "monitoramento" (genérica) rouba o match de "combo de monitoramento". Tudo dentro de `storages/plugins/vendas_ia/`, zero core, bump `1.2.0 → 1.2.1`.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|-----------------------|
| D1 | ✅ (2026-07-09) O gate deixa de exigir `active_agent_key` **vazio**. Passa a permitir a triagem enquanto a conversa está **no roteador OU vazia**; só faz no-op quando já há um **spoke real** (não-roteador) atribuído. | Corrige a causa-raiz (Fase B). O critério vira `is_router` do agente vinculado, resolvido via `agent_repo.get`. |
| D2 | ✅ (2026-07-09) O match de keyword passa a eleger a **keyword mais específica = mais longa** entre TODAS as ofertas, com empate resolvido pela **ordem atual** (primeira da lista). Substring/case-insensitive/`;`-split mantidos. | Corrige o achado secundário 1 (Fase C). Muda `triage.match_keyword`. |
| D3 | ✅ (2026-07-09) **Zero mudança no core.** Toda a correção fica em `storages/plugins/vendas_ia/` (`filters.py`, `triage.py`, `tests/`, `plugin.yaml`). O core (`conversation_repo`, `agent_repo`) é só referência de leitura. | Nenhum arquivo de `agent/`, `app/`, `server/`, `db/` é tocado. |
| D4 | ✅ (2026-07-09) Princípio fixo: plugin sem produção estável a proteger ⇒ **corrigir de vez**, sem stopgap. O gate errado é substituído, não empilhado. | Fase B reescreve o gate; não adiciona flag de contorno. |
| D5 | ✅ (2026-07-09) O timeout de `pesquisar_ofertas` (busca híbrida lenta no Nexus) é **problema separado** — NÃO entra neste plano. Some naturalmente quando o filter fixa a oferta e o comercial deixa de pesquisar. | Registrado em "Falsos positivos / fora de escopo"; sem itens de execução. |
| D6 | ✅ (2026-07-09) Bump de versão `1.2.0 → 1.2.1` (correção de bug, aditiva). Distribuição por `.zip` (o plugin não está no git). | Fase D. |

---

## 1. Resumo executivo

Dois defeitos no plugin `vendas_ia` (pós-plano 39) impedem a triagem por palavra-chave de funcionar em produção:

1. **Gate morto (causa-raiz):** o filter `filter.agent.resolve` desiste sempre porque testa `active_agent_key` vazio, mas conversas novas neste deploy já nascem com `active_agent_key = 'roteador'` (agente `is_default`, plano 36). Resultado: o roteador roda, transfere para o comercial e o comercial re-pesquisa — exatamente o sintoma que o plano 39 deveria ter eliminado. **Fix:** o gate só faz no-op quando a conversa já está num **spoke real** (agente com `is_router = 0`); vazio ou roteador ⇒ segue a triagem.
2. **Match genérico vence (secundário):** "combo de monitoramento" casa a oferta errada (`SCRIPTS DE FAILOVER`, keyword `monitoramento`) porque a regra é "primeira da lista vence". **Fix:** eleger a keyword **mais longa** (mais específica) entre todas as ofertas.

Ambos ficam contido no plugin, cobertos por testes DB-free, sem tocar o core.

---

## 2. Como funciona hoje (mapa)

### 2.1 O gate que nunca passa (a causa-raiz)

| Etapa | Local (`arquivo:linha`) | O que acontece |
|-------|-------------------------|----------------|
| Aplicação do filter | [app/services/agent_run_service.py:54-63](../app/services/agent_run_service.py#L54) | `_resolve_agent_spec` monta o spec baseline (`build_for_contact`) e aplica `filter.agent.resolve`. Síncrono, aguardado, no início do turno. |
| Gate do plugin | [storages/plugins/vendas_ia/filters.py:56-61](../storages/plugins/vendas_ia/filters.py#L56) | `conv = get_open_for_contact_scoped(contact)`; **`if conv.get("active_agent_key"): return spec`** — no-op se houver QUALQUER agente vinculado. |
| Seed do agente na criação | [db/repositories/conversation_repo.py:95-96](../db/repositories/conversation_repo.py#L95) | `_insert_conversation`: **`if active_agent_key is None: active_agent_key = default_agent_key_for_inbox(inbox_id)`** — toda conversa nova é carimbada. |
| Resolução do agente-padrão | [db/repositories/conversation_repo.py:43-66](../db/repositories/conversation_repo.py#L43) | `default_agent_key_for_inbox`: inbox `default_agent_key` (None no caso) → agente `is_default=1` (**`roteador`**, plano 36) → `DEFAULT_AGENT_KEY`. |

⚠️ **Consequência:** `active_agent_key` **nunca é vazio** numa conversa nova → o gate de [filters.py:59](../storages/plugins/vendas_ia/filters.py#L59) barra 100% das vezes → a triagem por keyword **nunca executa**.

**Provas coletadas ao vivo (banco de produção, canal `telegram_1cfe2138`, inbox 11):**
- `is_default` agent = `roteador`; `inbox 11 default_agent_key = None` ⇒ conversa nova nasce com `active_agent_key='roteador'`.
- conv 54 (a do print do usuário): **sem oferta fixada** (`plugin_vendas_ia_conversa` vazia para `conversation_id=54`).
- Fixações por keyword (`matched_keyword` preenchido) só existem às **16:30–16:38** (conv 47/49) — era do handler antigo `message.saved` (removido no plano 39). **Zero** fixações por keyword depois de o filter subir (worker novo 19:38 UTC; turno 20:06 UTC).
- Replay de `on_resolve_agent` com os dados reais **forçando `active_agent_key=None`** ⇒ o filter casou, chamou `set_offer`+`set_agent` e logou `vendas_ia: oferta O06C57F42 fixada (kw='monitoramento') → agente 'comercial' conv=54 [filter]`. **A lógica funciona; só o gate a bloqueia.**

### 2.2 O que o gate PRECISA distinguir (hub-and-spoke)

| Peça | Local (`arquivo:linha`) | Nota |
|------|-------------------------|------|
| `agent_repo.get` devolve `is_router` | [db/repositories/agent_repo.py:66,71](../db/repositories/agent_repo.py#L66) | `_row_to_dict` coage `d["is_router"] = bool(...)`; `get(agent_key)` retorna o dict (ou `None`). Base do novo gate. |
| Agentes semeados (produção) | (query ao vivo) | `roteador` (`is_router=1`), `comercial`/`suporte`/`fechamento`/`default` (`is_router=0`), todos `enabled=1`. |
| Roteamento hub-and-spoke | [ai_engine/routing.py](../ai_engine/routing.py) (referência) | O **roteador** é o hub de entrada; spokes assumem via `transferir_agente`. "Ainda no roteador" = conversa não roteada ⇒ é onde a keyword deve redirecionar. |

### 2.3 O match que elege a keyword errada (secundário)

| Peça | Local (`arquivo:linha`) | Nota |
|------|-------------------------|------|
| `match_keyword` (primeiro-vence) | [storages/plugins/vendas_ia/triage.py:25-36](../storages/plugins/vendas_ia/triage.py#L25) | Itera ofertas na ordem da lista; para cada, itera `key_words.split(";")`; **`return oferta, kw` no 1º hit** (linha 35). |
| Origem das ofertas | [storages/plugins/vendas_ia/nexus_db.py:146-154](../storages/plugins/vendas_ia/nexus_db.py#L146) | `fetch_ofertas_ativas`: `SELECT id, name, offercode, key_words ... WHERE is_active_for_ia = true` — **ordem não determinística** (sem `ORDER BY`). |

⚠️ **Consequência:** com "monitoramento" listado em DUAS ofertas (`SCRIPTS DE FAILOVER` e `COMBO DE MONITORAMENTO`) e a genérica aparecendo primeiro, "combo de monitoramento" casa a oferta errada. A keyword específica (`combo de monitoramento`, mais longa) deveria vencer.

### 2.4 O que permanece intacto

| Peça | Local | Nota |
|------|-------|------|
| Fluxo pós-gate do filter (set_offer → set_agent → rebuild) | [storages/plugins/vendas_ia/filters.py:65-95](../storages/plugins/vendas_ia/filters.py#L65) | Comprovadamente correto no replay. **Sem mudança** além do gate. |
| `ai_in_command` (defesa em profundidade) | [storages/plugins/vendas_ia/triage.py:39-52](../storages/plugins/vendas_ia/triage.py#L39) | Permanece (barra humano/IA pausada/tag de transferência). |
| Leitura do texto do turno | [storages/plugins/vendas_ia/filters.py:66-72](../storages/plugins/vendas_ia/filters.py#L66) | `message_repo.get_last_user_message(contact.id, conversation_id=conv_id)`. Sem mudança. |
| `on_tool_after` / `on_startup` | [storages/plugins/vendas_ia/events.py](../storages/plugins/vendas_ia/events.py) | Sem mudança. |

---

## 3. Inventário / análise (itens a fazer)

| # | Item | Local | O que falta | Abordagem | Risco | Esforço |
|---|------|-------|-------------|-----------|-------|---------|
| I1 | Gate por "spoke real", não por "vazio" | `filters.py:56-61` | Testa só `active_agent_key` truthy | Ler `akey=conv.get("active_agent_key")`; se `akey`, `a=agent_repo.get(akey)`; **no-op só se `a` existe e `a.get("is_router")` é falso** (spoke). Vazio OU roteador OU agente inexistente ⇒ segue. | médio | S |
| I2 | Não re-forçar quando já é o target | `filters.py` (gate) | — | Coberto por I1: `comercial` (o target) tem `is_router=0` ⇒ o gate já faz no-op. Confirmar com teste. | baixo | S |
| I3 | Match: keyword mais longa vence | `triage.py:25-36` | "primeiro-vence" | Varrer TODAS as ofertas/keywords, guardar o candidato de **maior `len(kw)`** que casa; empate ⇒ mantém a 1ª ordem encontrada. Substring/case/`;`-split idênticos. | baixo | S |
| I4 | Import de `agent_repo` no filter | `filters.py:23` | Já importado | Confirmar que `agent_repo` está no import (está: linha 23). | baixo | S |
| I5 | Testes do novo gate + novo match | `tests/test_triage_filter.py` | Cobrem só o gate antigo/1º-vence | Ajustar `test_match_first_offer_wins` (vira "mais-longa-vence") e o `patched`/gate para o cenário "conversa no roteador ⇒ swap"; manter "spoke real ⇒ no-op". | médio | M |
| I6 | Bump de versão | `plugin.yaml:3` | `1.2.0` | `1.2.1` + nota no comentário do `filters`. | baixo | S |
| I7 | Regerar `.zip` | (distribuição) | zip antigo | Regerar no formato do endpoint de export (arcnames relativos, sem `__pycache__`/`.db`). | baixo | S |

### 3.1 Falsos positivos / fora de escopo (descartados com razão)

| Suspeita | Por que NÃO é o alvo deste plano |
|----------|----------------------------------|
| "O filter não está registrado / plugin não recarregou" | Falso. `GET /api/plugins/manifest` mostra `vendas_ia` **v1.2.0** carregado; `plugins.load_error = None`; o loader registra `entry.filters` via `_entry_filters`/`register_plugin_filters` ([plugins/loader.py:271-274](../plugins/loader.py#L271), [server/app.py:150-151](../server/app.py#L150)). O worker (PID vivo) subiu 19:38 UTC, o turno foi 20:06 UTC. Está tudo carregado — o problema é o gate. |
| "O escopo por canal (`get_open_for_contact_scoped`) resolve a conversa errada" | Falso. Replay com o canal real (`telegram_1cfe2138`) resolveu a conv 54 (inbox 11) corretamente. Não é o problema. |
| "`build_for_contact` volta o roteador mesmo após `set_agent(comercial)`" | Falso. Verificado ao vivo: com `active_agent_key='comercial'`, `build_for_contact` devolve `agent_key='comercial'`. O rebuild funciona. |
| "`pesquisar_ofertas` estoura timeout ⇒ o roteamento está quebrado" | Fora de escopo (D5). É a busca híbrida no Nexus lenta — problema separado; some quando o filter fixa a oferta e o comercial não pesquisa mais. |
| "Precisa mudar o core (`_insert_conversation`) para não carimbar o roteador" | Rejeitado (D3). O carimbo é comportamento desejado do core (plano 36: painel mostra "IA padrão" desde o início). A correção certa é o **plugin** entender que "no roteador" = "ainda não roteado". |

---

## 4. Mudanças por camada

- **Backend/core:** nenhuma (D3). `filter.agent.resolve`, `agent_repo.get` (com `is_router`) e o carimbo de criação já existem.
- **Plugin `vendas_ia`:**
  - `filters.py` — novo gate por `is_router` (I1/I2).
  - `triage.py` — `match_keyword` elege a keyword mais longa (I3).
  - `tests/test_triage_filter.py` — ajusta/expande cobertura (I5).
  - `plugin.yaml` — bump `1.2.1` (I6).
- **DB/migrations:** nenhuma.
- **Frontend:** nenhuma.

---

## 5. Fases / Roadmap

### 5.1 Diagrama de dependências

```
WAVE 0   B(gate is_router)  ·  C(match mais-longa)        ← 🟢 paralelos, arquivos distintos
             │                     │
             └───────── barreira ──┘  (ambos prontos)
                        │
WAVE 1   D1(testes) ──────────────► D2(bump + zip + validação manual)   ← 🔴 sequencial
```

> B (`filters.py`) e C (`triage.py`) tocam arquivos diferentes e são independentes → paralelos. D1 (testes) depende dos dois. D2 (empacotar/validar) fecha.

### 5.2 Tabela de fases

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando / dependência |
|------|------|------------|-------|-------|------------------------------|
| 0 | B | Gate por `is_router` (`filters.py`) | 🟢 | médio | Numa conversa carimbada com `roteador`, o filter segue a triagem; num spoke real, no-op |
| 0 | C | Match keyword mais longa (`triage.py`) | 🟢 | baixo | "combo de monitoramento" casa `OF5540D5F` (COMBO), não `O06C57F42` (SCRIPTS) |
| 1 | D1 | Testes DB-free | 🔴 | médio | `[depende de: B, C]` suíte do plugin verde |
| 1 | D2 | Bump + zip + validação manual | 🔴 | baixo | `[depende de: D1]` v1.2.1, zip regerado, cenário real OK no painel |

---

### Fase B — Gate por "spoke real" (`is_router`), não por "vazio"
**Objetivo:** o filter deixa de desistir quando a conversa está no **roteador/default**; só faz no-op num spoke já atribuído. `[paralelo com C]`
**Itens:**
- `[sequencial]` Em [filters.py:56-61](../storages/plugins/vendas_ia/filters.py#L56), substituir o gate atual:
  ```python
  conv = conversation_repo.get_open_for_contact_scoped(contact)
  if not conv:
      return spec
  akey = conv.get("active_agent_key")
  if akey:
      a = agent_repo.get(akey)
      # No-op só se um SPOKE real (não-roteador) já assumiu a conversa.
      # Vazio, roteador (is_router) ou agente inexistente ⇒ segue a triagem.
      if a and not a.get("is_router"):
          return spec
  ```
- `[sequencial]` Atualizar o docstring do módulo e o comentário do gate ([filters.py:9-12,50](../storages/plugins/vendas_ia/filters.py#L9)): a semântica não é mais "`active_agent_key` vazio" e sim "ainda no roteador/default (não roteada)".
- `[sequencial]` Confirmar que `agent_repo` já está importado ([filters.py:23](../storages/plugins/vendas_ia/filters.py#L23)) — está.
- **A confirmar em execução:** que o `target` (`comercial`, `is_router=0`) faz o gate cair no no-op num 2º turno (não re-força a cada mensagem) — cobre a intenção D2 do plano 39 sem re-consultar o Nexus quando já é spoke.
**Pronto quando:** replay/teste com conversa carimbada `roteador` ⇒ o filter fixa a oferta e devolve o spec do `comercial`; com conversa em `comercial`/`suporte` ⇒ retorna o `spec` inalterado sem tocar o Nexus.

#### Status de execução — Fase B
**Estado:** ✅ Concluída
- **O que foi feito:** `storages/plugins/vendas_ia/filters.py` — gate reescrito (§3): em vez de `if conv.get("active_agent_key"): return spec`, agora lê `akey`, resolve `a = agent_repo.get(akey)` e só faz no-op `if a and not a.get("is_router")`. Docstring do módulo (linhas 9-13) atualizado para a nova semântica "spoke real". `agent_repo` já importado (linha 23).
- **Como foi feito / decisões:** exatamente o esboço do plano (I1). Vazio, roteador (`is_router=1`) e agente inexistente (`a is None`) ⇒ seguem a triagem; só spoke não-roteador atribuído barra.
- **Problemas / pendências:** nenhum.
- **Verificação:** cobertura por `test_gate_spoke_agent_is_noop` (comercial ⇒ no-op), `test_gate_router_agent_proceeds` (roteador ⇒ swap), `test_gate_missing_agent_proceeds` (agente deletado ⇒ segue). Suíte verde.

---

### Fase C — Match elege a keyword mais específica (mais longa)
**Objetivo:** a keyword mais longa que casa vence, entre TODAS as ofertas — não a "primeira da lista". `[paralelo com B]`
**Itens:**
- `[sequencial]` Em [triage.py:25-36](../storages/plugins/vendas_ia/triage.py#L25) reescrever `match_keyword` para varrer todas as ofertas/keywords e guardar o **melhor** candidato por `len(kw)` (maior vence); empate ⇒ manter a 1ª ordem encontrada. Preservar: `msg = (text or "").lower()`, guarda de vazio, `key_words.split(";")`, `kw.strip()`, comparação `kw.lower() in msg`. Esboço:
  ```python
  best = None  # (oferta, kw)
  for oferta in ofertas:
      for raw in (oferta.get("key_words") or "").split(";"):
          kw = raw.strip()
          if kw and kw.lower() in msg:
              if best is None or len(kw) > len(best[1]):
                  best = (oferta, kw)
  return best
  ```
- `[sequencial]` Atualizar o docstring/comentário de `match_keyword` (a regra deixou de ser "primeiro-vence"; passa a ser "mais-específica/mais-longa vence, empate pela ordem").
- **Nota:** mudança compatível — não altera assinatura nem os call sites (`filters.py` e `resolve_keyword_offer`). "primeiro-vence" só existia como detalhe interno; nenhum contrato externo depende dele.
**Pronto quando:** `match_keyword("quero informações sobre o combo de monitoramento", ofertas)` devolve `('OF5540D5F', 'combo de monitoramento')` (COMBO), não `('O06C57F42', 'monitoramento')` (SCRIPTS).

#### Status de execução — Fase C
**Estado:** ✅ Concluída
- **O que foi feito:** `storages/plugins/vendas_ia/triage.py` — `match_keyword` varre TODAS as ofertas/keywords e guarda o candidato de maior `len(kw)` (`best`), em vez de retornar no 1º hit. Docstring do módulo e da função atualizados para "mais longa vence, empate pela 1ª encontrada".
- **Como foi feito / decisões:** esboço do plano (I3) preservando `msg=lower`, guarda de vazio, `;`-split, `kw.strip()`, `kw.lower() in msg`. Assinatura e call sites inalterados.
- **Problemas / pendências:** nenhum.
- **Verificação:** `test_match_longest_keyword_wins` ("combo de monitoramento" → OF5540D5F, não SCRIPTS), `test_match_length_tie_keeps_first` (2 kw de 10 chars ⇒ 1ª), `test_match_semicolon_split_second_alternative` ajustado (agora casa "plano anual", a mais longa). Verde.

---

### Fase D1 — Testes automatizados (DB-free)
**Objetivo:** blindar o novo gate e o novo match. `[depende de: B, C]`
**Itens (padrão do arquivo existente — monkeypatch, sem Postgres):**
- `[paralelo]` **Match**: renomear/ajustar `test_match_first_offer_wins` ([tests/test_triage_filter.py:51](../storages/plugins/vendas_ia/tests/test_triage_filter.py#L51)) para `test_match_longest_keyword_wins`: com o dataset das 2 ofertas contendo `monitoramento` (genérica, 1ª) e `combo de monitoramento` (específica, 2ª), o texto "combo de monitoramento" casa a **específica**. Manter os demais testes de match verdes.
- `[paralelo]` **Gate (roteador ⇒ swap)**: novo teste — `get_open_for_contact_scoped` devolve `{active_agent_key: "roteador", ...}` e `agent_repo.get("roteador")` devolve `{is_router: True, enabled: True}`; asserta que o filter **prossegue** (chama `fetch_ofertas_ativas`, `set_offer`, `set_agent`, devolve o spec do comercial). Estender o fixture `patched` ([tests/test_triage_filter.py:87](../storages/plugins/vendas_ia/tests/test_triage_filter.py#L87)) para stubar `agent_repo.get` por `agent_key`.
- `[paralelo]` **Gate (spoke real ⇒ no-op)**: `active_agent_key: "comercial"`, `agent_repo.get("comercial")` → `{is_router: False}`; asserta `out is spec` e `fetch_ofertas_ativas` **não** chamado (ajustar/renomear `test_gate_bound_agent_is_noop` [linha 126], que hoje usa `roteador` como "bound" — agora `roteador` deve PASSAR, então o caso de no-op muda para um spoke).
- `[paralelo]` **Regressão**: `test_no_conversation_is_noop`, `test_no_match_is_noop`, `test_keyword_disabled_is_noop`, `test_swap_missing_agent_fixes_offer_only` seguem verdes (ajustar mocks de `agent_repo.get` onde necessário).
- **Onde:** [storages/plugins/vendas_ia/tests/test_triage_filter.py](../storages/plugins/vendas_ia/tests/test_triage_filter.py) (+ `conftest.py` de bootstrap de sys.path já existe).
**Pronto quando:** `venv/bin/python -m pytest storages/plugins/vendas_ia/tests/test_triage_filter.py -q` verde; `tests/test_endpoints.py` não afetado (core intacto).

#### Status de execução — Fase D1
**Estado:** ✅ Concluída
- **O que foi feito:** `tests/test_triage_filter.py` — fixture `patched` estendida com stub de `agent_repo.get` por `agent_key` (roteador=router, comercial/suporte=spoke). `test_match_first_offer_wins` virou `test_match_longest_keyword_wins` + `test_match_length_tie_keeps_first`. `test_gate_bound_agent_is_noop` (usava roteador) virou `test_gate_spoke_agent_is_noop` (comercial) + os novos `test_gate_router_agent_proceeds` e `test_gate_missing_agent_proceeds`.
- **Como foi feito / decisões:** DB-free (monkeypatch), padrão do arquivo. Regressões (`test_swap_on_keyword_match`, `test_swap_missing_agent_fixes_offer_only`, `test_no_conversation/no_match/keyword_disabled_is_noop`) mantidas verdes.
- **Problemas / pendências:** nenhum.
- **Verificação:** `venv/bin/python -m pytest storages/plugins/vendas_ia/tests/test_triage_filter.py -q` → 14 passed. Core não tocado (nenhum arquivo `agent/`/`app/`/`server/`/`db/`).

---

### Fase D2 — Bump, zip e validação manual
**Objetivo:** empacotar e provar o fim-a-fim. `[depende de: D1]`
**Itens:**
- `[sequencial]` Bump `version: 1.2.0 → 1.2.1` em [plugin.yaml:3](../storages/plugins/vendas_ia/plugin.yaml#L3); atualizar o comentário do `entry.filters` se fizer sentido.
- `[sequencial]` Regerar o `.zip` no formato do endpoint de export ([server/routes/plugins.py:339-348](../server/routes/plugins.py#L339)): arcnames relativos à pasta do plugin (manifest na raiz do zip), excluindo `__pycache__/` e `.db*`.
- `[sequencial]` **Reinstalar/recarregar** o plugin (importar o `.zip` na tela Plugins **ou**, em dev, o `--reload` do uvicorn após tocar os `.py`) e confirmar `load_error = None` + versão `1.2.1` no `GET /api/plugins/manifest`.
- `[paralelo]` **Validação manual (painel)** — conversa nova no canal, mensagem `"quero informações sobre o combo de monitoramento"`:
  - Esperado: **sem** card de `transferir_agente` do roteador; a IA responde já como **comercial**; **sem** `pesquisar_ofertas`; a oferta fixada é a **COMBO DE MONITORAMENTO** (`oferta_atual` no painel), não SCRIPTS.
  - Regressão: mensagem sem keyword → roteador normal; conversa já no comercial → filter no-op.
**Pronto quando:** o cenário real bate o esperado (comercial direto, oferta certa fixada) e as regressões passam.

#### Status de execução — Fase D2
**Estado:** ✅ Concluída (validação manual no painel pendente do usuário)
- **O que foi feito:** `plugin.yaml` bump `1.2.0 → 1.2.1`. `.zip` regerado no formato do endpoint de export (arcnames relativos à pasta do plugin, `plugin.yaml` na raiz, sem `__pycache__`/`.db*`) — 31 entradas, ~66 KB.
- **Como foi feito / decisões:** zip gerado por script replicando `server/routes/plugins.py:339-348` (rglob + skips). Salvo no scratchpad: `.../scratchpad/vendas_ia-plugin.zip`.
- **Problemas / pendências:** validação fim-a-fim no painel (mensagem "quero informações sobre o combo de monitoramento" → comercial direto, oferta COMBO fixada) depende de reinstalar o zip + rodar no ambiente conectado — a fazer pelo usuário.
- **Verificação:** `version: 1.2.1` confirmado dentro do zip; suíte do plugin verde. Em dev o `--reload` do uvicorn recarrega ao tocar os `.py`; para distribuir, importar o `.zip` na tela Plugins.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Gate "roteador ⇒ segue" re-forçando a cada mensagem | Enquanto a conversa segue no roteador, o filter poderia re-rodar a triagem a cada turno | Aceitável: no 1º hit o filter faz `set_agent(comercial)` → nos turnos seguintes `active_agent_key='comercial'` (spoke) ⇒ gate no-op. A janela "no roteador" dura só até o 1º match. |
| `agent_repo.get(akey)` retorna `None` (agente deletado) | Tratar como "spoke" barraria a triagem indevidamente | Novo gate: `if a and not a.get("is_router")` — agente inexistente (`a is None`) **não** faz no-op (segue a triagem), coerente com "não é um spoke real assumindo". |
| Consulta extra ao `agent_repo` por turno | Custo | `agent_repo.get` é um SELECT por PK, barato; só roda quando `active_agent_key` truthy (quase sempre, mas trivial). O gate ainda evita o **Nexus** quando é spoke. |
| `is_router` ausente/legado no dict | `a.get("is_router")` `None` | `_row_to_dict` já coage `bool(...)` ([agent_repo.py:66](../db/repositories/agent_repo.py#L66)); `None` só se o dict não vier do repo — no fluxo real sempre vem. |
| Match mais-longa muda oferta em conversas existentes | Uma keyword antes escolhida muda de oferta | Desejado (é a correção). Só afeta NOVOS matches; ofertas já fixadas em `plugin_vendas_ia_conversa` não são reavaliadas. |
| Empate de comprimento de keyword | Duas keywords do mesmo tamanho casam | Resolver pela ordem de varredura (1ª encontrada), documentado — determinístico o suficiente; ordem das ofertas vem do Nexus sem `ORDER BY` (aceito). |
| Restart de plugin | O fix só vale após recarregar (toggle/`--reload`/reimport do zip) | Documentado no "Pronto quando" de D2; em dev o `--reload` cobre `storages/plugins`. |
| Postgres (único backend) nos testes | Testes assumindo SQLite | Testes são DB-free (monkeypatch); não tocam engine. |
| Segredos | `nexus_dsn`/chave em log | Sem mudança nesse eixo; nada novo logado. |

---

## 7. Perguntas em aberto

- **P1 — O gate deve considerar também o agente `is_default` não-roteador (se um dia o default deixar de ser o roteador)?**
  ✅ DECIDIDO (2026-07-09): o critério é `is_router`. Hoje o `is_default` É o roteador, então "no roteador" cobre o caso. Se no futuro o default virar um spoke, a triagem deixaria de rodar sobre ele — aceitável (o operador escolheu um spoke como padrão). Reavaliar só se surgir o caso.

- **P2 — Adicionar `ORDER BY` em `fetch_ofertas_ativas` para tornar o empate determinístico?**
  ⏸️ ADIADO: o critério "mais longa vence" já resolve o caso real; o empate exato de comprimento é raro. Se virar problema, adicionar `ORDER BY offercode` (barato) num incremento — fora deste plano.

- **P3 — Re-fixar só a oferta (sem trocar agente) quando a conversa já está no comercial e chega outra keyword?**
  ⏸️ ADIADO (herdado do plano 39 P1): o gate faz no-op em spoke. Se quiser re-fixar a oferta mid-conversa, é um ramo isolado ("spoke == target ⇒ só `set_offer`") — extensão futura, não neste plano.

---

## 8. Apêndice — arquivos-chave

**Plugin `vendas_ia` (tudo aqui):**
- `storages/plugins/vendas_ia/filters.py` — novo gate por `is_router` (B)
- `storages/plugins/vendas_ia/triage.py` — `match_keyword` mais-longa-vence (C)
- `storages/plugins/vendas_ia/tests/test_triage_filter.py` — testes do gate + match (D1)
- `storages/plugins/vendas_ia/plugin.yaml` — bump `1.2.1` (D2)

**Core (apenas referência — NÃO editar):**
- `db/repositories/conversation_repo.py:43-96` — `default_agent_key_for_inbox` / `_insert_conversation` (por que `active_agent_key` nasce carimbado)
- `db/repositories/agent_repo.py:66,71` — `get` retorna `is_router` (base do novo gate)
- `app/services/agent_run_service.py:54-63` — onde `filter.agent.resolve` é aplicado
- `server/routes/plugins.py:339-348` — formato do `.zip` de export

---

## 9. Checklist de verificação

- [ ] Conversa nova (carimbada `roteador`) + keyword → turno roda como **comercial** (sem card do roteador, sem `pesquisar_ofertas`); oferta fixada + "OFERTA EM FOCO" no prompt
- [ ] "combo de monitoramento" fixa a oferta **COMBO DE MONITORAMENTO** (`OF5540D5F`), não SCRIPTS DE FAILOVER
- [ ] Conversa já num spoke real (`comercial`/`suporte`) → filter no-op, **sem** consulta ao Nexus
- [ ] Conversa nova + mensagem sem keyword → roteador normal (sem regressão)
- [ ] Plugin desativado → resolução de agente normal, sem latência/erro
- [ ] `venv/bin/python -m pytest storages/plugins/vendas_ia/tests/test_triage_filter.py -q` verde
- [ ] `tests/test_endpoints.py` verde (core intacto — nenhum arquivo de `agent/`/`app/`/`server/`/`db/` modificado)
- [ ] `plugin.yaml` em `1.2.1`; `GET /api/plugins/manifest` mostra a versão nova sem `load_error` após reload
- [ ] `.zip` do plugin regerado para distribuição (a correção chega por zip, não pelo core)
- [ ] Nenhum segredo em log/URL; campos `password` intactos
