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
- **Rótulo FIXO** (Atendente): único rótulo fixo (não-criável/removível), presente nos 2
  escopos, sempre OBRIGATÓRIO e escondido da tela de Configurações — só aparece em
  "Resolver atendimento"/"Finalizar protocolo". Seu VALOR liga ao assignee NATIVO. Início/
  Fim/ID vêm das colunas (``started_at``/``ended_at``/``protocolo_id``); OBS é EXTRA default.
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

import asyncio
import json
import logging
import re
import time
import unicodedata
import uuid

from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError

from plugins.context import broadcast, make_plugin_db
from db.engine import get_engine
from db.repositories import (channel_repo, config_repo, contact_repo,
                             conversation_repo, custom_attribute_repo, user_repo)
from db.tables import conversations as _conversations_tbl
from db.tables import messages as _messages_tbl
from server.pagination import CAP_LIST, PAGE_LIST, clamp_limit, clamp_offset

logger = logging.getLogger(__name__)

PLUGIN_ID = "protocolos"
SCOPES = ("protocolo", "atendimento")
EXTRA_SCOPES = ("protocolo", "atendimento")  # ambos têm rótulos extras
FIELD_TYPES = {"text", "textarea", "number", "date", "select", "checkboxes", "radio", "checkbox",
               "atendente"}
# Tipos de campo com OPÇÕES fixas — os únicos que viram colunas de Kanban / filtro dropdown.
_OPTION_FIELD_TYPES = {"select", "radio", "checkboxes"}
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,48}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_BACKFILL_FLAG = f"plugin.{PLUGIN_ID}.campos_extras_backfilled"
# Backfill one-time dos valores que o ext_demo gravou em conversations.custom_attributes.
_CA_BACKFILL_FLAG = f"plugin.{PLUGIN_ID}.custom_attrs_backfilled"
# Liga/desliga o espelho dos campos de resolução no core (conversations.custom_attributes).
_MIRROR_FLAG = f"plugin.{PLUGIN_ID}.mirror_custom_attributes"
# Registro das chaves que ESTE plugin espelhou como atributo de conversa no core. É o
# critério de posse do reconcile: só aposentamos definição que nós criamos.
_MIRROR_KEYS_FLAG = f"plugin.{PLUGIN_ID}.mirrored_attr_keys"
# One-time: obs deixou de ser rótulo FIXO (coluna própria) e virou rótulo EXTRA comum.
_OBS_MIGRATE_FLAG = f"plugin.{PLUGIN_ID}.obs_to_extra_migrated"

# Visualizações personalizadas do Kanban (abas de "Agrupar por"). Nome interno (não vem
# de input) → seguro em f-string SQL.
_VIEWS_TABLE = "plugin_protocolos_kanban_views"
# Preferência POR-USUÁRIO e POR-VISUALIZAÇÃO dos filtros pré-determinados (pessoal x equipe).
_PREFS_TABLE = "plugin_protocolos_user_view_prefs"
# Sentinela p/ "não informado" no update (distingue de None=todos os filtros, []=nenhum).
_UNSET = object()
_VIEW_GROUP_BY = {"status", "atendente", "data", "pfield"}
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
# O ÚNICO rótulo FIXO é "atendente" (nos DOIS escopos): não é criável/editável/removível
# e NÃO aparece na tela de Configurações — só nos formulários de "Resolver atendimento" e
# "Finalizar protocolo", onde é SEMPRE obrigatório (deve-se escolher um atendente). Quando
# já existe atendente salvo, ele é preservado; quando não existe, a UI sugere o usuário
# conectado. Seu valor liga ao atendente NATIVO (assignee) do protocolo/conversa — não é
# armazenado como extra. ID, Início e Fim NÃO são rótulos (vêm automáticos nas colunas);
# Observações é um EXTRA default (editável/removível).
def _atendente_fixed_def() -> dict:
    return {"id": "fixed_atendente", "key": "atendente", "label": "Atendente",
            "type": "atendente", "options": [], "required": True,
            "regex_pattern": "", "regex_cue": "", "multiple": False, "fixed": True}

FIXED_FIELD_DEFS: dict[str, list[dict]] = {
    "protocolo": [_atendente_fixed_def()],
    "atendimento": [_atendente_fixed_def()],
}

# Keys reservadas pelos fixos por escopo ({"atendente"} nos dois).
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
    """Rótulos fixos do escopo. "atendente" é o único fixo hoje: label/tipo imutáveis e
    SEMPRE obrigatório (não editável na UI). Se um dia houver outro fixo, seu `required`
    viria do config (`_fixed_required_key`)."""
    req = set(config_repo.get(_fixed_required_key(scope), []) or [])
    out = []
    for d in FIXED_FIELD_DEFS.get(scope, []):
        nd = dict(d)
        # Atendente é sempre obrigatório; demais fixos (nenhum hoje) leriam do config.
        nd["required"] = True if nd.get("type") == "atendente" else (nd["key"] in req)
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
        if (d or {}).get("type") == "atendente":
            continue  # "atendente" agora é rótulo FIXO — ignora extras legados
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


