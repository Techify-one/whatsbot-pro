# Changelog da API de plugins (`WHATSBOT_API_VERSION`)

Este arquivo versiona **só** o que um plugin pode consumir do core sem que o core
saiba dele: os catálogos de eventos/filtros, `plugins/context.py`, o schema do
manifest + os contratos de `entry`, `channels/base.py` + `channels/events.py`, e
as convenções de host (prefixo de tabela, namespace do pacote, mounts, isenção
`/public/`, chave RBAC, formato de ação de auditoria).

**Não** está aqui: `db.repositories` e os demais módulos do core que plugins
importam (dependência real, não API declarada — a proteção deles é o import
defensivo); o frontend, que tem números próprios (`FRONTEND_API_VERSION` e
`PLUGIN_SERVICES_VERSION` em `web/static/js/plugins/api.js`); e os seams que
plugins publicam entre si (`filter.retornos.*`, `protocolos.*`), versionados
pelo `version` do plugin que publica.

A superfície é travada por [tests/contracts/test_plugin_api_surface.py](../tests/contracts/test_plugin_api_surface.py)
+ `tests/goldens/plugin_api_surface.json`. Mudou a superfície ⇒ bump aqui, ou a
suíte fica vermelha.

## Como bumpar

| Nível | Gatilho |
|---|---|
| **MAJOR** | Remover/renomear nome de catálogo **com produtor vivo**; mudar o tipo do valor pipeado de um filtro ou a semântica do `None` (abortar ↔ manter); remover/renomear símbolo público, campo de dataclass de contexto/capability, chave de `entry` ou convenção de host; tornar obrigatório um campo de manifest que era opcional; tornar abstrato um método de `Channel` que tinha default. **Derruba os 36 manifests do parque de uma vez** — é tranche que republica os ZIPs com ordem de deploy documentada, não decisão de commit |
| **MINOR** | Acrescentar nome ao catálogo (**no mesmo commit do call site**), símbolo público, campo com default, chave de `entry`, campo opcional de manifest, método com default em `Channel`, capability nova; alargar o `ctx.extras` de um filtro; ampliar o conjunto de situações em que um evento existente é emitido |
| **PATCH** | Correção que não muda a forma da superfície (bug em `apply_filter`, mensagem de log, docstring); retirar nome de catálogo **sem produtor vivo** no core suportado — exige varredura repo-wide, entrada aqui e o teste de WARNING |

Exceção: seam listado em `EXPERIMENTAL_FILTERS` ([plugins/events.py](../plugins/events.py))
pode sair sem MAJOR — o contrato já diz que ele pode se mover até se formar.

**Sintaxe do range (armadilha):** os dois parsers divergem. O do backend
([plugins/semver.py](../plugins/semver.py)) aceita só `*`, semver de 3
componentes (**igualdade exata**) e comparadores separados por vírgula —
`"1.1"`, `"^1.1"`, `"~1.1"` e `"1"` são **rejeitados**, e um range rejeitado
significa plugin que não carrega. O do frontend aceita todas essas formas e
trata versão pura como compatibilidade por MAJOR — semântica oposta. **No
`whatsbot_api_version` use sempre comparadores:** `">=1.1,<2.0"`.

---

## 1.8.0 — 2026-08-20 · `filter.provisioning.message` — a frase do provisionamento vem junto com o número (e os dois têm rede)

**Aditiva no catálogo.** Um plugin que não registre o filtro novo não muda em
nada. Muda, sim, o comportamento do core quando nada está configurado — ver
"Reversão da 1.7.0" no fim.

### O seam

`filter.provisioning.message` — `str`. Produtor:
[`app/services/provisioning_service.py`](../app/services/provisioning_service.py)
`fetch_provision_target()` — o mesmo da 1.7.0, renomeado porque agora resolve o
PAR. `fetch_provision_number()` continua existindo como atalho para quem só quer
o destino.

Simétrico ao `filter.provisioning.number`, e existe pelo mesmo motivo que ele:
**a frase É o gatilho que o destino reconhece**. Um plugin que aponte o envio
para outro número sem poder trocar a mensagem entrega um texto que o outro lado
ignora em silêncio — o pior desfecho possível, porque nada falha visivelmente.

Ordem dentro do produtor, que importa:

1. a mensagem é resolvida **antes**, então `filter.provisioning.number` já recebe
   a frase final em `ctx.extras["message"]`;
2. o filtro de número roda e pode abortar;
3. `filter.provisioning.message` só roda **se houver destino**, e recebe o número
   já decidido em `ctx.extras["number"]`.

