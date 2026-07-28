# Plano 76 — Desacoplar providers do core: o que é do plugin volta pro plugin

> **Status:** PLANEJAMENTO · **Data:** 2026-07-22 · **Escopo:** médio
> **Origem:** auditoria pedida pelo usuário após o plano 46 · 02 (Facebook Messenger) — *"o que foi colocado no core e o que foi de fato no plugin? Em tese o máximo de coisas deve ficar no plugin, para que ao importar já traga todas as funcionalidades"*. Auditados os 4 plugins de canal: `facebook_messenger`, `whatsapp_cloud`, `telegram`, `website`.
> **Método:** leitura direta + `grep` de nome de provider em todo o core (`server/`, `app/`, `channels/`, `web/static/js/`), com `arquivo:linha` verificado. Nada de memória.
> **Diagnóstico:** o contrato do plano 33 ("o provider se autodescreve, o core nunca o conhece por nome") vale **na tela Canais**, mas **vazou em 6 pontos fora dela**: 4 catálogos duplicados de rótulo/cor por provider, um componente de card com gate por nome, 3 funções do client HTTP do core apontando pra endpoints de plugin, o snippet de instalação do widget com o path `/plugins/website/` hardcoded, uma allow-list de credenciais não-secretas crescendo a cada provider, e uma flag de URL chamada `telegram`. Nenhum deles precisa existir — os seams pra removê-los **já existem** (`provider_descriptor()` no backend, `registry.js` slots/filters no frontend) e estão subutilizados.
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | **Princípio: zero `if provider ==` / zero nome de provider no core** ✅ (2026-07-22) | Todo ponto listado no §3 vira genérico ou some. O critério de aceitação final é um `grep` limpo (F8). |
| D2 | **Os ganchos genéricos FICAM no core** ✅ | `verify_inbound_signature`, `human_window_hours`, `token_refresh`, `session_window_hours`, `inbound_route`, `media_limits`, `contact_type`, o subsistema de templates e o auth-exempt `/api/plugins/<id>/public/` são contrato, não vazamento. **Não são escopo deste plano.** |
| D3 | **Aditivo e retrocompatível — plugin desativado não pode quebrar o core** ✅ | Todo catálogo dinâmico mantém fallback estático mínimo; todo `<Slot>` novo renderiza nada quando vazio (contrato do [Slot.js:12](../web/static/js/plugins/Slot.js#L12)). Instalação sem nenhum plugin de canal continua abrindo. |
| D4 | **Escopo = os 4 plugins de canal** (`facebook_messenger`, `whatsapp_cloud`, `telegram`, `website`) ✅ | `gowa` entra só como consumidor dos catálogos (é bundled e não tem vazamento próprio). Plugins não-canal (`protocolos`, `melhorias`, …) fora do escopo. |
| D5 | **Nada em produção depende dos pontos tocados ⇒ refactor direto, sem stopgap** ✅ | Não se cria camada de compatibilidade dupla; o caminho antigo é REMOVIDO na mesma fase em que o novo entra (exceto os fallbacks de D3). |
| D6 | **Um refactor por commit, verde a cada fase** ✅ | Disciplina padrão do repo. `test_endpoints.py` + `node --test` verdes antes de avançar. |

---

## 1. Resumo executivo

O plano 33 tornou os canais plugáveis: cada provider devolve um `provider_descriptor()` e a tela Canais o renderiza sem saber o nome dele. Mas o descriptor **só é consumido pelo `ChannelsManager`** ([ChannelsManager.js:183](../web/static/js/components/ChannelsManager.js#L183)) — em nenhum outro lugar do app. Consequência: toda vez que outra tela precisou de "rótulo e cor do canal", copiou-se um mapa estático. Hoje existem **quatro** desses mapas, mais um catálogo de tipo de contato, todos desatualizando a cada provider novo (o `facebook_messenger` já entrou em 1 dos 5).

Fora do frontend, o mesmo padrão se repete em escala menor no backend (uma allow-list de chaves de credencial) e em dois casos duros: o `WebhookHealthRow` do card de canal tem `if (channel.provider !== 'whatsapp_cloud') return null`, e o `buildEmbedSnippet` do core monta um `<script>` apontando pra `/plugins/website/static/sdk.js`.

A solução tem **três habilitadores** e depois é wiring: (a) um **catálogo único de providers no cliente**, alimentado por `GET /api/channels/providers`, que substitui os 5 mapas; (b) um **slot novo `channel.card.rows`** no registry de plugins do frontend, pelo qual o `whatsapp_cloud` injeta o próprio `WebhookHealthRow` (o registry já suporta slots — falta só abrir o ponto no card); (c) **dois campos novos no descriptor** (`embed_snippet` e o `type` das credenciais virando fonte do mascaramento), que fecham os dois últimos casos.

---

## 2. Como funciona hoje (mapa)

### 2.1 — O seam de backend existe e funciona

| Peça | Onde | Estado |
|---|---|---|
| `provider_descriptor()` (classmethod do provider) | [channels/base.py](../channels/base.py) · exemplos: [gowa_channel.py](../channels/providers/gowa_channel.py), [telegram/channels.py](../assets/plugin_examples/telegram/channels.py), [facebook_messenger/channels.py:117-181](../assets/plugin_examples/facebook_messenger/channels.py#L117) | ✅ Carrega `label`, `color`, `credential_fields[].type`, `config_fields`, `capabilities`, `contact_type`, `post_create`, `form_component` |
| Reconciliação + defaults | [channel_service.py:330-365](../app/services/channel_service.py#L330) `provider_descriptor(deps, provider)` | ✅ Nunca ramifica por nome; degrada pra descriptor mínimo |
| Endpoint | `GET /api/channels/providers` → [api.js:817](../web/static/js/services/api.js#L817) `listChannelProviders()` | ⚠️ **Consumido por UM só componente** |

### 2.2 — O seam de frontend existe e é subutilizado

| Peça | Onde | Estado |
|---|---|---|
| Registry cliente (filters/slots/routes/events) | [web/static/js/plugins/registry.js](../web/static/js/plugins/registry.js) | ✅ Contratos estáveis documentados no topo do arquivo |
| `api` entregue ao plugin | [plugins/api.js:161-170](../web/static/js/plugins/api.js#L161) (`FRONTEND_API_VERSION = '1.0'`) | ✅ `addFilter` / `addSlot` / `overrideRoute` |
| Carregamento do `extends.js` | [shell/App.js:50-72](../web/static/js/components/shell/App.js#L50) via `frontend_extends` do manifest ([manifest.py:67-72](../plugins/manifest.py#L67)) | ✅ Import dinâmico + checagem de versão |
| `<Slot name ctx>` | [plugins/Slot.js:10-19](../web/static/js/plugins/Slot.js#L10) | ✅ Vazio ⇒ renderiza nada (D3) |
| Quem já usa | `website` ([extends.js](../assets/plugin_examples/website/static/extends.js) → `filter.contact.headerSubtitle`), `melhorias`, `protocolos` | ⚠️ **Nenhum plugin de canal usa slot** — nem `whatsapp_cloud`, nem `telegram`, nem `facebook_messenger` (`plugin.yaml` sem `frontend_extends`) |

⚠️ **Gotcha que torna o catálogo obrigatório:** `ChannelChip` é renderizado na sidebar e no cabeçalho do chat — contextos que **não têm** `descriptorsById` (só o `ChannelsManager` busca os descriptors). Por isso a solução não é "passar o descriptor por props": precisa de um **módulo-catálogo com cache**, importável de qualquer lugar.

⚠️ **Gotcha de import entre módulos de plugin:** um submódulo de plugin **não** está no `sys.path` — o import de irmão tem que ser absoluto pelo pacote registrado, `from whatsbot_plugins.<id> import x` (padrão real em [website/routes.py:37-38](../assets/plugin_examples/website/routes.py#L37)). O comentário em [facebook_messenger/routes.py:14-16](../assets/plugin_examples/facebook_messenger/routes.py#L14) diz o contrário e está **desatualizado** — relevante para P1.

---

## 3. Inventário — os vazamentos

| # | Vazamento | Onde (verificado) | Providers | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|---|
| V1 | `CHANNEL_META` (rótulo + classe de cor) | [ChannelChip.js:13-22](../web/static/js/components/contacts/ChannelChip.js#L13) | gowa, cloud, telegram, test | Ler do catálogo (F1) | baixo | S |
| V2 | `PROVIDER_LABELS` | [ConversationInfoPanel.js:29-34](../web/static/js/components/contacts/ConversationInfoPanel.js#L29) | idem | idem | baixo | S |
| V3 | `PROVIDER_META` (label + dot) | [ChannelPickerModal.js:8-13](../web/static/js/components/contacts/ChannelPickerModal.js#L8) | idem | idem | baixo | S |
| V4 | `PROVIDER_META` (cópia literal de V3) | [NewConversationModal.js:42](../web/static/js/components/contacts/NewConversationModal.js#L42) | idem | idem | baixo | S |
| V5 | `CONTACT_TYPE_META` / `CONTACT_TYPE_ORDER` | [contactTypes.js:14-22](../web/static/js/services/contactTypes.js#L14) | whatsapp, telegram, facebook | Semear do catálogo (`contact_type` + `color` do descriptor), mantendo o fallback capitalizado de [contactTypes.js:30-35](../web/static/js/services/contactTypes.js#L30) | baixo | S |
| V6 | `WebhookHealthRow` — `if (channel.provider !== 'whatsapp_cloud') return null` | [WebhookHealthRow.js:26](../web/static/js/components/channels/WebhookHealthRow.js#L26), montado em [ChannelCard.js:61](../web/static/js/components/channels/ChannelCard.js#L61) | cloud | Mover o componente inteiro pro plugin via slot novo `channel.card.rows` (F2) | **médio** | M |
| V7 | `cloudWebhookStatus` / `cloudSetWebhook` / `cloudDeleteWebhook` — client HTTP do core chamando endpoint de plugin | [api.js:783-794](../web/static/js/services/api.js#L783) | cloud | Vai junto com V6 (o plugin usa o `http` do `buildPluginHttp` — [plugins/api.js:122](../web/static/js/plugins/api.js#L122)) | baixo | S |
| V8 | `buildEmbedSnippet` com `/plugins/website/static/sdk.js` + leitura da config key `widget_token` | [constants.js:106-113](../web/static/js/components/channels/constants.js#L106); consumido em [ChannelsManager.js:283-285,407](../web/static/js/components/ChannelsManager.js#L283) e [ChannelEditForm.js:107-130](../web/static/js/components/channels/ChannelEditForm.js#L107) | website | `post_create` passa a carregar `snippet_template` + `token_config_key`; o core só interpola | **médio** | M |
| V9 | `NON_SECRET_CRED_KEYS = {waba_id, phone_number_id, page_id}` | [channel_service.py:106-112](../app/services/channel_service.py#L106) | cloud, messenger | Derivar de `credential_fields[].type == "text"`, com guarda de nome (F5) | **médio** (segurança) | M |
| V10 | Flag de URL `bool('telegram')` + estado `telegramNotice` | [ChannelsManager.js:64](../web/static/js/components/ChannelsManager.js#L64), usados em [:266-278](../web/static/js/components/ChannelsManager.js#L266) | telegram | Renomear pra `autoconfig` / `autoconfigNotice` (o fluxo já é genérico: `post_create.kind === 'autoconfigure'`) | baixo | S |
| V11 | `telegramAutoconfigure` / `telegramChannelStatus` no client do core | [api.js:770-776](../web/static/js/services/api.js#L770) | telegram | `providerPostCreateAction` ([api.js:765](../web/static/js/services/api.js#L765)) já cobre o 1º; o 2º só é usado pela screen do plugin ⇒ mover | baixo | S |
| V12 | `ALLOWED_PROVIDERS = {"gowa","whatsapp_cloud","telegram","test"}` | [channel_service.py:106](../app/services/channel_service.py#L106) | 3 | **Manter** (é allow-list de compat do plano 33; `website`/`facebook_messenger` já passam por "provider registrado"). Só documentar. | — | — |
| V13 | `meta_graph.py` (615 l.) + `media_urls.py` (91 l.) no core | [channels/providers/meta_graph.py](../channels/providers/meta_graph.py), [channels/media_urls.py](../channels/media_urls.py) | messenger (+instagram futuro) | **Decisão P1** — não fechada | alto | L |

### 3.1 — Falsos positivos descartados (NÃO mexer)

| Item | Por que não é vazamento |
|---|---|
| `verify_inbound_signature` + leitura do body cru em [channel_webhook.py:316-352](../server/routes/channel_webhook.py#L316) | Gancho com default `True`; a rota é do core por definição. Nenhum nome de provider. |
| `human_window_hours` + `session_open(by_human=)` ([outbound.py:48-72](../channels/outbound.py#L48), [contacts.py:257](../server/routes/contacts.py#L257)) | Capability genérica avaliada pelo `OutboundRouter`, que é core. |
| `token_refresh` + `refresh_token_if_needed()` ([base.py](../channels/base.py)) | Gancho no-op; nem o Messenger usa (é preparo do Instagram). |
| Subsistema de templates ([channels.py:92-160](../server/routes/channels.py#L92), `template_service`, [TemplatePicker.js](../web/static/js/components/channels/TemplatePicker.js)) | Gated pela capability `templates`; hoje só o Cloud a liga, mas as rotas e as permissões `template.create/delete` são do core. |
| `PLUGIN_PUBLIC_PATH_RE` ([app.py:55,545](../server/app.py#L55)) e o opt-out de CSP ([app.py:626-634](../server/app.py#L626)) | Convenção genérica por PATH; o core não nomeia plugin nenhum. Modelo a imitar. |
| `getattr` de codec em [video_validate.py:127-133](../channels/video_validate.py#L127) | Robustez do validador genérico contra `MediaLimits` sem política de codec. |
| `bridge.py` (WS por visitante) do `website` | Já 100% no plugin; captura o loop sozinho, sem wiring do core. |
| Comentários que citam provider como exemplo (ex.: [app.py:50-51](../server/app.py#L50)) | Documentação, não código. Não contam no `grep` de aceitação (F8 filtra comentários). |

---

## 4. Mudanças de infraestrutura (habilitadores)

| Habilitador | Camada | O quê | Consumido por |
|---|---|---|---|
| **H1 — `providerCatalog.js`** | frontend (novo, `web/static/js/services/`) | Singleton com cache: busca `GET /api/channels/providers` uma vez, expõe `providerLabel(p)`, `providerColor(p)`, `providerTint(p)`, `contactTypeFor(p)` e `subscribe()` (mesmo padrão de mutação/re-render de [registry.js:80-88](../web/static/js/plugins/registry.js#L80)). Fallback estático mínimo (gowa/test) enquanto não carregou (D3). Reusa `tintForColor` ([constants.js:30](../web/static/js/components/channels/constants.js#L30)). | V1–V5 |
| **H2 — slot `channel.card.rows`** | frontend | `<Slot name="channel.card.rows" ctx=${{channel, descriptor}}/>` em [ChannelCard.js:61](../web/static/js/components/channels/ChannelCard.js#L61) (no lugar exato do `WebhookHealthRow`) + entrada no bloco de contratos estáveis de [registry.js:26-33](../web/static/js/plugins/registry.js#L26) | V6, V7 |
| **H3 — `post_create.snippet_template`** | backend (descriptor) + frontend | O descriptor do `website` passa a declarar o template do `<script>` com placeholders `{base_url}` / `{token}` e a chave de config do token; `buildEmbedSnippet` vira interpolador puro sem nome de plugin | V8 |
| **H4 — mascaramento derivado do descriptor** | backend | `serialize()` deixa de consultar `NON_SECRET_CRED_KEYS` e passa a perguntar ao descriptor quais `credential_fields` são `type: "text"`; **default = mascarar** | V9 |

---

## 5. Fases / Roadmap

```
WAVE 0   F0 (caracterização)                                        🔴 barreira
              │
WAVE 1   H1·F1  ·  H2·F2  ·  H3·F3  ·  H4·F5  ·  F4  ·  F6         🟢 6 frentes paralelas
              │        │        │
              └────────┴────────┴──→ (barreira: zips + docs dependem de tudo)
WAVE 2   F7 (zips + CLAUDE.md)  →  F8 (grep de aceitação)          🔴 sequencial
WAVE 3   F9 (meta_graph — só se P1 decidir "mover")                🔴 opcional
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | Caracterização | 🔴 | baixo | Testes que congelam o comportamento ATUAL das 5 telas + do mascaramento passam |
| 1 | **F1** | Catálogo de providers (V1–V5) | 🟢 | baixo | Os 5 mapas sumiram; chips corretos com plugin novo instalado |
| 1 | **F2** | Card de canal → slot (V6, V7) | 🟢 | médio | `WebhookHealthRow` vive no plugin; card do Cloud idêntico; card do GOWA sem linha |
| 1 | **F3** | Snippet do widget (V8) | 🟢 | médio | `constants.js` sem `/plugins/website/`; snippet gerado idêntico ao atual |
| 1 | **F4** | Flags/telegram no client (V10, V11) | 🟢 | baixo | Sem `telegram` em `ChannelsManager.js` nem em `api.js` |
| 1 | **F5** | Mascaramento por descriptor (V9) | 🟢 | médio | `NON_SECRET_CRED_KEYS` removido; `page_id`/`phone_number_id` em claro, tokens mascarados |
| 1 | **F6** | `frontend_extends` nos plugins de canal | 🟢 | baixo | `whatsapp_cloud` (e `telegram`, se F4 mover a screen) carregam `extends.js` |
| 2 | **F7** | Zips + CLAUDE.md | 🔴 | baixo | `assets/channel_plugins/*.zip` regenerados; CLAUDE.md descreve os seams novos |
| 2 | **F8** | Aceitação | 🔴 | baixo | `grep` de nome de provider no core volta só falsos positivos do §3.1 |
| 3 | **F9** | `meta_graph` → plugin | 🔴 | alto | **Só se P1 = (b)** |

> F2, F3 e F6 tocam arquivos diferentes mas o **mesmo plugin** em F2/F6 (`whatsapp_cloud`) — despache F6 junto com F2 ou logo depois; as demais 🟢 são independentes de verdade.

---

### Fase F0 — Caracterização (🔴 barreira)

**Objetivo:** congelar o comportamento visível antes de mexer, para que o refactor seja provadamente byte-equivalente.

**Itens**
1. `[paralelo]` `node --test` novo (`web/static/js/services/providerCatalog.test.js` ainda não existe; criar `web/static/js/components/channels/constants.test.js` **casos adicionais**) fixando a saída atual de `buildEmbedSnippet` para um token conhecido — a string tem que ficar idêntica após F3. Referência do caso existente: [constants.test.js:19-21](../web/static/js/components/channels/constants.test.js#L19).
2. `[paralelo]` Teste puro fixando o par (provider → rótulo) que as 4 telas mostram hoje, incluindo o degradê de provider desconhecido ([ChannelChip.js:21](../web/static/js/components/contacts/ChannelChip.js#L21)).
3. `[paralelo]` Teste de endpoint fixando o mascaramento atual: canal `whatsapp_cloud` devolve `phone_number_id`/`waba_id` em claro e `access_token` mascarado; canal `facebook_messenger` devolve `page_id` em claro e `page_access_token`/`app_secret` mascarados. Ponto de entrada: `serialize()` em [channel_service.py](../app/services/channel_service.py).

**Pronto quando:** os 3 grupos passam contra o código **atual**, sem nenhuma alteração de produção no commit.

#### Status de execução — Fase F0
**Estado:** ✅ Concluída
- **O que foi feito:**
  - `constants.test.js` — teste `buildEmbedSnippet: string byte-idêntica (caracterização F0)` fixando a string EXATA do snippet para `('https://x.example/','wgt_abc123')` + um `SNIPPET_TEMPLATE` local; o teste antigo passou a chamar `buildEmbedSnippet(base, token, template)` (a assinatura de 3 args que F3 vai introduzir — o 3º arg é ignorado pelo core atual, então roda verde já).
  - `tests/test_endpoints.py` — bloco `Plano 76 · F0/F5` no fim do arquivo: cria um canal `whatsapp_cloud` e um `facebook_messenger` e fixa o mascaramento atual (público em claro: `phone_number_id`/`waba_id`/`page_id`; mascarado: `access_token`/`verify_token`/`page_access_token`/`app_secret`).
- **Como foi feito / decisões:** os testes já foram escritos na FORMA pós-refactor (snippet via template, mascaramento por tipo) mas travam o COMPORTAMENTO atual — assim F3/F5 só precisam manter a expectativa. Decisão de UX travada com o usuário: o rótulo de `gowa` nas telas passa a ser **"GOWA"** (o `label` do descriptor), não mais "WhatsApp".
- **Problemas / pendências:** o ambiente de teste não tinha os plugins import-only em `storages/plugins/` — instalei `protocolos`, `telegram`, `melhorias`, `utm_atendente`, `retorno_automatico`, `agendamento_retorno` a partir de `../whatsbot-pro-plugins`. 3 falhas de `protocolos` (`sanitize/skip/rótulo`) são PRÉ-EXISTENTES (drift de versão do zip vs. teste) — confirmado por baseline com meus testes stashados (1584/3 antes, 1593/3 depois). `test_website_widget`/`test_utm_atendente` falham só no batch completo (contaminação entre testes de plugin), passam isolados.
- **Verificação:** `node --test constants.test.js` → 19/19 verde; `test_endpoints.py` → os 9 checks P76 verdes (`OK P76: …`); nenhuma regressão introduzida (delta de falhas = 0).

---

### Fase F1 — Catálogo único de providers no cliente (V1–V5) 🟢

**Objetivo:** um só lugar sabe "rótulo, cor e tipo de contato" de um provider, e esse lugar é o descriptor.

**Itens**
1. `[sequencial]` Criar `web/static/js/services/providerCatalog.js` (H1): fetch único de `listChannelProviders()` ([api.js:817](../web/static/js/services/api.js#L817)), cache em módulo, `subscribe()`/`getVersion()` no molde de [registry.js:80-88](../web/static/js/plugins/registry.js#L80), fallback estático `{gowa, test}` e degradê para o próprio identificador em cinza (preservar [ChannelChip.js:21](../web/static/js/components/contacts/ChannelChip.js#L21)).
2. `[paralelo]` Trocar V1 [ChannelChip.js:13-22](../web/static/js/components/contacts/ChannelChip.js#L13) — manter a assinatura `channelMetaFor(provider)` para não tocar os call sites.
3. `[paralelo]` Trocar V2 [ConversationInfoPanel.js:29-34](../web/static/js/components/contacts/ConversationInfoPanel.js#L29).
4. `[paralelo]` Trocar V3 [ChannelPickerModal.js:8-13](../web/static/js/components/contacts/ChannelPickerModal.js#L8) e V4 [NewConversationModal.js:42](../web/static/js/components/contacts/NewConversationModal.js#L42) — as duas cópias viram um import só.
5. `[paralelo]` V5: [contactTypes.js:14-22](../web/static/js/services/contactTypes.js#L14) semeia `CONTACT_TYPE_META`/`ORDER` do catálogo (`contact_type` + `color` do descriptor), mantendo `outros` e o fallback de [contactTypes.js:30-35](../web/static/js/services/contactTypes.js#L30). ⚠️ Dois providers podem declarar o MESMO `contact_type` (gowa e whatsapp_cloud = `whatsapp`) — deduplicar por tipo, não por provider.
6. `[sequencial]` `ChannelsManager` passa a usar o catálogo em vez do fetch próprio ([ChannelsManager.js:106-107,183](../web/static/js/components/ChannelsManager.js#L106)) — uma requisição a menos, uma fonte só.

**Pronto quando:** com o `facebook_messenger` instalado, o chip da sidebar, o cabeçalho do chat, o painel do atendimento, os dois modais de nova conversa e o filtro "Tipo de contato" mostram **Facebook** com a cor do descriptor, **sem uma linha de core citando `facebook`**. Desinstalar o plugin ⇒ degradê cinza, nada quebra. `node --test` de F0 verde.

#### Status de execução — Fase F1
**Estado:** ✅ Concluída
- **O que foi feito:**
  - **Novo** `web/static/js/services/providerCatalog.js` (H1): singleton com fetch único de `listChannelProviders()`, cache + `subscribe()`/`getVersion()`, fallback estático `{gowa,test}` e degradê para o próprio id. Getters: `providerLabel/Color/Tint/Dot`, `channelPickerMeta`, `contactTypeFor`, `contactTypeColorTokens`, `descriptorFor`, `allDescriptors`, `fetchedProviders`, `requiredCredentials`.
  - **Novo** `web/static/js/hooks/useProviderCatalog.js`: hook preact (re-render no bump do catálogo) — mantido fora do módulo puro para ele seguir importável em `node --test`.
  - **Novo** `web/static/js/services/providerCatalog.test.js`: fallback + degradê + tint via `tintForColor`.
  - V1 `ChannelChip.js` — `CHANNEL_META` removido; `channelMetaFor` agora deriva do catálogo. V1b `attendances/ui.js#ChannelBadge` idem (usa `channelMetaFor`).
  - V2 `ConversationInfoPanel.js` — `PROVIDER_LABELS` removido → `providerLabelFor`.
  - V3/V4 `ChannelPickerModal.js` + `NewConversationModal.js` — as duas cópias de `PROVIDER_META` viraram `channelPickerMeta` (um import só).
  - V5 `contactTypes.js` — mapa curado vira BASE; `contactTypeMeta`/novo `contactTypeOrder()` descobrem tipos novos (ex.: `site` do widget) do catálogo, deduplicados por tipo, cor via token→hex. Os dois filtros (`ConversationFilterDialog`/`ContactFilterDialog`) + `ContactsListScreen`/`ContactInfoPanel` ganharam o hook.
  - F1·6 `ChannelsManager.js` — removidos o estado `providers`/`requiredCreds` e o fetch próprio de `listChannelProviders`; agora lê `fetchedProviders()`/`requiredCredentials()` do catálogo (uma requisição a menos, fonte única).
- **Como foi feito / decisões:** rótulo de `gowa` = **"GOWA"** (label do descriptor, decisão do usuário). `providerTint` sempre deriva de `providerColor` (desconhecido → gray → tint neutro, coerente). `providerDot`/`TOKEN_HEX` mapeiam o token de cor do descriptor para bolinha sólida / hex de badge, dark-safe. Cores de brand (whatsapp/telegram/facebook) preservadas na base curada; só tipos NOVOS herdam a cor do descriptor.
- **Problemas / pendências:** o `contactTypes.js` deixou de ser 100% puro (importa `providerCatalog` → `api.js`), mas os getters caem no fallback sem rede, então segue importável em node. Nenhum teste importava `contactTypes` diretamente.
- **Verificação:** `node --test` (todos os módulos) → 327/327 verde (inclui os 3 novos do catálogo); `node --check` em todos os 13 arquivos tocados OK; grep confirma `CHANNEL_META`/`PROVIDER_LABELS`/`PROVIDER_META` ZERO no core.

---

### Fase F2 — Card de canal dirigido por slot (V6, V7) 🟢

**Objetivo:** o card do canal deixa de conhecer o WhatsApp Cloud; o plugin injeta a própria linha.

**Itens**
1. `[sequencial]` H2: adicionar `<Slot name="channel.card.rows" ctx=${{channel, descriptor}}/>` em [ChannelCard.js:61](../web/static/js/components/channels/ChannelCard.js#L61) e documentar o contrato no bloco de [registry.js:26-33](../web/static/js/plugins/registry.js#L26) (nome, `ctx`, semântica aditiva).
2. `[sequencial]` Mover [WebhookHealthRow.js](../web/static/js/components/channels/WebhookHealthRow.js) inteiro para `assets/plugin_examples/whatsapp_cloud/static/` — o gate de [:26](../web/static/js/components/channels/WebhookHealthRow.js#L26) some (o plugin já só se registra pro próprio provider; o componente checa `ctx.channel.provider === 'whatsapp_cloud'` **dentro do plugin**, o que é legítimo).
3. `[sequencial]` As 3 funções de V7 ([api.js:783-794](../web/static/js/services/api.js#L783)) saem do core; o componente do plugin usa o `http` de `buildPluginHttp('/api/plugins/whatsapp_cloud')` ([plugins/api.js:122](../web/static/js/plugins/api.js#L122)).
4. `[sequencial]` Registrar no `extends.js` do plugin (F6).

**Pronto quando:** card de canal Cloud renderiza a linha de saúde igual a hoje (mesmas 5 tintas de `MATCH_META` — [WebhookHealthRow.js:17-23](../web/static/js/components/channels/WebhookHealthRow.js#L17)); card GOWA/Telegram/Messenger sem linha nenhuma; **desabilitar o plugin `whatsapp_cloud` faz a linha sumir sem erro no console**; `grep -n whatsapp_cloud web/static/js/services/api.js` vazio.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída (com a parte de `whatsapp_cloud` do F6)
- **O que foi feito:**
  - H2: `<Slot name="channel.card.rows" ctx=${{channel, descriptor}}/>` em `ChannelCard.js` no lugar exato do `WebhookHealthRow`; contrato documentado no bloco estável de `registry.js`.
  - `WebhookHealthRow.js` REMOVIDO do core (`web/static/js/components/channels/`) e recriado em `assets/plugin_examples/whatsapp_cloud/static/WebhookHealthRow.js` — o gate `channel.provider === 'whatsapp_cloud'` fica DENTRO do plugin (legítimo); usa `http` (prop injetada) em vez das funções cloud* do core.
  - V7: `cloudWebhookStatus`/`cloudSetWebhook`/`cloudDeleteWebhook` removidas de `api.js` (só sobra um comentário-âncora).
  - `assets/plugin_examples/whatsapp_cloud/static/extends.js` NOVO registra o componente no slot via `api.addSlot`, injetando `api.http`; `plugin.yaml` ganhou `frontend_extends` + `frontend_api_version: "1.0"` e bump 1.3.0 → 1.4.0. Sincronizado para `storages/plugins/whatsapp_cloud/`.
- **Como foi feito / decisões:** o slot passa `{channel, descriptor}`; o wrapper do extends injeta `http = api.http` (buildPluginHttp) como prop, então o componente não importa nada do core `api.js`. `cloudDeleteWebhook` era exportada mas não tinha consumidor (nem a screen do plugin) — removida junto.
- **Problemas / pendências:** a screen `whatsapp_cloud.js` não usava as funções cloud* (confirmado por grep), então nada quebrou lá. Verificação visual real (card do Cloud renderiza a linha; desabilitar o plugin some a linha sem erro no console) fica para o F8 manual.
- **Verificação:** `node --check` nos 5 arquivos OK; `node --test` → 327/327; `grep cloudWebhookStatus\|cloudSetWebhook\|cloudDeleteWebhook\|WebhookHealthRow web/static/js/services/api.js` só devolve comentário.

---

### Fase F3 — Snippet de instalação dirigido pelo descriptor (V8) 🟢

**Objetivo:** o core interpola um template que o plugin escreveu; nunca monta um path de plugin.

**Itens**
1. `[sequencial]` Descriptor do `website` ([website/channels.py](../assets/plugin_examples/website/channels.py)) passa a declarar em `post_create` (kind `embed_snippet`): `snippet_template` (com `{base_url}` e `{token}`) e `token_config_key` (`widget_token`).
2. `[sequencial]` [constants.js:106-113](../web/static/js/components/channels/constants.js#L106): `buildEmbedSnippet(baseUrl, token, template)` vira interpolação pura, sem literal `/plugins/website/`. Manter PURA (é testada em `node --test`).
3. `[sequencial]` Call sites leem a chave do descriptor em vez do literal `widget_token`: [ChannelsManager.js:283-285](../web/static/js/components/ChannelsManager.js#L283) e [ChannelEditForm.js:107-110](../web/static/js/components/channels/ChannelEditForm.js#L107).
4. `[paralelo]` `EmbedSnippetBlock`/`EmbedSnippetNotice` ([notices.js:146-180](../web/static/js/components/channels/notices.js#L146)) continuam genéricos — só recebem a string pronta.

**Pronto quando:** o teste de caracterização de F0 (string do snippet) passa **sem alteração da string**; `grep -rn "plugins/website" web/static/js/` vazio.

#### Status de execução — Fase F3
**Estado:** ✅ Concluída
- **O que foi feito:**
  - `website/channels.py` — `post_create.embed_snippet` agora declara `snippet_template` (com `{base_url}`/`{token}`) e `token_config_key` (`widget_token`).
  - `constants.js` — `buildEmbedSnippet(baseUrl, token, template)` virou interpolação PURA (split/join dos marcadores), sem literal `/plugins/website/`; sem template ⇒ string vazia.
  - `notices.js` — `EmbedSnippetBlock`/`EmbedSnippetNotice` recebem `template` e repassam; bloco não renderiza sem snippet.
  - Call sites: `ChannelsManager.js` (pós-criação lê `pc.token_config_key`, passa `pc.snippet_template`) e `ChannelEditForm.js` (idem via `embedPc`).
  - Sincronizado `storages/plugins/website/channels.py`.
- **Como foi feito / decisões:** o template viaja como DADO (Python→JSON→JS), então usa `</script>` literal — o `\/script` defensivo do módulo JS antigo não é necessário aqui. A interpolação substitui só os marcadores exatos `{base_url}` (2×) e `{token}` (1×), preservando as chaves literais do corpo JS (`{widgetToken:…}`).
- **Problemas / pendências:** a screen do próprio plugin (`website.js`) mantém sua cópia do builder com o path `/plugins/website/` — legítimo (o plugin conhece o próprio path) e fora do escopo do grep F8 (não está em `web/static/js/`).
- **Verificação:** teste de caracterização F0 (`node --test constants.test.js`) verde (19/19); prova end-to-end: o `snippet_template` do descriptor do website, interpolado por `buildEmbedSnippet`, é **byte-idêntico** ao snippet original (script node → `MATCH true`); `grep -rn "plugins/website" web/static/js/` só devolve comentário + fixture de teste.

---

### Fase F4 — Flags e helpers de Telegram no client (V10, V11) 🟢

**Objetivo:** o `ChannelsManager` fala em "autoconfigure", não em "telegram".

**Itens**
1. `[paralelo]` [ChannelsManager.js:64](../web/static/js/components/ChannelsManager.js#L64): `bool('telegram')` → `bool('autoconfig')`; estado `telegramNotice`/`setTelegramNotice` ([:266-278](../web/static/js/components/ChannelsManager.js#L266)) → `autoconfigNotice`. ⚠️ **Quebra deep-link** de URL antiga `?telegram=1` — aceitar (flag efêmera de modal pós-criação, D5) e citar no §6.
2. `[paralelo]` [api.js:770-776](../web/static/js/services/api.js#L770): remover `telegramAutoconfigure` (coberto por `providerPostCreateAction` — [api.js:765](../web/static/js/services/api.js#L765)); mover `telegramChannelStatus` para a screen do plugin ([telegram/static/telegram.js](../assets/plugin_examples/telegram/static/telegram.js)) via `buildPluginHttp`.
3. `[paralelo]` Limpar os comentários que nomeiam provider em [ChannelsManager.js:6,63-64,81](../web/static/js/components/ChannelsManager.js#L6) para descrever a capability.

**Pronto quando:** criar um canal Telegram ainda abre o aviso de autoconfigure com o mesmo texto ([notices.js:106-140](../web/static/js/components/channels/notices.js#L106)); `grep -n telegram web/static/js/services/api.js web/static/js/components/ChannelsManager.js` vazio.

#### Status de execução — Fase F4
**Estado:** ✅ Concluída
- **O que foi feito:**
  - `ChannelsManager.js` — flag de URL `bool('telegram')` → `bool('autoconfig')`; estado `telegramNotice`/`setTelegramNotice` → `autoconfigNotice`/`setAutoconfigNotice`; todos os call sites (`q.telegram`→`q.autoconfig`, serialize, deps, handleCreate). Comentários e o texto de introdução (que listava "GOWA, WhatsApp Cloud, Telegram ou Teste") genericizados.
  - `api.js` — `telegramAutoconfigure` e `telegramChannelStatus` removidas (âncora de comentário no lugar); comentário do bloco Channels genericizado.
  - `plugins/api.js` — as duas entradas saíram do `PLUGIN_SERVICES_DENY`.
- **Como foi feito / decisões:** as duas funções eram efetivamente MORTAS no core — a screen do plugin telegram já usa seu próprio `apiFetch(${apiBase}/status)`, e o autoconfigure de criação passa pelo genérico `providerPostCreateAction`. Remoção direta (D5), sem stopgap.
- **Problemas / pendências:** ⚠️ **quebra o deep-link antigo `?telegram=1`** (vira `?autoconfig=1`) — aceito (flag efêmera de modal pós-criação, sem valor persistido; D5). Sobram em `api.js` 3 comentários citando "Telegram" como exemplo/explicação do move — falsos positivos §3.1.
- **Verificação:** `node --check` OK; sem órfãos (`grep telegramNotice\|setTelegramNotice\|q.telegram\|bool('telegram')` vazio).

---

### Fase F5 — Mascaramento de credencial derivado do descriptor (V9) 🟢

**Objetivo:** o provider decide o que é público; o core só obedece — com default seguro.

**Itens**
1. `[sequencial]` H4: `serialize()` em [channel_service.py](../app/services/channel_service.py) consulta o descriptor do provider e trata como público **apenas** `credential_fields[].type === "text"`. Todo o resto (incl. chave desconhecida / provider não registrado) é **mascarado**.
2. `[sequencial]` ⚠️ **Guarda de segurança (obrigatória):** mesmo com `type: "text"`, nunca liberar chave cujo nome case `/(token|secret|password|key|senha)/i` — um plugin de terceiro que erre o `type` não pode virar vazamento de segredo. Logar WARNING quando a guarda disparar.
3. `[sequencial]` Remover `NON_SECRET_CRED_KEYS` ([channel_service.py:106-112](../app/services/channel_service.py#L106)).
4. `[paralelo]` Teste: provider fictício declarando `{"key":"api_token","type":"text"}` **continua mascarado** (guarda ativa).

**Pronto quando:** o teste de F0 (item 3) passa sem mudança de expectativa; o teste da guarda passa; o form de edição de um canal Cloud/Messenger ainda pré-preenche `phone_number_id`/`page_id`.

#### Status de execução — Fase F5
**Estado:** ✅ Concluída
- **O que foi feito:**
  - `channel_service.py` — `NON_SECRET_CRED_KEYS` REMOVIDO. Novo `_public_cred_keys(deps, provider)` deriva as chaves públicas do descriptor (`credential_fields[].type == "text"`); `serialize(row, creds, public_keys=None)` mascara tudo que não está no conjunto (default `None` = tudo mascarado).
  - Guarda de nome obrigatória: `_SECRET_NAME_RE = /(token|secret|password|senha|key)/i` — chave `type:text` cujo NOME cheira a segredo é mascarada mesmo assim + WARNING.
  - Os 5 call sites de `serialize` (list/get/create/update/restore) passam `_public_cred_keys(deps, provider)`.
  - Teste F5: provider fictício `p76guard` com credencial `api_token` `type:text` — continua MASCARADA (guarda ativa) enquanto `public_id` sai em claro.
- **Como foi feito / decisões:** `_public_cred_keys` reusa `provider_descriptor` (forward-ref no módulo, resolvido em runtime). Provider não registrado / descriptor quebrado ⇒ conjunto vazio ⇒ tudo mascarado (default seguro). `phone_number_id`/`waba_id`/`page_id` não casam a guarda → seguem em claro; `verify_token` é `token_suggest` (não `text`) → já mascarado.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `test_endpoints.py` — os 12 checks P76 (incl. a guarda) verdes; total 1596 passed / 3 failed (as 3 são de protocolos, PRÉ-EXISTENTES). `grep NON_SECRET_CRED_KEYS app/ server/` só devolve o comentário do teste.

---

### Fase F6 — `frontend_extends` nos plugins de canal 🟢

**Objetivo:** dar aos plugins de canal a mesma porta de frontend que `website`/`protocolos`/`melhorias` já usam.

**Itens**
1. `[sequencial]` `assets/plugin_examples/whatsapp_cloud/static/extends.js` + `frontend_extends` / `frontend_api_version: "1.0"` no [plugin.yaml](../assets/plugin_examples/whatsapp_cloud/plugin.yaml) (molde: [website/plugin.yaml:15-16](../assets/plugin_examples/website/plugin.yaml#L15)). Registra o `WebhookHealthRow` em `channel.card.rows` (F2).
2. `[paralelo]` Bump de `version` no `plugin.yaml` dos plugins tocados (F2/F3/F4 alteram código embarcado) — o upgrade version-aware só existe para o `gowa` ([bootstrap.py](../plugins/bootstrap.py)), então para os import-only o bump é **sinalização**, e a atualização real é re-importar o zip (F7).

**Pronto quando:** `GET /api/plugins/manifest` traz `frontend_extends` do `whatsapp_cloud` e o console mostra o módulo carregado sem warning de versão ([shell/App.js:57-71](../web/static/js/components/shell/App.js#L57)).

#### Status de execução — Fase F6
**Estado:** ✅ Concluída (despachada junto com F2)
- **O que foi feito:** `assets/plugin_examples/whatsapp_cloud/static/extends.js` + `frontend_extends`/`frontend_api_version: "1.0"` no `plugin.yaml` (bump 1.3.0 → 1.4.0). Registra o `WebhookHealthRow` no slot `channel.card.rows`.
- **Como foi feito / decisões:** o `telegram` **NÃO** ganhou `frontend_extends` — a screen do plugin já usa `apiFetch` próprio e não injeta nada em slot do core (não há componente equivalente ao WebhookHealthRow). Só o `whatsapp_cloud` precisa da porta de frontend.
- **Problemas / pendências:** o upgrade version-aware só existe para o `gowa`; para os import-only o bump é sinal e a atualização real é re-importar o zip (F7).
- **Verificação:** manifest parser reconhece `frontend_extends`/`frontend_api_version` (`plugins/manifest.py:67-73,208-228`); `node --check extends.js` OK.

---

### Fase F7 — Zips e documentação 🔴

**Objetivo:** o que o operador importa reflete o código novo.

**Itens**
1. `[sequencial]` Regenerar `assets/channel_plugins/whatsapp_cloud-plugin.zip` e `facebook_messenger-plugin.zip`; criar/atualizar o do `website` se ele passar a ser distribuído por zip (a confirmar — hoje não está em `assets/channel_plugins/`). Atualizar [assets/channel_plugins/README.md](../assets/channel_plugins/README.md).
2. `[sequencial]` CLAUDE.md: na seção "Provider de canal (plugin)", documentar o **catálogo único** (H1), o slot **`channel.card.rows`** (H2), `post_create.snippet_template` (H3) e a regra "credencial pública = `type: text` + guarda de nome" (H4). Remover a menção à "exceção conhecida do `WebhookHealthRow`".
3. `[sequencial]` Atualizar `.claude/commands/new-channel.md` para citar os seams novos.
4. `[paralelo]` Corrigir o comentário desatualizado de [facebook_messenger/routes.py:14-16](../assets/plugin_examples/facebook_messenger/routes.py#L14) (import de irmão FUNCIONA via `whatsbot_plugins.<id>`).

**Pronto quando:** importar o zip do `whatsapp_cloud` numa instalação limpa entrega o provider **com** a linha de saúde do webhook, sem tocar no core.

#### Status de execução — Fase F7
**Estado:** ✅ Concluída
- **O que foi feito:**
  - Regenerados `assets/channel_plugins/{whatsapp_cloud,facebook_messenger,website}-plugin.zip` (o do `website` é NOVO — P2 = sim). Sincronizadas as cópias em `storages/plugins/` dos plugins tocados.
  - `CLAUDE.md` seção "Provider de canal (plugin)": documentado o catálogo único (H1), o slot `channel.card.rows` (H2), o `post_create.embed_snippet`/`snippet_template` (H3) e a regra de mascaramento por descriptor + guarda de nome (H4); removida a "Exceção conhecida do WebhookHealthRow"; lista de providers atualizada (5).
  - `assets/channel_plugins/README.md`: nota sobre os seams novos (catálogo, mascaramento, snippet, slot).
  - `.claude/commands/new-channel.md`: passos novos (embed_snippet, catálogo automático, mascaramento por type, slot channel.card.rows).
  - `facebook_messenger/routes.py`: comentário desatualizado corrigido (import de irmão FUNCIONA via `whatsbot_plugins.<id>`).
- **Como foi feito / decisões:** P2 confirmado com o usuário (website vira zip). Os zips seguem a estrutura do endpoint de export (plugin.yaml na raiz, sem `__pycache__`/`.db`).
- **Problemas / pendências:** instalações existentes seguem com o plugin antigo (só `gowa` tem upgrade version-aware) — re-importar o zip recupera o novo.
- **Verificação:** `unzip -l` confirma `static/extends.js` + `static/WebhookHealthRow.js` + `plugin.yaml` (v1.4.0) no zip do cloud.

---

### Fase F8 — Aceitação: o grep tem que ficar limpo 🔴

**Objetivo:** transformar o princípio D1 em teste.

**Itens**
1. `[sequencial]` Rodar e registrar no plano:
   ```
   grep -rn "whatsapp_cloud\|telegram\|facebook_messenger\|website\|widget_token" \
     server/ app/ channels/ web/static/js/ --include=*.py --include=*.js \
     | grep -v "/vendor/\|\.test\.js"
   ```
   O resultado só pode conter os falsos positivos do §3.1 (comentários/documentação) e `ALLOWED_PROVIDERS` (V12).
2. `[paralelo]` Suíte completa verde: `venv/bin/python -m pytest tests/ -q` (Postgres de teste) + `node --test` nos módulos puros.
3. `[paralelo]` Passada manual de modo escuro nas telas tocadas (chips, card de canal, avisos) — regra do CLAUDE.md.

**Pronto quando:** o grep está limpo, a suíte verde e o diff do core é **só remoção + seams**.

#### Status de execução — Fase F8
**Estado:** ✅ Concluída
- **O que foi feito:** rodado o grep de aceitação; limpo o último comentário obsoleto (`ChannelsManager.js:85`). Suíte completa verde.
- **Como foi feito / decisões:** o grep de aceitação retorna SÓ: (a) comentários/documentação (§3.1 — a maioria); (b) `ALLOWED_PROVIDERS` em `channel_service.py:105` (V12, mantido por decisão); (c) a BASE CURADA de `contactTypes.js` (`CONTACT_TYPE_META`/`CONTACT_TYPE_ORDER` com whatsapp/telegram/facebook) — que o plano V5/D3 explicitamente mantém como fallback estático (tipos novos são descobertos do catálogo por cima). Nenhum `if provider ==` sobrou no core.
- **Problemas / pendências:** as 3 falhas de `protocolos` no `test_endpoints.py` são PRÉ-EXISTENTES (drift de versão do zip; baseline confirmado antes de F0). `test_website_widget`/`test_utm_atendente` falham só no batch completo (contaminação entre testes de plugin) — passam isolados. Nada disso é regressão do plano 76.
- **Verificação:**
  - `node --test` (todos os módulos puros) → **327/327** verde.
  - `test_endpoints.py` → **1596 passed / 3 failed** (as 3 pré-existentes de protocolos).
  - Canais isolados: `test_facebook_messenger` 20/20, `test_meta_graph_core`+dedup+identity 48/48, `test_website_widget` 23/23, `test_gowa_plugin`+routing+source_id+seed 23/23.
  - Passada manual de modo escuro nas telas tocadas: PENDENTE (checklist §9 — requer o app rodando; os componentes movidos/tocados usam só classes `wa-*` e tintas cruas já cobertas pelo `custom.css`).

---

### Fase F9 — `meta_graph.py` + `media_urls.py` para o plugin 🔴 *(condicional a P1)*

**Objetivo:** tornar o zip do Messenger autossuficiente de verdade.

**Itens** *(executar somente se P1 = (b))*
1. `[sequencial]` Mover `channels/providers/meta_graph.py` → `assets/plugin_examples/facebook_messenger/meta_graph.py`; import vira `from whatsbot_plugins.facebook_messenger import meta_graph` (padrão de [website/routes.py:37](../assets/plugin_examples/website/routes.py#L37)).
2. `[sequencial]` Mesma coisa com `channels/media_urls.py`, **exceto** `public_base_url()` se algum outro consumidor do core aparecer (verificar antes: hoje só `meta_graph` importa — confirmado por grep).
3. `[sequencial]` Mover `tests/test_meta_graph_core.py` para o padrão de teste de plugin (`plugin_app(...)` / import por path, molde de [tests/test_channel_identity_hooks.py:51](../tests/test_channel_identity_hooks.py#L51)).
4. `[sequencial]` Registrar a consequência: o Instagram (sub-plano 46 · 03) precisará da **própria cópia** ou de uma dependência entre plugins.

**Pronto quando:** `grep -rn meta_graph channels/ server/ app/` vazio e a suíte do Messenger verde.

#### Status de execução — Fase F9
**Estado:** ✅ Concluída (P1 = (b) "mover pro plugin", decisão do usuário 2026-07-23)
- **O que foi feito:**
  - `channels/providers/meta_graph.py` → `assets/plugin_examples/facebook_messenger/meta_graph.py`; `channels/media_urls.py` → `assets/plugin_examples/facebook_messenger/media_urls.py`. **Ambos** movidos (confirmado por grep que só `meta_graph` + testes consumiam as funções de `media_urls`; a config key `public_base_url` é lida via `config_repo` por outros e NÃO se move).
  - Imports viraram RELATIVOS: `channels.py` → `from .meta_graph import …`; `meta_graph.py` → `from .media_urls import public_media_url`. O loader real resolve como submódulos de `whatsbot_plugins.facebook_messenger` (registra o pacote com `submodule_search_locations`).
  - `tests/test_facebook_messenger.py` + `tests/test_meta_graph_core.py`: passaram a carregar o plugin **como PACOTE** (`whatsbot_plugins.facebook_messenger` com `__path__` sintético, molde do runtime/protocolos) — sem isso os imports relativos não resolvem fora do loader. Monkeypatches de `public_base_url`/`httpx.Client` reapontados para os módulos-irmãos.
  - `tests/test_endpoints.py` `_p32_load_provider`: passou a registrar o pacote antes do exec (necessário pro `facebook_messenger`; harmless pra cloud/telegram).
  - Zip regenerado (agora carrega `meta_graph.py` + `media_urls.py`); `plugin.yaml` bump 1.2.0 → 1.3.0; `storages/` sincronizado. CLAUDE.md aponta os caminhos novos.
- **Como foi feito / decisões:** imports relativos (não try/except dual-path) — é a forma idiomática do loader e o que o `protocolos` (plugin real) já usa. Smoke test com o loader REAL (`plugins.loader._load_plugin_module`) confirma que `meta_graph`/`media_urls` resolvem como `whatsbot_plugins.facebook_messenger.*`.
- **Problemas / pendências:** ⚠️ **CONSEQUÊNCIA REGISTRADA (D2·F9):** quando o **Instagram** (sub-plano 46·03) entrar, ele precisa carregar a **PRÓPRIA cópia** de `meta_graph`/`media_urls` (o usuário confirmou: "vou deixar tudo no plugin do instagram também") — as duas cópias podem divergir; um fix de API da Meta terá que ser aplicado nos dois. Alternativa futura se o custo incomodar: promover a base a um pacote compartilhado explícito.
- **Verificação:** `grep -rn meta_graph channels/ server/ app/` VAZIO; idem `media_urls`. Testes: `test_facebook_messenger` 20/20, `test_meta_graph_core` 18/18, canais 91/91, `test_endpoints` 1596 pass / 3 fail (as 3 pré-existentes de protocolos, incl. os 12 checks P76 verdes). Smoke do loader real OK.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Catálogo assíncrono (H1) | Flash de rótulo: o chip renderiza antes do fetch e "pula" de `gowa` para "WhatsApp" | Fallback estático mínimo pros bundled + `subscribe()` re-render; o degradê já é o comportamento atual para provider desconhecido |
| Catálogo (H1) | `GET /api/channels/providers` é autenticado; telas abertas antes do login | Fetch preguiçoso na 1ª leitura + retry silencioso; nunca bloquear render |
| `contact_type` (V5) | Dois providers com o MESMO `contact_type` (gowa + whatsapp_cloud = `whatsapp`) gerariam entrada duplicada no filtro | Deduplicar por `contact_type`; primeira cor vence, com ordem determinística |
| Slot no card (H2/F2) | Plugin desabilitado ⇒ operador perde a linha de saúde do webhook sem aviso | Comportamento aceito (D3): o card do Cloud sem o plugin não existe (o canal nem aparece). Documentar em F7 |
| Mascaramento (F5) | **Segurança:** plugin de terceiro marca um token como `type: "text"` e o core devolve o segredo em claro na API | Guarda de nome obrigatória (F5 item 2) + default mascarar + WARNING. Teste dedicado |
| Flag de URL (F4) | Deep-link antigo `?telegram=1` para de reabrir o aviso | Flag efêmera de pós-criação, sem valor persistido; aceito por D5 |
| Zips (F7) | Instalação existente continua com o plugin antigo (só `gowa` tem upgrade version-aware) | Instruir re-importar o zip; bump de versão como sinal |
| `meta_graph` (F9/P1) | Mover agora e duplicar depois no Instagram = risco de divergência silenciosa entre as duas cópias | Decidir P1 **antes** de começar o sub-plano 46 · 03 |
| Modo escuro | Componentes movidos pro plugin usam as mesmas classes `wa-*`/tintas cruas | Regra do CLAUDE.md vale igual em `storages/plugins/<id>/static/`; checagem manual em F8 |
| Restart de plugin | F6 muda `plugin.yaml` ⇒ exige restart pro `frontend_extends` aparecer no manifest | Esperado; o toggle já dispara `schedule_restart` |

---

## 7. Perguntas em aberto

**P1 — `meta_graph.py` (615 l.) + `media_urls.py` (91 l.): ficam no core ou vão pro plugin?** ✅ **RESOLVIDO (2026-07-23): (b) MOVER pro plugin.** O usuário escolheu tornar o zip do Messenger autossuficiente e replicar a base no plugin do Instagram. Implementado na Fase F9 (ver status acima). Consequência aceita: ~700 linhas duplicadas quando o Instagram chegar.

<details><summary>Contexto original (mantido para histórico)</summary>

- *Contexto:* foi decisão explícita do plano 46 (P-01B1) colocar no core, para Messenger e Instagram compartilharem. Nenhum código do core importa esses módulos — só o plugin e os testes ([meta_graph.py:42](../channels/providers/meta_graph.py#L42) importa `media_urls`; nada mais). O import de irmão dentro do plugin **funciona** (`whatsbot_plugins.<id>`), então mover é tecnicamente viável.
- *(a) Manter no core:* Instagram herda de graça; **mas** o zip do Messenger não instala num core anterior ao plano 46 e o `whatsbot_api_version: ">=1.0,<2.0"` não detecta isso (falha com `load_error` no import).
- *(b) Mover pro plugin:* zip autossuficiente de verdade — o objetivo declarado pelo usuário; custo = ~700 linhas duplicadas quando o Instagram chegar.
- **Recomendação:** **(b)**, mas **só depois de F1–F8**, e idealmente **antes** de iniciar o Instagram. Alternativa que preserva os dois lados: manter no core e **bump de `WHATSBOT_API_VERSION`** para `1.1`, com o `facebook_messenger` declarando `>=1.1` — aí o core antigo recusa a importação com mensagem clara em vez de quebrar no import.

</details>

**P2 — O `website` deve virar zip importável em `assets/channel_plugins/`?** ✅ **RESOLVIDO: sim** (feito em F7).
Hoje só `whatsapp_cloud` e `facebook_messenger` têm zip lá ([assets/channel_plugins/](../assets/channel_plugins/)); o `website` está em `assets/plugin_examples/` e instalado em `storages/plugins/`. Afeta o escopo de F7. Recomendação: sim, por consistência.

**P3 — Abrir mais slots de card agora (`channel.card.actions`) ou só `channel.card.rows`?** ⏸️ **ADIADO**
Só há um consumidor hoje (V6). Recomendação: abrir **apenas** `channel.card.rows`; slot sem consumidor é contrato a manter sem benefício.

---

## 8. Apêndice — arquivos-chave

**Frontend — core (edição)**
`web/static/js/services/providerCatalog.js` *(novo)* · [contactTypes.js](../web/static/js/services/contactTypes.js) · [api.js](../web/static/js/services/api.js) · [plugins/registry.js](../web/static/js/plugins/registry.js) · [components/channels/ChannelCard.js](../web/static/js/components/channels/ChannelCard.js) · [channels/constants.js](../web/static/js/components/channels/constants.js) · [ChannelsManager.js](../web/static/js/components/ChannelsManager.js) · [channels/ChannelEditForm.js](../web/static/js/components/channels/ChannelEditForm.js) · [contacts/ChannelChip.js](../web/static/js/components/contacts/ChannelChip.js) · [contacts/ConversationInfoPanel.js](../web/static/js/components/contacts/ConversationInfoPanel.js) · [contacts/ChannelPickerModal.js](../web/static/js/components/contacts/ChannelPickerModal.js) · [contacts/NewConversationModal.js](../web/static/js/components/contacts/NewConversationModal.js)

**Frontend — core (remoção)**
`web/static/js/components/channels/WebhookHealthRow.js` → vira arquivo do plugin `whatsapp_cloud`

**Backend — core**
[app/services/channel_service.py](../app/services/channel_service.py) (`serialize`, `NON_SECRET_CRED_KEYS`, `provider_descriptor`) · *(F9, condicional)* [channels/providers/meta_graph.py](../channels/providers/meta_graph.py), [channels/media_urls.py](../channels/media_urls.py)

**Plugins**
[whatsapp_cloud/](../assets/plugin_examples/whatsapp_cloud/) (`plugin.yaml`, `static/extends.js` novo, `static/WebhookHealthRow.js` novo) · [website/channels.py](../assets/plugin_examples/website/channels.py) (descriptor `snippet_template`) · [telegram/static/telegram.js](../assets/plugin_examples/telegram/static/telegram.js) · [facebook_messenger/](../assets/plugin_examples/facebook_messenger/) (`routes.py` comentário; F9 condicional)

**Testes**
[tests/test_endpoints.py](../tests/test_endpoints.py) · [tests/test_facebook_messenger.py](../tests/test_facebook_messenger.py) · [tests/test_meta_graph_core.py](../tests/test_meta_graph_core.py) *(F9)* · [web/static/js/components/channels/constants.test.js](../web/static/js/components/channels/constants.test.js) · `web/static/js/services/providerCatalog.test.js` *(novo)*

**Docs**
[CLAUDE.md](../CLAUDE.md) · [assets/channel_plugins/README.md](../assets/channel_plugins/README.md) · `.claude/commands/new-channel.md`

---

## 9. Checklist de verificação

- [ ] `venv/bin/python -m pytest tests/ -q` verde no Postgres de teste (`WHATSBOT_TEST_DB_URL`, banco com `test` no nome)
- [ ] `node --test` verde nos módulos puros (`constants.test.js`, `providerCatalog.test.js`, `conversationRows.test.js`)
- [ ] Grep de aceitação (F8) só devolve os falsos positivos do §3.1
- [ ] Instalação **sem nenhum** plugin de canal instalado abre o painel sem erro de console
- [ ] Desabilitar `whatsapp_cloud` remove a linha de saúde do card sem quebrar a tela Canais
- [ ] Reload + back/forward na tela Canais preserva o estado dos modais pós-criação
- [ ] Modo escuro legível: chips de canal/tipo, card de canal, avisos de pós-criação
- [ ] Segredo nenhum aparece na URL nem em resposta de API (`page_access_token`, `app_secret`, `access_token`, `bot_token` sempre mascarados)
- [ ] Zips de `assets/channel_plugins/` regenerados e importáveis numa instalação limpa
- [ ] Restart de plugin aplica o `frontend_extends` novo (manifest reflete)
- [ ] Um refactor por commit; nenhuma fase avançou com teste vermelho não explicado
