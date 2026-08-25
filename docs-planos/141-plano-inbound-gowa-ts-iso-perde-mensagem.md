# Plano 141 — A mensagem do cliente não pode sumir: o `timestamp` ISO do GOWA derruba o INSERT do inbound

> **Status:** PLANEJAMENTO · **Data:** 2026-08-25 · **Escopo:** médio (core; 4 arquivos de produção + testes; **zero migration, zero mudança de plugin**)
> **Origem:** incidente relatado pelo operador — "tem uma conversa que abriu porém não tem nada nela" (conversa `16156`, painel de produção). **Método:** leitura do código real com `arquivo:linha` verificados + investigação somente-leitura no banco de produção pelo cofre de credenciais e no plugin `debug_bus` (a identificação da credencial fica fora deste documento — repositório público).
> **O quê/porquê:** o GOWA manda `payload.timestamp` como **string RFC 3339** (`"2026-08-24T17:43:58Z"`); [gowa/inbound.py:734](../gowa/inbound.py#L734) repassa o valor **sem coerção**, e desde o **plano 129** ([34f7764](../gowa/inbound.py), 2026-08-18) esse valor viaja até `messages.ts`, que é `double precision`. O INSERT levanta `InvalidTextRepresentation`, a exceção é engolida em [messaging_service.py:1768-1769](../app/services/messaging_service.py#L1768-L1769) e **a mensagem do cliente é destruída** — a fila em memória já foi consumida em [messaging_service.py:1816](../app/services/messaging_service.py#L1816). Medido: **zero** mensagens `role='user'` em todas as inboxes GOWA desde 2026-08-17 17:15, contra 3–45/dia antes.
>
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0 — Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 ✅ (2026-08-25) | O `ts` real do GOWA é **preservado**, não descartado. O helper entende epoch **e** RFC 3339. | Só cair em `time.time()` quando o valor for ininterpretável. Descartar seria desfazer o plano 129 justamente no canal onde ele mais importa. |
| D2 ✅ (2026-08-25) | A correção é em **três camadas** (parser → contrato → repositório), não só no parser. | O desastre não foi o carimbo errado: foi **a mensagem do cliente sumir**. Uma camada só conserta este payload; três impedem a classe inteira. |
| D3 ✅ (2026-08-25) | `message_repo.add` **nunca** deixa um `ts` ruim derrubar o INSERT. Falha de carimbo ≠ perda de mensagem. | Última linha de defesa: valor não-numérico vira `time.time()` + `logger.warning`, e a linha entra. |
| D4 ✅ (2026-08-25) | **Zero migration.** `messages.ts` já é `Float` ([db/tables.py:122](../db/tables.py#L122)) e o repo já aceita `ts=`. | Nada de schema neste plano. |
| D5 ✅ (2026-08-25) | Nenhuma mudança em plugin, nenhum bump de `WHATSBOT_API_VERSION`. | O bug é 100 % core — inclusive porque **o código do GOWA mora no core**, não na pasta do plugin (§2.4.1). Sem zip, sem rebuild, sem re-importação: sai em deploy normal. Confirmação do guard fica na F5. |
| D7 ✅ (2026-08-25) | Das quatro fases de código, **só a F1 é do GOWA**. F2/F3/F4 valem para **todo canal**. | Hoje nenhum outro provider tem o defeito (§2.4), mas a *classe* alcança qualquer provider futuro — inclusive plugin de terceiro que o core não revisa. Corrigir só a F1 deixaria a armadilha armada. |
| D6 ✅ (2026-08-25) | A falha de save do inbound passa a ser **visível** (bolha de erro + log `ERROR`), mesmo depois de o `ts` estar consertado. | Sem isso, o próximo defeito no mesmo trecho volta a apagar mensagem em silêncio por 6 dias. |

**Princípio fixo:** neste caminho, **perder mensagem de cliente é o pior desfecho possível** — pior que carimbo errado, que ordem errada no fio, que bolha feia. Toda escolha deste plano se resolve por aí.

---

## 1 — Resumo executivo

O plano 129 passou a persistir o timestamp REAL do provedor no inbound, e acertou o desenho: o guard escolhido foi `event.ts or None`, protegendo contra **ausente/zero**. O que ninguém protegeu foi o **tipo**. Dos cinco providers, quatro coagem o valor com um `_to_float()` local; o quinto — o GOWA, único que mora no core — repassa cru, e é o único que manda **string ISO** em vez de epoch.

Resultado: desde 2026-08-18, **todo inbound 1:1 dos canais GOWA morre no INSERT**. O que mascarou o defeito por 6 dias foi o filtro de tipo de JID: os três canais GOWA só aceitam `person`/`person_lid`, e ~99 % do tráfego deles é grupo — descartado no portão, **antes** do ponto de crash. Hoje (24/08): 104 webhooks de grupo descartados, **1** mensagem de pessoa, que quebrou.

A correção tem três camadas e uma quarta preocupação:

1. **Parser** — o GOWA passa a interpretar RFC 3339 e devolver epoch de verdade (D1).
2. **Contrato** — `InboundEvent.__post_init__` coage `ts`, para que **nenhum** provider (core ou plugin de terceiro) consiga injetar um não-float de novo.
3. **Repositório** — `message_repo.add` recusa carimbo ruim sem recusar a mensagem (D3).
4. **Visibilidade** — a falha de save do inbound deixa de ser silenciosa (D6).

---

## 2 — Como funciona hoje (mapa verificado)

### 2.1 O caminho da mensagem, e onde ele quebra

| # | Passo | Arquivo:linha | Estado do `ts` |
|---|---|---|---|
| 1 | GOWA POSTa o webhook | `POST /api/webhook/gowa/{channel_id}` | `payload.timestamp = "2026-08-24T17:43:58Z"` (**str**) |
| 2 | Parser monta o `InboundEvent` | [gowa/inbound.py:734](../gowa/inbound.py#L734) `ts=data.get("timestamp") or 0.0` | **str** — sem coerção |
| 3 | Dataclass aceita calada | [channels/events.py:29](../channels/events.py#L29) `ts: float = 0.0` | **str** (anotação não é enforcement) |
| 4 | `parsed_msg` para o filtro | [message_ingest_service.py:480](../app/services/message_ingest_service.py#L480) `"ts": event.ts or time.time()` | **str** (truthy ⇒ passa) |
| 5 | `filter.message.before_save` | plugins veem o dict | **str** |
| 6 | Conversa é criada | evento `conversation.created` | — (é o card que sobra na tela) |
| 7 | Item entra na fila do batch | [message_ingest_service.py:592](../app/services/message_ingest_service.py#L592) `"ts": event.ts` | **str** |
| 8 | Batch é consumido (pop) | [messaging_service.py:1813-1816](../app/services/messaging_service.py#L1813-L1816) | itens saem da memória |
| 9 | `text_ts_last` recebe o item | [messaging_service.py:1399-1400](../app/services/messaging_service.py#L1399-L1400) | **str** (truthy ⇒ passa) |
| 10 | Save do batch de texto (M4) | [messaging_service.py:1448](../app/services/messaging_service.py#L1448) `ts=(text_ts_last or None)` | **str** |
| 11 | Guard do repo | [message_repo.py:40](../db/repositories/message_repo.py#L40) `ts = ts or time.time()` | **str não-vazia é truthy ⇒ PASSA** |
| 12 | **INSERT** | [message_repo.py:41-58](../db/repositories/message_repo.py#L41-L58) → `messages.ts` `Float` ([db/tables.py:122](../db/tables.py#L122)) | 💥 `psycopg.errors.InvalidTextRepresentation` |
| 13 | Exceção engolida | [messaging_service.py:1768-1769](../app/services/messaging_service.py#L1768-L1769) `except Exception as exc: await aend_execution(exec_id, error=str(exc))` | mensagem **perdida** (a fila já foi consumida no passo 8) |

⚠️ **O passo 11 é a armadilha central.** `ts = ts or time.time()` parece um guard de tipo e não é: qualquer string não-vazia é truthy e atravessa.

⚠️ **O passo 13 é o que transformou um bug em incidente de 6 dias.** Não há bolha de erro, não há retry, não há log de nível operador — só uma linha em `executions.error`. Do lado do atendente, o cliente "abriu uma conversa vazia".

### 2.2 Os quatro pontos de save do plano 129 — todos com o mesmo defeito

| Marca | Caminho | Arquivo:linha | Exercitado hoje? |
|---|---|---|---|
| **M4** | batch de texto | [messaging_service.py:1448](../app/services/messaging_service.py#L1448) | ✅ **é o que quebrou em produção** |
| **M5** | batch de mídia | [messaging_service.py:1581](../app/services/messaging_service.py#L1581) | quebra igual assim que chegar mídia 1:1 |
| **M6** | grupo sem @menção | [message_ingest_service.py:556](../app/services/message_ingest_service.py#L556) | **dormente** — dispara no dia em que alguém marcar `group` no `JidTypePicker` |
| **M7** | echo (`is_from_me`) | [message_ingest_service.py:306](../app/services/message_ingest_service.py#L306) | quebra ao mandar 1:1 pelo celular |

### 2.3 Os cinco call sites do parser GOWA

Todos com a mesma linha literal, `ts=data.get("timestamp") or 0.0`:

| Evento | Arquivo:linha | Destino do `ts` |
|---|---|---|
| `message.reaction` | [gowa/inbound.py:502](../gowa/inbound.py#L502) | broadcast (não persiste em coluna `Float`) |
| `message.edited` | [gowa/inbound.py:515](../gowa/inbound.py#L515) | broadcast (`mark_edited` usa epoch próprio, [message_repo.py:602](../db/repositories/message_repo.py#L602)) |
| `message.revoked` | [gowa/inbound.py:526](../gowa/inbound.py#L526) | broadcast |
| `message.deleted` | [gowa/inbound.py:537](../gowa/inbound.py#L537) | broadcast |
| **`message`** | **[gowa/inbound.py:734](../gowa/inbound.py#L734)** | **persiste** — é o crash |

Só o quinto derruba INSERT hoje, mas os quatro primeiros propagam string para o WebSocket e para o bus de plugins. Corrigir os cinco custa a mesma linha.

### 2.4 O que os OUTROS providers fazem (e é por isso que só o GOWA quebra)

| Provider | Onde | Como |
|---|---|---|
| whatsapp_cloud | [channels.py:1416](../storages/plugins/whatsapp_cloud/channels.py#L1416) | `ts = _to_float(msg.get("timestamp"))` |
| telegram | [channels.py:501](../storages/plugins/telegram/channels.py#L501) | `ts=_to_float(msg.get("date"))` |
| instagram / messenger | [meta_graph.py:527](../storages/plugins/instagram/meta_graph.py#L527) | `_to_float(item.get("timestamp")) / 1000.0` (Meta manda ms) |
| website | [channels.py:239](../storages/plugins/website/channels.py#L239) | `_to_float(raw.get("ts")) or time.time()` |
| **gowa (core)** | **[gowa/inbound.py:734](../gowa/inbound.py#L734)** | **nada** |

⚠️ A disciplina existe e está documentada por repetição em quatro plugins. O único provider **sem** plugin — porque mora no core — é o único **sem** o guard.

### 2.4.1 ⚠️ O código do GOWA NÃO está na pasta do plugin (procure no core)

`storages/plugins/gowa/channels.py` tem **17 linhas** e é só uma casca de re-export ([:12-16](../storages/plugins/gowa/channels.py#L12-L16)); o próprio docstring do arquivo diz que "the implementation physically stays in the core tree for now (`channels/providers/gowa_channel.py`); the file move into this folder is a deferred follow-up (plano 13 §2.1)". A implementação real são **1.206 linhas no core**: [channels/providers/gowa_channel.py](../channels/providers/gowa_channel.py) (470) + [gowa/inbound.py](../gowa/inbound.py) (736).

| | whatsapp_cloud · telegram · instagram · messenger · website | **gowa** |
|---|---|---|
| Onde mora o `parse_inbound` | na pasta do plugin, autocontido | **no core** (`gowa/inbound.py`) |
| Como o conserto chega ao cliente | rebuild do `.zip` + `Importar (.zip)` | **deploy normal** — sem zip, sem re-importação |
| Ganhou o `_to_float()` | sim, quatro vezes, independentemente | **não** |

**Esta é a explicação do bug, não uma curiosidade.** A convenção de coagir o `ts` se formou por **repetição entre plugins autônomos** — nunca foi escrita em lugar nenhum. O GOWA, por não ser um plugin de verdade, ficou fora dessa formação. É também por isso que o plano não para na F1: as camadas F2/F3 existem para que a convenção deixe de depender de cada autor lembrar dela (D2).

⚠️ **Não procure o defeito em `storages/plugins/gowa/`** — não há uma linha a mudar lá, e nenhuma fase deste plano toca a pasta.

### 2.5 Por que o teste do plano 129 ficou verde

[tests/integration/test_inbound_provider_ts_ordering.py:86-88](../tests/integration/test_inbound_provider_ts_ordering.py#L86-L88) injeta `payload["timestamp"] = ts` recebendo **float** dos chamadores ([:103](../tests/integration/test_inbound_provider_ts_ordering.py#L103), [:154](../tests/integration/test_inbound_provider_ts_ordering.py#L154), [:185](../tests/integration/test_inbound_provider_ts_ordering.py#L185)). O GOWA real nunca manda float. O teste cobre o caminho certo com o **payload errado** — e por isso passou por cima da regressão.

---

## 3 — Evidência de produção (somente leitura, 2026-08-24/25)

### 3.1 A quebra tem data

Mensagens `role='user'` por dia, nas três inboxes GOWA:

| Data | Inbound | Outbound |
|---|---|---|
| 08-11 | 45 | 71 |
| 08-14 | 22 | 29 |
| 08-15 | 8 | 4 |
| **08-17** | **22** | **41** |
| 08-18 → 08-24 | **0** (todos os dias) | 3 · 1 |

Última mensagem recebida em qualquer canal GOWA: **2026-08-17 17:15 BRT**. O outbound continuou funcionando o tempo todo — assinatura exata de uma falha só no INSERT do inbound. O commit do plano 129 é de **2026-08-18**.

### 3.2 O caso confirmado

| Item | Valor |
|---|---|
| Conversa | `16156` (`display_id` 16138 — o "#16138" do card **é** esta conversa, não outra) |
| Inbox / canal | 17 · provider `gowa` |
| Linhas em `messages` | **1**, e é o card `conversation_event` |
| `executions` | id **12215**, `status='failed'` — o texto perdido do cliente está no `input_text` e no `error` desta linha |
| Erro | `invalid input syntax for type double precision: "2026-08-24T17:43:58Z"` |
| Rastro no bus | `filter.message.before_save` → `conversation.created` → `message.received` → `execution.started` → `execution.ended(error)` — **sem** `message.persisted` nem `message.saved` |

Comparativo, no mesmo minuto, de um canal `whatsapp_cloud` saudável: `before_save` → `received` → `execution.started` → **`message.persisted` → `message.saved`** → `execution.ended`.

### 3.3 O formato do GOWA é estável (não é payload torto)

1.447 webhooks GOWA capturados hoje, agrupados por forma do `timestamp`: **uma única forma**, `NNNN-NN-NNTNN:NN:NNZ` (RFC 3339 UTC, sem subsegundo), em 100 % dos casos, nos quatro tipos de evento (`message`, `message.ack`, `chat_presence`, `group.participants`). Não há variante numérica a acomodar — mas o helper aceita as duas mesmo assim (D1).

### 3.4 Por que passou 6 dias despercebido

Os três canais GOWA têm `allowed_jid_types = ["person","person_lid"]` — **sem `group`**, que é o default de criação desde o plano 103. Quase todo o tráfego deles é grupo, descartado no portão de JID **antes** do ponto de crash. Recorte de hoje, nos 105 webhooks `event: "message"`:

| Canal | de mim? | grupo? | n | Desfecho |
|---|---|---|---|---|
| A | não | sim | 51 | descartado no portão |
| B | não | sim | 49 | descartado no portão |
| A | sim | sim | 4 | descartado no portão |
| **B** | **não** | **não** | **1** | **💥 chegou ao INSERT e quebrou** |

⚠️ **O portão de JID não é o bug — é o anestésico.** Marcar `group` num canal GOWA hoje transformaria o incidente de 1 mensagem/semana em centenas/dia, pelo caminho **M6**.

### 3.5 Perdas sem rastro nenhum

A conversa `16156` deixou rastro só porque **nasceu** com a mensagem. Uma mensagem de contato com conversa **já aberta** não cria conversa, não escreve card e — depois que `executions` for podada — não deixa absolutamente nada. Quatro conversas GOWA estiveram abertas dentro da janela 18–24/08. O tamanho real da perda é **não observável** hoje; a `executions` só retém a partir de 2026-08-23 e o `debug_bus` retém ~3,5 h (é exatamente o que o **plano 140** existe para resolver).

---

## 4 — Inventário das mudanças

| # | Item | Arquivo:linha | O que falta | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|---|
| I1 | Helper de coerção de timestamp | novo, em [gowa/inbound.py](../gowa/inbound.py) | não existe | `_epoch(value) -> float`: número → `float`; string numérica → `float`; string RFC 3339 → `datetime.fromisoformat(...).timestamp()`; qualquer outra coisa → `0.0`. **Nunca levanta.** | baixo | S |
| I2 | Aplicar nos 5 call sites | [gowa/inbound.py:502](../gowa/inbound.py#L502) · [:515](../gowa/inbound.py#L515) · [:526](../gowa/inbound.py#L526) · [:537](../gowa/inbound.py#L537) · [:734](../gowa/inbound.py#L734) | valor cru | `ts=_epoch(data.get("timestamp"))` | baixo | S |
| I3 | Coerção no contrato | [channels/events.py:29](../channels/events.py#L29) | dataclass não valida | `__post_init__` que força `ts` a float (fallback `0.0`, sem levantar) | baixo | S |
| I4 | Guard do repositório | [message_repo.py:40](../db/repositories/message_repo.py#L40) | `ts = ts or time.time()` deixa str passar | tenta `float(ts)`; falhou ⇒ `logger.warning` + `time.time()`. **Nunca propaga** | baixo | S |
| I5 | Falha de inbound deixa de ser silenciosa | [messaging_service.py:1768-1769](../app/services/messaging_service.py#L1768-L1769) | só grava `executions.error` | `logger.exception` + bolha de erro via `error_bubble` ([messaging_service.py:60-70](../app/services/messaging_service.py#L60-L70)) | **médio** (caminho quente) | M |
| I6 | Fixture do teste 129 vira payload REAL | [test_inbound_provider_ts_ordering.py:86-88](../tests/integration/test_inbound_provider_ts_ordering.py#L86-L88) | injeta float | passar a formatar RFC 3339 como o GOWA manda; manter um caso numérico ao lado | baixo | M |
| I7 | Regressão dos outros 3 saves | M5/M6/M7 (§2.2) | nenhum teste com payload GOWA real | um teste por caminho | baixo | M |
| I8 | Coerência `parsed_msg["ts"]` ⇄ item da fila | [message_ingest_service.py:480](../app/services/message_ingest_service.py#L480) vs [:592](../app/services/message_ingest_service.py#L592) | 480 tem fallback `or time.time()`, 592 não; e o 592 lê `event.ts`, **ignorando** o que o filtro escreveu | ver **P2** — não é o bug, é dívida adjacente | baixo | S |
| I9 | Recuperação em produção | conversa `16156` · execution `12215` | mensagem perdida ainda não está no fio | ver **P1** (exige aprovação explícita) | **médio** | S |
| I10 | Documentação | [docs/CANAIS.md](../docs/CANAIS.md) + ≤2 linhas no [CLAUDE.md](../CLAUDE.md) | nada registrado | a regra + o ⚠️ no CLAUDE.md; o caso completo no guia | baixo | S |

### 4.1 Falsos positivos descartados

| Suspeita | Por que NÃO é |
|---|---|
| "A conversa `#16138` do card é outra conversa" | É o `display_id` da própria `16156`. Pista falsa que custa tempo — registrada aqui de propósito. |
| "É bug/regressão do GOWA v8.11.0" | O GOWA sempre mandou RFC 3339: 1.447/1.447 capturas hoje na mesma forma (§3.3). O que mudou foi o **core passar a usar** o campo. |
| "Os outros providers têm o mesmo defeito" | Todos coagem (§2.4). Nenhum plugin é tocado por este plano. |
| "É o filtro de tipo de JID que está derrubando as mensagens" | Ele derruba **grupos**, por configuração intencional dos três canais. Ele **mascara** o bug (§3.4), não o causa. |
| "`ts = ts or time.time()` já protegia" | String não-vazia é truthy — [message_repo.py:40](../db/repositories/message_repo.py#L40) atravessa. É I4. |
| "Vai precisar de migration" | `messages.ts` já é `Float` ([db/tables.py:122](../db/tables.py#L122)) e o repo já aceita `ts=` (D4). |
| "Os goldens de execução vão quebrar" | [tests/golden.py:83](../tests/golden.py#L83) normaliza a chave `ts` para `<TS>` **independentemente do tipo**. Os 4 goldens de `webhook_received` seguem determinísticos. |
| "`__post_init__` move a superfície da API de plugins" | [test_plugin_api_surface.py:190](../tests/contracts/test_plugin_api_surface.py#L190) pula atributos com `_`; dunder não entra no golden. Confirmar rodando o guard (F5). |
| "`fromisoformat` não parseia `Z`" | Parseia a partir do Python 3.11; o Dockerfile é `python:3.11-slim` ([Dockerfile:1](../Dockerfile#L1)). Verificado: devolve `1787593438.0`, tz-aware em UTC. |

---

## 5 — Cuidados de desenho

### 5.1 Fuso: o erro que este plano NÃO pode cometer

`"2026-08-24T17:43:58Z"` é UTC. `datetime.fromisoformat` no 3.11+ devolve um objeto **tz-aware**, e `.timestamp()` sobre ele está correto. O perigo é o caminho vizinho: parsear uma string **sem** o `Z` (ou remover o `Z` "para simplificar") produz um `datetime` **naive**, e `.timestamp()` de um naive assume **hora local** — em BRT isso desloca o carimbo em 3 h. É a mesma armadilha que já mordeu na migração dos agendamentos de retorno. O helper deve tratar naive explicitamente como UTC, nunca deixar o Python adivinhar.

### 5.2 O fallback é `time.time()`, nunca `0.0`

O plano 129 já sabia disso e o texto do commit diz por quê: gravar `0.0` põe a mensagem em **1970** e ela afunda para sempre no topo do fio. A cadeia atual funciona (`0.0` → `or None` → `time.time()`) e não pode ser quebrada por engano ao mexer no I4: o guard novo devolve `time.time()`, não `0.0`, e não pode transformar `None` em `0.0` no meio do caminho.

### 5.3 O item da fila é consumido antes de persistir

[messaging_service.py:1816](../app/services/messaging_service.py#L1816) faz `state.pending_messages.pop(key, None)` **antes** do save. Isso é intencional (plano 33 F6 — evitar que um novo webhook cancele o ciclo na janela pop→persist), mas significa que **uma exceção depois do pop destrói o item**. O I5 não deve tentar consertar isso re-enfileirando às cegas: um retry cego duplica a mensagem quando a falha for parcial. Ver **P3**.

### 5.4 Mudança de tipo visível a plugins

Depois do I1/I2, `parsed_msg["ts"]` em `filter.message.before_save` passa de **str** para **float** nos canais GOWA. Consumidor conhecido: [vendas_ia/filters.py:79](../storages/plugins/vendas_ia/filters.py#L79) `ts=value.get("ts")`. Hoje ele receberia uma string do GOWA (caminho de referral CTWA, que na prática só é exercitado pelo `whatsapp_cloud`) — a mudança é **correção**, não regressão. Registrar no changelog da API mesmo sem bump (D5).

---

## 6 — Fases / Roadmap

```
WAVE 0   F0 (caracterização: payload GOWA REAL — VERMELHO hoje)        ← SOZINHA (bloqueia tudo)
             │
WAVE 1   F1 (parser: ISO→epoch) · F2 (repo: guard) · F3 (contrato)     ← paralelo
             │   (barreira: F1+F2+F3 têm de deixar a F0 verde)
WAVE 2   F4 (falha de inbound deixa de ser silenciosa) · F5 (regressão M5/M6/M7 + guards)   ← paralelo
             │
WAVE 3   F6 (deploy + recuperação em produção)  →  F7 (documentação)   ← sequenciais
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | Caracterização com payload GOWA real `[bloqueia: F1–F5]` | 🔴 | baixo | Existe um teste que reproduz o `InvalidTextRepresentation` e está **vermelho** |
| 1 | **F1** | `gowa/inbound.py`: helper + 5 call sites `[dep: F0]` | 🟢 | baixo | F0 verde; o `ts` gravado é o epoch real do provedor |
| 1 | **F2** | `message_repo.add`: guard de tipo `[dep: F0]` | 🟢 | baixo | `ts` lixo grava `time.time()` + warning, e a linha entra |
| 1 | **F3** | `InboundEvent.__post_init__` `[dep: F0]` | 🟢 | baixo | Provider que devolva str/None não vaza para fora do parse |
| 2 | **F4** | Falha de save do inbound vira bolha + log `[dep: F1–F3]` | 🟢 | **médio** | Exceção simulada no save produz card de erro no painel e `ERROR` no log |
| 2 | **F5** | Regressão M5/M6/M7 + guards de contrato/goldens `[dep: F1–F3]` | 🟢 | baixo | Os 4 caminhos do plano 129 cobertos com payload real; suíte verde |
| 3 | **F6** | Deploy + recuperação em produção `[dep: F1–F5]` `[requer P1]` | 🔴 | **médio** | Um inbound 1:1 real de GOWA aparece no painel; a perda conhecida é tratada |
| 3 | **F7** | Documentação `[dep: F6]` | 🔴 | baixo | Guia atualizado; CLAUDE.md dentro do orçamento e `test_docs_hygiene` verde |

---

### Fase F0 — Caracterização: reproduzir com o payload que o GOWA manda de verdade

**Objetivo:** provar o bug num teste antes de tocar em qualquer linha de produção.

**Itens:**
- `[sequencial]` Acrescentar a `tests/integration/test_inbound_provider_ts_ordering.py` um caso que POSTa o webhook GOWA com `payload["timestamp"] = "2026-08-24T17:43:58Z"` — a **forma verificada em produção** (§3.3), não um float.
- `[sequencial]` Asserção dupla: (a) a mensagem **é salva**; (b) o `ts` gravado corresponde ao instante do provedor, não a `time.time()`.
- `[paralelo]` Um caso irmão pelo caminho **M7** (echo, `is_from_me: true`) com o mesmo payload — hoje quebra igual e não tem cobertura nenhuma.
- `[sequencial]` Documentar no docstring do arquivo **por que** a fixture antiga mentia (§2.5) — é o que impede a regressão de voltar pela mesma porta.

**Pronto quando:** `venv/bin/python -m pytest tests/integration/test_inbound_provider_ts_ordering.py` falha com `InvalidTextRepresentation` (ou com a mensagem ausente), e a falha é **exatamente** a assinatura de produção da §3.2. Um teste que fica verde aqui não reproduziu o bug — refazer.

#### Status de execução — Fase F0
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase F1 — Camada 1: o parser do GOWA entende RFC 3339

**Objetivo:** devolver ao plano 129 o que ele queria — o carimbo **real** do provedor — no canal onde ele mais importa.

**Itens:**
- `[sequencial]` Escrever `_epoch(value) -> float` em [gowa/inbound.py](../gowa/inbound.py), perto dos outros helpers privados do módulo. Ordem de tentativa: número → string numérica → RFC 3339 (tratando naive como **UTC**, §5.1) → `0.0`. **Nunca levanta** — é caminho quente de webhook.
- `[paralelo]` Trocar os cinco call sites do §2.3 por `ts=_epoch(data.get("timestamp"))`. Os quatro de broadcast entram junto: mesmo custo, e param de vazar string para o WS e para o bus.
- `[sequencial]` Testes de unidade do helper (puro, sem DB): epoch int/float, string numérica, RFC 3339 com `Z`, ISO sem timezone, `None`, `""`, lixo. Assertar que **nenhuma entrada levanta** e que RFC 3339 com `Z` bate com o epoch esperado.

**Pronto quando:** F0 verde; o `ts` persistido de um inbound GOWA é o instante do provedor (diferença mensurável de `time.time()` quando o webhook chega atrasado); nenhuma entrada do helper levanta.

#### Status de execução — Fase F1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(preencher ao executar)_
- **Problemas / pendências:** _(preencher ao executar)_
- **Verificação:** _(preencher ao executar)_

---

### Fase F2 — Camada 3: carimbo ruim nunca mais derruba a mensagem

**Objetivo:** garantir que a **classe** do incidente acabe, mesmo que um provider futuro volte a mandar lixo.

**Itens:**
- `[sequencial]` Em [message_repo.py:40](../db/repositories/message_repo.py#L40), trocar `ts = ts or time.time()` por uma coerção real: `float(ts)` protegido; falhou ⇒ `logger.warning` com o valor recebido (truncado) **e** `time.time()`. Preservar `None`/`0` → `time.time()` (§5.2).
- `[paralelo]` Teste: `message_repo.add(..., ts="2026-08-24T17:43:58Z")` **salva a linha** com um `ts` plausível e emite warning; `ts=None`, `ts=0`, `ts=""` e `ts=<float>` mantêm o comportamento atual byte a byte.

**Pronto quando:** nenhum valor de `ts` — de qualquer tipo — consegue impedir o INSERT de uma mensagem.

#### Status de execução — Fase F2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(preencher ao executar)_
- **Problemas / pendências:** _(preencher ao executar)_
- **Verificação:** _(preencher ao executar)_

---

### Fase F3 — Camada 2: o contrato para de aceitar mentira

**Objetivo:** fazer `ts: float` valer para **todo** provider, inclusive plugin de terceiro que o core não revisa.

**Itens:**
- `[sequencial]` `__post_init__` em [channels/events.py:15](../channels/events.py#L15) que coage `ts` para float; ininterpretável ⇒ `0.0` (que a cadeia já converte em `time.time()`, §5.2). Não levanta — um provider mal-comportado não pode derrubar o webhook inteiro.
- `[paralelo]` Comentário curto no campo [:29](../channels/events.py#L29) dizendo que a anotação agora é **enforced** e por quê — o próximo autor de provider precisa ler isso ali, não neste plano.
- `[sequencial]` Rodar `venv/bin/python -m pytest tests/contracts/test_plugin_api_surface.py` e confirmar que o golden **não se move** (§4.1). Se mover, parar e reavaliar D5.

**Pronto quando:** um provider fictício que devolva `ts="qualquer coisa"` produz `InboundEvent.ts` float, e o guard de superfície segue verde sem regeneração.

#### Status de execução — Fase F3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(preencher ao executar)_
- **Problemas / pendências:** _(preencher ao executar)_
- **Verificação:** _(preencher ao executar)_

---

### Fase F4 — A falha de save do inbound deixa de ser silenciosa

**Objetivo:** que o próximo defeito neste trecho dure minutos, não 6 dias.

**Itens:**
- `[sequencial]` Em [messaging_service.py:1768-1769](../app/services/messaging_service.py#L1768-L1769), acrescentar `logger.exception` ao `except Exception as exc` — hoje o erro só existe dentro de `executions.error`, invisível no log.
- `[sequencial]` Emitir a bolha de erro para o atendente reusando `error_bubble` ([messaging_service.py:60-70](../app/services/messaging_service.py#L60-L70)) — o helper já existe e hoje só serve a falha de **envio**. Texto na linha de "não foi possível registrar a mensagem recebida"; o objetivo é que **alguém veja**.
- `[sequencial]` A emissão da bolha vai em `try/except` próprio: falha ao avisar **nunca** pode mascarar o erro original nem derrubar o ciclo.
- `[paralelo]` Teste: com o save forçado a levantar, o ciclo produz (a) `executions.error` como hoje, (b) log `ERROR`, (c) broadcast `new_message` com `role: "error"`.
- `[sequencial]` **Não** re-enfileirar o item nesta fase (§5.3) — retry é a P3, decisão separada.

**Pronto quando:** uma exceção simulada no save do inbound aparece no log em `ERROR` e vira card de erro no painel, sem alterar o comportamento do caminho feliz.

#### Status de execução — Fase F4
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(preencher ao executar)_
- **Problemas / pendências:** _(preencher ao executar)_
- **Verificação:** _(preencher ao executar)_

---

### Fase F5 — Regressão dos quatro caminhos + guards

**Objetivo:** cobrir com payload real os quatro saves do plano 129, não só o que quebrou.

**Itens:**
- `[sequencial]` Reescrever a fixture `_post_gowa` ([test_inbound_provider_ts_ordering.py:74-91](../tests/integration/test_inbound_provider_ts_ordering.py#L74-L91)) para emitir RFC 3339 por padrão, mantendo um caso numérico ao lado (o helper aceita os dois, e o teste tem de provar isso).
- `[paralelo]` **M5** — batch de mídia ([messaging_service.py:1581](../app/services/messaging_service.py#L1581)) com payload GOWA de imagem.
- `[paralelo]` **M6** — grupo sem @menção ([message_ingest_service.py:556](../app/services/message_ingest_service.py#L556)) com `group` habilitado no canal de teste. É o caminho **dormente** de maior potencial de estrago (§3.4).
- `[paralelo]` **M7** — echo `is_from_me` ([message_ingest_service.py:306](../app/services/message_ingest_service.py#L306)).
- `[sequencial]` Rodar os 4 goldens de `webhook_received` e confirmar que seguem determinísticos (§4.1) — se algum se mover, é sinal de que a normalização não cobriu algo, e isso precisa ser entendido, não regenerado às cegas.
- `[sequencial]` Suíte inteira do core no Postgres de teste, **sem** outra suíte concorrente.

**Pronto quando:** os quatro caminhos verdes com payload real; goldens intactos; `venv/bin/python -m pytest` sem regressão nova além das falhas pré-existentes conhecidas.

#### Status de execução — Fase F5
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(preencher ao executar)_
- **Problemas / pendências:** _(preencher ao executar)_
- **Verificação:** _(preencher ao executar)_

---

### Fase F6 — Deploy e recuperação do que se perdeu

**Objetivo:** parar a hemorragia em produção e tratar a perda conhecida.

**Itens:**
- `[sequencial]` Deploy. **A hemorragia só para aqui** — cada dia adicional é mais inbound 1:1 perdido.
- `[sequencial]` Validação em produção: mandar uma mensagem 1:1 real para o número de um canal GOWA e confirmar que ela **aparece no painel** com o horário correto. Confirmar em `messages` que existe linha `role='user'` nova numa inbox GOWA — a primeira desde 2026-08-17.
- `[paralelo]` Varredura: procurar em `executions` outras linhas com `InvalidTextRepresentation` (a tabela só retém a partir de 2026-08-23; o que for anterior está perdido, §3.5) e conversas de inbox GOWA criadas entre 18 e 24/08 sem mensagem real.
- `[sequencial]` **Requer P1** — recuperar a mensagem da conversa `16156`. O texto está no `input_text`/`error` da execution `12215`. Escrita em produção **não acontece sem aprovação explícita do operador**.
- `[sequencial]` Avisar o atendimento sobre a janela 18–24/08: contatos que escreveram por canal GOWA e "não foram respondidos" não foram ignorados — a mensagem nunca chegou ao painel.

**Pronto quando:** inbound 1:1 de GOWA volta a aparecer no painel; a perda conhecida está tratada ou explicitamente descartada por decisão do operador.

#### Status de execução — Fase F6
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(preencher ao executar)_
- **Problemas / pendências:** _(preencher ao executar)_
- **Verificação:** _(preencher ao executar)_

---

### Fase F7 — Documentação

**Objetivo:** deixar registrado onde o próximo autor de provider vai olhar.

**Itens:**
- `[sequencial]` [docs/CANAIS.md](../docs/CANAIS.md): o caso completo — o formato RFC 3339 do GOWA, a regra "todo provider coage o `ts`", as três camadas, e **por que o portão de JID mascarou o incidente por 6 dias** (é a parte que mais vale registrar; a próxima falha vai se esconder pelo mesmo mecanismo).
- `[sequencial]` [CLAUDE.md](../CLAUDE.md): **no máximo 2 linhas** na seção de Canais — a regra e o ⚠️, com link para o guia. Nada de história aqui (plano 139).
- `[paralelo]` [docs/PLUGIN_API_CHANGELOG.md](../docs/PLUGIN_API_CHANGELOG.md): nota **sem bump** (D5) registrando que `InboundEvent.ts` passou a ser coagido e que `filter.message.before_save` agora recebe float também no GOWA (§5.4).
- `[sequencial]` `venv/bin/python -m pytest tests/contracts/test_docs_hygiene.py`.

**Pronto quando:** guia atualizado, CLAUDE.md dentro do teto, `test_docs_hygiene` verde.

#### Status de execução — Fase F7
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(preencher ao executar)_
- **Problemas / pendências:** _(preencher ao executar)_
- **Verificação:** _(preencher ao executar)_

---

## 7 — Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Parse de RFC 3339 | Tratar string naive como hora local desloca o carimbo em 3 h (BRT) | Naive é assumido **UTC** explicitamente; teste com o valor de produção e o epoch esperado (§5.1) |
| Fallback | Trocar `time.time()` por `0.0` põe mensagens em 1970 | Testes de F1/F2 assertam o fallback; é exatamente o que o plano 129 já tinha acertado (§5.2) |
| Helper no caminho quente | Uma exceção no parse derrubaria o webhook inteiro — pior que o bug atual | `_epoch` e `__post_init__` **nunca levantam**; teste com entradas-lixo |
| F4 no ciclo do batch | Mexer no `except` do orquestrador pode alterar o caminho feliz | Só acrescenta log + broadcast, cada um em `try/except` próprio; nenhum `return`/`raise` novo |
| Retry do item perdido | Re-enfileirar às cegas duplica mensagem em falha parcial | Fora de escopo nesta fase; é a **P3** |
| Tipo visível a plugins | `parsed_msg["ts"]` muda de str→float no GOWA | Único consumidor conhecido é [vendas_ia/filters.py:79](../storages/plugins/vendas_ia/filters.py#L79); a mudança é correção (§5.4); registrado no changelog |
| Superfície da API | `__post_init__` mover o golden forçaria bump | Dunder é pulado pelo extrator ([test_plugin_api_surface.py:190](../tests/contracts/test_plugin_api_surface.py#L190)); **confirmar rodando** na F3 antes de assumir |
| Goldens de execução | 4 goldens gravam o item da fila verbatim | `ts` é normalizado para `<TS>` ([tests/golden.py:83](../tests/golden.py#L83)); conferir na F5, nunca regenerar às cegas |
| Suíte no Postgres | Duas suítes concorrentes recriam o mesmo schema `public` | Rodar sozinho; o concorrente pode estar em **outra máquina** |
| Escrita em produção (I9) | INSERT manual pode duplicar ou cair na conversa errada | Só com aprovação (P1); conferir `msg_id` antes; `POST /api/admin/repair-sequences` se necessário |
| Canal GOWA com `group` marcado | Multiplicaria a perda pelo caminho M6 antes do deploy | Não mexer em `allowed_jid_types` até a F6 concluída |
| Janela de observação | `executions` só retém desde 23/08 e o `debug_bus` ~3,5 h | O tamanho real da perda 18–24/08 é **não observável** — declarar isso, não estimar (§3.5). Ver **P4** |

---

## 8 — Perguntas em aberto

**P1 — Recuperar a mensagem perdida da conversa `16156` em produção?**
⏸️ **AGUARDANDO DECISÃO DO OPERADOR.** O texto do cliente está preservado no `input_text`/`error` da execution `12215`, e o `msg_id` original também. Um `INSERT` em `messages` com o `ts` real (`2026-08-24T17:43:58Z` → epoch) reconstrói a conversa como se nada tivesse acontecido.
(a) **Recomendada** — recuperar, com aprovação explícita e conferindo o `msg_id` antes para não duplicar caso o cliente reenvie.
(b) Deixar como está e responder o cliente por fora — mais simples, mas a conversa fica permanentemente sem o que foi pedido, e o atendente não sabe do que se trata.

**P2 — Alinhar `parsed_msg["ts"]` com o item da fila (I8)?**
⏸️ **ADIADO** para depois da F6. [message_ingest_service.py:592](../app/services/message_ingest_service.py#L592) lê `event.ts` cru, ignorando o `ts` que um plugin tenha reescrito no `filter.message.before_save` — e `apply_message_filter` ([:63-70](../app/services/message_ingest_service.py#L63-L70)) nem re-extrai o campo.
(a) **Recomendada** — fazer o item da fila ler `parsed_msg["ts"]`: um único ponto de normalização e o filtro passa a valer. É ampliação de contrato (MINOR) e merece commit próprio.
(b) Deixar como está — a divergência é inofensiva hoje e não tem consumidor.
Não misturar com este plano: a correção do incidente não pode ficar esperando uma decisão de contrato.

**P3 — A falha de save deve tentar re-enfileirar a mensagem?**
⏸️ **ADIADO.** O item é consumido antes de persistir (§5.3), então uma falha o destrói.
(a) **Recomendada** — nesta rodada só **avisar** (F4). Retry cego duplica mensagem quando a falha for parcial (linha gravada, broadcast falhou), e duplicata no fio do cliente é pior que a bolha de erro.
(b) Retry com dead-letter em tabela própria — o desenho certo a prazo, mas é plano próprio, não corolário deste.

**P4 — Priorizar o plano 140 (retenção do `debug_bus` por tempo) por causa deste incidente?**
⏸️ **ADIADO.** Esta investigação só fechou porque o `debug_bus` ainda tinha as 3,5 h que continham o evento. Meia hora mais tarde, a captura teria rotacionado e a causa-raiz seria conjectura. É evidência direta a favor do 140 — registrar lá, decidir separadamente.

---

## 9 — Apêndice — arquivos-chave

**Core — provider**
- [gowa/inbound.py](../gowa/inbound.py) — helper novo + 5 call sites (`:502`, `:515`, `:526`, `:537`, `:734`)
- [channels/events.py](../channels/events.py) — `InboundEvent.__post_init__` (`:15`, `:29`)

**Core — persistência**
- [db/repositories/message_repo.py](../db/repositories/message_repo.py) — guard de tipo (`:40`)

**Core — pipeline de inbound**
- [app/services/messaging_service.py](../app/services/messaging_service.py) — `except` do ciclo (`:1768-1769`), `error_bubble` (`:60-70`); leitura: pop do batch (`:1816`), M4 (`:1448`), M5 (`:1581`)
- [app/services/message_ingest_service.py](../app/services/message_ingest_service.py) — leitura: `parsed_msg` (`:480`), item da fila (`:592`), M6 (`:556`), M7 (`:306`)

**Testes**
- [tests/integration/test_inbound_provider_ts_ordering.py](../tests/integration/test_inbound_provider_ts_ordering.py) — fixture que mentia (`:74-91`)
- [tests/contracts/test_plugin_api_surface.py](../tests/contracts/test_plugin_api_surface.py) — guard de superfície (`:190`)
- [tests/golden.py](../tests/golden.py) — normalização de `ts` (`:83`)
- `tests/goldens/exec_*.json` — 4 goldens com `webhook_received`

**Documentação**
- [docs/CANAIS.md](../docs/CANAIS.md) · [CLAUDE.md](../CLAUDE.md) (≤2 linhas) · [docs/PLUGIN_API_CHANGELOG.md](../docs/PLUGIN_API_CHANGELOG.md)

**Referência (não editar — é o padrão a imitar)**
- [storages/plugins/whatsapp_cloud/channels.py:1416](../storages/plugins/whatsapp_cloud/channels.py#L1416) · [telegram/channels.py:501](../storages/plugins/telegram/channels.py#L501) · [instagram/meta_graph.py:527](../storages/plugins/instagram/meta_graph.py#L527) · [website/channels.py:239](../storages/plugins/website/channels.py#L239)

---

## 10 — Checklist de verificação

- [ ] F0 reproduziu o bug **antes** de qualquer mudança de produção (vermelho com a assinatura de §3.2)
- [ ] O helper aceita epoch int/float, string numérica e RFC 3339 com `Z`, e **não levanta** para `None`/`""`/lixo
- [ ] RFC 3339 com `Z` produz o epoch exato (caso de produção conferido contra o valor esperado)
- [ ] String ISO **sem** timezone é tratada como UTC, não como hora local (sem deslocamento de 3 h)
- [ ] `ts` ausente/ininterpretável cai em `time.time()`, **nunca** em `0.0` (mensagem em 1970)
- [ ] Os 5 call sites de [gowa/inbound.py](../gowa/inbound.py) usam o helper — inclusive os 4 de broadcast
- [ ] `message_repo.add` salva a linha com `ts` de qualquer tipo, emitindo warning quando coage
- [ ] `ts=None`, `ts=0`, `ts=""` e `ts=<float>` mantêm o comportamento anterior byte a byte
- [ ] `InboundEvent.ts` é float depois do `__post_init__`, mesmo com provider mal-comportado
- [ ] `test_plugin_api_surface` verde **sem** regenerar golden e **sem** bump de `WHATSBOT_API_VERSION`
- [ ] Os 4 caminhos do plano 129 (M4, M5, M6, M7) cobertos com payload GOWA real
- [ ] M6 testado com `group` habilitado no canal de teste (caminho dormente de maior estrago)
- [ ] Os 4 goldens `exec_*.json` seguem determinísticos, sem regeneração
- [ ] Falha simulada no save do inbound produz log `ERROR` **e** card de erro no painel
- [ ] Falha ao emitir a bolha não mascara o erro original nem derruba o ciclo
- [ ] Caminho feliz do batch inalterado (sem `return`/`raise` novo no orquestrador)
- [ ] Suíte do core verde no Postgres de teste (`WHATSBOT_TEST_DB_URL`), **sem** outra suíte concorrente
- [ ] `test_docs_hygiene` verde; CLAUDE.md dentro do teto e com no máximo 2 linhas novas
- [ ] Em produção: inbound 1:1 real de GOWA aparece no painel com o horário correto
- [ ] Em produção: existe linha `role='user'` nova em inbox GOWA — a primeira desde 2026-08-17
- [ ] Varredura de `executions` e de conversas GOWA vazias da janela 18–24/08 concluída
- [ ] P1 decidida pelo operador **antes** de qualquer escrita em produção
- [ ] `allowed_jid_types` dos canais GOWA não foi alterado antes do deploy
- [ ] Atendimento avisado sobre a janela 18–24/08 (mensagens que nunca chegaram ao painel)
