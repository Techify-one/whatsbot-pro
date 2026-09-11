"""Trilha do alerta de desconexão do `gowa`, e o GET que escrevia (plano 148 §4.10).

Este arquivo trava DUAS correções diferentes na mesma tela ("Configurar" do canal
WhatsApp (GOWA) → alerta de desconexão via Telegram), e a diferença entre elas é
o ponto do plano:

1. **AUDITORIA** — o botão "Testar alerta" (``POST /alert-test``) não só testava:
   quando o grupo do Telegram é promovido a **supergrupo**, a Bot API recusa o
   envio e devolve o id novo em ``parameters.migrate_to_chat_id``. A rota
   persistia esse id novo em ``plugin.gowa.disconnect_alert_chat_id`` e reenviava
   — ou seja, **mudava para onde TODO alerta de desconexão passa a ir** — sem
   uma linha na trilha. A última linha ``gowa.alerta.config`` continuava exibindo
   o chat antigo, então a Auditoria respondia a pergunta "quem mudou o destino
   dos alertas?" com o valor errado. Agora grava ``gowa.alerta.chat_id_migrado``.

   ⚠️ **São DOIS call sites, e o do painel é o RARO.** O mesmo
   ``config_repo.set`` existe no loop de fundo (``alerts._tg_call``), que é onde
   a promoção a supergrupo costuma ser descoberta — o alerta reenvia a cada
   ``interval_min`` enquanto o número está fora do ar. Cobrir só a rota faria a
   trilha exibir a ação com zero linhas do caminho real, e um auditor leria isso
   como "o destino nunca mudou". O loop grava a **mesma ação** com o ator
   forçado a ``system`` (não há humano na request).

2. **DESENHO, NÃO AUDITORIA** — o ``GET /alert-settings`` gravava o fuso do
   navegador (query ``?tz=``) em ``plugin.gowa.disconnect_alert_timezone_auto``.
   Só de ABRIR a aba, o horário exibido em todo alerta mudava, sem o operador
   salvar nada. A correção **não** é auditar o GET (isso viraria log de
   navegação, R4 do plano): é **tirar a escrita**. O fuso detectado continua
   voltando no corpo como ``timezone_auto``, a tela pré-preenche o seletor com
   ele e o valor entra em ``disconnect_alert_timezone`` pelo **PUT**, que já é
   auditado — e agora o ``_alert_audit_view`` carrega também ``timezone_auto``,
   sem o que o diff do PUT esconde qual horário o alerta passa a usar.

   ⚠️ **Tirar a escrita quebrou o ``timezone_effective`` da resposta.** Enquanto
   o GET persistia o fuso do navegador, "o que a tela sugere" e "o que o alerta
   usa" coincidiam por acidente; sem a escrita, a resposta passou a jurar
   ``America/Manaus`` enquanto o loop imprimia ``America/Sao_Paulo``. O campo
   agora espelha ``alerts._resolve_tz_name()`` (só o que está SALVO) e
   ``timezone_auto`` continua sendo a SUGESTÃO do seletor — o teste ancora um no
   outro para o par não voltar a divergir. A tela ganhou o único aviso desta
   frente: "ainda não salvo — os alertas usam X até você clicar em Salvar",
   exibido só enquanto o seletor mostra algo diferente do que está em vigor.

⚠️ **O ramo legado em ``alerts._resolve_tz_name`` continua de pé.** Nenhuma rota
grava mais ``disconnect_alert_timezone_auto``, mas a instalação que já tem o
valor gravado continua honrando-o. Removê-lo faria essas instalações pularem
para Brasília sem ninguém pedir. O teste ``test_o_fuso_legado_continua_valendo``
existe para que "limpeza de código morto" não apague esse piso.

⚠️ **ARMADILHA DO MONKEYPATCH** — ``gowa/routes.py`` resolve o seam no IMPORT do
módulo (``from plugins.context import audit as _core_audit``). Patchar
``plugins.context.audit`` NÃO intercepta nada aqui: o nome já foi resolvido e o
teste passaria verde sem provar coisa alguma. O alvo é o global do módulo do
plugin, no namespace canônico do loader (``whatsbot_plugins.gowa.routes``), que
só existe DEPOIS de ``build_app`` — é o que ``loaded_plugin_module`` devolve.

⚠️ **ARMADILHA DAS TRÊS FONTES** — o `gowa` é o único plugin bundled e tem a
fonte em ``assets/plugin_examples/gowa/`` (esta árvore) **e** em
``whatsbot-pro-plugins/plugins/gowa/src/``. ``build_test_app`` resolve pelo
mesmo ``plugin_source_candidates`` de sempre, então este arquivo mede
``assets/`` no pytest do core e ``plugins/gowa/src`` quando roda pelo harness do
repositório de plugins (que seta ``WHATSBOT_PLUGIN_SOURCE_ROOT``). Corrigir uma
cópia só = verde numa árvore e vermelho na outra.

Rodar::

    cd whatsbot-pro && flock /tmp/whatsbot-pytest.lock \\
        venv/bin/python -m pytest -q tests/integration/test_gowa_alert_audit.py
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from db.audit_actions import PLUGIN_ACTION_RE
from db.repositories import audit_repo, config_repo
from tests.plugin_test_utils import loaded_plugin_module

API = "/api/plugins/gowa"
CFG = "plugin.gowa."

# Um token reconhecível: se ele aparecer numa linha da trilha ou no corpo de uma
# resposta, o teste do vazamento acusa na hora.
TOKEN = "123456:NAO_PODE_VAZAR"
CHAT_ANTIGO = "-100111"
CHAT_NOVO = "-1001999888777"


# ── bancada ──────────────────────────────────────────────────────────────────

@pytest.fixture
def cfg_isolada():
    """Preserva as chaves ``plugin.gowa.`` — o banco de teste é COMPARTILHADO.

    Sem isto, o alerta ligado por um teste vazaria para o seguinte (e para o loop
    de fundo do plugin), e a ordem de coleta mudaria o resultado.
    """
    antes = {k: v for k, v in config_repo.get_all().items() if k.startswith(CFG)}
    yield
    config_repo.delete_prefix(CFG)
    if antes:
        config_repo.set_many(antes)


def _bancada(build_app, authenticated_admin, monkeypatch, *, capturar=True):
    """App real com o `gowa` + admin autenticado + captura das chamadas ao seam.

    ``capturar=False`` deixa o seam REAL em pé — é o caminho do teste de ponta a
    ponta, o único que confere a linha no banco.
    """
    built = build_app(["gowa"])
    # As rotas exigem ``channel.manage`` e o plano 48 fecha a API assim que existe
    # ≥1 usuário; autenticar torna o teste independente da ordem de coleta.
    authenticated_admin(built.client, name="P148 Operador GOWA")
    routes = loaded_plugin_module("gowa", "routes")

    linhas: list[dict] = []
    if capturar:
        monkeypatch.setattr(
            routes, "_core_audit",
            lambda pid, action, **kw: linhas.append({"p": pid, "a": action, **kw}))

    return SimpleNamespace(client=built.client, routes=routes, linhas=linhas)


class _Resp:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Cliente httpx de mentira: devolve as respostas na ordem, e registra os POSTs."""

    def __init__(self, respostas):
        self._respostas = list(respostas)
        self.chamadas: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def post(self, url, json=None, **_kw):
        self.chamadas.append({"url": url, "json": json})
        assert self._respostas, "o POST chamou o Telegram mais vezes do que o previsto"
        return _Resp(self._respostas.pop(0))


