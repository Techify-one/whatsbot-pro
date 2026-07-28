"""Plano 68 — reabrir + atribuir o atendimento ao vencer um agendamento de retorno.

Cobre o passo ``_reopen_and_assign`` que o ``dispatch`` do plugin executa no vencimento:
desarquivar → reabrir → atribuir a quem agendou, idempotente e gated pelo setting
``reopen_assign_on_due`` (default ON). Também trava o texto com o autor e o role
``system_notice`` (que NÃO polui o histórico da IA — está na exclusão do
``message_repo``).

Exercita a cópia INSTALADA (real) em ``storages/plugins/agendamento_retorno``.

    venv/bin/python -m pytest tests/test_plano68_reopen_assign_on_due.py -q
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

_STORAGES_PLUGINS = Path(__file__).resolve().parents[1] / "storages" / "plugins"


@pytest.fixture(autouse=True)
def _load_from_storages(monkeypatch):
    monkeypatch.setattr("tests.support.REAL_PLUGIN_EXAMPLES", _STORAGES_PLUGINS)


def _logic(build_app):
    build_app(["gowa", "agendamento_retorno"])
    return importlib.import_module("whatsbot_plugins.agendamento_retorno.logic")


def _patch_service(monkeypatch, logic, conv):
    """Monkeypatcha o repo + serviço + a ponte e devolve a lista de ações capturadas.

    Cada função do ``conversation_service`` vira um stub síncrono que devolve um marcador
    ``("verbo", args)``; ``_run_on_loop`` só registra o marcador recebido — assim a lista
    ``ordered`` reflete quais verbos foram invocados e em que ordem, sem loop real."""
    from db.repositories import conversation_repo, config_repo
    from app.services import conversation_service

    # DB de teste é compartilhado no processo — garante o toggle ON (o teste de
    # toggle-off o sobrescreve via monkeypatch de reopen_assign_on_due_enabled).
    config_repo.set(f"plugin.{logic.PLUGIN_ID}.reopen_assign_on_due", True)
    ordered: list = []
    monkeypatch.setattr(conversation_repo, "get_with_channel", lambda _id: conv)
    monkeypatch.setattr(conversation_service, "archive",
                        lambda deps, c, archived, **kw: ("archive", archived, kw))
    monkeypatch.setattr(conversation_service, "set_status",
                        lambda deps, c, status, **kw: ("set_status", status, kw))
    monkeypatch.setattr(conversation_service, "assign",
                        lambda deps, c, uid, **kw: ("assign", uid, kw))
    monkeypatch.setattr(logic, "_run_on_loop", lambda marker, **kw: ordered.append(marker))
    return ordered


def _verbs(ordered):
    return [m[0] for m in ordered]


# ── Texto + setting ──────────────────────────────────────────────────────────

def test_build_message_text_includes_scheduler(build_app):
    logic = _logic(build_app)
    msg = logic.build_message_text("ligar amanhã", "Cliente X", "Atendente Y")
    assert "Cliente X" in msg
    assert "Agendado por: Atendente Y" in msg
    assert "Descrição: ligar amanhã" in msg


def test_build_message_text_without_scheduler(build_app):
    logic = _logic(build_app)
    msg = logic.build_message_text("nota", "Cliente X")
    assert "Agendado por" not in msg


def test_toggle_default_on_and_off(build_app):
    logic = _logic(build_app)
    from db.repositories import config_repo
    config_repo.delete_prefix(f"plugin.{logic.PLUGIN_ID}.reopen_assign_on_due")
    assert logic.reopen_assign_on_due_enabled() is True
    config_repo.set(f"plugin.{logic.PLUGIN_ID}.reopen_assign_on_due", False)
    assert logic.reopen_assign_on_due_enabled() is False


# ── Guardas do _reopen_and_assign ────────────────────────────────────────────

def test_closed_archived_reopens_assigns_unarchives(build_app, monkeypatch):
    logic = _logic(build_app)
    conv = {"id": 7, "status": "closed", "is_archived": 1,
            "assignee_user_id": None, "channel_id": "default"}
    ordered = _patch_service(monkeypatch, logic, conv)
    row = {"conversation_id": 7, "assignee_user_id": 42, "atendente_name": "Fulano"}

    out = logic._reopen_and_assign(row, object())

    assert out is conv
    assert _verbs(ordered) == ["archive", "set_status", "assign"], \
        "ordem fixa: desarquivar → reabrir → atribuir"
    assert ordered[0][1] == 0                       # archive(archived=0)
    assert ordered[1][1] == "open"                  # set_status("open")
    assert ordered[2][1] == 42                       # assign(42)
    assert ordered[2][2].get("actor_name") == "Fulano"


def test_open_and_already_assigned_is_noop(build_app, monkeypatch):
    logic = _logic(build_app)
    conv = {"id": 7, "status": "open", "is_archived": 0,
            "assignee_user_id": 42, "channel_id": "default"}
    ordered = _patch_service(monkeypatch, logic, conv)
    row = {"conversation_id": 7, "assignee_user_id": 42, "atendente_name": "Fulano"}

    logic._reopen_and_assign(row, object())
    assert ordered == [], "conversa já aberta e atribuída ao mesmo → nenhum card"


def test_no_conversation_id_out_of_scope(build_app, monkeypatch):
    logic = _logic(build_app)
    ordered = _patch_service(monkeypatch, logic, {"id": 1})
    row = {"conversation_id": None, "assignee_user_id": 42}

    assert logic._reopen_and_assign(row, object()) is None
    assert ordered == []


def test_no_assignee_only_reopens(build_app, monkeypatch):
    logic = _logic(build_app)
    conv = {"id": 7, "status": "closed", "is_archived": 0,
            "assignee_user_id": None, "channel_id": "default"}
    ordered = _patch_service(monkeypatch, logic, conv)
    row = {"conversation_id": 7, "assignee_user_id": None, "atendente_name": ""}

    logic._reopen_and_assign(row, object())
    assert _verbs(ordered) == ["set_status"], "sem agendador: só reabre, não atribui"


def test_toggle_off_skips_actions_but_returns_conv(build_app, monkeypatch):
    logic = _logic(build_app)
    conv = {"id": 7, "status": "closed", "is_archived": 1,
            "assignee_user_id": None, "channel_id": "wa1"}
    ordered = _patch_service(monkeypatch, logic, conv)
    monkeypatch.setattr(logic, "reopen_assign_on_due_enabled", lambda: False)
    row = {"conversation_id": 7, "assignee_user_id": 42, "atendente_name": "Fulano"}

    out = logic._reopen_and_assign(row, object())
    assert out is conv, "ainda devolve o conv (usado p/ re-derivar o canal do dispatch)"
    assert ordered == [], "toggle off → comportamento antigo (nenhuma ação de lifecycle)"


# ── Não-poluição do LLM: system_notice é excluído do contexto ────────────────

def test_system_notice_role_excluded_from_llm_context(build_app):
    """A nota disparada usa role ``system_notice`` — que o ``message_repo`` exclui do
    contexto do LLM (ao contrário de ``private_note``, que entrava como ``user``)."""
    _logic(build_app)
    from db.repositories import contact_repo, message_repo

    contact = contact_repo.get_or_create("5511960000068")
    message_repo.add(contact["id"], "user", "oi")
    message_repo.add(contact["id"], "system_notice", "🔔 Retorno agendado por Fulano")

    ctx = message_repo.get_context(contact["id"], limit=50)
    roles = [m["role"] for m in ctx]
    assert "system_notice" not in roles, "system_notice não pode ir ao LLM"
    assert "user" in roles
