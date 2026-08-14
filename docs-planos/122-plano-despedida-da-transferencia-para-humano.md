# Plano 122 — A IA volta a se despedir ao transferir para humano (sem desfazer o plano 96)

> **Status:** ✅ **EXECUTADO** (F0–F4 em 2026-08-14; F5 aguarda o deploy) · **Data:** 2026-08-14
> · **Escopo:** pequeno-médio (core-only, sem migration, sem frontend)
> **Origem:** relatório do plugin `melhorias` — *"a IA estava transferindo para o humano, mas não avisava no chat"*.
> **Método:** leitura do código real + `git show` do commit da regressão + **medição no Postgres de produção**
> (`banco-nexus-redes-brasil`, database `whatsbot`, somente leitura). Todo `arquivo:linha` abaixo foi
> verificado nesta sessão; os números são medidos, não estimados.
>
> **Resultado:** 2 arquivos de core e 8 testes novos. O perdão `allow_self_handoff` atravessa os 3 guards e
> é derivado nos 3 caminhos de saída; a época continua intocada e **mutation-testada** nos dois sentidos
> (F4). ⚠️ Os `arquivo:linha` deste documento são os do código **ANTES** da execução — em
> `messaging_service.py` tudo abaixo de `_cycle_may_continue` desceu ~60 linhas. Use-os para entender o
> raciocínio, não para navegar: para o estado atual, `grep` por `allow_self_handoff` / `_turn_handed_off`.
>
> **O achado:** [transfer_to_human.py:87-89](../agent/tools/transfer_to_human.py) grava `ai_active=0` na
> conversa **durante** o turno. O guard do plano 96 ([messaging_service.py:872-877](../app/services/messaging_service.py))
> reconsulta o gate **depois** do LLM e descarta a resposta — que é justamente a despedida que aquele
> mesmo turno acabou de escrever. A IA cala a si mesma. A resposta não é só bloqueada: como o save só
> acontece para `sent_parts` ([:528-551](../app/services/messaging_service.py)), **nem o operador vê no
> painel** o que a IA queria dizer.
>
> **Regressão datada:** commit `78ceb73` (plano 96, 2026-07-31). Antes disso o gate era lido **uma vez**,
> antes do LLM — não havia reconsulta pós-LLM e a despedida saía normalmente.
>
> **Medido em produção** (cards `role='tool_call'` com `content LIKE '%transfer_to_human%'` × resposta
> `assistant` não-operador nos 120s seguintes):
>
> | Semana | Transferências | Com despedida |
> |---|---|---|
> | 20/07 | 178 | **178** (100%) |
> | 27/07 | 158 | 135 (corte no dia 31: 17 de 26) |
> | 03/08 | 115 | **0** |
> | 10/08 | 90 | **2** (o fail-open de "sem conversa aberta") |
>
> **226 transferências saíram sem aviso nenhum** desde 31/07. O cliente fica falando sozinho até um
> humano aparecer — caso real de 14/08 10:13 na conversa do Marcos: transferência às 10:13:28, duas
> mensagens do cliente no vazio (10:13:33 e 10:13:44), atendente só entra 10:13:48.
>
> **O que este plano NÃO faz:** afrouxar o plano 96. O discriminador já existe e é preciso — toda tomada
> humana passa por `abort_ai_cycle`, que **sempre incrementa a época** ([:1444-1450](../app/services/messaging_service.py)),
> enquanto a transferência da própria IA **não toca na época**. Perdoar só o gate de banco, mantendo a
> checagem de época, separa os dois casos sem zona cinzenta.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar
> para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| D1 | ✅ (2026-08-14) A correção é **no core**, não num plugin | Quem corta a mensagem é o guard do core ([:872-877](../app/services/messaging_service.py)). A regra do CLAUDE.md §"O que fica no core e o que vai pro plugin" governa **comportamento de negócio novo**; aqui não há negócio novo — há um guard do core cancelando uma saída do core. Um plugin só conseguiria contornar mandando **texto fixo** por fora, perdendo a redação contextual da IA (ver §7 P4, descartado) |
| D2 | ✅ (2026-08-14) A **época (`abort_epoch`) continua sendo lei** | O perdão jamais ignora a época. Isso preserva 100% do plano 96: `assign`/`assign_me`/`assign_unified`/`set_ai(0)` ([conversation_service.py:461/492/529/607](../app/services/conversation_service.py)) e o envio do operador ([contacts.py:350-362](../server/routes/contacts.py)) passam por `abort_ai_cycle`, que incrementa a época **antes de qualquer outra coisa** e mesmo quando não consegue cancelar a task |
| D3 | ✅ (2026-08-14) O perdão é **explícito no call site**, por parâmetro — nada de `ContextVar`, flag global ou estado em `AppState` | Os dois call sites já têm `result.tool_calls` em mãos ([:1079](../app/services/messaging_service.py) e [:1275](../app/services/messaging_service.py)). Parâmetro é testável isoladamente e morre no fim da chamada — não há como "vazar" para o turno seguinte |
| D4 | ✅ (2026-08-14) O card **"🤖 A IA assumiu a conversa"** continua gateado pelo predicado ESTRITO | Um turno que termina em transferência não é um takeover. `maybe_emit_ai_takeover` ([:1093-1096](../app/services/messaging_service.py) e [:1289-1292](../app/services/messaging_service.py)) **não** recebe o perdão |
| D5 | ✅ (2026-08-14) `transfer_to_human` continua gravando `ai_active=0` **na hora** | A alternativa (adiar a escrita para depois do envio) abre uma janela de corrida em que o cliente responde e a IA pega o turno de novo — trocaria um bug por outro pior. A tool não muda |
| D6 | ✅ (2026-08-14) Sem migration, sem mudança de schema, sem frontend | O sintoma é 100% de fluxo de envio no backend |

---

## 1. Resumo executivo

