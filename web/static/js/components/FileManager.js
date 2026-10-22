// Tela "Arquivos" (plano 179): ver o que ocupa espaço e apagar a mídia recebida/enviada.
//
// O servidor só recebe ids opacos e só lista as duas pastas de mídia, então nada aqui
// alcança o banco, a sessão do WhatsApp ou o código. O recorte do que cada pessoa vê
// (inbox/time) e a proteção dos arquivos de plugin são decididos no servidor; a tela
// só os mostra (`usage.scoped`, `item.plugin`, `item.orphan`) e antecipa o impacto na
// confirmação. A lógica que não precisa de DOM mora em services/fileManagerView.js.

import { h } from 'preact';
import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks';
import htm from 'htm';
import {
  getFilesUsage, listFiles, deleteFiles, listInboxes, fetchFilePreview,
} from '../services/filesApi.js';
import { subscribe } from '../services/wsBus.js';
import { notify } from '../services/notify.js';
import {
  PAGE, SELECT_PAGE, DELETE_BATCH, TEXT_PREVIEW_BYTES, DISK_WARNING_TEXT, PLUGIN_ONLY_TITLE,
  fmtBytes, fmtDateTime, plural, periodRange, diskPercent, diskLevel,
  previewFamily, conversationHref, canDelete, summarizeSelection, confirmCopy,
  emptyDeleteResult, addDeleteResult, deleteNotice, buildListParams, hasActiveFilters,
} from '../services/fileManagerView.js';
import { ContactPicker } from './files/ContactPicker.js';

const html = htm.bind(h);

const ROOTS = [
  {
    id: 'received',
    label: 'Recebidos',
    hint: 'Fotos, vídeos, áudios e documentos que seus contatos enviaram. Um arquivo que você mandou pelo celular do WhatsApp também aparece aqui.',
  },
  {
    id: 'sent',
    label: 'Enviados (painel, API e plugins)',
    hint: 'Arquivos enviados pelo painel, pela API e por plugins. O que você mandou direto do celular fica em Recebidos.',
  },
];

const SORTS = [
  { id: 'date:desc', label: 'Mais recentes' },
  { id: 'date:asc', label: 'Mais antigos' },
  { id: 'size:desc', label: 'Mais pesados' },
  { id: 'size:asc', label: 'Mais leves' },
  { id: 'kind:asc', label: 'Por tipo' },
  { id: 'name:asc', label: 'Por nome' },
];

const KINDS = [
  { id: '', label: 'Todos os tipos' },
  { id: 'image', label: 'Fotos' },
  { id: 'video', label: 'Vídeos' },
  { id: 'audio', label: 'Áudios' },
  { id: 'document', label: 'Documentos e outros' },
];

const CHAT_TYPES = [
  { id: '', label: 'Conversas e grupos' },
  { id: 'individual', label: 'Só conversas individuais' },
  { id: 'group', label: 'Só grupos' },
];

const PERIODS = [
  { id: 'today', label: 'Hoje', days: 1 },
  { id: '7d', label: 'Últimos 7 dias', days: 7 },
  { id: '30d', label: 'Últimos 30 dias', days: 30 },
];

const KIND_LABEL = { image: 'Foto', video: 'Vídeo', audio: 'Áudio', document: 'Documento' };
const KIND_ICON = { image: '🖼️', video: '🎬', audio: '🎧', document: '📄' };

const FIELD = 'wa-field rounded-lg px-3 py-2 text-[14px] outline-none focus:ring-2 focus:ring-wa-teal';

