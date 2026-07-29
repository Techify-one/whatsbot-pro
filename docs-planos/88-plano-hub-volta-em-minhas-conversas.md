# Plano 88 — O hub de conversas passa a abrir sempre em "Minhas" (e para de cair em "Todas" ao voltar de outra tela)

> **Status:** ✅ EXECUTADO (F1–F4 · 2026-07-29; F5 com o roteiro manual pendente) · **Data:** 2026-07-28 · **Escopo:** pequeno/médio (só frontend)
> **Origem:** Relato do usuário — "quando algum usuário entra em alguma outra tela e volta, sempre está voltando para a guia de todas as conversas; isso está atrapalhando os atendentes". **Método:** leitura do código real do shell de rotas + do hub de conversas (`arquivo:linha`) + `grep` nos consumidores da aba de atribuição; nenhuma afirmação abaixo veio de memória.
> A causa não é "o filtro se perde": é que **trocar de aba do app desmonta o hub inteiro** e a rota volta como `/` **sem query-string**, então toda a view renasce nos defaults — e o default da aba de atribuição é `all` ("Todas"), em dois lugares. A correção troca esse default para `mine` ("Minhas"), com degradação segura para `all` quando não há usuário autenticado.
>
> ⛔ **PRÉ-REQUISITO: o [plano 89](89-plano-link-de-conversa-sempre-abre.md) precisa estar mergeado antes deste.** A auditoria de 2026-07-28 (8 agentes, 3 adversariais) mostrou que o deep-link `/conversations/<id>` já é quebrado hoje quando a sidebar chega vazia **do servidor** — bug pré-existente desde `288d686`, não regressão deste plano. Com "Minhas", lista vazia deixa de ser exceção e o bug sairia da raridade. Por isso ele virou plano próprio, executado **antes**; a antiga Fase 4 daqui migrou inteira para lá.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | ✅ (2026-07-28) **"Sempre Minhas, fixo"** — escolhido pelo usuário entre as três opções apresentadas (fixo × lembrar a última aba × lembrar a view inteira) | O default do hub passa a ser `mine` para **todo mundo, toda vez**. **Não** há memória por usuário (`localStorage`), **não** há preferência por cargo, **não** há chave de config. Zero backend, zero migration |
| D2 | ✅ (2026-07-28) Sem identidade de usuário ⇒ default degrada para `all` | Obrigatório, não é "defensivo": (a) a aba "Minhas" **nem é renderizada** sem identidade ([ConversationFilterBar.js:478](../web/static/js/components/contacts/ConversationFilterBar.js#L478)) — o operador ficaria numa aba invisível; (b) o servidor **recusa** `assignee=me` sem sessão ([translate.py:220-222](../db/filters/translate.py#L220)), e a sidebar responderia lista vazia em silêncio ([useConversationList.js:232](../web/static/js/components/contacts/hooks/useConversationList.js#L232)) |
| D3 | ✅ (2026-07-28) A **URL continua vencendo** o default | Precedência já existente (Plano 24 · D3, [Contacts.js:203](../web/static/js/components/contacts/Contacts.js#L203)): um link com `?assignment=all` abre em Todas. O default só vale para a URL **limpa** (`/`) |
| D4 | ✅ (2026-07-28) O escopo é **só a aba de atribuição** | Status (Abertas/Resolvidas), etiquetas, ordenação e filtro avançado **também** se perdem na navegação hoje — e continuam se perdendo. Isso é a opção "lembrar a view inteira", explicitamente **não** escolhida. Vira P2 (adiado) |
| D5 | ✅ (2026-07-28, **revista** após a auditoria) A troca de default **não pode** quebrar abrir uma conversa por link — e isso virou o [plano 89](89-plano-link-de-conversa-sempre-abre.md), executado ANTES deste | O guard de deep-link ([useConversationSelection.js:172](../web/static/js/components/contacts/hooks/useConversationSelection.js#L172)) lê a lista **crua** `contacts`, não a sidebar renderizada — o bug exige vazio vindo do **servidor** (é o caso de `assignee=me`). Não é regressão deste plano: já existe desde `288d686`. A antiga Fase 4 saiu daqui (§4) |
| D6 | ✅ (2026-07-28) **A autorização do servidor não tem noção de dono** — verificado | `get_with_channel` filtra só por id ([conversation_repo.py:592-601](../db/repositories/conversation_repo.py#L592)); o único escopo é membership de inbox ([authz.py:77-90](../server/authz.py#L77)). A aba "Minhas" é 100% cosmética do cliente: nada nela restringe acesso, nem deve dar essa impressão em nenhum texto do plano |

---

## 1. Resumo executivo

O hub de conversas (`<Contacts/>`) guarda a aba de atribuição em estado de componente, espelhado na query-string. Trocar de tela no app é `history.pushState(path)` ([App.js:167](../web/static/js/components/shell/App.js#L167)) e o `<ScreenRouter/>` **troca o componente renderizado** ([ScreenRouter.js:129](../web/static/js/components/shell/ScreenRouter.js#L129)) — o hub desmonta e todo o estado morre. Voltar é outro `pushState('/')`, agora **sem query**: o hub remonta, hidrata da URL vazia e cai nos defaults. O default da aba é `'all'`, declarado em **dois** lugares que precisam concordar: o seed do `useState` ([useConversationFilters.js:46](../web/static/js/components/contacts/hooks/useConversationFilters.js#L46)) e — o que de fato manda — o default do schema de URL ([Contacts.js:30](../web/static/js/components/contacts/Contacts.js#L30)), porque a hidratação do mount sobrescreve o seed.

A correção é trocar esse default para `'mine'`, resolvido **uma vez no mount** a partir de "existe usuário logado?" (D2). Como o default do schema é também a regra de **omissão** na serialização, a URL inverte de forma: `/` passa a significar "Minhas" e escolher Todas escreve `?assignment=all` — o deep-link continua exato nos dois casos.

Com "Minhas", a lista vazia deixa de ser exceção — e isso expõe dois problemas que já existem: o deep-link `/conversations/<id>` desiste quando a lista vem vazia do servidor (**virou o [plano 89](89-plano-link-de-conversa-sempre-abre.md)**, pré-requisito deste), e uma conversa que o operador acabou de iniciar não é dele e some da sidebar (Fase 4 daqui).

---

## 2. Como funciona hoje (mapa)

### 2.1 Por que o estado se perde ao trocar de tela

| Etapa | Local | O que acontece |
|-------|-------|----------------|
| 1. Operador clica em "Protocolos" no menu da engrenagem | [App.js:163-171](../web/static/js/components/shell/App.js#L163) `setTab` | `history.pushState(null, '', '/protocolos')` — a query-string do hub (`?assignment=mine&status=…`) **é descartada aqui**, não há preservação |
| 2. Render | [ScreenRouter.js:129-134](../web/static/js/components/shell/ScreenRouter.js#L129) | `tab === 'contacts'` deixa de valer → `<Contacts/>` **desmonta**. Todo o estado dos hooks (`useConversationFilters`, `useConversationList`, seleção) é destruído |
| 3. Operador volta (botão "voltar" do `PageHeader` → `setTab('contacts')`) | [App.js:167](../web/static/js/components/shell/App.js#L167) | `pushState(null, '', '/')` — **URL limpa** |
| 4. Remonta | [Contacts.js:230-259](../web/static/js/components/contacts/Contacts.js#L230) `useUrlState` | `apply(readParams(location.search, HUB_URL_SCHEMA))` → `search` vazia ⇒ **todo campo cai no default do schema** ⇒ `setAssignmentTab('all')` ([Contacts.js:238](../web/static/js/components/contacts/Contacts.js#L238)) |

⚠️ **A hidratação do mount vence o seed do `useState`.** `useUrlState` hidrata sempre no mount ([useUrlState.js:29-34](../web/static/js/hooks/useUrlState.js#L29)) e a escrita para a URL **pula a primeira execução** ([useUrlState.js:40](../web/static/js/hooks/useUrlState.js#L40)). Mudar só o `useState('all')` do hook de filtros **não teria efeito nenhum** — o schema mandaria de volta para `'all'` no mesmo mount.

### 2.2 Onde o default `'all'` está declarado

| # | Local | Papel | Efeito de mudar |
|---|-------|-------|-----------------|
| 1 | [Contacts.js:30](../web/static/js/components/contacts/Contacts.js#L30) `enumStr('assignment', 'all')` | Default de **leitura** (URL sem o param) **e** regra de **omissão** na escrita ([urlState.js:50-52](../web/static/js/services/urlState.js#L50)) | É o que decide o comportamento. Também inverte a URL: `mine` some da query, `all` passa a aparecer |
| 2 | [useConversationFilters.js:46](../web/static/js/components/contacts/hooks/useConversationFilters.js#L46) `useState('all')` | Seed do primeiro render, **antes** da hidratação | Sozinho não muda nada (§2.1), mas precisa concordar: senão o primeiro frame renderiza "Todas" e pisca para "Minhas" — e, pior, dispara um fetch da lista com `assignmentTab='all'` que depois é refeito |

### 2.3 Quem consome a aba

| Consumidor | Local | Comportamento com `mine` |
|---|---|---|
| Params da lista server-side | [conversationFilterSpec.js:146-152](../web/static/js/services/conversationFilterSpec.js#L146) `assignmentParams` | `{ assignee: 'me' }` — o **servidor** resolve "me" pela sessão ([translate.py:220](../db/filters/translate.py#L220)), não depende do `currentUserId` do cliente |
| Filtro client-side (fallback) | [useConversationFilters.js:205-214](../web/static/js/components/contacts/hooks/useConversationFilters.js#L205) `matchesAssignment(c, tab, currentUserId)` | **Depende** do `currentUserId`, que chega **assíncrono** via `getMe()` ([useConversationActions.js:264](../web/static/js/components/contacts/hooks/useConversationActions.js#L264)) |
| Renderização da aba | [ConversationFilterBar.js:478](../web/static/js/components/contacts/ConversationFilterBar.js#L478) | `hasIdentity ? tabBtn('mine', …) : null` — `hasIdentity = currentUserId != null` ([Contacts.js:382](../web/static/js/components/contacts/Contacts.js#L382)) |
| Insert/drop de linha via WS | [conversationRows.js:261-270](../web/static/js/services/conversationRows.js#L261) `rowMatchesView` | Linha de conversa **não atribuída ao operador não entra** na sidebar em tempo real — semântica correta da aba, mas é mudança de rotina para quem hoje fica em "Todas" |
| Refetch ao trocar de filtro | [useConversationFilters.js:173-178](../web/static/js/components/contacts/hooks/useConversationFilters.js#L173) | Já cobre `assignmentTab` nas deps — nada a fazer |
| Contador do rodapé | [ContactList.js:792](../web/static/js/components/contacts/ContactList.js#L792) | `tabCounts[assignmentTab]` — já genérico |
| Badge global de não lidas | [useConversationList.js:80](../web/static/js/components/contacts/hooks/useConversationList.js#L80) → `GET /api/contacts/unread-count` ([api.js:209](../web/static/js/services/api.js#L209)) | **Independente da aba** (server-side, global) — o operador continua vendo que chegou mensagem, mesmo fora de "Minhas". ✅ Não é risco |

### 2.4 Como a identidade é conhecida **no mount** (sem esperar rede)

`currentUserId` só existe depois do `getMe()` (§2.3), mas o app **já guarda o usuário logado no `localStorage`** sob a chave `whatsbot_user`, escrita pelo `AuthGate` ([AuthGate.js:41](../web/static/js/components/shell/AuthGate.js#L41), [:66](../web/static/js/components/shell/AuthGate.js#L66)) e removida no logout ([AuthGate.js:101](../web/static/js/components/shell/AuthGate.js#L101), [:118](../web/static/js/components/shell/AuthGate.js#L118)). O módulo de rascunhos já usa exatamente esse truque para namespacear **antes** do shell montar ([drafts.js:19](../web/static/js/services/drafts.js#L19)). É a fonte **síncrona** de "há identidade?" para resolver o default no mount.

---

## 3. Inventário das mudanças

| # | Onde | O que fazer | Risco | Esforço |
|---|------|-------------|-------|---------|
| M1 | novo `web/static/js/services/hubDefaults.js` | Módulo **puro**: `defaultAssignmentTab(hasIdentity)` → `'mine' \| 'all'` + `buildHubUrlSchema(defaultAssignment)` (o `HUB_URL_SCHEMA` de hoje virando fábrica) + `hubUrlHasParams(search)` (movido de [Contacts.js:39-42](../web/static/js/components/contacts/Contacts.js#L39)). Sem preact/DOM/rede ⇒ `node --test` | baixo | S |
| M2 | novo `web/static/js/services/hubDefaults.test.js` | Testes puros: default com/sem identidade; `readParams('')` → `mine`; `readParams('?assignment=all')` → `all` (URL vence, D3); `writeParams({assignment:'mine'})` **omite** e `'all'` **escreve**; sem identidade o schema volta a ter default `all` | baixo | S |
| M3 | [Contacts.js:29-42](../web/static/js/components/contacts/Contacts.js#L29) | Trocar a constante `HUB_URL_SCHEMA` por `useMemo(() => buildHubUrlSchema(defaultAssignmentTab(hasStoredUser())), [])` — resolvido **uma vez no mount** (a identidade não muda durante o mount). Usar esse schema nos dois lados do `useUrlState` ([:232](../web/static/js/components/contacts/Contacts.js#L232) e [:248](../web/static/js/components/contacts/Contacts.js#L248)) | médio | S |
| M4 | [useConversationFilters.js:43-46](../web/static/js/components/contacts/hooks/useConversationFilters.js#L43) | Nova opção `defaultAssignmentTab = 'all'` (default preserva callers antigos) usada como seed do `useState`. `Contacts.js` passa o mesmo valor de M3 ⇒ primeiro frame já correto, sem piscada nem fetch descartado | baixo | S |
| ~~M5~~ | ~~deep-link~~ | **MOVIDO para o [plano 89](89-plano-link-de-conversa-sempre-abre.md)** — é bug pré-existente, não regressão daqui, e ganhou 3 itens que esta análise não tinha visto (adoção tardia de telefone/canal, visibilidade no mobile, módulo puro testável) | — | — |
| M6 | [useChannelPicker.js:116-119](../web/static/js/components/contacts/hooks/useChannelPicker.js#L116) `handleNewConversationSent` / `openInChannel` | Conversa iniciada pelo operador nasce **sem** `assignee_user_id` ⇒ não pertence a "Minhas" e some da sidebar (o chat abre, mas a linha não aparece). Mitigação client-side: ao abrir uma conversa que não casa a view corrente, cair para a aba `all`. Ver P1 para a alternativa server-side | médio | M |

### 3.1 Falsos positivos descartados

| Suspeita | Por que **não** é problema |
|---|---|
| "Os filtros salvos (presets) vão brigar com o novo default" | A aba **é excluída de propósito** do spec de preset — [useConversationFilters.js:239-244](../web/static/js/components/contacts/hooks/useConversationFilters.js#L239) ("assignmentTab is intentionally excluded: switching tabs is a view change, not a filter"). Nada a mudar em `normalizeSpec`/`isDefaultSpec`/`clearAllFilters` |
| "`clearAllFilters` (o X de limpar) vai jogar o operador em Todas" | [useConversationFilters.js:325-332](../web/static/js/components/contacts/hooks/useConversationFilters.js#L325) não toca `assignmentTab` — pelo mesmo motivo acima |
| "`web/static/js/services/urlState.test.js` vai quebrar" | O teste declara um **schema próprio local** ([urlState.test.js:16](../web/static/js/services/urlState.test.js#L16)), não importa o do hub. Idem `conversationFilterSpec.test.js`, que passa `assignmentTab` explícito em cada caso. **Nenhum teste existente muda** |
| "A suíte Python (`tests/`) precisa mudar" | A mudança é 100% de default de UI. Backend, rotas, tradutor de filtros e migrations ficam byte-idênticos |
| "Precisa persistir a preferência (localStorage/config)" | D1 fechou: fixo, sem memória. O `localStorage` é lido **só** para saber se há usuário (§2.4), nunca para guardar a aba |
| "O operador vai deixar de saber que chegou mensagem nova de conversa não atribuída" | O badge global vem de `/api/contacts/unread-count`, independente da aba (§2.3), e a aba "Não atribuídas" mantém a contagem ao vivo ao lado |
| "`?assignment=mine` nos links já existentes vai parar de funcionar" | `readParams` continua lendo o param quando presente; só o **default da ausência** muda. Link antigo com `mine` segue exato |
| "A aba 'Minhas' restringe o que o operador pode ACESSAR" | **Não.** É filtro de exibição. O servidor não escopa por dono (D6) — o operador continua podendo abrir qualquer conversa por link, busca ou tela Contatos. O único escopo real é membership de canal |
| "Com 'Minhas' o operador perde as conversas não atribuídas que chegarem" | Elas não entram na sidebar em tempo real (`rowMatchesView`), mas o **badge global** de não lidas é server-side e independente da aba, e a aba "Não atribuídas" mantém contagem ao vivo ao lado. Aceito por D1 |

---

## 4. Fases / Roadmap

```
PRÉ-REQUISITO   ═══ plano 89 mergeado e verificado ═══     ⛔ portão, não fase
                            │
WAVE 0   F1 (módulo puro + testes)                         🔴 base de tudo
              │
              ├─────────────┬──────────────────────────────┐
WAVE 1   F2 (Contacts.js)  F3 (hook de filtros)            🟢 paralelas (arquivos distintos)
              └──── barreira: comportamento novo em pé ────┘
                            │
WAVE 2   F4 (conversa nova iniciada pelo operador)         🔴 sozinha
                            │
WAVE 3   F5 (validação manual + suítes)                    🔴 sozinha, fecha o plano
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|------|------|------------|-------|-------|----------------|
| — | ⛔ | **Plano 89 mergeado** (deep-link imune ao estado da lista) | 🔴 portão | — | Checklist do 89 marcado |
| 0 | F1 | `services/hubDefaults.js` + teste | 🔴 [bloqueia: F2, F3] | baixo | `node --test` verde no módulo novo |
| 1 | F2 | `Contacts.js` — schema no mount | 🟢 [depende de: F1] | médio | URL limpa abre em "Minhas" |
| 1 | F3 | `useConversationFilters.js` — seed | 🟢 [depende de: F1] | baixo | Sem piscada "Todas → Minhas" no 1º frame |
| 2 | F4 | `useChannelPicker.js` — conversa nova (era F5) | 🔴 [depende de: F2+F3] | médio | Conversa iniciada pelo operador aparece na sidebar |
| 3 | F5 | Validação (era F6) | 🔴 | baixo | Checklist da §7 todo marcado |

⚠️ **Renumeração (2026-07-28):** a antiga F4 (deep-link) virou o plano 89; as antigas F5 e F6 passaram a F4 e F5. Nenhuma fase havia sido executada, então nada de progresso se perdeu.

Disciplina do repo a respeitar: **verde a cada fase**; **um refactor por commit** (F1 é um commit próprio; F2+F3 podem ir juntas por serem uma única mudança de comportamento; F4 é commit separado).

---

### Fase 1 — Módulo puro `hubDefaults` (🔴 base)

**Objetivo:** tirar de dentro do componente a regra "qual é o default da aba" e "qual é o schema de URL do hub", para virar testável por `node --test`.

**Itens**
1. `[sequencial]` Criar `web/static/js/services/hubDefaults.js` com:
   - `hasStoredUser()` — lê `whatsbot_user` do `localStorage` (mesmo padrão de [drafts.js:19](../web/static/js/services/drafts.js#L19)), dentro de `try/catch`, retorna boolean. **Aceita o storage indisponível** (modo privado) devolvendo `false` (degrada para `all`, D2).
   - `defaultAssignmentTab(hasIdentity)` → `hasIdentity ? 'mine' : 'all'`.
   - `buildHubUrlSchema(defaultAssignment = 'all')` — o array de hoje ([Contacts.js:29-37](../web/static/js/components/contacts/Contacts.js#L29)) virando retorno de função, com `enumStr('assignment', defaultAssignment)`. **Nenhum outro campo muda.**
   - `hubUrlHasParams(search)` — movido literal de [Contacts.js:39-42](../web/static/js/components/contacts/Contacts.js#L39) (as chaves passam a sair do schema construído).
2. `[paralelo]` Criar `web/static/js/services/hubDefaults.test.js` cobrindo M2 da §3.
3. `[sequencial]` Cabeçalho de comentário no arquivo novo no estilo do repo (o "porquê", não o "o quê"): por que o default é resolvido **uma vez no mount**, e por que sem identidade ele **tem** que degradar (as duas razões de D2, com os `arquivo:linha`).

**Pronto quando:** `node --test web/static/js/services/hubDefaults.test.js` verde e nenhum outro arquivo importa o módulo ainda (a fase é puramente aditiva — o app roda igualzinho).

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-07-29)
- **O que foi feito:** novo [web/static/js/services/hubDefaults.js](../web/static/js/services/hubDefaults.js) com `hasStoredUser()`, `defaultAssignmentTab(hasIdentity)`, `buildHubUrlSchema(defaultAssignment)`, `hubUrlHasParams(search, schema?)` e — antecipando a F4 — `shouldYieldAssignmentTab({...})`. Novo [hubDefaults.test.js](../web/static/js/services/hubDefaults.test.js) (19 casos). Fase puramente aditiva: nenhum outro arquivo importa o módulo ainda.
- **Como foi feito / decisões:** (a) a regra "a aba cede" (F4) nasceu **junto** neste módulo em vez de virar um helper novo depois — é a outra metade da mesma pergunta ("qual aba o operador vê") e assim a F4 já entra testada, com um só lugar para a política; (b) `hubUrlHasParams` recebe o schema como parâmetro **opcional** (as chaves não dependem do default), então o call site não precisa ordenar a construção do schema antes dele; (c) `shouldYieldAssignmentTab` exclui `mentions` de propósito — abrir a menção zera `has_user_mention` na linha ([useConversationSelection.js:266](../web/static/js/components/contacts/hooks/useConversationSelection.js#L266)), e julgar ali faria a aba ceder a cada menção lida; (d) sem evidência nenhuma (conversa nova, sem linha e sem detalhe) só `mine` cede — uma conversa que nasce sem responsável nunca é "minha", mas é legitimamente "não atribuída".
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --test web/static/js/services/hubDefaults.test.js` → **19/19 verde**.

---

### Fase 2 — `Contacts.js` passa a usar o schema com default resolvido (🟢, depende de F1)

**Objetivo:** fazer a URL limpa significar "Minhas".

**Itens**
1. `[sequencial]` Remover a constante de módulo `HUB_URL_SCHEMA` e o `hubUrlHasParams` local ([Contacts.js:29-42](../web/static/js/components/contacts/Contacts.js#L29)); importar de `services/hubDefaults.js`.
2. `[sequencial]` Dentro do componente, **antes** do `useConversationFilters`: `const defaultTab = useMemo(() => defaultAssignmentTab(hasStoredUser()), [])` e `const hubSchema = useMemo(() => buildHubUrlSchema(defaultTab), [defaultTab])`.
   - ⚠️ Deps vazias de propósito: resolver a identidade **uma vez** por mount. Um schema que mudasse no meio da vida do componente reescreveria a URL sem o operador ter tocado em nada.
3. `[sequencial]` Usar `hubSchema` nos dois lados do `useUrlState` ([Contacts.js:232](../web/static/js/components/contacts/Contacts.js#L232) `readParams` e [:248](../web/static/js/components/contacts/Contacts.js#L248) `writeParams`) e no `hasUrlFilters` ([:203](../web/static/js/components/contacts/Contacts.js#L203)).
4. `[sequencial]` Conferir que a precedência URL > preset salvo continua intacta ([:209](../web/static/js/components/contacts/Contacts.js#L209) `skipStoredPreset`) — `hubUrlHasParams` só mudou de arquivo.

**Pronto quando:** abrir `/` (URL sem query) cai na aba **Minhas**; clicar em "Todas" escreve `?assignment=all` na barra de endereços; recarregar nesse link volta em Todas (D3); abrir `?assignment=mine` continua exato.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-07-29)
- **O que foi feito:** [Contacts.js](../web/static/js/components/contacts/Contacts.js) — a constante de módulo `HUB_URL_SCHEMA` e o `hubUrlHasParams` local saíram; entraram `defaultTab = useMemo(() => defaultAssignmentTab(hasStoredUser()), [])` e `hubSchema = useMemo(() => buildHubUrlSchema(defaultTab), [defaultTab])`, usados nos dois lados do `useUrlState` (`readParams`/`writeParams`) e no `hasUrlFilters`. O import de `urlState.js` encolheu para `readParams`/`writeParams` (os codecs agora são consumidos dentro do módulo novo).
- **Como foi feito / decisões:** deps vazias no `defaultTab` conforme o plano — o hub desmonta a cada troca de tela, então "uma vez por mount" já é "toda vez que o operador volta". `hasUrlFilters` passou a depender de `hubSchema` (mesma identidade estável, o memo não recalcula), então a precedência URL > preset salvo continua avaliada uma vez com a `location.search` da montagem.
- **Problemas / pendências:** consequência conhecida e aceita: `selectContact` faz `pushState('/conversations/<id>')` **sem** a query, então a aba escolhida à mão não sobrevive a um F5 feito com a conversa aberta — o comportamento é o mesmo de antes, só com o default invertido (é a P2/D4, não regressão).
- **Verificação:** `node --input-type=module --check` no arquivo; `node --test` nos 5 módulos puros do hub → **126/126 verde**. Validação manual pendente na F5.

---

### Fase 3 — Seed do hook de filtros concorda com o schema (🟢, depende de F1)

**Objetivo:** o primeiro frame já nascer em "Minhas" — sem piscar "Todas" e sem disparar um fetch de lista que será refeito.

**Itens**
1. `[sequencial]` [useConversationFilters.js:43](../web/static/js/components/contacts/hooks/useConversationFilters.js#L43): nova opção `defaultAssignmentTab = 'all'` na desestruturação (default mantém qualquer caller antigo byte-idêntico).
2. `[sequencial]` [useConversationFilters.js:46](../web/static/js/components/contacts/hooks/useConversationFilters.js#L46): `useState(defaultAssignmentTab)` no lugar de `useState('all')`, com comentário curto apontando que a fonte da verdade é o schema de URL (§2.1) e que este seed existe só para o 1º frame.
3. `[sequencial]` [Contacts.js:205-218](../web/static/js/components/contacts/Contacts.js#L205): passar `defaultAssignmentTab: defaultTab` (o mesmo valor da F2 — **um só cálculo**, nunca dois `hasStoredUser()` independentes).

**Pronto quando:** com o DevTools na aba Network, voltar de outra tela dispara **um** request de lista, já com `assignee=me` (nenhum request com a query de "Todas" antes).

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-07-29)
- **O que foi feito:** [useConversationFilters.js](../web/static/js/components/contacts/hooks/useConversationFilters.js) ganhou a opção `defaultAssignmentTab = 'all'` (default preserva qualquer caller antigo byte-idêntico) e o `useState('all')` da aba virou `useState(defaultAssignmentTab)`, com comentário apontando que a fonte da verdade é o schema de URL e que este seed existe só para o 1º frame. `Contacts.js` passa `defaultAssignmentTab: defaultTab` — o **mesmo** valor da F2, um só `hasStoredUser()`.
- **Como foi feito / decisões:** commitada junto com a F2 (é uma única mudança de comportamento, como o plano previu). O comentário da linha do `useState` também corrigiu a enumeração da aba (`all|mine|unassigned|mentions` — `mentions` existe desde o plano 72 e faltava ali).
- **Problemas / pendências:** nenhuma. `Contacts.js` é o único caller do hook no repo (verificado por `grep`), então o default `'all'` da opção é só um contrato de compatibilidade.
- **Verificação:** mesmas suítes da F2 (**126/126 verde**). A prova do "um único request de lista" fica na F5 (DevTools).

---

### ~~Fase 4 — Deep-link~~ → **migrou para o [plano 89](89-plano-link-de-conversa-sempre-abre.md)**

A auditoria de 2026-07-28 mostrou que (a) o bug é **pré-existente** (`288d686`, 2026-06-20 — o guard foi copiado do deep-link de contato no mesmo commit que criou o ramo `else` que o torna desnecessário), (b) a condição real é a lista **crua** `contacts` vazia **vinda do servidor**, não "sidebar vazia", e (c) faltavam três itens: adoção tardia de telefone/canal, **visibilidade no mobile** ([Contacts.js:431](../web/static/js/components/contacts/Contacts.js#L431) gateia o painel por `selected`, que é nulo no caminho "abrir por id") e um módulo puro testável (o caminho não tem **nenhuma** cobertura hoje).

Por isso virou plano próprio, executado **antes** deste (D1 do 89). Este plano passa a ter o 89 como **portão**, não como fase.

---

### Fase 4 — Conversa iniciada pelo operador não pode sumir da sidebar (🔴, depende de F2+F3)

**Objetivo:** fechar a segunda consequência do novo default.

**Contexto verificado:** `handleNewConversationSent` ([useChannelPicker.js:116-119](../web/static/js/components/contacts/hooks/useChannelPicker.js#L116)) só abre a conversa. A linha nasce **sem** `assignee_user_id` (não há atribuição ao criador no fluxo de criação manual — o único carimbo automático é o `default_assignee_user_id` **por canal** do inbound, [ai_settings.py:48](../channels/ai_settings.py#L48)). Em "Minhas", `rowMatchesView` ([conversationRows.js:268](../web/static/js/services/conversationRows.js#L268)) rejeita a linha e o servidor também não a devolve — o chat abre, mas a sidebar fica sem ela.

**Itens**
1. `[sequencial]` No hub, após abrir uma conversa recém-criada, detectar "a linha aberta não pertence à aba corrente" e cair para `all` (a aba é uma **view**, não um filtro salvo — trocá-la não descarta nada do operador). Reusar o `viewSpecRef` já mantido fresco em render ([Contacts.js:87-92](../web/static/js/components/contacts/Contacts.js#L87) / [useConversationFilters.js:88-92](../web/static/js/components/contacts/hooks/useConversationFilters.js#L88)) em vez de recalcular a view.
2. `[paralelo]` Verificar o mesmo caminho para o `openInChannel` disparado pelo picker de canal (mesma função, dois pontos de entrada).
3. `[sequencial]` **Não** implementar atribuição automática ao criador aqui — é mudança de backend e de semântica de negócio; fica em **P1**.
4. `[sequencial]` Generalizar a regra em vez de amarrá-la ao fluxo "nova conversa": **"conversa aberta que não casa a aba faz a aba ceder"** vale igualmente para o deep-link (P1 do [plano 89](89-plano-link-de-conversa-sempre-abre.md)). Uma regra, dois consumidores — não duas mitigações parecidas em arquivos diferentes.

**Pronto quando:** com o hub em "Minhas", "Nova conversa" → escolher canal → enviar: a conversa aparece na sidebar (a aba pula para "Todas") e continua aberta no chat. E abrir por link uma conversa que não é do operador produz o mesmo efeito.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-07-29)
- **O que foi feito:** a regra `shouldYieldAssignmentTab` (pura, em [hubDefaults.js](../web/static/js/services/hubDefaults.js), 9 casos de teste) + um efeito em [Contacts.js](../web/static/js/components/contacts/Contacts.js) que a aplica **uma vez por thread aberta**, quando o detalhe assenta (`!loadingDetail && !detailStale`), com carimbo em `yieldDecidedRef`. Cedendo, `setAssignmentTab('all')` — o efeito de filtros do hook já refaz a lista.
- **Como foi feito / decisões (desvios do plano, deliberados):**
  1. **Onde**: o plano sugeria o call site do `useChannelPicker`; ficou no **container**, disparado pela conversa ABERTA. Assim a regra é uma só e cobre os dois consumidores de graça (conversa nova **e** deep-link — o item 4 da fase e a P1 do plano 89) sem duplicar mitigação, e `useChannelPicker.js` **não foi tocado** (os dois pontos de entrada dele passam por `openInChannel` → `selectContact`, que o efeito já observa).
  2. **Evidência, não ausência**: em vez de "a linha não está na lista ⇒ cede", julga-se a conversa **carregada** (`contactData.conversation`, que traz `assignee_user_id`/`active_agent_key`), com a linha da sidebar como reserva. Sem isso, uma conversa minha fora das páginas carregadas trocaria a aba à toa.
  3. **`mentions` não cede**: abrir a menção zera `has_user_mention` na própria linha ([useConversationSelection.js:266](../web/static/js/components/contacts/hooks/useConversationSelection.js#L266)) — julgar ali derrubaria a aba a cada menção lida. A reconciliação dela já é outra (plano 72 F8).
  4. **Uma decisão por thread**: reavaliar a cada mudança de `contactData` faria a aba ceder no instante em que o operador **atribui a si mesmo** uma conversa vinda da fila "Não atribuídas" — o fluxo normal daquela aba.
  5. Sem evidência nenhuma (thread nova, sem linha e sem conversa carregada) só `mine` cede: a conversa nasce sem responsável ⇒ nunca é "minha", mas é legitimamente "não atribuída".
- **Problemas / pendências:** P1 (atribuir a conversa ao criador no backend) segue ADIADO, como o plano determina. Efeito colateral aceito: abrir, **durante uma busca**, uma conversa de outro atendente também faz a aba ceder — ao limpar a busca o operador cai em "Todas" (um clique de volta).
- **Verificação:** `node --test hubDefaults.test.js` → 19/19; suíte dos 5 módulos puros do hub → **126/126 verde**; `node --input-type=module --check` no `Contacts.js`. Roteiro manual na F5.

---

### Fase 5 — Validação (🔴 sozinha)

**Objetivo:** provar o comportamento nos três perfis que existem hoje, sem regressão nas suítes.

**Itens**
1. `[paralelo]` `node --test` nos módulos puros de frontend (o novo + os do hub que já existem: `urlState.test.js`, `conversationFilterSpec.test.js`, `conversationRows.test.js`, e o `deepLinkResolve.test.js` que veio do plano 89).
2. `[paralelo]` Suíte Python contra o Postgres de teste (`WHATSBOT_TEST_DB_URL`) — esperada **verde e inalterada** (nada de backend mudou); é a prova de que o plano não vazou para o servidor.
3. `[sequencial]` Roteiro manual da §7.
4. `[sequencial]` Conferir o modo escuro: **nenhuma superfície nova** foi criada (só mudou qual aba nasce ativa) ⇒ nada a re-tematizar. Registrar no status como verificado, não como N/A.

**Pronto quando:** checklist da §7 inteiro marcado.

#### Status de execução — Fase 5
**Estado:** 🟡 Automatizada concluída · **roteiro manual do §7 pendente** (exige navegador com sessão de atendente)
- **O que foi feito:** suítes automatizadas rodadas e o checklist do §7 marcado no que é verificável sem browser; nenhum arquivo mudou nesta fase.
- **Como foi feito / decisões:** o item 4 (modo escuro) foi verificado por inspeção do diff — nenhuma superfície, classe ou cor nova entrou, então não há o que re-tematizar; registrado como **verificado**, não como N/A, conforme a fase pede.
- **Problemas / pendências:**
  1. **Roteiro manual do §7 não executado** — os 3 blocos (atendente com conversas, atendente sem conversa atribuída, instalação sem login) precisam de um navegador logado. É o que falta para fechar o plano.
  2. O run do **diretório** `tests/characterization` mostra falhas **não determinísticas** (mudam de arquivo entre execuções; cada arquivo passa sozinho) — interferência entre arquivos, agravada pelo WIP não-commitado de terceiros na árvore (`server/routes/sandbox.py`, `agent/memory.py`, `gowa/inbound.py`, goldens de legenda de mídia). **Não é deste plano** (zero `.py` tocado), mas vale um registro à parte.
- **Verificação:**
  - `node --test` em **todos** os `*.test.js` do frontend → **389/389 verde** (inclui os 19 do `hubDefaults.test.js` e os do plano 89).
  - `venv/bin/python tests/test_endpoints.py` → **1626 passed, 0 failed** (Postgres de teste `whatsbot_test`).
  - `pytest tests/characterization/test_webhook_characterization.py` → 28/28 verde; `test_sandbox_improve_characterization.py` → verde isolado.
  - `git diff --stat 4d7b500..HEAD` → só `.js`/`.md`; nenhum `.py`, nenhuma migration.

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Instalação **aberta** (sem login) | `assignee=me` é recusado pelo servidor ([translate.py:220-222](../db/filters/translate.py#L220)) e a sidebar responde vazia **sem mensagem de erro** ([useConversationList.js:232](../web/static/js/components/contacts/hooks/useConversationList.js#L232)); além disso a aba "Minhas" nem é desenhada | D2: `defaultAssignmentTab` degrada para `all`. Teste dedicado em F1 + item explícito no roteiro manual |
| `localStorage` indisponível (modo privado / política do navegador) | `hasStoredUser()` lança e derruba o mount do hub | `try/catch` devolvendo `false` (degrada para "Todas" — pior UX, nunca tela branca) |
| Sessão expirada com `whatsbot_user` ainda no storage | Nasce em "Minhas" e a lista falha | Cenário já existente e auto-corrigido: o `AuthGate` limpa a chave e manda para o login ([AuthGate.js:101](../web/static/js/components/shell/AuthGate.js#L101)) |
| Admin/supervisor cuja caixa "Minhas" é sempre vazia | Passa a ver "Nenhum contato encontrado" toda vez que volta de outra tela | Consequência **aceita** por D1. Escape a um clique ("Todas"), e a escolha sobrevive enquanto ele ficar no hub — só não sobrevive à troca de tela (D4). É a P2 |
| Inversão do significado da URL limpa | Um link `/` compartilhado antes (que significava "Todas") passa a abrir "Minhas" para quem recebe | Aceito e intencional. Link **com** `?assignment=…` continua exato nos dois valores. Deixar isso explícito no comentário do módulo |
| Piscada "Todas → Minhas" no mount | Dois defaults desalinhados (schema × seed) causariam re-render + fetch descartado | F3 é obrigatória junto com F2, com o **mesmo** valor calculado uma vez |
| Menos linhas entrando ao vivo na sidebar | Mensagem nova de conversa não atribuída não insere linha em "Minhas" ([conversationRows.js:268](../web/static/js/services/conversationRows.js#L268)) | Semântica correta da aba. O badge global de não lidas é server-side e independente (§2.3); a aba "Não atribuídas" mantém contagem ao vivo ao lado |
| Plugin `protocolos` (override da rota `attendances`) | É justamente a tela de onde os atendentes voltam (print do usuário) | O plugin não toca o estado do hub — a mudança é interna ao `<Contacts/>`. Ainda assim, o roteiro manual usa **Protocolos** como tela de ida e volta |
| **Executar este plano sem o 89** | "Minhas" vazia + guard de deep-link intacto = link de conversa deixa de abrir **em silêncio** para o atendente, virando chamado diário | ⛔ Portão na §4: o 89 é pré-requisito, não sugestão. Se por algum motivo este plano for adiantado, a mitigação mínima é o item 1 da Fase 2 do 89 (o guard) |
| Achar que "Minhas" restringe acesso | Alguém pode concluir que a aba isola dados e usar isso como controle de privacidade | D6: o servidor não escopa por dono. Quem quer isolamento real usa **membership de canal** ([authz.py:77-90](../server/authz.py#L77)). Nenhum texto do plano ou da UI deve sugerir o contrário |
| Vazamento pelo `/ws` (fora deste plano) | O fan-out do WebSocket entrega conteúdo de canais fora da membership ([registro 45, achado #1](45-registro-bugs-riscos-realtime.md)) — nada a ver com a aba, mas costuma ser confundido | [Plano 90](90-plano-escopo-do-websocket-por-canal.md). Este plano **não** melhora nem piora esse ponto |

---

## 6. Perguntas em aberto

**P1 — Conversa iniciada pelo operador deveria nascer atribuída a ele?**
⏸️ **ADIADO** (2026-07-28). Hoje ela nasce sem responsável (§Fase 4) — por isso a F4 precisa da mitigação client-side de trocar para "Todas".
(a) Manter como está + mitigação de UI (**escolha do plano**: zero backend, zero migration, reversível).
(b) Atribuir ao criador no backend, no create manual — resolve na raiz (a conversa nova é de fato "minha") e alinha com o Chatwoot, mas muda semântica de negócio, mexe em `conversation_service` e afeta relatório/auditoria.
**Recomendação:** executar (a) agora; levar (b) ao usuário como pergunta de produto separada, já que "quem inicia é o dono" é uma regra de atendimento, não uma decisão técnica.

**P2 — Estender a memória ao resto da view (status, etiquetas, ordenação, filtro avançado)?**
⏸️ **ADIADO** (2026-07-28) por D4 — era a terceira opção oferecida e o usuário escolheu a fixa. O sintoma é o mesmo (§2.1: a query-string morre no `setTab`) e a correção natural seria preservar a query do hub no shell ([App.js:163-171](../web/static/js/components/shell/App.js#L163)), o que resolveria **todas** as dimensões de uma vez. Reabrir só se os atendentes reclamarem de perder o chip "Abertas/Resolvidas" ou as etiquetas.

**P3 — A aba deveria depender de permissão/cargo?**
⏸️ **ADIADO**. Existe RBAC no painel ([authz.py](../server/authz.py)), então "supervisor abre em Todas, atendente em Minhas" é implementável sem inventar conceito novo. Fora de escopo por D1; anotado porque é a evolução mais provável se a P2 voltar.

---

## 7. Checklist de verificação

Portão (antes de começar):
- [x] **[Plano 89](89-plano-link-de-conversa-sempre-abre.md) mergeado e com o checklist dele marcado** — sem isso, este plano transforma um bug raro em rotina *(F1–F5 executadas; `deepLinkResolve.js` + teste em pé)*

Frontend puro:
- [x] `node --test web/static/js/services/hubDefaults.test.js` verde *(19/19)*
- [x] `node --test` verde nos módulos puros vizinhos (`urlState.test.js`, `conversationFilterSpec.test.js`, `conversationRows.test.js`) *(e TODOS os `*.test.js` do frontend: 389/389)*

Backend (prova de não-vazamento):
- [x] Suíte no Postgres de teste verde e **sem diff** (`WHATSBOT_TEST_DB_URL` apontando para um banco com `test` no nome) — `tests/test_endpoints.py` **1626 passed, 0 failed**; `tests/characterization/test_webhook_characterization.py` 28/28. ⚠️ O run do **diretório** `tests/characterization` inteiro tem falhas que **mudam de arquivo a cada execução** (1ª vez: webhook; 2ª: sandbox/audit/rbac) e que **passam quando o arquivo roda sozinho** — interferência entre arquivos + WIP não-commitado de terceiros na árvore (`server/routes/sandbox.py`, `agent/memory.py`, `gowa/inbound.py`, goldens de legenda). Nada disso pode vir deste plano: ele não toca `.py`
- [x] `git diff --stat` não toca nenhum arquivo `.py`, nem `db/alembic/versions/` — os 3 commits mexem só em 2 `.js` do hub, 2 `.js` novos em `services/` e este `.md`

Roteiro manual — atendente logado (com conversas atribuídas):
- [ ] Abrir `/` → aba **Minhas** ativa, sem piscar "Todas"
- [ ] Ir em **Protocolos** (menu da engrenagem) e voltar → continua em **Minhas** ✅ *(o sintoma relatado)*
- [ ] Idem via **Contatos**, **Canais** e **Configurações Gerais**
- [ ] Clicar em "Todas" → URL vira `?assignment=all`; recarregar (F5) mantém Todas
- [ ] Botão **voltar** do navegador (popstate) hidrata a aba certa nos dois sentidos
- [ ] Abrir `?assignment=unassigned` direto → abre em "Não atribuídas"

Roteiro manual — atendente **sem** conversa atribuída (a sidebar fica de fato vazia):
- [ ] `/conversations/<id>` de uma conversa de outra pessoa **abre o chat** *(garantido pelo 89 — reconferir aqui porque este plano é quem torna o cenário rotineiro)*
- [ ] `/conversations/<id>?message=<msg_id>` rola e destaca a mensagem
- [ ] "Nova conversa" → enviar: a conversa aparece na sidebar e segue aberta
- [ ] O mesmo em largura de celular (< `lg`)

Roteiro manual — instalação **sem login** (modo aberto):
- [ ] Abre em **Todas** (a aba "Minhas" nem é renderizada) e a lista carrega normalmente
- [ ] Nenhum request de lista retorna erro de filtro (`Filtro 'me' requer um usuário autenticado`)

Transversal:
- [x] Modo escuro conferido no hub — **nenhuma superfície, classe ou cor nova** foi introduzida (o diff de UI é só qual aba nasce ativa); nada a re-tematizar
- [x] Nenhum segredo/identificador de usuário passou a aparecer na URL — a query só ganha `assignment=all`; o id do usuário é lido do `localStorage` e **nunca** serializado
- [x] Um refactor por commit: F1 isolado; F2+F3 juntas; F4 separado

---

## 8. Apêndice — arquivos-chave

**Frontend — novos**
- `web/static/js/services/hubDefaults.js` (módulo puro: default da aba + fábrica do schema de URL do hub)
- `web/static/js/services/hubDefaults.test.js` (`node --test`)

**Frontend — alterados**
- [web/static/js/components/contacts/Contacts.js](../web/static/js/components/contacts/Contacts.js) — §29-42 (schema), §203-218 (precedência + props do hook), §230-259 (`useUrlState`)
- [web/static/js/components/contacts/hooks/useConversationFilters.js](../web/static/js/components/contacts/hooks/useConversationFilters.js) — §43-46 (opção + seed)
- [web/static/js/components/contacts/hooks/useChannelPicker.js](../web/static/js/components/contacts/hooks/useChannelPicker.js) — §116-119 (conversa nova)
- ~~`useConversationSelection.js`~~ — saiu daqui; é do [plano 89](89-plano-link-de-conversa-sempre-abre.md)

**Frontend — lidos, não alterados** (contexto obrigatório para o executor)
- [web/static/js/hooks/useUrlState.js](../web/static/js/hooks/useUrlState.js) — por que a hidratação do mount vence o seed
- [web/static/js/services/urlState.js](../web/static/js/services/urlState.js) — o default do schema é **também** a regra de omissão na escrita
- [web/static/js/services/conversationFilterSpec.js](../web/static/js/services/conversationFilterSpec.js) — `assignmentTab` → `assignee=me`
- [web/static/js/services/conversationRows.js](../web/static/js/services/conversationRows.js) — `matchesAssignment` / `rowMatchesView`
- [web/static/js/components/contacts/ConversationFilterBar.js](../web/static/js/components/contacts/ConversationFilterBar.js) — `hasIdentity` esconde a aba "Minhas"
- [web/static/js/components/shell/App.js](../web/static/js/components/shell/App.js) / [ScreenRouter.js](../web/static/js/components/shell/ScreenRouter.js) — onde a query-string morre e o hub desmonta
- [web/static/js/components/shell/AuthGate.js](../web/static/js/components/shell/AuthGate.js) / [web/static/js/services/drafts.js](../web/static/js/services/drafts.js) — a chave `whatsbot_user` como fonte síncrona de identidade

**Backend — apenas referência (nada muda)**
- [db/filters/translate.py](../db/filters/translate.py) — `assignee=me` exige sessão
- [channels/ai_settings.py](../channels/ai_settings.py) — `default_assignee_user_id` por canal (só inbound; contexto da P1)
- [server/authz.py](../server/authz.py) · [db/repositories/conversation_repo.py](../db/repositories/conversation_repo.py) — a prova de D6: o único escopo é membership de canal, nunca o dono

**Planos relacionados**
- [89 — link de conversa sempre abre](89-plano-link-de-conversa-sempre-abre.md) — **pré-requisito deste**; recebeu a antiga Fase 4
- [45 — registro de bugs realtime](45-registro-bugs-riscos-realtime.md) — achado #1 (vazamento do `/ws`), problema distinto que costuma ser confundido com escopo de aba
