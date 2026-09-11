"""Plano 123 · F2 — ``message.saved``/``message.sent`` carregam a ROTA da mensagem.

Antes desta fase o bus dizia só *de quem* veio (``phone``) e nunca *por onde*. Um
plugin que precisasse da conversa tinha de resolvê-la por telefone —
``contact_repo.get_by_phone`` (que casa as variantes BR de 12↔13 dígitos e devolve
``.first()`` sem ``ORDER BY``) seguido de ``get_open_for_contact``, que ignora o
inbox. Com o mesmo cliente atendido em dois canais, o plugin escrevia na thread
errada.

O teste sobe o app pelo LOADER REAL com um plugin-sonda que assina os dois eventos,
dirige um inbound pela rota ``/api/webhook/gowa/default`` real e um envio do
operador pela rota real, e confere que ``channel_id``/``conversation_id`` chegam ao
handler. Carregar o módulo por caminho continuaria verde com a costura arrancada.
"""

from __future__ import annotations

import asyncio
import importlib
from contextlib import contextmanager
from pathlib import Path

from db.repositories import contact_repo, message_repo


PROBE_ID = "bus_route_probe"


def _write_probe_plugin(root: Path) -> Path:
    """Plugin-sonda: guarda cada payload de message.saved/sent num global do módulo."""
    source = root / PROBE_ID
    source.mkdir(parents=True)
    (source / "plugin.yaml").write_text(
        f"id: {PROBE_ID}\n"
        "name: Bus Route Probe\n"
        "version: 1.0.0\n"
        'whatsbot_api_version: ">=1.0,<2.0"\n'
        "entry:\n"
        "  events: events\n",
        encoding="utf-8",
    )
    (source / "events.py").write_text(
        "SEEN = []\n"
        "\n"
        "def _capture(ctx, payload):\n"
        "    SEEN.append((ctx.event_name, dict(payload)))\n"
        "\n"
        "EVENT_HANDLERS = {'message.saved': _capture, 'message.sent': _capture}\n",
        encoding="utf-8",
    )
    return source


def _seen() -> list[tuple[str, dict]]:
    """Lê o global do módulo REALMENTE carregado pelo loader (namespace canônico)."""
    mod = importlib.import_module(f"whatsbot_plugins.{PROBE_ID}.events")
    return list(mod.SEEN)


def _reset_seen() -> None:
    mod = importlib.import_module(f"whatsbot_plugins.{PROBE_ID}.events")
    mod.SEEN.clear()


def _drain(built, channel_id: str, phone: str) -> None:
    """Espera o batch do orquestrador terminar E o fan-out do bus rodar.

    ⚠️ Roda pelo ``client.portal`` — a task vive no loop do app, e um
    ``asyncio.run`` próprio abriria OUTRO loop, onde o await falha calado.
    O ``sleep`` no fim existe porque ``emit`` é fire-and-forget
    (``create_task`` por handler): sem ceder o loop, a asserção corre antes."""
    async def _run():
        for _ in range(6):
            task = built.app.state.deps.state.processing_tasks.get((channel_id, phone))
            if task is None:
                break
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0)
        for _ in range(20):
            await asyncio.sleep(0.05)

    built.client.portal.call(_run)


@contextmanager
def _bus_wired(built):
    """Liga o bus ao loop do app, como o lifespan real faz.

    ``build_test_app`` não chama ``events.set_runtime``, então ``_loop is None``
    e todo ``emit`` é DESCARTADO em silêncio (``events.py`` só loga em debug).
    Sem isto o teste passaria vazio — pior que vermelho. Mesmo idioma de
    ``tests/integration/characterization/test_rbac_characterization.py``.
    """
    from plugins import events as bus

    prev_loop = getattr(bus, "_loop", None)
    prev_handler = getattr(bus, "_agent_handler", None)
    loop = built.client.portal.call(asyncio.get_running_loop)
    bus.set_runtime(loop, built.agent_handler)
    try:
        yield
    finally:
        bus.set_runtime(prev_loop, prev_handler)


def _pick(seen, event: str, source: str) -> dict | None:
    for name, payload in seen:
        if name == event and payload.get("source") == source:
            return payload
    return None


