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

import asyncio
import logging
from datetime import date, datetime
from decimal import Decimal

from . import _config, identity
from . import client as tk_client

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

async def _catalog(http) -> list[dict]:
    """Catálogo de campos ativos do CDP, em cache.

    Parte do CATÁLOGO e não dos valores do contato, de propósito: a aba
    "Informações do Contato" do Trackify mostra TODO campo ativo, com o valor em
    branco quando o contato não tem aquele dado. Um campo ausente da lista é
    indistinguível de um campo vazio, e "sem CPF cadastrado" é justamente o que o
    atendente precisa enxergar.
    """
    res = await tk_client.cached("custom-fields", lambda: tk_client.custom_fields(http))
    if not res.ok:
        return []
    linhas = res.data if isinstance(res.data, list) else (res.data or {}).get("data") or []
    ativos = [f for f in linhas if f.get("isActive") and not f.get("deletedAt")]
    # Mesma ordem do SQL antigo: identificador primeiro, depois prioridade, nome.
    ativos.sort(key=lambda f: (
        not f.get("isIdentifier"),
        f.get("identifierPriority") if f.get("identifierPriority") is not None else 10**9,
        (f.get("name") or "").lower(),
    ))
    return ativos


async def fetch_identity(http, contact_id: str) -> dict | None:
    """Bloco 1. ``None`` quando o contato não existe (ou foi soft-deletado)."""
    res = await tk_client.get_contact(http, contact_id)
    if not res.ok:
        return None
    c = res.data or {}
    if not c.get("id"):
        return None

    # Valores do contato, indexados por slug.
    valores: dict[str, str] = {}
    for row in c.get("contactFieldValues") or []:
        slug = ((row.get("customField") or {}).get("slug")) or ""
        if slug:
            valores[slug] = row.get("value") or ""

    catalogo = await _catalog(http)
    campos = [
        {"slug": f.get("slug") or "", "name": f.get("name") or "",
         "is_identifier": bool(f.get("isIdentifier")),
         "value": valores.get(f.get("slug") or "", "")}
        for f in catalogo
    ]

    tags = [
        {"name": (t.get("tag") or {}).get("name"), "color": (t.get("tag") or {}).get("color")}
        for t in (c.get("contactTags") or [])
    ]
    tags = [t for t in tags if t.get("name")]
    tags.sort(key=lambda t: (t.get("name") or ""))

    return {
        "contact_id": str(c.get("id")),
        "status": c.get("status"),
        "total_spent": _money(c.get("totalSpent")),
        "total_spent_raw": float(c.get("totalSpent") or 0),
        "first_seen_at": _iso(c.get("firstSeenAt")),
        "converted_at": _iso(c.get("convertedAt")),
        "name": valores.get("name") or "",
        # Sem filtrar por valor preenchido: campo em branco é informação.
        "identifiers": [
            {"slug": f["slug"], "name": f["name"], "value": f["value"]}
            for f in campos if f["is_identifier"]
        ],
        "fields": [
            {"slug": f["slug"], "name": f["name"], "value": f["value"]}
            for f in campos if not f["is_identifier"]
        ],
        "tags": tags,
        "link": _config.contact_link(str(c.get("id"))),
    }


# ── Bloco 3 — linha do tempo ─────────────────────────────────────────────

def _event_row(r: dict) -> dict:
    """Linha de evento vinda da API → forma que a tela consome."""
    campos = {}
    for fv in r.get("eventFieldValues") or []:
        slug = ((fv.get("eventCustomField") or {}).get("slug")) or ""
        if slug:
            campos[slug] = fv.get("value")
    return {
        "id": str(r.get("id") or ""),
        "event_type": r.get("eventType"),
        "title": r.get("title"),
        "description": r.get("description"),
        "value": _money(r.get("value")),
        "occurred_at": _iso(r.get("occurredAt")),
        "channel": (r.get("channel") or {}).get("slug"),
        "fields": campos,
    }


async def fetch_timeline(http, contact_id: str, *, limit: int | None = None,
                         offset: int = 0, event_type: str | None = None) -> dict:
    """Bloco 3, paginado. ``event_type`` filtra (o vendedor quer só compras/só falhas)."""
    lim = limit or _config.timeline_page_size()
    off = max(0, offset)
    res = await tk_client.list_events(http, contact_id, limit=lim, offset=off,
                                      event_type=event_type)
    if not res.ok:
        return {"events": [], "total": 0, "limit": lim, "offset": off}
    body = res.data or {}
    return {
        "events": [_event_row(r) for r in (body.get("data") or [])],
        "total": int(((body.get("meta") or {}).get("total")) or 0),
        "limit": lim,
        "offset": off,
    }


