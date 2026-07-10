# Plano 47 — Toggle global por-tool "reaproveitar a resposta da tool para a IA entre turnos"

> **Status:** PLANEJAMENTO · **Data:** 2026-07-10 · **Escopo:** pequeno/médio (1 coluna nova em `tool_overrides` + 1 migration + 3 funções de repo + 1 config key global + 2 campos de UI + a lógica de reinjeção no `tool_memory` · retrocompatível por default).
> **Origem:** investigação nesta sessão (workflow multi-agente de trade-offs) sobre "a IA re-consulta uma tool cujo resultado ela já tinha". Motivação concreta: no chat OFERTAX, a IA chamou `pesquisar_perguntas_frequentes` (cujo resultado já continha a resposta do MEC) e, no turno seguinte, chamou **de novo** para responder "é reconhecido pelo MEC?". Hoje o resultado de uma tool é descartado ENTRE turnos de propósito ([tool_memory.py:53](../agent/tool_memory.py#L53) dá `break` na linha `→`). **Método:** leitura do código real + `grep` exaustivo + workflow de 7 investigadores + crítica adversarial nesta sessão. Todo `arquivo:linha` abaixo foi **verificado**.
> **O quê/por quê:** dar ao operador um **botão por-tool** ("reaproveitar a resposta desta tool para a IA nas próximas mensagens") na tela **AI Engine → Tools**, **padrão DESLIGADO**. Tool marcada ⇒ o `tool_memory` reinjeta o RESULTADO (não só nome+args) no bloco compacto do turno seguinte, então a IA responde follow-ups sem re-consultar. É **global no core** (vale para toda tool registrada — core, plugin, code-in-DB), **não vinculado a nenhum plugin** e **sem regras diferentes por tipo de tool** (o toggle aparece igual em TODAS — D8). O tamanho máximo do resultado reaproveitado é um **campo único global** em Configurações da IA (D9). Ex.: `pesquisar_perguntas_frequentes` = LIGADO (FAQ estática, a IA lembra); `pesquisar_ofertas` = DESLIGADO (preço muda, sempre consulta fresco).
>
> **Como usar este plano:** ao executar cada fase, preencha o bloco **"Status de execução"** dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro. **Verde a cada fase.** **Caracterização (default-off byte-idêntico) ANTES** de tocar no `tool_memory`. **Um refactor por commit.** As waves marcam o que roda em paralelo (🟢) e o que é sequencial/bloqueante (🔴).

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** ✅ (2026-07-10) | **Flag GLOBAL por-tool no core**, não per-plugin, não per-canal, não per-agente. O usuário pediu "de forma global no sistema, eu escolho o que eu quiser, não vinculado a nenhum plugin". | Nova coluna `tool_overrides.reuse_result` ([db/tables.py:597-606](../db/tables.py#L597)). A row de `tool_overrides` já é criada pra TODA tool registrada (core+plugin+code-in-DB) via `tool_override_repo.ensure` — então o botão aparece pra todas automaticamente. |
| **D2** ✅ (2026-07-10) | **Padrão DESLIGADO (`reuse_result=0`).** Sem nada marcado, o comportamento é **byte-idêntico** ao de hoje (só nome+args reinjetados). | `server_default="0"` na coluna; `ensure` insere `reuse_result: 0`. Zero regressão; o ganho é opt-in. |
| **D3** ✅ (2026-07-10) | **A reinjeção vive DENTRO do `tool_memory`** ([agent/tool_memory.py](../agent/tool_memory.py)), não num caminho novo. O bloco já é conversation-scoped, hoistado pro `system_message`, herdado por todos os hops de routing e gated por `ai_tool_memory_enabled`. | Só muda `build_block`/`_tool_signature`. **Reusa** o kill-switch `ai_tool_memory_enabled` ([config/settings.py:159](../config/settings.py#L159)) — OFF desliga memória E reuse. Sem kill-switch novo. |
| **D4** ✅ (2026-07-10) | **A matéria-prima já existe.** O card `role='tool_call'` guarda o resultado COMPLETO em `messages.content` (`🔧 nome\nk: v\n→ resultado`, [messaging_service.py:476-479](../app/services/messaging_service.py#L476)), lido por `get_tool_calls_by_conversation` ([message_repo.py:177](../db/repositories/message_repo.py#L177)) que NÃO filtra `tool_call`. | Nenhuma tabela/coluna nova em `messages`. A reinjeção lê o card e mantém a linha `→` (em vez de dar `break`) para as tools marcadas. |
| **D5** ✅ (2026-07-10) | **`reuse_result` NÃO afeta o schema enviado ao LLM.** É política de contexto, não descrição/parâmetro. | `refresh_tool_overrides` ([tool_registry.py:231](../agent/tool_registry.py#L231)) **não muda** (continua só mexendo em `description`/`enabled`). Só `list_tools` passa a expor o campo pra UI. |
| **D6** ✅ (2026-07-10) | **Fail-open + caps agressivos.** O resultado reintroduzido é scrubado de base64, truncado por-resultado, e **degradado** (só o resultado MAIS RECENTE de cada tool marcada entra full; ocorrências antigas caem pra nome+args). | Herda o padrão do `tool_memory` (best-effort, `try/except` → sem bloco) e da degradação de imagem ([memory.py:476-497](../agent/memory.py#L476)). Nunca deixa a IA muda nem incha o prompt sem teto. |
| **D7** ✅ (2026-07-10) | **Reaplicar a lista-negra de histórico** (`history_filter`, plano 43) sobre o resultado reintroduzido. | Um resultado que casa `ai_history_exclude_patterns` (ex.: `Protocolo aberto · PROT-…`) tem a linha `→` cortada mesmo com a tool marcada — o reuse não fura o filtro do operador. |
| **D8** ✅ (2026-07-10) | **Toggle UNIFORME em toda tool — SEM trava/denylist** (resolve P1). O usuário quer poder ligar/desligar em qualquer tool "para não ter regras diferentes no sistema" (ainda que não vá ligar nas que executam ações). | **Nenhuma** denylist, `reusable` flag, bloqueio no PUT nem filtragem no `build_block`. O toggle aparece e funciona igual em TODAS as tools. Um **aviso genérico** (não por-tool) no texto de ajuda alerta que reusar resultado de tools que *executam ações* não costuma fazer sentido — informativo, sem enforcement. |
| **D9** ✅ (2026-07-10) | **Tamanho do resultado reaproveitado = 1 campo GLOBAL** em Configurações da IA, valendo para TODAS as tools por enquanto (resolve P2). | Nova `ConfigKey("ai_tool_reuse_result_max_chars", default=800)` ([config/settings.py](../config/settings.py)), editável em [GeneralSettings.js](../web/static/js/components/ai/GeneralSettings.js) (mesma tela do `ai_history_exclude_patterns`). O `tool_memory` lê esse valor como o cap por-resultado. |
| **Princípio fixo** | O `tool_memory` roda em TODO turno (sync e async) e é herdado por todos os hops de routing. ⇒ a mudança tem que ser **barata** e **fail-open**, e **não** pode ligar `add_history_to_context` do AGNO (trava CLAUDE.md / plano 37 §2.4 — WhatsBot é dono do contexto). | A reinjeção continua sendo um bloco `system` compacto, não histórico nativo do AGNO. |

---

## 1. Resumo executivo

Hoje, entre turnos, o resultado de uma tool é descartado em 3 pontos: o motor AGNO roda stateless ([agno_engine.py:369-381](../agent/agno_engine.py#L369)), o histórico do LLM exclui `role='tool_call'` ([message_repo.py:128-129](../db/repositories/message_repo.py#L128)), e o bloco `tool_memory` reinjeta só NOME+ARGS, dando `break` na linha `→` ([tool_memory.py:53](../agent/tool_memory.py#L53)). Resultado: a IA "sabe que chamou X" mas não "o que X respondeu", e re-consulta.

A solução: um **botão por-tool** `reuse_result` (nova coluna em `tool_overrides`, **default 0**) na tela **AI Engine → Tools**, **uniforme em todas as tools** (D8). Para as tools marcadas, o `tool_memory` passa a **manter a linha `→` (o resultado)** no bloco compacto — scrubado, truncado (por um **tamanho global configurável** em Configurações da IA, D9), degradado (só o mais recente por tool) e re-filtrado pela lista-negra de histórico. Tools não marcadas ⇒ comportamento atual byte-idêntico. Reusa o kill-switch `ai_tool_memory_enabled` e todo o encanamento de override que já existe (`tool_overrides` + `/api/tools` + tela Tools).

---

## 2. Como funciona hoje (mapa) — `arquivo:linha` verificado

### 2.1 A cadeia do resultado de tool

```
DENTRO do turno (o modelo VÊ o resultado — por isso o turno 1 funciona):
  agno_engine.run_async/run_sync → runner.arun(input=convo)          [agno_engine.py:570 / :630]
    entrypoint da tool → handler._dispatch_tool(...) → feedback       [agno_engine.py:248]
    AGNO injeta o feedback como Message role='tool' e re-invoca o modelo  (loop interno)

ENTRE turnos (o resultado é DESCARTADO em 3 pontos independentes):
  (1) motor stateless: Agent novo por msg, run_output.messages jogado fora  [agno_engine.py:369-381]
  (2) histórico do DB exclui role='tool_call'                                [message_repo.py:128-129]
  (3) tool_memory reinjeta nome+args, DÁ BREAK na linha "→"                  [tool_memory.py:53]

Persistência do resultado (a matéria-prima existe):
  messaging_service.broadcast_tool_calls monta o card "🔧 nome\nk: v\n→ result"  [messaging_service.py:466-479]
    → contact.add_message("tool_call", content)   (resultado COMPLETO, não truncado)  [messaging_service.py:490-491]
```

### 2.2 Montagem do bloco `tool_memory` (o ponto de mudança)

```
agent_run_service.aprocess_message
  context_messages = contact.get_context_messages(eff_max_context)          [agent_run_service.py:280]
  _mem_block = tool_memory.build_block(contact)                             [agent_run_service.py:292]
    conv = conversation_repo.get_open_for_contact_scoped(contact)          [tool_memory.py:84]  (conversation-scoped ✓)
    for card in message_repo.get_tool_calls_by_conversation(conversation_id):  [tool_memory.py:93]
        sig = _tool_signature(card["content"])   # ← DÁ BREAK no "→", descarta resultado  [tool_memory.py:41-67]
    # + atributos já definidos (re-lidos AO VIVO do repo, não cacheados)    [tool_memory.py:104-119]
  context_messages += [{"role":"system","content":_mem_block}]             [agent_run_service.py:293-295]
  messages = [{"role":"system", "content": system_prompt_str}, *context_messages]  [agent_run_service.py:328-331]
```

- **Caps atuais do bloco**: `MAX_TOOL_LINES=12`, `ITEM_MAX_CHARS=160`, `TOOL_MEMORY_MAX_CHARS=1200` ([tool_memory.py:27-29](../agent/tool_memory.py#L27)); scrub de base64 `_BASE64_RE` ([tool_memory.py:33](../agent/tool_memory.py#L33)); header `_HEADER` ([tool_memory.py:34](../agent/tool_memory.py#L34)).
- **`get_tool_calls_by_conversation`** devolve oldest→newest, sem filtrar `tool_call` ([message_repo.py:177-196](../db/repositories/message_repo.py#L177)).
- **Hoisting (multi-agente)**: `split_messages` do AGNO concatena TODO `role='system'` (inclusive o bloco no tail) num único `system_message` ([agno_engine.py:189-213](../agent/agno_engine.py#L189)); o bloco é herdado idêntico por todos os hops de routing e pelo forced-followup ([agno_engine.py:512-530](../agent/agno_engine.py#L512)).

### 2.3 O encanamento de override (o que reusamos de graça)

| Peça | `arquivo:linha` | Papel |
|---|---|---|
| Tabela `tool_overrides` | [db/tables.py:597-606](../db/tables.py#L597) | `name` (PK), `plugin_id`, `enabled`, `description`, `display_label`, `updated_at` |
| `ensure(name, plugin_id, …)` | [tool_override_repo.py:51-77](../db/repositories/tool_override_repo.py#L51) | cria a row default na 1ª vez que a tool registra (core+plugin) |
| `upsert_override(name, …)` | [tool_override_repo.py:80-106](../db/repositories/tool_override_repo.py#L80) | update parcial via sentinel `_UNSET` |
| `get` / `list_all` | [tool_override_repo.py:29 / :37](../db/repositories/tool_override_repo.py#L29) | `select(tool_overrides)` (pega a coluna nova automaticamente) |
| PUT `/api/tools/{name}` | [server/routes/tools.py:30-74](../server/routes/tools.py#L30) | monta `update_kwargs` só com as keys presentes no body → `upsert` → `refresh_tool_overrides` → WS `tools_changed` |
| `list_tools()` | [tool_registry.py:263-291](../agent/tool_registry.py#L263) | merge do override no dict que a UI consome |
| `refresh_tool_overrides()` | [tool_registry.py:231-261](../agent/tool_registry.py#L231) | reconstrói `_tool_schemas` (só `enabled`/`description`) |
| Tela Tools | [ToolsUnified.js](../web/static/js/components/ai/ToolsUnified.js) + `EditModal` [ToolsManager.js:10](../web/static/js/components/ToolsManager.js#L10) | lista + modal de edição (rótulo/descrição/toggle Ativa) |
| Config global da IA | [GeneralSettings.js](../web/static/js/components/ai/GeneralSettings.js) (`populate` :46, `handleSave` :101) | tela que já hospeda `ai_history_exclude_patterns` (plano 43) — home do campo de tamanho (D9) |

### 2.4 Tools side-effecting (contexto — NÃO enforced, ver D8)

- Core (4, todas write/handoff): `save_contact_info`, `set_custom_attribute`, `transfer_to_human`, `transferir_agente`.
- Plugin conhecido: `reminder_create` (INSERT de lembrete).
- Reusar o "resultado" dessas engana o modelo a crer que já agiu — por isso o **texto de ajuda** avisa. Mas, por **D8**, o sistema **não bloqueia**: o toggle é uniforme e o operador decide. Não há classificação `read_only`/`side_effecting` no código (nem se cria uma) — mantém "sem regras diferentes".

### Falsos positivos descartados

| "Parece que precisa mexer" | Por que NÃO |
|---|---|
| `refresh_tool_overrides` ([tool_registry.py:231](../agent/tool_registry.py#L231)) | `reuse_result` não vai ao LLM como schema (D5). Continua só com `enabled`/`description`. |
| `message_repo.get_context` lista-negra ([message_repo.py:128](../db/repositories/message_repo.py#L128)) | O reuse NÃO reintroduz o card `tool_call` no histórico cru — passa pelo `tool_memory` (bloco compacto). A lista-negra fica como está. |
| Novo schema/coluna em `messages` | O resultado completo já está em `messages.content` do card (D4). Reparsear o card basta. |
| Denylist / classificação de tools | Removido por D8 (toggle uniforme, sem enforcement). |
| Message `role='tool'` nativas ao AGNO | Exige pareamento `tool_call_id` (schema novo) — adiado no plano 43 P6. Fora de escopo (ver §7). |
| `improvement_service` (análise "Gerar melhoria") | Consome `get_context*`, não o `tool_memory`. O reuse não o toca. |

---

## 3. Mudanças de dados / config

| Item | Onde | Detalhe |
|---|---|---|
| Coluna nova | [db/tables.py:597-606](../db/tables.py#L597) | `Column("reuse_result", Integer, nullable=False, server_default="0")` |
| Migration | `db/alembic/versions/20260710_0047_tool_reuse_result.py` | `revision="0047_tool_reuse_result"` (22 chars ≤32 ✓), `down_revision="0046_message_agent_key"`. `upgrade`: `op.add_column("tool_overrides", sa.Column("reuse_result", sa.Integer(), nullable=False, server_default="0"))`. `downgrade`: `op.drop_column`. Aditivo — rows existentes nascem `0`. |
| Config key (D9) | [config/settings.py:159](../config/settings.py#L159) (junto ao `ai_tool_memory_enabled`) | `ConfigKey("ai_tool_reuse_result_max_chars", default=800, exposed=True, writable=True)` — tamanho máx. (chars) do resultado reaproveitado, por-resultado, global. |

---

## 4. Fases / Roadmap

### Diagrama de dependências (waves)

```
WAVE 0   A0 (DB + repo, ENABLER) ─────────────────────────────  🔴 faça sozinha primeiro
             │ (barreira: A0 libera todo o resto)
WAVE 1   B0 (tool_memory)  ·  A1 (surface: API + config key)  ·  D0 (frontend UI)   🟢 paralelo
             │                     │                                │
             └──────── barreira ───┴────────────────────────────────┘
WAVE 2   E0 (testes)                                              🔴 fecha tudo
```

### Tabela de fases

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **A0** | DB + `tool_override_repo` | 🔴 (enabler) | baixo | migration sobe/desce; `ensure` grava `reuse_result=0`; `reuse_enabled_names()` retorna set |
| 1 | **B0** | `agent/tool_memory.py` | 🟢 [dep A0] | médio | tool marcada ⇒ bloco inclui `→ resultado` (capado pelo config/scrubado); não marcada ⇒ byte-idêntico |
| 1 | **A1** | `tool_registry.list_tools` + `routes/tools.py` + ConfigKey | 🟢 [dep A0] | baixo | `GET /api/tools` traz `reuse_result`; `PUT` persiste `{reuse_result}`; `ai_tool_reuse_result_max_chars` gravável |
| 1 | **D0** | `EditModal`/`ToolsUnified` + `GeneralSettings` | 🟢 [dep A1 contrato] | baixo | toggle uniforme salva/reflete; campo de tamanho salva; modo escuro ok |
| 2 | **E0** | testes | 🔴 (fecha) | baixo | suíte verde no Postgres; casos do §Checklist cobertos |

---

### Fase A0 — Coluna + repo (enabler) 🔴

**Objetivo:** persistir `reuse_result` por-tool e expor a lista de tools marcadas.

**Itens:**
1. [sequencial] Adicionar `Column("reuse_result", Integer, nullable=False, server_default="0")` em [db/tables.py:597-606](../db/tables.py#L597).
2. [sequencial] Migration `20260710_0047_tool_reuse_result.py` (ver §3).
3. [sequencial] `tool_override_repo.ensure` ([tool_override_repo.py:51-77](../db/repositories/tool_override_repo.py#L51)): incluir `"reuse_result": 0` no dict do `upsert_stmt`. **Manter `update_cols=["plugin_id"]`** — rows existentes NÃO regridem (nunca sobrescreve a escolha do usuário no re-registro).
4. [sequencial] `upsert_override` ([tool_override_repo.py:80-106](../db/repositories/tool_override_repo.py#L80)): novo param `reuse_result=_UNSET`; `new_reuse = existing["reuse_result"] if reuse_result is _UNSET else (1 if reuse_result else 0)`; incluir no `.values(...)`. (`get`/`list_all` já pegam a coluna via `select(tool_overrides)`.)
5. [sequencial] Novo helper `reuse_enabled_names() -> set[str]` no repo: `{row["name"] for row in list_all() if row.get("reuse_result")}`. Fail-safe (try/except → `set()`).

**Pronto quando:** `alembic upgrade head` e `downgrade -1` rodam limpos; um `ensure("x", None)` cria row com `reuse_result=0`; `upsert_override("x", reuse_result=True)` seta 1; `reuse_enabled_names()` devolve `{"x"}`.

#### Status de execução — Fase A0
**Estado:** ✅ Concluída (2026-07-10)
- **O que foi feito:** Coluna `reuse_result` (Integer, NOT NULL, `server_default="0"`) em [db/tables.py:604](../db/tables.py#L604); migration `20260710_0047_tool_reuse_result.py` (`revision="0047_tool_reuse_result"`, `down_revision="0046_message_agent_key"`); `ensure` insere `reuse_result: 0` mantendo `update_cols=["plugin_id"]`; `upsert_override` ganhou `reuse_result=_UNSET` (grava só quando passado); novo `reuse_enabled_names() -> set[str]` fail-safe.
- **Como foi feito / decisões:** `new_reuse = existing.get("reuse_result", 0) if _UNSET else (1 if reuse_result else 0)`. Re-registro (`ensure` on-conflict) **não** toca `reuse_result` (só `plugin_id`), então a escolha do usuário nunca regride.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `alembic upgrade head` + `downgrade -1` + `upgrade head` limpos (round-trip). Smoke test: `ensure`→0, `upsert_override(reuse_result=True)`→1, `reuse_enabled_names()`→`{"demo_tool"}`, re-`ensure` não regride, `upsert_override(description=...)` preserva `reuse_result`.

---

### Fase B0 — Reinjeção do resultado no `tool_memory` 🟢 [dep A0]

**Objetivo:** para as tools marcadas, manter o `→ resultado` no bloco compacto (capado pelo config global, scrubado, degradado, re-filtrado).

**Itens:**
1. [sequencial] Em `build_block` ([tool_memory.py:70-139](../agent/tool_memory.py#L70)): carregar `reuse_names = tool_override_repo.reuse_enabled_names()` (fail-open → `set()`) e `result_cap = int(config_repo.get("ai_tool_reuse_result_max_chars", 800))` (fail-open → 800; `RESULT_REINJECT_FALLBACK=800` como const). **Sem** interseção com denylist (D8).
2. [sequencial] Novo parser `_tool_signature_with_result(content, result_cap)` (ou parâmetro em `_tool_signature`): igual ao atual mas, ao chegar no `→`, em vez de `break`, capturar o resultado. Aplicar: (a) `_scrub` (base64 já existe); (b) truncar a `result_cap`; (c) reaplicar `history_filter` (D7): se `history_filter.matches(...)` casar o `→` contra `ai_history_exclude_patterns`, **descartar o resultado** (cai pra nome+args). Formato: `nome(args) → resultado`.
3. [sequencial] No loop de cards ([tool_memory.py:93](../agent/tool_memory.py#L93)): por card, se `card_tool_name in reuse_names` usar o parser-com-resultado; senão o atual (nome+args). **Degradação (D6):** manter o resultado full só para a ocorrência **MAIS RECENTE** de cada tool marcada; ocorrências anteriores caem pra nome+args (dedup por nome já existe via `seen`, [tool_memory.py:94-99](../agent/tool_memory.py#L94); como os cards vêm oldest→newest, iterar ao contrário para "mais recente vence", ou marcar full só o último de cada nome).
4. [sequencial] Ajustar o header/label: hoje é "Ferramentas já executadas … não chame de novo" ([tool_memory.py:126-128](../agent/tool_memory.py#L126)). Quando houver resultado reusado, separar em: "Resultados que você JÁ TEM (use-os, não re-consulte): …" vs a lista atual de nome+args.
5. [sequencial] Teto global do bloco: hoje `TOOL_MEMORY_MAX_CHARS=1200` ([tool_memory.py:29](../agent/tool_memory.py#L29)) e há corte final ([tool_memory.py:134-135](../agent/tool_memory.py#L134)). Como o `result_cap` é configurável, elevar o teto do bloco para caber pelo menos 1 resultado + a lista (ex.: `max(1200, result_cap + 600)`) para o corte final não engolir o resultado recém-incluído.

**Pronto quando:** com `pesquisar_perguntas_frequentes` marcada, o bloco `tool_memory` de um 2º turno contém o texto do resultado da FAQ (truncado no `result_cap`); com ela DESMARCADA, o bloco é byte-idêntico ao de hoje (só nome+args). Teste de caracterização (E0) confirma o default-off.

#### Status de execução — Fase B0
**Estado:** ✅ Concluída (2026-07-10)
- **O que foi feito:** Em [agent/tool_memory.py](../agent/tool_memory.py): novo `_parse_tool_card` (retorna `(name, sig, result)`), `_tool_signature` virou wrapper fino dele (compat com o teste). Helpers fail-open `_reuse_enabled_names()`, `_result_cap()` (guard `max(50,int(cfg))`, fallback `RESULT_REINJECT_FALLBACK=800`), `_history_compiled()`, `_result_blacklisted()`. `build_block` agora monta Seção A ("Resultados que você JÁ TEM…") só com o resultado MAIS RECENTE por tool marcada (degradação D6), Seção B (nome+args) exclui os `promoted_sigs`. Teto do bloco = 1200 quando nada reusado (byte-idêntico), `max(1200, result_cap+600)` quando há Seção A.
- **Como foi feito / decisões:** cards vêm oldest→newest ⇒ `result_by_name[name]` sobrescreve (mais recente vence). `_result_blacklisted` testa `matches("tool_call", result, compiled)` (D7). Scrub de base64 roda no arg E no resultado antes do truncate. Só carrega `_history_compiled()` quando há tool marcada.
- **Problemas / pendências:** nenhuma. (Ao smoke-testar, um payload de 3000 chars contíguos casou o `_BASE64_RE` e foi scrubado — comportamento correto; teste refeito com texto com espaços.)
- **Fix da revisão adversarial (2026-07-10):** a versão inicial gravava `result_by_name` só quando o resultado passava na lista-negra — se a ocorrência MAIS RECENTE de uma tool marcada era cortada por D7 mas uma ANTERIOR não, o bloco ressuscitava o resultado ANTIGO (stale), violando D6/D7. Corrigido: `recent_by_name` guarda a ocorrência mais recente **incondicionalmente**; o filtro blacklist/vazio roda DEPOIS ao montar a Seção A — resultado recente cortado ⇒ cai pra nome+args, sem ressuscitar o antigo. Regressão travada em `test_blacklisted_most_recent_does_not_resurrect_older`.
- **Verificação:** `test_tool_memory.py` + `test_tool_memory_injection.py` + `test_tool_memory_reuse.py` (11) verdes (default-off byte-idêntico preservado). Smokes: reuse+degradação (mais recente vence, sem vazar tool não marcada), D7 (blacklist corta resultado marcado → cai pra nome+args, sem stale), cap escala com config (200/500), cap=0 guardado ≥50, config inválida → 800.

---

### Fase A1 — Superfície backend (list + PUT + config key) 🟢 [dep A0]

**Objetivo:** expor/persistir `reuse_result` por tool e tornar o tamanho global gravável.

**Itens:**
1. [paralelo] `tool_registry.list_tools` ([tool_registry.py:263-291](../agent/tool_registry.py#L263)): acrescentar `"reuse_result": bool(ov.get("reuse_result", 0))` no dict de cada item.
2. [paralelo] PUT `/api/tools/{name}` ([server/routes/tools.py:42-58](../server/routes/tools.py#L42)): acrescentar `if "reuse_result" in body: update_kwargs["reuse_result"] = bool(body["reuse_result"])`. Sem guarda por nome (D8).
3. [paralelo] `ConfigKey("ai_tool_reuse_result_max_chars", default=800, exposed=True, writable=True)` em [config/settings.py:159](../config/settings.py#L159). Como o PUT `/api/config` só aceita keys em `writable_config_keys()` ([config.py:94-99](../server/routes/config.py#L94)), declarar `writable=True`/`exposed=True` é o que habilita salvar/ler. Nenhuma outra mudança no route de config (é genérico).

**Pronto quando:** `GET /api/tools` retorna `reuse_result` por item; `PUT /api/tools/pesquisar_perguntas_frequentes {"reuse_result":true}` persiste; `GET /api/config` retorna `ai_tool_reuse_result_max_chars` e o `PUT` salva um novo valor.

#### Status de execução — Fase A1
**Estado:** ✅ Concluída (2026-07-10)
- **O que foi feito:** `list_tools` ([tool_registry.py:286](../agent/tool_registry.py#L286)) expõe `"reuse_result": bool(ov.get("reuse_result", 0))`; PUT `/api/tools/{name}` ([tools.py:46](../server/routes/tools.py#L46)) aceita `reuse_result` (sem guarda por nome, D8); `ConfigKey("ai_tool_reuse_result_max_chars", default=800, exposed=True, writable=True)` ([settings.py](../config/settings.py)). · **Decisões:** route de config genérico não muda (writable=True já habilita). · **Pendências:** nenhuma. · **Verificação:** TestClient: `GET /api/tools` traz `reuse_result`; `PUT {reuse_result:true}` persiste até em `save_contact_info` (side-effecting) SEM guarda; `GET/PUT /api/config` de `ai_tool_reuse_result_max_chars` (default 800 → 350) funciona.

---

### Fase D0 — UI: toggle por-tool + campo de tamanho 🟢 [dep A1 contrato]

**Objetivo:** o operador liga/desliga o reuse por tool (uniforme) e define o tamanho global, legível no modo escuro.

**Itens:**
1. [sequencial] `EditModal` ([ToolsManager.js:10](../web/static/js/components/ToolsManager.js#L10)): adicionar um toggle/checkbox "Reaproveitar a resposta desta tool para a IA" ligado a `tool.reuse_result`; incluir `reuse_result` no `body` do `save()` quando mudar. **Uniforme em toda tool (D8)** — sem desabilitar por nome. Texto de ajuda curto com o **aviso genérico**: "A IA lembra o resultado nas próximas mensagens em vez de consultar de novo. Deixe DESLIGADO para dados que mudam (ex.: preços) e para tools que executam ações (salvar, transferir…)." Classes `wa-*`/`.wa-field` (modo escuro).
2. [sequencial] `ToolsUnified.js`: indicador discreto na linha quando `reuse_result` (badge "lembra resposta"), opcional. Toggle principal no modal.
3. [sequencial] `GeneralSettings.js` (D9): novo campo numérico "Tamanho máx. do resultado reaproveitado (caracteres)" ligado a `ai_tool_reuse_result_max_chars`. Wiring: `populate` ([GeneralSettings.js:46-58](../web/static/js/components/ai/GeneralSettings.js#L46)) → `setToolReuseMaxChars(cfg.ai_tool_reuse_result_max_chars ?? 800)`; `handleSave` data ([:101-117](../web/static/js/components/ai/GeneralSettings.js#L101)) → `ai_tool_reuse_result_max_chars: parseInt(toolReuseMaxChars,10) || 800`; input `type="number"` com `.wa-field`, perto do textarea de `ai_history_exclude_patterns` (mesma seção de contexto/memória).
4. [paralelo] `web/static/js/services/api.js`: `saveTool`/`saveConfig` já mandam o body — confirmar que não há allow-list de keys no client que barre `reuse_result`/`ai_tool_reuse_result_max_chars`.

**Pronto quando:** marcar "Reaproveitar resposta" numa tool, salvar, recarregar → continua marcado; mudar o tamanho em Configurações e salvar → persiste; ambos legíveis no modo escuro.

#### Status de execução — Fase D0
**Estado:** ✅ Concluída (2026-07-10)
- **O que foi feito:** `EditModal` ([ToolsManager.js](../web/static/js/components/ToolsManager.js)) ganhou checkbox "Reaproveitar a resposta desta tool para a IA" (state `reuseResult`, incluído no `dirty` e no `body` do `save()` só quando muda) + texto de ajuda com o aviso genérico (D8). `ToolsUnified.js` propaga `reuse_result` na row, mostra o badge "lembra resposta" (roxo, legível no dark) e fecha o modal quando `reuse_result` está no body. `GeneralSettings.js` (D9) tem o campo numérico "Tamanho máx. do resultado reaproveitado (caracteres)" (`type=number`, `.wa-field`, min 50, default 800) perto do filtro de histórico, com wiring `populate`/`handleSave`. · **Decisões:** toggle uniforme (sem desabilitar por nome). Classes `wa-*`/`.wa-field` em tudo (dark ok). · **Pendências:** nenhuma. · **Verificação:** `node --check` limpo nos 3 arquivos; `api.js` (`saveConfig`/`saveOverride` via `request`) passa o body inteiro — sem allow-list de keys no client.
- **Fix da revisão adversarial (2026-07-10):** o botão do modal era inalcançável para tools **code-in-DB** (o "Editar" delas abre o editor Python `ToolForm`, não o `EditModal`) — furava D1/D8 ("aparece pra TODAS, inclusive code-in-DB"). Corrigido: `ToolsUnified.js` ganhou um **toggle inline** "Reaproveitar resposta" (novo handler `toggleReuse` → mesmo `PUT /api/tools`) exibido nas rows `isCode && registered`; core/plugin continuam pelo modal. `node --check` limpo.

---

### Fase E0 — Testes 🔴 (fecha)

**Objetivo:** travar o comportamento (default-off byte-idêntico + reuse-on + caps + config).

**Itens:**
1. **Caracterização (ANTES de confiar no B0):** com `reuse_result` default 0, o bloco `tool_memory` de uma conversa com tool_call é idêntico ao de hoje (só nome+args; nenhuma linha `→`).
2. Reuse ligado: marcar uma tool (`upsert_override(reuse_result=True)`), gravar um card com `→ resultado`, chamar `build_block` → bloco contém o resultado (truncado).
3. Cap/scrub: resultado gigante/base64 é truncado no `ai_tool_reuse_result_max_chars` e scrubado; mudar o config muda o corte.
4. Degradação: duas chamadas da mesma tool marcada ⇒ só a mais recente entra full.
5. History-filter (D7): com `ai_history_exclude_patterns` casando o resultado, a linha `→` é cortada mesmo com a tool marcada.
6. Uniformidade (D8): marcar `reuse_result=true` para `transferir_agente` **persiste** (sem guarda) e o `build_block` inclui o resultado — confirma que não há trava.
7. Kill-switch: `ai_tool_memory_enabled=False` ⇒ nenhum bloco (nem reuse).
8. Endpoint: `GET/PUT /api/tools` incluindo `reuse_result`; `GET/PUT /api/config` incluindo `ai_tool_reuse_result_max_chars` em `tests/test_endpoints.py`.

**Pronto quando:** `venv/bin/python -m pytest tests/ -q` verde no Postgres de teste (`WHATSBOT_TEST_DB_URL`); novo `tests/test_tool_memory_reuse.py` verde.

#### Status de execução — Fase E0
**Estado:** ✅ Concluída (2026-07-10)
- **O que foi feito:** Novo [tests/test_tool_memory_reuse.py](../tests/test_tool_memory_reuse.py) (10 casos: default-off byte-idêntico, reuse-on, sem-vazar-tool-não-marcada, cap+config+guard, base64 scrub, degradação (mesmo/diferente args), D7 blacklist, D8 uniforme sem guarda em `transferir_agente`, kill-switch). Coverage de endpoint em [tests/test_endpoints.py](../tests/test_endpoints.py): `GET/PUT /api/config` do `ai_tool_reuse_result_max_chars` + `GET/PUT /api/tools` do `reuse_result`.
- **Decisões:** endpoint coverage foi para o suite legado (`test_endpoints.py`, 1215 checks) porque é lá que a app autenticada roda; os casos de motor ficaram no arquivo pytest novo (fixture `_engine_ready`).
- **Pendências / nota de merge:** durante a execução surgiu (trabalho concorrente do plano 42) a migration `0047_source_id_native` compartilhando `down_revision=0046` → 2 heads. Resolvido **linearizando**: minha migration virou `0048_tool_reuse_result` (`down_revision="0047_source_id_native"`). Head único restaurado.
- **Verificação:** `test_tool_memory_reuse.py` (10) + `test_tool_memory*.py` (7) + `test_schema_drift.py` + `test_alembic_hygiene::test_single_alembic_head` verdes; `test_endpoints.py` → **1215 passed, 0 failed**; suites de tool/repo/history verdes. **Falhas pré-existentes (confirmadas independentes do plano 47, reproduzem com meu código stashed):** `test_alembic_hygiene::{test_linear_chain, test_no_unexpected_duplicate}` (prefixos 0037/0042/0043 duplicados na história de merge do repo) e `test_gowa_plugin::gowa setup()` (ImportError de import relativo do plugin gowa, base plano-42).

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| **Marcar tool side-effecting** | Por D8 o operador PODE marcar `save_contact_info`/`transferir_agente` etc.; reusar o "resultado" pode enganar o modelo a crer que já agiu. | Escolha consciente do usuário (quer uniformidade, não vai usar nessas). Aviso genérico no texto de ajuda. O efeito colateral em si **não** re-executa (só o texto reaparece) — o dano é no máximo o modelo se confundir; reversível desmarcando. |
| **Vazamento roteador↔spoke** | O bloco é hoistado pro `system_message` e herdado por todos os hops ([agno_engine.py:189-213](../agent/agno_engine.py#L189)) → um spoke vê o resultado de uma tool marcada. | Aceitável para tools read-only de catálogo (conhecimento compartilhado). Documentar. |
| **Forced-followup com dado stale** | O bloco reinjetado entra no `system_prompt` que o followup agent (sem tools) reusa ([agno_engine.py:512-530](../agent/agno_engine.py#L512)). | O operador marca só tools de staleness baixa; `pesquisar_ofertas` fica DESLIGADA por padrão. |
| **Inchaço do prompt** | Resultado reinjetado a cada turno multiplica tokens (×`max_context`, ×hops). | `ai_tool_reuse_result_max_chars` (D9) + degradação (só o mais recente) + teto global do bloco. Ajuda orienta "ligue só em resultados pequenos e muito perguntados". |
| **PII/base64 no resultado** | O card completo não é scrubado ([messaging_service.py:476](../app/services/messaging_service.py#L476)). | `_scrub` (base64) já roda; truncação limita PII; `history_filter` (D7) permite cortar padrões sensíveis. |
| **Config inválido** | `ai_tool_reuse_result_max_chars` vazio/negativo. | `int(...)` com try/except → fallback 800; guard `max(50, valor)` para não zerar. Fail-open. |
| **Migration order (Postgres único)** | `down_revision` errado quebra o boot. | `down_revision="0046_message_agent_key"` verificado ([...0046...py:17](../db/alembic/versions/20260710_0046_message_agent_key.py#L17)). `revision` ≤32 chars. |
| **Modo escuro** | Toggle/campo novos ilegíveis. | `wa-*`/`.wa-field`; testar (Checklist). |
| **Concorrência** | `reuse_enabled_names()` + `config` lidos a cada `build_block` (todo turno). | `SELECT`/get baratos, sem estado global. Opcional: cache TTL 30s como o `history_filter` (P3). |

---

## 6. Perguntas em aberto

- **P1 — Guarda de tools side-effecting.** ✅ **DECIDIDO (2026-07-10):** **sem guarda** — toggle uniforme em toda tool (D8). O usuário quer poder ligar/desligar em qualquer tool para não ter regras diferentes; aceita não usar nas que executam ações. Só um aviso genérico no texto de ajuda.
- **P2 — Tamanho do resultado reaproveitado.** ✅ **DECIDIDO (2026-07-10):** **1 campo global** em Configurações da IA (`ai_tool_reuse_result_max_chars`, default 800), valendo para todas as tools por enquanto (D9). Numérico, editável em `GeneralSettings.js`.
- **P3 — Cache de `reuse_enabled_names()`/config.** ⏸️ ADIADO. Começar sem cache (leituras baratas); adicionar TTL 30s se aparecer no profiling. Não bloqueia.
- **P4 — Medir antes (Passo 0 da pesquisa).** ⏸️ Opcional. Instrumentar `P(re-chamada redundante)` com `tool_memory` ON para quantificar o ganho e informar quais tools marcar. Não bloqueia (o valor de UX já justifica).

---

## 7. Alternativa futura (fora de escopo)

**Message `role='tool'` nativas ao AGNO** (fidelidade máxima ao framework): montar `Message(role='assistant', tool_calls=[…])` + `Message(role='tool', tool_call_id=…, content=result)` no `convo`, como o `_followup_input` já faz DENTRO do turno ([agno_engine.py:512-530](../agent/agno_engine.py#L512)). Bloqueado pelo contrato OpenAI de pareamento `tool_call_id` (os cards atuais são texto sem id) → exige schema estruturado novo em `messages`. **Conscientemente adiado** (plano 43 P6). Este plano fica no bloco `system` compacto, suficiente para o objetivo e respeitando "WhatsBot é dono do contexto".

---

## 8. Apêndice — arquivos-chave

**DB / repo (A0):**
- [db/tables.py:597-606](../db/tables.py#L597) — coluna `reuse_result`
- `db/alembic/versions/20260710_0047_tool_reuse_result.py` — migration (nova)
- [db/repositories/tool_override_repo.py](../db/repositories/tool_override_repo.py) — `ensure`, `upsert_override`, `reuse_enabled_names` (novo)

**Motor (B0):**
- [agent/tool_memory.py](../agent/tool_memory.py) — `build_block`, `_tool_signature` (+ variante com resultado), leitura do `result_cap`
- [db/repositories/message_repo.py:177-196](../db/repositories/message_repo.py#L177) — `get_tool_calls_by_conversation` (lido, não muda)
- [agent/history_filter.py](../agent/history_filter.py) — `load_compiled`/`matches` (reuso, D7)

**Superfície (A1):**
- [agent/tool_registry.py:263-291](../agent/tool_registry.py#L263) — `list_tools` (+ `reuse_result`)
- [server/routes/tools.py:30-74](../server/routes/tools.py#L30) — PUT (+ `reuse_result`)
- [config/settings.py:159](../config/settings.py#L159) — `ai_tool_memory_enabled` (kill-switch reusado) + `ai_tool_reuse_result_max_chars` (novo)

**Frontend (D0):**
- [web/static/js/components/ToolsManager.js:10](../web/static/js/components/ToolsManager.js#L10) — `EditModal` (+ toggle uniforme)
- [web/static/js/components/ai/ToolsUnified.js](../web/static/js/components/ai/ToolsUnified.js) — indicador na linha
- [web/static/js/components/ai/GeneralSettings.js:46-117](../web/static/js/components/ai/GeneralSettings.js#L46) — campo de tamanho (D9)
- [web/static/js/services/api.js](../web/static/js/services/api.js) — `saveTool`/`saveConfig` (confirmar passagem do body)

---

## 9. Checklist de verificação

- [x] `alembic upgrade head` + `downgrade -1` limpos (round-trip da migration)
- [x] `ensure` cria row com `reuse_result=0`; re-registro não regride escolha do usuário
- [x] `GET /api/tools` traz `reuse_result`; `PUT {reuse_result:true}` persiste (uniforme, sem guarda)
- [x] `GET/PUT /api/config` com `ai_tool_reuse_result_max_chars` funciona
- [x] **Default-off byte-idêntico:** bloco `tool_memory` sem nada marcado = comportamento atual (teste de caracterização)
- [x] Tool marcada ⇒ bloco inclui `→ resultado` truncado no config/scrubado; degradação (só o mais recente)
- [x] Mudar `ai_tool_reuse_result_max_chars` muda o corte do resultado
- [x] `history_filter` re-aplicado sobre o resultado reintroduzido (D7)
- [x] `ai_tool_memory_enabled=False` ⇒ sem bloco (nem reuse)
- [x] `tests/test_endpoints.py` (1215) + `tests/test_tool_memory_reuse.py` (10) verdes no Postgres (`WHATSBOT_TEST_DB_URL`)
- [x] Modo escuro: toggle + campo de tamanho legíveis (`wa-*`/`.wa-field`; `text-purple-700 dark:text-purple-400` no badge)
- [x] Sem segredo/PII vazando no bloco (scrub base64 + truncação conferidos num resultado grande; D7 corta padrões sensíveis)
