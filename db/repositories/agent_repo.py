"""Repository for ``ai_agents`` (config-in-DB agent definitions).

One row per agent; in the single-agent MVP there is exactly one (``default``).
The JSON columns (``model_config``/``tool_names``/``routing_targets``/
``hooks_config``) are **native JSONB** (plano 34 F5): the repo hands the DB a
native ``dict``/``list`` and SQLAlchemy serializes it exactly once — no manual
``json.dumps`` — which structurally rules out the double-encoding that broke
agent resolution. Reads still pass through ``coerce_json`` defensively (a no-op
on the native value, and a last-resort peeler for any legacy dirty row).
Every ``save`` bumps ``version`` and writes a snapshot to ``ai_agents_history``
for rollback / change trail.
"""

from __future__ import annotations

import json
import time

from sqlalchemy import select

from db.engine import get_engine
from db.repositories import agent_prompt_repo
from db.repositories._mapping import coerce_json
from db.tables import ai_agents, ai_agents_history, atendimentos, inboxes
from db.upsert import upsert, upsert_ignore

DEFAULT_AGENT_KEY = "default"

# Colunas versionadas que entram no snapshot de ``ai_agents_history`` (mesma
# ordem/conteúdo do dict ``values`` montado em :func:`save`).
_SNAPSHOT_COLS = (
    "agent_key", "display_name", "prompt", "prompt_key", "model_config",
    "tool_names", "enabled", "description", "is_router", "is_default",
    "routing_targets", "hooks_config", "version", "updated_at",
)


def _decode_json(value, fallback):
    return coerce_json(value, fallback)


def _native_json_field(value, default):
    """Normalize a JSON column value to a **native** dict/list for a JSONB column.

    Plano 34 F5: the columns are native JSONB now, so we hand SQLAlchemy the raw
    object and let it serialize **exactly once** — never ``json.dumps`` here (that
    would store a JSON *string* in the JSONB column, re-introducing the double
    encoding). ``coerce_json`` (N-layer) still runs as a guard: a caller that passes
    an already-encoded string gets peeled back to the object before it reaches the
    DB. ``default`` steers the empty case (``{}`` for model_config/hooks_config,
    ``None`` for tool_names/routing_targets → SQL NULL).
    """
    return coerce_json(value, default)


def _row_to_dict(row) -> dict:
    d = dict(row)
    # JSONB (F5): psycopg already returns native dict/list, so coerce_json is a
    # no-op passthrough here — kept as a last-resort peeler for any legacy row
    # still holding a JSON-encoded string (defense in depth).
    d["model_config"] = coerce_json(d.get("model_config"), {})
    d["tool_names"] = coerce_json(d.get("tool_names"), None)
    d["routing_targets"] = coerce_json(d.get("routing_targets"), None)
    d["hooks_config"] = coerce_json(d.get("hooks_config"), {})
    d["enabled"] = bool(d.get("enabled", 1))
    d["is_router"] = bool(d.get("is_router", 0))
    d["is_default"] = bool(d.get("is_default", 0))
    return d


def get(agent_key: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(ai_agents).where(ai_agents.c.agent_key == agent_key)
        ).mappings().first()
    return _row_to_dict(row) if row else None


def is_enabled(agent_key: str) -> bool:
    """O agente existe E está habilitado? UMA coluna, para o caminho quente.

    Espelha ``user_repo.is_active``: o "atendente padrão" de um canal que é um
    agente de IA (plano 152) é validado a CADA resolução de conversa, e
    :func:`get` traria a row inteira — prompt inline incluso, que pode ter vários
    KB. Ausente ⇒ ``False``."""
    with get_engine().connect() as conn:
        row = conn.execute(
            select(ai_agents.c.enabled).where(ai_agents.c.agent_key == agent_key)
        ).first()
    return bool(row[0]) if row else False


def get_default() -> dict | None:
    """The system's FALLBACK agent — the one the runtime cascade lands on.

    Unifica os dois "padrões" (fix agente-padrão, 2026-07): o agente marcado
    ``is_default=1`` (plano 36, "Padrão para novas conversas") é TAMBÉM o fallback
    de runtime quando existe e está habilitado; só sem marcação o sistema cai no
    piso legado — a row de chave literal ``"default"``. Retorna ``None`` quando
    nenhum dos dois existe (caller degrada para o piso de emergência in-code).
    Antes o runtime ignorava a marcação e caía SEMPRE na chave ``"default"`` —
    era por isso que fechar+reabrir um atendimento voltava ao "Agente padrão"
    mesmo com outro agente marcado.
    """
    entry = get_new_conversation_default()
    if entry and entry.get("enabled"):
        return entry
    return get(DEFAULT_AGENT_KEY)