async def fetch_event_types(http, contact_id: str) -> list[dict]:
    """Tipos de evento do contato + contagem — alimenta o filtro do modal."""
    res = await tk_client.events_summary(http, contact_id)
    if not res.ok:
        return []
    return [
        {"event_type": row.get("eventType"), "total": int(row.get("total") or 0)}
        for row in ((res.data or {}).get("byType") or [])
    ]


# ── Bloco 2 — assinaturas (derivado, sem consulta extra por assinatura) ──

def _subscription_row(r: dict) -> dict:
    """Evento da API na forma que a derivação de assinatura espera."""
    linha = _event_row(r)
    return {
        "id": linha["id"],
        "event_type": linha["event_type"],
        "title": linha["title"],
        "value": r.get("value"),
        "occurred_at": r.get("occurredAt"),
        "fields": linha["fields"],
    }


async def fetch_subscriptions(http, contact_id: str, *, today: date | None = None) -> list[dict]:
    """Bloco 2 — agrupa os eventos de assinatura por ``subscription_id``.

    Sem ``subscription_id`` cai no ``product_name`` (é o que o CDP tem para os
    lançamentos antigos). "Dias restantes" sai de ``next_charge_date``, que é o
    dado que EXISTE — nunca inventamos uma data de expiração.
    """
    res = await tk_client.list_events(http, contact_id,
                                      limit=_SUBSCRIPTION_SCAN_LIMIT, offset=0,
                                      event_types=list(_SUBSCRIPTION_EVENTS))
    if not res.ok:
        return []
    rows = [_subscription_row(r) for r in ((res.data or {}).get("data") or [])]
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
def _amount(value) -> Decimal:
    """``events.value`` → ``Decimal``. ``None`` e lixo viram ``0``."""
    if value is None:
        return Decimal(0)
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return Decimal(0)


def _name_partner(campo: str) -> str | None:
    """``product_id`` → ``product_name``. Convenção de nomenclatura do CDP.

    Derivar o par em vez de chumbar a dupla é o que faz um slug NOVO
    (``plan_id``/``plan_name``) funcionar só por ser acrescentado à setting.
    """
    return campo[:-3] + "_name" if campo.endswith("_id") else None


def _identity_map(rows: list[dict], campos: list[str]) -> dict[str, str]:
    """id do produto → nome, colhido dos eventos que trazem os DOIS.

    Existe para não PARTIR um produto em duas linhas: no ``ticto`` parte dos
    eventos de um mesmo produto traz o nome e parte só o id. Sem este mapa, o
    recurso ao id (abaixo) daria uma linha "Combo de Redes" e outra
    "prod-1234" para a mesma coisa.
    """
    pares = [(c, _name_partner(c)) for c in campos]
    pares = [(i, n) for i, n in pares if n and n in campos]
    m: dict[str, str] = {}
    for r in rows:
        f = r.get("fields") or {}
        for campo_id, campo_nome in pares:
            i, n = f.get(campo_id), f.get(campo_nome)
            if i and n:
                m.setdefault(str(i), n)
    return m


def _product_identity(fields: dict, id2name: dict | None = None,
                      campos: list[str] | None = None) -> tuple[str, str] | None:
    """``(chave, nome)`` do produto, ou ``None`` quando o evento não identifica um.

    A guarda do ``None`` é o que impede inventar um produto chamado "purchase":
    sem identificação, cair no ``event_type`` como chave viraria uma linha
    fantasma na tela do atendente. A chave é ``subscription_id`` → nome, porque
    o id da assinatura é o único estável entre renovações e os lançamentos
    antigos não o trazem.

    ``campos`` é a ordem de precedência CONFIGURÁVEL (``_config``), e não uma
    lista chumbada, porque cada gateway do CDP nomeia de um jeito: o ``ticto``
    preenche ``product_name``/``offer_name``, e o ``pagarme`` **nunca** preenche
    nome nenhum (medido: 1.281 ``charge.paid``, zero com nome) — só
    ``product_id``/``offer_id``. Sem o degrau de id, uma compra paga e já
    contada no "Total gasto" não virava linha nenhuma; sem a setting, cada
    gateway novo com um slug próprio exigiria release do plugin.

    O id cru só chega à tela quando ``id2name`` não souber traduzi-lo — rótulo
    feio é melhor que compra invisível.
    """
    campos = campos or list(_config.PRODUCT_IDENTITY_FIELDS)
    nome = None
    for campo in campos:
        bruto = fields.get(campo)
        if not bruto:
            continue
        traduzido = (id2name or {}).get(str(bruto)) if _name_partner(campo) else None
        nome = traduzido or bruto
        break
    if not nome:
        return None
    return (fields.get("subscription_id") or nome), nome


