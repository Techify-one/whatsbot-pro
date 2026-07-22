// @ts-check
//
// Fila de mídia pendente (plano 64 · F3/F4) — a parte PURA do
// `useMediaUpload`: classificar um File no `kind` que decide a rota de envio, e
// montar as entradas da fila. Sem DOM, sem rede, sem preact — testável direto
// com `node --test`.
//
// O modo de envio (`sendMode`) vem do GESTO, não de um passo posterior
// (decisão D1 do plano — zonas divididas estilo Telegram):
//
//   zona "Foto ou vídeo" → sendMode 'media' → imagem/vídeo vão inline
//   zona "Arquivo"       → sendMode 'file'  → tudo vai como documento (original)
//
// Áudio, PDF, zip e afins caem em documento nas DUAS zonas: não existe "foto"
// para eles, e /send-audio é nota de voz (PTT, sem legenda) — mandar um .mp3
// anexado por lá seria errado.

/**
 * @param {{type?:string}} file
 * @param {'media'|'file'} sendMode
 * @returns {'image'|'video'|'document'}
 */
export function classifyFile(file, sendMode) {
  const type = (file && file.type) || '';
  if (sendMode === 'media') {
    if (type.startsWith('image/')) return 'image';
    if (type.startsWith('video/')) return 'video';
  }
  return 'document';
}

/** Nome sempre presente — a bolha de documento lê `filename` e mostraria
 *  "[Documento enviado: undefined]" se viesse vazio. */
export function filenameFor(file, kind) {
  const name = (file && file.name) || '';
  if (name) return name;
  if (kind === 'image') return 'imagem.jpg';
  if (kind === 'video') return 'video.mp4';
  return 'arquivo';
}

let _seq = 0;
/** Id local único de item da fila (só precisa ser único no processo). */
export function nextQueueId() {
  _seq += 1;
  return `q${Date.now().toString(36)}_${_seq}`;
}

/**
 * Converte uma lista de File na fila de itens pendentes.
 *
 * @param {ArrayLike<any>} files
 * @param {'media'|'file'} sendMode
 * @param {{makeId?:()=>string, makePreviewUrl?:(f:any)=>(string|null)}} [deps]
 *   Injetáveis para teste — em produção o hook passa `URL.createObjectURL`.
 * @returns {Array<any>}
 */
export function buildQueueItems(files, sendMode, deps = {}) {
  const makeId = deps.makeId || nextQueueId;
  const makePreviewUrl = deps.makePreviewUrl || (() => null);
  return Array.from(files || []).filter(Boolean).map((file) => {
    const kind = classifyFile(file, sendMode);
    return {
      id: makeId(),
      file,
      kind,
      sendMode,
      filename: filenameFor(file, kind),
      // Só imagem/vídeo têm miniatura útil; documento mostra ícone + nome.
      previewUrl: (kind === 'image' || kind === 'video') ? makePreviewUrl(file) : null,
      caption: '',
      _status: 'queued',
      error: null,
    };
  });
}

/**
 * Item da fila para um clipe gravado pelo microfone. Áudio nunca vem de drop —
 * só do gravador — e sempre é nota de voz (sem legenda).
 */
export function audioQueueItem({ blob, filename = 'voice.ogg', previewUrl = null }, deps = {}) {
  const makeId = deps.makeId || nextQueueId;
  return {
    id: makeId(), blob, kind: 'audio', sendMode: 'media',
    filename, previewUrl, caption: '', _status: 'queued', error: null,
  };
}

/** Rótulo curto de progresso ("Enviando 2 de 5…"). */
export function progressLabel(sentCount, total) {
  if (!total) return '';
  if (total === 1) return 'Enviando…';
  return `Enviando ${Math.min(sentCount + 1, total)} de ${total}…`;
}
