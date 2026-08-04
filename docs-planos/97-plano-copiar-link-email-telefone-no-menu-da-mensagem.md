# Plano 97 — Copiar/abrir link, e-mail e telefone pelo menu de contexto da mensagem (paridade com Chatwoot/WhatsApp)

> **Status:** ✅ IMPLEMENTADO (2026-07-30) — F0→F5 executadas; falta só a validação visual no navegador (§7, itens 👁️) e a suíte pytest, não rodada por instrução do operador · **Data:** 2026-07-30 · **Escopo:** pequeno/médio (frontend puro — zero backend, zero DB, zero migration)
> **Origem:** relato de operador — "no Chatwoot e no WhatsApp eu seguro o dedo (ou clico com o botão direito) em cima do link e consigo *copiar só o link*, ou *ir para o link*. No WhatsBot não consigo — e queria o mesmo para e-mail e telefone". **Método:** leitura do renderizador de texto da bolha + do menu de contexto da mensagem, com `arquivo:linha` verificado (`grep`/`sed` sobre `web/static/js`), incluindo a checagem de qual regra de formatação roda em qual ordem.
> **Achado central:** *ir para o link já funciona* (a bolha renderiza `<a target="_blank">` desde sempre — [formatWhatsApp.js:41-42](../web/static/js/utils/formatWhatsApp.js#L41-L42)). O que está quebrado é **o botão direito**: a bolha chama `e.preventDefault()` ([ContactDetail.js:439](../web/static/js/components/contacts/ContactDetail.js#L439)) e substitui o menu **nativo** do navegador — que traria "Copiar endereço do link" — pelo menu da mensagem, que **não tem nenhum item de link**. O operador perdeu o item nativo e não ganhou um equivalente. E-mail e telefone nem sequer são detectados no texto.
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| D1 ✅ (2026-07-30) | O menu de contexto da mensagem **continua sendo o nosso** (não voltamos ao menu nativo do navegador). O que faltava eram os **itens** | F3 acrescenta itens **contextuais** ao topo de `buildBaseItems` ([ContactDetail.js:402](../web/static/js/components/contacts/ContactDetail.js#L402)); o `preventDefault()` fica |
| D2 ✅ (2026-07-30) | **Frontend puro.** Nenhuma rota, coluna, migration, config ou evento novo. Nada muda no que é enviado ao provedor nem no que é gravado em `messages` | O plano não toca em `server/`, `db/`, `agent/`. A suíte Postgres deve ficar **byte-idêntica** |
| D3 ✅ (2026-07-30) | Detecção de entidade é **puro e testável** (`node --test`), fora do componente | Módulo novo `web/static/js/services/messageEntities.js`, no padrão de `mediaLimits.js` / `drafts.js` / `conversationRows.js` |
| D4 ✅ (2026-07-30) | **Telefone é detectado de forma conservadora.** Linkificar todo número seria um desastre neste produto (protocolos `PROT-12345678`, valores, datas, ids de pedido) | F1 só aceita telefone com marca explícita de telefone (`+` internacional, ou máscara BR `(11) 99999-8888`) — ver §2.4 |
| D5 ✅ (2026-07-30) | O texto continua **escapado antes** de qualquer formatação. A ordem "escapa → formata" de [formatWhatsApp.js:21](../web/static/js/utils/formatWhatsApp.js#L21) é invariante de segurança e não se mexe nela | F2 insere a linkificação **depois** do escape, nunca antes; nenhum `href` é montado a partir de texto não-escapado |
| D6 ✅ (2026-07-30) | Nenhuma mudança no seam de plugin `filter.message.contextMenu.items` | Os itens contextuais entram na **base**, que o filtro recebe normalmente ([ContactDetail.js:446](../web/static/js/components/contacts/ContactDetail.js#L446)) — o `melhorias` continua acrescentando o dele por cima, sem alteração |

---

## 1. Resumo executivo

Ao clicar com o botão direito numa bolha de mensagem, o WhatsBot abre o **seu** menu (Responder / Editar / Copiar / Copiar link da mensagem / Apagar) e **cancela o menu nativo** do navegador. Consequência não intencional: um link dentro da mensagem perde o "Copiar endereço do link" nativo e **não ganha substituto** — o "Copiar" do nosso menu copia a mensagem **inteira**, não o link.

Além disso, o renderizador só reconhece **um** tipo de entidade em texto livre: URL `http(s)` ([formatWhatsApp.js:41](../web/static/js/utils/formatWhatsApp.js#L41)). **E-mail não é detectado** e **telefone não é detectado** — só existe uma regra cosmética que pinta `<dígitos>@<domínio>` de azul sem torná-lo clicável ([formatWhatsApp.js:45-46](../web/static/js/utils/formatWhatsApp.js#L45-L46)), pensada para JID do WhatsApp e que hoje **captura e-mails com usuário numérico por engano** (`5511999@gmail.com`).

A forma da solução tem duas camadas independentes:

- **Camada A — render:** um módulo **puro** detecta URL / e-mail / telefone no texto **já escapado** e emite âncoras com `data-entity` + `data-value`. E-mail vira `mailto:`, telefone vira `tel:`.
- **Camada B — menu:** ao abrir o menu, resolvemos a entidade **sob o cursor** (`e.target.closest('[data-entity]')`) e **prefixamos** itens contextuais: *Abrir link · Copiar link* / *Enviar e-mail · Copiar e-mail* / *Copiar número · Conversar no WhatsApp*. Sem entidade sob o cursor, o menu é **byte-idêntico** ao de hoje.

Brinde barato da mesma mudança: quando há **texto selecionado** dentro da bolha, o menu ganha "Copiar seleção" — hoje o `preventDefault()` também mata o "Copiar" nativo da seleção.

---

## 2. Como funciona hoje (mapa)

### 2.1 O renderizador de texto da bolha

[web/static/js/utils/formatWhatsApp.js](../web/static/js/utils/formatWhatsApp.js) — 116 linhas, função `formatWhatsApp(text, mentionNames)` ([:19](../web/static/js/utils/formatWhatsApp.js#L19)). Ordem exata das regras:

| # | Linha | Regra | Observação |
|---|---|---|---|
| 1 | [:21](../web/static/js/utils/formatWhatsApp.js#L21) | `escapeHtml` | ⚠️ **invariante de segurança** (D5): tudo depois opera sobre string escapada |
| 2 | [:24-29](../web/static/js/utils/formatWhatsApp.js#L24-L29) | ``` ``` ``` e `` ` `` | code block antes de inline |
| 3 | [:32](../web/static/js/utils/formatWhatsApp.js#L32) | `*negrito*` | |
| 4 | [:35](../web/static/js/utils/formatWhatsApp.js#L35) | `_itálico_` | `\b` para não pegar `_` de URL |
| 5 | [:38](../web/static/js/utils/formatWhatsApp.js#L38) | `~tachado~` | |
| 6 | [:41-42](../web/static/js/utils/formatWhatsApp.js#L41-L42) | **URL** → `<a href target="_blank" rel="noopener noreferrer">` | **já existe e já funciona**: clicar abre em nova aba |
| 7 | [:45-46](../web/static/js/utils/formatWhatsApp.js#L45-L46) | `(\d{7,15})@([\w.]+)` → `<span>` azul, `cursor:default` | pensado para JID; **não é clicável**; ver o bug de colisão em §2.4 |
| 8 | [:54-61](../web/static/js/utils/formatWhatsApp.js#L54-L61) | `@menções` + `@todos` | |

O resultado é injetado por `dangerouslySetInnerHTML`. **Um único ponto de entrada**: `fmt()` em [ContactDetail.js:321-326](../web/static/js/components/contacts/ContactDetail.js#L321-L326), passado como prop para:

| Consumidor | Linha |
|---|---|
| `MessageBubble` (corpo da bolha) | [MessageBubble.js](../web/static/js/components/contacts/MessageBubble.js) |
| `MediaContent` (legendas de imagem/áudio/vídeo/documento) | [MediaContent.js:60,68,79,137,142](../web/static/js/components/contacts/MediaContent.js#L60) |
| `SystemMessageCard` (nota privada, transcrição, avisos, CTA) | [SystemMessageCard.js:78,102,120,144,160,185,212](../web/static/js/components/contacts/SystemMessageCard.js#L78) |

✅ Consequência boa: **mudar `formatWhatsApp` cobre os três de uma vez**, inclusive legendas de mídia e notas privadas. `grep -rn formatWhatsApp web assets storages` confirma que **nenhum outro** lugar do painel (sidebar, busca, preview) usa a função — a sidebar mostra texto cru, então não há risco de âncora aparecer numa linha de lista.

### 2.2 O menu de contexto da mensagem

Fluxo verificado:

1. Botão direito na bolha → `onContextMenu` em [MessageBubble.js:66](../web/static/js/components/contacts/MessageBubble.js#L66) (e em [SystemMessageCard.js:56](../web/static/js/components/contacts/SystemMessageCard.js#L56) para a nota privada) chama `openMsgMenu(e, m, isFromMe)`.
2. [ContactDetail.js:438-451](../web/static/js/components/contacts/ContactDetail.js#L438-L451) — `openMsgMenu`:
   - [:439](../web/static/js/components/contacts/ContactDetail.js#L439) **`e.preventDefault()`** ← ⚠️ **é aqui que o menu nativo morre**
   - monta `buildBaseItems(message, isFromMe)` ([:402-432](../web/static/js/components/contacts/ContactDetail.js#L402-L432))
   - aplica `filter.message.contextMenu.items` ([:446](../web/static/js/components/contacts/ContactDetail.js#L446)) — seam de plugin, documentado em [registry.js:42-49](../web/static/js/plugins/registry.js#L42-L49)
   - `actions.setMsgMenu({x, y, message, isFromMe, items})`
3. [MessageContextMenu.js:88-103](../web/static/js/components/contacts/MessageContextMenu.js#L88-L103) renderiza a lista `{label, icon, onClick, disabled?, danger?}` e faz clamp no viewport ([:37-49](../web/static/js/components/contacts/MessageContextMenu.js#L37-L49)).

Itens base de hoje ([ContactDetail.js:403-431](../web/static/js/components/contacts/ContactDetail.js#L403-L431)): Responder (condicional) · Editar (condicional) · **Copiar** · **Copiar link da mensagem** · Selecionar mensagens (só com plugin) · Apagar (condicional).

⚠️ **Armadilha de nomenclatura já existente:** "Copiar link da mensagem" ([:418](../web/static/js/components/contacts/ContactDetail.js#L418)) **não é o link do texto** — é o *permalink interno* do WhatsBot (`/conversations/<id>?message=<id>`, [useMessageActions.js:109-119](../web/static/js/components/contacts/hooks/useMessageActions.js#L109-L119)). Um item novo chamado "Copiar link" ao lado dele confunde. Ver **P1**.

E "Copiar" ([:417](../web/static/js/components/contacts/ContactDetail.js#L417) → [useMessageActions.js:96-103](../web/static/js/components/contacts/hooks/useMessageActions.js#L96-L103)) copia a mensagem **inteira** (tirando o prefixo `[Remetente]: ` de grupo) — nunca uma parte.

### 2.3 Infra que já existe e vai ser reusada (nada a construir)

| Peça | Onde | Uso no plano |
|---|---|---|
| `copyToClipboard(text)` — funciona em **contexto inseguro** (HTTP) via `execCommand` | [MessageContextMenu.js:145-165](../web/static/js/components/contacts/MessageContextMenu.js#L145-L165) | toda ação "Copiar …" |
| `CopyIcon` / `LinkIcon` | [MessageContextMenu.js:111-133](../web/static/js/components/contacts/MessageContextMenu.js#L111-L133) | itens novos (faltam ícones de e-mail/telefone/abrir — F4) |
| `notify(msg, {kind})` — barramento de toast desacoplado do Preact | [services/notify.js](../web/static/js/services/notify.js) | feedback "Link copiado" |
| `formatPhoneDisplay` / `samePhone` | [utils/phone.js:37-58](../web/static/js/utils/phone.js#L37-L58), [:81-87](../web/static/js/utils/phone.js#L81-L87) | rótulo bonito do telefone no item do menu |
| CSP do painel | [server/app.py:645-660](../server/app.py#L645-L660) | ✅ não restringe navegação: não há `form-action` nem `navigate-to`; `mailto:` / `tel:` / `https://wa.me/…` abrem normalmente. **Nada a mudar no backend** |

### 2.4 Falsos positivos e bugs adjacentes (descartados ou anotados)

| Achado | Veredito |
|---|---|
| "O link não abre" | ❌ **Falso positivo.** A âncora existe desde sempre ([formatWhatsApp.js:41-42](../web/static/js/utils/formatWhatsApp.js#L41-L42)) com `target="_blank" rel="noopener noreferrer"`. Clique esquerdo **já vai** para o link. O relato do usuário ("não tenho certeza se funciona") se explica pelo botão direito não oferecer nada |
| `(\d{7,15})@([\w.]+)` captura e-mail de usuário numérico | ⚠️ **Bug real, pequeno e no caminho.** `5511999@gmail.com` casa a regra de JID ([:45](../web/static/js/utils/formatWhatsApp.js#L45)) e vira um `span` morto. F2 corrige **de graça** exigindo sufixo conhecido do WhatsApp (`s.whatsapp.net`, `lid`, `g.us`, `c.us`, `broadcast`, `newsletter`) e rodando a regra de e-mail **antes** |
| Negrito/itálico corrompem URL que contenha `*` ou `_texto_` | ⚠️ **Pré-existente, FORA de escopo.** As regras 3–5 rodam antes da regra 6. Já há uma mitigação parcial (`\b` no itálico, [:35](../web/static/js/utils/formatWhatsApp.js#L35)). Consertar exigiria reordenar/tokenizar o pipeline inteiro — risco alto para ganho marginal. **Não mexer**; anotar em P3 |
| Linkificar telefone em texto livre | ⚠️ **Perigoso neste produto.** O plugin `protocolos` escreve `🔖 Protocolo aberto · PROT-12345678` no fio; há ids de pedido, valores e datas. **D4** limita a detecção a padrão com marca de telefone (ver F1) |
| "Long-press no celular" (o "segurar o dedo" do relato) | ⚠️ **Parcial por natureza da plataforma.** Android/Chrome dispara `contextmenu` no long-press → ganha o menu novo. **iOS/Safari não dispara** `contextmenu`: mostra o *callout* nativo, que **já oferece** "Copiar link" para uma `<a>`. Ou seja, no iOS o comportamento nativo já é o desejado e continua. Sem hack de `touchstart`+timer — ver **P2** |
| Sidebar/preview/busca mostrariam âncoras | ❌ **Falso positivo.** `formatWhatsApp` não é usado lá (`grep` em §2.1). `collapsedPreview` recebe conteúdo **cru** de propósito ([messageView.js:105-112](../web/static/js/services/messageView.js#L105-L112)) |
| E-mail/telefone no painel do contato (`ContactInfoPanel`) | ⏸️ **Fora do escopo desta rodada** — Email virou atributo customizado ([ContactInfoPanel.js:31](../web/static/js/components/contacts/ContactInfoPanel.js#L31)) e há o tipo `link` em [CustomAttributeField.js:48](../web/static/js/components/contacts/CustomAttributeField.js#L48). Ver **P4** |

---

## 3. Inventário do que muda

| # | Arquivo | O que falta hoje | Abordagem | Risco | Esforço |
|---|---|---|---|---|---|
| 1 | `web/static/js/services/messageEntities.js` **(novo)** | não existe | Módulo **puro**: `linkifyEntities(escapedHtml)` + `detectEntity(text)` + `entityFromElement(el)` + `entityActions(entity)` | baixo | M |
| 2 | `web/static/js/services/messageEntities.test.js` **(novo)** | — | `node --test`: URL, e-mail, telefone BR/internacional, JID, negativos (protocolo, valor, data), não-quebra de `&amp;` | baixo | M |
| 3 | [formatWhatsApp.js:40-46](../web/static/js/utils/formatWhatsApp.js#L40-L46) | só URL; JID colide com e-mail | Substituir as regras 6+7 por uma chamada a `linkifyEntities`; endurecer o sufixo de JID | **médio** (é o renderizador de todas as bolhas) | S |
| 4 | `web/static/js/utils/formatWhatsApp.test.js` **(novo)** | **não há teste nenhum** para o renderizador | **Caracterização primeiro** (F0): congela a saída atual antes de mexer | baixo | M |
| 5 | [ContactDetail.js:402-432](../web/static/js/components/contacts/ContactDetail.js#L402-L432) `buildBaseItems` | assinatura não recebe o evento | Passar a entidade/seleção resolvidas e **prefixar** os itens contextuais | baixo | S |
| 6 | [ContactDetail.js:438-451](../web/static/js/components/contacts/ContactDetail.js#L438-L451) `openMsgMenu` | não olha `e.target` | Resolver `e.target.closest('[data-entity]')` + `window.getSelection()` **antes** do `preventDefault()` surtir efeito na leitura | baixo | S |
| 7 | [MessageContextMenu.js:111-139](../web/static/js/components/contacts/MessageContextMenu.js#L111-L139) | faltam ícones | `MailIcon`, `PhoneIcon`, `OpenExternalIcon` no mesmo padrão (24×24, `fill="currentColor"`) | baixo | S |
| 8 | [MessageContextMenu.js:88-103](../web/static/js/components/contacts/MessageContextMenu.js#L88-L103) | itens sem separador | Suporte opcional a `{separator: true}` para destacar o bloco contextual do bloco da mensagem | baixo | S |

**Não muda:** nenhum arquivo em `server/`, `db/`, `agent/`, `channels/`, `assets/plugin_examples/`.

---

## 4. Fases / Roadmap

```
WAVE 0   F0 (caracterização)                              🔴 sozinha, ANTES de tudo
            │ (barreira: congela o HTML atual)
WAVE 1   F1 (módulo puro + testes) · F4 (ícones + separador)      🟢 paralelo
            │ (barreira: F2 e F3 importam de F1)
WAVE 2   F2 (formatWhatsApp delega) · F3 (menu contextual)        🟢 paralelo
            │
WAVE 3   F5 (regressão + validação manual)                🔴 sozinha
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | Testes | 🔴 sozinha | baixo | `formatWhatsApp.test.js` verde contra o código **inalterado** |
| 1 | **F1** | Serviço puro | 🟢 agrupar | baixo | `node --test messageEntities.test.js` verde `[bloqueia: F2, F3]` |
| 1 | **F4** | UI (ícones) | 🟢 agrupar | baixo | ícones renderizam nos dois temas |
| 2 | **F2** | Render | 🟢 agrupar | médio | F0 continua verde (só os casos que o plano MUDA de propósito são reescritos) `[depende de: F1]` |
| 2 | **F3** | Menu | 🟢 agrupar | baixo | itens aparecem só com entidade sob o cursor `[depende de: F1, F4]` |
| 3 | **F5** | Regressão | 🔴 sozinha | baixo | checklist §7 inteiro |

---

### Fase F0 — Caracterizar o renderizador antes de tocar nele 🔴

**Objetivo:** o renderizador de **todas** as bolhas não tem um único teste; mexer nele às cegas é a única parte arriscada deste plano.

**Itens:**
1. `[sequencial]` Criar `web/static/js/utils/formatWhatsApp.test.js` (`node --test`, sem Preact — a função é pura e já é `export`).
2. `[paralelo]` Casos que **congelam o comportamento atual**, um por regra de [§2.1](#21-o-renderizador-de-texto-da-bolha): escape de `<`/`&`/`"`; code block e inline; negrito; itálico com `\b`; tachado; **URL virando `<a target="_blank" rel="noopener noreferrer">`**; `@menção` de membro; `@todos` sem membros; JID `5511999999999@s.whatsapp.net` virando `span`.
3. `[paralelo]` Casos-armadilha que devem continuar iguais: URL com `&` na query (vira `&amp;` no texto **e** no `href`); texto vazio/`null` → `''`; nome de membro com regex especial (`escapeRegex`, [:15-17](../web/static/js/utils/formatWhatsApp.js#L15-L17)).
4. `[sequencial]` **Não** escrever ainda os casos de e-mail/telefone (eles falham por design até F2).

**Pronto quando:** `node --test web/static/js/utils/formatWhatsApp.test.js` verde **sem nenhuma alteração no código-fonte**. Qualquer caso que já falhe aqui é bug pré-existente → anotar, não consertar de carona.

#### Status de execução — Fase 0
**Estado:** ✅ Concluída (2026-07-30)
- **O que foi feito:** criado `web/static/js/utils/formatWhatsApp.test.js` (novo, 15 casos, `node --test`) cobrindo as 8 regras de `formatWhatsApp`: escape (`<`, `&`, `"`, `'`), texto vazio/`null`, HTML injetado que não vira tag, code block antes do inline, negrito/itálico/tachado, `\b` do itálico (`nome_do_arquivo` intacto), URL virando `<a target="_blank" rel="noopener noreferrer">` com o estilo azul, URL com `&` na query (`&amp;` no texto **e** no `href`), JID em `span` não-clicável, `@menção` de membro, `@todos` sem membros, nome com caractere de regex e nome longo vencendo o curto. **Nenhuma linha de código-fonte alterada.**
- **Como foi feito / decisões:** assertivas por **propriedade** (`includes` do que importa) em vez de igualdade de string inteira nos casos com `style` inline — o plano MUDA a âncora de propósito na F2 (ganha `data-entity`/`data-value`), então travar o HTML byte-a-byte transformaria a F2 numa reescrita de teste em massa e esconderia regressão real. Onde a saída é curta e estável (escape, negrito/itálico/tachado) a igualdade exata foi mantida. O caso do JID foi escrito tolerante ao atributo novo (`cursor:default` **ou** `data-entity="jid"`) porque a F2 mantém o `span` mas acrescenta o dado — o que ele realmente trava é "**não** vira âncora".
- **Problemas / pendências:** nenhum caso falhou → nenhum bug pré-existente novo descoberto na caracterização. Os dois já mapeados no plano seguem valendo: colisão JID×e-mail numérico (§2.4, corrigido na F2) e negrito/itálico corrompendo URL (P3, fora de escopo — **não** foi coberto por teste para não congelar comportamento que se quer mudar um dia).
- **Verificação:** `node --test web/static/js/utils/formatWhatsApp.test.js` → **15/15 verde** (`# pass 15 # fail 0`), com `formatWhatsApp.js` intocado.

---

### Fase F1 — Módulo puro `messageEntities.js` 🟢

**Objetivo:** toda a inteligência de "o que é isso no texto e o que dá para fazer com isso" num módulo sem Preact, testável por `node --test` — padrão de [mediaLimits.js](../web/static/js/services/mediaLimits.js) e [drafts.js](../web/static/js/services/drafts.js).

**Itens:**
1. `[sequencial]` Criar `web/static/js/services/messageEntities.js` com quatro exports:

   | Export | Assinatura | Papel |
   |---|---|---|
   | `linkifyEntities` | `(escapedHtml: string) => string` | consome HTML **já escapado** e devolve com as âncoras. Chamado por `formatWhatsApp` (F2) |
   | `detectEntity` | `(text: string) => {kind, value, display} \| null` | detecta numa string curta (fallback de seleção) |
   | `entityFromElement` | `(el: Element \| null) => {kind, value, display} \| null` | lê `dataset.entity`/`dataset.value` do `closest('[data-entity]')` |
   | `entityActions` | `(entity) => Array<{id, label, href?, copy?}>` | a **tabela de ações por tipo** (§ abaixo), sem tocar em DOM nem clipboard |

2. `[paralelo]` **Regras de detecção** (ordem obrigatória, primeira que casa vence):

   | Ordem | `kind` | Padrão | `href` emitido | Nota |
   |---|---|---|---|---|
   | 1 | `url` | `https?://[^\s<]+` (o mesmo de hoje, [:41](../web/static/js/utils/formatWhatsApp.js#L41)) | o próprio | inalterado — F0 protege |
   | 2 | `email` | `[\w.+-]+@[\w-]+(\.[\w-]+)+` | `mailto:<valor>` | ⚠️ **antes** do JID (D4/§2.4) |
   | 3 | `jid` | `\d{7,15}@(s\.whatsapp\.net\|lid\|g\.us\|c\.us\|broadcast\|newsletter)` | — (`span`, como hoje) | sufixo **fechado**, não `[\w.]+` |
   | 4 | `phone` | **conservador (D4)**: `\+\d[\d\s().-]{7,17}\d` **ou** `\(\d{2}\)\s?\d{4,5}-\d{4}` | `tel:+<dígitos>` | exige `+` ou máscara BR completa |

3. `[paralelo]` **Ações por tipo** (`entityActions`) — o que F3 vai renderizar:

   | `kind` | Itens | Detalhe |
   |---|---|---|
   | `url` | *Abrir link* · *Copiar link* | abrir = `window.open(href, '_blank', 'noopener,noreferrer')` (feito por F3, não aqui) |
   | `email` | *Enviar e-mail* · *Copiar e-mail* | `mailto:` |
   | `phone` | *Copiar número* · *Ligar* · *Conversar no WhatsApp* | WhatsApp = `https://wa.me/<só dígitos>`; rótulo usa `formatPhoneDisplay` ([phone.js:37](../web/static/js/utils/phone.js#L37)) |
   | `jid` | *Copiar número* | sem navegação (é identificador interno) |

4. `[sequencial]` **Invariantes de segurança** a codificar e testar:
   - a entrada de `linkifyEntities` é **sempre** HTML já escapado (D5) — o módulo **nunca** escapa nem desescapa;
   - o `href` só pode nascer de um match de `http(s)`, de `mailto:` sobre e-mail casado, ou de `tel:`/`wa.me` sobre **apenas dígitos** — nunca de texto arbitrário. `javascript:` é inalcançável por construção;
   - a âncora **não** pode ser reprocessada por uma regra posterior (o `@` de um `mailto:` dentro de um atributo não pode virar `@menção`) → emitir as âncoras com um **placeholder/token** e reidratar no fim, **ou** garantir que a regra de menção ([:60](../web/static/js/utils/formatWhatsApp.js#L60)) não case dentro de tag. **Decidir na F1 e documentar no cabeçalho do módulo.**

5. `[paralelo]` Criar `messageEntities.test.js` com os positivos da tabela acima **e os negativos que importam**: `PROT-12345678`, `R$ 1.234,56`, `30/07/2026`, `12345678901` (CPF cru), `5511999999999` (número cru sem `+` → **não** vira link, por D4), `5511999@gmail.com` → `email` (não `jid`).

**Pronto quando:** `node --test web/static/js/services/messageEntities.test.js` verde, incluindo todos os negativos. Nenhum arquivo do painel importa o módulo ainda.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-07-30)
- **O que foi feito:** criados `web/static/js/services/messageEntities.js` e `messageEntities.test.js` (29 casos). Exports: `linkifyEntities(escapedHtml)`, `linkifyToTokens(escapedHtml) → {text, restore}` (**5º export, não previsto** — ver decisões), `detectEntity(text)`, `entityFromElement(el)` e `entityActions(entity)`. Um único `ENTITY_RE` com grupos nomeados varre o texto uma vez; `phoneDigits`/`hrefFor`/`displayFor` são privados. Nenhum arquivo do painel importa o módulo ainda.
- **Como foi feito / decisões:**
  - **Anti-reprocessamento (item 4) = token**, não "menção ciente de tag". `linkifyToTokens` troca cada âncora por `U+E000<n>U+E001` (Área de Uso Privado: não é `\w`, não tem `@`/`<`/`*`/`_`/`~`, ninguém digita) e devolve `restore()` para reidratar no FIM do pipeline. É genérico — **qualquer** regra futura fica cega ao markup gerado, não só a menção. `linkifyEntities` é o atalho tokeniza+restaura (usado pelos testes e por quem não tem regras depois). Documentado no cabeçalho do módulo, como o plano exigia.
  - **⚠️ Desvio consciente: JID vem ANTES de e-mail** (o plano pedia e-mail antes). Com o sufixo de JID já fechado em `s.whatsapp.net|lid|g.us|c.us|broadcast|newsletter`, a ordem do plano quebraria o JID: `5511999999999@s.whatsapp.net` **também** casa a forma de e-mail e, com e-mail primeiro, viraria `mailto:` — regressão contra o comportamento de hoje. Com a lista fechada + JID primeiro, os DOIS objetivos do plano convivem: JID de verdade continua `span`, e `5511999@gmail.com` (o bug §2.4) vira e-mail. Testado nos dois sentidos, mais o caso-armadilha `1234567@lidera.com.br` (o sufixo `lid` não pode sequestrar o e-mail — resolvido com o lookahead `(?![\w.-])`).
  - **Detecção só fora de tags:** a entrada é fatiada por `/(<[^>]*>)/` e só os pedaços ímpares (texto) são varridos. Como o texto do usuário já teve `<` virado `&lt;`, as únicas tags ali são as que as regras 2–5 geraram — nenhum `style="…"` pode virar entidade, hoje ou depois.
  - **P5 decidida (recomendação do plano): `wa.me`.** "Conversar no WhatsApp" → `https://wa.me/<dígitos>`; abrir a conversa DENTRO do WhatsBot exigiria resolver contato+canal e fica para o plano seguinte.
  - **P1 decidida (recomendação (a)):** o item da entidade é **"Copiar endereço do link"** (vocabulário do próprio navegador) e o "Copiar link da mensagem" (permalink interno) fica intocado. Zero renomeação para o operador.
  - **Máscara BR ganha `55`:** `(11) 99999-8888` é, por construção, um número BR sem código de país — `phoneDigits` prefixa `55` (só para 10/11 dígitos sem `+`), senão `tel:`/`wa.me` apontariam para lugar nenhum. Com `+` explícito, respeita o que veio.
  - `entityActions` devolve `icon` como **chave** (`'open'|'copy'|'mail'|'phone'`), resolvida para o SVG por quem renderiza — o módulo continua sem tocar em Preact/DOM.
- **Problemas / pendências:** nada bloqueante. Limitação aceita e conhecida: um apóstrofo escapado (`&#39;`) antes de um e-mail encurta o valor casado (`o'brien@x.com` → `brien@x.com`), porque `#`/`;` não estão na classe de caractere do usuário — o efeito é um `mailto:` parcial, nunca HTML quebrado. Negrito/itálico corrompendo URL segue fora de escopo (P3).
- **Verificação:** `node --test web/static/js/services/messageEntities.test.js` → **29/29 verde**. Inclui os positivos (URL, e-mail, `+55 11 99999-8888`, `(11) 99999-8888`, JID), os **negativos** (`PROT-12345678` no formato exato do plugin `protocolos`, `R$ 1.234,56`, `30/07/2026`, CPF cru, `5511999999999` cru, id de pedido `2024-000123456` — todos byte-idênticos na saída), os de **segurança** (`javascript:alert(1)` não vira `href`; `&lt;img … onerror&gt;` passa intacto; URL com `&amp;`/`&quot;` não fecha o atributo; tag existente não é reprocessada) e os de token/reidratação.

---

### Fase F4 — Ícones e separador no menu 🟢

**Objetivo:** o bloco contextual precisa ser visualmente distinto do bloco da mensagem, sem redesenhar o menu.

**Itens:**
1. `[paralelo]` Em [MessageContextMenu.js:111-139](../web/static/js/components/contacts/MessageContextMenu.js#L111-L139), acrescentar `MailIcon`, `PhoneIcon` e `OpenExternalIcon` no **mesmo formato** dos existentes (`html` de `htm`, `viewBox="0 0 24 24"`, `width/height=18`, `fill="currentColor"` — herda a cor do item, então o modo escuro sai de graça).
2. `[sequencial]` Em [MessageContextMenu.js:89-103](../web/static/js/components/contacts/MessageContextMenu.js#L89-L103), suportar `{separator: true}` na lista: renderiza `<div class="my-[4px] border-t border-wa-border">` em vez de `<button>`. ⚠️ A `key` hoje é `item.label` ([:91](../web/static/js/components/contacts/MessageContextMenu.js#L91)) — separadores sem label precisam de key própria (índice).
3. `[sequencial]` Conferir que o clamp de viewport ([:37-49](../web/static/js/components/contacts/MessageContextMenu.js#L37-L49)) continua correto com o menu mais alto — o `useLayoutEffect` depende de `items.length`, que muda; ok.

**Pronto quando:** um item `{separator: true}` injetado à mão renderiza uma linha divisória legível **no claro e no escuro** (token `border-wa-border`, não cor crua — regra de tema do `CLAUDE.md`), e o menu não estoura a janela quando aberto perto da borda inferior.

#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-07-30)
- **O que foi feito:** em [MessageContextMenu.js](../web/static/js/components/contacts/MessageContextMenu.js): (a) três ícones novos exportados — `OpenExternalIcon`, `MailIcon`, `PhoneIcon` — no mesmo formato dos existentes (`viewBox="0 0 24 24"`, 18×18, `fill="currentColor"`); (b) suporte a `{separator: true}` na lista de itens, renderizado como `<div class="my-[4px] border-t border-wa-border">`; (c) o contrato de `items` documentado no cabeçalho do componente.
- **Como foi feito / decisões:** a `key` passou de `item.label` para o **índice + label** (`'item'+i+':'+label`) em vez de só o índice: resolve o separador sem label (a armadilha que o plano apontou) e ainda dá key estável/única quando dois itens compartilham rótulo — hoje "Copiar número" aparece uma vez, mas o bloco contextual e o da mensagem podem colidir no futuro. O separador usa **token de tema** (`border-wa-border`), nenhuma cor crua, então some/aparece corretamente nos dois temas. `fill="currentColor"` faz os ícones herdarem a cor do item (inclusive o vermelho de `danger` e o cinza de `disabled`).
- **Problemas / pendências:** nenhuma. O clamp de viewport (`useLayoutEffect`) já depende de `items.length`, que muda quando o bloco contextual entra — o menu mais alto continua sendo reposicionado antes do paint, sem mudança necessária. O outro consumidor do componente (menu do compositor) não passa separador e fica byte-idêntico.
- **Verificação:** `node --input-type=module --check` no arquivo → sintaxe OK (crase dentro de `html\`\`` é armadilha conhecida do projeto). Validação visual do separador/ícones vai junto com a F5 (§7).

---

### Fase F2 — `formatWhatsApp` delega a linkificação `[depende de: F1]` 🟢

**Objetivo:** trocar as duas regras de entidade ([:40-46](../web/static/js/utils/formatWhatsApp.js#L40-L46)) por uma chamada a `linkifyEntities`, mantendo tudo o mais idêntico.

**Itens:**
1. `[sequencial]` Importar `linkifyEntities` e substituir as linhas [:41-46](../web/static/js/utils/formatWhatsApp.js#L41-L46) por uma chamada única, **na mesma posição do pipeline** (depois de tachado, antes de menções).
2. `[sequencial]` Marcação emitida — âncora **com os dados que a F3 vai ler**:
   ```
   <a href="…" target="_blank" rel="noopener noreferrer"
      data-entity="url|email|phone" data-value="…"
      style="color:#53bdeb;text-decoration:underline;word-break:break-all">…</a>
   ```
   ⚠️ Manter o `style` inline **exatamente** como hoje ([:42](../web/static/js/utils/formatWhatsApp.js#L42)) para não regredir o visual, e manter o `span` de JID com `data-entity="jid"` (segue não-clicável).
3. `[sequencial]` Atualizar `formatWhatsApp.test.js` **só** nos casos que o plano muda **de propósito**: (a) a âncora de URL ganha `data-entity`/`data-value`; (b) `5511999@gmail.com` passa de `span` para `mailto:`. Todo o resto da F0 continua **intacto** — se outro caso quebrar, é regressão, não expectativa nova.
4. `[paralelo]` Acrescentar casos novos: e-mail comum, telefone `+55 11 99999-8888`, telefone `(11) 99999-8888`, e o negativo `PROT-12345678` no formato exato que o plugin `protocolos` escreve.

**Pronto quando:** `node --test` verde nos dois arquivos; abrindo uma conversa real, um link continua azul/sublinhado e **abre no clique** (comportamento de hoje), um e-mail vira link `mailto:` e um telefone com `+` vira link `tel:`; uma mensagem com `PROT-…` **não** ganha link nenhum.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-07-30)
- **O que foi feito:** [formatWhatsApp.js](../web/static/js/utils/formatWhatsApp.js) — as duas regras de entidade (URL → âncora; `(\d{7,15})@([\w.]+)` → span de JID) saíram e viraram uma chamada a `linkifyToTokens(s)` na **mesma posição do pipeline** (depois do tachado, antes das menções); o `return` final passou a ser `linkified.restore(s)`. Único import novo: `../services/messageEntities.js`. Sete casos novos em `formatWhatsApp.test.js` (22 no total).
- **Como foi feito / decisões:**
  - Usa `linkifyToTokens` (não `linkifyEntities`) porque `formatWhatsApp` **tem** regra depois: a menção. Tokenizar antes e reidratar no `return` é o que garante que `@Empresa` não case dentro de `href="mailto:contato@empresa.com"`. Há teste dedicado para exatamente esse cenário (grupo com o membro "Empresa" + link + e-mail na mesma mensagem → duas âncoras íntegras, menção destacada só no texto).
  - `style` inline preservado **caractere por caractere** (`color:#53bdeb;text-decoration:underline;word-break:break-all` na âncora, `…;cursor:default` no span de JID) — zero diferença visual.
  - **Nenhum caso da F0 precisou ser reescrito**, ao contrário do que o plano previa: as assertivas por propriedade absorveram os atributos novos. Os dois pontos que o plano MUDA de propósito viraram casos NOVOS e explícitos (âncora com `data-entity`/`data-value`; `5511999@gmail.com` deixando de ser `span` e virando `mailto:`), então a mudança está travada por teste nos dois sentidos.
- **Problemas / pendências:** nenhuma. Confirmado por `grep` que só `ContactDetail.js` importa `formatWhatsApp` (os demais importam `highlightComposerMarkup`/`toWhatsAppMarkup`, intocados) — o compositor não é afetado.
- **Verificação:** `node --test web/static/js/utils/formatWhatsApp.test.js` → **22/22 verde** (15 de caracterização inalterados + 7 novos: `data-entity`/`data-value`, e-mail numérico virando `mailto:`, e-mail comum, telefone `+` e máscara BR virando `tel:+5511999998888`, `🔖 Protocolo aberto · PROT-12345678` byte-idêntico, menção+link+e-mail juntos, URL dentro de negrito).

---

### Fase F3 — Itens contextuais no menu `[depende de: F1, F4]` 🟢

**Objetivo:** o botão direito sobre um link/e-mail/telefone abre o menu **já com** as ações daquela entidade no topo; fora de entidade, o menu é o de hoje.

**Itens:**
1. `[sequencial]` Em `openMsgMenu` ([ContactDetail.js:438](../web/static/js/components/contacts/ContactDetail.js#L438)), **antes** de montar os itens, resolver dois contextos a partir do evento:
   - `entity = entityFromElement(e.target?.closest?.('[data-entity]'))`;
   - `selectionText` = `window.getSelection()` **não colapsada** e contida na bolha (`anchorNode` dentro de `e.currentTarget`), aparada e limitada (ex.: 5 000 chars).
   ⚠️ Ler **antes** de qualquer `setState`; o `preventDefault()` de [:439](../web/static/js/components/contacts/ContactDetail.js#L439) não afeta a seleção, mas o clique fora fecha o menu ([MessageContextMenu.js:20-22](../web/static/js/components/contacts/MessageContextMenu.js#L20-L22)) — a seleção precisa estar capturada no momento da abertura.
2. `[sequencial]` Passar os dois para `buildBaseItems(message, isFromMe, {entity, selectionText})` ([:402](../web/static/js/components/contacts/ContactDetail.js#L402)) e **prefixar**, nesta ordem:

   | Condição | Itens | Depois |
   |---|---|---|
   | `entity` | itens de `entityActions(entity)` | `{separator:true}` |
   | `selectionText` | *Copiar seleção* | `{separator:true}` |
   | sempre | os itens de hoje, **inalterados** | — |

3. `[paralelo]` Ligar as ações: copiar via `copyToClipboard` ([MessageContextMenu.js:145](../web/static/js/components/contacts/MessageContextMenu.js#L145)); abrir via `window.open(href, '_blank', 'noopener,noreferrer')`. Após cada cópia, `notify('Link copiado.', {kind:'success'})` / `'E-mail copiado.'` / `'Número copiado.'` — o `MessageContextMenu` já fecha sozinho após o `onClick` ([:93](../web/static/js/components/contacts/MessageContextMenu.js#L93)), então não há espaço para o `✓` inline do `CopyLinkButton`.
4. `[sequencial]` **Rótulos** (PT-BR, ver **P1**): usar *"Copiar endereço do link"* para a entidade, deixando *"Copiar link da mensagem"* ([:418](../web/static/js/components/contacts/ContactDetail.js#L418)) intocado — ou renomear os dois; decidir em P1 **antes** de codificar.
5. `[sequencial]` Verificar o caminho da **setinha** de hover ([MessageBubble.js:72-80](../web/static/js/components/contacts/MessageBubble.js#L72-L80)): ela também chama `openMsgMenu`, com `e.target` = o botão, fora de qualquer entidade → `entity = null` → menu **idêntico ao de hoje**. Correto por construção; garantir que nada quebre com `closest` em `e.target` de um `<svg>` (usar `e.target.closest` com guard, pois `SVGElement.closest` existe, mas `e.target` pode ser `#text` em alguns navegadores).
6. `[paralelo]` Idem para a nota privada ([SystemMessageCard.js:56](../web/static/js/components/contacts/SystemMessageCard.js#L56)) — mesma `openMsgMenu`, ganha o recurso de graça.

**Pronto quando:**
- botão direito **em cima de um link** numa bolha → menu abre com *Abrir link* + *Copiar endereço do link* no topo, separador, e o menu de sempre embaixo; "Copiar endereço do link" cola **só a URL**;
- botão direito **em cima de um e-mail** → *Enviar e-mail* + *Copiar e-mail*;
- botão direito **em cima de `+55 11 99999-8888`** → *Copiar número* + *Ligar* + *Conversar no WhatsApp*;
- botão direito **no meio do texto**, sem seleção → menu **byte-idêntico** ao de hoje;
- com texto selecionado → *Copiar seleção* copia exatamente a seleção;
- o item *Gerar melhoria* do plugin `melhorias` ([extends.js:40](../assets/plugin_examples/melhorias/static/extends.js#L40)) continua aparecendo **no fim**, como hoje.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-07-30)
- **O que foi feito:** [ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js) — quatro helpers de módulo novos (`entityMenuItems`, `openEntityHref`, `entityUnderCursor`, `selectionInside`) + as tabelas `ENTITY_ICONS` (chave do módulo puro → SVG) e `COPY_TOASTS`; `buildBaseItems(message, isFromMe, ctx = {})` passou a **prefixar** o bloco contextual (ações da entidade → separador → *Copiar seleção* → separador → itens de hoje, inalterados); `openMsgMenu` resolve `entity` + `selectionText` **antes** de montar os itens. Imports novos: `entityFromElement`/`entityActions`, `notify`, `copyToClipboard` e os três ícones da F4.
- **Como foi feito / decisões:**
  - **P1 aplicada (opção a):** o item da entidade é *"Copiar endereço do link"*; *"Copiar link da mensagem"* (permalink interno) segue com o nome de sempre. Nenhum rótulo existente mudou.
  - **`mailto:`/`tel:` não abrem aba.** `openEntityHref` só usa `window.open(..., 'noopener,noreferrer')` para `http(s)`; esquemas de handler do sistema vão por `window.location.href`, senão o navegador deixa uma aba em branco pendurada. `https://wa.me/…` cai no ramo de aba nova, como esperado.
  - **Leitura síncrona do contexto do clique:** `entity`/`selectionText` são resolvidos na primeira linha de `openMsgMenu`, antes do `await applyFilter` — depois do `await` o evento nativo já perdeu o `currentTarget`, e o clique que fecha o menu desfaria a seleção.
  - **Robustez de `e.target`:** `entityUnderCursor` usa `closest` quando existe e cai no `parentElement.closest` quando o alvo é nó de texto; tudo dentro de `try/catch`. Pela **setinha de hover** o alvo é o `<button>`/`<svg>`, fora de qualquer `[data-entity]` → `entity = null` → menu byte-idêntico ao de hoje (o caminho da setinha nunca ganha bloco contextual, por construção).
  - **Seleção:** só conta se não estiver colapsada e o `anchorNode` estiver **dentro** do `currentTarget` da bolha; aparada e limitada a 5 000 chars, como o plano pediu.
  - **Separador condicional:** o `{separator:true}` só entra quando o bloco correspondente existe — sem entidade e sem seleção, o array é **exatamente** o de antes (nem separador solto no topo).
  - **D6 respeitado:** o filtro `filter.message.contextMenu.items` recebe a base já prefixada, com o mesmo ctx de sempre; o `melhorias` continua dando `push` no fim.
  - **F3 item 6 sai de graça:** a nota privada ([SystemMessageCard.js](../web/static/js/components/contacts/SystemMessageCard.js):56) chama a MESMA `openMsgMenu` — nenhuma linha lá precisou mudar.
- **Problemas / pendências:** o modo de seleção em lote (F5 item 5) precisa de um cuidado real — tratado na F5.
- **Verificação:** `node --input-type=module --check` nos três arquivos alterados → sintaxe OK. Suíte pura completa do painel (`node --test` em todos os `*.test.js` de `web/static/js`) → **454/454 verde**. Validação manual no navegador: §7 (F5).

---

### Fase F5 — Regressão e validação manual 🔴

**Objetivo:** provar que uma mudança no renderizador de **todas** as bolhas não quebrou nada em volta.

**Itens:**
1. `[sequencial]` `node --test` em `formatWhatsApp.test.js` + `messageEntities.test.js` + os módulos puros já existentes que rodam junto (`conversationRows`, `messageView`, `mediaLimits`).
2. `[sequencial]` Suíte Postgres (`WHATSBOT_TEST_DB_URL`) — deve ficar **byte-idêntica**: o plano não toca em Python (D2). Qualquer diferença é sinal de que algo saiu do escopo.
3. `[paralelo]` Validação manual no painel, **claro e escuro**: bolha de entrada, bolha de saída, **legenda de imagem** ([MediaContent.js:60](../web/static/js/components/contacts/MediaContent.js#L60)), **legenda de documento** ([:137](../web/static/js/components/contacts/MediaContent.js#L137)), **nota privada** ([SystemMessageCard.js:78](../web/static/js/components/contacts/SystemMessageCard.js#L78)), **transcrição** ([:102](../web/static/js/components/contacts/SystemMessageCard.js#L102)), **card de CTA** ([:185](../web/static/js/components/contacts/SystemMessageCard.js#L185)) e **sandbox**.
4. `[paralelo]` Mensagem de **grupo** com `@menção` + link + e-mail na mesma linha — nenhuma regra pode comer a outra.
5. `[paralelo]` **Modo seleção em lote** ([MessageBubble.js:59,66](../web/static/js/components/contacts/MessageBubble.js#L59)): com `selectionMode` ligado o `onContextMenu` é `null` — confirmar que continua assim e que clicar numa âncora não dispara navegação em vez de marcar a mensagem (⚠️ **cuidado real**: a âncora captura o clique antes do `onClick` do container). Se acontecer, desativar `pointer-events` das âncoras em `selectionMode`.
6. `[paralelo]` Conferir que nada aparece na **sidebar** (a lista não usa `formatWhatsApp` — §2.1).

**Pronto quando:** checklist §7 inteiro marcado.

#### Status de execução — Fase 5
**Estado:** 🟡 Parcial — automação completa e verde; **falta a validação visual no navegador** (§7, itens de olho humano)
- **O que foi feito:**
  1. `node --test` em **todos** os `*.test.js` de `web/static/js` (inclui `conversationRows`, `messageView`, `mediaLimits`, `composerMirror`, `phone`, além dos dois novos) → **454/454 verde**.
  2. **Item 5 do plano implementado** (era condicional): `fmt()` em [ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js) neutraliza as âncoras com `pointer-events:none` quando `actions.selectionMode` está ligado. Sem isso, clicar numa âncora em modo de seleção marcaria a mensagem **e** abriria uma aba — a navegação é o default do `<a>` e o `onClick` do container não a cancela. O problema já existia para URL antes deste plano; com e-mail/telefone linkificados ficaria fácil de esbarrar. Feito **dentro do escopo** (o `fmt` do container), sem tocar em `MessageBubble.js`.
  3. Grafo de módulos conferido **servido de verdade** pelo dev server deste checkout (porta 8090): `messageEntities.js`, `formatWhatsApp.js`, `phone.js`, `notify.js`, `ContactDetail.js` e `MessageContextMenu.js` todos **HTTP 200** — os caminhos relativos novos resolvem como URL no navegador, que é onde um import errado apareceria.
  4. Item 6 conferido por `grep`: só `ContactDetail.js` importa `formatWhatsApp`; sidebar/preview/busca continuam com texto cru (os demais importam `highlightComposerMarkup`/`toWhatsAppMarkup`, do compositor, intocados).
- **Como foi feito / decisões:** o `pointer-events:none` é injetado no **começo** do `style` da âncora por regex sobre a saída já pronta — não depende da ordem dos atributos e não cria API nova em `formatWhatsApp`/`messageEntities` para um estado que é do container. Verificado por execução: as 3 âncoras de uma mensagem com link+e-mail+telefone são neutralizadas.
- **Problemas / pendências:**
  - **pytest NÃO foi executado** — instrução explícita do operador nesta rodada (a suíte Postgres está em uso por outras IAs trabalhando nos planos 91/93/94/95 no mesmo checkout). A garantia de D2 foi dada por outro caminho: `git status` confirma que **nenhum** arquivo `server/`, `db/`, `agent/`, `channels/` ou `app/` foi tocado por este plano (as mudanças em `agent/memory.py`, `app/services/agent_run_service.py` e `assets/plugin_examples/whatsapp_cloud/*` são dos outros planos em andamento, não deste). Rodar `venv/bin/python -m pytest tests/test_endpoints.py -q` fica pendente para quando a suíte estiver livre — expectativa: inalterada.
  - **Validação visual pendente** (precisa de olho humano no painel, claro e escuro): botão direito sobre link/e-mail/telefone, menu fora de entidade, "Copiar seleção", legenda de imagem/documento, nota privada, transcrição, card de CTA, sandbox, grupo com menção+link+e-mail, modo de seleção em lote e o item *Gerar melhoria* no fim. Itens marcados no §7 abaixo.
- **Verificação:** `node --test` (454/454) · `node --input-type=module --check` nos 3 arquivos alterados · HTTP 200 nos 6 módulos servidos · regex de `selectionMode` verificada por execução (3 de 3 âncoras neutralizadas).

---

## 5. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| `formatWhatsApp` renderiza **toda** bolha, legenda e card do sistema | Um regex errado desfigura o painel inteiro | **F0 primeiro** (caracterização antes de tocar) — é a única fase 🔴 antes do trabalho |
| Escape → formatação (invariante D5) | Montar `href` de texto não-escapado = XSS armazenado | `linkifyEntities` **nunca** escapa/desescapa; `href` só de match `http(s)` / e-mail / dígitos. Testar `<img src=x onerror=…>` e `javascript:alert(1)` no `messageEntities.test.js` |
| Âncora reprocessada por regra posterior | O `@` de `mailto:` dentro do atributo vira `@menção` e corrompe o HTML | Token/placeholder na F1 (item 4), com teste dedicado: `contato@empresa.com` num **grupo com membros** |
| Linkificação de telefone | Protocolo/valor/CPF viram link e o operador liga para um número que não existe | **D4**: só `+…` ou máscara BR completa. Negativos explícitos no teste (`PROT-`, `R$`, data, CPF) |
| Regra de JID hoje é `[\w.]+` | Ao apertar o sufixo, um JID exótico (canal/comunidade) deixa de ser pintado | Lista fechada cobre `s.whatsapp.net`, `lid`, `g.us`, `c.us`, `broadcast`, `newsletter` (os tipos de [channels/jid.py](../channels/jid.py)). Degradação é **cosmética**, não funcional |
| `key={item.label}` no menu | Separador sem label quebra a lista | F4 item 2 troca por key por índice |
| Modo escuro | Item/separador novo ilegível | Ícones com `fill="currentColor"`; separador com `border-wa-border`. **Nenhuma cor crua** (regra do `CLAUDE.md`) |
| Modo seleção em lote | Âncora rouba o clique de marcar mensagem | F5 item 5 |
| iOS/Safari | `contextmenu` não dispara no long-press | **Aceito** (§2.4): o callout nativo do iOS já oferece "Copiar link" numa `<a>`. Ver **P2** |
| Seam de plugin | Um plugin que **substitui** o array (em vez de acrescentar) apagaria os itens novos | Comportamento já existente e documentado ([registry.js:44-49](../web/static/js/plugins/registry.js#L44-L49)); o `melhorias` só faz `push`. Sem mudança de contrato (D6) |

---

## 6. Perguntas em aberto

**P1 — Como chamar o item, dado que "Copiar link da mensagem" já existe?**
✅ **RESOLVIDA na F1/F3: opção (a).** O item da entidade é *"Copiar endereço do link"*; o permalink interno segue com o nome de sempre. Nenhum rótulo conhecido pelo operador mudou. Hoje [ContactDetail.js:418](../web/static/js/components/contacts/ContactDetail.js#L418) chama de "Copiar link da mensagem" o **permalink interno**. Um "Copiar link" novo ao lado confunde.
(a) Entidade = *"Copiar endereço do link"*, permalink fica como está — **zero risco, é o vocabulário do navegador**.
(b) Entidade = *"Copiar link"*, permalink vira *"Copiar link do WhatsBot"* — mais claro, mas renomeia um item que o operador já conhece.
**Recomendação: (a).**

**P2 — Long-press no celular (o "segurar o dedo" do relato)?**
⏸️ **ADIADO.** Android/Chrome já ganha tudo via `contextmenu`. iOS/Safari não dispara `contextmenu`, mas mostra o callout nativo — que **já faz** o que o usuário pediu para links. Um handler `touchstart`+timer daria paridade no iOS ao custo de brigar com o callout e com o scroll. **Medir o uso real em iOS antes de investir.**

**P3 — Consertar negrito/itálico corrompendo URLs?**
⏸️ **ADIADO, fora deste plano** (§2.4). Exige tokenizar o pipeline inteiro de `formatWhatsApp`. Vira plano próprio se aparecer relato real.

**P4 — Estender para o painel do contato (atributos `email`/`link`)?**
⏸️ **ADIADO.** [CustomAttributeField.js:48](../web/static/js/components/contacts/CustomAttributeField.js#L48) já tem o tipo `link` (input `url`), e Email virou atributo customizado ([ContactInfoPanel.js:31](../web/static/js/components/contacts/ContactInfoPanel.js#L31)). Depois da F5, `entityActions` estaria pronto para reuso ali com custo baixo.

**P5 — "Conversar no WhatsApp" deveria abrir a conversa DENTRO do WhatsBot em vez do `wa.me`?**
✅ **RESOLVIDA na F1: `wa.me` nesta rodada** (`https://wa.me/<dígitos>`, com `55` inferido na máscara BR). Abrir internamente fica para o plano seguinte. Abrir internamente exigiria resolver contato/canal (existe fluxo em [NewConversationModal.js](../web/static/js/components/contacts/NewConversationModal.js) e o `checkingPhone` de [ContactList.js:208](../web/static/js/components/contacts/ContactList.js#L208)) — **mais útil para o operador**, porém mais caro e com decisão de canal envolvida.
**Recomendação: `wa.me` nesta rodada** (uma linha, zero acoplamento); interno vira P4/plano seguinte.

---

## 7. Checklist de verificação

- [x] `node --test web/static/js/utils/formatWhatsApp.test.js` verde (F0 **antes** de qualquer edição: 15/15; depois da F2: 22/22)
- [x] `node --test web/static/js/services/messageEntities.test.js` verde (29/29), **incluindo os negativos** (`PROT-…`, `R$ …`, data, CPF, número cru sem `+`, id de pedido)
- [x] Testes de XSS no módulo puro: `javascript:`, `"` em URL, `<img onerror>` — nenhum vira `href` nem escapa da âncora
- [ ] ⏸️ `venv/bin/python -m pytest tests/test_endpoints.py -q` — **não rodado nesta sessão por instrução do operador** (suíte em uso pelas outras IAs no mesmo checkout). D2 garantido por `git status`: este plano não tocou nenhum arquivo de `server/`, `db/`, `agent/`, `channels/` ou `app/`
- [ ] 👁️ Botão direito **sobre link** → *Abrir link* + *Copiar endereço do link*; a cola traz **só a URL**
- [ ] 👁️ Botão direito **sobre e-mail** → *Enviar e-mail* + *Copiar e-mail*
- [ ] 👁️ Botão direito **sobre telefone** → *Copiar número* + *Ligar* + *Conversar no WhatsApp*
- [ ] 👁️ Botão direito **fora de entidade e sem seleção** → menu **byte-idêntico** ao de hoje (por construção: sem entidade e sem seleção o array não ganha item nem separador)
- [ ] 👁️ Com texto selecionado → *Copiar seleção* cola exatamente a seleção
- [x] Clique esquerdo num link **continua** abrindo em nova aba (`target="_blank" rel="noopener noreferrer"` travado por teste na F0 **e** na F2)
- [ ] 👁️ Modo escuro: itens, ícones e separador legíveis (por construção: `fill="currentColor"` + `border-wa-border`, sem cor crua)
- [ ] 👁️ Legenda de imagem/documento, nota privada, transcrição, card de CTA e sandbox renderizam corretamente
- [x] Grupo: `@menção` + link + e-mail na mesma mensagem, sem uma regra comer a outra (teste dedicado na F2: membro "Empresa" × `mailto:contato@empresa.com`)
- [x] Modo de seleção em lote: clicar numa âncora **marca a mensagem**, não navega (`pointer-events:none` nas âncoras em `selectionMode` — F5 item 2) · 👁️ confirmar no navegador
- [x] Sidebar/preview/busca sem âncoras (`grep`: só `ContactDetail.js` importa `formatWhatsApp`)
- [ ] 👁️ Item *Gerar melhoria* do plugin `melhorias` continua no fim do menu (seam intacto — a base só ganhou prefixo, D6)
- [ ] 👁️ Reload + voltar/avançar do navegador sem erro no console (os 6 módulos do grafo já respondem HTTP 200 no dev server)

> 👁️ = precisa de olho humano no painel; o resto está travado por teste automatizado.

---

## 8. Apêndice — arquivos-chave

**Novos (frontend, puros):**
- `web/static/js/services/messageEntities.js`
- `web/static/js/services/messageEntities.test.js`
- `web/static/js/utils/formatWhatsApp.test.js`

**Alterados (frontend):**
- [web/static/js/utils/formatWhatsApp.js](../web/static/js/utils/formatWhatsApp.js) — [:40-46](../web/static/js/utils/formatWhatsApp.js#L40-L46)
- [web/static/js/components/contacts/ContactDetail.js](../web/static/js/components/contacts/ContactDetail.js) — [:402-432](../web/static/js/components/contacts/ContactDetail.js#L402-L432), [:438-451](../web/static/js/components/contacts/ContactDetail.js#L438-L451)
- [web/static/js/components/contacts/MessageContextMenu.js](../web/static/js/components/contacts/MessageContextMenu.js) — [:88-103](../web/static/js/components/contacts/MessageContextMenu.js#L88-L103), [:111-139](../web/static/js/components/contacts/MessageContextMenu.js#L111-L139)

**Somente leitura / verificação (não alterar):**
- [web/static/js/components/contacts/MessageBubble.js](../web/static/js/components/contacts/MessageBubble.js) — [:59,66,72-80](../web/static/js/components/contacts/MessageBubble.js#L59)
- [web/static/js/components/contacts/MediaContent.js](../web/static/js/components/contacts/MediaContent.js) — [:60,68,79,137,142](../web/static/js/components/contacts/MediaContent.js#L60)
- [web/static/js/components/contacts/SystemMessageCard.js](../web/static/js/components/contacts/SystemMessageCard.js) — [:56,78,102,120,144,160,185,212](../web/static/js/components/contacts/SystemMessageCard.js#L56)
- [web/static/js/components/contacts/hooks/useMessageActions.js](../web/static/js/components/contacts/hooks/useMessageActions.js) — [:96-119](../web/static/js/components/contacts/hooks/useMessageActions.js#L96-L119)
- [web/static/js/utils/phone.js](../web/static/js/utils/phone.js) — [:37-58](../web/static/js/utils/phone.js#L37-L58)
- [web/static/js/services/notify.js](../web/static/js/services/notify.js)
- [web/static/js/plugins/registry.js](../web/static/js/plugins/registry.js) — [:42-49](../web/static/js/plugins/registry.js#L42-L49)
- [assets/plugin_examples/melhorias/static/extends.js](../assets/plugin_examples/melhorias/static/extends.js) — [:40](../assets/plugin_examples/melhorias/static/extends.js#L40)
- [server/app.py](../server/app.py) — [:645-660](../server/app.py#L645-L660) (CSP — confirmado que **não** precisa mudar)

**Backend / DB:** nenhum arquivo tocado (D2).
