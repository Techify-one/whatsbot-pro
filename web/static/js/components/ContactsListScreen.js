// Tela "Contatos" (full-page) — lista todos os contatos em ordem alfabética,
// com busca, "Ver detalhes" (painel editável com todos os atributos, incluindo
// os personalizados), "Iniciar conversa" (abre o chat no hub de conversas) e
// importar/exportar contatos via CSV. Acessível pelo menu da lista de conversas.
//
// Deep-link: abrir o detalhe reflete na URL como /contacts/{id} (id do contato);
// back/forward reabre/fecha o painel. A lista em si fica em /contacts.
import { h } from 'preact';
import { useState, useEffect, useMemo, useCallback, useRef } from 'preact/hooks';
import htm from 'htm';
import { DefaultAvatar, GroupAvatar, SearchIcon, PlusIcon } from './contacts/icons.js';
import { avatarUrl } from './contacts/utils.js';
import { ContactInfoPanel } from './contacts/ContactInfoPanel.js';
import { useDeepLink } from '../hooks/useDeepLink.js';
import {
  getContacts, getContact, getTags, deleteContact, checkPhone,
  updateContactInfo, getContactConversation, exportContacts, importContacts,
} from '../services/api.js';

const html = htm.bind(h);

const PAGE_SIZE = 15;

// Casefold + strip accents (espelha o `_fold` do backend) para a busca casar
// independente de acento/caixa.
function foldStr(s) {
  return (s || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
}

function formatPhoneDisplay(phone) {
  if (!phone || phone.length < 12) return phone || '';
  // 55 85 97360559 → +55 (85) 97360-559
  return `+${phone.slice(0, 2)} (${phone.slice(2, 4)}) ${phone.slice(4, 9)}-${phone.slice(9)}`;
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
// Reusa o ContactInfoPanel do hub de conversas (mesma UX, atributos personalizados,
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
        <!-- Ação "Iniciar conversa" flutuante (o ContactInfoPanel não a tem) -->
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

// ── Tela principal ───────────────────────────────────────────────────────
export default function ContactsListScreen({ initialEntity = null }) {
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

  // Busca client-side (nome, telefone, tags).
  const filtered = useMemo(() => {
    const q = foldStr(search.trim());
    const digits = search.replace(/\D/g, '');
    if (!q && !digits) return sorted;
    return sorted.filter((c) => {
      if (q && foldStr(c.name).includes(q)) return true;
      if (digits && (c.phone || '').includes(digits)) return true;
      if (q && foldStr(c.email).includes(q)) return true;
      if (q && (c.tags || []).some((t) => foldStr(t).includes(q))) return true;
      return false;
    });
  }, [sorted, search]);

  // Reseta a página quando a busca muda o conjunto.
  useEffect(() => { setPage(1); }, [search]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const start = (safePage - 1) * PAGE_SIZE;
  const pageItems = filtered.slice(start, start + PAGE_SIZE);

  // Abre o chat do contato no hub. Resolve a conversa ativa (se houver) e navega
  // por /conversations/{id}; sem conversa ainda, cai na raiz do hub.
  async function startConversation(contact) {
    closeDetail();
    try {
      const res = await getContactConversation(contact.phone, { includeClosed: true });
      const conv = res && res.ok ? res.data.conversation : null;
      if (conv && conv.id != null) { navigate(`/conversations/${conv.id}`); return; }
    } catch { /* fallthrough */ }
    navigate('/');
  }

  // Salvou no painel: reflete nome/email/tags na linha da lista.
  function handleSaved(contact, savedInfo, savedTags) {
    setContacts((prev) => prev.map((c) => c.id === contact.id
      ? { ...c, name: savedInfo.name ?? c.name, email: savedInfo.email ?? c.email, tags: savedTags ?? c.tags }
      : c));
    setDetail((d) => (d && d.id === contact.id
      ? { ...d, name: savedInfo.name ?? d.name, email: savedInfo.email ?? d.email, tags: savedTags ?? d.tags }
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

      <!-- Busca + Novo contato -->
      <div class="mb-4 flex items-center gap-3">
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
        <button
          onClick=${() => setShowCreate(true)}
          class="flex items-center gap-2 bg-wa-teal text-white text-[14px] font-medium px-4 h-[42px] rounded-lg hover:opacity-90 transition-opacity shrink-0"
        >
          <${PlusIcon} />
          <span class="hidden sm:inline">Novo contato</span>
        </button>
      </div>

      ${error ? html`
        <div class="text-center text-red-400 py-8 text-[14px]">${error}</div>
      ` : loading ? html`
        <div class="text-center text-wa-secondary py-12 animate-pulse-slow text-[14px]">Carregando contatos...</div>
      ` : filtered.length === 0 ? html`
        <div class="text-center text-wa-secondary py-12 text-[14px]">
          ${search ? 'Nenhum contato encontrado.' : 'Nenhum contato ainda.'}
        </div>
      ` : html`
        <!-- Lista -->
        <div class="flex flex-col gap-3">
          ${pageItems.map((c) => html`
            <div
              key=${c.id}
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
                <div class="text-[16px] font-semibold text-wa-text truncate">
                  ${c.name || formatPhoneDisplay(c.phone) || 'Sem nome'}
                </div>
                <div class="flex items-center flex-wrap gap-x-2 gap-y-[2px] text-[13px] mt-[2px]">
                  <span class="text-wa-secondary truncate max-w-full">
                    ${c.email
                      ? c.email
                      : (c.is_group ? 'Grupo' : formatPhoneDisplay(c.phone))}
                  </span>
                  <span class="text-wa-border">|</span>
                  <button
                    onClick=${() => openDetail(c)}
                    class="text-wa-teal font-medium hover:underline shrink-0"
                  >Ver detalhes</button>
                </div>
                ${c.email && !c.is_group ? html`
                  <div class="text-[13px] text-wa-secondary truncate mt-[1px]">
                    ${formatPhoneDisplay(c.phone)}
                  </div>
                ` : null}
              </div>

              <!-- Iniciar conversa -->
              <button
                onClick=${() => startConversation(c)}
                title="Iniciar conversa"
                class="w-[38px] h-[38px] rounded-full flex items-center justify-center text-wa-teal hover:bg-wa-teal/10 transition-colors shrink-0"
              >
                <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg>
              </button>
            </div>
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

      ${showImport ? html`
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