A IA transfere para humano e, no mesmo turno, escreve a despedida ("já vou te conectar com um atendente").
Essa despedida nunca chega ao cliente desde 31/07, porque a própria transferência fecha o portão que o
guard do plano 96 consulta logo antes de mandar.

O conserto tem uma peça só: **um perdão de escopo mínimo no guard**, que vale por uma resposta, só quando
o turno chamou `transfer_to_human`, e **só sobre o gate de banco** — a época continua valendo. Como toda
tomada humana bumpa a época e a transferência da IA não, o perdão é inaplicável exatamente nos casos que
o plano 96 existe para cobrir.

Em volta disso: cabear os **três** caminhos de saída (batch de texto, batch de mídia e IA da nota privada
— este último hoje ainda grava um aviso **factualmente errado**, dizendo que "um atendente assumiu"), e
uma suíte que trava os **dois lados** — porque o buraco existe justamente onde a suíte do plano 96 não
olhou: uma tool que fecha o próprio gate.

---

## 2. Como funciona hoje (mapa)

### 2.1 A sequência que perde a mensagem

| Ordem | Onde | O que acontece |
|---|---|---|
| 1 | [messaging_service.py:1060-1061](../app/services/messaging_service.py) | Gate **pré-LLM**: `ai_may_speak(...) and _abort_epoch(...) == abort_epoch` → ✅ passa (a conversa ainda está com a IA) |
| 2 | [:1075-1078](../app/services/messaging_service.py) | `aprocess_message` roda o AGNO |
| 3 | [transfer_to_human.py:58](../agent/tools/transfer_to_human.py) | `ctx.contact.set_ai_enabled(False)` (flag legado do contato) |
| 4 | [transfer_to_human.py:87-89](../agent/tools/transfer_to_human.py) | `conversation_repo.assign_agent(conv_id, assignee_user_id=None, active_agent_key=None, **ai_active=0**)` ⟵ **o portão fecha aqui** |
| 5 | [transfer_to_human.py:110](../agent/tools/transfer_to_human.py) | devolve `_TRANSFER_FEEDBACK` ([:44-48](../agent/tools/transfer_to_human.py)) — literalmente *"Responda ao cliente de forma curta e natural, apenas confirmando que já vai ser atendido"* |
| 6 | [agno_engine.py:460-486](../agent/agno_engine.py) | o modelo escreve a despedida; `_extract_reply` a devolve |
| 7 | [:1080](../app/services/messaging_service.py) | `broadcast_tool_calls` grava o card `🔧 transfer_to_human` e, em [:671-712](../app/services/messaging_service.py), dispara o alerta sonoro + o card *"🤖 SISTEMA pausou a IA"* |
| 8 | [:872-877](../app/services/messaging_service.py) | `_send_with_typing_guard` reconsulta → `_cycle_may_continue` → `_ai_may_speak_now` → `_conversation_ai_active` lê `ai_active=0` → **`False`** → `return False` |
| 9 | — | **a despedida evapora**: não vai ao wire ([:482](../app/services/messaging_service.py) nunca é alcançado) e não é salva ([:540-551](../app/services/messaging_service.py) só itera `sent_parts`) |

O segundo guard, o por-parte em [:465-476](../app/services/messaging_service.py), nem chega a rodar — mas
bloquearia igual. **Os dois precisam do perdão**, senão o conserto para na metade.

### 2.2 O gate e seus dois sinais (é aqui que mora a solução)

```
_cycle_may_continue(channel_id, phone, abort_epoch)     ← messaging_service.py:224-234
  ├── época divergiu?  → False     ... sinal do HUMANO  (abort_ai_cycle sempre incrementa)
  └── _ai_may_speak_now → gate DB  ... sinal de ESTADO  (ai_active / assignee / canal / global)
```

| Sinal | Quem escreve | A transferência da IA mexe? | Tomada humana mexe? |
|---|---|---|---|
| **Época** (`state.ai_abort_epochs`) | só `abort_ai_cycle` [:1444-1450](../app/services/messaging_service.py) | ❌ **não** (a tool não chama nada disso) | ✅ **sempre** — `assign`, `assign_me`, `assign_unified`, `set_ai(0)` e o envio do operador |
| **Gate de banco** (`ai_active`, `assignee_user_id`) | `transfer_to_human`, `conversation_service`, painel | ✅ sim (`ai_active=0`) | ✅ sim |

⚠️ O ponto que torna a correção segura: `abort_ai_cycle` **incrementa a época ANTES de tudo**, inclusive
quando se recusa a cancelar a task por estar em `sending`/`processing` ([:1444-1457](../app/services/messaging_service.py)).
Não existe caminho de tomada humana que mexa no gate sem mexer na época.

### 2.3 Por que prompt, configuração e plugin não resolvem

| Tentativa | Por que falha | Evidência |
|---|---|---|
| "A IA avisa **antes** de chamar a tool" | `_extract_reply` **descarta de propósito** todo texto que vem na mesma mensagem da tool call (o *chatter* pré-tool), porque concatená-lo corromperia o JSON do `split_messages` | [agno_engine.py:460-477](../agent/agno_engine.py) |
| "A IA avisa **depois**" | é o que ela já faz — e é o que o guard corta | §2.1, passo 8 |
| "Um agente anterior (vendedora) avisa" | quem fecha o portão é a **recepção**, num turno posterior; avisar antes é avisar cedo demais e no lugar errado | [transferir_agente.py](../agent/tools/transferir_agente.py) não escreve `ai_active` |
| "Desligar por configuração" | não há chave: `ai_active=0` é literal no código da tool | [transfer_to_human.py:89](../agent/tools/transfer_to_human.py) |

### 2.4 O terceiro caminho: a IA da nota privada

