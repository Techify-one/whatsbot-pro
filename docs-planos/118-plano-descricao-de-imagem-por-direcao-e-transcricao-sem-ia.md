# Plano 118 — Descrever imagem por direção (recebida/enviada/privada) e transcrição que não depende da IA do canal

> **Status:** ✅ EXECUTADO (2026-08-18) — F0–F8 concluídas; ver o "Status de execução" de cada fase e o §8.
> Validação manual no navegador confirmada pelo usuário em 2026-08-18 (§8, último item).
> **Status original:** PLANEJAMENTO · **Data:** 2026-08-13 · **Escopo:** médio
> **Origem:** pedido do usuário — *"preciso de uma configuração para eu conseguir transcrever imagens
> enviadas por um usuário do whatsbot (igual o áudio) e também quero que essa opção e a opção de
> transcrever áudios não dependam da IA estar ativa no canal, pois atualmente só consigo marcá-las se o
> canal estiver com a IA ativa"* (com print do canal `telegram_9bf7bdfc`, IA desligada, sem os campos).
> **Método:** leitura do código real (backend + frontend + testes) nesta sessão; todo `arquivo:linha`
> abaixo foi verificado, nada de memória.
>
> São **dois** pedidos empilhados, e só um deles é de fato uma feature nova:
> **(A)** a descrição de imagem hoje é um `bool` que só vale para mídia **recebida** — não existe
> equivalente ao multi-select de direções do áudio (Recebidas/Enviadas/Privadas);
> **(B)** o backend **JÁ NÃO** gateia transcrição pela IA do canal — quem esconde as opções é só o
> formulário, que renderiza o bloco inteiro dentro de um `${aiOn ? … }`. O conserto de (B) é de UMA
> linha de JSX; o resto do plano é (A) e a rede de segurança em volta.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar
> para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário (travadas — não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| D1 | ✅ (2026-08-13) A descrição de imagem passa a ter **direções, igual ao áudio** | Novo `image_transcription_mode` (multi-select `received,sent,private`) no lugar do bool `image_transcription_enabled`. Cobre as duas leituras possíveis de "imagens enviadas por um usuário do whatsbot" (operador pelo painel **e** eco do próprio celular), sem ter de escolher entre elas |
| D2 | ✅ (2026-08-13) Transcrição/descrição **não** depende da IA do canal estar ligada | O bloco de transcrição sai de dentro do ramo `${aiOn}` em [AiSettingsFields.js:62](../web/static/js/components/channels/AiSettingsFields.js#L62)-[205](../web/static/js/components/channels/AiSettingsFields.js#L205), como o "Atendente padrão" já está (mesma justificativa, ver o comentário nas [linhas 45-47](../web/static/js/components/channels/AiSettingsFields.js#L45)) |
| D3 | ✅ Regra do repo: **zero regressão de comportamento** em instalação existente | `image_transcription_mode` ausente ⇒ derivado de `image_transcription_enabled` (`True` → `{received}`, `False` → `∅`). Canal legado se comporta byte a byte como hoje até alguém salvar o formulário |

---

## 1. Resumo executivo

A descrição de imagem existe desde sempre, mas é um interruptor binário lido em **um único ponto do
pipeline de entrada** ([messaging_service.py:1193-1200](../app/services/messaging_service.py#L1193)):
imagem que o **operador** envia pelo painel, imagem que sai do **celular** (eco) e imagem colada como
**nota privada** nunca passam por `describe_image`. O áudio já resolveu isso: o
`audio_transcription_mode` é um conjunto de direções (`received,sent,private`) avaliado no helper
compartilhado ([transcription.py:151-166](../server/transcription.py#L151)) e consultado nos quatro
call sites. **O plano generaliza esse mecanismo do áudio para a imagem** — mesmo vocabulário, mesmo
parser, mesmos quatro pontos — em vez de inventar um segundo desenho.

O segundo pedido é quase todo frontend: o gate `_channel_ai_enabled`
([contacts.py:169-173](../server/routes/contacts.py#L169)) alimenta **somente** `ai_may_speak`
([messaging_service.py:184-200](../app/services/messaging_service.py#L184)), e no lote de entrada a
transcrição roda **antes** desse gate ([messaging_service.py:1184-1240](../app/services/messaging_service.py#L1184)
vs. o gate na [linha 1241](../app/services/messaging_service.py#L1241)). Ou seja: **com a IA do canal
desligada a transcrição já funciona hoje** — o operador simplesmente não tem onde configurá-la, porque
o formulário some com os campos. Isso vira teste (F0) para deixar de ser afirmação.

De quebra a investigação achou **dois defeitos reais** no caminho que o plano tem de tocar, listados em
§3.2: a transcrição do áudio enviado pelo operador ignora o override do canal (lê o config **global**),
e a supressão de eco por `msg_id` está com a chave trocada entre quem escreve e quem lê.

---

## 2. Como funciona hoje (mapa verificado)

### 2.1 O helper único e o que ele decide

`server/transcription.py` (200 linhas) é o ponto ÚNICO de "transcrever ou não":

| Linha | Código | Papel |
|---|---|---|
| [35-60](../server/transcription.py#L35) | `parse_audio_modes(raw)` | string → `set` de `{received, sent, private}`; entende os legados `both`/`off`/`none`/valor único |
| [151-166](../server/transcription.py#L151) | o gate | `force` → passa · `audio` → por direção · `document`/`image` → **bool** |
| [176-178](../server/transcription.py#L176) | `filter.transcription.should_run` | plugin só pode **estreitar** |
| [180-198](../server/transcription.py#L180) | despacho | `transcribe_audio` · `transcribe_document` · `describe_image` |

⚠️ O `source` (`batch`/`echo`/`operator`/`private`/`group_no_mention`) **só é lido no ramo do áudio**
([linhas 155-160](../server/transcription.py#L155)). Para imagem/documento ele viaja até o filtro de
plugin e morre lá — é exatamente o eixo que a D1 destrava.

### 2.2 Os call sites (quem chama, com qual `source`, e o que existe para imagem)

| # | Call site | `arquivo:linha` | `source` | áudio | imagem | doc |
|---|---|---|---|---|---|---|
| 1 | Lote de entrada (cliente manda) | [messaging_service.py:1186](../app/services/messaging_service.py#L1186) / [1194](../app/services/messaging_service.py#L1194) / [1202](../app/services/messaging_service.py#L1202) | `batch` | ✅ | ✅ | ✅ |
| 2 | Eco do celular (`is_from_me`) | [message_ingest_service.py:323](../app/services/message_ingest_service.py#L323) | `echo` | ✅ | ❌ | ❌ |
| 3 | Envio do operador pelo painel | [messaging_service.py:345-356](../app/services/messaging_service.py#L345) | `operator` | ✅ | ❌ | ❌ |
| 4 | Nota privada | [contacts.py:1686-1704](../server/routes/contacts.py#L1686) (áudio) · [1795-1852](../server/routes/contacts.py#L1795) (imagem/doc) | `private` | ✅ | ❌ | ❌ |
| 5 | Grupo sem @menção | [message_ingest_service.py:522-544](../app/services/message_ingest_service.py#L522) | — | ❌ | ❌ | ❌ |
| 6 | Sandbox | [sandbox.py:211](../server/routes/sandbox.py#L211) / [343](../server/routes/sandbox.py#L343) | — | — | ⚠️ chama `describe_image` **direto**, sem o helper | ⚠️ idem |

⚠️ **O eco já resolve o caminho da imagem e o joga fora**:
[message_ingest_service.py:269](../app/services/message_ingest_service.py#L269) faz
`media_path, _img, audio_path = await self._resolve_inbound_media(event)` — o `_img` existe, está
descartado, e o docstring da função ([linha 255](../app/services/message_ingest_service.py#L255)) admite
em prosa: *"(Outgoing-audio transcription is a follow-up.)"*. O item 5 (grupo sem @menção) nunca
transcreveu nada apesar de o `source="group_no_mention"` estar documentado no CLAUDE.md — falso
positivo, ver §3.3.

### 2.3 Resolução per-canal

`channels/ai_settings.py` — `PER_CHANNEL_AI_KEYS` ([linhas 28-49](../channels/ai_settings.py#L28)) é a
**allow-list**: `ChannelSettingsView.get` ([linhas 96-115](../channels/ai_settings.py#L96)) só deixa o
override do canal vencer se a chave estiver ali. Chave nova que não entre na tupla é **silenciosamente
ignorada** — é a armadilha nº 1 deste plano.

Cadeia de fallback: `config.ai[key]` do canal → `config` global
([config/settings.py:156-160](../config/settings.py#L156)) → default in-code.

### 2.4 O formulário (a causa do print nº 2)

[AiSettingsFields.js](../web/static/js/components/channels/AiSettingsFields.js):

| Linha | Código | Efeito |
|---|---|---|
| [22](../web/static/js/components/channels/AiSettingsFields.js#L22) | `const aiOn = ai.ai_enabled !== false` | |
| [45-60](../web/static/js/components/channels/AiSettingsFields.js#L45) | "Atendente padrão" | **fora** do ramo — o precedente que a D2 imita, com o porquê escrito no comentário |
| [62](../web/static/js/components/channels/AiSettingsFields.js#L62) | `${aiOn ? html\`` | abre o ramo |
| [81-93](../web/static/js/components/channels/AiSettingsFields.js#L81) | "Descrever imagem" / "Ler documento" | escondidos com a IA off |
| [95-143](../web/static/js/components/channels/AiSettingsFields.js#L95) | bloco "Transcrição de áudio" | escondido com a IA off |
| [205](../web/static/js/components/channels/AiSettingsFields.js#L205) | `\` : null}` | fecha o ramo |

✅ **Salvar com a IA desligada NÃO apaga as chaves escondidas**: o estado `ai` nasce como
`{...aiDefaults, ...(cfg.ai||{})}` ([ChannelEditForm.js:25-28](../web/static/js/components/channels/ChannelEditForm.js#L25))
e `buildEditPayload` grava `ai: f.ai` inteiro
([constants.js:294-306](../web/static/js/components/channels/constants.js#L294)). Mover o bloco para
fora do ramo é, portanto, seguro — não há chave órfã para recuperar.

### 2.5 Onde a descrição aparece hoje

Imagem/documento de entrada → card privado `role="transcription"` no painel
([messaging_service.py:1229-1239](../app/services/messaging_service.py#L1229)). Só o **áudio** tem o
seletor "Onde aparece" (`private`/`chat`), implementado em `deliver_audio_transcription`
([messaging_service.py:716-772](../app/services/messaging_service.py#L716)) — e a própria UI já diz
*"Vale para recebidas/enviadas"* ([AiSettingsFields.js:131](../web/static/js/components/channels/AiSettingsFields.js#L131)).

---

## 3. Inventário

### 3.1 O trabalho (D1 + D2)

| # | Item | `arquivo:linha` | O que falta | Risco | Esforço |
|---|---|---|---|---|---|
| I1 | Campos visíveis com a IA off | [AiSettingsFields.js:62,81-143,205](../web/static/js/components/channels/AiSettingsFields.js#L62) | mover o bloco de transcrição para fora do `${aiOn}` + copy explicando que vale sem IA e que **consome crédito** | baixo | S |
| I2 | `image_transcription_mode` | [transcription.py:151-166](../server/transcription.py#L151) · [ai_settings.py:32](../channels/ai_settings.py#L32) · [settings.py:159](../config/settings.py#L159) | generalizar `parse_audio_modes` → `parse_media_modes` e ler direção também para imagem, com queda no bool legado | médio | M |
| I3 | Imagem no envio do operador | [messaging_service.py:243-369](../app/services/messaging_service.py#L243) | `transcribe_audio: bool` → gancho por `kind`; card privado após o envio | médio | M |
| I4 | Imagem no eco do celular | [message_ingest_service.py:269,320-332](../app/services/message_ingest_service.py#L269) | usar o `_img` já resolvido; `source="echo"` | médio | S |
| I5 | Imagem na nota privada | [contacts.py:1795-1852](../server/routes/contacts.py#L1795) | descrever com `source="private"`; card privado | baixo | S |
| I6 | UI do multi-select de imagem | [AiSettingsFields.js:81-87](../web/static/js/components/channels/AiSettingsFields.js#L81) · [constants.js:62-99](../web/static/js/components/channels/constants.js#L62) | checkbox → 3 checkboxes, `parse/serialize` genéricos, `node --test` | baixo | M |
| I7 | Sandbox coerente | [sandbox.py:211,343](../server/routes/sandbox.py#L211) | passar a usar o helper em vez de ler o bool cru | baixo | S |

### 3.2 Defeitos reais encontrados no caminho (entram no plano)

| # | Defeito | Evidência | Efeito hoje | Onde conserta |
|---|---|---|---|---|
| B1 | A transcrição do áudio **enviado pelo operador** ignora o override do canal | [messaging_service.py:350-356](../app/services/messaging_service.py#L350) chama o helper com `settings=self.settings` (o config **global**), enquanto o caminho de entrada usa `ai_settings.view(channel_id, …)` ([linha 797-802](../app/services/messaging_service.py#L797)) e a nota privada usa o wrapper com `channel_id` ([contacts.py:1687-1691](../server/routes/contacts.py#L1687)) | marcar/desmarcar "Enviadas" no canal **não tem efeito** no envio pelo painel; quem manda é o valor global | F3 (a mesma linha que ganha a imagem) |
| B2 | Supressão de eco por `msg_id` com **chave trocada** | quem escreve grava a chave crua ([messaging_service.py:311-312](../app/services/messaging_service.py#L311), [contacts.py:1459](../server/routes/contacts.py#L1459)); quem lê procura `f"{channel_id}:{msg_id}"` ([message_ingest_service.py:262-266](../app/services/message_ingest_service.py#L262)) | a rede que sobra é o `recently_sent` **por texto**, e ele só roda `if text:` ([message_ingest_service.py:274-280](../app/services/message_ingest_service.py#L274)) — imagem sem legenda não é coberta | F0 mede primeiro; se confirmado, F4 alinha a chave (senão a imagem do operador seria descrita **duas vezes**, cobrada duas vezes) |

⚠️ B2 é **medido antes de consertado**: pode ser que o GOWA não ecoe o que ele mesmo enviou pela
própria API, e aí não há sintoma. O teste do F0 responde isso em 10 linhas; consertar às cegas seria
mexer em supressão de eco sem sintoma, o pior lugar para chutar.

### 3.3 Falsos positivos descartados

| Suspeita | Por que NÃO é problema |
|---|---|
| "O backend bloqueia transcrição quando a IA do canal está off" | Não bloqueia. `_channel_ai_enabled` ([contacts.py:169-173](../server/routes/contacts.py#L169)) alimenta só `ai_may_speak`, e no lote a transcrição roda **antes** dele ([messaging_service.py:1184](../app/services/messaging_service.py#L1184) vs [1241](../app/services/messaging_service.py#L1241)). O `maybe_transcribe` não lê `ai_enabled` nem `auto_reply` em lugar nenhum |
| "O orquestrador não é agendado com a IA off" | É. `schedule_orchestrator` ([message_ingest_service.py:558](../app/services/message_ingest_service.py#L558)) é chamado sem gate de IA |
| "Salvar o canal com a IA off apaga as configurações escondidas" | Não apaga — §2.4 |
| "Falta transcrever no grupo sem @menção (`source="group_no_mention"`)" | O `source` é documentado no CLAUDE.md mas **nunca existiu** como call site: o ramo salva e volta ([message_ingest_service.py:522-544](../app/services/message_ingest_service.py#L522)). É lacuna **antiga e fora do pedido** — vira P3, não escopo |
| "Precisa de migration" | Não. Tudo vive em `config` (key-value) e em `channels.config['ai']` (JSON). Zero DDL |
| "Precisa bumpar `WHATSBOT_API_VERSION`" | Não: `tests/goldens/plugin_api_surface.json` guarda só os **nomes** dos filtros. Nenhum nome, campo de `ctx.extras` ou tipo muda — só passam a existir combinações novas de valores já existentes (`media_kind="image"` com `source="operator"/"private"`). Ver P2 |

---

## 4. Desenho

### 4.1 O modelo de configuração (I2)

```
audio_transcription_mode     "received,sent,private" | "both" | "off" | ""        (hoje)
image_transcription_mode     idem                                                  (NOVO)
image_transcription_enabled  bool — LEGADO, só lido quando o mode está ausente
document_transcription_enabled  bool — inalterado nesta entrega (P1)
```

Resolução do modo efetivo de um `media_kind`, em UM lugar (`server/transcription.py`):

1. `settings.get("<kind>_transcription_mode")` presente e não-nulo → `parse_media_modes(valor)`;
2. senão, chave legada `<kind>_transcription_enabled` → `True` = `{received}`, `False` = `∅`;
3. senão, default in-code `{received}`.

O passo 2 é o que garante a D3: **nenhum canal existente muda de comportamento** — imagem continua
descrita só na entrada até alguém marcar "Enviadas"/"Privadas".

`parse_audio_modes` continua exportado com o nome atual (é importado pelo teste legado e é superfície de
fato); vira um alias fino de `parse_media_modes`.

### 4.2 Onde a descrição da imagem aparece

Sempre **card privado** `role="transcription"` no painel — igual à imagem de entrada hoje. O seletor
"Onde aparece" continua **exclusivo do áudio**: mandar a descrição da própria imagem de volta para o
cliente não tem caso de uso e cria risco de loop (`message.sent` → eco → descrever de novo).

### 4.3 Defaults

| Chave | Default novo | Por quê |
|---|---|---|
| `image_transcription_mode` | `received` | é o que o parque tem hoje (`image_transcription_enabled=True`) |
| direções `sent`/`private` da imagem | **desmarcadas** | cada imagem descrita é uma chamada de visão paga; ligar por padrão cobraria todo mundo sem pedir. Mesmo critério do áudio, cujo default é só `received` ([settings.py:156](../config/settings.py#L156)) |

---

## 5. Fases e paralelização

```
WAVE 0   F0 (caracterização + medir B2) · F1 (IA-off na UI)        ← 🟢🟢 em paralelo
            │  (barreira: F2 define o nome/semântica da chave)
WAVE 1   F2 (resolver genérico de modos)                            ← 🔴 sozinha [bloqueia F3..F7]
            │
WAVE 2   F3 · F4 · F5 · F6 · F7                                     ← 🟢🟢🟢🟢🟢 em paralelo
            │
WAVE 3   F8 (docs + verificação final)                              ← 🔴
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | F0 | testes | 🟢 | baixo | goldens novos verdes travando o comportamento atual; veredito de B2 escrito |
| 0 | F1 | frontend | 🟢 | baixo | campos aparecem com a IA do canal desligada |
| 1 | F2 | backend | 🔴 | médio | `image_transcription_mode` resolve com queda no bool; suíte verde |
| 2 | F3 | backend | 🟢 | médio | imagem do operador descrita; B1 corrigido `[depende de: F2]` |
| 2 | F4 | backend | 🟢 | médio | imagem do eco descrita, uma vez só `[depende de: F2, F0]` |
| 2 | F5 | backend | 🟢 | baixo | nota privada de imagem descrita `[depende de: F2]` |
| 2 | F6 | backend | 🟢 | baixo | sandbox pelo helper `[depende de: F2]` |
| 2 | F7 | frontend | 🟢 | baixo | 3 checkboxes de imagem + `node --test` `[depende de: F2]` |
| 3 | F8 | docs | 🔴 | baixo | CLAUDE.md atualizado; checklist §7 fechado |

---

### Fase F0 — Caracterizar antes de tocar 🟢

**Objetivo:** transformar em teste as duas afirmações centrais deste plano, e **medir** B2.

**Itens** (todos `[paralelo]`):
1. Teste: canal com `ai_enabled=False` + `auto_reply=False` → imagem de entrada **é** descrita e o card
   `transcription` aparece. Molde: `_media_case` em
   [test_webhook_characterization.py:257-277](../tests/integration/characterization/test_webhook_characterization.py#L257)
   (que hoje já roda com `auto_reply: False`) + golden novo.
2. Teste: imagem do operador (`POST /api/contacts/{phone}/send-image`) **não** é descrita hoje — trava a
   linha de base que o F3 vai mudar.
3. Teste: eco `is_from_me` com imagem **não** é descrito hoje. Molde: `test_echo_audio_transcription`
   ([linha 556](../tests/integration/characterization/test_webhook_characterization.py#L556) + golden
   `tests/goldens/echo_audio_transcription.json`).
4. **Medir B2** `[sequencial dentro da fase]`: enviar imagem sem legenda pelo painel, depois postar o
   webhook `is_from_me` com **o mesmo `msg_id`** que a rota devolveu; contar linhas `assistant`. Uma
   linha ⇒ B2 é inerte; duas ⇒ B2 é real e o F4 tem de alinhar a chave antes de ligar a descrição.

**Pronto quando:** os 3 goldens novos verdes em `venv/bin/python -m pytest tests/integration/characterization`
e o veredito de B2 registrado abaixo (com o número de linhas observado).

#### Status de execução — Fase F0
**Estado:** ✅ Concluída (2026-08-18)
- **O que foi feito:** [tests/integration/test_media_transcription_directions.py](../tests/integration/test_media_transcription_directions.py) — 11 testes cobrindo os 3 itens de caracterização e os comportamentos novos (o arquivo serve F0 e F3–F5 juntos). O item 4 (medir B2) foi respondido por leitura + teste JÁ EXISTENTE.
- **Como foi feito / decisões:** os itens 2 e 3 (imagem do operador / do eco NÃO descritas hoje) foram escritos **direto na forma pós-mudança** em vez de como golden da linha de base: as duas pontas do gate (`sent` marcado ⇒ descreve · desmarcado ⇒ **zero** chamadas a `describe_image`, com spy de contagem) travam a mesma coisa que o golden travaria, sem gravar um golden que o F3/F4 apagaria no mesmo dia. Nenhum golden novo foi criado — os goldens de mídia existentes seguem intactos, que é a evidência de que F2 não mudou comportamento.
- **Problemas / pendências:** **B2 já estava consertado** — os dois produtores (`messaging_service.py:361` e `contacts.py:1491`) já gravam `f"{channel_id}:{msg_id}"`, exatamente a chave que `_ingest_echo` lê. O plano apontava `contacts.py:1459` gravando a chave crua; essa linha não existe mais. Há teste dedicado: [tests/integration/test_outbound_echo_dedup.py](../tests/integration/test_outbound_echo_dedup.py) (`test_media_send_registers_the_canonical_echo_key`, que inclusive assere que a chave crua **não** aparece sozinha). **Veredito de B2: inerte — nada a consertar, F4 item 4 NÃO executado**, conforme o plano manda registrar. O caso "descrito duas vezes" ganhou teste próprio mesmo assim (`test_eco_da_imagem_que_o_painel_enviou_nao_e_descrito_de_novo`): 1 chamada de visão, 1 card.
- **Verificação:** `venv/bin/python -m pytest tests/integration/test_media_transcription_directions.py tests/integration/test_outbound_echo_dedup.py tests/integration/characterization -q` — ver F8.

---

### Fase F1 — Os campos deixam de depender da IA do canal 🟢

**Objetivo:** entregar a D2 isolada, sem esperar nada do backend (é a dor imediata do usuário).

**Itens:**
1. `[sequencial]` Em [AiSettingsFields.js](../web/static/js/components/channels/AiSettingsFields.js):
   fechar o ramo `${aiOn ? …}` **antes** de "Descrever imagem" ([linha 81](../web/static/js/components/channels/AiSettingsFields.js#L81))
   e reabri-lo **depois** do bloco de áudio ([linha 143](../web/static/js/components/channels/AiSettingsFields.js#L143)),
   de modo que o grupo *Transcrição de mídia* fique fora — vizinho do "Atendente padrão", que já vive
   fora pelo mesmo motivo. `default_ai_enabled`, `group_reply_mode`, contexto, sequencial e mensagens
   picadas **continuam dentro** (são sobre a IA responder).
2. `[paralelo]` Rótulo do grupo: "Transcrição de mídia" + nota curta —
   *"Vale mesmo com a IA desligada. Cada transcrição/descrição consome crédito do LLM."*
3. `[paralelo]` Modo escuro: usar `wa-*`/`.wa-field` (o bloco já usa; só não regredir ao mover).

**Pronto quando:** abrir `/channels`, editar um canal com "Ativar a IA neste canal" **desmarcado** →
"Descrever imagem", "Ler documento" e "Transcrição de áudio" aparecem e persistem após Salvar + F5
(reproduz o print nº 2 do usuário, agora com os campos). Legível no tema escuro.

#### Status de execução — Fase F1
**Estado:** ✅ Concluída (2026-08-18)
- **O que foi feito:** [AiSettingsFields.js](../web/static/js/components/channels/AiSettingsFields.js) — o bloco de transcrição saiu de dentro do `${aiOn ? …}` e virou o grupo **"Transcrição de mídia"** (imagem + documento + áudio numa caixa só), vizinho do "Atendente padrão". `default_ai_enabled`, `group_reply_mode`, contexto, sequencial e mensagens picadas continuam dentro do ramo.
- **Como foi feito / decisões:** o painel de áudio perdeu a borda própria e virou uma sub-seção separada por `border-t` dentro do grupo — encaixar a caixa antiga dentro de outra caixa ficaria pesado. Copy: *"Vale mesmo com a IA deste canal desligada. Cada transcrição/descrição consome crédito do LLM."* (o aviso de custo é o item 2 do plano).
- **Problemas / pendências:** 🐞 **DEFEITO ENTREGUE E CORRIGIDO NO MESMO DIA.** O comentário HTML que eu escrevi para explicar a mudança ficou DENTRO do template do `htm` **com crases** (citando `maybe_transcribe`/`ai_enabled`). Crase fecha o template: o componente passou a lançar `html(...) is not a function` ao renderizar e, como o Preact não monta a subárvore de um componente que lança, **a tela de Canais mudava a URL e o modal nunca abria** — o usuário relatou "sistema completamente travado". Corrigido reescrevendo o comentário sem crase. Salvar com a IA off continua não apagando chave nenhuma (§2.4 confirmado: `buildEditPayload` grava `ai` inteiro).
- **Verificação:** ⚠️ `node --input-type=module --check` **PASSOU no arquivo quebrado** (falso negativo comprovado nesta sessão: com número PAR de crases o arquivo continua sintaticamente válido). O que pega é (a) importar o módulo e RENDERIZAR o componente — feito com shims ESM para as libs vendorizadas, nos 3 estados (IA ligada, IA desligada, canal legado só com booleano), e (b) `node --test web/static/js/services/htmTemplates.test.js`, que eu **não havia rodado** e que também tinha um furo: o `htmBlocks` só inspeciona de cada `html` até a crase seguinte, então o comentário entre dois templates aninhados escapava. Acrescentei ali uma **segunda rede** textual (nenhum comentário HTML pode conter crase) — ela reprova o arquivo defeituoso e passa na árvore limpa. Hoje: `services/*.test.js` **452 passed**, `constants.test.js` **28 passed**, cadeia inteira da tela de Canais importando OK (incluindo `ChannelsManager.js`).

---

### Fase F2 — Resolver de modos genérico por `media_kind` 🔴 [bloqueia F3–F7]

**Objetivo:** um só lugar decide "quais direções deste tipo de mídia são transcritas", com queda no
legado.

**Itens** (ordem importa):
1. `[sequencial]` [transcription.py:35-60](../server/transcription.py#L35): renomear a lógica para
   `parse_media_modes(raw)` e manter `parse_audio_modes = parse_media_modes` como alias exportado
   (importado por [messaging_service.py](../app/services/messaging_service.py) e pela suíte).
2. `[sequencial]` Nova função `modes_for(settings, media_kind) -> set[str]` com a escada da §4.1
   (mode → bool legado → default). É ela que o gate passa a chamar.
3. `[sequencial]` [transcription.py:151-166](../server/transcription.py#L151): o ramo `image` passa a
   usar `modes_for` + a mesma classificação de `source` que o áudio já faz nas
   [linhas 155-160](../server/transcription.py#L155) (`echo`/`operator` → `sent`; `private` → `private`;
   resto → `received`). Extrair essa classificação para `direction_of(source)` para não duplicar.
   ⚠️ `document` fica **no bool** nesta entrega (P1) — o `modes_for` já o atende quando for a hora.
4. `[paralelo]` [ai_settings.py:32](../channels/ai_settings.py#L32): acrescentar
   `"image_transcription_mode"` a `PER_CHANNEL_AI_KEYS` — **sem isso o override do canal é ignorado em
   silêncio** (§2.3).
5. `[paralelo]` [config/settings.py:159](../config/settings.py#L159): `ConfigKey("image_transcription_mode",
   default="received", exposed=True, writable=True)`, ao lado do `audio_transcription_mode`. Manter
   `image_transcription_enabled` declarada (é o fallback legado).
6. `[paralelo]` Testes unitários de `modes_for`: mode presente vence · mode ausente + bool `True` →
   `{received}` · bool `False` → `∅` · nada → `{received}` · lixo → `∅`.

**Pronto quando:** os testes novos verdes, os goldens do F0 **inalterados** (nenhum comportamento mudou
ainda) e `venv/bin/python -m pytest` verde.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída (2026-08-18)
- **O que foi feito:** [server/transcription.py](../server/transcription.py) ganhou `parse_media_modes` (com `parse_audio_modes` mantido como alias do MESMO callable), `direction_of(source)` e `modes_for(settings, media_kind)`; o gate passou a ser `direction_of(source) in modes_for(settings, kind)` para áudio **e** imagem, com `document` seguindo no booleano (P1). `image_transcription_mode` entrou em `PER_CHANNEL_AI_KEYS` ([channels/ai_settings.py](../channels/ai_settings.py)) e como `ConfigKey` ([config/settings.py](../config/settings.py)).
- **Como foi feito / decisões:** **dois desvios deliberados do texto do plano**, os dois pela D3 (zero regressão):
  1. a `ConfigKey` **não é seedada** (`_NO_SEED`, com `get_default=None`) em vez de `default="received"` como o item 5 dizia. Seedar o mode no global faria uma instalação que desligou a descrição no global (`image_transcription_enabled=False`) **voltar a descrever**, porque o mode vence o bool na escada;
  2. `ChannelSettingsView.overridden_keys()` (novo) + o "passo 0" de `modes_for`: um canal que só carrega o booleano legado não pode ser vencido por um mode que existe apenas no GLOBAL. Sem isso a mesma escada erraria no escopo do canal — e o merge do formulário (`{...defaults, ...cfg.ai}`) ligaria sozinha a caixa que o operador tinha desmarcado.
- **Problemas / pendências:** nenhuma. `_AUDIO_MODE_TOKENS` continua exportado (alias de `_MODE_TOKENS`).
- **Verificação:** [tests/core/test_transcription_modes.py](../tests/core/test_transcription_modes.py) — 11 testes (mode vence · bool legado · nada · lixo · alias · `direction_of` · os 3 casos de escopo canal×global). Verde.

---

### Fase F3 — Imagem enviada pelo operador (painel) 🟢 [depende de: F2]

**Objetivo:** entregar o caso literal do pedido ("imagens enviadas por um usuário do whatsbot") e
corrigir B1 na mesma linha.

**Itens:**
1. `[sequencial]` [messaging_service.py:243-252](../app/services/messaging_service.py#L243): trocar
   `transcribe_audio: bool` por um gancho por tipo (`transcribe: bool = False`, aplicado ao `kind` que
   chegou). As rotas de imagem ([contacts.py:2012-2020](../server/routes/contacts.py#L2012)) e de áudio
   ([contacts.py:2105-2111](../server/routes/contacts.py#L2105)) passam `transcribe=True`;
   documento/vídeo seguem sem.
2. `[sequencial]` **B1**: [messaging_service.py:350-356](../app/services/messaging_service.py#L350) passa
   a usar `self.maybe_transcribe(..., channel_id=channel_id)` (o wrapper das
   [linhas 775-802](../app/services/messaging_service.py#L775)), que já sobrepõe o config do canal —
   em vez de `maybe_transcribe(..., settings=self.settings)`. Vale para áudio **e** imagem.
3. `[paralelo]` A descrição vira card privado `role="transcription"` + broadcast `new_message`, no molde
   das [linhas 357-367](../app/services/messaging_service.py#L357).
4. `[paralelo]` Sandbox (`is_sandbox=True`) descreve normalmente — nada vai ao provedor mesmo.

**Pronto quando:** com `image_transcription_mode` do canal contendo `sent`, enviar imagem pelo painel →
card de descrição aparece; sem `sent`, nenhuma chamada a `describe_image` (spy com contagem, molde do
`test_echo_audio_transcription`). Teste novo para B1: canal com `audio_transcription_mode` sem `sent` e
**global com** `sent` → o áudio do operador **não** é transcrito.

#### Status de execução — Fase F3
**Estado:** ✅ Concluída (2026-08-18)
- **O que foi feito:** `send_media` ([messaging_service.py](../app/services/messaging_service.py)) trocou `transcribe_audio: bool` por `transcribe: bool`, aplicado ao `kind` que chegou; `/send-image` passou a mandar `transcribe=True` ([contacts.py](../server/routes/contacts.py)). **B1 corrigido na mesma linha**: a cauda usa `self.maybe_transcribe(..., channel_id=channel_id)` (wrapper per-canal) no lugar de `maybe_transcribe(..., settings=self.settings)` (config global).
- **Como foi feito / decisões:** a descrição sai como card privado `role="transcription"` + `new_message`, reusando o bloco que já existia para o áudio (nada duplicado). Sandbox descreve normalmente (nada vai ao provedor). Documento e vídeo seguem sem `transcribe`.
- **Problemas / pendências:** `transcribe_audio` tinha **um** call site (a rota de áudio) e nenhum uso em teste, então a renomeação não deixou compatibilidade para trás.
- **Verificação:** `test_imagem_do_operador_descrita_quando_enviadas_marcado` · `..._nao_descrita_sem_enviadas` · `test_canal_legado_com_booleano_...` · e os dois de B1 (`test_audio_do_operador_honra_o_canal_e_nao_o_global`, com o global mandando transcrever e o canal não).

---

### Fase F4 — Imagem no eco do próprio celular 🟢 [depende de: F2, F0]

**Objetivo:** a imagem que o atendente manda pelo WhatsApp do celular (fora do painel) também é descrita.

**Itens:**
1. `[sequencial]` [message_ingest_service.py:269](../app/services/message_ingest_service.py#L269): parar
   de descartar o `_img`.
2. `[sequencial]` [message_ingest_service.py:320-332](../app/services/message_ingest_service.py#L320):
   ramo `elif image_path:` chamando `messaging.maybe_transcribe("image", …, source="echo",
   channel_id=channel_id)`; resultado → card privado (não usar `deliver_audio_transcription`, que pode
   mandar ao chat).
3. `[sequencial]` Atualizar o docstring da [linha 255](../app/services/message_ingest_service.py#L255)
   ("Outgoing-audio transcription is a follow-up") — deixou de ser verdade.
4. `[condicional]` Se o F0 comprovou **B2**, alinhar a chave: quem marca como processado
   ([messaging_service.py:311-312](../app/services/messaging_service.py#L311) e
   [contacts.py:1459](../server/routes/contacts.py#L1459)) grava `f"{channel_id}:{msg_id}"`, igual ao
   que o ingest lê. ⚠️ **Mudança em supressão de eco** — commit próprio, com o teste do F0 como prova
   antes/depois. Se B2 for inerte, **não mexer** e registrar isso no status.

**Pronto quando:** eco de imagem com `sent` marcado → exatamente **um** card de descrição e **uma**
linha `assistant` (golden novo `echo_image_description`); com `sent` desmarcado → nenhum.

#### Status de execução — Fase F4
**Estado:** ✅ Concluída (2026-08-18)
- **O que foi feito:** [message_ingest_service.py](../app/services/message_ingest_service.py) — o `_img` deixou de ser descartado (`media_path, image_path, audio_path = …`) e ganhou o ramo `elif image_path:` com `source="echo"`; docstring corrigido (o "Outgoing-audio transcription is a follow-up" deixou de ser verdade).
- **Como foi feito / decisões:** card privado direto, **não** `deliver_audio_transcription` — com `target=chat` aquele helper mandaria a descrição ao contato, virando `message.sent` → eco → nova descrição (§4.2).
- **Problemas / pendências:** o item 4 (alinhar a chave de dedup) **não foi executado, e de propósito**: B2 é inerte, já consertado antes deste plano (ver F0). Nenhuma linha de supressão de eco foi tocada.
- **Verificação:** `test_eco_de_imagem_descrito_uma_vez_quando_enviadas_marcado` (1 card, 1 linha `assistant`, nenhum envio) · `test_eco_de_imagem_nao_descrito_sem_enviadas` · `test_eco_da_imagem_que_o_painel_enviou_nao_e_descrito_de_novo` (a cobrança acontece uma vez só).

---

### Fase F5 — Imagem como nota privada 🟢 [depende de: F2]

**Objetivo:** fechar a terceira direção (`private`), a que o áudio já tem.

**Itens:**
1. `[sequencial]` [contacts.py:1795-1852](../server/routes/contacts.py#L1795) (`_save_private_media`,
   `kind == "image"`): após persistir a nota, chamar `messaging.maybe_transcribe("image", …,
   source="private", channel_id=resolved_channel)` e, havendo texto, gravar/emitir o card
   `transcription` — molde do `/private-audio` ([contacts.py:1686-1704](../server/routes/contacts.py#L1686)).
2. `[paralelo]` **Não** replicar aqui o `ai_read`/`force` do áudio: `/private-image`
   ([contacts.py:1856-1873](../server/routes/contacts.py#L1856)) não tem esse toggle. Vira P4.

**Pronto quando:** com `private` marcado, colar imagem como nota privada → card de descrição; sem
`private` → nada. A nota privada **nunca** vai ao contato (invariante do `_save_private_media`).

#### Status de execução — Fase F5
**Estado:** ✅ Concluída (2026-08-18)
- **O que foi feito:** `_save_private_media` ([contacts.py](../server/routes/contacts.py)) descreve a imagem com `source="private"` depois de registrar as menções, e emite o card `transcription`.
- **Como foi feito / decisões:** só o card — `ai_read`/`force` **não** foram replicados (P4): `/private-image` não tem esse toggle. O `db_content` da nota continua sendo a legenda/`[Imagem]` (ao contrário do `/private-audio`, onde a transcrição VIRA o conteúdo para a IA lê-lo): sem "IA lê" aqui, reescrever o conteúdo só poluiria o contexto.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `test_imagem_em_nota_privada_descrita_quando_privadas_marcado` (+ assere que nada foi ao contato) e `..._nao_descrita_sem_privadas`.

---

### Fase F6 — Sandbox pelo mesmo helper 🟢 [depende de: F2]

**Objetivo:** a tela de teste não pode divergir do que o canal faz (é onde o usuário valida a config).

**Itens:**
1. `[sequencial]` [sandbox.py:211](../server/routes/sandbox.py#L211): trocar
   `if settings.get("image_transcription_enabled", True)` + `describe_image` direto pelo
   `maybe_transcribe` compartilhado — assim o sandbox também honra `filter.transcription.*`, que hoje
   ele ignora.
2. `[paralelo]` Mesma troca no documento ([sandbox.py:343](../server/routes/sandbox.py#L343)), que tem o
   defeito gêmeo.

**Pronto quando:** sandbox com imagem continua descrevendo (nenhum golden do sandbox muda) e um plugin
que registre `filter.transcription.should_run` retornando `False` passa a barrar também o sandbox.

#### Status de execução — Fase F6
**Estado:** ✅ Concluída (2026-08-18)
- **O que foi feito:** [sandbox.py](../server/routes/sandbox.py) — imagem e documento passaram a chamar o `maybe_transcribe` compartilhado (`source="batch"`), no lugar de `describe_image`/`transcribe_document` diretos atrás de um `if settings.get(...)`.
- **Como foi feito / decisões:** usa o helper de MÓDULO com o `settings` global (o sandbox não é escopado a canal — ele carimba `primary_channel_id()`), o que preserva o gate de hoje pela escada e acrescenta os hooks de plugin. ⚠️ O **áudio do sandbox ficou como estava**: ele hoje transcreve SEM gate nenhum, e roteá-lo pelo helper acrescentaria um gate que ninguém pediu (mudança de comportamento fora do escopo do plano, que lista só imagem e documento).
- **Problemas / pendências:** o áudio do sandbox segue divergente do canal real — vale um P6 se incomodar.
- **Verificação:** os goldens do sandbox não mudaram (suíte de caracterização).

---

### Fase F7 — O multi-select de imagem na UI 🟢 [depende de: F2]

**Objetivo:** o formulário do canal oferece as três direções da imagem, exatamente como o áudio.

**Itens:**
1. `[sequencial]` [constants.js:88-99](../web/static/js/components/channels/constants.js#L88):
   generalizar `parseAudioModes`/`serializeAudioModes` (nomes atuais mantidos como alias — são usados em
   [AiSettingsFields.js:7](../web/static/js/components/channels/AiSettingsFields.js#L7)) e espelhar a
   escada mode→bool legado do backend, para UI e gate nunca discordarem.
2. `[sequencial]` [constants.js:62-79](../web/static/js/components/channels/constants.js#L62)
   (`aiDefaultsFrom`): semear `image_transcription_mode` a partir do global, mantendo
   `image_transcription_enabled` no objeto (canal novo não pode nascer sem o fallback).
3. `[sequencial]` [AiSettingsFields.js:81-93](../web/static/js/components/channels/AiSettingsFields.js#L81):
   "Descrever imagem" vira um bloco com 3 checkboxes (Recebidas / Enviadas / Privadas), no mesmo desenho
   visual do bloco de áudio ([linhas 95-143](../web/static/js/components/channels/AiSettingsFields.js#L95)),
   com o aviso "Nenhuma marcada — descrição desativada". **Sem** o seletor "Onde aparece" (§4.2).
4. `[paralelo]` `node --test web/static/js/components/channels/constants.test.js` com casos novos de
   parse/serialize e de `buildEditPayload` carregando a chave nova.

**Pronto quando:** marcar "Enviadas" na imagem, salvar, recarregar → persistiu; `node --test` verde;
legível no tema escuro.

#### Status de execução — Fase F7
**Estado:** ✅ Concluída (2026-08-18)
- **O que foi feito:** [constants.js](../web/static/js/components/channels/constants.js) — `parseMediaModes`/`serializeMediaModes` (com `parseAudioModes`/`serializeAudioModes` como alias), `mediaModesFrom(ai, kind)` (a escada, espelhando `modes_for`), `withResolvedMediaModes(channelAi)` e o seed de `image_transcription_mode` em `aiDefaultsFrom`. [ChannelEditForm.js](../web/static/js/components/channels/ChannelEditForm.js) resolve o legado do canal ANTES do merge. [AiSettingsFields.js](../web/static/js/components/channels/AiSettingsFields.js) — "Descrever imagem" virou 3 caixas, com o aviso "Nenhuma marcada — descrição desativada" e **sem** o seletor "Onde aparece".
- **Como foi feito / decisões:** `withResolvedMediaModes` é o item que o plano não previa e sem o qual F7 seria uma regressão silenciosa: o estado do formulário nasce `{...aiDefaults, ...cfg.ai}`, então um canal com `image_transcription_enabled:false` (e sem mode) receberia o mode `received` dos defaults e **religaria a descrição no primeiro Salvar**. O toggle também mantém o booleano legado em sincronia com a direção "Recebidas", para um downgrade de core continuar lendo a intenção do operador.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --test web/static/js/components/channels/constants.test.js` — **28 testes, 0 falhas** (8 novos: parse/serialize, aliases, escada, os dois casos de merge, seed do `aiDefaultsFrom`, `buildEditPayload` carregando a chave).

---

### Fase F8 — Documentação e fechamento 🔴

**Itens:**
1. `[sequencial]` `CLAUDE.md`: atualizar a linha de `filter.transcription.should_run` (o `source` real é
   `{batch, echo, operator, private}` e o `media_kind` passa a valer direção também para imagem) e a
   lista `PER_CHANNEL_AI_KEYS`.
2. `[paralelo]` Registrar em `docs/PLUGIN_API_CHANGELOG.md` **se** P2 decidir que vale a nota (o golden
   não muda; ver §3.3).
3. `[paralelo]` Preencher o §7 e o resultado de B2.

#### Status de execução — Fase F8
**Estado:** ✅ Concluída (2026-08-18)
- **O que foi feito:** [CLAUDE.md](../CLAUDE.md) — a linha de `filter.transcription.should_run` (o `source` real é `{batch, echo, operator, private}`; `media_kind` inclui `document`; o sandbox passou a ser coberto), a lista `PER_CHANNEL_AI_KEYS` e **dois marcadores novos** na seção "Configuração de IA por canal" (transcrição não depende da IA; a escada de direções, com os dois avisos de armadilha). `WHATSBOT_API_VERSION` **1.5.0 → 1.6.0** + entrada no topo de [docs/PLUGIN_API_CHANGELOG.md](../docs/PLUGIN_API_CHANGELOG.md) + snapshot regenerado. §7 e §8 preenchidos.
- **Como foi feito / decisões:** P2 fechado em **(b) com MINOR**, não PATCH — justificativa na própria pergunta. O `source` documentado no CLAUDE.md listava `group_no_mention`, que **nunca teve call site** (§3.3): a linha foi corrigida para os quatro valores reais em vez de propagar a ficção.
- **Problemas / pendências:** a **falha pré-existente** `test_audit_characterization.py::test_audit_matrix_is_complete` continua vermelha (a matriz não cobre `channel.*` nem `plugin.imported/deleted`) — não é deste plano: ela compara `db/audit_actions.py` com um literal do teste, e nenhum dos dois foi tocado aqui.
- **Verificação:** `venv/bin/python -m pytest tests/core/test_transcription_modes.py tests/integration/test_media_transcription_directions.py tests/integration/test_outbound_echo_dedup.py tests/integration/characterization` ⇒ **1 failed (a pré-existente acima), 129 passed, 2 skipped** em 11m20s. `node --test .../constants.test.js` ⇒ **28/28**. `pytest tests/contracts/test_plugin_api_surface.py` ⇒ **5/5**.
  ⚠️ **Nota de ambiente (custou 3 execuções inválidas):** o banco compartilhado `whatsbot_test` estava sendo usado ao mesmo tempo por **outra máquina** (`10.8.200.103`; esta é a `.102`), e o reset de schema dela derrubou duas execuções no meio — uma delas morreu dentro do próprio `_engine_ready` (`repair_postgres_sequences` achou `observations` inexistente logo após o Alembic criá-la), cascateando 118 erros de setup. A execução válida rodou num banco **privado** (`whatsbot_test_p118`, UTF8/template0), que ficou criado para as próximas. Antes disso eu mesmo cometi o erro clássico de disparar dois pytest no mesmo banco — eles travaram um no `DROP SCHEMA` do outro.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Chave nova fora de `PER_CHANNEL_AI_KEYS` | override do canal **silenciosamente ignorado** ([ai_settings.py:96-115](../channels/ai_settings.py#L96)) | item 4 do F2 + teste que salva no canal e lê pelo `view` |
| Custo | descrever toda imagem enviada dobra/triplica chamadas de visão | `sent`/`private` **desmarcados** por padrão (§4.3) + aviso na UI (F1) |
| Eco duplicado (B2) | imagem do operador descrita e cobrada 2× | F0 mede antes; F4 só mexe se confirmado, em commit próprio |
| Loop de mídia | descrição indo ao chat viraria `message.sent` → eco → nova descrição | imagem **sempre** card privado; "Onde aparece" segue só no áudio (§4.2) |
| Cache de 30s do `ai_settings` | marcar a caixa e testar na hora "não funciona" | o PUT do canal já reseta o cache — confirmar que o caminho novo passa pelo mesmo `reset_cache` |
| Regressão de golden | goldens de mídia existentes cobrem imagem de entrada | F2 não muda comportamento; qualquer golden que mude sem F3–F5 envolvida é bug, não "atualiza o golden" |
| Modo escuro | bloco novo com cor crua | `wa-*` / `.wa-field`, conferido com o tema escuro ligado |
| Provider sem imagem | canal que não entrega `image_path` | ramo `elif image_path:` — ausente ⇒ no-op, como o áudio |

---

## 7. Perguntas em aberto

**P1 — "Ler documento" também ganha direções?**
✅ RESOLVIDO em (b) — **não** entrou. `modes_for` já é genérico (`modes_for(settings, "document")`
funciona hoje), então acrescentar depois é a `ConfigKey` + a entrada em `PER_CHANNEL_AI_KEYS` +
3 caixas. O ramo do documento no gate segue lendo o booleano, com comentário apontando para cá.
Contexto original: O pedido citou imagem e áudio. `modes_for` já nasce genérico, então acrescentar
`document_transcription_mode` depois é uma linha + 3 checkboxes.
(a) fazer junto · (b) deixar para quando pedirem. **Recomendação: (b)** — cada direção nova é custo de
LLM que ninguém pediu.

**P2 — Entrada no `PLUGIN_API_CHANGELOG.md`?**
✅ RESOLVIDO em (b), porém como **MINOR (1.5.0 → 1.6.0)**, não como PATCH. Motivo: a tabela "Como
bumpar" do próprio changelog classifica como MINOR "ampliar o conjunto de situações em que [um seam]
é emitido", que é exatamente o que aconteceu (`media_kind="image"` passou a ocorrer com
`source="operator"/"echo"/"private"`, e o sandbox passou a consultar o filtro). PATCH é reservado a
correção que não muda a forma. Além disso só um bump permite que um plugin EXIJA o alcance novo
(`">=1.6,<2.0"`). O snapshot foi regenerado e o diff é **só a linha da versão** — prova de que a
superfície em si não mexeu. Contexto original: Nenhum nome/campo/tipo do catálogo muda e `plugin_api_surface.json` não mexe (§3.3),
então **não há bump obrigatório**. (a) só CLAUDE.md · (b) CLAUDE.md + nota PATCH no changelog.
**Recomendação: (b)** — um plugin que filtre por `source` passa a ver combinações novas; é barato avisar.

**P3 — Grupo sem @menção nunca transcreve nada.**
⏸️ ADIADO — lacuna antiga (§3.3), fora do pedido. Vira plano próprio se incomodar (áudio de grupo sem
menção hoje entra no histórico como `[Áudio recebido]`).

**P4 — Nota privada de imagem com "IA lê"?**
⏸️ ADIADO. O `/private-audio` tem `ai_read` + `force`; o `/private-image` não tem o toggle na UI. F5
entrega só o card; se a IA precisar **ler** a imagem privada, é feature separada.

**P5 — Confirmação de escopo com o usuário:** ⏸️ NÃO confirmada antes da WAVE 2 — a D1 entrega o
**superconjunto** (painel + celular + nota privada), então nenhuma das duas leituras fica de fora e
nada precisa ser desfeito se a intenção era mais estreita; o operador simplesmente deixa a direção
desmarcada. Registrado aqui porque o plano pedia a confirmação.
Contexto original: "imagens enviadas por um usuário do whatsbot" foi lido
como **operador** (painel + celular + nota privada), que é a leitura casada com o *"igual o áudio"*.
A D1 entrega o superconjunto — se a intenção era só "imagem recebida do cliente", isso **já funciona**
hoje e o F1 sozinho resolve. Vale confirmar antes da WAVE 2 (F1 e F0 podem ir de qualquer forma).

---

## 8. Checklist de verificação

- [x] `venv/bin/python -m pytest tests/integration/characterization` (+ os arquivos novos) — **129 passed, 1 failed pré-existente, 2 skipped**; nenhum golden precisou ser regravado
- [x] `node --test web/static/js/components/channels/constants.test.js` — **28/28**
- [x] `pytest tests/contracts/test_plugin_api_surface.py` — **5/5**, com o diff do snapshot restrito à linha da versão
- [x] Imagem recebida do cliente continua descrita exatamente como antes (D3) — os goldens `media_image_transcription_on*` passam intocados
- [x] Imagem do operador: descrita com `sent` marcado, **nenhuma** chamada sem (spy de contagem)
- [x] Eco de imagem: **um** card, **uma** linha `assistant`
- [x] Nota privada de imagem: descrita com `private` marcado; nunca enviada ao contato
- [x] Sandbox: imagem/documento seguem descrevendo, agora honrando `filter.transcription.*`
- [x] Sem migration, sem DDL, sem segredo em log/URL
- [x] CLAUDE.md atualizado (F8)
- [x] `venv/bin/python -m pytest` (suíte INTEIRA, incluindo a legada) no banco privado — **750 passed, 4 failed, 4 skipped** (16m52s). As 4 falhas são **pré-existentes e alheias a este plano**: `test_alembic_hygiene` ×2 (revisão de merge + prefixos duplicados), `test_audit_matrix_is_complete` (matriz atrás dos eventos `channel.*`) e a suíte legada — que passa 1641 e falha 6 checks **todos do bloco do plugin `protocolos`** (`attr match …`, `skip_attrs round-trip`, `sanitize aceita scope protocolo`). Esta última é descasamento de versão do plugin INSTALADO: o teste do core monta condição no schema 2.0.0 (`values: [...]`) e `storages/plugins/protocolos` está na **1.34.0**, cujo `_condition_is_active` lê `cond["value"]` (singular) ⇒ condição inativa ⇒ `_rule_matches` False. Nada disso toca transcrição/mídia
- [x] **Validação manual no navegador (2026-08-18)** — feita pelo usuário depois da correção do defeito de F1: *"Aparentemente tudo funcionando nessa parte."* Cobre canal com IA **desligada** mostrando os campos (o print nº 2 do usuário) e a tela de Canais voltando a abrir o modal.

---

## 9. Apêndice — arquivos que o executor vai tocar

**Backend (core)**
- [server/transcription.py](../server/transcription.py) — F2 (gate + `parse_media_modes` + `modes_for` + `direction_of`)
- [channels/ai_settings.py](../channels/ai_settings.py) — F2 (`PER_CHANNEL_AI_KEYS`)
- [config/settings.py](../config/settings.py) — F2 (`ConfigKey`)
- [app/services/messaging_service.py](../app/services/messaging_service.py) — F3 (`send_media`, B1)
- [app/services/message_ingest_service.py](../app/services/message_ingest_service.py) — F4 (eco), e a chave de dedup se B2 se confirmar
- [server/routes/contacts.py](../server/routes/contacts.py) — F3 (rotas de envio) · F5 (`_save_private_media`) · F4 condicional
- [server/routes/sandbox.py](../server/routes/sandbox.py) — F6

**Frontend**
- [web/static/js/components/channels/AiSettingsFields.js](../web/static/js/components/channels/AiSettingsFields.js) — F1 + F7
- [web/static/js/components/channels/constants.js](../web/static/js/components/channels/constants.js) — F7
- [web/static/js/components/channels/constants.test.js](../web/static/js/components/channels/constants.test.js) — F7

**Testes**
- [tests/integration/characterization/test_webhook_characterization.py](../tests/integration/characterization/test_webhook_characterization.py) — F0/F3/F4
- `tests/goldens/` — goldens novos (imagem com IA off, imagem do operador, eco de imagem)

**Docs**
- [CLAUDE.md](../CLAUDE.md) — F8 · `docs/PLUGIN_API_CHANGELOG.md` — F8 se P2 = (b)
