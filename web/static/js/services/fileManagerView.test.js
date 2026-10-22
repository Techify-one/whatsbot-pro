// Run with: node --test web/static/js/services/fileManagerView.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  fmtBytes, plural, isoDate, dayStart, periodRange,
  diskPercent, diskLevel, previewFamily,
  conversationHref, canDelete,
  summarizeSelection, confirmCopy,
  emptyDeleteResult, addDeleteResult, deleteNotice,
  buildListParams, hasActiveFilters,
  DISK_WARN_PCT, DISK_DANGER_PCT, DELETE_BATCH, SELECT_PAGE,
} from './fileManagerView.js';

const KB = 1024;
const MB = KB * 1024;
const GB = MB * 1024;
const TB = GB * 1024;

// ── fmtBytes ────────────────────────────────────────────────────────────────

test('fmtBytes: zero, nulo e negativo viram "0 B"', () => {
  assert.equal(fmtBytes(0), '0 B');
  assert.equal(fmtBytes(null), '0 B');
  assert.equal(fmtBytes(undefined), '0 B');
  assert.equal(fmtBytes(-5), '0 B');
});

test('fmtBytes: bytes sem casas, vírgula decimal pt-BR', () => {
  assert.equal(fmtBytes(512), '512 B');
  assert.equal(fmtBytes(1536), '1,50 KB');
  assert.equal(fmtBytes(12.34 * MB), '12,3 MB');
});

test('fmtBytes: vai até TB (formatBytes de uploadLimits para em MB)', () => {
  assert.equal(fmtBytes(3 * GB), '3,00 GB');
  assert.equal(fmtBytes(2.5 * TB), '2,50 TB');
  // acima de TB continua em TB, sem inventar unidade
  assert.equal(fmtBytes(2048 * TB), '2048 TB');
});

test('fmtBytes: casas decimais caem conforme o número cresce', () => {
  assert.equal(fmtBytes(1.234 * MB), '1,23 MB');
  assert.equal(fmtBytes(15.67 * MB), '15,7 MB');
  assert.equal(fmtBytes(150.9 * MB), '151 MB');
});

// ── plural ──────────────────────────────────────────────────────────────────

test('plural: singular só no 1', () => {
  assert.equal(plural(1, 'arquivo', 'arquivos'), '1 arquivo');
  assert.equal(plural(0, 'arquivo', 'arquivos'), '0 arquivos');
  assert.equal(plural(2, 'arquivo', 'arquivos'), '2 arquivos');
  assert.equal(plural(null, 'arquivo', 'arquivos'), '0 arquivos');
});

// ── datas ───────────────────────────────────────────────────────────────────

test('isoDate: YYYY-MM-DD com zero à esquerda', () => {
  assert.equal(isoDate(new Date(2026, 0, 5)), '2026-01-05');
  assert.equal(isoDate(new Date(2026, 11, 31)), '2026-12-31');
});

test('dayStart: meia-noite LOCAL do dia escolhido', () => {
  assert.equal(dayStart('2026-10-21'), new Date(2026, 9, 21).getTime() / 1000);
});

test('dayStart(addDays=1): fim EXCLUSIVO — o dia escolhido inteiro conta', () => {
  const start = dayStart('2026-10-21');
  const end = dayStart('2026-10-21', 1);
  assert.equal(end, new Date(2026, 9, 22).getTime() / 1000);
  assert.ok(end > start);
  // vira o mês sem aritmética de 86400
  assert.equal(dayStart('2026-10-31', 1), new Date(2026, 10, 1).getTime() / 1000);
});

test('dayStart: vazio ou inválido devolve "" (campo fica fora da query)', () => {
  assert.equal(dayStart(''), '');
  assert.equal(dayStart(undefined), '');
  assert.equal(dayStart('21/10/2026'), '');
});

