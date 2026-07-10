"""Event handlers do plugin ``melhorias``.

Único handler: no ``app.startup``, faz o backfill one-time da config legada do
core (``improvement_model``/``improvement_prompt``) para o namespace do plugin.
"""

from __future__ import annotations

from . import logic


def on_startup(ctx, payload) -> None:  # noqa: ANN001 — assinatura do bus
    logic.backfill_core_config()


EVENT_HANDLERS = {"app.startup": on_startup}
