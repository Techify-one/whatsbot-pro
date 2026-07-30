"""Settings declarativas do plugin whatsapp_cloud (Plano 02 Fase 2).

Só preferências NÃO-secretas vivem aqui (versão da Graph API e o descarte da
mensagem sem conteúdo do plano 95) — o form auto-gerado pelo
``PluginSettingsForm`` persiste em ``plugin.whatsapp_cloud.*``. São GLOBAIS do
plugin: valem para todo canal Cloud desta instalação.

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
    ignore_empty_meta_messages: bool = Field(
        default=True,
        title="Ignorar mensagens que a Meta entrega sem conteúdo",
        description="Às vezes a Meta entrega no webhook uma mensagem SEM texto "
        "nenhum — só o aviso \"Message type unknown\". O caso típico é o código "
        "de verificação do Facebook enviado para um número que está na API "
        "oficial: chega uma bolha vazia, e o WhatsBot trata como se fosse fala "
        "de cliente. Com esta opção ligada, essas mensagens não abrem NADA — "
        "sem atendimento, sem protocolo, sem resposta da IA e sem marcador de "
        "não lida. O registro fica só no log do servidor (com telefone e id da "
        "mensagem). Desligue para voltar ao comportamento antigo.",
    )
    ignore_error_codes: str = Field(
        default="",
        title="Códigos de erro ignorados",
        description="Restringe a opção acima a códigos específicos da Meta, "
        "separados por vírgula (ex.: 131051). Deixe VAZIO (padrão) para ignorar "
        "toda mensagem entregue sem conteúdo, qualquer que seja o código.",
    )
