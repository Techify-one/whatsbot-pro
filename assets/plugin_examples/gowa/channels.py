"""GOWA channel provider registration (plano 13 Fase 2).

Re-exports the core ``GOWAChannel`` so the plugin contributes the ``gowa``
provider through the standard ``entry.channels`` extension point — exactly like
``whatsapp_cloud``/``telegram`` do. The implementation physically stays in the
core tree for now (``channels/providers/gowa_channel.py``); the file move into
this folder is a deferred follow-up (plano 13 §2.1). ``register_provider`` is
last-wins idempotent (``channels/registry.py``), so re-registering the same class
the core also registers during the transition logs a benign 'replacing' line.
"""

from channels.providers.gowa_channel import (  # noqa: F401
    GOWAChannel,
    build_gowa_channel,
)

CHANNEL_PROVIDERS = [GOWAChannel]
