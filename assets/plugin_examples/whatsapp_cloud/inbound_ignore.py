"""Regra de descarte da mensagem que a Meta entrega SEM conteúdo (plano 95).

A Cloud API às vezes entrega no webhook um item de ``messages[]`` que não tem
corpo nenhum — só o aviso de que a plataforma não conseguiu entregar o
conteúdo::

    {"from": "447974905044", "id": "wamid.…", "timestamp": "1785420528",
     "errors": [{"code": 131051, "title": "Message type unknown", …}],
     "type": "unsupported", "unsupported": {"type": "unknown"}}

Caso real: os códigos de verificação (2FA) que a Meta manda para um número que
está na API oficial. Sem corpo, sem mídia, sem template — nada a recuperar.
O core, que não tem como saber que aquilo não é fala de cliente, roda o
pipeline inteiro em cima disso (contato, não-lida, atendimento, protocolo, IA).

Este módulo é a decisão, e SÓ a decisão: puro, stdlib-only, sem import do core
e sem I/O. O call site (``channels.parse_inbound``) é quem loga e dá o
``continue``.

⚠️ A âncora é o TIPO literal ``"unsupported"`` + o ``errors[].code``, NUNCA o
texto renderizado na bolha — esse texto é gerado por nós em
``inbound_text.describe_unsupported`` e reescrevê-lo quebraria a regra em
silêncio.

⚠️ Fail-open em todos os níveis: qualquer coisa estranha ⇒ ``False`` (a
mensagem passa). O modo de falha aceitável é "voltou o ruído"; o inaceitável é
"sumiu mensagem de cliente".
"""

from __future__ import annotations

# Vazio = descarta QUALQUER ``unsupported``, independente do código (plano 95
# D5). A lista existe para o operador ESTREITAR a regra pela config do plugin
# (ex.: ``"131051"`` volta ao conservador), nunca para alargá-la.
DEFAULT_IGNORED_ERROR_CODES: tuple[int, ...] = ()

# O tipo literal que a Meta usa quando não sabe entregar o conteúdo. Um tipo
# NOVO e NOMEADO (ex.: a Meta passar a mandar ``"poll"``) pode vir COM payload
# — por isso a comparação é exata, e não "tipo que eu não conheço".
UNSUPPORTED_TYPE = "unsupported"


def parse_codes(raw) -> tuple[int, ...]:
    """``"131051, 131052"`` → ``(131051, 131052)``. Lixo é ignorado.

    Devolve ``()`` para vazio/``None``/só-lixo — que é exatamente o default
    "descartar todo ``unsupported``".
    """
    try:
        if raw is None:
            return ()
        if isinstance(raw, (list, tuple)):
            parts = [str(p) for p in raw]
        else:
            parts = str(raw).replace(";", ",").split(",")
        codes: list[int] = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            try:
                code = int(part)
            except (TypeError, ValueError):
                continue
            if code not in codes:
                codes.append(code)
        return tuple(codes)
    except Exception:  # noqa: BLE001 — fail-open: sem lista = comportamento default
        return ()


def should_ignore(msg, codes: tuple[int, ...] = DEFAULT_IGNORED_ERROR_CODES) -> bool:
    """``True`` quando a Meta entregou a mensagem SEM conteúdo algum.

    | Condição                                            | Decisão   |
    |-----------------------------------------------------|-----------|
    | ``type != "unsupported"``                            | passa     |
    | ``unsupported`` e ``codes`` vazio (default)          | descarta  |
    | ``unsupported`` e algum ``errors[].code`` em ``codes`` | descarta |
    | ``unsupported``, ``codes`` cheio, código fora da lista | passa    |
    | ``unsupported`` sem ``errors``, ``codes`` cheio      | passa     |
    | ``msg``/``errors`` malformado                        | passa     |
    """
    try:
        if not isinstance(msg, dict):
            return False
        if msg.get("type") != UNSUPPORTED_TYPE:
            return False
        if not codes:
            # D5 — sem estreitamento, todo ``unsupported`` é ruído.
            return True

        errors = msg.get("errors")
        if not isinstance(errors, (list, tuple)):
            return False
        for err in errors:
            if not isinstance(err, dict):
                continue
            try:
                code = int(err.get("code"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
            if code in codes:
                return True
        return False
    except Exception:  # noqa: BLE001 — fail-open: erro na regra nunca engole cliente
        return False


def describe_ignored(msg) -> str:
    """Rótulo humano do descarte, para o ``logger.warning`` do call site.

    É aqui — e SÓ aqui — que o ``title`` da Meta é lido: ele pode mudar de
    redação/locale, então serve de rótulo, jamais de critério de casamento.
    """
    try:
        if not isinstance(msg, dict):
            return "code=? title=? subtype=?"
        code = title = ""
        errors = msg.get("errors")
        if isinstance(errors, (list, tuple)):
            for err in errors:
                if isinstance(err, dict):
                    code = str(err.get("code") or "")
                    title = str(err.get("title") or err.get("message") or "")
                    break
        subtype = ""
        unsupported = msg.get(UNSUPPORTED_TYPE)
        if isinstance(unsupported, dict):
            subtype = str(unsupported.get("type") or "")
        return f"code={code or '?'} title={title or '?'} subtype={subtype or '?'}"
    except Exception:  # noqa: BLE001 — log nunca derruba o descarte
        return "code=? title=? subtype=?"
