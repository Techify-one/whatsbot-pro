"""Data-access das 5 tabelas `plugin_retornos_*` (SQLAlchemy Core via `make_plugin_db`).

Regras do repo (CLAUDE.md): bind params nomeados, nunca `%s`/`?`; escrita em transação
(`make_plugin_db` = `engine.begin()`). As árvores de regras são gravadas como TEXT com
JSON — `_decode`/`_encode` são o único ponto que sabe disso.

O `claim_due` é o **lock atômico** do dispatcher: um único `UPDATE … RETURNING` marca e
devolve os controles vencidos, então dois processos/ciclos concorrentes nunca disparam o
mesmo controle (porte do `dispatcher.service.ts` do Nexus).
"""

from __future__ import annotations

import json
import logging
import time

from sqlalchemy import text

from plugins.context import make_plugin_db

logger = logging.getLogger("plugin.retornos")

T_CONFIGURACOES = "plugin_retornos_configuracoes"
T_RETORNOS = "plugin_retornos_retornos"
T_MENSAGENS = "plugin_retornos_mensagens"
T_CONTROLE = "plugin_retornos_controle"
T_LOG = "plugin_retornos_log"

STATUS_ACTIVE = "active"
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"

ARVORE_VAZIA = {"regras": []}

TIPOS_MENSAGEM = ("text", "private_note", "ia_responde_agora",
                  "image", "audio", "video", "document")

# Colunas editáveis de uma configuração (a rota nunca escreve nada fora daqui).
CAMPOS_CONFIGURACAO = (
    "nome", "descricao", "posicao", "ativo", "on_reply",
    "cancel_on_resolve", "cancel_on_assign_human", "cancel_on_ai_off",
    "apply_to_groups", "tz_offset_hours",
)
CAMPOS_RETORNO = ("ordem", "nome", "filtros", "ab_ativo", "delay_mensagens_seg")

# Teto da pausa entre mensagens de um retorno (segundos). O ciclo do dispatcher é SERIAL:
# enquanto um retorno pausa, os outros controles do mesmo ciclo esperam — daí o teto.
MAX_PAUSA_MENSAGENS_SEG = 300
CAMPOS_MENSAGEM = ("ordem", "tipo", "content", "media_path", "media_url", "file_name")

_JSON_COLS = ("filtros", "data")
_BOOL_COLS = ("ativo", "cancel_on_resolve", "cancel_on_assign_human", "cancel_on_ai_off",
              "apply_to_groups", "ab_ativo", "processing")


def _now() -> float:
    return time.time()


def _decode(row) -> dict:
    """Row → dict com as colunas JSON já desserializadas e os inteiros-bool como bool."""
    if row is None:
        return {}
    d = dict(row)
    for col in _JSON_COLS:
        if col in d:
            d[col] = _loads(d[col])
    for col in _BOOL_COLS:
        if col in d and d[col] is not None:
            d[col] = bool(d[col])
    return d


def _loads(value):
    if value is None or value == "":
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def _encode_arvore(value) -> str:
    if value is None:
        return json.dumps(ARVORE_VAZIA)
    if isinstance(value, str):
        parsed = _loads(value)
        return json.dumps(parsed if parsed else ARVORE_VAZIA)
    if isinstance(value, dict) and "regras" in value:
        return json.dumps({"regras": value.get("regras") or []})
    return json.dumps(ARVORE_VAZIA)


def _sanitize(fields: dict, allowed: tuple[str, ...]) -> dict:
    out: dict = {}
    for key, value in (fields or {}).items():
        if key not in allowed:
            continue
        if key == "filtros":
            out[key] = _encode_arvore(value)
        elif key in _BOOL_COLS:
            out[key] = 1 if _truthy(value) else 0
        elif key == "on_reply":
            out[key] = "cancel" if str(value).lower() == "cancel" else "reset"
        elif key == "tipo":
            out[key] = str(value) if str(value) in TIPOS_MENSAGEM else "private_note"
        elif key in ("posicao", "ordem"):
            out[key] = _int(value)
        elif key == "delay_mensagens_seg":
            # Anulável de propósito: vazio/None = herda a pausa GLOBAL (setting do plugin).
            out[key] = _int_ou_none(value, hi=MAX_PAUSA_MENSAGENS_SEG)
        elif key == "tz_offset_hours":
            out[key] = _float(value, -3.0)
        else:
            out[key] = "" if value is None else str(value)
    return out


