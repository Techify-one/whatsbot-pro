// MarkdownEditor — Preact wrapper around Toast UI Editor in WYSIWYG mode.
//
// The user edits formatted text (Notion/Word-style) and never has to write
// markdown syntax by hand: a toolbar + shortcuts apply the formatting and the
// editor serializes back to clean markdown via getMarkdown(). It is a drop-in
// replacement for a markdown <textarea> — `value` and `onChange` both speak
// markdown strings, so what gets stored is unchanged.
//
// The editor keeps its built-in "WYSIWYG | Markdown" mode tabs, so a power user
// can still flip to raw markdown when they need to.
//
// Dark mode: Toast UI ships its dark variant as the `.toastui-editor-dark` class
// on the root `.toastui-editor-defaultUI` element (both base + dark sheets are
// loaded in index.html). We follow the app's `<html>.dark` live with a
// MutationObserver and toggle that class — no editor recreation.
//
// Graceful degradation: if the vendored UMD bundle is missing for any reason we
// fall back to a plain markdown textarea so the form still works.

import { h } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import htm from 'htm';

const html = htm.bind(h);

function isDark() {
  return document.documentElement.classList.contains('dark');
}

// Heuristic: does this pasted plain text carry markdown markup worth rendering?
// Covers headings, bold/italic/strike, lists, quotes, links, code, hr and tables.
function looksLikeMarkdown(t) {
  if (!t) return false;
  return /(^|\n)\s{0,3}#{1,6}\s/.test(t)          // # heading
    || /\*\*[^*\n]+\*\*|__[^_\n]+__/.test(t)        // **bold**
    || /(^|\n)\s{0,3}[-*+]\s+\S/.test(t)            // - list
    || /(^|\n)\s{0,3}\d+\.\s+\S/.test(t)            // 1. list
    || /(^|\n)\s{0,3}>\s/.test(t)                   // > quote
    || /\[[^\]\n]+\]\([^)\n]+\)/.test(t)            // [link](url)
    || /`[^`\n]+`|```/.test(t)                      // `code` / ```
    || /(^|\n)\s*---+\s*(\n|$)|(^|\n)\s*\*\*\*+\s*(\n|$)/.test(t)  // hr
    || /(^|\n)\s*\|.+\|/.test(t);                   // | table |
}

// Render a markdown string to HTML using a throwaway Toast UI Viewer, so pasted
// markdown becomes formatted content (no raw symbols) in the WYSIWYG editor.
// Returns '' if conversion is unavailable, so the caller can fall back to raw text.
function markdownToHtml(md) {
  const factory = window.toastui && window.toastui.Editor && window.toastui.Editor.factory;
  if (!factory) return '';
  const holder = document.createElement('div');
  holder.style.cssText = 'position:absolute;left:-99999px;top:0;width:1px;height:1px;overflow:hidden';
  document.body.appendChild(holder);
  let out = '';
  try {
    const viewer = factory({ el: holder, viewer: true, initialValue: md, usageStatistics: false });
    const contents = holder.querySelector('.toastui-editor-contents');
    out = contents ? contents.innerHTML : '';
    try { viewer.destroy(); } catch (e) { /* noop */ }
  } catch (e) {
    out = '';
  } finally {
    holder.remove();
  }
  return out;
}

// Inline SVG icons for the custom undo/redo toolbar buttons. `currentColor` makes
// them follow the toolbar text color, so light/dark themes work without changes.
const UNDO_SVG = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>';
const REDO_SVG = '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>';
// Maximize icon: opens the editor in a full-screen modal for comfortable editing.
const ZOOM_SVG = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>';
// Chevron used by the expand/collapse arrow in the bottom bar (rotates when tall).
const CHEVRON_SVG = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>';

// Group rapid keystrokes into a single undoable snapshot (ms of inactivity).
const SNAPSHOT_DELAY = 500;