// Espera a digitação pausar, para cada tecla não varrer as pastas de novo.
function useDebounced(value, ms = 350) {
  const [out, setOut] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setOut(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return out;
}

// Mesmo hand-off SPA do resto do painel (pushState + popstate): clique com modificador
// (ctrl/cmd/shift) e botão do meio ficam com o padrão do navegador (nova aba).
function openConversation(e, href) {
  if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  history.pushState(null, '', href);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

function Origin({ item }) {
  const o = item.origin;
  if (!o) {
    return html`<span class="text-wa-secondary" title="Nenhuma conversa usa este arquivo">Sem conversa</span>`;
  }
  const label = `${o.is_group ? '👥 ' : ''}${o.name}`;
  const href = conversationHref(o);
  return html`
    <span class="block min-w-0">
      ${href
        ? html`<a href=${href} onClick=${(e) => openConversation(e, href)}
             class="block truncate text-wa-teal hover:underline" title="Abrir a conversa na mensagem deste arquivo">${label}</a>`
        : html`<span class="block truncate text-wa-text" title=${label}>${label}</span>`}
      ${o.channel_name ? html`<span class="block truncate text-[12px] text-wa-secondary">${o.channel_name}</span>` : null}
    </span>`;
}

function PluginBadge({ plugin }) {
  return html`
    <span class="inline-flex max-w-full items-center rounded-full border border-wa-border bg-wa-hover px-2 py-0.5 text-[11.5px] text-wa-secondary"
          title="Este arquivo é usado por um plugin: apagar aqui só tira das conversas, o arquivo continua no servidor.">
      <span class="truncate">Usado pelo plugin ${plugin.name || plugin.id}</span>
    </span>`;
}

function UsageBar({ usage }) {
  if (!usage) {
    return html`<div class="h-24 rounded-xl bg-wa-hover animate-pulse-slow"></div>`;
  }
  const disk = usage.disk || null;
  const pct = diskPercent(disk);
  const level = diskLevel(pct);
  const barColor = level === 'danger' ? 'bg-red-500' : level === 'warn' ? 'bg-amber-500' : 'bg-wa-teal';
  const pctColor = level === 'danger' ? 'text-red-600' : level === 'warn' ? 'text-amber-700' : 'text-wa-secondary';
  const recv = usage.roots?.received || { files: 0, bytes: 0 };
  const sent = usage.roots?.sent || { files: 0, bytes: 0 };
  const visible = usage.visible || { files: 0, bytes: 0 };

  return html`
    <section class="bg-wa-bg rounded-xl p-4 border border-wa-border shadow-sm" aria-label="Uso de espaço">
      <div class="flex flex-wrap items-baseline justify-between gap-2">
        <div class="min-w-0">
          <div class="text-[12px] font-semibold uppercase tracking-wider text-wa-secondary">Disco do servidor</div>
          ${disk && disk.total ? html`
            <div class="text-[22px] font-semibold text-wa-text leading-tight">
              ${fmtBytes(disk.used)}<span class="text-[14px] font-normal text-wa-secondary"> de ${fmtBytes(disk.total)} · ${fmtBytes(disk.free)} livres</span>
            </div>` : html`
            <div class="text-[14px] text-wa-secondary">Não foi possível medir o disco do servidor.</div>`}
        </div>
        ${pct != null ? html`<div class="text-[13px] font-medium ${pctColor}">${Math.round(pct)}%</div>` : null}
      </div>
      ${pct != null ? html`
        <div class="mt-3 h-2.5 rounded-full bg-wa-hover overflow-hidden" role="progressbar" aria-label="Disco usado" aria-valuemin="0" aria-valuemax="100" aria-valuenow=${Math.round(pct)}>
          <div class="h-full ${barColor} transition-all" style="width:${pct}%"></div>
        </div>` : null}
      ${level !== 'ok' ? html`
        <p class="mt-2 text-[12.5px] ${level === 'danger' ? 'text-red-600' : 'text-amber-700'}" role="alert">${DISK_WARNING_TEXT}</p>` : null}
      ${usage.scoped ? html`
        <dl class="mt-3 grid grid-cols-1 gap-2 text-[13px]">
          <div class="rounded-lg bg-wa-panel border border-wa-border px-3 py-2 min-w-0">
            <dt class="text-wa-secondary text-[12px]">Arquivos das conversas que você pode ver</dt>
            <dd class="text-wa-text font-medium">${fmtBytes(visible.bytes)} <span class="text-wa-secondary font-normal">· ${plural(visible.files, 'arquivo', 'arquivos')}</span></dd>
          </div>
        </dl>` : html`
        <dl class="mt-3 grid grid-cols-1 sm:grid-cols-3 gap-2 text-[13px]">
          <div class="rounded-lg bg-wa-panel border border-wa-border px-3 py-2 min-w-0">
            <dt class="text-wa-secondary text-[12px]">Recebidos</dt>
            <dd class="text-wa-text font-medium">${fmtBytes(recv.bytes)} <span class="text-wa-secondary font-normal">· ${plural(recv.files, 'arquivo', 'arquivos')}</span></dd>
          </div>
          <div class="rounded-lg bg-wa-panel border border-wa-border px-3 py-2 min-w-0">
            <dt class="text-wa-secondary text-[12px]">Enviados (painel, API e plugins)</dt>
            <dd class="text-wa-text font-medium">${fmtBytes(sent.bytes)} <span class="text-wa-secondary font-normal">· ${plural(sent.files, 'arquivo', 'arquivos')}</span></dd>
          </div>
          <div class="rounded-lg bg-wa-panel border border-wa-border px-3 py-2 min-w-0">
            <dt class="text-wa-secondary text-[12px]">Sistema e dados do app</dt>
            <dd class="text-wa-text font-medium">${fmtBytes(usage.system_bytes)} <span class="text-wa-secondary font-normal">· não pode ser apagado aqui</span></dd>
          </div>
        </dl>`}
    </section>
  `;
}

// UTF-8 primeiro; arquivo salvo por programa antigo do Windows costuma ser Latin-1.
// ``stream`` evita que um caractere multibyte cortado pelo teto pareça inválido.
async function readText(blob) {
  const buf = await blob.slice(0, TEXT_PREVIEW_BYTES).arrayBuffer();
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(buf, { stream: true });
  } catch {
    return new TextDecoder('windows-1252').decode(buf);
  }
}

function Preview({ item, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const [blobUrl, setBlobUrl] = useState('');
  const [mime, setMime] = useState('');
  const [text, setText] = useState(null);
  const [clipped, setClipped] = useState(false);
  const [failed, setFailed] = useState(false);
  // O navegador recebeu o arquivo mas não decodifica (HEIC fora do Safari, AMR, ...).
  const [undecodable, setUndecodable] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let url = '';
    const ctl = new AbortController();
    setBlobUrl(''); setMime(''); setText(null); setClipped(false); setFailed(false); setUndecodable(false);
    fetchFilePreview(item.id, { signal: ctl.signal })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error('bad'))))
      .then(async (b) => {
        const type = (b.type || '').split(';')[0].trim().toLowerCase();
        const body = previewFamily(type) === 'text' ? await readText(b) : null;
        if (cancelled) return;
        url = URL.createObjectURL(b);
        setMime(type);
        setClipped(body !== null && b.size > TEXT_PREVIEW_BYTES);
        setText(body);
        setBlobUrl(url);
      })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; ctl.abort(); if (url) URL.revokeObjectURL(url); };
  }, [item.id]);

  const family = previewFamily(mime);
  const wide = family === 'pdf' || family === 'text';
  const cantShow = undecodable || (blobUrl && family === 'other');
  const onBroken = () => setUndecodable(true);

  const body = failed ? html`<p class="text-[13px] text-wa-secondary">Não foi possível abrir este arquivo.</p>`
    : !blobUrl ? html`<p class="text-[13px] text-wa-secondary animate-pulse-slow">Carregando...</p>`
    : cantShow ? html`
        <div class="flex flex-col items-center gap-3 py-6 text-center">
          <span class="text-[40px]" aria-hidden="true">${KIND_ICON[item.kind] || '📄'}</span>
          <p class="text-[14px] text-wa-text max-w-sm">
            ${undecodable
              ? 'O seu navegador não consegue mostrar este arquivo aqui. Baixe para abrir no seu computador.'
              : 'Este tipo de arquivo não tem visualização. Baixe para abrir no seu computador.'}
          </p>
          <a href=${blobUrl} download=${item.name} class="px-4 py-2 rounded-lg bg-wa-teal text-white text-[14px] font-medium max-w-full truncate">Baixar ${item.name}</a>
        </div>`
    : family === 'image' ? html`<img src=${blobUrl} alt=${item.name} onError=${onBroken} class="max-w-full max-h-[70vh] rounded" />`
    : family === 'video' ? html`<video src=${blobUrl} controls onError=${onBroken} class="max-w-full max-h-[70vh] rounded"></video>`
    : family === 'audio' ? html`<audio src=${blobUrl} controls onError=${onBroken} class="w-full"></audio>`
    : family === 'pdf' ? html`<iframe src=${blobUrl} title=${item.name} class="w-full h-[72vh] rounded bg-white"></iframe>`
    : html`
        <div class="w-full self-stretch flex flex-col gap-2 min-h-0">
          <pre tabindex="0" class="flex-1 min-h-0 overflow-auto rounded border border-wa-border bg-wa-bg p-3 text-[12.5px] leading-relaxed text-wa-text font-mono whitespace-pre-wrap break-words max-h-[70vh]">${text}</pre>
          ${clipped ? html`<p class="text-[12px] text-wa-secondary">Mostrando só o começo do arquivo. Baixe para ver tudo.</p>` : null}
        </div>`;

  return html`
    <div class="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4"
         role="dialog" aria-modal="true" aria-label=${item.name}
         onClick=${(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div class="bg-wa-bg rounded-xl ${wide ? 'max-w-5xl' : 'max-w-3xl'} w-full max-h-[90vh] flex flex-col overflow-hidden border border-wa-border">
        <div class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-4 py-3 border-b border-wa-border">
          <div class="min-w-0 flex-1 basis-[12rem]">
            <div class="text-[14px] font-medium text-wa-text truncate" title=${item.name}>${item.name}</div>
            <div class="text-[12px] text-wa-secondary">${fmtBytes(item.size)} · ${fmtDateTime(item.mtime)}</div>
          </div>
          <div class="flex shrink-0 items-center gap-1 ml-auto">
            ${blobUrl && !cantShow && family === 'pdf' ? html`
              <a href=${blobUrl} target="_blank" rel="noopener" class="px-3 py-1.5 rounded-lg text-[13px] text-wa-teal hover:bg-wa-hover">Abrir em nova aba</a>` : null}
            ${blobUrl && !cantShow ? html`
              <a href=${blobUrl} download=${item.name} class="px-3 py-1.5 rounded-lg text-[13px] text-wa-teal hover:bg-wa-hover">Baixar</a>` : null}
            <button type="button" onClick=${onClose} class="px-3 py-1.5 rounded-lg text-[13px] text-wa-secondary hover:bg-wa-hover">Fechar</button>
          </div>
        </div>
        <div class="flex-1 min-h-0 overflow-auto p-4 flex items-center justify-center bg-wa-panel">${body}</div>
      </div>
    </div>
  `;
}

