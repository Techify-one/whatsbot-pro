# Plano 129 — Ordem de mensagens: persistir o timestamp REAL do provedor no inbound

> **Status:** ✅ EXECUTADO (F0–F2; F3 opt-in aguardando decisão de P3) · **Data:** 2026-08-18 · **Escopo:** pequeno/médio (backend only, sem migration) · **Onde:** worktree `plano-129-ordem-mensagens` (branch off `developer`) — não mesclado
> **Origem:** incidente em produção (conversa 15651, canal Telegram) — uma resposta citada (`reply_to_msg_id`) apareceu ACIMA da mensagem original ao reabrir a conversa. **Método:** investigação no banco de produção via VAULT + leitura do código real (`arquivo:linha` verificados) + sub-agente de varredura.
> **O quê/porquê:** hoje TODA mensagem é carimbada com `ts = time.time()` no momento do INSERT (relógio do servidor), e o timestamp real do provedor (`event.ts` — Telegram `date`, GOWA `timestamp`, Cloud `timestamp`), embora capturado, é **descartado antes de persistir**. Uma mensagem recebida com atraso (entrega tardia do provedor / re-poll pós-restart) ganha um `ts` de "agora" e afunda para depois de mensagens que na verdade vieram depois dela — e a thread é ordenada por `(ts, id)`. O conserto é propagar `event.ts` até o `add_message`/`message_repo.add` no caminho de INBOUND, mantendo `time.time()` no outbound.
>
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0 — Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 ✅ (2026-08-18) | O `ts` do INBOUND passa a ser o timestamp real do provedor (`event.ts`), com fallback para `time.time()` quando ausente/zero. | Muda os sites de save de inbound; **guard `event.ts or time.time()` obrigatório** (nunca gravar `0.0` = epoch 1970). |
| D2 ✅ (2026-08-18) | O OUTBOUND (operador/IA) **continua** com `time.time()` no momento do envio. | O `save_operator_message`/`save_assistant_message` **não mudam** — o `ts` de saída é a hora real da ação. |
| D3 ✅ (2026-08-18) | A ordenação da sidebar (`last_activity_at` via `touch_activity`) **continua em `now()`**, não no `ts` da mensagem. | Uma mensagem atrasada ainda sobe a conversa no topo (atividade recente), mas assenta na posição cronológica CERTA dentro da thread. `touch_activity` **não** recebe o `event.ts`. |
| D4 ✅ (2026-08-18) | Sem migration de schema. | A coluna `messages.ts` já existe ([db/tables.py:122](db/tables.py#L122)) e `message_repo.add` **já aceita** `ts=` ([db/repositories/message_repo.py:26](db/repositories/message_repo.py#L26)). |
| D5 ✅ (2026-08-18) | Reparo de dados históricos (linhas já fora de ordem) é **fora de escopo** desta correção. | Vira pergunta em aberto (P3), opt-in/manual — o `ts` real dos registros antigos foi descartado e não é recuperável de forma confiável. |

**Princípio fixo:** este é um bug de corrupção de ordenação em produção; a correção é de raiz (persistir o dado que já temos), **sem stopgap** de reordenar no cliente.

---

## 1 — Resumo executivo

A thread do chat é ordenada por `(ts, id)` com `ts` como chave primária. O `ts` de cada linha é gravado com o **relógio do servidor no INSERT** ([db/repositories/message_repo.py:40](db/repositories/message_repo.py#L40) `ts = ts or time.time()`), e **nenhum caminho de produção** passa `ts=`. O timestamp real do provedor viaja no `InboundEvent.ts` ([channels/events.py:29](channels/events.py#L29)) e é preenchido por todos os providers (GOWA/Telegram/Cloud), mas é **jogado fora** na fila do batch e nunca chega ao `add_message`. Resultado: mensagem entregue com atraso → `ts` de "agora" → ordena depois de mensagens posteriores → visualmente "resposta acima da original".

A solução: propagar `event.ts` (com fallback seguro) pelos **quatro** sites de save de inbound (batch de texto, batch de mídia, grupo-sem-@menção, echo), adicionando um parâmetro `ts` ao `ContactMemory.add_message`. Outbound e ordenação da sidebar ficam intactos.

---

## 2 — Como funciona hoje (mapa verificado)

### 2.1 Um único caminho de inbound

Todo inbound (GOWA incluído) entra em `POST /api/webhook/{provider}/{channel_id}` → `deps.ingest_event` → `MessageIngestService.ingest_event(InboundEvent)` — o fallback legado `/api/webhook` foi aposentado ([server/routes/webhook.py:1-7](server/routes/webhook.py#L1-L7)). Ou seja, **existe uma fonte única de `ts` de provedor**: `event.ts`.

### 2.2 O provedor JÁ entrega o `ts` real — e ele é descartado

| Provider | Onde `event.ts` é preenchido | Fonte |
|---|---|---|
| GOWA | [gowa/inbound.py:502,515,526,537,734](gowa/inbound.py#L502) | `data.get("timestamp") or 0.0` |
| Telegram | [storages/plugins/telegram/channels.py:501](storages/plugins/telegram/channels.py#L501) | `_to_float(msg.get("date"))` |
| WhatsApp Cloud | [storages/plugins/whatsapp_cloud/channels.py:1462,1508,1558](storages/plugins/whatsapp_cloud/channels.py#L1462) | `_to_float(...timestamp)` |

⚠️ Todos caem em **`0.0`** quando o campo falta — por isso o fallback `event.ts or time.time()` é **obrigatório** em cada site de save (gravar `0.0` colocaria a mensagem em 1970 e quebraria a ordenação de vez).

O `event.ts` chega ao `parsed_msg["ts"]` ([app/services/message_ingest_service.py:479](app/services/message_ingest_service.py#L479)) — **mas esse dict só alimenta o broadcast do WebSocket e o emit `message.received`**. O item que entra na fila do batch **não carrega `ts`** ([app/services/message_ingest_service.py:581-590](app/services/message_ingest_service.py#L581-L590)).

### 2.3 Os sites de save de inbound (onde o `ts` do provedor deveria entrar)

| Site | Arquivo:linha | Situação hoje |
|---|---|---|
| Batch de texto (combina N msgs em 1 linha) | [app/services/messaging_service.py:1106](app/services/messaging_service.py#L1106) | `add_message("user", combined, msg_id=last_msg_id, ...)` — **sem `ts`** |
| Batch de mídia (1 linha por mídia) | [app/services/messaging_service.py:1234](app/services/messaging_service.py#L1234) | `add_message("user", ..., msg_id=item.get("msg_id"), ...)` — **sem `ts`** |
| Grupo sem @menção (salva mas não roda IA) | [app/services/message_ingest_service.py:552](app/services/message_ingest_service.py#L552) | `add_message("user", ui_text, ...)` — **sem `ts`** (tem `event.ts` no escopo) |
| Echo (msg enviada do celular, Meta) | [app/services/message_ingest_service.py:303](app/services/message_ingest_service.py#L303) | `add_message("assistant", text, ...)` — **sem `ts`** (tem `event.ts` no escopo) |

`ContactMemory.add_message` ([agent/memory.py:436-473](agent/memory.py#L436-L473)) **não tem** parâmetro `ts` — ele nunca repassa ao `message_repo.add` (que já o aceita).

### 2.4 Ordenação e reconciliação (por que reabrir mostra o bug, ao vivo não)

- Leitura da thread: `_select_messages` ordena por `ts`/`(ts, id)` ([db/repositories/message_repo.py:194,200-202,205-208](db/repositories/message_repo.py#L194)); o cursor keyset é o par `(ts, id)` ([_keyset:134-176](db/repositories/message_repo.py#L134-L176)). **`ts` domina; `id` só desempata `ts` igual.**
- Ao vivo o painel **anexa** na ordem de chegada (append-only, sem re-sort no cliente) — por isso a ordem parece certa. Ao reabrir, relê do banco por `ts` e a mensagem atrasada assenta na posição do seu `ts` de insert (errada).
- O `new_message` autoritativo pós-save usa `saved["ts"]` ([app/services/realtime_broadcast.py:61](app/services/realtime_broadcast.py#L61) `build_inbound_saved_message`), mas a **reconciliação otimista é por `msg_id`/`_id`, não por `ts`** — então mudar o `ts` da linha não quebra o dedup do frontend.

### 2.5 Evidência do incidente (produção, conversa 15651)

IDs de mensagem do Telegram são estritamente crescentes por chat ⇒ ordem de envio real indiscutível:

| msg_id Telegram | id no banco | `ts` gravado (BRT) | papel |
|---|---|---|---|
| **75198** (cliente) | **671545** | **15:12:01.441** | a **original** "Nesse script…" |
| 75199–75202 (Matheus) | 671526–671530 | 15:09:26 – 15:10:06 | respostas do operador |
| 75200 (Matheus, `reply_to=75198`) | 671528 | 15:09:55 | a **resposta citada** |
| 75204 (cliente, imagem) | 671546 | 15:12:01.457 | +16 ms de 75198 |

75198 foi enviada ANTES de 75199 (Telegram), mas gravada 2,5 min DEPOIS (15:12:01), a 16 ms da imagem 75204 — prova de que o `ts` é o relógio de INSERT do lote, não o horário real. Prevalência medida: em 30 dias, **1** em **1.374** respostas com citação ficou fora de ordem (este caso). Raro, mas permanente (o `ts` nunca é corrigido depois).

---

## 3 — Inventário de mudanças

| # | Arquivo:linha | O que falta | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|
| M1 | [agent/memory.py:436-445](agent/memory.py#L436-L445) | `add_message` não tem parâmetro `ts` | Adicionar `ts: float \| None = None` na assinatura | baixo | S |
| M2 | [agent/memory.py:464-473](agent/memory.py#L464-L473) | não repassa `ts` | Encaminhar `ts=ts` ao `message_repo.add` (já aceita) | baixo | S |
| M3 | [app/services/message_ingest_service.py:581-590](app/services/message_ingest_service.py#L581-L590) | item da fila do batch dropa `ts` | Incluir `"ts": event.ts` no dict do item | baixo | S |
| M4 | [app/services/messaging_service.py:1050-1066](app/services/messaging_service.py#L1050-L1066) + [:1106](app/services/messaging_service.py#L1106) | linha combinada de texto sem `ts` | Coletar `text_ts_last` no loop de itens (junto de `text_msg_ids`); passar `ts=(text_ts_last or time.time())` — coerente com `last_msg_id` (o `ts` do ÚLTIMO item) | médio | M |
| M5 | [app/services/messaging_service.py:1234-1241](app/services/messaging_service.py#L1234-L1241) | mídia sem `ts` | Passar `ts=(item.get("ts") or time.time())` | baixo | S |
| M6 | [app/services/message_ingest_service.py:552-554](app/services/message_ingest_service.py#L552-L554) | grupo-sem-@menção sem `ts` | Passar `ts=(event.ts or time.time())` | baixo | S |
| M7 | [app/services/message_ingest_service.py:303](app/services/message_ingest_service.py#L303) | echo sem `ts` | Passar `ts=(event.ts or time.time())` | baixo | S |
| — | [db/repositories/message_repo.py:26,40](db/repositories/message_repo.py#L26) | — | **NENHUMA** — já aceita `ts` e já faz `ts or time.time()` | — | — |
| M8 ⚠️ | `tests/goldens/exec_{ai_turn_no_tools,ai_turn_with_tool,cost_accumulation,gate_off}.json` | **descoberto na F2, não previsto** — o step `webhook_received` de `executions` grava o item da fila VERBATIM, então a chave `"ts"` do M3 entra no dump | Regenerar com `UPDATE_GOLDENS=1` escopado a `test_execution_characterization.py`; conferir que o diff é só `"ts": "<TS>"` | baixo | S |

**Guard único (repetir em M4–M7):** o valor efetivo é sempre `provider_ts or time.time()`. Onde o `add_message` recebe `ts=None`, o `message_repo.add` já aplica `time.time()` — então basta **não** forçar `0.0`: passe `event.ts or None` (ou `event.ts or time.time()`), nunca `event.ts` cru.

### 3.1 Falsos positivos descartados

| Candidato | Por que NÃO mexer | Evidência |
|---|---|---|
| `message_repo.add` | Já aceita `ts` e já tem o fallback correto | [db/repositories/message_repo.py:26,40](db/repositories/message_repo.py#L26) |
| Providers (gowa/telegram/cloud) | Já preenchem `event.ts` corretamente | [gowa/inbound.py:502](gowa/inbound.py#L502), [telegram:501](storages/plugins/telegram/channels.py#L501), [cloud:1462](storages/plugins/whatsapp_cloud/channels.py#L1462) |
| Saves de OUTBOUND (`save_operator_message`/`save_assistant_message`) | `ts` de saída = hora real da ação (D2) | [agent/handler.py:381-414](agent/handler.py#L381) |
| `touch_activity` / ordem da sidebar | Deve continuar em `now()` (D3) — senão msg atrasada não sobe a conversa | [db/repositories/conversation_repo.py:906-909](db/repositories/conversation_repo.py#L906-L909) |
| Broadcast otimista t=0 (`"ts": time.time()`) | É metadado de UI para o append ao vivo, reconciliado por `msg_id` | [app/services/message_ingest_service.py:518](app/services/message_ingest_service.py#L518) |
| `"ts": time.time()` nos payloads `message.saved`/`message.received` | Sinais de bus (metadado do evento), não a linha persistida — fora de escopo (ver P2) | [message_ingest_service.py:569](app/services/message_ingest_service.py#L569), [messaging_service.py:1260](app/services/messaging_service.py#L1260) |
| Reconciliação/ordenação no frontend | Nunca re-sorta no cliente; confia no `(ts, id)` do servidor | [web/static/js/services/threadData.js:85-86](web/static/js/services/threadData.js#L85-L86) |
| Migration de schema | Coluna `ts` já existe; nada a migrar (D4) | [db/tables.py:122](db/tables.py#L122) |

---

## 4 — Fases / Roadmap

### 4.1 Dependências (waves)

```
WAVE 0   F0 (caracterização + teste que REPRODUZ o bug)        🔴 sozinha, ANTES de tudo
            │  (barreira: F0 fixa o comportamento antes da mudança)
WAVE 1   F1 (propagar ts nos 4 sites + add_message)            🔴 sozinha (um refactor, um commit)
            │
WAVE 2   F2 (verde: reprodução vira passa + regressão)         🔴 depende de F1
         F3 (reparo histórico — OPT-IN)                        🟢 independente [ver P3]
```

### 4.2 Tabela de fases

| Wave | Fase | Workstream | Paraleliz. | Risco | Pronto quando / obs |
|---|---|---|---|---|---|
| 0 | F0 | Testes de caracterização + reprodução | 🔴 FAÇA SOZINHA | baixo | Suíte de integração fixa o save atual + 1 teste vermelho que reproduz "reply antes da citada" [bloqueia: F1] |
| 1 | F1 | Propagar `event.ts` (M1–M7) | 🔴 FAÇA SOZINHA | médio | Um único refactor coeso, um commit [depende de: F0] |
| 2 | F2 | Regressão verde | 🔴 depende de F1 | baixo | Teste de reprodução passa; suíte do core verde no Postgres |
| 2 | F3 | Reparo de linhas históricas | 🟢 PODE AGRUPAR | médio | Opt-in; só se P3 for DECIDIDO fazer [independente do código] |

> Observação honesta de paralelização: o núcleo é **sequencial** (F0→F1→F2). A única frente genuinamente paralela é **F3** (reparo de dados), que não toca no código de F1 e só roda se decidido. Redação dos testes novos de F0 pode ser rascunhada em paralelo à leitura, mas a validação depende do estado pré-mudança.

---

### Fase F0 — Caracterização + reprodução (🔴 antes de mexer)

**Objetivo:** travar o comportamento atual e ter um teste que **reproduz** o bug (vermelho hoje, verde depois de F1).

**Itens:**
1. [sequencial] Teste de integração que faz `add_message("user", ...)` **com** `ts` explícito e confirma que a linha persiste esse `ts` (fixa o contrato de `message_repo.add`, que já aceita `ts`).
2. [sequencial] Teste de **reprodução** do incidente, no caminho de save real: simular um inbound cujo `event.ts` é ANTERIOR ao de um outbound já salvo (mensagem "atrasada") e assertar a ordem de `get_by_conversation(...)`:
   - **hoje** (pré-F1): a mensagem inbound recebe `ts≈now()` → ordena DEPOIS do outbound → teste vermelho no assert de ordem cronológica.
   - **pós-F1**: recebe `event.ts` real → ordena ANTES → verde.
   - Cobrir o caso do **reply**: inbound `X` (event.ts cedo) salvo tarde; outbound `Y` com `reply_to_msg_id=X` salvo antes; após reload, `X` deve vir ANTES de `Y`.
3. [paralelo] Teste do **guard**: inbound com `event.ts = 0.0` (campo ausente) deve cair em `time.time()`, nunca gravar `0.0`.

**Pronto quando:** o teste de reprodução está **vermelho** de forma explicada (ordena errado), os testes de caracterização/guard passam contra o código atual, e a suíte roda no Postgres de teste (`WHATSBOT_TEST_DB_URL`).

#### Status de execução — Fase F0
**Estado:** ✅ Concluída (2026-08-18)
- **O que foi feito:** Novo `tests/integration/test_inbound_provider_ts_ordering.py` com 4 testes: F0.1 contrato (`message_repo.add(ts=)` persiste), F0.2 reprodução (inbound atrasado ordena antes do outbound posterior), F0.2b reprodução do incidente (resposta citada não sobe acima da original), F0.3 guard (`event.ts=0.0` cai em `time.time()`).
- **Como foi feito / decisões:** Driver = rota GOWA real (`POST /api/webhook/gowa/default`), que copia `payload.timestamp`→`event.ts` verbatim — controla o relógio do provedor sem depender de plugin externo (pura-core). Epoch fixo `BASE=1_600_000_000` separa "ts do provedor" de `time.time()` sem ambiguidade. Trabalho feito num **git worktree** (`plano-129-ordem-mensagens`, branch off `developer`) + banco de teste isolado `whatsbot_test_plano129` (UTF8/template0) — porque outra IA executa o plano 128 em paralelo (dev server com `--reload` no dir principal; teste concorrente no `whatsbot_test`).
- **Problemas / pendências:** As linhas do `message_repo` não expõem `id` no dict (`_row_to_dict`), só `msg_id`/`ts`/`conversation_id` — o fetch-back casa por `msg_id`.
- **Verificação:** Pré-F1: F0.1 + F0.3 verdes, F0.2 + F0.2b **vermelhos** (ordem com a original por último). É a reprodução esperada.

---

### Fase F1 — Propagar o `ts` do provedor (🔴 um refactor, um commit)

**Objetivo:** o `ts` de INBOUND passa a ser `event.ts` real, com fallback seguro; outbound e sidebar intactos.

**Itens (ordem sugerida):**
1. [sequencial] **M1+M2** — `ContactMemory.add_message` ganha `ts: float | None = None` ([agent/memory.py:436-445](agent/memory.py#L436-L445)) e o encaminha ao `message_repo.add` ([:464-473](agent/memory.py#L464-L473)). Nada mais no método muda (o `touch_activity` continua sem `ts` — D3).
2. [sequencial] **M3** — incluir `"ts": event.ts` no item da fila do batch ([app/services/message_ingest_service.py:581-590](app/services/message_ingest_service.py#L581-L590)).
3. [sequencial] **M4** — no orquestrador do batch, coletar `text_ts_last` no loop de itens ([app/services/messaging_service.py:1054-1066](app/services/messaging_service.py#L1054-L1066)) e passar `ts=(text_ts_last or None)` na save da linha combinada ([:1106](app/services/messaging_service.py#L1106)). Usar o `ts` do **último** item de texto (coerente com `last_msg_id = text_msg_ids[-1]`).
4. [paralelo] **M5** — `ts=(item.get("ts") or None)` na save de mídia ([:1234-1241](app/services/messaging_service.py#L1234-L1241)).
5. [paralelo] **M6** — `ts=(event.ts or None)` na save grupo-sem-@menção ([app/services/message_ingest_service.py:552-554](app/services/message_ingest_service.py#L552-L554)).
6. [paralelo] **M7** — `ts=(event.ts or None)` na save do echo ([:303](app/services/message_ingest_service.py#L303)).

**Regra de guard:** passar `event.ts or None` (deixar o `message_repo.add` aplicar `time.time()`), OU `event.ts or time.time()`. **Nunca** passar `0.0`.

**Pronto quando:** os 4 sites de inbound repassam o `ts` do provedor; `grep` confirma que nenhum outbound foi tocado; a suíte compila. (O verde final é F2.)

#### Status de execução — Fase F1
**Estado:** ✅ Concluída (2026-08-18)
- **O que foi feito:** M1/M2 — `ContactMemory.add_message` ganhou `ts: float | None = None` e o encaminha ao `message_repo.add` ([agent/memory.py](../agent/memory.py)). M3 — item da fila do batch leva `"ts": event.ts` ([app/services/message_ingest_service.py](../app/services/message_ingest_service.py)). M6 — grupo-sem-@menção passa `ts=(event.ts or None)`. M7 — echo passa `ts=(event.ts or None)`. M4 — batch de texto coleta `text_ts_last` no loop e salva com `ts=(text_ts_last or None)` ([app/services/messaging_service.py](../app/services/messaging_service.py)). M5 — batch de mídia passa `ts=(item.get("ts") or None)`.
- **Como foi feito / decisões:** Guard `event.ts or None` em todos os sites (deixa o `message_repo.add` aplicar `time.time()`; nunca grava `0.0`). Diff total: 3 arquivos, +20/-3 linhas. Nenhum save de OUTBOUND (`handler.py`) nem o `touch_activity` (`conversation_repo.py`) foi tocado — D2/D3 intactas (confirmado por `git diff --stat`).
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `grep` confirma os 5 `ts=` só nos sites de inbound + o forward; M3 é chave de dict `"ts": event.ts`. Suíte compila. Verde final na F2.

---

### Fase F2 — Regressão verde (🔴 depende de F1)

**Objetivo:** provar que o bug sumiu sem regressão.

**Itens:**
1. [sequencial] O teste de reprodução de F0 passa a **verde** (inbound atrasado ordena antes do outbound posterior; reply depois da citada).
2. [sequencial] Rodar a suíte afetada no Postgres: `venv/bin/python -m pytest tests/integration -k "message or ingest or order or thread"` + camada de contratos se tocada.
3. [paralelo] Validação manual (opcional, se houver ambiente): abrir uma conversa, receber uma mensagem, responder citando; simular atraso não é trivial em UI, então o teste automatizado é a rede principal.

**Pronto quando:** reprodução verde, suíte do core verde no Postgres, nenhum teste de outbound/sidebar quebrado.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída (2026-08-18)
- **O que foi feito:** As duas reproduções (F0.2 + F0.2b) passaram a VERDE após a F1; contrato e guard seguem verdes. Rodada a fatia afetada, os caminhos dos outros providers e a **suíte completa do core**. Regenerados 4 goldens de caracterização de `executions` (ver abaixo).
- **Como foi feito / decisões:** Banco isolado `whatsbot_test_plano129`. Além da fatia `-k "message or ingest or order or thread"`, rodei a suíte inteira com `WHATSBOT_PLUGIN_SOURCE_ROOT` apontado para a fonte externa dos plugins (a correção é provider-agnóstica; Cloud/Telegram são outros inbounds).
- **Problemas / pendências:**
  - **Achado não previsto pelo plano (§3.1 não o listava):** o step `webhook_received` da tabela `executions` grava o item da fila de batch **verbatim** ([app/services/message_ingest_service.py](../app/services/message_ingest_service.py) `track_step("webhook_received", {"items": ...})`), então a chave `"ts"` do M3 passou a aparecer no dump — quebrando 4 goldens de caracterização (`exec_ai_turn_no_tools`, `exec_ai_turn_with_tool`, `exec_cost_accumulation`, `exec_gate_off`). **É mudança desejada, não regressão**: o rastro de execução agora mostra o carimbo do provedor. `ts` já está em `_TS_KEYS` do normalizador ([tests/golden.py](../tests/golden.py)) ⇒ é redigido como `<TS>`, então o golden continua determinístico (inclusive com `event.ts = 0.0`). Regenerados com `UPDATE_GOLDENS=1` **escopado só a esse arquivo**; o diff é exatamente uma linha `"ts": "<TS>"` por golden, nada mais.
  - Validação manual em UI não feita (simular atraso de entrega não é trivial na UI; o teste automatizado é a rede principal, como o plano previa).
- **Verificação:**
  - `test_inbound_provider_ts_ordering.py` = **4 passed**.
  - Fatia `tests/integration -k "message or ingest or order or thread"` = **71 passed, 14 skipped** sem source root; **85 passed, 0 skipped** com source root.
  - **Suíte completa do core** (`pytest` na raiz, com source root) = verde exceto **5 falhas pré-existentes**, provadas revertendo os 3 arquivos ao HEAD e re-rodando (falham idênticas sem a mudança): `test_alembic_hygiene` ×2, `test_legacy_script[legacy_endpoints]`, `test_legacy_script[legacy_gowa_plugin_lifecycle]`, `test_audit_matrix_is_complete`.
  - As 4 falhas de `test_execution_characterization` **passam** no HEAD e falhavam com a mudança ⇒ foram investigadas até a causa raiz antes de regenerar (nunca regenerar golden sem explicar a diferença).

---

### Fase F3 — Reparo de linhas históricas (🟢 OPT-IN — só se P3 = fazer)

**Objetivo:** corrigir o(s) registro(s) já fora de ordem (ex.: a linha 671545/msg_id 75198 da conversa 15651).

**Itens (apenas se decidido):**
1. Como o `ts` real não foi persistido, o reparo geral é **heurístico** e restrito. Opção mínima e segura: corrigir **pontualmente** a linha conhecida do incidente, setando seu `ts` para um valor imediatamente ANTERIOR ao da mensagem seguinte na ordem real do Telegram (ex.: `ts` de 75199 − ε), via `UPDATE` manual auditado no VAULT.
2. **NÃO** fazer backfill automático em massa por `id`/`msg_id` — risco de reordenar linhas legítimas (importação/backfill quebram a monotonicidade `id↔ts`).

**Pronto quando:** a conversa 15651 reabre com a resposta 75200 DEPOIS da original 75198 — **ou** P3 = ADIADO e nada é feito.

#### Status de execução — Fase F3
**Estado:** ⏸️ Bloqueada em P3 (decisão do usuário) — **reparo levantado e pronto, NÃO executado**
- **O que foi feito:** Só **leitura** em produção (VAULT, credencial `banco-privado-redes-brasil-geral-cb4e43`, banco `whatsbot`, transação READ ONLY). Nenhuma escrita. Confirmado que o incidente **persiste**: na conversa 15651 a resposta 75200 (`reply_to_msg_id=75198`, `ts` 15:09:55.285) ainda renderiza ACIMA da original 75198 (`id` 671545, `ts` 15:12:01.441).
- **Como foi feito / decisões:** Levantada a janela segura pelos vizinhos na ordem REAL do Telegram (`msg_id` é estritamente crescente por chat):

  | papel | msg_id | id | `ts` atual | BRT |
  |---|---|---|---|---|
  | anterior | 75197 | 671451 | `1787075404.8258054` | 14:50:04.825 |
  | **a corrigir** | **75198** | **671545** | `1787076721.4410775` | **15:12:01.441** ❌ |
  | seguinte | 75199 | 671526 | `1787076566.7177906` | 15:09:26.717 |

  Qualquer valor em `(1787075404.826, 1787076566.718)` restaura a ordem. Escolha do plano (`ts` de 75199 − ε, ε = 1s) ⇒ **`1787076565.7177906`**, bem dentro da janela. A imagem 75204 (`id` 671546, 15:12:01.457) **não** se move: `msg_id` 75204 > 75202, então a posição dela já está certa. É UMA linha; **sem** backfill em massa (D5).
- **Problemas / pendências:** **Aguarda P3.** O `UPDATE` altera dado de produção e ainda exige aprovação humana de escrita no VAULT. Declaração exata, pronta para colar:
  ```sql
  UPDATE messages SET ts = 1787076565.7177906
   WHERE id = 671545 AND conversation_id = 15651 AND msg_id = '75198';
  ```
  (a tripla `id` + `conversation_id` + `msg_id` impede acertar outra linha; esperado `UPDATE 1`).
- **Verificação:** _(ao executar: reabrir a conversa 15651 e conferir que 75198 aparece ANTES de 75199/75200)_

---

## 5 — Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Guard do fallback | Gravar `event.ts = 0.0` (campo ausente) → mensagem em 1970, ordenação destruída | `event.ts or None`/`time.time()` em TODOS os sites; teste dedicado (F0.3) |
| Batch combina N textos em 1 linha | Escolher `ts` errado (primeiro vs último) desalinha do `last_msg_id` | Usar o `ts` do **último** item (coerente com `last_msg_id = text_msg_ids[-1]`) |
| Sidebar | Se o `ts` do provedor vazasse para `touch_activity`, msg atrasada não subiria a conversa | D3: `touch_activity` fica em `now()`; não repassar `event.ts` a ele |
| Ao vivo × reload | Append otimista (chegada) pode divergir do reload (cronológico) numa msg atrasada | Comportamento aceito e correto — o reload é autoritativo; reconciliação otimista é por `msg_id`, não por `ts` |
| `ts` no futuro | Provedor com relógio adiantado poria a msg "no futuro" | Baixíssimo (timestamps são server-side do provedor). Ver P1 (clamp opcional) — **não** implementar sem evidência |
| Granularidade em segundos | Telegram/Cloud dão `ts` em segundos → empates de `ts` | Desempate por `id` já existe no `ORDER BY ts, id` |
| Postgres único backend | Nenhuma mudança de dialeto/DDL | Sem migration (D4); rodar suíte no Postgres de teste |
| Provider novo | Se não setar `event.ts`, cai em `time.time()` (comportamento atual) | `InboundEvent.ts` default `0.0` → fallback seguro; documentar no contrato do `/new-channel` |

---

## 6 — Perguntas em aberto

**P1 — Clampar `ts` de provedor "no futuro"?**
⏸️ ADIADO. Contexto: um provedor com relógio adiantado poderia gravar `ts > now()`. (a) ignorar (timestamps são server-side, risco marginal); (b) clampar a `min(event.ts, now())`. **Recomendação:** (a) — não adicionar clamp sem evidência real; se surgir, é um `min()` de uma linha.

**P2 — Alinhar também o `"ts"` dos payloads de bus (`message.saved`/`message.received`)?**
⏸️ ADIADO. Hoje esses emits usam `time.time()` ([message_ingest_service.py:569](app/services/message_ingest_service.py#L569), [messaging_service.py:1260](app/services/messaging_service.py#L1260)). São metadado do EVENTO (quando o bus disparou), não da linha. (a) deixar como está; (b) trocar para o `ts` persistido, por consistência. **Recomendação:** (a) — fora do escopo do bug; mudar arriscaria semântica de plugins que assinam o bus. Reavaliar só se algum plugin depender disso.

**P3 — Reparar as linhas históricas fora de ordem?**
⏸️ ADIADO (decisão do usuário). Só há **1** caso em 30 dias (a conversa 15651). (a) não reparar (a correção evita novos casos; o histórico fica como está); (b) reparo pontual manual da linha conhecida (F3). **Recomendação:** decidir com o usuário; se sim, F3 pontual e auditada, **sem** backfill em massa.

---

## 7 — Checklist de verificação

- [x] `message_repo.add` persiste o `ts` recebido; sem `ts`, cai em `time.time()` (F0.1).
- [x] Inbound com `event.ts` anterior a um outbound posterior ordena ANTES dele em `get_by_conversation` (F0.2 → F2).
- [x] Reply (`reply_to_msg_id`) renderiza DEPOIS da mensagem citada após reload (F0.2 → F2).
- [x] Inbound com `event.ts = 0.0` cai em `time.time()` (nunca grava epoch 0) (F0.3).
- [x] `grep` confirma: nenhum save de OUTBOUND passou a receber `event.ts` (D2 intacta) — `agent/handler.py` fora do diff.
- [x] `touch_activity` continua em `now()`; conversa com msg atrasada ainda sobe na sidebar (D3) — `db/repositories/conversation_repo.py` fora do diff.
- [x] Suíte do core **verde no Postgres** (`WHATSBOT_TEST_DB_URL`): fatia afetada + suíte completa, sobrando só as 5 falhas pré-existentes provadas contra o HEAD.
- [x] Sem migration nova (D4) — `db/tables.py` e `db/alembic/versions/` inalterados.
- [ ] Reload / back-forward da thread mantém ordem correta (validação manual — **não feita**, ver F2).
- [ ] (Se P3 = fazer) conversa 15651 reabre com 75200 depois de 75198 (F3) — **P3 ainda ADIADO**.

---

## 8 — Apêndice: arquivos-chave

**Backend (core) — a tocar:**
- [agent/memory.py:436-473](agent/memory.py#L436-L473) — `ContactMemory.add_message` ganha `ts` (M1/M2).
- [app/services/message_ingest_service.py:303](app/services/message_ingest_service.py#L303), [:552-554](app/services/message_ingest_service.py#L552-L554), [:581-590](app/services/message_ingest_service.py#L581-L590) — echo, grupo-sem-@menção, item da fila (M3/M6/M7).
- [app/services/messaging_service.py:1054-1066](app/services/messaging_service.py#L1054-L1066), [:1106](app/services/messaging_service.py#L1106), [:1234-1241](app/services/messaging_service.py#L1234-L1241) — batch de texto e mídia (M4/M5).

**Backend (core) — NÃO tocar (só referência):**
- [db/repositories/message_repo.py:26,40,179-209](db/repositories/message_repo.py#L26) — `add` já aceita `ts`; ordenação `(ts, id)`.
- [db/repositories/conversation_repo.py:906-909](db/repositories/conversation_repo.py#L906-L909) — `touch_activity` (D3).
- [agent/handler.py:381-414](agent/handler.py#L381-L414) — outbound saves (D2).
- [app/services/realtime_broadcast.py:61](app/services/realtime_broadcast.py#L61) — `build_inbound_saved_message` (reconciliação por `msg_id`).
- [channels/events.py:29](channels/events.py#L29) — `InboundEvent.ts` (contrato).

**Providers (só referência — já preenchem `event.ts`):**
- [gowa/inbound.py:502](gowa/inbound.py#L502), [storages/plugins/telegram/channels.py:501](storages/plugins/telegram/channels.py#L501), [storages/plugins/whatsapp_cloud/channels.py:1462](storages/plugins/whatsapp_cloud/channels.py#L1462).

**Testes:**
- `tests/integration/` — novos testes de ordenação/save de inbound (F0/F2).
