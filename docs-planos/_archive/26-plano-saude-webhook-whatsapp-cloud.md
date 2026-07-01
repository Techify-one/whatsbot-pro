# Plano 26 — Saúde do webhook do WhatsApp Cloud (detectar webhook apontando pra outro lugar) + botão "Configurar webhook"

> **Status:** PLANEJAMENTO · **Data:** 2026-06-30 · **Escopo:** médio
> **Origem:** pedido do usuário — o card do canal WhatsApp Cloud mostra "Conectado / Autenticado" (verde) mesmo quando o webhook da Meta aponta pra **outro domínio** (falso positivo). Quer (a) que isso apareça no painel e (b) um botão pra apontar o webhook de volta pra esta instância.
> **Método:** investigação nesta sessão — leitura do código (`arquivo:linha` abaixo) + **provas reais contra a Graph API** com o `access_token` já salvo do canal `teste` (read-only). Confirmado que dá pra **ler** (`?fields=webhook_configuration` e `/subscribed_apps`) e **setar** (`POST /{waba_id}/subscribed_apps`) o webhook só com `access_token` + `waba_id` + `verify_token` (sem `app_secret`/`app_id`).
>
> O status atual valida só o caminho de **saída** (token + phone_number_id na Graph API). Não diz nada sobre o webhook de **entrada**, que é uma config do lado da Meta apontando uma URL de volta pra nós. Este plano adiciona uma checagem de saúde do webhook (lê o que a Meta tem configurado e compara com a URL que ESTA instância espera) e um botão que repointa o webhook via override no nível da WABA.
>
> **Como usar este plano:** ao executar cada fase, preencha o bloco **"Status de execução"** dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.
> Legenda de estado: `⬜ Não iniciada` · `🟡 Em andamento` · `✅ Concluída` · `⛔ Bloqueada`.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 ✅ (2026-06-30) | A funcionalidade é **só para `whatsapp_cloud`**. GOWA (QR/linked-device) e Telegram (já tem `getWebhookInfo`/autoconfigure próprios) ficam de fora. | A linha de saúde e o botão só renderizam quando `channel.provider === 'whatsapp_cloud'`. Nenhuma mudança nos outros providers. |
| D2 ✅ (2026-06-30) | O override é setado no **nível da WABA** (`POST /{waba_id}/subscribed_apps` com `override_callback_uri` + `verify_token`) — não no App Dashboard. | Usa credenciais já salvas (`access_token`, `waba_id`, `verify_token`); **não** precisa de `app_secret`/`app_id`. Provado nesta sessão. |
| D3 ✅ (2026-06-30) | A comparação "aponta pra cá?" usa a **URL inteira** (origin + path com `channel_id`), não só o domínio. | No caso real a Meta tinha `.../whatsapp_cloud/whatsapp_oficial_meta` enquanto o id local é `teste` → mesmo no mesmo domínio o inbound cairia em `unknown_channel` (`server/routes/channel_webhook.py:329-333`). O health-check precisa pegar esse segundo mismatch. |
| D4 ✅ (2026-06-30) | A URL esperada desta instância vem do **frontend** (`window.location.origin`), não de config no backend. | Não há `public_url` salvo em `config` (só `account_url` do billing). O frontend já deriva de `window.location.origin` (`web/static/js/components/channels/notices.js:30`, `ChannelsManager.js:186`). O backend recebe a URL esperada como parâmetro. |
| D5 ✅ (2026-06-30) | Setar o webhook **repointa** ele pra cá (rouba de onde quer que esteja hoje). | Exige **confirmação** explícita no clique do botão (dialog). O `POST` só sucede se a Meta conseguir fazer o handshake `GET` nesta instância e o `verify_token` casar — ou seja, um set bem-sucedido **prova** que o webhook chega aqui. |

**Princípio fixo (memória `refactor-rollout-context`):** o produto **não está em produção/distribuído** — pode-se mexer no plugin bundled sem stopgap de compatibilidade. Mas há **duas cópias** do plugin (live + seed) que **precisam ficar em sincronia** (ver §4 e gotcha do `CLAUDE.md`).

---

## 1. Resumo executivo

O card do canal Cloud confia só no `status()` do provider, que faz um `GET /{phone_number_id}` na Graph API — valida **outbound** (conseguimos enviar). Quando o `access_token` é válido mas o **webhook** está apontado pra outro domínio (ou pra um `channel_id` que não existe aqui), o card pinta verde cego e o operador acha que está recebendo mensagens — mas não está.

A solução: (1) um **endpoint de leitura** que pergunta à Meta qual webhook está configurado (`webhook_configuration` no número + `override_callback_uri` na WABA) e classifica contra a URL esperada desta instância; (2) uma **linha de saúde do webhook** no card (`OK` / `apontando pra outro domínio` / `path/channel_id divergente` / `override não configurado` / `desconhecido`); (3) um **botão "Configurar webhook"** que faz o `POST` do override apontando pra `${origin}/api/webhook/whatsapp_cloud/${channel.id}` com o `verify_token` salvo, e re-checa.

---

## 2. Como funciona hoje (mapa)

### 2.1 O falso positivo (backend)

