// @ts-check
//
// Conversation filters hook (Plano 23 · D2) — extracted verbatim from
// Contacts.js. Owns the status chip / assignment tab / sort / tag funnel /
// advanced-clause state AND the saved-filter presets (named snapshots persisted
// per user, re-applied on reload from localStorage). Produces the derived lists
// the sidebar renders: `activeContacts` (only rows with a real message, plus the
// open thread), `statusTagFiltered`, `tabCounts`, and `displayedContacts`.
//
// All matching is client-side via the pure helpers in services/conversationRows.
// Cross-hook wiring: `contacts` + the open-thread keys + `currentUserId` come in;
// `displayedRef` (owned by the list hook) is kept in sync for "selecionar todas".
import { useState, useEffect, useCallback, useMemo, useRef } from 'preact/hooks';
import {
  listSavedFilters, createSavedFilter, updateSavedFilter, deleteSavedFilter,
  countConversations,
} from '../../../services/api.js';
import {
  isUnassigned, matchesStatus, matchesAssignment, matchesAdvFilters, matchesTags,
  isVisibleInSidebar, sortContactsBy, normalizeSpec, specsEqual, isDefaultSpec,
} from '../../../services/conversationRows.js';
import {
  buildCountParams, buildListParams, isServerExpressible, isListServerExpressible,
} from '../../../services/conversationFilterSpec.js';
import { draftKeyFor, getDraftAt } from '../../../services/drafts.js';
import { useDrafts } from '../../../hooks/useDrafts.js';

// Persists which saved conversation-filter preset is applied, so it survives a
// page reload (per device). Stores the preset id; re-applied once the presets load.
const ACTIVE_FILTER_KEY = 'whatsbot_active_conv_filter';

/**
 * @param {Object} opts
 * @param {Record<string, any>[]} opts.contacts
 * @param {string} [opts.search]
 * @param {string|null} opts.selected
 * @param {number|null} opts.selectedConvId
 * @param {number|null} opts.currentUserId
 * @param {{ current: Record<string, any>[] }} opts.displayedRef
 * @param {boolean} [opts.searching] - há um termo de busca ativo na barra lateral.
 * @param {boolean} [opts.showArchived]
 */