[`_run_private_ai`](../server/routes/contacts.py) tem gate próprio ([contacts.py:1300-1304](../server/routes/contacts.py)
→ [:336-348](../server/routes/contacts.py)) e o mesmo furo: se a IA privada com *"responder no chat"*
chamar `transfer_to_human`, a resposta é bloqueada em [:1367-1371](../server/routes/contacts.py) **e grava
um card mentindo** — `_blocked_notice` ([:1306-1323](../server/routes/contacts.py)) afirma *"um atendente
assumiu a conversa"*, o que não aconteceu.

---

## 3. Inventário do que muda

| # | Arquivo:linha | O que falta | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|
| I1 | [messaging_service.py:224-234](../app/services/messaging_service.py) `_cycle_may_continue` | não sabe distinguir "gate fechado pelo humano" de "gate fechado pela própria IA neste turno" | kwarg `allow_self_handoff: bool = False`; **época primeiro** (inalterada), e só então o perdão curto-circuita `_ai_may_speak_now` | baixo | S |
| I2 | [messaging_service.py:856-884](../app/services/messaging_service.py) `_send_with_typing_guard` | descarta a resposta inteira | kwarg homônimo, repassado a `_cycle_may_continue` [:872](../app/services/messaging_service.py) **e** adiante a `send_reply` | baixo | S |
| I3 | [messaging_service.py:465-476](../app/services/messaging_service.py) guard por-parte em `send_reply` | cortaria na parte 1/N | `send_reply` recebe o kwarg e o repassa ao `_cycle_may_continue` do laço | baixo | S |
| I4 | [messaging_service.py:1079-1096](../app/services/messaging_service.py) call site **texto** | tem `result.tool_calls` e não usa | `handoff = any(tc.get("tool") == "transfer_to_human" and not tc.get("skipped") for tc in (result.tool_calls or []))` → passa em [:1089](../app/services/messaging_service.py). `maybe_emit_ai_takeover` [:1093-1096](../app/services/messaging_service.py) **fica estrito** (D4) | baixo | S |
| I5 | [messaging_service.py:1275-1292](../app/services/messaging_service.py) call site **mídia** | idêntico ao I4 | mesmo predicado, mesmo tratamento do takeover | baixo | S |
| I6 | [contacts.py:1300-1304](../server/routes/contacts.py) `_may_reply_in_chat_now` | bloqueia a despedida da IA privada | mesmo perdão, derivado de `result.tool_calls` já disponível em [:1340](../server/routes/contacts.py) | baixo | S |
| I7 | [contacts.py:1306-1323](../server/routes/contacts.py) `_blocked_notice` | texto afirma "um atendente assumiu" mesmo quando ninguém assumiu | com o I6 o caso deixa de ocorrer; **verificar** se ainda cabe distinguir o motivo (ver P3) | baixo | S |
| I8 | [tests/integration/test_human_assignment_ai_gate.py](../tests/integration/test_human_assignment_ai_gate.py) (546 linhas) | nenhum teste cobre "tool que fecha o próprio gate" — foi por isso que passou | novos testes nos dois sentidos, reusando a fixture `cycle` [:103-131](../tests/integration/test_human_assignment_ai_gate.py) e `_turn_ai_off` [:134-137](../tests/integration/test_human_assignment_ai_gate.py) | baixo | M |

### 3.1 Falsos positivos descartados

| Suspeita | Veredito | Razão |
|---|---|---|
| "Outras tools também fecham o gate no meio do turno" | ❌ descartado | `grep` por `ai_active=0` / `set_ai_enabled(False)` em todo o repo (core + 17 plugins instalados): fora de testes e migrations, os únicos escritores são `transfer_to_human` e `conversation_service` (que é painel, não turno) e `provisioning_service.py:101-104` (wizard). **`transfer_to_human` é o único escritor in-turn** |
| "O card 🔧 e o alerta sonoro também se perderam" | ❌ descartado | `broadcast_tool_calls` roda **antes** do envio ([:1080](../app/services/messaging_service.py) vs [:1089](../app/services/messaging_service.py)); o card, o `human_transfer_alert` e o *"SISTEMA pausou a IA"* ([:671-712](../app/services/messaging_service.py)) sempre chegaram. Confirmado no fio real de produção |
| "O `set_ai_enabled(False)` do contato ([:58](../agent/tools/transfer_to_human.py)) participa do bloqueio" | ❌ descartado | Desde o plano 37 o gate é 100% por-conversa: `_conversation_ai_active` [:1496-1533](../app/services/messaging_service.py) **não lê** o flag do contato. Quem bloqueia é só o `ai_active=0` da linha 89 |
| "A tag `transferido_atendente` bloqueia" | ❌ descartado | Rótulo visual desde o plano 37 — a docstring [:1516-1521](../app/services/messaging_service.py) é explícita |
| "É a época que está cortando" | ❌ descartado | `transfer_to_human` não chama `abort_ai_cycle` nem toca `state`. Quem corta é `_ai_may_speak_now` |
| "Os 2 casos com despedida na semana de 10/08 provam que às vezes funciona" | ⚠️ é o **fail-open**, não funcionamento | Sem conversa aberta, `conv is None` → `_conversation_ai_active` devolve `True` ([:1530](../app/services/messaging_service.py)) e a tool também não escreveu nada ([:66-67](../agent/tools/transfer_to_human.py)) |

### 3.2 Divergência de carona (decidir em P2)

[messaging_service.py:672](../app/services/messaging_service.py) usa
`any(tc.get("tool") == "transfer_to_human" for tc in tool_calls)` — **sem** `not tc.get("skipped")`,
ao contrário dos três predicados irmãos ([:648](../app/services/messaging_service.py),
[agent_run_service.py:105](../app/services/agent_run_service.py) e [:402](../app/services/agent_run_service.py)).
Efeito hoje: um `filter.tool.args` que pule a transferência ([agno_engine.py:239/287](../agent/agno_engine.py))
ainda dispara alerta sonoro e o card "SISTEMA pausou a IA". Pré-existente, não é a regressão.

---

## 4. Fases / Roadmap

