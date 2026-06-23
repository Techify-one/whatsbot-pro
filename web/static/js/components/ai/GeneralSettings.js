// AI Engine — "Configurações" tab. Holds the GLOBAL AI settings ONLY:
// o interruptor GLOBAL de ligar/desligar a IA (checado ANTES de tudo), a chave de
// API e o aviso de saldo. TODO o resto (automação por contato, resposta em grupos,
// transcrição, contexto, agrupamento, mensagens picadas, alerta de transferência)
// é configurado POR CANAL na tela "Canais" (plano 21). O PROMPT e o MODELO são
// definidos por agente na aba "Agentes".
// Self-contained: loads via getConfig() and persists via saveConfig() (partial
// PUT — only sends the keys it owns, so the Painel keeps owning the rest).

import { h } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import htm from 'htm';
import { getConfig, saveConfig, testApiKey } from '../../services/api.js';

const html = htm.bind(h);

export default function GeneralSettings() {
  const [config, setConfig] = useState(null);
  // Interruptor GLOBAL da IA (checado antes do toggle por canal e por conversa).
  const [autoReply, setAutoReply] = useState(true);
  // API
  const [apiKey, setApiKey] = useState('');
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  // Saldo
  const [lowBalanceEnabled, setLowBalanceEnabled] = useState(true);
  const [lowBalanceThreshold, setLowBalanceThreshold] = useState(0.5);

  const [saving, setSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [error, setError] = useState('');
  // Modal de confirmação ao DESLIGAR o interruptor global.
  const [confirmOff, setConfirmOff] = useState(false);

  function populate(cfg) {
    setAutoReply(cfg.auto_reply ?? true);
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

  async function handleTestKey() {
    const key = apiKey.trim();
    if (!key) { setTestResult({ ok: false, message: 'Insira uma API key primeiro.' }); return; }
    setTesting(true);
    setTestResult(null);
    try {
      const res = await testApiKey(key);
      if (res.ok) {
        setTestResult({ ok: res.data.valid, message: res.data.message });
        if (res.data.valid) {
          // Auto-save the key and refresh the masked display.
          await saveConfig({ openrouter_api_key: key });
          setApiKey('');
          load();
        }
      } else {
        setTestResult({ ok: false, message: res.error || 'Erro ao testar.' });
      }
    } catch {
      setTestResult({ ok: false, message: 'Erro de conexão.' });
    }
    setTesting(false);
  }

  async function handleSave() {
    setSaving(true);
    setSaveSuccess(false);
    setError('');
    const data = {
      auto_reply: autoReply,
      low_balance_enabled: lowBalanceEnabled,
      low_balance_threshold: isNaN(parseFloat(lowBalanceThreshold)) ? 0.5 : parseFloat(lowBalanceThreshold),
    };
    // Only include the API key when the user typed a new one.
    if (apiKey.trim()) data.openrouter_api_key = apiKey.trim();
    const res = await saveConfig(data);
    setSaving(false);
    if (res && res.ok) {
      setSaveSuccess(true);
      setApiKey('');
      setTimeout(() => setSaveSuccess(false), 3000);
    } else {
      setError((res && res.error) || 'Falha ao salvar as configurações.');
    }
  }

  // Toggle do interruptor global. Ao DESLIGAR, abre o modal de confirmação;
  // ao LIGAR, aplica direto.
  function handleToggleAI() {
    if (autoReply) {
      setConfirmOff(true);
    } else {
      setAutoReply(true);
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
        Configurações globais da IA: o interruptor geral, a chave de API e o aviso de saldo.
        O comportamento de cada número (responder, grupos, transcrição, contexto, mensagens
        picadas, alerta de transferência) é configurado <span class="font-medium">por canal</span>
        na tela <span class="font-medium">Canais</span>. O prompt e o modelo são definidos por
        agente na aba <span class="font-medium">Agentes</span>.
      </p>

      ${error ? html`<div class="text-[13px] text-red-500">${error}</div>` : null}

      <!-- Interruptor GLOBAL da IA -->
      <div class="flex flex-col gap-2 p-3 bg-wa-panel rounded-lg border border-wa-border">
        <div class="flex items-center justify-between gap-3 p-3 rounded-lg border ${autoReply ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}">
          <span class="text-sm font-semibold text-wa-text">Ativar a IA (interruptor global)</span>
          <button
            type="button"
            role="switch"
            aria-checked=${autoReply}
            onClick=${handleToggleAI}
            class="relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-wa-teal focus:ring-offset-1 ${autoReply ? 'bg-wa-teal' : 'bg-wa-border'}"
          >
            <span class="inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${autoReply ? 'translate-x-5' : 'translate-x-0.5'}"></span>
          </button>
        </div>
        <span class="text-[12px] text-wa-secondary">
          Liga/desliga a IA em TODO o sistema. É verificado primeiro: com isto desligado,
          nenhum canal responde, mesmo que a IA esteja ativada no canal. Ligado, cada canal
          decide se responde pela sua própria configuração.
        </span>
      </div>

      <!-- API -->
      <div class="flex flex-col gap-3 p-3 bg-wa-panel rounded-lg border border-wa-border">
        <div class="text-[14px] font-semibold text-wa-text">Chave de API</div>
        <div>
          <label class="block text-sm font-semibold text-wa-text mb-1">Chave de API Techify</label>
          <div class="flex gap-2">
            <input
              type="password"
              value=${apiKey}
              onInput=${(e) => setApiKey(e.target.value)}
              placeholder=${config.openrouter_api_key || 'sk-or-...'}
              class="flex-1 bg-wa-bg text-wa-text px-3 py-2 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none"
            />
            <button
              onClick=${handleTestKey}
              disabled=${testing}
              class="px-4 py-2 bg-wa-bg hover:bg-wa-hover disabled:opacity-50 text-wa-text text-sm rounded-lg transition-colors whitespace-nowrap border border-wa-border"
            >
              ${testing ? '...' : 'Testar'}
            </button>
          </div>
          ${testResult ? html`
            <p class="text-xs mt-1 ${testResult.ok ? 'text-green-600' : 'text-red-500'}">
              ${testResult.ok ? '✓' : '✗'} ${testResult.message}
            </p>
          ` : config.openrouter_api_key ? html`
            <p class="text-xs mt-1 text-wa-secondary">Chave salva: ${config.openrouter_api_key}</p>
          ` : null}
        </div>
      </div>

      <!-- Aviso de saldo -->
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

      <!-- Modal de confirmação ao desligar a IA -->
      ${confirmOff ? html`
        <div
          class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick=${() => setConfirmOff(false)}
        >
          <div
            class="w-full max-w-sm bg-wa-panel rounded-lg border border-wa-border shadow-xl p-5 flex flex-col gap-3"
            onClick=${(e) => e.stopPropagation()}
          >
            <h3 class="text-[15px] font-semibold text-wa-text">Desligar a IA?</h3>
            <p class="text-[13px] text-wa-secondary">
              Tem certeza que quer desligar a IA? Com o interruptor global desligado,
              nenhum canal responde, mesmo que a IA esteja ativada no canal.
            </p>
            <div class="flex justify-end gap-2 mt-1">
              <button
                onClick=${() => setConfirmOff(false)}
                class="px-4 py-2 bg-wa-bg hover:bg-wa-hover text-wa-text text-sm rounded-lg transition-colors border border-wa-border"
              >
                Cancelar
              </button>
              <button
                onClick=${() => { setAutoReply(false); setConfirmOff(false); }}
                class="px-4 py-2 bg-red-600 hover:opacity-90 text-white text-sm font-medium rounded-lg transition-colors"
              >
                Desligar
              </button>
            </div>
          </div>
        </div>
      ` : null}
    </div>
  `;
}
