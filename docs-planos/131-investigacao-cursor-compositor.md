# 131 — Investigação: o cursor do compositor

**Status:** investigação concluída, causa principal **provada em navegador real**. Nenhuma correção
foi escrita.
**Data:** 2026-08-19
**Contexto:** segunda rodada. A primeira (relatório entregue como artefato) errou o alvo principal;
este documento corrige o que ficou errado lá e é a fonte de verdade.

---

## 1. Veredito

O sintoma central — *"coloco o cursor no fim do texto e ele apaga do meio"* — tem **uma causa única,
identificada, medida e com causalidade provada**:

> Quando a mensagem **termina em quebra de linha** e é **longa o bastante para o campo rolar**
> (mais de 6 linhas), o `<div>` espelho fica **uma linha inteira atrasado** em relação à
> `<textarea>`. O operador clica onde ele **vê** o texto e o caret cai **~65 caracteres adiante**,
> uma linha abaixo. Nada avisa.

A `<textarea>` reserva uma última linha vazia para o cursor depois de um `\n` final; o espelho, que
usa `white-space: pre-wrap`, **não gera essa linha**. As alturas de rolagem divergem em exatos 20px
(uma linha), e `mirror.scrollTop = textarea.scrollTop` ([composerMirror.js:42](../web/static/js/utils/composerMirror.js))
é **truncado** ao máximo do espelho, que é menor.

Isso não tem nada a ver com acento, com autocomplete, com IA nem com a quantidade de texto colado —
só com **terminar em quebra de linha** e **passar de 6 linhas**.

---

## 2. A medição

Réplica fiel do compositor (mesmo HTML, mesmo CSS, importando `highlightComposerMarkup` e
`syncMirror` **de produção**), dirigida por Playwright + Chromium.

### 2.1 A geometria

| texto | `textarea.scrollHeight` | espelho | defasagem |
|---|---|---|---|
| 3 linhas | 58 | 58 | 0 |
| 6 linhas | 98 | 98 | 0 |
| 11 linhas | 238 | 238 | 0 |
| **11 linhas + `\n` final** | **258** | **238** | **20px = 1 linha** |
| 22 linhas | 438 | 438 | 0 |

### 2.2 O efeito no operador

Campo rolado até o fim; clico no centro de caracteres **visíveis** no espelho e leio onde o caret caiu.

```
■ sem \n no fim        pior erro = 0
    vejo o caractere 383 → caret caiu em 383   +0
    vejo o caractere 653 → caret caiu em 653   +0

■ COM \n no fim        pior erro = 71
    vejo o caractere 383 → caret caiu em 454   +71   <<<
    vejo o caractere 428 → caret caiu em 493   +65   <<<
    vejo o caractere 473 → caret caiu em 538   +65   <<<
    vejo o caractere 518 → caret caiu em 581   +63   <<<
    vejo o caractere 563 → caret caiu em 622   +59   <<<
```

### 2.3 Prova de causalidade

Acrescentar ao espelho a linha final que falta (um `<br>` quando o texto termina em `\n`) e medir de novo:

| | espelho | textarea | defasagem | pior erro de clique |
|---|---|---|---|---|
| como está hoje | 238 | 258 | 20px | **71 caracteres** |
| com a linha final | 258 | 258 | **0** | **0** |

Remover a causa elimina o efeito, em todos os pontos medidos. É o padrão conhecido de qualquer
implementação de *highlight sobre textarea*: o espelho precisa de um caractere ou `<br>` a mais
quando o conteúdo termina em quebra de linha.

---

## 3. Por que casa com o relato

| Relato do operador | Explicação |
|---|---|
| "ao **colar texto grande**" | texto colado quase sempre carrega o `\n` final, e é longo o bastante para rolar |
| "ou **digitar por muito tempo**" | passar de 6 linhas é o que liga o defeito; abaixo disso não há rolagem e o erro é zero |
| "coloco o cursor **no final**, apaga do **meio**" | o caret cai uma linha adiante do que se vê |
| "cursor num lugar específico, **não apaga corretamente**" | o desvio vale para **todo** ponto do campo, não só o fim |
| "às vezes **duplica**" | não explicado por esta causa — continua em aberto (§6) |