```
WAVE 0   F0 ──────────────────────────────────  caracterização (congela o bug)   🔴
            │  [bloqueia: F1 — sem o congelamento não há prova da inversão]
WAVE 1   F1 ──────────────────────────────────  o seam no guard                  🔴
            │  [bloqueia: F2, F3]
WAVE 2   F2 · F3 ─────────────────────────────  call sites  ·  IA privada        🟢 🟢
            │  (barreira: os dois precisam existir antes da blindagem)
WAVE 3   F4 ──────────────────────────────────  blindagem dos DOIS lados         🔴
            │
WAVE 4   F5 ──────────────────────────────────  verificação em produção          🟢 (pós-deploy)
```

| Wave | Fase | Workstream | Paralelismo | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | testes | 🔴 sozinha | baixo | 3 testes de caracterização **verdes** provando o bug atual |
| 1 | **F1** | backend/guard | 🔴 sozinha | baixo | `_cycle_may_continue`/`_send_with_typing_guard`/`send_reply` aceitam o perdão; suíte do plano 96 verde |
| 2 | **F2** | backend/webhook | 🟢 com F3 | baixo | despedida chega e é salva nos batches de texto e mídia |
| 2 | **F3** | backend/nota privada | 🟢 com F2 | baixo | IA privada com "responder no chat" também entrega a despedida |
| 3 | **F4** | testes | 🔴 sozinha | baixo | F0 invertida + contraprovas de que o perdão não vaza |
| 4 | **F5** | verificação | 🟢 | baixo | a query de produção volta a ~100% de despedidas |

---

### Fase F0 — Caracterização: congelar o bug antes de tocar nele 🔴

**Objetivo:** provar, em teste, que hoje a despedida é descartada — nos três caminhos. Disciplina do repo
(o próprio [test_human_assignment_ai_gate.py:1-22](../tests/integration/test_human_assignment_ai_gate.py)
nasceu assim: "congelando o comportamento ATUAL e invertido pelas fases F1–F3").

**Itens:**
1. `[sequencial]` Novo bloco no fim de [test_human_assignment_ai_gate.py](../tests/integration/test_human_assignment_ai_gate.py),
   seção `── Plano 122 · a IA não pode calar a si mesma ──`.
2. `[paralelo]` `test_caracterizacao_despedida_da_transferencia_e_descartada` — fixture `cycle`, fecha o
   gate **como a tool fecha** (`assign_agent(conv, assignee_user_id=None, active_agent_key=None, ai_active=0)`
   — repare: `assignee=None`, ao contrário do `_turn_ai_off` [:134-137](../tests/integration/test_human_assignment_ai_gate.py),
   que simula o painel), chama `_send_with_typing_guard` e assere `sent is False` + `outbound.sent == []`.
3. `[paralelo]` Idem para o guard por-parte (split de 2+ partes ⇒ zero partes entregues).
4. `[paralelo]` Idem para a IA privada, reusando `_private_ai_send` [:469](../tests/integration/test_human_assignment_ai_gate.py).
5. `[sequencial]` Cada teste com docstring dizendo **explicitamente** que assere o bug e será invertido na F4.

**Pronto quando:** `venv/bin/python -m pytest tests/integration/test_human_assignment_ai_gate.py -q` verde,
com os novos testes passando **porque o bug existe**.

#### Status de execução — Fase F0
**Estado:** ✅ Concluída (2026-08-14)
- **O que foi feito:** seção `── Plano 122 · a IA não pode calar a si mesma ──` no fim de
  [test_human_assignment_ai_gate.py](../tests/integration/test_human_assignment_ai_gate.py), com o helper
  `_transfer_closes_the_gate(conv_id)` (escreve `assignee_user_id=None, active_agent_key=None, ai_active=0`
  — o que a tool faz, **não** o que o painel faz) e 3 testes de caracterização. `_private_ai_send` ganhou
  os kwargs `ai_active` e `tool_calls` para conseguir montar o fechamento de portão da própria IA.
- **Como foi feito / decisões:** os dois primeiros testes batem direto no seam (`_send_with_typing_guard`
  e `send_reply`) e o terceiro sobe o app inteiro e bate no endpoint real da nota privada. Isso deixou uma
  assimetria proposital: os dois primeiros só invertem quando alguém **passa** o perdão, então não provam o
  cabeamento — foi por isso que a F4 ganhou um 4º teste que dirige o webhook de ponta a ponta.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `venv/bin/python -m pytest tests/integration/test_human_assignment_ai_gate.py -q`
  → **24 passed**, com os 3 novos verdes *porque o bug existe*.

---

### Fase F1 — O seam: perdão de escopo mínimo no guard 🔴 `[bloqueia: F2, F3]`

**Objetivo:** ensinar o guard a distinguir "o humano me calou" de "eu me calei", **sem** relaxar o primeiro.

**Itens:**
1. `[sequencial]` [messaging_service.py:224-234](../app/services/messaging_service.py) — `_cycle_may_continue`
   ganha `*, allow_self_handoff: bool = False`. Ordem obrigatória (a inversão quebraria o D2):
   ```python
   if abort_epoch is not None and self._abort_epoch(...) != abort_epoch:
       return False              # época PRIMEIRO — o perdão nunca a alcança
   if allow_self_handoff:
       return True               # o gate de banco foi fechado por ESTE turno
   return self._ai_may_speak_now(channel_id, phone)
   ```
2. `[sequencial]` Docstring explicando **por que é seguro**: `abort_ai_cycle` incrementa a época antes de
   tudo ([:1444-1450](../app/services/messaging_service.py)), inclusive quando não cancela; logo nenhuma
   tomada humana chega aqui com a época intacta. Citar este plano e o 96.
3. `[sequencial]` [:856-884](../app/services/messaging_service.py) `_send_with_typing_guard` — mesmo kwarg,
   repassado na reconsulta [:872](../app/services/messaging_service.py) **e** na chamada a `send_reply` [:880-882](../app/services/messaging_service.py).
