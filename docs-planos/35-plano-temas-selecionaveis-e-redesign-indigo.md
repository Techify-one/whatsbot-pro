# Plano 35 — Temas selecionáveis + redesign visual índigo (estilo Chatwoot) como temas novos

> **Status:** PLANEJAMENTO · **Data:** 2026-07-07 · **Escopo:** grande (frontend-only; infra de tokens + 5 temas + seletor + fonte + reskin do inbox; sem backend, sem DB, sem migration).
> **Origem:** Pedido do usuário (Ezequiel) — "desenvolver temas para o WhatsBot, o usuário escolhe o tema que desejar" + prompt de redesign estilo Chatwoot (índigo/violeta, Manrope, dark/light/mid) que ele prototipou e gostou.
> **Método:** leitura + grep dos arquivos reais do frontend (`arquivo:linha` abaixo verificado nesta sessão). Nenhum backend tocado.
> **O quê/por quê:** o app já tem um sistema de design tokens (`--wa-*` CSS vars → paleta Tailwind `wa-*`), mas o tema é **binário** (`.dark` on/off). Este plano (1) generaliza o controlador de tema de binário para **N temas selecionáveis** via `data-theme`, mantendo os dois visuais WhatsApp atuais (verde claro/escuro) intactos, e (2) adiciona **3 temas novos** com o look índigo/Chatwoot (indigo-dark, indigo-light, midnight). A tese central: **a mesma marcação gera todos os visuais só trocando `data-theme`** — as diferenças de cor E de forma (radius/padding/sombra/painéis flutuantes) viram tokens; quase nenhum `if tema == X` no JS.
>
> **Como usar este plano:** ao executar cada fase, preencha o bloco **"Status de execução"** dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro. **Legibilidade em TODOS os 5 temas a cada fase** (não só no que você está editando). **Um refactor por commit.** Sem build step (Preact+HTM+Tailwind runtime). A Fase 0 (infra) **bloqueia** todo o resto; depois disso, as colunas (chat/lista) são paralelas.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** ✅ (2026-07-07) | Escopo = **"redesign como tema novo"**: manter WhatsApp verde (claro+escuro) como temas selecionáveis E **adicionar** o look índigo (dark/light/mid) como temas novos. NÃO substituir o verde. | 5 temas coexistem. Os 2 temas WhatsApp devem sair **pixel-idênticos** ao de hoje (tokens novos recebem valores neutros neles). |
| **D2** ✅ (2026-07-07) | Adotar **Manrope** (vendorizada/self-host, sem CDN em runtime), fallback `system-ui, -apple-system, sans-serif`. | Fonte aplicada **globalmente** (todos os temas), via `body`/`:root`. Baixar os `.woff2` para `web/static/vendor/fonts/` + `@font-face` em `custom.css`. |
| **D3** ✅ (2026-07-07) | **NÃO** criar namespace de token paralelo (`--app/--panel/--accent` do protótipo). Dobrar no namespace existente **`--wa-*`**. | Cada tema é um bloco `:root[data-theme="..."]` de vars `--wa-*`. Zero fork do design system; os 77 componentes que já usam `wa-*` herdam de graça. |
| **D4** ✅ (2026-07-07) | Migrar de classe `.dark` para atributo **`data-theme`** no `<html>`, MAS **manter `.dark` sincronizada** (setar/remover conforme o tema seja de família escura). | Preserva os fallbacks `html.dark .bg-white/...` de telas de plugin (custom.css:177-240) e o toastui-editor-dark, sem reescrevê-los. `data-theme` é a fonte da verdade; `.dark` é derivada. |
| **D5** ✅ (2026-07-07) | Default = **manter o atual** (WhatsApp). Instalações existentes não mudam de cara sozinhas. | Migração de `localStorage`: `'dark'→wa-dark`, `'light'/ausente→wa-light`. Nenhum usuário acorda no índigo sem escolher. |
| **Princípio fixo** | Mudança **puramente visual e aditiva**: zero regressão funcional (envio, IA on/off, responsável, canais, filtros, sandbox). Nenhum `name`/rota/evento muda. Seguir CLAUDE.md (regra de modo escuro / `wa-*` / `.wa-field`). | Todo passo é re-skin. Se algo exigir mudar lógica de dados, está fora deste plano. |

---

## 1. Resumo executivo

O WhatsBot pinta a UI a partir de CSS custom properties `--wa-*` (tripletos RGB) consumidas pela paleta Tailwind `wa-*`. Hoje há **2 temas** (claro/escuro) alternados por uma classe `.dark`. Queremos **5 temas selecionáveis** — os 2 atuais + 3 novos com um look índigo/Chatwoot (indigo-dark, indigo-light, midnight) — escolhidos num seletor no menu da engrenagem.

A solução tem três camadas:

