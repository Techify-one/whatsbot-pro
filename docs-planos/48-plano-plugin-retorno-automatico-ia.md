# Plano 48 — Plugin "retorno_automatico" (Retorno Automático — IA)

> **Status:** ✅ IMPLEMENTADO (2026-07-13) — F0→F5 + F7 verdes; F6 fora do MVP (P3) · **Data:** 2026-07-11 · **Escopo:** médio (plugin novo, self-contained; sem mudança no core)
> **Origem:** pedido do usuário — "plugin de retornos automáticos com IA". MVP: quando um cliente atendido por IA fica em silêncio, postar uma NOTA PRIVADA pedindo para reativar a IA.
> **Método:** leitura do código real + 4 sub-agentes de exploração (modelo de atendimentos, papéis de mensagem, tarefas de fundo de plugin, plugin de horário). Todo ponto de mudança abaixo tem `arquivo:linha` verificado.
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.
> **⚠️ Execução em PARALELO:** outra IA está implementando o **Plano 49** (plugin `utm_atendente`) neste mesmo repositório ao mesmo tempo. Toque **somente** em `storages/plugins/retorno_automatico/**`, `tests/test_retorno_automatico.py` e os blocos de status deste arquivo. **Zero** edição no core. Use um banco de teste próprio (`whatsbot_test_48`), rode testes por arquivo e, se subir o servidor, use a porta 8148.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano | Data |
|---|---------|----------------------|------|
| D1 | **Fora do expediente ⇒ enviar no próximo expediente.** O relógio de 3h corre em tempo real; a nota atrasada sai no 1º tick dentro do expediente. | Sem cálculo de "próxima janela": o disparo é **gated** por `is_open_now()`. Nota vencida à noite/fim de semana dispara no próximo dia útil às 08:00. ✅ | 2026-07-11 |
| D2 | **Resposta manual do atendente conta igual à da IA.** Reinicia as 3h; **NÃO** zera a contagem. Só a resposta do **cliente** zera. | O "âncora" do relógio inclui `role='assistant'` com `status='operator'` (manual) **e** IA. O contador só reseta quando `role='user'` (cliente) avança. ✅ | 2026-07-11 |
| D3 | **Ao atingir o limite de notas consecutivas ⇒ apenas parar (standby).** | Sem tag, sem desligar IA, sem transferência. Fica em standby até o cliente responder (reset), aí o ciclo pode recomeçar. ✅ | 2026-07-11 |
| D4 | **Expediente = horário fixo único + dias da semana** (default 08:00–18:00, seg–sex, fuso −3). | Settings simples: `business_start`, `business_end`, 7 toggles de dia, `tz_offset_hours`. Sem intervalo por-dia diferente. ✅ | 2026-07-11 |
| P0 | **Princípio fixo:** o plugin é 100% self-contained em `storages/plugins/retorno_automatico/` — **zero edição no core**. Só lê tabelas core (via repos e/ou SELECT read-only) e escreve `private_note` pela camada de memória existente. | Nenhum arquivo fora de `storages/plugins/retorno_automatico/` é tocado. Distribuição por `.zip` (Importar na tela Plugins). | 2026-07-11 |

---

## 1. Resumo executivo

**Problema.** Quando a IA está atendendo um cliente e ele **para de responder**, a conversa fica parada — a IA só reage a mensagem recebida, então ninguém é avisado de que o cliente sumiu. O usuário quer um "cutucão" interno para um atendente reativar a IA e dar seguimento.

**Solução (MVP).** Um plugin com uma **tarefa supervisionada por minuto** (mesmo mecanismo do `agendamento_retorno`) que varre as conversas **abertas atribuídas a uma IA** e, para cada cliente em silêncio há ≥ N horas (dentro do expediente), posta uma **nota privada** (painel-only) do tipo *"Cliente X sem resposta há 3h — reative a IA (tentativa n/max)"*. Repete a cada N horas até um limite de notas consecutivas; **a resposta do cliente zera tudo**.

**Insight arquitetural — STATELESS.** As próprias `private_note` do plugin (marcadas por um `sent_by_name` reservado) **são a memória**: uma consulta por conversa devolve `last_client_ts`, `last_outbound_ts`, `last_note_ts` e a contagem de notas desde a última resposta do cliente. **Sem tabela, sem migration, sem drift**, e o comportamento se auto-recupera após restart (uma nota vencida durante downtime dispara no próximo tick útil).

---

## 2. Como funciona hoje (mapa do que vamos reaproveitar)

### 2.1 O plugin-modelo: `agendamento_retorno` (blueprint quase idêntico)

Já existe em `storages/plugins/agendamento_retorno/` um plugin que roda um verificador por minuto e, na hora, escreve uma nota privada. Copiamos a **estrutura**, trocando "tabela de agendamentos com `due_at`" por "varredura por regra".

| Peça | Arquivo:linha | O que reusar |
|------|--------------|--------------|
| Loop supervisado por minuto | `storages/plugins/agendamento_retorno/lifecycle.py:20-47` | `setup(ctx)` define `async def _loop(): while True: await asyncio.to_thread(run_cycle); await asyncio.sleep(60)` e registra com `ctx.spawn_task("scheduler", _loop, policy=RestartPolicy.PERMANENT)` |
| Dispatch de nota privada | `storages/plugins/agendamento_retorno/logic.py:260-309` | `deps.agent_handler._get_contact(phone, channel_id=…).add_message("private_note", body, sent_by_user_id=…, sent_by_name=…)` + `broadcast("new_message", {...})` |
| Settings declarativas | `storages/plugins/agendamento_retorno/settings.py:10-20` | `class Settings(BaseModel)` com `Field(...)`; lida em runtime via `config_repo.get(f"plugin.{ID}.{campo}")` (`logic.py:328-335`) |
| Manifesto | `storages/plugins/agendamento_retorno/plugin.yaml` | `entry: { lifecycle, settings }`, `permissions: [db.write]` |

### 2.2 Infra de plugin (core, já pronta — não mudar)

