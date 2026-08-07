"""Clique em botão → AÇÃO → escrita no cadastro do CDP.

Um clique cujo **payload** casa um dos códigos configurados ativa a ação dona
daquele código. Os códigos são os mesmos dos dois lados: o módulo Campanhas os
manda nos botões do template, este plugin os reconhece. Hoje existe uma ação
(descadastro de marketing); o catálogo mora em ``actions.py``.

Não há tabela de regras, allow-list de canais nem lista de "botões vistos" —
todos existiam para o operador **adivinhar** qual string a Meta devolveria, e
com o payload explícito não há o que adivinhar.

Também não há fila. O handler grava direto, numa task própria:

* **numa task, não no handler** — o barramento é fire-and-forget e engole
  exceção, e uma chamada HTTP dentro dele seguraria o evento mais quente do
  sistema;
* **direto, sem outbox** — decisão explícita do produto. O preço está registrado
  em :func:`write_now`: um descadastro perdido numa queda de rede longa ou num
  restart no meio da entrega **não é recuperado sozinho**. As tentativas
  internas encurtam a janela, não a fecham; o que sobra fica em
  ``plugin_trackify_consent_state.last_error``.
"""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import text

from plugins.context import audit, make_plugin_db

from . import _config, actions, consent_match, field_map, push, writer
from . import client as tk_client

logger = logging.getLogger("plugins.trackify.consent")

OFFICIAL_PROVIDER = "whatsapp_cloud"

CHANNEL_TTL = 30.0

# Tentativas dentro da MESMA task, para falha de rede/ritmo passageira. Não é
# fila: nada disto sobrevive a um restart.
MAX_ATTEMPTS = 3
RETRY_DELAYS = (2.0, 8.0)

# Cache de provider por canal. ``message.received`` é o evento mais quente do
# sistema — mesmo com os guards baratos na frente, uma ida ao banco por clique
# não precisa virar uma por mensagem. Precedente: o cache de tipos de JID em
# ``app/services/message_ingest_service.py``.
_channel_cache: dict[str, tuple[float, str]] = {}

# Referência forte das tasks em voo. Sem isso o GC pode coletar uma task ainda
# não concluída e o descadastro some sem log (documentado em
# `plugins/context.py`, mesmo cuidado dos observadores de webhook).
_inflight: set = set()

# O laço do servidor, guardado no ``setup(ctx)``.
#
# ⚠️ Este handler é SÍNCRONO, e o core despacha handler síncrono com
# ``await asyncio.to_thread(handler, ctx, payload)`` (``plugins/events.py``) —
# ou seja, ele roda numa thread do executor, onde ``get_running_loop()``
# SEMPRE levanta. Sem esta referência não haveria onde agendar a gravação e
# todo clique morreria em "sem laço de eventos", que foi exatamente o que
# aconteceu em produção. O handler continua síncrono de propósito: ele toca o
# banco, e banco no laço travaria o evento mais quente do sistema.
_loop: asyncio.AbstractEventLoop | None = None


def _now() -> float:
    return time.time()


