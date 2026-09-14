"""A janela deixou de ser um booleano: o painel precisa saber QUANDO ela fecha.

Plano 159 F1. O compositor mostra uma contagem regressiva ("faltam 5h 12min para
o cliente sair da janela de texto livre"), e para isso precisa de dois números
que até aqui só existiam no servidor: o carimbo do ÚLTIMO INBOUND e o TAMANHO
EFETIVO da janela do atendente.

Duas propriedades são travadas aqui:

1. **O tamanho é o EFETIVO, não o declarado.** Um canal Meta com a tag
   HUMAN_AGENT ligada dá 7 dias ao atendente (``human_window_hours``) e 24h a
   todo o resto. Exportar ``session_window_hours`` cru faria o painel contar 24h
   e anunciar o fim da janela no 1º dia enquanto o envio seguia passando por mais
   seis — painel e rota discordando sobre a mesma regra, que é exatamente o bug
   que o ``by_human=True`` do ``session_open`` já existia para evitar.

2. **A conta é UMA só.** O número exportado sai de ``OutboundRouter.window_hours``,
   o mesmo helper de onde o ``session_open`` tira o seu — nunca de um
   ``max(...)`` recalculado na rota.

``0`` (GOWA/Telegram: sem janela) é um valor VÁLIDO e significa "não exiba
contagem nenhuma"; não confundir com ausência do campo.

    venv/bin/python -m pytest tests/integration/test_session_window_countdown.py -q
"""

from __future__ import annotations

import time

from channels.base import ChannelCapabilities
from channels.outbound import OutboundRouter
from db.repositories import (channel_repo, contact_repo, inbox_repo,
                             message_repo)
from tests.fake_provider import FakeChannel


DAY = 24 * 3600

# Meta com a tag HUMAN_AGENT ligada: 24h para todos, 7 dias para o atendente.
META_CAPS = ChannelCapabilities(
    media=True, presence=True, reactions=True,
    session_window_hours=24, human_window_hours=24 * 7, ai_window_hours=24,
)
# WhatsApp Cloud: 24h e nenhuma extensão para o humano.
CLOUD_CAPS = ChannelCapabilities(media=True, session_window_hours=24)


def _register(app, channel_id: str, caps: ChannelCapabilities, provider: str):
    registry = app.state.deps.channel_registry
    cls = FakeChannel.configured(provider=provider, capabilities=caps)
    registry.register_provider(cls)
    inst = cls(channel_id)
    registry.add_channel(channel_id, inst)
    if channel_repo.get(channel_id) is None:
        channel_repo.create(id=channel_id, provider=provider, display_name=channel_id)
    inbox_repo.get_or_create_for_channel(channel_id, name=channel_id)
    return inst


def _seed_conversation(app, phone: str, channel_id: str, *, inbound_age: float):
    """Contato + conversa no canal, com UM inbound de ``inbound_age`` atrás."""
    mem = app.state.deps.agent_handler._get_contact(phone, channel_id=channel_id)
    seeded = mem.add_message("assistant", "seed")   # não é inbound
    contact = contact_repo.get_by_phone(phone)
    ts = time.time() - inbound_age
    message_repo.add(contact["id"], "user", "oi",
                     conversation_id=seeded["conversation_id"], ts=ts)
    return contact, seeded["conversation_id"], ts


# ── O helper do core ────────────────────────────────────────────────────────

def test_window_hours_devolve_a_janela_efetiva(app):
    """``by_human=True`` ⇒ o MAIOR entre a janela do canal e a extensão humana."""
    _register(app, "swc_meta", META_CAPS, "swc_meta_provider")
    _register(app, "swc_cloud", CLOUD_CAPS, "swc_cloud_provider")
    _register(app, "swc_livre", ChannelCapabilities(media=True), "swc_livre_provider")
    router: OutboundRouter = app.state.deps.outbound_router

    # A extensão do atendente VENCE — 7 dias, não as 24h declaradas.
    assert router.window_hours("swc_meta", by_human=True) == 24 * 7
    # Sem o flag (visão da IA / envio automático) continuam sendo 24h.
    assert router.window_hours("swc_meta") == 24

    # Canal sem extensão: os dois caminhos dão o mesmo número.
    assert router.window_hours("swc_cloud", by_human=True) == 24
    assert router.window_hours("swc_cloud") == 24

    # Canal SEM janela (GOWA/Telegram) e canal desconhecido: 0 = não há contagem.
    assert router.window_hours("swc_livre", by_human=True) == 0
    assert router.window_hours("swc_inexistente", by_human=True) == 0


