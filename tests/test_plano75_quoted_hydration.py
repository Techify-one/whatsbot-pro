"""Plano 75 F10 — citação cujo alvo está FORA da página deixa de sumir (bug C2).

O painel resolvia a citação client-side, varrendo só a janela já carregada
(``ContactDetail.findQuoted``). Com paginação keyset (plano 50), uma resposta que
cita uma mensagem de dias antes nunca resolvia e virava "Mensagem original
indisponível" — mesmo com o dado 100% correto no banco (caso real: msg 633432 →
626303, mesma conversa, `msg_id` idêntico com prefixo ``WAID:``).

O conserto hidrata o alvo no payload da página (1 query em lote, índice
``idx_msg_id`` já existente — zero DDL). Estes testes travam:

* o alvo fora da página volta em ``messages[].quoted`` (com o trecho truncado);
* o alvo DENTRO da página não hidrata nada (payload não cresce à toa — risco R11);
* alvo inexistente não quebra e não inventa ``quoted``;
* alvo em OUTRA conversa NÃO vaza (escopo obrigatório na query);
* ``msg_id`` com prefixo ``WAID:`` (301k linhas importadas do Chatwoot) casa por
  IGUALDADE EXATA e a forma sem prefixo NÃO casa (F.P.#7 / R12 — proibido normalizar).

    venv/bin/python -m pytest tests/test_plano75_quoted_hydration.py -q

NOTA SOBRE AUTENTICAÇÃO (robustez na suíte agregada)
----------------------------------------------------
O banco de teste é COMPARTILHADO pelo processo inteiro e o middleware de auth
liga sozinho assim que existe ≥1 usuário RBAC (``enforce = rbac_enforced() or
has_users`` em ``server/app.py``). Rodando isolado este arquivo vive em modo
aberto; rodando junto com um teste que cria usuário, toda rota ``/api/*`` passa a
responder 401. Por isso as requisições vão por :func:`_get`, que faz a chamada
NORMAL e só autentica **se** vier 401 — nunca antes. Autenticar sempre criaria
usuário num ambiente aberto e LIGARIA o RBAC para os testes seguintes, ou seja,
este arquivo passaria a causar exatamente a poluição de que sofre.
"""

from __future__ import annotations

import time

import pytest

from db.repositories import contact_repo, conversation_repo, message_repo

# Id no formato importado do Chatwoot (prefixo WAID:) — de propósito: é a forma do
# caso real de produção e trava a regra "casamento por igualdade exata".
TARGET_MSG_ID = "WAID:wamid.HBgNNTUxMTk5OTk5OTk5ORUCABIYFjNFQjA3Rjc3RUVFMkJCREIzODlFNjY="
TARGET_BODY = (
    "CLIENTE EXEMPLO — contrato 000000, plano 600 MEGA, vencimento dia 10, "
    "endereço Rua Exemplo 100, ponto de referência ao lado da praça"
)
FILLER_COUNT = 12


def _mk_conversation(phone: str) -> tuple[int, int]:
    """Contato + conversa. Devolve ``(contact_id, conversation_id)``."""
    contact = contact_repo.get_or_create(phone)
    conv, _created = conversation_repo.resolve_for_contact_ex(
        contact["id"], f"{phone}@s.whatsapp.net")
    return contact["id"], conv["id"]


@pytest.fixture
def thread_two_pages(seed):
    """Uma conversa com histórico suficiente para 2 páginas.

    Ordem: [alvo da citação] → 12 mensagens de enchimento → [resposta que cita].
    Com ``limit=5`` o alvo fica fora da página mais recente.
    """
    contact_id, conv_id = _mk_conversation("5511900000751")
    base = time.time() - 3600
    target = message_repo.add(
        contact_id, "user", TARGET_BODY, msg_id=TARGET_MSG_ID,
        conversation_id=conv_id, ts=base)
    for i in range(FILLER_COUNT):
        message_repo.add(contact_id, "assistant", f"enchimento {i}",
                         msg_id=f"filler-{conv_id}-{i}", status="operator",
                         conversation_id=conv_id, ts=base + 1 + i)
    reply = message_repo.add(
        contact_id, "assistant", "vou verificar esse contrato",
        msg_id=f"reply-{conv_id}", status="operator",
        reply_to_msg_id=TARGET_MSG_ID, conversation_id=conv_id,
        ts=base + 100)
    return {"contact_id": contact_id, "conv_id": conv_id,
            "target_id": target["id"], "reply_id": reply["id"]}


