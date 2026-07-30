import { h } from 'preact';
import { useState, useEffect, useMemo, useCallback } from 'preact/hooks';
import htm from 'htm';
import { formatPhoneDisplay } from './phone.js';
import { applyView, contarPorAba } from './templateFilter.js';

const html = htm.bind(h);

// ── Modal "Enviar template" — a tela É DO PROVIDER (plano 92 · C1) ───────────
//
// Este arquivo era do core (`web/static/js/components/contacts/TemplatePicker.js`)
// e mudou de dono: a FORMA de um template — categorias UTILITY/MARKETING, os
// `{{n}}`, os 4 tipos de botão, o handle de upload, o ciclo de aprovação — é
// vocabulário da Graph API da Meta, não do WhatsBot. O core mantém apenas um
// ponto de extensão exclusivo (`template.picker`, plugins/registry.js) e um host
// que o resolve; quem reivindica é o `extends.js` deste plugin.
//
// CONTRATO DE PROPS (fixado pelo host do core — não mudar sem mudar o host):
//   {conversationId, channelId, phone, onClose, onSent}
// mais `svc`, injetado pelo wrapper do `extends.js`. `svc` é a allowlist curada
// `api.services`: o plugin NUNCA importa `services/api.js` do core (o caminho
// relativo resolveria para dentro de /plugins/... e daria 404). Os dois uploads
// TÊM de sair por `svc` — `api.http` é JSON-only e transformaria o FormData em
// "{}", com o FastAPI devolvendo 422.
//
// DOIS MODOS, ambos obrigatórios:
//   • conversa existente (`conversationId`) — rotas conv-scoped;
//   • canal + telefone (`channelId` + `phone`, `channelMode`) — "Novo
//     atendimento", quando ainda não há conversa.
//
// O que a tela faz: lista os templates do canal (qualquer status, com selo), e
// ao escolher um APROVADO monta um formulário a partir da definição — um campo
// por `{{n}}` do corpo, URL de mídia (ou texto) para o cabeçalho, e um campo por
// botão de URL dinâmico. Mostra prévia ao vivo e envia os `components` montados.
//
// Gestão (autorizada NO SERVIDOR; os flags só decidem o que aparece):
//  - `can_create` → botão "Novo template" abre o formulário de criação;
//  - `can_delete` → lixeira por linha apaga o template (todos os idiomas).
//
// Modo escuro: usa as classes semânticas `wa-*` / `.wa-field` do core, que são
// globais da SPA e valem igual numa tela de plugin; os tints crus (green/amber/
// red -100/-700) são cobertos pelo fallback `html.dark` de `custom.css`. NÃO
// introduza cor fora dessa lista (hex inline, `bg-*-300`) — fica ilegível.
//
// IDs de DOM são prefixados com `wac-` de propósito: enquanto o fallback do core
// existir, os dois modais podem coexistir no documento, e `datalist`/`radio`
// casam por id/name GLOBAIS — sem o prefixo, mexer num desmarcaria o outro.

const MEDIA_FORMATS = ['image', 'video', 'document'];
const LANG_SUGGESTIONS = ['pt_BR', 'en_US', 'en', 'es_ES', 'es'];

// ── Criação: cabeçalho de mídia + botões (plano 73) ──────────────────────────
// Espelha as regras do servidor (app/services/template_service.py) só para dar
// feedback imediato — a Meta continua sendo a validação final.
const MEDIA_HEADER_KINDS = ['IMAGE', 'VIDEO', 'DOCUMENT'];
const HEADER_KIND_OPTIONS = [
  { value: 'none', label: 'Nenhum' },
  { value: 'TEXT', label: 'Texto' },
  { value: 'IMAGE', label: 'Imagem' },
  { value: 'VIDEO', label: 'Vídeo' },
  { value: 'DOCUMENT', label: 'Documento' },
];
const MEDIA_ACCEPT = {
  IMAGE: 'image/jpeg,image/png',
  VIDEO: 'video/mp4,video/3gpp',
  DOCUMENT: '.pdf,.doc,.docx,.xls,.xlsx',
};
const BUTTON_TYPE_OPTIONS = [
  { value: 'QUICK_REPLY', label: 'Resposta rápida' },
  { value: 'URL', label: 'Link (URL)' },
  { value: 'PHONE_NUMBER', label: 'Telefone' },
  { value: 'COPY_CODE', label: 'Copiar código' },
];
const BUTTON_TEXT_MAX = 25;
const BUTTONS_MAX = 10;
const BUTTON_TYPE_MAX = { URL: 2, PHONE_NUMBER: 1, COPY_CODE: 1 };

