// Pré-visualização do anexo de uma mensagem do retorno, num modal.
//
// Sem isto, uma mensagem já salva mostra só o NOME do arquivo — quem volta para conferir o
// retorno não tem como saber QUAL imagem/áudio/vídeo está ali sem disparar para um cliente.
//
// ⚠️ HTM: NUNCA use crase nem ${...} dentro de comentário em html`...` — fecha o template
// e o módulo quebra em silêncio. Comentários explicativos ficam FORA do html.
import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import { categoria, extensao } from './mediaKinds.js';

const html = htm.bind(h);

// `media_path` é relativo à raiz da instalação ("statics/outbox/<arquivo>") e o core serve
// esse diretório same-origin em "/statics/outbox/<arquivo>" — dá para renderizar embutido.
// `media_url` é um endereço EXTERNO digitado pelo operador: a CSP do painel
// (img-src/media-src 'self') não deixa carregar de outro host, então ali só resta abrir em
// outra aba. Por isso o `externo` viaja junto — é o que decide a mensagem do fallback.
export function midiaDaMensagem(msg) {
  if (!msg) return null;
  const caminho = String(msg.media_path || '').trim();
  if (caminho) {
    const externo = /^https?:\/\//i.test(caminho);
    const src = externo || caminho.startsWith('/') ? caminho : `/${caminho}`;
    return { src, nome: msg.file_name || caminho.split('/').pop() || caminho, externo };
  }
  const url = String(msg.media_url || '').trim();
  if (!url) return null;
  return { src: url, nome: msg.file_name || url.split('/').pop() || url, externo: true };
}

// Como EXIBIR: pela categoria real do arquivo (extensão), não pelo tipo da mensagem. Um PDF
// mandado como "Documento" abre no leitor do navegador; um .docx não tem player nenhum e
// vira link de download. O tipo da mensagem só entra como último recurso (arquivo sem
// extensão reconhecível, ex.: URL sem sufixo).
// PDF e afins NÃO são embutidos aqui: a CSP do painel manda `frame-ancestors 'none'` em toda
// resposta do servidor, então um <iframe>/<object> apontado para o próprio /statics é
// bloqueado e mostraria um quadro cinza vazio (medido no navegador). Abrir em outra aba é
// navegação de topo e funciona — é o que o cartão de download oferece.
export function formaDeExibir(msg, midia) {
  const nome = (midia && midia.nome) || '';
  const cat = categoria(nome, '');
  if (cat) return cat;
  if (['image', 'audio', 'video'].includes(msg && msg.tipo)) return msg.tipo;
  return extensao(nome) === 'pdf' ? 'pdf' : 'download';
}

const TITULOS = { image: 'Imagem', audio: 'Áudio', video: 'Vídeo', pdf: 'Documento',
  download: 'Arquivo' };

export function MediaPreviewModal({ msg, onClose }) {
  // O arquivo pode não carregar por CSP (host externo), por ter sido apagado do disco da
  // instância (statics/ não é persistente por padrão) ou por formato que o navegador não
  // toca. Em qualquer um dos casos cai no mesmo cartão de fallback, que sempre oferece
  // abrir/baixar em outra aba — a navegação de topo não passa pela CSP do painel.
  const [falhou, setFalhou] = useState(false);
  const midia = midiaDaMensagem(msg);

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  if (!midia) return null;
  const forma = falhou ? 'download' : formaDeExibir(msg, midia);
  const falhar = () => setFalhou(true);

  const corpo = forma === 'image' ? html`
    <img src=${midia.src} alt=${midia.nome} onError=${falhar}
      class="max-h-[70vh] max-w-full mx-auto object-contain rounded" />`
    : forma === 'video' ? html`
    <video src=${midia.src} controls onError=${falhar}
      class="max-h-[70vh] max-w-full mx-auto rounded bg-black"></video>`
    : forma === 'audio' ? html`
    <audio src=${midia.src} controls onError=${falhar} class="w-full"></audio>`
    : html`
    <div class="text-center py-8 space-y-2">
      <p class="text-sm text-wa-text break-all">${midia.nome}</p>
      <p class="text-xs text-wa-secondary">
        ${midia.externo
          ? 'Arquivo em outro endereço — o painel não pode exibi-lo aqui. Abra em outra aba para conferir.'
          : forma === 'pdf'
            ? 'O PDF abre no leitor do navegador em outra aba (o painel não permite exibi-lo embutido).'
            : 'Este formato não tem pré-visualização no navegador. Baixe o arquivo para conferir.'}
      </p>
    </div>`;

  return html`
    <div class="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-4"
      onClick=${onClose}>
      <div class="bg-wa-panel text-wa-text rounded-xl border border-wa-border shadow-lg
        max-w-3xl w-full p-4 space-y-3" onClick=${(e) => e.stopPropagation()}>
        <div class="flex items-center gap-2">
          <div class="min-w-0">
            <h4 class="font-semibold text-sm">${TITULOS[forma] || 'Arquivo'}</h4>
            <p class="text-[11px] text-wa-secondary truncate" title=${midia.src}>${midia.nome}</p>
          </div>
          <div class="ml-auto flex items-center gap-2">
            <a href=${midia.src} target="_blank" rel="noopener noreferrer"
              class="text-xs px-2 py-1 rounded bg-wa-hover text-wa-text hover:bg-wa-border">
              ${midia.externo ? 'Abrir em nova aba' : 'Abrir / baixar'}
            </a>
            <button type="button" onClick=${onClose}
              class="text-xs px-2 py-1 rounded bg-wa-hover text-wa-text hover:bg-wa-border">
              Fechar
            </button>
          </div>
        </div>
        ${corpo}
      </div>
    </div>`;
}
