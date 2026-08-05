"""Settings declarativas GLOBAIS do plugin (modal "Configurar" do card em /plugins).

Só o que é do CICLO (infra). Tudo que é comportamento de follow-up — horário, condições,
cancelamentos, fuso — vive na PRÓPRIA CONFIGURAÇÃO (tela Retorno Automático), porque varia
por configuração. Lidas a cada ciclo (`dispatcher.load_settings`), então editar vale no ciclo
seguinte, sem restart.
"""

from pydantic import BaseModel, Field

# Texto de fábrica do aviso de janela fechada. Vive aqui (e não em `actions`) porque é o
# default do campo — quem edita a setting substitui esta string inteira.
JANELA_24H_MESSAGE_DEFAULT = (
    "⏳ A janela de 24 horas do WhatsApp expirou para {cliente} — o retorno automático NÃO "
    "foi enviado. Retome o contato manualmente (ou use um template aprovado)."
)


class Settings(BaseModel):
    enabled: bool = Field(
        default=True,
        title="Ativar o verificador",
        description=("Interruptor mestre. Desligado, nenhuma configuração dispara (as "
                     "configurações e os agendamentos em andamento ficam intactos)."),
    )
    grace_minutes: int = Field(
        default=15, ge=0, le=1440,
        title="Janela de tolerância (minutos)",
        description=("Se um agendamento vencer e o servidor só voltar a rodar depois desse "
                     "tempo, ele é CANCELADO em vez de disparar atrasado. 0 desliga "
                     "a tolerância (dispara sempre, por mais atrasado que esteja)."),
    )
    max_attempts_per_retorno: int = Field(
        default=60, ge=1, le=10000,
        title="Máximo de reavaliações do mesmo retorno",
        description=("Teto de segurança do ciclo, igual para todos os retornos. Quando as "
                     "condições de um retorno não batem, ele é reavaliado no ciclo seguinte — "
                     "esgotado esse número de reavaliações, o agendamento é encerrado como "
                     "“expirado” em vez de reavaliar para sempre."),
    )
    retorno_deadline_minutes: int = Field(
        default=4320, ge=0, le=525600,
        title="Prazo máximo de um retorno (minutos)",
        description=("O outro teto de segurança: tempo máximo que um retorno pode ficar sendo "
                     "reavaliado desde que virou o retorno atual. 0 = sem prazo (só o máximo "
                     "de reavaliações limita). Padrão 4320 = 3 dias."),
    )
    delay_between_messages_seconds: int = Field(
        default=2, ge=0, le=60,
        title="Pausa entre mensagens do mesmo retorno (segundos) — padrão",
        description=("Evita empilhar várias mensagens no mesmo instante no WhatsApp. Vale para "
                     "os retornos que não têm pausa própria: cada retorno pode definir a sua "
                     "no bloco “Mensagens deste retorno”, e o valor do retorno ganha."),
    )
    janela_24h_message: str = Field(
        default=JANELA_24H_MESSAGE_DEFAULT,
        title="Aviso quando a janela de 24h já passou",
        description=("Nota privada (só o atendente vê, nunca vai para o cliente) escrita no "
                     "atendimento quando um retorno vence mas a última mensagem do cliente já "
                     "passou da janela de 24 horas do WhatsApp — nesse caso a mensagem do "
                     "retorno NÃO é enviada. Placeholders: {cliente}, {primeiro_nome}, "
                     "{retorno} (nome do retorno) e {disparos}. Em branco, nenhuma nota é "
                     "escrita (o retorno é pulado em silêncio)."),
    )
    max_per_cycle: int = Field(
        default=50, ge=1, le=500,
        title="Máximo de agendamentos processados por ciclo",
        description=("Teto por minuto. Agendamentos além do teto ficam para o ciclo seguinte "
                     "(nada é perdido)."),
    )
