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
# plano 47 — fallback do tamanho máx. por-resultado reaproveitado quando a config
# ``ai_tool_reuse_result_max_chars`` está ausente/inválida (fail-open).
RESULT_REINJECT_FALLBACK = 800

_ARROW = "→"             # → — the tool_call card outcome line prefix
_WRENCH = "\U0001f527"        # 🔧 — the tool_call card header prefix
# Long base64 run (data URIs / raw blobs) — scrub before anything reaches context.
_BASE64_RE = re.compile(r"[A-Za-z0-9+/]{100,}={0,2}")
_HEADER = "[Memória deste atendimento — NÃO repita ações já concluídas abaixo]"


def _scrub(s: str) -> str:
    return _BASE64_RE.sub("[…]", s or "")


def _truncate(s: str, cap: int) -> str:
    return s if len(s) <= cap else s[:cap] + "…"


def _parse_tool_card(content: str) -> tuple[str, str, str] | None:
    """Parse a tool_call card (``🔧 name\\nkey: val\\n…\\n→ result``) into
    ``(name, signature, result)``:

    * ``name`` — the tool name (identity, used for the reuse toggle lookup);
    * ``signature`` — compact ``name(k: v, …)`` (args scrubbed + truncated), same
      string the legacy block listed;
    * ``result`` — the ``→ result`` text (scrubbed, may be multi-line, NOT yet
      truncated), or ``""`` when the card carries no outcome.

    ``None`` if unparseable (no name). Pure — the reuse policy lives in
    :func:`build_block`."""
    if not content:
        return None
    name: str | None = None
    args: list[str] = []
    result_lines: list[str] = []
    in_result = False
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not in_result and line.startswith(_ARROW):
            in_result = True
            rest = line[len(_ARROW):].strip()
            if rest:
                result_lines.append(rest)
            continue
        if in_result:  # continuation of a multi-line result
            result_lines.append(line)
            continue
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
    result = _scrub("\n".join(result_lines)) if result_lines else ""
    return name, sig, result


def _tool_signature(content: str) -> str | None:
    """From a tool_call card keep the tool name + its args, DROPPING the
    (possibly huge) ``→ result`` line. Returns a compact ``name(k: v, …)`` string,
    truncated; ``None`` if unparseable. Thin wrapper over :func:`_parse_tool_card`
    (public — covered by tests)."""
    parsed = _parse_tool_card(content)
    return parsed[1] if parsed else None


def _reuse_enabled_names() -> set[str]:
    """Tools flagged ``reuse_result`` (plano 47). Fail-open → empty set (no reuse)."""
    try:
        from db.repositories import tool_override_repo
        return tool_override_repo.reuse_enabled_names()
    except Exception:  # noqa: BLE001 — a repo read must never break a turn
        return set()


def _result_cap() -> int:
    """Per-result char cap from ``ai_tool_reuse_result_max_chars`` (D9). Fail-open
    to :data:`RESULT_REINJECT_FALLBACK`; guarded ``max(50, …)`` so a 0/negative
    config never zeroes the result out."""
    try:
        from db.repositories import config_repo
        raw = config_repo.get("ai_tool_reuse_result_max_chars", RESULT_REINJECT_FALLBACK)
        return max(50, int(raw))
    except Exception:  # noqa: BLE001
        return RESULT_REINJECT_FALLBACK


def _history_compiled():
    """Compiled history blacklist patterns (plano 43, D7). Fail-open → ``[]``."""
    try:
        from agent import history_filter
        return history_filter.load_compiled()
    except Exception:  # noqa: BLE001
        return []