| Ponto | `arquivo:linha` | O que faz |
|-------|-----------------|-----------|
| `WhatsAppCloudChannel.status()` | `storages/plugins/whatsapp_cloud/channels.py:110` | Faz `GET /{phone_number_id}` na Graph API; HTTP 200 ⇒ `{connected: True, logged_in: True}`. **Só valida outbound.** Não checa o webhook. |
| Acesso a credencial | `storages/plugins/whatsapp_cloud/channels.py:71` (`_cred`) | `registry.get_credential(channel_id, key)` ou fallback ao dict `credentials` em memória (**usável em testes / fora do registry**). |
| Helpers Graph | `channels.py:81` (`_graph_version`), `:97` (`_base_url`), `:100` (`_headers`) | Montam `https://graph.facebook.com/v21.0` + `Authorization: Bearer`. |
| Serviço de status do core | `app/services/channel_service.py:226` (`status`) → `:236` `inst.status()` | Chamado pelo `GET /api/channels/{id}/status` e em massa por `refreshStatuses` (`web/static/js/components/ChannelsManager.js:128`). ⚠️ **caminho quente em massa** — não bloquear com chamadas Graph extras (ver P1). |

### 2.2 O webhook de entrada (core, não plugin)

| Ponto | `arquivo:linha` | O que faz |
|-------|-----------------|-----------|
| Handshake `GET` | `server/routes/channel_webhook.py:268-283` | Echo de `hub.challenge` quando `hub.verify_token == channel_credential_repo.get(channel_id, "verify_token")` (`:277`). **O `verify_token` que setarmos no override TEM que ser o mesmo salvo no canal**, senão o handshake da Meta falha. |
| Inbound `POST` | `server/routes/channel_webhook.py:285-349` | Resolve `channel_repo.get(channel_id)`; **`None` ⇒ `ignored / unknown_channel`** (`:329-333`). Por isso o `channel_id` no path precisa bater com o id local. Depois `parse_inbound` → `_dispatch_events` → pipeline agêntico. |

### 2.3 As provas da Graph API (rodadas nesta sessão, read-only, canal `teste`)

| Chamada | Retorno (resumido) | Credenciais necessárias |
|---------|--------------------|--------------------------|
| `GET /v21.0/{phone_number_id}?fields=webhook_configuration` | `{"webhook_configuration": {"whatsapp_business_account": "<url>", "application": "<url>"}}` | `access_token` + `phone_number_id` (**sempre presentes**) |
| `GET /v21.0/{waba_id}/subscribed_apps` | `{"data":[{"whatsapp_business_api_data": {...}, "override_callback_uri": "<url>"}]}` | `access_token` + `waba_id` |
| `POST /v21.0/{waba_id}/subscribed_apps` body `{override_callback_uri, verify_token}` | seta o override (Meta valida com handshake `GET`) | `access_token` + `waba_id` + `verify_token` |
| `DELETE /v21.0/{waba_id}/subscribed_apps` | remove o override (volta pro webhook do App) | `access_token` + `waba_id` |

No caso real, ambos os reads retornaram `https://whatsbot-luisa.teste.techify.run/api/webhook/whatsapp_cloud/whatsapp_oficial_meta` — **domínio diferente** E **`channel_id` diferente** (`whatsapp_oficial_meta` ≠ `teste`).

### 2.4 Precedente do botão (Telegram)

| Ponto | `arquivo:linha` | Reuso |
|-------|-----------------|-------|
| `GET /status` (getWebhookInfo) | `storages/plugins/telegram/routes.py:57-70` | Padrão de "endpoint de ajuda do provider que lê estado do webhook". |
| `POST /set-webhook` | `storages/plugins/telegram/routes.py:73-87` | Padrão de "endpoint que seta o webhook com o token do canal, lido via `channel_credential_repo`". |
| `_call(token, method, payload)` | `storages/plugins/telegram/routes.py:32-44` | Padrão de helper httpx síncrono isolado em `asyncio.to_thread`. |
| Service no frontend | `web/static/js/services/api.js:599` (`telegramAutoconfigure`), `:604` (`telegramChannelStatus`) | Padrão de wrapper `request(...)` pra rota de plugin. |

### 2.5 O card e o status no frontend

| Ponto | `arquivo:linha` | O que faz |
|-------|-----------------|-----------|
| Pills "Conectado/Autenticado" | `web/static/js/components/channels/ChannelCard.js:39-49` | Onde a **linha de saúde do webhook** vai ser inserida (logo abaixo, só pra cloud). |
| Aviso amber (creds faltando) | `ChannelCard.js:59-66` | **Padrão visual a copiar** (tint amber com fallback dark via `custom.css`). |
| Botão "Atualizar" | `ChannelCard.js:82-84` → `onRefresh` → `getChannelStatus` | O "Configurar webhook" entra na mesma fileira de ações (`:75-92`), só pra cloud. |
| Merge de status no card | `ChannelsManager.js:241-253` (`handleRefresh`) e `:128-145` (bulk) | Onde mergear o campo `webhook_health` no objeto do canal. |
| `WebhookNotice` (URL pós-criação) | `web/static/js/components/channels/notices.js:29-30` | Já constrói `${window.location.origin}/api/webhook/whatsapp_cloud/${channelId}` — **mesma fórmula** da URL esperada. |
| Cred fields cloud | `web/static/js/components/channels/constants.js:27` (`requiredCreds`), `:139-145` (`buildCredentials`) | `requiredCreds.whatsapp_cloud = ['access_token','phone_number_id','verify_token']` — **`waba_id` é opcional hoje** (ver P2). |

