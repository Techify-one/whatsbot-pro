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

from . import _config, consent, dispatcher, pull, push, reconcile
from . import client as tk_client

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


# ── Sincronização de campos do contato ───────────────────────────────────

def _blocked_reason() -> str:
    """Motivo pelo qual a sincronização se auto-desligou (credencial ruim)."""
    return (_config.setting("sync_blocked_reason") or "").strip()


def _field_rate_per_min() -> int:
    try:
        v = int(_config.setting("field_sync_rate_per_min", 15))
    except (TypeError, ValueError):
        return 15
    # As rotas de contato do Trackify limitam a 30/min POR IP — e esse balde é
    # compartilhado com QUALQUER outra chamada que saia pelo mesmo IP. Metade de
    # folga, e um teto de código que a tela não consegue estourar.
    return max(1, min(v, 25))


async def _field_cycle(client: httpx.AsyncClient) -> int:
    if not _config.field_sync_ready() or _blocked_reason():
        return 0
    if push.cooldown_remaining() > 0:
        return 0

    rate = _field_rate_per_min()
    gap = 60.0 / rate
    batch = max(1, min(rate, 5))

    rows = await asyncio.to_thread(push.claim, batch, _worker_id)
    if not rows:
        return 0

    done = 0
    for row in rows:
        outcome = await push.deliver_one(client, row)
        done += 1
        if outcome == "throttled" or push.cooldown_remaining() > 0:
            break
        # Só espaça quando de fato houve ida à rede: um lote inteiro de "nada a
        # fazer" não pode consumir minutos de relógio à toa.
        if done < len(rows) and outcome in ("sent", "conflict", "blocked", "retry"):
            await asyncio.sleep(gap)
    return done


async def _field_loop(ctx) -> None:
    async with httpx.AsyncClient(timeout=tk_client.WRITE_TIMEOUT) as client:
        while True:
            processed = 0
            try:
                processed = await _field_cycle(client)
            except Exception:  # noqa: BLE001 — o laço NUNCA morre (ver docstring)
                logger.warning("trackify: ciclo de sincronização de campos falhou",
                               exc_info=True)
            cool = push.cooldown_remaining()
            await asyncio.sleep(max(cool, TICK_BUSY if processed else TICK_IDLE))


def _poll_seconds() -> float:
    try:
        v = float(_config.setting("field_sync_poll_seconds", 60))
    except (TypeError, ValueError):
        return 60.0
    return max(15.0, min(v, 3600.0))


async def _pull_loop(ctx) -> None:
    """Leitura periódica do changelog do CDP.

    É consulta, não webhook, porque o Trackify não emite nenhum evento de saída
    para contato — ver a docstring de ``pull``.
    """
    async with httpx.AsyncClient(timeout=tk_client.READ_TIMEOUT) as client:
        await _pull_forever(client)


async def _pull_forever(client: httpx.AsyncClient) -> None:
    while True:
        espera = _poll_seconds()
        try:
            resumo = await pull.cycle(client)
            if resumo.get("gravadas") or resumo.get("recusadas"):
                logger.info("trackify: leitura do CDP — %s gravada(s), %s recusada(s)",
                            resumo["gravadas"], resumo["recusadas"])
            if resumo.get("truncado"):
                # Ainda há backlog dentro da janela: volta já, em vez de esperar
                # o intervalo inteiro para andar mais um pedaço.
                espera = 1.0
        except Exception:  # noqa: BLE001 — o laço NUNCA morre (ver docstring)
            logger.warning("trackify: ciclo de leitura de campos falhou", exc_info=True)
        await asyncio.sleep(espera)


async def _reconcile_loop(ctx) -> None:
    """Varredura de conferência, em passagens parciais e com teto por ciclo.

    Sobe com um atraso inicial: no boot as outras duas tasks já têm o que fazer,
    e uma varredura competindo com elas pelo mesmo orçamento de 30/min só
    atrasaria a sincronização que o operador está olhando.
    """
    await asyncio.sleep(120.0)
    async with httpx.AsyncClient(timeout=tk_client.READ_TIMEOUT) as client:
        await _reconcile_forever(client)


