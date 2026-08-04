"""A jornada do contato no CDP: identidade, assinaturas e linha do tempo.

Leitura pura. Cada bloco é uma consulta isolada para que uma falhar não leve as
outras junto — o vendedor prefere ver a identidade sem a timeline a ver um erro.

Armadilhas de dados MEDIDAS em produção (não descobrir na frente do cliente):

* ``next_charge_date`` é **string ``dd/mm/yyyy``**, não ``date``.
* ``subscription_canceled_at`` às vezes contém a palavra ``"system"``.
* Nem todo evento tem campo dinâmico → ``jsonb_object_agg`` precisa do ``FILTER``,
  senão o Postgres levanta ``field name must not be null``.
* ``events.value`` é ``Decimal`` **nullable**.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal

from . import _config, identity, trackify_db

logger = logging.getLogger("plugins.trackify.journey")

# Tipos de evento que descrevem o ciclo de uma assinatura.
_SUBSCRIPTION_EVENTS = (
    "active_subscription", "subscription_canceled", "subscription_delayed",
    "purchase", "refunded", "chargeback",
)
_SUBSCRIPTION_SCAN_LIMIT = 500

# Eventos que dizem alguma coisa sobre POSSE de produto. Lista fixa, e não a
# tabela ``channel_value_rules`` do CDP (que seria o oráculo "isto é receita?"),
# porque aquela é POR CANAL e canal sem regra cai em "ignorar" — uma compra
# legítima sumiria do painel em silêncio. Aqui errar para mais é inofensivo:
# a tela é de leitura.
_PRODUCT_EVENTS = (
    "purchase", "authorized", "active_subscription", "charge.paid", "order.paid",
    "subscription_canceled", "subscription_delayed",
    "refunded", "chargeback", "charge.refunded", "order.canceled",
)
# O evento MAIS RECENTE do produto decide se o cliente ainda o tem.
_PRODUCT_LOST = {"subscription_canceled", "refunded", "chargeback",
                 "charge.refunded", "order.canceled"}
_PRODUCT_PAID = {"purchase", "authorized", "active_subscription",
                 "charge.paid", "order.paid"}
_PRODUCT_SCAN_LIMIT = 500

# Campo dinâmico do evento → chave do bloco de assinatura.
_SUB_FIELD_MAP = {
    "product_name": "product",
    "offer_name": "offer",
    "subscription_interval": "interval",
    "payment_method": "payment_method",
    "successful_charges": "successful_charges",
    "failed_charges": "failed_charges",
}


# ── Formatação e parse tolerante ─────────────────────────────────────────

def _money(value) -> str | None:
    """``Decimal``/número → ``"R$ 1.234,56"``. ``None`` quando não há valor."""
    if value is None:
        return None
    try:
        d = Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None
    s = f"{d:,.2f}"                                   # 1,234.56
    s = s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"R$ {s}"


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _parse_loose_date(raw) -> date | None:
    """Aceita ``dd/mm/yyyy``, ISO e ``datetime``. Qualquer outra coisa → ``None``.

    É aqui que ``"system"`` morre: não casa formato nenhum, então o campo é
    tratado como "sem data" em vez de virar exceção ou texto de data falso.
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    s = str(raw).strip()
    if not s:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None


# ── Bloco 1 — identidade ─────────────────────────────────────────────────

_SQL_CONTACT = """
SELECT c.id::text AS id, c.status::text AS status, c.total_spent,
       c.first_seen_at, c.converted_at, c.created_at
FROM contacts c
WHERE c.id = CAST(:cid AS uuid) AND c.deleted_at IS NULL
"""

# Parte de ``custom_fields`` e não de ``contact_field_values``, de propósito: a
# aba "Informações do Contato" do Trackify mostra TODO campo ativo, com o valor
# em branco quando o contato não tem aquele dado. Um campo ausente da lista é
# indistinguível de um campo vazio, e "sem CPF cadastrado" é justamente o que o
# atendente precisa enxergar.
_SQL_FIELDS = """
SELECT cf.slug, cf.name, cf.is_identifier, cfv.value
FROM custom_fields cf
LEFT JOIN contact_field_values cfv
       ON cfv.custom_field_id = cf.id AND cfv.contact_id = CAST(:cid AS uuid)
WHERE cf.deleted_at IS NULL AND cf.is_active
ORDER BY cf.is_identifier DESC, cf.identifier_priority NULLS LAST, cf.name
"""