---

## 3. Inventário / análise

| # | Item | `arquivo:linha` | O que falta / muda | Abordagem | Risco | Esforço |
|---|------|-----------------|--------------------|-----------|-------|---------|
| 1 | Endpoint de **leitura** da saúde do webhook | `storages/plugins/whatsapp_cloud/routes.py` (novo) + cópia em `assets/plugin_examples/whatsapp_cloud/routes.py` | `GET /webhook-status?channel_id=&expected_url=` → lê Meta, classifica match | httpx inline (padrão telegram `_call`), creds via `channel_credential_repo` | baixo | M |
| 2 | Endpoint de **set** do override | mesmos 2 arquivos | `POST /set-webhook` body `{channel_id, url}` → `POST /{waba_id}/subscribed_apps` `{override_callback_uri:url, verify_token:<salvo>}` + re-read | idem | médio | M |
| 3 | Endpoint de **delete** do override (opcional) | mesmos 2 arquivos | `POST /delete-webhook` → `DELETE /{waba_id}/subscribed_apps` | idem | baixo | S |
| 4 | Services no frontend | `web/static/js/services/api.js` (após `:605`) | `cloudWebhookStatus(channelId, expectedUrl)`, `cloudSetWebhook(channelId, url)`, (`cloudDeleteWebhook`) | wrappers `request(...)` | baixo | S |
| 5 | Componente **WebhookHealthRow** + fetch lazy | `web/static/js/components/channels/` (novo) + `ChannelCard.js:49` | Linha de saúde (só cloud) + botão "Configurar webhook" (confirm → set → re-check) | self-fetch via `useEffect`, gate por provider | médio | M |
| 6 | Wiring no card/manager | `ChannelCard.js:12` (props), `ChannelsManager.js` | Passar handlers/estado; re-check após set | seguir padrão `onRefresh` | baixo | S |
| 7 | Testes (mock Graph) | `tests/` (novo arquivo) | Cobrir read (ok/wrong/unset/unknown) + set (sucesso/erro/sem waba) | mock httpx; ver §6 padrão de mock | baixo | M |

### Falsos positivos descartados

| Hipótese | Veredito | Razão |
|----------|----------|-------|
| "O verde 'Conectado/Autenticado' indica que o inbound funciona." | **FALSO** | `status()` (`channels.py:110`) só faz `GET /{phone_number_id}` — valida outbound. É exatamente o falso positivo que o plano resolve. |
| "Precisa de `app_secret`/`app_id` pra ler ou setar o webhook." | **FALSO** | Provado nesta sessão: `access_token` + `waba_id` (+ `verify_token` pro set) bastam. `app_secret` só serviria pra validar assinatura `X-Hub-Signature` do POST — que o core **não** valida hoje (`channel_webhook.py:285` não checa assinatura). Fora de escopo. |
| "Dá pra ler a URL do webhook configurado no **App Dashboard**." | **FALSO** sem `app_id\|app_secret` | Só o **override** (número/WABA) é legível com o token de negócio. Se não há override, o estado é `unset` = "pode estar usando o webhook do App, não verificável aqui" (honesto, não afirmar OK). |
| "A comparação deve ir dentro de `status()`." | **Descartado** | `status()` não tem o `origin` esperado (vem do browser) e é chamado **em massa** (`ChannelsManager.js:128`) — uma chamada Graph extra por canal a cada refresh é lenta/rate-limit. Vai em endpoint dedicado, fetch lazy só pra cards cloud (P1). |
| "Comparar só o domínio basta." | **FALSO** (D3) | O `channel_id` no path também precisa bater (`channel_webhook.py:329-333`), senão é `unknown_channel`. Comparar URL inteira. |

---

## 4. Mudanças de infraestrutura / cuidados de camada

- **Duas cópias do plugin (sincronizar):** `storages/plugins/whatsapp_cloud/` (live, é o que roda) **e** `assets/plugin_examples/whatsapp_cloud/` (seed bundled, copiado no 1º boot). O `CLAUDE.md` (§"Sistema de plugins" + gotcha de bootstrap) lista `whatsapp_cloud` como um dos 3 providers bundled cujo frontend é parte do core e **não pode divergir**. **Toda** mudança em `routes.py` vai nas **duas** cópias. (As outras cópias de `channels.py` não mudam neste plano.)
- **Import cross-módulo é frágil sob o plugin loader:** `routes.py:13-17` já documenta que importar de `channels.py` é brittle (submódulos não estão no `sys.path`). Por isso os helpers Graph dos novos endpoints ficam **inline no `routes.py`** (httpx direto + `channel_credential_repo`), como o telegram faz — **não** importar `WhatsAppCloudChannel` no `routes.py`. A versão da Graph API se resolve de `config_repo.get("plugin.whatsapp_cloud.graph_api_version")` (setting declarativa em `settings.py`) com fallback `"v21.0"` (já duplicado em `routes.py:17`).
- **Segredos:** nunca ecoar `verify_token`/`access_token` nas respostas dos endpoints novos nem em log. A resposta de `/webhook-status` devolve só `configured_url`, `expected_url`, `match`, `can_set`, `reason`. (A camada de masking de credencial fica em `server/routes/channels.py`, não aqui.)
- **Sem migration / sem schema change:** nada toca o banco além de **ler** `channel_credentials` (já existe). Não criar revision Alembic.
- **Modo escuro:** a linha de saúde usa tints `green`/`amber`/`red` com classes cobertas pelo fallback `html.dark` do `custom.css` (mesmo padrão de `ChannelCard.js:59-66`) ou classes `wa-*`. Testar com modo escuro ligado.