def get_fallback_key() -> str | None:
    """Key do agente que :func:`get_default` resolve AGORA (``None`` se nenhum).

    Usado pelos guards de exclusão/desativação: o fallback atual do sistema não
    pode ser removido nem desativado — marque outro agente como padrão primeiro.
    """
    row = get_default()
    if row and row.get("enabled"):
        return row["agent_key"]
    return None


def display_name_for(agent_key: str | None) -> str | None:
    """Nome exibível de um agente a partir do ``agent_key`` (para o painel montar
    "IA - <NOME>" / "Ferramenta IA - <NOME>"). Fallback para o próprio ``agent_key``
    quando o ``display_name`` está vazio; ``None`` quando a chave é vazia ou o agente
    não existe mais (ex.: agente removido) — o frontend cai no rótulo "IA"."""
    if not agent_key:
        return None
    row = get(agent_key)
    if not row:
        return None
    return (row.get("display_name") or "").strip() or agent_key


def list_all() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(ai_agents).order_by(ai_agents.c.agent_key)
        ).mappings().all()
    return [_row_to_dict(r) for r in rows]


def ensure(
    agent_key: str,
    *,
    display_name: str = "",
    prompt: str = "",
    prompt_key: str = "",
    model_config: dict | None = None,
    tool_names: list[str] | None = None,
    enabled: bool = True,
) -> None:
    """Insert the agent only if it does not exist yet (no version bump).

    Used to seed the default agent at boot without clobbering user edits.
    """
    now = time.time()
    values = {
        "agent_key": agent_key,
        "display_name": display_name,
        "prompt": prompt,
        "prompt_key": prompt_key,
        "model_config": _native_json_field(model_config, {}),
        "tool_names": _native_json_field(tool_names, None),
        "enabled": 1 if enabled else 0,
        "version": 1,
        "updated_at": now,
    }
    with get_engine().begin() as conn:
        conn.execute(upsert_ignore(ai_agents, values, conflict_cols=["agent_key"]))


def save(
    agent_key: str,
    *,
    display_name: str,
    prompt: str = "",
    prompt_key: str = "",
    model_config: dict,
    tool_names: list[str] | None,
    enabled: bool,
    description: str = "",
    is_router: bool = False,
    is_default: bool = False,
    routing_targets: list[str] | None = None,
    hooks_config: dict | None = None,
    change_note: str | None = None,
    version_mode: str = "new",
) -> dict:
    """Upsert an agent, bump version and snapshot to history. Returns the row.

    ``prompt`` is the inline, per-agent system prompt (source of truth).
    ``prompt_key`` is legacy (a reference to a shared ai_prompts template) and is
    no longer read for resolution; it is still accepted/persisted for back-compat.
    ``change_note`` is an optional commit message for the dedicated prompt trail.
    ``version_mode`` controls the dedicated prompt trail only (the whole-agent
    history always snapshots): ``"new"`` (default) appends a new prompt version;
    ``"amend"`` overwrites the latest prompt version in place (git ``--amend``).

    Dedup: if every versioned field is identical to the stored row, this is a no-op
    (no version bump, no history row, no prompt-trail growth) and the existing row
    is returned unchanged. Values are compared as Python objects *before* JSON
    encoding, so key ordering never produces a false positive.
    """
    now = time.time()
    existing = get(agent_key)
    if existing is not None and (
        (display_name or "") == (existing.get("display_name") or "")
        and (prompt or "") == (existing.get("prompt") or "")
        and (prompt_key or "") == (existing.get("prompt_key") or "")
        and (model_config or {}) == (existing.get("model_config") or {})
        and tool_names == existing.get("tool_names")
        and bool(enabled) == bool(existing.get("enabled", True))
        and (description or "") == (existing.get("description") or "")
        and bool(is_router) == bool(existing.get("is_router", False))
        and bool(is_default) == bool(existing.get("is_default", False))
        and routing_targets == existing.get("routing_targets")
        and (hooks_config or {}) == (existing.get("hooks_config") or {})
    ):
        return existing
    version = (existing["version"] + 1) if existing else 1
    values = {
        "agent_key": agent_key,
        "display_name": display_name,
        "prompt": prompt or "",
        "prompt_key": prompt_key or "",
        "model_config": _native_json_field(model_config, {}),
        "tool_names": _native_json_field(tool_names, None),
        "enabled": 1 if enabled else 0,
        "description": description or "",
        "is_router": 1 if is_router else 0,
        "is_default": 1 if is_default else 0,
        "routing_targets": _native_json_field(routing_targets, None),
        "hooks_config": _native_json_field(hooks_config, {}),
        "version": version,
        "updated_at": now,
    }
    with get_engine().begin() as conn:
        # Único roteador (plano 29 Eixo B): salvar este agente como roteador
        # REBAIXA qualquer outro (semântica radio) na MESMA transação — antes do
        # upsert, para nunca violar o índice único parcial (0035).
        if is_router:
            _demote_other_routers(conn, agent_key, now)
        # Único agente padrão de novas conversas (plano 36): mesma semântica radio.
        if is_default:
            _demote_other_defaults(conn, agent_key, now)
        conn.execute(upsert(
            ai_agents, values, conflict_cols=["agent_key"],
            update_cols=["display_name", "prompt", "prompt_key", "model_config",
                         "tool_names", "enabled", "description", "is_router",
                         "is_default", "routing_targets", "hooks_config", "version",
                         "updated_at"],
        ))
        conn.execute(ai_agents_history.insert().values(
            agent_key=agent_key,
            version=version,
            snapshot=json.dumps(values, ensure_ascii=False),
            created_at=now,
        ))
        # Dedicated prompt trail — same transaction (atomic). "new" appends a
        # version (record()'s dedup keeps it from growing on an unchanged prompt);
        # "amend" overwrites the latest version in place (git --amend).
        if version_mode == "amend":
            agent_prompt_repo.amend(agent_key, prompt or "", note=change_note, conn=conn)
        else:
            agent_prompt_repo.record(agent_key, prompt or "", note=change_note, conn=conn)
    return _row_to_dict(values)