def _truthy(v) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "sim", "yes", "on")
    return bool(v)


def _int(v, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _int_ou_none(v, *, lo: int = 0, hi: int | None = None) -> int | None:
    """Inteiro clampado, ou ``None`` quando o campo vem vazio (= "herda o padrão")."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    n = max(lo, n)
    return min(hi, n) if hi is not None else n


def _float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ── Configurações ────────────────────────────────────────────────────────────────────

def list_configuracoes(*, only_active: bool = False) -> list[dict]:
    where = "WHERE ativo = 1 " if only_active else ""
    with make_plugin_db() as conn:
        rows = conn.execute(text(
            f"SELECT * FROM {T_CONFIGURACOES} {where}ORDER BY posicao ASC, id ASC"
        )).mappings().all()
    return [_decode(r) for r in rows]


def get_configuracao(configuracao_id: int) -> dict | None:
    with make_plugin_db() as conn:
        row = conn.execute(text(f"SELECT * FROM {T_CONFIGURACOES} WHERE id = :id"),
                           {"id": int(configuracao_id)}).mappings().first()
    return _decode(row) if row else None


def create_configuracao(fields: dict) -> dict:
    data = _sanitize(fields, CAMPOS_CONFIGURACAO)
    data.setdefault("nome", "Nova configuração")
    if "posicao" not in data:
        data["posicao"] = _next_posicao()
    now = _now()
    data["created_at"] = now
    data["updated_at"] = now
    cols = ", ".join(data)
    binds = ", ".join(f":{c}" for c in data)
    with make_plugin_db() as conn:
        new_id = conn.execute(
            text(f"INSERT INTO {T_CONFIGURACOES} ({cols}) VALUES ({binds}) RETURNING id"), data
        ).scalar()
    return get_configuracao(new_id) or {}


def _next_posicao() -> int:
    with make_plugin_db() as conn:
        v = conn.execute(text(f"SELECT COALESCE(MAX(posicao), -1) + 1 FROM {T_CONFIGURACOES}")).scalar()
    return _int(v)


def update_configuracao(configuracao_id: int, fields: dict) -> dict | None:
    data = _sanitize(fields, CAMPOS_CONFIGURACAO)
    if not data:
        return get_configuracao(configuracao_id)
    data["updated_at"] = _now()
    data["id"] = int(configuracao_id)
    sets = ", ".join(f"{c} = :{c}" for c in data if c != "id")
    with make_plugin_db() as conn:
        conn.execute(text(f"UPDATE {T_CONFIGURACOES} SET {sets} WHERE id = :id"), data)
    return get_configuracao(configuracao_id)


def delete_configuracao(configuracao_id: int) -> bool:
    rid = int(configuracao_id)
    with make_plugin_db() as conn:
        conn.execute(text(
            f"DELETE FROM {T_MENSAGENS} WHERE retorno_id IN "
            f"(SELECT id FROM {T_RETORNOS} WHERE configuracao_id = :rid)"), {"rid": rid})
        conn.execute(text(f"DELETE FROM {T_RETORNOS} WHERE configuracao_id = :rid"), {"rid": rid})
        conn.execute(text(f"DELETE FROM {T_CONTROLE} WHERE configuracao_id = :rid"), {"rid": rid})
        res = conn.execute(text(f"DELETE FROM {T_CONFIGURACOES} WHERE id = :rid"), {"rid": rid})
    return (res.rowcount or 0) > 0


def replace_configuracao(configuracao_id: int, payload: dict) -> dict | None:
    """Sobrescreve UMA configuração com o conteúdo de um JSON exportado (import destrutivo).

    Tudo numa transação só: os retornos/mensagens atuais caem e os do arquivo entram no
    lugar — nunca existe o meio-termo "configuração sem retorno nenhum" visível ao
    dispatcher. `posicao` e `ativo` são da INSTÂNCIA (o lugar na lista e o liga/desliga de
    quem já está rodando), então nunca vêm do arquivo, mesmo que o export os carregue.

    Devolve ``None`` quando a configuração não existe.
    """
    rid = int(configuracao_id)
    campos = _sanitize(payload or {}, CAMPOS_CONFIGURACAO)
    for chave in ("posicao", "ativo"):
        campos.pop(chave, None)
    if not (payload or {}).get("nome"):
        campos.pop("nome", None)  # arquivo sem nome mantém o nome atual
    now = _now()
    campos["updated_at"] = now
    campos["id"] = rid

    with make_plugin_db() as conn:
        existe = conn.execute(text(f"SELECT 1 FROM {T_CONFIGURACOES} WHERE id = :id"),
                              {"id": rid}).first()
        if not existe:
            return None
        sets = ", ".join(f"{c} = :{c}" for c in campos if c != "id")
        conn.execute(text(f"UPDATE {T_CONFIGURACOES} SET {sets} WHERE id = :id"), campos)

        conn.execute(text(
            f"DELETE FROM {T_MENSAGENS} WHERE retorno_id IN "
            f"(SELECT id FROM {T_RETORNOS} WHERE configuracao_id = :rid)"), {"rid": rid})
        # Agendamento parado num retorno que deixou de existir volta ao retorno 1.
        conn.execute(text(
            f"UPDATE {T_CONTROLE} SET retorno_atual_id = NULL, tentativas_retorno = 0 "
            f"WHERE configuracao_id = :rid"), {"rid": rid})
        conn.execute(text(f"DELETE FROM {T_RETORNOS} WHERE configuracao_id = :rid"), {"rid": rid})

        for ordem, retorno in enumerate(_lista(payload.get("retornos"))):
            dados = _sanitize(retorno, CAMPOS_RETORNO)
            dados["ordem"] = ordem  # a ordem do arquivo é a ordem da lista
            dados.setdefault("filtros", json.dumps(ARVORE_VAZIA))
            dados.setdefault("nome", f"Retorno {ordem + 1}")
            dados.update({"configuracao_id": rid, "created_at": now, "updated_at": now})
            cols = ", ".join(dados)
            binds = ", ".join(f":{c}" for c in dados)
            novo_id = conn.execute(text(
                f"INSERT INTO {T_RETORNOS} ({cols}) VALUES ({binds}) RETURNING id"), dados).scalar()
            for pos, msg in enumerate(_lista(retorno.get("mensagens"))):
                m = _sanitize(msg, CAMPOS_MENSAGEM)
                m["ordem"] = pos
                m.setdefault("tipo", "private_note")
                m.update({"retorno_id": int(novo_id), "created_at": now, "updated_at": now})
                m_cols = ", ".join(m)
                m_binds = ", ".join(f":{c}" for c in m)
                conn.execute(text(
                    f"INSERT INTO {T_MENSAGENS} ({m_cols}) VALUES ({m_binds})"), m)

    return configuracao_full(rid)


def _lista(value) -> list:
    """Lista de dicts do payload importado (qualquer outra coisa é ignorada)."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def reorder_configuracoes(ids: list) -> None:
    now = _now()
    with make_plugin_db() as conn:
        for pos, rid in enumerate(ids or []):
            conn.execute(text(
                f"UPDATE {T_CONFIGURACOES} SET posicao = :p, updated_at = :u WHERE id = :id"),
                {"p": pos, "u": now, "id": _int(rid)})


# ── Retornos ────────────────────────────────────────────────────────────────────

def list_retornos(configuracao_id: int) -> list[dict]:
    with make_plugin_db() as conn:
        rows = conn.execute(text(
            f"SELECT * FROM {T_RETORNOS} WHERE configuracao_id = :rid ORDER BY ordem ASC, id ASC"),
            {"rid": int(configuracao_id)}).mappings().all()
    return [_decode(r) for r in rows]


def get_retorno(retorno_id: int) -> dict | None:
    with make_plugin_db() as conn:
        row = conn.execute(text(f"SELECT * FROM {T_RETORNOS} WHERE id = :id"),
                           {"id": int(retorno_id)}).mappings().first()
    return _decode(row) if row else None


def first_retorno(configuracao_id: int) -> dict | None:
    retornos = list_retornos(configuracao_id)
    return retornos[0] if retornos else None


def next_retorno(configuracao_id: int, ordem: int) -> dict | None:
    with make_plugin_db() as conn:
        row = conn.execute(text(
            f"SELECT * FROM {T_RETORNOS} WHERE configuracao_id = :rid AND ordem > :o "
            f"ORDER BY ordem ASC, id ASC LIMIT 1"),
            {"rid": int(configuracao_id), "o": _int(ordem)}).mappings().first()
    return _decode(row) if row else None


def create_retorno(configuracao_id: int, fields: dict) -> dict:
    data = _sanitize(fields, CAMPOS_RETORNO)
    data.setdefault("filtros", json.dumps(ARVORE_VAZIA))
    data.setdefault("nome", "Novo retorno")
    if "ordem" not in data:
        with make_plugin_db() as conn:
            data["ordem"] = _int(conn.execute(text(
                f"SELECT COALESCE(MAX(ordem), -1) + 1 FROM {T_RETORNOS} WHERE configuracao_id = :rid"),
                {"rid": int(configuracao_id)}).scalar())
    now = _now()
    data.update({"configuracao_id": int(configuracao_id), "created_at": now, "updated_at": now})
    cols = ", ".join(data)
    binds = ", ".join(f":{c}" for c in data)
    with make_plugin_db() as conn:
        new_id = conn.execute(
            text(f"INSERT INTO {T_RETORNOS} ({cols}) VALUES ({binds}) RETURNING id"), data
        ).scalar()
    return get_retorno(new_id) or {}


def update_retorno(retorno_id: int, fields: dict) -> dict | None:
    data = _sanitize(fields, CAMPOS_RETORNO)
    if not data:
        return get_retorno(retorno_id)
    data["updated_at"] = _now()
    data["id"] = int(retorno_id)
    sets = ", ".join(f"{c} = :{c}" for c in data if c != "id")
    with make_plugin_db() as conn:
        conn.execute(text(f"UPDATE {T_RETORNOS} SET {sets} WHERE id = :id"), data)
    return get_retorno(retorno_id)


def delete_retorno(retorno_id: int) -> bool:
    pid = int(retorno_id)
    with make_plugin_db() as conn:
        conn.execute(text(f"DELETE FROM {T_MENSAGENS} WHERE retorno_id = :pid"), {"pid": pid})
        # Controle parado neste retorno volta ao retorno 1 na próxima avaliação.
        conn.execute(text(
            f"UPDATE {T_CONTROLE} SET retorno_atual_id = NULL, tentativas_retorno = 0 "
            f"WHERE retorno_atual_id = :pid"), {"pid": pid})
        res = conn.execute(text(f"DELETE FROM {T_RETORNOS} WHERE id = :pid"), {"pid": pid})
    return (res.rowcount or 0) > 0


def reorder_retornos(configuracao_id: int, ids: list) -> None:
    now = _now()
    with make_plugin_db() as conn:
        for pos, pid in enumerate(ids or []):
            conn.execute(text(
                f"UPDATE {T_RETORNOS} SET ordem = :o, updated_at = :u "
                f"WHERE id = :id AND configuracao_id = :rid"),
                {"o": pos, "u": now, "id": _int(pid), "rid": int(configuracao_id)})


def bump_msg_cursor(retorno_id: int) -> int:
    """Avança (atomicamente) o cursor de rotação A/B e devolve o valor ANTERIOR."""
    with make_plugin_db() as conn:
        prev = conn.execute(text(
            f"UPDATE {T_RETORNOS} SET proxima_mensagem_index = proxima_mensagem_index + 1 "
            f"WHERE id = :id RETURNING proxima_mensagem_index - 1"),
            {"id": int(retorno_id)}).scalar()
    return _int(prev)


# ── Mensagens ─────────────────────────────────────────────────────────────────

def list_mensagens(retorno_id: int) -> list[dict]:
    with make_plugin_db() as conn:
        rows = conn.execute(text(
            f"SELECT * FROM {T_MENSAGENS} WHERE retorno_id = :pid ORDER BY ordem ASC, id ASC"),
            {"pid": int(retorno_id)}).mappings().all()
    return [_decode(r) for r in rows]


def get_mensagem(msg_id: int) -> dict | None:
    with make_plugin_db() as conn:
        row = conn.execute(text(f"SELECT * FROM {T_MENSAGENS} WHERE id = :id"),
                           {"id": int(msg_id)}).mappings().first()
    return _decode(row) if row else None


def create_mensagem(retorno_id: int, fields: dict) -> dict:
    data = _sanitize(fields, CAMPOS_MENSAGEM)
    data.setdefault("tipo", "private_note")
    if "ordem" not in data:
        with make_plugin_db() as conn:
            data["ordem"] = _int(conn.execute(text(
                f"SELECT COALESCE(MAX(ordem), -1) + 1 FROM {T_MENSAGENS} WHERE retorno_id = :pid"),
                {"pid": int(retorno_id)}).scalar())
    now = _now()
    data.update({"retorno_id": int(retorno_id), "created_at": now, "updated_at": now})
    cols = ", ".join(data)
    binds = ", ".join(f":{c}" for c in data)
    with make_plugin_db() as conn:
        new_id = conn.execute(
            text(f"INSERT INTO {T_MENSAGENS} ({cols}) VALUES ({binds}) RETURNING id"), data
        ).scalar()
    return get_mensagem(new_id) or {}


def update_mensagem(msg_id: int, fields: dict) -> dict | None:
    data = _sanitize(fields, CAMPOS_MENSAGEM)
    if not data:
        return get_mensagem(msg_id)
    data["updated_at"] = _now()
    data["id"] = int(msg_id)
    sets = ", ".join(f"{c} = :{c}" for c in data if c != "id")
    with make_plugin_db() as conn:
        conn.execute(text(f"UPDATE {T_MENSAGENS} SET {sets} WHERE id = :id"), data)
    return get_mensagem(msg_id)


def delete_mensagem(msg_id: int) -> bool:
    with make_plugin_db() as conn:
        res = conn.execute(text(f"DELETE FROM {T_MENSAGENS} WHERE id = :id"),
                           {"id": int(msg_id)})
    return (res.rowcount or 0) > 0


def reorder_mensagens(retorno_id: int, ids: list) -> None:
    now = _now()
    with make_plugin_db() as conn:
        for pos, mid in enumerate(ids or []):
            conn.execute(text(
                f"UPDATE {T_MENSAGENS} SET ordem = :o, updated_at = :u "
                f"WHERE id = :id AND retorno_id = :pid"),
                {"o": pos, "u": now, "id": _int(mid), "pid": int(retorno_id)})


def configuracao_full(configuracao_id: int) -> dict | None:
    """Configuração + retornos + mensagens (o payload que a tela de edição carrega)."""
    configuracao = get_configuracao(configuracao_id)
    if not configuracao:
        return None
    retornos = list_retornos(configuracao_id)
    for p in retornos:
        p["mensagens"] = list_mensagens(p["id"])
    configuracao["retornos"] = retornos
    return configuracao


# ── Controle ──────────────────────────────────────────────────────────────────

def get_controle(controle_id: int) -> dict | None:
    with make_plugin_db() as conn:
        row = conn.execute(text(f"SELECT * FROM {T_CONTROLE} WHERE id = :id"),
                           {"id": int(controle_id)}).mappings().first()
    return _decode(row) if row else None


def get_controle_by_conversation(conversation_id: int) -> dict | None:
    with make_plugin_db() as conn:
        row = conn.execute(text(
            f"SELECT * FROM {T_CONTROLE} WHERE conversation_id = :cid"),
            {"cid": int(conversation_id)}).mappings().first()
    return _decode(row) if row else None


def upsert_controle(*, conversation_id: int, configuracao_id: int, contact_id=None, phone="",
                    contact_name="", channel_id="default", inbox_id=None,
                    retorno_atual_id=None, next_at=None, last_client_ts=None) -> dict:
    """Cria (ou re-arma) o controle da conversa. ``conversation_id`` é UNIQUE (P3)."""
    now = _now()
    params = {
        "cid": int(conversation_id), "rid": int(configuracao_id),
        "contact_id": contact_id, "phone": phone or "", "contact_name": contact_name or "",
        "channel_id": channel_id or "default", "inbox_id": inbox_id,
        "retorno": retorno_atual_id, "next_at": next_at, "last_client": last_client_ts,
        "now": now,
    }
    with make_plugin_db() as conn:
        conn.execute(text(
            f"INSERT INTO {T_CONTROLE} (conversation_id, configuracao_id, contact_id, phone, "
            f"contact_name, channel_id, inbox_id, retorno_atual_id, retorno_started_at, "
            f"disparos_enviados, tentativas_retorno, last_client_ts, next_at, processing, "
            f"status, created_at, updated_at) VALUES (:cid, :rid, :contact_id, :phone, "
            f":contact_name, :channel_id, :inbox_id, :retorno, :now, 0, 0, :last_client, "
            f":next_at, 0, 'active', :now, :now) "
            f"ON CONFLICT (conversation_id) DO UPDATE SET configuracao_id = EXCLUDED.configuracao_id, "
            f"contact_id = EXCLUDED.contact_id, phone = EXCLUDED.phone, "
            f"contact_name = EXCLUDED.contact_name, channel_id = EXCLUDED.channel_id, "
            f"inbox_id = EXCLUDED.inbox_id, retorno_atual_id = EXCLUDED.retorno_atual_id, "
            f"retorno_started_at = EXCLUDED.retorno_started_at, disparos_enviados = 0, "
            f"tentativas_retorno = 0, last_client_ts = EXCLUDED.last_client_ts, "
            f"next_at = EXCLUDED.next_at, processing = 0, processing_since = NULL, "
            f"status = 'active', last_error = NULL, updated_at = EXCLUDED.updated_at"),
            params)
    return get_controle_by_conversation(conversation_id) or {}


def update_controle(controle_id: int, **fields) -> dict | None:
    allowed = ("retorno_atual_id", "retorno_started_at", "disparos_enviados",
               "tentativas_retorno", "last_client_ts", "next_at", "processing",
               "processing_since", "status", "last_error", "configuracao_id", "channel_id")
    data = {k: v for k, v in fields.items() if k in allowed}
    if not data:
        return get_controle(controle_id)
    data["updated_at"] = _now()
    data["id"] = int(controle_id)
    sets = ", ".join(f"{c} = :{c}" for c in data if c != "id")
    with make_plugin_db() as conn:
        conn.execute(text(f"UPDATE {T_CONTROLE} SET {sets} WHERE id = :id"), data)
    return get_controle(controle_id)


def set_status(controle_id: int, status: str, *, last_error: str | None = None) -> dict | None:
    return update_controle(controle_id, status=status, processing=0,
                           processing_since=None, last_error=last_error)


def recover_stale_locks(*, older_than: float) -> int:
    """Solta locks presos (processo morto no meio do ciclo)."""
    with make_plugin_db() as conn:
        res = conn.execute(text(
            f"UPDATE {T_CONTROLE} SET processing = 0, processing_since = NULL, "
            f"updated_at = :now WHERE processing = 1 AND "
            f"(processing_since IS NULL OR processing_since < :cutoff)"),
            {"now": _now(), "cutoff": float(older_than)})
    return res.rowcount or 0


def claim_due(*, now: float, limit: int = 50) -> list[dict]:
    """Marca e devolve os controles vencidos — lock atômico (`UPDATE … RETURNING`)."""
    with make_plugin_db() as conn:
        rows = conn.execute(text(
            f"UPDATE {T_CONTROLE} SET processing = 1, processing_since = :now, "
            f"updated_at = :now WHERE id IN (SELECT id FROM {T_CONTROLE} "
            f"WHERE status = 'active' AND processing = 0 AND next_at IS NOT NULL "
            f"AND next_at <= :now ORDER BY next_at ASC LIMIT :lim) RETURNING *"),
            {"now": float(now), "lim": max(1, int(limit))}).mappings().all()
    return [_decode(r) for r in rows]


def release(controle_id: int) -> None:
    with make_plugin_db() as conn:
        conn.execute(text(
            f"UPDATE {T_CONTROLE} SET processing = 0, processing_since = NULL "
            f"WHERE id = :id"), {"id": int(controle_id)})


def _controle_where(status, configuracao_id, disparos, next_from, next_to) -> tuple[str, dict]:
    """Cláusula comum de `list_controles`/`count_controles` — a página e o total têm de
    filtrar IGUAL (contar sobre a página diria "1 de 200" para sempre).

    `disparos` casa EXATAMENTE com a coluna "Disparos" (`disparos_enviados`) — `0` é um
    filtro legítimo (quem ainda não disparou), então o teste é contra `None`, nunca
    contra a falsidade do valor.

    `next_from`/`next_to` são epochs e recortam a coluna "Próximo" (`next_at`); um
    agendamento sem próximo disparo fica de fora quando qualquer um dos dois é passado
    (não existe instante que caiba no intervalo).
    """
    clauses, params = [], {}
    if status and status not in ("todos", "all", ""):
        clauses.append("c.status = :st")
        params["st"] = status
    if configuracao_id:
        clauses.append("c.configuracao_id = :rid")
        params["rid"] = int(configuracao_id)
    if disparos is not None:
        clauses.append("c.disparos_enviados = :disp")
        params["disp"] = int(disparos)
    if next_from is not None:
        clauses.append("c.next_at IS NOT NULL AND c.next_at >= :nfrom")
        params["nfrom"] = float(next_from)
    if next_to is not None:
        clauses.append("c.next_at IS NOT NULL AND c.next_at <= :nto")
        params["nto"] = float(next_to)
    return (("WHERE " + " AND ".join(clauses)) if clauses else ""), params


def list_controles(*, status: str | None = None, configuracao_id: int | None = None,
                   disparos: int | None = None,
                   next_from: float | None = None, next_to: float | None = None,
                   limit: int = 200, offset: int = 0) -> list[dict]:
    """Uma PÁGINA de agendamentos do monitor, já filtrada **no servidor**.

    O `ORDER BY` termina em `c.id DESC` de propósito: os critérios de cima empatam com
    facilidade (vários `next_at` NULL, mesmo `updated_at`) e, sem desempate estável, a
    mesma linha apareceria em duas páginas — ou em nenhuma.
    """
    where, params = _controle_where(status, configuracao_id, disparos, next_from, next_to)
    params["lim"] = max(1, min(int(limit or 200), 1000))
    params["off"] = max(0, int(offset or 0))
    with make_plugin_db() as conn:
        rows = conn.execute(text(
            f"SELECT c.*, r.nome AS configuracao_nome, p.nome AS retorno_nome, p.ordem AS retorno_ordem "
            f"FROM {T_CONTROLE} c "
            f"LEFT JOIN {T_CONFIGURACOES} r ON r.id = c.configuracao_id "
            f"LEFT JOIN {T_RETORNOS} p ON p.id = c.retorno_atual_id "
            f"{where} ORDER BY (c.status = 'active') DESC, c.next_at ASC NULLS LAST, "
            f"c.updated_at DESC, c.id DESC LIMIT :lim OFFSET :off"), params).mappings().all()
    return [_decode(r) for r in rows]


def count_controles(*, status: str | None = None, configuracao_id: int | None = None,
                    disparos: int | None = None,
                    next_from: float | None = None, next_to: float | None = None) -> int:
    """Total de agendamentos do filtro — a paginação da tela precisa do total, não da página."""
    where, params = _controle_where(status, configuracao_id, disparos, next_from, next_to)
    with make_plugin_db() as conn:
        return _int(conn.execute(text(
            f"SELECT COUNT(*) FROM {T_CONTROLE} c {where}"), params).scalar())


def stats() -> dict:
    """Contagem por status **no servidor** (o monitor nunca conta sobre a página)."""
    with make_plugin_db() as conn:
        rows = conn.execute(text(
            f"SELECT status, COUNT(*) AS n FROM {T_CONTROLE} GROUP BY status")).mappings().all()
        disparos = conn.execute(text(
            f"SELECT COALESCE(SUM(disparos_enviados), 0) FROM {T_CONTROLE}")).scalar()
    out = {"active": 0, "completed": 0, "cancelled": 0, "expired": 0,
           "total": 0, "disparos": _int(disparos)}
    for r in rows:
        out[str(r["status"])] = _int(r["n"])
        out["total"] += _int(r["n"])
    return out


def cancel_by_conversation(conversation_id: int, motivo: str = "") -> dict | None:
    ctrl = get_controle_by_conversation(conversation_id)
    if not ctrl or ctrl.get("status") != STATUS_ACTIVE:
        return None
    updated = set_status(ctrl["id"], STATUS_CANCELLED, last_error=motivo or None)
    add_log("cancelled", configuracao_id=ctrl.get("configuracao_id"), controle_id=ctrl.get("id"),
            conversation_id=conversation_id, data={"motivo": motivo})
    return updated


# ── Log ───────────────────────────────────────────────────────────────────────

def add_log(evento: str, *, configuracao_id=None, controle_id=None, conversation_id=None,
            retorno_id=None, nivel: str = "info", data: dict | None = None) -> None:
    """Uma linha de observabilidade. Best-effort: nunca levanta."""
    try:
        with make_plugin_db() as conn:
            conn.execute(text(
                f"INSERT INTO {T_LOG} (evento, nivel, configuracao_id, controle_id, "
                f"conversation_id, retorno_id, data, ts) VALUES (:e, :n, :r, :c, :cv, :p, "
                f":d, :ts)"),
                {"e": str(evento)[:60], "n": nivel, "r": configuracao_id, "c": controle_id,
                 "cv": conversation_id, "p": retorno_id,
                 "d": json.dumps(data or {}, ensure_ascii=False, default=str)[:8000],
                 "ts": _now()})
    except Exception:  # noqa: BLE001
        logger.debug("retornos: add_log falhou (%s)", evento, exc_info=True)


def _log_where(conversation_id, configuracao_id, evento) -> tuple[str, dict]:
    """Cláusula comum de `list_logs`/`count_logs` — a página e o total têm de filtrar IGUAL."""
    clauses, params = [], {}
    if conversation_id:
        clauses.append("conversation_id = :cv")
        params["cv"] = int(conversation_id)
    if configuracao_id:
        clauses.append("configuracao_id = :r")
        params["r"] = int(configuracao_id)
    if evento:
        clauses.append("evento = :ev")
        params["ev"] = str(evento)[:60]
    return (("WHERE " + " AND ".join(clauses)) if clauses else ""), params


def list_logs(*, conversation_id: int | None = None, configuracao_id: int | None = None,
              evento: str | None = None, limit: int = 200, offset: int = 0) -> list[dict]:
    where, params = _log_where(conversation_id, configuracao_id, evento)
    params["lim"] = max(1, min(int(limit or 200), 1000))
    params["off"] = max(0, int(offset or 0))
    with make_plugin_db() as conn:
        rows = conn.execute(text(
            f"SELECT * FROM {T_LOG} {where} ORDER BY ts DESC, id DESC "
            f"LIMIT :lim OFFSET :off"), params).mappings().all()
    return [_decode(r) for r in rows]


def count_logs(*, conversation_id: int | None = None, configuracao_id: int | None = None,
               evento: str | None = None) -> int:
    """Total de linhas do filtro — a paginação da tela precisa do total, não da página."""
    where, params = _log_where(conversation_id, configuracao_id, evento)
    with make_plugin_db() as conn:
        return _int(conn.execute(text(f"SELECT COUNT(*) FROM {T_LOG} {where}"), params).scalar())


def log_eventos() -> list[str]:
    """Tipos de evento presentes no log (alimenta o filtro da aba Eventos)."""
    with make_plugin_db() as conn:
        rows = conn.execute(text(
            f"SELECT DISTINCT evento FROM {T_LOG} ORDER BY evento")).scalars().all()
    return [str(r) for r in rows if r]


def prune_logs(*, keep: int = 5000) -> int:
    """Mantém apenas as últimas ``keep`` linhas (o log é observabilidade, não histórico)."""
    with make_plugin_db() as conn:
        res = conn.execute(text(
            f"DELETE FROM {T_LOG} WHERE id NOT IN "
            f"(SELECT id FROM {T_LOG} ORDER BY id DESC LIMIT :keep)"),
            {"keep": max(100, int(keep))})
    return res.rowcount or 0
