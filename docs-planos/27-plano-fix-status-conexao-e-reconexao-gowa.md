# Plano 27 — Corrigir status "Conectado" dos canais GOWA e a reconexão sem QR

> **Status:** IMPLEMENTADO (F0–F5 ✅; F6 parcial — SQLite verde, PG + manual pendentes) · **Data:** 2026-07-01 · **Escopo:** médio
> **Origem:** pedido do usuário — dois bugs no card de Canais (GOWA): (1) um número **realmente conectado** (envia/recebe mensagens) aparece como **"Desconectado"**; (2) um canal GOWA **já existente** não gera QR ao clicar "Conectar" (trava em "Gerando QR Code…"), enquanto um canal **novo** gera normalmente.
> **Método:** investigação nesta sessão — leitura do código real (`arquivo:linha` abaixo), sub-agente `Explore` para o fluxo de QR, e cruzamento com o print do usuário (canal `teste_gowa`: `Desconectado / Autenticado / 5544999990001`). **Sem GOWA vivo** nesta máquina — o gatilho exato do bug #2 (ALREADY_LOGGED_IN vs DEVICE_NOT_FOUND) fica marcado "a confirmar em log" (P1), mas o fix cobre os dois.
>
> **Raiz comum dos dois bugs:** o WhatsBot lê o estado do GOWA por **sinais errados/misturados** — deriva "connected" do **subprocesso** (não do device) e **funde** `is_connected` (websocket vivo) com `is_logged_in` (sessão pareada) num único `is_connected()`. Este plano separa os dois campos na fonte (`GOWAClient.connection_state()`), faz `GOWAChannel.status()` usar o estado real do device, conserta o gate do QR e adiciona **Reconectar/Desconectar por canal**.
>
> **Como usar este plano:** ao executar cada fase, preencha o bloco **"Status de execução"** dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.
> Legenda de estado: `⬜ Não iniciada` · `🟡 Em andamento` · `✅ Concluída` · `⛔ Bloqueada`.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 ✅ (2026-07-01) | **"Conectado"** = websocket do device vivo (`is_connected` do `/app/status`); **"Autenticado"** = sessão pareada (`is_logged_in`); **`needs_qr`** = `not logged_in`. **Parar** de usar `manager.is_running` para "connected". | `GOWAChannel.status()` (`channels/providers/gowa_channel.py:51-63`) passa a ler o device, não o subprocesso. |
| D2 ✅ (2026-07-01) | Adicionar `GOWAClient.connection_state()` como **nova** fonte com os dois campos separados. **Não** remover `is_connected()` — é usado em vários caminhos quentes (background loops, webhook, `get_own_number`) onde o significado "qualquer-liveness" é aceitável hoje; removê-lo arrisca regressão. | Método novo aditivo; refactors dos call sites do core ficam **fora de escopo** (P3). |
| D3 ✅ (2026-07-01) | O gate do QR (`get_qr_code`, `gowa/client.py:277`) **para de conflar**: só pula o QR quando o device está **de fato conectado** (websocket vivo), não quando apenas tem sessão salva. | Um device pareado-mas-offline deixa de abortar o QR silenciosamente; a decisão "QR vs reconnect vs logout+QR" passa a ser explícita (F2). |
| D4 ✅ (2026-07-01) | **Reconectar/Desconectar por canal** operam no **device certo** (via a instância do registry). O legado `/api/whatsapp/reconnect|logout` (`server/routes/whatsapp.py:43-78`), que só mexe no device singleton `whatsbot`, **permanece** (não remover) mas deixa de ser o caminho da UI de Canais. | Novos endpoints `POST /api/channels/{id}/reconnect` e `/logout`; novos métodos `reconnect()`/`logout()` em `GOWAChannel`. |
| D5 ✅ (2026-07-01) | Botões no card: **"Conectar"** (QR) quando `!logged_in` (como hoje); **"Reconectar"** quando `logged_in && !connected`; **"Desconectar"** quando `logged_in`. | `web/static/js/components/channels/ChannelCard.js:78-95` ganha as ações, **só para `provider === 'gowa'`**. |

**Princípio fixo (memória `refactor-rollout-context`):** o produto **não está em produção/distribuído** — pode-se refatorar de forma agressiva, sem stopgap de compatibilidade. **Exceção deste plano:** o GOWA singleton (`default`) dirige o pipeline legado de mensagens (`server/background.py`), então mudanças em `is_connected()`/na adoção de device do singleton precisam de cuidado (ver P2/P3).

---

## 1. Resumo executivo

O card de Canais mostra dois estados errados para GOWA. **Bug #2 (status):** `GOWAChannel.status()` deriva "connected" de `self._manager.is_running` (`gowa/manager.py:60-61`), que checa se **este** objeto `GOWAManager` tem o subprocesso — mas desde o plano 13 o subprocesso é criado pelo **plugin** via `ctx.spawn_subprocess` (`assets/plugin_examples/gowa/lifecycle.py:149`), **sem** chamar `gowa_manager.start()`. Logo `manager._managed` fica `None` para sempre → `is_running=False` → **todo canal GOWA mostra "Desconectado"**, mesmo enviando/recebendo. **Bug #1 (QR):** o pipeline de QR engole erros e devolve 204 (`get_qr_code` retorna `None` em erro; `svc.qr` vira `""`; a rota vira `Response(status_code=204)`), e o gate `if self.is_connected(): return None` (`gowa/client.py:277`) — com `is_connected()` **conflando** `is_logged_in` — aborta o QR de um device pareado-mas-offline; para o canal `default`, o cliente singleton `strict_device=False` ainda pode **adotar o device de outro canal** já logado (`gowa/client.py:150-154`).

A solução, com **uma raiz comum**: (a) `GOWAClient.connection_state()` lê o `/app/status` cru e devolve `{connected, logged_in}` separados; (b) `GOWAChannel.status()` usa isso (`connected←is_connected`, `logged_in←is_logged_in`, `needs_qr=not logged_in`); (c) o fluxo de conexão distingue **`!logged_in`→QR**, **`logged_in && !connected`→reconnect**, **QR travado por sessão velha→logout+QR**, e o gate do QR para de conflar; (d) botões **Reconectar/Desconectar por canal** com endpoints que agem no device certo.

