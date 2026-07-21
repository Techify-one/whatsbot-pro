// @ts-check
//
// Pré-validação de anexo no compositor — espelho client-side de
// `channels/media_limits.py`.
//
// Os limites NÃO moram aqui: vêm do payload da conversa (`media_limits`), que o
// backend monta a partir do que o PROVIDER declara (o WhatsApp oficial declara os
// seus no plugin `whatsapp_cloud`). Este módulo só avalia o arquivo escolhido
// contra o que o canal mandou, para bloquear o anexo com um popup ANTES do envio
// em vez de deixar o provedor recusar depois.
//
// Canal que não declara limites para um tipo (GOWA/Telegram) => nada é bloqueado.

// Teto defensivo de entrada de vídeo: acima disso nem o transcode do servidor
// tenta (espelha `video_transcode.MAX_INPUT_BYTES`).
export const VIDEO_INPUT_CEILING_BYTES = 200 * 1024 * 1024;

const KIND_LABEL = {
  image: 'Imagem',
  video: 'Vídeo',
  audio: 'Áudio',
  document: 'Documento',
  sticker: 'Figurinha',
};

/** Tamanho legível (mesma regra do backend: MB sem casa quando redondo). */
export function fmtSize(numBytes) {
  if (!(numBytes > 0)) return '0 KB';
  if (numBytes < 1024 * 1024) return `${Math.round(numBytes / 1024)} KB`;
  const mb = numBytes / (1024 * 1024);
  return Math.abs(mb - Math.round(mb)) < 0.05 ? `${Math.round(mb)} MB` : `${mb.toFixed(1)} MB`;
}

function extOf(filename) {
  const name = String(filename || '');
  const dot = name.lastIndexOf('.');
  return dot > 0 ? name.slice(dot).toLowerCase() : '';
}

function extList(entry) {
  return (entry && Array.isArray(entry.extensions) ? entry.extensions : [])
    .map(e => String(e).replace(/^\./, '').toUpperCase());
}

/**
 * Frase de apoio para o popup: o que ESTE canal aceita para o tipo.
 * @returns {string} vazio quando o canal não impõe nada.
 */
export function limitsSummary(kind, entry) {
  if (!entry) return '';
  const parts = [];
  const exts = extList(entry);
  if (exts.length) parts.push(exts.join(', '));
  if (entry.max_bytes) parts.push(`até ${fmtSize(entry.max_bytes)}`);
  if (!parts.length) return '';
  const label = (KIND_LABEL[kind] || 'Arquivo').toLowerCase();
  return `Este canal aceita ${label} em ${parts.join(' ')}.`;
}

/**
 * Avalia um arquivo escolhido contra os limites do canal.
 *
 * @param {{name?:string, size?:number}} file - o File escolhido.
 * @param {string} kind - 'image' | 'video' | 'audio' | 'document' | 'sticker'.
 * @param {Object|null} mediaLimits - mapa `{kind: {max_bytes, extensions, transcode}}`.
 * @returns {{reason:string, message:string, detail:string}|null} null = pode enviar.
 */
export function checkMediaFile(file, kind, mediaLimits) {
  if (!file) return null;
  const size = Number(file.size) || 0;
  const filename = file.name || '';

  // Teto de entrada de vídeo — vale mesmo sem limites declarados (o servidor
  // também recusa: nem valida nem recomprime um arquivo desse tamanho).
  if (kind === 'video' && size > VIDEO_INPUT_CEILING_BYTES) {
    return {
      reason: 'too_big',
      message: `Vídeo muito grande (máx. ${fmtSize(VIDEO_INPUT_CEILING_BYTES)}). `
        + 'Reduza o arquivo e tente novamente.',
      detail: '',
    };
  }

  const entry = mediaLimits && mediaLimits[kind];
  if (!entry) return null;

  // Vídeo com transcode disponível no servidor: um arquivo fora do padrão ainda
  // pode ser recomprimido, então não bloqueia aqui (o servidor decide).
  if (kind === 'video' && entry.transcode) return null;

  const label = KIND_LABEL[kind] || 'Arquivo';
  const detail = limitsSummary(kind, entry);
  const exts = extList(entry);
  if (exts.length && !exts.includes(extOf(filename).replace(/^\./, '').toUpperCase())) {
    return {
      reason: 'bad_format',
      message: `Formato de ${label.toLowerCase()} não suportado por este canal. `
        + `Use ${exts.join('/')}.`,
      detail,
    };
  }
  if (entry.max_bytes && size > entry.max_bytes) {
    return {
      reason: 'too_big',
      message: `${label} acima do limite de ${fmtSize(entry.max_bytes)} deste canal `
        + `(o arquivo tem ${fmtSize(size)}). Reduza o tamanho e tente novamente.`,
      detail,
    };
  }
  return null;
}
