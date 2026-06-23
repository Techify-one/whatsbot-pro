// Helpers visuais compartilhados pela tela de Atendimentos (lista + kanban).
// Espelham o visual de Conversations.js (relativeTime, badges) para manter a
// identidade e a legibilidade no modo escuro (classes wa-*).
import { h } from 'preact';
import htm from 'htm';

const html = htm.bind(h);

// Epoch (segundos, float) → rótulo relativo "há ...".
export function relativeTime(epochSeconds) {
  if (!epochSeconds) return '—';
  const then = epochSeconds * 1000;
  const diff = Date.now() - then;
  if (diff < 0) return 'agora';
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return 'há instantes';
  const min = Math.floor(sec / 60);
  if (min < 60) return `há ${min} min`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `há ${hr} h`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `há ${day} d`;
  try {
    return new Date(then).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric' });
  } catch (e) {
    return '—';
  }
}

export function GroupIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" class="text-wa-secondary shrink-0" aria-label="Grupo">
      <path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z"/>
    </svg>
  `;
}

export function StatusBadge({ status }) {
  const open = status === 'open';
  const cls = open ? 'bg-wa-teal/15 text-wa-teal' : 'bg-wa-hover text-wa-secondary';
  return html`<span class="px-2 py-0.5 rounded-full text-[11px] font-medium ${cls}">${open ? 'Aberta' : 'Fechada'}</span>`;
}

const CHANNEL_META = {
  gowa: { label: 'WhatsApp', cls: 'bg-wa-teal/15 text-wa-teal' },
  whatsapp_cloud: { label: 'Cloud API', cls: 'bg-blue-100 text-blue-700' },
  telegram: { label: 'Telegram', cls: 'bg-blue-100 text-blue-700' },
  test: { label: 'Teste', cls: 'bg-wa-hover text-wa-secondary' },
};

export function ChannelBadge({ provider, name }) {
  if (!provider) return null;
  const meta = CHANNEL_META[provider] || { label: provider, cls: 'bg-wa-hover text-wa-secondary' };
  return html`
    <span class="px-2 py-0.5 rounded-full text-[11px] font-medium ${meta.cls} inline-flex items-center gap-1"
      title=${name ? `Canal: ${name} (${provider})` : `Canal: ${provider}`}>
      <span class="w-1.5 h-1.5 rounded-full bg-current opacity-70" aria-hidden="true"></span>
      ${name || meta.label}
    </span>
  `;
}

// Chip de etiqueta no mesmo estilo do ConversationLabelEditor (alpha seguro no escuro).
export function LabelChip({ name, color }) {
  const c = color || '#6b7280';
  return html`
    <span class="px-1.5 py-0.5 rounded text-[10px] font-medium"
      style=${`background:${c}20;color:${c};border:1px solid ${c}40;`}>${name}</span>
  `;
}

export function nameOf(convo) {
  return convo.contact_name || convo.contact_phone || `Conversa #${convo.display_id ?? convo.id}`;
}
