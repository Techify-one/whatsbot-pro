# Plano 53 — Nota privada sem duplicata visual: dedup por identidade (`_id`/`msg_id`) + autor/hora do servidor nos cards ao vivo

> **Status:** IMPLEMENTADO (F0–F5 ✅ · F6: suítes ✅, validação manual no navegador pendente) · **Data:** 2026-07-16 · **Escopo:** médio
> **Origem:** bug reportado pelo usuário (screenshot: nota "teste" duplicada — card 15:58 sem autor + card 16:03 "por Admin"; após F5 sobra só um). **Método:** investigação nesta conversa — leitura direta com `arquivo:linha` verificado + consulta SQL ao banco vivo (confirmado: **1 única row** no Postgres; a duplicata é 100% visual).
> A bolha otimista da nota privada usa `ts` do **relógio do navegador** e a cópia do broadcast WS usa `ts` do **servidor**; o dedup atual é só heurístico (mesmo conteúdo dentro de 30s — `DEDUP_WINDOW_S`). Com o relógio do cliente defasado >30s (caso real: ~5 min), as duas cópias não colapsam. As identidades estáveis que **já existem dos dois lados** (`_id` da row; `msg_id` sintético `pn:<uuid>`) não são usadas no dedup. Sintoma-irmão: payloads de broadcast incompletos fazem cards ao vivo aparecerem **sem autor** ("por Retorno Automático" só após reload) e com **hora errada**.
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | Correção **robusta a relógio** — dedup passa a usar identidade estável (`_id`/`msg_id`); a janela de 30s vira **fallback** para payloads legados ✅ (2026-07-16) | F1/F2 mudam o predicado puro + o handler WS. Nunca aumentar `DEDUP_WINDOW_S` (não resolve e afrouxa merges legítimos). |
| D2 | **Zero mudança de schema/DB** — tudo que o fix precisa já existe (`messages.id`, `msg_id pn:`, `sent_by_name`) ✅ (2026-07-16) | Sem migration. Só frontend + payloads de rota + payloads de plugin. |
| D3 | O core **não pode depender** de update de plugin: a duplicata se resolve inteira no core; plugin update (zip) só corrige o **autor ao vivo** dos cards de automação ✅ (2026-07-16) | F5 (plugins) é paralela e não bloqueia nada. Distribuição segue o fluxo zip do repo `whatsbot-pro-plugins`. |
| D4 | Comportamento pós-fix: o card único adota **ts/autor do servidor** (a hora exibida corrige-se ao confirmar o POST) ✅ (2026-07-16) | F3 adota `ts`/`sent_by_name`/`msg_id`/`_id` do response na bolha otimista. |

---

## 1. Resumo executivo