---

## 5. Fases / Roadmap

### Diagrama de dependências

```
WAVE 0   F0 (caracterização — congela o contrato + testes RED)        ← 🔴 sozinha (define a API)
            │  (barreira: o contrato de F0 destrava F1 e F2)
WAVE 1   F1 (backend: endpoints) · F2 (frontend: services)            ← 🟢 paralelas (arquivos distintos)
            │  (barreira: F3 precisa de F1 e F2 prontas)
WAVE 2   F3 (frontend: card + botão, fetch real)                      ← 🔴 integra
            │
WAVE 3   F4 (verificação SQLite+PG, dark, manual)                     ← 🔴 fecha
```

### Tabela de fases

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|------|------|-----------|-------|-------|----------------|
| 0 | F0 — Caracterização + contrato | testes | 🔴 | baixo | testes mock-Graph existem e **falham** (endpoints ausentes); shape de request/response congelado |
| 1 | F1 — Backend: read/set/delete em `routes.py` (×2 cópias) | backend | 🟢 `[bloqueia: F3]` | médio | os 3 endpoints respondem; testes F0 passam |
| 1 | F2 — Frontend: services `api.js` | frontend | 🟢 `[bloqueia: F3]` | baixo | `cloudWebhookStatus`/`cloudSetWebhook` chamam as rotas certas (contra o contrato F0) |
| 2 | F3 — Frontend: WebhookHealthRow + botão no card | frontend | 🔴 `[depende de: F1, F2]` | médio | card cloud mostra o estado certo nos 4 casos; botão repointa e re-checa |
| 3 | F4 — Verificação integrada | QA | 🔴 `[depende de: tudo]` | baixo | checklist §8 todo marcado |

---

### Fase 0 — Caracterização + congelar o contrato `[🔴 sozinha]`

**Objetivo:** fixar o shape dos endpoints e escrever testes que **falham hoje** (endpoints não existem) e passam após F1.

**Contrato (congelado):**

- `GET /api/plugins/whatsapp_cloud/webhook-status?channel_id=<id>&expected_url=<url>` →
  `{"ok": true, "data": {"configured_url": str|null, "expected_url": str, "match": "ok"|"wrong_domain"|"wrong_path"|"unset"|"unknown", "can_set": bool, "reason": str}}`
  - `match`: `ok` (configured == expected, normalizado) · `wrong_domain` (host difere) · `wrong_path` (host igual, path/`channel_id` difere) · `unset` (sem override — Meta pode estar usando webhook do App, não verificável) · `unknown` (sem creds / erro HTTP).
  - `can_set`: `true` só se `waba_id` **e** `verify_token` presentes (ver P2).
- `POST /api/plugins/whatsapp_cloud/set-webhook` body `{channel_id, url}` →
  `{"ok": bool, "error": str|null, "data": {"match": <reclassificado após re-read>}}`.
- `POST /api/plugins/whatsapp_cloud/delete-webhook` body `{channel_id}` → `{"ok": bool, "error": str|null}` (opcional, item 3).

**Itens:**
- **0.1 [sequencial]** — Novo arquivo de teste (ex.: `tests/endpoints/test_p26_cloud_webhook.py` ou no estilo de `tests/test_endpoints.py`), com **mock de httpx** para as chamadas Graph (ver §6). Casos: read→`ok`, read→`wrong_domain`, read→`wrong_path` (mesmo host, `channel_id` diferente — o caso real), read→`unset` (sem override), read→`unknown` (sem `access_token`).
- **0.2 [paralelo]** — Teste de set: `POST /set-webhook` chama `POST /{waba_id}/subscribed_apps` com `override_callback_uri` = `url` e `verify_token` = o salvo; e que **sem `waba_id`** retorna erro claro (`can_set=false`).
- **0.3 [paralelo]** — Teste de regressão: `GET /api/channels/{id}/status` (caminho `status()` em massa) **não** ganhou chamada Graph extra de webhook (continua só o `GET /{phone_number_id}`) — guarda o P1.

**Pronto quando:** os testes existem, rodam e **falham pelas razões certas** (404/rota ausente). Contrato registrado no Status.

