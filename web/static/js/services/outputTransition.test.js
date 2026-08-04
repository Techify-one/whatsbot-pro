// Run with: node --test web/static/js/services/outputTransition.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { transitionAfterOutput } from './outputTransition.js';

test('só volta ao fim depois de a saída confirmar', async () => {
  const ref = { current: null };
  let calls = 0;
  assert.equal(await transitionAfterOutput(false, true, () => { calls += 1; }, ref), false);
  assert.equal(calls, 0);
  assert.equal(await transitionAfterOutput(true, true, () => { calls += 1; }, ref), true);
  assert.equal(calls, 1);
});

test('confirmações concorrentes compartilham uma única transição', async () => {
  const ref = { current: null };
  let calls = 0;
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const back = async () => { calls += 1; await gate; };
  const first = transitionAfterOutput(true, true, back, ref);
  const second = transitionAfterOutput(true, true, back, ref);
  await Promise.resolve();
  assert.equal(calls, 1);
  release();
  assert.deepEqual(await Promise.all([first, second]), [true, true]);
  assert.equal(ref.current, null);
});