_SQL_TAGS = """
SELECT t.name, t.color
FROM contact_tags ct
JOIN tags t ON t.id = ct.tag_id
WHERE ct.contact_id = CAST(:cid AS uuid)
ORDER BY t.name
"""


def fetch_identity(contact_id: str) -> dict | None:
    """Bloco 1. ``None`` quando o contato não existe (ou foi soft-deletado)."""
    rows = trackify_db.run_read(_SQL_CONTACT, {"cid": contact_id})
    if not rows:
        return None
    c = rows[0]
    fields = trackify_db.run_read(_SQL_FIELDS, {"cid": contact_id})
    tags = trackify_db.run_read(_SQL_TAGS, {"cid": contact_id})

    by_slug = {f["slug"]: f["value"] for f in fields if f.get("value")}
    return {
        "contact_id": c["id"],
        "status": c["status"],
        "total_spent": _money(c["total_spent"]),
        "total_spent_raw": float(c["total_spent"] or 0),
        "first_seen_at": _iso(c["first_seen_at"]),
        "converted_at": _iso(c["converted_at"]),
        "name": by_slug.get("name") or "",
        # Sem filtrar por valor preenchido: campo em branco é informação.
        "identifiers": [
            {"slug": f["slug"], "name": f["name"], "value": f["value"] or ""}
            for f in fields if f.get("is_identifier")
        ],
        "fields": [
            {"slug": f["slug"], "name": f["name"], "value": f["value"] or ""}
            for f in fields if not f.get("is_identifier")
        ],
        "tags": [{"name": t["name"], "color": t.get("color")} for t in tags],
        "link": _config.contact_link(c["id"]),
    }


# ── Bloco 3 — linha do tempo ─────────────────────────────────────────────
#
# ⚠️ O ``LIMIT`` tem que ser aplicado ANTES de juntar os campos dinâmicos.
# A versão "natural" (agregar tudo e limitar no fim) fazia seq scan em 179.733
# linhas de ``event_field_values`` — 24,3 ms por abertura, crescendo com a base.
# Paginando primeiro pelo ``idx_events_contact_time``: 0,918 ms (medido na F0).

_SQL_TIMELINE = """
WITH page AS (
  SELECT e.id, e.event_type, e.title, e.description, e.value, e.occurred_at, e.channel_id
  FROM events e
  WHERE e.contact_id = CAST(:cid AS uuid) AND e.deleted_at IS NULL
    AND (CAST(:event_type AS text) IS NULL OR e.event_type = :event_type)
  ORDER BY e.occurred_at DESC
  LIMIT :limit OFFSET :offset
)
SELECT p.id::text AS id, p.event_type, p.title, p.description, p.value, p.occurred_at,
       ch.slug AS channel,
       jsonb_object_agg(ecf.slug, efv.value) FILTER (WHERE ecf.slug IS NOT NULL) AS fields
FROM page p
JOIN channels ch ON ch.id = p.channel_id
LEFT JOIN event_field_values efv ON efv.event_id = p.id
LEFT JOIN event_custom_fields ecf ON ecf.id = efv.event_custom_field_id
GROUP BY p.id, p.event_type, p.title, p.description, p.value, p.occurred_at, ch.slug
ORDER BY p.occurred_at DESC
"""

_SQL_TIMELINE_COUNT = """
SELECT count(*)::int AS total
FROM events e
WHERE e.contact_id = CAST(:cid AS uuid) AND e.deleted_at IS NULL
  AND (CAST(:event_type AS text) IS NULL OR e.event_type = :event_type)
"""

_SQL_EVENT_TYPES = """
SELECT e.event_type, count(*)::int AS total
FROM events e
WHERE e.contact_id = CAST(:cid AS uuid) AND e.deleted_at IS NULL
GROUP BY e.event_type ORDER BY 2 DESC, 1
"""


