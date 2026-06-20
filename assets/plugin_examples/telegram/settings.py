"""Settings declarativas do plugin telegram (plano 13 Fase 3).

Só preferências NÃO-secretas. O ``bot_token`` é CREDENCIAL DE CANAL — gravado por
canal via o registry do core (tela "Canais"), nunca aqui. O form auto-gerado
(``PluginSettingsForm``) persiste em ``plugin.telegram.*`` e é lido em runtime
pela ``lifecycle.py`` (modo de inbound + cadência do long-poll).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Settings(BaseModel):
    inbound_mode: str = Field(
        default="poll",
        description="Como receber mensagens: 'poll' (long-poll getUpdates — "
        "funciona sem host público, recomendado para desktop/EXE) ou 'webhook' "
        "(registre a URL mostrada na tela de configuração; exige host público). "
        "Trocar exige reiniciar.",
    )
    poll_interval: float = Field(
        default=2.0,
        description="Intervalo (segundos) entre ciclos de polling quando "
        "inbound_mode='poll'. O long-poll já segura a conexão; este é o respiro "
        "entre ciclos.",
    )
