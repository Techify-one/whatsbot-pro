# Plano 138 — Arrastar a barra do áudio para retroceder volta a funcionar (scrubber por pointer events)

> **Status:** EXECUTADO (2026-08-24) — código completo e suíte verde; **falta a validação por gesto no navegador** (F5) · **Data:** 2026-08-24 · **Escopo:** pequeno/médio (**1 componente de 116 linhas** + 1 módulo puro novo + testes; **zero backend**, **zero migration**, **zero plugin**)
> **Origem:** reclamação de operador — "chego na metade do áudio, arrasto a barra um pouco para a esquerda para retroceder e simplesmente não volta, o áudio continua tocando". Acontece em **todas as conversas**. **Método:** leitura do código real com `arquivo:linha` verificados + prova empírica do lado servidor (`Range` → 206) + `ffprobe` nos arquivos reais + `git log` do componente.
> **O quê/porquê:** não é o servidor, não é o arquivo e não é o navegador — os três foram **medidos e descartados** (§4). O player de áudio é um componente próprio que **nunca teve arraste**: ele tem só um `onClick` numa faixa de **4 pixels** de altura ([AudioPlayer.js:97](../web/static/js/components/contacts/AudioPlayer.js#L97)). Num gesto de arrastar, o `mouseup` quase sempre sai dos 4px, o `click` é entregue ao contêiner pai (que não tem handler) e **nada acontece** — o áudio segue tocando. O plano troca o `onClick` por um scrubber de verdade (`pointerdown/move/up` + `setPointerCapture`), com a matemática num módulo puro testável.
>
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0 — Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 ✅ (2026-08-24) | **A causa é frontend.** Servidor, MIME e integridade dos arquivos foram medidos e estão corretos. | Nenhuma linha de `server/` entra neste plano. Ver §4 (falsos positivos) — não refazer essa investigação. |
| D2 ✅ (2026-08-24) | **Pointer Events** (`pointerdown/pointermove/pointerup` + `setPointerCapture`), não `mousedown`+listener no `document`, nem `<input type="range">`. | Um só caminho cobre mouse, toque e caneta; a captura mantém os eventos chegando ao elemento **mesmo quando o dedo sai dos 4px**, que é exatamente o defeito. `<input type=range>` foi descartado: estilizar `::-webkit-slider-thumb`/`::-moz-range-thumb` nos dois temas custa mais que o handler. |
| D3 ✅ (2026-08-24) | **A altura VISUAL da barra continua 4px.** O alvo de toque cresce por padding transparente. | A barra vive dentro da bolha da mensagem; engrossá-la mexe no layout de três superfícies (§3.2). Padding + margem negativa aumenta o alvo sem deslocar um pixel. |
| D4 ✅ (2026-08-24) | **A matemática do seek sai do componente** para um módulo puro `services/audioScrub.js` com `.test.js` (`node --test`). | Segue o precedente do repo (`chatCalendar.js`, `composerSubmit.js`, `mediaLimits.js`): a regra é testável sem DOM, e o componente vira só fiação. É o que dá cobertura a um bug que nunca teve teste nenhum. |
| D5 ✅ (2026-08-24) | **Enquanto o dedo está arrastando, o `timeupdate` NÃO manda na posição exibida.** | Hoje o áudio segue tocando durante o gesto e a barra corre embaixo do cursor. Sem essa regra, o arraste continua parecendo quebrado mesmo depois de consertado. |
| D6 ✅ (2026-08-24) | **Escopo fechado no gesto + nos defeitos que fazem um seek bem-sucedido PARECER falho** (§3.3). | O rótulo que mostra a duração no lugar da posição ([AudioPlayer.js:104](../web/static/js/components/contacts/AudioPlayer.js#L104)) entra: sem ele, o operador retrocede com sucesso e o número na tela não muda — e ele reporta o mesmo bug de novo. |
| D7 ✅ (2026-08-24) | **Sem tokens de cor novos.** Reuso de `wa-teal` / `wa-border` / `wa-secondary`. | [themeContrast.js](../web/static/js/services/themeContrast.js) não é tocado e não há regra nova a satisfazer. |

**Princípio fixo:** o player toca áudio de cliente real dentro do histórico. Entre "um gesto ambíguo não fazer nada" e "um gesto ambíguo pular o áudio para um ponto errado", prefere-se **não fazer nada** — todo cálculo de posição é clampado e todo estado inválido (duração desconhecida) desabilita o controle **visivelmente**, em vez de falhar em silêncio como hoje.

---

## 1 — Resumo executivo

O `AudioPlayer` é um player desenhado à mão (116 linhas) que substitui os controles nativos. A barra de progresso é um `<div class="relative h-[4px] …" onClick=${seek}>` — **4 pixels de altura, só `onClick`, nenhum `pointerdown`**.

Num clique perfeitamente parado dentro da faixa de 4px, funciona. Num **arraste** — que é o gesto que todo mundo usa, porque é o que o WhatsApp e o Telegram fazem — o `mousedown` sai de um alvo e o `mouseup` cai em outro; o navegador entrega o `click` ao **ancestral comum**, que é a coluna flex de [AudioPlayer.js:96](../web/static/js/components/contacts/AudioPlayer.js#L96) — **sem handler**. O `seek` nunca roda. O áudio continua tocando. É exatamente o relato.

O `git log` do arquivo tem **dois commits** e o segundo é de tema — o componente nasceu assim em `e0c8a45`. A suspeita do usuário ("às vezes isso nunca funcionou direito") está correta: **nunca funcionou**, e a intermitência tem explicação (§3.1, a bolinha invisível de 12px).

A correção é uma só: transformar a barra num scrubber com Pointer Events e captura de ponteiro, com a matemática num módulo puro testado. Junto vão três defeitos que fazem um seek **bem-sucedido** parecer que falhou (§3.3).

---

## 2 — Como funciona hoje (mapa verificado)

### 2.1 O componente e seus três consumidores

| Superfície | Arquivo:linha | Fonte do áudio |
|---|---|---|
| Bolha de mensagem no chat | [MediaContent.js:64-66](../web/static/js/components/contacts/MediaContent.js#L64-L66) | `media_path` do servidor (`.oga` inbound) ou blob local otimista |
| Card de **nota privada** | [SystemMessageCard.js:85-88](../web/static/js/components/contacts/SystemMessageCard.js#L85-L88) | idem, dentro de `min-w-[220px] max-w-[280px]` |
| Prévia da **bandeja de anexo** | [MediaTray.js:189-192](../web/static/js/components/contacts/MediaTray.js#L189-L192) | `previewUrl` = `URL.createObjectURL` do blob do `opus-recorder` ([useAudioRecorder.js:64](../web/static/js/components/contacts/hooks/useAudioRecorder.js#L64)) |

Os três passam `isLocalBlob`; o componente monta a URL em [AudioPlayer.js:16](../web/static/js/components/contacts/AudioPlayer.js#L16).

### 2.2 O caminho do seek, linha a linha

| Passo | Arquivo:linha | O que faz |
|---|---|---|
| Estado de posição | [AudioPlayer.js:13](../web/static/js/components/contacts/AudioPlayer.js#L13) `currentTime` | alimentado só por `timeupdate` |
| Listeners | [AudioPlayer.js:18-40](../web/static/js/components/contacts/AudioPlayer.js#L18-L40) | ⚠️ deps `[]` — ver §3.3 |
| ⚠️ **O handler do seek** | [AudioPlayer.js:54-60](../web/static/js/components/contacts/AudioPlayer.js#L54-L60) | `a.currentTime = (x / rect.width) * duration` — **sem clamp**, e só chamado por `click` |
| ⚠️ **O alvo de 4px** | [AudioPlayer.js:97](../web/static/js/components/contacts/AudioPlayer.js#L97) | `h-[4px] … cursor-pointer group` + `onClick=${seek}` |
| Preenchimento | [AudioPlayer.js:98-99](../web/static/js/components/contacts/AudioPlayer.js#L98-L99) | `absolute`, `transition-[width] duration-100` |
| ⚠️ **A bolinha invisível** | [AudioPlayer.js:100-101](../web/static/js/components/contacts/AudioPlayer.js#L100-L101) | 12px, `opacity-0 group-hover:opacity-100` |
| ⚠️ Rótulo de tempo | [AudioPlayer.js:104](../web/static/js/components/contacts/AudioPlayer.js#L104) | `fmt(playing ? currentTime : duration)` |

### 2.3 Por que o `click` não chega ao handler

```
┌─ div.flex-1.flex-col            ← [AudioPlayer.js:96] SEM onClick  ◀── o click cai AQUI
│  ┌─ div.h-[4px].group           ← [AudioPlayer.js:97] onClick=seek   (4px de altura)
│  │   ├─ div (preenchimento, absolute, h-full)
│  │   └─ div (bolinha, 12px, opacity-0)     ← recebe ponteiro mesmo invisível
│  └─ div.flex.justify-between    ← [AudioPlayer.js:103] rótulo
```

Regra do DOM: o `click` é despachado no **ancestral comum mais próximo** do alvo do `mousedown` e do alvo do `mouseup`. Arrastar 3px para baixo — trivial num alvo de 4px — muda o alvo do `mouseup` para a coluna pai, e o `click` é entregue a um elemento **sem handler**. Silêncio total: nem erro, nem log, nem feedback.

---

## 3 — Inventário do que muda

### 3.1 A causa-raiz e o que a torna intermitente

| # | Defeito | Arquivo:linha | Por que morde | Risco | Esforço |
|---|---|---|---|---|---|
| C1 | **Nenhum suporte a arraste.** Só `onClick`. | [AudioPlayer.js:97](../web/static/js/components/contacts/AudioPlayer.js#L97) | O gesto natural (e o único que existe no WhatsApp/Telegram) não é escutado. Causa direta do relato. | — | M |
| C2 | **Alvo de 4px de altura.** | [AudioPlayer.js:97](../web/static/js/components/contacts/AudioPlayer.js#L97) | Mesmo um *clique* exige precisão de 4px; num arraste, garante que o `mouseup` escape. | — | S |
| C3 | **A bolinha de 12px é `opacity-0`.** | [AudioPlayer.js:100](../web/static/js/components/contacts/AudioPlayer.js#L100) | Elemento invisível **continua recebendo ponteiro**. Perto da cabeça de leitura o alvo vira 12px; longe dela, 4px. É isto que produz o "às vezes funciona" e fez o bug sobreviver — quem testa clica perto do playhead. | — | S |
| C4 | **Sem feedback ao vivo.** O `timeupdate` continua mandando na barra durante o gesto. | [AudioPlayer.js:22](../web/static/js/components/contacts/AudioPlayer.js#L22), [:69](../web/static/js/components/contacts/AudioPlayer.js#L69) | A barra **foge do cursor** enquanto se arrasta. Reforça a leitura de "não voltou". | baixo | S |

### 3.2 A correção

| # | Mudança | Onde | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|
| F-a | Módulo puro `audioScrub.js` | **novo** `web/static/js/services/audioScrub.js` | `ratioFromPointer(clientX, rect)` (clampado 0–1) · `timeFromRatio(ratio, duration)` (clampado, rejeita `NaN`/`Infinity`) · `progressPercent({currentTime, duration, scrubRatio})` (o arraste vence — D5) · `isSeekable(duration)` · `nudge(currentTime, delta, duration)` para o teclado | baixo | S |
| F-b | Scrubber por Pointer Events | [AudioPlayer.js:97-102](../web/static/js/components/contacts/AudioPlayer.js#L97-L102) | `onPointerDown` → `setPointerCapture(e.pointerId)` + seek imediato (o clique simples continua funcionando pelo mesmo caminho); `onPointerMove` só com captura ativa; `onPointerUp`/`onPointerCancel` → commit + `releasePointerCapture`. `touch-action: none` no alvo. | médio | M |
| F-c | Alvo de toque maior sem mexer no visual | [AudioPlayer.js:97](../web/static/js/components/contacts/AudioPlayer.js#L97) | Envelope `py-[8px] -my-[8px]` (ou `::before` esticado) em volta da barra de 4px — D3. | baixo | S |
| F-d | Bolinha visível durante o arraste | [AudioPlayer.js:100](../web/static/js/components/contacts/AudioPlayer.js#L100) | manter `group-hover`, acrescentar o estado "arrastando" (`opacity-100`) — senão o alvo some no meio do gesto no toque, onde não existe hover | baixo | S |
| F-e | Clamp do destino | [AudioPlayer.js:59](../web/static/js/components/contacts/AudioPlayer.js#L59) | passa a vir de `timeFromRatio` (F-a). Hoje um ponteiro sobre a saliência esquerda da bolinha gera `x` **negativo**. | baixo | S |
| F-f | Estado não-arrastável explícito | [AudioPlayer.js:56](../web/static/js/components/contacts/AudioPlayer.js#L56) | `if (!duration) return` é um **retorno mudo**: antes do `loadedmetadata` o clique não faz nada e não avisa. Trocar por `isSeekable()` + `cursor-default` + `aria-disabled`. | baixo | S |

### 3.3 Defeitos que fazem um seek **bem-sucedido** parecer falho (D6)

| # | Defeito | Arquivo:linha | Sintoma | Risco | Esforço |
|---|---|---|---|---|---|
| G1 | **O rótulo mostra a duração quando pausado.** `fmt(playing ? currentTime : duration)` | [AudioPlayer.js:104](../web/static/js/components/contacts/AudioPlayer.js#L104) | Retrocedeu com o áudio pausado → o número na tela **não muda**. Passa a exibir posição/duração. O `justify-between` de [:103](../web/static/js/components/contacts/AudioPlayer.js#L103) já foi escrito para dois filhos e hoje tem um só — resquício. | baixo | S |
| G2 | **`useEffect` com deps `[]` + `<source>` trocado.** | [AudioPlayer.js:18-40](../web/static/js/components/contacts/AudioPlayer.js#L18-L40) + [:74-78](../web/static/js/components/contacts/AudioPlayer.js#L74-L78) | Mudar o `src` de um `<source>` **não recarrega** o `<audio>` sem `a.load()`. Quando a bolha otimista (`_isLocalBlob: true`, [useMediaUpload.js:467](../web/static/js/components/contacts/hooks/useMediaUpload.js#L467)) é reconciliada para o caminho do servidor, duração e posição ficam do blob antigo. | médio | S |
| G3 | **`playbackRate` se perde.** Só é aplicado dentro de `cycleSpeed`. | [AudioPlayer.js:51](../web/static/js/components/contacts/AudioPlayer.js#L51) | Depois de um `load()`/troca de fonte o chip continua dizendo `2x` e o áudio toca em `1x`. Reaplicar no `loadedmetadata`. | baixo | S |
| G4 | **`durationchange` não é escutado.** | [AudioPlayer.js:26-30](../web/static/js/components/contacts/AudioPlayer.js#L26-L30) | Em Ogg a duração pode ser **refinada** depois do `loadedmetadata`; a barra fica calibrada errada até o fim. | baixo | S |

### 3.4 Acessibilidade (entra junto porque é o mesmo elemento)

Hoje a barra é uma `<div>` sem `role`, sem `tabIndex` e sem ARIA: **não há como retroceder pelo teclado**. Com o elemento sendo reescrito, `role="slider"` + `tabIndex={0}` + `aria-valuemin/max/now/valuetext` + `←/→` (±5s) e `Home`/`End` são acréscimo marginal — e dão ao operador uma segunda via caso o gesto ainda escape em algum dispositivo.

---

## 4 — Falsos positivos descartados (não reinvestigar)

| Suspeita | Como foi medida | Veredito |
|---|---|---|
| **Servidor não suporta `Range`** (o suspeito nº 1 para "não dá para retroceder") | `StaticFiles` sobre `statics/` real: `GET` → `200 · Accept-Ranges: bytes · audio/ogg · 1 112 934 B`; `Range: bytes=1000-2000` → **`206 · Content-Range: bytes 1000-2000/1112934`** | ❌ **Descartado.** O servidor responde 206 corretamente. |
| `FileResponse` da rota `/statics/outbox/{name}` não faz Range | Starlette **1.3.1**; `Range` presente na implementação de `FileResponse`. Rota em [server/app.py:522-536](../server/app.py#L522-L536) | ❌ Descartado. |
| Middleware quebrando o 206 | Não há `GZipMiddleware` no app; os `@app.middleware("http")` de [server/app.py:566](../server/app.py#L566), [:637](../server/app.py#L637), [:652](../server/app.py#L652), [:682](../server/app.py#L682) preservam status e headers | ❌ Descartado. |
| **MIME errado** para `.oga` | `mimetypes` → `.oga`/`.ogg`/`.opus` = `audio/ogg`; `audio/ogg` está na allow-list inline de [server/app.py:515-521](../server/app.py#L515-L521) | ❌ Descartado. |
| **Arquivo sem duração / sem tabela de busca** | `ffprobe` no `.oga` real: `codec_name=opus · format_name=ogg · duration=171.860000` | ❌ Descartado — duração conhecida e Ogg com granulepos é buscável. |
| **Navegador não consegue buscar nesses arquivos** | O `<video controls preload="metadata">` de [MediaContent.js:34](../web/static/js/components/contacts/MediaContent.js#L34) usa os **controles nativos**, os **mesmos arquivos** e o **mesmo servidor** — e busca normalmente | ❌ Descartado. Isola o defeito no `AudioPlayer`. |
| A ordem dos `<source>` (`audio/wav` antes de `audio/ogg`) | [AudioPlayer.js:75-77](../web/static/js/components/contacts/AudioPlayer.js#L75-L77) — três `<source>` para a **mesma URL** com três `type` diferentes | ⚠️ **Não é a causa** (o navegador confia no `Content-Type` real para decodificar), mas é sujeira que pode custar uma tentativa de carga extra. Vira limpeza opcional em F3 — ver **P4**. |
| `duration === Infinity` no blob do gravador | O `opus-recorder` ([useAudioRecorder.js:41-47](../web/static/js/components/contacts/hooks/useAudioRecorder.js#L41-L47)) entrega um **OGG/Opus completo** de uma vez, não um stream do `MediaRecorder` | ⚠️ **Provavelmente não ocorre**, mas o guard `isSeekable()` (F-f) cobre de graça. A confirmar em F5, superfície "bandeja". |
| `statics/outbox/1781999691895.ogg` está corrompido (`CRC mismatch! End of file`) | `ffprobe` | ⚠️ **Real, mas fora de escopo** — é artefato da suíte de testes (trio `.png`/`.ogg`/`_relatorio.pdf` repetido). Não afeta áudio inbound. Registrado em **P5**. |

---

## 5 — Fases e paralelização

```
WAVE 0   F1 (módulo puro + testes) 🟢  ·  F0 (roteiro de reprodução) 🟢
            │                                    └─ independente, serve de critério p/ F5
            │ (barreira: F1 bloqueia F2 — o componente consome o módulo)
WAVE 1   F2 (scrubber por pointer events) 🔴          [depende de: F1]
            │
WAVE 2   F3 (correções de estado: rótulo, load(), rate, durationchange) 🔴   [depende de: F2]
            │
WAVE 3   F4 (teclado + ARIA) 🔴                        [depende de: F2]
            │
WAVE 4   F5 (validação nas 3 superfícies + modo escuro + toque) 🔴  [depende de: F3, F4]
```

⚠️ **Honestidade sobre paralelismo:** este plano tem **pouco** a paralelizar, e isso é uma propriedade do alvo, não uma omissão. F2/F3/F4 editam **o mesmo arquivo de 116 linhas** — despachá-las juntas produz conflito de edição, não velocidade. O único paralelismo real está na WAVE 0.

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F1** | módulo puro `audioScrub.js` + `.test.js` | 🟢 | baixo | `node --test` verde; nenhum import de DOM no módulo |
| 0 | **F0** | roteiro de reprodução/validação manual | 🟢 | baixo | roteiro escrito, bug reproduzido **antes** da correção |
| 1 | **F2** | scrubber (pointer events + captura + alvo) | 🔴 | médio | arrastar para a esquerda retrocede, nas 3 superfícies |
| 2 | **F3** | correções de estado (§3.3) | 🔴 | baixo | rótulo acompanha o seek com o áudio **pausado** |
| 3 | **F4** | teclado + ARIA | 🔴 | baixo | `Tab` até a barra, `←` retrocede 5s |
| 4 | **F5** | validação final | 🔴 | baixo | checklist §8 inteiro marcado |

---

### Fase F0 — Reproduzir e congelar o roteiro 🟢

**Objetivo:** provar o bug antes de tocar no código, e deixar escrito o que F5 vai reverificar.

**Itens** *(todos `[paralelo]` com F1)*
1. Abrir uma conversa com áudio recebido; tocar até a metade.
2. **Gesto A (clique parado)** exatamente sobre a faixa de 4px → anotar se busca.
3. **Gesto B (arraste para a esquerda)** soltando ~5px abaixo da barra → anotar (esperado hoje: **nada acontece, o áudio continua**).
4. **Gesto C (arraste começando na bolinha)** → anotar (esperado: intermitente — é o C3 da §3.1).
5. Repetir A/B nas 3 superfícies da §2.1 e no **toque** (celular/DevTools em modo dispositivo).

**Pronto quando:** o Gesto B falha de forma reprodutível e está registrado abaixo. Se **não** falhar no ambiente do executor, **pare** e registre — a hipótese muda.

#### Status de execução — Fase F0
**Estado:** ✅ Concluída (com ressalva de método)
- **O que foi feito:** o roteiro dos Gestos A/B/C está escrito no corpo da fase e foi mantido como critério da F5. A reprodução, porém, **não foi feita por gesto num navegador** — o executor não tinha painel aberto.
- **Como foi feito / decisões:** a prova ficou no NÍVEL DO CÓDIGO, e é conclusiva: (1) `grep -rn "setPointerCapture|pointerdown|touch-action" web/static/js/` devolvia **zero** ocorrências no repositório inteiro — não havia suporte a arraste em lugar nenhum; (2) o único handler era `onClick` num alvo `h-[4px]` ([AudioPlayer.js:97] original), e o `click` do DOM é despachado no ancestral comum do mousedown com o mouseup, que ali é a coluna flex **sem handler**; (3) `git log` do arquivo tem dois commits, o segundo de tema — nasceu assim.
- **Problemas / pendências:** ⚠️ **A validação por GESTO continua devendo** e é o item que só o usuário pode fechar — vale para a F5 inteira. Nada no restante do plano depende dela para estar correto, mas o "pronto" final depende.
- **Verificação:** estática (grep + leitura + `git log`). Os falsos positivos da §4 (Range 206, MIME, `ffprobe`, `<video controls>` nativo) já haviam sido medidos na fase de planejamento e não foram refeitos.

---

### Fase F1 — Módulo puro `audioScrub.js` + testes 🟢 `[bloqueia: F2]`

**Objetivo:** tirar toda a aritmética de posição do componente e cobri-la com teste, já que hoje ela não tem nenhum.

**Itens**
1. `[paralelo]` Criar `web/static/js/services/audioScrub.js` — sem `import` de Preact e sem tocar em `document`. Assinaturas pretendidas:
   ```js
   export function isSeekable(duration)                    // finito && > 0
   export function ratioFromPointer(clientX, rect)         // → 0..1, clampado
   export function timeFromRatio(ratio, duration)          // → segundos, clampado; 0 se !isSeekable
   export function progressPercent({ currentTime, duration, scrubRatio })  // scrubRatio vence (D5)
   export function nudge(currentTime, deltaSeconds, duration)              // teclado (F4)
   ```
2. `[paralelo]` Criar `web/static/js/services/audioScrub.test.js` cobrindo: ponteiro **à esquerda** da barra (`x` negativo → 0, o C-e da §3.2) · ponteiro à direita (→ duração) · `rect.width === 0` (não pode virar `NaN`/divisão por zero) · `duration` `0`/`NaN`/`Infinity`/negativa → `isSeekable === false` e `timeFromRatio === 0` · `progressPercent` **ignorando** `currentTime` quando há `scrubRatio` (D5) · `nudge` clampado nas duas pontas.

**Pronto quando:** `node --test web/static/js/services/audioScrub.test.js` verde e o módulo não importa nada do DOM.

#### Status de execução — Fase F1
**Estado:** ✅ Concluída
- **O que foi feito:** criado `web/static/js/services/audioScrub.js` (puro, sem Preact e sem DOM) e `web/static/js/services/audioScrub.test.js`.
- **Como foi feito / decisões:** a superfície planejada saiu inteira (`isSeekable`, `ratioFromPointer`, `timeFromRatio`, `progressPercent`, `nudge`) e ganhou **duas funções a mais**, ambas exigidas pela D5/G1: `displayTime` (posição EXIBIDA — durante o arraste é a do arraste, não a do `timeupdate`; sem ela o rótulo contaria uma história diferente da barra logo acima) e `formatClock` (o `fmt` local do componente, trazido para cá porque o rótulo passou a formatar DOIS valores). `isSeekable` usa `Number.isFinite` e não o global — o global faz coerção e deixaria `"30"` passar.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --test web/static/js/services/audioScrub.test.js` → **22/22 verdes**. Casos que cobrem os defeitos reais: `x` negativo sobre a saliência de 6px da bolinha → 0; `rect.width === 0` → 0 em vez de `NaN`; duração `0`/`NaN`/`Infinity`/negativa/string → `isSeekable false` e `timeFromRatio 0`; **`scrubRatio === 0` vencendo o `currentTime`** (a armadilha do `||`); `nudge` clampado nas duas pontas.

---

### Fase F2 — Scrubber por Pointer Events 🔴 `[depende de: F1]`

**Objetivo:** o arraste passa a retroceder. É a fase que resolve o relato.

**Itens** *(todos `[sequencial]` — mesmo arquivo)*
1. Estado novo `scrubRatio` (`null` = não está arrastando) e ref do `pointerId` capturado.
2. Substituir `onClick=${seek}` ([AudioPlayer.js:97](../web/static/js/components/contacts/AudioPlayer.js#L97)) por:
   - `onPointerDown` — `if (!isSeekable(duration)) return;` · `e.currentTarget.setPointerCapture(e.pointerId)` · calcula e **aplica** o seek já no `down` (é o que preserva o clique simples num só caminho de código) · `e.stopPropagation()` (ver R4).
   - `onPointerMove` — só quando há captura; atualiza `scrubRatio` e aplica `a.currentTime` (**P1**).
   - `onPointerUp` / `onPointerCancel` — commit final, `releasePointerCapture`, `scrubRatio = null`.
   - **Remover** o `onClick`: com o seek no `pointerdown`, mantê-lo produz **seek duplo**.
3. `style="touch-action:none"` no alvo — sem isso o navegador móvel trata o arraste horizontal como rolagem e **rouba o gesto** (o bug voltaria só no celular).
4. `[F-c]` Envolver a barra de 4px num alvo de ~20px com `py-[8px] -my-[8px]`. ⚠️ Usar valor arbitrário **simples**: o Tailwind runtime vendorizado falha **calado** com função aninhada em valor arbitrário (gotcha do `CLAUDE.md`, precedente `MediaTray`). Na dúvida, `style` inline.
5. `[F-d]` A bolinha ([AudioPlayer.js:100](../web/static/js/components/contacts/AudioPlayer.js#L100)) fica `opacity-100` enquanto `scrubRatio !== null` — no toque não existe `:hover` e ela sumiria durante o gesto.
6. `[F-b/D5]` `progress` ([AudioPlayer.js:69](../web/static/js/components/contacts/AudioPlayer.js#L69)) passa a sair de `progressPercent({...})`, com `scrubRatio` vencendo o `currentTime`.
7. Enquanto `scrubRatio !== null`, **desligar** o `transition-[width] duration-100` de [AudioPlayer.js:98](../web/static/js/components/contacts/AudioPlayer.js#L98) — 100ms de interpolação faz a barra "arrastar atrás" do dedo.
8. `[F-f]` Sem duração utilizável: `cursor-default`, sem handlers, `aria-disabled="true"` — em vez do `return` mudo de [AudioPlayer.js:56](../web/static/js/components/contacts/AudioPlayer.js#L56).
9. Liberar a captura no cleanup do `useEffect` (desmontar no meio do gesto não pode deixar ponteiro capturado).

**Pronto quando:** com o áudio tocando, arrastar a barra para a esquerda **retrocede** e o áudio continua da nova posição; o gesto funciona soltando **fora** da faixa de 4px; funciona no toque; o clique simples continua funcionando; e o Gesto B do roteiro F0 passa nas 3 superfícies da §2.1.

#### Status de execução — Fase F2
**Estado:** ✅ Concluída
- **O que foi feito:** `AudioPlayer.js` — `onClick=${seek}` e a função `seek` **removidos**; entram `onPointerDown/Move/Up/Cancel` com `setPointerCapture`, o estado `scrubRatio` (+ ref espelho), o envelope de toque, o `progressPercent` no lugar da conta inline e o cleanup de captura. `MediaContent.js` e `MessageBubble.js` ganharam o repasse de `selectionMode` (P3).
- **Como foi feito / decisões:** **P1 = (a) ao vivo**, com throttle de `requestAnimationFrame` (`scheduleSeek`) — no máximo um seek por quadro, em vez de dezenas por segundo num Ogg. **P2 = (a) segue tocando** (WhatsApp). **P3 = (a) inerte em modo seleção**, via prop `disabled` propagada `MessageBubble` → `MediaContent` → `AudioPlayer`: são duas linhas e evitam a faixa de 20px onde clicar não marcaria a mensagem. `pointercancel` commita a ÚLTIMA razão conhecida (o evento não traz coordenada confiável), e `applyTime` embrulha `a.currentTime` em `try/catch` para uma exceção não deixar a captura pendurada.
- **Problemas / pendências:** ⚠️ **Custou um incidente durante a execução**: a primeira versão pôs a explicação do envelope num comentário `<!-- … -->` DENTRO do `` html`…` ``, com crases em volta de `touch-action`. A crase fecha o template — o módulo quebrou na hora. O texto foi movido para comentário JS **fora** do template. É a armadilha que `htmTemplates.test.js` existe para pegar.
- **Verificação:** `node --check --input-type=module` OK; `node --test web/static/js/services/htmTemplates.test.js` → 2/2 verdes. **Falta a validação por gesto** (ver F5).

---

### Fase F3 — Correções de estado que fazem o seek parecer falho 🔴 `[depende de: F2]`

**Objetivo:** garantir que um seek bem-sucedido **apareça** como tal (D6).

**Itens** *(todos `[sequencial]` — mesmo arquivo)*
1. `[G1]` [AudioPlayer.js:103-105](../web/static/js/components/contacts/AudioPlayer.js#L103-L105): trocar `fmt(playing ? currentTime : duration)` por **posição à esquerda / duração à direita** — o `justify-between` já espera dois filhos. Durante o arraste, exibir a posição **do arraste**, não a do `timeupdate`.
2. `[G2]` [AudioPlayer.js:18-40](../web/static/js/components/contacts/AudioPlayer.js#L18-L40): acrescentar `audioSrc` às deps e chamar `a.load()` quando a fonte muda; zerar `currentTime`/`duration`/`scrubRatio` no reset.
3. `[G3]` Reaplicar `SPEEDS[speedIdx]` em `playbackRate` no `loadedmetadata` e após `load()`.
4. `[G4]` Escutar `durationchange` além de `loadedmetadata`.
5. `[opcional — P4]` Colapsar os três `<source>` ([AudioPlayer.js:74-78](../web/static/js/components/contacts/AudioPlayer.js#L74-L78)) num `src` único no `<audio>`.

**Pronto quando:** pausar no meio, retroceder e ver **o número mudar**; enviar um áudio gravado e, ao virar mensagem confirmada, a duração continuar correta; trocar para `2x`, deixar reconciliar e o áudio realmente tocar em `2x`.

#### Status de execução — Fase F3
**Estado:** ✅ Concluída
- **O que foi feito:** G1 — o rótulo virou **posição à esquerda / duração à direita** via `displayTime` + `formatClock` (o `fmt` local saiu). G2 — efeito próprio em `[audioSrc]` que chama `a.load()` e zera posição/duração/`scrubRatio`. G3 — `playbackRate` reaplicado no `loadedmetadata` a partir de `speedRef`. G4 — `durationchange` escutado junto com `loadedmetadata`. P4 — os três `<source>` colapsados num `src` único.
- **Como foi feito / decisões:** **desvio do plano em G2**: em vez de acrescentar `audioSrc` às deps do efeito de listeners, criei um efeito SEPARADO. Motivo: as deps novas re-registrariam os cinco listeners a cada troca de fonte sem necessidade — o elemento `<audio>` é o mesmo nó. O efeito novo pula a primeira execução (`firstSrcRef`) para não dar um `load()` gratuito na montagem. Em G3 a velocidade vai num **ref espelho** (`speedRef`) justamente para o listener poder lê-la sem entrar nas deps. **P4 foi confirmado antes de mexer**, como o plano exigia: `ls statics/media statics/outbox` mostra só `.oga`, `.ogg` e `.m4a` de áudio, e os três `<source>` apontavam para a **MESMA URL** — nunca foram cadeia de fallback, eram o mesmo arquivo listado três vezes; o navegador decodifica pelo `Content-Type` real.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --check` OK; `htmTemplates.test.js` verde. **Falta a validação por gesto** (ver F5).

---

### Fase F4 — Teclado e ARIA 🔴 `[depende de: F2]`

**Objetivo:** retroceder sem depender de precisão de ponteiro.

**Itens** *(todos `[sequencial]` — mesmo arquivo)*
1. No alvo: `role="slider"`, `tabIndex={0}`, `aria-label="Posição do áudio"`, `aria-valuemin=0`, `aria-valuemax={duration}`, `aria-valuenow={currentTime}`, `aria-valuetext={fmt(currentTime)}`.
2. `onKeyDown`: `←`/`→` = ∓/±5s via `nudge` (F1) · `Home`/`End` = 0/fim · `Espaço` = play/pause. Chamar `preventDefault` **só** nas teclas tratadas — `Espaço`/setas não podem vazar para a rolagem do chat.
3. Foco visível com o anel padrão do painel (sem token de cor novo — D7).

**Pronto quando:** `Tab` alcança a barra, `←` retrocede 5s e o leitor de tela anuncia a posição.

#### Status de execução — Fase F4
**Estado:** ✅ Concluída
- **O que foi feito:** o envelope ganhou `role="slider"`, `tabIndex` (0 quando buscável, −1 quando não), `aria-label`, `aria-orientation`, `aria-valuemin/max/now/valuetext`, `aria-disabled` e `onKeyDown`. `←`/`→` = ∓/±5s via `nudge`, `Home`/`End` = pontas, `Espaço` = play/pause. Anel de foco com `focus:ring-2 focus:ring-wa-teal/50`.
- **Como foi feito / decisões:** `preventDefault`/`stopPropagation` rodam **só** nas teclas efetivamente tratadas (flag `handled`) — `Espaço` e setas continuam rolando o chat quando o foco não está na barra. Como o `pointerdown` chama `preventDefault`, o elemento **não** recebe foco por clique: o anel fica sendo de teclado, sem token de cor novo (D7). Descartei `cursor-default` no estado não-buscável: é a única classe da mudança sem precedente no repositório, e o cursor padrão de uma `div` já é esse — R5 não vale o risco por zero ganho.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --check` OK. **Leitor de tela não foi exercitado** — entra na pendência de validação manual da F5.

---

### Fase F5 — Validação nas 3 superfícies 🔴 `[depende de: F3, F4]`

**Objetivo:** fechar o roteiro do F0 e não deixar regressão nas superfícies vizinhas.

**Itens** *(todos `[sequencial]`)*
1. Reexecutar os Gestos A/B/C do F0 nas 3 superfícies da §2.1 — mouse **e** toque.
2. Áudio **longo** (o `.oga` real de 171,86s) e áudio **curto** (< 3s).
3. **Modo escuro** ligado nas 3 superfícies (regra do `CLAUDE.md`).
4. **Modo seleção** de mensagem ligado ([MessageBubble.js:62](../web/static/js/components/contacts/MessageBubble.js#L62)): confirmar que arrastar a barra **não** marca/desmarca a mensagem (R4) — e decidir **P3**.
5. Menu de contexto da mensagem ([MessageBubble.js:70](../web/static/js/components/contacts/MessageBubble.js#L70)) continua abrindo com o botão direito sobre a bolha.
6. Vários áudios na mesma conversa: arrastar num **não** mexe nos outros.
7. `node --test web/static/js/services/audioScrub.test.js` + a suíte de módulos puros do frontend.

**Pronto quando:** checklist §8 inteiro marcado.

#### Status de execução — Fase F5
**Estado:** 🟡 Em andamento — **a parte automatizável está verde; a validação por GESTO/tela continua devendo**
- **O que foi feito:** rodada a suíte pura inteira do frontend e as verificações estáticas que substituem parte do roteiro.
- **Como foi feito / decisões:** não há jsdom, `node_modules` nem `package.json` neste repositório (o frontend é vendorizado, sem build step), então **não existe caminho para simular o gesto headless** sem introduzir uma dependência — o que estaria fora do escopo e contra o grão do projeto. Em vez de fingir cobertura, os itens 1–6 do roteiro ficam explicitamente **em aberto para o usuário**.
- **Problemas / pendências:** ⚠️ **Continuam devendo, e só podem ser fechados no navegador:** (1) Gestos A/B/C nas 3 superfícies, mouse **e** toque; (2) áudio longo (~172s) e curto (<3s); (3) modo escuro nas 3 superfícies; (4) modo seleção — confirmar que arrastar a barra não marca a mensagem (P3 = inerte); (5) menu de contexto do botão direito sobre a bolha; (6) vários áudios na mesma conversa sem interferência; (7) leitor de tela anunciando a posição.
- **Verificação:** `node --test` em **todos** os `*.test.js` de `web/static/js` → **674/674 verdes** (516 em `services/`, incluindo os 22 novos). `htmTemplates.test.js` verde (a rede da crase, que mordeu de verdade na F2). `tests/contracts/test_docs_hygiene.py` → 2/2 verdes após documentar. `git status`: os únicos arquivos tocados são `AudioPlayer.js`, `MediaContent.js`, `MessageBubble.js`, `audioScrub.js` (novo), `audioScrub.test.js` (novo), `docs/UI_CONVERSA.md` e `CLAUDE.md` — **nenhum** arquivo de `server/`, `db/`, `alembic/` ou plugin, como o plano previa (R9). Suíte Python não foi executada por isso mesmo: zero linhas de Python mudaram.

---

## 6 — Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| R1 · `touch-action` | Sem `touch-action:none`, o navegador móvel trata o arraste horizontal como rolagem e **cancela o ponteiro** — o bug voltaria só no celular, e passaria no teste de desktop | Item 3 da F2; validação por toque explícita na F5 |
| R2 · Captura de ponteiro | Desmontar a bolha durante o gesto (chegou mensagem, trocou de conversa) deixando ponteiro capturado | Liberar no cleanup do `useEffect` (item 9 da F2) e tratar `pointercancel` como `pointerup` |
| R3 · Seek duplo | Manter `onClick` **e** seek no `pointerdown` faz o clique buscar duas vezes | Remover o `onClick` (item 2 da F2) |
| R4 · Bolha por cima | [MessageBubble.js:62](../web/static/js/components/contacts/MessageBubble.js#L62) põe `onClick` na **linha inteira** em modo seleção; um arraste no player marcaria a mensagem | `stopPropagation` no `pointerdown`; decisão sobre desabilitar o scrubber em modo seleção em **P3** |
| R5 · Tailwind vendorizado | Valor arbitrário com função aninhada **falha calado** (gotcha do `CLAUDE.md`) — a barra ficaria com alvo de 0px sem erro | Valores arbitrários simples ou `style` inline; conferir no navegador, não só no código |
| R6 · Margem negativa | `-my-[8px]` pode fazer o alvo cobrir a legenda logo abaixo ([MediaContent.js:67-69](../web/static/js/components/contacts/MediaContent.js#L67-L69)) e roubar o clique dela | Verificar na F5 que o texto da legenda continua selecionável; se colidir, usar `::before` esticado em vez de margem |
| R7 · Nota privada estreita | O card tem `min-w-[220px] max-w-[280px]` ([SystemMessageCard.js:86](../web/static/js/components/contacts/SystemMessageCard.js#L86)); a barra é curta e cada pixel vale mais segundos | Conferir a precisão do arraste nessa superfície na F5 |
| R8 · `transition-[width]` | Interpolação de 100ms durante o arraste faz a barra "arrastar atrás" do dedo | Desligar a transição enquanto `scrubRatio !== null` (item 7 da F2) |
| R9 · Backend | — | **Nenhum arquivo de `server/`, `db/` ou plugin é tocado.** Sem migration, sem restart de plugin, sem evento/filtro afetado. |

---

## 7 — Perguntas em aberto

**P1 — Buscar ao vivo durante o arraste, ou só ao soltar?**
✅ **DECIDIDO (2026-08-24, na F2): (a) ao vivo**, com throttle de `requestAnimationFrame` (`scheduleSeek`) — no máximo um seek por quadro.
Contexto: buscar a cada `pointermove` num Ogg/Opus faz muitos seeks por segundo.
(a) Ao vivo, como WhatsApp/Telegram — resposta imediata, risco de engasgo.
(b) Só no `pointerup`, com a barra em pré-visualização durante o gesto — sempre suave, mas o áudio segue tocando o trecho antigo enquanto se arrasta.
▶️ **Recomendação: (a)**, com *throttle* por `requestAnimationFrame`. Servidor confirmado com 206 (§4) e o arquivo é local ao navegador depois de bufferizado. **Se** a F2 medir engasgo, cair para (b) — é troca de 3 linhas. ⏸️ **ADIADO para a F2** (decidir com o comportamento medido, não no papel).

**P2 — Pausar o áudio enquanto se arrasta?**
✅ **DECIDIDO (2026-08-24, na F2): (a) manter tocando**, coerente com P1=(a).
(a) Manter tocando (WhatsApp). (b) Pausar no `down` e retomar no `up` (evita o pedaço "picotado").
▶️ **Recomendação: (a)** — o relato reclama justamente de "o áudio continua tocando" como *sintoma de que nada mudou*, não como incômodo. Se P1 = (b), (a) fica estranho e P2 deve virar (b) junto. ⏸️ **ADIADO para a F2.**

**P3 — Em modo seleção de mensagem, o scrubber deve funcionar?**
✅ **DECIDIDO (2026-08-24, na F2): (a) inerte em modo seleção** — prop `disabled` propagada `MessageBubble` → `MediaContent` → `AudioPlayer`. Duas linhas, e evita a faixa de 20px onde clicar não marcaria a mensagem.
Contexto: R4 — a linha inteira vira alvo de seleção ([MessageBubble.js:62](../web/static/js/components/contacts/MessageBubble.js#L62)).
(a) Desabilitar o scrubber em modo seleção (a linha toda seleciona, previsível). (b) Manter, com `stopPropagation`.
▶️ **Recomendação: (a)** — em modo seleção o operador está selecionando, não ouvindo; (b) cria uma zona morta de 20px onde clicar não seleciona. ⏸️ **ADIADO para a F5** (requer ver na tela).

**P4 — Colapsar os três `<source>` num `src` único?**
✅ **DECIDIDO (2026-08-24, na F3): sim.** Confirmado antes de mexer, como o plano exigia: só `.oga`/`.ogg`/`.m4a` em `statics/`, e os três `<source>` apontavam para a **MESMA URL** — nunca foram cadeia de fallback.
([AudioPlayer.js:74-78](../web/static/js/components/contacts/AudioPlayer.js#L74-L78) — três `type` diferentes para a mesma URL.)
▶️ **Recomendação: sim, na F3.** Não é a causa (§4) e o `Content-Type` real manda na decodificação. **A confirmar:** que nenhuma mídia legada dependa da cadeia de fallback — checar as extensões realmente presentes em `statics/media` e `statics/outbox` antes de mexer. Se houver dúvida, **não mexer** — é limpeza, não correção.

**P5 — Os `.ogg` corrompidos em `statics/outbox/`?**
`ffprobe` acusa `CRC mismatch! End of file` em `1781999691895.ogg`; o padrão `.png` + `.ogg` + `_relatorio.pdf` repetido sugere artefato da suíte de testes.
▶️ ⏸️ **ADIADO — fora do escopo deste plano.** Não afeta áudio inbound. Se um operador relatar áudio **enviado** que não toca, vira plano próprio.

---

## 8 — Checklist de verificação

- [ ] **F0 registrado:** o Gesto B (arraste) reproduziu o bug **antes** da correção
- [ ] `node --test web/static/js/services/audioScrub.test.js` verde
- [ ] `node --test` da suíte de módulos puros do frontend continua verde (sem regressão)
- [ ] Arrastar a barra para a **esquerda** retrocede — soltando **dentro** e **fora** da faixa de 4px
- [ ] Arrastar para a **direita** avança
- [ ] **Clique simples** continua buscando (sem seek duplo)
- [ ] Funciona nas **3 superfícies**: bolha do chat, nota privada, bandeja de anexo
- [ ] Funciona por **toque** (celular real ou DevTools em modo dispositivo)
- [ ] A barra **não foge do cursor** durante o arraste
- [ ] Rótulo de tempo acompanha o seek **com o áudio pausado**
- [ ] `2x` sobrevive à reconciliação da bolha otimista
- [ ] `Tab` alcança a barra; `←`/`→` movem 5s; `Home`/`End` funcionam
- [ ] `Espaço` e setas **não** rolam o chat quando a barra está focada
- [ ] **Modo escuro** legível nas 3 superfícies (`wa-*`, sem token novo)
- [ ] Modo **seleção** de mensagem: comportamento conforme **P3**
- [ ] Menu de contexto da mensagem continua abrindo sobre a bolha
- [ ] Vários áudios na mesma conversa não interferem entre si
- [ ] Áudio **longo** (~172s) e **curto** (<3s) buscam corretamente
- [ ] Recarregar a página (F5) e repetir o Gesto B
- [ ] **Nenhum** arquivo de `server/`, `db/`, `alembic/` ou plugin no diff

---

## 9 — Apêndice — arquivos-chave

**Frontend — modificado**
| Arquivo | Papel |
|---|---|
| [web/static/js/components/contacts/AudioPlayer.js](../web/static/js/components/contacts/AudioPlayer.js) | **o alvo** (116 linhas) — F2, F3, F4 |

**Frontend — novo**
| Arquivo | Papel |
|---|---|
| `web/static/js/services/audioScrub.js` | módulo puro da matemática de posição — F1 |
| `web/static/js/services/audioScrub.test.js` | `node --test` — F1 |

**Frontend — só leitura (consumidores, validação na F5)**
| Arquivo:linha | Papel |
|---|---|
| [MediaContent.js:64-66](../web/static/js/components/contacts/MediaContent.js#L64-L66) | bolha de áudio no chat |
| [SystemMessageCard.js:85-88](../web/static/js/components/contacts/SystemMessageCard.js#L85-L88) | áudio em nota privada |
| [MediaTray.js:189-192](../web/static/js/components/contacts/MediaTray.js#L189-L192) | prévia da gravação |
| [MessageBubble.js:62](../web/static/js/components/contacts/MessageBubble.js#L62), [:70](../web/static/js/components/contacts/MessageBubble.js#L70) | handlers da linha (R4 / P3) |
| [useMediaUpload.js:467](../web/static/js/components/contacts/hooks/useMediaUpload.js#L467) | origem do `_isLocalBlob` (G2) |
| [MediaContent.js:34](../web/static/js/components/contacts/MediaContent.js#L34) | `<video controls>` — o contraste que isola o defeito |

**Backend — verificado e NÃO tocado**
| Arquivo:linha | Por quê |
|---|---|
| [server/app.py:522-536](../server/app.py#L522-L536) | rota `/statics/outbox/{name}` — `FileResponse` com Range OK |
| [server/app.py:539](../server/app.py#L539) | mount `/statics` — `StaticFiles`, 206 comprovado |
