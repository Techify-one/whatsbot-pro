// App-shell GearMenu + PageHeader (Plano 23 · D4), extracted verbatim from
// app.js. The gear (top-right) menu: core screens gated by permission (P48: hide,
// don't disable), plugin screens filtered by `requires`, the gear.menu.items
// extension slot, the dark-mode toggle, the account-balance link, and logout.
// Filtering/gating semantics are preserved EXACTLY.
import { h } from 'preact';
import { useState, useEffect, useRef } from 'preact/hooks';
import htm from 'htm';
import { Slot } from '../../plugins/Slot.js';
import { hasPermission, hasAnyPermission, AI_PERMS } from '../../utils/permissions.js';
import { CORE_TAB_PATHS, pluginTabId } from './screenRegistry.js';

const html = htm.bind(h);

function MenuItem({ active, href, onClick, icon, children, gated = true }) {
  if (!gated) return null;  // permission gate (P48: hide, don't disable)
  function handleClick(e) {
    // Let the browser handle modified clicks (Ctrl/Cmd/Shift) and middle-click
    // so users can open the menu item in a new tab/window.
    if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    onClick();
  }
  return html`
    <a
      href=${href}
      onClick=${handleClick}
      class="w-full text-left px-4 py-2.5 text-[14px] hover:bg-wa-hover transition-colors flex items-center gap-2 no-underline ${active ? 'text-wa-teal font-medium' : 'text-wa-text'}"
    >
      ${icon}
      ${children}
    </a>
  `;
}

