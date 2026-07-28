// Safe, self-contained Markdown renderer for the melhorias plugin chat.
//
// Subset covered (GFM-ish):
//   - Fenced code blocks ```lang ... ``` and inline code `code`
//   - Headings # .. ###### (h1..h6)
//   - Blockquotes >
//   - Horizontal rules --- / *** / ___
//   - Unordered lists - / * / + and ordered lists 1. (simple indentation nesting)
//   - Paragraphs (blank-line separated; soft line breaks join with <br>)
//   - Inline: **bold**/__bold__, *italic*/_italic_, ~~strike~~, [text](url)
//   - Pipe tables are intentionally NOT supported (deferred — see plano 55 F0/g).
//
// Security contract (D5) — escape-first, XSS-proof:
//   1. The ENTIRE source is HTML-escaped (& < > " ') BEFORE any parsing. Raw
//      HTML in the input can therefore never produce live markup: `<script>`
//      becomes `&lt;script&gt;`, an `onerror=` attribute never becomes real.
//   2. Only markup this renderer itself emits is real HTML. Code spans/blocks
//      are extracted to placeholders first and never re-parsed.
//   3. Link URLs are validated against a strict /^https?:\/\//i allow-list.
//      Anything else (javascript:, data:, relative, ...) renders as plain text
//      (the label only, no <a>). The URL is only ever interpolated into href
//      after passing that regex.
// Output is a ready-to-inject HTML string (already sanitized).

const CODE_BLOCK_MARK = "\x00CODEBLOCK";
const CODE_INLINE_MARK = "\x00CODEINLINE";
const MARK_END = "\x00";

// Strict allow-list: only absolute http(s) URLs may become links.
const SAFE_URL = /^https?:\/\//i;

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Apply inline formatting to already-escaped text (no code — code is a placeholder).
function renderInline(text) {
  let out = text;

  // Links: [label](url). Label may contain other inline markup (processed after).
  out = out.replace(/\[([^\]]*)\]\(([^)\s]+)\)/g, (m, label, url) => {
    // url comes from the escaped string; validate strictly before use.
    if (!SAFE_URL.test(url)) {
      return label; // unsafe scheme/relative → render label as plain text
    }
    return `<a href="${url}" target="_blank" rel="noopener noreferrer" class="text-wa-teal underline">${label}</a>`;
  });

  // Bold before italic so ** is not eaten by * rules.
  out = out.replace(/\*\*([^\s](?:.*?[^\s])?)\*\*/g, "<b>$1</b>");
  out = out.replace(/__([^\s](?:.*?[^\s])?)__/g, "<b>$1</b>");
  // Strikethrough.
  out = out.replace(/~~([^\s](?:.*?[^\s])?)~~/g, "<s>$1</s>");
  // Italic (single * / _), avoiding word-internal underscores.
  out = out.replace(/(^|[^\*])\*([^\s*](?:[^*]*?[^\s*])?)\*/g, "$1<i>$2</i>");
  out = out.replace(/(^|[^_\w])_([^\s_](?:[^_]*?[^\s_])?)_(?![\w])/g, "$1<i>$2</i>");

  return out;
}

// Count leading spaces (tabs count as 2) for simple list nesting.
function indentOf(line) {
  const m = line.match(/^[ \t]*/);
  let n = 0;
  for (const ch of m[0]) n += ch === "\t" ? 2 : 1;
  return n;
}