def _telegram(b, respostas, monkeypatch) -> _FakeClient:
    """Planta o cliente falso no módulo VIVO do plugin (não no httpx global)."""
    fake = _FakeClient(respostas)
    monkeypatch.setattr(b.routes, "httpx",
                        SimpleNamespace(AsyncClient=lambda **_kw: fake))
    return fake


# Resposta real da Bot API quando o grupo virou supergrupo.
_MIGROU = {
    "ok": False,
    "error_code": 400,
    "description": "Bad Request: group chat was upgraded to a supergroup chat",
    "parameters": {"migrate_to_chat_id": int(CHAT_NOVO)},
}
_ENVIOU = {"ok": True, "result": {"message_id": 7}}


def _alerts_vivo():
    """O módulo ``alerts`` do plugin no namespace canônico do loader.

    Ele não está em ``entry:`` (o lifecycle o importa sob demanda), então o
    ``build_app`` não o materializa sozinho — mas o PACOTE canônico já existe
    depois do build, e é dele que o submódulo tem de sair (importar o arquivo
    solto leria OUTRA cópia da fonte).
    """
    import importlib

    return importlib.import_module("whatsbot_plugins.gowa.alerts")


def _uma(linhas: list[dict], acao: str) -> dict:
    """A única linha com esta ação (falha se houver zero ou mais de uma)."""
    achadas = [linha for linha in linhas if linha["a"] == acao]
    assert len(achadas) == 1, \
        f"esperava 1 linha {acao!r}, achei {[linha['a'] for linha in linhas]}"
    assert achadas[0]["p"] == "gowa"
    return achadas[0]


