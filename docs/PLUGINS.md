# Sistema de plugins — arquitetura, regras e superfícies

> Guia do sistema de plugins do core. O [`CLAUDE.md`](../CLAUDE.md) carrega a **regra curta** e os avisos ⚠️;
> aqui está o **porquê**, o histórico e o detalhe. Texto migrado do `CLAUDE.md` no plano 139
> — nada foi reescrito na migração, só realocado.

Para **criar** um plugin use o comando `/new-plugin`; para um provider de canal, `/new-channel`.
O barramento de eventos/filtros tem guia próprio: [PLUGIN_BUS.md](PLUGIN_BUS.md).

---

## Sistema de plugins

Plugins são extensões opcionais isoladas em `storages/plugins/<id>/` (volume Docker / pasta separada no Windows, ignorada por updates). Um plugin pode agregar:

- **Tools** para o agente LLM (registradas no mesmo registry das tools core)
- **Prompt fragments** injetados dinamicamente no system prompt
- **Endpoints REST** sob `/api/plugins/<id>/...`
- **Tela Preact** carregada via `import()` ES dinâmico
- **Migrations SQL** com prefixo `plugin_<id>_` obrigatório
- **Settings declarativas** via Pydantic (form auto-gerado pela UI)
- **Broadcast WebSocket** via `from plugins.context import broadcast; broadcast("evento", {...})` — thread-safe, fire-and-forget, ws_manager + loop são injetados no startup do server. Use pra empurrar atualizações em tempo real à tela do plugin (a tela escuta `/ws` e filtra pelo nome do evento).

### Layout de um plugin

```
storages/plugins/<id>/
├── plugin.yaml              # manifest (id, name, version, whatsbot_api_version, entry, screens)
├── __init__.py
├── tools.py                 # CORE_TOOLS = [(schema, executor), ...]   (opcional)
├── prompts.py               # PROMPT_FRAGMENTS = [callable, ...]        (opcional)
├── routes.py                # router = APIRouter()                       (opcional)
├── settings.py              # class Settings(BaseModel) — Pydantic       (opcional)
│                            #   (config do plugin: settings OU screen config:true)
├── migrations/
│   └── 001_initial.sql      # tabelas com prefixo plugin_<id>_
└── static/
    └── <id>.js              # default-export componente Preact
```

### Lifecycle

1. **Bootstrap**: com `storages/plugins/` vazio, `plugins.bootstrap.bootstrap_initial_plugins()` semeia **somente `gowa`** a partir de `assets/plugin_examples/gowa/` (`BUNDLED_AUTO_INSTALL`) — que é o **único** diretório ali. Qualquer outro plugin entra por `Importar (.zip)`.
2. **Discovery**: `discover_and_load(plugins_dir)` escaneia o filesystem, parseia cada manifest, faz `upsert` na tabela `plugins`.
3. **Migrations**: para plugins com `enabled=1`, `run_pending_migrations` aplica SQL files em ordem numérica. Naming `NNN_descricao.sql`. O migrator valida regex que toda `CREATE TABLE`/`ALTER TABLE`/`CREATE INDEX` use prefixo `plugin_<id>_`.
4. **Import**: `importlib.spec_from_file_location` registra o pacote como `whatsbot_plugins.<id>`. Submódulos declarados no `entry:` são importados sob demanda.
5. **Wiring**: `agent_handler.register_plugin_tools/prompts` adicionam ao registry. `app.include_router` monta o router em `/api/plugins/<id>`. `app.mount` serve `static/` em `/plugins/<id>/static`. `screens[].path` é registrado como rota SPA dinâmica.
6. **Toggle**: enable/disable atualiza a tabela `plugins` e dispara `schedule_restart` (`os._exit(0)` após delay; supervisor relança — Coolify/Docker `restart: unless-stopped` ou launcher do EXE).

### Settings declarativas (Pydantic Valves)

Plugin declara `class Settings(BaseModel)` em `settings.py`. O endpoint `GET /api/plugins/<id>/settings` retorna `model_json_schema()` + valores atuais; `PUT` valida via Pydantic e persiste em `config_repo` com prefixo `plugin.<id>.<field>`. Frontend (`PluginSettingsForm.js`) renderiza form genérico para string/int/float/bool/enum.

### O que fica no core e o que vai pro plugin (REGRA DE DECISÃO)

**Tudo que puder ir para o plugin vai SÓ para o plugin, com o mínimo possível no core.** Um recurso só merece **ramo, evento ou campo novo no core** quando os **três** critérios forem verdade ao mesmo tempo:

1. **≥ 2 consumidores previstos** — reais, não hipotéticos;
2. **nenhum gancho existente enxerga o sinal**;
3. **usar o gancho existente custaria caro no caminho quente**.

