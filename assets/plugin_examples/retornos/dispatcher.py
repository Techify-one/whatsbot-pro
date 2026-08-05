"""Dispatcher — o ciclo por minuto que dispara / reagenda / expira (plano 76 F4).

Sequência de um ciclo (porte de `dispatcher.service.ts` com as correções D5/D6):

1. **recovery** de locks presos (`processing` há mais de 5 min — processo morto no meio);
2. **lock atômico** dos controles vencidos (`UPDATE … RETURNING`, `repo.claim_due`);
3. **grace window**: controle atrasado além da tolerância é CANCELADO (não dispara uma
   mensagem de ontem hoje);
4. por controle: monta o contexto com dados **frescos** e avalia a árvore do retorno:
   * **falso** → `tentativas_retorno + 1` e **reagenda o MESMO retorno** (D5 — nunca avança
     fingindo progresso); esgotado o teto GLOBAL de segurança (settings
     `max_attempts_per_retorno` / `retorno_deadline_minutes`) ⇒ `expired`;
   * **verdadeiro** → executa as mensagens (rotação A/B), conta o disparo **só se ao
     menos uma saiu** (D6), e agenda o retorno seguinte (ou `completed`).

Roda inteiro em thread (`asyncio.to_thread`) — nenhuma coroutine é aguardada aqui; quem
precisa do loop é `actions._run_on_loop`.
"""

from __future__ import annotations

import logging
import time

from plugins.context import broadcast

from . import actions, evalctx, repo, rules

logger = logging.getLogger("plugin.retornos")

PLUGIN_ID = "retornos"
STALE_LOCK_SECONDS = 300.0
# De quanto em quanto tempo o lock do controle é renovado durante a pausa entre mensagens.
LOCK_HEARTBEAT_SECONDS = 30.0
# Teto do que UM disparo pode passar pausando. O ciclo é serial: sem teto, um retorno com
# muitas mensagens e pausa alta seguraria os demais controles (e os empurraria para fora da
# janela de tolerância). Estourado o teto, as mensagens restantes saem sem pausa.
MAX_PAUSA_TOTAL_SEGUNDOS = 300.0


# ── Settings (lidas a cada ciclo — valem sem restart) ─────────────────────────

def load_settings():
    from db.repositories import config_repo

    from .settings import Settings

    prefix = f"plugin.{PLUGIN_ID}."
    missing = object()
    data: dict = {}
    for field_name in Settings.model_fields:
        raw = config_repo.get(prefix + field_name, missing)
        if raw is not missing:
            data[field_name] = raw
    try:
        return Settings(**data)
    except Exception:  # noqa: BLE001 — config ruim nunca derruba o ciclo
        logger.warning("retornos: settings inválidas — usando defaults", exc_info=True)
        return Settings()


def _notify() -> None:
    try:
        broadcast("retornos_changed", {"ts": time.time()})
    except Exception:  # noqa: BLE001
        pass


# ── Agendamento ───────────────────────────────────────────────────────────────

def proximo_para_retorno(retorno: dict, ctx: dict, *, now: float,
                       base: float | None = None) -> float:
    """Quando este retorno pode disparar.

    Quem decide é a árvore de regras: o retorno é agendado para o próximo instante em que as
    regras temporais podem passar (agora mesmo, se não houver regra de hora/data). Não existe
    mais espera fixa por retorno — "esperar X" é condição (`modulo.horas_desde_*`), avaliada
    no disparo com dados frescos.
    """
    por_regra = rules.proximo_instante(retorno.get("filtros") or {}, ctx, now=now)
    return max(float(base if base is not None else now), por_regra)


def pausa_entre_mensagens(retorno: dict, cfg) -> float:
    """Segundos de pausa entre as mensagens DESTE retorno.

    O retorno manda quando tem valor próprio (`delay_mensagens_seg`); `NULL` = herda a
    setting GLOBAL do plugin (`delay_between_messages_seconds`), que é o estado de todo
    retorno criado antes da 1.6.0.
    """
    proprio = (retorno or {}).get("delay_mensagens_seg")
    if proprio is not None:
        try:
            return max(0.0, min(float(proprio), float(repo.MAX_PAUSA_MENSAGENS_SEG)))
        except (TypeError, ValueError):  # coluna com lixo nunca derruba o disparo
            pass
    return max(0.0, float(getattr(cfg, "delay_between_messages_seconds", 2) or 0))


