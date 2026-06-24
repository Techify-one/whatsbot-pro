import { h } from 'preact';
import { useState, useEffect, useMemo, useCallback } from 'preact/hooks';
import htm from 'htm';
import {
  getConversationTemplates,
  sendConversationTemplate,
  createConversationTemplate,
  deleteConversationTemplate,
  getChannelTemplates,
  sendChannelTemplate,
  createChannelTemplate,
  deleteChannelTemplate,
} from '../../services/api.js';

const html = htm.bind(h);

// ── Template picker (Cloud API, Frente C) ────────────────────────────────────
// Lists the channel's templates (any status) with a category + status badge and,
// on selecting an APPROVED one, builds a dynamic form from the template
// definition: a text input per body {{n}}, a media URL (or text) for the header,
// and inputs for dynamic URL buttons. Shows a live preview, then POSTs the
// assembled `components`.
//
// Management (gated server-side, flags in the list response):
//  - `can_create` → a "Novo template" button (header) opens a create form that
//    POSTs a simple definition; the provider assembles the Graph components.
//  - `can_delete` → a trash action per row deletes the template (all languages).
// Dark-mode-safe: wa-* / .wa-field + the green/amber/red -100/-700 tints covered
// by the html.dark fallback in custom.css.

const MEDIA_FORMATS = ['image', 'video', 'document'];
const LANG_SUGGESTIONS = ['pt_BR', 'en_US', 'en', 'es_ES', 'es'];

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

function isApproved(tpl) {
  return (tpl && (tpl.status || '')).toUpperCase() === 'APPROVED';
}

// Status → {label PT-BR, css}. Crude tints (green/amber/red -100/-700) are
// re-themed by the html.dark fallback in custom.css.
function statusBadge(status) {
  const s = (status || '').toUpperCase();
  if (s === 'APPROVED') return { label: 'Aprovado', cls: 'bg-green-100 text-green-700' };
  if (s === 'PENDING' || s === 'IN_APPEAL' || s === 'PENDING_DELETION')
    return { label: 'Pendente', cls: 'bg-amber-100 text-amber-700' };
  if (s === 'REJECTED') return { label: 'Rejeitado', cls: 'bg-red-100 text-red-700' };
  if (s === 'PAUSED') return { label: 'Pausado', cls: 'bg-red-100 text-red-700' };
  if (s === 'DISABLED') return { label: 'Desabilitado', cls: 'bg-red-100 text-red-700' };
  if (!s) return { label: '—', cls: 'bg-wa-hover text-wa-secondary' };
  return { label: s, cls: 'bg-wa-hover text-wa-secondary' };
}