Falhando **qualquer um**, o comportamento de negócio vai inteiro para o plugin — mesmo que o gancho disponível seja mais tosco que o ramo que você desenharia. A exceção é a **fronteira de confiança**: identidade/resolução da rota, veredito de assinatura e autorização permanecem no core e entregam ao plugin apenas o contexto já validado. Precedente conta como evidência: se outro plugin já resolve o mesmo problema pelo mesmo gancho em produção, o gancho está provado e o ônus da prova é de quem quer o ramo no core.

**Por que a regra existe (não é estética):** reduzir seams reduz acoplamento e ordem de deploy, mas não autoriza esconder dependências. A fronteira de confiança (identidade da rota, veredito de assinatura, autorização) continua no core mesmo com um único consumidor de negócio: plugin não deve autenticar a si próprio a partir de uma rota pública. Critério de aceite: **o plugin carrega num core da release anterior; qualquer feature que exija seam novo degrada explicitamente e documenta “core antes do zip”**. No plano 84, por exemplo, o alerta de webhook da Meta degrada fechado sem `ctx.extras`, enquanto polling e `message.failed` continuam funcionando.

**Casos que fixaram a regra:** plano 84 (o motor dos avisos da conta Meta cabe em `filter.webhook.payload`; o ramo por `kind` foi revertido, e ficou no core só o seam genérico de procedência/autenticação necessário para o filtro não confiar em uma rota pública); plano 76 · F9 (`MetaGraphChannel` desceu para dentro do plugin, e o Instagram carrega a **própria cópia** em vez de importar a do Messenger — *dois canais Meta, duas cópias, preço do zip autossuficiente*); plano 92 · B1 (o modal "Enviar template" virou do plugin via `overrideComponent`).

⚠️ **"Não muda o core" ≠ "não depende do core".** Um plugin extraído continua importando `db.repositories`, `plugins.context`, `runtime.supervisor`, `server.message_errors` — e **nenhum desses é API declarada**. A superfície declarada (catálogos do bus, `plugins.context`, schema do manifest + `entry`, `channels.base`/`channels.events`, convenções de host) é a versionada por `WHATSBOT_API_VERSION` e travada por [tests/contracts/test_plugin_api_surface.py](../tests/contracts/test_plugin_api_surface.py) — ver "Versionamento da API de plugins"; `db.repositories` e companhia ficam **de fora de propósito** (snapshotar a camada de dados inteira tornaria o número inútil por ruído). Até 2026-08-11 a constante esteve congelada em `1.0.0` e o guard nunca rejeitou nada; da `1.1.0` em diante ela anda, mas isso **não** promove esses módulos a API. Por isso **todo import além do mínimo continua defensivo**: `try/except` que degrada em vez de quebrar. Import não-defensivo de módulo que mudou = o plugin **nem carrega**, falha muda no boot.

**Contrato do observador de `filter.webhook.payload`** (o gancho que torna plugin de canal viável fora do core — mesmo padrão em `janela_72h`, `debug_bus` e `whatsapp_cloud`):

- **devolver `None` DESCARTA a mensagem inbound** — o core responde 200 sem processar; um observador que erre isso derruba a caixa de entrada;
- **`ctx.extras` traz procedência resolvida**: `{provider, channel_id, signature_authenticated}`. O core só chama o filtro depois de confirmar que o canal existe, pertence ao provider da rota, está materializado no registry e passou o veredito atômico `verify_inbound_signature_result`; para GOWA multi-device, `channel_id` já é o device/canal final mesmo se a antiga URL `default` sumiu. `signature_authenticated=True` só quando o MESMO snapshot que aceitou o corpo também confirmou o HMAC — hoje, no WhatsApp Cloud; aceitar por compatibilidade não conta como autenticação;
- observador com efeito externo deve validar de novo provider/canal e casar a identidade do payload (`entry[].id`/WABA id, `page_id`, `bot_id`) com a credencial **exata daquele canal**. Sem contexto/assinatura/identidade, **fail-closed**; nunca usar fallback “único canal” numa rota pública;
- **prioridade: número MENOR roda ANTES** — observador usa 9000 para nunca disputar com filtro que de fato transforma;
- roda para **todos** os providers em **todo** inbound (call site único) — o guard tem de sair na **primeira comparação**;
- trabalho de banco/rede é **offloaded** para fora do request (`loop.create_task` guardando **referência forte** da task).

Um plugin autônomo persiste estado em `plugin_<id>_*` (nunca em memória — o toggle derruba o processo), agenda com `ctx.spawn_task` + `RestartPolicy.PERMANENT`, e nasce com agregação/cooldown se emite alerta. E leva **ao menos um teste que sobe o app pelo loader real e bate no endpoint real**: teste que carrega o módulo por caminho continua **verde com a costura arrancada**.

