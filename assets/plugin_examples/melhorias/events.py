"""Event handlers do plugin ``melhorias``.

- ``app.startup``: backfill one-time da config legada do core
  (``improvement_model``/``improvement_prompt``) para o namespace do plugin.
- ``contact.updated``: contato renomeado → re-sincroniza o snapshot de nome/telefone
  gravado nas sugestões daquele contato (exibição + busca refletem o nome atual).
"""

from __future__ import annotations

from . import logic


def on_startup(ctx, payload) -> None:  # noqa: ANN001 — assinatura do bus
    logic.backfill_core_config()


def on_contact_updated(ctx, payload) -> None:  # noqa: ANN001 — assinatura do bus
    logic.refresh_contact_snapshot(str((payload or {}).get("phone") or ""))


EVENT_HANDLERS = {
    "app.startup": on_startup,
    "contact.updated": on_contact_updated,
}