def _demote_other_routers(conn, agent_key: str, now: float) -> list[str]:
    """Set ``is_router = 0`` on every OTHER router, inside the caller's txn.

    Radio semantics (plano 29 P2a): promoting an agent to router demotes the
    current one. Each demotion bumps ``version`` and snapshots to history, so
    the change trail / rollback keeps working for the demoted agent too.
    """
    rows = conn.execute(
        select(ai_agents).where(ai_agents.c.is_router == 1,
                                ai_agents.c.agent_key != agent_key)
    ).mappings().all()
    demoted: list[str] = []
    for row in rows:
        d = dict(row)
        new_version = (d.get("version") or 0) + 1
        conn.execute(
            ai_agents.update()
            .where(ai_agents.c.agent_key == d["agent_key"])
            .values(is_router=0, version=new_version, updated_at=now)
        )
        snap = {k: d.get(k) for k in _SNAPSHOT_COLS}
        snap.update({"is_router": 0, "version": new_version, "updated_at": now})
        conn.execute(ai_agents_history.insert().values(
            agent_key=d["agent_key"],
            version=new_version,
            snapshot=json.dumps(snap, ensure_ascii=False),
            created_at=now,
        ))
        demoted.append(d["agent_key"])
    return demoted


def get_router() -> dict | None:
    """The single router agent (plano 29 Eixo B), or ``None``."""
    with get_engine().connect() as conn:
        row = conn.execute(
            select(ai_agents).where(ai_agents.c.is_router == 1)
        ).mappings().first()
    return _row_to_dict(row) if row else None


def _demote_other_defaults(conn, agent_key: str, now: float) -> list[str]:
    """Set ``is_default = 0`` on every OTHER default agent, inside the caller's txn.

    Radio semantics (plano 36): promoting an agent to "default for new conversations"
    demotes the current one. Each demotion bumps ``version`` and snapshots to history,
    so the change trail / rollback keeps working for the demoted agent too.
    """
    rows = conn.execute(
        select(ai_agents).where(ai_agents.c.is_default == 1,
                                ai_agents.c.agent_key != agent_key)
    ).mappings().all()
    demoted: list[str] = []
    for row in rows:
        d = dict(row)
        new_version = (d.get("version") or 0) + 1
        conn.execute(
            ai_agents.update()
            .where(ai_agents.c.agent_key == d["agent_key"])
            .values(is_default=0, version=new_version, updated_at=now)
        )
        snap = {k: d.get(k) for k in _SNAPSHOT_COLS}
        snap.update({"is_default": 0, "version": new_version, "updated_at": now})
        conn.execute(ai_agents_history.insert().values(
            agent_key=d["agent_key"],
            version=new_version,
            snapshot=json.dumps(snap, ensure_ascii=False),
            created_at=now,
        ))
        demoted.append(d["agent_key"])
    return demoted


def get_new_conversation_default() -> dict | None:
    """The agent marked as the default for NEW conversations (plano 36), or ``None``.

    Consulted by the creation stamp (``conversation_repo.default_agent_key_for_inbox``)
    AND by :func:`get_default` (fix agente-padrão, 2026-07): the marked agent is also
    the runtime fallback, so "novas conversas" e "padrão do sistema" são o MESMO
    conceito. Retorna a row crua (sem filtrar ``enabled`` — cada caller decide).
    """
    with get_engine().connect() as conn:
        row = conn.execute(
            select(ai_agents).where(ai_agents.c.is_default == 1)
        ).mappings().first()
    return _row_to_dict(row) if row else None