#### Status de execução — Fase 0
**Estado:** ✅ Concluída
- **O que foi feito:** `tests/endpoints/test_p26_cloud_webhook.py` (novo, 9 testes) cobrindo read→ok/wrong_domain/wrong_path/unset/unknown, set→sucesso/erro Graph/sem-waba, e a regressão do P1 (`status()` sem chamada Graph de webhook).
- **Como foi feito / decisões:** mock da Graph via **opção (a)** — monkeypatch de `httpx.Client` (o `routes.py` faz `import httpx` + `httpx.Client(...)`), independente do nome dinâmico do módulo do plugin. Fake `_FakeClient` context-manager que grava todas as chamadas em `calls` (usado nas asserts de "não chamou Graph"). Contrato F0 implementado direto junto com F1 (testes passam, não ficaram RED, pois F1 entrou na mesma sessão). Ordem importante: `build_app` ANTES do patch, senão o patch quebra a construção do cliente OpenAI no `AgentHandler`.
- **Problemas / pendências:** nenhuma.
- **Verificação:** 9/9 verdes em SQLite e em Postgres (DB isolado UTF8 `whatsbot_p26_test`, criado e dropado).

---

### Fase 1 — Backend: endpoints read/set/delete `[🟢 paralela com F2]`

**Objetivo:** os 3 endpoints no `routes.py` do plugin (nas **duas** cópias), usando httpx inline + `channel_credential_repo`.

> **Internamente:** 1.1 (helpers) → 1.2 (read) → 1.3 (set) → 1.4 (delete) → 1.5 (sincronizar cópia). Mesmo arquivo: editar em ordem.

- **1.1** — Helpers de módulo em `storages/plugins/whatsapp_cloud/routes.py` (espelhar telegram `_call` em `storages/plugins/telegram/routes.py:32`):
  - `_graph_base()` → `https://graph.facebook.com/{ver}` com `ver` de `config_repo.get("plugin.whatsapp_cloud.graph_api_version")` ou `DEFAULT_GRAPH_API_VERSION` (`routes.py:17`).
  - `_creds(channel_id)` → `channel_credential_repo.get_all(channel_id)` (1 query; pega `access_token`/`phone_number_id`/`waba_id`/`verify_token`).
  - `_normalize_url(u)` → strip trailing `/`, host lowercase, comparar `scheme+host+path` (ignorar querystring). Usado na classificação.
  - `_classify(configured, expected)` → retorna `ok`/`wrong_domain`/`wrong_path`/`unset` conforme D3.
- **1.2** — `GET /webhook-status`: lê via `GET /{phone_number_id}?fields=webhook_configuration` (primário — só precisa de `phone_number_id`); se vier vazio e houver `waba_id`, tenta `GET /{waba_id}/subscribed_apps` (`override_callback_uri`). Classifica contra `expected_url`. `can_set = bool(waba_id and verify_token)`. Chamadas httpx em `asyncio.to_thread`.
- **1.3** — `POST /set-webhook`: valida `waba_id` + `verify_token` presentes (senão `{ok:false, error:"configure WABA ID e Verify Token antes"}`); `POST /{waba_id}/subscribed_apps` `{override_callback_uri:url, verify_token}`; em sucesso, **re-read** e devolve o novo `match`. Tratar erro Graph (a Meta retorna `{"error":{"message":...}}` — extrair, como `_graph_error` em `channels.py:760`). ⚠️ o `POST` pode falhar se a Meta não conseguir o handshake nesta instância (URL não pública / `verify_token` divergente) — propagar a mensagem da Meta.
- **1.4 [opcional]** — `POST /delete-webhook`: `DELETE /{waba_id}/subscribed_apps`.
- **1.5** — **Copiar as mesmas mudanças** para `assets/plugin_examples/whatsapp_cloud/routes.py` (sincronia obrigatória — §4). Conferir `diff` entre as duas cópias = vazio (fora de comentários de path).

**Pronto quando:** os 3 endpoints respondem; testes da F0 passam; as duas cópias de `routes.py` idênticas.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída
- **O que foi feito:** 3 endpoints + helpers em `storages/plugins/whatsapp_cloud/routes.py` (e cópia idêntica em `assets/plugin_examples/whatsapp_cloud/routes.py`): `GET /webhook-status`, `POST /set-webhook`, `POST /delete-webhook`. Helpers inline: `_graph_version`/`_graph_base`, `_creds`, `_graph_get`/`_graph_post`/`_graph_delete`, `_graph_error`, `_normalize_url`, `_classify`, `_read_via_subscribed_apps`, `_read_configured_url`.
- **Como foi feito / decisões:** httpx INLINE (não importa `WhatsAppCloudChannel`). Versão da Graph via `config_repo.get("plugin.whatsapp_cloud.graph_api_version")` com fallback `v21.0`. Read primário por `phone_number_id` (`?fields=webhook_configuration`, campo `whatsapp_business_account`); fallback a `/{waba_id}/subscribed_apps` só quando falta `phone_number_id` ou o read primário dá erro HTTP. `can_set = bool(waba_id and verify_token)` (P2). `set` usa SEMPRE o `verify_token` salvo e re-lê pra devolver o `match` reclassificado; erro Graph propagado via `_graph_error` (sem 500). Segredos nunca ecoados/logados. Sincronia: `cp` das duas cópias + `diff` vazio.
- **Problemas / pendências:** as duas cópias estavam divergentes ANTES (a de `assets` não tinha `"app_secret"` em `credential_keys`); a reescrita igualou as duas (diff agora vazio).
- **Verificação:** testes F0 9/9 verdes (SQLite+PG); `diff` entre as duas cópias = vazio.

---

### Fase 2 — Frontend: services `[🟢 paralela com F1]`

**Objetivo:** wrappers `request(...)` pros novos endpoints, contra o contrato F0 (não depende da impl de F1).

