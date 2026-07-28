"""Settings declarativas do plugin Protocolos (toggles simples).

Os CAMPOS configuráveis (criar/remover, tipos) NÃO vivem aqui — são gerenciados
pela tela de configuração (screen ``config:true``) e persistidos como JSON em
``plugin.protocolos.field_defs_*``. Aqui ficam só os interruptores booleanos,
que o ``PluginSettingsForm`` já renderiza.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Settings(BaseModel):
    enforce_backend: bool = Field(
        default=True,
        title="Exigir campos no backend",
        description=(
            "Recusar fechar o atendimento via API (HTTP 403) se algum campo de "
            "resolução obrigatório não estiver preenchido — não burlável por "
            "chamada direta."
        ),
    )
    auto_link: bool = Field(
        default=True,
        title="Vincular atendimentos automaticamente",
        description=(
            "Ao receber/enviar mensagens, vincular o atendimento ao protocolo "
            "aberto do contato (criando um novo se não houver). Desligado, os "
            "vínculos só acontecem ao resolver um atendimento."
        ),
    )
    mirror_custom_attributes: bool = Field(
        default=True,
        title="Espelhar campos no core (custom_attributes)",
        description=(
            "Ao resolver um atendimento, gravar também os campos preenchidos em "
            "conversations.custom_attributes do core — assim eles aparecem no painel "
            "de informações do atendimento, ficam filtráveis e sobrevivem mesmo se o "
            "plugin for desativado. Desligado, os campos ficam só nas tabelas do plugin."
        ),
    )
    reactivate_ai_on_close: bool = Field(
        default=True,
        title="Devolver a conversa à IA depois de resolver",
        description=(
            "Depois que o atendimento é resolvido, devolver a conversa à IA (se o "
            "interruptor global e a IA do canal estiverem ligados). O tempo de espera "
            "fica na aba Geral do plugin: durante ele o atendente que resolveu continua "
            "com a conversa e a IA fica calada; com o tempo em 0 a IA reassume assim que "
            "o protocolo é finalizado. Desligado, o fechamento não mexe na IA."
        ),
    )