`ctx.extras`: `{source ∈ {"service_number", "fallback"}, number}`. `None`/`""`
**ABORTA** — `request_key` devolve `("no_message", {})` e a rota responde com erro
acionável. Nada do caminho de envio roda: mensagem vazia queimaria a única
abertura de conversa que o WhatsApp concede a um contato novo (o reach-out
timelock é por contato).

O core continua **não validando formato** e continua engolindo exceção de filtro
(quem quer fail-closed captura a própria e devolve `None`).

### Fora do catálogo, na mesma release

**`/service_number` passou a ditar a frase.** O endpoint responde
`{"ok": true, "phone": "...", "message": "..."}` e é a fonte da verdade dos dois
campos: rotacionar número ou frase é editar essa resposta, sem release e sem env
em cliente nenhum. Os campos são resolvidos de forma **independente** — uma
resposta que ainda não traga `message` continua ditando o `phone`, e a frase cai
no fallback. Antes disso o core lia só `phone` e descartava o resto.

**Precedência por campo:** `/service_number` → env
(`TECHIFY_PROVISION_NUMBER` / `TECHIFY_PROVISION_MESSAGE`, esta última é nova) →
literal em `config/settings.py` → seam de plugin (última palavra).

### Reversão da 1.7.0

O literal embutido em `TECHIFY_PROVISION_NUMBER` (`"5513981744038"`), **retirado
na 1.7.0**, voltou — e `TECHIFY_PROVISION_MESSAGE` ganhou override por env com o
literal como default. A 1.7.0 argumentou que "um número escrito no código é um
destino que ninguém escolheu"; a decisão de produto que a reverte é mais estreita
e vem da operação: sem literal, **uma queda do endpoint parava o provisionamento
de todo cliente novo** — quem acabou de conectar o QR não tem env, não tem plugin
e não teria como pedir a própria chave.

O desfecho `no_destination` **não sumiu**: continua valendo quando alguém esvazia
a env ou quando um plugin aborta o seam. O que mudou é que ele deixou de ser o
padrão de quem simplesmente não configurou nada.

### Migração

Nenhuma para plugin existente. Quem registrar o filtro novo declara
`">=1.8,<2.0"`.

## 1.7.0 — 2026-08-20 · `filter.provisioning.number` — o destino do provisionamento é plugável (e deixa de ter reserva)

**Aditiva no catálogo.** Um plugin que não registre o filtro não muda em nada.
Muda, sim, o comportamento do core quando NADA está configurado — ver "Quebra
possível" no fim.

### O seam

`filter.provisioning.number` — `str`. Produtor único:
[`app/services/provisioning_service.py`](../app/services/provisioning_service.py)
`fetch_provision_number()`, chamado pelo wizard de 1ª execução
(`POST /api/setup/request-key`) e por toda re-provisão de chave.

O core resolve o que consegue — `GET /service_number` da Techify, caindo na env
`TECHIFY_PROVISION_NUMBER` — e oferece o resultado à cadeia de filtros, que tem a
**última palavra**. `ctx.extras` traz `{source, message}`, onde `source ∈
{"service_number", "fallback"}` diz de onde veio o valor oferecido e `message` é a
frase que será enviada (`TECHIFY_PROVISION_MESSAGE`).

`None` (ou `""`) **ABORTA**: significa "não há destino", e o core **não envia
nada** — `request_key` devolve `("no_destination", {})` e a rota responde com erro
acionável. Nada do caminho de envio roda: o contato do destino não é materializado
(seria um contato fantasma de telefone vazio) e o polling da chave não é armado (o
wizard giraria até o TTL esperando uma chave que ninguém pediu).

O core **não valida o formato** do que volta: o valor vai cru para o link `wa.me` e
para o envio pelo provider, exatamente como sempre foi com o valor do
`/service_number`. Normalizar o telefone é de quem responde ao filtro.

⚠️ **Um filtro que LEVANTA não aborta** — `apply_filter` engole a exceção e o valor
segue intacto pela cadeia. Plugin que queira fail-closed precisa capturar a própria
exceção e devolver `None` (é o que o `criar_conta` faz quando não consegue ler a
configuração: mensagem de WhatsApp para o número errado não tem desfazer).

### Por que existe

O destino era imutável em runtime: vinha de um endpoint remoto ou de uma env, e
trocá-lo exigia acesso ao ambiente do container. Quem opera a ponta que *recebe* o
pedido não tinha como apontar o wizard para o próprio número. O seam é o mínimo
genérico que resolve isso sem o core conhecer plugin por nome — mesma forma de
`filter.authz.decision`: o core resolve e oferece, o plugin decide.

