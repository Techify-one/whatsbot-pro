// Channels — small shared presentational pieces (Plano 23 · D4), extracted
// verbatim from ChannelsManager.js: the status Dot, the clipboard fallback, the
// post-create webhook notices (WhatsApp Cloud + Telegram), and the hard-delete
// confirmation modal. No behavior change.
import { h } from 'preact';
import { useState } from 'preact/hooks';
import htm from 'htm';
import { buildEmbedSnippet } from './constants.js';

const html = htm.bind(h);

export function Dot({ on }) {
  return html`<span class="inline-block w-2 h-2 rounded-full ${on ? 'bg-green-500' : 'bg-gray-400'}"></span>`;
}

// Copy `text` via the legacy execCommand path (navigator.clipboard is unavailable
// over plain HTTP / non-secure contexts, common when the panel is on a LAN IP).
export function fallbackCopyText(text, onOk) {
  try {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.focus(); ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    if (ok && onOk) onOk();
  } catch (e) { /* clipboard truly unavailable */ }
}

// ── Webhook-URL notice (post-create `webhook_url`, plano 33) ──
// Generic: the URL + title + help come from the provider descriptor's
// `post_create` block (resolved by ChannelsManager), so the core doesn't know
// which provider needs a callback URL — it just renders what it's handed.
export function WebhookNotice({ url, title, help, onDismiss }) {
  const [copied, setCopied] = useState(false);
  function flagCopied() {
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }
  function fallbackCopy() {
    // navigator.clipboard is unavailable over plain HTTP (non-secure context),
    // which is the common case here (panel served on a LAN IP). Fall back to
    // the legacy execCommand approach via a temporary textarea.
    try {
      const ta = document.createElement('textarea');
      ta.value = url;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      if (ok) flagCopied();
    } catch (e) { /* clipboard truly unavailable */ }
  }
  function copy() {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(flagCopied).catch(fallbackCopy);
    } else {
      fallbackCopy();
    }
  }
  return html`
    <div class="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-4">
      <div class="text-[14px] font-medium text-blue-700 mb-1">${title || 'Canal criado'}</div>
      <p class="text-[13px] text-wa-text mb-2">
        ${help || 'Cole esta URL como Callback URL na configuração de webhook do provider:'}
      </p>
      <div class="flex gap-2 items-center flex-wrap">
        <code class="flex-1 min-w-0 break-all px-3 py-2 rounded-md text-[13px] bg-wa-bg border border-wa-border text-wa-text">${url}</code>
        <button class="px-3 py-2 rounded-md text-[13px] text-wa-text border border-wa-border hover:bg-wa-hover transition-colors shrink-0"
          onClick=${copy}>${copied ? 'Copiado!' : 'Copiar'}</button>
      </div>
      <div class="flex justify-end mt-3">
        <button class="px-3 py-1.5 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
          onClick=${onDismiss}>Fechar</button>
      </div>
    </div>
  `;
}

// ── Autoconfigure post-create notice (post-create `autoconfigure`, plano 33) ──
// Shown after creating a channel whose provider self-configures its inbound
// delivery (e.g. Telegram: webhook if a public HTTPS domain exists, else
// long-poll). Generic on the autoconfigure RESULT the provider route returned:
// ``result`` = {mode, webhook_url, registered, reason}. The core doesn't know the
// provider — it renders whatever the route reported.
export function AutoconfigureNotice({ result, onDismiss }) {
  const url = (result && result.webhook_url) || '';
  const isWebhook = result && result.mode === 'webhook';
  const [copied, setCopied] = useState(false);
  function flagCopied() { setCopied(true); setTimeout(() => setCopied(false), 2000); }
  function fallbackCopy() {
    try {
      const ta = document.createElement('textarea');
      ta.value = url; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.focus(); ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      if (ok) flagCopied();
    } catch (e) { /* clipboard truly unavailable */ }
  }
  function copy() {
    if (!url) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(flagCopied).catch(fallbackCopy);
    } else { fallbackCopy(); }
  }
  // `message` (opcional) é o texto que o PRÓPRIO provider escreveu — quando vem,
  // manda nele; sem ele, cai no par histórico webhook/long-poll do Telegram.
  const message = (result && result.message) || '';
  // mode === 'manual' → o provider não conseguiu registrar sozinho e a URL precisa
  // ser colada à mão, então o bloco da URL aparece mesmo fora do caso webhook.
  const isManual = result && result.mode === 'manual';
  const tint = isWebhook ? 'bg-green-50 border-green-200' : 'bg-amber-50 border-amber-200';
  const titleColor = isWebhook ? 'text-green-700' : 'text-amber-700';
  return html`
    <div class=${`${tint} border rounded-lg p-4 mb-4`}>
      <div class=${`text-[14px] font-medium ${titleColor} mb-1`}>
        ${isWebhook ? '✓ Canal criado — webhook registrado automaticamente'
                    : (isManual ? 'Canal criado — falta registrar o webhook'
                                : 'Inbox criada — usando long-poll')}
      </div>
      <p class="text-[13px] text-wa-text mb-2">
        ${message
          ? html`${isManual && result.reason ? html`Não foi possível registrar automaticamente (${result.reason}). ` : ''}${message}`
          : (isWebhook
            ? html`O Telegram já está entregando as mensagens neste endereço. Guarde a URL caso precise reconfigurar:`
            : html`Não foi possível usar webhook${result && result.reason ? html` (${result.reason})` : ''} — a inbox recebe por <span class="font-medium">long-poll</span> e já está funcionando. Para usar webhook é necessário um domínio público (HTTPS).`)}
      </p>
      ${isWebhook || (isManual && url) ? html`
        <div class="flex gap-2 items-center flex-wrap">
          <code class="flex-1 min-w-0 break-all px-3 py-2 rounded-md text-[13px] bg-wa-bg border border-wa-border text-wa-text">${url || '—'}</code>
          <button class="px-3 py-2 rounded-md text-[13px] text-wa-text border border-wa-border hover:bg-wa-hover transition-colors shrink-0"
            onClick=${copy}>${copied ? 'Copiado!' : 'Copiar'}</button>
        </div>` : null}
      <div class="flex justify-end mt-3">
        <button class="px-3 py-1.5 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
          onClick=${onDismiss}>Fechar</button>
      </div>
    </div>
  `;
}

