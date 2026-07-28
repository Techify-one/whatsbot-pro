// Run with: node --test web/static/js/components/contacts/menuLayout.test.js
//
// Locks the flyout vertical placement (Tags / Atribuir atendente submenus): a
// submenu near the bottom of the screen must shift UP so every option stays inside
// the viewport, instead of being clipped by the bottom edge.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { clampFlyoutOffset } from './menuLayout.js';

const VH = 800; // viewport height for these cases
const M = 8;    // default margin used by clampFlyoutOffset

test('row with plenty of room below → no shift (offset 0)', () => {
  // Flyout 300px tall, row at 100px: 100 + 300 + 8 = 408 <= 800, fits as-is.
  assert.equal(clampFlyoutOffset(100, 300, VH), 0);
});

test('row near the bottom → shifts UP so the flyout fits (negative offset)', () => {
  // Row at 700px, flyout 300px: 700 + 300 + 8 = 1008 > 800 → clamp.
  // Desired top = 800 - 300 - 8 = 492; offset = 492 - 700 = -208.
  assert.equal(clampFlyoutOffset(700, 300, VH), -208);
});

test('after shifting up, the flyout bottom sits at exactly the bottom margin', () => {
  const rowTop = 700, flyH = 300;
  const offset = clampFlyoutOffset(rowTop, flyH, VH);
  const flyoutTop = rowTop + offset;
  assert.equal(flyoutTop + flyH, VH - M); // bottom edge respects the margin
});

test('row at the very bottom → clamped, never pushed above the top margin', () => {
  // Tall flyout (max-h ~70vh) with the row at the extreme bottom.
  const rowTop = 790, flyH = 560; // 70vh of 800
  const offset = clampFlyoutOffset(rowTop, flyH, VH);
  const flyoutTop = rowTop + offset;
  assert.ok(flyoutTop >= M, `flyoutTop ${flyoutTop} should stay >= ${M}`);
  assert.ok(flyoutTop + flyH <= VH - M + 1e-9, 'flyout stays within the bottom edge');
});

test('flyout barely taller than viewport → pinned to the top margin', () => {
  // Degenerate case: flyout cannot fully fit; clamp keeps its top at the margin
  // (the internal scrollbar then covers the overflow).
  const offset = clampFlyoutOffset(400, VH, VH);
  assert.equal(400 + offset, M);
});

test('short flyout near the bottom still just fits without leaving the row', () => {
  // Small list (e.g. 2 agents): 750 + 120 + 8 = 878 > 800 → nudge up to 672.
  assert.equal(clampFlyoutOffset(750, 120, VH), 672 - 750);
});