### Quebra possível (fora do catálogo, mas na mesma release)

`config.settings.TECHIFY_PROVISION_NUMBER` **deixou de ter o literal embutido**
(`"5513981744038"`) e nasce vazia. Um número escrito no código é um destino que
ninguém escolheu: com o campo do plugin em branco, ou com `/service_number` fora
do ar, a mensagem saía calada para a Techify. Agora "nenhum destino" é um desfecho
normal e explícito.

**Efeito:** instalação que dependia do literal — sem env e sem `/service_number`
acessível — para de provisionar sozinha e passa a exibir o aviso. Restaurar o
comportamento antigo é definir `TECHIFY_PROVISION_NUMBER` no ambiente.

### Migração

Nenhuma para plugin existente.

## 1.6.0 — 2026-08-18 · `filter.transcription.*` alcança mais situações

Aditiva por ALCANCE: nenhum nome, tipo ou semântica de `None` mudou — o mesmo
filtro passou a ser aplicado onde antes não era. Todo manifest do parque
(`">=1.0,<2.0"`) segue válido; declare `">=1.6,<2.0"` só se o seu plugin
DEPENDER de ser consultado nos call sites novos.

### O que mudou

O plano 118 deu **direções** à descrição de imagem (`image_transcription_mode` =
`received`/`sent`/`private`, como o áudio já tinha) e ligou os call sites que
existiam mas nunca descreviam imagem. Consequência para quem assina
`filter.transcription.should_run` / `filter.transcription.result`:

| `ctx.extras` | Antes | Agora |
|---|---|---|
| `media_kind="image"` com `source="operator"` | nunca ocorria | ocorre no envio do operador pelo painel |
| `media_kind="image"` com `source="echo"` | nunca ocorria | ocorre no eco do próprio celular |
| `media_kind="image"` com `source="private"` | nunca ocorria | ocorre na imagem colada como nota privada |
| imagem/documento do **sandbox** | não passava pelo filtro (chamava `describe_image`/`transcribe_document` direto) | passa pelo helper compartilhado, como qualquer canal |

O `source` documentado no CLAUDE.md listava `group_no_mention`, que **nunca teve
call site** (o ramo salva e volta); o valor real é `{batch, echo, operator,
private}`.

### O que fazer

