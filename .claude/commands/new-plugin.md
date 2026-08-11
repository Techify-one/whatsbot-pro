# /new-plugin — Criar um novo plugin do WhatsBot

Você (Claude) vai criar um novo plugin do WhatsBot **sem mexer em nenhum arquivo do core**. Tudo fica no repositório irmão `../whatsbot-pro-plugins/plugins/<id>/`: fonte instalável em `src/`, testes de desenvolvimento em `tests/`, metadados e ZIP no diretório do plugin.

Argumento opcional do usuário (descrição do plugin): `$ARGUMENTS`

## Passo 1 — Coletar requisitos

Use `AskUserQuestion` para coletar (ou inferir do `$ARGUMENTS`):

1. **id do plugin** (snake_case, ex: `orders`, `cardapio`, `agenda`). Validar regex `^[a-z][a-z0-9_]{0,31}$`.
2. **Nome humano** e descrição curta.
3. **Telas**: lista de objetos `{title, path, icon, config}`. Ex: `[{title: 'Pedidos', path: '/orders', icon: 'shopping-cart', config: false}]`. Ao menos 1. Use `config: true` para a tela de **configuração** do plugin (abre no botão "Configurar" do card em `/plugins`); `config: false` (default) para telas de funcionalidade (viram página no menu da engrenagem).
4. **Tools que o LLM vai expor**: lista `[{name, description, params: {field: type}}]`. Pode ser vazia se o plugin é só UI.
5. **Precisa injetar conteúdo no system prompt?** (ex: cardápio). Se sim, descreva o que injetar.
6. **Tabelas no banco**: lista de `{name, columns}` (sem o prefixo, vou adicionar). Pode ser vazia.
7. **Settings declaráveis** (Pydantic Valves) — campos configuráveis pelo usuário. **Toda configuração do plugin vive na aba de configuração DO PRÓPRIO plugin** (botão "Configurar" em `/plugins`), NUNCA numa aba nova do painel de Configurações do core. Escolha: (a) `settings.py` com `class Settings(BaseModel)` → form auto-gerado; e/ou (b) uma screen `config: true` com UI custom. Pode ser vazio se o plugin não tem o que configurar.
8. **Events que o plugin observa** (fire-and-forget, paralelo): lista de nomes a assinar — ex: `message.received`, `message.sent`, `llm.after`, `tool.after`, `*` (catch-all). Pode ser vazia. Use `plugins/events.py::KNOWN_EVENTS` como fonte executável; a tabela do `CLAUDE.md` é guia de payloads, não catálogo exaustivo.
9. **Filters que o plugin intercepta** (síncronos, podem modificar ou abortar): lista de nomes a interceptar — ex: `filter.message.before_save`, `filter.reply.part`, `filter.system_prompt`, `filter.tool.args`. Retornar `None` aborta a ação. Pode ser vazia. Use `plugins/events.py::KNOWN_FILTERS` como catálogo executável e `CLAUDE.md` para tipos/contexto.
10. **Controle de acesso (RBAC)**: "Quais funcionalidades têm controle de acesso? Para cada uma, quais ações (ver/editar/excluir)?" → gera o bloco `rbac:` no manifest. Convenção forte de chaves: `view`/`edit`/`delete`. Pode ser vazia (plugin acessível a todos, como hoje).

Se o usuário escreveu tudo no `$ARGUMENTS`, deduza e confirme com **uma** pergunta de validação.

## Passo 2 — Ler referências do core (NÃO modificar)

Antes de gerar qualquer arquivo, **leia** estes arquivos para seguir os padrões existentes:

