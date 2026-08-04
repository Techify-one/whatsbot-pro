"""Ponte de identidade WhatsBot → Trackify.

Serve as DUAS direções e é por isso que vive separado de ``journey.py``:

* **Leitura** — descobrir de quem é a jornada a mostrar no modal.
* **Escrita** — descobrir o valor EXATO que o Trackify já guarda, para a ingestão
  casar a linha existente em vez de criar um contato novo. A resolução de
  identidade do Trackify é igualdade exata de string (``pipeline.ts``), e com a
  política "criar sempre" um telefone em grafia diferente vira um contato
  duplicado, em silêncio, para sempre.

Ordem de tentativa = a ``identifier_priority`` do próprio Trackify (medida na F0):
``email`` (10) → ``whatsapp`` (20) → ``cpf`` (30) → ``telegram_id`` (40). O e-mail
vem primeiro porque **99,5% dos contatos do CDP têm e-mail** — quando o atendente
preencheu o e-mail no painel, ele casa muito melhor que qualquer telefone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import phone as phone_mod
from . import trackify_db

logger = logging.getLogger("plugins.trackify.identity")

# Slug de identificador → como o valor é preparado antes de comparar.
SLUG_WHATSAPP = "whatsapp"
SLUG_EMAIL = "email"
SLUG_CPF = "cpf"
SLUG_TELEGRAM = "telegram_id"


@dataclass(frozen=True)
class Match:
    """Um contato do Trackify que casou com um identificador do WhatsBot."""

    contact_id: str
    slug: str
    exact_value: str   # a grafia EXATA gravada no CDP — é o que a escrita reenvia
    matched_by: str    # 'variant' (indexado) | 'digits' (fallback com máscara)


# ── Consultas ────────────────────────────────────────────────────────────

_SQL_BY_VALUES = """
SELECT c.id::text AS contact_id, cfv.value AS exact_value
FROM contact_field_values cfv
JOIN custom_fields cf ON cf.id = cfv.custom_field_id AND cf.deleted_at IS NULL
JOIN contacts c       ON c.id = cfv.contact_id AND c.deleted_at IS NULL
WHERE cf.slug = :slug AND cfv.value = ANY(:cands)
"""

# Fallback tolerante a máscara. Medido na F0: só 6 de 10.910 valores de
# ``whatsapp`` têm caractere fora de [0-9+], então isto é CAMINHO FRIO — roda
# apenas quando a busca indexada não achou nada. Não use como caminho primário:
# ``regexp_replace`` nos dois lados não usa ``idx_cfv_identifier_lookup``.
_SQL_BY_DIGITS = """
SELECT c.id::text AS contact_id, cfv.value AS exact_value
FROM contact_field_values cfv
JOIN custom_fields cf ON cf.id = cfv.custom_field_id AND cf.deleted_at IS NULL
JOIN contacts c       ON c.id = cfv.contact_id AND c.deleted_at IS NULL
WHERE cf.slug = :slug
  AND regexp_replace(cfv.value, '[^0-9]', '', 'g') = ANY(:digits)
