"""Seam ``filter.provisioning.number`` — o destino do provisionamento é plugável.

O wizard de 1ª execução manda a frase de provisionamento para um número que o core
resolvia sem alavanca em runtime: ``GET /service_number`` da Techify, caindo num
literal embutido no código. Este seam (API 1.7.0) deixa um plugin apontar o envio
para outro número — é o que o ``criar_conta`` usa para mandar o pedido de conta ao
próprio atendimento — ou RECUSAR o envio quando ninguém configurou destino.

Os testes exercitam o CORE direto (sem plugin): registram um filtro em processo e
chamam ``fetch_provision_number`` com a ida ao ``/service_number`` mockada. O que está travado aqui é o contrato do
seam: ``None``/``""`` ABORTA — não há destino, e o core recusa o envio em vez de
mandar a frase para o número que ele por acaso conhecia. O core também não tem
mais número embutido (``TECHIFY_PROVISION_NUMBER`` nasce vazia) nem opina sobre o
formato do que volta.

    venv/bin/python -m pytest tests/contracts/test_provisioning_number_filter.py -q
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from app.services import provisioning_service as svc
from plugins import events as bus

FILTER = "filter.provisioning.number"
REMOTE = "5511111111111"
ENV_FALLBACK = svc.TECHIFY_PROVISION_NUMBER


def _resolve() -> str:
    """``fetch_provision_number`` é async; a suíte do core não usa pytest-asyncio."""
    return asyncio.run(svc.fetch_provision_number())


@pytest.fixture
def registered():
    """Registra filtros e limpa o bus no fim (o registry é global do processo)."""
    added: list = []

    def _register(fn, priority: int = 100):
        bus.register_filter("test_provisioning", FILTER, fn, priority)
        added.append(fn)
        return fn

    yield _register
    bus._filters.pop(FILTER, None)


@pytest.fixture
def remote_ok(monkeypatch):
    """``/service_number`` responde com um número — o caminho feliz do core."""
    _fake_service_number(monkeypatch, {"phone": REMOTE})


@pytest.fixture
def remote_down(monkeypatch):
    """``/service_number`` fora do ar — o core cai na env."""
    class _Boom:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): raise RuntimeError("sem rede")
    monkeypatch.setattr(svc.httpx, "AsyncClient", lambda *a, **k: _Boom())


def _fake_service_number(monkeypatch, body: dict):
    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return body

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _Resp()

    monkeypatch.setattr(svc.httpx, "AsyncClient", lambda *a, **k: _Client())


# ── sem filtro ───────────────────────────────────────────────────────────────
def test_sem_filtro_usa_o_numero_remoto(remote_ok):
    assert _resolve() == REMOTE


def test_sem_filtro_e_sem_rede_cai_na_env(remote_down):
    """A env é o único fallback que sobrou — e nasce VAZIA."""
    assert _resolve() == ENV_FALLBACK


def test_o_core_nao_tem_numero_embutido():
    """Nenhum destino escrito no código: sem env, sem rede e sem filtro, ninguém
    envia nada. Um literal aqui é um número que ninguém escolheu."""
    assert svc.TECHIFY_PROVISION_NUMBER == "" or os.environ.get("TECHIFY_PROVISION_NUMBER")


# ── o override ───────────────────────────────────────────────────────────────
def test_filtro_troca_o_destino(remote_ok, registered):
    registered(lambda ctx, number: "5599888887777")
    assert _resolve() == "5599888887777"


def test_override_vale_tambem_quando_o_endpoint_esta_fora(remote_down, registered):
    """O seam roda DEPOIS da resolução, então cobre os dois caminhos do core."""
    registered(lambda ctx, number: "5599888887777")
    assert _resolve() == "5599888887777"


def test_o_core_nao_normaliza_o_que_o_filtro_devolve(remote_ok, registered):
    """Formato é de quem responde, não do core.

    O valor sempre foi usado cru (era assim com o do ``/service_number``), e
    normalizar aqui seria política de telefone no core. Quem garante dígitos é o
    plugin — travado no ``test_destino_aceita_formatacao_e_sai_so_com_digitos``
    da suíte do ``criar_conta``.
    """
    registered(lambda ctx, number: "+55 (99) 88888-7777")
    assert _resolve() == "+55 (99) 88888-7777"


def test_extras_dizem_de_onde_veio_o_numero_oferecido(remote_ok, registered):
    seen: dict = {}

    def _spy(ctx, number):
        seen["number"] = number
        seen["extras"] = dict(getattr(ctx, "extras", None) or {})
        return number

    registered(_spy)
    _resolve()
    assert seen["number"] == REMOTE
    assert seen["extras"]["source"] == "service_number"
    assert seen["extras"]["message"] == svc.TECHIFY_PROVISION_MESSAGE


def test_extras_marcam_o_fallback_da_env(remote_down, registered):
    seen: dict = {}
    registered(lambda ctx, number: seen.update(
        number=number, source=(getattr(ctx, "extras", None) or {}).get("source")) or number)
    _resolve()
    assert seen == {"number": ENV_FALLBACK, "source": "fallback"}


# ── o aborto: sem destino, ninguém envia ─────────────────────────────────────
@pytest.mark.parametrize("devolvido", [None, "", "   "])
def test_valor_vazio_significa_sem_destino(remote_ok, registered, devolvido):
    """``None`` ABORTA. É o caso do campo em branco: o operador não escolheu
    destino, e o número que o core resolveu NÃO serve de reserva."""
    registered(lambda ctx, number: devolvido)
    assert _resolve() == ""


def test_aborto_vence_o_numero_que_o_core_resolveu(remote_ok, registered):
    """A ordem importa: o filtro tem a última palavra, mesmo com /service_number
    respondendo. Sem isto, "em branco" viraria "manda para a Techify"."""
    registered(lambda ctx, number: None)
    assert REMOTE and _resolve() == ""


def test_filtro_que_levanta_deixa_o_valor_do_core_passar(remote_ok, registered):
    """Contrato do bus, não deste seam: ``apply_filter`` isola a exceção e o valor
    segue intacto — o core não põe rede própria em volta. Quem quer fail-closed
    captura a própria exceção e devolve ``None``."""
    def _boom(ctx, number):
        raise RuntimeError("plugin quebrado")

    registered(_boom)
    assert _resolve() == REMOTE


def test_cadeia_respeita_prioridade(remote_ok, registered):
    registered(lambda ctx, number: "5500000000000", priority=10)
    registered(lambda ctx, number: number + "9", priority=20)
    assert _resolve() == "55000000000009"


def test_o_nome_esta_no_catalogo():
    """Seam nasce com o produtor: nome fora do catálogo vira WARNING no plugin."""
    assert FILTER in bus.KNOWN_FILTERS


# ── o efeito que importa: sem destino, o wizard não manda nada ───────────────

def _deps_espiao():
    """``deps`` mínimo para ``request_key``, com TUDO que não deveria acontecer
    armado para explodir: envio, materialização do contato e polling."""
    enviados = []

    class _Gowa:
        def get_own_number(self):
            return "5511999999999"

        def send_message(self, phone, text):
            enviados.append((phone, text))
            return {"ok": True}

    class _Handler:
        def _get_contact(self, phone, **kw):
            raise AssertionError(
                f"contato {phone!r} materializado sem destino de provisionamento")

    state = SimpleNamespace(bot_phone="5511999999999", setup_key_number=None,
                            setup_key_requested_at=None)
    return SimpleNamespace(gowa_client=_Gowa(), agent_handler=_Handler(),
                           state=state), enviados


def test_sem_destino_a_mensagem_nao_e_enviada(remote_ok, registered):
    """Campo em branco no plugin ⇒ ``None`` no filtro ⇒ NADA sai.

    E nada de meio-caminho: sem contato materializado (seria um contato fantasma
    com telefone vazio) e sem polling armado (o wizard giraria até o TTL
    esperando uma chave que ninguém pediu).
    """
    registered(lambda ctx, number: None)
    deps, enviados = _deps_espiao()

    kind, data = asyncio.run(svc.request_key(deps))

    assert kind == "no_destination"
    assert data == {}
    assert enviados == [], "mensagem de provisionamento saiu sem destino configurado"
    assert deps.state.setup_key_number is None
    assert deps.state.setup_key_requested_at is None


def test_com_destino_a_mensagem_sai_para_o_numero_configurado(remote_ok, registered):
    """O contraste do teste acima: com o campo preenchido, sai — e sai para ELE."""
    registered(lambda ctx, number: "5599888887777")
    deps, enviados = _deps_espiao()
    deps.agent_handler = SimpleNamespace()  # _get_contact ausente ⇒ o service loga e segue

    kind, data = asyncio.run(svc.request_key(deps))

    assert kind == "sent"
    assert enviados == [("5599888887777", svc.TECHIFY_PROVISION_MESSAGE)]
    assert deps.state.setup_key_number == "5511999999999"


def test_a_rota_devolve_erro_acionavel_sem_destino():
    """O operador precisa saber POR QUE não foi — silêncio aqui é o bug antigo."""
    import inspect

    from server.routes import setup as setup_routes

    fonte = inspect.getsource(setup_routes.register_routes)
    assert "no_destination" in fonte
    assert "Nenhum número de destino configurado" in fonte
