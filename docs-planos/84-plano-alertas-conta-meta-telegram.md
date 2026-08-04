# Plano 84 — Alertas da conta Meta (template pausado, qualidade, limite) num grupo do Telegram, configurados no próprio plugin `whatsapp_cloud`

> **Status:** IMPLEMENTADO NA 1.10.2 COM SEAM MÍNIMO SEGURO NO CORE (F0·F2–F8; F1 depende de operação na Meta) · **Data:** 2026-07-27 · **Execução/revisão:** 2026-07-31 · **Escopo:** médio — motor, tela e estado no plugin; core apenas resolve procedência, autenticação atômica e o snapshot de reentrega antes do fan-out
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
| **P1** (princípio) | Padrão do repo: **o provider declara, o core só avalia**. | Não nasceu `kind="account"`: o plugin observa o payload cru. O core ganhou apenas seams genéricos de procedência/autenticação e o snapshot `is_redelivery`; a exceção GOWA já existente só foi movida para antes da validação da URL compartilhada. |
| **P2** (princípio) | **Plugin autossuficiente**: um plugin não importa código de outro (D2·F9 do plano 76 — o `facebook_messenger` carrega a própria cópia de `meta_graph`). | O motor de Telegram é **copiado** de `gowa/alerts.py`, não importado. Duas cópias é o preço do zip autossuficiente. |

---

## 2. Resumo executivo