Nada, se o seu filtro já era defensivo (o contrato sempre disse "filtre por
`media_kind`/`source` no INÍCIO do handler"). ⚠️ Um filtro que devolvia `False`
apenas para `media_kind="audio"` e deixava imagem passar agora vai ver imagem em
três situações novas — cada uma é uma chamada de visão PAGA. Quem quiser barrar
por direção pode ler o `source`; quem quiser barrar de vez, o operador desmarca a
direção no canal (Canais → editar → Transcrição de mídia).

---

## 1.5.0 — 2026-08-17 · `ChannelCapabilities.ai_window_hours`

Aditiva. Um campo **novo com default `0`** na capability e um avaliador no core
(`OutboundRouter.ai_window_open`). Provider que não declara nada não muda em
nada, e todo manifest do parque (`">=1.0,<2.0"`) segue válido.

### O problema

O painel enxergava UMA janela (`session_open`) e existem TRÊS. Nos canais Meta,
com o toggle `human_agent_tag` ligado, o compositor do atendente fica aberto por
7 dias (`human_window_hours`, tag `HUMAN_AGENT`) — enquanto o `filters.py` do
próprio plugin já calou a IA às 24h devolvendo `None` em `filter.llm.messages`.
No intervalo entre as duas, o painel continuava oferecendo os toggles "IA lê" e
"IA responde no chat" da nota privada: o atendente escrevia a instrução, o turno
era abortado antes do LLM e **nada acontecia, sem card, sem erro, sem log no
fio**. A terceira janela não é derivável das outras duas — `session_window_hours`
vale para todo mundo e `human_window_hours` só para o humano.

### O que mudou

| Símbolo | Semântica |
|---|---|
| `ChannelCapabilities.ai_window_hours` | Horas após o último inbound em que a IA do canal pode falar. `0` (default) = sem restrição |
| `OutboundRouter.ai_window_open(channel_id, last_inbound_ts)` | Avalia a capability. `0` ⇒ sempre `True`; sem inbound ⇒ `False`, mesma leitura de `session_open` |

O campo `ai_window_open` passou a sair nos payloads de conversa/contato, e o
compositor esconde os dois toggles de instrução para a IA quando ele é `False`.

### O que o provider precisa saber

- **Quem cala a IA continua sendo o plugin**, no filtro. A capability é só o que
  o painel lê para parar de OFERECER o que vai ser descartado; o core não ganhou
  nenhum poder de bloquear turno.
- **Declare condicionalmente se quiser carregar num core anterior**: passar
  `ai_window_hours=` a um `ChannelCapabilities` que não tem o campo levanta
  `TypeError` no import e o plugin **não carrega**. O idioma é o mesmo já usado
  para `media_limits`:

  ```python
  _AI_WINDOW_CAP = any(f.name == "ai_window_hours"
                       for f in dataclasses.fields(ChannelCapabilities))
  ...
  **({"ai_window_hours": 24} if _AI_WINDOW_CAP else {}),
  ```

- Consumidores hoje: `facebook_messenger` e `instagram` (24h cada). `whatsapp_cloud`
  fica em `0` de propósito — lá o turno da IA roda fora da janela e produz nota
  privada normalmente; só o envio ao cliente é recusado pela Meta.

---

## 1.4.0 — 2026-08-17 · `screens[].width` — a screen de config declara a largura do modal

Aditiva. Um campo **opcional** de manifest; screen que não o declara continua
byte-idêntica, e todo manifest do parque (`">=1.0,<2.0"`) segue válido.

### O problema

O modal **Configurar** de um plugin é do core (`PluginsManager.js`), com largura
fixa em `max-w-2xl` (~672 px) para toda screen `config: true`. Uma tela de
configuração não consegue passar disso por dentro — `max-width` é restrição do
pai, e furá-la exigiria posicionamento fixo ou margem negativa, isto é, um plugin
escapando do próprio container. O resultado era uma coluna estreita para
configurações que não são estreitas: o `protocolos` tem quatro abas, um
construtor de rótulos e um construtor de regras, tudo empilhado numa coluna só.

As duas saídas ruins eram alargar o modal para **todos** os plugins (telas
estreitas passariam a nadar num modal largo) ou deixar o plugin hackear o layout.

### O que mudou

- `screens[].width` no manifest — `normal` (default) · `wide` · `full`. Vale só
  para screen `config: true`; screen de funcionalidade já é full-page.
- O core **apenas avalia**: `configModalWidth()` traduz o valor numa classe por
  um mapa fechado. Valor desconhecido cai no default — a string do manifest
  **nunca** é interpolada numa classe (senão um plugin injetaria CSS arbitrário
  no painel). Mesmo padrão de `MediaLimits`/`TemplateSpec`: o plugin declara, o
  core executa, sem `if plugin_id ==`.
- Junto veio uma correção de layout no mesmo modal: ele virou `flex flex-col` com
  cabeçalho `shrink-0` e corpo `flex-1 overflow-y-auto` (antes o modal inteiro
  rolava, levando o cabeçalho embora). Isso também é o que faz `sticky top-0` /
  `sticky bottom-0` dentro de uma screen de config funcionarem contra o
  scrollport do corpo, e não contra a página.

### O que o consumidor precisa saber

- **Não declare `">=1.4,<2.0"` só por causa disto.** O campo degrada sozinho: num
  core anterior a chave é descartada pelo parser do manifest (o dict de screen é
  uma whitelist) e o modal fica no tamanho de sempre. Continue em
  `">=1.0,<2.0"` — travar o range aqui só compra `load_error` num core antigo em
  troca de alguns pixels.
- **Escolha pelo conteúdo, não por gosto.** `wide` é para tela com grade de duas
  colunas ou construtor de regras; `full` é para tabela larga. Uma configuração de
  seis campos deve continuar `normal`.

---

## 1.3.0 — 2026-08-15 · `channel_id`/`conversation_id` em `message.saved` e `message.sent`

Aditiva. Dois campos **acrescentados** a payloads que já existem; quem não os lê
não vê diferença, e todo manifest do parque (`">=1.0,<2.0"`) segue válido.

### O problema

O bus dizia *de quem* veio a mensagem (`phone`) e nunca *por onde*. Um plugin que
precisasse da conversa tinha de resolvê-la por telefone —
`contact_repo.get_by_phone` (que casa as variantes BR de 12↔13 dígitos e devolve
`.first()` **sem `ORDER BY`**) seguido de `conversation_repo.get_open_for_contact`,
que **ignora o inbox**. Com o mesmo cliente atendido em dois canais, o plugin
escrevia na thread errada; com um par de contatos duplicados, qual contato voltava
era indefinido. Seis plugins instalados em produção compartilhavam esse idioma
(`protocolos`, `agendamento_retorno`, `retornos`, `trackify`, `janela_72h`,
`utm_atendente`). O dado sempre existiu no escopo — o `ws_manager.broadcast`
imediatamente acima de cada emit já carregava `channel_id`; só não era publicado.

### O que mudou

| Evento | Campos novos | Onde |
|---|---|---|
| `message.saved` | `channel_id`, `conversation_id` | `batch_text`, `batch_media`, `group_no_mention` |
| `message.sent` | `channel_id`, `conversation_id` | `operator` (texto, mídia e sandbox), `private_ai`, `echo`, `template`, `retry` (só `channel_id`) |

### O que o consumidor precisa saber

- **`conversation_id` pode faltar.** Publicamos só onde o id está de fato resolvido
  no escopo do call site. No `retry` (que apenas faz `UPDATE` de status numa row
  existente) e na resposta da IA (`source="ai"`, cujo save acontece depois do
  envio) ele **não** vem. Campo ausente é melhor que valor errado — trate `None`.
- **`channel_id` agora vem em todos os sites**; antes vinha só em alguns
  (`source="ai"`, `echo`, `group_no_mention`).
- **Um plugin que queira exigir isto declara `">=1.3,<2.0"`** — e aí falha duro
  (`load_error`) num core anterior, que é a garantia que a declaração compra. Quem
  precisa carregar nos dois deve continuar em `">=1.0,<2.0"` e degradar: sem
  `channel_id` no payload, cair no comportamento antigo.

---

## 1.2.0 — 2026-08-12 · API interna plugin→plugin (`entry.services`)

Tudo abaixo é **aditivo**. Nenhum plugin precisa mudar — `">=1.0,<2.0"` continua
válido em todo o parque. Um plugin que dependa dos serviços pode passar a
declarar `">=1.2,<2.0"`; um core anterior recusa o manifest (`load_error`), que é
exatamente a garantia que a declaração compra.

### O terceiro canal entre plugins: request/response

O barramento é broadcast ("aconteceu algo") e os filtros são interceptivos
("reescreva este valor"). Faltava a pergunta com resposta. O motor novo é
[plugins/services.py](../plugins/services.py) — irmão Python do seam que o
frontend já tinha em `buildPluginApi`, com allowlist e negociação de versão.

| Superfície | O que entra |
|---|---|
| `entry.services` | 9ª chave de `_ENTRY_SPECS`, **acrescentada no fim** (a ordem é contratual). O provedor exporta `SERVICES = {"op": callable}`, opcionalmente `SERVICES_VERSION` e `SERVICES_ALLOW`. Um core sem esta linha simplesmente nunca consulta o campo |
| `uses_services` | Campo **opcional** de manifest: `[{plugin: <id>, version: ">=1.0,<2.0"}]`. Entrada malformada é descartada com WARNING, nunca bloqueia o load |
| `plugins.services` | Módulo público: `ServiceResult`, `ServiceProxy`, `ServiceDisabled`, `get`/`call`/`acall`/`available`, `register_plugin_services`/`register_plugin_uses`/`unregister_plugin`, `validate_services`, `describe`, `reset` |
| `plugins.context.get_loop()` | Símbolo público novo — devolve o loop do runtime (ou `None`), usado para atravessar de um chamador síncrono para uma op assíncrona |

**Envelope, nunca exceção**: o dispatch devolve sempre um `ServiceResult` com
status `ok` · `unavailable` · `unknown_op` · `incompatible` · `disabled` ·
`wrong_context` · `error`. `get()` é null-object — nunca devolve `None`, então
feature detection é `if services.get("trackify"):` e um proxy indisponível ainda
responde com o status certo em vez de `AttributeError`.

**Invisível ao HTTP** é o requisito central, não um efeito colateral:
`plugins/services.py` não importa `fastapi`, `_entry_services` nunca toca
`loaded.router`, e nenhum provedor expõe `/rpc`. Travado por
[tests/contracts/test_plugin_services.py](../tests/contracts/test_plugin_services.py)
e por `test_services_are_never_reachable_over_http`, que compara a tabela de
rotas com e sem `entry.services`.

**Registro em `create_app`, antes do lifespan** — o que impõe uma linha de
contrato ao provedor: uma op **não pode depender de estado criado no `setup()`**.
Se depender, devolve `DISABLED`/`ERROR` até ficar pronta; nunca quebra, nunca
bloqueia.

⚠️ `uses_services` é INDEPENDENTE de `plugin_services_version` (que é a
superfície de **frontend**, `api.services`). A colisão de nome é a armadilha.

---

## 1.1.0 — 2026-08-11 · consolidação retroativa (baseline)

Primeira release da API desde a `1.0.0` (2026-05-10). A constante ficou parada
**93 dias** enquanto a superfície crescia de 35 para 75 eventos, de 0 para 24
filtros e ganhava `channels/base.py` inteiro — ou seja, o guard de compat nunca
rejeitou nada e nenhum plugin conseguia dizer "preciso de um core que tenha X".

Esta entrada **não reescreve** esse passado em minors fabricados. Um número de
versão só vale como piso declarável: `">=1.12,<2.0"` só significa alguma coisa
se existiu um core que se identificou como `1.12` e um changelog dizendo o que
ele continha. Numerar levas retroativas criaria pisos fantasma que um autor de
plugin pode declarar e sobre os quais ninguém consegue responder — o mesmo
problema, com aparência de precisão. O passado vira prosa no apêndice; a
contagem começa aqui.

Tudo abaixo é **aditivo**. Nenhum plugin precisa mudar — `">=1.0,<2.0"` continua
válido nos 36 manifests do parque. Um plugin que dependa de um seam listado aqui
pode passar a declarar `">=1.1,<2.0"`, o que era impossível com a constante
congelada.

### Reconciliação do catálogo do bus

Doze nomes **já existiam no core, com produtor vivo, e não estavam listados** —
assinar qualquer um deles produzia um WARNING falso de "nome desconhecido" sobre
um gancho que funciona.

Eventos (10):

| Nome | Produtor |
|---|---|
| `channel.system_event` | `server/routes/channel_webhook.py` — inbound de SISTEMA de um canal (plano 82); o único gancho de bus para o que não é mensagem |
| `conversation.pinned` | `app/services/conversation_service.py` — fixar/desafixar |
| `conversation.labeled` | `server/routes/conversation_labels.py` — etiquetas de UMA conversa |
| `conversation_label.created` / `.updated` / `.deleted` | idem — CRUD do registro global de etiquetas |
| `custom_attribute.created` / `.updated` / `.deleted` | `server/routes/custom_attributes.py` — CRUD de definição |
| `ai.config.changed` | `server/routes/ai_engine.py` — save/rollback de agente, tool, variável ou prompt |

Filtros (2):

| Nome | Valor | `None`/`False` faz | Produtor |
|---|---|---|---|
| `filter.message.notify` | `bool` (default `True`) | Mensagem SILENCIOSA: salva e exibida, sem badge de não-lida/som | `app/services/message_ingest_service.py` |
| `filter.conversation.before_reopen` | `bool` (default `True`) | A mensagem NÃO reabre a conversa fechada; ela aparece normalmente e a conversa segue resolvida | 4 call sites: inbound, envio do operador (texto e mídia) |

Os dois filtros entraram no core em `4e78062` (2026-07-07), num commit que tocou
os 4 arquivos de call site e **não** tocou o catálogo — violando a regra que
estava escrita em `plugins/events.py` desde 8 dias antes. Ficou assim por mais
de um mês, e o plugin `protocolos` (que consome os dois) levava o WARNING falso
a cada boot. É exatamente o caso que o guard desta versão existe para pegar.

Totais desta baseline: **`KNOWN_EVENTS` = 75, `KNOWN_FILTERS` = 24** (2 marcados
experimental), 7 eventos de lifecycle não interceptáveis, 2 chaves dispatch-only
(`*`, `message.any`).

### Nota de compatibilidade herdada (não força MAJOR)

`receipt.changed` foi ampliado em `75dd719` (plano 75): o emit saiu de dentro do
ramo `delivered`/`read` e passou a valer também para `sent`, `failed` e
`played`, com o campo novo `errors`. Um subscriber escrito antes, assumindo "só
chega delivered/read", passa a receber mais do que esperava. Não é MAJOR — nome,
assinatura e forma do payload continuam compatíveis (é superset) e o contrato
escrito nunca restringiu os status —, mas fica registrado porque é o tipo de
mudança que precisa aparecer em algum lugar.

---

## Histórico pré-1.1.0 (não versionado)

Reconstruído por diff commit a commit de `plugins/events.py`, `plugins/context.py`
e `channels/base.py`. Cerca de 17 levas aditivas e **zero remoções com produtor
vivo** — é por isso que o MAJOR permanece em `1`.

### 2026-05-10 → 05-14 · nascimento

`1678c99` cria o sistema de plugins já com `WHATSBOT_API_VERSION = "1.0.0"`.
`b5f0106` (11/05) entrega o bus inteiro **depois** da constante: 35 eventos + 7
filtros, sem bump. `755cb1a` (14/05) acrescenta `message.saved`, mais filtros e
9 media types.

### 2026-06-19 → 06-26 · runtime, canais, conversas

Nasce `channels/base.py` em `bb4fba0` (`Channel`, `ChannelCapabilities`,
`SendResult`, `entry.channels`). Entram `PluginContext`, `spawn_task`,
`spawn_subprocess`, `on_unload`, `entry.lifecycle`, `task.crashed`,
`subprocess.*`, 6 eventos de conversa, `session_window_hours`, `get_deps`,
RBAC de plugin + `filter.authz.decision`, `required_credentials`.

### 2026-06-28 → 07-03 · plano 23 (a maior leva única)

Camada de extensão de frontend, `PLUGIN_SERVICES_VERSION` 1.0, 6 verbos de
conversa, e em `f0a7451` **nasce o catálogo de filtros** — 19 nomes que já
existiam sendo finalmente listados. `40c45ee` mata o produtor de
`filter.media.unknown` (2 horas ANTES do catálogo nascer, que por isso já nasceu
com um nome morto dentro). `d1658d5` acrescenta `KNOWN_FILTERS` + o WARNING de
nome desconhecido. `4dd630d` move a constante para `plugins/semver.py` com o
valor intacto.

Foi nesta leva que o contrato de versionamento foi escrito em comentário — e a
mesma leva adicionou filtros e eventos sem bumpar.

### 2026-07-06 → 07-17 · canais plugáveis

`e25c11d` `AccountIdentity` + os 3 ganchos de identidade (plano 32) · `d789778`
`provider_descriptor()` (plano 33) · `source_id_for`, `contact_type`,
`can_initiate_conversation`, `edit_text` + capability `edit_message`.

### 2026-07-21 → 07-30 · mídia, entrega, auditoria, templates

`dba9f96` `MediaLimits`/`VideoLimits` · `AudioLimits` ·
`filter.conversation.clear_assignee_on_close` · `75dd719` `message.failed` + a
ampliação de `receipt.changed` · `a35db21` `filter.outbound.text` · `0e84e09`
`verify_inbound_signature`, `refresh_token_if_needed`, `human_window_hours` ·
`core_permission()` · `42a9aac` o seam `audit()` + 8 eventos · `d58622a`
`TemplateSpec` + `overrideComponent` (frontend).

### 2026-07-31 → 08-06 · desacoplamento

`c51b3c5` acrescenta `verify_inbound_signature_result` + `ctx.extras`
(`{provider, channel_id, signature_authenticated}`) ao `filter.webhook.payload`.
O plugin `whatsapp_cloud` passou a depender disso **sem ter como declarar**, e
teve de degradar fechado em runtime — é o remendo que um MINOR honesto teria
dispensado, e o melhor argumento para esta versão existir. `440536b` retira
`filter.media.unknown` (bugfix: a entrada nunca coexistiu com produtor vivo em
nenhum commit) e bumpa `PLUGIN_SERVICES_VERSION` 1.0→2.0; `7443fb2` leva a 2.1
(`subscribe`).

## 1.0.0 — 2026-05-10

Nascimento do sistema de plugins (`1678c99`): manifest, loader, migrations com
prefixo obrigatório, tools, prompts, telas e settings declarativas. O bus de
eventos e filtros chegou no dia seguinte, já sem bump.

---

## Apêndice — política de versionamento (migrada do `CLAUDE.md`, plano 139)

> O que está DENTRO da superfície versionada, a tabela MAJOR/MINOR/PATCH, o fluxo quando o
> guard fica vermelho e a história do congelamento em `1.0.0` por 93 dias.

### Versionamento da API de plugins (`WHATSBOT_API_VERSION`)

**Versão atual: `1.7.0`** ([plugins/semver.py](../plugins/semver.py) — fonte única; `plugins/manifest.py` é re-export por valor). Changelog: [docs/PLUGIN_API_CHANGELOG.md](../docs/PLUGIN_API_CHANGELOG.md). Guard: [tests/contracts/test_plugin_api_surface.py](../tests/contracts/test_plugin_api_surface.py) + `tests/goldens/plugin_api_surface.json`.

⚠️ **A constante ficou congelada em `1.0.0` por 93 dias** (2026-05-10 → 2026-08-11) enquanto a superfície crescia de 35 para 75 eventos e de 0 para 24 filtros. Consequência: o guard de compat nunca rejeitou nada e **nenhum plugin conseguia declarar de que core ele precisa** — o `whatsapp_cloud` teve de degradar fechado em runtime porque não tinha como exigir o `ctx.extras.signature_authenticated` do plano 84. A regra em prosa existia desde 2026-06-29 e foi violada 8 dias depois, em silêncio. Por isso a disciplina agora tem dente, não só texto.

**Dentro da superfície versionada** (mudou ⇒ bump): os catálogos do bus (`KNOWN_EVENTS`, `KNOWN_FILTERS`, `EXPERIMENTAL_FILTERS`, `_LIFECYCLE_EVENTS`, `_DISPATCH_ONLY_KEYS`) e a semântica de cada filtro (tipo do valor, o que `None` faz, `ctx.extras`) · os símbolos públicos de [plugins/context.py](../plugins/context.py) e os campos dos contextos · o schema do manifest, as regexes de validação e as 9 chaves de `_ENTRY_SPECS` **em ordem** · [channels/base.py](../channels/base.py) + [channels/events.py](../channels/events.py) · as convenções de host (prefixo `plugin_<id>_`, namespace `whatsbot_plugins.<id>`, mounts `/api/plugins/<id>` e `/plugins/<id>/static`, isenção `/public/`, prefixo `plugin.<id>.` de config, chave RBAC, `PLUGIN_ACTION_RE`, `TEARDOWN_TIMEOUT_SEC`).

**Fora**: `db.repositories` e os demais módulos do core que plugins importam — são dependência real, **não API declarada**, e a proteção deles continua sendo o import defensivo (ver o aviso em "O que fica no core e o que vai pro plugin"); e o frontend, que tem números próprios (`FRONTEND_API_VERSION`, `PLUGIN_SERVICES_VERSION` em [web/static/js/plugins/api.js](../web/static/js/plugins/api.js)) e falha de forma **assimétrica** — lá, incompatível pula o `frontend_extends`; aqui, incompatível faz o plugin **deixar de existir**. **Nunca sincronize os valores.** Seam que um plugin publica para outro (`filter.retornos.*`, `protocolos.*`) é versionado pelo `version` do plugin publicador.

O teste de pertinência é mecânico: **está dentro se existe um snapshot que falha quando aquilo muda**. Querer mover algo para dentro custa escrever o snapshot.

| Nível | Gatilho |
|---|---|
| **MAJOR** | remover/renomear nome de catálogo **com produtor vivo**; mudar o tipo do valor de um filtro ou a semântica do `None`; remover/renomear símbolo público, campo de dataclass, chave de `entry` ou convenção de host; tornar obrigatório campo opcional do manifest; tornar abstrato método de `Channel` que tinha default. **Derruba os 36 manifests do parque de uma vez** (todos declaram `">=1.0,<2.0"`), inclusive o `gowa` bundled — é tranche que republica os ZIPs com ordem de deploy, não decisão de commit |
| **MINOR** | acrescentar nome ao catálogo (**no mesmo commit do call site**), símbolo, campo com default, chave de `entry`, capability, método com default; alargar `ctx.extras`; ampliar quando um evento existente é emitido |
| **PATCH** | correção que não muda a forma; retirar nome de catálogo **sem produtor vivo** (exige varredura repo-wide + changelog + teste de WARNING) |

Exceção: seam em `EXPERIMENTAL_FILTERS` pode sair sem MAJOR — o contrato em [plugins/events.py](../plugins/events.py) já diz que ele pode se mover até se formar.

**Fluxo quando o guard fica vermelho** (ele imprime estes 3 passos na falha):
1. bump em [plugins/semver.py](../plugins/semver.py);
2. entrada no topo de `docs/PLUGIN_API_CHANGELOG.md` — o heading `## X.Y.Z — data` precisa ser o **primeiro** heading de versão do arquivo (o apêndice histórico usa `###` de propósito);
3. `UPDATE_PLUGIN_API_SURFACE=1 venv/bin/python -m pytest tests/contracts/test_plugin_api_surface.py`.

A regeneração **se recusa a rodar** enquanto a constante não tiver andado — é isso que torna a disciplina aplicável em vez de documentada. A env é deliberadamente separada do `UPDATE_GOLDENS` usado em massa nos goldens de caracterização, que varreria a superfície da API junto.
