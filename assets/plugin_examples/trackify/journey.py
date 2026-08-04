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

# O que conta como COMPRA sai da tabela ``channel_value_rules`` do CDP (o
# oráculo "isto é dinheiro?"), e não de uma lista chumbada de tipos de evento.
# A troca foi MEDIDA na produção: a regra por ``effect`` concorda com a lista
# antiga em 11.166 dos 11.173 pares contato↔produto; os 7 divergentes são
# produtos cuja compra é anterior ao CDP e cujo único rastro é o cancelamento —
# exatamente os que a regra nova RECUPERA (``subtract`` prova que houve venda).
# O risco de canal mal configurado (compra que cairia em ``ignore`` e sumiria em
# silêncio) não desapareceu: virou o diagnóstico ``_SQL_UNRULED_PRODUCT_EVENTS``,
# reportado na tela em vez de escondido.
#
# Tipos que ROTULAM sem criar produto: não são dinheiro (``effect='ignore'`` no
# canal ``ticto``), mas são o estado que o selo precisa mostrar. Incluir os de
# reembolso aqui é de graça — onde eles têm ``effect='subtract'`` a linha já
# entra pelo caminho do dinheiro; onde caíram em ``ignore``, ainda rotulam.
_PRODUCT_STATE_EVENTS = (
    "subscription_canceled", "subscription_delayed",
    "refunded", "chargeback", "charge.refunded", "order.canceled",
)
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

# ⚠️ Mesma forma CTE-primeiro do ``_SQL_TIMELINE``: pagina antes de juntar os
# campos dinâmicos. A lição de performance já foi paga naquele SQL (24,3 ms →
# 0,918 ms); a forma "agrega tudo e limita no fim" volta a fazer seq scan.
_SQL_PURCHASE_EVENTS = """
WITH page AS (
  SELECT e.id, e.event_type, e.value, e.occurred_at, e.channel_id,
         COALESCE(vr.effect::text, 'ignore') AS effect
  FROM events e
  LEFT JOIN channel_value_rules vr
         ON vr.channel_id = e.channel_id AND vr.event_type = e.event_type
  WHERE e.contact_id = CAST(:cid AS uuid) AND e.deleted_at IS NULL
    AND (vr.effect::text IN ('add', 'subtract')
         OR e.event_type = ANY(:state_types))
  ORDER BY e.occurred_at DESC
  LIMIT :limit
)
SELECT p.id::text AS id, p.event_type, p.value, p.occurred_at, p.effect,
       ch.slug AS channel,
       jsonb_object_agg(ecf.slug, efv.value) FILTER (WHERE ecf.slug IS NOT NULL) AS fields
FROM page p
JOIN channels ch ON ch.id = p.channel_id
LEFT JOIN event_field_values efv ON efv.event_id = p.id
LEFT JOIN event_custom_fields ecf ON ecf.id = efv.event_custom_field_id
GROUP BY p.id, p.event_type, p.value, p.occurred_at, p.effect, ch.slug
ORDER BY p.occurred_at DESC
"""
# ``vr.effect::text``, nunca ``vr.effect IN (...)``: comparar o enum com um
# literal que não existe no tipo levanta erro; o cast para texto nunca levanta.
# ``p.effect``/``ch.slug`` precisam entrar no GROUP BY (a dependência funcional
# da PK não cobre coluna vinda de join); ``channel_value_rules`` é única em
# ``(channel_id, event_type)``, então o LEFT JOIN não multiplica linha.

