# Plano 32 — Prevenção de canais/caixas duplicados na origem (contrato genérico de identidade de conta)

> **Status:** ✅ EXECUTADO (Lane A, branch `feat/plano-32`, 8 commits F1–F8) · **Data:** 2026-07-06 · **Escopo:** médio (1 fix no client GOWA · 1 contrato `AccountIdentity` + motor de dedup genérico no core · 1 migration: 2 colunas + índice único · persist de `own_phone`/`account_identity` via sweep · 3 implementações finas dos ganchos nos providers · enforcement create/update/login · UI + docs do contrato)
> **Origem:** pedido do usuário — "no GOWA consigo conectar dois números iguais via QR; quero uma lógica que identifica e aponta a caixa duplicada" → refinado para **PREVENÇÃO na origem** (bloquear/rejeitar) com uma arquitetura **genérica no core, específica no plugin** (o usuário vai ter mais canais: Instagram, Messenger, widget de site…). **Método:** leitura + `grep` + investigação multiagente adversarial — todas as afirmações vêm com `arquivo:linha` verificado nesta sessão.
> **O quê/por quê:** hoje nada impede duas linhas em `channels` apontarem para a **mesma conta do mesmo provider**. A solução não é um `if` por provider: cada **provider (plugin) declara sua própria identidade de conta** via um contrato estável; o **core** faz toda a mecânica genérica de dedup (comparação, storage, índice único, enforcement, UI). Adicionar um canal novo no futuro = implementar 1–2 métodos finos; o core não muda.
>
> **Como usar este plano:** ao executar cada fase, preencha o bloco **"Status de execução"** dela ANTES de passar para a próxima. **Verde a cada fase**; **caracterização ANTES** de mexer no roteamento inbound (F1/F4, `get_gowa_channel_for_device` é P13); **um refactor por commit**.

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** ✅ | **Bloquear/rejeitar** a duplicata (não só avisar). | 409 no create/update; recusa do login no GOWA. |
| **D2** ✅ | **Persistir `channels.own_phone`** (hoje coluna morta) **COM** fix do `get_own_number` device-scoped. | F1 (fix) + F4 (sweep grava `own_phone`/`account_identity`/`connected`/`logged_in`). Efeito bom: destrava o roteamento `by_phone`. |
| **D3** ✅ | Cobrir os providers chaveando em **`(provider, identidade)`**. Mesmo número em providers **diferentes** NÃO é duplicata (plano 11 D1/D2). | Motor de dedup escopa ao mesmo provider. |
| **D4** ✅ | Foco = **caixa/canal**, NÃO IA/conversa. | Nada muda no pipeline de mensagens/IA (§7 P4). |
| **D5** ✅ (2026-07-06) | **Arquitetura genérica via contrato** (capability-style, igual `required_credentials`): o **plugin declara a identidade**, o **core faz o dedup**. Storage genérico: coluna `channels.account_identity` (+ `account_identity_kind`) + **um** índice único `(provider, account_identity)`. | F2 cria o contrato+motor no core; F5 são implementações finas nos providers. Novo provider ⇒ core intacto. |
| **D6** ✅ (2026-07-06) | Guardar o **`kind`** da identidade (`phone`/`phone_number_id`/`bot_id`/…). Comparar só `kind` igual. | `account_identity_kind` na tabela; motor nunca compara `phone` com `@lid`. |
| **D7** ✅ (2026-07-06) | Recusa pós-conexão = **ação genérica com default `logout()`/`stop()`** do provider; plugin pode sobrescrever. | GOWA desloga o device recém-pareado; contrato prevê override. |
| **D8** ✅ (2026-07-06) | Cross-provider (mesma conta em providers diferentes) = **canais separados** (YAGNI cross-provider). | Dedup sempre dentro do mesmo provider. |
| **Princípio** | Não quebrar `get_gowa_channel_for_device` (P13) nem o multi-canal legítimo (plano 11). | Índice/guard aditivos; roteamento intacto. |

---

## 1. Resumo executivo

Cada canal é uma "box" de um provider com uma **identidade de conta**. Não há **nenhuma** unicidade sobre ela ([db/tables.py:218-248](../db/tables.py)). O único que sabe **qual campo** identifica a conta, **quando** ele é conhecível e **como** normalizar é o provider — então isso vira um **contrato** que o plugin implementa (espelhando o padrão `required_credentials`, [channels/base.py:40](../channels/base.py)), e o **core** faz o resto genericamente.

O contrato tem **dois ganchos**, porque os providers se dividem por **quando** a identidade aparece: no **create** (está na credencial — Cloud `phone_number_id`, Telegram `bot_token`) ou só **pós-conexão** (aparece no login — GOWA `own_phone` pós-QR). O core tenta os dois e age quando vier não-`None`: 409 no create/update; recusa (logout) no login. Um **índice único parcial** `(provider, account_identity)` (Postgres) é o cinto de segurança, junto com a **persistência** de `own_phone`/`account_identity` (hoje inexistente), que também conserta o `by_phone` de roteamento.

---

## 2. Como funciona hoje (mapa) — `arquivo:linha` verificado

### 2.1 Schema — zero unicidade de identidade
- `channels` ([db/tables.py:218-238](../db/tables.py)): `id`(PK), `provider`, `gowa_device_id`, `own_phone`, `connected`, `logged_in`, `enabled`, `archived`, `config`, `last_error`. **Nenhum** unique/index em `provider`/`gowa_device_id`/`own_phone`.
- `channel_credentials` ([db/tables.py:240-248](../db/tables.py)): só `UniqueConstraint(channel_id,key)`. Valor **plaintext** (P15), mascarado só na borda ([channel_service.py:53-57](../app/services/channel_service.py)).