def _event_row(r: dict) -> dict:
    return {
        "id": r["id"],
        "event_type": r["event_type"],
        "title": r["title"],
        "description": r.get("description"),
        "value": _money(r.get("value")),
        "occurred_at": _iso(r["occurred_at"]),
        "channel": r.get("channel"),
        "fields": r.get("fields") or {},
    }


def fetch_timeline(contact_id: str, *, limit: int | None = None, offset: int = 0,
                   event_type: str | None = None) -> dict:
    """Bloco 3, paginado. ``event_type`` filtra (o vendedor quer só compras/só falhas)."""
    lim = limit or _config.timeline_page_size()
    params = {"cid": contact_id, "limit": lim, "offset": max(0, offset),
              "event_type": event_type or None}
    rows = trackify_db.run_read(_SQL_TIMELINE, params)
    total = trackify_db.run_read(_SQL_TIMELINE_COUNT, params)
    return {
        "events": [_event_row(r) for r in rows],
        "total": (total[0]["total"] if total else 0),
        "limit": lim,
        "offset": max(0, offset),
    }


def fetch_event_types(contact_id: str) -> list[dict]:
    """Tipos de evento do contato + contagem — alimenta o filtro do modal."""
    return trackify_db.run_read(_SQL_EVENT_TYPES, {"cid": contact_id})


# ── Bloco 2 — assinaturas (derivado, sem consulta extra por assinatura) ──

_SQL_SUBSCRIPTION_EVENTS = """
SELECT e.id::text AS id, e.event_type, e.title, e.value, e.occurred_at,
       jsonb_object_agg(ecf.slug, efv.value) FILTER (WHERE ecf.slug IS NOT NULL) AS fields
FROM events e
LEFT JOIN event_field_values efv ON efv.event_id = e.id
LEFT JOIN event_custom_fields ecf ON ecf.id = efv.event_custom_field_id
WHERE e.contact_id = CAST(:cid AS uuid) AND e.deleted_at IS NULL
  AND e.event_type = ANY(:types)
GROUP BY e.id
ORDER BY e.occurred_at DESC
LIMIT :limit
"""


def fetch_subscriptions(contact_id: str, *, today: date | None = None) -> list[dict]:
    """Bloco 2 — agrupa os eventos de assinatura por ``subscription_id``.

    Sem ``subscription_id`` cai no ``product_name`` (é o que o CDP tem para os
    lançamentos antigos). "Dias restantes" sai de ``next_charge_date``, que é o
    dado que EXISTE — nunca inventamos uma data de expiração.
    """
    rows = trackify_db.run_read(_SQL_SUBSCRIPTION_EVENTS, {
        "cid": contact_id,
        "types": list(_SUBSCRIPTION_EVENTS),
        "limit": _SUBSCRIPTION_SCAN_LIMIT,
    })
    if not rows:
        return []

    today = today or date.today()
    groups: dict[str, dict] = {}

    for r in rows:
        f = r.get("fields") or {}
        key = (f.get("subscription_id") or f.get("product_name")
               or f.get("offer_name") or r["event_type"])
        g = groups.setdefault(key, {
            "key": key,
            "subscription_id": f.get("subscription_id"),
            "product": f.get("product_name") or f.get("offer_name"),
            "offer": f.get("offer_name"),
            "interval": f.get("subscription_interval"),
            "payment_method": f.get("payment_method"),
            "status": None,
            "next_charge": None,
            "next_charge_raw": None,
            "days_left": None,
            "canceled_at": None,
            "successful_charges": f.get("successful_charges"),
            "failed_charges": f.get("failed_charges"),
            "last_value": None,
            "last_event_at": None,
            "events": 0,
        })
        g["events"] += 1

        # O primeiro (mais recente) define o estado corrente do grupo.
        if g["last_event_at"] is None:
            g["last_event_at"] = _iso(r["occurred_at"])
            g["last_value"] = _money(r.get("value"))
            g["status"] = f.get("status") or r["event_type"]

        # Completa o que ainda estiver vazio com o evento mais recente que tiver
        # o campo — eventos antigos costumam trazer o que os novos omitem.
        for src, dst in _SUB_FIELD_MAP.items():
            if f.get(src) and not g.get(dst):
                g[dst] = f[src]

        # Próxima cobrança: string dd/mm/yyyy no CDP.
        if g["next_charge"] is None and f.get("next_charge_date"):
            raw = f["next_charge_date"]
            parsed = _parse_loose_date(raw)
            g["next_charge_raw"] = str(raw)
            if parsed:
                g["next_charge"] = parsed.isoformat()
                g["days_left"] = (parsed - today).days

        # ``subscription_canceled_at`` guarda "system" em algumas linhas — só
        # vira data quando REALMENTE casa um formato de data.
        if g["canceled_at"] is None and f.get("subscription_canceled_at"):
            parsed = _parse_loose_date(f["subscription_canceled_at"])
            if parsed:
                g["canceled_at"] = parsed.isoformat()

        if r["event_type"] == "subscription_canceled":
            g["canceled"] = True

    out = list(groups.values())
    out.sort(key=lambda g: (g.get("last_event_at") or ""), reverse=True)
    return out


