# Plano 132 — O caret cai onde o operador clica

> **Status:** PLANEJAMENTO · **Data:** 2026-08-19 · **Escopo:** médio (100% frontend; zero migration, zero backend, zero plugin)
> **Origem:** [investigação 131](131-investigacao-cursor-compositor.md), que fechou o chamado dos operadores ("colo texto grande, ponho o cursor no fim e apaga do meio"). **Método:** leitura do código real (`arquivo:linha` conferido), réplica fiel do compositor dirigida por Playwright + Chromium importando os módulos **de produção**, e medição da população de risco no banco de produção via MCP vault.
> A causa principal está **provada, não inferida**: o `<div>` espelho não gera a linha final que a `<textarea>` reserva para o caret, as alturas de rolagem divergem 20px e `mirror.scrollTop = textarea.scrollTop` é **truncado** ([composerMirror.js:42](../web/static/js/utils/composerMirror.js#L42)). O operador clica onde vê o texto e o caret cai ~65 caracteres adiante. Acrescentar a linha que falta **zera o erro em todos os pontos medidos**.
> Este plano corrige essa causa, mais quatro defeitos vizinhos confirmados, e fecha o buraco de teste que deixou tudo isso passar: **`highlightComposerMarkup` tem hoje ZERO testes** — `grep -c` em [formatWhatsApp.test.js](../web/static/js/utils/formatWhatsApp.test.js) devolve `0`.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | ✅ **A correção da linha final vai em `highlightComposerMarkup`**, não nos dois call sites | A função é a **fonte única** dos DOIS espelhos — [Composer.js:370](../web/static/js/components/contacts/Composer.js#L370) e [NewConversationModal.js:505](../web/static/js/components/contacts/NewConversationModal.js#L505) — e não tem nenhum outro consumidor (grep exaustivo). É **pura**, então a regressão é `node --test` sem DOM |
| D2 | ✅ **A paridade `mirror.textContent.length === input.length` é invariante do compositor** | O comentário em [formatWhatsApp.js:78-84](../web/static/js/utils/formatWhatsApp.js#L78) já a declara ("Dropping the markers would shorten the mirror text and drift the caret") e a medição confirmou que ela vale hoje. Por isso a linha que falta entra como **`<br>`**, que não é texto — um `\n` literal mudaria a contagem |
| D3 | ✅ **O espelho não pode alterar métrica de fonte** | Medido: `<b>` e `<code monospace>` desalinham até **17 caracteres acumulados**, e nenhum ajuste de `padding` corrige erro proporcional. O próprio [composerMirror.js:1-17](../web/static/js/utils/composerMirror.js#L1) declara o contrato: "os dois precisam quebrar as linhas no MESMO ponto" |
| D4 | ✅ **Normalizar Unicode só no `paste`, nunca a cada tecla** | Normalizar dentro de componente controlado quebra a sessão de composição da tecla morta e joga o caret para o fim. Não existe hoje **nenhuma** normalização no caminho do texto (as 10 ocorrências de `normalize` no frontend são dobra de acento para **busca**) |
| D5 | ✅ **NÃO trocar a `<textarea>` por editor dedicado** (ProseMirror/Lexical/Quill) nesta rodada | Custo medido: 58–78 KB gzip contra 4 KB de Preact, sem build step para tree-shakear, e exigiria reescrever rascunho, @menção, /atalho, emoji, colar mídia, presença e a bandeja do plano 124. É projeto, não correção — e os 5 defeitos deste plano somam menos de 200 linhas |
| D6 | ✅ **O bug do `replaceToken` entra, mas como workstream SEPARADO** | É real e provado (duplica 300 caracteres num caso, apaga 297 noutro), porém o menu de `@` só existe em **grupo** e **nota privada**. Não é a causa do chamado dos operadores; é dívida vizinha que se conserta barato no mesmo passe |
| D7 | ✅ **Caracterização ANTES da correção** | Disciplina do repo. `highlightComposerMarkup` está no caminho quente do compositor e hoje é território sem teste — mexer nela sem travar o comportamento atual é apostar |

---

## 1. Resumo executivo

O compositor não mostra o texto da `<textarea>`: ela é `text-transparent` e o que o operador lê é um `<div>` espelho pintado atrás dela ([Composer.js:366-385](../web/static/js/components/contacts/Composer.js#L366)). O caret e o clique, porém, são resolvidos na textarea. Todo o desenho depende de as duas caixas quebrarem a linha **no mesmo ponto** — e há **cinco** maneiras de isso deixar de valer, das quais o fix de julho ([c3f401c](../web/static/js/utils/composerMirror.js)) cobriu apenas uma.

A pior delas é silenciosa e não precisa de marcação nenhuma: **texto terminando em quebra de linha**. A textarea reserva uma última linha vazia para o caret; o espelho, com `white-space: pre-wrap`, não gera essa linha. As alturas de rolagem divergem exatos 20px, `mirror.scrollTop` é truncado ao máximo do espelho, e o campo inteiro passa a mostrar conteúdo **uma linha adiantado**.

A correção é um `<br>` condicional numa função pura. As outras quatro (métrica de fonte, teclas mortas que são marcadores, ausência de `ResizeObserver`, e o splice do autocomplete) fecham a mesma família. Nada disso toca backend, banco ou plugin.

---

## 2. Como funciona hoje (mapa)

### 2.1 A topologia

```
<div id="wrap" class="relative">
  <div  class="absolute inset-0 overflow-hidden whitespace-pre-wrap break-words"   ← o operador LÊ isto
        dangerouslySetInnerHTML={highlightComposerMarkup(input)} />                   Composer.js:366-371
  <textarea class="relative z-[1] text-transparent max-h-[120px] wa-scrollbar"     ← o caret VIVE aqui
        value={input} style="caret-color: rgb(var(--wa-text))" />                     Composer.js:372-385
</div>
```

O único elo entre as camadas é [`syncMirror`](../web/static/js/utils/composerMirror.js#L29), que escreve **duas** coisas e mais nada:

```js
if (mirror.style.paddingRight !== padRight) mirror.style.paddingRight = padRight;  // :41  (gutter da barra)
mirror.scrollTop = textarea.scrollTop;                                              // :42  ← truncado
```

Chamado em três lugares: no efeito `[input]` ([Composer.js:78](../web/static/js/components/contacts/Composer.js#L78)), num `requestAnimationFrame` logo depois ([:83](../web/static/js/components/contacts/Composer.js#L83), porque o auto-resize mora no efeito do **pai**, [useComposer.js:146-147](../web/static/js/components/contacts/hooks/useComposer.js#L146)) e no `onScroll` ([:379](../web/static/js/components/contacts/Composer.js#L379)). **Nunca** em `resize`.

### 2.2 A medição que fecha o caso

Réplica com o mesmo HTML/CSS, importando `formatWhatsApp.js` e `composerMirror.js` de produção:

| texto | `textarea.scrollHeight` | espelho | defasagem |
|---|---|---|---|
| 3, 6, 11 e 22 linhas | 58 / 98 / 238 / 438 | idem | **0** |
| **11 linhas + `\n` final** | **258** | **238** | **20px = 1 linha** |

Efeito no operador, campo rolado até o fim, clicando no centro de caracteres **visíveis**:

```
sem \n no fim   → erro 0 em todos os pontos
COM \n no fim   → vejo o caractere 383 → caret cai em 454   (+71)
                  vejo o caractere 428 → caret cai em 493   (+65)
                  vejo o caractere 473 → caret cai em 538   (+65)
```

Prova de causalidade — acrescentar a linha que falta e medir de novo:

| | espelho | textarea | defasagem | pior erro de clique |
|---|---|---|---|---|
| como está hoje | 238 | 258 | 20px | **71 caracteres** |
| com `<br>` quando termina em `\n` | 258 | 258 | **0** | **0** |

### 2.3 ⚠️ Gotchas que tornam certas escolhas obrigatórias

| Gotcha | Consequência |
|---|---|
| `handleSend` faz `input.trim()` ([useComposer.js:228](../web/static/js/components/contacts/hooks/useComposer.js#L228)) | **A quebra final nunca chega ao banco.** O defeito só existe enquanto se compõe — é invisível nos dados por construção, e nenhuma consulta ao histórico o teria encontrado |
| As teclas mortas do ABNT2 são `` ` `` (à) e `~` (ã, õ) | São **exatamente** os marcadores de código inline ([formatWhatsApp.js:95](../web/static/js/utils/formatWhatsApp.js#L95)) e de tachado ([:108](../web/static/js/utils/formatWhatsApp.js#L108)). O Chrome põe o caractere morto **literal** no `value` durante a composição — medido |
| `\b` em JS é ASCII-only | A regex de itálico ([formatWhatsApp.js:104](../web/static/js/utils/formatWhatsApp.js#L104)) casa diferente conforme a forma de normalização: em NFC `á` é não-word, em NFD o `a` é word e a marca combinante não. O mesmo texto visível produz destaque diferente |
| `<html lang="pt-BR">` ([index.html:2](../web/index.html#L2)) e a textarea sem `autocorrect` | O padrão é **ligado**, e em português quase toda autocorreção **produz um acento**. Backspace logo após uma autocorreção significa *desfazer a autocorreção*, não apagar um caractere |
| Não existe `package.json` no repo | Não há jsdom nem devDependencies JS. Teste com DOM real exige decisão de infra — ver **P1** |

---

## 3. Inventário

| # | Defeito | Onde | O que falta | Abordagem | Risco | Esforço |
|---|---------|------|-------------|-----------|-------|---------|
| **F1** | Espelho não gera a linha final ⇒ rolagem truncada em 1 linha | [formatWhatsApp.js:85-112](../web/static/js/utils/formatWhatsApp.js#L85) | nada trata o `\n` terminal | devolver `s + '<br>'` quando `text` termina em `\n`; preserva a paridade de D2 | baixo | S |
| **F2** | Espelho altera métrica: `<b>` e `<code monospace>` | [formatWhatsApp.js:100](../web/static/js/utils/formatWhatsApp.js#L100) e [:95](../web/static/js/utils/formatWhatsApp.js#L95) | realce que não muda layout | trocar por cor/fundo, mantendo o `dim()` por opacidade que já existe (ver **P2**) | médio | M |
| **F3** | Tecla morta injeta marcador ⇒ um trecho troca de fonte no meio da digitação | mesma origem de F2 | guarda de composição, ou F2 | resolvido **de graça** por F2; alternativa em **P2(b)** | médio | S |
| **F4** | Espelho não re-sincroniza em `resize` | [Composer.js:77-85](../web/static/js/components/contacts/Composer.js#L77) (deps `[input]`) | `ResizeObserver` na textarea | observar a textarea e chamar `syncMirror`; desobservar no cleanup | baixo | S |
| **F5** | `replaceToken` mistura índice vivo do DOM com string do state | [composerTokens.js:64-69](../web/static/js/services/composerTokens.js#L64), chamada em [useTokenAutocomplete.js:171](../web/static/js/components/contacts/hooks/useTokenAutocomplete.js#L171) e [:217](../web/static/js/components/contacts/hooks/useTokenAutocomplete.js#L217) | validar `start <= caret`; ler valor e índice da MESMA fonte | espelhar o `insertEmoji` vizinho ([useComposer.js:176-189](../web/static/js/components/contacts/hooks/useComposer.js#L176)), que já lê `el.value` | baixo | S |
| **F6** | `autocorrect` ligado por herança | [Composer.js:372-385](../web/static/js/components/contacts/Composer.js#L372) | atributo ausente | `autocorrect="off"`; **manter** `spellcheck` (só sublinha, não substitui) | baixo | S |
| **F7** | Colagem em NFD ⇒ Backspace tira só o acento | [useMediaUpload.js:291-303](../web/static/js/components/contacts/hooks/useMediaUpload.js#L291) | normalização no paste | normalizar para NFC **uma vez**, no `paste` de texto (D4) | baixo | S |
| **F0** | `highlightComposerMarkup` sem nenhum teste | [formatWhatsApp.test.js](../web/static/js/utils/formatWhatsApp.test.js) (`grep -c` = 0) | caracterização | suíte `node --test` cobrindo os 5 marcadores + paridade de contagem | baixo | M |
| **F9** | A pilha de **desfazer** nativa morre na 1ª escrita programática de `.value` — emoji, @menção, /atalho e hidratação de rascunho matam o Ctrl+Z | [Composer.js:375](../web/static/js/components/contacts/Composer.js#L375) (textarea controlada) + os 4 escritores de `setInput` | inserir via `document.execCommand('insertText')` ou `beforeinput`, que preservam o histórico | médio | M |
| **F10** | **Arrastar-e-soltar de texto** dentro do campo move o trecho, e ninguém intercepta — com o texto transparente o gesto dispara sem querer | [useDropZone.js:15-19](../web/static/js/components/contacts/hooks/useDropZone.js#L15) (`dragHasFiles` só olha arquivo) | decidir se intercepta ou aceita — ver **P5** | médio | S |

**Tamanhos medidos** (`wc -l`): Composer 410 · useComposer 407 · useTokenAutocomplete 290 · composerMirror 43 · formatWhatsApp 125 · composerTokens 130 · ContactDetail 1175.

**Cobertura atual ao redor do compositor**: 66 testes (`composerTokens` 26 · `formatWhatsApp` 22 · `composerSubmit` 15 · `composerMirror` 3). **Nenhum** toca caret, seleção, geometria do espelho ou `highlightComposerMarkup`.

### 3.1 Falsos positivos descartados

| Alegação | Por que NÃO é problema |
|---|---|
| "Render lento perde caracteres digitados" | Medido na réplica com Preact real e **55 ms** de custo por render (o pior caso, grupo de 118 membros): digitando 64 caracteres o mais rápido possível, **zero perdidos** e `dom === state` em todas as amostras. O `useState` do Preact atualiza o valor do hook de forma síncrona e [`handleInputChange`](../web/static/js/components/contacts/hooks/useComposer.js#L193) grava `e.target.value` verbatim |
| "Tecla morta corrompe o VALOR do texto" | Composição real via CDP, com e sem render caro: resultado exato `'ja não vou'`, `dom === state` durante e depois. O que a tecla morta estraga é o **espelho** (F3), não o valor |
| "Acento desalinha a geometria" | Quebras idênticas caractere a caractere com e sem acento: `textarea=[0,64,126,188,252]`, `espelho=[0,64,126,188,252]` |
| "A colagem faz splice manual e erra o índice" | Os dois handlers de paste ([useMediaUpload.js:296](../web/static/js/components/contacts/hooks/useMediaUpload.js#L296) e [ContactDetail.js:461](../web/static/js/components/contacts/ContactDetail.js#L461)) fazem `if (item.kind !== 'file') continue` e retornam **antes** do `preventDefault`. Colagem de texto é 100% nativa |
| "Existe handler de Backspace errado" | Grep vazio. [`handleKeyDown`](../web/static/js/components/contacts/ContactDetail.js#L735) só trata o menu de autocomplete e o Enter. A exclusão é inteiramente nativa |
| "`*asterisco simples*` desalinha" | Medido: **0**. O realce exige `**` (dois), então a sintaxe nativa do WhatsApp não casa e não muda métrica |
| "`_itálico_` desalinha" | Medido: **0**. O itálico sintetizado tem a mesma largura de avanço |
| "O fix `c3f401c` está errado ou ausente" | Está presente e correto no que se propôs. Ele escreve apenas `paddingRight` e `scrollTop`; não há `setSelectionRange`, `.value =` nem `focus()` no módulo. É **estruturalmente incapaz** de apagar um caractere |
| "A textarea é remontada pelo menu de @menção / bandeja de mídia / troca enviar↔microfone" | Verificado em [Composer.js:312-365](../web/static/js/components/contacts/Composer.js#L312): os menus são irmãos, não pais. O **único** estado que ainda substitui a barra é a gravação de áudio (documentado no plano 124) |
| "O auto-resize (`height='auto'` + `scrollHeight`) zera o `scrollTop` ou atrapalha a seleção" | Medido: não zera |
| "Algum caminho alimenta `setInput` com valor derivado" | `toWhatsAppMarkup` só roda no envio ([useComposer.js:228](../web/static/js/components/contacts/hooks/useComposer.js#L228)) — nunca volta ao campo |

---

## 4. Infraestrutura habilitadora — o banco de medição

F1, F2, F3 e F4 são defeitos de **geometria**: só se provam com layout real. O repo não tem DOM de teste (sem `package.json`, sem jsdom), e por isso a família inteira passou despercebida.

O banco usado na investigação 131 é uma página que replica o HTML/CSS do compositor e **importa os módulos de produção por HTTP**, dirigida por Playwright + Chromium num venv próprio. A medição que importa:

```js
// 1. aplica o texto na ORDEM do app (efeito do filho, efeito do pai, rAF)
// 2. leva o caret ao fim e rola, como quem acabou de escrever
// 3. para cada caractere VISÍVEL no espelho: clica no centro dele
//    e compara com textarea.selectionStart  →  divergiu = bug
```

⚠️ **Duas armadilhas que produziram falso positivo na investigação** e estão documentadas em [131 §Apêndice](131-investigacao-cursor-compositor.md): clicar fora da caixa visível (o texto tem 11 linhas, o campo mostra 6) e localizar o caractere por `y >= topo && y <= topo + 20`, que casa a **linha de cima** porque o retângulo do glifo é mais baixo que a caixa de linha. Use os limites reais da textarea e o `bottom` real do retângulo.

Se o banco entra ou não no repositório é **P1**.

---

## 5. Roadmap

```
WAVE 0   F0 · F5 · F6                                    ← 🟢 paralelo, sem dependência entre si
            │ (barreira: F0 trava o comportamento atual de highlightComposerMarkup)
WAVE 1   F1 · F4 · F7                                    ← F1 depende de F0; F4 e F7 independentes
            │ (barreira: F2 precisa de F1 pronto para medir o ganho isolado)
WAVE 2   F2 (→ resolve F3)                               ← 🔴 sozinha, decisão de produto em P2
            │
WAVE 3   F8 validação em produção + roteiro com o operador
```

| Wave | Fase | Workstream | Paral. | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | caracterização de `highlightComposerMarkup` | 🟢 | baixo | suíte nova verde, cobrindo os 5 marcadores + paridade `[bloqueia: F1, F2]` |
| 0 | **F5** | `replaceToken` defensivo | 🟢 | baixo | teste do splice destrutivo passa a falhar antes / verde depois |
| 0 | **F6** | `autocorrect="off"` | 🟢 | baixo | atributo presente; `spellcheck` intacto |
| 1 | **F1** | a linha final do espelho | 🔴 | baixo | `[depende de: F0]` erro de clique = 0 no banco de medição |
| 1 | **F4** | `ResizeObserver` | 🟢 | baixo | redimensionar a janela re-sincroniza sem digitar |
| 1 | **F7** | NFC no paste | 🟢 | baixo | colar NFD passa a apagar 1 caractere por Backspace |
| 2 | **F2** | espelho sem métrica variável | 🔴 | médio | `[depende de: F1, P2]` divergência = 0 com negrito e mono |
| 3 | **F8** | validação | 🔴 | baixo | roteiro de 131 §7 não reproduz mais; operador do vídeo confirma |

---

### Fase F0 — Caracterizar `highlightComposerMarkup`

**Objetivo:** travar o comportamento atual da função antes de mexer nela.

**Itens** — todos `[paralelo]`, um arquivo novo `web/static/js/utils/composerHighlight.test.js` (ou acrescentar a [formatWhatsApp.test.js](../web/static/js/utils/formatWhatsApp.test.js)):
1. Escape de `< > & " '` antes de qualquer marcação.
2. Um caso por marcador: ``` ``` ```, `` ` ``, `**`, `_`, `~` — cada um mantendo o marcador esmaecido.
3. **Paridade de contagem** (D2): para uma bateria de textos, o `textContent` do HTML produzido tem o mesmo comprimento da entrada. Este é o invariante que a correção F1 não pode quebrar.
4. Marcador não pareado não vira tag.
5. Texto vazio/nulo → string vazia.

**Pronto quando:** `node --test web/static/js/utils/*.test.js` verde, e a suíte falha se algum marcador for alterado.

#### Status de execução — Fase F0
**Estado:** ✅ Concluída
- **O que foi feito:** arquivo novo [composerHighlight.test.js](../web/static/js/utils/composerHighlight.test.js) — 29 casos cobrindo escape, um caso por marcador (```` ``` ````, `` ` ``, `**`, `_`, `~`), marcador solto que não vira tag, entrada vazia/nula e o invariante de **paridade de contagem** sobre um corpus de 15 textos.
- **Como foi feito / decisões:** arquivo PRÓPRIO em vez de crescer o `formatWhatsApp.test.js` (que é a caracterização do plano 97, de outra função) — a suíte é sobre o invariante do espelho e cresceu com a F1. Como não há DOM, o `textContent` é reproduzido por um helper que tira tags e decodifica as 9 entidades que a função emite; é o helper que torna a paridade testável sem jsdom.
- **Problemas / pendências:** nenhuma.
- **Verificação:** 29/29 verde. **Teste de mutação** para provar que a suíte morde: dropar os marcadores do negrito ⇒ 4 falhas; re-emitir `~` cru em vez de `&#126;` ⇒ 2 falhas. Restaurado e verde de novo.
---

### Fase F5 — `replaceToken` defensivo

**Objetivo:** o splice do autocomplete parar de apagar/duplicar trecho quando o cursor se moveu depois de o menu abrir.

**Itens:**
1. `[sequencial]` Acrescentar a [composerTokens.test.js](../web/static/js/services/composerTokens.test.js) os dois casos destrutivos (hoje passam calados): `start > caret` duplica o trecho; `caret > start` além do token apaga o trecho.
2. `[sequencial]` Em [composerTokens.js:64](../web/static/js/services/composerTokens.js#L64), abortar devolvendo o valor intacto quando `start > caret`, quando os índices estiverem fora de `[0, value.length]`, ou quando o token não estiver mais em `value.slice(start, caret)`.
3. `[paralelo]` Em [useTokenAutocomplete.js:171](../web/static/js/components/contacts/hooks/useTokenAutocomplete.js#L171) e [:217](../web/static/js/components/contacts/hooks/useTokenAutocomplete.js#L217), ler o valor de `el.value` (vivo) em vez do `input` do closure — mesma fonte do índice, como já faz [`insertEmoji`](../web/static/js/components/contacts/hooks/useComposer.js#L178).
4. `[paralelo]` Fechar/recalcular o menu também em `onSelect`, `onClick` e `onBlur` da textarea — hoje `updateMenus` tem **um único** call site ([useComposer.js:196](../web/static/js/components/contacts/hooks/useComposer.js#L196)), o evento `input`.

**Pronto quando:** os dois testes novos ficam verdes; num grupo, abrir o menu de `@`, clicar no meio do texto e teclar Enter não apaga nada.

#### Status de execução — Fase F5
**Estado:** ✅ Concluída
- **O que foi feito:** guarda em [`replaceToken`](../web/static/js/services/composerTokens.js) (aborta devolvendo o valor intacto quando `start > caret`, índices fora de `[0, length]`, não-inteiros, ou quando `[start, caret)` deixou de ser um token); `applyMention` e `applyQuickReply` ([useTokenAutocomplete.js](../web/static/js/components/contacts/hooks/useTokenAutocomplete.js)) passaram a ler `el.value` — a MESMA fonte do índice; e `updateMenus` ganhou **quatro** gatilhos novos em [useComposer.js](../web/static/js/components/contacts/hooks/useComposer.js) — `onSelect`, `onClick`, `onKeyUp` (só teclas que movem o caret) e `onBlur` — onde antes tinha um só (o evento `input`).
- **Como foi feito / decisões:** a validação "a região ainda é um token" (`/^[@/][\p{L}\p{N}_-]*$/u`) é mais forte que o `start <= caret` do plano — sozinho, ele não pega o caso em que o operador clica MUITO à frente, que é justamente o que apagava 297 caracteres. ⚠️ **Registro de uma correção no meio da execução.** Eu tinha ligado **só** o `onSelect`, no raciocínio de que ele cobriria qualquer movimento de caret. Uma sessão paralela mediu e mostrou que não: o evento `select` da textarea dispara em **seleção**, não em movimento de cursor. Confirmei em Chromium 151 e Firefox 153, com o campo em uso normal:

  | gesto | eventos disparados |
  |---|---|
  | **clique que move o caret** | `click` — **`select` NÃO dispara** |
  | seta / Home / End | `keyup` — `select` **não** dispara |
  | Shift+seta (seleção real) | `select` + `keyup` |

  Ou seja, o `onSelect` sozinho **não cobria o caso relatado**, que é justamente o operador CLICAR noutro ponto. (Um primeiro teste meu sugeriu o contrário; era artefato — eu chamava `setSelectionRange` por código antes de clicar, e o clique disparava `select` ao colapsar aquela faixa programática. Com o campo em uso normal, não dispara.) `ArrowUp`/`ArrowDown` ficam de fora de propósito: com o menu aberto elas são a navegação entre candidatos, e recalcular ali zeraria o índice a cada seta. O `onBlur` é seguro porque os itens do menu aplicam a escolha no `onMouseDown` com `preventDefault`, então clicar num deles não tira o foco do campo.
- **Problemas / pendências:** nenhuma em aberto — mas fica o registro de que a parte dos GATILHOS foi entregue em duas mãos, e a versão que vale é a medida.
- **Verificação:** 6 casos novos, escritos ANTES da correção (D7) — **4 falhavam** de propósito, provando o splice destrutivo; 32/32 verde depois.
---

### Fase F6 — `autocorrect="off"`

**Objetivo:** o navegador parar de substituir palavra por conta própria dentro do compositor.

**Itens:**
1. `[sequencial]` Acrescentar `autocorrect="off"` à textarea de [Composer.js:372-385](../web/static/js/components/contacts/Composer.js#L372) e à de [NewConversationModal.js:506-513](../web/static/js/components/contacts/NewConversationModal.js#L506).
2. `[sequencial]` **Manter** `spellcheck` como está — ele só sublinha; quem substitui é o `autocorrect`. Tirar o sublinhado seria perda de recurso sem ganho.

**Pronto quando:** digitar `nao` no compositor não vira `não` sozinho; o sublinhado vermelho continua aparecendo.

#### Status de execução — Fase F6
**Estado:** ✅ Concluída
- **O que foi feito:** `autocorrect="off"` na textarea dos DOIS compositores — [Composer.js](../web/static/js/components/contacts/Composer.js) e [NewConversationModal.js](../web/static/js/components/contacts/NewConversationModal.js). `spellcheck` intacto.
- **Como foi feito / decisões:** o comentário explicativo foi parar **fora** da lista de atributos. Medido no `htm` vendorizado: comentário HTML ENTRE atributos corrompe o elemento — `autocorrect="off"` virou conteúdo de texto e nasceram três atributos-lixo (`<!--`, `oi`, `--`). Em posição de irmão o `htm` descarta corretamente.
- **Problemas / pendências:** a 1ª versão do comentário tinha uma crase (em `` `spellcheck` ``), que fecha o template literal — **o guard do próprio repo pegou** ([htmTemplates.test.js](../web/static/js/services/htmTemplates.test.js)), que é exatamente para isso que ele existe. Reescrito sem crase.
- **Verificação:** suíte verde; e o texto real do comentário passado pelo `htm` devolve `{rows, onInput, onSelect, autocorrect:"off"}` sem lixo e sem filho de texto.
---

### Fase F1 — A linha final do espelho `[depende de: F0]`

**Objetivo:** as duas camadas voltarem a rolar juntas quando o texto termina em quebra de linha. **É a correção do chamado.**

**Itens:**
1. `[sequencial]` Em [formatWhatsApp.js:111](../web/static/js/utils/formatWhatsApp.js#L111), devolver `s + '<br>'` quando `text` termina em `\n`. **`<br>` e não `\n`** — D2: `<br>` não conta como texto e a paridade de contagem sobrevive.
2. `[sequencial]` Teste puro: entrada terminando em `\n` produz saída terminando em `<br>`; entrada sem `\n` final não ganha nada; **`\n\n` final também ganha exatamente UM `<br>`** (medido: o espelho fica sempre 1 linha curto, nunca 2).
3. `[sequencial]` Reconfirmar a paridade de contagem de F0 com a saída nova.
4. `[sequencial]` Verificar no banco de medição: `textarea.scrollHeight === mirror.scrollHeight` e erro de clique 0 nos casos `\n`, `\n\n`, `\n` + assinatura, e curto-que-não-rola.

**Pronto quando:** no painel real, texto de mais de 6 linhas terminado em Shift+Enter — clicar no meio de uma palavra põe o caret exatamente ali; Backspace apaga o que está à esquerda do cursor. Sem o Shift+Enter, idem (não regrediu).

#### Status de execução — Fase F1
**Estado:** ✅ Concluída
- **O que foi feito:** `if (text.endsWith('\n')) s += '<br>';` no fim de [`highlightComposerMarkup`](../web/static/js/utils/formatWhatsApp.js) + 5 casos novos na suíte da F0.
- **Como foi feito / decisões:** o teste é em `text` (o que o operador digitou) e não em `s` (o HTML já marcado) — assim continua correto se uma regra futura acrescentar HTML depois da quebra final. `<br>` e nunca `\n` literal: um caso dedicado assere que a paridade de contagem sobrevive.
- **Problemas / pendências:** nenhuma.
- **Verificação — MEDIDA, não inferida.** A/B no banco de medição, ligando e desligando a linha:

  | | altura textarea/espelho | pontos de clique | errados | pior erro |
  |---|---|---|---|---|
  | sem `\n` final (controle) | 238/238 | 39 | 0 | 0 |
  | **com `\n` — ANTES** | **258/238** | 39 | **39** | **+71** |
  | **com `\n` — DEPOIS** | **258/258** | 29 | **0** | **0** |
  | dois `\n` — ANTES | 278/258 | 29 | 29 | +69 |
  | dois `\n` — DEPOIS | 278/278 | 20 | 0 | 0 |

  **Idêntico em Chromium 151 e Firefox 153** — mesmos números nos dois motores, o que responde a **P3** para esta correção (o comportamento do `pre-wrap` com quebra final é de especificação). 111 pontos de clique medidos no total, todos com erro 0 depois da correção.
---

### Fase F4 — `ResizeObserver` no espelho

**Objetivo:** o espelho re-sincronizar quando a largura muda sem o texto mudar.

**Itens:**
1. `[sequencial]` Em [Composer.js:77-85](../web/static/js/components/contacts/Composer.js#L77), observar a textarea com `ResizeObserver` chamando `syncMirror`; desobservar no cleanup do efeito (⚠️ o `ResizeObserver` de [useChatDayHeader.js](../web/static/js/components/contacts/hooks/useChatDayHeader.js) já é precedente de observador que **nunca desobserva** — não repetir).
2. `[paralelo]` Mesmo tratamento no espelho de [NewConversationModal.js:90-93](../web/static/js/components/contacts/NewConversationModal.js#L90), que hoje nem tem o `requestAnimationFrame`.

**Pronto quando:** com texto longo no campo, abrir/fechar o painel de informações do contato ou redimensionar a janela mantém o caret alinhado sem precisar digitar uma tecla.

#### Status de execução — Fase F4
**Estado:** ✅ Concluída
- **O que foi feito:** `ResizeObserver` sobre a textarea, em efeito próprio com deps `[]` e `ro.disconnect()` no cleanup, nos DOIS espelhos. O do modal ganhou também o `requestAnimationFrame` que só o do chat tinha.
- **Como foi feito / decisões:** efeito SEPARADO do `[input]` — pendurado nele, criaria e destruiria um observador por tecla. Sem laço de realimentação: observa o textarea, e `syncMirror` só escreve no espelho.
- **Problemas / pendências:** ⚠️ **não reproduzi a falha que esta fase previne.** O gutter medido é **0px** em Chromium e Firefox *headless* (barra de rolagem em sobreposição, que não ocupa layout), então o mecanismo do fix de julho — o único que fica obsoleto numa mudança de largura — nunca chega a ser exercido no banco de medição. Testei encolher e alargar, rolado no meio e no fim, e a divergência foi **0 em todos**: as duas caixas refluem juntas e o navegador reancora as duas rolagens igual. Fica como correção **defensiva e argumentada pelo código** (`syncMirror` grava o gutter como padding persistente, logo ele fica errado se a barra nascer/morrer sem evento de `input`), não como correção medida. Confirmar num Chrome de mesa real, onde `.wa-scrollbar` ocupa 6px de fato.
- **Verificação:** sintaxe e suíte verdes; sem regressão nas medições de F1 (que rodam com o observador ativo no app).
---

### Fase F7 — NFC na colagem

**Objetivo:** texto colado de PDF/macOS parar de exigir dois Backspaces por letra acentuada.

**Itens:**
1. `[sequencial]` Em [useMediaUpload.js:291](../web/static/js/components/contacts/hooks/useMediaUpload.js#L291) (ou num handler de paste próprio do compositor), quando o clipboard traz **texto** e ele difere de `normalize('NFC')`, inserir a versão normalizada e `preventDefault`. Quando já está em NFC, **não tocar** — deixar o caminho nativo, que é o que funciona hoje.
2. `[sequencial]` D4: nada de normalizar em `handleInputChange`. Só no paste.
3. `[sequencial]` Teste puro da função de normalização (entrada NFD → NFC; NFC → idêntico; string sem acento → idêntico por identidade de referência).

**Pronto quando:** colar `manutenção` decomposto e apertar Backspace 3 vezes remove 3 caracteres visíveis, não 3 unidades de código.

#### Status de execução — Fase F7
**Estado:** ✅ Concluída
- **O que foi feito:** módulo puro novo [composerPaste.js](../web/static/js/utils/composerPaste.js) (`toComposerNfc`, `needsNfcNormalization`) + ramo de TEXTO no `handlePaste` de [useMediaUpload.js](../web/static/js/components/contacts/hooks/useMediaUpload.js) + 9 testes.
- **Como foi feito / decisões:** três escolhas que valem registro. (1) `toComposerNfc` devolve **a mesma referência** quando o texto já está em NFC, então o call site decide interceptar com um `===` e a colagem normal — a esmagadora maioria — continua 100% nativa. (2) A inserção usa **`document.execCommand('insertText')`**, não `el.value = …`: ela insere pelo caminho do próprio navegador, **preserva a pilha de desfazer** e dispara `input`, de modo que o state do Preact se atualiza sozinho e não há aritmética de índice para errar — o erro da F5 noutra roupa. Há retaguarda manual caso `execCommand` falhe, porque o `preventDefault` já foi dado e deixar passar perderia a colagem. (3) Mora no `handlePaste` que já existe: `onPaste` é um evento só, e dois ouvintes disputando o mesmo `preventDefault` seria bug de ordem.
- **Problemas / pendências:** o listener de `paste` no `document` ([ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js)) segue só-arquivo de propósito — texto colado FORA do campo não deve ser inserido. O campo de "Nova conversa" não tem `onPaste` e portanto não normaliza; fora do escopo, anotado.
- **Verificação — o mecanismo foi REPRODUZIDO antes de ser corrigido.** Com o cursor no fim, um Backspace sobre texto em NFD:

  | palavra | NFC | NFD |
  |---|---|---|
  | `está` | → `est` ✓ | → **`esta`** (apagou só o acento) |
  | `você` | → `voc` ✓ | → **`voce`** (apagou só o acento) |
  | `não` | → `nã` ✓ | → `nã` ✓ (a última letra não é acentuada) |

  Idêntico em Chromium e Firefox. Isto casa com o vídeo do operador — *palavras sem acento apagavam normalmente* — e explica por que só ALGUMAS palavras falham: só as que TERMINAM em letra acentuada. Depois da correção, com colagem **real** (Ctrl+C/Ctrl+V pelo clipboard do navegador), nos dois motores: texto chega em NFC, `Backspace` devolve `est`/`voc`, caminho `execCommand` confirmado e **Ctrl+Z continua desfazendo**.
---

### Fase F2 — Espelho sem métrica variável `[depende de: F1, P2]`

**Objetivo:** eliminar **estruturalmente** a divergência de quebra de linha — e, de brinde, o defeito da tecla morta (F3).

**Itens:**
1. `[sequencial]` Decidir **P2** antes de escrever qualquer linha.
2. `[sequencial]` Em [formatWhatsApp.js:91-109](../web/static/js/utils/formatWhatsApp.js#L91), trocar o que altera métrica por realce que não altera: `<code style="font-family:monospace">` → cor + fundo na mesma família; `<b>` → cor/peso que não mude a largura de avanço (**a confirmar** por medição: `font-weight` sintetizado costuma mudar; cor não muda).
3. `[sequencial]` Manter o `dim()` por opacidade de [formatWhatsApp.js:88](../web/static/js/utils/formatWhatsApp.js#L88) — é a única parte do realce atual que é segura por construção.
4. `[sequencial]` Modo escuro: qualquer cor nova sai das classes/tokens `wa-*`, nunca hex cru (regra do CLAUDE.md).
5. `[sequencial]` Medir: divergência de quebra = 0 para `**negrito**`, dois negritos, `` `mono` ``, e para a tecla morta com marcador solto no texto.

**Pronto quando:** os casos que hoje divergem 11 e 17 caracteres medem 0; digitar `à`/`ã` com um `` ` ``/`~` solto no texto não muda a fonte de nenhum trecho.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída
- **O que foi feito:** em [highlightComposerMarkup](../web/static/js/utils/formatWhatsApp.js), `<b>` e `<code style="font-family:monospace">` deram lugar a duas constantes de estilo que **não entram no cálculo de layout**: `BOLD_STYLE = -webkit-text-stroke:.4px currentColor` e `CODE_STYLE = background:rgb(var(--wa-text) / .12);border-radius:3px`. Itálico e tachado ficaram como estavam (medidos neutros). Dois testes novos na suíte da F0, um deles uma **lista-negra** (`<b>`, `<strong>`, `<code`, `font-family`, `font-weight`, `font-size`, `letter-spacing`, `font-stretch`) que impede a reintrodução por descuido.
- **Como foi feito / decisões:** **P2 foi resolvida por uma opção que não estava na lista.** As três do plano custavam alguma coisa — (a) perdia o negrito ao vivo, (b) não corrigia o desalinhamento, (c) era meio-termo. Medindo, apareceu uma quarta: manter o realce e trocar a TÉCNICA. `-webkit-text-stroke` engorda o traço do glifo sem mexer na largura de avanço, e um fundo não ocupa espaço nenhum — então o negrito continua parecendo negrito e o desalinhamento vai a zero. **Decidido com o usuário em 2026-08-20**, com o antes/depois à vista. O único efeito visível é o código deixar de ser monoespaçado e ganhar uma tarja; é inevitável, porque trocar de família tipográfica É trocar a métrica. Cor pelo token `--wa-text` (regra do CLAUDE.md), sem hex cru, conferida nos dois temas.
- **Problemas / pendências:** o realce da BOLHA do chat (`formatWhatsApp`) **não foi tocado** — mensagem enviada continua com negrito real e código monoespaçado, e `toWhatsAppMarkup` segue colapsando `**x**` → `*x*`. A mudança é exclusiva da prévia do compositor.
- **Verificação — MEDIDA nos dois motores:**

  | caso | ANTES (erros/pontos, pior) | DEPOIS |
  |---|---|---|
  | sem marcação (controle) | 0/45 · 0 | 0/45 · 0 |
  | um `**negrito**` | 0/49 · 0 | 0/49 · 0 |
  | **dois `**negritos**`** | **52/52 · +27** | **0/49 · 0** |
  | **três `**negritos**`** | **54/54 · +14** (e 198px contra 218px — uma linha inteira a menos) | **0/54 · 0** (198/198) |
  | **`` `mono` `` + `**negrito**`** | **51/51 · +16** | **0/50 · 0** |
  | `~tachado~` / `_italico_` | 0 · 0 | 0 · 0 |

  Chromium 151 e Firefox 153 dão os mesmos números. Legibilidade conferida por captura nos temas claro e escuro.
---

### Fase F8 — Validação

**Objetivo:** confirmar com quem abriu o chamado, não com o próprio teste.

**Itens:**
1. `[sequencial]` Rodar o roteiro de [131 §7](131-investigacao-cursor-compositor.md) e confirmar que não reproduz mais.
2. `[paralelo]` Levar ao operador do vídeo as perguntas de [131 §8](131-investigacao-cursor-compositor.md) — sobretudo **a nº 6** (havia `~` ou `` ` `` solto, e a palavra que falhou tinha `ã`/`õ`/`à`?), que é a única que distingue F3 dos demais. Acrescentar: **o texto foi arrastado com o mouse dentro do campo?** (distingue F10).
3. `[paralelo]` **Sonda de NFD em produção** — com o operador, no chat onde falhou e **antes de apagar nada**, colar no console:
   ```js
   const v = document.querySelector('textarea').value;
   console.log(v.length, v.normalize('NFC').length, v === v.normalize('NFC'));
   ```
   Comprimentos divergentes ⇒ o texto está decomposto e F7 é a correção daquele caso. Custo zero, resposta definitiva.
4. `[paralelo]` Acompanhar por uma semana se o chamado volta.

**Pronto quando:** o operador confirma, ou aparece um sintoma residual — que vira entrada nova neste plano, não plano novo.

#### Status de execução — Fase F8
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| `<br>` no fim do HTML do espelho | Quebrar a paridade `textContent.length === input.length` (D2) e deslocar o caret — o oposto do objetivo | `<br>` é elemento, não texto: não entra no `textContent`. Travado por teste em F0 item 3 e reconfirmado em F1 item 3 |
| `highlightComposerMarkup` roda a cada tecla | Acrescentar trabalho no caminho quente | A mudança é um `endsWith('\n')` e uma concatenação — O(1). F2 **reduz** o custo (menos tags emitidas) |
| F2 remove o negrito ao vivo | Perda de recurso entregue em 20/07; precedente do Slack (revolta pública ao remover markdown do compositor) | É **P2**, decisão de produto explícita. A alternativa P2(b) preserva o recurso |
| A saída do espelho é `dangerouslySetInnerHTML` | Qualquer mudança em `highlightComposerMarkup` é superfície de XSS | O `escapeHtml` de [formatWhatsApp.js:8-15](../web/static/js/utils/formatWhatsApp.js#L8) roda **primeiro** e continua rodando; F0 item 1 trava isso, e [formatWhatsApp.test.js:31](../web/static/js/utils/formatWhatsApp.test.js#L31) já cobre injeção na função irmã |
| `ResizeObserver` (F4) | Observador que não desobserva vaza — precedente real no repo | Cleanup obrigatório no retorno do efeito; revisar junto o de `useChatDayHeader` |
| F7 com `preventDefault` na colagem | Assumir o caminho de inserção reintroduz problema de índice (é o erro de F5, em outra roupa) | Só interceptar quando o texto **realmente** difere em NFC; caso contrário deixar nativo. Nunca normalizar por tecla (D4) |
| F5 muda `composerTokens.js` | O módulo é puro e já tem 26 testes — regressão silenciosa é possível | Rodar a suíte inteira; os casos novos entram **antes** da correção (D7) |
| Modo escuro | Cores novas de realce (F2) ilegíveis no tema escuro | Usar `wa-*`/tokens; conferir com o tema escuro ligado, conforme a regra do CLAUDE.md |
| Dois espelhos, não um | Corrigir só o do chat e esquecer o da conversa nova | D1 põe a correção na função compartilhada; F4 e F6 têm item explícito para o segundo call site |
| Rascunho com **duas abas** do painel abertas | O listener de `storage` zera o mapa em memória ([drafts.js:229-236](../web/static/js/services/drafts.js#L229)) e a re-hidratação pode reescrever o compositor com texto atrasado, perdendo até 400 ms não persistidos | Fora do escopo deste plano, mas **não piorar**: nenhuma fase pode acrescentar escrita programática no campo. Se F9 mexer nos escritores, revalidar este caminho |

**Fora de escopo por construção:** backend, banco, migration, plugin, WebSocket, contrato de API de plugin. Nenhum arquivo `.py` é tocado.

---

## 7. Perguntas em aberto

**P1 — O banco de medição entra no repositório?**
⏸️ **ADIADO** (decidir na F0). O repo não tem `package.json` nem infra de teste com DOM, e foi exatamente essa lacuna que deixou uma família inteira de bugs geométricos passar. Opções: **(a)** manter fora, como ferramenta de diagnóstico documentada em 131 — custo zero, mas a regressão de F1/F2/F4 volta a não ter rede; **(b)** entrar em `tests/frontend/` com venv próprio e Playwright, rodado sob demanda (nunca no boot, como o runner de plugins); **(c)** jsdom — **não serve**, não faz layout, e layout é justamente o que se mede aqui.
**Recomendação:** (b) restrito a um único arquivo de geometria do compositor. É o único jeito de F1 não voltar em seis meses. Se o time recusar a dependência, (a) com o roteiro manual de 131 §7 no checklist de release.

**P2 — F2: como preservar o realce sem alterar métrica?**
✅ **DECIDIDO (2026-08-20): nenhuma das três — apareceu uma quarta.** Manter o realce e trocar a TÉCNICA: `-webkit-text-stroke` para o negrito (engorda o traço sem mexer na largura de avanço) e tarja de fundo para o código (mantendo a família tipográfica). Medido: **0 erros em todos os casos**, nos dois motores, sem perder o negrito ao vivo. O custo é o código deixar de ser monoespaçado — inevitável, porque trocar de família É trocar a métrica. Registro das opções originais: Opções: **(a)** trocar `<b>`/`<code monospace>` por cor + fundo, mantendo o esmaecimento — resolve F2 **e** F3 estruturalmente, ao custo de o negrito não aparecer em negrito enquanto se digita; **(b)** manter o realce e **não repintar o espelho enquanto `isComposing`** (`compositionstart`/`compositionend`) — preserva o recurso e resolve F3, mas **não** resolve F2 (dois negritos continuam desalinhando 17 caracteres); **(c)** as duas.
**Recomendação:** (a). É a única que honra D3. O padrão CSS proíbe negrito em *highlight* exatamente porque afeta layout, e enquanto o espelho alterar métrica nenhum ajuste de padding resolve. Se o negrito ao vivo for inegociável para o produto, (c) — mas então F2 fica reduzida ao `<code>`, e a divergência do `<b>` vira dívida aceita e **documentada**.

**P3 — Vale medir em Firefox e Safari?**
✅ **PARCIALMENTE RESPONDIDA (2026-08-20): Firefox medido, resultado idêntico.** F1, F2 e F7 foram medidas em Chromium 151 **e** Firefox 153, com os mesmos números em todas as tabelas — inclusive o bug ANTES da correção (258/238, +71) e o Backspace sobre NFD (`está` → `esta` nos dois). **Safari continua sem medição** (o WebKit não está instalado no banco de medição). Registro original: Toda a medição foi em Chromium. O comportamento de `pre-wrap` com quebra final é de especificação (baixo risco de divergir), mas a largura da barra de rolagem e o Backspace em NFD variam entre motores. **Recomendação:** descobrir na F8 qual navegador os operadores usam; medir um segundo motor só se não for Chrome/Edge.

**P5 — F9 (desfazer morto) e F10 (arrastar texto) entram neste plano?**
⏸️ **ADIADO** (decidir na F8, com a resposta do operador). São **confirmados**, da mesma família, mas nenhum dos dois é necessário para explicar o chamado. **F9** custa médio (trocar os 4 escritores por `insertText`, que é justamente o caminho que preserva o histórico nativo) e devolve o Ctrl+Z, hoje quebrado desde o primeiro emoji. **F10** é uma linha se a decisão for interceptar, mas pode ser comportamento desejado — arrastar texto é recurso nativo do campo.
**Recomendação:** F9 entra numa Wave 4 **depois** de F8 confirmar que o chamado fechou, para não misturar correção de sintoma com melhoria; F10 só se o operador disser que arrasta texto no campo.

**P4 — O relato de "mensagem duplicada" entra aqui?**
⏸️ **ADIADO — provavelmente NÃO.** [131 §9](131-investigacao-cursor-compositor.md) o deixou em aberto com duas explicações concorrentes (envio duplo real × bolha otimista não reconciliada), e nenhuma tem relação com o caret. **Recomendação:** decidir com uma consulta ao banco (duplicata que some no F5 é a bolha; que sobrevive é envio duplo) e abrir plano próprio se for envio duplo.

---

## 8. Apêndice — arquivos-chave

**Núcleo da correção (frontend, `web/static/js/`)**

| Arquivo | Fases | Papel |
|---|---|---|
| [utils/formatWhatsApp.js](../web/static/js/utils/formatWhatsApp.js) | F0, F1, F2 | `highlightComposerMarkup` — fonte única dos dois espelhos |
| [utils/composerMirror.js](../web/static/js/utils/composerMirror.js) | F4 | `syncMirror`; nenhuma mudança de contrato prevista |
| [components/contacts/Composer.js](../web/static/js/components/contacts/Composer.js) | F4, F6 | espelho + textarea do chat |
| [components/contacts/NewConversationModal.js](../web/static/js/components/contacts/NewConversationModal.js) | F4, F6 | o **segundo** espelho, fácil de esquecer |
| [services/composerTokens.js](../web/static/js/services/composerTokens.js) | F5 | `replaceToken` |
| [components/contacts/hooks/useTokenAutocomplete.js](../web/static/js/components/contacts/hooks/useTokenAutocomplete.js) | F5 | call sites do splice |
| [components/contacts/hooks/useMediaUpload.js](../web/static/js/components/contacts/hooks/useMediaUpload.js) | F7 | `handlePaste` |
| [components/contacts/hooks/useComposer.js](../web/static/js/components/contacts/hooks/useComposer.js) | F5 | `updateMenus`, `insertEmoji` (o padrão certo a copiar) |

**Testes**

| Arquivo | Fases |
|---|---|
| [utils/formatWhatsApp.test.js](../web/static/js/utils/formatWhatsApp.test.js) (ou `composerHighlight.test.js` novo) | F0, F1, F2 |
| [services/composerTokens.test.js](../web/static/js/services/composerTokens.test.js) | F5 |
| `tests/frontend/` (a criar — **P1**) | F1, F2, F4 |

**Referência:** [131-investigacao-cursor-compositor.md](131-investigacao-cursor-compositor.md) — medições, prova de causalidade, falsos positivos e as armadilhas do banco de medição.

---

## 9. Checklist de verificação

Aplicável a **cada** fase antes de fechar:

- [ ] `node --test web/static/js/utils/*.test.js web/static/js/services/*.test.js` — verde
- [ ] Nenhum arquivo `.py` tocado (este plano é 100% frontend; se um `.py` mudou, o escopo vazou)
- [ ] `venv/bin/python -m pytest` com `WHATSBOT_TEST_DB_URL` — verde (rede de segurança; não deve mudar nada)
- [ ] Reload duro do painel + navegação back/forward: compositor hidrata o rascunho e o caret continua correto
- [ ] Modo escuro ligado: realce do compositor legível nos dois temas (só relevante em F2)
- [ ] Os **dois** espelhos conferidos: chat e "Nova conversa"
- [ ] Rascunho por conversa intacto: digitar, trocar de conversa, voltar — texto e caret preservados
- [ ] Bandeja de anexos (plano 124) intacta: colar imagem com texto no campo continua usando o texto como legenda
- [ ] @menção e /atalho continuam funcionando em grupo e em nota privada (F5 mexe nesse caminho)
- [ ] Envio real: `**negrito**` continua chegando ao WhatsApp como `*negrito*` (o `toWhatsAppMarkup` de [useComposer.js:228](../web/static/js/components/contacts/hooks/useComposer.js#L228) não foi afetado)
- [ ] Roteiro manual de [131 §7](131-investigacao-cursor-compositor.md) não reproduz o defeito
- [ ] Um refactor por commit; nunca avançar com teste vermelho não explicado
