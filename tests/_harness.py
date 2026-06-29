"""Transition shim for migrating the script-style ``check()`` suites to pytest.

Background
----------
The legacy suites (notably ``tests/test_endpoints.py``) accumulate results in
module-level ``passed``/``failed`` counters via a ``check(name, condition)``
helper, then ``print`` a summary and ``sys.exit(1 if failed else 0)``. That is
incompatible with pytest collection.

Migration intent (Phase A1)
---------------------------
Rather than rewrite ~847 inline assertions at once, A1 wraps a group of related
checks in a single pytest function that drives a :class:`Checker`:

    from tests._harness import Checker

    def test_contacts(client):
        chk = Checker()
        chk.section("Contacts")
        r = client.get("/api/contacts")
        chk.check("list 200", r.status_code == 200)
        chk.check("payload ok", r.json().get("ok") is True)
        chk.finish()   # raises AssertionError listing every failure

This keeps the human-readable, accumulate-then-report ergonomics of the old
harness while making each block a real, independently-failing pytest test.

The shim is deliberately tiny and dependency-free so it can be imported by both
native pytest tests and (eventually) by the legacy script itself.
"""

from __future__ import annotations

from typing import List, Tuple


class Checker:
    """Accumulate boolean checks; assert all passed at the end.

    Use :meth:`check` to record a named condition and :meth:`finish` to raise an
    ``AssertionError`` enumerating any failures. :meth:`section` is a no-op
    label kept for parity with the legacy ``section()`` (it groups output).
    """

    def __init__(self) -> None:
        self.passed: int = 0
        self.failed: int = 0
        self._failures: List[str] = []
        self._current_section: str = ""

    def section(self, title: str) -> None:
        """Record a section label (purely organizational)."""
        self._current_section = title

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        """Record one named check. Returns the (bool) outcome."""
        ok = bool(condition)
        if ok:
            self.passed += 1
        else:
            self.failed += 1
            label = f"[{self._current_section}] {name}" if self._current_section else name
            if detail:
                label += f" -- {detail}"
            self._failures.append(label)
        return ok

    @property
    def failures(self) -> List[str]:
        return list(self._failures)

    def finish(self) -> None:
        """Raise ``AssertionError`` if any check failed; otherwise return.

        The message lists every failing check so a single pytest test surfaces
        all problems in its block at once.
        """
        if self.failed:
            lines = "\n".join(f"  - {f}" for f in self._failures)
            raise AssertionError(
                f"{self.failed} of {self.passed + self.failed} checks failed:\n{lines}"
            )

    def summary(self) -> Tuple[int, int]:
        """Return ``(passed, failed)`` counts."""
        return self.passed, self.failed


# Module-level convenience: a default singleton-style API mirroring the legacy
# free functions, for call sites that prefer ``check(...)``/``section(...)``
# without instantiating. (A1 may use either style.)
_default = Checker()


def check(name: str, condition: bool, detail: str = "") -> bool:
    return _default.check(name, condition, detail)


def section(title: str) -> None:
    _default.section(title)


def finish() -> None:
    _default.finish()


def reset() -> None:
    """Reset the module-level default checker (test isolation helper)."""
    global _default
    _default = Checker()


# ── Self-test (proves the shim works under pytest) ─────────────────────────

def test_checker_passes_clean():
    chk = Checker()
    chk.section("self-test")
    chk.check("true is true", True)
    chk.check("1 == 1", 1 == 1)
    # No failures -> finish() must NOT raise.
    chk.finish()
    assert chk.summary() == (2, 0)


def test_checker_raises_on_failure():
    import pytest

    chk = Checker()
    chk.check("ok", True)
    chk.check("this fails", False, detail="intentional")
    with pytest.raises(AssertionError) as exc:
        chk.finish()
    msg = str(exc.value)
    assert "1 of 2 checks failed" in msg
    assert "this fails" in msg
    assert "intentional" in msg


def test_module_level_helpers():
    reset()
    section("mod")
    check("pass", True)
    finish()  # clean -> no raise
    check("fail", False)
    import pytest

    with pytest.raises(AssertionError):
        finish()
    reset()
