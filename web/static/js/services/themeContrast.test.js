// Run with: node --test web/static/js/services/themeContrast.test.js
//
// Trava de legibilidade dos dois temas. Lê o custom.css REAL (não uma cópia dos
// valores) e mede o contraste WCAG dos pares que importam. Se alguém reintroduzir
// um cinza secundário abaixo de AA, apagar um separador ou voltar a dar o mesmo
// valor para `--wa-hover` e `--wa-selected` (o que já aconteceu no tema escuro e
// tornava a conversa aberta indistinguível da conversa sob o cursor), o teste
// aponta o par exato e a razão medida.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
  parseThemeTokens, relativeLuminance, contrastRatio, lstar, deltaLstar,
  evaluateTheme, CONTRAST_RULES,
} from './themeContrast.js';

const CSS_PATH = fileURLToPath(new URL('../../css/custom.css', import.meta.url));
const cssText = readFileSync(CSS_PATH, 'utf8');
const tokens = parseThemeTokens(cssText);

const fmt = (r) => `${r.fg} sobre ${r.bg} (${r.kind}) = `
  + (r.value === null ? `token ausente: ${r.missing}` : `${r.value.toFixed(2)}${r.unit}`)
  + `, mínimo ${r.min}${r.unit}`;

// ── primitivas WCAG ────────────────────────────────────────────────
test('relativeLuminance: âncoras conhecidas (preto/branco)', () => {
  assert.equal(relativeLuminance([0, 0, 0]), 0);
  assert.equal(relativeLuminance([255, 255, 255]), 1);
});

test('contrastRatio: preto/branco = 21:1 e é simétrico', () => {
  assert.equal(Math.round(contrastRatio([0, 0, 0], [255, 255, 255])), 21);
  assert.equal(contrastRatio([255, 255, 255], [0, 0, 0]), contrastRatio([0, 0, 0], [255, 255, 255]));
});

test('contrastRatio: cor contra ela mesma = 1:1', () => {
  assert.equal(contrastRatio([32, 44, 51], [32, 44, 51]), 1);
});

test('lstar: âncoras conhecidas (preto=0, branco=100)', () => {
  assert.equal(Math.round(lstar([0, 0, 0])), 0);
  assert.equal(Math.round(lstar([255, 255, 255])), 100);
});

// A razão de ser do ΔL*: a razão WCAG achata degraus reais no extremo escuro.
// Este par (fundo vs painel do tema escuro) mede só 1,19:1 mas ΔL* ≈ 8, um
// degrau perfeitamente visível — foi exatamente o que motivou métricas separadas.
test('deltaLstar: separa superfícies escuras que a razão WCAG achata', () => {
  const bg = [10, 16, 20], panel = [22, 35, 43];
  assert.ok(contrastRatio(bg, panel) < 1.3, 'razão WCAG achata este par (esperado)');
  assert.ok(deltaLstar(bg, panel) > 6, 'ΔL* enxerga o degrau');
  assert.equal(deltaLstar([32, 44, 51], [32, 44, 51]), 0);
});

// ── parsing do custom.css ──────────────────────────────────────────
test('parseThemeTokens: extrai os dois temas do custom.css real', () => {
  assert.ok(Object.keys(tokens.light).length >= 10, 'tema claro deve ter os tokens wa-*');
  assert.ok(Object.keys(tokens.dark).length >= 10, 'tema escuro deve ter os tokens wa-*');
  for (const key of ['bg', 'panel', 'text', 'secondary', 'border', 'hover', 'selected', 'teal']) {
    assert.ok(tokens.light[key], `tema claro sem --wa-${key}`);
    assert.ok(tokens.dark[key], `tema escuro sem --wa-${key}`);
  }
});

test('parseThemeTokens: toda tripleta está no intervalo 0-255', () => {
  for (const [theme, set] of Object.entries(tokens)) {
    for (const [name, rgb] of Object.entries(set)) {
      assert.equal(rgb.length, 3, `${theme}/--wa-${name} não é uma tripleta`);
      for (const c of rgb) {
        assert.ok(Number.isInteger(c) && c >= 0 && c <= 255, `${theme}/--wa-${name} fora de 0-255: ${c}`);
      }
    }
  }
});

// ── as regras de contraste ─────────────────────────────────────────
for (const theme of ['light', 'dark']) {
  const results = evaluateTheme(tokens[theme], theme);

  test(`tema ${theme}: todas as regras de contraste passam`, () => {
    const bad = results.filter(r => !r.ok);
    assert.deepEqual(bad.map(fmt), [], `contraste insuficiente no tema ${theme}`);
  });
}

test('nenhuma regra é ignorada silenciosamente: cada uma vale em ao menos um tema', () => {
  for (const rule of CONTRAST_RULES) {
    const themes = Object.keys(rule.min);
    assert.ok(themes.length > 0, `regra ${rule.fg}/${rule.bg} não se aplica a nenhum tema`);
  }
});

// Regressão nomeada: os três degraus de superfície do tema escuro já foram o
// MESMO valor. Uma igualdade exata é mais explícita que a razão de contraste
// para o leitor entender o que não pode voltar a acontecer.
test('tema escuro: border, hover e selected são valores distintos', () => {
  const { border, hover, selected } = tokens.dark;
  assert.notDeepEqual(hover, selected, 'hover e selected iguais: a conversa aberta some sob o hover');
  assert.notDeepEqual(border, hover, 'border e hover iguais: separadores desaparecem no hover');
});
