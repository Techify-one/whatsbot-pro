# Plano 46 · Sub-plano 01 — Core & base Meta (habilitadores da Wave 0)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-09 · **Escopo:** médio · **Mestre:** [44-…-00-mestre](46-plano-canais-meta-email-widget-00-mestre.md)
> **Como usar:** preencha o "Status de execução" de cada fase ANTES da próxima. Este sub-plano é a **barreira** da Wave 0 — `01-A` e `01-B` desbloqueiam Messenger/Instagram; `01-D` desbloqueia o Widget.
> **Método:** verificado contra o código real (`arquivo:linha`).

## Objetivo
Criar os 4 habilitadores no core/base que os 4 canais reusam, **sem introduzir `if provider ==`** em lugar nenhum: (A) validação `X-Hub-Signature-256` opt-in no webhook do core; (B) base `MetaGraphChannel` + helper de URL pública de mídia; (C) hook de refresh de token no lifecycle; (D) infra de WebSocket público por-visitante + rota isenta de auth para o widget.

## Referências de decisão
D3 (assinatura), D4 (mídia por URL), D8 (refresh), D7/P1 (WS do widget), D11 (segredos em `channel_credentials`). Ver mestre §0.

---

## Fase 01-A — Validação `X-Hub-Signature-256` no webhook do core 🔴 [bloqueia 02, 03]

**Objetivo:** o POST do webhook do core passa a validar a assinatura HMAC-SHA256 sobre o **body cru** quando (e só quando) o provider/canal declara um `app_secret` — GOWA/telegram/whatsapp_cloud atuais ficam intactos.

**Como funciona hoje (⚠️ gap):**
- `server/routes/channel_webhook.py:291` `webhook_inbound()` faz `raw = await request.json()` (`:295`) e **nunca** valida assinatura. GET handshake (`:274`, valida `verify_token` em `:283`) é a única checagem.
- `channel_credential_repo.get(channel_id, key)` já é usado (`:283` p/ `verify_token`) — o mesmo acessor serve p/ `app_secret`.
- `whatsapp_cloud` **anuncia** `app_secret` em `routes.py:209` (lista `credential_keys`) mas **não** o usa e nem está nos `credential_fields` do descriptor (`channels.py:86-101`).

**Itens:**
1. `[sequencial]` **Caracterização primeiro**: adicionar teste que exercita o POST atual de GOWA/telegram/whatsapp_cloud e confirma o comportamento (200, parse, dispatch) — para provar que 01-A não regrede. (`tests/` — molde dos testes de webhook existentes.)
2. `[sequencial]` Ler o body cru **antes** do parse: em `webhook_inbound` (`channel_webhook.py:292-295`), trocar por `body_bytes = await request.body()` e depois `raw = json.loads(body_bytes or b"{}")`. Guardar `body_bytes` p/ o HMAC.
3. `[sequencial]` Costura opt-in no `Channel` (`channels/base.py`): novo método default
   ```python
   def verify_inbound_signature(self, raw_body: bytes, headers: Mapping[str,str]) -> bool:
       return True   # default: sem verificação (GOWA/telegram)
   ```
   Meta providers sobrescrevem: pegam `app_secret = self._cred("app_secret")`; se vazio → `True` (não configurado ⇒ não bloqueia, log de aviso); senão computam `sha256=`+`hmac.new(app_secret, raw_body, sha256).hexdigest()` e `hmac.compare_digest` contra o header `X-Hub-Signature-256`.
4. `[sequencial]` No core (`channel_webhook.py`, após resolver `inst` em `:342`, antes de `parse_inbound` em `:347`): `if inst and not inst.verify_inbound_signature(body_bytes, request.headers): return _ok({"status":"bad_signature"})` (responde 200 p/ Meta não re-tentar em loop; loga WARNING).
5. `[paralelo]` Adicionar `app_secret` como `credential_field` (`type:secret`, `required:False`) no descriptor dos providers Meta (feito nos sub-planos 02/03) — aqui só o gancho.

**Pronto quando:** um POST com `X-Hub-Signature-256` inválido num canal que tem `app_secret` → resposta `bad_signature` e nada é ingerido; assinatura válida passa; canais **sem** `app_secret` (GOWA/telegram) seguem byte-idênticos (teste de caracterização verde antes e depois).