- **2.1** — Em `web/static/js/services/api.js` (após `telegramChannelStatus`, `:605`):
  - `export async function cloudWebhookStatus(channelId, expectedUrl)` → `request('GET', '/api/plugins/whatsapp_cloud/webhook-status?channel_id=…&expected_url=' + encodeURIComponent(expectedUrl))`.
  - `export async function cloudSetWebhook(channelId, url)` → `request('POST', '/api/plugins/whatsapp_cloud/set-webhook', { channel_id, url })`.
  - (`cloudDeleteWebhook(channelId)` se F1.4 entrar.)

**Pronto quando:** as funções existem e batem nas rotas/parametrização do contrato (revisão de código; integração real em F3).

#### Status de execução — Fase 2
**Estado:** ✅ Concluída
- **O que foi feito:** `cloudWebhookStatus(channelId, expectedUrl)`, `cloudSetWebhook(channelId, url)` e `cloudDeleteWebhook(channelId)` em `web/static/js/services/api.js` (após `telegramChannelStatus`).
- **Como foi feito / decisões:** wrappers `request(...)` batendo nas rotas do contrato F0; `encodeURIComponent` nos query params do GET.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --check api.js` OK; integração real exercida na F3.

---

### Fase 3 — Frontend: linha de saúde + botão no card `[🔴 depende de F1+F2]`

**Objetivo:** o card do canal cloud mostra o estado do webhook e oferece o botão de configurar.

- **3.1** — Novo componente `WebhookHealthRow` (ex.: em `web/static/js/components/channels/` ou dentro de `ChannelCard.js`): recebe `channel`; **só renderiza se `channel.provider === 'whatsapp_cloud'`** (D1). Em `useEffect`, monta `expectedUrl = \`${window.location.origin}/api/webhook/whatsapp_cloud/${channel.id}\`` (mesma fórmula de `notices.js:30`) e chama `cloudWebhookStatus`. Renderiza por `match`:
  - `ok` → chip verde "Webhook: apontando pra cá ✓".
  - `wrong_domain` → amber "Webhook aponta pra outro domínio" + mostra `configured_url`.
  - `wrong_path` → amber "Webhook aponta pra outro canal (path divergente)" + `configured_url`.
  - `unset` → amber "Override de webhook não configurado (pode estar usando o webhook do App)".
  - `unknown` → cinza "Webhook: não verificável" + `reason`.
  - Tints com fallback dark (padrão `ChannelCard.js:59-66`).
- **3.2** — Botão **"Configurar webhook"** na fileira de ações (`ChannelCard.js:75-92`), só cloud, habilitado quando `can_set` e `match !== 'ok'`. No clique: `confirm("Apontar o webhook deste número para ESTA instância? Isso substitui o webhook atual na Meta.")` (D5) → `cloudSetWebhook(channel.id, expectedUrl)` → on success re-checa (re-`cloudWebhookStatus`) e mostra o novo estado; on error, exibe a mensagem da Meta. Estado de busy/erro local (espelha `handleRefresh`).
- **3.3** — Wiring: passar o que precisar via props em `ChannelCard` (`:12`) a partir de `ChannelsManager.js`. Decidir se o fetch é no próprio `WebhookHealthRow` (self-contained, recomendado) ou mergeado em `ChannelsManager` — **recomendado self-fetch** pra não tocar o caminho de status em massa (P1). Após um set bem-sucedido, opcionalmente disparar `onRefresh(channel)` pra atualizar as pills também.

**Pronto quando:** abrindo /channels com o canal `teste`, o card mostra "aponta pra outro domínio" (estado real atual); clicar "Configurar webhook" (com instância pública alcançável) repointa e a linha vira verde "apontando pra cá ✓". Modo escuro legível.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída
- **O que foi feito:** novo componente `web/static/js/components/channels/WebhookHealthRow.js` (self-fetch) + import e render em `ChannelCard.js` (logo abaixo das pills Conectado/Autenticado).
- **Como foi feito / decisões:** **self-fetch** (recomendado, P1) — o componente gate-a por `channel.provider === 'whatsapp_cloud'` e retorna `null` pros demais (GOWA/Telegram intactos, D1), então não precisou de novas props no `ChannelsManager`. Monta `expectedUrl = ${origin}/api/webhook/whatsapp_cloud/${channel.id}` (fórmula de `notices.js`) e chama `cloudWebhookStatus` no mount. Renderiza chip por `match` (verde `ok`; amber `wrong_domain`/`wrong_path`/`unset`; cinza `unknown` com `reason`), mostra `configured_url` quando ≠ ok, e hint "preencha WABA ID/Verify Token" quando `!can_set` (P2). Botão "Configurar webhook" só quando `can_set && match !== 'ok'`: `confirm()` (D5) → `cloudSetWebhook` → re-`check()`; erro da Meta exibido inline. Tints usam classes cobertas pelo fallback `html.dark` (green/amber -50/200, text -600/700) + semânticas `wa-*`.
- **Problemas / pendências:** validação visual no navegador (modo claro/escuro + fluxo real de set) é manual e fica para a F4.3 quando houver instância pública.
- **Verificação:** `node --check` OK em `WebhookHealthRow.js` e `ChannelCard.js`.

---

