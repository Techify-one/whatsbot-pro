// Contraste do tema (WCAG 2.1) — módulo PURO, sem DOM.
//
// O tema do painel é dirigido por variáveis CSS (tripletas RGB) declaradas em
// `web/static/css/custom.css`: o bloco `:root` é o tema claro e `html.dark` é o
// escuro. Toda a paleta `wa-*` do Tailwind resolve para elas, então esses dois
// blocos são a ÚNICA fonte de verdade das cores do app.
//
// Este módulo lê aqueles blocos e mede o contraste entre pares de tokens, para
// que o teste companheiro (themeContrast.test.js) trave regressões de
// legibilidade — o histórico que motivou isto: `--wa-border`, `--wa-hover` e
// `--wa-selected` chegaram a ter o MESMO valor no tema escuro, o que apagava os
// separadores e tornava a conversa aberta indistinguível da conversa sob o
// cursor. Um olho humano deixa isso passar; uma razão de contraste, não.

// Extrai as tripletas `--wa-<token>: R G B;` de um bloco CSS.
function parseBlock(body) {
  const tokens = {};
  const re = /--wa-([A-Za-z][A-Za-z0-9]*)\s*:\s*(\d+)\s+(\d+)\s+(\d+)\s*;/g;
  let m;
  while ((m = re.exec(body)) !== null) {
    tokens[m[1]] = [Number(m[2]), Number(m[3]), Number(m[4])];
  }
  return tokens;
}

// Recorta o corpo `{ … }` da primeira regra cujo seletor casa exatamente.
function ruleBody(cssText, selector) {
  const at = cssText.indexOf(selector);
  if (at === -1) throw new Error(`seletor não encontrado no CSS: ${selector}`);
  const open = cssText.indexOf('{', at);
  const close = cssText.indexOf('}', open);
  if (open === -1 || close === -1) throw new Error(`bloco malformado para ${selector}`);
  return cssText.slice(open + 1, close);
}

// `{ light: {token: [r,g,b]}, dark: {…} }` a partir do texto do custom.css.
export function parseThemeTokens(cssText) {
  return {
    light: parseBlock(ruleBody(cssText, ':root')),
    dark: parseBlock(ruleBody(cssText, 'html.dark')),
  };
}