async def _reconcile_forever(client: httpx.AsyncClient) -> None:
    while True:
        try:
            minutos = float(_config.setting("field_sync_reconcile_minutes", 60) or 60)
        except (TypeError, ValueError):
            minutos = 60.0
        espera = max(15.0, min(minutos, 1440.0)) * 60.0
        try:
            resumo = await reconcile.cycle(client)
            if resumo.get("enfileirados") or resumo.get("gravados"):
                logger.info("trackify: conferência — %s conferido(s), %s enfileirado(s), "
                            "%s gravado(s)", resumo["conferidos"],
                            resumo["enfileirados"], resumo["gravados"])
            if resumo.get("conferidos") and not resumo.get("volta"):
                # Ainda há base para percorrer: segue no ritmo do lote em vez de
                # esperar o intervalo inteiro a cada 100 contatos.
                espera = min(espera, 60.0)
        except Exception:  # noqa: BLE001 — o laço NUNCA morre (ver docstring)
            logger.warning("trackify: ciclo de conferência falhou", exc_info=True)
        await asyncio.sleep(espera)


def _limpar_segredos_legados() -> None:
    """Apaga do banco a senha da conta de serviço e o DSN do CDP.

    Roda uma vez por boot e é barata (dois DELETEs por chave que ainda exista).
    Deixar a senha de um usuário real do Nexus e um DSN de Postgres de PRODUÇÃO
    parados na tabela ``config`` depois que ninguém mais os lê é dívida de
    segurança, não compatibilidade — não há caminho de volta que os use.
    """
    try:
        from db.repositories import config_repo
        for chave in ("service_password", "service_email", "nexus_dsn",
                      "sync_user_id", "sync_last_login_error"):
            plena = _config.PREFIX + chave
            if config_repo.get(plena, None) in (None, ""):
                continue
            config_repo.set(plena, "")
            logger.info("trackify: setting legada '%s' limpa (não é mais usada)", chave)
    except Exception:  # noqa: BLE001 — limpeza nunca pode derrubar o boot
        logger.debug("trackify: falha ao limpar settings legadas", exc_info=True)


def setup(ctx) -> None:
    """Sobe as tasks. Nunca derruba o boot: em harness de teste o supervisor não
    está cabeado e ``spawn_task`` levanta (padrão do protocolos)."""
    _limpar_segredos_legados()
    # O handler de consentimento é síncrono e o core o despacha numa thread do
    # executor, onde não há laço. Sem esta referência a gravação não tem onde
    # ser agendada e todo clique morre em "sem laço de eventos". Fica FORA do
    # try/except do supervisor: não depende dele, e um supervisor ausente (o
    # harness de teste) não pode levar o descadastro junto.
    try:
        consent.bind_loop(getattr(ctx, "loop", None))
    except Exception:  # noqa: BLE001 — nunca derruba o boot
        logger.debug("trackify: não foi possível guardar o laço", exc_info=True)
    try:
        from runtime.supervisor import RestartPolicy
        ctx.spawn_task("outbox", lambda: _loop(ctx), policy=RestartPolicy.PERMANENT)
        ctx.spawn_task("fieldsync", lambda: _field_loop(ctx),
                       policy=RestartPolicy.PERMANENT)
        ctx.spawn_task("fieldpull", lambda: _pull_loop(ctx),
                       policy=RestartPolicy.PERMANENT)
        ctx.spawn_task("fieldreconcile", lambda: _reconcile_loop(ctx),
                       policy=RestartPolicy.PERMANENT)
        # Não há task de consentimento: desde a 3.0.0 o clique é gravado direto
        # numa task própria disparada pelo handler (ver ``consent.write_now``).
    except Exception:  # noqa: BLE001
        logger.debug("trackify: supervisor indisponível — tasks não iniciadas",
                     exc_info=True)


def teardown(ctx) -> None:
    """Nada a parar: o core cancela as tasks por owner ao desativar o plugin."""
    return None
