# Configurar o Trackify para receber os eventos do WhatsBot

> Passo manual, feito **na interface do Trackify** — nenhuma linha de código muda lá.
> Enquanto isto não estiver pronto, deixe `Espelhar acontecimentos no Trackify` **desligado**
> nas configurações do plugin: a fila só acumularia erro.

## 0. Criar a API key (faça isto primeiro)

**Configurações → API Keys → Nova API Key**, com as três permissões:

| escopo | para quê |
|---|---|
| `read` | ler a jornada, a linha do tempo, os campos e o changelog |
| `contacts:write` | corrigir campo do contato (`PUT /contacts/:id`) |
| `ingest` | postar os eventos do espelho |

A chave aparece **uma única vez**, na criação — o Trackify guarda só o hash dela.
Copie e cole em **Plugins → Trackify → Configurar → Campos do contato → API key**,
e clique em **Testar acesso**: o veredito diz o nome da chave e as permissões que
ela carrega.

Essa chave é a **única credencial do plugin**. Não existe mais conta de serviço
(usuário e senha de gente) nem DSN de Postgres: as duas direções passam por HTTP
autenticado por ela. Revogar a chave no Trackify para a sincronização na hora.

## 1. Criar o canal

**Canais → Novo canal**

| campo | valor |
|---|---|
| Nome | `WhatsBot` |
| Slug | `whatsbot` |

Um canal só, não vários. O limite de ingestão é **por IP**, não por canal (criar três não
triplica a vazão), o dedup é `(channel_id, external_id)` e `channel` é a dimensão "fonte"
do CDP — um WhatsBot é uma fonte. O tipo do acontecimento é o que `event_type` distingue.

**Autenticação** — a rota de ingestão aceita a API key do módulo (escopo `ingest`), que
você já configurou no passo 0. Deixe `config.auth` como `{"type": "none"}` a menos que
outros produtores (gateways, formulários) também postem nesse canal — nesse caso mantenha
a chave própria do canal, que continua funcionando ao lado da do módulo:

```json
{ "auth": { "type": "api_key_header",
            "headerName": "X-Trackify-Key",
            "apiKey": "<gere 64 caracteres hexadecimais>" } }
```

Não use `signature`: ele valida HMAC sobre `req.rawBody`, que o bootstrap do Nexus não
habilita — a assinatura sairia calculada sobre string vazia e todo POST honesto falharia.
Não use `api_key_query`: a chave cairia em log de acesso do proxy.

⚠️ A chave do canal é **mascarada** na leitura da API desde a versão com API keys; a tela
mostra "Guardada — deixe em branco para manter". Digitar algo novo substitui.

## 2. Campos de evento (Campos personalizados → Eventos)

Todos do tipo **Texto**, exceto onde indicado. **Nenhum** com `sum_to_total_spent`.

| slug | nome | por quê |
|---|---|---|
| `atendente` | Atendente | quem cuidou do cliente |
| `canal` | Canal | inbox de origem |
| `protocolo_id` | Protocolo | liga ao protocolo do WhatsBot |
| `conversation_id` | Atendimento | liga à conversa |
| `aberto_por` | Aberto por | Contato / Atendente / IA |
| `motivo` | Motivo | preenchido em fechamento por limpeza de órfão |
| `nota` | Nota da avaliação | **Número** (1 a 5) |
| `sugestao` | Sugestão do cliente | texto livre da avaliação |
| `etiquetas` | Etiquetas | snapshot das tags |
| `etiqueta_removida` | Etiqueta removida | a remoção vira evento, não tira tag |
| `origem` | Origem | `inbound` / `manual` / `painel` |
| `wb_raw` | Payload bruto (WhatsBot) | escape forense — ver §4 |

⚠️ **Nunca marque `sum_to_total_spent` em nenhum destes.** `contacts.total_spent` é
monotônico e alimentá-lo promoveria todo lead a `customer` sem volta.

Os rótulos do protocolo chegam como `campo_<slug>` (ex.: `campo_motivo_contato`). Crie um
campo de evento para cada rótulo que você quiser consultável; os demais continuam visíveis
dentro de `wb_raw`.

## 3. Mapeamentos (Canal `whatsbot` → Mapeamentos)

Todos ativos, todos com `sum_to_total_spent = false`.

| expressão (JSONata) | entidade | campo destino |
|---|---|---|
| `identity.whatsapp` | contact | `whatsapp` |
| `identity.email` | contact | `email` |
| `identity.telegram_id` | contact | `telegram_id` |
| `contact.name` | contact | `name` |
| `kind` | event | `event_type` |
| `title` | event | `title` |
| `external_id` | event | `external_id` |
| `occurred_at` | event | `occurred_at` |
| `data.atendente` | event | `atendente` |
| `data.canal` | event | `canal` |
| `data.protocolo_id` | event | `protocolo_id` |
| `data.conversation_id` | event | `conversation_id` |
| `data.aberto_por` | event | `aberto_por` |
| `data.motivo` | event | `motivo` |
| `data.nota` | event | `nota` |
| `data.sugestao` | event | `sugestao` |
| `data.etiquetas` | event | `etiquetas` |
| `data.etiqueta_removida` | event | `etiqueta_removida` |
| `data.origem` | event | `origem` |
| `$string($)` | event | `wb_raw` |

Uma tabela só serve todos os tipos: expressão que resolve `undefined` é ignorada pelo
adapter, então `data.nota` simplesmente não dispara num `conversation_created`.

**Três coisas que NÃO devem ser mapeadas:**

- **`value`** — alimentaria `total_spent` (ver §2).
- **`contact.tags`** — a ingestão do Trackify é aditiva por regra dura: tag entra, nunca
  sai. Como o WhatsBot também remove etiqueta, mapear isso faria as tags do CDP derivarem
  só para cima e nunca voltarem. A remoção vira **evento** (`etiqueta_removida`), que
  registra o fato sem mentir sobre o estado.
- **qualquer campo que não exista** — o validador recusa com 422 e a linha inteira fica
  `blocked` na fila do WhatsBot.

## 4. Sobre o `wb_raw`

Guarda o envelope inteiro serializado. Existe porque a rota "metadata" do adapter é
inalcançável hoje (o validador recusa destino desconhecido antes de chegar nela), então
sem este mapping qualquer campo novo do WhatsBot se perderia em silêncio até alguém criar
o campo correspondente. Com ele, o dado chega e fica consultável mesmo antes do mapping.

## 5. Ordem de ativação (não pule)

1. API key criada (passo 0) e testada; canal + campos + mapeamentos criados.
2. No WhatsBot: `URL de ingestão` = `https://SEU-NEXUS/trackify/api/v1/ingestion/whatsbot`.
3. Ligue **Espelhar acontecimentos** deixando **Modo seco LIGADO**. A fila enche e nada é
   postado — confira o envelope e os `external_id` em Plugins → Trackify → fila.
4. Desligue o modo seco e observe o primeiro punhado em `Fila`. Confira no Trackify que os
   eventos caíram **no contato certo**, não num contato novo.
5. Só então deixe rodando.

O passo 4 é o que pega erro de mapeamento: um `422` põe a linha em `blocked` com o motivo,
e o botão **Reprocessar** devolve tudo à fila depois que você consertar o JSONata.
