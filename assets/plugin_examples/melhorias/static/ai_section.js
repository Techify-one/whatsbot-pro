// Seção "Sugestão de melhoria" renderizada no slot ai.settings.sections da aba
// "Configurações de IA". Recebe `api` (via extends.js) → usa api.http/api.services.
// Config (modelo + prompt da análise) editável só com permissão `configure`; lista
// das últimas análises geradas (aprovadas), cada uma linkando ao painel.
//
// Só substitui a antiga área de "sugestão de melhoria" que vivia no core: como é
// um slot, some por completo quando o plugin é desativado.

import { h } from 'preact';
import { useState, useEffect, useCallback } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

function fmtTs(ts) {
  if (!ts) return '—';
  try { return new Date(ts * 1000).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' }); }
  catch (_) { return '—'; }
}

function goToPanel(detailId) {
  const url = detailId != null ? `/melhorias?detail=${detailId}` : '/melhorias';
  history.pushState(null, '', url);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

export function MelhoriaAiSection({ api, can }) {
  const canConfigure = can('configure');
  const [model, setModel] = useState('');
  const [prompt, setPrompt] = useState('');
  const [promptDefault, setPromptDefault] = useState('');
  const [models, setModels] = useState([]);
  const [recent, setRecent] = useState([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');
  // plano 51: servidor agêntico (executor Claude Code externo).
  const [backend, setBackend] = useState('direct');
  const [aiUrl, setAiUrl] = useState('');
  const [aiSecret, setAiSecret] = useState('');
  const [aiModel, setAiModel] = useState('');
  const [testResult, setTestResult] = useState('');

  const load = useCallback(async () => {
    try {
      const cfg = await api.http.get('/config');
      if (cfg && cfg.ok && cfg.data) {
        setModel(cfg.data.model || '');
        setPrompt(cfg.data.prompt || '');
        setPromptDefault(cfg.data.prompt_default || '');
        setBackend(cfg.data.generator_backend || 'direct');
        setAiUrl(cfg.data.ai_server_url || '');
        setAiSecret(cfg.data.ai_server_secret || '');
        setAiModel(cfg.data.ai_model || '');
      }
    } catch (_) { /* ignore */ }
    try {
      const ms = api.services && api.services.getModels ? await api.services.getModels() : null;
      if (ms && ms.ok && Array.isArray(ms.data)) setModels(ms.data);
    } catch (_) { /* ignore */ }
    try {
      const r = await api.http.get('/suggestions?status=aprovada&limit=20');
      if (r && r.ok && Array.isArray(r.data)) setRecent(r.data);
    } catch (_) { /* ignore */ }
  }, [api]);

  useEffect(() => { load(); }, [load]);

  async function save() {
    if (saving || !canConfigure) return;
    setSaving(true); setError(''); setSaved(false);
    try {
      const res = await api.http.put('/config', {
        model: model || '', prompt: prompt || '',
        generator_backend: backend || 'direct',
        ai_server_url: aiUrl || '',
        ai_server_secret: aiSecret || '',
        ai_model: aiModel || '',
      });
      if (res && res.ok) {
        setSaved(true); setTimeout(() => setSaved(false), 2000);
        if (res.data) setAiSecret(res.data.ai_server_secret || '');
      } else { setError((res && res.error) || 'Falha ao salvar.'); }
    } catch (_) { setError('Erro de conexão.'); }
    setSaving(false);
  }

  async function testConnection() {
    setTestResult('…');
    try {
      const res = await api.http.post('/admin/test-connection', {});
      if (res && res.ok && res.data && res.data.ok) setTestResult('✓ Secret válido (auth-check 200)');
      else setTestResult(`✕ ${(res && (res.error || (res.data && `HTTP ${res.data.status_code}`))) || 'falhou'}`);
    } catch (_) { setTestResult('✕ Executor inalcançável'); }
  }

  return html`
    <div class="mt-6 pt-6 border-t border-wa-border">
      <div class="flex items-center justify-between mb-3">
        <h3 class="text-[15px] font-semibold text-wa-text">Sugestão de melhoria</h3>
        <button onClick=${() => goToPanel()}
          class="text-[13px] px-3 py-1.5 rounded-md border border-wa-border text-wa-text hover:bg-wa-hover transition-colors">
          Abrir painel de aprovação
        </button>
      </div>
      <p class="text-[12px] text-wa-secondary mb-4">
        A análise é gerada por uma chamada direta ao modelo abaixo, quando um pedido é
        <strong>aprovado</strong> no painel (botão-direito numa resposta da IA → "Gerar melhoria").
      </p>

      <label class="block text-[13px] text-wa-text mb-1">Modelo da análise</label>
      <select class="wa-field w-full rounded p-[8px] text-[13px] mb-4" value=${model}
        disabled=${!canConfigure} onChange=${(e) => setModel(e.target.value)}>
        <option value="">— Usar o mesmo modelo do agente —</option>
        ${models.map((m) => html`<option key=${m.id} value=${m.id}>${m.name || m.id}</option>`)}
      </select>

      <div class="flex items-center justify-between mb-1">
        <label class="block text-[13px] text-wa-text">Prompt da análise</label>
        ${canConfigure ? html`<button onClick=${() => setPrompt(promptDefault)}
          class="text-[12px] text-wa-teal hover:underline">Restaurar padrão</button>` : ''}
      </div>
      <textarea class="wa-field w-full rounded p-[8px] text-[13px] mb-2" rows="6"
        disabled=${!canConfigure}
        placeholder=${promptDefault}
        value=${prompt} onInput=${(e) => setPrompt(e.target.value)}></textarea>

      <div class="border border-wa-border rounded-lg p-3 mb-4">
        <h4 class="text-[13px] font-semibold text-wa-text mb-2">Melhoria agêntica (plano 51)</h4>
        <p class="text-[12px] text-wa-secondary mb-3">
          Com o motor <strong>Agêntico (executor externo)</strong>, aprovar uma sugestão abre um
          <strong>chat com a IA</strong> que analisa o atendimento e propõe mudanças em agentes,
          prompts, tools e variáveis — cada mudança exige sua aprovação (✓/✕) e é versionada
          (dá para reverter).
        </p>
        <label class="block text-[13px] text-wa-text mb-1">Motor da análise</label>
        <select class="wa-field w-full rounded p-[8px] text-[13px] mb-3" value=${backend}
          disabled=${!canConfigure} onChange=${(e) => setBackend(e.target.value)}>
          <option value="direct">Direto (análise one-shot — padrão)</option>
          <option value="external">Agêntico (chat com executor Claude Code externo)</option>
          <option value="stub">Stub (testes)</option>
        </select>
        <label class="block text-[13px] text-wa-text mb-1">URL do executor</label>
        <input class="wa-field w-full rounded p-[8px] text-[13px] mb-3" type="text"
          placeholder="http://203.0.113.10:8015" disabled=${!canConfigure}
          value=${aiUrl} onInput=${(e) => setAiUrl(e.target.value)} />
        <label class="block text-[13px] text-wa-text mb-1">Secret compartilhado (≥32 caracteres)</label>
        <input class="wa-field w-full rounded p-[8px] text-[13px] mb-1" type="password"
          placeholder="cole o mesmo valor do .env do executor" disabled=${!canConfigure}
          value=${aiSecret} onInput=${(e) => setAiSecret(e.target.value)} />
        <div class="text-[11px] text-wa-secondary mb-3">Deixar como “***” mantém o secret atual.</div>
        <label class="block text-[13px] text-wa-text mb-1">Modelo do executor (opcional)</label>
        <input class="wa-field w-full rounded p-[8px] text-[13px] mb-3" type="text"
          placeholder="(default do executor)" disabled=${!canConfigure}
          value=${aiModel} onInput=${(e) => setAiModel(e.target.value)} />
        ${canConfigure ? html`
          <div class="flex items-center gap-3">
            <button onClick=${testConnection}
              class="px-3 py-1.5 rounded-md border border-wa-border text-wa-text text-[12px] hover:bg-wa-hover">Testar conexão</button>
            ${testResult ? html`<span class="text-[12px] ${testResult.startsWith('✓') ? 'text-wa-teal' : 'text-red-500'}">${testResult}</span>` : ''}
          </div>` : ''}
      </div>

      ${error ? html`<div class="text-[12px] text-red-500 mb-2">${error}</div>` : ''}
      ${canConfigure ? html`
        <div class="flex items-center gap-3 mb-6">
          <button onClick=${save} disabled=${saving}
            class="px-[16px] py-[8px] rounded-full bg-wa-teal text-white text-[14px] font-medium hover:opacity-90 transition-opacity disabled:opacity-50">
            ${saving ? 'Salvando…' : 'Salvar'}</button>
          ${saved ? html`<span class="text-[13px] text-wa-teal">✓ Salvo</span>` : ''}
        </div>` : html`<div class="text-[12px] text-wa-secondary mb-6">
          Você não tem permissão para editar o modelo/prompt.
        </div>`}

      <h4 class="text-[13px] font-semibold text-wa-text mb-2">Análises geradas recentes</h4>
      ${recent.length === 0
        ? html`<div class="text-[12px] text-wa-secondary">Nenhuma análise aprovada ainda.</div>`
        : html`<ul class="space-y-1">
            ${recent.map((s) => html`<li key=${s.id}>
              <button onClick=${() => goToPanel(s.id)}
                class="w-full text-left text-[13px] text-wa-text hover:bg-wa-hover rounded px-2 py-1.5 flex items-center justify-between gap-2">
                <span class="truncate">${(s.message_content || '').trim() || '(sem conteúdo)'}</span>
                <span class="text-[11px] text-wa-secondary whitespace-nowrap">${fmtTs(s.decided_at)}</span>
              </button>
            </li>`)}
          </ul>`}
    </div>`;
}

export default MelhoriaAiSection;