// `conversationId` → conversa existente (modo padrão). Quando ausente e `channelId`
// + `phone` são passados, opera em modo CHANNEL (plano 21): iniciar conversa nova
// pela "Nova conversa" usa as rotas channel-scoped (ainda não há conversa).
export function TemplatePicker({ conversationId, channelId, phone, onClose, onSent }) {
  const channelMode = conversationId == null && !!channelId;
  const [loading, setLoading] = useState(true);
  const [supported, setSupported] = useState(true);
  const [templates, setTemplates] = useState([]);
  const [canCreate, setCanCreate] = useState(false);
  const [canDelete, setCanDelete] = useState(false);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState(null);
  const [creating, setCreating] = useState(false);

  // Per-selection (send) form state.
  const [bodyVars, setBodyVars] = useState({});       // {n: value}
  const [headerValue, setHeaderValue] = useState('');  // media URL or header text
  const [buttonVars, setButtonVars] = useState({});    // {index: value}
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState('');

  // Delete state.
  const [confirmDelete, setConfirmDelete] = useState(null);  // template name
  const [deletingName, setDeletingName] = useState(null);
  const [deleteError, setDeleteError] = useState('');

  const loadTemplates = useCallback(() => {
    setLoading(true);
    const fetchTemplates = channelMode
      ? getChannelTemplates(channelId)
      : getConversationTemplates(conversationId);
    return fetchTemplates.then(res => {
      if (res && res.ok && res.data) {
        setSupported(!!res.data.supported);
        setTemplates(res.data.templates || []);
        setCanCreate(!!res.data.can_create);
        setCanDelete(!!res.data.can_delete);
        setError('');
      } else {
        setError((res && res.error) || 'Falha ao carregar templates.');
      }
      setLoading(false);
    }).catch(() => { setError('Falha ao carregar templates.'); setLoading(false); });
  }, [conversationId, channelId, channelMode]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    const fetchTemplates = channelMode
      ? getChannelTemplates(channelId)
      : getConversationTemplates(conversationId);
    fetchTemplates.then(res => {
      if (!alive) return;
      if (res && res.ok && res.data) {
        setSupported(!!res.data.supported);
        setTemplates(res.data.templates || []);
        setCanCreate(!!res.data.can_create);
        setCanDelete(!!res.data.can_delete);
      } else {
        setError((res && res.error) || 'Falha ao carregar templates.');
      }
      setLoading(false);
    }).catch(() => { if (alive) { setError('Falha ao carregar templates.'); setLoading(false); } });
    return () => { alive = false; };
  }, [conversationId, channelId, channelMode]);

  function selectTemplate(tpl) {
    setSelected(tpl);
    setBodyVars({});
    setHeaderValue('');
    setButtonVars({});
    setSendError('');
  }

  const selApproved = selected ? isApproved(selected) : false;
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
    if (!selected || sending || !selApproved) return false;
    if (headerIsMedia && !headerValue.trim()) return false;
    if (headerTextVars.length && !headerValue.trim()) return false;
    for (const n of bodyIdxs) if (!(bodyVars[n] || '').trim()) return false;
    for (const b of dynButtons) if (!(buttonVars[b.index] || '').trim()) return false;
    return true;
  }, [selected, sending, selApproved, headerIsMedia, headerTextVars, headerValue, bodyIdxs, bodyVars, dynButtons, buttonVars]);

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
    const payload = {
      template_name: selected.name,
      language: langCode(selected),
      components,
      preview_text: preview,
    };
    const res = channelMode
      ? await sendChannelTemplate(channelId, { phone, ...payload })
      : await sendConversationTemplate(conversationId, payload);
    setSending(false);
    if (res && res.ok) {
      if (onSent) onSent(res.data);
      onClose();
    } else {
      setSendError((res && res.error) || 'Falha ao enviar template.');
    }
  }

  async function performDelete(name) {
    setConfirmDelete(null);
    setDeletingName(name);
    setDeleteError('');
    const res = channelMode
      ? await deleteChannelTemplate(channelId, name)
      : await deleteConversationTemplate(conversationId, name);
    setDeletingName(null);
    if (res && res.ok) {
      await loadTemplates();
    } else {
      setDeleteError((res && res.error) || 'Falha ao apagar template.');
    }
  }

  const filtered = templates.filter(t => {
    if (!search.trim()) return true;
    const q = search.trim().toLowerCase();
    return (t.name || '').toLowerCase().includes(q) || (t.category || '').toLowerCase().includes(q);
  });

  const title = creating ? 'Novo template' : (selected ? selected.name : 'Enviar template');

  return html`
    <div class="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-4" onClick=${onClose}>
      <div class="bg-wa-panel w-full max-w-[480px] max-h-[85vh] rounded-[12px] shadow-2xl flex flex-col overflow-hidden"
        onClick=${(e) => e.stopPropagation()}>
        <!-- Header -->
        <div class="flex items-center justify-between px-4 py-3 border-b border-wa-border shrink-0 gap-2">
          <div class="flex items-center gap-2 min-w-0">
            ${(selected || creating) ? html`
              <button onClick=${() => { setSelected(null); setCreating(false); }} class="text-wa-secondary hover:text-wa-text text-[18px] leading-none shrink-0" title="Voltar">‹</button>
            ` : null}
            <span class="text-wa-text text-[15px] font-medium truncate">${title}</span>
          </div>
          <div class="flex items-center gap-2 shrink-0">
            ${(!loading && supported && !selected && !creating && canCreate) ? html`
              <button onClick=${() => { setCreating(true); setDeleteError(''); }}
                class="px-2.5 py-1 rounded-[8px] text-[13px] text-white bg-wa-teal hover:opacity-90 transition-opacity"
                title="Criar novo template">＋ Novo</button>
            ` : null}
            <button onClick=${onClose} class="text-wa-secondary hover:text-wa-text text-[18px] leading-none" title="Fechar">✕</button>
          </div>
        </div>

        <div class="flex-1 overflow-y-auto wa-scrollbar p-4">
          ${loading ? html`<div class="text-wa-secondary text-[14px] py-8 text-center">Carregando templates…</div>` : null}
          ${error ? html`<div class="text-red-500 text-[13px] mb-2">${error}</div>` : null}

          ${!loading && !supported ? html`
            <div class="text-wa-secondary text-[14px] py-6 text-center">
              Este canal não envia templates. Templates exigem um canal WhatsApp Cloud API com o WABA ID configurado.
            </div>
          ` : null}

          <!-- Create form -->
          ${!loading && supported && creating ? html`
            <${CreateTemplateForm}
              conversationId=${conversationId}
              channelId=${channelId}
              channelMode=${channelMode}
              onCancel=${() => setCreating(false)}
              onCreated=${async () => { setCreating(false); await loadTemplates(); }}
            />
          ` : null}

          <!-- Template list -->
          ${!loading && supported && !selected && !creating ? html`
            <input
              type="text"
              value=${search}
              onInput=${(e) => setSearch(e.target.value)}
              placeholder="Buscar por nome ou categoria…"
              class="wa-field w-full text-[14px] rounded-[8px] px-3 py-2 border border-wa-border outline-none mb-3"
            />
            ${deleteError ? html`<div class="text-red-500 text-[13px] mb-2">${deleteError}</div>` : null}
            ${filtered.length === 0 ? html`
              <div class="text-wa-secondary text-[14px] py-6 text-center">
                ${templates.length === 0 ? 'Nenhum template encontrado. Verifique o WABA ID na configuração do canal.' : 'Nenhum template corresponde à busca.'}
              </div>
            ` : html`
              <div class="flex flex-col gap-2">
                ${filtered.map(t => {
                  const badge = statusBadge(t.status);
                  const approved = isApproved(t);
                  return html`
                  <div key=${(t.name || '') + '|' + langCode(t)}
                    class="flex items-stretch gap-2 bg-wa-bg border border-wa-border rounded-[8px] overflow-hidden hover:border-wa-iconActive transition-colors">
                    <button onClick=${() => selectTemplate(t)}
                      class="flex-1 text-left px-3 py-2.5 min-w-0">
                      <div class="flex items-center gap-2">
                        <span class="text-wa-text text-[14px] font-medium truncate">${t.name}</span>
                        <span class="text-[10px] font-medium px-1.5 py-0.5 rounded-full shrink-0 ${badge.cls}">${badge.label}</span>
                      </div>
                      <div class="text-wa-secondary text-[12px] mt-0.5">
                        ${(t.category || '—')} · ${langCode(t)}${approved ? '' : ' · não enviável'}
                      </div>
                    </button>
                    ${canDelete ? html`
                      <div class="flex items-center pr-2 shrink-0">
                        ${confirmDelete === t.name ? html`
                          <div class="flex items-center gap-1">
                            <button onClick=${() => performDelete(t.name)}
                              class="text-[12px] px-2 py-1 rounded-[6px] text-white bg-red-500 hover:opacity-90">Apagar</button>
                            <button onClick=${() => setConfirmDelete(null)}
                              class="text-[12px] px-2 py-1 rounded-[6px] text-wa-text hover:bg-wa-hover">Não</button>
                          </div>
                        ` : html`
                          <button onClick=${() => { setConfirmDelete(t.name); setDeleteError(''); }}
                            disabled=${deletingName === t.name}
                            class="text-wa-secondary hover:text-red-500 p-1.5 rounded-[6px] hover:bg-wa-hover transition-colors disabled:opacity-50"
                            title="Apagar template">
                            ${deletingName === t.name ? '…' : html`<${TrashIcon} />`}
                          </button>
                        `}
                      </div>
                    ` : null}
                  </div>
                `; })}
              </div>
            `}
          ` : null}

          <!-- Selected template form (send) -->
          ${!loading && supported && selected ? html`
            <div class="space-y-4">
              ${!selApproved ? html`
                <div class="bg-amber-100 text-amber-700 text-[13px] rounded-[8px] px-3 py-2">
                  Este template está "${statusBadge(selected.status).label}". Apenas templates aprovados pela Meta podem ser enviados.
                </div>
              ` : null}

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

        <!-- Footer (send mode) -->
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