// ── Embed-snippet block (reusable, persistent) — plano 46 ──
// The <script> install snippet built from the channel's public `widget_token` + the
// panel base URL, with a copy button. Shown BOTH in the post-create notice and in
// the channel edit form (so the operator can always copy it again). No dismiss —
// this is the always-available block. `widgetToken` comes from the channel config;
// `template` (plano 76 · F3) é o `post_create.snippet_template` do descriptor do
// provider — o core só interpola. Sem template ⇒ nada renderiza.
export function EmbedSnippetBlock({ widgetToken, baseUrl, help, template }) {
  const [copied, setCopied] = useState(false);
  const b = (baseUrl || window.location.origin || '').replace(/\/+$/, '');
  const snippet = buildEmbedSnippet(b, widgetToken, template);
  if (!snippet) return null;
  function flagCopied() { setCopied(true); setTimeout(() => setCopied(false), 2000); }
  function copy() {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(snippet).then(flagCopied).catch(() => fallbackCopyText(snippet, flagCopied));
    } else fallbackCopyText(snippet, flagCopied);
  }
  return html`
    <div>
      ${help ? html`<p class="text-[13px] text-wa-text mb-2">${help}</p>` : null}
      <div class="flex gap-2 items-start flex-wrap">
        <code class="flex-1 min-w-0 break-all px-3 py-2 rounded-md text-[12px] bg-wa-bg border border-wa-border text-wa-text">${snippet}</code>
        <button class="px-3 py-2 rounded-md text-[13px] text-wa-text border border-wa-border hover:bg-wa-hover transition-colors shrink-0"
          onClick=${copy}>${copied ? 'Copiado!' : 'Copiar'}</button>
      </div>
    </div>
  `;
}

// ── Embed-snippet post-create notice (post-create `embed_snippet`, plano 46) ──
// Shown right after creating a widget channel. Wraps the reusable block + a dismiss.
export function EmbedSnippetNotice({ widgetToken, baseUrl, template, onDismiss }) {
  return html`
    <div class="bg-wa-teal/10 border border-wa-teal/30 rounded-lg p-4 mb-4">
      <div class="text-[14px] font-medium text-wa-teal mb-1">✓ Widget criado</div>
      <${EmbedSnippetBlock} widgetToken=${widgetToken} baseUrl=${baseUrl} template=${template}
        help=${html`Cole este trecho antes de <code>&lt;/body&gt;</code> no seu site. Você pode
          copiá-lo de novo depois em <span class="font-medium">Canais → Editar</span> ou em
          <span class="font-medium">Plugins → Widget de site → Configurar</span>.`} />
      <div class="flex justify-end mt-3">
        <button class="px-3 py-1.5 rounded-md text-[13px] text-wa-text hover:bg-wa-hover transition-colors"
          onClick=${onDismiss}>Fechar</button>
      </div>
    </div>
  `;
}

// Confirmation modal for a hard-delete (purge) of a channel. Hard-delete is
// irreversible: it removes the channel and its entire inbox/history.
export function PurgeChannelModal({ channel, onCancel, onConfirm }) {
  const name = channel.display_name || channel.id;
  return html`
    <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick=${onCancel}>
      <div class="bg-wa-panel border border-wa-border rounded-lg shadow-xl max-w-md w-full p-5"
        onClick=${(e) => e.stopPropagation()}>
        <h3 class="text-[16px] font-medium text-wa-text mb-2">Excluir canal</h3>
        <p class="text-[13px] text-wa-secondary mb-1">
          Tem certeza que deseja excluir o canal
          <span class="font-medium text-wa-text">"${name}"</span>?
        </p>
        <p class="text-[13px] text-red-600 mb-5">
          Esta ação é permanente e não pode ser desfeita: o canal e todo o
          histórico de conversas da inbox serão apagados.
        </p>
        <div class="flex justify-end gap-2">
          <button class="px-3 py-2 rounded-md text-[14px] text-wa-text hover:bg-wa-hover transition-colors"
            onClick=${onCancel}>Cancelar</button>
          <button class="px-3 py-2 rounded-md text-[14px] text-white bg-red-600 hover:bg-red-700 transition-colors"
            onClick=${onConfirm}>Excluir definitivamente</button>
        </div>
      </div>
    </div>
  `;
}