export function renderMarkdown(src) {
  if (!src) return "";

  // (a) Escape everything first — neutralizes all raw HTML.
  let text = escapeHtml(src);

  const codeBlocks = [];
  const codeInlines = [];

  // (b) Extract fenced code blocks (content already escaped, no further formatting).
  text = text.replace(/```[^\n]*\n([\s\S]*?)```/g, (m, body) => {
    // Drop a single trailing newline inside the fence.
    const content = body.replace(/\n$/, "");
    const idx = codeBlocks.length;
    codeBlocks.push(content);
    return `${CODE_BLOCK_MARK}${idx}${MARK_END}`;
  });

  // (c) Extract inline code before bold/italic so * inside code survives.
  text = text.replace(/`([^`\n]+)`/g, (m, body) => {
    const idx = codeInlines.length;
    codeInlines.push(body);
    return `${CODE_INLINE_MARK}${idx}${MARK_END}`;
  });

  // (d) Process line-based blocks.
  const lines = text.split("\n");
  const out = [];
  let paragraph = [];
  // list stack: each entry { type: 'ul'|'ol', indent }
  let listStack = [];

  function flushParagraph() {
    if (paragraph.length) {
      const joined = paragraph.map(renderInline).join("<br>");
      out.push(`<p class="my-1 text-wa-text">${joined}</p>`);
      paragraph = [];
    }
  }

  function closeListsTo(indent) {
    while (
      listStack.length &&
      listStack[listStack.length - 1].indent >= indent
    ) {
      const top = listStack.pop();
      out.push(top.type === "ol" ? "</ol>" : "</ul>");
    }
  }

  function closeAllLists() {
    while (listStack.length) {
      const top = listStack.pop();
      out.push(top.type === "ol" ? "</ol>" : "</ul>");
    }
  }

  for (const rawLine of lines) {
    const line = rawLine.replace(/\s+$/, "");

    // Blank line: end paragraph and lists.
    if (!line.trim()) {
      flushParagraph();
      closeAllLists();
      continue;
    }

    // Standalone fenced-code placeholder line.
    const blockMatch = line.match(
      new RegExp(`^\\s*${CODE_BLOCK_MARK.replace("\x00", "\\x00")}(\\d+)\\x00\\s*$`)
    );
    if (blockMatch) {
      flushParagraph();
      closeAllLists();
      out.push(line.trim());
      continue;
    }

    // Horizontal rule.
    if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      flushParagraph();
      closeAllLists();
      out.push('<hr class="border-wa-border my-2">');
      continue;
    }

    // Heading.
    const heading = line.match(/^\s*(#{1,6})\s+(.*)$/);
    if (heading) {
      flushParagraph();
      closeAllLists();
      const level = heading[1].length;
      const sizes = {
        1: "text-lg",
        2: "text-base",
        3: "text-sm",
        4: "text-sm",
        5: "text-xs",
        6: "text-xs",
      };
      out.push(
        `<h${level} class="font-semibold text-wa-text ${sizes[level]} mt-2 mb-1">${renderInline(
          heading[2].trim()
        )}</h${level}>`
      );
      continue;
    }

    // Blockquote. Note: '>' was HTML-escaped to '&gt;' before parsing.
    const quote = line.match(/^\s*&gt;\s?(.*)$/);
    if (quote) {
      flushParagraph();
      closeAllLists();
      out.push(
        `<blockquote class="border-l-2 border-wa-border pl-2 text-wa-secondary my-1">${renderInline(
          quote[1]
        )}</blockquote>`
      );
      continue;
    }

    // List item (unordered or ordered).
    const ulItem = line.match(/^(\s*)([-*+])\s+(.*)$/);
    const olItem = line.match(/^(\s*)(\d+)\.\s+(.*)$/);
    if (ulItem || olItem) {
      flushParagraph();
      const item = ulItem || olItem;
      const indent = indentOf(item[1]);
      const type = ulItem ? "ul" : "ol";
      const content = renderInline(item[3]);

      // Close deeper/sibling lists that no longer apply.
      closeListsTo(indent + 1);

      const top = listStack[listStack.length - 1];
      if (!top || top.indent < indent) {
        // Open a new nested list.
        const cls =
          type === "ol"
            ? 'list-decimal pl-5 my-1 text-wa-text'
            : 'list-disc pl-5 my-1 text-wa-text';
        out.push(`<${type} class="${cls}">`);
        listStack.push({ type, indent });
      } else if (top.type !== type) {
        // Same indent but different list type: switch.
        out.push(top.type === "ol" ? "</ol>" : "</ul>");
        listStack.pop();
        const cls =
          type === "ol"
            ? 'list-decimal pl-5 my-1 text-wa-text'
            : 'list-disc pl-5 my-1 text-wa-text';
        out.push(`<${type} class="${cls}">`);
        listStack.push({ type, indent });
      }
      out.push(`<li>${content}</li>`);
      continue;
    }

    // Otherwise: paragraph text. If inside a list, treat as continuation line
    // by appending to the last item is complex; keep it simple — close lists.
    if (listStack.length) {
      closeAllLists();
    }
    paragraph.push(line.trim());
  }

  flushParagraph();
  closeAllLists();

  let result = out.join("\n");

  // (h) Reinject placeholders — inline code first, then code blocks.
  result = result.replace(
    new RegExp(`${CODE_INLINE_MARK.replace("\x00", "\\x00")}(\\d+)\\x00`, "g"),
    (m, i) =>
      `<code class="bg-wa-bg border border-wa-border rounded px-1 text-[12px]">${codeInlines[Number(i)]}</code>`
  );
  result = result.replace(
    new RegExp(`${CODE_BLOCK_MARK.replace("\x00", "\\x00")}(\\d+)\\x00`, "g"),
    (m, i) =>
      `<pre class="bg-wa-bg border border-wa-border rounded-md p-2 my-1.5 overflow-x-auto text-[12px]"><code>${codeBlocks[Number(i)]}</code></pre>`
  );

  return result;
}
