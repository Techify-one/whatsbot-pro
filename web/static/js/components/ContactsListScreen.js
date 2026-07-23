// Tela "Contatos" (full-page) — lista todos os contatos em ordem alfabética,
// com busca, "Ver detalhes" (painel editável com todos os atributos, incluindo
// os personalizados), "Iniciar atendimento" (abre o chat no hub de atendimentos) e
// importar/exportar contatos via CSV. Acessível pelo menu da lista de atendimentos.
//
// Paginação: 15 contatos por página, numa barra fixa no rodapé (sticky — acompanha a
// rolagem): "Exibindo X - Y de N contatos" à esquerda; à direita Primeira/Anterior, o
// campo "ir para a página" + "de N páginas" e Próxima/Última. Servida pelo
// envelope {items, total, has_more} de GET /api/contacts?limit&offset&sort=name. A
// página vive na URL como ?page=N (1-indexed). Substituiu o scroll infinito do plano 50.
//
// Deep-link: abrir o detalhe reflete na URL como /contacts/{id} (id do contato);
// back/forward reabre/fecha o painel. A lista em si fica em /contacts.
import { h } from 'preact';
import { useState, useEffect, useMemo, useCallback, useRef } from 'preact/hooks';
import htm from 'htm';
import { DefaultAvatar, GroupAvatar, SearchIcon, PlusIcon } from './contacts/icons.js';
import { avatarUrl } from './contacts/utils.js';
import { ContactInfoPanel } from './contacts/ContactInfoPanel.js';
import { ContactFilterDialog } from './contacts/ContactFilterDialog.js';
import { useContactSubtitle } from './contacts/hooks/useContactSubtitle.js';
import { useDeepLink } from '../hooks/useDeepLink.js';
import { useUrlState } from '../hooks/useUrlState.js';
import { readParams, writeParams, str, int, json } from '../services/urlState.js';
import {
  getContacts, getContact, getTags, deleteContact, checkPhone,
  updateContactInfo, getContactConversation, exportContacts, importContacts,
  getCustomAttributes,
} from '../services/api.js';
import { matchesAdvFilters } from '../services/conversationRows.js';
import {
  buildContactFilterParams, isContactFilterServerExpressible,
} from '../services/conversationFilterSpec.js';
import { contactTypeMeta, contactTypeBadge } from '../services/contactTypes.js';
import { useProviderCatalog } from '../hooks/useProviderCatalog.js';
import { formatPhoneDisplay } from '../utils/phone.js';
import { hasPermission } from '../utils/permissions.js';

const html = htm.bind(h);

// Contatos por página (paginação clássica Primeira/Anterior/Próxima/Última — não mais
// scroll infinito).
const PAGE_SIZE = 15;

// Botão da barra de paginação (mesmo visual dos botões de página da tela de Auditoria).
const PAGE_BTN = 'px-2.5 py-1 rounded border border-wa-border text-wa-text hover:bg-wa-hover '
  + 'disabled:opacity-30 disabled:cursor-not-allowed transition-colors whitespace-nowrap';