// ── Create form (sub-component) ──────────────────────────────────────────────
// Builds a SIMPLE definition (name/category/language + header/body/footer text +
// example values per {{n}}) and POSTs it; the provider assembles the Graph
// components. Media headers / buttons are intentionally out of scope here.
function CreateTemplateForm({ conversationId, channelId, channelMode, onCancel, onCreated }) {
  const [name, setName] = useState('');
  const [category, setCategory] = useState('UTILITY');
  const [language, setLanguage] = useState('pt_BR');
  const [headerText, setHeaderText] = useState('');
  const [bodyText, setBodyText] = useState('');
  const [footerText, setFooterText] = useState('');
  const [headerEx, setHeaderEx] = useState({});  // {n: value}
  const [bodyEx, setBodyEx] = useState({});       // {n: value}
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [okMsg, setOkMsg] = useState('');

  const nameValid = /^[a-z0-9_]+$/.test(name);
  const headerIdxs = useMemo(() => placeholders(headerText), [headerText]);
  const bodyIdxs = useMemo(() => placeholders(bodyText), [bodyText]);

  const canCreate = useMemo(() => {
    if (saving || !nameValid || !bodyText.trim()) return false;
    for (const n of headerIdxs) if (!(headerEx[n] || '').trim()) return false;
    for (const n of bodyIdxs) if (!(bodyEx[n] || '').trim()) return false;
    return true;
  }, [saving, nameValid, bodyText, headerIdxs, headerEx, bodyIdxs, bodyEx]);

  async function submit() {
    if (!canCreate) return;
    setSaving(true);
    setError('');
    setOkMsg('');
    const payload = {
      name: name.trim().toLowerCase(),
      category,
      language: language.trim() || 'pt_BR',
      body_text: bodyText.trim(),
    };
    if (headerText.trim()) payload.header_text = headerText.trim();
    if (footerText.trim()) payload.footer_text = footerText.trim();
    if (bodyIdxs.length) payload.body_examples = bodyIdxs.map(n => (bodyEx[n] || '').trim());
    if (headerIdxs.length) payload.header_examples = headerIdxs.map(n => (headerEx[n] || '').trim());

    const res = channelMode
      ? await createChannelTemplate(channelId, payload)
      : await createConversationTemplate(conversationId, payload);
    setSaving(false);
    if (res && res.ok) {
      setOkMsg('Template enviado para aprovação da Meta.');
      // Brief confirmation, then back to the (refreshed) list.
      setTimeout(() => { if (onCreated) onCreated(); }, 700);
    } else {
      setError((res && res.error) || 'Falha ao criar template.');
    }
  }

  const fieldCls = 'wa-field w-full text-[14px] rounded-[8px] px-3 py-2 border border-wa-border outline-none';

  return html`
    <div class="space-y-4">
      <div>
        <label class="text-wa-secondary text-[12px] font-medium block mb-1">Nome</label>
        <input type="text" value=${name}
          onInput=${(e) => setName(e.target.value.toLowerCase())}
          placeholder="ex: pedido_confirmado"
          class=${fieldCls} />
        <div class="text-[11px] mt-1 ${name && !nameValid ? 'text-red-500' : 'text-wa-secondary'}">
          Apenas letras minúsculas, números e _ (sem espaços ou acentos).
        </div>
      </div>

      <div class="flex gap-3">
        <div class="flex-1">
          <label class="text-wa-secondary text-[12px] font-medium block mb-1">Categoria</label>
          <select value=${category} onChange=${(e) => setCategory(e.target.value)} class=${fieldCls}>
            <option value="UTILITY">Utilidade</option>
            <option value="MARKETING">Marketing</option>
          </select>
        </div>
        <div class="flex-1">
          <label class="text-wa-secondary text-[12px] font-medium block mb-1">Idioma</label>
          <input type="text" value=${language} list="tpl-langs"
            onInput=${(e) => setLanguage(e.target.value)} class=${fieldCls} />
          <datalist id="tpl-langs">
            ${LANG_SUGGESTIONS.map(l => html`<option key=${l} value=${l}></option>`)}
          </datalist>
        </div>
      </div>

      <div>
        <label class="text-wa-secondary text-[12px] font-medium block mb-1">Cabeçalho (texto, opcional)</label>
        <input type="text" value=${headerText}
          onInput=${(e) => setHeaderText(e.target.value)}
          placeholder="ex: Olá, {{1}}!" class=${fieldCls} />
      </div>
      ${headerIdxs.length ? html`
        <div class="space-y-2 pl-2 border-l-2 border-wa-border">
          <div class="text-wa-secondary text-[11px]">Exemplos do cabeçalho (obrigatório para aprovação)</div>
          ${headerIdxs.map(n => html`
            <div key=${'h' + n}>
              <label class="text-wa-secondary text-[12px] block mb-1">Exemplo {{${n}}}</label>
              <input type="text" value=${headerEx[n] || ''}
                onInput=${(e) => setHeaderEx(prev => ({ ...prev, [n]: e.target.value }))}
                class=${fieldCls} />
            </div>
          `)}
        </div>
      ` : null}

      <div>
        <label class="text-wa-secondary text-[12px] font-medium block mb-1">Corpo</label>
        <textarea value=${bodyText}
          onInput=${(e) => setBodyText(e.target.value)}
          rows="4"
          placeholder="ex: Olá {{1}}, seu pedido nº {{2}} foi confirmado!"
          class=${fieldCls}></textarea>
        <div class="text-wa-secondary text-[11px] mt-1">Use {{1}}, {{2}}… para variáveis.</div>
      </div>
      ${bodyIdxs.length ? html`
        <div class="space-y-2 pl-2 border-l-2 border-wa-border">
          <div class="text-wa-secondary text-[11px]">Exemplos do corpo (obrigatório para aprovação)</div>
          ${bodyIdxs.map(n => html`
            <div key=${'b' + n}>
              <label class="text-wa-secondary text-[12px] block mb-1">Exemplo {{${n}}}</label>
              <input type="text" value=${bodyEx[n] || ''}
                onInput=${(e) => setBodyEx(prev => ({ ...prev, [n]: e.target.value }))}
                class=${fieldCls} />
            </div>
          `)}
        </div>
      ` : null}

      <div>
        <label class="text-wa-secondary text-[12px] font-medium block mb-1">Rodapé (opcional)</label>
        <input type="text" value=${footerText}
          onInput=${(e) => setFooterText(e.target.value)}
          placeholder="ex: Equipe Techify" class=${fieldCls} />
      </div>

      ${error ? html`<div class="text-red-500 text-[13px]">${error}</div>` : null}
      ${okMsg ? html`<div class="text-green-700 text-[13px]">${okMsg}</div>` : null}

      <div class="flex justify-end gap-2 pt-1">
        <button onClick=${onCancel}
          class="px-3 py-2 rounded-[8px] text-[14px] text-wa-text hover:bg-wa-hover transition-colors">Cancelar</button>
        <button onClick=${submit} disabled=${!canCreate}
          class="px-4 py-2 rounded-[8px] text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50">
          ${saving ? 'Criando…' : 'Criar template'}
        </button>
      </div>
    </div>
  `;
}

function TrashIcon() {
  return html`
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <polyline points="3 6 5 6 21 6"></polyline>
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"></path>
      <path d="M10 11v6"></path><path d="M14 11v6"></path>
      <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"></path>
    </svg>
  `;
}

export default TemplatePicker;
