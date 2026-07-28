"""Posse temporária do atendente pós-fechamento (plugin ``protocolos`` 1.22.0).

"Quem resolveu fica com a conversa por N minutos, depois a IA reassume."

Cobre a política inteira:

* config — janela default/clamp/round-trip no ``general-config`` e o gate
  ``ai_takeover_enabled`` (exige a devolução ligada E janela > 0);
* ``clear_assignee_on_close`` — mantém o atendente durante a janela, e só ele
  (conversa sem dono continua caindo no comportamento do core);
* ARMAR — modo ``owner`` (não mexe na conversa) × modo ``muted`` (grava ``ai_active=0``);
* CANCELAR — reabertura manual, atendente respondendo, IA religada na mão, transferência
  entre humanos, conversa deletada e protocolo reaberto;
* VENCER — a varredura devolve a conversa à IA via ``conversation_service.set_ai``, e
  respeita os gates global/canal.

Aponta para a cópia INSTALADA em ``storages/plugins/protocolos`` (monkeypatch
``REAL_PLUGIN_EXAMPLES``), como os demais testes do plugin.

    venv/bin/python -m pytest tests/test_protocolos_ai_takeover.py -q
"""

from __future__ import annotations

import asyncio
import importlib
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from db.engine import get_engine
from db.repositories import (config_repo, contact_inbox_repo, contact_repo,
                             conversation_repo)

_STORAGES_PLUGINS = Path(__file__).resolve().parents[1] / "storages" / "plugins"
INBOX_ID = 1


@pytest.fixture(autouse=True)
def _load_from_storages(monkeypatch):
    monkeypatch.setattr("tests.support.REAL_PLUGIN_EXAMPLES", _STORAGES_PLUGINS)


def _logic():
    return importlib.import_module("whatsbot_plugins.protocolos.logic")


@pytest.fixture
def logic(build_app):
    """App com o plugin carregado (migrations aplicadas) + config na janela default."""
    build_app(["gowa", "protocolos"])
    mod = _logic()
    _reset_config(mod)
    yield mod
    _reset_config(mod)


def _reset_config(mod) -> None:
    config_repo.set(f"plugin.{mod.PLUGIN_ID}.reactivate_ai_on_close", True)
    config_repo.set(mod._general_key("ai_takeover_delay_minutes"), 30)
    config_repo.set(mod._general_key("resolve_keep_assignee"), False)


def _conversation(*, assignee_user_id=None, ai_active=1, status="open") -> dict:
    """Conversa REAL do core (telefone único por teste — o banco é global ao processo)."""
    phone = f"55119{uuid.uuid4().int % 10**8:08d}"
    jid = f"{phone}@s.whatsapp.net"
    contact = contact_repo.get_or_create(phone)
    ci = contact_inbox_repo.get_or_create(
        inbox_id=INBOX_ID, contact_id=contact["id"], source_id=jid, source_jid=jid)
    conv = conversation_repo.create(
        inbox_id=INBOX_ID, contact_id=contact["id"], contact_inbox_id=ci["id"],
        ai_active=ai_active)
    if conv["ai_active"] != ai_active:
        # O create aplica o gate GLOBAL (auto_reply) sobre o seed; para este teste o
        # que importa é o estado pedido, então firmamos com a primitiva low-level.
        conv = conversation_repo.set_ai_active(conv["id"], ai_active)
    if assignee_user_id is not None:
        conv = conversation_repo.set_assignee(conv["id"], assignee_user_id)
    if status != "open":
        conv = conversation_repo.set_status(conv["id"], status, clear_assignee=False)
    return conv


class _Ctx:
    """FilterContext mínimo (só ``extras``), como o core entrega ao filtro."""

    def __init__(self, extras):
        self.extras = extras


@pytest.fixture
def _deps(monkeypatch):
    """``plugins.context.get_deps`` cabeado: o harness roda com a lifespan no-op, então
    os deps do runtime (que a varredura usa para chamar ``set_ai``) ficariam nulos."""
    import plugins.context as plugin_context

    monkeypatch.setattr(plugin_context, "get_deps", lambda: object())


# ── Config ────────────────────────────────────────────────────────────────────