def _purchase_row(r: dict) -> dict:
    """Evento de valor vindo da API na forma que a derivação de produto espera.

    A rota já devolve os campos dinâmicos achatados em ``fields`` e o canal como
    slug, então aqui é só renomear — nenhuma regra de negócio mora nesta função.
    """
    return {
        "id": str(r.get("id") or ""),
        "event_type": r.get("eventType"),
        "title": r.get("title"),
        "description": None,
        "value": r.get("value"),
        "occurred_at": r.get("occurredAt"),
        "effect": r.get("effect"),
        "channel": r.get("channel"),
        "fields": r.get("fields") or {},
    }


def _flat_event_row(r: dict) -> dict:
    """``_event_row`` para uma linha JÁ achatada por ``_purchase_row``.

    O histórico embutido em cada produto usa o MESMO formato da linha do tempo —
    o modal reusa o componente de evento em vez de inventar um segundo jeito de
    desenhar a mesma coisa.
    """
    return {
        "id": r.get("id"),
        "event_type": r.get("event_type"),
        "title": r.get("title"),
        "description": r.get("description"),
        "value": _money(r.get("value")),
        "occurred_at": _iso(r.get("occurred_at")),
        "channel": r.get("channel"),
        "fields": r.get("fields") or {},
    }


async def fetch_purchases(http, contact_id: str) -> dict:
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
    campos = _config.product_identity_fields()
    res = await tk_client.purchases(http, contact_id, limit=_PRODUCT_SCAN_LIMIT,
                                    state_types=list(_PRODUCT_STATE_EVENTS),
                                    identity_fields=campos)
    if not res.ok:
        # A rota é dependência NOVA: um Trackify anterior a ela devolve 404, e
        # apagar a aba Jornada inteira por causa disso quebraria a promessa do
        # módulo. Quem chama trata `unavailable`.
        raise RuntimeError(res.error or "bloco de compras indisponível")
    corpo = res.data or {}
    rows = [_purchase_row(r) for r in (corpo.get("events") or [])]
    diagnostico = corpo.get("diagnostics") or {}

    groups: dict[str, dict] = {}
    somas: dict[str, dict[str, Decimal]] = {}
    # Antes de qualquer passada: quem sabe traduzir id de produto em nome são os
    # próprios eventos do contato que trazem os dois campos.
    id2name = _identity_map(rows, campos)

    # 1ª passada — só dinheiro. É ela que CRIA o produto.
    for r in rows:                      # já vem do mais novo para o mais antigo
        effect = r.get("effect")
        if effect not in ("add", "subtract"):
            continue
        f = r.get("fields") or {}
        ident = _product_identity(f, id2name, campos)
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
            # O histórico DAQUELE produto, no MESMO formato da linha do tempo
            # (``_event_row``) — o modal reusa o componente de evento em vez de
            # inventar um segundo jeito de desenhar a mesma coisa. Embutir sai
            # de graça: a consulta já lê estas linhas, e o contato mais pesado
            # da produção tem 26 eventos (média 1,2), ~16 KB — menos que a 1ª
            # página da timeline, que o modal já carrega sempre.
            "events": [],
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

    # 2ª passada — o selo e o HISTÓRICO. Percorre TUDO (inclusive os ``ignore``),
    # mas só
    # escreve em grupo que JÁ existe: é isso que impede um cancelamento órfão
    # (produto comprado antes do CDP, sem nenhum evento de dinheiro) de virar
    # produto — ele rotula quem existe e é ignorado quando não há quem rotular.
    for r in rows:
        f = r.get("fields") or {}
        ident = _product_identity(f, id2name, campos)
        if ident is None:
            continue
        g = groups.get(ident[0])
        if g is None:
            continue
        if g["last_event_at"] is None:      # o 1º que chega é o mais recente
            g["last_event_at"] = _iso(r["occurred_at"])
            g["last_event_type"] = r["event_type"]
            g["last_effect"] = r.get("effect")
            g["gateway_status"] = f.get("status")
            g["channel"] = r.get("channel")
        # O histórico leva TAMBÉM os eventos de estado (cancelamento, atraso):
        # é o que explica o selo que a linha já mostra.
        g["events"].append(_flat_event_row(r))

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

    def _diag(chave: str) -> list[dict]:
        out_diag = []
        for u in diagnostico.get(chave) or []:
            item = {"channel": u.get("channel"), "event_type": u.get("eventType"),
                    "events": int(u.get("events") or 0)}
            # `fields` só existe no diagnóstico de "sem identidade" — e só
            # quando o CDP tinha o que listar. Carimbar uma string vazia aqui
            # faria a tela mostrar "campos: " sem campo nenhum.
            if "fields" in u:
                item["fields"] = u.get("fields") or ""
            out_diag.append(item)
        return out_diag

    return {
        "items": out,
        "unruled": _diag("unruledProductEvents"),
        "unnamed": _diag("unnamedPurchaseEvents"),
    }


