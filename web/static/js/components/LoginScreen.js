import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';
import { login, bootstrapAdmin } from '../services/api.js';

const html = htm.bind(h);

// Login screen. Supports three flows that coexist:
//  - Legacy single-password login: leave the email blank, type the panel
//    password. Sends {password}.
//  - RBAC user login: fill in the email + password. Sends {email, password}.
//  - First-access bootstrap: when there are no users yet (needsBootstrap),
//    creates the first admin via /api/auth/bootstrap, then logs in.
export function LoginScreen({ onLogin, needsBootstrap = false }) {
  const [bootstrap, setBootstrap] = useState(!!needsBootstrap);
  const [email, setEmail] = useState('');
  const [name, setName] = useState('Admin');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  function persistAndEnter(res) {
    localStorage.setItem('whatsbot_token', res.data.token);
    if (res.data.user) {
      try { localStorage.setItem('whatsbot_user', JSON.stringify(res.data.user)); } catch (e) {}
    } else {
      try { localStorage.removeItem('whatsbot_user'); } catch (e) {}
    }
    onLogin(res.data.user || null);
  }

  async function handleLogin(e) {
    e.preventDefault();
    if (!email.trim() || !password.trim()) return;
    setLoading(true);
    setError('');
    try {
      const res = await login(password, email);
      if (res.ok) {
        persistAndEnter(res);
      } else {
        setError(res.error || 'Email ou senha incorretos.');
      }
    } catch {
      setError('Erro de conexão.');
    }
    setLoading(false);
  }

  async function handleBootstrap(e) {
    e.preventDefault();
    if (!email.trim() || password.length < 8) return;
    setLoading(true);
    setError('');
    try {
      const res = await bootstrapAdmin({ email: email.trim().toLowerCase(), name: name.trim(), password });
      if (!res.ok) {
        setError(res.error || 'Falha ao criar o administrador.');
        setLoading(false);
        return;
      }
      // Bootstrap succeeded — log in immediately with the new credentials.
      const loginRes = await login(password, email);
      if (loginRes.ok) {
        persistAndEnter(loginRes);
      } else {
        setError('Administrador criado. Faça login para continuar.');
        setBootstrap(false);
      }
    } catch {
      setError('Erro de conexão.');
    }
    setLoading(false);
  }

  const canBootstrap = email.trim() && password.length >= 8;

  return html`
    <div class="h-screen bg-wa-panel flex items-center justify-center">
      <div class="bg-wa-bg rounded-xl shadow-lg border border-wa-border p-8 w-full max-w-sm">
        <div class="text-center mb-6">
          <div class="w-16 h-16 mx-auto mb-3 bg-wa-teal rounded-full flex items-center justify-center">
            <svg viewBox="0 0 24 24" width="32" height="32" fill="white">
              <path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z"/>
            </svg>
          </div>
          <h1 class="text-xl font-semibold text-wa-text">WhatsBot</h1>
          <p class="text-sm text-wa-secondary mt-1">
            ${bootstrap
              ? 'Crie o administrador para começar'
              : 'Entre com seu acesso ao painel'}
          </p>
        </div>

        ${bootstrap ? html`
          <form onSubmit=${handleBootstrap}>
            <input
              type="email"
              value=${email}
              onInput=${(e) => setEmail(e.target.value)}
              placeholder="Email"
              autofocus
              class="wa-field w-full px-4 py-3 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none mb-3"
            />
            <input
              type="text"
              value=${name}
              onInput=${(e) => setName(e.target.value)}
              placeholder="Nome (opcional)"
              class="wa-field w-full px-4 py-3 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none mb-3"
            />
            <input
              type="password"
              value=${password}
              onInput=${(e) => setPassword(e.target.value)}
              placeholder="Senha (mín. 8 caracteres)"
              class="wa-field w-full px-4 py-3 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none mb-1"
            />
            ${password && password.length < 8 ? html`
              <p class="text-wa-secondary text-xs mb-2">A senha deve ter ao menos 8 caracteres.</p>
            ` : html`<div class="mb-2"></div>`}

            ${error ? html`<p class="text-red-500 text-xs mb-3">${error}</p>` : null}

            <button
              type="submit"
              disabled=${loading || !canBootstrap}
              class="w-full py-3 bg-wa-teal hover:bg-wa-tealDark disabled:opacity-50 text-white font-medium rounded-lg transition-colors"
            >
              ${loading ? 'Criando...' : 'Criar administrador'}
            </button>
          </form>
        ` : html`
          <form onSubmit=${handleLogin}>
            <input
              type="email"
              value=${email}
              onInput=${(e) => setEmail(e.target.value)}
              placeholder="Email"
              autofocus
              class="wa-field w-full px-4 py-3 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none mb-3"
            />
            <input
              type="password"
              value=${password}
              onInput=${(e) => setPassword(e.target.value)}
              placeholder="Senha"
              class="wa-field w-full px-4 py-3 rounded-lg text-sm border border-wa-border focus:border-wa-teal focus:outline-none mb-3"
            />

            ${error ? html`<p class="text-red-500 text-xs mb-3">${error}</p>` : null}

            <button
              type="submit"
              disabled=${loading || !email.trim() || !password.trim()}
              class="w-full py-3 bg-wa-teal hover:bg-wa-tealDark disabled:opacity-50 text-white font-medium rounded-lg transition-colors"
            >
              ${loading ? 'Entrando...' : 'Entrar'}
            </button>
          </form>
        `}
      </div>
    </div>
  `;
}