def test_janela_default_e_clamp(logic):
    assert logic.ai_takeover_delay_minutes() == 30
    assert logic.ai_takeover_enabled() is True

    logic.set_general_config({"ai_takeover_delay_minutes": "abc"})   # inválido → default
    assert logic.ai_takeover_delay_minutes() == 30
    logic.set_general_config({"ai_takeover_delay_minutes": -5})      # negativo → 0
    assert logic.ai_takeover_delay_minutes() == 0
    assert logic.ai_takeover_enabled() is False                      # 0 desliga a posse
    logic.set_general_config({"ai_takeover_delay_minutes": 99999})   # teto de 7 dias
    assert logic.ai_takeover_delay_minutes() == 10080


def test_general_config_roundtrip_e_payload_legado(logic):
    cfg = logic.get_general_config()
    assert cfg["ai_takeover_delay_minutes"] == 30

    logic.set_general_config({"ai_takeover_delay_minutes": 5})
    assert logic.get_general_config()["ai_takeover_delay_minutes"] == 5
    # Payload sem a chave NÃO zera o valor gravado.
    logic.set_general_config({"relink_prompt_enabled": True})
    assert logic.get_general_config()["ai_takeover_delay_minutes"] == 5


def test_devolucao_desligada_desliga_a_posse(logic):
    logic.set_general_config({"reactivate_ai_on_close": False})
    assert logic.ai_takeover_enabled() is False   # sem devolução não há prazo a contar


# ── clear_assignee_on_close ───────────────────────────────────────────────────

def test_mantem_o_atendente_durante_a_janela(logic):
    conv = _conversation(assignee_user_id=4242)
    ctx = _Ctx({"conversation_id": conv["id"]})
    assert logic.clear_assignee_on_close(ctx, True) is False


def test_conversa_sem_dono_nao_muda_o_comportamento_do_core(logic):
    conv = _conversation()
    assert logic.clear_assignee_on_close(_Ctx({"conversation_id": conv["id"]}), True) is True


def test_sem_janela_o_filtro_so_responde_pelo_toggle_legado(logic):
    conv = _conversation(assignee_user_id=4242)
    logic.set_general_config({"ai_takeover_delay_minutes": 0})
    ctx = _Ctx({"conversation_id": conv["id"]})
    assert logic.clear_assignee_on_close(ctx, True) is True     # posse desligada
    logic.set_general_config({"resolve_keep_assignee": True})
    assert logic.clear_assignee_on_close(ctx, True) is False    # "manter para sempre"


def test_filtro_tolera_ctx_ausente(logic):
    """O core pode chamar sem extras (e os testes chamam direto): nunca levanta."""
    assert logic.clear_assignee_on_close(None, True) is True
    assert logic.clear_assignee_on_close(_Ctx({}), True) is True


# ── Armar ─────────────────────────────────────────────────────────────────────

def test_armar_com_atendente_nao_toca_a_conversa(logic):
    conv = _conversation(assignee_user_id=4242)
    before = conversation_repo.get(conv["id"])["ai_active"]
    logic.on_conversation_status(None, {"conversation_id": conv["id"], "status": "closed",
                                        "assignee_user_id": 4242, "ai_active": before})

    hold = logic.get_ai_hold(conv["id"])
    assert hold is not None
    assert hold["mode"] == "owner"
    assert hold["owner_user_id"] == 4242
    assert hold["hold_until"] > time.time()
    # Modo owner: quem cala a IA é o gate de humano do core, não um ai_active forjado —
    # a conversa sai do fechamento exatamente como entrou.
    assert conversation_repo.get(conv["id"])["ai_active"] == before


def test_armar_sem_atendente_cala_a_ia(logic):
    conv = _conversation()
    logic.on_conversation_status(None, {"conversation_id": conv["id"], "status": "closed",
                                        "assignee_user_id": None, "ai_active": 1})

    hold = logic.get_ai_hold(conv["id"])
    assert hold["mode"] == "muted"
    assert hold["owner_user_id"] is None
    assert conversation_repo.get(conv["id"])["ai_active"] == 0   # a mordaça


