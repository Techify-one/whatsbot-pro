// Tools management screen — tabela com busca, toggle inline e edição via modal.
// Refresh imediato no backend (sem restart), atualiza via WebSocket.

import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

export function EditModal({ tool, onClose, onSave, busy }) {
  const [description, setDescription] = useState(tool.current_description || '');
  const [label, setLabel] = useState(tool.current_label || '');

  const dirty =
    description.trim() !== (tool.current_description || '').trim() ||
    label.trim() !== (tool.current_label || '').trim();

  function save() {
    const body = {};
    if (description.trim() !== (tool.current_description || '').trim()) {
      if (!description.trim() || description.trim() === (tool.default_description || '').trim()) {
        body.description = null;
      } else {
        body.description = description.trim();
      }
    }
    if (label.trim() !== (tool.current_label || '').trim()) {
      const defaultLabel = (tool.default_label || '').trim();
      if (!label.trim() || label.trim() === defaultLabel) {
        body.display_label = null;
      } else {
        body.display_label = label.trim();
      }
    }
    onSave(tool.name, body);
  }

  function reset() {
    onSave(tool.name, { description: null, display_label: null });
  }

  const anyOverride = tool.has_override || tool.has_label_override;

  return html`
    <div class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center" onClick=${onClose}>
      <div class="bg-wa-bg rounded-lg shadow-xl max-w-lg w-full mx-4 max-h-[85vh] overflow-y-auto"
           onClick=${(e) => e.stopPropagation()}>
        <div class="border-b border-wa-border px-4 py-3 flex items-center justify-between">
          <div>
            <div class="font-medium">Editar tool</div>
            <code class="text-[12px] text-wa-secondary">${tool.name}</code>
          </div>
          <button class="text-wa-secondary hover:text-wa-text text-xl leading-none" onClick=${onClose}>×</button>
        </div>
        <div class="p-4 space-y-4">
          <div>
            <label class="text-[12px] text-wa-secondary block mb-1">
              Rótulo (visível só na UI)
              ${tool.has_label_override ? html`<span class="text-[11px] text-blue-700"> · sobrescrito</span>` : null}
            </label>
            <input
              type="text"
              value=${label}
              onInput=${(e) => setLabel(e.target.value)}
              placeholder=${tool.default_label || tool.name}
              class="w-full wa-field text-[13px] border border-wa-border rounded px-2 py-1.5 focus:outline-none focus:border-wa-teal"
            />
            ${tool.has_label_override && tool.default_label ? html`
              <div class="text-[11px] text-wa-secondary mt-1">
                Padrão: <span class="italic">${tool.default_label}</span>
              </div>
            ` : null}
          </div>
          <div>
            <label class="text-[12px] text-wa-secondary block mb-1">
              Descrição enviada ao LLM
              ${tool.has_override ? html`<span class="text-[11px] text-blue-700"> · sobrescrita</span>` : null}
            </label>
            <textarea
              rows="6"
              value=${description}
              onInput=${(e) => setDescription(e.target.value)}
              placeholder=${tool.default_description}
              class="w-full wa-field text-[13px] border border-wa-border rounded px-2 py-1.5 focus:outline-none focus:border-wa-teal resize-y"
            />
            ${tool.has_override ? html`
              <div class="text-[11px] text-wa-secondary mt-1">
                Padrão: <span class="italic">${tool.default_description}</span>
              </div>
            ` : null}
          </div>
        </div>
        <div class="border-t border-wa-border px-4 py-3 flex items-center justify-between">
          <div>
            ${anyOverride ? html`
              <button
                onClick=${reset}
                disabled=${busy}
                class="px-3 py-1 text-[13px] rounded bg-wa-panel border border-wa-border disabled:opacity-50"
              >Restaurar padrão</button>
            ` : null}
          </div>
          <div class="flex gap-2">
            <button
              onClick=${onClose}
              class="px-3 py-1 text-[13px] rounded bg-wa-panel border border-wa-border"
            >Cancelar</button>
            <button
              onClick=${save}
              disabled=${!dirty || busy}
              class="px-3 py-1 text-[13px] rounded bg-wa-teal text-white disabled:opacity-50"
            >${busy ? 'Salvando…' : 'Salvar'}</button>
          </div>
        </div>
      </div>
    </div>
  `;
}
