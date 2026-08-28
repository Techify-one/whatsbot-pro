# Frontend — tema, modo escuro e legibilidade

> Guia das regras de cor/contraste **e de largura/transbordo** do painel e das telas de plugin. O [`CLAUDE.md`](../CLAUDE.md) carrega a **regra curta** e os avisos ⚠️;
> aqui está o **porquê**, o histórico e o detalhe. Texto migrado do `CLAUDE.md` no plano 139
> — nada foi reescrito na migração, só realocado.

---

## Tema e modo escuro (legibilidade)

O painel suporta **modo claro e escuro**. O tema é a classe `.dark` no `<html>` (toggle no menu da engrenagem → "Modo escuro", persistido em `localStorage["whatsbot_theme"]`; um script inline no `<head>` do `web/index.html` aplica antes do 1º paint pra não piscar). As cores são dirigidas por **variáveis CSS (canais RGB)** em [web/static/css/custom.css](../web/static/css/custom.css): a paleta `wa-*` do Tailwind (`bg-wa-panel`, `text-wa-text`, `border-wa-border`, …) resolve para `rgb(var(--wa-*) / <alpha-value>)` (config em `web/index.html`), então alternar a classe re-tematiza o app inteiro e os modificadores de opacidade (`bg-wa-teal/10`) continuam funcionando.

**REGRA — ao adicionar QUALQUER área nova (tela core, card, modal, tela de plugin), garanta que as cores sejam legíveis no modo escuro.** Na prática:

- **Prefira as classes semânticas `wa-*`** para superfícies/textos/bordas (`bg-wa-bg`, `bg-wa-panel`, `text-wa-text`, `text-wa-secondary`, `border-wa-border`, `bg-wa-hover`, `bg-wa-teal`). Elas trocam de cor sozinhas nos dois temas — é o caminho recomendado e à prova de futuro.
- **Não dependa de cores cruas do Tailwind** (`bg-white`, `text-gray-*`, `bg-green-50`…) nem do fundo padrão do navegador em inputs. Como rede de segurança, `custom.css` tem overrides `html.dark` que re-tematizam as cruas mais comuns (brancos, cinzas `50–300`, e as tintas de acento green/red/amber/yellow/blue/orange/purple/pink em `-50/100/200` + textos `-600/700/800`). Isso é **fallback**, não substitui usar `wa-*` — cores fora dessa lista (ex.: um hex inline, um `bg-*-300` de fundo, uma cor nova) NÃO são cobertas e ficarão ilegíveis.
- **Campos de formulário**: use a classe `.wa-field` (fundo cinza + texto preto, legível nos dois temas) em `<input>`/`<textarea>`/`<select>`. Deixar sem cor de fundo cai no branco padrão do navegador + texto claro do tema = ilegível.
- **Controles nativos** (date/time/range/checkbox/scrollbar) seguem o tema via `color-scheme` (já setado em `:root`/`html.dark`).
- **Acentos** (`text-white` em botão colorido, vermelho de "excluir") podem ficar como estão.
- **Sempre teste**: abra a tela, ligue o modo escuro e confira o contraste. Se uma cor crua não estiver coberta, ou troque por `wa-*`/`.wa-field`, ou adicione o override `html.dark` correspondente em `custom.css`.

Telas de plugin (`storages/plugins/<id>/static/*.js`) seguem as MESMAS regras — usam o mesmo runtime do Tailwind e o mesmo `custom.css`.

---

## Largura e transbordo — a regra do `min-w-0`

> **REGRA — coluna flex que recebe conteúdo de largura livre precisa de `min-w-0`.**
> "Largura livre" é markdown renderizado, `<pre>`, tabela, JSON, log — qualquer coisa cuja largura o autor da tela não controla.

O motivo é uma regra do flexbox que quase ninguém tem na ponta da língua:

> Um item flex nasce com `min-width: auto`, e `auto` **resolve para o min-content do conteúdo** — a não ser que o item seja scroll container (`overflow` ≠ `visible` no eixo).