// Luminância relativa WCAG 2.1 de um [r, g, b] 0-255.
export function relativeLuminance([r, g, b]) {
  const lin = (c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

// Razão de contraste WCAG entre duas cores (1 = idênticas, 21 = preto/branco).
export function contrastRatio(a, b) {
  const la = relativeLuminance(a);
  const lb = relativeLuminance(b);
  const [hi, lo] = la >= lb ? [la, lb] : [lb, la];
  return (hi + 0.05) / (lo + 0.05);
}

// Claridade CIE L* (0 = preto, 100 = branco).
export function lstar(rgb) {
  const y = relativeLuminance(rgb);
  const f = y > 0.008856 ? Math.cbrt(y) : 7.787 * y + 16 / 116;
  return 116 * f - 16;
}

// Diferença de claridade perceptual entre duas cores, em unidades de L*.
//
// Por que NÃO usar contrastRatio para separar superfícies: o `+0.05` da fórmula
// WCAG domina no extremo escuro, então dois cinzas visivelmente distintos ficam
// espremidos perto de 1:1 — o fundo (#0A1014) e o painel (#16232B) deste tema
// medem 1,19:1 apesar de ΔL* = 8,4, que é um degrau claramente perceptível. A
// razão WCAG foi desenhada para legibilidade de TEXTO; para "esta superfície
// está acima daquela", L* é a métrica adequada. Referência prática: ΔL* ≈ 2-3 é
// o limiar de percepção em áreas grandes.
export function deltaLstar(a, b) {
  return Math.abs(lstar(a) - lstar(b));
}

// Regras verificadas. Cada uma declara a MÉTRICA adequada ao que ela protege:
//   - metric 'contrast' → razão WCAG, para legibilidade de texto e de acento.
//     Pisos: 4,5:1 (texto pequeno AA), 7:1 (AAA), 3:1 (componente não-textual).
//   - metric 'lightness' → ΔL*, para separação de SUPERFÍCIE (ver deltaLstar
//     acima: a razão WCAG não serve para isso no extremo escuro).
//
// `min` é por tema porque os dois têm ambições diferentes: o tema CLARO mantém
// separadores deliberadamente suaves (herança do visual WhatsApp) e endurecê-los
// seria redesenhá-lo; o tema ESCURO foi refeito para alto contraste a pedido do
// usuário, então os pisos dele são altos de propósito — são exatamente o que
// impede alguém de achatar a paleta de volta sem perceber.
export const CONTRAST_RULES = [
  // ── Legibilidade (razão WCAG) ──────────────────────────────────────
  // Texto primário sobre cada superfície onde ele de fato aparece.
  { fg: 'text', bg: 'bg', kind: 'texto', metric: 'contrast', min: { light: 4.5, dark: 7 } },
  { fg: 'text', bg: 'panel', kind: 'texto', metric: 'contrast', min: { light: 4.5, dark: 7 } },
  { fg: 'text', bg: 'inputBg', kind: 'texto', metric: 'contrast', min: { dark: 7 } },
  { fg: 'text', bg: 'outgoing', kind: 'texto', metric: 'contrast', min: { light: 4.5, dark: 4.5 } },
  { fg: 'text', bg: 'incoming', kind: 'texto', metric: 'contrast', min: { light: 4.5, dark: 7 } },
  // Texto secundário (horário, prévia da conversa, rótulos auxiliares).
  { fg: 'secondary', bg: 'bg', kind: 'texto', metric: 'contrast', min: { light: 4.5, dark: 7 } },
  { fg: 'secondary', bg: 'panel', kind: 'texto', metric: 'contrast', min: { light: 4.5, dark: 7 } },
  // Acento: ícones ativos, links, botão primário.
  { fg: 'teal', bg: 'bg', kind: 'acento', metric: 'contrast', min: { light: 3, dark: 4.5 } },
  { fg: 'teal', bg: 'panel', kind: 'acento', metric: 'contrast', min: { light: 3, dark: 4.5 } },

  // ── Separação de superfície (ΔL*) ──────────────────────────────────
  // Elevação: o painel tem que ler como "acima" do fundo — é o que faz a barra
  // lateral, os cards e os modais se destacarem em vez de derreterem na tela.
  { fg: 'panel', bg: 'bg', kind: 'elevação', metric: 'lightness', min: { dark: 6 } },
  // Separador visível sobre o painel.
  { fg: 'border', bg: 'panel', kind: 'superfície', metric: 'lightness', min: { light: 3, dark: 10 } },
  // Os dois estados de linha da sidebar não podem se confundir.
  { fg: 'selected', bg: 'hover', kind: 'superfície', metric: 'lightness', min: { dark: 6 } },
  { fg: 'hover', bg: 'bg', kind: 'superfície', metric: 'lightness', min: { dark: 8 } },
  // Bubble recebido contra o fundo do chat.
  { fg: 'incoming', bg: 'chatBg', kind: 'elevação', metric: 'lightness', min: { dark: 6 } },
];

// Avalia as regras de um tema. Devolve uma linha por regra aplicável, com o
// valor medido e o veredito — o teste afirma sobre `ok`, e um relatório legível
// sai de graça quando algo reprova. `unit` deixa o relatório honesto sobre qual
// métrica reprovou (`:1` de razão vs `ΔL*` de claridade).
export function evaluateTheme(tokens, theme) {
  const out = [];
  for (const rule of CONTRAST_RULES) {
    const min = rule.min[theme];
    if (min === undefined) continue;   // regra não se aplica a este tema
    const fg = tokens[rule.fg];
    const bg = tokens[rule.bg];
    const unit = rule.metric === 'lightness' ? 'ΔL*' : ':1';
    if (!fg || !bg) {
      out.push({ ...rule, theme, min, unit, value: null, ok: false, missing: !fg ? rule.fg : rule.bg });
      continue;
    }
    const value = rule.metric === 'lightness' ? deltaLstar(fg, bg) : contrastRatio(fg, bg);
    out.push({ ...rule, theme, min, unit, value, ok: value >= min });
  }
  return out;
}