4. `[sequencial]` `send_reply` — mesmo kwarg, repassado ao `_cycle_may_continue` do laço [:471-472](../app/services/messaging_service.py).
   ⚠️ Sem este item a despedida morre na parte 1/N e o conserto fica pela metade.
5. `[paralelo]` Log do guard [:473-476](../app/services/messaging_service.py) e [:874-876](../app/services/messaging_service.py):
   deixar claro no texto quando o corte foi por época × por gate — hoje a mensagem funde os dois motivos
   ("gate fechado ou ciclo invalidado"), o que dificultaria diagnosticar a próxima ocorrência.

**Pronto quando:** a suíte inteira do plano 96 verde **sem alteração** (`allow_self_handoff` tem default
`False`, então todo call site existente mantém o comportamento byte a byte) e a F0 continua verde
(ninguém passou o perdão ainda).

#### Status de execução — Fase F1
**Estado:** ✅ Concluída (2026-08-14)
- **O que foi feito:** em [messaging_service.py](../app/services/messaging_service.py) —
  `_cycle_may_continue` ganhou `*, allow_self_handoff: bool = False` na ordem exigida (época → perdão →
  gate); `_send_with_typing_guard` e `send_reply` ganharam o mesmo kwarg e o repassam (o segundo até o
  guard por-parte do laço). Novo helper `_guard_reason(channel_id, phone, abort_epoch)`.
- **Como foi feito / decisões:** o item 5 (log) virou o helper `_guard_reason` em vez de dois `if` inline —
  os dois guards imprimem o mesmo vocabulário e o motivo sai de uma leitura em memória (`ai_abort_epochs`),
  sem SELECT extra no caminho quente. A mensagem única de antes ("gate fechado ou ciclo invalidado")
  fundia os dois motivos, que agora têm consequências opostas.
- **Problemas / pendências:** nenhuma. `asyncio.to_thread` aceita kwargs, então os 3 call sites do guard
  não precisaram de `functools.partial`.
- **Verificação:** suíte do plano 96 **verde sem alteração** (24 passed) — com o default `False` todo call
  site existente ficou byte a byte igual, e a F0 continuou verde porque ninguém passava o perdão ainda.

---

### Fase F2 — Cabear os dois call sites do webhook 🟢 `[depende de: F1]` `[paralelo com F3]`

**Objetivo:** o batch de texto e o de mídia passam a informar que o turno terminou em transferência.

**Itens:**
1. `[sequencial]` Helper de módulo (uma linha, sem `if` espalhado):
   `_turn_handed_off(tool_calls) -> bool` = `any(tc.get("tool") == "transfer_to_human" and not tc.get("skipped") for tc in (tool_calls or []))`.
   Usa a MESMA forma dos predicados irmãos ([:648](../app/services/messaging_service.py),
   [agent_run_service.py:105](../app/services/agent_run_service.py)/[:402](../app/services/agent_run_service.py)).
2. `[paralelo]` Call site **texto** [:1089-1092](../app/services/messaging_service.py): passa
   `allow_self_handoff=_turn_handed_off(result.tool_calls)`.
3. `[paralelo]` Call site **mídia** [:1285-1288](../app/services/messaging_service.py): idem.
4. `[sequencial]` **Não** propagar o perdão para `maybe_emit_ai_takeover` [:1093-1096](../app/services/messaging_service.py)
   e [:1289-1292](../app/services/messaging_service.py) (D4) — comentário curto no código dizendo por quê,
   senão a próxima leitura "corrige" a assimetria.
5. `[paralelo]` Conferir que o save volta a acontecer: com `sent_parts` não-vazio, [:540-551](../app/services/messaging_service.py)
   grava a despedida com `agent_key` e `execution_id`, e `response_sent`/`output_text` ([:559-566](../app/services/messaging_service.py))
   voltam a registrar o turno.

**Pronto quando:** num fio de teste, uma resposta pós-`transfer_to_human` (a) chega ao `outbound`,
(b) aparece salva como `assistant`, (c) **não** gera card *"A IA assumiu a conversa"*, e (d) o card
*"SISTEMA pausou a IA"* continua aparecendo.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída (2026-08-14)
- **O que foi feito:** helper de módulo `_turn_handed_off(tool_calls)` em
  [messaging_service.py](../app/services/messaging_service.py) (antes de `MessagingContext`), e os dois call
  sites passando `allow_self_handoff=_turn_handed_off(result.tool_calls)`.
- **Como foi feito / decisões:** `maybe_emit_ai_takeover` ficou **intocado** — e o D4 sai de graça: com o
  predicado estrito, `_cycle_may_continue` lê o gate fechado e devolve `False`, então o card não é emitido
  mesmo agora que `sent` passou a ser `True`. Ficou um comentário no call site de texto explicando a
  assimetria, para a próxima leitura não "corrigir" o que é proposital.
- **Problemas / pendências:** nenhuma. O item 5 (save de volta) não precisou de mudança: com `sent_parts`
  não-vazio o laço de save já roda — foi verificado pelo teste ponta-a-ponta da F4, que lê a row salva.
- **Verificação:** `test_despedida_e_salva_e_nao_vira_takeover` cobre as quatro condições do "pronto quando"
  (chegou ao wire, salva como `assistant`, sem card *"A IA assumiu"*, com card *"SISTEMA pausou a IA"*).

---

### Fase F3 — A IA da nota privada 🟢 `[depende de: F1]` `[paralelo com F2]`

**Objetivo:** o mesmo perdão no terceiro caminho de saída — e parar de gravar um aviso falso.

**Itens:**
1. `[sequencial]` [contacts.py:1300-1304](../server/routes/contacts.py) `_may_reply_in_chat_now` aceita o
   perdão (época primeiro, igual à F1 — [:1301-1302](../server/routes/contacts.py) fica intacta).
2. `[sequencial]` [:1367](../server/routes/contacts.py) passa o predicado derivado de `result.tool_calls`
   (já disponível em [:1340](../server/routes/contacts.py)). Importar o helper da F2 em vez de reescrever
   o `any(...)`.
