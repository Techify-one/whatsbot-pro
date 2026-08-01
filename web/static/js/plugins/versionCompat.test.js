import { test } from 'node:test';
import assert from 'node:assert/strict';
import { satisfiesVersionRange, selectSupportedVersion } from './versionCompat.js';

test('exact declarations retain major-version compatibility', () => {
  assert.equal(satisfiesVersionRange('1.9.0', '1.0'), true);
  assert.equal(satisfiesVersionRange('2.0.0', '1.0'), false);
});

test('comparator ranges accept only versions inside their bounds', () => {
  assert.equal(satisfiesVersionRange('2.0', '>=1.0,<3.0'), true);
  assert.equal(satisfiesVersionRange('3.0', '>=1.0,<3.0'), false);
  assert.equal(satisfiesVersionRange('1.5', '>= 1.0 < 2.0'), true);
});

test('wildcard, caret and tilde ranges are supported', () => {
  assert.equal(satisfiesVersionRange('7.0', '*'), true);
  assert.equal(satisfiesVersionRange('1.8', '^1.2'), true);
  assert.equal(satisfiesVersionRange('2.0', '^1.2'), false);
  assert.equal(satisfiesVersionRange('1.2.9', '~1.2'), true);
  assert.equal(satisfiesVersionRange('1.3', '~1.2'), false);
});

test('service negotiation selects the newest compatible host surface', () => {
  const supported = ['1.0', '2.0'];
  assert.equal(selectSupportedVersion('>=1.0,<3.0', supported), '2.0');
  assert.equal(selectSupportedVersion('1.0', supported), '1.0');
  assert.equal(selectSupportedVersion('3.0', supported), null);
  assert.equal(selectSupportedVersion(undefined, supported, { defaultRange: '1.0' }), '1.0');
  assert.equal(selectSupportedVersion('', supported, { defaultRange: '1.0' }), '1.0');
  assert.equal(selectSupportedVersion('*', supported, { defaultRange: '1.0' }), '2.0');
});

test('malformed or partially understood ranges fail closed', () => {
  for (const range of ['banana', '>=1.0,lixo', '1.0 || 3.0', '^1.0 trailing']) {
    assert.equal(satisfiesVersionRange('2.0', range), false, range);
    assert.equal(selectSupportedVersion(range, ['1.0', '2.0']), null, range);
  }
});
