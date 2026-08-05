"""Lifecycle — registra o VERIFICADOR (ciclo por minuto).

A tarefa é *owned* pelo plugin via `ctx.spawn_task` (plano 09): aparece como
``retornos:scheduler`` em ``GET /api/runtime/tasks`` / painel de Runtime e é cancelada
sozinha ao desativar o plugin. Espelha `retorno_automatico/lifecycle.py`.

Este plugin NÃO mexe em outro plugin: quem decide desativar o `retorno_automatico`
(que posta a própria nota de silêncio — com os dois ativos a conversa recebe nota
duplicada) é o operador, em Gerenciar Plugins.
"""

from __future__ import annotations

import asyncio
import logging

from . import actions, dispatcher

logger = logging.getLogger("plugin.retornos")

CHECK_INTERVAL_SECONDS = 60.0


def setup(ctx):
    """Chamado (e aguardado) pelo host depois que o supervisor/loop estão vivos."""
    logger.info("retornos: setup()")

    async def _scheduler_loop():
        # O dispatcher roda em thread e precisa do loop principal para as coroutines
        # do core (a IA responde agora) — registra na 1ª entrada do laço.
        actions.set_loop(asyncio.get_running_loop())
        while True:
            try:
                resumo = await asyncio.to_thread(dispatcher.run_cycle)
                if ctx.broadcast:
                    try:
                        ctx.broadcast("retornos_tick", {
                            "checked_at": resumo.get("checked_at"),
                            "claimed": resumo.get("claimed", 0),
                            "dispatched": resumo.get("dispatched", 0),
                            "retry": resumo.get("retry", 0),
                            "expired": resumo.get("expired", 0),
                            "cancelled": resumo.get("cancelled", 0),
                            "completed": resumo.get("completed", 0),
                        })
                    except Exception as e:  # broadcast ruim nunca mata o laço
                        logger.debug("tick broadcast falhou: %s", e)
            except Exception as e:  # noqa: BLE001 — um ciclo com erro não derruba o laço
                logger.warning("[Retornos] ciclo falhou: %s", e)
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    from runtime.supervisor import RestartPolicy
    ctx.spawn_task("scheduler", _scheduler_loop, policy=RestartPolicy.PERMANENT)
    logger.info("retornos: verificador (scheduler) registrado")


def teardown(ctx):
    """A tarefa é parada pelo supervisor (stop_owner); nada extra a limpar aqui."""
    logger.info("retornos: teardown()")