Hoje o WhatsBot é **cego** para tudo que a Meta diz sobre a *conta* — só escuta o campo `messages`. Se um template for pausado por baixa qualidade, se o número cair de qualidade ou se o tier de mensagens for cortado, o operador só descobre quando um envio começa a falhar. A causa é dupla e ambas as pontas estão medidas: **(a)** o `parse_inbound` do plugin percorre exclusivamente `value.messages[]` e `value.statuses[]` ([channels.py:1007-1017](../assets/plugin_examples/whatsapp_cloud/channels.py#L1007)) e **ignora o `change["field"]`** — um `change` de `account_update` produz **zero** `InboundEvent` e some sem log; **(b)** o `_dispatch_events` do core roteia por `kind` e **não tem `else`** ([channel_webhook.py:277-611](../server/routes/channel_webhook.py#L277)), então mesmo um evento novo seria descartado em silêncio.

A solução executada tem três camadas de produto no plugin e uma costura mínima genérica no core:

1. **Captura autenticada** — o observador `filter.webhook.payload` do próprio plugin extrai todo `change` cujo `field` **não** seja `messages` e despacha o alerta fora do request. Antes dele, o core resolve canal/provider, executa `verify_inbound_signature_result() -> (accepted, authenticated)` em worker e entrega `{provider, channel_id, signature_authenticated}` em `ctx.extras`. O plugin exige Cloud + canal ativo + HMAC confirmado + WABA ID exato; não há fallback para canal único nem alerta sem etiqueta. Não cria `InboundEvent`, contato, conversa ou mensagem.
2. **Detecção sem depender da Meta** — um loop de polling lê o `quality_rating` que o `status()` **já busca e joga fora** ([channels.py:379](../assets/plugin_examples/whatsapp_cloud/channels.py#L379)) e alerta na variação. Isto funciona **antes** de qualquer configuração no App Dashboard e é o cinto de segurança do plano (ver R3).
3. **Notificação** — `alerts.py` recebe diretamente os avisos extraídos e `events.py` assina `message.failed` (bus que **já existe**, [channel_webhook.py:404](../server/routes/channel_webhook.py#L404)); ambos mandam ao grupo do Telegram com **agregação e cooldown** (sem isso as 15 falhas medidas em 3h viram 15 mensagens no grupo). Token/chat_id/intervalo/fuso e os liga-desliga por grupo de alerta vivem na aba **Configurar** do plugin.

⚠️ **Pré-requisito operacional que nenhum código resolve:** os campos precisam estar **assinados no App Dashboard da Meta**. Sem isso a camada (1) nunca recebe nada — por isso a camada (2) existe e por isso a F1 é uma fase de **operação + documentação na tela**, não de código.

---

## 3. Como funciona hoje (mapa)

### 3.1 O caminho de um webhook da Meta

| # | Etapa | Arquivo:linha | O que acontece com `field: "messages"` | O que acontece com `field: "account_update"` |
|---|---|---|---|---|
| 1 | Body cru + rota + assinatura | [channel_webhook.py](../server/routes/channel_webhook.py) | lê os bytes, resolve canal/provider e executa o veredito atômico | idem; Cloud só marca autenticado quando o HMAC do mesmo snapshot confere |
| 2 | Filtro de plugin | [channel_webhook.py](../server/routes/channel_webhook.py) | `apply_filter("filter.webhook.payload", raw, extras)` | recebe provider/canal/autenticação resolvidos; o plugin só causa efeito com WABA exata |
| 3 | Buffers de debug | [channel_webhook.py:676-685](../server/routes/channel_webhook.py#L676) | grava em `_RECENT` | idem |
| 4 | `parse_inbound` | [channels.py:971](../assets/plugin_examples/whatsapp_cloud/channels.py#L971) | 1 evento por `messages[]` + 1 por `statuses[]` | **0 eventos** — o `for` só olha `messages`/`statuses` ([:1007](../assets/plugin_examples/whatsapp_cloud/channels.py#L1007) e [:1016](../assets/plugin_examples/whatsapp_cloud/channels.py#L1016)) |
| 5 | Dispatch | [channel_webhook.py:277](../server/routes/channel_webhook.py#L277) | roteia por `kind` | lista vazia ⇒ `handled=0`, log `→ 0 evento(s) parseado(s)` e **fim** |

⚠️ **Gotcha decisivo:** `change["field"]` é lido **em lugar nenhum** do plugin (`grep field` em `channels.py` só acha `credential_fields`/`config_fields`/`"fields"` de query da Graph). O discriminador que a Meta usa para separar "mensagem" de "aviso da conta" é justamente esse — e ele é descartado na entrada.

### 3.2 Por que não nasceu um `kind="account"`

O aviso da conta não é mensagem e não precisa entrar em `_dispatch_events`: o filtro cru é o único ponto que enxerga `change["field"]` antes de o parser reduzi-lo a zero eventos. A revisão manteve toda classificação/entrega no plugin e acrescentou no core apenas a procedência/autenticação necessária para o filtro causar efeito externo sem adivinhar a origem.

### 3.3 O que já existe a favor (nada disto precisa ser inventado)

| Peça pronta | Onde | Como será reusada |
|---|---|---|
| Motor de alerta Telegram **em produção** | [gowa/alerts.py](../assets/plugin_examples/gowa/alerts.py) — `_tg_call`([:215](../assets/plugin_examples/gowa/alerts.py#L215)), `_tg_send`([:243](../assets/plugin_examples/gowa/alerts.py#L243)), `_tg_edit`([:255](../assets/plugin_examples/gowa/alerts.py#L255)), `_tg_delete`([:271](../assets/plugin_examples/gowa/alerts.py#L271)), `_tick`([:351](../assets/plugin_examples/gowa/alerts.py#L351)), `disconnect_alert_loop`([:449](../assets/plugin_examples/gowa/alerts.py#L449)) | **Copiado** (P2) para `whatsapp_cloud/alerts.py`. Inclui de graça: HTML, `disable_web_page_preview`, retry transparente quando o grupo vira supergrupo (`migrate_to_chat_id`, [:224-236](../assets/plugin_examples/gowa/alerts.py#L224)), fuso configurável e estado que sobrevive a restart |
| Rotas de config do alerta | [gowa/routes.py:74](../assets/plugin_examples/gowa/routes.py#L74) (GET), [:110](../assets/plugin_examples/gowa/routes.py#L110) (PUT), [:141](../assets/plugin_examples/gowa/routes.py#L141) (POST `/alert-test`) | Molde das 3 rotas novas; já gateadas por `core_permission("channel.manage")` (plano 81) |
| UI da seção de alerta | [gowa/static/gowa.js:43](../assets/plugin_examples/gowa/static/gowa.js#L43) (`DisconnectAlerts`) + `browserTimezone()`([:37](../assets/plugin_examples/gowa/static/gowa.js#L37)) | Molde da seção nova em [whatsapp_cloud.js](../assets/plugin_examples/whatsapp_cloud/static/whatsapp_cloud.js) (que hoje tem 213 linhas e é só ajuda/documentação) |
| Evento de bus de falha de envio | [channel_webhook.py](../server/routes/channel_webhook.py) — `message.failed` com `error_code`, `error_title`, `is_new`, `is_redelivery` | Assinado direto em `events.py`; **`is_redelivery`** é o snapshot de dedupe. `is_new=False` também cobre status antes da row e não pode ser descartado |
| Catálogo de códigos → PT-BR | [server/message_errors.py:30-48](../server/message_errors.py#L30) — inclui `132015` (pausado) e `132016` (desabilitado) | Reusado no texto do alerta via `from server.message_errors import describe_failure` (é core, import legítimo) |
| `quality_rating` já buscado | [channels.py:379](../assets/plugin_examples/whatsapp_cloud/channels.py#L379), dentro de `status()`([:351](../assets/plugin_examples/whatsapp_cloud/channels.py#L351)) | Vira a fonte do alerta de qualidade por polling (F4) — **hoje é lido e descartado** |
| Screen `config: true` | [plugin.yaml:22-27](../assets/plugin_examples/whatsapp_cloud/plugin.yaml#L22) | Já registrada; só ganha conteúdo |
| Supervisão de task de plugin | `ctx.spawn_task` ([plugins/context.py:350](../plugins/context.py#L350)); uso real em [gowa/lifecycle.py](../assets/plugin_examples/gowa/lifecycle.py) | O plugin **ainda não tem** `lifecycle` no `entry` — a F4 adiciona |

### 3.4 Estado das credenciais e da assinatura (relevante para a F1)

| Fato | Verificação | Consequência |
|---|---|---|
| Credenciais incluem `app_secret` | [channels.py](../assets/plugin_examples/whatsapp_cloud/channels.py) | obrigatório pelo descriptor na criação nova; fora do conjunto de saúde operacional para legados, que continuam loadable/fail-open sem falso aviso de desconexão, mas precisam configurar o segredo para receber avisos de conta |
| Veredito atômico de assinatura | `verify_inbound_signature_result(raw, headers) -> (accepted, authenticated)` | Cloud lê o segredo uma vez, rejeita assinatura ausente/inválida quando configurado e nunca marca o caminho compatível sem segredo como autenticado |
| `POST /{waba_id}/subscribed_apps` já é chamado pelo plugin | [routes.py:270](../assets/plugin_examples/whatsapp_cloud/routes.py#L270) | Ele assina **o app na WABA** e seta `override_callback_uri` — **não escolhe os campos**. Ver F.P.#3 |

---

## 4. Inventário / análise

| # | Item | Arquivo:linha | O que falta | Abordagem | Risco | Esf. |
|---|---|---|---|---|---|---|
| 1 | Assinar os campos na Meta | App Dashboard (fora do repo) | `message_template_status_update`, `account_update`, etc. não assinados | operação manual + checklist na tela do plugin (F1); programático fica em **P2** | médio | S |
| 2 | Capturar `field != "messages"` | [filters.py](../assets/plugin_examples/whatsapp_cloud/filters.py) | o parser o reduz a zero eventos | observador passa-tudo, offload fora do request | baixo | S |
| 3 | Procedência segura no core | [channel_webhook.py](../server/routes/channel_webhook.py) + [base.py](../channels/base.py) | filtro antes recebia `{}` e não distinguia fail-open de HMAC | resolver rota/provider antes do filtro + veredito atômico `(accepted, authenticated)` | médio | M |
| 4 | Motor de alerta no plugin | `whatsapp_cloud/alerts.py` (novo) | não existe | **port** de [gowa/alerts.py](../assets/plugin_examples/gowa/alerts.py) + agregação/cooldown | médio | L |
| 5 | Assinatura dos eventos | `whatsapp_cloud/events.py` (novo) | plugin não tem `entry.events` | `EVENT_HANDLERS = {"message.failed": …}`; avisos de conta vêm do filtro cru | baixo | S |
| 6 | Loop de qualidade (polling) | [channels.py:379](../assets/plugin_examples/whatsapp_cloud/channels.py#L379) | valor lido e descartado | `lifecycle.py` novo + `ctx.spawn_task`; compara com o último valor gravado | baixo | M |
| 7 | Estado / dedupe | migration nova `plugin_whatsapp_cloud_*` | não existe | 1 tabela de estado por chave de alerta (molde: [gowa/migrations/001](../assets/plugin_examples/gowa/migrations/001_disconnect_alerts.sql)) | baixo | S |
| 8 | Rotas de config | [whatsapp_cloud/routes.py:190](../assets/plugin_examples/whatsapp_cloud/routes.py#L190) | só `/info`, `/webhook-status`, `/set-webhook`, `/delete-webhook` | +3 rotas espelhando o gowa; **token mascarado no GET** | baixo | M |
| 9 | UI da config | [whatsapp_cloud.js](../assets/plugin_examples/whatsapp_cloud/static/whatsapp_cloud.js) (213 linhas) | seção não existe | seção "Alertas via Telegram" espelhando `DisconnectAlerts` | baixo | M |
| 10 | Testes | [tests/test_plano84_account_alerts.py](../tests/test_plano84_account_alerts.py) | nenhum cobria `field != messages` | extrator/filtro + webhook real assinado + origens inválidas + retries/cooldown/HTML | médio | M |
| 11 | Distribuição | [assets/channel_plugins/whatsapp_cloud-plugin.zip](../assets/channel_plugins/) | — | bump 1.10.2 + core primeiro + zip/repo de plugins depois | médio | S |

### Falsos positivos descartados

| # | Hipótese | Por que NÃO é o caminho |
|---|---|---|
| **1** | "Mandar o alerta pelo **canal Telegram** do sistema (plugin `telegram`)." | ❌ Três problemas: (a) criaria **contato + conversa** no painel — o alerta viraria atendimento; (b) acopla dois plugins, violando P2; (c) exigiria o plugin `telegram` instalado e um canal configurado. O precedente `gowa/alerts.py` **já decidiu isto** e documenta no cabeçalho: *"100% contido neste plugin — NÃO usa a caixa de entrada/canal Telegram do sistema"*. |
| **2** | "Colocar a config numa aba nova em Configurações." | ❌ Proibido pelo [CLAUDE.md](../CLAUDE.md) ("Nunca adicione uma seção/aba nova ao painel de Configurações padrão para algo que pertence a um plugin") e contra **D1**. |
| **3** | "Basta chamar `POST /{waba_id}/subscribed_apps` que o plugin já tem ([routes.py](../assets/plugin_examples/whatsapp_cloud/routes.py))." | ❌ Esse endpoint assina **o app na WABA** e define o callback; ele **não seleciona os campos** (`messages`, `account_update`, …). A 1.10.2 tem App Secret para HMAC, mas a seleção app-wide continua operação manual no App Dashboard (automatizá-la mexeria em todas as WABAs do app). |
| **4** | "`message_errors.py` já traduz 132015/132016, então o operador já é avisado." | ❌ Aquilo só dispara **quando você tenta enviar e falha** — é reativo e aparece só no fio de uma conversa. O plano é sobre saber **antes** (push da Meta) e **fora** do painel (grupo do Telegram). |
| **5** | "Reusar o `kind="system"` do plano 82." | ❌ Aquele ramo anexa um card à **conversa de um contato** ([channel_webhook.py:545-607](../server/routes/channel_webhook.py#L545)). Um `account_update` é da **WABA** — não tem `chat_id`, não tem contato, não tem conversa. Reusar geraria card órfão ou nada. Kind próprio. |
| **6** | "Usar `filter.webhook.payload` e resolver tudo dentro do plugin, sem tocar no core." | ⚠️ A captura pelo filtro virou o plano A, mas **não** a versão insegura de `ctx={}`: o core resolve provider/canal e autenticação antes do filtro. Sem o seam mínimo, o plugin degrada fechado e não envia aviso da conta. |
| **7** | "Incluir alerta de saldo do LLM." | ⚠️ **Fora de escopo**: o saldo é do proxy **Techify**, não da Meta, e **já existe** (`GET /api/balance` + WS `low_balance` + `LowBalanceModal.js`). O "limite" que entra aqui é o **messaging limit tier da Meta**. Ver **P4**. |
| **8** | "Alertar todo `message.failed`." | ❌ Vira spam: **15 falhas `131047` medidas em 2h47**, todas do mesmo fluxo. O alerta de falha nasce **agregado por código, com janela e cooldown** (F5), e o `131047` entra **desligado por padrão** (é erro de operação, não de conta). |

---

## 5. Mudanças de infraestrutura (por camada)

**Contrato (`channels/`):** `verify_inbound_signature_result(raw_body, headers) -> (accepted, authenticated)`. O default delega ao booleano legado e devolve `authenticated=False`; Cloud sobrescreve e deriva os dois valores de um único snapshot do App Secret, fechando TOCTOU.

**Core (`server/routes/channel_webhook.py`):** resolve rota/canal/provider antes do filtro, preservando o reroute GOWA mesmo se a antiga URL compartilhada sumiu; executa o veredito em `asyncio.to_thread`; chama `filter.webhook.payload` com `{provider, channel_id, signature_authenticated}`. Para `message.failed`, publica também `is_redelivery`, calculado antes do fan-out, e repete o UPDATE condicional quando o writer insere a row entre `mark` e `exists`.

**Provider (`assets/plugin_examples/whatsapp_cloud/channels.py`):** App Secret entra nas credenciais obrigatórias de canais novos. Legados sem segredo continuam aceitos com WARNING uma vez por instância, mas o veredito é `(True, False)` e o filtro de alertas degrada fechado.

**Plugin (`whatsapp_cloud/`) — arquivos novos:**

| Arquivo | Papel |
|---|---|
| `alerts.py` | motor: formatação PT-BR, agregação/cooldown, Bot API do Telegram, loop de qualidade |
| `events.py` | `EVENT_HANDLERS = {"message.failed": …}` |
| `lifecycle.py` | `setup(ctx)` → `ctx.spawn_task("quality_poll", …)` (`RestartPolicy.PERMANENT`) |
| `migrations/002_alert_state.sql` | tabela `plugin_whatsapp_cloud_alert_state` (prefixo obrigatório) |
| `plugin.yaml` | `entry.events`, `entry.filters`, `entry.lifecycle`, `migrations: migrations`, `version` bump, `permissions: +runtime.task` |

**DB:** 1 migration **de plugin** (não Alembic). Tabela `plugin_whatsapp_cloud_alert_state (alert_key TEXT PRIMARY KEY, last_value TEXT, last_alert_ts DOUBLE PRECISION, occurrences INTEGER, telegram_message_id BIGINT, telegram_chat_id TEXT)`. ⚠️ Comentários SQL **sem `;`** (o migrator splita por `;` antes de tirar comentários — ver [gowa/migrations/001](../assets/plugin_examples/gowa/migrations/001_disconnect_alerts.sql) e a nota lá dentro).

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
WAVE 1   F1 (assinar campos na Meta +     F2 (plugin: filtro cru)        🟢
             doc na tela)  🟢              F3 (core: procedência/HMAC)   🟢
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
| 1 | **F2** | Plugin/filtro | 🟢 `[bloqueia: F4]` | baixo | payload assinado de `account_update` é extraído sem criar `InboundEvent` |
| 1 | **F3** | Core | 🟢 `[bloqueia: F4]` | médio | filtro recebe rota/procedência/HMAC atômicos e origem inválida não o alcança |
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
**Estado:** ✅ Concluída (2026-07-31)
- **O que foi feito:** paridade verificada entre `assets/plugin_examples/whatsapp_cloud/`, `storages/plugins/whatsapp_cloud/` e `assets/channel_plugins/whatsapp_cloud-plugin.zip` (conteúdo, não versão); baseline da suíte registrado.
- **Como foi feito / decisões:** `diff -rq` das três cópias (o zip extraído num scratchpad) — **byte-idênticas**, única diferença sendo uma pasta `tests/` VAZIA que só existe em `storages/` (artefato do discovery de testes de plugin, não é código). ⚠️ O plano dizia `1.6.0`; as três cópias já estavam em **1.9.0** (o plugin andou nos planos 92/95) — o bump da F8 partiu daí.
- **Problemas / pendências:** nenhuma no escopo. Fora dele, o checkout tem WIP de terceiros não commitado (plano 96) e `tests/test_endpoints.py` **não coleta** (`_build_list_where() got an unexpected keyword argument 'vinculo'`) porque a cópia instalada do plugin `protocolos` é anterior à 1.24.0 — pré-existente, sem relação com este plano.
- **Verificação:** `pytest tests/test_plano75_parse_inbound.py tests/test_plano82_system_inbound.py tests/test_whatsapp_cloud_ignore_empty.py -q` → **86 passed**. Baseline do §F0·3 confirmado: um payload de `account_update` produzia **0 eventos**.
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
**Estado:** ⚠️ Parcial — a parte de CÓDIGO/documentação foi feita; a assinatura na Meta é **operação do usuário**
- **O que foi feito:** o checklist dos campos a assinar foi escrito na tela do plugin (bloco de ajuda no fim da seção "Alertas da conta Meta", em [static/whatsapp_cloud.js](../assets/plugin_examples/whatsapp_cloud/static/whatsapp_cloud.js)), com o caminho exato no painel da Meta (App → WhatsApp → Configuração → Webhooks → Gerenciar) e o que cada campo entrega.
- **Como foi feito / decisões:** os nomes usados são `message_template_status_update`, `message_template_quality_update`, `template_category_update`, `phone_number_quality_update`, `business_capability_update`, `account_update` e `account_review_update`. Como §6 era hipótese e não há acesso ao App Dashboard nesta rodada, o classificador foi escrito **genérico por construção** (R4): campo desconhecido cai no grupo `unknown` com o JSON resumido em vez de ser ignorado — um nome errado degrada para "alerta bruto", nunca para silêncio.
- **Problemas / pendências:** **assinar os campos no App Dashboard continua pendente** (só o dono da conta Meta pode) e, portanto, o critério de pronto do plano (ver um `field != "messages"` real em `/api/channel-webhook-payloads`) **não foi observado em produção**. Enquanto isso, o polling de qualidade (F4) e o alerta de falhas (F5) já funcionam sem assinatura nenhuma — que é exatamente o porquê de eles existirem (R3).
- **Verificação:** payload real **não** observado (bloqueado no acesso à Meta). O caminho foi validado ponta a ponta com envelope sintético no teste de dispatch da F7.
---

### F2 — Plugin: captura autenticada de `field != "messages"` 🟢

**Objetivo:** parar de descartar o que a Meta manda.

**Itens** `[sequencial]`:
1. `filters.py` extrai `change["field"] != "messages"` sem transformar o payload.
2. Exigir simultaneamente `provider=whatsapp_cloud`, canal ativo exato, `signature_authenticated=True` e `entry[].id == waba_id` daquele canal.
3. Offload de banco/rede fora do request; sem contato/conversa/`InboundEvent`.

**Pronto quando:** um POST assinado no webhook real alerta e produz zero eventos; provider/canal/assinatura/WABA errados produzem zero efeito; `messages` continua byte-idêntico no filtro.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-07-31) — filtro no plugin + procedência mínima no core
- **O que foi feito:** a captura mudou de lugar. Em vez de o `parse_inbound` emitir `kind="account"`, quem extrai o aviso é o observador novo [filters.py](../assets/plugin_examples/whatsapp_cloud/filters.py) em `filter.webhook.payload`, com o extrator PURO `account_changes(raw)`. O `parse_inbound` só ganhou um `continue` para pular o `change` de conta (que não tem `messages`/`statuses`).
- **Como foi feito / decisões:** o observador tem prioridade 9000, **sempre** devolve o valor intacto e offloada banco/rede. `_authenticated_channel` é fail-closed: usa a procedência resolvida de `ctx.extras`, exige HMAC verdadeiro e casa a WABA com a credencial do canal exato. **Não existe fallback de canal único nem alerta sem etiqueta.**
- **Problemas / pendências:** em core antigo, `ctx.extras` não traz a procedência e a fonte webhook degrada fechada; polling e `message.failed` continuam funcionando.
- **Verificação:** `test_account_field_is_extracted_from_the_raw_payload`, `test_unknown_field_is_not_swallowed`, `test_hot_path_payloads_extract_nothing` (GOWA/inbound normal/lixo ⇒ `[]`), `test_observer_always_returns_the_payload_untouched`, `test_account_change_produces_no_inbound_event` e `test_messages_field_is_untouched_by_the_provider`.
---

### F3 — Core: seam mínimo de procedência/autenticação 🟢

**Objetivo:** permitir que um filtro com efeito externo conheça a rota real e distinga HMAC de aceitação compatível.

**Itens** `[sequencial]`:
1. Resolver provider/canal/instância antes do filtro; no GOWA, resolver o device antes de exigir a antiga URL compartilhada.
2. `verify_inbound_signature_result() -> (accepted, authenticated)` atômico, executado em worker.
3. Passar `{provider, channel_id, signature_authenticated}` ao filtro; rejeitar rota desconhecida, mismatch, instância inativa e assinatura inválida antes de qualquer plugin/debug.

**Pronto quando:** uma rota pública errada não alcança o filtro; HMAC fail-open nunca vira autenticado; GOWA continua roteando se a URL `default` estiver ausente/inativa.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-07-31) — sem `kind="account"`, mas com seam mínimo seguro
- **O que foi feito:** [channels/base.py](../channels/base.py) ganhou o veredito atômico; [channel_webhook.py](../server/routes/channel_webhook.py) resolve e valida a procedência antes do filtro e publica o snapshot `is_redelivery` no bus de falhas.
- **Como foi feito / decisões:** o motor continua no plugin. O core não classifica conta Meta e não cria evento/contato/conversa; só fornece fatos genéricos que ele é o único capaz de afirmar sem TOCTOU.
- **Problemas / pendências:** o aviso da conta continua corretamente logado como zero `InboundEvent`; o efeito ocorre no observador cru.
- **Verificação:** testes de provider errado, canal desconhecido/inativo, assinatura ausente/erro de credencial, WABA errada, segredo mutável e callback GOWA ausente/inativo.
---

### F4 — Motor de alerta no plugin (Telegram + polling de qualidade) 🔴

**Objetivo:** transformar evento em mensagem no grupo, sem spam e sobrevivendo a restart.

**Itens:**
1. `[sequencial]` `alerts.py`: **port** de [gowa/alerts.py](../assets/plugin_examples/gowa/alerts.py) — `_tg_call`/`_tg_send`/`_tg_edit`/`_tg_delete`, config lida **a cada ciclo** (editar não exige restart), fuso, e o retry de `migrate_to_chat_id`. Prefixo de config `plugin.whatsapp_cloud.alert_*`.
2. `[sequencial]` Migration `002_alert_state.sql` com a tabela de estado (§5). ⚠️ sem `;` em comentário.
3. `[paralelo]` Formatação PT-BR por grupo de alerta (§6), com **função pura** `format_alert(kind, payload) -> str` (testável sem rede).
4. `[paralelo]` **Agregação/cooldown**: função pura `should_alert(state, key, now, cooldown) -> bool` + contador. Regra: 1ª ocorrência alerta na hora; repetições dentro da janela **incrementam contador** e re-editam a mensagem existente (`editMessageText`) em vez de mandar outra.
5. `[sequencial]` `lifecycle.py` + `entry.lifecycle` no manifest: `ctx.spawn_task("quality_poll", …, policy=RestartPolicy.PERMANENT)` lendo `status()["quality_rating"]` ([channels.py:379](../assets/plugin_examples/whatsapp_cloud/channels.py#L379)) e alertando **na variação** do valor gravado. Cadência sugerida: 5–15 min (≫ que os 30 s do gowa — qualidade muda devagar e cada tick é uma chamada Graph).
6. `[sequencial]` `events.py` com `EVENT_HANDLERS = {"message.failed": …}` + `entry.events` no manifest; avisos de conta chegam direto do filtro autenticado.

**Pronto quando:** com o alerta ligado, um aviso de conta autenticado chega ao Telegram; derrubar/religar o servidor não perde o estado confirmado.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-07-31)
- **O que foi feito:** [alerts.py](../assets/plugin_examples/whatsapp_cloud/alerts.py) (motor completo), [events.py](../assets/plugin_examples/whatsapp_cloud/events.py), [lifecycle.py](../assets/plugin_examples/whatsapp_cloud/lifecycle.py), [migrations/002_alert_state.sql](../assets/plugin_examples/whatsapp_cloud/migrations/002_alert_state.sql) e o manifesto (`entry.events`, `entry.lifecycle`, `permissions: +runtime.task`).
- **Como foi feito / decisões:** **copiado** do `gowa` (P2): `_tg_call`/`_tg_send`/`_tg_edit` com retry de `migrate_to_chat_id`, config por evento e fuso. **Adaptado**: (a) repetição edita o contador e só o vencimento manda outra; (b) classificação/formatação/agregação são puras; (c) `_LOCK` serializa o estado; (d) polling em minutos; (e) canal sempre no texto; (f) primeira leitura de qualidade é baseline; (g) `last_value`/cooldown e `quality_seen` só avançam após `message_id`/edição confirmados; (h) toda parte dinâmica é escapada para `parse_mode=HTML`, preservando apenas as tags internas `<b>`/`<code>`; (i) o dispatch one-shot do webhook faz duas retentativas curtas e limitadas quando o transporte retorna `failed` (best-effort, sem outbox durável e sem prender o request).
- **Problemas / pendências:** o `alert_state` guarda `occurrences` (não `count`, que é palavra reservada demais em SQL para gosto próprio). Entrega ponta a ponta ao Telegram REAL não foi exercida (sem token/grupo nesta rodada) — validada com o transporte mockado; o "Enviar teste" da tela cobre isso em produção.
- **Verificação:** `test_account_event_reaches_telegram`, `test_describe_*`, `test_should_alert_aggregates_within_window`, `test_format_alert_carries_channel_and_count`, `test_group_enabled_defaults`.
---

### F5 — Alerta de falha de envio (bus `message.failed`) 🟢

**Objetivo:** avisar no grupo quando as falhas passam de ruído a padrão — sem virar spam.

**Itens:**
1. `[sequencial]` Assinar `message.failed` em `events.py`; o payload traz `error_code`/`error_title`/`is_new` e, na 1.10.2, `is_redelivery`.
2. `[sequencial]` Guard de dedupe: descartar somente `is_redelivery=True`. `is_new=False` também representa o primeiro status que chegou antes da row e não pode ser perdido.
3. `[paralelo]` Filtrar por código conforme §6 (`131047` **OFF** por padrão).
4. `[paralelo]` Agregar por `(error_code, janela)` reusando `should_alert` da F4; o texto usa `describe_failure` de [server/message_errors.py:69](../server/message_errors.py#L69) — **não** reescrever as frases.

**Pronto quando:** 10 eventos `message.failed` do mesmo código em sequência produzem **1** mensagem no grupo (com contagem), e 1 evento de código desligado produz **0**.

#### Status de execução — Fase 5
**Estado:** ✅ Concluída (2026-07-31)
- **O que foi feito:** `message.failed` assinado em `events.py` → `alerts.on_message_failed`, com guard de reentrega, filtro por código e agregação compartilhada com a F4.
- **Como foi feito / decisões:** o core snapshota `is_redelivery` antes do fan-out fire-and-forget e tenta um segundo `mark_failed_by_msg_id` se o writer inserir entre o primeiro UPDATE e o `exists`. O plugin descarta apenas reentrega confirmada e exige `channel_id` existente do provider Cloud; vazio, desconhecido e outro provider degradam fechados. O texto reusa `describe_failure`; `131047` fica OFF e os códigos relevantes agregam por valor.
- **Problemas / pendências:** a entrega do bus é best-effort, sem outbox durável: se o Telegram falhar nesta única ocorrência e a Meta apenas reentregar o mesmo receipt, o guard de `is_redelivery` não duplica. O estado/cooldown não é consumido, então a próxima falha nova do mesmo código tenta novamente. Já o aviso one-shot do webhook tem duas retentativas curtas e limitadas.
- **Verificação:** `test_burst_of_same_failure_becomes_one_message` (10 falhas iguais ⇒ **1** `sendMessage` + 9 `editMessageText`, com "Ocorrências: 10"), `test_redelivered_failure_is_ignored`, `test_disabled_group_sends_nothing`, `test_other_provider_event_is_ignored`.
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
**Estado:** ✅ Concluída (2026-07-31)
- **O que foi feito:** `GET`/`PUT /alert-settings` e `POST /alert-test` em [routes.py](../assets/plugin_examples/whatsapp_cloud/routes.py); seção "Alertas da conta Meta (Telegram)" + bloco de ajuda da F1 em [static/whatsapp_cloud.js](../assets/plugin_examples/whatsapp_cloud/static/whatsapp_cloud.js).
- **Como foi feito / decisões:** as três rotas espelham o `gowa` e são gateadas por `core_permission("channel.manage")` (plano 81). Token **mascarado** no GET (`bot_token_set` + `bot_token_hint` = últimos 4) e gravado no PUT só quando vem valor real. O **catálogo de grupos vem do motor** (`ALERT_GROUPS` via `_alert_groups_view()`), então acrescentar um grupo de alerta não exige tocar no JS. A auditoria usa um `_audit_plugin` novo: esta config é da instalação inteira, então cai em `plugin:whatsapp_cloud` — as ações de webhook/template continuam em `channel:<id>`. Modo escuro: só `wa-*`/`.wa-field`, e o seletor de fuso é o `SearchableSelect` do core.
- **Problemas / pendências:** a tela não foi aberta no navegador nesta rodada (sem servidor de dev subido) — validada por sintaxe (`node --check` em modo módulo) e pelos testes de rota.
- **Verificação:** `test_alert_settings_never_leaks_the_token` (o segredo não aparece em lugar nenhum do corpo; o catálogo de grupos vem do servidor) e `test_put_without_token_keeps_the_saved_one`.
---

### F7 — Testes 🔴

**Objetivo:** travar o comportamento novo sem tocar a rede.

**Itens** `[paralelo entre si]`:
1. **Captura**: extrator cru encontra `account_update`; `messages` fica **inalterado**.
2. **Webhook real**: POST HMAC válido alerta sem criar contato/conversa; origem/provider/canal/WABA/assinatura inválidos não causam efeito.
3. **Puros**: `format_alert` e `should_alert` (agregação/cooldown/contador) — sem rede, sem DB.
4. **Falhas**: `is_redelivery=True` não alerta; corrida sem row alerta; 10× mesmo código = 1 mensagem; falha Telegram não consome cursor/cooldown.
5. **Rotas**: GET não devolve o token; PUT vazio não apaga o token salvo.

**Pronto quando:** suíte verde no Postgres (`WHATSBOT_TEST_DB_URL`) e nenhum teste existente do plano 75 quebrado.

#### Status de execução — Fase 7
**Estado:** ✅ Concluída (2026-07-31)
- **O que foi feito:** [tests/test_plano84_account_alerts.py](../tests/test_plano84_account_alerts.py) cobre captura/HMAC/procedência, webhook real, GOWA, puros, HTML, falhas/corridas, polling, rotas e credenciais obrigatórias; regressões adicionais vivem nos planos 75/82 e multicanal.
- **Como foi feito / decisões:** os módulos do plugin são carregados POR CAMINHO com o mesmo truque do `plugins/loader.py` (pacote sintético em `sys.modules`), como no molde do plano 75. O Telegram é mockado por `monkeypatch` em `_tg_send`/`_tg_edit` — nenhum teste toca a rede. Descoberta durante a fase: as rotas gateadas por RBAC quebravam **por ordem de execução** (o plano 48 fecha a API assim que existe ≥1 usuário, e o banco de teste é compartilhado); a fixture `admin_client` autentica **e apaga o usuário no teardown**, para não fechar a API para os testes seguintes que não autenticam.
- **Problemas / pendências:** `tests/test_endpoints.py` não coleta (mismatch do plugin `protocolos` instalado) e `tests/endpoints/` tem falhas por poluição entre arquivos — **ambas pré-existentes** (reproduzidas sem este plano no working tree). `tests/test_plugin_events.py` falha inteiro com "async def functions are not natively supported" — também pré-existente (ambiente sem o plugin asyncio do pytest).
- **Verificação:** 42 testes do plano 84 verdes no Postgres isolado `whatsbot_test_p84`; regressões dos planos 75/82, MetaGraph, multicanal e caracterização de webhook também executadas na revisão 1.10.2.
---

### F8 — Distribuição e deploy 🔴

**Objetivo:** empacotar e colocar o plugin em produção.

**Itens** `[sequencial]`:
1. Bump de `version` no `plugin.yaml`.
2. Sincronizar `assets/plugin_examples/whatsapp_cloud/` → `storages/plugins/whatsapp_cloud/` e regenerar `assets/channel_plugins/whatsapp_cloud-plugin.zip`.
3. Publicar o core primeiro; depois o `.zip` 1.10.2 e metadados no `whatsbot-pro-plugins`.
4. Importar o zip em produção (tela Plugins → Importar `.zip`) e **reiniciar** (o toggle já força restart).
5. Validar com um evento real (pausar um template de teste) e conferir a mensagem no grupo.

**Pronto quando:** o alerta real chega no grupo em produção.

#### Status de execução — Fase 8
**Estado:** ⚠️ Parcial — empacotado; **deploy em produção pendente**
- **O que foi feito:** `plugin.yaml` em **1.10.2** (1.10.0 = motor; 1.10.1 = captura; 1.10.2 = procedência/HMAC fail-closed, retries e correções de segurança); fonte sincronizada, zip recriado e repo de plugins preparado com JSON/catálogo/README 1.10.2.
- **Como foi feito / decisões:** o zip foi apagado antes de gerar — `zip -r` ACRESCENTA a um arquivo existente, o que deixaria lixo de versões antigas dentro. O `rsync --delete` excluiu `tests/` (a pasta vazia que só existe na cópia instalada) e `__pycache__`.
- **Problemas / pendências:** fazer merge/deploy do core **antes** de importar 1.10.2; depois configurar App Secret + WABA nos legados, assinar os campos na Meta e validar evento real. Em core antigo a fonte webhook degrada fechada; polling/falhas continuam.
- **Verificação:** conteúdo do zip conferido (`unzip -l`) com os 5 arquivos novos (`alerts.py`, `events.py`, `filters.py`, `lifecycle.py`, `migrations/002_alert_state.sql`); `diff -rq` entre `assets/` e `storages/` limpo. Alerta real em produção **não** observado.
---

## 9. Riscos e cuidados

| # | Ponto | Risco | Mitigação |
|---|---|---|---|
| **R1** | Volume de alertas | 15 falhas em 2h47 medidas ⇒ grupo inutilizável por ruído | Agregação + cooldown **desde a F4** (não como melhoria depois); `131047` OFF por default; contador em vez de nova mensagem |
| **R2** | Token do bot em `config` | segredo em texto na tabela `config` | Mesmo modelo já aceito no `gowa`; **mascarar no GET** (F6·2) e nunca logar. Não vai para a URL |
| **R3** | Campos não assinados na Meta | plano inteiro fica mudo e ninguém percebe | O **polling de qualidade** (F4·5) não depende de assinatura nenhuma; e a tela mostra o checklist da F1 |
| **R4** | Nomes/formatos dos campos são hipótese (§6) | tratar `value` errado ⇒ alerta vazio ou exceção | F1 confirma contra a doc **e** contra payload real; fallback **genérico** para `field` desconhecido (nunca ignorar) |
| **R5** | Assinatura/rota pública | fail-open legado ou TOCTOU poderia disparar alerta falso | Canais novos exigem App Secret; veredito atômico lê o segredo uma vez e erro de storage rejeita; filtro exige rota/provider/canal/WABA exatos; legado sem segredo recebe mensagem, mas nunca alerta conta |
| **R6** | 4 lugares onde o plugin vive | editar a cópia errada = mudança que não roda | F0 (paridade) + F8 (ordem de deploy). Regra do [CLAUDE.md](../CLAUDE.md): comparar **conteúdo**, nunca só a versão |
| **R7** | Task supervisionada nova | loop com exceção derruba/reinicia em laço | `RestartPolicy.PERMANENT` + `try/except` largo por tick (padrão do `gowa/alerts.py`); cadência de minutos, não segundos |
| **R8** | Chamada Graph por tick | consumo de rate limit da conta | Cadência ≥5 min; ler o `status()` que **já é chamado** por outros caminhos quando possível (cache) |
| **R9** | Restart de plugin | enable/disable derruba o processo ([plugins/restart.py](../plugins/restart.py)) | Comportamento esperado; a config é lida **a cada ciclo**, então editar não exige restart |
| **R10** | Migration de plugin | `;` em comentário quebra o splitter | Documentado no molde do gowa; conferir antes de subir |

---

## 10. Perguntas em aberto

**P1 — Captura via `kind` novo ou filtro cru com seam mínimo?**
✅ **DECIDIDO NA REVISÃO 1.10.2:** filtro cru no plugin, sem `kind="account"`, mas com procedência/autenticação genérica resolvida no core. O plugin não adivinha: exige `ctx.extras` + WABA exata e degrada fechado se o core for antigo.

**P2 — Assinar os campos programaticamente?**
Exige chamada app-wide e mexe na configuração de **todas** as WABAs. O App Secret já existe para HMAC, mas isso não muda o risco operacional.
**Decisão: ⏸️ ADIADO.** F1 manual + checklist na tela.

**P3 — Verificar a assinatura `X-Hub-Signature-256` no canal Cloud?**
✅ **FEITO NA 1.10.2.** Canais novos exigem App Secret. Legados sem segredo permanecem fail-open apenas para mensagens; `signature_authenticated=False` bloqueia avisos de conta. Erro de leitura do segredo rejeita o webhook.

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

- [x] `diff` das 3 cópias do plugin **antes** e **depois** (conteúdo, não versão)
- [x] Payload `messages` continua produzindo exatamente os mesmos eventos (regressão zero no hot path)
- [x] O aviso da conta **não** cria contato, **não** cria/reabre conversa, **não** grava mensagem (e-2-e pelo webhook real)
- [x] Core alterado somente nos seams genéricos documentados (veredito/rota, separação genérica create × saúde e contrato do bus), sem ramo Meta/account
- [ ] `tests/test_endpoints.py` verde — ⚠️ **não coleta** por mismatch PRÉ-EXISTENTE do plugin `protocolos` instalado (`_build_list_where(vinculo=…)`), sem relação com este plano
- [x] Suíte verde no **Postgres** (`WHATSBOT_TEST_DB_URL`, banco com `test` no nome)
- [x] Testes puros de `format_alert`/`should_alert` verdes
- [x] Migration de plugin aplica limpa (e **sem `;`** em comentário)
- [x] Restart do plugin (enable/disable) não duplica nem perde alerta — o estado (valor + contador + message_id) vive no Postgres, não em memória
- [x] `GET /alert-settings` **não** devolve o token; PUT vazio não apaga o salvo
- [x] Nenhum segredo em URL nem em log
- [x] Tela de config legível no **modo escuro** (`wa-*` / `.wa-field`) — por construção; ⚠️ não conferida no navegador nesta rodada
- [x] Rotas novas gateadas por `core_permission("channel.manage")` (plano 81)
- [x] Ordem de deploy travada: **core primeiro → plugin 1.10.2 depois**; em core antigo a fonte webhook fecha sem efeito

---

## 12. Apêndice — arquivos-chave

**Core (muda):**
- [server/routes/channel_webhook.py](../server/routes/channel_webhook.py) — rota/procedência antes do filtro, veredito em worker, reroute GOWA cedo e `is_redelivery`
- [channels/base.py](../channels/base.py) — `verify_inbound_signature_result() -> (accepted, authenticated)`
- [app/services/channel_service.py](../app/services/channel_service.py) + [server/routes/channels.py](../server/routes/channels.py) — requisitos de criação do descriptor separados da saúde operacional dos legados
- [plugins/events.py](../plugins/events.py) — contrato público de `message.failed` documenta `is_redelivery`

**Core (só leitura/reuso):**
- [server/message_errors.py](../server/message_errors.py) — `describe_failure` ([:69](../server/message_errors.py#L69))
- [plugins/context.py](../plugins/context.py) — `spawn_task` ([:350](../plugins/context.py#L350)), `core_permission`

**Plugin `whatsapp_cloud` (muda — e a cópia em `storages/plugins/`):**
- [channels.py](../assets/plugin_examples/whatsapp_cloud/channels.py) — `parse_inbound` ([:971](../assets/plugin_examples/whatsapp_cloud/channels.py#L971)), `status()`/`quality_rating` ([:379](../assets/plugin_examples/whatsapp_cloud/channels.py#L379))
- [routes.py](../assets/plugin_examples/whatsapp_cloud/routes.py) — +3 rotas de alerta
- [static/whatsapp_cloud.js](../assets/plugin_examples/whatsapp_cloud/static/whatsapp_cloud.js) — seção "Alertas via Telegram"
- [plugin.yaml](../assets/plugin_examples/whatsapp_cloud/plugin.yaml) — `entry.events`, `entry.lifecycle`, `migrations`, `version`, `permissions`
- `alerts.py`, `events.py`, `filters.py`, `lifecycle.py`, `migrations/002_alert_state.sql` — **novos**

**Plugin `gowa` (molde, não muda):**
- [alerts.py](../assets/plugin_examples/gowa/alerts.py) · [routes.py:74-141](../assets/plugin_examples/gowa/routes.py#L74) · [static/gowa.js:43](../assets/plugin_examples/gowa/static/gowa.js#L43) · [lifecycle.py](../assets/plugin_examples/gowa/lifecycle.py) · [migrations/001_disconnect_alerts.sql](../assets/plugin_examples/gowa/migrations/001_disconnect_alerts.sql)

**Distribuição:**
- [assets/channel_plugins/whatsapp_cloud-plugin.zip](../assets/channel_plugins/)
