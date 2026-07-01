// Copiar deep-link (Plano 24) — o util compartilhado que transforma um path
// interno ("/conversations/12?message=5") num link absoluto copiável e um botão
// reutilizável "copiar link". Reusa `copyToClipboard` (que já funciona em
// contexto inseguro/HTTP via execCommand) e o `LinkIcon` do menu de mensagem.
import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';
import { copyToClipboard, LinkIcon } from '../components/contacts/MessageContextMenu.js';

const html = htm.bind(h);

// Absolute shareable URL for an in-app path. Kept tiny so message permalink and
// every "copiar link" button build the origin the same way.
export function deepLinkUrl(path) {
  const origin = (typeof window !== 'undefined' && window.location) ? window.location.origin : '';
  return `${origin}${path}`;
}

// Copy the absolute deep-link for `path` to the clipboard. Returns the URL.
export function copyDeepLink(path) {
  const url = deepLinkUrl(path);
  if (url) copyToClipboard(url);
  return url;
}

// Reusable "copiar link" button. Pass the in-app `path`; shows a ✓ for 2s after
// copy. `variant='icon'` (default) renders just the link icon; `variant='text'`
// renders an icon + label. Stops propagation so it works inside clickable rows.
export function CopyLinkButton({ path, title = 'Copiar link', label = 'Copiar link', variant = 'icon', class: cls = '' }) {
  const [copied, setCopied] = useState(false);
  function onClick(e) {
    e.preventDefault();
    e.stopPropagation();
    if (!path) return;
    copyDeepLink(path);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }
  if (variant === 'text') {
    return html`
      <button type="button" title=${title} onClick=${onClick}
        class=${`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-[13px] text-wa-text border border-wa-border hover:bg-wa-hover transition-colors ${cls}`}>
        ${copied ? '✓' : LinkIcon}<span>${copied ? 'Link copiado' : label}</span>
      </button>`;
  }
  return html`
    <button type="button" title=${copied ? 'Link copiado' : title} onClick=${onClick}
      class=${`inline-flex items-center justify-center p-1.5 rounded-md text-wa-secondary hover:text-wa-text hover:bg-wa-hover transition-colors ${cls}`}>
      ${copied ? html`<span class="text-[13px] text-wa-teal">✓</span>` : LinkIcon}
    </button>`;
}
