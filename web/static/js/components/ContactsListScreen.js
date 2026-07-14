// Tela "Contatos" (full-page) — lista todos os contatos em ordem alfabética,
// com busca, "Ver detalhes" (painel editável com todos os atributos, incluindo
// os personalizados), "Iniciar atendimento" (abre o chat no hub de atendimentos) e
// importar/exportar contatos via CSV. Acessível pelo menu da lista de atendimentos.
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
import { readParams, writeParams, str, json } from '../services/urlState.js';
import {
  getContacts, getContact, getTags, deleteContact, checkPhone,
  updateContactInfo, getContactConversation, exportContacts, importContacts,
  getCustomAttributes,
} from '../services/api.js';
import { matchesAdvFilters } from '../services/conversationRows.js';
import { contactTypeMeta, contactTypeBadge } from '../services/contactTypes.js';
import { formatPhoneDisplay } from '../utils/phone.js';
import { hasPermission } from '../utils/permissions.js';

const html = htm.bind(h);

const PAGE_SIZE = 15;

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

// Deep-link do estado da lista de contatos (Plano 24): busca + filtro avançado
// na query legível. `adv` guarda só as cláusulas (JSON), omitido quando vazio.
const CONTACTS_URL_SCHEMA = [
  str('search', ''),
  json('adv', { isDefault: (v) => !Array.isArray(v) || v.length === 0 }),
];

// ── Tela principal ───────────────────────────────────────────────────────
export default function ContactsListScreen({ initialEntity = null, currentUser = null }) {
  const canImport = hasPermission(currentUser, 'contact.import');
  const [contacts, setContacts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
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
      setAdvFilters(Array.isArray(s.adv) ? s.adv.map((f, i) => ({ ...f, id: `u${i}` })) : []);
    },
    serialize: () => writeParams({
      search,
      adv: (advFilters || []).map(({ id, ...rest }) => rest),
    }, CONTACTS_URL_SCHEMA),
    deps: [search, advFilters],
  });

  function reload() {
    setLoading(true);
    return getContacts('', false)
      .then((res) => {
        if (res && res.ok) { setContacts(res.data || []); setError(null); }
        else setError((res && res.error) || 'Falha ao carregar contatos');
        return res;
      })
      .catch((e) => { setError(String(e)); })
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    let alive = true;
    setLoading(true);
    getContacts('', false)
      .then((res) => {
        if (!alive) return;
        if (res && res.ok) setContacts(res.data || []);
        else setError((res && res.error) || 'Falha ao carregar contatos');
      })
      .catch((e) => { if (alive) setError(String(e)); })
      .finally(() => { if (alive) setLoading(false); });
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

  // Ordem alfabética por nome (cai no telefone quando não há nome).
  const sorted = useMemo(() => {
    const arr = [...contacts];
    arr.sort((a, b) => {
      const an = (a.name || a.phone || '').trim();
      const bn = (b.name || b.phone || '').trim();
      return an.localeCompare(bn, 'pt-BR', { sensitivity: 'base' });
    });
    return arr;
  }, [contacts]);

  // Busca client-side (nome, telefone, tags) + filtros avançados (etiqueta/atributos
  // personalizados do contato). A avaliação reusa matchesAdvFilters: cada contato
  // carrega `tags` e `custom_attributes`, então `tag`/`cattr:contact:*` casam direto.
  const filtered = useMemo(() => {
    const q = foldStr(search.trim());
    const digits = search.replace(/\D/g, '');
    const now = Math.floor(Date.now() / 1000);
    // Busca por nome / telefone / tags. Email NÃO entra na busca (a pedido) — para
    // filtrar por email use o filtro (é um atributo personalizado).
    const bySearch = (!q && !digits) ? sorted : sorted.filter((c) => {
      if (q && foldStr(c.name).includes(q)) return true;
      if (digits && (c.phone || '').includes(digits)) return true;
      if (q && (c.tags || []).some((t) => foldStr(t).includes(q))) return true;
      return false;
    });
    if (!advFilters.length) return bySearch;
    return bySearch.filter((c) => matchesAdvFilters(c, advFilters, now));
  }, [sorted, search, advFilters]);

  // Reseta a página quando a busca ou os filtros mudam o conjunto.
  useEffect(() => { setPage(1); }, [search, advFilters]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const start = (safePage - 1) * PAGE_SIZE;
  const pageItems = filtered.slice(start, start + PAGE_SIZE);

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
      ` : filtered.length === 0 ? html`
        <div class="text-center text-wa-secondary py-12 text-[14px]">
          ${(search || advFilters.length) ? 'Nenhum contato encontrado.' : 'Nenhum contato ainda.'}
        </div>
      ` : html`
        <!-- Lista -->
        <div class="flex flex-col gap-3">
          ${pageItems.map((c) => html`
            <${ContactRow}
              key=${c.id}
              c=${c}
              onOpenDetail=${openDetail}
              onStartConversation=${startConversation}
            />
          `)}
        </div>

        <!-- Paginação -->
        <div class="flex items-center justify-between mt-4 text-[13px] text-wa-secondary">
          <span>Exibindo ${start + 1} - ${start + pageItems.length} de ${filtered.length} contatos</span>
          ${totalPages > 1 ? html`
            <div class="flex items-center gap-1">
              <button
                onClick=${() => setPage((p) => Math.max(1, p - 1))}
                disabled=${safePage <= 1}
                class="px-3 py-[6px] rounded-lg border border-wa-border text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >Anterior</button>
              <span class="px-2">${safePage} de ${totalPages}</span>
              <button
                onClick=${() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled=${safePage >= totalPages}
                class="px-3 py-[6px] rounded-lg border border-wa-border text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >Próxima</button>
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
            await reload();
            // Abre o detalhe do contato recém-criado (resolve o id pela lista nova).
            const res = await getContacts('', false);
            const created = res && res.ok ? (res.data || []).find((c) => c.phone === phone) : null;
            if (created) openDetail(created);
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