### Exposição em produção (últimos 60 dias, mensagens de saída)

| | |
|---|---|
| mensagens enviadas | 72.931 |
| **contêm quebra de linha** | 20.304 (28%) |
| longas o bastante para rolar (>380 chars) | 2.674 |
| **quebra de linha E rolam** | **2.421 (3,3%) ≈ 40 por dia** |

⚠️ Isto é **piso**, não teto: `handleSend` faz `input.trim()`, então **a quebra final é removida no
envio** e nunca chega ao banco. O defeito só existe enquanto se **compõe** — é, por construção,
invisível nos dados. Os 40/dia contam apenas quem tinha quebra *interna* e texto longo.

---

## 4. O que foi REFUTADO (correções ao relatório da 1ª rodada)

Medido, não deduzido. Estas hipóteses **não** se sustentam:

1. **"Acento desalinha a geometria do espelho" — FALSO.**
   Com e sem acento, as quebras de linha são idênticas caractere a caractere:
   `textarea=[0, 64, 126, 188, 252]`, `espelho=[0, 64, 126, 188, 252]`.

2. **"Render lento faz perder caracteres" — FALSO.**
   Réplica com Preact controlado de verdade e custo artificial de 55 ms por render (o pior caso
   medido, grupo de 118 membros): digitando 64 caracteres o mais rápido possível, **zero perdidos**,
   e `dom === state` em todas as amostras. O `useState` do Preact atualiza o valor do hook de forma
   síncrona, e `handleInputChange` grava `e.target.value` verbatim.

3. **"Tecla morta / IME corrompe o VALOR do texto" — FALSO** (no desktop, Chromium).
   Composição real via CDP (`Input.imeSetComposition` + `insertText`), com e sem render caro:
   resultado exato `'ja não vou'`, `dom === state` durante e depois da composição.
   ⚠️ Isto vale para o **valor**. O que a tecla morta estraga é o **espelho** — ver §6.1, que é um
   defeito diferente e confirmado.

4. **"O autocomplete de @menção / atalho `/` é a causa deste chamado" — improvável.**
   O bug do `replaceToken` é **real e continua provado** (duplica 300 caracteres num caso, apaga 297
   noutro), mas o menu de `@` só existe em **grupo** e em **nota privada**. Se o vídeo é de conversa
   1 a 1, está fora. Vale corrigir; não é este chamado.

---

## 5. Defeito secundário, também confirmado

**O espelho muda a largura das letras.** Ele emite `<b>`, `<i>` e `<code style="font-family:monospace">`
de verdade ([formatWhatsApp.js:85-112](../web/static/js/utils/formatWhatsApp.js)), enquanto a textarea
desenha tudo na fonte base. Medido, **sem** quebra de linha final:

| conteúdo | desvio das quebras |
|---|---|
| sem marcação | 0 |
| `*asterisco simples*` (sintaxe do WhatsApp) | **0** — o realce exige `**`, então não casa e não desalinha |
| um `**negrito**` | −11, −9, −8 e depois realinha |
| **dois `**negritos**`** | −11, −17, −15, **−17 e persiste até o fim** |
| `` `mono` `` | −11, −9, −8 |
| `_itálico_` | 0 (o itálico sintetizado tem a mesma largura de avanço) |

Ou seja: **cada trecho em negrito acrescenta desvio, e dois já não se cancelam.** É um segundo
caminho para o mesmo sintoma, independente do `\n` final. 13,4% das mensagens de saída contêm
asterisco — mas só desalinha quem escreve `**` (dois), não `*` (um).

---

## 6. A pista do acento

O operador relatou em vídeo que **palavras sem acento apagaram normalmente**. Investiguei três
mecanismos que separam, por construção, palavra acentuada de palavra sem acento. Dois estão medidos.

### 6.1 As teclas mortas do ABNT2 SÃO os marcadores do espelho ⚠️ (medido, e é bug nosso)

