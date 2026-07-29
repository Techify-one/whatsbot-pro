# Plano 89 — Link de conversa sempre abre: o deep-link deixa de depender da sidebar

> **Status:** ✅ EXECUTADO (F1–F5 · 2026-07-29; F6 com o roteiro manual pendente) · **Data:** 2026-07-28 · **Escopo:** pequeno (frontend) + 1 teste de backend
> **Origem:** Levantado durante o [plano 88](88-plano-hub-volta-em-minhas-conversas.md) e destacado pelo usuário: *"se uma pessoa me enviar um link de conversa, eu não conseguiria abrir porque não é minha? Isso não pode acontecer"*. **Método:** auditoria com 8 agentes (4 de investigação + 3 **adversariais** tentando refutar o achado + 1 de síntese), leitura do código real (`arquivo:linha`), `git log -L` na origem do guard, e **medição no banco de produção** `whatsbot@10.8.100.5` via MCP vault.
> **Resposta à pergunta: não, "não é minha" nunca impede.** O backend não tem noção de dono na leitura de conversa — nem `assignee_user_id`, nem equipe. O que quebra o link é um **guard de UI mal formulado** ([useConversationSelection.js:172](../web/static/js/components/contacts/hooks/useConversationSelection.js#L172)): quando a lista da sidebar chega **vazia do servidor**, o deep-link não é resolvido e **não há feedback nenhum** — URL intacta, painel no placeholder, sem erro. É bug **pré-existente** (nasceu em `288d686`, 2026-06-20), independente do plano 88; o 88 apenas o tiraria da raridade.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | ✅ (2026-07-28) **Plano próprio, executado ANTES do 88** — escolha do usuário | Este plano não depende do 88 e pode ser mergeado sozinho. O 88 perde a Fase 4 e passa a **depender** deste. Corrige hoje um bug que já afeta a operação |
| D2 | ✅ (2026-07-28) **Um link de conversa é um endereço permanente.** Abrir não pode depender de qual aba/filtro o operador tinha na sidebar | É o princípio que decide a forma da correção: remover o acoplamento, não reduzir a frequência dele |
| D3 | ✅ (2026-07-28) **O servidor está certo como está** — ler conversa alheia é permitido por design | Nada de backend muda. O único `.py` tocado é um **teste novo** que congela esse contrato (F5) |
| D4 | ✅ (2026-07-28) O bloqueio **legítimo** (canal fora da membership → 404) deve continuar existindo **e aparecer** | Hoje ele já renderiza o card "Não foi possível abrir esta conversa" ([Contacts.js:433-441](../web/static/js/components/contacts/Contacts.js#L433)). Com a correção, esse card passa a ser alcançável em mais casos — é ganho, não regressão |
| D5 | ✅ (2026-07-28) A correção **não** pode ser "forçar a aba para Todas quando há deep-link" | Não resolve: chip "Abertas" + conversa fechada continua devolvendo zero linhas. E muda a view sem o operador pedir. Descartado com razão registrada (§3.1) |

---

## 1. Resumo executivo

O efeito que traduz `/conversations/<id>` em "conversa aberta" só roda quando a lista da sidebar tem **pelo menos uma linha**:

```js
if (initialConversationId === lastResolvedConvId.current) return;
if (contacts.length === 0 || loading) return;              // ← linha 172
```

A condição conflaciona **"a lista ainda não chegou"** (esperar é certo) com **"a lista chegou e está vazia"** (aí é preciso abrir mesmo assim). O `loading` já distingue as duas — o `contacts.length === 0` é redundante no primeiro caso e destrutivo no segundo.

O ramo `else` logo abaixo ([:178-183](../web/static/js/components/contacts/hooks/useConversationSelection.js#L178)) **já sabe** abrir uma conversa sem linha na sidebar — é o caminho usado hoje para conversa além da 1ª página ou arquivada — e o loader de detalhe é conversa-cêntrico, derivando canal e telefone **da resposta** do endpoint ([:269](../web/static/js/components/contacts/hooks/useConversationSelection.js#L269), [:272](../web/static/js/components/contacts/hooks/useConversationSelection.js#L272)). A capacidade existe; o guard é que a bloqueia.

A correção é remover o acoplamento (trocar o guard por `if (loading) return;`), extrair a decisão para um módulo **puro e testado** (hoje esse caminho tem **zero** cobertura), e fechar as duas arestas que o caminho "abrir por id" tem: a janela em que o canal fica em `'default'` e a **invisibilidade no mobile**.

---

## 2. Como funciona hoje (mapa)

### 2.1 As duas camadas — só uma está em jogo

| Camada | Regra real | Bloqueia por "não é minha"? |
|---|---|---|
| **Autorização (servidor)** | `conversation.read` + membership de **inbox/canal** ([conversations.py:306-321](../server/routes/conversations.py#L306), [:339-354](../server/routes/conversations.py#L339); [authz.py:77-90](../server/authz.py#L77)) | **Não.** `get_with_channel` filtra **só por id** — sem `assignee_user_id`, sem `status`, sem `is_archived` ([conversation_repo.py:592-601](../db/repositories/conversation_repo.py#L592)). Conversa alheia, não atribuída, fechada ou arquivada devolve **200** |
| **Guard de UI (cliente)** | `contacts.length === 0` no efeito de resolução | **Sim, indiretamente** — e é o único ponto que quebra |

⚠️ Não existe escopo por **equipe**: `conversations.team_id` é coluna nullable sem FK, não lida por nenhum caminho de autorização ([db/tables.py:475](../db/tables.py#L475)).

Os **4 bloqueios legítimos**, todos com feedback visível ou esperado:

| Situação | Resposta | O operador vê |
|---|---|---|
| Sem `conversation.read` | 403 "Permissão negada." | card de erro |
| Canal fora da membership | 404 "Conversa não encontrada." | card de erro |
| Sem sessão (com ≥1 usuário cadastrado) | 401 | tela de login |
| **Lista vazia (o guard)** | *nenhuma requisição é feita* | **nada** ⚠️ |

### 2.2 Medição na instância de produção (2026-07-28)

| Canal (inbox) | Conversas | Membros |
|---|---|---|
| Atendimento | **14.341** (95,6%) | **os 13 usuários** |
| RedesBrasil_bot | 570 | todos, menos Mábia |
| whatsapp_oficial_disparo | 47 | todos, menos Mábia |
| Avisos Curseduca | 25 | 6 de 13 |
| Site | 4 | só o usuário "Teste" |
| numero_recuperacao / Teste | 6 / 0 | todos |

5 usuários são `admin` → curto-circuito no RBAC ([rbac_repo.py:78](../db/repositories/rbac_repo.py#L78)) → veem tudo. Os 8 `atendente` têm `conversation.read` e são membros do canal que concentra ~96% das conversas. **Conclusão: hoje, na prática, qualquer link que circule internamente é autorizado para quase todo mundo** — o que falha é o guard.

### 2.3 O caminho completo do link

| Etapa | Local | O que acontece |
|-------|-------|----------------|
| 1. `/conversations/1851` | [routing.js:81](../web/static/js/components/shell/routing.js#L81), [:107-110](../web/static/js/components/shell/routing.js#L107) | regex → `tab='contacts'` + `initialConversationId=1851`. **Não** é rota de entidade (`useDeepLink` devolve null) |
| 2. Render | [ScreenRouter.js:125-129](../web/static/js/components/shell/ScreenRouter.js#L125) | `<Contacts/>` monta, gateado só por `conversation.read` — **nenhum gate por atribuição** |
| 3. Resolução | [useConversationSelection.js:169-190](../web/static/js/components/contacts/hooks/useConversationSelection.js#L169) | o guard da §1. Com linha → `select`; sem linha → `else` (abre por id) |
| 4. Carga do detalhe | [:255-296](../web/static/js/components/contacts/hooks/useConversationSelection.js#L255) | `GET /api/atendimentos/{id}/messages` ([api.js:234-241](../web/static/js/services/api.js#L234)). Deps `[selected, selectedConvId, retryNonce]` — **não depende da lista** |

### 2.4 Por que o guard existe (verificado por `git log`)

Nasceu em **`f3eca48`** ("feat(web): add contact URL navigation and deep linking") no deep-link de **contato**, onde ele faz sentido: sem linha na lista não há o que abrir — não existe endpoint "abrir contato por id" equivalente. Foi **copiado verbatim** para o ramo de conversa em **`288d686`** ("feat: planos 11 (runtime multicanal) + 12"), **no mesmo commit que criou o ramo `else`** que o torna desnecessário. Nunca foi reavaliado.

---

## 3. Inventário das mudanças

| # | Onde | O que fazer | Risco | Esforço |
|---|------|-------------|-------|---------|
| M1 | novo `web/static/js/services/deepLinkResolve.js` | Extrair a DECISÃO do efeito para uma função pura: `resolveDeepLink({initialConversationId, initialContactId, contacts, loading, lastResolvedConvId, lastResolvedId})` → `{action: 'wait'\|'select'\|'open_by_id'\|'deselect'\|'noop', row?, conversationId?}`. Sem preact/DOM/rede ⇒ `node --test` | baixo | M |
| M2 | novo `web/static/js/services/deepLinkResolve.test.js` | Os 7 casos da F1. **É a única coisa que impede a regressão voltar** — hoje não há teste nenhum desse caminho | baixo | S |
| M3 | [useConversationSelection.js:169-214](../web/static/js/components/contacts/hooks/useConversationSelection.js#L169) | O efeito passa a ser consumidor fino do módulo: chama `resolveDeepLink(...)` e aplica a ação. Ramo da conversa espera **só** o `loading`; ramo do contato **mantém** o guard (não há `else` lá) | médio | M |
| M4 | [useConversationSelection.js](../web/static/js/components/contacts/hooks/useConversationSelection.js) (efeito novo) | **Adoção tardia**: quando `contacts` mudar e aparecer a linha do `selectedConvId` **enquanto `selected` ainda for nulo**, adotar `phone` + `channel_id` da linha. Fecha a janela em que compositor/presença operam no canal `'default'` | baixo | S |
| M5 | [Contacts.js:431](../web/static/js/components/contacts/Contacts.js#L431) | `${!selected ? 'hidden lg:flex' : 'flex'}` — a visibilidade do chat depende do **telefone**, que é nulo no caminho "abrir por id". Passar a considerar `selectedConvId` | médio | S |
| M6 | novo teste em `tests/` | Congelar o contrato do servidor (D3): conversa de OUTRO dono / não atribuída / fechada / arquivada ⇒ **200** para membro do inbox sem `read_all` | baixo | S |

### 3.1 Falsos positivos e formulações erradas descartadas

| Suspeita | Por que **não** procede |
|---|---|
| "Sidebar vazia quebra o link" | **Errado, e importa.** A sidebar renderiza `displayedContacts` ([Contacts.js:351](../web/static/js/components/contacts/Contacts.js#L351)); o guard lê o array **cru** `contacts`. Quando o vazio vem de corte **client-side** (`serverMode=false` — [conversationFilterSpec.js:78](../web/static/js/services/conversationFilterSpec.js#L78)) ou do gate `isVisibleInSidebar` ([conversationRows.js:91-96](../web/static/js/services/conversationRows.js#L91)), a sidebar aparece vazia e **o link abre normalmente**. O bug exige vazio vindo do **servidor**. ⚠️ Testar o cenário errado dá falso "não reproduz" |
| "O link nunca mais abre / trava para sempre" | **Exagero.** O carimbo `lastResolvedConvId` está **depois** do guard ([:188](../web/static/js/components/contacts/hooks/useConversationSelection.js#L188)), então o efeito fica **armado**: qualquer coisa que popule `contacts` resolve sozinha — `conversation_upsert` via WS ([useConversationWsEvents.js:268](../web/static/js/components/contacts/hooks/useConversationWsEvents.js#L268)), troca de chip/aba ([useConversationFilters.js:173-178](../web/static/js/components/contacts/hooks/useConversationFilters.js#L173)), ou **digitar uma busca** (que ignora o filtro de status, [:101](../web/static/js/components/contacts/hooks/useConversationFilters.js#L101) e [:121](../web/static/js/components/contacts/hooks/useConversationFilters.js#L121), então até conversa fechada reaparece). O correto é **"enquanto a lista permanecer vazia"** |
| "Basta o operador recarregar / re-clicar o link" | **Não cura**: mesma URL, mesmo filtro default, mesmo vazio. E sair pelo menu e voltar é **pior** — `setTab` faz `pushState` + `PopStateEvent` sintético ([App.js:163-172](../web/static/js/components/shell/App.js#L163)) e o listener re-lê a URL limpa ([:198-210](../web/static/js/components/shell/App.js#L198)), **descartando** o `initialConversationId` |
| "Resolver o deep-link no mount, sem gatear em nada" | Degrada **toda** abertura: no 1º render `contacts` está vazio, o `find` falha, cai no `else` e o carimbo fecha a questão ⇒ **todo** link nasceria com telefone nulo e canal `'default'`, com cabeçalho/compositor no canal errado até a resposta chegar. Rejeitado; M4 recupera o benefício sem o custo |
| "Forçar a aba para `all` quando há deep-link" | D5. Não resolve o caso mais traiçoeiro (chip "Abertas" + conversa fechada = zero linhas) e surpreende o operador. **Onde isso é legítimo** é *depois* de abrir (a aba cede para mostrar a conversa aberta), nunca como pré-condição |
| "Injetar a conversa aberta na lista" | Exige fetch extra ou linha sintética, inventa linha que a view não deveria conter e colide com `rowMatchesView` ([conversationRows.js:261-270](../web/static/js/services/conversationRows.js#L261)) no próximo upsert |
| "É regressão criada pelo plano 88" | **Não.** É pré-existente desde `288d686` (2026-06-20). O 88 só aumenta a exposição — daí este plano vir antes (D1) |

---

## 4. Fases / Roadmap

```
WAVE 0   F1 (módulo puro + teste)   ·   F5 (teste de autorização)     🟢 independentes entre si
              │
WAVE 1   F2 (o efeito consome o módulo; guard relaxado)               🔴 [depende de: F1]
              │
              ├──────────────┬────────────────────────────────────────┐
WAVE 2   F3 (adoção tardia)  F4 (visibilidade no mobile)              🟢 [dependem de: F2]
              └──────────────┴────────────────────────────────────────┘
WAVE 3   F6 (validação)                                               🔴
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|------|------|------------|-------|-------|----------------|
| 0 | F1 | `services/deepLinkResolve.js` + teste | 🟢 [bloqueia: F2] | baixo | `node --test` verde, app inalterado (aditivo) |
| 0 | F5 | teste Python do contrato do servidor | 🟢 | baixo | Teste novo verde **sem tocar em código de produção** |
| 1 | F2 | o efeito passa a consumir o módulo | 🔴 [depende de: F1] | médio | Link abre com a sidebar vazia |
| 2 | F3 | adoção tardia de telefone/canal | 🟢 [depende de: F2] | baixo | Compositor no canal certo assim que a linha aparece |
| 2 | F4 | visibilidade no mobile | 🟢 [depende de: F2] | médio | Link abre abaixo do breakpoint `lg` |
| 3 | F6 | validação | 🔴 | baixo | Checklist da §7 |

Disciplina: **um refactor por commit** (F1 sozinha; F2 sozinha; F3 e F4 separados); **verde a cada fase**; F1 é **caracterização antes** — o módulo puro nasce reproduzindo o comportamento atual, e só a F2 muda o comportamento.

---

### Fase 1 — Extrair a decisão para um módulo puro (🟢, bloqueia F2)

**Objetivo:** tornar testável a decisão que hoje vive dentro de um efeito com histórico de sutilezas — e que não tem **nenhuma** cobertura.

**Contexto:** os 23 `*.test.js` do repo são todos de módulos puros com `node --test`; **não há `package.json`, jsdom nem testing-library**. Não invente infra de teste de componente.

**Itens**
1. `[sequencial]` Criar `web/static/js/services/deepLinkResolve.js` com a função pura de M1. Ela **reproduz o comportamento atual** — inclusive o `contacts.length === 0` — nesta fase. É caracterização.
2. `[paralelo]` Criar `deepLinkResolve.test.js` cobrindo:
   - `loading: true` ⇒ `wait`, em qualquer tamanho de lista
   - lista com 5 linhas, nenhuma casando ⇒ `open_by_id`
   - lista com a linha ⇒ `select` com `row.phone` e `row.channel_id`
   - `initialConversationId === lastResolvedConvId` ⇒ `noop` (não reabre em loop quando a lista muda depois)
   - **ramo contato** com `contacts: []` ⇒ `wait` (nunca `open_by_id`) — trava a assimetria deliberada
   - ambos os ids nulos, com algo resolvido antes ⇒ `deselect`
   - **`contacts: []`, `loading: false`, `initialConversationId` setado ⇒ `wait`** — este é o teste que a F2 vai **inverter** para `open_by_id`. Escrito agora com um comentário dizendo que é o comportamento defeituoso sendo congelado antes da troca

**Pronto quando:** `node --test web/static/js/services/deepLinkResolve.test.js` verde e **nenhum** arquivo do app importa o módulo ainda (o app roda byte-idêntico).

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-07-29)
- **O que foi feito:** novos `web/static/js/services/deepLinkResolve.js` (função pura `resolveDeepLink`) e `deepLinkResolve.test.js` (7 casos). Nenhum arquivo do app importa o módulo nesta fase — o app roda byte-idêntico.
- **Como foi feito / decisões:** a função devolve `{action, via?, row?, conversationId?, contactId?}`. O campo `via` (`'conversation' | 'contact'`) foi acrescentado ao contrato de M1 para o hook saber QUAL carimbo gravar (`lastResolvedConvId` × `lastResolvedId`) sem re-derivar a condição — o módulo decide, o hook aplica. O ramo do contato que não acha linha devolve `wait` (o efeito original não carimbava nada nesse caso, logo re-tentava).
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --test web/static/js/services/deepLinkResolve.test.js` → 7/7 verde.

---

### Fase 5 — Congelar o contrato do servidor (🟢, independente — pode ir junto com a F1)

**Objetivo:** documentar como **contrato** o que hoje é acidente de implementação — que ler conversa alheia é permitido — para que ninguém "endureça" o endpoint achando que corrige privacidade e quebre todo permalink do produto.

**Contexto:** o precedente é [tests/test_conversation_read_isolation.py:72-95](../tests/test_conversation_read_isolation.py#L72), que testa **inbox**, nunca dono.

**Itens**
1. `[sequencial]` Ao lado do teste existente, adicionar casos: usuário com `conversation.read`, **membro do inbox**, **sem** `read_all`, abrindo (a) conversa com `assignee_user_id` de OUTRO usuário, (b) conversa com `assignee_user_id = NULL`, (c) conversa `is_archived=1`, (d) conversa `status='resolved'` ⇒ **200** em `GET /api/atendimentos/{id}` **e** em `/{id}/messages`.
2. `[sequencial]` Comentário no topo do bloco explicando **por que** isso é contrato: permalinks de protocolos/melhorias/agendamento e o compartilhamento de link entre atendentes dependem disso (D3).

**Pronto quando:** os casos novos passam **sem alterar nenhuma linha de código de produção** — se algum falhar, o servidor não é o que este plano afirma e a §2.1 precisa ser corrigida antes de seguir.

#### Status de execução — Fase 5
**Estado:** ✅ Concluída (2026-07-29)
- **O que foi feito:** bloco novo em [tests/test_conversation_read_isolation.py](../tests/test_conversation_read_isolation.py) — helper `_plain_user`, fixture `ownership_env` e o teste parametrizado `test_read_is_not_scoped_by_ownership` (4 estados × 2 endpoints). Comentário-cabeçalho explica **por que** é contrato (permalinks + link colado no chat interno) e que privacidade aqui é membership de canal, nunca atribuição.
- **Como foi feito / decisões:** o estado "resolvido" da UI é `status='closed'` no banco (o vocabulário do repo é `open`/`closed`, ver [conversation_service.py:200](../app/services/conversation_service.py#L200)) — a chave da fixture ficou `closed` para não induzir a erro. O dono alheio é um usuário SEM membership (`_plain_user`), porque `_scoped_user` chama `set_members`, que REPLACE a lista e expulsaria o usuário do teste.
- **Problemas / pendências:** nenhuma. **Zero linhas de código de produção alteradas** — o servidor é exatamente o que a §2.1 afirma.
- **Verificação:** `venv/bin/python -m pytest tests/test_conversation_read_isolation.py -q` → **8 passed** (4 antigos + 4 novos).

---

### Fase 2 — O efeito consome o módulo e o guard cede (🔴, depende de F1)

**Objetivo:** o link abrir com a sidebar vazia.

**Itens**
1. `[sequencial]` [useConversationSelection.js:169-214](../web/static/js/components/contacts/hooks/useConversationSelection.js#L169): substituir a lógica inline por `resolveDeepLink(...)` + um `switch` sobre a ação. Os setters e o carimbo (`lastResolvedConvId`/`lastResolvedId`) continuam no hook — o módulo **decide**, o hook **aplica**.
2. `[sequencial]` No módulo, trocar a regra do ramo da conversa para esperar **só** o `loading`. Inverter o teste correspondente da F1 (`open_by_id` em vez de `wait`), removendo o comentário de "comportamento defeituoso congelado".
3. `[sequencial]` **Manter** o guard no ramo do contato, com comentário explicando por quê (não existe `else` ali; o carimbo só ocorre no sucesso, [:211](../web/static/js/components/contacts/hooks/useConversationSelection.js#L211), então o efeito re-tenta sozinho quando a lista chegar). Sem esse comentário alguém "conserta por simetria".
4. `[paralelo]` Conferir que o permalink `?message=` continua consumido uma vez — ele está **dentro** do bloco antes gateado ([:187](../web/static/js/components/contacts/hooks/useConversationSelection.js#L187)), então passa a funcionar em mais casos, não menos.

**Pronto quando:** logado como atendente **sem nenhuma conversa atribuída** e com o hub na aba "Minhas" (ou com qualquer filtro que zere a lista **no servidor**), colar `/conversations/<id>` abre o chat. Com `?message=<id>` junto, rola até a mensagem. E um link de conversa em canal **fora da membership** passa a mostrar o card de erro em vez de silêncio.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-07-29)
- **O que foi feito:** no módulo, o guard do ramo da conversa virou `if (loading) return;`. No hook ([useConversationSelection.js:167-217](../web/static/js/components/contacts/hooks/useConversationSelection.js#L167)) o efeito passou a ser consumidor fino: chama `resolveDeepLink(...)` e aplica a ação num `switch`; os setters, o carimbo e o `?message=` continuam no hook. O teste de caracterização da F1 foi invertido para `open_by_id` (comentário de "bug congelado" removido, trocado pela descrição do cenário real).
- **Como foi feito / decisões:** o guard do ramo do CONTATO foi mantido, com o porquê escrito **no módulo** (não no hook) — é lá que alguém tentaria "consertar por simetria". Verificado que `loading` nasce `true` ([useConversationList.js:41](../web/static/js/components/contacts/hooks/useConversationList.js#L41)): o 1º render de um deep-link continua caindo em `wait`, então a alternativa rejeitada na §3.1 ("resolver no mount sem gatear em nada", que faria todo link nascer com telefone nulo) **não** foi introduzida por acidente. O `?message=` segue dentro do bloco antes gateado — passa a funcionar em mais casos, nunca em menos.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --test` nos 19 arquivos de teste puros do repo → **317/317 verde**. `node --input-type=module --check` no hook (a armadilha de crase em template `htm` não se aplica: o hook não tem template).

---

### Fase 3 — Adoção tardia de telefone e canal (🟢, depende de F2)

**Objetivo:** fechar a janela em que a conversa está aberta por id, mas o painel ainda não sabe o telefone nem o canal.

**Contexto:** o loader já adota os dois **da resposta** ([:269](../web/static/js/components/contacts/hooks/useConversationSelection.js#L269), [:272](../web/static/js/components/contacts/hooks/useConversationSelection.js#L272)) — o problema é só o intervalo até ela chegar, em que `selectedChannelId` fica em `'default'` e as chaves de presença/digitação (`typingKey`) ficam desalinhadas. Com a F2, "abrir por id" deixa de ser caso raro.

**Itens**
1. `[sequencial]` Efeito curto: quando `contacts` mudar, se houver linha com `conversation_id === selectedConvId` **e** `selected` ainda for nulo, adotar `row.phone` + `row.channel_id`.
2. `[paralelo]` Verificar que isso **não** pisca "Carregando…": a chave de thread é conversa-primeiro (`conv:<id>`, [:36-39](../web/static/js/components/contacts/hooks/useConversationSelection.js#L36)), então adotar o telefone depois não muda a chave — comportamento já garantido pelo plano 85.
3. `[paralelo]` Conferir que não há corrida com a adoção do loader (os dois escrevem o mesmo valor; o que chegar primeiro vence, o segundo é no-op).

**Pronto quando:** abrindo por link uma conversa que **está** na lista mas fora da 1ª página, o cabeçalho mostra o selo do canal correto sem esperar o fim do carregamento.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-07-29)
- **O que foi feito:** efeito curto novo em [useConversationSelection.js:219-233](../web/static/js/components/contacts/hooks/useConversationSelection.js#L219) — com `selected` nulo e `selectedConvId` setado, procura a linha do atendimento em `contacts` e adota `phone` + `channel_id` dela.
- **Como foi feito / decisões:** escrito no mesmo passe da F2 (mesma árvore de trabalho, blocos separados e comentados) em vez de num commit próprio — a F2 sozinha deixaria a janela do canal `'default'` aberta justamente no caminho que ela promove a comum. Guarda dupla (`if (selected || selectedConvId == null) return;`) para não interferir na seleção normal por clique nem em linha legada sem atendimento. Confirmado que não há corrida com a adoção do loader ([:290](../web/static/js/components/contacts/hooks/useConversationSelection.js#L290)): os dois escrevem o mesmo valor e o segundo vira no-op. Confirmado também que a chave de thread é conversa-primeiro (`conv:<id>`, [threadKeyOf](../web/static/js/components/contacts/hooks/useConversationSelection.js#L37)), então adotar o telefone depois **não** troca a chave — nada pisca "Carregando…" (garantia do plano 85 A2).
- **Problemas / pendências:** o 2º fetch do detalhe (disparado por `selected` mudar) já existia antes desta fase — é o "segundo passe do deep-link" que o comentário do loader descreve; a F3 só o antecipa, não o cria.
- **Verificação:** suíte de módulos puros verde; validação visual do selo do canal fica no roteiro manual da §7 (ver F6).

---

### Fase 4 — Visibilidade no mobile (🟢, depende de F2)

**Objetivo:** impedir que a correção troque um bug silencioso por outro em telas pequenas.

**Contexto verificado:** [Contacts.js:431](../web/static/js/components/contacts/Contacts.js#L431) — `<div class="flex-1 min-w-0 min-h-0 ${!selected ? 'hidden lg:flex' : 'flex'} relative">`. A visibilidade do painel de chat depende de `selected` (**telefone**), e o caminho "abrir por id" começa com telefone nulo. Abaixo do breakpoint `lg`, a conversa **e o card de erro** ficam invisíveis até a resposta chegar.

**Itens**
1. `[sequencial]` Trocar a condição para considerar a thread selecionada por **qualquer** dimensão (telefone **ou** `selectedConvId`) — o `selectedKey`/`threadKeyOf` já existente é a expressão canônica disso ([:36-39](../web/static/js/components/contacts/hooks/useConversationSelection.js#L36)).
2. `[paralelo]` Conferir os outros pontos que decidem layout por `selected` no mesmo arquivo (sidebar oculta em mobile, botão "voltar") para não deixar o operador preso sem saída.
3. `[paralelo]` Verificar o card de erro ([:433](../web/static/js/components/contacts/Contacts.js#L433)): ele já exige `selectedKey` (que é conversa-primeiro), então passa a aparecer corretamente.

**Pronto quando:** com a janela em largura de celular (< `lg`), abrir `/conversations/<id>` com a sidebar vazia mostra o chat (ou o card de erro), não a sidebar vazia.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-07-29)
- **O que foi feito:** em [Contacts.js](../web/static/js/components/contacts/Contacts.js), **duas** condições de layout passaram de `selected` (telefone) para `selectedKey` (`threadKeyOf`, conversa-primeiro): a do painel de chat (§431, era o item do plano) **e a da sidebar** (§347, `selected ? 'hidden lg:flex…'`). Além disso, o card de erro ganhou um botão **"Voltar"** `lg:hidden`.
- **Como foi feito / decisões:** trocar só a linha 431 teria deixado, no mobile, sidebar E chat visíveis ao mesmo tempo (as duas condições precisam concordar) — daí a §347 entrar junto, como manda o item 2 da fase. O botão "Voltar" é o desdobramento do mesmo item: com a sidebar oculta, o card de erro não tinha nenhuma saída no mobile (o "voltar" do cabeçalho vive no `ContactDetail`, que nem renderiza nesse ramo) — o operador ficaria preso. Ele reusa `selectContact(null)`, o mesmo handler do `onBack`, e usa `border-wa-border`/`text-wa-text`/`hover:bg-wa-hover` (classes semânticas, legíveis nos dois temas por construção).
- **Problemas / pendências:** nenhuma. O card de erro (§433) já exigia `selectedKey`, então passou a ser alcançável sem outra mudança.
- **Verificação:** `node --input-type=module --check` no `Contacts.js` (guarda contra a armadilha de crase dentro de `html\`…\`` — os comentários novos foram escritos sem nenhuma crase, de propósito).

---

### Fase 6 — Validação (🔴)

**Itens**
1. `[paralelo]` `node --test` no módulo novo + nos vizinhos do hub (`routing.test.js`, `conversationRows.test.js`, `conversationFilterSpec.test.js`).
2. `[paralelo]` Suíte Python no Postgres de teste (`WHATSBOT_TEST_DB_URL`), incluindo os casos novos da F5.
3. `[sequencial]` Roteiro manual da §7, nos **três** perfis (admin, atendente membro, atendente **não** membro do canal).
4. `[sequencial]` Modo escuro: o card de erro já existente passa a ser mais alcançável — conferir contraste nos dois temas.

**Pronto quando:** checklist da §7 inteiro marcado.

#### Status de execução — Fase 6
**Estado:** 🟨 Parcial (2026-07-29) — automatizada concluída; **roteiro manual pendente com o usuário**
- **O que foi feito:** itens 1 e 2 executados. Item 3 (roteiro manual nos três perfis) e item 4 (contraste do card de erro nos dois temas) **não** foram executados — exigem navegador com sessão logada, fora do alcance desta execução.
- **Como foi feito / decisões:** os testes Python rodaram em **um único processo** (`test_conversation_read_isolation` + `test_conversation_race` + `test_plano69_list_matches_count`), porque o helper de DB derruba e recria o schema uma vez por processo — dois pytest simultâneos no mesmo banco se atropelam.
- **Problemas / pendências:** o roteiro manual da §7 continua **aberto** — em especial o cenário-armadilha (vazio vindo do **servidor**, não de filtro client-side) e o teste em largura < `lg`. Recomendado rodar antes de mergear.
- **Verificação:** `node --test` em todos os `*.test.js` (services + `shell/routing` + `contacts/menuLayout`) → **317 passed, 0 failed**. `venv/bin/python -m pytest` nos três arquivos acima → **19 passed**. `git diff --stat` confirma o contrato da §7: o único `.py` alterado é o de teste; nada em `db/alembic/versions/`, `server/routes/`, `app/services/` ou `db/repositories/`.

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Testar o cenário errado | Sidebar vazia **por filtro client-side** já funciona hoje ⇒ "não reproduz" falso, e a correção é declarada desnecessária | §3.1 explicita a diferença; o roteiro manual usa vazio **do servidor** (aba "Minhas" sem atribuições, ou chip "Resolvidas" numa conta sem resolvidas) |
| Mobile | Abrir por id vira o caminho normal e o painel some abaixo de `lg` | F4 é obrigatória, não opcional |
| Janela de canal `'default'` | Compositor/presença/typing operam no canal errado entre a resolução e a resposta | F3 (adoção tardia) + o loader que já adota da resposta |
| Efeito com histórico de sutilezas | O bloco mexe em `lastResolvedConvId`/`lastResolvedId`/`?message=`; erro reabre em loop ou perde o permalink | F1 extrai a decisão para módulo puro **antes** de mudar comportamento; o caso `noop` está entre os testes |
| Falha de rede no fetch da lista | Hoje também mata o deep-link (o `catch` faz `setLoading(false)` sem tocar em `contacts`, [useConversationList.js:238-241](../web/static/js/components/contacts/hooks/useConversationList.js#L238)) | Com a F2, deixa de matar — o `else` roda. Incluído no roteiro |
| Alguém "endurecer" o backend depois | Achar que ler conversa alheia é falha de privacidade e escopar por `assignee_user_id` quebraria **todo** permalink | F5 congela o contrato com teste e comentário explicando o porquê |
| Confundir com o vazamento do WS | O `/ws` **realmente** entrega conteúdo de canais fora da membership — problema **diferente**, catalogado como achado #1 de [45-registro-bugs-riscos-realtime.md](45-registro-bugs-riscos-realtime.md) | Este plano **não** toca no WS. Ver P3 |

---

## 6. Perguntas em aberto

**P1 — A aba deveria "ceder" quando a conversa aberta não pertence a ela?**
⏸️ **ADIADO**. É a forma **correta** da ideia rejeitada em D5: não como pré-condição para abrir, mas como ajuste cosmético **depois** de abrir (a conversa está na tela, mas a linha dela não aparece na sidebar — a aba poderia ceder para "Todas" para mostrar o contexto). A mesma regra serviria à Fase 5 do [plano 88](88-plano-hub-volta-em-minhas-conversas.md) (conversa nova iniciada pelo operador). Recomendação: decidir junto com o 88, como **uma** regra do hub, não duas.

**P2 — Mostrar algo quando a conversa não existe (id inválido)?**
⏸️ **ADIADO**. Com a F2, `/conversations/999999` passa a fazer a requisição e receber 404 ⇒ card "Não foi possível abrir esta conversa". Hoje, com a lista cheia, o comportamento já é esse. Nada a fazer — registrado só para o executor não se assustar ao ver o card num id inventado.

**P3 — O WebSocket entrega conteúdo de canais fora da membership.**
⏸️ **FORA DESTE PLANO** → virou o [plano 90](90-plano-escopo-do-websocket-por-canal.md). Catalogado desde 2026-07-09 como achado #1 🔴 CONFIRMED em [45-registro-bugs-riscos-realtime.md](45-registro-bugs-riscos-realtime.md) e como a limitação **L1** em [44-avaliacao-realtime-websocket-vs-chatwoot.md](44-avaliacao-realtime-websocket-vs-chatwoot.md). Medido nesta instância: afeta RedesBrasil_bot (Mábia), Avisos Curseduca (7 de 13) e Site (12 de 13). **Não** confundir com este plano: aqui o problema é o painel mostrar **de menos**; lá, o servidor manda **demais**.

---

## 7. Checklist de verificação

Módulos puros:
- [x] `node --test web/static/js/services/deepLinkResolve.test.js` verde (7 casos)
- [x] `node --test` verde em `routing.test.js`, `conversationRows.test.js`, `conversationFilterSpec.test.js` — na verdade nos **19** arquivos de teste puro do repo (317 checagens)

Backend:
- [x] Suíte no Postgres de teste verde (`WHATSBOT_TEST_DB_URL`, banco com `test` no nome) — 19 passed nos arquivos de conversa/RBAC
- [x] Os casos novos da F5 passam **sem** alterar código de produção
- [x] `git diff --stat` não toca `db/alembic/versions/`, `server/routes/`, `app/services/` nem `db/repositories/` — o único `.py` é o arquivo de teste

Roteiro manual — **vazio vindo do servidor** (o cenário certo) — ⏳ **pendente (F6 item 3)**:
- [ ] Atendente sem conversa atribuída, aba "Minhas": `/conversations/<id>` de conversa alheia **abre**
- [ ] O mesmo com `?message=<msg_id>`: rola e destaca a mensagem
- [ ] Chip "Resolvidas" numa view sem resolvidas: o link abre
- [ ] Com a rede da lista falhando (DevTools → offline no fetch da lista): o link abre

Roteiro manual — controle e bordas — ⏳ **pendente**:
- [ ] Sidebar vazia por filtro **client-side**: continua abrindo (comportamento de hoje, não pode regredir)
- [ ] Conversa **na** lista: abre pela linha, com telefone e canal corretos de imediato
- [ ] Conversa em canal **fora da membership** (ex.: um atendente sem o canal + link daquele canal): card "Não foi possível abrir esta conversa", **não** silêncio
- [ ] `/conversations/999999` (id inexistente): card de erro
- [ ] Reabrir o mesmo link duas vezes seguidas não entra em loop

Mobile e tema — ⏳ **pendente**:
- [ ] Largura < `lg`: link com sidebar vazia mostra o chat (e o card de erro quando for o caso)
- [ ] Card de erro legível no modo escuro — inclusive o botão "Voltar" novo (`lg:hidden`, classes `wa-*`)

---

## 8. Apêndice — arquivos-chave

**Novos**
- `web/static/js/services/deepLinkResolve.js` — decisão pura do deep-link
- `web/static/js/services/deepLinkResolve.test.js` — `node --test`
- casos novos em `tests/test_conversation_read_isolation.py` (ou arquivo irmão) — contrato do servidor

**Alterados**
- [web/static/js/components/contacts/hooks/useConversationSelection.js](../web/static/js/components/contacts/hooks/useConversationSelection.js) — §169-214 (consome o módulo), efeito novo de adoção tardia
- [web/static/js/components/contacts/Contacts.js](../web/static/js/components/contacts/Contacts.js) — §431 (visibilidade do painel no mobile)

**Lidos, não alterados** (contexto obrigatório)
- [web/static/js/components/shell/routing.js](../web/static/js/components/shell/routing.js) — `/conversations/<id>` → tab + id
- [web/static/js/components/shell/App.js](../web/static/js/components/shell/App.js) — §163-172 e §198-210: por que sair pelo menu descarta o deep-link
- [web/static/js/components/contacts/hooks/useConversationList.js](../web/static/js/components/contacts/hooks/useConversationList.js) — §226-241: como a lista termina vazia com `loading=false`
- [server/routes/conversations.py](../server/routes/conversations.py) · [server/authz.py](../server/authz.py) · [db/repositories/conversation_repo.py](../db/repositories/conversation_repo.py) — a autorização real (sem noção de dono)
- [tests/test_conversation_read_isolation.py](../tests/test_conversation_read_isolation.py) — precedente do teste de isolamento

**Planos relacionados**
- [88 — hub volta em "Minhas"](88-plano-hub-volta-em-minhas-conversas.md) — **depende deste**; perde a antiga Fase 4
- 85 — painel preso na conversa anterior (plano concluído e removido em 2026-07-29; ver commits `3e60769`/`87706d5`/`4d7b500`) — origem do carimbo de thread e do card de erro que este plano reutiliza
- [45 — registro de bugs realtime](45-registro-bugs-riscos-realtime.md) · [44 — avaliação realtime](44-avaliacao-realtime-websocket-vs-chatwoot.md) — o vazamento do WS (P3), problema distinto
