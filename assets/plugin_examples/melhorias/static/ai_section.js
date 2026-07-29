// Seção "Sugestão de melhoria" renderizada no slot ai.settings.sections da aba
// "Configurações de IA". Recebe `api` (via extends.js) → usa api.http/api.services.
// Config editável só com permissão `configure`. Desde a 1.3.0 o motor é SEMPRE o
// agêntico (executor Claude Code externo) — a análise one-shot "direta" saiu da
// UI (o backend legado segue selecionável via config, p/ testes).
// As análises geradas são consultadas no painel de aprovação (botão acima).
//
// Só substitui a antiga área de "sugestão de melhoria" que vivia no core: como é
// um slot, some por completo quando o plugin é desativado.

import { h } from 'preact';
import { useState, useEffect, useCallback } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

function goToPanel(detailId) {
  const url = detailId != null ? `/melhorias?detail=${detailId}` : '/melhorias';
  history.pushState(null, '', url);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

export function MelhoriaAiSection({ api, can }) {
  const canConfigure = can('configure');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');
  // plano 51: servidor agêntico (executor Claude Code externo).
  const [aiUrl, setAiUrl] = useState('');
  const [aiSecret, setAiSecret] = useState('');
  const [cbUrl, setCbUrl] = useState('');
  const [cbUrlEffective, setCbUrlEffective] = useState('');
  const [testResult, setTestResult] = useState('');

  const load = useCallback(async () => {
    try {
      const cfg = await api.http.get('/config');
      if (cfg && cfg.ok && cfg.data) {
        setAiUrl(cfg.data.ai_server_url || '');
        setAiSecret(cfg.data.ai_server_secret || '');
        setCbUrl(cfg.data.callback_url || '');
        setCbUrlEffective(cfg.data.callback_url_effective || '');
      }
    } catch (_) { /* ignore */ }
  }, [api]);

  useEffect(() => { load(); }, [load]);

  async function save() {
    if (saving || !canConfigure) return;
    setSaving(true); setError(''); setSaved(false);
    try {
      const res = await api.http.put('/config', {
        // Motor único: salvar reafirma o backend agêntico (cura instalações
        // que ainda tenham "direct" salvo de versões antigas).
        generator_backend: 'external',
        ai_server_url: aiUrl || '',
        ai_server_secret: aiSecret || '',
        callback_url: cbUrl || '',
      });
      if (res && res.ok) {
        setSaved(true); setTimeout(() => setSaved(false), 2000);
        if (res.data) setAiSecret(res.data.ai_server_secret || '');
      } else { setError((res && res.error) || 'Falha ao salvar.'); }
    } catch (_) { setError('Erro de conexão.'); }
    setSaving(false);
  }

  // O teste prova DUAS coisas (plano 60 · 2.8): o segredo bate E a sessão do
  // Claude está viva. Antes dizia só "✓ Secret válido" — com o Claude deslogado,
  // meia verdade. Sem `GET /health` no executor (camada 3), o veredito da sessão
  // é o que o gateway sabe; "desconhecido" quando ninguém sabe.
  const SESSION_LABEL = { ok: 'sessão do Claude ativa', expired: 'SESSÃO DO CLAUDE EXPIRADA',
                          unknown: 'sessão desconhecida' };

  async function testConnection() {
    setTestResult('…');
    try {
      const res = await api.http.post('/admin/test-connection', {});
      const data = (res && res.data) || {};
      if (res && res.ok && (data.secret_ok || data.ok)) {
        const st = (data.session && data.session.status) || 'unknown';
        const label = SESSION_LABEL[st] || SESSION_LABEL.unknown;
        setTestResult(`${st === 'expired' ? '✕' : '✓'} Secret válido · ${label}`);
      } else {
        setTestResult(`✕ ${(res && (res.error || (data.status_code && `HTTP ${data.status_code}`))) || 'falhou'}`);
      }
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
        Aprovar uma sugestão (botão-direito numa resposta da IA → "Gerar melhoria")
        abre um <strong>chat com a IA</strong> que analisa o atendimento e propõe mudanças em
        agentes, prompts, tools e variáveis — cada mudança exige sua aprovação (✓/✕) e é
        versionada (dá para reverter).
      </p>

      <div class="border border-wa-border rounded-lg p-3 mb-4">
        <h4 class="text-[13px] font-semibold text-wa-text mb-2">Executor agêntico</h4>
        <label class="block text-[13px] text-wa-text mb-1">URL do executor</label>
        <input class="wa-field w-full rounded p-[8px] text-[13px] mb-3" type="text"
          placeholder="http://203.0.113.10:8015" disabled=${!canConfigure}
          value=${aiUrl} onInput=${(e) => setAiUrl(e.target.value)} />
        <label class="block text-[13px] text-wa-text mb-1">Secret compartilhado (≥32 caracteres)</label>
        <input class="wa-field w-full rounded p-[8px] text-[13px] mb-1" type="password"
          placeholder="cole o mesmo valor do .env do executor" disabled=${!canConfigure}
          value=${aiSecret} onInput=${(e) => setAiSecret(e.target.value)} />
        <div class="text-[11px] text-wa-secondary mb-3">Deixar como “***” mantém o secret atual.</div>
        <label class="block text-[13px] text-wa-text mb-1">URL de callback (este WhatsBot, opcional)</label>
        <input class="wa-field w-full rounded p-[8px] text-[13px] mb-1" type="text"
          placeholder=${cbUrlEffective ? `(auto: ${cbUrlEffective})` : 'ex.: http://203.0.113.20:8090'}
          disabled=${!canConfigure}
          value=${cbUrl} onInput=${(e) => setCbUrl(e.target.value)} />
        <div class="text-[11px] text-wa-secondary mb-3">
          URL desta instância que o executor chama de volta. Vazio = usa a URL do painel
          (public_base_url) automaticamente. Precisa ser alcançável a partir do servidor da IA.
        </div>
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
          Você não tem permissão para editar a configuração.
        </div>`}
    </div>`;
}

export default MelhoriaAiSection;