test('periodRange: "Hoje" é de=até=hoje; 7 dias volta 6 dias', () => {
  const now = new Date(2026, 9, 21, 15, 30);
  assert.deepEqual(periodRange(1, now), { from: '2026-10-21', to: '2026-10-21' });
  assert.deepEqual(periodRange(7, now), { from: '2026-10-15', to: '2026-10-21' });
  assert.deepEqual(periodRange(30, now), { from: '2026-09-22', to: '2026-10-21' });
});

// ── disco ───────────────────────────────────────────────────────────────────

test('limiares do aviso: 80% amarelo, 95% vermelho', () => {
  assert.equal(DISK_WARN_PCT, 80);
  assert.equal(DISK_DANGER_PCT, 95);
});

test('diskPercent: usado/total, travado em 0–100', () => {
  assert.equal(diskPercent({ total: 100 * GB, used: 50 * GB }), 50);
  assert.equal(diskPercent({ total: 100, used: 250 }), 100);
  assert.equal(diskPercent({ total: 100, used: 0 }), 0);
});

test('diskPercent: sem medida ⇒ null (não inventa 0%)', () => {
  assert.equal(diskPercent(null), null);
  assert.equal(diskPercent({}), null);
  assert.equal(diskPercent({ total: 0, used: 0 }), null);
});

test('diskLevel: ok < 80 ≤ warn < 95 ≤ danger; as bordas pertencem ao nível de cima', () => {
  assert.equal(diskLevel(null), 'ok');
  assert.equal(diskLevel(0), 'ok');
  assert.equal(diskLevel(79.9), 'ok');
  assert.equal(diskLevel(80), 'warn');
  assert.equal(diskLevel(94.9), 'warn');
  assert.equal(diskLevel(95), 'danger');
  assert.equal(diskLevel(100), 'danger');
});

// ── prévia ──────────────────────────────────────────────────────────────────

test('previewFamily: segue o Content-Type do servidor', () => {
  assert.equal(previewFamily('image/jpeg'), 'image');
  assert.equal(previewFamily('video/mp4'), 'video');
  assert.equal(previewFamily('audio/ogg'), 'audio');
  assert.equal(previewFamily('application/pdf'), 'pdf');
  assert.equal(previewFamily('text/plain'), 'text');
});

test('previewFamily: ignora parâmetros e caixa; o resto é "other" (cartão Baixar)', () => {
  assert.equal(previewFamily('Text/Plain; charset=utf-8'), 'text');
  assert.equal(previewFamily('application/octet-stream'), 'other');
  assert.equal(previewFamily('application/vnd.openxmlformats-officedocument.wordprocessingml.document'), 'other');
  assert.equal(previewFamily(''), 'other');
  assert.equal(previewFamily(undefined), 'other');
});

// ── linha da lista ──────────────────────────────────────────────────────────

test('conversationHref: /conversations/<id>?message=<_id>', () => {
  assert.equal(conversationHref({ conversation_id: 12, message_id: 345 }), '/conversations/12?message=345');
});

test('conversationHref: sem conversation_id (legado) ⇒ null, só o nome', () => {
  assert.equal(conversationHref({ conversation_id: null, message_id: 5 }), null);
  assert.equal(conversationHref({ message_id: 5 }), null);
  assert.equal(conversationHref(null), null);
  assert.equal(conversationHref(undefined), null);
});

test('conversationHref: conversa sem mensagem ainda abre a conversa', () => {
  assert.equal(conversationHref({ conversation_id: 7, message_id: null }), '/conversations/7');
});

test('conversationHref: id 0 não é "ausente"', () => {
  assert.equal(conversationHref({ conversation_id: 0, message_id: 0 }), '/conversations/0?message=0');
});

test('canDelete: só arquivo de plugin SEM conversa fica sem o botão', () => {
  assert.equal(canDelete({ plugin: { id: 'x' }, orphan: true }), false);
  assert.equal(canDelete({ plugin: { id: 'x' }, orphan: false }), true);
  assert.equal(canDelete({ plugin: null, orphan: true }), true);
  assert.equal(canDelete({ orphan: false }), true);
});