### Fase 4 — Verificação integrada `[🔴 depende de tudo]`

**Objetivo:** garantir que nada regrediu e que os 4 estados + o set funcionam nos dois bancos.

- **4.1** — Suíte em **SQLite**: `source venv/Scripts/activate && python tests/test_endpoints.py` + os testes novos da F0. Contagem de checagens não regrediu.
- **4.2** — Repetir contra **Postgres** (`WHATSBOT_TEST_DB_URL=postgresql+psycopg://…`, memória `postgres-dev-target`: usar **DB de teste isolado UTF8**, nunca o `whatsbot` real). Os endpoints só **leem** `channel_credentials` — sensível só a `get_all`; validar mesmo assim.
- **4.3** — Validação manual com `linux_start.sh` (instância pública / túnel):
  - Card do `teste`: estado **"aponta pra outro domínio"** (bate com o real apurado nesta sessão).
  - Botão "Configurar webhook" → confirma → linha vira **verde "apontando pra cá ✓"**; mandar uma mensagem real ao número e ver chegar no painel (prova ponta-a-ponta).
  - Canal **sem `waba_id`**: botão desabilitado com hint (P2).
  - GOWA/Telegram: **nenhuma** linha/botão de webhook cloud aparece (D1).
  - Modo escuro: linha e chips legíveis.

**Pronto quando:** checklist §8 todo marcado.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída (automatizada) · 🟡 pendente validação manual em instância pública
- **O que foi feito:** suíte legada `tests/test_endpoints.py` (SQLite) + `tests/endpoints/` + `tests/test_smoke.py` + a suíte nova P26 em SQLite e Postgres.
- **Como foi feito / decisões:** Postgres validado contra DB isolado UTF8 `whatsbot_p26_test` (criado via `template0` ENCODING UTF8 e dropado ao final — nunca tocou o `whatsbot` real, memória `postgres-dev-target`).
- **Problemas / pendências:** F4.3 (validação manual no navegador: estado real do canal `teste`, set ponta-a-ponta, modo escuro, canal sem `waba_id`) requer `linux_start.sh` + instância pública/túnel — fica para o usuário rodar; o código está pronto.
- **Verificação:** `python tests/test_endpoints.py` → **911 passed, 0 failed** (SQLite). `pytest tests/endpoints/ tests/test_smoke.py` → 25 passed. `pytest tests/endpoints/test_p26_cloud_webhook.py` → 9/9 em SQLite **e** em Postgres. `diff` das duas cópias de `routes.py` vazio. Sem migration criada.

---

## 6. Padrão de mock da Graph API nos testes

Os testes não podem bater na Meta real. Duas opções (escolher na F0, registrar no Status):

- **(a) Monkeypatch de `httpx.Client`** dentro do `routes.py` (como os testes existentes mockam GOWA/LLM em `tests/test_endpoints.py`): interceptar `GET …/{phone_number_id}` e `POST …/subscribed_apps`, devolvendo payloads fixos (incl. o caso `wrong_path` com `.../whatsapp_oficial_meta`).
- **(b)** Se F1 expuser os helpers como funções de módulo, mockar diretamente o `_creds`/o cliente httpx via `monkeypatch.setattr`.

Cobrir explicitamente: `ok`, `wrong_domain`, `wrong_path`, `unset` (response sem `webhook_configuration`/sem `override_callback_uri`), `unknown` (sem `access_token` ⇒ sem chamada). Para o set: sucesso (200) + erro Graph (`{"error":{"message":…}}`) + sem `waba_id`.

---

## 7. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Caminho de status em massa | Adicionar chamada Graph no `status()` deixaria `refreshStatuses` (`ChannelsManager.js:128`) lento / rate-limited | Endpoint **dedicado** + fetch **lazy** só pra cards cloud (P1, D-§5). `status()` intocado (teste 0.3 guarda). |
| Set repointa o webhook | "Configurar" rouba o webhook de onde estiver (ex.: instância da Luísa) | `confirm()` explícito (D5); o set só sucede se a Meta fizer handshake AQUI → prova positiva. |
| `verify_token` divergente | Se o override usar um `verify_token` que não bate com `channel_credential_repo.get(channel_id,"verify_token")` (`channel_webhook.py:277`), o handshake da Meta falha e o `POST` é rejeitado | Set **sempre** usa o `verify_token` salvo do canal; se ausente, `can_set=false` + hint (P2). |
| `waba_id` ausente | Read primário (por `phone_number_id`) funciona, mas **set** e o fallback `subscribed_apps` precisam de `waba_id` | Read degrada graciosamente; `can_set=false` quando sem `waba_id`; UI mostra hint "preencha o WABA ID". |
| Duas cópias do plugin divergirem | Editar só `storages/` e esquecer `assets/` → seed desatualizado em instalações novas | F1.5 obriga sincronizar; checklist confere `diff`. |
| Import frágil no plugin loader | `from .channels import …` quebra (`routes.py:13-17`) | Helpers Graph **inline** no `routes.py` (não importar a classe). |
| Segredos em log/resposta | Vazar `verify_token`/`access_token` | Respostas só com `configured_url/expected_url/match/can_set/reason`; sem log de token. |
| Estado `unset` mal interpretado | Mostrar "OK" quando na verdade não dá pra saber (webhook do App não é legível) | `unset` = amber "não verificável / pode estar no App", **nunca** verde. |
| SQLite vs Postgres | `get_all` roda nos dois dialetos | F4.2 valida em PG (DB isolado UTF8). |