Ver [docs-planos/100-plano-devolver-ao-plugin-o-que-e-do-plugin.md](../docs-planos/100-plano-devolver-ao-plugin-o-que-e-do-plugin.md) — inclui a lista dos candidatos já auditados e **refutados** (parecem acoplamento e não são).

### Onde fica a configuração de um plugin (REGRA)

**Toda configuração de um plugin vive na aba de configuração DO PRÓPRIO plugin** — o botão **Configurar** no card em *Gerenciar Plugins* (`/plugins`). **Nunca** adicione uma seção/aba nova ao painel de Configurações padrão do WhatsBot ([web/static/js/components/ConfigPanel.js](../web/static/js/components/ConfigPanel.js)) para algo que pertence a um plugin. O core não deve crescer com opções de plugin.

Há dois jeitos (escolha um, ou combine) de preencher o modal "Configurar":

1. **Settings declarativas** (`settings.py` → `class Settings(BaseModel)`): form auto-gerado pelo `PluginSettingsForm`. Use quando as opções são campos simples (string/int/float/bool/enum) persistidos no servidor (`plugin.<id>.<field>`).
2. **Tela de configuração custom** (`screen` com `config: true`): um componente Preact próprio, renderizado dentro do mesmo modal "Configurar" via `PluginScreen`. Use quando precisa de UI rica (toggles que aplicam na hora, preferências em `localStorage` per-device, upload, preview de som, etc.). Quando o plugin tem uma screen `config: true`, o modal renderiza ela **no lugar** do form declarativo.

Referências: `auto_signature` (settings declarativas, na Loja de Plugins — repo *community*) e as screens `config: true` de `gowa`/`telegram`/`whatsapp_cloud`, que combinam as duas coisas.

**Largura do modal: o plugin DECLARA, o core traduz** (API 1.4.0). O modal "Configurar" é do core ([PluginsManager.js](../web/static/js/components/PluginsManager.js)), então uma screen não escapa da largura dele por dentro — `max-width` é restrição do pai. Uma screen `config: true` declara `screens[].width` no manifest: `normal` (default, `max-w-2xl`) · `wide` (`max-w-6xl`) · `full` (`max-w-[95vw]`). O core apenas **avalia**, por um mapa fechado em `configModalWidth()`: valor desconhecido cai no default e a string do manifest **nunca** é interpolada numa classe (senão um plugin injetaria CSS arbitrário no painel). Mesmo padrão de `MediaLimits`/`TemplateSpec` — sem `if plugin_id ==`. O modal é `flex flex-col` com cabeçalho `shrink-0` e corpo `flex-1 overflow-y-auto`, então `sticky top-0`/`sticky bottom-0` DENTRO da screen grudam no scrollport do corpo (é como o `protocolos` fixa a tira de abas e a barra de Salvar). ⚠️ **Não declare `">=1.4,<2.0"` só por causa disto**: a chave degrada sozinha (o dict de screen em [manifest.py](../plugins/manifest.py) é uma whitelist, e um core anterior a descarta → modal no tamanho de sempre). Escolha pelo conteúdo: `wide` para grade de 2 colunas ou construtor de regras, `normal` para uma configuração de meia dúzia de campos. Quem usa hoje: `protocolos` (`wide`).

⚠️ **`custom_sounds` e `notifications` NÃO são mais plugins** (medido em 2026-07-31): o subsistema de som foi absorvido pelo core na direção CONTRÁRIA (plugin → core) e hoje vive em [server/sound_catalog.py](../server/sound_catalog.py), [server/routes/sound_prefs.py](../server/routes/sound_prefs.py), [db/repositories/custom_sound_repo.py](../db/repositories/custom_sound_repo.py), a tabela `custom_sounds` e o componente core [SoundSettings.js](../web/static/js/components/SoundSettings.js). Nenhum dos dois está instalado em `storages/plugins/`.

### Frontend dinâmico

`/api/plugins/manifest` retorna apenas plugins carregados com seus `screens[]`. `app.js` faz fetch no boot e separa as screens por flag `config`:

- **`config: false`** (default) — screen "de funcionalidade": aparece como página no `GearMenu` (menu da engrenagem) e é renderizada full-page via `PluginScreen`. Ex: uma tela de listagem/operação do plugin.
- **`config: true`** — screen "de configuração": **filtrada fora do GearMenu** (`app.js` faz `.filter(s => !s.config)`) e renderizada dentro do modal **Configurar** do card em `/plugins` (`PluginsManager.js`). É a aba de configuração do próprio plugin.

