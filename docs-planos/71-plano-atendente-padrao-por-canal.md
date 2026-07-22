# Plano 71 — Atendente padrão para novas conversas (por canal): dúvida Curseduca nasce atribuída ao Atendente X (core, sem Windmill)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-21 · **Escopo:** médio (core + canais + frontend; sem migration se guardar em `config`)
> **Origem:** pedido do usuário (Empresa Exemplo prod) + **plano 70** (abordagem via Windmill/operador = **Opção A**, descartada). Após investigar, o usuário escolheu a **Opção B**: um recurso genérico de **"atendente padrão para novas conversas" por canal**, 100% no core do WhatsBot — o **Windmill não muda**. Este plano 71 é a solução escolhida; o 70 fica como registro histórico da Opção A.
> **Método:** verificado nesta sessão — (a) banco de produção via Vault (DB privado Empresa Exemplo / database `whatsbot`) confirmou canal/inbox/ids/premissa; (b) leitura do caminho de nascimento da conversa + config de canal + form via 2 sub-agentes Explore, com `arquivo:linha`; (c) leitura do script de produção no Windmill. Nada de memória.
> **Forma da solução:** cada canal ganha um campo opcional **"Atendente padrão para novas conversas"** (`user_id` humano). Quando setado, toda conversa **nascida** nesse canal é carimbada com `assignee_user_id` + nasce com **IA off** (`ai_active=0` ⇒ sem agente vinculado, pela regra que já existe no INSERT). Aplicação imediata: canal "Avisos Curseduca" → Atendente X. **Zero mudança no Windmill, Chatwoot ou n8n.**
>
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 ✅ (2026-07-21) | **IA desligada** na conversa atribuída (humano assume 100%) | Nascer com `ai_active=0`. A regra do INSERT já zera o `active_agent_key` quando `ai_active=0` — sem limpar agente à mão |
| D3 ✅ (2026-07-21) | Automação Curseduca vive no **Windmill** (não n8n) e **não será alterada** | Mudança **só no core do WhatsBot**. O script `f/whatsbot/duvidas_forum_curseduca_prod` fica **intocado** |
| D6 ✅ (2026-07-21) | Alvo = `atendimento.coolify.exemplo.com.br`, canal Website **"Avisos Curseduca"** | Confirmado no banco: `channel_id = website_54146c91`, `inbox_id = 20`, widget `wgt_0ad4HfbwTJ` |
| D8 ✅ (2026-07-21) | **Atendente fixo = Atendente X** (id **5**, `atendente.exemplo@example.com`, ativo) | Vira o valor do campo "Atendente padrão" **na config do canal** Avisos Curseduca |
| D10 ✅ (2026-07-21) | **CPF não será coletado** (era só viabilidade) | Fora de escopo |
| D11 ✅ (2026-07-21) | **Opção B escolhida** — recurso de core; **não** a Opção A (Windmill loga como operador) | Opção A descartada (evita usuário-robô + senha + poll). Rascunho de script A (`scratchpad/duvidas_forum_curseduca_prod_v2.py`) fica arquivado, não usado |
| D12 ✅ (2026-07-21) | A automação **e o novo código NÃO usam nada de Chatwoot nem n8n** | Puro core WhatsBot (config de canal + carimbo no nascimento). Sem import/chamada a Chatwoot/n8n. (Herança de schema Chatwoot na tabela `users` é só linhagem, não dependência de runtime) |
| D13 ✅ (2026-07-21) | Recurso é **genérico** (qualquer canal pode setar), não hard-coded para Curseduca | Um `<select>` no form de canal; o valor mora na config do canal. Curseduca é o 1º consumidor |
| **D14 ✅ (2026-07-21, pós-teste)** | **Aplicar TAMBÉM na REABERTURA quando a conversa está "Não atribuída"** — reverte a P2 de "só no nascimento" para o **híbrido**. Pedido do usuário após testar: no Curseduca a 1ª dúvida do aluno cria contato+conversa (atribui); ele fecha; a **2ª dúvida reabre a MESMA conversa**, que o fechamento deixou órfã — e precisa voltar pro Atendente X | Fase 6. Na reabertura, se `assignee_user_id IS NULL`, reaplica o atendente padrão + IA off. **Nunca** sobrescreve uma atribuição existente (só quando NULL), então respeita reatribuição manual sobrevivente (plano 67). Reuse de conversa JÁ ABERTA não é tocado |

---

## 1. Resumo executivo

