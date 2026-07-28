"""Pure prompt-diff helpers (no DB I/O), mirroring agent/agent_factory's split.

Computes a GitHub-style unified diff with intra-line **word highlighting** using
only ``difflib`` from the stdlib (no new deps). The git insight that guides the
design: store full snapshots (blobs) and compute the diff on demand — never store
deltas. This module is the on-the-fly diff engine; the trail repo keeps the blobs.

The output is structured (``lines[]`` of typed segments), so the frontend renders
straight from data without parsing a diff string. Kept generic (operates on two
strings) so tools/prompts can reuse it later.
"""

from __future__ import annotations

import difflib
import re

# Tokenize into whitespace runs and non-whitespace runs, so joining the tokens
# back reproduces the input byte-for-byte (spaces are their own segments).
_WORD_RE = re.compile(r"\s+|\S+")


def word_segments(old: str, new: str) -> tuple[list[dict], list[dict]]:
    """Token-level diff of two changed lines.

    Returns ``(del_segments, add_segments)`` where each segment is
    ``{"t": "eq"|"del"|"add", "s": str}``. Whitespace runs are preserved as their
    own tokens so the reconstructed text is identical to the input.
    """
    a = _WORD_RE.findall(old or "")
    b = _WORD_RE.findall(new or "")
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    del_segs: list[dict] = []
    add_segs: list[dict] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        a_chunk = "".join(a[i1:i2])
        b_chunk = "".join(b[j1:j2])
        if tag == "equal":
            if a_chunk:
                del_segs.append({"t": "eq", "s": a_chunk})
            if b_chunk:
                add_segs.append({"t": "eq", "s": b_chunk})
        elif tag == "delete":
            if a_chunk:
                del_segs.append({"t": "del", "s": a_chunk})
        elif tag == "insert":
            if b_chunk:
                add_segs.append({"t": "add", "s": b_chunk})
        else:  # replace
            if a_chunk:
                del_segs.append({"t": "del", "s": a_chunk})
            if b_chunk:
                add_segs.append({"t": "add", "s": b_chunk})
    return del_segs, add_segs


def text_diff(
    text_from: str | None,
    text_to: str | None,
    *,
    from_label: str = "anterior",
    to_label: str = "atual",
) -> dict:
    """Two-level (line, then word) unified diff between two prompt snapshots.

    ``None`` is treated as ``""`` (pre-0030 snapshots may lack the prompt key).
    Returns a render-ready dict — see the module/plan docs for the shape.
    """
    a_text = text_from or ""
    b_text = text_to or ""
    a_lines = a_text.splitlines()
    b_lines = b_text.splitlines()
    sm = difflib.SequenceMatcher(None, a_lines, b_lines, autojunk=False)

    lines: list[dict] = []
    added = 0
    removed = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for line in a_lines[i1:i2]:
                lines.append({"type": "ctx", "text": line})
        elif tag == "delete":
            for line in a_lines[i1:i2]:
                lines.append({"type": "del", "text": line})
                removed += 1
        elif tag == "insert":
            for line in b_lines[j1:j2]:
                lines.append({"type": "add", "text": line})
                added += 1
        else:  # replace — pair old/new lines by index, highlight word changes
            old_block = a_lines[i1:i2]
            new_block = b_lines[j1:j2]
            paired = min(len(old_block), len(new_block))
            for k in range(paired):
                del_segs, add_segs = word_segments(old_block[k], new_block[k])
                lines.append({"type": "del", "segments": del_segs})
                lines.append({"type": "add", "segments": add_segs})
                removed += 1
                added += 1
            for line in old_block[paired:]:   # leftover old → pure deletes
                lines.append({"type": "del", "text": line})
                removed += 1
            for line in new_block[paired:]:   # leftover new → pure adds
                lines.append({"type": "add", "text": line})
                added += 1

    unified = "\n".join(difflib.unified_diff(
        a_lines, b_lines, fromfile=from_label, tofile=to_label, lineterm=""))
    return {
        "lines": lines,
        "added_lines": added,
        "removed_lines": removed,
        "changed": a_text != b_text,
        "unified_diff": unified,
    }