async def _purchases_block(http, contact_id: str) -> dict:
    """Fachada com guarda. A função de dentro continua levantando (e testável).

    A rota ``/purchases`` é dependência NOVA: um Trackify mais antigo responde
    404, e sem esta guarda isso apagaria também a aba Jornada — o docstring do
    módulo promete o contrário.
    """
    try:
        return await fetch_purchases(http, contact_id)
    except Exception:  # noqa: BLE001
        logger.warning("trackify: bloco de compras indisponível", exc_info=True)
        return {"items": [], "unruled": [], "unnamed": [], "unavailable": True}


async def build_journey(http, contact_id: str) -> dict:
    """Os blocos de um contato JÁ resolvido no Trackify.

    A chave é ``purchases`` (dict), não mais ``products`` (lista): o rename é
    deliberado. Um ``JourneyModal.js`` velho em cache lê ``data.purchases ===
    undefined`` e simplesmente não renderiza o bloco; se a chave continuasse
    ``products`` com um dict dentro, ``products.filter`` explodiria e apagaria o
    modal inteiro.
    """
    ident = await fetch_identity(http, contact_id)
    if ident is None:
        return {"found": False, "contact_id": contact_id}

    # Os quatro blocos são independentes: buscar em paralelo troca a soma das
    # latências pela maior delas. Com SQL local a diferença era ruído; com HTTP
    # é a diferença entre abrir o modal em ~1 ida e volta ou em quatro.
    subs, compras, linha, tipos = await asyncio.gather(
        fetch_subscriptions(http, contact_id),
        _purchases_block(http, contact_id),
        fetch_timeline(http, contact_id),
        fetch_event_types(http, contact_id),
    )
    return {
        "found": True,
        "identity": ident,
        "subscriptions": subs,
        "purchases": compras,
        "timeline": linha,
        "event_types": tipos,
    }


async def journey_for(http, *, phone: str | None, email: str | None = None,
                      cpf: str | None = None, extras: dict | None = None,
                      contact_type: str = "whatsapp") -> dict:
    """Resolve a identidade e devolve a jornada.

    Três desfechos, todos normais e todos explícitos para a tela:
      * ``found=False`` — não há cadastro no CDP (o caso MAIS comum: ~77%);
      * ``ambiguous`` — mais de um cadastro casou (medido: 5 números com 2);
      * jornada completa.
    """
    if not tk_client.is_configured():
        return {"found": False, "configured": False,
                "error": "API key do Trackify não configurada."}

    # ``extras`` = campos conectados que são identificador no CDP. Os parâmetros
    # soltos continuam valendo para quem chama sem mapeamento nenhum.
    pistas = dict(extras or {})
    if email:
        pistas.setdefault(identity.SLUG_EMAIL, email)
    if cpf:
        pistas.setdefault(identity.SLUG_CPF, cpf)
    matches = await identity.resolve_mapped(http, phone=phone, extras=pistas,
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
                {**(ident or {"contact_id": m.contact_id}), "matched_by": m.slug}
                for m, ident in zip(
                    matches,
                    await asyncio.gather(*(fetch_identity(http, m.contact_id)
                                           for m in matches)),
                )
            ],
        }

    m = matches[0]
    data = await build_journey(http, m.contact_id)
    data["matched_by"] = m.slug
    data["matched_value"] = m.exact_value
    data["configured"] = True
    return data