`PluginScreen` faz `import(screen.component)` dinâmico e passa `apiBase = "/api/plugins/<id>"` como prop. Importmap em `web/index.html` cobre `preact`, `preact/hooks`, `htm` — plugin usa os mesmos sem bundle. Screen custom pode importar utilitários do core por URL absoluta (ex: `import { playNotificationSound } from '/static/js/utils/notifications.js'`).

🚫 **Tela de plugin NUNCA abre `new WebSocket('/ws')`** (plano 107). O socket cru não leva o `?token=` e o servidor o fecha com **4401** assim que existe ≥1 usuário ([websocket.py](../server/routes/websocket.py) — o gate do plano 48 F0). É uma falha **silenciosa e permanente**: sem `onerror`/`onclose` nada é logado, e a tela simplesmente para de atualizar sozinha (foi assim que `protocolos`, `agendamento_retorno`, `lembretes` e a tela core `/tools` passaram meses sem tempo real, cada um com o mesmo bug). O transporte é sempre o **barramento único e autenticado** do core — `api.services.subscribe(handlers)` (plugin services **≥ 2.1**) ou, equivalente, `import { subscribe } from '/static/js/services/wsBus.js'`. Ele entrega **qualquer** nome de evento, inclusive o `plugin_<id>_*` que o próprio plugin emite pelo `plugins.context.broadcast` — ao contrário do `api.services.useWebSocket`, cujo mapa de eventos é fixo nos nomes do CORE. Devolve a função de unsubscribe; o efeito a retorna direto. ⚠️ Se o handler dispara refetch caro, ponha **debounce com jitter**: um evento costuma significar cache invalidado em todas as réplicas, e N operadores recarregando no mesmo instante trocam um bug de UX por um de carga.

Um `frontend_extends` recebe `buildPluginApi(id)` e negocia duas superfícies separadas no manifest: `frontend_api_version` (registry/slots/overrides) e `plugin_services_version` (`api.services`). A allowlist atual de serviços é 2.x (2.1 acrescentou `subscribe`); manifest legado sem o segundo campo recebe o adapter 1.x. O objeto expõe `api.pluginServicesVersion` (superfície negociada) e `api.pluginServicesHostVersion` (mais nova do host); ainda assim, faça feature detection da função antes de chamar. Range incompatível ou malformado faz o core pular o módulo (fail-closed). O parser de frontend aceita `*`, comparadores AND (`>=2.0,<3.0`), `^` e `~`; uma declaração numérica como `"2.0"` significa compatibilidade por MAJOR, não igualdade exata, e `||` não é aceito.

### Override de componente (plano 92 · B1)

Terceira semântica do registry, ao lado de **slots** (aditivos) e **route override** (exclusivo): `overrideComponent(name, C)` ([registry.js](../web/static/js/plugins/registry.js)) deixa um plugin **substituir uma peça de UI que não é rota**. Contrato igual ao `overrideRoute` — **primeiro que registra ganha**, reivindicação posterior é logada e ignorada (nunca silenciosa). Use quando a tela inteira pertence ao domínio do plugin; um slot resolve quando é só acrescentar.

O core renderiza um **Host** que resolve o override e cai no próprio componente enquanto existir fallback — nenhum arquivo do core sabe qual plugin reivindicou o quê. O Host **congela o componente na montagem** (só a transição "nada → algo" é aceita): re-resolver a cada `bump()` do registry trocaria o tipo do vnode com o modal aberto e descartaria o formulário do operador, porque `loadPluginExtensions` limpa o registry de forma síncrona a cada toggle de plugin.

| Nome | Host | Dono hoje | ctx |
|---|---|---|---|
| `template.picker` | [TemplatePickerHost.js](../web/static/js/components/contacts/TemplatePickerHost.js) | `whatsapp_cloud` | `{conversationId, channelId, phone, onClose, onSent}` |

**O modal "Enviar template" é do plugin.** O formato de um template é ditado pela API do provedor, então quem o desenha é o plugin de canal — o `static/TemplatePicker.js` do `whatsapp_cloud` (fonte no repositório de plugins), com favoritos por usuário, arquivar global (permissão `plugin.whatsapp_cloud.template_archive`, que **nasce sem dono**) e busca por conteúdo. A cópia do core ([TemplatePicker.js](../web/static/js/components/contacts/TemplatePicker.js)) está **congelada** como fallback de transição e some na release seguinte — as duas já divergiram de propósito, **não corrija bug nela**. Quem chama o picker deve gatear com `templatePickerAvailable()` (sem plugin e sem fallback, o botão não aparece e o aviso de 24h degrada para texto).

