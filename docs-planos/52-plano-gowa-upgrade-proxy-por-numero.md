# Plano 52 — Upgrade GOWA v8.11.0 + proxy de saída por número (processo dedicado por canal)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-16 · **Escopo:** médio-grande
> **Origem:** pedido do usuário ("atualizar o sistema para a nova versão" + "colocar um proxy — webshare.io por exemplo — para cada número do gowa que eu for conectar"). **Método:** leitura direta + 2 sub-agentes `Explore` (wiring de subprocess/webhook e camada de canais), diffs do upstream via `gh api` (PR #664 do GOWA, `.env.example`/`root.go` da tag v8.11.0), tudo com `arquivo:linha` verificado.
> O GOWA embarcado é v8.8.0; a v8.11.0 (13/jul/2026) traz `WHATSAPP_PROXY` (proxy outbound do WebSocket do WhatsApp, SOCKS5/HTTP/HTTPS), suporte a passkey (fluxo "Shortcake" da Meta, v8.10.0) e fixes de estabilidade multi-device. **Porém o proxy upstream é global POR PROCESSO** — e o WhatsBot roda N números como N devices num ÚNICO processo GOWA. Para proxy POR NÚMERO, canais com proxy configurado ganham um **processo GOWA dedicado** (com `WHATSAPP_PROXY` próprio via env); canais sem proxy continuam no processo compartilhado (zero regressão). A costura de storage para isso **já existe e está dormente**: `channels.gowa_isolation` (`shared|dedicated_process`), criada na migration 0011 e nunca cabeada.
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | **Upgrade para GOWA v8.11.0** ✅ (2026-07-16) | Fase 0. Pré-requisito de qualquer opção de proxy (o `WHATSAPP_PROXY` só existe a partir da v8.11.0) e traz o suporte a passkey (v8.10.0) + fix de slot↔companion por AD JID (v8.11.0), que beneficia diretamente o modelo N-devices-num-processo do WhatsBot. |
| D2 | **Proxy por número via processo GOWA dedicado por canal** — sem fork do GOWA ✅ (2026-07-16) | Waves 1–2. O upstream não tem proxy per-device (verificado: `config.WhatsappProxy` é global, aplicado a TODOS os devices em `device_manager.go`; nenhum issue/PR upstream pede per-device; PR #400 de multi-instância foi fechado). |
| D3 | **Arquitetura híbrida**: canal SEM proxy fica no processo compartilhado de hoje; canal COM proxy ganha processo dedicado | Zero regressão para instalações existentes (nenhum número precisa re-parear ao atualizar). Só quem LIGA um proxy re-pareia aquele número (esperado: a identidade de IP muda de qualquer forma). |
| D4 | **Provedor de proxy**: IPs **estáticos e dedicados** (1 IP fixo por número — static residential do Webshare ou outro provedor). Rotating residential está descartado (sticky ≤30min; IP muda a cada reconexão = padrão de ban) ✅ (2026-07-16) | O campo aceita `socks5://user:pass@host:porta` ou `http(s)://user:pass@host:porta` (formatos que o `SetProxyAddress` do whatsmeow aceita). O plano não valida o provedor, só o esquema da URL. |
| D5 | **Segredo do proxy nunca em argv** | `gowa/manager.py:103` loga o cmd completo (`logger.info("Starting GOWA ...: %s", " ".join(cmd))`) e argv aparece em `ps aux`. O proxy entra por **env `WHATSAPP_PROXY`** no `SubprocessSpec.env` (campo já existe — `runtime/subprocess_service.py:141`), nunca por `--whatsapp-proxy`. |

---

## 1. Resumo executivo

Duas entregas encadeadas. **(1) Upgrade GOWA 8.8.0 → 8.11.0**: a versão está pinada em [scripts/_common.sh:14](../scripts/_common.sh#L14) e [Dockerfile:3](../Dockerfile#L3); o binário Windows é commitado ([.gitignore](../.gitignore) tem `!bin/gowa.exe`); não há breaking change nos endpoints que [gowa/client.py](../gowa/client.py) usa (a única mudança semântica — logout mantém o device slot, v8.10.0 — já é tolerada pelo fluxo de QR em [gowa_channel.py:184-237](../channels/providers/gowa_channel.py#L184)). **(2) Proxy por número**: como o `WHATSAPP_PROXY` do GOWA é por processo, canais com proxy configurado passam a rodar num **processo GOWA dedicado** — porta própria, `cwd` próprio (isola `whatsapp.db`/`chatstorage.db`, que NÃO têm override de path — verificado no `root.go` v8.11.0), env `WHATSAPP_PROXY` própria e webhook próprio na rota por canal **que já existe** ([channel_webhook.py:291](../server/routes/channel_webhook.py#L291) `/api/webhook/{provider}/{channel_id}`). Um **reconcile loop** no plugin gowa (dono do subprocess desde o plano 13) compara estado desejado (canais gowa ativos com credencial `proxy_url`) vs processos vivos e faz spawn/stop/restart — declarativo, auto-curativo, sem `if provider ==` no core. O campo proxy entra como `credential_field` tipo `secret` no descriptor do provider (mascaramento na borda + input password **de graça** — [channel_service.py:116-130](../app/services/channel_service.py#L116), [DescriptorFields.js:94](../web/static/js/components/channels/DescriptorFields.js#L94)).

---

## 2. Como funciona hoje (mapa)

| Peça | Onde | Comportamento atual |
|------|------|---------------------|
| Versão GOWA pinada | [scripts/_common.sh:14](../scripts/_common.sh#L14) (`GOWA_VERSION=8.8.0`, fonte p/ linux+macos), [Dockerfile:3](../Dockerfile#L3) (`ARG GOWA_VERSION=8.8.0`), [gowa/client.py:47](../gowa/client.py#L47) (docstring) | Launchers só baixam se `bin/gowa` **não existir** ([linux_start.sh:63-93](../linux_start.sh#L63)) — sem checagem de versão. `bin/gowa.exe` é trackeado no git; `windows_start.bat:172-181` só valida existência (não baixa). |
| Processo GOWA (único) | [assets/plugin_examples/gowa/lifecycle.py:73-177](../assets/plugin_examples/gowa/lifecycle.py#L73) `setup(ctx)` | Plugin gowa é o dono: monta `SubprocessSpec(name="gowa", cmd=gm._build_cmd(), signature=binary, ...)` (:131-144, **sem `cwd` nem `env`** — herda do pai) e spawna via `ctx.spawn_subprocess` (:150). |
| Argv do GOWA | [gowa/manager.py:63-93](../gowa/manager.py#L63) `_build_cmd()` | `rest --port <p> --webhook <url> --webhook-events ... --presence-on-connect available --os "Techify - WhatsBot" [--debug=true]`. ⚠️ O cmd inteiro é **logado** em [manager.py:103](../gowa/manager.py#L103). |
| Porta do GOWA | [main.py:48](../main.py#L48) / [server/dev.py:40](../server/dev.py#L40) `settings.get("gowa_port", 3000)`; ⚠️ default REGISTRADO é **64996** ([config/settings.py:222](../config/settings.py#L222)); env override `WHATSBOT_GOWA_PORT` ([settings.py:44](../config/settings.py#L44)) | Uma porta só, para o processo único. |
| Webhook outbound do GOWA | [main.py:55-56](../main.py#L55) / [server/dev.py:47-50](../server/dev.py#L47) | `http://127.0.0.1:<web_port>/api/webhook/gowa/default` — path fixo `default` para TODOS os devices. |
| Webhook inbound (por canal) | [server/routes/channel_webhook.py:291-355](../server/routes/channel_webhook.py#L291) `POST /api/webhook/{provider}/{channel_id}` | ✅ Rota por canal **já existe**. Para gowa, como tudo chega em `.../gowa/default`, o canal real é re-resolvido pelo `device_id` do envelope v8 (:323-333, `channel_repo.get_gowa_channel_for_device`). |
| N números = N devices | [channels/providers/gowa_channel.py:368-385](../channels/providers/gowa_channel.py#L368) `build_gowa_channel` | Canal `default` usa o client singleton; os demais ganham `GOWAClient` próprio (`strict_device=True`) **na mesma porta** (:382 `port=getattr(gowa_client, "port", 3000)`) → mesmo processo. |
| SQLite de sessão do GOWA | grep `db-uri` no repo = **zero**; [runtime/subprocess_service.py:199](../runtime/subprocess_service.py#L199) `Popen(..., cwd=spec.cwd)` com `cwd=None` | GOWA grava `storages/whatsapp.db` + `storages/chatstorage.db` **relativos ao cwd herdado** (raiz do repo). ⚠️ `ChatStorageURI` NÃO tem override por env/flag na v8.11.0 (verificado em `root.go`/`settings.go` da tag) — isolar = mudar o `cwd`. |
| Mídia | [server/app.py:126-135](../server/app.py#L126) (statics dirs; comentário :129 "subprocess inherits our cwd and uses statics/senditems/") | GOWA lê/escreve mídia relativo ao cwd; o painel serve `statics/` da raiz. Mudar o cwd de um processo **quebra os paths de mídia** se não houver ponte (ver §4.2). |
| Costura dormente | [db/tables.py:241](../db/tables.py#L241) `gowa_isolation` (`shared|dedicated_process`), criada na migration `20260620_0011_channels.py`; aceita em [channel_repo.py:93-99](../db/repositories/channel_repo.py#L93) | Coluna existe, default `shared`, **nunca lida por ninguém**. Sem migration nova. |
| Credenciais de canal | [db/tables.py:273-281](../db/tables.py#L273) `channel_credentials` (texto plano, P15); mascaramento na borda em [channel_service.py:116-130](../app/services/channel_service.py#L116) (`••••`+4); edit ignora placeholder ([channel_service.py:576-579](../app/services/channel_service.py#L576), [constants.js:204](../web/static/js/components/channels/constants.js#L204)) | Campo `secret` em `credential_fields` vira `<input type="password">` ([DescriptorFields.js:94](../web/static/js/components/channels/DescriptorFields.js#L94)). `ConfigField` NÃO tem tipo secret. |
| Registry vivo | [server/app.py:177-204](../server/app.py#L177) (boot); `register_live` em [channel_service.py:167-185](../app/services/channel_service.py#L167) (runtime create); eventos `channel.updated` em [channel_service.py:569-575](../app/services/channel_service.py#L569) | Canal criado/editado vira instância viva sem restart. |
| Loops de fundo | [server/background.py:39](../server/background.py#L39) `status_poll_loop` e [:149](../server/background.py#L149) `qr_poll_loop` usam o **client único** (`deps.gowa_client`, canal default); [:128](../server/background.py#L128) `channel_identity_sweep_loop` itera **por canal via registry** | Canais não-default já são atendidos por `Channel.status()`/`get_qr()` com client próprio — só falta o client apontar pra porta certa. |
| Testes | [tests/test_gowa_plugin.py](../tests/test_gowa_plugin.py) (:95-147 direct-drive c/ subprocess real; :251-292 setup → 1 spec `name=="gowa"`; :228-244 interface guard), [tests/test_subprocess.py](../tests/test_subprocess.py) (stale-kill por signature :69-94, pid reciclado :97-113) | Base de caracterização já existe; estender, não recriar. |

### O que mudou no upstream (8.8.0 → 8.11.0) — resumo verificado

| Versão (data) | Relevante para o WhatsBot |
|---|---|
| v8.9.0 (27/jun) | Pool de conexões no chat storage (`CHAT_STORAGE_MAX_OPEN_CONNS=5`, busy timeout 5s→30s) — corrige timeouts aleatórios de envio sob carga; media `direct_path` persistido; `/chat/{jid}/messages` de chat ausente = vazio (não mais 500). |
| v8.10.0 (03/jul) | **Passkey pairing** (fluxo "Shortcake" da Meta — endpoints `/app/passkey*`, eventos WS `PASSKEY_*`); logout **mantém** o device slot (antes deletava); webhook por device; fix panic `/send/file`. |
| v8.11.0 (13/jul) | **`WHATSAPP_PROXY`** (env **e** flag `--whatsapp-proxy`; SOCKS5/HTTP/HTTPS via `SetProxyAddress` do whatsmeow — aplicado a TODOS os devices do processo); fix slot↔companion por **AD JID completo** (session hijack/registry loss com 2+ devices); history sync de grupo recuperado; `WHATSAPP_WEBHOOK_IGNORE_JIDS`. |

Assets da release: `whatsapp_8.11.0_linux_{amd64,arm64}.zip`, `whatsapp_8.11.0_windows_amd64.zip`, `whatsapp_8.11.0_darwin_{amd64,arm64}.zip` (verificado via `gh api`).

---

## 3. Inventário / análise

| # | Item | Ponto de mudança (`arquivo:linha`) | O que falta | Abordagem | Risco | Esforço |
|---|------|-----------------------------------|-------------|-----------|-------|---------|
| I1 | Bump de versão | [scripts/_common.sh:14](../scripts/_common.sh#L14), [Dockerfile:3](../Dockerfile#L3), [gowa/client.py:47](../gowa/client.py#L47), `bin/gowa.exe`, menções no [CLAUDE.md](../CLAUDE.md) | Tudo em 8.8.0 | `8.11.0` nos pins; substituir `bin/gowa.exe` (asset windows_amd64); `rm bin/gowa` local | Baixo | S |
| I2 | Spec de subprocess parametrizável | [gowa/manager.py:63-93](../gowa/manager.py#L63) `_build_cmd`; [lifecycle.py:131-144](../assets/plugin_examples/gowa/lifecycle.py#L131) (spec inline) | `_build_cmd` fixo em `self.port`/`self.webhook_url`; spec sem `cwd`/`env` | Extrair builder reutilizável (porta/webhook/cwd/env por chamada); manter o caminho atual byte-idêntico p/ o processo compartilhado | Baixo | M |
| I3 | Campo proxy no canal | [gowa_channel.py:87-119](../channels/providers/gowa_channel.py#L87) `provider_descriptor` (`credential_fields: []`) | Sem campo | `credential_fields: [{key:"proxy_url", type:"secret", required:false, ...}]` — persiste em `channel_credentials`, mascara na borda, input password. Zero mudança no form genérico | Baixo | S |
| I4 | Orquestrador de processos dedicados | novo módulo no plugin gowa (`assets/plugin_examples/gowa/processes.py`) + hook em [lifecycle.py:160-173](../assets/plugin_examples/gowa/lifecycle.py#L160) | Não existe | Reconcile loop (`ctx.spawn_task`, PERMANENT): desired = canais gowa `enabled` c/ credencial `proxy_url`; spawn/stop/restart specs `name="gowa_<cid>"`; porta alocada e persistida; atualiza `gowa_isolation` | Médio | L |
| I5 | Isolamento de storage por processo | `cwd` por canal (`storages/gowa_ch_<cid>/`) no spec ([subprocess_service.py:142](../runtime/subprocess_service.py#L142) campo `cwd`) | GOWA sem override de path p/ `chatstorage.db` | `cwd` dedicado + **symlink `statics` → raiz** dentro do dir (mídia continua nos paths que o painel serve); `whatsapp.db`/`chatstorage.db` nascem isolados nos defaults relativos | Médio | M |
| I6 | Proxy via env | [subprocess_service.py:141](../runtime/subprocess_service.py#L141) `env`; ⚠️ [manager.py:103](../gowa/manager.py#L103) loga cmd | — | `env={**os.environ, "WHATSAPP_PROXY": url}` no spec dedicado (D5). Validar esquema da URL (`socks5|http|https`) antes de subir | Baixo | S |
| I7 | Client port-aware | [gowa_channel.py:382](../channels/providers/gowa_channel.py#L382) (`GOWAClient(port=...)` fixo na porta compartilhada) | Ignora processo dedicado | `build_gowa_channel` lê `config.gowa_dedicated_port` do row e usa; sem a chave ⇒ porta compartilhada (byte-idêntico) | Médio | M |
| I8 | Webhook por processo | [main.py:55](../main.py#L55) (URL `.../gowa/default`); rota já genérica em [channel_webhook.py:291](../server/routes/channel_webhook.py#L291) | Processo dedicado postaria no path `default` | Spec dedicado usa `--webhook .../api/webhook/gowa/<cid>` — o canal chega resolvido pelo path (a re-resolução por device :323-333 continua como safety-net) | Baixo | S |
| I9 | Transição shared↔dedicated | [gowa_channel.py:265-274](../channels/providers/gowa_channel.py#L265) `logout`; client `delete_device` ([gowa/client.py](../gowa/client.py)) | Sessão antiga ficaria viva no processo compartilhado (sem proxy!) | Ao LIGAR proxy: logout+remover o device do processo compartilhado ANTES do spawn dedicado; ao DESLIGAR: parar processo dedicado + recriar device no compartilhado. Re-pareamento (QR) esperado e comunicado | Médio | M |
| I10 | Testes + docs | [tests/test_gowa_plugin.py](../tests/test_gowa_plugin.py), [tests/test_subprocess.py](../tests/test_subprocess.py), [CLAUDE.md](../CLAUDE.md) | — | Unit: builder de spec (env/cwd/porta), alocador de porta, reconcile (desired-state puro); integração: setup c/ 1 canal proxied ⇒ 2 specs. Docs: seção GOWA/proxy + deploy | Baixo | M |

### Falsos positivos descartados

| Suspeita | Por que NÃO é o caminho |
|----------|-------------------------|
| "Precisa fork do GOWA para proxy por número" | Não — 1 processo/canal com env própria resolve com upstream puro. Fork/PR per-device fica como P4 (paralelo, não bloqueia). |
| "Precisa migration para o modo de isolamento" | `channels.gowa_isolation` já existe desde a 0011 ([db/tables.py:241](../db/tables.py#L241)), `server_default='shared'`. Nada de Alembic neste plano. |
| "Precisa rota de webhook nova por canal" | Já existe: `POST /api/webhook/{provider}/{channel_id}` ([channel_webhook.py:291](../server/routes/channel_webhook.py#L291)). Só mudar a URL que o processo dedicado recebe. |
| "Passar o proxy como flag `--whatsapp-proxy`" | Vaza `user:pass` no log ([manager.py:103](../gowa/manager.py#L103)) e no `ps aux`. Env `WHATSAPP_PROXY` tem o mesmo efeito (verificado no diff do PR #664: viper bind + flag são equivalentes) sem exposição. |
| "Isolar SQLite via `--db-uri` basta" | `--db-uri` isola só a sessão; `chatstorage.db` NÃO tem override (verificado no `root.go` v8.11.0) e compartilhar o arquivo entre processos reintroduz o `SQLITE_BUSY` que a v8.9.0 corrigiu. `cwd` por processo isola os dois de uma vez. |
| "status_poll/qr_poll precisam virar per-channel" | Já operam apenas no canal default ([background.py:39](../server/background.py#L39), [:149](../server/background.py#L149)); canais não-default usam `Channel.status()`/`get_qr()` com client próprio — só falta a porta certa (I7). |
| "Upgrade quebra o logout/QR (v8.10 mudou logout)" | O fluxo já re-garante o device antes do QR ([gowa_channel.py:198-204](../channels/providers/gowa_channel.py#L198)) e trata ambos os comportamentos. Logout que preserva slot é *melhor* para o caso de uso. |
| "O default 64996 de `gowa_port` é bug a corrigir aqui" | [config/settings.py:222](../config/settings.py#L222) vs fallback 3000 em [main.py:48](../main.py#L48) é preexistente e fora do escopo; apenas respeitar `settings.get("gowa_port", ...)` como base do alocador. |

---

## 4. Contratos fixos (as fases paralelizam contra estes)

**4.1 — Campo de proxy (descriptor do provider gowa):**
```python
# channels/providers/gowa_channel.py — provider_descriptor()
"credential_fields": [{
    "key": "proxy_url", "label": "Proxy de saída (opcional)", "type": "secret",
    "required": False,
    "placeholder": "socks5://usuario:senha@ip:porta",
    "help": "IP FIXO e dedicado deste número (socks5:// ou http(s)://). "
            "Salvar/alterar o proxy reinicia a sessão deste número — será preciso ler o QR de novo.",
}]
```
- Persistência/mascaramento/edição já são genéricos (`channel_credentials` + `••••`+4 + placeholder ignorado no PUT). Validação de esquema (`socks5://`, `http://`, `https://`) no orquestrador (fail = `last_error` no canal, processo não sobe).

**4.2 — Spec do processo dedicado (um por canal com proxy):**
```
name       = "gowa_<channel_id>"                  (único; pid-file storages/run/gowa_<cid>.pid)
cmd        = [bin/gowa, rest, --port <porta_dedicada>,
              --webhook http://127.0.0.1:<web_port>/api/webhook/gowa/<channel_id>,
              --webhook-events <mesma lista do _build_cmd>, --presence-on-connect available,
              --os "Techify - WhatsBot", (--debug=true se WHATSBOT_GOWA_DEBUG)]
env        = {**os.environ, "WHATSAPP_PROXY": <proxy_url>}          ← D5 (nunca argv)
cwd        = storages/gowa_ch_<channel_id>/                          ← isola whatsapp.db + chatstorage.db
             (criado no spawn; contém symlink  statics -> ../../statics  para a mídia
              continuar caindo nos paths que o painel serve — server/app.py:126-135)
signature  = <path do binário> (igual ao compartilhado; stale-kill é por pid-file/name, seguro)
max_restarts=3 / window 60s / restart_delay 5s (iguais ao compartilhado)
```
- **Porta dedicada**: alocada uma vez (primeira porta livre a partir de `gowa_port+1`, bind-check) e **persistida** em `channels.config["gowa_dedicated_port"]` — estável entre restarts (webhook/client dependem dela).
- `gowa_isolation` do row é atualizado pelo orquestrador (`dedicated_process` ⟷ `shared`) — observabilidade, não fonte de decisão (a fonte é a credencial `proxy_url`).

**4.3 — Reconcile (desired vs running), executado no plugin gowa:**
```
desired  = {cid: proxy_url}  para canais provider=gowa, enabled=1, archived=0, credencial proxy_url não-vazia
running  = processos "gowa_<cid>" vivos no SubprocessService
→ spawn   (desired − running)         [com migração I9 se o device estava no compartilhado]
→ stop    (running − desired)         [+ recriar device no compartilhado]
→ restart (desired ∩ running com proxy/porta diferente do processo vivo)
```
- Função de cálculo **pura** (entra lista de canais + snapshot de processos, sai plano de ações) — unit-testável sem subprocess.
- Disparo: no `setup()` do plugin (boot) + loop PERMANENT (`ctx.spawn_task`, intervalo ~15s) — cobre create/edit/delete/enable/disable sem depender de evento; `channel.updated` ([channel_service.py:569](../app/services/channel_service.py#L569)) pode encurtar a latência depois (P5).

---

## 5. Fases / Roadmap

```
WAVE 0  F0(upgrade 8.11.0) ─ F1(caracterização lifecycle)      ← paralelas entre si
              │ (F0 é pré-requisito de QUALQUER proxy; F1 trava o baseline antes de I2)
              ▼
WAVE 1  F2(builder spec param.) · F3(campo proxy no descriptor)  ← paralelas entre si
              │  (barreira: F2+F3 bloqueiam F4)
              ▼
WAVE 2  F4(orquestrador + reconcile) ─▶ F5(client port-aware + transição)   ← sequencial
              ▼
WAVE 3  F6(testes de integração) · F7(docs)                     ← paralelas entre si
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|------|------|-----------|-------|-------|----------------|
| 0 | F0 | Upgrade GOWA v8.11.0 | 🟢 [bloqueia: F4] | Baixo | Boot ok; QR/send/receive ok; suíte verde |
| 0 | F1 | Caracterização do lifecycle/spec atual | 🟢 [bloqueia: F2] | Baixo | Testes fixam spec atual (name/env=None/cwd=None) |
| 1 | F2 | Builder de spec parametrizável (porta/webhook/cwd/env) | 🟢 [depende: F1; bloqueia: F4] | Baixo | Compartilhado byte-idêntico; unit do builder verde |
| 1 | F3 | Campo `proxy_url` no descriptor + validação de esquema | 🟢 [bloqueia: F4] | Baixo | Form cria/edita/mascara o campo sem tocar no core |
| 2 | F4 | Orquestrador (reconcile + alocador de porta + cwd/symlink) | 🔴 [depende: F0,F2,F3] | Médio | Canal c/ proxy ⇒ 2º processo vivo com env/cwd/webhook próprios |
| 2 | F5 | Client port-aware + transição shared↔dedicated | 🔴 [depende: F4] | Médio | QR/status/send fluem pela porta dedicada; IP de saída = IP do proxy |
| 3 | F6 | Testes de integração + regressão | 🟢 [depende: F5] | Baixo | `test_gowa_plugin.py` estendido verde; endpoints verdes |
| 3 | F7 | Docs (CLAUDE.md + deploy) | 🟢 [depende: F5] | Baixo | Seções novas revisadas |

**Disciplina (regras do repo):** verde a cada fase; **caracterização ANTES** de mexer no lifecycle (F1 → F2/F4); **um refactor por commit**; nunca avançar com teste vermelho não-explicado.

---

### Fase 0 — Upgrade GOWA v8.11.0 🟢 [bloqueia: F4]
**Objetivo:** binários e pins na v8.11.0, comportamento atual preservado.
**Itens:**
1. `[paralelo]` [scripts/_common.sh:14](../scripts/_common.sh#L14): `GOWA_VERSION="${GOWA_VERSION:-8.11.0}"`.
2. `[paralelo]` [Dockerfile:3](../Dockerfile#L3): `ARG GOWA_VERSION=8.11.0`.
3. `[paralelo]` Substituir `bin/gowa.exe` pelo binário de `whatsapp_8.11.0_windows_amd64.zip` (asset da release; commitá-lo — é trackeado via `!bin/gowa.exe` no `.gitignore`).
4. `[paralelo]` [gowa/client.py:47](../gowa/client.py#L47): docstring "v8.8.0" → "v8.11.0"; atualizar menções de versão no [CLAUDE.md](../CLAUDE.md) (stack + tabela GOWA).
5. `[sequencial]` Dev local: `rm bin/gowa` e rodar `./linux_start.sh` (re-download 8.11.0 — o launcher NÃO checa versão, só existência: [linux_start.sh:65](../linux_start.sh#L65)).
6. `[sequencial]` Smoke manual: boot limpo (migration interna 35 `ad_jid` do GOWA se auto-aplica), status do canal conectado, enviar/receber mensagem, revogar/reagir, QR de canal novo.

**Pronto quando:** `bin/gowa --help` lista `--whatsapp-proxy`; app conecta e troca mensagens como antes; `venv/bin/python -m pytest tests/ -q` (dirs suportados) verde.

#### Status de execução — Fase 0
**Estado:** ✅ Concluída (2026-07-16)
- **O que foi feito:** `scripts/_common.sh` e `Dockerfile` → 8.11.0; `bin/gowa` e `bin/gowa.exe` substituídos pelos assets oficiais da release v8.11.0 (SHA256 conferido contra `checksums.txt`); docstring `gowa/client.py:47` e menções no CLAUDE.md (stack + seção GOWA REST API) atualizadas.
- **Como foi feito / decisões:** binário Linux em uso pelo processo vivo → troca por unlink+cp (inode antigo preservado até o respawn). O hot-reload do dev server (edit em `gowa/client.py`) respawnou o GOWA já no binário novo. `gowa/client.py:104` ("GOWA >= 8.8.0") mantido — é nota histórica factual sobre quando o formato de erro mudou.
- **Problemas / pendências:** baseline de `tests/test_gowa_plugin.py` tem 1 falha PRÉ-EXISTENTE: `from . import alerts` (lifecycle.py:171) quebra no loader standalone do teste (`spec_from_file_location` sem pacote). Corrigir o loader na F1 (necessário de qualquer forma para o `processes.py` da F4).
- **Verificação:** `bin/gowa rest --help` lista `--whatsapp-proxy` e `--webhook-ignore-jids`; processo vivo (pid) rodando o binário novo; `GET /devices` responde com os 2 devices e o `whatsbot` **connected** (sessão sobreviveu; migration 35 `ad_jid` aplicada silenciosamente); `tests/test_subprocess.py` 11/11 verde; `tests/test_gowa_plugin.py` 49/50 (1 falha pré-existente documentada acima).

---

### Fase 1 — Caracterização do lifecycle atual 🟢 [bloqueia: F2]
**Objetivo:** fixar o contrato do spawn atual antes de parametrizar (pega regressão do processo compartilhado).
**Itens:**
1. `[sequencial]` Em [tests/test_gowa_plugin.py:251-292](../tests/test_gowa_plugin.py#L251), reforçar asserções do spec compartilhado: `name=="gowa"`, `env is None`, `cwd is None`, webhook `.../gowa/default`, argv SEM `--whatsapp-proxy` — o baseline que F2/F4 não podem mudar.
2. `[paralelo]` Anotar no teste o argv completo esperado (ordem dos flags de [manager.py:70-93](../gowa/manager.py#L70)).

**Pronto quando:** os testes novos passam contra o código ATUAL (pré-F2).

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-07-16)
- **O que foi feito:** `tests/test_gowa_plugin.py`: (a) novo loader `_load_gowa_plugin_module()` que carrega o plugin como PACOTE (`submodule_search_locations`) — corrige a falha pré-existente do `from . import alerts` e habilita o `from . import processes` da F4; (b) baseline do spec compartilhado (`env is None`, `cwd is None`, argv sem `--whatsapp-proxy`); (c) seção nova de caracterização do argv REAL de `_build_cmd` (rest/--port/--webhook/--webhook-events/--presence/--os, sem proxy), guardada por `_get_gowa_binary().exists()`.
- **Como foi feito / decisões:** caracterização usa `GOWAManager` real com `data_dir` tmp (o `__init__` faz mkdir); o argv é validado por posição (`[1:4]`) + presença de flags, não por string inteira (tolerante a espaçamento).
- **Problemas / pendências:** nenhum.
- **Verificação:** `tests/test_gowa_plugin.py` **58/58 verde** (a falha pré-existente da F0 também resolvida pelo loader).

---

### Fase 2 — Builder de spec parametrizável 🟢 [depende: F1; bloqueia: F4]
**Objetivo:** um único builder monta o spec do processo compartilhado E dos dedicados, sem duplicar a lista de webhook-events.
**Itens:**
1. `[sequencial]` [gowa/manager.py:63-93](../gowa/manager.py#L63): extrair a montagem do argv para aceitar `port`/`webhook_url` por chamada (ex.: `_build_cmd(port=None, webhook_url=None)` com defaults = atributos da instância — caminho atual byte-idêntico). ⚠️ NÃO adicionar proxy ao argv (D5).
2. `[sequencial]` [lifecycle.py:131-144](../assets/plugin_examples/gowa/lifecycle.py#L131): extrair a construção do `SubprocessSpec` para função reutilizável (no plugin) parametrizada por `(name, cmd, env, cwd, stdout/stderr)` — o compartilhado continua `name="gowa"`, `env=None`, `cwd=None`.
3. `[paralelo]` Unit tests do builder: dedicado gera argv com porta/webhook próprios; env contém `WHATSAPP_PROXY`; compartilhado idêntico ao baseline da F1.

**Pronto quando:** F1 continua verde (compartilhado inalterado); unit do builder cobre os dois modos.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-07-16)
- **O que foi feito:** `gowa/manager.py` `_build_cmd(port=None, webhook_url=None)` (defaults = atributos da instância ⇒ compartilhado byte-idêntico; docstring avisa que proxy NUNCA vai no argv — D5). Novo `assets/plugin_examples/gowa/processes.py` com `build_spec()` (um shape de spec pros dois modos; signature derivada de `cmd[0]`); `lifecycle.py` passou a usar o builder (imports órfãos `SubprocessSpec`/`sys`/var `binary` removidos).
- **Como foi feito / decisões:** o builder foi direto pro `processes.py` (o módulo da F4) em vez de um helper solto no lifecycle — evita mover código duas vezes.
- **Problemas / pendências:** nenhum.
- **Verificação:** baseline F1 verde pós-mudança (spec compartilhado com `env is None`/`cwd is None`/argv idêntico); unit `build_spec` cobre env/cwd pass-through e defaults herdados.

---

### Fase 3 — Campo `proxy_url` no canal 🟢 [bloqueia: F4]
**Objetivo:** o operador configura o proxy no form do canal GOWA; segredo mascarado, sem mudança no core.
**Itens:**
1. `[sequencial]` [gowa_channel.py:87-119](../channels/providers/gowa_channel.py#L87): adicionar o `credential_field` do contrato §4.1. O form genérico ([ChannelEditForm.js:144](../web/static/js/components/channels/ChannelEditForm.js#L144) `CredentialFields`) renderiza sozinho.
2. `[sequencial]` Validação de esquema da URL (helper no plugin gowa ou em `channels/providers/gowa_channel.py`): aceitar apenas `socks5://`, `http://`, `https://` com host não-vazio; usada pelo orquestrador (F4) — URL inválida ⇒ processo não sobe + `last_error` no canal.
3. `[paralelo]` Conferir a borda de mascaramento: GET do canal devolve `proxy_url` como `••••…` ([channel_service.py:116-130](../app/services/channel_service.py#L116)); PUT com placeholder não sobrescreve ([:576-579](../app/services/channel_service.py#L576)). Nada a codar se já genérico — só testar.

**Pronto quando:** criar/editar canal GOWA mostra o campo password; valor salvo aparece mascarado no GET; re-salvar sem tocar no campo preserva o segredo.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-07-16)
- **O que foi feito:** `credential_field` `proxy_url` (type `secret`, required False, help com aviso de re-pareamento + "nunca proxy rotativo") no `provider_descriptor()` de [channels/providers/gowa_channel.py](../channels/providers/gowa_channel.py). `validate_proxy_url()` em `processes.py` (esquemas socks5/http/https + host não-vazio, mensagens PT-BR).
- **Como foi feito / decisões:** validação mora no plugin (usada pelo reconcile; URL inválida ⇒ `last_error` "Proxy inválido: …" no canal, processo não sobe). Form genérico renderiza sem mudança no core (verificado: `CredentialField` type secret → input password; masking/placeholder já genéricos).
- **Problemas / pendências:** nenhum.
- **Verificação:** units de `validate_proxy_url` (7 casos) verdes; integração confirma `last_error` para proxy inválido e para proxy no canal `default`.

---

### Fase 4 — Orquestrador de processos dedicados 🔴 [depende: F0, F2, F3]
**Objetivo:** canal gowa com proxy ⇒ processo GOWA próprio (porta/cwd/env/webhook próprios), gerido declarativamente.
**Itens:**
1. `[sequencial]` Novo `assets/plugin_examples/gowa/processes.py` (espelhar em `storages/plugins/gowa/` conforme fluxo de plugin instalado — ver memória "Plugin changes via zip"): função **pura** `plan_reconcile(channels, running) -> {spawn, stop, restart}` (§4.3) + executor que materializa specs via builder da F2.
2. `[sequencial]` Alocador de porta: primeira porta livre ≥ `settings.get("gowa_port")+1` (bind-check com `socket`), persistida em `channels.config["gowa_dedicated_port"]` via `channel_repo`; reutilizada se já persistida e livre.
3. `[sequencial]` Preparo do `cwd`: criar `storages/gowa_ch_<cid>/` + symlink `statics -> ../../statics` (em Windows, junction via `os.symlink(..., target_is_directory=True)`/fallback documentado — ver Riscos). PID files já vão para `storages/run/` absoluto ([subprocess_service.py:39](../runtime/subprocess_service.py#L39)) — nada a fazer.
4. `[sequencial]` Wire no [lifecycle.py `setup()`](../assets/plugin_examples/gowa/lifecycle.py#L73): reconcile inicial após o spawn do compartilhado + `ctx.spawn_task("process_reconcile", ..., PERMANENT)` (~15s). Atualizar `gowa_isolation` do row a cada convergência.
5. `[paralelo]` Env do spec: `{**os.environ, "WHATSAPP_PROXY": proxy}` — conferir que o log de spawn NÃO imprime env ([manager.py:103](../gowa/manager.py#L103) só imprime cmd — ok por construção).
6. `[paralelo]` Unit tests de `plan_reconcile` (puro): sem proxy ⇒ nenhum dedicado; adicionar/remover/trocar proxy ⇒ spawn/stop/restart correto; canal disabled/archived sai do desired.
7. `[sequencial]` **Distribuição para instalações existentes** (ver P7): bump da `version` no `plugin.yaml` do gowa e estender [plugins/bootstrap.py:116-159](../plugins/bootstrap.py#L116) `bootstrap_gowa_upgrade` para, ALÉM do caso "faltando", re-copiar quando a versão bundled (`load_manifest(src).version`) for **maior** que a instalada em `storages/plugins/gowa` (semver compare; tombstone respeitado; `WHATSBOT_TEST` respeitado). Sem isso, `git pull`/redeploy em instalação existente entrega o core novo mas o plugin velho — e o proxy por número nunca ativa lá.

**Pronto quando:** com 1 canal proxied, `SubprocessService` mostra `gowa` + `gowa_<cid>` vivos; `storages/gowa_ch_<cid>/whatsapp.db` existe; webhook do dedicado aponta para `/api/webhook/gowa/<cid>`; derrubar o processo (kill) ⇒ watchdog o repõe.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-07-16)
- **O que foi feito:** `assets/plugin_examples/gowa/processes.py` completo: `plan_reconcile` (puro), `desired_proxies` (puro; exclui default/disabled/archived/inválido), `allocate_port` (persisted-first + bind-check injetável), `prepare_channel_dir` (cwd + symlink relativo `statics`), `reconcile_once` (heal de claims órfãos + stop/restart/spawn + `last_error`), `reconcile_loop` (PERMANENT, 15s, `to_thread`). Wire no `lifecycle.setup` (`gowa:process_reconcile`). `plugins/bootstrap.py`: `_semver_tuple` + `_upgrade_bundled_gowa_in_place` (copy-to-temp + rename swap, rollback em falha, NUNCA re-habilita plugin desabilitado) + `bootstrap_gowa_upgrade` delega quando target existe. `plugin.yaml` 1.0.0 → 1.1.0.
- **Como foi feito / decisões:** (a) **Heal adicional** não previsto no plano: canal com `gowa_isolation='dedicated_process'`/porta persistida mas sem proxy desejado E sem processo vivo (ex.: proxy removido com o servidor desligado) converge de volta a shared — o diff puro nunca o veria (`running` vazio no boot). (b) `_rebuild_live_instance` recusa canal `archived` (não ressuscita soft-delete no registry). (c) proxy no canal `default` ⇒ `last_error` explicativo (nunca dedicado — singleton legado). (d) `ctx.on_unload(managed.stop)` por spawn como backstop (paridade com o shared).
- **Problemas / pendências:** debug-log (`WHATSBOT_GOWA_DEBUG`) dos processos dedicados vai pra DEVNULL (só o shared loga em `logs/gowa.log`) — nice-to-have futuro.
- **Verificação:** 109/109 no `tests/test_gowa_plugin.py` (units puros + integração contra o Postgres de teste com cwd temporário). **Upgrade version-aware validado AO VIVO**: bump pra 1.1.0 + reload ⇒ `storages/plugins/gowa` substituído (processes.py presente, lifecycle sincronizado — recuperou inclusive o sweep do plano 32 que a cópia instalada não tinha), row `plugins` = `{version: 1.1.0, enabled: 1, load_error: None}`, device `whatsbot` seguiu **connected**.

---

### Fase 5 — Client port-aware + transição shared↔dedicated 🔴 [depende: F4]
**Objetivo:** todo o tráfego do canal proxied (QR, status, send, avatar, membros) flui pelo processo dedicado; ligar/desligar proxy migra a sessão de forma limpa.
**Itens:**
1. `[sequencial]` [gowa_channel.py:368-385](../channels/providers/gowa_channel.py#L368) `build_gowa_channel`: ler `gowa_dedicated_port` do `row["config"]` e usar em `GOWAClient(port=...)`; ausente ⇒ porta compartilhada (byte-idêntico). ⚠️ O canal `default` nunca é dedicado (é o singleton do pipeline legado).
2. `[sequencial]` Transição **shared→dedicated** (no executor do reconcile, antes do spawn): client do processo compartilhado faz `logout` + remoção do device `gowa_device_id` (evita sessão duplicada sem proxy — I9); broadcast `gowa_status`/`channel.updated` para a UI refletir "reconectar (QR)".
3. `[sequencial]` Transição **dedicated→shared** (proxy removido): parar processo dedicado (`stop`), limpar `gowa_dedicated_port` e `gowa_isolation='shared'`; `ensure_device` no compartilhado recria o device (QR de novo).
4. `[sequencial]` Rebuild da instância viva no registry após transição (o client aponta pra porta velha): re-registrar via `registry.add_channel(build_gowa_channel(...))` — mesmo mecanismo do `register_live` ([channel_service.py:167-185](../app/services/channel_service.py#L167)).
5. `[paralelo]` Sweep de identidade ([background.py:128](../server/background.py#L128)) continua válido — os hooks `account_identity`/`reject_duplicate` operam via client do canal (agora port-aware). Conferir, não mudar.

**Pronto quando:** configurar proxy num canal ⇒ processo dedicado sobe, painel pede QR, após parear o número troca mensagens normalmente e `curl ifconfig.me` "visto" pelo WhatsApp é o IP do proxy (validar via painel do provedor/logs); remover o proxy ⇒ volta ao compartilhado com novo QR; os DEMAIS canais não piscam em nenhum dos dois sentidos.

#### Status de execução — Fase 5
**Estado:** ✅ Concluída (2026-07-16)
- **O que foi feito:** `build_gowa_channel` lê `config["gowa_dedicated_port"]` (ausente/inválido ⇒ porta compartilhada, byte-idêntico; `default` nunca dedicado). Transições no executor: shared→dedicated evict via `logout` + novo `GOWAClient.delete_device()` (`DELETE /devices/{id}`, purge completo — semântica v8.10) ANTES do spawn; dedicated→shared limpa porta + `gowa_isolation` + rebuild. `_rebuild_live_instance` re-registra a instância viva (mesmo mecanismo do `register_live`).
- **Como foi feito / decisões:** o evict usa client efêmero com `_device_ready=True` (age só sobre o slot existente, nunca cria); `storages/gowa_ch_<cid>/` é preservado ao desligar proxy (sessão pareada sobrevive a um re-enable). Sweep de identidade conferido: opera via `Channel.status()` da instância (agora port-aware) — sem mudança.
- **Problemas / pendências:** validação E2E com proxy real (webshare) pendente — depende de o usuário contratar os IPs dedicados (as camadas de env/porta/webhook estão verificadas por teste).
- **Verificação:** integração 109/109: spawn com env `WHATSAPP_PROXY` (nunca argv), webhook `/api/webhook/gowa/<cid>`, cwd + symlink, porta persistida, isolation flip, registry rebuild, steady-state no-op, restart em troca de proxy, stop+volta a shared em remoção, heal de claim órfão.

---

### Fase 6 — Testes de integração + regressão 🟢 [depende: F5]
**Objetivo:** o comportamento novo está coberto e o antigo, protegido.
**Itens:**
1. `[paralelo]` Estender [tests/test_gowa_plugin.py](../tests/test_gowa_plugin.py): setup com 1 canal proxied ⇒ 2 specs (nomes, env com `WHATSAPP_PROXY`, cwd distinto, webhook por canal); teardown `stop_owner('gowa')` derruba ambos.
2. `[paralelo]` Unit: alocador de porta (colisão ⇒ próxima livre; persistida é reutilizada); validador de URL de proxy (esquemas inválidos rejeitados).
3. `[paralelo]` Endpoints: criar canal gowa com credencial `proxy_url` via `POST /api/channels` (form genérico) — GET mascarado, PUT com placeholder preserva.
4. `[sequencial]` Suíte completa nos dirs suportados (memória: `pytest tests/` inteiro não roda — rodar por dir/arquivo) contra o Postgres de teste (`WHATSBOT_TEST_DB_URL`).

**Pronto quando:** tudo verde; nenhum teste pré-existente alterado sem justificativa registrada.

#### Status de execução — Fase 6
**Estado:** ✅ Concluída (2026-07-16)
- **O que foi feito:** suíte estendida em `tests/test_gowa_plugin.py` (units puros de `validate_proxy_url`/`plan_reconcile`/`desired_proxies`/`allocate_port`/`build_spec`/`_channel_webhook_url` + integração `reconcile_once` contra o Postgres de teste + upgrade version-aware do bootstrap com 4 cenários). Regressão ampla executada.
- **Como foi feito / decisões:** integração usa `os.chdir` pra um tmp (o `prepare_channel_dir` ancora em `Path.cwd()` — nada é criado no repo) e ctx/deps fake com `GOWAManager` real (binário do repo).
- **Problemas / pendências:** falhas PRÉ-EXISTENTES documentadas, todas fora do diff: (a) `tests/test_endpoints.py` 1265 passed / 8 failed — os 8 são da seção do plugin **protocolos** (dependem do zip instalado, memória do projeto); (b) `pytest tests/endpoints/` tem 2–3 falhas de ORDENAÇÃO (passam isoladas; **provado por `git stash`**: falham igualmente sem o diff do plano 52). Extensão `unaccent` criada no test DB durante o diagnóstico (efeito neutro — o reset de schema a dropa e a busca dobra acentos em Python).
- **Verificação:** `test_gowa_plugin.py` **109/109** · `test_subprocess.py` **11/11** · `node --test constants.test.js` **18/18** · `test_endpoints.py` **1265 passed** (8 fails pré-existentes de protocolos) · arquivos de `tests/endpoints/` verdes isolados.

---

### Fase 7 — Documentação 🟢 [depende: F5]
**Objetivo:** operador e futuras IAs sabem como o proxy por número funciona.
**Itens:**
1. `[paralelo]` [CLAUDE.md](../CLAUDE.md): bump da versão GOWA nas seções de stack/API; nova subseção "Proxy de saída por número" (arquitetura híbrida, campo `proxy_url`, re-pareamento ao ligar/desligar, `storages/gowa_ch_*/`).
2. `[paralelo]` [docs/DEPLOY_COOLIFY.md](../docs/DEPLOY_COOLIFY.md): lembrar que o Persistent Storage de `/app/storages` já cobre os dirs `gowa_ch_*` (sessões dos números proxied) — sem passo novo, mas explicitar.
3. `[paralelo]` Nota de operação: recomendação de proxy (IP estático dedicado, 1 por número; NUNCA rotating) no help do campo e no doc.

**Pronto quando:** docs revisados e coerentes com o implementado.

#### Status de execução — Fase 7
**Estado:** ✅ Concluída (2026-07-16)
- **O que foi feito:** CLAUDE.md — versão GOWA v8.11.0 (stack + seção REST API), nova seção "Proxy de saída por número (plano 52)" e a exceção version-aware no gotcha "Bootstrap de plugins". [docs/DEPLOY_COOLIFY.md](../docs/DEPLOY_COOLIFY.md) — tabela de Persistent Storage menciona `storages/gowa_ch_<canal>/`.
- **Como foi feito / decisões:** seção do CLAUDE.md colocada junto aos blocos de canais (antes de "Tipo de contato por canal"), no mesmo nível de detalhe das seções vizinhas.
- **Problemas / pendências:** nenhum.
- **Verificação:** revisão de leitura; links relativos conferidos.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Credencial do proxy em log/argv | `user:pass` do proxy vazando em `logs/` ou `ps aux` | D5: env-only (`WHATSAPP_PROXY` no `spec.env`); [manager.py:103](../gowa/manager.py#L103) loga só o cmd; GOWA rediga a URL nos logs dele (`redactProxyURL`, verificado no PR #664); GET do canal mascara (`••••`) |
| Sessão duplicada na transição | Ligar proxy sem derrubar o device antigo ⇒ número conectado 2× (uma via IP real!) | F5 item 2: logout+delete no compartilhado ANTES do spawn dedicado; sweep de identidade (plano 32) como safety-net |
| Colisão de porta | Porta persistida ocupada por outro serviço após reboot | Bind-check no reconcile; ocupada ⇒ realocar + atualizar config + respawn (webhook/client derivam da config, seguem juntos) |
| Symlink `statics` no Windows | `os.symlink` exige privilégio no Windows | Dev Windows: tentar symlink → fallback junction (`_winapi`/`mklink /J` via subprocess) → último caso: documentar que proxy por número é feature de servidor (Linux/Docker). Não bloqueia o compartilhado |
| Dois processos, um binário | Stale-kill matar o processo errado | Não ocorre: pid-file é por `name` ([subprocess_service.py:143](../runtime/subprocess_service.py#L143)) e o guard confere assinatura no PID do próprio arquivo ([test_subprocess.py:69-94](../tests/test_subprocess.py#L69)) |
| RAM/CPU por processo | ~40–80 MB por GOWA dedicado | Aceito (D2/D3): só canais com proxy pagam; documentar em F7 |
| Watchdog storm | Proxy morto ⇒ GOWA não conecta ⇒ restart loop | `max_restarts=3/60s` já limita ([lifecycle.py:139-141](../assets/plugin_examples/gowa/lifecycle.py#L139)); GOWA sobe mesmo com proxy inválido (loga warning — verificado no PR #664), então o erro aparece como "desconectado" no card, não como crash |
| Re-pareamento obrigatório | Operador surpreso com QR ao salvar proxy | Help do campo avisa (§4.1); broadcast de status após transição (F5) |
| Upgrade em produção (Coolify) | Redeploy baixa 8.11.0; migration 35 interna do GOWA roda no 1º boot | Não-destrutiva (coluna nova + backfill); sessões preservadas em `/app/storages` (Persistent Storage já exigido) |
| `pytest tests/` inteiro | Coleção quebra (scripts standalone com `sys.exit` — memória do projeto) | Rodar por dir/arquivo como hoje (`tests/test_gowa_plugin.py`, `tests/test_subprocess.py`, `tests/endpoints/...`) |
| Instalação existente não recebe o plugin novo | `bootstrap_gowa_upgrade` só copia se `storages/plugins/gowa` estiver FALTANDO ([bootstrap.py:138-139](../plugins/bootstrap.py#L138)) — `git pull` entregaria core novo + plugin velho (proxy nunca ativa) | F4 item 7: upgrade version-aware (re-copiar quando bundled > instalado, tombstone respeitado). Risco residual: usuário que EDITOU o plugin gowa na mão perde a edição — logar aviso claro no boot ao re-copiar |

---

## 7. Perguntas em aberto

| # | Pergunta | Estado |
|---|----------|--------|
| P1 | Isolamento do `chatstorage.db` sem override de path | ✅ DECIDIDO (2026-07-16): `cwd` por processo + symlink `statics` (verificado: `ChatStorageURI` não tem env/flag na v8.11.0). Alternativa (a) `--db-uri` só isola a sessão — insuficiente; (b) PR upstream criando `CHAT_STORAGE_URI` — bônus futuro, não bloqueia. |
| P2 | Expor `gowa_isolation` na UI como opção manual? | ✅ DECIDIDO (2026-07-16): não — derivado da presença de `proxy_url` (menos um conceito pro operador). A coluna vira observabilidade. |
| P3 | Migrar canais SEM proxy para processos dedicados também (isolamento total)? | ⏸️ ADIADO — exigiria re-parear todos os números existentes (sessões vivem no `storages/whatsapp.db` compartilhado). Se um dia valer, é um plano próprio de migração. |
| P4 | Propor PR upstream de proxy per-device (opção C da investigação)? | ⏸️ ADIADO — paralelo, não bloqueia. Se mergeado um dia, permite voltar a 1 processo. O upstream aceita PRs de comunidade rápido (#664/#671/#736). |
| P5 | Encurtar a latência do reconcile com evento `channel.updated`? | ⏸️ ADIADO — o loop de ~15s atende; plugar o evento é otimização de UX posterior (1 linha no `EVENT_HANDLERS` do plugin). |
| P6 | Passkey (Shortcake) na UI do WhatsBot | ⏸️ FORA DO ESCOPO deste plano — a v8.11.0 traz os endpoints `/app/passkey*` e eventos `PASSKEY_*`, mas o fluxo de QR do painel ([QRConnect.js](../web/static/js/components/channels/QRConnect.js), [SetupWizard.js](../web/static/js/components/SetupWizard.js)) não os conhece. Vira plano próprio se/quando um número cair no rollout. |
| P7 | Como instalações EXISTENTES recebem o plugin gowa novo? | ✅ DECIDIDO (2026-07-16): upgrade **version-aware** no `bootstrap_gowa_upgrade` (F4 item 7) — bump de `version` no `plugin.yaml` + re-copiar de `assets/` quando bundled > instalado (tombstone e `WHATSBOT_TEST` respeitados). Alternativas descartadas: (a) exigir re-import manual do zip em toda instalação (não escala, silenciosamente deixa o proxy inerte); (b) sobrescrever sempre no boot (clobber de edições do usuário sem critério de versão). Fresh installs já funcionam via `bootstrap_initial_plugins` (copia quando `storages/plugins` está vazio). |

---

## 8. Apêndice — arquivos-chave

**Upgrade (F0):**
- [scripts/_common.sh](../scripts/_common.sh) · [Dockerfile](../Dockerfile) · `bin/gowa.exe` (substituir) · [gowa/client.py](../gowa/client.py) (docstring) · [CLAUDE.md](../CLAUDE.md)

**Backend / plugin gowa (F1–F5):**
- [gowa/manager.py](../gowa/manager.py) — `_build_cmd` parametrizável
- [assets/plugin_examples/gowa/lifecycle.py](../assets/plugin_examples/gowa/lifecycle.py) — spec builder extraído + wire do reconcile (espelhar em `storages/plugins/gowa/`)
- `assets/plugin_examples/gowa/processes.py` — **novo**: `plan_reconcile` + executor + alocador de porta + preparo de cwd/symlink
- [channels/providers/gowa_channel.py](../channels/providers/gowa_channel.py) — descriptor (`proxy_url`) + `build_gowa_channel` port-aware
- [runtime/subprocess_service.py](../runtime/subprocess_service.py) — sem mudança prevista (já tem `env`/`cwd`); referência de contrato
- [db/repositories/channel_repo.py](../db/repositories/channel_repo.py) — leitura/gravação de `gowa_isolation` + `config.gowa_dedicated_port`
- [plugins/bootstrap.py](../plugins/bootstrap.py) — `bootstrap_gowa_upgrade` version-aware (F4 item 7 / P7) + bump de `version` no `plugin.yaml` do gowa

**Sem mudança (referência de fluxo):**
- [server/routes/channel_webhook.py](../server/routes/channel_webhook.py) (rota por canal já existe) · [app/services/channel_service.py](../app/services/channel_service.py) (persistência/mascaramento genéricos) · [web/static/js/components/channels/DescriptorFields.js](../web/static/js/components/channels/DescriptorFields.js) e [constants.js](../web/static/js/components/channels/constants.js) (form dirigido pelo descriptor) · [server/background.py](../server/background.py) (loops)

**Testes (F1, F6):**
- [tests/test_gowa_plugin.py](../tests/test_gowa_plugin.py) · [tests/test_subprocess.py](../tests/test_subprocess.py) · `tests/endpoints/` (canais)

---

## 9. Checklist de verificação (aplicar a cada fase)

- [ ] `venv/bin/python -m pytest` verde nos dirs/arquivos suportados, contra o Postgres de teste (`WHATSBOT_TEST_DB_URL`; nome do banco contém `test`)
- [ ] Processo compartilhado byte-idêntico enquanto nenhum canal tem proxy (F1 baseline verde)
- [ ] Nenhum segredo de proxy em: logs do app, argv (`ps aux`), resposta de API sem máscara
- [ ] Canal proxied: QR → parear → enviar/receber → reagir/revogar → avatar, tudo pela porta dedicada
- [ ] Kill manual do processo dedicado ⇒ watchdog repõe; disable do plugin gowa ⇒ `stop_owner` derruba todos
- [ ] Transição liga/desliga proxy não afeta os OUTROS canais (mensagens continuam fluindo durante)
- [ ] Modo escuro: campo novo no form de canal legível (usa componentes `wa-*` existentes — conferir mesmo assim)
- [ ] Docker/Coolify: build baixa 8.11.0; `storages/gowa_ch_*/` persiste em redeploy (Persistent Storage de `/app/storages`)
- [ ] Dev: `rm bin/gowa` + `./linux_start.sh` re-baixa 8.11.0; `windows_start.bat` sobe com o novo `.exe`