def _pausar(segundos: float, *, controle_id: int, sleeper=time.sleep) -> None:
    """Dorme em fatias, renovando o lock do controle a cada fatia.

    Sem isso, uma pausa longa deixaria o controle parado em `processing` além de
    `STALE_LOCK_SECONDS` e o `recover_stale_locks` do ciclo seguinte o devolveria para a
    fila com `next_at` no passado — ou seja, o MESMO retorno dispararia de novo enquanto o
    primeiro disparo ainda está no meio das mensagens (mensagem duplicada para o cliente).
    """
    restante = float(segundos)
    while restante > 0:
        fatia = min(restante, LOCK_HEARTBEAT_SECONDS)
        sleeper(fatia)
        restante -= fatia
        if restante > 0:
            try:
                repo.update_controle(controle_id, processing_since=time.time())
            except Exception:  # noqa: BLE001 — heartbeat é best-effort
                logger.debug("retornos: heartbeat do lock %s falhou", controle_id)


def pick_mensagens(retorno: dict) -> list[dict]:
    """Mensagens deste disparo.

    O teste A/B é do RETORNO inteiro (`ab_ativo`), não de cada mensagem: ligado, sai UMA das
    mensagens por disparo, alternando pelo cursor — desligado, saem todas, na ordem.
    """
    todas = repo.list_mensagens(retorno["id"])
    if not retorno.get("ab_ativo") or len(todas) <= 1:
        return todas
    idx = repo.bump_msg_cursor(retorno["id"]) % len(todas)
    return [todas[idx]]


# ── Um controle ───────────────────────────────────────────────────────────────

def _reagendar_ou_expirar(ctrl: dict, retorno: dict, ctx: dict, *, now: float, cfg,
                          motivo: str, erro: str | None = None) -> list[str]:
    """Reagenda o MESMO retorno — ou expira o agendamento no teto GLOBAL de segurança.

    O teto não é mais do retorno (aquilo era configuração de negócio pedindo número de infra):
    vem das Settings do plugin e vale igual para todos, só para que um agendamento cujas
    condições nunca batem não fique reavaliando eternamente.
    """
    tentativas = int(ctrl.get("tentativas_retorno") or 0) + 1
    iniciado = float(ctrl.get("retorno_started_at") or now)
    max_tent = max(1, int(getattr(cfg, "max_attempts_per_retorno", 60) or 60))
    deadline = max(0, int(getattr(cfg, "retorno_deadline_minutes", 4320) or 0)) * 60.0
    estourou_prazo = deadline > 0 and (now - iniciado) > deadline
    if tentativas >= max_tent or estourou_prazo:
        repo.update_controle(ctrl["id"], tentativas_retorno=tentativas, processing=0,
                             processing_since=None, status=repo.STATUS_EXPIRED,
                             last_error=erro or motivo)
        repo.add_log("expired", configuracao_id=ctrl.get("configuracao_id"), controle_id=ctrl["id"],
                     conversation_id=ctrl.get("conversation_id"), retorno_id=retorno.get("id"),
                     nivel="warning",
                     data={"motivo": motivo, "tentativas": tentativas,
                           "prazo_estourado": estourou_prazo, "erro": erro})
        _notify()
        return ["expired"]

    next_at = rules.proximo_instante(retorno.get("filtros") or {}, ctx, now=now)
    repo.update_controle(ctrl["id"], tentativas_retorno=tentativas, next_at=next_at,
                         processing=0, processing_since=None, last_error=erro)
    repo.add_log("retry" if not erro else "failed",
                 configuracao_id=ctrl.get("configuracao_id"), controle_id=ctrl["id"],
                 conversation_id=ctrl.get("conversation_id"), retorno_id=retorno.get("id"),
                 nivel="warning" if erro else "info",
                 data={"motivo": motivo, "tentativas": tentativas,
                       "next_at": next_at, "erro": erro})
    return ["failed", "retry"] if erro else ["retry"]


