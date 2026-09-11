// Plugins management screen — list cards, toggle, settings drawer,
// import (zip upload), export and delete. The Toggle/Delete actions
// trigger a server-side restart; we show a brief "reiniciando…" overlay.

import { h } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import htm from 'htm';
import { PluginSettingsForm } from './PluginSettingsForm.js';
import { PluginScreen } from './PluginScreen.js';
import { authHeaders, handleUnauthorized } from '../services/api.js';
import { useDeepLink } from '../hooks/useDeepLink.js';

const html = htm.bind(h);

// Largura do modal "Configurar". O core é dono do modal, então uma screen de
// configuração não consegue passar da largura dele por dentro: o plugin DECLARA
// um tamanho em `screens[].width` e aqui ele é traduzido para uma classe.
// Valor desconhecido cai no default — nunca interpolar a string do manifest numa
// classe (um plugin passaria CSS arbitrário para dentro do painel).
const CONFIG_MODAL_WIDTHS = {
  normal: 'max-w-2xl',
  wide: 'max-w-6xl',
  full: 'max-w-[95vw]',
};

function configModalWidth(cfgScreen) {
  if (!cfgScreen) return 'max-w-lg';   // form declarativo (Pydantic), sempre estreito
  return CONFIG_MODAL_WIDTHS[cfgScreen.width] || CONFIG_MODAL_WIDTHS.normal;
}


function StatusBadge({ plugin }) {
  if (plugin.error) {
    return html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-red-100 text-red-700">Manifest inválido</span>`;
  }
  if (plugin.load_error) {
    return html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-red-100 text-red-700" title=${plugin.load_error}>Erro ao carregar</span>`;
  }
  if (plugin.enabled && plugin.loaded) {
    return html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-green-100 text-green-700">Ativo</span>`;
  }
  if (plugin.enabled && !plugin.loaded) {
    return html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-yellow-100 text-yellow-800">Ativado (aguardando restart)</span>`;
  }
  return html`<span class="px-2 py-0.5 rounded-full text-[11px] bg-wa-panel text-wa-secondary">Desativado</span>`;
}


function RestartBanner() {
  return html`
    <div class="fixed inset-0 bg-black/40 z-[60] flex items-center justify-center">
      <div class="bg-wa-bg rounded-lg shadow-xl p-6 max-w-sm">
        <div class="flex items-center gap-3">
          <div class="w-6 h-6 border-2 border-wa-teal border-t-transparent rounded-full animate-spin"></div>
          <div>
            <div class="font-medium">Reiniciando o servidor…</div>
            <div class="text-[12px] text-wa-secondary mt-1">A página será recarregada em alguns segundos.</div>
          </div>
        </div>
      </div>
    </div>
  `;
}


