"""Busca `q` resolvida NO SQL + paginação real (plano 62 · Fase F5).

Complementa a caracterização da F0
(``tests/integration/characterization/test_busca_contatos_characterization.py``), que congela o comportamento
observável) cobrindo o que a F5 ADICIONA:

  * paginação com ``q`` agora acontece no banco (``LIMIT/OFFSET`` sobre a
    cláusula) — página 2 não repete a página 1, a união das páginas é o universo
    filtrado e ``total`` = COUNT no SQL, não ``len()`` de uma lista em memória;
  * ``include_messages=False`` desliga o ramo de conteúdo de mensagem (casa por
    nome/telefone/grupo/tag, e a linha não ganha ``match_snippet``);
  * fold acento/caixa nos DOIS sentidos, agora feito por
    ``f_unaccent(lower(x)) ILIKE …`` — inclui o caso que uma comparação ``LIKE``
    perderia sob a collation ``C`` dos clusters (busca ACENTUADA MAIÚSCULA);
  * tag acentuada, grupo por ``group_name``;
  * ``q`` de 1 caractere não aciona o ramo de conteúdo (``MIN_SCAN_QUERY_LEN``);
  * a busca por conteúdo não tem mais o teto de ``MESSAGE_SCAN_CAP`` mensagens
    recentes — uma mensagem ANTIGA continua encontrável.

Dataset com prefixo de telefone próprio deste arquivo (``77900065…`` — não-BR,
para ``br_phone_variants`` nunca colapsar os números), então nunca colide com o
seed do conftest nem com as outras suítes no banco compartilhado do processo.
Todas as asserções são RELATIVAS (filtradas por este prefixo).
"""

from __future__ import annotations

import time

import pytest


PREFIX = "77900065"

P_ANA = PREFIX + "01"       # "Ana Conceição" — nome acentuado + tag "Órfão"
P_BRUNO = PREFIX + "02"     # "Bruno Conceicao" — nome SEM acento
P_CARLA = PREFIX + "03"     # "Carla Zebrinha" — casa só por CONTEÚDO
P_GRUPO = PREFIX + "04"     # grupo, group_name "Equipe Comercial", name vazio
P_ANTIGO = PREFIX + "05"    # "Cliente Antigo" — conteúdo com ts MUITO antigo
P_SETE = PREFIX + "07"      # "Contato Sete" — telefone contém "7"
P_OITO = PREFIX + "20"      # "Contato Vinte" — o dígito "8" só existe no CONTEÚDO

# Contatos usados só para a paginação (nome comum → todos casam em "paginavel").
P_PAGE = [PREFIX + f"1{i}" for i in range(6)]


@pytest.fixture(scope="module")
def dataset(_engine_ready) -> dict:
    """Semeia o dataset deste arquivo uma vez por processo."""
    from db.repositories import contact_repo, message_repo, tag_repo

    base = time.time() - 9000.0
    data: dict = {"base": base}

    def make(phone: str, name: str = "", **fields) -> int:
        c = contact_repo.get_or_create(phone)
        if name or fields:
            contact_repo.update(c["id"], name=name, **fields)
        data[phone] = c["id"]
        return c["id"]

    ana = make(P_ANA, "Ana Conceição")
    message_repo.add(ana, "user", "bom dia", ts=base + 10)
    tag_repo.create("Órfão", "#123456")
    tag_repo.add_contact_tag(ana, "Órfão")

    make(P_BRUNO, "Bruno Conceicao")

    carla = make(P_CARLA, "Carla Zebrinha")
    message_repo.add(carla, "user", "o pedido tem coelhopluma pendente", ts=base + 20)

    make(P_GRUPO, "", is_group=1, group_name="Equipe Comercial")

    # Conteúdo casável numa mensagem ANTIGA: a busca legada varria só as
    # MESSAGE_SCAN_CAP mais recentes, então algo assim escapava. ts bem no
    # passado garante que ela nunca esteja entre as mais recentes do banco.
    antigo = make(P_ANTIGO, "Cliente Antigo")
    m_old = message_repo.add(
        antigo, "user", "contrato jabuticabex assinado", ts=1_000_000.0)
    data["antigo_msg_id"] = m_old["id"]

    sete = make(P_SETE, "Contato Sete")
    message_repo.add(sete, "user", "sem token", ts=base + 30)

    # "8" não aparece em NENHUM telefone/nome deste arquivo — só no conteúdo
    # desta mensagem. Alvo da guarda de 1 caractere.
    oito = make(P_OITO, "Contato Vinte")
    message_repo.add(oito, "user", "codigo 8 confirmado", ts=base + 40)

    # Seis contatos de nome "Paginavel NN" para exercitar LIMIT/OFFSET.
    for i, phone in enumerate(P_PAGE):
        cid = make(phone, f"Paginavel {i:02d}")
        message_repo.add(cid, "user", "linha de paginacao", ts=base + 100 + i)

    return data


