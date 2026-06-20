"""REST endpoints do plugin whatsapp_cloud (mountados em /api/plugins/whatsapp_cloud).

São endpoints de AJUDA/UI do provider — NÃO incluem o webhook. O webhook do
WhatsApp Cloud API é do core (path ``/api/webhook/whatsapp_cloud/{channel_id}``,
registrado/verificado pela Meta). Aqui ficam só conveniências para a tela de
configuração do plugin.
"""

from __future__ import annotations

from fastapi import APIRouter

# Default Graph API version. Kept in sync with settings.Settings.graph_api_version
# (the real configured value is read from plugin settings by the core). Avoids a
# cross-module import, which is brittle under the plugin loader's file-based
# import (submodules are not on sys.path).
DEFAULT_GRAPH_API_VERSION = "v21.0"

router = APIRouter()


@router.get("/info")
async def info():
    """Metadados do provider para a tela de configuração."""
    return {
        "ok": True,
        "data": {
            "provider": "whatsapp_cloud",
            "name": "WhatsApp Cloud API",
            "graph_api_version": DEFAULT_GRAPH_API_VERSION,
            "capabilities": {
                "qr": False,
                "templates": True,
                "groups": False,
                "inbound_route": "path",
            },
            "credential_keys": [
                "phone_number_id",
                "waba_id",
                "access_token",
                "verify_token",
                "app_secret",
            ],
            "webhook_path_template": "/api/webhook/whatsapp_cloud/{channel_id}",
        },
    }


@router.get("/templates")
async def list_templates(channel_id: str = ""):
    """Lista templates (HSM) aprovados do WABA.

    STUB: numa fase futura isto consulta
    ``GET /{waba_id}/message_templates`` na Graph API usando o access_token do
    canal e retorna os templates aprovados para o operador escolher. Por ora
    retorna lista vazia para a UI não quebrar.
    """
    return {"ok": True, "data": [], "stub": True}
