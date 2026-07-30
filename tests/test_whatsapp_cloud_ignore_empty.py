"""Plano 95 F4 — testes puros da regra que descarta o inbound vazio da Meta.

A fixture central é o item LITERAL de ``messages[]`` capturado em produção
(``debug_bus_1785431496831.jsonl:3703``, contato 447974905044): a Meta entregou
``type: "unsupported"`` + ``errors[0].code = 131051`` e NENHUM corpo.

O módulo sob teste vive dentro do plugin
(``assets/plugin_examples/whatsapp_cloud/inbound_ignore.py``) e tem que
permanecer puro/stdlib, então é carregado POR CAMINHO — nunca como pacote de
plugin. Nada aqui toca banco, app ou rede.

O último bloco sobe o ``channels.py`` do plugin (também por caminho, com o
pacote sintético que resolve o ``from .inbound_ignore import …``) e prova o
gancho de F2 no ``parse_inbound``: payload real ⇒ ``[]``; lote misto ⇒ só a
mensagem de texto; ``statuses[]`` intacto.

    venv/bin/python -m pytest tests/test_whatsapp_cloud_ignore_empty.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_PLUGIN_DIR = _ROOT / "assets" / "plugin_examples" / "whatsapp_cloud"
_MODULE_PATH = _PLUGIN_DIR / "inbound_ignore.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "whatsbot_test_cloud_inbound_ignore", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ig = _load_module()


# ── Fixtures literais ───────────────────────────────────────────────────────

REAL_META_MSG = {
    "from": "447974905044",
    "from_user_id": "GB.2076874439870767",
    "id": "wamid.HBgMNDQ3OTc0OTA1MDQ0FQIAEhgSQzk5Q0I4Q0E3MUI2RDYwNkRBAA==",
    "timestamp": "1785420528",
    "errors": [{"code": 131051, "title": "Message type unknown",
                "message": "Message type unknown",
                "error_data": {"details": "Message type is currently not supported."}}],
    "type": "unsupported",
    "unsupported": {"type": "unknown"},
}

TEXT_MSG = {
    "from": "5511999990000",
    "id": "wamid.text.95",
    "timestamp": "1785420600",
    "type": "text",
    "text": {"body": "oi"},
}


# ── should_ignore — a tabela §4 do plano, linha a linha ─────────────────────

def test_ignores_real_meta_payload():
    """O caso que originou o plano: default (``codes=()``) descarta."""
    assert ig.should_ignore(REAL_META_MSG, ()) is True


def test_default_ignores_any_unsupported_code():
    """D5 — sem estreitamento, o código é irrelevante."""
    msg = {"type": "unsupported", "errors": [{"code": 999999, "title": "Outro"}]}
    assert ig.should_ignore(msg, ()) is True


def test_default_ignores_unsupported_without_errors():
    assert ig.should_ignore({"type": "unsupported"}, ()) is True
    assert ig.should_ignore({"type": "unsupported", "unsupported": {"type": "x"}}, ()) is True


def test_default_codes_constant_is_empty():
    """A constante do módulo É o default do plano (D5) — vazia."""
    assert ig.DEFAULT_IGNORED_ERROR_CODES == ()
    assert ig.should_ignore(REAL_META_MSG) is True  # sem passar ``codes``


def test_keeps_text_message():
    assert ig.should_ignore(TEXT_MSG, ()) is False
    assert ig.should_ignore(TEXT_MSG, (131051,)) is False


def test_keeps_named_unknown_type():
    """A âncora é o literal ``"unsupported"``.

    Um tipo NOVO e nomeado (a Meta passar a entregar ``poll``) pode vir COM
    payload — tem que continuar passando e caindo no fallback de hoje.
    """
    msg = {"type": "poll", "poll": {"name": "Qual?", "options": ["a", "b"]}}
    assert ig.should_ignore(msg, ()) is False
    # Nem mesmo um ``errors`` casando a lista muda isso: o tipo manda.
    msg_with_error = dict(msg, errors=[{"code": 131051, "title": "x"}])
    assert ig.should_ignore(msg_with_error, (131051,)) is False


def test_narrowed_list_keeps_other_code():
    msg = {"type": "unsupported", "errors": [{"code": 999999, "title": "Outro"}]}
    assert ig.should_ignore(msg, (131051,)) is False


def test_narrowed_list_ignores_listed_code():
    assert ig.should_ignore(REAL_META_MSG, (131051,)) is True


def test_narrowed_list_keeps_unsupported_without_errors():
    """Sem ``errors`` não há o que casar contra uma lista explícita."""
    assert ig.should_ignore({"type": "unsupported"}, (131051,)) is False


def test_narrowed_list_matches_string_code():
    """A Meta é inconsistente com número × string; ``"131051"`` casa igual."""
    msg = {"type": "unsupported", "errors": [{"code": "131051"}]}
    assert ig.should_ignore(msg, (131051,)) is True


@pytest.mark.parametrize("errors", ["x", 7, [{}], [None], [{"code": "abc"}], {"code": 131051}])
def test_malformed_errors_fails_open(errors):
    msg = {"type": "unsupported", "errors": errors}
    assert ig.should_ignore(msg, (131051,)) is False


@pytest.mark.parametrize("msg", [None, [], "x", 0, object()])
def test_not_a_dict_fails_open(msg):
    assert ig.should_ignore(msg, ()) is False
    assert ig.should_ignore(msg, (131051,)) is False


# ── parse_codes ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("131051, 131052", (131051, 131052)),
    ("", ()),
    ("lixo", ()),
    ("131051,,x", (131051,)),
    (None, ()),
    ("  131051  ", (131051,)),
    ("131051,131051", (131051,)),          # dedup
    ([131051, "131052"], (131051, 131052)),  # lista também serve
])
def test_parse_codes(raw, expected):
    assert ig.parse_codes(raw) == expected


# ── describe_ignored (rótulo do log — único lugar que lê ``title``) ─────────

def test_describe_ignored_has_code_and_title():
    out = ig.describe_ignored(REAL_META_MSG)
    assert "131051" in out
    assert "Message type unknown" in out
    assert "unknown" in out  # subtipo


def test_describe_ignored_never_raises():
    for bad in (None, "x", {}, {"errors": "x"}, {"errors": [1]}):
        assert isinstance(ig.describe_ignored(bad), str)


# ── Gancho no parse_inbound (F2), ainda sem banco e sem app ─────────────────

def _load_channels_module():
    """Sobe ``whatsapp_cloud/channels.py`` por caminho, no molde do loader.

    O pacote sintético é o que faz o ``from .inbound_ignore import …`` resolver
    — mesma técnica de ``tests/test_plano75_parse_inbound.py``.
    """
    pkg_name = "whatsbot_test_pkg_wa_cloud_ignore"
    if pkg_name not in sys.modules:
        pkg_spec = importlib.util.spec_from_file_location(
            pkg_name, _PLUGIN_DIR / "__init__.py",
            submodule_search_locations=[str(_PLUGIN_DIR)])
        assert pkg_spec is not None and pkg_spec.loader is not None
        pkg = importlib.util.module_from_spec(pkg_spec)
        sys.modules[pkg_name] = pkg
        pkg_spec.loader.exec_module(pkg)
    full = f"{pkg_name}.channels"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, _PLUGIN_DIR / "channels.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


_channels = _load_channels_module()


def _envelope(messages=None, statuses=None) -> dict:
    value = {"messaging_product": "whatsapp",
             "metadata": {"display_phone_number": "556299071262",
                          "phone_number_id": "PNID_P95"}}
    if messages is not None:
        value["messages"] = messages
    if statuses is not None:
        value["statuses"] = statuses
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "WABA_P95", "changes": [{"field": "messages",
                                                      "value": value}]}]}


@pytest.fixture
def cloud(monkeypatch):
    """Provider isolado, com o toggle LIGADO e a lista default (sem banco)."""
    monkeypatch.setattr(_channels, "_ignore_settings", lambda: (True, ()))
    return _channels.WhatsAppCloudChannel(
        channel_id="p95", registry=None, credentials={})


def test_parse_inbound_drops_the_real_payload(cloud):
    assert cloud.parse_inbound(_envelope(messages=[REAL_META_MSG])) == []


def test_parse_inbound_keeps_the_text_of_a_mixed_batch(cloud):
    """O ``continue`` é POR ITEM — um lote misto não pode se perder inteiro."""
    events = cloud.parse_inbound(_envelope(messages=[REAL_META_MSG, TEXT_MSG]))
    assert [e.external_msg_id for e in events] == ["wamid.text.95"]
    assert events[0].text == "oi"


def test_parse_inbound_keeps_statuses(cloud):
    """Recibo é outro laço: descartar mensagem não pode calar ``statuses[]``."""
    status = {"id": "wamid.out.95", "recipient_id": "5511999990000",
              "status": "delivered", "timestamp": "1785420700"}
    events = cloud.parse_inbound(_envelope(messages=[REAL_META_MSG], statuses=[status]))
    assert [e.kind for e in events] == ["receipt"]
    assert events[0].external_msg_id == "wamid.out.95"


def test_parse_inbound_toggle_off_restores_old_behaviour(cloud, monkeypatch):
    monkeypatch.setattr(_channels, "_ignore_settings", lambda: (False, ()))
    events = cloud.parse_inbound(_envelope(messages=[REAL_META_MSG]))
    assert len(events) == 1
    assert events[0].kind == "message"
    assert events[0].media_type == "unsupported"


def test_parse_inbound_narrowed_list_keeps_other_code(cloud, monkeypatch):
    monkeypatch.setattr(_channels, "_ignore_settings", lambda: (True, (131051,)))
    other = dict(REAL_META_MSG, id="wamid.other.95",
                 errors=[{"code": 999999, "title": "Outro"}])
    events = cloud.parse_inbound(_envelope(messages=[REAL_META_MSG, other]))
    assert [e.external_msg_id for e in events] == ["wamid.other.95"]


def test_discard_logs_phone_and_wamid(cloud):
    """O warning é o ÚNICO rastro em texto — tem que dar para investigar.

    O handler é pendurado NO logger do plugin em vez de usar ``caplog``: o
    ``create_app`` reconfigura o logging do processo, e uma suíte que já tenha
    subido o app deixaria o ``caplog`` (que escuta a raiz) mudo.
    """
    import logging

    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Collector(level=logging.WARNING)
    plugin_logger = _channels.logger
    previous = (plugin_logger.level, plugin_logger.disabled)
    plugin_logger.addHandler(handler)
    plugin_logger.setLevel(logging.WARNING)
    # ``disabled = False`` é load-bearing: se outro teste da sessão já subiu o
    # app, o ``dictConfig`` do boot (``disable_existing_loggers`` default) deixou
    # este logger — criado pelo import por caminho — DESABILITADO. É artefato de
    # teste; o módulo que o app carrega nasce depois da configuração.
    plugin_logger.disabled = False
    try:
        cloud.parse_inbound(_envelope(messages=[REAL_META_MSG]))
    finally:
        plugin_logger.removeHandler(handler)
        plugin_logger.setLevel(previous[0])
        plugin_logger.disabled = previous[1]

    blob = "\n".join(r.getMessage() for r in records)
    assert "447974905044" in blob
    assert REAL_META_MSG["id"] in blob
    assert "131051" in blob


def test_plugin_loads_without_the_new_module(monkeypatch):
    """Zip antigo sem ``inbound_ignore.py`` ⇒ o plugin ainda CARREGA.

    Reproduz o import defensivo removendo o módulo do disco virtualmente: um
    ``ImportError`` no topo de ``channels.py`` derrubaria o canal inteiro.
    """
    real_find = importlib.util.find_spec

    def _blocked(name, package=None):
        if name.endswith("inbound_ignore"):
            raise ImportError("simulado: zip antigo")
        return real_find(name, package)

    pkg_name = "whatsbot_test_pkg_wa_cloud_noignore"
    plugin_dir = _PLUGIN_DIR
    pkg_spec = importlib.util.spec_from_file_location(
        pkg_name, plugin_dir / "__init__.py",
        submodule_search_locations=[str(plugin_dir)])
    pkg = importlib.util.module_from_spec(pkg_spec)
    sys.modules[pkg_name] = pkg
    pkg_spec.loader.exec_module(pkg)
    # Bloqueia o submódulo novo: o import relativo tem que falhar e ser engolido.
    sys.modules[f"{pkg_name}.inbound_ignore"] = None  # type: ignore[assignment]
    try:
        spec = importlib.util.spec_from_file_location(
            f"{pkg_name}.channels", plugin_dir / "channels.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"{pkg_name}.channels"] = module
        spec.loader.exec_module(module)  # não pode levantar
        assert module.should_ignore(REAL_META_MSG, ()) is False  # fail-open
        ch = module.WhatsAppCloudChannel(channel_id="p95", registry=None, credentials={})
        assert len(ch.parse_inbound(_envelope(messages=[REAL_META_MSG]))) == 1
    finally:
        for name in (f"{pkg_name}.channels", f"{pkg_name}.inbound_ignore", pkg_name):
            sys.modules.pop(name, None)
