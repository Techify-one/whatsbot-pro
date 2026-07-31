// Extensão de frontend (frontend_extends): o app importa este arquivo UMA vez no
// boot e chama register(api). Adiciona um botão "Jornada" no cabeçalho da conversa
// (slot conversation.header.actions). Ao clicar, abre o modal com a jornada do
// contato no Trackify (o CDP do Nexus).
//
// Aditivo: desativar o plugin remove o slot no próximo boot; o core fica idêntico.
// Molde: storages/plugins/agendamento_retorno/static/extends.js
import { h } from 'preact';
import htm from 'htm';
import { JourneyModal } from '/plugins/trackify/static/JourneyModal.js';

const html = htm.bind(h);

function currentUser() {
  try { return JSON.parse(localStorage.getItem('whatsbot_user') || 'null'); }
  catch (_) { return null; }
}

export default function register(api) {
  // Default-allow em instalação legada/sem RBAC (mesmo padrão do agendamento_retorno).
  const can = (key) => (api.services && api.services.hasPermission
    ? api.services.hasPermission(currentUser(), `plugin.trackify.${key}`) : true);

  api.addSlot('conversation.header.actions', ({ conv }) => {
    if (!conv || !can('view')) return null;

    async function openJourney() {
      // O contact_id do slot basta: a rota resolve telefone/e-mail NO SERVIDOR.
      // Nunca mandamos telefone na query string (log de proxy + spoofing).
      let contactId = conv.contact_id;
      if (contactId == null) {
        try {
          const r = await api.http.get(`/api/atendimentos/${conv.id}`);
          if (r && r.ok && r.data) {
            const d = r.data.conversation || r.data;
            contactId = d.contact_id;
          }
        } catch (_) { /* segue sem: o modal mostra o erro */ }
      }

      await api.ui.openModal((close) => html`
        <${JourneyModal} api=${api} contactId=${contactId} onClose=${() => close(null)} />
      `);
    }

    return html`
      <button type="button" onClick=${openJourney}
        title="Jornada do cliente no Trackify" aria-label="Jornada do cliente no Trackify"
        class="px-2 py-1 rounded-md text-wa-secondary hover:text-wa-teal hover:bg-wa-hover transition-colors flex items-center gap-1">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M3 17h2v-6H3v6zm4 4h2V7H7v14zm4-4h2V3h-2v14zm4 4h2V9h-2v12zm4-8h2v-4h-2v4z"/></svg>
        <span class="hidden sm:inline text-[12px]">Jornada</span>
      </button>`;
  });
}
