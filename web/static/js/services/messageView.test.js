// Run with: node --test web/static/js/services/messageView.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  SYSTEM_CARD_VARIANTS, isSystemCardRole, senderColor, quotedMediaText,
  isCollapsibleRole, collapsedPreview, cardStateKey, mediaCaptionOf,
} from './messageView.js';

test('isSystemCardRole: all panel-only roles recognized', () => {
  for (const role of ['private_note', 'transcription', 'system_notice',
                       'tool_call', 'conversation_event', 'system', 'error']) {
    assert.equal(isSystemCardRole(role), true, role);
  }
});

test('isSystemCardRole: chat roles are NOT system cards', () => {
  assert.equal(isSystemCardRole('user'), false);
  assert.equal(isSystemCardRole('assistant'), false);
  assert.equal(isSystemCardRole(undefined), false);
});

test('SYSTEM_CARD_VARIANTS: system_notice/system/error use semantic wa-* classes', () => {
  for (const role of ['system_notice', 'system', 'error']) {
    const v = SYSTEM_CARD_VARIANTS[role];
    assert.equal(v.useWaClasses, true, role);
    assert.match(v.cardClass, /wa-/, role);
    // No raw hex left in the card styling for the fixed variants.
    assert.equal(v.style, undefined, role);
  }
});

test('SYSTEM_CARD_VARIANTS: error keeps red text accent', () => {
  assert.match(SYSTEM_CARD_VARIANTS.error.cardClass, /text-red-500/);
});