No teclado ABNT2, `à` se digita com a tecla morta **`` ` ``** e `ã`/`õ` com **`~`**. Esses são
exatamente os marcadores de **código inline** e de **tachado** em `highlightComposerMarkup`
([formatWhatsApp.js:95-96 e :108-109](../web/static/js/utils/formatWhatsApp.js)).

E o Chrome coloca o caractere morto **literal** dentro de `textarea.value` durante a composição.
Medido via CDP (`Input.imeSetComposition`) na réplica com Preact real:

```
til (ã, õ)   value durante a composição = '~'
crase (à)    value durante a composição = '`'
```

Com um marcador **já presente** no texto, a tecla morta fecha o par e o espelho vira markup:

| texto | ao digitar | o espelho produz |
|---|---|---|
| `aten~ao, preciso de informa` | `ã` (til) | `<s>ao, preciso de informa</s>` — 168px tachados |
| `o protocolo ` do cliente e a fatur` | `à` (crase) | `<code style="font-family:monospace">` — **190px trocam de fonte** |

A monoespaçada é a que machuca: um trecho inteiro **muda de métrica no meio da digitação**, as
quebras de linha andam, e o caret desencontra dos glifos. É a mesma classe de divergência de 11
caracteres do §5 — só que **disparada por digitar um acento**.

Exposição: **778 mensagens de saída em 60 dias (1,07%) contêm `~` ou `` ` `` solto**, e 581 delas
também têm acento — ~13 por dia em que um marcador órfão convive com texto acentuado. Um `~` órfão
nasce fácil: teclar `~` seguido de consoante (erro de digitação comum) deixa o til literal no texto.

### 6.2 Texto colado em NFD (medido, mas é do navegador, não nosso)

Texto em **NFD** (acento como caractere combinante separado, `a` + U+0303, em vez do `ã`
pré-composto). Medido numa `<textarea>` **pelada**, sem WhatsBot nenhum:

```
NFC 'manutenção' (10 unidades)      NFD 'manutenção' (12 unidades)
  backspace 1 → 'manutençã'  9 vis.   backspace 1 → 'manutençã'  9 visíveis
  backspace 2 → 'manutenç'   8 vis.   backspace 2 → 'manutença'  9 visíveis  ← nada sumiu!
  backspace 3 → 'manuten'    7 vis.   backspace 3 → 'manutenç'   8 visíveis
                                      backspace 4 → 'manutenc'   8 visíveis  ← nada sumiu!
