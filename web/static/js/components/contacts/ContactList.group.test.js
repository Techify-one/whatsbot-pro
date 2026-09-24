// Run with: node --test web/static/js/components/contacts/ContactList.group.test.js
//
// Structural guard for the group marker on the sidebar row: it sits on line 1,
// glued to the channel chip, and only for groups.
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

test('GroupChip is an icon-only marker with an accessible name', () => {
  const chip = between('function GroupChip', '// Kebab');

  assert.match(chip, /title="Grupo"/);
  assert.match(chip, /aria-label="Grupo"/);
  assert.match(chip, /<svg[^>]*aria-hidden="true"/);
  assert.doesNotMatch(chip, /fetch\s*\(/);
});

test('GroupChip does not reuse the two-person glyph of TeamChip', () => {
  const glyph = (name, next) => between(`function ${name}`, next).match(/<path d="([^"]+)"/)[1];

  assert.notEqual(glyph('GroupChip', '// Kebab'), glyph('TeamChip', '// Marcador de GRUPO'));
});

test('group marker renders next to the channel chip, only for groups', () => {
  const line = between(
    '<${ChannelChip} provider=${c.channel_provider}',
    '<div class="flex justify-between items-center gap-[6px] min-w-0">',
  );

  assert.match(line, /c\.is_group \? html`<\$\{GroupChip\} \/>` : null/);
  assert.ok(
    line.indexOf('<${ChannelChip}') < line.indexOf('<${GroupChip}'),
    'o ícone de grupo vai depois do selo do canal',
  );
  assert.ok(
    line.indexOf('<${GroupChip}') < line.indexOf('<${AssigneeChip}'),
    'o ícone de grupo fica do lado do canal, não do responsável',
  );
});