def test_nao_arma_com_a_posse_desligada(logic):
    logic.set_general_config({"ai_takeover_delay_minutes": 0})
    conv = _conversation(assignee_user_id=4242)
    logic.on_conversation_status(None, {"conversation_id": conv["id"], "status": "closed",
                                        "assignee_user_id": 4242, "ai_active": 1})
    assert logic.get_ai_hold(conv["id"]) is None


def test_janela_configurada_define_o_prazo(logic):
    logic.set_general_config({"ai_takeover_delay_minutes": 5})
    conv = _conversation(assignee_user_id=1)
    before = time.time()
    logic.arm_ai_hold(conv)
    hold = logic.get_ai_hold(conv["id"])
    assert 5 * 60 - 5 <= hold["hold_until"] - before <= 5 * 60 + 5


# ── Cancelar (o humano agiu) ──────────────────────────────────────────────────

def test_reabertura_manual_cancela(logic):
    conv = _conversation(assignee_user_id=4242)
    logic.arm_ai_hold(conv)
    logic.on_conversation_status(None, {"conversation_id": conv["id"], "status": "open"})
    assert logic.get_ai_hold(conv["id"]) is None


def test_reabertura_manual_religa_a_ia_no_modo_muted(logic):
    conv = _conversation()
    logic.arm_ai_hold(conv)
    assert conversation_repo.get(conv["id"])["ai_active"] == 0
    logic.on_conversation_status(None, {"conversation_id": conv["id"], "status": "open"})
    assert conversation_repo.get(conv["id"])["ai_active"] == 1


def test_atendente_respondendo_cancela_e_fica_com_a_conversa(logic):
    conv = _conversation(assignee_user_id=4242)
    logic.arm_ai_hold(conv)
    phone = contact_repo.get(conv["contact_id"])["phone"]

    logic.cancel_ai_hold_on_human_send({"phone": phone, "source": "operator"})

    assert logic.get_ai_hold(conv["id"]) is None
    # A conversa continua dele — a devolução automática simplesmente não acontece.
    assert conversation_repo.get(conv["id"])["assignee_user_id"] == 4242


def test_resposta_da_ia_nao_cancela(logic):
    conv = _conversation(assignee_user_id=4242)
    logic.arm_ai_hold(conv)
    phone = contact_repo.get(conv["contact_id"])["phone"]

    logic.cancel_ai_hold_on_human_send({"phone": phone, "source": "ai"})
    logic.cancel_ai_hold_on_human_send({"phone": phone, "source": "echo"})

    assert logic.get_ai_hold(conv["id"]) is not None


def test_ia_religada_na_mao_cancela(logic):
    conv = _conversation(assignee_user_id=4242)
    logic.arm_ai_hold(conv)
    logic.on_conversation_ai_toggled(None, {"conversation_id": conv["id"], "ai_active": 1})
    assert logic.get_ai_hold(conv["id"]) is None
    # Desligar a IA não mexe na janela.
    logic.arm_ai_hold(conv)
    logic.on_conversation_ai_toggled(None, {"conversation_id": conv["id"], "ai_active": 0})
    assert logic.get_ai_hold(conv["id"]) is not None


def test_ia_assumindo_pela_atribuicao_cancela(logic):
    conv = _conversation(assignee_user_id=4242)
    logic.arm_ai_hold(conv)
    logic.on_conversation_assigned(None, {"conversation_id": conv["id"],
                                          "assignee_user_id": None,
                                          "active_agent_key": "comercial"})
    assert logic.get_ai_hold(conv["id"]) is None


def test_transferencia_entre_humanos_troca_o_dono_e_mantem_o_prazo(logic):
    conv = _conversation(assignee_user_id=4242)
    logic.arm_ai_hold(conv)
    until = logic.get_ai_hold(conv["id"])["hold_until"]

    logic.on_conversation_assigned(None, {"conversation_id": conv["id"],
                                          "assignee_user_id": 77,
                                          "active_agent_key": None})

    hold = logic.get_ai_hold(conv["id"])
    assert hold["owner_user_id"] == 77
    assert hold["hold_until"] == pytest.approx(until)


def test_conversa_deletada_apaga_o_hold(logic):
    conv = _conversation(assignee_user_id=4242)
    logic.arm_ai_hold(conv)
    logic.on_conversation_deleted(None, {"conversation_id": conv["id"]})
    assert logic.get_ai_hold(conv["id"]) is None