---

## 2. Como funciona hoje (mapa)

### 2.1 Bug #2 — "connected" vem do subprocesso, não do device

| Ponto | `arquivo:linha` | O que faz |
|-------|-----------------|-----------|
| `GOWAChannel.status()` | `channels/providers/gowa_channel.py:51-63` | `connected = bool(self._manager and self._manager.is_running)` ← **errado**. `logged_in = self._client.is_connected()` (conflado). `own_phone` só se `logged_in`. |
| `GOWAManager.is_running` | `gowa/manager.py:60-61` | `self._managed is not None and self._managed.is_running()`. |
| `_managed` nasce `None` | `gowa/manager.py:52` | Só vira `ManagedProcess` dentro de `start()` (`:141-142`). |
| Subprocesso é do **plugin** | `assets/plugin_examples/gowa/lifecycle.py:149` | `ctx.spawn_subprocess(spec)` — comentário `:48-50` diz "**MINUS** the `gowa_manager.start()` call". Logo `gowa_manager._managed` nunca é setado. |
| `register_live` **não** chama `start()` | `app/services/channel_service.py:104-122` | Só `registry.instantiate(...)` + `add_channel` — "Pure constructor — no network". Então `GOWAChannel.start()`→`manager.start()` também não roda por aqui. |
| Card lê status live | `web/static/js/components/ChannelsManager.js:130-144` (`refreshStatuses`) e `:241-253` (`handleRefresh`) | Chamam `getChannelStatus` (`web/static/js/services/api.js:662-664`) → `GET /api/channels/{id}/status` → `svc.status` → `inst.status()`. |
| Renderização das bolinhas | `web/static/js/components/channels/ChannelCard.js:40-50` | `channel.connected ? 'Conectado' : 'Desconectado'` e `channel.logged_in ? 'Autenticado' : 'Não autenticado'`. |

⚠️ **Resultado observado:** `teste_gowa` mostra `Autenticado` + `📱 5544999990001` (⇒ `is_connected()`=True ⇒ `logged_in`) mas `Desconectado` (⇒ `manager.is_running`=False). Textbook do bug #2.

### 2.2 Bug #1 — o QR engole erro e o gate confla

| Ponto | `arquivo:linha` | O que faz |
|-------|-----------------|-----------|
| Frontend polling do QR | `web/static/js/components/channels/QRConnect.js:44-70` | `getChannelQR` (`api.js:669-681`) — em **204** retorna `null` → tela fica em "Gerando QR Code…" pra sempre. |
| Rota QR | `server/routes/channels.py:361-380` | `result = await svc.qr(...)`; se `not result` → `Response(status_code=204)` (`:376-378`). |
| Serviço QR | `app/services/channel_service.py:256-281` | Se sem instância, `register_live` on-demand (`:270`); chama `get_qr`/`qr`; **`return png or ""`** (`:281`) — `None` vira `""` → 204. |
| `GOWAChannel.get_qr()` | `channels/providers/gowa_channel.py:65-86` | Já **recria device sumido**: `self._client._device_ready = False; ensure_device()` (`:82-83`) → cobre `DEVICE_NOT_FOUND`. **Não** cobre sessão-velha. |
| Gate conflado no QR | `gowa/client.py:277` | `if self.is_connected(): return None` — `is_connected()` (`:192-200`) devolve `results.get("is_logged_in", results.get("is_connected", False))` (`:199`) ⇒ **um device pareado-mas-offline aborta o QR**. |
| `/app/login` engolido | `gowa/client.py:280-300` | Erro HTTP (ex.: `ALREADY_LOGGED_IN`) é logado em `_request` (`:91`) mas o retorno é `None` (`raise_on_error=False`) → sobe como 204. |
| Singleton adota device alheio | `gowa/client.py:150-154` | `default` reusa o cliente **singleton** (`gowa_channel.py:192-193`) com `strict_device=False` → "adota o primeiro device existente" (pode ser um logado ⇒ `is_connected()`=True ⇒ 204). |
| Canal ≠ default é **strict** | `channels/providers/gowa_channel.py:194-200` | `device_id = row["gowa_device_id"] or channel_id`; `strict_device=True` (só o próprio device, cria se faltar). |

### 2.3 Estado de conexão do GOWA (contrato do `/app/status`)

`GOWAClient.is_connected()` (`gowa/client.py:192-200`) lê `results = status.get("results", status.get("data", status))` e retorna `results.get("is_logged_in", results.get("is_connected", False))`. Ou seja o payload cru tem **os dois** campos: `is_connected` (socket) **e** `is_logged_in` (sessão). Hoje eles são fundidos num OR. `get_status()` (`:186-190`) já faz `ensure_device()` antes de `GET /app/status`.

### 2.4 Reconnect/logout que já existem

| Ponto | `arquivo:linha` | Observação |
|-------|-----------------|------------|
| `GOWAClient.reconnect()` / `logout()` | `gowa/client.py:733-743` | `GET /app/reconnect` / `GET /app/logout` + `self.reset()` (zera `_device_ready`). Operam no device do **próprio** cliente. |
| Rotas legadas | `server/routes/whatsapp.py:43-78` | `POST /api/whatsapp/reconnect|logout` mexem **só no singleton** `gowa_client` (device `whatsbot`). Não servem para `gowa_gCPhWavqAs` etc. |
| Services legados | `web/static/js/services/api.js:98-103` | `reconnect()`/`logout()` → rotas legadas singleton. |
| Contrato `Channel` | `channels/base.py:60-72` | Tem `start/stop/status/get_qr` — **não** tem `reconnect`/`logout`. Adicionar como métodos opcionais (default no-op) na base + implementação em `GOWAChannel`. |

---

## 3. Inventário / análise

