"""Seams ``filter.provisioning.number`` e ``filter.provisioning.message``.

O wizard de 1ª execução manda uma frase de provisionamento para um número. Os dois
lados desse par são resolvidos CAMPO A CAMPO pelo core — ``GET /service_number``
(fonte da verdade, devolve ``{phone, message}``) → env → literal do código — e
cada um é então oferecido ao seu filtro, que tem a ÚLTIMA palavra (API 1.7.0 para
o número, 1.8.0 para a mensagem).

Os testes exercitam o CORE direto (sem plugin): registram filtros em processo e
chamam ``fetch_provision_target`` com a ida ao ``/service_number`` mockada. O que
está travado aqui:

* ``None``/``""`` em qualquer um dos dois ABORTA — o core recusa o envio em vez de
  mandar a frase para um número que ninguém escolheu, ou uma mensagem vazia para
  ele;
* os campos são INDEPENDENTES — endpoint que só devolve ``phone`` não derruba a
  frase, e vice-versa;
* existe fallback embutido nos DOIS (a rede para o endpoint fora do ar);
* o core não opina sobre o formato do que volta.

    venv/bin/python -m pytest tests/contracts/test_provisioning_number_filter.py -q
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services import provisioning_service as svc
from plugins import events as bus

FILTER = "filter.provisioning.number"
MSG_FILTER = "filter.provisioning.message"
REMOTE = "5511111111111"
REMOTE_MSG = "Frase publicada no endpoint"
ENV_FALLBACK = svc.TECHIFY_PROVISION_NUMBER
MSG_FALLBACK = svc.TECHIFY_PROVISION_MESSAGE


def _resolve() -> str:
    """``fetch_provision_number`` é async; a suíte do core não usa pytest-asyncio."""
    return asyncio.run(svc.fetch_provision_number())


def _resolve_target() -> svc.ProvisionTarget:
    return asyncio.run(svc.fetch_provision_target())


@pytest.fixture
def registered():
    """Registra filtros e limpa o bus no fim (o registry é global do processo)."""
    added: list = []

    def _register(fn, priority: int = 100, name: str = FILTER):
        bus.register_filter("test_provisioning", name, fn, priority)
        added.append(fn)
        return fn

    yield _register
    bus._filters.pop(FILTER, None)
    bus._filters.pop(MSG_FILTER, None)


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


def test_o_core_tem_rede_embutida_para_os_dois_campos():
    """Fallback no código é DELIBERADO (API 1.8.0, revertendo a 1.7.0).

    Sem ele, uma queda do ``/service_number`` parava o provisionamento de todo
    cliente novo — quem acabou de conectar o QR não tem env, não tem plugin e não
    teria como pedir a própria chave. O literal não é a alavanca de mudança (essa
    é o endpoint); é a última rede.
    """
    assert svc.TECHIFY_PROVISION_NUMBER, "número sem fallback embutido"
    assert svc.TECHIFY_PROVISION_MESSAGE, "mensagem sem fallback embutido"


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


# ── a mensagem: mesmo par, mesmas regras ─────────────────────────────────────
@pytest.fixture
def remote_full(monkeypatch):
    """``/service_number`` devolve os DOIS campos — o contrato publicado."""
    _fake_service_number(monkeypatch, {"ok": True, "phone": REMOTE,
                                       "message": REMOTE_MSG})


def test_endpoint_dita_numero_e_mensagem(remote_full):
    """A fonte da verdade: trocar qualquer um dos dois é editar a resposta dele."""
    alvo = _resolve_target()
    assert (alvo.number, alvo.message) == (REMOTE, REMOTE_MSG)


def test_campos_sao_independentes_endpoint_so_com_numero(remote_ok):
    """Resposta legada (só ``phone``) NÃO derruba a frase: ela cai no fallback.

    É o que mantém o provisionamento de pé enquanto o campo novo não é publicado.
    """
    alvo = _resolve_target()
    assert (alvo.number, alvo.message) == (REMOTE, MSG_FALLBACK)


def test_campos_sao_independentes_endpoint_so_com_mensagem(monkeypatch):
    """E o simétrico: só ``message`` publicado ⇒ número no fallback."""
    _fake_service_number(monkeypatch, {"ok": True, "message": REMOTE_MSG})
    alvo = _resolve_target()
    assert (alvo.number, alvo.message) == (ENV_FALLBACK, REMOTE_MSG)


def test_endpoint_fora_do_ar_cai_nos_dois_literais(remote_down):
    """O requisito que trouxe o fallback de volta (1.8.0): endpoint fora do ar e
    o pedido de conta sai igual, com número E frase embutidos."""
    alvo = _resolve_target()
    assert alvo.number == ENV_FALLBACK and alvo.number
    assert alvo.message == MSG_FALLBACK and alvo.message


def test_filtro_troca_a_mensagem(remote_full, registered):
    registered(lambda ctx, msg: "Outra frase", name=MSG_FILTER)
    alvo = _resolve_target()
    assert alvo.message == "Outra frase"
    assert alvo.number == REMOTE, "trocar a frase não pode mexer no destino"


def test_filtro_de_mensagem_recebe_o_numero_ja_decidido(remote_full, registered):
    """A ordem é o ponto: quem reescreve a frase precisa saber para QUEM ela vai —
    é assim que um plugin manda o gatilho que aquele destino reconhece."""
    seen: dict = {}

    def _spy(ctx, msg):
        seen["message"] = msg
        seen["extras"] = dict(getattr(ctx, "extras", None) or {})
        return msg

    registered(lambda ctx, number: "5599888887777")
    registered(_spy, name=MSG_FILTER)
    _resolve_target()
    assert seen["message"] == REMOTE_MSG
    assert seen["extras"]["source"] == "service_number"
    assert seen["extras"]["number"] == "5599888887777"


def test_o_filtro_de_numero_ja_ve_a_frase_final(remote_full, registered):
    """O contrário do teste acima: a mensagem é resolvida ANTES, então quem
    decide o destino enxerga a frase que de fato sairá."""
    seen: dict = {}
    registered(lambda ctx, number: seen.update(
        message=(getattr(ctx, "extras", None) or {}).get("message")) or number)
    _resolve_target()
    assert seen["message"] == REMOTE_MSG


@pytest.mark.parametrize("devolvido", [None, "", "   "])
def test_mensagem_vazia_e_um_aborto(remote_full, registered, devolvido):
    registered(lambda ctx, msg: devolvido, name=MSG_FILTER)
    assert _resolve_target().message == ""


def test_sem_destino_o_filtro_de_mensagem_nem_roda(remote_full, registered):
    """Envio já morreu no número: perguntar a frase seria trabalho para o lixo —
    e um plugin com efeito colateral no filtro rodaria à toa."""
    chamou = []
    registered(lambda ctx, number: None)
    registered(lambda ctx, msg: chamou.append(msg) or msg, name=MSG_FILTER)
    alvo = _resolve_target()
    assert (alvo.number, alvo.message) == ("", "")
    assert chamou == []


def test_o_nome_da_mensagem_esta_no_catalogo():
    assert MSG_FILTER in bus.KNOWN_FILTERS


# ── o efeito: sem frase, o wizard também não manda nada ──────────────────────
def test_sem_mensagem_nada_e_enviado(remote_full, registered):
    """Mensagem vazia queimaria a única abertura de conversa que o WhatsApp
    concede com um contato novo (o reach-out timelock é por contato)."""
    registered(lambda ctx, msg: None, name=MSG_FILTER)
    deps, enviados = _deps_espiao()

    kind, data = asyncio.run(svc.request_key(deps))

    assert kind == "no_message"
    assert data == {}
    assert enviados == []
    assert deps.state.setup_key_number is None
    assert deps.state.setup_key_requested_at is None


def test_a_frase_enviada_e_a_do_endpoint(remote_full):
    """O caminho feliz de ponta a ponta: o que a Cloudflare publicou é o que sai
    no wire — sem passar pelo literal do código."""
    deps, enviados = _deps_espiao()
    deps.agent_handler = SimpleNamespace()  # _get_contact ausente ⇒ loga e segue

    kind, _ = asyncio.run(svc.request_key(deps))

    assert kind == "sent"
    assert enviados == [(REMOTE, REMOTE_MSG)]


def test_fallback_manual_mostra_a_frase_resolvida(remote_full):
    """Bloqueio anti-spam do WhatsApp: o painel pede que o operador mande a frase
    à mão. É o ÚNICO ponto em que ele vê o par — tem de ser o resolvido, não o
    literal, senão ele copia um gatilho que o destino não reconhece."""
    from gowa.client import GOWASendError

    deps, _ = _deps_espiao()
    deps.agent_handler = SimpleNamespace()

    def _blocked(phone, text):
        raise GOWASendError("bloqueado", error_type="reachout_timelock")

    deps.gowa_client.send_message = _blocked

    kind, data = asyncio.run(svc.request_key(deps))

    assert kind == "manual"
    assert data["provision_number"] == REMOTE
    assert data["provision_message"] == REMOTE_MSG
    assert data["wa_link"].startswith(f"https://wa.me/{REMOTE}?text=")
    assert deps.state.setup_key_number, "polling precisa continuar armado"


def test_a_rota_devolve_erro_acionavel_sem_mensagem():
    import inspect

    from server.routes import setup as setup_routes

    fonte = inspect.getsource(setup_routes.register_routes)
    assert "no_message" in fonte
    assert "Nenhuma mensagem de provisionamento configurada" in fonte
