import { h } from 'preact';
import { useState, useEffect, useMemo } from 'preact/hooks';
import htm from 'htm';
import { getConversationTemplates, sendConversationTemplate } from '../../services/api.js';

const html = htm.bind(h);

// ── Template picker (Cloud API, Frente C) ────────────────────────────────────
// Lists approved templates for the conversation's channel and, on selection,
// builds a dynamic form from the template definition: a text input per body
// {{n}}, a media URL (or text) for the header, and inputs for dynamic URL
// buttons. Shows a live preview, then POSTs the assembled `components`.
// Dark-mode-safe: wa-* / .wa-field only.

const MEDIA_FORMATS = ['image', 'video', 'document'];

// Unique, ascending {{n}} indices found in a string.
function placeholders(text) {
  const found = new Set();
  const re = /\{\{(\d+)\}\}/g;
  let m;
  while ((m = re.exec(text || ''))) found.add(parseInt(m[1], 10));
  return [...found].sort((a, b) => a - b);
}

function findComponent(tpl, type) {
  return (tpl.components || []).find(c => (c.type || '').toLowerCase() === type) || null;
}

function langCode(tpl) {
  if (!tpl) return 'pt_BR';
  if (typeof tpl.language === 'string') return tpl.language;
  return (tpl.language && tpl.language.code) || 'pt_BR';
}