3. `[paralelo]` `_blocked_notice` [:1306-1323](../server/routes/contacts.py): com o item 2 o caso da
   transferência deixa de cair aqui. Ver P3 antes de mexer no texto.

**Pronto quando:** IA privada com *"responder no chat"* + transferência ⇒ a despedida chega ao cliente e
**nenhum** card *"⚠️ … porque um atendente assumiu a conversa"* é gravado; e o teste
`test_nota_privada_nao_fala_em_conversa_atribuida` [:530](../tests/integration/test_human_assignment_ai_gate.py)
continua verde (conversa atribuída a humano ⇒ segue calada).

#### Status de execução — Fase F3
**Estado:** ✅ Concluída (2026-08-14)
- **O que foi feito:** em [contacts.py](../server/routes/contacts.py) — `_may_reply_in_chat_now` passou a
  aceitar `allow_self_handoff` (época primeiro, igual à F1); `handed_off = _turn_handed_off(result.tool_calls)`
  é calculado uma vez após o `broadcast_tool_calls` e alimenta **os dois** pontos de bloqueio (o de entrada e
  o guard por-parte do split). `_turn_handed_off` importado junto de `MessagingContext`/`MessagingService`.
- **Como foi feito / decisões:** o plano listava só o bloqueio de entrada; o guard por-parte do split
  privado também precisava do perdão, senão a despedida da IA privada morreria na parte 2/N — mesmo defeito
  que o item 4 da F1 conserta no caminho normal.
- **Problemas / pendências:** o `_blocked_notice` (P3) ficou **como está**, conforme a recomendação: depois
  desta fase a transferência não cai mais ali, e os motivos restantes (época e atribuição) são exatamente os
  casos em que o texto *"um atendente assumiu a conversa"* é verdadeiro.
- **Verificação:** `test_ia_privada_entrega_a_despedida` verde; `test_nota_privada_nao_fala_em_conversa_atribuida`
  e `test_nota_privada_reconsulta_gate_entre_partes` continuam verdes (conversa com dono humano segue calada).

---

### Fase F4 — Blindagem: os dois lados, no mesmo arquivo 🔴 `[depende de: F2, F3]`

**Objetivo:** que ninguém possa reintroduzir nem o bug do plano 96 nem o deste plano. O buraco existiu
porque a suíte só olhou um lado; a partir daqui os dois ficam travados lado a lado.

**Itens — lado A (a despedida PASSA):** `[paralelo]`
1. Inverter os 3 testes da F0 (docstring: *"a F0 assegurava o oposto"*, padrão do arquivo).
2. `test_despedida_e_salva_e_nao_vira_takeover` — a resposta é persistida e `maybe_emit_ai_takeover` não roda.
3. `test_despedida_atravessa_o_split_inteiro` — 3 partes ⇒ 3 entregues (trava o item 4 da F1).

**Itens — lado B (o perdão NÃO vaza):** `[paralelo]`
4. `test_perdao_nao_sobrevive_a_atribuicao_humana` — turno com transferência **e** `abort_ai_cycle`
   chamado no meio (época+1) ⇒ nada sai, mesmo com `allow_self_handoff=True`. **É o teste central do D2.**
5. `test_perdao_nao_vaza_para_o_turno_seguinte` — turno 1 com transferência entrega; turno 2 (sem
   transferência, gate ainda fechado) **não** entrega.
6. `test_tool_pulada_por_filtro_nao_ganha_perdao` — `tool_calls` com `skipped=True` ⇒ sem perdão
   (trava o predicado da F2 item 1).
7. `test_envio_do_operador_durante_o_turno_continua_cortando` — cobre [contacts.py:350-362](../server/routes/contacts.py).

**Itens — regressão do parque:** `[sequencial]`
8. Suíte inteira verde no Postgres: `venv/bin/python -m pytest` (as três árvores). ⚠️ Existem 3 falhas
   pré-existentes conhecidas (2 de alembic + 1 da matriz de auditoria) — comparar com `git stash` antes
   de culpar este plano.

**Pronto quando:** os 7 testes novos verdes, a suíte do plano 96 intacta, e nenhuma falha nova na suíte
completa.

#### Status de execução — Fase F4
**Estado:** ✅ Concluída (2026-08-14)
- **O que foi feito:** os 3 testes da F0 invertidos + 5 novos, os 8 na mesma seção do arquivo do plano 96.
  **Lado A:** `test_despedida_da_transferencia_chega_ao_cliente`, `test_despedida_atravessa_o_split_inteiro`,
  `test_ia_privada_entrega_a_despedida`, `test_despedida_e_salva_e_nao_vira_takeover`.
  **Lado B:** `test_perdao_nao_sobrevive_a_atribuicao_humana`, `test_perdao_nao_vaza_para_o_turno_seguinte`,
  `test_tool_pulada_por_filtro_nao_ganha_perdao`, `test_envio_do_operador_durante_o_turno_continua_cortando`.
  Helpers novos: `_make_fake_handoff_run` (stub do AGNO que fecha o gate **dentro** do turno, como a tool
  faz) e `_run_handoff_turn` (turno completo pelo webhook + drain do orquestrador).
- **Como foi feito / decisões:** os testes de seam sozinhos não bastavam — passar `allow_self_handoff=True`
  na mão prova o guard, mas ficaria **verde com o call site esquecido**. Daí o `_run_handoff_turn`, que
  dirige `POST /api/webhook/gowa/default` de ponta a ponta e afere pelo que ficou **persistido** (o save só
  roda para `sent_parts`, então row salva = ida ao wire). O card de takeover é aferido por
  `system_notices.has_event(conv_id, "ai_takeover")`, o mesmo predicado que o código usa.
- **Problemas / pendências:** nenhuma. As duas garantias centrais foram **mutation-testadas** em vez de
  presumidas (ver Verificação) — vale repetir esses dois experimentos se alguém mexer no guard.
