// Run with: node --test web/static/js/services/messageView.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  SYSTEM_CARD_VARIANTS, isSystemCardRole, senderColor, quotedMediaText,
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

test('senderColor: user blue, operator amber, AI green', () => {
  assert.equal(senderColor(true, false), '#1f7aec');
  assert.equal(senderColor(false, true), '#b45309');
  assert.equal(senderColor(false, false), '#047857');
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
