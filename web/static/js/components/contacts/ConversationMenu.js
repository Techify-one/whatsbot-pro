// Menu (⋮) de ações da conversa — canto direito do cabeçalho do chat.
//
// Junta num lugar só o que antes disputava espaço na barra do cabeçalho: as ações
// que os PLUGINS injetam (slot `conversation.header.actions` — "Jornada",
// "Agendar", …) e as ações do core que não são de uso constante, como abrir as
// informações do atendimento. O botão "Resolver" continua FORA daqui de propósito:
// é a ação principal do atendente e tem de custar um clique só.
//
// O slot é o MESMO de antes — nenhum plugin muda, nenhum nome novo entra no
// contrato; mudou apenas ONDE ele é pintado. Cada item de plugin vira uma linha do
// menu por CSS (`.wa-conv-menu-item` em custom.css), porque o markup do botão é do
// plugin e o core não edita plugin.

import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import { Slot } from '../../plugins/Slot.js';
import { getSlot, subscribe } from '../../plugins/registry.js';

const html = htm.bind(h);

const SLOT = 'conversation.header.actions';

// `items`: [{ key, label, icon?, onClick }] — ações do core.
// `slotCtx`: ctx passado aos itens de plugin; `null` não renderiza o slot.
// Sem item nenhum (nem core nem plugin) o botão não aparece — um menu vazio seria
// pior que menu nenhum.
export function ConversationMenu({ items = [], slotCtx = null }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  // O registry é preenchido de forma assíncrona no boot (loadPluginExtensions), e
  // esvaziado/repovoado a cada toggle de plugin: sem assinar, a contagem abaixo
  // ficaria congelada no valor do primeiro render.
  const [, force] = useState(0);
  useEffect(() => subscribe(() => force(n => n + 1)), []);

  useEffect(() => {
    if (!open) return;
    function onDown(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    }
    function onKey(e) { if (e.key === 'Escape') setOpen(false); }
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const coreItems = (items || []).filter(Boolean);
  const slotCount = slotCtx ? getSlot(SLOT).length : 0;
  if (!coreItems.length && !slotCount) return null;

  return html`
    <div ref=${rootRef} class="relative shrink-0 ml-1">
      <button
        type="button"
        onClick=${() => setOpen(o => !o)}
        aria-haspopup="menu"
        aria-expanded=${open}
        title="Mais opções"
        class="text-wa-icon hover:text-wa-text p-[6px] rounded-full hover:bg-wa-hover transition-colors ${open ? 'bg-wa-hover text-wa-text' : ''}"
      >
        <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor">
          <path d="M12 8c1.1 0 2-.9 2-2s-.9-2-2-2-2 .9-2 2 .9 2 2 2zm0 2c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2zm0 6c-1.1 0-2 .9-2 2s.9 2 2 2 2-.9 2-2-.9-2-2-2z"/>
        </svg>
      </button>
      ${open ? html`
        <div
          role="menu"
          class="absolute right-0 mt-1 z-50 bg-wa-bg rounded-lg shadow-lg border border-wa-border py-1 min-w-[220px] max-h-[70vh] overflow-y-auto"
        >
          ${coreItems.map(it => html`
            <button
              key=${it.key}
              type="button"
              role="menuitem"
              onClick=${() => { setOpen(false); it.onClick(); }}
              class="w-full text-left px-4 py-2.5 text-[14px] text-wa-text hover:bg-wa-hover transition-colors flex items-center gap-2"
            >
              ${it.icon || null}<span class="truncate">${it.label}</span>
            </button>
          `)}
          ${slotCount ? html`
            <!-- O clique fecha o menu na SUBIDA do evento — o handler do plugin
                 (fase de bolha, no próprio botão) já rodou quando chega aqui. -->
            <div class="wa-conv-menu-item" onClick=${() => setOpen(false)}>
              <${Slot} name=${SLOT} ctx=${slotCtx} />
            </div>
          ` : null}
        </div>
      ` : null}
    </div>
  `;
}