export function useConversationFilters({ contacts, search = '', selected, selectedConvId, currentUserId, displayedRef, searching = false, showArchived = false, skipStoredPreset = false, serverFilterRef = null, fetchContacts = null, viewSpecRef = null }) {
  // Conversation tabs/filters (plano 10 FF2) — applied client-side over `contacts`.
  const [statusFilter, setStatusFilter] = useState('open');   // open|closed|all (default Abertas)
  const [assignmentTab, setAssignmentTab] = useState('all');  // all|mine|unassigned
  const [sortBy, setSortBy] = useState('activity');           // activity|oldest|unread
  const [tagFilter, setTagFilter] = useState([]);             // funil simples (esquerda) — etiquetas
  const [advFilters, setAdvFilters] = useState([]);           // [{dim, op, value}] — filtro avançado (direita)
  // Saved filter presets (Chatwoot-style): named snapshots persisted per user.
  // `activeFilterId` = the preset currently applied (null = ad-hoc/none); the
  // toolbar shows its name in the corner and flags it "modificado" once the live
  // filters diverge from the saved spec.
  const [savedFilters, setSavedFilters] = useState([]);
  const [activeFilterId, setActiveFilterId] = useState(null);
  const [serverCounts, setServerCounts] = useState(null);

  // plano 69 F2/F3 — a lista da sidebar (conversa-first, SEM busca) pode ser
  // server-filtrada pela MESMA query da contagem. `serverMode` = o spec inteiro
  // (status + aba + tags + avançado) é 100% expressável no servidor. Ligado ⇒ o
  // servidor já cortou tudo: NÃO re-filtramos no cliente (a lista não encolhe abaixo
  // do total do servidor) e a contagem também vem do servidor (D4 — nunca uma metade
  // server e outra client).
  const serverMode = useMemo(() => {
    if (searching) return false;
    return isListServerExpressible({
      search, searching, statusFilter, tagFilter, advFilters,
      archived: showArchived, assignmentTab,
    });
  }, [searching, statusFilter, tagFilter, advFilters, showArchived, assignmentTab]);

  // Mantém o ref lido pelo list-hook SEMPRE fresco. Escrita em RENDER (não em efeito)
  // de propósito: o efeito `[showArchived]` do list-hook roda ANTES dos efeitos deste
  // hook, então só uma sincronização síncrona garante que aquele refetch já leia os
  // params novos. Ref-mirror puro (idempotente, sem efeito colateral externo).
  if (serverFilterRef) {
    serverFilterRef.current = serverMode
      ? { params: buildListParams({
          search, searching, statusFilter, tagFilter, advFilters,
          archived: showArchived, assignmentTab }) }
      : null;
  }

  // plano 72 F2 — espelho fresco da VIEW p/ os handlers de WS (que só leem refs).
  // Escrito em RENDER pela mesma razão do `serverFilterRef` acima (idempotente, sem
  // efeito colateral externo). Carrega `serverMode` + as dimensões que `rowMatchesView`/
  // `specNeedsServer` precisam para gatear insert/drop das linhas.
  if (viewSpecRef) {
    viewSpecRef.current = {
      serverMode, statusFilter, assignmentTab, tagFilter, advFilters, currentUserId,
    };
  }

  // Quais atendimentos entram na sidebar (plano 28) — regra em `isVisibleInSidebar`.
  //
  // COM BUSCA o gate é DESLIGADO: digitar um termo é intenção explícita, então tudo
  // que casa aparece — inclusive contato recém-criado, que não tem mensagem nem
  // `origin` (o payload de `list_contacts` nem carrega a coluna) e por isso sumia da
  // busca embora aparecesse na tela Contatos.
  const activeContacts = useMemo(() => {
    if (searching) return contacts;
    const selKey = selectedConvId != null ? `conv:${selectedConvId}` : (selected ? `phone:${selected}` : null);
    return contacts.filter(c => isVisibleInSidebar(c, selKey));
  }, [contacts, selected, selectedConvId, searching]);

  // Derived list: status + tag filter feed the tab counts; the active assignment
  // tab + sort produce what's actually rendered.
  const statusTagFiltered = useMemo(() => {
    // plano 69 F3: em serverMode o servidor já aplicou status+tags+avançado — não
    // re-filtrar (senão a lista encolheria abaixo do total server-side ao paginar).
    if (serverMode) return activeContacts;
    const now = Date.now() / 1000;
    // A status clause in the advanced filter overrides the status chip, so the two
    // never AND into an empty list (e.g. chip "Abertas" + cláusula "Fechada").
    const hasStatusClause = (advFilters || []).some(
      cl => cl.dim === 'status' && cl.value !== '' && cl.value != null);
    // Com busca ativa o chip de status também é ignorado (default 'open' escondia da
    // busca todo atendimento fechado). Tag/filtro avançado continuam valendo — são
    // seleções deliberadas do usuário e nascem vazias, ao contrário do chip.
    return activeContacts.filter(c =>
      (searching || hasStatusClause || matchesStatus(c, statusFilter))
      && matchesTags(c, tagFilter)
      && matchesAdvFilters(c, advFilters, now));
  }, [activeContacts, statusFilter, tagFilter, advFilters, searching, serverMode]);

  const clientTabCounts = useMemo(() => ({
    all: statusTagFiltered.length,
    mine: currentUserId == null ? 0 : statusTagFiltered.filter(c => c.assignee_user_id === currentUserId).length,
    unassigned: statusTagFiltered.filter(isUnassigned).length,
    mentions: statusTagFiltered.filter(c => c.has_user_mention).length,
  }), [statusTagFiltered, currentUserId]);
  const tabCounts = serverCounts || clientTabCounts;

  useEffect(() => {
    const spec = {
      search, searching, statusFilter, tagFilter, advFilters, archived: showArchived,
    };
    // plano 69 F3/D4: a contagem só é server-side quando a LISTA também é (serverMode).
    // Caso contrário cai no cliente junto com a lista — nunca uma metade server e outra
    // client (senão o badge da aba não bateria com a lista client-filtrada).
    if (!serverMode) {
      setServerCounts(null);
      return () => {};
    }
    setServerCounts(null);
    let alive = true;
    const timer = setTimeout(() => {
      countConversations(buildCountParams(spec)).then((res) => {
        if (!alive) return;
        if (res && res.ok && res.data) {
          setServerCounts({
            all: Number(res.data.all || 0),
            mine: Number(res.data.mine || 0),
            unassigned: Number(res.data.unassigned || 0),
            mentions: Number(res.data.mentions || 0),
          });
        } else {
          setServerCounts(null);
        }
      }).catch(() => {
        if (alive) setServerCounts(null);
      });
    }, 300);
    return () => { alive = false; clearTimeout(timer); };
  }, [search, searching, statusFilter, tagFilter, advFilters, showArchived, serverMode, contacts]);

  // plano 69 F2 — refaz a 1ª página da sidebar quando um FILTRO muda (status/aba/
  // tags/avançado). `serverFilterRef` já foi sincronizado no render acima, então o
  // fetch lê os params certos. Busca e arquivadas NÃO entram aqui: o list-hook já
  // refaz nesses casos (evita fetch duplo). Pula o run do mount (o load inicial cobre)
  // e o modo busca (a busca é dona do próprio fetch).
  const filterMountRef = useRef(false);
  useEffect(() => {
    if (!fetchContacts) return;
    if (!filterMountRef.current) { filterMountRef.current = true; return; }
    if (searching) return;
    fetchContacts('');
  }, [statusFilter, assignmentTab, tagFilter, advFilters, serverMode]);

  // Com busca ativa a aba de atribuição também não corta (buscar é procurar em tudo,
  // não só no que está atribuído a mim). As abas seguem visíveis com os contadores
  // do resultado bruto — clicar numa delas não muda a lista enquanto a busca durar.
  // Rascunho conta como atividade: a conversa que o operador deixou escrita sobe
  // na lista. A conversa ABERTA é a exceção — enquanto ele digita ali, a linha
  // dela não pula de lugar (nem mostra "Rascunho:"); ao sair, `openKey` muda e
  // este memo recalcula. Em serverMode reordena só o que já está carregado — o
  // rascunho é local, o servidor não sabe dele (a conversa recém-aberta está na
  // 1ª página de qualquer forma).
  const openKey = selectedConvId != null ? `conv:${selectedConvId}` : (selected ? `phone:${selected}` : null);
  const draftsVersion = useDrafts(openKey);
  // A conversa aberta usa o valor CONGELADO na abertura, não o rascunho ao vivo:
  // digitar não a faz subir no meio da digitação, e reabrir uma que já tinha
  // subido não a faz despencar de volta. Ela volta a acompanhar o rascunho ao ser
  // fechada. (Escrita de ref em render, como o `viewSpecRef` do plano 72.)
  const frozenOpenRef = useRef({ key: null, ts: 0 });
  if (frozenOpenRef.current.key !== openKey) {
    frozenOpenRef.current = { key: openKey, ts: openKey ? getDraftAt(openKey) / 1000 : 0 };
  }
  const draftTsFor = useCallback((c) => {
    const key = draftKeyFor({ conversationId: c.conversation_id, phone: c.phone });
    if (!key) return 0;
    if (key === openKey) return frozenOpenRef.current.ts;
    return getDraftAt(key) / 1000;   // o mapa guarda ms; as rows falam em segundos
  }, [openKey, draftsVersion]);
  const displayedContacts = useMemo(
    () => sortContactsBy(
      // plano 69 F3: em serverMode (e na busca) o servidor/caminho de busca já cortou a
      // aba de atribuição — só ordenar. Fora disso, filtra a aba no cliente como antes.
      (searching || serverMode)
        ? statusTagFiltered
        : statusTagFiltered.filter(c => matchesAssignment(c, assignmentTab, currentUserId)),
      sortBy, draftTsFor),
    [statusTagFiltered, assignmentTab, currentUserId, sortBy, searching, serverMode, draftTsFor],
  );
  useEffect(() => { displayedRef.current = displayedContacts; }, [displayedContacts]);

  // ── Saved filter presets ─────────────────────────────────────────
  // Load the user's presets once on mount (best-effort; degrade silently). If a
  // preset was active before a reload (persisted in localStorage), re-apply it so
  // the inbox comes back filtered — not reset.
  useEffect(() => {
    listSavedFilters().then(res => {
      if (!res || !res.ok || !Array.isArray(res.data)) return;
      setSavedFilters(res.data);
      // Precedência (Plano 24 · D3): quando a URL já carrega filtros ad-hoc, NÃO
      // auto-aplicar o preset salvo — a URL é a fonte da verdade. Ainda assim
      // carregamos a lista de presets acima (o chip/menu da toolbar precisa dela).
      if (skipStoredPreset) return;
      let storedId = null;
      try { storedId = parseInt(localStorage.getItem(ACTIVE_FILTER_KEY) || '', 10); } catch {}
      if (storedId != null && !Number.isNaN(storedId)) {
        const preset = res.data.find(f => f.id === storedId);
        if (preset) applySavedFilter(preset);
        else { try { localStorage.removeItem(ACTIVE_FILTER_KEY); } catch {} }
      }
    }).catch(() => {});
  }, []);

  // Live filter snapshot — what a "Salvar filtro" would persist right now.
  // assignmentTab is intentionally excluded: switching tabs is a view change,
  // not a filter.
  const currentSpec = useMemo(() => ({
    statusFilter, sortBy, tagFilter, advFilters,
  }), [statusFilter, sortBy, tagFilter, advFilters]);

  // The applied preset (if any) + whether the live filters still match it. Once
  // the operator tweaks anything, `modified` flips true so the chip can offer to
  // update/save the preset.
  const activeFilter = useMemo(() => {
    if (activeFilterId == null) return null;
    const preset = savedFilters.find(f => f.id === activeFilterId);
    if (!preset) return null;
    return { ...preset, modified: !specsEqual(currentSpec, preset.spec) };
  }, [activeFilterId, savedFilters, currentSpec]);

  // Drop the active-preset binding whenever the live filters are reset to the
  // defaults (e.g. "Limpar filtros") — nothing is "in use" then.
  useEffect(() => {
    if (activeFilterId != null && isDefaultSpec(currentSpec)) {
      setActiveFilterId(null);
      try { localStorage.removeItem(ACTIVE_FILTER_KEY); } catch {}
    }
  }, [activeFilterId, currentSpec]);

  // Apply a preset: hydrate every filter dimension from its spec and mark it active.
  const applySavedFilter = useCallback((preset) => {
    const s = normalizeSpec(preset.spec);
    setStatusFilter(s.statusFilter);
    setSortBy(s.sortBy);
    setTagFilter(s.tagFilter);
    // Re-seed clause ids so the advanced dialog can edit rows without collisions.
    setAdvFilters(s.advFilters.map((f, i) => ({ ...f, id: `s${preset.id}_${i}` })));
    setActiveFilterId(preset.id);
    try { localStorage.setItem(ACTIVE_FILTER_KEY, String(preset.id)); } catch {}
  }, []);

  // Persist the current filters under a new name.
  const saveCurrentFilter = useCallback(async (name) => {
    const res = await createSavedFilter(name, normalizeSpec(currentSpec));
    if (res && res.ok && res.data) {
      setSavedFilters(prev => [...prev, res.data]);
      setActiveFilterId(res.data.id);
      try { localStorage.setItem(ACTIVE_FILTER_KEY, String(res.data.id)); } catch {}
      return { ok: true };
    }
    return { ok: false, error: (res && res.error) || 'Falha ao salvar o filtro.' };
  }, [currentSpec]);

  // Overwrite an existing preset's spec with the current filters (the "modificado"
  // chip → "Atualizar" action).
  const overwriteSavedFilter = useCallback(async (id) => {
    const res = await updateSavedFilter(id, { spec: normalizeSpec(currentSpec) });
    if (res && res.ok && res.data) {
      setSavedFilters(prev => prev.map(f => (f.id === id ? res.data : f)));
      setActiveFilterId(id);
      try { localStorage.setItem(ACTIVE_FILTER_KEY, String(id)); } catch {}
      return { ok: true };
    }
    return { ok: false, error: (res && res.error) || 'Falha ao atualizar o filtro.' };
  }, [currentSpec]);

  const renameSavedFilter = useCallback(async (id, name) => {
    const res = await updateSavedFilter(id, { name });
    if (res && res.ok && res.data) {
      setSavedFilters(prev => prev.map(f => (f.id === id ? res.data : f)));
      return { ok: true };
    }
    return { ok: false, error: (res && res.error) || 'Falha ao renomear o filtro.' };
  }, []);

  const removeSavedFilter = useCallback(async (id) => {
    const res = await deleteSavedFilter(id);
    if (res && res.ok) {
      setSavedFilters(prev => prev.filter(f => f.id !== id));
      if (activeFilterId === id) {
        setActiveFilterId(null);
        try { localStorage.removeItem(ACTIVE_FILTER_KEY); } catch {}
      }
      return { ok: true };
    }
    return { ok: false, error: (res && res.error) || 'Falha ao excluir o filtro.' };
  }, [activeFilterId]);

  // Clear all live filters back to the defaults and unbind any active preset.
  const clearAllFilters = useCallback(() => {
    setStatusFilter('open');
    setSortBy('activity');
    setTagFilter([]);
    setAdvFilters([]);
    setActiveFilterId(null);
    try { localStorage.removeItem(ACTIVE_FILTER_KEY); } catch {}
  }, []);

  // True when any filter dimension differs from the defaults — drives the "Salvar
  // filtro" / "Limpar" affordances in the toolbar.
  const anyFilterActive = useMemo(() => !isDefaultSpec(currentSpec), [currentSpec]);

  return {
    statusFilter, setStatusFilter,
    assignmentTab, setAssignmentTab,
    sortBy, setSortBy,
    tagFilter, setTagFilter,
    advFilters, setAdvFilters,
    savedFilters, activeFilter, anyFilterActive,
    applySavedFilter, saveCurrentFilter, overwriteSavedFilter,
    renameSavedFilter, removeSavedFilter, clearAllFilters,
    statusTagFiltered, tabCounts, displayedContacts,
  };
}
