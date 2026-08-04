"""Mapeamentos de campo: leitura, validação e escrita da configuração.

A validação vive no SERVIDOR, não na tela. A tela valida por conveniência (para
o operador ver o erro na linha certa antes de salvar), mas o gate é aqui: os
dois vocabulários que um mapeamento referencia — atributos do WhatsBot e campos
do CDP — só são consultáveis daqui, e um chamador programático não passa pela
tela nenhuma.

Os erros voltam POR LINHA (``{"2": ["..."]}``), não como uma frase só. Um "erro
ao salvar" genérico numa lista de vinte mapeamentos obriga o operador a caçar
qual linha ofendeu.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import text

from plugins.context import make_plugin_db

from . import trackify_db
from .field_codec import compat

logger = logging.getLogger("plugins.trackify.field_map")

MAX_ROWS = 50

SCOPE_ATTR = "attribute"
SCOPE_COLUMN = "column"

# Colunas nativas do contato oferecidas no lado esquerdo. Só ``name``: e-mail,
# profissão, empresa e endereço migraram para atributo personalizado (migration
# 0028 do core) e mapear a COLUNA legada gravaria num lugar que o painel não lê.
# ``phone`` fica de fora de propósito — é a chave da conversa, e sobrescrevê-lo
# a partir do CDP orfanaria o atendimento.
ALLOWED_COLUMNS = {"name": "Nome"}

DIRECTIONS = ("to_trackify", "to_whatsbot", "both")
POLICIES = ("whatsbot_wins", "trackify_wins", "hold")

_WRITES_TK = ("to_trackify", "both")
_WRITES_WB = ("to_whatsbot", "both")

_COLS = (
    "id, wb_scope, wb_key, tk_slug, tk_field_id, tk_field_type, tk_regex, "
    "tk_is_identifier, direction, conflict_policy, ack_identifier, enabled, "
    "unverified, position, pushed_ok, pushed_err, pulled_ok, pulled_rejected, "
    "conflicts, last_push_at, last_pull_at, last_error, created_at, updated_at"
)


# ── Vocabulários ─────────────────────────────────────────────────────────

def wb_vocabulary() -> dict:
    """Atributos de contato + as colunas nativas oferecidas.

    Servido pelo plugin (e não consumido direto de ``/api/custom-attributes``
    pelo navegador) para que a lista de colunas permitidas seja EXATAMENTE a
    mesma que este módulo valida. Duplicá-la em JS garantiria divergência.
    """
    from db.repositories import custom_attribute_repo as ca_repo

    attributes = []
    try:
        for d in ca_repo.list_definitions("contact"):
            attributes.append({
                "key": d["attribute_key"],
                "label": d.get("display_name") or d["attribute_key"],
                "type": (d.get("type") or "text"),
                "options": d.get("options") or [],
                "is_system": bool(d.get("is_system")),
                "regex_pattern": d.get("regex_pattern") or "",
                "regex_cue": d.get("regex_cue") or "",
            })
    except Exception:  # noqa: BLE001
        logger.exception("trackify: falha ao listar atributos de contato")
    columns = [{"key": k, "label": v, "type": "text", "options": [],
                "is_system": False} for k, v in ALLOWED_COLUMNS.items()]
    return {"columns": columns, "attributes": attributes}


_TK_FIELDS_SQL = """
SELECT cf.id::text        AS id,
       cf.slug            AS slug,
       cf.name            AS name,
       cf.is_identifier   AS is_identifier,
       cf.identifier_priority AS identifier_priority,
       COALESCE(ft.slug, '')          AS field_type,
       COALESCE(ft.regex_pattern, '') AS regex_pattern