# ── 1 · a migração de supergrupo deixa rastro ────────────────────────────────

def test_migracao_de_supergrupo_registra_o_destino_novo(
        build_app, authenticated_admin, monkeypatch, cfg_isolada):
    """Mudou para ONDE todo alerta vai: tem de ter dono, valor velho e novo."""
    b = _bancada(build_app, authenticated_admin, monkeypatch)
    config_repo.set_many({CFG + "disconnect_alert_bot_token": TOKEN,
                          CFG + "disconnect_alert_chat_id": CHAT_ANTIGO,
                          CFG + "disconnect_alert_enabled": False})
    fake = _telegram(b, [_MIGROU, _ENVIOU], monkeypatch)

    r = b.client.post(f"{API}/alert-test", json={})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}

    # A escrita aconteceu: o chat novo é o que fica salvo, e o reenvio usou ele.
    assert config_repo.get(CFG + "disconnect_alert_chat_id") == CHAT_NOVO
    assert [c["json"]["chat_id"] for c in fake.chamadas] == [CHAT_ANTIGO, CHAT_NOVO]

    linha = _uma(b.linhas, "alerta.chat_id_migrado")
    # O ``before`` é o snapshot PRÉ-escrita (tirado no mesmo hop de thread do
    # ``_resolve``); o ``after`` é derivado dele, sem reler o banco dentro do try.
    assert linha["before"]["chat_id"] == CHAT_ANTIGO
    assert linha["after"]["chat_id"] == CHAT_NOVO
    assert set(linha["before"]) == set(linha["after"])
    # Recurso default (``plugin:gowa``): o alerta de desconexão é config da
    # INSTALAÇÃO, não de um canal — carve-out da §5 do guia, que cita este caso
    # nominalmente. A rota nem tem um ``channel_id`` à mão.
    assert "resource_type" not in linha and "resource_id" not in linha


def test_a_acao_casa_o_formato_que_o_core_aceita(
        build_app, authenticated_admin, monkeypatch, cfg_isolada):
    """Ação fora da regex é DESCARTADA com WARNING — a lacuna voltaria em silêncio."""
    b = _bancada(build_app, authenticated_admin, monkeypatch)
    config_repo.set_many({CFG + "disconnect_alert_bot_token": TOKEN,
                          CFG + "disconnect_alert_chat_id": CHAT_ANTIGO})
    _telegram(b, [_MIGROU, _ENVIOU], monkeypatch)

    b.client.post(f"{API}/alert-test", json={})

    # Sem esta guarda o teste passaria VAZIO: some a chamada ao seam e o ``for``
    # não itera nada, ficando verde justamente quando a lacuna volta.
    assert b.linhas, "nenhuma linha para validar"
    for linha in b.linhas:
        completa = f"{linha['p']}.{linha['a']}"
        assert PLUGIN_ACTION_RE.match(completa), completa


def test_o_token_do_bot_nunca_entra_na_linha(
        build_app, authenticated_admin, monkeypatch, cfg_isolada):
    """A linha leva ``bot_token_definido: True``, jamais o valor (guia §3)."""
    b = _bancada(build_app, authenticated_admin, monkeypatch)
    config_repo.set_many({CFG + "disconnect_alert_bot_token": TOKEN,
                          CFG + "disconnect_alert_chat_id": CHAT_ANTIGO})
    _telegram(b, [_MIGROU, _ENVIOU], monkeypatch)

    b.client.post(f"{API}/alert-test", json={})

    linha = _uma(b.linhas, "alerta.chat_id_migrado")
    assert linha["before"]["bot_token_definido"] is True
    assert TOKEN not in json.dumps(linha, ensure_ascii=False, default=str)