# Diagnóstico: evento que NOMEIA um produto mas cujo ``(canal, tipo)`` não tem
# regra de valor. É o medo legítimo da regra antiga — compra de canal mal
# configurado sumindo em silêncio. Em vez de descartar a regra nova, a gente
# REPORTA na tela. Casa por ``(channel_id, event_type)``, não só por canal:
# assim pega também o canal configurado onde ``purchase`` foi marcado
# ``ignore`` por engano — o caso mais provável e o mais invisível.
#
# O join com ``event_field_values`` é o que torna o aviso preciso: só conta
# evento que nomeia produto. Sem ele, um lead com 40 ``lead_email`` dispararia
# alarme falso. Roda SEMPRE, não só quando a lista sai vazia — "tem produto do
# canal A e perda silenciosa no canal B" é o caso que ninguém descobriria.
_SQL_UNRULED_PRODUCT_EVENTS = """
SELECT ch.slug AS channel, e.event_type, count(DISTINCT e.id)::int AS events
FROM events e
JOIN channels ch ON ch.id = e.channel_id
JOIN event_field_values efv ON efv.event_id = e.id
JOIN event_custom_fields ecf ON ecf.id = efv.event_custom_field_id
                             AND ecf.slug IN ('product_name', 'offer_name')
WHERE e.contact_id = CAST(:cid AS uuid) AND e.deleted_at IS NULL
  AND COALESCE(efv.value, '') <> ''
  AND NOT EXISTS (
    SELECT 1 FROM channel_value_rules vr
    WHERE vr.channel_id = e.channel_id AND vr.event_type = e.event_type
      AND vr.effect::text IN ('add', 'subtract'))
GROUP BY ch.slug, e.event_type
ORDER BY 3 DESC, 1, 2
"""


def _amount(value) -> Decimal:
    """``events.value`` → ``Decimal``. ``None`` e lixo viram ``0``."""
    if value is None:
        return Decimal(0)
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return Decimal(0)


def _product_identity(fields: dict) -> tuple[str, str] | None:
    """``(chave, nome)`` do produto, ou ``None`` quando o evento não nomeia um.

    A guarda do ``None`` é o que impede inventar um produto chamado "purchase":
    sem nome, cair no ``event_type`` como chave viraria uma linha fantasma na
    tela do atendente. A chave é ``subscription_id`` → ``product_name`` →
    ``offer_name``, porque o id da assinatura é o único estável entre renovações
    e os lançamentos antigos não o trazem.
    """
    nome = fields.get("product_name") or fields.get("offer_name")
    if not nome:
        return None
    return (fields.get("subscription_id") or nome), nome


