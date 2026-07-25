# Importable channel-provider plugins

Since plano 33 (canais 100% plugáveis), a **fresh install auto-installs only GOWA**
(the default WhatsApp channel — see `plugins/bootstrap.py` `BUNDLED_AUTO_INSTALL`).
The other channel providers are **import-only**: install them from the Plugins
screen with **Importar (.zip)**, or via `POST /api/plugins/import`.

This folder ships the ready-to-import zips:

| Provider | Zip | Adds channel type |
|---|---|---|
| Telegram (Bot API) | `telegram-plugin.zip` | `telegram` |
| WhatsApp Cloud API (Meta) | `whatsapp_cloud-plugin.zip` | `whatsapp_cloud` |
| Facebook Messenger (Meta) | `facebook_messenger-plugin.zip` | `facebook_messenger` |
| Site (Widget de chat) | `website-plugin.zip` | `website` |

Once imported and enabled, the provider **registers itself** in the channel
registry, its `provider_descriptor()` is served by `GET /api/channels/providers`,
and the core Channels screen renders its create/edit form **dynamically** — the
core has no per-provider code.

Beyond the form, the descriptor now also drives (plano 76): the provider's
**label/color/contact-type** across every screen (via the client `providerCatalog`),
credential **masking** at the API edge (`type: "text"` = public, everything else
masked, with a name guard), and the widget's install **snippet** (via
`post_create.snippet_template`). A provider that needs a custom row on its channel
card (e.g. `whatsapp_cloud`'s webhook-health line) ships a `frontend_extends`
module that registers a component into the `channel.card.rows` slot — the core
never names the provider.

## Regenerating the zips

The source of truth lives in `assets/plugin_examples/<id>/`. After editing a
provider, rebuild its zip (same structure the export endpoint produces —
`plugin.yaml` at the zip root, no `__pycache__`/`.db`):

```bash
venv/bin/python - <<'PY'
import zipfile, pathlib
for pid in ("telegram", "whatsapp_cloud", "website", "facebook_messenger"):
    src = pathlib.Path("assets/plugin_examples")/pid
    out = pathlib.Path("assets/channel_plugins")/f"{pid}-plugin.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src.rglob("*")):
            if p.is_dir() or "__pycache__" in p.parts or p.suffix in (".db",".pyc"): continue
            zf.write(p, p.relative_to(src).as_posix())
PY
```