### 2.2 ⚠️ `own_phone`/`connected`/`logged_in` são colunas **mortas** em produção
- `registry.set_status` ([channels/registry.py:69](../channels/registry.py)) **sem chamador**; `channel_repo.set_status(own_phone=…)` só em testes. Status é lido **AO VIVO por card a cada 8s** ([ChannelsManager.js:194-214](../web/static/js/components/ChannelsManager.js)) via `GET /api/channels/{id}/status` → [channel_service.py:226-253](../app/services/channel_service.py) → `GOWAChannel.status()` ([gowa_channel.py:51-75](../channels/providers/gowa_channel.py)) → `get_own_number()`.
- ⚠️ Docstrings em [channel_repo.py:97,156](../db/repositories/channel_repo.py) ("set by the status poll") **FALSOS/stale**.

### 2.3 ⚠️ `get_own_number` best-effort e **pode devolver o número de OUTRO canal**
- [gowa/client.py:234-270](../gowa/client.py): tenta `/app/status` (device-scoped) e, se não achar JID, cai num **fallback `/devices` GLOBAL não-ordenado** ([client.py:167-172](../gowa/client.py)) → retorna o **primeiro** device com JID (misattribution). Também retorna `""` (desconhecido) mesmo logado.

### 2.4 Identidade por provider (o insumo do contrato)
| Provider | Identidade | De onde | Conhecível quando | Gancho |
|---|---|---|---|---|
| **gowa** | dígitos de `own_phone` | `get_own_number()` ([gowa_channel.py:67-70](../channels/providers/gowa_channel.py)) | pós-login | `account_identity()` (live) |
| **whatsapp_cloud** | `phone_number_id` (cred) | `_phone_number_id` ([whatsapp_cloud/channels.py:90-91](../storages/plugins/whatsapp_cloud/channels.py)) | no create ✅ | `identity_from_credentials()` |
| **telegram** | `bot_id` via `getMe`; `bot_token` (cred) | `status()`→`getMe` ([telegram/channels.py:110-119](../storages/plugins/telegram/channels.py)); `_cred("bot_token")` (:83) | token no create; `bot_id` pós-`getMe` | `identity_from_credentials()` (+ live opcional) |
| **test** | — | — | — | nenhum (ganchos → `None`) |

### 2.5 Precedente do contrato: `required_credentials` (a imitar)
- `ChannelCapabilities.required_credentials` ([channels/base.py:33-40](../channels/base.py)) é lido **genericamente** pelo core para validar no create ([channel_service.py:81-101](../app/services/channel_service.py)), sondando uma instância — **nunca** por nome de provider. O contrato de identidade segue a MESMA forma.

### 2.6 Create/update — nenhuma checagem de identidade
- Create ([channels.py:229-274](../server/routes/channels.py)): valida só provider allow-list, `required_credentials`, formato/colisão de `id`. Service ([channel_service.py:339-405](../app/services/channel_service.py)): grava creds **verbatim** (sem trim); `update` ([:400-403](../app/services/channel_service.py)) permite **editar credencial para colidir DEPOIS** e ignora placeholder `••••`.

### 2.7 Roteamento inbound (não quebrar — P13)
- `_device_map` ([channel_repo.py:123-141](../db/repositories/channel_repo.py)): `by_dev` (session_id/device_id) e `by_phone` (`digits(own_phone)`, **vazio hoje**). `get_gowa_channel_for_device` ([:150-172](../db/repositories/channel_repo.py)) prefere `by_dev`; fallback `by_phone` usa `endswith/startswith` ([:168-171](../db/repositories/channel_repo.py)) — **não** reusar como igualdade de dedup. Usado em [channel_webhook.py:311-328](../server/routes/channel_webhook.py).

### 2.8 Normalização (reusar)
- `br_phone_variants` ([channels/br_phone.py:12-27](../channels/br_phone.py)): BR 12↔13 dígitos (9 extra). O provider normaliza pra **forma canônica única** → core compara com **igualdade exata**. Cuidado `@lid`≠`@s.whatsapp.net` ([channels/jid.py](../channels/jid.py)).

### 2.9 UI dos cards
- [ChannelCard.js:53-55](../web/static/js/components/channels/ChannelCard.js): renderiza `📱 own_phone` (branco hoje). Pattern a reusar: banner **âmbar** ([:68-75](../web/static/js/components/channels/ChannelCard.js)) e **erro vermelho** ([:77-81](../web/static/js/components/channels/ChannelCard.js)) — `wa-*`, dark-safe.

### 2.10 Backstop de banco — template
- Índice único parcial: espelhar [20260702_0035_single_router_index.py](../db/alembic/versions/20260702_0035_single_router_index.py) (`create_index(..., unique=True, postgresql_where=...)`, guardado/idempotente/reversível). **Head Alembic atual = `20260703_0037_drop_ai_variables_category`** — encadear do head corrente (confirmar com `grep`).

---

## 3. Inventário / análise