def test_a_migracao_do_loop_de_fundo_tambem_registra(
        build_app, authenticated_admin, monkeypatch, cfg_isolada):
    """O MESMO ``config_repo.set`` existe no loop de fundo — e ele é o caminho COMUM.

    O botão "Testar alerta" é o caso raro; quem costuma topar com o supergrupo é
    o loop, que reenvia a cada ``interval_min`` enquanto o número está fora do ar.
    Auditar só a rota faria a Auditoria exibir ``gowa.alerta.chat_id_migrado`` sem
    nenhuma linha do caminho real — um auditor leria isso como "o destino nunca
    mudou". Aqui não há humano na request, então o ator é ``system``.
    """
    import asyncio

    b = _bancada(build_app, authenticated_admin, monkeypatch)
    alerts = _alerts_vivo()
    config_repo.delete_prefix(CFG)
    config_repo.set(CFG + "disconnect_alert_chat_id", CHAT_ANTIGO)

    linhas: list[dict] = []
    monkeypatch.setattr(
        alerts, "_core_audit",
        lambda pid, action, **kw: linhas.append({"p": pid, "a": action, **kw}))
    fake = _FakeClient([_MIGROU, _ENVIOU])
    monkeypatch.setattr(alerts, "httpx",
                        SimpleNamespace(AsyncClient=lambda **_kw: fake))

    data = asyncio.run(alerts._tg_call(
        TOKEN, "sendMessage", {"chat_id": CHAT_ANTIGO, "text": "caiu"}))

    assert data.get("ok") is True
    assert config_repo.get(CFG + "disconnect_alert_chat_id") == CHAT_NOVO
    assert [c["json"]["chat_id"] for c in fake.chamadas] == [CHAT_ANTIGO, CHAT_NOVO]

    linha = _uma(linhas, "alerta.chat_id_migrado")
    completa = f"{linha['p']}.{linha['a']}"
    assert PLUGIN_ACTION_RE.match(completa), completa
    assert linha["before"]["chat_id"] == CHAT_ANTIGO
    assert linha["after"]["chat_id"] == CHAT_NOVO
    # Ator FORÇADO: o default herdaria quem estivesse no ``ContextVar`` — e
    # ninguém clicou nada aqui.
    assert linha["actor_type"] == "system"
    assert TOKEN not in json.dumps(linha, ensure_ascii=False, default=str)
    # E o token do bot não vaza para o log/retorno nem no caminho de exceção.
    assert "bot_token" not in json.dumps(linha, ensure_ascii=False, default=str)
    assert b.linhas == []          # a rota não foi tocada neste teste


# ── 2 · o que NÃO pode virar linha ───────────────────────────────────────────

def test_teste_que_deu_certo_de_primeira_nao_registra(
        build_app, authenticated_admin, monkeypatch, cfg_isolada):
    """Sem migração não houve mudança nenhuma — e "testei o alerta" não é evento
    de auditoria (o histórico de conversa/uso não passa por aqui)."""
    b = _bancada(build_app, authenticated_admin, monkeypatch)
    config_repo.set_many({CFG + "disconnect_alert_bot_token": TOKEN,
                          CFG + "disconnect_alert_chat_id": CHAT_ANTIGO})
    _telegram(b, [_ENVIOU], monkeypatch)

    r = b.client.post(f"{API}/alert-test", json={})
    assert r.status_code == 200 and r.json() == {"ok": True}

    assert b.linhas == []
    assert config_repo.get(CFG + "disconnect_alert_chat_id") == CHAT_ANTIGO


def test_teste_sem_token_ou_chat_id_nao_registra(
        build_app, authenticated_admin, monkeypatch, cfg_isolada):
    """Uma tentativa que nem saiu do servidor não é uma mudança (R7 do plano)."""
    b = _bancada(build_app, authenticated_admin, monkeypatch)
    config_repo.delete_prefix(CFG)

    r = b.client.post(f"{API}/alert-test", json={})
    assert r.status_code == 200
    assert r.json()["ok"] is False

    assert b.linhas == []


def test_erro_do_telegram_sem_migracao_nao_registra(
        build_app, authenticated_admin, monkeypatch, cfg_isolada):
    """Chat inexistente devolve erro e NÃO troca destino nenhum."""
    b = _bancada(build_app, authenticated_admin, monkeypatch)
    config_repo.set_many({CFG + "disconnect_alert_bot_token": TOKEN,
                          CFG + "disconnect_alert_chat_id": CHAT_ANTIGO})
    _telegram(b, [{"ok": False, "description": "Bad Request: chat not found"}],
              monkeypatch)

    r = b.client.post(f"{API}/alert-test", json={})
    assert r.json()["ok"] is False

    assert b.linhas == []
    assert config_repo.get(CFG + "disconnect_alert_chat_id") == CHAT_ANTIGO


# ── 3 · a trilha nunca custa a ação ──────────────────────────────────────────

