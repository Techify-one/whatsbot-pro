// AI Engine — "Configurações" tab. Holds the AI settings moved out of the
// main Painel (ConfigPanel): the chat model, the agent instructions
// (system_prompt) and the behaviour block (context size, batching, split
// messages, transfer alert, low-balance alert). Self-contained: loads via
// getConfig() and persists via saveConfig() (partial PUT — only sends the keys
// it owns, so the Painel keeps owning the rest).

import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import { getConfig, saveConfig } from '../../services/api.js';
import { ModelSelect } from '../ModelSelect.js';

const html = htm.bind(h);

export default function GeneralSettings() {
  const [config, setConfig] = useState(null);
  const [model, setModel] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [maxContext, setMaxContext] = useState(10);
  const [batchDelay, setBatchDelay] = useState(3);
  const [splitMessages, setSplitMessages] = useState(true);
  const [splitDelay, setSplitDelay] = useState(2);
  const [transferAlertEnabled, setTransferAlertEnabled] = useState(true);
  const [transferAlertDuration, setTransferAlertDuration] = useState(5);
  const [lowBalanceEnabled, setLowBalanceEnabled] = useState(true);
  const [lowBalanceThreshold, setLowBalanceThreshold] = useState(0.5);

  const [saving, setSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [error, setError] = useState('');
  const [promptFullscreen, setPromptFullscreen] = useState(false);

  function populate(cfg) {
    setModel(cfg.model || '');
    setSystemPrompt(cfg.system_prompt || '');
    setMaxContext(cfg.max_context_messages ?? 10);
    setBatchDelay(cfg.message_batch_delay ?? 3);
    setSplitMessages(cfg.split_messages ?? true);
    setSplitDelay(cfg.split_message_delay ?? 2);
    setTransferAlertEnabled(cfg.transfer_alert_enabled ?? true);
    setTransferAlertDuration(cfg.transfer_alert_duration ?? 5);
    setLowBalanceEnabled(cfg.low_balance_enabled ?? true);
    setLowBalanceThreshold(cfg.low_balance_threshold ?? 0.5);
  }

  async function load() {
    setError('');
    const res = await getConfig();
    if (res && res.ok) {
      setConfig(res.data);
      populate(res.data);
    } else {
      setError((res && res.error) || 'Falha ao carregar as configurações.');
    }
  }

  useEffect(() => { load(); }, []);

  async function handleSave() {
    setSaving(true);
    setSaveSuccess(false);
    setError('');
    const data = {
      model: model.trim() || 'deepseek/deepseek-v4-pro',
      system_prompt: systemPrompt,
      max_context_messages: parseInt(maxContext, 10) || 10,
      message_batch_delay: isNaN(parseFloat(batchDelay)) ? 0 : parseFloat(batchDelay),
      split_messages: splitMessages,
      split_message_delay: isNaN(parseFloat(splitDelay)) ? 0 : parseFloat(splitDelay),
      transfer_alert_enabled: transferAlertEnabled,
      transfer_alert_duration: parseInt(transferAlertDuration, 10) || 5,
      low_balance_enabled: lowBalanceEnabled,
      low_balance_threshold: isNaN(parseFloat(lowBalanceThreshold)) ? 0.5 : parseFloat(lowBalanceThreshold),
    };
    const res = await saveConfig(data);
    setSaving(false);
    if (res && res.ok) {
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
    } else {
      setError((res && res.error) || 'Falha ao salvar as configurações.');
    }
  }

  if (!config) {
    return html`
      <div class="text-[14px] text-wa-secondary">
        ${error || 'Carregando…'}
      </div>
    `;
  }

  return html`
    <div class="flex flex-col gap-4">
      <p class="text-[13px] text-wa-secondary">
        Configurações gerais da IA: modelo, instruções e comportamento das respostas.
      </p>

      ${error ? html`<div class="text-[13px] text-red-500">${error}</div>` : null}

      <!-- Model -->
      <div>
        <label class="block text-[13px] font-medium text-wa-text mb-1">Modelo de IA (chat)</label>
        <${ModelSelect}
          value=${model}
          onChange=${setModel}
          placeholder="deepseek/deepseek-v4-pro"
        />
      </div>

      <!-- Instructions (system prompt) -->
      <div class="flex flex-col">
        <div class="flex items-center justify-between mb-1">
          <label class="block text-[13px] font-medium text-wa-text">Instruções</label>
          <button
            type="button"
            onClick=${() => setPromptFullscreen(true)}
            class="text-wa-secondary hover:text-wa-teal transition-colors p-1 rounded"
            title="Abrir editor em tela cheia"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg>
          </button>
        </div>
        <textarea
          value=${systemPrompt}
          onInput=${(e) => setSystemPrompt(e.target.value)}
          rows="6"
          class="wa-field w-full px-3 py-2 rounded-md text-[14px] resize-y"
        ></textarea>
      </div>

      <!-- Fullscreen Prompt Editor -->
      ${promptFullscreen ? html`
        <div class="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4" onClick=${(e) => { if (e.target === e.currentTarget) setPromptFullscreen(false); }}>
          <div class="bg-wa-bg w-full h-full rounded-xl flex flex-col shadow-2xl overflow-hidden">
            <div class="flex items-center justify-between px-5 py-3 border-b border-wa-border">
              <h2 class="text-sm font-semibold text-wa-text">Instruções</h2>
              <button
                type="button"
                onClick=${() => setPromptFullscreen(false)}
                class="text-wa-secondary hover:text-wa-text transition-colors p-1 rounded"
                title="Fechar"
              >
                <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              </button>
            </div>
            <textarea
              value=${systemPrompt}
              onInput=${(e) => setSystemPrompt(e.target.value)}
              class="flex-1 w-full bg-wa-bg text-wa-text px-5 py-4 text-sm leading-relaxed focus:outline-none resize-none"
              autofocus
            ></textarea>
          </div>
        </div>
      ` : null}

      <!-- Context & Batch -->
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label class="block text-[13px] font-medium text-wa-text mb-1">Mensagens de contexto</label>
          <input
            type="number"
            min="2"
            max="100"
            value=${maxContext}
            onInput=${(e) => setMaxContext(e.target.value)}
            class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
          />
          <span class="text-[12px] text-wa-secondary">Qtd de msgs enviadas ao LLM</span>
        </div>
        <div>
          <label class="block text-[13px] font-medium text-wa-text mb-1">Agrupar mensagens (s)</label>
          <input
            type="number"
            min="0"
            max="30"
            step="0.5"
            value=${batchDelay}
            onInput=${(e) => setBatchDelay(e.target.value)}
            class="wa-field w-full px-3 py-2 rounded-md text-[14px]"
          />
          <span class="text-[12px] text-wa-secondary">Espera antes de responder</span>
        </div>
      </div>

      <!-- Split Messages -->
      <div class="flex flex-col gap-2 p-3 bg-wa-panel rounded-lg border border-wa-border">
        <label class="flex items-center gap-2 text-[14px] font-medium text-wa-text cursor-pointer">
          <input
            type="checkbox"
            checked=${splitMessages}
            onChange=${(e) => setSplitMessages(e.target.checked)}
            class="w-4 h-4 rounded border-wa-border accent-wa-teal"
          />
          Mensagens picadas (dividir resposta)
        </label>
        <span class="text-[12px] text-wa-secondary">Divide a resposta da IA em várias mensagens curtas, como uma conversa natural</span>
        ${splitMessages ? html`
          <div class="mt-1">
            <label class="block text-[12px] font-medium text-wa-text mb-1">Intervalo entre mensagens (s)</label>
            <input
              type="number"
              min="0"
              max="10"
              step="0.5"
              value=${splitDelay}
              onInput=${(e) => setSplitDelay(e.target.value)}
              class="wa-field w-32 px-3 py-1.5 rounded-md text-[14px]"
            />
          </div>
        ` : null}
      </div>

      <!-- Transfer Alert -->
      <div class="flex flex-col gap-2 p-3 bg-wa-panel rounded-lg border border-wa-border">
        <label class="flex items-center gap-2 text-[14px] font-medium text-wa-text cursor-pointer">
          <input
            type="checkbox"
            checked=${transferAlertEnabled}
            onChange=${(e) => setTransferAlertEnabled(e.target.checked)}
            class="w-4 h-4 rounded border-wa-border accent-wa-teal"
          />
          Alerta sonoro ao transferir para humano
        </label>
        <span class="text-[12px] text-wa-secondary">Emite um alerta sonoro quando a IA transfere o atendimento para um humano</span>
        ${transferAlertEnabled ? html`
          <div class="mt-1">
            <label class="block text-[12px] font-medium text-wa-text mb-1">Duração do alerta (segundos)</label>
            <input
              type="number"
              min="1"
              max="30"
              step="1"
              value=${transferAlertDuration}
              onInput=${(e) => setTransferAlertDuration(e.target.value)}
              class="wa-field w-32 px-3 py-1.5 rounded-md text-[14px]"
            />
          </div>
        ` : null}
      </div>

      <!-- Low balance alert -->
      <div class="flex flex-col gap-2 p-3 bg-wa-panel rounded-lg border border-wa-border">
        <label class="flex items-center gap-2 text-[14px] font-medium text-wa-text cursor-pointer">
          <input
            type="checkbox"
            checked=${lowBalanceEnabled}
            onChange=${(e) => setLowBalanceEnabled(e.target.checked)}
            class="w-4 h-4 rounded border-wa-border accent-wa-teal"
          />
          Avisar quando o saldo estiver acabando
        </label>
        <span class="text-[12px] text-wa-secondary">Exibe um pop-up no painel com link de recarga quando o saldo cair abaixo do limite</span>
        ${lowBalanceEnabled ? html`
          <div class="mt-1">
            <label class="block text-[12px] font-medium text-wa-text mb-1">Limite (USD)</label>
            <input
              type="number"
              min="0"
              max="100"
              step="0.01"
              value=${lowBalanceThreshold}
              onInput=${(e) => setLowBalanceThreshold(e.target.value)}
              class="wa-field w-32 px-3 py-1.5 rounded-md text-[14px]"
            />
            <span class="text-[12px] text-wa-secondary block mt-1">Padrão: 0.50 (50 centavos de dólar)</span>
          </div>
        ` : null}
      </div>

      <!-- Save -->
      <div class="flex justify-end">
        <button
          onClick=${handleSave}
          disabled=${saving}
          class="px-5 py-2.5 ${saveSuccess ? 'bg-green-600' : 'bg-wa-teal hover:opacity-90'} disabled:opacity-50 text-white font-medium rounded-lg transition-colors"
        >
          ${saving ? 'Salvando…' : saveSuccess ? '✓ Salvo!' : 'Salvar'}
        </button>
      </div>
    </div>
  `;
}
