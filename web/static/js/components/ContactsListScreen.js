// Tela "Contatos" (full-page) — lista todos os contatos em ordem alfabética,
// com busca, "Ver detalhes" (modal com infos) e "Iniciar conversa" (abre o chat
// do contato no hub de conversas). Acessível pelo menu da lista de conversas.
import { h } from 'preact';
import { useState, useEffect, useMemo, useRef } from 'preact/hooks';
import htm from 'htm';
import { DefaultAvatar, GroupAvatar, SearchIcon } from './contacts/icons.js';
import { avatarUrl } from './contacts/utils.js';
import { getContacts, getContact, exportContacts, importContacts } from '../services/api.js';

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

// ── Modal "Ver detalhes" ─────────────────────────────────────────────────
function ContactDetailModal({ contact, onClose, onStartConversation }) {
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
  const tags = (data && data.tags) || contact.tags || [];

  const Row = (label, value) => (value ? html`
    <div class="flex flex-col gap-[2px] py-[8px] border-b border-wa-border last:border-b-0">
      <span class="text-[12px] text-wa-secondary">${label}</span>
      <span class="text-[14px] text-wa-text break-words">${value}</span>
    </div>
  ` : null);

  return html`
    <div
      class="fixed inset-0 z-[120] bg-black/50 flex items-center justify-center p-4"
      onClick=${onClose}
    >
      <div
        class="bg-wa-panel rounded-xl shadow-xl border border-wa-border w-full max-w-md max-h-[85vh] overflow-y-auto wa-scrollbar"
        onClick=${(e) => e.stopPropagation()}
      >
        <!-- Header -->
        <div class="flex items-center gap-3 p-4 border-b border-wa-border">
          <div class="w-[52px] h-[52px] rounded-full overflow-hidden shrink-0">
            ${contact.is_group
              ? html`<${GroupAvatar} size=${52} avatarUrl=${avatarUrl(contact.phone, contact.avatar_v)} />`
              : html`<${DefaultAvatar} size=${52} avatarUrl=${avatarUrl(contact.phone, contact.avatar_v)} />`}
          </div>
          <div class="min-w-0 flex-1">
            <div class="text-[16px] font-semibold text-wa-text truncate">
              ${contact.name || formatPhoneDisplay(contact.phone) || 'Sem nome'}
            </div>
            ${!contact.is_group ? html`
              <div class="text-[13px] text-wa-secondary">${formatPhoneDisplay(contact.phone)}</div>
            ` : null}
          </div>
          <button
            onClick=${onClose}
            class="w-[34px] h-[34px] rounded-full flex items-center justify-center text-wa-secondary hover:bg-wa-hover transition-colors shrink-0"
            title="Fechar"
          >
            <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>
          </button>
        </div>

        <!-- Body -->
        <div class="px-4 py-2">
          ${loading ? html`
            <div class="text-center text-wa-secondary py-8 animate-pulse-slow text-[14px]">Carregando...</div>
          ` : html`
            ${Row('Telefone', !contact.is_group ? formatPhoneDisplay(contact.phone) : null)}
            ${Row('E-mail', info.email)}
            ${Row('Profissão', info.profession)}
            ${Row('Empresa', info.company)}
            ${Row('Endereço', info.address)}
            ${tags && tags.length ? html`
              <div class="flex flex-col gap-[4px] py-[8px] border-b border-wa-border">
                <span class="text-[12px] text-wa-secondary">Tags</span>
                <div class="flex flex-wrap gap-[6px]">
                  ${tags.map((t) => html`
                    <span class="text-[12px] px-[8px] py-[2px] rounded-full bg-wa-teal/15 text-wa-teal">${t}</span>
                  `)}
                </div>
              </div>
            ` : null}
            ${info.observations && info.observations.length ? html`
              <div class="flex flex-col gap-[4px] py-[8px]">
                <span class="text-[12px] text-wa-secondary">Observações</span>
                ${info.observations.map((o) => html`
                  <span class="text-[14px] text-wa-text break-words">• ${o}</span>
                `)}
              </div>
            ` : null}
            ${!loading && !info.email && !info.profession && !info.company && !info.address
              && !(tags && tags.length) && !(info.observations && info.observations.length)
              ? html`<div class="text-center text-wa-secondary py-6 text-[13px]">Sem informações adicionais.</div>`
              : null}
          `}
        </div>

        <!-- Footer -->
        <div class="p-4 border-t border-wa-border flex justify-end">
          <button
            onClick=${() => onStartConversation(contact)}
            class="flex items-center gap-2 bg-wa-teal text-white text-[14px] font-medium px-4 py-[8px] rounded-lg hover:opacity-90 transition-opacity"
          >
            <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg>
            Iniciar conversa
          </button>
        </div>
      </div>
    </div>
  `;
}

// ── Tela principal ───────────────────────────────────────────────────────
export default function ContactsListScreen() {
  const [contacts, setContacts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [detail, setDetail] = useState(null); // contato aberto no modal
  const [importing, setImporting] = useState(false);
  const [toast, setToast] = useState(null); // {kind:'ok'|'err', text}
  const [showImport, setShowImport] = useState(false); // modal de importação
  const [importError, setImportError] = useState(null); // erro dentro do modal
  const fileInputRef = useRef(null);

  function loadContacts() {
    setLoading(true);
    setError(null);
    return getContacts('', false)
      .then((res) => {
        if (res && res.ok) setContacts(res.data || []);
        else setError((res && res.error) || 'Falha ao carregar contatos');
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }

  useEffect(() => { loadContacts(); }, []);

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
        await loadContacts();
      } else {
        setImportError((res && res.error) || 'Falha ao importar contatos.');
      }
    } catch (err) {
      setImportError('Falha ao importar contatos.');
    } finally {
      setImporting(false);
    }
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

  function startConversation(contact) {
    setDetail(null);
    navigate(`/contacts/${contact.id}`);
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
          class="flex items-center gap-2 text-[14px] font-medium px-4 py-[8px] rounded-lg bg-wa-teal text-white hover:opacity-90 transition-opacity"
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

      <!-- Busca -->
      <div class="mb-4">
        <div class="flex items-center bg-wa-bg rounded-lg h-[42px] px-[12px] gap-[10px] border border-wa-border">
          <${SearchIcon} />
          <input
            type="text"
            placeholder="Pesquisar contatos..."
            value=${search}
            onInput=${(e) => setSearch(e.target.value)}
            class="bg-transparent border-none outline-none text-wa-text text-[14px] w-full placeholder-wa-secondary"
          />
        </div>
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
                    onClick=${() => setDetail(c)}
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
        <${ContactDetailModal}
          contact=${detail}
          onClose=${() => setDetail(null)}
          onStartConversation=${startConversation}
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
