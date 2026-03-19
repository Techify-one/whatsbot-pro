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
server/app.py        → FastAPI app (endpoints REST, WebSocket, background tasks)
gowa/manager.py      → lifecycle do subprocess GOWA (start/stop/watchdog)
gowa/client.py       → HTTP client para REST API do GOWA (localhost:3000)
agent/handler.py     → processa mensagens com LLM via OpenRouter
config/settings.py   → load/save config.json em %APPDATA%/WhatsBot/
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

## API REST do WhatsBot (backend FastAPI, porta 8080)

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

Binário iniciado com: `gowa.exe rest --port 3000`

## Convenções de código

- Python com type hints nas assinaturas de função
- Logging via `logging` stdlib (nunca print)
- Operações bloqueantes (GOWA, OpenRouter) usam `asyncio.to_thread()` no backend FastAPI
- Nomes de variáveis e comentários em inglês; textos exibidos ao usuário em português BR
- Tratar respostas da API GOWA com fallback para nomes de campo alternativos (a API não é 100% consistente nos nomes)
- Frontend: ES modules, componentes Preact em PascalCase, services/hooks em camelCase

## Dados do usuário

Tudo salvo em `%APPDATA%/WhatsBot/` (Windows) ou `~/.config/WhatsBot/` (Linux):
- `config.json` — configurações do usuário
- `logs/` — logs com rotação

## Gotchas

- O GOWA demora ~5s para iniciar e aceitar conexões — o polling de QR/status deve tolerar falhas silenciosamente
- **Device obrigatório**: `POST /devices` deve ser chamado antes de qualquer outro endpoint; sem device registrado, tudo retorna 404 `DEVICE_NOT_FOUND`
- **Login quando já conectado**: `GET /app/login` retorna erro `ALREADY_LOGGED_IN` se o device já está autenticado — verificar `is_connected()` antes de pedir QR
- **Respostas aninhadas**: listas de chats/mensagens vêm em `results.data[]`, não direto em `results`
- JIDs do WhatsApp seguem formato `5511999999999@s.whatsapp.net` — extrair phone com `.split("@")[0]`
- PyInstaller no Windows: paths de binários e web/ mudam (`sys._MEIPASS`), tratado em `gowa/manager.py` e `server/app.py`
- `subprocess.CREATE_NO_WINDOW` é necessário no Windows para não abrir janela de console do GOWA
- Config auto-salva no shutdown do server (lifespan) e na primeira execução (`Settings.load`)
- Frontend vendorizado: libs JS em `web/static/vendor/` — sem dependência de CDN em runtime
