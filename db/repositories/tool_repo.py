"""Repository for ``ai_tools`` (code-in-DB tools).

Each row holds Python source plus install metadata. The installer materialises
``code`` to disk, resolves ``dependencies`` and registers the tool when the
contract validates. ``dependencies`` and ``installed_deps`` are JSON arrays.
Every ``save`` (from the CRUD path) bumps ``version`` and snapshots to history;
the installer uses the lighter status setters which do NOT bump version.
"""

from __future__ import annotations

import json
import time

from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy import delete as sa_delete

from db.engine import get_engine
from db.repositories._mapping import coerce_json
from db.tables import ai_tools, ai_tools_history
from db.upsert import upsert


def _decode_list(value):
    out = coerce_json(value, [])
    return out if isinstance(out, list) else []


def _row_to_dict(row) -> dict:
    d = dict(row)
    d["dependencies"] = _decode_list(d.get("dependencies"))
    d["installed_deps"] = _decode_list(d.get("installed_deps"))
    d["enabled"] = bool(d.get("enabled", 1))
    d["kind"] = d.get("kind") or "code"
    d["plugin_id"] = d.get("plugin_id") or None
    return d


def get(name: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(ai_tools).where(ai_tools.c.name == name)
        ).mappings().first()
    return _row_to_dict(row) if row else None


def list_all() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(ai_tools).order_by(ai_tools.c.name)).mappings().all()
    return [_row_to_dict(r) for r in rows]


def list_enabled() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(ai_tools).where(ai_tools.c.enabled == 1).order_by(ai_tools.c.name)
        ).mappings().all()
    return [_row_to_dict(r) for r in rows]


def save(
    name: str,
    *,
    description: str,
    code: str,
    dependencies: list[str] | None,
    enabled: bool,
    kind: str | None = None,
    plugin_id: str | None = None,
) -> dict:
    """Upsert a tool (CRUD path). Resets install_status to ``pending`` so the
    next boot re-validates, bumps version and snapshots to history.

    ``kind`` defaults to the existing row's kind (or ``'code'`` for a new tool),
    so editing a builtin tool keeps it a builtin. ``plugin_id`` segue a MESMA
    regra, e por um motivo bem concreto: ``rollback()`` chama ``save()`` sem
    nenhum dos dois, e o UPSERT escreve um ``values`` completo — sem esta linha,
    reverter (ou salvar) uma tool de plugin apagaria o dono dela em silêncio, e
    a row viraria uma tool code-in-DB órfã.
    """
    now = time.time()
    existing = get(name)
    row_kind = kind or (existing or {}).get("kind") or "code"
    row_plugin_id = plugin_id or (existing or {}).get("plugin_id") or None
    deps = dependencies or []
    if existing is not None and (
        (description or "") == (existing.get("description") or "")
        and (code or "") == (existing.get("code") or "")
        and deps == (existing.get("dependencies") or [])
        and row_kind == (existing.get("kind") or "code")
        and row_plugin_id == (existing.get("plugin_id") or None)
    ):
        # Toggle de `enabled` (ou save idêntico) NÃO é edição: sem bump de
        # versão, sem history (plano 30 · review F3). Sem isso, ligar um
        # builtin nasce-OFF pela UI unificada virava version=2 = "editado" e o
        # register_builtin_overrides passava a executar o código do BANCO
        # in-process (e a cópia congelada deixava de seguir updates do disco).
        # install_status re-valida no próximo boot, como no save normal.
        with get_engine().begin() as conn:
            conn.execute(sa_update(ai_tools).where(ai_tools.c.name == name).values(
                enabled=1 if enabled else 0,
                install_status="pending",
                install_error=None,
                updated_at=now,
            ))
        return get(name)
    version = (existing["version"] + 1) if existing else 1
    values = {
        "name": name,
        "kind": row_kind,
        "plugin_id": row_plugin_id,
        "description": description,
        "code": code,
        "dependencies": json.dumps(deps, ensure_ascii=False),
        "enabled": 1 if enabled else 0,
        "install_status": "pending",
        "install_error": None,
        # Preserve the install cache marker so unchanged deps skip pip.
        "installed_deps": json.dumps(
            (existing or {}).get("installed_deps", []), ensure_ascii=False
        ),
        "version": version,
        "updated_at": now,
    }
    with get_engine().begin() as conn:
        conn.execute(upsert(
            ai_tools, values, conflict_cols=["name"],
            update_cols=["kind", "plugin_id", "description", "code", "dependencies",
                         "enabled", "install_status", "install_error", "version",
                         "updated_at"],
        ))
        conn.execute(ai_tools_history.insert().values(
            name=name,
            version=version,
            snapshot=json.dumps(values, ensure_ascii=False),
            created_at=now,
        ))
    return _row_to_dict(values)


