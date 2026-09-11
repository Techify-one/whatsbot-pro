# Plano 146 — A mescla do batch vira entrada da IA, não linha do histórico

> **Status:** ✅ EXECUTADO (código, testes, docs e plugin) · **Data:** 2026-08-28 · **Auditoria adversarial + correções:** 2026-09-01 · **Escopo:** médio (core: 1 arquivo quente; plugins: 2 a auditar; sem migration, sem bump de `WHATSBOT_API_VERSION`)
> **Origem:** relato do operador sobre a conversa **10886** de produção — o atendente clicou com o botão direito em "Responder" numa mensagem do cliente e a bolha citada saiu **"Mensagem original indisponível"**, acima da mensagem que ela citava. **Método:** leitura do código com `arquivo:linha` verificados + consulta **somente-leitura** ao banco de produção pelo cofre (a credencial fica fora deste documento — repositório público).
> **O quê/porquê:** o batch de recepção junta N mensagens de texto do cliente numa **única linha** de `messages`, sob o `msg_id` da **última**. Os `msg_id` das demais **nunca são persistidos** — existem só como `supersedes` num evento de WebSocket. Toda citação que aponte para uma mensagem engolida fica **órfã para sempre**. Este plano mantém a mescla onde ela serve (o ciclo da IA: um turno, uma resposta) e devolve ao histórico uma linha por mensagem real.
>
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0 — Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 ✅ (2026-08-28) | A mescla continua existindo **para a IA** — um ciclo, uma chamada de LLM, uma resposta por batch. O que muda é **onde ela vive**: deixa de ser a linha do banco. | Nada no orquestrador (`_orchestrate`, `message_batch_delay`, espera de digitação) é tocado. A mudança é do ponto de **save** para dentro. |
| D2 ✅ (2026-08-28) | O painel mostra **uma bolha por mensagem que o cliente mandou**. Duas mensagens nunca viram uma. | O ramo de texto passa a salvar N linhas, como o de mídia **já faz** hoje. |
| D3 ✅ (2026-08-28) | **Sem retroatividade.** As linhas já mescladas ficam como estão. | Não há backfill possível: os `msg_id` engolidos foram descartados no momento do save e não existem em lugar nenhum. As 33 citações órfãs medidas em produção (§2.1) permanecem órfãs. |
| D4 ✅ (2026-08-28) | A auditoria dos assinantes de `message.saved` é **Fase 0 bloqueante**, não uma verificação depois do fato. | O leque de N eventos é a única consequência que sai do core e atinge plugin em produção. P1 só é decidida com o resultado da F0.2 na mão. |
| D5 ✅ (2026-08-28) | Nada de flag de configuração para escolher entre mesclar e não mesclar. | Duas formas de gravar o mesmo fato é a dívida que este plano existe para pagar. O `message_batch_delay` continua sendo o único botão. |

**Princípio fixo:** o banco registra **o que o cliente fez**; a mescla é uma conveniência de processamento. Sempre que os dois discordarem, o registro ganha.

---

## 1 — Resumo executivo

