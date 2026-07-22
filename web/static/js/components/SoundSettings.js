// Aba "Sons" de Configurações Gerais — COMO cada evento soa.
//
// É a única aba da tela SEM permissão: som é preferência PESSOAL, todo atendente
// logado entra, escolhe o som/volume/duração de cada evento, ouve o preview e
// importa sons para a biblioteca da equipe. QUANDO avisar (ativação por evento e
// notas privadas) mora na aba "Notificações" — as duas leem o mesmo registro
// (`useSoundPrefs`), cada uma editando os campos que lhe pertencem.
//
// Camadas: preferência do usuário (segue entre dispositivos, /api/me/sound-prefs)
// → padrão da equipe (config global, editável com settings.notifications/manage)
// → seed do código. O master "Tocar sons" e o multiplicador de volume são
// POR-DISPOSITIVO (localStorage).
//
// Regras de tema/dark-mode (CLAUDE.md): classes wa-* e .wa-field; nada de cor crua.
import { h } from 'preact';
import { useState, useEffect, useCallback } from 'preact/hooks';
import htm from 'htm';
import { authHeaders } from '../services/api.js';
import { getNotifPref, setNotifPref } from '../utils/notifications.js';
import * as soundEngine from '../utils/soundEngine.js';
import { hasPermission } from '../utils/permissions.js';
import { useSoundPrefs } from './sound/useSoundPrefs.js';

const html = htm.bind(h);

// Espelha DURATION_MIN/DURATION_MAX de server/sound_catalog.py (o PUT clampa).
const DURATION_MIN = 1;
const DURATION_MAX = 30;
// Espelha MAX_SOUND_BYTES / _AUDIO_EXTS de server/routes/sound_prefs.py — o
// servidor é quem manda; aqui a checagem só evita um upload fadado ao erro.
const MAX_SOUND_BYTES = 1024 * 1024;
const ACCEPT_EXTS = '.mp3,.ogg,.oga,.wav,.m4a,.aac,.webm,.flac';

// Sons oferecidos para um evento pela classe: 'alert' → sons de alerta; qualquer
// outra (notification/one-shot) → sons 'once'. 'any' (silêncio e os IMPORTADOS,
// que servem para os dois) sempre disponível.
function soundsForClass(sounds, cls) {
  const want = cls === 'alert' ? 'alert' : 'once';
  return (sounds || []).filter(s => s.cls === want || s.cls === 'any');
}

function VolumeSlider({ value, onInput, onChange, label }) {
  return html`
    <div class="flex items-center gap-2 min-w-[160px]">
      <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" class="text-wa-secondary shrink-0"><path d="M3 9v6h4l5 5V4L7 9H3z"/></svg>
      <input type="range" min="0" max="1" step="0.05" value=${value}
        aria-label=${label || 'Volume'}
        onInput=${(e) => onInput && onInput(parseFloat(e.target.value))}
        onChange=${(e) => onChange && onChange(parseFloat(e.target.value))}
        class="flex-1 accent-wa-teal" />
      <span class="text-[11px] text-wa-secondary w-8 text-right">${Math.round((value || 0) * 100)}%</span>
    </div>`;
}

// Modal de confirmação in-app (mesmo padrão de CustomAttributesManager/RolesManager
// — substitui o confirm() nativo do navegador, que não segue o tema).
function ConfirmModal({ title, message, confirmLabel = 'Confirmar', danger = false,
                       busy = false, onConfirm, onClose }) {
  return html`
    <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick=${onClose}>
      <div class="bg-wa-bg border border-wa-border rounded-lg p-5 w-full max-w-sm"
        onClick=${(e) => e.stopPropagation()}>
        <div class="flex items-center justify-between mb-2">
          <div class="text-[15px] font-medium text-wa-text">${title}</div>
          <button class="text-wa-secondary hover:text-wa-text text-xl leading-none" onClick=${onClose}>×</button>
        </div>
        <div class="text-[13px] text-wa-secondary mb-4 break-words">${message}</div>
        <div class="flex gap-2 justify-end">
          <button class="px-3 py-2 rounded-md text-[14px] text-wa-text hover:bg-wa-hover transition-colors"
            onClick=${onClose} disabled=${busy}>Cancelar</button>
          <button
            class=${`px-4 py-2 rounded-md text-[14px] text-white transition-opacity disabled:opacity-50 ${danger ? 'bg-red-600 hover:opacity-90' : 'bg-wa-teal hover:opacity-90'}`}
            onClick=${onConfirm} disabled=${busy}>${busy ? 'Aguarde…' : confirmLabel}</button>
        </div>
      </div>
    </div>`;
}

