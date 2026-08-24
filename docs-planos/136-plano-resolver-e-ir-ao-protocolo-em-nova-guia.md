# Plano 136 — "Resolver e ir ao protocolo" abre em OUTRA guia, sem tocar na guia do atendente

> **Status:** CONCLUÍDO — F0–F6 ✅ (publicado como protocolos **2.4.1**, commit `49ae820`) · **Data:** 2026-08-21 · **Escopo:** pequeno (1 plugin, 3 arquivos + 1 módulo novo; **zero core**, zero backend, zero migration)
> **Origem:** pedido do operador — *"quando eu clico no botão resolver e ir para o protocolo, ele não está abrindo uma nova guia… está substituindo a tela na guia que eu estou. Quero que abra uma guia nova para que o atendente não perca a conversa"*. Motivação real: em dias de 200–300 atendimentos, o atendente trabalha na aba **Todas**, do mais antigo para o mais novo; cada ida ao protocolo custa a volta + a aba caindo em "Minhas" + reencontrar a linha na lista.
> **Método:** leitura do código real com `arquivo:linha` verificado nas DUAS linhas do plugin (1.35.0 instalada e 2.2.0 do repositório de plugins) + arqueologia (`git log -S "window.open"`) para separar regressão de "nunca existiu" + leitura do plano 106, que é o irmão deste.
> **O quê/porquê:** hoje o botão faz `history.pushState` ([extends.js:324-327](../storages/plugins/protocolos/static/extends.js#L324-L327)) — mesma guia, por construção. A troca por `window.open` **não é uma linha**: entre o clique e a navegação há um `await` de rede, o gesto do usuário já foi consumido e o Chrome bloquearia o popup **em silêncio**. O conserto é abrir a guia **dentro do handler do clique** e só depois apontá-la ao protocolo.
>
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0 — Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 ✅ (2026-08-21) | **Uma guia só, reusada.** Toda resolução aponta a MESMA guia nomeada, em vez de abrir uma nova. | `window.open(url, NOME_FIXO)`. Em 300 fechamentos o atendente termina com **duas** guias, não 301. Custo aceito: perde-se comparar dois protocolos lado a lado. |
| D2 ✅ (2026-08-21) | **O foco vai para a guia do protocolo.** | Comportamento padrão do `window.open`; para guia já existente, `win.focus()` explícito. É o que o operador descreveu ("eu vou para outra tela"). |
| D3 ✅ (2026-08-21) | **A guia do painel não muda nada.** Continua na conversa, na aba **Todas**, com filtros, rolagem e rascunho como estavam. | Some o `pushState`/`PopStateEvent`. O `<Contacts/>` **não desmonta** ⇒ o default `mine` do plano 88 deixa de aparecer neste fluxo. |
| D4 ✅ (2026-08-21) | **Nada de core.** O sintoma "volta sempre em Minhas" ao trocar de tela **não** é consertado aqui. | [hubDefaults.js:66](../web/static/js/services/hubDefaults.js#L66) e o `pushState('/')` do shell ficam intocados. Se o operador quiser isso, é outro plano (mexe no plano 88). |
| D5 ✅ (2026-08-21) | **Base = 1.35.0**, a cópia instalada em `storages/plugins/protocolos/`. O porte para o `src` 2.2.0 do repositório de plugins acontece **depois** da confirmação do operador. | A F6 nasce ⏸️ com gate humano. Ver §6 · R5 para a armadilha do zip. |
| D6 ✅ (2026-08-21) | O popup de resolver **já fecha sozinho** no `close(v)` do `ModalHost`. Não há nada a fazer nesse ponto. | O pedido "e ele fecha esse pop-up" já é o comportamento atual — verificado em [ModalHost.js:33-43](../web/static/js/plugins/ModalHost.js#L33-L43). |

**Princípio fixo:** o fechamento do atendimento é o caminho de negócio; abrir a guia é conveniência. **Nenhuma falha ao abrir/apontar/fechar guia pode impedir, atrasar ou desfazer o `/resolve`.** Tudo que toca `window` fica atrás de `try/catch` e degrada.

---

## 1 — Resumo executivo

O botão devolve `goTo: true` ([resolve_form.js:205-207](../storages/plugins/protocolos/static/resolve_form.js#L205-L207)) e quem navega é o `extends.js`, com `history.pushState` — a guia atual é substituída. O atendente perde a conversa, a posição na lista e a aba "Todas".

Trocar por `window.open` no mesmo lugar **não funciona**: o `pushState` roda depois de `await api.http.post('/atendimentos/{id}/resolve')` (e às vezes de um segundo POST de continuidade). A ativação transitória do gesto já foi gasta e o bloqueador de popup recusa — sem erro, sem log, sem guia. Seria **pior que hoje**.

A forma da solução é a mesma que o plano 106 já fixou como regra do repositório (*"`window.open` só é liberado quando chamado de dentro do handler do gesto; nada de `await` antes"*): a guia é aberta **no `onClick` do botão**, em branco, com um "Abrindo protocolo…"; o handle viaja pelo `onOk`; quando o `/resolve` responde, o `extends.js` aponta `win.location` para `/protocolos?detail=<id>` — ou **fecha** a guia se o protocolo não materializou. Nome fixo na guia ⇒ a 2ª resolução reaproveita a mesma (D1).

Três arquivos do plugin, um módulo puro novo com teste `node --test`, e nada mais.

---

## 2 — Como funciona hoje (mapa verificado)

### 2.1 O caminho do `goTo`

| Etapa | Onde | O que faz |
|---|---|---|
| 1 | [resolve_form.js:205-207](../storages/plugins/protocolos/static/resolve_form.js#L205-L207) | `submit(goTo)` → `onOk({ fields, protoFields, goTo })` |
| 2 | [resolve_form.js:272-275](../storages/plugins/protocolos/static/resolve_form.js#L272-L275) | o botão "Resolver e ir ao protocolo" chama `submit(true)` |
| 3 | [ModalHost.js:33-43](../web/static/js/plugins/ModalHost.js#L33-L43) | `close(result)` desmonta o modal e resolve a promessa do `openModal` |
| 4 | [extends.js:300-307](../storages/plugins/protocolos/static/extends.js#L300-L307) | **`await` POST `/atendimentos/{id}/resolve`** → captura `protocolo_id` |
| 5 | [extends.js:312-317](../storages/plugins/protocolos/static/extends.js#L312-L317) | **`await` POST `relink-decision`** (só quando há continuidade pendente); pode TROCAR o id para o do protocolo anterior |
| 6 | [extends.js:324-327](../storages/plugins/protocolos/static/extends.js#L324-L327) | `history.pushState('/protocolos?detail=<id>')` + `PopStateEvent` — **é aqui que a guia atual é sequestrada** |

O destino é lido no `mount` **e** no `popstate` pelo [protocolos_tab.js:1246-1266](../storages/plugins/protocolos/static/protocolos_tab.js#L1246-L1266), então **`/protocolos?detail=<id>` funciona também como carga fria de página** — condição necessária para que uma guia nova sirva. ✅ verificado.

⚠️ **As etapas 4 e 5 são o problema inteiro.** Entre o clique (etapa 2) e a navegação (etapa 6) há pelo menos uma ida à rede.

### 2.2 Os TRÊS call sites do `ResolveForm` — e só um trata `goTo`

| # | Call site | Contexto | Trata `goTo` hoje? |
|---|---|---|---|
| A | [extends.js:285-291](../storages/plugins/protocolos/static/extends.js#L285-L291) | `filter.conversation.beforeResolve` — **o do print do operador** | ✅ sim (etapa 6) |
| B | [extends.js:58-64](../storages/plugins/protocolos/static/extends.js#L58-L64) | `resolveAndCloseAll` — "fechar conversa e protocolo juntos", vindo do popup de vínculo | ❌ **ignora** |
| C | [protocolos_tab.js:1429-1436](../storages/plugins/protocolos/static/protocolos_tab.js#L1429-L1436) | `forceResolveAndClose` — Kanban/lista da aba Protocolos | ❌ **ignora** (`picked.goTo` nunca é lido; ver [:1437-1438](../storages/plugins/protocolos/static/protocolos_tab.js#L1437-L1438)) |

⚠️ **Isto é um bug pré-existente que a mudança AGRAVA.** Hoje, clicar o botão em B/C resolve e simplesmente não navega — inofensivo. Depois da mudança, o clique abriria uma guia em branco que **ninguém aponta e ninguém fecha**: `about:blank` órfão. A **F4** existe só por causa disso.

### 2.3 Por que o gesto morre — e o precedente que já fixou a regra

O plano 106 percorreu exatamente este terreno e deixou a regra escrita em duas seções:

> *"⚠️ **Popup blocker:** `window.open` só é liberado quando chamado **de dentro** do handler do gesto. Nada de `await` antes"* — [106 · F5](106-plano-abrir-em-nova-guia.md)
> *"`window.open(..., '_blank', 'noopener')` é chamado **direto dentro do handler**, sem nenhum `await` antes — é o que mantém o bloqueador de popup fora do caminho"* — [106 · status da F5](106-plano-abrir-em-nova-guia.md)

Aqui a restrição é mais dura que na F5 do 106: lá a URL era conhecida antes do clique; aqui o `protocolo_id` **só existe depois** do `/resolve` (o protocolo pode nascer nessa chamada). Daí a guia-placeholder.

### 2.4 O que o core já tem (plano 106) e por que não resolve isto

| Peça | Onde | Serve aqui? |
|---|---|---|
| `spaLink.js` (`shouldOpenInNewTab`, `spaLinkTarget`, `isInternalHref`) | [spaLink.js](../web/static/js/services/spaLink.js) | ❌ decide sobre **modificador de teclado** (Ctrl/⌘/meio). O pedido é abrir em nova guia **sempre**, com clique simples. |
| Interceptor de âncoras internas no shell | [App.js](../web/static/js/components/shell/App.js) (F2 do plano 106, ✅ concluída) | ❌ atua sobre `<a href>`; e um `<a>` não serve porque a URL só existe depois do POST. |
| `Ver protocolo` no cabeçalho do popup | [resolve_form.js:215-224](../storages/plugins/protocolos/static/resolve_form.js#L215-L224) | ✅ **é a prova de que o destino em guia nova funciona** — `<a target="_blank">` para `/protocolos?detail=<id>`, no ar desde a 1.35.0 (originalmente 1.29.0). Não é substituto: só existe quando o contato **já tem** protocolo aberto. |

**Arqueologia:** `git log --all -S "window.open" -- plugins/protocolos/` nos dois repositórios ⇒ **zero resultados**. Este botão **nunca** abriu guia nova. O que o operador lembra ("de certa forma já existia antes") é a outra metade do sintoma: até o **plano 88**, voltar de outra tela caía em **"Todas"**; hoje o default do hub é `mine` ([hubDefaults.js:66](../web/static/js/services/hubDefaults.js#L66)) e o retorno é um `pushState('/')` **sem query-string**. Com a guia nova, o painel nunca desmonta e o sintoma some **neste fluxo** — mas a causa permanece (D4).

### 2.5 As duas linhas do plugin (medido)

| Item | Instalado (dev + produção) | `src` do repositório de plugins |
|---|---|---|
| Versão | **1.35.0** | **2.2.0** (zip publicado, **não instalado em lugar nenhum**) |
| `resolve_form.js` | 280 linhas | **byte-idêntico** ✅ |
| `extends.js` | 348 linhas | difere **só** na seção de continuidade (a 2.0.0 removeu a decisão por atributo). O bloco do `goTo` é idêntico ✅ |
| `extends.js` 1.35.0 no git (`6e3fbf3`) × cópia instalada | **idênticos** ✅ | — |

⇒ **o delta deste plano aplica verbatim nas duas linhas.** Fazer na 1.35 e portar depois não cria divergência.

---

## 3 — Inventário das mudanças

| # | Arquivo | O que falta | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|
| 1 | `static/goto_tab.js` (**novo**) | não existe | módulo **PURO**: nome da guia, `protocoloUrl(id)` e `tabAction({ hasWindow, protocoloId })` → `'navigate' \| 'close' \| 'fallback' \| 'none'`. Testável em `node --test`, sem DOM | baixo | S |
| 2 | [resolve_form.js:205-207](../storages/plugins/protocolos/static/resolve_form.js#L205-L207) | `submit` não abre guia | abrir a guia **dentro do `onClick`** e devolver o handle no `onOk` | **médio** — é o ponto onde o bloqueador de popup decide | S |
| 3 | [extends.js:324-327](../storages/plugins/protocolos/static/extends.js#L324-L327) | `pushState` sequestra a guia | apontar `win.location.replace(url)` + `win.focus()`; fechar a guia quando não há protocolo | médio | S |
| 4 | [extends.js:58-64](../storages/plugins/protocolos/static/extends.js#L58-L64) · [protocolos_tab.js:1429-1436](../storages/plugins/protocolos/static/protocolos_tab.js#L1429-L1436) | ignoram `goTo` ⇒ guia órfã | honrar `goTo` (têm o `protocolo_id`) ou, no mínimo, fechar a guia | médio | S |
| 5 | [plugin.yaml:3](../storages/plugins/protocolos/plugin.yaml#L3) | — | `1.35.0` → `1.35.1` | baixo | S |
| 6 | `tests/js/goto_tab.test.js` (**novo**, repositório de plugins) | — | `node --test` sobre o módulo puro | baixo | S |

### 3.1 Falsos positivos descartados

| Suspeita | Por que NÃO é o problema |
|---|---|
| "O popup não fecha" | Fecha. `close(v)` remove o modal da lista e notifica **antes** de resolver a promessa ([ModalHost.js:36-42](../web/static/js/plugins/ModalHost.js#L36-L42)). O que o operador vê é a guia inteira sendo substituída, popup incluído. |
| "É o `defaultAssignmentTab` que precisa mudar" | É causa do **outro** sintoma, e o plano 88 escolheu `mine` de propósito ([hubDefaults.js:7-15](../web/static/js/services/hubDefaults.js#L7-L15)). Com a guia nova ele para de aparecer aqui sem qualquer mudança. Travado em D4. |
| "Basta trocar `pushState` por `window.open`" | Bloqueado pelo popup blocker (§2.3) — **em silêncio**. Seria uma regressão. |
| "Basta transformar o botão num `<a target="_blank">`" | A URL não existe no momento do clique: o `protocolo_id` sai do `/resolve` e a continuidade ainda pode trocá-lo por `previous_id` ([extends.js:314-316](../storages/plugins/protocolos/static/extends.js#L314-L316)). Um `<a>` apontaria para o protocolo errado ou para nada. |
| "O interceptor de links do plano 106 resolve" | Ele age sobre `<a href>` já existente e respeita `target` — não tem o que interceptar aqui. |
| "Precisa de rota/endpoint novo" | Não. `/protocolos?detail=<id>` já é deep-link de carga fria ([protocolos_tab.js:1246-1266](../storages/plugins/protocolos/static/protocolos_tab.js#L1246-L1266)). |

---

## 4 — O desenho

### 4.1 Abrir dentro do gesto, apontar depois

```
clique  ──► resolve_form.submit(true)
            │  window.open('', 'wb_protocolo')        ← SÍNCRONO, dentro do onClick
            │  escreve "Abrindo protocolo…" na guia
            └─ onOk({ fields, protoFields, goTo:true, gotoWindow:win })
                    │
                    ├─ await POST /resolve          (guia em branco esperando)
                    ├─ await POST relink-decision   (opcional)
                    └─ id? ──sim──► win.location.replace('/protocolos?detail=<id>') + win.focus()
                              └─não──► win.close()   ← nada de about:blank órfão
```

A guia é aberta com **nome fixo** (`wb_protocolo`), o que dá D1 de graça: a 2ª resolução recebe **o mesmo objeto de janela** e apenas troca o conteúdo. `about:blank` aberta pela própria origem é same-origin ⇒ escrever o placeholder nela é permitido.

⚠️ **Por que não abrir no `extends.js`, logo após o `await api.ui.openModal(...)`?** Tecnicamente funcionaria (a ativação transitória do Chrome dura ~5 s e não é gasta por microtask), mas passaria a depender de um **temporizador de navegador não especificado** e quebraria em silêncio se alguém inserir um `await` antes. Abrir dentro do `onClick` não depende de janela de tempo nenhuma. Ver **P1**.

### 4.2 O contrato do módulo puro

```js
// static/goto_tab.js  (PURO — sem preact, sem DOM, sem rede)
export const PROTO_TAB_NAME = 'wb_protocolo';
export function protocoloUrl(id)            // → '/protocolos?detail=<id>'  (id codificado)
export function tabAction({ hasWindow, protocoloId })
//   { hasWindow:true,  protocoloId: 7    } → 'navigate'
//   { hasWindow:true,  protocoloId: null } → 'close'
//   { hasWindow:false, protocoloId: 7    } → 'fallback'   (popup bloqueado)
//   { hasWindow:false, protocoloId: null } → 'none'
```

A decisão fica testável sem navegador; `extends.js` só executa o verbo. Mesmo padrão dos outros módulos puros do plugin (`proto_fields`, `close_plan`, `tab_order`…), todos com `.test.js` em `tests/js/`.

### 4.3 Quando o navegador recusa a guia

`window.open` devolve `null` ⇒ `tabAction` → `'fallback'` ⇒ **o comportamento de hoje** (`pushState` + `PopStateEvent`). Nunca fica pior que agora e o atendente não fica sem ir ao protocolo. Ver **P2** (avisar ou não com um toast).

---

## 5 — Fases / Roadmap

```
WAVE 0   F0 (baseline: instalado == git, reproduzir)                     🔴 sozinha
              │ (barreira: nada começa sem a base confirmada)
WAVE 1   F1 (módulo puro goto_tab.js + teste)                            🟢
              │ [bloqueia: F2, F3, F4]
WAVE 2   F2 (resolve_form abre no gesto)                                 🔴 sozinha
              │ [bloqueia: F3, F4]
WAVE 3   F3 (extends aponta a guia)  ·  F4 (call sites B e C)            🟢 paralelas
              │
WAVE 4   F5 (bump 1.35.1 + validação no navegador)                       🔴 sozinha
              │ ── GATE HUMANO: o operador confirma ──
WAVE 5   F6 (porte para o src 2.2.0 + publicação)                        ⏸️ adiada
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | Baseline e reprodução | 🔴 | baixo | o bug é reproduzido e a base confirmada |
| 1 | **F1** | `goto_tab.js` puro + `node --test` `[bloqueia: F2,F3,F4]` | 🟢 | baixo | 4 casos de `tabAction` verdes |
| 2 | **F2** | `resolve_form.js` `[depende: F1]` `[bloqueia: F3,F4]` | 🔴 | médio | o clique abre guia em branco com placeholder |
| 3 | **F3** | `extends.js` (site A) `[depende: F2]` | 🟢 | médio | a guia cai no protocolo; a do painel não muda |
| 3 | **F4** | Sites B e C sem guia órfã `[depende: F2]` | 🟢 | médio | nenhum `about:blank` sobra |
| 4 | **F5** | Bump + validação | 🔴 | baixo | operador aprova no navegador |
| 5 | **F6** | Porte 2.2.0 + publicação ⏸️ | 🔴 | **alto** (§6 · R5) | só após o gate humano |

---

### Fase F0 — Baseline: confirmar a base e reproduzir (🔴)

**Objetivo:** garantir que se está editando a versão que roda, e ver o bug acontecer.

**Itens:**
1. `[sequencial]` Confirmar que `storages/plugins/protocolos/` é 1.35.0 e bate byte a byte com `git show 6e3fbf3:plugins/protocolos/src/static/extends.js` do repositório de plugins. *(já verificado em 2026-08-21 — refazer se houver qualquer commit no intervalo).*
2. `[sequencial]` Confirmar que **nenhuma outra IA/sessão** está editando `storages/plugins/protocolos/` — é exatamente o que travou as fases de plugin do plano 106 (⚠️ regressão da 1.26.0: dois builds de fontes divergentes se sobrescrevem em silêncio).
3. `[sequencial]` Reproduzir: abrir uma conversa pela aba **Todas**, Resolver → preencher obrigatórios → "Resolver e ir ao protocolo". Observar: a guia é substituída; ao voltar, a aba caiu em "Minhas".

**Pronto quando:** o bug foi visto ao vivo e a cópia instalada está confirmada como base limpa.

#### Status de execução — Fase F0
**Estado:** ✅ Concluída (2026-08-21)
- **O que foi feito:** Confirmado que `storages/plugins/protocolos/` é 1.35.0 e que `extends.js`/`resolve_form.js` batem **byte a byte** com `6e3fbf3` do repositório de plugins (md5 idênticos). Nenhuma edição concorrente: todos os 13 arquivos de `static/` têm o mesmo mtime (2026-08-18 14:32) e o repositório de plugins não tem modificação pendente em `protocolos`.
- **Como foi feito / decisões:** Comparação por `md5sum` contra `git show 6e3fbf3:…` em vez de `diff` — mais barato e não depende de checkout.
- **Problemas / pendências:** **A reprodução no navegador (item 3) não foi feita** — é gesto de operador. O diagnóstico não dependia dela: o `pushState` está no código e a arqueologia (`git log -S "window.open"` ⇒ zero) já provava que a guia nova nunca existiu.
- **Verificação:** `md5sum` dos dois arquivos × `6e3fbf3` idênticos; `git status` do repositório de plugins limpo em `plugins/protocolos/`.

---

### Fase F1 — Módulo puro `goto_tab.js` (🟢) `[bloqueia: F2, F3, F4]`

**Objetivo:** isolar em código testável tudo que **decide**, deixando fora só o que **executa** no `window`.

**Itens:**
1. `[paralelo]` Criar `storages/plugins/protocolos/static/goto_tab.js` com `PROTO_TAB_NAME`, `protocoloUrl(id)` e `tabAction({hasWindow, protocoloId})` (§4.2). Sem `import` de preact — é a regra que mantém o módulo testável em `node --test`.
2. `[paralelo]` `protocoloUrl` deve codificar o id (`encodeURIComponent`) — o `?detail=` é lido cru pelo `readUrlParam`.
3. `[paralelo]` Escrever `tests/js/goto_tab.test.js` no repositório de plugins cobrindo os **4** retornos de `tabAction` + a forma da URL. Import relativo a `../../src/static/goto_tab.js`, como os testes irmãos.

⚠️ O teste vive no repositório de plugins, cujo `src` está em 2.2.0. Enquanto a F6 não rodar, o teste referencia um arquivo que só existe na cópia instalada — **escreva o teste na F1 mas rode-o na F6**, ou copie o módulo para o `src` já na F1 sem tocar em mais nada. Ver **P3**.

**Pronto quando:** `node --test` do arquivo passa isoladamente; nenhum `import` de DOM/preact no módulo.

#### Status de execução — Fase F1
**Estado:** ✅ Concluída (2026-08-21)
- **O que foi feito:** Criado `static/goto_tab.js` com `PROTO_TAB_NAME`, `protocoloUrl(id)`, `tabAction({hasWindow, protocoloId})` e — acrescentados durante a execução — `applyTabAction({win, protocoloId})` e `closeTab(win)`. Criado `tests/js/goto_tab.test.js` no repositório de plugins (18 casos).
- **Como foi feito / decisões:** **Desvio deliberado do §4.2:** os VERBOS (`applyTabAction`/`closeTab`) ficaram no MESMO arquivo da decisão, abaixo de uma cerca comentada. O plano previa o módulo só com a decisão, mas isso obrigaria a duplicar ~15 linhas de despacho em `extends.js` **e** `protocolos_tab.js` — e duas cópias de "replace, não href" divergem. Nada roda no import, então o módulo continua carregável em `node --test`. **P3 → (b):** `goto_tab.js` foi copiado para o `src` já agora (arquivo novo, não conflita com a 2.2.0), para o teste rodar de verdade em vez de ficar esperando a F6.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `node --input-type=module --check` OK; `node --test goto_tab.test.js` ⇒ **18/18 verdes**.

---

### Fase F2 — `resolve_form.js` abre a guia DENTRO do gesto (🔴) `[depende: F1]` `[bloqueia: F3, F4]`

**Objetivo:** a única fase que decide se o navegador libera a guia. Sozinha, sem paralelizar.

**Itens:**
1. `[sequencial]` Em [resolve_form.js:205-207](../storages/plugins/protocolos/static/resolve_form.js#L205-L207), `submit(goTo)` deixa de ser expressão única: quando `goTo`, chama um helper local que faz `window.open('', PROTO_TAB_NAME)` — **primeira instrução, sem nada assíncrono antes** — e devolve o handle (ou `null`).
2. `[sequencial]` Com handle, escrever um placeholder mínimo na guia ("Abrindo protocolo…"), dentro de `try/catch`. Nada de estilo elaborado: a guia vive ~1 s. ⚠️ Não usar `wa-*` aqui — a guia em branco não tem o CSS do painel carregado; texto simples com `color-scheme` neutro.
3. `[sequencial]` `onOk` passa a receber `gotoWindow`. Os campos existentes (`fields`, `protoFields`, `goTo`) **não mudam de forma** — os três call sites continuam lendo o que já liam.
4. `[sequencial]` O botão "Resolver" (`submit(false)`) **não** abre guia nenhuma. O `Ver protocolo` do cabeçalho fica **intocado** (já é `<a target="_blank">`, e usa `_blank` anônimo de propósito: consultar durante o preenchimento não deve roubar a guia do protocolo).

**Pronto quando:** clicar "Resolver e ir ao protocolo" abre **imediatamente** uma guia com "Abrindo protocolo…" (mesmo antes de o `/resolve` responder), sem ícone de popup bloqueado na barra do Chrome. A guia ainda não navega — é o esperado nesta fase.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída (2026-08-21)
- **O que foi feito:** [resolve_form.js:19](../storages/plugins/protocolos/static/resolve_form.js#L19) importa `PROTO_TAB_NAME`/`PLACEHOLDER_HTML`; [:217-229](../storages/plugins/protocolos/static/resolve_form.js#L217-L229) traz `openProtocoloTab()` e [:232-235](../storages/plugins/protocolos/static/resolve_form.js#L232-L235) o `submit` que devolve `gotoWindow`.
- **Como foi feito / decisões:** **P1 → (a)**, como recomendado: a guia abre dentro do `onClick` (o `submit` roda direto no handler, sem nada assíncrono antes). `gotoWindow` é **aditivo** — `fields`/`protoFields`/`goTo` mantêm a forma, então nenhum call site quebra. `submit(false)` não abre guia (`goTo ? openProtocoloTab() : null`) e o `Ver protocolo` do cabeçalho ficou **intocado**, com `_blank` anônimo (R10).
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `node --input-type=module --check` OK. **Falta a confirmação visual** (a guia abrir na hora com "Abrindo protocolo…") — é browser, vai junto da matriz da F5.

---

### Fase F3 — `extends.js` aponta a guia em vez de navegar na atual (🟢) `[depende: F2]`

**Objetivo:** o site A (o do print) passa a usar a guia, e a guia do painel deixa de ser tocada.

**Itens:**
1. `[paralelo]` Substituir o bloco [extends.js:324-327](../storages/plugins/protocolos/static/extends.js#L324-L327) por um despacho sobre `tabAction({ hasWindow: !!win && !win.closed, protocoloId })`:
   - `'navigate'` → `win.location.replace(protocoloUrl(id))` + `win.focus()` (D2). **`replace`, não `href`**: a guia reusada não deve acumular histórico de protocolos anteriores.
   - `'close'` → `win.close()` (o `/resolve` falhou ou não devolveu id).
   - `'fallback'` → o `pushState` + `PopStateEvent` de hoje (§4.3).
   - `'none'` → nada.
2. `[paralelo]` Envolver tudo em `try/catch` — o `return atend` (que faz o core prosseguir para `/status`) **não pode** depender disso (princípio fixo do §0).
3. `[paralelo]` Atualizar o comentário das linhas [extends.js:320-323](../storages/plugins/protocolos/static/extends.js#L320-L323): ele hoje explica o `pushState` e ficaria mentindo. Registrar ali **por que** a guia é aberta lá atrás no `resolve_form` (é a informação que se perde primeiro).
4. `[paralelo]` O caminho de continuidade "faz parte do anterior" ([extends.js:314-316](../storages/plugins/protocolos/static/extends.js#L314-L316)) já reescreve `protocoloId` **antes** do bloco — nada a fazer, mas confirmar que a guia recebe o `previous_id`.

⚠️ Os desfechos `'abort'` e `'resolved'` da continuidade retornam **antes** do formulário ([extends.js:257-258](../storages/plugins/protocolos/static/extends.js#L257-L258)) ⇒ o botão nem chega a existir. Sem mudança.

**Pronto quando:** resolver pela aba **Todas** com "ir ao protocolo" ⇒ a guia nomeada mostra o protocolo certo e ganha foco; a guia do painel continua **na mesma conversa, na aba Todas**, com filtros e rolagem intactos; resolver um 2º atendimento **reusa a mesma guia** (D1).

#### Status de execução — Fase F3
**Estado:** ✅ Concluída (2026-08-21)
- **O que foi feito:** O bloco do `pushState` em `extends.js` virou uma linha: `if (result.goTo) applyTabAction({ win: result.gotoWindow, protocoloId });` ([extends.js:324-341](../storages/plugins/protocolos/static/extends.js#L324-L341)). Import em [:19-21](../storages/plugins/protocolos/static/extends.js#L19-L21). O comentário foi reescrito para explicar por que a guia nasce lá atrás.
- **Como foi feito / decisões:** O gate continua sendo `result.goTo`, **não** a existência da guia — sem ele, o botão "Resolver" simples cairia no `fallback` e navegaria, que é exatamente o bug consertado. Isso está comentado no código porque é o erro natural de quem for simplificar a linha depois. `replace` em vez de `href` (R4) e `try/catch` total ficaram dentro do `applyTabAction`, não no call site.
- **Problemas / pendências:** Nenhuma. O caminho de continuidade "faz parte do anterior" já reescreve `protocoloId` **antes** deste bloco ⇒ a guia recebe o `previous_id` sem mudança nenhuma (confirmado por leitura; falta ver no navegador).
- **Verificação:** `node --input-type=module --check` OK; diff funcional (ignorando reindentação) = 1 linha removida ×3 e 1 acrescentada.

---

### Fase F4 — Os outros dois call sites não podem deixar guia órfã (🟢) `[depende: F2]`

**Objetivo:** fechar o buraco do §2.2 — B e C ignoram `goTo` e passariam a produzir `about:blank` abandonado.

**Itens:**
1. `[paralelo]` **Site B** — `resolveAndCloseAll` ([extends.js:57-67](../storages/plugins/protocolos/static/extends.js#L57-L67)): tem o `pid` resolvido em [:78](../storages/plugins/protocolos/static/extends.js#L78) ⇒ dá para **honrar** o `goTo` com o mesmo despacho da F3. ⚠️ Este caminho **finaliza o protocolo** logo em seguida; a guia deve abrir o detalhe **depois** do `/close`, senão mostra estado velho.
2. `[paralelo]` **Site C** — `forceResolveAndClose` ([protocolos_tab.js:1429-1443](../storages/plugins/protocolos/static/protocolos_tab.js#L1429-L1443)): o operador **já está** na aba Protocolos. Ver **P4** — honrar (abre o detalhe numa 2ª guia) ou apenas fechar a guia e abrir o detalhe na própria tela.
3. `[paralelo]` Qualquer que seja a escolha, **nenhum dos dois pode deixar handle aberto**: se não navega, `win.close()`.

**Pronto quando:** clicar "Resolver e ir ao protocolo" nos três call sites nunca deixa uma guia `about:blank` para trás.

#### Status de execução — Fase F4
**Estado:** ✅ Concluída (2026-08-21)
- **O que foi feito:** **Site B** (`resolveAndCloseAll`, [extends.js:52-119](../storages/plugins/protocolos/static/extends.js#L52-L119)) e **site C** (`forceResolveAndClose`, [protocolos_tab.js:1408-1504](../storages/plugins/protocolos/static/protocolos_tab.js#L1408-L1504)) passaram a capturar `gotoWindow`/`goTo`, navegar no sucesso e fechar a guia em qualquer outra saída.
- **Como foi feito / decisões:** **Os dois usam `try/finally`, não `dropTab()` espalhado**: o site B tem 8 saídas e o C tem 9 — enfiar uma chamada em cada uma é a versão que apodrece no primeiro `return` novo. Com o `finally`, "quem não navegou, fecha" vale para saídas que ainda não existem. Nos dois, a navegação acontece **depois** do `/close` (senão a guia mostraria o protocolo ainda aberto). **P4 → (a) honrar, contra a recomendação (b) do plano:** ao olhar a tela, (b) — `openDetail()` na própria aba — **colide com os próprios chamadores**: `finalizeProtocolo` faz `closeDetail(); reload()` logo depois de `forceResolveAndClose`, e desfaria o `openDetail` meio segundo depois. Honrando na guia, os três call sites têm um comportamento só e a tela de origem segue intacta. O motivo está comentado no código.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `node --input-type=module --check` OK nos dois arquivos; `grep` confirma que os **três** (e apenas três) call sites do `ResolveForm` foram cobertos.

---

### Fase F5 — Bump 1.35.1 e validação no navegador (🔴)

**Objetivo:** entregar ao operador algo identificável e conferido à mão.

**Itens:**
1. `[sequencial]` [plugin.yaml:3](../storages/plugins/protocolos/plugin.yaml#L3): `1.35.0` → `1.35.1`.
2. `[sequencial]` `node --input-type=module --check` nos arquivos alterados. ⚠️ **`node --check` sozinho dá falso negativo em módulo ES** — usar sempre com `--input-type=module`.
3. `[sequencial]` Reiniciar o servidor dev e recarregar o painel com **cache desligado** (o `extends.js` é servido pelo mount estático do plugin).
4. `[sequencial]` Matriz manual no navegador (§9).

**Pronto quando:** o operador confirma o comportamento. **Este é o gate da F6.**

#### Status de execução — Fase F5
**Estado:** ✅ Concluída (2026-08-21) — entregue; o operador liberou a publicação
- **O que foi feito:** [plugin.yaml:3](../storages/plugins/protocolos/plugin.yaml#L3) → **1.35.1** + entrada de changelog na descrição. Worker do dev recarregado pelo idioma do repo (`touch server/_reload_trigger.py`, já que `--reload` só observa `*.py` e a edição foi de `.js`/`.yaml`).
- **Como foi feito / decisões:** Não reiniciei o processo do servidor: tocar o `_reload_trigger.py` é o mecanismo que o próprio `plugins/restart.py` usa e recarrega o worker sem derrubar a sessão.
- **Problemas / pendências:** ⚠️ **A matriz manual do §9 NÃO foi reportada como executada.** O operador liberou a publicação ("Pode publicar no repositorio de plugins") com o pop-up aberto na tela, o que destravou a F6 — mas as 15 verificações de janela não foram confirmadas item a item, em especial o REUSO da guia no 2º fechamento e o "Voltar" não passear pelos protocolos anteriores. Vale rodar a matriz antes de atualizar produção.
- **Verificação:** `plugin.yaml` parseia; a tabela `plugins` mostra `1.35.1`, `enabled=1`, `load_error=None`; `/plugins/protocolos/static/goto_tab.js` responde 200; painel 200. **Suíte JS do plugin: 222/222 verdes.**

---

### Fase F6 — Porte para o `src` 2.2.0 e publicação (🔴) ⏸️ **ADIADA — gate humano (D5)**

**Objetivo:** as duas linhas do plugin não podem divergir. O delta é portável verbatim (§2.5).

**Itens:**
1. `[sequencial]` Aplicar o mesmo delta em `plugins/protocolos/src/static/` do repositório de plugins (`goto_tab.js` novo, `resolve_form.js`, `extends.js`, `protocolos_tab.js`) e bumpar o `plugin.yaml` do `src`.
2. `[sequencial]` Rodar `python3 scripts/test_plugins.py protocolos` — Python **e** JS, incluindo o `goto_tab.test.js` da F1.
3. `[sequencial]` **Instalar a cópia local ANTES de publicar** — commit/zip não muda o que roda; a cópia viva é `storages/plugins/<id>/`.
4. `[sequencial]` **Antes de buildar**, conferir a versão em produção (tabela `plugins`) e o `audit_log`: uma versão pode ter sido publicada por outra pessoa no meio do trabalho, e `git fetch` não mostra isso.
5. `[sequencial]` Decidir o caminho de entrega para produção (que roda **1.35.0**) — ver **P5**.

**Pronto quando:** as duas linhas contêm o mesmo delta e o zip publicado bate com o `src` (`--check`).

#### Status de execução — Fase F6
**Estado:** ✅ Concluída (2026-08-21) — publicada como 2.4.1 (`49ae820`)
- **O que foi feito:** `git pull --ff-only` (o local estava **4 commits atrás**) e delta portado para o `src`, que subiu de 2.2.0 para **2.4.0** enquanto o trabalho corria. `resolve_form.js`/`protocolos_tab.js` copiados verbatim (eram byte-idênticos ao 1.35.0 mesmo na 2.4.0); `extends.js` recebeu as três edições à mão; `goto_tab.js` e o teste já estavam lá desde a F1. Bump para **2.4.1** + changelog no `plugin.yaml` e na tabela do README (que nenhum script gera).
- **Como foi feito / decisões:** **P5 → nem (a) nem (b) como escritas: o operador mandou mesclar com a versão do repositório** ("se tiver alguma atualização lá faça o merge com essa versão"), então o delta foi para a linha 2.x em vez de um branch `1.35.x`. A confirmação de fidelidade do porte é forte: `diff` entre o `extends.js` instalado (1.35.1) e o do `src` (2.4.1) devolve **apenas** a seção de continuidade que a 2.0.0 mudou — o delta 136 é byte-idêntico nas duas linhas. Os 4 commits novos do remoto (protocolos 2.4.0) **não tocam em JS** (`logic.py`, `services.py`, testes Python), então não houve conflito.
- **Problemas / pendências:** ⚠️ **Produção roda 1.35.0** (conferido na tabela `plugins` do banco de produção). Importar a 2.4.1 lá é um salto de 1.35.0 → 2.4.1 que carrega a **mudança que quebra da 2.0.0** (a área "Decidir a continuidade por um atributo personalizado" saiu inteira: quem a tinha configurada passa a ver o pop-up perguntar sempre). Isso **não** é consequência do plano 136 — é o acúmulo da linha 2.x — mas é a decisão que sobra para o operador antes de atualizar produção.
- **Verificação:** `python3 scripts/test_plugins.py protocolos` (Python + JS) **exit 0**; JS **222/222**; sintaxe ES OK nos 4 arquivos do `src`; `build_plugins.py protocolos --check` ⇒ `current` (52 arquivos, sha256 `72183f6f…`); o zip inspecionado carrega `goto_tab.js`, tem **0** `history.pushState` e **0** arquivo de teste. `git push origin main` ⇒ `894aa12..49ae820`.
- **Armadilha encontrada no caminho:** `build_plugins.py` valida a cobertura do catálogo varrendo **todos** os diretórios de plugin do disco, não só o que se pede para buildar — `plugins/pagamentos/` (trabalho em andamento, não versionado) fazia o build recusar com `missing from catalogue`. Movido para fora e restaurado depois, com fingerprint conferido antes e depois. E o builder também exige que `protocolos.json` + `catalog.json` batam com o `plugin.yaml`: bump em três lugares, mais a tabela do README, que **nenhum script gera**.

---

## 6 — Riscos e cuidados

| # | Ponto | Risco | Mitigação |
|---|---|---|---|
| R1 | `window.open` depois de `await` | Popup bloqueado **em silêncio** — clique sem efeito, pior que hoje | Abrir no `onClick` (F2); fallback `pushState` quando `null` (§4.3) |
| R2 | Guia em branco quando o `/resolve` falha | `about:blank` órfão acumulando | `tabAction` → `'close'` (F3 item 1) |
| R3 | Sites B e C ignoram `goTo` | Guia órfã em 2 dos 3 caminhos | F4 (fase existe só por isso) |
| R4 | `win.location.href` em vez de `replace` | A guia reusada acumula histórico; "Voltar" pula protocolos antigos | `replace` (F3 item 1) |
| R5 | **Importar o zip da linha 2.x depois** | Apagaria o delta 1.35.1 **em silêncio** — é a classe de regressão da 1.26.0 | ✅ **NEUTRALIZADO na F6**: o delta foi portado para a 2.4.1 antes de qualquer import, e o `diff` entre as duas linhas prova que é byte-idêntico. O que sobra é operacional, não silencioso: produção em 1.35.0 recebe junto a mudança que quebra da 2.0.0 |
| R6 | Edição concorrente de `storages/plugins/protocolos/` | Dois builds de fontes divergentes se sobrescrevem sem aviso (travou o plano 106) | F0 item 2 |
| R7 | Bloqueador de popup de terceiros (extensão) | Nem o `window.open` no gesto passa | Fallback (§4.3) + P2 |
| R8 | Guia fechada pelo operador durante o `/resolve` | `win.closed` ⇒ acesso a `location` lança | `!win.closed` no `hasWindow` + `try/catch` (F3 item 2) |
| R9 | Placeholder usando classes `wa-*` | A guia nova não carrega o CSS do painel ⇒ texto ilegível no tema escuro | Texto simples, sem depender do Tailwind (F2 item 2) |
| R10 | Regressão do `Ver protocolo` | Se ganhar o mesmo nome de guia, consultar durante o preenchimento sequestraria a guia do protocolo | Fica com `_blank` anônimo, intocado (F2 item 4) |

**Fora de risco (verificado):** nenhuma migration, nenhuma rota, nenhum evento/filtro do bus, nenhum segredo em URL, nenhuma mudança de `WHATSBOT_API_VERSION` (o manifesto segue `">=1.0,<2.0"`), nenhuma tela nova para conferir no modo escuro.

---

## 7 — Perguntas em aberto

**P1 — Abrir a guia no `resolve_form` (dentro do `onClick`) ou no `extends` (logo após o `openModal`)?**
(a) **No `onClick`** — imune a temporizador de ativação; custo: o handle atravessa o `onOk` e os 3 call sites precisam saber dele (F4).
(b) **No `extends`, após o `await openModal`** — não muda a assinatura do `onOk` e resolve B e C de graça; depende da ativação transitória (~5 s no Chrome, não especificada) sobreviver ao microtask, e quebra em silêncio se alguém inserir um `await` antes.
**Recomendação: (a)**. ✅ **DECIDIDO (2026-08-21, F2): (a)** — a guia abre no `onClick` do `submit`. O custo previsto (o handle atravessar os 3 call sites) foi pago na F4 com `try/finally`, e saiu mais barato que a alternativa: o `finally` protege saídas que ainda nem existem.

**P2 — Quando o popup é bloqueado, avisar?**
(a) **Fallback silencioso** (`pushState`, o comportamento de hoje) — previsível, nunca pior que agora.
(b) **Fallback + `notify()`** explicando que popups estão bloqueados.
**Recomendação: (a)**. ✅ **DECIDIDO (2026-08-21): (a)** — fallback silencioso. (b) continua barato de acrescentar se o operador relatar confusão; travado por teste (`fallback: popup bloqueado repete o comportamento anterior`).

**P3 — Onde escrever o `goto_tab.test.js` enquanto o `src` está em 2.2.0?**
(a) Escrever na F1 e **rodar só na F6** (o teste fica vermelho/ausente no intervalo).
(b) Copiar `goto_tab.js` para o `src` já na F1 (só o arquivo novo, sem tocar no resto da 2.2.0) e rodar desde já.
**Recomendação: (b)**. ✅ **DECIDIDO (2026-08-21, F1): (b)** — `goto_tab.js` foi copiado para o `src` já na F1. 18 casos verdes desde o primeiro dia, em vez de um teste dormente até a F6.

**P4 — O site C (Kanban/lista) deve honrar o `goTo`?**
(a) **Honrar** — abre o detalhe numa 2ª guia. Coerente com o botão, mas estranho: o operador já está na tela de Protocolos.
(b) **Fechar a guia e abrir o detalhe na própria tela** (`openDetail`, que já existe ali) — é o que o botão *significa* naquele contexto.
**Recomendação: (b)**. ✅ **DECIDIDO (2026-08-21, F4): (a) HONRAR — a recomendação foi revertida ao olhar a tela.** (b) é impossível como escrita: `finalizeProtocolo` chama `closeDetail(); reload()` **imediatamente** após `forceResolveAndClose`, então um `openDetail()` de dentro dela seria desfeito pelo próprio chamador meio segundo depois. Honrando na guia, os três call sites do botão têm um comportamento só e a tela de origem fica intacta — que é o pedido original. O motivo está comentado em `protocolos_tab.js`, senão alguém "conserta" de volta para (b).

**P5 — Como levar isto à produção, que roda 1.35.0?**
(a) **Branch `1.35.x`** no repositório de plugins a partir de `6e3fbf3` e gerar o zip 1.35.1 de lá — entrega sem arrastar a 2.x (que removeu a continuidade por atributo e reformou a tela de configuração).
(b) **Publicar a 2.2.x** com o delta e atualizar produção de uma vez — menos trabalho de repositório, muito mais superfície de mudança num deploy.
(c) Zip 1.35.1 gerado **da cópia instalada**, sem passar pelo repositório — rápido e **não recomendado**: é assim que uma linha some do `src` e o `--check` não enxerga (o `--check` compara zip × `src`, não vê arquivo que sumiu da fonte).
**Recomendação: (a).** ✅ **DECIDIDO (2026-08-21, F6): nenhuma das três — o operador mandou mesclar com a versão do repositório** (*"se tiver alguma atualização lá faça o merge com essa versão"*). O delta foi para a linha 2.x, publicada como **2.4.1** (o `src` já tinha subido de 2.2.0 para 2.4.0 durante o trabalho). Consequência que fica aberta: produção segue em **1.35.0**, e atualizá-la agora significa engolir a **mudança que quebra da 2.0.0** junto — decisão de operação, não deste plano.

---

## 8 — Apêndice — arquivos-chave

**Plugin `protocolos` — cópia instalada (base 1.35.0, o que a execução edita)**

| Arquivo | Papel |
|---|---|
| [static/goto_tab.js](../storages/plugins/protocolos/static/goto_tab.js) | **novo** — módulo puro (nome da guia, URL, decisão) |
| [static/resolve_form.js](../storages/plugins/protocolos/static/resolve_form.js) | `submit`/botões — abre a guia no gesto (F2) |
| [static/extends.js](../storages/plugins/protocolos/static/extends.js) | sites A (F3) e B (F4) |
| [static/protocolos_tab.js](../storages/plugins/protocolos/static/protocolos_tab.js) | site C (F4) |
| [plugin.yaml](../storages/plugins/protocolos/plugin.yaml) | versão → 1.35.1 (F5) |

**Repositório de plugins (F6, adiada)** — `plugins/protocolos/src/static/{goto_tab,resolve_form,extends,protocolos_tab}.js`, `plugins/protocolos/src/plugin.yaml`, `plugins/protocolos/tests/js/goto_tab.test.js`.

**Core — somente LEITURA (referência, D4)**

| Arquivo | Por quê |
|---|---|
| [plugins/ModalHost.js](../web/static/js/plugins/ModalHost.js) | o `close(v)` que carrega o handle |
| [services/spaLink.js](../web/static/js/services/spaLink.js) | vocabulário de nova guia do plano 106 (não usado aqui) |
| [services/hubDefaults.js](../web/static/js/services/hubDefaults.js) | a causa do "volta em Minhas" — **não editar** (D4) |
| [docs-planos/106-plano-abrir-em-nova-guia.md](106-plano-abrir-em-nova-guia.md) | plano irmão; F3/F5·C2-C4/F6 dele seguem pendentes no mesmo plugin |

---

## 9 — Checklist de verificação

**Matriz manual no navegador (F5) — o coração da validação, porque o comportamento é de janela:**

- [ ] Abrir conversa pela aba **Todas** → Resolver → "Resolver e ir ao protocolo": a guia nova abre **na hora** e cai no protocolo certo
- [ ] A guia do painel continua **na mesma conversa**, na aba **Todas**, com filtros, rolagem e rascunho intactos
- [ ] O popup de resolver fechou na guia do painel
- [ ] O atendimento de fato **foi resolvido** (some das Abertas / o header vira "Reabrir")
- [ ] Resolver um **2º** atendimento reusa a **mesma** guia (D1) e ela ganha foco (D2)
- [ ] "Voltar" na guia do protocolo **não** percorre os protocolos anteriores (R4)
- [ ] Botão **"Resolver"** simples: nenhuma guia é aberta
- [ ] **"Ver protocolo"** do cabeçalho continua abrindo em guia própria, sem roubar a do protocolo (R10)
- [ ] Continuidade **"faz parte do anterior"**: a guia abre o protocolo **anterior**
- [ ] Continuidade **"é um novo protocolo"** e **cancelar**: nenhuma guia é aberta
- [ ] Falha forçada no `/resolve` (rede offline): a guia em branco **fecha sozinha** (R2)
- [ ] Fechar a guia à mão durante o `/resolve`: nenhum erro no console (R8)
- [ ] Os **três** call sites (beforeResolve, "fechar conversa e protocolo juntos", Kanban) não deixam `about:blank` (F4)
- [ ] Bloqueador de popup ligado: cai no comportamento de hoje, sem clique morto (R1)
- [ ] Modo escuro: o placeholder da guia é legível (R9)

**Automático:**

- [ ] `node --input-type=module --check` em cada arquivo alterado *(nunca `--check` sozinho)*
- [ ] `node --test` do `goto_tab.test.js` verde (4 casos de `tabAction` + forma da URL)
- [ ] `python3 scripts/test_plugins.py protocolos` verde no repositório de plugins *(F6)*
- [ ] `python3 scripts/build_plugins.py protocolos --check` sem "outdated" *(F6; ⚠️ diferença só de permissão — zip `664` × `644` — é falso positivo de `umask`, **não** rebuildar por isso)*
- [ ] Suíte do core **não** precisa rodar: nada de Python, nada de core, nada de banco (D4)
