# Importable channel-provider plugins

Since plano 33 (canais 100% plugáveis), a **fresh install auto-installs only GOWA**
(the default WhatsApp channel — see `plugins/bootstrap.py` `BUNDLED_AUTO_INSTALL`).
The other channel providers are **import-only**: install them from the Plugins
screen with **Importar (.zip)**, or via `POST /api/plugins/import`.

This folder is the local output location for these ready-to-import zips. The
archives are ignored by Git, so a checkout ships only this README until someone
builds or downloads/publishes the artifacts separately:

| Provider | Zip | Adds channel type |
|---|---|---|
| Telegram (Bot API) | `telegram-plugin.zip` | `telegram` |
| WhatsApp Cloud API (Meta) | `whatsapp_cloud-plugin.zip` | `whatsapp_cloud` |
| Facebook Messenger (Meta) | `facebook_messenger-plugin.zip` | `facebook_messenger` |
| Instagram Direct (Meta) | `instagram-plugin.zip` | `instagram` |
| Site (Widget de chat) | `website-plugin.zip` | `website` |

Once imported and enabled, the provider **registers itself** in the channel
registry, its `provider_descriptor()` is served by `GET /api/channels/providers`,
and the core Channels screen renders its create/edit form **dynamically** — the
core Channels form has no provider-specific branch.

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
provider, rebuild its zip with the deterministic builder. It validates the
manifest at the archive root, sorts members, normalizes ZIP metadata and excludes
caches, Python bytecode, arquivos iniciados por `.env` e bancos locais:

```bash
venv/bin/python scripts/build_plugin_zips.py \
  telegram whatsapp_cloud website facebook_messenger instagram
```

Useful modes:

```bash
# Show every discoverable source plugin without writing files.
venv/bin/python scripts/build_plugin_zips.py --list

# Build every valid direct child of assets/plugin_examples/.
venv/bin/python scripts/build_plugin_zips.py --all

# CI/local parity check: exits 1 when a zip is missing or differs; never writes.
venv/bin/python scripts/build_plugin_zips.py --check \
  telegram whatsapp_cloud website facebook_messenger instagram
```

Generated `*-plugin.zip` files remain local build artifacts and are ignored by
Git. Publishing/distribution is a separate step.
