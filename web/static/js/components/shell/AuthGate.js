// App-shell AuthGate (Plano 23 · D4), extracted verbatim from app.js. The
// password/login gate + RBAC bootstrap. Behavior preserved EXACTLY:
//   • 'checking' → checkAuth(); while NO users exist (has_users === false) force
//     the first-admin bootstrap even over a legacy/open session.
//   • authenticated → enrich with permissions[] (getMe) for GearMenu gating (FF1).
//   • the `whatsbot:unauthorized` window event drops back to the login screen.
//   • the stored user is kept in localStorage so the gear menu can show who's in.
// The /wizard exemption + WS auth live elsewhere (App handles the wizard URL; the
// WS bus reads the token from localStorage) and are untouched here.
import { h } from 'preact';
import { useState, useEffect } from 'preact/hooks';
import htm from 'htm';
import { LoginScreen } from '../LoginScreen.js';
import { checkAuth, logoutSession, getMe } from '../../services/api.js';
import { App } from './App.js';

const html = htm.bind(h);

function loadStoredUser() {
  try {
    const raw = localStorage.getItem('whatsbot_user');
    return raw ? JSON.parse(raw) : null;
  } catch (e) { return null; }
}

export function AuthGate() {
  const [authState, setAuthState] = useState('checking'); // 'checking' | 'login' | 'ready'
  const [hasPassword, setHasPassword] = useState(false);
  const [needsBootstrap, setNeedsBootstrap] = useState(false);
  const [currentUser, setCurrentUser] = useState(loadStoredUser);

  // Enrich the logged-in user with permissions[] (checkAuth/login don't carry
  // them). Drives the GearMenu gating (FF1). Best-effort: failure leaves the
  // user without perms → helpers default to permissive, so nothing breaks.
  function refreshPermissions() {
    getMe().then(res => {
      if (res && res.ok && res.data && res.data.user) {
        setCurrentUser(res.data.user);
        try { localStorage.setItem('whatsbot_user', JSON.stringify(res.data.user)); } catch (e) {}
      }
    }).catch(() => {});
  }

  useEffect(() => {
    checkAuth().then(res => {
      // Migration to multi-user (plano 03/10): while NO users exist, force the
      // first-admin bootstrap — even over a legacy/open session. The panel only
      // becomes reachable after an admin (email + senha) is created.
      const hasUsers = res && res.data && res.data.has_users;
      if (hasUsers === false) {
        setHasPassword(!!(res.data && res.data.has_password));
        setNeedsBootstrap(true);
        setCurrentUser(null);
        try { localStorage.removeItem('whatsbot_user'); } catch (e) {}
        setAuthState('login');
        return;
      }
      if (res.ok) {
        setHasPassword(res.data.has_password);
        // The backend echoes the authenticated user (RBAC) on the check; keep
        // it so the gear menu can show who's logged in.
        if (res.data.user) {
          setCurrentUser(res.data.user);
          try { localStorage.setItem('whatsbot_user', JSON.stringify(res.data.user)); } catch (e) {}
          refreshPermissions();  // fetch permissions[] for menu gating
        }
        setAuthState('ready');
      } else {
        // Not authenticated and users exist → email + senha login (no bootstrap).
        setHasPassword(res.data && res.data.has_password !== undefined ? res.data.has_password : true);
        setNeedsBootstrap(false);
        setAuthState('login');
      }
    }).catch(() => {
      setAuthState('ready');
    });
  }, []);

  useEffect(() => {
    function onUnauthorized() {
      setHasPassword(true);
      setCurrentUser(null);
      try { localStorage.removeItem('whatsbot_user'); } catch (e) {}
      setAuthState('login');
    }
    window.addEventListener('whatsbot:unauthorized', onUnauthorized);
    return () => window.removeEventListener('whatsbot:unauthorized', onUnauthorized);
  }, []);

  function handleLogin(user) {
    setCurrentUser(user || null);
    setAuthState('ready');
    setHasPassword(true);
    if (user) refreshPermissions();  // login payload may omit permissions[]
  }

  async function handleLogout() {
    try { await logoutSession(); } catch (e) { /* best-effort */ }
    localStorage.removeItem('whatsbot_token');
    try { localStorage.removeItem('whatsbot_user'); } catch (e) {}
    setCurrentUser(null);
    setAuthState('login');
  }

  if (authState === 'checking') {
    return html`
      <div class="h-screen flex items-center justify-center">
        <div class="text-center text-wa-secondary animate-pulse-slow">Carregando...</div>
      </div>
    `;
  }

  if (authState === 'login') {
    return html`<${LoginScreen} onLogin=${handleLogin} needsBootstrap=${needsBootstrap} />`;
  }

  return html`<${App} onLogout=${handleLogout} hasPassword=${hasPassword} currentUser=${currentUser} />`;
}
