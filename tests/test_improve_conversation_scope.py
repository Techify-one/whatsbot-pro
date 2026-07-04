"""Plano 31 F2/F3 — "Gerar melhoria" multi-canal e multi-agente.

F2 (D.A): o card ``system`` da análise vai na CONVERSA da resposta marcada
(inbox ≠ default incluído), sem materializar conversa fantasma no inbox
default; ``conversation_id`` de outro contato é ignorado (validação de posse →
fallback D9); sem ``conversation_id`` o comportamento legado é preservado
(coberto também pelos goldens de characterization).

F3 (C1+): a análise reconstrói a cadeia de agentes do turno via
``executions.routing_steps`` e mostra o prompt INLINE CRU de cada agente
(render_template — convenção acordada com o plano 30/WS5: NÃO o prompt
enriquecido de runtime), as tools atribuídas e as usadas POR agente, e o
histórico escopado à conversa marcada.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import func, select


_ANALYSIS = "**Diagnóstico**\nok\n\n**Recomendações**\n- item"


def _fake_llm_response(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


class _FakeClient:
    def __init__(self, response):
        self.create_calls: list[dict] = []
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.create_calls.append(kwargs)
                return response

        self.chat = SimpleNamespace(completions=_Completions())


def _conv_count(contact_id: int) -> int:
    from db.engine import get_engine
    from db.tables import atendimentos

    with get_engine().connect() as conn:
        return conn.execute(
            select(func.count()).select_from(atendimentos)
            .where(atendimentos.c.contact_id == contact_id)
        ).scalar() or 0


def _mk_channel_inbox(channel_id: str):
    from db.repositories import channel_repo, inbox_repo

    if channel_repo.get(channel_id) is None:
        channel_repo.create(id=channel_id, provider="whatsapp_cloud",
                            display_name=channel_id, enabled=1)
    inbox = inbox_repo.get_by_channel(channel_id)
    if inbox is None:
        inbox = inbox_repo.create(channel_id=channel_id, name=channel_id)
    return inbox


def test_improve_card_lands_in_flagged_conversation(build_app):
    """Card na conversa real de um inbox ≠ default; nenhuma conversa nova."""
    built = build_app(["gowa"])
    handler = built.agent_handler
    phone = "5511932000001"
    _mk_channel_inbox("p31_cloud")

    contact = handler._get_contact(phone, channel_id="p31_cloud")
    contact.add_message("user", "qual o preço?")
    saved = contact.add_message("assistant", "não sei dizer")
    conv_id = saved["conversation_id"]
    assert conv_id is not None
    before = _conv_count(contact.id)

    fake = _FakeClient(_fake_llm_response(_ANALYSIS))
    with patch.object(handler, "_get_client", return_value=fake):
        r = built.client.post(f"/api/contacts/{phone}/improve", json={
            "message": {"content": "não sei dizer", "ts": saved["ts"],
                        "conversation_id": conv_id},
            "feedback": "saiu errado",
        })
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["role"] == "system"
    assert data["conversation_id"] == conv_id
    # D.A: sem conversa fantasma no inbox default.
    assert _conv_count(contact.id) == before


def test_improve_card_lands_in_flagged_conversation_even_if_not_latest(build_app):
    """Plano 31 review: o INSERT vai DIRETO na conversa marcada — mesmo quando
    OUTRA conversa do mesmo inbox é mais recente (o resolve por 'mais recente
    do inbox' do add_message escolheria a errada)."""
    built = build_app(["gowa"])
    handler = built.agent_handler
    phone = "5511932000005"

    contact = handler._get_contact(phone)
    contact.add_message("user", "pergunta")
    saved = contact.add_message("assistant", "resposta antiga")
    conv_a = saved["conversation_id"]

    # Fabrica uma conversa B FECHADA e mais recente no mesmo inbox (estado
    # legítimo: várias fechadas por contato/inbox são permitidas).
    from db.engine import get_engine
    from db.tables import atendimentos
    from sqlalchemy import insert, select

    with get_engine().begin() as conn:
        row = conn.execute(
            select(atendimentos).where(atendimentos.c.id == conv_a)
        ).mappings().first()
        vals = dict(row)
        vals.pop("id")
        vals.update(status="closed",
                    display_id=row["display_id"] + 987654,
                    last_activity_at=row["last_activity_at"] + 9999)
        conv_b = conn.execute(
            insert(atendimentos).values(**vals)).inserted_primary_key[0]

    fake = _FakeClient(_fake_llm_response(_ANALYSIS))
    with patch.object(handler, "_get_client", return_value=fake):
        r = built.client.post(f"/api/contacts/{phone}/improve", json={
            "message": {"content": "resposta antiga", "ts": saved["ts"],
                        "conversation_id": conv_a},
            "feedback": "",
        })
    assert r.status_code == 200, r.text
    assert r.json()["data"]["conversation_id"] == conv_a
    assert r.json()["data"]["conversation_id"] != conv_b


def test_improve_foreign_conversation_id_ignored(build_app):
    """conversation_id de OUTRO contato não injeta card em conversa alheia."""
    built = build_app(["gowa"])
    handler = built.agent_handler
    phone_a, phone_b = "5511932000002", "5511932000003"

    contact_b = handler._get_contact(phone_b)
    conv_b = contact_b.add_message("user", "oi")["conversation_id"]

    contact_a = handler._get_contact(phone_a)
    saved_a = contact_a.add_message("assistant", "resposta A")

    fake = _FakeClient(_fake_llm_response(_ANALYSIS))
    with patch.object(handler, "_get_client", return_value=fake):
        r = built.client.post(f"/api/contacts/{phone_a}/improve", json={
            "message": {"content": "resposta A", "ts": saved_a["ts"],
                        "conversation_id": conv_b},
            "feedback": "",
        })
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    # Posse validada → fallback: o card fica na conversa do próprio contato A.
    assert data["conversation_id"] != conv_b
    assert data["conversation_id"] == saved_a["conversation_id"]


def test_improve_analysis_shows_router_and_spoke(build_app):
    """C1+: análise cita prompt do router E do spoke + tools por agente, e o
    histórico é o da conversa marcada (não mistura outros canais)."""
    built = build_app(["gowa"])
    handler = built.agent_handler
    from db.repositories import agent_repo, execution_repo

    phone = "5511932000004"

    agent_repo.save(
        "p31_roteador", display_name="Roteador P31",
        prompt="PROMPT-DO-ROTEADOR-P31 {p31_var}",
        model_config={"model": "openai/gpt-4o-mini"},
        tool_names=["transferir_agente"], enabled=True, is_router=True)
    agent_repo.save(
        "p31_comercial", display_name="Comercial P31",
        prompt="PROMPT-DO-COMERCIAL-P31",
        model_config={"model": "openai/gpt-4o-mini"},
        tool_names=None, enabled=True)
    built.client.put("/api/ai/variables/p31_var", json={"value": "VALOR-RENDERIZADO"})

    # Histórico em DOIS canais: o do default não pode vazar pra análise.
    _mk_channel_inbox("p31_cloud2")
    other = handler._get_contact(phone, channel_id="default")
    other.add_message("user", "MENSAGEM-DO-CANAL-DEFAULT")
    contact = handler._get_contact(phone, channel_id="p31_cloud2")
    contact.add_message("user", "quero comprar")
    saved = contact.add_message("assistant", "segue o link")
    conv_id = saved["conversation_id"]

    # Execução do turno: router → comercial, com uma tool por hop.
    exec_id = execution_repo.create(phone)
    execution_repo.add_step(exec_id, "tool_executed",
                            {"tool": "transferir_agente",
                             "args": {"destino": "p31_comercial"}},
                            agent_key="p31_roteador")
    execution_repo.add_step(exec_id, "tool_executed",
                            {"tool": "save_contact_info", "args": {"name": "Zé"}},
                            agent_key="p31_comercial")
    execution_repo.set_routing_steps(exec_id, [
        {"from": "p31_roteador", "to": "p31_comercial", "depth": 1,
         "reason": "cliente quer comprar"},
    ])
    execution_repo.set_agent_key(exec_id, "p31_comercial")
    execution_repo.complete(exec_id)

    fake = _FakeClient(_fake_llm_response(_ANALYSIS))
    with patch.object(handler, "_get_client", return_value=fake):
        out = handler.generate_improvement(
            phone, {"content": "segue o link", "ts": time.time(),
                    "conversation_id": conv_id},
            "resposta errada", conversation_id=conv_id)
    assert out == _ANALYSIS

    assert len(fake.create_calls) == 1
    user_prompt = fake.create_calls[0]["messages"][1]["content"]

    # Cadeia completa, na ordem, com marcação de roteador.
    assert "Roteador P31 (p31_roteador) — ROTEADOR" in user_prompt
    assert "Comercial P31 (p31_comercial)" in user_prompt
    assert user_prompt.index("p31_roteador") < user_prompt.index("p31_comercial")
    # Prompt INLINE CRU renderizado (convenção com o plano 30/WS5).
    assert "PROMPT-DO-ROTEADOR-P31 VALOR-RENDERIZADO" in user_prompt
    assert "PROMPT-DO-COMERCIAL-P31" in user_prompt
    # Tools usadas atribuídas ao agente certo.
    assert "transferir_agente" in user_prompt
    assert "save_contact_info" in user_prompt
    # Histórico escopado à conversa marcada — o outro canal não vaza.
    assert "quero comprar" in user_prompt
    assert "MENSAGEM-DO-CANAL-DEFAULT" not in user_prompt
    # Limpeza: agentes de teste fora do caminho dos demais testes.
    built.client.delete("/api/ai/agents/p31_roteador")
    built.client.delete("/api/ai/agents/p31_comercial")
    built.client.delete("/api/ai/variables/p31_var")