def list_history(agent_key: str) -> list[dict]:
    """Version trail (newest first), sem o snapshot completo."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(ai_agents_history.c.version, ai_agents_history.c.created_at)
            .where(ai_agents_history.c.agent_key == agent_key)
            .order_by(ai_agents_history.c.version.desc())
        ).mappings().all()
    return [dict(r) for r in rows]


def get_snapshot(agent_key: str, version: int) -> dict | None:
    with get_engine().connect() as conn:
        snap = conn.execute(
            select(ai_agents_history.c.snapshot).where(
                ai_agents_history.c.agent_key == agent_key,
                ai_agents_history.c.version == version)
        ).scalar()
    return _decode_json(snap, None)


def rollback(agent_key: str, version: int, preserve_prompt: bool = False,
             preserve_config: bool = False) -> dict | None:
    """Re-apply a past snapshot as a NEW version (preserva o trail).

    O snapshot do agente empacota **prompt + config** juntos, então reverter o
    agente inteiro toca os dois. As flags RBAC deixam o rollback restaurar só o que
    o chamador tem permissão de mudar, mantendo o valor ATUAL do resto:

    - ``preserve_prompt`` (sem ``agent.prompts.version``): mantém o prompt atual e
      reverte apenas os campos de config.
    - ``preserve_config`` (sem ``agent.config.manage``): mantém a config atual
      (modelo/tools/roteamento/nome/etc) e reverte apenas o prompt.

    Sem flags, restaura o snapshot inteiro (comportamento original).
    """
    snap = get_snapshot(agent_key, version)
    if not snap:
        return None
    current = get(agent_key) if (preserve_prompt or preserve_config) else None

    prompt = snap.get("prompt", "")
    if preserve_prompt:
        prompt = (current or {}).get("prompt", "") or ""

    if preserve_config and current:
        # Campos de config vêm do agente ATUAL (get() já devolve JSON decodificado).
        return save(
            agent_key,
            display_name=current.get("display_name", "") or "",
            prompt=prompt,
            prompt_key=current.get("prompt_key", "") or "",
            model_config=current.get("model_config") or {},
            tool_names=current.get("tool_names"),
            enabled=bool(current.get("enabled", True)),
            description=current.get("description", "") or "",
            is_router=bool(current.get("is_router", False)),
            routing_targets=current.get("routing_targets"),
            hooks_config=current.get("hooks_config") or {},
        )
    return save(
        agent_key,
        display_name=snap.get("display_name", ""),
        prompt=prompt,
        prompt_key=snap.get("prompt_key", ""),
        model_config=_decode_json(snap.get("model_config"), {}),
        tool_names=_decode_json(snap.get("tool_names"), None),
        enabled=bool(snap.get("enabled", 1)),
        description=snap.get("description", ""),
        is_router=bool(snap.get("is_router", 0)),
        is_default=bool(snap.get("is_default", 0)),
        routing_targets=_decode_json(snap.get("routing_targets"), None),
        hooks_config=_decode_json(snap.get("hooks_config"), {}),
    )


def delete(agent_key: str) -> bool:
    """Remove an agent and its version history. Returns ``True`` if a row went.

    O agente que é o FALLBACK atual do sistema (:func:`get_fallback_key` — o
    marcado ``is_default``, ou a chave literal ``"default"`` sem marcação) nunca
    pode ser removido: a cascata de runtime cai nele. Qualquer OUTRO agente —
    inclusive o ``"default"`` legado, desde que outro carregue a marcação — é
    removível (fix agente-padrão, 2026-07). Refuse defensively even though the
    route guards too. Na MESMA transação, limpa as referências: atendimentos
    vinculados e inboxes com ``default_agent_key`` apontando pro excluído voltam
    a NULL (o runtime então resolve o fallback do momento).
    ``executions.agent_key`` is historical and intentionally left untouched
    (no FK), so usage history survives.
    """
    if agent_key == get_fallback_key():
        return False
    with get_engine().begin() as conn:
        conn.execute(
            ai_agents_history.delete().where(ai_agents_history.c.agent_key == agent_key)
        )
        result = conn.execute(
            ai_agents.delete().where(ai_agents.c.agent_key == agent_key)
        )
        if (result.rowcount or 0) > 0:
            conn.execute(
                atendimentos.update()
                .where(atendimentos.c.active_agent_key == agent_key)
                .values(active_agent_key=None)
            )
            conn.execute(
                inboxes.update()
                .where(inboxes.c.default_agent_key == agent_key)
                .values(default_agent_key=None)
            )
    return (result.rowcount or 0) > 0