// `null` quando a lista é válida; senão a mensagem PT-BR do primeiro problema.
function validateButtons(buttons) {
  if (!buttons || !buttons.length) return null;
  if (buttons.length > BUTTONS_MAX) return `No máximo ${BUTTONS_MAX} botões.`;
  const counts = {};
  for (let i = 0; i < buttons.length; i++) {
    const b = buttons[i];
    const n = i + 1;
    counts[b.type] = (counts[b.type] || 0) + 1;
    const limit = BUTTON_TYPE_MAX[b.type];
    if (limit && counts[b.type] > limit) {
      const label = (BUTTON_TYPE_OPTIONS.find(o => o.value === b.type) || {}).label || b.type;
      return `No máximo ${limit} botão(ões) do tipo "${label}".`;
    }
    const text = (b.text || '').trim();
    if (b.type !== 'COPY_CODE') {
      if (!text) return `Botão ${n}: informe o texto.`;
      if (text.length > BUTTON_TEXT_MAX) return `Botão ${n}: texto até ${BUTTON_TEXT_MAX} caracteres.`;
    }
    if (b.type === 'URL') {
      const url = (b.url || '').trim();
      if (!url) return `Botão ${n}: informe a URL.`;
      if (url.includes('{{') && !(b.example || '').trim()) {
        return `Botão ${n}: URL com variável exige um exemplo.`;
      }
    }
    if (b.type === 'PHONE_NUMBER' && !(b.phone_number || '').trim()) {
      return `Botão ${n}: informe o telefone.`;
    }
    if (b.type === 'COPY_CODE' && !(b.example || '').trim()) {
      return `Botão ${n}: informe o código de exemplo.`;
    }
  }
  return null;
}

// Só as chaves que o tipo usa (nada extra chega ao servidor).
function buildButtonPayload(b) {
  const text = (b.text || '').trim();
  if (b.type === 'URL') {
    const out = { type: 'URL', text, url: (b.url || '').trim() };
    if ((b.example || '').trim()) out.example = (b.example || '').trim();
    return out;
  }
  if (b.type === 'PHONE_NUMBER') {
    return { type: 'PHONE_NUMBER', text, phone_number: (b.phone_number || '').trim() };
  }
  if (b.type === 'COPY_CODE') {
    return { type: 'COPY_CODE', example: (b.example || '').trim() };
  }
  return { type: 'QUICK_REPLY', text };
}

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

