# Plano 123 — Protocolo por CANAL: acabar com o fechamento em cascata entre conversas de canais diferentes

> **Status:** PLANEJAMENTO · **Data:** 2026-08-14 · **Escopo:** médio
> **Origem:** incidente reportado pelo usuário em 2026-08-14 — a operadora fechou o atendimento do
> contato Abimael no canal *Atendimento* (`/conversations/6870`) e o WhatsBot fechou **junto** a
> conversa dele no canal *numero_recuperacao* (`/conversations/15647`), que estava em curso.
> **Método:** leitura do código-fonte do plugin em `whatsbot-pro-plugins/plugins/protocolos/src`
> (versão **1.33.0**, a mesma que roda em produção — conferida na tabela `plugins`) + leitura do
> core + consultas `READ ONLY` no banco de produção (`whatsbot`@10.8.100.5).
>
> O protocolo hoje é **por contato**; a conversa, desde o plano 11 F1, é **por canal**. Quando o
> mesmo cliente é atendido em dois canais, as duas conversas caem no mesmo protocolo — e o caminho
> "Finalizar protocolo" do painel resolve e **fecha no core** a conversa do outro canal sem avisar
> ninguém. Este plano aplica ao plugin a mesma correção por-canal que o core já fez, e antes disso
> tampa o buraco com uma confirmação explícita.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de
> passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| **D1** ✅ 2026-08-14 | "Se eu fechar um protocolo, ele deve estar vinculado **apenas à conversa**, não ao número." | O escopo do protocolo deixa de ser `contact_id` e passa a ser `(contact_id, inbox_id)`. Fases F4–F6. |
| **D2** ✅ 2026-08-14 | Só planejar — **nada é implementado** nesta rodada. | Nenhuma fase pode ser iniciada sem pedido explícito. |
| **D3** ✅ 2026-08-14 | O merge de contatos duplicados (`plano-merge-contatos-duplicados/`) é trabalho **separado** e virá depois. | Este plano **não** mexe em `contacts`. Mas ver P4: a ordem entre os dois importa. |
| **D4** — princípio | Produção roda 1.33.0 e tem 15.5k protocolos — **não há liberdade para refactor agressivo**; toda mudança de escopo precisa de migration reversível e backfill medido. | F4 é `🔴 sozinha`, com ensaio em transação antes do commit. |
| **D5** — princípio do repo | Tudo que puder ficar no plugin fica no plugin. | Só **uma** costura entra no core (F2), e ela passa nos três critérios do `CLAUDE.md` — ver §3. |

---

## 1. Resumo executivo