def fetch_purchases(contact_id: str) -> dict:
    """Bloco 4. O que o contato COMPROU, uma linha por produto.

    Compra é o que a ``channel_value_rules`` do CDP diz que é: ``effect='add'``.
    ``subtract`` (reembolso/chargeback) também PROVA a compra — não existe
    reembolso sem venda —, mas desconta o dinheiro. Estado (cancelamento,
    atraso) apenas ROTULA: nunca cria produto nem filtra a linha. Se o contato
    comprou alguma vez, ele aparece.

    Piso em zero: o CDP trava ``total_spent`` em zero (``clampFloor`` no
    ``pipeline.ts`` e ``GREATEST(0, …)`` na migration de backfill). A linha de
    produto ESPELHA esse piso de propósito — para as duas telas contarem a mesma
    história — e o valor descontado aparece à parte em ``refunded``. Não é
    arredondamento nosso.

    Dinheiro acumula em ``Decimal``, nunca ``float``: o contrato é "a soma usa a
    mesma regra do CDP", e centavos em ``float`` quebram isso num contato com
    muitas parcelas. O JSON expõe ``float(...)`` só na borda.
    """
    rows = trackify_db.run_read(_SQL_PURCHASE_EVENTS, {
        "cid": contact_id,
        "state_types": list(_PRODUCT_STATE_EVENTS),
        "limit": _PRODUCT_SCAN_LIMIT,
    })

    groups: dict[str, dict] = {}
    somas: dict[str, dict[str, Decimal]] = {}

    # 1ª passada — só dinheiro. É ela que CRIA o produto.
    for r in rows:                      # já vem do mais novo para o mais antigo
        effect = r.get("effect")
        if effect not in ("add", "subtract"):
            continue
        f = r.get("fields") or {}
        ident = _product_identity(f)
        if ident is None:
            continue
        key, nome = ident
        g = groups.setdefault(key, {
            "key": key,
            "name": nome,
            "offer": f.get("offer_name"),
            "subscription_id": f.get("subscription_id"),
            "interval": f.get("subscription_interval"),
            "payment_method": f.get("payment_method"),
            "purchases": 0,
            "paid_total_raw": 0.0,
            "paid_total": None,
            "refunded_raw": 0.0,
            "refunded": None,
            "first_purchase_at": None,
            "last_purchase_at": None,
            "last_event_type": None,
            "last_effect": None,
            "last_event_at": None,
            "gateway_status": None,
            "channel": None,
        })
        s = somas.setdefault(key, {"add": Decimal(0), "sub": Decimal(0)})

        if effect == "add":
            g["purchases"] += 1
            s["add"] += _amount(r.get("value"))
            # Vem DESC: o primeiro a chegar é o mais recente (grava só se ainda
            # não gravou), e o último a chegar é o mais antigo (sobrescreve).
            if g["last_purchase_at"] is None:
                g["last_purchase_at"] = _iso(r["occurred_at"])
            g["first_purchase_at"] = _iso(r["occurred_at"])
        else:
            s["sub"] += _amount(r.get("value"))

        # Evento antigo costuma trazer o que o novo omite.
        for src, dst in (("offer_name", "offer"), ("subscription_interval", "interval"),
                         ("payment_method", "payment_method"),
                         ("subscription_id", "subscription_id")):
            if f.get(src) and not g.get(dst):
                g[dst] = f[src]

    # 2ª passada — o selo. Percorre TUDO (inclusive os ``ignore``), mas só
    # escreve em grupo que JÁ existe: é isso que impede um cancelamento órfão
    # (produto comprado antes do CDP, sem nenhum evento de dinheiro) de virar
    # produto — ele rotula quem existe e é ignorado quando não há quem rotular.
    for r in rows:
        f = r.get("fields") or {}
        ident = _product_identity(f)
        if ident is None:
            continue
        g = groups.get(ident[0])
        if g is None or g["last_event_at"] is not None:
            continue
        g["last_event_at"] = _iso(r["occurred_at"])
        g["last_event_type"] = r["event_type"]
        g["last_effect"] = r.get("effect")
        g["gateway_status"] = f.get("status")
        g["channel"] = r.get("channel")

    out = list(groups.values())
    for g in out:
        s = somas[g["key"]]
        pago = s["add"] - s["sub"]
        if pago < 0:
            pago = Decimal(0)           # espelho do clampFloor do CDP
        g["paid_total_raw"] = float(pago)
        g["paid_total"] = _money(pago)          # SEMPRE preenchido, inclusive "R$ 0,00"
        g["refunded_raw"] = float(s["sub"])
        g["refunded"] = _money(s["sub"]) if s["sub"] > 0 else None

    # Dois sorts estáveis em vez de uma chave composta: o ``sort`` do Python
    # preserva a ordem anterior, então o segundo desempata pelo primeiro.
    out.sort(key=lambda g: (g.get("name") or "").lower())
    out.sort(key=lambda g: g.get("last_event_at") or "", reverse=True)

    unruled = trackify_db.run_read(_SQL_UNRULED_PRODUCT_EVENTS, {"cid": contact_id})
    return {
        "items": out,
        "unruled": [{"channel": u["channel"], "event_type": u["event_type"],
                     "events": int(u["events"] or 0)} for u in unruled],
    }


def _purchases_block(contact_id: str) -> dict:
    """Fachada com guarda. A função pura continua levantando (e testável).

    ``channel_value_rules`` é dependência NOVA: um CDP mais antigo pode não ter
    a tabela, e sem esta guarda um ``UndefinedTable`` apagaria também a aba
    Jornada — o docstring do módulo promete o contrário.
    """
    try:
        return fetch_purchases(contact_id)
    except Exception:  # noqa: BLE001
        logger.warning("trackify: bloco de compras indisponível", exc_info=True)
        return {"items": [], "unruled": [], "unavailable": True}


def build_journey(contact_id: str) -> dict:
    """Os blocos de um contato JÁ resolvido no Trackify.

    A chave é ``purchases`` (dict), não mais ``products`` (lista): o rename é
    deliberado. Um ``JourneyModal.js`` velho em cache lê ``data.purchases ===
    undefined`` e simplesmente não renderiza o bloco; se a chave continuasse
    ``products`` com um dict dentro, ``products.filter`` explodiria e apagaria o
    modal inteiro.
    """
    ident = fetch_identity(contact_id)
    if ident is None:
        return {"found": False, "contact_id": contact_id}
    return {
        "found": True,
        "identity": ident,
        "subscriptions": fetch_subscriptions(contact_id),
        "purchases": _purchases_block(contact_id),
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
