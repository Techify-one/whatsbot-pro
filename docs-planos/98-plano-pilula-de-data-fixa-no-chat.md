# Plano 98 — Pílula de data fixa no topo do chat: saber de que dia é o trecho sem rolar atrás do separador

> **Status:** ✅ IMPLEMENTADO (2026-07-31) — F1/F2a/F2b/F3/F4 concluídas; falta só o checklist visual do §7 no navegador · **Data:** 2026-07-31 · **Escopo:** pequeno (frontend puro)
> **Origem:** pedido do usuário (2026-07-31) — "quando eu entro numa conversa grande eu não consigo saber de qual dia se trata; preciso rolar até achar a mudança de dia. Queria o comportamento do WhatsApp, sobreposto na tela." **Método:** leitura do render do chat + do hook de scroll infinito, com `arquivo:linha` verificado por `sed`/`grep` nesta sessão.
> O separador de data **já existe** e já tem o vocabulário certo (`HOJE`/`ONTEM`/`quarta-feira`/`1 de janeiro`), mas é **inline**: rola para fora da viewport e o operador perde a referência temporal. Este plano acrescenta uma **pílula flutuante sempre visível** no topo da área de mensagens, que troca o rótulo conforme a rolagem. Zero backend, zero banco, zero configuração.
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| D1 ✅ (2026-07-31) | **Pílula flutuante, SEMPRE visível** — sobreposta no topo da área de mensagens, trocando o rótulo conforme a rolagem. Não é `position: sticky` | A medição é feita em JS sobre os separadores (§4·F2). A alternativa "sticky por dia" foi descartada e está registrada como falso-positivo (§3.1) |
| D2 ✅ (2026-07-31) | **O rótulo é o mesmo de hoje** — reusa `formatDateSeparator` sem mudança: `HOJE` / `ONTEM` / `quarta-feira` / `1 de janeiro` / `1 de janeiro de 2025` | [utils.js:47](../web/static/js/components/contacts/utils.js#L47) fica **intocado**. A pílula fixa e o separador inline nunca divergem porque leem a mesma função |
| D3 ✅ (2026-07-31) | **Clicar na pílula não faz nada** — indicador puro, como no WhatsApp | Sem navegação, sem seletor de data, sem `onClick`. "Ir para data" é o [Plano 99](99-plano-busca-na-conversa-e-ir-para-data.md), não este |
| D4 ✅ (2026-07-31) | Escopo travado: **frontend puro**. Nada de endpoint, migration, coluna, evento WS ou chave de config | Nenhum arquivo fora de `web/static/js/` é tocado |

---

## 1. Resumo executivo

O chat renderiza um separador de data inline sempre que o dia vira ([ContactDetail.js:625-634](../web/static/js/components/contacts/ContactDetail.js#L625-L634)), dentro do mesmo container de rolagem das mensagens ([ContactDetail.js:610](../web/static/js/components/contacts/ContactDetail.js#L610)). Como ele é um elemento comum do fluxo, sai da viewport assim que o operador rola — e numa conversa de centenas de mensagens (a instância de produção tem threads de milhares) descobrir "que dia é isto?" exige rolar para trás até topar com o próximo separador.

A solução é uma **pílula flutuante** ancorada no topo do container, sempre visível, cujo rótulo é o do **último separador que já passou acima da borda superior**. A medição roda a cada rolagem (throttle por `requestAnimationFrame`) sobre os **separadores** — não sobre as mensagens —, o que torna o custo proporcional ao **número de dias carregados** (dezenas), não ao número de mensagens (milhares), e imune a mídia que carrega depois e muda a altura da lista.

A decisão "qual dia está no topo" é extraída para um **módulo puro** em `web/static/js/services/`, testável por `node --test`, seguindo o padrão já estabelecido no repo (`conversationRows.js`, `mediaLimits.js`, `drafts.js`, `threadData.js` — 21 arquivos `*.test.js` em [web/static/js/services/](../web/static/js/services/)).

---

## 2. Como funciona hoje (mapa)

### 2.1 O separador inline

[ContactDetail.js:623-634](../web/static/js/components/contacts/ContactDetail.js#L623-L634), dentro do `messages.map(...)`:

```js
const prevTs = i > 0 ? messages[i - 1].ts : null;
const showDateSep = m.ts && (!prevTs || !isSameDay(prevTs, m.ts));
const dateSeparator = showDateSep
  ? html`<div key=${`sep-${m.ts}-${i}`} class="flex justify-center my-[12px]">
      <span class="bg-wa-bg/90 text-wa-secondary text-[12px] font-medium uppercase
                   tracking-wide rounded-[7.5px] px-[12px] py-[5px] shadow-sm">
        ${formatDateSeparator(m.ts)}
      </span>
    </div>`
  : null;
```

Cada item do `map` devolve o **array** `[dateSeparator, <bolha ou card>]` ([:649](../web/static/js/components/contacts/ContactDetail.js#L649) e [:655](../web/static/js/components/contacts/ContactDetail.js#L655)) — ou seja, separador e mensagem são **irmãos** no mesmo pai.

### 2.2 O rótulo (a reusar como está)

[utils.js:47-64](../web/static/js/components/contacts/utils.js#L47-L64) — `formatDateSeparator(ts)`: `HOJE` (diff 0), `ONTEM` (diff 1), dia da semana por extenso (diff 2–6), `1 de janeiro` (mesmo ano), `1 de janeiro de 2025` (ano anterior). Recebe **epoch em segundos** e resolve tudo no fuso do navegador.

### 2.3 A geometria da tela

| Elemento | `arquivo:linha` | Papel na feature |
|---|---|---|
| Header do chat (`h-[59px]`, `shrink-0`) | [ContactDetail.js:551](../web/static/js/components/contacts/ContactDetail.js#L551) | Fica **fora** do container de rolagem — a pílula não deve invadi-lo |
| Slot `chat.header.banner` | [ContactDetail.js:607](../web/static/js/components/contacts/ContactDetail.js#L607) | ⚠️ Plugin injeta faixa AQUI, **acima** do container e de altura variável. A pílula tem de se ancorar ao container, não a um offset fixo da janela |
| Container de rolagem (`ref=${chatRef}`) | [ContactDetail.js:610](../web/static/js/components/contacts/ContactDetail.js#L610) | `flex-1 min-h-0 overflow-y-auto … wa-chat-pattern py-2 px-[4%] lg:px-[7%]` |
| Sentinela "Carregando anteriores…" | [ContactDetail.js:613-619](../web/static/js/components/contacts/ContactDetail.js#L613-L619) | Primeiro filho do container quando `hasMore` — a pílula vai flutuar **sobre** ele |
| Raiz do componente | [ContactDetail.js:543](../web/static/js/components/contacts/ContactDetail.js#L543) | Já é `relative` — há onde ancorar um `absolute` |
| Chip "Fulano está digitando" | [ContactDetail.js:673](../web/static/js/components/contacts/ContactDetail.js#L673) | Precedente EXATO do padrão a usar: `relative h-0 z-10 pointer-events-none` + filho `absolute`. Fica no rodapé; não colide |

### 2.4 O scroll infinito (prepend)

[useInfiniteScroll.js:124-151](../web/static/js/hooks/useInfiniteScroll.js#L124-L151) — `useReverseInfiniteScroll`: sentinela no topo dispara `loadOlder`, e a posição visual é **restaurada em `useLayoutEffect`** somando o delta de altura ([:141-147](../web/static/js/hooks/useInfiniteScroll.js#L141-L147)).

⚠️ **Consequência para este plano:** ao carregar anteriores, o conteúdo acima da viewport cresce e `scrollTop` salta junto — mas a **posição visual não muda**, então o dia exibido também não deve mudar. Uma implementação que cacheasse `offsetTop` dos separadores quebraria aqui; medir por `getBoundingClientRect()` a cada frame não quebra (é coordenada de viewport, não de documento).

### 2.5 Onde a lógica pura mora no repo

Padrão consolidado: módulo sem Preact/DOM em `web/static/js/services/` + irmão `*.test.js` rodado por `node --test`. 21 arquivos hoje ([conversationRows.test.js](../web/static/js/services/conversationRows.test.js), [mediaLimits.test.js](../web/static/js/services/mediaLimits.test.js), [threadData.test.js](../web/static/js/services/threadData.test.js), [drafts.test.js](../web/static/js/services/drafts.test.js), …). Não há runner npm — a suíte roda por invocação direta (`node --test web/static/js/services/`).

---

## 3. Inventário

| # | Item | Onde | O que falta | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|---|
| I1 | Módulo puro `chatDayHeader.js` | novo, `web/static/js/services/` | não existe | `pickCurrentDay(rects, topEdge, pushZone)` → `{ label, offsetY }`. Recebe uma lista já medida (`[{label, top, bottom}]`) — **sem DOM** | baixo | S |
| I2 | Teste `chatDayHeader.test.js` | novo, irmão do I1 | não existe | `node --test`: nenhum separador; um só; alvo acima da borda; empurrão em curso; lista fora de ordem | baixo | S |
| I3 | Marcar cada separador com `data-day` | [ContactDetail.js:626](../web/static/js/components/contacts/ContactDetail.js#L626) | separador não tem atributo identificável | Acrescentar `data-day=${formatDateSeparator(m.ts)}` ao `<div>` do separador. Uma linha, sem mudar layout | baixo | S |
| I4 | Hook de medição `useChatDayHeader` | novo, `web/static/js/components/contacts/hooks/` | não existe | `scroll` no container (passivo) + `requestAnimationFrame` throttle; `querySelectorAll('[data-day]')` + `getBoundingClientRect()`; re-mede quando `messages` muda, quando filhos mudam de tamanho/DOM e em eventos de carga/transição | médio | M |
| I5 | Render da pílula | [ContactDetail.js:610](../web/static/js/components/contacts/ContactDetail.js#L610) (logo antes) | não existe | Container `relative h-0 z-10 pointer-events-none` + filho `absolute` centrado — **mesmo padrão** do chip de digitação ([:673](../web/static/js/components/contacts/ContactDetail.js#L673)). Estilo idêntico ao separador inline, com fundo opaco | baixo | S |
| I6 | Anti-duplicação ("empurrão") | I4 + I5 | — | Quando o próximo separador inline entra na zona de empurrão, a pílula desliza para cima / esvanece e o inline assume | médio | M |

### 3.1 Falsos positivos descartados

| Descartado | Por quê |
|---|---|
| **`position: sticky` no separador inline** (a solução "óbvia", 1 linha de CSS) | Separadores e mensagens são **irmãos** no mesmo pai ([ContactDetail.js:649](../web/static/js/components/contacts/ContactDetail.js#L649) e [:655](../web/static/js/components/contacts/ContactDetail.js#L655)). Elementos `sticky` irmãos **não se empurram**: cada um gruda no mesmo `top` do container e eles se **empilham/sobrepõem**. O efeito "o dia seguinte empurra o anterior" do WhatsApp Web exige que cada cabeçalho esteja num **containing block** próprio (um wrapper por dia) |
| **Agrupar as mensagens por dia num `<section>` por dia** (o que faria o `sticky` funcionar de verdade) | Reescreve o `map` de [ContactDetail.js:623-670](../web/static/js/components/contacts/ContactDetail.js#L623-L670): mexe nas `key`s (que o comentário de [:634-641](../web/static/js/components/contacts/ContactDetail.js#L634-L641) documenta como sensíveis ao prepend), no cálculo de `isFirst` (que compara com o item anterior) e na restauração de scroll do prepend. Custo/risco desproporcional para uma diferença de animação |
| **`IntersectionObserver` nos separadores** | Resolve "entrou/saiu", não "**qual** está no topo agora". Exigiria manter um estado ordenado dos observados e reconciliá-lo a cada prepend — mais estado e mais casos de borda do que medir os poucos separadores por frame |
| **Medir as MENSAGENS (`[data-mid]`) em vez dos separadores** | `data-mid` já existe em todas as bolhas e cards ([MessageBubble.js:57](../web/static/js/components/contacts/MessageBubble.js#L57), [SystemMessageCard.js:54](../web/static/js/components/contacts/SystemMessageCard.js#L54)), mas são **milhares** de nós por thread e o custo por frame seria O(nº de mensagens). Os separadores são O(nº de dias) |
| **Cachear `offsetTop` dos separadores** | Quebra em dois cenários reais: prepend (todo offset muda) e mídia que carrega depois e muda a altura. `getBoundingClientRect()` por frame é coordenada de viewport e é sempre correto |
| **Chave de config / toggle em Configurações** | É comportamento padrão do WhatsApp, não preferência. Cada toggle novo é dívida permanente (ver P3) |

---

## 4. Fases / Roadmap

```
WAVE 0   F1 (módulo puro + teste)  ·  F2a (data-day no separador)     ← paralelo
            │                            │
            └──────────── barreira ──────┘
WAVE 1   F2b (hook de medição + render da pílula)                      ← sozinha
            │
WAVE 2   F3 (empurrão / anti-duplicação)  ·  F4 (tema + bordas)        ← paralelo
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F1** | Lógica pura + teste | 🟢 | baixo | `node --test` verde no módulo novo |
| 0 | **F2a** | `data-day` no separador | 🟢 | baixo | DOM tem `[data-day]` em cada separador; nada muda visualmente |
| 1 | **F2b** | Hook + render da pílula | 🔴 [depende de: F1, F2a] | médio | A pílula aparece e acompanha a rolagem |
| 2 | **F3** | Empurrão / anti-duplicação | 🟢 [depende de: F2b] | médio | Nunca se lê o mesmo dia duas vezes |
| 2 | **F4** | Tema escuro + casos de borda | 🟢 [depende de: F2b] | baixo | Legível nos dois temas; sem pílula fantasma |

---

### Fase F1 — Módulo puro `chatDayHeader.js`

**Objetivo:** isolar "qual dia está no topo" numa função sem DOM, testável.

**Itens:**
1. `[sequencial]` Criar `web/static/js/services/chatDayHeader.js`. Assinatura proposta:
   ```js
   // seps: [{ label: 'ONTEM', top: <number>, bottom: <number> }] em coords de VIEWPORT,
   //       na ordem do documento (mais antigo → mais recente)
   // topEdge: coordenada Y da borda superior do container de rolagem
   // pushZone: altura (px) da zona onde o separador inline "assume" o lugar da pílula
   // → { label: string|null, offsetY: number }   offsetY < 0 ⇒ a pílula está saindo
   export function pickCurrentDay(seps, topEdge, pushZone) { … }
   ```
2. `[sequencial]` Regras a implementar:
   - **Nenhum separador** ⇒ `{ label: null, offsetY: 0 }` (o chamador não renderiza nada).
   - **Dia corrente** = o rótulo do **último** separador cujo `top <= topEdge`. Se nenhum passou ainda (a viewport está acima do primeiro separador), usa o **primeiro** — é o dia da mensagem mais antiga carregada.
   - **Empurrão**: se o próximo separador tem `top` dentro de `[topEdge, topEdge + pushZone]`, devolver `offsetY` negativo proporcional (`top - topEdge - pushZone`), para o chamador deslizar a pílula para fora enquanto o inline entra.
   - Tolerar lista vazia, `null`, e valores não numéricos sem lançar.
3. `[paralelo]` **F1·I2** — `chatDayHeader.test.js` cobrindo: lista vazia; um separador acima da borda; um abaixo (viewport no topo do histórico); dois com o segundo na zona de empurrão; entrada malformada.

**Pronto quando:** `node --test web/static/js/services/chatDayHeader.test.js` verde, sem importar Preact nem tocar em `document`.

#### Status de execução — Fase F1
**Estado:** ✅ Concluída (2026-07-31)
- **O que foi feito:** `web/static/js/services/chatDayHeader.js` (novo) — `pickCurrentDay(seps, topEdge, geom?)` + constantes exportadas `PILL_TRAVEL` (44), `PUSH_START` (56), `PUSH_END` (24). `web/static/js/services/chatDayHeader.test.js` (novo) — 13 casos.
- **Como foi feito / decisões:** dois **desvios deliberados** do texto do plano, ambos porque a fórmula proposta (`offsetY = top - topEdge - pushZone`, dia corrente pelo `top`) produzia estado de repouso errado:
  1. **O dia corrente é decidido pelo `bottom`, não pelo `top`.** Trocar o rótulo quando o separador inline *cruza* a borda deixaria, por ~26px de rolagem, a pílula e o separador desenhando o MESMO dia lado a lado — exatamente a duplicata que a F3 existe para evitar. Agora o rótulo só troca quando o separador sai **inteiro** pelo topo.
  2. **O empurrão termina em `PUSH_END = 24`, não em 0**, e o 3º parâmetro virou um objeto de geometria (`{travel, pushStart, pushEnd}`) em vez do escalar `pushZone`. Com o término em 0, uma conversa curta de um dia só (que não rola) ficaria **parada** com a pílula meio-esvanecida por cima do próprio separador inline — o separador nasce a ~20px da borda (`py-2` do container + `my-[12px]`). Com 24, a pílula simplesmente não aparece nesse caso: a resposta já está inline na tela.
  Normalização defensiva (descarta entrada sem rótulo/`top` numérico, ordena por `top`, `bottom` ausente cai no `top`) e degradação sem exceção em `topEdge`/geometria inválidos.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --test web/static/js/services/chatDayHeader.test.js` → **13/13 verde**. Nenhum import de Preact/DOM no módulo.

---

### Fase F2a — `data-day` no separador inline

**Objetivo:** tornar os separadores localizáveis pelo hook, sem mudar nada visualmente.

**Itens:**
1. `[sequencial]` Em [ContactDetail.js:626](../web/static/js/components/contacts/ContactDetail.js#L626), acrescentar `data-day=${formatDateSeparator(m.ts)}` ao `<div>` do separador. **Não** alterar classes, `key`, nem a estrutura do array devolvido pelo `map`.

**Pronto quando:** com uma conversa de 2+ dias aberta, `document.querySelectorAll('[data-day]').length` no console é igual ao número de separadores visíveis, e a tela está **byte-idêntica** à de antes.

#### Status de execução — Fase F2a
**Estado:** ✅ Concluída (2026-07-31)
- **O que foi feito:** [ContactDetail.js:663](../web/static/js/components/contacts/ContactDetail.js#L663) — `data-day=${formatDateSeparator(m.ts)}` no `<div>` do separador.
- **Como foi feito / decisões:** só o atributo. Classes, `key` e a estrutura do array devolvido pelo `map` intocadas; `formatDateSeparator` não foi tocada (D2), então pílula e separador leem literalmente a mesma função e não podem divergir.
- **Problemas / pendências:** nenhuma.
- **Verificação:** `node --input-type=module --check` no arquivo; diff da linha é o atributo e nada mais (layout byte-idêntico).

---

### Fase F2b — Hook de medição + render da pílula

**Objetivo:** a pílula aparece no topo e acompanha a rolagem.

**Itens:**
1. `[sequencial]` Criar `web/static/js/components/contacts/hooks/useChatDayHeader.js`:
   - Entrada: `{ scrollRef, items }` (o `chatRef` e `messages`, para re-medir quando a lista muda).
   - Listener `scroll` no **container** (não no `window`), com `{ passive: true }`, coalescido por `requestAnimationFrame` (nunca mais de uma medição por frame; cancelar o frame pendente no cleanup).
   - Medição: `scrollRef.current.querySelectorAll('[data-day]')` → `getBoundingClientRect()` de cada + `getBoundingClientRect().top` do container como `topEdge` → `pickCurrentDay(...)`.
   - Re-medir também: quando `items` muda (mensagem nova, prepend), e num `ResizeObserver` do container (o painel lateral abre/fecha e reflui a largura).
   - Só chamar `setState` quando `label`/`offsetY` **mudam de fato** (evita re-render por frame).
2. `[sequencial]` Render em [ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js), **imediatamente antes** do container de rolagem ([:610](../web/static/js/components/contacts/ContactDetail.js#L610)), no padrão já usado pelo chip de digitação ([:673](../web/static/js/components/contacts/ContactDetail.js#L673)):
   - Wrapper `relative h-0 z-10 pointer-events-none` (altura zero ⇒ **não empurra** o container nem o compositor).
   - Filho `absolute top-[8px] left-1/2 -translate-x-1/2` com as **mesmas classes visuais** do separador inline, trocando `bg-wa-bg/90` por um fundo **opaco** (o texto das bolhas passa por baixo).
   - `label == null` ⇒ não renderiza nada.
3. `[sequencial]` Ancoragem: o wrapper fica **dentro** da raiz `relative` ([:543](../web/static/js/components/contacts/ContactDetail.js#L543)) e **depois** do slot `chat.header.banner` ([:607](../web/static/js/components/contacts/ContactDetail.js#L607)) — assim uma faixa injetada por plugin empurra a pílula junto, em vez de ficar por cima dela.

**Pronto quando:** abrindo uma conversa com 3+ dias e rolando, a pílula está sempre visível e o rótulo bate com o separador inline mais próximo acima; ao "carregar anteriores" a viewport não salta e o rótulo não pisca; na Sandbox a pílula também aparece.

#### Status de execução — Fase F2b
**Estado:** ✅ Concluída (2026-07-31)
- **O que foi feito:** `web/static/js/components/contacts/hooks/useChatDayHeader.js` (novo) — listener `scroll` **passivo** no container; `ResizeObserver` no scrollport **e nos filhos diretos**; `MutationObserver` para filhos/atributos novos; captura de `load`/`loadedmetadata` e `transitionend`. Tudo é coalescido por `requestAnimationFrame` (um frame pendente por vez, cancelado no cleanup) e `setState` só roda quando `label`/`offsetY` mudam de fato. Isso fecha o ponto cego em que imagem/vídeo carregava ou card expandia sem mudar `messages`. Em [ContactDetail.js:150](../web/static/js/components/contacts/ContactDetail.js#L150) o hook é chamado logo depois do `useReverseInfiniteScroll`, e o render da pílula entrou em [ContactDetail.js:617-640](../web/static/js/components/contacts/ContactDetail.js#L617-L640), entre o slot `chat.header.banner` e o container de rolagem.
- **Como foi feito / decisões:**
  - **Re-medição em `useLayoutEffect([messages])`** (não `useEffect`): a medição por rAF só correria no quadro seguinte e a pílula da conversa ANTERIOR sobraria por um paint na troca de conversa (borda listada na F4). Medir antes do paint mata o caso na raiz. A ordem dos hooks garante que o listener é instalado antes do efeito que rola pro fim — a atribuição de `scrollTop` dispara `scroll` e o hook re-mede.
  - **Caixa de recorte** (`absolute top-0 inset-x-0 h-[48px] overflow-hidden`) entre o wrapper `relative h-0 z-10 pointer-events-none` e a pílula: sem ela, os 44px de `translateY` negativo do empurrão jogariam a pílula **por cima do header/banner**. Com ela, a pílula é aparada exatamente na borda superior do container.
  - `transform` e `opacity` vão em `style` inline (string) — a translação horizontal do centro (`translate(-50%, …)`) e a vertical precisam sair do MESMO `transform`, senão a classe `-translate-x-1/2` seria sobrescrita.
  - `transition-opacity duration-150` só na opacidade (o `transform` continua exato quadro a quadro, sem lag na rolagem rápida) — a reaparição da pílula com o dia novo vira fade em vez de pop.
- **Problemas / pendências:** validação **visual** (rolar uma conversa real de 3+ dias) não foi feita por mim — não há navegador nesta sessão. Os módulos são servidos com HTTP 200 pelo servidor de dev (`:8090`), então o grafo de imports resolve.
- **Verificação:** `node --input-type=module --check` nos dois arquivos; `curl` dos dois módulos novos no dev server → 200. ⚠️ Uma armadilha custou um ciclo: **crase dentro de comentário HTML no template `html\`…\`` fecha o template** (`chat.header.banner` entre crases quebrou o parse) — o `--check` pegou; o comentário agora não usa crase.

---

### Fase F3 — Empurrão / anti-duplicação

**Objetivo:** nunca exibir o mesmo dia duas vezes (pílula + inline logo abaixo).

**Itens:**
1. `[sequencial]` Consumir o `offsetY` da F1: aplicar `transform: translateY(<offsetY>px)` + `opacity` proporcional na pílula, para ela sair de cena enquanto o separador inline entra na zona de empurrão.
2. `[sequencial]` Calibrar `pushZone` com a altura real do separador (~26px + `my-[12px]`) — deixar a constante no módulo puro, nomeada, não espalhada pelo componente.
3. `[paralelo]` Conferir o caso de dois dias **muito próximos** (poucas mensagens num dia): a pílula sai e volta sem "tremer" (a histerese, se necessária, entra aqui, no módulo puro, com teste).

**Pronto quando:** rolando devagar por uma virada de dia, em nenhum quadro se lê o mesmo rótulo em dois lugares; rolando rápido, a pílula não fica presa fora de posição.

#### Status de execução — Fase F3
**Estado:** ✅ Concluída (2026-07-31)
- **O que foi feito:** o `offsetY` da F1 é consumido no render como `transform: translate(-50%, <offsetY>px)` + `opacity: 1 + offsetY/PILL_TRAVEL` (0 quando o empurrão completa). As três constantes de calibração (`PILL_TRAVEL`, `PUSH_START`, `PUSH_END`) moram **no módulo puro**, nomeadas e documentadas com a geometria real (pílula na faixa [8, 34] a partir da borda: `top-[8px]` + ~26px).
- **Como foi feito / decisões:** a anti-duplicação ficou nas **duas** pontas, não só na animação: a pílula sai de cena antes de o separador inline alcançar a faixa dela (`PUSH_END = 24`) **e** o rótulo só troca quando o inline sai inteiro pelo topo (regra do `bottom`, F1). Histerese explícita não foi necessária — o caso "dois dias colados" (um dia com poucas mensagens) cai naturalmente em "pílula continua fora de cena", porque o separador seguinte já está na zona de empurrão; está travado por teste.
- **Problemas / pendências:** a verificação quadro-a-quadro ("rolando devagar por uma virada de dia") é visual e fica para o teste manual; o invariante equivalente está coberto por teste puro.
- **Verificação:** testes `empurrão: parada, deslizando, e fora de cena`, `o rótulo só troca quando o separador inline sai INTEIRO pelo topo` e `dois dias colados … => sem tremer entre eles` — verdes.

---

### Fase F4 — Tema escuro e casos de borda

**Objetivo:** legibilidade nos dois temas e ausência de pílula fantasma.

**Itens:**
1. `[paralelo]` **Tema**: usar exclusivamente tokens `wa-*` (`bg-wa-bg`, `text-wa-secondary`, `shadow-sm`) — nada de cor crua nem hex inline (regra "Tema e modo escuro" do [CLAUDE.md](../CLAUDE.md)). Conferir sobre o `wa-chat-pattern` nos dois temas.
2. `[paralelo]` **Bordas a validar**: conversa vazia ("Nenhuma mensagem ainda", [ContactDetail.js:620-623](../web/static/js/components/contacts/ContactDetail.js#L620-L623)) ⇒ sem pílula; mensagem otimista sem `_id` ainda; troca de conversa (a pílula do contato anterior não pode sobrar no primeiro quadro); `hasMore` com a sentinela "Carregando anteriores…" visível ⇒ a pílula flutua **sobre** ela sem ocultá-la por completo.
3. `[paralelo]` **Z-index**: confirmar que a pílula (`z-10`) não briga com o overlay de drag-and-drop nem com os modais (`z-[130]`, [ContactDetail.js:745](../web/static/js/components/contacts/ContactDetail.js#L745)).

**Pronto quando:** checklist do §7 todo marcado nos dois temas.

#### Status de execução — Fase F4
**Estado:** ✅ Concluída no código (2026-07-31) · validação visual pendente
- **O que foi feito:**
  - **Tema**: só tokens `wa-*` — `bg-wa-bg` (OPACO, não `/90`: as bolhas passam por baixo), `text-wa-secondary`, `shadow-md`. É o mesmo par de cores do separador inline, que já é o padrão validado nos dois temas.
  - **Conversa vazia / sem separador**: `chatDay.label === null` ⇒ nada renderiza.
  - **Conversa de um dia só que cabe na tela**: a pílula fica fora de cena por geometria (F1) — o separador inline visível já responde, sem duplicata.
  - **Troca de conversa**: medição em `useLayoutEffect`, antes do paint (F2b).
  - **Sentinela "Carregando anteriores…"**: a pílula é **suprimida** enquanto `loadingOlder` — os dois ocupam o mesmo ponto (centro, topo) e a pílula cobriria o indicador por completo, o que o próprio checklist proíbe. **Desvio do plano**, que previa sobreposição parcial.
  - **Z-index**: pílula em `z-10`, abaixo do overlay de drag-and-drop (`z-[60]`, [DropOverlay.js:34](../web/static/js/components/contacts/DropOverlay.js#L34)) e dos modais (`z-[130]`) — sem briga. `pointer-events-none` no wrapper (D3).
- **Como foi feito / decisões:** ver a caixa de recorte na F2b — é ela que impede a pílula de invadir o header/banner durante o empurrão.
- **Problemas / pendências:** **o checklist visual do §7 precisa ser rodado no navegador** (não há um nesta sessão): contraste no escuro sobre bolha verde e sobre o `wa-chat-pattern`, e o comportamento ao rolar.
- **Verificação:** revisão de código dos pontos acima + `grep` dos z-index concorrentes. Suíte pura completa do workspace atual: `node --test web/static/js/services/*.test.js` → **415/415 verde**. A reação do hook a reflow é DOM/browser e continua no checklist visual pendente.

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Medição por frame | Jank em thread longa | Medir **só** os `[data-day]` (O(nº de dias)); `requestAnimationFrame` coalescido; `setState` apenas quando o rótulo muda |
| Prepend ("carregar anteriores") | Rótulo pisca ou some ao restaurar o scroll | `getBoundingClientRect()` é coordenada de **viewport** — imune ao salto de `scrollTop`. Nunca cachear `offsetTop` |
| Mídia carregando depois / card expandido | Alturas mudam sem scroll nem troca de `messages` | `ResizeObserver` nos filhos + captura de `load`/`loadedmetadata`/`transitionend` + `MutationObserver`, todos coalescidos no mesmo rAF |
| Slot `chat.header.banner` | Plugin injeta faixa e a pílula fica atrás/deslocada | Ancorar ao container de rolagem, **depois** do slot ([:607](../web/static/js/components/contacts/ContactDetail.js#L607)); nunca a um offset fixo da janela |
| Altura do wrapper | Pílula empurra as mensagens ou o compositor | `h-0` + filho `absolute`, exatamente como o chip de digitação ([:673](../web/static/js/components/contacts/ContactDetail.js#L673)) |
| Sobreposição de texto | Bolha passa por baixo e o rótulo fica ilegível | Fundo **opaco** (não `/90`) + `shadow-sm`; testar sobre bolha verde no tema escuro |
| Cliques | Pílula rouba clique de uma bolha embaixo | `pointer-events-none` no wrapper (D3 já diz que ela não é clicável) |
| Salto para mensagem citada | Pílula cobrindo o alvo | Não se aplica: `focusMessage` usa `block: 'center'` ([ContactDetail.js:290](../web/static/js/components/contacts/ContactDetail.js#L290)) |
| Modo escuro | Cor crua ilegível | Só tokens `wa-*`; regra do [CLAUDE.md](../CLAUDE.md) |

---

## 6. Perguntas em aberto

| # | Pergunta | Estado |
|---|---|---|
| **P1** | **Simular o empurrão** (a pílula desliza/esvanece quando o separador inline se aproxima do topo) ou **aceitar a sobreposição** momentânea (lê-se "ONTEM" duas vezes por um instante)? <br>(a) simular — mais fiel ao WhatsApp Web, custa a F3; (b) aceitar — a F3 some do plano. **Recomendação: (a).** | ✅ **(a) implementada** — F3 |
| **P2** | A decisão "qual dia está no topo" vai para um **módulo puro em `services/` com teste `node --test`** (F1), ou fica **dentro do componente**? <br>(a) módulo puro — padrão do repo, 21 precedentes; (b) inline — menos arquivos, sem teste. **Recomendação: (a).** | ✅ **(a) implementada** — `chatDayHeader.js`, 13 testes |
| **P3** | Manter a pílula **na Sandbox** (sai de graça, é o mesmo componente) e **sem chave de config**? <br>(a) sim para ambos; (b) esconder na Sandbox e/ou criar toggle. **Recomendação: (a)** — comportamento padrão não vira opção. | ✅ **(a) implementada** — a Sandbox usa o mesmo `ContactDetail`; zero config |
| **P4** | Mostrar a pílula **sempre**, inclusive em conversa curta de um dia só que cabe inteira na tela? <br>(a) sempre — consistente, responde a pergunta também na conversa curta; (b) só quando há rolagem / 2+ dias. **Recomendação: (a)** — (b) faz a pílula aparecer e sumir sozinha conforme chegam mensagens. | ⚠️ **(a) com uma correção de geometria** — ver abaixo |

> **P4, na prática.** Não há gate nenhum por nº de dias ou por "tem rolagem?" (nada aparece e some conforme chegam mensagens — o defeito de (b) foi evitado). Mas a conversa curta de um dia só **não exibe a pílula**, e não por regra: por geometria. O separador inline dela nasce a ~20px da borda, dentro da zona de empurrão, então a pílula está fora de cena — é o MESMO mecanismo que impede ler o mesmo dia duas vezes. Como o separador inline está inteiro na tela, a pergunta "que dia é isto?" continua respondida. Se o desejado for a pílula visível também aí (duplicando o dia), basta baixar `PUSH_END` no módulo puro.

---

## 7. Checklist de verificação

- [x] `node --test web/static/js/services/chatDayHeader.test.js` verde — **13/13**
- [x] `node --test web/static/js/services/*.test.js` verde — **415/415** no workspace atual (o runner desta versão do node não aceita o diretório, precisa do glob)
- [ ] Carregar imagem/vídeo e expandir/recolher card sem rolar: pílula re-mede no navegador — listeners/observers implementados; **smoke visual pendente**
- [ ] Conversa com 3+ dias: a pílula acompanha a rolagem e bate com o separador inline — **validação visual pendente**
- [ ] "Carregar anteriores" (rolar até o topo): a viewport não salta e o rótulo não pisca — **pendente** (a pílula é suprimida enquanto o "Carregando anteriores…" está em tela)
- [x] Virada de dia: em nenhum quadro se lê o mesmo rótulo duas vezes — coberto por teste puro (regra do `bottom` + `PUSH_END`)
- [x] Conversa vazia e conversa de um dia só: sem pílula fantasma (`label === null` / fora de cena por geometria)
- [ ] Troca de conversa: `useLayoutEffect` mede antes do paint por inspeção; **confirmação visual de ausência de um quadro fantasma pendente**
- [x] Sandbox: usa o mesmo `ContactDetail` — sai de graça (visual pendente)
- [ ] **Modo escuro**: pílula legível sobre bolha verde e sobre o `wa-chat-pattern` — **pendente**; só tokens `wa-*`, mesmo par de cores do separador inline, com fundo OPACO
- [x] Salto por deep-link `?message=<id>` e clique numa citação: `focusMessage` usa `block: 'center'` — a pílula não cobre o alvo
- [x] Plugin que injeta `chat.header.banner` ativo: a pílula é irmã **posterior** ao slot e ancorada ao container de rolagem — a faixa a empurra junto
- [x] Sem mudança em backend, migration, evento WS ou chave de config (D4) — só 3 arquivos em `web/static/js/`

---

## 8. Apêndice — arquivos-chave

**Frontend (novos)**
- `web/static/js/services/chatDayHeader.js` — decisão pura "qual dia está no topo"
- `web/static/js/services/chatDayHeader.test.js` — `node --test`
- `web/static/js/components/contacts/hooks/useChatDayHeader.js` — medição + estado

**Frontend (alterados)**
- [web/static/js/components/contacts/ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js) — `data-day` no separador ([:626](../web/static/js/components/contacts/ContactDetail.js#L626)) e render da pílula (antes de [:610](../web/static/js/components/contacts/ContactDetail.js#L610))

**Frontend (só leitura, não alterar)**
- [web/static/js/components/contacts/utils.js:47](../web/static/js/components/contacts/utils.js#L47) — `formatDateSeparator` (D2)
- [web/static/js/hooks/useInfiniteScroll.js:124](../web/static/js/hooks/useInfiniteScroll.js#L124) — `useReverseInfiniteScroll` (contexto do prepend)

**Backend / DB:** nenhum arquivo (D4).

---

## 9. Relação com outros planos

| Plano | Relação |
|---|---|
| [99 — Busca na conversa + ir para data](99-plano-busca-na-conversa-e-ir-para-data.md) | **Sinergia, sem dependência de código.** Este plano não precisa do 99. Já o 99 fica bem melhor com este pronto: ao saltar para o meio de um histórico de milhares de mensagens, é a pílula que diz **onde** o operador aterrissou. Ordem recomendada: 98 → 99 |
| 63 (cards colapsáveis) | Toca o mesmo `map` de [ContactDetail.js:623-670](../web/static/js/components/contacts/ContactDetail.js#L623-L670). Se ambos estiverem em voo, resolver conflito no `map` a favor das `key`s do 63 |