#### Status de execução — Fase 01-A
**Estado:** ✅ Concluída (2026-07-22)
- **O que foi feito:** `Channel.verify_inbound_signature(raw_body, headers) -> bool` (default `True`) em [channels/base.py](../channels/base.py); o POST `/api/webhook/{provider}/{channel_id}` ([server/routes/channel_webhook.py](../server/routes/channel_webhook.py)) passou a ler `await request.body()` e derivar o dict com `json.loads` dos MESMOS bytes, e chama o hook (em `asyncio.to_thread`) depois de resolver `inst` e ANTES do `parse_inbound`; assinatura inválida ⇒ resposta 200 `{"status":"bad_signature"}` sem ingerir nada.
- **Como foi feito / decisões:** hook no base `Channel` (NÃO capability nova) — a decisão "verifica ou não" é do provider e depende de credencial (`app_secret`), não é estática. `MetaGraphChannel` implementa o HMAC uma vez para todos os providers Meta; canal Meta SEM `app_secret` retorna `True` + WARNING (não deixa a caixa muda). Exceção no hook ⇒ trata como assinatura inválida (fail-closed). O `filter.webhook.payload` continua rodando antes (o plugin ainda vê o payload cru).
- **Problemas / pendências:** o corpo que não é um objeto JSON (lista/escalar) agora vira `{}` em vez de estourar mais adiante — endurecimento, não regressão.
- **Verificação:** [tests/test_meta_graph_core.py](../tests/test_meta_graph_core.py) (default permissivo, HMAC válido/adulterado/segredo errado/header ausente) + [tests/test_facebook_messenger.py](../tests/test_facebook_messenger.py) (rota real: `bad_signature` em assinatura errada/ausente, `received` na válida, handshake GET com `verify_token`, e caracterização do POST do GOWA continuando 200/ingerindo).

---

## Fase 01-B — Base `MetaGraphChannel` + helper de URL pública de mídia 🟢

**Objetivo:** fatorar o que IG e Messenger compartilham (D2) para os sub-planos 02/03 só declararem host/token/id/escopos, e resolver o envio de mídia por URL pública (D4).

**Itens:**
1. `[paralelo]` Criar a base (dentro de cada plugin como mixin importável, OU um módulo compartilhado copiado — decidir em P-01B1). Ela concentra:
   - `_graph_base()` = `f"https://{host}/{graph_version}"` (host difere: `graph.facebook.com` × `graph.instagram.com`).
   - `_post_message(payload)` (molde `whatsapp_cloud/channels.py:207`), erro via `_graph_error` (`:817`).
   - `parse_inbound` que caminha **`entry[].messaging[]`** (NÃO `changes[].value` do whatsapp_cloud) → `InboundEvent(kind=message|reaction|receipt, chat_id=sender.id, external_msg_id=message.mid, direction=out se is_echo)`. Comentários/story ficam p/ fase 2 do 03.
   - `verify_inbound_signature` (usa 01-A) com o `app_secret`.
   - `download_media(url_or_id, dest_dir, ...)` que baixa direto da `payload.url` (mais simples que o 2-step do whatsapp_cloud `:605`).
2. `[paralelo]` Helper de **URL pública de mídia** no core/base: dado um `media_path` local (ex.: `statics/senditems/x.jpg`), montar `f"{public_base_url}/{media_path}"`. `public_base_url` já é capturado/persistido no `GET /api/config` (config key global — ver CLAUDE.md "API REST"). `send_media` dos providers Meta manda `{attachment:{type, payload:{url: <link público>, is_reusable:true}}}`.
3. `[paralelo]` Unit test do parser `messaging[]` com fixtures reais (texto, anexo, reaction, read, is_echo) — sem rede.

**Pronto quando:** `parse_inbound` converte um payload `entry[].messaging[]` de exemplo em `InboundEvent` correto (texto, anexo com URL, echo com `direction=out`); o helper de URL pública devolve um link `https://…` a partir de um path local + `public_base_url`.

#### Status de execução — Fase 01-B
**Estado:** ✅ Concluída (2026-07-22)
- **O que foi feito:** [channels/providers/meta_graph.py](../channels/providers/meta_graph.py) — `MetaGraphChannel` com `_graph_base()` (host por subclasse), `_cred`/`_channel_config` (config do canal com cache 30s), `appsecret_proof`, `_post_message` em `/me/messages`, `verify_inbound_signature`, `parse_inbound` sobre `entry[].messaging[]` (texto, anexos, echo, reaction, delivery/read, postback, location), `download_media` (1 GET direto na URL do CDN), `resolve_sender_name` (cache 6h), `send_text`/`send_media`/`react`/`mark_read`/`send_presence`. Helper de URL pública em [channels/media_urls.py](../channels/media_urls.py) (`public_media_url`, ancorado no componente `statics/`).
- **Como foi feito / decisões:** **P-01B1 resolvida** — módulo compartilhado no CORE (`channels/providers/meta_graph.py`), importável pelos plugins pelo mesmo caminho estável de `channels.base`; a classe é abstrata (sem `provider`/descriptor), então **não registra provider nenhum** sozinha. Anexo inbound carrega a URL do CDN em `media_extras["media_id"]` — assim o resolver de mídia do core (`download_media(media_id, …)`) funciona sem mudança e a URL (que expira) nunca é persistida. No echo os papéis invertem: `chat_id` é sempre o HUMANO.
- **Problemas / pendências:** caption de anexo vai como mensagem de texto de follow-up (a Send API não aceita caption no attachment).
- **Verificação:** [tests/test_meta_graph_core.py](../tests/test_meta_graph_core.py) — 16 casos, sem rede (parser com fixtures reais + URL pública + `appsecret_proof`).