FROM custom_fields cf
LEFT JOIN field_types ft ON ft.id = cf.field_type_id
WHERE cf.deleted_at IS NULL AND cf.is_active
ORDER BY cf.is_identifier DESC, cf.identifier_priority NULLS LAST, cf.name
"""

# Sem o join de tipo: se ``field_types`` não existir/mudar de forma no CDP, a
# tela ainda lista os campos (só perde a dica de tipo, que é consultiva).
_TK_FIELDS_SQL_BARE = """
SELECT cf.id::text      AS id,
       cf.slug          AS slug,
       cf.name          AS name,
       cf.is_identifier AS is_identifier,
       cf.identifier_priority AS identifier_priority,
       ''               AS field_type,
       ''               AS regex_pattern
FROM custom_fields cf
WHERE cf.deleted_at IS NULL AND cf.is_active
ORDER BY cf.is_identifier DESC, cf.identifier_priority NULLS LAST, cf.name
"""


def tk_fields(refresh: bool = False) -> dict:
    """Campos de contato ativos no CDP. NUNCA levanta — no máximo devolve vazio
    com o motivo, para a tela distinguir "não configurado" de "fora do ar" de
    "o CDP não tem campo nenhum"."""
    if not trackify_db.is_configured():
        return {"configured": False, "reachable": False,
                "message": "DSN do Nexus não configurado.", "fields": []}

    def _read():
        rows = trackify_db.run_read(_TK_FIELDS_SQL, {})
        if not rows:
            rows = trackify_db.run_read(_TK_FIELDS_SQL_BARE, {})
        return [dict(r) for r in rows]

    try:
        rows = _read() if refresh else trackify_db.cached("field_map:cfields", _read)
    except Exception as e:  # noqa: BLE001
        logger.warning("trackify: falha ao listar campos do CDP: %s", type(e).__name__)
        return {"configured": True, "reachable": False,
                "message": "Não foi possível ler os campos do Trackify.", "fields": []}
    return {"configured": True, "reachable": True, "message": "", "fields": rows}


def identifier_hints(contact: dict) -> dict:
    """``{slug_no_trackify: valor_no_whatsbot}`` dos campos conectados que são
    IDENTIFICADOR no CDP — o que a busca automática usa além do telefone.

    Lê o valor do atributo personalizado, com a coluna legada como reserva. Essa
    reserva importa: e-mail/profissão/empresa/endereço migraram das colunas para
    o JSON (migração 0028 do core) e as colunas ficaram vazias, então ler só a
    coluna — como o código de identidade fazia — nunca encontrava o e-mail e a
    busca caía sempre no telefone.
    """
    attrs = contact.get("custom_attributes")
    if not isinstance(attrs, dict):
        attrs = {}
    out: dict = {}
    try:
        for m in list_maps(enabled_only=True):
            if not m.get("tk_is_identifier"):
                continue
            if m["wb_scope"] == SCOPE_COLUMN:
                valor = contact.get(m["wb_key"])
            else:
                valor = attrs.get(m["wb_key"])
                if valor in (None, "") and m["wb_key"] in contact:
                    valor = contact.get(m["wb_key"])       # coluna legada
            if valor not in (None, "") and not isinstance(valor, (dict, list)):
                out[m["tk_slug"]] = str(valor).strip()
    except Exception:  # noqa: BLE001 — sem pistas, sobra o telefone
        logger.debug("trackify: falha ao montar pistas de identidade", exc_info=True)
    return out


# ── Leitura ──────────────────────────────────────────────────────────────

def list_maps(enabled_only: bool = False) -> list[dict]:
    sql = f"SELECT {_COLS} FROM plugin_trackify_field_map"
    if enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY position, id"
    with make_plugin_db() as conn:
        rows = conn.execute(text(sql)).mappings().all()
    return [dict(r) for r in rows]


# ── Validação ────────────────────────────────────────────────────────────

def validate(rows: list, *, credential_set: bool) -> tuple[list[dict], dict]:
    """Devolve ``(linhas_limpas, erros_por_índice)``.

    ``credential_set`` entra como parâmetro (e não é lido daqui) para esta
    função continuar testável sem tocar em config.
    """
    errors: dict[str, list[str]] = {}
    clean: list[dict] = []

    def err(i: int, msg: str) -> None:
        errors.setdefault(str(i), []).append(msg)

    if not isinstance(rows, list):
        return [], {"_": ["Formato inválido."]}
    if len(rows) > MAX_ROWS:
        return [], {"_": [f"Máximo de {MAX_ROWS} mapeamentos."]}

    vocab = wb_vocabulary()
    attrs = {a["key"]: a for a in vocab["attributes"]}
    tk = tk_fields()
    tk_by_slug = {f["slug"]: f for f in tk["fields"]}
    # Só dá para afirmar que um slug NÃO existe quando conseguimos ler o
    # catálogo. Com o CDP fora do ar o mapeamento é aceito e marcado.
    can_verify = bool(tk["reachable"])

    seen_left: dict[tuple[str, str], int] = {}
    seen_right: dict[str, int] = {}

    for i, raw in enumerate(rows):
        if not isinstance(raw, dict):
            err(i, "Formato inválido.")
            continue

        scope = str(raw.get("wb_scope") or SCOPE_ATTR).strip()
        wb_key = str(raw.get("wb_key") or "").strip()
        tk_slug = str(raw.get("tk_slug") or "").strip()
        direction = str(raw.get("direction") or "to_trackify").strip()
        policy = str(raw.get("conflict_policy") or "whatsbot_wins").strip()
        ack = bool(raw.get("ack_identifier"))
        enabled = bool(raw.get("enabled", True))

        if not wb_key or not tk_slug:
            err(i, "Escolha os dois lados.")
            continue
        if direction not in DIRECTIONS:
            err(i, "Direção inválida.")
            continue
        if policy not in POLICIES:
            err(i, "Regra de conflito inválida.")
            continue

        writes_tk = direction in _WRITES_TK
        writes_wb = direction in _WRITES_WB

        # ── Lado esquerdo ────────────────────────────────────────────────
        definition = None
        if scope == SCOPE_COLUMN:
            if wb_key not in ALLOWED_COLUMNS:
                err(i, f"'{wb_key}' não é um campo de contato mapeável.")
                continue
            wb_type = "text"
        elif scope == SCOPE_ATTR:
            definition = attrs.get(wb_key)
            if definition is None:
                err(i, "Este atributo não existe mais no WhatsBot.")
                continue
            wb_type = definition["type"]
            if definition["is_system"] and writes_wb:
                # Atributo de sistema é mantido por outro plugin, que o
                # reescreve no ritmo dele: deixar o Trackify gravar aí começa
                # uma guerra de escrita que nenhum dos dois lados vence.
                err(i, "Atributo mantido por outro plugin: só pode ser enviado "
                       "ao Trackify (→), nunca recebido.")
                continue
        else:
            err(i, "Escopo inválido.")
            continue

        dup = seen_left.get((scope, wb_key))
        if dup is not None:
            err(i, f"Este campo do WhatsBot já está mapeado na linha {dup + 1}.")
            continue
        seen_left[(scope, wb_key)] = i

        dup = seen_right.get(tk_slug)
        if dup is not None:
            err(i, f"Este campo do Trackify já está mapeado na linha {dup + 1}.")
            continue
        seen_right[tk_slug] = i

        # ── Lado direito ─────────────────────────────────────────────────
        field = tk_by_slug.get(tk_slug)
        if field is None and can_verify:
            err(i, "Este campo não existe mais no Trackify.")
            continue

        is_identifier = bool(field and field.get("is_identifier"))
        if is_identifier and writes_tk and not ack:
            err(i, "Campo identificador: confirme o aviso antes de salvar.")
            continue

        if writes_tk and not credential_set:
            err(i, "Sem conta de serviço configurada não é possível escrever no Trackify.")
            continue

        clean.append({
            "wb_scope": scope,
            "wb_key": wb_key,
            "tk_slug": tk_slug,
            "tk_field_id": (field or {}).get("id") or "",
            "tk_field_type": (field or {}).get("field_type") or "",
            "tk_regex": (field or {}).get("regex_pattern") or "",
            "tk_is_identifier": 1 if is_identifier else 0,
            "direction": direction,
            "conflict_policy": policy,
            "ack_identifier": 1 if ack else 0,
            "enabled": 1 if enabled else 0,
            "unverified": 0 if field else 1,
            "position": len(clean),
            "compat": compat(wb_type, (field or {}).get("field_type")),
        })

    return clean, errors


# ── Escrita ──────────────────────────────────────────────────────────────

def replace_all(rows: list[dict]) -> None:
    """Troca o conjunto inteiro numa transação.

    Apaga-e-insere (em vez de diferenciar) porque os dois índices UNIQUE
    tropeçariam numa simples reordenação: trocar a linha 1 com a linha 2
    passaria por um estado intermediário com slug duplicado.
    O ESTADO por contato é preservado: ``plugin_trackify_field_state`` é
    reancorado pela chave lógica, senão toda edição de mapeamento faria a base
    inteira parecer "primeira sincronização" e reescreveria tudo.
    """
    now = time.time()
    with make_plugin_db() as conn:
        old = conn.execute(text(
            "SELECT id, wb_scope, wb_key, tk_slug FROM plugin_trackify_field_map"
        )).mappings().all()
        old_key = {r["id"]: (r["wb_scope"], r["wb_key"], r["tk_slug"]) for r in old}

        conn.execute(text("DELETE FROM plugin_trackify_field_map"))
        new_id: dict[tuple, int] = {}
        for r in rows:
            res = conn.execute(text(
                "INSERT INTO plugin_trackify_field_map "
                "(wb_scope, wb_key, tk_slug, tk_field_id, tk_field_type, tk_regex, "
                " tk_is_identifier, direction, conflict_policy, ack_identifier, "
                " enabled, unverified, position, created_at, updated_at) "
                "VALUES (:wb_scope, :wb_key, :tk_slug, :tk_field_id, :tk_field_type, "
                " :tk_regex, :tk_is_identifier, :direction, :conflict_policy, "
                " :ack_identifier, :enabled, :unverified, :position, :now, :now) "
                "RETURNING id"),
                {**{k: r[k] for k in (
                    "wb_scope", "wb_key", "tk_slug", "tk_field_id", "tk_field_type",
                    "tk_regex", "tk_is_identifier", "direction", "conflict_policy",
                    "ack_identifier", "enabled", "unverified", "position")},
                 "now": now})
            new_id[(r["wb_scope"], r["wb_key"], r["tk_slug"])] = res.scalar()

        # Reancora o estado dos pares que sobreviveram; descarta o resto.
        for old_id, key in old_key.items():
            target = new_id.get(key)
            if target is None:
                conn.execute(text("DELETE FROM plugin_trackify_field_state "
                                  "WHERE map_id = :m"), {"m": old_id})
            elif target != old_id:
                conn.execute(text("UPDATE plugin_trackify_field_state "
                                  "SET map_id = :new WHERE map_id = :old"),
                             {"new": target, "old": old_id})


def bump(map_id: int, counter: str, *, stamp: str = "", error: str = "") -> None:
    """Incrementa UM contador. Nunca reescreve a linha inteira: o save da tela e
    três tasks de fundo escrevem nesta tabela e se sobreporiam."""
    if counter not in ("pushed_ok", "pushed_err", "pulled_ok",
                       "pulled_rejected", "conflicts"):
        return
    sets = [f"{counter} = {counter} + 1", "updated_at = :now"]
    params: dict = {"now": time.time(), "id": map_id}
    if stamp in ("last_push_at", "last_pull_at"):
        sets.append(f"{stamp} = :now")
    sets.append("last_error = :err")
    params["err"] = (error or "")[:500]
    try:
        with make_plugin_db() as conn:
            conn.execute(text(f"UPDATE plugin_trackify_field_map SET {', '.join(sets)} "
                              f"WHERE id = :id"), params)
    except Exception:  # noqa: BLE001
        logger.debug("trackify: falha ao atualizar contador do mapeamento", exc_info=True)