def _result_blacklisted(result: str, compiled) -> bool:
    """True if the reintroduced result matches the operator's history blacklist
    (D7) — tested as a ``tool_call`` row so content anchors (``Protocolo aberto``,
    ``PROT-\\d{8}``) and a ``^tool_call\\t`` anchor both work. Fail-open → False."""
    if not compiled:
        return False
    try:
        from agent import history_filter
        return history_filter.matches("tool_call", result, compiled)
    except Exception:  # noqa: BLE001
        return False


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

        # plano 47 — política de reaproveitamento (fail-open, lida a cada turno).
        reuse_names = _reuse_enabled_names()
        result_cap = _result_cap()
        compiled = _history_compiled() if reuse_names else []

        # 1) Tools already executed this conversation.
        #    - Marked tools (reuse_result ON): the MOST RECENT occurrence keeps its
        #      ``→ resultado`` in a "results you already have" section (degradação
        #      D6: 1 por tool). Older occurrences fall back to name+args.
        #    - Everything else (unmarked tools + degraded occurrences): name+args,
        #      deduped — byte-identical to the legacy block.
        # recent_by_name keeps the MOST RECENT (sig, raw_result) per marked tool,
        # UNCONDITIONALLY (most-recent wins). The blacklist/empty filter is applied
        # AFTER, when building Section A — so a blacklisted/empty most-recent result
        # degrades to name+args instead of resurrecting a stale OLDER result (D6/D7).
        recent_by_name: dict[str, tuple[str, str]] = {}
        parsed_cards: list[tuple[str, str]] = []  # (name, sig) oldest→newest
        try:
            for card in message_repo.get_tool_calls_by_conversation(conversation_id):
                parsed = _parse_tool_card(card.get("content") or "")
                if not parsed:
                    continue
                name, sig, result = parsed
                parsed_cards.append((name, sig))
                if name in reuse_names:
                    # oldest→newest ⇒ a later occurrence overwrites (most recent wins).
                    recent_by_name[name] = (sig, result)
        except Exception:
            logger.debug("tool_memory: leitura de tool_call falhou", exc_info=True)

        # Section A: only the MOST RECENT occurrence of each marked tool, dropping
        # empty/blacklisted results (D7) so they fall back to name+args in Section B.
        # result_by_name maps tool name -> (signature, "name(args) → result").
        result_by_name: dict[str, tuple[str, str]] = {}
        for name, (sig, result) in recent_by_name.items():
            if result and not _result_blacklisted(result, compiled):
                result_by_name[name] = (sig, f"{sig} {_ARROW} {_truncate(result, result_cap)}")

        # Section A (reused results) + Section B (plain name+args). The exact
        # signature promoted to A is dropped from B (no redundancy); older calls of
        # the same tool with DIFFERENT args survive in B.
        result_lines = [line for _sig, line in result_by_name.values()]
        promoted_sigs = {sig for sig, _line in result_by_name.values()}
        tool_lines: list[str] = []
        seen: set[str] = set()
        for _name, sig in parsed_cards:
            if sig in promoted_sigs or sig in seen:
                continue
            seen.add(sig)
            tool_lines.append(sig)
            if len(tool_lines) >= MAX_TOOL_LINES:
                break

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

        if not result_lines and not tool_lines and not attr_pairs:
            return None

        parts = [_HEADER]
        if result_lines:  # plano 47 — só aparece quando há tool marcada com resultado
            parts.append(
                "Resultados que você JÁ TEM (use-os, não re-consulte): "
                + "; ".join(result_lines) + ".")
        if tool_lines:
            parts.append(
                "Ferramentas já executadas neste atendimento (não chame de novo "
                "com os mesmos dados): " + "; ".join(tool_lines) + ".")
        if attr_pairs:
            parts.append(
                "Atributos já definidos (não regrave os mesmos valores): "
                + ", ".join(attr_pairs) + ".")
        block = "\n".join(parts)
        # Cap: legacy 1200 when nothing is reused (byte-identical), raised to fit at
        # least one reused result + the list when Section A is present (plano 47 D6).
        cap = TOOL_MEMORY_MAX_CHARS
        if result_lines:
            cap = max(TOOL_MEMORY_MAX_CHARS, result_cap + 600)
        if len(block) > cap:
            block = block[:cap] + "… (truncado)"
        return block
    except Exception:
        logger.debug("tool_memory: build_block falhou", exc_info=True)
        return None
