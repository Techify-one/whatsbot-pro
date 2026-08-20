# Plano 133 — A descrição da imagem para de vazar como mensagem pública do cliente

> **Status:** EXECUTADO (F0–F4) · F5 cancelada por decisão (P1=(a)) · **Data:** 2026-08-20 · **Escopo:** pequeno (backend only, sem migration, sem frontend)
> **Origem:** incidente em produção (conversa 13043, mensagem 681435) — o texto interno `[Descrição da imagem]: …` apareceu no fio como se fosse mensagem do CLIENTE. **Método:** leitura do código real (`arquivo:linha` verificados por `grep`/`sed`) + consulta **somente-leitura** ao banco de produção pelo cofre de credenciais (a identificação da credencial fica fora deste documento — repositório público).
> **O quê/porquê:** o batch de mídia **não cola** a descrição na linha da mídia que acabou de inserir — ele **reprocura** "a última mensagem `role='user'` da conversa" ordenada por `ts DESC` e reescreve o `content` dela, sem exigir que a linha seja mídia. Depois do plano 129 (que passou a gravar o `ts` REAL do provedor) uma mensagem de TEXTO do mesmo lote pode ter `ts` maior que a imagem — e a descrição é gravada nela. Como a linha de texto tem `media_type=NULL`, o painel não esconde o prefixo e o conteúdo interno vira bolha pública. A correção é mirar a linha recém-salva pelo `id` que o próprio `INSERT` devolveu.
>
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0 — Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 ✅ (2026-08-20) | A descrição/transcrição é colada na **linha da mídia recém-inserida**, mirada por **`id`**, nunca por reprocura. | Muda [app/services/messaging_service.py:1308-1312](app/services/messaging_service.py#L1308-L1312) e os 3 sites do sandbox. Elimina a dependência de `ts`/ordem. |
| D2 ✅ (2026-08-20) | ⚠️ A chave do dict de escrita é **`"id"`**, não `"_id"`. | `message_repo.add` devolve `"id"` ([db/repositories/message_repo.py:60-61](db/repositories/message_repo.py#L60-L61)); só o caminho de LEITURA (`_row_to_dict`, [db/repositories/message_repo.py:911](db/repositories/message_repo.py#L911)) expõe `_id`. Usar `saved["_id"]` daria `KeyError`/`None` e mataria a colagem em silêncio. |
| D3 ✅ (2026-08-20) | O formato COMPOSTO do `content` (`[Descrição da imagem]: <desc>\n<legenda>`) **não muda**. | `format_media_content` ([server/transcription.py:123](server/transcription.py#L123)) fica intocado; goldens do plano 87 continuam válidos byte a byte. |
| D4 ✅ (2026-08-20) | O card privado `role="transcription"` **não muda** — já está correto em todos os caminhos. | [app/services/messaging_service.py:1318](app/services/messaging_service.py#L1318) e os equivalentes de echo/operador/nota privada ficam como estão. |
| D5 ✅ (2026-08-20) | `message_repo.get_last_user_message` **não é removida nem tem a semântica alterada**. | Ela tem consumidor externo vivo: `vendas_ia/filters.py:176` (triagem lê o texto do turno). Endurecer com `media_type IS NOT NULL` quebraria esse plugin — e seria paliativo, já que o alvo certo é o `id`. |
| D6 ✅ (2026-08-20) | O painel **não** ganha defesa nova. | Esconder um `role='user'` sem `media_type` que comece com prefixo de IA esconderia texto de cliente. O problema é corrupção de dado, não de renderização. |

**Princípio fixo:** bug de corrupção de dado **em produção**; correção de raiz (mirar a linha certa), sem stopgap de reordenar/filtrar no cliente.

---

## 1 — Resumo executivo

O laço de mídia do batch salva **uma linha por mídia** e guarda o retorno do `INSERT` em `saved` ([app/services/messaging_service.py:1241](app/services/messaging_service.py#L1241)). Quando a descrição/transcrição fica pronta, porém, ele **descarta esse `saved`** e chama `agent_handler.update_last_user_message_content(phone, …)` ([:1308-1312](app/services/messaging_service.py#L1308-L1312)), que faz `get_last_user_message(contact_id, conversation_id)` → `ORDER BY ts DESC LIMIT 1` ([db/repositories/message_repo.py:534-552](db/repositories/message_repo.py#L534-L552)) — **sem exigir `media_type`** — e reescreve o `content` daquela linha ([:555](db/repositories/message_repo.py#L555)).

Enquanto o `ts` era o relógio do INSERT, a mídia (salva por último) sempre vencia e o alvo saía certo por acidente. O **plano 129** (2026-08-18) passou a gravar o `ts` REAL do provedor no inbound ([:1246](app/services/messaging_service.py#L1246) `ts=(item.get("ts") or None)`), e o acaso acabou: **qualquer** linha `role='user'` da mesma conversa com `ts` maior — tipicamente o texto do MESMO lote, salvo antes ([:1112](app/services/messaging_service.py#L1112)) mas com carimbo posterior — passa a ser o alvo. Efeito triplo: (1) o prefixo interno vira **bolha pública**; (2) o **texto original do cliente é destruído** (o `UPDATE` troca o `content` inteiro); (3) a linha da imagem fica **sem** a descrição, então turnos futuros perdem o conteúdo da foto.

A correção é de duas linhas por site: usar `saved["id"]` → `message_repo.update_content(...)`. Ela cobre imagem, áudio e documento de uma vez, e vale igualmente para os 3 sites gêmeos do sandbox.

---

## 2 — Como funciona hoje (mapa verificado)

### 2.1 O laço de mídia do batch (o site do bug)

| Passo | Arquivo:linha | O que faz |
|---|---|---|
| Salva a linha da mídia | [messaging_service.py:1241-1249](app/services/messaging_service.py#L1241-L1249) | `saved = contact.add_message("user", …, media_type=…, media_path=…, ts=item["ts"])` — **`saved` já é a identidade da linha** |
| Transcreve/descreve | [messaging_service.py:1273-1294](app/services/messaging_service.py#L1273-L1294) | `self.maybe_transcribe("audio"/"image"/"document", …)` |
| Monta o `content` composto | [messaging_service.py:1300-1305](app/services/messaging_service.py#L1300-L1305) | `format_media_content(kind, transcription, text)` |
| **Cola no alvo ERRADO** | [messaging_service.py:1308-1312](app/services/messaging_service.py#L1308-L1312) | `agent_handler.update_last_user_message_content(phone, new_content, channel_id)` — ignora `saved` |
| Card privado (correto) | [messaging_service.py:1318](app/services/messaging_service.py#L1318) | `contact.add_message("transcription", transcription)` |
| Texto que a IA lê no turno | [messaging_service.py:1341-1350](app/services/messaging_service.py#L1341-L1350) | `llm_text` é montado **em memória**, independente do banco |

⚠️ O `llm_text` ser independente é o que faz o bug ser **invisível no turno**: a IA respondeu certo; o estrago só aparece no fio e no histórico das próximas conversas.

⚠️ `saved` é **rebindado a cada iteração** do laço (e o mesmo nome é usado pelo batch de texto em [:1112](app/services/messaging_service.py#L1112)). A colagem tem de ficar DENTRO da iteração — mover para fora reintroduz o bug com outra cara.

### 2.2 A reprocura frágil

```
handler.update_last_user_message_content   agent/handler.py:423-437
  └─ conversation_repo.get_open_for_contact_scoped(contact)   # escopo por canal (plano 37 B5) — correto
  └─ message_repo.get_last_user_message(contact_id, conv_id)  # db/repositories/message_repo.py:534
        WHERE role='user' AND conversation_id=…               # ← sem media_type
        ORDER BY ts DESC LIMIT 1                              # ← ts do PROVEDOR desde o plano 129
  └─ message_repo.update_content(msg["_id"], new_content)     # :555 — não toca media_type
```

### 2.3 Os sites gêmeos do sandbox

| Kind | Save | Colagem | Observação |
|---|---|---|---|
| imagem | [sandbox.py:207](server/routes/sandbox.py#L207) | [sandbox.py:224-228](server/routes/sandbox.py#L224-L228) | prefixo montado **inline**, duplicando `format_media_content` |
| áudio | [sandbox.py:274](server/routes/sandbox.py#L274) | [sandbox.py:287-290](server/routes/sandbox.py#L287-L290) | idem |
| documento | [sandbox.py:340](server/routes/sandbox.py#L340) | [sandbox.py:355-359](server/routes/sandbox.py#L355-L359) | idem |

O sandbox usa `ts` de INSERT (não há provedor), então o alvo hoje sai certo — é bug **latente**, não ativo. Nenhum dos 3 sites captura o retorno do `add_message`.

### 2.4 Por que o painel não esconde

[web/static/js/services/messageView.js:213-217](web/static/js/services/messageView.js#L213-L217) lista os prefixos de IA e [`mediaCaptionOf`](web/static/js/services/messageView.js#L248) os corta — mas essa função só é consultada para linha **de mídia**. Linha com `media_type=NULL` é desenhada com o `content` cru. Por isso o vazamento é visível.

### 2.5 Evidência medida em produção (somente leitura, 2026-08-20)

```sql
SELECT id, conversation_id, to_timestamp(ts), left(content,60)
FROM messages
WHERE role='user' AND media_type IS NULL AND content LIKE '[Descrição da imagem]:%';
```

| Métrica | Valor |
|---|---|
| Linhas afetadas | **5**, em **3** conversas (13043, 13045, 1519) |
| Janela | 2026-08-19 13:01 → 2026-08-20 12:17 (**depois** do plano 129, mesclado em 2026-08-18) |
| Áudio (`[Transcrição do áudio]:`) | **0** linhas |
| Documento (`[Conteúdo do documento]:`) | **0** linhas |

Anatomia de um caso (conversa 13045): imagem `678047` com `ts` 13:01:46 e `content` vazio; texto `678046` com `ts` 13:01:47 **carregando a descrição**. Ou seja: **1 segundo** de diferença no relógio do provedor foi o bastante.

⚠️ Não é preciso ser o MESMO lote: basta existir, na conversa, **qualquer** linha `role='user'` com `ts` maior que o da mídia. Dois áudios/imagens no mesmo lote com um texto mais novo colapsam as duas descrições na MESMA linha de texto (a segunda sobrescreve a primeira).

### 2.6 Recuperabilidade do texto destruído

O `UPDATE` apagou o texto original, mas o batch já havia denormalizado a mensagem do cliente em `executions.input_text` (via `aset_execution_texts`, com o `msg_id` da última msg de texto). Join por `msg_id`:

| Linha | `executions.input_text` |
|---|---|
| 681435 | ✅ recuperável verbatim |
| 681937 | ✅ recuperável verbatim |
| 681954 | ✅ recuperável verbatim |
| 678046 | ❌ sem execução correspondente — irrecuperável |
| 678094 | ❌ sem execução correspondente — irrecuperável |

**3 de 5 recuperáveis verbatim**; as outras 2 não têm fonte (nem `execution_steps.batch_accumulated`).

⚠️ O conteúdo das mensagens fica **fora deste documento de propósito** — é texto de conversa de cliente e este repositório é público. Para conferir antes de um eventual reparo, releia `executions.input_text` no banco.

---

## 3 — Inventário das mudanças

| # | Site | Arquivo:linha | O que muda | Risco | Esforço |
|---|---|---|---|---|---|
| M1 | Batch de mídia (o bug ativo) | [messaging_service.py:1308-1312](app/services/messaging_service.py#L1308-L1312) | mirar `saved["id"]` via `message_repo.update_content`; sem `id` ⇒ WARNING e **não cola** (nunca reprocurar) | baixo | S |
| M2 | Sandbox imagem | [sandbox.py:207,224-228](server/routes/sandbox.py#L207) | capturar o retorno do `add_message`; colar por `id`; trocar o prefixo inline por `format_media_content("image", desc, caption)` (string idêntica) | baixo | S |
| M3 | Sandbox áudio | [sandbox.py:274,287-290](server/routes/sandbox.py#L274) | idem, `format_media_content("audio", transcription)` | baixo | S |
| M4 | Sandbox documento | [sandbox.py:340,355-359](server/routes/sandbox.py#L340) | idem, `format_media_content("document", transcription, content)` (mesma ordem texto→prefixo) | baixo | S |
| M5 | Guarda documental | [handler.py:423](agent/handler.py#L423), [message_repo.py:534](db/repositories/message_repo.py#L534) | docstring ⚠️ "não use para colar transcrição de mídia — ver plano 133"; **sem** mudança de assinatura ou de query (D5) | nenhum | S |
| M6 | Reparo de dados (opt-in) | produção | restaurar 3 linhas de `executions.input_text`; decidir o destino das 2 irrecuperáveis (P1) | médio | S |

### 3.1 Falsos positivos descartados

| Suspeito | Arquivo:linha | Por que NÃO é o bug |
|---|---|---|
| Echo (mídia enviada do celular) | [message_ingest_service.py:327-359](app/services/message_ingest_service.py#L327-L359) | Só cria card `transcription` / entrega áudio; **nunca** reescreve `content` de linha nenhuma. |
| Mídia enviada pelo operador | [messaging_service.py:408-424](app/services/messaging_service.py#L408-L424) | Idem — só card privado. |
| Nota privada (áudio/imagem) | [contacts.py:1720-1737](server/routes/contacts.py#L1720-L1737), [:1888-1905](server/routes/contacts.py#L1888-L1905) | Grava a transcrição no `content` da PRÓPRIA nota recém-criada, ou card; não reprocura. |
| Grupo sem @menção | [message_ingest_service.py:553-557](app/services/message_ingest_service.py#L553-L557) | Salva e retorna; não transcreve nada. |
| Endurecer `get_last_user_message` com `media_type IS NOT NULL` | [message_repo.py:534](db/repositories/message_repo.py#L534) | Paliativo E quebra consumidor vivo (`vendas_ia/filters.py:176` lê o texto do turno). Descartado por D5. |
| Esconder o prefixo no painel para linha sem `media_type` | [messageView.js:248](web/static/js/services/messageView.js#L248) | Esconderia texto real de cliente e mascararia corrupção de dado. Descartado por D6. |
| `llm_text` / resposta da IA no turno | [messaging_service.py:1341-1350](app/services/messaging_service.py#L1341-L1350) | Montado em memória — a IA leu a descrição certa mesmo com o bug. Não muda. |

---

## 4 — Fases e paralelização

```
WAVE 0   F0 (reprodução vermelha)                         🔴 sozinha  [bloqueia: F1,F2]
             │
WAVE 1   F1 (batch de mídia) · F2 (sandbox ×3) · F3 (docstrings)   🟢 podem ir juntas
             │  (barreira: F1 precisa estar verde para a F4 valer)
WAVE 2   F4 (goldens + suíte de integração verde)         🔴 sozinha  [depende de: F1,F2]
             │
WAVE 3   F5 (reparo dos dados em produção — opt-in)       🔴 sozinha  [depende de: P1 decidido]
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | F0 | testes | 🔴 | baixo | 2 testes novos VERMELHOS reproduzem o vazamento |
| 1 | F1 | backend/batch | 🟢 | baixo | F0 fica verde; goldens do plano 87 intactos |
| 1 | F2 | backend/sandbox | 🟢 | baixo | os 3 endpoints do sandbox colam por `id` |
| 1 | F3 | documentação inline | 🟢 | nenhum | docstrings avisam do plano 133 |
| 2 | F4 | testes | 🔴 | baixo | suíte de integração + caracterização verdes no Postgres |
| 3 | F5 | dados/produção | 🔴 | médio | as 5 linhas tratadas conforme P1, com backup antes |

---

### Fase F0 — Reprodução (caracterização ANTES de mexer)

**Objetivo:** provar o bug com teste automatizado, sem depender do mesmo lote.

**Itens**
1. `[sequencial]` Criar `tests/integration/test_media_description_target.py`, no molde de [tests/integration/test_inbound_provider_ts_ordering.py](tests/integration/test_inbound_provider_ts_ordering.py) (helpers `_post_gowa`/`_drain`/`_seed_conversation`, driver real `POST /api/webhook/gowa/default`, `message_batch_delay: 0`, `auto_reply: False`).
2. `[sequencial]` **Teste 1 — o caso da produção:** semear um TEXTO com `timestamp = BASE+100`; em seguida postar uma IMAGEM com `timestamp = BASE+50` (entrega atrasada), com `describe_image` mockado (`patch.object(built.agent_handler, "describe_image", return_value=…)`, como em [test_webhook_characterization.py:452](tests/integration/characterization/test_webhook_characterization.py#L452)). Asserções: a linha `media_type='image'` termina com a descrição **e** a linha de texto continua com o texto original. **Vermelho hoje.**
3. `[paralelo]` **Teste 2 — áudio:** mesmo gatilho com `transcribe_audio` mockado e `audio_transcription_mode` incluindo `received`; asserção de que o prefixo `[Transcrição do áudio]:` **não** aparece em linha sem `media_type`.
4. `[paralelo]` **Teste 3 — guarda:** duas mídias no mesmo lote ⇒ cada descrição na SUA linha (hoje colapsam numa só).

**Pronto quando:** os 3 testes rodam e falham **pelo motivo certo** (descrição na linha errada), com `WHATSBOT_TEST_DB_URL=… venv/bin/python -m pytest tests/integration/test_media_description_target.py -q`.

#### Status de execução — Fase F0
**Estado:** ✅ Concluída (2026-08-20)
- **O que foi feito:** criado `tests/integration/test_media_description_target.py` com os 3 testes previstos: `test_image_description_lands_on_image_row`, `test_audio_transcription_lands_on_audio_row`, `test_two_media_each_keep_their_own_description`.
- **Como foi feito / decisões:** driver real `POST /api/webhook/gowa/default` no molde de `test_inbound_provider_ts_ordering.py` (helpers `_drain`/`_new_phone`/`_post`). **Desvio deliberado:** os testes usam **dois POSTs separados** (texto com `ts=BASE+100`, depois mídia com `ts=BASE+50`) em vez de forçar o mesmo lote — o gatilho real não exige lote comum, basta existir na conversa uma linha `role='user'` com `ts` maior, e assim o teste fica determinístico (não depende de como o orquestrador agrupa).
- **Problemas / pendências:** `message_repo._row_to_dict` **omite chaves NULAS**, então `row["media_type"]` numa linha de texto dá `KeyError` em vez da asserção pretendida — todas as leituras do teste passaram a usar `.get()`.
- **Verificação:** 3/3 **VERMELHOS** antes da F1, pelo motivo certo — `content` da linha de TEXTO virava `[Descrição da imagem]: …` / `[Transcrição do áudio]: …`, destruindo o texto do cliente; e no teste das duas imagens a 1ª linha ficava com `content` vazio.

---

### Fase F1 — O batch cola na linha que ele mesmo inseriu 🟢

**Objetivo:** eliminar a reprocura no único site com bug ativo.

**Itens**
1. `[sequencial]` Em [app/services/messaging_service.py:1308-1312](app/services/messaging_service.py#L1308-L1312), trocar a chamada a `agent_handler.update_last_user_message_content` por uma atualização direta da linha da mídia: ler `(saved or {}).get("id")` (⚠️ **`id`**, não `_id` — D2) e chamar `message_repo.update_content(<id>, new_content)` em `asyncio.to_thread`.
2. `[sequencial]` Importar `message_repo` no módulo — hoje [messaging_service.py:39](app/services/messaging_service.py#L39) importa `agent_repo, contact_repo, conversation_repo`; acrescentar `message_repo` na mesma linha.
3. `[sequencial]` Sem `id` (defensivo, não deve acontecer — o `INSERT` sempre devolve a PK): `logger.warning` e **pular** a colagem. Nunca cair de volta na reprocura — é exatamente o bug. O card `transcription` e o `llm_text` do turno seguem intactos.
4. `[sequencial]` Comentário curto no site apontando o plano 133 e o motivo (`ts` do provedor ≠ ordem de inserção desde o plano 129).

**Pronto quando:** F0 fica **verde**; `tests/integration/characterization/test_webhook_characterization.py -k media` continua verde (goldens `media_image_transcription_on*`, `media_audio_transcription_on`, `media_document_transcription_on_com_legenda` **inalterados** — se algum golden mudar, a correção saiu errada).

#### Status de execução — Fase F1
**Estado:** ✅ Concluída (2026-08-20)
- **O que foi feito:** [app/services/messaging_service.py:39](app/services/messaging_service.py#L39) passou a importar `message_repo`; o site da colagem (agora ~`:1308-1336`) troca `agent_handler.update_last_user_message_content` por `message_repo.update_content(saved_id, new_content)` em `asyncio.to_thread`, com `saved_id = (saved or {}).get("id")`.
- **Como foi feito / decisões:** chave **`"id"`** conforme D2. Sem `id` ⇒ `logger.warning` e a colagem é **pulada** — nunca há fallback para reprocura. Comentário no site registra o porquê (plano 129 + o efeito triplo) e o aviso de que `saved` é rebindado a cada iteração.
- **Problemas / pendências:** nenhum.
- **Verificação:** os 3 testes da F0 passaram a **VERDES**; `tests/integration/characterization/test_webhook_characterization.py` **28 passed** com `git status tests/goldens` limpo (goldens de mídia byte-idênticos).

---

### Fase F2 — Sandbox mira a linha recém-salva (bug latente) 🟢

**Objetivo:** fechar os 3 sites gêmeos antes que alguém copie o padrão errado.

**Itens**
1. `[paralelo]` [sandbox.py:207](server/routes/sandbox.py#L207) (imagem): capturar `saved = contact.add_message(...)`; em [:224-228](server/routes/sandbox.py#L224-L228) trocar o prefixo inline por `format_media_content("image", description, caption)` e colar via `message_repo.update_content(saved["id"], …)`.
2. `[paralelo]` [sandbox.py:274,287-290](server/routes/sandbox.py#L274) (áudio): idem com `format_media_content("audio", transcription)`.
3. `[paralelo]` [sandbox.py:340,355-359](server/routes/sandbox.py#L340) (documento): idem com `format_media_content("document", transcription, content)` — a ordem texto→prefixo é a mesma do código atual.
4. `[sequencial]` Conferir que `format_media_content` já está importado em `sandbox.py` (hoje só `maybe_transcribe` vem de `server.transcription`, [sandbox.py:23](server/routes/sandbox.py#L23)).

⚠️ A troca do prefixo inline pelo helper tem de gerar **a mesma string**; qualquer divergência aparece nos goldens/asserções do sandbox.

**Pronto quando:** `venv/bin/python -m pytest tests/core tests/integration -k sandbox -q` verde; envio manual de imagem+legenda no sandbox mostra o card privado e a bolha com a legenda (sem prefixo visível).

#### Status de execução — Fase F2
**Estado:** ✅ Concluída (2026-08-20)
- **O que foi feito:** [server/routes/sandbox.py](server/routes/sandbox.py) — importa `message_repo` e `format_media_content`; ganhou o helper de módulo `_paste_media_content(saved, new_content, phone)`; os 3 sites (imagem/áudio/documento) capturam `saved = contact.add_message(...)` e colam por `id`, com os prefixos inline substituídos pelo helper compartilhado.
- **Como foi feito / decisões:** um helper único em vez de repetir o guard 3× (o sandbox não tem o `channel_id` do batch, então a assinatura é mais enxuta). Documento mantém a ordem INVERTIDA (texto→prefixo) e `content` nunca é vazio ali, então a string não muda.
- **Problemas / pendências:** **as 3 rotas de mídia do sandbox não tinham nenhuma cobertura automatizada** (`grep` por `sandbox/send-image|send-audio|send-document` em `tests/` = 0). A F2 iria para produção verificada só no olho, então acrescentei 3 testes ao arquivo da F0.
- **Verificação:** 6/6 verdes em `test_media_description_target.py`. **Prova de que a troca do prefixo inline pelo helper não mudou a string:** os 3 testes novos foram rodados contra o `server/routes/sandbox.py` de `HEAD` (código inline original) e passaram — 3 passed. `tests/core tests/integration -k sandbox` = 9 passed.

---

### Fase F3 — Guarda documental contra a reincidência 🟢

**Objetivo:** que a próxima pessoa não reintroduza a reprocura.

**Itens**
1. `[paralelo]` [agent/handler.py:423](agent/handler.py#L423) — docstring: ⚠️ o método **reprocura por `ts DESC`**; desde o plano 129 o `ts` é o do provedor, então ele **não serve** para colar transcrição de mídia (use o `id` do `INSERT`). Registrar que os únicos chamadores restantes são o teste de escopo por canal e o histórico.
2. `[paralelo]` [db/repositories/message_repo.py:534](db/repositories/message_repo.py#L534) — docstring: a função **não filtra `media_type`** e tem consumidor externo (`vendas_ia`), por isso a assinatura não muda (D5).
3. `[paralelo]` [db/repositories/message_repo.py:555](db/repositories/message_repo.py#L555) (`update_content`) — registrar que ela **não** ajusta `media_type`/`media_caption`: colar conteúdo composto numa linha que não é mídia produz vazamento no painel.

**Pronto quando:** `grep -rn "update_last_user_message_content" app server` devolve **zero** ocorrências (só o teste e a definição sobram).

#### Status de execução — Fase F3
**Estado:** ✅ Concluída (2026-08-20)
- **O que foi feito:** docstrings ⚠️ em [agent/handler.py](agent/handler.py) `update_last_user_message_content`, [db/repositories/message_repo.py](db/repositories/message_repo.py) `get_last_user_message` e `update_content`.
- **Como foi feito / decisões:** assinaturas e queries **intactas** (D5). A docstring de `get_last_user_message` registra explicitamente o consumidor externo (`vendas_ia`) como razão do congelamento; a de `update_content` explica por que colar conteúdo composto em linha sem `media_type` vaza no painel.
- **Problemas / pendências:** nenhum.
- **Verificação:** `grep -rn "update_last_user_message_content" app server` devolve só **2 menções em COMENTÁRIO** (o histórico do bug em `messaging_service.py` e em `sandbox.py`) — **zero chamadas**. A definição e o teste `tests/integration/test_multichannel_routing.py:276` seguem como previsto.

---

### Fase F4 — Rede de regressão permanente 🔴

**Objetivo:** travar o comportamento certo em golden, não só em asserção pontual.

**Itens**
1. `[sequencial]` Acrescentar um caso de caracterização em [tests/integration/characterization/test_webhook_characterization.py](tests/integration/characterization/test_webhook_characterization.py) no molde de `test_media_image_transcription_on_com_legenda` ([:430](tests/integration/characterization/test_webhook_characterization.py#L430)), com **texto de `ts` posterior + imagem de `ts` anterior**, gerando o golden novo `media_image_transcription_ts_fora_de_ordem.json` (`UPDATE_GOLDENS=1` **só** para o arquivo novo — conferir o diff antes de commitar).
2. `[sequencial]` Rodar a suíte inteira no Postgres de teste e comparar com o baseline conhecido de falhas pré-existentes (ver memória "4 falhas pré-existentes da suíte do core") — nenhuma falha nova.

**Pronto quando:** `WHATSBOT_TEST_DB_URL=… venv/bin/python -m pytest` sem falha nova; os goldens antigos de mídia **byte-idênticos**.

#### Status de execução — Fase F4
**Estado:** ✅ Concluída (2026-08-20)
- **O que foi feito:** novo caso `test_media_image_transcription_ts_fora_de_ordem` em [tests/integration/characterization/test_webhook_characterization.py](tests/integration/characterization/test_webhook_characterization.py) + golden `tests/goldens/media_image_transcription_ts_fora_de_ordem.json`.
- **Como foi feito / decisões:** o teste manda o TEXTO com `timestamp=1_600_000_100` e só então a IMAGEM com `timestamp=1_600_000_050`. O golden trava as duas linhas de uma vez: a da imagem com `[Descrição da imagem]: …` e a de texto com o texto do cliente **intacto** (`media_type: null`), mais o card `transcription`. `UPDATE_GOLDENS=1` rodado **só** para este teste.
- **Problemas / pendências:** nenhuma. **F5 NÃO foi executada** — escreve em produção e depende de P1, que segue em aberto.
- **Verificação:** suíte inteira no Postgres de teste: **4 falhas, todas as pré-existentes conhecidas** (`test_alembic_hygiene` ×2, `test_legacy_scripts[legacy_endpoints]`, `test_audit_characterization::test_audit_matrix_is_complete`) — **nenhuma nova**. As 6 falhas internas da suíte legada são todas de casamento de atributo do plugin `protocolos` (versão instalada), sem relação com mídia/sandbox. `git status tests/goldens` mostra **apenas o golden novo** — os de mídia pré-existentes ficaram byte-idênticos.

---

### Fase F5 — Reparo das 5 linhas em produção (opt-in, depende de P1) 🔴

**Objetivo:** devolver ao fio o texto do cliente onde ele for recuperável, e tirar o conteúdo interno de vista.

**Itens**
1. `[sequencial]` **Backup antes de tudo** (`pg_dump` da tabela `messages` filtrada pelas 3 conversas, no padrão de `~/whatsbot-backups`).
2. `[sequencial]` Restaurar as 3 linhas recuperáveis a partir de `executions.input_text` casado por `msg_id` (681435, 681937, 681954) — **uma a uma**, conferindo o texto antes.
3. `[sequencial]` Tratar 678046 e 678094 conforme P1.
4. `[sequencial]` **Opcional (P2):** colar a descrição na linha da imagem correspondente, para que o histórico volte a ter o conteúdo da foto.
5. `[sequencial]` Re-rodar o censo da §2.5 — tem de voltar **0 linhas**.

⚠️ Escrita em produção exige aprovação humana no fluxo do VAULT; a fase não pode ser executada "de passagem" junto com as outras.

#### Status de execução — Fase F5
**Estado:** ⛔ Não será executada — decisão do usuário (2026-08-20)
- **O que foi feito:** nada. Nenhuma escrita foi feita no banco de produção em nenhum momento deste plano; toda a investigação da §2.5/§2.6 foi **somente leitura**.
- **Como foi feito / decisões:** P1 = (a) "deixar como está" ⇒ P2 = (b). As 5 linhas históricas (3 conversas) permanecem com o texto interno no fio e sem o texto original do cliente. A correção fecha a ORIGEM; o passivo é aceito conscientemente.
- **Problemas / pendências:** o censo da §2.5 **não voltará 0** — deve estabilizar nas 5 linhas. Crescer a partir daí significa que a correção não pegou.
- **Verificação:** rodar o censo da §2.5 uma vez após o deploy (P3=(a)) e confirmar que a contagem parou de subir.

---

## 5 — Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Chave do dict | `saved["_id"]` não existe no caminho de escrita ⇒ colagem some em silêncio | D2: usar `"id"`; teste F0 pega na hora (a descrição não apareceria em lugar nenhum) |
| `saved` rebindado no laço | Mover a colagem para fora da iteração reintroduz o bug | Comentário no site + teste 3 da F0 (duas mídias no mesmo lote) |
| Goldens do plano 87 | Alterar o formato composto quebraria painel + LLM | D3: `format_media_content` intocado; goldens antigos são o gate da F1 |
| `get_last_user_message` | Endurecer a query quebraria `vendas_ia/filters.py:176` | D5: assinatura e query intactas |
| Sandbox | Troca de prefixo inline por helper mudar a string | Comparação literal na revisão + suíte do sandbox |
| Reparo em produção | `UPDATE` sem `WHERE` preciso / texto errado restaurado | Backup, uma linha por vez, `id` explícito, censo pós-reparo |
| 2 linhas irrecuperáveis | Reescrever com placeholder falsifica o histórico da conversa | Decisão explícita em P1, não default silencioso |
| Regressão futura | Novo caminho de mídia copiar o padrão da reprocura | F3 (docstrings) + golden da F4 |

---

## 6 — Perguntas em aberto

**P1 — O que fazer com as 2 linhas cujo texto do cliente é irrecuperável (678046, 678094)?**
Contexto: `executions.input_text` não tem correspondência e `execution_steps` não guardou o lote; o texto foi destruído pelo `UPDATE`.
(a) Deixar como está — o operador continua vendo o texto interno no fio.
(b) Substituir por um marcador neutro (ex.: `[mensagem perdida por falha do sistema]`) e mover a descrição para a linha da imagem.
(c) Apagar a linha (some do fio; a mensagem do cliente deixa de existir no histórico).
**Recomendação era (b)**; ✅ **DECIDIDO (2026-08-20): (a) — deixar como está.** São 5 linhas em 3 conversas antigas; o custo de mexer em produção (backup, `UPDATE` linha a linha, aprovação no cofre) não se paga contra o benefício. A correção fecha a origem; o passivo fica.

**P2 — Colar a descrição retroativamente nas linhas de imagem das 3 conversas?**
Contexto: a linha da imagem ficou sem descrição, então uma retomada dessas conversas perde o conteúdo da foto. A descrição está preservada no card `role='transcription'` correspondente (ex.: `678049`), então dá para reconstruir o `content` composto.
(a) Sim, junto da F5 — restaura o histórico completo.
(b) Não — 3 conversas antigas, custo/benefício baixo.
✅ **DECIDIDO (2026-08-20): (b) — não.** Decorre de P1=(a): sem o reparo, não há passo de escrita onde encaixar a colagem retroativa.

**P3 — Alerta/monitoramento para o padrão?**
Contexto: o censo da §2.5 é uma consulta barata que detecta a classe inteira (qualquer prefixo de IA em linha sem `media_type`).
(a) Rodar manualmente após o deploy da correção e encerrar.
(b) Virar verificação recorrente.
✅ **DECIDIDO (2026-08-20): (a) — censo único, manual, após o deploy.** Com a correção e o golden a origem deixa de existir; o censo serve só para confirmar que a contagem parou de crescer (deve permanecer nas 5 linhas históricas, não em 0 — ver P1).

---

## 7 — Apêndice — arquivos-chave

**Backend (correção)**
- [app/services/messaging_service.py](app/services/messaging_service.py) — laço de mídia do batch (`:1241` save, `:1300-1312` colagem, `:1318` card, `:1341-1350` `llm_text`)
- [server/routes/sandbox.py](server/routes/sandbox.py) — imagem `:207/:224-228`, áudio `:274/:287-290`, documento `:340/:355-359`
- [db/repositories/message_repo.py](db/repositories/message_repo.py) — `add` `:16-75` (devolve `"id"`), `get_last_user_message` `:534`, `update_content` `:555`, `_row_to_dict` `:864-916` (devolve `_id`)
- [agent/handler.py](agent/handler.py) — `update_last_user_message_content` `:423-437`

**Backend (contexto, não muda)**
- [server/transcription.py](server/transcription.py) — `format_media_content` `:123`, `modes_for` `:100-120`
- [app/services/message_ingest_service.py](app/services/message_ingest_service.py) — echo `:280-359`, grupo sem @menção `:540-570`
- [agent/memory.py](agent/memory.py) — `add_message` `:436-509` (devolve a linha inserida)

**Frontend (não muda — D6)**
- [web/static/js/services/messageView.js](web/static/js/services/messageView.js) — `AI_CONTENT_PREFIXES` `:213`, `mediaCaptionOf` `:248`

**Testes**
- `tests/integration/test_media_description_target.py` *(novo — F0)*
- [tests/integration/test_inbound_provider_ts_ordering.py](tests/integration/test_inbound_provider_ts_ordering.py) — molde de driver/helpers
- [tests/integration/characterization/test_webhook_characterization.py](tests/integration/characterization/test_webhook_characterization.py) — matriz de mídia + goldens
- `tests/goldens/media_image_transcription_on*.json`, `media_audio_transcription_on.json`, `media_document_transcription_on_com_legenda.json`

---

## 8 — Checklist de verificação

- [x] F0 vermelho ANTES da correção, pelo motivo certo (descrição na linha sem `media_type`)
- [x] `grep -rn "update_last_user_message_content" app server` = 0 **chamadas** (só 2 menções em comentário, registrando o histórico do bug)
- [x] Goldens de mídia pré-existentes **byte-idênticos** (`git status tests/goldens` mostra só o golden novo)
- [x] `WHATSBOT_TEST_DB_URL=… venv/bin/python -m pytest` sem falha nova vs. o baseline conhecido (4 pré-existentes)
- [x] Um único processo pytest contra o banco de teste (nunca duas suítes em paralelo)
- [x] Cobertura automatizada substituindo a validação manual do sandbox: 3 testes novos nas rotas `send-image`/`send-audio`/`send-document`, que **antes não tinham nenhuma** — e que passam também contra o código inline de `HEAD`, provando que a string não mudou
- [ ] Validação manual em produção: cliente manda imagem + texto quase simultâneos ⇒ bolha da imagem com a legenda, card privado com a descrição, texto do cliente intacto *(após deploy)*
- [ ] Censo da §2.5 **estável em 5 linhas** (não 0 — P1=(a) manteve o passivo) uma vez após o deploy
- [x] N/A — nenhuma escrita em produção (F5 cancelada por P1=(a)); toda a investigação foi somente leitura
- [x] Um refactor por commit; nada de "de passagem" no laço do batch
