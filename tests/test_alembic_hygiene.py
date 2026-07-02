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