// ── confirmação ─────────────────────────────────────────────────────────────

const f = (id, size, plugin) => ({ id, size, plugin: plugin ? { id: 'arquivos_suporte', name: 'Arquivos' } : undefined });

test('summarizeSelection: separa o que sai do disco do que é de plugin', () => {
  const s = summarizeSelection([f('a', 10), f('b', 20), f('c', 100, true)]);
  assert.deepEqual(s.ids, ['a', 'b', 'c']);
  assert.equal(s.total, 3);
  assert.deepEqual(s.disk, { count: 2, bytes: 30 });
  assert.deepEqual(s.plugin, { count: 1, bytes: 100 });
});

test('summarizeSelection: tamanho ausente conta zero', () => {
  const s = summarizeSelection([{ id: 'a' }]);
  assert.deepEqual(s.disk, { count: 1, bytes: 0 });
});

test('confirmCopy: um arquivo comum', () => {
  const c = confirmCopy(summarizeSelection([f('a', 2 * MB)]));
  assert.equal(c.title, 'Apagar este arquivo?');
  assert.match(c.lines[0], /^1 arquivo será apagado do servidor \(2,00 MB\)\. Isso não pode ser desfeito\.$/);
  assert.equal(c.lines.length, 2); // sem a linha de plugin
  assert.match(c.lines.at(-1), /Arquivo removido/);
});

test('confirmCopy: vários, com plugin ⇒ antecipa a linha dos plugins', () => {
  const c = confirmCopy(summarizeSelection([f('a', MB), f('b', MB), f('c', MB, true), f('d', MB, true)]));
  assert.equal(c.title, 'Apagar 4 arquivos?');
  assert.match(c.lines[0], /^2 arquivos serão apagados do servidor \(2,00 MB\)/);
  assert.equal(c.lines[1],
    '2 são usados por plugins: saem das conversas, mas continuam no servidor para o plugin enviar.');
});

test('confirmCopy: um só de plugin usa o singular', () => {
  const c = confirmCopy(summarizeSelection([f('a', MB, true), f('b', MB)]));
  assert.equal(c.lines[1],
    '1 é usado por plugin: sai das conversas, mas continua no servidor para o plugin enviar.');
});

test('confirmCopy: só arquivos de plugin ⇒ diz que nada sai do servidor', () => {
  const c = confirmCopy(summarizeSelection([f('a', MB, true)]));
  assert.equal(c.lines[0], 'Nenhum arquivo será apagado do servidor.');
});

// ── soma dos lotes e aviso final ────────────────────────────────────────────

test('addDeleteResult: soma os lotes e ignora campo ausente', () => {
  const acc = emptyDeleteResult();
  addDeleteResult(acc, { files: 3, bytes: 300, failed: 1, messages: 4, kept_plugin: 1, kept_unknown: 0 });
  addDeleteResult(acc, { files: 2, bytes: 50, failed: 0, messages: 2 }); // servidor sem kept_*
  assert.deepEqual(acc, { files: 5, bytes: 350, failed: 1, messages: 6, kept_plugin: 1, kept_unknown: 0 });
});

test('addDeleteResult: resposta vazia/nula não quebra', () => {
  const acc = emptyDeleteResult();
  addDeleteResult(acc, null);
  addDeleteResult(acc, {});
  assert.deepEqual(acc, emptyDeleteResult());
});

test('deleteNotice: sucesso simples repete os números reais', () => {
  const r = { ...emptyDeleteResult(), files: 1, bytes: 2 * MB };
  const n = deleteNotice(r);
  assert.equal(n.kind, 'success');
  assert.equal(n.text, '1 arquivo apagado · 2,00 MB liberados.');
});

