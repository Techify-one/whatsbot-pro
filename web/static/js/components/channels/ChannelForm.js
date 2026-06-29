// Channels — ChannelForm (Plano 23 · D4), extracted verbatim from
// ChannelsManager.js. Create-channel modal: pick a provider, a display name, the
// provider's credential fields, the per-channel AI settings, and the inbox agents.
// The create-payload construction is delegated to the pure `buildCreatePayload`
// (channels/constants.js) so the provider branching is locked by node --test.
import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import { listChannelAssignableUsers } from '../../services/api.js';
import {
  PROVIDERS, REQUIRED_CREDS_FALLBACK, DEFAULT_JID_TYPES,
  aiDefaultsFrom, randomToken, buildCreatePayload,
} from './constants.js';
import { JidTypePicker } from './JidTypePicker.js';
import { AiSettingsFields } from './AiSettingsFields.js';
import { AgentPicker } from './AgentPicker.js';

const html = htm.bind(h);

export function ChannelForm({ onCreated, onCancel, busy, error, aiDefaults, availableProviders, requiredCreds }) {
  // Only providers whose backing plugin is enabled are offered (GOWA is core and
  // always present). Falls back to the full catalogue while the list is still
  // loading. The badge/label catalogue (PROVIDERS) is unfiltered — existing
  // channels keep their badge even if their provider's plugin is later disabled.
  const providerEntries = Object.entries(PROVIDERS).filter(
    ([key]) => !availableProviders || availableProviders.includes(key));
  const [provider, setProvider] = useState(
    () => (availableProviders && availableProviders[0]) || 'gowa');
  const [displayName, setDisplayName] = useState('');
  // Per-channel AI settings (config.ai), seeded from the current global config.
  const [ai, setAi] = useState(() => aiDefaults || aiDefaultsFrom({}));
  // Provider-specific credential/config fields.
  // The GOWA device id is auto-generated and read-only: each channel maps to its
  // own GOWA device (one WhatsApp number) on the shared GOWA process.
  const [gowaDeviceId, setGowaDeviceId] = useState(() => `gowa_${randomToken(10)}`);
  // Which chat types this GOWA channel surfaces (config.allowed_jid_types).
  const [jidTypes, setJidTypes] = useState(DEFAULT_JID_TYPES);
  const [accessToken, setAccessToken] = useState('');
  const [phoneNumberId, setPhoneNumberId] = useState('');
  const [wabaId, setWabaId] = useState('');
  const [verifyToken, setVerifyToken] = useState('');
  const [botToken, setBotToken] = useState('');
  // Agents to assign to the new channel's inbox (all providers). Loaded once.
  const [users, setUsers] = useState([]);
  const [agentIds, setAgentIds] = useState([]);

  useEffect(() => {
    let alive = true;
    (async () => {
      const res = await listChannelAssignableUsers();
      if (alive && res && res.ok) setUsers(res.data.users || []);
    })();
    return () => { alive = false; };
  }, []);

  // O ID do canal é gerado automaticamente pelo backend (o usuário só escolhe o
  // nome de exibição). GOWA reusa o device id; demais providers, "<provider>_<hex>".
  // Credenciais obrigatórias do provider (capability-driven; backend é a fonte da
  // verdade, com fallback local enquanto a lista não chega) — sem elas o canal
  // nasceria "morto" (nunca conecta). O backend também rejeita; aqui é só UX.
  const required = (requiredCreds && requiredCreds[provider]) || REQUIRED_CREDS_FALLBACK[provider] || [];
  const credValues = {
    access_token: accessToken, phone_number_id: phoneNumberId,
    waba_id: wabaId, verify_token: verifyToken, bot_token: botToken,
  };
  const credsOk = required.every((k) => (credValues[k] || '').trim());
  const isRequired = (k) => required.includes(k);
  const canSave = !busy && displayName.trim() && credsOk;

  function submit() {
    if (!canSave) return;
    const payload = buildCreatePayload({
      provider, displayName, ai, gowaDeviceId, jidTypes,
      accessToken, phoneNumberId, wabaId, verifyToken, botToken,
    });
    onCreated(payload, agentIds);
  }

  return html`
    <div class="bg-wa-panel border border-wa-border rounded-lg p-4 mb-4">
      <div class="text-[14px] font-medium text-wa-text mb-3">Novo canal</div>
      <div class="flex flex-col gap-3">
        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Provider</label>
          <select class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
            value=${provider} onChange=${(e) => setProvider(e.target.value)} disabled=${busy}>
            ${providerEntries.map(([key, meta]) => html`
              <option key=${key} value=${key}>${meta.label}</option>
            `)}
          </select>
        </div>

        <div>
          <label class="block text-[12px] text-wa-secondary mb-1">Nome de exibição</label>
          <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
            type="text" placeholder="ex: Atendimento WhatsApp" value=${displayName}
            onInput=${(e) => setDisplayName(e.target.value)} />
        </div>

        ${provider === 'gowa' ? html`
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">GOWA Device ID <span class="text-wa-secondary">(gerado automaticamente)</span></label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px] opacity-60 cursor-not-allowed"
              type="text" value=${gowaDeviceId} readonly disabled />
            <div class="text-[12px] text-wa-secondary mt-1">
              Identifica este número dentro do GOWA. Após criar, leia o QR Code para conectar o WhatsApp.
            </div>
          </div>
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">O que deve aparecer no painel</label>
            <p class="text-[12px] text-wa-secondary mb-2">
              Escolha quais tipos de conversa deste número viram atendimento. Os tipos
              desmarcados são ignorados (não aparecem no painel).
            </p>
            <${JidTypePicker} selected=${jidTypes} onChange=${setJidTypes} disabled=${busy} />
          </div>
        ` : null}

        ${provider === 'whatsapp_cloud' ? html`
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Access Token${isRequired('access_token') ? html`<span class="text-red-500"> *</span>` : null}</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="password" placeholder="EAAB..." value=${accessToken}
              onInput=${(e) => setAccessToken(e.target.value)} />
          </div>
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Phone Number ID${isRequired('phone_number_id') ? html`<span class="text-red-500"> *</span>` : null}</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="text" placeholder="ID do número (Meta)" value=${phoneNumberId}
              onInput=${(e) => setPhoneNumberId(e.target.value)} />
          </div>
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">WABA ID <span class="text-wa-secondary">(para templates)</span></label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="text" placeholder="WhatsApp Business Account ID" value=${wabaId}
              onInput=${(e) => setWabaId(e.target.value)} />
            <div class="text-[12px] text-wa-secondary mt-1">
              Necessário para listar e criar templates (HSM). Em WhatsApp Manager → Configurações da conta. Diferente do Phone Number ID.
            </div>
          </div>
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Verify Token${isRequired('verify_token') ? html`<span class="text-red-500"> *</span>` : null}</label>
            <div class="flex gap-2">
              <input class="wa-field flex-1 px-3 py-2 rounded-md text-[14px]"
                type="text" placeholder="token de verificação do webhook" value=${verifyToken}
                onInput=${(e) => setVerifyToken(e.target.value)} />
              <button type="button"
                class="px-3 py-2 rounded-md text-[13px] text-wa-text border border-wa-border hover:bg-wa-hover transition-colors shrink-0"
                onClick=${() => setVerifyToken(randomToken())}>Sugerir</button>
            </div>
          </div>
        ` : null}

        ${provider === 'telegram' ? html`
          <div>
            <label class="block text-[12px] text-wa-secondary mb-1">Bot Token${isRequired('bot_token') ? html`<span class="text-red-500"> *</span>` : null}</label>
            <input class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
              type="password" placeholder="123456:ABC-DEF... (do @BotFather)" value=${botToken}
              onInput=${(e) => setBotToken(e.target.value)} />
            <div class="text-[12px] text-wa-secondary mt-1">
              Crie um bot com o <span class="font-medium">@BotFather</span> (<code>/newbot</code>) e cole o token.
              Recebe por long-poll (sem host público) — basta criar e mandar mensagem ao bot.
            </div>
          </div>
        ` : null}

        <div class="border-t border-wa-border pt-3">
          <label class="block text-[12px] text-wa-secondary mb-2">Inteligência Artificial</label>
          <${AiSettingsFields} value=${ai} onChange=${setAi} sequentialDefault=${provider === 'gowa'} />
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