Duas falhas independentes, com a mesma raiz conceitual ("o plugin identifica o atendimento pelo
**número**, o core identifica pelo **canal**"):

1. **Cascata de fechamento** — `close_protocolo` recusa fechar um protocolo com ciclo aberto
   (correto); o frontend então chama `forceResolveAndClose`, que pega o **primeiro** ciclo aberto
   que encontrar, resolve e fecha aquela conversa no core. Se o ciclo for de **outro canal**, a
   conversa do outro canal morre em silêncio. Foi exatamente o incidente.
2. **Roteamento cego a canal** — `_resolve_target` resolve o contato por `contact_repo.get_by_phone`
   (que casa variantes BR de 12↔13 dígitos e faz `.first()` **sem `ORDER BY`**) e depois pega
   `get_open_for_contact`, que ignora o inbox. Com o mesmo cliente em dois canais — ou com um par
   de contatos duplicados — o ciclo/protocolo pode ser aberto na thread errada.

A solução tem três camadas, da mais barata para a mais estrutural: **(a)** confirmação explícita
antes de fechar conversa de outro canal (frontend, shipável sozinho); **(b)** o bus passa a carregar
`channel_id`/`conversation_id` para o plugin parar de adivinhar; **(c)** o protocolo passa a ser
único por `(contact_id, inbox_id)`.

---

## 2. Como funciona hoje (mapa)

### 2.1 O escopo do protocolo

| Peça | Onde | Escopo hoje |
|---|---|---|
| Protocolo | `logic.py:889` `_select_open_protocolo` | **contato** (`WHERE contact_id = :cid AND status = 'aberto'`) |
| Índice que trava | `plugin_protocolos_one_open_per_contact` — `UNIQUE (contact_id) WHERE status='aberto'` | **contato** |
| Ciclo (atendimento) | `logic.py:3251` `get_open_cycle(conversation_id, protocolo_id)` | **conversa** ✅ já correto |
| Conversa | core, `db/repositories/conversation_repo.py:275` `get_open_for_contact_inbox` | **canal/inbox** ✅ desde o plano 11 F1 |

⚠️ O core **já fez** essa correção e deixou o idioma pronto: `get_open_for_contact_scoped`
(`conversation_repo.py:304`) documenta literalmente *"por-canal, não por-contato"* (plano 37). O
plugin ficou para trás — é a dívida que este plano paga.

### 2.2 O caminho que fecha a conversa do outro canal

| Passo | Onde | O que faz |
|---|---|---|
| 1 | `routes.py:304` `POST /protocolos/{atid}/close` | chama `logic.close_protocolo` |
| 2 | `logic.py:1349` `close_protocolo` | **recusa** com "Existe um atendimento aberto neste protocolo" se houver ciclo aberto resolvível (`_open_cycles_of_protocolo`, `logic.py:1277`) ou conversa aberta (`_has_open_conversation`, `logic.py:1289`) |
| 3 | `static/protocolos_tab.js:1450` `finalizeProtocolo` | casa o erro por **regex** `/atendimento abert[ao]/i` e chama `forceResolveAndClose` |
| 4 | `static/protocolos_tab.js:1398` | `atendimentos.find((c) => !c.ended_at && c.conversation_id)` — pega o **primeiro** ciclo aberto, sem olhar canal nem qual conversa o operador está fechando |
| 5 | `static/protocolos_tab.js:1419` | `POST /atendimentos/{conv}/resolve` |
| 6 | `static/protocolos_tab.js:1430` | `api.services.setConversationStatus(conv, 'closed')` — **fecha a conversa no core** |

O passo 4 é o defeito: nada garante que `openCycle.conversation_id` seja a conversa que o operador
tem na tela. O mesmo caminho é alcançado **duas vezes**:

| Entrada | Onde |
|---|---|
| Botão "Finalizar" no detalhe do protocolo | `static/protocolos_tab.js:1447` |
| Arrastar o card para a coluna "fechado" no Kanban | `static/protocolos_tab.js:1498` |

`resolveAndCloseAll` (`static/extends.js:49`) **não** tem o defeito: ela fecha só a conversa que
recebeu (`static/extends.js:73`) e, se sobrar ciclo aberto, o `POST /close` simplesmente devolve
erro. Ela entra no plano só para ganhar a mesma mensagem de erro melhorada (F1).

### 2.3 Evidência do incidente (produção, `READ ONLY`)

Contato **7680** (`556183146550`, "Abimael 🌐") tem conversa nos dois inboxes:

| conv | inbox | canal | status |
|---|---|---|---|
| 6870 | 21 `Atendimento` | `whatsapp_cloud_bc081279` | closed |
| 15647 | 17 `numero_recuperacao` | `gowa_gjOZx4jaNS` | closed |

Protocolo **15944** (aberto em 2026-08-10) acumulou ciclos das **duas**:

```
11:04:19.801  ciclo 22140 abre em 15647 (recuperação) DENTRO do proto 15944
11:31:47.955  operadora resolve o ciclo 22130 (conv 6870)
11:31:48.062  conv 6870 → closed            ← o que ela pediu
11:32:12.083  ciclo 22140 resolvido         ← ninguém pediu
11:32:12.113  conv 15647 → closed           ← ninguém pediu
11:32:12.173  proto 15944 → fechado
```

Os 124 ms entre o passo 4 e o 6 são a assinatura do `forceResolveAndClose`.

### 2.4 O roteamento cego a canal

`logic.py:3451` `_resolve_target`:

```python
contact = contact_repo.get_by_phone(phone)                    # variantes BR, .first() sem ORDER BY
atend = (conversation_repo.get_open_for_contact(contact["id"])   # ignora inbox
        or conversation_repo.get_latest_for_contact(contact["id"]))
```

Dois defeitos empilhados:

| # | Defeito | Onde | Efeito |
|---|---|---|---|
| a | `get_by_phone` casa **variantes** de 12↔13 dígitos e devolve `.first()` **sem `ORDER BY`** | core, `db/repositories/contact_repo.py:94` | com par duplicado, qual contato volta é indefinido |
| b | `get_open_for_contact` / `get_latest_for_contact` ignoram o inbox | core, `conversation_repo.py:252` e `:264` | a conversa escolhida pode ser de outro canal |

⚠️ **O plugin não tem como acertar sozinho hoje**: os payloads `message.saved` e `message.sent`
carregam `phone`, `text`, `msg_id`, `media_*`, `is_group`, `source`, `ts` — e **não** `channel_id`
nem `conversation_id` (`app/services/messaging_service.py:1111`, `:385`;
`server/routes/contacts.py:1101`). O `ws_manager.broadcast("new_message", …)` imediatamente acima
**tem** `channel_id` — o dado existe no escopo, só não é publicado no bus. É isso que a F2 corrige.

Evidência do sintoma em produção: às **10:57:40** de 2026-08-14 a operadora reabriu/atribuiu a conv
**9272** (contato 5894, `5537996652009`) e a nota privada de retorno foi gravada na conv **8955**
(contato 5848, `553796652009`) — o par duplicado do Genilson. O `agendamento_retorno` usa o mesmo
idioma (`logic.py:154` `get_by_phone` → `logic.py:157` `get_latest_for_contact`).

### 2.5 Escala medida

| Métrica (produção, 2026-08-14) | Valor |
|---|---|
| Contatos com histórico em 2+ inboxes | **62** |
| Protocolos que já cruzaram inbox | **9** |
| Protocolos ABERTOS cruzando inbox agora | **0** (o incidente acabou de ser fechado) |
| Pares de contato duplicado 12↔13 | 203 (1 com as duas conversas abertas) |
| Plugins que usam `get_by_phone` + conversa inbox-cega | ≥ 6 (`protocolos`, `agendamento_retorno`, `retornos`, `trackify`, `janela_72h`, `utm_atendente`) |

---

## 3. A costura no core (F2) — por que ela é legítima

O `CLAUDE.md` exige os **três** critérios juntos para crescer o core:

| Critério | Veredito |
|---|---|
| ≥ 2 consumidores previstos, reais | ✅ 6 plugins instalados em produção resolvem contato→conversa por telefone hoje |
| nenhum gancho existente enxerga o sinal | ✅ `message.saved`/`message.sent` não publicam `channel_id`; não há outro gancho no caminho |
| usar o gancho existente custaria caro no caminho quente | ✅ a alternativa plugin-only é um `SELECT` em `messages` por `msg_id` a **cada** mensagem, e falha quando `msg_id` é nulo |

É **MINOR** (`WHATSBOT_API_VERSION` 1.2.0 → 1.3.0, `plugins/semver.py:45`): campo acrescentado a
payload existente, no mesmo commit dos call sites. Nenhum consumidor atual quebra — quem não lê o
campo não vê diferença.

---

## 4. Inventário das mudanças

### 4.1 Plugin `protocolos` (fonte: `whatsbot-pro-plugins/plugins/protocolos/src/`)

| # | Arquivo:linha | O que falta | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|
| 1 | `static/protocolos_tab.js:1398` | `find` pega qualquer ciclo aberto | listar **todos** os ciclos abertos, rotular por canal, confirmar antes de fechar os de outra conversa | baixo | S |
| 2 | `static/protocolos_tab.js:1447` / `:1498` | os dois call sites herdam o defeito | ambos passam a chamar a versão com confirmação | baixo | S |
| 3 | `static/protocolos_tab.js:1450` | acoplamento por **regex** na mensagem de erro | backend passa a devolver código estruturado; regex vira fallback | médio | S |
| 4 | `routes.py:304` | erro do `close` é só string | devolver também `{ code: "open_cycles", cycles: [{conversation_id, channel_id, inbox_name}] }` | baixo | S |
| 5 | `logic.py:3451` `_resolve_target` | resolve contato/conversa por telefone, cego a canal | usar `channel_id` do payload (F2) → `get_open_for_contact_inbox` | médio | M |
| 6 | `logic.py:889` `_select_open_protocolo` | `WHERE contact_id` | `WHERE contact_id AND inbox_id` | alto | M |
| 7 | `logic.py:999` `ensure_protocolo_ex` | cria protocolo sem inbox | gravar `inbox_id`; a corrida do índice parcial passa a ser por par | alto | M |
| 8 | `logic.py:904` `get_last_closed_protocolo_for_contact` | base do popup de vínculo (plano 49), contact-scoped | escopar por inbox — senão o popup sugere vincular ao protocolo de **outro canal** | médio | S |
| 9 | `logic.py:1425` `reopen_protocolo` | guard "já existe protocolo aberto para este contato" | vira "…neste canal" | médio | S |
| 10 | `logic.py:1539` `merge_into_previous` · `logic.py:1619` `relink_suggestion_for_contact` · `logic.py:1776` `apply_resolve_decision` | continuidade entre protocolos, contact-scoped | escopar por inbox junto com o item 6 | médio | M |
| 11 | `routes.py` `GET /contacts/{contact_id}/protocolo` (`routes.py:244` vizinha) | devolve "o" protocolo do contato | aceitar `conversation_id`/`inbox_id`; sem ele, ambíguo | médio | S |
| 12 | `migrations/021_*.sql` | não existe | `ALTER TABLE … ADD COLUMN inbox_id integer` + troca do índice único | alto | M |

### 4.2 Core

| # | Arquivo:linha | Mudança | Risco |
|---|---|---|---|
| 13 | `app/services/messaging_service.py:1111` (`message.saved`) | acrescentar `channel_id` e `conversation_id` ao payload | baixo |
| 14 | `app/services/messaging_service.py:385` e `server/routes/contacts.py:1101`, `:1494`, `:1975` (`message.sent`) | idem, nos call sites onde `channel_id` já está no escopo | baixo |
| 15 | `plugins/semver.py:45` | `WHATSBOT_API_VERSION` → `1.3.0` + entrada em `docs/PLUGIN_API_CHANGELOG.md` + regenerar o golden | baixo |

### 4.3 Falsos positivos descartados

| Suspeita | Por que NÃO é o problema |
|---|---|
| "`close_protocolo` fecha as conversas do protocolo" | Não fecha. Ele só faz `UPDATE` na tabela do plugin (`logic.py:1349`). `_conversation_ids_of_protocolo` (`logic.py:1830`) é usado **só para leitura**, em `_has_open_conversation`. Quem fecha no core é o frontend. |
| "É o bug dos contatos duplicados 12↔13" | O Abimael é **um contato só** (7680). Duplicidade não participa desse caso — e o merge planejado **aumentaria** essa classe de incidente, não reduziria (ver P4). |
| "`resolveAndCloseAll` (`extends.js:49`) também cascateia" | Não. Fecha só a conversa recebida (`extends.js:73`); com ciclo aberto sobrando, o `POST /close` devolve erro e para. Entra no plano só pela mensagem (F1, item 3). |
| "`get_by_phone` casar variantes é o bug" | O casamento de variante é **desejado** (é o que reconcilia o 9º dígito). O defeito é o `.first()` **sem `ORDER BY`** — e, mesmo com ordenação determinística, a conversa continuaria sendo escolhida sem olhar o inbox. |
| "Basta o plugin ler `messages` pelo `msg_id`" | Funciona, mas é um `SELECT` por mensagem no caminho quente e quebra quando `msg_id` é nulo. Vira plano B da F2 (ver P3). |

---

## 5. Fases / Roadmap

```
WAVE 0   F0 ─ caracterização        · F1 ─ confirmação no fechar     ← 🟢 paralelas
            │                             │
            │  (barreira: F0 é a rede que valida tudo que vem depois)
            ▼                             ▼
WAVE 1   F2 ─ core: channel_id no bus  ──→ F3 ─ _resolve_target por inbox
            (🔴 sozinha, bump MINOR)        [depende de: F2]
                                             │
                                             ▼
WAVE 2   F4 ─ migration 021 (🔴) ──→ F5 ─ escopo por inbox em logic.py (🔴)
                                             │
                                             ▼
WAVE 3   F6 ─ rotas/UI do escopo   · F7 ─ backfill dos 9 históricos  ← 🟢 paralelas
                                             │
                                             ▼
WAVE 4   F8 ─ publicar zip + regressão (🔴)
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | testes | 🟢 | baixo | os 3 testes de caracterização reproduzem os bugs (vermelhos por motivo certo) |
| 0 | **F1** | frontend plugin | 🟢 | baixo | fechar protocolo com ciclo em outro canal pede confirmação nomeando o canal |
| 1 | **F2** | core | 🔴 `[bloqueia: F3]` | baixo | `channel_id` chega nos handlers; golden da API regenerado em 1.3.0 |
| 1 | **F3** | backend plugin | 🔴 `[depende de: F2]` | médio | mensagem em canal A nunca abre/mexe em ciclo do canal B |
| 2 | **F4** | DB plugin | 🔴 `[bloqueia: F5]` | alto | migration sobe e desce; 15.5k protocolos com `inbox_id` preenchido |
| 2 | **F5** | backend plugin | 🔴 `[depende de: F4]` | alto | dois canais do mesmo contato ⇒ dois protocolos abertos simultâneos |
| 3 | **F6** | rotas + UI | 🟢 | médio | painel do chat mostra o protocolo **daquele** canal |
| 3 | **F7** | dados | 🟢 | médio | os 9 protocolos cross-inbox históricos tratados conforme P2 |
| 4 | **F8** | release | 🔴 | médio | zip publicado, `--check` verde, produção atualizada |

---

### Fase F0 — Caracterização (rede de segurança)

**Objetivo:** provar os dois bugs com teste antes de tocar em qualquer linha de produção.

**Itens** (todos `[paralelo]` entre si):

1. Teste: contato com conversa aberta em **dois inboxes** → `close_protocolo` recusa (já é o
   comportamento correto; congela o contrato).
2. Teste do frontend puro sobre a escolha do ciclo — extrair de `forceResolveAndClose` a decisão
   "quais ciclos serão fechados" para um módulo puro testável com `node --test`, do jeito que
   `constants.test.js` faz nos canais. Hoje a decisão está embutida numa função `async` que fala
   HTTP, e por isso nunca foi testável.
3. Teste: `message.saved` de um canal, com o contato tendo conversa aberta em outro canal ⇒ hoje o
   ciclo nasce na conversa errada. Vermelho **esperado** até a F3.
4. Teste: `get_by_phone` com par duplicado 12↔13 ⇒ documentar o retorno indefinido (marcar
   `xfail`/skip com a razão; a correção é do plano de merge, não deste).

**Pronto quando:** os 3 primeiros rodam no runner do repo externo
(`python3 scripts/test_plugins.py protocolos`) e falham pelo motivo descrito, não por erro de setup.

#### Status de execução — Fase F0
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase F1 — Confirmação antes de fechar conversa de outro canal 🟢

**Objetivo:** o incidente relatado para de acontecer **hoje**, sem esperar a mudança de escopo.
Esta fase é auto-suficiente e pode ser publicada sozinha (1.34.0).

**Itens:**

1. `[sequencial]` `routes.py:304`: quando `close_protocolo` recusar por ciclo aberto, devolver
   também `code: "open_cycles"` e a lista `[{conversation_id, channel_id, inbox_name, started_at}]`.
   A resolução canal↔ciclo já existe no plugin — `_attach_channels` (`logic.py:2599`) faz
   exatamente o join `ciclo → atendimentos → inboxes → channel_id`; reusar, não reescrever.
   ⚠️ Manter a **string** de erro atual intacta: `static/protocolos_tab.js:1450` e `:1498` casam
   por regex, e o core em produção pode ficar uma versão atrás do painel.
2. `[sequencial]` Extrair de `static/protocolos_tab.js:1395` um módulo puro
   (`static/close_plan.js`) com `planClose({ cycles, currentConversationId })` →
   `{ target, alsoCloses: [...] }`. É o que a F0·2 testa.
3. `[sequencial]` `static/protocolos_tab.js:1398`: em vez de `find`, usar o plano acima. Se
   `alsoCloses` não estiver vazio, abrir confirmação: *"Este protocolo também tem atendimento
   aberto em **numero_recuperacao**. Finalizar vai fechar essa conversa também."* com
   Cancelar/Confirmar.
4. `[paralelo]` Mesmo tratamento no arrastar do Kanban (`static/protocolos_tab.js:1498`).
5. `[paralelo]` `static/extends.js:49`: exibir a mensagem estruturada quando o `POST /close`
   recusar, em vez do texto genérico.
6. `[paralelo]` Contraste do modal novo conferido no **modo escuro** (`wa-*`, `.wa-field`).

**Pronto quando:** com um contato tendo conversa aberta em dois canais, "Finalizar" abre a
confirmação nomeando o outro canal; cancelar não fecha **nada** (verificado no banco: `atendimentos.status`
e `plugin_protocolos_atendimentos.ended_at` intactos); confirmar fecha os dois, como o operador
escolheu. `node --test` do módulo puro verde.

#### Status de execução — Fase F1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(…)_
- **Problemas / pendências:** _(…)_
- **Verificação:** _(…)_

---

### Fase F2 — Core: `channel_id` + `conversation_id` no bus de mensagem 🔴

**Objetivo:** dar ao plugin o dado que ele precisa para não adivinhar canal. `[bloqueia: F3]`

**Itens:**

1. `[sequencial]` `app/services/messaging_service.py:1111` (`message.saved`, `source=batch_text`):
   acrescentar `"channel_id": channel_id` e `"conversation_id"` (da row `saved`). O `channel_id` já
   está no escopo — o `ws_manager.broadcast` logo acima o usa.
2. `[paralelo]` Mesmo acréscimo nos demais sites de `message.saved` (`batch_media`,
   `group_no_mention`) e de `message.sent`: `messaging_service.py:385`, `:573`,
   `server/routes/contacts.py:1007`, `:1101`, `:1494`, `:1975`.
   ⚠️ Onde o `conversation_id` **não** estiver no escopo, publicar só `channel_id` — melhor um campo
   ausente do que um valor errado; a F3 precisa tolerar `None`.
3. `[sequencial]` `plugins/semver.py:45` → `1.3.0`; entrada no topo de
   `docs/PLUGIN_API_CHANGELOG.md`; regenerar com
   `UPDATE_PLUGIN_API_SURFACE=1 venv/bin/python -m pytest tests/contracts/test_plugin_api_surface.py`.
   A regeneração **se recusa a rodar** enquanto a constante não andar — é o comportamento esperado.
4. `[paralelo]` Teste: um `message.saved` emitido carrega `channel_id` do canal certo.

**Pronto quando:** `venv/bin/python -m pytest tests/contracts tests/integration` verde; um plugin de
teste assinando `message.saved` recebe `channel_id`; `docs/PLUGIN_API_CHANGELOG.md` com a entrada
1.3.0 como **primeiro** heading de versão.

#### Status de execução — Fase F2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(…)_
- **Problemas / pendências:** _(…)_
- **Verificação:** _(…)_

---

### Fase F3 — `_resolve_target` por inbox 🔴 `[depende de: F2]`

**Objetivo:** mensagem do canal A nunca mais mexe em conversa/ciclo do canal B.

**Itens:**

1. `[sequencial]` `logic.py:3451`: quando o payload trouxer `conversation_id`, usar direto
   (`conversation_repo.get`). Senão, com `channel_id`, resolver o inbox e usar
   `get_open_for_contact_inbox` / `get_latest_for_contact_inbox`
   (`conversation_repo.py:275` e `:292`).
2. `[sequencial]` **Fail-open explícito**: sem `channel_id` no payload (core anterior à F2), cair no
   comportamento de hoje. O plugin declara `whatsbot_api_version: ">=1.0,<2.0"` e precisa continuar
   carregando num core antigo — a regra do repo. Logar em `debug`, uma vez por processo.
3. `[paralelo]` Aplicar o mesmo idioma em `on_inbound` (`logic.py:3472`) e `on_outbound`
   (`logic.py:3497`), que hoje passam o `atend` do `_resolve_target` adiante sem checar canal.
4. `[paralelo]` Tornar `get_by_phone` **determinístico** é do plano de merge, não deste — mas anotar
   em `db/repositories/contact_repo.py:94` que o `.first()` sem `ORDER BY` é indefinido com par
   duplicado (comentário, sem mudança de comportamento).

**Pronto quando:** o teste F0·3 fica verde; contato com conversa aberta em dois canais recebe
mensagem no canal A e só o ciclo do canal A se mexe.

#### Status de execução — Fase F3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(…)_
- **Problemas / pendências:** _(…)_
- **Verificação:** _(…)_

---

### Fase F4 — Migration 021: `inbox_id` no protocolo 🔴 `[bloqueia: F5]`

**Objetivo:** o banco passa a permitir um protocolo aberto **por canal**.

**Itens:**

1. `[sequencial]` `migrations/021_protocolo_por_inbox.sql` — prefixo `plugin_protocolos_` obrigatório
   em todo objeto. ⚠️ O migrator **splita por `;` antes** de tirar comentários: nenhum comentário
   pode conter `;`.
   - `ALTER TABLE plugin_protocolos_protocolos ADD COLUMN IF NOT EXISTS inbox_id integer;`
   - backfill: `inbox_id` = inbox da conversa do ciclo **mais recente** do protocolo (mesmo join do
     `_attach_channels`, `logic.py:2599`); protocolo sem ciclo fica `NULL`.
   - `DROP INDEX IF EXISTS plugin_protocolos_one_open_per_contact;`
   - `CREATE UNIQUE INDEX plugin_protocolos_one_open_per_contact_inbox ON plugin_protocolos_protocolos (contact_id, inbox_id) WHERE status = 'aberto';`
2. `[sequencial]` ⚠️ **`NULL` não colide em índice único no Postgres.** Protocolo legado sem ciclo
   (`inbox_id IS NULL`) deixa de ser travado pelo índice — dois abertos passariam. Decidir entre
   `COALESCE(inbox_id, -1)` no índice ou um `NOT NULL` com default sentinela. **Recomendação:**
   índice sobre `(contact_id, COALESCE(inbox_id, -1))`, que preserva o travamento sem inventar linha.
3. `[sequencial]` **Ensaio antes do commit**: rodar o backfill numa transação e conferir contra o
   baseline (protocolos abertos por contato antes × pares `(contact_id, inbox_id)` depois). Se o
   backfill criar violação do índice, é porque existem 2 abertos do mesmo contato **no mesmo
   inbox** — não deveria haver, e é sinal de dado sujo a tratar antes.
4. `[paralelo]` Medir os 9 protocolos cross-inbox: qual inbox o backfill dá a cada um (o do ciclo
   mais recente) e se algum deles tem ciclo **aberto** em outro inbox. Alimenta a F7.

**Pronto quando:** migration aplicada num banco de teste com cópia do volume de produção; contagem
de protocolos inalterada; `inbox_id` preenchido para todo protocolo com ao menos um ciclo; nenhum
par `(contact_id, inbox_id, 'aberto')` duplicado.

#### Status de execução — Fase F4
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(…)_
- **Problemas / pendências:** _(…)_
- **Verificação:** _(…)_

---

### Fase F5 — Escopo por inbox no `logic.py` 🔴 `[depende de: F4]`

**Objetivo:** o código passa a ler/escrever protocolo por `(contato, canal)`.

**Itens** (`[sequencial]` entre si — mesma superfície):

1. `logic.py:889` `_select_open_protocolo(contact_id, inbox_id)`.
2. `logic.py:900` `get_open_protocolo_for_contact` — assinatura ganha `inbox_id`; manter um caminho
   sem inbox só para chamadas de listagem/relatório, marcado como tal.
3. `logic.py:999` `ensure_protocolo_ex` — gravar `inbox_id`; a corrida do `IntegrityError` passa a
   ser por par. Derivar o inbox da `conversation_id` que já é parâmetro.
4. `logic.py:904` `get_last_closed_protocolo_for_contact` — escopar por inbox. ⚠️ Sem isto, o popup
   de vínculo do plano 49 passa a sugerir vincular a conversa do canal A ao protocolo fechado do
   canal B — o incidente ao contrário.
5. `logic.py:1425` `reopen_protocolo` — guard vira "já existe protocolo aberto **neste canal**".
6. `logic.py:1539` `merge_into_previous`, `logic.py:1619` `relink_suggestion_for_contact`,
   `logic.py:1776` `apply_resolve_decision` — mesma escopagem.

**Pronto quando:** o mesmo contato recebendo mensagem em dois canais gera **dois** protocolos
abertos, cada um com seus ciclos; fechar um não toca no outro (nem no banco, nem na tela); suíte do
plugin verde.

#### Status de execução — Fase F5
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(…)_
- **Problemas / pendências:** _(…)_
- **Verificação:** _(…)_

---

### Fase F6 — Rotas e UI do novo escopo 🟢

**Objetivo:** o painel mostra o protocolo **daquele** canal, e não "o" protocolo do contato.

**Itens:**

1. `[sequencial]` `GET /contacts/{contact_id}/protocolo` (vizinha de `routes.py:244`) passa a aceitar
   `conversation_id` (preferido) ou `inbox_id`. Sem parâmetro, manter o comportamento antigo +
   `WARNING` — é chamada por `protoShortcut` (`static/extends.js`) e por telas que podem estar
   desatualizadas.
2. `[paralelo]` `static/extends.js` — `protoShortcut` e `contactIdOf` passam a mandar a conversa.
3. `[paralelo]` Kanban/lista: um contato com dois protocolos abertos agora rende **duas linhas**.
   Conferir que a coluna de canal (já resolvida por `_attach_channels`, `logic.py:2599`) aparece,
   senão as duas linhas ficam indistinguíveis na tela.
4. `[paralelo]` Modo escuro conferido em qualquer rótulo novo.

**Pronto quando:** abrir a conversa do canal A mostra o protocolo do canal A; o Kanban distingue as
duas linhas pelo canal.

#### Status de execução — Fase F6
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(…)_
- **Problemas / pendências:** _(…)_
- **Verificação:** _(…)_

---

### Fase F7 — Os 9 protocolos históricos cross-inbox 🟢

**Objetivo:** decidir e aplicar o tratamento do passado. Depende de **P2**.

**Itens:**

1. `[sequencial]` Listar os 9 com seus ciclos e inboxes (query da F4·4).
2. `[sequencial]` Aplicar a decisão de P2 — recomendação: **não retroagir**. Todos estão fechados;
   o backfill os ancora no inbox do ciclo mais recente e o histórico continua legível.
3. `[paralelo]` Verificação: nenhum órfão novo; contagens contra o baseline.

**Pronto quando:** os 9 conferidos um a um e a decisão registrada aqui.

#### Status de execução — Fase F7
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(…)_
- **Problemas / pendências:** _(…)_
- **Verificação:** _(…)_

---

### Fase F8 — Publicação 🔴

**Objetivo:** entregar sem regressão.

**Itens:**

1. `[sequencial]` Bump da versão em `src/plugin.yaml`; `python3 scripts/build_plugins.py protocolos`;
   `--check` verde. ⚠️ `--check` pode acusar "outdated" falso por **umask** (zip 664 em vez de 644) —
   conferir o modo antes de rebuildar.
2. `[sequencial]` **Instalar a cópia local antes de publicar** — a cópia viva é
   `storages/plugins/protocolos/`, e é ela que o usuário testa.
3. `[sequencial]` ⚠️ **Ordem de deploy**: o core (F2) vai **antes** do zip. O plugin degrada
   graciosamente num core antigo (F3·2), mas o inverso não foi projetado.
4. `[paralelo]` Antes de publicar, reconferir a versão de produção na tabela `plugins` — já houve
   caso de versão publicada por outra pessoa no meio do trabalho.

**Pronto quando:** produção com o zip novo; um fechamento real em contato multicanal se comporta
como a F1 descreve.

#### Status de execução — Fase F8
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(…)_
- **Problemas / pendências:** _(…)_
- **Verificação:** _(…)_

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Migration 021 | 15.5k protocolos; backfill errado desancora o Kanban inteiro | ensaio em transação + baseline de contagens (F4·3); as tabelas do plugin **não têm FK**, então nada cascateia — mas nada protege também |
| `NULL` no índice único | `NULL` não colide no Postgres ⇒ dois protocolos abertos passariam | índice sobre `COALESCE(inbox_id, -1)` (F4·2) |
| Mudança de semântica | um cliente atendido em 2 canais passa a ter **2 números de protocolo** simultâneos | é o que D1 pede; validar com quem opera **antes** da F5 (ver P1) |
| Ordem de deploy | zip novo em core antigo = `channel_id` ausente | fail-open na F3·2 + core primeiro (F8·3) |
| Regex de erro no frontend | `static/protocolos_tab.js:1450` casa a mensagem por texto | manter a string atual e **acrescentar** o código (F1·1); nunca trocar |
| Restart do plugin | enable/disable derruba o processo; estado em memória se perde | nada novo em memória; tudo em `plugin_protocolos_*` |
| Migrator do plugin | splita SQL por `;` **antes** de remover comentários | nenhum `;` dentro de comentário na 021 |
| `WHATSBOT_API_VERSION` | esquecer o bump deixa o guard vermelho e o plugin sem como declarar o que precisa | F2·3 é bloqueante da fase |
| Merge de duplicados | executar o merge antes deste plano concentra mais conversas por contato e **aumenta** a exposição | ver P4 |
| Modo escuro | modal de confirmação novo (F1) | `wa-*`/`.wa-field`, conferido com o tema ligado |

---

## 7. Perguntas em aberto

**P1 — O protocolo passa a ser por canal ou por conversa?**
⏸️ ADIADO — confirmar com quem opera antes da F5.
Contexto: D1 diz "vinculado à conversa". Mas o protocolo existe justamente para dar **continuidade
entre** conversas do mesmo cliente naquele canal (é o que `merge_into_previous` e o popup do plano 49
fazem). Por conversa, cada reabertura viraria um protocolo novo e o número perderia sentido.
(a) por `(contact_id, inbox_id)` — **recomendada**: atende D1 na prática (canais não se contaminam),
preserva a continuidade e segue o precedente do core (`get_open_for_contact_scoped`, plano 37).
(b) por `conversation_id` — mais literal, mas destrói a continuidade e multiplica protocolos.
**Recomendação: (a).**

**P2 — O que fazer com os 9 protocolos históricos cross-inbox?**
⏸️ ADIADO — decidir na F7.
(a) não retroagir: backfill ancora no inbox do ciclo mais recente e pronto — **recomendada**, os 9
estão fechados e ninguém opera sobre eles.
(b) dividir cada um em dois protocolos por inbox: reescreve histórico e quebra os números de
protocolo já informados ao cliente.
**Recomendação: (a).**

**P3 — Costura no core (F2) ou lookup por `msg_id` dentro do plugin?**
⏸️ ADIADO — decidir antes da F2.
(a) core publica `channel_id`/`conversation_id` — **recomendada**: passa nos três critérios (§3),
serve aos 6 plugins que hoje adivinham, custo zero em runtime.
(b) plugin lê `messages` pelo `msg_id` do payload: zero mudança no core, mas um `SELECT` por
mensagem no caminho quente e falha quando `msg_id` é nulo.
**Recomendação: (a)**, com (b) como plano B se o bump de API for indesejado agora.

**P4 — Este plano vem antes ou depois do merge de contatos duplicados?**
⏸️ ADIADO — decidir junto com o dono do merge.
O merge consolida 203 pares, o que **concentra mais conversas sob um mesmo contato** — exatamente a
condição que produz a cascata. Fazer o merge primeiro aumenta a janela de exposição.
**Recomendação:** F1 (a confirmação) **antes** do merge, obrigatoriamente; o resto pode vir depois.
E o `plano-merge-contatos-duplicados/README.md` precisa de dois reparos independentes deste plano:
o par bloqueante mudou (era Adilson 7213/12878; hoje é **Genilson 5848/5894**, único com as duas
conversas abertas) e a premissa "não há bug de core a corrigir" vale para a *criação* de duplicata,
não para a *leitura* (`contact_repo.py:94`).

---

## 8. Apêndice — arquivos-chave

**Plugin `protocolos`** (`whatsbot-pro-plugins/plugins/protocolos/src/`)

| Camada | Arquivo |
|---|---|
| Backend | `logic.py` (`:889`, `:900`, `:904`, `:999`, `:1277`, `:1289`, `:1349`, `:1425`, `:1539`, `:1619`, `:1776`, `:1830`, `:2599`, `:3251`, `:3292`, `:3366`, `:3451`, `:3472`, `:3497`) |
| Rotas | `routes.py` (`:244`, `:304`, `:657`) |
| Frontend | `static/protocolos_tab.js` (`:1395`, `:1398`, `:1419`, `:1430`, `:1447`, `:1450`, `:1498`), `static/extends.js` (`:49`, `:73`), **novo** `static/close_plan.js` |
| DB | **nova** `migrations/021_protocolo_por_inbox.sql` |
| Manifest | `plugin.yaml` (versão) |
| Testes | `whatsbot-pro-plugins/plugins/protocolos/tests/` |

**Core** (`whatsbot-pro/`)

| Camada | Arquivo |
|---|---|
| Bus | `app/services/messaging_service.py` (`:385`, `:573`, `:1111`), `server/routes/contacts.py` (`:1007`, `:1101`, `:1494`, `:1975`) |
| Versão da API | `plugins/semver.py:45`, `docs/PLUGIN_API_CHANGELOG.md`, `tests/goldens/plugin_api_surface.json` |
| Leitura (só comentário) | `db/repositories/contact_repo.py:94` |
| Referência (não muda) | `db/repositories/conversation_repo.py` (`:252`, `:264`, `:275`, `:292`, `:304`) |

---

## 9. Checklist de verificação

- [ ] `venv/bin/python -m pytest tests/contracts tests/integration` verde (Postgres, `WHATSBOT_TEST_DB_URL`)
- [ ] `cd ../whatsbot-pro-plugins && python3 scripts/test_plugins.py protocolos` verde
- [ ] `node --test` verde no módulo puro novo (`static/close_plan.js`)
- [ ] Migration 021 aplica e reverte sem perda; contagens batem com o baseline
- [ ] Nenhum par `(contact_id, inbox_id, 'aberto')` duplicado após o backfill
- [ ] Toggle do plugin (disable → enable) sem erro no boot; `load_error` vazio
- [ ] Modal de confirmação legível no **modo escuro**
- [ ] `docs/PLUGIN_API_CHANGELOG.md` com 1.3.0 como primeiro heading de versão
- [ ] `python3 scripts/build_plugins.py protocolos --check` verde (conferir umask antes de rebuildar)
- [ ] Cópia instalada em `storages/plugins/protocolos/` atualizada antes de publicar
- [ ] Core deployado **antes** do zip
- [ ] Versão de produção reconferida na tabela `plugins` imediatamente antes de publicar
- [ ] Teste manual do incidente: contato com conversa aberta em dois canais → finalizar pede confirmação e respeita o Cancelar
