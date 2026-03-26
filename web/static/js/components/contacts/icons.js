import { h } from 'preact';
import htm from 'htm';

const html = htm.bind(h);

// ── SVG Icons (WhatsApp Web exact style) ─────────────────────────

export function SearchIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="20" height="20" class="shrink-0">
      <path fill="#54656f" d="M15.009 13.805h-.636l-.22-.219a5.184 5.184 0 001.257-3.386 5.207 5.207 0 10-5.207 5.208 5.183 5.183 0 003.385-1.258l.22.22v.635l4.004 3.999 1.194-1.195-3.997-4.004zm-4.806 0a3.6 3.6 0 110-7.202 3.6 3.6 0 010 7.202z"/>
    </svg>
  `;
}

export function SendIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor" class="shrink-0">
      <path d="M1.101 21.757L23.8 12.028 1.101 2.3l-.01 7.51 16.29 2.218-16.29 2.218.01 7.51z"/>
    </svg>
  `;
}

export function BackArrowIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="24" height="24" fill="#54656f">
      <path d="M12 4l1.4 1.4L7.8 11H20v2H7.8l5.6 5.6L12 20l-8-8 8-8z"/>
    </svg>
  `;
}

export function DefaultAvatar({ size = 49 }) {
  return html`
    <svg viewBox="0 0 212 212" width="${size}" height="${size}">
      <path fill="#DFE5E7" d="M106.251.5C164.653.5 212 47.846 212 106.25S164.653 212 106.25 212C47.846 212 .5 164.654.5 106.25S47.846.5 106.251.5z"/>
      <path fill="#FFF" d="M173.561 171.615a62.767 62.767 0 00-16.06-22.06 62.91 62.91 0 00-22.794-14.132 17.694 17.694 0 001.883-1.467c7.87-7.168 12.762-17.434 12.762-28.812s-4.893-21.644-12.762-28.812c-7.869-7.168-18.753-11.597-30.84-11.597s-22.971 4.43-30.84 11.597c-7.87 7.168-12.762 17.434-12.762 28.812s4.892 21.644 12.762 28.812a17.71 17.71 0 001.883 1.467 62.91 62.91 0 00-22.794 14.131 62.769 62.769 0 00-16.06 22.06A105.752 105.752 0 01.5 106.25C.5 47.846 47.846.5 106.251.5S212 47.846 212 106.25a105.754 105.754 0 01-38.439 65.365z"/>
    </svg>
  `;
}

export function GroupAvatar({ size = 49 }) {
  return html`
    <svg viewBox="0 0 212 212" width="${size}" height="${size}">
      <path fill="#DFE5E7" d="M106.251.5C164.653.5 212 47.846 212 106.25S164.653 212 106.25 212C47.846 212 .5 164.654.5 106.25S47.846.5 106.251.5z"/>
      <path fill="#FFF" d="M82 108c-8.284 0-15-6.716-15-15s6.716-15 15-15 15 6.716 15 15-6.716 15-15 15zm48 0c-8.284 0-15-6.716-15-15s6.716-15 15-15 15 6.716 15 15-6.716 15-15 15zM82 118c-13.255 0-25 8.745-25 22v5h50v-5c0-13.255-11.745-22-25-22zm48 0c-2.08 0-4.08.254-6 .72 5.268 4.75 8.5 11.568 8.5 19.28v5h22.5v-5c0-13.255-11.745-20-25-20z"/>
    </svg>
  `;
}

export function EmojiIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="26" height="26" fill="#54656f" class="shrink-0">
      <path d="M9.153 11.603c.795 0 1.439-.879 1.439-1.962s-.644-1.962-1.439-1.962-1.439.879-1.439 1.962.644 1.962 1.439 1.962zm5.694 0c.795 0 1.439-.879 1.439-1.962s-.644-1.962-1.439-1.962-1.439.879-1.439 1.962.644 1.962 1.439 1.962zM12 2C6.486 2 2 6.486 2 12s4.486 10 10 10 10-4.486 10-10S17.514 2 12 2zm0 18c-4.411 0-8-3.589-8-8s3.589-8 8-8 8 3.589 8 8-3.589 8-8 8zm-.002-3.299a5.078 5.078 0 01-4.759-3.294h1.628a3.498 3.498 0 003.13 1.935 3.498 3.498 0 003.131-1.935h1.628a5.078 5.078 0 01-4.758 3.294z"/>
    </svg>
  `;
}