Enviar uma nota privada gera **duas cópias legítimas** no painel: a bolha otimista (append local no composer) e a cópia do broadcast WS `new_message`. Elas devem colapsar em uma via `sameMessage` ([messages.js:41-47](../web/static/js/services/messages.js#L41)) — mas o predicado só casa por `ts` idêntico ou conteúdo igual dentro de 30s ([messages.js:24](../web/static/js/services/messages.js#L24)), e a bolha usa `Date.now()/1000` do cliente ([useComposer.js:167](../web/static/js/components/contacts/hooks/useComposer.js#L167)) enquanto o WS carrega o `ts` do servidor. Relógio do cliente defasado (caso real: ~5 min) ⇒ duplicata. A solução tem 3 pernas: (1) **identidade primeiro** — `sameMessage`/handler WS reconhecem `_id` (sempre presente no payload WS e no response do POST) e `msg_id` (`pn:<uuid>` — [memory.py:411-412](../agent/memory.py#L411)) antes da heurística; (2) **fechar a corrida POST×WS** no composer com o mesmo padrão `serverCopyArrived` que o envio normal já usa ([useComposer.js:228-238](../web/static/js/components/contacts/hooks/useComposer.js#L228)), adotando `ts`/`sent_by_name` do servidor; (3) **completar os payloads** dos emit sites (core: `msg_id` em 4 sites + `sent_by_name` no `/private-audio`; plugins `retorno_automatico`/`agendamento_retorno`: `sent_by_name` + `msg_id`) — o que também some com o sintoma "autor só aparece após F5".

---

## 2. Como funciona hoje (mapa)

| Peça | Onde | Comportamento atual |
|------|------|---------------------|
| Bolha otimista (nota texto) | [useComposer.js:166-176](../web/static/js/components/contacts/hooks/useComposer.js#L166) | `{role:'private_note', content, ts: Date.now()/1000, _localId, _status:'sending'}` — **sem `sent_by_name`** (o envio normal põe, [:212-213](../web/static/js/components/contacts/hooks/useComposer.js#L212)), sem ids. |
| Response do POST → bolha | [useComposer.js:188-191](../web/static/js/components/contacts/hooks/useComposer.js#L188) | Só seta `_status` e `_id`. **Não** adota `ts`/`sent_by_name`/`msg_id` do servidor, **não** checa se a cópia WS já chegou (o envio normal checa — `serverCopyArrived` [:228-238](../web/static/js/components/contacts/hooks/useComposer.js#L228)). |
| Bolhas otimistas (mídia privada) | [useMediaUpload.js:149-186](../web/static/js/components/contacts/hooks/useMediaUpload.js#L149) | Imagem/documento/áudio privados: mesmo `ts` do cliente ([:185](../web/static/js/components/contacts/hooks/useMediaUpload.js#L185)), sem `sent_by_name`. Só o áudio merge-ia `_id` do response ([:194-198](../web/static/js/components/contacts/hooks/useMediaUpload.js#L194)); imagem/documento nem isso ([:200-202](../web/static/js/components/contacts/hooks/useMediaUpload.js#L200)). |
| Dedup puro | [messages.js:41-47](../web/static/js/services/messages.js#L41) `sameMessage`, [:88-91](../web/static/js/services/messages.js#L88) `optimisticDupIndex`, [:56-72](../web/static/js/services/messages.js#L56) `isDuplicateMessage`/`findDuplicateIndex` | Só `role` + (`ts` igual OU conteúdo igual com Δts < `DEDUP_WINDOW_S=30`). **Ignora `_id` e `msg_id`** (o `!m.msg_id` do `optimisticDupIndex` é guarda, não identidade). |
| Handler WS `new_message` | [useConversationWsEvents.js:507-519](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L507) reconcile por `msg_id`; [:522-535](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L522) `optimisticDupIndex`; [:536-540](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L536) append | Reconcilia por `msg_id` GOWA (payload da nota **não traz** `msg_id` → nunca roda) e cai na heurística de 30s. **Não reconcilia por `_id`** (presente no payload). Buffer pré-load usa a mesma heurística ([:497-499](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L497)); merge pós-fetch usa `isDuplicateMessage` ([useConversationSelection.js:203-211](../web/static/js/components/contacts/hooks/useConversationSelection.js#L203), [:253-258](../web/static/js/components/contacts/hooks/useConversationSelection.js#L253)). |
| ⚠️ `msg_id` sintético existe na row | [memory.py:364](../agent/memory.py#L364) `_notify_private_enabled`, [:411-412](../agent/memory.py#L411) | Com `notify_private_messages` ligado (caso desta instalação — confirmado no banco: `msg_id='pn:af7645c2…'`), toda nota ganha `msg_id="pn:"+uuid`. `message_repo.add` **retorna** `id` e `msg_id` ([message_repo.py:52-64](../db/repositories/message_repo.py#L52)) — as rotas têm o dado em mãos e não o emitem. |
| Emit `/private-message` | [contacts.py:1237-1250](../server/routes/contacts.py#L1237) | `note_msg = {role, content, ts, status, conversation_id}` + `sent_by_name` (1244-1245) + `_id` (1246-1247). **Sem `msg_id`.** Response `_ok(note_msg)` = mesmo objeto (1258). |
| Emit nota da IA privada | [contacts.py:1046-1066](../server/routes/contacts.py#L1046) | `_id` ok (1062-1063). **Sem `msg_id`.** (Sem autor por design — a row também não tem.) |
| Emit `/private-audio` | [contacts.py:1338-1370](../server/routes/contacts.py#L1338) | Usa `message_repo.get_last` **racy** (1345 — o próprio `/private-message` comenta por que evitar). `note_msg` **sem `sent_by_name`** (1359-1366 — a row TEM, 1343-1344) e **sem `msg_id`**. |
| Emit mídia privada (imagem/doc) | [contacts.py:1434-1453](../server/routes/contacts.py#L1434) | Mesmo `get_last` racy (1440). `sent_by_name` ok (1448-1449), `_id` ok (1450-1451). **Sem `msg_id`.** |
| Emit plugin `retorno_automatico` | `storages/plugins/retorno_automatico/logic.py:276-291` | Salva com `sent_by_name=NOTE_AUTHOR` (276) mas o payload WS (278-289) **omite `sent_by_name` e `msg_id`** → seus screenshots: "Tentativa 2/3" e "3/3" ao vivo sem "por Retorno Automático"; após F5 aparecem (a leitura do banco expõe tudo — [message_repo.py:487-530](../db/repositories/message_repo.py#L487) `_row_to_dict` devolve `_id`, `msg_id`, `sent_by_name`). |
| Emit plugin `agendamento_retorno` | `storages/plugins/agendamento_retorno/logic.py:294-309` | Idem: salva com `sent_by_name=atendente` (296) e o payload (298-309) omite `sent_by_name`/`msg_id`. |
| Render do autor | [SystemMessageCard.js:51](../web/static/js/components/contacts/SystemMessageCard.js#L51) | `· por ${m.sent_by_name}` quando presente. Nada a mudar no render. |

**Confirmação em banco (2026-07-16):** SQL no Postgres vivo mostrou **1 única row** `role='private_note', content='teste'` (id 1425, ts 18:52:49 UTC = 15:52 BRT, `sent_by_name='Thiago'`, `msg_id='pn:…'`). O card "15:48" era a bolha otimista com o relógio do cliente ~4-5 min atrasado.

---

## 3. Inventário / análise

| # | Item | Ponto de mudança (`arquivo:linha`) | O que falta | Abordagem | Risco | Esforço |
|---|------|-----------------------------------|-------------|-----------|-------|---------|
| I1 | Identidade no predicado puro | [messages.js:41-47](../web/static/js/services/messages.js#L41) | `sameMessage` ignora `_id`/`msg_id` | Curto-circuito: ambos com `_id` → `a._id === b._id` decide (true E false); ambos com `msg_id` → idem; senão heurística atual (fallback legado) | Médio | S |
| I2 | Reconcile por `_id` no handler WS | [useConversationWsEvents.js:507-519](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L507) | Só reconcilia por `msg_id` GOWA | Novo passo após o de `msg_id`, antes do `optimisticDupIndex`: match por `m._id === message._id` → merge in place (adota `content`/`status`/`ts`/`sent_by_name`, limpa `_status`) | Médio | S |
| I3 | Corrida POST×WS + adoção do servidor (nota texto) | [useComposer.js:169-176](../web/static/js/components/contacts/hooks/useComposer.js#L169), [:188-191](../web/static/js/components/contacts/hooks/useComposer.js#L188) | Bolha sem `sent_by_name`; response não adota `ts`/autor nem checa cópia já chegada | (a) bolha += `sent_by_name` do `currentUser` (espelhar [:212-213](../web/static/js/components/contacts/hooks/useComposer.js#L212)); (b) no response, padrão `serverCopyArrived` por `_id` (espelhar [:228-238](../web/static/js/components/contacts/hooks/useComposer.js#L228)): cópia WS já presente → dropar a bolha; senão adotar `{ts, _id, msg_id, sent_by_name}` do `res.data` | Médio | M |
| I4 | Idem para mídia privada | [useMediaUpload.js:149-186](../web/static/js/components/contacts/hooks/useMediaUpload.js#L149), [:194-207](../web/static/js/components/contacts/hooks/useMediaUpload.js#L194) | Bolhas sem `sent_by_name`; imagem/doc não mergeiam nem `_id` | Mesmo tratamento do I3 nos 3 ramos privados (imagem 151-153, doc 159-161, áudio 176-180); **manter** `media_path` local (`_isLocalBlob`) no merge — ver P1 | Médio | M |
| I5 | `msg_id` nos payloads core | [contacts.py:1237-1247](../server/routes/contacts.py#L1237) (`/private-message`), [:1046-1063](../server/routes/contacts.py#L1046) (nota IA), [:1359-1368](../server/routes/contacts.py#L1359) (`/private-audio`), [:1443-1451](../server/routes/contacts.py#L1443) (mídia) | Nenhum emite `msg_id` da nota | `note_msg["msg_id"] = saved.get("msg_id")` (nullable — quando `notify_private_messages` off fica `None`/ausente; `_id` continua sendo a identidade universal) | Baixo | S |
| I6 | `sent_by_name` no `/private-audio` + trocar `get_last` racy | [contacts.py:1345](../server/routes/contacts.py#L1345), [:1359-1368](../server/routes/contacts.py#L1359), [:1440](../server/routes/contacts.py#L1440) | Payload sem autor; 2 sites usam `get_last` (racy, já documentado como evitável em [:1236](../server/routes/contacts.py#L1236)) | Usar o retorno de `add_message` (como `/private-message` faz). ⚠️ chave: `add` retorna `"id"`, `get_last` retorna `"_id"` — normalizar no call site | Baixo | S |
| I7 | Autor ao vivo nos plugins | `storages/plugins/retorno_automatico/logic.py:278-289`; `storages/plugins/agendamento_retorno/logic.py:298-309` | Payload omite `sent_by_name` (+ `msg_id`) | Payload += `"sent_by_name"` (NOTE_AUTHOR / atendente) + `"msg_id": saved.get("msg_id")`; bump de versão no `plugin.yaml`; sync dos zips no repo `whatsbot-pro-plugins` | Baixo | S |
| I8 | Testes | [messages.test.js](../web/static/js/services/messages.test.js), [test_endpoints.py:605-622](../tests/test_endpoints.py#L605) | Sem cobertura de identidade / skew / campos novos | Casos novos: `_id` igual → dup mesmo com Δts grande; `_id` diferente → nunca dup; endpoint devolve `msg_id`/`sent_by_name` | Baixo | S |

### Falsos positivos descartados

| Suspeita | Por que NÃO é o caminho |
|----------|-------------------------|
| "Duplicou no banco" | SQL no Postgres vivo: **1 row** por nota. O F5 do usuário confirmou (2º screenshot). Nada a corrigir em `add_message`/rota de save. |
| "Relógio do servidor errado" | Host e `now()` do Postgres batem entre si (UTC correto). O skew é do **cliente** — e não importa: o fix é por identidade, independe de qualquer relógio. |
| Aumentar `DEDUP_WINDOW_S` | Não resolve (skew pode ser de horas) e afrouxa o merge de mensagens reais distintas ("ok"/"ok") — exatamente o bug que o plano 33 F4 corrigiu ([messages.js:74-83](../web/static/js/services/messages.js#L74)). |
| Payload do `protocolos`/`melhorias` sem autor | As rows dessas notas **também não têm** `sent_by_name` (automação sem autor por design; `melhorias` usa role `system`). Payload = row ⇒ consistente. Só ganhariam `msg_id` por uniformidade — adiado (P2). |
| Broadcast do card `transcription` sem `_id` ([contacts.py:1381-1389](../server/routes/contacts.py#L1381)) | Não existe bolha otimista para `transcription` e o `ts` é do servidor — risco de duplicata nulo. Fora do escopo. |
| Dois broadcasts pela mesma nota (rota + listener) | `_notify_private_unread` só emite `conversation_upsert` (sidebar), não `new_message` ([memory.py:372-385](../agent/memory.py#L372)). Não há emissão dupla no core. |

---

## 4. Contrato de identidade (frontend e backend paralelizam contra este)

**Payload WS `new_message` / response do POST de toda nota privada (core e plugins):**

```
message: {
  role: "private_note", content, ts,            ← ts SEMPRE do servidor (row)
  status: null, conversation_id,
  _id: <messages.id>,                            ← identidade universal (sempre presente)
  msg_id: <"pn:…" | ausente>,                    ← presente quando notify_private_messages on
  sent_by_name: <autor | ausente>,               ← presente quando a row tem
  media_type?/media_path?                        ← quando mídia
}
```

**Semântica de dedup (ordem de decisão no frontend):**
1. Ambos os lados com `msg_id` → `msg_id` decide (igual = mesma, diferente = distintas).
2. Ambos com `_id` → `_id` decide.
3. Senão → heurística legada (`ts` igual OU conteúdo igual com Δts < 30s) — fallback para payloads antigos.

---

## 5. Fases / Roadmap

```
WAVE 0  F0 (caracterização: node --test + endpoint verde)          🔴 barreira
WAVE 1  F1(messages.js) ─▶ F2(handler WS) ─▶ F3(composer/mídia)    🔴 sequencial (mesma frente)
            ·  F4 (payloads backend core)                          🟢 paralela a F1–F3
            ·  F5 (plugins retorno_automatico/agendamento_retorno) 🟢 paralela a tudo
WAVE 2  F6 (fecho: testes de integração + validação manual c/ skew) 🔴 barreira final
```

> **Paralelização:** F4 (backend, `server/routes/contacts.py`) e F5 (plugins instalados + zips) tocam arquivos disjuntos da frente frontend F1→F3 — as três frentes rodam em paralelo após F0. Dentro da frente frontend a ordem é obrigatória: o predicado (F1) é usado pelo handler (F2), e o composer (F3) conta com o reconcile por `_id` do F2 para a corrida WS-antes-do-POST.

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|------|------|-----------|-------|-------|----------------|
| 0 | F0 | Caracterização (baseline verde) | 🔴 [bloqueia: tudo] | Baixo | `node --test` de `messages.test.js` verde; `pytest tests/test_endpoints.py` verde |
| 1 | F1 | Identidade em `sameMessage` | 🔴 [bloqueia: F2] | Médio | Testes novos de identidade verdes; os 17 existentes intactos |
| 1 | F2 | Reconcile por `_id` no handler WS | 🔴 [depende: F1; bloqueia: F3] | Médio | Nota com Δts grande colapsa (teste manual/mock) |
| 1 | F3 | Composer/mídia: `serverCopyArrived` + adoção do servidor | 🔴 [depende: F2] | Médio | Enviar nota → 1 card com autor + hora do servidor, mesmo com WS antes do POST |
| 1 | F4 | Payloads backend core (`msg_id`, autor no áudio, fim do `get_last`) | 🟢 [independente] | Baixo | Response/WS da nota carregam `_id`+`msg_id`(+`sent_by_name`); endpoint tests verdes |
| 1 | F5 | Plugins: autor+`msg_id` no payload ao vivo | 🟢 [independente] | Baixo | Card do "Retorno Automático" nasce com autor sem F5 do navegador |
| 2 | F6 | Fecho: testes integrados + validação manual com skew | 🔴 [depende: F1–F5] | Baixo | Checklist §9 completo |

**Disciplina (regras do repo):** verde a cada fase; caracterização ANTES (F0); um refactor por commit; nunca avançar com teste vermelho não-explicado.

---

### Fase 0 — Caracterização (baseline) 🔴 [bloqueia: tudo]
**Objetivo:** fixar o comportamento atual antes de mexer no predicado de dedup (fluxo crítico do chat).
**Itens:**
1. `[paralelo]` Rodar `node --test web/static/js/services/messages.test.js` — os 17 casos atuais ([messages.test.js:9-117](../web/static/js/services/messages.test.js#L9)) verdes; eles são a rede de segurança do F1 (em especial `two distinct inbound "ok" → APPEND` e `operator echo collapses`).
2. `[paralelo]` Rodar `venv/bin/python tests/test_endpoints.py` (Postgres de teste, `WHATSBOT_TEST_DB_URL`) — os checks de `/private-message` ([test_endpoints.py:605-622](../tests/test_endpoints.py#L605)) verdes; anotar o shape atual do response (sem `msg_id`) que o F4 vai mudar de forma consciente.

**Pronto quando:** as duas suítes verdes, sem mudança de código.

#### Status de execução — Fase 0
**Estado:** ✅ Concluída (2026-07-16)
- **O que foi feito:** Baseline rodado sem mudança de código: `node --test web/static/js/services/messages.test.js` e `venv/bin/python tests/test_endpoints.py` (Postgres `whatsbot_test`).
- **Como foi feito / decisões:** Suíte de endpoint salva em log no scratchpad para comparação pós-mudança.
- **Problemas / pendências:** 8 FAILs **pré-existentes** na suíte de endpoints, todos de busca acento/case-insensível (`q busca joão/joao/...`, `pf opção`, `cattr`) — causa confirmada: extensão `unaccent` ausente no banco de teste (`pg_extension` só tem `plpgsql`). Sem relação com este plano; baseline documentado para comparação.
- **Verificação:** `node --test` 17/17 verde; endpoints **1265 passed / 8 failed (todos unaccent, pré-existentes)**.

---

### Fase 1 — Identidade em `sameMessage` 🔴 [bloqueia: F2]
**Objetivo:** identidade estável decide o dedup ANTES da heurística de conteúdo/janela — nos dois sentidos (match e mismatch).
**Itens:**
1. `[sequencial]` [messages.js:41-47](../web/static/js/services/messages.js#L41): curto-circuito no topo de `sameMessage` (após o guard de `role`): ambos com `msg_id` → retorna `a.msg_id === b.msg_id`; senão ambos com `_id` → retorna `a._id === b._id`; senão heurística atual byte-idêntica. Atualizar o doc-comment (§4 deste plano é a referência).
2. `[sequencial]` [messages.js:88-91](../web/static/js/services/messages.js#L88): revisar `optimisticDupIndex` — o guard `!m.msg_id` continua (a bolha otimista não tem `msg_id`); nenhuma mudança esperada além da que herda de `sameMessage`. Confirmar que o caso "two distinct ok/ok" segue APPEND (agora garantido também pelo mismatch de `msg_id`).
3. `[paralelo]` [messages.test.js](../web/static/js/services/messages.test.js): casos novos — (a) `_id` igual + Δts de horas + conteúdo diferente → **dup**; (b) `_id` diferente + mesmo conteúdo + Δts 1s → **não dup**; (c) `msg_id` igual → dup; (d) um lado sem ids → cai na heurística (comportamento antigo); (e) `optimisticDupIndex` com lista contendo cópia WS `_id`-only.

**Pronto quando:** `node --test` verde (17 antigos + novos).

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-07-16)
- **O que foi feito:** `sameMessage` ([messages.js](../web/static/js/services/messages.js)) ganhou o curto-circuito de identidade após o guard de `role`: ambos com `msg_id` → decide; senão ambos com `_id` → decide; senão heurística legada byte-idêntica. Typedef `ChatMessage` documenta `_id`. 7 testes novos em [messages.test.js](../web/static/js/services/messages.test.js) (24 total).
- **Como foi feito / decisões:** `msg_id` checado antes de `_id` (id externo estável vence o interno). Desvio consciente: a asserção "regression guard" do teste do plano 33 F4 (`findDuplicateIndex` das duas inbound "ok" retornava 0 documentando a falha antiga) foi atualizada para `-1` — identidade agora é autoritativa em TODOS os predicados, o que é estritamente melhor (o guard `!m.msg_id` do `optimisticDupIndex` segue intacto).
- **Problemas / pendências:** nenhum. Únicos consumidores externos dos predicados: `useConversationSelection.js` (`isDuplicateMessage`, linhas 207/257) — beneficiados sem mudança.
- **Verificação:** `node --test` **24/24 verde** (17 antigos + 7 novos: `_id` igual com Δts de horas → dup; `_id` diferente 1s → não-dup; `msg_id` vence `_id`; identidade não cruza roles; fallback legado com um lado sem ids; fold pós-POST por `_id`; buffer vs row carregada).

---

### Fase 2 — Reconcile por `_id` no handler WS 🔴 [depende: F1; bloqueia: F3]
**Objetivo:** uma cópia WS que corresponde a uma linha já presente (por `_id`) faz merge in place — nunca append.
**Itens:**
1. `[sequencial]` [useConversationWsEvents.js:507-519](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L507): após o reconcile por `msg_id`, adicionar o passo por `_id`: `message._id` presente e `findIndex(m => m._id === message._id)` → atualizar in place adotando do servidor `content`, `status`, `ts`, `sent_by_name` (quando presentes) e limpando `_status`. **Não** adotar `media_path` (preserva blob local — P1).
2. `[sequencial]` Conferir que o caminho do buffer ([:497-499](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L497)) e os merges pós-fetch ([useConversationSelection.js:203-211](../web/static/js/components/contacts/hooks/useConversationSelection.js#L203), [:253-258](../web/static/js/components/contacts/hooks/useConversationSelection.js#L253)) já ficam corretos só com o F1 (usam `optimisticDupIndex`/`isDuplicateMessage` → `sameMessage`) — sem mudança de código esperada aí.

**Pronto quando:** com relógio do cliente adulterado (>30s), enviar nota privada → a cópia WS colapsa na bolha (1 card). Verificável via mock/manual (F6 formaliza).

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-07-16)
- **O que foi feito:** Novo passo de reconcile por `_id` em [useConversationWsEvents.js](../web/static/js/components/contacts/hooks/useConversationWsEvents.js), após o de `msg_id` e antes do `optimisticDupIndex`: match por `m._id === message._id` → merge in place adotando `content`/`status`/`ts`/`msg_id`/`sent_by_name` do servidor e limpando `_status`. `media_path` deliberadamente NÃO adotado (P1 — preserva blob local).
- **Como foi feito / decisões:** Além do plano, o merge do ramo `optimisticDupIndex` (bolha ainda sem ids, dedup pela heurística) também passou a adotar `ts`/`sent_by_name` do servidor — coerente com a D4 (o card único espelha a row); antes esse ramo mantinha o ts do relógio do cliente e ficava sem autor até o reload mesmo com relógio certo.
- **Problemas / pendências:** nenhum. Buffer pré-load e merges pós-fetch confirmados corretos só com o F1 (usam `optimisticDupIndex`/`isDuplicateMessage` → `sameMessage`), sem mudança.
- **Verificação:** `node --test` 24/24 verde (o handler é hook — validação funcional consolidada na F6 manual).

---

### Fase 3 — Composer/mídia: fechar a corrida POST×WS + adotar o servidor 🔴 [depende: F2]
**Objetivo:** a bolha otimista nasce com autor, e ao confirmar o POST vira o espelho da row do servidor (ou é dropada se a cópia WS chegou primeiro).
**Itens:**
1. `[paralelo]` [useComposer.js:169-176](../web/static/js/components/contacts/hooks/useComposer.js#L169): bolha da nota += `sent_by_name: (currentUser && currentUser.name) || undefined` (espelho exato do envio normal [:212-213](../web/static/js/components/contacts/hooks/useComposer.js#L212)).
2. `[sequencial]` [useComposer.js:188-191](../web/static/js/components/contacts/hooks/useComposer.js#L188): trocar o `updateMsgByLocalId` simples pelo padrão `serverCopyArrived` do envio normal ([:228-238](../web/static/js/components/contacts/hooks/useComposer.js#L228)), com identidade `_id`: se já existe `m._id === res.data._id` com `_localId` diferente → **remover** a bolha otimista; senão merge adotando `{_status: null, _id, msg_id, ts, sent_by_name}` de `res.data`.
3. `[paralelo]` [useMediaUpload.js:151-153](../web/static/js/components/contacts/hooks/useMediaUpload.js#L151), [:159-161](../web/static/js/components/contacts/hooks/useMediaUpload.js#L159), [:176-180](../web/static/js/components/contacts/hooks/useMediaUpload.js#L176): os 3 ramos privados ganham `sent_by_name` na bolha; e o pós-response ([:194-207](../web/static/js/components/contacts/hooks/useMediaUpload.js#L194)) aplica o mesmo padrão do item 2 aos 3 (hoje imagem/doc privados nem mergeiam `_id`). Manter `media_path` local no merge (P1).
4. `[sequencial]` Sanidade de render: mensagens renderizam em ordem de array (não re-ordenam por `ts`) — adotar o `ts` do servidor só corrige o rótulo de hora ([SystemMessageCard.js](../web/static/js/components/contacts/SystemMessageCard.js) usa `fmt(m.ts)`), sem reordenação. Confirmar visualmente.

**Pronto quando:** enviar nota texto/imagem/doc/áudio privados → sempre **1 card**, com "· por <nome>" imediato e hora igual à pós-F5, nas duas ordens de chegada (WS antes/depois do POST).

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-07-16)
- **O que foi feito:** (a) [useComposer.js](../web/static/js/components/contacts/hooks/useComposer.js): bolha da nota nasce com `sent_by_name` do `currentUser`; pós-response substituído pelo padrão `serverCopyArrived` por `_id` — cópia WS já presente → dropa a bolha; senão adota `{_status:null, _id, msg_id, ts, sent_by_name}` de `res.data`. (b) [useMediaUpload.js](../web/static/js/components/contacts/hooks/useMediaUpload.js): objeto compartilhado `privateNote` (role+status+autor) nos 3 ramos privados (imagem/documento/áudio); pós-response unificado num ramo `isPrivateNote` com o mesmo padrão (antes imagem/doc privados nem mergeavam `_id`). `media_path` local (blob) preservado no merge — P1.
- **Como foi feito / decisões:** `res.ok` falso agora também marca a bolha `_status:'failed'` no caminho da nota de texto (antes um `ok:false` deixava a bolha "sending" para sempre — melhoria colateral do mesmo bloco). No cenário serverCopyArrived de mídia, a cópia WS que fica mostra o `media_path` do servidor (servível) — aceitável, só ocorre com relógio defasado.
- **Problemas / pendências:** nenhum.
- **Verificação:** `node --check` nos 4 arquivos JS tocados OK; `node --test` 24/24; auditoria de `_isLocalBlob` (só render hint de `media_path` — consistente). Validação funcional na F6.

---

### Fase 4 — Payloads backend core 🟢 [independente das F1–F3]
**Objetivo:** todo emit de nota privada do core espelha a row (contrato §4): `_id` + `msg_id` + `sent_by_name` + `ts` do servidor.
**Itens:**
1. `[paralelo]` [contacts.py:1237-1247](../server/routes/contacts.py#L1237) (`/private-message`): `note_msg["msg_id"] = saved.get("msg_id")` quando presente (o `saved` do `add_message` já traz — [message_repo.py:52-64](../db/repositories/message_repo.py#L52)). O response `_ok(note_msg)` (1258) herda de graça.
2. `[paralelo]` [contacts.py:1046-1063](../server/routes/contacts.py#L1046) (nota da IA privada): idem — `msg_id` do `saved_note`.
3. `[paralelo]` [contacts.py:1338-1370](../server/routes/contacts.py#L1338) (`/private-audio`): (a) capturar o retorno de `add_message` em vez de `message_repo.get_last` (1345) — ⚠️ `add` retorna a chave `"id"` e `get_last` retorna `"_id"`; ajustar 1367-1368 e o `_record_private_mentions` (ele já aceita ambos — [contacts.py:1156](../server/routes/contacts.py#L1156)); (b) `note_msg` += `sent_by_name` (de `_u`, como em [:1448-1449](../server/routes/contacts.py#L1448)) e `msg_id`.
4. `[paralelo]` [contacts.py:1434-1453](../server/routes/contacts.py#L1434) (`_save_private_media`): mesma troca `get_last`→retorno de `add_message` (1440) + `msg_id` no `note_msg`.
5. `[sequencial]` [tests/test_endpoints.py:605-622](../tests/test_endpoints.py#L605): estender os checks — response de `/private-message` com `sent_by_name` e (com `notify_private_messages=true` setado no teste) `msg_id` prefixado `pn:`; com a config off, `_id` presente e `msg_id` ausente/None.

**Pronto quando:** `pytest tests/test_endpoints.py` verde com os checks novos; `curl` manual do POST mostra o contrato §4.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-07-16)
- **O que foi feito:** [server/routes/contacts.py](../server/routes/contacts.py) — os 4 emit sites de nota privada agora cumprem o contrato §4: `/private-message` e nota da IA privada ganharam `msg_id` no `note_msg`; `/private-audio` e `_save_private_media` trocaram o `message_repo.get_last` racy pelo retorno do `add_message` e ganharam `msg_id` (áudio também ganhou `sent_by_name` e `conversation_id`, que faltavam). Testes: +5 checks em [tests/test_endpoints.py](../tests/test_endpoints.py) (contrato com `notify_private_messages` off/on).
- **Como foi feito / decisões:** Normalização `id` vs `_id` feita nos call sites (o retorno de `add` usa `"id"`); `_record_private_mentions` já aceitava ambos. Além do plano: `conversation_id` adicionado aos payloads de áudio/mídia privados (routing exato por thread no painel, como `/private-message` já fazia). No teste com notify ON, o unread gerado é limpo via `POST /{phone}/read` para não vazar estado aos testes seguintes.
- **Problemas / pendências:** nenhum.
- **Verificação:** suíte de endpoints **1270 passed / 8 failed** (mesmos 8 unaccent pré-existentes do baseline; +5 = exatamente os checks novos). `tests/endpoints/test_p25_unread_badge_and_ingest.py` 6 passed; `tests/test_tool_call_broadcast.py` exit 0.

---

### Fase 5 — Plugins: autor + `msg_id` no payload ao vivo 🟢 [independente]
**Objetivo:** cards de automação nascem com "· por <autor>" sem F5 (sintoma dos screenshots: "Tentativa 2/3"/"3/3" sem autor até recarregar).
**Itens:**
1. `[paralelo]` `storages/plugins/retorno_automatico/logic.py:278-289`: payload += `"sent_by_name": NOTE_AUTHOR` e `"msg_id": saved.get("msg_id")` (a row já é salva com autor na 276).
2. `[paralelo]` `storages/plugins/agendamento_retorno/logic.py:298-309`: payload += `"sent_by_name": atendente` e `"msg_id": saved.get("msg_id")` (row salva com autor na 294-297).
3. `[sequencial]` Bump `version` no `plugin.yaml` de cada um + restart do servidor (toggle/restart de plugin — regra do repo) + **sync dos zips** no repo `Techify-one/whatsbot-pro-plugins` (`plugins/<id>/<id>.zip` + `.json`), conforme o fluxo de distribuição por zip.

**Pronto quando:** disparar um lembrete do `retorno_automatico` (ou reduzir `silence_minutes` num teste) → o card ao vivo já nasce "· por Retorno Automático"; após F5 fica idêntico.

#### Status de execução — Fase 5
**Estado:** ✅ Concluída (2026-07-16)
- **O que foi feito:** Payloads dos dois plugins agora espelham a row: `retorno_automatico/logic.py` (+`sent_by_name: NOTE_AUTHOR`, +`msg_id`) e `agendamento_retorno/logic.py` (+`sent_by_name: atendente`, +`msg_id`). `plugin.yaml` de ambos: 1.0.0 → 1.0.1. Zips regenerados a partir das cópias instaladas e sincronizados no repo `Techify-one/whatsbot-pro-plugins` (commit `ed93170` na master: zip + `.json` + `catalog.json` com 1.0.1).
- **Como foi feito / decisões:** Antes de regenerar, diff zip-do-repo × instalado confirmou que as ÚNICAS diferenças eram as edições deste plano (sem divergência prévia a preservar). `msg_id` pode ir `null` no payload (notify off) — inofensivo, os checks do frontend são truthy.
- **Problemas / pendências:** `tests/test_retorno_automatico.py` só roda via `pytest` (como script direto falha em `ModuleNotFoundError: plugins` — peculiaridade de sys.path pré-existente, não regressão). `agendamento_retorno` não tem suíte própria.
- **Verificação:** `pytest tests/test_retorno_automatico.py` **37 passed**; AST-check dos dois `logic.py` OK; dev server (whatsbot.service, :8090) recarregou os plugins via hot-reload sem erro no journal (0 tracebacks).

---

### Fase 6 — Fecho: testes integrados + validação manual com skew 🔴 [depende: F1–F5]
**Objetivo:** provar o cenário-raiz de ponta a ponta e fechar o checklist.
**Itens:**
1. `[sequencial]` Suítes completas: `node --test web/static/js/services/` + `venv/bin/python tests/test_endpoints.py` (Postgres de teste) — tudo verde.
2. `[sequencial]` Manual (o cenário do bug): com o relógio da máquina cliente adulterado em ±5 min (ou `Date.now` sobrescrito no console), enviar nota privada texto → **1 card**, autor imediato, hora do servidor. Repetir com áudio/imagem/doc privados. Testar as duas ordens (rede lenta: throttle no devtools faz o WS chegar antes do POST).
3. `[sequencial]` Manual (plugins): card do `retorno_automatico` ao vivo com autor (F5 do navegador não muda nada).
4. `[sequencial]` Regressões: mensagens normais do operador (send + echo), inbound "ok"/"ok" em batches distintos (devem continuar 2 bolhas), reconexão do WS (`reloadOpenThread` — [useConversationSelection.js:237-265](../web/static/js/components/contacts/hooks/useConversationSelection.js#L237)) sem duplicar nem sumir nota.

**Pronto quando:** checklist §9 inteiro marcado.

#### Status de execução — Fase 6
**Estado:** 🟡 Em andamento (suítes ✅ · validação manual no navegador pendente)
- **O que foi feito:** Item 1 completo: `node --test` em TODOS os módulos puros de `web/static/js/services/` (**142/142**) + `channels/constants.test.js` (18/18) + suíte de endpoints final (**1270 passed / 8 failed — os mesmos 8 unaccent pré-existentes do baseline F0**). Dev server (:8090) recarregado e saudável (0 erros no journal).
- **Como foi feito / decisões:** A suíte de endpoints final também valida as edições da F5 (o boot do app descobre e carrega os plugins instalados). Gotcha de invocação: `node --test <dir>/` não resolve neste Node 22 — usar glob `<dir>/*.test.js`.
- **Problemas / pendências:** Itens 2–4 (validação manual) exigem navegador e o relógio da máquina cliente — ficam para o operador: (a) nota texto/mídia com relógio ±5 min → 1 card, autor imediato, hora do servidor; (b) throttle no devtools (WS antes do POST) → 1 card; (c) card do `retorno_automatico` ao vivo já com autor; (d) regressões: envio normal, inbound "ok"/"ok" (coberto por unit test), reconexão WS.
- **Verificação:** JS 142/142 + 18/18; endpoints 1270/8-unaccent (= baseline + 5 checks novos); `pytest tests/test_retorno_automatico.py` 37 passed; `tests/endpoints/test_p25_unread_badge_and_ingest.py` 6 passed.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| `sameMessage` com mismatch autoritativo (`_id` diferentes → não-dup) | Payloads antigos/exóticos com `_id` "sujo" poderiam deixar de colapsar | O curto-circuito só decide quando **ambos** os lados têm o campo; um lado sem `_id`/`msg_id` cai na heurística legada byte-idêntica. Testes F1 cobrem os 4 quadrantes. |
| Corrida WS-antes-do-POST | Reconcile por `_id` no WS não acha a bolha (ainda sem `_id`) → append; sem o F3 ficariam 2 cards com o mesmo `_id` | F3 item 2 (`serverCopyArrived`): o response dropa a bolha quando a cópia já chegou. É o mesmo padrão já batalha-testado do envio normal. |
| Adoção do `ts` do servidor | Card "muda de hora" ao confirmar o POST | Comportamento desejado (D4). Render é por ordem de array — sem reordenação; só o rótulo muda. |
| `media_path` blob local vs servidor | Adotar o path do servidor no merge poderia piscar/quebrar o preview (`_isLocalBlob`) | **Não** adotar `media_path` no merge (F2/F3); manter comportamento atual. Ver P1. |
| Duas cópias de dedup (`optimisticDupIndex` guarda `!m.msg_id`) | Bolha que ganhou `msg_id` do response deixaria de ser match de `optimisticDupIndex` | Correto e intencional: quem cobre esse caso passa a ser o reconcile por `msg_id`/`_id` do handler (F2), que roda **antes**. |
| `notify_private_messages` desligado | Nota sem `msg_id` (None) | `_id` é a identidade universal (sempre no payload/response). Teste F4 item 5 cobre a config off. |
| Plugins instalados ≠ zips do repo | Editar só `storages/plugins/` deixa o zip do repo defasado (próxima instalação regride) | F5 item 3: bump de versão + sync no `whatsbot-pro-plugins` no mesmo PR/commit do plugin. |
| Restart de plugin | Mudança em `logic.py` de plugin não vale a quente em prod (dev tem hot-reload) | Reiniciar o servidor após editar os plugins (F5). |
| Regressão do plano 33 F4 ("ok"/"ok") | Mexer no predicado pode re-fundir inbounds distintos | Caracterização F0 + teste existente [messages.test.js:93](../web/static/js/services/messages.test.js#L93) protegem; mismatch de `msg_id` agora até reforça o append. |

---

## 7. Perguntas em aberto

- **P1 — Adotar `media_path` do servidor no merge da bolha de mídia privada?** ⏸️ ADIADO (default: **não**). Contexto: a bolha usa blob local (`_isLocalBlob`, [useMediaUpload.js:185-186](../web/static/js/components/contacts/hooks/useMediaUpload.js#L185)); o payload WS traz o path real (`statics/outbox/…`). Opções: (a) manter blob até reload (zero flicker, comportamento atual); (b) adotar o path do servidor no merge (estado 100% espelho, mas re-fetch da imagem). **Recomendação:** (a) — o blob morre no reload e o path do banco assume naturalmente.
- **P2 — `msg_id` no payload das notas do `protocolos` (logic.py:2661-2671) e `melhorias`?** ⏸️ ADIADO. Sem sintoma (não têm bolha otimista nem autor na row); fazer por uniformidade na próxima vez que esses plugins forem tocados.
- **P3 — Detectar/avisar relógio do cliente defasado (banner "seu relógio está errado")?** ⏸️ ADIADO. O fix por identidade torna o skew inofensivo para o dedup; um aviso de UX é feature separada (comparar `ts` do `/api/status` com `Date.now()` no boot do app), fora do escopo.

---

## 8. Apêndice — arquivos-chave

**Frontend (frente F1→F3)**
- [web/static/js/services/messages.js:24](../web/static/js/services/messages.js#L24) (`DEDUP_WINDOW_S`), [:41-47](../web/static/js/services/messages.js#L41) (`sameMessage`), [:88-91](../web/static/js/services/messages.js#L88) (`optimisticDupIndex`).
- [web/static/js/services/messages.test.js](../web/static/js/services/messages.test.js) — casos de identidade novos.
- [web/static/js/components/contacts/hooks/useConversationWsEvents.js:507-540](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L507) — reconcile por `_id`.
- [web/static/js/components/contacts/hooks/useComposer.js:166-198](../web/static/js/components/contacts/hooks/useComposer.js#L166) — bolha + `serverCopyArrived` da nota; molde em [:228-238](../web/static/js/components/contacts/hooks/useComposer.js#L228).
- [web/static/js/components/contacts/hooks/useMediaUpload.js:148-207](../web/static/js/components/contacts/hooks/useMediaUpload.js#L148) — 3 ramos privados de mídia.
- (Só leitura/confirmação) [useConversationSelection.js:203-211](../web/static/js/components/contacts/hooks/useConversationSelection.js#L203), [:253-258](../web/static/js/components/contacts/hooks/useConversationSelection.js#L253); [SystemMessageCard.js:51](../web/static/js/components/contacts/SystemMessageCard.js#L51).

**Backend (F4)**
- [server/routes/contacts.py:1237-1258](../server/routes/contacts.py#L1237) (`/private-message`), [:1046-1066](../server/routes/contacts.py#L1046) (nota IA), [:1338-1370](../server/routes/contacts.py#L1338) (`/private-audio`), [:1434-1453](../server/routes/contacts.py#L1434) (`_save_private_media`).
- (Só leitura) [db/repositories/message_repo.py:52-64](../db/repositories/message_repo.py#L52) (`add` retorna `id`/`msg_id`), [:487-530](../db/repositories/message_repo.py#L487) (`_row_to_dict` expõe `_id`/`msg_id`/`sent_by_name`); [agent/memory.py:364](../agent/memory.py#L364), [:411-412](../agent/memory.py#L411) (`pn:` msg_id).

**Plugins (F5 — instalados em `storages/plugins/`, fora do git do core; sync via `whatsbot-pro-plugins`)**
- `storages/plugins/retorno_automatico/logic.py:276-291` + `plugin.yaml` (bump).
- `storages/plugins/agendamento_retorno/logic.py:294-309` + `plugin.yaml` (bump).

**Testes**
- [tests/test_endpoints.py:605-622](../tests/test_endpoints.py#L605) — checks do `/private-message` a estender.

---

## 9. Checklist de verificação

- [x] `node --test web/static/js/services/messages.test.js` verde — 24/24 (17 antigos + 7 de identidade); sweep completo dos services 142/142.
- [x] `venv/bin/python tests/test_endpoints.py` no Postgres de teste: **1270 passed / 8 failed** — os 8 são unaccent pré-existentes (baseline F0, extensão ausente no DB de teste); inclui os 5 checks novos de `msg_id`/`sent_by_name`.
- [ ] **(manual)** Nota privada texto com relógio do cliente ±5 min → **1 card**, "· por <nome>" imediato, hora do servidor (igual pós-F5).
- [ ] **(manual)** Mesmo teste com WS chegando **antes** do response do POST (throttle) → 1 card (bolha dropada pelo `serverCopyArrived`).
- [ ] **(manual)** Áudio/imagem/documento privados → 1 card cada, com autor; preview local não pisca (P1: `media_path` local preservado).
- [ ] **(manual)** Card do `retorno_automatico`/`agendamento_retorno` ao vivo nasce com autor; F5 não muda nada.
- [ ] **(manual)** Regressão: envio normal do operador (texto + mídia) sem duplicar; echo do celular ok.
- [x] Regressão plano 33 F4: dois inbound "ok"/"ok" em batches distintos continuam **2 bolhas** (unit test mantido e reforçado pelo mismatch de `msg_id`).
- [ ] **(manual)** Reconexão WS (`reloadOpenThread`) e reload/back-forward: sem duplicata nem nota sumida.
- [x] `notify_private_messages` OFF: nota sem `msg_id` continua deduplicando por `_id` (unit test F1 + checks de endpoint F4).
- [x] Plugins: versão 1.0.1, hot-reload aplicado no dev server, zips sincronizados no `whatsbot-pro-plugins` (commit `ed93170`).
- [x] Sem segredo em URL; nenhum novo elemento de UI (nada a checar de modo escuro).
