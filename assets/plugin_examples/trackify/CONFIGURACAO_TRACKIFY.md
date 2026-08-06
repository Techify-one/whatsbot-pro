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

---

## 6. Descadastro de marketing por clique em botão (aba "Descadastro por botão")

Quando o contato toca um botão de um template de marketing num canal de **WhatsApp
oficial**, o plugin grava o resultado num campo do cadastro dele no Trackify. É esse
campo que o módulo **Campanhas de Marketing** lê para montar a lista de disparo — ou
seja, depois desta mudança o CDP é o **único elo** entre os dois sistemas.

### 6.1 O contrato do campo (leia antes de escolher os valores)

O Campanhas aplica o gate em SQL como
`campo IS NULL OR lower(btrim(campo)) IN (...)`:

| Valor no campo | Efeito no disparo |
|---|---|
| vazio · `nao` · `não` · `n` · `no` · `false` · `0` · campo ausente | **entra** na lista |
| qualquer outro valor (`sim`, `true`, `1`, uma data ISO, texto livre) | **fica de fora** |

Por isso o padrão do plugin é gravar **`sim`** ao descadastrar e **vazio** ao voltar a
receber (vazio APAGA a linha do campo no CDP, que é o estado mais limpo). A tela recusa
uma combinação incoerente — escolher `0` como "descadastrado" produziria uma
configuração que parece funcionar e não bloqueia ninguém.

### 6.2 O slug tem de ser o mesmo nos dois lados

- No **plugin**: campo *"Campo de descadastro no Trackify"*.
- No **Campanhas**: chave de configuração `trackify_campo_optout`
  (aba Configurações → Trackify), cujo padrão é `optout_marketing`.

Apontar para slugs diferentes **não quebra nada**: o valor é gravado, a fila fica verde,
e o disparo continua indo para quem pediu para sair. É a falha silenciosa mais provável
desta integração — confira os dois antes de tirar o modo seco.

O campo tem de existir e estar **ativo** no Trackify (Campos personalizados → Contatos).
Slug desativado ou inexistente é **ignorado em silêncio** pelo PUT, que devolve 200 —
o plugin confere a resposta campo a campo e põe o item em `blocked` com o motivo, em vez
de contar sucesso numa escrita que nunca aconteceu.

### 6.3 Só canal de WhatsApp oficial

A feature só opera em canais com provider `whatsapp_cloud`. GOWA, Telegram e teste
**nunca** participam — nem sequer registram o clique. Além do gate de provider, a
allow-list da aba é **fail-closed**: sem canal marcado, nada é gravado.

> **Limitação conhecida desta fase:** um contato que responde "SAIR" ou clica em botão
> num canal não-oficial deixa de ser descadastrado automaticamente. Nesses casos, marque
> o campo à mão na ficha do contato no Trackify.

### 6.4 Como descobrir o texto do botão

O casamento é pelo **texto ou payload do botão**, não pelo nome do template: o objeto
que a Meta manda no clique não carrega nome de template nem índice de botão.

Como o template normalmente é criado do lado do Campanhas e a Meta **ecoa o próprio
rótulo** quando não há payload explícito, há duas formas de preencher sem adivinhar:

- **Importar dos templates** — lista os templates `MARKETING` da conta Meta do canal que
  têm botão de resposta rápida (os de utilidade e autenticação não aparecem).
- **Botões vistos recentemente** — todo clique reconhecido que ainda não casou regra é
  registrado com contador e data, mesmo em modo seco e mesmo com a captura desligada.
  Um clique em "Usar como regra" preenche a linha com a string exata.

### 6.5 Ordem de ativação

1. Confirme que o número que dispara a campanha está conectado como canal
   `whatsapp_cloud` **neste** WhatsBot e que o *Webhook URL* do app Meta aponta para
   `https://SEU-WHATSBOT/api/webhook/whatsapp_cloud/{channel_id}`.
   A Meta aceita **um** webhook por app: enquanto ele apontar para outro lugar, nenhum
   clique chega aqui.
2. Confira o slug nos dois lados (6.2) e os valores (6.1).
3. Marque os canais e ligue **Registrar descadastro por clique em botão** deixando o
   **modo seco LIGADO**.
4. Dispare um template com botão para um número de teste e clique. O botão aparece em
   *"Botões vistos recentemente"* — vire regra a partir dali.
5. Clique de novo e confira, na **Fila de gravação**, o item concluído com
   `[modo seco] gravaria …`.
6. Desligue o modo seco, repita o clique e confira o campo na ficha do contato no
   Trackify. Depois confirme no Campanhas que esse contato **não aparece mais** ao montar
   uma lista.

### 6.6 Não mapeie o mesmo campo na aba "Campos do contato"

Se o slug do descadastro também for mapeado lá, a leitura periódica do Trackify pode
**desfazer** um descadastro trazendo o valor antigo de volta. As duas abas se recusam a
reivindicar o mesmo campo — a mensagem aponta a outra.