"""


def _by_values(slug: str, candidates: list[str]) -> list[Match]:
    if not candidates:
        return []
    rows = trackify_db.run_read(_SQL_BY_VALUES, {"slug": slug, "cands": candidates})
    return [Match(r["contact_id"], slug, r["exact_value"], "variant") for r in rows]


def _by_digits(slug: str, digits: list[str]) -> list[Match]:
    if not digits:
        return []
    rows = trackify_db.run_read(_SQL_BY_DIGITS, {"slug": slug, "digits": digits})
    return [Match(r["contact_id"], slug, r["exact_value"], "digits") for r in rows]


def _dedupe(matches: list[Match]) -> list[Match]:
    """Um contato só, mesmo que dois candidatos tenham casado nele."""
    seen: set[str] = set()
    out: list[Match] = []
    for m in matches:
        if m.contact_id in seen:
            continue
        seen.add(m.contact_id)
        out.append(m)
    return out


# ── API pública ──────────────────────────────────────────────────────────

def resolve_by_phone(value: str | None) -> list[Match]:
    """Contatos do Trackify cujo ``whatsapp`` casa com ``value``.

    Primeiro pelas variantes (indexado, 0,085 ms). Só no vazio tenta a comparação
    por dígitos, que tolera máscara.
    """
    candidates = phone_mod.lookup_candidates(value)
    if not candidates:
        return []
    found = _by_values(SLUG_WHATSAPP, candidates)
    if found:
        return _dedupe(found)
    digits = [c for c in candidates if not c.startswith("+")]
    return _dedupe(_by_digits(SLUG_WHATSAPP, digits))


def resolve_by_email(value: str | None) -> list[Match]:
    """Contatos cujo ``email`` casa (comparação insensível a caixa e espaço)."""
    v = (value or "").strip().lower()
    if not v or "@" not in v:
        return []
    # O CDP guarda a grafia que recebeu; testamos as duas formas óbvias em vez
    # de um ILIKE (que não usaria o índice).
    return _dedupe(_by_values(SLUG_EMAIL, [v, (value or "").strip()]))


def resolve_by_cpf(value: str | None) -> list[Match]:
    """Contatos cujo ``cpf`` casa. CPF circula com e sem máscara — testamos as duas."""
    digits = phone_mod.digits_only(value)
    if len(digits) != 11:
        return []
    masked = f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
    found = _by_values(SLUG_CPF, [digits, masked])
    if found:
        return _dedupe(found)
    return _dedupe(_by_digits(SLUG_CPF, [digits]))


def resolve_by_telegram(value: str | None) -> list[Match]:
    """Contatos cujo ``telegram_id`` casa (chat_id numérico, comparação literal)."""
    v = (value or "").strip()
    return _dedupe(_by_values(SLUG_TELEGRAM, [v])) if v else []


# Prioridade padrão do Trackify, usada quando o catálogo do CDP não pôde ser
# lido. Medida na F0 e igual à ordem que o ``pipeline.ts`` percorre.
_PRIORIDADE_PADRAO = {SLUG_EMAIL: 10, SLUG_WHATSAPP: 20, SLUG_CPF: 30,
                      SLUG_TELEGRAM: 40}


def resolve_by_slug(slug: str, value: str | None) -> list[Match]:
    """Resolve por QUALQUER identificador, inclusive um criado pelo cliente.

    Os quatro slugs conhecidos têm normalização própria (telefone tem variante
    brasileira, CPF circula com e sem máscara, e-mail é insensível a caixa). Um
    identificador que o cliente criou no CDP não tem regra conhecida, então vale
    a comparação exata com um fallback por dígitos — que é o que dá certo para
    documento e telefone mascarados, os casos comuns.
    """
    slug = (slug or "").strip()
    value = (value or "").strip()
    if not slug or not value:
        return []
    if slug == SLUG_WHATSAPP:
        return resolve_by_phone(value)
    if slug == SLUG_EMAIL:
        return resolve_by_email(value)
    if slug == SLUG_CPF:
        return resolve_by_cpf(value)
    if slug == SLUG_TELEGRAM:
        return resolve_by_telegram(value)

    found = _by_values(slug, [value])
    if found:
        return _dedupe(found)
    digits = phone_mod.digits_only(value)
    return _dedupe(_by_digits(slug, [digits])) if digits else []


def resolve_mapped(*, phone: str | None, extras: dict | None = None,
                   contact_type: str = "whatsapp") -> list[Match]:
    """Tenta o TELEFONE e todo campo conectado que seja identificador no CDP.

    ``extras`` é ``{slug_no_trackify: valor_no_whatsbot}``, montado a partir dos
    mapeamentos ativos (ver ``field_map.identifier_hints``). Assim, conectar um
    campo na tela passa a valer também para ENCONTRAR o cadastro, e não só para
    copiar o valor depois — que era a pergunta natural de quem configura.

    A ordem é a ``identifier_priority`` do próprio Trackify, porque é a mesma que
    a ingestão dele percorre: divergir faria a leitura casar num contato e a
    escrita em outro. Para no primeiro identificador que achar alguém.
    """
    candidatos: dict[str, str] = {}
    for slug, valor in (extras or {}).items():
        if slug and str(valor or "").strip():
            candidatos[slug] = str(valor).strip()

    # O telefone entra sempre, mesmo sem mapeamento: é a chave do WhatsBot.
    if contact_type == "telegram":
        candidatos.setdefault(SLUG_TELEGRAM, (phone or "").strip())
    elif phone:
        candidatos.setdefault(SLUG_WHATSAPP, phone.strip())

    prioridade = _prioridades()
    ordenados = sorted(candidatos.items(),
                       key=lambda kv: (prioridade.get(kv[0], 999), kv[0]))
    for slug, valor in ordenados:
        found = resolve_by_slug(slug, valor)
        if found:
            return found
    return []


def _prioridades() -> dict:
    """``{slug: identifier_priority}`` do CDP, com o padrão medido como piso."""
    out = dict(_PRIORIDADE_PADRAO)
    try:
        from . import field_map
        catalogo = field_map.tk_fields()
        for f in catalogo.get("fields") or []:
            if f.get("is_identifier"):
                pr = f.get("identifier_priority")
                out[f["slug"]] = 999 if pr is None else int(pr)
    except Exception:  # noqa: BLE001 — sem catálogo, o padrão serve
        logger.debug("trackify: catálogo de identificadores indisponível", exc_info=True)
    return out


def resolve(*, phone: str | None = None, email: str | None = None,
            cpf: str | None = None, telegram_id: str | None = None,
            contact_type: str = "whatsapp") -> list[Match]:
    """Resolve o contato tentando os identificadores na prioridade do Trackify.

    Para no PRIMEIRO identificador que achar alguém — é a mesma semântica do
    ``pipeline.ts``, que percorre os identificadores por prioridade e para no
    primeiro acerto. Devolve lista porque o mesmo número pode estar em mais de
    um contato (medido: 5 números com 2 cadastros) — quem chama decide se mostra
    um seletor ou se desiste.
    """
    if email:
        found = resolve_by_email(email)
        if found:
            return found
    # Telegram não tem telefone: o "phone" do contato é o chat_id.
    if contact_type == "telegram":
        found = resolve_by_telegram(telegram_id or phone)
        if found:
            return found
    elif phone:
        found = resolve_by_phone(phone)
        if found:
            return found
    if cpf:
        found = resolve_by_cpf(cpf)
        if found:
            return found
    return []


def canonical_identity(*, phone: str | None, email: str | None = None,
                       contact_type: str = "whatsapp") -> dict[str, str]:
    """Identificadores a ENVIAR quando o contato não existe no CDP (criação).

    Só chaves com valor — a ingestão devolve 422 se nenhum identificador
    sobreviver, e mandar string vazia não ajuda ninguém.
    """
    out: dict[str, str] = {}
    e = (email or "").strip().lower()
    if e and "@" in e:
        out[SLUG_EMAIL] = e
    if contact_type == "telegram":
        tid = phone_mod.digits_only(phone)
        if tid:
            out[SLUG_TELEGRAM] = tid
    else:
        wa = phone_mod.canonical_e164(phone)
        if wa:
            out[SLUG_WHATSAPP] = wa
    return out