# ── Fachada ──────────────────────────────────────────────────────────────

# ── Bloco 4: produtos que o cliente possui ───────────────────────────────

_SQL_PRODUCT_EVENTS = """
SELECT e.id::text AS id, e.event_type, e.title, e.value, e.occurred_at,
       jsonb_object_agg(ecf.slug, efv.value) FILTER (WHERE ecf.slug IS NOT NULL) AS fields
FROM events e
LEFT JOIN event_field_values efv ON efv.event_id = e.id
LEFT JOIN event_custom_fields ecf ON ecf.id = efv.event_custom_field_id
WHERE e.contact_id = CAST(:cid AS uuid) AND e.deleted_at IS NULL
  AND e.event_type = ANY(:types)
GROUP BY e.id
ORDER BY e.occurred_at DESC
LIMIT :limit
"""


def fetch_products(contact_id: str, *, today: date | None = None) -> list[dict]:
    """Bloco 4. Produtos que o contato possui (ou perdeu), agrupados.

    O Trackify **não tem tabela de produto**: posse é implícita em ``events`` +
    ``event_field_values``, e a arquitetura de lá é deliberadamente "um evento é
    um evento", sem tratamento especial por nome. Então a regra de posse mora
    aqui:

    * a chave do produto é ``subscription_id`` → ``product_name`` → ``offer_name``
      (a primeira que existir), porque o id da assinatura é o único estável entre
      renovações e lançamentos antigos não o trazem;
    * o evento MAIS RECENTE do grupo decide o estado — cancelamento, reembolso e
      chargeback tiram a posse, o resto mantém;
    * evento sem nome de produto NÃO vira produto. Sem essa guarda, cair no
      ``event_type`` como chave inventaria um produto chamado "purchase".
    """
    today = today or date.today()
    rows = trackify_db.run_read(_SQL_PRODUCT_EVENTS, {
        "cid": contact_id, "types": list(_PRODUCT_EVENTS),
        "limit": _PRODUCT_SCAN_LIMIT,
    })

    groups: dict[str, dict] = {}
    for r in rows:                      # já vem do mais novo para o mais antigo
        f = r.get("fields") or {}
        nome = f.get("product_name") or f.get("offer_name")
        if not nome:
            continue
        key = f.get("subscription_id") or nome
        g = groups.setdefault(key, {
            "key": key,
            "name": nome,
            "offer": f.get("offer_name"),
            "subscription_id": f.get("subscription_id"),
            "interval": f.get("subscription_interval"),
            "payment_method": f.get("payment_method"),
            "gateway_status": None,
            "last_event_type": None,
            "last_event_at": None,
            "last_value": None,
            "first_event_at": None,
            "active": True,
            "canceled_at": None,
            "next_charge": None,
            "next_charge_raw": None,
            "days_left": None,
            "paid_total_raw": 0.0,
            "paid_total": None,
            "events": 0,
        })
        g["events"] += 1

        if g["last_event_at"] is None:
            # O primeiro que chega é o mais recente: é ele que define o estado.
            g["last_event_at"] = _iso(r["occurred_at"])
            g["last_event_type"] = r["event_type"]
            g["last_value"] = _money(r.get("value"))
            g["gateway_status"] = f.get("status")
            g["active"] = r["event_type"] not in _PRODUCT_LOST
        # ...e o último a chegar é o mais antigo: quando o cliente adquiriu.
        g["first_event_at"] = _iso(r["occurred_at"])

        if r["event_type"] in _PRODUCT_PAID and r.get("value") is not None:
            try:
                g["paid_total_raw"] += float(r["value"])
            except (TypeError, ValueError):
                pass

        # Eventos antigos costumam trazer o que os novos omitem.
        for src, dst in (("offer_name", "offer"), ("subscription_interval", "interval"),
                         ("payment_method", "payment_method"),
                         ("subscription_id", "subscription_id")):
            if f.get(src) and not g.get(dst):
                g[dst] = f[src]

        if g["next_charge"] is None and f.get("next_charge_date"):
            g["next_charge_raw"] = str(f["next_charge_date"])
            parsed = _parse_loose_date(f["next_charge_date"])
            if parsed:
                g["next_charge"] = parsed.isoformat()
                g["days_left"] = (parsed - today).days

        if g["canceled_at"] is None and f.get("subscription_canceled_at"):
            parsed = _parse_loose_date(f["subscription_canceled_at"])
            if parsed:
                g["canceled_at"] = parsed.isoformat()

    out = list(groups.values())
    for g in out:
        g["paid_total"] = _money(g["paid_total_raw"]) if g["paid_total_raw"] else None
    # Ativos primeiro, e dentro de cada bloco o mais recente na frente. Dois
    # sorts em vez de uma chave composta: o ``sort`` do Python é ESTÁVEL, então
    # o segundo preserva a ordenação por data feita pelo primeiro.
    out.sort(key=lambda g: g.get("last_event_at") or "", reverse=True)
    out.sort(key=lambda g: not g["active"])
    return out


