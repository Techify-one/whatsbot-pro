"""Testes do plugin ``trackify`` (jornada do cliente + espelho para o CDP).

Divididos em dois grupos, de propósito:

* **puros** — telefone, ``external_id``, derivação de assinatura e tolerância aos
  formatos tortos do CDP. Não tocam banco nem rede: carregam o módulo por
  ``importlib`` e rodam em milissegundos.
* **com app** — fila de saída e rotas, pela fixture ``plugin_app`` (monta o
  plugin de verdade, com migração aplicada).

Os dados torturados aqui não foram inventados: ``next_charge_date`` em
``dd/mm/yyyy`` e ``subscription_canceled_at`` valendo a string ``"system"`` são
valores REAIS lidos da produção durante o planejamento (plano 94).
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "assets" / "plugin_examples" / "trackify"
_PKG = "trackify_src"   # nome PRÓPRIO: não colide com o que o harness monta


def _load(module: str):
    """Carrega ``<módulo>`` do plugin como pacote isolado, SEM mexer no ``sys.path``.

    Inserir ``storages/plugins`` (ou ``assets/plugin_examples``) no ``sys.path``
    parece atalho e é armadilha: essas pastas têm um diretório ``gowa/`` que
    SOMBREIA o pacote ``gowa/`` da raiz do repo, e aí ``server/routes/contacts.py``
    quebra com ``No module named 'gowa.client'`` — derrubando qualquer teste que
    suba o app. Registrar o pacote por spec evita isso; os imports relativos
    (``from . import _config``) continuam funcionando porque o pacote existe de
    verdade em ``sys.modules``.
    """
    if _PKG not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            _PKG, _SRC / "__init__.py", submodule_search_locations=[str(_SRC)])
        pkg = importlib.util.module_from_spec(spec)
        sys.modules[_PKG] = pkg
        spec.loader.exec_module(pkg)
    return importlib.import_module(f"{_PKG}.{module}")


# ── Puros: telefone ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def phone():
    return _load("phone")


@pytest.mark.parametrize("value,expected", [
    ("5564996162906", "+5564996162906"),          # móvel 13 dígitos: já tem o 9
    ("556496162906", "+5564996162906"),           # móvel 12: restaura o 9
    ("+55 (64) 99616-2906", "+5564996162906"),    # mascarado
    ("5564996162906@s.whatsapp.net", "+5564996162906"),   # JID
    ("12025550123", "+12025550123"),              # fora do Brasil: intacto
])
def test_canonical_e164(phone, value, expected):
    assert phone.canonical_e164(value) == expected


def test_canonical_nao_inventa_nono_digito_em_telefone_fixo(phone):
    """O 9 só entra em CELULAR.

    ``br_phone_variants`` (do core) insere o 9 em qualquer número de 12 dígitos
    começando com 55 — o que num FIXO produziria um número que não existe. Como
    a política é criar o contato quando ele não está no CDP, esse número
    inventado ficaria gravado para sempre (contato lá só tem soft delete).
    """
    assert phone.canonical_e164("556432168000") == "+556432168000"
    # ...mas na BUSCA sobregerar é inofensivo, então a variante continua lá.
    assert "5564932168000" in phone.lookup_candidates("556432168000")


@pytest.mark.parametrize("value", [
    "wsess_f0qD7g7e3TmKcZwGfpoSHRtF_0vwLEy1",   # id de sessão do widget de site
    "120363999@g.us",                            # JID de grupo
    "12345",                                     # curto demais
    "1234567890123456",                          # longo demais (E.164 vai até 15)
    "abc", "", None,
])
def test_o_que_nao_e_telefone_nao_vira_candidato(phone, value):
    """O ``contacts.phone`` do WhatsBot é identificador genérico por canal, não
    telefone: o widget de site grava id de sessão ali. Sem este guard, os dígitos
    soltos do id virariam busca — e, na escrita, contato novo de lixo no CDP."""
    assert phone.looks_like_phone(value) is False
    assert phone.lookup_candidates(value) == []
    assert phone.canonical_e164(value) == ""


def test_candidatos_cobrem_as_grafias_do_cdp(phone):
    """90% do CDP grava com ``+`` e o WhatsBot sem — por isso as duas formas de
    cada variante entram na consulta indexada.

    Desde o caso do cadastro em forma nacional, entram também as grafias sem o
    código do país (``6496162906``), que é como formulário e planilha gravam.
    """
    got = phone.lookup_candidates("556496162906")
    assert {"556496162906", "+556496162906",
            "5564996162906", "+5564996162906"} <= set(got)
    assert {"6496162906", "64996162906"} <= set(got)


# ── Puros: jornada ───────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def journey():
    return _load("journey")


def test_data_solta_do_cdp(journey):
    """O CDP guarda ``next_charge_date`` como STRING ``dd/mm/yyyy``."""
    assert journey._parse_loose_date("25/02/2027") == datetime.date(2027, 2, 25)
    assert journey._parse_loose_date("2027-02-25") == datetime.date(2027, 2, 25)


def test_system_nao_e_data(journey):
    """``subscription_canceled_at`` vem com a string ``"system"`` em linhas reais
    de ``active_subscription``. Não pode virar data nem exceção."""
    assert journey._parse_loose_date("system") is None
    assert journey._parse_loose_date("") is None
    assert journey._parse_loose_date(None) is None


def test_dinheiro_em_pt_br(journey):
    assert journey._money(Decimal("1234.5")) == "R$ 1.234,50"
    assert journey._money(Decimal("97.00")) == "R$ 97,00"
    assert journey._money(None) is None          # events.value é nullable


def _mock_eventos(journey, monkeypatch, linhas, total=None):
    """Substitui ``GET /contacts/:id/events`` — a rota que serve a linha do tempo
    e o bloco de assinaturas."""
    tk = _load("client")

    async def fake(http, contact_id, **kwargs):
        return tk.Result(tk.OK, 200, data={
            "data": list(linhas),
            "meta": {"total": total if total is not None else len(linhas)},
        })
    monkeypatch.setattr(journey.tk_client, "list_events", fake)


def test_assinatura_derivada_de_linha_real(journey, monkeypatch):
    """Payload copiado de um evento REAL de produção, com as duas armadilhas."""
    campos = {
        "status": "Pagamento Autorizado",
        "product_name": "Combo de Redes",
        "offer_name": "Combo de Redes (Multivendor)",
        "payment_method": "Pix", "successful_charges": "1", "failed_charges": "0",
        "next_charge_date": "25/02/2027",
        "subscription_canceled_at": "system",
    }
    linha = {
        "id": "48c7304f", "eventType": "active_subscription",
        "title": "Assinatura ativa", "value": "97.00",
        "occurredAt": datetime.datetime(2026, 4, 29, 13, 30, 16),
        "channel": {"slug": "ticto"},
        "eventFieldValues": [
            {"value": v, "eventCustomField": {"slug": k}} for k, v in campos.items()],
    }
    _mock_eventos(journey, monkeypatch, [linha])
    subs = _run(journey.fetch_subscriptions(_FakeHttp(), "x",
                                            today=datetime.date(2026, 7, 31)))

    assert len(subs) == 1
    s = subs[0]
    assert s["product"] == "Combo de Redes"
    assert s["next_charge"] == "2027-02-25"
    assert s["days_left"] == 209
    assert s["next_charge_raw"] == "25/02/2027"   # o cru fica, para exibir se o parse falhar
    assert s["canceled_at"] is None               # "system" NÃO virou data
    assert s["last_value"] == "R$ 97,00"


def test_nenhum_parametro_e_engolido_por_cast():
    """`:param::tipo` NÃO funciona no SQLAlchemy — e falha só em runtime.

    O regex de bind param do SQLAlchemy tem um lookahead negativo: `:cid`
    seguido de `:` é ignorado de propósito (para não colidir com o cast `::` do
    Postgres). O resultado é que `WHERE id = :cid::uuid` chega LITERAL no banco
    e vira `syntax error at or near ":"` — um 500 na cara do vendedor.

    Este teste existe porque os testes de jornada mockam `run_read`: o SQL nunca
    era compilado, então a suíte ficava verde com a consulta quebrada. A forma
    correta é `CAST(:cid AS uuid)`.
    """
    import re
    from sqlalchemy import text

    # `journey` e `identity` deixaram de carregar SQL quando a leitura virou
    # HTTP; o teste segue guardando os módulos que ainda escrevem consulta.
    for mod_name in ("dispatcher", "mirror"):
        mod = _load(mod_name)
        for attr in dir(mod):
            value = getattr(mod, attr)
            if not isinstance(value, str) or "SELECT" not in value.upper():
                continue
            engolidos = re.findall(r":(\w+)::", value)
            assert not engolidos, (
                f"{mod_name}.{attr}: parâmetro(s) {engolidos} colados num cast "
                f"`::` — o SQLAlchemy não os substitui. Use CAST(:x AS tipo)."
            )
            # Cinto de segurança: todo `:nome` do SQL tem que virar bind param.
            declarados = set(re.findall(r"(?<![:\w]):(\w+)", value))
            reconhecidos = set(text(value)._bindparams)
            assert declarados == reconhecidos, (
                f"{mod_name}.{attr}: o SQLAlchemy reconheceu {reconhecidos}, "
                f"mas o SQL escreve {declarados}."
            )


def test_sem_api_key_tudo_degrada_para_vazio(journey, monkeypatch):
    """Sem configuração o plugin é no-op logado — nunca levanta, nunca 500."""
    monkeypatch.setattr(journey.tk_client, "is_configured", lambda: False)
    out = _run(journey.journey_for(_FakeHttp(), phone="5564996162906"))
    assert out["found"] is False and out["configured"] is False


# ── Puros: identidade e external_id ──────────────────────────────────────

def test_identidade_canonica_omite_vazios():
    """A ingestão devolve 422 se nenhum identificador sobreviver; mandar string
    vazia não ajuda ninguém."""
    identity = _load("identity")
    out = identity.canonical_identity(phone="556496162906", email="")
    assert out == {"whatsapp": "+5564996162906"}

    out = identity.canonical_identity(phone="556496162906", email="A@B.com ")
    assert out == {"email": "a@b.com", "whatsapp": "+5564996162906"}

    # Telegram: o "phone" é chat_id, não telefone — nunca vira ``whatsapp``.
    out = identity.canonical_identity(phone="123456789", contact_type="telegram")
    assert out == {"telegram_id": "123456789"}


def test_hash_de_etiquetas_e_estavel_e_ordem_nao_importa():
    """``contact.tagged`` é emitido em TODO save, mesmo sem mudança. O
    ``external_id`` endereçado pelo conteúdo colapsa re-saves idênticos em zero
    eventos no CDP — e a ordem das tags não pode gerar id diferente."""
    mirror = _load("mirror")
    a = mirror._tags_hash(["cliente", "vip"])
    b = mirror._tags_hash(["vip", "cliente"])
    assert a == b
    assert a != mirror._tags_hash(["cliente"])


def test_elegibilidade(monkeypatch):
    mirror = _load("mirror")
    monkeypatch.setattr(mirror._config, "setting",
                        lambda k, d=None: "whatsapp" if k == "mirror_contact_types" else d)

    ok, _ = mirror.eligible({"phone": "5564996162906", "contact_type": "whatsapp"})
    assert ok is True

    # Grupo: contato no Trackify só tem soft delete — um JID de grupo viraria
    # linha-lixo permanente no CDP.
    ok, why = mirror.eligible({"phone": "5564996162906", "is_group": 1})
    assert ok is False and "grupo" in why

    ok, why = mirror.eligible({"phone": "wsess_abc123def456", "contact_type": "whatsapp"})
    assert ok is False and "telefone" in why

    ok, why = mirror.eligible({"phone": "5564996162906", "contact_type": "outros"})
    assert ok is False and "outros" in why


# ── Com app: fila de saída ───────────────────────────────────────────────

def _mirror_on(**extra):
    base = {"plugin.trackify.mirror_enabled": True,
            "plugin.trackify.mirror_dry_run": True}
    base.update(extra)
    return base


def test_migracao_cria_a_fila(plugin_app):
    built = plugin_app("trackify")
    from sqlalchemy import text
    from db.engine import get_engine
    with get_engine().begin() as conn:
        conn.execute(text("SELECT id, external_id, status, attempts, next_attempt_at "
                          "FROM plugin_trackify_outbox LIMIT 1"))
        conn.execute(text("SELECT phone, exact_value FROM plugin_trackify_identity LIMIT 1"))
    assert built is not None


def test_enfileira_uma_vez_por_fato(plugin_app):
    """Idempotência LOCAL: o mesmo fato enfileirado duas vezes (re-entrega do
    bus, replay, emit duplo) colapsa numa linha só, antes mesmo do dedup do CDP."""
    plugin_app("trackify", settings_overrides=_mirror_on())
    mirror = _load("mirror")
    contact = {"id": 1, "phone": "5564996162906", "name": "Fulano",
               "contact_type": "whatsapp", "email": ""}
    eid = mirror.make_external_id("proto.999.c1753900000")

    assert mirror.enqueue("protocolo_closed", contact=contact, external_id=eid,
                          data={"protocolo_id": 999}, occurred_at=1753900000.0) is True
    mirror.enqueue("protocolo_closed", contact=contact, external_id=eid,
                   data={"protocolo_id": 999}, occurred_at=1753900000.0)

    from sqlalchemy import text
    from db.engine import get_engine
    with get_engine().begin() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM plugin_trackify_outbox "
                              "WHERE external_id = :e"), {"e": eid}).scalar()
    assert n == 1


def test_grupo_nunca_entra_na_fila(plugin_app):
    plugin_app("trackify", settings_overrides=_mirror_on())
    mirror = _load("mirror")
    ok = mirror.enqueue("conversation_created",
                        contact={"id": 2, "phone": "120363999", "is_group": 1},
                        external_id=mirror.make_external_id("conv.2"), data={})
    assert ok is False


def test_envelope_tem_data_iso_com_fuso(plugin_app):
    """O adapter do Trackify faz ``new Date(v)``: número seria lido como
    MILISSEGUNDOS (o ts do WhatsBot é em segundos → tudo em 1970) e ISO sem
    offset seria lido no fuso do servidor de lá."""
    plugin_app("trackify", settings_overrides=_mirror_on())
    dispatcher = _load("dispatcher")
    row = {
        "id": 1, "external_id": "wb.x.conv.1", "kind": "conversation_created",
        "phone": "5564996162906", "occurred_at": 1753900000.0, "attempts": 0,
        "payload": json.dumps({
            "v": 1, "kind": "conversation_created", "title": "Atendimento iniciado",
            "contact": {"name": "Fulano", "phone": "5564996162906",
                        "contact_type": "whatsapp", "email": ""},
            "data": {"conversation_id": 7},
        }),
    }
    body = _run(dispatcher.build_body(_FakeHttp(), row))
    # Data EXATA: prova que segundos foram lidos como segundos. Se o envelope
    # mandasse o epoch cru, o ``new Date(número)`` do adapter leria como
    # milissegundos e carimbaria 1970 em todo evento.
    assert body["occurred_at"] == "2025-07-30T18:26:40+00:00"
    assert body["title"]                       # sem title a timeline lê o event_type
    assert "phone" not in body["contact"]      # telefone só como identificador
    assert body["identity"]["whatsapp"] == "+5564996162906"


def test_health_distingue_nao_configurado(plugin_app):
    """"Não configurado", "inalcançável" e "schema mudou" são coisas diferentes —
    um schema alterado no CDP não pode virar tela vazia sem explicação."""
    built = plugin_app("trackify")
    r = built.client.get("/api/plugins/trackify/health")
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["configured"] is False and d["reachable"] is False
    assert "não configurada" in d["message"].lower()


def test_journey_recusa_contato_inexistente(plugin_app):
    built = plugin_app("trackify")
    r = built.client.get("/api/plugins/trackify/journey?contact_id=999999")
    assert r.status_code == 404
    assert r.json()["ok"] is False


# ── Puros: codec de campo (WhatsBot ↔ Trackify) ──────────────────────────

@pytest.fixture(scope="module")
def codec():
    return _load("field_codec")


def _defn(t="text", **extra):
    return {"attribute_key": "campo", "type": t, **extra}


_MAP = {"tk_regex": "", "tk_field_type": "text"}


@pytest.mark.parametrize("value,expected", [
    (1500.0, "1500"),          # float redondo NÃO vira "1500.0"
    (1500, "1500"),
    (1.5, "1.5"),
    (0.1 + 0.2, "0.3"),        # o erro binário some no corte de 10 casas
    (1e16, "10000000000000000"),
    (-42.25, "-42.25"),
])
def test_numero_nunca_vira_notacao_cientifica(codec, value, expected):
    """``str(1e16)`` dá ``1e+16``; o ``Number()`` do lado de lá lê com perda e o
    ida-e-volta quebra justo nos números grandes."""
    out, err = codec.to_trackify(_MAP, _defn("number"), value)
    assert (out, err) == (expected, None)
    assert "e" not in out.lower()


@pytest.mark.parametrize("raw,expected", [
    ("R$ 1.500,50", 1500.5),   # dinheiro pt-BR REAL do CDP
    ("1.500", 1500.0),         # ponto como separador de milhar
    ("1,5", 1.5),              # vírgula decimal simples (o core já resolve)
    ("97", 97.0),
])
def test_dinheiro_ptbr_do_cdp_vira_numero(codec, raw, expected):
    out, err = codec.to_whatsbot(_MAP, _defn("number"), raw)
    assert err is None and out == expected


@pytest.mark.parametrize("raw,expected", [
    ("2027-02-25", "2027-02-25"),
    ("25/02/2027", "2027-02-25"),              # formato real da produção
    ("2026-08-03T23:30:00Z", "2026-08-03"),    # ISO com hora: parte da data
])
def test_data_aceita_formatos_tortos_do_cdp(codec, raw, expected):
    out, err = codec.to_whatsbot(_MAP, _defn("date"), raw)
    assert (out, err) == (expected, None)


def test_data_ilegivel_e_recusada_e_nao_vira_hoje(codec):
    out, err = codec.to_whatsbot(_MAP, _defn("date"), "system")
    assert out is None and err and "data" in err.lower()


def test_checkbox_nunca_cai_em_falso_por_default(codec):
    """A checagem do core (``in ('1','true','sim')``) devolveria False para
    "talvez" e gravaria uma mentira em silêncio."""
    assert codec.to_whatsbot(_MAP, _defn("checkbox"), "sim")[0] is True
    assert codec.to_whatsbot(_MAP, _defn("checkbox"), "não")[0] is False
    out, err = codec.to_whatsbot(_MAP, _defn("checkbox"), "talvez")
    assert out is None and err


@pytest.mark.parametrize("wb_type,value,extra", [
    ("text", "Fulano de Tal", {}),
    ("number", 1500.5, {}),
    ("number", 97.0, {}),
    ("date", "2027-02-25", {}),
    ("checkbox", True, {}),
    ("checkbox", False, {}),
    ("list", "Ouro", {"options": ["Ouro", "Prata"]}),
    ("link", "https://exemplo.com/x", {}),
])
def test_roundtrip_por_tipo(codec, wb_type, value, extra):
    """A invariante que sustenta a feature: o que sai volta igual."""
    d = _defn(wb_type, **extra)
    enviado, err = codec.to_trackify(_MAP, d, value)
    assert err is None
    voltou, err = codec.to_whatsbot(_MAP, d, enviado)
    assert err is None and voltou == value


def test_regex_do_campo_do_cdp_barra_valor_torto(codec):
    """O ``PUT`` do Trackify não valida NADA (o ``regex_pattern`` só roda na
    ingestão). Esta checagem é a única barreira entre um erro de digitação e um
    campo permanentemente torto no CRM do cliente."""
    m = {"tk_regex": r"^[^\s@]+@[^\s@]+\.[^\s@]+$", "tk_field_type": "email"}
    ok, err = codec.to_trackify(m, _defn(), "joao@empresa.com")
    assert (ok, err) == ("joao@empresa.com", None)
    out, err = codec.to_trackify(m, _defn(), "joao@")
    assert out is None and err and "email" in err


def test_limpeza_e_distinguida_de_erro(codec):
    """Quem chama TEM que olhar o segundo termo: ``None`` sozinho é ambíguo."""
    assert codec.to_trackify(_MAP, _defn(), None) == ("", None)
    assert codec.to_whatsbot(_MAP, _defn(), "") == (None, None)
    assert codec.to_whatsbot(_MAP, _defn(), "   ") == (None, None)
    out, err = codec.to_whatsbot(_MAP, _defn("number"), "abc")
    assert out is None and err is not None


def test_hash_ignora_espaco_e_colapsa_vazio_com_ausente(codec):
    """Se a normalização do hash divergisse da do codec, um valor com espaço à
    direita viraria "mudou" em todo ciclo e os dois lados se escreveriam para
    sempre."""
    assert codec.hash_value("abc ") == codec.hash_value("abc")
    assert codec.hash_value(None) == codec.hash_value("")
    assert codec.hash_value(1500.0) == codec.hash_value("1500")
    assert codec.hash_value(True) == codec.hash_value("true")
    assert codec.hash_value("a") != codec.hash_value("b")


@pytest.mark.parametrize("wb,tk,verdict", [
    ("text", "text", "ok"),
    ("text", "email", "ok"),
    ("number", "number", "ok"),
    ("date", "datetime", "ok"),
    ("list", "text", "lossy"),        # as opções viram texto livre
    ("checkbox", "text", "lossy"),
    ("text", "number", "bad"),
    ("text", None, "unknown"),        # join não resolveu: NÃO avisar nada
])
def test_matriz_de_compatibilidade(codec, wb, tk, verdict):
    assert codec.compat(wb, tk) == verdict


# ── Puros: motor de decisão ──────────────────────────────────────────────

@pytest.fixture(scope="module")
def core():
    return _load("sync_core")


def _m(direction="both", policy="whatsbot_wins"):
    return {"direction": direction, "conflict_policy": policy}


def _st(core_mod, wb, tk, **extra):
    return {"wb_hash": core_mod.hash_value(wb), "tk_hash": core_mod.hash_value(tk), **extra}


def test_so_um_lado_mudou_decide_a_direcao(core):
    st = _st(core, "a", "a")
    assert core.decide(_m(), st, "b", "a").action == core.PUSH
    assert core.decide(_m(), st, "a", "b").action == core.PULL


def test_dois_lados_mudaram_para_o_mesmo_valor_nao_e_conflito(core):
    d = core.decide(_m(), _st(core, "a", "a"), "b", "b")
    assert d.action == core.RESTAMP


def test_nada_mudou_e_lados_iguais_e_noop(core):
    assert core.decide(_m(), _st(core, "a", "a"), "a", "a").action == core.NOOP


@pytest.mark.parametrize("policy,expected", [
    ("whatsbot_wins", "push"),
    ("trackify_wins", "pull"),
    ("hold", "conflict"),
])
def test_conflito_respeita_a_politica(core, policy, expected):
    d = core.decide(_m(policy=policy), _st(core, "a", "a"), "b", "c")
    assert d.action == expected


def test_direcao_unica_nunca_gera_conflito(core):
    """Com uma via só não há o que disputar — por construção."""
    st = _st(core, "a", "a")
    assert core.decide(_m("to_trackify"), st, "b", "c").action == core.PUSH
    assert core.decide(_m("to_whatsbot"), st, "b", "c").action == core.PULL


def test_primeira_visao_semeia_e_nunca_apaga(core):
    """Sem hashes anteriores não dá para saber quem mudou. Chamar isso de
    conflito faria toda ativação nascer com a tela cheia de conflito falso — e
    semear com o lado VAZIO apagaria campo no CRM do cliente."""
    assert core.decide(_m(), None, "novo", "").action == core.PUSH
    assert core.decide(_m(), None, "", "existente").action == core.PULL
    # Só o lado que não podemos escrever tem valor: carimba, não apaga.
    assert core.decide(_m("to_whatsbot"), None, "só_no_wb", "").action == core.RESTAMP


def test_valor_ja_recusado_nao_e_redetectado(core):
    """Sem esta guarda a varredura acha a MESMA divergência para sempre."""
    st = _st(core, "a", "a", rejected_hash=core.hash_value("lixo"))
    assert core.decide(_m("to_whatsbot"), st, "a", "lixo").action != core.PULL


def test_divergencia_pendente_e_retentada(core):
    """Nenhum lado mudou desde o carimbo, mas divergem: o push anterior não
    aterrissou. Ficar parado divergindo seria pior."""
    st = {"wb_hash": core.hash_value("b"), "tk_hash": core.hash_value("c")}
    assert core.decide(_m(), st, "b", "c").action == core.PUSH


def test_direcao_que_nao_permite_escrever_apenas_carimba(core):
    st = _st(core, "a", "a")
    assert core.decide(_m("to_whatsbot"), st, "b", "a").action == core.RESTAMP
    assert core.decide(_m("to_trackify"), st, "a", "b").action == core.RESTAMP


# ── Com app: mapeamentos de campo ────────────────────────────────────────

def test_migracao_002_cria_as_tabelas_do_field_sync(plugin_app):
    from sqlalchemy import text as _t

    from db.engine import get_engine
    plugin_app("trackify")
    esperadas = ("plugin_trackify_field_map", "plugin_trackify_field_state",
                 "plugin_trackify_field_outbox", "plugin_trackify_sync_cursor")
    with get_engine().connect() as conn:
        for tabela in esperadas:
            assert conn.execute(_t("SELECT to_regclass(:t)"),
                                {"t": tabela}).scalar() is not None, tabela
        # O índice único PARCIAL é o que faz a fila coalescer por contato.
        idx = conn.execute(_t(
            "SELECT indexdef FROM pg_indexes "
            "WHERE indexname = 'plugin_trackify_field_outbox_pending'")).scalar()
    assert idx and "WHERE" in idx.upper() and "pending" in idx


def test_fila_de_push_colapsa_por_contato(plugin_app):
    """Cinco edições em dez segundos têm que virar UM PUT, não cinco."""
    from sqlalchemy import text as _t

    from db.engine import get_engine
    plugin_app("trackify")
    now = 1_754_000_000.0
    with get_engine().begin() as conn:
        for i in range(5):
            conn.execute(_t(
                "INSERT INTO plugin_trackify_field_outbox "
                "(contact_id, reason, enqueued_at, created_at, updated_at) "
                "VALUES (42, :r, :now, :now, :now) "
                "ON CONFLICT (contact_id) WHERE status = 'pending' "
                "DO UPDATE SET reason = :r, updated_at = :now"),
                {"r": f"edicao-{i}", "now": now + i})
    with get_engine().connect() as conn:
        n = conn.execute(_t("SELECT COUNT(*) FROM plugin_trackify_field_outbox "
                            "WHERE contact_id = 42")).scalar()
    assert n == 1


def test_mapeamento_recusa_escrita_sem_api_key(plugin_app):
    """Sem credencial não há como gravar no Trackify — e dizer isso na linha é o
    que evita o operador achar que salvou e nada acontecer."""
    built = plugin_app("trackify")
    r = built.client.put("/api/plugins/trackify/mappings", json={"rows": [
        {"wb_scope": "attribute", "wb_key": "cpf", "tk_slug": "cpf",
         "direction": "to_trackify"},
    ]})
    assert r.status_code == 400
    erros = r.json()["data"]["row_errors"]["0"]
    assert any("API key" in e for e in erros)


def test_mapeamento_recusa_duplicata_nos_dois_lados(plugin_app):
    built = plugin_app("trackify")
    base = {"direction": "to_whatsbot"}   # ← não exige credencial
    r = built.client.put("/api/plugins/trackify/mappings", json={"rows": [
        {"wb_scope": "attribute", "wb_key": "cpf", "tk_slug": "cpf", **base},
        {"wb_scope": "attribute", "wb_key": "cpf", "tk_slug": "outro", **base},
    ]})
    assert r.status_code == 400
    assert "linha 1" in r.json()["data"]["row_errors"]["1"][0]

    r = built.client.put("/api/plugins/trackify/mappings", json={"rows": [
        {"wb_scope": "attribute", "wb_key": "cpf", "tk_slug": "cpf", **base},
        {"wb_scope": "attribute", "wb_key": "email", "tk_slug": "cpf", **base},
    ]})
    assert r.status_code == 400
    assert "Trackify" in r.json()["data"]["row_errors"]["1"][0]


def test_mapeamento_salva_e_marca_nao_verificado_sem_cdp(plugin_app):
    """Salvar com o CDP fora do ar tem que FUNCIONAR: é exatamente quando alguém
    precisa desligar um mapeamento ruim."""
    built = plugin_app("trackify")
    r = built.client.put("/api/plugins/trackify/mappings", json={"rows": [
        {"wb_scope": "attribute", "wb_key": "cpf", "tk_slug": "cpf",
         "direction": "to_whatsbot"},
    ]})
    assert r.status_code == 200
    linha = r.json()["data"]["rows"][0]
    assert linha["wb_key"] == "cpf" and linha["unverified"] == 1

    r = built.client.get("/api/plugins/trackify/mappings")
    assert len(r.json()["data"]["rows"]) == 1


def test_mapeamento_recusa_atributo_inexistente(plugin_app):
    built = plugin_app("trackify")
    r = built.client.put("/api/plugins/trackify/mappings", json={"rows": [
        {"wb_scope": "attribute", "wb_key": "nao_existe", "tk_slug": "x",
         "direction": "to_whatsbot"},
    ]})
    assert r.status_code == 400
    assert "não existe mais" in r.json()["data"]["row_errors"]["0"][0]


def test_telefone_nao_e_mapeavel(plugin_app):
    """Sobrescrever o telefone a partir do CDP orfanaria o atendimento."""
    built = plugin_app("trackify")
    vocab = built.client.get("/api/plugins/trackify/contact-attributes").json()["data"]
    assert [c["key"] for c in vocab["columns"]] == ["name"]

    r = built.client.put("/api/plugins/trackify/mappings", json={"rows": [
        {"wb_scope": "column", "wb_key": "phone", "tk_slug": "whatsapp",
         "direction": "to_whatsbot"},
    ]})
    assert r.status_code == 400


def test_api_key_nunca_volta_em_claro(plugin_app):
    built = plugin_app("trackify")
    r = built.client.put("/api/plugins/trackify/api-key", json={"key": "tk_segredo"})
    assert r.status_code == 200

    r = built.client.get("/api/plugins/trackify/api-key")
    d = r.json()["data"]
    assert d["key_masked"] == "***"
    assert "tk_segredo" not in r.text

    # O sentinela preserva a chave em vez de apagá-la.
    built.client.put("/api/plugins/trackify/api-key", json={"key": "***"})
    from db.repositories import config_repo
    assert config_repo.get("plugin.trackify.sync_api_key") == "tk_segredo"

    # E uma chave NOVA descarta o id da anterior: ele é o ator com que as nossas
    # escritas aparecem no changelog, e o da chave velha não reconhece mais nada.
    config_repo.set("plugin.trackify.sync_api_key_id", "k-antiga")
    built.client.put("/api/plugins/trackify/api-key", json={"key": "tk_outra"})
    assert config_repo.get("plugin.trackify.sync_api_key") == "tk_outra"
    assert config_repo.get("plugin.trackify.sync_api_key_id") == ""

    config_repo.set_many({"plugin.trackify.sync_api_key": "",
                          "plugin.trackify.sync_api_key_id": ""})


def test_status_do_field_sync_responde_sem_configuracao(plugin_app):
    built = plugin_app("trackify")
    # A tabela ``config`` é compartilhada pela suíte e outro teste desta mesma
    # classe grava a conta de serviço — zerar aqui é o que torna a asserção
    # sobre "sem configuração" verdadeira em vez de dependente da ordem.
    from db.repositories import config_repo
    config_repo.set_many({"plugin.trackify.service_email": "",
                          "plugin.trackify.service_password": ""})
    d = built.client.get("/api/plugins/trackify/field-sync/status").json()["data"]
    assert d["enabled"] is False and d["credential_set"] is False
    assert d["dry_run"] is True          # modo seco é o padrão, de propósito
    # Forma da resposta (não o conteúdo: as tabelas do plugin também são
    # compartilhadas pela suíte, e outro teste desta classe grava mapeamento).
    assert isinstance(d["mappings"], list) and isinstance(d["conflicts"], list)
    assert d["conflicts"] == []


def test_nenhuma_migracao_tem_ponto_e_virgula_em_comentario():
    """O migrador divide o arquivo por ``;`` de forma ingênua, INCLUSIVE dentro
    de comentário, e o pedaço solto vira "syntax error" na subida do plugin —
    ou seja, o plugin não carrega em produção e a tela some.

    Este teste existe porque o aviso está escrito no cabeçalho das duas
    migrações e mesmo assim foi violado ao escrever a 002: um comentário em
    português acumula ponto-e-vírgula sem ninguém perceber.
    """
    import re
    ofensas = []
    for sql in sorted((_SRC / "migrations").glob("*.sql")):
        for n, linha in enumerate(sql.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"^\s*--", linha) and ";" in linha:
                ofensas.append(f"{sql.name}:{n}")
    assert not ofensas, (
        "ponto-e-vírgula dentro de comentário SQL em: " + ", ".join(ofensas))


# ── Puros: escrita no Trackify (writer) ──────────────────────────────────

class _FakeResp:
    """Resposta mínima no formato que o Trackify devolve."""

    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        # ``or {}`` aqui seria bug: um dict-like VAZIO é falsy e o objeto com
        # ``get_list`` seria trocado por um dict comum.
        self.headers = headers if headers is not None else {}

    def json(self):
        return self._payload


class _FakeClient:
    """Client httpx-like de resposta única, para exercitar o ``client`` do plugin.

    Implementa ``request`` porque é por lá que ``client._request`` passa; os
    verbos soltos ficam para o código que ainda chama ``get``/``post`` direto.
    """

    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    async def request(self, method, url, params=None, json=None, headers=None,
                      timeout=None):
        self.calls.append({"method": method, "url": url, "params": params,
                           "json": json, "headers": headers})
        return self._resp

    async def get(self, url, params=None, headers=None, timeout=None):
        return await self.request("GET", url, params=params, headers=headers,
                                  timeout=timeout)

    async def put(self, url, json=None, headers=None, timeout=None):
        return await self.request("PUT", url, json=json, headers=headers,
                                  timeout=timeout)

    async def post(self, url, json=None, headers=None, timeout=None):
        return await self.request("POST", url, json=json, headers=headers,
                                  timeout=timeout)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeHttp(_FakeClient):
    """Fake sem resposta programada.

    Serve aos testes que substituem as funções do ``client`` inteiras (e portanto
    nunca chegam à rede) mas ainda precisam passar ALGO como cliente HTTP.
    Qualquer chamada que escape do stub estoura, em vez de devolver um 200 falso
    que faria o teste passar por engano.
    """

    def __init__(self):
        super().__init__(None)

    async def request(self, *a, **k):
        raise AssertionError(
            "chamada HTTP não esperada: o teste deveria ter substituído o client")


def _cdp_contact(**slugs):
    return {"contactFieldValues": [
        {"value": v, "customField": {"slug": k}} for k, v in slugs.items()]}


def _run(coro):
    import asyncio as _a
    return _a.new_event_loop().run_until_complete(coro)


@pytest.fixture(scope="module")
def writer():
    return _load("writer")


@pytest.fixture()
def _api_base(monkeypatch):
    """Base + credencial configuradas, sem tocar no banco de config."""
    c = _load("client")
    monkeypatch.setattr(c._config, "api_base",
                        lambda: "https://nexus.example/trackify/api/v1")
    monkeypatch.setattr(c, "api_key", lambda: "tk_teste")


def test_corpo_do_put_so_tem_fieldValues(writer, _api_base):
    """O ValidationPipe do Trackify é ``forbidNonWhitelisted``: qualquer chave
    a mais derruba a requisição inteira com 400."""
    client = _FakeClient(_FakeResp(200, _cdp_contact(email="a@b.com")))
    res = _run(writer.put_contact(client, "uuid-1", {"email": "a@b.com"}))

    assert res.verdict == writer.OK
    assert set(client.calls[0]["json"]) == {"fieldValues"}
    # A credencial é a API key. Cookie de sessão não existe mais: era a conta de
    # serviço, que este trabalho inteiro serviu para aposentar.
    assert client.calls[0]["headers"]["X-API-Key"] == "tk_teste"
    assert "Cookie" not in client.calls[0]["headers"]


def test_slug_ignorado_pelo_cdp_vira_erro_e_nao_sucesso(writer, _api_base):
    """Slug desativado/inexistente é pulado EM SILÊNCIO pelo Trackify e o 200
    vem igual. Sem conferir a resposta, contaríamos sucesso numa escrita que
    nunca aconteceu — a falha mais perigosa desta feature."""
    client = _FakeClient(_FakeResp(200, _cdp_contact(email="antigo@b.com")))
    res = _run(writer.put_contact(client, "uuid-1", {"email": "novo@b.com"}))

    assert res.verdict == writer.BLOCKED
    assert res.dropped == ["email"]
    assert "ignorou" in res.error


def test_limpeza_e_confirmada_pela_ausencia_da_linha(writer, _api_base):
    """Apagar no Trackify DELETA a linha EAV: sucesso é o campo sumir."""
    client = _FakeClient(_FakeResp(200, _cdp_contact(cpf="111")))
    res = _run(writer.put_contact(client, "uuid-1", {"email": ""}))
    assert res.verdict == writer.OK and res.landed == {"email": ""}

    # A linha continuar lá significa que a limpeza NÃO aconteceu.
    client = _FakeClient(_FakeResp(200, _cdp_contact(email="ainda@aqui.com")))
    res = _run(writer.put_contact(client, "uuid-1", {"email": ""}))
    assert res.verdict == writer.BLOCKED and res.dropped == ["email"]


@pytest.mark.parametrize("status,verdict", [
    (409, "conflict"),      # outro cadastro é dono do identificador
    (401, "unauthorized"),
    (404, "unlinked"),
    # 403 mudou de significado: com cookie era "este usuário não vê o contato";
    # com chave é "a chave não tem o escopo", que é problema de configuração.
    (403, "unauthorized"),
    (400, "blocked"),
    (422, "blocked"),
    (429, "throttled"),
    (500, "retry"),
])
def test_taxonomia_de_status_do_put(writer, _api_base, status, verdict):
    client = _FakeClient(_FakeResp(status, {"message": "erro"}))
    res = _run(writer.put_contact(client, "uuid-1", {"email": "a@b.com"}))
    assert res.verdict == verdict


def test_409_carrega_motivo_legivel(writer, _api_base):
    """É a falha ESPERADA desta feature (dois cadastros com o mesmo e-mail), não
    a excepcional — precisa chegar legível na tela."""
    client = _FakeClient(_FakeResp(409, {"message": "Email já cadastrado"}))
    res = _run(writer.put_contact(client, "uuid-1", {"email": "a@b.com"}))
    assert res.verdict == writer.CONFLICT and "Email já cadastrado" in res.error


# ── Puros: cliente HTTP (API key) ────────────────────────────────────────
#
# Substituem o antigo bloco "sessão da conta de serviço". Não há mais login,
# cookie, backoff nem auto-bloqueio por senha errada: a credencial é uma chave
# que o operador cola, e o que precisa de teste é a higiene dela e a tradução de
# HTTP em veredito.


@pytest.fixture()
def cliente(monkeypatch):
    c = _load("client")
    c.reset_for_tests()
    monkeypatch.setattr(c._config, "api_base",
                        lambda: "https://nexus.example/trackify/api/v1")
    monkeypatch.setattr(c, "api_key", lambda: "tk_segredo")
    return c


def test_a_chave_vai_no_cabecalho_e_nunca_na_url(cliente):
    http = _FakeClient(_FakeResp(200, {"id": "k1", "name": "WhatsBot",
                                       "scopes": ["read"]}))
    res = _run(cliente.whoami(http))

    assert res.ok and res.data["name"] == "WhatsBot"
    chamada = http.calls[0]
    assert chamada["headers"]["X-API-Key"] == "tk_segredo"
    # Credencial em query string vaza para o log de acesso do proxy.
    assert "tk_segredo" not in chamada["url"]
    assert "tk_segredo" not in str(chamada["params"] or "")


def test_a_chave_nunca_aparece_em_log(cliente, caplog):
    class _Explode:
        async def request(self, *a, **k):
            raise RuntimeError("falhou em https://nexus.example?key=tk_segredo")

    with caplog.at_level("DEBUG"):
        res = _run(cliente.whoami(_Explode()))

    assert res.verdict == cliente.RETRY
    # Só o TIPO da exceção é logado: a mensagem de um erro de request pode
    # carregar a URL, e URL com credencial embutida acontece.
    assert "tk_segredo" not in caplog.text
    assert "tk_segredo" not in res.error


def test_sem_credencial_nao_ha_ida_a_rede(cliente, monkeypatch):
    monkeypatch.setattr(cliente, "api_key", lambda: "")

    class _Proibido:
        async def request(self, *a, **k):
            raise AssertionError("não deveria ter chamado o Trackify")

    res = _run(cliente.whoami(_Proibido()))
    assert res.verdict == cliente.BLOCKED and "não configurada" in res.error
    assert not cliente.is_configured()


@pytest.mark.parametrize("status,verdict", [
    (200, "ok"), (201, "ok"), (204, "ok"),      # o NestJS escolhe o status pelo verbo
    (401, "unauthorized"), (403, "unauthorized"),
    (404, "unlinked"), (409, "conflict"),
    (400, "blocked"), (422, "blocked"),
    (429, "throttled"), (500, "retry"), (502, "retry"),
])
def test_taxonomia_de_status(cliente, status, verdict):
    http = _FakeClient(_FakeResp(status, {"message": "algo"}))
    res = _run(cliente.get_contact(http, "uuid-1"))
    assert res.verdict == verdict


def test_401_e_403_dao_mensagens_diferentes(cliente):
    """Mandar o operador gerar outra chave quando o problema é escopo faz ele
    trocar a chave e continuar quebrado."""
    r401 = _run(cliente.get_contact(_FakeClient(_FakeResp(401, {})), "uuid-1"))
    r403 = _run(cliente.get_contact(
        _FakeClient(_FakeResp(403, {"message": "escopo faltando"})), "uuid-1"))

    assert "revogada" in r401.error
    assert "escopo faltando" in r403.error


def test_cache_de_leitura_nao_guarda_erro(cliente):
    """Cachear um erro transitório o congelaria pelo TTL inteiro."""
    ruim = _run(cliente.cached("k", lambda: cliente.get_contact(
        _FakeClient(_FakeResp(500, {})), "uuid-1")))
    assert not ruim.ok

    bom = _run(cliente.cached("k", lambda: cliente.get_contact(
        _FakeClient(_FakeResp(200, {"id": "uuid-1"})), "uuid-1")))
    assert bom.ok

    # Agora sim ficou guardado: um client que estoura prova que não houve rede.
    class _Proibido:
        async def request(self, *a, **k):
            raise AssertionError("deveria ter vindo do cache")

    de_novo = _run(cliente.cached("k", lambda: cliente.get_contact(_Proibido(), "u")))
    assert de_novo.data == {"id": "uuid-1"}


def test_resolve_manda_identificadores_no_corpo(cliente):
    """POST e não GET: identificador em query string vaza para log de proxy."""
    http = _FakeClient(_FakeResp(200, {"matches": []}))
    _run(cliente.resolve(http, {"whatsapp": ["5564993467452"]}))

    chamada = http.calls[0]
    assert chamada["method"] == "POST"
    assert chamada["json"]["identifiers"] == {"whatsapp": ["5564993467452"]}
    assert "5564993467452" not in chamada["url"]
    # O fallback por dígitos é caminho FRIO: não vai por padrão.
    assert "digitsFallback" not in chamada["json"]


# ── Com app: saída de campos (push) ──────────────────────────────────────

def _field_sync_on(**extra):
    base = {
        "plugin.trackify.field_sync_enabled": True,
        "plugin.trackify.field_sync_dry_run": True,
        "plugin.trackify.sync_api_key": "tk_teste",
        "plugin.trackify.sync_api_base": "https://nexus.example/trackify/api/v1",
    }
    base.update(extra)
    return base


def _liga_contato_ao_cdp(phone: str, tk_id: str, *, idade: float = 0.0):
    """Cria o vínculo de identidade que a sincronização exige."""
    import time as _time

    from sqlalchemy import text as _t

    from db.engine import get_engine
    with get_engine().begin() as conn:
        conn.execute(_t(
            "INSERT INTO plugin_trackify_identity "
            "(phone, trackify_contact_id, matched_slug, exact_value, resolved_at) "
            "VALUES (:p, :tk, 'whatsapp', :p, :at) "
            "ON CONFLICT (phone) DO UPDATE SET trackify_contact_id = :tk, "
            " resolved_at = :at"),
            {"p": phone, "tk": tk_id, "at": _time.time() - idade})


def _enfileira_e_reserva(push, contact_id: int):
    """Enfileira o contato e devolve a linha DELE.

    A fila é compartilhada pela suíte (outro teste deixa uma linha pendente),
    então filtrar por contato é o que torna estes testes independentes de ordem.
    """
    push.enqueue(contact_id, "teste")
    linhas = [r for r in push.claim(10, "w1") if int(r["contact_id"]) == contact_id]
    assert len(linhas) == 1
    return linhas[0]


def _mapeia(client, wb_key, tk_slug, direction="to_trackify"):
    r = client.put("/api/plugins/trackify/mappings", json={"rows": [
        {"wb_scope": "attribute", "wb_key": wb_key, "tk_slug": tk_slug,
         "direction": direction},
    ]})
    assert r.status_code == 200, r.text
    return r.json()["data"]["rows"][0]


def _cdp_tem(monkeypatch, push, valores: dict | None = None):
    """Valores que o CDP devolve para os slugs mapeados.

    Substitui ``_tk_values``, que antes era uma consulta SQL e hoje é
    ``GET /contacts/:id``. ``None`` simula um contato sem nenhum dos campos.
    """
    async def fake(http, tk_id, slugs):
        base = {s: "" for s in slugs}
        base.update(valores or {})
        return base
    monkeypatch.setattr(push, "_tk_values", fake)


def test_push_ignora_contato_nao_vinculado(plugin_app, monkeypatch):
    """Esta feature NUNCA cria contato no Trackify — quem faz isso é o espelho
    de eventos, com o toggle e o limite dele."""
    built = plugin_app("trackify", settings_overrides=_field_sync_on())
    push = _load("push")
    _mapeia(built.client, "cpf", "cpf")

    from db.repositories import contact_repo
    c = contact_repo.get_or_create("5511900000001")
    plano = _run(push.plan_for_contact(_FakeHttp(), int(c["id"])))
    assert "não vinculado" in plano.skip
    assert plano.field_values == {}


def test_push_so_envia_o_que_diverge(plugin_app, monkeypatch):
    """Depois do primeiro ciclo o caso esmagadoramente comum é "nada mudou".
    Zero HTTP nesse caso é o que mantém o orçamento de 30/min utilizável."""
    built = plugin_app("trackify", settings_overrides=_field_sync_on())
    push = _load("push")
    _mapeia(built.client, "cpf", "cpf")

    from db.repositories import contact_repo, custom_attribute_repo
    from db.tables import contacts as contacts_tbl
    c = contact_repo.get_or_create("5511900000002")
    custom_attribute_repo.set_values(contacts_tbl, int(c["id"]), {"cpf": "11122233344"})
    _liga_contato_ao_cdp("5511900000002", "uuid-cdp-2")

    # O CDP ainda não tem o CPF → tem que entrar no plano.
    _cdp_tem(monkeypatch, push)
    plano = _run(push.plan_for_contact(_FakeHttp(), int(c["id"])))
    assert plano.field_values == {"cpf": "11122233344"}
    assert plano.trackify_contact_id == "uuid-cdp-2"

    # O CDP já tem o MESMO valor → nada a enviar, nem uma chamada.
    _cdp_tem(monkeypatch, push, {"cpf": "11122233344"})
    plano = _run(push.plan_for_contact(_FakeHttp(), int(c["id"])))
    assert plano.field_values == {}


def test_push_em_modo_seco_nao_chama_o_trackify(plugin_app, monkeypatch):
    """Modo seco é o padrão de propósito: toda escrita nossa dispara automações
    no Trackify, e a primeira execução sobre a base inteira não pode ser uma
    surpresa."""
    built = plugin_app("trackify", settings_overrides=_field_sync_on())
    push = _load("push")
    _mapeia(built.client, "cpf", "cpf")

    from db.repositories import contact_repo, custom_attribute_repo
    from db.tables import contacts as contacts_tbl
    c = contact_repo.get_or_create("5511900000003")
    custom_attribute_repo.set_values(contacts_tbl, int(c["id"]), {"cpf": "999"})
    _liga_contato_ao_cdp("5511900000003", "uuid-cdp-3")
    _cdp_tem(monkeypatch, push)

    linha = _enfileira_e_reserva(push, int(c["id"]))
    client = _FakeClient(_FakeResp(500, {}))     # qualquer chamada seria erro
    assert _run(push.deliver_one(client, linha)) == "dry_run"
    assert client.calls == []                    # NADA foi à rede


def test_push_grava_e_carimba_quando_o_modo_seco_sai(plugin_app, monkeypatch):
    built = plugin_app("trackify", settings_overrides=_field_sync_on(
        **{"plugin.trackify.field_sync_dry_run": False}))
    push = _load("push")
    linha_map = _mapeia(built.client, "cpf", "cpf")

    from db.repositories import contact_repo, custom_attribute_repo
    from db.tables import contacts as contacts_tbl
    c = contact_repo.get_or_create("5511900000004")
    custom_attribute_repo.set_values(contacts_tbl, int(c["id"]), {"cpf": "12345"})
    _liga_contato_ao_cdp("5511900000004", "uuid-cdp-4")
    _cdp_tem(monkeypatch, push)

    linha = _enfileira_e_reserva(push, int(c["id"]))
    client = _FakeClient(_FakeResp(200, _cdp_contact(cpf="12345")))
    assert _run(push.deliver_one(client, linha)) == "sent"
    assert client.calls[0]["json"] == {"fieldValues": {"cpf": "12345"}}

    # Carimbado: o próximo ciclo não reenvia.
    plano = _run(push.plan_for_contact(_FakeHttp(), int(c["id"])))
    assert plano.field_values == {}
    from sqlalchemy import text as _t

    from db.engine import get_engine
    with get_engine().connect() as conn:
        ok = conn.execute(_t("SELECT pushed_ok FROM plugin_trackify_field_map "
                             "WHERE id = :i"), {"i": linha_map["id"]}).scalar()
    assert ok == 1


def test_push_com_409_nao_retenta_e_marca_conflito(plugin_app, monkeypatch):
    """O 409 (outro cadastro é dono do e-mail) é a falha ESPERADA desta feature.
    Re-tentar oito vezes não muda nada — precisa de decisão humana."""
    built = plugin_app("trackify", settings_overrides=_field_sync_on(
        **{"plugin.trackify.field_sync_dry_run": False}))
    push = _load("push")
    linha_map = _mapeia(built.client, "email", "email")

    from db.repositories import contact_repo, custom_attribute_repo
    from db.tables import contacts as contacts_tbl
    c = contact_repo.get_or_create("5511900000005")
    custom_attribute_repo.set_values(contacts_tbl, int(c["id"]),
                                     {"email": "dup@empresa.com"})
    _liga_contato_ao_cdp("5511900000005", "uuid-cdp-5")
    _cdp_tem(monkeypatch, push)

    linha = _enfileira_e_reserva(push, int(c["id"]))
    client = _FakeClient(_FakeResp(409, {"message": "Email já cadastrado"}))
    assert _run(push.deliver_one(client, linha)) == "conflict"

    from sqlalchemy import text as _t

    from db.engine import get_engine
    with get_engine().connect() as conn:
        st = conn.execute(_t("SELECT status, attempts FROM plugin_trackify_field_outbox "
                             "WHERE id = :i"), {"i": linha["id"]}).mappings().first()
        conf = conn.execute(_t("SELECT conflict, conflict_reason "
                               "FROM plugin_trackify_field_state WHERE map_id = :m"),
                            {"m": linha_map["id"]}).mappings().first()
    assert st["status"] == "blocked" and st["attempts"] == 0   # NÃO re-tentou
    assert conf["conflict"] == 1 and "cadastrado" in conf["conflict_reason"]

    d = built.client.get("/api/plugins/trackify/field-sync/status").json()["data"]
    assert len(d["conflicts"]) == 1


# ── Puros: cursor e supressão de eco do poller ───────────────────────────

@pytest.fixture(scope="module")
def pull():
    return _load("pull")


def test_cursor_e_relogio_saem_do_servidor(pull):
    """O fatiamento por contato e o ``merge_chunks`` deixaram de existir.

    Eles só existiam porque não havia índice em ``created_at`` sozinho do lado do
    CDP, e um "tudo que mudou desde T" global varria a tabela de auditoria. O
    índice agora existe lá e o cursor keyset é responsabilidade do servidor —
    junto com o relógio, que precisa ser o do BANCO e não o desta máquina.
    """
    tk = _load("client")
    capturado = {}

    async def fake(http, *, since=0.0, since_id="", limit=500, field_slugs=None):
        capturado.update({"since": since, "since_id": since_id,
                          "field_slugs": field_slugs})
        return tk.Result(tk.OK, 200, data={
            "data": [{"id": "r-2", "contactId": "c", "customFieldId": "f",
                      "newValue": "x", "userId": "u", "createdEpoch": 120.0}],
            "meta": {"serverEpoch": 200.0, "truncated": False,
                     "nextSince": 120.0, "nextSinceId": "r-2"},
        })

    original = tk.changelog
    tk.changelog = fake
    try:
        pagina = _run(pull.fetch_changes(_FakeHttp(), (55.0, "r-1"), ["email"]))
    finally:
        tk.changelog = original

    assert capturado == {"since": 55.0, "since_id": "r-1", "field_slugs": ["email"]}
    assert pagina["server_epoch"] == 200.0
    assert pagina["next"] == (120.0, "r-2")
    assert pagina["rows"][0]["row_id"] == "r-2"
    # As chaves camelCase da API são traduzidas na BORDA, para `apply_row` (e os
    # testes dele) não saberem que o transporte mudou.
    assert pagina["rows"][0]["tk_contact_id"] == "c"
    assert pagina["rows"][0]["field_id"] == "f"


def test_falha_de_leitura_nao_move_o_cursor(pull):
    tk = _load("client")

    async def fake(http, **k):
        return tk.Result(tk.RETRY, 500, "fora do ar")

    original = tk.changelog
    tk.changelog = fake
    try:
        pagina = _run(pull.fetch_changes(_FakeHttp(), (55.0, "r-1"), []))
    finally:
        tk.changelog = original

    assert pagina["rows"] == [] and pagina["next"] == (55.0, "r-1")
    # Sem relógio do servidor o ciclo não tem como aplicar a margem de segurança.
    assert pagina["server_epoch"] == 0.0


def test_eco_e_reconhecido_pelo_ATOR_da_escrita(pull):
    """Com API key a procedência é exata: uma linha assinada por
    ``apikey:<nossa chave>`` só pode ter saído daqui.

    Com o cookie de sessão isso não era verdade — a escrita da integração e a
    edição de uma pessoa usando as mesmas credenciais chegavam com o mesmo
    ``user_id``, e era preciso comparar o valor para desempatar.
    """
    assert pull._e_nosso_eco(
        {"user_id": "apikey:k1", "new_value": "a@b.com"}, {}, "apikey:k1") is True

    # Uma PESSOA editando na tela do Trackify: outro ator, nunca é eco — mesmo
    # que por acaso tenha escrito o mesmo valor que nós.
    codec = _load("field_codec")
    nosso = {"tk_hash": codec.hash_value("a@b.com")}
    assert pull._e_nosso_eco(
        {"user_id": "humano-2", "new_value": "outro@b.com"}, nosso, "apikey:k1") is False


def test_eco_por_valor_continua_como_segunda_camada(pull):
    """Não é redundância: pega a linha de ingestion/merge/import que carrega o
    valor que nós mesmos acabamos de escrever — essa não tem o nosso ator."""
    codec = _load("field_codec")
    nosso = {"tk_hash": codec.hash_value("a@b.com")}

    assert pull._e_nosso_eco(
        {"user_id": "", "source": "ingestion", "new_value": "a@b.com"},
        nosso, "apikey:k1") is True

    # Valor diferente do que enviamos: é mudança de verdade.
    assert pull._e_nosso_eco(
        {"user_id": "", "new_value": "outro@b.com"}, nosso, "apikey:k1") is False

    # Sem memória do que enviamos, não dá para afirmar que é eco.
    assert pull._e_nosso_eco(
        {"user_id": "humano", "new_value": "a@b.com"}, {}, "apikey:k1") is False


def test_eco_nao_ressuscita_valor_antigo(pull):
    """Por que a 2ª camada compara com o que NÓS enviamos e não com o valor atual
    do WhatsBot: entre o nosso envio e a leitura o operador pode ter mudado o
    campo de novo, e a linha antiga sobrescreveria a mudança nova."""
    codec = _load("field_codec")
    estado = {"tk_hash": codec.hash_value("enviado@x.com")}
    linha_antiga = {"user_id": "", "new_value": "enviado@x.com"}
    assert pull._e_nosso_eco(linha_antiga, estado, "apikey:k1") is True


# ── Com app: volta do Trackify ───────────────────────────────────────────

def _pagina(monkeypatch, pull_mod, linhas, *, truncated=False,
            server_epoch=2_000_000_000.0):
    """Uma página do ``GET /contact-changelog``, já normalizada.

    O ``server_epoch`` é o relógio do BANCO do CDP: é dele que sai a margem de
    segurança de 5s contra transações que commitam fora de ordem.
    """
    ultimo = linhas[-1] if linhas else None

    async def fake(http, since, field_slugs):
        return {
            "rows": list(linhas),
            "next": ((ultimo["created_epoch"], ultimo["row_id"]) if ultimo else since),
            "truncated": truncated,
            "server_epoch": server_epoch,
        }
    monkeypatch.setattr(pull_mod, "fetch_changes", fake)


def test_pull_grava_avisa_o_painel_e_nao_emite_contact_updated(plugin_app, monkeypatch):
    """Contrato anti-laço: o refresh é o broadcast de WebSocket, NUNCA o evento
    de barramento ``contact.updated`` — forjá-lo dispararia o nosso próprio
    espelho, que mandaria ao CDP um evento sobre uma mudança que VEIO do CDP.
    """
    built = plugin_app("trackify", settings_overrides=_field_sync_on(**{
        "plugin.trackify.field_sync_pull_enabled": True,
        "plugin.trackify.sync_api_key_id": "k1",
    }))
    pull_mod = _load("pull")
    _mapeia(built.client, "cpf", "cpf", direction="to_whatsbot")

    from db.repositories import contact_repo, custom_attribute_repo
    from db.tables import contacts as contacts_tbl
    c = contact_repo.get_or_create("5511900000010")
    _liga_contato_ao_cdp("5511900000010", "uuid-cdp-10")

    # O mapeamento precisa do id do campo no CDP para casar com o changelog.
    from sqlalchemy import text as _t

    from db.engine import get_engine
    with get_engine().begin() as conn:
        conn.execute(_t("UPDATE plugin_trackify_field_map SET tk_field_id = 'f-cpf'"))

    # Sem API key o ciclo sai antes de qualquer leitura (no-op por desenho).
    monkeypatch.setattr(pull_mod.tk_client, "is_configured", lambda: True)
    _pagina(monkeypatch, pull_mod, [
        {"row_id": "r1", "tk_contact_id": "uuid-cdp-10",
         "field_id": "f-cpf", "slug": "cpf", "new_value": "55566677788",
         "source": "manual", "user_id": "humano-2",
         "created_epoch": 1_999_999_000.0}])

    emitidos, avisos = [], []
    import plugins.context as pctx
    monkeypatch.setattr(pctx, "broadcast",
                        lambda ev, payload: avisos.append((ev, payload)))
    from plugins import events as bus
    monkeypatch.setattr(bus, "emit", lambda ev, payload=None: emitidos.append(ev))

    resumo = _run(pull_mod.cycle(_FakeHttp()))
    assert resumo["gravadas"] == 1

    valores = custom_attribute_repo.get_values(contacts_tbl, int(c["id"]))
    assert valores["cpf"] == "55566677788"
    assert [e for e, _ in avisos] == ["contact_info_updated"]
    assert "contact.updated" not in emitidos


def test_pull_ignora_o_que_a_propria_conta_de_servico_escreveu(plugin_app, monkeypatch):
    """Sem o id da conta de serviço não há supressão de eco possível, então o
    ciclo NÃO roda em vez de reimportar as próprias escritas."""
    built = plugin_app("trackify", settings_overrides=_field_sync_on(**{
        "plugin.trackify.field_sync_pull_enabled": True,
        "plugin.trackify.sync_api_key_id": "",
    }))
    pull_mod = _load("pull")
    _mapeia(built.client, "cpf", "cpf", direction="to_whatsbot")

    chamou = []

    async def _nunca(*a, **k):
        chamou.append(1)
        return {"rows": [], "next": (0.0, ""), "truncated": False,
                "server_epoch": 0.0}
    monkeypatch.setattr(pull_mod, "fetch_changes", _nunca)
    resumo = _run(pull_mod.cycle(_FakeHttp()))
    assert resumo["lidas"] == 0 and chamou == []


def test_pull_recusa_valor_invalido_sem_gravar_e_marca_para_nao_repetir(
        plugin_app, monkeypatch):
    """Valor que a validação do WhatsBot rejeita não pode ser gravado nem
    re-tentado para sempre."""
    built = plugin_app("trackify", settings_overrides=_field_sync_on(**{
        "plugin.trackify.field_sync_pull_enabled": True,
        "plugin.trackify.sync_api_key_id": "k1",
    }))
    pull_mod = _load("pull")

    from db.repositories import contact_repo, custom_attribute_repo
    from db.tables import contacts as contacts_tbl
    # Atributo de DATA: o CDP vai mandar lixo que não é data.
    r = built.client.post("/api/custom-attributes", json={
        "attribute_key": "data_teste_pull", "display_name": "Data teste",
        "type": "date", "applies_to": "contact"})
    assert r.status_code in (200, 400)     # 400 = já existe de uma rodada anterior
    _mapeia(built.client, "data_teste_pull", "data_cdp", direction="to_whatsbot")

    c = contact_repo.get_or_create("5511900000011")
    _liga_contato_ao_cdp("5511900000011", "uuid-cdp-11")
    from sqlalchemy import text as _t

    from db.engine import get_engine
    with get_engine().begin() as conn:
        conn.execute(_t("UPDATE plugin_trackify_field_map SET tk_field_id = 'f-data'"))

    # Sem API key o ciclo sai antes de qualquer leitura (no-op por desenho).
    monkeypatch.setattr(pull_mod.tk_client, "is_configured", lambda: True)
    _pagina(monkeypatch, pull_mod, [
        {"row_id": "r9", "tk_contact_id": "uuid-cdp-11",
         "field_id": "f-data", "slug": "data_cdp", "new_value": "system",
         "source": "manual", "user_id": "humano-2",
         "created_epoch": 1_999_999_000.0}])

    resumo = _run(pull_mod.cycle(_FakeHttp()))
    assert resumo["recusadas"] == 1 and resumo["gravadas"] == 0
    assert "data_teste_pull" not in custom_attribute_repo.get_values(
        contacts_tbl, int(c["id"]))

    with get_engine().connect() as conn:
        rej = conn.execute(_t("SELECT rejected_hash FROM plugin_trackify_field_state "
                              "WHERE contact_id = :c"), {"c": int(c["id"])}).scalar()
    assert rej          # carimbado: a varredura não redetecta a mesma divergência


# ── Com app: varredura de conferência ────────────────────────────────────

def test_conferencia_pega_o_que_o_csv_deixou_invisivel(plugin_app, monkeypatch):
    """Importação por CSV não emite evento NENHUM — sem a varredura, 3 mil
    e-mails importados nunca chegariam ao CDP."""
    built = plugin_app("trackify", settings_overrides=_field_sync_on())
    reconcile = _load("reconcile")
    push = _load("push")
    _mapeia(built.client, "cpf", "cpf")

    from db.repositories import contact_repo, custom_attribute_repo
    from db.tables import contacts as contacts_tbl
    c = contact_repo.get_or_create("5511900000020")
    _liga_contato_ao_cdp("5511900000020", "uuid-cdp-20")
    # Escrita "por CSV": direto no repositório, sem passar por rota nem evento.
    custom_attribute_repo.set_values(contacts_tbl, int(c["id"]), {"cpf": "77788899900"})
    _cdp_tem(monkeypatch, push)

    from sqlalchemy import text as _t

    from db.engine import get_engine
    with get_engine().begin() as conn:
        conn.execute(_t("DELETE FROM plugin_trackify_field_outbox"))
    sync_state = _load("sync_state")
    sync_state.set_cursor("reconcile", 0, "", "")

    resumo = _run(reconcile.cycle(_FakeHttp()))
    assert resumo["enfileirados"] >= 1
    with get_engine().connect() as conn:
        n = conn.execute(_t("SELECT COUNT(*) FROM plugin_trackify_field_outbox "
                            "WHERE contact_id = :c"), {"c": int(c["id"])}).scalar()
    assert n == 1


def test_conferencia_respeita_o_teto_de_envios(plugin_app, monkeypatch):
    """Sem teto, a primeira execução sobre milhares de contatos satura o
    orçamento de 30/min por horas E inunda a fila de automações do Trackify."""
    built = plugin_app("trackify", settings_overrides=_field_sync_on())
    reconcile = _load("reconcile")
    push = _load("push")
    _mapeia(built.client, "cpf", "cpf")

    from db.repositories import contact_repo, custom_attribute_repo
    from db.tables import contacts as contacts_tbl
    from sqlalchemy import text as _t

    from db.engine import get_engine
    with get_engine().begin() as conn:
        conn.execute(_t("DELETE FROM plugin_trackify_field_outbox"))
        conn.execute(_t("DELETE FROM plugin_trackify_identity"))

    for i in range(reconcile.MAX_PUSHES + 5):
        phone = f"55119011{i:05d}"
        c = contact_repo.get_or_create(phone)
        custom_attribute_repo.set_values(contacts_tbl, int(c["id"]), {"cpf": f"{i:011d}"})
        _liga_contato_ao_cdp(phone, f"uuid-lote-{i}")
    _cdp_tem(monkeypatch, push)
    _load("sync_state").set_cursor("reconcile", 0, "", "")

    resumo = _run(reconcile.cycle(_FakeHttp()))
    assert resumo["enfileirados"] == reconcile.MAX_PUSHES
    # E o cursor andou, para o resto entrar no ciclo seguinte em vez de sumir.
    cur = _load("sync_state").get_cursor("reconcile")
    assert cur["cursor_id"]


def test_todas_as_rotas_novas_respondem_sem_configuracao(plugin_app):
    """Fumaça: o plugin sobe e nenhuma rota nova quebra numa instalação crua.

    Sem credencial e sem mapeamento é o estado de TODA instalação no
    primeiro boot — e é exatamente quando um 500 numa rota deixaria a aba em
    branco, sem explicação.
    """
    built = plugin_app("trackify")
    for path in ("/api/plugins/trackify/health",
                 "/api/plugins/trackify/contact-attributes",
                 "/api/plugins/trackify/trackify-fields",
                 "/api/plugins/trackify/mappings",
                 "/api/plugins/trackify/api-key",
                 "/api/plugins/trackify/field-sync/status"):
        r = built.client.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code} {r.text[:200]}"
        assert r.json()["ok"] is True

    r = built.client.post("/api/plugins/trackify/api-key/test", json={})
    assert r.status_code == 200 and r.json()["data"]["ok"] is False

    # Simular exige contato: uma varredura da base inteira não pode rodar dentro
    # da requisição.
    assert built.client.post("/api/plugins/trackify/field-sync/run",
                             json={}).status_code == 400
    r = built.client.post("/api/plugins/trackify/field-sync/run",
                          json={"contact_id": 999999})
    assert r.status_code == 200 and r.json()["data"]["skip"]


def test_tela_de_configuracao_e_servida(plugin_app):
    """A aba nova é um módulo próprio: se o mount estático não servir o arquivo,
    a tela quebra em runtime com um import que falha — e nenhum teste de Python
    pegaria isso."""
    built = plugin_app("trackify")
    for asset in ("config.js", "FieldSync.js"):
        r = built.client.get(f"/plugins/trackify/static/{asset}")
        assert r.status_code == 200, asset
    # E o config.js precisa de fato importar o módulo novo.
    assert "FieldSync.js" in built.client.get(
        "/plugins/trackify/static/config.js").text


# ── Puros: cadastro completo e produtos na jornada ───────────────────────

def test_cadastro_traz_campo_vazio_em_vez_de_omitir(journey, monkeypatch):
    """A aba "Informações do Contato" mostra TODO campo ativo, com o valor em
    branco quando o contato não tem aquele dado. Campo ausente da lista é
    indistinguível de campo vazio, e "sem CPF cadastrado" é justamente o que o
    atendente precisa enxergar."""
    tk = _load("client")

    async def _contato(http, cid):
        return tk.Result(tk.OK, 200, data={
            "id": "uuid-1", "status": "customer", "totalSpent": "1234.5",
            "firstSeenAt": "2026-07-31T00:00:00", "convertedAt": None,
            "contactFieldValues": [
                {"value": "leandro@exemplo.com", "customField": {"slug": "email"}},
                {"value": "Leandro", "customField": {"slug": "name"}},
                {"value": "GO", "customField": {"slug": "estado"}},
            ],
            "contactTags": [{"tag": {"name": "vip", "color": "#fff"}}],
        })

    async def _catalogo(http):
        return tk.Result(tk.OK, 200, data=[
            {"slug": "email", "name": "Email", "isIdentifier": True, "isActive": True,
             "identifierPriority": 10},
            {"slug": "cpf", "name": "CPF", "isIdentifier": True, "isActive": True,
             "identifierPriority": 30},
            {"slug": "name", "name": "Nome", "isIdentifier": False, "isActive": True},
            {"slug": "cidade", "name": "Cidade", "isIdentifier": False, "isActive": True},
            {"slug": "estado", "name": "Estado", "isIdentifier": False, "isActive": True},
            # Campo desativado não entra na tela.
            {"slug": "morto", "name": "Morto", "isIdentifier": False, "isActive": False},
        ])

    monkeypatch.setattr(journey.tk_client, "get_contact", _contato)
    monkeypatch.setattr(journey.tk_client, "custom_fields", _catalogo)
    journey.tk_client.reset_for_tests()

    out = _run(journey.fetch_identity(_FakeHttp(), "uuid-1"))
    assert [f["slug"] for f in out["identifiers"]] == ["email", "cpf"]
    assert [f["slug"] for f in out["fields"]] == ["cidade", "estado", "name"]
    # Vazio vira string vazia (a tela mostra "—"), NUNCA some da lista.
    assert next(f for f in out["identifiers"] if f["slug"] == "cpf")["value"] == ""
    assert out["name"] == "Leandro"
    assert out["total_spent"] == "R$ 1.234,50"
    assert out["tags"] == [{"name": "vip", "color": "#fff"}]


def test_cadastro_parte_do_catalogo_e_nao_dos_valores(journey, monkeypatch):
    """Se a lista saísse dos valores do contato, campo sem valor jamais
    apareceria — que é o bug que este teste tranca."""
    tk = _load("client")
    pediu = []

    async def _contato(http, cid):
        return tk.Result(tk.OK, 200, data={"id": "uuid-1", "contactFieldValues": []})

    async def _catalogo(http):
        pediu.append(1)
        return tk.Result(tk.OK, 200, data=[
            {"slug": "cpf", "name": "CPF", "isIdentifier": True, "isActive": True}])

    monkeypatch.setattr(journey.tk_client, "get_contact", _contato)
    monkeypatch.setattr(journey.tk_client, "custom_fields", _catalogo)
    journey.tk_client.reset_for_tests()

    out = _run(journey.fetch_identity(_FakeHttp(), "uuid-1"))
    assert pediu, "o catálogo de campos precisa ser consultado"
    assert [f["slug"] for f in out["identifiers"]] == ["cpf"]
    assert out["identifiers"][0]["value"] == ""


def _ev(tipo, quando, campos, valor=None, effect="add"):
    """Uma linha de ``GET /contacts/:id/purchases``, no formato da API.

    ``effect`` é o veredito da ``channel_value_rules`` do CDP — ``add`` (compra),
    ``subtract`` (reembolso) ou ``ignore`` (só estado). É ele, e não o tipo do
    evento, que decide se a linha cria produto.
    """
    import datetime as _dt
    # ``title`` faz parte da linha porque o histórico por produto reusa o
    # formato da linha do tempo — é o corpo da linha no ``EventRow``.
    return {"id": f"e-{quando}", "eventType": tipo, "title": tipo, "effect": effect,
            "value": str(valor) if valor is not None else None,
            "occurredAt": _dt.datetime(2026, 1, quando, 12, 0),
            "channel": "ticto", "fields": campos}


def _mock_compras(journey, monkeypatch, linhas, unruled=(), unnamed=()):
    """Substitui a chamada a ``GET /contacts/:id/purchases``.

    Uma rota só devolve os eventos e os dois diagnósticos — antes eram três
    consultas SQL separadas.
    """
    tk = _load("client")

    async def fake(http, contact_id, **kwargs):
        return tk.Result(tk.OK, 200, data={
            "events": list(linhas),
            "diagnostics": {
                "unruledProductEvents": list(unruled),
                "unnamedPurchaseEvents": list(unnamed),
            },
        })
    monkeypatch.setattr(journey.tk_client, "purchases", fake)


def test_compra_e_o_que_a_regra_de_valor_diz_que_e(journey, monkeypatch):
    """A definição de compra sai do CDP (``effect='add'``), não de uma lista de
    tipos chumbada aqui. Um evento que o canal marcou ``ignore`` e que não é
    estado de nada não inventa produto."""
    _mock_compras(journey, monkeypatch, [
        _ev("purchase", 5, {"product_name": "Curso Avulso",
                            "payment_method": "Pix"}, 197),
        _ev("webinar_assistido", 4, {"product_name": "Curso Avulso"}, effect="ignore"),
    ])
    itens = _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))["items"]
    assert [p["name"] for p in itens] == ["Curso Avulso"]
    assert itens[0]["purchases"] == 1
    assert itens[0]["paid_total"] == "R$ 197,00"
    assert itens[0]["payment_method"] == "Pix"


def test_reembolso_prova_que_houve_compra_e_lista_o_produto(journey, monkeypatch):
    """Os 7 pares recuperados pela regra nova: produto comprado ANTES do CDP,
    cujo único rastro é o reembolso. Não existe reembolso sem venda."""
    _mock_compras(journey, monkeypatch, [
        _ev("refunded", 20, {"product_name": "Comprado antes do CDP"}, 97,
            effect="subtract"),
    ])
    itens = _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))["items"]
    assert len(itens) == 1
    p = itens[0]
    assert p["name"] == "Comprado antes do CDP"
    assert p["purchases"] == 0                  # nenhum evento de compra sobrou
    assert p["last_event_type"] == "refunded" and p["last_effect"] == "subtract"


def test_produto_cancelado_continua_na_lista(journey, monkeypatch):
    """O selo ROTULA, nunca filtra: se o contato comprou alguma vez, aparece."""
    _mock_compras(journey, monkeypatch, [
        _ev("subscription_canceled", 20, {"product_name": "Combo de Redes"},
            effect="ignore"),
        _ev("active_subscription", 10, {"product_name": "Combo de Redes",
                                        "subscription_interval": "Mensal"}, 97),
    ])
    itens = _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))["items"]
    assert [p["name"] for p in itens] == ["Combo de Redes"]
    p = itens[0]
    assert p["last_event_type"] == "subscription_canceled"
    assert p["last_effect"] == "ignore"
    assert p["purchases"] == 1 and p["paid_total"] == "R$ 97,00"
    # Atributo que só o evento ANTIGO trazia é preservado.
    assert p["interval"] == "Mensal"


def test_estado_sozinho_nao_inventa_produto(journey, monkeypatch):
    """Trava a 2ª passada: cancelamento sem NENHUM evento de dinheiro é órfão —
    rotula quem existe, e é descartado quando não há quem rotular."""
    _mock_compras(journey, monkeypatch, [
        _ev("subscription_canceled", 20, {"product_name": "Fantasma"},
            effect="ignore"),
    ])
    assert _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))["items"] == []


def test_total_pago_soma_add_e_desconta_subtract(journey, monkeypatch):
    _mock_compras(journey, monkeypatch, [
        _ev("refunded", 20, {"product_name": "Curso"}, 50, effect="subtract"),
        _ev("purchase", 10, {"product_name": "Curso"}, 200),
    ])
    p = _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))["items"][0]
    assert p["paid_total"] == "R$ 150,00" and p["paid_total_raw"] == 150.0
    assert p["refunded"] == "R$ 50,00" and p["refunded_raw"] == 50.0
    assert p["purchases"] == 1


def test_total_pago_nunca_fica_negativo(journey, monkeypatch):
    """Espelho deliberado do ``clampFloor`` do CDP (que trava ``total_spent`` em
    zero): as duas telas têm que contar a mesma história. O valor descontado não
    some — aparece em ``refunded``."""
    _mock_compras(journey, monkeypatch, [
        _ev("chargeback", 20, {"product_name": "Só perda"}, 97, effect="subtract"),
    ])
    p = _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))["items"][0]
    assert p["paid_total"] == "R$ 0,00" and p["paid_total_raw"] == 0.0
    assert p["refunded"] == "R$ 97,00"
    assert p["first_purchase_at"] is None and p["last_purchase_at"] is None


def test_uma_linha_por_produto_mesmo_com_varias_compras(journey, monkeypatch):
    _mock_compras(journey, monkeypatch, [
        _ev("purchase", 20, {"product_name": "Curso"}, 100),
        _ev("purchase", 10, {"product_name": "Curso"}, 100),
        _ev("purchase", 5, {"product_name": "Curso"}, 100),
    ])
    itens = _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))["items"]
    assert len(itens) == 1
    p = itens[0]
    assert p["purchases"] == 3 and p["paid_total"] == "R$ 300,00"
    assert p["first_purchase_at"].startswith("2026-01-05")
    assert p["last_purchase_at"].startswith("2026-01-20")


def test_assinatura_id_agrupa_renovacoes_do_mesmo_produto(journey, monkeypatch):
    """``subscription_id`` é a única chave estável entre renovações — inclusive
    quando o nome do produto muda de grafia entre uma cobrança e outra."""
    _mock_compras(journey, monkeypatch, [
        _ev("charge.paid", 20, {"product_name": "Combo de Redes (2026)",
                                "subscription_id": "sub-7"}, 97),
        _ev("active_subscription", 10, {"product_name": "Combo de Redes",
                                        "subscription_id": "sub-7"}, 97),
    ])
    itens = _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))["items"]
    assert len(itens) == 1
    assert itens[0]["key"] == "sub-7" and itens[0]["purchases"] == 2


def test_evento_sem_nome_de_produto_nao_vira_produto(journey, monkeypatch):
    """Sem esta guarda, cair no ``event_type`` como chave inventaria um produto
    chamado "purchase" na tela do atendente."""
    _mock_compras(journey, monkeypatch, [
        _ev("purchase", 5, {"transaction_id": "abc"}, 10),
        _ev("charge.paid", 6, {}, 10),
    ])
    assert _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))["items"] == []


def test_compra_sem_nome_cai_no_id_do_produto(journey, monkeypatch):
    """Caso REAL do canal ``pagarme``: 1.281 ``charge.paid`` na produção, ZERO
    com ``product_name``/``offer_name`` — ele grava ``product_id``/``offer_id``.
    Sem este degrau, uma compra paga e já somada no "Total gasto" não virava
    linha nenhuma e a aba saía vazia sem explicação."""
    _mock_compras(journey, monkeypatch, [
        _ev("charge.paid", 10, {"offer_id": "oferta-principal",
                                "product_id": "produto", "status": "paid"}, 5),
        _ev("charge.paid", 5, {"offer_id": "upssel", "status": "paid"}, 3),
    ])
    itens = _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))["items"]
    assert sorted(p["name"] for p in itens) == ["produto", "upssel"]


def test_id_vira_nome_quando_outro_evento_do_mesmo_produto_o_traz(journey, monkeypatch):
    """O recurso ao id não pode PARTIR um produto em duas linhas: no ``ticto``
    parte dos eventos traz o nome e parte só o id. Quem souber traduzir são os
    próprios eventos do contato."""
    _mock_compras(journey, monkeypatch, [
        _ev("purchase", 20, {"product_id": "prod-42"}, 100),
        _ev("purchase", 10, {"product_id": "prod-42",
                             "product_name": "Combo de Redes"}, 100),
    ])
    itens = _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))["items"]
    assert len(itens) == 1
    assert itens[0]["name"] == "Combo de Redes" and itens[0]["purchases"] == 2


def test_compra_sem_nome_nem_id_e_reportada(journey, monkeypatch):
    """O diagnóstico IRMÃO do ``unruled``, e o oposto dele: aqui é dinheiro
    RECONHECIDO que não diz o quê foi comprado. Sem ele, o caso do ``pagarme``
    mandava configurar o canal de checkout — que não era o problema."""
    _mock_compras(journey, monkeypatch, [], unnamed=[
        {"channel": "pagarme", "eventType": "charge.paid", "events": 4},
    ])
    out = _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))
    assert out["items"] == [] and out["unruled"] == []
    assert out["unnamed"] == [{"channel": "pagarme", "event_type": "charge.paid",
                               "events": 4}]


def test_cada_produto_carrega_o_proprio_historico(journey, monkeypatch):
    """"Ver histórico" abre sem roundtrip porque a consulta JÁ lê estas linhas —
    antes elas eram agrupadas e jogadas fora. Inclui o evento de ESTADO: é ele
    que explica o selo que a linha mostra."""
    _mock_compras(journey, monkeypatch, [
        _ev("subscription_canceled", 20, {"product_name": "Combo"}, effect="ignore"),
        _ev("charge.paid", 10, {"product_name": "Combo", "status": "paid"}, 97),
        _ev("charge.paid", 5, {"product_name": "Combo", "status": "paid"}, 97),
    ])
    p = _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))["items"][0]
    assert p["purchases"] == 2                       # o cancelamento não é compra…
    assert len(p["events"]) == 3                     # …mas ENTRA no histórico
    assert [e["event_type"] for e in p["events"]] == [
        "subscription_canceled", "charge.paid", "charge.paid"]   # mais novo primeiro


def test_historico_do_produto_usa_o_mesmo_formato_da_linha_do_tempo(journey, monkeypatch):
    """Mesmo serializador (`_event_row`) ⇒ o modal reusa o `EventRow` da timeline
    em vez de um segundo jeito de desenhar evento. Trocar o formato aqui quebra
    a tela em silêncio."""
    _mock_compras(journey, monkeypatch, [
        _ev("charge.paid", 10, {"product_name": "Combo", "transaction_id": "tx-1"}, 97),
    ])
    evento = _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))["items"][0]["events"][0]
    # Os DOIS serializadores existem (a timeline recebe a linha crua da API, o
    # histórico do produto recebe a já achatada), e é justamente por isso que a
    # paridade precisa de teste: divergir aqui quebra a tela em silêncio.
    modelo = journey._event_row({
        "id": "x", "eventType": "t", "title": "T", "value": None,
        "occurredAt": None, "channel": {"slug": "c"}, "eventFieldValues": []})
    assert set(evento) == set(modelo)
    assert evento["value"] == "R$ 97,00" and evento["fields"]["transaction_id"] == "tx-1"
    # `title` é o corpo da linha no EventRow — sem ele, a tela fica muda.
    assert evento["title"] == "charge.paid"


def test_campos_de_identificacao_saem_da_configuracao(journey, monkeypatch):
    """Gateway novo com slug próprio vira CONFIGURAÇÃO, não release do plugin."""
    monkeypatch.setattr(journey._config, "product_identity_fields",
                        lambda: ["plan_name"])
    _mock_compras(journey, monkeypatch, [
        _ev("purchase", 10, {"plan_name": "Plano Anual"}, 500),
        _ev("purchase", 5, {"product_name": "Fora da lista"}, 100),
    ])
    itens = _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))["items"]
    assert [p["name"] for p in itens] == ["Plano Anual"]


def test_slug_novo_de_id_e_traduzido_pelo_par_name(journey, monkeypatch):
    """O par id↔nome é DERIVADO (`plan_id` → `plan_name`), não uma dupla
    chumbada — senão um slug novo voltaria a partir o produto em duas linhas."""
    assert journey._name_partner("plan_id") == "plan_name"
    assert journey._name_partner("product_name") is None
    monkeypatch.setattr(journey._config, "product_identity_fields",
                        lambda: ["plan_name", "plan_id"])
    _mock_compras(journey, monkeypatch, [
        _ev("purchase", 20, {"plan_id": "p-9"}, 100),
        _ev("purchase", 10, {"plan_id": "p-9", "plan_name": "Plano Anual"}, 100),
    ])
    itens = _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))["items"]
    assert len(itens) == 1
    assert itens[0]["name"] == "Plano Anual" and itens[0]["purchases"] == 2


def test_lista_de_campos_vazia_volta_ao_padrao(journey, monkeypatch):
    """Apagar o campo na tela não pode apagar a aba Produtos inteira."""
    cfg = journey._config
    monkeypatch.setattr(cfg, "setting", lambda k, d=None: "" if k == "product_identity_fields" else d)
    assert cfg.product_identity_fields() == list(cfg.PRODUCT_IDENTITY_FIELDS)
    # Espaço solto e duplicata não viram campo fantasma, e a ORDEM é preservada.
    monkeypatch.setattr(cfg, "setting",
                        lambda k, d=None: " offer_id , product_name ,offer_id" if k == "product_identity_fields" else d)
    assert cfg.product_identity_fields() == ["offer_id", "product_name"]


def _stub_blocos(monkeypatch, journey, *, identity=None, subs=None,
                 purchases=None, timeline=None, tipos=None):
    """Substitui os quatro blocos de ``build_journey`` por valores fixos."""
    async def _ident(http, cid):
        return identity if identity is not None else {"contact_id": cid}

    async def _subs(http, cid, **k):
        return subs if subs is not None else []

    async def _compras(http, cid, **k):
        return purchases if purchases is not None else {"items": [], "unruled": []}

    async def _linha(http, cid, **k):
        return timeline if timeline is not None else {"events": []}

    async def _tipos(http, cid):
        return tipos if tipos is not None else []

    monkeypatch.setattr(journey, "fetch_identity", _ident)
    monkeypatch.setattr(journey, "fetch_subscriptions", _subs)
    monkeypatch.setattr(journey, "fetch_purchases", _compras)
    monkeypatch.setattr(journey, "fetch_timeline", _linha)
    monkeypatch.setattr(journey, "fetch_event_types", _tipos)


def _config_identity_fields(journey):
    return journey._config.product_identity_fields()


def _campos_pedidos(journey, monkeypatch):
    """Quais ``identityFields`` o plugin manda para a rota de compras."""
    tk = _load("client")
    visto = {}

    async def fake(http, contact_id, **kwargs):
        visto["campos"] = kwargs.get("identity_fields")
        return tk.Result(tk.OK, 200, data={"events": [], "diagnostics": {}})
    monkeypatch.setattr(journey.tk_client, "purchases", fake)
    _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))
    return visto["campos"]


def test_diagnostico_diz_quais_campos_o_evento_traz(journey, monkeypatch):
    """Sem isso o operador não teria como adivinhar qual slug configurar."""
    _mock_compras(journey, monkeypatch, [], unnamed=[
        {"channel": "pagarme", "eventType": "charge.paid", "events": 4,
         "fields": "card_brand, status, transaction_id"},
    ])
    assert _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))["unnamed"][0]["fields"] == (
        "card_brand, status, transaction_id")
    # E o plugin manda os campos CONFIGURADOS, não uma lista fixa — quem
    # consulta é o Trackify, mas quem sabe o que nomeia um produto é daqui.
    assert _campos_pedidos(journey, monkeypatch) == _config_identity_fields(journey)


def test_os_dois_diagnosticos_nao_se_misturam(journey, monkeypatch):
    """Trocar um pelo outro aponta o operador para o canal errado.

    O SQL dos dois vive no Trackify desde que a leitura virou HTTP; o que
    continua sendo responsabilidade daqui é não embaralhar as duas listas na
    tradução da resposta.
    """
    _mock_compras(
        journey, monkeypatch, [],
        unruled=[{"channel": "hotmart", "eventType": "purchase", "events": 4}],
        unnamed=[{"channel": "pagarme", "eventType": "charge.paid", "events": 2,
                  "fields": "transaction_id"}])
    out = _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))

    assert out["unruled"] == [{"channel": "hotmart", "event_type": "purchase",
                               "events": 4}]
    assert out["unnamed"] == [{"channel": "pagarme", "event_type": "charge.paid",
                               "events": 2, "fields": "transaction_id"}]
    # `fields` é do diagnóstico de "sem identidade" e SÓ dele: é o que diz ao
    # operador qual slug configurar.
    assert "fields" not in out["unruled"][0]


def test_campo_novo_de_settings_tem_interface(journey):
    """O core renderiza a screen `config:true` NO LUGAR do form declarativo, então
    setting sem campo em `config.js` fica sem interface nenhuma — e o PUT é
    destrutivo, então um campo esquecido volta ao default a cada save."""
    settings = _load("settings")
    # A tela é composta: `config.js` + o `FieldSync.js` que ela embute.
    telas = "".join((_SRC / "static" / n).read_text(encoding="utf-8")
                    for n in ("config.js", "FieldSync.js"))
    # Settings que NÃO passam pelo PUT de settings — têm rota própria de propósito:
    por_rota_propria = {
        # Opcional e DEDUZIDA das outras URLs (`_config.api_base`); o valor
        # efetivo aparece read-only no card da API key.
        "sync_api_base",
    }
    for campo in settings.Settings.model_fields:
        if campo in por_rota_propria:
            continue
        assert campo in telas, f"setting '{campo}' não tem campo na tela Configurar"


def test_modal_reusa_o_event_row_no_historico_do_produto():
    """Se alguém escrever um segundo componente de evento, as duas telas
    divergem e campo de gateway novo passa a aparecer só numa delas."""
    js = (_SRC / "static" / "JourneyModal.js").read_text(encoding="utf-8")
    corpo = js[js.index("function PurchaseRow("):js.index("const JOURNEY_TABS")]
    assert "ver histórico" in corpo
    assert "<${EventRow}" in corpo


def test_aba_de_produtos_nao_mostra_diagnostico_do_cdp():
    """O diagnóstico detalhado é configuração do CDP, não informação de
    atendimento — e ocupava mais espaço que a própria lista de compras. O
    backend continua devolvendo `unruled`/`unnamed`; a TELA é que não os
    despeja no rodapé."""
    js = (_SRC / "static" / "JourneyModal.js").read_text(encoding="utf-8")
    for frase in ("não têm regra de valor", "entraram no total gasto",
                  "Campos que identificam o produto", "u.fields"):
        assert frase not in js, f"o diagnóstico voltou para a aba: {frase!r}"
    # ...mas a distinção honesta do estado vazio fica: quem comprou e não pôde
    # ser listado não pode ler "ainda não comprou nada".
    assert "Nenhuma compra pôde ser listada." in js
    assert "Este contato ainda não comprou nada." in js


def test_strip_de_abas_nao_cria_barra_de_rolagem():
    """`overflow-x-auto` força o eixo Y a virar `auto` (regra do CSS), e o
    `-mb-px` dos botões transbordava um pixel — o strip ganhava uma barra de
    rolagem vertical. São duas abas curtas: não há o que rolar."""
    js = (_SRC / "static" / "JourneyModal.js").read_text(encoding="utf-8")
    nav = js[js.index("function JourneyTabs("):js.index("</nav>")]
    assert "overflow" not in nav


def test_dinheiro_de_produto_soma_em_decimal(journey, monkeypatch):
    """Trava a decisão contra quem "simplificar" para ``float``: três parcelas de
    dez centavos têm que dar trinta, não R$ 0,30000000000000004."""
    _mock_compras(journey, monkeypatch, [
        _ev("purchase", 5 + i, {"product_name": "Centavos"}, "0.10") for i in range(3)
    ])
    p = _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))["items"][0]
    assert p["paid_total"] == "R$ 0,30"


def test_canal_sem_regra_de_valor_e_reportado_em_vez_de_sumir_calado(journey, monkeypatch):
    """O medo legítimo da regra antiga vira diagnóstico na tela: compra de canal
    mal configurado não some em silêncio, ela é NOMEADA."""
    _mock_compras(journey, monkeypatch, [], unruled=[
        {"channel": "hotmart", "eventType": "purchase", "events": 4},
    ])
    out = _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))
    assert out["items"] == []
    assert out["unruled"] == [{"channel": "hotmart", "event_type": "purchase",
                               "events": 4}]


def test_produtos_indisponiveis_nao_derrubam_a_jornada(journey, monkeypatch):
    """A rota ``/purchases`` é dependência NOVA: num Trackify antigo ela não
    existe, e o bloco de compras cai sozinho — a aba Jornada continua de pé."""
    async def explode(http, cid, **k):
        raise RuntimeError("a rota /purchases não existe neste Trackify")
    _stub_blocos(monkeypatch, journey, timeline={"events": [1]})
    monkeypatch.setattr(journey, "fetch_purchases", explode)
    out = _run(journey.build_journey(_FakeHttp(), "uuid-1"))
    assert out["purchases"] == {"items": [], "unruled": [], "unnamed": [],
                                "unavailable": True}
    assert out["identity"] == {"contact_id": "uuid-1"}
    assert out["timeline"] == {"events": [1]}


def test_jornada_completa_carrega_as_compras(journey, monkeypatch):
    _stub_blocos(monkeypatch, journey,
                 purchases={"items": [{"name": "X"}], "unruled": []})
    out = _run(journey.build_journey(_FakeHttp(), "uuid-1"))
    assert out["purchases"]["items"] == [{"name": "X"}]
    assert "products" not in out       # o rename protege o JS velho em cache


def test_definicao_de_compra_mora_no_cdp_e_nao_aqui(journey, monkeypatch):
    """A definição de compra tem que vir da regra de valor do CDP, não de uma
    lista de tipos chumbada aqui dentro."""
    # É o `effect` que a rota devolve — não o nome do evento — que cria produto.
    _mock_compras(journey, monkeypatch, [
        _ev("um_nome_qualquer", 5, {"product_name": "Curso"}, 197),
        _ev("purchase", 4, {"product_name": "Outro"}, 99, effect="ignore"),
    ])
    itens = _run(journey.fetch_purchases(_FakeHttp(), "uuid-1"))["items"]
    assert [p["name"] for p in itens] == ["Curso"]


def test_bloco_de_compras_indisponivel_e_reportado_e_nao_500(journey, monkeypatch):
    """O equivalente honesto do antigo ``schema_check``: o plugin não conhece
    mais tabela nenhuma do CDP, então o que resta verificar é que uma rota
    ausente (Trackify mais antigo) vira aviso e não derruba a jornada."""
    tk = _load("client")

    async def _sem_rota(http, contact_id, **k):
        return tk.Result(tk.UNLINKED, 404, "não encontrado no Trackify")
    monkeypatch.setattr(journey.tk_client, "purchases", _sem_rota)

    out = _run(journey._purchases_block(_FakeHttp(), "uuid-1"))
    assert out["unavailable"] is True and out["items"] == []


def test_data_torta_do_cdp_nao_derruba_as_assinaturas(journey, monkeypatch):
    """`next_charge_date` é TEXT em dd/mm/aaaa e `subscription_canceled_at` chega
    valendo a string "system" — valores REAIS de produção."""
    campos = {"product_name": "Combo", "next_charge_date": "25/02/2027",
              "subscription_canceled_at": "system"}
    _mock_eventos(journey, monkeypatch, [{
        "id": "e-10", "eventType": "active_subscription", "title": "Assinatura",
        "value": "97", "occurredAt": datetime.datetime(2026, 1, 10, 12, 0),
        "channel": {"slug": "ticto"},
        "eventFieldValues": [
            {"value": v, "eventCustomField": {"slug": k}} for k, v in campos.items()],
    }])
    s = _run(journey.fetch_subscriptions(_FakeHttp(), "uuid-1",
                                         today=datetime.date(2026, 7, 31)))[0]
    assert s["next_charge"] == "2027-02-25" and s["days_left"] == 209
    assert s["next_charge_raw"] == "25/02/2027"
    assert s["canceled_at"] is None      # "system" NÃO virou data


_SUPORTE_JS = Path(__file__).resolve().parents[1] / "support_js"


def _node(script: str, *args) -> tuple[int, str]:
    import subprocess
    r = subprocess.run(["node", str(_SUPORTE_JS / script), *args],
                       capture_output=True, text=True, timeout=120)
    return r.returncode, (r.stdout + r.stderr).strip()


def test_as_telas_do_plugin_EXECUTAM():
    """`node --check` valida sintaxe e deixou passar DOIS erros que apagaram o
    modal de configuração na cara do operador:

    * um `const` usado no efeito de montagem antes de ser declarado (temporal
      dead zone) — o modal ficou completamente em branco;
    * um componente referenciado sem existir.

    Aqui os módulos são importados de verdade e os componentes são CHAMADOS,
    com preact/htm dublados. Não renderiza a árvore: o objetivo é executar o
    corpo das funções, que é onde esse tipo de erro mora.
    """
    static = _SRC / "static"
    for arquivo, componentes in (("config.js", []),
                                 ("FieldSync.js", ["default", "ApiKeyCard"])):
        code, saida = _node("smoke_plugin_screen.mjs", str(static), arquivo, *componentes)
        assert code == 0, f"{arquivo} não executa:\n{saida}"


def test_nenhum_setter_fantasma_nas_telas():
    """Setter chamado dentro de um `useEffect` não é alcançado pelo smoke (o
    dublê não roda o efeito), mas explode na tela.

    Foi assim que `setNewDsn(null)` sobreviveu à remoção do estado `newDsn` e
    deixou a aba Conexão em branco.
    """
    alvos = [str(p) for p in sorted((_SRC / "static").glob("*.js"))]
    code, saida = _node("lint_phantom_setters.mjs", *alvos)
    assert code == 0, saida


def test_status_nao_reporta_setting_que_nao_existe_mais():
    """Regressão real: `/field-sync/status` continuou devolvendo `logged_in`
    lido de `sync_user_id` — setting que foi REMOVIDA e que a limpeza de boot
    ainda apaga. O campo era eternamente falso, e a tela mostrava para sempre um
    alarme vermelho dizendo que "a conta de serviço nunca autenticou".

    Um campo de status que lê uma chave morta é pior que campo nenhum: ele
    afirma com confiança algo que não pode ser verdade.
    """
    fonte = (_SRC / "routes.py").read_text(encoding="utf-8")
    mortas = ("sync_user_id", "sync_last_login_error", "service_password", "service_email")
    for chave in mortas:
        assert chave not in fonte, f"routes.py ainda lê a setting morta {chave!r}"

    # E as settings citadas na rota de status precisam existir de verdade.
    from importlib import import_module
    cfg = _load("_config")
    bloco = fonte[fonte.index("async def field_sync_status"):]
    bloco = bloco[:bloco.index("@router.post")]
    import re
    for chave in set(re.findall(r'_config\.setting\("(\w+)"', bloco)):
        assert chave in cfg.DEFAULTS, f"status lê '{chave}', que não está em _config.DEFAULTS"


def test_a_tela_so_le_campos_que_a_rota_de_status_devolve():
    """O elo que quebrou: a rota parou de mandar `logged_in` e a tela continuou
    lendo `data.logged_in` — que em JavaScript é `undefined`, ou seja, falso, e
    o alarme ficou aceso para sempre sem ninguém errar visivelmente."""
    import re
    fonte = (_SRC / "routes.py").read_text(encoding="utf-8")
    bloco = fonte[fonte.index("async def field_sync_status"):]
    bloco = bloco[:bloco.index("@router.post")]
    devolvidos = set(re.findall(r'"(\w+)":', bloco))

    js = (_SRC / "static" / "FieldSync.js").read_text(encoding="utf-8")
    corpo = js[js.index("function SyncStatus("):js.index("function Simular(")]
    lidos = set(re.findall(r"\bdata\.(\w+)", corpo))

    faltando = lidos - devolvidos
    assert not faltando, (
        f"a tela lê campos que /field-sync/status não devolve: {sorted(faltando)}")


def test_o_poller_aprende_o_id_da_chave_sozinho(monkeypatch):
    """Antes, o id só era gravado por quem clicasse em "Testar acesso": quem
    colasse a chave e salvasse ficava sem a 1ª camada de supressão de eco, com um
    aviso na tela que nunca sumia."""
    pull_mod = _load("pull")
    tk = _load("client")
    gravado = {}

    async def _whoami(http):
        return tk.Result(tk.OK, 200, data={"id": "k-descoberta", "scopes": ["read"]})
    monkeypatch.setattr(pull_mod.tk_client, "whoami", _whoami)
    monkeypatch.setattr(pull_mod._config, "setting", lambda k, d=None: "")

    import db.repositories.config_repo as cr
    monkeypatch.setattr(cr, "set_many", lambda pares: gravado.update(pares))

    ator = _run(pull_mod._garantir_ator(_FakeHttp()))

    assert ator == "apikey:k-descoberta"
    assert gravado == {"plugin.trackify.sync_api_key_id": "k-descoberta"}


def test_falha_ao_aprender_o_id_nao_para_a_leitura(monkeypatch):
    """Sem o ator a leitura continua válida pela comparação de valor — travar o
    ciclo por causa disso seria trocar um degradado por um parado."""
    pull_mod = _load("pull")
    tk = _load("client")

    async def _fora_do_ar(http):
        return tk.Result(tk.RETRY, 500, "fora do ar")
    monkeypatch.setattr(pull_mod.tk_client, "whoami", _fora_do_ar)
    monkeypatch.setattr(pull_mod._config, "setting", lambda k, d=None: "")

    assert _run(pull_mod._garantir_ator(_FakeHttp())) == ""


def test_farol_da_conexao_nao_fala_de_DSN_nem_de_tabela():
    """O card de saúde tem que descrever o que EXISTE hoje.

    Regressão real: a rota `/health` foi reescrita para reportar credencial e
    escopos, mas os rótulos da tela continuaram dizendo "DSN do Nexus", "Banco
    inalcançável" e "Estrutura das tabelas incompatível" — três coisas que
    deixaram de existir. O operador via tudo vermelho, apontando para uma
    configuração que não está mais em lugar nenhum.
    """
    js = (_SRC / "static" / "config.js").read_text(encoding="utf-8")
    farol = js[js.index("function Health("):js.index("function Health(") + 2200]

    for morto in ("DSN do Nexus", "Banco ${", "Estrutura das tabelas"):
        assert morto not in farol, f"o farol ainda fala de algo que não existe: {morto!r}"

    # E diz o que de fato é verificado agora.
    for vivo in ("API key", "Permissões da chave"):
        assert vivo in farol, f"o farol não reporta {vivo!r}"


def test_a_api_key_fica_na_aba_de_CONEXAO():
    """Ela é a credencial da conexão — vale para ler, escrever e ingerir.

    Nasceu na aba "Campos do contato" porque substituiu a conta de serviço, que
    só servia à sincronização de campos. Deixá-la lá esconde o ÚNICO campo que o
    operador precisa preencher atrás de uma aba sobre outro assunto — e a aba
    Conexão fica sem nada configurável.
    """
    js = (_SRC / "static" / "config.js").read_text(encoding="utf-8")
    aba = js[js.index("tab === 'conexao'"):js.index("tab === 'espelho'")]
    assert "ApiKeyCard" in aba

    # E não pode continuar duplicada na aba de campos.
    fs = (_SRC / "static" / "FieldSync.js").read_text(encoding="utf-8")
    corpo = fs[fs.index("export default function FieldSync("):]
    assert "<${ApiKeyCard}" not in corpo


def test_modal_da_jornada_tem_duas_abas():
    """A aba Produtos existe e usa o mesmo strip do resto do plugin."""
    js = (_SRC / "static" / "JourneyModal.js").read_text(encoding="utf-8")
    assert "'Jornada'" in js and "'Produtos'" in js
    assert "border-b-2" in js and "border-wa-teal" in js


def test_modal_nao_filtra_produto_por_estado():
    """A decisão central da 1.2.0: o selo rotula, nunca decide se a linha
    aparece. Se `p.active` voltar ao arquivo, a regra morta voltou junto."""
    js = (_SRC / "static" / "JourneyModal.js").read_text(encoding="utf-8")
    assert "p.active" not in js


def test_abas_nao_aparecem_sem_jornada():
    """Os seis estados sem jornada (carregando/erro/não configurado/grupo/
    ambíguo/não encontrado) renderizam como antes: `tabStrip` fica `null` e o
    `htm` não desenha nada. Uma única atribuição prova que só o ramo terminal
    da jornada completa preenche a variável."""
    js = (_SRC / "static" / "JourneyModal.js").read_text(encoding="utf-8")
    assert "let tabStrip = null;" in js
    assert js.count("tabStrip = html`") == 1


def test_modal_da_jornada_nao_deixa_valor_estourar_o_painel():
    """Regressão de layout: item de grade nasce com `min-width:auto`, então um
    hash ou um blob de JSON alarga a coluna e empurra o painel para fora da tela.
    O conserto é `min-w-0` + quebra de palavra, e some sem ninguém notar."""
    js = (_SRC / "static" / "JourneyModal.js").read_text(encoding="utf-8")
    stat = js[js.index("function Stat("):js.index("function Stat(") + 500]
    assert "min-w-0" in stat and "break-words" in stat
    # Valor longo/JSON ocupa a linha inteira em vez de espremer a coluna.
    assert "function DetailField(" in js and "sm:col-span-2" in js
    assert "max-w-5xl" in js
    # htm não entende comentário HTML: um `<!--` no template viraria texto na tela.
    assert "<!--" not in js


def test_erro_de_credencial_distingue_chave_de_escopo(plugin_app):
    """Mandar o operador gerar outra chave quando o problema é permissão faz ele
    trocar a chave e continuar quebrado — são conselhos opostos.

    Substitui o antigo teste de mensagens de LOGIN: não há mais senha para
    conferir nem sessão para criar.
    """
    built = plugin_app("trackify")
    rotas = _load("routes")

    m401 = rotas._mensagem_de_erro(401)
    m403 = rotas._mensagem_de_erro(403)
    m404 = rotas._mensagem_de_erro(404)

    assert "revogada" in m401 and "401" in m401
    assert "escopo" in m403.lower() and "403" in m403
    # Um Trackify anterior ao sistema de chaves responde 404 — e isso não é
    # "chave errada", é "atualize o outro lado".
    assert "atualize o Trackify" in m404


def test_falha_de_autenticacao_aparece_na_primeira_tentativa(plugin_app, monkeypatch):
    """Antes, o erro só ficava visível depois de 3 falhas seguidas — e o contador
    zerava a cada restart do servidor, então na prática NUNCA aparecia: a fila
    acumulava "sem sessão" sem nada na tela explicando o porquê.

    Agora a fila marca a linha e desliga a sincronização com motivo já na
    PRIMEIRA recusa de credencial, porque uma chave inválida não melhora com
    retentativa.
    """
    plugin_app("trackify")
    push = _load("push")
    tk = _load("client")

    push._block_sync(tk.Result(tk.UNAUTHORIZED, 401,
                               "API key inválida, revogada ou expirada.").error)

    from db.repositories import config_repo
    motivo = config_repo.get("plugin.trackify.sync_blocked_reason", "")
    assert motivo and "revogada" in motivo
    assert config_repo.get("plugin.trackify.sync_last_auth_error", "") == motivo

    config_repo.set_many({"plugin.trackify.sync_blocked_reason": "",
                          "plugin.trackify.sync_last_auth_error": ""})


@pytest.mark.parametrize("status", [200, 201, 204])
def test_leitura_aceita_qualquer_2xx(cliente, status):
    """O NestJS escolhe o status pelo VERBO (POST→201) e o ``@ApiResponse`` do
    controller documenta outro — exigir 200 exato já fez uma resposta
    BEM-SUCEDIDA ser lida como recusa."""
    http = _FakeClient(_FakeResp(status, {"id": "uuid-1"}))
    res = _run(cliente.get_contact(http, "uuid-1"))
    assert res.ok and res.http_status == status


def test_escrita_no_contato_aceita_qualquer_2xx(writer, _api_base):
    client = _FakeClient(_FakeResp(201, _cdp_contact(email="a@b.com")))
    res = _run(writer.put_contact(client, "uuid-1", {"email": "a@b.com"}))
    assert res.verdict == writer.OK


def test_edicao_humana_no_trackify_e_aplicada_e_a_nossa_e_ignorada(
        plugin_app, monkeypatch):
    """Regressão do caso real, agora resolvido na raiz.

    Antes, o operador podia estar logado na tela do Trackify com a MESMA conta
    configurada na integração, e toda edição dele carregava o ``user_id`` da
    conta de serviço. O filtro por autor descartava tudo em silêncio — a Jornada
    (que lê o Trackify ao vivo) mostrava o valor novo e o painel do WhatsBot
    ficava no antigo, sem erro em lugar nenhum.

    Com API key a ambiguidade DEIXA DE EXISTIR: a nossa escrita é assinada por
    ``apikey:<id>`` e a de uma pessoa nunca é. Não há conta compartilhada
    possível, e o desempate não depende mais de comparar valores.
    """
    built = plugin_app("trackify", settings_overrides=_field_sync_on(**{
        "plugin.trackify.field_sync_pull_enabled": True,
        "plugin.trackify.sync_api_key_id": "k1",
    }))
    pull_mod = _load("pull")
    _mapeia(built.client, "email", "email", direction="both")

    from db.repositories import contact_repo, custom_attribute_repo
    from db.tables import contacts as contacts_tbl
    c = contact_repo.get_or_create("5511900000030")
    cid = int(c["id"])
    custom_attribute_repo.set_values(contacts_tbl, cid, {"email": "antigo@x.com"})
    _liga_contato_ao_cdp("5511900000030", "uuid-cdp-30")

    from sqlalchemy import text as _t

    from db.engine import get_engine
    with get_engine().begin() as conn:
        conn.execute(_t("UPDATE plugin_trackify_field_map SET tk_field_id = 'f-mail'"))
        mid = conn.execute(_t("SELECT id FROM plugin_trackify_field_map")).scalar()

    # Memória do que NÓS enviamos por último: "antigo@x.com".
    sync_state = _load("sync_state")
    codec = _load("field_codec")
    sync_state.record(mid, cid, wb_hash=codec.hash_value("antigo@x.com"),
                      tk_hash=codec.hash_value("antigo@x.com"),
                      trackify_contact_id="uuid-cdp-30")

    monkeypatch.setattr(pull_mod.tk_client, "is_configured", lambda: True)
    _pagina(monkeypatch, pull_mod, [
        # 1) o nosso próprio envio voltando: ator `apikey:k1` -> eco, na hora.
        {"row_id": "r1", "tk_contact_id": "uuid-cdp-30", "field_id": "f-mail",
         "slug": "email", "new_value": "antigo@x.com", "source": "api",
         "user_id": "apikey:k1", "created_epoch": 1_999_998_000.0},
        # 2) o HUMANO editando na tela do Trackify: outro ator -> aplica.
        {"row_id": "r2", "tk_contact_id": "uuid-cdp-30", "field_id": "f-mail",
         "slug": "email", "new_value": "novo@x.com", "source": "manual",
         "user_id": "usuario-humano-7", "created_epoch": 1_999_999_000.0},
    ])

    resumo = _run(pull_mod.cycle(_FakeHttp()))
    assert resumo["ecos"] == 1                      # o nosso envio foi ignorado
    assert resumo["gravadas"] == 1                  # a edição do humano entrou
    assert custom_attribute_repo.get_values(contacts_tbl, cid)["email"] == "novo@x.com"


def test_escrita_de_OUTRA_chave_nao_e_tratada_como_eco(plugin_app, monkeypatch):
    """Duas integrações podem escrever no mesmo CDP. A de outra chave é mudança
    de verdade — descartá-la seria o mesmo bug de antes, com outro disfarce."""
    pull_mod = _load("pull")
    codec = _load("field_codec")
    nosso = {"tk_hash": codec.hash_value("nosso@x.com")}

    assert pull_mod._e_nosso_eco(
        {"user_id": "apikey:OUTRA", "new_value": "dela@x.com"},
        nosso, "apikey:k1") is False


# ── Busca automática por telefone + campos conectados ────────────────────

def test_candidatos_conhecem_as_normalizacoes_de_cada_identificador():
    """Cada identificador circula de um jeito: telefone tem variante brasileira,
    CPF vai com e sem máscara, e-mail é insensível a caixa.

    Gerar as grafias é o que SOBROU deste módulo depois que a consulta virou
    ``POST /contacts/resolve`` — o servidor compara byte a byte, então a
    qualidade do casamento depende inteiramente do que sai daqui. Por isso a
    função é pura e o teste não toca em rede.
    """
    identity = _load("identity")

    assert "5564996162906" in identity.candidates_for("whatsapp", "556496162906")
    cpf = identity.candidates_for("cpf", "056.224.381-01")
    assert "05622438101" in cpf and "056.224.381-01" in cpf
    assert "joao@empresa.com" in identity.candidates_for("email", " Joao@Empresa.COM ")
    # Identificador criado pelo cliente: comparação exata, sem regra especial.
    assert identity.candidates_for("matricula", "A-1234") == ["A-1234"]
    # Lixo não vira candidato.
    assert identity.candidates_for("email", "sem-arroba") == []
    assert identity.candidates_for("cpf", "123") == []


def test_busca_usa_telefone_e_os_campos_conectados(monkeypatch):
    """O ponto da mudança: conectar um campo passa a valer para ENCONTRAR o
    cadastro, não só para copiar o valor depois."""
    identity = _load("identity")
    tk = _load("client")
    enviados = {}

    async def fake(http, identifiers, **k):
        enviados.update(identifiers)
        return tk.Result(tk.OK, 200, data={"matches": [
            {"contactId": "uuid-x", "slug": "cpf", "value": "05622438101",
             "matchedBy": "variant"}]})
    monkeypatch.setattr(identity.tk_client, "resolve", fake)

    achou = _run(identity.resolve_mapped(
        _FakeHttp(), phone="5564996162906",
        extras={"email": "x@y.com", "cpf": "05622438101"}))

    assert [m.slug for m in achou] == ["cpf"]
    # TODOS os identificadores vão numa chamada só: com SQL local cada tentativa
    # custava 0,085 ms, mas agora cada uma seria uma ida à rede. Quem ordena por
    # prioridade é o servidor — manter uma segunda cópia da regra aqui faria a
    # leitura casar num contato e a escrita da ingestão em outro.
    assert set(enviados) == {"email", "whatsapp", "cpf"}


def test_telefone_entra_na_busca_mesmo_sem_estar_mapeado(monkeypatch):
    identity = _load("identity")
    tk = _load("client")
    enviados = {}

    async def fake(http, identifiers, **k):
        enviados.update(identifiers)
        return tk.Result(tk.OK, 200, data={"matches": []})
    monkeypatch.setattr(identity.tk_client, "resolve", fake)

    _run(identity.resolve_mapped(_FakeHttp(), phone="5564996162906", extras={}))
    assert set(enviados) == {"whatsapp"}
    assert "5564996162906" in enviados["whatsapp"]


def test_fallback_por_digitos_so_roda_depois_do_exato_vazio(monkeypatch):
    """É caminho FRIO dos dois lados: a consulta tolerante a máscara não usa o
    índice de identificador (medido: 6 de 10.910 valores de `whatsapp` têm
    caractere fora de [0-9+])."""
    identity = _load("identity")
    tk = _load("client")
    chamadas = []

    async def fake(http, identifiers, *, limit=10, digits_fallback=False):
        chamadas.append(digits_fallback)
        if digits_fallback:
            return tk.Result(tk.OK, 200, data={"matches": [
                {"contactId": "uuid-mascarado", "slug": "cpf", "value": "056.224.381-01",
                 "matchedBy": "digits"}]})
        return tk.Result(tk.OK, 200, data={"matches": []})
    monkeypatch.setattr(identity.tk_client, "resolve", fake)

    achou = _run(identity.resolve_by_cpf(_FakeHttp(), "05622438101"))

    assert chamadas == [False, True]
    assert achou[0].matched_by == "digits"


def test_pistas_saem_dos_mapeamentos_identificadores(plugin_app):
    """E leem o ATRIBUTO personalizado, não a coluna legada — que está vazia
    desde a migração 0028 do core e era por onde o código antigo procurava."""
    built = plugin_app("trackify")
    field_map = _load("field_map")
    built.client.put("/api/plugins/trackify/mappings", json={"rows": [
        {"wb_scope": "attribute", "wb_key": "cpf", "tk_slug": "cpf",
         "direction": "to_whatsbot"},
    ]})
    from sqlalchemy import text as _t

    from db.engine import get_engine
    with get_engine().begin() as conn:
        conn.execute(_t("UPDATE plugin_trackify_field_map SET tk_is_identifier = 1"))

    contato = {"phone": "5511911111111", "email": "",       # coluna legada vazia
               "custom_attributes": {"cpf": "05622438101"}}
    assert field_map.identifier_hints(contato) == {"cpf": "05622438101"}

    # Campo não-identificador no CDP não vira pista de busca.
    with get_engine().begin() as conn:
        conn.execute(_t("UPDATE plugin_trackify_field_map SET tk_is_identifier = 0"))
    assert field_map.identifier_hints(contato) == {}


def test_sincronizacao_vincula_o_contato_sob_demanda(plugin_app, monkeypatch):
    """Antes, o escopo dependia de o espelho de eventos já ter passado por aquele
    contato: conectar o CPF na tela não fazia diferença nenhuma até ele gerar um
    evento."""
    built = plugin_app("trackify", settings_overrides=_field_sync_on())
    push = _load("push")
    identity = _load("identity")
    _mapeia(built.client, "cpf", "cpf")

    from db.repositories import contact_repo, custom_attribute_repo
    from db.tables import contacts as contacts_tbl
    c = contact_repo.get_or_create("5511922222222")
    custom_attribute_repo.set_values(contacts_tbl, int(c["id"]), {"cpf": "05622438101"})

    from sqlalchemy import text as _t

    from db.engine import get_engine
    with get_engine().begin() as conn:
        conn.execute(_t("UPDATE plugin_trackify_field_map SET tk_is_identifier = 1"))
        conn.execute(_t("DELETE FROM plugin_trackify_identity WHERE phone = :p"),
                     {"p": "5511922222222"})

    async def _achou(http, **k):
        return [identity.Match("uuid-novo", "cpf", "05622438101", "variant")]
    monkeypatch.setattr(identity, "resolve_mapped", _achou)
    _cdp_tem(monkeypatch, push)

    plano = _run(push.plan_for_contact(_FakeHttp(), int(c["id"])))
    assert plano.trackify_contact_id == "uuid-novo"
    with get_engine().connect() as conn:
        gravado = conn.execute(_t("SELECT trackify_contact_id, matched_slug "
                                  "FROM plugin_trackify_identity WHERE phone = :p"),
                               {"p": "5511922222222"}).mappings().first()
    assert dict(gravado) == {"trackify_contact_id": "uuid-novo", "matched_slug": "cpf"}


def test_vinculo_ambiguo_nunca_e_escolhido_em_silencio(plugin_app, monkeypatch):
    """Dois cadastros casando é ambiguidade real (medido). Escolher sozinho
    colaria os dados de uma pessoa no cadastro de outra."""
    built = plugin_app("trackify", settings_overrides=_field_sync_on())
    push = _load("push")
    identity = _load("identity")
    _mapeia(built.client, "cpf", "cpf")

    from db.repositories import contact_repo
    c = contact_repo.get_or_create("5511933333333")
    async def _ambiguo(http, **k):
        return [identity.Match("uuid-a", "cpf", "1", "variant"),
                identity.Match("uuid-b", "cpf", "1", "variant")]
    monkeypatch.setattr(identity, "resolve_mapped", _ambiguo)

    plano = _run(push.plan_for_contact(_FakeHttp(), int(c["id"])))
    assert "não vinculado" in plano.skip


def test_candidatos_incluem_a_forma_nacional_sem_o_codigo_do_pais(phone):
    """O CDP recebe número de formulário e de planilha, onde "6492973092" (DDD +
    8 dígitos, sem o 55) é comum. ``br_phone_variants`` do core só alterna o 9º
    dígito e mantém o 55 sempre, então esse cadastro nunca casava."""
    got = phone.lookup_candidates("5564992973092")
    assert "6492973092" in got        # sem o 55 E sem o 9 — o caso relatado
    assert "64992973092" in got       # sem o 55, com o 9
    assert "5564992973092" in got and "+5564992973092" in got   # não regrediu


def test_forma_nacional_nunca_leva_o_mais(phone):
    """`+6492973092` é um número VÁLIDO da Nova Zelândia (+64). Casar com ele
    grudaria os dados do cliente no cadastro de outra pessoa."""
    for cand in phone.lookup_candidates("5564992973092"):
        if cand.startswith("+"):
            assert cand.startswith("+55"), cand


def test_numero_estrangeiro_nao_ganha_forma_nacional(phone):
    """Só número com prefixo 55 vira forma nacional: cortar os dois primeiros
    dígitos de um número de outro país produziria lixo que pode casar com um
    telefone brasileiro real."""
    assert phone.lookup_candidates("12025550123") == ["12025550123", "+12025550123"]


def test_gravacao_continua_em_e164_completo(phone):
    """A forma nacional vale para BUSCAR. Para CRIAR o contato no CDP, sobregerar
    é erro — e um número sem código de país forkaria o cadastro."""
    assert phone.canonical_e164("5564992973092") == "+5564992973092"
    assert phone.canonical_e164("556432168000") == "+556432168000"
