"""Detector de escrita não-latina na saída da IA (plano 167).

Allowlist, não denylist: toda LETRA do texto precisa pertencer à escrita
latina — qualquer outra reprova por definição. Isso cobre alfabetos que
ninguém listou explicitamente (e futuros acréscimos ao Unicode) sem manter
uma lista de "alfabetos proibidos".

Não é uma trava de idioma: símbolos, emoji, dígitos, pontuação e caracteres
invisíveis NÃO são letras (fora de Lu/Ll/Lt/Lm/Lo) e passam sempre —
inclusive texto em inglês, que é escrita latina. Ver docs/IA.md
§"Motor de agente (AGNO)" para o caso real que motivou isto (plano 167).
"""

from __future__ import annotations

import unicodedata

# Letter-category codepoints that unicodedata does NOT name "LATIN..." but are
# typographically Latin/technical, not a foreign script — kept as \uXXXX
# escapes, never the literal character: an editor that normalizes the file on
# save could silently turn the literal into something else and disarm the
# allowlist.
#   U+00AA FEMININE ORDINAL INDICATOR ("1ª") / U+00BA MASCULINE ORDINAL
#   INDICATOR ("3º") — briefing original.
#   U+00B5 MICRO SIGN ("10 µs") — NFKC folds it to GREEK SMALL LETTER MU, so
#   normalizing alone would still flag it (plano 167 §4.2 / P1).
#   U+02BC MODIFIER LETTER APOSTROPHE — a typographic apostrophe some models
#   emit; it has no Latin-named NFKC decomposition (P1).
_ALLOWED_LETTERS = frozenset({"ª", "º", "µ", "ʼ"})

# Unicode general categories that count as "a letter" for this guard.
_LETTER_CATEGORIES = frozenset({"Lu", "Ll", "Lt", "Lm", "Lo"})


def _is_foreign_letter(ch: str) -> bool:
    """True when ``ch`` is a letter that is not Latin script.

    Non-letters (emoji, digits, punctuation, invisible format chars) are
    never foreign — this is only ever meaningful on letter-category input.
    """
    if ch in _ALLOWED_LETTERS:
        return False
    if unicodedata.category(ch) not in _LETTER_CATEGORIES:
        return False
    # NFKC folds typographic/stylistic Latin variants (superscripts, fullwidth,
    # mathematical bold, SCRIPT SMALL L...) onto their plain Latin base before
    # the name check, so those pass instead of being flagged as foreign — a
    # font style, not a different script (plano 167 §4.2 / P1).
    for nch in unicodedata.normalize("NFKC", ch):
        if unicodedata.category(nch) not in _LETTER_CATEGORIES:
            continue
        # unicodedata.name() has no entry for some codepoints; treat those —
        # and anything without a "LATIN..." name — as foreign by default. An
        # allowlist has to fail closed on what it doesn't recognize.
        if not unicodedata.name(nch, "").startswith("LATIN"):
            return True
    return False


def has_foreign_script(text: str) -> bool:
    """True if ``text`` contains at least one non-Latin letter."""
    if not text:
        return False
    return any(_is_foreign_letter(ch) for ch in text)


def foreign_letters(text: str) -> list[str]:
    """Sorted, de-duplicated list of the non-Latin letters found in ``text``."""
    if not text:
        return []
    return sorted({ch for ch in text if _is_foreign_letter(ch)})


def foreign_sample(text: str, limit: int = 8) -> str:
    """A short, deterministic sample of the non-Latin letters in ``text``.

    For log lines and ``track_step`` payloads — never raises, never unbounded.
    """
    return "".join(foreign_letters(text)[:limit])
