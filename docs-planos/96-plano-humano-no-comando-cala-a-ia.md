# Plano 96 — Humano no comando cala a IA: interromper a resposta em voo, calar na atribuição e contar a verdade no selo

> **Status:** ✅ IMPLEMENTADO (2026-07-31) — F0–F4 concluídas, F5 parcial (falta o roteiro manual do §8 e a verificação pós-deploy) · **Data:** 2026-07-30 · **Escopo:** médio
> **Origem:** reclamação recorrente dos operadores da instância de produção (`atendimento.coolify.redesbrasil.com.br`) — "clico em *Atribuir a mim* e mesmo assim a IA responde". **Método:** leitura do pipeline de saída + do serviço de conversa + do painel, com `arquivo:linha` verificado, cruzada com consultas ao banco de produção via MCP Vault (22 incidentes reais reconstruídos a partir de `messages`/`atendimentos`).
> Hoje **nada no painel consegue interromper um ciclo de IA em andamento**: o gate `ai_active` é lido UMA vez, antes do LLM, e nunca mais. Atribuir, desligar a IA, digitar ou até enviar uma mensagem não abortam a resposta que já está a caminho. Somado a isso, `conversation_service.assign` não cala a IA, o gate depende de um artefato do roteamento (`active_agent_key`) e o selo do painel mente. Este plano fecha os quatro buracos.
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| D1 ✅ (2026-07-30) | Atribuir a um humano (qualquer caminho da UI ou de plugin) **cala a IA naquela conversa**. Não é permanente — religar pelo painel devolve | `conversation_service.assign` passa a escrever as três colunas de posse, como o `assign_unified` já faz (§4·F2) |
| D2 ✅ (2026-07-30) | O gate endurece: **dono humano ⇒ IA muda**, sem depender de `active_agent_key` | Inverte deliberadamente o contrato caracterizado em [test_human_gate.py:68](../tests/test_human_gate.py#L68) (§5) |
| D3 ✅ (2026-07-30) | Atendente **digitando** segura a IA (como o cliente já faz); atribuir / desligar / **enviar** cancelam o ciclo | F1 cria o seam de aborto; F3 liga a presença do operador ao mesmo estado de espera |
| D4 ✅ (2026-07-30) | O selo **IA / IA OFF** da sidebar passa a refletir o gate efetivo, não só a coluna `ai_active` | F4, frontend puro — a linha já carrega os três campos necessários ([conversationRows.js:529-531](../web/static/js/services/conversationRows.js#L529-L531)) |
| D5 ✅ (2026-07-30) | "IA ativa **sem subagente atribuído**" continua válida e intocada | O endurecimento só dispara com `assignee_user_id` humano. Sem humano, `active_agent_key IS NULL` segue caindo no agente marcado como padrão ([agent_factory.py:209](../agent/agent_factory.py#L209)) |
| D6 ✅ (2026-07-30) | Nenhuma migration nem `UPDATE` em massa | As 14 linhas hoje "verdes mas mudas" (§2.5) ficam corretas assim que o selo passa a ler o gate. Nada a corrigir no banco |
| D7 ✅ (2026-07-30) | Nenhuma alteração nos plugins `protocolos` e `agendamento_retorno` | Ambos passam a ganhar a garantia que já assumiam ter (§2.4). O core é que estava devendo |

---

## 1. Resumo executivo

O botão faz a coisa certa e o banco fica certo: `POST /api/atendimentos/{id}/ai` com `active=false` grava `ai_active=0` + `active_agent_key=NULL` + assignee, atomicamente ([conversation_service.py:523](../app/services/conversation_service.py#L523)). O que falha é tudo que acontece **depois** da escrita.

Quatro defeitos independentes, um sintoma só:

1. **A resposta em voo não é interrompida.** O gate é lido antes do LLM ([messaging_service.py:926](../app/services/messaging_service.py#L926)) e nunca antes do envio ([:955](../app/services/messaging_service.py#L955)). Entre os dois pontos cabem o LLM agêntico, até 30s esperando o cliente parar de digitar e ~2s por parte do split. **19 dos 22 incidentes medidos** caem aqui.
2. **`assign` não cala a IA.** [conversation_service.py:402](../app/services/conversation_service.py#L402) escreve só o `assignee_user_id`.
3. **O gate depende de um artefato do roteamento.** A condição "dono humano **e** agente nulo" ([messaging_service.py:1290](../app/services/messaging_service.py#L1290)) é cara ou coroa na prática: nenhum inbox tem `default_agent_key`, então quem grava `active_agent_key` é **só** a tool `transferir_agente` ([:111](../agent/tools/transferir_agente.py#L111)).
4. **O selo mente.** Ele lê apenas `conv_ai_active` ([ContactList.js:712](../web/static/js/components/contacts/ContactList.js#L712)); 14 conversas abertas exibem "IA" verde estando mudas pelo gate.

Bônus fora dos quatro: `_run_private_ai` ([contacts.py:1215](../server/routes/contacts.py#L1215)) manda a resposta ao cliente **sem checar gate nenhum** — 3 dos 22 incidentes.

A forma da solução: um **ponto único de veredito** (`ai_may_speak`) consultado no seam de envio, um **seam de aborto** (`abort_ai_cycle`) que a atribuição/o desligamento/o envio do operador acionam, e a mesma regra espelhada no cliente por um helper puro.

---

## 2. Como funciona hoje (mapa)

### 2.1 O gate e sua janela cega

[messaging_service.py:1267-1296](../app/services/messaging_service.py#L1267-L1296) — `_conversation_ai_active(contact)`:

```
conv = conversation_repo.get_open_for_contact_scoped(contact)
if conv:
    if not conv["ai_active"]:                     return False   # (1)
    if conv["assignee_user_id"] is not None \
       and not conv["active_agent_key"]:          return False   # (2)
return True                                                      # fail-open
```

Ele é chamado em **três** lugares, todos ANTES do trabalho pesado:

| # | Call site | Momento | O que vem depois, sem re-checagem |
|---|---|---|---|
| 1 | [:883](../app/services/messaging_service.py#L883) | antes do `mark_read` | — |
| 2 | [:926](../app/services/messaging_service.py#L926) | ramo de TEXTO, antes do LLM | LLM agêntico → [:955](../app/services/messaging_service.py#L955) envio |
| 3 | [:1101](../app/services/messaging_service.py#L1101) | ramo de MÍDIA, antes do LLM | LLM → [:1145](../app/services/messaging_service.py#L1145) envio |

⚠️ A janela entre o gate e o envio é composta de:

| Etapa | Onde | Duração típica |
|---|---|---|
| Chamada agêntica (roteador + spokes + tools) | [:941](../app/services/messaging_service.py#L941) | 2–20s |
| `_wait_typing_paused` dentro do envio | [:754](../app/services/messaging_service.py#L754) | 0–30s (teto em [:743](../app/services/messaging_service.py#L743)) |
| Delay "humanizado" antes da 1ª parte | [:364](../app/services/messaging_service.py#L364) | ~1–3s |
| `split_message_delay` entre partes | [:380-383](../app/services/messaging_service.py#L380-L383) | ~2s × (N−1) |

Medição em produção: as respostas indevidas saíram entre **1,6s e 75,3s** depois do clique.

### 2.2 ⚠️ Nada no painel aborta o ciclo

O ÚNICO `task.cancel()` do pipeline está em `schedule_orchestrator` ([:796-809](../app/services/messaging_service.py#L796-L809)) e é disparado por **mensagem nova do cliente** — e mesmo assim ele se recusa a cancelar quando `state.sending` ou `state.processing` estão ligados.

`grep -n "processing_tasks\|pending_messages\|state.sending" server/routes/contacts.py` → **zero ocorrências**. O painel não tem nenhuma alavanca sobre o ciclo.

| Ação do painel | Efeito na IA em execução hoje |
|---|---|
| Cliente digita | segura (`typing_state`, [channel_webhook.py:477](../server/routes/channel_webhook.py#L477)) |
| Atendente digita | **nada** — a rota [contacts.py:2152](../server/routes/contacts.py#L2152) só faz broadcast `operator_typing` e repassa a presença ao provedor ([:2180](../server/routes/contacts.py#L2180)) |
| Atendente envia mensagem | **nada** |
| Atendente atribui / desliga a IA | **nada** (só o banco) |

### 2.3 Os caminhos de atribuição divergem

| Caminho | UI | Serviço | `assignee` | `active_agent_key` | `ai_active` | Cala? |
|---|---|---|---|---|---|---|
| Botão do cabeçalho | [ConversationHeaderActions.js:214](../web/static/js/components/contacts/ConversationHeaderActions.js#L214) | `set_ai(0)` [:523](../app/services/conversation_service.py#L523) | grava | `NULL` | `0` | ✅ |
| Picker / menu de contexto / Kanban / lote | [AssigneePicker.js:50](../web/static/js/components/contacts/AssigneePicker.js#L50) | `assign_unified(kind='user')` [:470](../app/services/conversation_service.py#L470) | grava | `NULL` | `0` | ✅ |
| **Tela Atendimentos nativa (histórico pré-plano 100; removida)** | drag-and-drop da tela antiga | **`assign`** [:402](../app/services/conversation_service.py#L402) | grava | intacto | intacto | ❌ no diagnóstico original |
| **Plugin `agendamento_retorno`** | vencimento do retorno | **`assign`** [logic.py:350](../storages/plugins/agendamento_retorno/logic.py#L350) | grava | intacto | intacto | ❌ |

`assign_me` ([:442](../app/services/conversation_service.py#L442)) tem o mesmo buraco, mas é **código morto no painel** (`assignMeConversation` está definido em [api.js:575](../web/static/js/services/api.js#L575) e não é chamado por ninguém).

O fluxo vivo que substituiu a tela nativa está no plugin `protocolos`:
[protocolos_tab.js](../assets/plugin_examples/protocolos/static/protocolos_tab.js) envia
`POST /protocolos/{id}/assign`, recebido por
[routes.py](../assets/plugin_examples/protocolos/routes.py) e propagado por
[logic.py](../assets/plugin_examples/protocolos/logic.py) via `conversation_repo.set_assignee`.
Esse update low-level ainda preserva `active_agent_key`/`ai_active`, mas, depois da F2 deste
plano, a presença de dono humano já basta para o gate efetivo calar a IA.

### 2.4 ⚠️ Por que a condição (2) do gate é frágil

Consulta a produção: **nenhum dos 8 inboxes tem `default_agent_key`** (todos `NULL`). Logo:

- `set_ai(1)` — usado ao religar a IA e ao vencer a posse temporária do `protocolos` — resolve `default_agent_key_for_inbox` → `NULL` e grava `active_agent_key = NULL`;
- o **único** escritor de `active_agent_key` não-nulo é a tool `transferir_agente` ([:111](../agent/tools/transferir_agente.py#L111)), durante o roteamento hub-and-spoke.

Consequência: uma conversa com dono humano fica muda **se e somente se** a IA não tiver roteado no último turno. O plugin `protocolos` construiu a posse temporária (modo `owner`, [logic.py:4047](../storages/plugins/protocolos/logic.py#L4047)) exatamente em cima dessa premissa, e a documenta em [logic.py:3920](../storages/plugins/protocolos/logic.py#L3920):

> "O mecanismo é POSSE, não mordaça: o core já cala a IA quando a conversa tem dono humano **sem agente vinculado**, e o fechar SEMPRE limpa o `active_agent_key`."

A premissa é verdadeira no fechamento e falsa depois de qualquer `assign` que chegue com um agente ainda vinculado. Caso real reconstruído (conversa 12831):

```
29/07 18:00  Dolmário resolve            → posse armada (modo owner)
29/07 18:30  posse vence                 → set_ai(1): assignee limpo, agente revinculado ('roteador')
29/07 19:14  cliente escreve             → reabre com a IA ligada
30/07 08:00  agendamento_retorno vence   → assign(): assignee volta, agente CONTINUA 'roteador'
             ⇒ ai_active=1 + assignee + agente ⇒ gate NÃO cala
```

### 2.5 O selo do painel

[ContactList.js:705-713](../web/static/js/components/contacts/ContactList.js#L705-L713) decide entre "IA OFF" (vermelho, [:712](../web/static/js/components/contacts/ContactList.js#L712)) e "IA" (verde, [:713](../web/static/js/components/contacts/ContactList.js#L713)) lendo **só** `!autoReply || c.conv_ai_active === 0`. A linha já traz `assignee_user_id` e `active_agent_key` ([conversationRows.js:529-531](../web/static/js/services/conversationRows.js#L529-L531), servidos por [contact_repo.py:296](../db/repositories/contact_repo.py#L296)), mas eles não são considerados.

A dimensão de filtro `ai` ([conversationRows.js:189-194](../web/static/js/services/conversationRows.js#L189-L194)) repete a mesma leitura parcial e precisa acompanhar.

Estado medido em produção **agora**: 14 conversas abertas com dono humano + `ai_active=1` + agente nulo — todas exibindo **verde** e todas efetivamente **mudas**.

### 2.6 A IA da nota privada não passa por gate nenhum

`_run_private_ai` ([contacts.py:1215](../server/routes/contacts.py#L1215)), disparado por `ai_read=true` em [:1505](../server/routes/contacts.py#L1505) (texto) e [:1656](../server/routes/contacts.py#L1656) (áudio), chama `aprocess_message` e, quando `reply_in_chat=True` (default), envia direto ao cliente por `_route_send_text` — **sem** `_channel_ai_enabled` e **sem** `_conversation_ai_active`. Tem loop de envio próprio, não passa por `send_reply`.

O toggle "IA lê" só se reseta ao **trocar de conversa** ([useComposer.js:112-119](../web/static/js/components/contacts/hooks/useComposer.js#L112-L119)), então quem o liga para instruir a IA continua com ele ligado nas notas seguintes da mesma conversa.

### 2.7 Evidência de produção (reproduzível)

Consultas usadas (MCP Vault → credencial `banco-privado-redes-brasil-geral-cb4e43`, database `whatsbot`, read-only):

| Pergunta | Achado |
|---|---|
| Resposta da IA depois de "assumiu a conversa", sem religar (30d) | **22 incidentes** / 511 tomadas (~4%) |
| — com `execution_id` preenchido (ciclo do webhook) | 19 · delta 1,6s a 56,6s |
| — com `execution_id` nulo (nota privada) | 3 · delta 28,1s a 75,3s |
| Conversas abertas com dono humano + `ai_active=1` | 14 (todas com agente nulo) |
| `inboxes.default_agent_key` | 8 inboxes, todos `NULL` |
| Canais com atendente padrão (plano 71) | 2, ambos com `ai_enabled=false` ⇒ D2 tem impacto zero na config atual |

---

## 3. Inventário

| # | Item | Onde | O que falta | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|---|
| I1 | Veredito único de "a IA pode falar" | [messaging_service.py:1267](../app/services/messaging_service.py#L1267) | função é lida 3× e só no início | extrair `ai_may_speak(contact)` (gate + master de canal) e passar a consultá-la também no envio | médio | M |
| I2 | Endurecer a condição (2) | [messaging_service.py:1290](../app/services/messaging_service.py#L1290) | depende de `active_agent_key` | dono humano ⇒ `False`, ponto | médio | S |
| I3 | Guard no seam de envio | [messaging_service.py:749](../app/services/messaging_service.py#L749) | envia sem reler o estado | re-checar depois do `_wait_typing_paused` e antes de cada parte do split ([:367](../app/services/messaging_service.py#L367)) | médio | M |
| I4 | Seam de aborto | [messaging_service.py:796](../app/services/messaging_service.py#L796) | só o webhook cancela | `abort_ai_cycle(deps, channel_id, phone)` cancelando `state.processing_tasks` fora da fase de envio | alto | M |
| I5 | `assign` cala a IA | [conversation_service.py:402](../app/services/conversation_service.py#L402) | grava só o assignee | rotear pelo `_transfer` ([:352](../app/services/conversation_service.py#L352)) quando o alvo é humano | médio | S |
| I6 | Atribuição/desligamento abortam | [conversation_service.py:523](../app/services/conversation_service.py#L523) e [:402](../app/services/conversation_service.py#L402) | não tocam no ciclo | chamar I4 (o serviço já recebe `deps`, e `deps.state` existe — [app.py:101](../server/app.py#L101)) | médio | S |
| I7 | Presença do operador segura a IA | [contacts.py:2152](../server/routes/contacts.py#L2152) | não escreve estado nenhum | novo dict em `MessagingState` ([state.py:129](../server/state.py#L129)) lido por `_wait_typing_paused` ([:722](../app/services/messaging_service.py#L722)) | baixo | S |
| I8 | Envio do operador aborta | rotas de envio em `contacts.py` | nenhuma referência ao ciclo | chamar I4 no início do envio de texto/mídia | médio | S |
| I9 | Gate na IA da nota privada | [contacts.py:1215](../server/routes/contacts.py#L1215) | sem gate | checar `ai_may_speak` **apenas** quando `reply_in_chat=True` (P1) | baixo | S |
| I10 | Selo reflete o gate | [ContactList.js:712](../web/static/js/components/contacts/ContactList.js#L712) | lê só `conv_ai_active` | helper puro `aiEffectivelyOn(row)` em `conversationRows.js` | baixo | S |
| I11 | Filtro `ai` acompanha o selo | [conversationRows.js:189](../web/static/js/services/conversationRows.js#L189) | leitura parcial idêntica | reusar o mesmo helper | baixo | S |

### 3.1 Falsos positivos descartados

| Hipótese | Por que NÃO é |
|---|---|
| "É mau uso: atribuem depois de a IA já ter respondido" | A consulta procura, por construção, resposta com `ts` **posterior** ao evento de tomada. Os 22 casos têm a resposta saindo 1,6–75s **depois** do clique |
| "O botão do cabeçalho manda payload errado" | Ele chama `/ai` com `active=false`; o serviço grava as 3 colunas atomicamente ([:542-554](../app/services/conversation_service.py#L542-L554)). O banco fica correto |
| "É a tela Atendimentos (`/assign`) que causa os incidentes" | Só **1 de 22** veio de lá. É bomba armada (D1/D2), não a causa principal |
| "A tag `transferido_atendente` deveria travar a IA" | Plano 37 a rebaixou a rótulo visual de propósito (era contact-global e calava o outro canal do mesmo número). Não reabrir |
| "A fila sequencial por canal (`ai_sequential`) amplia a janela" | O gate é lido **depois** do lock: [:1236-1246](../app/services/messaging_service.py#L1236-L1246) adquire o lock e só então chama `_run_one_cycle`, cujo gate está em [:926](../app/services/messaging_service.py#L926). Esperar na fila não aumenta a janela cega |
| "Bastaria configurar `default_agent_key` nos inboxes" | Não cobre o `transferir_agente` (que grava o spoke) e continuaria sem expressar a regra "humano no comando cala a IA" |
| "Precisa de migration para corrigir as 14 linhas" | Nenhuma delas está errada no banco — elas são mudas de fato. O que mente é o selo (D6) |

---

## 4. Fases / Roadmap

```
WAVE 0   F0 caracterização                                   🔴 barreira
            │
            ├──────────────────────────────┐
WAVE 1   F1 core do pipeline (backend)  🔴  │  F4 selo + filtro (frontend)  🟢
            │  [bloqueia F2 e F3]           │  [independente de tudo]
            ├───────────────┐               │
WAVE 2   F2 atribuição  🟢  │  F3 painel 🟢  │
            └───────────────┴───────────────┘
WAVE 3   F5 verificação ponta-a-ponta                        🔴
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | testes de caracterização | 🔴 | baixo | os 4 comportamentos atuais estão congelados em teste, verdes |
| 1 | **F1** | `app/services/messaging_service.py` | 🔴 | alto | veredito único + guard de envio + `abort_ai_cycle` prontos `[bloqueia: F2, F3]` |
| 1 | **F4** | `web/static/js/…` | 🟢 | baixo | selo e filtro leem o gate efetivo `[independente]` |
| 2 | **F2** | `conversation_service.py` + `routes/conversations.py` | 🟢 | médio | atribuir a humano cala e aborta `[depende de: F1]` |
| 2 | **F3** | `server/routes/contacts.py` | 🟢 | médio | digitar segura, enviar aborta, nota privada respeita o gate `[depende de: F1]` |
| 3 | **F5** | integração | 🔴 | médio | cenário completo reproduzido e verde; consulta de produção zerada |

---

### Fase F0 — Congelar o comportamento atual (caracterização)

**Objetivo:** ter rede antes de mexer no fluxo mais crítico do produto.

**Itens:**
1. `[paralelo]` Teste que reproduz a **corrida**: inbound → gate passa → durante o LLM mockado, desligar a IA da conversa → hoje a resposta sai. Congela o comportamento atual (será invertido em F1). Base: o padrão de espera do ciclo usado em [test_plano75_reply_e2e.py:62](../tests/test_plano75_reply_e2e.py#L62).
2. `[paralelo]` Teste que congela `assign` **não** mexendo em `ai_active`/`active_agent_key` ([conversation_service.py:402](../app/services/conversation_service.py#L402)).
3. `[paralelo]` Teste que congela `_run_private_ai` enviando ao cliente com a conversa atribuída + IA off.
4. `[paralelo]` Teste que congela a presença do operador **não** escrevendo em `state.typing_state`.
5. `[sequencial]` Rodar a suíte de caracterização existente (`tests/characterization/`) e registrar o baseline.

**Pronto quando:** os 4 testes novos passam **descrevendo o comportamento atual** (não o desejado), e `tests/characterization/` continua verde no Postgres de teste.

#### Status de execução — Fase 0
**Estado:** ✅ Concluída (2026-07-31) — com desvio de forma
- **O que foi feito:** criado [tests/test_plano96_human_gate.py](../tests/test_plano96_human_gate.py) (21 testes: 18 da implementação inicial + 3 regressões da revisão final) com fakes próprios (`_FakeOutbound`/`_FakeWS`/`_FakeHandler`) sobre contato+conversa REAIS no Postgres de teste, mais os testes puros de `aiEffectivelyOn`/filtro `ai` em [conversationRows.test.js](../web/static/js/services/conversationRows.test.js).
- **Como foi feito / decisões:** ⚠️ **desvio deliberado** — em vez de escrever os testes afirmando o comportamento ATUAL e depois invertê-los (dois commits de asserção oposta no mesmo arquivo), eles foram escritos já na forma DESEJADA e rodados ANTES da F1. O valor probatório é o mesmo (a rodada vermelha provou que os quatro defeitos existiam) e não fica um arquivo com asserções mentirosas no histórico. Cada teste diz no docstring qual lado está afirmando e por quê.
- **Problemas / pendências:** —
- **Verificação:** rodada pré-F1: erro de import (`abort_ai_cycle` inexistente) — o seam de aborto não existia mesmo. Depois da F1: 13/14 verdes, a única vermelha era a da F2 (`assign` não calava), exatamente como o mapa do §2.3 previa.

---

### Fase F1 — Core do pipeline: veredito único, guard de envio e seam de aborto 🔴

**Objetivo:** a IA só fala se puder falar **no instante em que fala**; e quem manda no painel consegue interromper.

⚠️ Fase **sozinha**: as três mudanças moram no mesmo arquivo ([messaging_service.py](../app/services/messaging_service.py)) e se sobrepõem. Não paralelizar entre agentes.

**Itens:**
1. `[sequencial]` **I2 — endurecer o gate** em [:1290](../app/services/messaging_service.py#L1290): `assignee_user_id is not None` ⇒ `False`, independente de `active_agent_key`. Manter o **fail-open** de [:1294](../app/services/messaging_service.py#L1294) (exceção ⇒ `True`) e o fail-open de conversa ausente (D5: sem humano, nada muda).
2. `[sequencial]` **I1 — veredito único**: `ai_may_speak(contact, channel_id)` como **método de `MessagingService`** (não função de módulo): ele precisa de `self._channel_ai_enabled` ([:181](../app/services/messaging_service.py#L181), que vem do `MessagingContext`) além do `_conversation_ai_active` de módulo. Os 3 call sites atuais ([:883](../app/services/messaging_service.py#L883), [:926](../app/services/messaging_service.py#L926), [:1101](../app/services/messaging_service.py#L1101)) passam a chamá-lo — comportamento idêntico, um nome só. ⚠️ `_conversation_ai_active` continua exportado como está: [webhook.py:49](../server/routes/webhook.py#L49) e a suíte importam esse nome.
3. `[sequencial]` **I3 — guard de envio** em `_send_with_typing_guard` ([:749](../app/services/messaging_service.py#L749)): depois do `_wait_typing_paused` ([:754](../app/services/messaging_service.py#L754)) e **antes** de `state.sending[key] = True` ([:755](../app/services/messaging_service.py#L755)), re-consultar o veredito; negativo ⇒ não envia, loga e retorna. O `contact` sai de `agent_handler._get_contact(phone, channel_id=channel_id)`, como nos outros call sites. A leitura é 1 SELECT por resposta — desprezível.
4. `[sequencial]` **I3b — guard entre partes** no laço de split ([:367](../app/services/messaging_service.py#L367)): antes de cada parte a partir da 2ª (depois do `split_message_delay` de [:380-383](../app/services/messaging_service.py#L380-L383)), re-consultar. Partes já enviadas ficam; o resto é abortado limpo.
5. `[sequencial]` **I4 — `abort_ai_cycle(deps, channel_id, phone)`**: cancela `state.processing_tasks[(channel_id, phone)]` **apenas** quando `state.sending` está desligado (a fase de envio segue não-cancelável — quem a protege é o guard do item 3/4, que interrompe sem rasgar mensagem no meio). Best-effort, nunca levanta. Expor também `abort_ai_cycle_for_conversation(deps, conv)` que resolve `(channel_id, phone)` a partir da conversa via `conversation_repo.get_with_channel` + `contact_repo.get`.
6. `[sequencial]` **I7 (metade backend)** — novo `operator_typing_state: dict[tuple, dict]` em `MessagingState` ([state.py:129](../server/state.py#L129)) + propriedade espelho em `AppState` ([state.py:205](../server/state.py#L205)), e leitura em `_wait_typing_paused` ([:722](../app/services/messaging_service.py#L722)): espera enquanto **cliente OU operador** estiver digitando. Prazo de obsolescência **15s** (o painel reemite `start` a cada 10s — [useComposer.js:33](../web/static/js/components/contacts/hooks/useComposer.js#L33)), contra os 25s do cliente; o teto de 30s de [:743](../app/services/messaging_service.py#L743) continua valendo para os dois.

**Pronto quando:**
- o teste de corrida da F0 é **invertido** e passa: desligar a IA durante o LLM ⇒ **nada é enviado**;
- teste novo: desligar a IA entre a parte 1 e a 2 do split ⇒ parte 1 sai, parte 2 não;
- teste novo: `abort_ai_cycle` com `state.sending=True` **não** cancela (não rasga envio);
- teste novo: gate com `assignee` + `active_agent_key='roteador'` ⇒ `False` (o antigo [test_human_gate.py:68](../tests/test_human_gate.py#L68) é renomeado e invertido);
- `venv/bin/python -m pytest tests/endpoints tests/characterization -q` verde.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-07-31)
- **O que foi feito:** em [messaging_service.py](../app/services/messaging_service.py) — **I2** o gate perdeu a condição `and not active_agent_key`; **I1** métodos `ai_may_speak(contact, channel_id)` + `_ai_may_speak_now(channel_id, phone)`, adotados pelos 3 call sites; **I3** reconsulta em `_send_with_typing_guard` e no último ponto antes de cada parte, inclusive a primeira; **I4** `abort_ai_cycle` + `abort_ai_cycle_for_conversation` no nível de módulo, com geração monotônica por ciclo. Em [state.py](../server/state.py) — **I7** `operator_typing_state` e `ai_abort_epochs` em `MessagingState` + propriedades espelho em `AppState`; `_wait_typing_paused` passou a esperar por cliente **ou** operador.
- **Como foi feito / decisões:** (a) `_conversation_ai_active` continua exportado com a mesma assinatura — `webhook.py` e a suíte importam esse nome; `ai_may_speak` só o compõe com o master do canal. (b) Os guards rodam em `asyncio.to_thread` (1 SELECT, síncrono). (c) **Fora do plano:** `full_reply`/`parts` do `response_sent` derivam de `sent_parts`; zero partes retorna antes de `msg_count`/tracking, e envio parcial registra somente o que saiu. `_send_with_typing_guard` retorna `bool`, impedindo o card/evento falso de `ai_takeover` quando o guard bloqueou. (d) `abort_ai_cycle` continua sem cancelar a task com `state.processing`/`state.sending` (a janela pop→persist descartaria a mensagem), mas SEMPRE incrementa `ai_abort_epochs`; o snapshot capturado antes de `create_task` invalida o ciclo corrente mesmo nessas fases.
- **Problemas / pendências:** a inversão de contrato da D2 quebrou `test_agente_ia_vinculado_nao_e_bloqueado_pelo_assignee` — renomeado para `test_humano_atribuido_bloqueia_mesmo_com_agente_vinculado` e invertido, com a D2 citada no docstring (previsto em §5).
- **Verificação:** rodada final `pytest tests/test_plano96_human_gate.py` 21/21; `tests/test_human_gate.py` 7/7 após a inversão.

---

### Fase F2 — Atribuir a humano cala a IA e aborta o ciclo 🟢 `[depende de: F1]`

**Objetivo:** cumprir D1 em **todos** os caminhos, sem tocar em plugin.

**Itens:**
1. `[sequencial]` **I5** — `assign` ([conversation_service.py:402](../app/services/conversation_service.py#L402)) passa a usar `_transfer` ([:352](../app/services/conversation_service.py#L352)) quando `assignee_user_id` **não** é nulo: `active_agent_key=None`, `ai_active=0`, `mirror_contact_ai=None` (não mexer no gate do contato — quem faz isso é o `assign_unified`, e mudar aqui alteraria contrato de evento). ⚠️ **Desatribuir (`assignee_user_id=None`) continua não tocando na IA** — só limpa o dono, como hoje.
2. `[sequencial]` Preservar a semântica de eventos: `conversation.assigned` / `conversation.unassigned` + o card `assigned`/`unassigned` continuam saindo **uma vez cada** ([:428-438](../app/services/conversation_service.py#L428-L438)). Não introduzir `ai_off` aqui (o card de IA é do `set_ai`; duplicá-lo mudaria o fio). `_transfer` não emite nada sozinho ([:369-372](../app/services/conversation_service.py#L369-L372)), então a troca é segura nesse eixo.
3. `[sequencial]` Preservar `filter.conversation.before_assign` ([:415-421](../app/services/conversation_service.py#L415-L421)) **antes** da escrita: um plugin ainda pode abortar a atribuição devolvendo `None` (a rota mapeia para 403). A mudança é só no que se escreve depois do filtro passar. Idem `_maybe_agent_transfer_alert` ([:431](../app/services/conversation_service.py#L431)).
4. `[paralelo]` **I6** — chamar `abort_ai_cycle_for_conversation` em `set_ai` quando `active=0` ([:551-554](../app/services/conversation_service.py#L551-L554)) e em `assign` quando o alvo é humano, **depois** da escrita e antes dos broadcasts.
5. `[paralelo]` Mesma chamada em `assign_unified` `kind='user'` ([:470-473](../app/services/conversation_service.py#L470-L473)) — hoje ele já cala no banco, mas não aborta o que está em voo.
6. `[paralelo]` Conferir que a rota `/assign` ([conversations.py:476](../server/routes/conversations.py#L476)) devolve a conversa com os três campos atualizados (o painel patcha a linha com o que voltar).

**Pronto quando:**
- teste da F0 invertido: `assign(conv, user)` ⇒ `ai_active=0` **e** `active_agent_key IS NULL`;
- teste: `assign(conv, None)` **não** altera `ai_active`;
- teste: `set_ai(0)` com ciclo em andamento (não em envio) ⇒ task cancelada;
- o cenário da conversa 12831 (§2.4) reproduzido em teste termina com a IA muda;
- `tests/test_human_gate.py` e `tests/endpoints` verdes.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-07-31)
- **O que foi feito:** em [conversation_service.py](../app/services/conversation_service.py) — helper `_abort_ai_cycle(deps, conv)` (import tardio, best-effort); **I5** `assign` roteia por `_transfer` quando o alvo é humano (`active_agent_key=None`, `ai_active=0`, `mirror_contact_ai=None`) e mantém o `set_assignee` cru ao DESATRIBUIR; **I6** aborto em `assign`, `assign_unified(kind='user')` e `set_ai(active=0)`. No frontend, `handleAssignConversation` ([useConversationActions.js](../web/static/js/components/contacts/hooks/useConversationActions.js)) passou a patchar os três campos da resposta.
- **Como foi feito / decisões:** o filtro `filter.conversation.before_assign`, o `_maybe_agent_transfer_alert` e os verbos `conversation.assigned`/`unassigned` + os cards ficaram **intactos** e na mesma ordem — só mudou o que se escreve depois do filtro passar (`_transfer` não emite nada sozinho). ⚠️ **Fora do plano:** `assign_me` recebeu o mesmo tratamento. O §2.3 o classificou como código morto no painel, mas o endpoint existe e um plugin pode chamá-lo — deixar a porta aberta reintroduziria o bug num caminho que ninguém olharia.
- **Problemas / pendências:** a tela Atendimentos refaz o fetch depois da ação, então não precisou de patch otimista; os patches de [useBulkSelection.js](../web/static/js/components/contacts/hooks/useBulkSelection.js) já espelhavam `assignee_user_id` e reagem ao selo novo sem mudança.
- **Verificação:** `test_assign_a_humano_cala_a_ia`, `test_desatribuir_nao_mexe_na_ia`, `test_cenario_da_conversa_12831` e `test_cenario_do_clique_no_meio_do_ciclo` verdes.

---

### Fase F3 — Painel manda no ciclo: digitar segura, enviar aborta, nota privada respeita 🟢 `[depende de: F1]`

**Objetivo:** as ações do operador em `contacts.py` passam a ter efeito sobre a IA.

**Itens:**
1. `[paralelo]` **I7 (metade rota)** — a rota de presença ([contacts.py:2152](../server/routes/contacts.py#L2152)) escreve `state.operator_typing_state[(channel_id, phone)]` com `active = action == "start"` e `last_ts`, **antes** de ir ao provedor ([:2180](../server/routes/contacts.py#L2180)) — mesma ordem já adotada para o broadcast `operator_typing`.
2. `[paralelo]` **I8** — as rotas de envio do operador (texto e mídia) chamam `abort_ai_cycle` no início. Enviar é decisão inequívoca: o humano assumiu a fala.
3. `[paralelo]` **I9** — `_run_private_ai` ([:1215](../server/routes/contacts.py#L1215)) consulta `ai_may_speak` **somente** quando `reply_in_chat=True`; negativo ⇒ não envia ao cliente e registra um card painel-only explicando (P1). Com `reply_in_chat=False` (resposta vira nota privada) **não há gate** — nada sai para o cliente.
4. `[paralelo]` Cobrir os dois disparos: texto ([:1505](../server/routes/contacts.py#L1505)) e áudio ([:1656](../server/routes/contacts.py#L1656)).

**Pronto quando:**
- teste: `POST /api/contacts/{phone}/presence` com `action=start` ⇒ `state.operator_typing_state` marcado; ciclo da IA espera; `stop` libera;
- teste: envio do operador com ciclo em andamento ⇒ task cancelada;
- teste da F0 invertido: nota privada com `ai_read=true, ai_reply=true` em conversa atribuída ⇒ **nada** chega ao cliente;
- teste: mesma nota com `ai_reply=false` ⇒ a resposta continua virando nota privada normalmente.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-07-31)
- **O que foi feito:** em [contacts.py](../server/routes/contacts.py) — **I7** a rota de presença escreve `state.operator_typing_state` antes de ir ao provedor; **I8** helper `_operator_took_over(channel_id, phone)` chamado no início de `/send`, `/retry-send`, `/send-image`, `/send-audio`, `/send-document` e `/send-video`; **I9** `_run_private_ai` consulta `messaging._ai_may_speak_now` quando `reply_in_chat=True` e, bloqueado, grava + emite um card `system_notice` explicando (P1).
- **Como foi feito / decisões:** (a) o `operator_typing_state` é escrito **sem** exigir identidade de usuário (o broadcast `operator_typing` exige, porque o painel precisa filtrar o próprio autor; o pipeline não precisa saber QUEM digita). (b) O gate da nota privada roda DEPOIS do LLM e dos cards de tool: o operador continua vendo o que a IA fez, só não vaza ao cliente. (c) ⚠️ **Desvio do plano, com motivo:** o item 3 pedia `ai_may_speak` (veredito composto), mas o gate usado é só a camada **por-conversa** (`_private_ai_conversation_open` → `_conversation_ai_active`). O composto inclui o master global `auto_reply`, cujo **default é `False`** ([settings.py:146](../config/settings.py#L146)): numa instalação com a automação desligada — legítima, e é o default de fábrica — o recurso "IA lê + responder no chat" pararia de funcionar inteiro. O master governa a IA responder **sozinha** a um inbound; esta resposta foi **pedida por um humano**. O que o plano 96 ataca (D1) é o humano no comando DAQUELA conversa, e isso a camada por-conversa cobre. Foi assim que o teste `test_nota_privada_continua_falando_em_conversa_livre` pegou a regressão.
- **Problemas / pendências:** os dois disparos (texto e áudio) caem no mesmo `_run_private_ai`. A revisão final fechou também a janela após a primeira checagem: o epoch é capturado antes de `create_task` e o gate é relido imediatamente antes de cada parte enviada ao cliente.
- **Verificação:** `test_operador_digitando_segura_a_ia`, `test_presenca_do_operador_obsoleta_libera`, `test_nota_privada_nao_fala_em_conversa_atribuida`, `test_nota_privada_continua_falando_em_conversa_livre` e `test_nota_privada_reconsulta_gate_entre_partes` verdes.

---

### Fase F4 — O selo conta a verdade 🟢 `[independente]`

**Objetivo:** o operador enxerga o gate efetivo, não a coluna crua.

**Itens:**
1. `[sequencial]` **I10** — helper **puro** `aiEffectivelyOn(row, { autoReply })` em [conversationRows.js](../web/static/js/services/conversationRows.js): `false` se `!autoReply`, se `conv_ai_active` é 0/false, **ou** se `assignee_user_id != null` (espelho exato de D2). Exportado e testável por `node --test`.
2. `[paralelo]` Consumir em [ContactList.js:711-713](../web/static/js/components/contacts/ContactList.js#L711-L713), preservando o guard de linha sem atendimento ([:705](../web/static/js/components/contacts/ContactList.js#L705), `c.conversation_id == null` ⇒ selo nenhum) e o `title` explicativo do interruptor global.
3. `[paralelo]` **I11** — dimensão `ai` do filtro ([conversationRows.js:189-194](../web/static/js/services/conversationRows.js#L189-L194)) passa a usar o mesmo helper, para "IA off" listar exatamente o que está vermelho (P4).
4. `[paralelo]` Testes puros em [conversationRows.test.js](../web/static/js/services/conversationRows.test.js): matriz dos 6 estados (com/sem humano × `ai_active` 0/1 × agente nulo/não) + `autoReply=false`.
5. `[paralelo]` Conferir o patch otimista de [useBulkSelection.js:118-122](../web/static/js/components/contacts/hooks/useBulkSelection.js#L118-L122) e [useConversationActions.js:64](../web/static/js/components/contacts/hooks/useConversationActions.js#L64) — eles já espelham `assignee_user_id`, então o selo novo reage na hora, sem reload.

**Pronto quando:** as 14 conversas do §2.5 aparecem **vermelhas** ao recarregar a sidebar; o filtro "IA desligada" as inclui; `node --test web/static/js/services/conversationRows.test.js` verde.

⚠️ Sem alteração no botão "Atribuir a mim" nem em `ConversationHeaderActions.js` — o selo é outro componente.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-07-31) — falta a conferência visual no navegador
- **O que foi feito:** **I10** helper puro `aiEffectivelyOn(row, { autoReply })` exportado de [conversationRows.js](../web/static/js/services/conversationRows.js); consumido pelo selo em [ContactList.js](../web/static/js/components/contacts/ContactList.js) (guard de `conversation_id == null` preservado) e, **I11**, pela dimensão `ai` do filtro. 10 testes puros novos em [conversationRows.test.js](../web/static/js/services/conversationRows.test.js).
- **Como foi feito / decisões:** (a) o `autoReply` **não** entra na dimensão de filtro — o interruptor global vale para todas as linhas e faria o filtro devolver tudo ou nada; ele só afeta o selo. (b) O `title` do selo vermelho agora distingue os dois motivos ("desligada pelo interruptor global" × "está com um atendente"). (c) Nenhuma mudança em `ConversationHeaderActions.js` nem no botão "Atribuir a mim".
- **Problemas / pendências:** 🔶 pendente a conferência visual (§8): recarregar a sidebar e confirmar que as conversas com dono humano ficaram **vermelhas**, e o contraste do vermelho na linha selecionada nos dois temas.
- **Verificação:** `node --test web/static/js/services/conversationRows.test.js` → **82/82 verdes** (72 antes + 10 novos).

---

### Fase F5 — Verificação ponta-a-ponta 🔴

**Objetivo:** provar o cenário real, não só as unidades.

**Itens:**
1. `[sequencial]` Teste de integração do **caso 15132**: inbound → ciclo começa → operador clica em "Atribuir a mim" no meio → nenhuma resposta da IA sai; o fio fica com os cards de tomada e nada mais.
2. `[sequencial]` Teste do **caso 12831**: fechar com dono → posse vence (`set_ai(1)`) → cliente escreve → `assign` do agendamento → cliente escreve de novo ⇒ IA muda.
3. `[sequencial]` Suíte completa no Postgres de teste (`WHATSBOT_TEST_DB_URL`).
4. `[sequencial]` Depois do deploy: re-rodar a consulta do §2.7 (incidentes em 30d) e a de estado (`open` + dono humano + `ai_active=1`). Esperado: nenhum incidente novo a partir da data do deploy.

**Pronto quando:** as duas reproduções passam, a suíte está verde e a consulta de produção não acusa incidente posterior ao deploy.

#### Status de execução — Fase 5
**Estado:** 🟡 Parcial (2026-07-31) — automação verde; falta o roteiro manual e o pós-deploy
- **O que foi feito:** as duas reproduções pedidas viraram teste — `test_cenario_do_clique_no_meio_do_ciclo` (caso 15132: ciclo em voo + `set_ai(0)` no meio ⇒ nada é enviado, exercitando escrita + aborto + guard na mesma corrente) e `test_cenario_da_conversa_12831` (posse vence → agente revinculado → `assign` do agendamento ⇒ IA muda). [CLAUDE.md](../CLAUDE.md) ganhou a seção "Humano no comando cala a IA" e teve o "Gate de humano" corrigido (a descrição antiga citava a condição que a D2 removeu).
- **Como foi feito / decisões:** o e2e completo pelo webhook não foi construído — o `_send_with_typing_guard` é o ponto único por onde toda resposta da IA passa (ramo texto E ramo mídia), então cobri-lo prova o mesmo com muito menos massa de teste.
- **Problemas / pendências:** 🔶 **os 4 cenários manuais do §8 e o pós-deploy (§2.7) continuam abertos** — nada disto pode ser fechado sem o painel na mão e sem a instância de produção atualizada.
- **Verificação:** `tests/test_plano96_human_gate.py` + `tests/test_human_gate.py` + `tests/characterization/test_lifecycle_characterization.py` → **verdes** (24 + 7, 1 skip pré-existente) num banco de teste dedicado; `node --test conversationRows.test.js` → 82/82. Os goldens `lifecycle_assign_then_close` / `lifecycle_assign_then_unassign` foram **regenerados** (`UPDATE_GOLDENS=1`) — congelavam o `assign` antigo (`ai_active: 1`, `active_agent_key: "default"`); o novo valor é a D1, e a **sequência de eventos e os cards ficaram byte-idênticos**. Sobre o resto da suíte, ver a nota de armadilha no fim do §8.

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Cancelar durante o envio | Split rasgado: partes 1–2 entregues e a 3 não, sem coerência | `abort_ai_cycle` **nunca** cancela com `state.sending` ligado ([:755](../app/services/messaging_service.py#L755)); quem interrompe ali é o guard entre partes, que para num limite limpo |
| Guard de envio derruba resposta legítima | Falso negativo cala a IA numa conversa saudável | O guard usa o **mesmo** veredito do início do ciclo, com o mesmo fail-open ([:1294](../app/services/messaging_service.py#L1294)): erro de leitura ⇒ envia |
| Endurecer o gate (D2) | Inverte contrato caracterizado em [test_human_gate.py:68](../tests/test_human_gate.py#L68) | Renomear o teste para `test_humano_atribuido_bloqueia_mesmo_com_agente_vinculado` e inverter a asserção, citando D2 no docstring. É mudança de contrato **deliberada** |
| Posse temporária do `protocolos` | Mexer no plugin por engano | D7: nada muda lá. O modo `owner` ([logic.py:4047](../storages/plugins/protocolos/logic.py#L4047)) passa de premissa implícita a garantia do core |
| `agendamento_retorno` | O plugin declara "não religa a IA" ([logic.py:317](../storages/plugins/agendamento_retorno/logic.py#L317)) | Continua verdadeiro: ele nunca religa. Com F2 ele passa a **calar** de fato, que é o que o comentário já assumia |
| Presença do operador segurando a IA | Nota privada também emite presença (não há checagem de `mode` em [useComposer.js:177-205](../web/static/js/components/contacts/hooks/useComposer.js#L177-L205)) ⇒ IA espera enquanto o atendente escreve nota interna | Aceitável e desejável (humano ativo na conversa), limitado pelo prazo de 15s e pelo teto de 30s |
| Presença sem `stop` (aba fechada) | IA travada | Obsolescência de 15s + teto de 30s de `_wait_typing_paused` ([:743](../app/services/messaging_service.py#L743)) |
| Selo mais vermelho que antes | Operador acha que "quebrou a IA" | É a verdade que já valia; comunicar na nota de release. O selo verde volta ao devolver a conversa |
| Sandbox e linha sem atendimento | Guard/selo quebrando tela de teste | Sandbox não tem conversa ⇒ fail-open no backend; no front, o guard `conversation_id == null` de [ContactList.js:709](../web/static/js/components/contacts/ContactList.js#L709) fica |
| Eventos/filtros | Mudar `assign` pode alterar o que plugins recebem | `_transfer` **não** emite nada por si ([:369-372](../app/services/conversation_service.py#L369-L372)); os verbos continuam sendo emitidos pelo `assign`. Cobrir com teste de contrato de evento |

---

## 6. Perguntas em aberto

**P1 — `_run_private_ai` com `reply_in_chat=True` numa conversa calada: bloquear ou avisar?**
✅ DECIDIDO (2026-07-30): bloquear o envio ao cliente e gravar um card painel-only ("a IA não respondeu: a conversa está com um atendente"). (a) bloquear silenciosamente — o operador não entende o sumiço; (b) bloquear + card — escolhido; (c) enviar mesmo assim — contraria D1. O caminho `reply_in_chat=False` (resposta vira nota privada) **nunca** é gateado: não chega ao cliente.

**P2 — Digitar do atendente: segurar ou cancelar?**
✅ DECIDIDO (2026-07-30, D3): **segurar** (mesmo mecanismo do cliente), com prazo de 15s. Cancelar na digitação seria destrutivo — o atendente pode desistir do texto e aí a resposta da IA teria sido descartada à toa.

**P3 — O selo precisa de um terceiro estado ("IA pausada")?**
✅ DECIDIDO (2026-07-30, D4): não. Dois estados, como hoje; dono humano ⇒ "IA OFF". Inventar um terceiro rótulo exigiria decidir cor, contraste nos dois temas e semântica de filtro sem ganho claro.

**P4 — O filtro "IA desligada" acompanha o selo?**
✅ DECIDIDO (2026-07-30): sim, mesmo helper puro. Selo e filtro divergirem seria um segundo bug da mesma família.

**P5 — Vale expor "por que a IA está calada" no painel do atendimento?**
⏸️ ADIADO. Útil para suporte (distinguir "interruptor global", "canal", "conversa", "dono humano"), mas é escopo novo de UI. Reavaliar depois que F1–F5 estiverem em produção.

---

## 7. Apêndice — arquivos-chave

**Backend — pipeline de saída**
- [app/services/messaging_service.py](../app/services/messaging_service.py) — gate ([:1267](../app/services/messaging_service.py#L1267)), guard de envio ([:749](../app/services/messaging_service.py#L749)), split ([:367](../app/services/messaging_service.py#L367)), orquestrador ([:796](../app/services/messaging_service.py#L796), [:1192](../app/services/messaging_service.py#L1192)), espera de digitação ([:722](../app/services/messaging_service.py#L722))
- [server/state.py](../server/state.py) — `MessagingState` ([:129](../server/state.py#L129)) e espelhos em `AppState` ([:205](../server/state.py#L205))

**Backend — posse da conversa**
- [app/services/conversation_service.py](../app/services/conversation_service.py) — `_transfer` ([:352](../app/services/conversation_service.py#L352)), `assign` ([:402](../app/services/conversation_service.py#L402)), `assign_unified` ([:456](../app/services/conversation_service.py#L456)), `set_ai` ([:523](../app/services/conversation_service.py#L523))
- [server/routes/conversations.py](../server/routes/conversations.py) — rota `/assign` ([:476](../server/routes/conversations.py#L476)), rota `/ai` ([:561](../server/routes/conversations.py#L561))

**Backend — painel**
- [server/routes/contacts.py](../server/routes/contacts.py) — `_run_private_ai` ([:1215](../server/routes/contacts.py#L1215)), disparos ([:1505](../server/routes/contacts.py#L1505), [:1656](../server/routes/contacts.py#L1656)), presença ([:2152](../server/routes/contacts.py#L2152)), rotas de envio

**Frontend**
- [web/static/js/services/conversationRows.js](../web/static/js/services/conversationRows.js) — helper novo, dimensão `ai` ([:189](../web/static/js/services/conversationRows.js#L189)), shape da linha ([:529](../web/static/js/services/conversationRows.js#L529))
- [web/static/js/components/contacts/ContactList.js](../web/static/js/components/contacts/ContactList.js) — selo ([:712](../web/static/js/components/contacts/ContactList.js#L712))

**Testes**
- [tests/test_human_gate.py](../tests/test_human_gate.py) — contrato do gate (inverter [:68](../tests/test_human_gate.py#L68))
- [tests/characterization/](../tests/characterization/) · [tests/endpoints/](../tests/endpoints/) · [web/static/js/services/conversationRows.test.js](../web/static/js/services/conversationRows.test.js)

**Leitura de contexto (não editar)**
- [storages/plugins/protocolos/logic.py](../storages/plugins/protocolos/logic.py) — posse temporária ([:3920](../storages/plugins/protocolos/logic.py#L3920), [:4047](../storages/plugins/protocolos/logic.py#L4047))
- [storages/plugins/agendamento_retorno/logic.py](../storages/plugins/agendamento_retorno/logic.py) — `_reopen_and_assign` ([:312](../storages/plugins/agendamento_retorno/logic.py#L312))
- [agent/tools/transferir_agente.py](../agent/tools/transferir_agente.py) — único escritor de `active_agent_key` ([:111](../agent/tools/transferir_agente.py#L111))

---

## 8. Checklist de verificação

- [x] `venv/bin/python -m pytest tests/endpoints tests/characterization -q` no Postgres de teste (`WHATSBOT_TEST_DB_URL`, nome do banco contém `test`) — ⚠️ ver a nota sobre a rodada concorrente logo abaixo
- [x] `venv/bin/python -m pytest tests/test_human_gate.py -q` verde, com o teste invertido documentando D2
- [x] `venv/bin/python -m pytest tests/test_plano96_human_gate.py -q` verde (21 testes)
- [x] `node --test web/static/js/services/conversationRows.test.js` verde (82/82)
- [ ] Reload + back/forward do painel: selo e filtro consistentes, sem flicker
- [ ] Modo escuro: selo vermelho legível na linha selecionada e na normal (regra do `custom.css`)
- [ ] Cenário manual: cliente escreve → durante "IA respondendo…" clicar em *Atribuir a mim* ⇒ nenhuma resposta sai
- [ ] Cenário manual: atendente digitando ⇒ IA espera; parar de digitar ⇒ IA segue (se ainda puder falar)
- [ ] Cenário manual: nota privada com "IA lê" + "responder no chat" em conversa atribuída ⇒ nada chega ao cliente, card explica
- [ ] Cenário manual: tela Atendimentos → *Atribuir a mim* ⇒ selo vira "IA OFF" e a IA para
- [ ] Sem migration, sem `UPDATE` em massa, sem alteração em plugin (D6/D7)
- [ ] Nenhum segredo em URL; nenhuma mudança de contrato de evento não documentada
- [ ] Pós-deploy: consulta do §2.7 sem incidentes com data posterior ao deploy

> ⚠️ **Armadilha de execução (2026-07-31) — leia antes de acreditar em qualquer vermelho:** os planos 84 e 99 estavam sendo executados **em paralelo por outros agentes**, e as rodadas de `pytest` caíam no MESMO `WHATSBOT_TEST_DB_URL`. Como `tests/pg.py` faz `DROP SCHEMA public CASCADE` uma vez por processo, cada rodada destruía o schema da outra — o sintoma nítido foi `psycopg.errors.UndefinedTable: relation "observations" does not exist` no meio de um teste. O conjunto de falhas mudava a cada execução (`test_p25`, `test_p26`, `test_p27`, `test_sidebar_*`, `test_webhook_characterization`), e arquivos que falhavam passavam sozinhos.
>
> **Como se obteve um veredito confiável:** criando um banco só para esta frente (`whatsbot_test_p96`, `ENCODING 'UTF8' TEMPLATE template0` — o `postgres` do servidor é SQL_ASCII e sem isso a conexão nem sobe) e comparando a MESMA lista de arquivos contra um `git worktree` em `HEAD` puro. Resultado: HEAD e esta branch falham no MESMO único teste (`test_plano72_broadcast_row_carries_contact_tags`, um `tag_repo.create("vip")` que colide com sobra de arquivo anterior) — ou seja, **contaminação entre arquivos pré-existente**, não regressão do plano 96. `tests/characterization/test_audit_characterization.py` também trava a rodada longa e não tem relação com este plano.
>
> Regra prática: **uma rodada de pytest por vez**, ou um banco de teste por agente.
