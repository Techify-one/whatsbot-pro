# Plano 126 — Mídia que não pôde ser baixada avisa no chat, em vez de virar caixa cinza muda

> **Status:** EXECUTADO E PUBLICADO (F0–F6 + F8; F7 aguarda a P1) · **Data:** 2026-08-17 · **Escopo:** pequeno/médio (plugin `telegram` **1.4.0**, **zero mudança no core** — nenhum arquivo do core foi tocado)
> **Origem:** relato do usuário (2026-08-17) — cliente manda vídeo no Telegram e o painel mostra "Vídeo indisponível"; ninguém sabe por quê. **Método:** leitura do código + log real do `debug_bus` de produção (`logs_12_08.jsonl`, 5.001 linhas, 12:42→17:21 de 2026-08-17) + consultas ao banco de PRODUÇÃO. Toda evidência está medida e citada em §2.
> A causa está provada: a Bot API do Telegram **recusa** `getFile` para arquivo acima de 20 MB. O plugin engole o erro em silêncio, o core grava `media_path=NULL` e o painel desenha uma caixa cinza cujo tooltip **mente** ("o arquivo não está mais disponível no servidor" — aponta para um problema de volume que não existe). Este plano faz a falha se explicar: nota privada no fio, texto na bolha e log do erro real.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.
>
> **Validado em dev (2026-08-17):** áudio real de **125,6 MB** — a bolha explica o motivo na própria mensagem e a nota privada aparece no fio. Publicado no repo de plugins (commit `c872280`, `main` em `0da6978`).
>
> **O que falta:** (1) repetir a validação com **documento** grande (o teto de 20 MB não é só de vídeo/áudio — R10) e reenviar o mesmo arquivo para confirmar que não duplica a nota (R12); (2) exportar a cópia de PRODUÇÃO e comparar antes de importar o zip novo (resíduo da F0, um clique); (3) decidir a **P1** (o tooltip mentiroso do core, fase F7 — a única que tocaria em arquivo do core, e que não conserta nada, só para de apontar para o lugar errado).

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| **D1** ✅ 2026-08-17 | **Local Bot API Server está fora.** Investigado, funciona (`--local` → "Download files without a size limit"), e foi **recusado pelo usuário** | Nenhuma fase toca em `TELEGRAM_API_BASE` ([channels.py:49](../storages/plugins/telegram/channels.py#L49)). O seam continua lá, de graça, se um dia mudar de ideia |
| **D2** ✅ 2026-08-17 | **Rota MTProto (Telethon/Pyrogram) está fora** | Exige o mesmo `api_id`/`api_hash` do servidor local, ninguém confirma para *bot token*, e uma 2ª sessão MTProto do mesmo bot pode dividir updates com o webhook (mensagem de cliente sumindo em silêncio) |
| **D3** ✅ 2026-08-17 | **Contorno operacional oficial:** pedir ao cliente que reenvie comprimido **ou** que envie para uma **conta comum** do Telegram, onde o limite de 20 MB não existe | Esse texto entra **no corpo do aviso** — o atendente não precisa lembrar da regra. Ver F2 |
| **D4** ✅ 2026-08-17 | **Zero mudança obrigatória no core** | Tudo é alcançável pelos ganchos que já existem: `file_size` já chega no payload, e `message.saved` já carrega `media_extras` + `conversation_id`. O único item de core do plano é **opcional** (F7) e gateado por P1 |
| **D5** ✅ 2026-08-17 | O aviso é **nota privada** (`private_note`), não `system_notice` | `private_note` entra no contexto do LLM (não está na lista-negra de roles) — e isso é **desejado**: a IA passa a saber que o vídeo não chegou e pode pedir o reenvio, em vez de responder no vazio. Ver R3 |
| **D6** ✅ 2026-08-17 | **Não gatear o `getFile` pelo tamanho** | A chamada condenada custa 0,63 s e é grátis; gatear exigiria acertar a fronteira exata (20 MB × 20 MiB) e erraria calado. O tamanho serve para **explicar** a falha, nunca para causá-la. Ver R6 |

**Princípio fixo do repo aplicado aqui:** a cópia viva é `storages/plugins/telegram/`, mas a **fonte de desenvolvimento** é `../whatsbot-pro-plugins/plugins/telegram/src/`. Editar em `storages/` e esquecer a fonte é perder o trabalho no próximo import — e o inverso (editar a fonte e não instalar) entrega ao usuário uma versão que ninguém testou.

---

## 1. Resumo executivo

O Telegram não deixa **bot** baixar arquivo acima de **20 MB** (limite do servidor público, não do protocolo). Quando isso acontece, três coisas se somam e o resultado é uma bolha muda:

1. o plugin **descarta** a mensagem de erro do `getFile` — nenhum log, nenhuma pista;
2. o core grava a mensagem com `media_type='video'` e `media_path=NULL`, e o safety-net de texto **não** entra em ação porque `video` é um tipo "desenhável";
3. o painel tenta desenhar `<video src="/null">`, o `onError` dispara e cai no placeholder de **arquivo perdido**, cujo tooltip acusa o servidor.

A correção é pequena e cabe inteira no plugin, porque **nada precisa ser construído**: o `file_size` já vem no payload do Telegram e é jogado no lixo pelo `parse_inbound`; o evento `message.saved` já entrega `media_extras` + `conversation_id`. O plano acrescenta ao plugin um módulo puro de decisão, um `filters.py` (texto na bolha), um `events.py` (nota privada) e uma linha de log.

⚠️ **O escopo é MAIOR que vídeo.** O teto de 20 MB do `getFile` vale para **todo** tipo de arquivo, e no Telegram um documento pode ter até 2 GB. Um PDF de 30 MB falha exatamente igual, hoje, em silêncio. O plano trata todos os tipos de mídia em que o plugin tem `file_size` — não só `video`.

---

## 2. Como funciona hoje (mapa) — e a evidência medida

### 2.1 A cadeia da falha, ponto a ponto

| # | Passo | Onde | O que acontece |
|---|---|---|---|
| 1 | Telegram entrega o update com `video.file_size` | payload cru | `25.204.422` bytes no caso real |
| 2 | `parse_inbound` monta `media_extras` | [channels.py:415-419](../storages/plugins/telegram/channels.py#L415-L419) | grava `media_id`/`mime_type`/`duration_ms` e **descarta `file_size`** |
| 3 | Core pede o arquivo ao provider | [message_ingest_service.py:396](../app/services/message_ingest_service.py#L396) → [:203-216](../app/services/message_ingest_service.py#L203-L216) | só loga quando há **exceção**; `None` devolvido passa calado |
| 4 | `download_media` chama `getFile` | [channels.py:278-280](../storages/plugins/telegram/channels.py#L278-L280) | `if not meta.get("ok"): return None` — **`meta["error"]` é descartado**. É aqui que a pista morre |
| 5 | Filtro de conteúdo roda **depois** do download | [message_ingest_service.py:456](../app/services/message_ingest_service.py#L456) | no `filter.message.before_save` o `media_path` **já é `None`** — é o gancho certo para o texto da bolha |
| 6 | Save do item de mídia | [messaging_service.py:1209-1231](../app/services/messaging_service.py#L1209-L1231) | `_saved_text = text or …`; `_saved_caption` vem de `media_extras["caption"]`, **nunca do `text`** |
| 7 | Safety-net de bolha muda **não** age | [transcription.py:115-120](../server/transcription.py#L115-L120) | `video` ∈ `RENDERABLE_MEDIA_TYPES` ⇒ devolve o texto vazio intacto, de propósito |
| 8 | Painel desenha e falha | [MediaContent.js:16-39](../web/static/js/components/contacts/MediaContent.js#L16-L39) | `url = '/' + null` → `/null` → `onError` → "Vídeo indisponível" + tooltip **"não está mais disponível no servidor"** |

### 2.2 Evidência medida (produção, 2026-08-17)

| Fato | Medição |
|---|---|
| Mensagem do relato | `messages.id = 669503`, conversa **15646**, contato `8190184333`, canal `telegram_9bf7bdfc` — `media_type='video'`, `media_path=NULL` |
| Não é a 1ª vez | `messages.id = 666717` (13/08 23:32, `msg_id` 74847), **mesmo contato, mesma falha** — é a isso que o "Vc não assistiu o vídeo" do cliente se refere |
| Tamanho | `file_size: 25204422` (≈24,0 MiB) — **20% acima** do teto |
| Limite oficial | doc do Bot API, verbatim: *"For the moment, bots can download files of up to 20MB in size"* e *"The maximum file size to download is 20 MB"* |
| **Prova de que foi recusa, não timeout** | `filter.webhook.payload` (log id 717243) às 16:30:34.710 → `filter.message.before_save` (id 717244) às 16:30:35.345 **já com `media_path: null`**. A tentativa inteira durou **0,63 s**, com `HTTP_TIMEOUT = 30.0` ([channels.py:42](../storages/plugins/telegram/channels.py#L42)). Nenhum byte do vídeo foi buscado |
| Não é falha geral de mídia | Da era nativa (≥21/07): 326 imagens WhatsApp, 169 áudios, 45 imagens Telegram, 8 áudios Telegram, 5 documentos Telegram, 8 vídeos WhatsApp — **zero falhas**. Só **2 de 4 vídeos Telegram** falharam, os dois do mesmo contato |
| A thumbnail É baixável | mesmo payload: `thumbnail.file_size: 15583` (15 KB) — ver P2 |
| Divergência de linhagem | `plugins.version` em PRODUÇÃO = **1.2.2**; `storages/plugins/telegram/plugin.yaml:3` e `../whatsbot-pro-plugins/plugins/telegram/src/plugin.yaml:3` = **1.3.1** |

### 2.3 ⚠️ Duas armadilhas do frontend que invalidam o caminho óbvio

Quem for implementar o "texto na bolha" vai escrever `[Vídeo de 24 MB — acima do limite]`, recarregar e **não ver nada**. Motivo:

| # | Armadilha | Onde | Consequência |
|---|---|---|---|
| **A1** | O ramo de vídeo **suprime** qualquer legenda que comece com `[Vídeo` | [MediaContent.js:72-82](../web/static/js/components/contacts/MediaContent.js#L72-L82) — `caption && !caption.startsWith('[Vídeo')` | Texto começando com `[Vídeo` fica **invisível**. E `'[Vídeo]'` também está em `MEDIA_PLACEHOLDERS` ([messageView.js:220-222](../web/static/js/services/messageView.js#L220-L222)) |
| **A2** | `mediaCaptionOf` dá **precedência absoluta** a `media_caption` sobre o `content` | [messageView.js:248-251](../web/static/js/services/messageView.js#L248-L251) | Vídeo **com legenda do cliente** ⇒ o painel mostra a legenda e **nunca** o nosso texto. O plugin já grava `extras["caption"]` quando há legenda ([channels.py:448-452](../storages/plugins/telegram/channels.py#L448-L452)) |

**Efeito no desenho:** o **texto na bolha é best-effort** (funciona no caso sem legenda — que é exatamente o caso real medido), e a **nota privada é a superfície que sustenta o plano**, porque nenhuma dessas duas regras a alcança.

### 2.4 Falsos positivos descartados

| Suspeita | Por que NÃO é | Evidência |
|---|---|---|
| Volume/`statics` não persistente | O arquivo **nunca existiu**; `media_path` é `NULL` no banco, não um path apontando para arquivo ausente | §2.2 |
| Timeout / link lento | Falha em 0,63 s contra `HTTP_TIMEOUT = 30.0` | §2.2 |
| Regressão do core / do plano 123 | Produção **já** emite `conversation_id` no `message.saved` (log id 717248: `"conversation_id": 15646`) | log |
| Bug de fuso (painel 13:30 × log 16:30) | Máquina de análise em UTC, painel em `America/Sao_Paulo`. 16:30 UTC = 13:30 BRT | conferido |
| WhatsApp Cloud / Instagram / Messenger com o mesmo defeito | Não têm teto de 20 MB no download (Graph/CDN); e cliente WhatsApp não consegue nem enviar vídeo de 25 MB | §2.2 (8 vídeos WhatsApp, 0 falhas) |
| `filter.media.unknown` para tratar o caso | **Não existe mais** — retirado de `KNOWN_FILTERS` no plano 100; registrar hoje só gera WARNING | CLAUDE.md |

---

## 3. Inventário do que fazer

| # | Item | Onde | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|
| 1 | `file_size` sobrevive ao parse | [channels.py:405-435](../storages/plugins/telegram/channels.py#L405-L435) | acrescentar `file_size` ao `extras` de **todos** os ramos de mídia (video, video_note, audio, voice, document, sticker, photo) | baixo | S |
| 2 | Log do erro real do `getFile` | [channels.py:278-280](../storages/plugins/telegram/channels.py#L278-L280) | `logger.warning` com `meta["error"]` antes do `return None` | baixo | S |
| 3 | Módulo puro de decisão | **novo** `storages/plugins/telegram/media_failure.py` | dado `(media_type, media_path, media_extras)` → `None` ou `(motivo, texto_bolha, texto_nota)`. Sem I/O, sem DB, 100% testável | baixo | M |
| 4 | Texto na bolha | **novo** `filters.py` + `entry.filters` | `filter.message.before_save`: **acrescenta** ao `text` (nunca substitui — A2/R2) | médio | M |
| 5 | Nota privada no fio | **novo** `events.py` + `entry.events` | `message.saved` → `add_message("private_note", …, reopen=False)` + `broadcast("new_message")`, copiando [janela_72h/note.py](../storages/plugins/janela_72h/note.py) | médio | M |
| 6 | Testes | `../whatsbot-pro-plugins/plugins/telegram/tests/python/` | hoje só existe `test_inbound_reply_parsing.py` | baixo | M |
| 7 | *(opcional, P1)* tooltip honesto | [MediaContent.js:19-31](../web/static/js/components/contacts/MediaContent.js#L19-L31) | `!src` ⇒ rótulo/tooltip de "nunca recebido", distinto de "arquivo perdido" | baixo | S |

### 3.1 Ganchos e precedentes já verificados (não re-investigar)

| Precisa de | Existe? | Onde |
|---|---|---|
| Filtro poder trocar o `text` | ✅ | `apply_message_filter` re-extrai `text` — [message_ingest_service.py:45-72](../app/services/message_ingest_service.py#L45-L72) |
| `entry.events` / `entry.filters` válidos | ✅ | `_ENTRY_SPECS` — [plugins/loader.py:325-337](../plugins/loader.py#L325-L337) |
| Forma do `EVENT_HANDLERS` | ✅ | [protocolos/events.py:35-45](../storages/plugins/protocolos/events.py#L35-L45) |
| `add_message(..., reopen=)` | ✅ | [agent/memory.py:436-445](../agent/memory.py#L436-L445) |
| Nota privada escrita por plugin | ✅ | [janela_72h/note.py](../storages/plugins/janela_72h/note.py) — documenta as 4 armadilhas: canal da **conversa**, linha devolvida por `add_message`, best-effort, `reopen=False` |
| `conversation_id` no `message.saved` | ✅ **em produção** | log id 717248 |
| Dedupe de update repetido | ✅ | `state.processed_messages` — [message_ingest_service.py:380-389](../app/services/message_ingest_service.py#L380-L389) |
| Plugin `telegram` já tem `events.py`/`filters.py`? | ❌ | `entry:` só tem channels/lifecycle/routes/settings — [plugin.yaml:14-18](../storages/plugins/telegram/plugin.yaml#L14) |

---

## 4. Fases e paralelização

```
WAVE 0  F0 (reconciliar linhagem 1.2.2 × 1.3.1)                    🔴 SOZINHA
           │  (barreira: F0 define QUAL fonte as próximas editam)
WAVE 1  F1 (file_size + log) · F2 (módulo puro) · F5c (caracterização)   🟢 juntas
           │  (barreira: F4 e F6 consomem F1 + F2)
WAVE 2  F3 (nota privada) · F4 (texto na bolha)                     🟢 juntas
           │
WAVE 3  F6 (testes + versão + zip) · F7 (tooltip do core, opcional)  🟢 juntas
           │
WAVE 4  F8 (instalar no local, validar, publicar)                   🔴 SOZINHA
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | linhagem/ops | 🔴 | **alto** | o delta 1.2.2×1.3.1 está capturado ou descartado com razão escrita `[bloqueia: todas]` |
| 1 | **F1** | plugin/parse | 🟢 | baixo | `file_size` aparece no `media_extras` do log do `debug_bus`; o erro do `getFile` aparece no log |
| 1 | **F2** | plugin/puro | 🟢 | baixo | `pytest` do módulo puro verde, sem tocar em rede/DB |
| 1 | **F5c** | testes | 🟢 | baixo | teste de caracterização congela o comportamento ATUAL (bolha muda) e passa **antes** de F3/F4 |
| 2 | **F3** | plugin/events | 🟢 | médio | `[depende de: F1, F2]` nota privada aparece no fio, sem reabrir conversa resolvida |
| 2 | **F4** | plugin/filters | 🟢 | médio | `[depende de: F1, F2]` a bolha explica; teste cobre A1 e A2 |
| 3 | **F6** | testes/build | 🟢 | baixo | `test_plugins.py telegram` verde; versão bumpada; `--check` limpo |
| 3 | **F7** | core/frontend | 🟢 | baixo | *(opcional — só se P1 = sim)* tooltip deixa de acusar o servidor |
| 4 | **F8** | deploy | 🔴 | médio | rodando em `storages/`, validado à mão, e só então commit/publish |

---

### Fase F0 — Reconciliar a linhagem do plugin (1.2.2 em produção × 1.3.1 no repo) 🔴

**Objetivo:** garantir que a versão que vai ganhar o aviso é a que roda em produção, e não uma linhagem paralela que vai apagar código vivo.

**Itens** `[sequencial]`
1. Confirmar no banco de produção: `SELECT id, version FROM plugins WHERE id='telegram'` → medido hoje como **1.2.2**.
2. Comparar **conteúdo** (não número — precedente registrado no repo: duas cópias do `protocolos` já vieram ambas marcadas `1.17.0` com conteúdos diferentes) entre `../whatsbot-pro-plugins/plugins/telegram/src/` (1.3.1) e o que roda em produção.
3. Decidir e **escrever a razão**: (a) o 1.2.2 é ancestral do 1.3.1 ⇒ seguir na fonte 1.3.1; (b) o 1.2.2 tem delta próprio ⇒ portar o delta para a fonte **antes** de qualquer fase seguinte.
4. Só então abrir código.

⚠️ Publicar o zip novo sem esse passo **sobrescreve em silêncio** o delta que está atendendo clientes hoje.

**Pronto quando:** existe uma frase escrita neste plano dizendo qual das duas hipóteses é verdadeira e com que evidência.

#### Status de execução — Fase F0
**Estado:** ✅ Concluída (2026-08-17)
- **O que foi feito:** nenhum arquivo mudou — é uma fase de investigação. Reconstruída a linhagem da 1.2.2 por três fontes independentes: histórico do core, histórico do repo de plugins e a `audit_log` de produção.
- **Como foi feito / decisões:** **hipótese (a) — a 1.2.2 é ancestral da 1.3.1.** Evidência: (1) a 1.2.2 **não existe em git nenhum** — o espelho do core foi `1.2.1 → 1.3.0` (`afdb503` → `42a9aac`) e o repo de plugins só publicou 1.3.0 e 1.3.1; (2) `audit_log` de prod registra `plugin.update {"version":"1.2.1"} → {"version":"1.2.2"}` em **2026-07-27 13:41 BRT**, compatível com o fluxo habitual de exportar do dev e importar no prod; (3) **o delta 1.2.1 → 1.3.1 inteiro** é `routes.py` (+28 linhas do seam de auditoria, 3 call sites) e o `name:` do manifest — `channels.py`, `lifecycle.py`, `settings.py`, `mode.py` e `static/` são **byte-idênticos**, e `channels.py` não é tocado desde **2026-07-23**, quatro dias ANTES de a 1.2.2 entrar em produção. Ou seja: o arquivo que este plano edita é o mesmo código que roda em produção hoje.
- **Problemas / pendências:** os bytes da 1.2.2 só existem no container de produção e **não há credencial do painel no cofre** (`vault_discover`: 7 credenciais, nenhuma do WhatsBot), então a comparação byte a byte não é possível daqui. Isso rebaixa a F0 de "bloqueia todas as fases" para **"bloqueia só o import em produção"**: escrever o código é seguro (a superfície tocada não mudou desde a 1.2.1), mas antes de importar o zip novo o operador deve **Exportar** a cópia de prod (Gerenciar Plugins → Exportar) e comparar com `src/` — a exposição, se houver, está em `routes.py`/`plugin.yaml`. Descoberto de quebra: a cópia instalada divergia da fonte numa linha (`name: Telegram` × `Canal · Telegram`); a F8 alinhou as duas.
- **Verificação:** `git log`/`git show` nos dois repositórios; `diff` por arquivo entre `afdb503:assets/plugin_examples/telegram` e `src/`; `SELECT` em `plugins` e `audit_log` de produção pelo MCP do cofre.

---

### Fase F1 — O `file_size` sobrevive ao parse, e o `getFile` passa a falar 🟢

**Objetivo:** trazer para dentro do sistema os dois dados que já existem e são jogados fora. Nenhum comportamento visível muda nesta fase.

**Itens**
1. `[paralelo]` Em [channels.py:405-435](../storages/plugins/telegram/channels.py#L405-L435), acrescentar `"file_size": <obj>.get("file_size")` ao `extras` de **cada** ramo: `video`, `video_note`, `audio`, `voice`, `document`, `sticker`. Para `photo`, o objeto é o `PhotoSize` escolhido. O filtro `{k: v for k, v in extras.items() if v is not None}` em [channels.py:455](../storages/plugins/telegram/channels.py#L455) já limpa o `None` — não inventar default.
2. `[paralelo]` Em [channels.py:278-280](../storages/plugins/telegram/channels.py#L278-L280), logar antes de desistir: `logger.warning("telegram getFile recusou %s: %s", file_id, meta.get("error"))`. **Não** mudar o `return None` (o core depende dele).
3. `[sequencial]` Conferir que o `download_media` do ramo de bytes ([channels.py:287-289](../storages/plugins/telegram/channels.py#L287-L289)) já loga o status HTTP — está lá, não duplicar.

**Pronto quando:** enviar qualquer mídia no Telegram em dev e ver `file_size` dentro de `media_extras` no `filter.message.before_save` (via `debug_bus` ou `GET /api/webhook-payloads`); e forçar uma falha de `getFile` produzir **uma linha de log com o texto do Telegram**.

#### Status de execução — Fase F1
**Estado:** ✅ Concluída (2026-08-17)
- **O que foi feito:** `channels.py` — `file_size` acrescentado ao `extras` dos **7** ramos com arquivo (`photo`, `voice`, `audio`, `video`, `video_note`, `document`, `sticker`); e o `download_media` passou a logar a recusa do `getFile` (`logger.warning` com `meta["error"]`) antes do `return None`.
- **Como foi feito / decisões:** o `return None` foi mantido intacto — o core depende dele. **Não** foi logada a URL montada em seguida: ela carrega o token do bot. O ramo de bytes já logava o status HTTP e não foi duplicado. A poda de `None` que já existia no fim do parse cobre payload sem o campo, então nenhum default foi inventado.
- **Problemas / pendências:** nenhuma. O teste pré-existente `test_voice_message_contract_remains_unchanged` compara `media_extras` por **igualdade exata** e continuou verde porque o fixture dele não traz `file_size` — a poda de `None` preserva o contrato.
- **Verificação:** `test_file_size_sobrevive_ao_parse_em_todos_os_ramos` (7 ramos) e `test_payload_sem_file_size_nao_inventa_a_chave`; suíte do plugin verde.

---

### Fase F2 — Módulo puro de decisão: `media_failure.py` 🟢

**Objetivo:** um só lugar decide "esta mídia falhou, por este motivo, e o texto é este" — consumido pelo filtro (F4) e pelo evento (F3), para as duas superfícies nunca discordarem.

**Itens**
1. `[sequencial]` Criar `storages/plugins/telegram/media_failure.py`, **sem** importar `db`, `httpx` ou `plugins.*` (módulo puro ⇒ testável sem harness).
2. Assinatura sugerida (ilustrativa, não é implementação):
   ```python
   BOT_DOWNLOAD_LIMIT = 20 * 1024 * 1024   # 20 MiB — piso para CULPAR o tamanho

   def describe(media_type: str | None, media_path: str | None,
                media_extras: dict | None) -> dict | None:
       """None quando não há falha a explicar. Senão: {reason, bubble, note, size_mb}."""
   ```
3. `[sequencial]` Regras de decisão, todas com razão:
   - `media_path` preenchido, ou `media_type` sem arquivo por design (`interactive`, `location`, `live_location`, `poll`, `contact`, `contacts`, `order`, `product`) ⇒ **`None`** (nada a explicar).
   - `media_path` vazio **e** `file_size >= BOT_DOWNLOAD_LIMIT` ⇒ `reason="too_big"`.
   - `media_path` vazio e tamanho **abaixo** do piso (ou desconhecido) ⇒ `reason="unknown"`, com texto que **não inventa causa**.
   - O piso é o **maior** dos dois valores possíveis (20 MiB, não 20 MB decimal) de propósito: errar culpando o tamanho manda o atendente pedir compressão de um arquivo de 5 MB. Errar para o genérico só perde detalhe, e continua honesto. Ver R6.
4. `[sequencial]` Textos, cientes de A1/A2 (§2.3) e de D3:
   - **bolha** — `⚠️ Vídeo não recebido — 24,0 MB, acima do limite de 20 MB do Telegram`. **NUNCA** começar com `[Vídeo` (A1) nem cair em `MEDIA_PLACEHOLDERS`.
   - **nota** — 3 linhas: o que aconteceu · por que · **o que fazer** (reenviar em qualidade menor **ou** mandar para uma conta comum do Telegram, onde o limite não existe — D3).
   - Prefixo com emoji (`⚠️ Mídia não recebida`), seguindo a convenção que o `protocolos` (`🔖`) e o `janela_72h` (`📣`) criaram: é o que torna a nota reconhecível como automação **e** recortável por `ai_history_exclude_patterns` se o operador quiser (R3).
   - Substantivo por tipo (`vídeo`/`áudio`/`documento`/`figurinha`), não "mídia" genérico.

**Pronto quando:** `pytest` do módulo cobre os 4 ramos (ok · too_big · unknown · tipo-sem-arquivo) e o texto da bolha é asserido como **não** começando com `[Vídeo`.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída (2026-08-17)
- **O que foi feito:** novo `media_failure.py` — puro (não importa `db`, `httpx` nem `plugins.*`) — com `BOT_DOWNLOAD_LIMIT = 20 * 1024 * 1024`, `_size_label()` e `describe(media_type, media_path, media_extras) -> dict | None` devolvendo `{reason, kind, noun, size_bytes, size_label, bubble, note}`.
- **Como foi feito / decisões:** **um desvio deliberado do plano, para melhor.** O plano previa uma lista de tipos "sem arquivo por design" (`location`, `contact`, `poll`, …) hardcoded. Foi trocada pelo teste de **presença de `media_id`**, que é o mecanismo REAL do core: `_resolve_inbound_media` ([message_ingest_service.py:203-211](../app/services/message_ingest_service.py#L203-L211)) só chama `download_media` quando `media_extras["media_id"]` existe. Uma lista de tipos ficaria desatualizada no primeiro tipo novo e passaria a escrever nota em toda mensagem de contato/localização. Demais decisões conforme o plano: piso em 20 **MiB** (o maior dos dois sentidos de "20 MB", para nunca culpar o tamanho por chute); tamanho exibido em MiB rotulado "MB" para a frase ser internamente coerente; substantivo por tipo em vez de "mídia"; o contorno da D3 dentro do texto da nota.
- **Problemas / pendências:** dois defeitos meus, achados e corrigidos aqui. (1) O `_size_label` da 1ª versão devolvia 2 casas decimais para **qualquer** tamanho abaixo do limite ("5,00 MB"); passou a subir a precisão só no caso ambíguo — 20,04 MiB imprimiria "20,0 MB — acima do limite de 20 MB", frase que se lê como bug. (2) O contorno era um texto único e mandava "peça o reenvio em qualidade menor" **também para documento** — não se reduz a qualidade de um PDF, e um aviso que soa errado é ignorado pelo atendente; virou `_workaround(kind)`, que só oferece "qualidade menor" para vídeo/áudio/imagem e "versão compactada" para o resto. A saída real (mandar para uma conta comum) continua nos dois.
- **Verificação:** 15 testes do módulo puro (limite, sucesso, texto, tipo-sem-arquivo, acima/abaixo/no limite, `file_size` malformado, rótulo de 2 casas, substantivo por tipo, tipo desconhecido, contorno por tipo) — verdes.

---

### Fase F5c — Caracterização ANTES de mexer 🟢

**Objetivo:** congelar o comportamento atual, para provar depois que a mudança é a pretendida e não um efeito colateral.

**Itens**
1. `[paralelo]` Teste que passa um update de vídeo com `file_size` acima do teto por `parse_inbound` e assere o estado de HOJE: `media_type == "video"`, `media_path is None`, `text == ""`.
2. `[paralelo]` Teste que assere que um vídeo **com legenda** hoje produz `text == caption` **e** `extras["caption"] == caption` ([channels.py:448-452](../storages/plugins/telegram/channels.py#L448-L452)) — é a linha de base que F4 não pode quebrar (A2/R2).
3. `[sequencial]` Rodar e ver **verde** antes de abrir F3/F4.

**Pronto quando:** os dois testes passam contra o código **não modificado** (a menos do F1, que não muda comportamento).

#### Status de execução — Fase F5c
**Estado:** ✅ Concluída (2026-08-17)
- **O que foi feito:** novo `tests/python/test_media_failure_characterization.py` (5 testes), usando o **payload real do incidente** (25.204.422 bytes, contato `8190184333`).
- **Como foi feito / decisões:** congela (1) que o parse nasce mudo (`media_type='video'`, `media_path is None`, `text == ""`) e (2) que a legenda do cliente vai para `text` **e** para `extras["caption"]` — é essa segunda invariante que obriga a F4 a ACRESCENTAR em vez de substituir.
- **Problemas / pendências:** nenhuma. Rodou **antes** de F3/F4 existirem, como a disciplina exige.
- **Verificação:** `python3 scripts/test_plugins.py telegram` → **15 passed** (10 pré-existentes + 5 novos) com F3/F4 ainda não escritas.

---

### Fase F3 — Nota privada no fio da conversa 🟢 `[depende de: F1, F2]`

**Objetivo:** o atendente descobre o motivo real e o que fazer, sem sair da conversa. É a superfície que sustenta o plano (§2.3).

**Itens**
1. `[sequencial]` Criar `storages/plugins/telegram/events.py` com `EVENT_HANDLERS = {"message.saved": <handler>}` (forma: [protocolos/events.py:35-45](../storages/plugins/protocolos/events.py#L35-L45)) e declarar `events: events` no bloco `entry:` de [plugin.yaml:14](../storages/plugins/telegram/plugin.yaml#L14).
2. `[sequencial]` **Guard na primeira comparação** — o `message.saved` chega para **todos** os canais e **todo** inbound. Sair imediatamente quando o `channel_id` não é de um canal deste plugin, quando não há `media_type`, ou quando `media_failure.describe(...)` devolve `None`.
3. `[sequencial]` Escrever a nota copiando os 4 cuidados de [janela_72h/note.py](../storages/plugins/janela_72h/note.py):
   - ancorar no canal da **CONVERSA**, não do contato (contact-scoped funde canais numa instalação multicanal);
   - **`reopen=False`** — a nota é automação; sem isso, uma nota que chegue depois de o atendimento ter sido resolvido o **reabriria**;
   - usar a **linha devolvida** por `add_message` para montar o `broadcast("new_message")`, nunca um `get_last` (numa rajada devolve outra mensagem);
   - **best-effort** — nota que falha nunca derruba o ingest; broadcast que falha não invalida a nota (ela já está gravada).
4. `[sequencial]` Usar o `conversation_id` que vem **no payload** do evento (plano 123). Ausente ⇒ degradar para o caminho por contato do precedente, **nunca** resolver por telefone sem o inbox (foi o fechamento em cascata do `protocolos` — CLAUDE.md).
5. `[sequencial]` Import defensivo de `db.repositories` / `plugins.context` (`try/except` que degrada), porque não são API declarada.

**Pronto quando:** em dev, enviar do Telegram um arquivo acima de 20 MB e ver, no fio: a bolha da mídia **e** o card de nota privada explicando motivo + o que fazer. Repetir com a conversa **resolvida** e confirmar que ela **continua** resolvida.

#### Status de execução — Fase F3
**Estado:** ✅ Concluída (2026-08-17)
- **O que foi feito:** dois arquivos novos — `events.py` (`EVENT_HANDLERS = {"message.saved": on_message_saved}`) e `notice.py` (`is_own_channel()` + `write_media_failure_note()`), mais `events: events` no `entry:` do manifest.
- **Como foi feito / decisões:** o lado impuro ficou num módulo próprio (`notice.py`), espelhando `janela_72h/note.py`, para o handler ficar legível e o gate ser reusável pela F4. Os 4 cuidados do precedente estão lá: canal da mensagem, **`reopen=False`** (detectado por `inspect.signature`, não por `TypeError`), a **linha devolvida** por `add_message` no broadcast, e best-effort em dois níveis (nota que falha não derruba o ingest; broadcast que falha não invalida a nota já gravada). **Dois acréscimos ao plano:** (1) o gate de provider (`is_own_channel`) lê o **registry EM MEMÓRIA** (`registry.get()` devolve a instância do canal, com `.provider`) em vez de consultar `channels` no banco — o plano admitia um `SELECT`, e não é preciso; é **fail-closed**, porque escrever "o Telegram recusou" sobre uma mídia do WhatsApp é pior que não escrever nada; (2) o `conversation_id` do payload não é usado para escrever, e sim para **conferir**: se a nota pousar em conversa diferente da que o evento nomeou, sai um `logger.warning` — a mis-âncora silenciosa é exatamente a classe de bug que fechou o `protocolos` em cascata.
- **Problemas / pendências:** a ordem dos guards é carregante e está comentada no código: 3 `dict.get` → veredito puro → gate (memória). O único estado consultado só é tocado depois de a mídia já ter sido reconhecida como falha (~0% do tráfego).
- **Verificação:** testes do gate (fail-closed sem registry; aceita `telegram`, recusa `whatsapp_cloud` e canal inexistente) e o teste de costura pelo loader real confirmando `message.saved` registrado pelo plugin `telegram`. Validação manual com arquivo real: pendente (F8).

---

### Fase F4 — A bolha deixa de ser muda 🟢 `[depende de: F1, F2]`

**Objetivo:** quem só olha o fio (sem ler a nota) entende a caixa cinza.

**Itens**
1. `[sequencial]` Criar `storages/plugins/telegram/filters.py` com `FILTERS = {"filter.message.before_save": <fn>}` e declarar `filters: filters` no `entry:`.
2. `[sequencial]` **ACRESCENTAR** ao `text`, jamais substituir (A2/R2): vídeo com legenda tem a legenda no `text` ([channels.py:449](../storages/plugins/telegram/channels.py#L449)), e sobrescrevê-la apagaria o que o cliente escreveu do `content` — que é o que alimenta o LLM.
3. `[sequencial]` **Não** tocar em `media_extras["caption"]`: ele vira `media_caption`, cuja semântica é "verbatim o que o cliente digitou" ([messaging_service.py:1218-1223](../app/services/messaging_service.py#L1218-L1223)). Escrever nosso texto ali seria mentira registrada em coluna.
4. `[sequencial]` **NUNCA devolver `None`** deste filtro — `None` **descarta a mensagem inbound**. Envolver tudo em `try/except` que devolve o `value` intacto.
5. `[sequencial]` Registrar no código, com comentário, que **o texto não aparece quando a mídia tem legenda** (A2) — é limitação conhecida e aceita; a nota (F3) cobre esse caso.

**Pronto quando:** vídeo grande **sem** legenda mostra o texto explicativo abaixo da caixa cinza; vídeo grande **com** legenda mostra a legenda do cliente intacta (e a nota explica); e um teste assere que o texto **não** começa com `[Vídeo` (A1).

#### Status de execução — Fase F4
**Estado:** ✅ Concluída (2026-08-17)
- **O que foi feito:** novo `filters.py` (`FILTERS = {"filter.message.before_save": explain_undownloaded_media}`, prioridade default) + `filters: filters` no `entry:`.
- **Como foi feito / decisões:** ACRESCENTA ao `text` (`f"{text}\n\n{bubble}"`), nunca substitui; **não** toca em `media_extras["caption"]`; devolve uma **cópia** (`dict(value)`) em vez de mutar o dict recebido, porque outro filtro da cadeia pode ter guardado a referência; idempotente (se o texto já contém o aviso, sai sem mexer). A limitação de A2 está registrada em comentário no arquivo: com legenda, o texto não aparece na bolha — mas **continua valendo para o contexto da IA**, que passa a saber que o vídeo não chegou (D5), e a nota da F3 cobre a tela.
- **Problemas / pendências:** nenhuma pendência. Um teste meu estava errado, não o código: eu assertei que o filtro nunca devolve `None` **inclusive recebendo `None`**, e devolver `None` para `None` é o certo (passa adiante intacto; e o core nunca entrega `None` a um filtro, porque `apply_filter` aborta a cadeia antes). O teste foi reescrito para a invariante real: **nunca transformar valor não-`None` em `None`**.
- **Verificação:** 12 testes do filtro — explica a bolha muda, acrescenta sem apagar legenda, não escreve em `media_caption`, não muta o dict, idempotência, ignora outro provider, ignora mensagem normal, nunca devolve `None` (6 entradas malformadas), sobrevive a veredito que levanta exceção.

---

### Fase F6 — Testes, versão e zip 🟢

**Objetivo:** o comportamento fica travado por teste e o artefato publicável é reprodutível.

**Itens**
1. `[paralelo]` Testes em `../whatsbot-pro-plugins/plugins/telegram/tests/python/` (hoje só há `test_inbound_reply_parsing.py`): módulo puro (F2), `parse_inbound` com `file_size` (F1), filtro que acrescenta sem destruir legenda (F4), e — o que mais importa — **um teste que sobe o app pelo loader real** e confirma que `entry.events`/`entry.filters` foram de fato cabeados. Teste que importa o módulo por caminho continua **verde com a costura arrancada** (CLAUDE.md).
2. `[paralelo]` Bump de versão no `src/plugin.yaml` conforme a decisão de F0. `whatsbot_api_version`: manter `">=1.0,<2.0"` **se** houver degradação para core sem `conversation_id`; exigir `">=1.3,<2.0"` se a implementação **depender** do campo (F3 item 4) — escolher e escrever a razão.
3. `[sequencial]` `python3 scripts/test_plugins.py telegram` e `python3 scripts/build_plugins.py telegram`, depois `--check`.

⚠️ `--check` pode acusar "outdated" por **permissão de arquivo** (zip 664 em vez de 644) — precedente registrado. Rebuildar para "consertar" é o caminho destrutivo; confira o modo antes.

**Pronto quando:** runner do plugin verde e `--check` limpo por conteúdo.

#### Status de execução — Fase F6
**Estado:** ✅ Concluída (2026-08-17)
- **O que foi feito:** `tests/python/test_media_failure.py` (novo, 30 testes contando parametrizações) cobrindo módulo puro, filtro, gate, manifest e **a costura pelo loader real**. Versão `1.3.1 → **1.4.0**` (MINOR: feature nova, nada removido) em `src/plugin.yaml`, `telegram.json` e `catalog.json`. Zip regerado.
- **Como foi feito / decisões:** **`whatsbot_api_version` subiu para `">=1.3,<2.0"`** — o plano deixava a escolha aberta, e a razão de apertar está escrita como comentário no manifest: o gate de provider depende do `channel_id` que o `message.saved` só carrega a partir da **API 1.3.0 (plano 123)**; num core anterior o gate é fail-closed e a nota **nunca** seria escrita — feature morta em silêncio, exatamente o que esse campo existe para impedir. Verificado que é seguro: o `WHATSBOT_API_VERSION` deste checkout é `1.3.0` e **produção já emite `conversation_id`** no `message.saved` (log do `debug_bus`, id 717248). O teste de costura usa a fixture `plugin_app("telegram")` (loader real) e faz o patch do gate no **namespace canônico** (`loaded_plugin_module`), não no módulo carregado por caminho — este último não afetaria o objeto cabeado no app vivo.
- **Problemas / pendências:** o builder recusou a 1ª tentativa por `telegram.json` ainda dizer 1.3.1 (ele compara `id`/`name`/`version`/`whatsbot_api_version` do metadado com o manifest) — atualizados os dois + `catalog.json`. ⚠️ O repositório de plugins tem **trabalho não commitado de outra frente** (`instagram` e `protocolos`, ~2.400 linhas): não foi tocado, e o commit da 1.4.0 precisa selecionar só os arquivos do `telegram`.
- **Verificação:** `python3 scripts/test_plugins.py --python-only telegram` → **58 passed**. A costura foi provada **carregante**: removendo `filters: filters` do manifest, o teste do loader falha com "entry.filters não foi cabeado pelo loader" (manifest restaurado em seguida). `build_plugins.py telegram` → `updated (12 files, 31615 bytes)`; `--check` → `current` (limpo por conteúdo, modo 644 — sem o falso "outdated" por umask). Zip inspecionado: contém os 4 módulos novos, **sem** `tests/` e sem `__pycache__`.

---

### Fase F7 — *(OPCIONAL — só se P1 = sim)* O tooltip do core para de mentir 🟢

**Objetivo:** separar "nunca foi baixado" de "arquivo sumiu do servidor" no placeholder — hoje os dois casos usam o mesmo texto, e é ele que manda o leitor investigar volume.

**Itens**
1. `[sequencial]` Em [MediaContent.js:16-31](../web/static/js/components/contacts/MediaContent.js#L16-L31), distinguir `!src` (nunca houve arquivo) de `failed` após erro de carga: no 1º caso, rótulo tipo "Vídeo não recebido" e tooltip sem acusar o servidor.
2. `[sequencial]` Genérico e sem `if provider ==`: vale para qualquer canal que entregue mídia sem arquivo.
3. `[sequencial]` Conferir contraste nos **dois** temas (regra de modo escuro do repo) — o bloco usa `bg-wa-hover`/`text-wa-secondary`, então herda; confirmar visualmente.

**Pronto quando:** mensagem com `media_path` nulo mostra rótulo/tooltip de "não recebido"; mensagem com path cujo arquivo foi apagado do disco continua com o texto de arquivo perdido.

#### Status de execução — Fase F7
**Estado:** ⬜ Não iniciada — **aguardando a P1**
- **O que foi feito:** nada, deliberadamente. O usuário confirmou o escopo como "só o plugin do Telegram", e esta é a **única** fase do plano que tocaria em arquivo do core.
- **Como foi feito / decisões:** ela não conserta a falha — o aviso da F3/F4 já conta a verdade no fio. O que ela conserta é o **falso rastro**: o tooltip continua dizendo "não está mais disponível no servidor" para um arquivo que nunca existiu, e foi essa frase que mandou a investigação olhar volume/persistência. Vale para qualquer canal, não só Telegram.
- **Problemas / pendências:** decisão da **P1**. São ~5 linhas em [MediaContent.js:16-31](../web/static/js/components/contacts/MediaContent.js#L16-L31) e sai em commit separado.
- **Verificação:** n/a.

---

### Fase F8 — Instalar no local, validar, e só então publicar 🔴

**Objetivo:** o que o usuário testa é `storages/plugins/telegram/` — commit e zip não mudam o que roda.

**Itens** `[sequencial]`
1. Sincronizar a fonte → cópia viva em `storages/plugins/telegram/` e **reiniciar** (toggle de plugin derruba o processo; sem supervisor o servidor não volta sozinho).
2. Validação manual do caminho inteiro: arquivo acima de 20 MB (vídeo **e** um documento — o teto vale para os dois) → bolha explica, nota aparece, log traz o erro do Telegram; mídia normal continua **byte-idêntica** (imagem, áudio, documento pequeno, figurinha).
3. Confirmar que uma mídia **sem** falha **não** gera nota nenhuma (o guard do item 2 de F3).
4. Commit no repo do core (se F7 entrar) e publish no repo de plugins, respeitando a decisão de F0.

⚠️ Antes de publicar, reconferir a tabela `plugins` de produção: a versão pode ter sido publicada **no meio do trabalho** por outra pessoa — `git fetch` não mostra isso (precedente registrado).

**Pronto quando:** rodando em dev com validação manual escrita acima, e a decisão de publicação tomada com a versão de produção conferida no mesmo dia.

#### Status de execução — Fase F8
**Estado:** ✅ Concluída para o essencial — validada em dev pelo operador e publicada; sobram só conferências de borda
- **O que foi feito:** os 6 arquivos (`channels.py`, `media_failure.py`, `events.py`, `filters.py`, `notice.py`, `plugin.yaml`) copiados de `src/` para `storages/plugins/telegram/`; `diff -r` confirma **cópia viva == fonte, byte a byte** (a divergência antiga do `name:` foi eliminada, adotando a convenção "Canal · "). O servidor de dev tem `--reload-dir storages/plugins` e recarregou sozinho. Commit `c872280` no repo de plugins (11 arquivos, **só** `telegram` + a linha dele no `catalog.json`) e merge/push da `main`.
- **Como foi feito / decisões:** confirmado antes de copiar que o dev aponta para **outro banco** (`10.8.200.13/whatsbot`) que não o de produção (`10.8.100.5`) — se compartilhassem, a redescoberta do dev teria reescrito a linha de versão de produção. No commit, o `catalog.json` foi restaurado ao `HEAD` e reeditado só na linha do telegram, para não arrastar as edições de outra frente (protocolos 1.34.0, pagamentos) nem **rebaixar** o instagram (o working tree tinha 3.0.0; o remoto já estava em 3.3.0).
- **Problemas / pendências:** **(a)** validado à mão com **áudio** de 125,6 MB — bolha explica na própria mensagem e a nota privada aparece no fio (print do operador); falta repetir com **documento** grande (R10) e reenviar o mesmo arquivo para confirmar que não duplica a nota (R12); **(b)** conferir a conversa **resolvida** continuar resolvida; **(c)** exportar a cópia de produção e comparar antes de importar (resíduo da F0).
- **Verificação:** o plugin recarregou e a tabela `plugins` do banco de dev mostra `telegram 1.4.0, enabled=1, load_error=NULL` — o manifest novo (com `entry.events`/`entry.filters` e `whatsbot_api_version ">=1.3,<2.0"`) é aceito pelo loader real em boot de verdade, não só em teste. Suíte reexecutada **depois** do core subir para `WHATSBOT_API_VERSION` 1.5.0 (commit `67bd855`): **58 verdes**, `build_plugins.py --check telegram` → `current`.

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| **R1** Texto da bolha | Começar com `[Vídeo` ⇒ o painel **suprime** e parece que o código não rodou ([MediaContent.js:78](../web/static/js/components/contacts/MediaContent.js#L78)) | Prefixo `⚠️`; teste assere o `startsWith` |
| **R2** Vídeo com legenda | `media_caption` tem precedência ⇒ nosso texto invisível ([messageView.js:250](../web/static/js/services/messageView.js#L250)) | A **nota** é a superfície que sustenta o plano; o texto **acrescenta** ao `text`, nunca substitui |
| **R3** Contexto do LLM | Nota e texto entram no histórico da IA (`private_note` não está na lista-negra de roles) | **Desejado** (D5). Prefixo com emoji deixa a nota recortável por `ai_history_exclude_patterns` se o operador preferir |
| **R4** Conversa resolvida | Nota de automação **reabriria** o atendimento | `reopen=False`, como no precedente |
| **R5** Filtro devolvendo `None` | `filter.message.before_save` com `None` **DESCARTA a mensagem inbound** — derruba a caixa de entrada | `try/except` devolvendo o `value` intacto; nunca `return None` |
| **R6** Fronteira 20 MB × 20 MiB | Culpar o tamanho num arquivo que falhou por outro motivo manda o atendente pedir compressão sem sentido | Piso no valor **maior** (20 MiB); abaixo dele, texto genérico que não inventa causa. E **não** gatear o download (D6) |
| **R7** Duas cópias do plugin | Editar `storages/` e esquecer `src/` (ou o inverso) | F8 item 1 + F0; precedente já registrado no repo |
| **R8** Produção em 1.2.2 | Publicar o zip apaga um delta vivo em silêncio | F0 é 🔴 e bloqueia todas as fases |
| **R9** `video_note` | Nota de vídeo redonda também mapeia para `media_type="video"` ([channels.py:420-425](../storages/plugins/telegram/channels.py#L420-L425)) e hoje também não captura `file_size` | F1 cobre **todos** os ramos |
| **R10** Escopo além do vídeo | O teto de 20 MB vale para **todo** tipo; documento no Telegram vai a 2 GB, então PDF grande falha igual **hoje** | Módulo de decisão kind-agnóstico (F2); F8 valida com documento também |
| **R11** Evento de alto volume | `message.saved` chega para todo inbound de todo canal | Guard na primeira comparação (F3 item 2); nada de DB antes dele |
| **R12** Nota duplicada | Update reentregue geraria duas notas | O core já deduplica em `state.processed_messages` ([message_ingest_service.py:380-389](../app/services/message_ingest_service.py#L380-L389)); confirmar em F8 reenviando o mesmo arquivo |

**Disciplina do repo aplicada:** verde a cada fase · caracterização (F5c) antes de mexer no fluxo de inbound · um refactor por commit · nunca avançar com teste vermelho não-explicado.

---

## 6. Perguntas em aberto

**P1 — O tooltip mentiroso do core (F7) entra neste plano?**
Contexto: [MediaContent.js:24](../web/static/js/components/contacts/MediaContent.js#L24) diz "O arquivo de mídia não está mais disponível no servidor" também quando o arquivo **nunca existiu** — foi essa frase que apontou a investigação para volume/persistência. É defeito de core, genérico a todos os canais, e a correção é pequena.
(a) Entra como F7 opcional — o fio para de mentir em qualquer canal. (b) Fica fora, mantendo D4 estrito (zero core), aceitando que o texto do plugin abaixo da caixa já conta a verdade.
**Recomendação: (a)**, como fase separada e commit separado — é 5 linhas de frontend e mata o falso rastro para sempre. ⏸️ **Ainda aberta em 2026-08-17.** Na execução o usuário confirmou o escopo como "só o plugin do Telegram", então a F7 **não** foi feita: o aviso no fio já conta a verdade, e o que sobra é o tooltip continuar apontando para o lugar errado na próxima investigação (de qualquer canal, não só Telegram).

**P2 — Baixar a thumbnail do vídeo grande?**
Contexto: o mesmo payload traz `thumbnail.file_id` com `file_size: 15583` (15 KB) — perfeitamente baixável. O atendente veria o frame em vez de uma caixa cinza.
(a) Fazer. (b) Não fazer agora. ⚠️ Apontar `media_path` para a thumbnail **quebra** o player (`<video>` com um JPEG) e mudar `media_type` para `image` seria mentira; exigiria um conceito de *poster* que o core não tem.
**Recomendação: (b) adiar** — o ganho é estético e o caminho honesto custa mudança no core, contra D4. ⏸️ Registrado para um plano futuro.

**P3 — Avisar o CLIENTE, não só o atendente?**
Contexto: hoje o cliente acha que enviou. Como a nota entra no contexto do LLM (R3), a IA **pode** pedir o reenvio por conta própria quando estiver ativa; numa conversa com humano, ninguém avisa.
(a) Deixar a critério da IA/atendente (nada a implementar). (b) Resposta automática ao cliente.
**Recomendação: (a)** — resposta automática atravessaria o gate de humano no comando (plano 96) e mereceria plano próprio. ⏸️

---

## 7. Apêndice — arquivos-chave

**Plugin `telegram` (fonte: `../whatsbot-pro-plugins/plugins/telegram/src/`; cópia viva: `storages/plugins/telegram/`)**

| Arquivo | O que muda |
|---|---|
| `channels.py` | F1 — `file_size` em todos os ramos de mídia ([:405-435](../storages/plugins/telegram/channels.py#L405-L435)); log do `getFile` ([:278-280](../storages/plugins/telegram/channels.py#L278-L280)) |
| `media_failure.py` | **novo** — F2, módulo puro de decisão + textos |
| `events.py` | **novo** — F3, `EVENT_HANDLERS = {"message.saved": …}` |
| `filters.py` | **novo** — F4, `FILTERS = {"filter.message.before_save": …}` |
| `plugin.yaml` | `entry.events`, `entry.filters`, versão ([:3](../storages/plugins/telegram/plugin.yaml#L3)) |
| `tests/python/` | F6 — módulo puro, parse, filtro, e o teste de costura pelo loader real |

**Core — somente leitura (nada muda, exceto F7 se P1 = sim)**

| Arquivo | Papel |
|---|---|
| [app/services/message_ingest_service.py](../app/services/message_ingest_service.py) | download ([:396](../app/services/message_ingest_service.py#L396)), filtro ([:456](../app/services/message_ingest_service.py#L456)), dedupe ([:380-389](../app/services/message_ingest_service.py#L380-L389)) |
| [app/services/messaging_service.py](../app/services/messaging_service.py) | save do item de mídia ([:1209-1231](../app/services/messaging_service.py#L1209-L1231)) |
| [server/transcription.py](../server/transcription.py) | por que a bolha fica muda ([:115-120](../server/transcription.py#L115-L120)) |
| [web/static/js/components/contacts/MediaContent.js](../web/static/js/components/contacts/MediaContent.js) | placeholder + supressão `[Vídeo` — **F7 se P1 = sim** |
| [web/static/js/services/messageView.js](../web/static/js/services/messageView.js) | precedência de `media_caption` ([:248-251](../web/static/js/services/messageView.js#L248-L251)) |
| [storages/plugins/janela_72h/note.py](../storages/plugins/janela_72h/note.py) | **precedente a copiar** na F3 |

---

## 8. Checklist de verificação

- [x] F0 respondida por escrito (linhagem 1.2.2 × 1.3.1) **antes** de qualquer código
- [x] Caracterização (F5c) verde **antes** de F3/F4 — 15 passed com F3/F4 ainda inexistentes
- [x] `python3 scripts/test_plugins.py telegram` verde (repo externo) — **58 passed**
- [x] Teste que sobe o app pelo **loader real** confirma `entry.events` + `entry.filters` cabeados — e provado carregante (falha se o `entry` sair)
- [x] `venv/bin/python -m pytest tests/contracts tests/integration` no Postgres (`WHATSBOT_TEST_DB_URL`) — **6 falhas, nenhuma deste plano**; ver a nota abaixo
- [x] Nenhuma suíte Postgres rodando em paralelo (mesmo schema `public`, inclusive de outra máquina)
- [x] Texto da bolha **não** começa com `[Vídeo` e **não** cai em `MEDIA_PLACEHOLDERS` — asserido para os 5 tipos e os 2 motivos
- [x] Vídeo **com** legenda: legenda do cliente intacta; `media_caption` não escrito pelo plugin (2 testes)
- [x] `filter.message.before_save` **nunca** devolve `None` — 6 entradas malformadas + veredito que levanta exceção
- [x] Conversa **resolvida** não é reaberta pela nota (`reopen=False` por `inspect.signature`) — **reconfirmar à mão na F8**
- [x] Mídia baixada com sucesso **não** gera nota nenhuma — guard no 1º/2º `get`, testado
- [x] Documento acima de 20 MB recebe o mesmo tratamento do vídeo (R10) — decisão kind-agnóstica, testada nos 7 ramos
- [ ] Reenvio do mesmo arquivo não duplica a nota (R12) — **validar à mão (F8)**
- [ ] Card da nota legível no **modo escuro** — render é o card `private_note` do core, sem CSS novo; **confirmar visualmente**
- [x] Restart de plugin exercitado sem `load_error` na tabela `plugins` — reload do dev deu `telegram 1.4.0, load_error=NULL`
- [x] Nenhum segredo em log/URL — o log novo cita `file_id` + a mensagem do Telegram, **nunca** a URL montada (que carrega o token)
- [x] `build_plugins.py --check` limpo **por conteúdo** — `current`, modo 644
- [x] Cópia viva em `storages/plugins/telegram/` sincronizada com `src/` (`diff -r` limpo)
- [ ] Versão de produção reconferida no dia da publicação — **pendente**, junto do export da cópia de prod (resíduo da F0)

> **Nota sobre a suíte do core (2026-08-17).** `tests/contracts tests/integration` terminou com **6 falhas**, e nenhuma pertence a este plano — que não tocou em **nenhum arquivo do core** (`git status` do repositório: só os `.md` de plano, não rastreados).
>
> * `test_audit_matrix_is_complete` — **pré-existente e já conhecida**; reproduz sozinha, isolada.
> * 5 de `test_lifecycle_characterization` (`assign_then_close`, `reopen_closed_conversation`, `reopen_noop_when_already_open`, `ai_toggle_conversation_scope`, `ai_toggle_contact_scope`) — **passam todas quando rodadas isoladamente**; falham só na varredura completa, o que as caracteriza como dependência de ordem/estado compartilhado entre testes de `tests/integration`.
>
> Que não podem envolver este plano é **estrutural, não estatístico**: essas fases usam a fixture `build_app`, que monta um app hermético com **exatamente `("gowa",)`** ([tests/support.py:425](../tests/support.py#L425)) — o plugin `telegram` nunca é carregado ali. Vale um olhar próprio para a poluição de ordem, fora deste plano.
