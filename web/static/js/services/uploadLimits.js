// @ts-check
//
// Tetos de upload (plano 64 · F2/F8) — espelho EXATO de server/upload_limits.py.
// O cliente valida para dar erro instantâneo (sem gastar rede); o servidor
// valida de novo porque o cliente é contornável. Mudou um lado, mude o outro.

/** 50 MB por arquivo. */
export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
/** 10 arquivos por gesto de soltar/colar/selecionar. */
export const MAX_FILES_PER_DROP = 10;

/** Formata bytes como "12,3 MB" (pt-BR) para mensagens ao operador. */
export function formatBytes(bytes) {
  const mb = bytes / (1024 * 1024);
  if (mb >= 1) return `${mb.toFixed(1).replace('.', ',')} MB`;
  const kb = Math.max(1, Math.round(bytes / 1024));
  return `${kb} KB`;
}

/**
 * Aplica os tetos a uma lista de arquivos.
 *
 * Puro: não toca em DOM nem em rede — devolve o que passou e o que foi cortado,
 * e quem chamou decide como avisar o operador.
 *
 * @param {Array<{name?:string, size?:number}>} files
 * @param {{maxBytes?:number, maxFiles?:number}} [opts]
 * @returns {{accepted:Array<any>, tooLarge:Array<any>, droppedForCount:number}}
 */
export function applyUploadLimits(files, opts = {}) {
  const maxBytes = opts.maxBytes ?? MAX_UPLOAD_BYTES;
  const maxFiles = opts.maxFiles ?? MAX_FILES_PER_DROP;
  const list = Array.from(files || []);
  const tooLarge = [];
  const withinSize = [];
  for (const f of list) {
    if (!f) continue;
    if ((f.size ?? 0) > maxBytes) tooLarge.push(f);
    else withinSize.push(f);
  }
  const accepted = withinSize.slice(0, Math.max(0, maxFiles));
  return {
    accepted,
    tooLarge,
    droppedForCount: withinSize.length - accepted.length,
  };
}

/**
 * Mensagem única (ou null) descrevendo o que foi recusado — pronta pro toast.
 * @param {{tooLarge:Array<any>, droppedForCount:number}} result
 */
export function limitsMessage(result, opts = {}) {
  const maxBytes = opts.maxBytes ?? MAX_UPLOAD_BYTES;
  const maxFiles = opts.maxFiles ?? MAX_FILES_PER_DROP;
  const parts = [];
  if (result.tooLarge.length === 1) {
    const f = result.tooLarge[0];
    parts.push(`"${f.name || 'arquivo'}" tem ${formatBytes(f.size || 0)} e excede o limite de ${formatBytes(maxBytes)}.`);
  } else if (result.tooLarge.length > 1) {
    parts.push(`${result.tooLarge.length} arquivos excedem o limite de ${formatBytes(maxBytes)}.`);
  }
  if (result.droppedForCount > 0) {
    parts.push(`Só é possível enviar ${maxFiles} arquivos por vez — ${result.droppedForCount} foram ignorados.`);
  }
  return parts.length ? parts.join(' ') : null;
}
