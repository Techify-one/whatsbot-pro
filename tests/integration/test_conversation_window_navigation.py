"""Plano 99 — janela ANCORADA, busca na conversa e "ir para data".

Este arquivo nasceu na **Fase F0a** como CARACTERIZAÇÃO do bug de produção
descrito na §2.4 do plano: pular para uma mensagem fora da janela carregada
falha em silêncio. A causa raiz é do backend — a paginação da thread é
**unidirecional** (só ``before_id``), então não existe forma de pedir "a janela
em torno desta mensagem". Sem isso o cliente só pode cascatear ``loadOlder`` de
50 em 50 e, quando o alvo chega, a flag ``justPrepended`` come a tentativa de
foco (bug de UI, coberto em ``web/static/js/services/threadJump.test.js``).

Os testes abaixo asseveram o lado NOVO (pós F0b/F0c/F1/F3). Rodando contra o
código anterior ao plano 99, os de ``around_id``/``after_id``/``search``/``at_ts``
falham — era exatamente esse o ponto da F0a.

    venv/bin/python -m pytest tests/integration/test_conversation_window_navigation.py -q
"""

from __future__ import annotations

import time
import uuid

import pytest

from db.repositories import contact_repo, conversation_repo, message_repo


PAGE = 50


@pytest.fixture
def long_thread(seed):
    """Conversa com 130 mensagens (> 2 páginas de 50). Devolve ``(conv_id, ids)``.

    ``ids`` é a lista de PKs em ordem CRONOLÓGICA (a [0] é a mais antiga), que é
    a ordem em que o painel as renderiza.
    """
    phone = f"5511{uuid.uuid4().int % 100000000:08d}"
    contact = contact_repo.get_or_create(phone)
    conv = conversation_repo.resolve_for_contact(contact["id"], f"{phone}@s.whatsapp.net")
    conv_id = conv["id"] if isinstance(conv, dict) else conv
    base = time.time() - 130 * 60
    ids: list[int] = []
    for i in range(130):
        row = message_repo.add(
            contact["id"],
            "user" if i % 2 == 0 else "assistant",
            f"mensagem número {i}",
            conversation_id=conv_id,
            ts=base + i * 60,
        )
        ids.append(row["id"])
    return conv_id, ids, phone, contact["id"]


@pytest.fixture
def out_of_order_thread(seed):
    """Thread cuja ordem de inserção/PK diverge deliberadamente de ``(ts, id)``."""
    phone = f"5511{uuid.uuid4().int % 100000000:08d}"
    contact = contact_repo.get_or_create(phone)
    conv = conversation_repo.resolve_for_contact(contact["id"], f"{phone}@s.whatsapp.net")
    conv_id = conv["id"] if isinstance(conv, dict) else conv
    base = time.time() - 3600
    inserted = []
    for label, offset in (
        ("a", 10), ("b", 50), ("c", 20),
        ("d", 40), ("e", 30), ("f", 30),
    ):
        row = message_repo.add(
            contact["id"], "user", f"fora de ordem {label}",
            conversation_id=conv_id, ts=base + offset,
        )
        inserted.append((row["id"], base + offset))
    chronological = [row_id for row_id, _ in
                     sorted(inserted, key=lambda item: (item[1], item[0]))]
    return conv_id, chronological


