# Plano 42 — Fechar os defeitos do go-live: `source_id` canal-aware (Defeito #1 residual) + validar isolamento de leitura (Defeito #2) + degradação graciosa do `/api/balance` (502)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-09 · **Escopo:** pequeno/médio (3 frentes independentes · 1 correção de dado + migration · 1 validação/limpeza · 1 hardening de endpoint)
> **Origem:** defeitos registrados em [CorrigirIAs.md](CorrigirIAs.md) durante o roteiro de [testaria.md](testaria.md) (go-live dia 20). **Método:** leitura do código real + `grep` exaustivo + 2 sub-agentes `Explore` em paralelo (isolamento de leitura · balance) nesta sessão. Todo `arquivo:linha` abaixo foi **verificado**.
> **O quê/por quê:** (1) a correção estrutural do Defeito #1 (inbox por-canal) já entrou no **plano 38**, mas `ContactMemory._jid()` ([agent/memory.py:166](../agent/memory.py#L166)) ainda hardcoda `@s.whatsapp.net`/`@g.us` para **qualquer** canal → o `source_id` do `contact_inbox` de contatos Telegram/Cloud grava com sufixo WhatsApp. Hoje é **cosmético** (o outbound roteia por `channel_id`, não por `source_id`), mas é bomba-relógio e contraria o "comportamento esperado" do defeito. (2) o Defeito #2 (leitura vazando entre canais) é **consequência** do #1 — não há furo de isolamento independente no backend; vira **validação + limpeza de dado**. (3) o `/api/balance` retorna **502 deliberado** quando o proxy Techify `/credits` está fora e não há cache — deve **degradar graciosamente** (não 502) para o painel não ficar "sem saldo".
>
> **Como usar este plano:** ao executar cada fase, preencha o bloco **"Status de execução"** dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro. **Verde a cada fase.** **Caracterização ANTES** de mexer no `source_id` (fluxo crítico de resolução de conversa). **Um refactor por commit.** As waves marcam o que roda em paralelo (🟢) e o que é sequencial/bloqueante (🔴).

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** ✅ (2026-07-09) | **GOWA fica byte-idêntico.** O `source_id` do GOWA continua `phone@s.whatsapp.net` / `phone@g.us` — casa o backfill 0013 ([20260620_0013_inbox_conversations.py:127](../db/alembic/versions/20260620_0013_inbox_conversations.py#L127)) e TODAS as linhas já existentes do inbox default (id=1). Mexer nisso reescreveria a tabela inteira sem ganho. | A correção do `_jid()` só muda o comportamento para providers **não-GOWA**. Nenhum risco no fluxo WhatsApp de produção. |
| **D2** ✅ (2026-07-09) | Para providers não-GOWA (Telegram/Cloud), o `source_id` passa a ser o **id nativo cru** (o `phone`/`chat_id` já bare que o ingest usa — [message_ingest_service.py:351](../app/services/message_ingest_service.py#L351)), **sem** sufixo WhatsApp. | Corrige o Defeito #1 residual. Exige **migration de re-âncora** dos `contact_inboxes` não-GOWA já gravados com `@s.whatsapp.net` (senão viram linha órfã e o dedup cria uma 2ª). |
| **D3** ✅ (2026-07-09) | **Sem novo `if provider == "gowa"`** onde a arquitetura já resolve por provider (princípio dos planos 32/33/38 D4). A forma do `source_id` é responsabilidade **do provider** (fina no provider, genérica no core). | O `_jid()` vira um builder resolvido pelo provider (hook de classe `source_id_for`, default bare, GOWA override) — ver P1. |
| **D4** ✅ (2026-07-09) | O Defeito #2 **não** ganha correção de código de isolamento — os 2 sub-agentes confirmaram que `visible_inbox_ids` já é aplicado nos 4 pontos de leitura ([authz.py:77](../server/authz.py#L77), rotas em [conversations.py](../server/routes/conversations.py)). Vira **teste de caracterização** (trava contra regressão) + **limpeza opcional** do dado fantasma legado. | Workstream B é validação + cleanup, não refactor de gate. |
| **D5** ✅ (2026-07-09) | O `/api/balance` **não deve retornar 502** por proxy fora. Fail-open de UX: responde **200** com `balance: null` + um flag `available:false` (e razão), o painel degrada (sem modal, sem "sem saldo" alarmante). O 400 de "sem api_key" **fica** (é config, não indisponibilidade). | Muda só o handler `get_balance` ([config.py:214-218](../server/routes/config.py#L214)) + o frontend tolera `available:false`. |
| **D6** ✅ (2026-07-09) | As três frentes são **independentes** e podem ser feitas/commitadas em paralelo por pessoas/sessões diferentes. Só a fase final (preencher `CorrigirIAs.md` + suíte verde) espera as três. | Waves explícitas abaixo. |
| **Princípio fixo** | Nada em produção estável a proteger nesses caminhos (Telegram/Cloud recém-multicanal; balance é best-effort) ⇒ **corrigir de vez**, sem stopgap. | Substituir, não empilhar flag de contorno. |

---

## 1. Resumo executivo

Três defeitos do roteiro de go-live, independentes:

1. **Defeito #1 (residual) — `source_id` com sufixo WhatsApp em canal não-WhatsApp.** `_jid()` reconstrói sempre `phone@s.whatsapp.net`/`@g.us`. O plano 38 já isolou a **conversa** por inbox (então não vaza mais entre canais), mas a **chave de dedup** (`contact_inboxes.source_id`) de um contato Telegram/Cloud ainda nasce com cara de JID WhatsApp. Correção: o `source_id` passa a ser responsabilidade do provider (GOWA mantém o sufixo; demais usam o id cru) + migration que re-âncora as linhas já gravadas.
2. **Defeito #2 — leitura de conversa de outro canal.** Confirmado como **consequência do #1** (o `EXISTS` de visibilidade casava por causa da conversa fantasma real no inbox WhatsApp — [contact_search.py:212](../db/search/contact_search.py#L212)). O backend **já** aplica `visible_inbox_ids` nos 4 pontos de leitura. Correção: teste de caracterização que trava o isolamento + limpeza do dado fantasma legado que sobrou de instalações afetadas.
3. **Balance 502.** `fetch_balance` engole a exceção do proxy e retorna `None` ([balance_monitor.py:69-71](../server/balance_monitor.py#L69)); sem cache, o handler devolve **502 explícito** ([config.py:218](../server/routes/config.py#L218)). Correção: degradar para 200 com `available:false`, e (opcional) primar o cache no boot.

---

## 2. Como funciona hoje (mapa) — `arquivo:linha` verificado

### 2.1 Defeito #1 — a cadeia do `source_id`

```
ingest (Telegram): phone = event.chat_id (id nativo, BARE)     [message_ingest_service.py:351]
  → handler._get_contact(phone, channel_id=telegram_...)        [message_ingest_service.py:408]
    → ContactMemory(channel_id, inbox_id=<telegram>)            [memory.py:91-98]  ✅ inbox por-canal (plano 38)
      → _resolve_conversation(...)                              [memory.py:207]
        → resolve_for_contact_ex(id, self._jid(), inbox_id=..)  [memory.py:208]
             self._jid() = f"{phone}@s.whatsapp.net"  ← SUFIXO WA CEGO   [memory.py:166-169]
          → contact_inbox_repo.get_or_create(source_id=jid)     [conversation_repo.py:289-290]
               dedup key = (inbox_id, source_id)                [contact_inbox_repo.py:14-31]
```

- **Único caller de `_jid()`**: [memory.py:208](../agent/memory.py#L208). Verificado por `grep`.
- **Único consumo de `source_id`/`source_jid`**: como **chave de dedup** em `contact_inbox_repo.get_or_create` ([contact_inbox_repo.py:22,29](../db/repositories/contact_inbox_repo.py#L22)) — a `EXISTS`/uniqueness é `(inbox_id, source_id)`. **Nada** no outbound lê `source_id` (o `OutboundRouter` roteia por `channel_id`+`chat_id` — [outbound.py:80](../channels/outbound.py#L80)). ⇒ hoje o sufixo é **cosmético**, mas é a única razão de a chave "parecer" WhatsApp.
- **GOWA já entrega `phone` bare**: `_phone_from_jid` faz `jid.split("@")[0].split(":")[0]` ([gowa/inbound.py:438-441](../gowa/inbound.py#L438)). Telegram entrega `chat_id` cru. ⇒ o `phone` em `ContactMemory` é **provider-agnóstico e bare** nos dois; só o `_jid()` re-adiciona o sufixo WA.
- **Backfill 0013** também hardcoda `@s.whatsapp.net`/`@g.us`, mas só roda com `conversations` vazio e só popula o inbox default=1 ([20260620_0013_inbox_conversations.py:120-132](../db/alembic/versions/20260620_0013_inbox_conversations.py#L120)) ⇒ **legado GOWA-only**, não afeta Telegram/Cloud.
- **Provider é resolvível por canal**: `channel_repo.get(channel_id)["provider"]` ([channel_repo.py:63](../db/repositories/channel_repo.py#L63)); a inbox conhece o canal (`inboxes.channel_id`, [tables.py:374](../db/tables.py#L374)).

### 2.2 Defeito #2 — o isolamento de leitura (já correto)

| Ponto de leitura | `arquivo:linha` | Scoping aplicado |
|---|---|---|
| Lista de conversas | [conversations.py:95-113](../server/routes/conversations.py#L95) | `inbox_ids=visible_inbox_ids(request)` → SQL `WHERE inbox_id IN (...)` ([conversation_repo.py:386](../db/repositories/conversation_repo.py#L386)) |
| GET conversa única | [conversations.py:192-207](../server/routes/conversations.py#L192) | `_inbox_hidden(request, conv.inbox_id)` → **404** ([conversations.py:47-54](../server/routes/conversations.py#L47)) |
| GET mensagens | [conversations.py:209-264](../server/routes/conversations.py#L209) | revalida `vis`/`inbox_id` ANTES do mark-read → **404** ([conversations.py:230](../server/routes/conversations.py#L230)) |
| Resolução por contato | [conversations.py:712-713,742-743](../server/routes/conversations.py#L712) | mesmo `_inbox_hidden` |
| Sidebar de contatos | [contact_search.py:212-218](../db/search/contact_search.py#L212) | `EXISTS(conversation WHERE inbox_id IN visible)` |

- Fonte da verdade: `visible_inbox_ids` ([authz.py:77-90](../server/authz.py#L77)) — `None`=sem scoping (admin/`conversation.read_all`/legacy); lista=só esses inboxes; membership em [inbox_member_repo.py:30-37](../db/repositories/inbox_member_repo.py#L30).
- ⚠️ **Assimetria estrutural (não é bug, mas é a única fragilidade):** o scoping mora na **camada de rota** — os repos `get_with_channel`/`get_by_conversation` buscam só por `id`, sem filtro de inbox ([conversation_repo.py:417-426](../db/repositories/conversation_repo.py#L417), [message_repo.py:71-84](../db/repositories/message_repo.py#L71)). A proteção depende de cada handler chamar o guard. Hoje **todos chamam** — o teste de caracterização trava isso contra um handler futuro esquecido.

### 2.3 Balance — o 502 deliberado

```
GET /api/balance                                               [config.py:203]
  → sem openrouter_api_key → 400                               [config.py:211-213]
  → balance = await fetch_balance(api_key)  (fetch AO VIVO, 10s) [config.py:214]
       httpx.AsyncClient(timeout=10).get({BASE}/credits)       [balance_monitor.py:54-56]
       resp.raise_for_status()                                 [balance_monitor.py:59]
       except Exception: logger.debug(...); return None        [balance_monitor.py:69-71]  ← engole TUDO
  → if balance is None: balance = get_cached()                 [config.py:215-216]
       get_cached() = _last_balance (só do loop background)     [balance_monitor.py:74-78]
  → if ainda None: return _err("Não foi possível consultar o saldo.", status=502)  [config.py:218]  ← O 502
```

- Frontend: disparo **único** no boot ([App.js:249-266](../web/static/js/components/shell/App.js#L249)); em 502 a condição `res.ok && res.data` é falsa e **nada acontece** — **sem loop de retry**. Ou seja, o 502 é ruído de console + painel sem saldo, não trava.
- `_err` → `JSONResponse(status_code=502)` ([helpers.py:20-24](../server/helpers.py#L20)). É intencional, não exceção de framework.
- Causa provável do relato: proxy Techify instável/lento (>10s) **no boot**, quando `_last_balance` ainda está vazio (nenhuma LLM call prévia populou o cache).

### 2.4 Falsos positivos / fora de escopo (descartados com razão)

| Hipótese | Veredito | Razão (verificada) |
|---|---|---|
| "O outbound envia pelo canal errado por causa do `source_id`" | ❌ Falso | `OutboundRouter.send_text(channel_id, chat_id, …)` roteia por `registry.get(channel_id)` ([outbound.py:80-84](../channels/outbound.py#L80)); nunca lê `source_id`. O smoking-gun do defeito (envio `…@s.whatsapp.net` via GOWA) só ocorria pela conversa fantasma no inbox WhatsApp — eliminada pelo plano 38. |
| "Há furo de isolamento de leitura independente" | ❌ Falso | Os 4 pontos de leitura aplicam `visible_inbox_ids` (2.2). O vazamento era 100% dado fantasma do #1. |
| "O 502 do balance é bug de código não-tratado (500/502 acidental)" | ❌ Falso | É `_err(..., status=502)` **explícito** ([config.py:218](../server/routes/config.py#L218)); `fetch_balance` nunca propaga exceção. |
| "GOWA precisa mudar o `source_id`" | ❌ Fora de escopo | D1 — GOWA byte-idêntico. |
| Seed `ai_active` por-canal | ✅ Já feito | Plano 38 F1; teste [tests/test_seed_ai_active_per_channel.py](../tests/test_seed_ai_active_per_channel.py). Não reabrir. |

---

## 3. Inventário / itens a fazer

| # | Item | `arquivo:linha` | O que falta | Abordagem | Risco | Esforço |
|---|------|-----------------|-------------|-----------|-------|---------|
| A | `source_id` canal-aware | [memory.py:166-169](../agent/memory.py#L166) | `_jid()` hardcoda sufixo WA | Provider decide a forma (hook, D3); GOWA mantém, demais usam id bare | Médio (dedup key) | M |
| A-mig | Re-âncora `contact_inboxes` não-GOWA | migration nova `0046` | linhas Telegram/Cloud já gravadas com `@s.whatsapp.net` | UPDATE strip-sufixo onde inbox é de provider não-GOWA | Médio (dado) | M |
| B | Teste de isolamento de leitura | [tests/](../tests/) (novo) | trava contra regressão de gate | Caracterização: usuário membro de 1 inbox → 404 no GET de conversa/mensagens de outro inbox | Baixo | S |
| B-clean | Limpeza do dado fantasma legado | script/migration opcional | fantasmas do #1 antes do plano 38 | Detectar `contact_inbox`/conversa no inbox default cujo contato só tem atividade em outro canal — **relatar antes de apagar** | Alto (destrutivo) | M |
| C | `/api/balance` degrada em vez de 502 | [config.py:214-218](../server/routes/config.py#L214) | 502 quando proxy fora | 200 `{balance:null, available:false, reason}` | Baixo | S |
| C-boot | Primar cache no boot (opcional) | [balance_monitor.py](../server/balance_monitor.py) / startup | cache vazio no 1º acesso | 1 fetch best-effort no `app.startup` | Baixo | S |
| C-fe | Frontend tolera `available:false` | [App.js:249-266](../web/static/js/components/shell/App.js#L249) | já ignora silenciosamente | garantir que `available:false` não abre modal nem loga erro | Baixo | S |

---

## 4. Fases / Roadmap

### Diagrama de dependências (waves)

```
WAVE 0  A0 · B0 · C0                         ← caracterização/diagnóstico, tudo em paralelo (🟢)
            │ (A0 bloqueia A1)  │ (C0 bloqueia C1)
WAVE 1  A1 → A2 · B1 · C1 · C2               ← A2 depende de A1; B1/C1/C2 independentes entre si (🟢, menos A2🔴)
            (barreira: A2 exige A1 mergeado)
WAVE 2  Z0                                   ← preencher CorrigirIAs.md + suíte verde (espera A/B/C)
```

### Tabela de fases

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando / dep |
|---|---|---|---|---|---|
| 0 | A0 | Caracterização `source_id` | 🟢 | baixo | teste atual do source_id verde (documenta comportamento GOWA) |
| 0 | B0 | Validar isolamento no banco | 🟢 | baixo | confirma inbox real do atendimento #3 + membership de `wa1` |
| 0 | C0 | Diagnóstico balance (proxy vivo?) | 🟢 | baixo | sabe se é proxy fora vs config |
| 1 | A1 | `_jid()` → provider hook | 🔴 | médio | `[depende: A0]` GOWA idêntico; Telegram usa id bare |
| 1 | A2 | Migration re-âncora | 🔴 | médio | `[depende: A1]` round-trip up/down verde |
| 1 | B1 | Teste isolamento + cleanup | 🟢 | baixo/alto | `[depende: B0]` teste trava 404 cross-inbox |
| 1 | C1 | Handler degrada (não 502) | 🟢 | baixo | `[depende: C0]` proxy fora → 200 `available:false` |
| 1 | C2 | Frontend tolera + boot-prime | 🟢 | baixo | painel não loga erro; modal só com saldo real |
| 2 | Z0 | Preencher `CorrigirIAs.md` + suíte | 🔴 | baixo | `[depende: A2,B1,C1,C2]` 3 defeitos com "Correção:" preenchida; suíte verde |

---

### Fase A0 — Caracterização do `source_id` (ANTES de mexer) 🟢
**Objetivo:** congelar o comportamento atual em teste antes de trocar `_jid()`.
- Itens:
  - `[paralelo]` Escrever `tests/test_source_id_per_channel.py`: cria canal GOWA (`default`) + canal não-GOWA (provider `whatsapp_cloud`/`telegram`), resolve conversa para um contato em cada, e **asserta** o `source_id` gravado em `contact_inboxes` (via `contact_inbox_repo`/select). Estado atual: **ambos** viram `phone@s.whatsapp.net` — o teste documenta isso primeiro (vai virar a asserção invertida em A1).
  - Reusar o helper de canal de [tests/test_seed_ai_active_per_channel.py](../tests/test_seed_ai_active_per_channel.py) (`_mk_channel`) como base.
- **Pronto quando:** `venv/bin/python -m pytest tests/test_source_id_per_channel.py -q` passa descrevendo o comportamento ATUAL (GOWA e não-GOWA ambos com sufixo).

#### Status de execução — Fase A0
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase B0 — Validar isolamento no banco (não é código) 🟢
**Objetivo:** confirmar que o Defeito #2 é 100% consequência do #1 (nenhum furo independente).
- Itens:
  - `[paralelo]` Query no banco afetado: a que `inbox_id` pertence o atendimento que `wa1@teste.com` conseguiu abrir; de quais inboxes `wa1` é membro (`inbox_members`); se o contato do Telegram tem `contact_inbox` no inbox default (o fantasma).
  - Se o atendimento aberto era o **fantasma** (inbox default) → confirma #1; se era o do Telegram e `wa1` **não** é membro → escalar (seria furo real, fora do que os sub-agentes acharam).
- **Pronto quando:** documentado no Status de execução: "leak = dado fantasma (inbox default)" **ou** "furo real → abrir defeito novo".

#### Status de execução — Fase B0
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher — inclua as queries e resultados)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase C0 — Diagnóstico do balance 🟢
**Objetivo:** separar "proxy fora/transitório" de "config errada".
- Itens:
  - `[paralelo]` `curl -H "Authorization: Bearer <api_key>" {LLM_API_BASE_URL}/credits` (usar a key real do config) — responde? latência < 10s? Confirma se o 502 é o proxy ou a base URL.
  - Conferir `LLM_API_BASE_URL` efetivo ([settings.py:17-19](../config/settings.py#L17)) e se `openrouter_api_key` está setado ([settings.py:108](../config/settings.py#L108)).
- **Pronto quando:** sabe-se a causa; a correção C1 (degradação) vale **independente** da causa (é hardening de UX).

#### Status de execução — Fase C0
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase A1 — `_jid()` vira `source_id` resolvido pelo provider 🔴 `[depende: A0]`
**Objetivo:** GOWA mantém `phone@s.whatsapp.net`/`@g.us`; providers não-GOWA usam o id nativo bare.
- Itens `[sequencial]`:
  1. Definir o builder de `source_id` **no provider** (D3, ver P1). Recomendado: classmethod `Channel.source_id_for(phone: str, is_group: bool) -> str` na base ([channels/base.py](../channels/base.py)) — default retorna `phone` (bare); `GOWAChannel` sobrescreve appendando `@g.us`/`@s.whatsapp.net` (precedente: `identity_from_credentials` classmethod, `fetch_avatar` hook do plano 38).
  2. `ContactMemory` resolve a forma pelo **provider do seu `channel_id`** (cache, igual `resolve_inbox_id` — [memory.py:20-34](../agent/memory.py#L20)) e usa em `_jid()`/no call de `resolve_for_contact_ex` ([memory.py:208](../agent/memory.py#L208)). `default`/GOWA ⇒ sufixo; demais ⇒ bare.
  3. Fallback fail-safe: se o provider não resolver, **manter o sufixo WA** (comportamento atual) — nunca quebrar a resolução (o `_resolve_conversation` já é fail-soft, [memory.py:212-214](../agent/memory.py#L212)).
  4. Inverter a asserção do teste A0: GOWA com sufixo, não-GOWA bare.
- **Pronto quando:** `tests/test_source_id_per_channel.py` verde com o comportamento NOVO; um contato Telegram novo grava `contact_inboxes.source_id = <chat_id bare>`; um contato GOWA continua `phone@s.whatsapp.net`. Suíte `tests/test_endpoints.py` verde (sem regressão no GOWA).

#### Status de execução — Fase A1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher — onde ficou o builder; como o provider é resolvido no write path)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase A2 — Migration de re-âncora dos `contact_inboxes` não-GOWA 🔴 `[depende: A1]`
**Objetivo:** reescrever as linhas já gravadas com sufixo WA em inboxes de provider não-GOWA, para casar a nova chave e evitar duplicata na próxima mensagem.
- Itens `[sequencial]`:
  1. Migration Alembic `0046` (próxima após [20260708_0045_mentions.py](../db/alembic/versions/20260708_0045_mentions.py)); `down_revision = "0045_mentions"`; **id ≤ 32 chars** (memória do repo: `alembic_version.version_num` é `varchar(32)`), ex.: `0046_source_id_native`.
  2. Lógica up: `UPDATE contact_inboxes SET source_id = <strip_sufixo>, source_jid = <idem>` **apenas** para linhas cujo `inbox_id` pertence a um canal de provider **≠ gowa** (JOIN `inboxes` → `channels.provider`), e cujo `source_id` termine em `@s.whatsapp.net`/`@g.us`/`@lid`. Strip = `split('@')[0]`.
  3. Tratar **colisão**: se já existir uma linha bare para o mesmo `(inbox_id, source_id_novo)`, consolidar (apontar conversas para a linha canônica e remover a duplicada) — o índice único `(inbox_id, source_id)` do `contact_inbox_repo` exige isso. Detectar e **logar** antes; se houver colisão real, resolver no `_load` da migration.
  4. Down: no-op documentado (não re-adiciona sufixo — perda de informação de provider; a re-âncora é one-way segura porque o novo `source_id` é o id nativo verdadeiro).
- **Pronto quando:** `alembic upgrade head` + `alembic downgrade -1` + `upgrade head` rodam limpos no Postgres de teste; após upgrade, nenhum `contact_inbox` de inbox não-GOWA termina em `@s.whatsapp.net`; enviar 2ª mensagem de um contato Telegram existente **não** cria uma 2ª `contact_inbox`.

#### Status de execução — Fase A2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher — tratamento de colisão)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher — round-trip up/down)_

---

### Fase B1 — Teste de isolamento de leitura + limpeza opcional 🟢 `[depende: B0]`
**Objetivo:** travar o isolamento contra regressão e limpar fantasmas legados (se B0 confirmar que existem).
- Itens:
  - `[paralelo]` `tests/test_conversation_read_isolation.py`: cria 2 inboxes (A/B) + 1 usuário membro só de A + conversa em cada. Assertar: GET `/api/atendimentos/{conv_B}` → **404**; GET `/api/atendimentos/{conv_B}/messages` → **404**; lista de conversas do usuário **não** traz a de B. (Reusa o padrão FastAPI TestClient de [tests/test_endpoints.py](../tests/test_endpoints.py).)
  - `[paralelo — só se B0 achou fantasmas]` Script de limpeza **não-destrutivo por padrão**: lista `contact_inbox`/conversas no inbox default cujo contato só tem atividade real em outro canal (heurística conservadora), **imprime** o que removeria; remoção real só com flag explícita e após revisão humana. ⚠️ Alto risco — nunca apagar cego (memória do repo: "não anexar/remover cego causou conversas duplicadas").
- **Pronto quando:** o teste de isolamento fica verde; a limpeza (se aplicável) rodou em modo relatório e o operador aprovou a remoção.

#### Status de execução — Fase B1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase C1 — `/api/balance` degrada em vez de 502 🟢 `[depende: C0]`
**Objetivo:** proxy fora não deixa o painel "sem saldo" nem gera 502.
- Itens:
  - Trocar o ramo de falha em [config.py:215-218](../server/routes/config.py#L215): quando `balance is None` e sem cache, retornar **`_ok`** com `{"balance": null, "available": false, "reason": "unavailable", "threshold": ..., "account_url": ...}` (status 200), em vez de `_err(status=502)`. Manter o **400** de api_key ausente ([config.py:211-213](../server/routes/config.py#L211)).
  - (Opcional) manter um log `warning` (não `debug`) em `fetch_balance` quando a exceção é de rede/5xx, para diagnóstico ([balance_monitor.py:69-71](../server/balance_monitor.py#L69)).
- **Pronto quando:** com proxy fora (mock levantando `httpx` error), `GET /api/balance` responde **200** com `available:false`; com proxy ok, responde o saldo real. Teste em `tests/test_endpoints.py` (o mock do LLM/proxy já existe lá).

#### Status de execução — Fase C1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase C2 — Frontend tolera `available:false` + prime de cache no boot 🟢
**Objetivo:** o painel ignora graciosamente saldo indisponível; reduzir a janela de 502 no boot.
- Itens:
  - `[paralelo]` [App.js:249-266](../web/static/js/components/shell/App.js#L249): garantir que `available:false` **não** abre o modal nem loga erro (hoje já ignora `res.ok:false`; validar que o novo shape `res.ok:true` + `data.available:false` também não dispara o modal — o gate deve ser `data.available && data.balance < threshold`).
  - `[paralelo, opcional]` Primar `_last_balance` no `app.startup` com um `fetch_balance` best-effort (try/except), para o 1º `GET /api/balance` já ter cache ([balance_monitor.py:74-99](../server/balance_monitor.py#L74)).
- **Pronto quando:** abrir o painel com proxy fora não mostra erro de saldo nem modal; com saldo baixo real, o modal ainda abre.

#### Status de execução — Fase C2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase Z0 — Preencher `CorrigirIAs.md` + suíte verde 🔴 `[depende: A2,B1,C1,C2]`
**Objetivo:** fechar o registro dos defeitos e provar verde ponta a ponta.
- Itens `[sequencial]`:
  - Preencher o campo **"Correção:"** dos Defeitos #1 e #2 em [CorrigirIAs.md](CorrigirIAs.md) com o `arquivo:linha` da correção real + nº da migration; anexar o item do balance (o console log tinha o 502) como resolvido, referenciando este plano.
  - Atualizar o bloco de status do topo deste plano para **IMPLEMENTADO** com o resumo por fase.
  - Rodar a suíte completa no Postgres de teste.
- **Pronto quando:** os campos "Correção:" estão preenchidos; a suíte está verde.

#### Status de execução — Fase Z0
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| `_jid()`/`source_id` (A1) | Mudar a chave de dedup sem migrar ⇒ 2ª mensagem de contato Telegram cria `contact_inbox` duplicada | A2 re-âncora as linhas existentes ANTES de o novo código rodar em produção; fallback fail-safe mantém sufixo se provider não resolver |
| Migration 0046 (A2) | Colisão `(inbox_id, source_id)` ao stripar sufixo se já existir linha bare | Detectar + consolidar (apontar conversas para a linha canônica) dentro da migration; logar antes |
| Migration 0046 (A2) | Id de revisão > 32 chars estoura no upgrade (memória do repo) | Nome curto `0046_source_id_native`; `down_revision="0045_mentions"` |
| GOWA `@lid` (A1) | Contatos GOWA por `@lid` — o `_jid()` appenda `@s.whatsapp.net` mesmo em lid hoje | GOWA fica **byte-idêntico** (D1) — não tocar; lid é nuance pré-existente do GOWA, fora de escopo |
| Limpeza de fantasmas (B1) | Apagar dado real por engano | Modo relatório por padrão; remoção só com flag + revisão humana; nunca cego |
| Balance 200 `available:false` (C1) | Frontend antigo interpretar `balance:null` como saldo zero e abrir modal | C2 gate `data.available && balance < threshold`; testar os dois shapes |
| Postgres único backend | Migration/queries devem ser Postgres-válidas | Rodar round-trip no `WHATSBOT_TEST_DB_URL` (banco com `test` no nome) |
| Plano irmão do 38 | Reabrir o seed `ai_active` por-canal | Não tocar — já fechado (2.4) |

---

## 6. Perguntas em aberto

**P1 — Onde mora o builder de `source_id` do provider?**
✅ DECIDIDO (2026-07-09): classmethod `Channel.source_id_for(phone, is_group)` na base ([channels/base.py](../channels/base.py)), default `phone` bare, `GOWAChannel` sobrescreve com o sufixo — coerente com os planos 32/33/38 (fina no provider, genérica no core), sem `if provider == "gowa"` no core.
- Contexto: `ContactMemory` (write path) tem `channel_id`, não necessariamente uma instância viva do canal.
- Opções: (a) **classmethod no provider** resolvido por um registry de **classes por provider** (não precisa de conexão viva) — recomendado; (b) helper puro keyed em `provider` resolvido via `channel_repo.get(channel_id)["provider"]` cacheado (1 ponto de `if provider`), fallback se (a) não alcançar o write path.
- **A confirmar na execução (A1):** se o registry de classes por provider é alcançável de `agent/memory.py` sem import circular / sem canal vivo. Se não for, cair para (b) — ainda respeita D3 concentrando a única checagem num helper, não espalhada.

**P2 — Limpar os fantasmas legados agora ou só travar daqui pra frente?**
⏸️ ADIADO para B0/B1: depende de a query de B0 achar fantasmas neste deploy. Recomendação: travar sempre (teste B1); limpar só em modo relatório + aprovação (dado destrutivo).

**P3 — Primar o cache do balance no boot (C2 opcional)?**
✅ DECIDIDO (2026-07-09): fazer, best-effort try/except no `app.startup`. Barato, reduz a janela de "sem saldo" no 1º acesso. Se falhar, o degrade da C1 cobre.

---

## 7. Apêndice — arquivos-chave (por camada)

**Backend / core**
- [agent/memory.py](../agent/memory.py) — `_jid()` (166), `_resolve_conversation` (207), `resolve_inbox_id` (20) — A1
- [channels/base.py](../channels/base.py) — hook `source_id_for` (novo) — A1
- [channels/providers/gowa_channel.py](../channels/providers/gowa_channel.py) — override GOWA — A1
- [db/repositories/conversation_repo.py](../db/repositories/conversation_repo.py) — `resolve_for_contact_ex` (289), `get_with_channel` (417) — referência
- [db/repositories/contact_inbox_repo.py](../db/repositories/contact_inbox_repo.py) — dedup key — referência

**DB / migration**
- `db/alembic/versions/0046_source_id_native.py` (novo) — A2
- [db/alembic/versions/20260708_0045_mentions.py](../db/alembic/versions/20260708_0045_mentions.py) — `down_revision` — A2

**Backend / balance**
- [server/routes/config.py](../server/routes/config.py) — `get_balance` (203-231) — C1
- [server/balance_monitor.py](../server/balance_monitor.py) — `fetch_balance` (49-71), `get_cached` (74) — C1/C2

**Frontend**
- [web/static/js/components/shell/App.js](../web/static/js/components/shell/App.js) — fetch de balance no boot (249-266) — C2

**Testes**
- `tests/test_source_id_per_channel.py` (novo) — A0/A1
- `tests/test_conversation_read_isolation.py` (novo) — B1
- [tests/test_endpoints.py](../tests/test_endpoints.py) — balance (C1) + regressão
- [tests/test_seed_ai_active_per_channel.py](../tests/test_seed_ai_active_per_channel.py) — helper `_mk_channel` reusável

**Doc**
- [docs-planos/CorrigirIAs.md](CorrigirIAs.md) — preencher "Correção:" — Z0

---

## 8. Checklist de verificação

- [ ] `tests/test_source_id_per_channel.py` verde (GOWA com sufixo; não-GOWA bare)
- [ ] `tests/test_conversation_read_isolation.py` verde (404 cross-inbox em GET conversa + mensagens)
- [ ] `tests/test_endpoints.py` verde no Postgres (`WHATSBOT_TEST_DB_URL`, banco com `test` no nome) — inclui balance degradado
- [ ] `tests/test_seed_ai_active_per_channel.py` continua verde (sem regressão do plano 38)
- [ ] Migration `0046` round-trip: `alembic upgrade head` → `downgrade -1` → `upgrade head` limpo
- [ ] Pós-migration: nenhum `contact_inbox` de inbox não-GOWA termina em `@s.whatsapp.net`; 2ª msg de contato Telegram existente não duplica `contact_inbox`
- [ ] `GET /api/balance` com proxy fora → 200 `available:false` (não 502); com proxy ok → saldo real
- [ ] Painel: proxy fora não abre modal nem loga erro; saldo baixo real ainda abre o modal
- [ ] `CorrigirIAs.md` com "Correção:" preenchida nos Defeitos #1 e #2 (+ nota do balance)
- [ ] Sem segredo (api_key) em URL/log; `fetch_balance` continua sem vazar a key
- [ ] Um refactor por commit; verde a cada fase