- [agent/tools/save_contact_info.py](../../agent/tools/save_contact_info.py) — padrão de tool (schema dict + `execute(ctx, args)`)
- [agent/handler.py](../../agent/handler.py) `register_plugin_prompts` — registro dos fragments
- [agent/prompt_builder.py](../../agent/prompt_builder.py) — chamada e isolamento dos prompt fragments
- [db/tables.py](../../db/tables.py) — `Table` objects do core (referência de tipos e nomes)
- [server/routes/tags.py](../../server/routes/tags.py) — padrão de APIRouter + helpers `_ok`/`_err`
- [web/static/js/components/Dashboard.js](../../web/static/js/components/Dashboard.js) — padrão de componente Preact + HTM
- [protocolos/src/](../../../whatsbot-pro-plugins/plugins/protocolos/src/) — plugin completo de referência (routes/settings/events/filters/lifecycle/UI/migrations)
- [melhorias/src/events.py](../../../whatsbot-pro-plugins/plugins/melhorias/src/events.py) — exemplo de `EVENT_HANDLERS`
- [protocolos/src/filters.py](../../../whatsbot-pro-plugins/plugins/protocolos/src/filters.py) — filtros de lifecycle de conversa
- [whatsapp_cloud/src/filters.py](../../../whatsbot-pro-plugins/plugins/whatsapp_cloud/src/filters.py) — observador defensivo de webhook bruto
- [plugins/events.py](../../plugins/events.py) — implementação do bus (assinaturas reais, prioridade, sync/async)
- [docs/PLUGINS_AUDITAVEIS.md](../../docs/PLUGINS_AUDITAVEIS.md) — como registrar as ações do plugin na trilha de Auditoria (leia se o plugin tiver `routes.py`)

## Passo 3 — Gerar a estrutura

Crie os arquivos em `../whatsbot-pro-plugins/plugins/<id>/`. **Sempre** prefixe nomes de tabela com `plugin_<id>_` — o migrator valida e rejeita o contrário.

```
../whatsbot-pro-plugins/plugins/<id>/
├── src/                     ← único conteúdo que entra no ZIP
│   ├── plugin.yaml          ← manifest (campos abaixo)
│   ├── __init__.py
│   ├── tools.py             ← se houver tools
│   ├── prompts.py           ← se houver fragments
│   ├── routes.py            ← se houver REST endpoints
│   ├── settings.py          ← se houver settings
│   ├── events.py            ← se houver event handlers
│   ├── filters.py           ← se houver filters
│   ├── migrations/
│   │   └── 001_initial.sql
│   └── static/
│       └── <id>.js
├── tests/
│   └── python/test_<comportamento>.py
├── <id>.json               ← metadados do catálogo
└── <id>.zip                ← gerado; nunca editar à mão
```

Adicione também a entrada correspondente em `../whatsbot-pro-plugins/catalog.json`.
Testes devem ter nomes comportamentais; não use número de plano no nome do arquivo.
Eles importam fixtures do core pelo `conftest.py` do repositório externo e **nunca**
ficam dentro de `src/`.

### plugin.yaml

```yaml
id: <id>
name: <Nome Humano>
version: 1.0.0
whatsbot_api_version: ">=1.0,<2.0"
description: <descrição curta>
author: <autor>
entry:
  tools: tools          # omitir se não houver
  prompts: prompts      # omitir se não houver
  routes: routes        # omitir se não houver
  settings: settings    # omitir se não houver
  events: events        # omitir se não houver
  filters: filters      # omitir se não houver
  services: services    # omitir se não houver — API INTERNA plugin→plugin
migrations: migrations  # omitir se não houver
# APIs internas de OUTROS plugins que este consome (omitir se não houver).
# Fornece o range default de services.call(..., _as="<id>").
# uses_services:
#   - plugin: <outro_id>
#     version: ">=1.0,<2.0"
screens:
  - id: <screen-id>
    title: <Título>
    path: /<path>       # SPA path, escolher algo único
    icon: <icon-name>   # opcional, informativo
    component: /plugins/<id>/static/<id>.js
    config: false       # true = tela de configuração do plugin (modal "Configurar"
                        #        em /plugins). false (default) = página no menu da engrenagem.
    requires: view      # opcional — esconde a screen no menu sem plugin.<id>.view
permissions: []
# RBAC de usuário (opcional) — distinto do 'permissions:' de capability acima.
# Cada chave vira plugin.<id>.<key> e aparece no PermissionPicker da tela Usuários,
# agrupada por "Plugin: <group>". Omitir = plugin acessível a todos (como hoje).
rbac:
  group: <Nome Humano>      # opcional; default = name do plugin
  permissions:
    - { key: view,   label: "Ver <funcionalidade>" }
    - { key: edit,   label: "Criar/editar <funcionalidade>" }
    - { key: delete, label: "Excluir <funcionalidade>" }
dependencies: []

# Somente se houver um módulo frontend_extends que recebe buildPluginApi:
# frontend_extends: /plugins/<id>/static/extends.js
# frontend_api_version: "1.0"
# plugin_services_version: ">=2.0,<3.0"
```

