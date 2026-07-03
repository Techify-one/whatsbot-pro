"""Alembic migration-graph hygiene checks (Phase A0).

HARD assertion:
    * exactly ONE Alembic head (the graph must be linear / converged).

WARN-only (never hard-fail in A0):
    * duplicate SEQUENCE prefixes among version filenames. There are KNOWN
      duplicate pairs, tolerated here because each is a pair of siblings that
      were developed in parallel branches and then LINEARIZED (one revises the
      other) while keeping the same numeric prefix:
        - ``..._0021_inbox_members`` / ``..._0021_template_permissions``
        - ``..._0032_more_permissions`` / ``..._0032_rename_atendimentos_plugin_to_protocolos``
        - ``..._0034_message_sent_by`` / ``..._0034_conversation_origin``
      Those pairs are allowlisted. Any OTHER duplicate sequence prefix is a real
      problem and fails the test.
"""

from __future__ import annotations

import re
import warnings
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERSIONS_DIR = REPO_ROOT / "db" / "alembic" / "versions"

# (sequence_prefix, frozenset-of-stems) pairs that are knowingly duplicated and
# tolerated in A0. The dup is fixed in a later phase.
ALLOWLISTED_DUP_PREFIXES = {"0021", "0032", "0034"}

# Filename pattern: YYYYMMDD_NNNN_description.py  -> capture the NNNN sequence.
_SEQ_RE = re.compile(r"^\d{8}_(\d{4})_.+\.py$")


def _load_script_dir():
    """Build a real Alembic ScriptDirectory using the project's config approach.

    Mirrors ``db.connection._run_alembic_upgrade``: load ``alembic.ini`` and set
    ``script_location`` to ``db/alembic``.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "db" / "alembic"))
    return ScriptDirectory.from_config(cfg)


def test_single_alembic_head():
    script_dir = _load_script_dir()
    heads = script_dir.get_heads()
    assert len(heads) == 1, (
        f"expected exactly 1 Alembic head, found {len(heads)}: {heads}. "
        "Migrations must form a linear, converged graph."
    )


def test_linear_chain_single_parent_reaches_all_revisions():
    """The revision graph must be ONE linear chain: no branch points, no orphans.

    This is the direct detector of the "janela paralela" incident (plano 29 /
    migration-0034 landmine): ``0034_message_sent_by`` and
    ``0034_conversation_origin`` briefly shared ``down_revision =
    0033_core_atendimento``. A DB that upgraded through the window recorded the
    head WITHOUT applying the sibling — ``alembic upgrade head`` never runs it
    again, and the live schema silently drifts from ``db/tables.py`` (the
    ``messages.sent_by_*`` 500). ``test_single_alembic_head`` alone does NOT
    catch it once the graph is re-linearized; this test fails the moment two
    revisions share a parent, BEFORE any database steps through the window.
    """
    script_dir = _load_script_dir()
    revisions = list(script_dir.walk_revisions())  # head → base

    children_of: dict[str | None, list[str]] = defaultdict(list)
    for rev in revisions:
        down = rev.down_revision
        assert not isinstance(down, (tuple, list)), (
            f"revision {rev.revision} has multiple down_revisions {down!r} "
            "(merge revision) — the graph must stay a single linear chain."
        )
        children_of[down].append(rev.revision)

    branch_points = {
        parent: sorted(children)
        for parent, children in children_of.items()
        if len(children) > 1
    }
    assert not branch_points, (
        f"parallel revisions detected (same down_revision): {branch_points}. "
        "A DB that upgrades through this window records the head without "
        "applying the sibling — linearize BEFORE merging (see the "
        "0034_message_sent_by / 0034_conversation_origin incident)."
    )

    # Single head + no branch points + no merges ⇒ the graph is ONE linear chain
    # (Alembic fails at map-load on a dangling down_revision, and an orphaned
    # side-chain would surface here as a second head).
    heads = script_dir.get_heads()
    assert len(heads) == 1, f"expected 1 head, found: {heads}"


def test_no_unexpected_duplicate_sequence_prefixes():
    by_seq: dict[str, list[str]] = defaultdict(list)
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        m = _SEQ_RE.match(path.name)
        if m:
            by_seq[m.group(1)].append(path.name)

    duplicates = {seq: names for seq, names in by_seq.items() if len(names) > 1}

    # Surface the known/allowlisted dup as a warning so it stays visible.
    for seq in sorted(duplicates):
        if seq in ALLOWLISTED_DUP_PREFIXES:
            warnings.warn(
                f"known/allowlisted duplicate Alembic sequence prefix _{seq}_: "
                f"{duplicates[seq]} (scheduled to be fixed in a later phase)",
                stacklevel=2,
            )

    unexpected = {
        seq: names
        for seq, names in duplicates.items()
        if seq not in ALLOWLISTED_DUP_PREFIXES
    }
    assert not unexpected, (
        f"unexpected duplicate Alembic sequence prefixes: {unexpected}"
    )