def test_session_open_continua_concordando_com_o_helper(app):
    """A extração não pode ter mudado o veredito: o booleano e o tamanho saem da
    MESMA conta. Se um dia divergirem, o compositor anuncia um fim de janela que
    o envio não respeita (ou o contrário)."""
    _register(app, "swc_sync", META_CAPS, "swc_sync_provider")
    router: OutboundRouter = app.state.deps.outbound_router
    agora = time.time()

    for idade in (1 * 3600, 3 * DAY, 8 * DAY):
        horas = router.window_hours("swc_sync", by_human=True)
        esperado = idade < horas * 3600
        assert router.session_open("swc_sync", agora - idade, by_human=True) is esperado


# ── Os dois campos chegam ao painel ─────────────────────────────────────────

def test_payload_da_conversa_carrega_a_janela(app, client):
    """``GET /api/atendimentos/{id}/messages`` — a porta do compositor."""
    _register(app, "swc_conv", META_CAPS, "swc_conv_provider")
    _c, conv_id, ts = _seed_conversation(app, "5511900000201", "swc_conv",
                                         inbound_age=3 * DAY)

    data = client.get(f"/api/atendimentos/{conv_id}/messages"
                      "?mark_read=false").json()["data"]

    # 7 dias, não 24h: é a janela que ESTE payload (o do operador) obedece.
    assert data["session_window_hours"] == 24 * 7
    assert data["last_inbound_ts"] == pytest_approx(ts)
    assert data["session_open"] is True


def test_payload_do_contato_carrega_a_janela(app, client):
    """``GET /api/contacts/{phone}?channel_id=`` — a outra porta do compositor."""
    _register(app, "swc_contact", CLOUD_CAPS, "swc_contact_provider")
    phone = "5511900000202"
    _c, _conv, ts = _seed_conversation(app, phone, "swc_contact", inbound_age=2 * 3600)

    data = client.get(f"/api/contacts/{phone}?channel_id=swc_contact").json()["data"]

    assert data["session_window_hours"] == 24
    assert data["last_inbound_ts"] == pytest_approx(ts)
    assert data["session_open"] is True


def test_canal_sem_janela_reporta_zero(app, client):
    """GOWA/Telegram: o campo VEM, valendo ``0`` — o painel não exibe contagem.
    Omitir o campo seria indistinguível de um core anterior."""
    _register(app, "swc_legado", ChannelCapabilities(media=True),
              "swc_legado_provider")
    phone = "5511900000203"
    _seed_conversation(app, phone, "swc_legado", inbound_age=30 * DAY)

    data = client.get(f"/api/contacts/{phone}?channel_id=swc_legado").json()["data"]

    assert data["session_window_hours"] == 0
    assert data["session_open"] is True


def test_conversa_sem_inbound_tem_carimbo_nulo(app, client):
    """Conversa que só teve saída: a janela nunca abriu. O carimbo é ``None`` e o
    painel não tem o que contar (a faixa não aparece)."""
    _register(app, "swc_semin", CLOUD_CAPS, "swc_semin_provider")
    phone = "5511900000204"
    mem = app.state.deps.agent_handler._get_contact(phone, channel_id="swc_semin")
    conv_id = mem.add_message("assistant", "so saida")["conversation_id"]

    data = client.get(f"/api/atendimentos/{conv_id}/messages"
                      "?mark_read=false").json()["data"]

    assert data["last_inbound_ts"] is None
    assert data["session_window_hours"] == 24
    assert data["session_open"] is False


def pytest_approx(value: float, tol: float = 1.0):
    """``ts`` é ``double precision`` e volta do Postgres com ruído de ponto
    flutuante — comparar com folga de 1s em vez de igualdade exata."""
    class _Approx:
        def __eq__(self, other):
            return other is not None and abs(float(other) - value) <= tol

        def __repr__(self):
            return f"~{value}"
    return _Approx()