`plugin_services_version` negocia separadamente a allowlist `api.services`.
Manifest legado sem o campo recebe a superfície de compatibilidade 1.x; plugin
novo deve declarar 2.x e ainda feature-detectar funções opcionais. Range inválido
ou incompatível faz o core pular o `frontend_extends` (fail-closed).

⚠️ **`plugin_services_version` (frontend) ≠ `uses_services` (backend)** — nomes
parecidos, superfícies sem relação nenhuma.

**`entry.services` — API interna plugin→plugin** (ver CLAUDE.md §"API interna
plugin→plugin"). O módulo exporta `SERVICES = {"op": callable, ...}` e,
opcionalmente, `SERVICES_VERSION` (semver da SUA superfície, mora no código) e
`SERVICES_ALLOW` (tupla de ids autorizados; vazio = qualquer plugin carregado).
Três regras duras:

1. **Nunca exponha isso por HTTP** — sem rota `/rpc`, sem `/service/{op}`. A
   fronteira é "nada sai do processo".
2. **`services.py` é FOLHA**: nenhum outro módulo do plugin o importa. É o que
   mantém o plugin carregando num core anterior, que não conhece
   `entry.services`. Helper compartilhado vai para um módulo vizinho.
3. **Nenhuma op pode depender de estado criado no `setup()`** — o registro roda
   em `create_app`, antes do lifespan. Uma op não pronta devolve
   `ServiceDisabled` (→ envelope `DISABLED`), nunca levanta.

Do lado CONSUMIDOR, o import é sempre defensivo (import duro no topo de um módulo
que o loader importa = o plugin não carrega, falha muda no boot):

```python
try:
    from plugins import services as _services   # core >= 1.1
except Exception:
    _services = None
...
if _services is not None:
    res = _services.call("<provedor>", "<op>", _as="<meu_id>", **kwargs)
    # res.ok / res.status ∈ ok|unavailable|unknown_op|incompatible|disabled|
    #                       wrong_context|error — NUNCA levanta
```

**Onde fica a configuração (REGRA):** se o plugin tem opções configuráveis, elas
vivem na aba de configuração DO PRÓPRIO plugin — `settings.py` (form auto-gerado)
e/ou uma screen `config: true` (UI custom no mesmo modal "Configurar"). **Nunca**
adicione opção de plugin ao painel de Configurações do core (`ConfigPanel.js`).
Quando há uma screen `config: true`, o modal renderiza ela no lugar do form
declarativo. Veja as screens `config: true` em
`../whatsbot-pro-plugins/plugins/website/src/` e
`../whatsbot-pro-plugins/plugins/protocolos/src/`.

### tools.py (se houver tools)

Cada tool é um par `(schema, executor)`. O executor recebe `ToolContext` (ver [plugins/context.py](../../plugins/context.py)) e retorna `str | None` (string vira `tool` reply no follow-up; `None` usa o default).

```python
import logging
import time

from sqlalchemy import text
from plugins.context import broadcast, make_plugin_db

logger = logging.getLogger(__name__)

MY_TOOL = {
    "type": "function",
    "display_label": "<Rótulo legível>",  # opcional — default mostrado em /tools
    "function": {
        "name": "<tool_name>",   # único globalmente
        "description": "<descrição clara, instrui quando chamar>",
        "parameters": {
            "type": "object",
            "properties": {
                "<param>": {"type": "string", "description": "..."},
            },
            "required": [],
        },
    },
}

def execute_my_tool(ctx, args: dict) -> str | None:
    # ctx.contact é ContactMemory; ctx.handler é AgentHandler
    # ctx.tag_registry; ctx.plugin_id == '<id>'
    with make_plugin_db() as conn:
        conn.execute(
            text("INSERT INTO plugin_<id>_items (text, ts) VALUES (:text, :ts)"),
            {"text": args.get("text", ""), "ts": int(time.time())},
        )
    return None  # ou string de feedback

CORE_TOOLS = [(MY_TOOL, execute_my_tool)]
```

**Banco de dados (importante)**: o WhatsBot roda em cima de SQLAlchemy Core
com **PostgreSQL como único backend** (plano 29 — a env `DATABASE_URL` é
obrigatória). Plugin acessa o banco SEMPRE via:

```python
from sqlalchemy import text
from plugins.context import make_plugin_db

with make_plugin_db() as conn:
    rows = conn.execute(
        text("SELECT * FROM plugin_<id>_items WHERE phone = :phone"),
        {"phone": phone},
    ).mappings().all()
```

Proibido em código de plugin (o backend é Postgres):

- `?` placeholders (use `:nome` bind params)
- `strftime('%s','now')` → use `int(time.time())` em Python
- `INSERT OR REPLACE` / `INSERT OR IGNORE` → ver `db.upsert.upsert()` ou refatore com select+update
- `cur.lastrowid` direto → use `result.inserted_primary_key[0]`
- Qualquer função/sintaxe SQLite-only (`AUTOINCREMENT`, `PRAGMA`) — exceção: `INTEGER PRIMARY KEY AUTOINCREMENT` em migration é traduzido pra `SERIAL PRIMARY KEY` pelo migrator (compat com plugins publicados).

### prompts.py (se houver fragments)

```python
from sqlalchemy import text
from plugins.context import make_plugin_db

def my_fragment(contact, ctx) -> str:
    # contact: ContactMemory; ctx: PromptContext
    return "\n\n--- <Título> ---\n<conteúdo>\n--- Fim ---"

PROMPT_FRAGMENTS = [my_fragment]
```

### routes.py (se houver REST)

Mounted em `/api/plugins/<id>` automaticamente. Auth do core já cobre.

```python
from fastapi import APIRouter
from sqlalchemy import text
from plugins.context import make_plugin_db, plugin_permission

router = APIRouter()

PLUGIN_ID = "<id>"

# AUDITORIA (obrigatório em rota que MUDA algo) — ver docs/PLUGINS_AUDITAVEIS.md.
# Import defensivo: o plugin roda por .zip e pode cair num core sem o seam.
try:
    from plugins.context import audit as _core_audit
except ImportError:  # pragma: no cover — core antigo
    _core_audit = None


def _audit(action: str, **kw) -> None:
    """Registra uma ação deste plugin na Auditoria. Nunca quebra a rota."""
    if _core_audit is None:
        return
    try:
        _core_audit(PLUGIN_ID, action, **kw)
    except Exception:  # noqa: BLE001 — auditoria nunca derruba a ação auditada
        pass


# RBAC: gate a rota com a dependency plugin_permission("<key>"). Ela infere o id
# do path, monta plugin.<id>.<key> e retorna 403 sem a permissão (default-allow
# em instalação legado/open). Use as chaves declaradas no bloco rbac: do manifest.
@router.get("/items", dependencies=[plugin_permission("view")])
async def list_items():
    # GET não audita.
    with make_plugin_db() as conn:
        rows = conn.execute(
            text("SELECT * FROM plugin_<id>_items ORDER BY ts DESC")
        ).mappings().all()
    return {"ok": True, "data": [dict(r) for r in rows]}

@router.delete("/items/{rid}", dependencies=[plugin_permission("delete")])
async def delete_item(rid: int):
    with make_plugin_db() as conn:
        # 1. snapshot ANTES  2. a ação  3. _audit DEPOIS do sucesso
        before = conn.execute(
            text("SELECT * FROM plugin_<id>_items WHERE id = :id"), {"id": rid}
        ).mappings().first()
        conn.execute(text("DELETE FROM plugin_<id>_items WHERE id = :id"), {"id": rid})
    _audit("item.delete", before=dict(before) if before else {},
           after={"item_id": rid, "deleted": True})
    return {"ok": True}
```

**Auditoria — o que auditar** (guia + checklist: `docs/PLUGINS_AUDITAVEIS.md`):

- SIM: configuração do plugin, mudança de estado com dono (fechar/atribuir/aprovar/excluir), escrita em recurso do core (agentes, prompts, tools, tags), ação com efeito externo.
- NÃO: `GET`/listagem/busca, teste de conexão, preferência pessoal por-usuário, evento de alto volume.
- **NUNCA audite tráfego de conversa** — enviar/receber mensagem, reação, recibo, presença. A trilha é de CONFIGURAÇÃO; o histórico de `messages` já registra a conversa, e uma linha por mensagem afogaria a tela.
- A ação é namespaceada pelo core → `<id>.<recurso>.<verbo>`; o ator é o usuário logado (automático). NUNCA ponha segredo/token no `before`/`after`; registre `{"secret_definido": True}`.
- Plugin **só com `settings.py`** não precisa de nada: o core já audita o `PUT /api/plugins/<id>/settings`.

### settings.py (se houver settings)

```python
from pydantic import BaseModel, Field

class Settings(BaseModel):
    field_a: str = Field(default="...", description="...")
    field_b: int = Field(default=10, description="...", ge=1)
```

### events.py (se houver event handlers)

Plugin assina eventos do bus declarando `EVENT_HANDLERS` (dict `nome -> callable`). Handler pode ser sync ou async — async é `await`-ado direto, sync vai pra `asyncio.to_thread`. Exceção em um handler é isolada (loga, não derruba outros).

```python
import logging

logger = logging.getLogger(__name__)

def on_message_received(ctx, payload: dict) -> None:
    # ctx: EventContext — ctx.handler, ctx.plugin_id, ctx.plugin_db,
    #                     ctx.event_name (importante p/ catch-all "*"), ctx.emitted_at
    # payload: dict tipado conforme o evento (ver tabela em CLAUDE.md)
    if payload.get("is_group"):
        return  # filtra cedo
    logger.info("[<id>] %s disse: %s", payload["phone"], payload["text"])

async def on_llm_after(ctx, payload: dict) -> None:
    # latency_ms, reply, tool_calls, usage
    logger.info("[<id>] LLM levou %sms", payload.get("latency_ms"))

EVENT_HANDLERS = {
    "message.received": on_message_received,
    "llm.after": on_llm_after,
    # "*": catch_all,   # opcional — recebe TODO evento (após handlers específicos)
}
```

**Eventos comuns** (catálogo executável completo em `plugins/events.py::KNOWN_EVENTS`):

- Mensagem: `message.received` (pre-DB), `message.saved` (post-DB, **use este pra ler do DB**), `message.sent`, `message.any`, `message.reaction`, `message.edited`, `message.revoked`, `message.deleted`
- Conexão/grupo: `presence.changed`, `receipt.changed`, `group.participants_changed`, `group.joined`, `call.received`, `connection.changed`, `chat.archived`
- LLM/tool: `llm.before`, `llm.after`, `tool.before`, `tool.after`
- Core: `contact.updated`, `contact.ai_toggled`, `contact.tagged`, `contact.untagged` (por tag removida), `tag.created/updated/deleted`, `config.changed`, `tool_override.changed`, `execution.started/ended`, `plugin.loaded/enabled/disabled/settings.changed`, `app.startup/shutdown`

**Não chame `gowa_client.send_message` dentro de handler de `message.sent`** — gera loop infinito (a send produz outro `message.sent`).

### filters.py (se houver filters)

Plugin intercepta o pipeline declarando `FILTERS` (dict `nome -> callable` ou `(callable, priority)`). Filter recebe `(ctx, value)` e retorna `value` modificado ou `None` para **abortar** a ação envolvida. Pode ser sync ou async. Exceção é isolada (loga, valor passa intacto adiante).

```python
import logging

logger = logging.getLogger(__name__)

def block_keyword(ctx, msg: dict) -> dict | None:
    # ctx: FilterContext — ctx.handler, ctx.plugin_id, ctx.plugin_db,
    #                       ctx.filter_name, ctx.emitted_at
    text = (msg.get("text") or "").lower()
    if "spam" in text:
        logger.info("[<id>] bloqueado: %s", msg.get("phone"))
        return None  # ABORTA: mensagem não é salva nem responde
    return msg

def add_signature(ctx, part: str) -> str:
    if not part.strip():
        return part
    return f"{part}\n\n— Atendimento <Plugin>"

FILTERS = {
    "filter.message.before_save": block_keyword,
    "filter.reply.part": (add_signature, 50),  # priority 50 — roda antes do default (100)
}
```

**Filters disponíveis** (tabela completa com tipo do `value` e `ctx.extras` em `CLAUDE.md`):

| Filter | `value` | `None` faz |
|---|---|---|
| `filter.webhook.payload` | `dict` (body bruto de qualquer provider; `ctx.extras` traz provider/canal/assinatura) | webhook responde 200 sem processar |
| `filter.message.before_save` | `dict` (mensagem tipada com `media_extras`) | mensagem ignorada |
| `filter.message.outgoing` | `dict` (echo do celular do usuário) | echo ignorado |
| `filter.transcription.should_run` | `bool` (default `True`) | pula transcribe/describe (mesmo que `False`) |
| `filter.transcription.result` | `str` (transcrição/descrição já gerada) | trata como vazia |
| `filter.contact.tags` | `list[str]` (tags pretendidas) | mantém tags atuais |
| `filter.event.before_emit` | `dict` (payload de qualquer evento exceto lifecycle) | cancela o emit |
| `filter.system_prompt` | `str` | system prompt vazio |
| `filter.llm.messages` | `list[dict]` | LLM não é chamado |
| `filter.llm.tools` | `list[dict]` | LLM chamado sem tools |
| `filter.tool.args` | `{tool_name, args}` | tool pulada |
| `filter.tool.result` | `str` | LLM recebe string vazia |
| `filter.reply.raw` | `str` | nada é enviado |
| `filter.reply.parts` | `list[str]` | nada é enviado |
| `filter.reply.part` | `str` (cada parte) | parte é pulada |
| `filter.outbound.text` | `str` wire-only antes do provider | mantém texto anterior |
| `filter.conversation.before_status` | `dict` da mudança de status | aborta fechamento |
| `filter.conversation.before_assign` | `dict` da atribuição | aborta atribuição |
| `filter.conversation.clear_assignee_on_close` | `bool` | default seguro limpa assignee |
| `filter.agent.resolve` | `AgentSpec` | mantém agente default |
| `filter.conversation.assignment` | `dict` de destino | mantém atribuição default |

Não existe um filtro genérico para mídia desconhecida. O antigo nome
`filter.media.unknown` nunca teve produtor após o refactor do webhook e foi
retirado do catálogo: um provider deve normalizar o payload no próprio
`Channel.parse_inbound()` para um `InboundEvent.kind` suportado.

**Filter síncrono trava o pipeline** — mantenha rápido. Persistência pesada / network → joga num event handler em `events.py`.

### migrations/001_initial.sql

**Toda** tabela / índice tem que começar com `plugin_<id>_`. O migrator faz validação por regex.

**Sintaxe das migrations.** O migrator usa `engine.begin()` e roda contra o
**Postgres** (único backend). Evite `strftime` (gere timestamps no Python com
`int(time.time())`) e defaults baseados em funções específicas. `INTEGER
PRIMARY KEY AUTOINCREMENT` é aceito por compat (o migrator traduz pra `SERIAL
PRIMARY KEY`), mas em plugin novo prefira `SERIAL PRIMARY KEY` direto — ou
gere o `id` no código (UUID) e declare `id TEXT PRIMARY KEY`.

```sql
CREATE TABLE IF NOT EXISTS plugin_<id>_items (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    created_at REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS plugin_<id>_items_created_at
    ON plugin_<id>_items(created_at);
```

### static/<id>.js

Componente Preact + HTM com `default export`. Usar imports do importmap (`preact`, `preact/hooks`, `htm`). Receber `apiBase` como prop.

**Cores / modo escuro (obrigatório):** a tela tem que ser legível nos temas claro E escuro. Use as classes semânticas `wa-*` para superfícies/textos/bordas — `bg-wa-bg`, `bg-wa-panel` (cards), `text-wa-text`, `text-wa-secondary`, `border-wa-border`, `bg-wa-hover`, `bg-wa-teal` (botão), `text-white` (texto sobre botão colorido). Em `<input>`/`<textarea>`/`<select>` use a classe `.wa-field` (fundo cinza + texto preto). NÃO use cores cruas de fundo/texto (`bg-white`, `text-gray-*`, hex inline) confiando no padrão claro — no escuro vira texto claro sobre fundo claro = ilegível. Sempre ligue o modo escuro (engrenagem → "Modo escuro") e confira o contraste. Detalhes em CLAUDE.md → "Tema e modo escuro (legibilidade)".

**Importante (auth):** quando o usuário configura uma senha no app, a API exige `Authorization: Bearer <token>` em **todas** as chamadas `/api/*`. O token fica em `localStorage` sob a chave `whatsbot_token`. Plugin precisa anexar esse header — senão a tela mostra `Não autenticado.` quando o app está protegido por senha. O helper abaixo cobre isso e também captura 401 pra disparar o evento de logout do core (`whatsbot:unauthorized`):

```js
import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

function authHeaders(extra = {}) {
  const token = localStorage.getItem('whatsbot_token') || '';
  return token ? { ...extra, Authorization: `Bearer ${token}` } : { ...extra };
}

async function apiFetch(url, init = {}) {
  const headers = authHeaders(init.headers || {});
  const res = await fetch(url, { ...init, headers });
  if (res.status === 401) {
    localStorage.removeItem('whatsbot_token');
    window.dispatchEvent(new Event('whatsbot:unauthorized'));
    throw new Error('Não autenticado.');
  }
  return res;
}

// O PluginScreen injeta a prop `can(key)` = hasPermission(user, 'plugin.<id>.<key>')
// (default-allow em instalação legado/open). Use pra esconder ações sem permissão.
export default function MyScreen({ apiBase, can }) {
  const [items, setItems] = useState([]);
  useEffect(() => {
    apiFetch(`${apiBase}/items`)
      .then(r => r.json())
      .then(d => { if (d.ok) setItems(d.data || []); })
      .catch(() => { /* unauthorized já tratado */ });
  }, []);
  return html`
    <div class="p-6 max-w-3xl mx-auto">
      <h1 class="text-2xl font-bold mb-4">Minha Tela</h1>
      ${items.map(it => html`<div key=${it.id} class="flex items-center gap-2">
        <span class="flex-1">${it.name}</span>
        ${can('delete') ? html`<button class="text-red-600">Excluir</button>` : null}
      </div>`)}
    </div>
  `;
}
```

Para `POST`/`PUT` com JSON, passe `headers: { 'Content-Type': 'application/json' }` e `body: JSON.stringify(...)` — o `apiFetch` adiciona o `Authorization` em cima desses headers. Para uploads (`FormData`), **não** defina `Content-Type` — o navegador define com boundary correto.

## Passo 4 — Testar, empacotar e instruir o usuário

No repositório `whatsbot-pro-plugins`, rode:

```bash
python3 scripts/test_plugins.py <id>
python3 scripts/build_plugins.py <id>
python3 scripts/build_plugins.py --check <id>
```

O build deve falhar se houver teste, cache, banco ou segredo em `src/`. Nunca copie
`tests/` para o ZIP e nunca execute testes durante instalação/atualização.

Ao terminar, mostre:

1. Caminho da pasta criada.
2. Lista de arquivos gerados.
3. Próximo passo: "Acesse `/plugins`, importe `plugins/<id>/<id>.zip` e depois clique em **Ativar**. O servidor reinicia em ~3s; telas de funcionalidade (`config: false`) aparecem no menu da engrenagem."
4. Para configurar: na tela `/plugins`, clique em **Configurar** no card do plugin (abre o form de settings e/ou a screen `config: true`).
5. Para compartilhar: entregue somente `<id>.zip`; fonte e testes continuam no repositório de desenvolvimento.

## Regras importantes

- **Nunca modifique arquivos do core** (`agent/`, `db/`, `server/`, `web/`). Plugin é totalmente isolado.
- **Sempre prefixe tabelas com `plugin_<id>_`**. O migrator rejeita o contrário.
- **Não invente nomes de imports** — use os do importmap (`preact`, `preact/hooks`, `htm`) e os módulos que o core já expõe (`db.engine`, `db.tables`, `db.repositories.*`, `agent.memory`, `plugins.context`). Para acesso ao banco em plugin: `from plugins.context import make_plugin_db` e `from sqlalchemy import text`.
- **Tool name é global**: se conflitar com um nome existente, o registry loga warning e ignora apenas a tool duplicada; o restante do plugin continua carregado. Prefira nomes específicos como `<id>_<verbo>` (ex: `orders_create`, `cardapio_listar`).
- **RBAC é declarativo no manifest** (bloco `rbac:`); **nunca cheque permissão na mão** — gate rotas com `dependencies=[plugin_permission("<key>")]` e esconda ações na UI com `can(key)`. Esconda a screen sem permissão com `requires:` no manifest. Convenção de chaves: `view`/`edit`/`delete`. As permissões aparecem sozinhas na tela Usuários, agrupadas pelo plugin.
- **Settings declarativas** geram UI automaticamente a partir do schema Pydantic — strings, ints, floats, bools, enums; não duplique esse form na mão. Quando a configuração exigir UI rica, use a screen `config: true` permitida acima.
- **Toda rota que MUDA algo chama `_audit(...)`** (helper com import defensivo de `plugins.context.audit`) — configuração, mudança de estado com dono, escrita em recurso do core. `GET`/teste de conexão/preferência pessoal não. Segredo nunca entra no `before`/`after`. Guia: [docs/PLUGINS_AUDITAVEIS.md](../../docs/PLUGINS_AUDITAVEIS.md).
- **Configuração SEMPRE na aba do próprio plugin** (settings declarativas e/ou screen `config: true` → modal "Configurar" em `/plugins`). **NUNCA** adicione seção/aba ao painel de Configurações do core (`web/static/js/components/ConfigPanel.js`) — isso é mexer no core e é proibido.
- **Migrations rodam uma única vez** por versão. Para evoluir o schema, crie `002_*.sql`, `003_*.sql` — não edite `001`.

## Contrato de tools (importante)

Toda tool registrada num plugin é automaticamente inserida na tabela `tool_overrides` com defaults (enabled=1, description=NULL). O usuário pode customizar via UI em `/tools` — ligar/desligar, editar a description que vai pro LLM, e renomear o display label.

Por isso:

- **`name`** vira identidade pública e estável. **NÃO renomeie** depois de release — quebra histórico de `usage` (que grava `call_type=<name>`) e qualquer override que o usuário tenha criado. Para evoluir, crie uma tool nova e deprecie a antiga.
- **`description`** em código é o **default** mostrado na UI. Escreva como instrução clara pro LLM (quando usar / quando NÃO usar) — seu default precisa funcionar sem customização. O usuário pode sobrescrever, mas o reset volta pro seu texto.
- **`display_label`** (opcional, no nível do dict raiz, fora de `function`) é o rótulo legível mostrado em `/tools`. O handler retira esse campo antes de mandar pro LLM (não vai pra OpenAI). Use português, curto. Ex: `"display_label": "Salva Dados do Contato"`.
- Quando o plugin é deletado pela UI, todas as overrides daquele plugin somem junto (`delete_for_plugin` no DELETE do plugin).
- Convenção de naming: `<plugin_id>_<verbo>` (ex: `lembretes_create`, `orders_search`) — evita colisão e ajuda o usuário a saber de que plugin a tool veio.
