// Espelho EXATO de `media_kinds.py` — mesma tabela de extensões, mesma regra, mesmos textos.
// Divergir aqui faz a tela aceitar um anexo que a rota recusa (ou o contrário), e o operador
// não teria como saber quem está certo. Mexeu num lado, mexa no outro.
//
// Regra: só bloqueia quando reconhece que o arquivo é de OUTRA categoria. Não deu pra
// classificar (sem extensão conhecida e MIME genérico) ⇒ passa. `document` aceita tudo de
// propósito: imagem, vídeo e PDF são documentos válidos quando o operador quer mandar como
// arquivo.

export const EXTENSOES = {
  image: ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'heic', 'heif', 'tif', 'tiff', 'avif'],
  audio: ['mp3', 'ogg', 'oga', 'opus', 'm4a', 'aac', 'wav', 'amr', 'flac', 'wma'],
  video: ['mp4', 'mov', '3gp', '3gpp', 'm4v', 'mkv', 'webm', 'avi', 'wmv', 'mpeg', 'mpg'],
};

export const TIPOS_RESTRITOS = ['image', 'audio', 'video'];

export const ROTULOS = { image: 'imagem', audio: 'áudio', video: 'vídeo', document: 'documento' };
const ARTIGOS = { image: 'uma imagem', audio: 'um áudio', video: 'um vídeo' };

// O que o seletor de arquivo do navegador oferece por padrão. `document` sem `accept`
// (qualquer formato).
export const ACCEPT = { image: 'image/*', audio: 'audio/*', video: 'video/*', document: '' };

export function extensao(nome) {
  const bruto = String(nome || '').split('?')[0].split('#')[0];
  const base = bruto.substring(bruto.lastIndexOf('/') + 1);
  const ponto = base.lastIndexOf('.');
  return ponto > 0 ? base.slice(ponto + 1).trim().toLowerCase() : '';
}

export function categoria(nome, mime) {
  const tipoMime = String(mime || '').trim().toLowerCase().split(';')[0];
  const familia = tipoMime.split('/')[0];
  if (EXTENSOES[familia] && tipoMime !== `${familia}/`) return familia;
  const ext = extensao(nome);
  if (!ext) return null;
  return Object.keys(EXTENSOES).find((k) => EXTENSOES[k].includes(ext)) || null;
}

export function combina(tipo, nome, mime) {
  if (!TIPOS_RESTRITOS.includes(tipo)) return true;
  const cat = categoria(nome, mime);
  return cat === null || cat === tipo;
}

export function erroDeIncompatibilidade(tipo, nome, mime) {
  if (combina(tipo, nome, mime)) return null;
  const cat = categoria(nome, mime);
  const bruto = String(nome || '');
  const arquivo = bruto.substring(bruto.lastIndexOf('/') + 1) || 'o arquivo';
  return `A mensagem é do tipo “${ROTULOS[tipo] || tipo}”, mas ${arquivo} é `
    + `${ARTIGOS[cat] || cat}. Troque o arquivo ou mude o tipo da mensagem `
    + '(“Documento” aceita qualquer formato).';
}
