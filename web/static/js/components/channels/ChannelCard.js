// Channels — ChannelCard (Plano 23 · D4), extracted verbatim from
// ChannelsManager.js. A single channel's card: identity + provider badge +
// connected/logged-in dots + own_phone + credentials, the zombie-channel warning
// (missing required creds), the raw error fallback, and the per-card actions.
import { h } from 'preact';
import htm from 'htm';
import { providerMeta, credLabel, missingCredsFor } from './constants.js';
import { Dot } from './notices.js';
import { Slot } from '../../plugins/Slot.js';
import { CopyLinkButton } from '../../utils/copyDeepLink.js';

const html = htm.bind(h);

export function ChannelCard({ channel, onToggle, onDelete, onPurge, onRefresh, onConnect, onReconnect, onLogout, onEdit, busyId, requiredCreds, descriptorsById }) {
  const descriptor = descriptorsById && descriptorsById[channel.provider];
  const meta = providerMeta(channel.provider, descriptorsById);
  const cred = channel.credentials || {};
  const credEntries = Object.entries(cred);
  const busy = busyId === channel.id;
  // Session actions (QR/linked-device providers, capability-driven — plano 33):
  // "Conectar" (read QR) when not logged in, "Reconectar" when paired but the
  // socket is down, "Desconectar" whenever there's a session to drop. Gated by
  // the descriptor's ``needs_qr`` capability, never by provider name.
  const needsQr = !!(descriptor && descriptor.capabilities && descriptor.capabilities.needs_qr);
  const canConnect = needsQr && !channel.logged_in;
  const canReconnect = needsQr && channel.logged_in && !channel.connected;
  const canLogout = needsQr && channel.logged_in;
  // Zombie-channel detection: required credentials this channel is missing
  // (capability-driven via the providers fetch, local fallback otherwise). A
  // credential-only provider missing these can never connect — flag it with a
  // shortcut to Editar instead of leaving the raw "missing_credentials" error.
  const missingCreds = missingCredsFor(channel, requiredCreds);
  // The backend status() returns this raw code for the same condition; the
  // friendly warning below replaces it, so don't show both.
  const showRawError = channel.last_error && channel.last_error !== 'missing_credentials';

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-3 flex items-start gap-3 flex-wrap">
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 flex-wrap">
          <span class="text-[14px] text-wa-text font-medium truncate">${channel.display_name || channel.id}</span>
          <span class="px-2 py-0.5 rounded-full text-[11px] ${meta.tint}">${meta.label}</span>
          ${channel.enabled
            ? html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-green-50 text-green-700">Ativo</span>`
            : html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-wa-hover text-wa-secondary">Inativo</span>`}
        </div>
        <div class="text-[12px] text-wa-secondary mt-0.5 font-mono break-words">${channel.id}</div>

        <div class="flex items-center gap-4 mt-2 flex-wrap">
          <span class="flex items-center gap-1.5 text-[12px] text-wa-secondary">
            <${Dot} on=${channel.connected} /> ${channel.connected ? 'Conectado' : 'Desconectado'}
          </span>
          <span class="flex items-center gap-1.5 text-[12px] text-wa-secondary">
            <${Dot} on=${channel.logged_in} /> ${channel.logged_in ? 'Autenticado' : 'Não autenticado'}
          </span>
          ${channel.own_phone ? html`
            <span class="text-[12px] text-wa-secondary">📱 ${channel.own_phone}</span>
          ` : null}
        </div>

        <${Slot} name="channel.card.rows" ctx=${{ channel, descriptor }} />

        ${credEntries.length ? html`
          <div class="text-[12px] text-wa-secondary mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5">
            ${credEntries.map(([k, v]) => html`
              <span key=${k}><span class="text-wa-text">${k}:</span> <span class="font-mono">${v}</span></span>
            `)}
          </div>
        ` : null}

        ${missingCreds.length ? html`
          <div class="text-[12px] text-amber-700 bg-amber-50 border border-amber-200 rounded-md px-2 py-1.5 mt-2 break-words">
            ⚠️ Credenciais faltando: ${missingCreds.map((k) => credLabel(k, descriptor)).join(', ')}.
            Este canal não vai conectar até serem preenchidas —
            <button class="underline hover:no-underline font-medium"
              onClick=${() => onEdit(channel)}>editar agora</button>.
          </div>
        ` : null}

        ${showRawError ? html`
          <div class="text-[12px] text-red-600 bg-red-50 border border-red-200 rounded-md px-2 py-1 mt-2 break-words">
            ${channel.last_error}
          </div>
        ` : null}
      </div>

      <div class="flex gap-1 shrink-0 flex-wrap justify-end items-center">
        <${CopyLinkButton} path=${`/channels/${encodeURIComponent(channel.id)}`}
          title="Copiar link deste canal" />
        ${canConnect ? html`
          <button class="px-2 py-1 rounded-md text-[13px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50"
            onClick=${() => onConnect(channel)} disabled=${busy}>Conectar</button>
        ` : null}
        ${canReconnect ? html`
          <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50"
            onClick=${() => onReconnect(channel)} disabled=${busy}>Reconectar</button>
        ` : null}
        ${canLogout ? html`
          <button class="px-2 py-1 rounded-md text-[13px] text-red-500 hover:bg-wa-hover transition-colors disabled:opacity-50"
            onClick=${() => onLogout(channel)} disabled=${busy}>Desconectar</button>
        ` : null}
        <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50"
          onClick=${() => onEdit(channel)} disabled=${busy}>Editar</button>
        <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50"
          onClick=${() => onRefresh(channel)} disabled=${busy}>
          ${busy ? '…' : 'Atualizar'}</button>
        <button class="px-2 py-1 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors disabled:opacity-50"
          onClick=${() => onToggle(channel)} disabled=${busy}>
          ${channel.enabled ? 'Desativar' : 'Ativar'}</button>
        <button class="px-2 py-1 rounded-md text-[13px] text-red-500 hover:bg-wa-hover transition-colors disabled:opacity-50"
          onClick=${() => onDelete(channel)} disabled=${busy}>Arquivar</button>
        <button class="px-2 py-1 rounded-md text-[13px] text-red-600 hover:bg-red-50 transition-colors disabled:opacity-50"
          onClick=${() => onPurge(channel)} disabled=${busy}>Excluir</button>
      </div>
    </div>
  `;
}