| Recurso | Arquivo:linha | Uso |
|---------|--------------|-----|
| `ctx.spawn_task(name, factory, *, policy, …)` | `plugins/context.py:316-338` | tarefa supervisionada, owner=plugin, auto-parada no disable. Só chamável de dentro de `setup(ctx)` (supervisor já vivo — `server/app.py:373-383`) |
| `RestartPolicy.PERMANENT` | `runtime/supervisor.py:27-31` | loop `while True` relançado se retornar/estourar (com backoff) |
| `make_plugin_db()` | `plugins/context.py:174-177` | `get_engine().begin()` — para SELECT read-only nas tabelas core e (se houvesse) tabelas do plugin |
| `get_deps()` → `deps.agent_handler` | `plugins/context.py:141-143` + `server/app.py:261-265` | acesso ao `AgentHandler` em runtime |
| `broadcast(event, data)` | `plugins/context.py:146-157` | push WS thread-safe, nunca levanta |
| `_get_contact(phone, channel_id=…)` | `agent/handler.py:245` | devolve `ContactMemory` escopada ao canal/inbox |
| `ContactMemory.add_message(role, content, *, sent_by_user_id, sent_by_name, …) -> dict` | `agent/memory.py:329-392` | resolve o atendimento, INSERE via `message_repo.add`, dá `touch_activity`, emite `message.persisted`; **retorna a linha salva** (`id`, `ts`, `conversation_id`) |
| Settings GET/PUT (form auto) | `server/routes/plugins.py:284-326` | `PUT` valida contra `Settings` e grava `plugin.<id>.<campo>` |

### 2.3 Modelo de conversa: tabela `atendimentos` (alias `conversations`)

`db/tables.py:417` (`atendimentos`, renomeada de `conversations`; alias em `:825`). Colunas que o plugin lê:

| Coluna | Linha | Significado p/ elegibilidade |
|--------|------|------------------------------|
| `status` (Text, default `open`) | `db/tables.py:425` | **só** `open`/`closed`. Elegível ⇒ `open` |
| `is_archived` (Integer, default 0) | `:426` | Elegível ⇒ `0` |
| `assignee_user_id` (Integer, nullable) | `:427` | atendente humano. Elegível ⇒ `IS NULL` (ninguém humano assumiu) |
| `ai_active` (Integer, default 1) | `:430` | gate IA nível 3. Elegível ⇒ `1` |
| `active_agent_key` (Text, nullable) | `:431` | agente IA da conversa. Elegível ⇒ `IS NOT NULL` (atribuída a uma IA) |
| `contact_id` (Integer) | `:422` | join com `contacts` (phone, name, is_group) |
| `last_activity_at` (Float, epoch) | `:440` | não usado para decidir (usamos `messages.ts`); útil p/ pré-filtro |

⚠️ **`_conversation_ai_active`** (`app/services/messaging_service.py:1205-1234`) é a fonte-de-verdade de "a IA responderia". Ela desliga quando `ai_active=0` OU (assignee humano setado **sem** `active_agent_key`). Nosso filtro (`ai_active=1 AND active_agent_key IS NOT NULL AND assignee_user_id IS NULL`) é o subconjunto "atribuída a uma IA, sem humano" — na prática `assignee` e `active_agent_key` são mutuamente exclusivos nos fluxos de toggle/transfer, então o filtro é robusto.

### 2.4 Papéis/status de mensagem — como distinguir cliente × IA × operador × nota

`db/tables.py:104-139` + `db/repositories/_mapping.py:103-106` (roles painel-only). Regra de discriminação:

| Tipo | `role` | `status` | `agent_key` | Uso no plugin |
|------|--------|----------|-------------|---------------|
| **Cliente (inbound)** | `user` | `NULL` | `NULL` | `last_client_ts` — o **reset** |
| **IA → cliente** | `assistant` | `sent`/`delivered`/`read` (ou `failed`) | **NOT NULL** | âncora (conta p/ reiniciar 3h) |
| **Operador manual** | `assistant` | `operator` | `NULL` | âncora (D2: conta igual à IA) |
| **Nota privada** | `private_note` | `NULL` (ou `pn:…`) | `NULL` | a memória do plugin (marcada por `sent_by_name`) |

- **Âncora de saída** = `role='assistant' AND status <> 'failed'` (cobre IA + operador; exclui envio falho que não chegou ao cliente).
- Helper pronto p/ o lado cliente: `message_repo.last_inbound_ts(conversation_id=…)` (`db/repositories/message_repo.py:216-241`) — só `role='user'`.

### 2.5 Referência de expediente: `horario_funcionamento` (não está no working tree)

Removido do bundle (existe no git em `0dcf432:assets/plugin_examples/horario_funcionamento/`). **Padrão a copiar** (sem DST, fuso fixo, evita `datetime.now()` naïve):

```python
local = datetime.fromtimestamp(time.time() + tz_offset_hours*3600, timezone.utc)
minute_of_day = local.hour*60 + local.minute          # comparar em minutos
weekday = local.weekday()                              # 0=segunda … 6=domingo
```
Ele **não** calcula "próxima janela" — e não precisamos (D1: gate por `is_open_now`).

---

## 3. Inventário / análise (o que construir)

Estrutura final do plugin (tudo novo, em `storages/plugins/retorno_automatico/`):

| Arquivo | O que faz | Risco | Esforço |
|---------|-----------|:---:|:---:|
| `plugin.yaml` | manifesto: `id`, `entry: {lifecycle, settings}`, `permissions:[db.write]`, sem `migrations` | baixo | S |
| `__init__.py` | vazio (pacote) | baixo | S |
| `settings.py` | `class Settings(BaseModel)` — enabled, silence_hours, max_consecutive_notes, business_start/end, 7 toggles de dia, tz_offset_hours, apply_to_groups, note_template | baixo | S |
| `schedule.py` | funções **puras** de expediente: `parse_hhmm`, `is_open_now(now_epoch, cfg)` | baixo | S |
| `logic.py` | núcleo: `list_candidates()`, `conversation_signals(conv_id)`, `decide(...)`, `dispatch_note(...)`, `run_cycle()` | **médio** | M |
| `lifecycle.py` | `setup(ctx)` → `ctx.spawn_task("scheduler", loop, PERMANENT)`; `teardown` no-op | baixo | S |
| `schedule.test.js` / teste py | (opcional) teste puro da lógica de expediente + decisão | baixo | S |
| **(opcional Fase 6)** `routes.py` + `static/retorno_automatico.js` | tela read-only "em acompanhamento" (contadores por conversa) | baixo | M |