export function TemplatePicker({ conversationId, onClose, onSent }) {
  const [loading, setLoading] = useState(true);
  const [supported, setSupported] = useState(true);
  const [templates, setTemplates] = useState([]);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState(null);

  // Per-selection form state.
  const [bodyVars, setBodyVars] = useState({});       // {n: value}
  const [headerValue, setHeaderValue] = useState('');  // media URL or header text
  const [buttonVars, setButtonVars] = useState({});    // {index: value}
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState('');

  useEffect(() => {
    let alive = true;
    setLoading(true);
    getConversationTemplates(conversationId).then(res => {
      if (!alive) return;
      if (res && res.ok && res.data) {
        setSupported(!!res.data.supported);
        setTemplates(res.data.templates || []);
      } else {
        setError((res && res.error) || 'Falha ao carregar templates.');
      }
      setLoading(false);
    }).catch(() => { if (alive) { setError('Falha ao carregar templates.'); setLoading(false); } });
    return () => { alive = false; };
  }, [conversationId]);

  function selectTemplate(tpl) {
    setSelected(tpl);
    setBodyVars({});
    setHeaderValue('');
    setButtonVars({});
    setSendError('');
  }

  const header = selected ? findComponent(selected, 'header') : null;
  const body = selected ? findComponent(selected, 'body') : null;
  const buttonsComp = selected ? findComponent(selected, 'buttons') : null;
  const headerFormat = header ? (header.format || 'text').toLowerCase() : null;
  const headerIsMedia = !!headerFormat && MEDIA_FORMATS.includes(headerFormat);
  const headerTextVars = (header && headerFormat === 'text') ? placeholders(header.text) : [];
  const bodyIdxs = body ? placeholders(body.text) : [];
  // Dynamic URL buttons (a {{1}} suffix in the url) need a value at send time.
  const dynButtons = useMemo(() => {
    const out = [];
    const btns = (buttonsComp && buttonsComp.buttons) || [];
    btns.forEach((b, i) => {
      const t = (b.type || '').toLowerCase();
      if (t === 'url' && /\{\{\d+\}\}/.test(b.url || '')) out.push({ index: i, text: b.text });
    });
    return out;
  }, [buttonsComp]);

  // Live preview of the body with substituted values.
  const preview = useMemo(() => {
    if (!body) return '';
    return (body.text || '').replace(/\{\{(\d+)\}\}/g, (_, n) => bodyVars[n] || `{{${n}}}`);
  }, [body, bodyVars]);

  const canSend = useMemo(() => {
    if (!selected || sending) return false;
    if (headerIsMedia && !headerValue.trim()) return false;
    if (headerTextVars.length && !headerValue.trim()) return false;
    for (const n of bodyIdxs) if (!(bodyVars[n] || '').trim()) return false;
    for (const b of dynButtons) if (!(buttonVars[b.index] || '').trim()) return false;
    return true;
  }, [selected, sending, headerIsMedia, headerTextVars, headerValue, bodyIdxs, bodyVars, dynButtons, buttonVars]);

  function buildComponents() {
    const components = [];
    if (header) {
      if (headerIsMedia && headerValue.trim()) {
        components.push({
          type: 'header',
          parameters: [{ type: headerFormat, [headerFormat]: { link: headerValue.trim() } }],
        });
      } else if (headerTextVars.length) {
        components.push({ type: 'header', parameters: [{ type: 'text', text: headerValue.trim() }] });
      }
    }
    if (bodyIdxs.length) {
      components.push({
        type: 'body',
        parameters: bodyIdxs.map(n => ({ type: 'text', text: (bodyVars[n] || '').trim() })),
      });
    }
    for (const b of dynButtons) {
      components.push({
        type: 'button', sub_type: 'url', index: String(b.index),
        parameters: [{ type: 'text', text: (buttonVars[b.index] || '').trim() }],
      });
    }
    return components;
  }

  async function handleSend() {
    if (!canSend) return;
    setSending(true);
    setSendError('');
    const components = buildComponents();
    const res = await sendConversationTemplate(conversationId, {
      template_name: selected.name,
      language: langCode(selected),
      components,
      preview_text: preview,
    });
    setSending(false);
    if (res && res.ok) {
      if (onSent) onSent(res.data);
      onClose();
    } else {
      setSendError((res && res.error) || 'Falha ao enviar template.');
    }
  }

  const filtered = templates.filter(t => {
    if (!search.trim()) return true;
    const q = search.trim().toLowerCase();
    return (t.name || '').toLowerCase().includes(q) || (t.category || '').toLowerCase().includes(q);
  });

  return html`
    <div class="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4" onClick=${onClose}>
      <div class="bg-wa-panel w-full max-w-[480px] max-h-[85vh] rounded-[12px] shadow-2xl flex flex-col overflow-hidden"
        onClick=${(e) => e.stopPropagation()}>
        <!-- Header -->
        <div class="flex items-center justify-between px-4 py-3 border-b border-wa-border shrink-0">
          <div class="flex items-center gap-2 min-w-0">
            ${selected ? html`
              <button onClick=${() => setSelected(null)} class="text-wa-secondary hover:text-wa-text text-[18px] leading-none shrink-0" title="Voltar">‹</button>
            ` : null}
            <span class="text-wa-text text-[15px] font-medium truncate">
              ${selected ? selected.name : 'Enviar template'}
            </span>
          </div>
          <button onClick=${onClose} class="text-wa-secondary hover:text-wa-text text-[18px] leading-none shrink-0" title="Fechar">✕</button>
        </div>

        <div class="flex-1 overflow-y-auto wa-scrollbar p-4">
          ${loading ? html`<div class="text-wa-secondary text-[14px] py-8 text-center">Carregando templates…</div>` : null}
          ${error ? html`<div class="text-red-500 text-[13px] mb-2">${error}</div>` : null}

          ${!loading && !supported ? html`
            <div class="text-wa-secondary text-[14px] py-6 text-center">
              Este canal não envia templates. Templates exigem um canal WhatsApp Cloud API com o WABA ID configurado.
            </div>
          ` : null}

          <!-- Template list -->
          ${!loading && supported && !selected ? html`
            <input
              type="text"
              value=${search}
              onInput=${(e) => setSearch(e.target.value)}
              placeholder="Buscar por nome ou categoria…"
              class="wa-field w-full text-[14px] rounded-[8px] px-3 py-2 border border-wa-border outline-none mb-3"
            />
            ${filtered.length === 0 ? html`
              <div class="text-wa-secondary text-[14px] py-6 text-center">
                ${templates.length === 0 ? 'Nenhum template aprovado encontrado. Verifique o WABA ID e os templates aprovados na Meta.' : 'Nenhum template corresponde à busca.'}
              </div>
            ` : html`
              <div class="flex flex-col gap-2">
                ${filtered.map(t => html`
                  <button key=${t.name + (langCode(t))} onClick=${() => selectTemplate(t)}
                    class="text-left bg-wa-bg border border-wa-border rounded-[8px] px-3 py-2.5 hover:border-wa-iconActive transition-colors">
                    <div class="text-wa-text text-[14px] font-medium">${t.name}</div>
                    <div class="text-wa-secondary text-[12px] mt-0.5">
                      ${(t.category || '—')} · ${langCode(t)}
                    </div>
                  </button>
                `)}
              </div>
            `}
          ` : null}

          <!-- Selected template form -->
          ${!loading && supported && selected ? html`
            <div class="space-y-4">
              ${header && (headerIsMedia || headerTextVars.length) ? html`
                <div>
                  <label class="text-wa-secondary text-[12px] font-medium block mb-1">
                    ${headerIsMedia ? `Cabeçalho — URL de ${headerFormat} (link público)` : 'Cabeçalho (texto)'}
                  </label>
                  <input type=${headerIsMedia ? 'url' : 'text'} value=${headerValue}
                    onInput=${(e) => setHeaderValue(e.target.value)}
                    placeholder=${headerIsMedia ? 'https://…' : 'Valor do cabeçalho'}
                    class="wa-field w-full text-[14px] rounded-[8px] px-3 py-2 border border-wa-border outline-none" />
                  ${headerIsMedia ? html`<div class="text-wa-secondary text-[11px] mt-1">A URL precisa ser pública e acessível pela Meta.</div>` : null}
                </div>
              ` : null}

              ${bodyIdxs.length ? html`
                <div class="space-y-3">
                  <div class="text-wa-secondary text-[12px] font-medium">Variáveis do corpo</div>
                  ${bodyIdxs.map(n => html`
                    <div key=${n}>
                      <label class="text-wa-secondary text-[12px] block mb-1">Variável {{${n}}}</label>
                      <input type="text" value=${bodyVars[n] || ''}
                        onInput=${(e) => setBodyVars(prev => ({ ...prev, [n]: e.target.value }))}
                        class="wa-field w-full text-[14px] rounded-[8px] px-3 py-2 border border-wa-border outline-none" />
                    </div>
                  `)}
                </div>
              ` : null}

              ${dynButtons.length ? html`
                <div class="space-y-3">
                  <div class="text-wa-secondary text-[12px] font-medium">Botões dinâmicos</div>
                  ${dynButtons.map(b => html`
                    <div key=${b.index}>
                      <label class="text-wa-secondary text-[12px] block mb-1">Botão "${b.text || ('#' + b.index)}" — valor da URL</label>
                      <input type="text" value=${buttonVars[b.index] || ''}
                        onInput=${(e) => setButtonVars(prev => ({ ...prev, [b.index]: e.target.value }))}
                        class="wa-field w-full text-[14px] rounded-[8px] px-3 py-2 border border-wa-border outline-none" />
                    </div>
                  `)}
                </div>
              ` : null}

              ${body ? html`
                <div>
                  <div class="text-wa-secondary text-[12px] font-medium mb-1">Prévia</div>
                  <div class="bg-wa-bg border border-wa-border rounded-[8px] px-3 py-2 text-wa-text text-[14px] whitespace-pre-wrap">${preview}</div>
                </div>
              ` : null}

              ${sendError ? html`<div class="text-red-500 text-[13px]">${sendError}</div>` : null}
            </div>
          ` : null}
        </div>

        <!-- Footer -->
        ${!loading && supported && selected ? html`
          <div class="px-4 py-3 border-t border-wa-border shrink-0 flex justify-end gap-2">
            <button onClick=${() => setSelected(null)}
              class="px-3 py-2 rounded-[8px] text-[14px] text-wa-text hover:bg-wa-hover transition-colors">Voltar</button>
            <button onClick=${handleSend} disabled=${!canSend}
              class="px-4 py-2 rounded-[8px] text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50">
              ${sending ? 'Enviando…' : 'Enviar'}
            </button>
          </div>
        ` : null}
      </div>
    </div>
  `;
}

export default TemplatePicker;
