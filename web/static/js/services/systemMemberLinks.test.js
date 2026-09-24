// Run with: node --test web/static/js/services/systemMemberLinks.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseMemberLinks, memberHref } from './systemMemberLinks.js';

test('token vira link com destino para "Novo contato" pré-preenchido', () => {
  const segs = parseMemberLinks('[[member:5511999999999|Thiago Carvalho]] entrou no grupo');
  assert.deepEqual(segs, [
    {
      type: 'member', phone: '5511999999999', label: 'Thiago Carvalho', name: 'Thiago Carvalho',
      href: '/contacts?createPhone=5511999999999&createName=Thiago%20Carvalho',
    },
    { type: 'text', text: ' entrou no grupo' },
  ]);
});

test('vários participantes: cada um vira link e o texto entre eles é preservado', () => {
  const segs = parseMemberLinks(
    '[[member:5511111111111|Ana]], [[member:5522222222222|Bruno]] entraram no grupo');
  assert.deepEqual(segs.map((s) => s.type), ['member', 'text', 'member', 'text']);
  assert.equal(segs[1].text, ', ');
  assert.equal(segs[3].text, ' entraram no grupo');
});

test('sem nome (rótulo é só o telefone): não pré-preenche o campo Nome', () => {
  const [seg] = parseMemberLinks('[[member:5511999999999|+5511999999999]] saiu do grupo');
  assert.equal(seg.label, '+5511999999999');
  assert.equal(seg.name, '');
  assert.equal(seg.href, '/contacts?createPhone=5511999999999');
});

test('rótulo vazio cai no "+telefone"', () => {
  const [seg] = parseMemberLinks('[[member:5511999999999|]] entrou no grupo');
  assert.equal(seg.label, '+5511999999999');
  assert.equal(seg.name, '');
});

test('linha antiga / sem token: um único segmento de texto, intacto', () => {
  assert.deepEqual(parseMemberLinks('Thiago Carvalho entrou no grupo'),
    [{ type: 'text', text: 'Thiago Carvalho entrou no grupo' }]);
});

test('conteúdo vazio ou não-string não quebra', () => {
  assert.deepEqual(parseMemberLinks(''), [{ type: 'text', text: '' }]);
  assert.deepEqual(parseMemberLinks(null), [{ type: 'text', text: '' }]);
  assert.deepEqual(parseMemberLinks(undefined), [{ type: 'text', text: '' }]);
});

test('telefone fora de 10–15 dígitos não é token (LID não vira link)', () => {
  const lid = '[[member:199998887776665123|Fulano]] entrou no grupo';   // 18 dígitos
  assert.deepEqual(parseMemberLinks(lid), [{ type: 'text', text: lid }]);
  const curto = '[[member:12345|Fulano]] entrou no grupo';
  assert.deepEqual(parseMemberLinks(curto), [{ type: 'text', text: curto }]);
});

test('nome com caracteres de URL é codificado no href', () => {
  const [seg] = parseMemberLinks('[[member:5511999999999|Ana & Cia/1?]] entrou no grupo');
  assert.equal(seg.href, '/contacts?createPhone=5511999999999&createName=Ana%20%26%20Cia%2F1%3F');
});

test('memberHref: nome vazio omite createName', () => {
  assert.equal(memberHref('5511999999999', ''), '/contacts?createPhone=5511999999999');
});
