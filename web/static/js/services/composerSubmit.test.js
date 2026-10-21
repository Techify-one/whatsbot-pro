import test from 'node:test';
import assert from 'node:assert';
import { submitPlan, isAudioOnly, captionTargetIndex } from './composerSubmit.js';

// Plano 124 · F1 — a matriz de roteamento do botão Enviar / tecla Enter.

test('sem texto e sem fila: não faz nada', () => {
  assert.deepEqual(submitPlan({}), { action: 'noop', caption: '', reason: 'empty' });
  assert.equal(submitPlan({ text: '   \n  ' }).action, 'noop');
});

test('só texto: mensagem de texto, já aparado', () => {
  const plan = submitPlan({ text: '  oi, tudo bem?  ' });
  assert.equal(plan.action, 'text');
  assert.equal(plan.caption, 'oi, tudo bem?');
});

test('fila sem texto: envia a mídia com legenda vazia', () => {
  const plan = submitPlan({ queueLength: 3 });
  assert.equal(plan.action, 'media');
  assert.equal(plan.caption, '');
});

test('texto + fila: o texto vira a LEGENDA (o caso do plano 124)', () => {
  const plan = submitPlan({ text: 'segue o comprovante', queueLength: 2 });
  assert.equal(plan.action, 'media');
  assert.equal(plan.caption, 'segue o comprovante');
});

test('lote em voo: a FILA não dispara de novo', () => {
  for (const extra of [{ queueLength: 2 }, { text: 'oi', queueLength: 2 }]) {
    const plan = submitPlan({ ...extra, sending: true });
    assert.equal(plan.action, 'noop', JSON.stringify(extra));
    assert.equal(plan.reason, 'sending');
  }
});

test('lote em voo NÃO bloqueia uma mensagem de texto (ela é independente)', () => {
  const plan = submitPlan({ text: 'já estou enviando as fotos', sending: true });
  assert.equal(plan.action, 'text');
  assert.equal(plan.caption, 'já estou enviando as fotos');
});

test('janela de 24h fechada: template, e a legenda volta intacta para o chamador', () => {
  const plan = submitPlan({ text: 'oi', queueLength: 1, sessionClosed: true });
  assert.equal(plan.action, 'template');
  // O chamador precisa do texto para NÃO apagá-lo ao abrir o seletor.
  assert.equal(plan.caption, 'oi');
});

test('janela fechada não afeta a NOTA PRIVADA (nunca sai para o provedor)', () => {
  assert.equal(submitPlan({ text: 'oi', sessionClosed: true, mode: 'private' }).action, 'text');
  assert.equal(
    submitPlan({ text: 'oi', queueLength: 1, sessionClosed: true, mode: 'private' }).action,
    'media');
});

test('janela fechada com o compositor vazio ainda é noop', () => {
  assert.equal(submitPlan({ sessionClosed: true }).action, 'noop');
});

test('áudio + texto: o texto vai ANTES, como mensagem — nunca é descartado', () => {
  const plan = submitPlan({ text: 'escuta isso', queueLength: 1, queueIsAudioOnly: true });
  assert.equal(plan.action, 'text_then_media');
  assert.equal(plan.caption, 'escuta isso');
  assert.equal(plan.reason, 'audio_no_caption');
});

test('áudio sem texto: envio simples de mídia', () => {
  assert.equal(submitPlan({ queueLength: 1, queueIsAudioOnly: true }).action, 'media');
});

test('isAudioOnly', () => {
  assert.equal(isAudioOnly([]), false);
  assert.equal(isAudioOnly(null), false);
  assert.equal(isAudioOnly([{ kind: 'audio' }]), true);
  assert.equal(isAudioOnly([{ kind: 'audio' }, { kind: 'image' }]), false);
  assert.equal(isAudioOnly([{ kind: 'image' }]), false);
});

