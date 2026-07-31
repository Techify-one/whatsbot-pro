"""Lifecycle do plugin WhatsApp Cloud API (plano 84).

Registra UMA task supervisionada: o polling de qualidade do número. Ela existe
porque a fonte principal do alerta (o webhook da conta) só funciona depois que os
campos forem assinados no App Dashboard da Meta — e ninguém percebe que isso não
foi feito. O polling não depende de assinatura nenhuma: ``status()`` do canal já
busca o ``quality_rating`` na Graph API (e o descartava até este plano).

Cadência em MINUTOS (default 10, piso 5): qualidade muda devagar e cada tique é
uma chamada Graph que consome rate limit da conta.

A task é ``owner``-escopada ao plugin — desabilitar/desinstalar o plugin a mata
(``stop_owner``). Sem ``deps`` (harness de teste, onde o lifespan é pulado) ou sem
supervisor cabeado, ``setup`` é um no-op limpo.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def setup(ctx) -> None:
    deps = getattr(ctx, "deps", None)
    if deps is None:
        logger.info("whatsapp_cloud: sem deps do servidor — lifecycle é no-op "
                    "(harness de teste)")
        return
    try:
        from runtime.supervisor import RestartPolicy
    except Exception as e:  # noqa: BLE001 — core antigo: o plugin segue funcionando
        logger.warning("whatsapp_cloud: supervisor indisponível (%s); "
                       "polling de qualidade não iniciado", e)
        return

    from . import alerts
    try:
        ctx.spawn_task("quality_poll", lambda: alerts.quality_poll_loop(deps),
                       policy=RestartPolicy.PERMANENT)
    except RuntimeError as e:  # supervisor não cabeado
        logger.warning("whatsapp_cloud: supervisor não cabeado (%s); "
                       "polling de qualidade não iniciado", e)
        return
    logger.info("whatsapp_cloud: lifecycle — polling de qualidade registrado "
                "(owner=%s)", ctx.plugin_id)


async def teardown(ctx) -> None:
    # A task supervisionada é parada automaticamente por stop_owner(plugin_id).
    logger.info("whatsapp_cloud: teardown (plugin_id=%s)", ctx.plugin_id)
