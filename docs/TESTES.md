# Testes — suítes do core, banco de teste e plugins

> Guia de como rodar e organizar os testes. O [`CLAUDE.md`](../CLAUDE.md) carrega a **regra curta** e os avisos ⚠️;
> aqui está o **porquê**, o histórico e o detalhe. Texto migrado do `CLAUDE.md` no plano 139
> — nada foi reescrito na migração, só realocado.

---

## Testes automatizados

Os testes do core estão separados por responsabilidade:

- `tests/core/`: unidades, caracterização interna e runner das suítes legadas;
- `tests/contracts/`: contratos públicos que qualquer plugin pode consumir;
- `tests/integration/`: API, Postgres e costuras entre componentes do core.

A suíte roda **contra um Postgres de teste** quando necessário. A URL vem de `WHATSBOT_TEST_DB_URL` (env ou `.env`) e [tests/pg.py](../tests/pg.py) recria o schema uma vez por processo. A trava exige que o nome do banco contenha `test`, salvo override explícito.

```bash
# Core inteiro; pyproject.toml limita a coleta às três árvores acima
venv/bin/python -m pytest

# Uma camada isolada
venv/bin/python -m pytest tests/contracts
```

Não rode duas suítes PostgreSQL em paralelo: cada processo recria o mesmo schema `public`. O pytest do core **não descobre** testes em `storages/plugins` e não modifica plugins instalados.

Os testes dos plugins rodam somente no repositório externo, por comando explícito:

```bash
cd ../whatsbot-pro-plugins
python3 scripts/test_plugins.py protocolos
python3 scripts/test_plugins.py --all
```

O runner injeta `WHATSBOT_CORE_ROOT` e `WHATSBOT_PLUGIN_SOURCE_ROOT`, reaproveita as fixtures públicas de [tests/plugin_fixtures.py](../tests/plugin_fixtures.py) e executa cada plugin separadamente. Instalar, atualizar, ativar ou iniciar um plugin em produção **nunca executa esses testes**.

Testes do core que ainda precisam de uma fonte real usam [tests/plugin_test_utils.py](../tests/plugin_test_utils.py): a resolução prefere `WHATSBOT_PLUGIN_SOURCE_ROOT/<id>/src`, cai em `assets/plugin_examples/<id>/` (hoje só o `gowa`) e só depois na instalação. Para contratos genéricos, prefira [tests/fake_provider.py](../tests/fake_provider.py). Teste de costura deve usar [tests/support.py](../tests/support.py) e o namespace canônico `whatsbot_plugins.<id>.*`.

Os testes inserem dados de teste (contatos, mensagens, tags, usage); o runner `tests/core/test_legacy_scripts.py` ainda executa a suíte histórica de endpoints como subprocesso durante a transição, além dos testes pytest nativos, cobrindo:
- Health, Auth (com e sem senha), Config (GET/PUT/test-key, `group_reply_mode`), Status, Balance
- Contacts (list, detail, search, archived, send, retry, image, audio, presence, read, toggle-ai, update info, **pin/unpin**, **unread/mark-all-read/mark-all-unread**, **unread-count**, **@menção em grupo / has_unread_mention**, **react/delete de mensagem**, **members** de grupo)
- Tags (CRUD + contact tags)
- Usage (summary, by-contact, detail)
- Logs, Webhook payloads, Webhook (presence, echo, ack, reaction, reply/quoted, revoke)
- WhatsApp/QR (get, refresh, reconnect, logout)
- Sandbox (send, clear)
- Frontend SPA routes (inclui `/wizard`)
- Auth middleware (proteção de endpoints, exemptions)

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

### Processo de teste para kill/restart

```bash
# Matar processos anteriores
taskkill //F //IM gowa.exe 2>&1; taskkill //F //IM python.exe 2>&1

# Iniciar servidor
source venv/Scripts/activate
python -c "import uvicorn; from server.dev import app; uvicorn.run(app, host='127.0.0.1', port=8080, log_level='info')"
```
