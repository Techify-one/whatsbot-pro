"""Prod's ``python:3.x-slim`` image ships no ``/etc/mime.types`` — only the
small builtin table in ``mimetypes``, which doesn't know ``.jfif`` (GOWA's
JPEG extension) or every audio extension used by channels/plugins.
``server.app`` registers
the missing types at import time (module-level ``mimetypes.add_type``) so
``/statics`` — served straight from ``StaticFiles``, which derives
Content-Type from the extension — never answers ``application/octet-stream``
for these, which a browser's ``window.open`` (the panel's "open image" click)
turns into a forced download instead of opening the file.

``mimetypes.add_type`` overwrites the global type map unconditionally, so this
assertion holds the same on a dev box that also has ``/etc/mime.types`` — the
regression this guards against is someone deleting the ``add_type`` calls, not
an environment difference.
"""

from __future__ import annotations

import mimetypes

import server.app  # noqa: F401 — import triggers the module-level add_type calls


def test_gowa_jpeg_extension_is_registered():
    assert mimetypes.guess_type("photo.jfif")[0] == "image/jpeg"


def test_ogg_audio_extensions_are_registered():
    assert mimetypes.guess_type("note.ogg")[0] == "audio/ogg"
    assert mimetypes.guess_type("note.oga")[0] == "audio/ogg"


def test_m4a_audio_extension_is_registered():
    assert mimetypes.guess_type("note.m4a")[0] == "audio/mp4"


def test_support_catalog_audio_extensions_are_registered():
    assert mimetypes.guess_type("material.mp3")[0] == "audio/mpeg"
    assert mimetypes.guess_type("material.aac")[0] == "audio/aac"
    assert mimetypes.guess_type("material.amr")[0] == "audio/amr"
    assert mimetypes.guess_type("material.opus")[0] == "audio/ogg"
