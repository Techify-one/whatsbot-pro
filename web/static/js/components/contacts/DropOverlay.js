import { h } from 'preact';
import htm from 'htm';

const html = htm.bind(h);

// Overlay de arrastar-e-soltar da conversa (plano 64 · F6) — duas metades
// estilo Telegram: soltar em cima manda como foto/vídeo (inline), embaixo como
// arquivo (documento, original). A metade sob o cursor fica realçada.
//
// Renderizado como ÚLTIMO filho da raiz do painel (que é `relative`), cobrindo
// só a conversa — nunca a sidebar nem os painéis de informação.
//
// `pointer-events-none`: o overlay é puramente visual. Quem trata dragover/drop
// é a raiz do painel (useDropZone); se o overlay capturasse o ponteiro, entrar
// nele dispararia um `dragleave` na raiz e o overlay piscaria.

function Half({ active, icon, title, subtitle, first }) {
  return html`
    <div class="flex-1 flex flex-col items-center justify-center gap-[10px] px-[24px] text-center transition-colors
                ${first ? 'border-b' : ''} border-dashed border-white/30
                ${active ? 'bg-wa-teal/25' : ''}">
      <div class="w-[54px] h-[54px] rounded-full flex items-center justify-center text-[26px]
                  ${active ? 'bg-wa-teal text-white' : 'bg-white/15 text-white'}">
        ${icon}
      </div>
      <div class="text-white text-[17px] font-medium leading-tight">${title}</div>
      <div class="text-white/70 text-[13px] leading-tight max-w-[280px]">${subtitle}</div>
    </div>
  `;
}

export function DropOverlay({ zone }) {
  return html`
    <div class="absolute inset-0 z-[60] pointer-events-none flex flex-col
                bg-black/70 backdrop-blur-[2px] border-[3px] border-dashed border-wa-teal rounded-[4px]">
      <${Half}
        first=${true}
        active=${zone === 'media'}
        icon="🖼️"
        title="Foto ou vídeo"
        subtitle="Enviado na conversa, com prévia. Outros tipos de arquivo vão como documento." />
      <${Half}
        active=${zone === 'file'}
        icon="📎"
        title="Arquivo"
        subtitle="Enviado como documento, no formato original." />
    </div>
  `;
}
