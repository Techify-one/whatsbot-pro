// Run with: node --test web/static/js/components/contacts/ContactList.team.test.js
//
// Structural guard for the responsive sidebar composition. Browser coverage can
// still exercise exact pixels, but these invariants prevent the common regressions:
// an extra row, a per-row request, or a fixed right column that overflows at zoom.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('./ContactList.js', import.meta.url), 'utf8');

function between(start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from);
  assert.notEqual(from, -1, `marcador inicial ausente: ${start}`);
  assert.notEqual(to, -1, `marcador final ausente: ${end}`);
  return source.slice(from, to);
}

test('TeamChip is presentation-only and never fetches per row', () => {
  const chip = between('function TeamChip', '// Kebab');

  assert.match(chip, /aria-label=\$\{team\.title\}/);
  assert.match(chip, /title=\$\{team\.title\}/);
  assert.match(chip, /class="truncate"/);
  assert.doesNotMatch(chip, /fetch\s*\(/);
});

test('team catalog is memoized once and resolved before rendering each row', () => {
  assert.match(source, /useMemo\(\(\) => buildTeamMap\(teams\), \[teams\]\)/);
  assert.match(source, /const team = teamPresentation\(c\.team_id, teamsById\)/);
});

test('assignee and team share a capped, shrinkable right column', () => {
  const caps = source.match(/ml-auto min-w-0 max-w-\[45%\]/g) || [];

  assert.equal(caps.length, 2, 'responsável e time devem usar o mesmo limite responsivo');
  assert.match(source, /max-w-\[110px\]/);
});

test('team stays on the name line and time stays on the preview line', () => {
  const nameLine = between(
    '<div class="flex justify-between items-center gap-[6px] min-w-0">',
    '${(c.conv_labels',
  );
  const previewLine = between(
    '<div class="flex justify-between items-center mt-[3px]">',
    '<!-- Plugin extension point:',
  );

  assert.match(nameLine, /<\$\{TeamChip\}/);
  assert.doesNotMatch(nameLine, /formatTime/);
  assert.match(previewLine, /formatTime\(c\.last_message_ts\)/);
});