def _page(q: str = "", archived: bool = False, **kw) -> dict:
    from db.repositories import contact_repo
    return contact_repo.list_contacts_page(q, archived, None, **kw)


def _phones(items: list[dict]) -> list[str]:
    return [c["phone"] for c in items]


# ── Paginação com q agora é SQL ──────────────────────────────────────────────

def test_pagination_with_q_is_sql_and_pages_do_not_overlap(dataset):
    """Páginas consecutivas com ``q`` são disjuntas e cobrem o universo filtrado.

    Antes da F5 a página era uma fatia Python da lista inteira; agora é
    ``LIMIT/OFFSET`` no banco. O contrato observável é o mesmo, então a prova é
    estrutural: sem repetição entre páginas e a união == a lista completa."""
    full = _page("paginavel", sort="name")
    full_phones = _phones(full["items"])
    assert len(full_phones) == len(P_PAGE)
    assert set(full_phones) == set(P_PAGE)

    page1 = _page("paginavel", limit=2, offset=0, sort="name")
    page2 = _page("paginavel", limit=2, offset=2, sort="name")
    page3 = _page("paginavel", limit=2, offset=4, sort="name")

    p1, p2, p3 = _phones(page1["items"]), _phones(page2["items"]), _phones(page3["items"])
    assert len(p1) == len(p2) == len(p3) == 2
    assert not set(p1) & set(p2)          # página 2 não repete a 1
    assert not set(p2) & set(p3)
    assert p1 + p2 + p3 == full_phones     # união ordenada == universo filtrado


def test_total_with_q_is_the_filtered_universe(dataset):
    """``total`` = COUNT (no SQL) da MESMA cláusula — o universo filtrado, não o
    tamanho da página. ``has_more`` acompanha."""
    page1 = _page("paginavel", limit=2, offset=0, sort="name")
    assert page1["total"] == len(P_PAGE)
    assert page1["has_more"] is True

    last = _page("paginavel", limit=2, offset=4, sort="name")
    assert last["total"] == len(P_PAGE)
    assert last["has_more"] is False

    beyond = _page("paginavel", limit=2, offset=99, sort="name")
    assert beyond["items"] == []
    assert beyond["has_more"] is False
    assert beyond["total"] == len(P_PAGE)


def test_pagination_with_q_does_not_shrink_page(dataset):
    """A página com ``q`` vem CHEIA (``limit`` linhas) enquanto houver resultado —
    o filtro corre no WHERE, então o LIMIT já conta só o que casou."""
    page = _page("paginavel", limit=4, offset=0, sort="name")
    assert len(page["items"]) == 4


# ── include_messages ─────────────────────────────────────────────────────────

def test_include_messages_false_skips_content_match(dataset):
    """``include_messages=False``: não casa por conteúdo de mensagem, mas continua
    casando por nome (e sem decorar a linha com ``match_snippet``)."""
    with_msgs = _page("coelhopluma")
    assert P_CARLA in _phones(with_msgs["items"])

    without = _page("coelhopluma", include_messages=False)
    assert P_CARLA not in _phones(without["items"])

    # ...e o mesmo contato continua encontrável pelo NOME com a flag desligada.
    by_name = _page("zebrinha", include_messages=False)
    row = next(c for c in by_name["items"] if c["phone"] == P_CARLA)
    assert "match_snippet" not in row
    assert "match_msg_id" not in row


def test_include_messages_false_affects_total_too(dataset):
    """A flag vale para o COUNT também (``total`` reflete a mesma cláusula)."""
    with_msgs = _page("coelhopluma", limit=10)
    without = _page("coelhopluma", limit=10, include_messages=False)
    assert with_msgs["total"] >= 1
    assert without["total"] == with_msgs["total"] - 1


# ── Fold acento/caixa nos dois sentidos (ILIKE + f_unaccent) ─────────────────

