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

_SRC = Path(__file__).resolve().parents[1] / "assets" / "plugin_examples" / "trackify"
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


def test_assinatura_derivada_de_linha_real(journey, monkeypatch):
    """Payload copiado de um evento REAL de produção, com as duas armadilhas."""
    linha = {
        "id": "48c7304f", "event_type": "active_subscription",
        "title": "Assinatura ativa", "value": Decimal("97.00"),
        "occurred_at": datetime.datetime(2026, 4, 29, 13, 30, 16),
        "fields": {
            "status": "Pagamento Autorizado",
            "product_name": "Combo de Redes",
            "offer_name": "Combo de Redes (Multivendor)",
            "payment_method": "Pix", "successful_charges": "1", "failed_charges": "0",
            "next_charge_date": "25/02/2027",
            "subscription_canceled_at": "system",
        },
    }
    monkeypatch.setattr(journey.trackify_db, "run_read", lambda *a, **k: [linha])
    subs = journey.fetch_subscriptions("x", today=datetime.date(2026, 7, 31))

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

    for mod_name in ("journey", "identity", "dispatcher", "mirror"):
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


def test_sem_dsn_tudo_degrada_para_vazio(journey, monkeypatch):
    """Sem configuração o plugin é no-op logado — nunca levanta, nunca 500."""
    monkeypatch.setattr(journey.trackify_db, "is_configured", lambda: False)
    out = journey.journey_for(phone="5564996162906")
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
    body = dispatcher.build_body(row)
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
    assert "não configurado" in d["message"].lower()


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


def test_mapeamento_recusa_escrita_sem_conta_de_servico(plugin_app):
    """Sem credencial não há como gravar no Trackify — e dizer isso na linha é o
    que evita o operador achar que salvou e nada acontecer."""
    built = plugin_app("trackify")
    r = built.client.put("/api/plugins/trackify/mappings", json={"rows": [
        {"wb_scope": "attribute", "wb_key": "cpf", "tk_slug": "cpf",
         "direction": "to_trackify"},
    ]})
    assert r.status_code == 400
    erros = r.json()["data"]["row_errors"]["0"]
    assert any("conta de serviço" in e for e in erros)


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


def test_senha_da_conta_de_servico_nunca_volta_em_claro(plugin_app):
    built = plugin_app("trackify")
    r = built.client.put("/api/plugins/trackify/service-account",
                         json={"email": "bot@empresa.com", "password": "s3nh4-secreta"})
    assert r.status_code == 200

    r = built.client.get("/api/plugins/trackify/service-account")
    d = r.json()["data"]
    assert d["email"] == "bot@empresa.com"
    assert d["password_masked"] == "***"
    assert "s3nh4-secreta" not in r.text

    # O sentinela preserva a senha em vez de apagá-la.
    built.client.put("/api/plugins/trackify/service-account",
                     json={"email": "bot2@empresa.com", "password": "***"})
    from db.repositories import config_repo
    assert config_repo.get("plugin.trackify.service_password") == "s3nh4-secreta"
    assert config_repo.get("plugin.trackify.service_email") == "bot2@empresa.com"


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
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    async def put(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self._resp

    async def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json})
        return self._resp


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
    w = _load("writer")
    monkeypatch.setattr(w._config, "api_base", lambda: "https://nexus.example/trackify/api/v1")


def test_corpo_do_put_so_tem_fieldValues(writer, _api_base):
    """O ValidationPipe do Trackify é ``forbidNonWhitelisted``: qualquer chave
    a mais derruba a requisição inteira com 400."""
    sess = _load("session").Session(cookie="trackify_session=tok", user_id="7",
                                    obtained_at=0.0)
    client = _FakeClient(_FakeResp(200, _cdp_contact(email="a@b.com")))
    res = _run(writer.put_contact(client, sess, "uuid-1", {"email": "a@b.com"}))

    assert res.verdict == writer.OK
    assert set(client.calls[0]["json"]) == {"fieldValues"}
    assert client.calls[0]["headers"]["Cookie"] == "trackify_session=tok"


def test_slug_ignorado_pelo_cdp_vira_erro_e_nao_sucesso(writer, _api_base):
    """Slug desativado/inexistente é pulado EM SILÊNCIO pelo Trackify e o 200
    vem igual. Sem conferir a resposta, contaríamos sucesso numa escrita que
    nunca aconteceu — a falha mais perigosa desta feature."""
    sess = _load("session").Session("trackify_session=t", "7", 0.0)
    client = _FakeClient(_FakeResp(200, _cdp_contact(email="antigo@b.com")))
    res = _run(writer.put_contact(client, sess, "uuid-1", {"email": "novo@b.com"}))

    assert res.verdict == writer.BLOCKED
    assert res.dropped == ["email"]
    assert "ignorou" in res.error


