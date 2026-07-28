"""Settings declarativas do plugin whatsapp_cloud (Plano 02 Fase 2).

Apenas a preferência NÃO-secreta (versão da Graph API) vive aqui — o form
auto-gerado pelo ``PluginSettingsForm`` persiste em ``plugin.whatsapp_cloud.*``.

Os segredos do provider (access_token, phone_number_id, waba_id, verify_token,
app_secret) NÃO ficam nas settings do plugin: eles são credenciais por-canal,
gravadas via o registry de canais do core (``channels.registry`` →
``channel_credential_repo``) na tela "Canais". O form Preact deste plugin
(``static/whatsapp_cloud.js``) é só ajuda/documentação do provider.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Settings(BaseModel):
    graph_api_version: str = Field(
        default="v21.0",
        description="Versão da Graph API da Meta usada nas chamadas "
        "(ex.: v21.0). Mude apenas se a Meta depreciar a versão atual.",
    )