// Casefold + strip accents (espelha o `_fold` do backend) para a busca casar
// independente de acento/caixa.
function foldStr(s) {
  return (s || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
}

// Funnel/tune icon for the "Filtros" toolbar button.
function FilterIcon() {
  return html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor">
    <path d="M3 17v2h6v-2H3zM3 5v2h10V5H3zm10 16v-2h8v-2h-8v-2h-2v6h2zM7 9v2H3v2h4v2h2V9H7zm14 4v-2H11v2h10zm-6-4h2V7h4V5h-4V3h-2v6z"/></svg>`;
}

// R\u00f3tulo amig\u00e1vel de uma cl\u00e1usula de filtro ativa (chip). `tag` \u00e9 multi-select;
// `cattr:contact:<key>` usa o display_name da defini\u00e7\u00e3o carregada.
function clauseChipLabel(cl, contactAttrDefs) {
  const OP_SEP = { ne: ' \u2260 ', contains: ' cont\u00e9m ', not_contains: ' n\u00e3o cont\u00e9m ', gt: ' > ', lt: ' < ' };
  const sep = OP_SEP[cl.op] || ': ';
  const list = Array.isArray(cl.value) ? cl.value : [cl.value];
  const m = String(cl.dim).match(/^cattr:contact:(.+)$/);
  if (m) {
    const def = (contactAttrDefs || []).find(d => d.attribute_key === m[1]);
    const name = def ? (def.display_name || m[1]) : m[1];
    return `${name}${sep}${list.join(', ')}`;
  }
  // Email/Profissão/Empresa/Endereço chegam como cattr:contact:* (atributos),
  // tratados acima. Só os cores 'tag'/'contact_type' restam aqui.
  const CORE_LABELS = { tag: 'Etiqueta', contact_type: 'Tipo' };
  // Tipo de contato exibe o rótulo amigável do catálogo (WhatsApp/Telegram/…).
  const vals = cl.dim === 'contact_type' ? list.map(v => contactTypeMeta(v).label) : list;
  return `${CORE_LABELS[cl.dim] || cl.dim}${sep}${vals.join(', ')}`;
}


// Navega para uma rota do SPA reusando o mesmo mecanismo do app.js (pushState +
// popstate re-sincroniza a aba a partir da URL).
function navigate(path) {
  if (window.location.pathname !== path) {
    history.pushState(null, '', path);
    window.dispatchEvent(new PopStateEvent('popstate'));
  }
}

// ── Modal "Novo contato" ─────────────────────────────────────────────────
function NewContactModal({ onClose, onCreated }) {
  const [phone, setPhone] = useState('');
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  async function handleCreate() {
    const digits = phone.replace(/\D/g, '');
    if (digits.length < 10) { setError('Informe DDD + número (mín. 10 dígitos).'); return; }
    setSaving(true);
    setError(null);
    try {
      // check-phone canonicaliza o número, valida no WhatsApp e já cria o contato
      // (create=true) quando registrado.
      const res = await checkPhone(digits, true);
      if (!res.ok) { setError(res.error || 'Falha ao verificar o número.'); setSaving(false); return; }
      if (!res.data.registered) {
        setError('Este número não está no WhatsApp, então não pode virar contato.');
        setSaving(false);
        return;
      }
      const canonical = res.data.phone || digits;
      const trimmedName = name.trim();
      if (trimmedName) {
        await updateContactInfo(canonical, { name: trimmedName });
      }
      onCreated(canonical);
    } catch (e) {
      setError(String(e));
      setSaving(false);
    }
  }

  return html`
    <div class="fixed inset-0 z-[120] bg-black/50 flex items-center justify-center p-4" onClick=${onClose}>
      <div class="bg-wa-panel rounded-xl shadow-xl border border-wa-border w-full max-w-md" onClick=${(e) => e.stopPropagation()}>
        <div class="flex items-center justify-between p-4 border-b border-wa-border">
          <span class="text-[16px] font-semibold text-wa-text">Novo contato</span>
          <button onClick=${onClose} class="w-[34px] h-[34px] rounded-full flex items-center justify-center text-wa-secondary hover:bg-wa-hover transition-colors" title="Fechar">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
          </button>
        </div>
        <div class="p-4 space-y-4">
          <div>
            <label class="text-wa-iconActive text-[13px] font-medium block mb-1">Telefone *</label>
            <input
              type="tel"
              value=${phone}
              onInput=${(e) => setPhone(e.target.value)}
              onKeyDown=${(e) => { if (e.key === 'Enter') { e.preventDefault(); handleCreate(); } }}
              placeholder="Ex: 11 99999-9999"
              autoFocus
              class="wa-field w-full text-[15px] rounded-[8px] px-3 py-2 border border-wa-border outline-none focus:border-wa-iconActive transition-colors"
            />
            <div class="text-[12px] text-wa-secondary mt-1">DDI 55 é assumido se você não informar.</div>
          </div>
          <div>
            <label class="text-wa-iconActive text-[13px] font-medium block mb-1">Nome</label>
            <input
              type="text"
              value=${name}
              onInput=${(e) => setName(e.target.value)}
              onKeyDown=${(e) => { if (e.key === 'Enter') { e.preventDefault(); handleCreate(); } }}
              placeholder="Nome do contato (opcional)"
              class="wa-field w-full text-[15px] rounded-[8px] px-3 py-2 border border-wa-border outline-none focus:border-wa-iconActive transition-colors"
            />
          </div>
          ${error ? html`
            <div class="text-[13px] text-red-600 bg-red-50 border border-red-200 rounded-[8px] px-3 py-2">${error}</div>
          ` : null}
        </div>
        <div class="p-4 border-t border-wa-border flex justify-end gap-2">
          <button onClick=${onClose} class="px-4 py-[8px] rounded-lg text-[14px] text-wa-secondary hover:bg-wa-hover transition-colors">Cancelar</button>
          <button
            onClick=${handleCreate}
            disabled=${saving}
            class="bg-wa-teal text-white text-[14px] font-medium px-4 py-[8px] rounded-lg hover:opacity-90 transition-opacity disabled:opacity-50"
          >${saving ? 'Criando...' : 'Criar contato'}</button>
        </div>
      </div>
    </div>
  `;
}

// ── Painel de detalhes (editável) ─────────────────────────────────────────
// Reusa o ContactInfoPanel do hub de atendimentos (mesma UX, atributos personalizados,
// tags, observações, exclusão) dentro de um overlay full-screen.
function ContactDetailOverlay({ contact, globalTags, onGlobalTagsChange, onClose, onSaved, onDeleted, onStartConversation }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    getContact(contact.phone, false)
      .then((res) => { if (alive) setData(res && res.ok ? res.data : null); })
      .catch(() => { if (alive) setData(null); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [contact.phone]);

  const info = (data && data.info) || {};
  const contactTags = (data && data.tags) || contact.tags || [];

  return html`
    <div class="fixed inset-0 z-[120]" onClick=${onClose}>
      <div class="absolute inset-0 bg-black/40"></div>
      <div onClick=${(e) => e.stopPropagation()}>
        ${loading ? html`
          <div class="absolute inset-0 flex justify-end">
            <div class="w-full lg:w-[400px] h-full bg-wa-panel flex items-center justify-center shadow-xl animate-slide-in-right text-wa-secondary text-[14px] animate-pulse-slow">
              Carregando...
            </div>
          </div>
        ` : html`
          <${ContactInfoPanel}
            phone=${contact.phone}
            info=${info}
            contactTags=${contactTags}
            globalTags=${globalTags}
            onGlobalTagsChange=${onGlobalTagsChange}
            isGroup=${!!contact.is_group}
            groupName=${contact.name}
            avatarV=${contact.avatar_v}
            onClose=${onClose}
            onSave=${(savedInfo, savedTags) => onSaved(contact, savedInfo, savedTags)}
            onDeleteContact=${() => onDeleted(contact)}
          />
        `}
        <!-- Ação "Iniciar atendimento" flutuante (o ContactInfoPanel não a tem) -->
        ${!loading && !contact.is_group ? html`
          <button
            onClick=${() => onStartConversation(contact)}
            title="Iniciar conversa"
            class="fixed bottom-6 right-6 lg:right-[424px] z-[121] flex items-center gap-2 bg-wa-teal text-white text-[14px] font-medium px-4 py-3 rounded-full shadow-lg hover:opacity-90 transition-opacity"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg>
            Iniciar conversa
          </button>
        ` : null}
      </div>
    </div>
  `;
}

// ── Linha da lista ────────────────────────────────────────────────────────
// Extraída em componente próprio para poder chamar o hook `useContactSubtitle`
// por linha: o subtítulo sob o nome é o telefone formatado por padrão, mas um
// plugin pode reescrevê-lo pelo seam genérico `filter.contact.headerSubtitle`
// (o widget de site mapeia o token opaco `wsess_…` → um código curto WEB-XXXXXX).
// Só exibição — o telefone segue sendo a identidade de roteamento.
function ContactRow({ c, onOpenDetail, onStartConversation }) {
  const resolved = useContactSubtitle(c.phone, { channelId: c.channel_id, contact: c });
  // `resolved !== c.phone` ⇒ um plugin sobrescreveu (mostra o código curto);
  // senão cai no telefone formatado (contatos normais mantêm +55 (AA) …).
  const phoneLabel = resolved !== c.phone ? resolved : formatPhoneDisplay(c.phone);
  const badge = contactTypeBadge(c.contact_type);
  return html`
    <div
      class="flex items-center gap-4 bg-wa-bg border border-wa-border rounded-2xl px-5 py-4 shadow-sm hover:shadow transition-shadow"
    >
      <!-- Avatar (circle, fixed size) -->
      <div class="w-[52px] h-[52px] rounded-full overflow-hidden shrink-0">
        ${c.is_group
          ? html`<${GroupAvatar} size=${52} avatarUrl=${avatarUrl(c.phone, c.avatar_v)} />`
          : html`<${DefaultAvatar} size=${52} avatarUrl=${avatarUrl(c.phone, c.avatar_v)} />`}
      </div>

      <!-- Nome + contato + Ver detalhes -->
      <div class="min-w-0 flex-1">
        <div class="flex items-center gap-2 min-w-0">
          <div class="text-[16px] font-semibold text-wa-text truncate">
            ${c.name || phoneLabel || 'Sem nome'}
          </div>
          <span
            class="shrink-0 text-[10px] font-semibold rounded-full px-[6px] py-[1px] leading-[15px] ${badge.className}"
            style=${badge.style}
            title="Tipo do contato (canal de origem)"
          >${badge.label}</span>
        </div>
        <div class="flex items-center flex-wrap gap-x-2 gap-y-[2px] text-[13px] mt-[2px]">
          <span class="text-wa-secondary truncate max-w-full">
            ${c.email
              ? c.email
              : (c.is_group ? 'Grupo' : phoneLabel)}
          </span>
          <span class="text-wa-border">|</span>
          <button
            onClick=${() => onOpenDetail(c)}
            class="text-wa-teal font-medium hover:underline shrink-0"
          >Ver detalhes</button>
        </div>
        ${c.email && !c.is_group ? html`
          <div class="text-[13px] text-wa-secondary truncate mt-[1px]">
            ${phoneLabel}
          </div>
        ` : null}
      </div>

      <!-- Iniciar atendimento -->
      <button
        onClick=${() => onStartConversation(c)}
        title="Iniciar conversa"
        class="w-[38px] h-[38px] rounded-full flex items-center justify-center text-wa-teal hover:bg-wa-teal/10 transition-colors shrink-0"
      >
        <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg>
      </button>
    </div>
  `;
}

// Deep-link do estado da lista de contatos (Plano 24): busca + filtro avançado +
// página na query legível. `adv` guarda só as cláusulas (JSON), omitido quando vazio.
// `page` é 1-indexed na URL (o estado interno é 0-indexed) e some quando é a 1ª.
const CONTACTS_URL_SCHEMA = [
  str('search', ''),
  int('page', 1),
  json('adv', { isDefault: (v) => !Array.isArray(v) || v.length === 0 }),
];

// ── Tela principal ───────────────────────────────────────────────────────
export default function ContactsListScreen({ initialEntity = null, currentUser = null }) {
  useProviderCatalog();  // re-render quando o catálogo de providers carregar (tipos de contato)
  const canImport = hasPermission(currentUser, 'contact.import');
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');
  // plano 62 F3: `search` reflete o input NA HORA (UX + URL); `debouncedSearch` é o que
  // alimenta o fetch — 1 request por pausa de digitação.
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [page, setPage] = useState(0);              // página atual, 0-indexed
  const [contacts, setContacts] = useState([]);     // itens da página atual
  const [total, setTotal] = useState(0);            // universo (p/ contar as páginas)
  const [loading, setLoading] = useState(true);
  const [reloadTick, setReloadTick] = useState(0);  // plano 50: força refetch (delete/import)
  const [detail, setDetail] = useState(null); // contato aberto no painel
  const [showCreate, setShowCreate] = useState(false);
  const [globalTags, setGlobalTags] = useState({});
  const [importing, setImporting] = useState(false);
  const [toast, setToast] = useState(null); // {kind:'ok'|'err', text}
  const [showImport, setShowImport] = useState(false); // modal de importação
  const [importError, setImportError] = useState(null); // erro dentro do modal
  const fileInputRef = useRef(null);
  // Filtros (espelham o "Filtrar atendimentos", mas só refletem nos contatos).
  const [advFilters, setAdvFilters] = useState([]); // [{ id, dim, op, value }]
  const [showFilters, setShowFilters] = useState(false); // dropdown do construtor
  const [contactAttrDefs, setContactAttrDefs] = useState([]); // atributos de contato (dinâmicos)
  const filterRef = useRef(null);

  // Deep-link do estado da lista → URL (Plano 24). Busca + filtro avançado na
  // query legível; hidrata no mount/back-forward, reflete ao mudar (replaceState).
  useUrlState({
    read: () => readParams(window.location.search, CONTACTS_URL_SCHEMA),
    apply: (s) => {
      setSearch(s.search);
      setPage(Math.max(0, (s.page || 1) - 1));
      setAdvFilters(Array.isArray(s.adv) ? s.adv.map((f, i) => ({ ...f, id: `u${i}` })) : []);
    },
    serialize: () => writeParams({
      search,
      page: page + 1,
      adv: (advFilters || []).map(({ id, ...rest }) => rest),
    }, CONTACTS_URL_SCHEMA),
    deps: [search, page, advFilters],
  });

  // plano 62 F3: debounce de 300ms entre digitar e disparar a busca no servidor.
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  // plano 69 F6 — filtro avançado server-side: quando TODAS as cláusulas são
  // expressáveis (tag/contact_type/cattr:contact:*), mandamos ao servidor, que corta a
  // lista E o `total` — a paginação passa a contar o universo JÁ filtrado. Cláusula não
  // coberta (ex.: etiqueta "≠") ⇒ fallback cliente sobre a página carregada (e aí sim
  // uma página pode exibir MENOS de 15 linhas, porque o total ainda é o não-filtrado).
  const filterServerMode = useMemo(
    () => advFilters.length > 0 && isContactFilterServerExpressible(advFilters),
    [advFilters]);
  const filterParams = useMemo(
    () => (filterServerMode ? buildContactFilterParams(advFilters) : null),
    [filterServerMode, advFilters]);
  const filterKey = useMemo(
    () => (filterParams ? JSON.stringify(filterParams) : ''),
    [filterParams]);

  // PAGINAÇÃO CLÁSSICA (15 por página). A busca vai pro servidor (q + limit/offset +
  // ordem alfabética via sort=name); o servidor devolve SEMPRE uma página de PAGE_SIZE
  // mais o `total` do universo, nunca a tabela inteira.
  // plano 62 F3: cada fetch aborta o anterior ainda em voo (página trocada ou busca
  // redigitada durante um request lento) — libera o slot do browser. AbortError não é
  // erro de UI e não pode zerar a lista: o request abortado simplesmente não escreve.
  const listAbortRef = useRef(null);
  useEffect(() => {
    let alive = true;
    if (listAbortRef.current) listAbortRef.current.abort();
    const ctrl = new AbortController();
    listAbortRef.current = ctrl;
    setLoading(true);
    getContacts(debouncedSearch, false, {
      limit: PAGE_SIZE, offset: page * PAGE_SIZE, sort: 'name',
      signal: ctrl.signal, filters: filterParams || undefined,
    })
      .then((res) => {
        if (!alive) return;
        if (res && res.ok) {
          setError(null);
          setContacts((res.data && res.data.items) || []);
          setTotal((res.data && res.data.total) || 0);
        } else {
          setError((res && res.error) || 'Falha ao carregar contatos');
          setContacts([]);
          setTotal(0);
        }
      })
      .catch((e) => {
        if (!alive || (e && e.name === 'AbortError')) return;
        setError(String(e));
        setContacts([]);
        setTotal(0);
      })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [debouncedSearch, page, reloadTick, filterKey]);

  // Busca/filtros/reload (delete/import) sempre voltam pra 1ª página — senão o usuário
  // ficaria numa página que não existe mais no novo universo de resultados. Observa a
  // busca JÁ debounced (o `search` cru zeraria a página a cada tecla).
  const firstLoad = useRef(true);
  useEffect(() => {
    // No mount a página vem da URL (deep-link); só resets POSTERIORES zeram.
    if (firstLoad.current) { firstLoad.current = false; return; }
    setPage(0);
    // eslint-disable-next-line
  }, [debouncedSearch, filterKey, advFilters, reloadTick]);

  // reload após ações (delete/import) força a 1ª página de novo.
  const reload = useCallback(() => setReloadTick((t) => t + 1), []);

  // Tags globais — carregadas uma vez no mount (independente do modo).
  useEffect(() => {
    let alive = true;
    getTags().then((res) => { if (alive && res.ok) setGlobalTags(res.data || {}); });
    return () => { alive = false; };
  }, []);

  // Auto-esconde o toast de feedback após alguns segundos.
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 6000);
    return () => clearTimeout(t);
  }, [toast]);

  // Definições de atributos personalizados de CONTATO (plano 05) — viram dimensões
  // de filtro dinâmicas. Recarrega ao ouvir o evento global de mudança (criar/editar
  // um atributo em Configurações reflete aqui sem reload).
  useEffect(() => {
    let alive = true;
    const load = () => {
      getCustomAttributes('contact')
        .then(r => { if (alive && r && r.ok && Array.isArray(r.data)) setContactAttrDefs(r.data); })
        .catch(() => {});
    };
    load();
    window.addEventListener('whatsbot:custom-attributes-changed', load);
    return () => { alive = false; window.removeEventListener('whatsbot:custom-attributes-changed', load); };
  }, []);

  // Fecha o dropdown do construtor de filtros em clique-fora / Escape.
  useEffect(() => {
    if (!showFilters) return undefined;
    const onDoc = (e) => { if (filterRef.current && !filterRef.current.contains(e.target)) setShowFilters(false); };
    const onKey = (e) => { if (e.key === 'Escape') setShowFilters(false); };
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey); };
  }, [showFilters]);

  async function handleExport() {
    try {
      const blob = await exportContacts();
      if (!blob) { setToast({ kind: 'err', text: 'Falha ao exportar contatos.' }); return; }
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'contatos.csv';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setToast({ kind: 'err', text: 'Falha ao exportar contatos.' });
    }
  }

  async function handleImportFile(e) {
    const file = e.target.files && e.target.files[0];
    e.target.value = ''; // permite re-selecionar o mesmo arquivo
    if (!file) return;
    // Só aceita CSV (alguns navegadores deixam burlar o accept do input).
    const name = (file.name || '').toLowerCase();
    const isCsv = name.endsWith('.csv') || (file.type || '').includes('csv');
    if (!isCsv) {
      setImportError('Apenas arquivos .csv são aceitos. Selecione um arquivo CSV.');
      return;
    }
    setImportError(null);
    setImporting(true);
    setToast(null);
    try {
      const res = await importContacts(file);
      if (res && res.ok) {
        const d = res.data || {};
        const parts = [];
        if (d.imported) parts.push(`${d.imported} novo(s)`);
        if (d.updated) parts.push(`${d.updated} atualizado(s)`);
        if (d.skipped) parts.push(`${d.skipped} ignorado(s)`);
        setToast({
          kind: 'ok',
          text: `Importação concluída: ${parts.join(', ') || 'nenhum contato'}.`,
        });
        setShowImport(false);
        await reload();
      } else {
        setImportError((res && res.error) || 'Falha ao importar contatos.');
      }
    } catch (err) {
      setImportError('Falha ao importar contatos.');
    } finally {
      setImporting(false);
    }
  }

  // Deep-link do detalhe: /contacts/{id} abre o painel daquele contato.
  const open = useCallback((sel) => {
    if (sel && sel.id != null) {
      const c = contacts.find((x) => x.id === sel.id);
      setDetail(c || null);
    } else {
      setDetail(null);
    }
  }, [contacts]);

  const push = useDeepLink({
    tab: 'contatos',
    resolve: (initialEntity && initialEntity.tab === 'contatos') ? initialEntity : null,
    ready: !loading && contacts.length > 0,
    open,
  });

  function openDetail(contact) {
    setDetail(contact);
    push({ id: contact.id });
  }

  function closeDetail() {
    setDetail(null);
    push(null);
  }

  // plano 69 F6: quando o filtro é server-expressável, o servidor já cortou a lista +
  // o total — NÃO re-filtrar (senão encolheria a página server-side). Só no fallback
  // (dim/op não coberto, ex.: etiqueta "≠") filtramos no cliente sobre os carregados.
  // `matchesAdvFilters` lê `tags`/`custom_attributes` de cada contato (vêm no payload).
  const pageItems = useMemo(() => {
    if (!advFilters.length || filterServerMode) return contacts;
    const now = Math.floor(Date.now() / 1000);
    return contacts.filter((c) => matchesAdvFilters(c, advFilters, now));
  }, [contacts, advFilters, filterServerMode]);

  // Total de páginas (mínimo 1), se ainda há página seguinte e a faixa exibida
  // ("Exibindo 31 - 45 de 69 contatos") — sempre sobre o universo do SERVIDOR.
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const hasNext = (page + 1) * PAGE_SIZE < total;
  const rangeFrom = total === 0 ? 0 : page * PAGE_SIZE + 1;
  const rangeTo = Math.min(total, (page + 1) * PAGE_SIZE);

  // Campo "ir para a página": editável enquanto se digita, aplicado no Enter/blur com
  // clamp em [1, totalPages]. Um valor inválido volta pra página atual.
  const [pageInput, setPageInput] = useState('1');
  useEffect(() => { setPageInput(String(page + 1)); }, [page]);
  const commitPageInput = useCallback(() => {
    const n = parseInt(pageInput, 10);
    if (Number.isNaN(n)) { setPageInput(String(page + 1)); return; }
    const target = Math.min(Math.max(1, n), totalPages) - 1;
    setPage(target);
    setPageInput(String(target + 1));
  }, [pageInput, page, totalPages]);

  // Abre o chat do contato no hub. Resolve o atendimento ativo (se houver) e navega
  // por /conversations/{id}; sem atendimento ainda, cai na raiz do hub.
  async function startConversation(contact) {
    closeDetail();
    try {
      const res = await getContactConversation(contact.phone, { includeClosed: true });
      const conv = res && res.ok ? res.data.conversation : null;
      if (conv && conv.id != null) { navigate(`/conversations/${conv.id}`); return; }
    } catch { /* fallthrough */ }
    navigate('/');
  }

  // Salvou no painel: reflete nome/email/tags/atributos na linha da lista. Email é
  // atributo personalizado agora — lê do custom_attributes salvo (top-level fica
  // só pra exibição na lista).
  function handleSaved(contact, savedInfo, savedTags) {
    const savedAttrs = savedInfo.custom_attributes || {};
    const savedEmail = savedAttrs.email ?? savedInfo.email;
    setContacts((prev) => prev.map((c) => c.id === contact.id
      ? { ...c, name: savedInfo.name ?? c.name, email: savedEmail ?? c.email, custom_attributes: savedAttrs, tags: savedTags ?? c.tags }
      : c));
    setDetail((d) => (d && d.id === contact.id
      ? { ...d, name: savedInfo.name ?? d.name, email: savedEmail ?? d.email, custom_attributes: savedAttrs, tags: savedTags ?? d.tags }
      : d));
  }

  // Excluiu no painel: remove da lista e fecha.
  async function handleDeleted(contact) {
    const res = await deleteContact(contact.phone);
    if (res && res.ok) {
      setContacts((prev) => prev.filter((c) => c.id !== contact.id));
      closeDetail();
    }
  }

  return html`
    <div>
      <!-- Importar / Exportar -->
      <div class="flex items-center justify-end gap-2 mb-3 flex-wrap">
        ${canImport ? html`
          <input
            ref=${fileInputRef}
            type="file"
            accept=".csv,text/csv"
            class="hidden"
            onChange=${handleImportFile}
          />
          <button
            onClick=${() => { setImportError(null); setShowImport(true); }}
            disabled=${importing}
            class="flex items-center gap-2 text-[14px] font-medium px-4 py-[8px] rounded-lg border border-wa-border text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M19 13v6H5v-6H3v6c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2v-6h-2zM11 3v9.17l-2.59-2.58L7 11l5 5 5-5-1.41-1.41L13 12.17V3h-2z" transform="rotate(180 12 12)"/></svg>
            ${importing ? 'Importando...' : 'Importar contatos'}
          </button>
        ` : null}
        <button
          onClick=${handleExport}
          class="flex items-center gap-2 text-[14px] font-medium px-4 py-[8px] rounded-lg border border-wa-border text-wa-text hover:bg-wa-hover transition-colors"
        >
          <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M19 13v6H5v-6H3v6c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2v-6h-2zM11 3v9.17l-2.59-2.58L7 11l5 5 5-5-1.41-1.41L13 12.17V3h-2z"/></svg>
          Exportar contatos
        </button>
      </div>

      ${toast ? html`
        <div class=${`mb-3 text-[13px] px-3 py-2 rounded-lg border ${toast.kind === 'ok'
          ? 'bg-wa-teal/10 border-wa-teal/30 text-wa-teal'
          : 'bg-red-500/10 border-red-500/30 text-red-500'}`}>
          ${toast.text}
        </div>
      ` : null}

      <!-- Busca + Filtros + Novo contato -->
      <div class="mb-3 flex items-center gap-3">
        <div class="flex-1 flex items-center bg-wa-bg rounded-lg h-[42px] px-[12px] gap-[10px] border border-wa-border">
          <${SearchIcon} />
          <input
            type="text"
            placeholder="Pesquisar contatos..."
            value=${search}
            onInput=${(e) => setSearch(e.target.value)}
            class="bg-transparent border-none outline-none text-wa-text text-[14px] w-full placeholder-wa-secondary"
          />
        </div>
        <div ref=${filterRef} class="relative shrink-0">
          <button
            onClick=${() => setShowFilters((o) => !o)}
            class="flex items-center gap-2 h-[42px] px-4 rounded-lg border text-[14px] font-medium transition-colors ${advFilters.length
              ? 'bg-wa-teal/15 border-wa-teal/40 text-wa-teal'
              : 'border-wa-border text-wa-text hover:bg-wa-hover'}"
            title="Filtrar contatos"
          >
            <${FilterIcon} />
            <span class="hidden sm:inline">Filtros</span>
            ${advFilters.length ? html`<span class="min-w-[18px] h-[18px] px-1 rounded-full bg-wa-teal text-white text-[11px] font-semibold flex items-center justify-center">${advFilters.length}</span>` : null}
          </button>
          ${showFilters ? html`
            <div class="absolute z-[70] mt-1 right-0 w-[600px] max-w-[90vw] bg-wa-panel rounded-xl shadow-2xl border border-wa-border p-4">
              <${ContactFilterDialog}
                filters=${advFilters}
                tagNames=${Object.keys(globalTags || {})}
                contactAttrDefs=${contactAttrDefs}
                onApply=${setAdvFilters}
                onClose=${() => setShowFilters(false)}
              />
            </div>
          ` : null}
        </div>
        <button
          onClick=${() => setShowCreate(true)}
          class="flex items-center gap-2 bg-wa-teal text-white text-[14px] font-medium px-4 h-[42px] rounded-lg hover:opacity-90 transition-opacity shrink-0"
        >
          <${PlusIcon} />
          <span class="hidden sm:inline">Novo contato</span>
        </button>
      </div>

      ${advFilters.length ? html`
        <div class="mb-4 flex flex-wrap items-center gap-1.5">
          ${advFilters.map((cl) => html`
            <span key=${cl.id}
              class="inline-flex items-center gap-1 max-w-full text-[12px] bg-wa-hover text-wa-text rounded-full pl-2.5 pr-1 py-0.5 border border-wa-border">
              <span class="truncate max-w-[220px]" title=${clauseChipLabel(cl, contactAttrDefs)}>${clauseChipLabel(cl, contactAttrDefs)}</span>
              <button onClick=${() => setAdvFilters((fs) => fs.filter((x) => x.id !== cl.id))} title="Remover este filtro"
                class="shrink-0 w-[16px] h-[16px] flex items-center justify-center rounded-full text-wa-secondary hover:bg-wa-border hover:text-red-400 transition-colors">
                <svg viewBox="0 0 24 24" width="11" height="11" fill="currentColor"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
              </button>
            </span>
          `)}
          <button onClick=${() => setAdvFilters([])}
            class="text-[12px] text-wa-secondary hover:text-red-400 hover:underline ml-1">Limpar filtros</button>
        </div>
      ` : null}

      ${error ? html`
        <div class="text-center text-red-400 py-8 text-[14px]">${error}</div>
      ` : loading ? html`
        <div class="text-center text-wa-secondary py-12 animate-pulse-slow text-[14px]">Carregando contatos...</div>
      ` : html`
        <!-- Lista + barra de paginação. A barra é filha DESTE container (e não irmã
             dele) de propósito: sticky bottom-0 só flutua enquanto o bloco que a
             contém ainda tem altura abaixo, então ficar dentro da lista é o que a faz
             acompanhar a rolagem em vez de aparecer só no fim. -->
        <div class="flex flex-col gap-3">
          ${pageItems.length === 0 ? html`
            <div class="text-center text-wa-secondary py-12 text-[14px]">
              ${(search || advFilters.length) ? 'Nenhum contato encontrado.' : 'Nenhum contato ainda.'}
            </div>
          ` : pageItems.map((c) => html`
            <${ContactRow}
              key=${c.id}
              c=${c}
              onOpenDetail=${openDetail}
              onStartConversation=${startConversation}
            />
          `)}

          <!-- Paginação: 15 contatos por página. Sempre visível (sticky no rodapé da
               área de rolagem), inclusive quando um filtro do cliente esvazia a página
               atual — senão o usuário ficaria preso sem como voltar. -->
          ${total > 0 ? html`
            <div class="sticky bottom-0 z-[60] -mx-1 px-1 pt-2 pb-1">
              <div class="flex items-center justify-between gap-3 px-3 py-2 rounded-xl border border-wa-border bg-wa-panel shadow-lg text-xs text-wa-secondary">
                <!-- Esquerda: faixa exibida no universo total (server-side). -->
                <span class="truncate">
                  Exibindo ${rangeFrom} - ${rangeTo} de ${total} contatos
                </span>
                <!-- Direita: navegação + a página atual num campo editável ("ir para"). -->
                <div class="flex items-center gap-1.5 shrink-0">
                  <button
                    onClick=${() => setPage(0)}
                    disabled=${page === 0}
                    title="Primeira página"
                    class=${PAGE_BTN}
                  >« <span class="hidden md:inline">Primeira</span></button>
                  <button
                    onClick=${() => setPage((p) => Math.max(0, p - 1))}
                    disabled=${page === 0}
                    title="Página anterior"
                    class=${PAGE_BTN}
                  >‹ <span class="hidden md:inline">Anterior</span></button>
                  <input
                    type="text"
                    inputMode="numeric"
                    value=${pageInput}
                    onInput=${(e) => setPageInput(e.target.value)}
                    onKeyDown=${(e) => { if (e.key === 'Enter') { e.preventDefault(); e.target.blur(); } }}
                    onBlur=${commitPageInput}
                    title="Ir para a página"
                    aria-label="Ir para a página"
                    class="wa-field w-[52px] text-center text-xs rounded border border-wa-border px-1 py-1 outline-none focus:border-wa-teal transition-colors"
                  />
                  <span class="whitespace-nowrap">de ${totalPages} páginas</span>
                  <button
                    onClick=${() => setPage((p) => p + 1)}
                    disabled=${!hasNext}
                    title="Próxima página"
                    class=${PAGE_BTN}
                  ><span class="hidden md:inline">Próxima</span> ›</button>
                  <button
                    onClick=${() => setPage(totalPages - 1)}
                    disabled=${!hasNext}
                    title="Última página"
                    class=${PAGE_BTN}
                  ><span class="hidden md:inline">Última</span> »</button>
                </div>
              </div>
            </div>
          ` : null}
        </div>
      `}

      ${detail ? html`
        <${ContactDetailOverlay}
          contact=${detail}
          globalTags=${globalTags}
          onGlobalTagsChange=${setGlobalTags}
          onClose=${closeDetail}
          onSaved=${handleSaved}
          onDeleted=${handleDeleted}
          onStartConversation=${startConversation}
        />
      ` : null}

      ${showCreate ? html`
        <${NewContactModal}
          onClose=${() => setShowCreate(false)}
          onCreated=${async (phone) => {
            setShowCreate(false);
            reload();
            // plano 62 F3: abre o detalhe buscando SÓ o contato criado
            // (GET /api/contacts/{phone}) em vez de re-baixar a lista completa.
            const res = await getContact(phone, false, null, { limit: 1 });
            if (res && res.ok && res.data && res.data.id != null) openDetail(res.data);
          }}
        />
      ` : null}

      ${showImport && canImport ? html`
        <div
          class="fixed inset-0 z-[120] bg-black/50 flex items-center justify-center p-4"
          onClick=${() => { if (!importing) setShowImport(false); }}
        >
          <div
            class="bg-wa-panel rounded-xl shadow-xl border border-wa-border w-full max-w-md"
            onClick=${(e) => e.stopPropagation()}
          >
            <!-- Header -->
            <div class="flex items-center gap-3 p-4 border-b border-wa-border">
              <h2 class="text-[16px] font-semibold text-wa-text flex-1">Importar contatos</h2>
              <button
                onClick=${() => { if (!importing) setShowImport(false); }}
                class="w-[34px] h-[34px] rounded-full flex items-center justify-center text-wa-secondary hover:bg-wa-hover transition-colors shrink-0"
                title="Fechar"
              >
                <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
              </button>
            </div>

            <!-- Body -->
            <div class="px-4 py-4 flex flex-col gap-3">
              <p class="text-[14px] text-wa-text">
                A importação precisa ser feita com um arquivo no formato <strong>.csv</strong>.
              </p>
              <div class="text-[13px] text-wa-secondary bg-wa-bg border border-wa-border rounded-lg px-3 py-2">
                <div class="mb-1">Colunas esperadas (apenas <strong>phone</strong> é obrigatório):</div>
                <code class="block text-[12px] text-wa-text break-words">
                  phone, name, email, profession, company, address, ai_enabled, tags
                </code>
              </div>

              ${importError ? html`
                <div class="text-[13px] px-3 py-2 rounded-lg border bg-red-500/10 border-red-500/30 text-red-500">
                  ${importError}
                </div>
              ` : null}
            </div>

            <!-- Footer -->
            <div class="p-4 border-t border-wa-border flex justify-end gap-2">
              <button
                onClick=${() => { if (!importing) setShowImport(false); }}
                disabled=${importing}
                class="text-[14px] font-medium px-4 py-[8px] rounded-lg border border-wa-border text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >Cancelar</button>
              <button
                onClick=${() => fileInputRef.current && fileInputRef.current.click()}
                disabled=${importing}
                class="flex items-center gap-2 text-[14px] font-medium px-4 py-[8px] rounded-lg bg-wa-teal text-white hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M19 13v6H5v-6H3v6c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2v-6h-2zM11 3v9.17l-2.59-2.58L7 11l5 5 5-5-1.41-1.41L13 12.17V3h-2z" transform="rotate(180 12 12)"/></svg>
                ${importing ? 'Importando...' : 'Selecionar arquivo CSV'}
              </button>
            </div>
          </div>
        </div>
      ` : null}
    </div>
  `;
}