def test_message_saved_carries_channel_and_conversation(build_app, tmp_path):
    """Inbound pela rota real ⇒ o handler recebe channel_id + conversation_id."""
    probe = _write_probe_plugin(tmp_path / "probe_saved")
    built = build_app(
        ["gowa", PROBE_ID],
        plugin_sources={PROBE_ID: probe},
        settings_overrides={"auto_reply": False, "message_batch_delay": 0},
    )
    _reset_seen()

    phone = "5511977230011"
    with _bus_wired(built):
        r = built.client.post("/api/webhook/gowa/default", json={
            "event": "message", "payload": {
                "from": f"{phone}@s.whatsapp.net", "id": "p123_saved_1",
                "body": "mensagem que precisa de rota", "from_name": "Cliente"}})
        assert r.status_code == 200, r.text
        _drain(built, "default", phone)

    payload = _pick(_seen(), "message.saved", "batch_text")
    assert payload is not None, f"message.saved(batch_text) não emitido: {_seen()}"
    assert payload.get("channel_id") == "default", (
        "sem channel_id o plugin volta a adivinhar a thread por telefone; "
        f"payload={payload}")

    conv_id = payload.get("conversation_id")
    assert conv_id is not None, f"conversation_id ausente no batch_text: {payload}"

    # O id publicado tem de ser o da linha REALMENTE salva — não basta vir preenchido.
    contact = contact_repo.get_by_phone(phone)
    assert contact
    rows = [m for m in message_repo.get_all(contact["id"]) if m["role"] == "user"]
    assert rows, "mensagem inbound não persistida"
    assert rows[-1]["conversation_id"] == conv_id


def test_message_sent_carries_channel_and_conversation(build_app, tmp_path):
    """Envio do operador pela rota real ⇒ mesma rota publicada no bus."""
    probe = _write_probe_plugin(tmp_path / "probe_sent")
    built = build_app(
        ["gowa", PROBE_ID],
        plugin_sources={PROBE_ID: probe},
        settings_overrides={"auto_reply": False, "message_batch_delay": 0},
    )

    phone = "5511977230012"
    with _bus_wired(built):
        # Materializa o contato/conversa com um inbound antes de responder.
        built.client.post("/api/webhook/gowa/default", json={
            "event": "message", "payload": {
                "from": f"{phone}@s.whatsapp.net", "id": "p123_sent_1",
                "body": "oi", "from_name": "Cliente"}})
        _drain(built, "default", phone)
        _reset_seen()

        r = built.client.post(f"/api/contacts/{phone}/send",
                              json={"message": "resposta do operador"})
        assert r.status_code == 200, r.text
        _drain(built, "default", phone)

    payload = _pick(_seen(), "message.sent", "operator")
    assert payload is not None, f"message.sent(operator) não emitido: {_seen()}"
    assert payload.get("channel_id") == "default", f"payload={payload}"
    assert payload.get("conversation_id") is not None, (
        f"conversation_id ausente no envio do operador: {payload}")


def test_conversation_id_may_be_absent_but_channel_id_never_is(build_app, tmp_path):
    """Contrato para o consumidor: ``conversation_id`` é opcional, ``channel_id`` não.

    O retry e a resposta da IA emitem sem o id de conversa resolvido no escopo —
    campo ausente é melhor que valor errado. Este teste congela a assimetria para
    que um plugin escrito contra 1.3.0 saiba o que pode exigir.
    """
    probe = _write_probe_plugin(tmp_path / "probe_contract")
    built = build_app(
        ["gowa", PROBE_ID],
        plugin_sources={PROBE_ID: probe},
        settings_overrides={"auto_reply": False, "message_batch_delay": 0},
    )
    _reset_seen()

    phone = "5511977230013"
    with _bus_wired(built):
        built.client.post("/api/webhook/gowa/default", json={
            "event": "message", "payload": {
                "from": f"{phone}@s.whatsapp.net", "id": "p123_contract_1",
                "body": "oi", "from_name": "Cliente"}})
        _drain(built, "default", phone)
        built.client.post(f"/api/contacts/{phone}/send", json={"message": "resposta"})
        _drain(built, "default", phone)

    seen = _seen()
    assert seen, "nenhum evento capturado"
    for name, payload in seen:
        assert "channel_id" in payload, (
            f"{name}(source={payload.get('source')}) sem channel_id — "
            "todo site de message.saved/sent tem o canal no escopo")