def process_controle(ctrl: dict, *, now: float, cfg) -> list[str]:
    """Processa UM controle já travado. Devolve o desfecho (para o sumário do ciclo)."""
    cid = ctrl["id"]
    conv_id = ctrl.get("conversation_id")

    configuracao = repo.get_configuracao(ctrl.get("configuracao_id"))
    if not configuracao or not configuracao.get("ativo"):
        repo.set_status(cid, repo.STATUS_CANCELLED, last_error="configuração inativa ou removida")
        repo.add_log("cancelled", configuracao_id=ctrl.get("configuracao_id"), controle_id=cid,
                     conversation_id=conv_id, data={"motivo": "configuracao_inativa"})
        _notify()
        return ["cancelled"]

    # Grace window — atrasado demais não dispara (mensagem de ontem hoje).
    grace = max(0, int(getattr(cfg, "grace_minutes", 15) or 0)) * 60.0
    atraso = now - float(ctrl.get("next_at") or now)
    if grace > 0 and atraso > grace:
        repo.set_status(cid, repo.STATUS_CANCELLED,
                        last_error=f"fora da janela de tolerância ({int(atraso // 60)} min de atraso)")
        repo.add_log("cancelled", configuracao_id=configuracao.get("id"), controle_id=cid,
                     conversation_id=conv_id, nivel="warning",
                     data={"motivo": "grace_window", "atraso_min": round(atraso / 60, 1)})
        _notify()
        return ["cancelled"]

    conv, contact = evalctx.load_target(conversation_id=int(conv_id))
    if not conv:
        repo.set_status(cid, repo.STATUS_CANCELLED, last_error="conversa não existe mais")
        repo.add_log("cancelled", configuracao_id=configuracao.get("id"), controle_id=cid,
                     conversation_id=conv_id, data={"motivo": "conversa_removida"})
        _notify()
        return ["cancelled"]

    if int((contact or {}).get("is_group") or 0) == 1 and not configuracao.get("apply_to_groups"):
        repo.set_status(cid, repo.STATUS_CANCELLED, last_error="configuração não se aplica a grupos")
        repo.add_log("cancelled", configuracao_id=configuracao.get("id"), controle_id=cid,
                     conversation_id=conv_id, data={"motivo": "grupo"})
        _notify()
        return ["cancelled"]

    retorno = (repo.get_retorno(ctrl["retorno_atual_id"]) if ctrl.get("retorno_atual_id")
             else repo.first_retorno(configuracao["id"]))
    if not retorno or int(retorno.get("configuracao_id") or 0) != int(configuracao["id"]):
        retorno = repo.first_retorno(configuracao["id"])
    if not retorno:
        repo.set_status(cid, repo.STATUS_COMPLETED, last_error=None)
        repo.add_log("completed", configuracao_id=configuracao.get("id"), controle_id=cid,
                     conversation_id=conv_id, data={"motivo": "sem_retornos"})
        _notify()
        return ["completed"]

    if ctrl.get("retorno_atual_id") != retorno["id"]:
        ctrl = repo.update_controle(cid, retorno_atual_id=retorno["id"], retorno_started_at=now,
                                    tentativas_retorno=0) or ctrl

    ctx = evalctx.build_ctx(conv=conv, contact=contact, configuracao=configuracao, controle=ctrl, now=now)

    if not rules.avaliar(retorno.get("filtros") or {}, ctx):
        return _reagendar_ou_expirar(ctrl, retorno, ctx, now=now, cfg=cfg,
                                     motivo="filtros_nao_bateram")

    # ── Dispara ──
    mensagens = pick_mensagens(retorno)
    if not mensagens:
        # Retorno sem mensagem = retorno de gate puro: cumpre e segue para o próximo.
        return _avancar(ctrl, configuracao, retorno, ctx, now=now, sucessos=0, erros=[],
                       vazio=True)

    espera = pausa_entre_mensagens(retorno, cfg)
    orcamento = MAX_PAUSA_TOTAL_SEGUNDOS
    truncou = False
    sucessos, erros, detalhes = 0, [], []
    for i, msg in enumerate(mensagens):
        if i and espera:
            fatia = min(espera, orcamento)
            if fatia < espera and not truncou:
                truncou = True
                logger.warning("[Retornos] retorno %s: teto de pausa por disparo atingido — "
                               "as mensagens restantes saem sem pausa", retorno.get("id"))
            if fatia > 0:
                _pausar(fatia, controle_id=cid)
                orcamento -= fatia
        try:
            res = actions.execute(msg, controle=ctrl, configuracao=configuracao, conv=conv,
                                  contact=contact, retorno=retorno, cfg=cfg)
        except Exception as e:  # noqa: BLE001 — uma mensagem ruim não derruba as outras
            logger.warning("[Retornos] mensagem %s falhou (conv=%s): %s",
                           msg.get("id"), conv_id, e)
            erros.append(f"{msg.get('tipo')}: {e}")
            continue
        if res.get("ok"):
            sucessos += 1
            detalhes.append(res.get("detalhe") or res.get("tipo"))
        else:
            erros.append(f"{msg.get('tipo')}: {res.get('detalhe')}")

    if sucessos == 0:
        return _reagendar_ou_expirar(ctrl, retorno, ctx, now=now, cfg=cfg, motivo="envio_falhou",
                                     erro="; ".join(erros)[:480] or "nenhuma mensagem saiu")

    return _avancar(ctrl, configuracao, retorno, ctx, now=now, sucessos=sucessos, erros=erros,
                    detalhes=detalhes)


