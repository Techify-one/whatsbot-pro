"""Índice de agrupamento do Kanban de protocolos + cache (Opção 2).

Em vez de mandar a coleção inteira ao cliente para ele agrupar, o servidor constrói UMA
vez um índice **leve** ``{coluna: [id, ...]}`` (só ids, na ordem global do backend) e o
guarda em cache; cada página de cada coluna é servida hidratando **somente** os ids
daquela fatia (``logic.hydrate_by_ids``).

Custo: uma varredura bounded por (visualização + filtros + geração). Ganho: memória
minúscula (ids), contagem por coluna exata e paginação real por coluna — nenhuma tela
carrega a coleção inteira.

Cache: em memória por processo (TTL curto) + um contador de **geração** persistido em
``config``. Qualquer escrita de protocolo/visualização bumpa a geração (via
``logic._broadcast_changed``), e como a geração entra na CHAVE do cache, a invalidação
vale para todas as réplicas — sem migration e sem estado compartilhado.

O agrupamento em si é genérico e mora em ``grouping.py``; aqui só orquestramos
varredura → enriquecimento mínimo → filtro → classificação → buckets.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time

from . import grouping

logger = logging.getLogger(__name__)

# Teto da varredura que alimenta o índice. Bem acima do teto antigo do Kanban (2000):
# o índice guarda só ids, então cabe folgado. Acima disso o resultado é marcado como
# ``truncated`` (contagens viram piso) e a UI avisa.
SCAN_CAP_KEY = "plugin.protocolos.kanban_scan_cap"
DEFAULT_SCAN_CAP = 20000

GENERATION_KEY = "plugin.protocolos.kanban_generation"

_CACHE_TTL = 30.0        # backstop de memória; a invalidação real é pela geração
_CACHE_MAX = 32          # entradas simultâneas (views × filtros ativos)
_GEN_TTL = 5.0           # micro-cache da leitura da geração (evita 1 SELECT por request)

_cache: dict[str, tuple[float, dict]] = {}
_gen_cache: tuple[float, int] | None = None


# ── Config / geração ──────────────────────────────────────────────────────────

def _config_get(key, default):
    from db.repositories import config_repo
    try:
        v = config_repo.get(key)
        return default if v is None else v
    except Exception:
        return default


def scan_cap() -> int:
    try:
        n = int(_config_get(SCAN_CAP_KEY, DEFAULT_SCAN_CAP))
        return n if n > 0 else DEFAULT_SCAN_CAP
    except (TypeError, ValueError):
        return DEFAULT_SCAN_CAP


def _tz_name():
    return _config_get(grouping.TZ_CONFIG_KEY, grouping.DEFAULT_TZ)


def generation() -> int:
    """Geração atual (micro-cacheada). Entra na chave do cache do índice."""
    global _gen_cache
    now = time.time()
    if _gen_cache and (now - _gen_cache[0]) < _GEN_TTL:
        return _gen_cache[1]
    try:
        gen = int(_config_get(GENERATION_KEY, 0) or 0)
    except (TypeError, ValueError):
        gen = 0
    _gen_cache = (now, gen)
    return gen


def bump_generation() -> None:
    """Invalida o índice em TODAS as réplicas. Chamado a cada escrita de protocolo ou
    de visualização (ver ``logic._broadcast_changed``). Nunca lança."""
    global _gen_cache
    try:
        from db.repositories import config_repo
        cur = 0
        try:
            cur = int(config_repo.get(GENERATION_KEY) or 0)
        except (TypeError, ValueError):
            cur = 0
        config_repo.set(GENERATION_KEY, cur + 1)
        _gen_cache = None
        _cache.clear()          # este processo já pode descartar o que tinha
    except Exception:
        logger.debug("protocolos: falha ao bumpar a geração do kanban", exc_info=True)


# ── Chave de cache ────────────────────────────────────────────────────────────

# Campos da view que MUDAM o agrupamento (o resto — nome, ACL, favoritos — não entra).
_VIEW_KEYS = ("group_by", "group_field_scope", "group_attr_key",
              "group_date_mode", "group_date_grain", "group_date_from", "group_date_to")


def _cache_key(view, filters, gen, tz_name) -> str:
    payload = {
        "v": {k: (view or {}).get(k) for k in _VIEW_KEYS},
        "f": _normalized_filters(filters),
        "g": gen, "tz": tz_name,
    }
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _normalized_filters(filters) -> dict:
    f = dict(filters or {})
    af = f.get("attr_filters") or {}
    return {
        "status": f.get("status"), "assignee_user_id": f.get("assignee_user_id"),
        "contact_id": f.get("contact_id"), "q": f.get("q"),
        "opened_from": f.get("opened_from"), "opened_to": f.get("opened_to"),
        # `nota` e `include_archived` entram na CHAVE porque mudam o recorte varrido por
        # `scan_protocolos`; fora daqui, trocar o filtro de avaliação serviria o índice
        # antigo do cache.
        "nota": f.get("nota"),
        "include_archived": bool(f.get("include_archived")),
        "attr_filters": {str(k): af[k] for k in sorted(af)} if af else {},
    }


# ── Enriquecimento mínimo ─────────────────────────────────────────────────────

def _filter_needs(af) -> set:
    """Quais anexações os filtros ativos exigem (mesma taxonomia de ``Grouping.needs``)."""
    needs = set()
    for k in (af or {}):
        k = str(k)
        if k == "canal":
            needs.add("channel")
        elif k.startswith("cattr:"):
            needs.add("contact_attrs")
        elif k.startswith("pf:atendimento:"):
            needs.add("atendimento_fields")
        elif k.startswith("pf:protocolo:"):
            needs.add("protocolo_extras")
    return needs


def _enrich(rows, needs: set) -> list[dict]:
    """Anexa SÓ o que o agrupamento/filtros pedem (tudo batch, sem N+1).

    Sem nenhuma necessidade (status/atendente/data sem filtro de atributo) isto é
    praticamente de graça: nenhuma query extra — é o que faz esses modos escalarem.

    As anexações são resolvidas por ``getattr``: a cópia semente em
    ``assets/plugin_examples`` é mais antiga e não tem todas (ex.: ``_attach_channels``).
    O que não existe é simplesmente pulado, em vez de quebrar o Kanban inteiro.
    """
    from . import logic
    ids = [r["id"] for r in rows]
    extras = logic._visible_extras("protocolo", ids) if "protocolo_extras" in needs else {}
    # _proto_dict normaliza ``fields`` para dict (a coluna crua é JSON TEXT); com extras
    # vazios vira {} — evita o JSON cru vazar como string na classificação.
    out = [logic._proto_dict(r, extras.get(r["id"], {})) for r in rows]
    for need, fname in (("atendimento_fields", "_attach_latest_atendimento"),
                        ("contact_attrs", "_attach_contact_attrs"),
                        ("channel", "_attach_channels")):
        if need not in needs:
            continue
        fn = getattr(logic, fname, None)
        if fn is None:
            logger.debug("protocolos: %s indisponível nesta cópia do plugin", fname)
            continue
        fn(out)
    return out


def _active_attr_filters(filters) -> dict:
    """Só as chaves que o filtro Python entende (mesma regra de ``list_protocolos``)."""
    af = (filters or {}).get("attr_filters") or {}
    return {str(k): v for k, v in af.items()
            if k and (str(k).startswith("pf:") or str(k).startswith("cattr:")
                      or str(k) == "canal")}


def _active_users() -> list[dict]:
    """Usuários ativos — colunas do modo 'atendente' (mesma fonte de assignable-agents)."""
    try:
        from db.repositories import user_repo
        return [{"id": u["id"], "name": u.get("name") or u.get("email")}
                for u in user_repo.list_all() if u.get("is_active")]
    except Exception:
        logger.debug("protocolos: falha ao listar usuários p/ colunas", exc_info=True)
        return []


# ── Construção do índice ──────────────────────────────────────────────────────

def build_index(view, filters, *, users=None, now_epoch=None, tz=None) -> dict:
    """Varre (bounded) → enriquece o mínimo → filtra → classifica → buckets de ids.

    Devolve ``{columns:[{id,label,total}], column_ids:{col:[id,...]}, truncated,
    unavailable, read_only}``. ``column_ids`` preserva a ordem global do backend
    (``(status='aberto') DESC, opened_at DESC``), que é a ordem dos cards na coluna.
    """
    from . import logic
    tz = tz if tz is not None else grouping.resolve_tz(_tz_name())
    g = grouping.build_grouping(
        view,
        users=_active_users() if users is None else users,
        option_def=getattr(logic, "_option_field_def", None),
        now_epoch=now_epoch, tz=tz)

    af = _active_attr_filters(filters)
    needs = set(g.needs) | _filter_needs(af)
    cap = scan_cap()

    rows = logic.scan_protocolos(filters, cap)
    truncated = len(rows) >= cap
    items = _enrich(rows, needs)

    matches = getattr(logic, "_row_matches_filter", None)
    if af and matches is not None:
        opt_keys = (logic._pf_option_keys()
                    if any(str(k).startswith("pf:") for k in af) else None)
        items = [a for a in items
                 if all(matches(a, fk, v, opt_keys) for fk, v in af.items())]
    elif af:
        # Cópia antiga sem o avaliador de filtros por atributo: agrupa sem filtrar (e avisa)
        # em vez de devolver um Kanban silenciosamente errado.
        logger.warning("protocolos: _row_matches_filter ausente — attr_filters ignorados "
                       "no índice do Kanban (cópia desatualizada do plugin).")

    column_ids: dict[str, list[int]] = {}
    for a in items:
        column_ids.setdefault(g.column_id_of(a), []).append(a["id"])

    cols = g.static_columns()
    if cols is None:                               # modos de data dinâmicos
        cols = g.build_dynamic_columns(column_ids.keys())
    else:
        # Coluna classificada fora do conjunto estático (ex.: usuário inativo, opção
        # removida) entra ao FIM — melhor do que sumir com os cards silenciosamente.
        known = {c["id"] for c in cols}
        for cid in column_ids:
            if cid not in known:
                cols.append({"id": cid, "label": g.label_for(cid)})

    columns = [{"id": c["id"], "label": c["label"],
                "total": len(column_ids.get(c["id"], []))} for c in cols]
    return {"columns": columns, "column_ids": column_ids, "truncated": truncated,
            "unavailable": g.unavailable, "read_only": g.read_only}


def get_index(view, filters, *, now_epoch=None) -> dict:
    """``build_index`` com cache (chave = agrupamento + filtros + geração + tz)."""
    tz_name = _tz_name()
    key = _cache_key(view, filters, generation(), tz_name)
    now = time.time()
    hit = _cache.get(key)
    if hit and (now - hit[0]) < _CACHE_TTL:
        return hit[1]
    idx = build_index(view, filters, now_epoch=now_epoch,
                      tz=grouping.resolve_tz(tz_name))
    _cache[key] = (now, idx)
    _prune(now)
    return idx


def _prune(now: float) -> None:
    for k, (ts, _) in list(_cache.items()):
        if (now - ts) >= _CACHE_TTL:
            _cache.pop(k, None)
    while len(_cache) > _CACHE_MAX:            # teto duro de memória (mais antigo sai)
        oldest = min(_cache.items(), key=lambda kv: kv[1][0])[0]
        _cache.pop(oldest, None)


def invalidate() -> None:
    """Descarta o cache deste processo (usado em teste)."""
    global _gen_cache
    _cache.clear()
    _gen_cache = None
