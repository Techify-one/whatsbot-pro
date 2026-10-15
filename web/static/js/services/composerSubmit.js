// @ts-check
//
// Roteamento do gesto de ENVIAR do compositor (plano 124 · F1).
//
// Desde que a fila de mídia virou uma BANDEJA que convive com a barra de
// entrada (em vez de substituí-la), "apertar Enter" deixou de ter um destino
// único: o mesmo campo de texto ora é uma mensagem, ora é a legenda do anexo.
// Esta é a parte PURA dessa decisão — sem preact, DOM ou rede, testável direto
// com `node --test`, no mesmo espírito de `mediaQueue.js`/`uploadLimits.js`.
//
// As regras não são novas: estavam espalhadas entre `useComposer.handleSend`
// (texto vazio, janela de 24h) e `useMediaUpload.confirmQueue` (lote em voo,
// janela de 24h de novo). Reuni-las aqui é o que impede que o botão e a tecla
// Enter divirjam.
//
// ⚠️ `text_then_media` existe por causa de UM contrato do backend: `/send-audio`
// é nota de voz (PTT) e NÃO aceita legenda. Descartar o texto que o operador
// digitou seria perda silenciosa; então ele vai como mensagem separada ANTES do
// clipe. Todos os outros tipos levam o texto como legenda.

/**
 * @typedef {'noop'|'text'|'media'|'media_group'|'text_then_media'|'template'} SubmitAction
 * @typedef {{action: SubmitAction, caption: string, reason?: string}} SubmitPlan
 */

/**
 * @param {Object} opts
 * @param {string} [opts.text] - conteúdo cru do compositor.
 * @param {number} [opts.queueLength] - itens na bandeja de anexos.
 * @param {boolean} [opts.queueIsAudioOnly] - a fila é só nota de voz?
 * @param {string} [opts.mode] - 'reply' | 'private'.
 * @param {boolean} [opts.sessionClosed] - janela de 24h fechada (WhatsApp Cloud).
 * @param {boolean} [opts.sending] - lote de mídia em voo.
 * @param {boolean} [opts.group] - envio agrupado (resultado de `canGroup`).
 * @returns {SubmitPlan}
 */
export function submitPlan({
  text = '', queueLength = 0, queueIsAudioOnly = false,
  mode = 'reply', sessionClosed = false, sending = false, group = false,
} = {}) {
  const caption = String(text || '').trim();
  const hasQueue = queueLength > 0;

  // Nada a enviar.
  if (!caption && !hasQueue) return { action: 'noop', caption: '', reason: 'empty' };

  // Fora da janela de 24h só passa template — vale para texto E mídia. O
  // chamador NÃO pode limpar texto nem fila aqui: o operador ainda vai precisar
  // do que escreveu depois de escolher (ou desistir do) template.
  if (sessionClosed && mode !== 'private') {
    return { action: 'template', caption, reason: 'session_window_closed' };
  }

  // ⚠️ `sending` (lote de mídia em voo) só barra o caminho de MÍDIA — o envio
  // sequencial da fila não pode ser disparado duas vezes. Uma mensagem de texto
  // é independente e continua permitida durante o upload, como sempre foi: a
  // barra de entrada já ficava visível enquanto o lote subia.
  if (!hasQueue) return { action: 'text', caption };

  if (sending) return { action: 'noop', caption: '', reason: 'sending' };

  if (queueIsAudioOnly && caption) {
    return { action: 'text_then_media', caption, reason: 'audio_no_caption' };
  }

  // Envio agrupado (plano 177): o chamador só passa `group: true` quando `canGroup` já
  // provou que o canal aceita e que há 2+ arquivos. É automático, sem escolha do
  // operador. A legenda é o CORPO do e-mail.
  if (group) return { action: 'media_group', caption };

  return { action: 'media', caption };
}

/** A fila inteira é nota de voz? (Áudio nunca leva legenda.) */
export function isAudioOnly(queue) {
  const list = Array.isArray(queue) ? queue : [];
  return list.length > 0 && list.every(item => item && item.kind === 'audio');
}

/**
 * Qual item do lote leva a legenda — o ÚLTIMO que a aceita (`-1` = nenhum).
 *
 * A legenda vai num item só: repeti-la nos 5 arquivos poluiria a conversa do
 * cliente. A escolha do ÚLTIMO (e não do primeiro, como na F1) é a ordem de
 * leitura do Telegram, e é também a que casa com o gesto real do operador —
 * ele cola as imagens e SÓ ENTÃO escreve, então o texto comenta o conjunto que
 * acabou de montar. Com a legenda no 1º arquivo, o comentário aparecia antes
 * do que ele descreve.
 *
 * ⚠️ Áudio é PULADO, não bloqueia: `/send-audio` é nota de voz e recusa
 * legenda, então numa fila mista (imagem + áudio gravado) a legenda desce para
 * a última imagem em vez de se perder. Fila só de áudio devolve `-1` — aí o
 * texto vira mensagem própria (`text_then_media`).
 *
 * @param {Array<{kind?: string}>} queue
 * @returns {number}
 */
export function captionTargetIndex(queue) {
  const list = Array.isArray(queue) ? queue : [];
  for (let i = list.length - 1; i >= 0; i -= 1) {
    if (list[i] && list[i].kind !== 'audio') return i;
  }
  return -1;
}

/**
 * Esta fila sai agrupada, numa só mensagem? (plano 177)
 *
 * Só quando o CANAL declara que entrega N arquivos como UMA mensagem
 * (`media_group_supported`, capability — nunca o nome do provider), a conversa é real
 * (sandbox não sai para o provedor), é resposta ao cliente (nota privada não vira
 * e-mail) e há o que agrupar: com UM arquivo não há grupo, e áudio gravado é nota de
 * voz e fica de fora — então contam só os itens que não são áudio.
 *
 * @param {Object} opts
 * @param {boolean} [opts.supported] - capability `media_group` do canal.
 * @param {boolean} [opts.sandbox]
 * @param {string} [opts.mode] - 'reply' | 'private'.
 * @param {Array<{kind?: string}>} [opts.queue]
 */
export function canGroup({ supported = false, sandbox = false, mode = 'reply', queue = [] } = {}) {
  if (!supported || sandbox || mode === 'private') return false;
  return groupableItems(queue).length >= 2;
}

/** Itens que entram num grupo: tudo menos áudio (nota de voz não é anexo de e-mail). */
export function groupableItems(queue) {
  const list = Array.isArray(queue) ? queue : [];
  return list.filter(item => item && item.kind !== 'audio');
}