**Sem tabela, sem migration** (design stateless — §1). **Sem tela obrigatória** (MVP = loop + settings).

### 3.1 Falsos positivos descartados

| "Parece que precisa" | Por que **não** precisa |
|----------------------|--------------------------|
| Tabela de estado `plugin_retorno_automatico_*` + migration | As `private_note` do plugin já são o estado (contador = nº de notas com `ts > last_client_ts`; âncora = `MAX(ts)` dessas notas). Menos código, zero drift, auto-recupera pós-restart. |
| Cálculo de "próximo horário de expediente" | D1: basta **gate** por `is_open_now()`; o loop por minuto dispara sozinho no 1º tick útil. |
| Editar o core (`message_repo`/`conversation_repo`) p/ um helper de "last outbound" | Dá pra obter via SELECT read-only parametrizado no `make_plugin_db` (ou `get_by_conversation` + filtro). P0: zero edição no core. |
| Subscrever eventos (`message.sent`) p/ manter estado incremental | Complexidade e edge-cases (toggle IA, fechar conversa) sem ganho no MVP. Varredura por minuto é suficiente e é o padrão do `agendamento_retorno`. |
| Diferenciar "IA re-engajou" de "operador respondeu" | D2: contam **igual**. Ambos entram na âncora de saída; nenhum zera o contador. |
| Ler a tag `transferido_atendente` p/ pausar | plano 37 aposentou essa tag como gate; o filtro `assignee_user_id IS NULL AND ai_active=1` já cobre "humano assumiu". |

---

## 4. Especificação do núcleo (o algoritmo, sem código de implementação)

### 4.1 Settings (`settings.py`)

```
enabled: bool = True                      # master do plugin
silence_minutes: int = 180 (ge=1,le=10080) # silêncio do cliente antes da nota (MINUTOS; 30=meia hora, 1=teste rápido — ajuste pós-impl. a pedido do usuário)
max_consecutive_notes: int = 3 (ge=1,le=20) # limite de notas consecutivas s/ resposta
business_start: str = "08:00"             # "HH:MM"
business_end:   str = "18:00"             # "HH:MM"
day_mon..day_fri: bool = True             # dias ativos (default seg–sex)
day_sat, day_sun: bool = False
tz_offset_hours: float = -3.0 (ge=-12,le=14)
apply_to_groups: bool = False             # default: pula grupos (só cliente individual)
note_template: str = "🔔 O cliente {cliente} está sem responder há {tempo}. "
                     "Reative a IA para dar seguimento. (Tentativa {tentativa}/{max})"
                     # placeholders: {cliente} {tempo}(ex.: 3h/30min) {minutos} {horas} {tentativa} {max}
```
Lidas a **cada ciclo** via `config_repo.get(f"plugin.retorno_automatico.{campo}")` (mudança vale na hora, sem restart — igual `agendamento_retorno/logic.py:328-335`).

### 4.2 Expediente (`schedule.py`, puro/testável)

`is_open_now(now_epoch, cfg) -> bool`:
1. `local = datetime.fromtimestamp(now + tz_offset_hours*3600, timezone.utc)`.
2. `weekday = local.weekday()`; se o toggle daquele dia estiver `False` ⇒ `False`.
3. `m = local.hour*60+local.minute`; `start = parse_hhmm(business_start)`, `end = parse_hhmm(business_end)`; retorna `start <= m < end` (assume expediente diurno `start < end`; se `start >= end`, tratar como "sem expediente" e logar — não há caso overnight nos requisitos).

### 4.3 Enumeração de candidatos (`logic.py::list_candidates`)

Consulta **read-only** (via `make_plugin_db()`), com bind params, retornando exatamente os elegíveis (join p/ pegar phone/nome/grupo):

```sql
SELECT a.id AS conversation_id, a.contact_id, a.inbox_id,
       c.phone, c.name AS contact_name, c.is_group
FROM atendimentos a JOIN contacts c ON c.id = a.contact_id
WHERE a.status='open' AND a.is_archived=0 AND a.ai_active=1
  AND a.active_agent_key IS NOT NULL AND a.assignee_user_id IS NULL
  AND (c.is_group=0 OR :apply_to_groups)
```
> **Alternativa sem SQL core** (se preferir 100% repos): `conversation_repo.list_conversations(status="open", is_archived=0, limit=…)` + filtro em Python — porém tem cap default 100 e exige paginação/`get()` extra p/ os campos. A consulta acima é O(1) por ciclo e mais eficiente; é **read-only** e parametrizada (a regra de prefixo `plugin_<id>_` vale só p/ CREATE/ALTER em migration, não p/ SELECT). **P1 (§8) decide qual seguir.**

O `channel_id` para o dispatch é resolvido só para quem **vai disparar** (poucos), via `conversation_repo.get_with_channel(conv_id)` — evita resolver canal p/ todos os candidatos.

### 4.4 Sinais por conversa (`logic.py::conversation_signals`)

Uma consulta agregada por `conversation_id` (marcador `NOTE_AUTHOR = "Retorno Automático"`):

```
last_client_ts   = MAX(ts) WHERE role='user'
last_outbound_ts = MAX(ts) WHERE role='assistant' AND status <> 'failed'   # IA + operador (D2)
last_note_ts     = MAX(ts) WHERE role='private_note' AND sent_by_name = :NOTE_AUTHOR
notes_since_reply= COUNT(*) WHERE role='private_note' AND sent_by_name = :NOTE_AUTHOR
                            AND ts > COALESCE(last_client_ts, 0)
```
> `last_client_ts` também está disponível via `message_repo.last_inbound_ts(conversation_id=…)`; agregamos tudo numa consulta por eficiência.

### 4.5 Decisão (`logic.py::decide`, pura)

