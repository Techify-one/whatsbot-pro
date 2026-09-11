"""Plano 118 F2 — a escada "mode → booleano legado → default", em um lugar só.

``modes_for`` é o ÚNICO ponto que decide quais direções de um tipo de mídia são
transcritas. O teste trava as duas pontas que a D3 exige:

* a chave nova (``<kind>_transcription_mode``) vence quando existe;
* na ausência dela o booleano legado (``<kind>_transcription_enabled``) manda —
  é o que garante que nenhum canal existente mude de comportamento antes de
  alguém abrir o formulário.

    venv/bin/python -m pytest tests/core/test_transcription_modes.py -q
"""

from __future__ import annotations

from server.transcription import (
    direction_of, modes_for, parse_audio_modes, parse_media_modes,
)


class _Settings(dict):
    """Objeto ``settings``-like mínimo (só ``get``), como o global do app."""

    def get(self, key, default=None):
        return dict.get(self, key, default)


class _View(_Settings):
    """Dublê do ``ChannelSettingsView``: sabe dizer o que o CANAL sobrepõe."""

    def __init__(self, values, overridden):
        super().__init__(values)
        self._ov = frozenset(overridden)

    def overridden_keys(self):
        return self._ov


# ── parse ────────────────────────────────────────────────────────────────────

def test_parse_media_modes_cobre_os_legados():
    assert parse_media_modes(None) == {"received"}
    assert parse_media_modes("") == set()
    assert parse_media_modes("off") == set()
    assert parse_media_modes("none") == set()
    assert parse_media_modes("both") == {"received", "sent"}
    assert parse_media_modes("received") == {"received"}
    assert parse_media_modes("received,sent,private") == {"received", "sent", "private"}
    assert parse_media_modes(["sent", "lixo"]) == {"sent"}
    assert parse_media_modes("lixo") == set()


def test_parse_audio_modes_continua_sendo_o_mesmo_calable():
    """O nome antigo é superfície de fato (importado pelo serviço e pela suíte)."""
    assert parse_audio_modes is parse_media_modes


# ── direction_of ─────────────────────────────────────────────────────────────

def test_direction_of_mapeia_os_sources_reais():
    assert direction_of("echo") == "sent"
    assert direction_of("operator") == "sent"
    assert direction_of("private") == "private"
    assert direction_of("batch") == "received"
    assert direction_of("group_no_mention") == "received"
    assert direction_of("qualquer_coisa_nova") == "received"


# ── modes_for ────────────────────────────────────────────────────────────────

def test_mode_presente_vence():
    s = _Settings({"image_transcription_mode": "sent,private",
                   "image_transcription_enabled": False})
    assert modes_for(s, "image") == {"sent", "private"}


def test_sem_mode_cai_no_booleano_legado():
    assert modes_for(_Settings({"image_transcription_enabled": True}), "image") == {"received"}
    assert modes_for(_Settings({"image_transcription_enabled": False}), "image") == set()


def test_sem_nada_o_default_e_recebidas():
    assert modes_for(_Settings({}), "image") == {"received"}


def test_mode_com_lixo_desliga():
    assert modes_for(_Settings({"image_transcription_mode": "lixo"}), "image") == set()


def test_audio_usa_a_mesma_escada():
    s = _Settings({"audio_transcription_mode": "both"})
    assert modes_for(s, "audio") == {"received", "sent"}


def test_booleano_do_canal_vence_o_mode_global():
    """O caso que a escada ingênua erraria (e que ligaria a descrição sozinha).

    Canal antigo que só tem ``image_transcription_enabled=False``, sobre um
    global que JÁ tem ``image_transcription_mode``: o view devolveria o mode
    global (o canal não sobrepõe essa chave) e a caixa desmarcada pelo operador
    voltaria a descrever. ``modes_for`` resolve no MESMO escopo."""
    view = _View({"image_transcription_mode": "received,sent",   # global
                  "image_transcription_enabled": False},          # override do canal
                 overridden=["image_transcription_enabled"])
    assert modes_for(view, "image") == set()


def test_mode_do_canal_vence_o_booleano_do_canal():
    view = _View({"image_transcription_mode": "sent",
                  "image_transcription_enabled": False},
                 overridden=["image_transcription_mode", "image_transcription_enabled"])
    assert modes_for(view, "image") == {"sent"}


def test_canal_sem_override_herda_o_global():
    view = _View({"image_transcription_mode": "private"}, overridden=[])
    assert modes_for(view, "image") == {"private"}
