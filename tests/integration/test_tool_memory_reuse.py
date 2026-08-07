"""Plano 47 — reaproveitar o RESULTADO de uma tool para a IA entre turnos.

Cobre a reinjeção do ``→ resultado`` no bloco ``agent.tool_memory.build_block``
para as tools marcadas com ``reuse_result`` (nova coluna em ``tool_overrides``):
default-OFF byte-idêntico, reuse-ON, cap global configurável + scrub, degradação
(só o mais recente por tool), re-aplicação da lista-negra de histórico (D7),
uniformidade sem guarda (D8) e o kill-switch ``ai_tool_memory_enabled``.

    venv/bin/python -m pytest tests/integration/test_tool_memory_reuse.py -q
"""

from __future__ import annotations

import pytest

from agent import history_filter, tool_memory
from db.repositories import (
    config_repo, contact_repo, conversation_repo, message_repo, tool_override_repo,
)

_WRENCH = "\U0001f527"


class _Contact:
    """Minimal ContactMemory stand-in (id + inbox_id drive the scoped resolver)."""

    def __init__(self, cid: int, inbox_id: int | None):
        self.id = cid
        self.inbox_id = inbox_id


def _mk(phone: str):
    """Contato com conversa aberta; devolve (contact_id, conv, _Contact)."""
    c = contact_repo.get_or_create(phone)
    conversation_repo.resolve_for_contact(
        c["id"], f"{phone}@s.whatsapp.net", reopen_if_closed=True)
    conv = conversation_repo.get_open_for_contact(c["id"])
    return c["id"], conv, _Contact(c["id"], conv["inbox_id"])


def _card(cid: int, conv_id: int, name: str, args_lines, result):
    """Grava um card ``tool_call`` no formato do painel (🔧 nome / args / → result)."""
    body = f"{_WRENCH} {name}"
    for a in args_lines:
        body += f"\n{a}"
    if result is not None:
        body += f"\n{tool_memory._ARROW} {result}"
    message_repo.add(cid, "tool_call", body, conversation_id=conv_id)


def _mark(name: str, on: bool = True):
    """Registra a row de override e liga/desliga o reuse_result da tool."""
    tool_override_repo.ensure(name, None)
    tool_override_repo.upsert_override(name, reuse_result=on)


@pytest.fixture(autouse=True)
def _reset_env(_engine_ready):
    """Cada teste começa com a memória ON, sem cap custom e sem lista-negra."""
    config_repo.set("ai_tool_memory_enabled", True)
    config_repo.set("ai_tool_reuse_result_max_chars", 800)
    config_repo.set("ai_history_exclude_patterns", [])
    history_filter.reset_cache()
    yield
    config_repo.set("ai_tool_memory_enabled", True)
    config_repo.set("ai_history_exclude_patterns", [])
    history_filter.reset_cache()


def test_default_off_is_byte_identical():
    """Caracterização (D2): sem a tool marcada, o bloco é idêntico ao legado —
    só nome+args, NENHUMA linha de resultado (nem o glifo →)."""
    cid, conv, contact = _mk("5511470000001")
    _card(cid, conv["id"], "minha_tool", ["arg: 1"], "resultado secreto")
    # Garante que a tool NÃO está marcada (default 0).
    tool_override_repo.ensure("minha_tool", None)

    block = tool_memory.build_block(contact)
    expected = (
        "[Memória deste atendimento — NÃO repita ações já concluídas abaixo]\n"
        "Ferramentas já executadas neste atendimento (não chame de novo com os "
        "mesmos dados): minha_tool(arg: 1)."
    )
    assert block == expected
    assert "resultado secreto" not in block
    assert tool_memory._ARROW not in block
    assert "Resultados que você JÁ TEM" not in block


def test_reuse_on_includes_result():
    """Reuse ligado: o bloco ganha a seção 'Resultados que você JÁ TEM' com o
    resultado da tool marcada."""
    cid, conv, contact = _mk("5511470000002")
    _mark("pesquisar_perguntas_frequentes")
    _card(cid, conv["id"], "pesquisar_perguntas_frequentes", ["termo: mec"],
          "Sim, o curso é reconhecido pelo MEC (portaria 123).")

    block = tool_memory.build_block(contact)
    assert "Resultados que você JÁ TEM (use-os, não re-consulte):" in block
    assert "reconhecido pelo MEC (portaria 123)" in block
    assert "pesquisar_perguntas_frequentes(termo: mec)" in block


def test_unmarked_tool_result_never_leaks():
    """Uma tool NÃO marcada nunca expõe o resultado, mesmo com outra marcada."""
    cid, conv, contact = _mk("5511470000003")
    _mark("faq")
    _card(cid, conv["id"], "faq", ["t: x"], "resposta da FAQ")
    _card(cid, conv["id"], "pesquisar_ofertas", ["t: black"], "1 oferta: SCRIPTS (O06)")

    block = tool_memory.build_block(contact)
    assert "resposta da FAQ" in block                 # marcada → resultado
    assert "SCRIPTS (O06)" not in block               # não marcada → só nome+args
    assert "pesquisar_ofertas(t: black)" in block