export function PluginsManager({ onPluginsChanged, initialEntity }) {
  const [plugins, setPlugins] = useState([]);
  const [apiVersion, setApiVersion] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(null); // plugin id
  const [descOpen, setDescOpen] = useState(null); // plugin id do popup "descrição completa"
  const [importing, setImporting] = useState(false);
  const [restarting, setRestarting] = useState(false);
  const [exporting, setExporting] = useState({}); // { [pluginId]: pct (0-100) }
  const [updating, setUpdating] = useState(null); // pid sendo atualizado
  const fileRef = useRef(null);
  const updateFileRef = useRef(null);
  const pendingUpdatePid = useRef(null); // pid alvo do <input> de atualização

  // Deep-link /plugins/<id>: a URL reflete o plugin com o modal Configurar aberto.
  const pushUrl = useDeepLink({
    tab: 'plugins',
    resolve: initialEntity ? { id: initialEntity.id } : null,
    ready: !loading,
    open: (sel) => {
      if (!sel) { setSettingsOpen(null); return; }
      if (plugins.find(p => p.id === sel.id)) setSettingsOpen(sel.id);
    },
  });

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch('/api/plugins', { headers: authHeaders() });
      if (r.status === 401) { handleUnauthorized(); throw new Error('Não autenticado.'); }
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'failed');
      setPlugins(data.data.plugins || []);
      setApiVersion(data.data.whatsbot_api_version || '');
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  // After we trigger a restart, poll /health until it responds again, then reload.
  useEffect(() => {
    if (!restarting) return;
    let cancelled = false;
    const startedAt = Date.now();
    async function poll() {
      while (!cancelled && Date.now() - startedAt < 60_000) {
        try {
          await new Promise(r => setTimeout(r, 1500));
          const r = await fetch('/health', { cache: 'no-store' });
          if (r.ok) { window.location.reload(); return; }
        } catch (_) { /* still down */ }
      }
      if (!cancelled) setRestarting(false);
    }
    poll();
    return () => { cancelled = true; };
  }, [restarting]);

  async function toggle(pid, enable) {
    const action = enable ? 'enable' : 'disable';
    try {
      const r = await fetch(`/api/plugins/${pid}/${action}`, { method: 'POST', headers: authHeaders() });
      if (r.status === 401) { handleUnauthorized(); throw new Error('Não autenticado.'); }
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'falha');
      setRestarting(true);
    } catch (e) {
      alert(`Erro ao ${enable ? 'ativar' : 'desativar'}: ${e.message || e}`);
    }
  }

  async function deletePlugin(pid) {
    if (!confirm(`Remover plugin '${pid}'? A pasta e as tabelas dele serão apagadas.`)) return;
    try {
      const r = await fetch(`/api/plugins/${pid}`, { method: 'DELETE', headers: authHeaders() });
      if (r.status === 401) { handleUnauthorized(); throw new Error('Não autenticado.'); }
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'falha');
      setRestarting(true);
    } catch (e) {
      alert(`Erro ao deletar: ${e.message || e}`);
    }
  }

  function clearExporting(pid) {
    setExporting(s => { const n = { ...s }; delete n[pid]; return n; });
  }

  async function exportPlugin(pid) {
    if (exporting[pid] != null) return; // already running
    setExporting(s => ({ ...s, [pid]: 0 }));
    try {
      const r = await fetch(`/api/plugins/${pid}/export`, { headers: authHeaders() });
      if (r.status === 401) { handleUnauthorized(); throw new Error('Não autenticado.'); }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);

      // Stream the body so we can report real download progress. Falls back to
      // a plain blob if the browser can't expose the stream or size.
      const total = Number(r.headers.get('content-length')) || 0;
      let blob;
      if (r.body && total > 0) {
        const reader = r.body.getReader();
        const chunks = [];
        let received = 0;
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          chunks.push(value);
          received += value.length;
          setExporting(s => ({ ...s, [pid]: Math.min(99, Math.round((received / total) * 100)) }));
        }
        blob = new Blob(chunks, { type: 'application/zip' });
      } else {
        blob = await r.blob();
      }
      setExporting(s => ({ ...s, [pid]: 100 }));

      const cd = r.headers.get('content-disposition') || '';
      const m = cd.match(/filename="?([^";]+)"?/i);
      const filename = (m && m[1]) || `${pid}.zip`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);

      // Hold the full bar briefly so it's visible even for tiny plugins.
      setTimeout(() => clearExporting(pid), 800);
    } catch (e) {
      clearExporting(pid);
      alert(`Erro ao exportar: ${e.message || e}`);
    }
  }

  async function importPlugin(file) {
    if (!file) return;
    setImporting(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const r = await fetch('/api/plugins/import', { method: 'POST', body: fd, headers: authHeaders() });
      if (r.status === 401) { handleUnauthorized(); throw new Error('Não autenticado.'); }
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'falha');
      await load();
      alert(`Plugin '${data.data.id}' importado. Ative-o quando estiver pronto.`);
    } catch (e) {
      alert(`Erro ao importar: ${e.message || e}`);
    } finally {
      setImporting(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  }

  // Atualizar: troca o código do plugin por um novo .zip SEM apagar tabelas/dados
  // (diferente de Deletar + Importar). O backend preserva o banco e roda só as
  // migrations novas no restart.
  function pickUpdate(pid) {
    if (!confirm(
      `Atualizar o plugin '${pid}' com um novo .zip?\n\n` +
      `Os dados e tabelas dele são PRESERVADOS — apenas o código é trocado e ` +
      `as migrations novas rodam no reinício.`
    )) return;
    pendingUpdatePid.current = pid;
    if (updateFileRef.current) updateFileRef.current.click();
  }

  async function updatePlugin(pid, file) {
    if (!file || !pid) return;
    setUpdating(pid);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const r = await fetch(`/api/plugins/${pid}/update`, { method: 'POST', body: fd, headers: authHeaders() });
      if (r.status === 401) { handleUnauthorized(); throw new Error('Não autenticado.'); }
      const data = await r.json();
      if (!data.ok) throw new Error(data.error || 'falha');
      if (data.data && data.data.warning) alert(`Atenção — ${data.data.warning}`);
      // Limpa o estado por-card: o RestartBanner (overlay) assume a UI a partir
      // daqui, e se o poll de restart estourar o timeout o botão não fica preso
      // em "Atualizando…".
      setUpdating(null);
      setRestarting(true);
    } catch (e) {
      alert(`Erro ao atualizar: ${e.message || e}`);
      setUpdating(null);
    } finally {
      pendingUpdatePid.current = null;
      if (updateFileRef.current) updateFileRef.current.value = '';
    }
  }

  if (loading) return html`<div class="text-wa-secondary">Carregando plugins…</div>`;
  if (error) return html`<div class="text-red-600">Erro: ${error}</div>`;

  // A plugin may ship a custom config UI as a screen flagged `config: true`
  // (rendered in the modal below instead of the auto-generated settings form).
  const cfgPlugin = settingsOpen ? plugins.find(p => p.id === settingsOpen) : null;
  const cfgScreen = cfgPlugin ? (cfgPlugin.screens || []).find(s => s.config) : null;

  return html`
    <div>
      ${restarting ? html`<${RestartBanner} />` : null}

      <div class="flex items-center justify-between mb-4">
        <div class="text-[12px] text-wa-secondary">
          API do core: ${apiVersion}
          · ${plugins.length} plugin${plugins.length === 1 ? '' : 's'}
        </div>
        <div class="flex items-center gap-2">
          <a
            href="https://whatsbot.techify.one/plugins"
            target="_blank"
            rel="noopener noreferrer"
            class="px-3 py-1.5 bg-red-600 text-white rounded text-[14px] hover:bg-red-700"
          >Loja de Plugins</a>
          <input type="file" ref=${fileRef} accept=".zip" class="hidden"
            onChange=${e => importPlugin(e.target.files && e.target.files[0])} />
          <input type="file" ref=${updateFileRef} accept=".zip" class="hidden"
            onChange=${e => updatePlugin(pendingUpdatePid.current, e.target.files && e.target.files[0])} />
          <button
            disabled=${importing}
            onClick=${() => fileRef.current && fileRef.current.click()}
            class="px-3 py-1.5 bg-wa-teal text-white rounded text-[14px] disabled:opacity-50"
          >${importing ? 'Importando…' : 'Importar (.zip)'}</button>
        </div>
      </div>

      ${plugins.length === 0
        ? html`
          <div class="bg-wa-panel border border-wa-border rounded p-6 text-center">
            <div class="font-medium mb-1">Nenhum plugin instalado</div>
            <div class="text-sm text-wa-secondary">
              Coloque um plugin em <code>storages/plugins/&lt;id&gt;/</code> ou importe um arquivo .zip.
            </div>
          </div>`
        : html`
          <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
            ${plugins.map(p => html`
              <div key=${p.id} class="bg-wa-bg border border-wa-border rounded-lg p-4">
                <div class="flex items-start justify-between gap-2">
                  <div>
                    <div class="font-medium text-[15px]">${p.name || p.id}</div>
                    <div class="text-[12px] text-wa-secondary">
                      <code>${p.id}</code>${p.version ? html` · v${p.version}` : null}
                      ${p.author ? html` · ${p.author}` : null}
                    </div>
                  </div>
                  <${StatusBadge} plugin=${p} />
                </div>
                ${(p.short_description || p.description) ? html`
                  <button
                    type="button"
                    onClick=${() => setDescOpen(p.id)}
                    title="Clique para ver a descrição completa"
                    class="block w-full text-left text-[13px] mt-2 text-wa-text hover:text-wa-teal cursor-pointer"
                  >
                    <span
                      class="block overflow-hidden"
                      style="display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;"
                    >${p.short_description || p.description}</span>
                    <span class="text-[12px] text-wa-teal mt-0.5 inline-block">ver mais</span>
                  </button>
                ` : null}
                ${(p.dependencies && p.dependencies.length) ? html`
                  <div class="text-[11px] mt-2 text-wa-secondary">
                    📦 Instala ao ativar: ${p.dependencies.join(', ')}
                  </div>
                ` : null}
                ${p.load_error ? html`
                  <div class="mt-2 text-[12px] text-red-700 bg-red-50 border border-red-100 rounded px-2 py-1 break-all">
                    ${p.load_error}
                  </div>
                ` : null}
                <div class="flex flex-wrap gap-2 mt-3">
                  <button
                    onClick=${() => toggle(p.id, !p.enabled)}
                    class="px-3 py-1 text-[13px] rounded ${p.enabled ? 'bg-yellow-500 text-white' : 'bg-green-600 text-white'}"
                  >${p.enabled ? 'Desativar' : 'Ativar'}</button>
                  <button
                    onClick=${() => { setSettingsOpen(p.id); pushUrl({ id: p.id }); }}
                    disabled=${!p.loaded}
                    class="px-3 py-1 text-[13px] rounded bg-wa-panel border border-wa-border disabled:opacity-50"
                  >Configurar</button>
                  <button
                    onClick=${() => exportPlugin(p.id)}
                    disabled=${exporting[p.id] != null}
                    class="px-3 py-1 text-[13px] rounded bg-wa-panel border border-wa-border disabled:opacity-50"
                  >${exporting[p.id] != null ? 'Exportando…' : 'Exportar'}</button>
                  <button
                    onClick=${() => pickUpdate(p.id)}
                    disabled=${updating === p.id}
                    title="Enviar um novo .zip preservando os dados/tabelas do plugin"
                    class="px-3 py-1 text-[13px] rounded bg-blue-50 text-blue-700 border border-blue-200 disabled:opacity-50"
                  >${updating === p.id ? 'Atualizando…' : 'Atualizar'}</button>
                  <button
                    onClick=${() => deletePlugin(p.id)}
                    class="px-3 py-1 text-[13px] rounded bg-red-50 text-red-700 border border-red-200"
                  >Deletar</button>
                </div>
                ${exporting[p.id] != null ? html`
                  <div class="mt-3">
                    <div class="flex items-center justify-between text-[11px] text-wa-secondary mb-1">
                      <span>Exportando…</span>
                      <span>${exporting[p.id]}%</span>
                    </div>
                    <div class="h-2 bg-wa-panel border border-wa-border rounded-full overflow-hidden">
                      <div class="h-full bg-wa-teal transition-all duration-150"
                           style=${`width:${exporting[p.id]}%`}></div>
                    </div>
                  </div>
                ` : null}
              </div>
            `)}
          </div>`
      }

      ${descOpen ? (() => {
        const dp = plugins.find(p => p.id === descOpen);
        if (!dp) return null;
        return html`
          <div class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center"
               onClick=${() => setDescOpen(null)}>
            <div class="bg-wa-bg rounded-lg shadow-xl max-w-lg w-full mx-4 max-h-[85vh] overflow-y-auto"
                 onClick=${e => e.stopPropagation()}>
              <div class="border-b border-wa-border px-4 py-3 flex items-center justify-between">
                <div>
                  <div class="font-medium">${dp.name || dp.id}</div>
                  <div class="text-[12px] text-wa-secondary">
                    <code>${dp.id}</code>${dp.version ? html` · v${dp.version}` : null}
                    ${dp.author ? html` · ${dp.author}` : null}
                  </div>
                </div>
                <button class="text-wa-secondary hover:text-wa-text"
                        onClick=${() => setDescOpen(null)}>×</button>
              </div>
              <div class="p-4 text-[14px] text-wa-text whitespace-pre-line leading-relaxed">
                ${dp.description || dp.short_description || 'Sem descrição.'}
              </div>
            </div>
          </div>
        `;
      })() : null}

      ${settingsOpen ? html`
        <div class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center">
          <div class="bg-wa-bg rounded-lg shadow-xl ${configModalWidth(cfgScreen)} w-full mx-4 max-h-[90vh] flex flex-col">
            <div class="border-b border-wa-border px-4 py-3 flex items-center justify-between shrink-0">
              <div class="font-medium">Configurações — ${(cfgPlugin && cfgPlugin.name) || settingsOpen}</div>
              <button class="text-wa-secondary hover:text-wa-text"
                      onClick=${() => { setSettingsOpen(null); pushUrl(null); }}>×</button>
            </div>
            <div class="p-4 flex-1 overflow-y-auto">
              ${cfgScreen
                ? html`<${PluginScreen} screen=${{ ...cfgScreen, pluginId: cfgPlugin.id }} />`
                : html`<${PluginSettingsForm}
                    pluginId=${settingsOpen}
                    onSaved=${() => onPluginsChanged && onPluginsChanged()}
                  />`}
            </div>
          </div>
        </div>
      ` : null}
    </div>
  `;
}

