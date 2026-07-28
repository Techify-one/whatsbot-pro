# Plano 84 — Alertas da conta Meta (template pausado, qualidade, limite) num grupo do Telegram, configurados no próprio plugin `whatsapp_cloud`

> **Status:** PLANEJAMENTO · **Data:** 2026-07-27 · **Escopo:** médio (1 ramo genérico no core + 1 provider fino + motor de alerta no plugin + tela de config + testes + distribuição)
> **Origem:** investigação de produção nesta sessão (instância **Redes Brasil**, canal `whatsapp_cloud_bc081279`). O operador perguntou "algum template meu fica em risco?" e a resposta só pôde ser dada **lendo o log manualmente** — o painel não sabe nada sobre a saúde da conta Meta. **Método:** análise de `logs_whatsbot.jsonl` (5.052 linhas do plugin `debug_bus`, janela 08:10–10:57 BRT de 27/07) + leitura do código com `arquivo:linha` verificado + `diff` de paridade das cópias do plugin.
> Medições que motivam o plano: das **1.074** notificações de webhook recebidas, **100% eram do campo `messages`** — zero `account_update`, zero `message_template_status_update`. Os **6 templates** em uso são **todos MARKETING** (extraído de `statuses[].pricing.category`). Houve **18 falhas de envio** em 2h47 (15× `131047`, 1× `131049`, 1× `131026`, 1× `130472`) sem nenhum aviso fora do fio da conversa. E o `quality_rating` do número **já é lido a cada chamada de status e descartado** ([channels.py:379](../assets/plugin_examples/whatsapp_cloud/channels.py#L379)).
> O plugin `whatsapp_cloud` passa a **detectar** eventos de conta/template (via webhook e via polling) e **avisar num grupo do Telegram**, com a configuração inteira na aba **Configurar** do próprio plugin. O motor de alerta é um **port do precedente já em produção** ([gowa/alerts.py](../assets/plugin_examples/gowa/alerts.py)), não uma invenção nova.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 1. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| **D1** ✅ 2026-07-27 | **A configuração fica em Gerenciar Plugins → Configurar do `whatsapp_cloud`.** Nada no painel de Configurações do core. | Regra do [CLAUDE.md](../CLAUDE.md) ("Onde fica a configuração de um plugin"). A screen `config: true` do plugin **já existe** ([plugin.yaml:22-27](../assets/plugin_examples/whatsapp_cloud/plugin.yaml#L22)) — ganha uma seção nova, sem tela nova. Ver F.P.#2. |
| **D2** ✅ 2026-07-27 | **O destino é um grupo do Telegram.** | Bot API direta (`api.telegram.org`), **não** o canal Telegram do sistema. Ver F.P.#1. |
| **D3** ✅ 2026-07-27 | **Cobrir também "outras coisas úteis"**, não só template caindo: qualidade do número, limite de mensagens, restrição da conta e **falhas de envio** (131049/131026/130429). | O plano define um **catálogo de alertas** (§6) com liga/desliga por grupo de alerta, não um alerta único. |
| **D4** ✅ 2026-07-27 | **Só planejar. Não implementar nesta rodada.** | Este arquivo é o entregável. |
| **P1** (princípio) | Padrão do repo: **o provider declara, o core só avalia** — nenhum `if provider ==` no core. | O core ganha **um ramo genérico** por `kind`, espelhando o que o plano 82 acabou de fazer com `kind="system"` ([channel_webhook.py:545](../server/routes/channel_webhook.py#L545)). |
| **P2** (princípio) | **Plugin autossuficiente**: um plugin não importa código de outro (D2·F9 do plano 76 — o `facebook_messenger` carrega a própria cópia de `meta_graph`). | O motor de Telegram é **copiado** de `gowa/alerts.py`, não importado. Duas cópias é o preço do zip autossuficiente. |

---

## 2. Resumo executivo

Hoje o WhatsBot é **cego** para tudo que a Meta diz sobre a *conta* — só escuta o campo `messages`. Se um template for pausado por baixa qualidade, se o número cair de qualidade ou se o tier de mensagens for cortado, o operador só descobre quando um envio começa a falhar. A causa é dupla e ambas as pontas estão medidas: **(a)** o `parse_inbound` do plugin percorre exclusivamente `value.messages[]` e `value.statuses[]` ([channels.py:1007-1017](../assets/plugin_examples/whatsapp_cloud/channels.py#L1007)) e **ignora o `change["field"]`** — um `change` de `account_update` produz **zero** `InboundEvent` e some sem log; **(b)** o `_dispatch_events` do core roteia por `kind` e **não tem `else`** ([channel_webhook.py:277-611](../server/routes/channel_webhook.py#L277)), então mesmo um evento novo seria descartado em silêncio.

A solução tem três camadas, todas com precedente direto no repo:

1. **Captura** — o provider passa a emitir `InboundEvent(kind="account")` para todo `change` cujo `field` **não** seja `messages`, e o core ganha **um ramo genérico** `elif kind == "account":` que **não toca em contato nem conversa** (esses eventos são da WABA, não de um cliente) e só emite o evento de bus **`channel.account_event`**. É o gêmeo exato do `channel.system_event` do plano 82.
2. **Detecção sem depender da Meta** — um loop de polling lê o `quality_rating` que o `status()` **já busca e joga fora** ([channels.py:379](../assets/plugin_examples/whatsapp_cloud/channels.py#L379)) e alerta na variação. Isto funciona **antes** de qualquer configuração no App Dashboard e é o cinto de segurança do plano (ver R3).
3. **Notificação** — um `alerts.py` no plugin assina `channel.account_event` + `message.failed` (bus que **já existe**, [channel_webhook.py:404](../server/routes/channel_webhook.py#L404)) e manda a mensagem ao grupo do Telegram, com **agregação e cooldown** (sem isso as 15 falhas medidas em 3h viram 15 mensagens no grupo). Token/chat_id/intervalo/fuso e os liga-desliga por grupo de alerta vivem na aba **Configurar** do plugin.

⚠️ **Pré-requisito operacional que nenhum código resolve:** os campos precisam estar **assinados no App Dashboard da Meta**. Sem isso a camada (1) nunca recebe nada — por isso a camada (2) existe e por isso a F1 é uma fase de **operação + documentação na tela**, não de código.

---

## 3. Como funciona hoje (mapa)

### 3.1 O caminho de um webhook da Meta

| # | Etapa | Arquivo:linha | O que acontece com `field: "messages"` | O que acontece com `field: "account_update"` |
|---|---|---|---|---|
| 1 | Body cru + assinatura | [channel_webhook.py:637-668](../server/routes/channel_webhook.py#L637) | lê bytes, `verify_inbound_signature` | idem — mas o Cloud **não** sobrescreve o hook (ver 3.4) |
| 2 | Filtro de plugin | [channel_webhook.py:673](../server/routes/channel_webhook.py#L673) | `apply_filter("filter.webhook.payload", raw, {})` | **idem — é o único ponto que já vê esse payload hoje** |
| 3 | Buffers de debug | [channel_webhook.py:676-685](../server/routes/channel_webhook.py#L676) | grava em `_RECENT` | idem |
| 4 | `parse_inbound` | [channels.py:971](../assets/plugin_examples/whatsapp_cloud/channels.py#L971) | 1 evento por `messages[]` + 1 por `statuses[]` | **0 eventos** — o `for` só olha `messages`/`statuses` ([:1007](../assets/plugin_examples/whatsapp_cloud/channels.py#L1007) e [:1016](../assets/plugin_examples/whatsapp_cloud/channels.py#L1016)) |
| 5 | Dispatch | [channel_webhook.py:277](../server/routes/channel_webhook.py#L277) | roteia por `kind` | lista vazia ⇒ `handled=0`, log `→ 0 evento(s) parseado(s)` e **fim** |

⚠️ **Gotcha decisivo:** `change["field"]` é lido **em lugar nenhum** do plugin (`grep field` em `channels.py` só acha `credential_fields`/`config_fields`/`"fields"` de query da Graph). O discriminador que a Meta usa para separar "mensagem" de "aviso da conta" é justamente esse — e ele é descartado na entrada.

### 3.2 O dispatch por `kind` (e por que um `kind` novo precisa de um ramo)

Ramos existentes em `_dispatch_events`: `message`, `reaction`, `receipt`, `edited`, `revoked`, `deleted`, `presence`, `group_participants`, `group_joined`, `call`, `newsletter`, `system`. **Não há `else`** ([:277-611](../server/routes/channel_webhook.py#L277)) ⇒ um `kind` desconhecido é engolido sem log. O ramo `system` do plano 82 ([:545-607](../server/routes/channel_webhook.py#L545)) é o **molde exato** do que a F2 vai escrever: resolve o mínimo, **não** chama `ingest`, emite um evento de bus próprio e incrementa `handled`.

### 3.3 O que já existe a favor (nada disto precisa ser inventado)

| Peça pronta | Onde | Como será reusada |
|---|---|---|
| Motor de alerta Telegram **em produção** | [gowa/alerts.py](../assets/plugin_examples/gowa/alerts.py) — `_tg_call`([:215](../assets/plugin_examples/gowa/alerts.py#L215)), `_tg_send`([:243](../assets/plugin_examples/gowa/alerts.py#L243)), `_tg_edit`([:255](../assets/plugin_examples/gowa/alerts.py#L255)), `_tg_delete`([:271](../assets/plugin_examples/gowa/alerts.py#L271)), `_tick`([:351](../assets/plugin_examples/gowa/alerts.py#L351)), `disconnect_alert_loop`([:449](../assets/plugin_examples/gowa/alerts.py#L449)) | **Copiado** (P2) para `whatsapp_cloud/alerts.py`. Inclui de graça: HTML, `disable_web_page_preview`, retry transparente quando o grupo vira supergrupo (`migrate_to_chat_id`, [:224-236](../assets/plugin_examples/gowa/alerts.py#L224)), fuso configurável e estado que sobrevive a restart |
| Rotas de config do alerta | [gowa/routes.py:74](../assets/plugin_examples/gowa/routes.py#L74) (GET), [:110](../assets/plugin_examples/gowa/routes.py#L110) (PUT), [:141](../assets/plugin_examples/gowa/routes.py#L141) (POST `/alert-test`) | Molde das 3 rotas novas; já gateadas por `core_permission("channel.manage")` (plano 81) |
| UI da seção de alerta | [gowa/static/gowa.js:43](../assets/plugin_examples/gowa/static/gowa.js#L43) (`DisconnectAlerts`) + `browserTimezone()`([:37](../assets/plugin_examples/gowa/static/gowa.js#L37)) | Molde da seção nova em [whatsapp_cloud.js](../assets/plugin_examples/whatsapp_cloud/static/whatsapp_cloud.js) (que hoje tem 213 linhas e é só ajuda/documentação) |
| Evento de bus de falha de envio | [channel_webhook.py:404](../server/routes/channel_webhook.py#L404) — `message.failed` com `error_code`, `error_title`, `is_new` | Assinado direto em `events.py`; o campo **`is_new` já é o guard de dedupe** contra a reentrega de rotina da Meta |
| Catálogo de códigos → PT-BR | [server/message_errors.py:30-48](../server/message_errors.py#L30) — inclui `132015` (pausado) e `132016` (desabilitado) | Reusado no texto do alerta via `from server.message_errors import describe_failure` (é core, import legítimo) |
| `quality_rating` já buscado | [channels.py:379](../assets/plugin_examples/whatsapp_cloud/channels.py#L379), dentro de `status()`([:351](../assets/plugin_examples/whatsapp_cloud/channels.py#L351)) | Vira a fonte do alerta de qualidade por polling (F4) — **hoje é lido e descartado** |
| Screen `config: true` | [plugin.yaml:22-27](../assets/plugin_examples/whatsapp_cloud/plugin.yaml#L22) | Já registrada; só ganha conteúdo |
| Supervisão de task de plugin | `ctx.spawn_task` ([plugins/context.py:350](../plugins/context.py#L350)); uso real em [gowa/lifecycle.py](../assets/plugin_examples/gowa/lifecycle.py) | O plugin **ainda não tem** `lifecycle` no `entry` — a F4 adiciona |

### 3.4 Estado das credenciais e da assinatura (relevante para a F1)

| Fato | Verificação | Consequência |
|---|---|---|
| Credenciais do canal: `access_token`, `phone_number_id`, `waba_id`, `app_id`, `verify_token` | [channels.py:263-282](../assets/plugin_examples/whatsapp_cloud/channels.py#L263) | **Não existe `app_secret`** |
| `verify_inbound_signature` **não** é sobrescrito pelo Cloud | `grep app_secret\|verify_inbound_signature` em `channels.py` = **0 ocorrências**; herda o default `True` ([base.py:528](../channels/base.py#L528)) | O webhook do Cloud **não é verificado hoje** — só a URL (com o `channel_id`) o protege. Ver R5 |
| `POST /{waba_id}/subscribed_apps` já é chamado pelo plugin | [routes.py:270](../assets/plugin_examples/whatsapp_cloud/routes.py#L270) | Ele assina **o app na WABA** e seta `override_callback_uri` — **não escolhe os campos**. Ver F.P.#3 |

---

## 4. Inventário / análise

| # | Item | Arquivo:linha | O que falta | Abordagem | Risco | Esf. |
|---|---|---|---|---|---|---|
| 1 | Assinar os campos na Meta | App Dashboard (fora do repo) | `message_template_status_update`, `account_update`, etc. não assinados | operação manual + checklist na tela do plugin (F1); programático fica em **P2** | médio | S |
| 2 | Emitir evento para `field != "messages"` | [channels.py:988-1017](../assets/plugin_examples/whatsapp_cloud/channels.py#L988) | o `field` é ignorado | 1 ramo novo no loop de `changes`, antes do walk de `messages`/`statuses` | baixo | S |
| 3 | Ramo genérico no core | [channel_webhook.py:607](../server/routes/channel_webhook.py#L607) (após o `system`) | `kind="account"` seria descartado (sem `else`) | espelhar o ramo `system`: sem contato/conversa, só bus `channel.account_event` | médio | M |
| 4 | Motor de alerta no plugin | `whatsapp_cloud/alerts.py` (novo) | não existe | **port** de [gowa/alerts.py](../assets/plugin_examples/gowa/alerts.py) + agregação/cooldown | médio | L |
| 5 | Assinatura dos eventos | `whatsapp_cloud/events.py` (novo) | plugin não tem `entry.events` | `EVENT_HANDLERS = {"channel.account_event": …, "message.failed": …}` | baixo | S |
| 6 | Loop de qualidade (polling) | [channels.py:379](../assets/plugin_examples/whatsapp_cloud/channels.py#L379) | valor lido e descartado | `lifecycle.py` novo + `ctx.spawn_task`; compara com o último valor gravado | baixo | M |
| 7 | Estado / dedupe | migration nova `plugin_whatsapp_cloud_*` | não existe | 1 tabela de estado por chave de alerta (molde: [gowa/migrations/001](../assets/plugin_examples/gowa/migrations/001_disconnect_alerts.sql)) | baixo | S |
| 8 | Rotas de config | [whatsapp_cloud/routes.py:190](../assets/plugin_examples/whatsapp_cloud/routes.py#L190) | só `/info`, `/webhook-status`, `/set-webhook`, `/delete-webhook` | +3 rotas espelhando o gowa; **token mascarado no GET** | baixo | M |
| 9 | UI da config | [whatsapp_cloud.js](../assets/plugin_examples/whatsapp_cloud/static/whatsapp_cloud.js) (213 linhas) | seção não existe | seção "Alertas via Telegram" espelhando `DisconnectAlerts` | baixo | M |
| 10 | Testes | [tests/test_plano75_parse_inbound.py](../tests/test_plano75_parse_inbound.py) | nenhum cobre `field != messages` | parse → kind; dispatch → bus; formatação pura; agregação pura | médio | M |
| 11 | Distribuição | [assets/channel_plugins/whatsapp_cloud-plugin.zip](../assets/channel_plugins/) | — | bump de versão + zip + importar em prod **depois** do core | médio | S |

### Falsos positivos descartados

| # | Hipótese | Por que NÃO é o caminho |
|---|---|---|
| **1** | "Mandar o alerta pelo **canal Telegram** do sistema (plugin `telegram`)." | ❌ Três problemas: (a) criaria **contato + conversa** no painel — o alerta viraria atendimento; (b) acopla dois plugins, violando P2; (c) exigiria o plugin `telegram` instalado e um canal configurado. O precedente `gowa/alerts.py` **já decidiu isto** e documenta no cabeçalho: *"100% contido neste plugin — NÃO usa a caixa de entrada/canal Telegram do sistema"*. |
| **2** | "Colocar a config numa aba nova em Configurações." | ❌ Proibido pelo [CLAUDE.md](../CLAUDE.md) ("Nunca adicione uma seção/aba nova ao painel de Configurações padrão para algo que pertence a um plugin") e contra **D1**. |
| **3** | "Basta chamar `POST /{waba_id}/subscribed_apps` que o plugin já tem ([routes.py:270](../assets/plugin_examples/whatsapp_cloud/routes.py#L270))." | ❌ Esse endpoint assina **o app na WABA** e define o `override_callback_uri`; ele **não seleciona os campos** (`messages`, `account_update`, …). A seleção de campos é do **objeto do App** (App Dashboard → Webhooks → `whatsapp_business_account`), ou via `POST /{app_id}/subscriptions` — que exige **app access token** (`{app_id}\|{app_secret}`) e o plugin **não tem `app_secret`** ([channels.py:263-282](../assets/plugin_examples/whatsapp_cloud/channels.py#L263)). Ver **P2**. |
| **4** | "`message_errors.py` já traduz 132015/132016, então o operador já é avisado." | ❌ Aquilo só dispara **quando você tenta enviar e falha** — é reativo e aparece só no fio de uma conversa. O plano é sobre saber **antes** (push da Meta) e **fora** do painel (grupo do Telegram). |
| **5** | "Reusar o `kind="system"` do plano 82." | ❌ Aquele ramo anexa um card à **conversa de um contato** ([channel_webhook.py:545-607](../server/routes/channel_webhook.py#L545)). Um `account_update` é da **WABA** — não tem `chat_id`, não tem contato, não tem conversa. Reusar geraria card órfão ou nada. Kind próprio. |
| **6** | "Usar `filter.webhook.payload` e resolver tudo dentro do plugin, sem tocar no core." | ⚠️ **Funciona e é o plano B** (é literalmente o único gancho que hoje enxerga esses payloads — [channel_webhook.py:673](../server/routes/channel_webhook.py#L673)), mas tem 3 defeitos: o `ctx` é **vazio** (`{}`) ⇒ o plugin não sabe o `channel_id` nem o provider e teria que adivinhar pelo `entry[].id`; o filtro roda para **todos** os providers (GOWA/Telegram/website) em **todo** inbound — custo no hot path; e o gancho ficaria **privado** do plugin, enquanto `channel.account_event` serve qualquer plugin futuro (dashboard de qualidade, auditoria). Ver **P1**. |
| **7** | "Incluir alerta de saldo do LLM." | ⚠️ **Fora de escopo**: o saldo é do proxy **Techify**, não da Meta, e **já existe** (`GET /api/balance` + WS `low_balance` + `LowBalanceModal.js`). O "limite" que entra aqui é o **messaging limit tier da Meta**. Ver **P4**. |
| **8** | "Alertar todo `message.failed`." | ❌ Vira spam: **15 falhas `131047` medidas em 2h47**, todas do mesmo fluxo. O alerta de falha nasce **agregado por código, com janela e cooldown** (F5), e o `131047` entra **desligado por padrão** (é erro de operação, não de conta). |

---

## 5. Mudanças de infraestrutura (por camada)

**Contrato (`channels/`):** nenhuma mudança de schema — `InboundEvent.kind` é string livre ([channels/events.py:18](../channels/events.py#L18)). Só se documenta o valor novo **`"account"`** ao lado de `"system"`.

**Core (`server/routes/channel_webhook.py`):** **um** ramo novo, genérico, sem `if provider ==`, colocado após o ramo `system` ([:607](../server/routes/channel_webhook.py#L607)):
- não resolve contato, não resolve conversa, **não** chama `deps.ingest_event`, **não** grava mensagem;
- emite `channel.account_event` com `{channel_id, provider, field, value, ts, raw}`;
- `handled += 1` (para o log `→ N evento(s)` deixar de mentir);
- herda o `try/except` do laço ([:608](../server/routes/channel_webhook.py#L608)) — um alerta que falha nunca derruba o webhook.

**Provider (`assets/plugin_examples/whatsapp_cloud/channels.py`):** em `parse_inbound` ([:988](../assets/plugin_examples/whatsapp_cloud/channels.py#L988)), dentro do `for change in changes:`, antes do walk de `messages`/`statuses`:
```python
field = (change or {}).get("field") or ""
if field and field != "messages":
    events.append(InboundEvent(kind="account", direction="in", channel_id=…,
                               media_extras={"field": field, "value": value}, raw=change))
    continue
```
⚠️ O `continue` é importante: um `change` de conta **não** tem `messages`/`statuses`, e sem ele o laço seguiria fazendo dois `for` vazios (inofensivo, mas ruidoso).

**Plugin (`whatsapp_cloud/`) — arquivos novos:**

| Arquivo | Papel |
|---|---|
| `alerts.py` | motor: formatação PT-BR, agregação/cooldown, Bot API do Telegram, loop de qualidade |
| `events.py` | `EVENT_HANDLERS = {"channel.account_event": …, "message.failed": …}` |
| `lifecycle.py` | `setup(ctx)` → `ctx.spawn_task("quality_poll", …)` (`RestartPolicy.PERMANENT`) |
| `migrations/001_alerts.sql` | tabela `plugin_whatsapp_cloud_alert_state` (prefixo obrigatório) |
| `plugin.yaml` | `entry.events`, `entry.filters`(não), `entry.lifecycle`, `migrations: migrations`, `version` bump, `permissions: +runtime.task` |

**DB:** 1 migration **de plugin** (não Alembic). Tabela `plugin_whatsapp_cloud_alert_state (alert_key TEXT PRIMARY KEY, last_value TEXT, last_alert_ts DOUBLE PRECISION, count INTEGER, telegram_message_id BIGINT, telegram_chat_id TEXT)`. ⚠️ Comentários SQL **sem `;`** (o migrator splita por `;` antes de tirar comentários — ver [gowa/migrations/001](../assets/plugin_examples/gowa/migrations/001_disconnect_alerts.sql) e a nota lá dentro).

**Frontend:** só a seção nova na screen `config:true` do plugin. **Nenhuma** tela do core muda.

---

## 6. Catálogo de alertas (o "e outras coisas também" do D3)

Cada grupo tem liga/desliga próprio na tela. Origem `webhook` = precisa da F1 (campo assinado); origem `bus`/`polling` = funciona sem nada configurado na Meta.

| Grupo | Origem | Gatilho | Por que importa | Default |
|---|---|---|---|---|
| **Template caiu** | webhook `message_template_status_update` | status → `PAUSED`, `REJECTED`, `DISABLED` | é a pergunta que originou o plano | **ON** |
| **Template voltou** | webhook `message_template_status_update` | status → `APPROVED` | fecha o ciclo do alerta acima | ON |
| **Template recategorizado** | webhook `template_category_update` | `UTILITY → MARKETING` | muda custo **e** expõe ao cap `131049` | ON |
| **Qualidade do número** | webhook `phone_number_quality_update` **+ polling** (F4) | `GREEN → YELLOW → RED` | precede corte de tier | **ON** |
| **Limite de mensagens** | webhook (`account_update`/`business_capability_update`) | mudança de tier | teto de destinatários/dia | ON |
| **Conta restrita / em revisão** | webhook `account_update` | restrição, ban, revisão | pior caso | ON |
| **Falha de envio relevante** | bus `message.failed` | `131049`, `131026`, `130429`, `132015`, `132016`, `131056` | agregado por código, com cooldown | ON |
| **Falha por janela de 24h** | bus `message.failed` | `131047` | 15 em 2h47 na medição — é erro de **operação**, vira spam | **OFF** |

⚠️ **A confirmar na execução (F1):** os nomes exatos dos campos e o formato de `value` de cada um vêm da doc oficial da Meta (Webhooks → `whatsapp_business_account`). O plano assume os nomes acima como **hipótese**; a F1 confirma contra a doc e contra um payload real. O código deve tratar campo desconhecido de forma **genérica** (alerta bruto com `field` + JSON resumido) em vez de ignorar — assim um campo novo da Meta nunca fica invisível.

---

## 7. Waves e paralelização

```
WAVE 0   F0 (baseline + paridade das 3 cópias)                          🔴 barreira
             │
             ├──────────────────────────────┐
             ▼                              ▼
WAVE 1   F1 (assinar campos na Meta +     F2 (provider: kind="account")  🟢
             doc na tela)  🟢              F3 (core: ramo genérico)      🟢
             │  [F1 é operação, não trava código]   └── arquivos disjuntos
             ▼
WAVE 2   F4 (alerts.py: motor + Telegram + polling de qualidade)  🔴  [dep: F2+F3]
             │
             ├──────────────────────────────┐
             ▼                              ▼
WAVE 3   F5 (alerta de falhas, bus)  🟢    F6 (rotas + UI de config)  🟢   [dep: F4]
             ▼
WAVE 4   F7 (testes)  🔴  [dep: F2..F6]
             ▼
WAVE 5   F8 (zip + deploy)  🔴  [dep: F7]
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | Baseline | 🔴 sozinha | baixo | 3 cópias idênticas confirmadas + suíte verde |
| 1 | **F1** | Operação/Meta | 🟢 | médio | um `account_update` real aparece em `/api/channel-webhook-payloads` |
| 1 | **F2** | Provider | 🟢 `[bloqueia: F4]` | baixo | payload de `account_update` → 1 evento `kind="account"` |
| 1 | **F3** | Core | 🟢 `[bloqueia: F4]` | médio | evento `kind="account"` injetado emite `channel.account_event` e **não** cria contato/conversa |
| 2 | **F4** | Plugin/motor | 🔴 sozinha `[dep: F2+F3]` | médio | mensagem chega no grupo do Telegram em teste ponta a ponta |
| 3 | **F5** | Plugin/bus | 🟢 `[dep: F4]` | médio | 10 falhas iguais viram **1** mensagem agregada |
| 3 | **F6** | Rotas + UI | 🟢 `[dep: F4]` | baixo | Configurar → salvar → "Testar" chega no grupo; token mascarado no GET |
| 4 | **F7** | Testes | 🔴 sozinha | médio | suíte verde no Postgres + `node --test` dos módulos puros |
| 5 | **F8** | Distribuição | 🔴 sozinha | médio | zip importado em prod **depois** do core; alerta real observado |

**Despache junto:** `F1 · F2 · F3` (wave 1) e `F5 · F6` (wave 3). F0, F4, F7, F8 são sequenciais.

---

## 8. Fases

### F0 — Baseline e paridade das 3 cópias 🔴

**Objetivo:** garantir que se edita o código que roda em produção e travar o comportamento atual.

**Itens** `[sequencial]`:
1. `diff` entre `assets/plugin_examples/whatsapp_cloud/` × `storages/plugins/whatsapp_cloud/` × o `.zip` de [assets/channel_plugins/](../assets/channel_plugins/). *(Na investigação: `channels.py` e `routes.py` **idênticos**, ambos `plugin.yaml` em `1.6.0` — reconfirmar, e conferir o zip, que não foi extraído.)* ⚠️ Memória do repo: versão igual **não** garante conteúdo igual — compare bytes.
2. Rodar `tests/test_plano75_parse_inbound.py` e registrar o baseline verde.
3. Registrar quantos eventos um payload de `account_update` produz hoje (esperado: **0**) — é o número que a F2 inverte.

**Pronto quando:** as 3 cópias batem, suíte verde e o baseline "0 eventos" registrado.

#### Status de execução — Fase 0
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### F1 — Assinar os campos na Meta + documentar na tela 🟢

**Objetivo:** fazer a Meta **enviar** os avisos. Sem esta fase, F2/F3 ficam corretos e mudos.

**Itens:**
1. `[sequencial]` Confirmar na doc oficial da Meta os nomes exatos dos campos do objeto `whatsapp_business_account` e o formato de `value` de cada um (§6 é hipótese).
2. `[sequencial]` No App Dashboard → WhatsApp → Configuração → Webhooks, assinar os campos do catálogo além de `messages`.
3. `[paralelo]` Escrever o **checklist na tela do plugin** (F6) com os nomes dos campos e o caminho no painel da Meta — é o que impede a próxima instalação de nascer muda.
4. `[paralelo]` Validar com um evento real: pausar/despausar um template de teste e conferir em `GET /api/channel-webhook-payloads` ([channel_webhook.py:730](../server/routes/channel_webhook.py#L730)).

**Pronto quando:** um `change` com `field != "messages"` aparece no buffer de payloads. ⚠️ Este é o **único critério** que prova a fase; código verde não prova nada aqui.

#### Status de execução — Fase 1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(campos realmente assinados; nomes confirmados × hipótese do §6)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(payload real observado? qual field?)_

---

### F2 — Provider: `field != "messages"` vira `kind="account"` 🟢

**Objetivo:** parar de descartar o que a Meta manda.

**Itens** `[sequencial]`:
1. Em `parse_inbound` ([channels.py:988](../assets/plugin_examples/whatsapp_cloud/channels.py#L988)), ler `change["field"]` e, quando diferente de `messages`, emitir `InboundEvent(kind="account", …, media_extras={"field":…, "value":…}, raw=change)` + `continue` (§5).
2. `chat_id`/`sender_id` ficam **vazios** de propósito — não há contato. O ramo do core (F3) não os usa.
3. Espelhar a mudança em `storages/plugins/whatsapp_cloud/channels.py`.

**Pronto quando:** um payload sintético de `message_template_status_update` produz exatamente **1** evento `kind="account"`, e um payload de `messages` continua produzindo **exatamente** o que produzia antes (regressão zero).

#### Status de execução — Fase 2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### F3 — Core: ramo genérico `kind="account"` 🟢

**Objetivo:** um gancho de bus para eventos de **conta**, disponível a qualquer provider/plugin.

**Itens** `[sequencial]`:
1. Novo `elif kind == "account":` após o ramo `system` ([channel_webhook.py:607](../server/routes/channel_webhook.py#L607)), espelhando-o em forma e disciplina.
2. Corpo: **nada** de contato/conversa/mensagem; `emit_with_filter("channel.account_event", {channel_id, provider, field, value, ts, raw})`; `handled += 1`.
3. Documentar o `kind` novo em [channels/events.py:18](../channels/events.py#L18), ao lado de `"system"`.

**Pronto quando:** injetar `InboundEvent(kind="account")` no dispatch emite o evento de bus, **não** cria contato nem conversa, **não** grava mensagem e **não** chama `ingest_event`.

#### Status de execução — Fase 3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### F4 — Motor de alerta no plugin (Telegram + polling de qualidade) 🔴

**Objetivo:** transformar evento em mensagem no grupo, sem spam e sobrevivendo a restart.

**Itens:**
1. `[sequencial]` `alerts.py`: **port** de [gowa/alerts.py](../assets/plugin_examples/gowa/alerts.py) — `_tg_call`/`_tg_send`/`_tg_edit`/`_tg_delete`, config lida **a cada ciclo** (editar não exige restart), fuso, e o retry de `migrate_to_chat_id`. Prefixo de config `plugin.whatsapp_cloud.alert_*`.
2. `[sequencial]` Migration `001_alerts.sql` com a tabela de estado (§5). ⚠️ sem `;` em comentário.
3. `[paralelo]` Formatação PT-BR por grupo de alerta (§6), com **função pura** `format_alert(kind, payload) -> str` (testável sem rede).
4. `[paralelo]` **Agregação/cooldown**: função pura `should_alert(state, key, now, cooldown) -> bool` + contador. Regra: 1ª ocorrência alerta na hora; repetições dentro da janela **incrementam contador** e re-editam a mensagem existente (`editMessageText`) em vez de mandar outra.
5. `[sequencial]` `lifecycle.py` + `entry.lifecycle` no manifest: `ctx.spawn_task("quality_poll", …, policy=RestartPolicy.PERMANENT)` lendo `status()["quality_rating"]` ([channels.py:379](../assets/plugin_examples/whatsapp_cloud/channels.py#L379)) e alertando **na variação** do valor gravado. Cadência sugerida: 5–15 min (≫ que os 30 s do gowa — qualidade muda devagar e cada tick é uma chamada Graph).
6. `[sequencial]` `events.py` com `EVENT_HANDLERS = {"channel.account_event": …}` + `entry.events` no manifest.

**Pronto quando:** com o alerta ligado e um `channel.account_event` sintético emitido, a mensagem chega no grupo do Telegram; derrubar/religar o servidor no meio não duplica o alerta.

#### Status de execução — Fase 4
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(cadência escolhida; o que foi copiado × adaptado do gowa)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### F5 — Alerta de falha de envio (bus `message.failed`) 🟢

**Objetivo:** avisar no grupo quando as falhas passam de ruído a padrão — sem virar spam.

**Itens:**
1. `[sequencial]` Assinar `message.failed` em `events.py`; o payload já traz `error_code`/`error_title`/`is_new` ([channel_webhook.py:404](../server/routes/channel_webhook.py#L404)).
2. `[sequencial]` **Guard de dedupe primeiro**: `if not payload.get("is_new"): return` — a Meta reentrega o mesmo `failed` de rotina.
3. `[paralelo]` Filtrar por código conforme §6 (`131047` **OFF** por padrão).
4. `[paralelo]` Agregar por `(error_code, janela)` reusando `should_alert` da F4; o texto usa `describe_failure` de [server/message_errors.py:69](../server/message_errors.py#L69) — **não** reescrever as frases.

**Pronto quando:** 10 eventos `message.failed` do mesmo código em sequência produzem **1** mensagem no grupo (com contagem), e 1 evento de código desligado produz **0**.

#### Status de execução — Fase 5
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### F6 — Rotas + tela de configuração (D1) 🟢

**Objetivo:** o operador liga, escolhe o grupo, testa e escolhe o que quer receber — tudo em Gerenciar Plugins → Configurar.

**Itens:**
1. `[sequencial]` 3 rotas em [whatsapp_cloud/routes.py](../assets/plugin_examples/whatsapp_cloud/routes.py), espelhando [gowa/routes.py:74/110/141](../assets/plugin_examples/gowa/routes.py#L74), todas com `dependencies=[core_permission("channel.manage")]` (plano 81): `GET /alert-settings`, `PUT /alert-settings`, `POST /alert-test`.
2. `[sequencial]` ⚠️ **Token mascarado no GET** — devolver só `has_token: bool` (+ últimos 4), nunca o valor; o PUT só grava quando vem um token não-vazio. É o contrato que o gowa já usa ([routes.py:87-89](../assets/plugin_examples/gowa/routes.py#L87) e [:134](../assets/plugin_examples/gowa/routes.py#L134)).
3. `[paralelo]` Seção "Alertas via Telegram" em [whatsapp_cloud.js](../assets/plugin_examples/whatsapp_cloud/static/whatsapp_cloud.js), espelhando `DisconnectAlerts` ([gowa.js:43](../assets/plugin_examples/gowa/static/gowa.js#L43)): token, chat_id, intervalo, fuso (auto do navegador via `browserTimezone()`), **checkboxes por grupo de alerta** (§6) e botão **Testar**.
4. `[paralelo]` Bloco de ajuda com o checklist da F1 (quais campos assinar e onde).
5. `[sequencial]` **Modo escuro**: só `wa-*` e `.wa-field`; conferir com o tema escuro ligado.

**Pronto quando:** salvar → "Testar" entrega a mensagem no grupo; recarregar a tela não vaza o token; o form fica legível no tema escuro.

#### Status de execução — Fase 6
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### F7 — Testes 🔴

**Objetivo:** travar o comportamento novo sem tocar a rede.

**Itens** `[paralelo entre si]`:
1. **Parse** (`tests/`, molde [test_plano75_parse_inbound.py](../tests/test_plano75_parse_inbound.py)): `account_update` → 1 evento `kind="account"`; `messages` → **inalterado** (regressão).
2. **Dispatch**: `kind="account"` emite `channel.account_event` e **não** cria contato/conversa/mensagem (o teste mais importante do plano).
3. **Puros**: `format_alert` e `should_alert` (agregação/cooldown/contador) — sem rede, sem DB.
4. **Falhas**: `is_new=False` não alerta; 10× mesmo código = 1 mensagem; código desligado = 0.
5. **Rotas**: GET não devolve o token; PUT vazio não apaga o token salvo.

**Pronto quando:** suíte verde no Postgres (`WHATSBOT_TEST_DB_URL`) e nenhum teste existente do plano 75 quebrado.

#### Status de execução — Fase 7
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### F8 — Distribuição e deploy 🔴

**Objetivo:** colocar em produção na ordem certa.

**Itens** `[sequencial]`:
1. Bump de `version` no `plugin.yaml` (1.6.0 → 1.7.0) nas **duas** fontes.
2. Sincronizar `assets/plugin_examples/whatsapp_cloud/` → `storages/plugins/whatsapp_cloud/` e regenerar `assets/channel_plugins/whatsapp_cloud-plugin.zip`.
3. ⚠️ **Core ANTES do zip**: o plugin emite `kind="account"`, que só vira alerta se o ramo da F3 existir. Zip novo em core velho = eventos descartados em silêncio (regride ao estado de hoje, sem quebrar nada — mas some o alerta).
4. Importar o zip em produção (tela Plugins → Importar `.zip`) e **reiniciar** (o toggle já força restart).
5. Validar com um evento real (pausar um template de teste) e conferir a mensagem no grupo.

**Pronto quando:** o alerta real chega no grupo em produção.

#### Status de execução — Fase 8
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

## 9. Riscos e cuidados

| # | Ponto | Risco | Mitigação |
|---|---|---|---|
| **R1** | Volume de alertas | 15 falhas em 2h47 medidas ⇒ grupo inutilizável por ruído | Agregação + cooldown **desde a F4** (não como melhoria depois); `131047` OFF por default; contador em vez de nova mensagem |
| **R2** | Token do bot em `config` | segredo em texto na tabela `config` | Mesmo modelo já aceito no `gowa`; **mascarar no GET** (F6·2) e nunca logar. Não vai para a URL |
| **R3** | Campos não assinados na Meta | plano inteiro fica mudo e ninguém percebe | O **polling de qualidade** (F4·5) não depende de assinatura nenhuma; e a tela mostra o checklist da F1 |
| **R4** | Nomes/formatos dos campos são hipótese (§6) | tratar `value` errado ⇒ alerta vazio ou exceção | F1 confirma contra a doc **e** contra payload real; fallback **genérico** para `field` desconhecido (nunca ignorar) |
| **R5** | Webhook do Cloud **não** verifica assinatura ([3.4](#34-estado-das-credenciais-e-da-assinatura-relevante-para-a-f1)) | quem souber a URL pode forjar um `account_update` e disparar alerta falso | Fora do escopo corrigir aqui (é o status quo de todo inbound do canal), mas **registrar**: adicionar `app_secret` + override de `verify_inbound_signature` vale um plano próprio (**P3**) |
| **R6** | 4 lugares onde o plugin vive | editar a cópia errada = mudança que não roda | F0 (paridade) + F8 (ordem de deploy). Regra do [CLAUDE.md](../CLAUDE.md): comparar **conteúdo**, nunca só a versão |
| **R7** | Task supervisionada nova | loop com exceção derruba/reinicia em laço | `RestartPolicy.PERMANENT` + `try/except` largo por tick (padrão do `gowa/alerts.py`); cadência de minutos, não segundos |
| **R8** | Chamada Graph por tick | consumo de rate limit da conta | Cadência ≥5 min; ler o `status()` que **já é chamado** por outros caminhos quando possível (cache) |
| **R9** | Restart de plugin | enable/disable derruba o processo ([plugins/restart.py](../plugins/restart.py)) | Comportamento esperado; a config é lida **a cada ciclo**, então editar não exige restart |
| **R10** | Migration de plugin | `;` em comentário quebra o splitter | Documentado no molde do gowa; conferir antes de subir |

---

## 10. Perguntas em aberto

**P1 — Captura via `kind` novo (core) ou via `filter.webhook.payload` (zero core)?**
Contexto: hoje o único gancho que vê esses payloads é o filtro ([channel_webhook.py:673](../server/routes/channel_webhook.py#L673)), mas o `ctx` é vazio e ele roda para todos os providers.
(a) `kind="account"` + ramo genérico no core — segue P1, serve qualquer plugin futuro, custo: 1 ramo no core.
(b) Só o filtro, dentro do plugin — zero core, mas o plugin adivinha o `channel_id` pelo `entry[].id` e paga custo no hot path de todo inbound.
**Recomendação: (a)** — é o padrão que o plano 82 acabou de estabelecer e o custo é um `elif`. ⏸️ **A CONFIRMAR na execução** (se houver resistência a tocar o core, (b) entrega o mesmo alerta com menos elegância).

**P2 — Assinar os campos programaticamente?**
Exigiria `POST /{app_id}/subscriptions` com app access token (`{app_id}|{app_secret}`) e portanto uma credencial **`app_secret`** nova ([channels.py:263-282](../assets/plugin_examples/whatsapp_cloud/channels.py#L263) não tem). Além disso a chamada é **app-wide**: mexeria na configuração de **todas** as WABAs daquele app.
**Recomendação: ⏸️ ADIADO.** F1 manual + checklist na tela. Reavaliar junto com P3 (que traz o mesmo `app_secret`).

**P3 — Verificar a assinatura `X-Hub-Signature-256` no canal Cloud?**
Hoje não verifica (R5). Traz `app_secret` — a mesma credencial de P2.
**Recomendação: ⏸️ ADIADO para plano próprio** (é mudança de segurança do inbound inteiro, não de alerta).

**P4 — Incluir saldo do LLM no mesmo grupo de alertas?**
O saldo é da Techify e **já** tem `low_balance` + modal. Juntar no Telegram seria conveniente, mas é outro domínio (não é o canal Meta) e pertenceria a outro plugin.
**Recomendação: ⏸️ ADIADO** (F.P.#7).

**P5 — Alerta por canal ou global?**
O `gowa` resolveu com **liga/desliga por canal** (`channels.config.disconnect_alert_enabled`) e token/destino globais. Com mais de um canal Cloud, o alerta deveria dizer **qual** número caiu.
**Recomendação:** global no MVP (uma WABA), **com o `channel_id`/número sempre no texto**; migrar para per-canal se surgir a 2ª WABA. ⏸️ A CONFIRMAR na F4.

**P6 — Um grupo só ou um por severidade?**
**Recomendação:** um grupo só, com prefixo de severidade no texto (🔴/🟡/🟢). Multi-destino é complexidade sem demanda medida.

---

## 11. Checklist de verificação

- [ ] `diff` das 3 cópias do plugin **antes** e **depois** (conteúdo, não versão)
- [ ] Payload `messages` continua produzindo exatamente os mesmos eventos (regressão zero no hot path)
- [ ] `kind="account"` **não** cria contato, **não** cria/reabre conversa, **não** grava mensagem
- [ ] `tests/test_endpoints.py` verde
- [ ] Suíte verde no **Postgres** (`WHATSBOT_TEST_DB_URL`, banco com `test` no nome)
- [ ] Testes puros de `format_alert`/`should_alert` verdes
- [ ] Migration de plugin aplica limpa (e **sem `;`** em comentário)
- [ ] Restart do plugin (enable/disable) não duplica nem perde alerta
- [ ] `GET /alert-settings` **não** devolve o token; PUT vazio não apaga o salvo
- [ ] Nenhum segredo em URL nem em log
- [ ] Tela de config legível no **modo escuro** (`wa-*` / `.wa-field`)
- [ ] Rotas novas gateadas por `core_permission("channel.manage")` (plano 81)
- [ ] Deploy na ordem: **core → zip do plugin → restart → validação com evento real**

---

## 12. Apêndice — arquivos-chave

**Core (muda):**
- [server/routes/channel_webhook.py](../server/routes/channel_webhook.py) — ramo `kind="account"` (após [:607](../server/routes/channel_webhook.py#L607))
- [channels/events.py](../channels/events.py) — documentar o `kind` novo ([:18](../channels/events.py#L18))

**Core (só leitura/reuso):**
- [server/message_errors.py](../server/message_errors.py) — `describe_failure` ([:69](../server/message_errors.py#L69))
- [plugins/context.py](../plugins/context.py) — `spawn_task` ([:350](../plugins/context.py#L350)), `core_permission`
- [channels/base.py](../channels/base.py) — `verify_inbound_signature` ([:528](../channels/base.py#L528))

**Plugin `whatsapp_cloud` (muda — e a cópia em `storages/plugins/`):**
- [channels.py](../assets/plugin_examples/whatsapp_cloud/channels.py) — `parse_inbound` ([:971](../assets/plugin_examples/whatsapp_cloud/channels.py#L971)), `status()`/`quality_rating` ([:379](../assets/plugin_examples/whatsapp_cloud/channels.py#L379))
- [routes.py](../assets/plugin_examples/whatsapp_cloud/routes.py) — +3 rotas de alerta
- [static/whatsapp_cloud.js](../assets/plugin_examples/whatsapp_cloud/static/whatsapp_cloud.js) — seção "Alertas via Telegram"
- [plugin.yaml](../assets/plugin_examples/whatsapp_cloud/plugin.yaml) — `entry.events`, `entry.lifecycle`, `migrations`, `version`, `permissions`
- `alerts.py`, `events.py`, `lifecycle.py`, `migrations/001_alerts.sql` — **novos**

**Plugin `gowa` (molde, não muda):**
- [alerts.py](../assets/plugin_examples/gowa/alerts.py) · [routes.py:74-141](../assets/plugin_examples/gowa/routes.py#L74) · [static/gowa.js:43](../assets/plugin_examples/gowa/static/gowa.js#L43) · [lifecycle.py](../assets/plugin_examples/gowa/lifecycle.py) · [migrations/001_disconnect_alerts.sql](../assets/plugin_examples/gowa/migrations/001_disconnect_alerts.sql)

**Distribuição:**
- [assets/channel_plugins/whatsapp_cloud-plugin.zip](../assets/channel_plugins/)
