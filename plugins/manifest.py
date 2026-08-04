"""Plugin manifest parsing and validation.

Each plugin ships a ``plugin.yaml`` (or ``plugin.json``) at the root of its
folder. To avoid an extra dependency, we hand-write a tiny YAML reader for the
restricted subset we actually use (top-level scalars, lists, nested mappings up
to two levels). If ``pyyaml`` is installed it is preferred.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from pathlib import Path
from typing import Any

# Semver parsing/matching lives in plugins.semver (plano 23 Fase C4 — single
# source). Re-exported here so existing import paths (``manifest.check_api_compat``,
# ``manifest._is_semver``, ``manifest._parse_simple_semver``) keep working.
from plugins.semver import (
    WHATSBOT_API_VERSION,
    check_api_compat,
    is_semver as _is_semver,
    parse_simple_semver as _parse_simple_semver,
)

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
# RBAC permission keys (plano "RBAC para Plugins" §3.2): allow ``view``,
# ``orders.export``, etc. The plugin id is prefixed at registration time.
_RBAC_KEY_RE = re.compile(r"^[a-z][a-z0-9_.]{0,48}$")


@dataclasses.dataclass
class PluginManifest:
    """In-memory representation of a parsed plugin manifest."""

    id: str
    name: str
    version: str
    description: str = ""
    # Short blurb (≤ ~3 lines) shown on the plugin card. Falls back to a
    # truncated ``description`` when the manifest omits it (see ``short_blurb``).
    short_description: str = ""
    author: str = ""
    whatsbot_api_version: str = "*"
    # Module names the loader imports per capability. Recognized keys:
    # tools, prompts, events, filters, routes, settings, lifecycle.
    # ``lifecycle`` (plano 09) → module exporting setup(ctx)/teardown(ctx).
    entry: dict[str, str] = dataclasses.field(default_factory=dict)
    migrations: str | None = None
    screens: list[dict] = dataclasses.field(default_factory=list)
    permissions: list[str] = dataclasses.field(default_factory=list)
    dependencies: list[str] = dataclasses.field(default_factory=list)
    # Documentation-only declarations. The loader does not enforce that the
    # plugin actually exports a handler for every event/filter listed here;
    # they exist so ``/api/plugins/manifest`` can show which surface the plugin
    # touches.
    events: list[str] = dataclasses.field(default_factory=list)
    filters: list[str] = dataclasses.field(default_factory=list)
    # User-facing RBAC permissions (plano "RBAC para Plugins"). Distinct from the
    # capability ``permissions`` field above. Shape after parsing:
    # ``{"group": str | None, "permissions": [{"key": str, "label": str}, ...]}``.
    rbac: dict = dataclasses.field(default_factory=dict)
    # Frontend extension layer (camada de extensão de frontend). ``frontend_extends``
    # is the URL of an ES module the app imports ONCE at boot (separate from screens)
    # to register filters / UI slots / route-overrides via the client-side registry.
    # ``frontend_api_version`` is the semver range that module targets; the client
    # checks it against FRONTEND_API_VERSION at load time (informational on the server).
    frontend_extends: str = ""
    frontend_api_version: str = "*"
    # Version range for ``api.services``. Legacy manifests predate this field and
    # therefore target the compatibility surface 1.x by default.
    plugin_services_version: str = "1.0"
    raw: dict = dataclasses.field(default_factory=dict)

    def to_public_dict(self) -> dict:
        """Serializable view exposed by ``/api/plugins`` endpoints."""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "short_description": self.short_blurb(),
            "author": self.author,
            "whatsbot_api_version": self.whatsbot_api_version,
            "screens": self.screens,
            "permissions": self.permissions,
            "dependencies": self.dependencies,
            "events": self.events,
            "filters": self.filters,
            "rbac": self.rbac,
            "frontend_extends": self.frontend_extends,
            "frontend_api_version": self.frontend_api_version,
            "plugin_services_version": self.plugin_services_version,
        }

    def short_blurb(self, max_len: int = 180) -> str:
        """Short card blurb: the explicit ``short_description`` when present,
        otherwise the first sentence of ``description`` truncated to ``max_len``.

        The client still clamps this to 3 lines visually; this keeps the payload
        small and gives legacy plugins (no ``short_description``) a sane preview.
        """
        text = (self.short_description or "").strip()
        if text:
            return text
        full = " ".join((self.description or "").split())
        if not full:
            return ""
        # Prefer cutting at the first sentence boundary when it's not too short.
        dot = full.find(". ")
        if 0 < dot + 1 <= max_len:
            return full[: dot + 1]
        if len(full) <= max_len:
            return full
        clipped = full[:max_len].rsplit(" ", 1)[0].rstrip(",;:. ")
        return f"{clipped}…"


def find_manifest_file(plugin_dir: Path) -> Path | None:
    for name in ("plugin.yaml", "plugin.yml", "plugin.json"):
        path = plugin_dir / name
        if path.is_file():
            return path
    return None


def load_manifest(plugin_dir: Path) -> PluginManifest:
    """Parse the manifest file inside ``plugin_dir``.

    Raises ``ValueError`` on parse error or missing required fields.
    """
    path = find_manifest_file(plugin_dir)
    if path is None:
        raise ValueError(f"manifest not found in {plugin_dir} (expected plugin.yaml or plugin.json)")
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        data = json.loads(text)
    else:
        data = _parse_yaml(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level must be a mapping")
    return _build_manifest(data, plugin_dir)


def _build_manifest(data: dict, plugin_dir: Path) -> PluginManifest:
    pid = data.get("id")
    if not isinstance(pid, str) or not _ID_RE.match(pid):
        raise ValueError(
            "manifest 'id' must be snake_case starting with a letter, "
            "32 chars max (got %r)" % pid
        )
    if pid != plugin_dir.name:
        raise ValueError(
            f"manifest id '{pid}' must match plugin folder name '{plugin_dir.name}'"
        )

    name = data.get("name") or pid
    version = str(data.get("version") or "0.0.0")
    if not _is_semver(version):
        raise ValueError(f"manifest 'version' must be semver (got {version!r})")

    api_range = str(data.get("whatsbot_api_version") or "*")
    if not check_api_compat(api_range):
        raise ValueError(
            f"plugin {pid} requires WhatsBot API {api_range}, "
            f"running {WHATSBOT_API_VERSION}"
        )

    entry = data.get("entry") or {}
    if not isinstance(entry, dict):
        raise ValueError("manifest 'entry' must be a mapping")
    entry_str = {k: str(v) for k, v in entry.items() if isinstance(v, str)}

    migrations = data.get("migrations")
    migrations_str = str(migrations) if migrations else None

    screens = data.get("screens") or []
    if not isinstance(screens, list):
        raise ValueError("manifest 'screens' must be a list")
    cleaned_screens = []
    for s in screens:
        if not isinstance(s, dict):
            continue
        if not s.get("path") or not s.get("component"):
            logger.warning("plugin %s: screen missing path/component, skipped: %s", pid, s)
            continue
        cleaned_screens.append({
            "id": str(s.get("id") or s["path"].lstrip("/")),
            "title": str(s.get("title") or s["path"]),
            "path": str(s["path"]),
            "icon": str(s.get("icon") or ""),
            "component": str(s["component"]),
            # When true, the screen is the plugin's configuration UI: shown in the
            # Plugins tab "Configurar" modal instead of as a standalone gear-menu page.
            "config": bool(s.get("config", False)),
            # Optional RBAC gate (plano "RBAC para Plugins"): when set, the screen
            # is hidden in the gear menu unless the user holds plugin.<id>.<requires>.
            "requires": str(s["requires"]).strip() if s.get("requires") else None,
        })

    permissions = [str(p) for p in (data.get("permissions") or []) if isinstance(p, str)]
    deps = [str(d) for d in (data.get("dependencies") or []) if isinstance(d, str)]
    events_declared = [str(e) for e in (data.get("events") or []) if isinstance(e, str)]
    filters_declared = [str(f) for f in (data.get("filters") or []) if isinstance(f, str)]
    rbac = _parse_rbac(data.get("rbac"), pid)

    # Frontend extension module (optional). Normalized to a string; empty = none.
    frontend_extends = str(data.get("frontend_extends") or "")
    frontend_api_version = str(data.get("frontend_api_version") or "*")
    plugin_services_version = str(data.get("plugin_services_version") or "1.0")

    return PluginManifest(
        id=pid,
        name=str(name),
        version=version,
        description=str(data.get("description") or ""),
        short_description=str(data.get("short_description") or ""),
        author=str(data.get("author") or ""),
        whatsbot_api_version=api_range,
        entry=entry_str,
        migrations=migrations_str,
        screens=cleaned_screens,
        permissions=permissions,
        dependencies=deps,
        events=events_declared,
        filters=filters_declared,
        rbac=rbac,
        frontend_extends=frontend_extends,
        frontend_api_version=frontend_api_version,
        plugin_services_version=plugin_services_version,
        raw=data,
    )


def _parse_rbac(raw: Any, pid: str) -> dict:
    """Parse + validate the optional ``rbac:`` manifest block.

    Returns ``{}`` when absent. Otherwise ``{"group": str | None,
    "permissions": [{"key", "label"}, ...]}``. Invalid keys are dropped with a
    warning (a bad permission key never blocks the whole plugin from loading).
    """
    if not raw:
        return {}
    if not isinstance(raw, dict):
        logger.warning("plugin %s: 'rbac' must be a mapping, ignored", pid)
        return {}
    group = raw.get("group")
    group_str = str(group).strip() if isinstance(group, str) and group.strip() else None
    perms_in = raw.get("permissions") or []
    if not isinstance(perms_in, list):
        logger.warning("plugin %s: 'rbac.permissions' must be a list, ignored", pid)
        return {"group": group_str, "permissions": []}
    perms: list[dict] = []
    seen: set[str] = set()
    for item in perms_in:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not _RBAC_KEY_RE.match(key):
            logger.warning(
                "plugin %s: invalid rbac permission key %r (expect %s), skipped",
                pid, key, _RBAC_KEY_RE.pattern,
            )
            continue
        if key in seen:
            continue
        seen.add(key)
        label = str(item.get("label") or key).strip()
        perms.append({"key": key, "label": label})
    return {"group": group_str, "permissions": perms}


# ---------------------------------------------------------------------------
# Tiny YAML subset parser (avoids hard pyyaml dependency).
# Supports: mappings (key: value), nested mappings via indentation, lists with
# ``-`` items (scalars or inline mappings), plain scalars (string/int/float/
# bool/null), comments with ``#``, double/single quoted strings.
# Does NOT support: anchors, multi-line strings (``|`` / ``>``), flow sequences
# ``[a, b]`` or flow mappings ``{a: 1}`` mid-line, complex types.
# ---------------------------------------------------------------------------


def _parse_yaml(text: str) -> Any:
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        pass
    return _parse_yaml_fallback(text)


def _parse_yaml_fallback(text: str) -> Any:
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        # strip inline comments only when not inside a quoted string (cheap)
        if "#" in line:
            in_str = False
            quote = ""
            for i, ch in enumerate(line):
                if ch in ('"', "'"):
                    if not in_str:
                        in_str = True
                        quote = ch
                    elif quote == ch:
                        in_str = False
                elif ch == "#" and not in_str:
                    line = line[:i].rstrip()
                    break
        lines.append(line)
    if not lines:
        return None
    pos = [0]

    def indent_of(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    def parse_block(indent: int) -> Any:
        items = None  # decided lazily: list or dict
        while pos[0] < len(lines):
            line = lines[pos[0]]
            cur_indent = indent_of(line)
            if cur_indent < indent:
                return items
            stripped = line[indent:]
            if stripped.startswith("- "):
                if items is None:
                    items = []
                pos[0] += 1
                value_part = stripped[2:].strip()
                if not value_part:
                    items.append(parse_block(indent + 2))
                elif ":" in value_part and not (value_part.startswith('"') or value_part.startswith("'")):
                    # inline mapping start — treat as nested mapping
                    key, _, rest = value_part.partition(":")
                    rest = rest.strip()
                    nested: dict = {}
                    if rest:
                        nested[key.strip()] = _scalar(rest)
                    extra = parse_block(indent + 2)
                    if isinstance(extra, dict):
                        nested.update(extra)
                    items.append(nested)
                else:
                    items.append(_scalar(value_part))
            elif ":" in stripped:
                if items is None:
                    items = {}
                key, _, rest = stripped.partition(":")
                key = key.strip()
                rest = rest.strip()
                pos[0] += 1
                if rest:
                    items[key] = _scalar(rest)
                else:
                    # nested block: indent of the next non-empty line
                    if pos[0] < len(lines) and indent_of(lines[pos[0]]) > cur_indent:
                        items[key] = parse_block(indent_of(lines[pos[0]]))
                    else:
                        items[key] = None
            else:
                # bare scalar at this level — unusual; skip
                pos[0] += 1
        return items

    return parse_block(0)


def _scalar(text: str) -> Any:
    text = text.strip()
    if not text:
        return ""
    if (text.startswith('"') and text.endswith('"')) or (
        text.startswith("'") and text.endswith("'")
    ):
        return text[1:-1]
    low = text.lower()
    if low == "true": return True
    if low == "false": return False
    if low in ("null", "~", ""): return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text
