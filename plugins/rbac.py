"""Plugin RBAC registration (plano "RBAC para Plugins" §3.3).

A plugin declares user-facing permissions in the ``rbac:`` block of its
``plugin.yaml`` (see ``plugins.manifest._parse_rbac``). At load time, for an
ENABLED plugin, each declared key is materialised into the ``permissions`` table
as ``plugin.<id>.<key>`` so it becomes assignable to roles/users and shows up in
the PermissionPicker. Disabling a plugin keeps the rows (assignments survive the
toggle); deleting a plugin removes them (``server/routes/plugins.py``).
"""

from __future__ import annotations

import logging

from db.repositories import rbac_repo
from plugins.manifest import PluginManifest

logger = logging.getLogger(__name__)


def sync_plugin_permissions(manifest: PluginManifest) -> int:
    """Upsert the permission rows declared by an enabled plugin's ``rbac:`` block.

    Returns the number of permissions registered. Best-effort: a failure to
    register perms must never block the plugin from loading, so the caller wraps
    this in the same try/except as the rest of the load. The ``group_label``
    defaults to the plugin's display name when ``rbac.group`` is omitted.
    """
    rbac = manifest.rbac or {}
    perms = rbac.get("permissions") or []
    if not perms:
        return 0
    group_label = rbac.get("group") or manifest.name or manifest.id
    count = 0
    for perm in perms:
        key = perm.get("key")
        if not key:
            continue
        full_key = f"plugin.{manifest.id}.{key}"
        rbac_repo.upsert_plugin_permission(
            key=full_key,
            description=perm.get("label") or full_key,
            plugin_id=manifest.id,
            group_label=group_label,
        )
        count += 1
    if count:
        logger.info("Plugin %s: registered %d RBAC permission(s)", manifest.id, count)
    return count