| Item | Onde | O que falta | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|
| I1 · `get_own_number` device-scoped | [gowa/client.py:234-270](../gowa/client.py) | fallback global | filtrar entry cujo device == `self.device_id`; preferir `@s.whatsapp.net` sobre `@lid`; `""` se não achar deste device | médio | S |
| I2 · contrato `AccountIdentity` (2 ganchos) | novo em [channels/base.py](../channels/base.py) | não existe | value object `AccountIdentity(kind,value)` + `identity_from_credentials(creds)` (classmethod, default `None`) + `account_identity()` (instância, default `None`) + `reject_duplicate()` (default `logout()`/`stop()`) | baixo | S |
| I3 · motor de dedup genérico | novo `channels/dedup.py` | não existe | `find_conflict(provider, identity, exclude_id)` (scan enabled+non-archived, mesmo provider, lê `account_identity` das rows; igualdade exata; exclui NULL/`""`) | baixo | M |
| I4 · colunas + índice único | nova migration (head 0037→novo) | não existe | `channels.account_identity` + `account_identity_kind`; `UNIQUE(provider,account_identity) WHERE enabled=1 AND archived=0 AND account_identity IS NOT NULL AND account_identity<>''` | baixo | S |
| I5 · persist + sweep + refuse (core) | novo loop + [channel_repo](../db/repositories/channel_repo.py) + [channel_service](../app/services/channel_service.py) | writer inexistente | sweep por canal grava `own_phone`/`account_identity`/status quando muda; ao gravar identidade → `find_conflict` → se colidir, `inst.reject_duplicate()` + `last_error`; `IntegrityError` = mesma via | médio | M |
| I6 · enforcement create/update (core) | [channels.py:229-284](../server/routes/channels.py) + [channel_service.py:339-405](../app/services/channel_service.py) | nenhuma checagem | genérico: chamar `provider.identity_from_credentials(creds)`; se não-`None` → `find_conflict` → **409** antes de persistir; senão grava `account_identity` | médio | M |
| I7 · ganchos nos providers (finos) | [gowa_channel.py](../channels/providers/gowa_channel.py), [whatsapp_cloud/channels.py](../storages/plugins/whatsapp_cloud/channels.py), [telegram/channels.py](../storages/plugins/telegram/channels.py) | não existem | gowa: `account_identity()`←own_phone (kind `phone`, canônico BR); cloud: `identity_from_credentials`←`phone_number_id`; telegram: `identity_from_credentials`←`bot_token` (+ `account_identity`←`getMe` bot_id opcional) | baixo | M |
| I8 · UI de erro (genérica) | [ChannelCard.js](../web/static/js/components/channels/ChannelCard.js), [ChannelsManager.js](../web/static/js/components/ChannelsManager.js) | 409/erro não tratado | 409 no form; card com banner "esta conta já está no canal X" | baixo | M |
| I9 · docs do contrato | [channels/base.py](../channels/base.py) docstrings + **CLAUDE.md** | não existe | docstrings dos ganchos (kind/canônico/quando `None`/exemplo) + seção "Contrato de identidade de conta (dedup)" no CLAUDE.md | baixo | S |
| I10 · testes | [tests/test_endpoints.py](../tests/test_endpoints.py) + unit | zero | 409 create/update dup; dedup via `set_status`+índice; unit do motor I3 | baixo | M |

### Falsos positivos descartados
| Candidato | Por que NÃO |
|---|---|
| Banner "zombie-channel" ([ChannelCard.js:25-32](../web/static/js/components/channels/ChannelCard.js)) | É **credencial faltando**, não duplicata. Só reusar o pattern visual. |
| `endswith` do roteamento ([channel_repo.py:168-171](../db/repositories/channel_repo.py)) | Heurística single-winner tolerante; over-match como igualdade (`''.endswith('')`). Nunca em dedup. |
| `gowa_device_id` como chave | `uuid`-único → 2 devices do mesmo número têm ids diferentes; nunca detecta. |
| `own_phone` atual como fonte | Coluna morta + `get_own_number` best-effort → só serve após I1+I5. |
| Um `if provider ==` no core | Proibido pela convenção; o contrato (I2) elimina a necessidade. |

---

## 4. Mudanças de infraestrutura (por camada)

- **Backend/GOWA client:** I1.
- **Backend/contrato (channels — path estável):** I2 (`channels/base.py`), I3 (`channels/dedup.py`).
- **Backend/service+loop:** I5, I6.
- **Plugins/providers:** I7 (ganchos finos).
- **DB:** I4 (colunas + índice, encadear do head 0037).
- **Frontend:** I8.
- **Docs:** I9.
- **Testes:** I10.

---

## 5. Fases / Roadmap