export function AttachIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="24" height="24" fill="#54656f" class="shrink-0">
      <path d="M1.816 15.556v.002c0 1.502.584 2.912 1.646 3.972s2.472 1.647 3.974 1.647a5.58 5.58 0 003.972-1.645l9.547-9.548c.769-.768 1.147-1.767 1.058-2.817-.079-.968-.548-1.927-1.319-2.698-1.594-1.592-4.068-1.711-5.517-.262l-7.916 7.915c-.881.881-.792 2.25.214 3.261.501.501 1.134.787 1.735.787.464 0 .882-.182 1.213-.509l5.511-5.512a.75.75 0 10-1.063-1.06l-5.509 5.509c-.093.093-.186.104-.241.104-.181 0-.477-.177-.717-.42-.488-.487-.574-1.049-.214-1.41l7.916-7.915c.899-.898 2.632-.832 3.857.393.579.578.897 1.248.947 1.888.052.654-.219 1.303-.762 1.846l-9.547 9.548a4.08 4.08 0 01-2.913 1.205 4.08 4.08 0 01-2.913-1.205 4.08 4.08 0 01-1.205-2.911 4.08 4.08 0 011.205-2.913l8.097-8.098a.75.75 0 10-1.063-1.06L3.463 11.59A5.58 5.58 0 001.816 15.556z"/>
    </svg>
  `;
}

export function MicIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="24" height="24" fill="#54656f" class="shrink-0">
      <path d="M11.999 14.942c2.001 0 3.531-1.53 3.531-3.531V4.35c0-2.001-1.53-3.531-3.531-3.531S8.469 2.35 8.469 4.35v7.061c0 2.001 1.53 3.531 3.53 3.531zm6.238-3.53c0 3.531-2.942 6.002-6.237 6.002s-6.237-2.471-6.237-6.002H4.761c0 3.885 3.009 7.06 6.737 7.533v3.236h1.004v-3.236c3.728-.472 6.737-3.648 6.737-7.533h-1.002z"/>
    </svg>
  `;
}

export function DoubleCheckIcon() {
  return html`
    <svg viewBox="0 0 16 11" width="16" height="11" class="inline-block mr-[3px] align-middle shrink-0">
      <path d="M11.071.653a.457.457 0 00-.304-.102.493.493 0 00-.381.178l-6.19 7.636-2.011-2.095a.463.463 0 00-.336-.153.48.48 0 00-.347.143.45.45 0 00-.14.337c0 .122.052.24.143.343l2.304 2.394c.096.099.218.153.35.153.132 0 .255-.058.348-.161L11.1 1.308a.452.452 0 00.109-.296.452.452 0 00-.138-.36z" fill="#53bdeb"/>
      <path d="M14.925.653a.457.457 0 00-.304-.102.493.493 0 00-.381.178l-6.19 7.636-1.006-1.048-.352.388.988 1.027c.096.099.218.153.35.153.132 0 .255-.058.348-.161l7.572-8.327a.452.452 0 00.109-.296.452.452 0 00-.134-.448z" fill="#53bdeb"/>
    </svg>
  `;
}

export function ClockIcon() {
  return html`
    <svg viewBox="0 0 16 15" width="14" height="14" class="inline-block mr-[3px] align-middle shrink-0">
      <path d="M9.75 7.713H8.244V5.359a.5.5 0 00-.5-.5H7.65a.5.5 0 00-.5.5v2.947a.5.5 0 00.5.5h2.1a.5.5 0 00.5-.5v-.094a.5.5 0 00-.5-.5zm-1.2-5.783A5.545 5.545 0 003 7.475a5.545 5.545 0 005.55 5.546 5.545 5.545 0 005.55-5.546A5.545 5.545 0 008.55 1.93z" fill="#92a58c"/>
    </svg>
  `;
}

export function FailedIcon() {
  return html`
    <svg viewBox="0 0 16 16" width="14" height="14" class="inline-block mr-[3px] align-middle shrink-0">
      <path d="M8 1.5a6.5 6.5 0 110 13 6.5 6.5 0 010-13zM7.25 5v4.5h1.5V5h-1.5zm0 6v1.5h1.5V11h-1.5z" fill="#e53e3e"/>
    </svg>
  `;
}

export function RetryIcon({ onClick }) {
  return html`
    <button onClick=${onClick} class="ml-[6px] inline-flex items-center align-middle opacity-70 hover:opacity-100 transition-opacity" title="Reenviar mensagem" style="background:none;border:none;cursor:pointer;padding:2px;">
      <svg viewBox="0 0 24 24" width="14" height="14" fill="#e53e3e">
        <path d="M17.65 6.35A7.958 7.958 0 0012 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0112 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z"/>
      </svg>
    </button>
  `;
}

export function StopIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="24" height="24" fill="#ef4444" class="shrink-0">
      <rect x="6" y="6" width="12" height="12" rx="2" />
    </svg>
  `;
}

// ── SVG Icons for Info Panel ──────────────────────────────────────

export function CloseIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="24" height="24" fill="currentColor">
      <path d="M19.6471 4.34705L4.34705 19.647M4.34705 4.34705L19.6471 19.647" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/>
    </svg>
  `;
}

export function PencilIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="18" height="18" fill="#8696a0">
      <path d="M3.95 16.7v3.4h3.4l9.8-9.8-3.4-3.4-9.8 9.8zm15.8-9.1c.4-.4.4-.9 0-1.3l-2.1-2.1c-.4-.4-.9-.4-1.3 0l-1.6 1.6 3.4 3.4 1.6-1.6z"/>
    </svg>
  `;
}

export function TrashIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="16" height="16" fill="#8696a0">
      <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/>
    </svg>
  `;
}

export function PlusIcon() {
  return html`
    <svg viewBox="0 0 24 24" width="18" height="18" fill="#00a884">
      <path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6v2z"/>
    </svg>
  `;
}