1. **Infra de tema** (Fase 0, bloqueante): um controlador `theme.js` que lê/escreve `localStorage`, aplica `data-theme` no `<html>`, sincroniza `.dark` + `color-scheme`, e um registry de temas. O script pré-paint do `index.html` passa a aplicar `data-theme` antes do 1º paint. Novos tokens semânticos (accent index, bolha de IA, rótulos, e **tokens de forma**: radius/padding/sombra/divider) são adicionados a **todos** os blocos de tema — neutros nos temas WhatsApp, ativos nos índigo.
2. **Reskin do inbox** (Fases 2–3, paralelas): chat (header/bolhas/compositor) e lista de conversas passam a ler os tokens novos. A maior parte já é automática (os componentes usam `wa-*`); os pontos de trabalho são os **valores hardcoded em JS** (`senderColor`, `SYSTEM_CARD_VARIANTS`, tints verdes) e as **classes de forma** (tails, radius, painéis flutuantes) que viram tokens.
3. **Seletor + fonte + polish** (Fases 1 e 4): UI de escolha de tema no GearMenu; Manrope vendorizada; varredura de QA visual nos 5 temas.

**Descobertas que reduzem escopo** (verificadas): os rótulos **Manual/IA/remetente já existem** na bolha ([MessageBubble.js:44-47](../web/static/js/components/contacts/MessageBubble.js#L44-L47)); o compositor **já tem abas Responder/Mensagem Privada** ([Composer.js:161-169](../web/static/js/components/contacts/Composer.js#L161-L169)); os pills de data e "conversa iniciada" já existem ([ContactDetail.js:294](../web/static/js/components/contacts/ContactDetail.js#L294)). Ou seja, o redesign índigo é **majoritariamente re-tint + re-shape via token**, não reconstrução estrutural.

---

## 2. Como funciona hoje (mapa) — `arquivo:linha` verificado

### 2.1 O sistema de tokens (a fundação que reaproveitamos)
- Blocos de token: `:root` (claro) [custom.css:8-28](../web/static/css/custom.css#L8) e `html.dark` [custom.css:30-50](../web/static/css/custom.css#L30). 18 vars `--wa-*` cada, tripletos RGB.
- Paleta Tailwind `wa-*` que mapeia pra essas vars: config inline [index.html:15-44](../web/index.html#L15), `darkMode: 'class'`, forma `rgb(var(--wa-x) / <alpha-value>)` (opacity modifiers como `bg-wa-teal/10` funcionam).
- **77 componentes** já usam classes `wa-*` (medido: `grep -rl wa-panel|wa-text|wa-teal|wa-bg` → 77 arquivos). Trocar valores de var re-tematiza todos automaticamente.

### 2.2 O controlador de tema (o que vamos generalizar)
- ⚠️ Toggle **binário** vive no menu da engrenagem: `toggleDark()` [GearMenu.js:53-59](../web/static/js/components/shell/GearMenu.js#L53) faz `classList.toggle('dark')` + `localStorage.setItem('whatsbot_theme', 'dark'|'light')`. Item "Modo escuro" com switch [GearMenu.js:168](../web/static/js/components/shell/GearMenu.js#L168).
- Script **pré-paint** no `<head>`: [index.html:8](../web/index.html#L8) — `if(localStorage.getItem('whatsbot_theme')==='dark') document.documentElement.classList.add('dark')`. Evita flash.

### 2.3 O que depende da classe `.dark` (⚠️ não pode quebrar — D4)
- Fallbacks de telas de plugin cruas: `html.dark .bg-white / .text-gray-* / .border-gray-* / .bg-green-50 …` [custom.css:177-240](../web/static/css/custom.css#L177). Re-tematizam qualquer tela que use utilitário cru do Tailwind. **Devem continuar valendo nos 3 temas escuros novos** → o controlador seta `.dark` quando o tema é escuro.
- Toast UI editor dark: `.toastui-editor-dark` togglado por elemento seguindo o tema (index.html:46-49, MarkdownEditor.js). Também keyado no conceito "app está escuro".
- `color-scheme` [custom.css:9,31](../web/static/css/custom.css#L9) — controla widgets nativos (date/checkbox/scrollbar). Precisa acompanhar cada tema.

### 2.4 Os pontos de cor **fora** do sistema de token (o trabalho real do reskin)
- ⚠️ **Bolha**: bg via `bg-wa-incoming` / `bg-wa-outgoing` [MessageBubble.js:53-57](../web/static/js/components/contacts/MessageBubble.js#L53). NÃO há bolha de IA distinta (IA e operador usam a mesma `wa-outgoing`). Tails via `.msg-tail-in/out` [MessageBubble.js:55-56](../web/static/js/components/contacts/MessageBubble.js#L55) + [custom.css:74-101](../web/static/css/custom.css#L74) (border-color = `var(--wa-incoming/outgoing)`).
- ⚠️ Rótulo do remetente (Manual/IA/nome) **já existe** [MessageBubble.js:44-47](../web/static/js/components/contacts/MessageBubble.js#L44), mas a **cor** vem de `senderColor()` [MessageBubble.js:47,67](../web/static/js/components/contacts/MessageBubble.js#L47) — computada em JS.
- ⚠️ **Hardcoded hex em JS** (não segue tema): `senderColor` e `SYSTEM_CARD_VARIANTS` em [messageView.js:35-53](../web/static/js/services/messageView.js#L35) (`#2d1b4e`, `#d4bfff`, `#fbbf24`, `#78350f`…). Estilo inline `style="color: ${sColor}"` [MessageBubble.js:67](../web/static/js/components/contacts/MessageBubble.js#L67). Estes **não** mudam com o tema hoje.
- Fundo do chat: `.wa-chat-pattern` (doodle SVG WhatsApp) [custom.css:130-133](../web/static/css/custom.css#L130) aplicado em [ContactDetail.js:283](../web/static/js/components/contacts/ContactDetail.js#L283). Nos temas índigo o doodle deve sumir (fundo liso `--wa-chatBg`).
- Sombra da bolha: `.wa-bubble` box-shadow fixa [custom.css:136-140](../web/static/css/custom.css#L136).
- Header do chat: avatar/nome/ações em [ContactDetail.js:233-270](../web/static/js/components/contacts/ContactDetail.js#L233); ações Resolver/Atribuir/Transferir em [ConversationHeaderActions.js](../web/static/js/components/contacts/ConversationHeaderActions.js).
- Compositor: abas + input em [Composer.js:155-350](../web/static/js/components/contacts/Composer.js#L155) (já usa `wa-*` + `text-violet-400` pra modo privado :343).
- Item da lista: [ContactList.js](../web/static/js/components/contacts/ContactList.js) (badges IA/IA OFF, chips, `✓✓`, item ativo). Já em `wa-*` majoritariamente.

### 2.5 Fonte
- Nenhuma `@font-face` / `font-family` custom hoje — herda `system-ui` do Tailwind base. `web/static/vendor/` não tem pasta `fonts/`.

### 2.6 Falsos positivos descartados

| Hipótese | Veredito | Razão (verificada) |
|---|---|---|
| "Precisa criar os tokens `--app/--panel/--accent` do protótipo" | ❌ Descartado (D3) | O app já tem `--wa-*` cobrindo os mesmos papéis. Um 2º namespace forçaria reescrever 77 componentes e duplicaria o fallback de plugin. Mapeamos os tokens do protótipo → `--wa-*`. |
| "Rótulos Manual/IA e abas do compositor precisam ser construídos" | ❌ Descartado | Já existem (MessageBubble.js:44-47, Composer.js:161-169). É re-tint, não construção. |
| "Cada tema precisa de um componente/branch próprio no JS" | ❌ Majoritariamente | Cor + forma (radius/pad/shadow/tails) cabem em tokens. O único branch de JS previsto é ler `senderColor`/variants de CSS var em vez de hex (§7.2). |
| "Manter `.dark` é legado a remover" | ❌ Descartado (D4) | `.dark` sustenta os fallbacks de plugin e o toastui-dark. Mais barato sincronizá-la do que reescrever esses seletores. |
| "Precisa mexer no backend pra salvar preferência de tema" | ❌ Descartado | Tema é preferência **per-device** → `localStorage`, igual ao toggle de hoje. Sem endpoint, sem coluna. |
| "`is_from_me` decide IA vs Manual — precisa de dado novo" | ❌ Descartado | A distinção IA/operador já vem de `m.status === 'operator'` (MessageBubble.js:29,46). O tema só re-estiliza. |

---

## 3. Mudanças de infraestrutura (frontend)

Todas no frontend; nenhuma no backend/DB.

| Camada | Mudança | Arquivo |
|---|---|---|
| Tokens | Adicionar tokens novos a **todos** os blocos de tema + criar 3 blocos `[data-theme]` novos | `web/static/css/custom.css` |
| Tokens (forma) | Classes de componente que leem tokens de forma (`.inbox-shell`, `.inbox-panel`, `.chat-panel`, `.msg-bubble`, `.chat-surface`) | `web/static/css/custom.css` |
| Tailwind | Registrar os `wa-*` de cor novos (accentWeak, accent2, aiBubble, aiText, manualLabel, aiLabel, inLabel) na paleta | `web/index.html` (config inline) |
| Controlador | Novo módulo `theme.js` (registry + `applyTheme`/`getTheme`/`setTheme` + migração de localStorage) | `web/static/js/services/theme.js` (novo) |
| Pré-paint | Script inline lê `data-theme` salvo e aplica antes do 1º paint | `web/index.html:8` |
| Fonte | `@font-face` Manrope + `font-family` global | `web/static/css/custom.css` + `web/static/vendor/fonts/` (novo) |
| JS de cor | `senderColor`/`SYSTEM_CARD_VARIANTS` passam a ler CSS vars (via classes/`var()`), não hex | `web/static/js/services/messageView.js` |

---

## 4. Especificação dos 5 temas (registry)

`data-theme` no `<html>`. `dark:true` ⇒ controlador adiciona `.dark`. Ordem = ordem no seletor.

| id (`data-theme`) | Rótulo (UI) | Família | Base | Accent |
|---|---|---|---|---|
| `wa-light` | WhatsApp Claro | claro | atual `:root` | teal `#008069` |
| `wa-dark` | WhatsApp Escuro | escuro | atual `html.dark` | teal `#00a884` |
| `indigo-dark` | Índigo Escuro | escuro | protótipo `dark` | índigo `#6366f1` |
| `indigo-light` | Índigo Claro | claro | protótipo `light` | índigo `#4f46e5` |
| `midnight` | Midnight | escuro (flutuante) | protótipo `mid` | violeta `#7c6cf6` |

**Regra de neutralidade (D1):** os tokens **novos** de forma recebem, nos temas `wa-light`/`wa-dark`, valores que reproduzem o visual de hoje (colunas full-bleed, sem sombra flutuante): `--wa-appPad:0`, `--wa-colGap:0`, `--wa-panelRadius:0`, `--wa-ring:none`, `--wa-shadow:none`, `--wa-divider:1px solid rgb(var(--wa-border))`, `--wa-bubbleRadius:7.5px`, `--wa-chatPattern:1` (doodle ligado), tails ligados. Só os temas índigo mudam esses valores.

### 4.1 Tokens novos (adicionar a TODOS os blocos)

**Cores** (tripletos RGB; entram na paleta Tailwind `wa-*`):
`--wa-accent` (= accent do tema; nos WhatsApp aponta pro teal existente), `--wa-accentWeak`, `--wa-accent2`, `--wa-aiBubble`, `--wa-aiText`, `--wa-manualLabel`, `--wa-aiLabel`, `--wa-inLabel`, `--wa-avBg`, `--wa-avText`, `--wa-headerBg`.

**Forma/layout** (valores livres, lidos por classes de componente, NÃO pela paleta Tailwind):
`--wa-appPad`, `--wa-colGap`, `--wa-panelRadius`, `--wa-ring`, `--wa-shadow`, `--wa-divider`, `--wa-bubbleRadius`, `--wa-bubbleBorder`, `--wa-bubbleShadow`, `--wa-tailColorIn`, `--wa-tailColorOut` (transparent nos índigo = sem tail), `--wa-chatPattern` (opacity 0/1 do doodle).

### 4.2 Valores índigo (do protótipo do usuário — mapear pro `--wa-*`)

> Fonte: prompt do usuário. Hex → tripleto RGB na implementação. Papéis do protótipo → var `--wa-*`:
> `--app`→`--wa-bg` do shell · `--panel`→`--wa-panel` · `--chat`→`--wa-chatBg` · `--panel-2`→`--wa-inputBg`/`--wa-hover` · `--border`→`--wa-border` · `--text/2/3`→`--wa-text`/`--wa-secondary`/(novo `--wa-text3`) · `--accent`→`--wa-accent`/`--wa-teal`/`--wa-iconActive` · `--accent-2`→`--wa-accent2` · `--accent-weak`→`--wa-accentWeak` · `--in-bubble/text`→`--wa-incoming`/`--wa-text` · `--out-bubble/text`→`--wa-outgoing` · `--ai-bubble/text`→`--wa-aiBubble`/`--wa-aiText` · `--manual-label/--ai-label/--in-label`→`--wa-manualLabel`/`--wa-aiLabel`/`--wa-inLabel` · `--danger`→(tint red) · `--av-bg/text`→`--wa-avBg`/`--wa-avText` · forma → tokens §4.1.

**indigo-dark:** app `#0a0e16`, panel `#0f1521`, chat `#0a0f1a`, panel-2 `#161d2b`, border `#1e2636`, text `#e8ebf2`, text-2 `#9aa4b8`, accent `#6366f1`, accent-2 `#818cf8`, accent-weak `#1b2140`, in-bubble `#172031`, out-bubble `#222b3f`, ai-bubble `#1f2547`, manual-label `#f3a13b`, ai-label `#8b93f8`, in-label `#5fa8f5`, av-bg `#222b3d`. Forma: bubbleRadius 14px, panelRadius 0, appPad 0, divider on, ring/shadow none, tails **off**, doodle **off**.

**indigo-light:** app `#e9ebf0`, panel `#ffffff`, chat `#f6f7f9`, panel-2 `#eef1f6`, border `#e5e8ee`, text `#1b202b`, text-2 `#5b6478`, accent `#4f46e5`, accent-2 `#5b54e8`, accent-weak `#ecedfe`, in-bubble `#ffffff`, out-bubble `#e9edfb`, ai-bubble `#e8e7fe`, manual-label `#c9710a`, ai-label `#5d50dd`, in-label `#2563eb`, av-bg `#e7eaf1`. Forma: bubbleRadius 14px, bubbleBorder `#e7e9ef`, bubbleShadow `0 1px 2px rgba(20,24,40,.06)`, tails **off**, doodle **off**.

**midnight:** app `#06070d`, panel `#0e1120`, chat `#0a0c18`, panel-2 `#171b2e`, border `#232a47`, text `#edeefb`, text-2 `#a0a6c6`, accent `#7c6cf6`, accent-2 `#9d8cff`, accent-weak `#201f47`, in-bubble `#161a2e`, out-bubble `#252b4d`, ai-bubble `#2b2657`, manual-label `#fbbf24`, ai-label `#a78bfa`, in-label `#7dd3fc`, av-bg `#232844`. **Forma (flutuante):** bubbleRadius 18px, panelRadius 16px, appPad 14px, colGap 14px, divider **none**, ring `inset 0 0 0 1px rgb(var(--wa-border))`, shadow `0 18px 40px -12px rgba(0,0,0,.55)`, tails **off**, doodle **off**.

---

## 5. Fases / Roadmap

### 5.1 Diagrama de dependências (waves)

```
WAVE 0  F0 (infra: tokens + theme.js + pré-paint)        ← 🔴 SOZINHA, bloqueia tudo
           │  (barreira: nada renderiza tematizado sem F0)
           ▼
WAVE 1  F1 (seletor no GearMenu) · F1b (fonte Manrope)   ← 🟢 paralelas entre si
           │
           ▼
WAVE 2  F2 (coluna chat) · F3 (coluna lista)             ← 🟢 paralelas entre si [ambas dependem de F0]
           │  (barreira: colunas prontas)
           ▼
WAVE 3  F4 (polish + QA visual nos 5 temas)              ← 🔴 SOZINHA (integra tudo)
```

### 5.2 Tabela de fases

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | **F0** | Infra: tokens novos em todos os blocos + 3 blocos índigo + `theme.js` + pré-paint + Tailwind config | 🔴 SOZINHA · [bloqueia: F1,F2,F3,F4] | médio | Trocar `data-theme` via console re-tematiza; `wa-light`/`wa-dark` idênticos ao de hoje |
| 1 | **F1** | Seletor de tema no GearMenu (substitui toggle binário) | 🟢 [depende de: F0] | baixo | Escolher tema no menu aplica + persiste; reload sem flash |
| 1 | **F1b** | Fonte Manrope vendorizada + global | 🟢 [depende de: F0] | baixo | Painel renderiza em Manrope; offline (sem rede) mantém |
| 2 | **F2** | Reskin coluna chat: header + bolhas (bolha IA, tails/shape por token, senderColor via var) + compositor + fundo | 🟢 [depende de: F0] | médio | Nos 5 temas: bolhas/rótulos/fundo corretos; WhatsApp inalterado |
| 2 | **F3** | Reskin coluna lista: app bar (sem barra verde nos índigo), item ativo, badges, chips | 🟢 [depende de: F0] | médio | Nos 5 temas: lista legível; item ativo com barra accent |
| 3 | **F4** | Polish + varredura QA visual + acessibilidade (foco/contraste) nos 5 temas | 🔴 SOZINHA | baixo | Checklist §8 verde nos 5 temas |

---

### Fase 0 — Infra de tokens + controlador de tema (🔴 bloqueante)

**Objetivo:** transformar o tema binário em N temas dirigidos por `data-theme`, com todos os tokens (velhos + novos) definidos nos 5 blocos, sem alterar a aparência dos 2 temas WhatsApp.

**Itens:**
1. `[sequencial]` **Tokens de forma neutros + cores novas nos blocos atuais**: adicionar as vars §4.1 a `:root` [custom.css:8](../web/static/css/custom.css#L8) e `html.dark` [custom.css:30](../web/static/css/custom.css#L30) com valores neutros (§4 regra de neutralidade). `--wa-accent`/`--wa-accentWeak`/`--wa-accent2` apontam pro teal atual; `--wa-aiBubble`=`--wa-outgoing`, `--wa-headerBg`=`--wa-panel`, tails/doodle ligados.
2. `[sequencial]` **Migrar seletor**: `:root` → `:root[data-theme="wa-light"]`; `html.dark` → `:root[data-theme="wa-dark"]`. Manter um `:root` base sem tema (= wa-light) como fallback defensivo. **Manter também** `html.dark { ... }` como alias que só seta `color-scheme` (os fallbacks de plugin §2.3 continuam keyados em `.dark`).
3. `[sequencial]` **3 blocos novos**: `:root[data-theme="indigo-dark"]`, `[indigo-light]`, `[midnight]` com os valores §4.2 (converter hex→RGB triplet; incluir tokens de forma).
4. `[sequencial]` **Classes de forma** em custom.css que consomem os tokens: `.inbox-shell` (padding `--wa-appPad`, gap `--wa-colGap`), `.inbox-panel`/`.chat-panel` (radius `--wa-panelRadius`, box-shadow `--wa-ring,--wa-shadow`, `overflow:hidden`), `.msg-bubble` (radius `--wa-bubbleRadius`, border `--wa-bubbleBorder`, shadow `--wa-bubbleShadow`). Ajustar `.wa-chat-pattern` pra respeitar `--wa-chatPattern` (opacity do doodle) e `.msg-tail-in/out` a usar `--wa-tailColorIn/Out`.
5. `[sequencial]` **Tailwind config** [index.html:15-44](../web/index.html#L15): registrar os `wa-*` de cor novos (`accent`, `accentWeak`, `accent2`, `aiBubble`, `aiText`, `manualLabel`, `aiLabel`, `inLabel`, `avBg`, `avText`, `headerBg`).
6. `[sequencial]` **`theme.js`** (`web/static/js/services/theme.js`, novo): `THEMES` (registry §4, `{id,label,dark,swatch}`), `getTheme()` (lê localStorage `whatsbot_theme`, com migração `dark→wa-dark`/`light→wa-light`), `setTheme(id)` (grava + `applyTheme`), `applyTheme(id)` (`documentElement.setAttribute('data-theme', id)` + `classList.toggle('dark', THEMES[id].dark)`). Mesma chave `whatsbot_theme` reusada.
7. `[sequencial]` **Pré-paint** [index.html:8](../web/index.html#L8): trocar por script inline que lê `whatsbot_theme` (com migração), seta `data-theme` E `.dark` antes do paint. **Inline puro** (não pode importar módulo — roda antes do bundle).

**Pronto quando:** com o app aberto, `document.documentElement.setAttribute('data-theme','indigo-dark')` no console re-tematiza o app inteiro; setar `wa-light`/`wa-dark` deixa **pixel-idêntico** ao claro/escuro de hoje; recarregar com um tema salvo não pisca.

#### Status de execução — Fase 0
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher ao executar)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase 1 — Seletor de tema no GearMenu (🟢)

**Objetivo:** trocar o switch binário "Modo escuro" por um seletor dos 5 temas com amostra de cor.

**Itens:**
1. Substituir `toggleDark`/estado `dark` [GearMenu.js:53-59](../web/static/js/components/shell/GearMenu.js#L53) por leitura de `getTheme()` e `setTheme()` de `theme.js`.
2. Substituir o item "Modo escuro" [GearMenu.js:168](../web/static/js/components/shell/GearMenu.js#L168) por um sub-menu/lista "Tema" com uma linha por tema (rótulo + swatch de 2–3 bolinhas do accent/panel + check no ativo). Alvos ≥36px, foco visível.
3. Aplicar tema **na hora** (sem reload) via `setTheme` — o `data-theme` re-tematiza tudo.

**Pronto quando:** abrir engrenagem → "Tema" → escolher "Índigo Escuro" muda o app imediatamente; recarregar mantém; sem flash.

#### Status de execução — Fase 1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase 1b — Fonte Manrope (🟢)

**Objetivo:** adotar Manrope globalmente, vendorizada (sem CDN em runtime).

**Itens:**
1. Baixar `Manrope` (pesos 400/500/600/700/800, `.woff2`) para `web/static/vendor/fonts/`.
2. `@font-face` (5 pesos, `font-display: swap`) em custom.css.
3. `font-family: 'Manrope', system-ui, -apple-system, sans-serif` no `body`/`:root` (e/ou `fontFamily` na config Tailwind, com fallback). Confirmar que não quebra os `text-[Npx]` existentes (só troca a família).

**Pronto quando:** DevTools → Network offline → reload; o painel renderiza em Manrope (não system default); nenhum request externo de fonte.

#### Status de execução — Fase 1b
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase 2 — Reskin da coluna do chat (🟢, depende de F0)

**Objetivo:** header + bolhas + compositor + fundo lêem os tokens novos; bolha de IA distinta; tails/shape por token; cores JS viram var.

**Itens:**
1. **`senderColor`/`SYSTEM_CARD_VARIANTS`** [messageView.js:35-53](../web/static/js/services/messageView.js#L35): trocar hex por CSS vars — retornar `var(--wa-inLabel)`/`var(--wa-manualLabel)`/`var(--wa-aiLabel)` (ou expor classes `.label-in/.label-manual/.label-ai`). Assim o rótulo muda de cor por tema. Ajustar o `style="color:${sColor}"` [MessageBubble.js:67](../web/static/js/components/contacts/MessageBubble.js#L67) conforme.
2. **Bolha de IA distinta** [MessageBubble.js:53-57](../web/static/js/components/contacts/MessageBubble.js#L53): quando `!isOperator && !isUser` (resposta IA) usar `bg-wa-aiBubble text-wa-aiText`; operador segue `wa-outgoing`; recebida `wa-incoming`. Adicionar classe `.msg-bubble` (radius/border/shadow por token) e trocar `rounded-[7.5px]` fixo por `--wa-bubbleRadius`.
3. **Tails por token**: `.msg-tail-in/out` já viram transparentes nos índigo via `--wa-tailColorIn/Out` (F0 item 4). Confirmar que `rounded-tl-none`/`tr-none` não deixam canto feio quando sem tail (usar `--wa-bubbleRadius` uniforme quando tail off) — pode exigir uma classe condicional simples, **não** um branch de tema.
4. **Fundo do chat** [ContactDetail.js:283](../web/static/js/components/contacts/ContactDetail.js#L283): `.wa-chat-pattern` respeita `--wa-chatPattern` (doodle some nos índigo, fundo `--wa-chatBg`).
5. **Header** [ContactDetail.js:233-270](../web/static/js/components/contacts/ContactDetail.js#L233): usar `--wa-headerBg`; avatar em `wa-accentWeak/wa-accent2`. **Ações** [ConversationHeaderActions.js](../web/static/js/components/contacts/ConversationHeaderActions.js): botão Resolver primário em `wa-accent` texto branco; Atribuir/Transferir fantasma (`border-wa-border text-wa-secondary`). Verificar que já não usam verde cru.
6. **Compositor** [Composer.js:155-350](../web/static/js/components/contacts/Composer.js#L155): abas Responder/Privada com `border-b-2 border-wa-accent`; `text-violet-400` [Composer.js:343](../web/static/js/components/contacts/Composer.js#L343) do modo privado → token (`--wa-aiLabel` ou manter violeta, decidir em P1). Input já `wa-*`.
7. **Pills** de data/sistema [ContactDetail.js:294](../web/static/js/components/contacts/ContactDetail.js#L294): `bg-wa-inputBg/panel-2 text-wa-secondary` (já quase lá).

**Pronto quando:** nos 5 temas — recebida à esquerda, operador/IA à direita com rótulo colorido certo; nos índigo sem tail/doodle e com radius maior; em `wa-light`/`wa-dark` **idêntico ao atual** (tail, doodle, verde). Envio/retry/reação/quote continuam funcionando.

#### Status de execução — Fase 2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase 3 — Reskin da coluna da lista (🟢, depende de F0)

**Objetivo:** app bar, item ativo, badges e chips lêem os tokens; "barra verde forte" some nos índigo.

**Itens:**
1. **App bar / header da lista** [ContactList.js](../web/static/js/components/contacts/ContactList.js): trocar acentos verdes crus por `wa-accent`; nos índigo o header usa `--wa-headerBg` sem faixa verde. Verificar `bg-white/10`/`bg-white/15` [ContactList.js:177,348](../web/static/js/components/contacts/ContactList.js#L177) (hover de ícones sobre header) — se o header for claro nos índigo-light, `bg-white/10` some; trocar por `bg-wa-hover` ou `bg-black/5` conforme legibilidade.
2. **Item ativo**: overlay `bg-wa-accentWeak` + barra esquerda `3px` `wa-accent` (hoje usa `wa-selected`). Aplicar via classe/token.
3. **Badges IA / IA OFF**: IA em `wa-accentWeak/wa-accent2`; IA OFF em tint danger. Confirmar contraste nos 5.
4. **Chips de label e canal, `✓✓`**: `✓✓` em `wa-accent2` (era teal). Chips já usam cor da tag (dado), manter.
5. **Painéis flutuantes (midnight)**: aplicar `.inbox-panel`/`.chat-panel` (radius/shadow por token) no container [Contacts.js](../web/static/js/components/contacts/Contacts.js) e `.inbox-shell` (padding/gap) no wrapper. Nos temas não-midnight os tokens são 0/none → full-bleed como hoje.

**Pronto quando:** nos 5 temas a lista fica legível; item ativo destacado com accent; no midnight as 2 colunas viram cartões flutuantes; em WhatsApp inalterado.

#### Status de execução — Fase 3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

### Fase 4 — Polish + QA visual nos 5 temas (🔴)

**Objetivo:** varrer telas além do inbox (Config, IA, Canais, Plugins, Wizard, Login, modais, Attendances) nos 5 temas e corrigir cores cruas fora de token que ficaram ilegíveis.

**Itens:**
1. Para cada tema, abrir as telas principais + modais e conferir contraste/legibilidade (regra CLAUDE.md). Onde uma cor crua não coberta pelos fallbacks `html.dark` (§2.3) quebrar num tema índigo, trocar por `wa-*` ou estender o fallback.
2. **⚠️ Fallbacks de plugin**: os `html.dark .bg-green-50 …` (custom.css:210-240) usam **hex fixo** de acento — valem igual nos 3 escuros (ok), mas confira que ficam aceitáveis sobre os painéis índigo (não precisam casar com o accent, só ser legíveis).
3. Foco visível (`focus-visible` ring em `wa-accent`) e alvos ≥36px no seletor e ações.
4. `node --test` nos módulos puros que existirem tocado (ex: se `theme.js` ganhar teste puro de migração de localStorage — opcional mas recomendado).

**Pronto quando:** checklist §8 verde nos 5 temas; nenhuma tela com texto ilegível; sem regressão funcional.

#### Status de execução — Fase 4
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(preencher)_
- **Como foi feito / decisões:** _(preencher)_
- **Problemas / pendências:** _(preencher)_
- **Verificação:** _(preencher)_

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Sincronizar `.dark` com `data-theme` | Se esquecer, telas de plugin cruas e o toastui-dark ficam claros num tema escuro índigo | `applyTheme` **sempre** faz `classList.toggle('dark', THEMES[id].dark)`; pré-paint idem. Teste: abrir tela de plugin em cada tema escuro. |
| Regressão nos temas WhatsApp | Tokens de forma novos mudarem sutil o visual atual | Regra de neutralidade (D1/§4): valores 0/none/tail-on nos `wa-*`; critério "pixel-idêntico" na F0/F2/F3. Comparar antes/depois lado a lado. |
| Cores hardcoded em JS | `senderColor`/`SYSTEM_CARD_VARIANTS` não seguem tema → rótulo errado no índigo | F2 item 1: mover pra CSS var/classe. |
| Flash de tema errado no reload | Pré-paint não cobrir os temas novos | Script inline lê `data-theme` real (não só 'dark') e aplica antes do bundle (F0 item 7). |
| Migração de localStorage | Usuário no 'dark' antigo cair num tema inexistente | `getTheme()` mapeia `dark→wa-dark`, `light`/desconhecido/ausente→`wa-light` (D5). |
| Fonte Manrope pesar/bloquear | woff2 grande atrasa 1º paint | `font-display: swap` + só 5 pesos; fallback `system-ui` renderiza na hora. |
| Opacity modifiers | Tokens de cor precisam ser **triplet RGB** pra `bg-wa-x/10` funcionar | Todos os `--wa-*` de cor novos entram como `R G B` (sem `rgb()`), igual aos existentes. Tokens de forma são valores livres (não passam pela paleta). |
| Tail sem cauda deixando canto | Bolha índigo com `rounded-tr-none` e sem tail = canto reto feio | F2 item 3: quando tail off, radius uniforme via `--wa-bubbleRadius`. |
| CSP / vendor | Fonte externa violaria a política self-only | Vendorizar em `web/static/vendor/fonts/` (mesma prática das libs JS). |

---

## 7. Perguntas em aberto

- **P1** — Cor do modo "Mensagem Privada" no compositor ([Composer.js:343](../web/static/js/components/contacts/Composer.js#L343), hoje `text-violet-400`). (a) manter violeta fixo em todos os temas (consistência de significado "privado"); (b) tokenizar pra `--wa-aiLabel`. **Recomendação:** (a) — privado é um estado semântico, não decorativo; manter violeta fixo. ⏸️ ADIADO pra F2.
- **P2** — Ícones: o protótipo pede SVGs de traço (não emoji). O app já usa SVG inline em quase tudo; há emojis pontuais? (a) auditar e trocar; (b) deixar como está (fora do escopo de "tema"). **Recomendação:** (b) — troca de ícone é cosmético ortogonal a tema; só trocar se algum emoji estiver claramente feio num tema. ⏸️ ADIADO pra F4.
- **P3** — Persistência por-conta vs per-device. Hoje tema é per-device (localStorage). Faz sentido salvar por usuário no backend? **Recomendação:** manter per-device (D — sem backend). Reabrir só se o usuário pedir sync entre dispositivos. ⏸️ ADIADO (provável nunca).
- **P4** — Preview ao vivo no seletor (hover mostra o tema antes de confirmar). **Recomendação:** não no MVP; aplicar no clique é suficiente. ⏸️ ADIADO.

---

## 8. Checklist de verificação

- [ ] `wa-light` e `wa-dark` **pixel-idênticos** ao claro/escuro de hoje (comparação antes/depois: inbox, header, bolhas, lista, compositor).
- [ ] Trocar tema no GearMenu aplica na hora e persiste; **reload sem flash** nos 5 temas.
- [ ] `localStorage` antigo (`'dark'`/`'light'`/ausente) migra pro tema certo sem erro.
- [ ] Cada tema **escuro** (wa-dark, indigo-dark, midnight) mantém `.dark` → tela de plugin crua e toastui-editor legíveis.
- [ ] Nos 3 índigo: sem barra verde, sem doodle, tails off, radius maior; midnight com painéis flutuantes.
- [ ] Rótulos Manual (laranja) / IA (violeta) / remetente (azul) com a cor certa **por tema**.
- [ ] Fonte Manrope carrega **offline** (sem request externo); fallback `system-ui` intacto.
- [ ] Sem regressão funcional: envio, retry, reação, quote/jump, IA on/off, atribuição, filtros, sandbox, canais.
- [ ] `tests/test_endpoints.py` verde no Postgres (`WHATSBOT_TEST_DB_URL`) — deve passar inalterado (mudança é só frontend).
- [ ] `node --test` verde nos módulos puros existentes (routing.test.js, constants.test.js) + eventual `theme.test.js`.
- [ ] Foco visível (ring `wa-accent`) e alvos ≥36px no seletor.
- [ ] QA visual das telas fora do inbox (Config, IA, Canais, Plugins, Wizard, Login, Attendances, modais) nos 5 temas — nenhuma ilegível.

---

## 9. Apêndice — arquivos-chave

**Infra (F0):**
- `web/static/css/custom.css` — blocos de token (5), classes de forma, `@font-face`, ajuste de `.wa-chat-pattern`/`.msg-tail-*`.
- `web/index.html` — Tailwind config inline (cores `wa-*` novas + fontFamily) + script pré-paint.
- `web/static/js/services/theme.js` *(novo)* — registry + `getTheme/setTheme/applyTheme` + migração.

**Seletor + fonte (F1/F1b):**
- `web/static/js/components/shell/GearMenu.js` — seletor de tema.
- `web/static/vendor/fonts/` *(novo)* — Manrope `.woff2`.

**Reskin chat (F2):**
- `web/static/js/components/contacts/MessageBubble.js` — bolha IA, shape por token, senderColor.
- `web/static/js/services/messageView.js` — `senderColor`/`SYSTEM_CARD_VARIANTS` → CSS var.
- `web/static/js/components/contacts/ContactDetail.js` — header, fundo, pills.
- `web/static/js/components/contacts/ConversationHeaderActions.js` — Resolver/Atribuir/Transferir.
- `web/static/js/components/contacts/Composer.js` — abas, modo privado.
- `web/static/js/components/contacts/SystemMessageCard.js` — cards centrais (via variants tokenizados).

**Reskin lista (F3):**
- `web/static/js/components/contacts/ContactList.js` — app bar, item ativo, badges, chips.
- `web/static/js/components/contacts/Contacts.js` — wrapper `.inbox-shell`/painéis flutuantes.

**Polish (F4):**
- Varredura ampla; correções pontuais em telas que usam cor crua fora de token.
