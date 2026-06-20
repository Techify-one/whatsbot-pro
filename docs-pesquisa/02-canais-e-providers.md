# 02 — Canais e Providers (abstração de canal, múltiplos números, WhatsApp Cloud API)

> Documento de pesquisa de arquitetura. **Não** descreve código já implementado — descreve uma
> **proposta** para suportar múltiplos canais/números de comunicação no WhatsBot.
>
> Decisão travada: criar uma **abstração de canal genérica** desde já. GOWA (WhatsApp não-oficial)
> é hoje a única implementação; WhatsApp Cloud API (oficial) e, no futuro,
> Telegram / Instagram / Messenger / Email são outras implementações da mesma interface.
> Cenário: **empresa única, server-hosted** (não é multi-tenant SaaS).
>
> **Decisão de reposicionamento (2026-06-18):** o cliente concluiu que o WhatsBot é, no fundo,
> um **sistema de atendimento** — não vive sem uma **caixa de entrada** para receber mensagens.
> Disso decorre a fronteira core vs plugin que organiza este doc inteiro: o **domínio de atendimento**
> (`Inbox → Conversation → Message`), o **contrato de canal** (`ChannelProvider`) e o **roteamento de
> entrada** (webhook → descobrir canal/inbox → conversa) são **CORE**; o **provider concreto** é
> implementação. O GOWA, hoje **cravado no core** (`gowa/manager.py`, `gowa/client.py`, device fixo
> `"whatsbot"`), passa a ser **um provider atrás do contrato**. Uma `Inbox` **tem** um `Channel`; o
> GOWA não "vira" inbox — é o *provider* de uma inbox WhatsApp não-oficial.
>
> **Decisão de produto (cliente, 2026-06-18):** **todo provider é plugin/opcional — inclusive o GOWA, já
> no v1** — para que um cliente que só usa WhatsApp oficial (Cloud API), só Telegram ou só e-mail **não
> instale nem rode o GOWA**. **Não** se escreve o GOWA built-in temporário para mover depois (evita
> trabalho jogado fora e recristalizar o acoplamento). A investigação (§3.4.4) mostra que isso é viável
> sobre **três capacidades de runtime, todas CORE**: lifecycle de plugin (`setup/teardown` aguardados),
> supervisor de tasks de fundo e serviço de subprocesso gerenciado. As três são core porque são a
> infraestrutura que os próprios plugins consomem — um plugin não pode fornecê-las (§3.4.4, nota). O custo
> por *tipo* de provider varia: *webhook-only* (Cloud API, Telegram-webhook) é plugin fácil; *polling
> leve* (Telegram long-poll, IMAP) usa o supervisor de tasks; *subprocesso* (GOWA) usa o serviço de
> subprocesso gerenciado. **Ordem de construção:** capacidades (i)+(ii) validadas num provider barato sem
> subprocesso → (iii) + GOWA-plugin por último (§3.4.8/§9). Isto **revisa** a conclusão anterior deste doc
> ("subprocesso não deve ser plugin / GOWA fica no core"). Princípio em
> [`00-visao-geral.md`](00-visao-geral.md) ("caixa de entrada é CORE; provider é implementação; qualquer
> provider pode ser plugin, inclusive o GOWA, mediante fundação no core"); mesmo padrão híbrido
> (núcleo/contratos/capacidades no core, implementações em core e/ou plugin) recomendado em
> [`06-motor-multiagente-agno.md`](06-motor-multiagente-agno.md).
>
> Documentos relacionados:
> - [`00-visao-geral.md`](00-visao-geral.md) — princípio "caixa de entrada é CORE; provider é implementação"
>   e modelo de domínio alvo (`Inbox 1:1 Channel`).
> - [`01-inbox-e-conversas.md`](01-inbox-e-conversas.md) — modelo de inbox/conversa. **Cada canal é uma fonte
>   (source) que alimenta o mesmo inbox 1:1.** O `channel` deste doc é o "de onde veio" de uma conversa.
> - [`03-rbac-usuarios-permissoes.md`](03-rbac-usuarios-permissoes.md) — quem pode adicionar/remover canais, ver
>   tokens, conectar QR. Gerenciar canais é uma ação privilegiada (admin).
> - [`06-motor-multiagente-agno.md`](06-motor-multiagente-agno.md) — recomendação híbrida análoga
>   (núcleo no core, UI/extensões via plugin) que serve de molde para o "provider no core vs plugin".

---

## 1. O que existe hoje

O WhatsBot fala WhatsApp por **um único** subprocess GOWA, com **um único device**, e o webhook
**não** distingue de qual número/canal a mensagem chegou. Pontos concretos:

### `gowa/manager.py` — UM processo, porta fixa
- `GOWAManager.__init__(port=3000, ...)` (linhas ~36-49): porta **hardcoded 3000**, um processo,
  um watchdog. Não há noção de "vários GOWAs" nem de "vários devices".
- `start()` (linhas ~55-135) monta `cmd = [binary, "rest", "--port", str(self.port), "--webhook", ...]`
  e dá `subprocess.Popen` único. O webhook URL é **um só** (`--webhook http://127.0.0.1:{web_port}/api/webhook`).
- `_watchdog()` (linhas ~169-210) supervisiona **um** PID e reinicia com rate-limit (3 restarts/60s).
  Toda a lógica assume um processo.

### `gowa/client.py` — `device_id="whatsbot"` HARDCODED
- `_DEFAULT_DEVICE_NAME = "whatsbot"` (linha 12) e `self.device_id = _DEFAULT_DEVICE_NAME` (linha 52).
- `_headers` (linhas 55-57) injeta `X-Device-Id: <device_id>` em **toda** request — sempre o mesmo valor.
- `ensure_device()` (linhas 112-141): se já existe device, **usa o primeiro da lista** (`devices[0]`,
  linha 127-128). Ou seja, o cliente é singleton de 1 device — não há como endereçar device B.
- Todos os métodos de envio (`send_message`, `send_image`, `send_audio`, `send_file`, `mark_as_read`,
  `react_to_message`, etc.) usam `self._headers` → sempre o mesmo número.

### `server/routes/webhook.py` — NÃO roteia por número
- `@app.post("/api/webhook")` / `async def webhook(body: dict)` (linhas ~1065-1066): **um** endpoint,
  recebe o payload bruto do GOWA e processa.
- O parsing extrai `chat_id`, `from`, `sender_jid`, `from_name`, `id`, etc. (linhas ~1289, 1374,
  1427-1476) — mas **nenhum** campo identifica "qual dos nossos números recebeu isto". O GOWA v8 já
  envia um campo de topo `device_id` no payload (ver §4), mas o WhatsBot **não o lê**.
- `gowa_client = deps.gowa_client` (linha 421): handler usa **o** client global único para responder.
  A resposta vai necessariamente pelo mesmo (único) número.

**Conclusão**: a arquitetura atual é "1 app = 1 número WhatsApp via GOWA". Para múltiplos números e/ou
canais oficiais, precisamos introduzir o conceito de **Canal** como entidade de primeira classe.

---

## 2. Requisitos

Funcionais:
1. **N números** atendidos pela mesma instância (ex.: comercial, suporte, financeiro).
2. **Tipos heterogêneos**: GOWA (não-oficial, QR) e WhatsApp Cloud API (oficial, token) coexistindo.
3. **Roteamento de entrada**: toda mensagem recebida precisa saber **a qual canal** pertence, e a
   conversa no inbox precisa carregar esse `channel_id` (ver [`01-inbox-e-conversas.md`](01-inbox-e-conversas.md)).
4. **Roteamento de saída**: ao responder uma conversa, enviar **pelo mesmo canal** de origem.
5. **Cloud API tradicional**: o cliente cria o app na Meta e cola **Phone Number ID + Access Token +
   verify token**. NÃO é embedded signup / tech provider.
6. **Base fácil para Telegram** depois (bot token + webhook/polling), sem reescrever a abstração.

Não-funcionais:
1. **Isolamento de falha**: crash/relogin de um canal não derruba os outros.
2. **Segurança de segredos**: tokens da Cloud API e bot tokens não podem vazar (mascarar na UI, não
   logar, idealmente cifrados em repouso).
3. **Compatibilidade**: não quebrar instalações de 1 número hoje (migração transparente para "canal único").
4. **Mínimo acoplamento ao core**: handler/agent não devem ter `if provider == "gowa"`; falam com a
   interface de canal.

---

## 3. Interface de canal proposta

### 3.0 Reposicionamento: caixa de entrada como core, GOWA como provider

Antes de detalhar a interface, vale fixar **o que é core e o que é implementação** — porque isto é
um **refactor**, não só uma feature nova. O §1 mostrou que hoje o GOWA está **cravado** no core:
`gowa/manager.py` (subprocess, watchdog, porta 3000), `gowa/client.py` (device fixo `"whatsbot"`,
`devices[0]`) e `server/routes/webhook.py` (endpoint único `/api/webhook`, sem noção de "qual canal").
A versão Pro **extrai** essa implementação para trás de um contrato (`ChannelProvider`/`Channel` do
§3.2) e a registra num `ChannelRegistry`. O core de atendimento (inbox/conversa/roteamento) deixa de
conhecer "GOWA" e passa a conhecer só a interface.

**Distinção a fixar** (alinhada a [`00-visao-geral.md`](00-visao-geral.md) e
[`01-inbox-e-conversas.md`](01-inbox-e-conversas.md)):

- **`Inbox`** = a **caixa lógica** de atendimento (nome, atendentes, fila, config, participação de
  agente IA). É CORE. O sistema de atendimento não existe sem ela.
- **`Channel`/`Provider`** = **como** aquela inbox fala com o mundo (GOWA, Cloud API, Telegram). É a
  *implementação* atrás do contrato. Uma `Inbox` **tem** um `Channel` (1:1); o GOWA **não** "vira"
  inbox — ele é o *provider* de uma inbox WhatsApp não-oficial.

| | **Antes (hoje — cravado)** | **Depois (provider registrado)** |
|---|---|---|
| Bridge WhatsApp | `gowa/manager.py` + `gowa/client.py` instanciados direto no startup; `deps.gowa_client` global | `GOWAChannel(Channel)` registrado no `ChannelRegistry`; core resolve `channel_id → Channel` |
| Device | `device_id="whatsbot"` hardcoded; `ensure_device()` pega `devices[0]` | um device GOWA **por canal**; `GOWAClient` parametrizado por `device_id` |
| Webhook | endpoint único `/api/webhook`, sem identificar o número | rota por canal `/api/webhook/{provider}/{channel_id}` → `registry.get(channel_id).parse_inbound(raw)` |
| Envio | handler chama o client global → sempre o mesmo número | handler chama `registry.get(conversa.channel_id).send_text(...)` |
| Acoplamento ao core | `if/else` implícito em "é GOWA" | core fala **só** com a interface; provider concreto pode ser core **ou** plugin |

**Padrão unificador (o mesmo do registry de TOOLS que o WhatsBot já tem):** o **core define o
contrato + o registry**; **core e plugins registram implementações no mesmo registry**. Hoje isso já
acontece com tools (`CORE_TOOLS` no core + `register_plugin_tools()` dos plugins, todas no mesmo
registry do `AgentHandler`). A proposta replica esse desenho para canais — ver §3.4 para o **novo
ponto de extensão** que os plugins precisam ganhar.

A ideia central: **um adapter por provider**, todos implementando a mesma interface. O core
(handler, inbox, rotas de envio) só conhece a interface. Inspirado no padrão "channel adapter /
provider" do Chatwoot, onde `Channel::Whatsapp` é o data store e a lógica específica vive em
`Whatsapp::Providers::*Service` ([Chatwoot WhatsApp Channel — DeepWiki](https://deepwiki.com/chatwoot/chatwoot/7.4-whatsapp-channel)).
No Chatwoot, os tipos de canal (`Channel::Whatsapp`, `Channel::Telegram`, `Channel::Email`, …) são
**built-in no core** (tabelas e models versionados), enquanto integrações de terceiros entram pela
camada de **Apps/Integrations** — uma distinção análoga à de "provider no core vs provider como
plugin" que a §3.4 formaliza.

### 3.1 Eventos normalizados de entrada

Cada adapter converte o payload bruto do provider em um **evento normalizado** comum, para que o
webhook/pipeline não saiba de onde veio. Forma sugerida (dicionário tipado):

```python
# Evento de entrada normalizado (independente de provider)
{
  "channel_id": "comercial",        # qual canal recebeu
  "provider": "gowa",               # gowa | whatsapp_cloud | telegram | ...
  "kind": "message" | "reaction" | "receipt" | "presence" | "connection" | ...,
  "direction": "in",
  "external_msg_id": "ABCD123",     # id do provider (idempotência)
  "chat_id": "5511999999999",       # identificador da conversa (phone, group jid, telegram chat id)
  "sender_id": "5511999999999",     # quem mandou (em grupo difere do chat_id)
  "sender_name": "Fulano",
  "is_group": False,
  "text": "olá",
  "media_type": "image" | None,
  "media_path": "/.../file.jpg" | None,
  "media_extras": { ... },
  "ts": 1718700000,
  "raw": { ... },                   # payload bruto do provider (debug / filtros)
}
```

Isso é o mesmo "tipo de mensagem" que os filtros de plugin (`filter.message.before_save`) já
manipulam — a abstração apenas **garante** o formato comum entre providers.

### 3.2 Interface (apenas ilustrativo — não commitar)

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class SendResult:
    external_msg_id: str | None
    ok: bool
    error: str | None = None

class Channel(ABC):
    """Adapter de um canal de comunicação (1 número / 1 bot)."""

    provider: str               # "gowa" | "whatsapp_cloud" | "telegram"
    channel_id: str             # id interno único (PK de `channels`)

    # ── ciclo de vida ──────────────────────────────────────────────
    @abstractmethod
    def start(self) -> None: ...           # subir subprocess / registrar webhook / nada
    @abstractmethod
    def stop(self) -> None: ...
    @abstractmethod
    def status(self) -> dict: ...          # {connected, logged_in, needs_qr, error}

    # ── conexão (varia por provider) ───────────────────────────────
    def get_qr(self) -> bytes | None:      # só GOWA; Cloud API/Telegram retornam None
        return None

    # ── envio (saída) ──────────────────────────────────────────────
    @abstractmethod
    def send_text(self, chat_id: str, text: str, *,
                  reply_to: str | None = None,
                  mentions: list[str] | None = None) -> SendResult: ...
    @abstractmethod
    def send_media(self, chat_id: str, kind: str, path_or_url: str, *,
                   caption: str = "", filename: str | None = None) -> SendResult: ...

    # ── operações opcionais (default no-op) ────────────────────────
    def mark_read(self, chat_id: str, external_msg_id: str) -> None: ...
    def send_presence(self, chat_id: str, state: str) -> None: ...     # typing
    def react(self, chat_id: str, external_msg_id: str, emoji: str) -> None: ...
    def revoke(self, chat_id: str, external_msg_id: str) -> None: ...

    # ── normalização de entrada (webhook → evento comum) ───────────
    @abstractmethod
    def parse_inbound(self, raw: dict) -> list[dict]: ...   # → eventos normalizados (§3.1)

    # ── templates (só Cloud API; ver §5) ───────────────────────────
    def send_template(self, chat_id: str, template: str, lang: str,
                      variables: dict) -> SendResult:
        raise NotImplementedError("provider não suporta templates")
```

Capacidades opcionais (QR, templates, presence, reactions) ficam como métodos com default no-op /
`NotImplementedError`, mais um descritor de capacidades (`Channel.capabilities`) para a UI saber o
que mostrar (GOWA mostra QR; Cloud API mostra formulário de token + aba de templates; Telegram não
tem nem QR nem templates).

### 3.3 Como o webhook roteia para o canal certo

Três estratégias (pode-se usar mais de uma):

| Provider | Sinal de roteamento | Mecanismo |
|---|---|---|
| **GOWA** | campo `device_id` no payload (GOWA v8 manda no topo) | `parse_inbound` lê `device_id` → mapeia para `channel_id` via tabela `channels`. **Alternativa mais robusta**: dar a cada device um webhook com path próprio (ver §4). |
| **WhatsApp Cloud** | path/token na URL do webhook + `metadata.phone_number_id` no payload | rota dedicada `/api/webhook/cloud/{channel_id}` (path = canal) **e/ou** match por `phone_number_id`. Verificação por `verify_token` por canal. |
| **Telegram** | bot token na URL do webhook | rota `/api/webhook/telegram/{channel_id}` (um endpoint por bot); ou polling dedicado por bot. |

**Recomendação**: webhook com **path por canal** (`/api/webhook/{provider}/{channel_id}`) é a forma
mais à prova de erro — o roteamento é explícito na URL, não depende de adivinhar por campo. Para o
GOWA isso significa registrar cada device com um `--webhook` distinto (ou um webhook por device, se a
versão suportar — ver §4). Um `ChannelRegistry` (substituto do `gowa_client` global) resolve
`channel_id → Channel` e o webhook chama `channel.parse_inbound(raw)` → eventos → pipeline.

---

## 3.4 Provider no core vs provider como plugin — e o novo ponto de extensão de canal

Definido que o **contrato + registry de canal são core** (§3.0), resta a pergunta operacional: **qual
provider concreto mora no core e qual pode ser plugin?** E, para os que forem plugin: **o sistema de
plugins atual suporta registrar um `ChannelProvider`?** (Resposta curta: hoje **não** — precisa ganhar
um ponto de extensão novo. Detalhado abaixo.)

### 3.4.1 Três tipos de provider — e o custo de cada um como plugin

> **Revisão da posição anterior.** Uma rodada anterior deste doc concluiu que "provider com subprocesso
> (GOWA) NÃO deve ser plugin — fica built-in no core". O cliente empurrou de volta com um requisito de
> produto legítimo: quem só usa Cloud API, só Telegram ou só e-mail **não deveria nem instalar/rodar o
> GOWA**. A investigação de código + precedentes (§3.4.4, §3.4.8) mostra que **qualquer provider pode
> ser plugin** — o que muda é **quanto de fundação no core** cada tipo exige. **Decisão (2026-06-18): o
> GOWA nasce como provider-plugin já no v1** (§3.4.8) — não há built-in temporário. As capacidades de
> runtime que o viabilizam (i/ii/iii) permanecem **core**.

O critério deixa de ser "fundacional vs extensível" (binário demais) e passa a ser **o que o provider
precisa em runtime**. Três tipos:

| Tipo | Exemplos | O que precisa em runtime | Vira plugin com | Custo |
|---|---|---|---|---|
| **(a) Webhook-only** | WhatsApp Cloud API, Telegram-webhook | **nada de fundo** — o core entrega o evento e o provider responde stateless | só o ponto de extensão de canal + roteamento de webhook (§3.4.2/§3.4.3) | **baixo** |
| **(b) Polling leve** | Telegram long-poll (`getUpdates`), IMAP/e-mail | **1 corrotina em loop** (puxa, normaliza, dorme) | + **(ii) supervisor de tasks de fundo** (§3.4.4) | **médio** |
| **(c) Subprocesso** | **GOWA** (binário Go), qualquer bridge externa | **processo do SO** + watchdog + ciclo de QR | + **(iii) serviço de subprocesso gerenciado** (§3.4.4) | **alto** |

Mapa por provider previsto (a **camada não é destino fixo** — é função de qual capacidade do core já existe):

| Provider | Tipo | Como plugin depende de | Recomendação de faseamento |
|---|---|---|---|
| **WhatsApp Cloud API** (oficial) | (a) webhook-only* | ponto de extensão de canal | Pode ser plugin desde cedo. *Tem peças de segurança (handshake `hub.challenge`, cifragem de tokens, janela/templates) — ver §3.4.8: o **contrato** dessas peças é core; a implementação pode ser plugin. |
| **Telegram** | (a) webhook **ou** (b) polling | (a)→nada extra; (b)→supervisor de tasks | **Primeiro provider-plugin.** Valida (a) e (b) de uma vez (Telegram suporta os dois modos). |
| **Instagram / Messenger** | (a) webhook-only | ponto de extensão de canal | Plugin (mesma família Meta Graph). |
| **Email** (IMAP/SMTP) | (b) polling (IMAP) | supervisor de tasks | Plugin assim que (ii) existir. |
| **GOWA** (WhatsApp não-oficial) | (c) subprocesso | **(iii) serviço de subprocesso gerenciado** | **Provider-plugin já no v1** (decisão 2026-06-18, §3.4.8). É o último a ser construído — depois de (i)+(ii) validadas num caso barato (sem subprocesso) — porque exige a capacidade (iii). |

Regra de bolso atualizada: **a camada de um provider é a capacidade de runtime que ele exige do core,
não uma classificação fixa.** Tudo que é *webhook-only* já é plugin fácil; *polling* vira plugin com um
supervisor de tasks; *subprocesso* vira plugin com um serviço de subprocesso gerenciado. É o mesmo
padrão híbrido do motor de IA ([`06-motor-multiagente-agno.md`](06-motor-multiagente-agno.md) §5.5:
"núcleo/contratos/capacidades no core; implementações em core e/ou plugin") e o mesmo que o Chatwoot
adota ao tratar tipos de canal como built-in e integrações de terceiros como apps — com a diferença de
que aqui **queremos** poder empurrar até o GOWA para plugin, dada a meta de produto "não rodar o GOWA
quem não usa".

### 3.4.2 O que o sistema de plugins precisa GANHAR

Hoje o `PluginRegistry`/loader registra apenas **tools, prompt fragments, rotas REST, telas Preact,
events, filters e migrations** (ver "Sistema de plugins" no `CLAUDE.md`). **Não existe** um ponto de
extensão para "canal". Para um plugin registrar um `ChannelProvider` sem tocar no core, propõe-se:

**(a) Novo campo no manifest** (`plugin.yaml`) — declarar o módulo de canais, no mesmo estilo de
`entry.tools`/`entry.events`/`entry.filters`:

```yaml
# plugin.yaml
id: telegram
name: Telegram
version: 0.1.0
whatsbot_api_version: ">=1.0,<2.0"
entry:
  channels: channels      # → módulo storages/plugins/telegram/channels.py
```

**(b) Contrato de export** — o módulo exporta uma lista de classes de provider, que o core registra no
`ChannelRegistry` durante o bootstrap do plugin (espelhando como `tools.py` exporta `CORE_TOOLS`):

```python
# storages/plugins/telegram/channels.py
from channels.base import Channel, SendResult   # contrato CORE (importado por caminho estável)

class TelegramChannel(Channel):
    provider = "telegram"
    capabilities = {"qr": False, "templates": False, "groups": True, "presence": False}

    def start(self) -> None: ...          # inicia long-poll OU registra webhook
    def stop(self) -> None: ...
    def status(self) -> dict: ...
    def send_text(self, chat_id, text, *, reply_to=None, mentions=None) -> SendResult: ...
    def send_media(self, chat_id, kind, path_or_url, *, caption="", filename=None) -> SendResult: ...
    def parse_inbound(self, raw: dict) -> list[dict]: ...   # → eventos normalizados §3.1

# lista descoberta pelo loader (igual a CORE_TOOLS)
CHANNEL_PROVIDERS = [TelegramChannel]
```

**(c) Wiring no loader** — em `plugins/loader.py`, ao carregar um plugin com `entry.channels`, importar
o módulo e chamar `channel_registry.register_provider(cls)` para cada classe em `CHANNEL_PROVIDERS`.
É um passo análogo ao `agent_handler.register_plugin_tools(...)` que já existe. O **disable** do plugin
desregistra o provider (e derruba os canais daquele tipo no próximo restart — tudo-ou-nada, como o
toggle de plugin já é hoje).

> **Provider class** registra um **tipo** de canal (ex.: "telegram"); cada `Channel` **instanciado** é
> um número/bot concreto (uma row em `channels`). O registry, então, tem duas camadas: `provider name →
> Provider class` (de core+plugins) e `channel_id → Channel instance` (instâncias vivas). Hoje o §3.3
> fala só da segunda; o ponto de extensão adiciona a primeira.

### 3.4.3 Roteamento de webhook para um provider-plugin

O core já vai expor a rota genérica `/api/webhook/{provider}/{channel_id}` (§3.3). Para um
provider-plugin, **o core continua dono do endpoint** e despacha por lookup no registry —
o plugin **não** abre uma rota própria de webhook. Fluxo:

1. Request chega em `/api/webhook/telegram/{channel_id}` (ou GET de verificação, se o provider exigir).
2. O core resolve `channel_id → Channel` (instância criada pela Provider class do plugin).
3. Chama `channel.parse_inbound(raw)` → eventos normalizados → pipeline comum.

Assim o plugin **não precisa** registrar uma rota `APIRouter` para receber mensagens (embora ainda
possa registrar rotas próprias para UI/config sob `/api/plugins/<id>/...`). O contrato do provider deve
**declarar como quer ser roteado** — recomenda-se que a Provider class exponha um descritor
(ex.: `inbound_route = "path"` ou `inbound_route = "poll"`) para o core saber se monta endpoint ou se
o provider faz polling (Telegram long-poll). **Recomendação:** roteamento **por path** (explícito na
URL) como padrão, com o campo `device_id`/`instance` do payload como confirmação/fallback — exatamente
o que §11.3 valida a partir do modelo "instance" da Evolution API.

### 3.4.4 O que o core precisa GANHAR para providers serem plugins

Esta é a **tensão técnica central** do provider-como-plugin, e onde a posição anterior ("subprocesso fica
reservado ao core") foi **revisada**. O contrato `Channel` tem `start()/stop()/status()`, mas hoje **o
sistema de plugins não tem como executar nem encerrar nada com segurança** além de importar o módulo. O
que falta não é "impossível" — são **três capacidades de runtime** que o **core** precisa construir.
Cada uma com o *porquê* (o que falta no código de hoje) e o *como* (precedente que mostra a forma).

> **As três capacidades (i), (ii) e (iii) abaixo são CORE — não são, e não podem ser, plugin.** O
> cliente perguntou explicitamente se o "supervisor de tasks" seria plugin ou core: a resposta é
> **core** — e o mesmo vale para o lifecycle de plugin (i) e o subprocesso gerenciado (iii). A razão é
> de dependência: elas são a **infraestrutura que os próprios plugins consomem**. O supervisor é dono do
> **event loop + lifespan + o registro onde as corrotinas se inscrevem**; um plugin não pode fornecê-lo
> porque **os plugins dependem dele para rodar** (problema do ovo e da galinha — um plugin precisaria do
> supervisor já existindo para poder se carregar e oferecer o supervisor). Elas vivem no **mesmo nível**
> do *plugin loader* e dos *registries* (de tools, de canais): fundação que existe antes de qualquer
> plugin. O que vira plugin é o **provider**, que usa essas capacidades através do `context` injetado.
> **Regra mental:** *contratos, registries e capacidades de runtime no core; implementações que se
> registram neles podem ser plugin.*

#### Achados de código — o que existe hoje (e por que não basta)

- **`plugins/loader.py` — não há gancho de inicialização.** O loader só faz `importlib` do módulo do
  plugin; **não existe** `setup()`/`start()`/`on_startup()` que o host chame após carregar. Um provider
  que precise "ligar" (abrir poll, subir subprocesso) não tem onde fazê-lo de forma gerenciada.
- **`plugins/events.py` — `app.startup`/`app.shutdown` são fire-and-forget.** Os dois eventos **são**
  emitidos no lifespan (`server/app.py` ~L159 e ~L171), mas `emit_event` usa `run_coroutine_threadsafe`
  **sem aguardar** `Future.result()` — o **shutdown não espera** os handlers de plugin terminarem. Um
  teardown de provider (fechar conexão, matar subprocesso) pode ser cortado no meio.
- **`plugins/restart.py` — o toggle usa `os._exit(0)`.** Desligar um plugin faz
  `emit(plugin.disabled)` → `schedule_restart()` → `os._exit(0)` após ~1,5 s. `os._exit` **não roda**
  finalizers/`atexit`/signal handlers → um subprocesso aberto pelo plugin **viraria órfão**.
- **`server/app.py` lifespan — 4 tasks HARDCODED.** O lifespan cria `start_gowa_task`,
  `status_poll_loop`, `qr_poll_loop`, `avatar_fetch_task` numa **lista local**; no shutdown faz
  `task.cancel()` e `gowa_manager.stop()`. **Não há registro reutilizável** de tasks ao qual um plugin
  possa se anexar.
- **`gowa/manager.py` — subprocesso é core, e a limpeza é grosseira.** GOWA é `Popen` + watchdog em
  thread daemon, `stop()` com terminate/kill; start/stop são chamados no lifespan do **core**. A limpeza
  de órfãos é externa e bruta (`pkill -f bin/gowa` em `linux_start.sh`, `taskkill` em
  `windows_start.bat`).
- **`plugins/context.py` — não expõe loop nem stop_event.** O contexto injeta `plugin_db`/`broadcast`,
  mas **não** dá ao plugin o event loop nem um `stop_event`/registro de cleanup — o plugin não consegue
  `create_task` gerenciado nem registrar teardown.

#### (i) Lifecycle de plugin real — `setup(ctx)` / `teardown(ctx)` **aguardados** pelo host

- **Porquê (gap):** hoje o loader só importa (sem gancho); `app.shutdown` é fire-and-forget; o toggle é
  `os._exit`. Um provider-plugin não tem um ponto **garantido** para ligar e — pior — não tem um ponto
  garantido para **desligar limpo** (o `os._exit` pula finalizers).
- **Como (precedente):** **VS Code** dá às extensões `activate()`/`deactivate()` mais o modelo
  **Disposable** (`context.subscriptions`) — tudo que a extensão registra é descartado pelo **host** no
  teardown; é o host que garante o `dispose`
  ([VS Code — Language Server Extension Guide](https://code.visualstudio.com/api/language-extensions/language-server-extension-guide);
  [vscode-languageserver-node](https://github.com/microsoft/vscode-languageserver-node)). **Home
  Assistant** usa `async_setup_entry`/`async_unload_entry` + `entry.async_on_unload(...)` (registro
  **declarativo** de cleanup, executado **mesmo se o setup falhar**), e faz **unload/reload em runtime
  sem reiniciar o processo**
  ([HA — config entry unloading](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/config-entry-unloading/)).
- **O que o core ganha:** ganchos `setup(ctx)`/`teardown(ctx)` **chamados e aguardados** pelo host (no
  `app.startup`/`app.shutdown` e no enable/disable), e **parar de usar hard-exit no caminho de disable**
  — ou, no mínimo, **rodar `teardown` ANTES do `os._exit`**. Um registro estilo Disposable/`async_on_unload`
  no `ctx` deixa o plugin declarar cleanups que o core executa.

#### (ii) Supervisor de tasks de fundo — resolve **Telegram e e-mail** como plugin

- **O que é "polling leve":** uma **corrotina que puxa** updates num loop (`while not stop_event: pull();
  sleep()`) — Telegram `getUpdates` (long-poll) ou IMAP `IDLE`/poll. É **muito mais simples que
  subprocesso**: roda no mesmo processo Python (sem PID externo, **sem órfão**), e o **cleanup é só
  `task.cancel()`** (ou sinalizar `stop_event`). Diferente de *webhook*, em que o core entrega o evento e
  o provider nem precisa de loop.
- **Porquê (gap):** as 4 tasks de fundo são **hardcoded** no lifespan (`server/app.py`); não há registro
  ao qual um plugin se anexe, e o `ctx` não expõe loop/`create_task`. Sem isso, um provider de polling não
  tem onde rodar seu loop de forma gerenciada (cancelamento no shutdown, restart em erro).
- **Como (precedente):** **árvores de supervisão do Erlang/OTP** — supervisor que **classifica restart**
  (`permanent`/`transient`/`temporary`) e aplica **rate-limit** (circuit breaker de crash-loop: se
  reiniciar demais em pouco tempo, desiste)
  ([Adopting Erlang — supervision trees](https://adoptingerlang.org/docs/development/supervision_trees/)).
- **O que o core ganha:** um **registry de tasks de fundo** onde **core e plugins** registram corrotinas
  de longa duração (loop com `stop_event`), com **restart classificado + backoff/rate-limit** (padrão
  OTP — o watchdog do GOWA já faz uma versão disso: 3 restarts/60 s). Generalizar as 4 tasks hardcoded
  nesse supervisor e expô-lo aos plugins via o `ctx`. **Este item, sozinho, habilita Telegram (long-poll)
  e e-mail (IMAP) como plugin.**

#### (iii) Serviço de subprocesso gerenciado — o que falta para o **GOWA virar plugin**

- **Porquê (gap):** gerenciar `Popen`+watchdog **dentro** de um plugin é frágil hoje — o `os._exit(0)`
  do toggle mataria o **pai** mas **não** necessariamente o **filho** (vira órfão), não há health, e a
  limpeza de órfãos é externa/grosseira. O subprocesso precisa de um dono **no core**, robusto.
- **Como (precedente):** boas práticas de subprocesso —
  **process group** (`subprocess.Popen(start_new_session=True)` no POSIX; `CREATE_NEW_PROCESS_GROUP` no
  Windows) para **matar a árvore inteira** com `os.killpg`
  ([docs subprocess](https://docs.python.org/3/library/subprocess.html)); **"die-with-parent"** como
  defesa contra hard-exit (Linux `PR_SET_PDEATHSIG`; Windows **Job Object** com
  `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`)
  ([Meziantou — Job Object kill-on-close](https://www.meziantou.net/killing-all-child-processes-when-the-parent-exits-job-object.htm));
  **PID file + matar instância stale no boot** (essencial para evitar **conflito de sessão WhatsApp** —
  lição dos Jenkins JNLP agents); e um **readiness probe** antes de declarar o processo "pronto"
  (padrão do [pytest-xprocess](https://pytest-xprocess.readthedocs.io/), que sobe um processo externo e
  espera o sinal de prontidão).
- **O que o core ganha:** um **serviço de subprocesso gerenciado** com: `Popen` em **process group** +
  **die-with-parent** + parada graciosa **SIGTERM→timeout→SIGKILL** + **PID file / stale-kill no boot**
  + **watchdog com rate-limit** + **readiness probe**. **Recomendação:** o **core constrói isso e o
  próprio core passa a usá-lo para o GOWA** — endurecendo o que já existe (hoje a limpeza de órfão é
  `pkill`/`taskkill` externo; o stale-kill resolve o conflito de sessão WhatsApp de forma nativa). E o
  serviço é **exposto aos plugins** → é exatamente isto que **habilita o GOWA-como-plugin**.

#### Síntese: caminho e decisão

| Necessidade do provider | Plugin com qual capacidade | Cleanup | Risco de órfão |
|---|---|---|---|
| **Webhook-only** (Cloud API, Telegram-webhook) | ponto de extensão de canal (já em §3.4.2/§3.4.3) | nenhum (stateless) | nenhum |
| **Polling leve** (Telegram long-poll, IMAP) | (i) lifecycle + (ii) supervisor de tasks | `task.cancel()` | nenhum (mesmo processo) |
| **Subprocesso** (GOWA) | (i) lifecycle + (iii) subprocesso gerenciado | SIGTERM→SIGKILL + killpg | mitigado por die-with-parent + stale-kill |

**Conclusão (revisada):** o contrato tem `start()/stop()/status()/healthcheck()`, mas o **supervisor é do
core** — e o core deve **ganhar (i)+(ii)+(iii)**, as três como **infraestrutura core** (ver a nota no
topo de §3.4.4). Com **(i)+(ii)** já se tem **Cloud API, Telegram e e-mail como plugins**. **(iii)** é o
que falta para o **GOWA virar plugin** — e, de quebra, endurece o subprocesso. **Decisão de produto
(2026-06-18): o GOWA já nasce como provider-plugin no v1** (não built-in temporário). **Ordem de
construção recomendada:** **(i)→(ii) primeiro**, validadas com um provider *barato* (sem subprocesso);
**(iii) + GOWA-plugin por último**, porque o subprocesso é o caso mais difícil. (Perguntas em aberto
correlatas em §10.)

### 3.4.5 Credenciais / settings do canal

Reusar parcialmente o mecanismo de **settings declarativas** de plugin (Pydantic `Settings`), mas com
ressalva: settings de plugin persistem em `config` com prefixo `plugin.<id>.<campo>` — adequado para
**preferências do provider** (ex.: "modo polling vs webhook" no Telegram), **mas não** para
**segredos por canal**. Tokens/credenciais por número vivem em **`channel_credentials` (tabela CORE,
cifrada — §5.7)**, porque (a) são por-canal e não por-plugin, (b) precisam de cifragem em repouso e
mascaramento, (c) são compartilhados entre core e provider. **Recomendação:** o **schema do formulário
de credenciais** pode ser declarado pelo provider (lista de chaves esperadas + labels, como a tabela do
§7 "chaves esperadas por provider"), mas a **persistência** é via API do core sobre `channel_credentials`,
não via `config_repo` do plugin.

### 3.4.6 Migrations — tabelas de canal são CORE

O migrator de plugin **força** o prefixo `plugin_<id>_*` em toda `CREATE TABLE` (recusa o contrário).
Mas `channels` e `channel_credentials` são tabelas de **domínio core** — não devem (nem podem) ser
criadas por um plugin. **Recomendação:** o provider-plugin **não cria tabelas de canal**; ele **lê/grava**
nas tabelas core através de uma **API que o core expõe** ao provider (ex.: métodos no `ctx`/registry:
`get_channel(channel_id)`, `get_credential(channel_id, key)`, `set_status(channel_id, ...)`). Se o
provider-plugin tiver estado **próprio** (ex.: cache de offsets do long-poll do Telegram), aí sim usa
suas tabelas `plugin_<id>_*` normais. Em resumo: **tabelas de canal = core (Alembic); estado interno do
provider = `plugin_<id>_*`.** (Mesmo princípio do doc 06: tabelas de domínio no core via Alembic, sem o
prefixo de plugin.)

### 3.4.7 Recomendação final

**Criar o ponto de extensão "channel provider" já no MVP**, e construir as capacidades de runtime que o
liberam para todos os tipos de provider. Concretamente:

1. **Contrato + `ChannelRegistry` + tabelas `channels`/`channel_credentials` no CORE.**
2. **GOWA como provider-plugin já no v1** (decisão 2026-06-18, §3.4.8) — registrado pela MESMA API que
   qualquer plugin usa (`entry.channels` + `CHANNEL_PROVIDERS`), consumindo as capacidades de runtime do
   core via o `context`. É o **último** provider a ser construído, **depois** de (i)+(ii) validadas num
   caso barato sem subprocesso (provider de teste ou Cloud API webhook-only), porque depende da capacidade
   (iii). Assim quem só usa Cloud API/Telegram/e-mail **não instala nem roda** o GOWA.
3. **Cloud API** entra como provider (a) webhook-only — pode já ser plugin, com o **contrato** das peças
   de segurança (handshake, cifragem, templates/janela) no core (§3.4.8).
4. **Novo campo de manifest `entry.channels` + export `CHANNEL_PROVIDERS`** wired no loader, para que
   **Telegram entre como plugin sem refactor** — webhook-only ou polling leve, gerenciado pelo
   **supervisor de tasks** do core (§3.4.4 (ii)).
5. **Construir as capacidades de runtime do §3.4.4 na ordem (i) → (ii) → (iii):** lifecycle de plugin
   aguardado, depois supervisor de tasks (destrava Telegram/e-mail e permite **validar (i)+(ii) num
   provider barato sem subprocesso**), e por fim o serviço de subprocesso gerenciado (destrava o
   **GOWA-como-plugin**). As três são CORE — infraestrutura que os plugins consomem (§3.4.4, nota).

Resultado: o contrato nasce exercitado por plugins (incluindo o GOWA no v1); nenhuma decisão fecha a porta
de "qualquer provider pode ser plugin".

### 3.4.8 Decisão (2026-06-18): GOWA nasce como provider-plugin no v1

**Não há barreira permanente** ao GOWA ser plugin. O que há é uma **dependência de capacidade** (a (iii)
"subprocesso gerenciado"). Sobre isso, o cliente **decidiu**:

> **DECISÃO (2026-06-18):** o GOWA **já nasce como provider-plugin no v1**, sobre as três capacidades de
> runtime do core (§3.4.4). **Não** será escrito como built-in temporário "cravado no lifespan" para ser
> movido depois.
>
> **Justificativa:** escrever o GOWA "do jeito antigo" (acoplado ao lifespan do core) e depois reescrevê-lo
> como plugin é trabalho jogado fora — e arrisca cristalizar de novo o acoplamento que este doc inteiro se
> propõe a quebrar (§1/§3.0). Implementar o GOWA já atrás do contrato de provider, consumindo as
> capacidades core via o `context`, garante que a meta de produto ("quem só usa Cloud API/Telegram/e-mail
> **não roda** o GOWA") seja atendida desde o v1, sem uma migração futura.

Como decorrência:

- **Cloud API, Telegram e e-mail** também são providers-plugin, viabilizados por (i)+(ii).
- **Peças de segurança/infra da Cloud API (handshake `hub.challenge`, cifragem de tokens,
  janela/templates):** o **contrato e a tabela `channel_credentials` cifrada são core** (§3.4.5/§3.4.6,
  §5); a **implementação** do provider Cloud pode viver em plugin sem que segredos vazem, porque persiste
  via a API do core, não em `config` plaintext.

#### Sequenciamento técnico: a ordem de construção ≠ a decisão de produto

A decisão de produto ("GOWA é plugin no v1") **não** dita a ordem de construção. O GOWA é o caso **mais
difícil**: subprocesso do SO (capacidade iii), com armadilhas específicas de SO — **matar a árvore de
processos** (process group + `os.killpg`), **die-with-parent** (`PR_SET_PDEATHSIG` no Linux / **Job
Object** com `KILL_ON_JOB_CLOSE` no Windows, defesa contra o hard-exit do toggle) e **stale-kill no boot**
(matar instância órfã antes de subir, **para não duplicar a sessão WhatsApp**). Validar as capacidades
(i)+(ii) **em cima do GOWA** seria depurar a fundação e o caso mais perigoso ao mesmo tempo.

**Recomendação de ordem:** construir e **validar** (i) lifecycle e (ii) supervisor de tasks com um
provider **simples primeiro** — por exemplo um **provider de teste** (loop trivial) ou o **Cloud API
webhook-only** (não tem subprocesso). Só com (i)+(ii) provadas, **fechar (iii) subprocesso gerenciado +
o GOWA-plugin**. Em resumo:

- **Decisão de produto:** GOWA é **plugin** no v1.
- **Ordem de construção:** **(i)+(ii)** (validadas num caso barato, sem subprocesso) **→ (iii) + GOWA-plugin**.

---

## 4. GOWA multi-número (1 processo com N devices vs N processos)

GOWA virou multi-device no **v8**: "you can now connect and manage multiple WhatsApp accounts
simultaneously in a single server instance"
([readme GOWA](https://github.com/aldinokemal/go-whatsapp-web-multidevice/blob/main/readme.md)).
Endpoints relevantes (v8):

- `GET /devices`, `POST /devices`, `GET /devices/:id`, `DELETE /devices/:id`
- `GET /devices/:id/login` (QR por device), `POST /devices/:id/logout`
- Toda chamada device-scoped exige **`X-Device-Id`** (header) **ou** `device_id` (query). Se só houver
  1 device, ele é usado como default ([readme GOWA](https://github.com/aldinokemal/go-whatsapp-web-multidevice/blob/main/readme.md);
  [Discussion #572](https://github.com/aldinokemal/go-whatsapp-web-multidevice/discussions/572)).
- WebSocket: `/ws?device_id=<id>`.
- **Webhook**: payloads do v8 incluem um campo de topo **`device_id`** identificando qual device
  recebeu o evento (ex.: `{"event":"message","device_id":"628...@s.whatsapp.net","payload":{...}}`).

### Opção A — 1 processo GOWA, N devices (RECOMENDADA)

```
GOWAManager (porta 3000)
   ├── device "comercial"  (X-Device-Id: comercial)
   ├── device "suporte"    (X-Device-Id: suporte)
   └── device "financeiro" (X-Device-Id: financeiro)
```

| Prós | Contras |
|---|---|
| Uma porta, um watchdog, um binário. Menos RAM (Go é eficiente, e o ponto forte do projeto é uso de memória). | **Crash do processo derruba todos os devices** ao mesmo tempo. |
| O GOWA já roteia internamente por device; é o modo "oficial" do v8. | Sessões compartilham o mesmo store/processo — menos isolamento. |
| Add/remove de número = `POST/DELETE /devices`, sem mexer em portas/processos. | Relogin/QR de um device durante operação dos outros (em geral OK no v8). |

**Como o webhook diferencia devices**: o payload traz `device_id` no topo → `parse_inbound` mapeia
para `channel_id`. **Mais robusto ainda**: se a versão permitir webhook por device, registrar
`--webhook .../api/webhook/gowa/<channel_id>` por device e usar o path. Caso contrário, um único
`/api/webhook/gowa` que lê `body["device_id"]`.

### Opção B — N processos GOWA (um por número)

```
GOWAManager(3001) → device comercial
GOWAManager(3002) → device suporte
GOWAManager(3003) → device financeiro
```

| Prós | Contras |
|---|---|
| Isolamento total: crash de um não afeta os outros. | N portas para alocar (e o problema de "sockets fantasma no Windows" se multiplica). |
| Webhook trivialmente roteável por porta/path. | N watchdogs, N processos = mais RAM/CPU. |
| | Manager precisa virar "pool de managers" — mais código de orquestração. |

**Recomendação**: **Opção A** como MVP (alinhado ao design do v8 e ao perfil server-hosted, empresa
única), deixando a **Opção B** como fallback opcional para quem precisa de isolamento forte (campo
`isolation` no canal, escolhendo "processo dedicado").

### O que muda no `manager.py` e no `client.py`

- `GOWAManager` continua subindo **1** processo, mas com `--webhook` por device (ou um webhook
  multiplexado). Watchdog inalterado (supervisiona o processo, não os devices).
- `GOWAClient` deixa de ter `device_id` fixo: passa a receber `device_id` no construtor (ou por
  chamada). Um `GOWAChannel(Channel)` envolve o `GOWAClient` com `device_id=self.external_device_id`.
- `ensure_device()` deixa de pegar `devices[0]`; passa a garantir **o device daquele canal** (criar
  se não existir, com o `device_id` do canal).

### Modelo de dados (tabela `channels`)

```sql
CREATE TABLE channels (
    id              TEXT PRIMARY KEY,          -- ex. "comercial" (snake_case)
    provider        TEXT NOT NULL,             -- gowa | whatsapp_cloud | telegram | ...
    display_name    TEXT NOT NULL,             -- "Comercial", mostrado na UI
    enabled         INTEGER NOT NULL DEFAULT 1,
    -- GOWA
    gowa_device_id  TEXT,                      -- X-Device-Id desse canal
    gowa_isolation  TEXT DEFAULT 'shared',     -- shared | dedicated_process
    -- conexão / status (cache)
    connected       INTEGER NOT NULL DEFAULT 0,
    logged_in       INTEGER NOT NULL DEFAULT 0,
    own_phone       TEXT,                      -- número conectado (digits)
    last_error      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- credenciais fora da linha principal (segredos; ver §5 e §7)
CREATE TABLE channel_credentials (
    channel_id  TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,                 -- access_token | phone_number_id | waba_id |
                                               -- verify_token | bot_token | ...
    value       TEXT NOT NULL,                 -- cifrado em repouso (ver §7)
    PRIMARY KEY (channel_id, key)
);
```

A conversa no inbox ganha `channel_id` (FK), fechando o ciclo entrada→conversa→saída
(ver [`01-inbox-e-conversas.md`](01-inbox-e-conversas.md)).

---

## 5. WhatsApp Cloud API (oficial, modo tradicional)

Fluxo **tradicional** (cliente cria o próprio app na Meta — NÃO embedded signup): o usuário gera as
credenciais no painel da Meta e **cola no WhatsBot**.

### 5.1 O que o usuário precisa colar

| Campo | Onde obtém | Observações |
|---|---|---|
| **Phone Number ID** | Meta App Dashboard → WhatsApp → API Setup. É **diferente** do número de telefone em si; é o ID usado nas chamadas de API ([referência webhook/setup](https://docs.sms-magic.com/3gRa19kt0b4-messaging-guide-ott-channels/BMoPP0zQ3Ps-whatsapp-cloud-api-setup)). | usado na URL de envio `POST /{phone_number_id}/messages`. |
| **WABA ID** (WhatsApp Business Account ID) | Business Manager | necessário para listar/sincronizar templates. |
| **Access Token** | **System User permanent token** em `business.facebook.com → Configurações → Usuários do sistema`. O token de teste expira em 24h; produção usa o permanente, que não expira até ser revogado ([resumo de tokens](https://forum.bubble.io/t/verifying-the-endpoint-for-whatsapp-webhooks/239045)). | **segredo** — mascarar e cifrar. |
| **Verify Token** | string arbitrária que o usuário define | usado só na verificação do webhook (handshake). |
| **App Secret** (opcional) | App Dashboard | para validar a assinatura `X-Hub-Signature-256` dos webhooks. |

### 5.2 Verificação do webhook (handshake `hub.challenge`)

Ao cadastrar a URL do webhook no painel da Meta, a Meta faz um **GET** com
`hub.mode=subscribe`, `hub.verify_token=<o que você definiu>` e `hub.challenge=<string aleatória>`.
O endpoint deve validar `mode` + `verify_token` e **responder o `hub.challenge` em texto puro com HTTP
200** ([guia de verificação](https://forum.bubble.io/t/verifying-the-endpoint-for-whatsapp-webhooks/239045);
[gist de exemplo](https://gist.github.com/mikedidomizio/fcef93d270a14ce90273322a7c3f3187)).

```python
# rota por canal — verify_token vem da tabela channel_credentials do canal
@app.get("/api/webhook/cloud/{channel_id}")
async def verify(channel_id: str, request: Request):
    p = request.query_params
    cred = get_channel_credential(channel_id, "verify_token")
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == cred:
        return PlainTextResponse(p.get("hub.challenge", ""))  # ECO do challenge, 200
    return PlainTextResponse("forbidden", status_code=403)
```

Mensagens entrantes chegam depois via **POST** na mesma URL, em estrutura aninhada
`entry[].changes[].value.messages[]` + `value.metadata.phone_number_id` (que identifica o canal).

### 5.3 Normalização de payload → interface comum

`WhatsAppCloudChannel.parse_inbound(raw)` percorre `entry[].changes[].value` e produz os eventos do
§3.1. Pontos de mapeamento:
- `value.metadata.phone_number_id` → `channel_id` (resolve qual canal).
- `value.messages[].from` → `chat_id`/`sender_id` (número do cliente, sem `@s.whatsapp.net`).
- `value.contacts[].profile.name` → `sender_name`.
- `messages[].type` (`text|image|audio|...`) → `media_type`; mídia vem por **media ID** que precisa de
  um `GET /{media_id}` (com token) para obter a URL temporária e baixar — diferente do GOWA, que já
  entrega o arquivo no disco.
- `value.statuses[]` → eventos `receipt` (sent/delivered/read).

### 5.4 Diferenças importantes vs GOWA

| Aspecto | GOWA (não-oficial) | WhatsApp Cloud API (oficial) |
|---|---|---|
| Conexão | **QR code** (WhatsApp Web), pode cair/deslogar | **Token**, sem QR, estável |
| Risco | conta pessoal/business pode ser banida (não-oficial) | suportado pela Meta |
| Mídia entrante | arquivo já baixado no disco (`media_path`) | **media ID** → `GET /{id}` → URL temporária → baixar |
| Mídia saída | upload multipart do arquivo | por **URL pública** ou por **media ID** (upload prévio) |
| Janela de 24h | não existe (manda quando quiser) | **existe** (ver §5.5) |
| Templates | não há | **obrigatórios fora da janela** (ver §5.5) |
| Custo | grátis (infra própria) | **pago por mensagem/template** (ver §5.6) |
| Grupos | sim | **não** (Cloud API não atende grupos) |
| Typing/presence | sim | limitado/diferente |

A interface absorve essas diferenças: `get_qr()` retorna `None` no Cloud; `send_media` recebe
`path_or_url` (o adapter Cloud aceita URL/ID, o GOWA aceita path); `send_template` só existe no Cloud.

### 5.5 Janela de 24h e templates (HSM)

- Toda vez que o cliente manda mensagem, abre-se uma **janela de atendimento de 24h** durante a qual
  a empresa pode responder com **mensagens livres** (texto/mídia) ([guia janela/templates — smsmode](https://www.smsmode.com/en/whatsapp-business-api-customer-care-window-ou-templates-comment-les-utiliser/)).
- **Fora** da janela (ou para iniciar conversa do zero), só é possível enviar **message templates**
  pré-aprovados pela Meta (os "HSM"). Categorias: **utility** (recibos/lembretes), **authentication**
  (OTP), **marketing** (promoções) ([respond.io pricing](https://respond.io/blog/whatsapp-business-api-pricing)).
- Janela estendida para **72h** quando o cliente chega por Click-to-WhatsApp ads / botão da Página
  ([ycloud — atualização de preços](https://www.ycloud.com/blog/whatsapp-api-pricing-update)).

Implicação para o WhatsBot: o adapter Cloud precisa rastrear o "último inbound" por conversa para
saber se a janela está aberta. Se fechada e o operador/IA tentar enviar texto livre → bloquear e
oferecer envio de template aprovado. Templates aprovados podem ser sincronizados do WABA e guardados
(JSONB/coluna) — é exatamente como o Chatwoot faz (`message_templates`)
([Chatwoot WhatsApp Channel](https://deepwiki.com/chatwoot/chatwoot/7.4-whatsapp-channel)).

### 5.6 Custo (2025/2026)

- **Mudança em 1º de julho de 2025**: a Meta migrou de cobrança **por conversa** para cobrança
  **por mensagem** — cada template entregue é cobrado individualmente
  ([ycloud](https://www.ycloud.com/blog/whatsapp-api-pricing-update); [respond.io](https://respond.io/blog/whatsapp-business-api-pricing)).
- **Grátis**: mensagens de serviço (texto livre) dentro da janela de 24h; conversas iniciadas por
  Click-to-WhatsApp/botão da Página (janela 72h); chamadas recebidas.
- **Pago**: templates fora da janela, com tarifa por **categoria** (marketing > authentication >
  utility) e por **país** do destinatário ([documentação oficial de pricing da Meta](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)).
- BSPs/intermediários cobram markup adicional — irrelevante no modo tradicional (cliente fala direto
  com a Meta).

### 5.7 Onde guardar os tokens (segredos)

- Tabela `channel_credentials` (§4), **cifrada em repouso** (ex.: Fernet/`cryptography` com chave de
  app vinda de env `WHATSBOT_SECRET_KEY`; em Docker/Coolify, da `.env`). NÃO em `config` plaintext.
- **Mascarar** na API (`/api/channels` devolve `access_token: "••••1234"`), como o `/api/config` já
  faz com a API key do LLM.
- **Nunca logar** o token. Cuidado com `payload["raw"]` em filtros de plugin.
- Acesso a tokens limpos restrito a admin (ver [`03-rbac-usuarios-permissoes.md`](03-rbac-usuarios-permissoes.md)).

---

## 6. Telegram (futuro — esboço)

Telegram encaixa na mesma interface com baixo atrito ([Telegram Bot API](https://core.telegram.org/bots/api)):

- **Credencial única**: `bot_token` (de @BotFather) → `channel_credentials`.
- **Entrada**: dois modos mutuamente exclusivos —
  - **Long polling** (`getUpdates` em loop; Telegram segura a conexão até haver update) — bom para dev
    e para quem não quer expor URL pública;
  - **Webhook** (`setWebhook` aponta para `https://.../api/webhook/telegram/{channel_id}`; Telegram faz
    POST com o `Update`).
  São excludentes: não dá para usar polling com webhook setado
  ([long polling vs webhook — GramIO](https://gramio.dev/updates/webhook)).
- **Saída**: `sendMessage`, `sendPhoto`, `sendDocument`, etc. — mapeiam direto em `send_text`/`send_media`.
- `chat_id` do Telegram (inteiro) vira o `chat_id` normalizado. Sem janela de 24h, sem templates, sem
  QR. `get_qr()` → `None`; `send_template` → `NotImplementedError`.

Isto **valida a interface**: GOWA precisa de QR + subprocess; Cloud precisa de token + janela +
templates; Telegram precisa de token + polling/webhook. A interface do §3.2 cobre os três sem
`if provider == ...` no core — cada adapter resolve suas particularidades.

---

## 7. Impacto no schema e nos pontos de código

### Schema (Alembic)
- Nova migration: tabelas `channels` e `channel_credentials` (§4).
- `contacts`/conversa ganham `channel_id` (FK) — ver [`01-inbox-e-conversas.md`](01-inbox-e-conversas.md).
  Migração: instalação atual de 1 número vira **um canal default** (`provider="gowa"`,
  `gowa_device_id="whatsbot"`, herdando a sessão existente); todas as conversas recebem esse `channel_id`.
- `messages` pode ganhar `channel_id` (denormalizado) para relatórios por número e para idempotência
  por `(channel_id, external_msg_id)`.

### Tabela de credenciais por canal

| `provider` | chaves esperadas em `channel_credentials` |
|---|---|
| `gowa` | (nenhuma — sessão fica no store do GOWA; só `gowa_device_id` na `channels`) |
| `whatsapp_cloud` | `phone_number_id`, `waba_id`, `access_token`, `verify_token`, `app_secret?` |
| `telegram` | `bot_token`, `webhook_secret?` |

### Código
- **GOWA → provider-plugin (decisão 2026-06-18, §3.4.8):** a lógica de hoje (`gowa/manager.py`,
  `gowa/client.py`) é **extraída para um plugin** (`storages/plugins/gowa/` com `channels.py` exportando
  `CHANNEL_PROVIDERS = [GOWAChannel]`). `GOWAClient` passa a ser parametrizado por `device_id`;
  `GOWAChannel(Channel)` envolve o client e consome o **serviço de subprocesso gerenciado do core**
  (capacidade iii) em vez de gerenciar o `Popen`/watchdog por conta própria. É o **último** provider a
  ser construído — depois de (i)+(ii) validadas num caso barato.
- **Novo pacote `channels/`** (ou `providers/`): `base.py` (interface `Channel` — importável de forma
  estável por plugins, §3.4.2), `registry.py` (`ChannelRegistry`: `provider name → Provider class` **e**
  `channel_id → Channel instance`; substitui o `gowa_client` global em `deps`; expõe ao core/providers a
  API de leitura/escrita de `channels`/`channel_credentials`, §3.4.6). GOWA, Cloud API (a webhook-only) e
  Telegram/IG/Email entram **todos como plugin** via `entry.channels` (§3.4), com o **contrato** das peças
  de segurança da Cloud no core (§3.4.8).
- **Capacidades de runtime no core (§3.4.4):** (i) **lifecycle de plugin aguardado** —
  `setup(ctx)`/`teardown(ctx)` chamados/aguardados pelo host (parar o hard-exit no disable, ou rodar
  teardown antes do `os._exit`); (ii) **supervisor de tasks de fundo** — generalizar as 4 tasks
  hardcoded do lifespan num registry com restart classificado + rate-limit, exposto a plugins via `ctx`;
  (iii) **serviço de subprocesso gerenciado** — process group + die-with-parent + SIGTERM→SIGKILL + PID
  file/stale-kill + watchdog/rate-limit + readiness probe; é o que o **GOWA-plugin consome** para subir o
  binário. As três capacidades são **core** (infraestrutura que os plugins consomem — §3.4.4, nota).
- **`plugins/loader.py` + `plugins/manifest.py` + `plugins/context.py`**: novo campo `entry.channels` no
  manifest e wiring que importa `CHANNEL_PROVIDERS` e chama `channel_registry.register_provider(cls)`
  (§3.4.2), espelhando o registro de tools/events/filters; expor no `ctx` o loop/`stop_event` e o
  registro de tasks/subprocesso (hoje o `ctx` não os expõe — §3.4.4).
- **`server/routes/webhook.py`**: rotas por canal `/{provider}/{channel_id}`; cada uma chama
  `registry.get(channel_id).parse_inbound(raw)` e injeta `channel_id` no pipeline. Verificação GET
  `hub.challenge` para Cloud.
- **`deps`**: `gowa_client` único → `channel_registry`. O handler responde via
  `registry.get(conversa.channel_id).send_text(...)` em vez do client global.
- **`config/settings.py`**: porta GOWA, isolamento; constantes Cloud (graph URL base) com override env.
- **Novas rotas**: `/api/channels` (CRUD), `/api/channels/{id}/qr`, `/api/channels/{id}/status`,
  `/api/channels/{id}/templates` (Cloud).

---

## 8. Impacto no frontend

Hoje QR/status são globais (uma instalação = um número). Vira **uma tela de Canais**:

- **Listagem de canais** (`/channels` ou aba em Settings): cards com `display_name`, provider, status
  (conectado / aguardando QR / erro), número conectado. Ações: adicionar, desativar, remover.
- **Adicionar canal** → escolher provider:
  - **GOWA**: cria o device, mostra **QR** (reaproveita o componente de QR atual, agora por canal) e
    faz polling de status até logar.
  - **WhatsApp Cloud**: **formulário de token** — Phone Number ID, WABA ID, Access Token (mascarado),
    Verify Token (gerado/sugerido pela UI), e exibe a **URL de webhook** que o usuário deve colar no
    painel da Meta (`https://<host>/api/webhook/cloud/{channel_id}`). Aba extra de **templates**
    aprovados.
  - **Telegram** (futuro): campo `bot_token` + URL de webhook.
- **Seleção de canal** no inbox/chat: como cada conversa carrega `channel_id`, a UI mostra de qual
  número veio e responde pelo mesmo. Onde for criar conversa nova, escolher o canal de origem.
- **Modo escuro / `wa-*` / `.wa-field`**: telas novas seguem as regras de tema do projeto.

Permissões: criar/editar canal e ver tokens são ações de admin (ver [`03-rbac-usuarios-permissoes.md`](03-rbac-usuarios-permissoes.md)).

---

## 9. Faseamento / MVP

> O faseamento separa **duas trilhas**: (A) a abstração de canal + multi-número (entregas de produto) e
> (B) as **capacidades de runtime** do §3.4.4 (i lifecycle, ii supervisor de tasks, iii subprocesso
> gerenciado), **todas core**, sobre as quais **todo provider — inclusive o GOWA — roda como plugin**.
> **Decisão de produto (2026-06-18):** o GOWA já nasce como provider-plugin no v1 (§3.4.8). **Ordem de
> construção:** (i)+(ii) validadas num caso barato (sem subprocesso) **→** (iii) + GOWA-plugin por último.

1. **Fase 0 — Abstração + canal único + ponto de extensão**: introduzir a interface `Channel`, o
   `ChannelRegistry`; **extrair o GOWA de dentro do core para trás do contrato** (§3.0); **criar o ponto
   de extensão de plugin "channel provider"** (campo de manifest `entry.channels` + export
   `CHANNEL_PROVIDERS` + wiring no loader, §3.4.2); e migrar a instalação atual para "1 canal default".
   Paga a dívida de acoplamento cedo e garante que o ponto de extensão é real e testado, não teórico.
2. **Fase 1 — Capacidades de runtime core (i)+(ii)**: ganchos `setup(ctx)`/`teardown(ctx)` **aguardados**
   pelo host (parar de usar hard-exit no disable, ou rodar teardown antes do `os._exit`); generalizar as 4
   tasks hardcoded do lifespan num **supervisor de tasks de fundo** com restart classificado + rate-limit
   (§3.4.4 i/ii). **Validar essas duas capacidades com um provider barato sem subprocesso** — um provider
   de teste e/ou o Cloud API webhook-only. Destrava providers **webhook-only e polling leve como plugin**.
3. **Fase 2 — WhatsApp Cloud API (provider-plugin webhook-only)**: verificação `hub.challenge`,
   normalização, templates + janela 24h, cifragem de tokens, formulário na UI. Entra como **plugin** via
   `entry.channels`, com o contrato das peças de segurança no core (§3.4.8). Serve também de **caso barato
   que valida (i)+(ii)** sem mexer em subprocesso.
4. **Fase 3 — Serviço de subprocesso gerenciado (capacidade iii core) + GOWA como provider-plugin**:
   construir o gerenciador de subprocesso (process group + die-with-parent + SIGTERM→SIGKILL + PID
   file/stale-kill + watchdog/rate-limit + readiness probe, §3.4.4 iii) **no core**; então **empacotar o
   GOWA como provider-plugin** que consome esse serviço — com tabela `channels`, `POST /devices` por canal,
   webhook roteado por `device_id`/path e tela de Canais com QR por device (multi-número). Por ser plugin,
   quem só usa Cloud API/Telegram/e-mail **não instala nem roda** o GOWA. É o **último** a ser construído,
   por ser o caso mais difícil (subprocesso + armadilhas de SO).
5. **Fase 4 — Telegram (provider-plugin)** e depois Instagram/Messenger/Email: mais providers pelo ponto
   de extensão de plugin (`entry.channels`), validando webhook-only / polling leve sobre o supervisor de
   tasks (Fase 1) — sem refactor do core.

---

## 10. Perguntas em aberto

1. **Webhook por device no GOWA**: a v8.5.0 permite `--webhook` distinto **por device**, ou só um
   webhook global por processo? Se for global, dependemos do campo `device_id` no payload (confirmar
   que vem em **todos** os tipos de evento, não só `message`). Validar contra a build empacotada.
2. **Isolamento**: vale a pena suportar `dedicated_process` (Opção B do §4) já no MVP, ou deixar para
   quando alguém precisar?
3. **Cifragem de segredos**: de onde vem a chave mestra (`WHATSBOT_SECRET_KEY`)? Em EXE Windows não há
   `.env` gerenciado — gerar e guardar onde (DPAPI? arquivo protegido)?
4. **Media da Cloud API**: baixar e cachear no mesmo esquema de `media_path` do GOWA (para o player do
   inbox funcionar igual), ou referenciar a URL temporária? (A URL expira.)
5. **Janela de 24h na UI**: como sinalizar ao operador que a janela fechou e só dá para mandar template?
   Bloquear o input e oferecer um seletor de template?
6. **Idempotência**: usar `(channel_id, external_msg_id)` como chave única para deduplicar reentregas
   de webhook (Meta/Telegram reenviam em falha de ACK)?
7. **Aprovação/sincronização de templates**: o WhatsBot sincroniza templates do WABA periodicamente, ou
   o usuário cadastra manualmente? Submissão de novos templates para aprovação fica fora de escopo?
8. **Número conectado x canal**: como descobrir/exibir o número real de um device GOWA de forma
   confiável (hoje `get_own_number()` é best-effort)?
9. ~~**Vale mover o GOWA para plugin no v1, ou deixá-lo built-in temporariamente?**~~ **DECIDIDA
   (2026-06-18):** o GOWA **nasce como provider-plugin no v1** (§3.4.8), sobre as três capacidades de
   runtime core — **não** será built-in temporário. Motivo: escrever o GOWA acoplado ao lifespan e
   reescrevê-lo depois é trabalho jogado fora e recristaliza o acoplamento que o doc remove; já nascer
   plugin atende a meta "quem não usa GOWA não o roda" desde o v1. (As três capacidades — i lifecycle, ii
   supervisor de tasks, iii subprocesso gerenciado — continuam **core**; é o *provider* que é plugin.)
10. **Em que ordem construir (i) lifecycle, (ii) supervisor de tasks e (iii) subprocesso gerenciado?**
    **Recomendação (adotada): (i)→(ii) primeiro, validadas num provider BARATO sem subprocesso** (provider
    de teste e/ou Cloud API webhook-only — cleanup = `task.cancel()`, sem risco de órfão); **(iii) +
    GOWA-plugin por último**, por ser o caso mais arriscado (processo do SO: matar a árvore,
    `PR_SET_PDEATHSIG`/Job Object, stale-kill no boot para não duplicar a sessão WhatsApp). Pontos finos
    ainda a definir: ganchos `setup/teardown` **aguardados** (parar o hard-exit no disable ou rodar
    teardown antes do `os._exit`); rate-limit/backoff de restart por task; como o disable do plugin derruba
    a task/subprocesso **sem matar o processo todo**; como sinalizar saúde do canal na tela de Canais (§8).
11. **Forma exata do contrato de export de plugin** (§3.4.2): `CHANNEL_PROVIDERS = [cls, ...]` +
    `entry.channels` no manifest é o formato final, ou convém também permitir registro imperativo
    (função `register(registry)`)? E onde o plugin importa a base `Channel` de forma estável (caminho
    de import do core exposto ao `whatsbot_plugins.<id>`)?
12. **API do core para o provider ler/gravar tabelas de canal** (§3.4.6): que superfície expor
    (`get_channel`, `get_credential`, `set_status`, …) e como passá-la ao provider (via `ctx` do
    plugin? via o próprio `ChannelRegistry`)? Garantir que o provider-plugin **não** acesse
    `channels`/`channel_credentials` por SQL direto.

---

## 11. Aprofundamento: whatsmeow, GOWA multi-número e Evolution API como base de referência

> Esta seção aprofunda o §4 (GOWA multi-número) e responde uma pergunta concreta do cliente:
> **como ficaria o suporte a vários números NÃO-OFICIAIS** e o que dá para **"tirar de base" da
> Evolution API**. Tudo aqui é pesquisa/proposta — não descreve código existente.

### 11.1 whatsmeow — a biblioteca por baixo do GOWA

O GOWA é um wrapper REST/WebUI sobre a lib Go **whatsmeow** (`go.mau.fi/whatsmeow`, de Tulir Aarnio),
que implementa o protocolo WhatsApp Web multidevice. Entender o whatsmeow é entender o teto do que o
GOWA consegue fazer — e o que ganharíamos indo direto nele.

**Modelo de multi-número (multi-conta) no whatsmeow** — um processo Go gerencia **N contas** assim:

- **`sqlstore.Container`** é o wrapper sobre um banco SQL (SQLite ou Postgres) criado **uma vez por
  processo** (`sqlstore.New(ctx, "sqlite3"|"postgres", dsn, log)`). Roda migrations internas
  (`Upgrade()`) e é o "container de devices".
- **Um `*whatsmeow.Client` por número**, todos compartilhando o **mesmo `Container`** (mesmo banco):
  ```go
  devices, _ := container.GetAllDevices(ctx)           // []*store.Device — um por número
  for _, dev := range devices {
      c := whatsmeow.NewClient(dev, log)                // um client por device
      c.AddEventHandler(func(evt any){ handle(dev.ID.String(), evt) })
      c.EnableAutoReconnect = true
      c.Connect()
  }
  ```
- **`store.Device`** representa o device persistido; `device.ID` é o **JID** (`nil` enquanto não pareado).
  `container.NewDevice()` cria em memória (gera noise/identity/signed-pre-key + registration ID);
  a persistência (`PutDevice`) acontece no pairing. Métodos: `GetAllDevices`, `GetDevice(jid)`,
  `GetFirstDevice`, `DeleteDevice`.
- **Persistência de sessão** = tabelas com prefixo `whatsmeow_` (`whatsmeow_device`, `..._sessions`,
  `..._identity_keys`, `..._pre_keys`, `..._sender_keys`, `..._app_state_*`, `..._contacts`,
  `..._chat_settings`, `..._lid_map`). **Cada conta é particionada por `our_jid`** — múltiplos números
  no mesmo banco não se interferem porque cada um só lê/escreve as rows do próprio JID.
- **Eventos** via `AddEventHandler` + type-switch: `*events.Message`, `*events.Receipt`,
  `*events.Connected`, `*events.Disconnected`, `*events.QR` (`v.Codes []string`),
  `*events.PairSuccess`, `*events.LoggedOut`, `*events.StreamReplaced`, `*events.TemporaryBan`
  (importante para multi-número — sinaliza ban temporário), `*events.ConnectFailure`,
  `*events.KeepAliveTimeout`. Os eventos `LoggedOut`/`StreamReplaced`/`TemporaryBan` implementam
  `PermanentDisconnect` → o client **não** reconecta sozinho.
- **Reconexão**: `EnableAutoReconnect` (default true), backoff linear `errors*2s`, hook
  `AutoReconnectHook(error) bool`.
- **Pairing**: QR via `client.GetQRChannel(ctx)` + `Connect()` (1º code ~60s, demais ~20s); ou
  **phone pairing code** via `client.PairPhone(ctx, phone, true, PairClientChrome, "Chrome (Linux)")`
  → link de 8 dígitos digitado no celular. (O GOWA expõe ambos: `/devices/{id}/login` e
  `/devices/{id}/login-with-code`.)

**"Multi-device do protocolo" ≠ "múltiplas contas"** — distinção que confunde:

| Conceito | O que é | JID | Suportado |
|---|---|---|---|
| **Linked device** (multidevice do protocolo) | O **mesmo número** em até 4 aparelhos (WhatsApp Web/Desktop). É o que o whatsmeow "é" — um linked device do seu número. | `...:2@s.whatsapp.net` (sufixo de device) | nativo |
| **Múltiplas contas** (o que queremos para multi-número) | **N números distintos**, independentes, cada um seu `*Client` + `store.Device`. | `5511...@s.whatsapp.net` (sem sufixo) | nativo (N devices num Container) |

Ou seja: o "multi-device" do §4 (GOWA v8) é, no fundo, **N linked devices de N números diferentes**
num mesmo Container whatsmeow — exatamente o caso de uso multi-número.

**Limites práticos** (sem limite hard-coded): ~2 goroutines de background por client
(`keepAliveLoop` + `handlerQueueLoop`, fila de 2048 frames) + uma WebSocket persistente + estado
cripto em memória. Relatos de produção rodam dezenas de números num binário; acima de ~100–200 contas
ativas convém monitorar goroutines/FDs/keepalive e considerar sharding por processo. SQLite+WAL
basta para um processo; **Postgres é obrigatório para múltiplas réplicas/processos** (mesma regra que
o WhatsBot já tem para Swarm). Há relato de goroutine leak sob carga com envios falhando
(whatsmeow #602, fechado como "not planned").

### 11.2 Até onde o GOWA vai — e onde para

O §4 já cobriu os endpoints (`/devices`, `X-Device-Id`, login/QR por device, `device_id` no webhook).
O aprofundamento revela as **lacunas estruturais** para um produto multi-número robusto:

| Lacuna no GOWA v8 | Detalhe | Impacto no WhatsBot |
|---|---|---|
| **Webhook é global, não por device** | Aceita múltiplas URLs (`--webhook a,b`) mas **todas recebem tudo de todos os devices**. Não há "device X → URL Y". | Confirma a "Pergunta em aberto" do §10.1: **não** dá webhook por device. Roteamento tem de ser por `body["device_id"]` no Python (e há também `session_id`, via PR #717). |
| **Sem auth/apikey por device** | Auth é global (`APP_BASIC_AUTH=user:pass,...`), server-wide. Sem credencial nem scope por número. | Quem alcança o GOWA alcança **todos** os números. Para multi-tenant real falta isolamento — relevante para o doc 03 (RBAC). |
| **Isolamento de falha parcial** | v8.1.0 corrigiu "data leak between devices" (#513, resolução de nomes cruzando devices) e mensagens duplicadas (#509). Crash do processo derruba **todos**. | Histórico de bugs cross-device → isolamento forte não garantido. Reforça a Opção B (processo dedicado) do §4 para quem precisa de isolamento. |
| **Reconexão/observabilidade por device rasas** | Há `POST /devices/{id}/reconnect`, mas sem estado fino (backoff, tentativas, razão da queda) nem métricas/health por device. | Difícil monitorar "qual número está instável" — importante quando se opera vários chips. |

**GOWA (subprocess) vs whatsmeow direto (serviço Go próprio):**

| Critério | GOWA subprocess (hoje) | whatsmeow embarcado (serviço Go próprio) |
|---|---|---|
| Esforço | Mínimo — binário pronto, REST documentada | Alto — protocolo Noise/protobuf/event loop/concorrência; meses vs dias |
| Toolchain | Nenhuma (binário pré-compilado em `bin/`) | Precisa de Go no build/CI; outro artefato para empacotar no EXE |
| Controle | Limitado à REST do GOWA | Total: ciclo de vida por `*Client`, backoff custom, auth por tenant, métricas |
| Webhook | Global (fan-out, roteia no Python) | Webhook/canal por número nativo, sem fan-out |
| Estabilidade | Boa, mas com bugs cross-device históricos | Você assume os bugs — e os conserta |
| Manutenção | Atualização do protocolo = trocar o binário GOWA | Você rebuilda/redeploya quando o whatsmeow atualiza |
| Fit com a stack Python | Excelente (já é o modelo atual; IPC HTTP localhost) | Pior — vira poliglota (Python + serviço Go) |

**Veredicto:** para o perfil do WhatsBot (server-hosted, empresa única, poucos números), **continuar
no GOWA** é o caminho certo — o custo/benefício de reescrever um serviço Go não se paga. Indo direto no
whatsmeow só faz sentido se o produto virar multi-tenant SaaS com isolamento/billing por número, aí as
lacunas de webhook-global e auth-por-device do GOWA passam a doer. Referência de implementação da
complexidade real: [DEV.to — multi-instance com Go/Echo/whatsmeow](https://dev.to/suharyadi2112/building-a-whatsapp-multi-instance-rest-api-with-go-echo-and-whatsmeow-5bln).

### 11.3 Evolution API — "tirar de base" do modelo de instância

A **Evolution API** (open-source) é a referência mais madura de "muitos números num servidor". É uma
stack **completamente diferente** do GOWA: **TypeScript/Node.js + Express**, usando **Baileys**
(`WhiskeySockets/Baileys`) como lib de WhatsApp — **NÃO** whatsmeow (Go). Não dá para reusar código;
dá para **copiar o design**.

**Conceito de "instance" = um número:** cada instância é uma conexão WhatsApp independente, orquestrada
por um singleton `WAMonitoringService` que mantém `instanceName → ChannelService`. Cada instância tem:
`instanceName` (id primário, string livre), `instanceId` (UUID), **token próprio**, rows isoladas no
Postgres (FK `instanceId`), chaves Redis com prefixo, e webhook/integrações independentes.

**Gestão de instâncias via REST** (v2) — todos com header `apikey: <GLOBAL_KEY>`:

| Método | Endpoint | Descrição |
|---|---|---|
| `POST` | `/instance/create` | Cria; body `{instanceName, integration, qrcode, number?}`; retorna `hash.apikey` (token da instância) + `qrcode.base64` |
| `GET` | `/instance/connect/{instanceName}` | Gera QR / pairing code |
| `GET` | `/instance/connectionState/{instanceName}` | Estado (`created`→`connecting`→`open`/`close`) |
| `GET` | `/instance/fetchInstances` | Lista todas (paginação `?page&offset`) |
| `PUT` | `/instance/restart/{instanceName}` | Reinicia sem perder credenciais |
| `DELETE` | `/instance/logout/{instanceName}` | Logout do WhatsApp |
| `DELETE` | `/instance/delete/{instanceName}` | Remove a instância |

**Autenticação em dois níveis** (ideia central a copiar): **(1) Global API Key**
(`AUTHENTICATION_API_KEY`) gerencia o ciclo de vida (`/instance/*`); **(2) Instance Token**
(`hash.apikey`, devolvido no create) opera mensagens (`/message/*`, `/chat/*`). Nunca se misturam.

**Roteamento de webhook por instância:** webhook **global** (`.env`: `WEBHOOK_GLOBAL_URL`,
`WEBHOOK_GLOBAL_WEBHOOK_BY_EVENTS`) **ou por instância** (`POST /webhook/set/{instanceName}` /
inline no create: `{enabled, url, webhookByEvents, events[]}`). Dois truques de design:

- **Campo `"instance"` em todo payload**: `{"event":"messages.upsert","instance":"meu_numero_1","data":{...}}`
  → um único endpoint receptor lê `payload["instance"]`, faz lookup e roteia. (É o análogo direto do
  `device_id`/`session_id` do GOWA — só que a Evolution **garante** o campo em todos os eventos.)
- **`webhookByEvents: true`**: acrescenta o nome do evento à URL (`.../webhook-MESSAGES_UPSERT`),
  permitindo handlers especializados por evento sem dispatcher central.

Eventos: `MESSAGES_UPSERT`, `MESSAGES_UPDATE/DELETE`, `SEND_MESSAGE`, `CONNECTION_UPDATE`,
`QRCODE_UPDATED`, `CONTACTS_*`, `CHATS_*`, `GROUPS_*`/`GROUP_PARTICIPANTS_UPDATE`, `PRESENCE_UPDATE`,
`CALL`, `INSTANCE_CREATE/DELETE`, `TYPEBOT_*`. HMAC-SHA256 para autenticidade nas versões recentes.

**Armazenamento:** Postgres/MySQL via **Prisma** (instâncias, mensagens, contatos, tokens) + **Redis**
(estado de conexão TTL 7d, dedupe de mensagens, pub/sub; `CACHE_REDIS_SAVE_INSTANCES=true` é
obrigatório para múltiplas réplicas — mesma lição do whatsmeow/Postgres e do Swarm do WhatsBot) +
**S3/MinIO** (mídia). `CACHE_REDIS_PREFIX_KEY` separa instalações no mesmo Redis. `DEL_INSTANCE` dá TTL
a instâncias inativas (descarregadas da memória, recarregadas sob demanda — anti memory-leak).

**Suporta também a Cloud API oficial** (integration `WHATSAPP-BUSINESS`) ao lado do Baileys
(`WHATSAPP-BAYLES`) no mesmo servidor, via interface comum `ChannelStartupService` — exatamente o
padrão "adapter por provider" do §3 deste doc, validado em produção.

**Integrações nativas** (só citar, como inspiração de roadmap): Chatwoot, Typebot, Dify, OpenAI,
Flowise, N8N; filas RabbitMQ/Kafka/SQS/NATS/Pusher; WebSocket (Socket.io); S3/MinIO.

**Mapeamento "instance" (Evolution) ↔ "channel/inbox" (docs 01/02):**

| Evolution API | Este doc (02) / doc 01 | Observação |
|---|---|---|
| `instanceName` | `channels.id` (§4) | id primário no path de todos os endpoints |
| `instanceId` (UUID) | (poderia ser uma coluna UUID extra) | id interno estável |
| `hash.apikey` (instance token) | `channel_credentials` (§4) | token por canal, cifrado |
| `integration` (`BAYLES`/`BUSINESS`) | `channels.provider` (gowa/whatsapp_cloud) | mesmo conceito de "tipo de canal" |
| `connectionState` (`open`/`close`/`connecting`) | `channels.connected/logged_in` (§4) | cache de status |
| `webhook.url` por instância | rota por canal `/api/webhook/{provider}/{channel_id}` (§3.3) | a Evolution prova que **path por instância OU campo `instance` no payload** funcionam — combinar os dois é o mais robusto |
| campo `"instance"` no payload | campo `device_id`/`session_id` do GOWA | mesma função de roteamento de entrada |
| Global API Key vs Instance Token | doc 03 (RBAC): admin gerencia canais; canal tem credencial própria | dois níveis de auth a adotar |

**Ideias de design a copiar (mesmo sem usar o código):**
1. **Dois níveis de chave** (global gerencia ciclo de vida; token por canal opera) — encaixa no doc 03.
2. **`channel_id` no path de todo endpoint** de operação — roteamento trivial no middleware.
3. **Campo de canal garantido em todo payload de webhook** — um receptor só, faz lookup e roteia.
4. **Status cacheado + estado em store compartilhado** (Redis/Postgres) para sobreviver a réplicas.
5. **TTL/descarregamento de canais inativos** da memória (anti-leak quando há muitos números) — casa
   com a Opção A (1 processo, N devices) do §4.
6. **Interface única de provider** (`ChannelStartupService`) cobrindo não-oficial + Cloud — é o §3.2.

### 11.4 Riscos do não-oficial com vários números (ban, rate limit, fingerprint, proxies)

Multi-número não-oficial **multiplica** o risco regulatório do §5.4 (toda lib que pede QR é
não-oficial e viola os ToS do WhatsApp; bane-se o **número**, não o servidor, em geral sem apelação).
Boas práticas levantadas de quem opera multi-número em produção (Baileys/WAHA/Evolution/GOWA):

- **Modo reativo reduz ban drasticamente.** Bots que só respondem a quem inicia: ban < 2%/ano; bots
  que mandam proativo para contatos novos: 15–30%/ano. O WhatsBot é majoritariamente reativo (webhook
  → responde) — o que está a favor —, mas qualquer feature de envio proativo/broadcast eleva muito o
  risco.
- **Aquecimento de número novo (~3 semanas):** semana 1 uso manual/orgânico; semana 2 ≤10–20 msgs
  automatizadas/dia; semana 3+ subir ~20% a cada poucos dias. Contas com 6+ meses são bem mais
  resilientes; VoIP e números sequenciais (comprados em lote) começam com reputação baixa.
- **Rate limits informais** (thresholds da comunidade): < 30 msgs/h; < 20 contatos novos/dia; < 5
  mensagens idênticas/h; delays **aleatórios** 15–45 s (intervalo fixo é detectável); pausa 10–15 min
  a cada ~50 envios; manter reply-rate > 30% e block-rate < 2%.
- **Fingerprint/IP por número** (o ponto mais crítico no multi-número): o WhatsApp correlaciona
  números que conectam pelo **mesmo IP** ("shared infrastructure correlation") — se um cai, os outros
  do mesmo IP ficam sob suspeita e o ban pode propagar. Boas práticas: **um IP dedicado por número**,
  preferindo **proxies residenciais ou 4G/LTE** (não datacenter compartilhado). Serviços gerenciados
  (ex.: GREEN-API) alocam IPv4 exclusivo por instância exatamente por isso. **Implicação para o §4:**
  a Opção A (1 processo GOWA, N devices) coloca **todos os números atrás do mesmo IP do servidor** —
  o pior cenário de correlação. Mitigar exige rotear a saída de cada device por um proxy próprio, o
  que o GOWA/whatsmeow não expõem por device de forma simples → argumento real a favor da Opção B
  (processo dedicado por número, cada um com seu proxy/container/IP) quando a operação for sensível.
- **Higiene de conteúdo/perfil:** perfil completo (foto/nome/status); simular humano (marcar lido →
  digitando com delay → enviar — fluxo que o WAHA documenta); opt-in explícito; variar conteúdo
  (spintax); evitar links encurtados na 1ª mensagem; nada de bulk para listas frias; manter 1–2
  números de reserva já aquecidos para continuidade.
- **Comparativo de risco:** a **Cloud API oficial (§5) não bane por comportamento** (tem quality
  rating com avisos antes, recuperação por apelação); o não-oficial bane direto e permanente. Para
  **proativo em escala**, a única via sem risco de ban é a Cloud API. O multi-número não-oficial é
  ótimo para **atendimento reativo de poucos chips**, não para disparo em massa.

### 11.5 Recomendação de arquitetura para multi-número não-oficial no WhatsBot

Coerente com a abstração de canal do §3 e o faseamento do §9:

1. **Manter o GOWA como bridge** (não reescrever em Go). O custo de embarcar whatsmeow só se justifica
   em cenário multi-tenant SaaS — fora do perfil atual.
2. **Adotar o modelo "instance" da Evolution** na nossa tabela `channels` (§4): `channel_id` no path
   de todos os endpoints de operação, **um device GOWA por canal** (Opção A do §4 como default).
3. **Roteamento de entrada robusto = path + payload**: rota `/api/webhook/gowa/{channel_id}` **e**
   leitura de `body["device_id"]`/`session_id` como confirmação/fallback (o GOWA não dá webhook por
   device, então o `channel_id` no path vem do nosso registro device↔canal; o campo no payload valida).
   Idempotência por `(channel_id, external_msg_id)` (§10.6) cobre reentregas.
4. **Auth em dois níveis** (Evolution): operações de gestão de canal são admin (doc 03); cada canal
   guarda sua credencial em `channel_credentials` cifrado (§5.7). Se um dia o GOWA ganhar auth por
   device, mapeamos 1:1.
5. **Isolamento por sensibilidade**: default Opção A (1 processo, N devices) pelo custo/RAM; expor
   `gowa_isolation = dedicated_process` (Opção B do §4) para quem precisa de **isolamento de falha
   E/OU IP/proxy dedicado por número** — este é o gancho técnico para mitigar o risco de correlação de
   IP do §11.4 (cada processo dedicado pode rodar atrás de seu proxy).
6. **Guard-rails anti-ban no core** (independente de provider, via os filtros de plugin já existentes):
   delays aleatórios entre partes de resposta (`filter.reply.part`), opção de "somente reativo" por
   canal, e avisos quando o operador tentar broadcast. Tratar `events.TemporaryBan` (exposto pelo GOWA
   como status/desconexão) como alerta visível na tela de Canais (§8).
7. **Status/observabilidade por canal** na UI (§8): conectado/QR/erro **e** sinais de saúde por número
   (último ban temporário, reconexões) — supre a lacuna de observabilidade-por-device do GOWA (§11.2)
   no nível do WhatsBot.
8. **Roadmap de integrações** inspirado na Evolution (Chatwoot/n8n/filas) fica para depois — citar como
   direção, não MVP.

Em uma frase: **copiar o modelo de "instância" e a auth-em-dois-níveis da Evolution, mantendo o GOWA
como motor (1 device por canal), com a opção de processo+proxy dedicado por número para operações
sensíveis ao ban** — tudo atrás da mesma interface `Channel` do §3.

---

## 12. Referências

GOWA / go-whatsapp-web-multidevice
- [Repositório oficial (GitHub)](https://github.com/aldinokemal/go-whatsapp-web-multidevice)
- [README (multi-device v8, endpoints /devices, X-Device-Id, webhook device_id)](https://github.com/aldinokemal/go-whatsapp-web-multidevice/blob/main/readme.md)
- [Release v8.0.0 (introdução do multi-device)](https://newreleases.io/project/github/aldinokemal/go-whatsapp-web-multidevice/release/v8.0.0)
- [openapi.yaml (spec da REST API)](https://github.com/aldinokemal/go-whatsapp-web-multidevice/blob/main/docs/openapi.yaml)
- [Discussion #572 — device_id required via X-Device-Id](https://github.com/aldinokemal/go-whatsapp-web-multidevice/discussions/572)

WhatsApp Cloud API (Meta)
- [Documentação oficial de Pricing (WhatsApp Business Platform)](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)
- [Verificação de webhook (hub.challenge / verify_token) — guia](https://forum.bubble.io/t/verifying-the-endpoint-for-whatsapp-webhooks/239045)
- [Exemplo de verificação + recebimento (gist)](https://gist.github.com/mikedidomizio/fcef93d270a14ce90273322a7c3f3187)
- [Setup Cloud API (Phone Number ID, tokens) — sms-magic docs](https://docs.sms-magic.com/3gRa19kt0b4-messaging-guide-ott-channels/BMoPP0zQ3Ps-whatsapp-cloud-api-setup)
- [Janela de 24h e templates — smsmode](https://www.smsmode.com/en/whatsapp-business-api-customer-care-window-ou-templates-comment-les-utiliser/)
- [Atualização de preços (jul/2025, por-mensagem, janela 72h) — YCloud](https://www.ycloud.com/blog/whatsapp-api-pricing-update)
- [Pricing 2026 e categorias de template — respond.io](https://respond.io/blog/whatsapp-business-api-pricing)
- [Guia de webhooks WhatsApp — Hookdeck](https://hookdeck.com/webhooks/platforms/guide-to-whatsapp-webhooks-features-and-best-practices)

Telegram Bot API
- [Telegram Bot API (oficial)](https://core.telegram.org/bots/api)
- [Long Polling vs Webhook — GramIO](https://gramio.dev/updates/webhook)
- [Marvin's Guide to Webhooks (oficial)](https://core.telegram.org/bots/webhooks)

Lifecycle de plugin / supervisor de tasks / subprocesso gerenciado (precedentes — §3.4.4)
- [VS Code — Language Server Extension Guide (activate/deactivate, gestão de Language Server como subprocesso)](https://code.visualstudio.com/api/language-extensions/language-server-extension-guide)
- [vscode-languageserver-node (modelo Disposable / context.subscriptions)](https://github.com/microsoft/vscode-languageserver-node)
- [Home Assistant — config entry unloading (async_setup_entry/async_unload_entry, entry.async_on_unload, reload em runtime)](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/config-entry-unloading/)
- [Adopting Erlang — supervision trees (restart permanent/transient/temporary + rate-limit/circuit breaker)](https://adoptingerlang.org/docs/development/supervision_trees/)
- [pytest-xprocess (subir processo externo com readiness probe)](https://pytest-xprocess.readthedocs.io/)
- [Python docs — subprocess (process group: start_new_session / CREATE_NEW_PROCESS_GROUP)](https://docs.python.org/3/library/subprocess.html)
- [Meziantou — matar a árvore de processos filhos no exit do pai (Windows Job Object, KILL_ON_JOB_CLOSE)](https://www.meziantou.net/killing-all-child-processes-when-the-parent-exits-job-object.htm)

Padrões de channel adapter (open source)
- [Chatwoot — WhatsApp Channel (provider pattern, templates) — DeepWiki](https://deepwiki.com/chatwoot/chatwoot/7.4-whatsapp-channel)
- [Chatwoot — Adicionar novos canais (discussão)](https://github.com/orgs/chatwoot/discussions/2759)
- [Chatwoot v2.7.0 — WhatsApp Cloud API](https://www.chatwoot.com/blog/v2-7-0/)
- [Chatwoot — Channels (tipos built-in) vs Integrations/Apps — docs](https://www.chatwoot.com/docs/product/channels/overview) (canais como tipos built-in no core; integrações de terceiros como apps — análogo a "provider no core vs plugin", §3.4)

whatsmeow (lib Go por baixo do GOWA) — §11.1
- [Repositório whatsmeow (GitHub)](https://github.com/tulir/whatsmeow)
- [pkg.go.dev — package whatsmeow](https://pkg.go.dev/go.mau.fi/whatsmeow)
- [pkg.go.dev — package sqlstore (Container, schema de sessão)](https://pkg.go.dev/go.mau.fi/whatsmeow/store/sqlstore)
- [pkg.go.dev — package events (events.Message/Receipt/QR/PairSuccess/TemporaryBan...)](https://pkg.go.dev/go.mau.fi/whatsmeow/types/events)
- [container.go (NewDevice/GetAllDevices/GetDevice)](https://github.com/tulir/whatsmeow/blob/main/store/sqlstore/container.go)
- [client.go (NewClient, Connect, EnableAutoReconnect)](https://github.com/tulir/whatsmeow/blob/main/client.go)
- [pair-code.go (PairPhone — phone pairing code)](https://github.com/tulir/whatsmeow/blob/main/pair-code.go)
- [PR #471 — exemplo de múltiplas sessões num processo](https://github.com/tulir/whatsmeow/pull/471)
- [Issue #602 — goroutine leak / handler queue cheia](https://github.com/tulir/whatsmeow/issues/602)
- [DEV.to — multi-instance REST API com Go/Echo/whatsmeow](https://dev.to/suharyadi2112/building-a-whatsapp-multi-instance-rest-api-with-go-echo-and-whatsmeow-5bln)

GOWA multi-device — lacunas (§11.2)
- [docs/webhook-payload.md (device_id + session_id no payload; webhook global)](https://github.com/aldinokemal/go-whatsapp-web-multidevice/blob/main/docs/webhook-payload.md)
- [Release v8.1.0 (correção de data leak/dup entre devices — #513, #509, #512)](https://github.com/aldinokemal/go-whatsapp-web-multidevice/releases/tag/v8.1.0)
- [DeepWiki — Device Context and Scoping / API Endpoints](https://deepwiki.com/aldinokemal/go-whatsapp-web-multidevice/4.2-api-endpoints-reference)

Evolution API (modelo de instância — §11.3)
- [GitHub oficial (EvolutionAPI/evolution-api)](https://github.com/EvolutionAPI/evolution-api)
- [Documentação oficial v2](https://doc.evolution-api.com/v2/en/configuration/webhooks)
- [Webhooks v2 (campo `instance`, webhookByEvents)](https://doc.evolution-api.com/v2/en/configuration/webhooks)
- [Fetch Instances (instance-controller)](https://doc.evolution-api.com/v2/api-reference/instance-controller/fetch-instances)
- [Database / requisitos (Postgres+Prisma, Redis)](https://doc.evolution-api.com/v2/en/requirements/database)
- [DeepWiki — arquitetura (WAMonitoringService, ChannelStartupService)](https://deepwiki.com/EvolutionAPI/evolution-api)
- [.env.example (CACHE_REDIS_SAVE_INSTANCES, DEL_INSTANCE, AUTHENTICATION_API_KEY)](https://github.com/EvolutionAPI/evolution-api/blob/main/.env.example)
- [Cliente Python oficial (evolutionapi)](https://github.com/EvolutionAPI/evolution-client-python/blob/main/README.md)
- [Baileys (WhiskeySockets/Baileys — lib usada pela Evolution)](https://github.com/WhiskeySockets/Baileys)

Riscos do não-oficial multi-número (ban, rate limit, fingerprint, proxies — §11.4)
- [WAHA — How to Avoid Blocking (fluxo lido→digitando→enviar)](https://waha.devlike.pro/docs/overview/%EF%B8%8F-how-to-avoid-blocking/)
- [Achiya — WhatsApp Spam Detection / Bot Banned 2026](https://achiya-automation.com/en/blog/whatsapp-spam-detection-2026/)
- [Kraya AI — WhatsApp Automation Ban Risk (taxas de ban reativo vs proativo)](https://blog.kraya-ai.com/whatsapp-automation-ban-risk)
- [WASenderApi — Anti-Ban Strategy for Unofficial APIs (2025)](https://wasenderapi.com/blog/stop-getting-banned-the-ultimate-whatsapp-anti-ban-strategy-for-unofficial-apis-in-2025)
- [WASenderApi — Evolution API sem ban (2026)](https://wasenderapi.com/blog/how-to-use-evolution-api-without-getting-banned-on-whatsapp-2026-guide)
- [GREEN-API — IPv4 exclusivo por instância (proteção contra bloqueio)](https://green-api.com/en/docs/faq/how-my-whatsapp-number-is-protected-from-blocking/)
- [Hidemium — gerenciar múltiplas contas WhatsApp com segurança (2026)](https://hidemium.io/blog/how-to-manage-multiple-whatsapp-accounts-safely-in-2026/)
- [Twinstrata — melhores proxies para WhatsApp (residencial/4G)](https://www.twinstrata.com/best-proxies-for-whatsapp/)
- [Baileys — Issue #1869 (onda de bans)](https://github.com/WhiskeySockets/Baileys/issues/1869)
- [WAHA — Issue #1362 (ban em dois números)](https://github.com/devlikeapro/waha/issues/1362)
- [Evolution API — Issue #2228 (risco de ban em consulta de números)](https://github.com/EvolutionAPI/evolution-api/issues/2228)