def test_reabrir_protocolo_apaga_os_holds_dele(logic):
    conv = _conversation()
    ts = time.time()
    with get_engine().begin() as conn:
        pid = conn.execute(text(
            "INSERT INTO plugin_protocolos_protocolos "
            "(contact_id, contact_phone, contact_name, status, fields, opened_at, "
            " closed_at, created_at, updated_at) "
            "VALUES (:cid, '5511', 'Cliente', 'fechado', '{}', :ts, :ts, :ts, :ts) "
            "RETURNING id"), {"cid": conv["contact_id"], "ts": ts}).scalar()
    logic.arm_ai_hold(conv, protocolo_id=int(pid))
    assert conversation_repo.get(conv["id"])["ai_active"] == 0   # modo muted

    at, err = logic.reopen_protocolo(int(pid))
    assert err is None and at["status"] == "aberto"
    assert logic.get_ai_hold(conv["id"]) is None
    assert conversation_repo.get(conv["id"])["ai_active"] == 1   # a mordaça foi solta


# ── Vencer ────────────────────────────────────────────────────────────────────

def _expire(logic, conv_id: int) -> None:
    """Empurra o prazo para o passado (evita sleep no teste)."""
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE plugin_protocolos_ai_holds SET hold_until = :t "
                          "WHERE conversation_id = :c"),
                     {"t": time.time() - 1, "c": conv_id})


def test_vencimento_devolve_a_conversa_a_ia(logic, monkeypatch, _deps):
    conv = _conversation(assignee_user_id=4242)
    logic.arm_ai_hold(conv)
    _expire(logic, conv["id"])

    calls = []

    async def _fake_set_ai(deps, c, active, **kw):
        calls.append((c["id"], active, kw))
        return c

    from app.services import conversation_service
    monkeypatch.setattr(conversation_service, "set_ai", _fake_set_ai)
    monkeypatch.setattr(logic, "_ai_master_gate", lambda _cid: True)

    assert asyncio.run(logic.expire_ai_holds_once()) == 1
    assert calls == [(conv["id"], 1, {"actor_name": None, "clear_transfer_tag": False})]
    assert logic.get_ai_hold(conv["id"]) is None


def test_vencimento_respeita_o_gate_global_do_canal(logic, monkeypatch, _deps):
    """IA desligada no global/canal ⇒ não religa (silêncio é intencional), mas a linha
    sai da fila para a varredura não ficar tentando para sempre."""
    conv = _conversation(assignee_user_id=4242)
    logic.arm_ai_hold(conv)
    _expire(logic, conv["id"])

    async def _boom(*a, **kw):  # pragma: no cover — não deve ser chamado
        raise AssertionError("set_ai não deveria ser chamado com o gate desligado")

    from app.services import conversation_service
    monkeypatch.setattr(conversation_service, "set_ai", _boom)
    monkeypatch.setattr(logic, "_ai_master_gate", lambda _cid: False)

    assert asyncio.run(logic.expire_ai_holds_once()) == 0
    assert logic.get_ai_hold(conv["id"]) is None


def test_hold_no_prazo_nao_e_devolvido(logic, monkeypatch, _deps):
    conv = _conversation(assignee_user_id=4242)
    logic.arm_ai_hold(conv)   # 30 min à frente

    async def _boom(*a, **kw):  # pragma: no cover
        raise AssertionError("set_ai não deveria ser chamado antes do vencimento")

    from app.services import conversation_service
    monkeypatch.setattr(conversation_service, "set_ai", _boom)

    assert asyncio.run(logic.expire_ai_holds_once()) == 0
    assert logic.get_ai_hold(conv["id"]) is not None


def test_vencimento_de_conversa_apagada_so_limpa_a_linha(logic, monkeypatch, _deps):
    conv = _conversation(assignee_user_id=4242)
    logic.arm_ai_hold(conv)
    _expire(logic, conv["id"])
    monkeypatch.setattr(conversation_repo, "get", lambda _cid: None)

    assert asyncio.run(logic.expire_ai_holds_once()) == 0
    assert logic.get_ai_hold(conv["id"]) is None
