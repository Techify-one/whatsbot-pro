"""Task supervisionada que drena a fila de saída.

Registrada em ``setup(ctx)`` e visível no painel de Runtime como
``trackify:outbox``. Molde: ``storages/plugins/agendamento_retorno/lifecycle.py``.

⚠️ O laço NÃO pode deixar exceção escapar. O supervisor desiste depois de 3
reinícios em 60s e marca a task como ``crashed`` — três quedas do Trackify num
minuto matariam o espelho até alguém desativar e reativar o plugin. Por isso o
``try/except`` abrange o ciclo inteiro e o laço sempre dorme e continua.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx

from . import _config, dispatcher

logger = logging.getLogger("plugins.trackify.lifecycle")

TICK_BUSY = 1.0     # com fila
TICK_IDLE = 5.0     # sem fila
PRUNE_EVERY = 3600.0

_worker_id = f"{os.getpid()}"


def _rate_per_min() -> int:
    try:
        v = int(_config.setting("rate_per_min", 40))
    except (TypeError, ValueError):
        return 40
    # O teto do Trackify é 60/min POR IP (não por canal). Nunca rodar no teto:
    # um 429 custa round-trip e a janela deslizante não é observável daqui.
    return max(1, min(v, 55))


async def _cycle(client: httpx.AsyncClient) -> int:
    """Um ciclo: reserva um lote e entrega respeitando o ritmo. Devolve quantos
    foram processados."""
    if not bool(_config.setting("mirror_enabled", False)):
        return 0
    if dispatcher.cooldown_remaining() > 0:
        return 0

    url = (_config.setting("ingestion_url") or "").strip()
    if not url:
        return 0
    api_key = (_config.setting("api_key") or "").strip()

    rate = _rate_per_min()
    gap = 60.0 / rate                     # espaçamento entre POSTs
    batch = max(1, min(rate, 10))         # lote curto: o ritmo manda, não o lote

    rows = await asyncio.to_thread(dispatcher.claim, batch, _worker_id)
    if not rows:
        return 0

    done = 0
    for row in rows:
        outcome = await dispatcher.deliver_one(client, row, url, api_key)
        done += 1
        if outcome == "throttled" or dispatcher.cooldown_remaining() > 0:
            # O servidor pediu calma: solta o resto do lote de volta na fila.
            break
        if done < len(rows):
            await asyncio.sleep(gap)
    return done


async def _loop(ctx) -> None:
    last_prune = 0.0
    async with httpx.AsyncClient(timeout=dispatcher.HTTP_TIMEOUT) as client:
        while True:
            processed = 0
            try:
                processed = await _cycle(client)

                now = time.time()
                if now - last_prune > PRUNE_EVERY:
                    last_prune = now
                    days = int(_config.setting("max_age_days", 7) or 7)
                    dropped = await asyncio.to_thread(dispatcher.prune, days)
                    if dropped:
                        logger.warning(
                            "trackify: %d evento(s) descartado(s) por passar de %d dias "
                            "na fila", dropped, days)
                    st = await asyncio.to_thread(dispatcher.stats)
                    try:
                        ctx.broadcast("trackify_outbox_tick", st)
                    except Exception:  # noqa: BLE001
                        pass
                    if st.get("backlog_age_s", 0) > 3600:
                        logger.warning("trackify: fila atrasada em %d s (pendentes=%s)",
                                       st["backlog_age_s"], st.get("pending"))
            except Exception:  # noqa: BLE001 — o laço NUNCA morre (ver docstring)
                logger.warning("trackify: ciclo do espelho falhou", exc_info=True)

            cool = dispatcher.cooldown_remaining()
            await asyncio.sleep(max(cool, TICK_BUSY if processed else TICK_IDLE))


def setup(ctx) -> None:
    """Sobe a task de entrega. Nunca derruba o boot: em harness de teste o
    supervisor não está cabeado e ``spawn_task`` levanta (padrão do protocolos)."""
    try:
        from runtime.supervisor import RestartPolicy
        ctx.spawn_task("outbox", lambda: _loop(ctx), policy=RestartPolicy.PERMANENT)
    except Exception:  # noqa: BLE001
        logger.debug("trackify: supervisor indisponível — espelho não iniciado",
                     exc_info=True)


def teardown(ctx) -> None:
    """Nada a parar: o core cancela as tasks por owner ao desativar o plugin."""
    return None