| # | Item | `arquivo:linha` | O que falta / muda | Abordagem | Risco | Esforço |
|---|------|-----------------|--------------------|-----------|-------|---------|
| 1 | `GOWAClient.connection_state()` | `gowa/client.py` (após `:200`) | Novo método: lê `/app/status` cru → `{connected: is_connected, logged_in: is_logged_in}` separados; defensivo (`{}` em erro) | espelha `is_connected()` mas sem OR | baixo | S |
| 2 | Degate do QR | `gowa/client.py:277` | Trocar `if self.is_connected()` por checagem de **connected real** (`connection_state()["connected"]`) | usa item 1 | baixo | S |
| 3 | `GOWAChannel.status()` | `channels/providers/gowa_channel.py:51-63` | `connected←connection_state.connected`; `logged_in←…logged_in`; `needs_qr = not logged_in`; `own_phone` se `logged_in` | usa item 1 | médio | S |
| 4 | `GOWAChannel.get_qr()` — recuperação de sessão-velha | `channels/providers/gowa_channel.py:65-86` | Após recriar device: se `logged_in && !connected` → `reconnect()`; se ainda sem QR e o device tem sessão presa → `logout()` + re-`login` | usa items 1,2 + `client.reconnect/logout` | **alto** | M |
| 5 | `GOWAChannel.reconnect()` / `logout()` | `channels/providers/gowa_channel.py` (novo) + `channels/base.py:64` | Delegar a `self._client.reconnect()/logout()`; retorno `{ok, error}` | idem `send_text` try/except | baixo | S |
| 6 | Service `reconnect`/`logout` por canal | `app/services/channel_service.py` (após `:281`) | `async def reconnect(deps,row)`/`logout(deps,row)`: `register_live` on-demand (como `qr`, `:269-271`), chamar `inst.reconnect/logout` em `to_thread`; sentinelas `not_gowa`/`unavailable` | copia forma de `qr()` | baixo | S |
| 7 | Endpoints REST por canal | `server/routes/channels.py` (após `:381`) | `POST /api/channels/{id}/reconnect` e `/logout`; `permission_denied(request,"channel.manage")`; `channel_repo.get`→404 | espelha `channel_qr` (`:361-380`) | baixo | S |
| 8 | Services frontend | `web/static/js/services/api.js` (após `:664`) | `channelReconnect(id)`, `channelLogout(id)` | wrappers `request('POST', …)` | baixo | S |
| 9 | Botões no card + wiring | `ChannelCard.js:13,78-95` + `ChannelsManager.js:296-307` | "Reconectar"/"Desconectar" (só GOWA, gate D5); handlers `handleReconnect/handleLogout` + refresh | espelha `handleRefresh` (`:241-253`) | médio | M |
| 10 | Caracterização + testes | `tests/` | Cobrir status (bug #2) e o fluxo QR/reconnect/logout | mock/stub do client (ver §6) | médio | M |

### Falsos positivos descartados

| Hipótese | Veredito | Razão |
|----------|----------|-------|
| "Basta consertar `manager.is_running` (re-wire o `ManagedProcess` do plugin de volta no `gowa_manager`)." | **Descartado** | Semântica errada: "Conectado" deve refletir o **socket do device**, não a vida do subprocesso. Um subprocesso vivo com device offline **deve** mostrar "Desconectado". Derivar do `/app/status` (item 3) é a fonte correta; a vida do subprocesso é outro sinal (poderia virar `error`, fora de escopo). |
| "O QR não sai porque o device sumiu (`DEVICE_NOT_FOUND`)." | **Parcial / já tratado** | `get_qr()` já recria device sumido (`gowa_channel.py:82-83`). O que persiste é a **sessão-velha** + a **conflação** do gate — cobertos pelos items 2 e 4. |
| "Consertar `is_connected()` no lugar (remover o OR)." | **Descartado (arriscado)** | É usado em caminhos quentes do singleton — `status_poll_loop` (`server/background.py:41`, e o comentário explícito `:55` "GOWA conflates these"), webhook, `get_own_number` — onde "qualquer-liveness" hoje é aceitável. Método **novo** aditivo (D2); refactor desses call sites fica pra P3. |
| "Mudar o card pra ler flags persistidas (`channel_repo` `connected/logged_in`, `:19`)." | **Descartado** | O card já lê status **live** via `inst.status()` (`ChannelsManager.js:130-144`); as colunas do repo são fallback quando não há instância (`channel_service.py:248-253`). O bug está no `status()` live, não no fallback. |
| "Aumentar o polling do QR / mexer no `QRConnect.js`." | **Descartado** | O front está correto: 204 = "sem QR". O defeito é o backend devolver 204 quando deveria emitir QR/reconectar. |

---

## 4. Mudanças de infraestrutura / cuidados de camada

- **Backend puro (sem migration / sem schema change):** nada toca o banco. `channel_repo` já tem `connected/logged_in/own_phone` (`db/repositories/channel_repo.py:19`) — continuam como fallback; **não** criar revision Alembic.
- **`GOWAChannel` fica no core**, não no plugin: o arquivo físico é `channels/providers/gowa_channel.py` (o plugin `gowa` só re-exporta — `assets/plugin_examples/gowa/channels.py:12-17`). Então itens 3/4/5 mexem **só no core**; **não** há "duas cópias" a sincronizar aqui (diferente do plano 26). A única cópia relevante do plugin GOWA é `lifecycle.py` (não muda neste plano).
- **Contrato `Channel` (base):** adicionar `reconnect()`/`logout()` como métodos **opcionais default no-op** em `channels/base.py:64` (junto de `start/stop`), pra não quebrar os outros providers (telegram/cloud/test) que não os implementam. O service (item 6) usa `getattr(inst, "reconnect", None)` como o `qr` faz com `get_qr`/`qr` (`channel_service.py:272-275`).
- **Singleton `default` (cuidado):** o device `whatsbot` dirige o pipeline legado (`server/background.py`). O `logout()` no `default` **desloga a sessão principal** — comportamento desejado (é o que "Desconectar" faz), mas o botão precisa de `confirm()` (D5/§7). A adoção não-strict do singleton (`gowa/client.py:150-154`) é um risco separado tratado em P2.
- **Modo escuro:** os botões novos reusam as classes já existentes na fileira de ações (`ChannelCard.js:83-90`, `text-wa-text hover:bg-wa-hover`) — já legíveis nos dois temas. "Desconectar" pode usar a tinta de risco (`text-red-500`, coberta pelo fallback `html.dark`). Sem cor nova.
- **Sem segredo em log/resposta:** os endpoints de reconnect/logout devolvem só `{ok, error}` / `{message}`; nunca ecoar device_id de outro canal nem token.

---

## 5. Fases / Roadmap

### Diagrama de dependências

```
WAVE 0   F0 (caracterização — congela contrato + testes RED)         ← 🔴 sozinha
            │  (barreira: contrato de F0 destrava F1 e F4)
WAVE 1   F1 (gowa/client.py: connection_state + degate)  ·  F4 (api.js services)   ← 🟢 paralelas (arquivos distintos)
            │  (barreira: F2 precisa de F1)
WAVE 2   F2 (gowa_channel.py: status + get_qr + reconnect/logout)     ← 🔴 [dep F1]
            │  (barreira: F3 precisa dos métodos de F2)
WAVE 3   F3 (service + endpoints reconnect/logout)                    ← 🔴 [dep F2]
            │  (barreira: F5 precisa de F3 e F4)
WAVE 4   F5 (frontend: botões no card + wiring)                       ← 🔴 [dep F3, F4]
            │
WAVE 5   F6 (verificação SQLite+PG, dark, manual)                     ← 🔴 [dep tudo]
```

### Tabela de fases

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|------|------|-----------|-------|-------|----------------|
| 0 | F0 — Caracterização + contrato | testes | 🔴 | médio | testes de status/QR/reconnect existem e **falham pelas razões certas**; shape de `connection_state()` + endpoints congelado |
| 1 | F1 — `client.py`: `connection_state()` + degate do QR | backend | 🟢 `[bloqueia: F2]` | baixo | `connection_state()` devolve os 2 campos; `get_qr_code` não confla mais |
| 1 | F4 — Frontend: services `api.js` | frontend | 🟢 `[bloqueia: F5]` | baixo | `channelReconnect`/`channelLogout` batem nas rotas do contrato F0 |
| 2 | F2 — `gowa_channel.py`: status + get_qr + reconnect/logout | backend | 🔴 `[depende de: F1]` `[bloqueia: F3]` | alto | `status()` reflete o device; `get_qr()` recupera sessão-velha; métodos `reconnect/logout` existem |
| 3 | F3 — Service + endpoints por canal | backend | 🔴 `[depende de: F2]` | baixo | `POST /api/channels/{id}/reconnect|logout` respondem no device certo; testes F0 verdes |
| 4 | F5 — Botões no card + wiring | frontend | 🔴 `[depende de: F3, F4]` | médio | card GOWA mostra o estado certo e os botões conforme D5; ações funcionam |
| 5 | F6 — Verificação integrada | QA | 🔴 `[depende de: tudo]` | baixo | checklist §9 todo marcado |

**Disciplina (regras do repo):** verde a cada fase; **caracterização ANTES** (F0) de mexer no fluxo crítico de conexão; **um refactor por commit**; nunca avançar com teste vermelho não-explicado.

---

### Fase 0 — Caracterização + congelar contrato `[🔴 sozinha]`

**Objetivo:** fixar o shape de `connection_state()` e dos endpoints, e escrever testes que **falham hoje** e passam após F1–F3.

**Contrato (congelado):**

- `GOWAClient.connection_state() -> dict` → `{"connected": bool, "logged_in": bool}`. Lê `GET /app/status`; `connected = results.is_connected`, `logged_in = results.is_logged_in` (mesma extração de `results` de `is_connected()`, `gowa/client.py:197`). Erro/sem device ⇒ `{"connected": False, "logged_in": False}`.
- `GOWAChannel.status()` → `{connected, logged_in, needs_qr, own_phone, error}` com `connected/logged_in` do device e `needs_qr = not logged_in`.
- `POST /api/channels/{id}/reconnect` → `{"ok": bool, "data": {"message": str}|null, "error": str|null}`; `not_gowa`→400, `unavailable`→503, canal inexistente→404.
- `POST /api/channels/{id}/logout` → idem.

**Itens:**
- **0.1 [sequencial]** — Novo arquivo `tests/endpoints/test_p27_gowa_status_reconnect.py` (estilo `tests/endpoints/test_p26_cloud_webhook.py`). Stub do client GOWA que expõe `connection_state`/`reconnect`/`logout` controláveis (o `FakeGowaClient` em `tests/fakes.py:34` cai em `__getattr__` no-op → `connection_state()` retorna `None`; o teste precisa **injetar** um client com `connection_state` real — ver §6).
- **0.2 [paralelo]** — Teste **bug #2**: com `connection_state` retornando `{connected: True, logged_in: True}`, `GET /api/channels/{id}/status` devolve `connected=True` **mesmo** com `manager.is_running=False` (garante que não depende mais do subprocesso).
- **0.3 [paralelo]** — Teste **status separado**: `{connected: False, logged_in: True}` → card-data `connected=False, logged_in=True, needs_qr=False` (o caso `teste_gowa`).
- **0.4 [paralelo]** — Teste **reconnect/logout**: `POST /api/channels/{id}/reconnect` chama `client.reconnect()` (device certo) e `200`; `/logout` idem; provider não-GOWA → `400 not_gowa`; canal inexistente → `404`.
- **0.5 [paralelo]** — Teste **degate do QR**: `connection_state={connected:False, logged_in:True}` **não** curto-circuita `get_qr_code` (chega a `GET /app/login`), enquanto `{connected:True}` pula (retorna `None`). Guard do item 2.
- **0.6 [paralelo]** — Regressão: `GET /api/channels/{id}/status` continua devolvendo as chaves `connected/logged_in` (não quebra `tests/test_endpoints.py:1017-1018`) e o `mock_gowa_client` (MagicMock, `tests/test_endpoints.py:91`) **precisa** de `connection_state` stubado (senão devolve um Mock truthy) — ajustar o mock global aqui.

**Pronto quando:** os testes existem, rodam e **falham pelas razões certas** (método/rota ausente). Contrato registrado no Status.

#### Status de execução — Fase 0
**Estado:** ✅ Concluída
- **O que foi feito:** novo `tests/endpoints/test_p27_gowa_status_reconnect.py` (9 testes): status bug #2 (`{T,T}`/`{F,T}`/`{F,F}`), reconnect/logout no device certo + `not_gowa`(400)/404, degate do QR (paired-offline chega em `/app/login`; connected pula). `mock_gowa_client.connection_state` stubado em `tests/test_endpoints.py`.
- **Como foi feito / decisões:** opção (a) do §6 — stub `_StatefulGowaClient(FakeGowaClient)` com `connection_state/reconnect/logout/get_own_number` controláveis, injetado via `build_app(..., gowa_client=stub)`. Registro "live" do canal `default` forçado batendo em `POST /reconnect` (que faz `register_live` on-demand), já que `status()` não registra sozinho. Degate testado direto no `GOWAClient` (monkeypatch de `connection_state`/`_request`).
- **Problemas / pendências:** implementado junto com F1–F5 (não houve fase RED separada — testes já passam verdes).
- **Verificação:** `pytest tests/endpoints/test_p27_gowa_status_reconnect.py` → 9 passed.

---

### Fase 1 — `client.py`: `connection_state()` + degate do QR `[🟢 paralela com F4]`

**Objetivo:** separar os dois campos na fonte e parar de conflar no gate do QR.

- **1.1** — Novo método `connection_state()` em `gowa/client.py` (após `is_connected`, `:200`): faz `if not self._device_ready: self.ensure_device()`; `status = self.get_status()`; extrai `results` como em `:197`; retorna `{"connected": bool(results.get("is_connected", False)), "logged_in": bool(results.get("is_logged_in", False))}`; qualquer falha ⇒ `{"connected": False, "logged_in": False}`.
- **1.2** — `is_connected()` (`:192-200`) fica **como está** (compat — D2). *Opcional:* reimplementá-lo por cima de `connection_state()` retornando `connected or logged_in` (mantém o OR legado) — só se não alterar o comportamento observável dos call sites (`server/background.py:41`); **na dúvida, não tocar**.
- **1.3** — Degate no `get_qr_code` (`gowa/client.py:277`): trocar `if self.is_connected(): return None` por `if self.connection_state().get("connected"): return None` — só pula QR quando o **socket** está vivo (D3). Manter o resto do método intacto (`:280-300`).

**Pronto quando:** `connection_state()` devolve os 2 campos; testes 0.2/0.3/0.5 destravam do lado do client; `python tests/test_endpoints.py` não regride.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída
- **O que foi feito:** `GOWAClient.connection_state()` novo (após `is_connected`, `gowa/client.py`) → `{connected, logged_in}` separados, `{False,False}` em qualquer erro; degate em `get_qr_code` (`if self.connection_state().get("connected"): return None`).
- **Como foi feito / decisões:** `is_connected()` mantido intacto (D2) + docstring explicando a conflação; não reimplementado por cima (item 1.2 opcional descartado — "na dúvida, não tocar").
- **Problemas / pendências:** nenhuma.
- **Verificação:** testes de degate (0.5) verdes; `python tests/test_endpoints.py` 966 passed.

---

### Fase 4 — Frontend: services `api.js` `[🟢 paralela com F1]`

**Objetivo:** wrappers `request(...)` pros novos endpoints, contra o contrato F0 (independe da impl de F2/F3).

- **4.1** — Em `web/static/js/services/api.js` (após `getChannelStatus`, `:664`):
  - `export async function channelReconnect(id)` → `request('POST', \`/api/channels/${encodeURIComponent(id)}/reconnect\`)`.
  - `export async function channelLogout(id)` → `request('POST', \`/api/channels/${encodeURIComponent(id)}/logout\`)`.
  - Não confundir com os legados `reconnect()`/`logout()` singleton (`:98-103`) — mantê-los.

**Pronto quando:** as funções existem e batem nas rotas/parametrização do contrato F0 (revisão de código; integração real em F5).

#### Status de execução — Fase 4
**Estado:** ✅ Concluída
- **O que foi feito:** `channelReconnect(id)`/`channelLogout(id)` em `web/static/js/services/api.js` (após `getChannelStatus`) → `request('POST', /api/channels/{id}/reconnect|logout)`.
- **Como foi feito / decisões:** legados singleton `reconnect()`/`logout()` mantidos; nomes novos distintos.
- **Problemas / pendências:** nenhuma.
- **Verificação:** revisão de código; batem no contrato F0/F3 (integração real em F5).

---

### Fase 2 — `gowa_channel.py`: status + get_qr + reconnect/logout `[🔴 depende de F1]`

**Objetivo:** o canal passa a refletir o device real, recuperar sessão-velha no QR e expor reconnect/logout.

> **Internamente:** 2.1 (status) → 2.2 (get_qr) → 2.3 (reconnect/logout + base). Mesmo arquivo (+ `channels/base.py`).

- **2.1** — `GOWAChannel.status()` (`channels/providers/gowa_channel.py:51-63`): substituir por leitura de `self._client.connection_state()` — `connected` e `logged_in` **separados**; `needs_qr = not logged_in`; `own_phone = self._client.get_own_number()` quando `logged_in`. **Remover** a dependência de `self._manager.is_running` para `connected` (D1). Defensivo: qualquer exceção ⇒ `{connected:False, logged_in:False, needs_qr:False, own_phone:"", error:str}`.
- **2.2** — `GOWAChannel.get_qr()` (`:65-86`): manter a recriação de device (`:82-83`). Depois, ler `st = self._client.connection_state()`:
  - se `st["connected"]` → device já conectado, `return None` (nada a fazer);
  - se `st["logged_in"] and not st["connected"]` → sessão existe mas socket caiu ⇒ **`self._client.reconnect()`** e `return None` (o poll do `QRConnect` vai ver `logged_in`/`connected` e fechar); **não** emitir QR aqui;
  - senão (`not logged_in`) → `return self._client.get_qr_code()` (fluxo normal de QR).
  - **Sessão-velha presa** (device pareado mas `/app/login` recusa QR): se `get_qr_code()` voltar `None` **e** `not logged_in` **e** `not connected`, tentar **`self._client.logout()`** (limpa credencial) + `self._client.get_qr_code()` de novo. ⚠️ **Ver P1** — este é o ramo que só dá pra validar com GOWA vivo; deixar logado e defensivo.
- **2.3** — Novos `reconnect()`/`logout()` em `GOWAChannel` delegando a `self._client.reconnect()/logout()`, retorno `{"ok": True}` / `{"ok": False, "error": str}` (try/except como `send_text`, `:103-110`). Declarar os dois como **default no-op** em `channels/base.py:64` (`def reconnect(self)->dict: return {"ok": False, "error": "não suportado"}` e idem `logout`).

**Pronto quando:** `status()` reflete o device (teste 0.2/0.3 verdes); `get_qr()` segue a árvore de decisão; `reconnect/logout` existem. `python tests/test_endpoints.py` verde.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída
- **O que foi feito:** `GOWAChannel.status()` lê `connection_state()` (device, não `manager.is_running`), `needs_qr = not logged_in`, `own_phone` só se `logged_in`; `get_qr()` segue a árvore de decisão (connected→None; logged_in&&!connected→`reconnect()`+None; !logged_in→`get_qr_code()`; QR vazio+!logged_in&&!connected→`logout()`+retry); novos `reconnect()`/`logout()` em `GOWAChannel` + default no-op `{"ok":False,"error":"não suportado"}` em `channels/base.py`.
- **Como foi feito / decisões:** status defensivo (exceção → dict com `error=str`). Ramo logout+relogin só quando `get_qr_code()` já falhou e device nem connected nem logged_in (P1 — confirmar em log com GOWA vivo).
- **Problemas / pendências:** ramo de sessão-velha (P1) não testável sem GOWA vivo — coberto por decisão/defensivo, validação manual F6.4 pendente.
- **Verificação:** testes 0.2/0.3 verdes; `tests/test_endpoints.py` 966 + `tests/test_gowa_plugin.py` 50 passed.

---

### Fase 3 — Service + endpoints reconnect/logout por canal `[🔴 depende de F2]`

**Objetivo:** expor as ações por canal, agindo no device certo.

- **3.1** — Em `app/services/channel_service.py` (após `qr`, `:281`): `async def reconnect(deps, row)` e `async def logout(deps, row)` — espelhar `qr()`: gate `row.get("provider") != "gowa"` → `"not_gowa"`; `register_live` on-demand (`:269-271`); `method = getattr(inst, "reconnect"/"logout", None)`; `inst is None or method is None` → `"unavailable"`; `await asyncio.to_thread(method)`; devolver o dict `{ok,...}`.
- **3.2** — Em `server/routes/channels.py` (após `channel_qr`, `:381`): `@app.post("/api/channels/{channel_id}/reconnect")` e `/logout` — espelhar `channel_qr` (`:361-380`): `permission_denied(request, "channel.manage")`; `channel_repo.get`→404; chamar `svc.reconnect/logout`; mapear `"not_gowa"`→`_err(...,400)`, `"unavailable"`→`_err(...,503)`; senão `_ok(...)`.

**Pronto quando:** `POST /api/channels/{id}/reconnect|logout` chamam o `reconnect/logout` do device certo; testes 0.4 verdes; sem regressão.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída
- **O que foi feito:** `channel_service.reconnect/logout` (via helper `_session_action`, espelha `qr()`: gate `not_gowa`, `register_live` on-demand, `getattr(inst, action)` em `to_thread`); endpoints `POST /api/channels/{id}/reconnect|logout` (helper `_channel_session` em `routes/channels.py`): `permission_denied("channel.manage")`, 404 canal inexistente, `not_gowa`→400, `unavailable`→503, `!ok`→502, senão `_ok({"message":"ok"})`.
- **Como foi feito / decisões:** um helper compartilhado nos dois lados evita duplicar reconnect vs logout.
- **Problemas / pendências:** nenhuma.
- **Verificação:** testes 0.4 verdes (reconnect/logout device certo, not_gowa 400, 404).

---

### Fase 5 — Frontend: botões no card + wiring `[🔴 depende de F3+F4]`

**Objetivo:** o card GOWA mostra os estados certos e oferece Reconectar/Desconectar conforme D5.

- **5.1** — `ChannelCard.js`: adicionar props `onReconnect`, `onLogout` (`:13`). Na fileira de ações (`:78-95`), **só quando `channel.provider === 'gowa'`**:
  - **"Conectar"** (QR): como hoje, `canConnect = provider==='gowa' && !channel.logged_in` (`:18`) — mantém.
  - **"Reconectar"**: quando `channel.logged_in && !channel.connected`. `onClick=${() => onReconnect(channel)}`.
  - **"Desconectar"**: quando `channel.logged_in`. `confirm("Desconectar este número do WhatsApp? Vai precisar ler o QR de novo pra reconectar.")` no handler, tinta `text-red-500`.
- **5.2** — `ChannelsManager.js`: `handleReconnect(channel)` e `handleLogout(channel)` — espelhar `handleRefresh` (`:241-253`): `setBusyId`, chamar `channelReconnect/channelLogout`, on-success `refreshStatuses()` (pra atualizar as bolinhas), on-error `setError(...)`. Passar `onReconnect=${handleReconnect}` e `onLogout=${handleLogout}` no `ChannelCard` (`:296-307`).
- **5.3** — Sanidade: os botões não aparecem em telegram/cloud/test (gate por `provider`); `default` também mostra Desconectar (é o singleton — o `confirm` cobre o risco de deslogar a sessão principal).

**Pronto quando:** abrindo /channels, o canal realmente conectado mostra **"Conectado / Autenticado"** (bug #2 sumiu); um canal logado-mas-offline mostra "Reconectar" e reconecta; "Desconectar" desloga e o card volta a oferecer "Conectar" (QR). Modo escuro legível.

#### Status de execução — Fase 5
**Estado:** ✅ Concluída
- **O que foi feito:** `ChannelCard.js`: props `onReconnect`/`onLogout`; botões (só `provider==='gowa'`) — "Reconectar" quando `logged_in && !connected`, "Desconectar" quando `logged_in` (tinta `text-red-500`). `ChannelsManager.js`: import `channelReconnect`/`channelLogout`; `handleReconnect`/`handleLogout` (espelham `handleRefresh` + `refreshStatuses()` no sucesso; logout com `confirm()`); wiring no `ChannelCard`.
- **Como foi feito / decisões:** `confirm()` no `handleLogout` (D5) — cobre o risco de deslogar o singleton `default`. Botões reusam classes `wa-*`/`text-red-500` (legível dark).
- **Problemas / pendências:** validação visual manual (dark + fluxo real) pendente — F6.4.
- **Verificação:** revisão de código; gate por provider garante ausência em telegram/cloud/test.

---

### Fase 6 — Verificação integrada `[🔴 depende de tudo]`

**Objetivo:** garantir que nada regrediu e que os dois bugs sumiram nos dois bancos.

- **6.1** — **SQLite**: `source venv/Scripts/activate && python tests/test_endpoints.py` + `pytest tests/endpoints/test_p27_gowa_status_reconnect.py`. Contagem de checagens não regride.
- **6.2** — **Postgres** (`WHATSBOT_TEST_DB_URL=postgresql+psycopg://…`, memória `postgres-dev-target`: **DB de teste UTF8 isolado**, nunca o `whatsbot` real). Rodar a suíte nova + `tests/test_endpoints.py`.
- **6.3** — `python tests/test_gowa_plugin.py` (garante que o wiring do plugin/lifecycle não regrediu — ele já cobre `default`/`whatsapp_teste` com `set_status`, `:509-531`).
- **6.4** — Validação manual com `linux_start.sh` + GOWA vivo:
  - Número realmente conectado (`teste_gowa`): card mostra **"Conectado / Autenticado"** (bug #2 resolvido); manda/recebe msg e o painel reflete.
  - Canal logado-mas-offline: botão **"Reconectar"** → volta pra "Conectado" sem QR.
  - Canal deslogado (`default`/`gowa2`): **"Conectar"** → **QR aparece** (bug #1 resolvido); ler e conecta.
  - **Sessão-velha** (P1): confirmar com `WHATSBOT_GOWA_DEBUG=1` + `GET /api/gowa-logs` que `GET /app/login` deixou de travar (ver ramo logout+relogin da F2.2).
  - **"Desconectar"** → desloga; card volta a oferecer "Conectar".
  - telegram/cloud/test: **sem** botões GOWA (gate por provider).
  - Modo escuro: card e botões legíveis.

**Pronto quando:** checklist §9 todo marcado.

#### Status de execução — Fase 6
**Estado:** 🟡 Em andamento (automático verde; manual + PG pendentes)
- **O que foi feito:** rodadas SQLite das 3 suítes.
- **Como foi feito / decisões:** —
- **Problemas / pendências:** **F6.2 (Postgres) pendente** — sem `WHATSBOT_TEST_DB_URL`/senha nesta máquina; código é backend-puro sem migration (risco PG mínimo). **F6.4 (manual com GOWA vivo) pendente** — sem GOWA nesta sessão; inclui confirmar o ramo sessão-velha (P1) via `WHATSBOT_GOWA_DEBUG=1`.
- **Verificação:** SQLite — `pytest tests/endpoints/test_p27_gowa_status_reconnect.py` 9 passed; `python tests/test_endpoints.py` 966 passed; `python tests/test_gowa_plugin.py` 50 passed.

---

## 6. Padrão de stub do client GOWA nos testes

O `FakeGowaClient` (`tests/fakes.py:34`) resolve métodos desconhecidos via `__getattr__` como no-op retornando `None` (`:104-110`) — então `connection_state()` volta `None` e `GOWAChannel.status()` **precisa** ser defensivo (encapsula em try/except ou `(x or {})`). Duas opções nos testes (escolher na F0, registrar no Status):

- **(a)** Injetar um client stub que **define** `connection_state`/`reconnect`/`logout` (subclasse de `FakeGowaClient` ou objeto simples com esses métodos), via `build_test_app(..., gowa_client=stub)` (`tests/support.py:83,87`). Permite controlar `{connected, logged_in}` por teste.
- **(b)** No `tests/test_endpoints.py`, o `mock_gowa_client` é `MagicMock` (`:91`) — **stubar** `mock_gowa_client.connection_state = MagicMock(return_value={"connected": False, "logged_in": False})` (senão devolve um Mock truthy que quebra `bool(...)`); `reconnect`/`logout` já são MagicMock (`:101-102`).

Cobrir explicitamente: status `{T,T}` com `manager.is_running=False` (bug #2), status `{F,T}` (`needs_qr=False`), status `{F,F}` (`needs_qr=True`), degate do QR (`{T,*}` pula, `{F,T}` não), reconnect/logout no device certo + `not_gowa`/404.

---

## 7. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Ramo logout+relogin no QR (F2.2) | `logout()` no device certo é destrutivo; se o gatilho real for outro, pode limpar sessão à toa | Só entra quando `get_qr_code()` já falhou **e** `not logged_in and not connected`; **confirmar em log** (P1) antes de confiar; defensivo (try/except, nunca quebra o endpoint). |
| Singleton `default` deslogar a sessão principal | "Desconectar" no `default` mata o device `whatsbot` que dirige o pipeline legado | `confirm()` explícito (D5); é o comportamento pedido — o operador escolhe. |
| Adoção não-strict do singleton | `default` pode adotar device de outro canal já logado no QR (`gowa/client.py:150-154`) → 204 ou QR errado | **P2** — investigar tornar o `default` strict OU fazer `get_qr()` do `default` não adotar device logado. Não bloqueia o fix principal. |
| `connection_state()` adiciona um `/app/status` por status | `refreshStatuses` chama status de todos os canais a cada 8s (`ChannelsManager.js:150`) | `status()` já fazia `is_connected()` (=1 `/app/status`); `connection_state()` é **1 chamada** também (não soma). Sem piora. |
| `is_connected()` mantido (D2) | Divergência conceitual entre `is_connected()` (OR) e `connection_state()` | Documentar no docstring; refactor dos call sites do core é P3 (fora de escopo), sem regressão porque o comportamento atual é preservado. |
| MagicMock global sem `connection_state` | `bool(Mock())` é `True` → status falso-verde nos testes legados | Teste 0.6 stuba `mock_gowa_client.connection_state` explicitamente. |
| SQLite vs Postgres | Endpoints novos só leem `channels`/instanciam registry (sem SQL novo) | F6.2 valida em PG mesmo assim (DB isolado UTF8). |
| Sem GOWA vivo nesta sessão | Ramo de sessão-velha não testável aqui | Cobertura por stub (comportamento de decisão) + validação manual F6.4 + P1. |

---

## 8. Perguntas em aberto

- **P1 — Qual o gatilho exato do bug #1 (sessão-velha `ALREADY_LOGGED_IN` vs `DEVICE_NOT_FOUND`)?**
  ✅ **DECIDIDO (2026-07-01):** implementar o fluxo robusto que cobre **os dois** (recriar device sumido **e** logout+relogin de sessão presa) — F2.2. Confirmar o gatilho real com `WHATSBOT_GOWA_DEBUG=1` + `GET /api/gowa-logs` na validação manual (F6.4), sem bloquear a implementação. (a) só device-sumido — já tratado, insuficiente; (b) só sessão-velha — provável, mas não confirmado; (c) ambos — recomendado. → (c).
- **P2 — O canal `default` (singleton não-strict) deve virar strict / não adotar device logado no QR?**
  ⏸️ **ADIADO:** o singleton adota "o primeiro device existente" (`gowa/client.py:150-154`), o que pode fazer o QR do `default` prender no device de outro canal. (a) tornar o `default` strict (device fixo `whatsbot`) — mais correto, mas mexe no cliente que dirige o pipeline legado (risco de regressão no inbound); (b) `get_qr()` do `default` recusar adotar um device já logado; (c) deixar como está e cobrir com o botão manual. → decidir na execução após F6.4 confirmar se o `default` reproduz; **não bloqueia** os demais canais (strict).
- **P3 — Refatorar os call sites que usam `is_connected()` (background loops, webhook) para `connection_state()`?**
  ⏸️ **ADIADO:** fora de escopo. O comentário `server/background.py:55` ("GOWA conflates these") mostra que o core assume o OR de propósito hoje. Separar `is_connected`/`is_logged_in` nesses caminhos (ex.: mostrar QR só quando `!logged_in`, não `!connected`) é uma melhoria posterior; este plano só adiciona a fonte nova sem tocar os consumidores legados.
- **P4 — "Reconectar" deveria aparecer também para provider ≠ GOWA (telegram/cloud)?**
  ✅ **DECIDIDO (2026-07-01):** **não** — reconnect/logout são conceitos de sessão do WhatsApp linked-device (GOWA). Telegram/Cloud não têm QR/reconexão de socket; ficam com seus próprios fluxos (webhook/autoconfigure). Gate por `provider === 'gowa'` (D5). → só GOWA.

---

## 9. Apêndice — arquivos-chave

**Backend (core):**
- `gowa/client.py` — novo `connection_state()` (após `:200`); degate em `get_qr_code` (`:277`). (`reconnect`/`logout` já existem `:733-743`.)
- `channels/providers/gowa_channel.py` — `status()` (`:51-63`), `get_qr()` (`:65-86`), novos `reconnect()`/`logout()`.
- `channels/base.py:64` — `reconnect`/`logout` default no-op no contrato `Channel`.
- `app/services/channel_service.py` — `reconnect`/`logout` por canal (após `:281`; espelha `qr` `:256-281`).
- `server/routes/channels.py` — `POST /api/channels/{id}/reconnect|logout` (após `:381`; espelha `channel_qr` `:361-380`).
- (referência, não editar) `gowa/manager.py:60-61` (`is_running`); `assets/plugin_examples/gowa/lifecycle.py:149` (spawn do subprocesso); `server/routes/whatsapp.py:43-78` (legado singleton — permanece).

**Frontend:**
- `web/static/js/services/api.js` (após `:664`) — `channelReconnect`/`channelLogout`.
- `web/static/js/components/channels/ChannelCard.js:13,78-95` — botões (só GOWA, gate D5).
- `web/static/js/components/ChannelsManager.js:241-253,296-307` — `handleReconnect`/`handleLogout` + wiring.
- (referência) `web/static/js/components/channels/QRConnect.js:44-70` (polling — não muda); `api.js:669-681` (`getChannelQR` — 204→null).

**Testes:**
- `tests/endpoints/test_p27_gowa_status_reconnect.py` (novo) — status (bug #2), degate do QR, reconnect/logout.
- (referência) `tests/fakes.py:34` (`FakeGowaClient`), `tests/support.py:83` (`build_test_app`), `tests/test_endpoints.py:90-122` (mock global), `tests/test_gowa_plugin.py:509-531` (status seed).

---

## 10. Checklist de verificação

- [x] F0: testes de status/QR/reconnect escritos (9, verdes — implementação junto).
- [x] `connection_state()` devolve `{connected, logged_in}` separados; `{}`/erro ⇒ ambos `False`.
- [x] `GOWAChannel.status()` reflete o **device** (não `manager.is_running`): `{T,T}` com `is_running=True` (MagicMock) → `connected` segue o device (bug #2).
- [x] `needs_qr = not logged_in`; caso `teste_gowa` (`{F,T}`) → `connected=False, logged_in=True, needs_qr=False`.
- [x] `get_qr_code` só pula QR quando o **socket** está vivo (`connection_state().connected`), não por sessão salva (D3).
- [x] `get_qr()`: device sumido recriado; `logged_in && !connected` → reconnect; sessão-velha → logout+relogin (P1, confirmar em log — F6.4).
- [x] `POST /api/channels/{id}/reconnect|logout` agem no **device certo**; `not_gowa`→400, `unavailable`→503, inexistente→404.
- [x] Botões só em `provider === 'gowa'`; "Conectar/Reconectar/Desconectar" conforme D5; "Desconectar" com `confirm()`.
- [x] telegram/cloud/test: nenhum botão GOWA (gate por provider); status desses providers inalterado.
- [x] `python tests/test_endpoints.py` verde em **SQLite** (966, não regride) e a suíte P27 verde (9).
- [ ] Suíte P27 + `tests/test_endpoints.py` verdes em **Postgres** (DB de teste UTF8 isolado) — **pendente** (sem senha PG nesta máquina).
- [x] `python tests/test_gowa_plugin.py` verde (50 — wiring do plugin/lifecycle intacto).
- [ ] Modo escuro: card + botões legíveis — **validação visual pendente** (F6.4).
- [x] Sem migration criada (nenhum schema change); sem segredo em log/resposta.
- [x] Cada bloco "Status de execução" preenchido.
