// Aba "Notificações" de Configurações Gerais — QUANDO você é avisado.
//
// Gateada por `settings.notifications` (ou `settings.manage`). Divide o mesmo
// registro de preferências da aba "Sons" (`useSoundPrefs`): aqui mora a ATIVAÇÃO
// por evento (quais eventos avisam) e as notificações globais da equipe; lá, o
// som/volume/duração de cada evento.
//
// Camadas: preferência do usuário → padrão da equipe → seed do código. Um evento
// desligado no padrão da equipe pode ser religado por quem quiser (o gate do
// SERVIDOR nas transferências é o único que o atendente não vence).
//
// Regras de tema/dark-mode (CLAUDE.md): classes wa-* e .wa-field; nada de cor crua.
import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import htm from 'htm';
import { hasPermission } from '../utils/permissions.js';
import { useSoundPrefs } from './sound/useSoundPrefs.js';

const html = htm.bind(h);

export default function NotificationSettings({ config, onSaveConfig, currentUser }) {
  // O padrão da equipe e as chaves globais são de quem administra notificações.
  const canManage = hasPermission(currentUser, 'settings.manage')
    || hasPermission(currentUser, 'settings.notifications');

  const p = useSoundPrefs({ onSaveConfig });

  // Notificação de notas privadas — config GLOBAL, salva na hora (sem botão).
  const [notifyPrivate, setNotifyPrivate] = useState(false);
  const [savingNotif, setSavingNotif] = useState(false);
  useEffect(() => { setNotifyPrivate(config?.notify_private_messages ?? false); }, [config]);
  async function saveNotifyPrivate(on) {
    setNotifyPrivate(on);
    setSavingNotif(true);
    const result = await onSaveConfig({ notify_private_messages: on });
    setSavingNotif(false);
    if (result === false) setNotifyPrivate(!on);   // rollback do otimismo
  }

  if (p.loading || !p.catalog) {
    return html`<div class="bg-wa-bg rounded-xl p-5 animate-pulse-slow text-wa-secondary border border-wa-border">Carregando…</div>`;
  }

  // Lista de eventos com a caixa de ativação. `mode` = 'user' | 'admin'.
  function EventList({ mode }) {
    return html`
      ${Object.entries(p.groups()).map(([groupName, evs]) => html`
        <div class="mb-4">
          <div class="text-[11px] font-semibold text-wa-secondary uppercase tracking-wider mb-2">${groupName}</div>
          <div class="flex flex-col gap-2">
            ${evs.map(ev => {
              const key = ev.key;
              const enabled = p.readVal(mode, key, 'enabled') !== false;
              const custom = mode === 'user' && p.isCustom(key);
              return html`
                <div key=${key} class="flex items-center justify-between gap-3 flex-wrap p-3 bg-wa-panel rounded-lg border border-wa-border">
                  <label class="flex items-center gap-2 text-sm font-semibold text-wa-text cursor-pointer">
                    <input type="checkbox" checked=${enabled}
                      onChange=${(e) => p.writeVal(mode, key, 'enabled', e.target.checked)}
                      class="w-4 h-4 rounded border-wa-border accent-wa-teal" />
                    ${ev.label}
                  </label>
                  <div class="flex items-center gap-2">
                    ${custom
                      ? html`<span class="text-[10px] px-2 py-0.5 rounded-full bg-wa-teal/15 text-wa-teal font-medium">personalizado</span>`
                      : html`<span class="text-[10px] px-2 py-0.5 rounded-full bg-wa-hover text-wa-secondary">padrão da equipe</span>`}
                    ${custom ? html`
                      <button type="button" onClick=${() => p.restoreDefault(key)}
                        class="text-[12px] text-wa-secondary hover:text-wa-text underline decoration-dotted">Restaurar padrão</button>
                    ` : null}
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
      <!-- Ativação por evento (por-usuário) -->
      <div class="bg-wa-bg rounded-xl p-5 border border-wa-border shadow-sm">
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-xs font-semibold text-wa-secondary uppercase tracking-wider">Quero ser avisado de</h3>
          ${p.savingUser ? html`<span class="text-[11px] text-wa-secondary">salvando…</span>` : null}
        </div>
        <span class="text-xs text-wa-secondary block mb-3">
          Escolha quais eventos avisam você. A escolha segue você em qualquer dispositivo;
          o som de cada um fica na aba <span class="font-semibold">Sons</span>.
        </span>
        ${EventList({ mode: 'user' })}
      </div>

      <!-- Notificações globais (admin) -->
      ${canManage ? html`
        <div class="bg-wa-bg rounded-xl p-5 border border-wa-border shadow-sm flex flex-col gap-2">
          <div class="flex items-center justify-between gap-3">
            <h3 class="text-xs font-semibold text-wa-secondary uppercase tracking-wider">Notificações da equipe</h3>
            ${savingNotif ? html`<span class="text-[11px] text-wa-secondary">salvando…</span>` : null}
          </div>
          <div class="flex flex-col gap-2 p-3 bg-wa-panel rounded-lg border border-wa-border">
            <label class="flex items-center gap-2 text-sm font-semibold text-wa-text cursor-pointer">
              <input type="checkbox" checked=${notifyPrivate}
                onChange=${(e) => saveNotifyPrivate(e.target.checked)}
                class="w-4 h-4 rounded border-wa-border accent-wa-teal" />
              Notificar mensagens privadas
            </label>
            <span class="text-xs text-wa-secondary">Ao adicionar uma nota privada (mensagem interna, não enviada ao contato), acende o ícone verde na conversa e a contagem na aba do navegador. Não toca som.</span>
          </div>
        </div>
      ` : null}

      <!-- Modo admin: padrão da equipe (master + ativação por evento) -->
      ${canManage ? html`
        <div class="bg-wa-bg rounded-xl p-5 border border-wa-border shadow-sm">
          <div class="flex items-center justify-between gap-3 flex-wrap">
            <div>
              <h3 class="text-xs font-semibold text-wa-secondary uppercase tracking-wider">Padrão da equipe</h3>
              <span class="text-xs text-wa-secondary">Vale para quem não personalizou. Nas transferências, desligar aqui silencia o alerta para todos.</span>
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
              <label class="flex items-center gap-2 text-sm font-semibold text-wa-text cursor-pointer mb-3">
                <input type="checkbox" checked=${p.adminDraft?.master_enabled !== false}
                  onChange=${(e) => p.setAdminMaster(e.target.checked)}
                  class="w-4 h-4 rounded border-wa-border accent-wa-teal" />
                Sons ligados por padrão para a equipe
              </label>
              ${EventList({ mode: 'admin' })}
            </div>
          ` : null}
        </div>
      ` : null}
    </div>`;
}
