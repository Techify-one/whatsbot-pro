"""A API interna do plugin — o único portão para o CDP.

Publicada pelo seam ``plugins.services`` (in-process, NUNCA HTTP). Qualquer
outro plugin do WhatsBot que precise LER do Trackify (jornada, compras,
identidade) ou ESCREVER sob demanda passa por aqui — e não fala com o CDP nem
com o Postgres dele.

⚠️ **Este módulo é FOLHA de propósito**: nenhum outro módulo do trackify o
importa. É o que mantém o plugin carregando num core anterior, que não conhece
``entry.services`` e portanto nunca importa este arquivo. Helper compartilhado
vai para ``identity``/``status``/``mirror``, jamais para cá.

Contrato do payload (travado por teste)
---------------------------------------
* Só JSON-serializável atravessa. **Nenhum tipo interno vaza** — ``client.Result``,
  ``identity.Match``/``Resolution``, ``push.Plan``, ``actions.Plan`` ficam dentro.
* ``contact_id`` é o int do WhatsBot; ``trackify_contact_id`` é o uuid do CDP.
* Timestamps são epoch float.
* Falha de DOMÍNIO devolve ``{"ok": False, "reason": …}``; nunca levanta.
* Desligado / sem credencial levanta :class:`ServiceDisabled` ⇒ envelope
  ``DISABLED`` do seam.
* **CDP fora do ar NÃO é ``DISABLED``**: o envelope fica ``ok`` e o dado carrega
  ``{"ok": False, "verdict": "retry"}``. Os vereditos de ``client.py``
  (``ok/conflict/unlinked/blocked/throttled/unauthorized/retry``) FAZEM PARTE do
  contrato — já são o vocabulário publicado do plugin (``writer.py``).

Versionamento: acrescentar op ⇒ MINOR. Remover/renomear op, ou mudar o
significado de uma chave, ⇒ MAJOR.

Ciclo de vida do httpx: um ``AsyncClient`` por chamada, igual a ``routes._http``
— rotas/ops de plugin são esporádicas e um cliente global é algo que o supervisor
não consegue fechar no disable.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from plugins.context import audit

from . import _config, consent, dispatcher, enroll, field_map, identity, journey
from . import mirror, push, status, writer
from . import client as tk_client

logger = logging.getLogger("plugins.trackify.services")

SERVICES_VERSION = "1.0.0"

try:  # core >= 1.1
    from plugins.services import ServiceDisabled
except Exception:  # noqa: BLE001 — core anterior: o registro casa por NOME de classe
    class ServiceDisabled(RuntimeError):  # type: ignore[no-redef]
        pass


# ── Utilidades ───────────────────────────────────────────────────────────

def _http():
    """Client HTTP por chamada (ver a nota de ciclo de vida no topo)."""
    import httpx
    return httpx.AsyncClient(timeout=tk_client.READ_TIMEOUT)


def _fail(reason: str, **extra) -> dict:
    return {"ok": False, "reason": reason, **extra}


def _need_mirror() -> None:
    if not bool(_config.setting("mirror_enabled", False)):
        raise ServiceDisabled("espelho de eventos desligado (mirror_enabled=False)")


def _need_credential() -> None:
    if not _config.credential_set():
        raise ServiceDisabled("API key do Trackify não configurada")


def _contact(contact_id=None, phone: str = "") -> dict | None:
    """Contato do WhatsBot por id ou telefone — nesta ordem."""
    return (mirror._contact_by_id(contact_id)
            or mirror._contact_by_phone(str(phone or "")))


# ── track_event: o kind aberto a outros plugins ──────────────────────────
#
# Reservamos os kinds internos: um chamador reusando ``protocolo_closed``
# colidiria no dedup do CDP com o evento que o próprio trackify produz.
_KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
RESERVED_KINDS = frozenset(
    v for k, v in vars(mirror).items() if k.startswith("KIND_") and isinstance(v, str))


def track_event(kind: str, *, contact_id=None, phone: str = "", data: dict | None = None,
                occurred_at: float | None = None, external_key: str = "",
                title: str = "") -> dict:
    """Enfileira UM evento genérico na timeline do contato no CDP.

    Existe para OUTROS plugins. Não é porta dos fundos: nada neste plano a usa
    para clique em botão — o único clique que vira evento é o descadastro, e ele
    entra por ``consent.py`` (§3.4 do plano), dentro do ramo já casado.
    """
    _need_mirror()
    slug = str(kind or "").strip().lower()
    if not _KIND_RE.match(slug):
        return _fail("kind inválido (use ^[a-z][a-z0-9_]{0,63}$)")
    if slug in RESERVED_KINDS:
        return _fail(f"kind {slug!r} é reservado pelo trackify")

    c = _contact(contact_id, phone)
    if not c:
        return _fail("contato não encontrado no WhatsBot")
    ok, why = mirror.eligible(c)
    if not ok:
        return _fail(why)

    ts = float(occurred_at or time.time())
    chave = str(external_key or "").strip() or f"{c.get('id')}.{int(ts)}"
    queued = mirror.enqueue(
        slug, contact=c, external_id=mirror.make_external_id(f"ext.{slug}.{chave}"),
        occurred_at=ts, data=dict(data or {}), title=str(title or ""))
    return {"ok": True, "queued": bool(queued), "kind": slug}


# ── Protocolos: as ops SEMÂNTICAS ────────────────────────────────────────
#
# Reusam os MESMOS handlers do barramento. É isso que mantém a tradução para o
# vocabulário do CDP (kind, external_id, achatamento ``campo_<k>``, TITLES) 100%
# dentro do trackify: o plugin `protocolos` manda exatamente o dict que já
# emitia e não aprende nada sobre o CDP.

def track_protocolo_opened(**payload) -> dict:
    _need_mirror()
    return {"ok": True, "queued": bool(mirror.on_protocolo_opened(None, payload))}


def track_protocolo_closed(**payload) -> dict:
    _need_mirror()
    return {"ok": True, "queued": bool(mirror.on_protocolo_closed(None, payload))}


def track_protocolo_rated(**payload) -> dict:
    _need_mirror()
    return {"ok": True, "queued": bool(mirror.on_protocolo_rated(None, payload))}


def track_protocolo_fields_updated(**payload) -> dict:
    _need_mirror()
    return {"ok": True, "queued": bool(mirror.on_protocolo_fields(None, payload))}


# ── Saúde ────────────────────────────────────────────────────────────────

async def op_status() -> dict:
    """Saúde do plugin + contadores das duas filas.

    NUNCA levanta ``ServiceDisabled``: reportar "não configurado" é justamente o
    trabalho dela.
    """
    async with _http() as http:
        data = await status.build(http)
    data["outbox"] = await asyncio.to_thread(dispatcher.stats)
    data["field_sync"] = await asyncio.to_thread(push.stats)
    data["services_version"] = SERVICES_VERSION
    return data


# ── Identidade / leitura ─────────────────────────────────────────────────

async def resolve_identity(contact_id=None, phone: str = "", email: str = "",
                           cpf: str = "", contact_type: str = "whatsapp") -> dict:
    """Quem é este contato no CDP. ``{found, trackify_contact_id, matched_by, link}``."""
    _need_credential()
    if contact_id is not None:
        scope = await asyncio.to_thread(identity.contact_scope, int(contact_id))
        if scope is None:
            return _fail("contato não encontrado no WhatsBot")
        if scope["is_group"]:
            return _fail("conversa de grupo")
        phone = phone or scope["phone"]
        email = email or scope["email"]
        contact_type = scope["contact_type"]
        extras = dict(scope["hints"])
    else:
        extras = {}
    if email:
        extras.setdefault(identity.SLUG_EMAIL, email)
    if cpf:
        extras.setdefault(identity.SLUG_CPF, cpf)

    async with _http() as http:
        resolution = await identity.resolve_mapped_ex(
            http, phone=phone or None, extras=extras, contact_type=contact_type)
    if not resolution.ok:
        return _fail(resolution.error or "leitura recusada", verdict=tk_client.RETRY)
    match, motivo = identity.pick_unique(resolution.matches)
    if match is None:
        return {"ok": True, "found": False, "reason": motivo,
                "candidates": [m.contact_id for m in resolution.matches]}
    return {
        "ok": True, "found": True,
        "trackify_contact_id": match.contact_id,
        "matched_by": match.slug,
        "matched_value": match.exact_value,
        "link": _config.contact_link(match.contact_id),
    }


async def get_journey(contact_id: int) -> dict:
    """Os 3 blocos da jornada — a MESMA resolução da rota ``GET /journey``."""
    _need_credential()
    scope = await asyncio.to_thread(identity.contact_scope, int(contact_id))
    if scope is None:
        return _fail("contato não encontrado no WhatsBot")
    if scope["is_group"]:
        return {"ok": True, "found": False, "is_group": True, "candidates": []}
    async with _http() as http:
        data = await journey.journey_for(
            http, phone=scope["phone"], email=scope["email"] or None,
            extras=scope["hints"], contact_type=scope["contact_type"])
    data["ok"] = True
    data["whatsbot"] = {"contact_id": int(contact_id), "name": scope["name"]}
    return data


async def get_events(trackify_contact_id: str, limit: int | None = None,
                     offset: int = 0, event_type: str | None = None) -> dict:
    _need_credential()
    async with _http() as http:
        data = await journey.fetch_timeline(
            http, str(trackify_contact_id), limit=limit, offset=offset,
            event_type=event_type)
    data["ok"] = True
    return data


async def get_purchases(trackify_contact_id: str) -> dict:
    _need_credential()
    async with _http() as http:
        data = await journey.fetch_purchases(http, str(trackify_contact_id))
    data["ok"] = True
    return data


async def get_subscriptions(trackify_contact_id: str) -> dict:
    _need_credential()
    async with _http() as http:
        rows = await journey.fetch_subscriptions(http, str(trackify_contact_id))
    return {"ok": True, "subscriptions": rows}


async def get_contact(trackify_contact_id: str) -> dict:
    """A ficha NORMALIZADA (``journey.fetch_identity``), não o payload cru do CDP."""
    _need_credential()
    async with _http() as http:
        data = await journey.fetch_identity(http, str(trackify_contact_id))
    if data is None:
        return _fail("cadastro não encontrado no Trackify", verdict=tk_client.UNLINKED)
    return {"ok": True, "contact": data}


async def list_custom_fields(refresh: bool = False) -> dict:
    _need_credential()
    async with _http() as http:
        data = await field_map.tk_fields(http, bool(refresh))
    data["ok"] = True
    return data


# ── Escrita ──────────────────────────────────────────────────────────────

async def put_contact_fields(contact_id=None, trackify_contact_id: str = "",
                             values: dict | None = None, reason: str = "") -> dict:
    """Grava ``{slug: valor}`` na ficha do CDP. ``""`` APAGA o campo.

    Auditada: é escrita com efeito externo. Só nomes de campo entram na trilha —
    nunca valor de contato.
    """
    _need_credential()
    vals = {str(k): v for k, v in (values or {}).items()}
    if not vals:
        return _fail("nenhum campo a gravar")

    espera = push.cooldown_remaining()
    if espera > 0:
        return _fail("limite de escrita do Trackify (429) ainda em vigor",
                     verdict=writer.THROTTLED, retry_after=espera)

    tk_id = str(trackify_contact_id or "").strip()
    async with _http() as http:
        if not tk_id:
            c = _contact(contact_id)
            if not c:
                return _fail("contato não encontrado no WhatsBot")
            tk_id, motivo = await push.linked_id_ex(http, str(c.get("phone") or ""), c)
            if not tk_id:
                return _fail(motivo or "contato não vinculado a um cadastro no Trackify")
        res = await writer.put_contact(http, tk_id, vals)

    if res.verdict == writer.THROTTLED:
        push.note_cooldown(res.retry_after)
    audit("trackify", "contact.fields_written", actor_type="system",
          resource_id=tk_id,
          after={"campos": sorted(vals), "verdict": res.verdict,
                 "motivo": str(reason or "")[:120]})
    return {
        "ok": res.ok,
        "verdict": res.verdict,
        "trackify_contact_id": tk_id,
        "error": res.error,
        "written": sorted(res.landed or {}),
        "dropped": list(res.dropped or []),
    }


def enqueue_field_sync(contact_id: int, reason: str = "") -> dict:
    """Agenda a sincronização de campos daquele contato (quem entrega é o worker)."""
    push.enqueue(int(contact_id), str(reason or "servico"))
    return {"ok": True, "queued": True, "contact_id": int(contact_id)}


async def ensure_cdp_contact(contact_id: int) -> dict:
    """O vínculo do contato no CDP, cadastrando se ainda não houver um."""
    if not enroll.ready():
        raise ServiceDisabled("cadastro automático desligado ou sem credencial")
    c = _contact(contact_id)
    if not c:
        return _fail("contato não encontrado no WhatsBot")
    async with _http() as http:
        tk_id, motivo = await enroll.ensure(http, c)
    if not tk_id:
        return _fail(motivo or "não foi possível vincular o contato")
    return {"ok": True, "trackify_contact_id": tk_id,
            "link": _config.contact_link(tk_id)}


# ── Consentimento ────────────────────────────────────────────────────────

async def consent_status(contact_id: int | None = None, action: str = "") -> dict:
    """Diagnóstico da aba de consentimento; com ``contact_id`` + ``action``,
    também o estado gravado daquele contato."""
    async with _http() as http:
        data = await consent.status(http)
    data["ok"] = True
    if contact_id is not None and action:
        estado = await asyncio.to_thread(
            consent.get_state, int(contact_id), str(action))
        data["contact_state"] = dict(estado) if estado else None
    return data


async def apply_consent_action(contact_id: int, action: str) -> dict:
    """Executa a ação de consentimento no CDP (o mesmo caminho do retry). Auditada."""
    _need_credential()
    desfecho = await consent.write_now(int(contact_id), str(action))
    audit("trackify", "consent.applied", actor_type="system",
          resource_id=str(contact_id),
          after={"action": str(action), "outcome": desfecho})
    return {"ok": desfecho in ("written", "skipped"), "outcome": desfecho}


# ── A superfície publicada ───────────────────────────────────────────────

SERVICES = {
    "status": op_status,
    "track_event": track_event,
    "track_protocolo_opened": track_protocolo_opened,
    "track_protocolo_closed": track_protocolo_closed,
    "track_protocolo_rated": track_protocolo_rated,
    "track_protocolo_fields_updated": track_protocolo_fields_updated,
    "resolve_identity": resolve_identity,
    "get_journey": get_journey,
    "get_events": get_events,
    "get_purchases": get_purchases,
    "get_subscriptions": get_subscriptions,
    "get_contact": get_contact,
    "list_custom_fields": list_custom_fields,
    "put_contact_fields": put_contact_fields,
    "enqueue_field_sync": enqueue_field_sync,
    "ensure_cdp_contact": ensure_cdp_contact,
    "consent_status": consent_status,
    "apply_consent_action": apply_consent_action,
}
