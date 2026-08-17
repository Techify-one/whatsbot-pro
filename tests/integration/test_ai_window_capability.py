"""A janela da IA é uma TERCEIRA janela, e o painel precisa enxergá-la.

Bug de produção (canais Meta): com o toggle ``human_agent_tag`` ligado, o
compositor do atendente fica aberto por 7 dias (``human_window_hours``, a tag
HUMAN_AGENT) enquanto o ``filters.py`` do plugin já calou a IA às 24h — devolve
``None`` em ``filter.llm.messages`` e aborta o turno antes do LLM. No intervalo
entre as duas janelas o painel continuava oferecendo os toggles "IA lê" e "IA
responde no chat" da nota privada: o atendente escrevia a instrução e **nada
acontecia**, sem card, sem erro, sem nada no fio.

A propriedade que estes testes travam é justamente a DIVERGÊNCIA: no mesmo
instante, para o mesmo canal, ``session_open`` (visão do operador) é ``True`` e
``ai_window_open`` (visão da IA) é ``False``. Derivar uma da outra reintroduz o
bug — por isso a capability é um campo próprio, e não um `max`/`min` dos outros.

Quem CALA a IA continua sendo o plugin, no filtro; o core só compara carimbos
para o painel parar de oferecer o que vai ser descartado.

    venv/bin/python -m pytest tests/integration/test_ai_window_capability.py -q
"""

from __future__ import annotations

import time

from channels.base import ChannelCapabilities
from channels.outbound import OutboundRouter
from db.repositories import (channel_repo, contact_repo, inbox_repo,
                             message_repo)
from tests.fake_provider import FakeChannel


DAY = 24 * 3600

# Um canal Meta como o Messenger/Instagram com a tag HUMAN_AGENT LIGADA: texto
# livre por 24h para todos, até 7 dias para o atendente humano, IA calada às 24h.
META_CAPS = ChannelCapabilities(
    media=True, presence=True, reactions=True,
    session_window_hours=24, human_window_hours=24 * 7, ai_window_hours=24,
)


def _register(app, channel_id: str, caps: ChannelCapabilities, provider: str):
    """Registra provider + instância viva + row + inbox, como uma operação real."""
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
    """Contato + conversa no canal, com UM inbound de ``inbound_age`` atrás.

    A materialização passa pelo ``ContactMemory`` (que resolve contact_inbox +
    conversa do CANAL certo); só o inbound é inserido à mão, com o carimbo no
    passado — é ele que ``last_inbound_ts`` lê para medir as janelas."""
    mem = app.state.deps.agent_handler._get_contact(phone, channel_id=channel_id)
    seeded = mem.add_message("assistant", "seed")   # não é inbound: não abre janela
    contact = contact_repo.get_by_phone(phone)
    message_repo.add(contact["id"], "user", "oi",
                     conversation_id=seeded["conversation_id"],
                     ts=time.time() - inbound_age)
    return contact, seeded["conversation_id"]


# ── O avaliador do core ─────────────────────────────────────────────────────

def test_ai_window_open_avalia_a_capability(app):
    """0 = sem restrição; >0 conta do último inbound; sem inbound = fechada."""
    _register(app, "aiw_meta", META_CAPS, "aiw_meta_provider")
    _register(app, "aiw_livre", ChannelCapabilities(media=True), "aiw_livre_provider")
    router: OutboundRouter = app.state.deps.outbound_router
    agora = time.time()

    assert router.ai_window_open("aiw_meta", agora - 3600) is True
    assert router.ai_window_open("aiw_meta", agora - 3 * DAY) is False
    # Sem inbound nenhum a janela nunca abriu — mesma leitura de ``session_open``.
    assert router.ai_window_open("aiw_meta", None) is False

    # ``ai_window_hours == 0``: o turno da IA sempre roda (GOWA, Telegram, Cloud).
    # Inclusive sem inbound — é o caso do disparo proativo.
    assert router.ai_window_open("aiw_livre", None) is True
    assert router.ai_window_open("aiw_livre", agora - 30 * DAY) is True

    # Canal desconhecido cai nas capabilities vazias ⇒ sem restrição (fail-open).
    assert router.ai_window_open("aiw_inexistente", None) is True


def test_janela_da_ia_fecha_antes_da_do_atendente(app):
    """O CERNE: no 3º dia o operador ainda fala e a IA já não. Duas respostas
    diferentes para o mesmo canal e o mesmo instante — se um dia estas duas
    asserções passarem a concordar, o painel volta a oferecer instrução para uma
    IA calada."""
    _register(app, "aiw_diverge", META_CAPS, "aiw_diverge_provider")
    router: OutboundRouter = app.state.deps.outbound_router
    tres_dias = time.time() - 3 * DAY

    assert router.session_open("aiw_diverge", tres_dias, by_human=True) is True
    assert router.ai_window_open("aiw_diverge", tres_dias) is False


# ── O sinal chega ao painel ─────────────────────────────────────────────────

def test_payload_da_conversa_carrega_ai_window_open(app, client):
    """``GET /api/atendimentos/{id}/messages`` — é este payload que o compositor
    lê para decidir se mostra os toggles de instrução para a IA."""
    _register(app, "aiw_conv", META_CAPS, "aiw_conv_provider")
    _contact, conv_id = _seed_conversation(app, "5511900000101", "aiw_conv",
                                           inbound_age=3 * DAY)

    data = client.get(f"/api/atendimentos/{conv_id}/messages"
                      "?mark_read=false").json()["data"]

    assert data["ai_window_open"] is False
    # O compositor do atendente continua ABERTO no mesmo payload — é essa
    # combinação que o bug produzia e que a UI agora sabe distinguir.
    assert data["session_open"] is True


def test_payload_do_contato_carrega_ai_window_open(app, client):
    """``GET /api/contacts/{phone}?channel_id=`` — a outra porta de entrada do
    compositor (conversa ainda não criada / caixa de entrada escolhida)."""
    _register(app, "aiw_contact", META_CAPS, "aiw_contact_provider")
    phone = "5511900000102"
    _seed_conversation(app, phone, "aiw_contact", inbound_age=2 * DAY)

    data = client.get(f"/api/contacts/{phone}?channel_id=aiw_contact").json()["data"]

    assert data["ai_window_open"] is False
    assert data["session_open"] is True


def test_canal_sem_a_capability_nao_muda_em_nada(app, client):
    """Retrocompatibilidade: provider que não declara ``ai_window_hours`` (GOWA,
    Telegram, WhatsApp Cloud) reporta a janela da IA aberta, e o compositor fica
    exatamente como sempre foi."""
    _register(app, "aiw_legado", ChannelCapabilities(media=True),
              "aiw_legado_provider")
    phone = "5511900000103"
    _seed_conversation(app, phone, "aiw_legado", inbound_age=30 * DAY)

    data = client.get(f"/api/contacts/{phone}?channel_id=aiw_legado").json()["data"]

    assert data["ai_window_open"] is True
