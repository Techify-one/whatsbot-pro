# Plano 39 — Palavra-chave→comercial do `vendas_ia` sem race: migrar para `filter.agent.resolve` + TTL do cache configurável

> **Status:** PLANEJAMENTO · **Data:** 2026-07-08 · **Escopo:** médio
> **Origem:** pedido do usuário (print: roteador roda e comercial re-pesquisa mesmo com palavra-chave) + discussão de design nesta sessão. **Método:** leitura do código real + grep (`arquivo:linha` verificados abaixo).
> A triagem por palavra-chave do plugin `vendas_ia` roda hoje no evento **fire-and-forget** `message.saved`, que é despachado em background (`asyncio.create_task` + `asyncio.to_thread`) e **perde a corrida** contra o turno da IA, que resolve o agente de forma **síncrona** no início. Resultado: o roteador assume, transfere para o comercial e o comercial precisa `pesquisar_ofertas` de novo. A solução é mover a triagem para o **filter `filter.agent.resolve`** (síncrono, aguardado, ANTES do turno), fixando a oferta + trocando para o comercial no mesmo turno, gated em "conversa ainda sem agente vinculado". E tornar o **TTL do cache de ofertas** configurável pela tela do plugin (hoje é a constante fixa `_CACHE_TTL = 300.0`). Tudo dentro do plugin — sem plugin, custo ~zero.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|-----------------------|
| D1 | ✅ (2026-07-08) A triagem passa a rodar em `filter.agent.resolve` (síncrono, aguardado), não mais no evento `message.saved`. | Elimina a race por construção: o filter roda dentro do pipeline, antes de o turno escolher o agente. Fase C/D. |
| D2 | ✅ (2026-07-08) Escopo de execução: só age enquanto a conversa **ainda não tem agente vinculado** (`active_agent_key` vazio → roteador/default). Se o comercial já assumiu, é no-op barato. | É simultaneamente o gate de performance ("não roda em cada mensagem") e a semântica correta ("só quando a conversa é aberta / ninguém atribuído"). Fase D. |
| D3 | ✅ (2026-07-08) **Match por substring** (contém), case-insensitive, `;`-split, **primeiro vence**. Já é o comportamento atual de `_match_keyword` — preservar. | `"quero info de COMBO26RB"` casa `COMBO26RB`. Nenhuma mudança de regra de match. Fase D. |
| D4 | ✅ (2026-07-08) O TTL do cache de ofertas vira **campo configurável na tela do plugin** (settings declarativas). Sem editar banco/código. | Novo campo `keyword_cache_ttl_seconds` em `settings.py` + `_config.DEFAULTS`; `nexus_db._cached` passa a ler o TTL do setting. Fase B/C. |
| D5 | ✅ (2026-07-08) **Zero mudança no core.** `filter.agent.resolve` já existe e já é aplicado no `_resolve_agent_spec`. Toda a mudança fica em `storages/plugins/vendas_ia/`. | Nenhum arquivo de `agent/`, `app/`, `server/`, `db/` é tocado. Fase D. |
| D6 | ✅ (2026-07-08) Princípio fixo: plugin sem produção estável a proteger ⇒ **substituir** o handler racy, não empilhar stopgap. O `on_message_saved` de keyword é **removido**; `on_tool_after` (fixar oferta quando a própria IA grava `codigo_oferta`) e `on_startup` **permanecem**. | Fase D remove só o ramo de keyword do `EVENT_HANDLERS`. |

---

## 1. Resumo executivo

O plugin `vendas_ia` tem uma regra de negócio: se a mensagem do cliente contém a **palavra-chave** de uma oferta, a conversa deve cair **direto no comercial** (pulando o roteador) e já com a **oferta fixada** (injetada no system prompt via o fragment "OFERTA EM FOCO"), pra IA não precisar pesquisar. Hoje isso está quebrado porque a triagem roda no evento `message.saved`, que é **fire-and-forget** e perde a corrida contra o turno da IA (o agente é resolvido de forma síncrona antes de o handler assíncrono terminar seu I/O no Nexus).

A solução é **portar a triagem para o filter `filter.agent.resolve`**, que roda de forma síncrona e aguardada dentro do pipeline, imediatamente antes de o turno escolher o agente. No match: fixa a oferta (`state.set_offer`), vincula o comercial (`conversation_repo.set_agent`) e devolve o `AgentSpec` do comercial para o próprio turno. Gate: só age quando a conversa ainda não tem agente vinculado. Some os dois sintomas de uma vez (roteador não pensa; comercial não repesquisa). Em paralelo, o **TTL do cache** de ofertas (hoje constante fixa) vira um campo editável na tela do plugin.

---

## 2. Como funciona hoje (mapa)

### 2.1 O caminho racy (o bug)

