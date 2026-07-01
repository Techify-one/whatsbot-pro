import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import {
  getConversation, getContactConversation, getCustomAttributes,
  updateConversationInfo, getMe,
} from '../../services/api.js';
import { resolveConversation } from '../../utils/resolveConversation.js';
import { Slot } from '../../plugins/Slot.js';
import { getFilters } from '../../plugins/registry.js';
import { CloseIcon } from './icons.js';
import { CustomAttributeField } from './CustomAttributeField.js';
import { AssigneePicker } from './AssigneePicker.js';
import { ConversationLabelEditor } from './ConversationLabelEditor.js';
import { RequiredAttributesModal } from './RequiredAttributesModal.js';
import { hasPermission } from '../../utils/permissions.js';
import { missingRequiredAttributes } from '../../utils/requiredAttributes.js';
import { useInfoPanelResize } from './hooks/useInfoPanelResize.js';

const html = htm.bind(h);

// ── Conversation Info Panel (plano conversa Onda 2) ──────────────────────────
// The conversation-scoped counterpart of ContactInfoPanel: status, assignment,
// conversation labels (Onda 3), conversation custom attributes and read-only
// metadata. Opened from the "Informações da conversa" (ℹ️) button in the chat
// header; the contact photo/name still opens ContactInfoPanel. Dark-mode-safe.

const PROVIDER_LABELS = {
  gowa: 'WhatsApp',
  whatsapp_cloud: 'WhatsApp Cloud API',
  telegram: 'Telegram',
  test: 'Teste',
};

function fmtTs(ts) {
  if (!ts) return '—';
  try {
    return new Date(ts * 1000).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
  } catch (e) {
    return '—';
  }
}