def _msgs(client, conv_id: int, qs: str = "") -> dict:
    r = client.get(f"/api/atendimentos/{conv_id}/messages?mark_read=false{qs}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, body
    return body["data"]


# ── F0a/F0c — a janela ancorada ──────────────────────────────────────────────

def test_pagina_recente_inalterada(client, long_thread):
    """Não-regresso: sem parâmetro nenhum, a página mais recente, cronológica."""
    conv_id, ids, _, _ = long_thread
    data = _msgs(client, conv_id)
    got = [m["_id"] for m in data["messages"]]
    assert got == ids[-PAGE:], "a página default deixou de ser as 50 mais recentes"
    assert data["has_more"] is True
    # Aliases novos, sem quebrar o antigo.
    assert data["has_more_older"] is True
    assert data["has_more_newer"] is False, "a página recente termina na última msg"


def test_before_id_byte_identico(client, long_thread):
    """O caminho quente de hoje (scroll-up) não pode mudar."""
    conv_id, ids, _, _ = long_thread
    first_page = _msgs(client, conv_id)["messages"]
    older = _msgs(client, conv_id, f"&before_id={first_page[0]['_id']}")
    assert [m["_id"] for m in older["messages"]] == ids[-2 * PAGE:-PAGE]
    assert older["has_more"] is True
    assert older["has_more_newer"] is True, "há mensagens mais recentes que esta janela"


def test_around_id_centra_a_janela_no_alvo(client, long_thread):
    """⚠️ O CORAÇÃO DA F0a — falha antes do plano 99 (``around_id`` inexistente).

    Pedir a janela em torno da 5ª mensagem (bem fora da página inicial) tem de
    devolver uma janela que CONTÉM o alvo, com contexto dos dois lados.
    """
    conv_id, ids, _, _ = long_thread
    target = ids[4]
    data = _msgs(client, conv_id, f"&around_id={target}")
    got = [m["_id"] for m in data["messages"]]
    assert target in got, (
        "a janela ancorada NÃO contém o alvo — é o bug do salto silencioso: "
        "sem around_id o servidor devolve a página mais recente e o cliente "
        "nunca consegue focar a mensagem")
    assert len(got) <= PAGE
    assert got == sorted(got), "a janela tem de sair cronológica"
    # O alvo está perto do começo do histórico ⇒ não há muito antes, mas há depois.
    assert data["has_more_newer"] is True


def test_around_id_no_meio_traz_contexto_dos_dois_lados(client, long_thread):
    conv_id, ids, _, _ = long_thread
    target = ids[65]
    data = _msgs(client, conv_id, f"&around_id={target}")
    got = [m["_id"] for m in data["messages"]]
    assert target in got
    pos = got.index(target)
    assert pos > 0, "faltou contexto ANTES do alvo"
    assert pos < len(got) - 1, "faltou contexto DEPOIS do alvo"
    assert data["has_more_older"] is True and data["has_more_newer"] is True


def test_after_id_traz_as_mais_recentes_em_ordem(client, long_thread):
    conv_id, ids, _, _ = long_thread
    data = _msgs(client, conv_id, f"&after_id={ids[0]}&limit=10")
    got = [m["_id"] for m in data["messages"]]
    assert got == ids[1:11], "after_id deve devolver as N SEGUINTES, cronológicas"
    assert data["has_more_newer"] is True


def test_after_id_no_fim_esgota(client, long_thread):
    conv_id, ids, _, _ = long_thread
    data = _msgs(client, conv_id, f"&after_id={ids[-3]}")
    assert [m["_id"] for m in data["messages"]] == ids[-2:]
    assert data["has_more_newer"] is False


def test_ancoras_mutuamente_exclusivas(client, long_thread):
    conv_id, ids, _, _ = long_thread
    r = client.get(f"/api/atendimentos/{conv_id}/messages"
                   f"?before_id={ids[10]}&around_id={ids[5]}")
    assert r.status_code == 400, "combinar duas âncoras tem de ser erro explícito"


def test_ancora_inexistente_nao_quebra(client, long_thread):
    conv_id, _, _, _ = long_thread
    data = _msgs(client, conv_id, "&around_id=999999999")
    assert data["messages"] == [] or isinstance(data["messages"], list)


def test_abertura_ancorada_nao_marca_como_lida(client, long_thread):
    """P6 — pular para janeiro não pode zerar o badge de hoje."""
    conv_id, ids, _, _ = long_thread
    r = client.get(f"/api/atendimentos/{conv_id}/messages?around_id={ids[4]}")
    assert r.status_code == 200, r.text
    body = r.json()["data"]
    # A resposta declara que NÃO marcou (o cliente não precisa adivinhar).
    assert body.get("marked_read") is False


# ── F1 — busca escopada à conversa ───────────────────────────────────────────

def _search(client, conv_id: int, q: str, qs: str = ""):
    r = client.get(f"/api/atendimentos/{conv_id}/messages/search?q={q}{qs}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, body
    return body["data"]


def test_busca_na_conversa_acha_ocorrencias(client, long_thread):
    conv_id, ids, _, _ = long_thread
    data = _search(client, conv_id, "mensagem")
    assert data["total"] == 130, f"esperava as 130 mensagens; veio {data['total']}"
    assert len(data["matches"]) <= 50
    first = data["matches"][0]
    assert {"id", "ts", "role", "snippet"} <= set(first), first
    # Ordem: mais recente primeiro (é o que o WhatsApp faz).
    assert first["id"] == ids[-1]


def test_busca_acento_e_caixa(client, seed):
    """``joao`` acha ``João``; ``ORÇAMENTO`` acha ``orçamento``."""
    phone = f"5511{uuid.uuid4().int % 100000000:08d}"
    contact = contact_repo.get_or_create(phone)
    conv = conversation_repo.resolve_for_contact(contact["id"], f"{phone}@s.whatsapp.net")
    conv_id = conv["id"] if isinstance(conv, dict) else conv
    message_repo.add(contact["id"], "user", "Falei com o João ontem",
                     conversation_id=conv_id)
    message_repo.add(contact["id"], "user", "segue o orçamento anexo",
                     conversation_id=conv_id)
    assert _search(client, conv_id, "joao")["total"] == 1
    assert _search(client, conv_id, "ORÇAMENTO")["total"] == 1


def test_busca_ignora_roles_internos(client, seed):
    phone = f"5511{uuid.uuid4().int % 100000000:08d}"
    contact = contact_repo.get_or_create(phone)
    conv = conversation_repo.resolve_for_contact(contact["id"], f"{phone}@s.whatsapp.net")
    conv_id = conv["id"] if isinstance(conv, dict) else conv
    message_repo.add(contact["id"], "tool_call", "segredo interno xyzzy",
                     conversation_id=conv_id)
    message_repo.add(contact["id"], "system_notice", "aviso xyzzy",
                     conversation_id=conv_id)
    message_repo.add(contact["id"], "user", "visível xyzzy", conversation_id=conv_id)
    data = _search(client, conv_id, "xyzzy")
    assert data["total"] == 1, "tool_call/system_notice não podem aparecer na busca"
    assert data["matches"][0]["role"] == "user"


def test_busca_curta_devolve_vazio_sem_erro(client, long_thread):
    conv_id, _, _, _ = long_thread
    data = _search(client, conv_id, "me")
    assert data["total"] == 0 and data["matches"] == []


# ── F3 — "ir para data" (at_ts no próprio endpoint, P2·b) ────────────────────

def test_at_ts_aterrissa_no_dia(client, long_thread):
    """``at_ts`` resolve o 1º id com ``ts >= X`` e devolve a JANELA em torno dele."""
    conv_id, ids, _, _ = long_thread
    rows = message_repo.get_by_conversation(conv_id)
    target_ts = rows[10]["ts"]
    data = _msgs(client, conv_id, f"&at_ts={int(target_ts)}")
    got = [m["_id"] for m in data["messages"]]
    assert data["anchor_id"] == rows[10]["_id"]
    assert data["anchor_id"] in got


def test_at_ts_antes_do_inicio_cai_na_primeira(client, long_thread):
    conv_id, ids, _, _ = long_thread
    data = _msgs(client, conv_id, "&at_ts=1")
    assert data["anchor_id"] == ids[0]


def test_at_ts_depois_do_fim_nao_acha(client, long_thread):
    conv_id, _, _, _ = long_thread
    data = _msgs(client, conv_id, f"&at_ts={int(time.time()) + 86400 * 30}")
    assert data["anchor_id"] is None, "sem mensagem depois da data ⇒ nada para ancorar"


# ── F0b·5 — o repo, direto (sem passar pela rota) ───────────────────────────

def test_repo_before_id_inalterado(long_thread):
    """Não-regresso do caminho quente (plano 50): a página mais recente."""
    conv_id, ids, _, _ = long_thread
    page = message_repo.get_by_conversation(conv_id, limit=10)
    assert [m["_id"] for m in page] == ids[-10:]
    older = message_repo.get_by_conversation(conv_id, limit=10, before_id=ids[-10])
    assert [m["_id"] for m in older] == ids[-20:-10]


def test_repo_after_id_devolve_cronologico(long_thread):
    conv_id, ids, _, _ = long_thread
    page = message_repo.get_by_conversation(conv_id, limit=10, after_id=ids[30])
    got = [m["_id"] for m in page]
    assert got == ids[31:41]
    assert got == sorted(got), "after_id deve sair cronológico como todo o resto"


def test_cursores_compostos_respeitam_timestamp_fora_da_ordem(client,
                                                               out_of_order_thread):
    """Regressão: PK maior não significa mensagem posterior em backfill/importação."""
    conv_id, chronological = out_of_order_thread
    anchor = chronological[2]

    before = message_repo.get_by_conversation(conv_id, limit=10, before_id=anchor)
    after = message_repo.get_by_conversation(conv_id, limit=10, after_id=anchor)
    assert [m["_id"] for m in before] == chronological[:2]
    assert [m["_id"] for m in after] == chronological[3:]

    win = message_repo.window_around(
        around_id=anchor, limit=5, conversation_id=conv_id,
    )
    assert [m["_id"] for m in win["messages"]] == chronological[:5]
    assert win["anchor_id"] == anchor

    data = _msgs(client, conv_id, f"&around_id={anchor}&limit=5")
    assert [m["_id"] for m in data["messages"]] == chronological[:5]
    assert data["anchor_id"] == anchor
    api_before = _msgs(client, conv_id, f"&before_id={anchor}&limit=10")
    api_after = _msgs(client, conv_id, f"&after_id={anchor}&limit=10")
    assert [m["_id"] for m in api_before["messages"]] == chronological[:2]
    assert [m["_id"] for m in api_after["messages"]] == chronological[3:]


def test_repo_window_around_centra(long_thread):
    conv_id, ids, _, _ = long_thread
    win = message_repo.window_around(around_id=ids[64], limit=20, conversation_id=conv_id)
    got = [m["_id"] for m in win["messages"]]
    assert len(got) == 20
    assert ids[64] in got
    assert got.index(ids[64]) in (9, 10), "a âncora deveria ficar no meio da janela"
    assert win["anchor_id"] == ids[64]
    assert win["has_more_older"] and win["has_more_newer"]


def test_repo_window_around_na_primeira_mensagem(long_thread):
    conv_id, ids, _, _ = long_thread
    win = message_repo.window_around(around_id=ids[0], limit=20, conversation_id=conv_id)
    got = [m["_id"] for m in win["messages"]]
    assert got[0] == ids[0], "a mais antiga não tem contexto antes dela"
    assert win["has_more_older"] is False
    assert win["has_more_newer"] is True


def test_repo_window_around_na_ultima_mensagem(long_thread):
    conv_id, ids, _, _ = long_thread
    win = message_repo.window_around(around_id=ids[-1], limit=20, conversation_id=conv_id)
    got = [m["_id"] for m in win["messages"]]
    assert got[-1] == ids[-1]
    assert win["has_more_newer"] is False
    assert win["has_more_older"] is True


def test_repo_window_around_ancora_de_outra_conversa(long_thread, seed):
    """Âncora que não pertence à thread degrada para a página recente, não erro."""
    conv_id, ids, _, _ = long_thread
    outra = f"5511{uuid.uuid4().int % 100000000:08d}"
    c2 = contact_repo.get_or_create(outra)
    conv2 = conversation_repo.resolve_for_contact(c2["id"], f"{outra}@s.whatsapp.net")
    conv2_id = conv2["id"] if isinstance(conv2, dict) else conv2
    intrusa = message_repo.add(c2["id"], "user", "de outra thread",
                               conversation_id=conv2_id)["id"]
    win = message_repo.window_around(around_id=intrusa, limit=20, conversation_id=conv_id)
    assert win["anchor_id"] is None, "não pode ancorar em mensagem de outra conversa"
    assert intrusa not in [m["_id"] for m in win["messages"]], "vazou conteúdo de outra thread"
    assert [m["_id"] for m in win["messages"]] == ids[-20:]


def test_repo_first_id_on_or_after(long_thread):
    conv_id, ids, _, _ = long_thread
    rows = message_repo.get_by_conversation(conv_id)
    alvo = rows[42]
    # Exatamente no ts da mensagem: ela mesma (o ">=" é deliberado).
    assert message_repo.first_id_on_or_after(
        alvo["ts"], conversation_id=conv_id) == alvo["_id"]
    # Um segundo depois: a próxima.
    assert message_repo.first_id_on_or_after(
        alvo["ts"] + 1, conversation_id=conv_id) == rows[43]["_id"]
    # Antes de tudo: a primeira.
    assert message_repo.first_id_on_or_after(1, conversation_id=conv_id) == ids[0]
    # Depois de tudo: nada.
    assert message_repo.first_id_on_or_after(
        time.time() + 86400, conversation_id=conv_id) is None


def test_repo_conversa_vazia(seed):
    phone = f"5511{uuid.uuid4().int % 100000000:08d}"
    contact = contact_repo.get_or_create(phone)
    conv = conversation_repo.resolve_for_contact(contact["id"], f"{phone}@s.whatsapp.net")
    conv_id = conv["id"] if isinstance(conv, dict) else conv
    win = message_repo.window_around(around_id=1, limit=20, conversation_id=conv_id)
    assert win["messages"] == [] and win["anchor_id"] is None
    assert message_repo.first_id_on_or_after(0, conversation_id=conv_id) is None
    assert message_repo.get_by_conversation(conv_id, limit=5, after_id=1) == []
