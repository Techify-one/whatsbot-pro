// Testes puros da detecção de entidades da mensagem (node --test).
//   node --test web/static/js/services/messageEntities.test.js
//
// Plano 97 · F1. Cobre os POSITIVOS (URL, e-mail, telefone, JID), os NEGATIVOS
// que importam neste produto (protocolo, valor, data, CPF, número cru sem `+`) e
// as invariantes de segurança (o módulo nunca escapa/desescapa; `javascript:` é
// inalcançável; a âncora não pode ser reprocessada por regra posterior).
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  linkifyEntities, linkifyToTokens, detectEntity, entityFromElement, entityActions,
} from './messageEntities.js';

// ── Positivos ────────────────────────────────────────────────────

test('URL vira âncora com data-entity/data-value e o estilo de sempre', () => {
  const out = linkifyEntities('veja https://exemplo.com/a?b=1 agora');
  assert.ok(out.includes('href="https://exemplo.com/a?b=1"'));
  assert.ok(out.includes('data-entity="url"'));
  assert.ok(out.includes('data-value="https://exemplo.com/a?b=1"'));
  assert.ok(out.includes('target="_blank"'));
  assert.ok(out.includes('rel="noopener noreferrer"'));
  assert.ok(out.includes('color:#53bdeb;text-decoration:underline;word-break:break-all'));
});

test('e-mail vira mailto:', () => {
  const out = linkifyEntities('escreva para contato@empresa.com.br hoje');
  assert.ok(out.includes('href="mailto:contato@empresa.com.br"'));
  assert.ok(out.includes('data-entity="email"'));
  assert.ok(out.includes('>contato@empresa.com.br</a>'));
});

test('telefone internacional (+) vira tel:', () => {
  const out = linkifyEntities('ligue +55 11 99999-8888');
  assert.ok(out.includes('data-entity="phone"'));
  assert.ok(out.includes('href="tel:+5511999998888"'));
});

test('máscara BR completa vira tel: com o 55 na frente', () => {
  const out = linkifyEntities('ligue (11) 99999-8888 amanhã');
  assert.ok(out.includes('data-entity="phone"'));
  assert.ok(out.includes('href="tel:+5511999998888"'));
});

test('JID continua span não-clicável (sufixo fechado)', () => {
  const out = linkifyEntities('de 5511999999999@s.whatsapp.net');
  assert.ok(out.includes('<span data-entity="jid"'));
  assert.ok(out.includes('cursor:default'));
  assert.ok(!out.includes('<a '));
  assert.ok(!out.includes('mailto:'));
});

test('e-mail com usuário numérico é e-mail, não JID (bug §2.4)', () => {
  const out = linkifyEntities('5511999@gmail.com');
  assert.ok(out.includes('href="mailto:5511999@gmail.com"'));
  assert.ok(!out.includes('data-entity="jid"'));
});

test('sufixo parecido com JID não sequestra o e-mail', () => {
  const out = linkifyEntities('1234567@lidera.com.br');
  assert.ok(out.includes('data-entity="email"'));
  assert.ok(out.includes('href="mailto:1234567@lidera.com.br"'));
});

// ── Negativos (D4 — não linkificar dígito solto) ─────────────────

for (const [rotulo, texto] of [
  ['protocolo do plugin', '🔖 Protocolo aberto · PROT-12345678'],
  ['valor em reais', 'ficou R$ 1.234,56 no total'],
  ['data', 'agendado para 30/07/2026'],
  ['CPF cru', 'o CPF é 12345678901'],
  ['número cru sem +', 'meu whats é 5511999999999'],
  ['id de pedido', 'pedido 2024-000123456 confirmado'],
]) {
  test(`negativo: ${rotulo} não vira link`, () => {
    const out = linkifyEntities(texto);
    assert.equal(out, texto, `não deveria linkificar: ${texto}`);
  });
}

test('texto sem entidade passa byte-idêntico', () => {
  const s = 'bom dia, tudo bem por aí?';
  assert.equal(linkifyEntities(s), s);
  assert.equal(linkifyEntities(''), '');
  assert.equal(linkifyEntities(null), '');
});

// ── Segurança (D5) ───────────────────────────────────────────────

test('javascript: nunca vira href', () => {
  const out = linkifyEntities('javascript:alert(1)');
  assert.ok(!out.includes('href'));
  assert.equal(out, 'javascript:alert(1)');
});

test('HTML já escapado passa intacto — o módulo não escapa nem desescapa', () => {
  const escapado = '&lt;img src=x onerror=alert(1)&gt;';
  assert.equal(linkifyEntities(escapado), escapado);
});