O vocabulário da Meta que o core carregava (categorias, formatos de cabeçalho, tipos/limites de botão, MIMEs de upload) virou `TemplateSpec` em [channels/base.py](../channels/base.py), declarado pelo provider em `ChannelCapabilities.template_spec` e apenas **avaliado** pelo core ([template_service.py](../app/services/template_service.py) `spec_for`) — mesmo padrão de `MediaLimits`/`VideoLimits`.

### Convenções obrigatórias

- **`id`**: snake_case, regex `^[a-z][a-z0-9_]{0,31}$`. Vira o prefixo de tabela e o nome do pacote Python.
- **Tabelas**: SEMPRE `plugin_<id>_<nome>`. O migrator rejeita o contrário com erro claro.
- **`whatsbot_api_version`**: range semver no manifest. **Use sempre comparadores** (`">=1.0,<2.0"`) — o parser do backend ([plugins/semver.py](../plugins/semver.py)) REJEITA `"1.1"`, `"^1.1"`, `"~1.1"` e `"1"`, e range rejeitado significa plugin que **não carrega**; o do frontend aceita essas formas, então não copie a sintaxe de um campo para o outro. Versão atual em [plugins/semver.py](../plugins/semver.py) (`plugins.manifest` é re-export) — ver "Versionamento da API de plugins".
- **`plugin_services_version`**: obrigatório em plugin novo que declare `frontend_extends` (use `">=2.1,<3.0"` hoje, se depender do `subscribe`; `">=2.0,<3.0"` continua válido). Omissão significa legado 1.x; é independente de `frontend_api_version`.
- **`entry.services` / `uses_services`**: a API interna plugin→plugin (ver a seção própria). **Nunca** exponha essa superfície por HTTP; `services.py` do provedor é FOLHA; consumidor importa `plugins.services` de forma defensiva. ⚠️ `uses_services` é INDEPENDENTE de `plugin_services_version` (que é frontend) — a colisão de nome é a armadilha aqui.
- **Tempo real**: `api.services.subscribe` / `wsBus`, **nunca** `new WebSocket('/ws')` — ver "Frontend dinâmico".
- **Permissions**: declaradas no manifest mas **não enforced no MVP** — informativo apenas.
- **Configuração no próprio plugin**: opções de um plugin vão SEMPRE na aba de configuração dele (settings declarativas e/ou screen `config: true`), NUNCA numa aba nova do painel de Configurações do core. Ver "Onde fica a configuração de um plugin".
- **Settings**: chaves persistem com prefixo `plugin.<id>.`. Plugin nunca grava direto na tabela `config` sem esse prefixo.
- **Cores / modo escuro**: a tela do plugin (`static/<id>.js`) DEVE ser legível no tema escuro. Use as classes semânticas `wa-*` (`bg-wa-bg`, `bg-wa-panel`, `text-wa-text`, `border-wa-border`, …) e `.wa-field` em inputs. Cores cruas (`bg-white`, `bg-green-50`, …) têm fallback no `custom.css`, mas hex inline e cores fora da lista coberta NÃO — teste com o modo escuro ligado. Ver "Tema e modo escuro (legibilidade)".
- **Auditoria**: toda rota do plugin que MUDA configuração ou estado com dono chama `plugins.context.audit(...)`. O guia é [docs/PLUGINS_AUDITAVEIS.md](../docs/PLUGINS_AUDITAVEIS.md).
  - ⚠️ **Auditar a configuração e não auditar a ação de estado é o defeito mais comum do parque** — não é falta de mecanismo, é falta de cobertura. A varredura do plano 148 achou 32 lacunas em 11 plugins e **nenhuma** era "o plugin não sabe auditar": o mesmo `routes.py` que registra o `PUT /config` deixa passar a rota que fecha o atendimento, apaga o agendamento ou exclui a visualização de equipe. O caso que fixou a regra é o `protocolos`, onde a **IA** fechando um atendimento deixa rastro (`actor_type="ai"`, 19 linhas em produção) e **o atendente humano fazendo o mesmo gesto, não** — a rota de IA foi escrita depois, com o seam à mão, e a humana ficou como estava.
  - O teste é simples e vale para todo plugin novo: liste os verbos da sua tela. Se um deles **fecha, atribui, aprova, reordena, apaga ou dispara efeito externo** e não tem `audit(...)`, a lacuna é essa. Configuração é a parte fácil — quem lê a trilha três meses depois quer saber quem fechou, não quem salvou.

### RBAC de plugins

Um plugin declara permissões de usuário no bloco `rbac:` do `plugin.yaml` (distinto do `permissions:` de capability `llm.tool`/`db.write`). Cada permissão vira a chave `plugin.<id>.<key>` registrada (upsert) na tabela `permissions` no load do plugin ([plugins/rbac.py](../plugins/rbac.py)), aparecendo no `PermissionPicker` na área **Plugins** agrupada por `rbac.group` (default = nome do plugin) **enquanto o plugin estiver ativo** (desativar esconde do picker; ver "Disable" abaixo). Convenção forte de chaves: `view`/`edit`/`delete` (chaves livres são aceitas — regex `^[a-z][a-z0-9_.]{0,48}$`).