function makeToolbarButton(svg, tooltip) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'toastui-editor-toolbar-icons wa-md-history-btn';
  btn.setAttribute('aria-label', tooltip);
  btn.innerHTML = svg;
  return btn;
}

export function MarkdownEditor({ value, onChange, placeholder, height = '360px', tallHeight = '640px', nested = false }) {
  const elRef = useRef(null);
  const editorRef = useRef(null);
  // Keep the latest onChange without re-creating the editor on every render.
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  // `tall` = inline height toggle (the ⌄ arrow in the bottom bar expands the box
  // so the whole prompt is visible without scrolling). `expanded` = the full-screen
  // modal (the ⤢ zoom button). Both are hidden on the `nested` editor that renders
  // INSIDE the modal, so it can't recursively open another modal.
  const [tall, setTall] = useState(false);
  const [expanded, setExpanded] = useState(false);
  // Imperative Toast-UI toolbar buttons flip these React states via refs.
  const setTallRef = useRef(setTall); setTallRef.current = setTall;
  const setExpandedRef = useRef(setExpanded); setExpandedRef.current = setExpanded;
  const arrowBtnRef = useRef(null);

  // Self-contained undo/redo history of markdown snapshots. `idx` points at the
  // snapshot currently shown; everything before it can be undone, everything
  // after redone. `suppress` guards programmatic setMarkdown from re-recording.
  const histRef = useRef({ stack: [value || ''], idx: 0, suppress: false, timer: null });
  const undoBtnRef = useRef(null);
  const redoBtnRef = useRef(null);
  // The last markdown THIS editor emitted upward. When the controlled `value` prop
  // comes back equal to it, that's our own echo — re-applying it via setMarkdown()
  // would rebuild the document and jump the scroll to the top on every keystroke.
  // (Two editors share one `value`, e.g. inline + full-screen modal; each must
  // still receive genuine external changes and edits from the OTHER editor.)
  const lastEmittedRef = useRef(value || '');

  const Editor = (typeof window !== 'undefined' && window.toastui && window.toastui.Editor) || null;
  const [unavailable] = useState(!Editor);

  // Reflect the history position onto the buttons' enabled/disabled state.
  const refreshButtons = () => {
    const h = histRef.current;
    const undoBtn = undoBtnRef.current;
    const redoBtn = redoBtnRef.current;
    if (undoBtn) undoBtn.disabled = h.idx <= 0;
    if (redoBtn) redoBtn.disabled = h.idx >= h.stack.length - 1;
  };

  // Fold the editor's current content into the history if it diverged from the
  // active snapshot (a pending, not-yet-debounced edit). Call before undo/redo.
  const commitPending = () => {
    const h = histRef.current;
    if (h.timer) { clearTimeout(h.timer); h.timer = null; }
    const editor = editorRef.current;
    if (!editor) return;
    const md = editor.getMarkdown();
    if (md === h.stack[h.idx]) return;
    h.stack = h.stack.slice(0, h.idx + 1);
    h.stack.push(md);
    h.idx = h.stack.length - 1;
  };

  // Move to the snapshot at `idx` and push it into the editor + parent form.
  const applySnapshot = (idx) => {
    const h = histRef.current;
    const editor = editorRef.current;
    if (!editor) return;
    h.idx = idx;
    const md = h.stack[idx];
    h.suppress = true;
    editor.setMarkdown(md, false); // false = keep cursor, don't scroll
    h.suppress = false;
    lastEmittedRef.current = md;
    if (onChangeRef.current) onChangeRef.current(md);
    refreshButtons();
  };

  const undo = () => {
    commitPending();
    const h = histRef.current;
    if (h.idx <= 0) { refreshButtons(); return; }
    applySnapshot(h.idx - 1);
  };

  const redo = () => {
    const h = histRef.current;
    if (h.idx >= h.stack.length - 1) { refreshButtons(); return; }
    applySnapshot(h.idx + 1);
  };

  // Create the editor once on mount (raw-textarea fallback skips this).
  useEffect(() => {
    if (!Editor || !elRef.current) return;

    const undoBtn = makeToolbarButton(UNDO_SVG, 'Desfazer');
    const redoBtn = makeToolbarButton(REDO_SVG, 'Refazer');
    undoBtn.addEventListener('click', undo);
    redoBtn.addEventListener('click', redo);
    undoBtnRef.current = undoBtn;
    redoBtnRef.current = redoBtn;

    // Zoom button at the far right of the toolbar → opens the full-screen modal.
    // Omitted on the nested (in-modal) editor so it can't reopen itself.
    const zoomBtn = nested ? null : makeToolbarButton(ZOOM_SVG, 'Abrir em tela cheia');
    if (zoomBtn) zoomBtn.addEventListener('click', () => setExpandedRef.current(true));

    const historyGroup = [
      { name: 'undo', tooltip: 'Desfazer', el: undoBtn },
      { name: 'redo', tooltip: 'Refazer', el: redoBtn },
    ];
    if (zoomBtn) historyGroup.push({ name: 'zoom', tooltip: 'Abrir em tela cheia', el: zoomBtn });

    const editor = new Editor({
      el: elRef.current,
      height,
      initialEditType: 'wysiwyg',
      // 'tab' (not 'vertical') so markdown mode is a SINGLE full-width pane of raw
      // text — no side-by-side preview. The Write/Preview tabs are hidden in CSS,
      // leaving just the editable markdown with all its markup.
      previewStyle: 'tab',
      initialValue: value || '',
      placeholder: placeholder || '',
      usageStatistics: false,
      autofocus: false,
      theme: isDark() ? 'dark' : 'default',
      toolbarItems: [
        ['heading', 'bold', 'italic', 'strike'],
        ['hr', 'quote'],
        ['ul', 'ol', 'task', 'indent', 'outdent'],
        ['table', 'link'],
        ['code', 'codeblock'],
        historyGroup,
      ],
    });
    editor.on('change', () => {
      const h = histRef.current;
      // Programmatic writes (value-sync effect, undo/redo) set `suppress`. They must
      // NOT push back into the parent form: doing so fights the controlled `value`
      // prop and, with markdown that doesn't round-trip stably (e.g. `---` next to a
      // heading), turns into an infinite setMarkdown↔onChange loop that freezes the
      // tab. Only genuine user edits (suppress=false) propagate up + record history.
      if (h.suppress) return;
      const md = editor.getMarkdown();
      lastEmittedRef.current = md;
      if (onChangeRef.current) onChangeRef.current(md);
      // Debounce so a burst of typing becomes one undo step, not one per key.
      if (h.timer) clearTimeout(h.timer);
      h.timer = setTimeout(() => {
        h.timer = null;
        const cur = editor.getMarkdown();
        if (cur === h.stack[h.idx]) return;
        h.stack = h.stack.slice(0, h.idx + 1);
        h.stack.push(cur);
        h.idx = h.stack.length - 1;
        refreshButtons();
      }, SNAPSHOT_DELAY);
    });
    editorRef.current = editor;
    // Float the undo/redo group to the far right edge of the toolbar.
    const histGroup = undoBtn.closest('.toastui-editor-toolbar-group');
    if (histGroup) histGroup.style.marginLeft = 'auto';

    // Replace the native two-tab mode switch with a single labeled toggle: in
    // WYSIWYG it reads "Markdown" (click → see the raw text with every markup);
    // in markdown it reads "Editor" (click → back to normal formatted editing).
    const switchBar = elRef.current.querySelector('.toastui-editor-mode-switch');
    if (switchBar) {
      // Expand/collapse arrow (left of the mode toggle): grows the inline box so
      // the whole prompt is visible without scrolling. Hidden on the nested editor
      // (the modal is already full height). Rotation follows the `tall` state via
      // the `tall` effect toggling `.is-tall` on this button.
      if (!nested) {
        const arrowBtn = document.createElement('button');
        arrowBtn.type = 'button';
        arrowBtn.className = 'wa-md-arrow-btn';
        arrowBtn.innerHTML = CHEVRON_SVG;
        arrowBtn.setAttribute('title', 'Expandir a caixa de texto');
        arrowBtn.setAttribute('aria-label', 'Expandir a caixa de texto');
        arrowBtn.addEventListener('click', () => setTallRef.current((v) => !v));
        arrowBtnRef.current = arrowBtn;
        switchBar.insertBefore(arrowBtn, switchBar.firstChild);
      }

      const toggleBtn = document.createElement('button');
      toggleBtn.type = 'button';
      toggleBtn.className = 'wa-md-mode-toggle';
      const syncLabel = () => {
        toggleBtn.textContent = editor.isMarkdownMode() ? 'Editor' : 'Markdown';
      };
      toggleBtn.addEventListener('click', () => {
        editor.changeMode(editor.isMarkdownMode() ? 'wysiwyg' : 'markdown', true);
        syncLabel();
      });
      syncLabel();
      switchBar.appendChild(toggleBtn);
    }
    refreshButtons();

    // Paste markdown as FORMATTED text (no raw symbols) in WYSIWYG. Toast UI pastes
    // plain text literally, so a copied markdown prompt would keep its `#`/`**`/`---`.
    // We detect markdown-looking plain text, convert it to HTML, and re-dispatch a
    // synthetic HTML paste so ProseMirror inserts it formatted at the cursor. Rich
    // HTML pastes and markdown mode are left untouched. `synthetic` guards the
    // re-dispatch from re-entering this same capture-phase handler.
    let synthetic = false;
    const onPaste = (e) => {
      if (synthetic) return;
      const ed = editorRef.current;
      if (!ed || ed.isMarkdownMode()) return; // markdown mode: raw paste is correct
      const cd = e.clipboardData;
      if (!cd) return;
      const htmlClip = cd.getData('text/html');
      if (htmlClip && htmlClip.trim()) return; // real HTML source → let Toast UI handle it
      const text = cd.getData('text/plain');
      if (!text || !looksLikeMarkdown(text)) return; // nothing markdown-ish → default paste
      const html = markdownToHtml(text);
      if (!html) return; // conversion unavailable → fall back to Toast UI's plain paste
      e.preventDefault();
      e.stopPropagation();
      const pmNode = elRef.current
        && elRef.current.querySelector('.toastui-editor-ww-container .ProseMirror, .toastui-editor-ww-container [contenteditable="true"]');
      if (!pmNode) { try { ed.insertText(text); } catch (err) { /* noop */ } return; }
      try {
        const dt = new DataTransfer();
        dt.setData('text/html', html);
        dt.setData('text/plain', text);
        synthetic = true;
        pmNode.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
      } catch (err) {
        try { ed.insertText(text); } catch (e2) { /* noop */ }
      } finally {
        synthetic = false;
      }
    };
    elRef.current.addEventListener('paste', onPaste, true); // capture: run before Toast UI

    return () => {
      const h = histRef.current;
      if (h.timer) { clearTimeout(h.timer); h.timer = null; }
      if (elRef.current) elRef.current.removeEventListener('paste', onPaste, true);
      try { editor.destroy(); } catch (e) { /* noop */ }
      editorRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Push external value changes (revert a version, form re-sync) into the editor.
  // Our own keystrokes already leave `value` equal to the editor content, so this
  // is a no-op for them — no feedback loop, no cursor jump. A genuine external
  // change is recorded as a fresh undoable snapshot.
  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    // The modal editor is fully uncontrolled after mount: it only pushes edits UP
    // (to the parent + the inline editor behind it) and NEVER takes value back in.
    // Any setMarkdown() here rebuilds the doc and jumps the scroll to the top on
    // every keystroke — especially in Markdown mode. It's the sole editing surface
    // while open, so it needs no external sync; it seeds from `initialValue` on mount.
    if (nested) return;
    // While the full-screen modal is open this (inline, hidden) editor is frozen:
    // it must NOT re-apply value changes. Otherwise, when the modal is in Markdown
    // mode, this WYSIWYG editor would receive the raw text, re-normalize it (adding
    // `\(` / `1\.` escapes) and emit that back — forcing a setMarkdown() on the modal
    // editor that scrolls it to the top and corrupts the text mid-typing. It re-syncs
    // when the modal closes (this effect re-runs because `expanded` is a dep).
    if (expanded) return;
    // Our own echo: the parent is just replaying what this editor emitted. Skip —
    // reapplying would rebuild the doc and scroll to the top mid-typing.
    if ((value || '') === lastEmittedRef.current) return;
    if ((value || '') !== editor.getMarkdown()) {
      const h = histRef.current;
      if (h.timer) { clearTimeout(h.timer); h.timer = null; }
      h.suppress = true;
      editor.setMarkdown(value || '', false); // false = keep cursor, don't scroll
      h.suppress = false;
      lastEmittedRef.current = value || '';
      if ((value || '') !== h.stack[h.idx]) {
        h.stack = h.stack.slice(0, h.idx + 1);
        h.stack.push(value || '');
        h.idx = h.stack.length - 1;
      }
      refreshButtons();
    }
  }, [value, expanded]);

  // Apply the inline height toggle: grow/shrink the editor and rotate the arrow.
  // Skipped while the modal is open (the box behind it doesn't need to resize) and
  // on the nested editor (fixed at 100% of the modal).
  useEffect(() => {
    const editor = editorRef.current;
    if (!editor || nested) return;
    try { editor.setHeight(expanded ? height : (tall ? tallHeight : height)); } catch (e) { /* noop */ }
    const arrow = arrowBtnRef.current;
    if (arrow) {
      arrow.classList.toggle('is-tall', tall);
      arrow.setAttribute('title', tall ? 'Recolher a caixa de texto' : 'Expandir a caixa de texto');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tall, expanded]);

  // Close the full-screen modal on Escape.
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e) => { if (e.key === 'Escape') setExpanded(false); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [expanded]);

  // Follow the app theme live by toggling the dark class on the editor root.
  useEffect(() => {
    if (!Editor) return;
    const apply = () => {
      const root = elRef.current && elRef.current.querySelector('.toastui-editor-defaultUI');
      if (root) root.classList.toggle('toastui-editor-dark', isDark());
    };
    apply();
    const obs = new MutationObserver(apply);
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    return () => obs.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (unavailable) {
    return html`<textarea
      class="wa-field w-full px-3 py-2 rounded-md text-[13px] font-mono resize-y" rows="12"
      placeholder=${placeholder || ''}
      value=${value || ''} onInput=${(e) => onChange && onChange(e.target.value)}></textarea>`;
  }

  return html`
    <div class="wa-md-editor ${nested ? 'wa-md-editor-nested' : ''}" ref=${elRef}></div>
    ${expanded ? html`
      <div class="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 p-2 sm:p-3"
        onClick=${(e) => { if (e.target === e.currentTarget) setExpanded(false); }}>
        <div class="bg-wa-bg rounded-2xl shadow-2xl w-full h-full flex flex-col overflow-hidden">
          <div class="flex items-center justify-between px-4 py-3 border-b border-wa-border shrink-0">
            <span class="text-[13px] font-medium text-wa-text">Editar prompt</span>
            <button type="button" title="Fechar (Esc)"
              class="text-wa-secondary hover:text-wa-text transition-colors p-1 rounded"
              onClick=${() => setExpanded(false)}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            </button>
          </div>
          <div class="flex-1 min-h-0 p-3">
            <${MarkdownEditor} value=${value} onChange=${onChange} placeholder=${placeholder} nested height="100%" />
          </div>
        </div>
      </div>
    ` : null}
  `;
}

export default MarkdownEditor;