def test_seam_que_explode_nao_derruba_a_migracao(
        build_app, authenticated_admin, monkeypatch, cfg_isolada):
    """``_audit`` engole tudo: a rota responde 200 e o chat novo continua salvo."""
    b = _bancada(build_app, authenticated_admin, monkeypatch, capturar=False)
    config_repo.set_many({CFG + "disconnect_alert_bot_token": TOKEN,
                          CFG + "disconnect_alert_chat_id": CHAT_ANTIGO})

    def _explode(*_a, **_kw):
        raise RuntimeError("trilha fora do ar")

    monkeypatch.setattr(b.routes, "_core_audit", _explode)
    _telegram(b, [_MIGROU, _ENVIOU], monkeypatch)

    r = b.client.post(f"{API}/alert-test", json={})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert config_repo.get(CFG + "disconnect_alert_chat_id") == CHAT_NOVO


def test_core_sem_o_seam_nao_derruba_a_migracao(
        build_app, authenticated_admin, monkeypatch, cfg_isolada):
    """O import é defensivo: num core anterior ao seam o plugin só não registra."""
    b = _bancada(build_app, authenticated_admin, monkeypatch, capturar=False)
    config_repo.set_many({CFG + "disconnect_alert_bot_token": TOKEN,
                          CFG + "disconnect_alert_chat_id": CHAT_ANTIGO})
    monkeypatch.setattr(b.routes, "_core_audit", None)
    _telegram(b, [_MIGROU, _ENVIOU], monkeypatch)

    r = b.client.post(f"{API}/alert-test", json={})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert config_repo.get(CFG + "disconnect_alert_chat_id") == CHAT_NOVO


def test_a_linha_chega_de_verdade_no_banco(
        build_app, authenticated_admin, monkeypatch, cfg_isolada):
    """Ponta a ponta com o seam REAL: a ação é aceita e vira row em ``audit_log``.

    Os testes acima capturam a chamada ao seam; este prova que ela sobrevive à
    validação de formato do core (ação fora da regex é descartada com WARNING, e
    a rota não acusaria nada). A escrita é fire-and-forget — daí o poll.
    """
    b = _bancada(build_app, authenticated_admin, monkeypatch, capturar=False)
    config_repo.set_many({CFG + "disconnect_alert_bot_token": TOKEN,
                          CFG + "disconnect_alert_chat_id": CHAT_ANTIGO})
    _telegram(b, [_MIGROU, _ENVIOU], monkeypatch)

    antes = audit_repo.count(action="gowa.alerta.chat_id_migrado")
    r = b.client.post(f"{API}/alert-test", json={})
    assert r.status_code == 200 and r.json() == {"ok": True}

    linhas: list[dict] = []
    for _ in range(60):
        if audit_repo.count(action="gowa.alerta.chat_id_migrado") > antes:
            linhas = audit_repo.query(action="gowa.alerta.chat_id_migrado", limit=1)
            break
        time.sleep(0.05)
    assert linhas, "a linha nunca pousou em audit_log (ação recusada pelo core?)"
    linha = linhas[0]
    assert linha["resource_type"] == "plugin"
    assert linha["resource_id"] == "gowa"
    assert linha["actor_type"] == "user"       # o humano da request, não `system`
    assert json.loads(linha["after_json"])["chat_id"] == CHAT_NOVO
    assert TOKEN not in json.dumps(linha, ensure_ascii=False, default=str)


# ── 4 · o GET não escreve mais (correção de DESENHO) ─────────────────────────

def test_abrir_a_aba_nao_grava_o_fuso_do_navegador(
        build_app, authenticated_admin, monkeypatch, cfg_isolada):
    """Abrir a tela mudava a hora de TODO alerta, sem salvar e sem dono.

    A correção não é auditar o GET (viraria log de navegação, R4): é a escrita
    sair. O fuso detectado continua voltando no corpo para a tela pré-preencher.
    """
    b = _bancada(build_app, authenticated_admin, monkeypatch)
    config_repo.delete_prefix(CFG)

    r = b.client.get(f"{API}/alert-settings?tz=America/Manaus")
    assert r.status_code == 200, r.text
    data = r.json()["data"]

    assert config_repo.get(CFG + "disconnect_alert_timezone_auto") is None, \
        "o GET voltou a escrever config — abrir a aba não pode mudar o alerta"
    assert config_repo.get(CFG + "disconnect_alert_timezone") is None
    # …e o GET continua útil: devolve o detectado para a tela pré-selecionar.
    assert data["timezone_auto"] == "America/Manaus"
    assert data["timezone"] == ""            # nada salvo ainda
    assert b.linhas == []                    # GET NÃO audita (R4)

    # ⚠️ SUGESTÃO ≠ EFETIVO. Como nada foi salvo, o alerta ainda imprime
    # Brasília — o "efetivo" tem de dizer isso, não o fuso que a tela apenas
    # PROPÕE. Enquanto o GET escrevia, os dois coincidiam por acidente; ancorar
    # o campo no motor é o que impede a resposta de voltar a mentir.
    assert data["timezone_effective"] == "America/Sao_Paulo"
    assert data["timezone_effective"] == _alerts_vivo()._resolve_tz_name()


