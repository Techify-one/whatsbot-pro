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
      const md = editor.getMarkdown();
      if (onChangeRef.current) onChangeRef.current(md);
      const h = histRef.current;
      if (h.suppress) return; // our own undo/redo/value-sync, not a user edit
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
    return () => {
      const h = histRef.current;
      if (h.timer) { clearTimeout(h.timer); h.timer = null; }
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
