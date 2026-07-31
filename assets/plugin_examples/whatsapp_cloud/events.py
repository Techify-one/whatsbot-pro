"""Event handlers do plugin WhatsApp Cloud API (plano 84).

Uma assinatura só: ``message.failed`` — o provedor recusou a entrega. O evento
**já existe no core** desde o plano 75 (com `error_code`/`error_title`/`is_new`;
na revisão 1.10.2 ganhou o snapshot `is_redelivery`),
então não foi preciso inventar nada: aqui ele é filtrado por código e AGREGADO,
senão uma rajada de falhas do mesmo fluxo vira uma enxurrada no grupo.

Os avisos da CONTA (template pausado, qualidade, tier, restrição) NÃO passam por
aqui: eles são capturados no webhook cru pelo observador em `filters.py`, depois
que o seam genérico do core resolveu rota/procedência e confirmou o HMAC. Não há
evento nem ramo de dispatch específico de conta (plano 84 · P1(b)).

O handler é defensivo por dentro (``alerts.py``): um alerta que falha nunca
derruba o pipeline de mensagens.
"""

from . import alerts

EVENT_HANDLERS = {
    "message.failed": alerts.on_message_failed,
}
