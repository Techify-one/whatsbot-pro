"""Lifecycle do plugin Protocolos — varredura da posse temporária pós-fechamento.

Quem resolve uma conversa fica com ela por N minutos (``ai_takeover_delay_minutes``);
vencido o prazo, a IA reassume. O vencimento é EAGER: esta task supervisada varre as
linhas vencidas de ``plugin_protocolos_ai_holds`` e devolve cada conversa à IA via
``conversation_service.set_ai`` (limpa o dono, revincula o agente padrão do inbox,
liga ``ai_active`` e registra o card "🤖 SISTEMA reativou a IA.").

DB-backed ⇒ sobrevive a restart (holds vencidos durante o downtime são processados no
boot). Best-effort: nunca levanta (o supervisor reinicia a task se algo fatal escapar).
"""

from __future__ import annotations

import asyncio
import logging

from . import logic

logger = logging.getLogger(__name__)

# A granularidade da janela é minutos; 30 s de folga é imperceptível e barato (a
# varredura sai por um SELECT indexado em hold_until, quase sempre vazio).
RELEASE_INTERVAL = 30.0


async def release_loop() -> None:
    """Task PERMANENTE (spawn no ``setup``): devolve à IA o que já venceu."""
    while True:
        try:
            n = await logic.expire_ai_holds_once()
            if n:
                logger.info("protocolos: %d conversa(s) devolvida(s) à IA "
                            "(posse temporária vencida)", n)
        except Exception as e:  # noqa: BLE001
            logger.warning("protocolos: varredura de posse temporária falhou: %s", e)
        await asyncio.sleep(RELEASE_INTERVAL)


async def setup(ctx) -> None:
    """Registra a varredura como task supervisada (owner = plugin). No-op limpo quando
    o supervisor de runtime não está cabeado (harness de teste / boot degradado)."""
    try:
        ctx.spawn_task("ai_takeover_release", release_loop)
        logger.info("protocolos: varredura de posse temporária ativa")
    except Exception as e:  # noqa: BLE001
        logger.info("protocolos: sem supervisor de runtime — varredura não iniciada (%s)", e)
