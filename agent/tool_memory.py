"""Plano 37 A1 — compact per-conversation tool memory for the LLM context.

The model never sees the raw ``tool_call`` cards (``message_repo.get_context``
excludes role='tool_call') and the AGNO engine runs stateless
(``add_history_to_context=False``), so every turn it re-runs the same tools —
re-searching offers, regravando os mesmos ``set_custom_attribute``. This module
builds a SHORT, truncated, base64-free ``system`` block listing what already ran
in THIS conversation, so the model stops repeating itself — WITHOUT reintroducing
the raw cards nor ceding the context to AGNO (WhatsBot stays the context owner,
per CLAUDE.md).

Contract: best-effort. Any failure returns ``None`` and the turn proceeds without
the block (never breaks a turn). Gated by the ``ai_tool_memory_enabled``
kill-switch (default ON). General by design — it does NOT parse any plugin tool's
result JSON; it lists tool name + args and the conversation/contact attributes,
both already persisted.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Hard caps — the block must never bloat the prompt nor leak a giant tool result.
MAX_TOOL_LINES = 12           # distinct tool invocations listed
ITEM_MAX_CHARS = 160          # per listed line
TOOL_MEMORY_MAX_CHARS = 1200  # whole block

_WRENCH = "\U0001f527"        # 🔧 — the tool_call card header prefix
# Long base64 run (data URIs / raw blobs) — scrub before anything reaches context.
_BASE64_RE = re.compile(r"[A-Za-z0-9+/]{100,}={0,2}")
_HEADER = "[Memória deste atendimento — NÃO repita ações já concluídas abaixo]"


def _scrub(s: str) -> str:
    return _BASE64_RE.sub("[…]", s or "")


def _tool_signature(content: str) -> str | None:
    """From a tool_call card (``🔧 name\\nkey: val\\n…\\n→ result``) keep the tool
    name + its args, DROPPING the (possibly huge) ``→ result`` line. Returns a
    compact ``name(k: v, …)`` string, truncated; ``None`` if unparseable."""
    if not content:
        return None
    name: str | None = None
    args: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("→"):  # '→ result' — skip the outcome entirely
            break
        if line.startswith(_WRENCH):
            name = line[len(_WRENCH):].strip()
            continue
        if name is None:
            name = line  # header without the wrench glyph — treat as the name
            continue
        args.append(_scrub(line))
    if not name:
        return None
    sig = name if not args else f"{name}({', '.join(args)})"
    if len(sig) > ITEM_MAX_CHARS:
        sig = sig[:ITEM_MAX_CHARS] + "…"
    return sig


def build_block(contact) -> str | None:
    """Build the compact tool-memory ``system`` block for ``contact``'s open
    conversation, or ``None`` when disabled / nothing to remember / on any error."""
    try:
        from db.repositories import config_repo
        if not config_repo.get("ai_tool_memory_enabled", True):
            return None

        from db.repositories import conversation_repo, message_repo
        cid = getattr(contact, "id", None)
        if not cid:
            return None
        # Scoped to the conversation of THIS channel's inbox (plano 37) so a sibling
        # channel of the same number doesn't cross-contaminate the memory.
        conv = conversation_repo.get_open_for_contact_scoped(contact)
        if not conv:
            return None
        conversation_id = conv["id"]

        # 1) Tools already executed this conversation (name + args, deduped).
        tool_lines: list[str] = []
        seen: set[str] = set()
        try:
            for card in message_repo.get_tool_calls_by_conversation(conversation_id):
                sig = _tool_signature(card.get("content") or "")
                if sig and sig not in seen:
                    seen.add(sig)
                    tool_lines.append(sig)
                    if len(tool_lines) >= MAX_TOOL_LINES:
                        break
        except Exception:
            logger.debug("tool_memory: leitura de tool_call falhou", exc_info=True)

        # 2) Attributes already defined (contact + conversation scope, merged).
        attr_pairs: list[str] = []
        try:
            from db.repositories import custom_attribute_repo as ca
            from db.tables import contacts as _contacts, conversations as _convs
            merged: dict = {}
            merged.update(ca.get_values(_contacts, cid) or {})
            merged.update(ca.get_values(_convs, conversation_id) or {})
            for k, v in merged.items():
                if v in (None, ""):
                    continue
                pair = f"{k}={_scrub(str(v))}"
                if len(pair) > ITEM_MAX_CHARS:
                    pair = pair[:ITEM_MAX_CHARS] + "…"
                attr_pairs.append(pair)
        except Exception:
            logger.debug("tool_memory: leitura de atributos falhou", exc_info=True)

        if not tool_lines and not attr_pairs:
            return None

        parts = [_HEADER]
        if tool_lines:
            parts.append(
                "Ferramentas já executadas neste atendimento (não chame de novo "
                "com os mesmos dados): " + "; ".join(tool_lines) + ".")
        if attr_pairs:
            parts.append(
                "Atributos já definidos (não regrave os mesmos valores): "
                + ", ".join(attr_pairs) + ".")
        block = "\n".join(parts)
        if len(block) > TOOL_MEMORY_MAX_CHARS:
            block = block[:TOOL_MEMORY_MAX_CHARS] + "… (truncado)"
        return block
    except Exception:
        logger.debug("tool_memory: build_block falhou", exc_info=True)
        return None
