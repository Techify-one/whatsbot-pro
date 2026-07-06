import { h } from 'preact';
import htm from 'htm';
import { ConfigPanel } from './ConfigPanel.js';

const html = htm.bind(h);

export function Dashboard({ config, saving, onSave, onNotify, onReopenSetup, currentUser }) {
  return html`
    <div class="flex flex-col gap-4">
      <${ConfigPanel}
        config=${config}
        saving=${saving}
        onSave=${onSave}
        onNotify=${onNotify}
        currentUser=${currentUser}
      />

      ${onReopenSetup ? html`
        <div class="flex justify-end">
          <button
            onClick=${onReopenSetup}
            class="px-4 py-2.5 text-[14px] rounded-lg border border-wa-border hover:bg-wa-hover transition-colors flex items-center gap-2 text-wa-text bg-wa-bg"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M19 3h-4.18C14.4 1.84 13.3 1 12 1c-1.3 0-2.4.84-2.82 2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-7 0c.55 0 1 .45 1 1s-.45 1-1 1-1-.45-1-1 .45-1 1-1zm-2 14l-4-4 1.41-1.41L10 14.17l6.59-6.59L18 9l-8 8z"/></svg>
            Refazer configuração
          </button>
        </div>
      ` : null}
    </div>
  `;
}