// `conversationId` → atendimento existente (modo padrão). Quando ausente e `channelId`
// + `phone` são passados, opera em modo CHANNEL (plano 21): iniciar atendimento novo
// pela "Novo atendimento" usa as rotas channel-scoped (ainda não há atendimento).
export function TemplatePicker({ conversationId, channelId, phone, onClose, onSent, svc, http }) {
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
  // Remetente do template: `{id, name, phone}` do canal desta conversa. Exibido
  // no cabeçalho do modal (lista / envio / criação) para o atendente saber de
  // qual número oficial o template sai.
  const [channelInfo, setChannelInfo] = useState(null);

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

  // ── Preferências (plano 92 · E1/E2) ───────────────────────────────────────
  // Favorito é PESSOAL, arquivado é GLOBAL. Vêm de uma rota do próprio plugin
  // (`/api/plugins/whatsapp_cloud/template-prefs`), separada da lista: a lista
  // tem cache de 5 min no provider e é igual para todo mundo, a marcação não.
  const [favorites, setFavorites] = useState(() => new Set());
  const [archived, setArchived] = useState(() => new Set());
  const [canArchive, setCanArchive] = useState(false);
  const [canFavorite, setCanFavorite] = useState(false);
  const [tab, setTab] = useState('todas');
  const [prefBusy, setPrefBusy] = useState(null);   // nome em trânsito
  const [prefError, setPrefError] = useState('');

  // O canal em modo conversa só é conhecido DEPOIS da lista (vem no payload como
  // `channel`); em modo canal já chega por prop.
  const prefsChannelId = channelId || (channelInfo && channelInfo.id) || '';

  const loadTemplates = useCallback(() => {
    setLoading(true);
    const fetchTemplates = channelMode
      ? svc.getChannelTemplates(channelId)
      : svc.getConversationTemplates(conversationId);
    return fetchTemplates.then(res => {
      if (res && res.ok && res.data) {
        setSupported(!!res.data.supported);
        setTemplates(res.data.templates || []);
        setCanCreate(!!res.data.can_create);
        setCanDelete(!!res.data.can_delete);
        setChannelInfo(res.data.channel || null);
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
      ? svc.getChannelTemplates(channelId)
      : svc.getConversationTemplates(conversationId);
    fetchTemplates.then(res => {
      if (!alive) return;
      if (res && res.ok && res.data) {
        setSupported(!!res.data.supported);
        setTemplates(res.data.templates || []);
        setCanCreate(!!res.data.can_create);
        setCanDelete(!!res.data.can_delete);
        setChannelInfo(res.data.channel || null);
      } else {
        setError((res && res.error) || 'Falha ao carregar templates.');
      }
      setLoading(false);
    }).catch(() => { if (alive) { setError('Falha ao carregar templates.'); setLoading(false); } });
    return () => { alive = false; };
  }, [conversationId, channelId, channelMode]);

  // Preferências: buscadas assim que o canal é conhecido. Falha aqui NÃO derruba
  // a lista — degrada para "sem estrela, sem arquivar", que é a tela de antes.
  useEffect(() => {
    if (!prefsChannelId || !http) return undefined;
    let alive = true;
    http.get(`/template-prefs?channel_id=${encodeURIComponent(prefsChannelId)}`)
      .then(res => {
        if (!alive || !res || !res.ok || !res.data) return;
        setFavorites(new Set(res.data.favorites || []));
        setArchived(new Set(res.data.archived || []));
        setCanArchive(!!res.data.can_archive);
        setCanFavorite(!!res.data.can_favorite);
      })
      .catch(() => { /* silencioso: a lista continua utilizável */ });
    return () => { alive = false; };
  }, [prefsChannelId, http]);

  // Otimista com rollback: marcar estrela tem de responder na hora. O servidor é
  // a autoridade — se recusar, o estado volta e o erro aparece.
  async function toggleFavorite(name) {
    if (!canFavorite || !prefsChannelId || !http) return;
    const on = !favorites.has(name);
    setPrefError('');
    setFavorites(prev => {
      const next = new Set(prev);
      if (on) next.add(name); else next.delete(name);
      return next;
    });
    const res = await http.post('/template-prefs/favorite',
      { channel_id: prefsChannelId, template_name: name, favorite: on });
    if (!res || !res.ok) {
      setFavorites(prev => {
        const next = new Set(prev);
        if (on) next.delete(name); else next.add(name);
        return next;
      });
      setPrefError((res && res.error) || 'Falha ao salvar o favorito.');
    }
  }

  async function toggleArchived(name) {
    if (!canArchive || !prefsChannelId || !http) return;
    const on = !archived.has(name);
    setPrefError('');
    setPrefBusy(name);
    setArchived(prev => {
      const next = new Set(prev);
      if (on) next.add(name); else next.delete(name);
      return next;
    });
    const res = await http.post('/template-prefs/archive',
      { channel_id: prefsChannelId, template_name: name, archived: on });
    setPrefBusy(null);
    if (!res || !res.ok) {
      setArchived(prev => {
        const next = new Set(prev);
        if (on) next.delete(name); else next.add(name);
        return next;
      });
      setPrefError((res && res.error) || 'Falha ao arquivar. Você tem permissão?');
    }
  }

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
      ? await svc.sendChannelTemplate(channelId, { phone, ...payload })
      : await svc.sendConversationTemplate(conversationId, payload);
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
      ? await svc.deleteChannelTemplate(channelId, name)
      : await svc.deleteConversationTemplate(conversationId, name);
    setDeletingName(null);
    if (res && res.ok) {
      await loadTemplates();
    } else {
      setDeleteError((res && res.error) || 'Falha ao apagar template.');
    }
  }

  // Arquivado sai → aba → busca (nome + categoria + CONTEÚDO) → favoritos no
  // topo. A ordem mora em `templateFilter.js`, que é puro e testado.
  const view = { query: search, tab, favorites, archived };
  const filtered = useMemo(
    () => applyView(templates, view),
    [templates, search, tab, favorites, archived]);
  const contagem = useMemo(
    () => contarPorAba(templates, view),
    [templates, search, favorites, archived]);

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
            <div class="flex flex-col min-w-0">
              <span class="text-wa-text text-[15px] font-medium truncate">${title}</span>
              <${ChannelBadge} channel=${channelInfo} />
            </div>
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
              svc=${svc}
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
              placeholder="Buscar por nome, categoria ou conteúdo…"
              class="wa-field w-full text-[14px] rounded-[8px] px-3 py-2 border border-wa-border outline-none mb-3"
            />
            <!-- Abas: só aparecem quando há o que separar (instalação sem login
                 não tem favoritos, e sem nada arquivado a aba seria uma lista
                 vazia permanente). -->
            ${(canFavorite || archived.size > 0) ? html`
              <div class="flex items-center gap-1.5 mb-3">
                ${[['todas', 'Todas'], ['favoritas', 'Favoritas'], ['arquivadas', 'Arquivadas']]
                  .filter(([k]) => (k !== 'favoritas' || canFavorite)
                                && (k !== 'arquivadas' || archived.size > 0 || canArchive))
                  .map(([k, rotulo]) => html`
                    <button key=${k} type="button" onClick=${() => setTab(k)}
                      class=${`px-2.5 py-1 rounded-full text-[12px] font-medium transition-colors ${
                        tab === k
                          ? 'bg-wa-teal text-white'
                          : 'bg-wa-hover text-wa-secondary hover:text-wa-text'}`}>
                      ${rotulo}${contagem[k] ? html` <span class="opacity-70">${contagem[k]}</span>` : null}
                    </button>
                  `)}
              </div>
            ` : null}
            ${deleteError ? html`<div class="text-red-500 text-[13px] mb-2">${deleteError}</div>` : null}
            ${prefError ? html`<div class="text-red-500 text-[13px] mb-2">${prefError}</div>` : null}
            ${filtered.length === 0 ? html`
              <div class="text-wa-secondary text-[14px] py-6 text-center">
                ${templates.length === 0
                  ? 'Nenhum template encontrado. Verifique o WABA ID na configuração do canal.'
                  : (tab === 'favoritas'
                      ? 'Você ainda não marcou nenhum favorito. Use a ☆ na lista.'
                      : (tab === 'arquivadas'
                          ? 'Nenhum template arquivado.'
                          : 'Nenhum template corresponde à busca.'))}
              </div>
            ` : html`
              <div class="flex flex-col gap-2">
                ${filtered.map(t => {
                  const badge = statusBadge(t.status);
                  const approved = isApproved(t);
                  return html`
                  <div key=${(t.name || '') + '|' + langCode(t)}
                    class="flex items-stretch gap-2 bg-wa-bg border border-wa-border rounded-[8px] overflow-hidden hover:border-wa-iconActive transition-colors">
                    ${canFavorite ? html`
                      <button type="button" onClick=${() => toggleFavorite(t.name)}
                        class=${`shrink-0 self-stretch px-2 transition-colors ${
                          t._favorito ? 'text-amber-500' : 'text-wa-secondary hover:text-amber-500'}`}
                        title=${t._favorito ? 'Remover dos favoritos' : 'Marcar como favorito'}
                        aria-pressed=${t._favorito ? 'true' : 'false'}>
                        <${StarIcon} filled=${t._favorito} />
                      </button>
                    ` : null}
                    <button onClick=${() => selectTemplate(t)}
                      class="flex-1 text-left px-3 py-2.5 min-w-0">
                      <div class="flex items-center gap-2">
                        <span class="text-wa-text text-[14px] font-medium truncate">${t.name}</span>
                        <span class="text-[10px] font-medium px-1.5 py-0.5 rounded-full shrink-0 ${badge.cls}">${badge.label}</span>
                      </div>
                      <div class="text-wa-secondary text-[12px] mt-0.5">
                        ${(t.category || '—')} · ${langCode(t)}${approved ? '' : ' · não enviável'}
                      </div>
                      <!-- Casou no conteúdo: sem mostrar ONDE, a linha parece um
                           falso positivo (o termo não está no nome nem na categoria). -->
                      ${t._trecho ? html`
                        <div class="text-wa-secondary text-[11px] mt-1 italic truncate">
                          ${t._campo}: ${t._trecho}
                        </div>
                      ` : null}
                    </button>
                    ${(canArchive || canDelete) ? html`
                      <div class="flex items-center pr-2 shrink-0">
                        ${canArchive && confirmDelete !== t.name ? html`
                          <button type="button" onClick=${() => toggleArchived(t.name)}
                            disabled=${prefBusy === t.name}
                            class="text-wa-secondary hover:text-wa-teal p-1.5 rounded-[6px] hover:bg-wa-hover transition-colors disabled:opacity-50"
                            title=${t._arquivado
                              ? 'Desarquivar (volta para a lista de todos)'
                              : 'Arquivar: some da lista para TODOS os atendentes. Não apaga na Meta.'}>
                            ${prefBusy === t.name ? '…' : html`<${ArchiveIcon} restore=${t._arquivado} />`}
                          </button>
                        ` : null}
                        <!-- Excluir tem gate PRÓPRIO (template.delete, do core):
                             quem só pode arquivar não pode ver a lixeira. Arquivar é
                             reversível e local; apagar é irreversível e vai à Meta. -->
                        ${!canDelete ? null : (confirmDelete === t.name ? html`
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
                        `)}
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

// ── Remetente (sub-component) ────────────────────────────────────────────────
// "De quem sai este template": nome do canal + número oficial, vindos do payload
// da listagem (`channel`). O core não sabe o provider — quem informa o número é
// o próprio plugin do canal (via `status()`), então um provider sem número
// simplesmente rende só o nome, e um canal ainda não resolvido não rende nada.
function ChannelBadge({ channel }) {
  if (!channel) return null;
  const name = (channel.name || '').trim();
  const phone = channel.phone ? formatPhoneDisplay(channel.phone) : '';
  if (!name && !phone) return null;
  const label = [name, phone].filter(Boolean).join(' · ');
  return html`
    <span class="text-wa-secondary text-[11px] truncate" title=${`Canal: ${label}`}>
      Enviando por ${label}
    </span>
  `;
}

// ── Create form (sub-component) ──────────────────────────────────────────────
// Builds a SIMPLE definition (name/category/language + header/body/footer +
// example values per {{n}} + media header handle + buttons) and POSTs it; the
// provider assembles the Graph components. Plano 73 added the media header
// (uploaded as an example → Meta handle) and the four button types.
function CreateTemplateForm({ conversationId, channelId, channelMode, onCancel, onCreated, svc }) {
  const [name, setName] = useState('');
  const [category, setCategory] = useState('UTILITY');
  const [language, setLanguage] = useState('pt_BR');
  const [headerKind, setHeaderKind] = useState('none');  // none|TEXT|IMAGE|VIDEO|DOCUMENT
  const [headerText, setHeaderText] = useState('');
  const [bodyText, setBodyText] = useState('');
  const [footerText, setFooterText] = useState('');
  const [headerEx, setHeaderEx] = useState({});  // {n: value}
  const [bodyEx, setBodyEx] = useState({});       // {n: value}
  const [mediaName, setMediaName] = useState('');
  const [mediaHandle, setMediaHandle] = useState('');
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const [buttons, setButtons] = useState([]);   // [{type, text, url, example, phone_number}]
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [okMsg, setOkMsg] = useState('');

  const nameValid = /^[a-z0-9_]+$/.test(name);
  const isMediaHeader = MEDIA_HEADER_KINDS.includes(headerKind);
  const headerIdxs = useMemo(
    () => (headerKind === 'TEXT' ? placeholders(headerText) : []),
    [headerKind, headerText]);
  const bodyIdxs = useMemo(() => placeholders(bodyText), [bodyText]);
  const buttonsError = useMemo(() => validateButtons(buttons), [buttons]);

  async function pickMedia(file) {
    if (!file) return;
    setUploadError('');
    setMediaHandle('');
    setMediaName(file.name || '');
    setUploading(true);
    const res = channelMode
      ? await svc.uploadChannelTemplateExample(channelId, file)
      : await svc.uploadConversationTemplateExample(conversationId, file);
    setUploading(false);
    if (res && res.ok && res.data && res.data.handle) {
      setMediaHandle(res.data.handle);
    } else {
      setUploadError((res && res.error) || 'Falha ao enviar o arquivo.');
    }
  }

  function updateButton(i, patch) {
    setButtons(prev => prev.map((b, idx) => (idx === i ? { ...b, ...patch } : b)));
  }

  const canCreate = useMemo(() => {
    if (saving || uploading || !nameValid || !bodyText.trim()) return false;
    if (isMediaHeader && !mediaHandle) return false;
    if (buttonsError) return false;
    for (const n of headerIdxs) if (!(headerEx[n] || '').trim()) return false;
    for (const n of bodyIdxs) if (!(bodyEx[n] || '').trim()) return false;
    return true;
  }, [saving, uploading, nameValid, bodyText, isMediaHeader, mediaHandle,
      buttonsError, headerIdxs, headerEx, bodyIdxs, bodyEx]);

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
    if (headerKind === 'TEXT' && headerText.trim()) payload.header_text = headerText.trim();
    if (isMediaHeader && mediaHandle) {
      payload.header_format = headerKind;
      payload.header_handle = mediaHandle;
    }
    if (footerText.trim()) payload.footer_text = footerText.trim();
    if (bodyIdxs.length) payload.body_examples = bodyIdxs.map(n => (bodyEx[n] || '').trim());
    if (headerIdxs.length) payload.header_examples = headerIdxs.map(n => (headerEx[n] || '').trim());
    if (buttons.length) payload.buttons = buttons.map(buildButtonPayload);

    const res = channelMode
      ? await svc.createChannelTemplate(channelId, payload)
      : await svc.createConversationTemplate(conversationId, payload);
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
          <input type="text" value=${language} list="wac-tpl-langs"
            onInput=${(e) => setLanguage(e.target.value)} class=${fieldCls} />
          <datalist id="wac-tpl-langs">
            ${LANG_SUGGESTIONS.map(l => html`<option key=${l} value=${l}></option>`)}
          </datalist>
        </div>
      </div>

      <div>
        <label class="text-wa-secondary text-[12px] font-medium block mb-1">Cabeçalho (opcional)</label>
        <div class="flex flex-wrap gap-3 mb-2">
          ${HEADER_KIND_OPTIONS.map(opt => html`
            <label key=${opt.value} class="flex items-center gap-1.5 text-[13px] text-wa-text cursor-pointer">
              <input type="radio" name="wac-tpl-header-kind" value=${opt.value}
                checked=${headerKind === opt.value}
                onChange=${() => { setHeaderKind(opt.value); setUploadError(''); }} />
              ${opt.label}
            </label>
          `)}
        </div>
        ${headerKind === 'TEXT' ? html`
          <input type="text" value=${headerText}
            onInput=${(e) => setHeaderText(e.target.value)}
            placeholder="ex: Olá, {{1}}!" class=${fieldCls} />
        ` : null}
        ${isMediaHeader ? html`
          <div class="rounded-[8px] border border-dashed border-wa-border p-3 bg-wa-bg space-y-2">
            <input type="file" accept=${MEDIA_ACCEPT[headerKind]}
              onChange=${(e) => pickMedia(e.target.files && e.target.files[0])}
              class="wa-field w-full text-[13px] rounded-[8px] px-2 py-1.5 border border-wa-border" />
            <div class="text-[11px] text-wa-secondary">
              Envie um arquivo de EXEMPLO (até 16 MB) — a Meta usa só para aprovar o modelo.
            </div>
            ${uploading ? html`<div class="text-[12px] text-wa-secondary">Enviando…</div>` : null}
            ${mediaHandle ? html`
              <div class="text-[12px] text-green-700">Pronto ✓ ${mediaName}</div>
            ` : null}
            ${uploadError ? html`<div class="text-[12px] text-red-500">${uploadError}</div>` : null}
          </div>
        ` : null}
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

      <div class="space-y-2">
        <div class="flex items-center justify-between">
          <label class="text-wa-secondary text-[12px] font-medium">Botões (opcional)</label>
          <button type="button" disabled=${buttons.length >= BUTTONS_MAX}
            onClick=${() => setButtons(prev => [...prev, { type: 'QUICK_REPLY', text: '' }])}
            class="text-[12px] px-2 py-1 rounded-[6px] text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50">
            + Adicionar botão
          </button>
        </div>
        ${buttons.map((b, i) => html`
          <div key=${'btn' + i} class="rounded-[8px] border border-wa-border p-2 space-y-2 bg-wa-bg">
            <div class="flex gap-2 items-center">
              <select value=${b.type}
                onChange=${(e) => updateButton(i, { type: e.target.value })}
                class="${fieldCls} flex-1">
                ${BUTTON_TYPE_OPTIONS.map(o => html`<option key=${o.value} value=${o.value}>${o.label}</option>`)}
              </select>
              <button type="button" title="Remover botão"
                onClick=${() => setButtons(prev => prev.filter((_, idx) => idx !== i))}
                class="px-2 py-1.5 rounded-[6px] text-wa-secondary hover:bg-wa-hover transition-colors">
                <${TrashIcon} />
              </button>
            </div>
            ${b.type !== 'COPY_CODE' ? html`
              <input type="text" value=${b.text || ''} maxlength=${BUTTON_TEXT_MAX}
                onInput=${(e) => updateButton(i, { text: e.target.value })}
                placeholder="Texto do botão (até 25 caracteres)" class=${fieldCls} />
            ` : null}
            ${b.type === 'URL' ? html`
              <input type="text" value=${b.url || ''}
                onInput=${(e) => updateButton(i, { url: e.target.value })}
                placeholder="https://exemplo.com/rastreio/{{1}}" class=${fieldCls} />
              ${(b.url || '').includes('{{') ? html`
                <input type="text" value=${b.example || ''}
                  onInput=${(e) => updateButton(i, { example: e.target.value })}
                  placeholder="Exemplo da URL completa" class=${fieldCls} />
              ` : null}
            ` : null}
            ${b.type === 'PHONE_NUMBER' ? html`
              <input type="text" value=${b.phone_number || ''}
                onInput=${(e) => updateButton(i, { phone_number: e.target.value })}
                placeholder="+5511999999999" class=${fieldCls} />
            ` : null}
            ${b.type === 'COPY_CODE' ? html`
              <input type="text" value=${b.example || ''}
                onInput=${(e) => updateButton(i, { example: e.target.value })}
                placeholder="Código de exemplo (ex: PROMO2026)" class=${fieldCls} />
            ` : null}
          </div>
        `)}
        ${buttonsError ? html`<div class="text-[12px] text-red-500">${buttonsError}</div>` : null}
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

// Estrela: contorno quando não é favorito, preenchida quando é. `currentColor`
// deixa o âmbar vir da classe do botão — legível nos dois temas sobre wa-bg.
function StarIcon({ filled }) {
  return html`
    <svg width="16" height="16" viewBox="0 0 24 24" fill=${filled ? 'currentColor' : 'none'}
      stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
    </svg>
  `;
}

// Caixa de arquivo — DELIBERADAMENTE diferente da lixeira: as duas ações moram
// lado a lado e têm risco oposto (arquivar é reversível e local; apagar é
// irreversível e vai à Meta). Com `restore`, a seta aponta para cima.
function ArchiveIcon({ restore }) {
  return html`
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <rect x="2" y="3" width="20" height="5" rx="1"></rect>
      <path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"></path>
      ${restore
        ? html`<path d="M12 18v-6"></path><path d="M9 15l3-3 3 3"></path>`
        : html`<path d="M12 12v6"></path><path d="M9 15l3 3 3-3"></path>`}
    </svg>
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