def _avancar(ctrl: dict, configuracao: dict, retorno: dict, ctx: dict, *, now: float,
             sucessos: int, erros: list, detalhes: list | None = None,
             vazio: bool = False) -> list[str]:
    """Conta o disparo (D6) e agenda o retorno seguinte — ou conclui a configuração."""
    cid = ctrl["id"]
    conv_id = ctrl.get("conversation_id")
    disparos = int(ctrl.get("disparos_enviados") or 0) + (1 if sucessos else 0)

    if sucessos:
        repo.add_log("dispatched", configuracao_id=configuracao.get("id"), controle_id=cid,
                     conversation_id=conv_id, retorno_id=retorno.get("id"),
                     data={"retorno": retorno.get("nome"), "mensagens_ok": sucessos,
                           "detalhes": detalhes or [], "erros": erros})
    elif vazio:
        repo.add_log("skipped", configuracao_id=configuracao.get("id"), controle_id=cid,
                     conversation_id=conv_id, retorno_id=retorno.get("id"),
                     data={"motivo": "retorno_sem_mensagem"})

    desfechos = ["dispatched"] if sucessos else (["skipped"] if vazio else [])
    prox = repo.next_retorno(configuracao["id"], int(retorno.get("ordem") or 0))
    if prox is None:
        repo.update_controle(cid, disparos_enviados=disparos, tentativas_retorno=0,
                             processing=0, processing_since=None,
                             status=repo.STATUS_COMPLETED, last_error=None)
        repo.add_log("completed", configuracao_id=configuracao.get("id"), controle_id=cid,
                     conversation_id=conv_id, data={"disparos": disparos})
        _notify()
        return desfechos + ["completed"]

    next_at = proximo_para_retorno(prox, ctx, now=now, base=now)
    repo.update_controle(cid, disparos_enviados=disparos, retorno_atual_id=prox["id"],
                         retorno_started_at=now, tentativas_retorno=0, next_at=next_at,
                         processing=0, processing_since=None, last_error=None)
    _notify()
    return desfechos


# ── Ciclo ─────────────────────────────────────────────────────────────────────

def run_cycle() -> dict:
    """Um ciclo completo. Nunca levanta: devolve o sumário (e loga o que falhou)."""
    now = time.time()
    cfg = load_settings()
    if not bool(getattr(cfg, "enabled", True)):
        return {"checked_at": now, "enabled": False, "claimed": 0}

    soltos = 0
    try:
        soltos = repo.recover_stale_locks(older_than=now - STALE_LOCK_SECONDS)
    except Exception as e:  # noqa: BLE001
        logger.warning("[Retornos] recovery de locks falhou: %s", e)

    try:
        pendentes = repo.claim_due(now=now, limit=int(getattr(cfg, "max_per_cycle", 50) or 50))
    except Exception as e:  # noqa: BLE001
        logger.warning("[Retornos] claim_due falhou: %s", e)
        return {"checked_at": now, "enabled": True, "claimed": 0, "erro": str(e)}

    # Um controle pode render MAIS DE UM desfecho (um disparo que encerra a configuração conta
    # `dispatched` e `completed`), então cada chave é contada de forma independente.
    resumo = {"checked_at": now, "enabled": True, "claimed": len(pendentes),
              "locks_liberados": soltos, "dispatched": 0, "retry": 0, "failed": 0,
              "expired": 0, "cancelled": 0, "completed": 0, "skipped": 0,
              "outros": 0, "falhas": 0}

    for ctrl in pendentes:
        try:
            desfechos = process_controle(ctrl, now=now, cfg=cfg)
        except Exception as e:  # noqa: BLE001 — um controle ruim não derruba o ciclo
            resumo["falhas"] += 1
            logger.warning("[Retornos] controle %s falhou (conv=%s): %s",
                           ctrl.get("id"), ctrl.get("conversation_id"), e)
            try:
                repo.update_controle(ctrl["id"], processing=0, processing_since=None,
                                     last_error=str(e)[:480],
                                     next_at=time.time() + 300.0)
                repo.add_log("failed", configuracao_id=ctrl.get("configuracao_id"),
                             controle_id=ctrl.get("id"),
                             conversation_id=ctrl.get("conversation_id"),
                             nivel="error", data={"erro": str(e)[:480]})
            except Exception:  # noqa: BLE001
                logger.debug("retornos: não conseguiu liberar o lock do controle %s",
                             ctrl.get("id"))
            continue
        for desfecho in (desfechos or []):
            if desfecho in resumo:
                resumo[desfecho] += 1
            else:
                resumo["outros"] += 1

    if resumo["dispatched"] or resumo["expired"] or resumo["falhas"]:
        logger.info("[Retornos] ciclo: %s", resumo)
    return resumo