def test_limpeza_e_confirmada_pela_ausencia_da_linha(writer, _api_base):
    """Apagar no Trackify DELETA a linha EAV: sucesso é o campo sumir."""
    sess = _load("session").Session("trackify_session=t", "7", 0.0)
    client = _FakeClient(_FakeResp(200, _cdp_contact(cpf="111")))
    res = _run(writer.put_contact(client, sess, "uuid-1", {"email": ""}))
    assert res.verdict == writer.OK and res.landed == {"email": ""}

    # A linha continuar lá significa que a limpeza NÃO aconteceu.
    client = _FakeClient(_FakeResp(200, _cdp_contact(email="ainda@aqui.com")))
    res = _run(writer.put_contact(client, sess, "uuid-1", {"email": ""}))
    assert res.verdict == writer.BLOCKED and res.dropped == ["email"]


@pytest.mark.parametrize("status,verdict", [
    (409, "conflict"),      # outro cadastro é dono do identificador
    (401, "unauthorized"),
    (404, "unlinked"),
    (403, "unlinked"),
    (400, "blocked"),
    (422, "blocked"),
    (429, "throttled"),
    (500, "retry"),
])
def test_taxonomia_de_status_do_put(writer, _api_base, status, verdict):
    sess = _load("session").Session("trackify_session=t", "7", 0.0)
    client = _FakeClient(_FakeResp(status, {"message": "erro"}))
    res = _run(writer.put_contact(client, sess, "uuid-1", {"email": "a@b.com"}))
    assert res.verdict == verdict


def test_409_carrega_motivo_legivel(writer, _api_base):
    """É a falha ESPERADA desta feature (dois cadastros com o mesmo e-mail), não
    a excepcional — precisa chegar legível na tela."""
    sess = _load("session").Session("trackify_session=t", "7", 0.0)
    client = _FakeClient(_FakeResp(409, {"message": "Email já cadastrado"}))
    res = _run(writer.put_contact(client, sess, "uuid-1", {"email": "a@b.com"}))
    assert res.verdict == writer.CONFLICT and "Email já cadastrado" in res.error


# ── Puros: sessão da conta de serviço ────────────────────────────────────

@pytest.fixture()
def sessao(monkeypatch):
    s = _load("session")
    s.reset_for_tests()
    monkeypatch.setattr(s._config, "api_base", lambda: "https://nexus.example/trackify/api/v1")
    return s


def test_cookie_e_lido_do_cabecalho(sessao):
    """O corpo do login devolve só o usuário — o token só existe no cabeçalho."""
    class _H(dict):
        def get_list(self, k):
            return ["outra=x; Path=/",
                    "trackify_session=abc123; Path=/; HttpOnly; SameSite=Lax"]
    resp = _FakeResp(200, {"user": {"id": "7"}}, headers=_H())
    assert sessao.cookie_from(resp) == "abc123"
    assert sessao.user_id_from(resp) == "7"


def test_login_sem_user_id_e_recusado(sessao, monkeypatch):
    """Sem o id não há supressão de eco possível: sincronizar assim faria o
    poller reimportar as próprias escritas como se fossem edições humanas."""
    class _H(dict):
        def get_list(self, k):
            return ["trackify_session=abc; Path=/"]
    monkeypatch.setattr(sessao._config, "setting", lambda k, d=None: {
        "service_email": "bot@x.com", "service_password": "s3nha"}.get(k, d))
    client = _FakeClient(_FakeResp(200, {"user": {}}, headers=_H()))
    assert _run(sessao._login(client)) is None


def test_senha_nunca_aparece_em_log(sessao, monkeypatch, caplog):
    """Nem no log, nem no corpo do erro — a rota de login do Nexus ecoa o que
    foi enviado, então o corpo dela nunca pode ser registrado."""
    monkeypatch.setattr(sessao._config, "setting", lambda k, d=None: {
        "service_email": "bot@x.com", "service_password": "s3nh4-secreta"}.get(k, d))
    client = _FakeClient(_FakeResp(401, {"message": "senha s3nh4-secreta inválida"}))
    with caplog.at_level("WARNING"):
        assert _run(sessao._login(client)) is None
    assert "s3nh4-secreta" not in caplog.text


def test_sem_credencial_nao_ha_tentativa_de_login(sessao, monkeypatch):
    """A rota de login é limitada a 5/min: uma instalação sem conta de serviço
    não pode bater nela a cada ciclo do worker."""
    monkeypatch.setattr(sessao, "is_configured", lambda: False)
    client = _FakeClient(_FakeResp(200, {}))
    assert _run(sessao.get(client)) is None
    assert client.calls == []


# ── Com app: saída de campos (push) ──────────────────────────────────────