```
WAVE 0  (habilitadores, 3 em paralelo)
   F1(get_own_number device-scoped)  ·  F2(contrato AccountIdentity + motor dedup)  ·  F3(migration: colunas + índice)
        └──────────── barreira: F1+F2+F3 alimentam F4/F5 ────────────┘

WAVE 1  (implementação, 3 em paralelo)
   F4(core: persist/sweep + refuse + enforcement create/update)   ·   F5(ganchos finos nos providers)   ·   F6(docs do contrato)
        │  [F4 depende F2,F3,F1]        [F5 depende F2]              [F6 depende F2]
        └──────────────── barreira: F4+F5 ────────────────┘

WAVE 2
   F7(UI genérica de erro)  ·  F8(testes)      ← ambos dependem de F4+F5
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | F1 | GOWA client | 🟢 | médio | `get_own_number` só retorna o número **deste** device |
| 0 | F2 | contrato core | 🟢 | baixo | `AccountIdentity` + 2 ganchos + `find_conflict` unit-verde |
| 0 | F3 | DB migration | 🟢 | baixo | colunas + índice; round-trip verde no Postgres de teste |
| 1 | F4 | core enforcement | 🟢 [dep F1,F2,F3] | médio | create/login duplicado é bloqueado (genérico, sem `if provider`) |
| 1 | F5 | providers | 🟢 [dep F2] | baixo | gowa/cloud/telegram implementam os ganchos |
| 1 | F6 | docs | 🟢 [dep F2] | baixo | docstrings + seção CLAUDE.md revisadas |
| 2 | F7 | frontend | 🟢 [dep F4,F5] | baixo | erro visível no form e no card |
| 2 | F8 | testes | 🟢 [dep F4,F5] | baixo | `tests/` verde no Postgres |

---

### Fase F1 — GOWA client: `get_own_number` device-scoped
**Objetivo:** a identidade GOWA é sempre **deste** device.
**Itens:**
- [sequencial] No fallback `/devices` ([gowa/client.py:259-268](../gowa/client.py)): filtrar o item cujo device id == `self.device_id` (confirmar o campo no payload real de `/devices`) em vez do primeiro; se não houver, retornar `""`.
- [sequencial] Preferir JID `@s.whatsapp.net` sobre `@lid`.
- [paralelo] Corrigir docstrings falsos em [channel_repo.py:97,156](../db/repositories/channel_repo.py).
**Pronto quando:** unit com `/devices` mockado (2 devices) prova que `client(device_A)` nunca devolve o número de B; sem match → `""`.

#### Status de execução — Fase F1
**Estado:** ✅ Concluída (commit F1)
- **O que foi feito:** `get_own_number` device-scoped em [gowa/client.py](../gowa/client.py). O fallback `/devices` agora só lê o item cujo id registrado casa com `self.device_id` (candidatos `id`/`name`/`device` — mesma extração do `ensure_device`); sem match → `""`. Preferência `@s.whatsapp.net` sobre `@lid` nas DUAS sondas (`/app/status` e `/devices`). Docstrings falsos ("set by the status poll") corrigidos em [channel_repo.py](../db/repositories/channel_repo.py).
- **Como foi feito / decisões:** o payload real do `/devices` não pôde ser confirmado ao vivo (sem GOWA nesta sessão), então o match é **defensivo** — casa `self.device_id` contra o conjunto de campos-id (`id`/`name`/`device`), e extrai o JID dos campos JID-ish preferindo `@s.whatsapp.net`. Quando `device_id` é vazio (singleton legado) mantém o comportamento antigo (adota o 1º).
- **Problemas / pendências:** confirmar o nome exato do campo no payload `/devices` de uma instância GOWA viva (baixo risco — o match cobre as 3 variações).
- **Verificação:** `tests/test_gowa_own_number.py` (6 testes) — 2 devices mockados provam que `client(A)` nunca devolve o número de B; sem match → `""`; `@s.whatsapp.net` vence `@lid`; `:device` suffix stripado.

---

### Fase F2 — Contrato `AccountIdentity` + motor de dedup genérico (core)
**Objetivo:** o core sabe comparar identidades sem conhecer nenhum provider.
**Itens:**
- [sequencial] Em [channels/base.py](../channels/base.py): `@dataclass(frozen=True) class AccountIdentity: kind: str; value: str` (value já canônico, não-vazio). Três ganchos no `Channel` (todos default no-op → não quebram provider/test existente):
  - `@classmethod identity_from_credentials(cls, creds: dict) -> AccountIdentity | None` (default `None`).
  - `def account_identity(self) -> AccountIdentity | None` (default `None`).
  - `def reject_duplicate(self) -> None` (default: `self.logout()` se existir, senão `self.stop()`).
- [sequencial] Docstrings **normativos** nos ganchos (parte de F6): o que é `kind`, retornar **canônico**, `None` = desconhecido, exemplo por provider.
- [sequencial] `channels/dedup.py`: `same(a: AccountIdentity, b) -> bool` (`kind` igual **e** `value` igual; nunca com `None`/`""`); `find_conflict(provider, identity, *, exclude_channel_id) -> str|None` — varre `channel_repo.list_all()` (enabled=1 AND archived=0, mesmo provider), lê `account_identity`/`account_identity_kind` das rows, retorna o `id` do 1º conflito. Puro de rede (só DB).
**Pronto quando:** unit cobre: NULL/`""` nunca casa; provider diferente nunca casa; `kind` diferente nunca casa; canônicos iguais casam; `exclude_channel_id` ignora a própria row.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída (commit F2)
- **O que foi feito:** `AccountIdentity(kind, value)` (frozen dataclass) + 3 ganchos default no-op no `Channel` ([channels/base.py](../channels/base.py)): `identity_from_credentials` (classmethod), `account_identity` (instância), `reject_duplicate` (default `logout()`→`stop()`). Motor `channels/dedup.py`: `same(a,b)` + `find_conflict(provider, identity, exclude_channel_id)`.
- **Como foi feito / decisões:** ganchos com docstrings normativos (já cobre F6). `find_conflict` importa `channel_repo` lazy (evita ciclo). `same` nunca casa `None`/`""`/kind-diferente. Value canônico BR decidido em F5 (não aqui — o core não normaliza).
- **Problemas / pendências:** nenhuma.
- **Verificação:** `tests/test_channel_dedup.py` (10 testes) — NULL/""/None/provider-diferente/kind-diferente nunca casam; canônicos iguais casam; `exclude_channel_id` ignora a própria row.

---

### Fase F3 — Migration: colunas `account_identity` + índice único parcial
**Objetivo:** storage genérico + cinto de segurança de banco, uniforme pros providers.
**Itens:**
- [sequencial] Nova migration encadeada do head `0037` (confirmar com `grep -rn "down_revision" db/alembic/versions`). Espelhar [0035](../db/alembic/versions/20260702_0035_single_router_index.py) (guardado/idempotente/reversível).
- [sequencial] `add_column channels.account_identity (Text, nullable)` + `account_identity_kind (Text, nullable)`.
- [sequencial] `create_index("ux_channels_account_identity", "channels", ["provider","account_identity"], unique=True, postgresql_where=sa.text("enabled=1 AND archived=0 AND account_identity IS NOT NULL AND account_identity <> ''"))`. Hoje todas as rows têm `account_identity` NULL → nenhuma violação pré-existente (cria limpo).
- [sequencial] `downgrade` = drop index + drop columns.
- [paralelo] Adicionar as colunas ao [db/tables.py](../db/tables.py) `channels` Table e ao `_STATUS_FIELDS` de [channel_repo.py:19-21](../db/repositories/channel_repo.py).
**Pronto quando:** `upgrade`/`downgrade` round-trip verde no `WHATSBOT_TEST_DB_URL`; 2 rows mesmo `(provider,account_identity)` enabled → `IntegrityError`; NULL/`""`/archived/disabled não violam.

#### Status de execução — Fase F3
**Estado:** ✅ Concluída (commit F3)
- **O que foi feito:** migration `20260706_0038_channels_account_identity.py` (encadeada do head `0037_drop_ai_variables_category`) adiciona `channels.account_identity` + `account_identity_kind` + índice único parcial `ux_channels_account_identity (provider, account_identity) WHERE enabled=1 AND archived=0 AND account_identity IS NOT NULL AND <> ''`. Colunas + `Index` espelhados em [db/tables.py](../db/tables.py); colunas adicionadas ao `_STATUS_FIELDS`.
- **Como foi feito / decisões:** guardada/idempotente/reversível (espelha `0035`); `postgresql_where` + `sqlite_where`. `kind` fora do índice (unicidade sobre `(provider, account_identity)` já implica kind igual na prática). Head confirmado por grep e por `ScriptDirectory.get_heads()` = `['0038_channels_account_identity']`.
- **Problemas / pendências:** nenhuma.
- **Verificação:** round-trip no `WHATSBOT_TEST_DB_URL`: upgrade cria colunas+índice; downgrade dropa; re-upgrade limpo. 2 rows enabled mesmo `(provider,account_identity)` → `IntegrityError`; NULL/""/archived/disabled e cross-provider coexistem. Também `tests/test_schema_drift.py` verde.

---

### Fase F4 — Core: persist/sweep + refuse + enforcement create/update  🔴 [dep F1,F2,F3]
**Objetivo:** dar vida às colunas e **bloquear** a duplicata nos dois momentos, tudo genérico.
**Itens (caracterização ANTES — golden do roteamento inbound):**
- [sequencial] **Sweep de status por canal:** loop supervisado (owner = plugin `gowa`, ao lado de `status_poll_loop` em [lifecycle.py](../storages/plugins/gowa/lifecycle.py) / [server/background.py](../server/background.py)) que, por canal enabled com instância viva, lê `inst.status()` + `inst.account_identity()` e grava `own_phone`/`connected`/`logged_in`/`account_identity`/`_kind` via `set_status` **só quando muda**.
- [sequencial] **Enforcement pós-conexão (genérico):** ao obter `account_identity()` não-`None`, `dedup.find_conflict(provider, id, exclude_channel_id=cid)`. Conflito → **não** persistir, chamar `inst.reject_duplicate()` (D7), `last_error="Esta conta já está conectada no canal <X>"`, manter `enabled=1`/`logged_in=0`. Envolver a escrita em `try/except IntegrityError` (índice F3) → mesma via. Nunca crashar o sweep.
- [sequencial] **Enforcement create/update (genérico):** em [channels.py](../server/routes/channels.py) create + [channel_service.py:400-403](../app/services/channel_service.py) update: `provider_cls.identity_from_credentials(submitted_creds)`; se não-`None` → `find_conflict(exclude_channel_id=cid)` → **409** antes de persistir; senão gravar `account_identity`/`_kind` na row. GOWA retorna `None` aqui (sem identidade no create) → sem 409 no create, cai no login.
- [paralelo] Alinhar normalização de `by_phone` ([channel_repo.py:135](../db/repositories/channel_repo.py)) ao roteamento (`split(':')[0]`), sem tocar `get_gowa_channel_for_device` (P13).
**Pronto quando:** com `default` logado no número X, criar 2º GOWA e ler QR do **mesmo** X → 2º device deslogado + card com erro; `default` intacto. 2º Cloud com mesmo `phone_number_id` → 409. Golden de roteamento inbound inalterado.

#### Status de execução — Fase F4
**Estado:** ✅ Concluída (commit F4)
- **O que foi feito:** (1) **Sweep** `channel_identity_sweep_loop` em [server/background.py](../server/background.py) (intervalo 15s), registrado via `ctx.spawn_task("channel_identity_sweep", …)` no lifecycle do plugin `gowa` ([lifecycle.py](../assets/plugin_examples/gowa/lifecycle.py) — assets tracked + storages installed). A lógica pura vive em [app/services/channel_identity.py](../app/services/channel_identity.py) (`sweep_channel`): grava `own_phone`/`connected`/`logged_in`/`account_identity`/`_kind` só-quando-muda; num conflito chama `reject_duplicate()` + `last_error` + `logged_in=0`, mantendo `enabled=1`, sem persistir a identidade. (2) **Enforcement create/update** genérico em [channel_service.py](../app/services/channel_service.py) (`_guard_duplicate`/`credential_identity`/`_persist_identity` + `DuplicateChannelError`); rotas mapeiam para **409**. Update guarda nas creds **efetivas** (armazenadas + edição não-placeholder). (3) Normalização `by_phone` alinhada ao roteamento (`split('@')[0].split(':')[0]`) sem tocar `get_gowa_channel_for_device`.
- **Como foi feito / decisões:** quem "ganha" = o canal que **já tinha** a identidade persistida; o recém-chegado é recusado (`reject_duplicate` default = `logout()`, que no GOWA desloga o device). `IntegrityError` no persist → mesma via 409 (create/update) ou refuse (sweep). Nada de `if provider ==` no core.
- **Problemas / pendências:** o sweep NÃO roda sob o TestClient hermético (lifespan skip) — coberto por teste direto de `sweep_channel`. UX GOWA: pode piscar "Conectado!" no QR antes do sweep (≤15s) recusar (ver F7). Cloud/Telegram estão **disabled** no app de teste (registro manual no F8).
- **Verificação:** golden de roteamento inbound inalterado (`test_gowa_plugin.py` = 49 passed / 1 pre-existing fail, **idêntico ao baseline**, incl. ":device suffix maps to default"). `tests/test_channel_dedup_enforcement.py` (sweep persiste/recusa/self/none + guard 409/self). Endpoint 409 no F8.

---

### Fase F5 — Ganchos finos nos providers  🟢 [dep F2]
**Objetivo:** cada provider declara sua identidade; nada de lógica no core.
**Itens:**
- [paralelo] **GOWA** ([gowa_channel.py](../channels/providers/gowa_channel.py)): `account_identity()` → `AccountIdentity("phone", canonical(digits(own_phone)))` (canônico BR via `br_phone_variants` — escolher 1 forma determinística), `None` se vazio; `identity_from_credentials` fica default `None`.
- [paralelo] **whatsapp_cloud** ([whatsapp_cloud/channels.py](../storages/plugins/whatsapp_cloud/channels.py)): `identity_from_credentials(creds)` → `AccountIdentity("phone_number_id", creds["phone_number_id"].strip())` ou `None`.
- [paralelo] **telegram** ([telegram/channels.py](../storages/plugins/telegram/channels.py)): `identity_from_credentials(creds)` → `AccountIdentity("bot_token", creds["bot_token"].strip())`; opcional `account_identity()` → `AccountIdentity("bot_id", str(getMe.id))` (mais canônico; best-effort). **Nota:** se usar `bot_id` no live e `bot_token` no create, o `kind` difere → decidir na execução usar **um** `kind` consistente (recomendo `bot_id` quando disponível, com fallback `bot_token`; documentar o trade-off).
**Pronto quando:** cada provider retorna a identidade certa (unit por provider, mockando cred/status); `test` retorna `None`.

#### Status de execução — Fase F5
**Estado:** ✅ Concluída (commit F5)
- **O que foi feito:** GOWA ([gowa_channel.py](../channels/providers/gowa_channel.py)) `account_identity()` → `AccountIdentity("phone", canonical)` via `get_own_number` device-scoped; canônico BR por `_canonical_phone` (menor variante de `br_phone_variants` → colapsa 12↔13 dígitos numa forma). whatsapp_cloud `identity_from_credentials` → `("phone_number_id", strip)`. telegram `identity_from_credentials` + `account_identity` → `("bot_id", …)`. `test` herda None.
- **Como foi feito / decisões:** **P1 resolvido** — Telegram usa `kind="bot_id"` nos DOIS ganchos, derivando o `bot_id` do próprio token `{bot_id}:{hash}` (sem rede), o que (a) dá kind consistente create↔live e (b) mantém o token secreto fora da coluna `account_identity`. Ganchos aplicados nas cópias **tracked** `assets/plugin_examples/{whatsapp_cloud,telegram}` (aditivo — sem clobber do `required_credentials`/`STATUS_TIMEOUT` já presentes lá) E nas cópias installed `storages/plugins/*` (gitignored, para os testes locais).
- **Problemas / pendências:** as cópias `storages/plugins/{whatsapp_cloud,telegram}` estavam **stale** vs `assets` (faltavam `required_credentials` etc. de outra lane) — NÃO sincronizei (fora de escopo); só adicionei os ganchos de identidade. Ver "Notas / reporte".
- **Verificação:** `tests/test_channel_identity_hooks.py` (10 testes) — cada provider retorna a identidade certa; 12↔13 colapsam; `test`→None; assets importam e retornam o mesmo que storages.

---

### Fase F6 — Docs do contrato (docstrings + CLAUDE.md)  🟢 [dep F2]
**Objetivo:** o contrato nasce documentado, no lugar que uma IA lê sozinha (habilita o `/new-channel` do plano 33).
**Itens:**
- [paralelo] Docstrings normativos dos 3 ganchos em [channels/base.py](../channels/base.py): semântica de `kind`, obrigação de `value` canônico, `None` = desconhecido, os dois momentos (create vs conexão), exemplo por provider, e o `reject_duplicate` default.
- [paralelo] Seção nova no **CLAUDE.md** ("Contrato de identidade de conta / dedup de canais"): a regra `(provider, identity)`, a coluna `account_identity`, o índice, e como um provider novo implementa os ganchos (apontando os 3 exemplos).
**Pronto quando:** um dev/IA lendo só `channels/base.py` + CLAUDE.md consegue implementar os ganchos de um provider novo sem ler o core.

#### Status de execução — Fase F6
**Estado:** ✅ Concluída (commit F6)
- **O que foi feito:** docstrings normativos dos 3 ganchos já escritos em [channels/base.py](../channels/base.py) (no commit F2). Nova seção "Contrato de identidade de conta / dedup de canais (plano 32)" no [CLAUDE.md](../CLAUDE.md) — regra `(provider, identidade)`, coluna/índice, os dois momentos (create vs conexão), e como um provider novo implementa os ganchos apontando gowa/cloud/telegram como exemplos.
- **Como foi feito / decisões:** seção adicionada como bloco NOVO (regra multi-lane: só adicionar, não reordenar), logo após "Filtro de tipos de JID".
- **Problemas / pendências:** nenhuma.
- **Verificação:** revisão manual — um dev/IA lendo só `channels/base.py` + a seção consegue implementar os ganchos de um provider novo (habilita `/new-channel` do plano 33).

---

### Fase F7 — Frontend: erro genérico (create/edit + card)  🟢 [dep F4,F5]
**Objetivo:** o usuário entende o bloqueio; texto genérico (não por provider).
**Itens:**
- [paralelo] Tratar o **409** do create/edit ([ChannelsManager.js](../web/static/js/components/ChannelsManager.js) `handleCreate` + `ChannelEditForm`) exibindo a mensagem do backend.
- [paralelo] No card, quando `last_error` for de duplicata, reusar o pattern de **erro** ([ChannelCard.js:77-81](../web/static/js/components/channels/ChannelCard.js)) — "Esta conta já está conectada no canal X" (dark-safe).
- [paralelo] (Opcional) No QR ([QRConnect.js](../web/static/js/components/channels/QRConnect.js)), ao detectar a recusa (volta `logged_in=false` + `last_error`), fechar o QR e mostrar o erro.
**Pronto quando:** create/edit duplicado mostra o motivo; GOWA duplicado mostra o banner no card; legível no **modo escuro**.

#### Status de execução — Fase F7
**Estado:** ✅ Concluída (sem mudança de código — os caminhos genéricos já cobrem)
- **O que foi feito:** verificado que o **409** do create/edit já é exibido pelos caminhos genéricos existentes: `handleCreate`→`setCreateError(res.error)`→`ChannelForm` renderiza `error` (linha 190); `update`→`setError(res.error)`→`ChannelsManager` renderiza (linha 371). O `request()` de [httpClient.js](../web/static/js/services/httpClient.js) devolve o body `{ok:false,error}` em qualquer status ≠401, então a mensagem do backend chega intacta. No card ([ChannelCard.js](../web/static/js/components/channels/ChannelCard.js)), `showRawError` já renderiza o `last_error` de duplicata ("Esta conta já está conectada no canal X") no banner vermelho.
- **Como foi feito / decisões:** o texto do backend é **genérico** (não por provider), então nada de código novo no front — reusa o padrão de erro existente, exatamente como o plano pediu. Dark-safe: as classes `text-red-600 bg-red-50 border-red-200` estão na lista de fallback `html.dark` do `custom.css`.
- **Problemas / pendências:** edge cosmético GOWA (opcional no plano): como a recusa é pós-QR (sweep ≤15s), o `QRConnect` pode piscar "Conectado!" antes de o sweep deslogar; depois o card mostra o banner de duplicata. Não implementei o fechamento proativo do QR (exigiria surfacing do `last_error` no status ao vivo, com risco de erro stale) — anotado como melhoria futura.
- **Verificação:** leitura do fluxo (create→form error; update→manager error; card→last_error banner). Cobertura dark-mode confirmada pela lista de fallback do `custom.css`.

---

### Fase F8 — Testes  🟢 [dep F4,F5]
**Objetivo:** travar o comportamento na suíte.
**Itens:**
- [paralelo] `tests/test_endpoints.py`: 2º Cloud mesmo `phone_number_id` → 409; 2º Telegram mesmo token → 409; `PUT` para colidir → 409; providers diferentes → OK.
- [paralelo] Repo/índice: 2 rows mesmo `(provider,account_identity)` enabled → `IntegrityError`; archived/disabled → OK.
- [paralelo] Unit do motor F2/F3 + dos ganchos F5.
**Pronto quando:** `venv/bin/python -m pytest tests/ -q` verde no `WHATSBOT_TEST_DB_URL`.

#### Status de execução — Fase F8
**Estado:** ✅ Concluída (commit F8)
- **O que foi feito:** endpoint 409 em [tests/test_endpoints.py](../tests/test_endpoints.py) (append-only, no fim): registra as classes cloud/telegram (disabled no app hermético) e valida 2º cloud mesmo `phone_number_id`→409, 2º telegram mesmo `bot_id`→409, editar-pra-colidir→409, e os negativos (id/kind diferente, própria identidade, campo não-credencial → 200). Índice/repo + sweep + guard em [tests/test_channel_dedup_enforcement.py](../tests/test_channel_dedup_enforcement.py). Units do motor (F2), ganchos (F5), device-scope (F1).
- **Como foi feito / decisões:** cloud/telegram vêm **disabled** no app de teste, então registro suas classes no registry vivo (`app.state.deps.channel_registry`) dentro da seção — como produção faz quando habilitados. Seção no fim do arquivo (não afeta testes anteriores). Regra multi-lane respeitada (só ADD em test_endpoints.py).
- **Problemas / pendências:** 1 falha **pré-existente** (`_missing_required ok quando preenchido`) reproduz no `developer` limpo (cópias `storages/plugins` stale) — NÃO é regressão desta lane. Ver "Notas / reporte".
- **Verificação:** `venv/bin/python tests/test_endpoints.py` → 1037 passed, 1 failed (a 1 é a pré-existente). Novos arquivos unit todos verdes. Full `pytest tests/ -q` rodado ao fim.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Persistir `own_phone` **ativa** o `by_phone` (hoje vazio) | Roteamento inbound muda (P13) | Não tocar `get_gowa_channel_for_device`; alinhar normalização; golden ANTES de F4; como a duplicata é bloqueada, `by_phone` não terá colisão real. |
| Falso-positivo NULL/`""` | Canais não-logados agrupados | `find_conflict` **e** índice **excluem** NULL/`""`. |
| Falso-positivo por sufixo | `endswith` marca números diferentes | Value **canônico** + igualdade exata; jamais `endswith`. |
| `get_own_number` misattribution | Gravar número de OUTRO device | F1 é **pré-requisito** de F4/F5; `""` se não resolver deste device. |
| `@lid` vs `@s.whatsapp.net` | own_phone lid não casa com phone | F1 prefere `@s.whatsapp.net`; `kind` separa namespaces. |
| Telegram `kind` inconsistente | `bot_token` (create) vs `bot_id` (live) têm `kind` diferente | Decidir 1 `kind` (recomendo `bot_id` com fallback), documentar (F5). |
| Race entre 2 QRs simultâneos | Ambos persistem e veem conflito | Índice único F3 serializa: 2ª escrita `IntegrityError` → perdedor deslogado. |
| Write-amplification no sweep | Gravar a cada tick | Gravar só quando muda. |
| Ordem de migration | Head errado quebra `upgrade` | Encadear do head **corrente** (`grep`); round-trip no Postgres de teste. |
| Sweep sem supervisor | Loop não sobe/duplica | Task supervisada do plugin `gowa`. |
| Credencial editada p/ colidir | Bypass do create-guard | Enforcement roda **também** no update (F4). |
| Modo escuro | Banner ilegível | Pattern `ChannelCard` (`wa-*`), testar dark. |

---

## 7. Perguntas em aberto

**P1 — Telegram: `kind` da identidade.**
⏸️ ADIADO p/ execução (F5). Recomendação: usar `bot_id` (`getMe`, canônico) quando disponível, fallback `bot_token`; garantir `kind` consistente no que é persistido.

**P2 — GOWA: ação ao recusar.**
✅ DECIDIDO: recusar o login (device deslogado via `reject_duplicate` default + `last_error`; canal fica enabled/não-logado; usuário decide). Não auto-arquivar (não-destrutivo).

**P3 — Arquivados contam?**
✅ DECIDIDO: **não** (`enabled=1 AND archived=0`), no `find_conflict` e no índice.

**P4 — Resposta-dupla da IA (fan-out multi-device).**
⏸️ FORA DE ESCOPO (D4): com a duplicata bloqueada na origem, o cenário não ocorre pela UI. Se surgir por outra via, tratar com dedup global de `external_msg_id` ([message_ingest_service.py:382](../app/services/message_ingest_service.py)) — plano futuro.

**P5 — Redundância `own_phone` × `account_identity` (GOWA).**
✅ DECIDIDO: aceitável — propósitos distintos (`own_phone` = roteamento/display; `account_identity` = chave de dedup genérica). Para GOWA são o mesmo número canônico.

---

## 8. Checklist de verificação

- [x] `venv/bin/python -m pytest tests/ -q` verde no `WHATSBOT_TEST_DB_URL` (menos as 3 falhas **pré-existentes** — ver Notas).
- [x] Unit do motor `dedup` + ganchos dos providers verde.
- [x] Migration `upgrade`/`downgrade` round-trip; índice rejeita 2 `(provider,account_identity)` enabled; ignora NULL/archived/disabled.
- [~] 2º QR do mesmo número (GOWA) recusado — **verificação automatizada** via `sweep_channel` (device deslogado + `last_error` no card + `logged_in=0`); 2º QR **manual** contra GOWA vivo fica para o QA de campo (sweep não roda no TestClient).
- [x] 2º Cloud mesmo `phone_number_id` → 409; 2º Telegram mesmo token (bot_id) → 409; update-para-colidir → 409.
- [x] Mesmo valor em providers diferentes → permitido (plano 11 D1/D2) — testado (kind separa namespaces).
- [x] `account_identity`/`account_identity_kind` persistidos (create/update + sweep); `own_phone`/`connected`/`logged_in` gravados pelo sweep quando muda.
- [x] Roteamento inbound inalterado (golden `test_gowa_plugin.py` = 49/1, idêntico ao baseline) — `get_gowa_channel_for_device` intacto.
- [x] Nenhum `if provider ==` novo no core (só nos providers).
- [x] `channels/base.py` + CLAUDE.md documentam o contrato (F6).
- [x] Banner de duplicata (card) legível no **modo escuro** (classes red-*/fallback `html.dark`).

