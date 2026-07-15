// Self-service "change my password" modal (plano 47). Available to any logged-in
// RBAC user via the account block of the GearMenu — NOT gated by a permission.
// Requires the current password (re-auth); distinct from the admin reset in
// UsersManager (which sets OTHER users' passwords without the current one).
import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';
import { changeMyPassword } from '../services/api.js';

const html = htm.bind(h);

const MIN_LEN = 8;

export function ChangePasswordModal({ user, onClose, onNotify }) {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const tooShort = next && next.length < MIN_LEN;
  const mismatch = confirm && next !== confirm;
  const sameAsOld = next && current && next === current;
  const canSave = !busy && current && next && confirm
    && !tooShort && !mismatch && !sameAsOld;

  async function submit() {
    if (!canSave) return;
    setBusy(true);
    setError('');
    // Wrong current password comes back as 400 (not 401), so it lands here as a
    // normal error instead of logging the user out.
    const res = await changeMyPassword(current, next).catch(() => null);
    setBusy(false);
    if (res && res.ok) {
      if (onNotify) onNotify('Senha alterada com sucesso.');
      onClose();
    } else {
      setError((res && res.error) || 'Não foi possível alterar a senha.');
    }
  }

  return html`
    <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick=${onClose}>
      <div class="bg-wa-bg border border-wa-border rounded-lg p-5 w-full max-w-sm" onClick=${(e) => e.stopPropagation()}>
        <div class="text-[15px] font-medium text-wa-text mb-1">Trocar minha senha</div>
        <div class="text-[13px] text-wa-secondary mb-3 break-words">${user && (user.name || user.email)}</div>

        <label class="text-[12px] text-wa-secondary">Senha atual</label>
        <input class="wa-field w-full px-3 py-2 rounded-md text-[14px] mb-2 mt-1"
          type="password" placeholder="Senha atual" value=${current} autofocus
          onInput=${(e) => setCurrent(e.target.value)} />

        <label class="text-[12px] text-wa-secondary">Nova senha</label>
        <input class="wa-field w-full px-3 py-2 rounded-md text-[14px] mb-1 mt-1 ${tooShort ? 'border border-red-400' : ''}"
          type="password" placeholder=${`Nova senha (mín. ${MIN_LEN} caracteres)`} value=${next}
          onInput=${(e) => setNext(e.target.value)} />
        ${tooShort ? html`<div class="text-[12px] text-red-500 mb-1">A senha deve ter ao menos ${MIN_LEN} caracteres.</div>` : null}
        ${sameAsOld ? html`<div class="text-[12px] text-red-500 mb-1">A nova senha deve ser diferente da atual.</div>` : null}

        <label class="text-[12px] text-wa-secondary mt-1 block">Confirmar nova senha</label>
        <input class="wa-field w-full px-3 py-2 rounded-md text-[14px] mb-1 mt-1 ${mismatch ? 'border border-red-400' : ''}"
          type="password" placeholder="Confirmar nova senha" value=${confirm}
          onInput=${(e) => setConfirm(e.target.value)} />
        ${mismatch ? html`<div class="text-[12px] text-red-500 mb-1">As senhas não coincidem.</div>` : null}

        ${error ? html`<div class="text-[12px] text-red-500 mt-2">${error}</div>` : null}

        <div class="flex gap-2 justify-end mt-4">
          <button class="px-3 py-2 rounded-md text-[14px] text-wa-text hover:bg-wa-hover transition-colors"
            onClick=${onClose} disabled=${busy}>Cancelar</button>
          <button class="px-4 py-2 rounded-md text-[14px] text-white bg-wa-teal hover:opacity-90 transition-opacity disabled:opacity-50"
            onClick=${submit} disabled=${!canSave}>
            ${busy ? 'Salvando…' : 'Salvar'}</button>
        </div>
      </div>
    </div>
  `;
}