test('SYSTEM_CARD_VARIANTS: private_note/transcription/tool_call keep theme accent colors', () => {
  assert.equal(SYSTEM_CARD_VARIANTS.private_note.useWaClasses, false);
  assert.match(SYSTEM_CARD_VARIANTS.transcription.style, /#2d1b4e/);
  assert.match(SYSTEM_CARD_VARIANTS.tool_call.style, /#2d1b0e/);
});

test('senderColor: user blue, operator amber, AI theme-aware var', () => {
  assert.equal(senderColor(true, false), '#1f7aec');
  assert.equal(senderColor(false, true), '#b45309');
  // IA usa a variável CSS --wa-ai-label (clara no dark, escura no light).
  assert.equal(senderColor(false, false), 'rgb(var(--wa-ai-label))');
});

test('quotedMediaText: per media type', () => {
  assert.equal(quotedMediaText({ media_type: 'image' }, ''), '📷 Foto');
  assert.equal(quotedMediaText({ media_type: 'image' }, 'minha foto'), 'minha foto');
  assert.equal(quotedMediaText({ media_type: 'audio' }, 'ignored'), '🎤 Áudio');
  assert.equal(quotedMediaText({ media_type: 'video' }, ''), '🎬 Vídeo');
  assert.equal(quotedMediaText({ media_type: 'sticker' }, 'x'), '🪧 Figurinha');
  assert.equal(quotedMediaText({ media_type: 'document' }, 'x'), '📄 Documento');
  assert.equal(quotedMediaText({ media_type: 'location' }, 'x'), '📍 Localização');
  assert.equal(quotedMediaText({ media_type: 'live_location' }, 'x'), '📍 Localização');
});

test('quotedMediaText: plain text passthrough', () => {
  assert.equal(quotedMediaText({}, 'hello'), 'hello');
  assert.equal(quotedMediaText({ media_type: 'poll' }, 'Enquete X'), 'Enquete X');
});

// ── Plano 87 — legenda da mídia ─────────────────────────────────────

const IMG_DESC = '[Descrição da imagem]: duas janelas do WinBox abertas\n'
  + '**Janela principal:**\n* linha de detalhe';

test('mediaCaptionOf: a coluna media_caption vence tudo', () => {
  assert.equal(
    mediaCaptionOf({ media_caption: 'olha esse erro', content: `${IMG_DESC}\nolha esse erro` }),
    'olha esse erro');
  // Mesmo quando o content é só a descrição da IA (linha sem legenda que
  // depois ganhou a coluna), a coluna manda.
  assert.equal(mediaCaptionOf({ media_caption: 'legenda', content: IMG_DESC }), 'legenda');
});

test('mediaCaptionOf: legado com descrição da IA no INÍCIO esconde tudo', () => {
  // O bug do plano 87: a legenda está no FIM de um markdown multilinha e NÃO
  // pode ser resgatada por corte posicional — melhor esconder do que expor a
  // descrição da IA como se fosse texto do cliente.
  assert.equal(mediaCaptionOf({ content: `${IMG_DESC}\nto pingando do roteador` }), '');
  assert.equal(mediaCaptionOf({ content: IMG_DESC }), '');
  assert.equal(mediaCaptionOf({ content: '[Transcrição do áudio]: olá' }), '');
});

test('mediaCaptionOf: legado com bloco da IA no FIM corta no marcador', () => {
  // Formato de DOCUMENTO (texto primeiro, prefixo depois) — era daqui que a
  // extração da IA vazava para dentro do balão.
  assert.equal(
    mediaCaptionOf({ content: 'segue o comprovante\n[Conteúdo do documento]: SICOOB\nR$ 898,30' }),
    'segue o comprovante');
  // Legenda multilinha do cliente é preservada inteira.
  assert.equal(
    mediaCaptionOf({ content: 'linha um\nlinha dois\n[Conteúdo do documento]: dump' }),
    'linha um\nlinha dois');
  // Documento SEM legenda: sobra só o bloco da IA ⇒ nada.
  assert.equal(mediaCaptionOf({ content: '[Conteúdo do documento]: dump' }), '');
});

test('mediaCaptionOf: placeholders não são legenda', () => {
  for (const p of ['[Imagem enviada pelo contato]', '[Áudio recebido]', '[Áudio]', '[Vídeo]']) {
    assert.equal(mediaCaptionOf({ content: p }), '', p);
  }
  assert.equal(mediaCaptionOf({ content: '' }), '');
  assert.equal(mediaCaptionOf(null), '');
});

test('mediaCaptionOf: legenda simples passa intacta', () => {
  assert.equal(mediaCaptionOf({ content: 'veja o vídeo' }), 'veja o vídeo');
  // displayContent (prefixo de grupo já removido) tem precedência sobre content.
  assert.equal(mediaCaptionOf({ content: '[Ana]: oi' }, 'oi'), 'oi');
});

// Regressão pega pela revisão adversarial: só GOWA/sandbox/envio-do-operador
// compõem "[Documento recebido: …]". Em Cloud/Telegram/Meta o content é a
// legenda PURA, e numa linha legada (media_caption NULL) devolver vazio apagaria
// ~200 legendas reais em produção — o próprio bug do plano 87, ao contrário.
test('mediaCaptionOf: documento SEM rótulo mantém a legenda legada', () => {
  assert.equal(mediaCaptionOf({ content: 'segue o comprovante assinado' }),
               'segue o comprovante assinado');
  // …e continua escondendo quando o que sobra é só texto da IA.
  assert.equal(mediaCaptionOf({ content: '[Conteúdo do documento]: dump do PDF' }), '');
  // Legenda + extração, sem rótulo (caminho Cloud): fica só a legenda.
  assert.equal(
    mediaCaptionOf({ content: 'segue o comprovante\n[Conteúdo do documento]: dump' }),
    'segue o comprovante');
});

test('quotedMediaText: citação não vaza a descrição da IA (plano 87)', () => {
  // Antes: o snippet mostrava "[Descrição da imagem]: duas janelas do WinBox…".
  assert.equal(quotedMediaText({ media_type: 'image', content: IMG_DESC }, IMG_DESC), '📷 Foto');
  assert.equal(
    quotedMediaText({ media_type: 'image', media_caption: 'olha o erro' }, IMG_DESC),
    'olha o erro');
  // Documento: rótulo fixo no legado, legenda quando a coluna existe.
  assert.equal(quotedMediaText({ media_type: 'document' }, 'qualquer'), '📄 Documento');
  assert.equal(
    quotedMediaText({ media_type: 'document', media_caption: 'contrato' }, 'x'), 'contrato');
});

// ── Plano 63 — collapsible cards ───────────────────────────────────

test('isCollapsibleRole: only transcription and tool_call collapse', () => {
  assert.equal(isCollapsibleRole('transcription'), true);
  assert.equal(isCollapsibleRole('tool_call'), true);
  // The table declares it — the render reads it (no longer dead data).
  assert.equal(SYSTEM_CARD_VARIANTS.transcription.collapsible, true);
  assert.equal(SYSTEM_CARD_VARIANTS.tool_call.collapsible, true);
});

test('isCollapsibleRole: the other 5 system cards do NOT collapse (D1)', () => {
  for (const role of ['private_note', 'system_notice', 'conversation_event',
                      'system', 'error']) {
    assert.equal(isCollapsibleRole(role), false, role);
  }
  assert.equal(isCollapsibleRole('user'), false);
  assert.equal(isCollapsibleRole('assistant'), false);
  assert.equal(isCollapsibleRole(undefined), false);
});

test('collapsedPreview: tool_call uses only the first non-empty line', () => {
  const content = '🔧 buscar_cliente\nnome: João\n→ encontrado';
  assert.equal(collapsedPreview('tool_call', content), '🔧 buscar_cliente');
});

test('collapsedPreview: tool_call skips leading blank lines', () => {
  assert.equal(
    collapsedPreview('tool_call', '\n\n🔧 abrir_protocolo\narg: 1'),
    '🔧 abrir_protocolo');
});

test('collapsedPreview: long text cut at word boundary + ellipsis', () => {
  const content =
    'A imagem mostra um cachorro correndo no parque durante uma tarde ensolarada';
  const out = collapsedPreview('transcription', content, { maxLen: 30 });
  assert.ok(out.length <= 31, `len ${out.length}: ${out}`);   // maxLen + '…'
  assert.ok(out.endsWith('…'), out);
  assert.ok(!out.slice(0, -1).endsWith(' '), 'no trailing space before ellipsis');
  assert.ok(content.startsWith(out.slice(0, -1)), 'preview is a prefix of content');
});

test('collapsedPreview: single very long word falls back to a hard cut', () => {
  const out = collapsedPreview('transcription', 'a'.repeat(200), { maxLen: 10 });
  assert.equal(out, 'aaaaaaaaaa…');
});

test('collapsedPreview: short text passes through without ellipsis', () => {
  assert.equal(collapsedPreview('transcription', 'Olá, tudo bem?'), 'Olá, tudo bem?');
});

test('collapsedPreview: collapses internal whitespace/newlines (non-tool_call)', () => {
  assert.equal(
    collapsedPreview('transcription', '  linha um\n\n  linha   dois  '),
    'linha um linha dois');
});

test('collapsedPreview: empty/whitespace/null content → empty string', () => {
  assert.equal(collapsedPreview('transcription', ''), '');
  assert.equal(collapsedPreview('transcription', null), '');
  assert.equal(collapsedPreview('transcription', undefined), '');
  assert.equal(collapsedPreview('transcription', '   \n  '), '');
  assert.equal(collapsedPreview('tool_call', '   \n  '), '');
});

test('cardStateKey: precedence _id > msg_id > role:ts > index', () => {
  assert.equal(cardStateKey({ _id: 42, msg_id: 'AB', role: 'transcription', ts: 9 }, 3), 'id:42');
  assert.equal(cardStateKey({ msg_id: 'AB', role: 'transcription', ts: 9 }, 3), 'mid:AB');
  assert.equal(cardStateKey({ role: 'tool_call', ts: 9 }, 3), 'rt:tool_call:9');
  assert.equal(cardStateKey({ role: 'tool_call' }, 3), 'ix:3');
  assert.equal(cardStateKey({}, 5), 'ix:5');
  assert.equal(cardStateKey(null, 7), 'ix:7');
});

test('cardStateKey: _id=0 is a valid id (not falsy-skipped)', () => {
  assert.equal(cardStateKey({ _id: 0, ts: 9, role: 'x' }, 3), 'id:0');
});

test('cardStateKey: empty msg_id falls through to role:ts', () => {
  assert.equal(cardStateKey({ msg_id: '', role: 'transcription', ts: 7 }, 2), 'rt:transcription:7');
});
