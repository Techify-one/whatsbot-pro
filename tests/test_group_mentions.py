"""Unit tests for agent.group_mentions.resolve_outgoing.

Focus: @all/@todos must expand to EVERY participant's phone (so WhatsApp actually
tags/notifies everyone), not a literal "@everyone" that tags no one. Individual
and combined mentions must keep working.

    venv/bin/python tests/test_group_mentions.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import group_mentions

_passed = 0
_failed = 0


def check(label, cond):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  OK {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}")


GROUP = "120363000000000000@g.us"

MEMBERS = [
    {"phone": "5511111111111", "lid": "", "name": "Ana", "is_admin": True},
    {"phone": "5522222222222", "lid": "", "name": "Bruno", "is_admin": False},
    {"phone": "5533333333333", "lid": "", "name": "", "is_admin": False},  # nameless
]
ALL_PHONES = {"5511111111111", "5522222222222", "5533333333333"}


def _seed(members, bot_phone=""):
    """Prime the members cache so get_members() returns `members` without HTTP."""
    group_mentions._client = object()          # non-None so get_members proceeds
    group_mentions._bot_phone = bot_phone
    group_mentions._members_cache[GROUP] = (time.time(), list(members))


def main():
    # @todos -> expande pra todos os phones; texto vira "@all"
    _seed(MEMBERS)
    text, mentions = group_mentions.resolve_outgoing(GROUP, "@todos bom dia")
    check("@todos: texto vira '@all bom dia'", text == "@all bom dia")
    check("@todos: mentions = todos os phones", set(mentions) == ALL_PHONES)
    check("@todos: sem literal '@everyone'", "@everyone" not in mentions)

    # aliases (@all/@geral/@everyone/@todes) também expandem (não passam o literal)
    for kw in ("@all", "@geral", "@everyone", "@todes"):
        _seed(MEMBERS)
        t, m = group_mentions.resolve_outgoing(GROUP, f"oi {kw}")
        check(f"{kw}: expande pra 3 phones, sem literal", len(m) == 3 and "@everyone" not in m)
        check(f"{kw}: token vira @all", "@all" in t)

    # exclui o phone do bot da menção-todos (sem self-ping)
    _seed(MEMBERS, bot_phone="5511111111111")
    text, mentions = group_mentions.resolve_outgoing(GROUP, "@todos")
    check("@todos: exclui o phone do bot",
          set(mentions) == {"5522222222222", "5533333333333"})

    # @all + menção individual combinados (sem duplicar quem já entrou no @all)
    _seed(MEMBERS)
    text, mentions = group_mentions.resolve_outgoing(GROUP, "@all e @Ana")
    check("@all + @Ana: texto tem @all e @<phone da Ana>",
          "@all" in text and "@5511111111111" in text)
    check("@all + @Ana: Ana não duplica e mentions = todos",
          mentions.count("5511111111111") == 1 and set(mentions) == ALL_PHONES)

    # fallback: membros indisponíveis -> literal "@everyone" (não regride pra nada)
    group_mentions._client = object()
    group_mentions._bot_phone = ""
    group_mentions._members_cache[GROUP] = (time.time(), [])
    text, mentions = group_mentions.resolve_outgoing(GROUP, "@todos oi")
    check("sem membros: fallback '@everyone'", mentions == ["@everyone"])
    check("sem membros: texto ainda vira @all", text == "@all oi")

    # menção individual pura continua funcionando (sem @all)
    _seed(MEMBERS)
    text, mentions = group_mentions.resolve_outgoing(GROUP, "fala @Bruno")
    check("individual: @Bruno -> @<phone>", text == "fala @5522222222222")
    check("individual: mentions só o Bruno", mentions == ["5522222222222"])

    # texto sem @ não toca em nada
    _seed(MEMBERS)
    text, mentions = group_mentions.resolve_outgoing(GROUP, "sem mencao aqui")
    check("sem @: texto inalterado, mentions vazio",
          text == "sem mencao aqui" and mentions == [])


main()
print(f"\nRESULTS: {_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