def sync_source(
    name: str,
    *,
    description: str,
    code: str,
    kind: str,
    plugin_id: str | None = None,
) -> bool:
    """Refresca uma row NÃO EDITADA a partir da fonte confiável do disco.

    Existe fora de :func:`save` de propósito: ``save`` bumpa versão e grava
    history, e nenhuma das duas coisas deve acontecer aqui. Espelhar o disco não
    é uma edição — uma linha sintética em ``ai_tools_history`` ofereceria um
    "reverter" para uma versão idêntica à atual e quebraria a relação
    versão ↔ histórico de que o modal de Histórico depende.

    Só escreve quando a row existe, está em ``version <= 1`` (ou seja, ninguém a
    editou) e o código de fato mudou. NUNCA toca ``enabled`` (desligar uma tool é
    decisão do operador e tem de sobreviver ao upgrade do plugin), ``version``,
    nem ``installed_deps``. Devolve ``True`` quando escreveu.
    """
    existing = get(name)
    if existing is None or int(existing.get("version", 1)) > 1:
        return False
    if ((existing.get("code") or "") == (code or "")
            and (existing.get("description") or "") == (description or "")
            and (existing.get("plugin_id") or None) == (plugin_id or None)):
        return False
    with get_engine().begin() as conn:
        conn.execute(sa_update(ai_tools).where(ai_tools.c.name == name).values(
            kind=kind,
            plugin_id=plugin_id,
            description=description,
            code=code,
            install_status="ok",
            install_error=None,
            updated_at=time.time(),
        ))
    return True


def list_for_plugin(plugin_id: str) -> list[dict]:
    """As rows de um plugin (kind='plugin'), pela coluna ``plugin_id``."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(ai_tools).where(ai_tools.c.plugin_id == plugin_id)
            .order_by(ai_tools.c.name)
        ).mappings().all()
    return [_row_to_dict(r) for r in rows]


def delete_for_plugin(plugin_id: str) -> list[str]:
    """Apaga as rows de um plugin (e o history delas). Devolve os nomes.

    Os nomes voltam porque quem chama — a desinstalação do plugin — precisa
    deles para limpar o tombstone daquele plugin, e depois de apagar as rows não
    há mais como descobrir quais eram.
    """
    names = [r["name"] for r in list_for_plugin(plugin_id)]
    if not names:
        return []
    with get_engine().begin() as conn:
        conn.execute(sa_delete(ai_tools_history).where(
            ai_tools_history.c.name.in_(names)))
        conn.execute(sa_delete(ai_tools).where(ai_tools.c.plugin_id == plugin_id))
    return names


def list_history(name: str) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(ai_tools_history.c.version, ai_tools_history.c.created_at)
            .where(ai_tools_history.c.name == name)
            .order_by(ai_tools_history.c.version.desc())
        ).mappings().all()
    return [dict(r) for r in rows]


def get_snapshot(name: str, version: int) -> dict | None:
    with get_engine().connect() as conn:
        snap = conn.execute(
            select(ai_tools_history.c.snapshot).where(
                ai_tools_history.c.name == name,
                ai_tools_history.c.version == version)
        ).scalar()
    if not snap:
        return None
    try:
        return json.loads(snap)
    except (json.JSONDecodeError, TypeError):
        return None


def rollback(name: str, version: int) -> dict | None:
    """Re-apply a past snapshot as a NEW version (install_status volta a pending)."""
    snap = get_snapshot(name, version)
    if not snap:
        return None
    return save(
        name,
        description=snap.get("description", ""),
        code=snap.get("code", ""),
        dependencies=_decode_list(snap.get("dependencies")),
        enabled=bool(snap.get("enabled", 1)),
    )


def set_status(name: str, status: str, error: str | None = None) -> None:
    """Update install_status/install_error without bumping version."""
    with get_engine().begin() as conn:
        conn.execute(
            sa_update(ai_tools)
            .where(ai_tools.c.name == name)
            .values(install_status=status, install_error=error, updated_at=time.time())
        )


def set_installed_deps(name: str, deps: list[str]) -> None:
    """Mark the dependency specs that were successfully installed (cache marker)."""
    with get_engine().begin() as conn:
        conn.execute(
            sa_update(ai_tools)
            .where(ai_tools.c.name == name)
            .values(installed_deps=json.dumps(deps, ensure_ascii=False),
                    updated_at=time.time())
        )


def set_enabled(name: str, enabled: bool) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            sa_update(ai_tools)
            .where(ai_tools.c.name == name)
            .values(enabled=1 if enabled else 0, updated_at=time.time())
        )


def delete(name: str) -> int:
    with get_engine().begin() as conn:
        result = conn.execute(sa_delete(ai_tools).where(ai_tools.c.name == name))
    return result.rowcount or 0
