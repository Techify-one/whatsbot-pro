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

Quando o contato toca o **botão de descadastro** de um template num canal de **WhatsApp
oficial**, o plugin grava um valor num campo do cadastro dele no Trackify. É esse campo
que o módulo **Campanhas de Marketing** lê para montar a lista de disparo — ou seja, o
CDP é o **único elo** entre os dois sistemas.

O reconhecimento é por **código**. O Campanhas põe um código livre em cada botão do
template e **não sabe o que ele significa** — quem define a função é esta aba. Hoje há uma
função: *Descadastro de marketing*. Qualquer clique cujo *payload* seja igual a **um dos
códigos** listados nela significa "não quero mais receber disparos".

Código que não está em ação nenhuma é **ignorado em silêncio** — esse é o caso comum, já
que a maioria dos botões de um template não fala com este plugin.

### 6.1 O contrato do campo (leia antes de escolher o valor)

O Campanhas aplica o gate em SQL como
`campo IS NULL OR lower(btrim(campo)) IN (...)`:

| Valor no campo | Efeito no disparo |
|---|---|
| vazio · `nao` · `não` · `n` · `no` · `false` · `0` · campo ausente | **entra** na lista |
| qualquer outro valor (`sim`, `true`, `1`, uma data ISO, texto livre) | **fica de fora** |

Por isso o padrão do plugin é gravar **`sim`** ao descadastrar. A tela recusa um valor
incoerente — escolher `0` como "descadastrado" produziria uma configuração que parece
funcionar e não bloqueia ninguém.

> Não existe mais "voltar a receber" por botão. Reinscrever é ação de operador: apague
> o valor do campo na ficha do contato no Trackify.

### 6.2 O que tem de bater nos dois lados

| O quê | No plugin (aba "Descadastro por botão") | No Campanhas |
|---|---|---|
| Campo no CDP | *Campo de descadastro no Trackify*, na linha da ação | `trackify_campo_optout` (Configurações → Trackify) |
| Valor gravado | *Valor ao descadastrar*, na linha da ação | `trackify_valor_optout` (Configurações → Trackify) |

Campo e valor são pareados **1-para-1** e divergir neles **não quebra nada**: nenhum erro
aparece e o disparo continua indo para quem pediu para sair. É a falha silenciosa mais
provável desta integração — confira os dois antes de tirar o modo seco.

O **código** já não é uma chave única do outro lado. No Campanhas, cada botão de cada
template da Meta tem o seu, digitado no próprio template. A regra é:

> Todo código que o Campanhas puser num **botão de descadastro** tem de estar na lista de
> *Códigos que significam descadastro* desta aba.

Vários códigos, **separados por vírgula**:

```
PARAR_PROMOS, PARAR_PROMOS_2
```

Ao trocar o código de um template, **acrescente o novo sem remover o antigo**: campanhas
já disparadas continuam recebendo cliques por dias, e apagar o antigo faria esses cliques
pararem de descadastrar em silêncio.

O campo tem de existir e estar **ativo** no Trackify (Campos personalizados → Contatos).
Slug desativado ou inexistente é **ignorado em silêncio** pelo PUT, que devolve 200 —
o plugin confere a resposta campo a campo e registra o motivo, em vez de contar sucesso
numa escrita que nunca aconteceu.

### 6.3 Só canal de WhatsApp oficial

A feature só opera em canais com provider `whatsapp_cloud`, e vale para **todos** eles.
GOWA, Telegram e teste **nunca** participam.

> **Limitação conhecida:** um contato que responde "SAIR" por texto, ou clica num botão
> de canal não-oficial, não é descadastrado automaticamente. Nesses casos, marque o campo
> à mão na ficha do contato no Trackify.

### 6.4 De onde vem o código

Quem coloca o código no botão é o **Campanhas**, no momento do disparo: ao montar o
template da campanha, cada botão de resposta rápida recebe um código, e ele sai como o
*payload* daquele botão.

Por isso não é preciso descobrir qual string a Meta devolve — é você quem a define. Vale
o contrário também: um template antigo, enviado sem payload explícito, faz a Meta ecoar o
**rótulo** do botão, que nunca coincide com o código. Ele simplesmente não descadastra
ninguém.

Escolha códigos curtos e sem espaço (`PARAR_PROMOS`, `OPTOUT_MKT`). Maiúscula/minúscula
e acento **não importam** na comparação; emoji e pontuação, sim.

> **Um código só pode pertencer a uma ação.** Se o mesmo código aparecer em duas, ele
> deixa de valer para as duas — a aba mostra o conflito em vermelho. Os demais códigos das
> duas ações continuam funcionando normalmente.

### 6.5 Ordem de ativação

1. Confirme que o número que dispara a campanha está conectado como canal
   `whatsapp_cloud` **neste** WhatsBot e que o *Webhook URL* do app Meta aponta para
   `https://SEU-WHATSBOT/api/webhook/whatsapp_cloud/{channel_id}`.
   A Meta aceita **um** webhook por app: enquanto ele apontar para outro lugar, nenhum
   clique chega aqui.
2. Confira a lista de códigos, o par campo/valor (6.2) e o contrato do valor (6.1). Os
   códigos que a aba reconheceu aparecem como etiquetas abaixo do campo — se um que você
   digitou não estiver lá, ele está em conflito com outra ação.
3. Ligue **Registrar ações de clique em botão** deixando o **modo seco LIGADO**.
4. No Campanhas, dispare para um número de teste um template cujo botão de descadastro
   leve um dos códigos da lista, e clique nele.
5. Confira na ficha do contato no Trackify que **nada** foi gravado (modo seco) — e no
   próprio painel, que o clique não caiu na lista de erros da aba.
6. Desligue o modo seco, repita o clique e confira o campo na ficha do contato no
   Trackify. Depois confirme no Campanhas que esse contato passa a ser **filtrado por
   padrão** ao montar uma lista.

### 6.6 Não mapeie o mesmo campo na aba "Campos do contato"

Se o campo escrito por uma ação também for mapeado lá, a leitura periódica do Trackify
pode **desfazer** o que ela gravou, trazendo o valor antigo de volta. As duas abas se
recusam a reivindicar o mesmo campo — a mensagem aponta a outra.

### 6.7 Um clique pode se perder (e como ver)

A gravação acontece **na hora do clique**, com poucas tentativas internas. Não há fila:
se o Trackify estiver fora do ar por vários minutos, ou o servidor reiniciar no meio da
entrega, aquele pedido **não é tentado de novo**.

O que falhou aparece na linha da própria ação, em *"Cliques que não chegaram ao
Trackify"*, com o motivo e a data — marque esses contatos à mão no CDP. A causa mais
comum não é queda de rede, e sim **contato sem cadastro vinculado** no Trackify.

### 6.8 Funções novas

Cada função é uma **ação** declarada no código do plugin (`actions.py`), e a aba desenha
uma linha por ação registrada: seu nome, seus códigos e os campos que ela grava. Hoje só
existe *Descadastro de marketing*.

Para o operador isso significa que uma função nova aparece sozinha na aba depois de uma
atualização do plugin, com o seu próprio campo de códigos — não há nada a cadastrar aqui,
e os códigos de uma ação **nunca** disparam outra.
