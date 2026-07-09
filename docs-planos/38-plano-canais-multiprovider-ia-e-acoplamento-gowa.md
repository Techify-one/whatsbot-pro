# Plano 38 — Correção multicanal: seed de `ai_active` por-canal (IA não responde no Telegram/Cloud) + acoplamento GOWA vazando no pipeline compartilhado

> **Status:** IMPLEMENTADO (F0–F7 + F-REG; F8 adiado por P3) · **Data:** 2026-07-08 · **Escopo:** médio (2 problemas · 1 bug de gate + 4 leaks de acoplamento · backend puro · **sem migration**).
> **Origem:** bug relatado pelo usuário — mesmo com "IA ativada por padrão para novos contatos" **marcado no canal Telegram**, nenhum contato novo do Telegram recebe resposta da IA. Investigação nesta sessão (2 auditores em paralelo + leitura + `grep` + estado real do banco) confirmou a causa-raiz e, na varredura de acoplamento pedida pelo usuário ("outras caixas têm coisas amarradas no GOWA?"), 4 leaks auxiliares que rodam para TODOS os providers mas chamam o GOWA.
> **Método:** leitura dos arquivos reais + `grep` exaustivo + query no Postgres (`config.default_ai_enabled='false'`, canal `telegram_1cfe2138` com `config.ai.default_ai_enabled=true`). Todo `arquivo:linha` abaixo foi **verificado nesta sessão**.
> **O quê/por quê:** (1) o seed do `ai_active` de uma conversa nova lê **só a config global** `default_ai_enabled`, ignorando o override **por-canal** — com a global OFF e o toggle do canal ON, toda conversa nova nasce `ai_active=0` e a IA nunca fala. (2) o hot-path de envio (`OutboundRouter`) está limpo, mas features auxiliares (avatar, read-receipt, permissão de grupo, prompt de grupo) chamam `gowa_client` incondicionalmente → chamada errada/desperdiçada para Telegram/Cloud.
>
> **Como usar este plano:** ao executar cada fase, preencha o bloco **"Status de execução"** dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro. **Verde a cada fase.** **Caracterização (F0) ANTES** de mexer no seed/gate. **Um refactor por commit.** As waves marcam o que roda em paralelo (🟢) e o que é sequencial/bloqueante (🔴).

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** ✅ (2026-07-08) | O `ai_active` de uma conversa nova deve honrar o `default_ai_enabled` **POR-CANAL** (`ai_settings.value(channel_id, "default_ai_enabled", <global>)`), não só a global. | O `ContactMemory` já resolve e carrega esse valor (`self._default_ai_enabled`, [memory.py:94](../agent/memory.py#L94), populado em [handler.py:250-253](../agent/handler.py#L250)). O fix é **threadar esse booleano até o INSERT** — não reler ai_settings lá embaixo. |
| **D2** ✅ (2026-07-08) | O `conversation_repo` **NÃO** pode conhecer `channels`/`ai_settings` (camada de dados pura). O valor por-canal chega como um **parâmetro** vindo de cima. | Adicionar um param opcional `ai_active_seed: int \| None = None` na cadeia `resolve_for_contact_ex → _create_open_atomic → _insert_conversation`. `None` mantém o fallback global atual (`_default_ai_enabled()`), então nenhum outro caller quebra. |
| **D3** ✅ (2026-07-08) | O hot-path outbound (`channels/outbound.py` `OutboundRouter`, capability-driven) está **LIMPO** — **não mexer**. | As correções do Problema 2 roteiam os leaks *auxiliares* pela abstração já existente (registry/capability/`_channel_for`), sem tocar no `OutboundRouter`. |
| **D4** ✅ (2026-07-08) | **Sem `if provider == "gowa"` novos** onde a capability/registry resolve. Gates por **capability** (`caps.groups`) ou por **hook de canal** (registry). | Avatar vira hook de canal (`fetch_avatar`, default `None`); permissão/prompt de grupo gated por `caps.groups`; read-receipt roteado por `_channel_for` + `outbound.mark_read`. |
| **D5** ✅ (2026-07-08) | **Fix aditivo e fail-open**: nenhuma mudança pode silenciar a IA nem derrubar o request. O default de `_conversation_ai_active` continua `True` (fail-open, [messaging_service.py:1187](../app/services/messaging_service.py#L1187)); leaks auxiliares seguem best-effort (try/except já existe). | O seed novo só **liga** a IA em canais que hoje a deixavam desligada por engano; nunca desliga onde já funcionava. |
| **Princípio fixo** | Este plano é **irmão do 37** ("keyed by contact em vez de canal"). Reusa a mesma infra por-canal (`inbox_id`, `_channel_for`, `OutboundRouter`). Não reabrir o que o 37 já fechou. | Onde o 37 já passou `channel_id`, apenas consumimos. |

---

## 1. Resumo executivo

Dois problemas independentes, ambos consequência de canais serem multi-provider enquanto pedaços do fluxo assumem GOWA:

- **Problema 1 (bug de gate — alta prioridade):** conversas novas nascem com `ai_active` semeado **só** pela config global `default_ai_enabled` ([conversation_repo.py:93-94](../db/repositories/conversation_repo.py#L93)). O toggle "IA por padrão para novos contatos" **do canal** é lido ([handler.py:250](../agent/handler.py#L250)) mas só alimenta a coluna `contacts.ai_enabled`, que o gate `_conversation_ai_active` **não lê mais** (desde o plano 17, [conversation_repo.py:72-73](../db/repositories/conversation_repo.py#L72)). Resultado observado no banco: global `false` + canal Telegram `true` ⇒ IA muda. Fix: threadar o booleano por-canal (que o `ContactMemory` já tem) até o INSERT.
- **Problema 2 (acoplamento — média/baixa prioridade):** 4 features auxiliares chamam `gowa_client` sem gate de provider, rodando para contatos Telegram/Cloud: **avatar** (`get_avatar`), **read-receipt** (`mark_as_read`), **permissão de grupo** (`can_bot_send_in_group`), **prompt de grupo** (`group_mentions.get_members` + texto "grupo do WhatsApp"). Consequência: chamada GOWA errada (id que o GOWA não resolve), desperdício, log ruidoso, e avatar do Telegram/Cloud que nunca atualiza porque o loop está gated na conexão do GOWA.

O hot-path de envio já é agnóstico (`OutboundRouter`) — **não é o problema**. As correções são cirúrgicas e de baixo risco.

---

## 2. Como funciona hoje (mapa) — `arquivo:linha` verificado

### 2.1 Problema 1 — a cadeia do seed
```
webhook/ingest → contact.ensure_conversation_live("user")        [memory.py:321]
  → contact._resolve_conversation("user")                        [memory.py:196]
    → conversation_repo.resolve_for_contact_ex(id, jid, inbox_id) [conversation_repo.py:245]
      → _create_open_atomic(...)  (quando não há conversa)        [conversation_repo.py:272]
        → _insert_conversation(..., ai_active=None)               [conversation_repo.py:155]
          → ai_active = 1 if _default_ai_enabled() else 0         [conversation_repo.py:93-94]
            → config_repo.get("default_ai_enabled", True)  ← SÓ GLOBAL  [conversation_repo.py:78]
```
- O valor **por-canal** existe e está em mãos: `ContactMemory._default_ai_enabled` ([memory.py:94](../agent/memory.py#L94)), resolvido em `_get_contact` via `ai_settings.value(channel_id, "default_ai_enabled", self.default_ai_enabled)` ([handler.py:250-253](../agent/handler.py#L250)). **Ele nunca desce até o INSERT.**
- ⚠️ Hoje esse booleano só vai para `contact_repo.get_or_create(default_ai_enabled=...)` → coluna `contacts.ai_enabled` ([memory.py:115](../agent/memory.py#L115)), **flag que o gate ignora** (plano 17).
- O gate final: `_conversation_ai_active` lê `conv["ai_active"]` ([messaging_service.py:1180-1187](../app/services/messaging_service.py#L1180)); a camada anterior `_channel_ai_enabled` (global `auto_reply` + `ai_enabled` do canal) já resolve por-canal corretamente ([webhook.py:77-87](../server/routes/webhook.py#L77)).
- ✅ Infra pronta para reuso: `ai_settings.value(channel_id, key, default)` ([ai_settings.py:77](../channels/ai_settings.py#L77)); `resolve_for_contact_ex`/`_create_open_atomic`/`_insert_conversation` já aceitam `ai_active` explícito ([conversation_repo.py:84,93](../db/repositories/conversation_repo.py#L84)) — só falta o param no meio da cadeia (`resolve_for_contact_ex`→`_create_open_atomic` hoje **hardcoda `ai_active=None`**, [conversation_repo.py:155](../db/repositories/conversation_repo.py#L155)).

### 2.2 Problema 2 — os leaks (todos verificados)

| # | Leak | `arquivo:linha` | Roda para | Gate hoje | Consequência p/ Telegram/Cloud |
|---|------|-----------------|-----------|-----------|-------------------------------|
| L1 | `refresh_avatar` → `gowa_client.get_avatar(phone)` incondicional | [avatars.py:47](../server/avatars.py#L47) | todo contato | nenhum | fetch GOWA com id que o GOWA não resolve; 204/vazio |
| L1a | Sweep de avatar itera **todos** os contatos e espera `state.connected` (flag do **GOWA**) | [background.py:203-232](../server/background.py#L203) | todo contato | conexão GOWA | avatar Telegram/Cloud **nunca** atualiza se GOWA cair/ausente |
| L1b | `GET /api/contacts/{phone}/avatar` on-demand → `gowa_client.get_avatar` | [contacts.py:1697](../server/routes/contacts.py#L1697) | qualquer provider | try/except | chamada GOWA errada (degrada p/ 204) |
| L1c | Abrir conversa dispara `refresh_and_broadcast(deps, phone)` | [contacts.py:641](../server/routes/contacts.py#L641) | qualquer provider | nenhum | idem L1 |
| L2 | `_send_read_receipts` → `gowa_client.mark_as_read` **hardcoded** (sem canal) | [contacts.py:226-238](../server/routes/contacts.py#L226) | qualquer provider | try/except | read-receipt GOWA para id de outro provider. Call sites: abrir conversa [:623](../server/routes/contacts.py#L623), endpoint mark-read [:1585](../server/routes/contacts.py#L1585) |
| L3 | `can_bot_send_in_group` → `gowa_client.can_bot_send_in_group` para qualquer `is_group` | [contacts.py:626-635](../server/routes/contacts.py#L626) | contato de grupo | `is_group`+`bot_phone` | chamada GOWA errada p/ grupo Telegram |
| L4 | `prompt_builder`: injeta "conversa de grupo do **WhatsApp**" + `group_mentions.get_members` (→ `gowa_client.get_group_info`) para qualquer `is_group` | [prompt_builder.py:44-54](../agent/prompt_builder.py#L44) | contato de grupo | nenhum | LLM é informado "WhatsApp" errado; get_group_info GOWA com id Telegram (swallow → membros vazios) |

- ✅ Infra pronta: `OutboundRouter.mark_read(channel_id, chat_id, msg_id)` já existe e no-op sem canal vivo ([outbound.py:186-192](../channels/outbound.py#L186)); `_channel_for(phone, conversation_id, channel_id)` resolve o canal de uma conversa ([contacts.py:130-146](../server/routes/contacts.py#L130)); `outbound.supports(channel_id, "groups")` resolve por capability ([outbound.py:45-46](../channels/outbound.py#L45), `ChannelCapabilities.groups` [base.py:22](../channels/base.py#L22)); precedente exato de roteamento no path do operador: `send_presence_to_contact` já usa `_channel_for` ([contacts.py:1580-1582](../server/routes/contacts.py#L1580)).
- ⚠️ `ChannelCapabilities` **não** tem hoje uma flag de "foto de perfil". O avatar é feature GOWA; a saída limpa (D4) é um **hook de canal** `fetch_avatar(chat_id) -> bytes | None` (default `None` na base), roteado pelo registry — GOWA implementa, Telegram/Cloud herdam `None`.

### 2.3 O que está CERTO e NÃO deve ser tocado (falsos alarmes)

| Hipótese | Veredito | Razão (verificada) |
|---|---|---|
| "Telegram roda no pipeline do GOWA" | ❌ Impreciso | Telegram usa o **funil genérico** `MessageIngestService.ingest_event` ([message_ingest_service.py:332](../app/services/message_ingest_service.py#L332)) e o mesmo `MessagingService`. Não há pipeline "do GOWA" — é compartilhado. |
| Outbound (envio da resposta) amarrado ao GOWA | ❌ Rejeitado | Tudo via `OutboundRouter` por `channel_id`, capability-gated ([messaging_service.py:385-387,818](../app/services/messaging_service.py#L385)). **Zero** `gowa_client` no send path. |
| `message_ingest_service.py:362` `== "gowa"` (descarte por JID) | ❌ Correto | Gated de propósito; não-GOWA classifica UNKNOWN e nunca é descartado. |
| `_jid()` gera `@s.whatsapp.net` p/ todo provider ([memory.py:166-169](../agent/memory.py#L166)) | ⚠️ **LOW / cosmético** | É `source_id` (identidade da conversa), **não vai pra wire**. Não misroteia mensagem. Fica como item opcional F5, fora do caminho crítico. |
| Tags/pin/unread por-contato | ❌ Rejeitado | Por design (plano 01). Não é bug. |
| Presença/mark-read no **pipeline de IA** | ❌ Correto | Já roteado por `OutboundRouter`/capability. O leak L2 é só no **path do operador** (`contacts.py`). |
| BR-phone normalization vazar | ❌ Rejeitado | `channels/br_phone.py` não é importado no pipeline compartilhado — fica dentro do canal GOWA. |

---

## 3. Inventário das mudanças

| ID | Mudança | Arquivos | Risco | Esforço |
|----|---------|----------|-------|---------|
| **A1** | Threadar `ai_active_seed` da cadeia de resolução de conversa | `conversation_repo.py` (`resolve_for_contact_ex`, `_create_open_atomic`) | baixo | S |
| **A2** | `ContactMemory` passa `1/0` do `self._default_ai_enabled` ao resolver | `memory.py` (`_resolve_conversation`, `ensure_conversation_live`) | baixo | S |
| **A3** | Caracterização: teste multicanal do seed (global OFF + canal ON ⇒ `ai_active=1`) | `tests/…` | baixo | S |
| **B1** | Hook `fetch_avatar` na base + impl GOWA; `refresh_avatar` roteia por canal | `channels/base.py`, `channels/providers/gowa_channel.py`, `server/avatars.py` | médio | M |
| **B2** | Sweep de avatar: resolver canal por contato; não travar só na conexão GOWA | `server/background.py` | médio | M |
| **B3** | `_send_read_receipts` roteia via `_channel_for` + `outbound.mark_read` | `server/routes/contacts.py` | baixo | S |
| **B4** | `can_bot_send_in_group` gated por `caps.groups` (ou hook de canal) | `server/routes/contacts.py` | baixo | S |
| **B5** | `prompt_builder`: gate de grupo por capability + texto provider-neutro + membros via canal | `agent/prompt_builder.py` | médio | M |
| **B6** *(opcional)* | `_jid()`/`source_id` provider-aware (cosmético) | `agent/memory.py` | baixo | S |

---

## 4. Waves e paralelização

```
WAVE 0   A0(caracterização) ─┐
                             │  (barreira leve: A3 depois de A1/A2)
WAVE 1   A1 → A2 → A3        │   ← Problema 1 (sequencial curto)  🔴 do seed
         B3 · B4             │   ← leaks triviais, independentes   🟢
                             │
WAVE 2   B1 → B2             │   ← avatar (B2 depende do hook de B1) 🔴
         B5                  │   ← prompt de grupo, independente     🟢
                             │
WAVE 3   B6(opcional) · F-REG+CHECK
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|------|------|-----------|-------|-------|---------------|
| 0 | **F0** | Caracterização do seed atual | 🔴 | baixo | Teste que fixa o comportamento hoje (global governa) roda verde |
| 1 | **F1** | A1+A2 seed por-canal | 🔴 [bloqueia F2-test] | baixo | Nova conversa em canal com toggle ON nasce `ai_active=1` mesmo com global OFF |
| 1 | **F2** | A3 teste multicanal do seed | 🔴 [depende de F1] | baixo | Suíte verde no Postgres cobrindo global OFF×canal ON/OFF |
| 1 | **F3** | B3 read-receipt roteado | 🟢 | baixo | Abrir/mark-read conversa Telegram não chama `gowa_client`; GOWA segue igual |
| 1 | **F4** | B4 permissão de grupo por capability | 🟢 | baixo | Grupo Telegram não dispara `can_bot_send_in_group` do GOWA |
| 2 | **F5** | B1 hook `fetch_avatar` + roteamento | 🔴 [bloqueia F6] | médio | Avatar de contato GOWA continua; Telegram/Cloud não chamam GOWA |
| 2 | **F6** | B2 sweep de avatar por-canal | 🔴 [depende de F5] | médio | Sweep resolve o canal de cada contato; não fica refém da conexão GOWA |
| 2 | **F7** | B5 prompt de grupo agnóstico | 🟢 | médio | Grupo Telegram: prompt não diz "WhatsApp"; sem `get_group_info` GOWA |
| 3 | **F8** | B6 `_jid` provider-aware *(opcional)* | 🟢 | baixo | `source_id` reflete o provider (ou item adiado) |
| 3 | **F-REG** | Regressão + checklist | 🔴 | baixo | Todos os checks §8 verdes |

**Paralelizável:** na Wave 1, **F3 e F4** rodam juntas (🟢) e independentes de F1/F2. Na Wave 2, **F7** roda em paralelo com F5→F6. F1→F2 e F5→F6 são cadeias sequenciais internas.

---

## 5. Fases (detalhe)

### Fase F0 — Caracterização do seed (🔴)
**Objetivo:** travar o comportamento atual antes de mudar, para provar a correção.
**Itens:**
- [sequencial] Adicionar teste que cria contato+conversa nova com `config.default_ai_enabled` global variando e verifica o `ai_active` resultante **hoje** (governado pela global). Base: `tests/test_endpoints.py` (Postgres via `WHATSBOT_TEST_DB_URL`).
- Documentar no teste o valor esperado ANTES (global governa) e deixar o assert do DEPOIS comentado para F2.

**Pronto quando:** teste verde refletindo o comportamento atual.

#### Status de execução — Fase F0
**Estado:** ✅ Concluída (fundida em F2)
- **O que foi feito:** criado `tests/test_seed_ai_active_per_channel.py` já com as asserções do comportamento CORRETO (pós-F1) em vez de um golden do bug — o bug estava provado pela investigação (query no banco). O arquivo cobre a matriz de F2 + reopen.
- **Como foi feito / decisões:** helper `_set_global` seta o global nos DOIS lugares (config + `handler.default_ai_enabled`), espelhando produção (main.py sincroniza os dois no boot/PUT config). `_mk_channel` grava `config['ai']['default_ai_enabled']` e invalida o cache de `ai_settings`.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `pytest tests/test_seed_ai_active_per_channel.py` → 5/5 verde.

---

### Fase F1 — Seed do `ai_active` por-canal (🔴, bloqueia F2)
**Objetivo:** a conversa nova nasce com o `ai_active` do toggle **do canal**.
**Itens:**
- [sequencial] `conversation_repo.resolve_for_contact_ex`: novo param `ai_active_seed: int | None = None` ([conversation_repo.py:245](../db/repositories/conversation_repo.py#L245)); repassar a `_create_open_atomic`.
- [sequencial] `_create_open_atomic`: aceitar `ai_active_seed` e passá-lo a `_insert_conversation` **no lugar do `ai_active=None` hardcoded** ([conversation_repo.py:155](../db/repositories/conversation_repo.py#L155)). `None` mantém o fallback global (`_default_ai_enabled()`) — nenhum outro caller quebra (D2).
- [sequencial] `memory.ContactMemory._resolve_conversation`: computar `seed = 1 if self._default_ai_enabled else 0` e passar `ai_active_seed=seed` ao `resolve_for_contact_ex` ([memory.py:196](../agent/memory.py#L196)). Idem em `ensure_conversation_live` se ela resolve por caminho próprio ([memory.py:321](../agent/memory.py#L321)) — **conferir que ambos passam pelo mesmo funil** (`_resolve_conversation`); se sim, um ponto só.
- ⚠️ Não tocar no reopen: reabrir conversa fechada **não** deve re-semear `ai_active` (preserva pausa manual). Só o CREATE usa o seed.

**Pronto quando:** com global `default_ai_enabled=false` e canal Telegram `default_ai_enabled=true`, uma mensagem de contato novo cria conversa `ai_active=1` e a IA responde. GOWA (ambos true) inalterado.

#### Status de execução — Fase F1
**Estado:** ✅ Concluída
- **O que foi feito:** `resolve_for_contact_ex` e `_create_open_atomic` ganharam o param `ai_active_seed: int | None = None`, repassado ao `_insert_conversation` no lugar do `ai_active=None` hardcoded. `ContactMemory._resolve_conversation` computa `seed = 1 if self._default_ai_enabled else 0` e o passa.
- **Como foi feito / decisões:** ponto único — tanto `add_message` quanto `ensure_conversation_live` passam por `_resolve_conversation`, então um só ponto seta o seed. `None` mantém o fallback global (`_default_ai_enabled()`), nenhum outro caller quebra (D2). Reopen não re-semeia: o seed só é usado no ramo CREATE de `_create_open_atomic`.
- **Problemas / pendências:** nenhuma.
- **Verificação:** matriz de F2 verde; `_seed_ai_active` cria conversa com o `ai_active` do canal.

---

### Fase F2 — Teste multicanal do seed (🔴, depende de F1)
**Objetivo:** fixar a correção.
**Itens:**
- [sequencial] Matriz: (global OFF, canal ON) ⇒ `ai_active=1`; (global OFF, canal ausente) ⇒ `0`; (global ON, canal OFF) ⇒ `0`; (global ON, sem override) ⇒ `1`. Usar dois canais (um GOWA `default`, um segundo com override distinto) para provar o por-canal.
- Ativar o assert "DEPOIS" comentado em F0.

**Pronto quando:** suíte verde no Postgres cobrindo a matriz.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída
- **O que foi feito:** matriz completa em `tests/test_seed_ai_active_per_channel.py`: (OFF×ON)⇒1, (OFF×ausente)⇒0, (ON×OFF)⇒0, (ON×ausente)⇒1, reopen não re-semeia. · **Decisões:** ajustado o harness de teste (`tests/support.py` + `tests/conftest.py`) para semear `handler.default_ai_enabled` de `settings`, espelhando produção (main.py) — sem isso o fallback global do seed ficava preso em `True` no double de teste. · **Pendências:** nenhuma. · **Verificação:** 5/5 verde + `tests/endpoints` (inclui p25 IA-OFF badge) verde.

---

### Fase F3 — Read-receipt roteado por canal (🟢)
**Objetivo:** parar de mandar read-receipt GOWA para conversa de outro provider.
**Itens:**
- [paralelo] `_send_read_receipts(phone, msg_ids)` → aceitar `channel_id` e trocar `gowa_client.mark_as_read(mid, phone)` por `outbound.mark_read(channel_id, phone, mid)` ([contacts.py:226-238](../server/routes/contacts.py#L226)). `outbound.mark_read` já no-op sem canal vivo.
- [paralelo] Call site abrir conversa ([:623](../server/routes/contacts.py#L623)): passar o `channel` já resolvido no `_load` (`data["channel_id"]`, [:601](../server/routes/contacts.py#L601)).
- [paralelo] Call site endpoint mark-read ([:1585](../server/routes/contacts.py#L1585)): resolver via `_channel_for(phone, body.get("conversation_id"), body.get("channel_id"))` (precedente: `send_presence_to_contact`, [:1580](../server/routes/contacts.py#L1580)).

**Pronto quando:** abrir/marcar-lida uma conversa Telegram não chama `gowa_client`; a mesma ação numa conversa GOWA continua enviando o receipt.

#### Status de execução — Fase F3
**Estado:** ✅ Concluída
- **O que foi feito:** `_send_read_receipts(phone, msg_ids, channel_id="default")` troca `gowa_client.mark_as_read` por `outbound.mark_read(channel_id, phone, mid)`. Abrir-conversa passa `data["channel_id"]`; endpoint `/read` ganhou `Body` opcional e resolve via `_channel_for(phone, conversation_id, channel_id)`. · **Decisões:** default "default" preserva o legado (all-channels view). · **Pendências:** nenhuma. · **Verificação:** `tests/endpoints` verde (p25 captura `outbound_router.mark_read` — Telegram não chama GOWA, GOWA segue enviando).

---

### Fase F4 — Permissão de grupo por capability (🟢)
**Objetivo:** não chamar `can_bot_send_in_group` do GOWA para grupo de outro provider.
**Itens:**
- [paralelo] Em `get_contact`/`_load` ([contacts.py:626-635](../server/routes/contacts.py#L626)): antes do `gowa_client.can_bot_send_in_group`, resolver o canal do contato (via `data["channel_id"]`/`_channel_for`) e checar `outbound.supports(channel_id, "groups")`. Se não suporta, **pular** (mantém `can_send` como está). Manter GOWA idêntico.
- ⚠️ Decisão P2: `can_bot_send_in_group` é específico do GOWA (não há hook genérico). Opção mínima = gate por capability + só chamar quando o canal é GOWA-capaz; opção completa = hook `can_send_in_group` na base (default `True`). Recomendação: **gate por capability agora**, hook depois se outro provider precisar (YAGNI).

**Pronto quando:** contato de grupo Telegram não dispara chamada GOWA; grupo GOWA inalterado.

#### Status de execução — Fase F4
**Estado:** ✅ Concluída
- **O que foi feito:** o check `gowa_client.can_bot_send_in_group` (abrir-conversa) agora só roda quando `outbound.supports(group_channel, "groups")`, com `group_channel = data["channel_id"] or "default"`. · **Decisões:** gate por capability (YAGNI, P2 decidido) — sem hook novo; grupo Telegram/Cloud (`groups=False`) é pulado, `can_send` fica como está. · **Pendências:** nenhuma. · **Verificação:** GOWA (default supports groups) inalterado; sem `if provider ==` novo.

---

### Fase F5 — Hook `fetch_avatar` + roteamento por canal (🔴, bloqueia F6)
**Objetivo:** avatar deixa de ser chamada GOWA fixa; passa pelo registry do canal.
**Itens:**
- [sequencial] `channels/base.py`: método `fetch_avatar(self, chat_id: str) -> bytes | None` default `return None` ([base.py](../channels/base.py) perto dos outros hooks opcionais). Não precisa de flag em `ChannelCapabilities` — o `None` já é o gate.
- [sequencial] `channels/providers/gowa_channel.py`: implementar `fetch_avatar` chamando o `gowa_client.get_avatar` do device ([gowa_channel.py](../channels/providers/gowa_channel.py)).
- [sequencial] `server/avatars.py:refresh_avatar`: receber `channel_id` e trocar `deps.gowa_client.get_avatar(phone)` por `registry.get(channel_id).fetch_avatar(chat_id)` (via um `OutboundRouter`/registry helper). Se o canal não existe/retorna `None` → mantém cache (comportamento atual). ([avatars.py:47](../server/avatars.py#L47))
- [sequencial] Call sites on-demand ([contacts.py:1697](../server/routes/contacts.py#L1697)) e abrir-conversa ([contacts.py:641](../server/routes/contacts.py#L641)): passar o `channel_id` do contato/conversa (`data["channel_id"]`/`_channel_for`).
- ⚠️ Ver P1 (contato compartilhado por vários canais × avatar keyed por phone).

**Pronto quando:** avatar de contato GOWA continua atualizando; um contato **exclusivamente** Telegram/Cloud não gera chamada `gowa_client.get_avatar`.

#### Status de execução — Fase F5
**Estado:** ✅ Concluída
- **O que foi feito:** hook `Channel.fetch_avatar(chat_id) -> bytes | None` (default `None`) em `base.py`; impl GOWA em `gowa_channel.py` (via `get_avatar` device-scoped); `OutboundRouter.fetch_avatar(channel_id, chat_id)`; `avatars.refresh_avatar/refresh_and_broadcast` recebem `channel_id` e roteiam por `deps.outbound_router.fetch_avatar`. Call sites: abrir-conversa e endpoint on-demand `/avatar` (com query `conversation_id`/`channel_id` → `_channel_for`). · **Decisões (P1):** avatar keyed por phone resolve pelo canal da conversa (a); on-demand cai em "default" quando o painel não informa canal. · **Pendências:** nenhuma. · **Verificação:** `None` é o gate — contato só-Telegram/Cloud nunca chama `gowa_client.get_avatar` (204).

---

### Fase F6 — Sweep de avatar por-canal (🔴, depende de F5)
**Objetivo:** o loop de fundo não fica refém da conexão do GOWA e não chama GOWA para contato de outro provider.
**Itens:**
- [sequencial] `server/background.py:avatar_fetch_task` ([:187-232](../server/background.py#L187)): para cada contato, resolver o canal (via conversa aberta mais recente → `conversation_repo.get_with_channel`/join inbox→channel) e chamar `refresh_and_broadcast(deps, phone, channel_id)`. Contatos sem canal resolvível → pular.
- [sequencial] A trava inicial `while not state.connected` ([:203](../server/background.py#L203)) deixa de bloquear o sweep inteiro: manter o warm-up do GOWA mas não impedir refresh de contatos não-GOWA (ou reordenar para não travar). ⚠️ **Cuidado** para não criar busy-loop; preservar o `AVATAR_REFRESH_INTERVAL` e o rate-limit `sleep(0.5)`.
- ⚠️ Ver P1: se a resolução por-canal do sweep ficar cara/ambígua, aceitar a versão mínima (avatar só para contatos com conversa num canal que implementa `fetch_avatar`).

**Pronto quando:** avatar de contatos Telegram/Cloud atualiza mesmo com GOWA desconectado (ou é limpo skip); GOWA inalterado; sem chamada GOWA para id de outro provider.

#### Status de execução — Fase F6
**Estado:** ✅ Concluída
- **O que foi feito:** novo `conversation_repo.channel_id_for_contact(contact_id)` (canal da conversa mais recente, via `get_latest_for_contact` + `get_with_channel`). `avatar_fetch_task` resolve o canal por contato e chama `refresh_and_broadcast(deps, phone, channel_id)`; contatos sem conversa são pulados. · **Decisões:** a trava `while not state.connected` virou um warm-up de 8s (não bloqueia o sweep inteiro na conexão GOWA); `AVATAR_REFRESH_INTERVAL` + `sleep(0.5)` preservados (sem busy-loop). · **Pendências:** nenhuma. · **Verificação:** sweep não fica refém do GOWA; contato não-GOWA atualiza via seu provider, GOWA no-op enquanto desconectado.

---

### Fase F7 — Prompt de grupo agnóstico (🟢)
**Objetivo:** o bloco de contexto de grupo não assume WhatsApp nem chama GOWA para grupo de outro provider.
**Itens:**
- [paralelo] `agent/prompt_builder.py:44-54`: trocar "conversa de grupo do **WhatsApp**" por texto provider-neutro ("conversa de grupo"). ([prompt_builder.py:46](../agent/prompt_builder.py#L46))
- [paralelo] Gate do `group_mentions.get_members(contact.phone)` por capability do canal do contato (`outbound.supports(channel_id, "groups")`) e/ou pelo provider ser o que o `group_mentions` conhece (GOWA). Sem capability/serviço → membros vazios (já é o fallback atual do try/except). ([prompt_builder.py:53-55](../agent/prompt_builder.py#L53))
- ⚠️ `prompt_builder` recebe `contact`; confirmar que dá para obter o `channel_id` ali (o `ContactMemory` carrega `self.channel_id`, [memory.py](../agent/memory.py)). Se `build` não recebe o canal, threadar a partir do handler que já o tem.

**Pronto quando:** grupo Telegram: prompt não diz "WhatsApp" e não dispara `get_group_info` GOWA; grupo GOWA mantém os hints de @menção.

#### Status de execução — Fase F7
**Estado:** ✅ Concluída
- **O que foi feito:** o bloco de grupo agora diz "conversa de grupo" (sem "do WhatsApp"). `group_mentions.get_members` só é chamado quando `_channel_supports_groups(contact.channel_id)` — helper que resolve capability via `get_channel_runtime()` (`outbound.supports(cid, "groups")`). · **Decisões:** fail-open a `True` quando o runtime não está wired (legado/testes) ou o canal é desconhecido — preserva GOWA; só um canal que declara `groups=False` (Telegram/Cloud) é gated out. Sem `if provider ==`. · **Pendências:** nenhuma. · **Verificação:** grupo Telegram: prompt sem "WhatsApp" e sem `get_group_info` GOWA; grupo GOWA mantém os hints de @menção.

---

### Fase F8 — `_jid`/`source_id` provider-aware *(opcional, 🟢)*
**Objetivo:** parar de estampar JID `@s.whatsapp.net` em conversa de outro provider (cosmético/data-integrity).
**Itens:**
- [paralelo] `agent/memory.py:_jid` ([:166-169](../agent/memory.py#L166)): derivar sufixo/forma do `source_id` conforme o provider do canal (ou guardar o chat_id cru para não-GOWA). **Baixo valor** — não vai à wire. Pode ser adiado (P3).

**Pronto quando:** decidido em P3 — implementado ou explicitamente adiado com nota.

#### Status de execução — Fase F8
**Estado:** ⏸️ Adiada (P3, conforme recomendação do plano)
- **O que foi feito:** nada — `_jid`/`source_id` provider-aware é cosmético (não vai à wire, não misroteia). · **Decisões:** adiado até aparecer um bug real de identidade de conversa. · **Pendências:** item opcional. · **Verificação:** N/A.

---

### Fase F-REG — Regressão + checklist (🔴)
**Objetivo:** travar o comportamento novo e garantir zero regressão GOWA.
**Itens:**
- Rodar suíte completa no Postgres; `grep` final por `gowa_client.` em `server/routes/contacts.py`, `server/avatars.py`, `agent/prompt_builder.py` — restam só usos legítimos (ou nenhum).
- Testar manualmente os 3 providers (GOWA, Telegram, Cloud): recepção → IA responde a contato novo (Problema 1); abrir conversa não gera chamada GOWA cruzada (Problema 2).

#### Status de execução — Fase F-REG
**Estado:** ✅ Concluída
- **O que foi feito:** `grep gowa_client.` em `avatars.py` (só comentário), `prompt_builder.py` (nenhum), `contacts.py` (`can_bot_send_in_group` agora gated; `check_phone`/`delete_message` fora do escopo do plano, legítimos). Nenhum `if provider == "gowa"` novo. · **Decisões:** validação de regressão por suítes isoladas (o processo compartilha um engine global → rodar tudo junto contamina; a suíte `characterization` tem flakiness intra-arquivo PRÉ-existente, confirmada com as mudanças stashed). · **Pendências:** P4 (religar conversas Telegram antigas com `ai_active=0`) a decidir com o usuário — não automatizado. · **Verificação:** `tests/endpoints` verde; `tests/test_seed_ai_active_per_channel` 5/5; `tests/test_multichannel_routing`, `test_human_gate`, `test_conversation_race` verdes; characterization (webhook 26/26, lifecycle, agent_turn, audit por-teste) verde em isolamento.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Seed `ai_active` (F1) | Ligar IA onde o operador **queria** desligada | O seed só reflete o toggle **do canal** que o operador marcou; global continua sendo o fallback. Reopen não re-semeia (preserva pausa manual). D5 fail-open só liga, nunca desliga onde já funcionava. |
| Param novo em `resolve_for_contact_ex` (F1) | Quebrar outros callers | Default `None` = comportamento atual (global). Auditar callers de `resolve_for_contact_ex`/`resolve_for_contact` (grep) — só o de `ContactMemory` passa o seed. |
| Sweep de avatar (F6) | Busy-loop ou remoção da trava GOWA quebrar warm-up | Preservar `AVATAR_REFRESH_INTERVAL` + `sleep(0.5)`; não remover o warm-up, só não bloquear contatos não-GOWA nele. |
| Avatar keyed por phone × contato multicanal (F5/F6) | Um phone em 2 canais: qual provider busca a foto? | P1 — resolver pelo canal da conversa mais recente; se ambíguo, versão mínima (só canais com `fetch_avatar`). |
| `prompt_builder` sem `channel_id` em mãos (F7) | Não dá para gate por capability | Threadar `channel_id` do handler (que já o tem); se custoso, gate por `group_mentions` ter o serviço GOWA inicializado. |
| Postgres (único backend) | Índice `uq_atend_open_contact_inbox` na race de create | Inalterado — F1 não muda a lógica de race, só o valor semeado. |
| Regressão de evento/filtro | `message.saved`/`ai_takeover` dependem de `ai_active` | F2 cobre; `ai_takeover` é 1×/conversa via dedupe — inalterado. |
| Segredos | — | Nenhuma mudança em URL/logs de credencial. |

---

## 7. Perguntas em aberto

- **P1 — Avatar de contato compartilhado por vários canais.** Contato é keyed por `phone` sem `channel_id`; avatar é cache por `phone`. Qual canal busca a foto? (a) o da conversa **aberta mais recente** (join inbox→channel); (b) preferir GOWA quando existir; (c) versão mínima — só buscar para contatos cuja conversa está num canal que implementa `fetch_avatar`. **Recomendação:** (a) com fallback (c). ⏸️ **A DECIDIR** antes de F6.
- **P2 — `can_bot_send_in_group` genérico?** Criar hook de canal `can_send_in_group` (default `True`) ou só gate por `caps.groups`? **Recomendação:** gate por capability agora (YAGNI); hook quando um 2º provider de grupo pedir. ✅ **DECIDIDO** (gate por capability, F4).
- **P3 — `_jid`/`source_id` provider-aware vale o esforço?** É cosmético (não vai à wire). **Recomendação:** ⏸️ **ADIADO** (F8 opcional) salvo se aparecer bug de identidade de conversa.
- **P4 — Migrar dados existentes?** Conversas Telegram já criadas com `ai_active=0` por causa do bug continuam mudas até serem reabertas/toggladas. Rodar um one-off para religar? **Recomendação:** não automatizar; documentar que conversas antigas precisam de toggle manual (ou um endpoint admin opcional). ⏸️ **A DECIDIR** com o usuário.

---

## 8. Checklist de verificação

- [x] `tests/endpoints` verde no Postgres (`WHATSBOT_TEST_DB_URL`).
- [x] Matriz do seed (F2) verde: (global OFF × canal ON) ⇒ `ai_active=1`.
- [ ] Contato novo em canal Telegram com toggle ON recebe resposta da IA (validação manual ponta-a-ponta) — **pendente de teste manual do usuário** (coberto por teste automatizado do seed).
- [x] GOWA inalterado: contato novo GOWA responde como antes; avatar/read-receipt/permissão de grupo GOWA seguem funcionando (defaults "default"/capability preservam o GOWA).
- [x] Abrir/mark-read/abrir-grupo de conversa Telegram/Cloud **não** chama `gowa_client` (roteado por canal/capability).
- [x] Avatar de contato não-GOWA atualiza (ou skip limpo) mesmo com GOWA desconectado (sweep não trava na conexão GOWA).
- [x] Prompt de grupo Telegram não contém "WhatsApp" e não dispara `get_group_info` GOWA (gate por capability).
- [x] `grep gowa_client.` em `contacts.py`/`avatars.py`/`prompt_builder.py` — só usos legítimos/roteados.
- [x] Sem `if provider == "gowa"` novo introduzido onde capability/registry resolve (D4).
- [x] Migration: **N/A** (sem schema change).
- [x] Restart de plugin não afetado (telegram/whatsapp_cloud continuam importáveis).

---

## 9. Apêndice — arquivos-chave

**Problema 1 (seed IA):**
- `db/repositories/conversation_repo.py` — `resolve_for_contact_ex` (:245), `_create_open_atomic` (:129), `_insert_conversation` (:83), `_default_ai_enabled` (:69).
- `agent/memory.py` — `_resolve_conversation` (:171), `ensure_conversation_live` (:321), `_default_ai_enabled` (:94).
- `agent/handler.py` — `_get_contact` (:241-253).
- `channels/ai_settings.py` — `value` (:77), `PER_CHANNEL_AI_KEYS` (:27).
- `app/services/messaging_service.py` — `_conversation_ai_active` (:1161), `_channel_ai_enabled` (:176).

**Problema 2 (acoplamento GOWA):**
- `server/avatars.py` — `refresh_avatar` (:36-61).
- `server/background.py` — `avatar_fetch_task` (:187-232).
- `server/routes/contacts.py` — `_send_read_receipts` (:226), `_channel_for` (:130), abrir conversa (:600-644), `can_bot_send_in_group` (:626), endpoint avatar (:1682-1706), mark-read (:1583-1590), presence (precedente, :1580).
- `agent/prompt_builder.py` — bloco de grupo (:42-60).
- `channels/base.py` — `ChannelCapabilities` (:19), hooks opcionais.
- `channels/providers/gowa_channel.py` — impl `fetch_avatar`.
- `channels/outbound.py` — `OutboundRouter.mark_read` (:186), `supports` (:45).

**Referência de padrão:** `docs-planos/37-plano-correcao-roteamento-por-canal-nao-contato.md` (irmão — infra por-canal).
