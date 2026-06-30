"""Settings declarativas do plugin Atendimentos (toggles simples).

Os CAMPOS configuráveis (criar/remover, tipos) NÃO vivem aqui — são gerenciados
pela tela de configuração (screen ``config:true``) e persistidos como JSON em
``plugin.atendimentos.field_defs_*``. Aqui ficam só os interruptores booleanos,
que o ``PluginSettingsForm`` já renderiza.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Settings(BaseModel):
    enforce_backend: bool = Field(
        default=True,
        title="Exigir campos no backend",
        description=(
            "Recusar fechar a conversa via API (HTTP 403) se algum campo de "
            "resolução obrigatório não estiver preenchido — não burlável por "
            "chamada direta."
        ),
    )
    auto_link: bool = Field(
        default=True,
        title="Vincular conversas automaticamente",
        description=(
            "Ao receber/enviar mensagens, vincular a conversa ao atendimento "
            "aberto do contato (criando um novo se não houver). Desligado, os "
            "vínculos só acontecem ao resolver uma conversa."
        ),
    )
    mirror_custom_attributes: bool = Field(
        default=True,
        title="Espelhar campos no core (custom_attributes)",
        description=(
            "Ao resolver uma conversa, gravar também os campos preenchidos em "
            "conversations.custom_attributes do core — assim eles aparecem no painel "
            "de informações da conversa, ficam filtráveis e sobrevivem mesmo se o "
            "plugin for desativado. Desligado, os campos ficam só nas tabelas do plugin."
        ),
    )
