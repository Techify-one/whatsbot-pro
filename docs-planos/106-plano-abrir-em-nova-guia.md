# Plano 106 — Ctrl+clique / clique do meio abrem em nova guia (links de verdade no painel)

> **Status:** 🟨 EM EXECUÇÃO — **core concluído** (F1, F2, F5·C1+C5, F4·B1+B6); **fases de plugin adiadas** (F3, F5·C2/C3/C4, F6) · **Data:** 2026-08-05 · **Execução do core:** 2026-08-06 · **Escopo:** médio (frontend do core + 3 plugins instalados; nenhuma migration)
>
> ⚠️ **Por que parou no core:** o plugin `protocolos` estava sob edição concorrente de outra IA (plano 107) na rodada de 2026-08-06. As fases que tocam `storages/plugins/` foram deixadas para depois — não por conflito de texto (as regiões mal se cruzam), mas pela **reconstrução do zip**: dois builds a partir de fontes divergentes se sobrescrevem em silêncio, que é exatamente a regressão do `protocolos` 1.26.0. **Retomar por:** F3 (é só remover 3 `preventDefault`) → F6 → F5·C4 → F7 itens 2-6.
> **Origem:** pedido do usuário — *"quando eu clicava nessa linha para ir para a conversa do atendimento, ela abria uma nova guia quando eu segurava o Ctrl. Hoje isso não está acontecendo… o sistema está sempre substituindo todas as guias"*.
> **Método:** leitura do código com `arquivo:linha` verificado + arqueologia no git (`git log -S`) para separar regressão de "nunca existiu". Medições por `grep -rln` / `wc -l`.
> O painel é uma SPA que navega por `history.pushState` dentro de `onClick` de `<div>`/`<tr>`/`<button>`. Elemento que não é `<a href>` não tem semântica de link — o navegador não tem o que abrir em outra guia. Só o menu da engrenagem faz certo. Este plano dá **um mecanismo único** (link interno interceptado no shell) e depois converte os pontos de navegação, um a um, em fases independentes.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| D1 ✅ (2026-08-05) | O comportamento desejado é **Ctrl/⌘+clique e clique do meio abrirem nova guia**, sem perder o que estava aberto | O clique **simples** continua navegando na mesma guia (SPA). Nada de `target="_blank"` forçado |
| D2 ✅ (2026-08-05) | O item **"Protocolos" da engrenagem é regressão confirmada** | Existia um `MenuItem` nativo "Atendimentos" com anchor + guard (`git show d7541cf:web/static/js/app.js`, linha 209), removido quando o plugin assumiu a rota. É a **F3**, a fase mais barata e de maior valor percebido |
| D3 ✅ (2026-08-05) | A **linha de "Atendimentos"** no detalhe do protocolo **nunca teve link** em versão nenhuma | Verificado no histórico do plugin **e** na tela nativa antiga (`git show d7541cf:web/static/js/components/attendances/Attendances.js`, linha 210, também só fazia `pushState`). É **feature nova**, não conserto — não gastar tempo procurando "o que quebrou" |
| D4 ✅ (2026-08-05) | O plano cobre core + os plugins **instalados** (`protocolos`, `melhorias`, `agendamento_retorno`) | Cada fase de conversão é independente: o usuário pode executar só as que quiser e parar quando bastar |
| D5 ✅ (2026-08-05) | Nada de mudança de banco, rota REST nova no core, ou contrato de plugin novo | Exceção única: a **F6**, que acrescenta um campo ao payload de um endpoint **do próprio plugin** protocolos |

---

## 1. Resumo executivo

Em todo o frontend do core existem exatamente **6 ocorrências de `href=`** fora do menu da engrenagem, e **todas** apontam para fora do app (`target="_blank"`: mapa, documento, saldo Techify, links de mensagem). A navegação interna é feita em **15 arquivos do core + 5 de plugin** por `history.pushState(...)` disparado de um `onClick`.