---

## 8. Perguntas em aberto

- **P1 — A checagem vai dentro de `status()` ou em endpoint dedicado?**
  ✅ **DECIDIDO (2026-06-30):** endpoint dedicado + fetch lazy só pra cards cloud. `status()` é chamado em massa (`ChannelsManager.js:128`) e uma chamada Graph extra por canal a cada refresh é cara. (a) dentro de `status()` — simples, mas polui o caminho quente; (b) dedicado — recomendado. → (b).
- **P2 — `waba_id`/`verify_token` ausentes: bloquear ou gerar?**
  ✅ **DECIDIDO (2026-06-30):** **bloquear o set** com `can_set=false` + hint na UI ("preencha WABA ID e Verify Token em Editar"). (a) bloquear — seguro, recomendado; (b) **gerar** um `verify_token` aleatório e salvar no canal antes do set — conveniente, mas mexe em credencial sem o usuário ver. → (a) no MVP; (b) vira melhoria futura (botão "gerar verify_token" na tela de Editar canal).
- **P3 — Setar também override no nível do número (`phone_number`), além da WABA?**
  ✅ **DECIDIDO (2026-06-30):** **só WABA** (D2). O `webhook_configuration` do número já reflete o override da WABA (provado). Override por-número é redundante e tem API distinta. ⏸️ reabrir só se um número precisar de webhook diferente dos outros da mesma WABA (não é o caso). → WABA only.
- **P4 — Validar assinatura `X-Hub-Signature-256` do POST com `app_secret`?**
  ⏸️ **ADIADO:** fora de escopo. O core não valida assinatura hoje (`channel_webhook.py:285`). Endurecer isso é um plano de segurança separado; não bloqueia esta feature.

---

## 9. Apêndice — arquivos-chave

**Backend (plugin — DUAS cópias):**
- `storages/plugins/whatsapp_cloud/routes.py` — novos endpoints `webhook-status` / `set-webhook` / (`delete-webhook`) + helpers Graph.
- `assets/plugin_examples/whatsapp_cloud/routes.py` — **cópia idêntica** (sincronizar).
- (referência, não editar) `storages/plugins/whatsapp_cloud/channels.py:110` (`status`), `:760` (`_graph_error`); `storages/plugins/telegram/routes.py:32-97` (padrão).
- (referência) `server/routes/channel_webhook.py:268-349` (handshake + inbound); `db/repositories/channel_credential_repo.py` (`get`/`get_all`).

**Frontend:**
- `web/static/js/services/api.js` (após `:605`) — `cloudWebhookStatus`/`cloudSetWebhook`/(`cloudDeleteWebhook`).
- `web/static/js/components/channels/ChannelCard.js:12,49,75-92` — linha de saúde + botão (só cloud).
- `web/static/js/components/channels/WebhookHealthRow.js` (novo, se extraído) — componente self-fetch.
- (referência) `web/static/js/components/channels/notices.js:30` (fórmula da URL); `constants.js:27,139` (cred fields); `ChannelsManager.js:128,241` (status).

**Testes:**
- `tests/endpoints/test_p26_cloud_webhook.py` (novo) — read (4+ casos) + set + regressão de `status()`.

---

## 10. Checklist de verificação

- [x] F0: testes mock-Graph escritos (passam contra F1; entraram na mesma sessão, então não ficaram RED).
- [x] Read: `match` correto em `ok` / `wrong_domain` / `wrong_path` (caso real `.../whatsapp_oficial_meta` vs `teste`) / `unset` / `unknown`.
- [x] Read primário funciona **só com `phone_number_id`** (sem `waba_id`); `can_set=false` quando falta `waba_id` ou `verify_token`.
- [x] Set: `POST /{waba_id}/subscribed_apps` com `override_callback_uri` = `${origin}/api/webhook/whatsapp_cloud/${channel.id}` e `verify_token` salvo; re-read confirma `ok`.
- [x] Set falho propaga a mensagem da Meta (URL não pública / verify_token errado) sem 500.
- [x] `status()` / `GET /api/channels/{id}/status` **inalterado** (sem chamada Graph de webhook no caminho em massa) — teste 0.3 verde.
- [x] Linha/botão **só** aparecem em `whatsapp_cloud` (GOWA e Telegram intactos) — gate por `provider` no componente.
- [x] Confirmação antes de repointar (D5); segredos não vazam em resposta/log (teste verifica ausência de token na resposta).
- [x] As duas cópias de `routes.py` (`storages/` e `assets/`) idênticas (`diff` vazio).
- [x] `python tests/test_endpoints.py` verde em **SQLite** (911 passed).
- [x] Suíte P26 verde em **Postgres** (DB de teste UTF8 isolado, `WHATSBOT_TEST_DB_URL`).
- [ ] Tela legível no **modo escuro** (linha de saúde + chips) — usa classes cobertas pelo fallback `html.dark`; validação visual final pendente (F4.3).
- [x] Sem migration criada (nenhum schema change).
- [x] Cada bloco "Status de execução" preenchido.