```

Em NFD, o Backspace tira **primeiro o acento e só depois a letra**. O operador aperta e parece que
nada foi apagado — e isso **só acontece em palavra acentuada**. É o sintoma do vídeo, ao pé da letra.

**Mas provavelmente não é o caso deles.** No banco de produção: **22 mensagens em 661.218 (0,003%)**
contêm marca combinante. NFD é raríssimo nesses dados. E — importante — **isso não é bug do WhatsBot**:
é o comportamento do navegador. Nada no caminho de entrada normaliza (as únicas chamadas a
`normalize()` no frontend são para dobrar acento na **busca**).

**Conclusão sobre NFD:** compatível com o vídeo, mas os dados dizem que é raro **nas mensagens
enviadas**. Vale notar que texto colado que o operador desistiu de enviar não aparece no banco — e
PDF, macOS/iOS e sistemas legados são fontes documentadas de NFD.

### 6.3 Autocorreção do navegador (não medido)

A `<textarea>` **não declara** `spellcheck`, `autocorrect` nem `autocapitalize`
([Composer.js:372-385](../web/static/js/components/contacts/Composer.js)), e o padrão de ambos é
**ligado**, com idioma herdado de `<html lang="pt-BR">`. Em português quase toda autocorreção
**produz um acento** (`nao`→`não`, `voce`→`você`, `ate`→`até`). Duas consequências exclusivas da
palavra acentuada: ela não foi digitada, foi **substituída** (`insertReplacementText` sobre um
intervalo calculado pelo navegador); e **Backspace logo após uma autocorreção significa desfazer a
autocorreção**, não apagar um caractere. Vale sobretudo em teclado de toque; não foi reproduzido aqui.

### 6.4 Ranking — e uma discordância registrada

Eu ordenaria **6.1** (é nosso, é medido, casa com o teclado que eles usam) → **6.3** → **6.2**,
porque NFD é 0,003% das mensagens enviadas.

**A síntese da pesquisa discorda e põe 6.2 em primeiro**, com um argumento que eu não tinha feito e
que é bom: em NFD, `não` é `n·a·◌̃·o`, e com o cursor **no fim da palavra** o primeiro Backspace
apaga o **til, que está no meio** — a palavra não encurta. Isso é, palavra por palavra, o relato
original (*"com o cursor no fim, apagar remove caracteres do meio"*), enquanto a minha objeção
estatística vale só para o que foi **enviado**, e o defeito acontece enquanto se **compõe**.

**Não resolvo isso por argumento — resolvo com uma sonda.** Com o operador, no chat onde falhou e
**antes de apagar nada**, colar no console:

```js
const v = document.querySelector('textarea').value;
console.log(v.length, v.normalize('NFC').length, v === v.normalize('NFC'));
```

Comprimentos diferentes ⇒ NFD confirmado em produção, e 6.2 sobe para primeiro.

⚠️ E nenhum dos três é necessário para explicar o relato original: o defeito do `\n` final (§1) não
distingue acento nenhum e é ordens de grandeza mais frequente. Uma frente de investigação
independente, que não sabia da minha medição, **chegou à mesma causa raiz pelo mesmo número**
(textarea 138 × espelho 118 — os mesmos 20px).

---

## 7. Como reproduzir (2 minutos, no painel real)

1. Abra uma conversa qualquer (**não** precisa ser grupo).
2. Cole ou digite um texto de **mais de 6 linhas** — até o campo ganhar barra de rolagem.
3. Aperte **Shift+Enter** no fim, para o texto **terminar em quebra de linha**.
4. Clique com o mouse **no meio** de uma palavra que você está vendo.
5. Aperte Backspace algumas vezes.

**Esperado:** o caret não está onde você clicou — está cerca de uma linha adiante, e a exclusão
acontece lá. Repita **sem** o Shift+Enter final: o erro desaparece.

---

## 8. O que perguntar a quem gravou o vídeo

1. A mensagem terminava em **linha em branco / Shift+Enter**? (decide a causa principal)
2. O campo estava com **barra de rolagem** (mais de 6 linhas)?
3. Era conversa **1 a 1** ou grupo/nota privada? (decide se o autocomplete entra)
4. O texto foi **colado**? De onde — WhatsApp, Word, PDF, outro sistema? (decide §6.2)
5. Tinha `**negrito**` com **dois** asteriscos na mensagem? (decide §5)
6. Havia um `~` ou uma crase `` ` `` soltos no texto, e a palavra que falhou tinha `ã`, `õ` ou `à`?
   (decide §6.1 — é a pergunta mais específica desta lista)
7. Desktop ou celular? Navegador? Teclado ABNT2?

---

## 9. O que continua em aberto

- **A duplicação de mensagem no fio** não foi investigada nesta rodada. Continua com duas explicações
  concorrentes (envio duplo real × bolha otimista não reconciliada). Decide-se olhando o banco:
  duplicata que some no F5 é a bolha; que sobrevive é envio duplo.
- **Celular** não foi testado. No Android o teclado compõe o tempo todo; a refutação do item 3 do §4
  vale para desktop.
- **Firefox e Safari** não foram medidos (só Chromium). O comportamento de `pre-wrap` com quebra final
  é padronizado, mas a largura da barra de rolagem e o Backspace em NFD variam entre motores.
- **Redimensionar a janela** não re-sincroniza o espelho: não há `ResizeObserver` nem listener de
  `resize`, e o efeito depende só de `[input]`. Abrir/fechar o painel lateral deixa o `paddingRight`
  velho até a próxima tecla. É buraco residual do fix `c3f401c`.
