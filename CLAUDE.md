# WhatsBot

Bot de WhatsApp com IA para usuários finais, distribuído como EXE Windows.

## Stack

- **Python 3.11+** — linguagem principal
- **GOWA** (go-whatsapp-web-multidevice v8.3.3) — bridge WhatsApp via REST, roda como subprocess
- **OpenRouter** — LLM provider (API compatível com OpenAI)
- **FastAPI + uvicorn** — backend web (REST API + WebSocket)
- **Preact + HTM + Tailwind CSS** — frontend web (sem build step, vendorizado local)
- **PyInstaller** — empacotamento como EXE

## Arquitetura

```
main.py              → entry point, inicia uvicorn + abre browser
server/app.py        → FastAPI app (endpoints REST, WebSocket, webhook, background tasks)
gowa/manager.py      → lifecycle do subprocess GOWA (start/stop/watchdog)
gowa/client.py       → HTTP client para REST API do GOWA (localhost:3000)
agent/handler.py     → processa mensagens com LLM via OpenRouter (tool calling)
agent/tools/         → definições de tools do LLM (uma tool por arquivo, exportadas em __init__.py)
config/settings.py   → load/save config.json na pasta do projeto
web/index.html       → entry point do frontend (HTML + import map)
web/static/js/       → componentes Preact + HTM (sem build step)
web/static/vendor/   → libs JS vendorizadas (preact, htm, tailwind)
bin/gowa.exe         → binário GOWA pré-compilado (não editar)
```

## Comandos

```bash
# Dev (Windows)
run_dev.bat

# Build EXE
build.bat

# Instalar deps manualmente
pip install -r requirements.txt
python main.py
```

## Fluxo de mensagens (webhook)

Mensagens recebidas no WhatsApp são entregues em tempo real via webhook do GOWA:

1. GOWA inicia com `--webhook http://127.0.0.1:{web_port}/api/webhook`
2. Mensagem chega → GOWA faz POST em `/api/webhook` com payload contendo `body`, `from`, `id`, `is_from_me`
3. Webhook acumula mensagens do mesmo contato por `message_batch_delay` segundos (padrão: 3s) — se o contato enviar várias mensagens em sequência, são juntadas em uma só
4. Após o delay, `_process_batch()` junta os textos com `\n` e chama `agent_handler.process_message()`
5. O AgentHandler faz a chamada ao LLM com tool calling — se o LLM detectar dados pessoais (nome, email, profissão, empresa), chama `save_contact_info` automaticamente
6. Resposta é enviada via `gowa_client.send_message()`

**NÃO usa polling** — o auto-reply por polling foi removido. Toda recepção de mensagens é via webhook.

## Memória por contato

Cada contato tem um arquivo JSON em `contacts/{phone}.json`:

```json
{
  "phone": "5511999999999",
  "info": {"name": "", "email": "", "profession": "", "company": "", "observations": []},
  "messages": [{"role": "user|assistant", "content": "...", "ts": 1234567890}],
  "created_at": 1234567890,
  "updated_at": 1234567890
}
```

- `info` é salvo automaticamente via tool calling do LLM e injetado no system prompt
- `messages` é o histórico completo; apenas as últimas N (configurável) são enviadas ao LLM
- Histórico persiste entre reinícios do app

## API REST do WhatsBot (backend FastAPI)

| Método | Endpoint | Descrição |
|--------|----------|-----------|
| GET | `/` | Serve o frontend (web/index.html) |
| GET | `/api/config` | Retorna config (API key mascarada) |
| PUT | `/api/config` | Salva config + atualiza AgentHandler |
| POST | `/api/config/test-key` | Testa API key OpenRouter |
| GET | `/api/status` | Status de conexão + contagem de msgs |
| GET | `/api/qr` | QR code como PNG (204 se indisponível) |
| POST | `/api/whatsapp/reconnect` | Reconectar GOWA |
| POST | `/api/whatsapp/logout` | Logout GOWA |
| POST | `/api/webhook` | Recebe mensagens do GOWA (webhook) |
| GET | `/api/contacts?archived=true` | Lista apenas contatos/grupos arquivados |
| GET | `/api/webhook-payloads?limit=N` | Últimos N payloads raw do webhook (debug, max 50) |
| WS | `/ws` | WebSocket para eventos real-time |

Formato de resposta REST: `{"ok": bool, "data": ..., "error": ...}`

Eventos WebSocket: `{"event": "status|qr_update|gowa_status|config_saved", "data": {...}}`

## GOWA REST API (endpoints reais — v8.3.3 multi-device)

IMPORTANTE: O GOWA v8.3.3 é multi-device. Antes de usar qualquer endpoint, é necessário criar um device via `POST /devices`. Após criação, todas as requests (exceto `/devices`) exigem header `X-Device-Id`.

| Operação | Método | Endpoint | Notas |
|---|---|---|---|
| Listar devices | GET | `/devices` | Sem header obrigatório |
| Criar device | POST | `/devices` body: `{device_id?}` | Sem header, retorna device_id |
| Login/QR | GET | `/app/login` | Retorna JSON com `results.qr_link` (URL do PNG) |
| Status | GET | `/app/status` | Retorna `results.is_connected`, `results.is_logged_in` |
| Logout | GET | `/app/logout` | |
| Reconectar | GET | `/app/reconnect` | |
| Enviar msg | POST | `/send/message` body: `{phone, message}` | |
| Listar chats | GET | `/chats?limit=N` | Resposta aninhada: `results.data[]` |
| Msgs do chat | GET | `/chat/{jid}/messages?limit=N` | Resposta aninhada: `results.data[]` |

Binário iniciado com: `gowa.exe rest --port 3000 --webhook http://127.0.0.1:{web_port}/api/webhook`