## 9. Notas / reporte da execução (Lane A)

- **Cópias `storages/plugins/{whatsapp_cloud,telegram,gowa}` são gitignored e estavam STALE** vs `assets/plugin_examples/*` (a fonte tracked): faltavam `required_credentials`, `STATUS_TIMEOUT`, o param `timeout` de `_request` etc. de outra lane. Apliquei os ganchos de identidade **aditivamente** nas duas cópias (assets = shipping, storages = testes locais) sem sincronizar o resto (fora de escopo). **Reporte:** convém re-instalar os zips atualizados de cloud/telegram nesta máquina para destravar a falha pré-existente.
- **Falhas PRÉ-EXISTENTES no `developer` limpo** (confirmadas por `git stash` + re-run, NÃO são regressão desta lane): `test_legacy_suite::test_endpoints.py` (`FAIL _missing_required ok quando preenchido` — depende do `required_credentials` que só existe nos assets, não nas cópias installed stale) e `test_legacy_suite::test_gowa_plugin.py` (1 fail — import relativo ao rodar standalone). O `test_schema_drift` que apareceu vermelho no baseline foi **falso** (rodei durante minhas edições); passa isolado.
- **Cloud/Telegram vêm `disabled`** no app de teste hermético (só `gowa` registrado). O F8 registra as classes no registry vivo para exercitar o 409 — em produção elas são habilitadas pelo usuário.
- **Contrato para o plano 33a:** `AccountIdentity` + os 3 ganchos em `channels/base.py` estão documentados e estáveis; `channels/dedup.py` é o motor genérico. Um provider novo implementa 1–2 métodos finos; o core não muda.

