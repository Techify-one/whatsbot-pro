# Marcar mensagens como lidas no WhatsApp Android

## Context
Quando o usuário lê mensagens no frontend do WhatsBot, o status "lido" é atualizado apenas localmente (reset do `unread_count`). O WhatsApp no Android continua mostrando as mensagens como não lidas. O GOWA v8.3.3 expõe `POST /message/{message_id}/read` que envia read receipts ao WhatsApp, mas o WhatsBot não usa esse endpoint.

**Problema raiz:** O `msg_id` do WhatsApp (recebido no webhook) não é armazenado nas mensagens do contato, então não temos os IDs necessários para chamar o GOWA.

## Plano

### 1. Armazenar `msg_id` nas mensagens do contato
**Arquivo:** `agent/handler.py` — método `add_message()`
- Adicionar parâmetro opcional `msg_id: str | None = None`
- Salvar `msg_id` no dict da mensagem quando presente

### 2. Passar `msg_id` pelo fluxo do webhook
**Arquivo:** `server/app.py` — webhook handler (~linha 816)
- Incluir `msg_id` no dict de `state.pending_messages[phone]`
- No `_process_batch()`, passar os `msg_id`s para o `add_message()` do contato
- Incluir `msg_id` no broadcast WebSocket (`broadcast_msg`)

### 3. Rastrear `msg_id`s não lidos por contato
**Arquivo:** `agent/handler.py` — classe `ContactMemory`
- Adicionar campo `unread_msg_ids: list[str]` (persistido no JSON)
- Em `increment_unread()` aceitar `msg_id` e adicionar à lista
- Em `mark_as_read()` retornar a lista de `msg_id`s e limpar

### 4. Adicionar método `mark_as_read()` no GOWAClient
**Arquivo:** `gowa/client.py`
- Novo método `mark_as_read(message_id: str, phone: str) -> dict | None`
- Chama `POST /message/{message_id}/read` com body `{"phone": "{phone}@s.whatsapp.net"}`
- Não levanta exceção em caso de falha (best-effort)

### 5. Atualizar endpoint `/api/contacts/{phone}/read` e `GET /api/contacts/{phone}`
**Arquivo:** `server/app.py`
- Ao marcar como lido, obter os `msg_id`s não lidos do contato
- Chamar `gowa_client.mark_as_read()` para cada `msg_id`
- **IMPORTANTE:** Enviar read receipts em background via `asyncio.create_task()` para não bloquear a resposta da API (evita "piscar" na tela ao trocar de contato)
- Criar função helper `_send_read_receipts(phone, msg_ids)` reutilizada em ambos endpoints
- Tratar falhas silenciosamente (log warning) — a leitura local não deve falhar por causa do GOWA

## Arquivos a modificar
1. `gowa/client.py` — novo método `mark_as_read`
2. `agent/handler.py` — `add_message()` + `ContactMemory` (unread_msg_ids)
3. `server/app.py` — webhook, batch processing, endpoints read

## Lição aprendida
Na primeira implementação, os read receipts eram enviados de forma síncrona (await) antes de retornar a resposta da API. Isso causava delay perceptível ao trocar de contato no frontend ("piscar" na tela). A solução correta é disparar os read receipts em background com `asyncio.create_task()`.

## Verificação
1. Iniciar servidor, conectar WhatsApp
2. Enviar mensagem de teste via Evolution API
3. Verificar nos logs que `msg_id` aparece no webhook
4. Abrir o contato no frontend → verificar nos logs que `mark_as_read` foi chamado no GOWA
5. Confirmar que trocar de contato no frontend continua fluido (sem piscar)
6. Conferir no WhatsApp Android que a mensagem aparece como lida (check azul)