// ── Biblioteca de sons importados ─────────────────────────────────────────────
// Qualquer atendente logado importa (decisão do produto). O servidor recusa o que
// não for áudio e o que passar de 1 MB; aqui damos o feedback antes de subir.
function SoundLibrary({ sounds, onChanged, onPreview }) {
  const [file, setFile] = useState(null);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  // Edição inline do nome: `id` é o id de CATÁLOGO (`custom:<n>`), o mesmo que
  // identifica a linha na lista; `num` é o id numérico que vai na URL da API.
  const [renaming, setRenaming] = useState(null);   // {id, num, name}
  const [confirmDelete, setConfirmDelete] = useState(null);  // {id, num, label}

  const custom = (sounds || []).filter(s => s.kind === 'file');

  function pick(f) {
    setErr('');
    if (!f) { setFile(null); return; }
    if (f.size > MAX_SOUND_BYTES) {
      setErr(`Arquivo grande demais (máx. ${Math.round(MAX_SOUND_BYTES / 1024)} KB).`);
      setFile(null);
      return;
    }
    setFile(f);
    if (!name) setName(f.name.replace(/\.[^.]+$/, '').slice(0, 60));
  }

  async function upload() {
    if (!file) return;
    setBusy(true); setErr('');
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('name', name);
      const res = await fetch('/api/sounds/library', {
        method: 'POST', headers: authHeaders(), body: fd,
      });
      const j = await res.json().catch(() => ({}));
      if (j && j.ok) { setFile(null); setName(''); await onChanged(); }
      else setErr((j && j.error) || 'Não foi possível importar o som.');
    } catch (_) { setErr('Não foi possível importar o som.'); }
    setBusy(false);
  }

  async function remove() {
    if (!confirmDelete) return;
    setBusy(true);
    try {
      await fetch(`/api/sounds/library/${confirmDelete.num}`, {
        method: 'DELETE', headers: authHeaders(),
      });
      await onChanged();
    } catch (_) { /* ignore */ }
    setConfirmDelete(null);
    setBusy(false);
  }

  async function commitRename() {
    if (!renaming || !renaming.name.trim()) return;
    setBusy(true);
    try {
      await fetch(`/api/sounds/library/${renaming.num}`, {
        method: 'PUT',
        headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ name: renaming.name }),
      });
      await onChanged();
    } catch (_) { /* ignore */ }
    setRenaming(null);
    setBusy(false);
  }

  return html`
    <div class="bg-wa-bg rounded-xl p-5 border border-wa-border shadow-sm flex flex-col gap-3">
      <h3 class="text-xs font-semibold text-wa-secondary uppercase tracking-wider">Meus sons importados</h3>
      <span class="text-xs text-wa-secondary -mt-2">
        Importe um áudio curto para usar em qualquer evento. Só arquivos de áudio
        (mp3, ogg, wav, m4a, aac, webm, flac), até ${Math.round(MAX_SOUND_BYTES / 1024)} KB.
        O nome escolhido é o que aparece na lista de sons.
      </span>

      <div class="flex items-end gap-3 flex-wrap">
        <div class="flex flex-col gap-1">
          <label class="text-[12px] text-wa-secondary" for="sound-file">Arquivo</label>
          <input id="sound-file" type="file" accept=${`audio/*,${ACCEPT_EXTS}`}
            onChange=${(e) => pick(e.target.files && e.target.files[0])}
            class="wa-field text-sm rounded-lg border border-wa-border px-2 py-1.5 max-w-[260px]" />
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-[12px] text-wa-secondary" for="sound-name">Nome do som</label>
          <input id="sound-name" type="text" maxlength="60" value=${name} placeholder="Ex.: Sino da recepção"
            onInput=${(e) => setName(e.target.value)}
            class="wa-field text-sm rounded-lg border border-wa-border px-2 py-1.5 w-[220px]" />
        </div>
        <button type="button" onClick=${upload} disabled=${!file || busy}
          class="px-3 py-1.5 rounded-lg text-sm bg-wa-teal text-white hover:opacity-90 disabled:opacity-50">
          ${busy ? 'Importando…' : 'Importar som'}
        </button>
      </div>
      ${err ? html`<span class="text-[12px] text-red-500">${err}</span>` : null}

      ${confirmDelete ? html`
        <${ConfirmModal}
          title="Excluir som importado"
          message=${`Excluir "${confirmDelete.label}"? Quem estiver usando este som volta a ouvir o som padrão do evento.`}
          confirmLabel="Excluir"
          danger
          busy=${busy}
          onConfirm=${remove}
          onClose=${() => setConfirmDelete(null)} />
      ` : null}

      ${custom.length ? html`
        <div class="flex flex-col gap-2 mt-1">
          ${custom.map(s => html`
            <div key=${s.id} class="flex items-center gap-3 flex-wrap p-2 rounded-lg bg-wa-panel border border-wa-border">
              ${renaming && renaming.id === s.id ? html`
                <input type="text" maxlength="60" value=${renaming.name} autofocus
                  onInput=${(e) => setRenaming({ ...renaming, name: e.target.value })}
                  onKeyDown=${(e) => { if (e.key === 'Enter') commitRename(); if (e.key === 'Escape') setRenaming(null); }}
                  class="wa-field text-sm rounded-lg border border-wa-border px-2 py-1 flex-1 min-w-[160px]" />
                <button type="button" onClick=${commitRename} disabled=${busy}
                  class="text-[12px] text-wa-teal hover:underline">Salvar</button>
                <button type="button" onClick=${() => setRenaming(null)}
                  class="text-[12px] text-wa-secondary hover:text-wa-text">Cancelar</button>
              ` : html`
                <span class="text-sm text-wa-text flex-1 min-w-[160px]">${s.label}</span>
                <button type="button" onClick=${() => onPreview(s.id)}
                  class="flex items-center gap-1 px-2.5 py-1 rounded-lg text-sm border border-wa-border text-wa-text hover:bg-wa-hover transition-colors">
                  <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                  Ouvir
                </button>
                <button type="button"
                  onClick=${() => setRenaming({ id: s.id, num: s.id.split(':')[1], name: s.label })}
                  class="text-[12px] text-wa-secondary hover:text-wa-text underline decoration-dotted">Renomear</button>
                <button type="button" disabled=${busy}
                  onClick=${() => setConfirmDelete({ id: s.id, num: s.id.split(':')[1], label: s.label })}
                  class="text-[12px] text-red-500 hover:text-red-600">Excluir</button>
              `}
            </div>`)}
        </div>
      ` : html`<span class="text-[12px] text-wa-secondary">Nenhum som importado ainda.</span>`}
    </div>`;
}

export default function SoundSettings({ config, onSaveConfig, currentUser }) {
  // O padrão da EQUIPE é uma config global: quem administra as notificações (ou
  // tudo) pode editá-lo. A aba em si continua aberta a qualquer atendente.
  const canManage = hasPermission(currentUser, 'settings.manage')
    || hasPermission(currentUser, 'settings.notifications');

  const p = useSoundPrefs({ onSaveConfig });

  // Camada por-dispositivo (localStorage): master ligado + multiplicador de volume.
  const [deviceMaster, setDeviceMaster] = useState(true);
  const [deviceVol, setDeviceVol] = useState(1);
  useEffect(() => {
    setDeviceMaster(getNotifPref('sound'));
    setDeviceVol(soundEngine.getDeviceVolumeMult());
  }, []);

  function toggleDeviceMaster(on) {
    setDeviceMaster(on);
    setNotifPref('sound', on);   // whatsbot_notif_sound (dispara whatsbot:notif-prefs)
  }
  function commitDeviceVol(v) {
    setDeviceVol(v);
    soundEngine.setDeviceVolumeMult(v);
  }

  // Recarrega catálogo + motor após mexer na biblioteca (o catálogo embute os
  // sons importados, então o seletor e o preview veem a mudança na hora).
  const reloadLibrary = useCallback(async () => {
    await p.reload();
    await soundEngine.reloadPrefs();
  }, [p]);

  // Preview (gesto de usuário → destrava a autoplay policy).
  function preview(soundId, volume, cls, duration) {
    soundEngine.playDescriptor(soundId, {
      volume,
      loop: cls === 'alert',
      duration: cls === 'alert' ? Math.min(duration || 3, 4) : 0,
    });
  }

  if (p.loading || !p.catalog) {
    return html`<div class="bg-wa-bg rounded-xl p-5 animate-pulse-slow text-wa-secondary border border-wa-border">Carregando…</div>`;
  }

  // Grid de eventos: som, preview, volume e (nos alertas) duração. `mode` = 'user' | 'admin'.
  function EventGrid({ mode }) {
    return html`
      ${Object.entries(p.groups()).map(([groupName, evs]) => html`
        <div class="mb-4">
          <div class="text-[11px] font-semibold text-wa-secondary uppercase tracking-wider mb-2">${groupName}</div>
          <div class="flex flex-col gap-2">
            ${evs.map(ev => {
              const key = ev.key;
              const soundId = p.readVal(mode, key, 'sound') || 'none';
              const rawVol = p.readVal(mode, key, 'volume');
              const volume = (typeof rawVol === 'number') ? rawVol : 0.6;
              const duration = p.readVal(mode, key, 'duration') ?? 5;
              const opts = soundsForClass(p.catalog.sounds, ev.cls);
              const custom = mode === 'user' && p.isCustom(key);
              const isAlert = ev.cls === 'alert';
              return html`
                <div key=${key} class="flex flex-col gap-2 p-3 bg-wa-panel rounded-lg border border-wa-border">
                  <div class="flex items-center justify-between gap-3 flex-wrap">
                    <span class="text-sm font-semibold text-wa-text">${ev.label}</span>
                    ${custom
                      ? html`<span class="text-[10px] px-2 py-0.5 rounded-full bg-wa-teal/15 text-wa-teal font-medium">personalizado</span>`
                      : html`<span class="text-[10px] px-2 py-0.5 rounded-full bg-wa-hover text-wa-secondary">padrão da equipe</span>`}
                  </div>
                  ${ev.hint ? html`<div class="text-[11px] text-wa-secondary -mt-1">${ev.hint}</div>` : null}
                  <div class="flex items-center gap-3 flex-wrap">
                    <select value=${soundId} onChange=${(e) => p.writeVal(mode, key, 'sound', e.target.value)}
                      class="wa-field px-2 py-1.5 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none">
                      ${opts.map(s => html`<option value=${s.id}>${s.label}</option>`)}
                    </select>
                    <button type="button"
                      onClick=${() => preview(soundId, volume, ev.cls, duration)}
                      class="flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-sm border border-wa-border text-wa-text hover:bg-wa-hover transition-colors">
                      <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                      Ouvir
                    </button>
                    <${VolumeSlider} value=${volume} label=${`Volume de ${ev.label}`}
                      onInput=${(v) => mode === 'admin' ? p.setAdminField(key, 'volume', v) : p.setUserFieldLocal(key, 'volume', v)}
                      onChange=${(v) => p.writeVal(mode, key, 'volume', v)} />
                    ${isAlert ? html`
                      <div class="flex items-center gap-2">
                        <label class="text-[12px] text-wa-secondary whitespace-nowrap" for=${`dur-${mode}-${key}`}>Duração (s)</label>
                        <input id=${`dur-${mode}-${key}`} type="number" min=${DURATION_MIN} max=${DURATION_MAX} step="1"
                          value=${duration}
                          onChange=${(e) => {
                            const n = parseInt(e.target.value, 10);
                            if (!isNaN(n)) p.writeVal(mode, key, 'duration', Math.max(DURATION_MIN, Math.min(DURATION_MAX, n)));
                          }}
                          class="wa-field w-16 px-2 py-1.5 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none" />
                      </div>
                    ` : null}
                    ${custom ? html`
                      <button type="button" onClick=${() => p.restoreDefault(key)}
                        class="text-[12px] text-wa-secondary hover:text-wa-text underline decoration-dotted">Restaurar padrão</button>
                    ` : null}
                  </div>
                  <div class="text-[11px] text-wa-secondary">
                    ${isAlert ? `Alerta contínuo — repete por ${duration}s.` : 'Toca uma vez.'}
                  </div>
                </div>`;
            })}
          </div>
        </div>
      `)}
    `;
  }

  return html`
    <div class="flex flex-col gap-4 flex-1">
      <!-- Master + volume do dispositivo -->
      <div class="bg-wa-bg rounded-xl p-5 border border-wa-border shadow-sm flex flex-col gap-4">
        <label class="flex items-center gap-2 text-sm font-semibold text-wa-text cursor-pointer">
          <input type="checkbox" checked=${deviceMaster}
            onChange=${(e) => toggleDeviceMaster(e.target.checked)}
            class="w-4 h-4 rounded border-wa-border accent-wa-teal" />
          Tocar sons
        </label>
        <span class="text-xs text-wa-secondary -mt-2">Liga/desliga todos os sons de notificação NESTE dispositivo (navegador). As escolhas por evento abaixo seguem você em qualquer dispositivo.</span>
        <div class="flex items-center gap-3">
          <span class="text-sm font-semibold text-wa-text min-w-[160px]">Volume neste dispositivo</span>
          <${VolumeSlider} value=${deviceVol} label="Volume neste dispositivo"
            onInput=${(v) => setDeviceVol(v)} onChange=${(v) => commitDeviceVol(v)} />
        </div>
        <span class="text-xs text-wa-secondary -mt-2">Ajuste local (ex.: PC do escritório × notebook silencioso). Multiplica o volume de cada evento sem mudar sua preferência sincronizada.</span>
      </div>

      <!-- Biblioteca de sons importados -->
      <${SoundLibrary} sounds=${p.catalog.sounds} onChanged=${reloadLibrary}
        onPreview=${(id) => preview(id, 0.8, 'notification', 0)} />

      <!-- Som por evento (por-usuário) -->
      <div class="bg-wa-bg rounded-xl p-5 border border-wa-border shadow-sm">
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-xs font-semibold text-wa-secondary uppercase tracking-wider">Som de cada evento</h3>
          ${p.savingUser ? html`<span class="text-[11px] text-wa-secondary">salvando…</span>` : null}
        </div>
        <span class="text-xs text-wa-secondary block mb-3">Quais eventos avisam você fica na aba <span class="font-semibold">Notificações</span>.</span>
        ${EventGrid({ mode: 'user' })}
      </div>

      <!-- Modo admin: padrão da equipe (som/volume/duração) -->
      ${canManage ? html`
        <div class="bg-wa-bg rounded-xl p-5 border border-wa-border shadow-sm">
          <div class="flex items-center justify-between gap-3 flex-wrap">
            <div>
              <h3 class="text-xs font-semibold text-wa-secondary uppercase tracking-wider">Padrão da equipe</h3>
              <span class="text-xs text-wa-secondary">Define o som para quem não personalizou.</span>
            </div>
            ${!p.adminMode
              ? html`<button type="button" onClick=${p.enterAdmin}
                  class="px-3 py-1.5 rounded-lg text-sm bg-wa-teal text-white hover:opacity-90 transition-opacity">Editar padrão da equipe</button>`
              : html`
                <div class="flex items-center gap-2">
                  ${p.adminSaved ? html`<span class="text-[12px] text-wa-teal font-medium">✓ Salvo!</span>` : null}
                  <button type="button" onClick=${p.exitAdmin}
                    class="px-3 py-1.5 rounded-lg text-sm border border-wa-border text-wa-text hover:bg-wa-hover">Cancelar</button>
                  <button type="button" onClick=${p.saveAdmin} disabled=${p.savingAdmin}
                    class="px-3 py-1.5 rounded-lg text-sm bg-wa-teal text-white hover:opacity-90 disabled:opacity-50">
                    ${p.savingAdmin ? 'Salvando…' : 'Salvar padrão'}</button>
                </div>`}
          </div>
          ${p.adminMode ? html`
            <div class="mt-4 pt-4 border-t border-wa-border">
              ${EventGrid({ mode: 'admin' })}
            </div>
          ` : null}
        </div>
      ` : null}
    </div>`;
}