O menu da engrenagem é o único lugar que faz certo — `MenuItem` renderiza `<a href>` e só cancela o clique quando ele é simples ([GearMenu.js:15-33](../web/static/js/components/shell/GearMenu.js#L15-L33)). Por isso Ctrl+clique funciona em "Melhorias"/"Vendas IA" (screens de manifest → viram `MenuItem`) e **não** funciona em "Protocolos" (vem do slot do plugin, com `preventDefault()` incondicional).

A forma da solução: (1) um **módulo puro** com a regra "este clique é do usuário ou do navegador?"; (2) um **interceptor delegado de links internos** no shell, que faz qualquer `<a href="/…">` do painel — do core *ou de plugin* — navegar por SPA no clique simples e ser entregue ao navegador no clique modificado; (3) conversão dos pontos de navegação em links, com um caminho alternativo (`window.open`) para os três casos onde `<a>` é impossível (`<tr>`, card arrastável, linha da sidebar com drag+menu de contexto).

O trabalho de **dar URL a tudo já foi feito** pelo plano 24 (deep-links de entidade, `?detail=`, `?message=`, `CopyLinkButton`). Este plano só transforma cliques em links.

---

## 2. Como funciona hoje (mapa verificado)

| # | Fato | Onde |
|---|---|---|
| 1 | `MenuItem` é `<a href>` e libera o clique modificado — **o único do core que funciona** | [GearMenu.js:15-33](../web/static/js/components/shell/GearMenu.js#L15-L33) |
| 2 | O guard existe desde `4025aa2` e sobreviveu à decomposição do shell (`77228a1`) — **não é o que quebrou** | `git log -S "e.ctrlKey" -- web/` |
| 3 | Havia um `MenuItem` nativo **"Atendimentos"** (anchor + guard) no menu | `git show d7541cf:web/static/js/app.js` linha 209 |
| 4 | Ele foi gateado por `!getRouteOverride('attendances')` e depois removido; o substituto veio do slot do plugin | `git show f5fb823:web/static/js/app.js` linha 258 · [GearMenu.js:89-91](../web/static/js/components/shell/GearMenu.js#L89-L91) |
| 5 | ⚠️ O item do slot tem `e.preventDefault()` **incondicional** ⇒ Ctrl+clique morre ali | [extends.js:297-303](../storages/plugins/protocolos/static/extends.js#L297-L303) |
| 6 | O `melhorias` tem o **mesmo bug**: `<a href>` real, mas `navConversation` cancela sempre | [panel.js:118](../storages/plugins/melhorias/static/panel.js#L118), [:138-146](../storages/plugins/melhorias/static/panel.js#L138-L146), [:703](../storages/plugins/melhorias/static/panel.js#L703) |
| 7 | Toda navegação interna do app faz `pushState` + `dispatchEvent(new PopStateEvent('popstate'))` | 11 arquivos (`grep -rn PopStateEvent`) |
| 8 | O shell re-sincroniza tab/contato/conversa/entidade **no `popstate`** — é o seam que um interceptor pode reusar sem inventar nada | [App.js:203-215](../web/static/js/components/shell/App.js#L203-L215) |
| 9 | Já existe precedente de **guarda global no `window`** dentro do App (plano 64, drag-and-drop) | [App.js:183-201](../web/static/js/components/shell/App.js#L183-L201) |
| 10 | O deep-link de todas as telas já existe e é compartilhável (`entityPath`, `basePath`, `useDeepLink`) | [useDeepLink.js:25-101](../web/static/js/hooks/useDeepLink.js#L25-L101) |
| 11 | Já existe util de "esta coisa tem URL" (`deepLinkUrl`, `CopyLinkButton`), usado em 4 telas | [copyDeepLink.js:14-29](../web/static/js/utils/copyDeepLink.js#L14-L29) |
| 12 | `/conversations/<id>`, `/executions/<id>`, `/contacts/<id>`, `/protocolos?detail=<id>` **abrem corretamente numa guia nova** hoje (colando a URL) | [routing.js:77-110](../web/static/js/components/shell/routing.js#L77-L110) + [deepLinkResolve.js:52-70](../web/static/js/services/deepLinkResolve.js#L52-L70) |
| 13 | A linha da sidebar é `<div>` com **drag-drop de arquivo + menu de contexto próprio + modo seleção** | [ContactList.js:643-670](../web/static/js/components/contacts/ContactList.js#L643-L670) |
| 14 | O card do Kanban é `<div draggable>` (arrastar entre colunas) | [protocolos_tab.js:1617-1628](../storages/plugins/protocolos/static/protocolos_tab.js#L1617-L1628) |
| 15 | ⚠️ A linha de atendimento resolve a âncora por **`fetch` assíncrono** antes de saber a URL final | [protocolos_tab.js:1733-1747](../storages/plugins/protocolos/static/protocolos_tab.js#L1733-L1747) |
| 16 | `cycle_anchor` (a query da âncora) é leve: 1–2 `SELECT` por ciclo | [logic.py:3039-3066](../storages/plugins/protocolos/logic.py#L3039-L3066) |

**Escala medida:** 15 arquivos do core + 5 de plugin com `pushState`; 6 `href=` no core, todos externos; 28 suítes `node --test` em `services/` (o padrão onde o helper puro deste plano se encaixa).

---

## 3. Inventário — pontos de navegação

### 3.1 Grupo A — já é `<a href>`, o JS é que cancela sempre (correção de 1 linha)

| # | Onde (UI) | Arquivo | Destino | Risco | Esforço |
|---|---|---|---|---|---|
| A1 | Engrenagem → **Protocolos** | [extends.js:297-303](../storages/plugins/protocolos/static/extends.js#L297-L303) | `/protocolos` | baixo | **S** |
| A2 | Melhorias → "Abrir conversa ↗" (coluna da lista) | [panel.js:113-122](../storages/plugins/melhorias/static/panel.js#L113-L122) | `conversation_url` | baixo | **S** |
| A3 | Melhorias → "Abrir conversa nesta mensagem ↗" (detalhe) | [panel.js:702-707](../storages/plugins/melhorias/static/panel.js#L702-L707) | `conversation_url` | baixo | **S** |

### 3.2 Grupo B — é botão/`div` simples; pode virar `<a>` sem conflito

| # | Onde (UI) | Arquivo | Destino | Risco | Esforço |
|---|---|---|---|---|---|
| B1 | Contatos → "Ver detalhes" | [ContactsListScreen.js:281-286](../web/static/js/components/ContactsListScreen.js#L281-L286) → [:546-549](../web/static/js/components/ContactsListScreen.js#L546-L549) | `/contacts/<id>` | baixo | **S** |
| B2 | Contatos → "Iniciar conversa" (ícone) | [ContactsListScreen.js:294-300](../web/static/js/components/ContactsListScreen.js#L294-L300) → [:585-595](../web/static/js/components/ContactsListScreen.js#L585-L595) | `/conversations/<id>` ⚠️ resolvido por `fetch` | médio | **M** |
| B3 | Agendamentos de retorno → "Abrir conversa" (cards) | [ScheduleTabs.js:334-338](../storages/plugins/agendamento_retorno/static/ScheduleTabs.js#L334-L338) | `/conversations/<id>` | baixo | **S** |
| B4 | Agendamentos de retorno → "Abrir conversa" (tabela) | [ScheduleTabs.js:406-410](../storages/plugins/agendamento_retorno/static/ScheduleTabs.js#L406-L410) | `/conversations/<id>` | baixo | **S** |
| B5 | Melhorias (aba IA) → "ver no painel" | [ai_section.js:16-20](../storages/plugins/melhorias/static/ai_section.js#L16-L20), [:95](../storages/plugins/melhorias/static/ai_section.js#L95) | `/melhorias?detail=<id>` | baixo | **S** |
| B6 | Sub-abas de IA / Usuários / Atributos | [AgentEngine.js:133-142](../web/static/js/components/ai/AgentEngine.js#L133-L142), [UsersManager.js:330-338](../web/static/js/components/UsersManager.js#L330-L338), [CustomAttributesManager.js:339-347](../web/static/js/components/CustomAttributesManager.js#L339-L347) | `/ai/<sub>`, `/users/roles`, … | baixo | **S** — *valor baixo, opcional* |

### 3.3 Grupo C — **não pode** virar `<a>`; precisa de tratamento explícito

| # | Onde (UI) | Arquivo | Destino | Impedimento | Risco | Esforço |
|---|---|---|---|---|---|---|
| C1 | **Linha da conversa na sidebar** | [ContactList.js:643-670](../web/static/js/components/contacts/ContactList.js#L643-L670) → [useConversationSelection.js:175-185](../web/static/js/components/contacts/hooks/useConversationSelection.js#L175-L185) | `/conversations/<id>` | drag-drop de arquivo + `onContextMenu` próprio + modo seleção | médio | **M** |
| C2 | **Card do Kanban** de protocolos | [protocolos_tab.js:1617-1628](../storages/plugins/protocolos/static/protocolos_tab.js#L1617-L1628) | `/protocolos?detail=<id>` | `draggable=true` (arrastar entre colunas) | médio | **M** |
| C3 | **Linha da lista** de protocolos | [protocolos_tab.js:1313-1317](../storages/plugins/protocolos/static/protocolos_tab.js#L1313-L1317) | `/protocolos?detail=<id>` | `<a>` não envolve `<tr>` | baixo | **S** |
| C4 | **Linha "Atendimentos"** no detalhe do protocolo *(a do print do usuário)* | [atendimentos_table.js:160-166](../storages/plugins/protocolos/static/atendimentos_table.js#L160-L166) → [protocolos_tab.js:1733-1747](../storages/plugins/protocolos/static/protocolos_tab.js#L1733-L1747) | `/conversations/<id>?message=<m>` | `<tr>` + URL só conhecida **depois** de um `fetch` | médio | **M** |
| C5 | **Linha da tabela** de Execuções | [Executions.js:1029-1036](../web/static/js/components/Executions.js#L1029-L1036) → [:686-703](../web/static/js/components/Executions.js#L686-L703) | `/executions/<id>` | `<a>` não envolve `<tr>` | baixo | **S** |

### 3.4 Falsos positivos descartados

| Suspeita | Por que NÃO é o caminho |
|---|---|
| "O roteador do SPA quebrou" | Não. `tabFromPathPure` ([routing.js:77-88](../web/static/js/components/shell/routing.js#L77-L88)) e o deep-link do plano 24 estão íntegros — **toda URL alvo abre certo quando colada numa guia nova**. O que falta é o clique virar link |
| "O menu da engrenagem perdeu o suporte" | Não. O guard está lá desde `4025aa2`. Teste A/B: Ctrl+clique em **"Melhorias"** abre nova guia; em **"Protocolos"** não. Mesmo menu, componentes diferentes |
| "O `overrideRoute` do plugin causa isso" | Não. O override decide **o que renderizar** para a tab; não passa perto do evento de clique |
| "Basta pôr `target="_blank"`" | Isso forçaria **sempre** nova guia e quebraria o clique normal (viola D1) |
| "É o `PageHeader` (botão voltar)" | Voltar é histórico, não navegação para nova guia. Fora do escopo |
| "Os saltos de mensagem (busca na conversa, citação, `?message=`)" | São rolagem **dentro** da thread aberta, não troca de página. Fora do escopo |
| "O `/wizard`" | Onboarding de instalação; abrir em outra guia não faz sentido |
| "Precisa expor um helper novo em `api.services` para os plugins" | Não — e seria armadilha: `protocolos` não declara `plugin_services_version`, então recebe o adapter **1.x** e não veria o nome novo. Os plugins deste plano não importam nada novo do core (ver **P1**) |

---

## 4. Fases / Roadmap

```
WAVE 0   F1 (módulo puro spaLink)                                 🔴 sozinha
              │  [bloqueia: F2, F5]
              ├──────────────┬───────────────┐
WAVE 1   F2 (interceptor)  F5 (linhas/cards)  F6 (âncora no payload)   🟢 paralelas
              │              ↑ item C4 depende de F6
              │  [bloqueia: F3, F4]
              ├──────────────┐
WAVE 2   F3 (plugins A1-A3)  F4 (core+plugins B1-B6)              🟢 paralelas
              │
WAVE 3   F7 (testes + doc + republicar zips)                      🔴 sozinha
```

| Wave | Fase | Workstream | Paraleliza? | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F1** | Módulo puro `spaLink.js` + testes | 🔴 sozinha | baixo | `node --test` verde; nada de UI mudou ainda |
| 1 | **F2** | Interceptor delegado de links internos no shell `[depende de: F1]` `[bloqueia: F3, F4]` | 🟢 | médio | um `<a href="/costs">` cru navega por SPA no clique e abre guia no Ctrl |
| 1 | **F5** | Grupo C — linhas/cards que não podem virar `<a>` `[depende de: F1]` | 🟢 | médio | Ctrl/meio nas 5 linhas abre nova guia; clique normal e arrastar intactos |
| 1 | **F6** | `anchor_message_id` no payload do protocolo (plugin) `[bloqueia: item C4 da F5]` | 🟢 | baixo | o detalhe traz a âncora sem `fetch` extra |
| 2 | **F3** | Grupo A — plugins: parar de cancelar o clique `[depende de: F2]` | 🟢 | baixo | **Ctrl+clique em "Protocolos" na engrenagem abre nova guia** |
| 2 | **F4** | Grupo B — botões que são links | 🟢 | baixo | Ctrl/meio nos botões listados abre nova guia |
| 3 | **F7** | Testes, `CLAUDE.md`, republicar os zips dos plugins | 🔴 sozinha | baixo | suíte verde + zips reconstruídos e instalados |

> **Atalho de valor:** se o usuário quiser só o pedido literal do print e do menu, o caminho mínimo é **F1 → F2 → F3 → (F5 item C4)**. F4, F6 e o resto da F5 são melhoria incremental.

---

### F1 — Módulo puro `spaLink.js` (🔴 sozinha) `[bloqueia: F2, F5]`

**Objetivo:** ter num lugar só, puro e testável, a regra "este clique é para o app ou para o navegador?" — hoje ela existe copiada em uma linha dentro do `GearMenu`.

**Itens:**
1. `[sequencial]` Criar `web/static/js/services/spaLink.js` (irmão de [urlState.js](../web/static/js/services/urlState.js) e [deepLinkResolve.js](../web/static/js/services/deepLinkResolve.js) — sem Preact, sem DOM global, `node --test`-ável). Superfície proposta:
   - `isModifiedClick(e)` → `true` quando `e.button !== 0 || ctrlKey || metaKey || shiftKey || altKey` (a MESMA condição de [GearMenu.js:20](../web/static/js/components/shell/GearMenu.js#L20), extraída).
   - `shouldOpenInNewTab(e)` → `true` para Ctrl/⌘ + clique **ou** botão do meio (`e.button === 1`).
   - `isInternalHref(href, origin)` → `true` só para path do próprio app; recusa `mailto:`, `tel:`, `#`, `javascript:` e outro host.
   - `spaLinkTarget(anchorLike, origin)` → `{ path } | null` — o que o interceptor da F2 vai consumir; puro, recebe `{ href, target, download, dataset }` em vez de um nó do DOM.
2. `[paralelo]` `web/static/js/services/spaLink.test.js` (`node --test`): clique simples, Ctrl, ⌘, meio, botão direito, `target="_blank"`, `download`, host externo, `mailto:`, âncora sem `href`.
3. `[paralelo]` **Não** mexer no `GearMenu` ainda (ele continua funcionando com o guard próprio; a simplificação dele é item da F7).

**Pronto quando:** `node --test web/static/js/services/spaLink.test.js` verde e **nenhuma** mudança visível no painel.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-08-06)
- **O que foi feito:** criados [web/static/js/services/spaLink.js](../web/static/js/services/spaLink.js) e `spaLink.test.js`. Quatro funções, exatamente a superfície proposta: `isModifiedClick`, `shouldOpenInNewTab`, `isInternalHref`, `spaLinkTarget`. `GearMenu.js` **não** foi tocado (é item da F7).
- **Como foi feito / decisões:**
  - Tudo puro. `URL` é global padrão (browser + Node), não API de DOM — dá para parsear href sem `document.createElement('a')`.
  - `isModifiedClick` (largo: Ctrl/⌘/Shift/Alt/qualquer botão ≠ esquerdo) e `shouldOpenInNewTab` (estrito: só Ctrl/⌘+esquerdo e botão do meio) são **deliberadamente diferentes**. O interceptor da F2 usa o largo — Shift (nova janela) e Alt (baixar) são gestos nativos que não se deve cancelar. As superfícies da F5 usam o estrito, porque só sabem emular "nova guia"; nelas Shift/Alt caem na ação normal, como hoje.
  - `download` no contrato de `spaLinkTarget` é **booleano** — o call site passa `a.hasAttribute('download')`, nunca `a.download` (que devolve `''` tanto para ausente quanto para `<a download>`, sem distinguir).
  - `target` vazio ou `_self` passa; qualquer outro valor (`_blank`, frame nomeado) recusa.
  - `isInternalHref` recusa por protocolo (`mailto:`/`tel:`/`javascript:`/`data:`/`blob:`) antes de comparar origin, e trata `#…` como não-navegação (senão `new URL('#x', origin)` resolveria para o próprio endereço e passaria).
- **Problemas / pendências:** nenhuma. Nenhuma mudança visível no painel (nada consome o módulo ainda).
- **Verificação:** `node --test web/static/js/services/spaLink.test.js` → **19/19 pass, 0 fail**. Inclui os casos reais que não podem ser capturados (link de mensagem, saldo Techify, mídia com `download` e mídia com `_blank`).

---

### F2 — Interceptor delegado de links internos no shell (🟢) `[depende de: F1]` `[bloqueia: F3, F4]`

**Objetivo:** qualquer `<a href="/…">` do painel — do core **ou de um plugin, sem o plugin fazer nada** — passa a navegar por SPA no clique simples e a ser entregue ao navegador no clique modificado. É o que remove a necessidade de repetir o guard em cada call site.

**Itens:**
1. `[sequencial]` Em [App.js](../web/static/js/components/shell/App.js), ao lado da guarda global de drag-and-drop do plano 64 ([App.js:183-201](../web/static/js/components/shell/App.js#L183-L201)), registrar um listener de `click` no `document`:
   - sair na primeira comparação quando `isModifiedClick(e)` (deixa o navegador abrir a guia) ou `e.defaultPrevented` (alguém já tratou);
   - subir do alvo até o `<a>` mais próximo (`e.target.closest('a[href]')`); sem âncora, sair;
   - passar por `spaLinkTarget(...)`; `null` ⇒ sair (link externo, `target=_blank`, `download`, `mailto:`);
   - caso contrário: `e.preventDefault()` → `history.pushState(null, '', path)` → `window.dispatchEvent(new PopStateEvent('popstate'))` — **exatamente** o par que todo call site já usa e que [App.js:203-215](../web/static/js/components/shell/App.js#L203-L215) escuta.
2. `[sequencial]` Escape hatch declarativo: uma âncora com `data-no-spa` (ou `target`) é ignorada pelo interceptor — para o dia em que uma tela precisar de recarga real.
3. `[paralelo]` Clique do meio: acrescentar `mousedown` que faça `e.preventDefault()` **apenas** quando `e.button === 1` sobre um link interno, para o Chrome não iniciar o *auto-scroll* em vez de abrir a guia.
4. `[paralelo]` Conferir que os links de **conteúdo de mensagem** ([messageEntities.js:144](../web/static/js/services/messageEntities.js#L144)) continuam intocados: são `target="_blank"` + host externo, recusados pelo `spaLinkTarget`. Idem "Saldo e Recarregar" ([GearMenu.js:126-137](../web/static/js/components/shell/GearMenu.js#L126-L137)) e os `<a>` de mídia ([MediaContent.js:100](../web/static/js/components/contacts/MediaContent.js#L100), [:131](../web/static/js/components/contacts/MediaContent.js#L131)).

⚠️ **Ordem importa:** o interceptor é um listener de *bubbling* no `document`, então ele roda **depois** dos `onClick` dos componentes. Qualquer handler que já chame `preventDefault()` (ex.: [copyDeepLink.js:33-34](../web/static/js/utils/copyDeepLink.js#L33-L34)) continua vencendo — o item 1 respeita `defaultPrevented`.

**Pronto quando:** colar temporariamente um `<a href="/costs">teste</a>` numa tela e verificar: clique simples troca de tela **sem recarregar a página**; Ctrl+clique abre nova guia em `/costs`; clique do meio idem; um link `https://` externo continua abrindo fora. Remover o `<a>` de teste.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-08-06)
- **O que foi feito:** interceptor delegado em [App.js](../web/static/js/components/shell/App.js), num `useEffect` próprio logo depois da guarda de drag-and-drop do plano 64. Dois listeners no `document`: `click` (navega por SPA) e `mousedown` (só mata o auto-scroll do Chrome).
- **Como foi feito / decisões:**
  - Helper local `targetFor(el)`: sobe com `closest('a[href]')` e delega a decisão ao `spaLinkTarget` puro da F1. O `onClick` sai cedo em `isModifiedClick(e)` (deixa o navegador abrir guia/janela/download) e em `e.defaultPrevented`.
  - A base passada ao predicado é `window.location.href`, **não** `window.location.origin`: além de comparar o host, resolve corretamente um href relativo que uma tela de plugin venha a usar.
  - `download` é lido com `a.hasAttribute('download')`, nunca `a.download` (a propriedade devolve `''` tanto para ausente quanto para `<a download>`, sem distinguir).
  - Anti-duplicidade de histórico: só empurra quando `pathname+search+hash` difere do destino; o `PopStateEvent` é disparado sempre (idempotente, e é o par que todo call site já usa).
  - Opt-out: `data-no-spa` na âncora (item 2). `target` diferente de vazio/`_self` também escapa.
  - `mousedown` só chama `preventDefault` quando `e.button === 1` sobre link interno — não faz `stopPropagation`, então o fecha-menu-ao-clicar-fora do `GearMenu` (que também escuta `mousedown` no document) continua funcionando.
- **Problemas / pendências:** nenhum link passou a ser capturado indevidamente. **Auditoria completa das âncoras** (`grep -rn 'href=' web/static/js` + `storages/plugins/*/static/`):
  - Todas as âncoras EXTERNAS do core têm `target="_blank"` → recusadas: mensagem ([messageEntities.js:144](../web/static/js/services/messageEntities.js#L144)), CTA de sistema, saldo Techify ([GearMenu.js:128](../web/static/js/components/shell/GearMenu.js#L128)), [LowBalanceModal.js:63](../web/static/js/components/LowBalanceModal.js#L63), [SetupWizard.js:324](../web/static/js/components/SetupWizard.js#L324)/[:452](../web/static/js/components/SetupWizard.js#L452), [PluginsManager.js:270](../web/static/js/components/PluginsManager.js#L270).
  - ⚠️ O caso mais arriscado era [MediaContent.js:131](../web/static/js/components/contacts/MediaContent.js#L131) — `href='/' + media_path` é **mesmo origin** (`/statics/outbox/x.pdf`). Está salvo por `target="_blank"`. Idem o mapa em [:100](../web/static/js/components/contacts/MediaContent.js#L100). Se alguém remover esse `target` no futuro, o interceptor sequestraria o documento: é o motivo do `data-no-spa`.
  - As únicas âncoras INTERNAS que existem hoje (os `MenuItem` do GearMenu, incl. os de screen de plugin em [:114](../web/static/js/components/shell/GearMenu.js#L114); `melhorias/panel.js:118`/`:703`; `protocolos/extends.js:298`) **todas chamam `preventDefault()` antes**, então o interceptor sai em `defaultPrevented`. ⇒ **A F2 não muda comportamento nenhum hoje**: ela habilita os `<a href>` da F4 e é o que torna a F3 uma remoção de código.
- **Verificação:** `node --input-type=module --check` nos 3 arquivos; servidor dev (`:8090`) serve `/static/js/services/spaLink.js` (200, 4 exports) e `App.js` (200); `/` e `/costs` respondem 200. **Pendente de conferência no navegador pelo usuário** (clique simples sem recarga · Ctrl · meio), já que hoje não há âncora interna que exercite o caminho — a F4 abaixo cria a primeira.

---

### F5 — Grupo C: linhas e cards que não podem virar `<a>` (🟢) `[depende de: F1]`

**Objetivo:** dar Ctrl/⌘+clique e clique do meio às 5 superfícies onde um `<a>` não cabe — `<tr>` (HTML não permite envolver), card arrastável e a linha da sidebar (drag-drop + menu de contexto próprio + modo seleção).

**Mecanismo (o mesmo nos 5):** um par de props derivado de `spaLink.js` — `onClick` que, quando `shouldOpenInNewTab(e)`, chama `window.open(href, '_blank', 'noopener')` e **não** executa a ação normal; mais `onAuxClick` para o botão do meio. Sem isso, a ação normal (`openDetail`, `onSelect`, `handleSelect`) segue idêntica.

**Itens:**
1. `[paralelo]` **C5 — Execuções**: [Executions.js:1029-1036](../web/static/js/components/Executions.js#L1029-L1036), href `/executions/<id>` (o mesmo `detailPath` que o [CopyLinkButton da linha 466](../web/static/js/components/Executions.js#L466) já monta).
2. `[paralelo]` **C3 — lista de protocolos**: [protocolos_tab.js:1313-1317](../storages/plugins/protocolos/static/protocolos_tab.js#L1313-L1317), href `/protocolos?detail=<id>` (idêntico ao `copyLink` de [:1699](../storages/plugins/protocolos/static/protocolos_tab.js#L1699)).
3. `[paralelo]` **C2 — card do Kanban**: [protocolos_tab.js:1617-1628](../storages/plugins/protocolos/static/protocolos_tab.js#L1617-L1628). ⚠️ O `onClick` já tem o guard `draggedRef.current` (não abrir depois de arrastar) — a checagem de nova guia entra **antes** dele; e o `title` do card deve mencionar o atalho.
4. `[paralelo]` **C1 — linha da sidebar**: [ContactList.js:643-670](../web/static/js/components/contacts/ContactList.js#L643-L670), href `/conversations/<id>` (só quando `c.conversation_id != null`; linha sem atendimento não tem URL). ⚠️ **Em `selectionMode` o Ctrl+clique NÃO abre guia** — ali o clique alterna a seleção em massa; manter o comportamento atual.
5. `[sequencial, depende de: F6]` **C4 — linha de atendimento do protocolo** *(o pedido do print)*: [atendimentos_table.js:160-166](../storages/plugins/protocolos/static/atendimentos_table.js#L160-L166). Com a F6, a linha já carrega `anchor_message_id` e o href fica completo (`/conversations/<id>?message=<m>`) **antes** do clique — que é o que permite `window.open` dentro do gesto do usuário. Ver **P2** para o caminho sem a F6.
6. `[paralelo]` Repassar o href como `title`/`aria` não é necessário; **não** transformar as células em links (mudaria layout e criaria alvo de clique duplo).

⚠️ **Popup blocker:** `window.open` só é liberado quando chamado **de dentro** do handler do gesto. Nada de `await` antes — é exatamente por isso que a F6 existe.

**Pronto quando:** nas 5 superfícies, Ctrl+clique e clique do meio abrem a guia certa; o clique simples faz o que sempre fez; arrastar card do Kanban e soltar arquivo na linha da sidebar continuam funcionando; em modo seleção a sidebar não abre guia.

#### Status de execução — Fase 5
**Estado:** 🟨 PARCIAL — só as 2 superfícies do CORE (C1, C5). C2/C3/C4 ficaram de fora.
- **O que foi feito:**
  - **C5 — Execuções** ([Executions.js](../web/static/js/components/Executions.js), a `<tr>` da lista): href `/executions/<id>`.
  - **C1 — linha da sidebar** ([ContactList.js](../web/static/js/components/contacts/ContactList.js)): href `/conversations/<id>`, só quando `c.conversation_id != null`.
- **Como foi feito / decisões:**
  - Forma final: **três** props, não duas. Ao `onClick` (Ctrl/⌘) e `onAuxClick` (botão do meio) juntou-se `onMouseDown` com `preventDefault` para `e.button === 1`. O plano previa o par; o `mousedown` é necessário porque essas superfícies **não são âncora** — o `mousedown` do interceptor da F2 só cobre `<a>`, então sem isso o Chrome inicia o *auto-scroll* em cima do clique do meio. No C1 ele só cancela quando o clique do meio de fato faria algo (fora do modo seleção e com conversa).
  - `shouldOpenInNewTab` (estrito) e não `isModifiedClick` (largo): Shift/Alt caem na ação normal, como hoje. Emular "nova janela"/"baixar" aqui seria adivinhar.
  - `window.open(..., '_blank', 'noopener')` é chamado **direto dentro do handler**, sem nenhum `await` antes — é o que mantém o bloqueador de popup fora do caminho.
  - **C1 · modo seleção**: o guard é a primeira condição do `onClick` (`!selectionMode && …`). Em modo seleção, Ctrl+clique continua alternando a seleção em massa, intocado. As três props também não interferem no drop de arquivo (a linha é ALVO de drop vindo de fora, não fonte de drag) nem no `onContextMenu` próprio.
  - **C5** ganhou `title` com o atalho; a linha da sidebar **não** (já tem `title` de conteúdo e o espaço é disputado).
- **Problemas / pendências:** **C2, C3 e C4 não entraram** — vivem em `storages/plugins/protocolos/static/` e o plugin está sob edição de outra IA (plano 107) nesta rodada. A **F6** (que o C4 depende) idem. Fora isso, nenhum popup bloqueado e nenhum conflito com drag.
- **Verificação:** `node --input-type=module --check` nos 2 arquivos; `node --test` de todo o frontend do core → **538/538 pass**. Os gestos (clique simples / Ctrl / meio / arrastar / modo seleção) **pendentes de conferência no navegador** pelo usuário.

---

### F6 — `anchor_message_id` no payload do protocolo (🟢, plugin `protocolos`)

**Objetivo:** tirar o `fetch` do caminho do clique, para que a linha de atendimento tenha URL completa na renderização (pré-requisito do item C4 da F5).

**Itens:**
1. `[sequencial]` No detalhe do protocolo (o que alimenta `{protocolo, atendimentos}` — endpoint `GET /protocolos/{id}` em [routes.py](../storages/plugins/protocolos/routes.py)), acrescentar a cada atendimento o campo **aditivo** `anchor_message_id`, reusando a lógica já existente de [logic.py:3039-3066](../storages/plugins/protocolos/logic.py#L3039-L3066).
2. `[sequencial]` Preferir **uma** query para todos os ciclos do protocolo (join lateral / `DISTINCT ON` por `conversation_id`+`started_at`) em vez de N chamadas — o detalhe pode ter dezenas de ciclos.
3. `[paralelo]` `GET /atendimentos/{id}/anchor` ([routes.py:621-625](../storages/plugins/protocolos/routes.py#L621-L625)) **continua existindo** — é o caminho do clique simples de hoje e o fallback quando o campo novo vier ausente (plugin novo em core antigo, cache do navegador).
4. `[paralelo]` Campo ausente ⇒ href degrada para `/conversations/<conversation_id>` (sem o `?message=`), nunca para link quebrado.

**Pronto quando:** abrir o detalhe de um protocolo e ver `anchor_message_id` no payload de cada atendimento; o clique simples continua rolando até a mensagem exata.

#### Status de execução — Fase 6
**Estado:** ⏸️ ADIADA — não iniciada nesta rodada (só o core foi executado)
- **O que foi feito:** nada. Toda a fase é `routes.py`/`logic.py` do plugin `protocolos`, sob edição concorrente (plano 107).
- **Como foi feito / decisões:** a **P2** segue em aberto e continua com a mesma recomendação — **(a), com a F6** — porque o valor da linha é cair no trecho certo de uma conversa longa.
- **Problemas / pendências:** bloqueia o item **C4** da F5 (a linha do print do usuário), que por isso também ficou fora.
- **Verificação:** —

---

### F3 — Grupo A: os plugins param de cancelar o clique (🟢) `[depende de: F2]` — **é o que resolve o pedido do menu**

**Objetivo:** devolver ao item "Protocolos" da engrenagem (D2) e aos links do `melhorias` o comportamento de link. Com a F2 no lugar, isso é **remover código**, não acrescentar.

**Itens:**
1. `[paralelo]` **A1** [extends.js:297-303](../storages/plugins/protocolos/static/extends.js#L297-L303): remover o `onClick` que faz `preventDefault()`. O `href="/protocolos"` já está lá; o clique simples passa a ser tratado pelo interceptor da F2. Manter o `close()` do menu — via `onClick` **sem** `preventDefault`, ou deixando o menu fechar pela re-renderização da tab.
2. `[paralelo]` **A2/A3** [panel.js:113-122](../storages/plugins/melhorias/static/panel.js#L113-L122) e [:702-707](../storages/plugins/melhorias/static/panel.js#L702-L707): `navConversation` ([:138-146](../storages/plugins/melhorias/static/panel.js#L138-L146)) sai de cena ou passa a sair cedo em clique modificado.
3. `[sequencial]` ⚠️ **Nenhum dos dois plugins importa módulo novo do core** (ver **P1**): num core anterior a este plano (sem F2), o clique simples vira **recarga de página inteira** — mais lento, porém correto. Degradação aceitável e explícita.

**Pronto quando:** na engrenagem, **Ctrl+clique em "Protocolos" abre nova guia** (paridade com "Melhorias"/"Vendas IA"); clique simples continua trocando de tela sem recarregar; o menu fecha como antes. Idem para os dois links do painel de Melhorias.

#### Status de execução — Fase 3
**Estado:** ⏸️ ADIADA — não iniciada nesta rodada (só o core foi executado)
- **O que foi feito:** nada. A1/A2/A3 são edições em `storages/plugins/protocolos/` e `storages/plugins/melhorias/`, e o `protocolos` está sob edição concorrente de outra IA (plano 107). Mexer nele agora arrisca conflito e, pior, sobrescrita silenciosa na hora de reconstruir o zip.
- **Como foi feito / decisões:** —
- **Problemas / pendências:** **a dependência já está satisfeita**: a F2 está no lugar, então a F3 virou o que o plano previu — **remoção de código**. Nos três pontos ([extends.js:298-300](../storages/plugins/protocolos/static/extends.js#L298-L300), `melhorias/panel.js:118` e `:703` via `navConversation` em [:136-145](../storages/plugins/melhorias/static/panel.js#L136-L145)) o `<a href>` já existe e o único obstáculo é o `e.preventDefault()` incondicional. Basta tirá-lo (mantendo o `close()` do menu na A1, sem `preventDefault`) e o interceptor do shell assume o clique simples.
- **Verificação:** —

---

### F4 — Grupo B: botões que na verdade são links (🟢) `[depende de: F2]`

**Objetivo:** os pontos que apenas *parecem* botão e cujo efeito é navegar viram `<a href>` de verdade — e ganham nova guia de graça pela F2 (inclusive "Abrir link em nova guia" do botão direito, que o Grupo C não tem).

**Itens:**
1. `[paralelo]` **B1** "Ver detalhes" ([ContactsListScreen.js:281-286](../web/static/js/components/ContactsListScreen.js#L281-L286)): `<a href="/contacts/<id>">` mantendo a aparência atual; o `onClick` continua chamando `openDetail` (a F2 é quem faz o `pushState`, então cuidar para não empurrar a URL duas vezes — `push({id})` em [:546-549](../web/static/js/components/ContactsListScreen.js#L546-L549) já é idempotente por `window.location.pathname !== path`).
2. `[paralelo]` **B3/B4** Agendamentos de retorno ([ScheduleTabs.js:334-338](../storages/plugins/agendamento_retorno/static/ScheduleTabs.js#L334-L338) e [:406-410](../storages/plugins/agendamento_retorno/static/ScheduleTabs.js#L406-L410)): `<button>` → `<a href="/conversations/<id>">` com o mesmo ícone/estilo; `goToConversation` ([:69-74](../storages/plugins/agendamento_retorno/static/ScheduleTabs.js#L69-L74)) só precisa continuar fechando o modal.
3. `[paralelo]` **B5** Melhorias aba IA ([ai_section.js:95](../storages/plugins/melhorias/static/ai_section.js#L95)): `<a href="/melhorias?detail=<id>">`.
4. `[paralelo]` **B2** "Iniciar conversa" ([ContactsListScreen.js:294-300](../web/static/js/components/ContactsListScreen.js#L294-L300)): ⚠️ o destino é resolvido por `fetch` ([:585-595](../web/static/js/components/ContactsListScreen.js#L585-L595)) — **mesmo problema da C4**. Ou fica de fora, ou o `conversation_id` passa a vir na listagem de contatos. Ver **P3**.
5. `[paralelo, opcional]` **B6** sub-abas de IA/Usuários/Atributos ([AgentEngine.js:133-142](../web/static/js/components/ai/AgentEngine.js#L133-L142), [UsersManager.js:330-338](../web/static/js/components/UsersManager.js#L330-L338), [CustomAttributesManager.js:339-347](../web/static/js/components/CustomAttributesManager.js#L339-L347)): valor baixo; executar só se sobrar tempo.

**Pronto quando:** cada item convertido abre em nova guia por Ctrl, meio **e** pelo menu do botão direito; o clique simples se comporta exatamente como hoje; nada mudou visualmente (o `<a>` herda as mesmas classes; conferir no **modo escuro**).

#### Status de execução — Fase 4
**Estado:** 🟨 PARCIAL — entraram **B1** e **B6** (os itens do core). B3/B4/B5 são de plugin; B2 segue adiado.
- **O que foi feito:**
  - **B1** "Ver detalhes" ([ContactsListScreen.js](../web/static/js/components/ContactsListScreen.js)): `<button>` → `<a href="/contacts/<id>">`, `onClick` inalterado.
  - **B6** sub-abas: [AgentEngine.js](../web/static/js/components/ai/AgentEngine.js) (`/ai/<sub>`), [UsersManager.js](../web/static/js/components/UsersManager.js) (`/users` · `/users/roles`), [CustomAttributesManager.js](../web/static/js/components/CustomAttributesManager.js) (`/custom-attributes/<escopo>`). O href reusa **a mesma expressão** `entityPath(...)`/`basePath(...)` que o `onClick` já calculava — zero fonte de verdade nova, e os três arquivos já importavam esses helpers.
- **Como foi feito / decisões:**
  - **B6 entrou** (o plano marcava como opcional): o custo real foi trocar a tag e acrescentar `no-underline`, porque o destino já estava calculado ali.
  - **B2 ficou de fora**, conforme a recomendação da **P3** — o destino é resolvido por `fetch` (`getContactConversation`), o mesmo problema assíncrono da C4.
  - **B3/B4/B5** são de `agendamento_retorno`/`melhorias`, fora do escopo desta rodada (plugins).
  - **Sem duplicidade de histórico** (o risco listado em §5). Verificado no código, não por suposição: `push` do [useDeepLink.js:133-140](../web/static/js/hooks/useDeepLink.js#L133-L140) só empurra quando `window.location.pathname !== path`, e o interceptor da F2 tem o mesmo guard sobre `pathname+search+hash`. O `onClick` roda primeiro (é handler do próprio elemento) e já deixa a URL no destino ⇒ o interceptor pula o `pushState` e só dispara o `popstate`. Esse `popstate` extra é inerte: `useDeepLink` carimba `appliedRef.current = selKey(norm)` **dentro do `push`**, então o efeito de resolução sai em `k === appliedRef.current` e não reabre nada.
  - **Aparência:** `no-underline` acrescentado em todos (o `<a>` do painel sublinha por padrão — é por isso que o `MenuItem` do GearMenu já o usa). Na B1 ficou `no-underline hover:underline`, reproduzindo o `hover:underline` que o `<button>` tinha. Os três contêineres de sub-aba são `flex`, então o `<a>` é blockificado como flex item e mantém `px-4 py-2`/`pb-2` idênticos.
- **Problemas / pendências:** conferência visual nos dois temas pendente com o usuário. Todas as classes preservadas verbatim + `no-underline`, então não há cor nova para o modo escuro julgar.
- **Verificação:** `node --input-type=module --check` nos 4 arquivos; parse real dos templates `htm` (comentário multi-linha contendo `<a href>`, `⌘` e aspas) contra o `htm.min.js` vendorizado, confirmando que o `<a>` sai com o href certo; `node --test` do frontend do core → **538/538 pass**.

---

### F7 — Testes, documentação e republicação dos plugins (🔴 sozinha)

**Objetivo:** travar a regra e não deixar o próximo refactor "limpar" o interceptor.

**Itens:**
1. `[sequencial]` `node --test` em `web/static/js/services/spaLink.test.js` (F1) e nos módulos puros já existentes de `services/` (28 suítes) — nenhuma regressão.
2. `[paralelo]` Simplificar o `MenuItem` da engrenagem ([GearMenu.js:15-33](../web/static/js/components/shell/GearMenu.js#L15-L33)) para consumir `isModifiedClick` de `spaLink.js` em vez da condição inline (o guard vira **um** ponto no código). Comportamento idêntico.
3. `[paralelo]` `venv/bin/python -m pytest tests/integration tests/contracts` no Postgres de teste (`WHATSBOT_TEST_DB_URL`) — o plano é frontend, mas a F6 mexe numa rota de plugin.
4. `[paralelo]` Runner do plugin no repositório externo: `python3 scripts/test_plugins.py protocolos` (e `melhorias`, `agendamento_retorno` se tiverem suíte).
5. `[sequencial]` **Republicar os plugins alterados** no repositório `whatsbot-pro-plugins` (fonte em `plugins/<id>/src/`, ZIP por `scripts/build_plugins.py <id>`) e **instalar o zip no ambiente local antes de commitar** — a cópia que roda é `storages/plugins/<id>/`, não a do git.
6. `[sequencial]` `CLAUDE.md`: seção nova curta — *"Links internos e nova guia"* — dizendo que o clique interno é interceptado no shell, que **toda navegação nova deve ser um `<a href>`** (e o que fazer quando não puder ser), e apontando `services/spaLink.js`.

**Pronto quando:** suítes verdes, `CLAUDE.md` atualizado, zips reconstruídos + instalados, e o `MenuItem` sem regra duplicada.

#### Status de execução — Fase 7
**Estado:** 🟨 PARCIAL — só o item 1 (testes JS). Os itens 2–6 ficaram para o fechamento do plano.
- **O que foi feito:** item 1 — `node --test` em `spaLink.test.js` e em **todas** as suítes puras já existentes.
- **Como foi feito / decisões:** —
- **Problemas / pendências:**
  - item 2 (simplificar o `MenuItem` do [GearMenu.js:15-33](../web/static/js/components/shell/GearMenu.js#L15-L33) para consumir `isModifiedClick`): **não feito**. É seguro e independente, mas o plano o coloca na F7 e ele não desbloqueia nada — fica para a rodada de fechamento.
  - itens 3 e 4 (pytest e runner de plugin): **não rodados**. Nada de Python foi tocado nesta rodada (a F6, a única fase com backend, não entrou).
  - item 5 (**republicar os zips**): **não feito, e é o ponto sensível** — nenhum arquivo de plugin foi tocado, então não há zip a reconstruir. Quando as fases de plugin forem executadas, elas precisam sair **depois** que o plano 107 terminar no `protocolos`, sob pena de dois builds a partir de fontes divergentes se sobrescreverem em silêncio.
  - item 6 (seção "Links internos e nova guia" no `CLAUDE.md`): **não feito** — a regra só fica verdadeira de ponta a ponta quando a F3 sair; documentar antes prometeria um comportamento que os plugins ainda cancelam.
- **Verificação:** `node --test $(find web/static/js -name '*.test.js')` → **538/538 pass, 0 fail** (29 suítes; a de `spaLink` sozinha: 19/19).

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Interceptor global (F2) | Capturar clique que não é navegação (link de mensagem, mídia, saldo Techify, download) | Predicado **puro e testado** (`spaLinkTarget`); recusa `target`, `download`, host externo, `mailto:`/`tel:`; respeita `defaultPrevented`; opt-out `data-no-spa` |
| Interceptor global (F2) | Um handler existente que já faz `preventDefault` deixar de funcionar | O interceptor é *bubbling* no `document` — roda **depois** dos `onClick` e sai cedo em `e.defaultPrevented` |
| Clique do meio | Chrome inicia *auto-scroll* em vez de abrir guia | `mousedown` com `preventDefault` **apenas** para `button === 1` sobre link interno (F2 item 3) |
| `<a>` sobre elemento arrastável (C1, C2) | Anchor é nativamente arrastável e brigaria com o drag-drop de arquivo / de card | Por isso o Grupo C **não** vira anchor — usa `window.open` no handler (F5) |
| `window.open` depois de `await` (C4, B2) | Bloqueado pelo *popup blocker* — a guia simplesmente não abre | URL resolvida **antes** do clique (F6); sem ela, degradar para `/conversations/<id>` sem `?message=` (**P2**) |
| Modo seleção da sidebar | Ctrl+clique abrir guia enquanto o usuário seleciona conversas em massa | Guard explícito: em `selectionMode`, comportamento atual intocado (F5 item 4) |
| Menu de contexto próprio (C1) | A linha da sidebar cancela o menu do navegador ⇒ "Abrir em nova guia" do botão direito **não** existe ali | Aceito e documentado: nessa superfície valem Ctrl e clique do meio. Não sequestrar o menu de contexto do app |
| Duplicidade de `pushState` (F4) | Componente empurra a URL **e** o interceptor também ⇒ duas entradas no histórico | Todos os call sites já testam `window.location.pathname !== path`; conferir por tela ao converter |
| Plugin em core anterior | Plugin sem `preventDefault` num core sem F2 ⇒ clique simples recarrega a página | Degradação **explícita** e aceitável (F3 item 3). Nenhum plugin importa módulo novo do core — ver **P1** |
| Zip vs. cópia instalada | Commitar o plugin sem instalar entrega versão errada ao usuário | F7 item 5: reconstruir o ZIP e **instalar antes de commitar** |
| Modo escuro | `<button>` virando `<a>` pode perder cor/sublinhado herdados | Manter as mesmas classes `wa-*` + `no-underline` onde aplicável; conferir nos dois temas (F4) |
| Segredo na URL | Nada aqui coloca dado sensível em URL | Todos os destinos já são deep-links existentes do plano 24 |

---

## 6. Perguntas em aberto

**P1 — Como os plugins ganham o comportamento: helper compartilhado do core ou nada?**
✅ **DECIDIDO (2026-08-05): nada — os plugins só param de cancelar o clique (F3) e usam markup próprio (F5).**
Contexto: (a) exportar `spaLink.js` e o plugin importar por URL absoluta (`/static/js/services/spaLink.js`) — há precedente ([extends.js:20](../storages/plugins/protocolos/static/extends.js#L20) importa `api.js`), mas um import **não-defensivo** de módulo novo faz o plugin **não carregar** num core anterior (a armadilha "core antes do zip" do `CLAUDE.md`); (b) expor em `api.services` — **não alcança o `protocolos`**, que não declara `plugin_services_version` e recebe o adapter 1.x; (c) o plugin não importa nada — com a F2, um `<a href>` cru já basta, e as 3 linhas de guard do Grupo C são baratas de duplicar.
**Escolha: (c).** É a leitura literal da regra "tudo que puder ir para o plugin vai só para o plugin".

**P2 — A linha de atendimento (C4) precisa mesmo da F6?**
⏸️ **DECIDIR ANTES DA F5 item 5.**
(a) **Com a F6**: o href sai completo (`?message=<m>`), a guia nova cai na mensagem exata — paridade total com o clique simples. Custo: uma query batelada no detalhe do protocolo.
(b) **Sem a F6**: href `/conversations/<id>` apenas; a guia nova abre a conversa **no fim**, não no ponto do ciclo. Custo zero, e o clique simples continua preciso.
(c) Buscar a âncora no clique modificado — **inviável**: `window.open` depois de `await` é bloqueado.
**Recomendação:** (a). O valor da linha é justamente cair no trecho certo de uma conversa longa; (b) entrega meia funcionalidade. Mas (b) é um fallback legítimo se a F6 revelar custo alto em protocolo com muitos ciclos.

**P3 — O botão "Iniciar conversa" da tela Contatos (B2) entra?**
⏸️ **ADIADO.** Tem o mesmo problema assíncrono da C4, mas a resolução é do **core** ([:585-595](../web/static/js/components/ContactsListScreen.js#L585-L595) chama `getContactConversation`). Opções: (a) o payload de `list_contacts` passar a trazer `conversation_id`; (b) deixar de fora. Contra (a): o hub já resolve isso por outro caminho e o campo teria de ser mantido fresco. **Recomendação:** deixar de fora da primeira execução e reavaliar se o usuário pedir.

**P4 — O interceptor deve virar comportamento documentado do contrato de plugin?**
⏸️ **ADIADO até a F7.** Com a F2, "renderize `<a href>` e funciona" passa a ser verdade para telas de plugin. Isso é uma promessa de API — se for documentado no `CLAUDE.md`, vira contrato que não se pode remover sem bump. **Recomendação:** documentar como comportamento do **core** (o que ele faz), não como garantia versionada, até haver segundo consumidor.

---

## 7. Apêndice — arquivos-chave

| Camada | Arquivo | Papel |
|---|---|---|
| Core · puro | [web/static/js/services/spaLink.js](../web/static/js/services/spaLink.js) | **cria** — predicados de clique e alvo de link (F1) |
| Core · puro | `web/static/js/services/spaLink.test.js` | **cria** — `node --test` (F1) |
| Core · shell | [web/static/js/components/shell/App.js:183-215](../web/static/js/components/shell/App.js#L183-L215) | **edita** — interceptor delegado + `mousedown` do botão do meio (F2) |
| Core · shell | [web/static/js/components/shell/GearMenu.js:15-33](../web/static/js/components/shell/GearMenu.js#L15-L33) | **edita (F7)** — consumir `isModifiedClick`; comportamento idêntico |
| Core · telas | [web/static/js/components/Executions.js:1029-1036](../web/static/js/components/Executions.js#L1029-L1036) | **edita** — linha da tabela (C5) |
| Core · telas | [web/static/js/components/contacts/ContactList.js:643-670](../web/static/js/components/contacts/ContactList.js#L643-L670) | **edita** — linha da sidebar (C1) |
| Core · telas | [web/static/js/components/ContactsListScreen.js:281-300](../web/static/js/components/ContactsListScreen.js#L281-L300) | **edita** — "Ver detalhes" (B1) e, se a P3 mudar, "Iniciar conversa" (B2) |
| Core · telas (opcional) | [AgentEngine.js:133](../web/static/js/components/ai/AgentEngine.js#L133), [UsersManager.js:330](../web/static/js/components/UsersManager.js#L330), [CustomAttributesManager.js:339](../web/static/js/components/CustomAttributesManager.js#L339) | **edita (B6, opcional)** — sub-abas |
| Plugin `protocolos` | [static/extends.js:297-303](../storages/plugins/protocolos/static/extends.js#L297-L303) | **edita** — item da engrenagem (A1) — *a regressão do D2* |
| Plugin `protocolos` | [static/protocolos_tab.js:1313](../storages/plugins/protocolos/static/protocolos_tab.js#L1313), [:1617](../storages/plugins/protocolos/static/protocolos_tab.js#L1617), [:1733](../storages/plugins/protocolos/static/protocolos_tab.js#L1733) | **edita** — lista (C3), card (C2), `openAtend` (C4) |
| Plugin `protocolos` | [static/atendimentos_table.js:160-166](../storages/plugins/protocolos/static/atendimentos_table.js#L160-L166) | **edita** — a linha do print (C4) |
| Plugin `protocolos` | [routes.py](../storages/plugins/protocolos/routes.py), [logic.py:3039-3066](../storages/plugins/protocolos/logic.py#L3039-L3066) | **edita** — `anchor_message_id` no payload (F6) |
| Plugin `melhorias` | [static/panel.js:113-146](../storages/plugins/melhorias/static/panel.js#L113-L146), [:702-707](../storages/plugins/melhorias/static/panel.js#L702-L707) | **edita** — A2/A3 |
| Plugin `melhorias` | [static/ai_section.js:16-20](../storages/plugins/melhorias/static/ai_section.js#L16-L20), [:95](../storages/plugins/melhorias/static/ai_section.js#L95) | **edita** — B5 |
| Plugin `agendamento_retorno` | [static/ScheduleTabs.js:69-74](../storages/plugins/agendamento_retorno/static/ScheduleTabs.js#L69-L74), [:334](../storages/plugins/agendamento_retorno/static/ScheduleTabs.js#L334), [:406](../storages/plugins/agendamento_retorno/static/ScheduleTabs.js#L406) | **edita** — B3/B4 |
| Referência (não mexer) | [routing.js](../web/static/js/components/shell/routing.js), [useDeepLink.js](../web/static/js/hooks/useDeepLink.js), [copyDeepLink.js](../web/static/js/utils/copyDeepLink.js) | as URLs de destino **já existem** — é daqui que saem os `href` |
| Doc | `CLAUDE.md` → seção nova "Links internos e nova guia" | registrar a regra para telas futuras (F7) |

---

## 8. Checklist de verificação

Legenda: ✅ feito · 🖥️ pronto no código, **falta o usuário conferir no navegador** · ⏸️ depende de fase de plugin adiada.

- [ ] ⏸️ **Engrenagem → "Protocolos"**: Ctrl+clique abre nova guia (paridade com "Melhorias" no mesmo menu) — *depende da F3*
- [ ] ⏸️ **Linha "Atendimentos" no detalhe do protocolo** (o print do usuário): Ctrl+clique e clique do meio abrem a conversa em nova guia — *depende da F6 + F5·C4*
- [ ] 🖥️ Clique **simples** em todos os pontos convertidos continua navegando na MESMA guia, **sem recarregar a página**
- [ ] 🖥️ Clique do meio abre guia sem disparar o *auto-scroll* do Chrome
- [ ] 🖥️ Botão direito → "Abrir link em nova guia" funciona nos itens do Grupo B (é o bônus de virarem `<a>` de verdade)
- [x] ✅ Link **externo** (mensagem com URL, mídia, "Saldo e Recarregar") continua abrindo fora, intocado — *auditado arquivo a arquivo na F2; todos têm `target="_blank"`, recusados pelo predicado*
- [ ] 🖥️ Arrastar card entre colunas do Kanban continua funcionando (⏸️ o card em si é F5·C2); soltar arquivo na linha da sidebar também
- [ ] 🖥️ Em **modo seleção** na sidebar, Ctrl+clique NÃO abre guia (segue alternando a seleção) — *guard é a 1ª condição do `onClick`*
- [ ] 🖥️ Voltar/avançar do navegador continua correto em todas as telas tocadas
- [x] ✅ Nenhuma tela ganhou entrada dupla no histórico (um clique = um passo no "voltar") — *guard de path no interceptor + `push` idempotente do `useDeepLink`; rastreado na F4*
- [ ] 🖥️ Telas tocadas legíveis no **modo escuro** (`<button>` que virou `<a>` manteve cor e ausência de sublinhado) — *classes preservadas verbatim + `no-underline`; nenhuma cor nova*
- [x] ✅ `node --test web/static/js/services/spaLink.test.js` verde — **19/19**
- [x] ✅ `node --test` das demais suítes puras de `services/` sem regressão — **538/538** em todo o frontend do core
- [ ] ⏸️ `venv/bin/python -m pytest tests/integration tests/contracts` verde no Postgres (`WHATSBOT_TEST_DB_URL`) — *nada de Python foi tocado; só faz sentido com a F6*
- [ ] ⏸️ `python3 scripts/test_plugins.py protocolos` verde no repositório de plugins
- [ ] ⏸️ ZIPs de `protocolos` / `melhorias` / `agendamento_retorno` reconstruídos **e instalados** em `storages/plugins/` antes do commit
- [ ] ⏸️ `CLAUDE.md` documenta a regra "navegação nova = `<a href>`" e o porquê do interceptor — *F7 item 6; só vira verdade com a F3*