@pytest.mark.parametrize("q", ["conceicao", "conceição", "CONCEIÇÃO", "CoNcEiÇãO"])
def test_q_name_accent_and_case_both_directions(dataset, q):
    """Toda grafia de ``q`` encontra AMBOS os nomes (acentuado e não acentuado).

    O caso ``CONCEIÇÃO`` é o que uma comparação ``LIKE`` perderia: sob a
    collation ``C`` dos clusters, ``lower()`` não rebaixa letras acentuadas, então
    ``f_unaccent(lower('CONCEIÇÃO'))`` = ``'conCeiCAO'`` — só o ``ILIKE`` fecha."""
    phones = _phones(_page(q)["items"])
    assert P_ANA in phones      # "Ana Conceição"
    assert P_BRUNO in phones    # "Bruno Conceicao"


@pytest.mark.parametrize("q", ["orfao", "Órfão", "ÓRFÃO"])
def test_q_tag_accent_and_case(dataset, q):
    assert P_ANA in _phones(_page(q)["items"])


@pytest.mark.parametrize("q", ["comercial", "COMERCIAL", "Equipe Comercial"])
def test_q_group_name(dataset, q):
    """Grupo casa pelo ``group_name`` (a coluna ``contacts.name`` está vazia)."""
    assert P_GRUPO in _phones(_page(q)["items"])


# ── Guarda de 1 caractere ────────────────────────────────────────────────────

def test_single_char_q_does_not_trigger_content_branch(dataset):
    """``len(fold(q)) < MIN_SCAN_QUERY_LEN``: telefone ainda casa, conteúdo não.

    P_SETE casa pelo telefone (o prefixo tem "7"); P_OITO só casaria por conteúdo
    ("codigo 8"), e nenhum telefone/nome deste arquivo contém "8"."""
    assert P_SETE in _phones(_page("7")["items"])

    rows = _page("8")["items"]
    assert P_OITO not in _phones(rows)
    # plano 62 F5 (fix de perf): o ramo de conteúdo agora exige TRIGRAM_MIN_LEN=3
    # (o pg_trgm só poda com >=3 chars; com 2 o ILIKE degeneraria em full scan).
    # Com 2 caracteres ("o8") o conteúdo NÃO é pesquisado — P_OITO só apareceria
    # por conteúdo, então continua fora.
    assert P_OITO not in _phones(_page("o8")["items"])
    # Com 3+ caracteres o ramo de conteúdo entra e P_OITO casa por "codigo 8".
    hit = next(c for c in _page("codigo 8")["items"] if c["phone"] == P_OITO)
    assert "codigo 8" in hit["match_snippet"]


# ── Sem teto de mensagens recentes (MESSAGE_SCAN_CAP) ────────────────────────

def test_content_match_reaches_old_messages(dataset):
    """A busca por conteúdo cobre TODO o histórico (o scan legado parava nas
    ``MESSAGE_SCAN_CAP`` mensagens mais recentes ⇒ ~5 dias em produção)."""
    items = _page("jabuticabex")["items"]
    row = next(c for c in items if c["phone"] == P_ANTIGO)
    assert "jabuticabex" in row["match_snippet"]
    assert row["match_msg_id"] == dataset["antigo_msg_id"]


# ── Snippet só para as linhas da página ──────────────────────────────────────

def test_content_snippet_only_for_rows_matched_by_content(dataset):
    """Quem casou por nome/telefone/grupo/tag NÃO ganha ``match_snippet``; quem
    casou só por conteúdo ganha (semântica do ``elif`` pré-F5, preservada)."""
    items = _page("conceicao")["items"]
    ana = next(c for c in items if c["phone"] == P_ANA)
    assert "match_snippet" not in ana

    items = _page("coelhopluma")["items"]
    carla = next(c for c in items if c["phone"] == P_CARLA)
    assert "coelhopluma" in carla["match_snippet"]


# ── Metacaracteres de LIKE são literais ──────────────────────────────────────

def test_like_wildcards_in_q_are_literal(dataset):
    """Um ``%`` digitado pelo operador casa um ``%`` literal — não vira coringa
    (o filtro Python que a F5 substituiu usava ``in``, sem wildcards)."""
    ours = set(P_PAGE) | {P_ANA, P_BRUNO, P_CARLA, P_GRUPO, P_ANTIGO, P_SETE, P_OITO}
    assert not ours & set(_phones(_page("%")["items"]))
    assert not ours & set(_phones(_page("Ana%Conceicao")["items"]))
    assert not ours & set(_phones(_page("_na Conceicao")["items"]))
    # sanidade: sem o wildcard o mesmo prefixo casa
    assert P_ANA in _phones(_page("Ana Concei")["items"])