- **Verificação:**
  - `tests/integration/test_human_assignment_ai_gate.py` → **29 passed**.
  - **Mutação 1** — inverter perdão↔época em `_cycle_may_continue`:
    `test_perdao_nao_sobrevive_a_atribuicao_humana` e
    `test_envio_do_operador_durante_o_turno_continua_cortando` ficam **VERMELHOS**. A ordem é coberta,
    não apenas documentada.
  - **Mutação 2** — remover `allow_self_handoff=` do call site de texto:
    `test_despedida_e_salva_e_nao_vira_takeover` fica **VERMELHO**. O cabeamento é coberto.
  - Ambas revertidas; suíte verde de novo.
  - `grep` de `send_reply`/`_send_with_typing_guard` em `app/`, `server/`, `agent/` e `storages/plugins/`:
    **3 call sites no total** (um interno ao próprio guard + os dois batches), todos cabeados. Não existe
    quarto caminho de saída da IA.
  - **Suíte completa** (`venv/bin/python -m pytest`, rodada EXCLUSIVA): 3 falhas, **todas as 3
    pré-existentes e PROVADAS como tal** — as mesmas 3 falham num `git worktree add … HEAD` sem nenhuma
    mudança deste plano (`test_alembic_hygiene` ×2 + `test_audit_matrix_is_complete`). Nenhuma falha nova,
    e `test_human_assignment_ai_gate.py` não aparece na lista de falhas.
  - ⚠️ **Armadilha operacional encontrada no caminho**: rodar a suíte completa em background e disparar um
    `pytest <arquivo>` "enquanto ela roda" é a colisão de schema do `tests/pg.py` — os dois resultados
    viram lixo. O sintoma foi um `grep passed|failed` voltando VAZIO. As rodadas acima são todas
    sequenciais e exclusivas; qualquer re-verificação deve ser também.

---

### Fase F5 — Verificação em produção (pós-deploy) 🟢

**Objetivo:** fechar o ciclo com o mesmo instrumento que mediu o bug — não com impressão.

**Itens:**
1. `[sequencial]` Após o deploy, rodar a mesma consulta (somente leitura, `banco-nexus-redes-brasil`,
   database `whatsbot`): cards `role='tool_call'` com `content LIKE '%transfer_to_human%'` × resposta
   `assistant` com `status <> 'operator'` e `sent_by_user_id IS NULL` nos 120s seguintes, agrupado por dia.
2. `[paralelo]` Amostrar 1 fio real pós-deploy e conferir a ordem: card `🔧 transfer_to_human` →
   despedida da IA → *"SISTEMA pausou a IA"* → atendente.
3. `[paralelo]` `grep` no log por `[Guard] resposta da IA descartada` — deve continuar aparecendo **só**
   em tomada humana real.

**Pronto quando:** a taxa diária de despedidas volta a ~100% (era 178/178 antes de 31/07) e nenhum
`[Guard]` novo aparece logo após um `transfer_to_human`.

#### Status de execução — Fase F5
**Estado:** ⛔ Bloqueada — **pelo deploy**, não por trabalho pendente (as F0–F4 estão prontas; esta fase, por
construção, só existe em produção)
- **O que foi feito:** nada ainda — por construção. É a única fase pós-deploy.
- **Como foi feito / decisões:** —
- **Problemas / pendências:** rodar **depois** que o código subir em
  [WhatsBot Redes Brasil](../CLAUDE.md) (Coolify). A consulta é a mesma que mediu o bug, para o "antes" e o
  "depois" serem comparáveis: cards `role='tool_call'` com `content LIKE '%transfer_to_human%'` × resposta
  `assistant` com `status <> 'operator'` e `sent_by_user_id IS NULL` nos 120s seguintes, agrupada por dia,
  no `banco-nexus-redes-brasil` / database `whatsbot` (somente leitura).
- **Verificação:** pendente. Alvo: taxa diária de despedida de volta a ~100% (era 178/178 antes de 31/07) e
  nenhum `[Guard] resposta da IA descartada` logo depois de um `transfer_to_human` — que agora diz
  **qual** dos dois motivos foi (F1 item 5), então o log basta para o diagnóstico.

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Ordem dentro de `_cycle_may_continue` | Checar o perdão **antes** da época desfaria o plano 96 inteiro em silêncio | Época primeiro, sempre (F1 item 1) + `test_perdao_nao_sobrevive_a_atribuicao_humana` (F4 item 4) |
| Guard por-parte esquecido | Conserto pela metade: a despedida morre na parte 1/N com `split_messages` ligado (default `True`, [:406-407](../app/services/messaging_service.py)) | F1 item 4 + `test_despedida_atravessa_o_split_inteiro` |
| Perdão vazar para o turno seguinte | A IA voltaria a falar numa conversa transferida | O perdão é parâmetro de chamada (D3), morre no `return`; travado por `test_perdao_nao_vaza_para_o_turno_seguinte` |
| Card "A IA assumiu" indevido | Fio absurdo: *"SISTEMA pausou a IA"* seguido de *"A IA assumiu a conversa"* | D4 — `maybe_emit_ai_takeover` fica no predicado estrito |
| `filter.conversation.assignment` redireciona para um humano | A tool pode gravar `assignee_user_id=<id>` ([transfer_to_human.py:73-89](../agent/tools/transfer_to_human.py)) e ainda assim a despedida sai | **Aceito e desejável**: quem transferiu foi a IA no mesmo turno; a época segue intacta porque nenhum humano clicou nada |
| Cliente responde durante o envio da despedida | Novo inbound com IA off não gera ciclo novo; o batch em curso termina | Comportamento já existente, não muda |
| Fail-open do gate | `_ai_may_speak_now` devolve `True` em erro ([:213-215](../app/services/messaging_service.py)) | Preservado — o perdão só acrescenta um caminho de `True`, nunca de `False` |
| Suíte concorrente | Duas suítes Postgres em paralelo corrompem o schema compartilhado | Rodar sozinho; conferir `pg_stat_activity` antes de culpar o código |
| Plugins | Nenhum plugin instalado escreve `ai_active` em turno (§3.1) | Nenhuma superfície de plugin muda ⇒ **sem bump de `WHATSBOT_API_VERSION`** |