test('deleteNotice: plugin e "na dúvida guarda" entram no aviso', () => {
  const r = { ...emptyDeleteResult(), files: 2, bytes: MB, kept_plugin: 3, kept_unknown: 1, failed: 2 };
  const n = deleteNotice(r);
  assert.equal(n.kind, 'success');
  assert.match(n.text, /2 arquivos apagados · 1,00 MB liberados/);
  assert.match(n.text, /3 arquivos de plugin saíram das conversas mas continuam no servidor/);
  assert.match(n.text, /1 arquivo continua no servidor porque não foi possível confirmar/);
  assert.match(n.text, /2 não puderam ser apagados/);
});

test('deleteNotice: só plugin ⇒ não diz que apagou', () => {
  const r = { ...emptyDeleteResult(), kept_plugin: 1 };
  const n = deleteNotice(r);
  assert.match(n.text, /^Nenhum arquivo foi apagado do servidor · 1 arquivo de plugin saiu das conversas/);
});

test('deleteNotice: falha no meio reporta o que já tinha saído', () => {
  const r = { ...emptyDeleteResult(), files: 1000, bytes: GB };
  const n = deleteNotice(r, 'Não foi possível apagar.');
  assert.equal(n.kind, 'error');
  assert.match(n.text, /^Não foi possível apagar\. Antes disso, 1\.000 arquivos apagados/);
});

test('deleteNotice: falha sem nada feito é só a mensagem de erro', () => {
  const n = deleteNotice(emptyDeleteResult(), 'Permissão negada.');
  assert.deepEqual(n, { kind: 'error', text: 'Permissão negada.' });
});

test('lotes batem com os tetos do servidor', () => {
  assert.equal(DELETE_BATCH, 1000);
  assert.equal(SELECT_PAGE, 500);
});

// ── query ───────────────────────────────────────────────────────────────────

const base = { root: 'received', sort: 'date', order: 'desc' };

test('buildListParams: sem filtros só leva root/sort/order/offset/limit', () => {
  assert.deepEqual(buildListParams(base, 0, 100),
    { root: 'received', sort: 'date', order: 'desc', offset: 0, limit: 100 });
});

test('buildListParams: contact_id (do seletor) vence o texto digitado', () => {
  const p = buildListParams({ ...base, contactId: 42, contact: 'Maria' }, 0, 100);
  assert.equal(p.contact_id, 42);
  assert.equal('contact' in p, false);
  const t = buildListParams({ ...base, contact: 'Maria' }, 0, 100);
  assert.equal(t.contact, 'Maria');
  assert.equal('contact_id' in t, false);
});

test('buildListParams: período manda meia-noite local e fim exclusivo', () => {
  const p = buildListParams({ ...base, dateFrom: '2026-10-20', dateTo: '2026-10-21' }, 0, 100);
  assert.equal(p.date_from, new Date(2026, 9, 20).getTime() / 1000);
  assert.equal(p.date_to, new Date(2026, 9, 22).getTime() / 1000);
});

test('buildListParams: só "até" não inventa "de"', () => {
  const p = buildListParams({ ...base, dateTo: '2026-10-21' }, 0, 100);
  assert.equal('date_from' in p, false);
  assert.ok(p.date_to);
});

test('buildListParams: canal, tipo de conversa, tipo de arquivo, órfãos e busca', () => {
  const p = buildListParams({
    ...base, kind: 'image', orphans: true, q: 'contrato', inboxId: 3, chatType: 'group',
  }, 500, 500);
  assert.equal(p.kind, 'image');
  assert.equal(p.orphans, true);
  assert.equal(p.q, 'contrato');
  assert.equal(p.inbox_id, 3);
  assert.equal(p.chat_type, 'group');
  assert.equal(p.offset, 500);
  assert.equal(p.limit, 500);
});

test('hasActiveFilters: ordenação e aba não contam como filtro', () => {
  assert.equal(hasActiveFilters(base), false);
  assert.equal(hasActiveFilters({ ...base, q: 'x' }), true);
  assert.equal(hasActiveFilters({ ...base, inboxId: 2 }), true);
  assert.equal(hasActiveFilters({ ...base, orphans: true }), true);
  assert.equal(hasActiveFilters({ ...base, dateFrom: '2026-10-01' }), true);
});