test('captionTargetIndex: a legenda vai no ÚLTIMO arquivo, não no primeiro', () => {
  assert.equal(captionTargetIndex([{ kind: 'image' }]), 0);
  assert.equal(captionTargetIndex([{ kind: 'image' }, { kind: 'image' }, { kind: 'image' }]), 2);
  assert.equal(captionTargetIndex([{ kind: 'image' }, { kind: 'document' }]), 1);
});

test('captionTargetIndex: áudio no fim é PULADO — a legenda desce, não se perde', () => {
  assert.equal(captionTargetIndex([{ kind: 'image' }, { kind: 'audio' }]), 0);
  assert.equal(captionTargetIndex([{ kind: 'image' }, { kind: 'audio' }, { kind: 'audio' }]), 0);
  assert.equal(captionTargetIndex([{ kind: 'audio' }, { kind: 'video' }, { kind: 'audio' }]), 1);
});

test('captionTargetIndex: sem alvo possível devolve -1 (fila só de áudio / vazia)', () => {
  assert.equal(captionTargetIndex([{ kind: 'audio' }]), -1);
  assert.equal(captionTargetIndex([]), -1);
  assert.equal(captionTargetIndex(null), -1);
  assert.equal(captionTargetIndex([null, undefined]), -1);
});

// Plano 177 — "Agrupar": vários anexos numa só mensagem (canal com a capability).

import { canGroup, groupableItems } from './composerSubmit.js';

const IMG = { kind: 'image' };
const DOC = { kind: 'document' };
const AUDIO = { kind: 'audio' };

test('canGroup: só com a capability do canal', () => {
  assert.equal(canGroup({ supported: false, queue: [IMG, IMG] }), false);
  assert.equal(canGroup({ supported: true, queue: [IMG, IMG] }), true);
  assert.equal(canGroup({ queue: [IMG, IMG] }), false, 'sem o campo = core anterior');
});

test('canGroup: com UM arquivo não há o que agrupar', () => {
  assert.equal(canGroup({ supported: true, queue: [IMG] }), false);
  assert.equal(canGroup({ supported: true, queue: [] }), false);
});

test('canGroup: áudio gravado não conta (nota de voz não é anexo)', () => {
  assert.equal(canGroup({ supported: true, queue: [IMG, AUDIO] }), false);
  assert.equal(canGroup({ supported: true, queue: [IMG, DOC, AUDIO] }), true);
});

test('canGroup: nota privada e sandbox nunca agrupam', () => {
  assert.equal(canGroup({ supported: true, mode: 'private', queue: [IMG, IMG] }), false);
  assert.equal(canGroup({ supported: true, sandbox: true, queue: [IMG, IMG] }), false);
});

test('groupableItems: tira só o áudio e preserva a ordem', () => {
  const a = { kind: 'image', n: 1 }; const b = { kind: 'document', n: 2 };
  assert.deepEqual(groupableItems([a, AUDIO, b]), [a, b]);
  assert.deepEqual(groupableItems(null), []);
});

test('submitPlan: group=true com fila vira media_group, com a legenda aparada', () => {
  const plan = submitPlan({ text: '  segue  ', queueLength: 3, group: true });
  assert.deepEqual(plan, { action: 'media_group', caption: 'segue' });
});

test('submitPlan: sem group o envio é o de sempre (regressão)', () => {
  assert.equal(submitPlan({ text: 'x', queueLength: 3 }).action, 'media');
  assert.equal(submitPlan({ text: 'x', queueLength: 3, group: false }).action, 'media');
});

test('submitPlan: group NÃO fura a janela de 24h nem o lote em voo', () => {
  assert.equal(submitPlan({ queueLength: 2, group: true, sessionClosed: true }).action, 'template');
  assert.equal(submitPlan({ queueLength: 2, group: true, sending: true }).action, 'noop');
});

test('submitPlan: sem anexo, group é ignorado (é só texto)', () => {
  assert.equal(submitPlan({ text: 'oi', group: true }).action, 'text');
});