def test_fuso_invalido_na_query_nao_vira_nada(
        build_app, authenticated_admin, monkeypatch, cfg_isolada):
    """Query torta cai no default, sem escrever e sem estourar."""
    b = _bancada(build_app, authenticated_admin, monkeypatch)
    config_repo.delete_prefix(CFG)

    r = b.client.get(f"{API}/alert-settings?tz=Marte/Olympus")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["timezone_auto"] == "America/Sao_Paulo"
    assert config_repo.get(CFG + "disconnect_alert_timezone_auto") is None


def test_o_fuso_legado_continua_valendo(
        build_app, authenticated_admin, monkeypatch, cfg_isolada):
    """PISO LEGADO: quem já tem a chave gravada não regride para Brasília.

    Nenhuma rota grava mais ``disconnect_alert_timezone_auto``, mas o motor
    continua lendo-a. Apagar esse ramo de ``alerts._resolve_tz_name`` como "código
    morto" mudaria o horário exibido nas instalações que já a têm.
    """
    _bancada(build_app, authenticated_admin, monkeypatch)
    alerts = _alerts_vivo()
    config_repo.delete_prefix(CFG)
    config_repo.set(CFG + "disconnect_alert_timezone_auto", "America/Manaus")

    assert alerts._resolve_tz_name() == "America/Manaus"
    # E o manual continua ganhando do automático.
    config_repo.set(CFG + "disconnect_alert_timezone", "America/Bahia")
    assert alerts._resolve_tz_name() == "America/Bahia"


def test_o_put_registra_o_fuso_e_o_piso_legado(
        build_app, authenticated_admin, monkeypatch, cfg_isolada):
    """O fuso agora entra pelo PUT — e o diff mostra os DOIS lados dele.

    Sem ``timezone_auto`` no ``_alert_audit_view`` a trilha diria "timezone: ''"
    numa instalação que exibe Manaus, e ninguém entenderia o horário do alerta.
    """
    b = _bancada(build_app, authenticated_admin, monkeypatch)
    config_repo.delete_prefix(CFG)
    config_repo.set(CFG + "disconnect_alert_timezone_auto", "America/Manaus")

    r = b.client.put(f"{API}/alert-settings",
                     json={"chat_id": CHAT_ANTIGO, "timezone": "America/Bahia"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert config_repo.get(CFG + "disconnect_alert_timezone") == "America/Bahia"

    linha = _uma(b.linhas, "alerta.config")
    assert linha["before"]["timezone"] == ""
    assert linha["after"]["timezone"] == "America/Bahia"
    assert linha["before"]["timezone_auto"] == "America/Manaus"
    assert linha["after"]["timezone_auto"] == "America/Manaus"


# ── 5 · o token do bot não volta no texto do erro ────────────────────────────

def test_erro_de_transporte_nao_ecoa_o_token(
        build_app, authenticated_admin, monkeypatch, cfg_isolada):
    """A URL da Bot API é ``/bot{token}/sendMessage`` e o texto de uma exceção
    httpx costuma carregá-la. Ecoá-la na resposta anularia o mascaramento que o
    GET faz de propósito (paridade com o gêmeo `whatsapp_cloud`)."""
    b = _bancada(build_app, authenticated_admin, monkeypatch)

    class _Explode:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def post(self, url, **_kw):
            raise RuntimeError(f"request failed for {url}")

    monkeypatch.setattr(b.routes, "httpx",
                        SimpleNamespace(AsyncClient=lambda **_kw: _Explode()))

    r = b.client.post(f"{API}/alert-test",
                      json={"bot_token": TOKEN, "chat_id": CHAT_ANTIGO})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": False, "error": "Falha ao contatar o Telegram."}
    assert TOKEN not in r.text