Quando o cliente manda duas mensagens seguidas, o orquestrador espera `message_batch_delay` (3s por padrão), junta os textos com `\n` e grava **uma linha só**, carimbada com o `msg_id` e o `ts` da **última** mensagem ([messaging_service.py:1434-1449](../app/services/messaging_service.py#L1434)). Os `msg_id` das anteriores viajam apenas no campo `supersedes` de um evento de WebSocket, para o painel colapsar as bolhas otimistas — e morrem ali.

Disso saem dois defeitos que o operador vê como um só:

1. **A citação fica sem origem.** Uma resposta que cite a *primeira* mensagem do batch aponta para um `msg_id` que não existe em `messages`; a hidratação da página não acha nada ([conversations.py:45-71](../server/routes/conversations.py#L45)) e a bolha desenha **"Mensagem original indisponível"** ([MessageBubble.js:111](../web/static/js/components/contacts/MessageBubble.js#L111)).
2. **A resposta sobe acima do que ela cita.** A linha combinada herda o `ts` do **último** item, então uma resposta escrita entre a primeira e a segunda mensagem do cliente fica com `ts` menor que o da linha e renderiza antes dela.

A correção é alinhar o ramo de texto com o ramo de mídia, que **já** salva uma linha por item, cada uma com o seu `msg_id`, o seu `reply_to_msg_id` e o seu `ts` do provedor ([messaging_service.py:1574-1582](../app/services/messaging_service.py#L1574)). O `combined` continua sendo montado — mas só como texto do ciclo (log, `executions.input_text`), nunca como conteúdo de linha.

---

## 2 — Como funciona hoje (mapa verificado)

### 2.1 A medição em produção

Consulta somente-leitura ao banco de produção em 2026-08-28:

| Medida | Valor |
|---|---|
| Mensagens com citação (`reply_to_msg_id IS NOT NULL`) | **2.128** |
| Citações **órfãs** (alvo inexistente em `messages`) | **33** |
| Órfãs vindas da importação do Chatwoot (`WAID:%`) | **0** |
| Órfãs nativas | **33** — a primeira em **22/07/2026**, a última em **28/08/2026** |
| Citações resolvidas cujo alvo tem `ts` **posterior** (resposta acima do alvo) | **1** em 1.826 |

22/07 é exatamente quando o plano 75 passou a capturar a citação inbound: **a taxa é de ~1 citação órfã por dia desde que o recurso existe**, e a mais recente é de hoje.

O sintoma "resposta acima do alvo" praticamente não aparece isolado (1 caso) porque, quando a resposta cita a mensagem engolida, ela vira **órfã e fora de ordem ao mesmo tempo** — é o caso da conversa 10886.

⚠️ **A taxa de mescla é alta:** 11,2 % dos ciclos de IA recentes têm `input_text` com quebra de linha, e 10,5 % das linhas de texto do cliente gravadas depois de 24/07 são multi-linha. Os dois números são **limite superior** (mensagem única com quebra de linha também conta), mas fixam a ordem de grandeza: a mescla acontece em cerca de **1 a cada 10 turnos**.

### 2.2 O caso 10886, linha a linha

Extraído de produção (`conversation_id = 10886`, 19/08, horário de São Paulo):

| id | role | quando | conteúdo | `msg_id` (fim) | cita (fim) |
|---|---|---|---|---|---|
| 678873 | assistant/operator | 14:05:05 | "Certo" | `…OUEwRTlGOQA=` | — |
| 678882 | assistant/operator | 14:05:30 | "Verifique se o comando está certinho…" | `…RkIyMzUzRQA=` | — |
| **678885** | assistant/operator | **14:05:37** | "Sem problemas, fico no aguardo." | `…NThDMDU2NAA=` | **`…MTMyQzk3AA==` — não existe** |
| **678892** | user | **14:05:50** | "Vou tentar abrir o computador… ⏎ Eu vou ver ainda se a vm está lá…" | `…NEQyODE2AA==` | — |

A linha **678892** é a mescla: **duas** falas da cliente num `content` só, sob **um** `msg_id` — o da segunda. A resposta **678885** cita a **primeira**, cujo `msg_id` não sobreviveu ao save. E, como a linha mesclada herdou o `ts` da segunda fala (14:05:50), a resposta das 14:05:37 desenha **acima** dela.

Um print da tela mostra os dois defeitos juntos: a bolha "Mensagem original indisponível" às 14:05, e — três minutos depois — outra resposta citando a **mesma** linha, agora resolvendo e exibindo **as duas frases coladas**, porque é isso que a linha guarda.

### 2.3 O caminho do inbound

| # | Etapa | `arquivo:linha` | Estado |
|---|---|---|---|
| 1 | Cada mensagem emite o seu **próprio** `new_message` otimista em t=0 | [message_ingest_service.py:544](../app/services/message_ingest_service.py#L544) | 1 evento por mensagem |
| 2 | O item entra na fila do batch **com** `msg_id`, `reply_to_msg_id` e `ts` do provedor | [message_ingest_service.py:583-595](../app/services/message_ingest_service.py#L583) | dado íntegro |
| 3 | O orquestrador espera digitação + `message_batch_delay` e consome a fila | [messaging_service.py:1832-1841](../app/services/messaging_service.py#L1832) | — |
| 4 | Acumulação: `text_msg_ids` guarda **todos**; `text_reply_to` e `text_ts_last` guardam **o último** | [messaging_service.py:1397-1403](../app/services/messaging_service.py#L1397) | ⚠️ perda começa aqui |
| 5 | `combined = "\n".join(...)`, `last_msg_id = text_msg_ids[-1]` | [messaging_service.py:1434-1438](../app/services/messaging_service.py#L1434) | — |
| 6 | **UM** `add_message` com o texto combinado | [messaging_service.py:1445-1449](../app/services/messaging_service.py#L1445) | ⚠️ N mensagens → 1 linha |
| 7 | `new_message` autoritativo com `supersedes=text_msg_ids` | [messaging_service.py:1458](../app/services/messaging_service.py#L1458) | manda o painel colapsar |
| 8 | **UM** `message.saved` com `source="batch_text"` | [messaging_service.py:1461-1470](../app/services/messaging_service.py#L1461) | 1 evento por batch |
| 9 | A IA roda (se o gate deixar) | [messaging_service.py:1471-1489](../app/services/messaging_service.py#L1471) | — |

### 2.4 O ramo de mídia já faz o certo

No **mesmo ciclo**, cada item de mídia salva a sua própria linha, com `msg_id=item["msg_id"]`, `reply_to_msg_id=item["reply_to_msg_id"]` e `ts=item["ts"]` ([messaging_service.py:1574-1582](../app/services/messaging_service.py#L1574)), emite `new_message` **sem** `supersedes` ([:1585-1589](../app/services/messaging_service.py#L1585)) e um `message.saved` com `source="batch_media"` **por item** ([:1592-1602](../app/services/messaging_service.py#L1592)).

⚠️ **Isto não é território novo, e o leque de N eventos já existe em produção** — um batch com três fotos já emite três `message.saved` hoje. O ramo de texto é o **único** que colapsa.

### 2.5 O achado que muda o desenho: a IA não lê o `combined`

O batch chama `aprocess_message(phone, combined, save_user_message=False, …)` ([messaging_service.py:1486-1489](../app/services/messaging_service.py#L1486)). Dentro de `run_turn`, o parâmetro `text` é usado **exclusivamente** para gravar a mensagem quando `save_user_message=True` ([agent_run_service.py:270-271](../app/services/agent_run_service.py#L270)). O que o modelo recebe é **só** o histórico lido do banco ([agent_run_service.py:280](../app/services/agent_run_service.py#L280) → [memory.py:636-647](../agent/memory.py#L636)).

Ou seja: **com `save_user_message=False`, o `combined` passado ao handler é descartado.** Quem mescla para a IA, hoje, é a **linha do banco** — não o argumento.

⚠️ **Consequência direta:** salvar N linhas faz o modelo passar a ver **N turnos `user` consecutivos** em vez de um. O conteúdo é o mesmo, na mesma ordem; muda a forma e muda a contagem da janela (`max_context_messages`, padrão 10). Isso é a **P2** e precisa de decisão explícita — não é efeito colateral a descobrir depois.

### 2.6 O painel não precisa mudar

As bolhas individuais **já existem** ao vivo (§2.3 item 1). Quem as colapsa é o `supersedes` do evento autoritativo, consumido por `dropSuperseded` ([messages.js:119-127](../web/static/js/services/messages.js#L119)) e por `mergeBufferedMessages` ([messages.js:143-165](../web/static/js/services/messages.js#L143)), chamados em [useConversationWsEvents.js:797-798](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L797).

É exatamente por isso que o operador vê **certo ao vivo e mesclado ao reabrir**: ao vivo sobram as bolhas otimistas até o autoritativo chegar; ao reabrir, o `GET` devolve a linha única do banco.

### 2.7 Quem escuta `message.saved` (medido, não suposto)

`grep` nos `EVENT_HANDLERS` dos 20 plugins instalados em `storages/plugins/`:

| Plugin | Assina | O que faz no inbound | Exposição ao leque de N |
|---|---|---|---|
| `protocolos` | `message.saved` | `on_inbound` → `ensure_protocolo_ex` + `ensure_open_cycle` ([logic.py:4112-4132](../storages/plugins/protocolos/logic.py#L4112)) | **Média** — os `ensure_*` são idempotentes, mas `_skip_open_matches` passa a testar **cada mensagem** em vez do texto combinado |
| `retornos` | `message.saved` | `on_message_saved` → arma/reseta/cancela o controle de retorno ([events.py:118-158](../storages/plugins/retornos/events.py#L118)) | **ALTA** — ver R1 em §5 |
| `telegram` | `message.saved` | Sai no primeiro guard quando não há `media_type` ([events.py:28-33](../storages/plugins/telegram/events.py#L28)) | **Nenhuma** para texto |
| `debug_bus` | `*` | Grava o evento | **Baixa** — mais linhas; a retenção do plano 140 cobre |

### 2.8 Falsos positivos descartados

| Suspeita | Veredito |
|---|---|
| "`trackify` e `janela_72h` também quebram." | ❌ **Não.** `trackify` assina `message.received` — que **já** dispara uma vez por mensagem, antes do batch. `janela_72h` **não assina** `message.saved`. Verificado nos `EVENT_HANDLERS` dos dois. |
| "É preciso passar o texto combinado explicitamente para a IA." | ❌ **Hoje ele já é passado e ignorado** (§2.5). Mexer nisso é a opção (c) da P2, não um requisito. |
| "O frontend precisa aprender a separar as bolhas." | ❌ **Já separa.** O que ele precisa é parar de receber a ordem de colapsar (§2.6). |
| "A ordem das mensagens está quebrada em geral e o plano 129 falhou." | ❌ **Não.** Medido: 1 caso em 1.826 citações resolvidas. O 129 resolveu o caso geral; sobrou o `ts` herdado pela linha mesclada. |
| "Dá para recuperar as citações órfãs com um backfill." | ❌ **Não.** O `msg_id` engolido não existe em nenhuma tabela nem em log — o payload cru do webhook só vive em memória (últimos 50). D3. |
| "Basta persistir os `msg_id` engolidos numa coluna/tabela de apelidos." | ⚠️ É a alternativa barata que foi **rejeitada** na conversa que originou o plano: resolve a citação e deixa o painel continuar mentindo sobre o que o cliente mandou (a citação exibiria as duas frases coladas, como no print). |

---

## 3 — Inventário das mudanças

| # | Onde | `arquivo:linha` | O que muda | Risco | Esforço |
|---|---|---|---|---|---|
| M1 | Acumulação do batch | [messaging_service.py:1384-1403](../app/services/messaging_service.py#L1384) | Guardar os **itens** de texto (lista de dicts), não três escalares derivados do último | baixo | S |
| M2 | Save do texto | [messaging_service.py:1433-1470](../app/services/messaging_service.py#L1433) | Laço por item: 1 `add_message` + 1 `new_message` + 1 `message.saved` por mensagem, espelhando o ramo de mídia | **alto** (caminho quente) | M |
| M3 | `supersedes` | [messaging_service.py:1458](../app/services/messaging_service.py#L1458) | Deixa de ser enviado (não há mais o que colapsar) | baixo | S |
| M4 | `combined` | [messaging_service.py:1434](../app/services/messaging_service.py#L1434) | Continua montado, mas só para log e `executions.input_text` | baixo | S |
| M5 | `filter.conversation.before_reopen` | [messaging_service.py:1441-1444](../app/services/messaging_service.py#L1441) | Passa a ser avaliado **por mensagem** | médio | S |
| M6 | Janela de contexto da IA | [agent_run_service.py:280](../app/services/agent_run_service.py#L280) | Depende da P2 | médio | S–M |
| M7 | `dropSuperseded` / `mergeBufferedMessages` | [messages.js:119](../web/static/js/services/messages.js#L119) | **Mantidos** — linhas legadas e rollback ainda dependem deles | nenhum | — |
| M8 | Plugin `retornos` | [events.py:118](../storages/plugins/retornos/events.py#L118) | Guard contra re-armar no mesmo turno (R1) | médio | S |
| M9 | Documentação | [docs/UI_CONVERSA.md](../docs/UI_CONVERSA.md), [docs/PLUGIN_BUS.md](../docs/PLUGIN_BUS.md) | Registrar a nova cardinalidade de `batch_text` | baixo | S |

---

## 4 — Fases, waves e paralelização

```
WAVE 0  F0.1 (caracterização) · F0.2 (auditoria dos assinantes)      ← paralelo
           │  barreira: P1 e P2 decididas antes de qualquer código
WAVE 1  F1 (o laço por item)                                          ← SOZINHA
           │
WAVE 2  F2 (regressão citação) · F3 (regressão ordem) · F4 (IA) ·     ← paralelo
        F5 (frontend, verificação) · F6 (plugin retornos)
           │
WAVE 3  F7 (documentação)
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | F0.1 | testes | 🟢 | baixo | Golden do comportamento atual congelado |
| 0 | F0.2 | plugins | 🟢 | baixo | Os 4 assinantes auditados; P1 decidida |
| 1 | F1 | backend | 🔴 | **alto** | `[bloqueia: F2–F6]` Uma linha por mensagem, suíte verde |
| 2 | F2 | testes | 🟢 | baixo | `[depende de: F1]` Citação à 1ª mensagem resolve |
| 2 | F3 | testes | 🟢 | baixo | `[depende de: F1]` Resposta renderiza depois do alvo |
| 2 | F4 | IA | 🟢 | médio | `[depende de: F1]` P2 aplicada e medida |
| 2 | F5 | frontend | 🟢 | baixo | `[depende de: F1]` Nenhuma mudança necessária, provado |
| 2 | F6 | plugin | 🟢 | médio | `[depende de: F0.2]` `retornos` não re-arma no mesmo turno |
| 3 | F7 | docs | 🟢 | baixo | Guias atualizados; `test_docs_hygiene` verde |

---

### Fase F0.1 — Caracterização do comportamento atual 🟢

**Objetivo:** congelar, em teste, o que acontece hoje com **duas** mensagens de texto no mesmo batch — hoje não existe nenhum teste que exercite isso.

**Itens:**
1. `[sequencial]` Novo arquivo `tests/integration/test_batch_message_identity.py`, no molde de [test_inbound_provider_ts_ordering.py](../tests/integration/test_inbound_provider_ts_ordering.py) (fixture `build_app`, `message_batch_delay: 0`, `_drain_orchestrator`).
2. `[paralelo]` Teste A: dois `POST /api/webhook/gowa/default` com `msg_id` distintos → hoje **1 linha**, `msg_id` do segundo, `content` com `\n`. Marcado com o comentário `VERDE antes da F1, VERMELHO depois — é o registro da mudança`.
3. `[paralelo]` Teste B: o `message.saved` com `source="batch_text"` é emitido **uma** vez.
4. `[paralelo]` Teste C: o `new_message` autoritativo carrega `supersedes` com o `msg_id` da primeira.
5. `[paralelo]` Teste D (**este nasce vermelho e é o alvo do plano**): resposta citando a **primeira** mensagem do batch → após releitura, `_attach_quoted` resolve o alvo.

**Pronto quando:** A, B e C verdes; D vermelho com a mensagem "Mensagem original indisponível" reproduzida em teste. `venv/bin/python -m pytest tests/integration/test_batch_message_identity.py` roda no Postgres de teste.

#### Status de execução — Fase F0.1
**Estado:** ✅ Executada (2026-08-28)
- **O que foi feito:** `tests/integration/test_batch_message_identity.py` (398 linhas) com os casos A (N linhas), B (N eventos `batch_text`), C (autoritativo) e D (a citação à primeira mensagem).
- **Como foi feito / decisões:** Os casos nasceram descrevendo a mescla e foram INVERTIDOS junto com a F1.
- **Problemas / pendências:** ⚠️ O arquivo nasceu **untracked** e só entrou no git no commit da F1 — então a inversão A/B/C **não é auditável no histórico**, ao contrário do que a R8 pedia. ⚠️ Nenhuma asserção distingue "2 mensagens num batch" de "2 batches de 1": todas continuariam verdes se a mescla nunca tivesse acontecido (risco de falso-verde). Sem cobertura para `reply_to_msg_id` por linha, para `executions.input_text` e para o `continue` de item vazio.
- **Verificação:** `venv/bin/python -m pytest tests/integration/test_batch_message_identity.py` no Postgres de teste.

---

### Fase F0.2 — Auditoria dos assinantes de `message.saved` 🟢 🔴 *(barreira)*

**Objetivo:** responder, com leitura de código e não por suposição, o que cada assinante faz quando o evento passa a chegar **N vezes** — e decidir a **P1**.

**Itens:**
1. `[paralelo]` `protocolos` — confirmar que `ensure_protocolo_ex` e `ensure_open_cycle` são idempotentes sob N chamadas no mesmo turno ([logic.py:4125-4130](../storages/plugins/protocolos/logic.py#L4125)) e medir o custo (consultas por evento). Avaliar `_skip_open_matches` ([logic.py:4116](../storages/plugins/protocolos/logic.py#L4116)): a regra "ignorar abertura" hoje casa contra o **texto combinado**; passará a casar contra **cada** mensagem.
2. `[paralelo]` `retornos` — confirmar R1 (§5) lendo o fluxo `ctrl ativo → on_reply=cancel → cancel_by_conversation` seguido, no evento seguinte, do laço "sem controle ativo → `_arm(motivo='entrada')`" ([events.py:136-153](../storages/plugins/retornos/events.py#L136)).
3. `[paralelo]` `telegram` — confirmar a saída no primeiro guard para texto ([events.py:30-31](../storages/plugins/telegram/events.py#L30)).
4. `[paralelo]` `debug_bus` — estimar o crescimento de linhas com a retenção do plano 140 ligada.
5. `[sequencial]` Registrar a decisão da **P1** neste arquivo.

**Pronto quando:** tabela preenchida com o veredito de cada plugin (`seguro` / `precisa de guard` / `muda de comportamento`), e P1 marcada ✅ com data.

#### Status de execução — Fase F0.2
**Estado:** ✅ Executada (2026-08-28) — **com furos descobertos depois**
- **O que foi feito:** Auditoria dos assinantes de `message.saved`; P1 decidida em **(a)**.
- **Como foi feito / decisões:** Varredura de `EVENT_HANDLERS` em `storages/plugins/`: `protocolos` (idempotente), `retornos` (alternante → F6), `telegram` (sai no 1º guard para texto), `debug_bus` (observador).
- **Problemas / pendências:** 🔴 **A varredura foi incompleta — três furos, achados na auditoria adversarial de 2026-09-01:** (1) o **5º assinante** nunca foi visto: `server/webhook_dispatcher.py` assina `"*"` e `message.saved` está em `EXPORTABLE_EVENTS`, então todo webhook de SAÍDA cadastrado passa a receber N entregas HTTP por batch, cada uma com texto parcial; (2) no `protocolos`, o R2 foi avaliado só em `on_inbound` — a mesma regex é lida em `suppress_ai_on_ignored` (`filter.llm.messages`, logic.py:6118), que pega a ÚLTIMA mensagem `user` do histórico: antes essa "última" era o bloco mesclado inteiro, hoje é só a última frase, então **a IA passa a responder onde antes calava**; (3) a idempotência do `protocolos` foi avaliada como SEQUENCIAL, mas o bus despacha com `asyncio.create_task` (plugins/events.py:472) — os dois eventos do mesmo batch correm em paralelo, e a migration 002 do plugin **derruba** o índice `plugin_protocolos_atend_unique`, deixando o get-or-create do ciclo como check-then-insert sem trava.
- **Verificação:** Leitura dos quatro `events.py` instalados. ⚠️ A varredura deveria ter incluído o CORE (`grep -rn 'EVENT_HANDLERS\|subscribe' server/ app/`), não só `storages/plugins/`.

---

### Fase F1 — O laço por item no ramo de texto 🔴 *(faça sozinha)*

**Objetivo:** o ramo de texto passa a salvar uma linha por mensagem, espelhando o de mídia. É o coração do plano e mexe no caminho quente do inbound.

**Itens:**
1. `[sequencial]` **M1** — na acumulação ([messaging_service.py:1393-1403](../app/services/messaging_service.py#L1393)), guardar `text_items: list[dict]` com o item inteiro. `text_parts`/`text_msg_ids` continuam derivados dele (o `combined` e o `executions.input_text` seguem iguais). **Não** remover `text_ts_last`/`text_reply_to` antes da M2 — a remoção é do mesmo commit, mas depois que o laço existir.
2. `[sequencial]` **M2** — trocar o bloco [1439-1470](../app/services/messaging_service.py#L1439) por um laço `for item in text_items:` que, para cada item:
   - avalia `filter.conversation.before_reopen` com o texto **daquela** mensagem (M5);
   - chama `contact.add_message("user", item["text"], msg_id=item["msg_id"], reply_to_msg_id=item["reply_to_msg_id"], ts=(item.get("ts") or None), reopen=…)`;
   - emite `new_message` com `build_inbound_saved_message(saved)` — **sem** `supersedes` (M3);
   - emite `message.saved` com `source="batch_text"`, `text` da mensagem e o `msg_id` dela.
   O molde exato é o ramo de mídia em [1574-1602](../app/services/messaging_service.py#L1574) — **copie a forma, não invente outra**.
3. `[sequencial]` **M4** — `combined` continua sendo montado em [:1434](../app/services/messaging_service.py#L1434) e continua indo para o log `[Batch] Processing %d text messages` e para `aset_execution_texts`. O `msg_id` da execução continua sendo o do **último** item (a execução é o turno, não a mensagem).
4. `[sequencial]` A chamada `aprocess_message(phone, combined, save_user_message=False, …)` em [:1486](../app/services/messaging_service.py#L1486) **não muda nesta fase** (ver F4 e P2).
5. `[sequencial]` Rodar a caracterização da F0.1: A, B e C **têm de virar vermelhos** e ser reescritos para a nova verdade, no **mesmo commit**, com o comentário explicando a inversão. D **tem de virar verde**.

⚠️ **Um refactor por commit.** M1+M2+M3+M4+M5 são uma unidade semântica (a linha do batch deixa de existir) e vão juntas; nada mais entra nesse commit.

**Pronto quando:**
- Duas mensagens no mesmo batch produzem **duas** linhas em `messages`, cada uma com o seu `msg_id` e o seu `ts` do provedor.
- `venv/bin/python -m pytest tests/integration tests/core` verde no Postgres de teste.
- `tests/integration/characterization/test_webhook_characterization.py` verde **sem alteração** (todos os seus casos mandam uma mensagem por batch — verificado).
- No painel: mandar duas mensagens seguidas do WhatsApp, recarregar a conversa (F5) e ver **duas bolhas**, não uma.

#### Status de execução — Fase F1
**Estado:** ✅ Executada (2026-08-28)
- **O que foi feito:** O laço por item no ramo `Text batch` de `app/services/messaging_service.py` (M1–M5): `text_items` substitui `text_reply_to`/`text_ts_last`, e cada mensagem vira uma linha com o SEU `msg_id`, `reply_to_msg_id` e `ts`.
- **Como foi feito / decisões:** Cópia da forma do ramo de mídia logo abaixo, como a M2 mandava. `combined` continua existindo para o log e `executions.input_text`. O `new_message` autoritativo passou a sair sem `supersedes`.
- **Problemas / pendências:** 🔴 **Efeito colateral não previsto pelo plano, corrigido em 2026-09-01:** `message_repo.get_context` (a leitura que alimenta o LLM) ordenava só por `ts DESC`. O `ts` do provedor tem resolução de SEGUNDO, então duas mensagens do mesmo segundo passaram a ser duas linhas com `ts` IDÊNTICO e ordem indefinida — o modelo podia ler os dois turnos `user` trocados. Desempate por `id` acrescentado em `get_context`, `get_context_by_conversation`, `get_last` e `get_last_user_message`. ⚠️ As 7 subqueries de `db/repositories/conversation_query.py` (preview da sidebar) têm o MESMO empate e **continuam sem desempate**.
- **Verificação:** Suíte completa verde exceto as 3 falhas pré-existentes (cadeia do Alembic ×2 + matriz de auditoria — idênticas no `HEAD` sem este patch) e `test_f4`, que passa isolada (flake de ordem).

---

### Fase F2 — Regressão da citação 🟢 `[depende de: F1]`

**Objetivo:** provar que a citação à **primeira** mensagem de um batch resolve, que é o defeito relatado.

**Itens:**
1. `[paralelo]` Promover o teste D da F0.1 a teste de regressão nomeado, com referência à conversa 10886 no docstring (é o padrão dos testes do plano 129).
2. `[paralelo]` Teste do caminho de **hidratação fora da página**: alvo antigo, resposta nova, `_attach_quoted` devolve `quoted` ([conversations.py:59-71](../server/routes/conversations.py#L59)) — garante que a correção vale também quando o alvo saiu da janela paginada.
3. `[paralelo]` Teste negativo: citação a um `msg_id` que **nunca** existiu continua caindo em "Mensagem original indisponível" (o fallback não pode sumir).

**Pronto quando:** os três verdes; e uma consulta manual na conversa 10886 do ambiente de desenvolvimento reproduz o cenário do print com a citação resolvida.

#### Status de execução — Fase F2
**Estado:** ✅ Executada (2026-08-28)
- **O que foi feito:** `test_reply_quoting_first_batch_message_resolves`, `test_quoted_target_outside_page_is_hydrated` e `test_quoted_target_that_never_existed_still_falls_back`.
- **Como foi feito / decisões:** O 3º prova que o fallback "Mensagem original indisponível" NÃO sumiu — ele continua correto para alvo que nunca existiu.
- **Problemas / pendências:** ⚠️ O critério original falava em "consulta manual na conversa 10886": isso é inatingível por construção (a D3 proíbe backfill e a linha mesclada segue mesclada). Lido como **reencenar** o cenário em dev. ⚠️ O teste da citação faz a mesma asserção do teste A e **não exercita `_attach_quoted`** — apagar o `message_repo.add` da resposta o deixaria verde.
- **Verificação:** Incluídos na suíte de integração.

---

### Fase F3 — Regressão da ordem 🟢 `[depende de: F1]`

**Objetivo:** a resposta escrita entre duas mensagens do cliente fica **entre** elas, não acima das duas.

**Itens:**
1. `[paralelo]` Teste no molde de `test_delayed_reply_renders_after_quoted_message` ([test_inbound_provider_ts_ordering.py:165](../tests/integration/test_inbound_provider_ts_ordering.py#L165)): cliente manda A (ts BASE+10) e B (ts BASE+30) no mesmo batch, operador responde citando A com ts BASE+20 → a ordem por `(ts, id)` tem de sair **A, resposta, B**.
2. `[paralelo]` Verificar que os testes M4/M5 do plano 129 continuam verdes (a herança de `ts` que eles descrevem deixa de existir para texto — se algum deles a afirmar, corrigir o comentário no mesmo commit).

**Pronto quando:** ordem `A → resposta → B` provada em teste; suíte do plano 129 verde.

#### Status de execução — Fase F3
**Estado:** ✅ Executada (2026-08-28)
- **O que foi feito:** Cobertura da ordem A → resposta → B, o 2º defeito do relato.
- **Como foi feito / decisões:** Asserção sobre a sequência lida do repositório.
- **Problemas / pendências:** Ver o desempate por `id` registrado na F1 — sem ele a própria ordem que esta fase testa era indefinida no empate de segundo.
- **Verificação:** Incluído na suíte de integração.

---

### Fase F4 — O que a IA passa a ver 🟢 `[depende de: F1]`

**Objetivo:** aplicar a decisão da **P2** e medir o efeito na janela de contexto.

**Itens:**
1. `[sequencial]` Registrar a P2 decidida (§6).
2. `[paralelo]` Teste: com duas mensagens no batch, `get_context_messages` devolve as duas como turnos `user` consecutivos, **na ordem certa**, e `_encode_history_for_split` não as embaralha ([agent_run_service.py:281-282](../app/services/agent_run_service.py#L281)).
3. `[paralelo]` Se a P2 escolher a opção (b) — colapso na montagem do contexto —, o colapso é uma **função pura**, testada por `node --test` se for frontend ou `pytest` se for backend, e **nunca** um `if` dentro do laço de leitura.
4. `[paralelo]` Avaliar `max_context_messages` (padrão 10, [config/settings.py](../config/settings.py); override por canal em [ai_settings.py:41](../channels/ai_settings.py#L41)): com ~1 em 10 turnos mesclando 2 mensagens, o consumo esperado da janela sobe ~10 %. Decidir se o padrão sobe — e registrar a decisão, mesmo que seja "não muda".

**Pronto quando:** o comportamento do modelo com batch de 2 mensagens está coberto por teste, e a escolha sobre `max_context_messages` está escrita.

#### Status de execução — Fase F4
**Estado:** ✅ Executada (2026-08-28) — P2 = (a), P3 = (a)
- **O que foi feito:** `test_ai_context_shows_two_consecutive_user_turns`. `app/services/agent_run_service.py` **não foi tocado** e `max_context_messages` **continua 10**.
- **Como foi feito / decisões:** P2 (a): N turnos `user` consecutivos, sem mecanismo novo. P3 (a): manter 10 — a mescla acontece em ~1 a cada 10 turnos e subir o padrão encareceria todo turno.
- **Problemas / pendências:** ⚠️ O teste lê `ContactMemory.get_context_messages`, não passa por `run_turn`: `_encode_history_for_split` fica descoberto.
- **Verificação:** Incluído na suíte de integração.

---

### Fase F5 — Frontend: provar que nada muda 🟢 `[depende de: F1]`

**Objetivo:** confirmar que o painel já faz o certo quando o `supersedes` some — e **não** remover a maquinaria de colapso.

**Itens:**
1. `[paralelo]` `node --test` em [messages.test.js](../web/static/js/services/messages.test.js): caso novo "autoritativo **sem** `supersedes` reconcilia N bolhas otimistas por `msg_id`, sem descartar nenhuma".
2. `[paralelo]` **Manter** `dropSuperseded` e o ramo de `supersedes` em `mergeBufferedMessages` (M7): linhas mescladas legadas continuam no banco e um rollback do core tem de encontrar o cliente preparado. Atualizar só o comentário de [messages.js:109-115](../web/static/js/services/messages.js#L109), que hoje descreve o comportamento como se fosse o único.
3. `[paralelo]` Teste manual: conversa aberta enquanto o cliente manda duas mensagens (a janela t=0↔save é onde o `supersedes` importava) → duas bolhas ao vivo, duas bolhas depois do F5 do navegador, **sem** bolha órfã e **sem** duplicata.

**Pronto quando:** `node --test web/static/js/services/messages.test.js` verde e o teste manual das duas bolhas confere ao vivo e após recarregar.

#### Status de execução — Fase F5
**Estado:** ✅ Executada (2026-08-28)
- **O que foi feito:** 4 casos novos em `web/static/js/services/messages.test.js`; `dropSuperseded` e o ramo `supersedes` de `mergeBufferedMessages` MANTIDOS (M7/R6), com o porquê no comentário.
- **Como foi feito / decisões:** A maquinaria fica porque as linhas mescladas legadas continuam no banco e um rollback do core precisa encontrar o cliente preparado.
- **Problemas / pendências:** ⚠️ Achado de 2026-09-01, **em aberto**: com a conversa aberta ENTRE a 1ª e a 2ª mensagem, o buffer pode conter o otimista da 2ª antes dos autoritativos da 1ª e da 2ª, e `mergeBufferedMessages` pode devolvê-las fora de ordem. Some ao recarregar. ⚠️ Nenhum caso novo exercita o buffer MISTO (otimista + autoritativo do mesmo batch).
- **Verificação:** `node --test web/static/js/services/messages.test.js` — **40/40**.

---

### Fase F6 — `retornos`: não re-armar no mesmo turno 🟢 `[depende de: F0.2]`

**Objetivo:** fechar o R1 — o risco real que a auditoria da F0.2 tende a confirmar.

**Itens:**
1. `[sequencial]` No repositório `whatsbot-pro-plugins`, em `plugins/retornos/src/events.py`: quando `on_reply=cancel` cancelar o controle, a **mesma conversa** não pode ser re-armada por outro `message.saved` do **mesmo turno**. Formas possíveis (decidir na execução): marca de "cancelado agora" com janela curta persistida em `plugin_retornos_*`, ou checagem do último cancelamento antes de `_arm(motivo="entrada")`.
2. `[sequencial]` ⚠️ **Estado em tabela, nunca em global** — o toggle do plugin derruba o processo.
3. `[paralelo]` Teste no runner do plugin: dois `message.saved` seguidos na mesma conversa com `on_reply=cancel` → **um** cancelamento e **nenhum** re-armar.
4. `[sequencial]` Publicar com `python3 scripts/build_plugins.py retornos` e **instalar no ambiente local antes de commitar** — a cópia viva é `storages/plugins/retornos/`.

⚠️ **Ordem de deploy:** o guard do `retornos` é compatível com o core atual (com uma mensagem por batch ele nunca dispara). Pode e **deve** ir a produção **antes** do core.

**Pronto quando:** teste do plugin verde; zip publicado; `retornos` instalado no dev com a versão nova.

#### Status de execução — Fase F6
**Estado:** ✅ Executada (2026-08-28) — publicada e **em produção**
- **O que foi feito:** Guard de re-armação no `retornos`: `_REARM_GUARD_SECONDS = 15.0` + `_cancelado_por_resposta_agora`, ancorado no marcador `_MOTIVO_ON_REPLY_CANCEL` em `last_error`. Sem tabela nova.
- **Como foi feito / decisões:** Publicado como **1.20.1** e hoje em **1.21.0** no repositório de plugins; instalado em `storages/plugins/` ANTES do commit do core, como a ordem de deploy exigia.
- **Problemas / pendências:** ⚠️ O guard é **unidirecional**: protege cancelar→re-armar, mas não armar→cancelar no mesmo batch. ⚠️ A janela de 15 s é maior que o `message_batch_delay` (3 s), então ela também alcança batches DISTINTOS — o comentário do código afirma o contrário. Em produção o risco é inalcançável hoje: as duas configurações usam `on_reply='reset'` e estão inativas.
- **Verificação:** `python3 scripts/test_plugins.py retornos` — **111 Python + 33 JS**, exit 0. `plugins` de produção confirma `retornos` 1.21.0 ativo.

---

### Fase F7 — Documentação 🟢

**Objetivo:** registrar a regra onde ela é procurada, sem inflar o `CLAUDE.md`.

**Itens:**
1. `[paralelo]` [docs/UI_CONVERSA.md](../docs/UI_CONVERSA.md) — seção nova: o batch mescla **para o ciclo da IA**, não para o histórico; uma linha por mensagem; o caso 10886 como o incidente que fixou a regra; por que `dropSuperseded` continua no código.
2. `[paralelo]` [docs/PLUGIN_BUS.md](../docs/PLUGIN_BUS.md) — a cardinalidade de `message.saved`/`batch_text` passa de "1 por batch" para "1 por mensagem", alinhada a `batch_media`. **Não** é mudança de catálogo (nenhum nome novo), então **não** há bump de `WHATSBOT_API_VERSION` — mas **entra no changelog** como nota de comportamento.
3. `[paralelo]` [docs/PLUGIN_API_CHANGELOG.md](../docs/PLUGIN_API_CHANGELOG.md) — entrada no topo descrevendo a mudança de cardinalidade e o que um assinante deve assumir.
4. `[sequencial]` `CLAUDE.md` — **até 2 linhas**, na seção "Fluxo de mensagens (webhook)": a regra + o ⚠️ de que a mescla não é mais a linha do banco, com ponteiro para o guia.
5. `[sequencial]` `venv/bin/python -m pytest tests/contracts/test_docs_hygiene.py`.

**Pronto quando:** os quatro documentos atualizados e `test_docs_hygiene.py` verde.

#### Status de execução — Fase F7
**Estado:** ✅ Executada (2026-08-28), revisada em 2026-09-01
- **O que foi feito:** `CLAUDE.md` (a regra em 1 linha), `docs/UI_CONVERSA.md` (o incidente e o porquê de `dropSuperseded`), `docs/PLUGIN_BUS.md` (cardinalidade) e `docs/PLUGIN_API_CHANGELOG.md` (entrada sem bump).
- **Como foi feito / decisões:** A regra ficou no `CLAUDE.md`; o mecanismo, a medição e os números de produção no guia.
- **Problemas / pendências:** 🔴 **Em aberto — o bump.** A tabela de política do próprio changelog (linha 25) lista como **MINOR**: *"ampliar o conjunto de situações em que um evento existente é emitido"*, que é exatamente o que aconteceu. A entrada nova afirma "sem bump" 18 linhas abaixo dela. **Decidir se `WHATSBOT_API_VERSION` vai a 1.9.0.** ⚠️ Também não documentado: a mudança de cardinalidade do webhook de SAÍDA (`docs/API_REST.md`), a nova semântica de `filter.conversation.before_reopen` (N avaliações, texto por mensagem) e a cardinalidade de `message.persisted`, que também foi de 1 para N.
- **Verificação:** Corrigidos em 2026-09-01: o passo 3 do `CLAUDE.md` ainda dizia que as mensagens "são juntadas em uma só" (contradizendo o ⚠️ sete linhas abaixo) e a docstring de `build_inbound_saved_message` ainda descrevia a mescla no presente. `tests/contracts/test_docs_hygiene.py` e `test_plugin_api_surface.py` verdes.

---

## 5 — Riscos e cuidados

| # | Ponto | Risco | Mitigação |
|---|---|---|---|
| R1 | `retornos` com `on_reply=cancel` | **O mais grave.** O 1º evento cancela o controle; o 2º não acha controle ativo, cai no laço "sem controle → `_arm(motivo='entrada')`" e **re-arma** ([events.py:136-153](../storages/plugins/retornos/events.py#L136)). Um cliente que responde com duas mensagens religa o follow-up que ele acabou de desligar. | F6, com deploy **antes** do core |
| R2 | `protocolos` · `_skip_open_matches` | A regra "ignorar abertura" passa a ser testada por mensagem. Uma regex que casava o bloco inteiro pode deixar de casar — e abrir protocolo onde não abria | F0.2 item 1; se confirmado, ajustar a regex do cliente ou aplicar a regra ao texto combinado dentro do plugin |
| R3 | Caminho quente | N `INSERT` + N broadcasts + N eventos de bus por batch, em vez de 1 | Já é o custo do ramo de mídia hoje. Medir o tempo do ciclo antes/depois na F1; o `combined` continua sendo **uma** chamada de LLM |
| R4 | Janela de contexto da IA | N linhas consomem N posições de `max_context_messages` (padrão 10) | F4 item 4; ordem de grandeza medida: ~1 em 10 turnos, ~10 % a mais de consumo |
| R5 | `message.saved` como contrato | Plugin de terceiro que assuma "1 evento por turno" quebra em silêncio | F7 itens 2 e 3; a assimetria já existia (`batch_media` sempre foi N) |
| R6 | Rollback do core | Voltar o core sem voltar o frontend | M7: `dropSuperseded` **permanece**; um autoritativo sem `supersedes` é no-op no cliente |
| R7 | Linhas legadas | Conversas antigas continuam com bolhas mescladas e citações órfãs | D3 — sem retroatividade, documentado no guia |
| R8 | Teste de caracterização invertido | Reescrever A/B/C sem explicar por quê apaga a memória da mudança | F1 item 5: a inversão vai no **mesmo commit**, com comentário |
| R9 | Suíte concorrente | Duas suítes PostgreSQL em paralelo recriam o mesmo schema `public` | Conferir `pg_stat_activity` antes de culpar o código |

---

## 6 — Perguntas em aberto

**P1 — `message.saved` deve disparar N vezes, ou N vezes + 1 agregado?**
✅ **DECIDIDA na F0.2 (2026-08-28): (a)** — N eventos, sem agregado.
Contexto: hoje `batch_text` emite 1 por batch e `batch_media` emite 1 por item. Assinantes reais: `protocolos`, `retornos`, `telegram`, `debug_bus` (§2.7).
(a) **N eventos, sem agregado** — o evento passa a descrever o que aconteceu de fato, e a cardinalidade fica igual à da mídia. Custo: R1 e R2.
(b) **N eventos + 1 agregado com `source` novo** (ex. `batch_text_combined`) — preserva os assinantes atuais intactos. Custo: dois vocabulários para o mesmo fato, para sempre, e um nome novo de catálogo (MINOR no changelog).
**Recomendação: (a).** A (b) preserva exatamente o acoplamento que este plano existe para desfazer, e os dois assinantes de risco precisam de ajuste de qualquer forma — R1 é um bug latente que a mídia já podia disparar hoje.

**P2 — o que o modelo deve ver: N turnos `user` ou um turno combinado?**
✅ **DECIDIDA na F4 (2026-08-28): (a)** — N turnos `user` consecutivos; `agent_run_service.py` não foi tocado.
Contexto: §2.5 — o `combined` passado ao handler é ignorado; quem mescla para a IA é a linha do banco.
(a) **N turnos `user` consecutivos** — nenhum mecanismo novo; é exatamente o que o modelo já vê hoje quando dois batches acontecem sem resposta da IA entre eles. Custo: consumo da janela (R4).
(b) **Colapsar turnos `user` consecutivos ao montar o contexto** — mantém a entrada do modelo byte a byte igual à de hoje. Custo: colapsa também mensagens que **não** eram do mesmo batch e que hoje o modelo vê separadas — muda comportamento fora do escopo.
(c) **Passar o `combined` explicitamente como turno atual** e excluir do histórico as linhas recém-salvas. Custo: o mais invasivo; mexe em `run_turn` e cria duas fontes para o mesmo texto.
**Recomendação: (a).** É a única que não inventa mecanismo, e a forma "N mensagens seguidas do cliente" já é rotina no histórico. A (b) só se a F4 medir degradação real de resposta.

**P3 — `max_context_messages` sobe?**
✅ **DECIDIDA na F4 (2026-08-28): (a)** — `max_context_messages` mantido em 10.
Se a P2 = (a), a janela efetiva encolhe ~10 % em conversas com mescla. (a) manter 10 e observar; (b) subir o padrão. **Recomendação: (a)** — subir o padrão encarece **todo** turno para resolver um caso em dez; o override por canal já existe para quem precisar.

---

## 7 — Apêndice: arquivos que o executor vai tocar

**Backend (core)**
- [app/services/messaging_service.py](../app/services/messaging_service.py) — M1–M5 (o laço), linhas 1384-1470
- [app/services/agent_run_service.py](../app/services/agent_run_service.py) — só se a P2 escolher (b) ou (c)

**Frontend**
- [web/static/js/services/messages.js](../web/static/js/services/messages.js) — apenas comentários (M7)
- [web/static/js/services/messages.test.js](../web/static/js/services/messages.test.js) — caso novo

**Testes**
- `tests/integration/test_batch_message_identity.py` — **novo** (F0.1, F2, F3)
- [tests/integration/test_inbound_provider_ts_ordering.py](../tests/integration/test_inbound_provider_ts_ordering.py) — conferir M4/M5 do plano 129
- [tests/integration/test_quoted_message_hydration.py](../tests/integration/test_quoted_message_hydration.py) — conferir
- [tests/core/test_realtime_broadcast.py](../tests/core/test_realtime_broadcast.py) — os testes de `supersedes` continuam válidos (a função mantém o parâmetro)

**Plugin (repositório `whatsbot-pro-plugins`)**
- `plugins/retornos/src/events.py` + testes — F6

**Documentação**
- [docs/UI_CONVERSA.md](../docs/UI_CONVERSA.md) · [docs/PLUGIN_BUS.md](../docs/PLUGIN_BUS.md) · [docs/PLUGIN_API_CHANGELOG.md](../docs/PLUGIN_API_CHANGELOG.md) · `CLAUDE.md` (≤2 linhas)

---

## 8 — Checklist de verificação

- [x] `venv/bin/python -m pytest` verde no Postgres de teste — **4 falhas, nenhuma deste plano**: as 3 pré-existentes conhecidas (cadeia do Alembic ×2 + matriz de auditoria, reproduzidas idênticas num worktree limpo do `HEAD` sem este patch) e `test_f4_inbound_save_failure_leaves_a_trace`, que **passa isolada** — flake dependente de ordem
- [x] `venv/bin/python -m pytest tests/integration/characterization/` verde **sem edição** — nenhum teste `.py` foi tocado pelo plano; a única falha ali é a matriz de auditoria, uma das 3 pré-existentes
- [x] `venv/bin/python -m pytest tests/contracts/` verde — `test_docs_hygiene.py` e `test_plugin_api_surface.py` rodados também à parte, no estado final
- [x] `node --test web/static/js/services/messages.test.js` verde — **40/40**
- [x] Runner do plugin: `python3 scripts/test_plugins.py retornos` — **111 Python + 33 JS**, exit 0
- [ ] ⏸️ Duas mensagens seguidas do WhatsApp → **duas** bolhas ao vivo **e** após F5 — *manual, exige aparelho real; verificar após o deploy*
- [ ] ⏸️ Responder (botão direito) à **primeira** das duas → a citação mostra o texto certo, e só o dela — *manual*
- [ ] ⏸️ A resposta escrita entre as duas renderiza **entre** elas após recarregar — *manual*
- [x] Nenhuma migration nova; `WHATSBOT_API_VERSION` inalterado em [plugins/semver.py](../plugins/semver.py) — ⚠️ **mas ver a pendência do bump na F7**: a política escrita no changelog classifica esta mudança como MINOR
- [x] `retornos` publicado e **instalado** em `storages/plugins/` antes do commit do core — 1.21.0, e já ativo em produção
- [x] Modo escuro: nada de UI nova — **N/A**

---

## 9 — Pendências abertas ao fim da execução

Levantadas pela auditoria adversarial de **2026-09-01** (6 dimensões, cada achado submetido a 3 refutadores independentes). As três primeiras foram confirmadas por leitura direta do código; **nenhuma bloqueou o envio**, e todas seguem abertas.

| # | Pendência | Onde | Gravidade |
|---|---|---|---|
| 1 | `suppress_ai_on_ignored` passa a ver só a ÚLTIMA mensagem do batch — a IA responde onde antes calava | `protocolos` `logic.py:6118` | alta |
| 2 | Dois `message.saved` do mesmo batch correm em paralelo (`asyncio.create_task`) sobre um get-or-create de ciclo sem índice único | `protocolos` + `plugins/events.py:472` | alta |
| 3 | O bump: a política do changelog (linha 25) classifica "ampliar o conjunto de situações em que um evento existente é emitido" como **MINOR** | `docs/PLUGIN_API_CHANGELOG.md` | média |
| 4 | Webhook de SAÍDA passa a entregar N vezes por batch, com texto parcial — não documentado em `docs/API_REST.md` | `server/webhook_dispatcher.py:172` | média |
| 5 | As 7 subqueries do preview da sidebar têm o mesmo empate de `ts` que a F1 corrigiu no histórico do LLM | `db/repositories/conversation_query.py` | média |
| 6 | `mergeBufferedMessages` pode devolver o batch fora de ordem quando a conversa é aberta no meio dele (some ao recarregar) | `web/static/js/services/messages.js` | baixa |
| 7 | Guard do `retornos` é unidirecional e a janela de 15 s alcança batches distintos | `retornos` `events.py` | baixa |
| 8 | Lacunas de teste: mesmo-batch não é provado, `reply_to_msg_id` por linha e `executions.input_text` sem cobertura | `tests/integration/test_batch_message_identity.py` | baixa |