Toda conversa nasce **sem atendente** — o INSERT em `atendimentos` nunca escreve `assignee_user_id` ([db/repositories/conversation_repo.py:146-152](../db/repositories/conversation_repo.py#L146-L152)), então fica NULL e cai na fila **Não atribuídas** (`assignee_user_id IS NULL AND active_agent_key IS NULL`). Atribuir a um humano só existe via **endpoint de operador autenticado** (`assign-agent`), que exigiria um usuário-robô + senha (Opção A, descartada — D11).

A **Opção B** adiciona um recurso de core **"atendente padrão para novas conversas" por canal**, espelhando o precedente do **agente de IA padrão por inbox** (`inboxes.default_agent_key`, carimbado no nascimento). O valor (um `user_id` humano) mora na **config do canal**; no nascimento, o core lê esse valor e, se presente, carimba `assignee_user_id` + força `ai_active=0`. A conversa **nasce atribuída ao Atendente X com a IA desligada**, fora de "Não atribuídas". O **Windmill entrega a dúvida como hoje** — não muda uma linha.

---

## 2. Como funciona hoje (mapa verificado)

### 2.1 — Premissa confirmada no banco de produção (DB `whatsbot`)

| Fato | Valor |
|---|---|
| Canal "Avisos Curseduca" | `channel_id = website_54146c91` (widget `wgt_0ad4HfbwTJ`, enabled, `config.ai.ai_enabled=false` → IA já desligada no canal) |
| Inbox do canal | `inbox_id = 20` |
| `phone` do contato website | `wsess_…` (o `session_token`); `contact_type = site` |
| Atendente X | `user_id = 5`, `atendente.exemplo@example.com`, **ativo** |
| Estado atual | conversa nova (14916, open) nasce `assignee_user_id = NULL` → **Não atribuída**. Antigas quase todas já `assignee_user_id = 5` (hoje o Atendente X atribui **na mão**) |

### 2.2 — O caminho de nascimento da conversa (onde a mudança acontece)

| Peça | `arquivo:linha` | O que faz / por que importa |
|------|------|---------|
| INSERT (único writer de `atendimentos`) | [conversation_repo.py:113](../db/repositories/conversation_repo.py#L113) `_insert_conversation` | `.values(...)` em [:146-152](../db/repositories/conversation_repo.py#L146-L152) escreve `display_id, inbox_id, contact_id, status, ai_active, active_agent_key, origin…` — **`assignee_user_id` NÃO é escrito** (nasce NULL). **Ponto de extensão** |
| Regra "IA off ⇒ sem agente" | [conversation_repo.py:134-135](../db/repositories/conversation_repo.py#L134-L135) | `if active_agent_key is None and ai_active: active_agent_key = default_agent_key_for_inbox(inbox_id)` → nascida `ai_active=0` **já fica sem agente**. Não precisa limpar à mão |
| Master global no create | [conversation_repo.py:142-144](../db/repositories/conversation_repo.py#L142-L144) | `if not _global_ai_enabled(): ai_active=0; active_agent_key=None` |
| Precedente a espelhar | [conversation_repo.py:46](../db/repositories/conversation_repo.py#L46) `default_agent_key_for_inbox` | Lê `inboxes.default_agent_key` e carimba o **agente de IA** padrão no nascimento. O "atendente padrão humano" segue a MESMA forma |
| Coluna alvo | [db/tables.py:466](../db/tables.py#L466) `assignee_user_id Integer` (nullable, sem FK) | Já existe — é só escrever nela |
| Get-or-create race-safe | [conversation_repo.py:178](../db/repositories/conversation_repo.py#L178) `_create_open_atomic(…, ai_active_seed=None)` → INSERT em [:205-209](../db/repositories/conversation_repo.py#L205-L209) | O `ai_active_seed` já é threadado até aqui. O `assignee_user_id_seed` vai pelo **mesmo trilho** |
| Decisor create-vs-reopen | [conversation_repo.py:296](../db/repositories/conversation_repo.py#L296) `resolve_for_contact_ex(…, ai_active_seed=None)` | Cria (event `created`) OU reabre (event `reopened`); threada `ai_active_seed` em [:344-346](../db/repositories/conversation_repo.py#L344-L346). Adicionar `assignee_user_id_seed` paralelo |
| **Call site (chokepoint)** | [agent/memory.py:324](../agent/memory.py#L324) | `seed = 1 if self._resolve_ai_seed() else 0` + `resolve_for_contact_ex(…, ai_active_seed=seed)`. **`self.channel_id` e `self.inbox_id` em escopo** ([memory.py:157-158](../agent/memory.py#L157-L158)) → dá pra resolver o atendente padrão do canal aqui |
| Seed de IA por canal | [agent/memory.py:174](../agent/memory.py#L174) `_resolve_ai_seed` | Lê `ai_settings.value(channel_id, "ai_enabled"/"default_ai_enabled")`. É onde forçar `seed=0` quando houver atendente padrão |
| Materialização inbound (t=0) | [app/services/message_ingest_service.py:480-481](../app/services/message_ingest_service.py#L480-L481) | `contact.ensure_conversation_live("user", _reopen)` → `_resolve_conversation` → o call site acima |

### 2.3 — Semântica "atribuir a humano" (referência; NÃO reusada no nascimento)

`assign_unified(kind="user")` ([app/services/conversation_service.py:449](../app/services/conversation_service.py#L449)) faz `assignee_user_id=user_id, active_agent_key=None, ai_active=0` + espelha o **contato** `ai_enabled=0` + emite WS `conversation_assigned` + card `assigned`. No **nascimento** não há `conv` nem estado anterior → **não** reusamos essa orquestração (evita WS/card/mirror duplicados). O carimbo direto no INSERT (`assignee` + `ai_active=0`) já entrega o mesmo estado de colunas. Ver risco do badge de contato em §6.

### 2.4 — Config de canal: como persiste e é editada (sem allow-list ⇒ chave nova passa)

| Peça | `arquivo:linha` | Fato |
|------|------|------|
| PUT canal (persist config) | [app/services/channel_service.py:578](../app/services/channel_service.py#L578) | `fields["config"] = json.dumps(cfg)` — grava o dict **inteiro**, **sem allow-list de chaves**. Chave nova passa |
| CREATE canal | [app/services/channel_service.py:531](../app/services/channel_service.py#L531) | idem, serializa `config` inteiro |
| Invalidação de cache | [channel_service.py:592](../app/services/channel_service.py#L592) → [:205](../app/services/channel_service.py#L205) → [ai_settings.py:85](../channels/ai_settings.py#L85) `reset_cache` | PUT com `config` reseta o cache de `ai_settings` (TTL 30s) |
| Reader por-canal | [channels/ai_settings.py:77](../channels/ai_settings.py#L77) `value(channel_id, key, default)` | Lê `config["ai"][key]`. **Não** checa `PER_CHANNEL_AI_KEYS` (só o `ChannelSettingsView` checa) — `value()` já lê qualquer chave do sub-objeto `ai` |
| Form de IA por canal | [web/static/js/components/channels/AiSettingsFields.js:51](../web/static/js/components/channels/AiSettingsFields.js#L51) (`<select>` de `group_reply_mode`) | Padrão exato a copiar para o novo `<select>`. Round-trip do objeto `ai` inteiro (`value=${ai}`/`onChange=${setAi}`) |
| Montagem do payload | [channels/constants.js:199](../web/static/js/components/channels/constants.js#L199) `buildEditPayload` / [:171](../web/static/js/components/channels/constants.js#L171) `buildCreatePayload` | `payload.config = { ...cfg, ...configValues, ai: f.ai }` — se o campo morar em `ai`, **não precisa** mexer aqui |
| Lista de usuários (picker) | [server/routes/channels.py:209](../server/routes/channels.py#L209) `GET /api/channels/assignable-users` → `{users:[{id,name,email,is_admin}]}` (gate `channel.manage`) | **Já existe** e o form **já carrega** (`ChannelForm.js:61` `listChannelAssignableUsers`, `ChannelEditForm.js:71` via `getChannelMembers` → `.users`). Reusar — sem endpoint novo |
| Classe de campo (modo escuro) | [web/static/css/custom.css:250](../web/static/css/custom.css#L250) `.wa-field` | O `<select>` novo usa `class="wa-field w-full px-3 py-2 rounded-md text-[14px]"` |

---

## 3. Inventário / análise (Opção B)

| # | Camada | Onde | O que fazer | Risco | Esforço |
|---|--------|------|-------------|-------|---------|
| 1 | Backend (repo) | [conversation_repo.py:113](../db/repositories/conversation_repo.py#L113) `_insert_conversation` | Novo param `assignee_user_id: int \| None = None`; incluir em `.values(...)` ([:146](../db/repositories/conversation_repo.py#L146)) | baixo | S |
| 2 | Backend (repo) | [conversation_repo.py:178](../db/repositories/conversation_repo.py#L178) `_create_open_atomic` | Novo param `assignee_user_id_seed`; passar ao INSERT ([:205-209](../db/repositories/conversation_repo.py#L205-L209)) | baixo | S |
| 3 | Backend (repo) | [conversation_repo.py:296](../db/repositories/conversation_repo.py#L296) `resolve_for_contact_ex` | Novo param `assignee_user_id_seed`; forward ao `_create_open_atomic` ([:344-346](../db/repositories/conversation_repo.py#L344-L346)) | baixo | S |
| 4 | Backend (seed) | [agent/memory.py:324](../agent/memory.py#L324) `_resolve_conversation` | Resolver o atendente padrão do canal; se houver, `seed=0` e passar `assignee_user_id_seed` | médio | M |
| 5 | Backend (reader) | [channels/ai_settings.py:27](../channels/ai_settings.py#L27) | Adicionar `"default_assignee_user_id"` a `PER_CHANNEL_AI_KEYS` (documenta intenção; `value()` já lê) | baixo | S |
| 6 | Frontend (form) | [AiSettingsFields.js:51](../web/static/js/components/channels/AiSettingsFields.js#L51) | Novo `<select>` "Atendente padrão para novas conversas" (opções: "Nenhum" + `users` ativos). Valor → `ai.default_assignee_user_id` (int/null). Passar prop `users` de `ChannelForm.js`/`ChannelEditForm.js` | baixo | M |
| 7 | Testes | `tests/` | Canal com `config.ai.default_assignee_user_id=<uid>` → inbound → conversa nasce `assignee_user_id=<uid>`, `ai_active=0`, `active_agent_key=NULL`, fora de "Não atribuídas" + reopen mantém o dono | médio | M |
| 8 | Config de prod | canal Avisos Curseduca | Setar "Atendente padrão = Atendente X (5)" na config do canal (via UI, pós-deploy). Nenhum código | baixo | S |

### Falsos positivos descartados

| Suspeita | Por que NÃO é o ponto |
|---|---|
| "Reusar `assign_unified` no nascimento" | No create não há `conv`/estado anterior; reusar geraria WS/card/mirror duplicados. Carimbo direto entrega o mesmo estado. §2.3 |
| "Precisa limpar o agente no nascimento" | A regra [conversation_repo.py:134-135](../db/repositories/conversation_repo.py#L134-L135) já não carimba agente quando `ai_active=0`. Basta forçar `seed=0` |
| "Backend rejeita chave nova na config" | Sem allow-list ([channel_service.py:578](../app/services/channel_service.py#L578)); config serializada inteira |
| "Precisa endpoint novo pra listar usuários" | `GET /api/channels/assignable-users` já existe e o form já carrega ([channels.py:209](../server/routes/channels.py#L209)) |
| "Precisa migration" | Só se guardar em coluna de `inboxes` (alternativa purista, P1). Em `config.ai` (recomendado) **não** há migration |
| Opção A (Windmill loga como operador) | Descartada (D11): exige usuário-robô + senha + poll. B é server-side, sem robô |

---

## 4. Design detalhado

### 4.1 — Onde guardar o valor (P1 — recomendação abaixo)

**Recomendado — `channels.config.ai.default_assignee_user_id`** (no sub-objeto `ai`):
- Sem migration; reusa cache/invalidação/`value()` de `ai_settings`; o `AiSettingsFields` já faz round-trip do objeto `ai` (frontend mínimo, sem tocar `buildCreate/EditPayload`); leitura no nascimento via `ai_settings.value(self.channel_id, "default_assignee_user_id", None)`.
- Contra: um humano dentro da config "ai" (aceitável — é sobre *quem atende a conversa nova*: IA-agente vs humano-atendente).

Alternativas (P1): top-level `config.default_assignee_user_id` (semântica mais limpa, mexe nos builders + `constants.test.js`) e coluna `inboxes.default_assignee_user_id` (purista, espelha `default_agent_key`, exige migration + form escreve no inbox).

### 4.2 — Lógica no nascimento (chokepoint [agent/memory.py:324](../agent/memory.py#L324))

Pseudo (ilustrativo, **não** é o patch):
```python
seed = 1 if self._resolve_ai_seed() else 0
assignee_seed = self._resolve_default_assignee()   # int|None da config do canal
if assignee_seed:
    seed = 0                                        # atendente humano ⇒ nasce IA off (⇒ sem agente)
conv, transition = conversation_repo.resolve_for_contact_ex(
    self.id, self._source_id(), reopen_if_closed=reopen_closed,
    inbox_id=self.inbox_id, origin=origin, create_closed=create_closed,
    ai_active_seed=seed, assignee_user_id_seed=assignee_seed)
```
`_resolve_default_assignee()` = `ai_settings.value(self.channel_id, "default_assignee_user_id", None)` coagido para `int`-ou-`None` (defensivo: `""`/`"0"`/inválido → None). O carimbo só vale no **create** (event `created`); no **reopen** a conversa mantém o dono (ver P2).

### 4.3 — Fronteira de threading no repo

`_insert_conversation` ganha `assignee_user_id` no `.values()`; `_create_open_atomic` e `resolve_for_contact_ex` ganham `assignee_user_id_seed` e repassam — idêntico ao trilho de `ai_active_seed`. `resolve_for_contact` (wrapper, [:353](../db/repositories/conversation_repo.py#L353)) **não** muda (o inbound usa o `_ex`).

---

## 5. Fases / Roadmap

```
WAVE 0  F1(caracterização/teste-alvo)                         ← primeiro (🔴), fixa o comportamento esperado
           │
WAVE 1  F2(backend: threading + seed) · F3(frontend: select)  ← paralelos (🟢) [F2 bloqueia F4]
           │ (barreira: F2 verde)
WAVE 2  F4(teste E2E core) → F5(deploy + setar Atendente X + validar em prod)   ← sequencial (🔴)
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | F1 | Teste-alvo (caracterização do nascimento) | 🔴 | baixo | teste vermelho prova "nasce sem assignee"; vira verde ao fim de F2 |
| 1 | F2 | Backend: `assignee_user_id` no INSERT + threading + seed no memory.py + chave em `ai_settings` | 🔴 [bloqueia F4] | médio | teste-alvo verde; conversa nasce atribuída + IA off |
| 1 | F3 | Frontend: `<select>` "Atendente padrão" no form de canal (prop `users`) | 🟢 | baixo | selecionar/salvar persiste em `config.ai.default_assignee_user_id`; legível no dark mode |
| 2 | F4 | Teste E2E no core (inbound → nasce atribuída; reopen mantém) + suíte verde | 🔴 [depende de: F2] | médio | `tests/` verde no Postgres |
| 2 | F5 | Deploy `developer`→prod + setar Atendente X na config do canal + validar dúvida real | 🔴 [depende de: F2,F3] | médio | dúvida real nasce atribuída ao Atendente X, IA off, fora de "Não atribuídas"; Windmill intocado |

---

### Fase 1 — Teste-alvo (caracterização)
**Objetivo:** travar o comportamento esperado antes de mexer no fluxo crítico de criação de conversa.
**Itens:**
1. `[sequencial]` Teste que cria um canal com `config.ai.default_assignee_user_id=<uid>`, simula um inbound e afirma o estado nascido. Inicialmente **vermelho** (hoje nasce NULL).
2. `[paralelo]` Controle: canal **sem** o campo → nasce `assignee_user_id=NULL` (legado inalterado).

**Pronto quando:** o teste-alvo falha por `assignee_user_id` NULL (prova o gap); o controle passa.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-07-21)
- **O que foi feito:** Criado `tests/test_plano71_default_assignee_per_channel.py` com o teste-alvo (`test_channel_with_default_assignee_stamps_owner_and_ia_off`) + o controle (`test_channel_without_default_assignee_is_unchanged`). Espelha `test_seed_ai_active_per_channel.py` (plano 38 F1): helper `_mk_channel(default_assignee_user_id=…)`, `_mk_user`, `_set_global`, `_seed_conv` (via `handler._get_contact(...).add_message("user", ...)`).
- **Como foi feito / decisões:** Trabalho num worktree isolado `plano-71-atendente-padrao` (base = developer HEAD 5bfe805). Banco de teste DEDICADO `whatsbot_test_71` (UTF8/template0) — nunca o `whatsbot_test` compartilhado (a suíte dá DROP SCHEMA por processo). O helper `_set_global(True)` liga `auto_reply`+`default_ai_enabled` para provar que o carimbo de atendente força IA-off mesmo com a IA global/canal ligada.
- **Problemas / pendências:** —
- **Verificação:** Alvo VERMELHO por `assert None == 1` (hoje `assignee_user_id` nasce NULL); controle VERDE (canal sem o campo nasce NULL + IA on). Vira verde ao fim de F2.

---

### Fase 2 — Backend (o núcleo)
**Objetivo:** conversa nascida num canal com atendente padrão nasce atribuída + IA off.
**Itens (sequenciais):**
1. `[sequencial]` `_insert_conversation`: param `assignee_user_id` + no `.values()` ([conversation_repo.py:113,146](../db/repositories/conversation_repo.py#L113)).
2. `[sequencial]` `_create_open_atomic` + `resolve_for_contact_ex`: threading `assignee_user_id_seed` ([:178](../db/repositories/conversation_repo.py#L178), [:296](../db/repositories/conversation_repo.py#L296)).
3. `[sequencial]` `agent/memory.py:_resolve_conversation`: `_resolve_default_assignee()` + forçar `seed=0` quando houver + passar o seed ([memory.py:324](../agent/memory.py#L324)).
4. `[paralelo]` `PER_CHANNEL_AI_KEYS += "default_assignee_user_id"` ([ai_settings.py:27](../channels/ai_settings.py#L27)).

**Pronto quando:** teste-alvo (F1) verde; conversa nasce `assignee_user_id=<uid>`, `ai_active=0`, `active_agent_key=NULL`; canal sem o campo segue nascendo NULL.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-07-21)
- **O que foi feito:**
  1. `_insert_conversation`: novo param `assignee_user_id: int | None = None` + escrito no `.values()` do INSERT ([conversation_repo.py:113,146](../db/repositories/conversation_repo.py#L113)).
  2. `_create_open_atomic`: novo param `assignee_user_id_seed` → repassado a `_insert_conversation` no ramo de create ([:178,205](../db/repositories/conversation_repo.py#L178)).
  3. `resolve_for_contact_ex`: novo param `assignee_user_id_seed` → forward ao `_create_open_atomic` (só no ramo `created`; o `create_closed`/reopen NÃO recebem dono) ([:296,344](../db/repositories/conversation_repo.py#L296)).
  4. `agent/memory.py`: novo `_resolve_default_assignee()` + fio no `_resolve_conversation` (`assignee_seed = self._resolve_default_assignee(); if assignee_seed: seed = 0`) ([memory.py:_resolve_conversation](../agent/memory.py)).
  5. `PER_CHANNEL_AI_KEYS += "default_assignee_user_id"` ([ai_settings.py:27](../channels/ai_settings.py#L27)).
- **Como foi feito / decisões:** **P1=(a)** — valor em `config.ai.default_assignee_user_id` (sem migration). **P2=(a)** — carimbo SÓ no `created` (o `assignee_user_id_seed` nem chega ao ramo reopen). **Coerção defensiva** em `_resolve_default_assignee`: `int` positivo (`None`/`""`/`"0"`/`0`/lixo ⇒ `None`), fail-open com `logger.exception`. **P5** aplicado: ignora atendente com `is_active=0` (guarda contra dono "fantasma"). O carimbo só força `seed=0` (IA off ⇒ sem agente pela regra existente do INSERT) — NÃO reusa `assign_unified` (sem WS/card/mirror duplicados, §2.3). Param opcional default `None` ⇒ canais sem o campo têm caminho byte-idêntico.
- **Problemas / pendências:** Zona compartilhada de `conversation_repo.py` com a sessão do plano 72 — só toquei `_insert_conversation`/`_create_open_atomic`/`resolve_for_contact_ex` (linhas 113–346); nada abaixo de ~500. P3 (espelhar `contact.ai_enabled=0`) a avaliar em F4 (fonte do badge).
- **Verificação:** teste-alvo F1 VERDE; suíte de conversa relacionada (seed plano 38, plano 67, race, read-isolation, plano 71) = **22 passed** no `whatsbot_test_71`.

---

### Fase 3 — Frontend (form do canal)
**Objetivo:** operador escolhe o atendente padrão na edição/criação do canal.
**Itens:**
1. `[sequencial]` `<select>` "Atendente padrão para novas conversas" em `AiSettingsFields` (opções: "Nenhum (fila Não atribuídas)" + usuários ativos), estilo `.wa-field`, espelhando o select de `group_reply_mode` ([AiSettingsFields.js:51](../web/static/js/components/channels/AiSettingsFields.js#L51)).
2. `[sequencial]` Passar a prop `users` (já carregada) de `ChannelForm.js`/`ChannelEditForm.js` para `AiSettingsFields`.
3. `[paralelo]` (opcional) Dica visual "a conversa nasce com a IA desligada" quando um atendente é escolhido.

**Pronto quando:** escolher um atendente + salvar persiste `config.ai.default_assignee_user_id`; reabrir o form mostra o valor; legível no modo escuro.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-07-21)
- **O que foi feito:** Novo `<select>` "Atendente padrão para novas conversas" em [AiSettingsFields.js](../web/static/js/components/channels/AiSettingsFields.js) (opções: `Nenhum (fila "Não atribuídas")` + usuários ativos), classe `.wa-field` (dark-mode). Grava `ai.default_assignee_user_id` como `int` (ou `null` no "Nenhum"). Prop `users` passada de [ChannelForm.js](../web/static/js/components/channels/ChannelForm.js) e [ChannelEditForm.js](../web/static/js/components/channels/ChannelEditForm.js) (já carregavam a lista para o `AgentPicker`). Dica visual "A conversa nasce atribuída a esta pessoa, com a IA desligada." quando um atendente é escolhido.
- **Como foi feito / decisões:** **Decisão de placement:** o select fica FORA do bloco `${aiOn ? ...}` (sempre visível), porque o canal-alvo "Avisos Curseduca" tem a IA do canal DESLIGADA (`ai_enabled=false`) — se estivesse dentro do bloco, não apareceria e o Atendente X não poderia ser setado. Sem tocar em `constants.js` (P1=(a): o campo mora em `ai`, e `buildCreate/EditPayload` já fazem round-trip de `ai` inteiro) ⇒ sem mudança em `constants.test.js`. **P3 resolvido sem mirror:** o badge "IA OFF" da linha lê `conv_ai_active` (o `ai_active` da conversa, plano 17 — [conversationRows.js:166-167,442-444](../web/static/js/services/conversationRows.js#L166)), não `contact.ai_enabled`; como nasce `ai_active=0`, o badge já mostra OFF. NÃO espelhei `contact.ai_enabled=0` (evita desligar a IA do contato em outros canais).
- **Problemas / pendências:** Flash cosmético no edit form: enquanto `users` carrega (async), a opção do atendente salvo ainda não existe → o select mostra "Nenhum"; ao carregar, exibe o valor certo. O valor em `ai` nunca é perdido (só o display pisca). Aceitável.
- **Verificação:** `node --check --input-type=module` OK nos 3 arquivos; `node --test constants.test.js` = 18/18 (payload builders intactos). Persistência round-trip validada por leitura de código (`parseChannelConfig`→`ai`→select→`buildEditPayload`).

---

### Fase 4 — Teste E2E no core
**Objetivo:** validar o fluxo completo sem regressão.
**Itens:**
1. `[sequencial]` Inbound novo (canal com atendente padrão) → conversa nasce atribuída + IA off + fora de "Não atribuídas".
2. `[sequencial]` Reopen: conversa fechada do mesmo contato reabre mantendo o dono (não re-carimba) — confere com P2.
3. `[sequencial]` Suíte cheia verde no Postgres (`WHATSBOT_TEST_DB_URL`).

**Pronto quando:** todos os testes verdes; nenhum efeito em canais sem o campo.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-07-21)
- **O que foi feito:** Ampliei `tests/test_plano71_default_assignee_per_channel.py` com os E2E: reuso de conversa aberta preserva o dono (cenário Curseduca), reopen pós-close-limpando NÃO re-carimba, reopen preserva dono trocado manualmente (P2), guarda P5 (atendente inativo ⇒ sem dono), e coerção defensiva (`""`/`"0"`/`0`/lixo ⇒ sem dono). **7/7 verdes.** Regressão verificada em amplitude no banco DEDICADO `whatsbot_test_71`.
- **Como foi feito / decisões:** O `pytest tests/` inteiro não roda num único processo (scripts standalone com `sys.exit` + `_engine_ready` é session-scoped ⇒ `DROP SCHEMA` 1×/processo ⇒ poluição cruzada de estado entre arquivos de app-boot; + fresh worktree sem `storages/plugins/`). Rodei por partes e, para cada suspeita, **isolei + comparei com a baseline** (minhas mudanças no `git stash`, mesmo ambiente):
  - **Sem regressão nova em lugar nenhum.** Toda falha observada é (a) poluição cruzada — passa isolada; ou (b) pré-existente/ambiente — **idêntica na baseline**.
- **Problemas / pendências:** Copiei `storages/plugins/` da checkout original pro worktree (gitignored, não vai no commit) pra tests dependentes de plugin coletarem. Falhas de ambiente pré-existentes (idênticas na baseline): characterization `rbac`(1)+`sandbox_improve`(9) [plugin `melhorias`/RBAC], `utm_atendente`(8) [rotas do plugin → 401], e `test_endpoints` (2, `agent_transfer_alert`). Nada disso é do plano 71.
- **Verificação:** plano71 **11/11** (cresceu de 7 com F4 + hardening); conjunto de conversa (seed p38 / p67 / race / read-isolation / plano71) **26/26**; `test_endpoints.py` **1427 pass / 2 fail** (2 idênticas na baseline); 3 scripts standalone **65/65**; `multichannel_routing`/`history_filter` verdes isolados; characterization+utm com falhas **byte-idênticas** minha-branch × baseline (stash).

**Revisão adversarial (multi-agente, pós-implementação):** rodei um workflow de review read-only (6 dimensões × verificação adversária de cada achado). 17 achados brutos → 9 confirmados. Hardening aplicado (backend, sem mudar o comportamento observável):
  - **Perf (hot path):** `_resolve_default_assignee` trocou `user_repo.get(uid)` (3 queries via `_with_roles`, descartadas no reuse) por novo `user_repo.is_active(uid)` (1 coluna, indexado). Canal SEM o campo continua zero-DB (early-return via `ai_settings.value` cacheado).
  - **Log:** coerção malformada (`int("abc")`) agora coage em silêncio (`except ValueError/TypeError → None`), sem `logger.exception`/traceback por mensagem; `logger.exception` reservado a falha inesperada de DB.
  - **Coerção:** barra `bool` explícito (`int(True)==1` stamparia o user 1).
  - **Testes (locks):** +4 casos — nascimento `create_closed` sem dono (P2), carimbo direto NÃO emite card de atribuição extra (§2.3, compara nº de `conversation_event` com×sem atendente), config REAL do Curseduca (master do canal OFF + atendente), e `auto_reply` global OFF ainda carimba.
  - **Não corrigido (nit, cosmético):** flash "Nenhum" no edit form enquanto `users` carrega — sem perda de dado (o valor sobrevive no `ai`), documentado em F3.

---

### Fase 5 — Deploy + config de prod
**Objetivo:** ativar em produção sem tocar no Windmill.
**Itens:**
1. `[sequencial]` Merge `developer`→prod (Coolify) do core.
2. `[sequencial]` Na UI do canal "Avisos Curseduca", setar "Atendente padrão = Atendente X".
3. `[sequencial]` Disparar uma dúvida real de teste → conferir: nasce atribuída ao Atendente X, IA off, protocolo do plugin `protocolos` ainda abre, Windmill/entrega intactos.

**Pronto quando:** dúvidas reais nascem atribuídas ao Atendente X; Windmill não mudou; sem regressão no agrupamento/dedup/protocolo.

#### Status de execução — Fase 5
**Estado:** 🟡 Pronto para deploy — ações de operador pendentes (código pronto)
- **O que foi feito:** Código do core+frontend pronto no branch `plano-71-atendente-padrao` (worktree isolado). Nenhuma linha de Windmill/Chatwoot/n8n tocada (D12). Migration NÃO necessária (P1=(a), valor em `config.ai`).
- **Como foi feito / decisões:** F5 é deploy + config de produção — **ações de operador**, fora do que esta sessão (worktree de implementação) executa. Passos a executar por quem faz o deploy:
  1. Merge do branch em `developer` → prod (Coolify) do core (esta feature vive no core; nenhum plugin muda).
  2. Na UI: **Canais → Avisos Curseduca → Editar** → campo "Atendente padrão para novas conversas" = **Atendente X (id 5)** → Salvar. (O select aparece mesmo com a IA do canal desligada, que é o caso — ver F3.) Persiste em `config.ai.default_assignee_user_id=5`.
  3. Disparar uma dúvida real de teste via o fluxo Curseduca (Windmill intocado) → conferir na conversa nova: `assignee_user_id=5` (Atendente X, fora de "Não atribuídas"), badge "IA OFF", protocolo do plugin `protocolos` ainda abre, entrega/agrupamento intactos.
- **Problemas / pendências:** Deploy + config + validação em prod pendentes (operador). Guarda P5 no core já protege se o id 5 estiver inativo (não carimba).
- **Verificação:** A validar em prod após o deploy (checklist §9 F5). O comportamento está travado pelos testes de F1/F4 (nasce atribuída + IA off; reopen preserva; inativo/lixo ⇒ sem dono).

---

### Fase 6 — Extensão: reatribuir na REABERTURA quando "Não atribuída" (D14)
**Objetivo (pedido do usuário pós-teste):** cobrir o fluxo real do Curseduca — a 1ª dúvida do aluno nasce atribuída ao atendente padrão; o atendente fecha; a **2ª dúvida reabre a MESMA conversa**, que o fechamento deixou órfã, e precisa **voltar** pro atendente padrão. Sem isto, o recurso só valia para contatos totalmente novos.
**Itens:**
1. `[sequencial]` `resolve_for_contact_ex`: no ramo de reabertura (`reopen_if_closed and status=="closed"`), após `set_status(open)`, se `assignee_user_id IS NULL` **e** há `assignee_user_id_seed`, reaplica `assignee_user_id` + `ai_active=0` + `active_agent_key=NULL` (via `_update`). Só quando órfã ⇒ **nunca** sobrescreve atribuição existente. Reuse de conversa já aberta não passa por aqui.
2. `[sequencial]` Testes: reabertura de conversa órfã reaplica o atendente + IA off (o fluxo Curseduca); reabertura que preserva um dono manual (close com `clear_assignee=False`) **não** reaplica o padrão.

**Pronto quando:** 2ª dúvida (reabrindo conversa fechada e órfã) volta atribuída ao atendente padrão + IA off; conversa com dono preservado não é mexida; canais sem o campo byte-idênticos.

#### Status de execução — Fase 6
**Estado:** ✅ Concluída (2026-07-21)
- **O que foi feito:** Ramo de reabertura de `resolve_for_contact_ex` ([conversation_repo.py](../db/repositories/conversation_repo.py)): `set_status(open)` → se `assignee_user_id IS NULL` e há seed, `_update` com `assignee_user_id`+`ai_active=0`+`active_agent_key=NULL`. Teste `test_reopen_does_not_restamp_default_assignee` **virou** `test_reopen_reassigns_default_when_unassigned` (agora exige a reatribuição + IA off); `test_reopen_preserves_manually_changed_owner` mantido (dono não-NULL não é sobrescrito).
- **Como foi feito / decisões:** Guarda **só quando `IS NULL`** — não rouba reatribuição manual nem a que sobrevive ao close (plano 67). O seed vem do mesmo `_resolve_default_assignee` (já valida user ativo — P5). Reuse de conversa aberta intocado (respeita o estado vivo). Para canais SEM o campo (`seed=None`) o ramo de reabertura é byte-idêntico ao legado. Chamo `_update`/`set_status` (definidos abaixo no arquivo) sem editá-los — nada na zona da sessão paralela (plano 72).
- **Problemas / pendências:** —
- **Verificação:** plano71 **11/11**; conjunto de conversa (seed p38 / p67 / race / read-isolation / plano71) **26/26** no `whatsbot_test_71`.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Fluxo crítico de criação | Mexer no nascimento pode regredir todos os canais | Caracterização ANTES (F1); param **opcional** (default None) ⇒ canais sem o campo têm caminho byte-idêntico; suíte cheia (F4) |
| Reabertura de conversa órfã (D14/F6) | Aluno recorrente: 1ª dúvida atribui, atendente fecha, 2ª dúvida reabre "Não atribuída" e ficaria sem dono | **Resolvido (F6):** na reabertura, se `assignee_user_id IS NULL`, reaplica o atendente padrão + IA off. Só quando órfã ⇒ não rouba reatribuição manual (nem a que sobrevive ao close via plano 67). Reuse de conversa já aberta não é tocado |
| Badge "IA OFF" do contato | `assign_unified` espelha `contacts.ai_enabled=0`; o carimbo direto **não**. Se o badge da linha ler `contact.ai_enabled`, pode mostrar "IA ON" apesar de `ai_active=0` | Confirmar a fonte do badge; se necessário, espelhar `contact.ai_enabled=0` no nascimento quando houver atendente padrão. Gate de IA **não** depende disso (nasce `ai_active=0`). P3 |
| Atendente inativo/removido | Config aponta pra `user_id` desativado → conversa nasce com dono "fantasma" | Picker só lista ativos; defensivo: ignorar o carimbo se o user não estiver ativo (P5). Editável na UI |
| Sem card "atribuído" no nascimento | Carimbo direto não emite o notice `assigned` | **Intencional** — evita card duplicado; o event `created` já aparece. Sem ação |
| Coerção do valor | `config.ai.default_assignee_user_id` pode vir string/"" | Coagir para `int`-ou-`None`; `""`/`0`/inválido ⇒ None |
| Escopo genérico | Vale pra QUALQUER canal | Desejado (D13). Só o canal Avisos Curseduca será setado agora |
| Modo escuro | `<select>` novo ilegível no dark | Usar `.wa-field` (regra do CLAUDE.md) |
| Chatwoot/n8n | Introduzir dependência indevida | **Nenhuma** — puro core WhatsBot; sem import/chamada a Chatwoot/n8n (D12) |
| Postgres (único backend) | — | Sem SQL novo se P1=config; se P1=coluna, migration Alembic round-trip |

---

## 7. Perguntas em aberto

**P1 — Onde guardar o `default_assignee_user_id`.** ⏸️ A DECIDIR. (a) `config.ai.default_assignee_user_id` — **recomendado** (sem migration, frontend mínimo, reusa `ai_settings`); (b) top-level `config.default_assignee_user_id` (semântica mais limpa, mexe nos builders + `constants.test.js`); (c) coluna `inboxes.default_assignee_user_id` (purista, espelha `default_agent_key`, migration + form escreve no inbox). **Recomendo (a).**

**P2 — Reatribuir no reopen?** ✅ **DECIDIDO (D14, 2026-07-21): híbrido seguro.** Inicialmente ficou em (a) "só no nascimento", mas o teste do usuário mostrou o gap real do Curseduca: a 1ª dúvida atribui, o atendente fecha, e a 2ª dúvida reabre a MESMA conversa "Não atribuída" (o close limpa o dono) — sem reatribuir. Escolhido: **nascimento + reabertura QUANDO `assignee_user_id IS NULL`** — reaplica o atendente padrão + IA off só quando a conversa está órfã, **nunca** sobrescrevendo uma atribuição existente (não rouba reatribuição manual; respeita o plano 67 "keep assignee"). Reuse de conversa **já aberta** não é tocado. Implementado na Fase 6.

**P3 — Espelhar `contact.ai_enabled=0` no nascimento?** ⏸️ A DECIDIR — depende da fonte do badge "IA OFF" da linha (a confirmar no frontend). Se lê `contact.ai_enabled`, espelhar; senão dispensável (gate já cala por `ai_active=0`). **Recomendo confirmar e, se preciso, espelhar (barato).**

**P4 — Atendente padrão humano OU agente de IA (unificado)?** ⏸️ A DECIDIR. `inboxes.default_agent_key` já cobre o agente de IA; este plano cobre o humano. Unificar (kind user|ai) é possível, fora do escopo imediato. **Recomendo manter separado por ora.**

**P5 — Validar atendente ativo no nascimento?** ⏸️ A DECIDIR — baixo. Ignorar carimbo se o `user_id` não estiver ativo. **Recomendo sim (guarda barata).**

---

## 8. Apêndice — arquivos-chave

**Backend (core) — vão mudar:**
- [db/repositories/conversation_repo.py](../db/repositories/conversation_repo.py): `_insert_conversation` ([:113](../db/repositories/conversation_repo.py#L113)/[:146](../db/repositories/conversation_repo.py#L146)), `_create_open_atomic` ([:178](../db/repositories/conversation_repo.py#L178)), `resolve_for_contact_ex` ([:296](../db/repositories/conversation_repo.py#L296)); precedente `default_agent_key_for_inbox` ([:46](../db/repositories/conversation_repo.py#L46)).
- [agent/memory.py:324](../agent/memory.py#L324) (`_resolve_conversation` seed) + [:174](../agent/memory.py#L174) (`_resolve_ai_seed`).
- [channels/ai_settings.py:27](../channels/ai_settings.py#L27) (`PER_CHANNEL_AI_KEYS`).
- (referência) [db/tables.py:466](../db/tables.py#L466) `assignee_user_id`.

**Frontend — vai mudar:**
- [web/static/js/components/channels/AiSettingsFields.js](../web/static/js/components/channels/AiSettingsFields.js) (novo `<select>`), + prop `users` de `ChannelForm.js`/`ChannelEditForm.js`.
- (se P1=b) [web/static/js/components/channels/constants.js](../web/static/js/components/channels/constants.js) + `constants.test.js`.

**Uso de endpoints existentes (nenhum arquivo muda):**
- Lista de usuários: [server/routes/channels.py:209](../server/routes/channels.py#L209) `assignable-users` (já usado pelo form).
- Persistência de config sem allow-list: [app/services/channel_service.py:578](../app/services/channel_service.py#L578).

**Windmill — NÃO muda:** script `f/whatsbot/duvidas_forum_curseduca_prod` intocado. (Rascunho da Opção A `scratchpad/duvidas_forum_curseduca_prod_v2.py` arquivado — D11.)

---

## 9. Checklist de verificação

- [ ] **F1:** teste-alvo vermelho prova "nasce sem assignee"; controle (canal sem campo) verde.
- [ ] **F2:** conversa nascida em canal com atendente padrão → `assignee_user_id=<uid>`, `ai_active=0`, `active_agent_key=NULL`; canal sem o campo inalterado (caminho byte-idêntico).
- [ ] **F3:** `<select>` persiste `config.ai.default_assignee_user_id`; reabre com o valor; legível no modo escuro (`.wa-field`); `node --test` (se `constants.js` mudar).
- [ ] **F4:** inbound E2E nasce atribuída + fora de "Não atribuídas"; reopen mantém o dono; `tests/` verde no Postgres (`WHATSBOT_TEST_DB_URL`).
- [ ] **F5:** deploy prod; canal Avisos Curseduca com Atendente X setado; dúvida real nasce atribuída ao Atendente X + IA off; protocolo do plugin `protocolos` ainda abre; **Windmill intocado**.
- [ ] Nenhum uso de Chatwoot/n8n introduzido (D12); mudança é puro core WhatsBot.
- [ ] Migration round-trip **apenas se** P1=coluna (senão, sem SQL novo).
- [ ] `git status`: só arquivos de core/frontend/testes do plano mudaram; nada no Windmill.
