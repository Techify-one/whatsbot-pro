// Channels — ChannelEditForm (Plano 23 · D4), extracted verbatim from
// ChannelsManager.js. Edits the channel's display info + per-channel AI settings
// + the agents that see its inbox (+ GOWA jid types / WhatsApp Cloud creds /
// Telegram receive-mode info). Does NOT reconnect or change the QR. The PUT
// payload is built by the pure `buildEditPayload` (channels/constants.js).
import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import {
  updateChannel, getChannelMembers, setChannelMembers, telegramChannelStatus,
  getGowaAlertSettings,
} from '../../services/api.js';
import {
  parseChannelConfig, aiDefaultsFrom, DEFAULT_JID_TYPES, buildEditPayload,
} from './constants.js';
import { JidTypePicker } from './JidTypePicker.js';
import { AiSettingsFields } from './AiSettingsFields.js';
import { AgentPicker } from './AgentPicker.js';
import { fallbackCopyText } from './notices.js';

const html = htm.bind(h);

export function ChannelEditForm({ channel, onSaved, onCancel, aiDefaults }) {
  const isCloud = channel.provider === 'whatsapp_cloud';
  const isGowa = channel.provider === 'gowa';
  const isTelegram = channel.provider === 'telegram';
  // Telegram: live inbound status (per-channel mode + the actually-registered
  // webhook URL from getWebhookInfo), so a webhook channel can show + copy its URL.
  const [tgStatus, setTgStatus] = useState(null);
  const [tgCopied, setTgCopied] = useState(false);
  useEffect(() => {
    if (!isTelegram) return;
    let alive = true;
    (async () => {
      const res = await telegramChannelStatus(channel.id);
      if (alive && res && res.ok) setTgStatus(res.data || null);
    })();
    return () => { alive = false; };
  }, [isTelegram, channel.id]);
  // Webhook URL: prefer the one Telegram reports as registered; else the canonical
  // route for this channel on the current origin.
  const tgWebhookUrl = (tgStatus && tgStatus.webhook && tgStatus.webhook.url)
    || `${window.location.origin}/api/webhook/telegram/${channel.id}`;
  const tgIsWebhook = !!(tgStatus && (tgStatus.mode === 'webhook'
    || (tgStatus.webhook && tgStatus.webhook.url)));
  function copyTgWebhook() {
    const done = () => { setTgCopied(true); setTimeout(() => setTgCopied(false), 2000); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(tgWebhookUrl).then(done).catch(() => fallbackCopyText(tgWebhookUrl, done));
    } else { fallbackCopyText(tgWebhookUrl, done); }
  }
  const [displayName, setDisplayName] = useState(channel.display_name || channel.id);
  // Per-channel AI settings (config.ai): the stored overrides layered on top of
  // the global-derived defaults (so unset keys show the inherited value).
  const [ai, setAi] = useState(() => {
    const cfg = parseChannelConfig(channel.config);
    return { ...(aiDefaults || aiDefaultsFrom({})), ...(cfg.ai || {}) };
  });
  // GOWA: which chat types this channel surfaces. Seed from the stored config
  // (falling back to the default set when unset).
  const [jidTypes, setJidTypes] = useState(() => {
    const cfg = parseChannelConfig(channel.config);
    return Array.isArray(cfg.allowed_jid_types) && cfg.allowed_jid_types.length
      ? cfg.allowed_jid_types
      : DEFAULT_JID_TYPES;
  });
  // GOWA: per-channel on/off for the disconnect alert (Telegram). Seed from the
  // channel's own config; if it never set one (undefined), the effect below fills
  // it from the plugin's legacy global value so existing installs stay consistent.
  const [gowaAlertEnabled, setGowaAlertEnabled] = useState(() => {
    const cfg = parseChannelConfig(channel.config);
    return !!cfg.disconnect_alert_enabled;
  });
  useEffect(() => {
    if (!isGowa) return;
    const cfg = parseChannelConfig(channel.config);
    if (cfg.disconnect_alert_enabled !== undefined) return; // channel already explicit
    let alive = true;
    (async () => {
      const res = await getGowaAlertSettings();
      if (alive && res && res.ok && res.data) setGowaAlertEnabled(!!res.data.enabled);
    })();
    return () => { alive = false; };
  }, [isGowa, channel.id]);
  // whatsapp_cloud secrets: blank means "keep current"; only non-empty is sent.
  const [accessToken, setAccessToken] = useState('');
  const [phoneNumberId, setPhoneNumberId] = useState('');
  const [verifyToken, setVerifyToken] = useState('');
  // WABA ID is NOT a secret (returned in clear), so it is pre-filled and editable.
  const [wabaId, setWabaId] = useState((channel.credentials && channel.credentials.waba_id) || '');

  const [users, setUsers] = useState([]);
  const [selected, setSelected] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    (async () => {
      const res = await getChannelMembers(channel.id);
      if (!alive) return;
      if (res && res.ok) {
        setUsers(res.data.users || []);
        setSelected(res.data.member_ids || []);
      } else {
        setError((res && res.error) || 'Falha ao carregar agentes.');
      }
      setLoading(false);
    })();
    return () => { alive = false; };
  }, [channel.id]);

  async function save() {
    if (busy || !displayName.trim()) return;
    setBusy(true); setError('');
    // PUT replaces config wholesale — buildEditPayload preserves existing keys
    // (gowa_device_id, allowed_jid_types) while updating the per-channel AI
    // settings (plano 21) and sends only the non-empty whatsapp_cloud creds.
    const payload = buildEditPayload({
      displayName, ai, jidTypes, isGowa, isCloud,
      gowaAlertEnabled,
      channelConfig: channel.config,
      accessToken, phoneNumberId, wabaId, verifyToken,
    });
    const r1 = await updateChannel(channel.id, payload);
    if (!r1 || !r1.ok) {
      setBusy(false);
      setError((r1 && r1.error) || 'Falha ao salvar o canal.');
      return;
    }
    const r2 = await setChannelMembers(channel.id, selected);
    setBusy(false);
    if (!r2 || !r2.ok) {
      setError((r2 && r2.error) || 'Canal salvo, mas falha ao salvar os agentes.');
      return;
    }
    onSaved();
  }

  return html`
    <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick=${onCancel}>
      <div class="bg-wa-bg border border-wa-border rounded-lg p-5 w-full max-w-md max-h-[90vh] overflow-auto"
        onClick=${(e) => e.stopPropagation()}>
        <div class="text-[15px] font-medium text-wa-text mb-1">Editar canal</div>
        <div class="text-[12px] text-wa-secondary mb-4 font-mono break-words">${channel.id}</div>

        <div class="flex flex-col gap-3">
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Nome de exibição</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="text" value=${displayName} onInput=${(e) => setDisplayName(e.target.value)} />
          </div>

          ${isTelegram ? html`
            <div class="border-t border-wa-border pt-3">
              <label class="block text-[12px] text-wa-secondary mb-1">Recebimento de mensagens</label>
              ${tgIsWebhook ? html`
                <p class="text-[12px] text-wa-secondary mb-2">
                  Este canal recebe por <span class="font-medium text-wa-text">webhook</span>. URL registrada na Bot API do Telegram:
                </p>
                <div class="flex gap-2 items-center flex-wrap">
                  <code class="flex-1 min-w-0 break-all px-3 py-2 rounded-md text-[13px] bg-wa-panel border border-wa-border text-wa-text">${tgWebhookUrl}</code>
                  <button type="button" class="px-3 py-2 rounded-md text-[13px] text-wa-text border border-wa-border hover:bg-wa-hover transition-colors shrink-0"
                    onClick=${copyTgWebhook}>${tgCopied ? 'Copiado!' : 'Copiar'}</button>
                </div>
                ${tgStatus && tgStatus.webhook && tgStatus.webhook.last_error_message ? html`
                  <div class="text-[12px] text-red-500 mt-1">Último erro do Telegram: ${tgStatus.webhook.last_error_message}</div>` : null}
              ` : html`
                <p class="text-[12px] text-wa-secondary">
                  Este canal recebe por <span class="font-medium text-wa-text">long-poll</span> (getUpdates) — não usa webhook.
                  ${tgStatus ? '' : ' Carregando estado…'}
                </p>`}
            </div>
          ` : null}

          ${isGowa ? html`
            <div class="border-t border-wa-border pt-3">
              <label class="block text-[12px] text-wa-secondary mb-1">O que deve aparecer no painel</label>
              <p class="text-[12px] text-wa-secondary mb-2">
                Escolha quais tipos de conversa deste número viram conversa. Os tipos
                desmarcados são ignorados (não aparecem no painel).
              </p>
              <${JidTypePicker} selected=${jidTypes} onChange=${setJidTypes} disabled=${busy} />
            </div>

            <div class="border-t border-wa-border pt-3">
              <label class="flex items-center gap-2 text-[14px] text-wa-text">
                <input type="checkbox" checked=${gowaAlertEnabled} disabled=${busy}
                  onChange=${(e) => setGowaAlertEnabled(e.target.checked)} />
                Ativar alertas de desconexão (Telegram)
              </label>
              <p class="text-[12px] text-wa-secondary mt-1">
                Avisa no Telegram quando este número cair. O bot, o chat de destino, o
                intervalo e o fuso são configurados em Plugins → WhatsApp (GOWA) → Configurar.
              </p>
            </div>
          ` : null}

          ${isCloud ? html`
            <div class="border-t border-wa-border pt-3">
              <label class="block text-[12px] text-wa-secondary mb-1">WABA ID <span class="text-wa-secondary">(para templates)</span></label>
              <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]" type="text"
                placeholder="WhatsApp Business Account ID" value=${wabaId}
                onInput=${(e) => setWabaId(e.target.value)} />
              <div class="text-[12px] text-wa-secondary mt-1 mb-3">
                Necessário para listar e criar templates (HSM). Diferente do Phone Number ID.
              </div>
              <div class="text-[12px] text-wa-secondary mb-2">Demais credenciais — deixe em branco para manter a atual.</div>
              <div class="flex flex-col gap-2">
                <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]" type="password"
                  placeholder="Access Token (manter)" value=${accessToken}
                  onInput=${(e) => setAccessToken(e.target.value)} />
                <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]" type="text"
                  placeholder="Phone Number ID (manter)" value=${phoneNumberId}
                  onInput=${(e) => setPhoneNumberId(e.target.value)} />
                <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]" type="text"
                  placeholder="Verify Token (manter)" value=${verifyToken}
                  onInput=${(e) => setVerifyToken(e.target.value)} />
              </div>
            </div>
          ` : null}

          <div class="border-t border-wa-border pt-3">
            <label class="block text-[12px] text-wa-secondary mb-2">Inteligência Artificial</label>
            <${AiSettingsFields} value=${ai} onChange=${setAi} />
          </div>

          <div class="border-t border-wa-border pt-3">
            <label class="block text-[12px] text-wa-secondary mb-1">Agentes desta caixa de entrada</label>
            <p class="text-[12px] text-wa-secondary mb-2">
              Os agentes selecionados veem as conversas deste canal e recebem as mensagens que caírem aqui.
              Administradores veem todos os canais.
            </p>
            ${loading
              ? html`<div class="text-[13px] text-wa-secondary">Carregando agentes…</div>`
              : html`<${AgentPicker} users=${users} selected=${selected} onChange=${setSelected} />`}
          </div>

          ${error ? html`<div class="text-[13px] text-red-500">${error}</div>` : null}

          <div class="flex gap-2 justify-end mt-1">
            <button class="px-3 py-2 rounded-md text-[14px] text-wa-text hover:bg-wa-hover transition-colors"
              onClick=${onCancel} disabled=${busy}>Cancelar</button>
            <button class="px-4 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50"
              onClick=${save} disabled=${busy || loading || !displayName.trim()}>
              ${busy ? 'Salvando…' : 'Salvar'}</button>
          </div>
        </div>
      </div>
    </div>
  `;
}