- **A pilha de desfazer nativa está morta** desde a primeira escrita programática de `.value` — o
  primeiro emoji, @menção, /atalho ou hidratação de rascunho mata o Ctrl+Z do operador. Confirmado,
  fora do escopo do chamado.
- **Arrastar-e-soltar texto dentro do campo** move o trecho e ninguém intercepta
  ([useDropZone.js:15-19](../web/static/js/components/contacts/hooks/useDropZone.js#L15) só olha
  arquivo). Com o texto transparente o gesto dispara sem querer — e produziria exatamente
  "sumiu daqui, apareceu ali". Vale perguntar ao operador.

---

## 10. Caminhos de correção (não implementados)

| | o que fazer | esforço | resolve |
|---|---|---|---|
| **A** | Espelho recebe a linha que falta quando o texto termina em `\n` (um `<br>` ou `\n` extra no `highlightComposerMarkup`, ou no `syncMirror`) | trivial | **a causa principal, provada** |
| **B** | Sincronizar o espelho também em `resize` (`ResizeObserver` na textarea) | baixo | o buraco residual do `c3f401c` |
| **C** | Tirar do espelho o que muda métrica: `<b>`, `<i>`, `<code>` viram cor/fundo, mantendo só o esmaecimento | baixo | o defeito secundário do §5 **e o §6.1**, estruturalmente |
| **D** | Corrigir `replaceToken`: validar `start <= caret`, ler valor e índice da mesma fonte, recalcular o menu em `onSelect`/`onClick`/`onBlur` | baixo | o bug do autocomplete (real, outro chamado) |
| **E** | Não repintar o espelho enquanto `isComposing` estiver ativo (`compositionstart`/`compositionend`) | baixo | o §6.1 sem abrir mão do negrito |
| **F** | Normalizar para NFC **no `paste`** (uma vez, nunca a cada tecla — normalizar dentro do componente controlado quebra a composição) | baixo | o §6.2 |
| **G** | `autocorrect="off"` na textarea (manter `spellcheck`, que só sublinha) | trivial | o §6.3 |

**A** é a correção deste chamado. **C** é a mais valiosa depois dela: fecha de uma vez o §5 e o §6.1,
porque enquanto o espelho alterar métrica de fonte nenhum ajuste de padding resolve. **E** é a
alternativa a **C** se o negrito ao vivo for inegociável. Nenhuma delas exige trocar o `<textarea>`
por editor dedicado.

---

## Apêndice — o banco de medição

Réplica em `harness/index.html`: mesmo HTML/CSS do `Composer.js`, importando `formatWhatsApp.js` e
`composerMirror.js` **de produção** por HTTP. Dirigida por Playwright + Chromium.

A medição que importa, em pseudocódigo:

```js
// 1. aplica o texto exatamente na ordem do app
ta.value = texto;
mirror.innerHTML = highlightComposerMarkup(texto);
syncMirror(ta, mirror);                                  // efeito do Composer (filho)
ta.style.height = 'auto';
ta.style.height = Math.min(ta.scrollHeight, 120) + 'px'; // efeito do useComposer (pai)
requestAnimationFrame(() => syncMirror(ta, mirror));     // o rAF do c3f401c

// 2. leva o caret ao fim, como quem acabou de escrever
ta.setSelectionRange(texto.length, texto.length);
ta.scrollTop = ta.scrollHeight;
syncMirror(ta, mirror);

// 3. para cada caractere VISÍVEL no espelho: clica no centro dele e compara
//    com textarea.selectionStart. Divergiu => o que se vê não é onde o caret cai.
```

⚠️ Duas armadilhas que me deram falso positivo antes de eu corrigir, registradas para quem repetir:
- clicar em coordenadas **fora da caixa visível** (o texto tem 11 linhas, o campo mostra 6) devolve
  desvios enormes e sem sentido — filtre pelos limites da textarea;
- localizar o caractere por `y >= topo && y <= topo + 20` casa a **linha de cima**, porque o retângulo
  do glifo é mais baixo que a caixa de linha. Use o `bottom` real do retângulo.