---

## Fase 01-C — Hook de refresh de token no lifecycle 🟢 (usado pelo 03)

**Objetivo:** um loop de background genérico, gated por capability, que renova tokens que expiram — usado pelo IG (60 dias, D8); no-op p/ quem não declara.

**Itens:**
1. `[paralelo]` Adicionar em `ChannelCapabilities` (`channels/base.py:18`) um marcador `token_refresh: bool = False` (ou um método `needs_token_refresh()`), e no `Channel` um método default `refresh_token_if_needed() -> None` (no-op).
2. `[paralelo]` Padrão de loop: o **plugin** que precisa registra via `ctx.spawn_task("token_refresh", loop)` no seu `lifecycle.setup(ctx)` (molde `telegram/lifecycle.py:181-195`). O loop varre os canais do provider a cada N min e chama `inst.refresh_token_if_needed()`. **Não** é preciso código de core novo além do capability flag — o supervisor (`plugins/context.py:316` / `runtime/supervisor.py`) já cancela no disable via `stop_owner`.
3. `[paralelo]` Documentar o contrato: `refresh_token_if_needed` só renova quando o token está válido, ≥24h de idade e <10 dias p/ expirar (regra do IG — ver 03), persistindo `access_token`+`expires_at` via `channel_credential_repo.set`.

**Pronto quando:** um plugin fictício que declara a capability e registra o loop tem a task supervisionada iniciada e cancelada no disable; provider sem a capability não inicia loop nenhum.

#### Status de execução — Fase 01-C
**Estado:** ✅ Concluída (2026-07-22)
- **O que foi feito:** `ChannelCapabilities.token_refresh: bool = False` + `Channel.refresh_token_if_needed() -> None` (no-op) em [channels/base.py](../channels/base.py), com o CONTRATO documentado na docstring (só renova token válido, ≥24h de idade, <10 dias p/ expirar; persiste `access_token`+`expires_at` via `set_credential`; nunca levanta).
- **Como foi feito / decisões:** nenhum código de core além do flag+hook, como o plano previu: quem precisa registra o loop no próprio `lifecycle.setup(ctx)` via `ctx.spawn_task("token_refresh", …)` e o supervisor já cancela no disable (`stop_owner`). Provider sem a capability não inicia loop nenhum. O consumidor real é o Instagram (sub-plano 03).
- **Problemas / pendências:** nenhuma — sem consumidor ainda; o loop concreto entra no 03.
- **Verificação:** [tests/test_meta_graph_core.py](../tests/test_meta_graph_core.py) (`test_token_refresh_capability_defaults_off_and_hook_is_noop`).

---

## Fase 01-D — Infra de WebSocket público por-visitante + rota isenta 🟢 [bloqueia 05]

**Objetivo:** dar ao widget (sub-plano 05) o que o core não tem: um caminho público (isento de auth do operador) para o navegador **entrar** e um WebSocket por-visitante para a resposta **sair** — sem nunca expor o `/ws` do operador (D7/P1).

**Como funciona hoje:**
- `_AUTH_EXEMPT_PREFIXES` (`server/app.py:447`) = `("/static/", "/statics/", "/plugins/", "/api/auth/", "/api/webhook/")`. `/plugins/` (estático) **já é público**; `/api/plugins/<id>/…` **não** (está sob `/api/`, protegido em `:488`).
- `/ws` (`server/routes/websocket.py:16`) é o socket do **operador** (fan-out de todas as conversas) — proibido ao visitante.
- `ws_manager.broadcast` (`server/state.py:68`) fala com os sockets do painel.

