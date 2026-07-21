# Plano 63 — Sons de notificação configuráveis (som, volume e duração por evento) + restaurar o controle de ligar o som

> **Status:** PLANEJAMENTO · **Data:** 2026-07-20 · **Escopo:** médio
>
> **Origem:** pedido do usuário — *"Pretendo mudar o som e/ou diminuir também, pois os atendentes estão reclamando dele. Tem sons para cada mensagem que chega, sons para quando eu atribuo a conversa para outro usuário (o som chega só para o outro), e som para transferência da IA. Quero poder escolher o som, volume e duração de cada. Arrume também a parte de que o controle que liga o som sumiu."*
>
> **Método:** diagnóstico multi-agente somente-leitura (8 mapeadores em paralelo + verificação adversarial de 40 achados críticos + 3 arquiteturas concorrentes julgadas por painel de 3 lentes + síntese). Toda afirmação com `arquivo:linha` **verificado por leitura real** — os 3 pilares (bug do ConfigPanel, WS sem identidade, vazamento do `mention_created`) foram re-conferidos manualmente após a síntese.
>
> **O que se está fazendo e por quê:** hoje só as **2 sirenes de transferência tocam** — e são justamente as que incomodam (volume fixo alto `0.3`, ignoram qualquer preferência, duração global). Os sons de **mensagem nova e menção estão mudos** por default OFF + sem UI que os ligue (a tela que fazia isso saiu para a Loja de Plugins). O plano traz o controle de som para o **core** (inarrancável), com preferência **por usuário** (segue o atendente entre dispositivos) e escolha de **som + volume + duração por evento**.
>
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | **Apenas planejar** — nada implementado ainda ✅ (2026-07-20) | Investigação 100% read-only; execução só após aprovação. |
| D2 | Há **instância em produção** (Empresa Exemplo, Coolify) na branch `developer` | Mudanças aditivas e retrocompatíveis; **verde a cada fase**; um refactor por commit; nunca quebrar as config keys legadas de transferência. |
| D3 | Postgres é o único backend (plano 29) | Livre para tabela nova + JSON nativo (`_json_type()`); sem preocupação com SQLite. |
| D4 | Config de plugin mora no plugin (regra CLAUDE.md) | A UI de som **NÃO** pode nascer num plugin removível — foi exatamente isso que causou o "controle sumido". Vai para o **core**. |
| D5 | O gotcha do `statics/` não-persistente (CLAUDE.md) | O MVP é **100% som sintetizado** (Web Audio, zero asset). Nada de `.mp3` em `statics/`. |

---

## 1. Resumo executivo

O WhatsBot tem **4 sons**, todos produzidos no core por Web Audio sintetizado (nenhum arquivo de áudio existe no repo): ding de **mensagem nova**, ding de **menção interna** (mesma função), sirene de **atribuição a outro atendente** e sirene de **transferência IA→humano**. Dos 4, **só as 2 sirenes tocam hoje** — as outras duas nascem com a preferência `sound` em `false` e a única tela que já a ligava foi removida do repo (commit `ccaebc4`), deixando o interruptor **inalcançável**. As sirenes, por sua vez, têm **volume fixo `0.3`** e ignoram qualquer preferência — é o barulho de que os atendentes reclamam.

A solução escolhida (após 3 vereditos de juízes) é **trazer som para o core** com **preferência por usuário** persistida no Postgres (segue o atendente entre máquinas), um **motor único** (`soundEngine.js`) que toca synth e arquivo com volume e duração unificados, e uma **tela core** "Notificações e sons" onde cada atendente escolhe **som, volume e duração por evento** e ouve um **preview**. O admin define o **padrão da equipe** (config global). Um **multiplicador de volume por dispositivo** (localStorage) reconcilia "PC do escritório ≠ notebook silencioso" sem quebrar o sync. A entrega é faseada: a **Fase 0** conserta hoje o controle sumido (e um bug real de save no ConfigPanel); as fases seguintes constroem o modelo de dados, o motor e a UI.

Resultado esperado: o atendente liga/desliga o som, escolhe timbre, **baixa o volume da sirene** e ajusta a duração de cada evento — e a preferência o acompanha em qualquer dispositivo.

---

## 2. Como funciona hoje (mapa)

### 2.1 — Os 4 sons (evento → produtor → gate → parametrizável)