def test_cap_truncates_and_config_changes_cut():
    """O resultado é truncado no ``ai_tool_reuse_result_max_chars``; mudar o
    config muda o corte; o guard impede zerar."""
    cid, conv, contact = _mk("5511470000004")
    _mark("faq")
    big = ("palavra " * 600).strip()  # ~4800 chars, com espaços (não vira base64)
    _card(cid, conv["id"], "faq", ["t: z"], big)

    config_repo.set("ai_tool_reuse_result_max_chars", 200)
    b1 = tool_memory.build_block(contact)
    config_repo.set("ai_tool_reuse_result_max_chars", 500)
    b2 = tool_memory.build_block(contact)
    assert b1.count("palavra") < b2.count("palavra")

    # guard: 0/negativo cai em >=50 chars (não zera o resultado)
    config_repo.set("ai_tool_reuse_result_max_chars", 0)
    b3 = tool_memory.build_block(contact)
    assert "palavra" in b3

    # config inválido → fallback 800 (fail-open, sem crash)
    config_repo.set("ai_tool_reuse_result_max_chars", "lixo")
    b4 = tool_memory.build_block(contact)
    assert "palavra" in b4


def test_base64_is_scrubbed_from_result():
    """Um blob base64 gigante no resultado é scrubado antes de virar contexto."""
    cid, conv, contact = _mk("5511470000005")
    _mark("faq")
    blob = "A" * 300
    _card(cid, conv["id"], "faq", ["t: b"], f"dados: {blob}")

    block = tool_memory.build_block(contact)
    assert blob not in block
    assert "[…]" in block


def test_degradation_only_most_recent():
    """Duas chamadas da MESMA tool marcada ⇒ só a mais recente entra full."""
    cid, conv, contact = _mk("5511470000006")
    _mark("faq")
    _card(cid, conv["id"], "faq", ["termo: mec"], "portaria ANTIGA")
    _card(cid, conv["id"], "faq", ["termo: mec"], "portaria NOVA")

    block = tool_memory.build_block(contact)
    assert "portaria NOVA" in block
    assert "portaria ANTIGA" not in block
    # Mesmo sig (args idênticos) não deve aparecer duplicado em nome+args.
    assert block.count("faq(termo: mec)") == 1


def test_degradation_different_args_keeps_older_as_name_args():
    """Chamada antiga com args DIFERENTES sobrevive como nome+args na seção B."""
    cid, conv, contact = _mk("5511470000007")
    _mark("faq")
    _card(cid, conv["id"], "faq", ["termo: mec"], "resposta MEC")
    _card(cid, conv["id"], "faq", ["termo: preco"], "resposta PRECO")

    block = tool_memory.build_block(contact)
    assert "resposta PRECO" in block          # mais recente → full na seção A
    assert "resposta MEC" not in block         # antiga → sem resultado
    assert "faq(termo: mec)" in block          # antiga sobrevive como nome+args


def test_history_filter_cuts_marked_result():
    """D7: um resultado que casa ``ai_history_exclude_patterns`` tem o resultado
    cortado mesmo com a tool marcada (cai pra nome+args)."""
    cid, conv, contact = _mk("5511470000008")
    _mark("abrir_protocolo")
    _card(cid, conv["id"], "abrir_protocolo", ["assunto: x"],
          "Protocolo aberto PROT-12345678")

    config_repo.set("ai_history_exclude_patterns", [r"PROT-\d{8}"])
    history_filter.reset_cache()
    block = tool_memory.build_block(contact)
    assert "PROT-12345678" not in block
    assert "Resultados que você JÁ TEM" not in block
    assert "abrir_protocolo(assunto: x)" in block


def test_blacklisted_most_recent_does_not_resurrect_older():
    """D6+D7 (regressão da review): se a ocorrência MAIS RECENTE de uma tool
    marcada é cortada pela lista-negra, ela cai pra nome+args — NUNCA ressuscita
    o resultado de uma ocorrência ANTERIOR (que traria contexto stale)."""
    cid, conv, contact = _mk("5511470000011")
    _mark("consultar_pedido")
    _card(cid, conv["id"], "consultar_pedido", ["id: 123"],
          "Pedido 123: em processamento")            # antigo, NÃO casa a lista-negra
    _card(cid, conv["id"], "consultar_pedido", ["id: 123"],
          "Pedido 123: PROT-45678901 aberto")         # recente, CASA a lista-negra

    config_repo.set("ai_history_exclude_patterns", [r"PROT-\d{8}"])
    history_filter.reset_cache()
    block = tool_memory.build_block(contact)
    assert "em processamento" not in block             # não ressuscita o antigo
    assert "PROT-45678901" not in block                # o recente foi cortado
    assert "Resultados que você JÁ TEM" not in block
    assert "consultar_pedido(id: 123)" in block        # degrada pra nome+args


def test_uniform_no_guard_side_effecting_tool():
    """D8: marcar uma tool que executa ação (ex.: transferir_agente) PERSISTE
    (sem guarda) e o resultado é reinjetado — confirma a ausência de trava."""
    cid, conv, contact = _mk("5511470000009")
    _mark("transferir_agente")
    assert "transferir_agente" in tool_override_repo.reuse_enabled_names()
    _card(cid, conv["id"], "transferir_agente", ["destino: comercial"],
          "Transferido para comercial")

    block = tool_memory.build_block(contact)
    assert "Transferido para comercial" in block


def test_kill_switch_off_disables_reuse():
    """Kill-switch: ``ai_tool_memory_enabled=False`` ⇒ nenhum bloco (nem reuse)."""
    cid, conv, contact = _mk("5511470000010")
    _mark("faq")
    _card(cid, conv["id"], "faq", ["t: x"], "resposta que não deve vazar")

    config_repo.set("ai_tool_memory_enabled", False)
    assert tool_memory.build_block(contact) is None