---

## 6. Perguntas em aberto

**P1 — O perdão deve ser um bypass simples ou condicionado à forma exata da escrita da tool?**
Contexto: (a) `allow_self_handoff=True` ⇒ `return True` direto, ignorando todo o gate de banco;
(b) validar que o gate está fechado **exatamente** como a tool o fecha (`ai_active=0` ∧ `assignee_user_id IS NULL`)
e só então perdoar.
⏸️ **Recomendação: (a).** (b) acopla o guard ao formato de escrita da tool e quebra sozinho quando um
plugin usa `filter.conversation.assignment` para mandar a conversa a um humano específico — caso em que
a despedida também deve sair. A época já entrega a precisão que (b) tentaria comprar caro.

**P2 — Corrigir de carona a falta do `not skipped` em [:672](../app/services/messaging_service.py)?**
⏸️ **Recomendação: não neste plano.** É bug pré-existente e independente (alerta sonoro + card disparando
quando a tool foi pulada por filtro). Misturar violaria "um refactor por commit". Registrar em §3.2 e
abrir plano próprio se incomodar.

**P3 — O `_blocked_notice` da IA privada deve distinguir o motivo do bloqueio?**
Contexto: hoje o texto afirma *"um atendente assumiu a conversa"* ([contacts.py:1307-1310](../server/routes/contacts.py))
para qualquer bloqueio. Depois da F3, a transferência não cai mais ali — restam época (envio do operador)
e atribuição, casos em que o texto **está correto**.
⏸️ **Recomendação: deixar como está** e reavaliar se aparecer um terceiro motivo.

**P4 — Manter a alternativa de plugin como paliativo enquanto o core não é publicado?**
Contexto: um plugin em `filter.tool.args`/`tool.before` de `transfer_to_human` pode mandar um texto fixo
pelo outbound router antes da tool rodar (envio de plugin não passa pelo guard; há precedente em
`retornos`/`agendamento_retorno`).
⏸️ **Recomendação: descartar.** O custo é alto (texto fixo no lugar da redação contextual, mais um lugar
para manter, e a mensagem da IA continuaria sendo perdida sem rastro) e o conserto real é pequeno e
core-only. Fica registrado apenas como saída de emergência se o deploy do core atrasar semanas.

---

## 7. Apêndice — arquivos-chave

**Backend / guard (o coração):**
- [app/services/messaging_service.py](../app/services/messaging_service.py) — `_cycle_may_continue` [:224-234](../app/services/messaging_service.py), `send_reply` [:465-476](../app/services/messaging_service.py), `_send_with_typing_guard` [:856-884](../app/services/messaging_service.py), call sites [:1079-1096](../app/services/messaging_service.py) e [:1275-1292](../app/services/messaging_service.py)

**Backend / nota privada:**
- [server/routes/contacts.py](../server/routes/contacts.py) — `_private_ai_conversation_open` [:336-348](../server/routes/contacts.py), `_run_private_ai` [:1276-1371](../server/routes/contacts.py)

**Leitura obrigatória, sem edição (contexto do porquê):**
- [agent/tools/transfer_to_human.py](../agent/tools/transfer_to_human.py) — quem fecha o portão (D5: não muda)
- [app/services/conversation_service.py](../app/services/conversation_service.py) — os 4 `_abort_ai_cycle` [:461/492/529/607](../app/services/conversation_service.py)
- [agent/agno_engine.py](../agent/agno_engine.py) — `_extract_reply` [:460-486](../agent/agno_engine.py) (por que prompt não resolve)

**Testes:**
- [tests/integration/test_human_assignment_ai_gate.py](../tests/integration/test_human_assignment_ai_gate.py) — fixture `cycle` [:103-131](../tests/integration/test_human_assignment_ai_gate.py), `_private_ai_send` [:469](../tests/integration/test_human_assignment_ai_gate.py)

---

## 8. Checklist de verificação

- [x] `venv/bin/python -m pytest tests/integration/test_human_assignment_ai_gate.py -q` — **29 passed**
- [x] `venv/bin/python -m pytest` (suíte completa, Postgres via `WHATSBOT_TEST_DB_URL`) — sem falha nova
      (só as 3 pré-existentes: 2 de alembic + 1 da matriz de auditoria)
- [x] Nenhuma suíte Postgres concorrente rodando (o schema `public` é recriado por processo)
- [x] Mutação 1 (perdão antes da época) ⇒ 2 testes vermelhos — a ordem é **coberta**, não só documentada
- [x] Mutação 2 (call site sem o cabeamento) ⇒ 1 teste vermelho — o cabeamento é **coberto**
- [x] Os 3 (únicos) call sites de envio da IA cabeados; nenhum quarto caminho de saída existe
- [x] Nenhuma mudança em superfície de plugin ⇒ `WHATSBOT_API_VERSION` **não** muda (segue `1.2.0`)
- [x] Sem migration, sem mudança de frontend, sem segredo em log ou URL
- [ ] Manual: transferir numa conversa de teste ⇒ despedida chega ao cliente **e** aparece salva no painel
- [ ] Manual: clicar *Atribuir a mim* durante a resposta da IA ⇒ a resposta continua sendo cortada (plano 96 intacto)
- [ ] Manual: enviar mensagem como operador durante a resposta da IA ⇒ continua cortando
- [ ] Fio do painel: card `🔧 transfer_to_human` → despedida → *"SISTEMA pausou a IA"*, **sem** *"A IA assumiu"*
- [ ] F5 rodada em produção: taxa diária de despedida de volta a ~100%