| Evento | Produtor | Disparo (`arquivo:linha`) | Fonte do som | Gate atual | Volume? | Duração? | **Toca hoje?** |
|---|---|---|---|---|---|---|---|
| **Mensagem nova (inbound)** | `playNotificationSound()` | [App.js:327](../web/static/js/components/shell/App.js#L327) | ding sintetizado G5→C6 ([notifications.js:100-113](../web/static/js/utils/notifications.js#L100)) **ou** data-URL custom ([:120](../web/static/js/utils/notifications.js#L120)) | `getNotifPref('sound')` per-device, **default FALSE** ([notifications.js:18](../web/static/js/utils/notifications.js#L18)) | Sim, global ([getNotifVolume :63](../web/static/js/utils/notifications.js#L63)) | Não (one-shot ~0,43s) | ❌ **NÃO** — default OFF + sem UI |
| **Menção interna (@ em nota privada)** | `playNotificationSound()` | [useConversationWsEvents.js:241](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L241) | **mesmo ding** | `getNotifPref('sound')` (mesma chave) | Sim (idem) | Não | ❌ **NÃO** (mesma pref) — e quando tocasse, seria indistinguível de mensagem |
| **Atribuição p/ outro atendente** | `playTransferAlert(seconds)` | [useConversationWsEvents.js:322](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L322) | sirene 2 tons square 880/660Hz ([alertSound.js:11,21](../web/static/js/utils/alertSound.js#L11)) | Servidor: `assignee != actor` ([conversation_service.py:109](../app/services/conversation_service.py#L109)) + filtro cliente `assignee_user_id !== uid` ([useConversationWsEvents.js:321](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L321)) + `agent_transfer_alert_enabled` ([settings.py:136](../config/settings.py#L136)) | ❌ **NÃO** (gain fixo `0.3`, [alertSound.js:22](../web/static/js/utils/alertSound.js#L22)) | Sim, `seconds` do payload | ✅ **SIM** — só p/ o destinatário |
| **Transferência IA → humano** | `playTransferAlert(seconds)` | [App.js:228](../web/static/js/components/shell/App.js#L228) | mesma sirene | Servidor **per-canal** `transfer_alert_enabled` ([channels/ai_settings.py](../channels/ai_settings.py), plano 21; default True) | ❌ **NÃO** | Sim, `duration` do payload | ✅ **SIM** — p/ TODOS os sockets ([messaging_service.py](../app/services/messaging_service.py) — payload sem destinatário) |

**Verdade crua:** os únicos sons audíveis hoje são as duas sirenes — as que incomodam. Os dois dings estão mudos.

### 2.2 — Infra relevante (verificado)

| Peça | Onde | Estado |
|---|---|---|
| Prefs de notificação per-device | [notifications.js:10-29](../web/static/js/utils/notifications.js#L10) | 3 chaves localStorage (`tab`/`browser`/`sound`); `sound` default **FALSE**; `setNotifPref` dispara `whatsbot:notif-prefs` ([:28](../web/static/js/utils/notifications.js#L28)) que `App.js:293-297` escuta |
| Seam de som custom (já existe, órfão) | [notifications.js:59-71](../web/static/js/utils/notifications.js#L59) | `CUSTOM_SOUND_KEY` + `VOLUME_KEY` — plugins escreviam aqui; hoje **ninguém escreve** |
| WS manager | [state.py:47-100](../server/state.py#L47) | `self.active: list[WebSocket]` — **sem identidade de usuário**; `broadcast` fan-out para **todos** ([:68-70](../server/state.py#L68)) |
| Handshake WS | [server/routes/websocket.py](../server/routes/websocket.py) | resolve `_user` do token mas **descarta** — `connect(websocket)` não recebe o user |
| Config declarativa | [settings.py:91-105](../config/settings.py#L91) | registry `ConfigKey` — 1 linha nova já expõe no `GET /api/config` e libera no allowlist do `PUT` (refactor R17) |
| Tabela `users` | [db/tables.py:316-332](../db/tables.py#L316) | identidade + auth + RBAC; **sem** coluna de preferências/JSON |
| Molde per-user | [db/tables.py:862-874](../db/tables.py#L862) `saved_atendimento_filters` | `user_id` nullable (FK **lógica**, NULL = modo aberto), payload `_json_type()`, índice por user; repo [saved_filter_repo.py:21-23](../db/repositories/saved_filter_repo.py#L21) tem `_user_match` NULL-safe |
| Endpoint per-user molde | [server/routes/saved_filters.py:34-92](../server/routes/saved_filters.py#L34) | `GET/PUT` por `current_user` |

### 2.3 — ⚠️ Gotchas que tornam algo obrigatório

- **Guard `authoritative` (plano 57)** em [App.js:315](../web/static/js/components/shell/App.js#L315): impede o re-emit pós-save de tocar de novo. **Preservar** ao reescrever o call site — senão toda mensagem toca 2×.
- **Guards `private_note`/`role!=='user'`/`silent`** ([App.js:322-326](../web/static/js/components/shell/App.js#L322)): regras de "não tocar". **Preservar** todos.
- **Filtro `assignee_user_id !== uid`** ([useConversationWsEvents.js:321](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L321)): é **o que faz** "som só para o outro" funcionar. Não remover — só endurecer (Fase 4).
- **`alembic_version.version_num` é varchar(32)** (memória do projeto): o id do revision da migration nova precisa ter **≤32 chars** (o nome descritivo vai no arquivo).
- **`statics/` não persiste** (CLAUDE.md): nada de arquivo de áudio em `statics/`. MVP sintetizado.
- **AudioContext vaza** em [alertSound.js:6](../web/static/js/utils/alertSound.js#L6): cria um `new AudioContext()` **por disparo** (fecha após `setTimeout`). O motor unificado deve usar um **singleton lazy** com `resume()`.

---

## 3. Diagnóstico do "controle que sumiu" (a parte que o usuário pediu para arrumar)

Causa-raiz **verificada** (4 elos):

1. A pref `sound` nasce **`false`** — [notifications.js:18](../web/static/js/utils/notifications.js#L18) (`DEFAULTS = { tab: true, browser: false, sound: false }`).
2. O único código que já chamou `setNotifPref('sound', true)` era o **plugin `notifications`** (screen `config:true`), removido no commit `ccaebc4` ("move o resto para a Loja"). **Não está** em `storages/plugins/` nem em `assets/plugin_examples/` — recuperável só do git (`git show ccaebc4^:assets/plugin_examples/notifications/static/notifications.js`, 106 linhas, zero backend).
3. Logo `setNotifPref` **não tem nenhum call site** no repo → o interruptor de som de mensagem/menção é **inalcançável**. O core dispara ([App.js:327](../web/static/js/components/shell/App.js#L327), [useConversationWsEvents.js:241](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L241)) mas o gate está travado em `false`.
4. As sirenes escapam disso porque o gate delas é no **servidor** ([settings.py:129,136](../config/settings.py#L129)) e **não passam** por `getNotifPref('sound')` — por isso são as únicas audíveis.

**Bug adjacente descoberto e verificado** (entra na Fase 0): [ConfigPanel.js:97-99](../web/static/js/components/ConfigPanel.js#L97) chama `setWebPassword('')` / `setWebPasswordConfirm('')` / `setRemovePassword(false)` — **três setters que não existem** (nenhum `useState` os declara; sobra do plano 48). Fluxo: `handleSave` faz `setSaveSuccess(true)` ([:96](../web/static/js/components/ConfigPanel.js#L96)) e na linha seguinte lança **`ReferenceError`** → o `setTimeout` de reverter ([:100](../web/static/js/components/ConfigPanel.js#L100)) **nunca roda** → botão preso em "✓ Salvo!" + unhandled rejection a cada save de config.

**Conclusão arquitetural:** o "controle sumido" foi *causado* por a config de som viver num plugin removível. A correção definitiva é o controle no **core, inarrancável** — o que fundamenta a escolha da seção 5.

---

## 4. Inventário / análise

| Item | Onde | O que falta | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|
| Reexpor liga/desliga do som | core (sumiu com o plugin) | UI inalcançável | Toggle no core (Fase 0) + tela dedicada (Fase 3) | baixo | S |
| Bug do save travado | [ConfigPanel.js:97-99](../web/static/js/components/ConfigPanel.js#L97) | 3 setters inexistentes | Remover as 3 linhas | baixo | S |
| Volume da sirene | [alertSound.js:22](../web/static/js/utils/alertSound.js#L22) | gain fixo `0.3` | Motor unificado aplica volume | baixo | S |
| Escolher som por evento | não existe | catálogo + resolução | Catálogo sintetizado + tela | médio | M |
| Volume por evento | parcial (global) | por-evento + por-device | 3-tier + multiplicador device | médio | M |
| Duração por evento | só sirenes | semântica por classe | `duration_applies` (§6) | médio | M |
| Pref por usuário | não existe | tabela + endpoints | Molde `saved_filters` | médio | M |
| Padrão global (admin) | não existe | 1 config key JSON | `ConfigKey("sound_settings")` | baixo | S |
| Direcionamento WS + privacidade | [state.py:57-100](../server/state.py#L57) | envio por usuário | Registry `user_id→sockets` (opcional) | médio | M |

### 4.1 — Falsos positivos descartados (NÃO gastar esforço)

- **O guard `authoritative` (plano 57, [App.js:315](../web/static/js/components/shell/App.js#L315)) NÃO é a causa do som sumido.** Ele impede o re-emit pós-save de tocar 2×; contrato correto, **preservar**. A causa é a pref default OFF sem UI (§3).
- **O guard `silent` ([App.js:326](../web/static/js/components/shell/App.js#L326)) NÃO quebra som.** É a regra "ignorar abertura" do plugin protocolos; preservar.
- **O filtro `assignee_user_id !== uid` NÃO impede "som só para o outro" — ele É o que faz funcionar.** Combinado com o gate servidor `if assignee == actor: return` ([conversation_service.py:109](../app/services/conversation_service.py#L109)), o requisito (c) do usuário **já funciona hoje**. A Fase 4 só o torna à prova de DevTools + fecha o vazamento — **não é pré-requisito**.
- **`AudioPlayer.js` NÃO é som de notificação** — é o player de mensagem de voz iniciado pelo usuário; fora de escopo.
- **Os 12 plugins instalados NÃO escondem nenhum som** — grep em `storages/plugins/*/static/` por `Audio`/`AudioContext`/`play()` → vazio.
- **A memória `rbac-enforce-sem-writer-api-aberta.md` está desatualizada** para este plano: o gate hoje é `has_users` self-healing ([server/routes/websocket.py](../server/routes/websocket.py) + middleware plano 48), então `uid` real quase sempre existe e per-user é viável. Mas **manter o caminho `uid=None`** (instalação zero-user) como fail-open.

---

## 5. Arquitetura escolhida (decisão única)

**Som é preocupação de CORE, com preferência POR USUÁRIO no servidor.** Verificado: os 2 produtores (158 linhas em `web/static/js/utils/`), os 4 call sites, os eventos WS e os 4 guards de disparo são **todos core**; **zero plugin produz som**. Os três juízes convergiram (arquitetura 8, UX 8, risco preferiu o híbrido leve — todos concordam que o modelo per-user encaixa melhor o requisito).

**Por que não as alternativas:**
- **Plugin dono da experiência** recria por design o bug "controle sumiu" (config num plugin desabilitável, `os._exit` no toggle, contra a decisão "só gowa auto-instalado").
- **Per-device puro (localStorage só)** é limpo mas **não faz a preferência seguir o atendente** entre máquinas — falha o requisito de equipe.

**Resolução 3-tier fail-open** (padrão de [channels/ai_settings.py](../channels/ai_settings.py) + `agent_repo.get_default` + [history_filter.py](../agent/history_filter.py)):

```
efetivo[evento][campo] = override_do_usuário  ??  default_global_admin  ??  code_seed
volume_real = volume_efetivo × device_volume_mult   (multiplicador local, §6c)
```

**Enxertos travados no plano** (das abordagens perdedoras + comuns às três):
- **Chave localStorage única versionada** para a camada per-device (`whatsbot_sound_prefs_v1`) — multiplicador de volume + master local, sync entre abas via evento `storage`.
- **Catálogo sintetizado-first** (zero asset) como default do MVP.
- **AudioContext singleton** lazy + `resume()` (corrige o vazamento de [alertSound.js:6](../web/static/js/utils/alertSound.js#L6)); piso `Math.max(0.0001, vol)` no ramp exponencial; **volume unificado cobrindo a sirene**; throttle ~300ms por evento; fallback arquivo→ding; **Preview** como gesto que destrava a autoplay policy.
- **Shims**: `playNotificationSound()`/`playTransferAlert(s)` viram wrappers finos sobre o motor — migração sem quebra.
- **Precedência nos eventos de transferência**: o **servidor pode SILENCIAR**; o usuário/dispositivo só customiza **dentro do que está habilitado** (reconcilia "duas fontes de verdade").
- **Upload de biblioteca própria = plugin opcional** (`custom_sounds`, recuperável de `ccaebc4^`), **fora do MVP**.

---

## 6. Modelo de dados e semântica

### 6a. Camada GLOBAL (admin) — 1 config key JSON

Uma linha em `CONFIG_KEYS` ([settings.py:105](../config/settings.py#L105)) — herda `DEFAULT_CONFIG`, `GET /api/config` e allowlist do `PUT` de graça (+ auditoria + broadcast `config_saved`):

```
ConfigKey("sound_settings", exposed=True, writable=True, get_default=<SEED>)
```

Valor (JSON como Text na tabela `config`, [db/tables.py:53-58](../db/tables.py#L53)):

```jsonc
{ "master_enabled": true,
  "events": {
    "new_message":    { "enabled": true,  "sound": "ding",  "volume": 0.6 },
    "mention":        { "enabled": true,  "sound": "chime", "volume": 0.8 },
    "private_note":   { "enabled": false, "sound": "soft",  "volume": 0.6 },
    "ia_to_human":    { "sound": "siren", "volume": 0.9 },
    "assigned_to_me": { "sound": "siren", "volume": 0.9 } } }
```

Os dois eventos de transferência **não carregam `enabled`/`duration` aqui** — esses seguem nas keys legadas `transfer_alert_*` / `agent_transfer_alert_*` ([settings.py:129,136](../config/settings.py#L129)). O JSON global só define `sound`/`volume` deles (§9).

### 6b. Camada POR USUÁRIO — tabela nova (molde `saved_atendimento_filters`)

```
user_sound_prefs (
  id          INTEGER PK autoincrement,
  user_id     INTEGER NULL,             -- FK lógica -> users.id; NULL = open-mode
  prefs       _json_type(),             -- ESPARSO: só o que o usuário sobrescreveu
  updated_at  Float NOT NULL )
UNIQUE INDEX ux_user_sound_prefs_user (user_id)   -- chave de upsert
```

Migration Alembic nova (autogenerate; **revision id ≤32 chars**). `prefs` é **esparso** (mesma forma de `events`, só as chaves alteradas → novos eventos caem no default global automaticamente). Repo `db/repositories/user_sound_pref_repo.py`: copiar `_user_match` NULL-safe ([saved_filter_repo.py:21-23](../db/repositories/saved_filter_repo.py#L21)) + `get(user_id)` / `upsert(user_id, prefs)` via `db.upsert.upsert` on-conflict `user_id`.

**Por que tabela e não config prefixada:** `PUT /api/config` só aceita a allowlist estática de `ConfigKey` e a tabela `config` tem PK só em `key` ([db/tables.py:53-58](../db/tables.py#L53)) — sem dimensão de usuário. Config per-user por essa via é **impossível** (verificado).

### 6c. Camada POR DISPOSITIVO — 1 chave localStorage versionada

```jsonc
localStorage["whatsbot_sound_prefs_v1"] = {
  "version": 1,
  "device_volume_mult": 1.0,   // 0..1, multiplicador LOCAL sobre o volume per-user
  "master_local": true         // "mudo neste navegador", independente do servidor
}
```

O **multiplicador de volume per-device** (reusa a semântica de `VOLUME_KEY`, [notifications.js:61](../web/static/js/utils/notifications.js#L61)) é a mitigação central: "PC do escritório ≠ notebook silencioso" sem quebrar o sync per-user. Sync entre abas via evento window `storage`; reload imediato via `whatsbot:notif-prefs` (já existe, [notifications.js:28](../web/static/js/utils/notifications.js#L28) + [App.js:293-297](../web/static/js/components/shell/App.js#L293)). As chaves `whatsbot_notif_tab` e `whatsbot_notif_browser` **permanecem** (não são som).

### 6d. Endpoints

| Método | Rota | Gate | Molde |
|---|---|---|---|
| `GET/PUT` | `/api/me/sound-prefs` | protegido, identidade via `current_user` | [saved_filters.py:34-92](../server/routes/saved_filters.py#L34) |
| `GET` | `/api/sounds/catalog` | público (só metadados) | catálogo estático |
| `GET/PUT` | `/api/config` (`sound_settings`) | `settings.manage` ([config.py](../server/routes/config.py)) | pipeline existente |

### 6e. Semântica de "DURAÇÃO" por classe de evento (a pergunta central, honesta)

Duração é propriedade da **classe do evento**, não do arquivo:

| Classe | Eventos | O que "duração" faz | UI |
|---|---|---|---|
| **notification (one-shot)** | `new_message`, `mention`, `private_note` | **Nada.** Toca uma vez; "duração" = comprimento natural do som | Slider de duração **OCULTO**; legenda "Toca uma vez" |
| **alert (sustained)** | `ia_to_human`, `assigned_to_me` | Duração = **por quantos segundos o alerta insiste** (repete). É o `seconds` que a sirene já usa ([alertSound.js:15](../web/static/js/utils/alertSound.js#L15)) | Slider **1–15s** visível; legenda "Repetir por N segundos" |

Comportamento do player (uniforme para synth e arquivo): sustained+synth → agenda beeps até `duration` (atual); sustained+arquivo → `audio.loop=true` + `setTimeout(stop, duration*1000)` (loop-and-cap); once+arquivo longo → toca inteiro sem truncar. **Honestidade:** loop-and-cap corta um clipe longo no meio (ok para alarme, feio para "musiquinha") — a UI **esconde** o slider onde é N/A e recomenda sons curtos para alerta.

### 6f. Catálogo de sons (MVP 100% sintetizado)

Parametrizações do que já existe inline em [notifications.js:100-113](../web/static/js/utils/notifications.js#L100) e [alertSound.js:11-27](../web/static/js/utils/alertSound.js#L11):

| id | Timbre | Classe |
|---|---|---|
| `ding` | 2 notas G5→C6 | once |
| `chime` | arpejo suave | once |
| `blip` | tom curto único | once |
| `soft` | ding grave discreto | once |
| `pulse` | alerta gentil | alert |
| `siren` | 2 tons square 880/660 | alert |
| `none` | silêncio explícito | qualquer |

Upload de biblioteca própria (`custom_sounds`, base64 no Postgres — sobrevive a redeploy) é **plugin opcional em cima** do catálogo, gated por `plugin_permission`, **fora do MVP**.

---

## 7. Fases / Roadmap

### 7.1 — Diagrama de dependências

```
WAVE 0   F0 (Fase 0: fix imediato)                    ← sozinha, shippável hoje
            │  (barreira: F1 congela o contrato do catálogo)
WAVE 1   F1 (backend: 3 camadas) · F5 (assets opcional)   ← F1 ∥ F5
            │  (barreira: F2 e F3 dependem do catálogo da F1)
WAVE 2   F2 (soundEngine) · F3 (UI)                    ← F2 ∥ F3
WAVE 3   F4 (WS direcionado + privacidade)             ← independente; a qualquer momento
```

### 7.2 — Tabela de fases

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | Fix imediato (frontend) | 🔴 | baixo | atendente liga o som e ele toca; save de config não trava |
| 1 | **F1** | Backend: 3 camadas de dados | 🟢 | médio | `PUT /api/me/sound-prefs` persiste; `sound_settings` no `GET /api/config` [bloqueia: F2, F3] |
| 1 | **F5** | Assets bundled (opcional) | 🟢 | baixo | `.ogg` servidos por `web/static/audio/` (se Q6=b) |
| 2 | **F2** | `soundEngine.js` (frontend) | 🟢 | médio | 4 origens tocam pelo motor único; sirene respeita volume [depende de: F1] |
| 2 | **F3** | UI "Notificações e sons" | 🟢 | médio | escolher som+volume+duração/evento, preview, sync cross-device [depende de: F1] |
| 3 | **F4** | WS direcionado + privacidade | 🔴 | médio | destinatário recebe, os demais não; preview de nota privada não vaza |

---

### Fase F0 — Fix imediato: reexpor o liga/desliga + consertar o save 🔴

**Objetivo:** o atendente volta a conseguir ligar o som HOJE, e o bug de save some.

**Itens:**
1. `[sequencial]` Remover [ConfigPanel.js:97-99](../web/static/js/components/ConfigPanel.js#L97) — as 3 chamadas a setters inexistentes (`setWebPassword`/`setWebPasswordConfirm`/`setRemovePassword`). Sem elas, `setTimeout` de reverter ([:100](../web/static/js/components/ConfigPanel.js#L100)) volta a rodar.
2. `[sequencial]` Reexpor o toggle de som: **opção rápida** = um toggle de ~3 linhas na seção "Notificações" do ConfigPanel ([:180](../web/static/js/components/ConfigPanel.js#L180)) escrevendo `setNotifPref('sound', …)` (per-device, sem backend). **NÃO** reviver o plugin (viola D4). O toggle definitivo (por evento) chega na F3.
3. `[paralelo]` Confirmar que `playNotificationSound` respeita o toggle no disparo ([App.js:327](../web/static/js/components/shell/App.js#L327)) — sem mudança de lógica, só validar.

**Pronto quando:** ligar "Som de notificação" e uma mensagem inbound toca o ding; salvar qualquer config não deixa o botão preso em "✓ Salvo!" (o "Salvo!" some após 3s); nenhum erro no console ao salvar.

#### Status de execução — Fase F0
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois)_
- **Verificação:** _(o que recarregar/clicar + resultado)_

---

### Fase F1 — Backend: as 3 camadas de dados + resolução 🟢 [Wave 1; bloqueia F2/F3]

**Objetivo:** persistência global (admin), por-usuário (servidor) e o catálogo.

**Itens:**
1. `[paralelo]` [settings.py:105](../config/settings.py#L105) → `ConfigKey("sound_settings", exposed=True, writable=True, get_default=<SEED>)`; special-case de reset/normalização no `PUT` ([config.py](../server/routes/config.py), molde `ai_history_exclude_patterns`).
2. `[paralelo]` [db/tables.py](../db/tables.py) → tabela `user_sound_prefs` (§6b) + migration Alembic (**revision id ≤32 chars**).
3. `[sequencial, após 2]` `db/repositories/user_sound_pref_repo.py` — molde [saved_filter_repo.py](../db/repositories/saved_filter_repo.py) (`_user_match` NULL-safe + `get`/`upsert`).
4. `[sequencial, após 1+3]` `server/routes/` → `GET/PUT /api/me/sound-prefs` (molde [saved_filters.py:34-92](../server/routes/saved_filters.py#L34)) + `GET /api/sounds/catalog` (metadados estáticos: eventos, sons, `duration_applies` por classe).
5. `[paralelo]` Teste de endpoint: `PUT`+`GET` per-user (uid válido) **e uid=None** (instalação zero-user); `sound_settings` no `GET /api/config`.

**Pronto quando:** `PUT /api/me/sound-prefs` grava e `GET` devolve o override do usuário; com `uid=None` a leitura cai no global sem erro; `sound_settings` aparece no `GET /api/config`; suíte verde no Postgres de teste.

#### Status de execução — Fase F1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(escolhas; desvios)_
- **Problemas / pendências:** _(pendências)_
- **Verificação:** _(testes + resultado)_

---

### Fase F5 — Assets de áudio bundled (OPCIONAL) 🟢 [Wave 1; paralelo com F1]

**Objetivo:** timbres "ricos" além do sintetizado, SE decidido (Q6=b).

**Itens:** `[paralelo]` 3–5 `.ogg` curtos em `web/static/audio/` (versionado, servido por `'self'` — **nunca** `statics/`) + manifest no catálogo. Confirmar que a CSP ([server/app.py](../server/app.py)) permite `media-src 'self'`.

**Pronto quando:** um som de arquivo do manifest toca via preview; sobrevive a redeploy (está no git). **Se Q6=a, esta fase não existe.**

#### Status de execução — Fase F5
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(escolhas; desvios)_
- **Problemas / pendências:** _(pendências)_
- **Verificação:** _(testes + resultado)_

---

### Fase F2 — `soundEngine.js`: motor unificado 🟢 [Wave 2; depende de F1]

**Objetivo:** um módulo toca synth E arquivo, com volume e duração, resolvendo as 3 camadas.

**Itens:**
1. `[sequencial]` Novo `web/static/js/utils/soundEngine.js`: `playEvent(eventKey, {durationOverride, enabledOverride})`, `playDescriptor(soundId, {volume, duration, loop})` (preview), `reloadPrefs()`.
2. `[sequencial]` Resolução 3-tier fail-open no cliente; **AudioContext singleton** lazy + `resume()`; volume unificado (× `device_volume_mult`) cobrindo **também a sirene**; piso `Math.max(0.0001, vol)`; once vs loop+duration (§6e); **throttle ~300ms** por eventKey; fallback arquivo→ding.
3. `[sequencial]` Shims: [notifications.js](../web/static/js/utils/notifications.js) (`playNotificationSound`) e [alertSound.js](../web/static/js/utils/alertSound.js) (`playTransferAlert`) viram wrappers finos. Trocar os **4 call sites** ([App.js:228,327](../web/static/js/components/shell/App.js#L228); [useConversationWsEvents.js:241,322](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L241)) por `soundEngine.playEvent(...)`, **preservando** os guards `authoritative`/`private_note`/`role!=='user'`/`silent` ([App.js:315-326](../web/static/js/components/shell/App.js#L315)) e o filtro `assignee_user_id !== uid` ([useConversationWsEvents.js:321](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L321)).
4. `[paralelo]` Teste JS puro (`node --test`, molde [phone.test.js](../web/static/js/utils/phone.test.js)) do resolvedor 3-tier (merge esparso, fallback, multiplicador de volume) — extrair a resolução como função pura.

**Pronto quando:** as 4 origens tocam pelo motor único; a sirene respeita o volume configurado; rajada de transferências não estoura AudioContext; **nenhuma mensagem toca 2×** (guard `authoritative` intacto); `node --test` verde.

#### Status de execução — Fase F2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(escolhas; desvios)_
- **Problemas / pendências:** _(pendências)_
- **Verificação:** _(testes + resultado)_

---

### Fase F3 — UI "Notificações e sons" 🟢 [Wave 2; depende de F1]

**Objetivo:** a tela onde o atendente escolhe **som, volume e duração por evento**.

**Itens:**
1. `[sequencial]` Tela core no **GearMenu** "Notificações e sons", visível a **todos** (som é pessoal — não gated por `settings.manage`); recebe `currentUser` (prop já disponível, ver `ConfigPanel({..., currentUser})` [ConfigPanel.js:21](../web/static/js/components/ConfigPanel.js#L21)).
2. `[sequencial]` Topo: **master "Tocar sons"** (fix definitivo do controle sumido) + **slider "Volume neste dispositivo"** (per-device, `device_volume_mult`).
3. `[sequencial]` Uma linha por evento (grupos Mensagens / Transferências): `[toggle] Nome [dropdown som ▾] [▶ preview] [slider volume] [slider duração — só se duration_applies]` + chip "padrão da equipe" vs "personalizado" + **↺ Restaurar padrão** (deleta o override esparso).
4. `[sequencial]` **▶ Preview** chama `playDescriptor(...)` sem salvar (gesto que destrava a autoplay policy).
5. `[sequencial]` Modo **admin** (`settings.manage`): toggle "Editar padrão da equipe" → a mesma grade edita `sound_settings` via `PUT /api/config`; admin vê `enabled`/`duration` das transferências como editáveis (moram nas keys legadas); atendente as vê como "definido pelo administrador".
6. `[paralelo]` Dark mode: classes `wa-*` + `.wa-field` (regra CLAUDE.md), nunca `bg-white`/`bg-*-50` cru. Testar com modo escuro ligado.

**Pronto quando:** um atendente escolhe som+volume+duração de cada evento, ouve o preview, a mudança aplica na hora e persiste após F5 (reload) **e em outro dispositivo/navegador** (via servidor); a tela é legível no modo escuro.

#### Status de execução — Fase F3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(escolhas; desvios)_
- **Problemas / pendências:** _(pendências)_
- **Verificação:** _(testes + resultado)_

---

### Fase F4 — Direcionamento WS server-side + fix de privacidade (OPCIONAL) 🔴 [Wave 3; independente]

**Objetivo:** o alerta chega só ao socket do destinatário e o preview de nota privada para de vazar.

**Itens:**
1. `[sequencial]` Registry `user_id → set[WebSocket]` em `ConnectionManager` ([state.py:57-58](../server/state.py#L57)); passar `_user` no handshake ([websocket.py](../server/routes/websocket.py) — hoje resolvido e **descartado**); método `send_to_user`/`broadcast_to_users` (replicando timeout+poda+close de [state.py:85-100](../server/state.py#L85), limpando o índice no `disconnect` E na poda).
2. `[sequencial]` Aplicar **só** a `agent_transfer_alert` ([conversation_service.py:118](../app/services/conversation_service.py#L118)) e `mention_created` ([contacts.py:1284](../server/routes/contacts.py#L1284)). Fecha o **vazamento real**: `mention_created` hoje manda `preview[:120]` de nota privada a **todos** os sockets. Manter o filtro cliente como cinto de segurança/retrocompat. **Aditivo** — não converter os ~146 call sites de `broadcast(`.
3. `[sequencial]` Ajustar o stub de teste que monkeypatcheia `ws_manager.broadcast` ([tests/test_endpoints.py:~5044](../tests/test_endpoints.py#L5044)) para cobrir o novo caminho.

**Pronto quando:** o socket do destinatário recebe o alerta e o dos demais **não** (teste "X recebeu, Y não"); `mention_created` não entrega mais o preview a quem não foi mencionado; suíte verde.

#### Status de execução — Fase F4
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(escolhas; desvios)_
- **Problemas / pendências:** _(pendências)_
- **Verificação:** _(testes + resultado)_

---

## 8. Riscos e cuidados

| # | Ponto | Risco | Mitigação | Ref |
|---|---|---|---|---|
| R1 | Decisão per-device → per-user | volume é genuinamente device-bound | Multiplicador **per-device** (`device_volume_mult`) sobre o volume per-user | [notifications.js:1-8](../web/static/js/utils/notifications.js#L1); §6c |
| R2 | Instalação zero-user (`uid=None`) | overrides colapsam / erro | Caminho NULL-safe `_user_match` + resolução cai no global; **teste explícito** | [saved_filter_repo.py:21-23](../db/repositories/saved_filter_repo.py#L21) |
| R3 | Corrida de identidade (`currentUser` best-effort) | prefs não carregadas no 1º disparo | soundEngine carrega prefs quando `AuthGate` tem `currentUser`; antes, code-seeds (nunca mudo) | — |
| R4 | `sound_settings` = JSON sem schema no `PUT` genérico | payload inválido persiste | Resolver **fail-open** (como `history_filter`) + normalizar na leitura + validar no PUT | [config.py](../server/routes/config.py) |
| R5 | Duas fontes de verdade na transferência (enabled/duration backend × sound/volume user) | inconsistência | Precedência "**servidor silencia; device customiza dentro do habilitado**" + rótulo na UI | §5, §7-F3 |
| R6 | Novo evento no catálogo × overrides esparsos antigos | evento novo sem preferência | Merge esparso cai no default global automaticamente | §6 |
| R7 | Autoplay policy | som antes de clique é descartado em silêncio | Preview é gesto de usuário; `AudioContext.resume()` | [notifications.js:90-97](../web/static/js/utils/notifications.js#L90) |
| R8 | AudioContext vaza (1 por disparo) | acúmulo de contexts | Singleton lazy no soundEngine (corrige [alertSound.js:6](../web/static/js/utils/alertSound.js#L6)) | §5 |
| R9 | Loop-and-cap corta clipe longo | corte no meio | `duration_applies` esconde slider onde N/A; recomendar sons curtos p/ alerta | §6e |
| R10 | Guard `authoritative` removido por engano na F2 | som toca 2× | Checklist F2 exige preservar [App.js:315](../web/static/js/components/shell/App.js#L315); teste "1 mensagem = 1 som" | §2.3 |
| R11 | F4 quebra teste que monkeypatcheia `broadcast` | suíte vermelha | Ajustar o stub junto ([tests:~5044](../tests/test_endpoints.py#L5044)), não depois | §7-F4 |
| R12 | Migration id > 32 chars | boot quebra (StringDataRightTruncation) | Revision id curto; nome descritivo no arquivo | memória do projeto |

---

## 9. Perguntas em aberto

**P1 — Default do master de som: ON ou OFF?**
- (a) **ON** com volume modesto 0.6 — *Recomendado.* Agora que é controlável, os atendentes reclamam de *não conseguir ligar*; nascer ON resolve a dor. Reversível por usuário.
- (b) OFF (preserva comportamento atual).
- ⏸️ ADIADO — decidir antes da F1 (define o `<SEED>` de `sound_settings`).

**P2 — Volume real: sincronizado cross-device ou 100% local?**
- (a) **volume per-user × multiplicador per-device (híbrido)** — *Recomendado.* Segue o atendente mas respeita o hardware. É a mitigação de R1.
- (b) volume só per-device.
- ⏸️ ADIADO — decidir antes da F2.

**P3 — Fase 4 (WS direcionado + fix de privacidade) entra no MVP?**
- (a) sim (fecha o vazamento de nota privada).
- (b) **depois, como fast-follow** — *Recomendado.* O pedido literal (escolher som/volume/duração) não depende dela; funciona hoje via filtro cliente. Mas o vazamento é real e barato (~5 linhas + registry) — não deixar cair.
- ⏸️ ADIADO.

**P4 — Biblioteca de upload (plugin `custom_sounds`) no escopo?**
- (a) **MVP só sintetizado** — *Recomendado.* Cobre "escolher som" sem risco de quota/persistência.
- (b) incluir upload já.
- ⏸️ ADIADO — vira plugin opcional depois.

**P5 — Volume default da sirene ao passar a respeitá-lo.** Hoje `0.3` fixo ([alertSound.js:22](../web/static/js/utils/alertSound.js#L22)).
- (a) **0.6** (o usuário pediu "diminuir" → mas hoje já é 0.3; ajustar para o que ele achar melhor no preview) — *a confirmar com o usuário no preview.*
- (b) manter ~0.3.
- ⏸️ ADIADO — o usuário calibra na tela (F3).

**P6 — Assets bundled (F5) valem a pena?**
- (a) **só sintetizado** no MVP — *Recomendado.*
- (b) 3–5 `.ogg` em `web/static/audio/` (versionado, nunca `statics/`) para timbres ricos.
- ⏸️ ADIADO.

---

## 10. Apêndice — arquivos-chave (por camada)

**Frontend — motor/produtores:**
- `web/static/js/utils/soundEngine.js` — **novo** (motor unificado)
- [notifications.js](../web/static/js/utils/notifications.js) — vira shim + chave `whatsbot_sound_prefs_v1` (hoje `:10-18`, `:59-71`, `:117-126`)
- [alertSound.js](../web/static/js/utils/alertSound.js) — `playTransferAlert` vira shim (hoje `:5-32`)

**Frontend — call sites (rewire preservando guards):**
- [App.js:228,327](../web/static/js/components/shell/App.js#L228) (guards `:315,322,323,326`)
- [useConversationWsEvents.js:241,322](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L241) (filtro `:321`)

**Frontend — UI:**
- Nova tela "Notificações e sons" (GearMenu) — componente novo, recebe `currentUser`
- [ConfigPanel.js](../web/static/js/components/ConfigPanel.js) — **F0**: remover `:97-99` + toggle rápido em `:180`; **F3**: seção admin opcional

**Backend — config:**
- [config/settings.py:105](../config/settings.py#L105) — `ConfigKey("sound_settings", …)`
- [server/routes/config.py](../server/routes/config.py) — special-case reset/validação

**Backend — per-user:**
- [db/tables.py](../db/tables.py) — tabela `user_sound_prefs` (molde `:862-874`)
- `db/alembic/versions/` — migration nova (id ≤32 chars)
- `db/repositories/user_sound_pref_repo.py` — **novo** (molde [saved_filter_repo.py](../db/repositories/saved_filter_repo.py))
- `server/routes/` — `GET/PUT /api/me/sound-prefs` (molde [saved_filters.py:34-92](../server/routes/saved_filters.py#L34)) + `GET /api/sounds/catalog`

**Backend — F4 (opcional):**
- [server/state.py:57-100](../server/state.py#L57) — registry `user_id → sockets` + `send_to_user`
- [server/routes/websocket.py](../server/routes/websocket.py) — passar `_user` no `connect`
- [server/routes/contacts.py:1284](../server/routes/contacts.py#L1284) + [app/services/conversation_service.py:118](../app/services/conversation_service.py#L118) — envio direcionado

**Testes:**
- [tests/test_endpoints.py](../tests/test_endpoints.py) — `/api/me/sound-prefs` (uid válido + uid=None), `sound_settings` no config; ajustar stub `ws_manager.broadcast` (`:~5044`) se F4
- `web/static/js/utils/soundEngine.test.js` — **novo** (`node --test`, resolvedor 3-tier)

**Plugin opcional (fora do MVP):**
- `custom_sounds` recuperável de `git show ccaebc4^:assets/plugin_examples/custom_sounds/*`

---

## 11. Checklist de verificação (aplicar a cada fase)

- [ ] Reload da página + back/forward: a tela de sons e o toggle sobrevivem
- [ ] `tests/test_endpoints.py` verde no Postgres de teste (`WHATSBOT_TEST_DB_URL`)
- [ ] `node --test` verde no `soundEngine.test.js` (resolvedor puro)
- [ ] Migration round-trip (`alembic upgrade head` + downgrade) sem erro; revision id ≤32 chars
- [ ] Modo escuro legível na tela nova (`wa-*` / `.wa-field`, sem cor crua)
- [ ] **1 mensagem inbound = 1 som** (guard `authoritative` preservado)
- [ ] "Som só para o outro atendente" continua: quem atribui **não** ouve, o destinatário **sim**
- [ ] Volume da sirene passa a responder ao slider (não mais fixo em 0.3)
- [ ] Preview toca sem salvar; a mudança salva aplica na hora (evento `whatsbot:notif-prefs`)
- [ ] Preferência sincroniza em outro navegador logado com o mesmo usuário (per-user)
- [ ] Instalação zero-user (`uid=None`): tela funciona caindo no padrão global, sem erro
- [ ] Salvar config no ConfigPanel não trava o botão em "✓ Salvo!" (bug F0 corrigido)
- [ ] Nenhum segredo em URL; sem regressão de evento/filtro de plugin
