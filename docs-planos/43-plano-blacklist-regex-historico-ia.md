# Plano 43 — Filtro genérico de lista-negra por regex para o histórico que vai à IA

> **Status:** ✅ IMPLEMENTADO (2026-07-09) · **Escopo:** pequeno/médio (1 módulo puro novo + 2 funções de repo + 1 config key + 1 campo de UI · retrocompatível por default). Todas as fases (A0/B0/A1/C0/D0/E0) concluídas e verdes; ver os blocos "Status de execução" por fase e o checklist (§8). P2 resolvido = (a) aplicar o filtro na análise de melhoria com proteção do alvo.
> **Origem:** investigação nesta sessão sobre "o que a IA enxerga do histórico" (áudio, tools, mensagens privadas). Motivação concreta: automações como o plugin `protocolos` gravam `private_note` (`🔖 Protocolo aberto · PROT-…`) que HOJE entram no contexto do LLM ([memory.py:482](../agent/memory.py#L482)) e **duplicam** com o bloco `tool_memory` (o mesmo protocolo aparece como nota privada **e** como atributo `observacao`). **Método:** leitura do código real + `grep` exaustivo nesta sessão + comparação com `/opt/nexus/gerenciamento-ia` (que exclui `private`+`activity` explicitamente). Todo `arquivo:linha` abaixo foi **verificado**.
> **O quê/por quê:** dar ao operador uma **lista de regex** (config global) que corta qualquer mensagem do histórico cujo `role + content` case com algum padrão, ANTES de o histórico virar contexto do LLM. Permite cortar por tipo (`^private_note\t`), por conteúdo (`Protocolo aberto`, `PROT-\d{8}`) ou combinação. Default **vazio** = comportamento atual byte-idêntico (nada cortado).
>
> **Como usar este plano:** ao executar cada fase, preencha o bloco **"Status de execução"** dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro. **Verde a cada fase.** **Módulo puro (`history_filter`) com testes ANTES** de plugar nos repos. **Um refactor por commit.** As waves marcam o que roda em paralelo (🟢) e o que é sequencial/bloqueante (🔴).

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** ✅ (2026-07-09) | **Filtro GLOBAL**, não per-canal. O usuário pediu explicitamente "um filtro geral". Uma única lista de regex em `config`. | Nova `ConfigKey("ai_history_exclude_patterns", default=[])` em [config/settings.py](../config/settings.py). NÃO entra em `PER_CHANNEL_AI_KEYS` ([channels/ai_settings.py:28](../channels/ai_settings.py#L28)). Se no futuro virar per-canal, é aditivo (ver P4). |
| **D2** ✅ (2026-07-09) | **Hook no repo** (`message_repo.get_context` / `get_context_by_conversation`), operando sobre `role + content` **crus** — ANTES da transformação que embrulha `private_note` em `[Nota privada do operador]:` ([memory.py:482](../agent/memory.py#L482)). | Um só ponto cobre os 2 consumidores (motor de IA + `improvement_service`). O match vê o texto original (`🔖 Protocolo aberto · …`), não o texto embrulhado. |
| **D3** ✅ (2026-07-09) | **Alvo do match = `f"{role}\t{content}"`** com `re.search` (não `fullmatch`). Um `\t` (tab) separa role de conteúdo. | Mantém "lista de regex" (o usuário digita strings). Ancora por tipo: `^private_note\t`; por conteúdo: `Protocolo aberto`; por tipo+conteúdo: `^assistant\t.*PROT-`. O `\t` no conteúdo real é raríssimo, então a âncora de role é confiável. |
| **D4** ✅ (2026-07-09) | **Over-fetch para preservar N.** Como o regex não roda no SQL, o corte em Python não pode "gastar" um slot do `limit`. O repo busca uma janela maior (as `HISTORY_FETCH_CAP` mais recentes), filtra, e só então corta as últimas N. | `get_context(..., exclude=…)` busca `max(limit, cap)` linhas quando há padrões; sem padrões, comportamento atual byte-idêntico (`limit` direto). |
| **D5** ✅ (2026-07-09) | **Default vazio = retrocompatível.** Sem padrões configurados, NADA é cortado e o caminho é idêntico ao atual (mesmo SQL, mesmo `limit`). Não semear `private_note` por padrão — quem quiser corta explicitamente (placeholder da UI sugere o padrão pronto). | Zero regressão em instalações existentes. O ganho é opt-in. |
| **D6** ✅ (2026-07-09) | **Regex inválida NUNCA quebra um turno.** Cada padrão é compilado com `try/except`; padrão inválido é ignorado + logado (`logger.warning`), os demais seguem. Qualquer falha do módulo inteiro → histórico passa intacto (fail-open). | O filtro é best-effort, igual `tool_memory` ([agent/tool_memory.py](../agent/tool_memory.py)). Nunca deixa a IA muda. |
| **D7** ✅ (2026-07-09) | **NÃO implementar** aqui os tool-calls estruturados da OpenAI/OpenRouter (`assistant.tool_calls` + `role:tool`). Fica como nota de alternativa futura (ver §7). | Escopo fechado no filtro de histórico. O `tool_memory` continua como está. |
| **Princípio fixo** | O caminho de histórico da IA é crítico (todo turno passa por ele) e roda também em paths sync (`process_message`). ⇒ o filtro tem que ser **barato** (compile cacheado) e **fail-open**. | Cache de compilação por valor de config; invalidação simples (ver Fase A0). |

---

## 1. Resumo executivo

Hoje o histórico que a IA vê é montado por `message_repo.get_context(contact_id, limit)` ([message_repo.py:87](../db/repositories/message_repo.py#L87)), que aplica uma **lista-negra de ROLES fixa** (`transcription`, `tool_call`, `system_notice`, `conversation_event`, `system`, `error`) + descarta `status="failed"`. **`private_note` não está excluído** e é ativamente convertido em `[Nota privada do operador]:` ([memory.py:482](../agent/memory.py#L482)), então notas de automação (protocolo) entram no contexto e duplicam com o `tool_memory`.

A solução: um **filtro genérico de lista-negra por regex**, configurável (`ai_history_exclude_patterns`, lista global). Cada mensagem é testada como `"{role}\t{content}"`; se algum regex casar, a mensagem é cortada do histórico do LLM. O corte roda no repo, sobre o texto cru, com over-fetch para não encolher a janela de contexto, compile cacheado e fail-open. Default vazio = nada muda. Um módulo puro novo (`agent/history_filter.py`) concentra a lógica e é testável isoladamente.

---

## 2. Como funciona hoje (mapa) — `arquivo:linha` verificado

### 2.1 A cadeia do histórico da IA

```
process_message / aprocess_message
  → agent_run_service.aprocess_message                          [agent_run_service.py]
      eff_max_context = ai_settings.value(channel_id,
                        "max_context_messages", handler.max_context_messages)   [agent_run_service.py:274]
      context_messages = contact.get_context_messages(eff_max_context)          [agent_run_service.py:280]
        → ContactMemory.get_context_messages(limit)             [memory.py:458]
            recent = message_repo.get_context(self.id, limit)   [memory.py:465]
              SELECT … WHERE contact_id=? AND role NOT IN (excluded)
                        AND (status IS NULL OR status != 'failed')
                ORDER BY ts DESC LIMIT ?                         [message_repo.py:87-105]
                excluded = (transcription, tool_call,
                            system_notice, conversation_event,
                            system, error)                       [message_repo.py:92-93]
            # transformação: private_note → "[Nota privada do operador]: …"    [memory.py:482]
            # imagem → base64 na última; dedup adjacente de assistant idênticos  [memory.py:490-520]
      # tool_memory (bloco system compacto, conversation-scoped) é ANEXADO depois [agent_run_service.py:291]
      # e é REUSADO em todos os hops de routing (não re-busca)   [agent_run_service.py:_run_routing_hop]
```

- **`get_context` aplica o `limit` no SQL** ([message_repo.py:104](../db/repositories/message_repo.py#L104)) e devolve em ordem cronológica (`reversed`) ([message_repo.py:106](../db/repositories/message_repo.py#L106)).
- **Único transform de `private_note`**: [memory.py:482](../agent/memory.py#L482) (`[Nota privada do operador]: {content}`). ⇒ se o filtro rodar DEPOIS disso, o match teria que casar o texto embrulhado; por isso D2 (filtrar no repo, texto cru).
- **`context_messages` é montado 1× e reusado** entre hops de routing (`_run_routing_hop` recebe `context_messages` pronto) ⇒ filtrar na origem cobre todos os hops automaticamente.

### 2.2 A variante por-conversa e o 2º consumidor

| Função | `arquivo:linha` | Mesmo filtro de role? | Consumidor |
|---|---|---|---|
| `get_context(contact_id, limit)` | [message_repo.py:87](../db/repositories/message_repo.py#L87) | sim (`excluded` linha 92) | motor de IA (via `get_context_messages`) **e** fallback do `improvement_service` |
| `get_context_by_conversation(conversation_id, limit)` | [message_repo.py:108](../db/repositories/message_repo.py#L108) | sim (`excluded` linha 119) | `improvement_service` (quando há `conversation_id`) |
| `get_tool_calls_by_conversation(...)` | [message_repo.py:131](../db/repositories/message_repo.py#L131) | **NÃO** (só `role='tool_call'`) | `tool_memory` — **fora de escopo** deste filtro |

- **`improvement_service`** ([improvement_service.py:250-253](../app/services/improvement_service.py#L250)) usa `get_context_by_conversation` (com `conversation_id`) ou `get_context` (fallback) para montar o bloco de histórico da análise de resposta sinalizada. Ver P2 (aplicar o filtro aqui também ou não).

### 2.3 A plumbing de config (single-source, já pronta para receber uma lista)

```
CONFIG_KEYS: tuple[ConfigKey, …]                                 [config/settings.py:105]
  ConfigKey(key, default, exposed, get_default, writable)        [config/settings.py:96]
    → DEFAULT_CONFIG (seed no boot)                              [config/settings.py:254]
    → exposed_config_keys()  → GET /api/config projeta            [config/settings.py:228] / [config.py:78-79]
    → writable_config_keys() → PUT allowlist                      [config/settings.py:233] / [config.py:94]
config_repo.set/get faz json.dumps/loads                         [db/repositories/config_repo.py:17,42]
  ⇒ um valor LISTA persiste sem problema (não há lista em CONFIG_KEYS hoje, mas
    allowed_jid_types por-canal já é lista — precedente de config em lista)
```

- Adicionar **uma linha** `ConfigKey("ai_history_exclude_patterns", default=[], exposed=True, writable=True)` faz a chave fluir sozinha por GET (projeção genérica em [config.py:78-79](../server/routes/config.py#L78)) e PUT (allowlist em [config.py:94-99](../server/routes/config.py#L94)). O valor lista é json-encoded/decoded transparentemente.

### 2.4 A UI de settings globais de IA

- [web/static/js/components/ai/GeneralSettings.js](../web/static/js/components/ai/GeneralSettings.js) já hospeda settings globais **avançadas** de IA: `improvement_model` e `improvement_prompt` (com `MarkdownEditor`) além de `auto_reply`/chave/saldo. Faz **PUT parcial** (só as chaves que possui — [GeneralSettings.js:96](../web/static/js/components/ai/GeneralSettings.js#L96)). ⇒ é o lar natural de um textarea "Filtro de histórico (regex, uma por linha)" (ver P3).

### 2.5 Referência externa (nexus) — o que ele faz

- `/opt/nexus/gerenciamento-ia/ai/src/middleware/context.py` monta histórico como **texto** e filtra explicitamente `message_type in (0,1,incoming,outgoing) and not m.get("private") and m.get("content")` — ou seja, **exclui notas privadas e mensagens de atividade**. Valida o desenho: cortar `private` do contexto é o comportamento desejado; aqui generalizamos para uma lista de regex configurável.

### 2.6 Falsos positivos / fora de escopo (descartados com razão)

| Hipótese | Veredito | Razão (verificada) |
|---|---|---|
| "Filtrar no `memory.get_context_messages`" | ❌ descartado | O transform de `private_note` ([memory.py:482](../agent/memory.py#L482)) roda ali; filtrar depois exigiria casar o texto embrulhado e **não preservaria N** (o `limit` já foi aplicado no SQL). D2 põe no repo. |
| "Filtrar no SQL com `~` do Postgres" | ❌ descartado | Portabilidade/legibilidade ruins, difícil compor N regex, e mistura política de app com o repo. Filtrar em Python com compile cacheado é barato o suficiente (janela ≤ cap). |
| "O `tool_memory` também precisa do filtro" | ❌ fora de escopo | `tool_memory` lê `get_tool_calls_by_conversation` (só `tool_call`) e monta `nome(args)` + atributos — não é histórico de mensagem. Cortar duplicata do protocolo se resolve cortando a `private_note` (a fonte); o atributo `observacao` é decisão de qual tool o agente chama, não deste filtro. |
| "Precisa de migration" | ❌ não | A chave nova é só `config` (key-value). Seed via `DEFAULT_CONFIG` no boot ([config/settings.py:279](../config/settings.py#L279)); sem DDL. |
| "Cortar mensagem quebra o `reply_to`/citação" | ❌ não se aplica | `get_context` já devolve um subconjunto para o LLM; cortar mais linhas não afeta o painel nem o histórico persistido (só o que vai ao modelo). |

---

## 3. Inventário / análise das mudanças

| # | Item | Arquivo (alvo) | O que falta | Abordagem | Risco | Esforço |
|---|------|----------------|-------------|-----------|-------|---------|
| 1 | Módulo puro do filtro | **novo** `agent/history_filter.py` | não existe | `load_compiled()` (lê config + compila + cache), `matches(role, content, compiled)`, `filter_rows(rows, compiled)` | baixo | S |
| 2 | Param `exclude` no repo | `db/repositories/message_repo.py:87,108` | funções só têm role-blacklist | Adicionar `exclude=None`; quando setado, over-fetch (`HISTORY_FETCH_CAP`) → filtrar → tail N; quando `None`, caminho atual byte-idêntico | médio | M |
| 3 | Wiring no motor de IA | `agent/memory.py:465` | passa só `(self.id, limit)` | Compilar via `history_filter.load_compiled()` e passar `exclude=` a `get_context` | baixo | S |
| 4 | Config key | `config/settings.py:105` (tupla `CONFIG_KEYS`) | chave não existe | 1 linha `ConfigKey("ai_history_exclude_patterns", default=[], exposed=True, writable=True)` | baixo | S |
| 5 | UI (textarea regex) | `web/static/js/components/ai/GeneralSettings.js` | sem campo | Textarea "uma regex por linha" ↔ lista; validação leve client-side; entra no PUT parcial | baixo | M |
| 6 | Decisão improvement_service | `app/services/improvement_service.py:250-253` | usa repo direto | Aplicar (ou não) o mesmo `exclude` — ver P2 | baixo | S |
| 7 | Testes | `tests/` + `agent/history_filter` (pytest) | — | pytest do módulo puro + endpoint (PUT/GET da chave) + get_context com exclude | baixo | M |
| 8 | Doc | `CLAUDE.md` (seção "Fluxo de mensagens" / roles) | — | 2–3 linhas sobre o filtro e onde configurar | baixo | S |

---

## 4. Contrato do módulo `agent/history_filter.py` (novo)

Descrição do quê/onde (não é implementação):

```python
# agent/history_filter.py  (puro, best-effort, fail-open)
CONFIG_KEY = "ai_history_exclude_patterns"     # lista[str] em config (global)
HISTORY_FETCH_CAP = 200                         # janela máx. de over-fetch (D4) — a confirmar (P5)
_SEP = "\t"                                     # separador role<TAB>content (D3)

def load_compiled() -> list[re.Pattern]:
    """Lê a config, compila cada padrão (try/except por item), cacheia por valor.
    Padrão inválido → warning + ignorado. Erro geral → [] (fail-open)."""

def matches(role: str, content: str, compiled) -> bool:
    """re.search em f'{role}{_SEP}{content}' contra cada compiled. Sem padrões → False."""

def filter_rows(rows: list[dict], compiled) -> list[dict]:
    """Remove as linhas que casam. Sem padrões/compiled vazio → devolve rows intacto."""
```

- **Cache de compile (TTL 30s — P5 ✅)**: guardado em módulo com timestamp, TTL de 30s (mesmo padrão de [channels/ai_settings.py](../channels/ai_settings.py) `_TTL = 30.0`). Dentro da janela, reusa os padrões já compilados sem reler a config; expirado, relê `ai_history_exclude_patterns` e recompila. Edição dos regex na UI demora ≤30s para valer — irrelevante na prática. `HISTORY_FETCH_CAP` (over-fetch) é **coisa separada** do cache (ver §4-bis).
- **Fail-open** em todos os níveis: erro ao ler config, ao compilar, ao filtrar ⇒ histórico passa intacto.

### §4-bis — `HISTORY_FETCH_CAP` vs cache (não confundir)

São dois mecanismos independentes:

| Mecanismo | O que é | Valor |
|---|---|---|
| `HISTORY_FETCH_CAP` | Quantas linhas **ler do banco** antes de filtrar, para o corte não encolher a janela útil abaixo de `max_context_messages`. Ex.: quer 10, o filtro corta 3 protocolos → precisa ter lido >10 pra ainda entregar 10. | fixo `200` (folga sobre qualquer `max_context_messages` real) |
| Cache de compile | Evitar `re.compile` a cada turno. | TTL `30s` |

---

## 5. Fases / Roadmap

### Diagrama de dependências

```
WAVE 0   A0(history_filter + testes)   ·   B0(config key)          ← 🟢 paralelos, independentes
              │                                   │
              │ (A0 pronto+verde)                 │ (chave existe em GET/PUT)
WAVE 1   A1(wiring memory→repo)         ·   C0(UI textarea)  ·  D0(decisão improvement) ← 🟢 paralelos
              │                                   │                    │
              └───────────────┬───────────────────┴────────────────────┘
WAVE 2   E0(testes integração + doc CLAUDE.md)                          ← 🔴 espera tudo
```

### Tabela de fases

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando / Nota |
|------|------|------------|-------|-------|----------------------|
| 0 | **A0** | Backend — módulo puro `history_filter` + pytest | 🟢 | baixo | módulo criado, pytest verde; não plugado ainda |
| 0 | **B0** | Config — `ConfigKey` nova | 🟢 | baixo | GET `/api/config` retorna `ai_history_exclude_patterns: []`; PUT aceita `[bloqueia: C0]` |
| 1 | **A1** | Backend — `exclude` no repo + wiring `memory` | 🟢 | médio | `[depende de: A0]` motor de IA corta as linhas que casam; N preservado |
| 1 | **C0** | Frontend — textarea regex em GeneralSettings | 🟢 | baixo | `[depende de: B0]` salva/recarrega a lista; modo escuro legível |
| 1 | **D0** | Backend — decisão improvement_service | 🟢 | baixo | `[depende de: A0]` P2 resolvido + implementado |
| 2 | **E0** | Testes de integração + doc | 🔴 | baixo | `[depende de: A1,C0,D0]` suíte verde no Postgres + CLAUDE.md atualizado |

---

### Fase A0 — Módulo puro `agent/history_filter.py` (+ testes) 🟢

**Objetivo:** concentrar a lógica de compile/match/filter num módulo testável, sem tocar em nada do fluxo ainda.

**Itens:**
- `[paralelo]` Criar `agent/history_filter.py` com `load_compiled`, `matches`, `filter_rows`, `CONFIG_KEY`, `HISTORY_FETCH_CAP`, `_SEP` (§4).
- `[paralelo]` Compile com `try/except re.error` por item → warning + skip (D6). Cache de compile com **TTL 30s** (P5 · §4-bis).
- `[sequencial]` pytest do módulo: (a) sem padrões → `filter_rows` devolve intacto; (b) `^private_note\t` corta só notas privadas; (c) `Protocolo aberto` corta por conteúdo em qualquer role; (d) regex inválida é ignorada e as válidas seguem; (e) `_SEP` garante âncora de role.

**Pronto quando:** `venv/bin/python -m pytest tests/…history_filter… -q` verde; nenhum call site importa o módulo ainda (isolado).

#### Status de execução — Fase A0
**Estado:** ✅ Concluída (2026-07-09)
- **O que foi feito:** Criado [agent/history_filter.py](../agent/history_filter.py) com `CONFIG_KEY`, `HISTORY_FETCH_CAP=200`, `_SEP="\t"`, `load_compiled()` (cache TTL 30s), `reset_cache()`, `matches()`, `filter_rows()`, `_read_patterns()`/`_compile()`. Criado [tests/test_history_filter.py](../tests/test_history_filter.py) com 10 testes.
- **Como foi feito / decisões:** Cache de compile TTL-30s (sentinela `compiled=None` = "não carregado"; `[]` = carregado sem padrões). Fail-open em cada nível (read/compile/match/filter). `_read_patterns` tolera string com `\n` e tipos inesperados. `matches` engole exceção de padrão patológico no match. `filter_rows([], compiled)`/sem padrões devolve o MESMO objeto (byte-idêntico).
- **Problemas / pendências:** Nenhum. Testes puros não precisam de DB; os de `load_compiled` usam `build_app` (engine Postgres de teste). O import de `db.repositories.config_repo` no topo do módulo é seguro (não conecta em import).
- **Verificação:** `venv/bin/python -m pytest tests/test_history_filter.py -q` → **10 passed**.

---

### Fase B0 — Config key `ai_history_exclude_patterns` 🟢

**Objetivo:** registrar a chave global no single-source para fluir em GET/PUT sem mudança de rota.

**Itens:**
- `[sequencial]` Adicionar `ConfigKey("ai_history_exclude_patterns", default=[], exposed=True, writable=True)` na tupla `CONFIG_KEYS` ([config/settings.py:105](../config/settings.py#L105)).
- `[sequencial]` Confirmar seed: `DEFAULT_CONFIG` passa a conter `[]` ([config/settings.py:254,279](../config/settings.py#L254)); GET projeta ([config.py:78-79](../server/routes/config.py#L78)); PUT aceita ([config.py:94-99](../server/routes/config.py#L94)).

**Pronto quando:** `GET /api/config` retorna a chave com `[]`; `PUT /api/config` com `{"ai_history_exclude_patterns": ["^private_note\\t"]}` persiste e o GET seguinte devolve a lista.

#### Status de execução — Fase B0
**Estado:** ✅ Concluída (2026-07-09)
- **O que foi feito:** `ConfigKey("ai_history_exclude_patterns", default=[], exposed=True, writable=True)` em [config/settings.py](../config/settings.py) (após `max_context_messages`). Reset do cache de compile no PUT em [server/routes/config.py](../server/routes/config.py) quando a chave muda.
- **Como foi feito / decisões:** Passar `[]` como argumento (não field-default) evita o erro de mutable-default do dataclass. A chave flui sozinha por `DEFAULT_CONFIG`/GET/PUT (single-source `CONFIG_KEYS`). O reset no PUT torna a edição instantânea (senão valeria em ≤30s pelo TTL).
- **Problemas / pendências:** Nenhum.
- **Verificação:** `DEFAULT_CONFIG['ai_history_exclude_patterns'] == []`, `exposed=True`, `writable=True` confirmados via import.

---

### Fase A1 — `exclude` no repo + wiring `memory` 🟢 `[depende de: A0]`

**Objetivo:** plugar o filtro no caminho vivo, preservando N e o comportamento default.

**Itens:**
- `[sequencial]` `message_repo.get_context(contact_id, limit, *, exclude=None)` ([message_repo.py:87](../db/repositories/message_repo.py#L87)):
  - `exclude is None` → **caminho atual byte-idêntico** (SQL `LIMIT limit`, sem Python extra).
  - `exclude` setado → SQL `LIMIT max(limit, HISTORY_FETCH_CAP)` (newest-first), `history_filter.filter_rows(...)`, `reversed`, e **tail N** (`[-limit:]` no resultado cronológico). O role-blacklist do SQL continua igual.
- `[paralelo]` Mesmo tratamento em `get_context_by_conversation(conversation_id, limit, *, exclude=None)` ([message_repo.py:108](../db/repositories/message_repo.py#L108)).
- `[sequencial]` `memory.get_context_messages` ([memory.py:465](../agent/memory.py#L465)): `compiled = history_filter.load_compiled()`, `message_repo.get_context(self.id, limit, exclude=compiled)`. O transform de `private_note` e o dedup adjacente ([memory.py:482-520](../agent/memory.py#L482)) ficam **inalterados** (agora operam sobre a lista já filtrada).

**Pronto quando:** com `ai_history_exclude_patterns=["^private_note\\t"]`, uma conversa com nota de protocolo NÃO manda a nota ao LLM (verificar via log de contexto / `_capture_llm_context`); com lista `[]`, o contexto é idêntico ao de antes; a contagem de mensagens úteis no contexto continua ≈ N mesmo quando linhas são cortadas (over-fetch funciona).

#### Status de execução — Fase A1
**Estado:** ✅ Concluída (2026-07-09)
- **O que foi feito:** `get_context(..., *, exclude=None)` e `get_context_by_conversation(..., *, exclude=None)` em [message_repo.py](../db/repositories/message_repo.py) + helpers `_fetch_limit`/`_apply_exclude`. Wiring em [memory.py](../agent/memory.py) (`get_context_messages` compila via `history_filter.load_compiled()` e passa `exclude=`). Import `from agent import history_filter` no topo de memory.py.
- **Como foi feito / decisões:** `_fetch_limit`/`_apply_exclude` importam `agent.history_filter` **lazy** (dentro da função) — mantém a camada db livre de dependência de load-time em `agent` (evita ciclo). `exclude` é keyword-only ⇒ todos os call sites posicionais existentes (`get_context(id, limit)`) seguem intactos. Caminho `not exclude` é byte-idêntico (`reversed` das exatas `limit` linhas). O filtro roda sobre `role+content` cru, ANTES do wrap `private_note`→"[Nota privada]" no memory.
- **Problemas / pendências:** Nenhum. Sem ciclo de import (verificado). Callers de `get_context`/`get_context_by_conversation` inventariados (memory, improvement_service, test_endpoints) — nenhum quebra.
- **Verificação:** `tests/test_history_filter.py` **14 passed** (inclui over-fetch preserva N, byte-idêntico com `None`/`[]`, corte de private_note fim-a-fim). Regressão: `test_context_dedup` + `test_tool_memory` + `test_improve_conversation_scope` **12 passed**.

---

### Fase C0 — UI: textarea de regex em GeneralSettings 🟢 `[depende de: B0]`

**Objetivo:** operador edita a lista (uma regex por linha) e salva.

**Itens:**
- `[sequencial]` Em [GeneralSettings.js](../web/static/js/components/ai/GeneralSettings.js): estado `historyExcludePatterns` (string do textarea); `populate` recebe `cfg.ai_history_exclude_patterns` (lista) → `join("\n")`; no save, `split("\n")` → `map(trim)` → dropar linhas vazias → array no PUT parcial (junto de `auto_reply` etc.).
- `[paralelo]` Validação leve client-side: tentar `new RegExp(linha)` por linha; marcar linhas inválidas (borda/aviso) sem bloquear o save (o backend também ignora inválidas — D6).
- `[paralelo]` Placeholder/ajuda com exemplos prontos: `^private_note\t` (corta todas as notas privadas), `Protocolo aberto`, `PROT-\d{8}`.
- `[sequencial]` **Modo escuro**: usar `.wa-field` no textarea + `wa-*` no rótulo/ajuda (regra do CLAUDE.md).

**Pronto quando:** editar a lista, salvar, recarregar a página → a lista persiste; ligar modo escuro → textarea/ajuda legíveis; linha de regex inválida é sinalizada mas não trava o save.

#### Status de execução — Fase C0
**Estado:** ✅ Concluída (2026-07-09)
- **O que foi feito:** Card "Filtro de histórico (regex)" em [GeneralSettings.js](../web/static/js/components/ai/GeneralSettings.js): estado `historyExcludePatterns`, `populate` (lista→`join('\n')`), save (`split('\n')`→trim→drop-vazias→array no PUT parcial), validação client-side (marca linhas com regex inválida sem bloquear o save), placeholder + exemplos.
- **Como foi feito / decisões:** Textarea com `.wa-field` + `font-mono` (legível nos 2 temas); rótulo/ajuda em `wa-*`. Exemplos de regex interpolados como strings JS (`${'^private_note\\t'}`) para não colidir com escapes do template htm. Aviso de linha inválida em `text-red-500`. Validação NÃO bloqueia (backend também ignora inválidas — D6). Símbolo `⇥` (tab) na ajuda para explicar o alvo `role⇥conteúdo`.
- **Problemas / pendências:** Nenhum. Sem build step; parse verificado com `node --check`.
- **Verificação:** `node --check web/static/js/components/ai/GeneralSettings.js` → SYNTAX OK. Persistência round-trip coberta pelo teste de endpoint na Fase E0.

---

### Fase D0 — Decisão + wiring do `improvement_service` 🟢 `[depende de: A0]`

**Objetivo:** resolver P2 (aplicar o mesmo filtro na análise de resposta sinalizada?) e implementar.

**Itens:**
- `[sequencial]` Decidir P2 (recomendação: **aplicar** o filtro — consistência com o que a IA viu — MAS proteger o alvo: nunca cortar a `assistant` marcada como incorreta). Implementar em [improvement_service.py:250-253](../app/services/improvement_service.py#L250) passando `exclude=history_filter.load_compiled()` às chamadas de repo, ou deixar sem filtro se P2 decidir "não".
- `[sequencial]` Se aplicar: garantir que a linha `target_content` marcada ([improvement_service.py:258-262](../app/services/improvement_service.py#L258)) sobreviva ao corte (a análise perde sentido se o alvo for cortado).

**Pronto quando:** P2 registrado com ✅ DECIDIDO; a análise de melhoria roda sem erro e (se aplicado) o histórico dela reflete o mesmo corte do motor de IA, com o alvo preservado.

#### Status de execução — Fase D0
**Estado:** ✅ Concluída (2026-07-09)
- **O que foi feito:** P2 resolvido como **(a) APLICAR o filtro** (recomendação). Em [improvement_service.py](../app/services/improvement_service.py): após buscar o histórico, aplica a mesma lista-negra por regex, **protegendo o alvo** (a resposta `assistant` marcada como incorreta nunca é cortada). Import `from agent import agent_factory, history_filter`.
- **Como foi feito / decisões:** Busca RAW (sem `exclude` no repo) e filtra IN-SERVICE — porque o filtro no repo cortaria o alvo se ele casasse um padrão. A guarda `(role==assistant AND content.strip()==target_content) OR not matches(...)` garante a sobrevivência do alvo. Over-fetch (N) não se aplica aqui (é diagnóstico one-shot, não o contexto vivo — D4 é escopado ao path da IA).
- **Problemas / pendências:** Nenhum. Sem `exclude` no repo neste path ⇒ nenhuma regressão quando a lista está vazia (`compiled` vazio ⇒ o `if compiled:` nem entra).
- **Verificação:** `import app.services.improvement_service` OK; `tests/test_improve_conversation_scope.py` **4 passed**. Cobertura fim-a-fim (alvo preservado) na Fase E0.

---

### Fase E0 — Testes de integração + doc 🔴 `[depende de: A1,C0,D0]`

**Objetivo:** travar o comportamento e documentar.

**Itens:**
- `[sequencial]` Teste de endpoint: PUT `ai_history_exclude_patterns` → GET devolve; inserir mensagens (user/assistant/private_note) e verificar que `get_context` com o padrão corta as certas e preserva N.
- `[paralelo]` Teste de regressão: lista `[]` → `get_context` byte-idêntico ao atual (mesma contagem/ordem).
- `[paralelo]` Atualizar [CLAUDE.md](../CLAUDE.md): nota na seção de roles/histórico ("filtro de histórico por regex, global, em Configurações → IA; corta `role\tcontent`; default vazio; fail-open").

**Pronto quando:** `venv/bin/python -m pytest tests/ -q` verde no Postgres de teste (`WHATSBOT_TEST_DB_URL`); CLAUDE.md atualizado.

#### Status de execução — Fase E0
**Estado:** ✅ Concluída (2026-07-09)
- **O que foi feito:** 2 testes de integração em [tests/test_history_filter.py](../tests/test_history_filter.py): `test_config_endpoint_round_trip` (PUT→GET da chave) e `test_improvement_filters_history_but_keeps_target` (filtro na análise + alvo protegido). Fixture autouse que restaura `ai_history_exclude_patterns=[]` após cada teste (anti-poluição na DB compartilhada). Doc no [CLAUDE.md](../CLAUDE.md) (nova subseção "Filtro de histórico por regex").
- **Como foi feito / decisões:** `test_history_filter.py` completo = **16 passed**. Regressão ampla: `tests/characterization/` + `tests/test_legacy_suite.py` (que roda `test_endpoints.py` ~990 checks como subprocess) verdes; `tests/endpoints/` + seed/human_gate/tool_memory_injection **61 passed**; context_dedup+tool_memory+improve **12 passed**.
- **Problemas / pendências:** **2 falhas PRÉ-EXISTENTES** no `developer`, SEM relação com plano 43 (confirmado revertendo minhas mudanças via `git stash` — falham igual no source limpo): (1) `test_rbac_characterization::test_having_permission_passes_gate[ai_engine…]` (gate `agent.config.manage` retorna 403 indevido); (2) `test_legacy_suite[test_gowa_plugin.py]` (49 passed/1 failed — `ImportError: attempted relative import` no `setup()` do gowa). Nenhuma tocada por este plano. Nota: `pytest tests/` inteiro não coleta (scripts standalone com `sys.exit` fora do `collect_ignore` — quirk conhecido do repo); rodar por dir/arquivo.
- **Verificação:** Ver seção 8 (checklist) — todos os itens marcados.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Regex inválida | Um `[` mal fechado quebraria o turno se compilado sem guarda | `try/except re.error` por padrão (D6); módulo inteiro fail-open → histórico intacto |
| Over-fetch (D4) | Buscar janela grande a cada turno pode custar I/O | `HISTORY_FETCH_CAP` bounded (≈200); só quando há padrões; sem padrões o SQL fica idêntico (LIMIT N) |
| Corte agressivo demais | Regex larga (ex. `.`) esvazia o contexto e a IA "esquece" tudo | UI mostra exemplos ancorados; validação client-side; é opt-in e reversível; **N preservado** só ajuda até o cap |
| Compile a cada turno | Recompilar N regex por mensagem custaria | Cache por valor de config no módulo (§4); recompila só quando a lista muda |
| Cortar o alvo do improvement | Análise de resposta sinalizada perde a mensagem marcada | D0/P2: preservar `target_content` mesmo se casar um padrão |
| `private_note` humana útil cortada junto | `^private_note\t` corta TODAS as notas (inclusive as escritas por atendente humano com contexto útil) | Documentar; quem quiser granularidade usa regex de conteúdo (`Protocolo`, `PROT-`) em vez de âncora de role; P4 (per-origem) fica como evolução |
| `_SEP` no conteúdo | Um `\t` real no texto poderia confundir a âncora de role | Raríssimo em mensagens de WhatsApp; `re.search` (não `^…$`) tolera; risco cosmético |
| Postgres único backend | Nada de SQL específico muda | Filtro é 100% Python; SQL só ganha um `LIMIT` maior condicional |

---

## 7. Perguntas em aberto

- **P1 — Global vs per-canal?** ✅ DECIDIDO (2026-07-09): **global** (D1). O usuário pediu "filtro geral". Evolução per-canal é aditiva (P4).
- **P2 — Aplicar o filtro no `improvement_service`?** ✅ DECIDIDO (2026-07-09): **(a) APLICAR** o filtro (a análise vê o mesmo contexto que a IA de atendimento viu — diagnóstico mais fiel), **preservando a mensagem-alvo marcada** (nunca cortada mesmo se casar um padrão). Implementado na Fase D0: busca RAW + filtro in-service com guarda do alvo (a proteção não é possível se o corte rodar no repo). Alternativa (b) "não aplicar" foi descartada — a análise cru veria protocolos/notas que a IA nunca viu, podendo diagnosticar errado.
- **P3 — Onde fica a UI?** ✅ DECIDIDO (2026-07-09): em **GeneralSettings.js** (aba Configurações → IA), junto de `improvement_*` (settings globais avançadas de IA). Não é config de canal (D1) nem de plugin, então NÃO vai em ChannelsManager nem em ConfigPanel de plugin.
- **P4 — Distinguir `private_note` de automação × de humano por estrutura?** ✅ DECIDIDO (2026-07-09): **NÃO — regex é o mecanismo definitivo.** O usuário quer poder cortar por CONTEÚDO qualquer coisa que uma automação futura injete via mensagem privada (não só protocolo). Uma flag estrutural (`sent_by_user_id IS NULL`) seria rígida demais e não pegaria uma automação que escreva com autor. Fica só o filtro por regex; nenhuma coluna/flag nova.
- **P5 — `HISTORY_FETCH_CAP` e estratégia de cache.** ✅ DECIDIDO (2026-07-09): **cap fixo `200`** (over-fetch, coisa separada) + **cache de compile com TTL de `30s`** (mesmo padrão do `ai_settings`). Ver §4-bis. Edição de regex demora ≤30s para valer.
- **P6 — Tool-calls estruturados (OpenAI/OpenRouter).** ⏸️ ADIADO (D7). Alternativa futura: persistir/reenviar histórico de tools como `assistant.tool_calls` + `role:tool` (formato nativo) em vez do bloco de texto `tool_memory`. Resolveria a duplicata do protocolo na raiz (a nota privada não precisaria carregar o que já está no `role:tool`), mas contraria a arquitetura atual "AGNO stateless + WhatsBot dono do contexto" ([agno_engine.py:200-208](../agent/agno_engine.py#L200)) e exige gravar dados estruturados de tool na tabela `messages` (hoje o card `tool_call` é string formatada). Plano próprio.

---

## 8. Checklist de verificação

- [x] `pytest` do módulo puro `agent/history_filter` verde (compile/match/filter, regex inválida, âncora de role). → `test_history_filter.py` **16 passed**.
- [x] `GET /api/config` expõe `ai_history_exclude_patterns` (default `[]`); `PUT` persiste a lista; round-trip via GET. → `test_config_endpoint_round_trip` verde + verificado por import.
- [x] Com `["^private_note\\t"]`: nota de protocolo NÃO aparece no contexto do LLM; com `[]`: contexto byte-idêntico ao atual. → `test_get_context_messages_cuts_private_note` + `test_get_context_no_exclude_byte_identical`.
- [x] N preservado: cortar linhas não encolhe a janela útil abaixo de `max_context_messages` (até o cap). → `test_get_context_over_fetch_preserves_n`.
- [x] `improvement_service` roda sem erro; alvo marcado preservado (P2=aplicar). → `test_improvement_filters_history_but_keeps_target` + `test_improve_conversation_scope` (4).
- [x] Suíte pytest verde no Postgres de teste, salvo 2 falhas **PRÉ-EXISTENTES** no `developer` (verificadas via `git stash`, sem relação com plano 43): `test_rbac_characterization::test_having_permission_passes_gate[ai_engine…]` (gate `agent.config.manage`) e `test_legacy_suite[test_gowa_plugin.py]` (ImportError de import relativo no `setup()` do gowa).
- [x] Modo escuro: textarea (`.wa-field`) + rótulo/ajuda (`wa-*`) legíveis. → classes aplicadas; `node --check` OK.
- [x] Fail-open confirmado: config corrompida / regex toda inválida ⇒ histórico passa intacto, turno não quebra. → `test_compile_skips_invalid_keeps_valid` + `test_load_compiled_skips_invalid_regex_fail_open` + fail-open em cada nível do módulo.
- [x] Sem migration necessária (só `config` key-value); boot semeia `[]`. → `DEFAULT_CONFIG['ai_history_exclude_patterns'] == []`.
- [x] CLAUDE.md atualizado com a nota do filtro. → seção "Filtro de histórico por regex (lista-negra — plano 43)".

---

## 9. Apêndice — arquivos-chave

**Backend (core):**
- `agent/history_filter.py` — **novo** módulo puro (compile/match/filter, cache, fail-open).
- `db/repositories/message_repo.py:87,108` — param `exclude` + over-fetch/tail (Fase A1).
- `agent/memory.py:465` — wiring `get_context_messages` → `get_context(exclude=…)`.
- `config/settings.py:105` — `ConfigKey("ai_history_exclude_patterns", …)`.
- `app/services/improvement_service.py:250-253` — decisão/wiring (Fase D0).

**Frontend:**
- `web/static/js/components/ai/GeneralSettings.js` — textarea de regex + PUT parcial.

**Testes / doc:**
- `tests/` — endpoint (PUT/GET da chave) + `get_context` com exclude + regressão lista vazia.
- `CLAUDE.md` — nota do filtro de histórico.

**Referência (não editar):**
- `/opt/nexus/gerenciamento-ia/ai/src/middleware/context.py` — precedente (`not m.get("private")`).
- `agent/tool_memory.py` — bloco reinjetado (fora do escopo do filtro).
- `agent/agno_engine.py:200-208` — flatten de histórico (contexto do P6).
