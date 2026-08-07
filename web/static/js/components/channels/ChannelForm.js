// Channels — ChannelForm (create). Plano 33: fully DESCRIPTOR-DRIVEN. The form
// picks a provider from the installed descriptors (GET /api/channels/providers)
// and renders that provider's config/credential fields generically — there is NO
// `if provider === 'gowa'/'telegram'/'whatsapp_cloud'` here. The per-channel AI
// settings (plano 21) are core and always rendered. The payload is assembled by
// the pure `buildCreatePayload` (channels/constants.js), locked by node --test.
import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import { listChannelAssignableUsers } from '../../services/api.js';
import {
  aiDefaultsFrom, buildCreatePayload, initialConfigValues, validateCredentials,
} from './constants.js';
import { ConfigFields, CredentialFields, FormComponentLoader } from './DescriptorFields.js';
import { AiSettingsFields } from './AiSettingsFields.js';
import { AgentPicker } from './AgentPicker.js';

const html = htm.bind(h);

export function ChannelForm({ onCreated, onCancel, onProviderChange, initialProvider, busy, error, aiDefaults, providers }) {
  // Descriptors of the installed providers (null while loading). The picker and
  // the whole form are driven by these; no local provider catalogue.
  const descriptors = providers || [];
  const byId = Object.fromEntries(descriptors.map((d) => [d.provider, d]));
  // Pré-seleção via deep-link (/channels/new?provider=…) tem precedência, desde
  // que o provider exista nos descriptors; senão cai no 1º disponível.
  const [provider, setProvider] = useState(
    () => (initialProvider && byId[initialProvider] && initialProvider)
      || (descriptors[0] && descriptors[0].provider) || '');
  const descriptor = byId[provider] || null;

  const [displayName, setDisplayName] = useState('');
  const [ai, setAi] = useState(() => aiDefaults || aiDefaultsFrom({}));
  // Descriptor-driven field state, keyed by field key. Re-seeded when the
  // provider changes (generated fields get a fresh value, multiselects their
  // defaults). credValues start empty.
  const [credValues, setCredValues] = useState({});
  const [configValues, setConfigValues] = useState(() => initialConfigValues(descriptor));
  // Agents to assign to the new channel's inbox (all providers). Loaded once.
  const [users, setUsers] = useState([]);
  const [agentIds, setAgentIds] = useState([]);

  // When a descriptor becomes available (or the provider changes), re-seed the
  // provider-specific field state.
  useEffect(() => {
    setCredValues({});
    setConfigValues(initialConfigValues(descriptor));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider, !!descriptor]);

  // Se o provider selecionado sumir dos descriptors (fetch tardio), cai no 1º.
  useEffect(() => {
    if (!provider && descriptors.length) pickProvider(descriptors[0].provider);
    else if (provider && !byId[provider] && descriptors.length) pickProvider(descriptors[0].provider);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providers]);

  function pickProvider(p) { setProvider(p); if (onProviderChange) onProviderChange(p); }

  useEffect(() => {
    let alive = true;
    (async () => {
      const res = await listChannelAssignableUsers();
      if (alive && res && res.ok) setUsers(res.data.users || []);
    })();
    return () => { alive = false; };
  }, []);

  // Reporta o provider inicial ao pai no mount, para a URL (?provider=) refletir.
  useEffect(() => { if (provider && onProviderChange) onProviderChange(provider); }, []);

  const setCred = (key, value) => setCredValues((prev) => ({ ...prev, [key]: value }));
  const setConfig = (key, value) => setConfigValues((prev) => ({ ...prev, [key]: value }));

  // Required credentials come from the descriptor's fields (capability-driven on
  // the backend). Without them the channel would be born "dead" (never connects).
  const requiredKeys = ((descriptor && descriptor.credential_fields) || [])
    .filter((f) => f.required).map((f) => f.key);
  const credsOk = requiredKeys.every((k) => (credValues[k] || '').toString().trim());
  // Formato das credenciais preenchidas, dirigido pelo descriptor (plano 104 F3):
  // um valor autopreenchido pelo navegador (senha do painel no "Proxy de saída")
  // não chega a ser salvo. O servidor revalida — isto aqui é só o aviso na hora.
  const credErrors = validateCredentials(descriptor, credValues);
  const canSave = !busy && !!descriptor && displayName.trim() && credsOk
    && !Object.keys(credErrors).length;

  function submit() {
    if (!canSave) return;
    const payload = buildCreatePayload({ provider, displayName, ai, descriptor, credValues, configValues });
    onCreated(payload, agentIds);
  }

  if (!descriptors.length) {
    return html`<div class="bg-wa-panel border border-wa-border rounded-lg p-4 mb-4 text-[14px] text-wa-secondary">
      Carregando providers de canal…
    </div>`;
  }

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-4 mb-4">
      <div class="text-[14px] font-medium text-wa-text mb-3">Novo canal</div>
      <div class="flex flex-col gap-3">
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Provider</label>
          <select class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
            value=${provider} onChange=${(e) => pickProvider(e.target.value)} disabled=${busy}>
            ${descriptors.map((d) => html`
              <option key=${d.provider} value=${d.provider}>${d.label || d.provider}</option>
            `)}
          </select>
        </div>

        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Nome de exibição</label>
          <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
            type="text" placeholder="ex: Suporte WhatsApp" value=${displayName}
            onInput=${(e) => setDisplayName(e.target.value)} />
        </div>

        ${descriptor ? html`
          <${ConfigFields} fields=${descriptor.config_fields} values=${configValues}
            onChange=${setConfig} editMode=${false} busy=${busy} />
          <${CredentialFields} fields=${descriptor.credential_fields} values=${credValues}
            onChange=${setCred} editMode=${false} busy=${busy} errors=${credErrors} />
          ${descriptor.form_component ? html`
            <${FormComponentLoader} path=${descriptor.form_component}
              descriptor=${descriptor} mode="create"
              credValues=${credValues} setCred=${setCred}
              configValues=${configValues} setConfig=${setConfig} busy=${busy} />
          ` : null}
        ` : null}

        <div class="border-t border-wa-border pt-3">
          <label class="block text-[12px] text-wa-secondary mb-2">Inteligência Artificial</label>
          <${AiSettingsFields} value=${ai} onChange=${setAi} users=${users}
            sequentialDefault=${!!(descriptor && descriptor.ai_sequential_default)} />
        </div>

        <div class="border-t border-wa-border pt-3">
          <label class="block text-[12px] text-wa-secondary mb-1">Agentes desta caixa de entrada</label>
          <p class="text-[12px] text-wa-secondary mb-2">
            Os agentes selecionados veem as conversas deste canal e recebem as mensagens que caírem aqui.
            Administradores veem todos os canais.
          </p>
          <${AgentPicker} users=${users} selected=${agentIds} onChange=${setAgentIds} />
        </div>

        ${error ? html`<div class="text-[13px] text-red-500">${error}</div>` : null}

        <div class="flex gap-2 justify-end">
          <button class="px-3 py-2 rounded-md text-[14px] text-wa-text hover:bg-wa-hover transition-colors"
            onClick=${onCancel} disabled=${busy}>Cancelar</button>
          <button class="px-4 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50"
            onClick=${submit} disabled=${!canSave}>${busy ? 'Criando…' : 'Criar canal'}</button>
        </div>
      </div>
    </div>
  `;
}
