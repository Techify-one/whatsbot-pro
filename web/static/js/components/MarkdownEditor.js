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

export function MarkdownEditor({ value, onChange, placeholder, height = '360px' }) {
  const elRef = useRef(null);
  const editorRef = useRef(null);
  // Keep the latest onChange without re-creating the editor on every render.
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  // Self-contained undo/redo history of markdown snapshots. `idx` points at the
  // snapshot currently shown; everything before it can be undone, everything
  // after redone. `suppress` guards programmatic setMarkdown from re-recording.
  const histRef = useRef({ stack: [value || ''], idx: 0, suppress: false, timer: null });
  const undoBtnRef = useRef(null);
  const redoBtnRef = useRef(null);

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
        [
          { name: 'undo', tooltip: 'Desfazer', el: undoBtn },
          { name: 'redo', tooltip: 'Refazer', el: redoBtn },
        ],
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
    if ((value || '') !== editor.getMarkdown()) {
      const h = histRef.current;
      if (h.timer) { clearTimeout(h.timer); h.timer = null; }
      h.suppress = true;
      editor.setMarkdown(value || '', false); // false = keep cursor, don't scroll
      h.suppress = false;
      if ((value || '') !== h.stack[h.idx]) {
        h.stack = h.stack.slice(0, h.idx + 1);
        h.stack.push(value || '');
        h.idx = h.stack.length - 1;
      }
      refreshButtons();
    }
  }, [value]);

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

  return html`<div class="wa-md-editor" ref=${elRef}></div>`;
}

export default MarkdownEditor;