```yaml
rbac:
  group: "Lembretes"          # opcional; default = name do plugin
  permissions:
    - { key: view,   label: "Ver lembretes" }
    - { key: delete, label: "Excluir lembretes" }
```

- **Enforce nas rotas** com a dependency `plugin_permission("<key>")` ([plugins/context.py](../plugins/context.py)): infere o `<id>` do path `/api/plugins/<id>/...`, monta `plugin.<id>.<key>` e retorna 403 quando o usuário logado não tem a permissão. **Default-allow** quando open (sem identidade de usuário, instalação sem admin ainda) — não quebra o modo aberto. Nunca cheque permissão na mão; use a dependency.
  ```python
  from plugins.context import plugin_permission
  @router.delete("/items/{id}", dependencies=[plugin_permission("delete")])
  async def delete_item(id: int): ...
  ```
- **Esconda a screen** sem permissão com `requires: <key>` no manifest da screen (`screens[].requires`) — o GearMenu filtra (padrão "hide, don't disable"). O componente da screen recebe a prop `can(key)` (= `hasPermission(user, 'plugin.<id>.<key>')`).
- **Decisão central**: [server/authz.py](../server/authz.py) `check()`/`acheck()` resolvem RBAC e então aplicam o seam ABAC `filter.authz.decision` (`{user, permission_key, allow}` → pode rebaixar allow→deny). **Nenhum avaliador é embarcado no core (v1)** — regras por atributo (ex: horário) viram um plugin de filtro depois, sem tocar nos call sites.
- **Catálogo**: `rbac_repo.list_catalog()` = core (`PERMISSION_CATALOG` estático, com `tier`/`group` de exibição via `domain.permission_catalog.PERMISSION_GROUPS`) + linhas de plugins **ATIVOS** (`plugin_id IS NOT NULL` ∧ `plugins.enabled=1`). O `PermissionPicker` renderiza dois tiers (**Sistema** × **Plugins**). `/api/roles` e a validação de criação de role/usuário usam o catálogo/keys efetivos.
- **Disable** mantém as linhas mas as **ESCONDE do picker** (`list_catalog` filtra por plugin ativo); os grants sobrevivem ao toggle e voltam a aparecer ao reativar. Para não perder um grant escondido ao editar cargo/usuário com o plugin off, `_replace_role_permissions`/`set_custom_permissions` **preservam** as chaves em `hidden_plugin_permission_keys()`. **Delete** do plugin remove `WHERE plugin_id = <id>` (grants em `role_permissions`/`user_permissions` caem por FK cascade).

### API interna plugin→plugin (`entry.services`) — nunca HTTP

Terceiro canal entre plugins, ao lado do **barramento** (broadcast, "aconteceu algo") e dos **filtros** (interceptivo, "reescreva este valor"): **request/response**. Um plugin publica uma superfície nomeada de operações e outro chama e LÊ a resposta. Motor em [plugins/services.py](../plugins/services.py) — irmão Python do seam que o frontend já tem em [web/static/js/plugins/api.js](../web/static/js/plugins/api.js) (`buildPluginApi`), com allowlist e negociação de versão.

