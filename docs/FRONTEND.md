# Frontend — tema, modo escuro e legibilidade

> Guia das regras de cor/contraste do painel e das telas de plugin. O [`CLAUDE.md`](../CLAUDE.md) carrega a **regra curta** e os avisos ⚠️;
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
