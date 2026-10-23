import { h } from 'preact';
import { useState, useRef } from 'preact/hooks';
import htm from 'htm';
import {
  contextState, previewOf, sizeLabel, legacyNotice,
} from '../../services/executionContext.js';

const html = htm.bind(h);

// Uma mensagem do step `llm_context` (plano 182). O system prompt chega inteiro,
// então a tela não o despeja de uma vez: começa MINIMIZADO (prévia curta) e o botão
// Expandir/Minimizar fica no cabeçalho, no lugar da antiga etiqueta "· truncado".
//
// Três casos, decididos por `contextState` (módulo puro, testado com node --test):
//   curto    → texto inteiro, sem controle;
//   completo → botão Expandir/Minimizar + painel rolável com Copiar / Ir ao fim;
//   legado   → execução gravada antes do plano 182: só o aviso, sem botão.
//
// O estado é local e NÃO segue o "Expandir tudo" do passo: abrir 36 mil caracteres
// por salto sozinho é exatamente o que o botão existe para evitar.
// stopPropagation em tudo que é clicável — o step inteiro é clicável (foca o passo e
// põe ?step= na URL).

const stop = (fn) => (e) => { e.stopPropagation(); fn(e); };

export function PromptViewer({ msg }) {
  const state = contextState(msg);
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const boxRef = useRef(null);
  const content = msg && msg.content != null ? String(msg.content) : '';

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (_) { /* clipboard bloqueado: o texto continua selecionável */ }
  };
  const toEnd = () => {
    const el = boxRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  };

  const role = html`<span>${msg.role}</span>`;

  if (state === 'legado') {
    return html`
      <div>
        <div class="text-xs font-medium text-wa-secondary mb-0.5 flex flex-wrap items-center gap-x-1.5">
          ${role}<span class="opacity-70">· truncado</span>
          <span class="opacity-70 font-normal">(${legacyNotice(msg)})</span>
        </div>
        <pre class="text-xs text-wa-text whitespace-pre-wrap break-words">${content}</pre>
      </div>
    `;
  }

  if (state === 'curto') {
    return html`
      <div>
        <div class="text-xs font-medium text-wa-secondary mb-0.5">${role}</div>
        <pre class="text-xs text-wa-text whitespace-pre-wrap break-words">${content}</pre>
      </div>
    `;
  }

  const preview = previewOf(content);
  return html`
    <div class="min-w-0">
      <div class="text-xs font-medium text-wa-secondary mb-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
        ${role}
        <button
          type="button"
          aria-expanded=${open}
          onClick=${stop(() => setOpen(o => !o))}
          class="font-normal text-wa-teal hover:underline select-none flex items-center gap-1"
          title=${open ? 'Minimizar o prompt' : 'Ver o prompt completo'}
        >
          <span class="inline-block w-3">${open ? '▾' : '▸'}</span>
          <span>${open ? 'Minimizar' : 'Expandir'}</span>
        </button>
        <span class="opacity-70 font-normal">${sizeLabel(msg)}</span>
        ${msg.scrubbed ? html`<span class="opacity-70 font-normal" title="Blobs base64 foram trocados por [base64 removido] antes de gravar.">· base64 removido</span>` : null}
      </div>
      ${open ? html`
        <div class="flex items-center gap-2 mb-1 text-xs">
          <button type="button" onClick=${stop(copy)}
            class="px-2 py-0.5 rounded border border-wa-border text-wa-text hover:bg-wa-hover">
            ${copied ? 'Copiado ✓' : 'Copiar'}
          </button>
          <button type="button" onClick=${stop(toEnd)}
            class="px-2 py-0.5 rounded border border-wa-border text-wa-text hover:bg-wa-hover">
            Ir ao fim
          </button>
        </div>
        <pre ref=${boxRef}
          class="text-xs text-wa-text bg-wa-bg border border-wa-border rounded p-2 whitespace-pre-wrap break-words overflow-auto max-h-[28rem]"
          onClick=${(e) => e.stopPropagation()}>${content}</pre>
      ` : html`
        <pre class="text-xs text-wa-text whitespace-pre-wrap break-words">${preview.text}${preview.cut ? '\n…' : ''}</pre>
      `}
    </div>
  `;
}