---

## Apêndice — arquivos-chave (por fase)

- **F1:** [gowa/client.py](../gowa/client.py), [db/repositories/channel_repo.py](../db/repositories/channel_repo.py).
- **F2:** [channels/base.py](../channels/base.py) (contrato), `channels/dedup.py` (novo), [db/repositories/channel_repo.py](../db/repositories/channel_repo.py), [db/repositories/channel_credential_repo.py](../db/repositories/channel_credential_repo.py), [channels/br_phone.py](../channels/br_phone.py).
- **F3:** `db/alembic/versions/<novo>_channels_account_identity.py` (ref [0035](../db/alembic/versions/20260702_0035_single_router_index.py)), [db/tables.py](../db/tables.py).
- **F4:** [storages/plugins/gowa/lifecycle.py](../storages/plugins/gowa/lifecycle.py), [server/background.py](../server/background.py), [app/services/channel_service.py](../app/services/channel_service.py), [server/routes/channels.py](../server/routes/channels.py), [db/repositories/channel_repo.py](../db/repositories/channel_repo.py).
- **F5:** [channels/providers/gowa_channel.py](../channels/providers/gowa_channel.py), [storages/plugins/whatsapp_cloud/channels.py](../storages/plugins/whatsapp_cloud/channels.py), [storages/plugins/telegram/channels.py](../storages/plugins/telegram/channels.py).
- **F6:** [channels/base.py](../channels/base.py), [CLAUDE.md](../CLAUDE.md).
- **F7:** [web/static/js/components/channels/ChannelCard.js](../web/static/js/components/channels/ChannelCard.js), [web/static/js/components/ChannelsManager.js](../web/static/js/components/ChannelsManager.js), [web/static/js/components/channels/QRConnect.js](../web/static/js/components/channels/QRConnect.js).
- **F8:** [tests/test_endpoints.py](../tests/test_endpoints.py), `tests/test_channel_dedup.py` (novo).
