# Plano 124 — Colar/anexar mídia sem perder o texto: a fila vira BANDEJA e o compositor não some

> **Status:** PLANEJAMENTO · **Data:** 2026-08-14 · **Escopo:** médio (frontend-only; **sem** migration, **sem** rota nova)
> **Origem:** relato do usuário em 2026-08-14 — *"é possível colar uma imagem quando ela está na área de
> transferência. Porém, se o usuário começar a digitar uma mensagem e tentar colar uma imagem, isso não
> funciona; é necessário que o chat esteja limpo. O Telegram deixa digitar texto e continuar colando
> imagens e outras coisas."*
> **Método:** leitura do código real do painel (`web/static/js/components/contacts/**`) + backend de envio
> de mídia (`server/routes/contacts.py`). Todo `arquivo:linha` abaixo foi verificado nesta sessão.
>
> **O achado:** o handler de colar **não olha o texto** ([useMediaUpload.js:259-271](../web/static/js/components/contacts/hooks/useMediaUpload.js#L259-L271)) — ele funciona sempre.
> O que quebra é o **render**: assim que a fila deixa de estar vazia, o `Composer` **substitui a barra de
> entrada inteira** pela prévia da fila ([Composer.js:143](../web/static/js/components/contacts/Composer.js#L143) — `: hasPending ? '' :`).
> O texto digitado continua vivo no estado e no rascunho, mas **desaparece da tela**, e a legenda passa a
> ser um **segundo campo** (`mediaCaption`, [useMediaUpload.js:81](../web/static/js/components/contacts/hooks/useMediaUpload.js#L81))
> que nasce vazio. Como a `<textarea>` é o **único** elemento com `onPaste`
> ([Composer.js:355](../web/static/js/components/contacts/Composer.js#L355)), desmontá-la também mata o
> Ctrl+V para o **segundo** arquivo — enquanto arrastar-e-soltar continua funcionando, o que esconde o bug.
>
> **A forma da solução:** a fila deixa de ser uma *tela modal de confirmação* e vira uma **bandeja** acima
> do compositor (modelo Telegram/WhatsApp Web). O compositor nunca desmonta; **o texto do compositor É a
> legenda**; o botão Enviar decide a rota. Nenhuma mudança de backend é necessária — `/send-image`,
> `/send-document` e `/send-video` já aceitam `caption`.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar
> para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| **D1** ✅ 2026-08-14 | *"não implemente nada ainda; é apenas investigação e planejamento"* | Nenhuma fase pode ser iniciada sem pedido explícito. |
| **D2** ✅ 2026-08-14 | O comportamento-alvo é o do **Telegram**: digitar texto e **continuar colando** imagens e outras coisas. | O compositor **não pode desmontar** enquanto há fila; colar N vezes acrescenta N vezes (F2/F3). |
| **D3** — princípio do repo | Lógica pura fora de componente, testável com `node --test` (precedente: [mediaQueue.js](../web/static/js/services/mediaQueue.js), [uploadLimits.js](../web/static/js/services/uploadLimits.js), [mediaLimits.js](../web/static/js/services/mediaLimits.js)). | A decisão "o que este Enviar faz" vira um módulo puro novo (F1), não um `if` dentro do JSX. |
| **D4** — princípio do repo | Modo escuro legível em qualquer superfície nova (`wa-*`, `.wa-field`). | A bandeja reaproveita as classes já usadas em [MediaQueuePreview.js:78](../web/static/js/components/contacts/MediaQueuePreview.js#L78). |
| **D5** — princípio | Comportamento de envio (rotas, limites, otimismo, reconciliação de ACK) é **preservado**. | F1–F4 são refactor de UI + roteamento de submit; `sendOne` ([useMediaUpload.js:305-447](../web/static/js/components/contacts/hooks/useMediaUpload.js#L305-L447)) **não muda**. |

---

## 1. Resumo executivo

Colar mídia com o compositor já preenchido **funciona** — o arquivo entra na fila. O que acontece é que a
barra de entrada inteira (alternador Responder/Privada, prévia de citação, aviso de 24h, `<textarea>`,
emoji, botão enviar/microfone) é **trocada** pela prévia da fila, então o operador vê seu texto sumir e
conclui que o colar falhou. Pior: sem a `<textarea>` montada não há mais alvo de `paste`, então o
**segundo** Ctrl+V é ignorado — exatamente o "continuar colando" que o usuário pede.

A correção é uma só decisão de layout com consequências em cascata: **a fila passa a conviver com o
compositor**. Some o campo de legenda separado (`mediaCaption`) e o texto do compositor vira a legenda;
o botão Enviar roteia (só texto → `handleSend`; fila → `confirmQueue(caption)`); o `Esc` global da prévia
([MediaQueuePreview.js:64-70](../web/static/js/components/contacts/MediaQueuePreview.js#L64-L70)) precisa
ser reescopado para não apagar o lote enquanto o operador digita.

Frontend-only. Backend intocado: `caption` já existe em `/send-image`
([contacts.py:1989](../server/routes/contacts.py#L1989)), `/send-document`
([:2137](../server/routes/contacts.py#L2137)) e `/send-video` ([:2199](../server/routes/contacts.py#L2199)).
A **única** exceção é o áudio, que por contrato não leva legenda ([:2113](../server/routes/contacts.py#L2113)) — ver **P1**.

---

## 2. Como funciona hoje (mapa verificado)

### 2.1 O caminho do colar

| # | Passo | Onde |
|---|---|---|
| 1 | `onPaste` na `<textarea>` — **único** ponto de escuta de colar do painel | [Composer.js:355](../web/static/js/components/contacts/Composer.js#L355) |
| 2 | `handlePaste` varre `clipboardData.items`, pega **todos** os `kind === 'file'`, `preventDefault()` e delega. Colar texto puro cai no comportamento nativo | [useMediaUpload.js:259-271](../web/static/js/components/contacts/hooks/useMediaUpload.js#L259-L271) |
| 3 | `requestFilesDrop(files, 'media')` — porta de entrada única (colar, arrastar, seletor): tetos globais → teto de 10 por FILA → `buildQueueItems` → limites do canal → `setPendingQueue(prev => prev.concat(keep))` (**acrescenta**, não substitui) | [useMediaUpload.js:167-221](../web/static/js/components/contacts/hooks/useMediaUpload.js#L167-L221) |
| 4 | `hasPending = pendingQueue.length > 0` | [Composer.js:55](../web/static/js/components/contacts/Composer.js#L55) |
| 5 | **A troca:** `${!canSend ? … : hasPending ? '' : recording ? … : html\`<form>…\`}` — com fila pendente, a barra de entrada renderiza `''` | [Composer.js:143](../web/static/js/components/contacts/Composer.js#L143) |
| 6 | No lugar dela, a prévia (renderizada antes, fora do `form`) com **campo de legenda próprio** | [Composer.js:106-122](../web/static/js/components/contacts/Composer.js#L106-L122) · [MediaQueuePreview.js:102-110](../web/static/js/components/contacts/MediaQueuePreview.js#L102-L110) |
| 7 | Confirmar lê **`mediaCaption`**, não o texto do compositor; a legenda vai só no **primeiro** item | [useMediaUpload.js:463](../web/static/js/components/contacts/hooks/useMediaUpload.js#L463) · [:478](../web/static/js/components/contacts/hooks/useMediaUpload.js#L478) |

### 2.2 O texto do compositor

`input` vive em [useComposer.js:54](../web/static/js/components/contacts/hooks/useComposer.js#L54) e é
espelhado no rascunho por conversa/usuário ([:90-94](../web/static/js/components/contacts/hooks/useComposer.js#L90-L94),
[services/drafts.js](../web/static/js/services/drafts.js)). **Nada no `useMediaUpload` toca nele** — por isso
o texto reaparece intacto quando a fila é cancelada ou enviada. É o sintoma clássico de "sumiu, mas não
perdeu": o operador não tem como saber disso.

### 2.3 O que mais morre junto com a barra

Tudo isto fica inacessível enquanto há fila pendente, e nada disso é intencional:

| Afordância | Linha |
|---|---|
| Alternador **Responder / Mensagem Privada** (+ toggles "IA lê" / "IA responde no chat") | [Composer.js:159-203](../web/static/js/components/contacts/Composer.js#L159-L203) |
| Prévia da **citação** (responder a uma mensagem) | [Composer.js:204-224](../web/static/js/components/contacts/Composer.js#L204-L224) |
| Aviso da **janela de 24h** + atalho para template | [Composer.js:225-234](../web/static/js/components/contacts/Composer.js#L225-L234) |
| Emoji, menu de anexo, botão de template | [Composer.js:236-287](../web/static/js/components/contacts/Composer.js#L236-L287) |
| `@menção` / `/atalho` (autocomplete) | [Composer.js:289-342](../web/static/js/components/contacts/Composer.js#L289-L342) |
| `Enter` envia / `Shift+Enter` quebra linha | [ContactDetail.js:594-600](../web/static/js/components/contacts/ContactDetail.js#L594-L600) |

⚠️ **Assimetria que esconde o bug:** arrastar-e-soltar **continua** funcionando com a fila aberta — a zona
de drop está na raiz do painel ([ContactDetail.js:318-325](../web/static/js/components/contacts/ContactDetail.js#L318-L325),
[useDropZone.js:24-71](../web/static/js/components/contacts/hooks/useDropZone.js#L24-L71)) e `requestFilesDrop`
acrescenta. Só o **colar** morre, porque depende de um elemento que foi desmontado. Isso explica por que o
problema nunca apareceu como "não dá para adicionar mais arquivos".

### 2.4 O que o backend já aceita

| Rota | Legenda | Menções | Citação | Passa por `filter.outbound.text` |
|---|---|---|---|---|
| `/send-image` [:1989](../server/routes/contacts.py#L1989) | ✅ `caption` | ❌ | ❌ | ❌ (só `emit_text`) |
| `/send-document` [:2137](../server/routes/contacts.py#L2137) | ✅ | ❌ | ❌ | ❌ |
| `/send-video` [:2199](../server/routes/contacts.py#L2199) | ✅ | ❌ | ❌ | ❌ |
| `/send-audio` [:2040](../server/routes/contacts.py#L2040) | ❌ **por contrato** ([:2113](../server/routes/contacts.py#L2113)) | ❌ | ❌ | ❌ |
| `/send` (texto) [:1872](../server/routes/contacts.py#L1872) | — | ✅ | ✅ `reply_to` | ✅ |

Isso fixa três limitações reais do modelo "texto = legenda" — ver **P2**, **P3**, **P4**.

---

## 3. Inventário de mudanças

| # | Item | Onde | O que falta | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|---|
| I1 | Roteamento do submit | novo `web/static/js/services/composerSubmit.js` | não existe | função **pura** `submitPlan({text, queue, mode, sessionClosed, sending})` → `{action:'noop'\|'text'\|'media'\|'template', caption}` | baixo | S |
| I2 | Barra de entrada não some | [Composer.js:143](../web/static/js/components/contacts/Composer.js#L143) | ternário troca a barra pela prévia | remover o ramo `hasPending ? ''`; a prévia passa a ser irmã **acima** do `<form>` | médio | S |
| I3 | Prévia → bandeja | [MediaQueuePreview.js](../web/static/js/components/contacts/MediaQueuePreview.js) | tem campo de legenda, botões Enviar/Cancelar e `Esc` global | vira faixa compacta: miniaturas + `✕` por item + "Limpar"; sem legenda, sem botão Enviar | médio | M |
| I4 | Legenda = texto do compositor | [useMediaUpload.js:81](../web/static/js/components/contacts/hooks/useMediaUpload.js#L81) · [:463](../web/static/js/components/contacts/hooks/useMediaUpload.js#L463) | `mediaCaption` é estado próprio | `confirmQueue(caption)` recebe a legenda por parâmetro; `mediaCaption` é removido (com shim durante a transição) | médio | S |
| I5 | Botão Enviar / `Enter` roteiam | [Composer.js:362-377](../web/static/js/components/contacts/Composer.js#L362-L377) · [ContactDetail.js:594-600](../web/static/js/components/contacts/ContactDetail.js#L594-L600) | só conhecem texto | consumir `submitPlan` (I1); com fila, Enviar confirma o lote e limpa o texto | médio | M |
| I6 | Botão Enviar aparece com fila vazia de texto | [Composer.js:362](../web/static/js/components/contacts/Composer.js#L362) (`hasText`) | com fila e sem texto o botão vira **microfone** | condição passa a `hasText \|\| hasPending` | baixo | S |
| I7 | `Esc` reescopado | [MediaQueuePreview.js:64-70](../web/static/js/components/contacts/MediaQueuePreview.js#L64-L70) | listener de `document` cancela o lote inteiro | ver **P5** | médio | S |
| I8 | Fila de áudio | [useMediaUpload.js:274-283](../web/static/js/components/contacts/hooks/useMediaUpload.js#L274-L283) (`setPendingAudio` **substitui** a fila) | áudio não aceita legenda | ver **P1** | médio | M |
| I9 | Colar fora da `<textarea>` (opcional) | [ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js) | só a textarea escuta | listener de `paste` no `document` quando a conversa está aberta e o foco não está em outro campo | baixo | S |
| I10 | Progresso do lote | [Composer.js:126-131](../web/static/js/components/contacts/Composer.js#L126-L131) | já é faixa própria | manter; com a barra visível, conferir que não empurra o layout | baixo | S |

### 3.1 Falsos positivos descartados

| Suspeita | Veredito | Razão |
|---|---|---|
| "O handler de colar ignora o evento quando há texto" | ❌ **falso** | `handlePaste` não lê `input` nem o cursor ([useMediaUpload.js:259](../web/static/js/components/contacts/hooks/useMediaUpload.js#L259)). O primeiro colar **sempre** enfileira. |
| "O texto digitado é perdido" | ❌ **falso** | Fica em `input` + rascunho ([useComposer.js:54](../web/static/js/components/contacts/hooks/useComposer.js#L54), [:90](../web/static/js/components/contacts/hooks/useComposer.js#L90)) e reaparece ao cancelar. O defeito é de **percepção e de fluxo**, não de perda de dado. |
| "O backend não aceita mídia com legenda" | ❌ **falso** | Três das quatro rotas aceitam `caption` (§2.4). Nenhuma mudança de backend neste plano. |
| "Arrastar-e-soltar tem o mesmo bug" | ❌ **falso** | O drop está na raiz do painel e acrescenta à fila aberta ([useDropZone.js](../web/static/js/components/contacts/hooks/useDropZone.js)). Só o colar depende da `<textarea>`. |
| "Precisa mexer no rascunho (`drafts.js`)" | ❌ **falso** | O texto continua sendo `input`; muda **quem o consome** no envio. `clearDraft` já é chamado no envio de texto ([useComposer.js:231](../web/static/js/components/contacts/hooks/useComposer.js#L231)) e será chamado no envio com anexo. |
| "Os limites de tamanho/formato precisam mudar" | ❌ **falso** | `applyUploadLimits` + `checkMediaFile` rodam no enfileiramento e são ortogonais ao layout ([useMediaUpload.js:169-215](../web/static/js/components/contacts/hooks/useMediaUpload.js#L169-L215)). |
| "Dá para citar uma mensagem ao enviar mídia" | ❌ **fora de escopo** | Nenhuma rota de mídia aceita `reply_to` (§2.4). Não é regressão — hoje o compositor com citação também some. Não vira requisito aqui. |

---

## 4. Fases / Roadmap

```
WAVE 0   F0 (caracterização)  ·  F1 (submitPlan puro)          ← 🟢 paralelas
              │                        │
              └──────── barreira ──────┘   (F2 depende do módulo puro de F1)

WAVE 1   F2 (Composer não desmonta) → F3 (bandeja + legenda unificada)   🔴 em série
                                            │
WAVE 2   F4 (wiring do submit/Enter) ─┬─ F5 (áudio, decide P1)   ← F5 e F6 🟢 entre si
                                      └─ F6 (colar global, opcional)
                                            │
WAVE 3   F7 (testes + dark mode + validação manual)   🔴 sozinha
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | caracterização | 🟢 | baixo | testes puros do comportamento atual verdes |
| 0 | **F1** | `services/composerSubmit.js` | 🟢 | baixo | `node --test` do módulo novo verde |
| 1 | **F2** | `Composer.js` layout | 🔴 [depende de: F1] | médio | colar 2× seguidas com texto digitado acrescenta 2 itens |
| 1 | **F3** | bandeja + legenda única | 🔴 [depende de: F2] | médio | não existe mais campo de legenda separado |
| 2 | **F4** | wiring `ContactDetail`/Enviar/Enter | 🟢 [depende de: F3] | médio | Enviar manda mídia **com** o texto como legenda |
| 2 | **F5** | áudio (P1) | 🟢 | médio | decisão de P1 implementada e observável |
| 2 | **F6** | colar global (opcional) | 🟢 | baixo | Ctrl+V com foco fora da textarea enfileira |
| 3 | **F7** | testes + tema + manual | 🔴 | baixo | checklist do §7 inteiro marcado |

---

### Fase F0 — Caracterização (rede de segurança ANTES de mexer)

**Objetivo:** travar por teste o que **não pode mudar** no envio de mídia.

**Itens** `[paralelo]`
1. Rodar a suíte pura atual: `node --test web/static/js/services/*.test.js` — anotar o baseline.
2. Cobrir em [mediaQueue.test.js](../web/static/js/services/mediaQueue.test.js) (ou arquivo novo) o que a
   refatoração pode quebrar sem avisar: `buildQueueItems` acrescenta (não substitui); a legenda vai só no
   **primeiro** item ([useMediaUpload.js:478](../web/static/js/components/contacts/hooks/useMediaUpload.js#L478));
   áudio nunca leva legenda.
3. Registrar o roteiro manual do estado atual (texto + colar → o texto some; segundo Ctrl+V é ignorado;
   arrastar continua acrescentando) para comparar depois.

**Pronto quando:** baseline verde registrado e o roteiro manual escrito no bloco de status abaixo.

#### Status de execução — Fase 0
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

### Fase F1 — `services/composerSubmit.js` (puro)

**Objetivo:** tirar do JSX a pergunta "o que este Enviar faz agora?".

**Itens** `[sequencial dentro da fase]`
1. Criar `web/static/js/services/composerSubmit.js` — **sem** preact, DOM ou rede (padrão de
   [mediaQueue.js](../web/static/js/services/mediaQueue.js)).
2. Assinatura ilustrativa:
   ```js
   // → { action: 'noop'|'text'|'media'|'template', caption: string, reason?: string }
   export function submitPlan({ text, queueLength, queueIsAudioOnly, mode, sessionClosed, sending })
   ```
3. Regras a codificar (todas já existentes hoje, só reunidas):
   - `sending` ⇒ `noop` ([useMediaUpload.js:454](../web/static/js/components/contacts/hooks/useMediaUpload.js#L454));
   - sem texto e sem fila ⇒ `noop` ([useComposer.js:214](../web/static/js/components/contacts/hooks/useComposer.js#L214));
   - `sessionClosed && mode !== 'private'` ⇒ `template` (vale para texto **e** mídia hoje:
     [useComposer.js:218-221](../web/static/js/components/contacts/hooks/useComposer.js#L218-L221),
     [useMediaUpload.js:457-461](../web/static/js/components/contacts/hooks/useMediaUpload.js#L457-L461));
   - fila > 0 ⇒ `media` com `caption = text.trim()` (respeitando **P1** para áudio);
   - senão ⇒ `text`.
4. `composerSubmit.test.js` com `node --test` cobrindo a matriz (texto×fila×modo×janela×enviando).

**Pronto quando:** `node --test web/static/js/services/composerSubmit.test.js` verde e o módulo não importa nada de `preact`.

#### Status de execução — Fase 1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase F2 — O compositor deixa de desmontar

**Objetivo:** a causa-raiz. Barra de entrada e fila coexistem.

**Itens**
1. `[sequencial]` Em [Composer.js:143](../web/static/js/components/contacts/Composer.js#L143), remover o
   ramo `hasPending ? ''` do ternário. A gravação de áudio (`recording`) **continua** substituindo a
   barra — é um estado modal de verdade (o operador está gravando).
2. `[sequencial]` Garantir que a bandeja ([Composer.js:106-122](../web/static/js/components/contacts/Composer.js#L106-L122))
   fique **acima** do `<form>` e dentro do mesmo `shrink-0`, para não roubar altura da lista de mensagens.
3. `[paralelo]` Conferir que a `<textarea>` permanece montada ⇒ `onPaste` vivo ⇒ o 2º/3º Ctrl+V acrescenta.
4. `[paralelo]` Conferir que a barra continua escondida quando `!canSend` (grupo somente-leitura,
   [Composer.js:134-142](../web/static/js/components/contacts/Composer.js#L134-L142)) e que a bandeja
   também some nesse caso (ela já é gateada por `hasPending && canSend`).

**Pronto quando:** com texto digitado, colar duas imagens seguidas mostra **2** miniaturas, o texto continua
visível na `<textarea>` e o alternador Responder/Privada + a citação continuam acessíveis.

#### Status de execução — Fase 2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase F3 — A prévia vira bandeja; a legenda é o texto do compositor

**Objetivo:** um só campo de texto na tela.

**Itens**
1. `[sequencial]` [MediaQueuePreview.js](../web/static/js/components/contacts/MediaQueuePreview.js): remover
   o `<input>` de legenda ([:102-110](../web/static/js/components/contacts/MediaQueuePreview.js#L102-L110)) e o
   botão **Enviar** ([:142-147](../web/static/js/components/contacts/MediaQueuePreview.js#L142-L147)); manter
   miniaturas + `✕` por item + um "Limpar" discreto (o `onCancel` que já existe). Considerar renomear o
   arquivo para `MediaTray.js` (**P6**).
2. `[sequencial]` Encolher o layout: a faixa passa a conviver com a barra de entrada, então a prévia
   grande de imagem única ([:89-92](../web/static/js/components/contacts/MediaQueuePreview.js#L89-L92)) vira
   miniatura como as demais (uma prévia de 200px empurraria a conversa a cada colar).
3. `[sequencial]` [useMediaUpload.js](../web/static/js/components/contacts/hooks/useMediaUpload.js):
   `confirmQueue(caption = '')` passa a receber a legenda; remover `mediaCaption`/`setMediaCaption`
   ([:81](../web/static/js/components/contacts/hooks/useMediaUpload.js#L81), [:522](../web/static/js/components/contacts/hooks/useMediaUpload.js#L522))
   e a limpeza dele ([:301](../web/static/js/components/contacts/hooks/useMediaUpload.js#L301), [:465](../web/static/js/components/contacts/hooks/useMediaUpload.js#L465)).
   A regra "legenda só no primeiro item" ([:478](../web/static/js/components/contacts/hooks/useMediaUpload.js#L478)) **fica**.
4. `[paralelo]` Toggles "IA lê"/"IA responde no chat" do áudio privado
   ([MediaQueuePreview.js:113-130](../web/static/js/components/contacts/MediaQueuePreview.js#L113-L130)):
   com a barra visível eles já existem no compositor ([Composer.js:178-201](../web/static/js/components/contacts/Composer.js#L178-L201))
   — remover a duplicata da bandeja.

**Pronto quando:** existe **um** campo de texto na tela; digitar antes ou depois de colar leva a mesma
legenda; o `✕` de cada miniatura ainda revoga o `objectURL`
([useMediaUpload.js:286-292](../web/static/js/components/contacts/hooks/useMediaUpload.js#L286-L292)).

#### Status de execução — Fase 3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase F4 — Wiring: Enviar, `Enter`, rascunho e janela de 24h

**Objetivo:** um gesto de envio, roteado pelo módulo puro da F1.

**Itens**
1. `[sequencial]` [ContactDetail.js:290-296](../web/static/js/components/contacts/ContactDetail.js#L290-L296):
   `handleSendGuarded` passa a consultar `submitPlan`. `action:'media'` ⇒ `media.confirmQueue(caption)` +
   limpar o texto/rascunho; `action:'text'` ⇒ caminho atual; `action:'template'` ⇒ `openTemplatePicker()`
   **sem** apagar texto nem fila (hoje `confirmQueue` **descarta a fila** nesse caso —
   [useMediaUpload.js:458](../web/static/js/components/contacts/hooks/useMediaUpload.js#L458) — o que com
   texto-como-legenda passaria a jogar fora o trabalho do operador; ver **Riscos**).
2. `[sequencial]` `Enter` ([ContactDetail.js:594-600](../web/static/js/components/contacts/ContactDetail.js#L594-L600))
   passa pelo mesmo roteamento — o autocomplete continua consumindo primeiro.
3. `[sequencial]` Botão Enviar visível com `hasText || hasPending`
   ([Composer.js:362](../web/static/js/components/contacts/Composer.js#L362)); com fila pendente o
   microfone não deve aparecer no lugar dele.
4. `[paralelo]` Limpeza do texto: reusar `applyInput('')` + `clearDraft`
   ([useComposer.js:230-231](../web/static/js/components/contacts/hooks/useComposer.js#L230-L231)) exportando
   um `consumeInput()` do `useComposer`, em vez de o `ContactDetail` mexer no rascunho por fora.
5. `[paralelo]` Presença: `stopPresence` ([useComposer.js:367](../web/static/js/components/contacts/hooks/useComposer.js#L367))
   já existe para "enviar mídia antes do form" — chamar no envio com anexo.
6. `[paralelo]` Decidir a conversão de marcação (**P2**): o texto de mensagem passa por `toWhatsAppMarkup`
   ([useComposer.js:213](../web/static/js/components/contacts/hooks/useComposer.js#L213)); a legenda hoje vai crua.

**Pronto quando:** digitar "segue o comprovante", colar a imagem e apertar `Enter` produz **uma** bolha de
imagem com a legenda certa, o compositor fica vazio, o rascunho some da sidebar e nada é enviado duas vezes.

#### Status de execução — Fase 4
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase F5 — Áudio (implementa a decisão de P1)

**Objetivo:** o único tipo que não aceita legenda não pode virar armadilha silenciosa.

**Itens**
1. `[sequencial]` Implementar a opção escolhida em **P1** (recomendação: enviar o texto como mensagem
   separada **antes** do áudio, e nunca descartá-lo em silêncio).
2. `[paralelo]` `setPendingAudio` **substitui** a fila hoje ([useMediaUpload.js:280](../web/static/js/components/contacts/hooks/useMediaUpload.js#L280)):
   decidir se um clipe gravado com arquivos já enfileirados substitui, acrescenta ou é recusado com aviso.
3. `[paralelo]` A gravação continua sendo estado modal (F2, item 1) — só a **confirmação** convive com a barra.

**Pronto quando:** gravar um áudio com texto digitado tem resultado previsível e visível (nada some sem aviso).

#### Status de execução — Fase 5
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase F6 — Colar com o foco fora da `<textarea>` (opcional)

**Objetivo:** paridade final com o Telegram — Ctrl+V funciona "na conversa", não só "no campo".

**Itens**
1. `[sequencial]` Listener de `paste` no `document` enquanto a conversa está aberta
   ([ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js), junto do `drop`), delegando ao
   mesmo `media.requestFilesDrop(files, 'media')`.
2. `[sequencial]` Guardas obrigatórias: ignorar quando o alvo é `input`/`textarea`/`[contenteditable]` de
   **outro** componente (busca na conversa, editar mensagem, filtros, telas de plugin), quando `!canReply`,
   quando `media.sending` e quando um modal está aberto — mesmas condições que já desabilitam o `useDropZone`
   ([ContactDetail.js:318-321](../web/static/js/components/contacts/ContactDetail.js#L318-L321)).

**Pronto quando:** Ctrl+V com o foco na lista de mensagens enfileira o anexo; colar texto dentro da busca da
conversa continua colando texto normalmente.

#### Status de execução — Fase 6
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase F7 — Testes, tema e validação manual

**Itens**
1. `[paralelo]` `node --test web/static/js/services/*.test.js` verde (inclui `composerSubmit.test.js`).
2. `[paralelo]` Suíte do core no Postgres (`WHATSBOT_TEST_DB_URL`): `venv/bin/python -m pytest` — nada de
   backend muda, então serve de guarda contra dano colateral.
3. `[paralelo]` Modo escuro na bandeja nova (classes `wa-*`; sem cor crua fora da lista coberta por
   `custom.css`).
4. `[sequencial]` Roteiro manual completo — §7.

#### Status de execução — Fase 7
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Janela de 24h fechada com fila + texto | `confirmQueue` hoje **descarta a fila** e abre o seletor de template ([useMediaUpload.js:457-461](../web/static/js/components/contacts/hooks/useMediaUpload.js#L457-L461)). Com texto-como-legenda, o operador perderia texto **e** anexo de uma vez | F4 item 1: `action:'template'` **não** limpa nada; a fila só é consumida quando o envio de fato acontece |
| `Esc` global | Hoje `Esc` cancela o lote inteiro de qualquer lugar da página ([MediaQueuePreview.js:64-70](../web/static/js/components/contacts/MediaQueuePreview.js#L64-L70)). Com o compositor vivo, digitar e apertar `Esc` (reflexo de "fechar menu") apagaria 5 anexos sem confirmação | **P5**; no mínimo, restringir o listener ao contexto da conversa e não disparar quando há texto no compositor |
| Perda de `objectURL` | Cada item guarda um `previewUrl` revogado no `✕`/cancelar/unmount ([:286](../web/static/js/components/contacts/hooks/useMediaUpload.js#L286), [:294](../web/static/js/components/contacts/hooks/useMediaUpload.js#L294), [:510](../web/static/js/components/contacts/hooks/useMediaUpload.js#L510)). Reescrever a bandeja pode perder um caminho de revogação | Não tocar em `removePendingItem`/`cancelPendingMedia`; só quem os chama muda |
| Legenda não passa pelos filtros de saída | O que hoje é mensagem de texto passa por `filter.reply.part`/`filter.outbound.text`; como legenda, **não** passa (§2.4). Um plugin de assinatura automática deixaria de assinar | **P3** — decidir explicitamente; se for bloqueante, a alternativa é enviar texto e mídia como duas mensagens |
| `@menção` em grupo dentro da legenda | `/send-image` não recebe `mentions` (§2.4): `@Fulano` numa legenda vira texto literal, sem menção real | **P4** — no mínimo, avisar na UI (ou desabilitar o autocomplete de menção quando há fila) |
| Altura do painel | Bandeja + faixa de progresso + aviso de 24h + citação simultâneos podem comer a conversa em telas baixas | Bandeja compacta (F3 item 2), miniaturas com rolagem horizontal (já existe em [:94](../web/static/js/components/contacts/MediaQueuePreview.js#L94)) |
| Duplo envio | `Enter` e clique no botão passam a ter dois destinos possíveis | `submitPlan` devolve `noop` quando `sending`; guarda já existe em [useMediaUpload.js:454](../web/static/js/components/contacts/hooks/useMediaUpload.js#L454) |
| Sandbox | O mesmo `Composer` roda no sandbox (`sandbox=true`), onde não há rascunho e a mídia entra como "recebida" ([useMediaUpload.js:315](../web/static/js/components/contacts/hooks/useMediaUpload.js#L315)) | Incluir o sandbox no roteiro manual |
| Nota privada | Modo privado tem rotas próprias de mídia com legenda ([contacts.py:1807-1830](../server/routes/contacts.py#L1807-L1830)) | Testar "texto + imagem" nos **dois** modos |

---

## 6. Perguntas em aberto

**P1 — Texto + áudio: para onde vai o texto?**
`/send-audio` não aceita legenda por contrato ([contacts.py:2113](../server/routes/contacts.py#L2113)).
(a) enviar o texto como **mensagem separada antes** do áudio; (b) bloquear o envio com aviso ("nota de voz
não aceita legenda"); (c) descartar o texto (❌ inaceitável — perda silenciosa).
**Recomendação: (a)** — é o que o Telegram faz na prática e nunca perde trabalho do operador.
⏸️ **ADIADO** — decidir antes da F5.

**P2 — A legenda passa por `toWhatsAppMarkup`?**
Hoje o texto de mensagem converte `**negrito**` → `*negrito*` ([useComposer.js:213](../web/static/js/components/contacts/hooks/useComposer.js#L213));
a legenda vai crua. Com o mesmo campo servindo aos dois, escrever `**oi**` daria resultados diferentes
conforme houvesse anexo. (a) aplicar a conversão também na legenda; (b) manter cru.
**Recomendação: (a)** — o realce visual do compositor ([Composer.js:343-348](../web/static/js/components/contacts/Composer.js#L343-L348))
já promete negrito ao operador. ⏸️ **ADIADO** — decidir na F4.

**P3 — Legenda deixa de passar pelos filtros de plugin. Aceitamos?**
(a) aceitar (o comportamento de legenda hoje já é esse); (b) enviar texto e mídia como **duas** mensagens
quando houver texto (preserva os filtros, mas polui a conversa do cliente e é o oposto do pedido);
(c) estender o caminho de mídia no backend para aplicar `filter.outbound.text` à legenda (fora do escopo
frontend-only). **Recomendação: (a)** agora, registrando (c) como trabalho separado. ⏸️ **ADIADO**.

**P4 — `@menção` de grupo na legenda.**
Sem `mentions` na rota de mídia, a menção não é real. (a) aceitar e documentar; (b) desabilitar o
autocomplete de `@` quando há fila pendente; (c) avisar com toast ao enviar.
**Recomendação: (b)** — não prometer o que não entrega. ⏸️ **ADIADO**.

**P5 — O que `Esc` faz com a bandeja aberta?**
(a) só limpa a fila quando o compositor está **vazio**; (b) `Esc` deixa de cancelar o lote (só o botão
"Limpar" e o `✕` de cada item); (c) `Esc` remove o **último** item.
**Recomendação: (a)** — mantém o atalho e elimina a destruição acidental. ⏸️ **ADIADO** — decidir na F3.

**P6 — Renomear `MediaQueuePreview` → `MediaTray`?**
O componente deixa de ser uma "prévia de confirmação". (a) renomear (mais honesto; toca 2 imports);
(b) manter o nome. **Recomendação: (a)**, num commit só de rename, separado do de comportamento.
⏸️ **ADIADO**.

---

## 7. Checklist de verificação

- [ ] `node --test web/static/js/services/*.test.js` verde (inclui `composerSubmit.test.js` novo)
- [ ] `venv/bin/python -m pytest` verde no Postgres de teste (`WHATSBOT_TEST_DB_URL`)
- [ ] **Colar com texto:** digitar → Ctrl+V imagem → o texto **continua visível**, a miniatura aparece
- [ ] **Continuar colando:** 2ª e 3ª imagem coladas **acrescentam** à bandeja (não substituem, não são ignoradas)
- [ ] **Misturar gestos:** colar + arrastar + menu de anexo na mesma bandeja
- [ ] **Enviar:** o texto vira legenda do **primeiro** item; compositor e rascunho ficam limpos; sem envio duplo
- [ ] **Cancelar:** `✕`/Limpar devolve o compositor intacto com o texto preservado
- [ ] **Teto:** 11 arquivos ⇒ aviso de 10 por vez; arquivo fora do limite do canal ⇒ `MediaRejectedModal` e o item não entra
- [ ] **Janela de 24h (WhatsApp Cloud):** texto + anexo ⇒ seletor de template **sem** perder texto nem fila
- [ ] **Nota privada:** texto + imagem em modo privado grava a nota com legenda
- [ ] **Sandbox:** mesmo fluxo, mídia entra como "recebida"
- [ ] **Áudio:** gravar com texto digitado segue a decisão de P1, sem perda silenciosa
- [ ] **Somente leitura:** grupo sem permissão de envio não mostra bandeja nem compositor
- [ ] **Modo escuro:** bandeja legível (fundo, borda, nome do arquivo, botão `✕`)
- [ ] **Reload / back-forward:** rascunho restaurado; fila pendente **não** persiste (esperado — `objectURL` morre)
- [ ] Sem regressão no `Enter`/`Shift+Enter`, no autocomplete `@`//`atalho` e na prévia de citação

---

## 8. Apêndice — arquivos-chave

**Frontend — componentes**
- [web/static/js/components/contacts/Composer.js](../web/static/js/components/contacts/Composer.js) — o ternário da causa-raiz (`:143`), botão enviar (`:362`), `onPaste` (`:355`)
- [web/static/js/components/contacts/MediaQueuePreview.js](../web/static/js/components/contacts/MediaQueuePreview.js) — vira bandeja
- [web/static/js/components/contacts/ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js) — wiring dos hooks (`:264`, `:304`, `:318`), `Enter` (`:594`)

**Frontend — hooks**
- [web/static/js/components/contacts/hooks/useMediaUpload.js](../web/static/js/components/contacts/hooks/useMediaUpload.js) — fila, colar, legenda, `confirmQueue`
- [web/static/js/components/contacts/hooks/useComposer.js](../web/static/js/components/contacts/hooks/useComposer.js) — texto, rascunho, envio de texto, presença
- [web/static/js/components/contacts/hooks/useDropZone.js](../web/static/js/components/contacts/hooks/useDropZone.js) — referência do gesto que **já** funciona

**Frontend — serviços puros (`node --test`)**
- `web/static/js/services/composerSubmit.js` — **novo** (F1)
- [web/static/js/services/mediaQueue.js](../web/static/js/services/mediaQueue.js) · [uploadLimits.js](../web/static/js/services/uploadLimits.js) · [mediaLimits.js](../web/static/js/services/mediaLimits.js) · [drafts.js](../web/static/js/services/drafts.js)

**Backend (leitura apenas — não muda neste plano)**
- [server/routes/contacts.py](../server/routes/contacts.py) — `/send-image` (`:1984`), `/send-audio` (`:2040`), `/send-document` (`:2132`), `/send-video` (`:2194`)
