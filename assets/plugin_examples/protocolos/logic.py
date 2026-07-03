"""Núcleo do plugin Protocolos (auto-contido, sem imports de irmãos).

Concentra TODA a lógica de dados + regras para que ``events.py`` / ``filters.py``
/ ``routes.py`` sejam casca fina. Importa só absoluto (``plugins.context``,
``db.repositories``, SQLAlchemy), então é importável standalone nos testes.

Conceito:
- **Protocolo** (``plugin_protocolos_protocolos``) agrupa N atendimentos de UM
  contato; no máximo 1 ABERTO por contato (índice único parcial). Ciclo próprio
  (aberto/fechado), independente do status da atendimento.
- **Vínculo / atendimento-ciclo** (``plugin_protocolos_atendimentos``) liga uma atendimento
  do core ao protocolo — é o "informações_atendimento_protocolo" do diagrama.
- **Rótulo FIXO** (OBS) + Início/Fim/Atendente/ID: Início/Fim/Atendente/ID vêm das
  colunas (``started_at``/``ended_at``/``assignee_name``/``protocolo_id``); OBS é um
  rótulo semeado no core (is_system) cujo VALOR é roteado para a coluna ``obs``.
- **DEFINIÇÕES dos rótulos** (atendimento + protocolo): vivem no CORE, em
  ``custom_attribute_definitions`` (escopos ``conversation``/``attendance``) — criadas/
  editadas na tela "Atributos Personalizados". O plugin só CONSOME via ``get_field_defs``
  (mapeando tipo e usando ``attribute_key`` como ``id``/``def_id``).
- **VALORES dos rótulos EXTRAS**: normalizados em ``plugin_protocolos_campos_extras``
  (atendimento) / ``plugin_protocolos_protocolo_extras`` (protocolo) — UMA linha por
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
                             custom_attribute_repo, user_repo)
from db.tables import conversations as _conversations_tbl

logger = logging.getLogger(__name__)

PLUGIN_ID = "protocolos"
SCOPES = ("protocolo", "atendimento")
EXTRA_SCOPES = ("protocolo", "atendimento")  # ambos têm rótulos extras
FIELD_TYPES = {"text", "textarea", "number", "date", "select", "checkboxes", "radio", "checkbox",
               "atendente"}
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,48}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BACKFILL_FLAG = f"plugin.{PLUGIN_ID}.campos_extras_backfilled"
# Backfill one-time dos valores que o ext_demo gravou em conversations.custom_attributes.
_CA_BACKFILL_FLAG = f"plugin.{PLUGIN_ID}.custom_attrs_backfilled"
# Liga/desliga o espelho dos campos de resolução no core (conversations.custom_attributes).
_MIRROR_FLAG = f"plugin.{PLUGIN_ID}.mirror_custom_attributes"
# One-time: obs deixou de ser rótulo FIXO (coluna própria) e virou rótulo EXTRA comum.
_OBS_MIGRATE_FLAG = f"plugin.{PLUGIN_ID}.obs_to_extra_migrated"

# Visualizações personalizadas do Kanban (abas de "Agrupar por"). Nome interno (não vem
# de input) → seguro em f-string SQL.
_VIEWS_TABLE = "plugin_protocolos_kanban_views"
# Preferência POR-USUÁRIO e POR-VISUALIZAÇÃO dos filtros pré-determinados (pessoal x equipe).
_PREFS_TABLE = "plugin_protocolos_user_view_prefs"
# Sentinela p/ "não informado" no update (distingue de None=todos os filtros, []=nenhum).
_UNSET = object()
_VIEW_GROUP_BY = {"status", "atendente", "data", "attr"}
_VIEW_SCOPES = {"personal", "team"}
# Sub-modos do agrupamento por Data. "personalizado" = janela (from/to) + granularidade.
_VIEW_DATE_MODES = {"dia", "faixas", "mes", "semana", "personalizado"}
# Granularidade do bucket quando o modo é "personalizado".
_VIEW_DATE_GRAINS = {"dia", "semana", "mes"}
_YMD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _valid_ymd(s) -> bool:
    """True se s é uma data 'YYYY-MM-DD' válida (usada na janela do modo personalizado)."""
    if not isinstance(s, str) or not _YMD_RE.match(s):
        return False
    try:
        from datetime import datetime
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False
# Teto interno de varredura ao filtrar por atributo (o filtro é em Python, antes do corte
# final) — limita o custo quando a aba usa filtro por atributo de atendimento.
_ATTR_SCAN_CAP = 2000

# Tabela de valores de extras por escopo — SEPARADAS: os extras do protocolo e os
# da atendimento vivem em tabelas distintas, chaveadas pelo seu próprio dono (protocolo_id
# vs atendimento-ciclo id). Nomes internos (não vêm de input) → seguro em f-string SQL.
_EXTRAS_TABLE = {
    "protocolo": ("plugin_protocolos_protocolo_extras", "protocolo_id"),
    "atendimento": ("plugin_protocolos_campos_extras", "atendimento_id"),
}


def now() -> float:
    return time.time()


# ── Rótulos FIXOS ────────────────────────────────────────────────────────────
# Não há mais rótulos FIXOS: Observações virou um rótulo EXTRA comum (editável/removível),
# semeado por DEFAULT_EXTRA_DEFS com id estável "fixed_obs". ID, Atendente, Início e Fim NÃO
# são rótulos — vêm automáticos nas colunas (PK/FK, opened_at/started_at, closed_at/ended_at,
# assignee_name) e seguem no cabeçalho/tabela. FIXED_FIELD_DEFS fica vazio (mantido só p/ compat
# de get_fixed_defs/FIXED_KEYS).
FIXED_FIELD_DEFS: dict[str, list[dict]] = {
    "protocolo": [],
    "atendimento": [],
}

# Keys reservadas pelos fixos por escopo (vazio agora — nenhuma key é reservada).
FIXED_KEYS = {scope: {d["key"] for d in defs} for scope, defs in FIXED_FIELD_DEFS.items()}

# Extras default por escopo. Valem até o operador editar na tela de config.
# (As Observações de cada escopo são o rótulo FIXO OBS, não extras.)
DEFAULT_EXTRA_DEFS = {
    "protocolo": [
        {"id": "fixed_obs", "key": "obs", "label": "Observações", "type": "textarea",
         "options": [], "required": False},
        {"id": "def_motivo_abertura", "key": "motivo_abertura", "label": "Motivo de abertura",
         "type": "select",
         "options": ["Entrou em contato", "Dúvida", "Reclamação", "Outro"], "required": False},
        {"id": "def_resultado", "key": "resultado", "label": "Resultado do protocolo",
         "type": "select",
         "options": ["Interessado em contratar", "Suporte comercial",
                     "Não houve protocolo", "Sem retorno"], "required": False},
        {"id": "def_tipo", "key": "tipo", "label": "Tipo protocolo", "type": "select",
         "options": ["Suporte", "Comercial"], "required": False},
        {"id": "def_curso_interesse", "key": "curso_interesse", "label": "Curso de interesse",
         "type": "checkbox", "options": [], "required": False},
    ],
    "atendimento": [
        {"id": "fixed_obs", "key": "obs", "label": "Observações", "type": "textarea",
         "options": [], "required": False},
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
    As DEFINIÇÕES vivem no config do plugin (``plugin.protocolos.field_defs_<scope>``)."""
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
        nd.setdefault("regex_pattern", "")   # validação por padrão (text/textarea/number)
        nd.setdefault("regex_cue", "")       # dica do formato mostrada ao usuário
        nd.setdefault("multiple", False)     # só p/ type=checkboxes: permite marcar várias
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
    if ftype in ("select", "radio", "checkboxes") and not isinstance(opts, list):
        raise ValueError(f"Campo '{key}': opções devem ser uma lista")
    did = str((d or {}).get("id") or "").strip() or _new_def_id()
    # "atendente" (atendente NATIVO): sem opções estáticas (a lista vem dos usuários),
    # sem multi e sem regex — só o valor (uid) importa.
    is_atendente = ftype == "atendente"
    return {
        "id": did,
        "key": key,
        "label": str(d.get("label") or key),
        "type": ftype,
        "options": [] if is_atendente else ([str(o) for o in opts] if isinstance(opts, list) else []),
        "required": bool(d.get("required")),
        "regex_pattern": "" if is_atendente else str(d.get("regex_pattern") or "").strip(),
        "regex_cue": "" if is_atendente else str(d.get("regex_cue") or "").strip(),
        "multiple": False if is_atendente else bool(d.get("multiple")),
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
    seen_atendente = False
    for d in defs:
        key = str((d or {}).get("key") or "").strip()
        if key in fixed_keys or (d or {}).get("fixed"):
            continue  # rótulo fixo não é gerenciado aqui (só o `required` acima)
        nd = _normalize_extra_def(d)
        if nd["type"] == "atendente":
            # Só UM rótulo Atendente por escopo (há um só atendente nativo).
            if seen_atendente:
                raise ValueError("Só é permitido um campo do tipo Atendente por escopo.")
            seen_atendente = True
        if nd["key"] in seen_keys:
            raise ValueError(f"Campo duplicado: '{nd['key']}'")
        if nd["id"] in seen_ids:
            nd["id"] = _new_def_id()
        seen_keys.add(nd["key"])
        seen_ids.add(nd["id"])
        out.append(nd)
    config_repo.set(_defs_key(scope), out)
    if scope == "atendimento":
        sync_core_atendimento_defs()  # mantém os atributos de atendimento do core em dia
    return get_field_defs(scope)


def required_keys(scope: str) -> list[str]:
    return [d["key"] for d in get_field_defs(scope) if d.get("required")]


def _is_multi(d: dict) -> bool:
    """Campo cujo VALOR é uma LISTA de opções: 'checkboxes' (sempre) ou 'select' com
    ``multiple`` ligado (a "Lista de seleção" em modo múltiplo). Single select/radio/text
    continuam string."""
    ft = (d or {}).get("type") or "text"
    return ft == "checkboxes" or (ft == "select" and bool((d or {}).get("multiple")))


def _is_filled_extra(d: dict, v) -> bool:
    """Um valor conta como "preenchido"? checkbox (bool) sempre; multi-opção (lista) só
    quando não-vazia; os demais quando string não-vazia."""
    ftype = d.get("type") or "text"
    if ftype == "checkbox":
        return True
    if ftype == "atendente":
        return v is not None and str(v).strip() not in ("", "0")
    if _is_multi(d):
        return isinstance(v, list) and len(v) > 0
    return v is not None and str(v).strip() != ""


def _coerce_extra(d: dict, value) -> tuple:
    """Coage + valida UM valor conforme o tipo do rótulo. Retorna (valor_normalizado,
    erro|None). Valor VAZIO nunca é inválido aqui — o `required` é checado à parte.
    - checkbox → bool; checkboxes → lista de opções (corta p/ 1 quando não-múltiplo);
    - number → string numérica (aceita vírgula); date → 'AAAA-MM-DD';
    - regex_pattern (text/textarea/number) → re.search, com a `regex_cue` na mensagem."""
    ftype = d.get("type") or "text"
    label = d.get("label") or d.get("key")
    if ftype == "checkbox":
        return bool(value), None
    if ftype == "atendente":
        # Valor = uid do atendente nativo (int) ou None p/ "Não atribuído".
        if value in (None, "", "0", 0):
            return None, None
        try:
            return int(value), None
        except (ValueError, TypeError):
            return None, f"'{label}': atendente inválido."
    if _is_multi(d):
        if value is None or value == "":
            vals = []
        elif isinstance(value, list):
            vals = [str(x).strip() for x in value if str(x).strip()]
        else:
            vals = [s.strip() for s in str(value).split(",") if s.strip()]
        opts = d.get("options") or []
        if opts:
            bad = [v for v in vals if v not in opts]
            if bad:
                return vals, f"'{label}': opção inválida ({', '.join(bad)})."
        if not d.get("multiple") and len(vals) > 1:
            vals = vals[:1]
        return vals, None
    s = "" if value is None else str(value)
    st = s.strip()
    if st == "":
        return s, None
    if ftype == "number":
        try:
            float(st.replace(",", "."))
        except ValueError:
            return s, f"'{label}' deve ser um número."
    elif ftype == "date":
        if not _DATE_RE.match(st):
            return s, f"'{label}' deve ser uma data (AAAA-MM-DD)."
    pat = d.get("regex_pattern")
    if pat and ftype in ("text", "textarea", "number"):
        try:
            if not re.search(pat, s):
                cue = d.get("regex_cue") or f"formato esperado: {pat}"
                return s, f"'{label}' inválido ({cue})."
        except re.error:
            pass  # regex mal-formado guardado → não bloqueia o valor (igual ao core)
    return s, None


def normalize_values(scope: str, values: dict) -> tuple[dict, str | None]:
    """Mantém só chaves editáveis (não-readonly), coage/valida cada valor por tipo
    (ver ``_coerce_extra``) e exige os obrigatórios EDITÁVEIS (obs + extras). Os fixos
    readonly (atendente/início/…) são checados contra a coluna no fechamento (ver
    ``_missing_required``), pois não vêm no formulário. checkbox conta sempre como
    preenchido; checkboxes exige ao menos uma opção."""
    defs = {d["key"]: d for d in get_field_defs(scope) if not d.get("readonly")}
    values = values or {}
    clean: dict = {}
    for key, d in defs.items():
        cv, err = _coerce_extra(d, values.get(key))
        if err:
            return clean, err
        clean[key] = cv
    for key, d in defs.items():
        if d.get("required") and not _is_filled_extra(d, clean.get(key)):
            return clean, f"Campo obrigatório não preenchido: {d.get('label', key)}"
    return clean, None


def _effective_values(scope: str, entity: dict) -> dict:
    """Valores efetivos p/ checar `required`: extras (entity['fields'], já inclui obs) +
    o valor do rótulo Atendente (lido do assignee_user_id nativo do protocolo). Usado nos
    pontos de fechamento."""
    entity = entity or {}
    eff = dict(entity.get("fields") or {})
    for d in get_field_defs(scope):
        if d.get("type") == "atendente":
            eff[d["key"]] = entity.get("assignee_user_id")
    return eff


def _missing_required(scope: str, eff: dict) -> str | None:
    """1ª mensagem de obrigatório vazio OU valor inválido (por tipo) em ``eff`` (fixos +
    extras), ou None. Gate do FECHAMENTO — revalida tipos (número/data/regex/opções) além
    do required, já que o save parcial não bloqueia. checkbox sempre conta como preenchido;
    checkboxes exige ao menos uma opção."""
    for d in get_field_defs(scope):
        _cv, terr = _coerce_extra(d, eff.get(d["key"]))
        if terr:
            return terr
        if d.get("required") and not _is_filled_extra(d, _cv):
            return f"Campo obrigatório não preenchido: {d.get('label', d['key'])}"
    return None


# ── Campos EXTRAS (valores normalizados, 1 linha por atendimento-ciclo + def) ────

def _extra_value_type(d: dict) -> str:
    """Tipo de VALOR no JSON auto-descritivo: boolean (checkbox), list (multi-opção:
    checkboxes ou select múltiplo) ou string (demais — inclui number/date, guardados
    como texto)."""
    if (d or {}).get("type") == "checkbox":
        return "boolean"
    if _is_multi(d):
        return "list"
    return "string"


def _extras_payload(d: dict, value) -> dict:
    """JSON auto-descritivo gravado na linha — permite recuperar o rótulo (label)
    e o tipo direto do banco mesmo depois da definição ser apagada."""
    return {
        "type": _extra_value_type(d),
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

def _proto_dict(row, extras: dict | None = None) -> dict:
    d = dict(row)
    d["obs"] = d.get("obs") or ""
    if extras is None:
        extras = _visible_extras("protocolo", [d["id"]]).get(d["id"], {})
    d["fields"] = extras  # só extras (do protocolo) com def atual (key → value)
    return d


def _atendimento_dict(row, extras: dict | None = None) -> dict:
    d = dict(row)
    d["obs"] = d.get("obs") or ""
    if extras is None:
        extras = _visible_extras("atendimento", [d["id"]]).get(d["id"], {})
    d["fields"] = extras  # só extras (da atendimento) com def atual (key → value)
    return d


# ── Espelho no core (conversations.custom_attributes) ─────────────────────────
# Além das tabelas do plugin, os campos EDITÁVEIS de resolução da atendimento são
# espelhados em ``conversations.custom_attributes`` do core — integra atendimento↔
# protocolo, fica queryável/filtrável pelo core e SOBREVIVE se o plugin desativar
# (herdado do ext_demo). As chaves são registradas como definições de atributo
# (``applies_to="conversation"``) p/ aparecerem no painel de info. Desligável via o
# setting ``mirror_custom_attributes``.

# date/number têm equivalente direto no core; checkboxes (multi) não tem → cai em "text"
# (valor espelhado como lista separada por vírgula). select/radio/text/textarea → "text".
_CORE_ATTR_TYPE = {"checkbox": "checkbox", "date": "date", "number": "number"}


def _core_attr_type(ftype: str) -> str:
    return _CORE_ATTR_TYPE.get(ftype or "text", "text")


def _mirror_value(d: dict, value):
    """Valor a gravar no custom_attributes do core: checkbox→bool, checkboxes→lista
    unida por vírgula (core não tem multi), demais→string (number/date incluídos —
    o core coage/valida no set_values)."""
    ftype = d.get("type") or "text"
    if ftype == "checkbox":
        return bool(value)
    if _is_multi(d):
        return ", ".join(str(x) for x in value) if isinstance(value, list) else ("" if value is None else str(value))
    return "" if value is None else str(value)


def sync_core_atendimento_defs() -> None:
    """Registra (idempotente) as defs EDITÁVEIS da atendimento como atributos de atendimento
    no core, p/ os valores espelhados aparecerem/serem editáveis no painel de info.
    ``ensure_system_definition`` é no-op se a def já existe (respeita edição/remoção do
    usuário) — best-effort, nunca quebra o fluxo de resolução."""
    try:
        for i, d in enumerate(get_field_defs("atendimento")):
            if d.get("readonly") or d.get("type") == "atendente":
                continue  # "atendente" já É a coluna nativa; não vira atributo do core
            custom_attribute_repo.ensure_system_definition(
                attribute_key=d["key"], display_name=d.get("label") or d["key"],
                type=_core_attr_type(d.get("type")), applies_to="conversation", position=i)
    except Exception as e:  # noqa: BLE001
        logger.debug("protocolos: sync_core_atendimento_defs falhou: %s", e)


def mirror_atendimento_to_core(conversation_id: int, clean: dict) -> None:
    """Espelha os valores limpos da resolução (obs + extras editáveis) em
    ``conversations.custom_attributes`` (merge). Best-effort; respeita o toggle."""
    try:
        if not config_repo.get(_MIRROR_FLAG, True):
            return
        defs = {d["key"]: d for d in get_field_defs("atendimento")
                if not d.get("readonly") and d.get("type") != "atendente"}
        partial = {k: _mirror_value(defs[k], v)
                   for k, v in (clean or {}).items() if k in defs}
        if not partial:
            return
        sync_core_atendimento_defs()  # garante que as chaves existam como atributos
        custom_attribute_repo.set_values(_conversations_tbl, int(conversation_id), partial)
    except Exception as e:  # noqa: BLE001
        logger.debug("protocolos: mirror_atendimento_to_core falhou: %s", e)


# ── Protocolo: leitura/criação ──────────────────────────────────────────────

def _select_open_protocolo(contact_id: int) -> dict | None:
    with make_plugin_db() as conn:
        row = conn.execute(
            text("SELECT * FROM plugin_protocolos_protocolos "
                 "WHERE contact_id = :cid AND status = 'aberto' "
                 "ORDER BY opened_at DESC"),
            {"cid": contact_id},
        ).mappings().first()
    return _proto_dict(row) if row else None


def get_open_protocolo_for_contact(contact_id: int) -> dict | None:
    return _select_open_protocolo(contact_id)


def get_protocolo(atid: int) -> dict | None:
    with make_plugin_db() as conn:
        row = conn.execute(
            text("SELECT * FROM plugin_protocolos_protocolos WHERE id = :id"),
            {"id": atid},
        ).mappings().first()
    return _proto_dict(row) if row else None


def ensure_protocolo_for_contact(contact_id: int, phone: str = "", name: str = "",
                                   conversation_id: int | None = None,
                                   announce_open: bool = False) -> dict:
    """Get-or-create do protocolo ABERTO do contato (race-safe via índice parcial).

    Quando ``announce_open`` e ESTA chamada criou o protocolo, grava UMA nota privada
    marcando a abertura com um ID pesquisável (ver ``_write_open_note``). Quem perde a
    corrida (re-seleciona o existente) não grava → idempotente, 1 nota por protocolo."""
    existing = _select_open_protocolo(contact_id)
    if existing:
        return existing
    ts = now()
    created = False
    try:
        with make_plugin_db() as conn:
            conn.execute(
                text("INSERT INTO plugin_protocolos_protocolos "
                     "(contact_id, contact_phone, contact_name, status, fields, "
                     " opened_at, created_at, updated_at) "
                     "VALUES (:cid, :phone, :name, 'aberto', '{}', :ts, :ts, :ts)"),
                {"cid": contact_id, "phone": phone or "", "name": name or "", "ts": ts},
            )
        created = True
    except IntegrityError:
        pass  # perdeu a corrida → o vencedor já existe; re-seleciona abaixo
    at = _select_open_protocolo(contact_id)
    if created and at and announce_open:
        _write_open_note(at, conversation_id)
        # Card de sistema "Protocolo aberto" no fio da atendimento (igual ao de resolver
        # atendimento). Só na CRIAÇÃO real (não em re-seleção do existente nem no backfill).
        _emit_proto_notice("protocolo_opened", conversation_id=conversation_id,
                           contact_id=at.get("contact_id"),
                           phone=at.get("contact_phone") or None)
    return at


def _write_open_note(at: dict, conversation_id: int | None) -> None:
    """Nota PRIVADA (painel-only, NÃO vai ao cliente) marcando a ABERTURA do protocolo
    com um ID pesquisável e não-editável pela interface:
    ``PROT-AAAAMMDD-HHMMSS-<id_protocolo>`` (data/hora da abertura + id do protocolo).
    Best-effort: qualquer falha só loga em debug."""
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
        proto_id = (f"PROT-{lt.tm_year:04d}{lt.tm_mon:02d}{lt.tm_mday:02d}-"
                    f"{lt.tm_hour:02d}{lt.tm_min:02d}{lt.tm_sec:02d}-{(at or {}).get('id') or 0}")
        text_p = f"🔖 Protocolo aberto · {proto_id}"
        # Ancora no canal da PRÓPRIA conversa que disparou a nota (não contact-scoped,
        # que funde canais em multicanal — plano 11).
        channel_id = _channel_for_conversation(conversation_id) or _channel_for_contact(contact_id)
        cm = agent_handler._get_contact(phone, channel_id=channel_id)
        # Usa a linha que add_message RETORNA (id/ts/conversation_id) — não um get_last
        # racy (que, numa rajada, pega a última msg do contato, não esta nota).
        saved = cm.add_message("private_note", text_p)
        note = {"role": "private_note", "content": text_p,
                "ts": (saved or {}).get("ts", now()), "status": None,
                "conversation_id": (saved or {}).get("conversation_id")}
        if saved and saved.get("id"):
            note["_id"] = saved["id"]
        broadcast("new_message", {"phone": phone, "channel_id": channel_id, "message": note})
    except Exception as e:  # noqa: BLE001
        logger.debug("protocolos: nota de abertura falhou: %s", e)


# ── Avisos de sistema no fio da atendimento (cards conversation_event) ───────────
# Marca a ABERTURA e a FINALIZAÇÃO do protocolo como cards de sistema no chat —
# mesmo visual dos avisos de resolver/reabrir ATENDIMENTO (plano 12). O plugin REGISTRA
# seu próprio grupo + tipos no registry do core (``server.system_notices``) via
# ``plugins.context.register_notice*`` — SEM dar patch no core. Gate por config
# namespaceada do plugin (``plugin.protocolos.system_notice_lifecycle``, default ON).
# Late import do ``server``: logic.py segue importável standalone nos testes.

_NOTICE_GROUP = "protocolo_lifecycle"
_NOTICE_CONFIG_KEY = f"plugin.{PLUGIN_ID}.system_notice_lifecycle"


def _f_protocolo_opened(actor=None, **_) -> str:
    return f"📂 {actor} abriu o protocolo." if actor else "📂 Protocolo aberto."


def _f_protocolo_closed(actor=None, **_) -> str:
    return f"🏁 {actor} finalizou o protocolo." if actor else "🏁 Protocolo finalizado."


# ── Redação "atendimento" dos avisos de STATUS do core (só enquanto o plugin ativo) ──
# O core, por padrão, chama a entidade de "conversa". Enquanto o plugin de protocolos
# está ATIVO, a entidade passa a ser um "atendimento" — então sobrescrevemos (no registry
# do core, mesmo mecanismo dos plugins) os formatters do grupo "status" para exibir
# "atendimento". Desativar o plugin → o core volta a "conversa" no próximo boot.
def _f_atend_status_closed(actor=None, **_) -> str:
    return f"✅ {actor} resolveu o atendimento." if actor else "✅ Atendimento resolvido."


def _f_atend_status_open(actor=None, **_) -> str:
    return f"🔄 {actor} reabriu o atendimento." if actor else "🔄 Atendimento reaberto."


def _f_atend_status_reopened_auto(**_) -> str:
    return "🔄 Atendimento reaberto automaticamente (cliente enviou mensagem)."


def _f_atend_status_reopened_auto_agent(**_) -> str:
    return "🔄 Atendimento reaberto automaticamente (resposta enviada)."


def register_system_notices() -> None:
    """Registra (idempotente) o grupo + os 2 tipos de aviso do protocolo no core, e
    sobrescreve os avisos de STATUS do core para a redação "atendimento".
    Best-effort: falha (ex.: ``server`` ausente nos testes) nunca quebra o startup."""
    try:
        from plugins.context import register_notice_group, register_notice
        register_notice_group(_NOTICE_GROUP, "Protocolo (abrir/finalizar)",
                              config_key=_NOTICE_CONFIG_KEY, default=True)
        register_notice("protocolo_opened", _NOTICE_GROUP, _f_protocolo_opened)
        register_notice("protocolo_closed", _NOTICE_GROUP, _f_protocolo_closed)
        # Reusa o grupo "status" do core (mantém o mesmo toggle system_notice_status).
        register_notice("status_closed", "status", _f_atend_status_closed)
        register_notice("status_open", "status", _f_atend_status_open)
        register_notice("status_reopened_auto", "status", _f_atend_status_reopened_auto)
        register_notice("status_reopened_auto_agent", "status", _f_atend_status_reopened_auto_agent)
    except Exception as e:  # noqa: BLE001
        logger.debug("protocolos: register_system_notices falhou: %s", e)


def _emit_proto_notice(event_type: str, *, conversation_id: int | None = None,
                       contact_id: int | None = None, phone: str | None = None,
                       actor: str | None = None) -> None:
    """Emite um card ``conversation_event`` no fio da atendimento (gate de config no core).
    Com ``conversation_id`` ancora naquela atendimento; senão resolve a do contato
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
        logger.debug("protocolos: emitir aviso %s falhou: %s", event_type, e)


def update_protocolo_fields(atid: int, values: dict, assignee_user_id: int | None = None,
                              assignee_name: str = "") -> tuple[dict | None, str | None]:
    at = get_protocolo(atid)
    if not at:
        return None, "Protocolo não encontrado."
    # Todos os rótulos extras (obs incluso) vão na tabela de extras do protocolo. O rótulo
    # "atendente" NÃO é extra: seu valor é roteado p/ o assignee NATIVO (ver abaixo).
    # Salvar parcial é permitido (required só é exigido ao FECHAR) → mescla com o atual.
    merged = {**(at.get("fields") or {}), **(values or {})}
    clean, _err = normalize_values("protocolo", merged)
    extra_defs = get_extra_defs("protocolo")
    ts = now()
    with make_plugin_db() as conn:
        # Atendente do protocolo NÃO é mais sobrescrito por "quem só edita campos": muda só
        # pelo rótulo Atendente (abaixo) ou no Finalizar. Aqui só toca o updated_at.
        conn.execute(
            text("UPDATE plugin_protocolos_protocolos SET updated_at = :ts WHERE id = :id"),
            {"ts": ts, "id": atid},
        )
        for d in extra_defs:
            if d.get("type") == "atendente":
                continue  # não é extra — vira atribuição nativa
            if d["key"] in clean:
                upsert_extra(conn, "protocolo", atid, d, clean[d["key"]])
    # Rótulo "atendente" (se existir e veio no payload) → atribui o atendente NATIVO do
    # protocolo (grava direto, permite limpar, e propaga p/ as conversas).
    at_def = next((d for d in get_field_defs("protocolo") if d.get("type") == "atendente"), None)
    if at_def and at_def["key"] in (values or {}):
        uid = clean.get(at_def["key"])
        if uid != at.get("assignee_user_id"):  # só reatribui quando de fato mudou
            uname = ""
            if uid is not None:
                u = user_repo.get(int(uid)) or {}
                uname = str(u.get("name") or u.get("email") or "")
            assign_protocolo(atid, uid, assignee_name=uname)
    _broadcast_changed(at["contact_id"], atid)
    return get_protocolo(atid), None


def _open_cycles_of_protocolo(atid: int) -> list[dict]:
    """Ciclos (atendimentos) ABERTOS (ended_at NULL) deste protocolo."""
    with make_plugin_db() as conn:
        rows = conn.execute(
            text("SELECT * FROM plugin_protocolos_atendimentos "
                 "WHERE protocolo_id = :id AND ended_at IS NULL "
                 "ORDER BY started_at DESC, id DESC"),
            {"id": atid},
        ).mappings().all()
    return [dict(r) for r in rows]


def close_protocolo(atid: int, assignee_user_id: int | None = None,
                      assignee_name: str = "") -> tuple[dict | None, str | None]:
    at = get_protocolo(atid)
    if not at:
        return None, "Protocolo não encontrado."
    if at["status"] == "fechado":
        return at, None
    # Só finaliza quando a ÚLTIMA atendimento do protocolo estiver resolvida: se há ciclo
    # aberto, força resolver antes (a UI abre o popup "Resolver atendimento"). HTTP 400.
    if _open_cycles_of_protocolo(atid):
        return None, ("Existe um atendimento aberto neste protocolo — "
                      "resolva-o antes de finalizar.")
    # Exige os rótulos OBRIGATÓRIOS (OBS + extras) antes de fechar — lidos do que já
    # está salvo (a UI grava os campos antes de fechar).
    err = _missing_required("protocolo", _effective_values("protocolo", at))
    if err:
        return None, err
    ts = now()
    # Atendente do protocolo: se existe rótulo Atendente e ele JÁ definiu um atendente
    # (at.assignee_user_id, gravado no salvar antes de finalizar), mantém-se esse; senão
    # marca automaticamente quem finalizou (sem campo, ou campo "não atribuído"/vazio).
    at_def = next((d for d in get_field_defs("protocolo") if d.get("type") == "atendente"), None)
    if at_def and at.get("assignee_user_id") is not None:
        clo_uid, clo_name = None, ""     # None/'' → COALESCE/CASE mantêm o atual (o do campo)
    else:
        clo_uid, clo_name = assignee_user_id, assignee_name
    with make_plugin_db() as conn:
        conn.execute(
            text("UPDATE plugin_protocolos_protocolos SET status = 'fechado', "
                 "closed_at = :ts, updated_at = :ts, "
                 "assignee_user_id = COALESCE(:auid, assignee_user_id), "
                 "assignee_name = CASE WHEN :aname <> '' THEN :aname ELSE assignee_name END "
                 "WHERE id = :id"),
            {"ts": ts, "auid": clo_uid, "aname": clo_name or "", "id": atid},
        )
    _broadcast_changed(at["contact_id"], atid)
    # Card de sistema "Protocolo finalizado" ancorado na atendimento MAIS RECENTE do
    # protocolo (via conversation_id) — não no caminho contact-scoped, que em multicanal
    # cairia na thread errada. Autor = operador que finalizou, quando houver.
    _emit_proto_notice("protocolo_closed",
                       conversation_id=_latest_conversation_of_protocolo(atid),
                       contact_id=at.get("contact_id"),
                       phone=at.get("contact_phone") or None, actor=(assignee_name or None))
    return get_protocolo(atid), None


def reopen_protocolo(atid: int) -> tuple[dict | None, str | None]:
    at = get_protocolo(atid)
    if not at:
        return None, "Protocolo não encontrado."
    if at["status"] == "aberto":
        return at, None
    other = _select_open_protocolo(at["contact_id"])
    if other:
        return None, "Já existe um protocolo aberto para este contato."
    ts = now()
    try:
        with make_plugin_db() as conn:
            conn.execute(
                text("UPDATE plugin_protocolos_protocolos SET status = 'aberto', "
                     "closed_at = NULL, updated_at = :ts WHERE id = :id"),
                {"ts": ts, "id": atid},
            )
    except IntegrityError:
        return None, "Já existe um protocolo aberto para este contato."
    _broadcast_changed(at["contact_id"], atid)
    return get_protocolo(atid), None


def _conversation_ids_of_protocolo(atid: int) -> list[int]:
    """conversation_ids (distintos) das atendimentos-ciclo deste protocolo."""
    with make_plugin_db() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT conversation_id FROM plugin_protocolos_atendimentos "
                 "WHERE protocolo_id = :id AND conversation_id IS NOT NULL"),
            {"id": atid},
        ).all()
    return [int(r[0]) for r in rows]


def _propagate_assignee_to_conversations(atend_ids: list[int],
                                         assignee_user_id: int | None) -> None:
    """Espelha o atendente do protocolo nas ATENDIMENTOS do core (``assignee_user_id``)
    — é o que aparece na lista de atendimentos e no painel "Agente atribuído". Emite o
    MESMO evento WS do core (``conversation_assigned``) p/ atualização ao vivo, com o
    payload que o frontend espera. Best-effort: uma falha nunca quebra a atribuição."""
    for cid in atend_ids:
        try:
            atend = conversation_repo.set_assignee(cid, assignee_user_id)
            if not atend:
                continue
            broadcast("conversation_assigned", {
                "conversation_id": atend.get("id"),
                "display_id": atend.get("display_id"),
                "contact_id": atend.get("contact_id"),
                "status": atend.get("status"),
                "assignee_user_id": atend.get("assignee_user_id"),
                "active_agent_key": atend.get("active_agent_key"),
                "ai_active": atend.get("ai_active"),
                "is_archived": atend.get("is_archived"),
                "inbox_id": atend.get("inbox_id"),
                "ts": now(),
            })
        except Exception as e:  # noqa: BLE001
            logger.debug("protocolos: propagar assignee p/ atendimento %s falhou: %s", cid, e)


def assign_protocolo(atid: int, assignee_user_id: int | None,
                       assignee_name: str = "") -> tuple[dict | None, str | None]:
    """Define o atendente do protocolo (sobrescreve; ``None`` = remove atribuição) E
    PROPAGA para as atendimentos do core (assignee_user_id) — senão a mudança não aparece
    na lista/painel de atendimentos. Usado pelo drag-and-drop do kanban "por atendente".
    Diferente de ``update_protocolo_fields``, aqui o assignee é gravado direto (sem
    COALESCE), para permitir LIMPAR a atribuição."""
    at = get_protocolo(atid)
    if not at:
        return None, "Protocolo não encontrado."
    name = assignee_name or "" if assignee_user_id is not None else ""
    ts = now()
    with make_plugin_db() as conn:
        conn.execute(
            text("UPDATE plugin_protocolos_protocolos SET assignee_user_id = :auid, "
                 "assignee_name = :aname, updated_at = :ts WHERE id = :id"),
            {"auid": assignee_user_id, "aname": name, "ts": ts, "id": atid},
        )
    # Espelha nas atendimentos do core (fora da conn do plugin).
    _propagate_assignee_to_conversations(_conversation_ids_of_protocolo(atid), assignee_user_id)
    _broadcast_changed(at["contact_id"], atid)
    return get_protocolo(atid), None


def _hydrate_protocolos(rows) -> list[dict]:
    """Batch: extras do protocolo + rótulos/atributos da última atendimento (evita N+1)."""
    extras = _visible_extras("protocolo", [r["id"] for r in rows])
    out = [_proto_dict(r, extras.get(r["id"], {})) for r in rows]
    _attach_latest_atendimento(out)  # atendimento_fields + atendimento_attrs (última atendimento)
    return out


def list_protocolos(*, status: str | None = None, assignee_user_id: int | None = None,
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
    base = ("SELECT * FROM plugin_protocolos_protocolos WHERE " + " AND ".join(where)
            + " ORDER BY (status = 'aberto') DESC, opened_at DESC")

    # Filtro por atributo de ATENDIMENTO (valor na última atendimento). Como o valor não vive nas
    # colunas do protocolo, filtra-se em Python ANTES do corte: varre um teto interno,
    # atribui atendimento_attrs e só então aplica offset/limit (caro só quando a aba usa este
    # filtro — caminho normal continua com LIMIT/OFFSET no SQL).
    af = {k: v for k, v in (attr_filters or {}).items() if k and _KEY_RE.match(k)}
    if af:
        with make_plugin_db() as conn:
            rows = conn.execute(text(base + " LIMIT :scan"),
                                {**params, "scan": _ATTR_SCAN_CAP}).mappings().all()
        out = _hydrate_protocolos(rows)
        out = [a for a in out
               if all(str((a.get("atendimento_attrs") or {}).get(k, "")) == str(v)
                      for k, v in af.items())]
        return out[off:off + lim]

    with make_plugin_db() as conn:
        rows = conn.execute(text(base + " LIMIT :limit OFFSET :offset"),
                            {**params, "limit": lim, "offset": off}).mappings().all()
    return _hydrate_protocolos(rows)


def _conversation_core_attrs(atend_ids: list[int]) -> dict[int, dict]:
    """{conversation_id: {key: value}} dos ATRIBUTOS PERSONALIZADOS do core (escopo atendimento)
    que NÃO são espelho do plugin — i.e., defs com is_system=0 (criadas na tela do core).
    Lê de ``conversations.custom_attributes``. Batch (evita N+1)."""
    ids = [int(c) for c in (atend_ids or []) if c]
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
            text("SELECT id, custom_attributes FROM atendimentos WHERE id IN :ids")
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


def _attach_latest_atendimento(items: list[dict]) -> None:
    """Anexa a cada protocolo os valores da ÚLTIMA atendimento (ciclo mais recente):
    ``atendimento_fields`` (rótulos do plugin do escopo atendimento — obs + extras) e
    ``atendimento_attrs`` (atributos personalizados do core — is_system=0). Tudo batch."""
    if not items:
        return
    atids = [a["id"] for a in items]
    with make_plugin_db() as conn:
        rows = conn.execute(
            text("SELECT protocolo_id, id, conversation_id, obs FROM plugin_protocolos_atendimentos "
                 "WHERE protocolo_id IN :ids ORDER BY started_at DESC, id DESC")
            .bindparams(bindparam("ids", expanding=True)),
            {"ids": atids},
        ).mappings().all()
    latest: dict[int, dict] = {}
    for r in rows:
        latest.setdefault(int(r["protocolo_id"]), dict(r))  # 1º = mais recente (ordem desc)
    atend_extras = _visible_extras("atendimento", [r["id"] for r in latest.values()])
    core_attrs = _conversation_core_attrs([r["conversation_id"] for r in latest.values()])
    for a in items:
        lc = latest.get(a["id"])
        if not lc:
            a["atendimento_fields"] = {}
            a["atendimento_attrs"] = {}
            continue
        cf = dict(atend_extras.get(lc["id"], {}))  # obs já vem aqui (rótulo extra)
        a["atendimento_fields"] = cf
        a["atendimento_attrs"] = core_attrs.get(lc["conversation_id"], {}) if lc.get("conversation_id") else {}


# ── Visualizações personalizadas do Kanban (abas de "Agrupar por") ────────────
# CRUD puro sobre plugin_protocolos_kanban_views. O GATE (pessoal x equipe, ownership)
# vive na rota (precisa do request p/ checar manage_team_views) — aqui só valida o shape.

def _avail_list(s):
    """available_filters TEXT → list[str] | None. None/NULL/JSON inválido → None
    (= TODOS os filtros disponíveis). Lista JSON → lista de chaves (strings)."""
    if s in (None, ""):
        return None
    try:
        v = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None
    return [str(x) for x in v] if isinstance(v, list) else None


def _int_list(s):
    """TEXT JSON → list[int] | None (ids de usuário). None/NULL/inválido → None."""
    if s in (None, ""):
        return None
    try:
        v = json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(v, list):
        return None
    out = []
    for x in v:
        try:
            out.append(int(x))
        except (ValueError, TypeError):
            pass
    return out


def _dump_str_list(v):
    """list não-vazia → JSON de strings; senão None (NULL = sem restrição)."""
    return json.dumps([str(x) for x in v]) if isinstance(v, list) and v else None


def _dump_int_list(v):
    """list não-vazia → JSON de ints; senão None (NULL = sem restrição)."""
    if not (isinstance(v, list) and v):
        return None
    out = []
    for x in v:
        try:
            out.append(int(x))
        except (ValueError, TypeError):
            pass
    return json.dumps(out) if out else None


def _view_dict(row) -> dict:
    d = dict(row)
    d["filters"] = _safe_json(d.get("filters"))  # JSON TEXT → dict
    d["available_filters"] = _avail_list(d.get("available_filters"))  # JSON array | None (=todos)
    d["column_order"] = _avail_list(d.get("column_order"))  # ordem das colunas (list[str] | None)
    # ACL de visibilidade (quem pode ver): grupos (role keys) + usuários incluídos/excluídos.
    d["visibility_roles"] = _avail_list(d.get("visibility_roles"))          # list[str] | None
    d["visibility_users_include"] = _int_list(d.get("visibility_users_include"))  # list[int] | None
    d["visibility_users_exclude"] = _int_list(d.get("visibility_users_exclude"))  # list[int] | None
    return d


def _user_role_keys(user_id) -> set:
    """role.key do usuário (via core user_roles/roles). Vazio se sem identidade ou erro."""
    if user_id is None:
        return set()
    try:
        with make_plugin_db() as conn:
            rows = conn.execute(
                text("SELECT r.key FROM user_roles ur JOIN roles r ON r.id = ur.role_id "
                     "WHERE ur.user_id = :uid"),
                {"uid": int(user_id)},
            ).all()
        return {row[0] for row in rows}
    except Exception:  # tabelas core ausentes em algum contexto → falha fechada
        return set()


def _view_visible(view: dict, uid, role_keys) -> bool:
    """A view é visível para (uid, role_keys)? CRIADOR e 'admin' veem SEMPRE. Sem grupos e
    sem incluídos numa view de EQUIPE = legado 'todos veem'. Exclusão bloqueia (menos
    criador/admin). uid None (legado/open) → tudo."""
    if uid is None:
        return True
    if view.get("owner_user_id") == uid:
        return True
    role_keys = role_keys or set()
    if "admin" in role_keys:
        return True
    if uid in set(view.get("visibility_users_exclude") or []):
        return False
    if view.get("scope") == "personal":
        return False
    roles = view.get("visibility_roles") or []
    include = set(view.get("visibility_users_include") or [])
    if not roles and not include:
        return True  # equipe legado (sem ACL) → todos veem
    if uid in include:
        return True
    return bool(roles and (set(role_keys) & set(roles)))


def list_kanban_views(*, user_id: int | None = None, role_keys=None) -> list[dict]:
    """Visualizações VISÍVEIS ao usuário (ver ``_view_visible``): pessoais dele + equipe
    conforme a ACL (grupos/usuários) + sempre as que ele criou + admin vê tudo. user_id None
    (legado/open) → todas. Ordena por (position, id) — as abas padrão semeadas (Status/Atendente,
    position 0 e 1) ficam à esquerda; NÃO ordena por scope (senão views pessoais viriam antes das
    de equipe). Anexa ``view["pref"]`` do chamador (default de equipe). ``role_keys`` None →
    resolvido do banco (testes passam)."""
    if role_keys is None:
        role_keys = _user_role_keys(user_id)
    role_keys = set(role_keys or [])
    with make_plugin_db() as conn:
        rows = conn.execute(
            text(f"SELECT * FROM {_VIEWS_TABLE} ORDER BY position ASC, id ASC")
        ).mappings().all()
        pref_rows = []
        if user_id is not None:
            pref_rows = conn.execute(
                text(f"SELECT view_id, use_personal, personal_filters, personal_column_order "
                     f"FROM {_PREFS_TABLE} WHERE user_id = :uid"),
                {"uid": int(user_id)},
            ).mappings().all()
    prefs = {int(p["view_id"]): _pref_dict(p) for p in pref_rows}
    out = []
    for r in rows:
        d = _view_dict(r)
        if not _view_visible(d, user_id, role_keys):
            continue
        d["pref"] = prefs.get(int(d["id"]),
                              {"use_personal": False, "personal_filters": {},
                               "personal_column_order": None})
        out.append(d)
    return out


def get_kanban_view(vid: int) -> dict | None:
    with make_plugin_db() as conn:
        row = conn.execute(
            text(f"SELECT * FROM {_VIEWS_TABLE} WHERE id = :id"), {"id": int(vid)},
        ).mappings().first()
    return _view_dict(row) if row else None


def _validate_view(*, name, scope, group_by, group_attr_key, group_date_mode,
                   group_date_from=None, group_date_to=None,
                   group_date_grain=None) -> str | None:
    if not (name or "").strip():
        return "Informe um nome para a visualização."
    if scope not in _VIEW_SCOPES:
        return "Escopo inválido."
    if group_by not in _VIEW_GROUP_BY:
        return "Agrupamento inválido."
    if group_by == "attr" and not _KEY_RE.match(group_attr_key or ""):
        return "Selecione um atributo (lista) válido para agrupar."
    if group_by == "data":
        if (group_date_mode or "") not in _VIEW_DATE_MODES:
            return "Selecione um modo de data válido (faixas, dia, semana, mês ou período personalizado)."
        if group_date_mode == "personalizado":
            if not (_valid_ymd(group_date_from) and _valid_ymd(group_date_to)):
                return "Informe as datas de início e fim do período personalizado."
            if group_date_from > group_date_to:
                return "A data de início não pode ser maior que a data de fim."
            if (group_date_grain or "") not in _VIEW_DATE_GRAINS:
                return "Selecione uma granularidade válida (dia, semana ou mês)."
    return None


def create_kanban_view(*, name, scope="personal", group_by="status", group_attr_key=None,
                       group_date_mode=None,
                       group_date_from=None, group_date_to=None, group_date_grain=None,
                       filters=None, available_filters=None,
                       column_order=None, visibility_roles=None, visibility_users_include=None,
                       visibility_users_exclude=None,
                       owner_user_id=None) -> tuple[dict | None, str | None]:
    name = (name or "").strip()
    vroles = _dump_str_list(visibility_roles)
    vinc = _dump_int_list(visibility_users_include)
    vexc = _dump_int_list(visibility_users_exclude)
    # scope DERIVADO: há grupo ou usuário incluído → compartilhado (team).
    if vroles or vinc:
        scope = "team"
    err = _validate_view(name=name, scope=scope, group_by=group_by,
                         group_attr_key=group_attr_key, group_date_mode=group_date_mode,
                         group_date_from=group_date_from, group_date_to=group_date_to,
                         group_date_grain=group_date_grain)
    if err:
        return None, err
    ts = now()
    fjson = json.dumps(filters if isinstance(filters, dict) else {})
    afjson = json.dumps([str(x) for x in available_filters]) if isinstance(available_filters, list) else None
    cojson = _dump_str_list(column_order)  # ordem das colunas ([]/None → NULL = ordem padrão)
    gak = (group_attr_key or None) if group_by == "attr" else None
    gdm = (group_date_mode or None) if group_by == "data" else None
    # Janela + granularidade: só valem quando data + personalizado; senão NULL.
    _custom = group_by == "data" and gdm == "personalizado"
    gdf = (group_date_from or None) if _custom else None
    gdt = (group_date_to or None) if _custom else None
    gdg = (group_date_grain or None) if _custom else None
    with make_plugin_db() as conn:
        pos = conn.execute(
            text(f"SELECT COALESCE(MAX(position), -1) + 1 AS p FROM {_VIEWS_TABLE}")
        ).scalar() or 0
        conn.execute(
            text(f"INSERT INTO {_VIEWS_TABLE} "
                 "(name, scope, owner_user_id, group_by, group_attr_key, group_date_mode, "
                 " group_date_from, group_date_to, group_date_grain, "
                 " filters, available_filters, column_order, visibility_roles, "
                 " visibility_users_include, visibility_users_exclude, position, "
                 " created_at, updated_at) "
                 "VALUES (:name, :scope, :owner, :gby, :gak, :gdm, :gdf, :gdt, :gdg, "
                 " :filters, :af, :co, :vr, "
                 " :vi, :ve, :pos, :ts, :ts)"),
            {"name": name, "scope": scope, "owner": owner_user_id, "gby": group_by,
             "gak": gak, "gdm": gdm, "gdf": gdf, "gdt": gdt, "gdg": gdg,
             "filters": fjson, "af": afjson, "co": cojson,
             "vr": vroles, "vi": vinc, "ve": vexc, "pos": int(pos), "ts": ts},
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
                       group_date_mode=None,
                       group_date_from=None, group_date_to=None, group_date_grain=None,
                       filters=None, available_filters=_UNSET,
                       column_order=_UNSET, visibility_roles=_UNSET,
                       visibility_users_include=_UNSET,
                       visibility_users_exclude=_UNSET) -> tuple[dict | None, str | None]:
    cur = get_kanban_view(vid)
    if not cur:
        return None, "Visualização não encontrada."
    name = cur["name"] if name is None else (name or "").strip()
    scope = scope or cur["scope"]
    group_by = group_by or cur["group_by"]
    group_attr_key = cur.get("group_attr_key") if group_attr_key is None else group_attr_key
    group_date_mode = cur.get("group_date_mode") if group_date_mode is None else group_date_mode
    group_date_from = cur.get("group_date_from") if group_date_from is None else group_date_from
    group_date_to = cur.get("group_date_to") if group_date_to is None else group_date_to
    group_date_grain = cur.get("group_date_grain") if group_date_grain is None else group_date_grain
    fjson = json.dumps(filters if isinstance(filters, dict) else (cur.get("filters") or {}))
    # available_filters: _UNSET = mantém atual; None = TODOS (NULL); lista = allow-list.
    af_src = cur.get("available_filters") if available_filters is _UNSET else available_filters
    afjson = json.dumps([str(x) for x in af_src]) if isinstance(af_src, list) else None
    # column_order: _UNSET = mantém atual; lista/None substitui ([]/None → NULL = ordem padrão).
    co_src = cur.get("column_order") if column_order is _UNSET else column_order
    cojson = _dump_str_list(co_src)
    # ACL de visibilidade: _UNSET = mantém atual; lista/None substitui.
    vr_src = cur.get("visibility_roles") if visibility_roles is _UNSET else visibility_roles
    vi_src = cur.get("visibility_users_include") if visibility_users_include is _UNSET else visibility_users_include
    ve_src = cur.get("visibility_users_exclude") if visibility_users_exclude is _UNSET else visibility_users_exclude
    vrjson = _dump_str_list(vr_src)
    vijson = _dump_int_list(vi_src)
    vejson = _dump_int_list(ve_src)
    # scope DERIVADO: há grupo ou usuário incluído → compartilhado (team).
    if vrjson or vijson:
        scope = "team"
    err = _validate_view(name=name, scope=scope, group_by=group_by,
                         group_attr_key=group_attr_key, group_date_mode=group_date_mode,
                         group_date_from=group_date_from, group_date_to=group_date_to,
                         group_date_grain=group_date_grain)
    if err:
        return None, err
    gak = (group_attr_key or None) if group_by == "attr" else None
    gdm = (group_date_mode or None) if group_by == "data" else None
    # Janela + granularidade: só valem quando data + personalizado; senão NULL.
    _custom = group_by == "data" and gdm == "personalizado"
    gdf = (group_date_from or None) if _custom else None
    gdt = (group_date_to or None) if _custom else None
    gdg = (group_date_grain or None) if _custom else None
    ts = now()
    with make_plugin_db() as conn:
        conn.execute(
            text(f"UPDATE {_VIEWS_TABLE} SET name = :name, scope = :scope, group_by = :gby, "
                 "group_attr_key = :gak, group_date_mode = :gdm, "
                 "group_date_from = :gdf, group_date_to = :gdt, group_date_grain = :gdg, "
                 "filters = :filters, "
                 "available_filters = :af, column_order = :co, visibility_roles = :vr, "
                 "visibility_users_include = :vi, visibility_users_exclude = :ve, "
                 "updated_at = :ts WHERE id = :id"),
            {"name": name, "scope": scope, "gby": group_by, "gak": gak, "gdm": gdm,
             "gdf": gdf, "gdt": gdt, "gdg": gdg,
             "filters": fjson, "af": afjson, "co": cojson, "vr": vrjson, "vi": vijson,
             "ve": vejson, "ts": ts, "id": int(vid)},
        )
    _broadcast_changed(None, None)
    return get_kanban_view(vid), None


def delete_kanban_view(vid) -> tuple[bool, str | None]:
    cur = get_kanban_view(vid)
    if not cur:
        return False, "Visualização não encontrada."
    with make_plugin_db() as conn:
        conn.execute(text(f"DELETE FROM {_VIEWS_TABLE} WHERE id = :id"), {"id": int(vid)})
        # Limpa as preferências por-usuário órfãs desta view (sem FK cross-table).
        conn.execute(text(f"DELETE FROM {_PREFS_TABLE} WHERE view_id = :id"), {"id": int(vid)})
    _broadcast_changed(None, None)
    return True, None


# ── Preferência pessoal x equipe dos filtros por usuário/visualização ─────────
# Cada usuário escolhe, por aba, se ao entrar aplica os filtros da EQUIPE (a coluna
# filters compartilhada) ou os seus PESSOAIS. É a pref do PRÓPRIO usuário — a rota
# que escreve exige só `view`, não `manage_team_views`.

def _pref_dict(row) -> dict:
    """Linha de preferência → dict com personal_filters decodificado. Default de equipe
    (use_personal False, sem filtros pessoais) quando a linha não existe."""
    if not row:
        return {"use_personal": False, "personal_filters": {}, "personal_column_order": None}
    d = dict(row)
    return {
        "use_personal": bool(d.get("use_personal")),
        "personal_filters": _safe_json(d.get("personal_filters")),
        "personal_column_order": _avail_list(d.get("personal_column_order")),  # list[str] | None
    }


def get_user_view_pref(view_id: int, user_id: int | None) -> dict:
    """Preferência (pessoal x equipe) de UM usuário para UMA aba. Ausente ou sem identidade
    (legado/open) → default de EQUIPE: {use_personal: False, personal_filters: {}}."""
    if user_id is None:
        return {"use_personal": False, "personal_filters": {}, "personal_column_order": None}
    with make_plugin_db() as conn:
        row = conn.execute(
            text(f"SELECT use_personal, personal_filters, personal_column_order "
                 f"FROM {_PREFS_TABLE} WHERE user_id = :uid AND view_id = :vid"),
            {"uid": int(user_id), "vid": int(view_id)},
        ).mappings().first()
    return _pref_dict(row)


def upsert_user_view_pref(view_id: int, user_id: int | None, *, use_personal=None,
                          personal_filters=None, personal_column_order=None) -> dict:
    """Cria/atualiza a preferência de (user_id, view_id) via UPSERT no índice único.
    Merge parcial: campos None não são alterados (igual a update_kanban_view). Para a ordem
    das colunas, None mantém e [] limpa (volta à ordem padrão). Retorna a pref resultante.
    user_id None (sem identidade) → no-op, devolve o default de equipe."""
    if user_id is None:
        return {"use_personal": False, "personal_filters": {}, "personal_column_order": None}
    cur = get_user_view_pref(view_id, user_id)
    up = cur["use_personal"] if use_personal is None else bool(use_personal)
    pf = cur["personal_filters"] if personal_filters is None else (
        personal_filters if isinstance(personal_filters, dict) else {})
    pco = cur.get("personal_column_order") if personal_column_order is None else personal_column_order
    pjson = json.dumps(pf, ensure_ascii=False)
    pcojson = _dump_str_list(pco)  # [] / None → NULL = ordem padrão
    ts = now()
    with make_plugin_db() as conn:
        conn.execute(
            text(f"INSERT INTO {_PREFS_TABLE} "
                 "(user_id, view_id, use_personal, personal_filters, personal_column_order, "
                 " created_at, updated_at) "
                 "VALUES (:uid, :vid, :up, :pf, :pco, :ts, :ts) "
                 "ON CONFLICT (user_id, view_id) DO UPDATE SET "
                 "use_personal = excluded.use_personal, "
                 "personal_filters = excluded.personal_filters, "
                 "personal_column_order = excluded.personal_column_order, "
                 "updated_at = excluded.updated_at"),
            {"uid": int(user_id), "vid": int(view_id),
             "up": 1 if up else 0, "pf": pjson, "pco": pcojson, "ts": ts},
        )
    return {"use_personal": up, "personal_filters": pf, "personal_column_order": _avail_list(pcojson)}


def set_atendimento_attr(atid: int, key: str, value) -> tuple[dict | None, str | None]:
    """Drag no kanban por atributo: grava ``key`` = ``value`` em
    ``conversations.custom_attributes`` da ÚLTIMA atendimento (ciclo mais recente) do
    protocolo. ``value`` None/"" remove a chave (cai na coluna "Sem valor"). Espelha o
    padrão de ``mirror_atendimento_to_core`` (set_values direto). Retorna o protocolo com
    ``atendimento_attrs`` já recarregado."""
    if not _KEY_RE.match(key or ""):
        return None, "Atributo inválido."
    at = get_protocolo(atid)
    if not at:
        return None, "Protocolo não encontrado."
    with make_plugin_db() as conn:
        row = conn.execute(
            text("SELECT conversation_id FROM plugin_protocolos_atendimentos "
                 "WHERE protocolo_id = :aid AND conversation_id IS NOT NULL "
                 "ORDER BY started_at DESC, id DESC LIMIT 1"),
            {"aid": int(atid)},
        ).mappings().first()
    atend_id = row["conversation_id"] if row else None
    if not atend_id:
        return None, "Este protocolo ainda não tem atendimento vinculado."
    v = None if value in (None, "") else str(value)
    try:
        custom_attribute_repo.set_values(_conversations_tbl, int(atend_id), {key: v})
    except Exception as e:  # noqa: BLE001
        logger.debug("protocolos: set_atendimento_attr falhou: %s", e)
        return None, "Falha ao gravar o atributo."
    _broadcast_changed(at.get("contact_id"), atid)
    out = [get_protocolo(atid)]
    _attach_latest_atendimento(out)
    return out[0], None


# ── Atendimentos do protocolo = CICLOS (aberto→resolvido) ──────────────────────
# Cada linha de plugin_protocolos_atendimentos é UM ciclo de protocolo de uma
# atendimento: nasce quando o cliente engaja (inbound) e fecha quando o operador
# resolve (ended_at + OBS/extras). O cliente voltar depois inicia um ciclo NOVO no
# mesmo protocolo → várias linhas acumulam. `ended_at IS NULL` = ciclo aberto.

def list_atendimentos(atid: int) -> list[dict]:
    with make_plugin_db() as conn:
        rows = conn.execute(
            text("SELECT * FROM plugin_protocolos_atendimentos "
                 "WHERE protocolo_id = :id ORDER BY started_at ASC, id ASC"),
            {"id": atid},
        ).mappings().all()
    extras = _visible_extras("atendimento", [r["id"] for r in rows])  # batch (evita N+1)
    out = [_atendimento_dict(r, extras.get(r["id"], {})) for r in rows]
    # Anexa os ATRIBUTOS PERSONALIZADOS do core (is_system=0) de CADA ciclo (lidos de
    # conversations.custom_attributes pelo conversation_id). A tabela de detalhe os mostra
    # numa coluna própria, separados das informações da atendimento. Batch (evita N+1).
    core_attrs = _conversation_core_attrs([c.get("conversation_id") for c in out])
    for c in out:
        cid = c.get("conversation_id")
        c["attrs"] = core_attrs.get(int(cid), {}) if cid else {}
    return out


def cycle_anchor(atendimento_id: int) -> dict:
    """Deep-link de scroll: devolve a atendimento (thread) do ciclo + o ``_id`` (=
    ``messages.id``, é o ``data-mid`` do chat) da PRIMEIRA mensagem a partir do
    ``started_at`` do ciclo — alvo do permalink ``/conversations/<id>?message=<_id>``.
    ``message_id`` é None se a atendimento não tiver mensagens. SELECT em ``messages`` do
    core na mesma conexão (mesmo banco)."""
    with make_plugin_db() as conn:
        cyc = conn.execute(
            text("SELECT conversation_id, started_at FROM plugin_protocolos_atendimentos "
                 "WHERE id = :id"),
            {"id": atendimento_id},
        ).mappings().first()
        if not cyc or cyc["conversation_id"] is None:
            return {"conversation_id": None, "message_id": None}
        atend_id = int(cyc["conversation_id"])
        started = float(cyc["started_at"] or 0)
        row = conn.execute(
            text("SELECT id FROM messages WHERE conversation_id = :cid AND ts >= :ts "
                 "ORDER BY ts ASC, id ASC LIMIT 1"),
            {"cid": atend_id, "ts": started},
        ).first()
        if row is None:  # started_at após a última msg (raro) → cai na última da atendimento
            row = conn.execute(
                text("SELECT id FROM messages WHERE conversation_id = :cid "
                     "ORDER BY ts DESC, id DESC LIMIT 1"),
                {"cid": atend_id},
            ).first()
    return {"conversation_id": atend_id, "message_id": (row[0] if row else None)}


def get_latest_cycle(conversation_id: int) -> dict | None:
    """O ciclo mais recente de uma atendimento (o que está sendo resolvido/ativo)."""
    with make_plugin_db() as conn:
        row = conn.execute(
            text("SELECT * FROM plugin_protocolos_atendimentos "
                 "WHERE conversation_id = :cid ORDER BY started_at DESC, id DESC"),
            {"cid": conversation_id},
        ).mappings().first()
    return _atendimento_dict(row) if row else None


def get_open_cycle(conversation_id: int, protocolo_id: int) -> dict | None:
    """O ciclo ABERTO (ended_at NULL) da atendimento neste protocolo, se houver."""
    with make_plugin_db() as conn:
        row = conn.execute(
            text("SELECT * FROM plugin_protocolos_atendimentos "
                 "WHERE conversation_id = :cid AND protocolo_id = :aid "
                 "AND ended_at IS NULL ORDER BY started_at DESC, id DESC"),
            {"cid": conversation_id, "aid": protocolo_id},
        ).mappings().first()
    return _atendimento_dict(row) if row else None


def _count_cycles(conversation_id: int, protocolo_id: int) -> int:
    with make_plugin_db() as conn:
        return conn.execute(
            text("SELECT COUNT(*) FROM plugin_protocolos_atendimentos "
                 "WHERE conversation_id = :cid AND protocolo_id = :aid"),
            {"cid": conversation_id, "aid": protocolo_id},
        ).scalar() or 0


def _insert_cycle(conversation_id: int, contact_id: int, protocolo_id: int,
                  assignee_name: str = "") -> dict:
    ts = now()
    with make_plugin_db() as conn:
        conn.execute(
            text("INSERT INTO plugin_protocolos_atendimentos "
                 "(protocolo_id, conversation_id, contact_id, assignee_name, "
                 " fields, started_at, created_at, updated_at) "
                 "VALUES (:aid, :cid, :ctid, :aname, '{}', :ts, :ts, :ts)"),
            {"aid": protocolo_id, "cid": conversation_id, "ctid": contact_id,
             "aname": assignee_name or "", "ts": ts},
        )
    return get_open_cycle(conversation_id, protocolo_id)


def ensure_open_cycle(conversation_id: int, contact_id: int, protocolo_id: int,
                      assignee_name: str = "") -> dict:
    """Ciclo aberto da atendimento neste protocolo; cria um NOVO se não houver
    (o último foi resolvido ou nunca existiu) — é isso que acumula as linhas."""
    cur = get_open_cycle(conversation_id, protocolo_id)
    if cur:
        return cur
    return _insert_cycle(conversation_id, contact_id, protocolo_id, assignee_name)


def ensure_cycle_exists(conversation_id: int, contact_id: int, protocolo_id: int) -> dict | None:
    """Bootstrap (saída do operador): cria um ciclo SÓ se não houver NENHUM neste
    protocolo — nunca abre um ciclo novo logo após uma resolução."""
    if _count_cycles(conversation_id, protocolo_id) > 0:
        return None
    return _insert_cycle(conversation_id, contact_id, protocolo_id)


def resolve_atendimento(conversation_id: int, values: dict, assignee_name: str = "",
                     assignee_user_id: int | None = None) -> tuple[dict | None, str | None]:
    """Fecha o ciclo ABERTO da atendimento (Fim + OBS + extras). Cria+fecha um se não houver.
    Cada rótulo extra (obs incluso) vai numa linha de campos_extras. Grava o AGENTE que
    resolveu (assignee_user_id + assignee_name) no ciclo."""
    atend = conversation_repo.get(conversation_id)
    if not atend:
        return None, "Atendimento não encontrada."
    contact_id = atend["contact_id"]
    contact = contact_repo.get(contact_id) or {}
    at = ensure_protocolo_for_contact(
        contact_id, phone=contact.get("phone", ""), name=_contact_name(contact),
        conversation_id=conversation_id, announce_open=True)
    cycle = (get_open_cycle(conversation_id, at["id"])
             or ensure_open_cycle(conversation_id, contact_id, at["id"], assignee_name))
    clean, err = normalize_values("atendimento", values)
    if err:
        return None, err
    ts = now()
    extra_defs = get_extra_defs("atendimento")
    # Atendente do CICLO (coluna "Atendente" do histórico): se o rótulo Atendente veio
    # PREENCHIDO, ELE manda; senão marca automaticamente quem resolveu (editor). Campo
    # ausente OU "Não atribuído" (None) mantém o automático — regra pedida pelo usuário.
    at_def = next((d for d in get_field_defs("atendimento") if d.get("type") == "atendente"), None)
    field_submitted = bool(at_def and at_def["key"] in (values or {}))
    field_uid = clean.get(at_def["key"]) if field_submitted else None
    cyc_uid, cyc_name = assignee_user_id, assignee_name
    if field_uid is not None:
        _u = user_repo.get(int(field_uid)) or {}
        cyc_uid, cyc_name = field_uid, str(_u.get("name") or _u.get("email") or "")
    with make_plugin_db() as conn:
        conn.execute(
            text("UPDATE plugin_protocolos_atendimentos SET ended_at = :ts, "
                 "assignee_user_id = COALESCE(:auid, assignee_user_id), "
                 "assignee_name = CASE WHEN :aname <> '' THEN :aname ELSE assignee_name END, "
                 "updated_at = :ts WHERE id = :id"),
            {"ts": ts, "auid": cyc_uid,
             "aname": cyc_name or "", "id": cycle["id"]},
        )
        for d in extra_defs:
            if d.get("type") == "atendente":
                continue  # não é extra — vira atribuição nativa da conversa (abaixo)
            if d["key"] in clean:
                upsert_extra(conn, "atendimento", cycle["id"], d, clean[d["key"]])
    # Rótulo "atendente" → atribui o atendente NATIVO DESTA conversa (set_assignee + WS),
    # quando veio no payload e de fato mudou.
    if field_submitted and field_uid != atend.get("assignee_user_id"):
        _propagate_assignee_to_conversations([conversation_id], field_uid)
    # Espelha os mesmos campos no core (conversations.custom_attributes) — integra
    # atendimento↔protocolo e sobrevive se o plugin for desativado.
    mirror_atendimento_to_core(conversation_id, clean)
    _broadcast_changed(contact_id, at["id"])
    return get_latest_cycle(conversation_id), None


# ── Auto-vínculo (event handlers) ─────────────────────────────────────────────

def _contact_name(contact: dict) -> str:
    return str((contact or {}).get("name") or (contact or {}).get("phone") or "")


def _resolve_target(payload: dict):
    """(contact, atend) do contato do payload, ou (None, None) se inaplicável.

    Sync (o bus roda em ``asyncio.to_thread``). Pula grupos e respeita o toggle.
    """
    payload = payload or {}
    if not config_repo.get(f"plugin.{PLUGIN_ID}.auto_link", True):
        return None, None
    if payload.get("is_group"):
        return None, None  # protocolos são por-contato (1:1)
    phone = payload.get("phone")
    if not phone:
        return None, None
    contact = contact_repo.get_by_phone(phone)
    if not contact:
        return None, None
    atend = (conversation_repo.get_open_for_contact(contact["id"])
            or conversation_repo.get_latest_for_contact(contact["id"]))
    return (contact, atend) if atend else (None, None)


def on_inbound(ctx, payload: dict) -> None:
    """``message.saved`` (cliente engajou) → garante protocolo aberto + ciclo
    ABERTO. Se o último ciclo foi resolvido, abre um NOVO (cliente voltou)."""
    try:
        contact, atend = _resolve_target(payload)
        if not atend:
            return
        at = ensure_protocolo_for_contact(
            contact["id"], phone=contact.get("phone", ""), name=_contact_name(contact),
            conversation_id=atend["id"], announce_open=True)
        ensure_open_cycle(atend["id"], contact["id"], at["id"])
    except Exception as e:  # noqa: BLE001 — um handler que falha nunca quebra o pipeline
        logger.debug("protocolos.on_inbound falhou: %s", e)


def on_outbound(ctx, payload: dict) -> None:
    """``message.sent`` (operador/IA) → garante protocolo + ciclo de bootstrap,
    mas NUNCA abre um ciclo novo logo após uma resolução (evita ciclo fantasma)."""
    try:
        contact, atend = _resolve_target(payload)
        if not atend:
            return
        at = ensure_protocolo_for_contact(
            contact["id"], phone=contact.get("phone", ""), name=_contact_name(contact),
            conversation_id=atend["id"], announce_open=True)
        ensure_cycle_exists(atend["id"], contact["id"], at["id"])
    except Exception as e:  # noqa: BLE001
        logger.debug("protocolos.on_outbound falhou: %s", e)


def on_startup(ctx, payload: dict) -> None:
    """``app.startup`` → backfills one-time idempotentes + registro dos atributos de
    atendimento no core + registro dos avisos de sistema do protocolo. Boot nunca quebra
    por causa daqui (tudo defensivo)."""
    register_system_notices()  # grupo + tipos de aviso (abrir/finalizar protocolo)
    try:
        _maybe_backfill()  # blob `fields` legado → tabelas normalizadas
    except Exception as e:  # noqa: BLE001
        logger.warning("protocolos: backfill no startup falhou (segue sem migrar): %s", e)
    try:
        _maybe_backfill_custom_attrs()  # custom_attributes do ext_demo → Protocolos
    except Exception as e:  # noqa: BLE001
        logger.warning("protocolos: backfill de custom_attributes falhou: %s", e)
    try:
        _maybe_migrate_obs_to_extra()  # obs FIXO (coluna) → rótulo EXTRA (tabela de extras)
    except Exception as e:  # noqa: BLE001
        logger.warning("protocolos: migração obs→extra falhou: %s", e)
    sync_core_atendimento_defs()  # atributos de atendimento do core p/ os campos espelhados


# ── Backfill (blob `fields` legado → tabela normalizada) ──────────────────────

def _safe_json(s) -> dict:
    try:
        v = json.loads(s or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return v if isinstance(v, dict) else {}


def _backfill_blob(conn, scope: str, owner_id: int, blob: dict, by_key: dict,
                   obs_table: str, ts: float) -> None:
    """Migra um blob `fields` legado: observacao/observacoes → rótulo EXTRA obs (def_id
    estável 'fixed_obs'); demais keys → tabela de extras do escopo (key com def atual → seu
    def_id; senão def_id órfão, nunca exibido). ON CONFLICT DO NOTHING = idempotente.
    (`obs_table` mantido por compat de assinatura — obs não vai mais p/ coluna.)"""
    table, owner_col = _EXTRAS_TABLE[scope]
    for k, v in blob.items():
        if k in ("observacao", "observacoes"):
            d = {"id": "fixed_obs", "key": "obs", "label": "Observações", "type": "textarea"}
        else:
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
    a_by_key = {d["key"]: d for d in set_field_defs("protocolo", get_extra_defs("protocolo"))
                if not d.get("fixed")}
    c_by_key = {d["key"]: d for d in set_field_defs("atendimento", get_extra_defs("atendimento"))
                if not d.get("fixed")}
    ts = now()
    with make_plugin_db() as conn:
        crows = conn.execute(
            text("SELECT id, fields FROM plugin_protocolos_atendimentos")).mappings().all()
        for r in crows:
            _backfill_blob(conn, "atendimento", r["id"], _safe_json(r["fields"]),
                           c_by_key, "plugin_protocolos_atendimentos", ts)
        arows = conn.execute(
            text("SELECT id, fields FROM plugin_protocolos_protocolos")).mappings().all()
        for r in arows:
            _backfill_blob(conn, "protocolo", r["id"], _safe_json(r["fields"]),
                           a_by_key, "plugin_protocolos_protocolos", ts)
    config_repo.set(_BACKFILL_FLAG, True)
    logger.info("protocolos: backfill de campos extras concluído")


def _maybe_migrate_obs_to_extra() -> None:
    """UMA vez: obs deixou de ser rótulo FIXO (coluna própria `obs`) e virou rótulo EXTRA
    comum. (a) Se a config de defs de um escopo foi customizada e não tem `obs`, prepende a
    def obs (id estável 'fixed_obs') p/ ela continuar aparecendo (o default já inclui obs via
    DEFAULT_EXTRA_DEFS). (b) Move o valor da coluna `obs` das duas entidades p/ a tabela de
    extras do escopo (def_id 'fixed_obs'). Idempotente: ON CONFLICT DO NOTHING + flag."""
    if config_repo.get(_OBS_MIGRATE_FLAG, False):
        return
    obs_def = {"id": "fixed_obs", "key": "obs", "label": "Observações", "type": "textarea"}
    # (a) config de defs já customizada sem obs → prepend.
    for scope in EXTRA_SCOPES:
        raw = config_repo.get(_defs_key(scope), None)
        if isinstance(raw, list) and not any((d or {}).get("key") == "obs" for d in raw):
            config_repo.set(_defs_key(scope),
                            [{**obs_def, "options": [], "required": False}] + list(raw))
    # (b) valor da coluna obs → tabela de extras do escopo (def_id 'fixed_obs').
    src = {"protocolo": "plugin_protocolos_protocolos",
           "atendimento": "plugin_protocolos_atendimentos"}
    ts = now()
    with make_plugin_db() as conn:
        for scope, src_tbl in src.items():
            table, owner_col = _EXTRAS_TABLE[scope]
            rows = conn.execute(
                text(f"SELECT id, obs FROM {src_tbl} WHERE obs IS NOT NULL AND obs <> ''")
            ).mappings().all()
            for r in rows:
                conn.execute(
                    text(f"INSERT INTO {table} ({owner_col}, def_id, payload, created_at, updated_at) "
                         f"VALUES (:oid, :did, :p, :ts, :ts) "
                         f"ON CONFLICT ({owner_col}, def_id) DO NOTHING"),
                    {"oid": r["id"], "did": "fixed_obs",
                     "p": json.dumps(_extras_payload(obs_def, str(r["obs"])), ensure_ascii=False),
                     "ts": ts})
    config_repo.set(_OBS_MIGRATE_FLAG, True)
    logger.info("protocolos: migração obs→extra concluída")


# ── Backfill dos dados do ext_demo (conversations.custom_attributes → Protocolos) ──
# O ext_demo gravava os campos de "Resolver atendimento" (CSV, default motivo,observacao)
# direto no core (conversations.custom_attributes). Ao aposentar o ext_demo, migramos
# esses valores para as tabelas do Protocolos (obs + extras da atendimento), UMA vez.

def _ext_demo_resolve_keys() -> list[str]:
    """Chaves que o ext_demo gravava em custom_attributes (CSV das settings dele)."""
    raw = config_repo.get("plugin.ext_demo.resolve_fields", "motivo,observacao")
    return [k.strip() for k in str(raw or "").split(",") if k.strip()]


def _maybe_backfill_custom_attrs() -> None:
    """One-time: migra os valores que o ext_demo gravou em conversations.custom_attributes
    para as tabelas do Protocolos. SÓ as chaves do ext_demo são migradas (não outros
    atributos do core). observacao/observacoes → coluna obs; demais → extras da atendimento
    (garantindo a def antes, p/ não virar órfã). Idempotente: ON CONFLICT DO NOTHING +
    guard de obs vazio (em _backfill_blob) + flag no config."""
    if config_repo.get(_CA_BACKFILL_FLAG, False):
        return
    keys = set(_ext_demo_resolve_keys())
    if not keys:
        config_repo.set(_CA_BACKFILL_FLAG, True)
        return
    # Garante que as chaves não-obs existam como defs extras da atendimento (senão o
    # _backfill_blob as gravaria com def_id órfão, e nunca apareceriam na UI).
    obs_like = {"observacao", "observacoes"}
    cur = get_extra_defs("atendimento")
    have = {d["key"] for d in cur}
    additions = [{"key": k, "label": k, "type": "text"}
                 for k in keys if k not in obs_like and k not in have and _KEY_RE.match(k)]
    if additions:
        set_field_defs("atendimento", cur + additions)
    c_by_key = {d["key"]: d for d in get_extra_defs("atendimento")}
    ts = now()
    with make_plugin_db() as conn:
        rows = conn.execute(
            text("SELECT id, contact_id, custom_attributes FROM atendimentos")
        ).mappings().all()
    # Filtra em Python (portável SQLite/Postgres; custom_attributes pode vir str ou dict).
    candidates = []
    for r in rows:
        attrs = r["custom_attributes"]
        attrs = attrs if isinstance(attrs, dict) else _safe_json(attrs)
        sub = {k: v for k, v in attrs.items() if k in keys}
        if sub:
            candidates.append((r["id"], r["contact_id"], sub))
    for atend_id, contact_id, sub in candidates:
        try:
            contact = contact_repo.get(contact_id) or {}
            at = ensure_protocolo_for_contact(
                contact_id, phone=contact.get("phone", ""), name=_contact_name(contact))
            cycle = (get_latest_cycle(atend_id)
                     or ensure_open_cycle(atend_id, contact_id, at["id"]))
            with make_plugin_db() as conn:
                _backfill_blob(conn, "atendimento", cycle["id"], sub, c_by_key,
                               "plugin_protocolos_atendimentos", ts)
        except Exception as e:  # noqa: BLE001 — uma atendimento que falha não trava o resto
            logger.debug("protocolos: backfill CA da atendimento %s falhou: %s", atend_id, e)
    config_repo.set(_CA_BACKFILL_FLAG, True)
    logger.info("protocolos: backfill de custom_attributes concluído (%d atendimentos)",
                len(candidates))


# ── Mensagens de protocolo/avaliação ao FINALIZAR o protocolo ───────────────
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
    """Canal da atendimento mais recente do contato (fallback 'default')."""
    try:
        atend = (conversation_repo.get_open_for_contact(contact_id)
                or conversation_repo.get_latest_for_contact(contact_id))
        if atend:
            full = conversation_repo.get_with_channel(atend["id"])
            if full and full.get("channel_id"):
                return full["channel_id"]
    except Exception:  # noqa: BLE001
        pass
    return "default"


def _channel_for_conversation(conversation_id) -> str | None:
    """Canal (channel_id) de UMA conversa específica — âncora correta em multicanal.
    Diferente de ``_channel_for_contact`` (contact-scoped, funde canais): resolve o
    canal da própria conversa que disparou a ação. None quando indisponível."""
    if not conversation_id:
        return None
    try:
        full = conversation_repo.get_with_channel(int(conversation_id))
        if full and full.get("channel_id"):
            return full["channel_id"]
    except Exception:  # noqa: BLE001
        pass
    return None


def _latest_conversation_of_protocolo(atid: int) -> int | None:
    """conversation_id da atendimento MAIS RECENTE do protocolo (âncora do card de
    finalização). None quando o protocolo ainda não tem atendimento vinculada."""
    try:
        with make_plugin_db() as conn:
            row = conn.execute(
                text("SELECT conversation_id FROM plugin_protocolos_atendimentos "
                     "WHERE protocolo_id = :aid AND conversation_id IS NOT NULL "
                     "ORDER BY started_at DESC, id DESC LIMIT 1"),
                {"aid": int(atid)},
            ).mappings().first()
        return int(row["conversation_id"]) if row else None
    except Exception:  # noqa: BLE001
        return None


def _compose_message(title: str, link: str, params: dict) -> str:
    body = _append_query(link, params)
    return f"{title}\n{body}" if title else body


def send_protocol_on_close(at: dict) -> None:
    """Ao FINALIZAR o protocolo: envia o link normal (WhatsApp) + o link privado
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
        # Canal da atendimento mais recente do protocolo (conversation-scoped), com
        # fallback contact-scoped — evita fundir canais em multicanal (plano 11).
        conv_id = _latest_conversation_of_protocolo((at or {}).get("id"))
        channel_id = (_channel_for_conversation(conv_id)
                      or _channel_for_contact((at or {}).get("contact_id")))

        # 1) Link normal → WhatsApp (envia pelo canal + salva como mensagem do operador).
        if normal.get("link") and outbound:
            text_n = _compose_message(normal.get("title") or "", normal["link"], params)
            try:
                res = outbound.send_text(channel_id, phone, text_n)
                ok = bool(getattr(res, "ok", False))
                mid = getattr(res, "external_msg_id", None)
                # reopen=False: a mensagem de avaliação é enviada no FECHAR — não deve
                # reabrir o atendimento (a conversa voltaria p/ "Abertas"). O WhatsApp já
                # foi enviado acima; aqui é só a persistência/exibição no painel.
                msg = agent_handler.save_operator_message(
                    phone, text_n, status="operator" if ok else "failed",
                    msg_id=mid, channel_id=channel_id, reopen=False)
                broadcast("new_message", {"phone": phone, "message": msg})
            except Exception as e:  # noqa: BLE001
                logger.warning("protocolos: falha ao enviar link de protocolo: %s", e)

        # 2) Link privado → nota privada (painel-only, NÃO vai ao WhatsApp).
        if priv.get("link"):
            text_p = _compose_message(priv.get("title") or "", priv["link"], params)
            try:
                # MESMO channel_id do link normal — senão a nota privada cairia no
                # inbox "default" (ContactMemory resolve a atendimento POR inbox) e abriria
                # uma atendimento nova em OUTRO canal. Os dois links têm que ir na mesma
                # atendimento do mesmo canal.
                cm = agent_handler._get_contact(phone, channel_id=channel_id)
                # Usa o retorno de add_message (id/ts/conversation_id) em vez de um
                # get_last racy; leva conversation_id/channel_id p/ rotear no painel.
                saved = cm.add_message("private_note", text_p)
                note = {"role": "private_note", "content": text_p,
                        "ts": (saved or {}).get("ts", now()), "status": None,
                        "conversation_id": (saved or {}).get("conversation_id")}
                if saved and saved.get("id"):
                    note["_id"] = saved["id"]
                broadcast("new_message",
                          {"phone": phone, "channel_id": channel_id, "message": note})
            except Exception as e:  # noqa: BLE001
                logger.warning("protocolos: falha ao salvar nota privada de protocolo: %s", e)
    except Exception as e:  # noqa: BLE001
        logger.warning("protocolos: send_protocol_on_close falhou: %s", e)


# ── Enforcement no backend (filter.conversation.before_status) ────────────────

def _check_before_status(payload: dict):
    if (payload or {}).get("new_status") != "closed":
        return payload  # só fechar é gated; reabrir nunca
    if not config_repo.get(f"plugin.{PLUGIN_ID}.enforce_backend", True):
        return payload
    if not any(d.get("required") for d in get_field_defs("atendimento")):
        return payload
    cid = payload.get("conversation_id")
    cycle = get_latest_cycle(cid)
    eff = _effective_values("atendimento", cycle or {})
    # Rótulo Atendente (escopo atendimento) reflete o assignee NATIVO da conversa (não o do ciclo).
    at_def = next((d for d in get_field_defs("atendimento") if d.get("type") == "atendente"), None)
    if at_def:
        eff[at_def["key"]] = (conversation_repo.get(cid) or {}).get("assignee_user_id")
    err = _missing_required("atendimento", eff)
    if err:
        logger.info("protocolos: recusando fechar atendimento %s — %s", cid, err)
        return None  # → HTTP 403
    return payload


async def before_status(ctx, payload):
    """Filter assíncrono: leituras de DB rodam fora do event loop."""
    import asyncio
    if (payload or {}).get("new_status") != "closed":
        return payload
    return await asyncio.to_thread(_check_before_status, payload)


# ── Util ──────────────────────────────────────────────────────────────────────

def _broadcast_changed(contact_id: int | None, protocolo_id: int | None) -> None:
    broadcast("plugin_protocolos_changed",
              {"contact_id": contact_id, "protocolo_id": protocolo_id})