test('URL com & e aspas escapadas não fecha o atributo', () => {
  const out = linkifyEntities('https://x.com/?a=1&amp;b=2&quot;c');
  // O href guarda a forma ESCAPADA; o navegador a desfaz ao ler `el.href`.
  assert.ok(out.includes('href="https://x.com/?a=1&amp;b=2&quot;c"'));
  // Nenhuma aspa crua escapou para dentro do atributo.
  assert.equal((out.match(/"/g) || []).length % 2, 0);
  assert.ok(!/href="[^"]*"[^ >]/.test(out));
});

test('não linkifica dentro de tag já gerada pelas regras anteriores', () => {
  const dentro = '<a href="https://ja.existe/x">rotulo</a>';
  const out = linkifyEntities(dentro);
  // O href da tag não é reprocessado; só o TEXTO entre tags é candidato.
  assert.equal(out, dentro);
});

// ── Token / reidratação (F1 item 4) ──────────────────────────────

test('linkifyToTokens esconde a âncora de regras posteriores', () => {
  const { text, restore } = linkifyToTokens('fale com contato@empresa.com');
  assert.ok(!text.includes('<a '));
  assert.ok(!text.includes('@empresa'));         // a menção não alcança o href
  // Uma regra posterior (menção) roda sobre o texto tokenizado sem estragar nada.
  const depois = text.replace(/@(empresa)/gi, '<span>@$1</span>');
  assert.equal(depois, text);
  const final = restore(depois);
  assert.ok(final.includes('href="mailto:contato@empresa.com"'));
});

test('restore devolve o token intacto quando o índice não existe', () => {
  const { restore } = linkifyToTokens('sem entidade');
  const orfao = '\uE000' + '42' + '\uE001';
  assert.equal(restore(orfao), orfao);
});

// ── detectEntity / entityFromElement / entityActions ─────────────

test('detectEntity acha a primeira entidade e classifica', () => {
  assert.equal(detectEntity('https://a.b/c').kind, 'url');
  assert.equal(detectEntity('x@y.com').kind, 'email');
  assert.equal(detectEntity('+55 11 99999-8888').kind, 'phone');
  assert.equal(detectEntity('5511999999999@s.whatsapp.net').kind, 'jid');
  assert.equal(detectEntity('PROT-12345678'), null);
  assert.equal(detectEntity(''), null);
  assert.equal(detectEntity(null), null);
});

test('detectEntity é reentrante (lastIndex não vaza entre chamadas)', () => {
  for (let i = 0; i < 3; i++) assert.equal(detectEntity('x@y.com').kind, 'email');
});

test('detectEntity monta o display bonito do telefone', () => {
  assert.equal(detectEntity('(11) 99999-8888').display, '+55 (11) 99999-8888');
});

test('entityFromElement lê o dataset e tolera lixo', () => {
  assert.deepEqual(
    entityFromElement({ dataset: { entity: 'url', value: 'https://a.b' } }),
    { kind: 'url', value: 'https://a.b', display: 'https://a.b' },
  );
  assert.equal(entityFromElement(null), null);
  assert.equal(entityFromElement({}), null);                                  // nó de texto/svg
  assert.equal(entityFromElement({ dataset: { entity: 'url' } }), null);      // sem valor
  assert.equal(entityFromElement({ dataset: { entity: 'xpto', value: 'a' } }), null);
});

test('entityFromElement cai no textContent quando falta data-value', () => {
  const e = entityFromElement({ dataset: { entity: 'email' }, textContent: 'a@b.com' });
  assert.equal(e.value, 'a@b.com');
});

test('entityActions: URL abre e copia', () => {
  const acts = entityActions({ kind: 'url', value: 'https://a.b/c', display: 'https://a.b/c' });
  assert.deepEqual(acts.map(a => a.label), ['Abrir link', 'Copiar endereço do link']);
  assert.equal(acts[0].href, 'https://a.b/c');
  assert.equal(acts[1].copy, 'https://a.b/c');
});

test('entityActions: e-mail e telefone', () => {
  const mail = entityActions({ kind: 'email', value: 'a@b.com', display: 'a@b.com' });
  assert.deepEqual(mail.map(a => a.label), ['Enviar e-mail', 'Copiar e-mail']);
  assert.equal(mail[0].href, 'mailto:a@b.com');

  const tel = entityActions({ kind: 'phone', value: '(11) 99999-8888', display: '' });
  assert.deepEqual(tel.map(a => a.label), ['Copiar número', 'Ligar', 'Conversar no WhatsApp']);
  assert.equal(tel[0].copy, '(11) 99999-8888');
  assert.equal(tel[1].href, 'tel:+5511999998888');
  assert.equal(tel[2].href, 'https://wa.me/5511999998888');
});

test('entityActions: JID só copia os dígitos; entidade vazia não gera item', () => {
  const jid = entityActions({ kind: 'jid', value: '5511999999999@s.whatsapp.net', display: '' });
  assert.deepEqual(jid.map(a => a.label), ['Copiar número']);
  assert.equal(jid[0].copy, '5511999999999');
  assert.deepEqual(entityActions(null), []);
  assert.deepEqual(entityActions({ kind: 'url', value: '' }), []);
});

test('todo item de ação tem id, label e ícone', () => {
  for (const kind of ['url', 'email', 'phone', 'jid']) {
    for (const a of entityActions({ kind, value: kind === 'phone' ? '+5511999998888' : 'a@b.com' })) {
      assert.ok(a.id && a.label && a.icon, `${kind}: item incompleto`);
      assert.ok(a.href || a.copy, `${kind}/${a.id}: sem href nem copy`);
    }
  }
});
