// Channels — ChannelEditForm (edit). Plano 33: fully DESCRIPTOR-DRIVEN, no
// `if provider === ...`. Edits the display name + the provider's editable config
// fields (e.g. GOWA jid types) + its credentials ("keep current" when blank) +
// the per-channel AI settings + the inbox agents. Provider-specific status/config
// (e.g. Telegram webhook vs long-poll) lives in that plugin's own config screen,
// not here. The PUT payload is built by the pure `buildEditPayload`.
import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import { updateChannel, getChannelMembers, setChannelMembers } from '../../services/api.js';
import { parseChannelConfig, aiDefaultsFrom, buildEditPayload } from './constants.js';
import { ConfigFields, CredentialFields, FormComponentLoader } from './DescriptorFields.js';
import { EmbedSnippetBlock } from './notices.js';
import { AiSettingsFields } from './AiSettingsFields.js';
import { AgentPicker } from './AgentPicker.js';

const html = htm.bind(h);

export function ChannelEditForm({ channel, descriptor, onSaved, onCancel, aiDefaults }) {
  const [displayName, setDisplayName] = useState(channel.display_name || channel.id);
  // Per-channel AI settings (config.ai): stored overrides layered on the
  // global-derived defaults (so unset keys show the inherited value).
  const [ai, setAi] = useState(() => {
    const cfg = parseChannelConfig(channel.config);
    return { ...(aiDefaults || aiDefaultsFrom({})), ...(cfg.ai || {}) };
  });
  // Editable config-field values (generated fields are immutable → skipped).
  const [configValues, setConfigValues] = useState(() => {
    const cfg = parseChannelConfig(channel.config);
    const out = {};
    for (const f of (descriptor && descriptor.config_fields) || []) {
      if (f.type === 'generated') continue;
      if (f.type === 'multiselect') {
        const cur = cfg[f.key];
        out[f.key] = Array.isArray(cur) && cur.length
          ? cur : (Array.isArray(f.default) ? f.default.slice() : []);
      } else {
        out[f.key] = cfg[f.key] != null ? cfg[f.key] : (f.default != null ? f.default : '');
      }
    }
    return out;
  });
  // Credential values: pre-fill only NON-masked (public) values the API returns in
  // clear (e.g. waba_id, phone_number_id); secrets come masked (••••) → blank =
  // "keep current".
  const [credValues, setCredValues] = useState(() => {
    const stored = channel.credentials || {};
    const out = {};
    for (const f of (descriptor && descriptor.credential_fields) || []) {
      const v = stored[f.key];
      if (v && !String(v).startsWith('••••')) out[f.key] = v;
    }
    return out;
  });

  const [users, setUsers] = useState([]);
  const [selected, setSelected] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const setConfig = (key, value) => setConfigValues((prev) => ({ ...prev, [key]: value }));
  const setCred = (key, value) => setCredValues((prev) => ({ ...prev, [key]: value }));

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
    const payload = buildEditPayload({
      displayName, ai, descriptor, channelConfig: channel.config, credValues, configValues,
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

  const hasConfig = descriptor && (descriptor.config_fields || [])
    .some((f) => f.type !== 'generated');
  const hasCreds = descriptor && (descriptor.credential_fields || []).length;
  // Widget (plano 46/76): se o provider declara o post-create `embed_snippet`,
  // mostra o bloco copiar-de-novo. O token público e o TEMPLATE do snippet vêm do
  // descriptor (token_config_key / snippet_template) — o core não conhece o nome
  // literal da chave nem o path do plugin.
  const embedPc = (descriptor && descriptor.post_create
    && descriptor.post_create.kind === 'embed_snippet') ? descriptor.post_create : null;
  const embedToken = embedPc
    ? (parseChannelConfig(channel.config)[embedPc.token_config_key] || '')
    : '';

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

          ${embedToken ? html`
            <div class="border-t border-wa-border pt-3">
              <label class="block text-[12px] text-wa-secondary mb-2">Snippet de instalação</label>
              <${EmbedSnippetBlock} widgetToken=${embedToken} baseUrl=${window.location.origin}
                template=${embedPc.snippet_template} />
            </div>
          ` : null}

          ${hasConfig ? html`
            <div class="border-t border-wa-border pt-3 flex flex-col gap-3">
              <${ConfigFields} fields=${descriptor.config_fields} values=${configValues}
                onChange=${setConfig} editMode=${true} busy=${busy} />
            </div>
          ` : null}

          ${hasCreds ? html`
            <div class="border-t border-wa-border pt-3 flex flex-col gap-2">
              <div class="text-[12px] text-wa-secondary">Credenciais — deixe em branco para manter a atual.</div>
              <${CredentialFields} fields=${descriptor.credential_fields} values=${credValues}
                onChange=${setCred} editMode=${true} busy=${busy} />
            </div>
          ` : null}

          ${descriptor && descriptor.form_component ? html`
            <div class="border-t border-wa-border pt-3">
              <${FormComponentLoader} path=${descriptor.form_component}
                descriptor=${descriptor} mode="edit" channel=${channel}
                credValues=${credValues} setCred=${setCred}
                configValues=${configValues} setConfig=${setConfig} busy=${busy} />
            </div>
          ` : null}

          <div class="border-t border-wa-border pt-3">
            <label class="block text-[12px] text-wa-secondary mb-2">Inteligência Artificial</label>
            <${AiSettingsFields} value=${ai} onChange=${setAi} users=${users} />
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