export function ConversationInfoPanel({ phone, conversationId = null, onClose, onOpenContactInfo = null, contactInfo = null, convAttrPatch = null }) {
  const [conv, setConv] = useState(null);
  const [loading, setLoading] = useState(true);
  const [convDefs, setConvDefs] = useState([]);
  const [contactDefs, setContactDefs] = useState([]);   // contact-scoped attribute defs
  const [convValues, setConvValues] = useState({});
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [user, setUser] = useState(null);
  const [missingAttrs, setMissingAttrs] = useState(null);   // { list, target } blocking resolve
  const [highlightAttrs, setHighlightAttrs] = useState(false);
  const attrsRef = useRef(null);
  const panelRootRef = useRef(null);   // overlay que delimita a área de conversa

  // Largura arrastável do painel (desktop), persistida por-dispositivo. O máximo é
  // dinâmico: pode crescer até quase cobrir toda a conversa (medida via panelRootRef).
  const { width, isResizing, isDesktop, startResize } = useInfoPanelResize({
    storageKey: 'whatsbot_conv_info_panel_width',
    containerRef: panelRootRef,
  });

  // Identity — permission gating for the Resolver/Reabrir action (P48: hide).
  useEffect(() => {
    let alive = true;
    getMe().then(r => { if (alive && r && r.ok && r.data && r.data.user) setUser(r.data.user); }).catch(() => {});
    return () => { alive = false; };
  }, []);

  // Attribute definitions (conversation shown here + contact scope for the resolve
  // guard); reload when the admin edits them.
  useEffect(() => {
    let cancelled = false;
    async function load() {
      const [cRes, kRes] = await Promise.all([
        getCustomAttributes('conversation'),
        getCustomAttributes('contact'),
      ]);
      if (cancelled) return;
      if (cRes.ok) setConvDefs(cRes.data || []);
      if (kRes.ok) setContactDefs(kRes.data || []);
    }
    load();
    window.addEventListener('whatsbot:custom-attributes-changed', load);
    return () => {
      cancelled = true;
      window.removeEventListener('whatsbot:custom-attributes-changed', load);
    };
  }, []);

  // Resolve the conversation: by id when known (one channel), else by phone.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const apply = (res) => {
      if (cancelled) return;
      const c = (res && res.ok && res.data) ? res.data.conversation : null;
      setConv(c || null);
      setConvValues({ ...((c && c.custom_attributes) || {}) });
      setLoading(false);
    };
    const fail = () => { if (!cancelled) { setConv(null); setConvValues({}); setLoading(false); } };
    if (conversationId != null) {
      getConversation(conversationId).then(apply).catch(fail);
    } else if (phone) {
      getContactConversation(phone, { includeClosed: true }).then(apply).catch(fail);
    } else {
      setConv(null); setConvValues({}); setLoading(false);
    }
    return () => { cancelled = true; };
  }, [phone, conversationId]);

  // P19 (locked decision 6): live-refresh when the AI writes a conversation-scope
  // custom attribute. Contacts.js forwards the `conversation_updated` WS event as
  // `convAttrPatch` ({conversation_id, custom_attributes}); merge the new values
  // into the open panel without a refetch — mirrors how the contact panel reacts to
  // `contact_info_updated`. The effect closure captures the latest `conv` (a new
  // patch prop triggers a re-render before the effect runs), so the id check is fresh.
  useEffect(() => {
    if (!convAttrPatch || !conv) return;
    if (convAttrPatch.conversation_id !== conv.id) return;
    const next = convAttrPatch.custom_attributes || {};
    setConvValues(prev => ({ ...prev, ...next }));
    setConv(prev => (prev ? { ...prev, custom_attributes: { ...(prev.custom_attributes || {}), ...next } } : prev));
  }, [convAttrPatch]);

  // Merge an updated conv row from an action endpoint into the current one — the
  // raw rows (assign/status) omit the channel fields, so we keep prev's enrichment.
  const mergeConv = (c) => setConv(prev => (prev ? { ...prev, ...c } : c));

  // Payload de custom_attributes p/ salvar: TODOS os atributos definidos, mandando `null`
  // para os que ficaram vazios (limpos). É necessário porque o backend faz MERGE
  // (set_values): uma chave AUSENTE do payload mantém o valor antigo — então limpar um
  // campo apenas removendo a chave de `convValues` o fazia reverter (e a re-sync via
  // `convAttrPatch` trazia o valor antigo de volta à UI). `null` remove a chave de fato;
  // demais valores sobrescrevem. (vazio→null, NÃO ''→ pois '' é rejeitado por tipos como
  // select/list na validação do backend.)
  const buildConvAttrsPayload = () => {
    const payload = {};
    for (const def of convDefs) {
      const v = convValues[def.attribute_key];
      payload[def.attribute_key] = (v === undefined || v === null || v === '') ? null : v;
    }
    return payload;
  };

  async function toggleStatus() {
    if (!conv || busy) return;
    const closing = conv.status === 'open';
    // When a plugin owns the resolve flow (filter.conversation.beforeResolve), it collects
    // and saves the conversation attributes in its own pre-filled popup — skip the native
    // conversation-scope gate here so the two popups don't stack. Contact-scope stays gated.
    const pluginOwnsResolve = getFilters('filter.conversation.beforeResolve').length > 0;
    // Resolver guard: every required ("Obrigatório preencher") attribute must have
    // a value before closing — conversation attributes first (priority), then the
    // contact's. Conversation values use the live (edited) state so the operator
    // can fill them right here; contact values come from the contact panel. Pending
    // conversation edits are persisted before closing. Reopening is never blocked.
    if (closing) {
      if (!pluginOwnsResolve) {
        const convMissing = missingRequiredAttributes(convDefs, convValues);
        if (convMissing.length) { setMissingAttrs({ list: convMissing, target: 'conversation' }); return; }
      }
      const contactMissing = missingRequiredAttributes(contactDefs, contactInfo && contactInfo.custom_attributes);
      if (contactMissing.length) { setMissingAttrs({ list: contactMissing, target: 'contact' }); return; }
    }
    setBusy(true);
    try {
      let convForResolve = conv;
      if (closing) {
        // Persist pending attribute edits first; abort the close if it fails
        // (e.g. regex validation) so nothing is silently lost. The saved values also
        // pre-fill the plugin's resolve popup (it reads conv.custom_attributes).
        const saveRes = await updateConversationInfo(conv.id, { custom_attributes: buildConvAttrsPayload() });
        if (!(saveRes && saveRes.ok)) return;
        if (saveRes.data && saveRes.data.conversation) {
          mergeConv(saveRes.data.conversation);
          convForResolve = { ...conv, ...saveRes.data.conversation };
        }
      }
      const r = await resolveConversation(convForResolve, closing ? 'closed' : 'open');
      if (r && r.ok && r.data && r.data.conversation) mergeConv(r.data.conversation);
    } finally {
      setBusy(false);
    }
  }

  // Modal "OK": route to where the pending attributes live. Conversation ones are
  // already on this panel — scroll to and briefly highlight them; contact ones
  // require switching to the contact panel ("Dados do contato").
  function onMissingConfirm() {
    const target = missingAttrs && missingAttrs.target;
    setMissingAttrs(null);
    if (target === 'contact') { if (onOpenContactInfo) onOpenContactInfo(); return; }
    setHighlightAttrs(true);
    try { attrsRef.current && attrsRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch (e) {}
    setTimeout(() => setHighlightAttrs(false), 2500);
  }

  async function handleSaveAttrs() {
    if (!conv) return;
    setSaving(true);
    try {
      const res = await updateConversationInfo(conv.id, { custom_attributes: buildConvAttrsPayload() });
      if (res && res.ok && res.data && res.data.conversation) mergeConv(res.data.conversation);
    } catch (e) {
      console.error('Failed to save conversation attributes:', e);
    }
    setSaving(false);
  }

  const isOpen = conv && conv.status === 'open';
  const providerLabel = conv && (conv.channel_name || PROVIDER_LABELS[conv.channel_provider] || conv.channel_provider || '—');
  const canResolve = hasPermission(user, 'conversation.resolve');
  const canAssign = hasPermission(user, 'conversation.assign');
  const canReply = hasPermission(user, 'conversation.reply');

  return html`
    <div ref=${panelRootRef} class="absolute inset-0 z-50 flex justify-end">
      <!-- Alça de redimensionamento (desktop): arraste p/ ajustar a largura do painel.
           Fica na borda esquerda porque o painel é ancorado à direita. -->
      <div
        onMouseDown=${startResize}
        class="hidden lg:flex items-center justify-center w-[10px] shrink-0 self-stretch select-none cursor-col-resize group animate-slide-in-right transition-colors ${isResizing ? 'bg-wa-teal/40' : 'hover:bg-wa-hover'}"
        role="separator"
        aria-orientation="vertical"
        title="Arraste para redimensionar"
      >
        <span class="w-px h-8 bg-wa-border group-hover:bg-wa-teal pointer-events-none transition-colors"></span>
      </div>
      <div
        class="w-full h-full bg-wa-panel flex flex-col shadow-xl animate-slide-in-right"
        style=${isDesktop ? `width:${width}px` : ''}>
        <!-- Header -->
        <div class="h-[59px] flex items-center px-4 bg-wa-teal shrink-0 gap-4">
          <button onClick=${onClose} class="text-white hover:opacity-80 shrink-0">
            <${CloseIcon} />
          </button>
          <span class="text-white text-[16px] font-medium">
            Informações da conversa${conv && conv.display_id != null ? html` <span class="opacity-80">#${conv.display_id}</span>` : null}
          </span>
        </div>

        <!-- Content -->
        <div class="flex-1 overflow-y-auto wa-scrollbar">
          ${loading ? html`<div class="px-6 py-8 text-wa-secondary text-[14px]">Carregando…</div>` : null}
          ${!loading && !conv ? html`
            <div class="px-6 py-8 text-wa-secondary text-[14px] text-center">
              Nenhuma conversa para este contato ainda.
            </div>
          ` : null}
          ${!loading && conv ? html`
            <!-- Status -->
            <div class="bg-wa-bg px-6 py-4 border-b border-wa-border">
              <div class="text-wa-iconActive text-[13px] font-semibold mb-2">Status</div>
              <div class="flex items-center justify-between gap-3">
                <span class="px-2.5 py-1 rounded-full text-[12px] font-medium ${isOpen ? 'bg-wa-teal/15 text-wa-teal' : 'bg-wa-hover text-wa-secondary'}">
                  ${isOpen ? 'Aberta' : 'Fechada'}
                </span>
                ${canResolve ? html`
                  <button disabled=${busy} onClick=${toggleStatus}
                    class="px-3 py-1.5 rounded-md text-[13px] border border-wa-border text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50 whitespace-nowrap">
                    ${isOpen ? 'Resolver' : 'Reabrir'}
                  </button>
                ` : null}
              </div>
            </div>

            <!-- Atribuição (humano ou IA) -->
            ${canAssign ? html`
            <div class="bg-wa-bg px-6 py-4 border-b border-wa-border">
              <${AssigneePicker} conv=${conv} onChange=${mergeConv} />
            </div>
            ` : null}

            <!-- Etiquetas da conversa (Onda 3) -->
            ${canReply ? html`
            <div class="bg-wa-bg px-6 py-4 border-b border-wa-border">
              <${ConversationLabelEditor} conversationId=${conv.id} currentUser=${user} />
            </div>
            ` : null}

            <!-- Atributos da conversa -->
            ${canReply && convDefs.length > 0 ? html`
              <div ref=${attrsRef} class="bg-wa-bg px-6 py-4 border-b border-wa-border transition-all duration-300 ${highlightAttrs ? 'ring-2 ring-inset ring-red-500' : ''}">
                <div class="text-wa-iconActive text-[13px] font-semibold mb-3">Dados desta conversa</div>
                <div class="space-y-4">
                  ${convDefs.map(def => html`
                    <${CustomAttributeField}
                      key=${def.id}
                      def=${def}
                      value=${convValues[def.attribute_key]}
                      onChange=${(v) => setConvValues(prev => {
                        const next = { ...prev };
                        if (v === null || v === undefined || v === '') delete next[def.attribute_key];
                        else next[def.attribute_key] = v;
                        return next;
                      })}
                    />
                  `)}
                </div>
                <button onClick=${handleSaveAttrs} disabled=${saving}
                  class="mt-3 w-full bg-wa-iconActive text-white text-[14px] font-medium py-2 rounded-[8px] hover:opacity-90 transition-opacity disabled:opacity-50">
                  ${saving ? 'Salvando…' : 'Salvar atributos'}
                </button>
              </div>
            ` : null}

            <!-- Ponto de extensão: um plugin pode injetar uma seção dentro do painel
                 da conversa (ex.: "Atendimento atual"). Renderiza nada quando vazio,
                 então é inerte sem plugin (camada de extensão de frontend). -->
            <${Slot} name="conversation.info.panel" ctx=${{ conv, contact: contactInfo, user }} />

            <!-- Metadados (somente leitura) -->
            <div class="px-6 py-4">
              <div class="text-wa-iconActive text-[13px] font-semibold mb-2">Detalhes</div>
              <dl class="space-y-1.5 text-[13px]">
                <div class="flex justify-between gap-3">
                  <dt class="text-wa-secondary shrink-0">Canal</dt>
                  <dd class="text-wa-text text-right truncate">${providerLabel}</dd>
                </div>
                <div class="flex justify-between gap-3">
                  <dt class="text-wa-secondary shrink-0">Número</dt>
                  <dd class="text-wa-text text-right">#${conv.display_id ?? '—'}</dd>
                </div>
                <div class="flex justify-between gap-3">
                  <dt class="text-wa-secondary shrink-0">Criada em</dt>
                  <dd class="text-wa-text text-right">${fmtTs(conv.opened_at || conv.created_at)}</dd>
                </div>
                <div class="flex justify-between gap-3">
                  <dt class="text-wa-secondary shrink-0">Última atividade</dt>
                  <dd class="text-wa-text text-right">${fmtTs(conv.last_activity_at)}</dd>
                </div>
                ${conv.resolved_at ? html`
                  <div class="flex justify-between gap-3">
                    <dt class="text-wa-secondary shrink-0">Resolvida em</dt>
                    <dd class="text-wa-text text-right">${fmtTs(conv.resolved_at)}</dd>
                  </div>
                ` : null}
              </dl>
            </div>
          ` : null}
        </div>
      </div>

      ${missingAttrs ? html`
        <${RequiredAttributesModal} missing=${missingAttrs.list} onConfirm=${onMissingConfirm} />
      ` : null}
    </div>
  `;
}

