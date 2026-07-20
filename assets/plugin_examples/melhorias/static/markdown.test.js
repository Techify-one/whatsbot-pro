import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderMarkdown } from './markdown.js';

test('bold', () => {
  assert.ok(renderMarkdown('**a**').includes('<b>a</b>'));
});

test('italic', () => {
  const out = renderMarkdown('*a*');
  assert.ok(out.includes('<i>a</i>'), out);
});

test('inline code', () => {
  const out = renderMarkdown('`b`');
  assert.ok(out.includes('<code'), out);
  assert.ok(out.includes('b</code>'), out);
});

test('fenced code block', () => {
  const out = renderMarkdown('```js\nconst x = 1;\n```');
  assert.ok(out.includes('<pre'), out);
  assert.ok(out.includes('const x = 1;'), out);
});

test('heading h2', () => {
  const out = renderMarkdown('## X');
  assert.ok(out.includes('<h2'), out);
  assert.ok(out.includes('X</h2>'), out);
});

test('unordered list', () => {
  const out = renderMarkdown('- a\n- b');
  assert.ok(out.includes('<ul'), out);
  assert.ok(out.includes('<li>a</li>'), out);
  assert.ok(out.includes('<li>b</li>'), out);
});

test('ordered list', () => {
  const out = renderMarkdown('1. a\n2. b');
  assert.ok(out.includes('<ol'), out);
  assert.ok(out.includes('<li>a</li>'), out);
});

test('blockquote', () => {
  const out = renderMarkdown('> quoted');
  assert.ok(out.includes('<blockquote'), out);
  assert.ok(out.includes('quoted'), out);
});

test('horizontal rule', () => {
  const out = renderMarkdown('---');
  assert.ok(out.includes('<hr'), out);
});

test('strikethrough', () => {
  assert.ok(renderMarkdown('~~a~~').includes('<s>a</s>'));
});

test('safe link', () => {
  const out = renderMarkdown('[x](https://e.com)');
  assert.ok(out.includes('href="https://e.com"'), out);
  assert.ok(out.includes('rel="noopener'), out);
  assert.ok(out.includes('>x</a>'), out);
});

test('XSS 1: raw script is escaped', () => {
  const out = renderMarkdown('<script>alert(1)</script>');
  assert.ok(!out.includes('<script'), out);
  assert.ok(out.includes('&lt;script'), out);
});

test('XSS 2: javascript link is neutralized', () => {
  const out = renderMarkdown('[x](javascript:alert(1))');
  assert.ok(!out.includes('href="javascript'), out);
  assert.ok(!out.includes('javascript:'), out);
  assert.ok(out.includes('x'), out);
});

test('XSS 3: img onerror is escaped', () => {
  const out = renderMarkdown('<img src=x onerror=alert(1)>');
  // No live <img tag — the whole thing is escaped text, so onerror can't fire.
  assert.ok(!out.includes('<img'), out);
  assert.ok(out.includes('&lt;img'), out);
});

test('empty and null', () => {
  assert.equal(renderMarkdown(''), '');
  assert.equal(renderMarkdown(null), '');
});