def _field_sync_on(**extra):
    base = {
        "plugin.trackify.field_sync_enabled": True,
        "plugin.trackify.field_sync_dry_run": True,
        "plugin.trackify.service_email": "bot@empresa.com",
        "plugin.trackify.service_password": "s3nha",
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


def test_push_ignora_contato_nao_vinculado(plugin_app, monkeypatch):
    """Esta feature NUNCA cria contato no Trackify — quem faz isso é o espelho
    de eventos, com o toggle e o limite dele."""
    built = plugin_app("trackify", settings_overrides=_field_sync_on())
    push = _load("push")
    _mapeia(built.client, "cpf", "cpf")

    from db.repositories import contact_repo
    c = contact_repo.get_or_create("5511900000001")
    plano = push.plan_for_contact(int(c["id"]))
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
    monkeypatch.setattr(push, "trackify_db_run", lambda *a, **k: [])
    plano = push.plan_for_contact(int(c["id"]))
    assert plano.field_values == {"cpf": "11122233344"}
    assert plano.trackify_contact_id == "uuid-cdp-2"

    # O CDP já tem o MESMO valor → nada a enviar, nem uma chamada.
    monkeypatch.setattr(push, "trackify_db_run",
                        lambda *a, **k: [{"slug": "cpf", "value": "11122233344"}])
    plano = push.plan_for_contact(int(c["id"]))
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
    monkeypatch.setattr(push, "trackify_db_run", lambda *a, **k: [])

    linha = _enfileira_e_reserva(push, int(c["id"]))
    client = _FakeClient(_FakeResp(500, {}))     # qualquer chamada seria erro
    assert _run(push.deliver_one(client, linha)) == "dry_run"
    assert client.calls == []                    # NADA foi à rede


def test_push_grava_e_carimba_quando_o_modo_seco_sai(plugin_app, monkeypatch):
    built = plugin_app("trackify", settings_overrides=_field_sync_on(
        **{"plugin.trackify.field_sync_dry_run": False}))
    push = _load("push")
    sess_mod = _load("session")
    sess_mod.reset_for_tests()
    linha_map = _mapeia(built.client, "cpf", "cpf")

    from db.repositories import contact_repo, custom_attribute_repo
    from db.tables import contacts as contacts_tbl
    c = contact_repo.get_or_create("5511900000004")
    custom_attribute_repo.set_values(contacts_tbl, int(c["id"]), {"cpf": "12345"})
    _liga_contato_ao_cdp("5511900000004", "uuid-cdp-4")
    monkeypatch.setattr(push, "trackify_db_run", lambda *a, **k: [])

    fake_sess = sess_mod.Session("trackify_session=t", "user-9", 1e12)
    async def _sessao(client, force=False):
        return fake_sess
    monkeypatch.setattr(push.session, "get", _sessao)

    linha = _enfileira_e_reserva(push, int(c["id"]))
    client = _FakeClient(_FakeResp(200, _cdp_contact(cpf="12345")))
    assert _run(push.deliver_one(client, linha)) == "sent"
    assert client.calls[0]["json"] == {"fieldValues": {"cpf": "12345"}}

    # Carimbado: o próximo ciclo não reenvia.
    plano = push.plan_for_contact(int(c["id"]))
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
    sess_mod = _load("session")
    sess_mod.reset_for_tests()
    linha_map = _mapeia(built.client, "email", "email")

    from db.repositories import contact_repo, custom_attribute_repo
    from db.tables import contacts as contacts_tbl
    c = contact_repo.get_or_create("5511900000005")
    custom_attribute_repo.set_values(contacts_tbl, int(c["id"]),
                                     {"email": "dup@empresa.com"})
    _liga_contato_ao_cdp("5511900000005", "uuid-cdp-5")
    monkeypatch.setattr(push, "trackify_db_run", lambda *a, **k: [])

    async def _sessao(client, force=False):
        return sess_mod.Session("trackify_session=t", "user-9", 1e12)
    monkeypatch.setattr(push.session, "get", _sessao)

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


# ── Puros: cursor fatiado do poller ──────────────────────────────────────

@pytest.fixture(scope="module")
def pull():
    return _load("pull")


def _linha(ts, rid):
    return {"created_epoch": ts, "row_id": rid}


def test_cursor_nao_pula_pedaco_truncado(pull):
    """O cursor é GLOBAL mas a consulta é fatiada por contato.

    Se o pedaço A voltou até T=100 e o pedaço B bateu no LIMIT em T=40, avançar
    para 100 faria tudo entre 40 e 100 do pedaço B nunca mais ser olhado — perda
    silenciosa de alteração feita no CRM do cliente.
    """
    chunks = [
        {"rows": [_linha(10, "a"), _linha(100, "b")], "truncated": False},
        {"rows": [_linha(20, "c"), _linha(40, "d")], "truncated": True},
    ]
    linhas, cursor, truncado = pull.merge_chunks(chunks, (0.0, ""))
    assert cursor == (40.0, "d") and truncado is True
    # A linha de T=100 fica para o próximo ciclo, em vez de ser pulada.
    assert [r["row_id"] for r in linhas] == ["a", "c", "d"]


def test_cursor_avanca_ate_o_fim_quando_nada_truncou(pull):
    chunks = [
        {"rows": [_linha(10, "a"), _linha(100, "b")], "truncated": False},
        {"rows": [_linha(20, "c")], "truncated": False},
    ]
    linhas, cursor, truncado = pull.merge_chunks(chunks, (0.0, ""))
    assert cursor == (100.0, "b") and truncado is False
    assert [r["row_id"] for r in linhas] == ["a", "c", "b"]   # ordenado por tempo


def test_cursor_nao_anda_sem_linha_nenhuma(pull):
    linhas, cursor, truncado = pull.merge_chunks(
        [{"rows": [], "truncated": False}], (55.0, "z"))
    assert (linhas, cursor, truncado) == ([], (55.0, "z"), False)


def test_changelog_nao_descarta_linha_por_autor(pull):
    """O SQL NÃO pode filtrar por autor.

    Filtrar assim engolia a edição de uma pessoa que usasse a mesma conta da
    integração para entrar no Trackify — cenário normal, e a perda era silenciosa.
    A supressão de eco passou a ser por VALOR (ver ``_e_nosso_eco``).
    """
    sql = pull._CHANGELOG_SQL
    assert "user_id" not in sql.split("WHERE")[1]
    # ...mas continua escopada: `created_at` sozinho não tem índice na tabela deles.
    assert "cl.contact_id = ANY(CAST(:ids AS uuid[]))" in sql


def test_eco_e_reconhecido_pelo_valor_e_nao_pelo_autor(pull):
    codec = _load("field_codec")
    nosso = {"tk_hash": codec.hash_value("a@b.com")}

    # A nossa própria escrita voltando: mesmo autor, mesmo valor.
    assert pull._e_nosso_eco(
        {"user_id": "svc-1", "new_value": "a@b.com"}, nosso, "svc-1") is True

    # Uma PESSOA usando a mesma conta: mesmo autor, valor DIFERENTE.
    assert pull._e_nosso_eco(
        {"user_id": "svc-1", "new_value": "outro@b.com"}, nosso, "svc-1") is False

    # Outro usuário: nunca é eco, mesmo que o valor coincida.
    assert pull._e_nosso_eco(
        {"user_id": "humano-2", "new_value": "a@b.com"}, nosso, "svc-1") is False

    # Sem memória do que enviamos, não dá para afirmar que é eco.
    assert pull._e_nosso_eco(
        {"user_id": "svc-1", "new_value": "a@b.com"}, {}, "svc-1") is False


def test_eco_nao_ressuscita_valor_antigo(pull):
    """Por que a guarda compara com o que NÓS enviamos e não com o valor atual
    do WhatsBot: entre o nosso envio e a leitura o operador pode ter mudado o
    campo de novo, e a linha antiga sobrescreveria a mudança nova."""
    codec = _load("field_codec")
    estado = {"tk_hash": codec.hash_value("enviado@x.com")}
    linha_antiga = {"user_id": "svc-1", "new_value": "enviado@x.com"}
    assert pull._e_nosso_eco(linha_antiga, estado, "svc-1") is True


# ── Com app: volta do Trackify ───────────────────────────────────────────

def test_pull_grava_avisa_o_painel_e_nao_emite_contact_updated(plugin_app, monkeypatch):
    """Contrato anti-laço: o refresh é o broadcast de WebSocket, NUNCA o evento
    de barramento ``contact.updated`` — forjá-lo dispararia o nosso próprio
    espelho, que mandaria ao CDP um evento sobre uma mudança que VEIO do CDP.
    """
    built = plugin_app("trackify", settings_overrides=_field_sync_on(**{
        "plugin.trackify.field_sync_pull_enabled": True,
        "plugin.trackify.sync_user_id": "svc-1",
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

    # Sem DSN o ciclo sai antes de qualquer leitura (no-op por desenho).
    monkeypatch.setattr(pull_mod.trackify_db, "is_configured", lambda: True)
    monkeypatch.setattr(pull_mod, "cdp_now", lambda: 2_000_000_000.0)
    monkeypatch.setattr(pull_mod, "fetch_changes", lambda *a, **k: [{
        "rows": [{"row_id": "r1", "tk_contact_id": "uuid-cdp-10",
                  "field_id": "f-cpf", "slug": "cpf", "new_value": "55566677788",
                  "source": "manual", "user_id": "humano-2",
                  "created_epoch": 1_999_999_000.0}],
        "truncated": False}])

    emitidos, avisos = [], []
    import plugins.context as pctx
    monkeypatch.setattr(pctx, "broadcast",
                        lambda ev, payload: avisos.append((ev, payload)))
    from plugins import events as bus
    monkeypatch.setattr(bus, "emit", lambda ev, payload=None: emitidos.append(ev))

    resumo = pull_mod.cycle()
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
        "plugin.trackify.sync_user_id": "",
    }))
    pull_mod = _load("pull")
    _mapeia(built.client, "cpf", "cpf", direction="to_whatsbot")

    chamou = []
    monkeypatch.setattr(pull_mod, "fetch_changes",
                        lambda *a, **k: chamou.append(1) or [])
    resumo = pull_mod.cycle()
    assert resumo["lidas"] == 0 and chamou == []


def test_pull_recusa_valor_invalido_sem_gravar_e_marca_para_nao_repetir(
        plugin_app, monkeypatch):
    """Valor que a validação do WhatsBot rejeita não pode ser gravado nem
    re-tentado para sempre."""
    built = plugin_app("trackify", settings_overrides=_field_sync_on(**{
        "plugin.trackify.field_sync_pull_enabled": True,
        "plugin.trackify.sync_user_id": "svc-1",
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

    # Sem DSN o ciclo sai antes de qualquer leitura (no-op por desenho).
    monkeypatch.setattr(pull_mod.trackify_db, "is_configured", lambda: True)
    monkeypatch.setattr(pull_mod, "cdp_now", lambda: 2_000_000_000.0)
    monkeypatch.setattr(pull_mod, "fetch_changes", lambda *a, **k: [{
        "rows": [{"row_id": "r9", "tk_contact_id": "uuid-cdp-11",
                  "field_id": "f-data", "slug": "data_cdp", "new_value": "system",
                  "source": "manual", "user_id": "humano-2",
                  "created_epoch": 1_999_999_000.0}],
        "truncated": False}])

    resumo = pull_mod.cycle()
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
    monkeypatch.setattr(push, "trackify_db_run", lambda *a, **k: [])

    from sqlalchemy import text as _t

    from db.engine import get_engine
    with get_engine().begin() as conn:
        conn.execute(_t("DELETE FROM plugin_trackify_field_outbox"))
    sync_state = _load("sync_state")
    sync_state.set_cursor("reconcile", 0, "", "")

    resumo = reconcile.cycle()
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
    monkeypatch.setattr(push, "trackify_db_run", lambda *a, **k: [])
    _load("sync_state").set_cursor("reconcile", 0, "", "")

    resumo = reconcile.cycle()
    assert resumo["enfileirados"] == reconcile.MAX_PUSHES
    # E o cursor andou, para o resto entrar no ciclo seguinte em vez de sumir.
    cur = _load("sync_state").get_cursor("reconcile")
    assert cur["cursor_id"]


def test_todas_as_rotas_novas_respondem_sem_configuracao(plugin_app):
    """Fumaça: o plugin sobe e nenhuma rota nova quebra numa instalação crua.

    Sem DSN, sem credencial e sem mapeamento é o estado de TODA instalação no
    primeiro boot — e é exatamente quando um 500 numa rota deixaria a aba em
    branco, sem explicação.
    """
    built = plugin_app("trackify")
    for path in ("/api/plugins/trackify/health",
                 "/api/plugins/trackify/contact-attributes",
                 "/api/plugins/trackify/trackify-fields",
                 "/api/plugins/trackify/mappings",
                 "/api/plugins/trackify/service-account",
                 "/api/plugins/trackify/field-sync/status"):
        r = built.client.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code} {r.text[:200]}"
        assert r.json()["ok"] is True

    r = built.client.post("/api/plugins/trackify/service-account/test", json={})
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
    """A aba "Informações do Contato" do Trackify mostra TODO campo ativo, com o
    valor em branco quando o contato não tem o dado. Omitir o campo torna "sem
    CPF cadastrado" indistinguível de "não existe campo de CPF" — e é justamente
    a ausência que o atendente precisa enxergar."""
    import datetime as _dt

    contato = [{"id": "uuid-1", "status": "lead", "total_spent": Decimal("0"),
                "first_seen_at": _dt.datetime(2026, 7, 31), "converted_at": None,
                "created_at": _dt.datetime(2026, 7, 31)}]
    campos = [
        {"slug": "email", "name": "Email", "is_identifier": True,
         "value": "leandro@exemplo.com"},
        {"slug": "cpf", "name": "CPF", "is_identifier": True, "value": None},
        {"slug": "name", "name": "Nome", "is_identifier": False, "value": "Leandro"},
        {"slug": "cidade", "name": "Cidade", "is_identifier": False, "value": None},
        {"slug": "estado", "name": "Estado", "is_identifier": False, "value": "GO"},
    ]

    def _fake(sql, params):
        if "FROM contacts c" in sql:
            return contato
        if "custom_fields cf" in sql:
            return campos
        return []
    monkeypatch.setattr(journey.trackify_db, "run_read", _fake)

    out = journey.fetch_identity("uuid-1")
    assert [f["slug"] for f in out["identifiers"]] == ["email", "cpf"]
    assert [f["slug"] for f in out["fields"]] == ["name", "cidade", "estado"]
    # Vazio vira string vazia (a tela mostra "—"), NUNCA some da lista.
    assert next(f for f in out["identifiers"] if f["slug"] == "cpf")["value"] == ""
    assert out["name"] == "Leandro"


def test_cadastro_le_de_custom_fields_e_nao_dos_valores(journey):
    """Se a consulta partisse de ``contact_field_values``, campo sem linha para
    aquele contato jamais apareceria — que é o bug que este teste tranca."""
    sql = journey._SQL_FIELDS
    assert "FROM custom_fields cf" in sql
    assert "LEFT JOIN contact_field_values" in sql
    assert "cf.is_active" in sql


def _ev(tipo, quando, campos, valor=None):
    import datetime as _dt
    return {"id": f"e-{quando}", "event_type": tipo, "title": tipo,
            "value": Decimal(str(valor)) if valor is not None else None,
            "occurred_at": _dt.datetime(2026, 1, quando, 12, 0), "fields": campos}


def test_produtos_o_evento_mais_recente_decide_a_posse(journey, monkeypatch):
    """O Trackify não tem tabela de produto: posse é derivada dos eventos."""
    linhas = [   # a consulta devolve do mais NOVO para o mais antigo
        _ev("subscription_canceled", 20, {"product_name": "Combo de Redes"}),
        _ev("active_subscription", 10, {"product_name": "Combo de Redes",
                                        "subscription_interval": "Mensal"}, 97),
        _ev("purchase", 5, {"product_name": "Curso Avulso",
                            "payment_method": "Pix"}, 197),
    ]
    monkeypatch.setattr(journey.trackify_db, "run_read", lambda *a, **k: linhas)
    produtos = journey.fetch_products("uuid-1", today=datetime.date(2026, 2, 1))

    assert [p["name"] for p in produtos] == ["Curso Avulso", "Combo de Redes"]
    combo = next(p for p in produtos if p["name"] == "Combo de Redes")
    assert combo["active"] is False and combo["last_event_type"] == "subscription_canceled"
    # Atributo que só o evento ANTIGO trazia é preservado.
    assert combo["interval"] == "Mensal"
    assert combo["events"] == 2
    curso = next(p for p in produtos if p["name"] == "Curso Avulso")
    assert curso["active"] is True and curso["paid_total"] == "R$ 197,00"
    assert curso["payment_method"] == "Pix"


def test_produtos_ativos_vem_antes_dos_perdidos(journey, monkeypatch):
    linhas = [
        _ev("refunded", 30, {"product_name": "Recente e reembolsado"}),
        _ev("purchase", 1, {"product_name": "Antigo mas ativo"}, 50),
    ]
    monkeypatch.setattr(journey.trackify_db, "run_read", lambda *a, **k: linhas)
    produtos = journey.fetch_products("uuid-1")
    assert [p["active"] for p in produtos] == [True, False]


def test_evento_sem_nome_de_produto_nao_vira_produto(journey, monkeypatch):
    """Sem esta guarda, cair no ``event_type`` como chave inventaria um produto
    chamado "purchase" na tela do atendente."""
    linhas = [_ev("purchase", 5, {"transaction_id": "abc"}, 10),
              _ev("charge.paid", 6, {}, 10)]
    monkeypatch.setattr(journey.trackify_db, "run_read", lambda *a, **k: linhas)
    assert journey.fetch_products("uuid-1") == []


def test_data_torta_do_cdp_nao_derruba_os_produtos(journey, monkeypatch):
    """`next_charge_date` é TEXT em dd/mm/aaaa e `subscription_canceled_at` chega
    valendo a string "system" — valores REAIS de produção."""
    linhas = [_ev("active_subscription", 10, {
        "product_name": "Combo", "next_charge_date": "25/02/2027",
        "subscription_canceled_at": "system"}, 97)]
    monkeypatch.setattr(journey.trackify_db, "run_read", lambda *a, **k: linhas)
    p = journey.fetch_products("uuid-1", today=datetime.date(2026, 7, 31))[0]
    assert p["next_charge"] == "2027-02-25" and p["days_left"] == 209
    assert p["next_charge_raw"] == "25/02/2027"
    assert p["canceled_at"] is None      # "system" NÃO virou data


def test_jornada_completa_carrega_os_produtos(journey, monkeypatch):
    monkeypatch.setattr(journey, "fetch_identity", lambda cid: {"contact_id": cid})
    monkeypatch.setattr(journey, "fetch_subscriptions", lambda cid: [])
    monkeypatch.setattr(journey, "fetch_products", lambda cid: [{"name": "X"}])
    monkeypatch.setattr(journey, "fetch_timeline", lambda cid: {"events": []})
    monkeypatch.setattr(journey, "fetch_event_types", lambda cid: [])
    out = journey.build_journey("uuid-1")
    assert out["products"] == [{"name": "X"}]


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


def test_erro_de_login_distingue_senha_de_falha_do_servidor(sessao):
    """Mandar o operador conferir a senha quando o problema é do servidor faz ele
    trocar a senha e continuar quebrado. Um 401 é credencial; um 5xx acontece
    DEPOIS da checagem de senha (ao criar a sessão) — são conselhos opostos."""
    m401 = sessao.login_error_message(401)
    m500 = sessao.login_error_message(500)
    assert "senha" in m401.lower() and "401" in m401
    assert "trackify_sessions" in m500 and "401" in m500   # explica como diferenciar
    assert "5 por minuto" in sessao.login_error_message(429)


def test_falha_de_login_aparece_na_primeira_tentativa(sessao, monkeypatch, plugin_app):
    """Antes, o erro só ficava visível depois de 3 falhas seguidas — e o contador
    zera a cada restart do servidor, então na prática NUNCA aparecia: a fila
    acumulava "sem sessão" sem nada na tela explicando o porquê."""
    plugin_app("trackify")
    monkeypatch.setattr(sessao._config, "setting", lambda k, d=None: {
        "service_email": "bot@x.com", "service_password": "s3nha"}.get(k, d))
    client = _FakeClient(_FakeResp(500, {"message": "Internal Server Error"}))
    assert _run(sessao._login(client)) is None

    from db.repositories import config_repo
    gravado = config_repo.get("plugin.trackify.sync_last_login_error", "")
    assert gravado and "500" in gravado
    # ...mas UMA falha não pode desligar a sincronização.
    assert not config_repo.get("plugin.trackify.sync_blocked_reason", "")


@pytest.mark.parametrize("status", [200, 201, 204])
def test_login_aceita_qualquer_2xx(sessao, monkeypatch, status):
    """A rota é ``@Post('login')`` SEM ``@HttpCode``, e o padrão do NestJS para
    POST é 201 — o ``@ApiResponse({status: 200})`` do controller é só Swagger e
    mente sobre o runtime. Exigir 200 exato descartava um login BEM-SUCEDIDO
    como se fosse recusa, com a mensagem "Login recusado (HTTP 201)"."""
    class _H(dict):
        def get_list(self, k):
            return ["trackify_session=tok; Path=/"]
    monkeypatch.setattr(sessao._config, "setting", lambda k, d=None: {
        "service_email": "bot@x.com", "service_password": "s3nha"}.get(k, d))
    monkeypatch.setattr(sessao, "_set_config", lambda **kw: None)
    client = _FakeClient(_FakeResp(status, {"user": {"id": "7"}}, headers=_H()))
    sess = _run(sessao._login(client))
    assert sess is not None and sess.user_id == "7"


def test_escrita_no_contato_aceita_qualquer_2xx(writer, _api_base):
    sess = _load("session").Session("trackify_session=t", "7", 0.0)
    client = _FakeClient(_FakeResp(201, _cdp_contact(email="a@b.com")))
    res = _run(writer.put_contact(client, sess, "uuid-1", {"email": "a@b.com"}))
    assert res.verdict == writer.OK


def test_edicao_humana_com_a_conta_de_servico_compartilhada_e_aplicada(
        plugin_app, monkeypatch):
    """Regressão do caso real: o operador estava logado na tela do Trackify com a
    MESMA conta configurada na integração, então toda edição dele carregava o
    ``user_id`` da conta de serviço. O filtro por autor descartava tudo em
    silêncio — a Jornada (que lê o Trackify ao vivo) mostrava o valor novo e o
    painel do WhatsBot ficava no antigo, sem nenhum erro em lugar nenhum.
    """
    built = plugin_app("trackify", settings_overrides=_field_sync_on(**{
        "plugin.trackify.field_sync_pull_enabled": True,
        "plugin.trackify.sync_user_id": "svc-1",
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

    monkeypatch.setattr(pull_mod.trackify_db, "is_configured", lambda: True)
    monkeypatch.setattr(pull_mod, "cdp_now", lambda: 2_000_000_000.0)

    def _linhas(*a, **k):
        return [{"rows": [
            # 1) o nosso próprio envio voltando: mesmo autor, MESMO valor -> eco
            {"row_id": "r1", "tk_contact_id": "uuid-cdp-30", "field_id": "f-mail",
             "slug": "email", "new_value": "antigo@x.com", "source": "manual",
             "user_id": "svc-1", "created_epoch": 1_999_998_000.0},
            # 2) o HUMANO usando a mesma conta: mesmo autor, valor NOVO -> aplica
            {"row_id": "r2", "tk_contact_id": "uuid-cdp-30", "field_id": "f-mail",
             "slug": "email", "new_value": "novo@x.com", "source": "manual",
             "user_id": "svc-1", "created_epoch": 1_999_999_000.0},
        ], "truncated": False}]
    monkeypatch.setattr(pull_mod, "fetch_changes", _linhas)

    resumo = pull_mod.cycle()
    assert resumo["ecos"] == 1                      # o nosso envio foi ignorado
    assert resumo["gravadas"] == 1                  # a edição do humano entrou
    assert resumo["conta_compartilhada"] == 1       # e ficou registrado o porquê
    assert custom_attribute_repo.get_values(contacts_tbl, cid)["email"] == "novo@x.com"


# ── Busca automática por telefone + campos conectados ────────────────────

def test_resolve_por_slug_conhece_as_normalizacoes_de_cada_identificador(monkeypatch):
    """Cada identificador circula de um jeito: telefone tem variante brasileira,
    CPF vai com e sem máscara, e-mail é insensível a caixa."""
    identity = _load("identity")
    consultas = []
    monkeypatch.setattr(identity, "_by_values",
                        lambda slug, cands: consultas.append((slug, cands)) or [])
    monkeypatch.setattr(identity, "_by_digits", lambda slug, d: [])

    identity.resolve_by_slug("whatsapp", "556496162906")
    identity.resolve_by_slug("cpf", "056.224.381-01")
    identity.resolve_by_slug("email", " Joao@Empresa.COM ")
    # Identificador criado pelo cliente: comparação exata, sem regra especial.
    identity.resolve_by_slug("matricula", "A-1234")

    por_slug = dict((s, c) for s, c in consultas)
    assert "5564996162906" in por_slug["whatsapp"]      # variante com o 9
    assert "05622438101" in por_slug["cpf"] and "056.224.381-01" in por_slug["cpf"]
    assert "joao@empresa.com" in por_slug["email"]
    assert por_slug["matricula"] == ["A-1234"]


def test_busca_usa_telefone_e_os_campos_conectados(monkeypatch):
    """O ponto da mudança: conectar um campo passa a valer para ENCONTRAR o
    cadastro, não só para copiar o valor depois."""
    identity = _load("identity")
    tentados = []

    def _fake(slug, value):
        tentados.append(slug)
        return [identity.Match("uuid-x", slug, value, "variant")] if slug == "cpf" else []
    monkeypatch.setattr(identity, "resolve_by_slug", _fake)
    monkeypatch.setattr(identity, "_prioridades", lambda: {
        "email": 10, "whatsapp": 20, "cpf": 30})

    achou = identity.resolve_mapped(
        phone="5564996162906",
        extras={"email": "x@y.com", "cpf": "05622438101"})

    assert [m.slug for m in achou] == ["cpf"]
    # Ordem = prioridade do próprio Trackify: divergir dela faria a leitura casar
    # num contato e a escrita da ingestão em outro.
    assert tentados == ["email", "whatsapp", "cpf"]


def test_telefone_entra_na_busca_mesmo_sem_estar_mapeado(monkeypatch):
    identity = _load("identity")
    tentados = []
    monkeypatch.setattr(identity, "resolve_by_slug",
                        lambda s, v: tentados.append((s, v)) or [])
    monkeypatch.setattr(identity, "_prioridades", lambda: {"whatsapp": 20})
    identity.resolve_mapped(phone="5564996162906", extras={})
    assert tentados == [("whatsapp", "5564996162906")]


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

    monkeypatch.setattr(identity, "resolve_mapped", lambda **k: [
        identity.Match("uuid-novo", "cpf", "05622438101", "variant")])
    monkeypatch.setattr(push, "trackify_db_run", lambda *a, **k: [])

    plano = push.plan_for_contact(int(c["id"]))
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
    monkeypatch.setattr(identity, "resolve_mapped", lambda **k: [
        identity.Match("uuid-a", "cpf", "1", "variant"),
        identity.Match("uuid-b", "cpf", "1", "variant")])

    plano = push.plan_for_contact(int(c["id"]))
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
