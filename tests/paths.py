"""Stable filesystem locations for tests, independent of test file nesting."""

from pathlib import Path


TESTS_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_ROOT.parent

__all__ = ["PROJECT_ROOT", "TESTS_ROOT"]