def _option_field_def(scope: str, key: str) -> dict | None:
    """Def de um campo de OPÇÃO (select/radio/checkboxes) do escopo, por key. None se a def
    não existe ou não é de opção. Usado pelo agrupamento/filtro do Kanban por campo de protocolo."""
    if scope not in SCOPES or not _KEY_RE.match(key or ""):
        return None
    for d in get_field_defs(scope):
        if d.get("key") == key and d.get("type") in _OPTION_FIELD_TYPES:
            return d
    return None


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
    for d in defs:
        key = str((d or {}).get("key") or "").strip()
        if key in fixed_keys or (d or {}).get("fixed"):
            continue  # rótulo fixo não é gerenciado aqui (só o `required` acima)
        if (d or {}).get("type") == "atendente":
            continue  # "atendente" é rótulo FIXO — nunca é criado/persistido como extra
        nd = _normalize_extra_def(d)
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
    (ver ``_coerce_extra``) e exige os obrigatórios EDITÁVEIS (obs + extras). O rótulo
    fixo "atendente" é COAGIDO (p/ rotear o assignee) mas seu `required` NÃO é checado
    aqui — é gate de FECHAMENTO/RESOLVER (``_missing_required`` / ``_check_before_status``)
    e do frontend. checkbox conta sempre como preenchido; checkboxes exige ≥1 opção."""
    defs = {d["key"]: d for d in get_field_defs(scope) if not d.get("readonly")}
    values = values or {}
    clean: dict = {}
    for key, d in defs.items():
        cv, err = _coerce_extra(d, values.get(key))
        if err:
            return clean, err
        clean[key] = cv
    for key, d in defs.items():
        if d.get("type") == "atendente":
            continue  # required do atendente é gate de fechamento + frontend, não do save
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


def _owned_mirror_keys(system_keys: set[str]) -> set[str]:
    """Chaves que ESTE plugin espelhou no core — o "de quem é" do reconcile.

    Sem esse registro a única heurística de posse seria "todo atributo de conversa com
    ``is_system=1``", e um outro plugin que espelhasse atributos de conversa teria as
    definições dele apagadas por nós. O registro é aditivo: cresce a cada sync e nunca
    encolhe (uma chave aposentada continua nossa, senão o órfão voltaria a ser intocável).

    Na PRIMEIRA execução o registro não existe e adotamos as linhas ``is_system=1`` já
    presentes — é o que traz os espelhos criados antes deste reconcile (hoje os únicos
    ``is_system=1`` de conversa do produto) para dentro da gestão.
    """
    raw = config_repo.get(_MIRROR_KEYS_FLAG, None)
    if raw is None:
        adopted = set(system_keys)
        config_repo.set(_MIRROR_KEYS_FLAG, sorted(adopted))
        return adopted
    return {str(k) for k in (raw or []) if str(k)}


def sync_core_atendimento_defs() -> None:
    """Reconcilia (idempotente) as defs EDITÁVEIS da atendimento com os atributos de
    conversa do core, p/ os valores espelhados aparecerem/serem editáveis no painel de
    info. Roda no boot, ao SALVAR os rótulos e a cada espelho de resolução.

    Três direções (antes só existia a primeira, e por isso rótulo apagado virava opção
    fantasma no seletor da aba Avaliação — que lista os atributos do core sem filtro):

    * **Rótulo novo** → cria a definição (``ensure_system_definition``).
    * **Rótulo renomeado/reordenado** → atualiza ``display_name``/``position``. Sem isso
      o nome fica congelado no dia da criação.
    * **Rótulo apagado** → soft-delete da definição, contanto que a chave seja NOSSA
      (ver ``_owned_mirror_keys``). Soft delete: a linha e os valores já gravados em
      ``conversations.custom_attributes`` permanecem no banco, só somem das telas.

    Um rótulo que VOLTA depois de apagado é restaurado (``restore_definition``) — a
    criação seria no-op, já que a linha soft-deletada ocupa a chave.

    Limite conhecido: o ``type`` do core é imutável no update, então trocar o tipo de um
    rótulo já espelhado não re-tipa a definição (o valor continua sendo gravado; só a
    renderização no painel do core segue o tipo antigo). Best-effort de ponta a ponta —
    nunca quebra o fluxo de resolução.
    """
    try:
        desired: dict[str, tuple[str, str, int]] = {}  # key → (label, tipo core, posição)
        for i, d in enumerate(get_field_defs("atendimento")):
            if d.get("readonly") or d.get("type") == "atendente":
                continue  # "atendente" já É a coluna nativa; não vira atributo do core
            desired[d["key"]] = (d.get("label") or d["key"],
                                 _core_attr_type(d.get("type")), i)

        rows = custom_attribute_repo.list_definitions(
            applies_to="conversation", include_deleted=True)
        mirrored = {r["attribute_key"]: r for r in rows if r.get("is_system")}
        owned = _owned_mirror_keys(set(mirrored))
        # Chave tomada por um atributo do USUÁRIO (is_system=0, ativo ou apagado): a
        # criação seria no-op silenciosa. Não sequestramos a definição dele.
        taken = {r["attribute_key"] for r in rows if not r.get("is_system")}

        for key, (label, ctype, pos) in desired.items():
            row = mirrored.get(key)
            if row is None:
                if key in taken:
                    logger.debug("protocolos: rótulo '%s' colide com atributo de conversa "
                                 "do usuário — espelho não registrado", key)
                    continue
                custom_attribute_repo.ensure_system_definition(
                    attribute_key=key, display_name=label, type=ctype,
                    applies_to="conversation", position=pos)
                continue
            if row.get("deleted_at") is not None:
                custom_attribute_repo.restore_definition(row["id"])
            if row.get("display_name") != label or row.get("position") != pos:
                custom_attribute_repo.update_definition(
                    row["id"], display_name=label, position=pos)

        # Aposenta o que é nosso e não é mais rótulo. Linha de outro dono fica intacta.
        for key, row in mirrored.items():
            if key in desired or key not in owned or row.get("deleted_at") is not None:
                continue
            custom_attribute_repo.delete_definition(row["id"])

        grown = owned | set(desired)
        if grown != owned:
            config_repo.set(_MIRROR_KEYS_FLAG, sorted(grown))
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


def get_last_closed_protocolo_for_contact(contact_id: int) -> dict | None:
    """Protocolo FECHADO mais recente do contato (mais novo por ``closed_at``). Base da
    detecção "acabou de fechar e o cliente voltou" (plano 49). ``NULLS LAST`` protege
    contra linhas legadas sem ``closed_at`` (nunca escolhidas na frente de uma fechada
    com timestamp)."""
    with make_plugin_db() as conn:
        row = conn.execute(
            text("SELECT * FROM plugin_protocolos_protocolos "
                 "WHERE contact_id = :cid AND status = 'fechado' "
                 "ORDER BY closed_at DESC NULLS LAST, id DESC LIMIT 1"),
            {"cid": contact_id},
        ).mappings().first()
    return _proto_dict(row) if row else None


def _count_atendimentos_of_protocolo(atid: int) -> int:
    """Nº de atendimentos (ciclos) vinculados ao protocolo — mostrado no popup de vínculo."""
    with make_plugin_db() as conn:
        row = conn.execute(
            text("SELECT COUNT(*) FROM plugin_protocolos_atendimentos WHERE protocolo_id = :id"),
            {"id": atid},
        ).first()
    return int(row[0]) if row else 0


def get_protocolo(atid: int) -> dict | None:
    with make_plugin_db() as conn:
        row = conn.execute(
            text("SELECT * FROM plugin_protocolos_protocolos WHERE id = :id"),
            {"id": atid},
        ).mappings().first()
    return _proto_dict(row) if row else None


def _last_operator_actor(conversation_id: int | None) -> tuple[int | None, str]:
    """(user_id, name) do atendente da mensagem de operador MAIS RECENTE da conversa —
    best-effort para atribuir "Aberto por" quando a abertura veio de um envio manual
    (``message.sent source=operator``, cujo payload NÃO carrega o usuário). Lê a tabela
    core ``messages`` (``sent_by_user_id``/``sent_by_name`` gravados no save do operador).
    Qualquer falha ⇒ (None, "") e o chamador cai no rótulo genérico "Atendente"."""
    if not conversation_id:
        return None, ""
    try:
        from sqlalchemy import select
        m = _messages_tbl
        with get_engine().connect() as conn:
            row = conn.execute(
                select(m.c.sent_by_user_id, m.c.sent_by_name)
                .where(m.c.conversation_id == conversation_id, m.c.status == "operator")
                .order_by(m.c.ts.desc(), m.c.id.desc()).limit(1)
            ).first()
        if row:
            return row[0], str(row[1] or "")
    except Exception as e:  # noqa: BLE001 — resolução do ator nunca quebra a abertura
        logger.debug("protocolos: _last_operator_actor falhou: %s", e)
    return None, ""


def _resolve_opener(source: str, conversation_id: int | None = None,
                    user_id: int | None = None, name: str = "") -> dict:
    """Descreve QUEM abriu, a partir da origem do evento/ação:
    ``{"kind", "user_id", "name"}`` — name é o snapshot exibido ('Contato'/'IA'/nome).

    - ``agent``           → ação explícita do painel (usa user_id/name do current_user)
    - ``inbound``         → mensagem recebida do cliente → "Contato"
    - ``ai``/``private_ai`` → resposta automática da IA → "IA"
    - ``operator``/``echo``/``retry`` → envio do atendente (resolve o nome best-effort)
    - qualquer outro      → assume contato (rótulo seguro)"""
    if source == "agent":
        return {"kind": "agent", "user_id": user_id, "name": name or "Atendente"}
    if source == "inbound":
        return {"kind": "contact", "user_id": None, "name": "Contato"}
    if source in ("ai", "private_ai"):
        return {"kind": "ia", "user_id": None, "name": "IA"}
    if source in ("operator", "echo", "retry"):
        uid, nm = _last_operator_actor(conversation_id)
        return {"kind": "agent", "user_id": uid, "name": nm or "Atendente"}
    return {"kind": "contact", "user_id": None, "name": "Contato"}


_EMPTY_OPENER = {"kind": "", "user_id": None, "name": ""}


def ensure_protocolo_for_contact(contact_id: int, phone: str = "", name: str = "",
                                   conversation_id: int | None = None,
                                   announce_open: bool = False,
                                   opener: dict | None = None) -> dict:
    """Get-or-create do protocolo ABERTO do contato (race-safe via índice parcial).

    Quando ``announce_open`` e ESTA chamada criou o protocolo, grava UMA nota privada
    marcando a abertura com um ID pesquisável (ver ``_write_open_note``). Quem perde a
    corrida (re-seleciona o existente) não grava → idempotente, 1 nota por protocolo.

    ``opener`` (``{kind,user_id,name}`` de ``_resolve_opener``) é gravado SÓ na criação
    real — quem re-seleciona o existente não sobrescreve quem abriu."""
    existing = _select_open_protocolo(contact_id)
    if existing:
        return existing
    ts = now()
    op = opener or _EMPTY_OPENER
    created = False
    try:
        with make_plugin_db() as conn:
            conn.execute(
                text("INSERT INTO plugin_protocolos_protocolos "
                     "(contact_id, contact_phone, contact_name, status, fields, "
                     " opened_by_kind, opened_by_user_id, opened_by_name, "
                     " opened_at, created_at, updated_at) "
                     "VALUES (:cid, :phone, :name, 'aberto', '{}', "
                     " :okind, :ouid, :oname, :ts, :ts, :ts)"),
                {"cid": contact_id, "phone": phone or "", "name": name or "", "ts": ts,
                 "okind": op.get("kind") or "", "ouid": op.get("user_id"),
                 "oname": op.get("name") or ""},
            )
        created = True
    except IntegrityError:
        pass  # perdeu a corrida → o vencedor já existe; re-seleciona abaixo
    at = _select_open_protocolo(contact_id)
    if created and at and announce_open:
        # Só a nota PRIVADA (com o ID pesquisável). NÃO há card de sistema na abertura:
        # a nota já anuncia o protocolo e o card era redundante no fio.
        _write_open_note(at, conversation_id)
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
# Marca a FINALIZAÇÃO (e o re-vínculo) do protocolo como cards de sistema no chat —
# mesmo visual dos avisos de resolver/reabrir ATENDIMENTO (plano 12). A ABERTURA NÃO
# gera card: a nota privada com o ID (``_write_open_note``) já a anuncia. O plugin REGISTRA
# seu próprio grupo + tipos no registry do core (``server.system_notices``) via
# ``plugins.context.register_notice*`` — SEM dar patch no core. Gate por config
# namespaceada do plugin (``plugin.protocolos.system_notice_lifecycle``, default ON).
# Late import do ``server``: logic.py segue importável standalone nos testes.

_NOTICE_GROUP = "protocolo_lifecycle"
_NOTICE_CONFIG_KEY = f"plugin.{PLUGIN_ID}.system_notice_lifecycle"


def _f_protocolo_closed(actor=None, **_) -> str:
    return f"🏁 {actor} finalizou o protocolo." if actor else "🏁 Protocolo finalizado."


def _f_protocolo_relinked(actor=None, **_) -> str:
    return (f"🔗 {actor} vinculou este atendimento ao protocolo anterior." if actor
            else "🔗 Atendimento vinculado ao protocolo anterior.")


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
    """Registra (idempotente) o grupo + os tipos de aviso do protocolo no core, e
    sobrescreve os avisos de STATUS do core para a redação "atendimento".
    Best-effort: falha (ex.: ``server`` ausente nos testes) nunca quebra o startup."""
    try:
        from plugins.context import register_notice_group, register_notice
        register_notice_group(_NOTICE_GROUP, "Protocolo (finalizar/vincular)",
                              config_key=_NOTICE_CONFIG_KEY, default=True)
        register_notice("protocolo_closed", _NOTICE_GROUP, _f_protocolo_closed)
        register_notice("protocolo_relinked", _NOTICE_GROUP, _f_protocolo_relinked)
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
        # A atendimento âncora pode ter sido deletada (protocolo órfão: contato/conversa
        # excluídos). Sem thread viva não há onde mostrar o card — no-op LIMPO em vez de
        # tentar inserir e cair num FK error logado (barulho à toa; o fechar já concluiu).
        if conversation_id is not None and conversation_repo.get(conversation_id) is None:
            return
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
                              assignee_name: str = "",
                              propagate_assignee: bool = True) -> tuple[dict | None, str | None]:
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
            assign_protocolo(atid, uid, assignee_name=uname,
                             propagate_to_conversations=propagate_assignee)
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


def _has_open_conversation(atid: int) -> bool:
    """Alguma conversa deste protocolo está ABERTA no core?

    Complementa ``_open_cycles_of_protocolo``: uma conversa pode estar aberta sem ciclo
    aberto (reabertura pelo botão, dados anteriores ao fix do ``on_outbound``) — e nesse
    estado o atendimento está em curso, então o protocolo não pode ser finalizado.
    Best-effort: erro de leitura NÃO trava o Finalizar."""
    try:
        for cid in _conversation_ids_of_protocolo(atid):
            conv = conversation_repo.get(cid)
            if conv and (conv.get("status") or "") != "closed":
                return True
    except Exception as e:  # noqa: BLE001
        logger.debug("protocolos: _has_open_conversation falhou: %s", e)
    return False


def _cycle_is_resolvable(cycle: dict) -> bool:
    """Um ciclo aberto só é 'resolvível' pela UI se aponta para uma conversa (atendimento)
    que AINDA existe no core — é a conversa que o operador abre no popup 'Resolver
    atendimento'. Ciclos órfãos (``conversation_id`` NULL ou conversa deletada) não têm
    caminho de UI para resolver e travariam o Finalizar para sempre."""
    cid = (cycle or {}).get("conversation_id")
    if not cid:
        return False
    try:
        return conversation_repo.get(cid) is not None
    except Exception:  # noqa: BLE001 — indisponibilidade do repo não deve travar o fechar
        return True  # fail-safe: na dúvida, trata como resolvível (não auto-encerra)


def _close_orphan_cycles(cycles: list[dict]) -> None:
    """Encerra (ended_at = agora) ciclos abertos órfãos — sem conversa viva no core —
    para que o protocolo possa ser finalizado. Best-effort."""
    ids = [c["id"] for c in cycles if c.get("id") is not None]
    if not ids:
        return
    ts = now()
    with make_plugin_db() as conn:
        conn.execute(
            text("UPDATE plugin_protocolos_atendimentos "
                 "SET ended_at = :ts, updated_at = :ts "
                 "WHERE id = ANY(:ids) AND ended_at IS NULL"),
            {"ts": ts, "ids": ids},
        )


def _close_orphan_cycles_of_conversation(conversation_id: int) -> None:
    """Encerra ciclos abertos de uma conversa (atendimento) que não existe mais no core.
    Best-effort — usado pelo resolve gracioso quando a conversa foi deletada."""
    ts = now()
    with make_plugin_db() as conn:
        conn.execute(
            text("UPDATE plugin_protocolos_atendimentos "
                 "SET ended_at = :ts, updated_at = :ts "
                 "WHERE conversation_id = :cid AND ended_at IS NULL"),
            {"ts": ts, "cid": conversation_id},
        )


def close_protocolo(atid: int, assignee_user_id: int | None = None,
                      assignee_name: str = "") -> tuple[dict | None, str | None]:
    at = get_protocolo(atid)
    if not at:
        return None, "Protocolo não encontrado."
    if at["status"] == "fechado":
        return at, None
    # Só finaliza quando a ÚLTIMA atendimento do protocolo estiver resolvida: se há ciclo
    # aberto RESOLVÍVEL (conversa viva no core), força resolver antes (a UI abre o popup
    # "Resolver atendimento"). HTTP 400. Ciclos ÓRFÃOS (conversa deletada/NULL) não têm como
    # ser resolvidos pela UI — auto-encerra-os aqui para não travar o Finalizar (robustez).
    open_cycles = _open_cycles_of_protocolo(atid)
    orphan = [c for c in open_cycles if not _cycle_is_resolvable(c)]
    live = [c for c in open_cycles if _cycle_is_resolvable(c)]
    if live:
        return None, ("Existe um atendimento aberto neste protocolo — "
                      "resolva-o antes de finalizar.")
    # Rede de segurança: conversa ABERTA sem ciclo aberto (ex.: reaberta pelo botão, ou
    # linhas antigas de antes de ``on_outbound`` garantir o ciclo). Sem isto dava para
    # finalizar o protocolo com o atendimento em curso. Resolver o atendimento cria+fecha
    # o ciclo que falta (``resolve_atendimento``), então o caminho de saída existe.
    if _has_open_conversation(atid):
        return None, ("Existe um atendimento aberto neste protocolo — "
                      "resolva-o antes de finalizar.")
    # Exige os rótulos OBRIGATÓRIOS (OBS + extras) antes de fechar — lidos do que já
    # está salvo (a UI grava os campos antes de fechar). Valida ANTES de qualquer
    # escrita para não deixar efeito colateral (ciclo órfão encerrado) num erro.
    err = _missing_required("protocolo", _effective_values("protocolo", at))
    if err:
        return None, err
    # Só órfãos (ou nenhum ciclo aberto) e required OK: encerra os ciclos órfãos
    # (conversa deletada — não resolvíveis pela UI) e finaliza o protocolo.
    if orphan:
        _close_orphan_cycles(orphan)
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
                     "closed_at = NULL, relink_reviewed = 0, updated_at = :ts WHERE id = :id"),
                {"ts": ts, "id": atid},
            )
    except IntegrityError:
        return None, "Já existe um protocolo aberto para este contato."
    # Reabrir o protocolo é o atendente RETOMANDO o atendimento: cancela a devolução
    # automática à IA que estivesse pendente nas conversas deste protocolo.
    clear_ai_holds_of_protocolo(atid)
    _broadcast_changed(at["contact_id"], atid)
    return get_protocolo(atid), None


def _discard_protocolo(atid: int) -> None:
    """Descarta um protocolo TRANSITÓRIO (recém-criado, ``status='aberto'``) + seus extras.
    Usado no vínculo ao anterior: o protocolo novo é absorvido pelo anterior e some.
    Só apaga se estiver ABERTO (nunca destrói um fechado com histórico)."""
    with make_plugin_db() as conn:
        conn.execute(
            text("DELETE FROM plugin_protocolos_protocolo_extras WHERE protocolo_id = :id"),
            {"id": atid},
        )
        conn.execute(
            text("DELETE FROM plugin_protocolos_protocolos "
                 "WHERE id = :id AND status = 'aberto'"),
            {"id": atid},
        )


def relink_to_previous(previous_id: int, current_open_id: int | None = None,
                        actor: str | None = None,
                        conversation_id: int | None = None) -> tuple[dict | None, str | None]:
    """Vincula o atendimento atual ao protocolo ANTERIOR (plano 49): move os ciclos do
    protocolo novo para o anterior, descarta o novo e reabre o anterior.

    Ordem OBRIGATÓRIA por causa do índice único "1 aberto por contato": mover ciclos →
    descartar o novo (``_discard_protocolo``) → ``reopen_protocolo`` — reabrir antes de
    descartar colidiria (``IntegrityError``). Emite o aviso ``protocolo_relinked`` +
    ``_broadcast_changed``.

    Convenção de erro (o route mapeia): "não encontrado" → 404; demais → 409."""
    prev = get_protocolo(previous_id)
    if not prev:
        return None, "Protocolo não encontrado."
    if prev["status"] != "fechado":
        return None, "O protocolo anterior não está fechado."
    contact_id = prev["contact_id"]
    # O ``current_open_id`` que vem do frontend pode estar DEFASADO: o ``auto_link`` abre
    # um protocolo novo ao chegar/enviar mensagem, e isso pode acontecer DEPOIS que o popup
    # calculou a sugestão (o id chega nulo ou velho). Por isso validamos o que veio (guard
    # defensivo de contato-errado) mas a FONTE DA VERDADE do "aberto a absorver" é o
    # protocolo aberto ATUAL do contato no banco — senão o ``reopen`` do anterior colidiria
    # com o aberto novo ("Já existe um protocolo aberto para este contato").
    if current_open_id is not None and current_open_id != previous_id:
        cur = get_protocolo(current_open_id)
        if cur and cur["contact_id"] != contact_id:
            return None, "O protocolo anterior não pertence a este contato."
        # cur inexistente/não-aberto → ignora o id e resolve pelo estado atual do banco.
    actual = _select_open_protocolo(contact_id)
    absorb_id = actual["id"] if (actual and actual["id"] != previous_id) else None
    if absorb_id is not None:
        ts = now()
        with make_plugin_db() as conn:
            conn.execute(
                text("UPDATE plugin_protocolos_atendimentos "
                     "SET protocolo_id = :prev, updated_at = :ts "
                     "WHERE protocolo_id = :cur"),
                {"prev": previous_id, "cur": absorb_id, "ts": ts},
            )
        _discard_protocolo(absorb_id)
    at, err = reopen_protocolo(previous_id)
    if err:
        return None, err
    # Garante que a conversa ATUAL fique vinculada ao protocolo reaberto. No caso deferido
    # (auto_link adiado) não houve ciclo/protocolo novo para absorver, então é aqui que o
    # ciclo desta conversa nasce no anterior. ``ensure_open_cycle`` é idempotente (reusa o
    # ciclo aberto se já existir — ex.: foi movido acima).
    if conversation_id is not None:
        try:
            _atd = conversation_repo.get(conversation_id)
            if _atd:
                ensure_open_cycle(conversation_id, _atd["contact_id"], previous_id)
        except Exception as e:  # noqa: BLE001 — vínculo best-effort, não quebra o relink
            logger.debug("protocolos: ensure_open_cycle no relink falhou: %s", e)
    _emit_proto_notice("protocolo_relinked",
                       conversation_id=_latest_conversation_of_protocolo(previous_id),
                       contact_id=contact_id,
                       phone=prev.get("contact_phone") or None, actor=actor)
    _broadcast_changed(contact_id, previous_id)
    return at, None


def merge_into_previous(conversation_id: int, previous_id: int | None = None,
                        actor: str | None = None) -> tuple[dict | None, str | None]:
    """"Faz parte do protocolo anterior" no momento de RESOLVER: FUNDE este re-engajamento
    no protocolo anterior, que segue FINALIZADO.

    Diferente de ``relink_to_previous`` (que reabria o anterior e movia o ciclo novo para
    dentro dele, criando mais um atendimento na lista): aqui nada de novo aconteceu, então
    o protocolo transitório aberto pela mensagem do cliente é DESCARTADO com os ciclos dele
    e apenas a DATA FINAL do último atendimento já existente do anterior é estendida. Os
    valores dos rótulos (do ciclo e do protocolo) ficam EXATAMENTE como estavam no
    fechamento anterior — nada é regravado.

    O protocolo anterior permanece ``fechado`` (só re-carimba ``closed_at``): não reabre,
    então os efeitos de fechar (mensagem de avaliação, religar IA) NÃO disparam de novo.

    Convenção de erro (o route mapeia): "não encontrad*" → 404; demais → 409."""
    atend = conversation_repo.get(conversation_id)
    if not atend:
        return None, "Conversa não encontrada."
    contact_id = atend["contact_id"]
    prev = get_protocolo(previous_id) if previous_id else \
        get_last_closed_protocolo_for_contact(contact_id)
    if not prev or prev.get("contact_id") != contact_id:
        return None, "Protocolo anterior não encontrado para este contato."
    if prev["status"] != "fechado":
        return None, "O protocolo anterior não está fechado."
    ts = now()
    # 1) Descarta o protocolo transitório (aberto pelo auto_link neste re-engajamento) e
    #    TODOS os ciclos dele — é o que garante "nenhum atendimento novo na lista".
    cur = _select_open_protocolo(contact_id)
    if cur and cur["id"] != prev["id"]:
        with make_plugin_db() as conn:
            conn.execute(
                text("DELETE FROM plugin_protocolos_campos_extras WHERE atendimento_id IN "
                     "(SELECT id FROM plugin_protocolos_atendimentos WHERE protocolo_id = :cur)"),
                {"cur": cur["id"]},
            )
            conn.execute(
                text("DELETE FROM plugin_protocolos_atendimentos WHERE protocolo_id = :cur"),
                {"cur": cur["id"]},
            )
        _discard_protocolo(cur["id"])
    # 2) Estende a data final do ÚLTIMO atendimento do anterior (só ended_at/updated_at —
    #    atendente e rótulos permanecem os do fechamento anterior).
    with make_plugin_db() as conn:
        last_id = conn.execute(
            text("SELECT id FROM plugin_protocolos_atendimentos WHERE protocolo_id = :aid "
                 "ORDER BY started_at DESC, id DESC LIMIT 1"),
            {"aid": prev["id"]},
        ).scalar()
        if last_id is not None:
            conn.execute(
                text("UPDATE plugin_protocolos_atendimentos "
                     "SET ended_at = :ts, updated_at = :ts WHERE id = :id"),
                {"ts": ts, "id": int(last_id)},
            )
    if last_id is None:
        # Dado legado: protocolo fechado sem nenhum ciclo. Cria um já encerrado para manter
        # o invariante "protocolo fechado tem ao menos 1 atendimento".
        cycle = _insert_cycle(conversation_id, contact_id, prev["id"])
        with make_plugin_db() as conn:
            conn.execute(
                text("UPDATE plugin_protocolos_atendimentos "
                     "SET ended_at = :ts, updated_at = :ts WHERE id = :id"),
                {"ts": ts, "id": cycle["id"]},
            )
    # 3) Re-carimba o fechamento do protocolo (continua 'fechado').
    with make_plugin_db() as conn:
        conn.execute(
            text("UPDATE plugin_protocolos_protocolos SET closed_at = :ts, updated_at = :ts "
                 "WHERE id = :id"),
            {"ts": ts, "id": prev["id"]},
        )
    _emit_proto_notice("protocolo_relinked", conversation_id=conversation_id,
                       contact_id=contact_id,
                       phone=prev.get("contact_phone") or None, actor=actor)
    _broadcast_changed(contact_id, prev["id"])
    return get_protocolo(prev["id"]), None


def relink_suggestion_for_contact(contact_id: int,
                                  conversation_id: int | None = None) -> dict:
    """Contrato §4 do plano 49: há protocolo FECHADO dentro da janela p/ este contato?

    Devolve ``suggest`` (mostrar o popup?), a janela, o tempo desde o fechamento e um
    resumo do protocolo anterior + do protocolo novo já aberto (se houver). ``suggest``
    só é ``True`` com o toggle ligado E dentro da janela.

    ``attr_decision`` carrega a decisão pendente gravada no atributo personalizado — quando
    presente ela manda e ``suggest`` vira False (não se pergunta o que já está decidido)."""
    enabled = relink_prompt_enabled()
    window_min = relink_window_minutes()
    _attr = read_relink_attr_decision(contact_id, conversation_id)
    out: dict = {
        "suggest": False,
        "enabled": enabled,
        "window_minutes": window_min,
        "seconds_since_close": None,
        "previous": None,
        "current_open": None,
        # No resolver só existem 2 decisões; ``block`` é regra de ABERTURA (ver on_inbound).
        "attr_decision": _attr if _attr in ("previous", "new") else None,
    }
    current = _select_open_protocolo(contact_id)
    if current:
        out["current_open"] = {"id": current["id"], "opened_at": current.get("opened_at")}
    if not enabled:
        return out
    prev = get_last_closed_protocolo_for_contact(contact_id)
    if not prev or prev.get("closed_at") is None:
        return out
    secs = now() - float(prev["closed_at"])
    out["seconds_since_close"] = secs
    out["previous"] = {
        "id": prev["id"],
        "closed_at": prev.get("closed_at"),
        "opened_at": prev.get("opened_at"),
        "assignee_name": prev.get("assignee_name") or "",
        "atendimentos_count": _count_atendimentos_of_protocolo(prev["id"]),
    }
    # A pergunta acontece no RESOLVER (não mais ao abrir a conversa): o protocolo atual já
    # está aberto — a mensagem do cliente sempre abre um. Sugere quando o protocolo anterior
    # fechou dentro da janela, ainda não foi revisado e não é o próprio protocolo atual.
    out["suggest"] = bool(not prev.get("relink_reviewed")
                          and (current is None or current["id"] != prev["id"])
                          and 0 <= secs <= window_min * 60)
    # Decisão gravada num atributo personalizado manda: não pergunta — o frontend aplica
    # ``attr_decision`` direto (``/relink-decision`` com ``source=attr``).
    if out["attr_decision"]:
        out["suggest"] = False
    return out


def mark_relink_reviewed(protocolo_id: int) -> None:
    """Marca o protocolo como "já decidido no popup de continuidade" — para de sugerir e de
    adiar a abertura de um novo protocolo para este re-engajamento (usado no "Fechar tudo")."""
    with make_plugin_db() as conn:
        conn.execute(
            text("UPDATE plugin_protocolos_protocolos SET relink_reviewed = 1, "
                 "updated_at = :ts WHERE id = :id"),
            {"ts": now(), "id": protocolo_id},
        )


def open_new_protocolo(conversation_id: int,
                       opener: dict | None = None) -> tuple[dict | None, str | None]:
    """Ação "É um novo protocolo" do popup de continuidade: abre AGORA o protocolo novo do
    contato desta conversa (o ``auto_link`` foi adiado até esta decisão) + garante o ciclo
    aberto. A partir daqui existe um protocolo aberto, então a sugestão para de aparecer.

    ``opener`` = quem executou a ação (atendente do painel); default vazio → "—"."""
    atend = conversation_repo.get(conversation_id)
    if not atend:
        return None, "Conversa não encontrada."
    contact_id = atend["contact_id"]
    contact = contact_repo.get(contact_id) or {}
    at = ensure_protocolo_for_contact(
        contact_id, phone=contact.get("phone", ""), name=_contact_name(contact),
        conversation_id=conversation_id, announce_open=True, opener=opener)
    ensure_open_cycle(conversation_id, contact_id, at["id"], opener=opener)
    _broadcast_changed(contact_id, at["id"])
    return at, None


# ── Decisão de continuidade gravada num atributo personalizado ───────────────
# Ver o bloco de config ``_sanitize_relink_attr`` para o contrato. Aqui ficam a LEITURA
# (que decisão está pendente), a ESCRITA (o que o atendente escolheu no popup) e a
# APLICAÇÃO (executar a decisão sem perguntar). Tudo best-effort: qualquer erro de
# leitura/escrita degrada para o fluxo manual de sempre, nunca quebra o pipeline.

# Prioridade de avaliação: o bloqueio vence, depois "faz parte do anterior", depois "novo".
_RELINK_ATTR_PRIORITY = ("block", "previous", "new")


def _relink_attr_target(cfg: dict, contact_id, conversation_id):
    """(tabela do core, id) onde o valor do atributo vive, conforme o escopo configurado."""
    if cfg["scope"] == "conversation":
        return (_conversations_tbl, int(conversation_id)) if conversation_id else None
    from db.tables import contacts as _contacts_tbl
    return (_contacts_tbl, int(contact_id)) if contact_id else None


def read_relink_attr_decision(contact_id, conversation_id=None) -> str | None:
    """Decisão PENDENTE gravada no atributo: ``'block'``/``'previous'``/``'new'`` ou None.

    None = nada configurado, nada gravado ou valor sem mapeamento → o popup decide."""
    try:
        cfg = get_relink_attr_config()
        if not cfg["enabled"]:
            return None
        target = _relink_attr_target(cfg, contact_id, conversation_id)
        if not target:
            return None
        stored = (custom_attribute_repo.get_values(*target) or {}).get(cfg["key"])
        for kind in _RELINK_ATTR_PRIORITY:
            if _attr_value_matches(stored, cfg["values"].get(kind)):
                return kind
        return None
    except Exception as e:  # noqa: BLE001 — na dúvida, cai no fluxo manual (popup)
        logger.debug("protocolos: read_relink_attr_decision falhou: %s", e)
        return None


def record_relink_attr_decision(kind: str, contact_id, conversation_id=None) -> None:
    """Grava no atributo o valor mapeado para a decisão que o atendente tomou no popup.
    No-op quando a feature está desligada ou a decisão não tem valor mapeado."""
    try:
        cfg = get_relink_attr_config()
        value = cfg["values"].get(kind) if cfg["enabled"] else ""
        if not value:
            return
        target = _relink_attr_target(cfg, contact_id, conversation_id)
        if not target:
            return
        custom_attribute_repo.set_values(*target, {cfg["key"]: value})
    except Exception as e:  # noqa: BLE001 — gravar o atributo nunca derruba a decisão
        logger.debug("protocolos: record_relink_attr_decision falhou: %s", e)


def clear_relink_attr_value(contact_id, conversation_id=None) -> None:
    """Consome a decisão: remove a chave do ``custom_attributes`` (``set_values`` apaga a
    chave quando o valor é None). No-op quando ``consume`` está desligado."""
    try:
        cfg = get_relink_attr_config()
        if not cfg["enabled"] or not cfg["consume"]:
            return
        target = _relink_attr_target(cfg, contact_id, conversation_id)
        if not target:
            return
        custom_attribute_repo.set_values(*target, {cfg["key"]: None})
    except Exception as e:  # noqa: BLE001
        logger.debug("protocolos: clear_relink_attr_value falhou: %s", e)


def apply_resolve_decision(conversation_id: int, kind: str, *, previous_id=None,
                           source: str = "user",
                           actor: str | None = None) -> tuple[dict, str | None]:
    """Aplica a decisão de continuidade tomada no momento de RESOLVER o atendimento.

    ``kind='previous'`` → FUNDE este re-engajamento no protocolo anterior
    (``merge_into_previous``): descarta o protocolo transitório + seus ciclos, estende a data
    final do último atendimento do anterior e o mantém FINALIZADO (sem atendimento novo, sem
    reabrir, sem reenviar avaliação). ``kind='new'`` → ABRE de fato o protocolo novo deste
    re-engajamento (reusa o que o ``auto_link`` já abriu — o índice só permite 1 aberto por
    contato) e garante um ATENDIMENTO (ciclo) aberto nele; o atendimento NÃO é resolvido,
    quem está no painel segue tocando o caso.

    ``source='user'`` (clique no popup) GRAVA a escolha no atributo personalizado, para que
    ela decida sozinha o próximo ciclo. ``source='attr'`` (a decisão veio do atributo)
    CONSOME o valor. Devolve ``({'applied': kind, ...}, erro)``."""
    if kind not in ("previous", "new"):
        return {}, "Decisão inválida."
    atend = conversation_repo.get(conversation_id)
    if not atend:
        return {}, "Conversa não encontrada."
    contact_id = atend["contact_id"]
    out = {"applied": kind, "protocolo_id": None}
    if kind == "previous":
        prev = get_protocolo(previous_id) if previous_id else \
            get_last_closed_protocolo_for_contact(contact_id)
        if not prev or prev.get("contact_id") != contact_id:
            return {}, "Protocolo anterior não encontrado para este contato."
        at, err = merge_into_previous(conversation_id, previous_id=prev["id"], actor=actor)
        if err:
            return {}, err
        out["protocolo_id"] = (at or {}).get("id")
    else:
        # Protocolo novo DE FATO: reusa o aberto (auto_link) ou cria, e garante o ciclo
        # aberto — é o atendimento novo que o atendente segue tocando.
        at, err = open_new_protocolo(
            conversation_id, opener=_resolve_opener("agent", conversation_id, name=actor or ""))
        if err:
            return {}, err
        out["protocolo_id"] = (at or {}).get("id")
        # Para de sugerir ESTE re-engajamento: sem isto a pergunta voltaria no próximo
        # clique em "Resolver" (o anterior segue fechado dentro da janela) e o atendimento
        # nunca conseguiria ser resolvido. ``reopen_protocolo`` zera a marca.
        prev = get_protocolo(previous_id) if previous_id else \
            get_last_closed_protocolo_for_contact(contact_id)
        if prev and prev.get("contact_id") == contact_id and prev["status"] == "fechado":
            mark_relink_reviewed(prev["id"])
    if source == "attr":
        clear_relink_attr_value(contact_id, conversation_id)
    else:
        record_relink_attr_decision(kind, contact_id, conversation_id)
    return out, None


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
                       assignee_name: str = "",
                       propagate_to_conversations: bool = True) -> tuple[dict | None, str | None]:
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
    if propagate_to_conversations:
        _propagate_assignee_to_conversations(_conversation_ids_of_protocolo(atid), assignee_user_id)
    _broadcast_changed(at["contact_id"], atid)
    return get_protocolo(atid), None


def _hydrate_protocolos(rows) -> list[dict]:
    """Batch: extras do protocolo + rótulos/atributos da última atendimento + atributos de
    contato do dono (evita N+1)."""
    extras = _visible_extras("protocolo", [r["id"] for r in rows])
    out = [_proto_dict(r, extras.get(r["id"], {})) for r in rows]
    _attach_latest_atendimento(out)  # atendimento_fields (última atendimento)
    _attach_contact_attrs(out)       # contact_attrs (atributos de contato do dono)
    _attach_avaliacao(out)           # avaliacao (última nota RESPONDIDA do protocolo)
    return out


def _proto_field_value(a: dict, scope: str, key: str):
    """Valor de um campo de protocolo na row hidratada: escopo protocolo → ``fields``;
    atendimento → ``atendimento_fields`` (última atendimento)."""
    src = a.get("fields") if scope == "protocolo" else a.get("atendimento_fields")
    return (src or {}).get(key)


def _pf_option_keys() -> set[str]:
    """Conjunto de '<scope>:<key>' dos campos de OPÇÃO (select/radio/checkboxes) de ambos os
    escopos. Distingue, no filtro por campo de protocolo, OPÇÃO (casa EXATO/pertence, valor vindo
    de dropdown) de TEXTO/número/data (casa SUBSTRING, valor digitado)."""
    out: set[str] = set()
    for scope in SCOPES:
        for d in get_field_defs(scope):
            if d.get("type") in _OPTION_FIELD_TYPES:
                out.add(f"{scope}:{d.get('key')}")
    return out


# Normalização de texto p/ os filtros da aba: case- E acento-insensível.
# Caminho Python (_norm_txt) e caminho SQL (_ACC_FROM/_ACC_TO via translate) ficam
# consistentes — busca por "leandro"/"joao" casa "Leandro"/"João".
# O mapa cobre minúsculas E MAIÚSCULAS acentuadas, ambas indo para a minúscula ASCII.
# As maiúsculas NÃO são redundantes: este banco roda com collation `C`, onde o `lower()`
# do Postgres é ASCII-only — `lower('JOÃO')` devolve `'joÃo'` (o `Ã` sobrevive). Com um
# mapa só-minúsculo o translate não o pegaria e "joao" não acharia "JOÃO SILVA" (nome em
# caixa alta é comum em dado importado). Mapeando a maiúscula acentuada direto para a
# minúscula ASCII, o resultado fica correto sob qualquer collation.
_ACC_FROM = "áàâãäçéèêëíìîïóòôõöúùûüñÁÀÂÃÄÇÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÑ"
_ACC_TO = "aaaaaceeeeiiiiooooouuuunaaaaaceeeeiiiiooooouuuun"   # MESMO tamanho (translate exige)


def _norm_txt(s) -> str:
    """Normaliza p/ comparação de filtro: trim + remove acentos (NFD, tira
    combinantes) + casefold → case- e acento-insensível. Retorna '' quando None."""
    s = unicodedata.normalize("NFD", str(s if s is not None else ""))
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn").casefold().strip()


def _option_value_set(val) -> set[str]:
    """Conjunto de opções INDIVIDUAIS do valor de um campo de OPÇÃO da row. Aceita lista ou
    escalar e desdobra rótulos legados unidos por vírgula ('teste 1,teste 2' → {'teste 1',
    'teste 2'}) — espelha o split de opções do frontend (splitOptionList), p/ o filtro casar
    mesmo em protocolos gravados antes da correção. Trim, sem vazios."""
    items = val if isinstance(val, list) else ([] if val is None else [val])
    out: set[str] = set()
    for it in items:
        for part in str(it).split(","):
            p = part.strip()
            if p:
                out.add(p)
    return out


def _row_matches_filter(a: dict, fk: str, v, option_keys: set | None = None) -> bool:
    """Casa UM filtro namespaceado contra a row. ``v`` pode ser um ÚNICO valor OU uma LISTA
    (multi-seleção do dropdown) — nesse caso casa se QUALQUER valor escolhido casar (OR):
    - ``pf:<scope>:<key>`` (campo de protocolo) → OPÇÃO: pertence ao conjunto de opções da row
      (valor da row desdobrado por vírgula p/ tolerar rótulos legados unidos); qualquer OUTRO
      tipo (texto/número/data): SUBSTRING case-insensitive (busca parcial). ``option_keys`` =
      conjunto de '<scope>:<key>' de opção; None (chamada direta/legado) → trata como opção.
    - ``cattr:<key>`` (atributo de CONTATO) → SUBSTRING case-insensitive (lista → pertence).
    - ``canal`` (canal da conversa mais recente do protocolo) → igualdade EXATA de channel_id.
    Chave desconhecida/malformada, OU nenhum valor escolhido → não restringe (True)."""
    # Multi-seleção: normaliza p/ lista de candidatos não-vazios; lista vazia = sem restrição.
    cands = [c for c in (v if isinstance(v, list) else [v]) if c is not None and str(c) != ""]
    if not cands:
        return True
    if fk == "canal":
        cv = str(a.get("channel_id") or "")
        return any(cv == str(c) for c in cands)
    if fk.startswith("pf:"):
        parts = fk.split(":", 2)
        if len(parts) != 3:
            return True
        scope, key = parts[1], parts[2]
        val = _proto_field_value(a, scope, key)
        is_option = option_keys is None or f"{scope}:{key}" in option_keys
        if is_option:
            # opções individuais da row (desdobra vírgulas), normalizadas case+acento.
            stored = {_norm_txt(x) for x in _option_value_set(val)}
            return any(_norm_txt(c) in stored for c in cands)
        # Campo de TEXTO: busca parcial (substring) — casa se QUALQUER termo aparecer.
        sval = _norm_txt(", ".join(str(x) for x in val) if isinstance(val, list) else val)
        return any(_norm_txt(c) in sval for c in cands)
    if fk.startswith("cattr:"):
        val = (a.get("contact_attrs") or {}).get(fk.split(":", 1)[1])
        if isinstance(val, list):
            low = [_norm_txt(x) for x in val]
            return any(_norm_txt(c) in low for c in cands)
        sval = _norm_txt(val)
        return any(_norm_txt(c) in sval for c in cands)
    return True


def _list_clause(sql: str, params: dict):
    """text() do listar com bindparams EXPANDING p/ os filtros IN (status/assignee multi-seleção)."""
    binds = [bindparam(k, expanding=True) for k in ("stats", "auids", "notas") if k in params]
    c = text(sql)
    return c.bindparams(*binds) if binds else c


def list_protocolos(*, status=None, assignee_user_id=None,
                      contact_id: int | None = None, q: str | None = None,
                      opened_from: float | None = None, opened_to: float | None = None,
                      attr_filters: dict | None = None, nota=None,
                      include_archived: bool = False,
                      limit: int = 200, offset: int = 0) -> dict:
    """Página de protocolos como envelope ``{items, total, has_more}`` (plano 50).

    ``limit``/``offset`` passam pelo teto central (`clamp_limit`/`clamp_offset`,
    default 50 / cap 200 — `PAGE_LIST`/`CAP_LIST`). Caminho normal pagina no SQL e
    conta o total via COUNT(*) com o mesmo WHERE. Caminho ``attr_filters`` filtra em
    Python após varrer ``_ATTR_SCAN_CAP`` linhas, então ``total``/``has_more`` são
    limite-inferior quando o scan-cap satura (mesma semântica do search ``q`` do core).
    """
    lim = clamp_limit(limit, PAGE_LIST, CAP_LIST)
    off = clamp_offset(offset)
    where, params = _build_list_where(
        status=status, assignee_user_id=assignee_user_id, contact_id=contact_id, q=q,
        opened_from=opened_from, opened_to=opened_to, nota=nota,
        include_archived=include_archived)
    base = ("SELECT * FROM plugin_protocolos_protocolos WHERE " + " AND ".join(where)
            + " ORDER BY (status = 'aberto') DESC, opened_at DESC")
    return _list_page(base, where, params, af_src=attr_filters, lim=lim, off=off)


def _build_list_where(*, status=None, assignee_user_id=None, contact_id=None,
                      q: str | None = None, opened_from=None, opened_to=None,
                      nota=None, include_archived: bool = False) -> tuple[list[str], dict]:
    """WHERE + params da listagem de protocolos.

    Extraído de ``list_protocolos`` para ser COMPARTILHADO com o índice de agrupamento
    do Kanban (``kanban_index.build_index`` via ``scan_protocolos``): as duas leituras
    precisam varrer exatamente o mesmo conjunto, senão contagem e páginas divergem.
    Por isso ``nota`` e ``include_archived`` moram AQUI e não no corpo da listagem —
    do contrário as colunas do Kanban contariam protocolos que a lista esconde.
    """
    where = ["1=1"]
    # Plano 54 (D3 — filtrar na leitura): esconde do quadro/contagem os protocolos cuja
    # conversa CORE mais recente está ARQUIVADA (arquivo agora é por atendimento). Não
    # destrutivo e reversível: desarquivar a conversa faz o protocolo reaparecer. A
    # "conversa mais recente" = o ciclo (plugin_protocolos_atendimentos) de maior
    # started_at/id com conversation_id não-nulo. ``include_archived=True`` desliga o
    # corte (uma futura aba "arquivados" pode usá-lo). Ciclos órfãos (sem conversa) ou
    # protocolos sem nenhum ciclo vinculado nunca casam o NOT IN → continuam visíveis.
    if not include_archived:
        where.append(
            "id NOT IN ("
            " SELECT pa.protocolo_id FROM plugin_protocolos_atendimentos pa"
            " JOIN atendimentos a ON a.id = pa.conversation_id"
            " WHERE a.is_archived = 1 AND pa.id = ("
            "   SELECT p2.id FROM plugin_protocolos_atendimentos p2"
            "   WHERE p2.protocolo_id = pa.protocolo_id AND p2.conversation_id IS NOT NULL"
            "   ORDER BY p2.started_at DESC, p2.id DESC LIMIT 1"
            " ))"
        )
    params: dict = {}
    # status/assignee aceitam UM valor OU uma LISTA (multi-seleção do filtro) → WHERE ... IN.
    stats = [s for s in (status if isinstance(status, list) else [status])
             if s in ("aberto", "fechado")]
    if stats:
        where.append("status IN :stats")
        params["stats"] = stats
    auids = []
    for x in (assignee_user_id if isinstance(assignee_user_id, list) else [assignee_user_id]):
        if x is None or str(x).strip() == "":
            continue
        try:
            auids.append(int(x))
        except (TypeError, ValueError):
            pass
    if auids:
        where.append("assignee_user_id IN :auids")
        params["auids"] = auids
    if contact_id is not None:
        where.append("contact_id = :cid")
        params["cid"] = contact_id
    if q:
        qs = str(q).strip()
        params["q"] = f"%{qs}%"
        # Busca case- E acento-insensível sem depender da extensão `unaccent`:
        # normaliza ambos os lados com lower()+translate() (mapa pt-BR). Os `%` do
        # padrão passam intactos (não estão no mapa).
        _norm_col = "translate(lower({c}), :acc_from, :acc_to)"
        _term = "translate(lower(:q), :acc_from, :acc_to)"
        params["acc_from"] = _ACC_FROM
        params["acc_to"] = _ACC_TO
        clause = (f"{_norm_col.format(c='contact_name')} LIKE {_term}"
                  f" OR {_norm_col.format(c='contact_phone')} LIKE {_term}")
        # Também casa o Nº do protocolo: aceita o id puro ("44") OU o código
        # "AAAAMMDD-HHMMSS-<id>" (o ÚLTIMO grupo de dígitos = id do protocolo).
        digits = re.findall(r"\d+", qs)
        if digits:
            try:
                params["qid"] = int(digits[-1])
                clause += " OR id = :qid"
            except (ValueError, OverflowError):
                pass
        where.append("(" + clause + ")")
    if opened_from is not None:
        where.append("opened_at >= :ofrom")
        params["ofrom"] = float(opened_from)
    if opened_to is not None:
        where.append("opened_at <= :oto")
        params["oto"] = float(opened_to)
    # Filtro por NOTA de avaliação (multi-seleção 1..5): protocolos com ao menos uma
    # avaliação RESPONDIDA cuja nota está na seleção (subquery na tabela de avaliações).
    notas = []
    for x in (nota if isinstance(nota, list) else [nota]):
        if x is None or str(x).strip() == "":
            continue
        try:
            v = int(x)
        except (TypeError, ValueError):
            continue
        if 1 <= v <= 5:
            notas.append(v)
    if notas:
        where.append("id IN (SELECT protocolo_id FROM plugin_protocolos_avaliacoes "
                     "WHERE answered_at IS NOT NULL AND nota IN :notas)")
        params["notas"] = notas
    return where, params


def _list_page(base: str, where: list[str], params: dict, *, af_src, lim: int, off: int) -> dict:
    """Corpo da paginação da LISTA (envelope). Separado do WHERE p/ o índice reusar este."""
    # Filtro por CAMPO DE PROTOCOLO (pf:<scope>:<key>) ou ATRIBUTO DE CONTATO (cattr:<key>).
    # O valor não vive nas colunas do protocolo, então filtra-se em Python ANTES do corte:
    # varre um teto interno, hidrata e só então aplica offset/limit (caro só quando a aba usa
    # este filtro — caminho normal continua com LIMIT/OFFSET no SQL).
    af = {str(k): v for k, v in (af_src or {}).items()
          if k and (str(k).startswith("pf:") or str(k).startswith("cattr:")
                    or str(k) == "canal")}
    if af:
        with make_plugin_db() as conn:
            rows = conn.execute(_list_clause(base + " LIMIT :scan", params),
                                {**params, "scan": _ATTR_SCAN_CAP}).mappings().all()
        out = _hydrate_protocolos(rows)
        if "canal" in af:
            _attach_channels(out)  # só paga a resolução de canal quando a aba filtra por canal
        # Chaves de campo de OPÇÃO (exato) vs TEXTO (substring) — só quando há filtro pf:.
        opt_keys = _pf_option_keys() if any(str(k).startswith("pf:") for k in af) else None
        out = [a for a in out if all(_row_matches_filter(a, fk, v, opt_keys) for fk, v in af.items())]
        total = len(out)
        items = out[off:off + lim]
        return {"items": items, "total": total, "has_more": off + len(items) < total}

    count_sql = "SELECT COUNT(*) FROM plugin_protocolos_protocolos WHERE " + " AND ".join(where)
    with make_plugin_db() as conn:
        total = int(conn.execute(_list_clause(count_sql, params), params).scalar() or 0)
        rows = conn.execute(_list_clause(base + " LIMIT :limit OFFSET :offset", params),
                            {**params, "limit": lim, "offset": off}).mappings().all()
    items = _hydrate_protocolos(rows)
    return {"items": items, "total": total, "has_more": off + len(items) < total}


# ── Kanban agrupado (índice em cache + paginação POR COLUNA) ──────────────────

def scan_protocolos(filters: dict, cap: int):
    """Varredura bounded de rows CRUAS que alimenta o índice do Kanban.

    Mesmo WHERE e mesma ORDEM da listagem (``_build_list_where``), sem hidratar nada —
    a hidratação fica para a página de uma coluna (``hydrate_by_ids``). ``nota`` e
    ``include_archived`` são repassados para o índice enxergar o mesmo recorte da lista.
    """
    f = filters or {}
    where, params = _build_list_where(
        status=f.get("status"), assignee_user_id=f.get("assignee_user_id"),
        contact_id=f.get("contact_id"), q=f.get("q"),
        opened_from=f.get("opened_from"), opened_to=f.get("opened_to"),
        nota=f.get("nota"), include_archived=bool(f.get("include_archived")))
    sql = ("SELECT * FROM plugin_protocolos_protocolos WHERE " + " AND ".join(where)
           + " ORDER BY (status = 'aberto') DESC, opened_at DESC LIMIT :scan")
    with make_plugin_db() as conn:
        return conn.execute(_list_clause(sql, params),
                            {**params, "scan": int(cap)}).mappings().all()


def hydrate_by_ids(ids: list[int]) -> list[dict]:
    """Hidrata SOMENTE os ids pedidos, PRESERVANDO a ordem recebida (a do índice)."""
    ids = [int(i) for i in (ids or [])]
    if not ids:
        return []
    with make_plugin_db() as conn:
        rows = conn.execute(
            text("SELECT * FROM plugin_protocolos_protocolos WHERE id IN :ids")
            .bindparams(bindparam("ids", expanding=True)), {"ids": ids}).mappings().all()
    by_id = {r["id"]: r for r in rows}
    return _hydrate_protocolos([by_id[i] for i in ids if i in by_id])


def grouped_columns(view: dict, filters: dict) -> dict:
    """Colunas do Kanban + contagem EXATA por coluna (do índice em cache)."""
    from . import kanban_index
    idx = kanban_index.get_index(view, filters)
    return {"columns": idx["columns"], "truncated": idx["truncated"],
            "unavailable": idx["unavailable"], "read_only": idx["read_only"]}


def count_protocolos_grouped(view: dict, filters: dict) -> dict:
    """Total real por coluna do Kanban, sem hidratar cards.

    Compatível com o contrato planejado de ``/protocolos/counts``. A implementação
    reusa o índice server-side já usado por ``/grouped/columns``: ele guarda ids por
    coluna, então o total é calculado sem carregar a página de cards no navegador.
    ``exact`` fica falso apenas quando a varredura do índice atingiu o teto de segurança.
    """
    data = grouped_columns(view, filters)
    columns = {str(c.get("id")): int(c.get("total") or 0)
               for c in (data.get("columns") or [])}
    total = sum(columns.values())
    exact = not bool(data.get("truncated"))
    return {"total": total, "columns": columns, "exact": exact,
            "truncated": bool(data.get("truncated")),
            "unavailable": bool(data.get("unavailable")),
            "read_only": bool(data.get("read_only"))}


def grouped_column_page(view: dict, filters: dict, col_id: str,
                        limit: int = PAGE_LIST, offset: int = 0) -> dict:
    """Uma página de UMA coluna — envelope ``{items,total,has_more}``.

    O índice já tem os ids daquela coluna na ordem certa; aqui só fatiamos e
    hidratamos a fatia (nunca a coleção inteira).
    """
    from . import kanban_index
    lim = clamp_limit(limit, PAGE_LIST, CAP_LIST)
    off = clamp_offset(offset)
    ids = kanban_index.get_index(view, filters)["column_ids"].get(str(col_id), [])
    page = ids[off:off + lim]
    return {"items": hydrate_by_ids(page), "total": len(ids),
            "has_more": off + len(page) < len(ids)}


def _attach_latest_atendimento(items: list[dict]) -> None:
    """Anexa a cada protocolo os valores da ÚLTIMA atendimento (ciclo mais recente):
    ``atendimento_fields`` (rótulos do plugin do escopo atendimento — obs + extras). Batch."""
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
    for a in items:
        lc = latest.get(a["id"])
        if not lc:
            a["atendimento_fields"] = {}
            continue
        cf = dict(atend_extras.get(lc["id"], {}))  # obs já vem aqui (rótulo extra)
        a["atendimento_fields"] = cf


def _attach_channels(items: list[dict]) -> None:
    """Anexa ``channel_id`` a cada protocolo — o canal da conversa MAIS RECENTE do protocolo
    (âncora multicanal: protocolo → atendimento-ciclo → conversa do core → inbox → canal).
    Batch (uma query, sem N+1). Só é chamado quando a aba filtra por ``canal``, então o caminho
    comum de listagem não paga esta resolução. Fallback '' quando o protocolo ainda não tem
    conversa vinculada (ou o inbox não tem canal)."""
    if not items:
        return
    for a in items:
        a["channel_id"] = ""
    atids = [a["id"] for a in items]
    with make_plugin_db() as conn:
        rows = conn.execute(
            text("SELECT pa.protocolo_id AS pid, i.channel_id AS channel_id "
                 "FROM plugin_protocolos_atendimentos pa "
                 "JOIN atendimentos a ON a.id = pa.conversation_id "
                 "JOIN inboxes i ON i.id = a.inbox_id "
                 "WHERE pa.protocolo_id IN :ids "
                 "ORDER BY pa.started_at DESC, pa.id DESC")
            .bindparams(bindparam("ids", expanding=True)),
            {"ids": atids},
        ).mappings().all()
    chan: dict[int, str] = {}
    for r in rows:
        chan.setdefault(int(r["pid"]), r["channel_id"] or "")  # 1º = mais recente (ordem desc)
    for a in items:
        a["channel_id"] = chan.get(int(a["id"]), "")


def _contact_core_attrs(contact_ids: list[int]) -> dict[int, dict]:
    """{contact_id: {key: value}} dos ATRIBUTOS PERSONALIZADOS de CONTATO do core
    (``applies_to='contact'``, ``is_system=0`` — criados pelo usuário na tela do core). Lê de
    ``contacts.custom_attributes``. Batch (evita N+1). É a fonte dos FILTROS por atributo de
    contato do Kanban."""
    ids = [int(c) for c in (contact_ids or []) if c]
    if not ids:
        return {}
    try:
        defs = custom_attribute_repo.list_definitions(applies_to="contact")
    except Exception:  # noqa: BLE001
        defs = []
    own = {d["attribute_key"] for d in defs if not d.get("is_system")}
    if not own:
        return {}
    out: dict[int, dict] = {}
    with make_plugin_db() as conn:
        crows = conn.execute(
            text("SELECT id, custom_attributes FROM contacts WHERE id IN :ids")
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


def _attach_contact_attrs(items: list[dict]) -> None:
    """Anexa ``contact_attrs`` a cada protocolo (atributos de CONTATO do dono, is_system=0)."""
    if not items:
        return
    cmap = _contact_core_attrs([a.get("contact_id") for a in items])
    for a in items:
        cid = a.get("contact_id")
        a["contact_attrs"] = cmap.get(int(cid), {}) if cid else {}


def _attach_avaliacao(items: list[dict]) -> None:
    """Anexa ``avaliacao`` a cada protocolo — a ÚLTIMA avaliação RESPONDIDA
    (``{nota, sugestao, answered_at}``), ou ``None`` se ainda não avaliado. Batch (uma
    query, sem N+1). Best-effort: qualquer erro deixa ``avaliacao=None`` (nunca quebra
    a listagem do Kanban)."""
    if not items:
        return
    for a in items:
        a["avaliacao"] = None
    pids = [a["id"] for a in items]
    try:
        with make_plugin_db() as conn:
            rows = conn.execute(
                text("SELECT protocolo_id, nota, sugestao, answered_at "
                     "FROM plugin_protocolos_avaliacoes "
                     "WHERE protocolo_id IN :ids AND answered_at IS NOT NULL "
                     "ORDER BY answered_at DESC, id DESC")
                .bindparams(bindparam("ids", expanding=True)),
                {"ids": pids},
            ).mappings().all()
    except Exception as e:  # noqa: BLE001
        logger.debug("protocolos: _attach_avaliacao falhou: %s", e)
        return
    latest: dict[int, dict] = {}
    for r in rows:
        latest.setdefault(int(r["protocolo_id"]),  # 1º = mais recente (ordem desc)
                          {"nota": r["nota"], "sugestao": r["sugestao"] or "",
                           "answered_at": r["answered_at"]})
    for a in items:
        a["avaliacao"] = latest.get(int(a["id"]))


def with_avaliacao(at: dict | None) -> dict | None:
    """Anexa ``avaliacao`` a UM protocolo já montado (detalhe). Devolve o próprio dict."""
    if at:
        _attach_avaliacao([at])
    return at


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
    d["favorite_filters"] = _avail_list(d.get("favorite_filters"))  # JSON array | None (=nenhum favorito)
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
                   group_field_scope=None, group_date_from=None, group_date_to=None,
                   group_date_grain=None) -> str | None:
    if not (name or "").strip():
        return "Informe um nome para a visualização."
    if scope not in _VIEW_SCOPES:
        return "Escopo inválido."
    if group_by not in _VIEW_GROUP_BY:
        return "Agrupamento inválido."
    if group_by == "pfield":
        # Agrupar exige campo de opção de VALOR ÚNICO (1 card = 1 coluna): select/radio/
        # checkboxes SEM `multiple`. (checkboxes single guarda lista de 1 item — ok p/ coluna.)
        d = _option_field_def(group_field_scope or "", group_attr_key or "")
        if not d or d.get("multiple"):
            return "Selecione um campo de opção (valor único) válido para agrupar."
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
                       group_date_mode=None, group_field_scope=None,
                       group_date_from=None, group_date_to=None, group_date_grain=None,
                       filters=None,
                       available_filters=None,
                       favorite_filters=None,
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
                         group_field_scope=group_field_scope,
                         group_date_from=group_date_from, group_date_to=group_date_to,
                         group_date_grain=group_date_grain)
    if err:
        return None, err
    ts = now()
    fjson = json.dumps(filters if isinstance(filters, dict) else {})
    afjson = json.dumps([str(x) for x in available_filters]) if isinstance(available_filters, list) else None
    favjson = _dump_str_list(favorite_filters)  # favoritos ([]/None → NULL = nenhum favorito)
    cojson = _dump_str_list(column_order)  # ordem das colunas ([]/None → NULL = ordem padrão)
    gak = (group_attr_key or None) if group_by == "pfield" else None
    gdm = (group_date_mode or None) if group_by == "data" else None
    gfs = (group_field_scope or None) if group_by == "pfield" else None
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
                 "(name, scope, owner_user_id, group_by, group_attr_key, group_field_scope, "
                 " group_date_mode, group_date_from, group_date_to, group_date_grain, "
                 " filters, available_filters, favorite_filters, column_order, visibility_roles, "
                 " visibility_users_include, visibility_users_exclude, position, "
                 " created_at, updated_at) "
                 "VALUES (:name, :scope, :owner, :gby, :gak, :gfs, :gdm, :gdf, :gdt, :gdg, "
                 " :filters, :af, :fav, :co, :vr, "
                 " :vi, :ve, :pos, :ts, :ts)"),
            {"name": name, "scope": scope, "owner": owner_user_id, "gby": group_by,
             "gak": gak, "gfs": gfs, "gdm": gdm, "gdf": gdf, "gdt": gdt, "gdg": gdg,
             "filters": fjson, "af": afjson, "fav": favjson, "co": cojson,
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
                       group_date_mode=None, group_field_scope=None,
                       group_date_from=None, group_date_to=None, group_date_grain=None,
                       filters=None,
                       available_filters=_UNSET,
                       favorite_filters=_UNSET,
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
    group_field_scope = cur.get("group_field_scope") if group_field_scope is None else group_field_scope
    group_date_from = cur.get("group_date_from") if group_date_from is None else group_date_from
    group_date_to = cur.get("group_date_to") if group_date_to is None else group_date_to
    group_date_grain = cur.get("group_date_grain") if group_date_grain is None else group_date_grain
    fjson = json.dumps(filters if isinstance(filters, dict) else (cur.get("filters") or {}))
    # available_filters: _UNSET = mantém atual; None = TODOS (NULL); lista = allow-list.
    af_src = cur.get("available_filters") if available_filters is _UNSET else available_filters
    afjson = json.dumps([str(x) for x in af_src]) if isinstance(af_src, list) else None
    # favorite_filters: _UNSET = mantém atual; lista/None substitui ([]/None → NULL = nenhum favorito).
    fav_src = cur.get("favorite_filters") if favorite_filters is _UNSET else favorite_filters
    favjson = _dump_str_list(fav_src)
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
                         group_field_scope=group_field_scope,
                         group_date_from=group_date_from, group_date_to=group_date_to,
                         group_date_grain=group_date_grain)
    if err:
        return None, err
    gak = (group_attr_key or None) if group_by == "pfield" else None
    gdm = (group_date_mode or None) if group_by == "data" else None
    gfs = (group_field_scope or None) if group_by == "pfield" else None
    # Janela + granularidade: só valem quando data + personalizado; senão NULL.
    _custom = group_by == "data" and gdm == "personalizado"
    gdf = (group_date_from or None) if _custom else None
    gdt = (group_date_to or None) if _custom else None
    gdg = (group_date_grain or None) if _custom else None
    ts = now()
    with make_plugin_db() as conn:
        conn.execute(
            text(f"UPDATE {_VIEWS_TABLE} SET name = :name, scope = :scope, group_by = :gby, "
                 "group_attr_key = :gak, group_field_scope = :gfs, group_date_mode = :gdm, "
                 "group_date_from = :gdf, group_date_to = :gdt, group_date_grain = :gdg, "
                 "filters = :filters, "
                 "available_filters = :af, favorite_filters = :fav, column_order = :co, "
                 "visibility_roles = :vr, "
                 "visibility_users_include = :vi, visibility_users_exclude = :ve, "
                 "updated_at = :ts WHERE id = :id"),
            {"name": name, "scope": scope, "gby": group_by, "gak": gak, "gfs": gfs, "gdm": gdm,
             "gdf": gdf, "gdt": gdt, "gdg": gdg,
             "filters": fjson, "af": afjson, "fav": favjson, "co": cojson, "vr": vrjson, "vi": vijson,
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


def set_protocolo_field(atid: int, scope: str, key: str, value) -> tuple[dict | None, str | None]:
    """Drag no kanban agrupado por CAMPO DE PROTOCOLO (de opção): grava o valor do campo do
    ``scope`` no dono certo — protocolo (``plugin_protocolos_protocolo_extras``) ou último
    ciclo de atendimento (``plugin_protocolos_campos_extras``). ``value`` None/"" limpa (cai
    na coluna "Sem valor"). Só campos de opção. Retorna o protocolo re-hidratado."""
    d = _option_field_def(scope, key)
    if not d:
        return None, "Campo de opção inválido."
    at = get_protocolo(atid)
    if not at:
        return None, "Protocolo não encontrado."
    val, err = _coerce_extra(d, value)
    if err:
        return None, err
    with make_plugin_db() as conn:
        if scope == "protocolo":
            upsert_extra(conn, "protocolo", int(atid), d, val)
        else:
            row = conn.execute(
                text("SELECT id FROM plugin_protocolos_atendimentos "
                     "WHERE protocolo_id = :aid ORDER BY started_at DESC, id DESC LIMIT 1"),
                {"aid": int(atid)},
            ).mappings().first()
            if not row:
                return None, "Este protocolo ainda não tem atendimento vinculado."
            upsert_extra(conn, "atendimento", int(row["id"]), d, val)
    _broadcast_changed(at.get("contact_id"), atid)
    out = [get_protocolo(atid)]
    _attach_latest_atendimento(out)
    _attach_contact_attrs(out)
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
                  assignee_name: str = "", opener: dict | None = None) -> dict:
    ts = now()
    op = opener or _EMPTY_OPENER
    with make_plugin_db() as conn:
        conn.execute(
            text("INSERT INTO plugin_protocolos_atendimentos "
                 "(protocolo_id, conversation_id, contact_id, assignee_name, "
                 " opened_by_kind, opened_by_user_id, opened_by_name, "
                 " fields, started_at, created_at, updated_at) "
                 "VALUES (:aid, :cid, :ctid, :aname, "
                 " :okind, :ouid, :oname, '{}', :ts, :ts, :ts)"),
            {"aid": protocolo_id, "cid": conversation_id, "ctid": contact_id,
             "aname": assignee_name or "", "ts": ts,
             "okind": op.get("kind") or "", "ouid": op.get("user_id"),
             "oname": op.get("name") or ""},
        )
    return get_open_cycle(conversation_id, protocolo_id)


def ensure_open_cycle(conversation_id: int, contact_id: int, protocolo_id: int,
                      assignee_name: str = "", opener: dict | None = None) -> dict:
    """Ciclo aberto da atendimento neste protocolo; cria um NOVO se não houver
    (o último foi resolvido ou nunca existiu) — é isso que acumula as linhas.

    ``opener`` (quem abriu ESTE ciclo) só é gravado quando um ciclo novo é criado."""
    cur = get_open_cycle(conversation_id, protocolo_id)
    if cur:
        return cur
    return _insert_cycle(conversation_id, contact_id, protocolo_id, assignee_name, opener)


def ensure_cycle_exists(conversation_id: int, contact_id: int, protocolo_id: int,
                        opener: dict | None = None) -> dict | None:
    """Bootstrap (saída do operador): cria um ciclo SÓ se não houver NENHUM neste
    protocolo — nunca abre um ciclo novo logo após uma resolução."""
    if _count_cycles(conversation_id, protocolo_id) > 0:
        return None
    return _insert_cycle(conversation_id, contact_id, protocolo_id, opener=opener)


def resolve_atendimento(conversation_id: int, values: dict, assignee_name: str = "",
                     assignee_user_id: int | None = None) -> tuple[dict | None, str | None]:
    """Fecha o ciclo ABERTO da atendimento (Fim + OBS + extras). Cria+fecha um se não houver.
    Cada rótulo extra (obs incluso) vai numa linha de campos_extras. Grava o AGENTE que
    resolveu (assignee_user_id + assignee_name) no ciclo."""
    atend = conversation_repo.get(conversation_id)
    if not atend:
        # Robustez: a conversa não existe mais no core (deletada) mas um ciclo aberto
        # ainda a referencia. Não há o que "resolver" no atendimento — encerra o(s)
        # ciclo(s) órfão(s) daquela conversa (best-effort) para não travar o Finalizar,
        # e retorna no-op de sucesso em vez de 404.
        try:
            _close_orphan_cycles_of_conversation(conversation_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("protocolos: falha ao encerrar ciclo órfão de conv %s: %s",
                           conversation_id, e)
        return None, None
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
        if _skip_open_matches((payload or {}).get("text") or "", "received"):
            return  # regra "ignorar abertura": recebida que casa não abre protocolo
        contact, atend = _resolve_target(payload)
        if not atend:
            return
        # A mensagem do cliente SEMPRE reabre/abre o protocolo — a continuidade é decidida
        # depois, ao resolver. A única exceção é o valor de BLOQUEIO do atributo.
        if read_relink_attr_decision(contact["id"], atend["id"]) == "block":
            return
        # Mensagem RECEBIDA → quem abriu (protocolo/ciclo) é o próprio contato.
        opener = _resolve_opener("inbound", atend["id"])
        at = ensure_protocolo_for_contact(
            contact["id"], phone=contact.get("phone", ""), name=_contact_name(contact),
            conversation_id=atend["id"], announce_open=True, opener=opener)
        ensure_open_cycle(atend["id"], contact["id"], at["id"], opener=opener)
    except Exception as e:  # noqa: BLE001 — um handler que falha nunca quebra o pipeline
        logger.debug("protocolos.on_inbound falhou: %s", e)


def on_outbound(ctx, payload: dict) -> None:
    """``message.sent`` (operador/IA) → garante protocolo + ciclo de bootstrap,
    mas NUNCA abre um ciclo novo logo após uma resolução (evita ciclo fantasma).

    Também encerra a posse temporária quando quem enviou foi um ATENDENTE: ele respondeu
    dentro da janela, então fica com a conversa (a IA não reassume no vencimento). Isso
    roda ANTES do gate ``auto_link``/``ignorar abertura`` — posse não depende deles."""
    cancel_ai_hold_on_human_send(payload)
    try:
        if _skip_open_matches((payload or {}).get("text") or "", "sent"):
            return  # regra "ignorar abertura": mensagem enviada casou a regex
        contact, atend = _resolve_target(payload)
        if not atend:
            return
        if read_relink_attr_decision(contact["id"], atend["id"]) == "block":
            return
        # Mensagem ENVIADA → quem abriu depende da origem (atendente logado / IA / echo).
        opener = _resolve_opener((payload or {}).get("source") or "", atend["id"])
        at = ensure_protocolo_for_contact(
            contact["id"], phone=contact.get("phone", ""), name=_contact_name(contact),
            conversation_id=atend["id"], announce_open=True, opener=opener)
        # Conversa ABERTA → garante um ciclo ABERTO: o envio do atendente numa conversa
        # fechada a REABRE antes deste evento (o core salva e só então emite message.sent),
        # e isso é um atendimento NOVO dentro do protocolo. Sem isto o protocolo ficava sem
        # ciclo aberto e dava para finalizá-lo com o atendimento em curso.
        # Conversa FECHADA (ex.: a mensagem de avaliação do fechar, enviada com
        # reopen=False) → só bootstrap: nunca abre ciclo logo após uma resolução.
        if (atend.get("status") or "") != "closed":
            ensure_open_cycle(atend["id"], contact["id"], at["id"], opener=opener)
        else:
            ensure_cycle_exists(atend["id"], contact["id"], at["id"], opener=opener)
    except Exception as e:  # noqa: BLE001
        logger.debug("protocolos.on_outbound falhou: %s", e)


def on_conversation_deleted(ctx, payload: dict) -> None:
    """``conversation.deleted`` (o core deletou uma conversa) → fecha o ciclo órfão do
    plugin e finaliza o protocolo se ele não tiver mais nenhum ciclo aberto.

    Sem isto, deletar a conversa no core deixava o protocolo pendurado em ``aberto`` no
    Kanban para sempre (o ciclo ficava com ``ended_at`` NULL apontando para uma conversa
    que não existe mais). Fechamento QUIET: não envia avaliação nem valida obrigatórios —
    não há como continuar um atendimento cuja conversa sumiu.

    Também apaga um hold de posse temporária pendente — sem isso a varredura tentaria
    devolver à IA uma conversa que não existe mais, a cada passada."""
    try:
        conv_id = (payload or {}).get("conversation_id") or (payload or {}).get("id")
        if not conv_id:
            return
        clear_ai_hold(int(conv_id))
        ts = now()
        affected: set[int] = set()
        with make_plugin_db() as conn:
            rows = conn.execute(
                text("SELECT protocolo_id FROM plugin_protocolos_atendimentos "
                     "WHERE conversation_id = :cv AND ended_at IS NULL"),
                {"cv": conv_id}).mappings().all()
            for r in rows:
                if r["protocolo_id"] is not None:
                    affected.add(int(r["protocolo_id"]))
            if rows:
                conn.execute(
                    text("UPDATE plugin_protocolos_atendimentos SET ended_at = :ts, "
                         "updated_at = :ts WHERE conversation_id = :cv AND ended_at IS NULL"),
                    {"ts": ts, "cv": conv_id})
        for pid in affected:
            _finalize_protocolo_if_no_open_cycle(pid, ts)
    except Exception as e:  # noqa: BLE001 — handler nunca quebra o pipeline
        logger.debug("protocolos.on_conversation_deleted falhou: %s", e)


def _finalize_protocolo_if_no_open_cycle(protocolo_id: int, ts: float) -> None:
    """Finaliza (QUIET) um protocolo ``aberto`` que ficou sem nenhum ciclo aberto —
    limpeza de órfão (conversa deletada). Direto no banco: sem avaliação e sem gate de
    obrigatórios. No-op se o protocolo já está fechado ou ainda tem ciclo aberto (o
    contato ainda tem uma conversa viva vinculada)."""
    contact_id = None
    with make_plugin_db() as conn:
        proto = conn.execute(
            text("SELECT contact_id, status FROM plugin_protocolos_protocolos WHERE id = :id"),
            {"id": protocolo_id}).mappings().first()
        if not proto or proto["status"] != "aberto":
            return
        open_left = conn.execute(
            text("SELECT COUNT(*) FROM plugin_protocolos_atendimentos "
                 "WHERE protocolo_id = :id AND ended_at IS NULL"),
            {"id": protocolo_id}).scalar()
        if open_left and int(open_left) > 0:
            return
        conn.execute(
            text("UPDATE plugin_protocolos_protocolos SET status = 'fechado', "
                 "closed_at = :ts, updated_at = :ts WHERE id = :id AND status = 'aberto'"),
            {"ts": ts, "id": protocolo_id})
        contact_id = proto["contact_id"]
    _broadcast_changed(contact_id, protocolo_id)


def on_startup(ctx, payload: dict) -> None:
    """``app.startup`` → backfills one-time idempotentes + registro dos atributos de
    atendimento no core + registro dos avisos de sistema do protocolo. Boot nunca quebra
    por causa daqui (tudo defensivo)."""
    register_system_notices()  # grupo + tipos de aviso (finalizar/vincular protocolo)
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


def _general_key(name: str) -> str:
    return f"plugin.{PLUGIN_ID}.general_{name}"


def auto_assign_conversation_on_close_enabled() -> bool:
    return bool(config_repo.get(_general_key("auto_assign_conversation_on_close"), True))


def resolve_keep_assignee_enabled() -> bool:
    """Plano 67 — "resolver sem desatribuir o atendente" ligado? Default OFF: o core
    limpa o ``assignee_user_id`` ao fechar, como sempre fez.

    Com a DEVOLUÇÃO temporizada ligada (``ai_takeover_delay_minutes`` > 0 +
    ``reactivate_ai_on_close``), este toggle deixa de ser o caminho normal: o atendente
    é mantido de qualquer jeito durante a janela (ver :func:`clear_assignee_on_close`) e
    a IA reassume no vencimento. Ele continua valendo como "manter para SEMPRE" quando a
    devolução está desligada."""
    return bool(config_repo.get(_general_key("resolve_keep_assignee"), False))


def ai_takeover_delay_minutes() -> int:
    """Minutos que o atendente fica com a conversa depois de resolver, antes de a IA
    reassumir. Default 30. ``0`` = sem janela (a IA volta na hora que o protocolo é
    finalizado, comportamento legado). Valor inválido cai no default; negativo vira 0."""
    try:
        m = int(config_repo.get(_general_key("ai_takeover_delay_minutes"), 30))
    except (TypeError, ValueError):
        return 30
    return max(0, min(m, 10080))  # teto de 7 dias — evita hold eterno por digitação


def ai_takeover_enabled() -> bool:
    """A posse temporária está ativa? Exige a devolução à IA LIGADA e uma janela > 0.
    Desligada, nada é armado e o fluxo é exatamente o de antes."""
    return get_reactivate_ai_on_close_setting() and ai_takeover_delay_minutes() > 0


def clear_assignee_on_close(ctx, value):
    """``filter.conversation.clear_assignee_on_close`` (plano 67) — o core pergunta se
    deve limpar o atendente humano ao FECHAR a conversa.

    Devolve ``False`` (mantém o atendente vinculado) em dois casos:

    * ``resolve_keep_assignee`` ligado — o toggle legado, "manter para sempre";
    * posse temporária ativa (:func:`ai_takeover_enabled`) E a conversa tem um atendente
      humano — ele segura a conversa durante a janela e a IA reassume no vencimento
      (a varredura do lifecycle). Sem atendente não há o que manter: cai no ``value``
      recebido e o hold é armado no modo ``muted`` (IA calada por ``ai_active=0``).

    Nunca levanta: sem ``ctx`` (chamada direta dos testes) ou com a conversa ilegível,
    responde só pelo toggle legado."""
    if resolve_keep_assignee_enabled():
        return False
    if not ai_takeover_enabled():
        return value
    conv_id = (getattr(ctx, "extras", None) or {}).get("conversation_id")
    if not conv_id:
        return value
    try:
        conv = conversation_repo.get(int(conv_id))
    except Exception as e:  # noqa: BLE001 — filtro nunca trava o fechamento
        logger.debug("protocolos: clear_assignee_on_close não leu a conversa %s: %s",
                     conv_id, e)
        return value
    if conv and conv.get("assignee_user_id") is not None:
        return False  # dono humano segura a conversa durante a janela
    return value


def relink_prompt_enabled() -> bool:
    """Popup "vincular ao protocolo anterior" ligado? (plano 49, default ON)."""
    return bool(config_repo.get(_general_key("relink_prompt_enabled"), True))


def relink_window_minutes() -> int:
    """Janela (minutos) do "logo após fechar" para sugerir o vínculo. Default 30;
    valores inválidos/≤0 caem no default (nunca uma janela nula que esconderia o popup)."""
    try:
        v = int(config_repo.get(_general_key("relink_window_minutes"), 30))
    except (TypeError, ValueError):
        return 30
    return v if v > 0 else 30


# ── Decisão de continuidade por ATRIBUTO PERSONALIZADO ───────────────────────
# O admin escolhe UM atributo personalizado do core (escopo contato OU conversa) e
# mapeia um valor para cada decisão do popup de continuidade:
#   previous → "faz parte do protocolo anterior"   new → "é um novo protocolo"
#   block    → "não abrir protocolo" (nem vincula, nem abre — e não reabre a conversa)
# O vínculo é BIDIRECIONAL: a escolha do atendente no popup GRAVA o valor mapeado, e um
# valor já presente DECIDE sozinho o próximo re-engajamento (o popup não aparece). Por
# padrão o valor é CONSUMIDO (removido) ao ser aplicado — decide uma vez, depois volta a
# perguntar. Mapeamento vazio ⇒ aquela decisão não é automatizada nem gravada.

_RELINK_ATTR_KINDS = ("previous", "new", "block")


def _sanitize_relink_attr(cfg) -> dict:
    """Normaliza o sub-objeto ``relink_attr``. Escopo inválido ou chave vazia ⇒ desligado
    (a feature nunca fica "ligada" apontando para lugar nenhum)."""
    cfg = cfg if isinstance(cfg, dict) else {}
    scope = cfg.get("scope")
    scope = scope if scope in ("contact", "conversation") else "contact"
    key = str(cfg.get("key") or "").strip()
    raw_values = cfg.get("values") if isinstance(cfg.get("values"), dict) else {}
    values = {k: str(raw_values.get(k) or "").strip() for k in _RELINK_ATTR_KINDS}
    return {
        # Sem chave de atributo não há como ler/gravar nada — a feature fica desligada.
        "enabled": bool(cfg.get("enabled")) and bool(key),
        "scope": scope,
        "key": key,
        "values": values,
        "consume": bool(cfg.get("consume", True)),
    }


def get_relink_attr_config() -> dict:
    return _sanitize_relink_attr({
        "enabled": config_repo.get(_general_key("relink_attr_enabled"), False),
        "scope": config_repo.get(_general_key("relink_attr_scope"), "contact"),
        "key": config_repo.get(_general_key("relink_attr_key"), ""),
        "values": {k: config_repo.get(_general_key(f"relink_attr_{k}"), "")
                   for k in _RELINK_ATTR_KINDS},
        "consume": config_repo.get(_general_key("relink_attr_consume"), True),
    })


def set_relink_attr_config(cfg: dict) -> dict:
    clean = _sanitize_relink_attr(cfg)
    config_repo.set(_general_key("relink_attr_enabled"), clean["enabled"])
    config_repo.set(_general_key("relink_attr_scope"), clean["scope"])
    config_repo.set(_general_key("relink_attr_key"), clean["key"])
    config_repo.set(_general_key("relink_attr_consume"), clean["consume"])
    for k in _RELINK_ATTR_KINDS:
        config_repo.set(_general_key(f"relink_attr_{k}"), clean["values"][k])
    return get_relink_attr_config()


def get_general_config() -> dict:
    return {
        "auto_assign_conversation_on_close": auto_assign_conversation_on_close_enabled(),
        "resolve_keep_assignee": resolve_keep_assignee_enabled(),
        "ai_takeover_delay_minutes": ai_takeover_delay_minutes(),
        "reactivate_ai_on_close": get_reactivate_ai_on_close_setting(),
        "relink_prompt_enabled": relink_prompt_enabled(),
        "relink_window_minutes": relink_window_minutes(),
        "relink_attr": get_relink_attr_config(),
    }


def set_general_config(cfg: dict) -> dict:
    cfg = cfg or {}
    config_repo.set(_general_key("auto_assign_conversation_on_close"),
                    bool(cfg.get("auto_assign_conversation_on_close")))
    # Plano 67: só grava quando presente (payload legado não zera o valor já gravado).
    if "resolve_keep_assignee" in cfg:
        config_repo.set(_general_key("resolve_keep_assignee"),
                        bool(cfg.get("resolve_keep_assignee")))
    # Religar a IA ao finalizar (key das settings declarativas, mesma que o getter lê —
    # não usa _general_key; só grava quando presente p/ não zerar o default em payload antigo).
    if "reactivate_ai_on_close" in cfg:
        config_repo.set(f"plugin.{PLUGIN_ID}.reactivate_ai_on_close",
                        bool(cfg.get("reactivate_ai_on_close")))
    # Janela da posse temporária (minutos). Só grava quando presente; inválido cai no
    # default 30 e negativo vira 0 (= devolver na hora, comportamento legado).
    if "ai_takeover_delay_minutes" in cfg:
        try:
            d = int(cfg.get("ai_takeover_delay_minutes"))
        except (TypeError, ValueError):
            d = 30
        config_repo.set(_general_key("ai_takeover_delay_minutes"), max(0, min(d, 10080)))
    # Chaves do plano 49: só grava quando presentes (payloads antigos não zeram o default).
    if "relink_prompt_enabled" in cfg:
        config_repo.set(_general_key("relink_prompt_enabled"),
                        bool(cfg.get("relink_prompt_enabled")))
    if "relink_window_minutes" in cfg:
        try:
            m = int(cfg.get("relink_window_minutes"))
        except (TypeError, ValueError):
            m = 30
        config_repo.set(_general_key("relink_window_minutes"), max(1, min(m, 1440)))
    if "relink_attr" in cfg:
        set_relink_attr_config(cfg.get("relink_attr"))
    return get_general_config()


# ── Ignorar abertura por regex (direção configurável) ────────────────────────
# Quando o texto de uma mensagem casa com a regex, o protocolo NÃO é aberto e a
# conversa/atendimento é mantida FECHADA (não reabre). A direção define qual lado
# é analisado: 'sent' (enviada pelo whatsbot: operador/IA), 'received' (recebida
# do contato) ou 'both'. A supressão do reopen é feita pelo core via o filtro
# ``filter.conversation.before_reopen`` (ver ``before_reopen`` abaixo); a supressão
# da abertura do protocolo é feita nos handlers ``on_inbound``/``on_outbound``.

def _skip_key(name: str) -> str:
    return f"plugin.{PLUGIN_ID}.skip_open_{name}"


def get_skip_open_config() -> dict:
    d = str(config_repo.get(_skip_key("direction"), "sent") or "sent")
    return {
        "enabled": bool(config_repo.get(_skip_key("enabled"), False)),
        "regex": str(config_repo.get(_skip_key("regex"), "") or ""),
        "direction": d if d in ("sent", "received", "both") else "sent",
    }


def set_skip_open_config(cfg: dict) -> dict:
    cfg = cfg or {}
    config_repo.set(_skip_key("enabled"), bool(cfg.get("enabled")))
    config_repo.set(_skip_key("regex"), str(cfg.get("regex") or ""))
    d = cfg.get("direction")
    config_repo.set(_skip_key("direction"),
                    d if d in ("sent", "received", "both") else "sent")
    return get_skip_open_config()


def _skip_open_matches(text_value: str, msg_direction: str) -> bool:
    """True se a mensagem deve ser IGNORADA (não abrir protocolo / não reabrir).

    ``msg_direction`` ∈ {'sent','received'} — o lado de quem enviou a mensagem.
    Respeita o toggle, a direção configurada e trata regex inválida como no-match.
    """
    cfg = get_skip_open_config()
    if not cfg["enabled"] or not cfg["regex"]:
        return False
    want = cfg["direction"]
    if want != "both" and want != msg_direction:
        return False
    try:
        return re.search(cfg["regex"], text_value or "") is not None
    except re.error:
        return False


# Escopos aceitos numa regra de "não enviar avaliação". Além dos atributos
# personalizados do CORE (contato/conversa), aceita ``protocolo`` = um rótulo da
# aba "Protocolo" (sistema de campos PRÓPRIO do plugin, lido de ``at["fields"]``).
_SKIP_ATTR_SCOPES = ("contact", "conversation", "protocolo")


# Operadores de uma condição. Os 2 últimos não usam valor (o campo some na tela).
_SKIP_OPS = ("eq", "neq", "contains", "not_contains", "filled", "empty")
_SKIP_OPS_NO_VALUE = ("filled", "empty")
# Como as condições da MESMA linha se combinam: qualquer uma (OU) ou todas (E).
_SKIP_JOINS = ("any", "all")


def _sanitize_skip_conditions(raw, legacy_value: str = "") -> list:
    """Normaliza as condições ``[{op, value}]`` de UMA regra — só a FORMA.

    Aceita o formato ANTIGO (a regra tinha um único ``value`` implicitamente "igual a")
    via ``legacy_value`` — config gravada antes das condicionais continua valendo.

    Condição com valor em branco é PRESERVADA (quem a ignora é a avaliação, ver
    :func:`_condition_is_active`): o operador costuma escolher o operador antes de
    digitar o valor, e descartar aqui apagaria a escolha dele ao salvar.
    """
    items = raw if isinstance(raw, list) else None
    if items is None:
        items = [{"op": "eq", "value": legacy_value}] if legacy_value else []
    out = []
    for c in items:
        if not isinstance(c, dict):
            continue
        op = str(c.get("op") or "eq").strip().lower()
        if op not in _SKIP_OPS:
            continue
        value = "" if op in _SKIP_OPS_NO_VALUE else str(c.get("value") or "").strip()
        out.append({"op": op, "value": value})
    return out


def _sanitize_skip_attrs(raw) -> list:
    """Normaliza as regras da aba Avaliação: ``{key, scope, join, conditions[]}``.

    Descarta itens inválidos (key vazia, escopo fora de :data:`_SKIP_ATTR_SCOPES`).
    Uma regra ainda em branco é MANTIDA (a linha continua na tela até o operador
    removê-la) — quem a ignora é a avaliação.

    Compatibilidade: a chave legada ``value`` (uma regra = uma igualdade) é lida quando
    não há ``conditions``, e é REGRAVADA quando a regra é uma única condição "igual a"
    — assim uma versão anterior do plugin ainda entende a config. Com qualquer outro
    operador a chave é omitida de propósito: uma versão antiga leria ``value`` vazio e
    trataria a regra como inerte (não pula o envio), que é o lado seguro do erro.
    """
    out = []
    for r in (raw or []):
        if not isinstance(r, dict):
            continue
        key = str(r.get("key") or "").strip()
        scope = r.get("scope")
        if not key or scope not in _SKIP_ATTR_SCOPES:
            continue
        conds = _sanitize_skip_conditions(r.get("conditions"), str(r.get("value") or ""))
        join = str(r.get("join") or "any").strip().lower()
        rule = {"key": key, "scope": scope,
                "join": join if join in _SKIP_JOINS else "any",
                "conditions": conds}
        if len(conds) == 1 and conds[0]["op"] == "eq":
            rule["value"] = conds[0]["value"]  # espelho legado (ver docstring)
        out.append(rule)
    return out


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
        "skip_attrs": _sanitize_skip_attrs(config_repo.get(_proto_key("skip_attrs"), [])),
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
    config_repo.set(_proto_key("skip_attrs"), _sanitize_skip_attrs(cfg.get("skip_attrs")))
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


def list_channels() -> list[dict]:
    """Canais disponíveis (não-arquivados) p/ o dropdown do filtro 'Canal' do Kanban.
    Reaproveita ``channel_repo.list_all`` do core; devolve só o que a UI usa
    (``id`` = valor do filtro; ``name`` = rótulo; ``provider`` informativo)."""
    try:
        rows = channel_repo.list_all(include_archived=False)
    except Exception:  # noqa: BLE001
        rows = []
    return [{"id": c["id"], "name": c.get("display_name") or c["id"],
             "provider": c.get("provider")} for c in rows]


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


def _is_orphan_protocolo(at: dict) -> bool:
    """True quando a conversa (atendimento) MAIS RECENTE do protocolo já não existe no
    core — o contato/conversa foi excluído e o protocolo ficou órfão. Nesse caso não há
    contexto/alvo válido para a avaliação: enviá-la cairia na conversa NOVA do mesmo
    número (sem relação com este protocolo). Só considera órfão quando há um
    conversation_id gravado que sumiu; protocolo sem conversa nenhuma não é órfão."""
    conv_id = _latest_conversation_of_protocolo((at or {}).get("id"))
    if conv_id is None:
        return False
    try:
        return conversation_repo.get(conv_id) is None
    except Exception:  # noqa: BLE001 — na dúvida, não trata como órfão (não pula)
        return False


def _attr_value_matches(stored, wanted) -> bool:
    """True se o valor ARMAZENADO do atributo bate com o valor DESEJADO da regra.

    Case-insensitive/trim. Trata: string simples, lista nativa (checkboxes/list) e
    string multi espelhada (", ".join do mirror de campos do plugin)."""
    w = str(wanted or "").strip().lower()
    if not w:
        return False
    if isinstance(stored, list):
        return any(str(x).strip().lower() == w for x in stored)
    s = str(stored if stored is not None else "").strip().lower()
    if s == w:
        return True
    if "," in s:
        return any(part.strip() == w for part in s.split(","))
    return False


def _stored_parts(stored) -> list:
    """Valor armazenado → lista de pedaços normalizados (minúsculo, sem espaços).

    Cobre os 3 formatos que chegam aqui: lista nativa (checkboxes/atributo de lista),
    string multi espelhada (``", ".join`` do mirror de campos do plugin) e string
    simples. Um valor com vírgula vira vários pedaços — é assim que "igual a" já
    casava um item dentro de uma seleção múltipla."""
    if isinstance(stored, list):
        raw = [str(x) for x in stored]
    else:
        s = str(stored if stored is not None else "")
        raw = s.split(",") if "," in s else [s]
    return [p.strip().lower() for p in raw if p.strip()]


def _condition_is_active(cond: dict) -> bool:
    """Condição que de fato filtra. Operador que exige valor e está sem valor é
    ignorado (regra antiga: "valor vazio nunca casa") — vale tanto para o modo OU
    quanto para o E, onde senão um campo em branco travaria a linha inteira."""
    op = (cond or {}).get("op") or "eq"
    return op in _SKIP_OPS_NO_VALUE or bool(str((cond or {}).get("value") or "").strip())


def _condition_matches(stored, cond: dict) -> bool:
    """Uma condição ``{op, value}`` contra o valor armazenado do atributo."""
    op = (cond or {}).get("op") or "eq"
    parts = _stored_parts(stored)
    if op == "filled":
        return bool(parts)
    if op == "empty":
        return not parts
    want = str((cond or {}).get("value") or "").strip().lower()
    if op == "eq":
        return any(p == want for p in parts)
    if op == "neq":
        # "diferente de" NÃO exige o atributo preenchido: um contato sem o atributo
        # é, de fato, diferente do valor. Espelha o comportamento de um filtro comum.
        return not any(p == want for p in parts)
    if op == "contains":
        return any(want in p for p in parts)
    if op == "not_contains":
        return not any(want in p for p in parts)
    return False


def _rule_matches(stored, rule: dict) -> bool:
    """Regra de uma LINHA: várias condições sobre o MESMO atributo, combinadas por
    ``join`` (``any`` = OU, ``all`` = E). Sem condição efetiva → não casa (inerte)."""
    conds = [c for c in ((rule or {}).get("conditions") or []) if _condition_is_active(c)]
    if not conds:
        # Regra legada (só ``value``) que não passou pelo saneamento novo.
        legacy = str((rule or {}).get("value") or "").strip()
        return _condition_matches(stored, {"op": "eq", "value": legacy}) if legacy else False
    if (rule or {}).get("join") == "all":
        return all(_condition_matches(stored, c) for c in conds)
    return any(_condition_matches(stored, c) for c in conds)


def _should_skip_evaluation(at: dict, conv_id) -> bool:
    """Decide se as mensagens da aba Avaliação devem ser PULADAS para este contato.

    Lê as regras {key, scope, join, conditions[]} da config e compara com os
    custom_attributes do contato e/ou da conversa E com os rótulos da aba "Protocolo"
    (escopo ``protocolo``, campos próprios do plugin). Dentro de uma LINHA as condições
    se combinam por ``join`` (OU/E); ENTRE linhas é sempre OU — qualquer regra que casar
    → pula (retorna True). Best-effort: erro de leitura NÃO bloqueia o envio (False)."""
    try:
        rules = get_protocol_config().get("skip_attrs") or []
        if not rules:
            return False
        contact_vals, conv_vals, proto_vals = {}, {}, {}
        # Escopos cujos valores dá para LER neste fechamento. Um escopo de fora (ex.:
        # regra de conversa num fechamento sem conversation_id) é ignorado em vez de
        # lido como vazio — senão um "está vazio" pularia o envio por falta de dado.
        available = {"protocolo"}
        cid = (at or {}).get("contact_id")
        if cid and any(r.get("scope") == "contact" for r in rules):
            from db.tables import contacts as _contacts_tbl
            contact_vals = custom_attribute_repo.get_values(_contacts_tbl, cid) or {}
        if cid:
            available.add("contact")
        if conv_id and any(r.get("scope") == "conversation" for r in rules):
            conv_vals = custom_attribute_repo.get_values(_conversations_tbl, conv_id) or {}
        if conv_id:
            available.add("conversation")
        if any(r.get("scope") == "protocolo" for r in rules):
            # `at` já vem hidratado com `fields` (``_proto_dict``); re-hidrata só se
            # o chamador passou uma row crua.
            proto_vals = (at or {}).get("fields")
            if proto_vals is None:
                proto_vals = ((get_protocolo((at or {}).get("id")) or {}).get("fields") or {})
        by_scope = {"contact": contact_vals, "conversation": conv_vals,
                    "protocolo": proto_vals}
        for r in rules:
            if r.get("scope") not in available:
                continue
            vals = by_scope.get(r.get("scope")) or {}
            if _rule_matches(vals.get(r.get("key")), r):
                return True
        return False
    except Exception as e:  # noqa: BLE001 — nunca travar o envio por erro de leitura
        logger.debug("protocolos: _should_skip_evaluation falhou: %s", e)
        return False


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

        # Protocolo ÓRFÃO (conversa do atendimento foi excluída): não há alvo válido para a
        # avaliação — enviá-la agora cairia na conversa NOVA do mesmo número, sem relação com
        # este protocolo antigo. Pula o envio (WhatsApp + nota privada) e só registra o motivo.
        if _is_orphan_protocolo(at):
            logger.info("protocolos: avaliação pulada (protocolo órfão — conversa inexistente) "
                        "— protocolo %s", (at or {}).get("id"))
            return

        channel_id = (_channel_for_conversation(conv_id)
                      or _channel_for_contact((at or {}).get("contact_id")))

        # Regra "pular avaliação": se o contato/conversa tiver um atributo personalizado
        # com um dos valores configurados, NÃO envia nem a mensagem normal nem a privada.
        if _should_skip_evaluation(at, conv_id):
            logger.info("protocolos: avaliação pulada (atributo personalizado) — protocolo %s",
                        (at or {}).get("id"))
            return

        # Round-trip da avaliação (SÓ o link do CLIENTE — plano 50 Q4): persiste o
        # id_protocol com o snapshot de atendente/contato/conversa/canal, para a página
        # externa consultar o atendente e gravar a nota de volta (rotas
        # /public/avaliacao/{id_protocol}). O link privado interno segue sem round-trip.
        if normal.get("link"):
            register_avaliacao(at, id_protocol=params["id_protocol"],
                               conversation_id=conv_id, channel_id=channel_id)

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


# ── Avaliação pública (round-trip da página externa) ─────────────────────────
# A página externa (Cloudflare Worker) chama, com o id_protocol na URL:
#   GET  /api/plugins/protocolos/public/avaliacao/{id_protocol} → nome do atendente
#   POST /api/plugins/protocolos/public/avaliacao/{id_protocol} → grava nota + sugestão
# Rotas isentas de auth (sob /public/); a segurança é id_protocol + rate-limit por IP
# (rate_limited) + uso único (answered_at). Ver plano 50.

_SUGESTAO_CAP = 2000        # teto de caracteres da sugestão do cliente
_RL_WINDOW = 60.0          # janela do rate-limit por IP (segundos)
# Teto GENEROSO de propósito: se a página externa (Cloudflare Worker) chama
# server-side, TODO o tráfego legítimo chega com o MESMO IP de egresso do Worker —
# um teto baixo (ex.: 30) throttlaria clientes reais em burst. Ainda freia um
# enumerador direto de IP único (120/min contra timestamp+random é impraticável).
_RL_MAX = 120              # máximo de requisições públicas por IP na janela
_rl_hits: dict[str, list[float]] = {}   # IP → timestamps recentes (em memória)


def rate_limited(ip: str) -> bool:
    """True se o IP estourou o teto de requisições públicas na janela. Best-effort,
    em memória (reseta a cada restart) — só freia enumeração/spam grosseiro do
    id_protocol. IP vazio nunca é limitado (não dá pra bucketar)."""
    if not ip:
        return False
    t = now()
    hits = [h for h in _rl_hits.get(ip, []) if t - h < _RL_WINDOW]
    if len(hits) >= _RL_MAX:
        _rl_hits[ip] = hits
        return True
    hits.append(t)
    _rl_hits[ip] = hits
    # Poda oportunista p/ limitar memória quando muitos IPs distintos passam.
    if len(_rl_hits) > 5000:
        for k in [k for k, v in _rl_hits.items() if all(t - h >= _RL_WINDOW for h in v)]:
            _rl_hits.pop(k, None)
    return False


def register_avaliacao(at: dict, *, id_protocol: str, conversation_id: int | None,
                       channel_id: str) -> None:
    """Grava a linha de token da avaliação NO FECHAMENTO (snapshot p/ o GET/POST público).
    Best-effort — nunca levanta (o envio do link não pode quebrar por causa disto)."""
    try:
        idp = str(id_protocol or "").strip()
        if not idp or not (at or {}).get("id"):
            return
        assignee_uid = (at or {}).get("assignee_user_id")
        assignee_name = str((at or {}).get("assignee_name") or "")
        if not assignee_name and assignee_uid:
            try:
                u = user_repo.get(int(assignee_uid))
                assignee_name = str((u or {}).get("name") or (u or {}).get("email") or "")
            except Exception:  # noqa: BLE001
                pass
        ts = now()
        with make_plugin_db() as conn:
            conn.execute(
                text("INSERT INTO plugin_protocolos_avaliacoes "
                     "(id_protocol, protocolo_id, contact_id, conversation_id, channel_id, "
                     " assignee_user_id, assignee_name, contact_phone, contact_name, "
                     " created_at, updated_at) "
                     "VALUES (:idp, :pid, :cid, :conv, :chan, :auid, :aname, :phone, :cname, "
                     " :ts, :ts)"),
                {"idp": idp, "pid": (at or {}).get("id"),
                 "cid": (at or {}).get("contact_id"), "conv": conversation_id,
                 "chan": channel_id or "", "auid": assignee_uid, "aname": assignee_name,
                 "phone": (at or {}).get("contact_phone") or "",
                 "cname": (at or {}).get("contact_name") or "", "ts": ts},
            )
    except IntegrityError:
        # id_protocol duplicado (colisão astronômica do random) — não re-registra.
        logger.warning("protocolos: id_protocol duplicado ao registrar avaliação: %s",
                       id_protocol)
    except Exception as e:  # noqa: BLE001
        logger.warning("protocolos: register_avaliacao falhou: %s", e)


def get_avaliacao_public(id_protocol: str) -> dict | None:
    """Consulta pública por id_protocol → ``{atendente, protocolo, ja_avaliado, nota}``.
    ``None`` quando o código não existe (a rota devolve 404)."""
    idp = str(id_protocol or "").strip()
    if not idp:
        return None
    with make_plugin_db() as conn:
        row = conn.execute(
            text("SELECT assignee_name, nota, answered_at "
                 "FROM plugin_protocolos_avaliacoes WHERE id_protocol = :idp"),
            {"idp": idp},
        ).mappings().first()
    if not row:
        return None
    return {
        "atendente": row["assignee_name"] or "",
        "protocolo": idp,
        "ja_avaliado": row["answered_at"] is not None,
        "nota": row["nota"],
    }


def record_avaliacao(id_protocol: str, nota, sugestao: str = "",
                     ip: str = "") -> tuple[dict | None, str | None]:
    """Grava a nota do cliente pela página externa. Valida ``nota ∈ 1..5``, corta a
    sugestão e aplica USO ÚNICO (um UPDATE condicional ``answered_at IS NULL`` serializa
    dois POSTs simultâneos). Devolve ``(dados, None)`` OK | ``(None, erro)``."""
    idp = str(id_protocol or "").strip()
    if not idp:
        return None, "Avaliação não encontrada."
    # bool é subclasse de int (True→1) — rejeita antes p/ não gravar nota de um `true`.
    if isinstance(nota, bool):
        return None, "Nota inválida (use 1 a 5)."
    try:
        n = int(nota)
    except (TypeError, ValueError):
        return None, "Nota inválida (use 1 a 5)."
    if n < 1 or n > 5:
        return None, "Nota inválida (use 1 a 5)."
    sug = str(sugestao or "").strip()[:_SUGESTAO_CAP]
    ts = now()
    with make_plugin_db() as conn:
        row = conn.execute(
            text("SELECT protocolo_id, contact_id, answered_at "
                 "FROM plugin_protocolos_avaliacoes WHERE id_protocol = :idp"),
            {"idp": idp},
        ).mappings().first()
        if not row:
            return None, "Avaliação não encontrada."
        if row["answered_at"] is not None:
            return None, "Esta avaliação já foi respondida."
        res = conn.execute(
            text("UPDATE plugin_protocolos_avaliacoes SET nota = :nota, sugestao = :sug, "
                 "answered_at = :ts, answered_ip = :ip, updated_at = :ts "
                 "WHERE id_protocol = :idp AND answered_at IS NULL"),
            {"nota": n, "sug": sug, "ts": ts, "ip": str(ip or "")[:64], "idp": idp},
        )
    if res.rowcount == 0:
        # Perdeu a corrida: outro POST respondeu entre o SELECT e o UPDATE.
        return None, "Esta avaliação já foi respondida."
    # Atualiza o Kanban/painel ao vivo (a nota passa a aparecer no protocolo).
    _broadcast_changed(row["contact_id"], row["protocolo_id"])
    return {"protocolo": idp, "nota": n, "sugestao": sug}, None


def get_reactivate_ai_on_close_setting() -> bool:
    """Setting ``plugin.protocolos.reactivate_ai_on_close`` (default True): religar a
    IA na conversa ao finalizar o protocolo. Serve de default quando o fechar não traz
    um override explícito ``reactivate_ai`` no corpo (o futuro botão de finalização)."""
    return bool(config_repo.get(f"plugin.{PLUGIN_ID}.reactivate_ai_on_close", True))


def _ai_master_gate(conv_id: int) -> bool:
    """Interruptor GLOBAL (``auto_reply``) + IA do CANAL (``ai_enabled``) da conversa.

    MESMA regra do webhook ``_channel_ai_enabled`` (que é closure e não é importável),
    replicada aqui: global primeiro, depois canal. ``False`` também quando o runtime não
    está cabeado (sem ``deps``) — nesses casos não há como religar a IA mesmo."""
    from plugins.context import get_deps
    deps = get_deps()
    if not deps:
        return False
    if not deps.settings.get("auto_reply", True):
        return False
    from channels import ai_settings
    return bool(ai_settings.value(_channel_for_conversation(conv_id), "ai_enabled", True))


async def reactivate_ai_after_close(at: dict, *, actor_name: str | None = None) -> None:
    """Ao FINALIZAR o protocolo: devolve a conversa à IA se o interruptor GLOBAL
    (``auto_reply``) E a IA do CANAL (``ai_enabled``) estiverem ligados. Mantém a tag
    ``transferido_atendente`` (é só rótulo visual desde o plano 37, não trava mais a IA).
    Best-effort — nunca levanta (não pode quebrar a resposta do fechar).

    Com a POSSE TEMPORÁRIA ativa (janela > 0) a devolução não é imediata: garante que há
    um hold armado para a conversa (cobre finalizar sem ter resolvido agora) e sai — quem
    devolve é a varredura de vencimento. Sem janela (``0``), comportamento legado: devolve
    na hora.

    Devolve via :func:`handoff_to_ai` — a IA volta SEM agente vinculado (quem escolhe é
    o roteamento no próximo turno)."""
    try:
        # Protocolo órfão (conversa excluída): sem alvo válido — não religa.
        if _is_orphan_protocolo(at):
            return
        conv_id = _latest_conversation_of_protocolo((at or {}).get("id"))
        if not conv_id:
            return
        if not _ai_master_gate(conv_id):
            return
        conv = conversation_repo.get(conv_id)
        if not conv:
            return
        if ai_takeover_enabled():
            # Janela ativa: quem devolve é o vencimento. Arma se ainda não há hold (o
            # caminho normal já armou no resolver; isto cobre finalizar isolado).
            if get_ai_hold(conv_id) is None:
                arm_ai_hold(conv, protocolo_id=(at or {}).get("id"), reason="finalizar")
            return
        # Guard anti-ruído: só age se a IA está de fato desligada nesta conversa
        # (ai_active=0 OU humano no comando sem agente de IA) — evita card "ai_on"
        # espúrio em protocolos fechados onde a IA nunca foi desligada.
        human_owned = (conv.get("assignee_user_id") is not None
                       and not conv.get("active_agent_key"))
        if conv.get("ai_active") and not human_owned:
            return
        await handoff_to_ai(conv)
    except Exception as e:  # noqa: BLE001
        logger.warning("protocolos: reactivate_ai_after_close falhou: %s", e)


# ── Devolver a conversa à IA (sem carimbar agente) ───────────────────────────
# NÃO usa ``conversation_service.set_ai``: aquele religa a IA JÁ VINCULANDO o agente
# padrão do inbox (``default_agent_key_for_inbox``), e a conversa reabre carimbada com
# ele. Aqui a devolução é deliberadamente "crua": humano fora, ``ai_active=1`` e
# ``active_agent_key`` NULO — quem decide o agente é o turno seguinte (o roteador/a
# triagem por palavra-chave), quando a mensagem do cliente chegar.
#
# Como não passamos pelo serviço, reproduzimos aqui os efeitos VISÍVEIS dele: os dois
# broadcasts que o painel escuta + o card "🤖 SISTEMA reativou a IA.". A tag
# ``transferido_atendente`` é PRESERVADA (é só rótulo visual desde o plano 37) — mesmo
# comportamento do ``clear_transfer_tag=False`` que o fechar-protocolo sempre usou.
#
# Os broadcasts são WS PURO (``plugins.context.broadcast``), não o bus ``emit`` — mesmo
# padrão que o espelho do modo ``muted``. Emitir ``conversation.ai_toggled`` daqui
# realimentaria :func:`on_conversation_ai_toggled` (o plugin reagindo à própria ação) e
# faria a devolução reentrar no bus dentro do event loop da varredura.

def _conv_ws_payload(conv: dict) -> dict:
    """Espelha o payload que ``conversation_service._broadcast`` manda ao painel."""
    return {
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
    }


async def handoff_to_ai(conv: dict) -> bool:
    """Tira o humano e devolve a conversa à IA **sem vincular agente**.

    Uma escrita atômica (``assign_agent``): ``assignee_user_id=None``,
    ``active_agent_key=None``, ``ai_active=1``. Best-effort; ``True`` quando gravou."""
    conv_id = int(conv["id"])
    try:
        updated = await asyncio.to_thread(
            conversation_repo.assign_agent, conv_id,
            assignee_user_id=None, active_agent_key=None, ai_active=1)
    except Exception as e:  # noqa: BLE001
        logger.warning("protocolos: falha ao devolver a conversa %s à IA: %s", conv_id, e)
        return False
    if not updated:
        return False
    payload = _conv_ws_payload(updated)
    for event in ("conversation_assigned", "conversation_ai_toggled"):
        try:
            broadcast(event, payload)
        except Exception as e:  # noqa: BLE001
            logger.debug("protocolos: broadcast %s falhou conv=%s: %s", event, conv_id, e)
    # Card no fio da conversa. actor=None ⇒ "🤖 SISTEMA reativou a IA." (ação automática).
    # Vai num to_thread: grava mensagem + broadcast, e estamos no event loop da varredura.
    await asyncio.to_thread(
        _emit_proto_notice, "ai_on", conversation_id=conv_id,
        contact_id=updated.get("contact_id"),
        phone=_phone_of_contact(updated.get("contact_id")), actor=None)
    return True


def _phone_of_contact(contact_id) -> str | None:
    """Telefone do contato (o ``get`` da conversa não traz) — chave do broadcast do card."""
    if contact_id is None:
        return None
    try:
        contact = contact_repo.get(int(contact_id))
        return (contact or {}).get("phone")
    except Exception:  # noqa: BLE001
        return None


# ── Posse temporária do atendente pós-fechamento ─────────────────────────────
# "Quem resolveu fica com a conversa por N minutos, depois a IA reassume."
#
# O mecanismo é POSSE, não mordaça: o core já cala a IA quando a conversa tem dono
# humano sem agente vinculado (``messaging_service._conversation_ai_active``), e o
# fechar SEMPRE limpa o ``active_agent_key``. Então manter o atendente (via
# ``clear_assignee_on_close``) já é o silêncio — de graça, por conversa e com o selo
# honesto. Só falta devolver a conversa à IA quando o prazo vence: é o que a varredura
# do lifecycle faz, lendo ``plugin_protocolos_ai_holds``.
#
# Conversa fechada SEM atendente (a própria IA/automação fechou) não tem dono a segurar:
# aí o hold entra no modo ``muted`` e a conversa recebe ``ai_active=0`` durante a janela
# (IA calada + selo "IA OFF"), voltando a 1 no vencimento.
#
# Qualquer ação HUMANA dentro da janela APAGA o hold: o atendente respondeu, reabriu pelo
# painel ou religou a IA na mão — em todos os casos o automático sai de cena.

_HOLDS_TABLE = "plugin_protocolos_ai_holds"


def get_ai_hold(conversation_id: int) -> dict | None:
    """Hold pendente da conversa (ou ``None``). Best-effort: erro ⇒ ``None``."""
    try:
        with make_plugin_db() as conn:
            row = conn.execute(
                text(f"SELECT conversation_id, hold_until, mode, owner_user_id, "
                     f"protocolo_id, reason, set_at FROM {_HOLDS_TABLE} "
                     f"WHERE conversation_id = :cv"),
                {"cv": int(conversation_id)}).mappings().first()
        return dict(row) if row else None
    except Exception as e:  # noqa: BLE001
        logger.debug("protocolos: get_ai_hold falhou conv=%s: %s", conversation_id, e)
        return None


def _write_ai_hold(conversation_id: int, *, hold_until: float, mode: str,
                   owner_user_id: int | None, protocolo_id: int | None,
                   reason: str) -> None:
    """Upsert do hold (uma linha por conversa). Best-effort — uma falha aqui só
    significa que a IA volta no prazo antigo (fail-open, nunca quebra o fechamento)."""
    ts = now()
    try:
        with make_plugin_db() as conn:
            conn.execute(
                text(f"INSERT INTO {_HOLDS_TABLE} (conversation_id, hold_until, mode, "
                     f"owner_user_id, protocolo_id, reason, set_at) "
                     f"VALUES (:cv, :until, :mode, :owner, :proto, :reason, :ts) "
                     f"ON CONFLICT (conversation_id) DO UPDATE SET "
                     f"hold_until = EXCLUDED.hold_until, mode = EXCLUDED.mode, "
                     f"owner_user_id = EXCLUDED.owner_user_id, "
                     f"protocolo_id = COALESCE(EXCLUDED.protocolo_id, {_HOLDS_TABLE}.protocolo_id), "
                     f"reason = EXCLUDED.reason, set_at = EXCLUDED.set_at"),
                {"cv": int(conversation_id), "until": float(hold_until), "mode": mode,
                 "owner": owner_user_id, "proto": protocolo_id,
                 "reason": reason or "", "ts": ts})
    except Exception as e:  # noqa: BLE001
        logger.warning("protocolos: não consegui armar a posse temporária conv=%s: %s",
                       conversation_id, e)


def clear_ai_hold(conversation_id: int) -> bool:
    """Apaga o hold da conversa. ``True`` quando havia um (para o caller decidir se
    precisa religar a IA no modo ``muted``)."""
    try:
        with make_plugin_db() as conn:
            res = conn.execute(
                text(f"DELETE FROM {_HOLDS_TABLE} WHERE conversation_id = :cv"),
                {"cv": int(conversation_id)})
        return bool(res.rowcount)
    except Exception as e:  # noqa: BLE001
        logger.debug("protocolos: clear_ai_hold falhou conv=%s: %s", conversation_id, e)
        return False


def clear_ai_holds_of_protocolo(protocolo_id: int) -> None:
    """Apaga os holds das conversas de um protocolo (reabrir/religar protocolo = o
    atendente está retomando o atendimento). Religa a IA das que estavam ``muted``."""
    try:
        with make_plugin_db() as conn:
            rows = conn.execute(
                text(f"SELECT conversation_id, mode FROM {_HOLDS_TABLE} "
                     f"WHERE protocolo_id = :pid"),
                {"pid": int(protocolo_id)}).mappings().all()
            if not rows:
                return
            conn.execute(text(f"DELETE FROM {_HOLDS_TABLE} WHERE protocolo_id = :pid"),
                         {"pid": int(protocolo_id)})
    except Exception as e:  # noqa: BLE001
        logger.debug("protocolos: clear_ai_holds_of_protocolo falhou pid=%s: %s",
                     protocolo_id, e)
        return
    for r in rows:
        if r["mode"] == "muted":
            _set_conversation_ai_active(int(r["conversation_id"]), 1)


def list_expired_ai_holds(now_ts: float, limit: int = 200) -> list[dict]:
    """Holds cujo prazo VENCEU (``hold_until <= now``), mais antigos primeiro.
    Alimenta a varredura do lifecycle. Best-effort: erro ⇒ lista vazia."""
    try:
        with make_plugin_db() as conn:
            rows = conn.execute(
                text(f"SELECT conversation_id, hold_until, mode, owner_user_id, "
                     f"protocolo_id FROM {_HOLDS_TABLE} WHERE hold_until <= :now "
                     f"ORDER BY hold_until LIMIT :lim"),
                {"now": float(now_ts), "lim": int(limit)}).mappings().all()
        return [dict(r) for r in rows]
    except Exception as e:  # noqa: BLE001
        logger.debug("protocolos: list_expired_ai_holds falhou: %s", e)
        return []


def _set_conversation_ai_active(conversation_id: int, ai_active: int) -> None:
    """Liga/desliga a IA da conversa no CORE + broadcast do selo (modo ``muted``).

    Usa a primitiva low-level ``conversation_repo.set_ai_active`` de propósito: sem mexer
    no assignee, sem card de sistema e — importante — o broadcast abaixo é WS puro (não o
    bus ``emit``), então não realimenta :func:`on_conversation_ai_toggled`."""
    try:
        conv = conversation_repo.set_ai_active(int(conversation_id), int(ai_active))
        if not conv:
            return
        broadcast("conversation_ai_toggled", {
            "conversation_id": conv["id"], "contact_id": conv.get("contact_id"),
            "ai_active": int(ai_active), "ts": now()})
    except Exception as e:  # noqa: BLE001
        logger.debug("protocolos: set_ai_active(%s) falhou conv=%s: %s",
                     ai_active, conversation_id, e)


def arm_ai_hold(conv: dict, *, protocolo_id: int | None = None,
                reason: str = "resolver") -> dict | None:
    """Arma a posse temporária para uma conversa recém-fechada.

    Com atendente (o filtro ``clear_assignee_on_close`` já o preservou) ⇒ modo ``owner``:
    nada é escrito na conversa, o core já cala a IA sozinho. Sem atendente ⇒ modo
    ``muted``: grava ``ai_active=0`` (IA calada + selo "IA OFF" durante a janela).

    Devolve o hold gravado (ou ``None`` quando a feature está desligada)."""
    if not conv or not ai_takeover_enabled():
        return None
    conv_id = int(conv["id"])
    owner = conv.get("assignee_user_id")
    mode = "owner" if owner is not None else "muted"
    until = now() + ai_takeover_delay_minutes() * 60.0
    if protocolo_id is None:
        protocolo_id = _protocolo_id_of_conversation(conv_id)
    _write_ai_hold(conv_id, hold_until=until, mode=mode,
                   owner_user_id=int(owner) if owner is not None else None,
                   protocolo_id=protocolo_id, reason=reason)
    if mode == "muted" and conv.get("ai_active"):
        _set_conversation_ai_active(conv_id, 0)
    logger.info("protocolos: posse temporária armada conv=%s modo=%s até %.0f",
                conv_id, mode, until)
    return get_ai_hold(conv_id)


def _protocolo_id_of_conversation(conversation_id: int) -> int | None:
    """Protocolo do ciclo mais recente da conversa (para limpar o hold ao reabrir o
    protocolo). ``None`` quando a conversa não está vinculada."""
    try:
        with make_plugin_db() as conn:
            row = conn.execute(
                text("SELECT protocolo_id FROM plugin_protocolos_atendimentos "
                     "WHERE conversation_id = :cv ORDER BY id DESC LIMIT 1"),
                {"cv": int(conversation_id)}).mappings().first()
        return int(row["protocolo_id"]) if row and row["protocolo_id"] is not None else None
    except Exception as e:  # noqa: BLE001
        logger.debug("protocolos: _protocolo_id_of_conversation falhou: %s", e)
        return None


def cancel_ai_hold(conversation_id: int, *, restore_ai: bool = True,
                   why: str = "") -> bool:
    """Encerra a posse temporária ANTES do vencimento porque um humano agiu.

    A conversa NÃO é devolvida à IA: quem agiu fica com ela. A exceção é o modo
    ``muted`` (fechada sem dono) — lá não há atendente a preservar, então religar a IA
    (``restore_ai``) mantém o comportamento de sempre. ``True`` quando havia hold."""
    hold = get_ai_hold(conversation_id)
    if hold is None:
        return False
    clear_ai_hold(conversation_id)
    if restore_ai and hold.get("mode") == "muted":
        _set_conversation_ai_active(int(conversation_id), 1)
    logger.info("protocolos: posse temporária encerrada conv=%s (%s)",
                conversation_id, why or "humano agiu")
    return True


def on_conversation_status(ctx, payload: dict) -> None:
    """``conversation.status_changed`` → arma a posse ao FECHAR, cancela ao reabrir.

    O payload do core já traz ``assignee_user_id``/``ai_active``, então armar não relê a
    conversa. Só a reabertura MANUAL (painel) passa por aqui — a automática (cliente
    escreveu) não emite este evento, e é justamente o que queremos: o prazo continua
    correndo com o atendente segurando a conversa reaberta."""
    try:
        payload = payload or {}
        conv_id = payload.get("conversation_id") or payload.get("id")
        if not conv_id:
            return
        status = payload.get("status")
        if status == "closed":
            if not ai_takeover_enabled():
                return
            arm_ai_hold({"id": conv_id,
                         "assignee_user_id": payload.get("assignee_user_id"),
                         "ai_active": payload.get("ai_active")},
                        reason="resolver")
        elif status == "open":
            cancel_ai_hold(int(conv_id), why="reaberta no painel")
    except Exception as e:  # noqa: BLE001 — handler nunca quebra o pipeline
        logger.debug("protocolos.on_conversation_status falhou: %s", e)


def on_conversation_ai_toggled(ctx, payload: dict) -> None:
    """``conversation.ai_toggled`` → o operador devolveu a IA na mão durante a janela.

    Só reage a religar (``ai_active`` verdadeiro): a IA já está no comando, o hold não
    tem mais o que fazer. Desligar a IA não mexe na janela. Não há realimentação: o
    espelho do modo ``muted`` usa broadcast WS puro, não o bus."""
    try:
        payload = payload or {}
        if not payload.get("ai_active"):
            return
        conv_id = payload.get("conversation_id") or payload.get("id")
        if conv_id:
            # restore_ai=False: a IA já foi religada por quem emitiu o evento.
            cancel_ai_hold(int(conv_id), restore_ai=False, why="IA religada no painel")
    except Exception as e:  # noqa: BLE001
        logger.debug("protocolos.on_conversation_ai_toggled falhou: %s", e)


def on_conversation_assigned(ctx, payload: dict) -> None:
    """``conversation.assigned`` → alguém mexeu na posse durante a janela.

    Passou para um AGENTE de IA (``active_agent_key``) ⇒ o hold perdeu a razão de ser.
    Passou para outro humano ⇒ atualiza o dono e mantém o prazo (a conversa continua
    com gente, só que outra)."""
    try:
        payload = payload or {}
        conv_id = payload.get("conversation_id") or payload.get("id")
        if not conv_id:
            return
        hold = get_ai_hold(int(conv_id))
        if hold is None:
            return
        if payload.get("active_agent_key"):
            cancel_ai_hold(int(conv_id), restore_ai=False, why="IA assumiu a conversa")
            return
        owner = payload.get("assignee_user_id")
        if owner is not None and owner != hold.get("owner_user_id"):
            _write_ai_hold(int(conv_id), hold_until=float(hold["hold_until"]),
                           mode="owner", owner_user_id=int(owner),
                           protocolo_id=hold.get("protocolo_id"),
                           reason=hold.get("reason") or "")
    except Exception as e:  # noqa: BLE001
        logger.debug("protocolos.on_conversation_assigned falhou: %s", e)


# ``message.sent`` de HUMANO (o envio da IA usa source="ai" e não pode se auto-liberar).
_HUMAN_SEND_SOURCES = frozenset({"operator", "template"})


def cancel_ai_hold_on_human_send(payload: dict) -> None:
    """Chamado de ``on_outbound``: o ATENDENTE respondeu dentro da janela ⇒ ele fica
    com a conversa e a devolução automática é cancelada.

    O payload de ``message.sent`` não traz ``conversation_id``, então resolvemos pelo
    telefone (aberta primeiro; o envio do operador já reabriu a conversa nesse ponto).
    Não passa pelo ``_resolve_target`` de propósito: aquele é gated por ``auto_link``,
    que não tem nada a ver com posse.

    LIMITAÇÃO CONHECIDA (herdada do payload): a resolução é CHANNEL-BLIND. Num install
    multicanal com o MESMO número em dois canais, responder num canal pode encerrar a
    janela do outro. Corrigir exigiria o core mandar ``channel_id``/``conversation_id``
    no evento. O erro é a favor do humano (a IA deixa de reassumir), não contra."""
    try:
        payload = payload or {}
        if str(payload.get("source") or "") not in _HUMAN_SEND_SOURCES:
            return
        phone = payload.get("phone")
        if not phone:
            return
        contact = contact_repo.get_by_phone(phone)
        if not contact:
            return
        conv = (conversation_repo.get_open_for_contact(contact["id"])
                or conversation_repo.get_latest_for_contact(contact["id"]))
        if conv:
            cancel_ai_hold(int(conv["id"]), why="atendente respondeu")
    except Exception as e:  # noqa: BLE001
        logger.debug("protocolos.cancel_ai_hold_on_human_send falhou: %s", e)


async def expire_ai_holds_once() -> int:
    """Uma passada da varredura: devolve à IA as conversas cujo prazo venceu.

    Usa :func:`handoff_to_ai` (tira o humano, ``ai_active=1``, SEM carimbar agente — quem
    escolhe é o roteamento no próximo turno) e emite o card "🤖 SISTEMA reativou a IA.".
    Gates global/canal desligados ⇒ a linha é apagada sem religar (a IA está desligada de
    propósito). Devolve quantas foram devolvidas. Nunca levanta."""
    rows = await asyncio.to_thread(list_expired_ai_holds, now())
    if not rows:
        return 0
    from plugins.context import get_deps
    if not get_deps():
        # Runtime não cabeado (boot degradado): sem ``deps`` o gate global nem dá para
        # avaliar, e apagar a linha perderia a intenção. Deixa para a próxima passada.
        logger.debug("protocolos: varredura sem deps — %d hold(s) adiado(s)", len(rows))
        return 0
    released = 0
    for row in rows:
        conv_id = int(row["conversation_id"])
        try:
            conv = await asyncio.to_thread(conversation_repo.get, conv_id)
            if not conv:
                await asyncio.to_thread(clear_ai_hold, conv_id)
                continue
            if not await asyncio.to_thread(_ai_master_gate, conv_id):
                await asyncio.to_thread(clear_ai_hold, conv_id)
                continue
            await handoff_to_ai(conv)
            await asyncio.to_thread(clear_ai_hold, conv_id)
            released += 1
        except Exception as e:  # noqa: BLE001 — uma conversa ruim não para a varredura
            logger.warning("protocolos: falha ao devolver a conversa %s à IA: %s",
                           conv_id, e)
    return released


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
    # Continuidade FUNDIDA no protocolo anterior (``merge_into_previous``): o ciclo deste
    # re-engajamento foi descartado e o protocolo já está finalizado — não há atendimento
    # novo a preencher, então não há o que exigir (e exigir aqui seria inacionável: não
    # existe ciclo aberto onde gravar o rótulo). No fluxo normal de resolver o protocolo do
    # contato ainda está ABERTO neste ponto, então o gate abaixo segue valendo.
    conv = conversation_repo.get(cid) or {}
    if conv.get("contact_id") is not None and _select_open_protocolo(conv["contact_id"]) is None:
        proto = get_protocolo(cycle["protocolo_id"]) \
            if cycle and cycle.get("protocolo_id") else None
        if cycle is None or (proto and proto["status"] == "fechado"):
            return payload
    eff = _effective_values("atendimento", cycle or {})
    # Rótulo Atendente (escopo atendimento) reflete o assignee NATIVO da conversa (não o do ciclo).
    at_def = next((d for d in get_field_defs("atendimento") if d.get("type") == "atendente"), None)
    if at_def:
        eff[at_def["key"]] = conv.get("assignee_user_id")
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


# ── Regra "ignorar abertura" — não acionar IA/protocolo por regex ─────────────
# A MENSAGEM sempre é salva/visível no painel, e a conversa é MANTIDA FECHADA nos dois
# casos — só as AUTOMAÇÕES (protocolo, IA) é que são suprimidas:
#  • ENVIADA (operador): o core aplica ``filter.conversation.before_reopen`` no envio
#    do operador → ``before_reopen`` devolve False p/ NÃO reabrir (a msg vai ao WhatsApp,
#    só não ressuscita o atendimento). ``on_outbound`` pula o protocolo.
#  • RECEBIDA (contato): o core aplica o MESMO ``before_reopen`` no save inbound (t=0 e
#    batch) → a conversa continua fechada, mas a mensagem aparece (é salva/broadcastada).
#    ``on_inbound`` pula o protocolo e ``suppress_ai_on_ignored`` (``filter.llm.messages``
#    → None) impede a resposta da IA — senão a resposta (que não casa a regex) reabriria
#    a conversa e abriria protocolo por conta própria.

def before_reopen(ctx, value):
    """``filter.conversation.before_reopen`` — o core aplica nos SAVES (envio do operador
    e recebimento do contato) com ``value=True`` (reabre). Devolve ``False`` p/ IMPEDIR a
    reabertura quando a regra "ignorar abertura" casar na direção do lado (``role``='user'
    → recebida, senão enviada). ``ctx.extras`` traz ``role`` e ``text``. Sync (config+regex)."""
    ex = getattr(ctx, "extras", None) or {}
    direction = "received" if ex.get("role") == "user" else "sent"
    if _skip_open_matches(ex.get("text") or "", direction):
        return False  # não reabrir
    if _relink_attr_blocks_phone(ex.get("phone")):
        return False  # atributo marcou "não abrir protocolo" → conversa segue fechada
    return value


def _relink_attr_blocks_phone(phone) -> bool:
    """True quando o atributo personalizado deste contato está no valor de BLOQUEIO.

    Dá ao valor de bloqueio a mesma semântica do botão "Fechar conversa e protocolo
    juntos": nem protocolo novo, nem reabertura automática da conversa. Leitura PURA (não
    consome o valor) e barata: só toca o banco quando a regra está ligada com valor de
    bloqueio mapeado. Best-effort — erro nunca impede a reabertura."""
    try:
        cfg = get_relink_attr_config()
        if not cfg["enabled"] or not cfg["values"].get("block") or not phone:
            return False
        contact = contact_repo.get_by_phone(phone)
        if not contact:
            return False
        conv_id = None
        if cfg["scope"] == "conversation":
            atend = (conversation_repo.get_open_for_contact(contact["id"])
                     or conversation_repo.get_latest_for_contact(contact["id"]))
            conv_id = (atend or {}).get("id")
        return read_relink_attr_decision(contact["id"], conv_id) == "block"
    except Exception as e:  # noqa: BLE001
        logger.debug("protocolos: _relink_attr_blocks_phone falhou: %s", e)
        return False


def suppress_ai_on_ignored(ctx, value):
    """``filter.llm.messages`` — ABORTA a resposta da IA (retorna None) quando a última
    mensagem do contato (RECEBIDA) casa a regra "ignorar abertura" (direção received/both).
    A mensagem já foi salva/exibida no painel; só a resposta AUTOMÁTICA da IA é impedida
    (senão a resposta reabriria a conversa e abriria protocolo). Não casa → IA normal."""
    if not isinstance(value, list):
        return value
    last_user = next((m for m in reversed(value)
                      if isinstance(m, dict) and m.get("role") == "user"), None)
    if last_user is not None:
        content = last_user.get("content")
        if _skip_open_matches(content if isinstance(content, str) else "", "received"):
            return None  # não chamar a IA para esta mensagem
    return value


def notify_on_ignored(ctx, value):
    """``filter.message.notify`` — devolve ``False`` (sem badge de não-lida / som / alerta)
    quando a mensagem RECEBIDA do contato casa a regra "ignorar abertura" (received/both).
    A mensagem continua salva e visível no painel; só o AVISO de nova mensagem é suprimido.
    Não casa → devolve o valor (True) e notifica normalmente. ``ctx.extras`` traz ``text``."""
    ex = getattr(ctx, "extras", None) or {}
    if _skip_open_matches(ex.get("text") or "", "received"):
        return False  # mensagem silenciosa (sem notificação)
    return value


# ── Util ──────────────────────────────────────────────────────────────────────

def _broadcast_changed(contact_id: int | None, protocolo_id: int | None) -> None:
    # Invalida o índice do Kanban ANTES de avisar o frontend: o refetch disparado pelo
    # WS já enxerga a geração nova (vale para todas as réplicas — a geração vive no
    # `config`). Defensivo: falhar aqui nunca pode impedir o broadcast.
    try:
        from . import kanban_index
        kanban_index.bump_generation()
    except Exception:
        logger.debug("protocolos: falha ao invalidar o índice do kanban", exc_info=True)
    broadcast("plugin_protocolos_changed",
              {"contact_id": contact_id, "protocolo_id": protocolo_id})