function ConfirmDelete({ summary, busy, onCancel, onConfirm }) {
  const ref = useRef(null);
  const copy = confirmCopy(summary);
  useEffect(() => {
    if (ref.current) ref.current.focus();
    const onKey = (e) => { if (e.key === 'Escape' && !busy) onCancel(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [busy]);
  return html`
    <div class="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" role="alertdialog" aria-modal="true" aria-labelledby="del-title"
         onClick=${(e) => { if (e.target === e.currentTarget && !busy) onCancel(); }}>
      <div class="bg-wa-bg rounded-xl max-w-md w-full p-5 border border-wa-border shadow-xl">
        <h2 id="del-title" class="text-[17px] font-semibold text-wa-text">${copy.title}</h2>
        <p class="mt-2 text-[14px] text-wa-text">${copy.lines[0]}</p>
        ${copy.lines.slice(1).map((line, i) => html`
          <p key=${i} class="mt-2 text-[13px] ${i === 0 && summary.plugin.count > 0 ? 'text-wa-text' : 'text-wa-secondary'}">${line}</p>`)}
        <div class="mt-5 flex justify-end gap-2">
          <button type="button" ref=${ref} onClick=${onCancel} disabled=${busy}
            class="px-4 py-2 rounded-lg text-[14px] text-wa-text border border-wa-border hover:bg-wa-hover disabled:opacity-60">Cancelar</button>
          <button type="button" onClick=${onConfirm} disabled=${busy}
            class="px-4 py-2 rounded-lg text-[14px] font-medium bg-red-600 text-white hover:bg-red-700 disabled:opacity-60">
            ${busy ? 'Apagando...' : 'Apagar'}
          </button>
        </div>
      </div>
    </div>
  `;
}

export function FileManager() {
  const [usage, setUsage] = useState(null);
  const [inboxes, setInboxes] = useState([]);
  const [root, setRoot] = useState('received');
  const [sort, setSort] = useState('date:desc');
  const [kind, setKind] = useState('');
  const [orphans, setOrphans] = useState(false);
  const [search, setSearch] = useState('');
  const [contactText, setContactText] = useState('');
  // O contato escolhido na lista (id exato); o texto digitado e não escolhido segue como busca simples.
  const [pickedContact, setPickedContact] = useState(null);
  const [inboxId, setInboxId] = useState('');
  const [chatType, setChatType] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [totalBytes, setTotalBytes] = useState(0);
  const [nextOffset, setNextOffset] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState(() => new Map());
  const [preview, setPreview] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [selectingAll, setSelectingAll] = useState(false);
  const reqRef = useRef(0);

  const [sortKey, sortOrder] = sort.split(':');
  const q = useDebounced(search.trim());
  const contact = useDebounced(contactText.trim());
  const contactId = pickedContact ? pickedContact.id : '';
  // "Só sem conversa" só existe para quem vê tudo: com escopo, arquivo sem conversa nunca aparece.
  const canOrphans = !!usage && usage.scoped === false;
  const filters = {
    root, sort: sortKey, order: sortOrder, kind,
    orphans: canOrphans && orphans,
    q, contact: contactId ? '' : contact, contactId, inboxId, chatType, dateFrom, dateTo,
  };
  const hasFilters = hasActiveFilters(filters);

  const loadUsage = useCallback(async (refresh = false) => {
    try {
      const res = await getFilesUsage(refresh);
      if (res && res.ok) setUsage(res.data);
    } catch { /* mantém os números anteriores */ }
  }, []);

  // ``silent``: recarga disparada por outra aba — troca a lista sem piscar "Carregando...".
  const load = useCallback(async ({ silent = false } = {}) => {
    const req = ++reqRef.current;
    if (!silent) setLoading(true);
    setError('');
    try {
      const res = await listFiles(buildListParams(filters, 0, PAGE));
      if (req !== reqRef.current) return;
      if (!res || !res.ok) {
        setError((res && res.error) || 'Não foi possível listar os arquivos.');
        setItems([]);
        return;
      }
      setItems(res.data.items);
      setTotal(res.data.total);
      setTotalBytes(res.data.total_bytes || 0);
      setNextOffset(res.data.next_offset);
    } catch {
      if (req === reqRef.current) setError('Não foi possível listar os arquivos.');
    } finally {
      if (req === reqRef.current) setLoading(false);
    }
  }, [root, sortKey, sortOrder, kind, filters.orphans, q, contactId, filters.contact, inboxId, chatType, dateFrom, dateTo]);

  async function loadMore() {
    if (nextOffset == null || loadingMore) return;
    const req = reqRef.current;
    setLoadingMore(true);
    try {
      const res = await listFiles(buildListParams(filters, nextOffset, PAGE));
      if (req !== reqRef.current) return;
      if (res && res.ok) {
        setItems((prev) => [...prev, ...res.data.items]);
        setNextOffset(res.data.next_offset);
      }
    } finally {
      setLoadingMore(false);
    }
  }

  useEffect(() => { loadUsage(true); }, []);
  useEffect(() => {
    listInboxes().then((res) => {
      if (res && res.ok && Array.isArray(res.data)) setInboxes(res.data);
    }).catch(() => { /* filtro opcional: sem a lista, só some o select */ });
  }, []);
  useEffect(() => { setSelected(new Map()); load(); }, [load]);

  // Outra aba (ou outra pessoa) apagou arquivos: atualiza os números e a lista. Sempre
  // pelo barramento único (🚫 nunca `new WebSocket('/ws')` — não leva o token). Os refs
  // deixam o handler enxergar o `load` mais novo sem reassinar a cada filtro.
  const loadRef = useRef(load);
  const loadUsageRef = useRef(loadUsage);
  loadRef.current = load;
  loadUsageRef.current = loadUsage;
  useEffect(() => {
    let timer;
    const off = subscribe({
      storage_changed: () => {
        clearTimeout(timer);
        timer = setTimeout(() => { loadUsageRef.current(true); loadRef.current({ silent: true }); }, 400);
      },
    });
    return () => { clearTimeout(timer); off(); };
  }, []);

  function clearFilters() {
    setSearch('');
    setContactText('');
    setPickedContact(null);
    setInboxId('');
    setChatType('');
    setDateFrom('');
    setDateTo('');
    setKind('');
    setOrphans(false);
  }

  function pickPeriod(days) {
    const r = periodRange(days);
    setDateFrom(r.from);
    setDateTo(r.to);
  }

  // As duas datas ficam sempre em ordem: passar uma da outra arrasta a outra junto.
  function changeFrom(v) {
    setDateFrom(v);
    if (v && dateTo && v > dateTo) setDateTo(v);
  }
  function changeTo(v) {
    setDateTo(v);
    if (v && dateFrom && v < dateFrom) setDateFrom(v);
  }

  const activePeriod = PERIODS.find((p) => {
    const r = periodRange(p.days);
    return dateFrom === r.from && dateTo === r.to;
  });

  const deletableItems = useMemo(() => items.filter(canDelete), [items]);

  function toggle(item) {
    if (!canDelete(item)) return;
    setSelected((prev) => {
      const next = new Map(prev);
      if (next.has(item.id)) next.delete(item.id); else next.set(item.id, item);
      return next;
    });
  }

  // Com filtro ligado, o checkbox do cabeçalho vale "tudo o que o filtro achou", não só
  // as páginas já carregadas.
  const allSelected = deletableItems.length > 0 && deletableItems.every((i) => selected.has(i.id))
    && (!hasFilters || nextOffset == null || selected.size >= total);

  async function selectAllMatching() {
    const req = reqRef.current;
    setSelectingAll(true);
    try {
      const all = [];
      let offset = 0;
      while (offset != null) {
        const res = await listFiles(buildListParams(filters, offset, SELECT_PAGE));
        // Os filtros mudaram enquanto as páginas eram lidas: esta lista é velha.
        if (req !== reqRef.current) return;
        if (!res || !res.ok) {
          notify((res && res.error) || 'Não foi possível selecionar todos os arquivos.', { kind: 'error' });
          return;
        }
        all.push(...res.data.items);
        offset = res.data.next_offset;
      }
      setSelected(new Map(all.filter(canDelete).map((i) => [i.id, i])));
    } catch {
      if (req === reqRef.current) notify('Não foi possível selecionar todos os arquivos.', { kind: 'error' });
    } finally {
      setSelectingAll(false);
    }
  }

  function toggleAll() {
    if (allSelected) { setSelected(new Map()); return; }
    if (hasFilters && nextOffset != null) { selectAllMatching(); return; }
    setSelected(new Map(deletableItems.map((i) => [i.id, i])));
  }

  const selectedList = useMemo(() => Array.from(selected.values()), [selected]);
  const selectedBytes = selectedList.reduce((n, i) => n + (i.size || 0), 0);
  // Selecionados que estão além das páginas mostradas ("Mostrar mais" ainda não clicado).
  const hiddenSelected = useMemo(() => {
    const shown = new Set(items.map((i) => i.id));
    return selectedList.filter((i) => !shown.has(i.id)).length;
  }, [items, selectedList]);

  function askDelete(list) {
    const ok = list.filter(canDelete);
    if (!ok.length) return;
    setConfirm(summarizeSelection(ok));
  }

  async function doDelete() {
    if (!confirm) return;
    setDeleting(true);
    const done = emptyDeleteResult();
    let error = '';
    try {
      // O servidor aceita um número limitado de ids por chamada: seleção grande vai em lotes.
      for (let i = 0; i < confirm.ids.length; i += DELETE_BATCH) {
        const res = await deleteFiles(confirm.ids.slice(i, i + DELETE_BATCH));
        if (!res || !res.ok) { error = (res && res.error) || 'Não foi possível apagar.'; break; }
        addDeleteResult(done, res.data);
      }
    } catch {
      error = 'Não foi possível apagar.';
    } finally {
      // Falha no meio ainda reporta o que já tinha saído.
      const n = deleteNotice(done, error);
      notify(n.text, { kind: n.kind === 'error' ? 'error' : 'success', timeoutMs: 9000 });
      setDeleting(false);
      setConfirm(null);
      setSelected(new Map());
      loadUsage(true);
      load();
    }
  }

  const rootInfo = ROOTS.find((r) => r.id === root);

  return html`
    <div class="flex flex-col gap-4 min-w-0">
      <${UsageBar} usage=${usage} />

      <section class="bg-wa-bg rounded-xl border border-wa-border shadow-sm overflow-hidden" aria-label="Arquivos">
        <div class="px-4 pt-3 border-b border-wa-border">
          <div class="flex flex-wrap gap-1" role="tablist">
            ${ROOTS.map((r) => html`
              <button type="button" role="tab" aria-selected=${root === r.id}
                onClick=${() => setRoot(r.id)}
                class="px-4 py-2 text-[14px] rounded-t-lg border-b-2 -mb-px ${root === r.id ? 'border-wa-teal text-wa-text font-medium' : 'border-transparent text-wa-secondary hover:text-wa-text'}">
                ${r.label}
              </button>`)}
          </div>
        </div>

        <div class="p-4 flex flex-col gap-3 min-w-0">
          <p class="text-[13px] text-wa-secondary">${rootInfo.hint} Conversas, contatos e configurações nunca são apagados por esta tela.</p>

          <div role="search" aria-label="Filtros de arquivos" class="flex flex-col gap-3">
            <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
              <label class="flex flex-col gap-1 text-[12px] text-wa-secondary min-w-0">
                Buscar por nome ou texto
                <input type="search" value=${search} onInput=${(e) => setSearch(e.target.value)}
                  placeholder="Ex.: contrato, boleto, foto da obra"
                  class=${FIELD} />
              </label>
              <${ContactPicker} text=${contactText} picked=${pickedContact}
                onText=${setContactText}
                onPick=${(c) => { setPickedContact(c); setContactText(''); }}
                onClear=${() => setPickedContact(null)} />
            </div>

            <div class="flex flex-wrap items-end gap-2">
              <label class="flex flex-col gap-1 text-[12px] text-wa-secondary">
                De
                <input type="date" value=${dateFrom} max=${dateTo || undefined} onChange=${(e) => changeFrom(e.target.value)}
                  class=${FIELD} />
              </label>
              <label class="flex flex-col gap-1 text-[12px] text-wa-secondary">
                Até
                <input type="date" value=${dateTo} min=${dateFrom || undefined} onChange=${(e) => changeTo(e.target.value)}
                  class=${FIELD} />
              </label>
              <div class="flex flex-wrap gap-1 pb-0.5" role="group" aria-label="Períodos prontos">
                ${PERIODS.map((p) => html`
                  <button type="button" onClick=${() => pickPeriod(p.days)} aria-pressed=${activePeriod?.id === p.id}
                    class="px-3 py-2 rounded-lg text-[13px] border ${activePeriod?.id === p.id ? 'border-wa-teal text-wa-teal font-medium' : 'border-wa-border text-wa-text hover:bg-wa-hover'}">
                    ${p.label}
                  </button>`)}
              </div>
            </div>

            <div class="flex flex-wrap items-end gap-2">
              ${inboxes.length ? html`
                <label class="flex flex-col gap-1 text-[12px] text-wa-secondary min-w-0">
                  Canal
                  <select value=${inboxId} onChange=${(e) => setInboxId(e.target.value)} class=${FIELD}>
                    <option value="">Todos os canais</option>
                    ${inboxes.map((ib) => html`<option key=${ib.id} value=${String(ib.id)}>${ib.name}</option>`)}
                  </select>
                </label>` : null}
              <label class="flex flex-col gap-1 text-[12px] text-wa-secondary">
                Tipo de conversa
                <select value=${chatType} onChange=${(e) => setChatType(e.target.value)} class=${FIELD}>
                  ${CHAT_TYPES.map((c) => html`<option value=${c.id}>${c.label}</option>`)}
                </select>
              </label>
              <label class="flex flex-col gap-1 text-[12px] text-wa-secondary">
                Tipo de arquivo
                <select value=${kind} onChange=${(e) => setKind(e.target.value)} class=${FIELD}>
                  ${KINDS.map((k) => html`<option value=${k.id}>${k.label}</option>`)}
                </select>
              </label>
              <label class="flex flex-col gap-1 text-[12px] text-wa-secondary">
                Ordenar por
                <select value=${sort} onChange=${(e) => setSort(e.target.value)} class=${FIELD}>
                  ${SORTS.map((s) => html`<option value=${s.id}>${s.label}</option>`)}
                </select>
              </label>
              ${canOrphans ? html`
                <label class="flex items-center gap-2 text-[13px] text-wa-text pb-2 cursor-pointer" title="Arquivos que nenhuma conversa usa mais">
                  <input type="checkbox" checked=${orphans} onChange=${(e) => setOrphans(e.target.checked)} />
                  Só arquivos sem conversa
                </label>` : null}
              ${hasFilters ? html`
                <button type="button" onClick=${clearFilters} class="mb-1 px-3 py-2 rounded-lg text-[13px] text-wa-teal hover:bg-wa-hover underline">Limpar filtros</button>` : null}
            </div>
          </div>

          ${selectingAll ? html`<div class="rounded-lg bg-wa-hover border border-wa-border px-3 py-2 text-[13.5px] text-wa-text animate-pulse-slow" role="status">Selecionando todos os arquivos deste filtro...</div>` : null}

          ${selectedList.length ? html`
            <div class="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-wa-hover border border-wa-border px-3 py-2">
              <span class="min-w-0 text-[13.5px] text-wa-text">${plural(selectedList.length, 'selecionado', 'selecionados')} · ${fmtBytes(selectedBytes)}${hiddenSelected ? ` (${hiddenSelected.toLocaleString('pt-BR')} ainda fora da lista; use “Mostrar mais” para vê-los)` : ''}</span>
              <div class="flex gap-2">
                <button type="button" onClick=${() => setSelected(new Map())} class="px-3 py-1.5 rounded-lg text-[13px] text-wa-secondary hover:bg-wa-panel">Limpar seleção</button>
                <button type="button" onClick=${() => askDelete(selectedList)} class="px-3 py-1.5 rounded-lg text-[13px] font-medium bg-red-600 text-white hover:bg-red-700">Apagar selecionados</button>
              </div>
            </div>` : null}

          ${loading ? html`<div class="py-10 text-center text-[14px] text-wa-secondary animate-pulse-slow">Carregando...</div>`
            : error ? html`<div class="py-8 text-center text-[14px] text-red-600" role="alert">${error}</div>`
            : items.length === 0 ? html`
              <div class="py-10 text-center text-[14px] text-wa-secondary">
                ${hasFilters ? html`
                  <p>Nenhum arquivo encontrado com esses filtros.</p>
                  <button type="button" onClick=${clearFilters} class="mt-2 text-wa-teal underline">Limpar filtros</button>`
                  : 'Nenhum arquivo aqui. O espaço está livre.'}
              </div>`
            : html`
              <div class="hidden md:block overflow-x-auto">
                <table class="w-full text-[13.5px]">
                  <thead>
                    <tr class="text-left text-[12px] uppercase tracking-wider text-wa-secondary border-b border-wa-border">
                      <th class="py-2 pr-2 w-8"><input type="checkbox" checked=${allSelected} disabled=${selectingAll || !deletableItems.length} onChange=${toggleAll} aria-label=${hasFilters ? 'Selecionar todos os arquivos deste filtro' : 'Selecionar todos desta lista'} /></th>
                      <th class="py-2 pr-3">Nome</th>
                      <th class="py-2 pr-3">Conversa</th>
                      <th class="py-2 pr-3">Tipo</th>
                      <th class="py-2 pr-3 text-right">Tamanho</th>
                      <th class="py-2 pr-3 whitespace-nowrap">Data e hora</th>
                      <th class="py-2 w-20"></th>
                    </tr>
                  </thead>
                  <tbody>
                    ${items.map((it) => {
                      const del = canDelete(it);
                      return html`
                      <tr key=${it.id} class="border-b border-wa-border last:border-b-0 hover:bg-wa-hover ${selected.has(it.id) ? 'bg-wa-hover' : ''}">
                        <td class="py-2 pr-2"><input type="checkbox" checked=${selected.has(it.id)} disabled=${!del} title=${del ? undefined : PLUGIN_ONLY_TITLE} onChange=${() => toggle(it)} aria-label=${`Selecionar ${it.name}`} /></td>
                        <td class="py-2 pr-3 max-w-[300px]">
                          <button type="button" onClick=${() => setPreview(it)} class="flex items-center gap-2 text-left text-wa-text hover:text-wa-teal max-w-full min-w-0" title=${it.name}>
                            <span aria-hidden="true">${KIND_ICON[it.kind] || '📄'}</span>
                            <span class="truncate">${it.name}</span>
                          </button>
                          ${it.plugin ? html`<div class="mt-1"><${PluginBadge} plugin=${it.plugin} /></div>` : null}
                          ${it.snippet ? html`<div class="mt-0.5 text-[12px] text-wa-secondary truncate" title=${it.snippet}>na mensagem: ${it.snippet}</div>` : null}
                        </td>
                        <td class="py-2 pr-3 max-w-[220px]"><${Origin} item=${it} /></td>
                        <td class="py-2 pr-3 text-wa-secondary">${KIND_LABEL[it.kind] || 'Arquivo'}</td>
                        <td class="py-2 pr-3 text-right text-wa-text whitespace-nowrap">${fmtBytes(it.size)}</td>
                        <td class="py-2 pr-3 text-wa-secondary whitespace-nowrap">${fmtDateTime(it.mtime)}</td>
                        <td class="py-2 text-right">
                          ${del ? html`
                            <button type="button" onClick=${() => askDelete([it])} class="px-2 py-1 rounded text-[12.5px] text-red-600 hover:bg-wa-panel">Apagar</button>`
                            : html`<button type="button" disabled title=${PLUGIN_ONLY_TITLE} class="px-2 py-1 rounded text-[12.5px] text-wa-secondary opacity-50 cursor-not-allowed">Apagar</button>`}
                        </td>
                      </tr>`;
                    })}
                  </tbody>
                </table>
              </div>

              <ul class="md:hidden flex flex-col gap-2">
                ${items.map((it) => {
                  const del = canDelete(it);
                  return html`
                  <li key=${it.id} class="rounded-lg border border-wa-border bg-wa-panel p-3 ${selected.has(it.id) ? 'ring-2 ring-wa-teal' : ''}">
                    <div class="flex items-start gap-3">
                      <input type="checkbox" class="mt-1" checked=${selected.has(it.id)} disabled=${!del} title=${del ? undefined : PLUGIN_ONLY_TITLE} onChange=${() => toggle(it)} aria-label=${`Selecionar ${it.name}`} />
                      <div class="min-w-0 flex-1">
                        <button type="button" onClick=${() => setPreview(it)} class="flex items-center gap-2 text-left text-[14px] text-wa-text max-w-full min-w-0">
                          <span aria-hidden="true">${KIND_ICON[it.kind] || '📄'}</span>
                          <span class="truncate">${it.name}</span>
                        </button>
                        ${it.plugin ? html`<div class="mt-1"><${PluginBadge} plugin=${it.plugin} /></div>` : null}
                        ${it.snippet ? html`<div class="mt-1 text-[12.5px] text-wa-secondary break-words" title=${it.snippet}>na mensagem: ${it.snippet}</div>` : null}
                        <div class="mt-1 text-[12.5px] text-wa-secondary"><${Origin} item=${it} /></div>
                        <div class="mt-1 text-[12.5px] text-wa-secondary">${KIND_LABEL[it.kind] || 'Arquivo'} · ${fmtBytes(it.size)} · ${fmtDateTime(it.mtime)}</div>
                      </div>
                      ${del ? html`
                        <button type="button" onClick=${() => askDelete([it])} class="shrink-0 px-2 py-1 rounded text-[12.5px] text-red-600 hover:bg-wa-hover">Apagar</button>`
                        : html`<button type="button" disabled title=${PLUGIN_ONLY_TITLE} class="shrink-0 px-2 py-1 rounded text-[12.5px] text-wa-secondary opacity-50 cursor-not-allowed">Apagar</button>`}
                    </div>
                  </li>`;
                })}
              </ul>

              <div class="flex flex-wrap items-center justify-between gap-2 pt-1 text-[12.5px] text-wa-secondary">
                <span>Mostrando ${items.length.toLocaleString('pt-BR')} de ${total.toLocaleString('pt-BR')} · ${fmtBytes(totalBytes)}${hasFilters ? ' neste filtro' : ''}</span>
                ${nextOffset != null ? html`
                  <button type="button" onClick=${loadMore} disabled=${loadingMore}
                    class="px-3 py-1.5 rounded-lg border border-wa-border text-wa-text hover:bg-wa-hover disabled:opacity-60">
                    ${loadingMore ? 'Carregando...' : 'Mostrar mais'}
                  </button>` : null}
              </div>`}
        </div>
      </section>

      ${preview ? html`<${Preview} item=${preview} onClose=${() => setPreview(null)} />` : null}
      ${confirm ? html`<${ConfirmDelete} summary=${confirm} busy=${deleting} onCancel=${() => setConfirm(null)} onConfirm=${doDelete} />` : null}
    </div>
  `;
}

export default FileManager;