Dado `now`, `signals`, `cfg`:
```
1. se last_outbound_ts é None            → SKIP  (a IA/operador nunca falou; não há retorno a cobrar)
2. cliente_em_silencio = (last_client_ts is None) or (last_client_ts < last_outbound_ts)
   se NÃO cliente_em_silencio            → SKIP  (cliente respondeu — contador zera naturalmente)
3. se notes_since_reply >= max_consecutive_notes → SKIP  (D3: standby até o cliente responder)
4. anchor = max(last_outbound_ts, last_note_ts or 0)
   se (now - anchor) < silence_minutes*60 → SKIP  (ainda não venceu)
5. se NOT is_open_now(now, cfg)          → SKIP  (D1: defere; dispara no próximo tick útil)
6. → FIRE  (tentativa = notes_since_reply + 1)
```
Idempotência: ao disparar, nasce uma nova `private_note` ⇒ no próximo tick `last_note_ts≈now` ⇒ `anchor≈now` ⇒ passo 4 barra por ~3h. Sem contador em memória, sem risco de duplicar.

### 4.6 Dispatch (`logic.py::dispatch_note`) — idêntico ao `agendamento_retorno`

```
deps = get_deps(); ah = deps.agent_handler
channel_id = conversation_repo.get_with_channel(conv_id)["channel_id"]  # canal autoritativo
cm = ah._get_contact(phone, channel_id=channel_id)
body = render(note_template, cliente=contact_name, minutes=silence_minutes,
              tentativa=tentativa, max=max_consecutive_notes)   # {tempo}/{minutos}/{horas} derivados
saved = cm.add_message("private_note", body, sent_by_name=NOTE_AUTHOR)   # sent_by_user_id=None
broadcast("new_message", { phone, channel_id, message: {role:"private_note",
          content:body, ts:saved["ts"], status:None,
          conversation_id:saved["conversation_id"], _id:saved["id"]} })
```
- `sent_by_name = NOTE_AUTHOR` é **o marcador** (aparece no card como "· por Retorno Automático" e serve de chave stateless). `sent_by_user_id=None`.
- Reresolver o canal pelo `conversation_id` evita escrever na inbox errada (mesmo cuidado de `agendamento_retorno/logic.py:284-296`).

### 4.7 Ciclo (`logic.py::run_cycle`) e loop (`lifecycle.py`)

`run_cycle()`: lê cfg; se `enabled` falso ⇒ retorna cedo; `now=time.time()`; para cada candidato: `signals` → `decide` → se FIRE, `dispatch_note` (try/except por item — falha de um não derruba o ciclo). Retorna sumário `{checked, fired, skipped, failed}`.

`setup(ctx)`: `async def _loop(): while True: await asyncio.to_thread(run_cycle); (broadcast tick opcional); await asyncio.sleep(60)` + `ctx.spawn_task("scheduler", _loop, policy=RestartPolicy.PERMANENT)` — aparece como `retorno_automatico:scheduler` no painel de Runtime.

---

## 5. Fases / Roadmap

