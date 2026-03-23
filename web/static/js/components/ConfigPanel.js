import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import htm from 'htm';
import { testApiKey } from '../services/api.js';

const html = htm.bind(h);

export function ConfigPanel({ config, saving, onSave, onNotify }) {
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [autoReply, setAutoReply] = useState(true);
  const [replyAll, setReplyAll] = useState(true);
  const [onlyContacts, setOnlyContacts] = useState(false);
  const [maxContext, setMaxContext] = useState(10);
  const [batchDelay, setBatchDelay] = useState(3);
  const [testing, setTesting] = useState(false);

  const [saveSuccess, setSaveSuccess] = useState(false);

  // Populate form when config loads
  useEffect(() => {
    if (config) {
      setApiKey(''); // Don't show masked key in input
      setModel(config.model || '');
      setSystemPrompt(config.system_prompt || '');
      setAutoReply(config.auto_reply ?? true);
      setReplyAll(config.reply_to_all ?? true);
      setOnlyContacts(config.only_saved_contacts ?? false);
      setMaxContext(config.max_context_messages ?? 10);
      setBatchDelay(config.message_batch_delay ?? 3);
    }
  }, [config]);

  const [testResult, setTestResult] = useState(null); // {ok, message}

  async function handleTestKey() {
    const key = apiKey.trim();
    if (!key) {
      onNotify('Insira uma API key primeiro.');
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const res = await testApiKey(key);
      if (res.ok) {
        setTestResult({ ok: res.data.valid, message: res.data.message });
        onNotify(res.data.message);
        // Auto-save when key is valid
        if (res.data.valid) {
          await onSave({ openrouter_api_key: key });
        }
      } else {
        setTestResult({ ok: false, message: res.error || 'Erro ao testar.' });
        onNotify(res.error || 'Erro ao testar.');
      }
    } catch {
      setTestResult({ ok: false, message: 'Erro de conexão.' });
      onNotify('Erro de conexão.');
    }
    setTesting(false);
  }

  async function handleSave() {
    const data = {
      model: model.trim() || 'openai/gpt-4o-mini',
      system_prompt: systemPrompt,
      auto_reply: autoReply,
      reply_to_all: replyAll,
      only_saved_contacts: onlyContacts,
      max_context_messages: parseInt(maxContext, 10) || 10,
      message_batch_delay: parseFloat(batchDelay) || 3,
    };
    // Only include api_key if user typed a new one
    if (apiKey.trim()) {
      data.openrouter_api_key = apiKey.trim();
    }
    setSaveSuccess(false);
    const result = await onSave(data);
    if (result !== false) {
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
    }
  }

  if (!config) {
    return html`<div class="bg-white rounded-xl p-5 animate-pulse-slow text-wa-secondary border border-wa-border">Carregando...</div>`;
  }

  return html`
    <div class="bg-white rounded-xl p-5 flex flex-col gap-4 flex-1 border border-wa-border shadow-sm">
      <!-- API Key -->
      <div>
        <label class="block text-sm font-semibold text-wa-text mb-1">API Key OpenRouter</label>
        <div class="flex gap-2">
          <input
            type="password"
            value=${apiKey}
            onInput=${(e) => setApiKey(e.target.value)}
            placeholder=${config.openrouter_api_key || 'sk-or-...'}
            class="flex-1 bg-wa-panel text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none"
          />
          <button
            onClick=${handleTestKey}
            disabled=${testing}
            class="px-4 py-2 bg-wa-panel hover:bg-wa-hover disabled:opacity-50 text-wa-text text-sm rounded-lg transition-colors whitespace-nowrap border border-wa-border"
          >
            ${testing ? '...' : 'Testar'}
          </button>
        </div>
        ${testResult ? html`
          <p class="text-xs mt-1 ${testResult.ok ? 'text-green-600' : 'text-red-500'}">
            ${testResult.ok ? '\u2713' : '\u2717'} ${testResult.message}
          </p>
        ` : config.openrouter_api_key ? html`
          <p class="text-xs mt-1 text-wa-secondary">Chave salva: ${config.openrouter_api_key}</p>
        ` : null}
      </div>

      <!-- Model -->
      <div>
        <label class="block text-sm font-semibold text-wa-text mb-1">Modelo LLM</label>
        <input
          type="text"
          value=${model}
          onInput=${(e) => setModel(e.target.value)}
          placeholder="openai/gpt-4o-mini"
          class="w-full bg-wa-panel text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none"
        />
      </div>

      <!-- System Prompt -->
      <div class="flex-1 flex flex-col">
        <label class="block text-sm font-semibold text-wa-text mb-1">System Prompt</label>
        <textarea
          value=${systemPrompt}
          onInput=${(e) => setSystemPrompt(e.target.value)}
          rows="4"
          class="w-full flex-1 bg-wa-panel text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none resize-none"
        ></textarea>
      </div>

      <!-- Context & Batch Settings -->
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-sm font-semibold text-wa-text mb-1">Mensagens de contexto</label>
          <input
            type="number"
            min="2"
            max="100"
            value=${maxContext}
            onInput=${(e) => setMaxContext(e.target.value)}
            class="w-full bg-wa-panel text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none"
          />
          <span class="text-xs text-wa-secondary">Qtd de msgs enviadas ao LLM</span>
        </div>
        <div>
          <label class="block text-sm font-semibold text-wa-text mb-1">Agrupar mensagens (s)</label>
          <input
            type="number"
            min="0"
            max="30"
            step="0.5"
            value=${batchDelay}
            onInput=${(e) => setBatchDelay(e.target.value)}
            class="w-full bg-wa-panel text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none"
          />
          <span class="text-xs text-wa-secondary">Espera antes de responder</span>
        </div>
      </div>

      <!-- Checkboxes -->
      <div class="flex flex-col gap-2">
        <label class="flex items-center gap-2 text-sm text-wa-text cursor-pointer">
          <input
            type="checkbox"
            checked=${autoReply}
            onChange=${(e) => setAutoReply(e.target.checked)}
            class="w-4 h-4 rounded border-wa-border accent-wa-teal"
          />
          Auto-resposta ativa
        </label>
        <label class="flex items-center gap-2 text-sm text-wa-text cursor-pointer">
          <input
            type="checkbox"
            checked=${replyAll}
            onChange=${(e) => setReplyAll(e.target.checked)}
            class="w-4 h-4 rounded border-wa-border accent-wa-teal"
          />
          Responder a todos
        </label>
        <label class="flex items-center gap-2 text-sm text-wa-text cursor-pointer">
          <input
            type="checkbox"
            checked=${onlyContacts}
            onChange=${(e) => setOnlyContacts(e.target.checked)}
            class="w-4 h-4 rounded border-wa-border accent-wa-teal"
          />
          Apenas contatos salvos
        </label>
      </div>

      <!-- Save Button -->
      <button
        onClick=${handleSave}
        disabled=${saving}
        class="w-full py-2.5 ${saveSuccess ? 'bg-green-600' : 'bg-wa-teal hover:bg-wa-tealDark'} disabled:opacity-50 text-white font-medium rounded-lg transition-colors"
      >
        ${saving ? 'Salvando...' : saveSuccess ? '\u2713 Salvo!' : 'Salvar Configurações'}
      </button>
    </div>
  `;
}
