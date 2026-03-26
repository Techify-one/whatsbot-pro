# Refatoração: Quebrar Arquivos Grandes em Módulos Menores

## Context

O projeto tem 3 arquivos muito grandes que concentram responsabilidades demais, dificultando manutenção e crescimento:
- `server/app.py` (1.418 linhas) — monolito com TODAS as rotas, webhook, batching, WebSocket, background tasks
- `web/static/js/components/Contacts.js` (1.320 linhas) — UI inteira de chat em um só arquivo
- `agent/handler.py` (715 linhas) — duas classes distintas (ContactMemory + AgentHandler) misturadas

O `gowa/client.py` (338 linhas) **NÃO será refatorado** — é uma classe coesa com responsabilidade única (HTTP client), sem abstrações distintas para extrair.

---

## Fase 1: Extrair helpers e state do server (baixo risco)

### Criar `server/helpers.py` (~30 linhas)
Mover funções puras: `_ok()`, `_err()`, `_mask_key()`, `_get_web_dir()`

### Criar `server/state.py` (~80 linhas)
Mover: `MemoryLogHandler`, `ConnectionManager`, `AppState`
Criar dataclass `ServerDeps` para injeção de dependências:
```python
@dataclasses.dataclass
class ServerDeps:
    settings: Settings
    gowa_manager: GOWAManager
    gowa_client: GOWAClient
    agent_handler: AgentHandler
    ws_manager: ConnectionManager
    state: AppState
    memory_log_handler: MemoryLogHandler
```

### Atualizar `server/app.py`
Trocar definições locais por imports de `server.helpers` e `server.state`

**Teste:** Rodar app, verificar que todos endpoints respondem normalmente.

---

## Fase 2: Extrair ContactMemory do agent (baixo risco)

### Criar `agent/memory.py` (~250 linhas)
Mover de `agent/handler.py`:
- Classe `ContactMemory` (linhas 26-241)
- Função `_build_image_content()` (linhas 243-267)

### Atualizar `agent/handler.py`
- Adicionar `from agent.memory import ContactMemory, _build_image_content`
- `ProcessResult` fica no handler (é retornado por `process_message()`)

### Atualizar `agent/__init__.py`
- Re-exportar `ContactMemory`

**Teste:** Enviar mensagem, verificar que memória de contato funciona.

---

## Fase 3: Extrair rotas do server em módulos (risco médio)

Cada módulo exporta `def register_routes(app: FastAPI, deps: ServerDeps)`.

### Nova estrutura `server/routes/`:

| Arquivo | Endpoints | ~Linhas |
|---------|-----------|---------|
| `logs.py` | `GET/DELETE /api/logs` | 20 |
| `sandbox.py` | `POST sandbox/send, sandbox/clear` | 50 |
| `config.py` | `GET/PUT /api/config`, `POST test-key`, `GET models`, `GET status` | 120 |
| `whatsapp.py` | `GET /api/qr`, `POST qr/refresh`, `reconnect`, `logout` | 60 |
| `websocket.py` | `WS /ws` | 50 |
| `usage.py` | `GET usage/summary`, `usage/by-contact`, `usage/contact/{phone}` + pricing cache | 80 |
| `contacts.py` | Todos os `/api/contacts/*` (CRUD, send, retry, presence, toggle-ai, info) | 350 |
| `webhook.py` | `POST /api/webhook` + `_process_batch`, `_send_reply`, `_parse_split_reply`, `_broadcast_tool_calls` | 200 |

### Criar `server/background.py` (~100 linhas)
Mover: `_start_gowa_task`, `_status_poll_loop`, `_qr_poll_loop`

### `server/app.py` final (~120 linhas)
Apenas: `create_app()` factory, lifespan, mount static, registrar routers.

### Ordem de extração (do mais simples ao mais complexo):
1. `logs.py` → 2. `sandbox.py` → 3. `config.py` → 4. `whatsapp.py` → 5. `websocket.py` → 6. `usage.py` → 7. `contacts.py` → 8. `webhook.py` → 9. `background.py` → 10. Slim down `app.py`

**Sem risco de import circular** — rotas importam de `server.state` e `server.helpers`. `app.py` importa dos módulos de rota. Nenhuma rota importa de outra.

**Teste:** Integração completa — enviar mensagem via Evolution API, verificar webhook + batch + reply nos logs.

---

## Fase 4: Quebrar Contacts.js em componentes (independente do backend)

### Nova estrutura `web/static/js/components/contacts/`:

| Arquivo | Conteúdo | ~Linhas |
|---------|----------|---------|
| `utils.js` | `formatTime()`, `formatBubbleTime()` | 20 |
| `icons.js` | Todos os SVG icons (16 componentes) | 130 |
| `ContextMenu.js` | Menu de contexto (right-click) | 50 |
| `ContactList.js` | Sidebar com lista de contatos | 100 |
| `ContactInfoPanel.js` | Painel de edição de info do contato | 150 |
| `ContactDetail.js` | Chat panel (mensagens, input, áudio, mídia) | 550 |
| `Contacts.js` | Orquestrador — gerencia state e compõe sub-componentes | 330 |

### Compatibilidade
O `Contacts.js` original (na raiz de `components/`) vira re-export:
```js
export { Contacts } from './contacts/Contacts.js';
```
Zero alterações em `app.js`.

**Sem risco de import circular** — `icons.js` e `utils.js` são folha. Componentes importam deles. Orquestrador importa componentes.

**Teste:** Abrir UI, verificar que lista de contatos, chat, edição de info e gravação de áudio funcionam.

---

## Estrutura final do projeto

```
server/
  __init__.py
  app.py              (~120 linhas — factory + lifespan + mount)
  dev.py              (inalterado)
  state.py            (~80 linhas — AppState, ConnectionManager, MemoryLogHandler, ServerDeps)
  helpers.py          (~30 linhas — _ok, _err, _mask_key, _get_web_dir)
  background.py       (~100 linhas — background loops)
  routes/
    __init__.py
    config.py
    whatsapp.py
    webhook.py
    contacts.py
    sandbox.py
    websocket.py
    logs.py
    usage.py

agent/
  __init__.py
  handler.py          (~450 linhas — AgentHandler + ProcessResult)
  memory.py           (~250 linhas — ContactMemory + _build_image_content)
  tools/              (inalterado)

web/static/js/components/
  Contacts.js         (re-export)
  contacts/
    Contacts.js       (~330 linhas — orquestrador)
    ContactDetail.js
    ContactList.js
    ContactInfoPanel.js
    ContextMenu.js
    icons.js
    utils.js
```

## Verificação

1. Rodar `python main.py` — app inicia sem erros
2. Abrir UI no browser — todas as páginas carregam
3. Enviar mensagem de teste via Evolution API — webhook recebe, batch processa, resposta é enviada
4. Verificar contato em `/api/contacts/{phone}` — memória e info salvos corretamente
5. Testar sandbox, config save, QR code, custos dashboard