```
WAVE 0 — Fundação
  F0 Scaffold (plugin.yaml + __init__)      🔴  [bloqueia F1..F5]
  F1 Settings declarativas                  🟢  [depende: F0]

WAVE 1 — Núcleo (funções puras, testáveis isoladas)
  F2 schedule.py (is_open_now)              🟢  ┐ paralelos entre si
  F3 logic: signals + candidates (reads)    🟢  ┘ [dependem: F0]
        (barreira: F2+F3 alimentam F4)

WAVE 2 — Integração
  F4 logic: decide + dispatch + run_cycle   🔴  [depende: F2,F3]
  F5 lifecycle: scheduler (spawn_task)      🔴  [depende: F4]

WAVE 3 — Qualidade / opcional
  F6 Tela read-only "em acompanhamento"     🟢  [opcional; depende: F4]
  F7 Testes + verificação e2e               🔴  [depende: F5]
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|:---:|:---:|---|:---:|:---:|---|
| 0 | F0 | Scaffold/manifesto | 🔴 | baixo | plugin aparece em `/api/plugins`, ativável, sem `load_error` |
| 0 | F1 | Settings | 🟢 | baixo | modal "Configurar" mostra os campos; `PUT` persiste `plugin.retorno_automatico.*` |
| 1 | F2 | Expediente | 🟢 | baixo | `is_open_now` correto p/ dia útil/fim de semana/limites de hora (teste puro) |
| 1 | F3 | Leituras | 🟢 | médio | `list_candidates`/`conversation_signals` retornam valores certos contra dados semeados |
| 2 | F4 | Decisão+Dispatch | 🔴 | médio | numa conversa IA em silêncio, `run_cycle()` posta 1 nota; 2º tick não duplica |
| 2 | F5 | Scheduler | 🔴 | baixo | `retorno_automatico:scheduler` em `GET /api/runtime/tasks` (running); some no disable |
| 3 | F6 | Tela (opcional) | 🟢 | baixo | lista conversas em acompanhamento + contador (read-only, legível no dark) |
| 3 | F7 | Testes/e2e | 🔴 | médio | cenário completo (silêncio→nota→limite→reset) validado |

**Disciplina do repo:** um comportamento por commit; teste/verificação verde ao fim de cada fase; nunca avançar com falha não explicada.

---

### Fase F0 — Scaffold do plugin
**Objetivo:** esqueleto ativável, sem lógica ainda.
**Itens:**
- `[sequencial]` Criar `storages/plugins/retorno_automatico/{__init__.py, plugin.yaml}`. Manifesto: `id: retorno_automatico`, `name: "Retorno Automático (IA)"`, `whatsbot_api_version: ">=1.0,<2.0"`, `entry: {lifecycle: lifecycle, settings: settings}`, `permissions: [db.write]`. Sem `migrations`. Espelhar `storages/plugins/agendamento_retorno/plugin.yaml`.
- `[sequencial]` `lifecycle.py` com `setup(ctx)`/`teardown(ctx)` que só logam (scheduler entra em F5).
- Ativar o plugin pela UII (`POST /api/plugins/retorno_automatico/enable`) e confirmar restart + sem `load_error`.
**Pronto quando:** card do plugin verde em `/plugins`, `GET /api/plugins` sem erro de carga.

#### Status de execução — Fase F0
**Estado:** ✅ Concluída (2026-07-13)
- **O que foi feito:** criados `storages/plugins/retorno_automatico/{__init__.py, plugin.yaml, lifecycle.py}`. Manifesto: `id: retorno_automatico`, `name: "Retorno Automático (IA)"`, `whatsbot_api_version: ">=1.0,<2.0"`, `entry: {lifecycle, settings}`, `permissions: [db.write]`, sem `migrations`. `lifecycle.setup/teardown` (na F0 só logavam; expandidos na F5).
- **Como foi feito / decisões:** manifesto espelha `agendamento_retorno/plugin.yaml` (sem `routes`/`rbac`/`migrations` — MVP é loop + settings, P3=não-tela). Como o harness `build_test_app` copia plugins de `assets/plugin_examples/` (intocável no plano 48), o "carrega limpo" é verificado dirigindo o **loader real** (`discover_and_load`) contra um diretório temporário com só este plugin, e montando um app hermético real (`create_app`) cujo `storages/plugins` contém só ele.
- **Problemas / pendências:** nenhuma. (Descoberta lateral: NÃO adicionar `storages/plugins` ao `sys.path` nos testes — o diretório do plugin `gowa` sombrearia o pacote CORE `gowa`; o import do plugin é feito por `importlib`.)
- **Verificação:** `test_f0_plugin_loads_clean` (loader real: `registry.loaded["retorno_automatico"]`, `load_error` nulo, `settings_cls`/`setup_fn`/`teardown_fn` presentes) e `test_f0_card_no_load_error_via_app` (app real: `GET /api/plugins` lista o card sem `load_error`) — ambos verdes.

---

### Fase F1 — Settings declarativas
**Objetivo:** todos os parâmetros configuráveis no modal "Configurar".
**Itens:**
- `[sequencial]` `settings.py` com o `Settings(BaseModel)` de §4.1 (títulos/descrições PT-BR nos `Field`).
- Verificar `GET /api/plugins/retorno_automatico/settings` (schema+valores) e `PUT` (persistência) — rota core `server/routes/plugins.py:284-326`, nada a mudar.
**Pronto quando:** editar `silence_hours`/`max_consecutive_notes`/expediente na UI e reabrir mostra os valores salvos.

#### Status de execução — Fase F1
**Estado:** ✅ Concluída (2026-07-13)
- **O que foi feito:** `settings.py` com `class Settings(BaseModel)` de §4.1 — `enabled`, `silence_minutes` (ge=1,le=10080; ver ajuste abaixo), `max_consecutive_notes` (ge=1,le=20), `business_start/end`, 7 toggles `day_mon..day_sun` (seg–sex default `True`, sáb/dom `False`), `tz_offset_hours` (ge=−12,le=14), `apply_to_groups` (default `False`), `note_template`. Todos com `title`/`description` PT-BR.
- **Como foi feito / decisões:** valores default refletem as decisões travadas (D1–D4). `logic.load_settings()` lê `plugin.retorno_automatico.*` a cada ciclo (só as chaves salvas sobrescrevem os defaults; config inválida cai para os defaults — fail-safe). A rota core `server/routes/plugins.py:284-326` não foi tocada.
- **Ajuste pós-impl. (2026-07-13, a pedido do usuário):** o campo de silêncio virou **`silence_minutes` (int, minutos, default 180 = 3h)** no lugar de `silence_hours` (float, horas) — permite testar sem esperar horas e configurar valores tipo 30 min. `decide` usa `silence_minutes*60`; o template ganhou `{tempo}` (duração amigável, ex.: "3h"/"30min"/"1min") — o default agora usa `{tempo}`, e `{minutos}`/`{horas}` continuam disponíveis. Plugin ainda não distribuído, sem migração de config.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `test_f1_settings_get_schema_and_values` (schema com os 10 campos + defaults 180/3), `test_f1_settings_put_persists` (PUT `silence_minutes=45` persiste e reflete), `test_f1_load_settings_reflects_config`/`_bad_config_falls_back`, e `test_f4_human_duration_formats` (formatação `{tempo}`) — todos verdes.

---

### Fase F2 — Expediente (`schedule.py`, puro) `[paralelo com F3]`
**Objetivo:** decidir "estamos no expediente agora?" sem tocar em DB.
**Itens:**
- `[paralelo]` `parse_hhmm("HH:MM") -> minutos`; `is_open_now(now_epoch, cfg) -> bool` conforme §4.2, com o offset fixo (padrão `horario_funcionamento`, `git 0dcf432`).
- `[paralelo]` teste puro cobrindo: dia útil dentro/fora, fim de semana (toggle off), borda `end` exclusiva, `start>=end` ⇒ fechado.
**Pronto quando:** teste do módulo verde; nunca usa `datetime.now()` naïve.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída (2026-07-13)
- **O que foi feito:** `schedule.py` puro — `parse_hhmm("HH:MM") -> minutos|None` e `is_open_now(now_epoch, cfg) -> bool` conforme §4.2 (offset fixo via `tz_offset_hours`, `datetime.fromtimestamp(now + tz*3600, timezone.utc)`, nunca `datetime.now()` naïve).
- **Como foi feito / decisões:** `cfg` aceita tanto um `Mapping` (`Settings.model_dump()`) quanto o próprio `Settings` (helper `_cfg_get`) — mantém o módulo puro/testável sem importar pydantic. `start >= end` e HH:MM inválido ⇒ "fechado" + log (sem caso overnight, D4). Fail-defensivo: qualquer exceção ⇒ `False` (pior caso "não dispara agora").
- **Problemas / pendências:** nenhuma.
- **Verificação:** testes puros com epoch FIXO (quarta 12:00 UTC) — `test_f2_parse_hhmm`, dia útil dentro, dia desligado (domingo), antes do início, `end` exclusivo (18:00 fechado / 17:59 aberto), fuso deslocando a janela, `start>=end` fechado, HH:MM inválido fechado — todos verdes.

---

### Fase F3 — Leituras (`list_candidates` + `conversation_signals`) `[paralelo com F2]`
**Objetivo:** obter candidatos e sinais por conversa a partir das tabelas core.
**Itens:**
- `[paralelo]` `list_candidates(apply_to_groups)` — SELECT de §4.3 via `make_plugin_db()` (ou alternativa por repo — ver P1).
- `[paralelo]` `conversation_signals(conv_id)` — consulta agregada de §4.4 (marcador `NOTE_AUTHOR`).
- Semear manualmente (ou via teste) uma conversa IA aberta com msgs `assistant`/`user`/`private_note` e conferir os 4 sinais.
**Pronto quando:** os sinais batem com os dados semeados (inclui casos: sem inbound, nota após inbound, envio `failed` ignorado na âncora).

#### Status de execução — Fase F3
**Estado:** ✅ Concluída (2026-07-13)
- **O que foi feito:** em `logic.py`: `list_candidates(apply_to_groups)` (SELECT read-only de §4.3 via `get_engine().connect()`, parametrizado) e `conversation_signals(conv_id)` (consulta agregada única de §4.4 com `FILTER (WHERE ...)` + CTE `lc` para o `last_client_ts`).
- **Como foi feito / decisões:** **P1 resolvido = (a)** — SELECT read-only parametrizado (O(1) por ciclo; a regra de prefixo `plugin_<id>_` vale só p/ CREATE/ALTER em migration, não p/ SELECT). Âncora de saída = `role='assistant' AND (status IS NULL OR status <> 'failed')` (IA + operador, exclui `failed`). `notes_since_reply` conta só notas com `sent_by_name = NOTE_AUTHOR` e `ts > COALESCE(last_client_ts, 0)`. **P2 = (a)** — marcador só por `sent_by_name` (sem `msg_id` prefixado).
- **Problemas / pendências:** nenhuma. (Postgres-only ⇒ `FILTER`/CTE OK.)
- **Verificação:** `test_f3_list_candidates_eligibility` (matriz: elegível ✓; excluídos: sem agente, humano atribuído, `ai_active=0`, fechada, arquivada, grupo; grupo entra só com `apply_to_groups=True`), `test_f3_conversation_signals` (4 sinais; `failed` NÃO vira âncora; nota de outro autor não conta), `test_f3_signals_note_before_reply_not_counted`, `test_f3_signals_empty_conversation` — todos verdes.

---

### Fase F4 — Decisão + Dispatch + `run_cycle` `[depende: F2,F3]`
**Objetivo:** postar a nota certa, no momento certo, sem duplicar.
**Itens:**
- `[sequencial]` `decide(now, signals, cfg)` conforme §4.5 (pura — testar cada branch: skip por silêncio/limite/vencimento/expediente; fire com `tentativa` correta).
- `[sequencial]` `dispatch_note(candidate, tentativa, cfg)` conforme §4.6 (canal reresolvido, `sent_by_name=NOTE_AUTHOR`, broadcast).
- `[sequencial]` `run_cycle()` amarra tudo com try/except por item + sumário.
**Pronto quando:** com um contato IA em silêncio (ajustando `silence_hours` p/ segundos no teste), 1 tick posta **uma** nota; o tick seguinte **não** duplica; ao semear uma msg `user` posterior, o contador zera e um novo ciclo pode disparar; ao atingir `max_consecutive_notes`, para.

#### Status de execução — Fase F4
**Estado:** ✅ Concluída (2026-07-13)
- **O que foi feito:** em `logic.py`: `decide(now, signals, cfg) -> Decision(fire, reason, tentativa)` (pura, §4.5), `render_note(...)` (format seguro — placeholder desconhecido fica literal; `{horas}` via `%g`), `dispatch_note(candidate, tentativa, cfg)` (§4.6 — reresolve canal por `conversation_repo.get_with_channel`, `ah._get_contact(...).add_message("private_note", body, sent_by_name=NOTE_AUTHOR)`, `broadcast("new_message", ...)`) e `run_cycle()` (lê cfg; `enabled=False` retorna cedo; try/except por item; sumário `{checked, fired, skipped, failed, checked_at, enabled}`).
- **Como foi feito / decisões:** **P4 = (a)** — `{horas}` usa `silence_hours` configurado. Idempotência garantida pela âncora `max(last_outbound_ts, last_note_ts)`: logo após disparar, `last_note_ts≈now` ⇒ passo 4 (`nao_venceu`) barra por ~N horas, sem contador em memória. Dispatch idêntico ao `agendamento_retorno` (mesmo cuidado de canal autoritativo p/ não escrever na inbox errada).
- **Problemas / pendências:** nenhuma.
- **Verificação:** `decide` — 7 branches (`sem_saida`, `cliente_respondeu`, `limite_atingido`, `nao_venceu`, nota re-ancora, `fora_expediente`, `fire` com `tentativa` correto). `run_cycle`+dispatch (com `wired_deps` + expediente forçado por fuso p/ determinismo): dispara 1 nota e **2º tick não duplica**; operador conta como âncora (D2); envio `failed` não dispara; resposta do cliente reseta e novo ciclo dispara; limite ⇒ standby (D3); grupo excluído por padrão; fora do expediente defere e dispara no 1º tick útil (D1); `enabled=False` inerte; template renderizado com nome/horas/tentativa. Todos verdes.

---

### Fase F5 — Scheduler supervisado `[depende: F4]`
**Objetivo:** rodar `run_cycle` a cada 60s como tarefa owned pelo plugin.
**Itens:**
- `[sequencial]` `setup(ctx)` cria o loop e chama `ctx.spawn_task("scheduler", _loop, policy=RestartPolicy.PERMANENT)` (`plugins/context.py:316`); espelhar `agendamento_retorno/lifecycle.py:20-47`.
- (opcional) `ctx.broadcast("retorno_automatico_tick", {...})` p/ observabilidade.
**Pronto quando:** `GET /api/runtime/tasks` lista `retorno_automatico:scheduler` como `running`; ao desativar o plugin, a tarefa some (via `stop_owner`).

#### Status de execução — Fase F5
**Estado:** ✅ Concluída (2026-07-13)
- **O que foi feito:** `lifecycle.setup(ctx)` define `async def _scheduler_loop(): while True: run_cycle (via asyncio.to_thread); tick opcional; sleep(60)` e registra com `ctx.spawn_task("scheduler", _scheduler_loop, policy=RestartPolicy.PERMANENT)`. `teardown` no-op (a tarefa é parada pelo `stop_owner`). Espelha `agendamento_retorno/lifecycle.py`.
- **Como foi feito / decisões:** broadcast de `retorno_automatico_tick` opcional (observabilidade) — nunca derruba o laço. `run_cycle` roda em thread para não bloquear o loop async.
- **Problemas / pendências:** nenhuma. (No harness padrão o lifespan é no-op, então o teste dirige o supervisor DIRETO: monta `TaskSupervisor`, chama `setup(ctx)` e valida registro/estado/parada — cobre o contrato sem subir o servidor inteiro.)
- **Verificação:** `test_f5_scheduler_registers_and_stops` — após `setup(ctx)`, `retorno_automatico:scheduler` aparece com `state="running"`, `owner="retorno_automatico"`, `policy="permanent"`; após `stop_owner("retorno_automatico")` fica `stopped`. Verde.

---

### Fase F6 — Tela read-only "em acompanhamento" (OPCIONAL)
**Objetivo:** visibilidade de quais conversas estão em follow-up e em qual tentativa.
**Itens:**
- `[opcional]` `routes.py` (`GET /items` derivado dos sinais, sem tabela) + `static/retorno_automatico.js` (screen normal). RBAC `view`. **Modo escuro:** usar classes `wa-*`/`.wa-field`.
**Pronto quando:** tela lista conversas com contador e "próximo disparo ~"; legível no dark. _(Pode ser cortada do MVP.)_

#### Status de execução — Fase F6
**Estado:** ⏭️ Não implementada (decisão P3 — fora do MVP)
- **O que foi feito:** nada, por decisão. P3 (§8) já decidiu em 2026-07-11: **não** incluir a tela no MVP — a nota no fio da conversa já é o feedback visível.
- **Como foi feito / decisões:** o manifesto não declara `routes`/`screens`; nenhum `routes.py`/`static/` foi criado. O gancho está documentado em §7 para evolução futura.
- **Problemas / pendências:** nenhuma — fase opcional, deliberadamente cortada.
- **Verificação:** n/a.

---

### Fase F7 — Testes + verificação e2e `[depende: F5]`
**Objetivo:** cenário completo verde e sem regressão.
**Itens:**
- `[sequencial]` Teste do fluxo: silêncio→nota(1)→re-engajo IA/operador→nota(2)→limite(3)→standby; resposta do cliente ⇒ reset ⇒ novo ciclo.
- `[sequencial]` Rodar a suíte core no Postgres de teste (garantir que o plugin ativo não quebra nada) e exportar `.zip` p/ distribuição.
**Pronto quando:** teste do cenário verde; suíte core verde; `.zip` importável reproduz o plugin.

#### Status de execução — Fase F7
**Estado:** ✅ Concluída (2026-07-13)
- **O que foi feito:** `tests/test_retorno_automatico.py` com **36 testes** cobrindo F0→F5 + o cenário e2e (silêncio→nota→re-engajo→limite→standby; resposta do cliente ⇒ reset ⇒ novo ciclo) + idempotência de 2 ticks + `failed`-não-âncora + operador-conta (D2) + fora-do-expediente-defere (D1). Round-trip do `.zip`: `test_f7_export_zip_roundtrips` bate no `GET /api/plugins/retorno_automatico/export` real, confere o conteúdo e **reimporta pelo loader** (carrega limpo).
- **Como foi feito / decisões:** testado no banco **`whatsbot_test_48`** (`ENCODING 'UTF8' TEMPLATE template0`), rodando por arquivo. Seeding via repos do core (padrão `test_human_gate`). Expediente forçado determinístico por fuso calculado (evita flakiness contra o relógio real). `.zip` distribuível gerado no scratchpad (`retorno_automatico-plugin.zip`, 6 arquivos, sem `__pycache__`).
- **Problemas / pendências:** nenhuma. (2 achados menores durante a execução, já corrigidos: `sys.path` do plugins-root sombreava o core `gowa` ⇒ import por `importlib`; e dois testes de `decide` usavam saída de 2,78h < 3h ⇒ ajustados.)
- **Verificação:** `pytest tests/test_retorno_automatico.py` ⇒ **36 passed**. Suíte core no `whatsbot_test_48` (`tests/test_endpoints.py`) ⇒ **1215 passed, 0 failed** (o plugin em `storages/plugins` não regride o core; app real sobe com o plugin carregado via a fixture `plugin_http`). Checklist §10 todo verde.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Marcador stateless (`sent_by_name`) | Uma nota manual de operador com o mesmo nome poluiria a contagem | `NOTE_AUTHOR` é constante interna reservada ("Retorno Automático"); improvável coincidir com nome de atendente. Alternativa reforçada: também setar um `msg_id` com prefixo `rauto:` (a confirmar em P2) |
| Escrever na inbox errada (multicanal) | `add_message` resolve conversa por phone+canal; canal defasado cria conversa nova | Reresolver `channel_id` pelo `conversation_id` antes do dispatch (§4.6) — mesmo cuidado do `agendamento_retorno` |
| Envio `failed` como âncora | Um envio da IA que falhou "reiniciaria" as 3h sem o cliente ter sido tocado | Âncora exclui `status='failed'` (§2.4/§4.4) |
| Custo por ciclo (varredura) | Muitas conversas abertas ⇒ N consultas/min | `list_candidates` já filtra no SQL (poucos candidatos); `conversation_signals` é 1 consulta agregada com índice `idx_msg_conversation_ts`. Se crescer, considerar pré-filtro por `last_activity_at` |
| Fuso fixo (−3, sem DST) | Se o Brasil reintroduzir horário de verão, a janela desloca 1h | Aceitável hoje (Brasil sem DST). `tz_offset_hours` é configurável; padrão `ZoneInfo` com fallback fixo (estilo `gowa/alerts.py`) fica p/ evolução |
| Gate global de IA (`auto_reply`) desligado | Nota "reative a IA" sem sentido se a IA global está off | MVP ignora (escopo por-conversa, conforme pedido). Evolução: opcional checar `auto_reply`/`ai_enabled` do canal |
| Restart de plugin (toggle) | Loop precisa de supervisor pra voltar | `ctx.spawn_task` + `RestartPolicy.PERMANENT`; em dev, restart via `restart.py` (já coberto pelo core) |
| Nota vira contexto do LLM? | `private_note` poluir o histórico da IA | `private_note` é painel-only (`_mapping.py:103-106`); não vai ao LLM. Sem ação necessária |
| Grupos | Disparar em grupos com IA | `apply_to_groups=False` por padrão (filtro em `list_candidates`) |

---

## 7. Evoluções futuras (fora do MVP — não implementar agora)

| Ideia | Gancho já existente |
|-------|--------------------|
| **Modo "auto-reengajar"**: em vez de só a nota, a IA envia um follow-up real ao cliente | `POST /api/contacts/{phone}/private-message` com `ai_read=true, ai_reply=true` (`server/routes/contacts.py:1251`) → `_run_private_ia` → `aprocess_message` (`:1045`). Modelar como `mode` na config (nota × reengajo), espelhando `send_mode` do `agendamento_retorno` |
| Regras/IFs por segmento (tag, canal, agente) | Ler tags do contato / `channel_id` no `decide` |
| Config **por canal** (expediente/horas diferentes) | AI é per-canal (`channels/ai_settings.py`); mover settings p/ per-channel depois |
| Escalonamento no limite (tag/transfer) | D3 mantém "só parar" hoje; trocar o passo 3 do `decide` no futuro |

---

## 8. Perguntas em aberto

- **P1 — Enumerar candidatos: SELECT read-only no core vs repos?**
  Contexto: `list_candidates` precisa de um filtro que os repos não expõem exatamente. (a) SELECT parametrizado read-only via `make_plugin_db` (eficiente, mas lê tabela core direto). (b) `conversation_repo.list_conversations` + filtro em Python (100% repo, mas cap 100/paginação + `get()` extra).
  **Recomendação:** (a) — read-only, parametrizado, O(1) por ciclo. ⏸️ ADIADO p/ execução (confirmar preferência de convenção com o dono do core).

- **P2 — Reforçar o marcador da nota com `msg_id` prefixado?**
  (a) só `sent_by_name=NOTE_AUTHOR` (simples). (b) também `msg_id="rauto:"+uuid` p/ discriminação à prova de homônimo.
  **Recomendação:** (a) no MVP; adotar (b) se aparecer colisão real. ⏸️ ADIADO.

- **P3 — Incluir a tela opcional (F6) no MVP?**
  ✅ DECIDIDO (2026-07-11): **não** — MVP é loop + settings; a nota no fio da conversa já é o feedback visível. F6 fica como fase opcional.

- **P4 — `{horas}` no template = limite configurado ou tempo real decorrido?**
  (a) `silence_hours` configurado (simples). (b) elapsed real desde a âncora (pode ser >3h se deferido p/ o próximo expediente).
  **Recomendação:** (a). ⏸️ ADIADO (trivial trocar depois).

---

## 9. Apêndice — arquivos que o executor vai criar/tocar

**Novos (todos em `storages/plugins/retorno_automatico/`):**
- `plugin.yaml`, `__init__.py`, `settings.py`, `schedule.py`, `logic.py`, `lifecycle.py`
- (opcional F6) `routes.py`, `static/retorno_automatico.js`
- (teste) módulo de teste puro p/ `schedule.py` + `decide`

**Core — somente leitura/referência (NÃO editar):**
- `storages/plugins/agendamento_retorno/{lifecycle,logic,settings}.py` + `plugin.yaml` (blueprint)
- `plugins/context.py:141,146,174,316` · `runtime/supervisor.py:27` · `server/app.py:261-265,373-383`
- `agent/handler.py:245` · `agent/memory.py:329-392`
- `db/repositories/message_repo.py:15,76,216` · `db/repositories/conversation_repo.py` (`get_with_channel`, `list_conversations`)
- `db/tables.py:417-458,104-139` · `db/repositories/_mapping.py:103-106`
- `app/services/messaging_service.py:1205-1234`
- `server/routes/plugins.py:284-326`
- (futuro) `server/routes/contacts.py:1045,1251`
- (referência expediente) `git show 0dcf432:assets/plugin_examples/horario_funcionamento/filters.py`

---

## 10. Checklist de verificação

- [x] Plugin ativa/desativa sem `load_error`; restart de plugin OK (supervisor relança). — `test_f0_plugin_loads_clean`, `test_f0_card_no_load_error_via_app`; supervisor `PERMANENT` (`test_f5_scheduler_registers_and_stops`).
- [x] `retorno_automatico:scheduler` aparece `running` em `GET /api/runtime/tasks` e some no disable. — `test_f5_scheduler_registers_and_stops` (contrato do supervisor: registro `running` → `stop_owner` → `stopped`).
- [x] Settings persistem em `plugin.retorno_automatico.*` e valem no ciclo seguinte (sem restart). — `test_f1_settings_put_persists` + `test_f1_load_settings_reflects_config`.
- [x] Teste puro de `is_open_now` + `decide` verde (pytest do módulo). — 8 testes `test_f2_*` + 7 `test_f4_decide_*`.
- [x] Cenário e2e: silêncio→nota(1)→nota(2)→limite(3)→standby; resposta do cliente ⇒ reset ⇒ novo ciclo. — `test_f4_client_reply_resets_then_new_cycle`, `test_f4_limit_standby`, `test_f4_run_cycle_fires_then_idempotent`.
- [x] 2 ticks seguidos **não** duplicam nota (idempotência). — `test_f4_run_cycle_fires_then_idempotent`.
- [x] Envio `failed` **não** conta como âncora; resposta manual do operador conta (D2). — `test_f4_failed_send_not_anchor`, `test_f4_operator_counts_as_anchor`, `test_f3_conversation_signals`.
- [x] Fora do expediente/fim de semana **não** dispara; dispara no 1º tick útil (D1). — `test_f4_outside_hours_defers_then_fires`, `test_f2_is_open_day_toggle_off`.
- [x] `private_note` não entra no contexto do LLM nem no preview da sidebar. — garantido pelo core (`_mapping.LIST_PANEL_ONLY_ROLES` inclui `private_note`); o plugin usa a role painel-only existente, sem código novo. Sem regressão (suíte core verde).
- [x] Suíte core verde no Postgres de teste (`WHATSBOT_TEST_DB_URL`) com o plugin ativo. — `tests/test_endpoints.py` no `whatsbot_test_48`: **1215 passed, 0 failed**; app real sobe com o plugin carregado (`plugin_http`).
- [—] (se F6) tela legível no modo escuro (`wa-*`/`.wa-field`). — F6 fora do MVP (P3); n/a.
- [x] `.zip` exportado (`GET /api/plugins/retorno_automatico/export`) reimporta e reproduz o plugin. — `test_f7_export_zip_roundtrips` (export real → reimport pelo loader).