# ── Auth de contingência (só usada quando a suíte já ligou o RBAC) ─────────

_AUTH_EMAIL = "p75_quoted_hydration@test.com"
_AUTH_PASSWORD = "supersecret"
# Permissões mínimas para ler a página de mensagens de qualquer inbox: o
# ``read_all`` evita que o escopo por membership (o usuário não é membro de
# inbox nenhum) transforme o 401 num 404 e mascare o que está sendo testado.
_AUTH_PERMISSIONS = ["conversation.read", "conversation.read_all", "contact.read"]

# Cache de processo: o token vive na tabela ``sessions`` (banco compartilhado),
# então serve para qualquer app construído depois.
_auth_headers: dict[str, str] | None = None


def _authenticate(client) -> dict[str, str]:
    """Cria (idempotente) o usuário de leitura e devolve o header ``Bearer``.

    Chamada APENAS depois de um 401 — ver a nota no topo do módulo.
    """
    global _auth_headers
    if _auth_headers is not None:
        return _auth_headers

    from db.repositories import user_repo
    from server.auth import hash_password_argon2

    if user_repo.get_by_email(_AUTH_EMAIL) is None:
        user_repo.create(
            email=_AUTH_EMAIL, name="Plano75 Quoted",
            password_hash=hash_password_argon2(_AUTH_PASSWORD),
            permission_keys=_AUTH_PERMISSIONS, custom=True)

    r = client.post("/api/auth/login",
                    json={"email": _AUTH_EMAIL, "password": _AUTH_PASSWORD})
    assert r.status_code == 200, r.text
    _auth_headers = {"Authorization": f"Bearer {r.json()['data']['token']}"}
    return _auth_headers


def _get(client, url: str):
    """GET normal; só se voltar 401 (RBAC ligado por outro teste) repete logado."""
    r = client.get(url)
    if r.status_code == 401:
        r = client.get(url, headers=_authenticate(client))
    return r