Campos do payload do webhook GOWA: `body`, `from`, `sender_jid`, `chat_id`, `id`, `is_from_me`, `timestamp`, `from_name`

## Convenções de código

- Python com type hints nas assinaturas de função
- Logging via `logging` stdlib (nunca print)
- Operações bloqueantes (GOWA, OpenRouter) usam `asyncio.to_thread()` no backend FastAPI
- Nomes de variáveis e comentários em inglês; textos exibidos ao usuário em português BR
- Tratar respostas da API GOWA com fallback para nomes de campo alternativos (a API não é 100% consistente nos nomes)
- Frontend: ES modules, componentes Preact em PascalCase, services/hooks em camelCase
- **Tools do LLM**: sempre criar em `agent/tools/`, um arquivo por tool, e exportar em `agent/tools/__init__.py` na lista `ALL_TOOLS`. Nunca definir tools inline no handler

## Dados do projeto

Tudo salvo na pasta raiz do projeto (dev) ou junto ao EXE (PyInstaller):
- `config.json` — configurações do usuário
- `contacts/` — memória persistente por contato (JSON por telefone)
- `logs/` — logs com rotação
- `storages/` — dados do GOWA (sessão WhatsApp)
- **Webhook payloads (debug)**: últimos 50 payloads raw do GOWA em memória, acessíveis via `GET /api/webhook-payloads`
- **Contatos arquivados**: ao receber mensagem de um contato, o webhook consulta `gowa_client.is_chat_archived(jid)` e persiste `is_archived` no JSON do contato. A sidebar filtra por `?archived=true/false`. O status de archive é atualizado on-demand (não por polling)

## Teste opcional com Evolution API

Se você tiver acesso a uma instância da Evolution API, pode testar o fluxo de mensagens de ponta a ponta. Isso é opcional, mas recomendado ao alterar webhook, agent, handler ou batching.

Variáveis de teste devem ser configuradas no arquivo `.env`:
- `EVOLUTION_API_URL` — URL base da Evolution API
- `EVOLUTION_API_KEY` — API key de autenticação
- `EVOLUTION_INSTANCE_ID` — ID da instância Evolution
- `EVOLUTION_TEST_NUMBER` — número WhatsApp para receber a mensagem de teste

### Como testar

1. Garanta que o servidor está rodando e conectado (`curl /api/status` → `connected: true`)
2. Envie mensagem de teste via Evolution API:
```bash
source .env
curl -X POST "${EVOLUTION_API_URL}/message/sendText/${EVOLUTION_INSTANCE_ID}" \
  -H "Content-Type: application/json" \
  -H "apikey: ${EVOLUTION_API_KEY}" \
  -d "{\"number\": \"${EVOLUTION_TEST_NUMBER}\", \"text\": \"mensagem de teste\"}"
```
3. Aguarde ~10 segundos e verifique os logs:
```bash
curl -s http://127.0.0.1:{web_port}/api/logs?limit=10
```
4. Confirme nos logs que aparece:
   - `[Webhook] Message from ...` — mensagem recebida
   - `[Batch] Processing N messages ...` — batch processado
   - `[Batch] Replied to ...` — resposta enviada
5. Se o teste envolveu dados pessoais, verifique o arquivo `contacts/{phone}.json` para confirmar que `info` foi atualizado

### Processo de teste para kill/restart

```bash
# Matar processos anteriores
taskkill //F //IM gowa.exe 2>&1; taskkill //F //IM python.exe 2>&1

# Iniciar servidor
source venv/Scripts/activate
python -c "import uvicorn; from server.dev import app; uvicorn.run(app, host='127.0.0.1', port=8080, log_level='info')"
```

## Gotchas

- O GOWA demora ~5s para iniciar e aceitar conexões — o polling de QR/status deve tolerar falhas silenciosamente
- **Device obrigatório**: `POST /devices` deve ser chamado antes de qualquer outro endpoint; sem device registrado, tudo retorna 404 `DEVICE_NOT_FOUND`
- **Login quando já conectado**: `GET /app/login` retorna erro `ALREADY_LOGGED_IN` se o device já está autenticado — verificar `is_connected()` antes de pedir QR
- **Respostas aninhadas**: listas de chats/mensagens vêm em `results.data[]`, não direto em `results`
- JIDs do WhatsApp seguem formato `5511999999999@s.whatsapp.net` — extrair phone com `.split("@")[0]`
- PyInstaller no Windows: paths de binários e web/ mudam (`sys._MEIPASS`), tratado em `gowa/manager.py` e `server/app.py`
- `subprocess.CREATE_NO_WINDOW` é necessário no Windows para não abrir janela de console do GOWA
- GOWA usa `stdout=subprocess.DEVNULL` — NUNCA usar `subprocess.PIPE` sem consumir, causa deadlock no Windows
- Config auto-salva no shutdown do server (lifespan) e na primeira execução (`Settings.load`)
- Frontend vendorizado: libs JS em `web/static/vendor/` — sem dependência de CDN em runtime
- **Sockets fantasma no Windows**: ao reiniciar frequentemente, portas podem ficar presas em LISTENING com PIDs inexistentes. Use porta alternativa ou reinicie o PC
- **run_dev.bat mata processos**: o bat já executa `taskkill` para gowa.exe e uvicorn.exe antes de iniciar
- **GOWA `/chats` limit máximo**: `GET /chats?limit=N` retorna HTTP 400 para valores acima de ~200. Usar `limit=100` como máximo seguro
- **Archive status é chat-level**: o webhook do GOWA **não** inclui campo de archive no payload. Para saber se um chat é arquivado, consultar `GET /chats` e verificar o campo `archived` no item com o `jid` correspondente