def build_journey(contact_id: str) -> dict:
    """Os 3 blocos de um contato JÁ resolvido no Trackify."""
    ident = fetch_identity(contact_id)
    if ident is None:
        return {"found": False, "contact_id": contact_id}
    return {
        "found": True,
        "identity": ident,
        "subscriptions": fetch_subscriptions(contact_id),
        "products": fetch_products(contact_id),
        "timeline": fetch_timeline(contact_id),
        "event_types": fetch_event_types(contact_id),
    }


def journey_for(*, phone: str | None, email: str | None = None,
                cpf: str | None = None, extras: dict | None = None,
                contact_type: str = "whatsapp") -> dict:
    """Resolve a identidade e devolve a jornada.

    Três desfechos, todos normais e todos explícitos para a tela:
      * ``found=False`` — não há cadastro no CDP (o caso MAIS comum: ~77%);
      * ``ambiguous`` — mais de um cadastro casou (medido: 5 números com 2);
      * jornada completa.
    """
    if not trackify_db.is_configured():
        return {"found": False, "configured": False,
                "error": "Conexão com o Trackify não configurada."}

    # ``extras`` = campos conectados que são identificador no CDP. Os parâmetros
    # soltos continuam valendo para quem chama sem mapeamento nenhum.
    pistas = dict(extras or {})
    if email:
        pistas.setdefault(identity.SLUG_EMAIL, email)
    if cpf:
        pistas.setdefault(identity.SLUG_CPF, cpf)
    matches = identity.resolve_mapped(phone=phone, extras=pistas,
                                      contact_type=contact_type)
    if not matches:
        return {"found": False, "configured": True, "candidates": []}

    if len(matches) > 1:
        # Nunca escolher em silêncio — o vendedor decide qual cadastro é o certo.
        return {
            "found": False,
            "configured": True,
            "ambiguous": True,
            "candidates": [
                {**(fetch_identity(m.contact_id) or {"contact_id": m.contact_id}),
                 "matched_by": m.slug}
                for m in matches
            ],
        }

    m = matches[0]
    data = build_journey(m.contact_id)
    data["matched_by"] = m.slug
    data["matched_value"] = m.exact_value
    data["configured"] = True
    return data