| Etapa | Local (`arquivo:linha`) | O que acontece |
|-------|-------------------------|----------------|
| Save do batch de texto | [app/services/messaging_service.py:834](../app/services/messaging_service.py#L834) | `contact.add_message("user", combined, ...)` persiste a mensagem. |
| Emit `message.saved` | [app/services/messaging_service.py:837](../app/services/messaging_service.py#L837) | `await emit_with_filter("message.saved", {...})` — **NÃO aguarda os handlers de plugin**. |
| Turno da IA | [app/services/messaging_service.py:859](../app/services/messaging_service.py#L859) | `await agent_handler.aprocess_message(...)` roda **em seguida**, ainda no mesmo bloco. |
| Dispatch fire-and-forget | [plugins/events.py:383-396](../plugins/events.py#L383) | `emit()` só **agenda** os handlers via `asyncio.create_task(_run_one(...))` dentro de `run_coroutine_threadsafe`. Retorna na hora. |
| Handler sync em thread | [plugins/events.py:466](../plugins/events.py#L466) | `_run_one` de handler síncrono faz `await asyncio.to_thread(handler, ...)` — offload para outra thread. |
| Triagem de keyword (perde a corrida) | [storages/plugins/vendas_ia/events.py](../storages/plugins/vendas_ia/events.py) `on_message_saved` | Faz I/O de rede no Nexus (`nexus_db.fetch_ofertas_ativas()`), casa keyword, `state.set_offer`, `conversation_repo.set_agent`. Lento ⇒ termina **depois** de o turno já ter resolvido o roteador. |
| Resolução síncrona do agente | [agent/agent_factory.py:204-229](../agent/agent_factory.py#L204) `resolve_active_agent_key` | Lê `conversation.active_agent_key`. Como ainda está vazio, cai no roteador/default. |

**Confirmação de que `emit_with_filter` não aguarda o plugin:** [plugins/events.py:401-426](../plugins/events.py#L401) — aguarda `apply_filter("filter.event.before_emit")` + `_run_core_sync_listeners` (listeners **do core**), e então chama `emit()` (fire-and-forget). Os handlers de **plugin** ficam fora do `await`.

### 2.2 O hook correto (já existe no core)

| Peça | Local (`arquivo:linha`) | Nota |
|------|-------------------------|------|
| Aplicação do filter | [app/services/agent_run_service.py:42-63](../app/services/agent_run_service.py#L42) `_resolve_agent_spec` | Constrói o spec (`build_for_contact`) e então aplica `filter.agent.resolve`. **Síncrono no pipeline, aguardado.** |
| Chamada | [app/services/agent_run_service.py:304](../app/services/agent_run_service.py#L304) | `agent_spec = await _resolve_agent_spec(handler, contact, sender)` — roda no início do turno. |
| Semântica de retorno | [app/services/agent_run_service.py:60-62](../app/services/agent_run_service.py#L60) | `None` ⇒ mantém o default (sem swap); um spec **diferente** (`swapped is not spec`) ⇒ troca o agente do turno. |
| `ctx_extras` disponíveis | [app/services/agent_run_service.py:56-59](../app/services/agent_run_service.py#L56) | `{"phone": sender, "contact_id": contact.id, "channel_id": contact.channel_id}`. |
| Chain de filters | [plugins/events.py:514-560](../plugins/events.py#L514) `apply_filter` | Cada filter recebe `(FilterContext, value)`; `None` **aborta** (retorna `None` ao caller e loga "aborted"); exceção passa o valor intacto. `FilterContext.handler` = AgentHandler; `ctx.extras` = os extras. |
| `filter.agent.resolve` é conhecido | [plugins/events.py:142,148](../plugins/events.py#L142) | Já registrado na lista de filtros conhecidos (sem warning de "unknown"). |

⚠️ **Gotcha crítico (sem recursão):** `build_for_contact` ([agent/agent_factory.py:292](../agent/agent_factory.py#L292)) **não** aplica `filter.agent.resolve` — só o wrapper `_resolve_agent_spec` aplica. Logo, chamar `build_for_contact` de dentro do nosso filter (para reconstruir o spec do comercial após `set_agent`) **não re-dispara o filter** → sem loop infinito.

⚠️ **Gotcha (evitar log de "aborted"):** retornar `None` do filter loga `filter ... aborted by plugin` em nível INFO ([plugins/events.py:553-557](../plugins/events.py#L553)) e é reinterpretado como "sem swap" no `_resolve_agent_spec`. Para o caso "sem match" o filter deve **retornar o próprio `value` (spec) inalterado**, não `None` — assim não há swap e nem ruído de log.

### 2.3 AgentSpec + reconstrução do contato

| Peça | Local (`arquivo:linha`) | Nota |
|------|-------------------------|------|
| `AgentSpec` | [agent/agent_factory.py:71](../agent/agent_factory.py#L71) | Campos: `agent_key, base_prompt, model_config, tool_names`. |
| `build_for_contact(handler, contact)` | [agent/agent_factory.py:292-361](../agent/agent_factory.py#L292) | Resolve via `resolve_active_agent_key(contact)` → `active_agent_key` → constrói o spec (render de prompt + seção de destinos do roteador + model_config). |
| `ContactMemory(phone, *, channel_id, inbox_id)` | [agent/memory.py:91-98](../agent/memory.py#L91) | Reconstruível só com `phone` + `channel_id` (o `inbox_id` é resolvido do canal). Necessário para chamar `build_for_contact` de dentro do filter (que só recebe ids nos extras). |
| `conversation_repo.get_open_for_contact_scoped(contact)` | [db/repositories/conversation_repo.py:232](../db/repositories/conversation_repo.py#L232) | Conversa aberta do **canal** do turno — mesma que `resolve_active_agent_key` usa. Serve para o gate (ler `active_agent_key`) e para pegar o `conversation_id`. |
| `conversation_repo.set_agent(conv_id, agent_key)` | [db/repositories/conversation_repo.py:535](../db/repositories/conversation_repo.py#L535) | Vincula o agente à conversa (persiste para os próximos turnos). |
| `agent_repo.get(agent_key)` | [db/repositories/agent_repo.py:71](../db/repositories/agent_repo.py#L71) | Confirmar que o comercial existe/está enabled antes de vincular (não deixar `active_agent_key` apontando para agente inexistente — regra já presente no handler antigo). |

### 2.4 Estado, prompt e cache (o que permanece)

| Peça | Local (`arquivo:linha`) | Nota |
|------|-------------------------|------|
| Fixar oferta | [storages/plugins/vendas_ia/state.py](../storages/plugins/vendas_ia/state.py) `set_offer` | Upsert na tabela do plugin + espelho em `custom_attributes` (respeita `mirror_offer_attribute`). **Reusar como está.** |
| Fragment "OFERTA EM FOCO" | [storages/plugins/vendas_ia/prompts.py](../storages/plugins/vendas_ia/prompts.py) `oferta_em_foco_fragment` | Lê `state.get_state` → offercode → dados do Nexus. Roda **depois** do `_resolve_agent_spec` (o prompt é montado após a resolução do agente), então enxerga a oferta fixada no filter no mesmo turno. **Sem mudança.** |
| Fallback via tool | [storages/plugins/vendas_ia/events.py](../storages/plugins/vendas_ia/events.py) `on_tool_after` | Fixa a oferta quando a própria IA grava `codigo_oferta`. **Permanece** (não é o caminho racy). |
| Match de keyword | [storages/plugins/vendas_ia/events.py](../storages/plugins/vendas_ia/events.py) `_match_keyword` | Substring, `;`-split, primeiro vence. **Mover para o módulo compartilhado** e reusar (D3). |
| Guarda "IA no comando" | [storages/plugins/vendas_ia/events.py](../storages/plugins/vendas_ia/events.py) `_ai_in_command` | Espelha `_conversation_ai_active`. **Reusar** (defesa em profundidade — mesmo com o gate a montante). |
| Cache de ofertas (TTL fixo) | [storages/plugins/vendas_ia/nexus_db.py:34-49](../storages/plugins/vendas_ia/nexus_db.py#L34) | `_cached(key, fn)` com `_CACHE_TTL = 300.0` **hardcoded**. `fetch_ofertas_ativas` ([nexus_db.py:146](../storages/plugins/vendas_ia/nexus_db.py#L146)) já usa. Falta: TTL vir do setting. |
| Settings declarativas | [storages/plugins/vendas_ia/settings.py](../storages/plugins/vendas_ia/settings.py) | Onde entra `keyword_cache_ttl_seconds`. |
| Defaults de leitura | [storages/plugins/vendas_ia/_config.py:16-27](../storages/plugins/vendas_ia/_config.py#L16) | `DEFAULTS` espelha os defaults de `settings.py`; `setting(key, default)` lê. |
| Manifest (wiring) | [storages/plugins/vendas_ia/plugin.yaml](../storages/plugins/vendas_ia/plugin.yaml) `entry` | Hoje: `events`, `prompts`, `settings`, `routes`. Falta: `filters: filters`. |

---

## 3. Inventário / análise (itens a fazer)

| # | Item | Local | O que falta | Abordagem | Risco | Esforço |
|---|------|-------|-------------|-----------|-------|---------|
| I1 | Campo `keyword_cache_ttl_seconds` no form | `settings.py` + `_config.py` | Não existe | Field pydantic (int, default 300, min razoável) + entrada em `DEFAULTS` | baixo | S |
| I2 | TTL do cache lido do setting | `nexus_db.py:34-49` | `_CACHE_TTL` é constante | `_cached` lê o TTL via `_config.setting("keyword_cache_ttl_seconds", 300)` (com coerção defensiva) | baixo | S |
| I3 | Módulo de triagem reutilizável | novo `triage.py` (ou dentro de `filters.py`) | Lógica está acoplada ao evento | Extrair `_match_keyword` + guarda + "resolver oferta/agente" para funções puras reusáveis | baixo | M |
| I4 | Filter `filter.agent.resolve` | novo `filters.py` | Não existe | `FILTERS = {"filter.agent.resolve": on_resolve_agent}`; gate por `active_agent_key` vazio; no hit: `set_offer` + `set_agent` + rebuild do spec do comercial | médio | M |
| I5 | Remover keyword do evento | `events.py` `EVENT_HANDLERS` | `message.saved: on_message_saved` | Remover a entrada de keyword; manter `on_tool_after`/`on_startup`; apagar `on_message_saved` (ou reduzir a no-op) | baixo | S |
| I6 | Wiring do manifest | `plugin.yaml` `entry` | Sem `filters` | Adicionar `filters: filters` | baixo | S |
| I7 | Testes | `tests/` (novo arquivo do plugin) | Sem cobertura do novo caminho | Teste do match/gate + teste de que o swap ocorre no turno; caracterização opcional do bug antigo | médio | M |

### 3.1 Falsos positivos descartados

| Suspeita | Por que NÃO é o problema |
|----------|--------------------------|
| "O match precisa ser exato" | Falso. `_match_keyword` já usa `kw.lower() in msg` (substring). `COMBO26RB` dentro de uma frase casa. O bug é a race, não o match. |
| "Duas keywords quebram a regra" | Falso. O código já retorna no **primeiro** match (`for oferta ... for kw ... return`). Comportamento desejado já existe (D3). |
| "O fragment OFERTA EM FOCO está bugado" | Falso. Ele lê `state.get_state` corretamente; só nunca via a oferta porque ela não era fixada a tempo. Com a fixação síncrona no filter, passa a enxergar no mesmo turno. |
| "Precisa mudar `emit`/`emit_with_filter` para aguardar plugins" | Rejeitado. Mudaria semântica global de eventos (fire-and-forget é contrato) e atrasaria TODO turno mesmo sem o plugin. O filter resolve localmente e sem custo para quem não tem o plugin (D5). |
| "Usar `filter.message.before_save`" | Rejeitado. Roda no ingest, **antes** de a conversa nova ser materializada ([message_ingest_service.py:479](../app/services/message_ingest_service.py#L479)) — no caso "primeira mensagem" (o principal) não há conversa para fixar agente. |
| "O `on_tool_after` também é racy e deve sair" | Falso. Ele reage a uma tool **já executada** dentro do turno (fixa a oferta post-hoc). Não decide roteamento; é fallback legítimo. Permanece. |

---

## 4. Mudanças por camada

- **Backend/core:** nenhuma (D5). `filter.agent.resolve` e `apply_filter` já existem.
- **Plugin `vendas_ia` (todas as mudanças):**
  - `settings.py` — novo campo `keyword_cache_ttl_seconds` (I1).
  - `_config.py` — novo default em `DEFAULTS` (I1).
  - `nexus_db.py` — `_cached` lê TTL do setting (I2).
  - `triage.py` (novo) — funções puras de match/resolução (I3).
  - `filters.py` (novo) — handler `filter.agent.resolve` (I4).
  - `events.py` — remover ramo de keyword (I5).
  - `plugin.yaml` — `entry.filters: filters` (I6).
- **DB/migrations:** nenhuma. (Settings de plugin persistem em `config` com prefixo `plugin.vendas_ia.`; não há tabela nova.)
- **Frontend:** nenhuma mudança de código — o campo novo aparece automaticamente no `PluginSettingsForm` (form declarativo). Conferir apenas legibilidade no modo escuro (o form é do core, já temado).

---

## 5. Fases / Roadmap

### 5.1 Diagrama de dependências

```
WAVE 0   A0(caracterização, opcional) · B1(settings TTL) · B2(cache lê TTL)   ← 🟢 paralelos
             │
             │ (B1 desbloqueia a leitura do TTL; B2 depende de B1)
             ▼
WAVE 1   C1(triage.py extrair)                                                ← 🔴 base para o filter
             │
             ▼
WAVE 2   C2(filters.py) → C3(remover keyword do events) → C4(plugin.yaml)     ← 🔴 sequencial (mesma feature)
             │
             ▼
WAVE 3   D1(testes) · D2(validação manual no painel)                          ← 🟢 paralelos
```

> Observação: B1→B2 é uma barreira curta (B2 lê o default que B1 define). A0 é independente de tudo. C1 precisa existir antes de C2. C2/C3/C4 tocam a mesma superfície (o wiring da feature) — fazer em sequência, um commit por passo.

### 5.2 Tabela de fases

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando / dependência |
|------|------|------------|-------|-------|------------------------------|
| 0 | A0 | Caracterização (opcional) | 🟢 | baixo | Teste que documenta o comportamento atual (roteador assume) — ou pular se for só descartável |
| 0 | B1 | Settings: campo TTL | 🟢 | baixo | `GET /api/plugins/vendas_ia/settings` mostra `keyword_cache_ttl_seconds` (default 300) |
| 0 | B2 | Cache lê TTL do setting | 🟢 | baixo | `[depende de: B1]` `_cached` respeita o valor salvo; TTL inválido cai no default |
| 1 | C1 | Extrair `triage.py` | 🔴 | baixo | Funções puras de match/resolução com teste unitário verde |
| 2 | C2 | `filters.py` (`filter.agent.resolve`) | 🔴 | médio | `[depende de: C1]` no match e conversa sem agente, o turno resolve **comercial** com oferta fixada |
| 2 | C3 | Remover keyword do `events.py` | 🔴 | baixo | `[depende de: C2]` `EVENT_HANDLERS` sem `message.saved` de keyword; `on_tool_after`/`on_startup` intactos |
| 2 | C4 | `plugin.yaml` `entry.filters` | 🔴 | baixo | `[depende de: C2]` filter registrado no boot (log de load sem erro) |
| 3 | D1 | Testes automatizados | 🟢 | médio | Suíte verde no Postgres |
| 3 | D2 | Validação manual (painel) | 🟢 | baixo | Mensagem com keyword → cai no comercial, sem passo do roteador, sem `pesquisar_ofertas` |

---

### Fase A0 — Caracterização (opcional)
**Objetivo:** registrar (num teste ou nota) o comportamento atual antes de mexer, para provar a regressão positiva depois.
**Itens:**
- `[sequencial]` Documentar, via teste ou observação, que hoje uma mensagem com keyword numa conversa nova resulta em `active_agent_key` do **roteador** no início do turno (a triagem por evento chega tarde). Como o caminho depende de I/O do Nexus, um teste determinístico é difícil — aceitável registrar como nota no PR se não valer o custo.
**Pronto quando:** existe um registro (teste ou descrição) do estado atual; ou decisão explícita de pular.

#### Status de execução — Fase A0
**Estado:** ⏭️ Pulada (decisão P5)
- **O que foi feito:** nada — caracterização automatizada dispensada.
- **Como foi feito / decisões:** o caminho antigo dependia de I/O do Nexus + timing (não determinístico); a prova de regressão positiva vem do swap coberto em D1 + validação manual D2.
- **Problemas / pendências:** —
- **Verificação:** —

---

### Fase B1 — Settings: campo TTL do cache
**Objetivo:** expor o TTL do cache de ofertas na tela do plugin (D4).
**Itens:**
- `[sequencial]` Em [settings.py](../storages/plugins/vendas_ia/settings.py), adicionar (seção "Feature toggles" ou nova "Cache"):
  ```python
  keyword_cache_ttl_seconds: int = Field(
      default=300, title="TTL do cache de ofertas (segundos)",
      description="Por quanto tempo o plugin reaproveita a lista de ofertas ativas do "
                  "Nexus antes de reconsultar (afeta a triagem por palavra-chave e a "
                  "OFERTA EM FOCO). Menor = dados mais frescos, mais consultas. Default 300s.")
  ```
- `[sequencial]` Em [_config.py](../storages/plugins/vendas_ia/_config.py) `DEFAULTS`, adicionar `"keyword_cache_ttl_seconds": 300`.
- **A confirmar:** `PluginSettingsForm` renderiza `int` como number input — confirmar que sim (o CLAUDE.md diz que renderiza string/int/float/bool/enum). Sem validação de min/max no schema pydantic simples; a coerção defensiva fica em B2.
**Pronto quando:** `GET /api/plugins/vendas_ia/settings` retorna o schema com `keyword_cache_ttl_seconds` e valor 300; salvar pela tela persiste `plugin.vendas_ia.keyword_cache_ttl_seconds`.

#### Status de execução — Fase B1
**Estado:** ✅ Concluída
- **O que foi feito:** campo `keyword_cache_ttl_seconds` (int, default 300) adicionado em `settings.py` (nova seção "Cache") e em `_config.DEFAULTS`.
- **Como foi feito / decisões:** Field pydantic com título/descrição PT-BR; sem min/max no schema (coerção defensiva fica no B2). O form declarativo renderiza `int` como number input.
- **Problemas / pendências:** —
- **Verificação:** `py_compile` OK; o default aparece via `_config.setting("keyword_cache_ttl_seconds", 300)`.

---

### Fase B2 — Cache lê o TTL do setting
**Objetivo:** `_cached` respeitar o TTL configurável em vez da constante fixa. `[depende de: B1]`
**Itens:**
- `[sequencial]` Em [nexus_db.py:40-49](../storages/plugins/vendas_ia/nexus_db.py#L40), trocar o uso de `_CACHE_TTL` por uma leitura do setting. Padrão sugerido (mantendo `_CACHE_TTL` como fallback):
  ```python
  def _ttl() -> float:
      from . import _config
      try:
          v = float(_config.setting("keyword_cache_ttl_seconds", _CACHE_TTL))
          return v if v > 0 else _CACHE_TTL
      except Exception:
          return _CACHE_TTL
  ```
  e usar `_ttl()` na comparação `(now - hit[0]) < _ttl()`.
- `[sequencial]` Import defensivo (late import de `_config` para não criar ciclo — `nexus_db` é importado cedo). Coerção: valores não numéricos/≤0 caem no default (fail-safe).
- **Nota:** não é preciso invalidar o cache ao salvar o TTL — o novo valor passa a valer na próxima leitura (a janela cai/estende naturalmente). `clear_cache()` ([nexus_db.py:52](../storages/plugins/vendas_ia/nexus_db.py#L52)) continua disponível se quisermos forçar.
**Pronto quando:** salvar um TTL curto (ex.: 5s) e observar que `fetch_ofertas_ativas` reconsulta após esse intervalo; TTL inválido (0/negativo/lixo) cai em 300s sem erro.

#### Status de execução — Fase B2
**Estado:** ✅ Concluída
- **O que foi feito:** `nexus_db._cached` passou a ler o TTL via novo helper `_ttl()` (setting `keyword_cache_ttl_seconds`), com `_CACHE_TTL = 300.0` como fallback.
- **Como foi feito / decisões:** `_ttl()` faz late import de `_config`, coage para `float` e devolve o default quando o valor é não numérico/≤0 (fail-safe). `_CACHE_TTL` mantido como constante de fallback (P3).
- **Problemas / pendências:** não há invalidação de cache ao salvar (por design — o novo TTL vale na próxima leitura; `clear_cache()` segue disponível).
- **Verificação:** `py_compile` OK.

---

### Fase C1 — Extrair a triagem para `triage.py`
**Objetivo:** ter a lógica de match + resolução de oferta/agente como funções puras, reusáveis pelo filter (e testáveis sem tocar no bus). `[bloqueia: C2]`
**Itens:**
- `[sequencial]` Criar `storages/plugins/vendas_ia/triage.py` movendo de [events.py](../storages/plugins/vendas_ia/events.py):
  - `match_keyword(text, ofertas) -> (oferta, kw) | None` (era `_match_keyword`; substring/`;`-split/primeiro vence — D3, sem alteração de regra).
  - `ai_in_command(contact_id, conv) -> bool` (era `_ai_in_command`).
  - `resolve_keyword_offer(phone, text) -> {oferta, kw, conv} | None` — encapsula: `nexus_db.is_configured()` → `fetch_ofertas_ativas()` → `match_keyword` → `contact_repo.get_by_phone` → `conversation_repo.get_open_for_contact_scoped`/`get_open_for_contact`. **Sem** efeitos colaterais (só leitura), para o filter decidir.
- `[sequencial]` `events.py` passa a importar de `triage` (o `on_tool_after` não usa `_match_keyword`, mas mantém coerência de imports).
**Pronto quando:** teste unitário de `match_keyword` cobre substring, case-insensitive, `;`-split e primeiro-vence; import de `triage` não quebra o load do plugin.

#### Status de execução — Fase C1
**Estado:** ✅ Concluída
- **O que foi feito:** criado `triage.py` com `match_keyword` (era `_match_keyword`), `ai_in_command` (era `_ai_in_command`) e `resolve_keyword_offer` (leitura pura).
- **Como foi feito / decisões:** regra de match preservada byte-a-byte (D3). `resolve_keyword_offer` aceita um `contact` opcional para usar `get_open_for_contact_scoped` (canal do turno) e cai em `get_open_for_contact` sem ele.
- **Problemas / pendências:** o filter (C2) acabou usando `triage.match_keyword` direto (com as ofertas já buscadas) em vez de `resolve_keyword_offer`, para não re-resolver a conversa; `resolve_keyword_offer` permanece para reuso/teste.
- **Verificação:** testes unitários de `match_keyword` verdes (substring/case/`;`/primeiro-vence/blank).

---

### Fase C2 — `filters.py` com `filter.agent.resolve`
**Objetivo:** fixar oferta + forçar comercial **de forma síncrona antes do turno**, no match e só quando a conversa ainda não tem agente vinculado. `[depende de: C1]`
**Itens (todos `[sequencial]` — é um handler só):**
- Criar `storages/plugins/vendas_ia/filters.py` exportando `FILTERS = {"filter.agent.resolve": on_resolve_agent}` (prioridade default 100; sem necessidade de tupla).
- Assinatura: `def on_resolve_agent(ctx, spec):` (pode ser sync — `apply_filter` aceita sync ou async; [plugins/events.py:543-546](../plugins/events.py#L543)).
- Fluxo do handler:
  1. Guarda de config/no-op: se `_config.setting("keyword_enabled", True)` for falso **ou** `not nexus_db.is_configured()` → `return spec` (sem swap, sem log de abort). ⚠️ **Nunca `return None`** (D2/§2.2 gotcha).
  2. Ler extras: `phone = ctx.extras.get("phone")`, `channel_id = ctx.extras.get("channel_id")`, `contact_id = ctx.extras.get("contact_id")`. Sem `phone` → `return spec`.
  3. **Gate (D2):** resolver a conversa do canal e checar `active_agent_key`. Se já houver agente vinculado (não vazio) → `return spec` (no-op barato: **não** consulta o Nexus). Reconstruir `contact = ContactMemory(phone, channel_id=channel_id)` e usar `conversation_repo.get_open_for_contact_scoped(contact)`; se não há conversa aberta → `return spec`.
  4. Guarda "IA no comando" (`triage.ai_in_command`) — defesa em profundidade → se falso, `return spec`.
  5. Triagem: `hit = triage.resolve_keyword_offer(...)` (ou usar as ofertas já buscadas). Sem hit → `return spec`.
  6. No hit: `target = (_config.setting("keyword_target_agent_key") or "comercial").strip()`.
     - Se `agent_repo.get(target)` é `None`/disabled → **não** vincular agente (evita `active_agent_key` órfão), mas **ainda** fixar a oferta (`state.set_offer(...)`) e `return spec` (o roteador segue, mas com oferta fixada — degradação suave). Logar warning como o handler antigo.
     - Caso ok: `state.set_offer(conv_id, offercode=..., offer_name=..., offer_id=..., matched_keyword=kw)` → `conversation_repo.set_agent(conv_id, target)` → reconstruir o spec do comercial: `new_spec = agent_factory.build_for_contact(ctx.handler, contact)` e `return new_spec` (⚠️ **não** re-dispara o filter — §2.2 gotcha). Se `ctx.handler` for `None` (chamadas de teste), fixar a oferta e `return spec` (o `set_agent` já garante o comercial no próximo turno).
  7. `try/except` amplo: qualquer falha → logar em debug e `return spec` (filter nunca trava o turno; exceção já é isolada por `apply_filter`, mas retornar o spec é mais explícito).
- **A confirmar em C2:** que `build_for_contact(ctx.handler, ContactMemory(...))`, após `set_agent(target)`, resolve mesmo o comercial (via `resolve_active_agent_key` → `get_open_for_contact_scoped`). Validar em teste/manual (a conversa reconstruída precisa bater o mesmo canal).
**Pronto quando:** numa conversa nova sem agente, mensagem contendo a keyword faz o turno rodar como **comercial** (não roteador), com a oferta já fixada em `plugin_vendas_ia_conversa`/`custom_attributes`, e o system prompt do turno contém o bloco "OFERTA EM FOCO".

#### Status de execução — Fase C2
**Estado:** ✅ Concluída
- **O que foi feito:** criado `filters.py` com `FILTERS = {"filter.agent.resolve": on_resolve_agent}`. Fluxo: guarda de config/is_configured → extras (phone/channel_id) → reconstrói `ContactMemory` → `get_open_for_contact_scoped` → **gate** (`active_agent_key` vazio, ANTES do Nexus) → `ai_in_command` → lê a última msg do usuário → `match_keyword` → fixa oferta + `set_agent(comercial)` + rebuild via `build_for_contact`.
- **Como foi feito / decisões:** **descoberta na execução** — os `ctx.extras` de `filter.agent.resolve` NÃO carregam o texto da mensagem (só phone/contact_id/channel_id). Como a msg já está salva antes de `_resolve_agent_spec`, o texto é lido do DB via `message_repo.get_last_user_message(contact.id, conversation_id=conv_id)`. Nunca retorna `None` (sempre o `value`/spec) — §2.2. `build_for_contact` não re-dispara o filter (sem recursão). Agente ausente/disabled ⇒ fixa só a oferta (degradação suave). `ctx.handler is None` ⇒ retorna spec (set_agent já garante o comercial no próximo turno).
- **Problemas / pendências:** —
- **Verificação:** testes de gate (no-op sem tocar Nexus), swap (retorna spec do comercial + `set_agent` chamado), agente ausente (fixa só oferta), sem-match e keyword-off — todos verdes.

---

### Fase C3 — Remover a triagem do evento `message.saved`
**Objetivo:** eliminar o caminho racy (D6). `[depende de: C2]`
**Itens (`[sequencial]`):**
- Em [events.py](../storages/plugins/vendas_ia/events.py), remover a entrada `"message.saved": on_message_saved` de `EVENT_HANDLERS`; manter `"tool.after": on_tool_after` e `"app.startup": on_startup`.
- Apagar `on_message_saved` (e helpers `_match_keyword`/`_ai_in_command` se totalmente migrados para `triage.py`). Ajustar o docstring do módulo.
**Pronto quando:** `EVENT_HANDLERS` não referencia mais keyword; `on_tool_after`/`on_startup` seguem funcionando; nenhum import quebrado.

#### Status de execução — Fase C3
**Estado:** ✅ Concluída
- **O que foi feito:** `events.py` reescrito: `EVENT_HANDLERS` agora só tem `tool.after` e `app.startup`; `on_message_saved`/`_match_keyword`/`_ai_in_command` removidos (migrados para `triage.py`). Docstring do módulo atualizado.
- **Como foi feito / decisões:** imports enxugados (`agent_repo`/`tag_repo` saíram; `on_tool_after` usa só `contact_repo`/`conversation_repo`/`nexus_db`/`state`).
- **Problemas / pendências:** —
- **Verificação:** grep confirma que nenhuma referência a `on_message_saved`/`_match_keyword`/`message.saved` sobra fora de docstrings; `py_compile` OK.

---

### Fase C4 — Wiring do manifest
**Objetivo:** registrar o filter no load do plugin. `[depende de: C2]`
**Itens (`[sequencial]`):**
- Em [plugin.yaml](../storages/plugins/vendas_ia/plugin.yaml) `entry`, adicionar `filters: filters` (junto de `events`/`prompts`/`settings`/`routes`). Atualizar o comentário do `entry` explicando o novo `filters`.
- Bump de versão (`1.1.0` → `1.2.0`) e nota no docstring/README interno se houver.
**Pronto quando:** ao reiniciar (toggle do plugin ou restart do server), o log mostra o filter `filter.agent.resolve` registrado para `vendas_ia`, sem `load_error`.

#### Status de execução — Fase C4
**Estado:** ✅ Concluída
- **O que foi feito:** `plugin.yaml`: adicionado `filters: filters` ao `entry` (com comentário), atualizados os comentários de `events`/`settings`, e bump de versão `1.1.0` → `1.2.0` (P4).
- **Como foi feito / decisões:** confirmado no `plugins/loader.py` (`_ENTRY_SPECS`/`_entry_filters`) que `entry.filters` → módulo `filters` → dict `FILTERS` é registrado no load, simétrico a events/prompts.
- **Problemas / pendências:** o filter só passa a valer após restart do plugin (toggle/reload) — em dev o `--reload` cobre.
- **Verificação:** wiring confirmado por leitura do loader; validação de load no boot fica para D2 (manual).

---

### Fase D1 — Testes automatizados
**Objetivo:** blindar match, gate e swap. `[paralelo com D2]`
**Itens:**
- `[paralelo]` Unit puro de `triage.match_keyword` (substring/case/`;`/primeiro-vence).
- `[paralelo]` Teste do gate: com `active_agent_key` já setado, o filter retorna o **mesmo** spec e **não** consulta o Nexus (mockar `nexus_db.fetch_ofertas_ativas` e assertar não-chamada).
- `[paralelo]` Teste do swap: com Nexus mockado retornando uma oferta cuja keyword casa e o agente `comercial` semeado, `on_resolve_agent` fixa a oferta (checar `state.get_state`) e retorna um spec com `agent_key == "comercial"`; e `conversation_repo.set_agent` foi chamado.
- **Onde:** novo arquivo de teste do plugin (seguir o padrão existente do repo — ver `tests/test_seed_ai_active_per_channel.py` como exemplo de teste focado; usar o Postgres de teste via `tests/pg.py`). Registrar deps do bus (`plugins.events.reset()` + registrar o FILTERS do plugin) conforme `tests/test_events_filters.py`.
**Pronto quando:** os novos testes passam no Postgres de teste (`WHATSBOT_TEST_DB_URL`); `tests/test_endpoints.py` segue verde.

#### Status de execução — Fase D1
**Estado:** ✅ Concluída
- **O que foi feito:** criado `storages/plugins/vendas_ia/tests/test_triage_filter.py` (11 testes) + `tests/conftest.py` do plugin (bootstrap de sys.path). Cobre `match_keyword` (5), gate (2), swap (1), agente ausente (1), sem-match (1), keyword-off (1).
- **Como foi feito / decisões:** **desvio do plano** — `build_test_app`/`plugin_app` do core NÃO servem para `vendas_ia` (ele não está em `assets/plugin_examples/`, é distribuído por zip; e o conftest de `tests/` não é ancestral do dir do plugin, então suas fixtures não alcançam). Optei por testes **DB-free** com `monkeypatch` de todas as dependências (repos, `nexus_db`, `state`, `ContactMemory`, `agent_factory`) — determinísticos e rodam em qualquer lugar sem o Postgres de teste. Ficam no dir do plugin (coletados pela mecânica de discovery de plugin do `tests/conftest.py`), então não deixam o `tests/` do core vermelho em CI (o plugin não existe lá).
- **Problemas / pendências:** o gate/swap não exercitam DB real (mockados); a prova fim-a-fim com DB fica em D2.
- **Verificação:** `venv/bin/python -m pytest storages/plugins/vendas_ia/tests/test_triage_filter.py -q` → 11 passed. Core inalterado (nenhum arquivo de `agent/`/`app/`/`server/`/`db/` tocado), então `tests/` do core não é afetado.

---

### Fase D2 — Validação manual no painel
**Objetivo:** provar o fim-a-fim. `[paralelo com D1]`
**Itens:**
- `[paralelo]` Com Nexus + chave configurados e o comercial semeado, abrir conversa nova e enviar `"quero informações de <KEYWORD>"`. Esperado: **sem** card de `transferir_agente` do roteador; a IA responde já como comercial; **sem** `pesquisar_ofertas`; a oferta aparece no painel (atributo `oferta_atual`).
- `[paralelo]` Enviar mensagem sem keyword em conversa nova → roteador funciona normal (regressão OK).
- `[paralelo]` Conversa já no comercial + nova mensagem → filter é no-op (sem custo/sem reconsulta ao Nexus).
- `[paralelo]` Desativar o plugin → turno resolve o agente como sempre (sem latência, sem erro).
**Pronto quando:** os quatro cenários acima batem o esperado.

#### Status de execução — Fase D2
**Estado:** ⏳ Pendente (validação manual do usuário)
- **O que foi feito:** —
- **Como foi feito / decisões:** requer Nexus + chave configurados, o comercial semeado e o servidor rodando — validação manual no painel (não automatizável aqui).
- **Problemas / pendências:** a fazer pelo usuário: (1) conversa nova + msg com keyword → cai no comercial sem card do roteador e sem `pesquisar_ofertas`; (2) msg sem keyword → roteador normal; (3) conversa já no comercial → filter no-op; (4) plugin desativado → resolução normal.
- **Verificação:** —

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Retorno `None` do filter | Loga "aborted" e é lido como "sem swap" — confuso e ruidoso | No-op **sempre** retorna o `value` (spec) inalterado, nunca `None` (§2.2). |
| Recursão no rebuild | Chamar `build_for_contact` dentro do filter re-disparar o filter | `build_for_contact` **não** aplica `filter.agent.resolve` (só `_resolve_agent_spec` aplica) — sem loop (§2.2 verificado). |
| `active_agent_key` órfão | Vincular um `comercial` inexistente/disabled trava o atendimento | Só `set_agent` se `agent_repo.get(target)` existe e enabled; senão fixa só a oferta e mantém o default (regra herdada do handler antigo). |
| Custo por mensagem | Filter rodar em toda mensagem pesaria | Gate por `active_agent_key` vazio **antes** de tocar o Nexus: conversa já atribuída = retorno imediato (D2). |
| `ContactMemory` reconstruído no canal errado | Resolver a conversa de outro canal | Passar `channel_id` dos extras ao construir `ContactMemory`; usar `get_open_for_contact_scoped` (mesma função de `resolve_active_agent_key`). Validar em C2. |
| Latência para quem não tem o plugin | Regressão de performance global | `apply_filter` retorna na hora quando não há filter registrado ([plugins/events.py:525-527](../plugins/events.py#L525)); zero custo (D5). |
| TTL inválido salvo pelo usuário | Cache quebra / divisão por janela negativa | `_ttl()` coage não numérico/≤0 para o default 300 (B2). |
| Restart de plugin | O filter só passa a valer após restart (toggle/`plugin.yaml`) | Documentar no "Pronto quando" de C4; em dev o `--reload` cobre; em Docker o supervisor relança. |
| Segredos | `nexus_dsn`/`openrouter_api_key` em log/URL | Sem mudança nesse eixo; o TTL não é segredo. Manter os campos password como estão. |
| Postgres (único backend) | Testes assumindo SQLite | Usar `tests/pg.py` (schema recriado por processo) e `WHATSBOT_TEST_DB_URL`. |

---

## 7. Perguntas em aberto

- **P1 — Keyword deve re-fixar a oferta mesmo depois que o comercial já assumiu?**
  ✅ DECIDIDO (2026-07-08): **Não** nesta entrega. O gate é "só enquanto sem agente vinculado" (D2), que também evita custo por mensagem. Se no futuro quiser re-fixar só a **oferta** (barato, sem trocar agente) mid-conversa, é um incremento isolado no mesmo filter (ramo "agente já vinculado → só `set_offer`"). Deixado como extensão.

- **P2 — `triage.py` separado ou lógica embutida em `filters.py`?**
  ✅ DECIDIDO (2026-07-08): módulo `triage.py` separado (a) para teste unitário sem o bus e (b) porque `events.on_tool_after` e um eventual re-fix (P1) reusam as mesmas funções. Custo baixo.

- **P3 — Manter `_CACHE_TTL` como constante de fallback?**
  ✅ DECIDIDO (2026-07-08): sim. `_CACHE_TTL = 300.0` vira o **fallback** de `_ttl()` (setting ausente/ inválido). Mantém compat e fail-safe.

- **P4 — Bump de versão do plugin.**
  ⏸️ ADIADO para a execução: sugerido `1.1.0 → 1.2.0` (feature aditiva). Confirmar convenção de release do plugin no momento de empacotar o `.zip`.

- **P5 — Precisamos de caracterização automatizada (A0)?**
  ✅ DECIDIDO (2026-07-08): opcional. O caminho antigo depende de I/O do Nexus e timing, difícil de tornar determinístico; a prova de regressão positiva vem dos testes de D1 (swap ocorre) + validação manual D2. Pular A0 se o custo não compensar.

---

## 8. Apêndice — arquivos-chave

**Plugin `vendas_ia` (tudo aqui):**
- `storages/plugins/vendas_ia/settings.py` — campo `keyword_cache_ttl_seconds` (B1)
- `storages/plugins/vendas_ia/_config.py` — default do TTL (B1)
- `storages/plugins/vendas_ia/nexus_db.py` — `_cached` lê TTL (B2); `fetch_ofertas_ativas` (leitura)
- `storages/plugins/vendas_ia/triage.py` — **novo**: match/guarda/resolução puros (C1)
- `storages/plugins/vendas_ia/filters.py` — **novo**: `filter.agent.resolve` (C2)
- `storages/plugins/vendas_ia/events.py` — remover keyword do `EVENT_HANDLERS` (C3)
- `storages/plugins/vendas_ia/plugin.yaml` — `entry.filters: filters` + bump (C4)
- `storages/plugins/vendas_ia/state.py` — `set_offer` (reuso, sem mudança)
- `storages/plugins/vendas_ia/prompts.py` — `oferta_em_foco_fragment` (reuso, sem mudança)
- `tests/…` — **novo** arquivo de teste do plugin (D1)

**Core (apenas referência — NÃO editar):**
- `app/services/agent_run_service.py:42-63,304` — onde `filter.agent.resolve` é aplicado
- `plugins/events.py:401-560` — `emit_with_filter`/`emit`/`apply_filter`
- `agent/agent_factory.py:71,204-361` — `AgentSpec`/`resolve_active_agent_key`/`build_for_contact`
- `agent/memory.py:91-98` — `ContactMemory`
- `db/repositories/conversation_repo.py:232,535` — `get_open_for_contact_scoped`/`set_agent`
- `db/repositories/agent_repo.py:71` — `agent_repo.get`

---

## 9. Checklist de verificação

- [ ] `GET /api/plugins/vendas_ia/settings` mostra `keyword_cache_ttl_seconds` (default 300); salvar persiste `plugin.vendas_ia.keyword_cache_ttl_seconds`
- [ ] TTL curto (ex.: 5s) faz `fetch_ofertas_ativas` reconsultar após o intervalo; TTL inválido cai em 300s sem erro
- [ ] Conversa nova + mensagem com keyword → turno roda como **comercial** (sem card do roteador, sem `pesquisar_ofertas`); oferta fixada + "OFERTA EM FOCO" no prompt
- [ ] Conversa nova + mensagem sem keyword → roteador normal (sem regressão)
- [ ] Conversa já no comercial → filter no-op, **sem** consulta ao Nexus
- [ ] Plugin desativado → resolução de agente normal, sem latência/erro
- [ ] `EVENT_HANDLERS` sem keyword; `on_tool_after`/`on_startup` intactos
- [ ] Filter registrado no boot sem `load_error` (checar após restart do plugin)
- [ ] Testes novos verdes no Postgres (`WHATSBOT_TEST_DB_URL`); `tests/test_endpoints.py` verde
- [ ] Nenhum arquivo do core (`agent/`, `app/`, `server/`, `db/`) modificado
- [ ] Nenhum segredo em log/URL; campos `password` intactos
- [ ] `.zip` do plugin regerado para distribuição (a triagem chega por zip, não pelo core — ver memória do projeto)