def bind_loop(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Guarda o laço em que a gravação será agendada. Chamado no ``setup``."""
    global _loop
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
    _loop = loop


def reset_for_tests() -> None:
    """Zera o estado de módulo. Só para a suíte — o cache é global."""
    global _loop
    _channel_cache.clear()
    _inflight.clear()
    _loop = None


# ── Gates ────────────────────────────────────────────────────────────────

def provider_of(channel_id: str) -> str:
    if not channel_id:
        return ""
    agora = _now()
    hit = _channel_cache.get(channel_id)
    if hit and (agora - hit[0]) < CHANNEL_TTL:
        return hit[1]
    try:
        from db.repositories import channel_repo
        ch = channel_repo.get(channel_id) or {}
    except Exception:  # noqa: BLE001 — falha de leitura não é cacheada
        return ""
    provider = str(ch.get("provider") or "")
    _channel_cache[channel_id] = (agora, provider)
    return provider


def is_official(channel_id: str) -> bool:
    """Gate DURO da feature. Só WhatsApp oficial (Cloud API).

    Com a allow-list de canais removida, é ele sozinho que decide de onde um
    clique pode virar descadastro. Continua valendo para TODO canal oficial:
    o código do payload é global, e um contato que pede para sair num número
    pediu para sair.
    """
    return provider_of(channel_id) == OFFICIAL_PROVIDER


def official_channels() -> list[dict]:
    """Canais elegíveis, para a tela mostrar. Nunca levanta."""
    try:
        from db.repositories import channel_repo
        rows = channel_repo.list_all() or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for ch in rows:
        if str(ch.get("provider") or "") != OFFICIAL_PROVIDER:
            continue
        if int(ch.get("archived") or 0):
            continue
        out.append({
            "channel_id": ch.get("id"),
            "display_name": ch.get("display_name") or ch.get("id"),
            "own_phone": ch.get("own_phone") or "",
            "enabled": int(ch.get("enabled") or 0),
        })
    return out


# ── Gancho do barramento ─────────────────────────────────────────────────

def on_message_received(ctx, payload: dict) -> None:
    """Um clique de botão ativou alguma ação?

    A ordem dos guards é deliberada: a primeira comparação é em memória e mata
    ~99,9% do tráfego (toda mensagem de texto) **sem tocar no banco**. É isso
    que autoriza este plugin a assinar o evento mais quente do sistema.
    """
    try:
        p = payload or {}
        if p.get("media_type") != "interactive":
            return
        if p.get("is_group"):
            return

        cand = consent_match.tokens(p.get("media_extras"))
        if not cand:
            # Forma que não reconhecemos (ex.: o {button_id,title} do GOWA, ou
            # uma resposta de Flow). Segunda linha de defesa além do gate de
            # provider abaixo.
            return

        channel_id = str(p.get("channel_id") or "")
        if not is_official(channel_id):
            return

        # UMA leitura de settings por clique casável: o gate e o índice de
        # códigos saem do mesmo snapshot.
        snap = actions.snapshot()
        if not snap.ready:
            return
        action_id = consent_match.action_for(cand, snap.index)
        if not action_id:
            # Código que não é de ação nenhuma (ou está em conflito entre duas).
            # O Campanhas manda um código por botão e a maioria não significa
            # nada para este plugin — ignorar em silêncio é o caso comum.
            return

        from db.repositories import contact_repo
        contact = contact_repo.get_by_phone(str(p.get("phone") or ""))
        if not contact or contact.get("is_group"):
            return

        contact_id = int(contact["id"])
        _record_state(contact_id, action_id, cand, channel_id,
                      str(p.get("msg_id") or ""))
        _spawn_write(contact_id, action_id)
    except Exception:  # noqa: BLE001 — handler de bus nunca derruba o pipeline
        logger.debug("trackify: falha ao processar clique de consentimento",
                     exc_info=True)


def _spawn_write(contact_id: int, action_id: str) -> None:
    """Agenda a gravação fora do caminho quente.

    Três caminhos, nesta ordem — o do meio é o de produção:

    1. **já estamos no laço** (handler async, teste async): ``create_task``;
    2. **thread do executor com laço guardado**: ``run_coroutine_threadsafe``.
       É por onde o core entrega este handler (veja ``_loop``);
    3. **nenhum laço à vista** (script, teste síncrono, boot): roda um laço
       próprio NESTA thread. Segura a thread durante as tentativas, mas é
       preferível a descartar um descadastro — o pedido é raro e o custo é de
       uma thread do executor.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        task = loop.create_task(write_now(contact_id, action_id))
        _inflight.add(task)
        task.add_done_callback(_inflight.discard)
        return

    alvo = _loop
    if alvo is not None and not alvo.is_closed():
        try:
            fut = asyncio.run_coroutine_threadsafe(
                write_now(contact_id, action_id), alvo)
        except RuntimeError:
            # Laço morrendo no meio de um restart: cai para o modo bloqueante.
            pass
        else:
            _inflight.add(fut)
            fut.add_done_callback(_inflight.discard)
            return

    try:
        asyncio.run(write_now(contact_id, action_id))
    except Exception as e:  # noqa: BLE001 — a entrega falha, o registro fica
        _record_error(contact_id, action_id,
                      f"falha ao gravar no Trackify: {type(e).__name__}")
        logger.warning("trackify: gravação síncrona do descadastro falhou: %s", e)


def _record_state(contact_id: int, action_id: str, cand: dict, channel_id: str,
                  msg_id: str) -> None:
    agora = _now()
    with make_plugin_db() as conn:
        conn.execute(text(
            "INSERT INTO plugin_trackify_consent_state "
            "(contact_id, action, channel_id, raw_payload, raw_text, msg_id, "
            " clicked_at, clicks, created_at, updated_at) "
            "VALUES (:cid, :a, :ch, :p, :t, :m, :now, 1, :now, :now) "
            "ON CONFLICT (contact_id, action) DO UPDATE SET "
            " channel_id = EXCLUDED.channel_id, "
            " raw_payload = EXCLUDED.raw_payload, raw_text = EXCLUDED.raw_text, "
            " msg_id = EXCLUDED.msg_id, clicked_at = EXCLUDED.clicked_at, "
            " clicks = plugin_trackify_consent_state.clicks + 1, "
            " updated_at = EXCLUDED.updated_at"),
            {"cid": contact_id, "a": action_id, "ch": channel_id,
             "p": cand["payload"][:300], "t": cand["text"][:300],
             "m": msg_id[:120], "now": agora})


def get_state(contact_id: int, action_id: str) -> dict | None:
    """O estado de UMA ação daquele contato.

    ``action_id`` não tem default de propósito: o caminho de escrita sempre sabe
    de qual ação está falando, e um default esconderia justamente o eixo novo.
    """
    with make_plugin_db() as conn:
        row = conn.execute(text(
            "SELECT * FROM plugin_trackify_consent_state "
            "WHERE contact_id = :c AND action = :a"),
            {"c": contact_id, "a": action_id}).mappings().first()
    return dict(row) if row else None


# ── Entrega ──────────────────────────────────────────────────────────────

async def write_now(contact_id: int, action_id: str) -> str:
    """Executa a ação no CDP. Devolve um rótulo do desfecho.

    ⚠️ **Não há rede de segurança depois daqui.** Esgotadas as tentativas, o
    pedido fica só como ``last_error`` no estado — ninguém tenta de novo. Foi a
    escolha feita ao remover a fila; se um dia incomodar, a volta do outbox é
    aditiva e este é o único ponto que muda.
    """
    import httpx

    erro = ""
    # Um client para a sequência inteira: as tentativas são do mesmo pedido, e
    # abrir conexão a cada uma só somaria latência ao retry.
    async with httpx.AsyncClient(timeout=tk_client.WRITE_TIMEOUT) as http:
        for tentativa in range(MAX_ATTEMPTS):
            desfecho, erro, repetir = await _write_once(http, contact_id, action_id)
            if not repetir:
                return desfecho
            if tentativa < len(RETRY_DELAYS):
                await asyncio.sleep(RETRY_DELAYS[tentativa])

    await asyncio.to_thread(_record_error, contact_id, action_id, erro)
    logger.warning("trackify: ação %r do contato %s NÃO foi gravada no "
                   "Trackify após %s tentativas: %s",
                   action_id, contact_id, MAX_ATTEMPTS, erro)
    audit("trackify", "consent.write_failed",
          after={"contact_id": contact_id, "action": action_id,
                 "error": erro[:200]})
    return "failed"


async def _write_once(client, contact_id: int, action_id: str) -> tuple[str, str, bool]:
    """Uma tentativa. ``(desfecho, erro, vale_repetir)``."""
    estado = await asyncio.to_thread(get_state, contact_id, action_id)
    if not estado:
        return "skipped", "sem clique registrado", False

    # Também cobre a linha órfã: uma ação que existia numa versão anterior e foi
    # removida do registro não tem mais o que gravar.
    acao = actions.get(action_id)
    if acao is None:
        return "skipped", f"ação '{action_id}' não existe mais", False

    from db.repositories import contact_repo
    contact = await asyncio.to_thread(contact_repo.get, contact_id)
    if not contact:
        return "skipped", "contato não existe mais", False
    if contact.get("is_group"):
        return "skipped", "grupo não tem cadastro no CDP", False

    if not tk_client.is_configured():
        motivo = (_config.setting("sync_blocked_reason")
                  or "API key do Trackify não configurada")
        await asyncio.to_thread(_record_error, contact_id, action_id, motivo)
        return "no_credential", motivo, False

    # Reuso deliberado: a resolução de identidade, o cache, a regra de "só grava
    # com acerto ÚNICO" e o "nunca cria contato no CDP" já vivem lá. Uma segunda
    # cópia divergiria e passaria a colar consentimento no cadastro errado.
    try:
        tk_id = await push._linked_id(client, str(contact.get("phone") or ""), contact)
    except Exception as e:  # noqa: BLE001
        return "read_failed", f"resolução de identidade falhou: {type(e).__name__}", True

    if not tk_id:
        # Sem vínculo não há o que gravar, e tentar de novo daqui a 8s não vai
        # criar um. Fica registrado para o operador vincular ou marcar à mão.
        motivo = ("contato não vinculado a um cadastro no Trackify — vincule ou "
                  "marque o descadastro à mão no CDP")
        await asyncio.to_thread(_record_error, contact_id, action_id, motivo)
        return "unlinked", motivo, False

    # O que a ação grava. `None` = configuração pela metade (campo ou valor em
    # branco): registrar o motivo é melhor que um PUT que apagaria o campo.
    plano = acao.plan()
    if plano is None:
        motivo = "; ".join(acao.errors()) or "configuração da ação incompleta"
        await asyncio.to_thread(_record_error, contact_id, action_id, motivo)
        return "misconfigured", motivo, False

    # Este MESMO clique já foi gravado: reentrega do webhook ou retentativa
    # depois de um sucesso. Zero HTTP é o que mantém o orçamento de 30/min por
    # IP do Trackify utilizável.
    #
    # ⚠️ A comparação é pelo ``msg_id``, não pelo valor. Comparar valores
    # tratava o registro local como prova do estado remoto: apagado o campo no
    # CDP (correção manual, merge, outro sistema), o contato clicava e o plugin
    # pulava a escrita para sempre. Sem ``msg_id`` (provider que não manda um)
    # a guarda não trava — escrever de novo é o lado seguro do erro.
    clique = str(estado.get("msg_id") or "")
    if (clique
            and str(estado.get("written_msg_id") or "") == clique
            and str(estado.get("trackify_contact_id") or "") == tk_id):
        return "noop", "", False

    if bool(_config.setting("consent_dry_run", True)):
        await asyncio.to_thread(
            _record_error, contact_id, action_id,
            f"[modo seco] gravaria {plano.describe} no contato {tk_id}")
        return "dry_run", "", False

    res = await writer.put_contact(client, tk_id, plano.field_values)
    return await asyncio.to_thread(_apply_result, contact_id, action_id, tk_id,
                                   plano.field_values, res, clique)


def _apply_result(contact_id: int, action_id: str, tk_id: str, valores: dict, res,
                  clique: str = "") -> tuple[str, str, bool]:
    if res.verdict == writer.OK:
        _record_written(contact_id, action_id, tk_id, valores, clique)
        return "sent", "", False

    if res.verdict == writer.UNLINKED:
        # Merge no CDP apagou a grafia deste contato: esquece o vínculo para a
        # próxima tentativa resolver do zero. Identidade é do CONTATO, não da
        # ação — por isso este esquecimento não é namespaceado.
        push._forget_identity(contact_id)
        _record_error(contact_id, action_id, res.error)
        return "unlinked", res.error, True

    if res.verdict == writer.UNAUTHORIZED:
        # A credencial é UMA só para as duas sincronizações: parar as duas é o
        # comportamento certo, e a tela mostra o motivo em vez de martelar o
        # Nexus do cliente. Repetir não resolveria — chave inválida/sem escopo
        # não conserta sozinha.
        push._block_sync(res.error)
        _record_error(contact_id, action_id, res.error)
        return "unauthorized", res.error, False

    if res.verdict in (writer.BLOCKED, writer.CONFLICT):
        _record_error(contact_id, action_id, res.error)
        return "blocked", res.error, False

    if res.verdict == writer.THROTTLED:
        push.note_cooldown(res.retry_after or 5.0)
        return "throttled", res.error, True

    return "retry", res.error, True


def _written_value(valores: dict) -> str:
    """O que vai para a coluna ``written_value``.

    Um campo só grava o VALOR (é o que a tela e os testes leem); mais de um
    grava o JSON. A coluna é TEXT desde a 003 e continua servindo aos dois —
    ``written_values JSONB`` é a saída limpa quando existir uma ação multi-campo
    de verdade.
    """
    if len(valores) == 1:
        return str(next(iter(valores.values())))
    import json
    return json.dumps(valores, ensure_ascii=False, sort_keys=True)


def _record_written(contact_id: int, action_id: str, tk_id: str, valores: dict,
                    msg_id: str) -> None:
    """Carimba a entrega — inclusive QUAL clique ela atendeu.

    O ``msg_id`` vem de fora, lido no INÍCIO da tentativa, e não da linha: um
    clique novo chegando no meio da entrega reescreve o ``msg_id`` do estado, e
    copiá-lo aqui marcaria como entregue um clique que ninguém gravou.
    """
    agora = _now()
    with make_plugin_db() as conn:
        conn.execute(text(
            "UPDATE plugin_trackify_consent_state SET written_value = :v, "
            " written_at = :now, trackify_contact_id = :tk, last_error = '', "
            " written_msg_id = :mid, "
            " last_attempt_at = :now, updated_at = :now "
            "WHERE contact_id = :c AND action = :a"),
            {"c": contact_id, "a": action_id, "v": _written_value(valores),
             "tk": tk_id, "now": agora, "mid": msg_id})


def _record_error(contact_id: int, action_id: str, erro: str) -> None:
    agora = _now()
    try:
        with make_plugin_db() as conn:
            conn.execute(text(
                "UPDATE plugin_trackify_consent_state SET last_error = :e, "
                " last_attempt_at = :now, updated_at = :now "
                "WHERE contact_id = :c AND action = :a"),
                {"c": contact_id, "a": action_id, "e": (erro or "")[:500],
                 "now": agora})
    except Exception:  # noqa: BLE001 — registrar o erro não pode virar outro
        logger.debug("trackify: falha ao registrar erro de descadastro",
                     exc_info=True)


def pending_errors(limit: int = 20) -> list[dict]:
    """Cliques que não chegaram ao CDP. Alimenta o aviso da tela.

    Devolve o ``action`` junto: o agrupamento por ação é feito em Python pelo
    :func:`status`, para a tela continuar custando UMA consulta.
    """
    try:
        with make_plugin_db() as conn:
            rows = conn.execute(text(
                "SELECT contact_id, action, last_error, last_attempt_at, clicked_at "
                "FROM plugin_trackify_consent_state "
                "WHERE COALESCE(last_error, '') <> '' AND written_at IS NULL "
                "ORDER BY updated_at DESC LIMIT :lim"),
                {"lim": max(1, min(int(limit or 20), 100))}).mappings().all()
    except Exception:  # noqa: BLE001
        return []
    return [dict(r) for r in rows]


# ── Status para a tela ───────────────────────────────────────────────────

def _mapped_slugs() -> set:
    """Slugs mapeados na aba 'Campos do contato'. UMA consulta para N ações."""
    try:
        return {str(m.get("tk_slug") or "") for m in field_map.list_maps()}
    except Exception:  # noqa: BLE001
        return set()


def _field_view(slug: str, achado: dict) -> dict:
    return {
        "slug": slug,
        "name": achado.get("name") or "",
        "is_active": bool(achado.get("isActive", achado.get("is_active", True))),
        "is_identifier": bool(achado.get("isIdentifier",
                                         achado.get("is_identifier", False))),
        "field_type": ((achado.get("fieldType") or {}).get("name")
                       if isinstance(achado.get("fieldType"), dict)
                       else achado.get("fieldType") or achado.get("field_type") or ""),
        "regex_pattern": achado.get("regexPattern") or achado.get("regex_pattern") or "",
    }


async def status(client) -> dict:
    """Diagnóstico acionável: o que falta para isto funcionar.

    Distingue os estados de propósito — *desligado*, *sem credencial*, *sem
    código*, *código em conflito*, *sem canal oficial* e *campo errado no CDP*
    falham todos do mesmo jeito (nada acontece) e exigem consertos diferentes.

    Uma linha por ação registrada, e **nenhum round-trip extra por ação**: o
    catálogo de ``custom-fields`` já é buscado (e cacheado) uma vez, e a consulta
    por ação é ``dict.get`` em memória.
    """
    snap = actions.snapshot()

    erros = await asyncio.to_thread(pending_errors)
    por_acao: dict = {}
    for linha in erros:
        por_acao.setdefault(str(linha.get("action") or ""), []).append(linha)

    mapeados = await asyncio.to_thread(_mapped_slugs)

    out = {
        "enabled": snap.enabled,
        "dry_run": bool(_config.setting("consent_dry_run", True)),
        "credential_set": snap.credential_set,
        "blocked_reason": (_config.setting("sync_blocked_reason") or "").strip(),
        "channels": await asyncio.to_thread(official_channels),
        # Código reivindicado por duas ações: não vale para nenhuma.
        "code_conflicts": [{"code": c, "actions": ids}
                           for c, ids in sorted(snap.conflicts.items())],
        # Erro de LEITURA do catálogo do CDP — vale para todas as linhas.
        "field_error": "",
        "actions": [],
    }

    por_slug: dict = {}
    if not snap.credential_set:
        out["field_error"] = "API key do Trackify não configurada."
    else:
        res = await tk_client.cached("custom-fields",
                                     lambda: tk_client.custom_fields(client))
        if not res.ok:
            out["field_error"] = (res.error
                                  or "não foi possível ler os campos do Trackify")
        else:
            linhas = (res.data if isinstance(res.data, list)
                      else (res.data or {}).get("data") or [])
            por_slug = {str(f.get("slug") or ""): f for f in linhas}

    catalogo_lido = bool(por_slug) or (snap.credential_set and not out["field_error"])

    for acao in actions.registered():
        slugs = list(acao.slugs())
        linha = {
            "id": acao.id,
            "label": acao.label,
            "description": acao.description,
            "codes_setting": acao.codes_setting,
            "codes_label": acao.codes_label,
            "codes_help": acao.codes_help,
            "codes_placeholder": acao.codes_placeholder,
            "codes": snap.codes.get(acao.id, []),
            # Os que de fato entraram no índice. A tela NÃO recalcula isso: a
            # normalização (NFKD + casefold + colapso de espaço) é do Python, e
            # uma segunda implementação em JS divergiria justamente nos casos
            # que o operador não vê (acento, emoji, espaço duplo).
            "codes_active": [c for c in snap.codes.get(acao.id, [])
                             if consent_match.normalize(c) in snap.index],
            # Descritores dos campos que a linha renderiza. O VALOR vem do
            # objeto de settings do cliente — este payload só descreve.
            "settings_fields": [
                {"key": f.key, "label": f.label, "help": f.help,
                 "placeholder": f.placeholder}
                for f in acao.settings_fields
            ],
            "code_conflicts": sorted(c for c, ids in snap.conflicts.items()
                                     if acao.id in ids),
            "config_errors": acao.errors(),
            # Guarda cruzada com a aba "Campos do contato": o mesmo slug mapeado
            # dos dois lados ligaria o ``pull``, que traria o valor antigo do CDP
            # e poderia DESFAZER o que esta ação grava.
            "field_map_conflict": any(s in mapeados for s in slugs),
            "targets": [],
            "field": None,
            "field_error": "",
        }

        try:
            plano = acao.plan()
        except Exception:  # noqa: BLE001
            plano = None
        if plano:
            linha["targets"] = [{"slug": s, "value": str(v)}
                                for s, v in plano.field_values.items()]

        for slug in slugs:
            if not catalogo_lido:
                break
            achado = por_slug.get(slug)
            if achado is None:
                linha["field_error"] = (
                    f"O campo '{slug}' não existe no Trackify. Enquanto ele não "
                    "existir, o Campanhas também não consegue ler o descadastro — "
                    "os dois lados precisam apontar para o mesmo slug.")
                break
            vista = _field_view(slug, achado)
            if linha["field"] is None:
                linha["field"] = vista
            if not vista["is_active"]:
                linha["field_error"] = (
                    f"O campo '{slug}' está desativado no Trackify: o PUT devolve "
                    "200 e não grava nada.")
                break

        out["actions"].append(linha)

    return out
