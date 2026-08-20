"""First-run setup wizard endpoints — Techify API key provisioning.

The setup wizard (frontend) connects WhatsApp, then triggers
``POST /api/setup/request-key`` which makes the WhatsBot send a WhatsApp
message to the Techify provisioning number. The provisioning number is
fetched at request time from Techify's ``/service_number`` endpoint (so it
can be rotated without a client release). Techify creates an account +
API key keyed by the sender's number. The wizard then polls
``GET /api/setup/key-status``, which in turn POSTs to Techify's
``/request-apikey`` endpoint (body ``{"number": ...}``) server-side and
saves the key to the config once ready.

Manual fallback: WhatsApp's reach-out timelock (error 463) can block the bot
from sending the provisioning message to a brand-new contact. When that happens
we still arm the key polling and return the data the frontend needs to ask the
user to send the very same message *by hand* from their own phone (a ``wa.me``
deep link + a scannable QR). Once it goes out, the key still lands in the config
on its own.

Plano 23 · Fase B6 — these routes are THIN delegators: the provisioning /
onboarding flow lives in ``app.services.provisioning_service``.
"""

import logging

import httpx  # noqa: F401 — re-exported so tests can patch server.routes.setup.httpx
from fastapi import Request

from app.services import provisioning_service as svc
from server.authz import permission_denied
from server.helpers import _ok, _err

logger = logging.getLogger(__name__)


def register_routes(app, deps):

    @app.post("/api/setup/request-key")
    async def request_key(request: Request):
        """Send the Techify provisioning message and arm the key polling.

        Gated by ``billing.manage`` — provisioning credits a Techify account
        (spends money). First-run onboarding has no logged-in user (open mode),
        so the gate passes; once users exist, only billing.manage can re-provision.
        """
        denied = permission_denied(request, "billing.manage")
        if denied:
            return denied
        kind, data = await svc.request_key(deps)
        if kind == "no_number":
            return _err(
                "Não foi possível identificar seu número. "
                "Aguarde a conexão concluir e tente de novo."
            )
        if kind == "no_destination":
            # O core não tem número embutido: sem destino configurado o wizard
            # recusa em vez de mandar a mensagem para um número que ninguém
            # escolheu. A mensagem não cita plugin nenhum — o core não os conhece.
            return _err(
                "Nenhum número de destino configurado para o provisionamento. "
                "Configure o número que deve receber o pedido de conta e "
                "tente de novo."
            )
        if kind == "send_failed":
            return _err(f"Não foi possível enviar a mensagem: {data['error']}")
        if kind == "manual":
            return _ok(data)
        return _ok({"status": "sent", "number": data["number"]})

    @app.get("/api/setup/key-status")
    async def key_status(request: Request):
        """Poll Techify for the provisioned API key; save it once ready.

        Saves the api_key when ready ⇒ a disguised write; gate like PUT /config.
        Open/first-run mode has no user, so onboarding still passes."""
        denied = permission_denied(request, "settings.manage")
        if denied:
            return denied
        return _ok(await svc.key_status(deps))