Ou seja: `flex-1` não significa "ocupe o espaço disponível", significa "ocupe o espaço disponível, **mas nunca menos que o meu min-content**". Se dentro houver um `<pre>` com uma linha de 4 000px, o item **exige** 4 000px, o container estoura, e o `overflow-hidden` do ancestral corta tudo que ficou à direita — em silêncio, sem barra de rolagem, sem erro no console.

⚠️ **`overflow-x-auto` num filho NÃO protege o ancestral.** A barra pertence ao filho; a largura intrínseca dele continua subindo pela árvore em bloco até encontrar o primeiro item flex, e é ali que ela vira largura de verdade. Quem tem de mudar é o **item flex**, não o `<pre>`.

⚠️ **Só quebra a partir do breakpoint em que a linha é `flex-row`.** Enquanto o layout é `flex-col`, a largura é o eixo **cruzado**, os filhos apenas esticam e nada estoura. É por isso que esse defeito costuma aparecer só no desktop e passar batido em quem testou no celular.

### O caso que fixou a regra (plano 145)

O modal de detalhe do plugin `melhorias` põe painel e chat lado a lado num `lg:flex-row`. A coluna do chat era `flex-1 min-h-0 flex flex-col` — tinha `min-h-0`, não tinha `min-w-0`. A IA de melhoria devolve o retorno da ferramenta num bloco de código `json` de **uma linha só**, e `<pre>` é `white-space: pre`.

Medido no painel real (Chromium headless, sugestão com bloco de código):

| | antes | depois |
|---|---|---|
| modal | 1200px | 1200px |
| coluna do chat | **2690px** | 820px |
| cortado à direita | **1870px** | 0px |
| botão **Enviar** | invisível | visível |

O operador via a resposta da IA truncada no meio e **não tinha como responder** — o compositor inteiro estava fora da área visível. Uma classe resolveu.

As colunas irmãs nunca quebraram, mas por acidente e não por cuidado: a da esquerda é `lg:shrink-0` com largura fixa quando há chat, e scroll container (`lg:overflow-y-auto`, que computa os dois eixos para `auto`) quando não há; o log de mensagens também é scroll container. Ambas ganham `min-width: 0` de graça.

### Quebra de palavra é problema separado

`min-w-0` faz a **coluna** caber. Não faz o **texto** quebrar: um token sem espaço maior que a linha — caminho de arquivo, hash, URL, `id` longo — continua transbordando, porque `overflow-wrap` é `normal` por padrão.

- **`break-words`** (`overflow-wrap: break-word`) quebra **só o que não cabe de jeito nenhum**. É o default certo para bolha, card e prosa.
- **`break-all`** (`word-break: break-all`) quebra também o token que caberia inteiro na linha seguinte, picando identificadores curtos no meio. Use só quando a coluna for estreita de propósito.
- `overflow-wrap` **é herdado**: `break-words` no contêiner de texto já alcança o `<code>` inline lá dentro. Repetir a classe no filho é útil quando ele pode ser montado em outro lugar — foi por isso que o `<code>` do `renderMarkdown` do `melhorias` recebeu a sua.

### Como conferir

O `class` mente pouco, mas o layout mente menos. Duas medições valem mais que uma leitura:

```js
// 1. algum descendente passa da borda direita do container?
//    (ignore quem está DENTRO de um scroll container próprio: ali é de propósito)
[...box.querySelectorAll('*')].filter(e =>
  e.getBoundingClientRect().right > box.getBoundingClientRect().right + 1)

// 2. o container rola de lado sem que ninguém tenha pedido?
box.scrollWidth - box.clientWidth   // deve ser 0
```

E o teste barato que impede a recaída: um `node --test` que lê o fonte e exige a classe, com a **mensagem de falha carregando o número medido** — quem vê o vermelho precisa saber o que volta a acontecer, não só o que sumiu. Exemplo em `plugins/melhorias/tests/js/layout_guard.test.js` no repositório de plugins.