def _messages(client, conv_id: int, **params) -> list[dict]:
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    r = _get(client, f"/api/atendimentos/{conv_id}/messages?{qs}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, body
    return body["data"]["messages"]


def _by_id(msgs: list[dict], mid: int) -> dict:
    for m in msgs:
        if m.get("_id") == mid:
            return m
    raise AssertionError(f"mensagem {mid} não veio na página: {[m.get('_id') for m in msgs]}")


def test_target_outside_page_comes_hydrated(client, thread_two_pages):
    """O caso do print: página recente sem o alvo ⇒ o servidor manda o ``quoted``."""
    env = thread_two_pages
    msgs = _messages(client, env["conv_id"], limit=5, mark_read="false")
    assert len(msgs) == 5, "página keyset com 5 linhas"
    assert env["target_id"] not in [m.get("_id") for m in msgs], \
        "pré-condição: o alvo TEM que estar fora da página"

    reply = _by_id(msgs, env["reply_id"])
    quoted = reply.get("quoted")
    assert quoted, "a resposta precisa vir com a citação hidratada"
    assert quoted["msg_id"] == TARGET_MSG_ID
    assert quoted["_id"] == env["target_id"]
    assert quoted["role"] == "user"
    assert TARGET_BODY.startswith(quoted["content"]), "o trecho é o começo do original"
    assert quoted["content"], "conteúdo não pode vir vazio (era o bug)"


def test_snippet_is_truncated_and_light(client, thread_two_pages):
    """Risco R11: o payload não pode carregar a mensagem inteira nem caminho de mídia."""
    env = thread_two_pages
    msgs = _messages(client, env["conv_id"], limit=5, mark_read="false")
    quoted = _by_id(msgs, env["reply_id"])["quoted"]
    assert len(TARGET_BODY) > message_repo.QUOTED_SNIPPET_MAX, "fixture precisa estourar o teto"
    assert len(quoted["content"]) == message_repo.QUOTED_SNIPPET_MAX
    assert "media_path" not in quoted and "raw" not in quoted
    assert set(quoted) == {"_id", "msg_id", "role", "content", "media_type",
                           "status", "sent_by_name"}


def test_target_inside_page_is_not_hydrated(client, thread_two_pages):
    """Alvo na própria página ⇒ o frontend resolve sozinho, nada é anexado."""
    env = thread_two_pages
    msgs = _messages(client, env["conv_id"], limit=100, mark_read="false")
    assert env["target_id"] in [m.get("_id") for m in msgs]
    assert "quoted" not in _by_id(msgs, env["reply_id"])
    assert all("quoted" not in m for m in msgs), \
        "nenhuma linha sem citação pode ganhar o campo"


def test_missing_target_does_not_invent_quote(client, seed):
    """Alvo apagado / nunca recebido: sem ``quoted`` (o painel segue mostrando
    'Mensagem original indisponível') e sem erro."""
    contact_id, conv_id = _mk_conversation("5511900000752")
    reply = message_repo.add(
        contact_id, "assistant", "resposta órfã", msg_id=f"orphan-{conv_id}",
        status="operator", reply_to_msg_id="WAID:wamid.NAO-EXISTE",
        conversation_id=conv_id)
    msgs = _messages(client, conv_id, limit=5, mark_read="false")
    assert "quoted" not in _by_id(msgs, reply["id"])


def test_other_conversation_does_not_leak(client, seed):
    """Escopo obrigatório: mesmo ``msg_id`` em outra conversa NÃO é hidratado."""
    other_contact, other_conv = _mk_conversation("5511900000753")
    leaked_id = "WAID:wamid.SEGREDO-DE-OUTRA-CONVERSA"
    message_repo.add(other_contact, "user", "dados sigilosos do outro cliente",
                     msg_id=leaked_id, conversation_id=other_conv)

    contact_id, conv_id = _mk_conversation("5511900000754")
    reply = message_repo.add(
        contact_id, "assistant", "resposta", msg_id=f"reply-{conv_id}",
        status="operator", reply_to_msg_id=leaked_id, conversation_id=conv_id)

    msgs = _messages(client, conv_id, limit=5, mark_read="false")
    assert "quoted" not in _by_id(msgs, reply["id"]), \
        "citação de outra conversa jamais pode vazar para o painel"


def test_waid_prefix_matches_exactly(seed):
    """F.P.#7 / R12: casa com o id EXATO; a forma sem o prefixo não casa."""
    contact_id, conv_id = _mk_conversation("5511900000755")
    message_repo.add(contact_id, "user", "original importado do Chatwoot",
                     msg_id=TARGET_MSG_ID, conversation_id=conv_id)

    hit = message_repo.get_by_msg_ids([TARGET_MSG_ID], conversation_id=conv_id)
    assert TARGET_MSG_ID in hit
    assert hit[TARGET_MSG_ID]["content"] == "original importado do Chatwoot"

    stripped = TARGET_MSG_ID.split("WAID:", 1)[1]
    assert message_repo.get_by_msg_ids([stripped], conversation_id=conv_id) == {}, \
        "normalizar o prefixo WAID: é proibido — quebraria as citações que funcionam"


def test_batch_lookup_requires_a_scope(seed):
    """Sem escopo a query varreria o banco inteiro: erro de programação explícito."""
    with pytest.raises(ValueError):
        message_repo.get_by_msg_ids([TARGET_MSG_ID])


def test_batch_lookup_edge_cases(seed):
    """Lista vazia / ids falsy não vão ao banco; ids desconhecidos somem do mapa."""
    _contact_id, conv_id = _mk_conversation("5511900000756")
    assert message_repo.get_by_msg_ids([], conversation_id=conv_id) == {}
    assert message_repo.get_by_msg_ids([None, ""], conversation_id=conv_id) == {}
    assert message_repo.get_by_msg_ids(["nao-existe"], conversation_id=conv_id) == {}