export function GearMenu({ tab, onTabChange, pluginScreens, hasPassword, onLogout, accountUrl, currentUser, onChangePassword }) {
  const [open, setOpen] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    function handleClick(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) setOpen(false);
    }
    if (open) document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [open]);

  const close = () => setOpen(false);

  // Dark mode: toggles `.dark` on <html> (re-themes the whole app via CSS
  // variables) and persists the choice. The early script in index.html applies
  // it before first paint so there's no flash on reload.
  const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'));
  function toggleDark() {
    const next = !document.documentElement.classList.contains('dark');
    document.documentElement.classList.toggle('dark', next);
    try { localStorage.setItem('whatsbot_theme', next ? 'dark' : 'light'); } catch (e) {}
    setDark(next);
  }

  // Permission gate for menu items (FF1). Open installs (no currentUser)
  // see everything; a logged-in user sees only what their role allows.
  const can = (perm) => hasPermission(currentUser, perm);

  return html`
    <div ref=${menuRef} class="fixed top-3 right-3 z-50">
      <button
        onClick=${() => setOpen(!open)}
        class="w-[36px] h-[36px] flex items-center justify-center rounded-full bg-wa-bg shadow-md border border-wa-border hover:bg-wa-hover transition-colors"
      >
        <svg viewBox="0 0 24 24" width="20" height="20" fill="#54656f">
          <path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 00.12-.61l-1.92-3.32a.488.488 0 00-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.484.484 0 00-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.07.62-.07.94s.02.64.07.94l-2.03 1.58a.49.49 0 00-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/>
        </svg>
      </button>
      ${open ? html`
        <div class="absolute right-0 mt-1 bg-wa-bg rounded-lg shadow-lg border border-wa-border py-1 min-w-[180px] max-h-[80vh] overflow-y-auto">
          <${MenuItem} active=${tab === 'dashboard'} href=${CORE_TAB_PATHS.dashboard} onClick=${() => { onTabChange('dashboard'); close(); }}
            icon=${html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 00.12-.61l-1.92-3.32a.488.488 0 00-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.484.484 0 00-.48-.41h-3.84c-.24 0-.43.17-.47.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.74 8.87c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.07.62-.07.94s.02.64.07.94l-2.03 1.58a.49.49 0 00-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.84c.24 0 .44-.17.47-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z"/></svg>`}
          >Configurações Gerais</${MenuItem}>
          <!-- "Notificações e sons": som é PESSOAL → visível a todos (sem gate). -->
          <${MenuItem} active=${tab === 'sounds'} href=${CORE_TAB_PATHS.sounds} onClick=${() => { onTabChange('sounds'); close(); }}
            icon=${html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg>`}
          >Notificações e sons</${MenuItem}>
          <${MenuItem} gated=${can('sandbox.use')} active=${tab === 'sandbox'} href=${CORE_TAB_PATHS.sandbox} onClick=${() => { onTabChange('sandbox'); close(); }}
            icon=${html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M7 5h10v2h2V3c0-.55-.45-1-1-1H6c-.55 0-1 .45-1 1v4h2V5zm8.41 11.59L20 12l-4.59-4.59L14 8.83 17.17 12 14 15.17l1.41 1.42zM10 15.17L6.83 12 10 8.83 8.59 7.41 4 12l4.59 4.59L10 15.17zM17 19H7v-2H5v4c0 .55.45 1 1 1h12c.55 0 1-.45 1-1v-4h-2v2z"/></svg>`}
          >Sandbox</${MenuItem}>
          <${MenuItem} gated=${can('usage.read')} active=${tab === 'costs'} href=${CORE_TAB_PATHS.costs} onClick=${() => { onTabChange('costs'); close(); }}
            icon=${html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M11.8 10.9c-2.27-.59-3-1.2-3-2.15 0-1.09 1.01-1.85 2.7-1.85 1.78 0 2.44.85 2.5 2.1h2.21c-.07-1.72-1.12-3.3-3.21-3.81V3h-3v2.16c-1.94.42-3.5 1.68-3.5 3.61 0 2.31 1.91 3.46 4.7 4.13 2.5.6 3 1.48 3 2.41 0 .69-.49 1.79-2.7 1.79-2.06 0-2.87-.92-2.98-2.1h-2.2c.12 2.19 1.76 3.42 3.68 3.83V21h3v-2.15c1.95-.37 3.5-1.5 3.5-3.55 0-2.84-2.43-3.81-4.7-4.4z"/></svg>`}
          >Custos</${MenuItem}>
          <${MenuItem} gated=${can('execution.read')} active=${tab === 'executions'} href=${CORE_TAB_PATHS.executions} onClick=${() => { onTabChange('executions'); close(); }}
            icon=${html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M13 3c-4.97 0-9 4.03-9 9H1l3.89 3.89.07.14L9 12H6c0-3.87 3.13-7 7-7s7 3.13 7 7-3.13 7-7 7c-1.93 0-3.68-.79-4.94-2.06l-1.42 1.42C8.27 19.99 10.51 21 13 21c4.97 0 9-4.03 9-9s-4.03-9-9-9zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z"/></svg>`}
          >Execuções</${MenuItem}>
          <!-- A aba "Atendimentos" (kanban/lista) é EXCLUSIVA do plugin protocolos:
               ele a provê via overrideRoute('attendances') + slot gear.menu.items.
               Com o plugin desativado não há aba — as conversas ficam no hub de Contatos. -->
          <${MenuItem} gated=${can('contact.read')} active=${tab === 'contatos'} href=${CORE_TAB_PATHS.contatos} onClick=${() => { onTabChange('contatos'); close(); }}
            icon=${html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z"/></svg>`}
          >Contatos</${MenuItem}>
          <${MenuItem} gated=${can('channel.manage')} active=${tab === 'channels'} href=${CORE_TAB_PATHS.channels} onClick=${() => { onTabChange('channels'); close(); }}
            icon=${html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M12 1a9 9 0 0 0-9 9v7a3 3 0 0 0 3 3h2v-8H5v-2a7 7 0 0 1 14 0v2h-3v8h2a3 3 0 0 0 3-3v-7a9 9 0 0 0-9-9z"/></svg>`}
          >Canais</${MenuItem}>

          ${(() => {
            // Hide, don't disable (P48): a plugin screen with `requires: <key>`
            // is shown only when the user holds plugin.<id>.<key>. Open installs
            // installs (no currentUser) see everything (plano "RBAC para Plugins").
            const visible = (pluginScreens || []).filter(s =>
              !s.requires || can(`plugin.${s.pluginId}.${s.requires}`));
            return visible.length > 0 ? html`
            <div class="border-t border-wa-border my-1"></div>
            <div class="px-4 py-1.5 text-[11px] uppercase tracking-wide text-wa-secondary">
              Plugins
            </div>
            ${visible.map(s => html`
              <${MenuItem}
                key=${pluginTabId(s)}
                active=${tab === pluginTabId(s)}
                href=${s.path}
                onClick=${() => { onTabChange(pluginTabId(s)); close(); }}
                icon=${html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M5 3h6v8H5V3zm8 0h6v6h-6V3zm0 8h6v10h-6V11zm-8 4h6v6H5v-6z"/></svg>`}
              >${s.title}</${MenuItem}>
            `)}
          ` : null;
          })()}

          <!-- Plugin-contributed menu entries (extension slot). -->
          <${Slot} name="gear.menu.items" ctx=${{ onTabChange, close, tab }} />

          <div class="border-t border-wa-border my-1"></div>
          ${accountUrl && can('billing.manage') ? html`
            <a
              href=${accountUrl}
              target="_blank"
              rel="noopener noreferrer"
              onClick=${close}
              class="w-full text-left px-4 py-2.5 text-[14px] hover:bg-wa-hover transition-colors flex items-center gap-2 no-underline text-wa-text"
            >
              <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M20 4H4c-1.11 0-1.99.89-1.99 2L2 18c0 1.11.89 2 2 2h16c1.11 0 2-.89 2-2V6c0-1.11-.89-2-2-2zm0 14H4v-6h16v6zm0-10H4V6h16v2z"/></svg>
              Saldo e Recargar
            </a>
          ` : null}
          <${MenuItem} gated=${can('quickreply.manage')} active=${tab === 'quick-replies'} href=${CORE_TAB_PATHS['quick-replies']} onClick=${() => { onTabChange('quick-replies'); close(); }}
            icon=${html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-7 9h-2V9h2v2zm0-4h-2V5h2v2zm4 4h-2V9h2v2zm0-4h-2V5h2v2zM9 11H7V9h2v2zm0-4H7V5h2v2z"/></svg>`}
          >Respostas Rápidas</${MenuItem}>
          <${MenuItem} gated=${can('custom_attribute.manage')} active=${tab === 'custom-attributes'} href=${CORE_TAB_PATHS['custom-attributes']} onClick=${() => { onTabChange('custom-attributes'); close(); }}
            icon=${html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M3 5h18v2H3V5zm0 6h18v2H3v-2zm0 6h12v2H3v-2z"/></svg>`}
          >Atributos Personalizados</${MenuItem}>
          <${MenuItem} gated=${can('settings.manage')} active=${tab === 'runtime'} href=${CORE_TAB_PATHS.runtime} onClick=${() => { onTabChange('runtime'); close(); }}
            icon=${html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M13 3a9 9 0 0 0-9 9H1l4 4 4-4H6a7 7 0 1 1 2 4.9l-1.4 1.4A9 9 0 1 0 13 3z"/></svg>`}
          >Runtime</${MenuItem}>
          <${MenuItem} gated=${can('users.manage')} active=${tab === 'users'} href=${CORE_TAB_PATHS.users} onClick=${() => { onTabChange('users'); close(); }}
            icon=${html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z"/></svg>`}
          >Usuários</${MenuItem}>
          <${MenuItem} gated=${can('audit.read')} active=${tab === 'audit'} href=${CORE_TAB_PATHS.audit} onClick=${() => { onTabChange('audit'); close(); }}
            icon=${html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M14 2H6c-1.1 0-1.99.9-1.99 2L4 20c0 1.1.89 2 1.99 2H18c1.1 0 2-.9 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z"/></svg>`}
          >Auditoria</${MenuItem}>
          <${MenuItem} gated=${hasAnyPermission(currentUser, AI_PERMS)} active=${tab === 'ai'} href=${CORE_TAB_PATHS.ai} onClick=${() => { onTabChange('ai'); close(); }}
            icon=${html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M12 2a2 2 0 0 1 2 2v1h3a2 2 0 0 1 2 2v2h1a2 2 0 0 1 0 4h-1v2a2 2 0 0 1-2 2h-3v1a2 2 0 0 1-4 0v-1H7a2 2 0 0 1-2-2v-2H4a2 2 0 0 1 0-4h1V7a2 2 0 0 1 2-2h3V4a2 2 0 0 1 2-2zm-3 7a1 1 0 0 0-1 1v4a1 1 0 0 0 2 0v-4a1 1 0 0 0-1-1zm6 0a1 1 0 0 0-1 1v4a1 1 0 0 0 2 0v-4a1 1 0 0 0-1-1z"/></svg>`}
          >Configurações de IA</${MenuItem}>
          <${MenuItem} gated=${can('plugins.manage')} active=${tab === 'plugins'} href=${CORE_TAB_PATHS.plugins} onClick=${() => { onTabChange('plugins'); close(); }}
            icon=${html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M20.5 11H19V7c0-1.1-.9-2-2-2h-4V3.5C13 2.12 11.88 1 10.5 1S8 2.12 8 3.5V5H4c-1.1 0-1.99.9-1.99 2v3.8H3.5c1.49 0 2.7 1.21 2.7 2.7s-1.21 2.7-2.7 2.7H2V20c0 1.1.9 2 2 2h3.8v-1.5c0-1.49 1.21-2.7 2.7-2.7s2.7 1.21 2.7 2.7V22H17c1.1 0 2-.9 2-2v-4h1.5c1.38 0 2.5-1.12 2.5-2.5S21.88 11 20.5 11z"/></svg>`}
          >Gerenciar Plugins</${MenuItem}>

          <div class="border-t border-wa-border my-1"></div>
          <button
            onClick=${toggleDark}
            class="w-full text-left px-4 py-2.5 text-[14px] hover:bg-wa-hover transition-colors flex items-center gap-2 text-wa-text"
          >
            ${dark
              ? html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M12 7c-2.76 0-5 2.24-5 5s2.24 5 5 5 5-2.24 5-5-2.24-5-5-5zm0-5l2.39 3.42C13.65 5.15 12.84 5 12 5c-.84 0-1.65.15-2.39.42L12 2zM3.34 7l4.16-.35C6.84 7.28 6.31 8 5.91 8.81L3.34 7zm0 10l2.57-1.81c.4.81.93 1.53 1.59 2.16L3.34 17zM12 22l-2.39-3.42c.74.27 1.55.42 2.39.42.84 0 1.65-.15 2.39-.42L12 22zm8.66-5l-4.16.35c.66-.63 1.19-1.35 1.59-2.16L20.66 17zm0-10l-2.57 1.81c-.4-.81-.93-1.53-1.59-2.16L20.66 7z"/></svg>`
              : html`<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M12 3a9 9 0 109 9c0-.46-.04-.92-.1-1.36a5.39 5.39 0 01-4.4 2.26 5.4 5.4 0 01-5.4-5.4c0-1.81.89-3.42 2.26-4.4-.44-.06-.9-.1-1.36-.1z"/></svg>`}
            <span class="flex-1">Modo escuro</span>
            <span class="w-9 h-5 rounded-full transition-colors relative shrink-0 ${dark ? 'bg-wa-teal' : 'bg-wa-border'}">
              <span class="absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-all ${dark ? 'left-[18px]' : 'left-0.5'}"></span>
            </span>
          </button>

          ${(hasPassword || currentUser) ? html`
            <div class="border-t border-wa-border my-1"></div>
            ${currentUser ? html`
              <div class="px-4 py-1.5">
                <div class="text-[13px] text-wa-text font-medium truncate">${currentUser.name || currentUser.email}</div>
                ${currentUser.name ? html`<div class="text-[11px] text-wa-secondary truncate">${currentUser.email}</div>` : null}
              </div>
              <button
                onClick=${() => { onChangePassword && onChangePassword(); close(); }}
                class="w-full text-left px-4 py-2.5 text-[14px] hover:bg-wa-hover transition-colors flex items-center gap-2 text-wa-text"
              >
                <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z"/></svg>
                Trocar minha senha
              </button>
            ` : null}
            <button
              onClick=${() => { onLogout(); close(); }}
              class="w-full text-left px-4 py-2.5 text-[14px] hover:bg-red-50 transition-colors flex items-center gap-2 text-red-600"
            >
              <svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M17 7l-1.41 1.41L18.17 11H8v2h10.17l-2.58 2.58L17 17l5-5zM4 5h8V3H4c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h8v-2H4V5z"/></svg>
              Sair
            </button>
          ` : null}
        </div>
      ` : null}
    </div>
  `;
}

export function PageHeader({ title, onBack }) {
  return html`
    <div class="flex items-center gap-3 mb-4">
      <button
        onClick=${onBack}
        class="w-[36px] h-[36px] flex items-center justify-center rounded-full hover:bg-wa-hover transition-colors"
      >
        <svg viewBox="0 0 24 24" width="22" height="22" fill="#54656f">
          <path d="M12 4l1.4 1.4L7.8 11H20v2H7.8l5.6 5.6L12 20l-8-8 8-8z"/>
        </svg>
      </button>
      <h1 class="text-[20px] font-medium text-wa-text">${title}</h1>
    </div>
  `;
}