- **Provedor**: exporta `SERVICES = {"op": callable, ...}` (opcionalmente `SERVICES_VERSION` e `SERVICES_ALLOW`) do módulo declarado em `entry.services` do manifesto. A versão mora no CÓDIGO (`SERVICES_VERSION`), não no manifesto — código e versão no mesmo arquivo, sem drift. ⚠️ **Não confundir com `plugin_services_version`**, que é a superfície de FRONTEND (`api.services`) e não tem relação nenhuma com este campo.
- **Consumidor**: declara `uses_services: [{plugin: <id>, version: ">=1.0,<2.0"}]` no manifesto e chama `services.call("<id>", "op", _as="<meu_id>", **kwargs)`. O range do manifesto é o default das chamadas feitas com `_as`.
- **Envelope**: toda chamada devolve um `ServiceResult` — **dispatch NUNCA levanta**. Status: `ok` · `unavailable` (plugin ausente/sem superfície/bloqueado por allowlist) · `unknown_op` · `incompatible` (range) · `disabled` (carregado mas desligado — a op levanta `ServiceDisabled`) · `wrong_context` (op async chamada de forma síncrona NA THREAD DO LOOP) · `error` (a implementação levantou).
- **`get()` é null-object**: nunca devolve `None`. Feature detection é `if services.get("trackify"):`; um proxy indisponível é falsy e o `.call()` dele ainda responde com o status certo, em vez de `AttributeError`. Ops **não** viram atributos do proxy de propósito.
- **Sync/async**: `await proxy.acall()` roda impl sync em `asyncio.to_thread` e impl async direto; `proxy.call()` de uma worker thread faz a ponte para o loop com `run_coroutine_threadsafe`; `proxy.call()` DA thread do loop com impl async devolve `WRONG_CONTEXT` + WARNING — nunca bloqueia o loop (mesma degradação de `apply_filter_sync`).
- **Registro em `create_app`**, antes do lifespan e do `run_setup`. Isso impõe uma linha de contrato ao provedor: **uma op não pode depender de estado criado no `setup()`** — se depender, devolve `DISABLED`/`ERROR` até ficar pronta, nunca quebra e nunca bloqueia. O desregistro é no shutdown do app (não em `plugins.lifecycle`, que sai cedo para plugin sem `entry.lifecycle`).
- **Invisível ao HTTP — o requisito central**: o módulo não importa `fastapi`, `_entry_services` nunca toca `loaded.router`, e nenhum provedor expõe `/rpc` ou `/service/{op}`. Travado por teste ([tests/contracts/test_plugin_services.py](../tests/contracts/test_plugin_services.py) e [tests/integration/test_plugin_services_wiring.py](../tests/integration/test_plugin_services_wiring.py), que compara a tabela de rotas com e sem `entry.services`).
- **`as_plugin` é FALSIFICÁVEL** (é o chamador que o informa): serve de contabilidade de raio de alcance, **não** é fronteira de segurança. A fronteira real é "nada sai do processo".
- **Auditoria é do PROVEDOR**, por operação com efeito externo (`plugins.context.audit`) — o registro não audita nada (não conhece semântica e algumas ops são de alto volume). Ver o guia [docs/PLUGINS_AUDITAVEIS.md](../docs/PLUGINS_AUDITAVEIS.md).
- **Compatibilidade com core anterior**: um core sem a linha `"services"` em `_ENTRY_SPECS` nunca consulta `entry.services` e nunca importa o módulo — por isso o `services.py` do provedor tem de ser **FOLHA** (nenhum outro módulo dele o importa; helper compartilhado vai para os módulos vizinhos). No consumidor, o import é sempre defensivo: `try: from plugins import services / except: _services = None` — import duro no topo de um módulo que o loader importa = o plugin não carrega, falha muda no boot.
- **Quem usa hoje**: `trackify` publica a superfície completa do CDP (`SERVICES_VERSION = "1.0.0"`, 18 ops: status, eventos, jornada, compras, assinaturas, identidade, campos, escrita, cadastro, consentimento) e é o **único ponto que fala com o CDP**; `protocolos` a consome para entregar `track_protocolo_*` (antes era assinatura de barramento). O `_emit_bus` do `protocolos` **continua emitindo** — o emit sobra como sinal de observabilidade para quem assina `"*"` (`debug_bus`); o que mudou foi só o caminho de ENTREGA.

### Plugin que envia sozinho: o gate da janela é DELE (plano 143)

Um plugin que chame `outbound.send_text` direto não passa por nenhuma das guardas que o painel tem. Dois plugins hoje enviam assim e **os dois pulam quando a janela está fechada** — mas por predicados **deliberadamente diferentes**, e unificá-los seria regressão nos dois sentidos:

| | `retornos` (`actions.janela_aberta`) | `protocolos` (`logic._evaluation_window_open`) |
|---|---|---|
| Predicado | **24 h fixas, uniformes em todo canal** — abandonou `session_open` de propósito | **`session_open(channel_id, last_inbound_ts, by_human=True)`** — por capability |
| Objetivo | não **incomodar** cliente frio, mesmo onde o envio funcionaria | não **produzir erro** — só faz sentido onde o provedor recusa |
| Na dúvida | **fail-closed** (o mal dele é incomodar) | **fail-open** (o mal dele seria deixar de avaliar quem podia) |
| Fora da janela | nota privada de aviso | **igual** |
| Desligável | `respeitar_janela_24h` | `respeitar_janela` + `avisar_janela_fechada` |

⚠️ **Não unifique.** A regra fixa do `retornos` aplicada à avaliação calaria os canais com `session_window_hours=0` (GOWA, Telegram, site), onde ela hoje é entregue sem uma falha sequer. A regra por capability do `protocolos` aplicada ao `retornos` voltaria a incomodar cliente frio no WhatsApp comum. Há teste dedicado a esse caso nos dois plugins.

Três detalhes que valem para qualquer plugin que copie o padrão:

- **`message_repo` não é superfície versionada** (a regra do `CLAUDE.md` é explícita: `db.repositories` fica de fora de propósito). O import é **local e defensivo**, e o gate inteiro degrada para o comportamento anterior se qualquer peça faltar — nunca calar o envio por um `AttributeError`.
- **Nota privada, nunca `role="error"`.** O ponto de pular é remover ruído vermelho do fio; trocar um card de erro por outro é não ter feito nada.
- **Gate é variável de decisão, não `return`.** Um `return` cedo mata também o que vinha depois — no `protocolos`, a nota privada do link interno, que é painel-only e nunca dependeu de janela nenhuma.

O mecanismo da janela em si (o que a Meta aceita e quando) está em [CANAIS_META.md](CANAIS_META.md).

### Onde vive o código de um plugin (NÃO confundir)

Nada sincroniza estes pontos automaticamente. Um mesmo plugin pode ter conteúdos diferentes em cada um, inclusive **com o mesmo número de versão** — já aconteceu com o `protocolos` (duas cópias distintas ambas marcadas `1.17.0`). Ao comparar versões, compare o CONTEÚDO, nunca só o número.

| # | Lugar | O que é | Como o código chega/sai |
|---|---|---|---|
| 1 | **Repositório de plugins do Pro** — [Techify-one/whatsbot-pro-plugins](https://github.com/Techify-one/whatsbot-pro-plugins) | Fonte de desenvolvimento em `plugins/<id>/src/`, testes em `plugins/<id>/tests/`, metadados e ZIP publicado no mesmo diretório | editar `src/`, testar e gerar com os scripts desse repositório; commit/push ali |
| 2 | `assets/plugin_examples/gowa/` | Fonte do **único** plugin bundled. Não há mais espelho dos outros | editado aqui; o boot copia para `storages/plugins/gowa/` (upgrade version-aware) |
| 3 | `storages/plugins/<id>/` | Cópia **instalada e rodando** (gitignored), tanto em desenvolvimento quanto em produção | `Importar (.zip)` na UI ou bootstrap do GOWA; nunca é fonte de verdade de desenvolvimento |
| 4 | `plugins/<id>/<id>.zip` no repositório externo | Artefato sem testes entregue ao cliente | gerado deterministicamente a partir de `src/`; instalação por `Importar (.zip)` |

**Cuidado com o termo "Loja de Plugins"**: ele designa EXCLUSIVAMENTE o repositório **community** [Techify-one/whatsbot-plugins](https://github.com/Techify-one/whatsbot-plugins) (publicado em https://whatsbot.techify.one/plugins) — outro repositório, outro produto. **Não** é o repositório de plugins do Pro (#3 acima). Não use "loja" para se referir ao `whatsbot-pro-plugins`.

Os plugins de exemplo abaixo vivem na **Loja de Plugins** (o repo community, não o do Pro) e são instalados via `Importar (.zip)` na tela Gerenciar Plugins: `event_logger` (assina `*`), `auto_signature` (`filter.reply.part`), `blacklist` (`filter.message.before_save` → `None`), `transcricao_grupos` (`filter.transcription.should_run` — controle de transcrição por grupo via UI + DB), `horario_funcionamento` (settings declarativas + `filter.system_prompt`/`filter.llm.tools`/`filter.llm.messages` + migrations — horário de funcionamento por dia da semana, com mensagem de ausência fora do expediente e cooldown por contato). ⚠️ `custom_sounds` e `notifications` saíram desta lista: viraram **core** (ver o aviso em "Onde fica a configuração de um plugin").

### Criar um plugin novo

Use o slash command `/new-plugin` no Claude Code. O comando lê os arquivos de referência, pergunta requisitos (id, telas, tools, tabelas, settings) e gera a estrutura de desenvolvimento em `../whatsbot-pro-plugins/plugins/<id>/`, com fonte em `src/` e testes fora do artefato. Veja `.claude/commands/new-plugin.md`.

### Importar/exportar

- Export: `GET /api/plugins/<id>/export` retorna um `.zip` da pasta (excluindo `__pycache__/` e arquivos `.db`).
- Import: `POST /api/plugins/import` (multipart) exige um único manifest na raiz, checa colisão de `id` e path traversal e extrai em `storages/plugins/<id>/`. A validação completa/compatibilidade do manifest ocorre no discovery/load; o importado fica `enabled=0` até o usuário ativar pela UI.
- Build publicado: no repositório `whatsbot-pro-plugins`, rode `python3 scripts/build_plugins.py <id>` ou `--all`; `--check` valida que cada `<id>.zip` corresponde byte a byte a `src/`. O builder rejeita testes, caches, bancos, segredos e traversal dentro da fonte instalável.