**Itens (decidir dono da infra em P-01D1 — core vs plugin):**
1. `[sequencial]` **Rota pública de inbound do widget:** ou (a) montar sob `/api/webhook/website/{channel_id}` (já isento, core-owned — reusa `channel_webhook.py`), ou (b) adicionar um prefixo novo a `_AUTH_EXEMPT_PREFIXES` (ex.: `/api/plugins/website/public/`) para o router do plugin. Recomendação: (a) para o POST de mensagem (reusa `parse_inbound`→`ingest_event`), (b) só se precisar de endpoints extras (config/typing/upload).
2. `[sequencial]` **WebSocket por-visitante:** um endpoint WS **dedicado** (ex.: `/api/plugins/website/ws?session=<token>`), auth-exempt, com um registry `{session_token → set[WebSocket]}` dono do plugin. Conexão valida o session token (e origem contra `allowed_domains`) antes de aceitar. **Não** reusar `/ws`.
3. `[sequencial]` **Ponte de saída:** como `Channel.send_text` (que roda no `OutboundRouter`) alcança esse registry? Threadar o registry/uma função `deliver(session_token, payload)` para a instância do canal (via `registry`/ctx), OU usar `plugins.context.broadcast` com um evento por-sessão consumido só pela conexão daquela sessão. Documentar a costura escolhida (é o ponto onde o contrato `Channel` "dobra" — outbound é dono pelo WhatsBot, não por uma API externa).
4. `[sequencial]` **Fila offline:** se não há socket conectado (aba fechada), a mensagem já é persistida pelo pipeline normal; ao reconectar (`GET config`/reabrir), o widget re-sincroniza as últimas N (molde Chatwoot `syncLatestMessages`).

**Pronto quando:** dois "visitantes" (dois session tokens) conectam ao WS do widget; um `broadcast`/`deliver` para o token A chega **só** no socket A; o token B não vê nada; o `/ws` do operador não é acessível com um session token de visitante.

#### Status de execução — Fase 01-D
**Estado:** ✅ Já concluída anteriormente (junto com o sub-plano 05 — widget)
- **O que foi feito:** a infra já está no repo: exceção de auth GENÉRICA por convenção `/api/plugins/<id>/public/…` ([server/app.py](../server/app.py):50 e :539) e o hub de WebSocket por-visitante do plugin `website` ([assets/plugin_examples/website/bridge.py](../assets/plugin_examples/website/bridge.py) `WsHub`, rota `/public/ws` em `routes.py`).
- **Como foi feito / decisões:** **P-01D1 resolvida na direção recomendada** — dono é o PLUGIN (registry `{session_token → set[WebSocket]}` + `deliver`), o core só cede a exceção de auth. `send_text` entrega pelo hub (`run_coroutine_threadsafe`, mesmo padrão de `plugins.context.broadcast`); o `/ws` do operador nunca é tocado.
- **Problemas / pendências:** nenhuma — nada foi alterado nesta execução.
- **Verificação:** [tests/test_website_widget.py](../tests/test_website_widget.py) (isolamento entre sessões + auth-exempt + CSP).

---

## Riscos específicos deste sub-plano

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| 01-A no fluxo compartilhado | Quebrar GOWA/telegram | Opt-in por `app_secret`; caracterização verde antes/depois. |
| HMAC sobre dict parseado | Não bate | Só sobre `await request.body()` cru, antes do `json.loads`. |
| 01-D vazar eventos | Visitante ver conversa de outro | WS dedicado por-sessão; jamais o `/ws` do operador; filtrar por `session_token`. |
| Ponte outbound | `Channel.send_text` não tem handle p/ o navegador | Threadar registry/`deliver` na instância; documentar como a exceção do contrato. |

## Perguntas em aberto (deste sub-plano)
- **P-01B1:** base `meta_graph` = mixin compartilhado (um módulo em `channels/providers/`) ou copiado em cada plugin? ⏸️ A decidir na execução; recomendo módulo `channels/providers/meta_graph.py` importável pelos dois plugins (sem virar auto-install).
- **P-01D1:** dono da infra do widget = core (rota+WS no core, provider-agnóstico) ou plugin `website`? Recomendo **plugin** dono do WS+registry, usando só a exceção de auth do core.

## Checklist
- [x] Caracterização GOWA/telegram/whatsapp_cloud verde antes e depois de 01-A.
- [x] POST forjado rejeitado; válido aceito; sem `app_secret` inalterado.
- [x] Unit test do parser `messaging[]` (01-B) verde (pytest).
- [x] Hook/capability de refresh (01-C) — o loop concreto (e seu teste de start/cancel) entra no sub-plano 03, que é o único consumidor.
- [x] Isolamento de sessão do WS do widget (01-D) testado (já existente).
- [x] Suíte `tests/` verde no Postgres.
