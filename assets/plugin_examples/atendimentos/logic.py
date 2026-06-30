"""Núcleo do plugin Atendimentos (auto-contido, sem imports de irmãos).

Concentra TODA a lógica de dados + regras para que ``events.py`` / ``filters.py``
/ ``routes.py`` sejam casca fina. Importa só absoluto (``plugins.context``,
``db.repositories``, SQLAlchemy), então é importável standalone nos testes.

Conceito:
- **Atendimento** (``plugin_atendimentos_atendimentos``) agrupa N conversas de UM
  contato; no máximo 1 ABERTO por contato (índice único parcial). Ciclo próprio
  (aberto/fechado), independente do status da conversa.
- **Vínculo / conversa-ciclo** (``plugin_atendimentos_conversas``) liga uma conversa
  do core ao atendimento — é o "informações_conversa_atendimento" do diagrama.
- **Rótulo FIXO** (OBS) + Início/Fim/Atendente/ID: Início/Fim/Atendente/ID vêm das
  colunas (``started_at``/``ended_at``/``assignee_name``/``atendimento_id``); OBS é um
  rótulo semeado no core (is_system) cujo VALOR é roteado para a coluna ``obs``.
- **DEFINIÇÕES dos rótulos** (conversa + atendimento): vivem no CORE, em
  ``custom_attribute_definitions`` (escopos ``conversation``/``attendance``) — criadas/
  editadas na tela "Atributos Personalizados". O plugin só CONSOME via ``get_field_defs``
  (mapeando tipo e usando ``attribute_key`` como ``id``/``def_id``).
- **VALORES dos rótulos EXTRAS**: normalizados em ``plugin_atendimentos_campos_extras``
  (conversa) / ``plugin_atendimentos_atendimento_extras`` (atendimento) — UMA linha por
  dono + def, com um JSON ``{type, name, label, value}``, chaveada por ``def_id`` (=
  ``attribute_key``). Soft-delete da def no core some da UI; a linha PERMANECE na tabela
  do plugin (recuperável só pelo banco, ver ``_visible_extras``).
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid

from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError

from plugins.context import broadcast, make_plugin_db
from db.repositories import (config_repo, contact_repo, conversation_repo,
                             custom_attribute_repo, message_repo)
from db.tables import conversations as _conversations_tbl

logger = logging.getLogger(__name__)

PLUGIN_ID = "atendimentos"
SCOPES = ("atendimento", "conversa")
EXTRA_SCOPES = ("atendimento", "conversa")  # ambos têm rótulos extras
FIELD_TYPES = {"text", "textarea", "select", "radio", "checkbox"}
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,48}$")
_BACKFILL_FLAG = f"plugin.{PLUGIN_ID}.campos_extras_backfilled"
# Backfill one-time dos valores que o ext_demo gravou em conversations.custom_attributes.
_CA_BACKFILL_FLAG = f"plugin.{PLUGIN_ID}.custom_attrs_backfilled"
# Liga/desliga o espelho dos campos de resolução no core (conversations.custom_attributes).
_MIRROR_FLAG = f"plugin.{PLUGIN_ID}.mirror_custom_attributes"

# Visualizações personalizadas do Kanban (abas de "Agrupar por"). Nome interno (não vem
# de input) → seguro em f-string SQL.
_VIEWS_TABLE = "plugin_atendimentos_kanban_views"
_VIEW_GROUP_BY = {"status", "atendente", "data", "attr"}
_VIEW_SCOPES = {"personal", "team"}
_VIEW_DATE_MODES = {"dia", "faixas", "mes"}
# Teto interno de varredura ao filtrar por atributo (o filtro é em Python, antes do corte
# final) — limita o custo quando a aba usa filtro por atributo de conversa.
_ATTR_SCAN_CAP = 2000

# Tabela de valores de extras por escopo — SEPARADAS: os extras do atendimento e os
# da conversa vivem em tabelas distintas, chaveadas pelo seu próprio dono (atendimento_id
# vs conversa-ciclo id). Nomes internos (não vêm de input) → seguro em f-string SQL.
_EXTRAS_TABLE = {
    "atendimento": ("plugin_atendimentos_atendimento_extras", "atendimento_id"),
    "conversa": ("plugin_atendimentos_campos_extras", "conversa_id"),
}


def now() -> float:
    return time.time()


# ── Rótulos FIXOS (não-deletáveis) ───────────────────────────────────────────
# Único rótulo FIXO em cada escopo: OBS (texto digitado pelo operador). ID, Atendente,
# Início e Fim NÃO são rótulos — vêm automáticos nas colunas (PK/FK, opened_at/started_at,
# closed_at/ended_at, assignee_name do operador) e seguem exibidos no cabeçalho/tabela.
FIXED_FIELD_DEFS = {
    "atendimento": [
        {"id": "fixed_obs", "key": "obs", "label": "Observações", "type": "textarea",
         "options": [], "required": False, "fixed": True, "readonly": False},
    ],
    "conversa": [
        {"id": "fixed_obs", "key": "obs", "label": "Observações", "type": "textarea",
         "options": [], "required": False, "fixed": True, "readonly": False},
    ],
}

# Keys reservadas pelos fixos por escopo (um extra nunca pode usá-las).
FIXED_KEYS = {scope: {d["key"] for d in defs} for scope, defs in FIXED_FIELD_DEFS.items()}

# Extras default por escopo. Valem até o operador editar na tela de config.
# (As Observações de cada escopo são o rótulo FIXO OBS, não extras.)
DEFAULT_EXTRA_DEFS = {
    "atendimento": [
        {"id": "def_motivo_abertura", "key": "motivo_abertura", "label": "Motivo de abertura",
         "type": "select",
         "options": ["Entrou em contato", "Dúvida", "Reclamação", "Outro"], "required": False},
        {"id": "def_resultado", "key": "resultado", "label": "Resultado do atendimento",
         "type": "select",
         "options": ["Interessado em contratar", "Suporte comercial",
                     "Não houve atendimento", "Sem retorno"], "required": False},
        {"id": "def_tipo", "key": "tipo", "label": "Tipo atendimento", "type": "radio",
         "options": ["Suporte", "Comercial"], "required": False},
        {"id": "def_curso_interesse", "key": "curso_interesse", "label": "Curso de interesse",
         "type": "checkbox", "options": [], "required": False},
    ],
    "conversa": [
        {"id": "def_resultado", "key": "resultado", "label": "Resultado", "type": "select",
         "options": ["Resolvido", "Pendente", "Sem retorno"], "required": True},
    ],
}


def _defs_key(scope: str) -> str:
    return f"plugin.{PLUGIN_ID}.field_defs_{scope}"


def _fixed_required_key(scope: str) -> str:
    return f"plugin.{PLUGIN_ID}.fixed_required_{scope}"


def _new_def_id() -> str:
    return "x" + uuid.uuid4().hex[:16]


def get_fixed_defs(scope: str) -> list[dict]:
    """Rótulos fixos do escopo. O ÚNICO atributo editável é `required` (persistido em
    config) — label/tipo/opções são imutáveis. Required = "deve estar sempre preenchido"
    (checkbox é exceção); para os fixos readonly é checado contra a coluna no fechamento."""
    req = set(config_repo.get(_fixed_required_key(scope), []) or [])
    out = []
    for d in FIXED_FIELD_DEFS.get(scope, []):
        nd = dict(d)
        nd["required"] = nd["key"] in req
        out.append(nd)
    return out


def get_extra_defs(scope: str) -> list[dict]:
    """Definições dos rótulos EXTRAS do escopo (default se nunca configurado).
    As DEFINIÇÕES vivem no config do plugin (``plugin.atendimentos.field_defs_<scope>``)."""
    if scope not in EXTRA_SCOPES:
        return []
    raw = config_repo.get(_defs_key(scope), None)
    if not isinstance(raw, list):
        raw = [dict(d) for d in DEFAULT_EXTRA_DEFS.get(scope, [])]
    out: list[dict] = []
    for d in raw:
        nd = dict(d)
        nd.setdefault("id", "def_" + str(nd.get("key") or ""))
        nd.setdefault("type", "text")
        nd.setdefault("options", [])
        nd.setdefault("required", False)
        nd.setdefault("label", nd.get("key"))
        nd["fixed"] = False
        out.append(nd)
    return out


def get_field_defs(scope: str) -> list[dict]:
    """Rótulos FIXOS (não-deletáveis) + EXTRAS, nessa ordem."""
    if scope not in SCOPES:
        return []
    return get_fixed_defs(scope) + get_extra_defs(scope)


def _normalize_extra_def(d: dict) -> dict:
    key = str((d or {}).get("key") or "").strip()
    if not _KEY_RE.match(key):
        raise ValueError(f"Chave de campo inválida: '{key}' (use snake_case: ^[a-z][a-z0-9_]*)")
    ftype = str((d or {}).get("type") or "text").strip()
    if ftype not in FIELD_TYPES:
        raise ValueError(f"Tipo de campo inválido: '{ftype}'")
    opts = d.get("options") or []
    if ftype in ("select", "radio") and not isinstance(opts, list):
        raise ValueError(f"Campo '{key}': opções devem ser uma lista")
    did = str((d or {}).get("id") or "").strip() or _new_def_id()
    return {
        "id": did,
        "key": key,
        "label": str(d.get("label") or key),
        "type": ftype,
        "options": [str(o) for o in opts] if isinstance(opts, list) else [],
        "required": bool(d.get("required")),
        "fixed": False,
    }


def set_field_defs(scope: str, defs: list) -> list[dict]:
    """Persiste os rótulos EXTRAS + o flag `required` dos rótulos FIXOS. Para os fixos,
    só `required` é gravado (label/tipo/opções são imutáveis e não-deletáveis). Para os
    extras, gera `id` novo p/ defs sem id e preserva o existente — é isso que torna a
    recuperação de um rótulo apagado impossível pela interface."""
    if scope not in SCOPES:
        raise ValueError(f"Escopo inválido: '{scope}'")
    if not isinstance(defs, list):
        raise ValueError("defs deve ser uma lista")
    fixed_keys = FIXED_KEYS.get(scope, set())
    # Flag de obrigatório dos rótulos FIXOS (único atributo editável neles).
    fixed_required = sorted({
        str((d or {}).get("key") or "").strip()
        for d in defs
        if str((d or {}).get("key") or "").strip() in fixed_keys and (d or {}).get("required")
    })
    config_repo.set(_fixed_required_key(scope), fixed_required)
    # Rótulos EXTRAS (ignora os fixos).
    out: list[dict] = []
    seen_keys: set[str] = set()
    seen_ids: set[str] = set()
    for d in defs:
        key = str((d or {}).get("key") or "").strip()
        if key in fixed_keys or (d or {}).get("fixed"):
            continue  # rótulo fixo não é gerenciado aqui (só o `required` acima)
        nd = _normalize_extra_def(d)
        if nd["key"] in seen_keys:
            raise ValueError(f"Campo duplicado: '{nd['key']}'")
        if nd["id"] in seen_ids:
            nd["id"] = _new_def_id()
        seen_keys.add(nd["key"])
        seen_ids.add(nd["id"])
        out.append(nd)
    config_repo.set(_defs_key(scope), out)
    if scope == "conversa":
        sync_core_conversa_defs()  # mantém os atributos de conversa do core em dia
    return get_field_defs(scope)


def required_keys(scope: str) -> list[str]:
    return [d["key"] for d in get_field_defs(scope) if d.get("required")]


def normalize_values(scope: str, values: dict) -> tuple[dict, str | None]:
    """Mantém só chaves editáveis (não-readonly), coage checkbox→bool, exige os
    obrigatórios EDITÁVEIS (obs + extras). Os fixos readonly (atendente/início/…) são
    checados contra a coluna no fechamento (ver ``_missing_required``), pois não vêm
    no formulário. Checkbox conta sempre como preenchido."""
    defs = {d["key"]: d for d in get_field_defs(scope) if not d.get("readonly")}
    values = values or {}
    clean: dict = {}
    for key, d in defs.items():
        v = values.get(key)
        if d["type"] == "checkbox":
            clean[key] = bool(v)
            continue
        if v is None:
            v = ""
        clean[key] = v
    for key, d in defs.items():
        if not d.get("required") or d["type"] == "checkbox":
            continue
        v = clean.get(key)
        if v is None or str(v).strip() == "":
            return clean, f"Campo obrigatório não preenchido: {d.get('label', key)}"
    return clean, None


def _effective_values(scope: str, entity: dict) -> dict:
    """Valores efetivos p/ checar `required`: OBS (coluna obs) + extras (entity['fields'])
    — os únicos rótulos obrigatórios-ável. Usado nos pontos de fechamento."""
    entity = entity or {}
    eff = {"obs": entity.get("obs") or ""}
    eff.update(entity.get("fields") or {})
    return eff


def _missing_required(scope: str, eff: dict) -> str | None:
    """1ª mensagem de obrigatório vazio em ``eff`` (fixos + extras), ou None.
    Checkbox conta sempre como preenchido."""
    for d in get_field_defs(scope):
        if not d.get("required") or d.get("type") == "checkbox":
            continue
        v = eff.get(d["key"])
        if v is None or str(v).strip() == "":
            return f"Campo obrigatório não preenchido: {d.get('label', d['key'])}"
    return None


# ── Campos EXTRAS (valores normalizados, 1 linha por conversa-ciclo + def) ────

def _extra_value_type(ftype: str) -> str:
    """Tipo de VALOR no JSON (fiel ao diagrama: string/boolean)."""
    return "boolean" if ftype == "checkbox" else "string"


def _extras_payload(d: dict, value) -> dict:
    """JSON auto-descritivo gravado na linha — permite recuperar o rótulo (label)
    e o tipo direto do banco mesmo depois da definição ser apagada."""
    return {
        "type": _extra_value_type(d.get("type") or "text"),
        "name": d.get("key"),
        "label": d.get("label") or d.get("key"),
        "value": value,
    }


def upsert_extra(conn, scope: str, owner_id: int, d: dict, value) -> None:
    """INSERT … ON CONFLICT(owner, def_id) na tabela do escopo — portável SQLite + Postgres."""
    table, owner_col = _EXTRAS_TABLE[scope]
    ts = now()
    conn.execute(
        text(f"INSERT INTO {table} ({owner_col}, def_id, payload, created_at, updated_at) "
             f"VALUES (:oid, :did, :p, :ts, :ts) "
             f"ON CONFLICT ({owner_col}, def_id) DO UPDATE SET "
             f"payload = excluded.payload, updated_at = excluded.updated_at"),
        {"oid": owner_id, "did": d["id"],
         "p": json.dumps(_extras_payload(d, value), ensure_ascii=False), "ts": ts},
    )


def _visible_extras(scope: str, owner_ids: list[int]) -> dict[int, dict]:
    """{owner_id: {key: value}} SÓ dos extras (do escopo) cuja def AINDA existe. As
    linhas de defs apagadas/órfãs ficam de fora (recuperáveis só pelo banco) — é o que
    garante que um rótulo deletado some do histórico e não volte pela interface."""
    ids = [int(c) for c in (owner_ids or [])]
    out: dict[int, dict] = {i: {} for i in ids}
    table, owner_col = _EXTRAS_TABLE.get(scope, (None, None))
    defs = {d["id"]: d for d in get_extra_defs(scope)}
    if not ids or not defs or not table:
        return out
    with make_plugin_db() as conn:
        rows = conn.execute(
            text(f"SELECT {owner_col} AS owner_id, def_id, payload FROM {table} "
                 f"WHERE {owner_col} IN :ids").bindparams(bindparam("ids", expanding=True)),
            {"ids": ids},
        ).mappings().all()
    for r in rows:
        d = defs.get(r["def_id"])
        if not d:
            continue  # def apagada/órfã → não exibe
        try:
            val = json.loads(r["payload"]).get("value")
        except (json.JSONDecodeError, TypeError):
            val = None
        out.setdefault(int(r["owner_id"]), {})[d["key"]] = val
    return out


# ── Mapeamento de linhas ──────────────────────────────────────────────────────

def _atend_dict(row, extras: dict | None = None) -> dict:
    d = dict(row)
    d["obs"] = d.get("obs") or ""
    if extras is None:
        extras = _visible_extras("atendimento", [d["id"]]).get(d["id"], {})
    d["fields"] = extras  # só extras (do atendimento) com def atual (key → value)
    return d


def _conversa_dict(row, extras: dict | None = None) -> dict:
    d = dict(row)
    d["obs"] = d.get("obs") or ""
    if extras is None:
        extras = _visible_extras("conversa", [d["id"]]).get(d["id"], {})
    d["fields"] = extras  # só extras (da conversa) com def atual (key → value)
    return d


# ── Espelho no core (conversations.custom_attributes) ─────────────────────────
# Além das tabelas do plugin, os campos EDITÁVEIS de resolução da conversa são
# espelhados em ``conversations.custom_attributes`` do core — integra conversa↔
# atendimento, fica queryável/filtrável pelo core e SOBREVIVE se o plugin desativar
# (herdado do ext_demo). As chaves são registradas como definições de atributo
# (``applies_to="conversation"``) p/ aparecerem no painel de info. Desligável via o
# setting ``mirror_custom_attributes``.

_CORE_ATTR_TYPE = {"checkbox": "checkbox"}  # demais tipos do plugin → "text"


def _core_attr_type(ftype: str) -> str:
    return _CORE_ATTR_TYPE.get(ftype or "text", "text")


def _mirror_value(d: dict, value):
    """Valor a gravar no custom_attributes do core: checkbox→bool, demais→string."""
    if (d.get("type") or "text") == "checkbox":
        return bool(value)
    return "" if value is None else str(value)


def sync_core_conversa_defs() -> None:
    """Registra (idempotente) as defs EDITÁVEIS da conversa como atributos de conversa
    no core, p/ os valores espelhados aparecerem/serem editáveis no painel de info.
    ``ensure_system_definition`` é no-op se a def já existe (respeita edição/remoção do
    usuário) — best-effort, nunca quebra o fluxo de resolução."""
    try:
        for i, d in enumerate(get_field_defs("conversa")):
            if d.get("readonly"):
                continue
            custom_attribute_repo.ensure_system_definition(
                attribute_key=d["key"], display_name=d.get("label") or d["key"],
                type=_core_attr_type(d.get("type")), applies_to="conversation", position=i)
    except Exception as e:  # noqa: BLE001
        logger.debug("atendimentos: sync_core_conversa_defs falhou: %s", e)


def mirror_conversa_to_core(conversation_id: int, clean: dict) -> None:
    """Espelha os valores limpos da resolução (obs + extras editáveis) em
    ``conversations.custom_attributes`` (merge). Best-effort; respeita o toggle."""
    try:
        if not config_repo.get(_MIRROR_FLAG, True):
            return
        defs = {d["key"]: d for d in get_field_defs("conversa") if not d.get("readonly")}
        partial = {k: _mirror_value(defs[k], v)
                   for k, v in (clean or {}).items() if k in defs}
        if not partial:
            return
        sync_core_conversa_defs()  # garante que as chaves existam como atributos
        custom_attribute_repo.set_values(_conversations_tbl, int(conversation_id), partial)
    except Exception as e:  # noqa: BLE001
        logger.debug("atendimentos: mirror_conversa_to_core falhou: %s", e)


# ── Atendimento: leitura/criação ──────────────────────────────────────────────

def _select_open_atendimento(contact_id: int) -> dict | None:
    with make_plugin_db() as conn:
        row = conn.execute(
            text("SELECT * FROM plugin_atendimentos_atendimentos "
                 "WHERE contact_id = :cid AND status = 'aberto' "
                 "ORDER BY opened_at DESC"),
            {"cid": contact_id},
        ).mappings().first()
    return _atend_dict(row) if row else None


def get_open_atendimento_for_contact(contact_id: int) -> dict | None:
    return _select_open_atendimento(contact_id)


def get_atendimento(atid: int) -> dict | None:
    with make_plugin_db() as conn:
        row = conn.execute(
            text("SELECT * FROM plugin_atendimentos_atendimentos WHERE id = :id"),
            {"id": atid},
        ).mappings().first()
    return _atend_dict(row) if row else None


def ensure_atendimento_for_contact(contact_id: int, phone: str = "", name: str = "",
                                   conversation_id: int | None = None,
                                   announce_open: bool = False) -> dict:
    """Get-or-create do atendimento ABERTO do contato (race-safe via índice parcial).

    Quando ``announce_open`` e ESTA chamada criou o atendimento, grava UMA nota privada
    marcando a abertura com um ID pesquisável (ver ``_write_open_note``). Quem perde a
    corrida (re-seleciona o existente) não grava → idempotente, 1 nota por atendimento."""
    existing = _select_open_atendimento(contact_id)
    if existing:
        return existing
    ts = now()
    created = False
    try:
        with make_plugin_db() as conn:
            conn.execute(
                text("INSERT INTO plugin_atendimentos_atendimentos "
                     "(contact_id, contact_phone, contact_name, status, fields, "
                     " opened_at, created_at, updated_at) "
                     "VALUES (:cid, :phone, :name, 'aberto', '{}', :ts, :ts, :ts)"),
                {"cid": contact_id, "phone": phone or "", "name": name or "", "ts": ts},
            )
        created = True
    except IntegrityError:
        pass  # perdeu a corrida → o vencedor já existe; re-seleciona abaixo
    at = _select_open_atendimento(contact_id)
    if created and at and announce_open:
        _write_open_note(at, conversation_id)
        # Card de sistema "Atendimento aberto" no fio da conversa (igual ao de resolver
        # conversa). Só na CRIAÇÃO real (não em re-seleção do existente nem no backfill).
        _emit_atend_notice("atendimento_opened", conversation_id=conversation_id,
                           contact_id=at.get("contact_id"),
                           phone=at.get("contact_phone") or None)
    return at


def _write_open_note(at: dict, conversation_id: int | None) -> None:
    """Nota PRIVADA (painel-only, NÃO vai ao cliente) marcando a ABERTURA do atendimento
    com um ID pesquisável e não-editável pela interface:
    ``ATD-AAAAMMDD-HHMMSS-<id_conversa>`` (data/hora da abertura + id da conversa que
    disparou). Best-effort: qualquer falha só loga em debug."""
    try:
        from plugins.context import get_deps
        deps = get_deps()
        agent_handler = getattr(deps, "agent_handler", None) if deps else None
        if not agent_handler:
            return
        contact_id = (at or {}).get("contact_id")
        phone = (at or {}).get("contact_phone") or ""
        if not phone:
            phone = (contact_repo.get(contact_id) or {}).get("phone") or ""
        if not phone:
            return
        lt = time.localtime(float((at or {}).get("opened_at") or now()))
        atd_id = (f"ATD-{lt.tm_year:04d}{lt.tm_mon:02d}{lt.tm_mday:02d}-"
                  f"{lt.tm_hour:02d}{lt.tm_min:02d}{lt.tm_sec:02d}-{conversation_id or 0}")
        text_p = f"🔖 Atendimento aberto · {atd_id}"
        channel_id = _channel_for_contact(contact_id)
        cm = agent_handler._get_contact(phone, channel_id=channel_id)
        cm.add_message("private_note", text_p)
        saved = message_repo.get_last(cm.id)
        note = {"role": "private_note", "content": text_p,
                "ts": (saved or {}).get("ts", now()), "status": None}
        if saved and saved.get("_id"):
            note["_id"] = saved["_id"]
        broadcast("new_message", {"phone": phone, "message": note})
    except Exception as e:  # noqa: BLE001
        logger.debug("atendimentos: nota de abertura falhou: %s", e)


# ── Avisos de sistema no fio da conversa (cards conversation_event) ───────────
# Marca a ABERTURA e a FINALIZAÇÃO do atendimento como cards de sistema no chat —
# mesmo visual dos avisos de resolver/reabrir CONVERSA (plano 12). O plugin REGISTRA
# seu próprio grupo + tipos no registry do core (``server.system_notices``) via
# ``plugins.context.register_notice*`` — SEM dar patch no core. Gate por config
# namespaceada do plugin (``plugin.atendimentos.system_notice_lifecycle``, default ON).
# Late import do ``server``: logic.py segue importável standalone nos testes.

_NOTICE_GROUP = "atendimento_lifecycle"
_NOTICE_CONFIG_KEY = f"plugin.{PLUGIN_ID}.system_notice_lifecycle"


def _f_atendimento_opened(actor=None, **_) -> str:
    return f"📂 {actor} abriu o atendimento." if actor else "📂 Atendimento aberto."


def _f_atendimento_closed(actor=None, **_) -> str:
    return f"🏁 {actor} finalizou o atendimento." if actor else "🏁 Atendimento finalizado."


def register_system_notices() -> None:
    """Registra (idempotente) o grupo + os 2 tipos de aviso do atendimento no core.
    Best-effort: falha (ex.: ``server`` ausente nos testes) nunca quebra o startup."""
    try:
        from plugins.context import register_notice_group, register_notice
        register_notice_group(_NOTICE_GROUP, "Atendimento (abrir/finalizar)",
                              config_key=_NOTICE_CONFIG_KEY, default=True)
        register_notice("atendimento_opened", _NOTICE_GROUP, _f_atendimento_opened)
        register_notice("atendimento_closed", _NOTICE_GROUP, _f_atendimento_closed)
    except Exception as e:  # noqa: BLE001
        logger.debug("atendimentos: register_system_notices falhou: %s", e)


def _emit_atend_notice(event_type: str, *, conversation_id: int | None = None,
                       contact_id: int | None = None, phone: str | None = None,
                       actor: str | None = None) -> None:
    """Emite um card ``conversation_event`` no fio da conversa (gate de config no core).
    Com ``conversation_id`` ancora naquela conversa; senão resolve a do contato
    (aberta→última). Best-effort: nunca levanta (um aviso jamais quebra a ação)."""
    try:
        from server import system_notices
        if conversation_id is not None:
            system_notices.emit_conversation_notice(
                event_type=event_type, conversation_id=conversation_id,
                contact_id=contact_id, phone=phone, actor=actor)
        elif contact_id is not None:
            system_notices.emit_for_contact(
                event_type=event_type, contact_id=contact_id, phone=phone, actor=actor)
    except Exception as e:  # noqa: BLE001
        logger.debug("atendimentos: emitir aviso %s falhou: %s", event_type, e)


def update_atendimento_fields(atid: int, values: dict, assignee_user_id: int | None = None,
                              assignee_name: str = "") -> tuple[dict | None, str | None]:
    at = get_atendimento(atid)
    if not at:
        return None, "Atendimento não encontrado."
    # OBS vai na coluna fixa; os demais rótulos extras vão na tabela do atendimento.
    # Salvar parcial é permitido (required só é exigido ao FECHAR) → mescla com o atual.
    merged = {**(at.get("fields") or {}), "obs": at.get("obs") or "", **(values or {})}
    clean, _err = normalize_values("atendimento", merged)
    obs_val = str(clean.get("obs") or "")
    extra_defs = get_extra_defs("atendimento")
    ts = now()
    with make_plugin_db() as conn:
        conn.execute(
            text("UPDATE plugin_atendimentos_atendimentos SET obs = :obs, "
                 "assignee_user_id = COALESCE(:auid, assignee_user_id), "
                 "assignee_name = CASE WHEN :aname <> '' THEN :aname ELSE assignee_name END, "
                 "updated_at = :ts WHERE id = :id"),
            {"obs": obs_val, "auid": assignee_user_id, "aname": assignee_name or "",
             "ts": ts, "id": atid},
        )
        for d in extra_defs:
            if d["key"] in clean:
                upsert_extra(conn, "atendimento", atid, d, clean[d["key"]])
    _broadcast_changed(at["contact_id"], atid)
    return get_atendimento(atid), None


def _open_cycles_of_atendimento(atid: int) -> list[dict]:
    """Ciclos (conversas) ABERTOS (ended_at NULL) deste atendimento."""
    with make_plugin_db() as conn:
        rows = conn.execute(
            text("SELECT * FROM plugin_atendimentos_conversas "
                 "WHERE atendimento_id = :id AND ended_at IS NULL "
                 "ORDER BY started_at DESC, id DESC"),
            {"id": atid},
        ).mappings().all()
    return [dict(r) for r in rows]


def close_atendimento(atid: int, assignee_user_id: int | None = None,
                      assignee_name: str = "") -> tuple[dict | None, str | None]:
    at = get_atendimento(atid)
    if not at:
        return None, "Atendimento não encontrado."
    if at["status"] == "fechado":
        return at, None
    # Só finaliza quando a ÚLTIMA conversa do atendimento estiver resolvida: se há ciclo
    # aberto, força resolver antes (a UI abre o popup "Resolver conversa"). HTTP 400.
    if _open_cycles_of_atendimento(atid):
        return None, ("Existe uma conversa aberta neste atendimento — "
                      "resolva-a antes de finalizar.")
    # Exige os rótulos OBRIGATÓRIOS (OBS + extras) antes de fechar — lidos do que já
    # está salvo (a UI grava os campos antes de fechar).
    err = _missing_required("atendimento", _effective_values("atendimento", at))
    if err:
        return None, err
    ts = now()
    with make_plugin_db() as conn:
        conn.execute(
            text("UPDATE plugin_atendimentos_atendimentos SET status = 'fechado', "
                 "closed_at = :ts, updated_at = :ts, "
                 "assignee_user_id = COALESCE(:auid, assignee_user_id), "
                 "assignee_name = CASE WHEN :aname <> '' THEN :aname ELSE assignee_name END "
                 "WHERE id = :id"),
            {"ts": ts, "auid": assignee_user_id, "aname": assignee_name or "", "id": atid},
        )
    _broadcast_changed(at["contact_id"], atid)
    # Card de sistema "Atendimento finalizado" no fio da conversa (resolve a conversa do
    # contato: aberta→última). Autor = operador que finalizou, quando houver.
    _emit_atend_notice("atendimento_closed", contact_id=at.get("contact_id"),
                       phone=at.get("contact_phone") or None, actor=(assignee_name or None))
    return get_atendimento(atid), None


def reopen_atendimento(atid: int) -> tuple[dict | None, str | None]:
    at = get_atendimento(atid)
    if not at:
        return None, "Atendimento não encontrado."
    if at["status"] == "aberto":
        return at, None
    other = _select_open_atendimento(at["contact_id"])
    if other:
        return None, "Já existe um atendimento aberto para este contato."
    ts = now()
    try:
        with make_plugin_db() as conn:
            conn.execute(
                text("UPDATE plugin_atendimentos_atendimentos SET status = 'aberto', "
                     "closed_at = NULL, updated_at = :ts WHERE id = :id"),
                {"ts": ts, "id": atid},
            )
    except IntegrityError:
        return None, "Já existe um atendimento aberto para este contato."
    _broadcast_changed(at["contact_id"], atid)
    return get_atendimento(atid), None


def _conversation_ids_of_atendimento(atid: int) -> list[int]:
    """conversation_ids (distintos) das conversas-ciclo deste atendimento."""
    with make_plugin_db() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT conversation_id FROM plugin_atendimentos_conversas "
                 "WHERE atendimento_id = :id AND conversation_id IS NOT NULL"),
            {"id": atid},
        ).all()
    return [int(r[0]) for r in rows]


def _propagate_assignee_to_conversations(conv_ids: list[int],
                                         assignee_user_id: int | None) -> None:
    """Espelha o atendente do atendimento nas CONVERSAS do core (``assignee_user_id``)
    — é o que aparece na lista de conversas e no painel "Agente atribuído". Emite o
    MESMO evento WS do core (``conversation_assigned``) p/ atualização ao vivo, com o
    payload que o frontend espera. Best-effort: uma falha nunca quebra a atribuição."""
    for cid in conv_ids:
        try:
            conv = conversation_repo.set_assignee(cid, assignee_user_id)
            if not conv:
                continue
            broadcast("conversation_assigned", {
                "conversation_id": conv.get("id"),
                "display_id": conv.get("display_id"),
                "contact_id": conv.get("contact_id"),
                "status": conv.get("status"),
                "assignee_user_id": conv.get("assignee_user_id"),
                "active_agent_key": conv.get("active_agent_key"),
                "ai_active": conv.get("ai_active"),
                "is_archived": conv.get("is_archived"),
                "inbox_id": conv.get("inbox_id"),
                "ts": now(),
            })
        except Exception as e:  # noqa: BLE001
            logger.debug("atendimentos: propagar assignee p/ conversa %s falhou: %s", cid, e)


def assign_atendimento(atid: int, assignee_user_id: int | None,
                       assignee_name: str = "") -> tuple[dict | None, str | None]:
    """Define o atendente do atendimento (sobrescreve; ``None`` = remove atribuição) E
    PROPAGA para as conversas do core (assignee_user_id) — senão a mudança não aparece
    na lista/painel de conversas. Usado pelo drag-and-drop do kanban "por atendente".
    Diferente de ``update_atendimento_fields``, aqui o assignee é gravado direto (sem
    COALESCE), para permitir LIMPAR a atribuição."""
    at = get_atendimento(atid)
    if not at:
        return None, "Atendimento não encontrado."
    name = assignee_name or "" if assignee_user_id is not None else ""
    ts = now()
    with make_plugin_db() as conn:
        conn.execute(
            text("UPDATE plugin_atendimentos_atendimentos SET assignee_user_id = :auid, "
                 "assignee_name = :aname, updated_at = :ts WHERE id = :id"),
            {"auid": assignee_user_id, "aname": name, "ts": ts, "id": atid},
        )
    # Espelha nas conversas do core (fora da conn do plugin).
    _propagate_assignee_to_conversations(_conversation_ids_of_atendimento(atid), assignee_user_id)
    _broadcast_changed(at["contact_id"], atid)
    return get_atendimento(atid), None


def _hydrate_atendimentos(rows) -> list[dict]:
    """Batch: extras do atendimento + rótulos/atributos da última conversa (evita N+1)."""
    extras = _visible_extras("atendimento", [r["id"] for r in rows])
    out = [_atend_dict(r, extras.get(r["id"], {})) for r in rows]
    _attach_latest_conversa(out)  # conversa_fields + conversa_attrs (última conversa)
    return out


def list_atendimentos(*, status: str | None = None, assignee_user_id: int | None = None,
                      contact_id: int | None = None, q: str | None = None,
                      opened_from: float | None = None, opened_to: float | None = None,
                      attr_filters: dict | None = None,
                      limit: int = 200, offset: int = 0) -> list[dict]:
    where = ["1=1"]
    lim = max(1, min(int(limit or 200), 500))
    off = max(0, int(offset or 0))
    params: dict = {}
    if status in ("aberto", "fechado"):
        where.append("status = :status")
        params["status"] = status
    if assignee_user_id is not None:
        where.append("assignee_user_id = :auid")
        params["auid"] = assignee_user_id
    if contact_id is not None:
        where.append("contact_id = :cid")
        params["cid"] = contact_id
    if q:
        where.append("(contact_name LIKE :q OR contact_phone LIKE :q)")
        params["q"] = f"%{q}%"
    if opened_from is not None:
        where.append("opened_at >= :ofrom")
        params["ofrom"] = float(opened_from)
    if opened_to is not None:
        where.append("opened_at <= :oto")
        params["oto"] = float(opened_to)
    base = ("SELECT * FROM plugin_atendimentos_atendimentos WHERE " + " AND ".join(where)
            + " ORDER BY (status = 'aberto') DESC, opened_at DESC")

    # Filtro por atributo de CONVERSA (valor na última conversa). Como o valor não vive nas
    # colunas do atendimento, filtra-se em Python ANTES do corte: varre um teto interno,
    # atribui conversa_attrs e só então aplica offset/limit (caro só quando a aba usa este
    # filtro — caminho normal continua com LIMIT/OFFSET no SQL).
    af = {k: v for k, v in (attr_filters or {}).items() if k and _KEY_RE.match(k)}
    if af:
        with make_plugin_db() as conn:
            rows = conn.execute(text(base + " LIMIT :scan"),
                                {**params, "scan": _ATTR_SCAN_CAP}).mappings().all()
        out = _hydrate_atendimentos(rows)
        out = [a for a in out
               if all(str((a.get("conversa_attrs") or {}).get(k, "")) == str(v)
                      for k, v in af.items())]
        return out[off:off + lim]

    with make_plugin_db() as conn:
        rows = conn.execute(text(base + " LIMIT :limit OFFSET :offset"),
                            {**params, "limit": lim, "offset": off}).mappings().all()
    return _hydrate_atendimentos(rows)


def _conversation_core_attrs(conv_ids: list[int]) -> dict[int, dict]:
    """{conversation_id: {key: value}} dos ATRIBUTOS PERSONALIZADOS do core (escopo conversa)
    que NÃO são espelho do plugin — i.e., defs com is_system=0 (criadas na tela do core).
    Lê de ``conversations.custom_attributes``. Batch (evita N+1)."""
    ids = [int(c) for c in (conv_ids or []) if c]
    if not ids:
        return {}
    try:
        defs = custom_attribute_repo.list_definitions(applies_to="conversation")
    except Exception:  # noqa: BLE001
        defs = []
    own = {d["attribute_key"] for d in defs if not d.get("is_system")}
    if not own:
        return {}
    out: dict[int, dict] = {}
    with make_plugin_db() as conn:
        crows = conn.execute(
            text("SELECT id, custom_attributes FROM conversations WHERE id IN :ids")
            .bindparams(bindparam("ids", expanding=True)),
            {"ids": ids},
        ).mappings().all()
    for r in crows:
        ca = r["custom_attributes"]
        ca = ca if isinstance(ca, dict) else _safe_json(ca)
        sub = {k: v for k, v in (ca or {}).items() if k in own}
        if sub:
            out[int(r["id"])] = sub
    return out


def _attach_latest_conversa(items: list[dict]) -> None:
    """Anexa a cada atendimento os valores da ÚLTIMA conversa (ciclo mais recente):
    ``conversa_fields`` (rótulos do plugin do escopo conversa — obs + extras) e
    ``conversa_attrs`` (atributos personalizados do core — is_system=0). Tudo batch."""
    if not items:
        return
    atids = [a["id"] for a in items]
    with make_plugin_db() as conn:
        rows = conn.execute(
            text("SELECT atendimento_id, id, conversation_id, obs FROM plugin_atendimentos_conversas "
                 "WHERE atendimento_id IN :ids ORDER BY started_at DESC, id DESC")
            .bindparams(bindparam("ids", expanding=True)),
            {"ids": atids},
        ).mappings().all()
    latest: dict[int, dict] = {}
    for r in rows:
        latest.setdefault(int(r["atendimento_id"]), dict(r))  # 1º = mais recente (ordem desc)
    conv_extras = _visible_extras("conversa", [r["id"] for r in latest.values()])
    core_attrs = _conversation_core_attrs([r["conversation_id"] for r in latest.values()])
    for a in items:
        lc = latest.get(a["id"])
        if not lc:
            a["conversa_fields"] = {}
            a["conversa_attrs"] = {}
            continue
        cf = dict(conv_extras.get(lc["id"], {}))
        if lc.get("obs"):
            cf["obs"] = lc["obs"]
        a["conversa_fields"] = cf
        a["conversa_attrs"] = core_attrs.get(lc["conversation_id"], {}) if lc.get("conversation_id") else {}


# ── Visualizações personalizadas do Kanban (abas de "Agrupar por") ────────────
# CRUD puro sobre plugin_atendimentos_kanban_views. O GATE (pessoal x equipe, ownership)
# vive na rota (precisa do request p/ checar manage_team_views) — aqui só valida o shape.

def _view_dict(row) -> dict:
    d = dict(row)
    d["filters"] = _safe_json(d.get("filters"))  # JSON TEXT → dict
    return d


def list_kanban_views(*, user_id: int | None = None) -> list[dict]:
    """Visualizações visíveis ao usuário: as PESSOAIS dele + TODAS as de equipe.
    user_id None (legado/open, sem identidade) → todas. Ordena por (scope, position, id)."""
    with make_plugin_db() as conn:
        if user_id is not None:
            rows = conn.execute(
                text(f"SELECT * FROM {_VIEWS_TABLE} "
                     "WHERE scope = 'team' OR owner_user_id = :uid "
                     "ORDER BY scope ASC, position ASC, id ASC"),
                {"uid": int(user_id)},
            ).mappings().all()
        else:
            rows = conn.execute(
                text(f"SELECT * FROM {_VIEWS_TABLE} ORDER BY scope ASC, position ASC, id ASC")
            ).mappings().all()
    return [_view_dict(r) for r in rows]


def get_kanban_view(vid: int) -> dict | None:
    with make_plugin_db() as conn:
        row = conn.execute(
            text(f"SELECT * FROM {_VIEWS_TABLE} WHERE id = :id"), {"id": int(vid)},
        ).mappings().first()
    return _view_dict(row) if row else None


def _validate_view(*, name, scope, group_by, group_attr_key, group_date_mode) -> str | None:
    if not (name or "").strip():
        return "Informe um nome para a visualização."
    if scope not in _VIEW_SCOPES:
        return "Escopo inválido."
    if group_by not in _VIEW_GROUP_BY:
        return "Agrupamento inválido."
    if group_by == "attr" and not _KEY_RE.match(group_attr_key or ""):
        return "Selecione um atributo (lista) válido para agrupar."
    if group_by == "data" and (group_date_mode or "") not in _VIEW_DATE_MODES:
        return "Selecione um modo de data válido (dia, faixas ou mês)."
    return None


def create_kanban_view(*, name, scope="personal", group_by="status", group_attr_key=None,
                       group_date_mode=None, filters=None,
                       owner_user_id=None) -> tuple[dict | None, str | None]:
    name = (name or "").strip()
    err = _validate_view(name=name, scope=scope, group_by=group_by,
                         group_attr_key=group_attr_key, group_date_mode=group_date_mode)
    if err:
        return None, err
    ts = now()
    fjson = json.dumps(filters if isinstance(filters, dict) else {})
    gak = (group_attr_key or None) if group_by == "attr" else None
    gdm = (group_date_mode or None) if group_by == "data" else None
    with make_plugin_db() as conn:
        pos = conn.execute(
            text(f"SELECT COALESCE(MAX(position), -1) + 1 AS p FROM {_VIEWS_TABLE}")
        ).scalar() or 0
        conn.execute(
            text(f"INSERT INTO {_VIEWS_TABLE} "
                 "(name, scope, owner_user_id, group_by, group_attr_key, group_date_mode, "
                 " filters, position, created_at, updated_at) "
                 "VALUES (:name, :scope, :owner, :gby, :gak, :gdm, :filters, :pos, :ts, :ts)"),
            {"name": name, "scope": scope, "owner": owner_user_id, "gby": group_by,
             "gak": gak, "gdm": gdm, "filters": fjson, "pos": int(pos), "ts": ts},
        )
        # Re-seleciona a linha recém-criada de forma portável (SQLite/Postgres): created_at
        # + name (ts é um float preciso do time.time desta chamada) ordenado por id DESC.
        row = conn.execute(
            text(f"SELECT * FROM {_VIEWS_TABLE} WHERE created_at = :ts AND name = :name "
                 "ORDER BY id DESC LIMIT 1"),
            {"ts": ts, "name": name},
        ).mappings().first()
    _broadcast_changed(None, None)
    return (_view_dict(row) if row else None), None


def update_kanban_view(vid, *, name=None, scope=None, group_by=None, group_attr_key=None,
                       group_date_mode=None, filters=None) -> tuple[dict | None, str | None]:
    cur = get_kanban_view(vid)
    if not cur:
        return None, "Visualização não encontrada."
    name = cur["name"] if name is None else (name or "").strip()
    scope = scope or cur["scope"]
    group_by = group_by or cur["group_by"]
    group_attr_key = cur.get("group_attr_key") if group_attr_key is None else group_attr_key
    group_date_mode = cur.get("group_date_mode") if group_date_mode is None else group_date_mode
    err = _validate_view(name=name, scope=scope, group_by=group_by,
                         group_attr_key=group_attr_key, group_date_mode=group_date_mode)
    if err:
        return None, err
    fjson = json.dumps(filters if isinstance(filters, dict) else (cur.get("filters") or {}))
    gak = (group_attr_key or None) if group_by == "attr" else None
    gdm = (group_date_mode or None) if group_by == "data" else None
    ts = now()
    with make_plugin_db() as conn:
        conn.execute(
            text(f"UPDATE {_VIEWS_TABLE} SET name = :name, scope = :scope, group_by = :gby, "
                 "group_attr_key = :gak, group_date_mode = :gdm, filters = :filters, "
                 "updated_at = :ts WHERE id = :id"),
            {"name": name, "scope": scope, "gby": group_by, "gak": gak, "gdm": gdm,
             "filters": fjson, "ts": ts, "id": int(vid)},
        )
    _broadcast_changed(None, None)
    return get_kanban_view(vid), None


def delete_kanban_view(vid) -> tuple[bool, str | None]:
    cur = get_kanban_view(vid)
    if not cur:
        return False, "Visualização não encontrada."
    with make_plugin_db() as conn:
        conn.execute(text(f"DELETE FROM {_VIEWS_TABLE} WHERE id = :id"), {"id": int(vid)})
    _broadcast_changed(None, None)
    return True, None


def set_conversa_attr(atid: int, key: str, value) -> tuple[dict | None, str | None]:
    """Drag no kanban por atributo: grava ``key`` = ``value`` em
    ``conversations.custom_attributes`` da ÚLTIMA conversa (ciclo mais recente) do
    atendimento. ``value`` None/"" remove a chave (cai na coluna "Sem valor"). Espelha o
    padrão de ``mirror_conversa_to_core`` (set_values direto). Retorna o atendimento com
    ``conversa_attrs`` já recarregado."""
    if not _KEY_RE.match(key or ""):
        return None, "Atributo inválido."
    at = get_atendimento(atid)
    if not at:
        return None, "Atendimento não encontrado."
    with make_plugin_db() as conn:
        row = conn.execute(
            text("SELECT conversation_id FROM plugin_atendimentos_conversas "
                 "WHERE atendimento_id = :aid AND conversation_id IS NOT NULL "
                 "ORDER BY started_at DESC, id DESC LIMIT 1"),
            {"aid": int(atid)},
        ).mappings().first()
    conv_id = row["conversation_id"] if row else None
    if not conv_id:
        return None, "Este atendimento ainda não tem conversa vinculada."
    v = None if value in (None, "") else str(value)
    try:
        custom_attribute_repo.set_values(_conversations_tbl, int(conv_id), {key: v})
    except Exception as e:  # noqa: BLE001
        logger.debug("atendimentos: set_conversa_attr falhou: %s", e)
        return None, "Falha ao gravar o atributo."
    _broadcast_changed(at.get("contact_id"), atid)
    out = [get_atendimento(atid)]
    _attach_latest_conversa(out)
    return out[0], None


# ── Conversas do atendimento = CICLOS (aberto→resolvido) ──────────────────────
# Cada linha de plugin_atendimentos_conversas é UM ciclo de atendimento de uma
# conversa: nasce quando o cliente engaja (inbound) e fecha quando o operador
# resolve (ended_at + OBS/extras). O cliente voltar depois inicia um ciclo NOVO no
# mesmo atendimento → várias linhas acumulam. `ended_at IS NULL` = ciclo aberto.

def list_conversas(atid: int) -> list[dict]:
    with make_plugin_db() as conn:
        rows = conn.execute(
            text("SELECT * FROM plugin_atendimentos_conversas "
                 "WHERE atendimento_id = :id ORDER BY started_at ASC, id ASC"),
            {"id": atid},
        ).mappings().all()
    extras = _visible_extras("conversa", [r["id"] for r in rows])  # batch (evita N+1)
    out = [_conversa_dict(r, extras.get(r["id"], {})) for r in rows]
    # Anexa os ATRIBUTOS PERSONALIZADOS do core (is_system=0) de CADA ciclo (lidos de
    # conversations.custom_attributes pelo conversation_id). A tabela de detalhe os mostra
    # numa coluna própria, separados das informações da conversa. Batch (evita N+1).
    core_attrs = _conversation_core_attrs([c.get("conversation_id") for c in out])
    for c in out:
        cid = c.get("conversation_id")
        c["attrs"] = core_attrs.get(int(cid), {}) if cid else {}
    return out


def cycle_anchor(conversa_id: int) -> dict:
    """Deep-link de scroll: devolve a conversa (thread) do ciclo + o ``_id`` (=
    ``messages.id``, é o ``data-mid`` do chat) da PRIMEIRA mensagem a partir do
    ``started_at`` do ciclo — alvo do permalink ``/conversations/<id>?message=<_id>``.
    ``message_id`` é None se a conversa não tiver mensagens. SELECT em ``messages`` do
    core na mesma conexão (mesmo banco)."""
    with make_plugin_db() as conn:
        cyc = conn.execute(
            text("SELECT conversation_id, started_at FROM plugin_atendimentos_conversas "
                 "WHERE id = :id"),
            {"id": conversa_id},
        ).mappings().first()
        if not cyc or cyc["conversation_id"] is None:
            return {"conversation_id": None, "message_id": None}
        conv_id = int(cyc["conversation_id"])
        started = float(cyc["started_at"] or 0)
        row = conn.execute(
            text("SELECT id FROM messages WHERE conversation_id = :cid AND ts >= :ts "
                 "ORDER BY ts ASC, id ASC LIMIT 1"),
            {"cid": conv_id, "ts": started},
        ).first()
        if row is None:  # started_at após a última msg (raro) → cai na última da conversa
            row = conn.execute(
                text("SELECT id FROM messages WHERE conversation_id = :cid "
                     "ORDER BY ts DESC, id DESC LIMIT 1"),
                {"cid": conv_id},
            ).first()
    return {"conversation_id": conv_id, "message_id": (row[0] if row else None)}


def get_latest_cycle(conversation_id: int) -> dict | None:
    """O ciclo mais recente de uma conversa (o que está sendo resolvido/ativo)."""
    with make_plugin_db() as conn:
        row = conn.execute(
            text("SELECT * FROM plugin_atendimentos_conversas "
                 "WHERE conversation_id = :cid ORDER BY started_at DESC, id DESC"),
            {"cid": conversation_id},
        ).mappings().first()
    return _conversa_dict(row) if row else None


def get_open_cycle(conversation_id: int, atendimento_id: int) -> dict | None:
    """O ciclo ABERTO (ended_at NULL) da conversa neste atendimento, se houver."""
    with make_plugin_db() as conn:
        row = conn.execute(
            text("SELECT * FROM plugin_atendimentos_conversas "
                 "WHERE conversation_id = :cid AND atendimento_id = :aid "
                 "AND ended_at IS NULL ORDER BY started_at DESC, id DESC"),
            {"cid": conversation_id, "aid": atendimento_id},
        ).mappings().first()
    return _conversa_dict(row) if row else None


def _count_cycles(conversation_id: int, atendimento_id: int) -> int:
    with make_plugin_db() as conn:
        return conn.execute(
            text("SELECT COUNT(*) FROM plugin_atendimentos_conversas "
                 "WHERE conversation_id = :cid AND atendimento_id = :aid"),
            {"cid": conversation_id, "aid": atendimento_id},
        ).scalar() or 0


def _insert_cycle(conversation_id: int, contact_id: int, atendimento_id: int,
                  assignee_name: str = "") -> dict:
    ts = now()
    with make_plugin_db() as conn:
        conn.execute(
            text("INSERT INTO plugin_atendimentos_conversas "
                 "(atendimento_id, conversation_id, contact_id, assignee_name, "
                 " fields, started_at, created_at, updated_at) "
                 "VALUES (:aid, :cid, :ctid, :aname, '{}', :ts, :ts, :ts)"),
            {"aid": atendimento_id, "cid": conversation_id, "ctid": contact_id,
             "aname": assignee_name or "", "ts": ts},
        )
    return get_open_cycle(conversation_id, atendimento_id)


def ensure_open_cycle(conversation_id: int, contact_id: int, atendimento_id: int,
                      assignee_name: str = "") -> dict:
    """Ciclo aberto da conversa neste atendimento; cria um NOVO se não houver
    (o último foi resolvido ou nunca existiu) — é isso que acumula as linhas."""
    cur = get_open_cycle(conversation_id, atendimento_id)
    if cur:
        return cur
    return _insert_cycle(conversation_id, contact_id, atendimento_id, assignee_name)


def ensure_cycle_exists(conversation_id: int, contact_id: int, atendimento_id: int) -> dict | None:
    """Bootstrap (saída do operador): cria um ciclo SÓ se não houver NENHUM neste
    atendimento — nunca abre um ciclo novo logo após uma resolução."""
    if _count_cycles(conversation_id, atendimento_id) > 0:
        return None
    return _insert_cycle(conversation_id, contact_id, atendimento_id)


def resolve_conversa(conversation_id: int, values: dict, assignee_name: str = "",
                     assignee_user_id: int | None = None) -> tuple[dict | None, str | None]:
    """Fecha o ciclo ABERTO da conversa (Fim + OBS + extras). Cria+fecha um se não houver.
    OBS vai na coluna fixa; cada rótulo extra vai numa linha de campos_extras. Grava o
    AGENTE que resolveu (assignee_user_id + assignee_name) no ciclo."""
    conv = conversation_repo.get(conversation_id)
    if not conv:
        return None, "Conversa não encontrada."
    contact_id = conv["contact_id"]
    contact = contact_repo.get(contact_id) or {}
    at = ensure_atendimento_for_contact(
        contact_id, phone=contact.get("phone", ""), name=_contact_name(contact),
        conversation_id=conversation_id, announce_open=True)
    cycle = (get_open_cycle(conversation_id, at["id"])
             or ensure_open_cycle(conversation_id, contact_id, at["id"], assignee_name))
    clean, err = normalize_values("conversa", values)
    if err:
        return None, err
    ts = now()
    obs_val = str(clean.get("obs") or "")
    extra_defs = get_extra_defs("conversa")
    with make_plugin_db() as conn:
        conn.execute(
            text("UPDATE plugin_atendimentos_conversas SET obs = :obs, ended_at = :ts, "
                 "assignee_user_id = COALESCE(:auid, assignee_user_id), "
                 "assignee_name = CASE WHEN :aname <> '' THEN :aname ELSE assignee_name END, "
                 "updated_at = :ts WHERE id = :id"),
            {"obs": obs_val, "ts": ts, "auid": assignee_user_id,
             "aname": assignee_name or "", "id": cycle["id"]},
        )
        for d in extra_defs:
            if d["key"] in clean:
                upsert_extra(conn, "conversa", cycle["id"], d, clean[d["key"]])
    # Espelha os mesmos campos no core (conversations.custom_attributes) — integra
    # conversa↔atendimento e sobrevive se o plugin for desativado.
    mirror_conversa_to_core(conversation_id, clean)
    _broadcast_changed(contact_id, at["id"])
    return get_latest_cycle(conversation_id), None


# ── Auto-vínculo (event handlers) ─────────────────────────────────────────────

def _contact_name(contact: dict) -> str:
    return str((contact or {}).get("name") or (contact or {}).get("phone") or "")


def _resolve_target(payload: dict):
    """(contact, conv) do contato do payload, ou (None, None) se inaplicável.

    Sync (o bus roda em ``asyncio.to_thread``). Pula grupos e respeita o toggle.
    """
    payload = payload or {}
    if not config_repo.get(f"plugin.{PLUGIN_ID}.auto_link", True):
        return None, None
    if payload.get("is_group"):
        return None, None  # atendimentos são por-contato (1:1)
    phone = payload.get("phone")
    if not phone:
        return None, None
    contact = contact_repo.get_by_phone(phone)
    if not contact:
        return None, None
    conv = (conversation_repo.get_open_for_contact(contact["id"])
            or conversation_repo.get_latest_for_contact(contact["id"]))
    return (contact, conv) if conv else (None, None)


def on_inbound(ctx, payload: dict) -> None:
    """``message.saved`` (cliente engajou) → garante atendimento aberto + ciclo
    ABERTO. Se o último ciclo foi resolvido, abre um NOVO (cliente voltou)."""
    try:
        contact, conv = _resolve_target(payload)
        if not conv:
            return
        at = ensure_atendimento_for_contact(
            contact["id"], phone=contact.get("phone", ""), name=_contact_name(contact),
            conversation_id=conv["id"], announce_open=True)
        ensure_open_cycle(conv["id"], contact["id"], at["id"])
    except Exception as e:  # noqa: BLE001 — um handler que falha nunca quebra o pipeline
        logger.debug("atendimentos.on_inbound falhou: %s", e)


def on_outbound(ctx, payload: dict) -> None:
    """``message.sent`` (operador/IA) → garante atendimento + ciclo de bootstrap,
    mas NUNCA abre um ciclo novo logo após uma resolução (evita ciclo fantasma)."""
    try:
        contact, conv = _resolve_target(payload)
        if not conv:
            return
        at = ensure_atendimento_for_contact(
            contact["id"], phone=contact.get("phone", ""), name=_contact_name(contact),
            conversation_id=conv["id"], announce_open=True)
        ensure_cycle_exists(conv["id"], contact["id"], at["id"])
    except Exception as e:  # noqa: BLE001
        logger.debug("atendimentos.on_outbound falhou: %s", e)


def on_startup(ctx, payload: dict) -> None:
    """``app.startup`` → backfills one-time idempotentes + registro dos atributos de
    conversa no core + registro dos avisos de sistema do atendimento. Boot nunca quebra
    por causa daqui (tudo defensivo)."""
    register_system_notices()  # grupo + tipos de aviso (abrir/finalizar atendimento)
    try:
        _maybe_backfill()  # blob `fields` legado → tabelas normalizadas
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimentos: backfill no startup falhou (segue sem migrar): %s", e)
    try:
        _maybe_backfill_custom_attrs()  # custom_attributes do ext_demo → Atendimentos
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimentos: backfill de custom_attributes falhou: %s", e)
    sync_core_conversa_defs()  # atributos de conversa do core p/ os campos espelhados


# ── Backfill (blob `fields` legado → tabela normalizada) ──────────────────────

def _safe_json(s) -> dict:
    try:
        v = json.loads(s or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return v if isinstance(v, dict) else {}


def _backfill_blob(conn, scope: str, owner_id: int, blob: dict, by_key: dict,
                   obs_table: str, ts: float) -> None:
    """Migra um blob `fields` legado: observacao/observacoes → coluna obs; demais keys
    → tabela de extras do escopo (key com def atual → seu def_id; senão def_id órfão,
    nunca exibido). ON CONFLICT DO NOTHING + guard de obs vazio = idempotente."""
    table, owner_col = _EXTRAS_TABLE[scope]
    for k, v in blob.items():
        if k in ("observacao", "observacoes"):
            conn.execute(
                text(f"UPDATE {obs_table} SET obs = :o, updated_at = :ts "
                     f"WHERE id = :id AND (obs IS NULL OR obs = '')"),
                {"o": str(v or ""), "ts": ts, "id": owner_id})
            continue
        d = by_key.get(k) or {"id": "orphan_" + str(k), "key": str(k),
                              "label": str(k), "type": "text"}
        conn.execute(
            text(f"INSERT INTO {table} ({owner_col}, def_id, payload, created_at, updated_at) "
                 f"VALUES (:oid, :did, :p, :ts, :ts) "
                 f"ON CONFLICT ({owner_col}, def_id) DO NOTHING"),
            {"oid": owner_id, "did": d["id"],
             "p": json.dumps(_extras_payload(d, v), ensure_ascii=False), "ts": ts})


def _maybe_backfill() -> None:
    """Migra, UMA vez, os valores hoje no blob `fields` para as tabelas normalizadas,
    em ambos os escopos (observacao(es) → coluna obs; demais keys → extras do escopo).
    Idempotente: ON CONFLICT DO NOTHING + flag no config + guard de obs vazio."""
    if config_repo.get(_BACKFILL_FLAG, False):
        return
    # Garante ids estáveis nas defs extras atuais dos dois escopos.
    a_by_key = {d["key"]: d for d in set_field_defs("atendimento", get_extra_defs("atendimento"))
                if not d.get("fixed")}
    c_by_key = {d["key"]: d for d in set_field_defs("conversa", get_extra_defs("conversa"))
                if not d.get("fixed")}
    ts = now()
    with make_plugin_db() as conn:
        crows = conn.execute(
            text("SELECT id, fields FROM plugin_atendimentos_conversas")).mappings().all()
        for r in crows:
            _backfill_blob(conn, "conversa", r["id"], _safe_json(r["fields"]),
                           c_by_key, "plugin_atendimentos_conversas", ts)
        arows = conn.execute(
            text("SELECT id, fields FROM plugin_atendimentos_atendimentos")).mappings().all()
        for r in arows:
            _backfill_blob(conn, "atendimento", r["id"], _safe_json(r["fields"]),
                           a_by_key, "plugin_atendimentos_atendimentos", ts)
    config_repo.set(_BACKFILL_FLAG, True)
    logger.info("atendimentos: backfill de campos extras concluído")


# ── Backfill dos dados do ext_demo (conversations.custom_attributes → Atendimentos) ──
# O ext_demo gravava os campos de "Resolver conversa" (CSV, default motivo,observacao)
# direto no core (conversations.custom_attributes). Ao aposentar o ext_demo, migramos
# esses valores para as tabelas do Atendimentos (obs + extras da conversa), UMA vez.

def _ext_demo_resolve_keys() -> list[str]:
    """Chaves que o ext_demo gravava em custom_attributes (CSV das settings dele)."""
    raw = config_repo.get("plugin.ext_demo.resolve_fields", "motivo,observacao")
    return [k.strip() for k in str(raw or "").split(",") if k.strip()]


def _maybe_backfill_custom_attrs() -> None:
    """One-time: migra os valores que o ext_demo gravou em conversations.custom_attributes
    para as tabelas do Atendimentos. SÓ as chaves do ext_demo são migradas (não outros
    atributos do core). observacao/observacoes → coluna obs; demais → extras da conversa
    (garantindo a def antes, p/ não virar órfã). Idempotente: ON CONFLICT DO NOTHING +
    guard de obs vazio (em _backfill_blob) + flag no config."""
    if config_repo.get(_CA_BACKFILL_FLAG, False):
        return
    keys = set(_ext_demo_resolve_keys())
    if not keys:
        config_repo.set(_CA_BACKFILL_FLAG, True)
        return
    # Garante que as chaves não-obs existam como defs extras da conversa (senão o
    # _backfill_blob as gravaria com def_id órfão, e nunca apareceriam na UI).
    obs_like = {"observacao", "observacoes"}
    cur = get_extra_defs("conversa")
    have = {d["key"] for d in cur}
    additions = [{"key": k, "label": k, "type": "text"}
                 for k in keys if k not in obs_like and k not in have and _KEY_RE.match(k)]
    if additions:
        set_field_defs("conversa", cur + additions)
    c_by_key = {d["key"]: d for d in get_extra_defs("conversa")}
    ts = now()
    with make_plugin_db() as conn:
        rows = conn.execute(
            text("SELECT id, contact_id, custom_attributes FROM conversations")
        ).mappings().all()
    # Filtra em Python (portável SQLite/Postgres; custom_attributes pode vir str ou dict).
    candidates = []
    for r in rows:
        attrs = r["custom_attributes"]
        attrs = attrs if isinstance(attrs, dict) else _safe_json(attrs)
        sub = {k: v for k, v in attrs.items() if k in keys}
        if sub:
            candidates.append((r["id"], r["contact_id"], sub))
    for conv_id, contact_id, sub in candidates:
        try:
            contact = contact_repo.get(contact_id) or {}
            at = ensure_atendimento_for_contact(
                contact_id, phone=contact.get("phone", ""), name=_contact_name(contact))
            cycle = (get_latest_cycle(conv_id)
                     or ensure_open_cycle(conv_id, contact_id, at["id"]))
            with make_plugin_db() as conn:
                _backfill_blob(conn, "conversa", cycle["id"], sub, c_by_key,
                               "plugin_atendimentos_conversas", ts)
        except Exception as e:  # noqa: BLE001 — uma conversa que falha não trava o resto
            logger.debug("atendimentos: backfill CA da conversa %s falhou: %s", conv_id, e)
    config_repo.set(_CA_BACKFILL_FLAG, True)
    logger.info("atendimentos: backfill de custom_attributes concluído (%d conversas)",
                len(candidates))


# ── Mensagens de protocolo/avaliação ao FINALIZAR o atendimento ───────────────
# Config (tela do plugin): 2 itens {título, link} — o "normal" vai ao WhatsApp e o
# "privado" vira nota privada (painel-only). Em ambos os links são adicionados, como
# query params, o id do atendente (assignee_id) e um id de protocolo único gerado no
# envio (id_protocol). Disparado pela rota de fechar (em thread), best-effort.

def _proto_key(name: str) -> str:
    return f"plugin.{PLUGIN_ID}.protocol_{name}"


def get_protocol_config() -> dict:
    return {
        "enabled": bool(config_repo.get(_proto_key("enabled"), False)),
        "normal": {
            "title": str(config_repo.get(_proto_key("normal_title"), "") or ""),
            "link": str(config_repo.get(_proto_key("normal_link"), "") or ""),
        },
        "privado": {
            "title": str(config_repo.get(_proto_key("private_title"), "") or ""),
            "link": str(config_repo.get(_proto_key("private_link"), "") or ""),
        },
    }


def set_protocol_config(cfg: dict) -> dict:
    cfg = cfg or {}
    normal = cfg.get("normal") or {}
    priv = cfg.get("privado") or cfg.get("private") or {}
    config_repo.set(_proto_key("enabled"), bool(cfg.get("enabled")))
    config_repo.set(_proto_key("normal_title"), str(normal.get("title") or ""))
    config_repo.set(_proto_key("normal_link"), str(normal.get("link") or ""))
    config_repo.set(_proto_key("private_title"), str(priv.get("title") or ""))
    config_repo.set(_proto_key("private_link"), str(priv.get("link") or ""))
    return get_protocol_config()


def _gen_protocol() -> str:
    """Protocolo único DDMMYYYY-HHMMSS.mmm-RRRRR (ex.: 25062026-135043.597-35828)."""
    import random
    t = time.time()
    lt = time.localtime(t)
    ms = int(round((t - int(t)) * 1000)) % 1000
    return (f"{lt.tm_mday:02d}{lt.tm_mon:02d}{lt.tm_year:04d}-"
            f"{lt.tm_hour:02d}{lt.tm_min:02d}{lt.tm_sec:02d}.{ms:03d}-{random.randint(10000, 99999)}")


def _append_query(url: str, params: dict) -> str:
    from urllib.parse import urlencode
    qs = urlencode({k: ("" if v is None else v) for k, v in params.items()})
    if not qs:
        return url
    return url + ("&" if "?" in url else "?") + qs


def _channel_for_contact(contact_id) -> str:
    """Canal da conversa mais recente do contato (fallback 'default')."""
    try:
        conv = (conversation_repo.get_open_for_contact(contact_id)
                or conversation_repo.get_latest_for_contact(contact_id))
        if conv:
            full = conversation_repo.get_with_channel(conv["id"])
            if full and full.get("channel_id"):
                return full["channel_id"]
    except Exception:  # noqa: BLE001
        pass
    return "default"


def _compose_message(title: str, link: str, params: dict) -> str:
    body = _append_query(link, params)
    return f"{title}\n{body}" if title else body


def send_protocol_on_close(at: dict) -> None:
    """Ao FINALIZAR o atendimento: envia o link normal (WhatsApp) + o link privado
    (nota privada), cada um com assignee_id + id_protocol na URL. Best-effort: nunca
    levanta. No-op se desativado, sem links, ou sem runtime (testes)."""
    try:
        cfg = get_protocol_config()
        if not cfg.get("enabled"):
            return
        normal, priv = cfg["normal"], cfg["privado"]
        if not (normal.get("link") or priv.get("link")):
            return
        from plugins.context import get_deps
        deps = get_deps()
        agent_handler = getattr(deps, "agent_handler", None) if deps else None
        outbound = getattr(deps, "outbound_router", None) if deps else None
        if not agent_handler:
            return
        phone = (at or {}).get("contact_phone") or ""
        if not phone:
            c = contact_repo.get((at or {}).get("contact_id"))
            phone = (c or {}).get("phone") or ""
        if not phone:
            return
        params = {"assignee_id": (at or {}).get("assignee_user_id") or "",
                  "id_protocol": _gen_protocol()}
        channel_id = _channel_for_contact((at or {}).get("contact_id"))

        # 1) Link normal → WhatsApp (envia pelo canal + salva como mensagem do operador).
        if normal.get("link") and outbound:
            text_n = _compose_message(normal.get("title") or "", normal["link"], params)
            try:
                res = outbound.send_text(channel_id, phone, text_n)
                ok = bool(getattr(res, "ok", False))
                mid = getattr(res, "external_msg_id", None)
                msg = agent_handler.save_operator_message(
                    phone, text_n, status="operator" if ok else "failed",
                    msg_id=mid, channel_id=channel_id)
                broadcast("new_message", {"phone": phone, "message": msg})
            except Exception as e:  # noqa: BLE001
                logger.warning("atendimentos: falha ao enviar link de protocolo: %s", e)

        # 2) Link privado → nota privada (painel-only, NÃO vai ao WhatsApp).
        if priv.get("link"):
            text_p = _compose_message(priv.get("title") or "", priv["link"], params)
            try:
                # MESMO channel_id do link normal — senão a nota privada cairia no
                # inbox "default" (ContactMemory resolve a conversa POR inbox) e abriria
                # uma conversa nova em OUTRO canal. Os dois links têm que ir na mesma
                # conversa do mesmo canal.
                cm = agent_handler._get_contact(phone, channel_id=channel_id)
                cm.add_message("private_note", text_p)
                saved = message_repo.get_last(cm.id)
                note = {"role": "private_note", "content": text_p,
                        "ts": (saved or {}).get("ts", now()), "status": None}
                if saved and saved.get("_id"):
                    note["_id"] = saved["_id"]
                broadcast("new_message", {"phone": phone, "message": note})
            except Exception as e:  # noqa: BLE001
                logger.warning("atendimentos: falha ao salvar nota privada de protocolo: %s", e)
    except Exception as e:  # noqa: BLE001
        logger.warning("atendimentos: send_protocol_on_close falhou: %s", e)


# ── Enforcement no backend (filter.conversation.before_status) ────────────────

def _check_before_status(payload: dict):
    if (payload or {}).get("new_status") != "closed":
        return payload  # só fechar é gated; reabrir nunca
    if not config_repo.get(f"plugin.{PLUGIN_ID}.enforce_backend", True):
        return payload
    if not any(d.get("required") for d in get_field_defs("conversa")):
        return payload
    cid = payload.get("conversation_id")
    cycle = get_latest_cycle(cid)
    err = _missing_required("conversa", _effective_values("conversa", cycle or {}))
    if err:
        logger.info("atendimentos: recusando fechar conversa %s — %s", cid, err)
        return None  # → HTTP 403
    return payload


async def before_status(ctx, payload):
    """Filter assíncrono: leituras de DB rodam fora do event loop."""
    import asyncio
    if (payload or {}).get("new_status") != "closed":
        return payload
    return await asyncio.to_thread(_check_before_status, payload)


# ── Util ──────────────────────────────────────────────────────────────────────

def _broadcast_changed(contact_id: int | None, atendimento_id: int | None) -> None:
    broadcast("plugin_atendimentos_changed",
              {"contact_id": contact_id, "atendimento_id": atendimento_id})
