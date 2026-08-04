"""Ponte de identidade WhatsBot → Trackify.

Serve as DUAS direções e é por isso que vive separado de ``journey.py``:

* **Leitura** — descobrir de quem é a jornada a mostrar no modal.
* **Escrita** — descobrir o valor EXATO que o Trackify já guarda, para a ingestão
  casar a linha existente em vez de criar um contato novo. A resolução de
  identidade do Trackify é igualdade exata de string (``pipeline.ts``), e com a
  política "criar sempre" um telefone em grafia diferente vira um contato
  duplicado, em silêncio, para sempre.

⚠️ **A consulta migrou para ``POST /contacts/resolve``.** Antes este módulo
carregava o SQL e o rodava direto no Postgres do CDP. O que ficou aqui é o que é
DO WHATSBOT: gerar as grafias equivalentes de um telefone brasileiro (DDI, nono
dígito) e de um CPF (com e sem máscara), e escolher em que ordem tentar. O
servidor compara — por igualdade exata, com o fallback por dígitos como caminho
frio opcional, exatamente como era antes.

Ordem de tentativa = a ``identifier_priority`` do próprio Trackify (medida na F0):
``email`` (10) → ``whatsapp`` (20) → ``cpf`` (30) → ``telegram_id`` (40). O e-mail
vem primeiro porque **99,5% dos contatos do CDP têm e-mail** — quando o atendente
preencheu o e-mail no painel, ele casa muito melhor que qualquer telefone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import client as tk_client
from . import phone as phone_mod

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


# ── Grafias equivalentes (puro, sem rede) ────────────────────────────────

def candidates_for(slug: str, value: str | None) -> list[str]:
    """Todas as grafias de ``value`` que devem ser tentadas para ``slug``.

    É PURO de propósito: o servidor compara byte a byte, então a qualidade do
    casamento depende inteiramente do que sai daqui — e isso precisa ser
    testável sem rede.
    """
    v = (value or "").strip()
    if not v:
        return []

    if slug == SLUG_WHATSAPP:
        return phone_mod.lookup_candidates(v)

    if slug == SLUG_EMAIL:
        low = v.lower()
        if "@" not in low:
            return []
        # O CDP guarda a grafia que recebeu; testamos as duas formas óbvias em
        # vez de um ILIKE, que não usaria o índice do outro lado.
        return list(dict.fromkeys([low, v]))

    if slug == SLUG_CPF:
        digits = phone_mod.digits_only(v)
        if len(digits) != 11:
            return []
        masked = f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
        return [digits, masked]

    if slug == SLUG_TELEGRAM:
        return [v]

    # Identificador criado pelo cliente: sem regra conhecida, vale o valor cru.
    # O fallback por dígitos do servidor cobre documento/telefone mascarados.
    return [v]


# ⚠️ A ORDEM DE PRIORIDADE saiu daqui. Quem ordena os casamentos é o servidor,
# pela `identifier_priority` do próprio CDP — a mesma que o `pipeline.ts` percorre
# na ingestão. Manter uma segunda cópia da regra aqui é o jeito de a leitura casar
# num contato e a escrita em outro.


# ── Consulta (rede) ──────────────────────────────────────────────────────

async def resolve_candidates(http, candidatos: dict) -> list[Match]:
    """Resolve ``{slug: valor}`` numa chamada só, na prioridade do CDP.

    Manda TODOS os identificadores de uma vez em vez de um por vez: com SQL local
    cada tentativa custava 0,085 ms, mas agora cada uma seria uma ida à rede. O
    servidor devolve os casamentos já ordenados por prioridade e com um contato
    por linha, que é a mesma semântica do laço antigo — parar no primeiro
    identificador que achar alguém.
    """
    payload: dict[str, list[str]] = {}
    for slug, valor in (candidatos or {}).items():
        cands = candidates_for(slug, valor)
        if cands:
            payload[slug] = cands
    if not payload:
        return []

    res = await tk_client.resolve(http, payload)
    if not res.ok:
        logger.debug("trackify: resolve recusado (%s)", res.error)
        return []

    matches = _matches_from(res.data)
    if matches:
        return matches

    # Nada no exato: repete pedindo o fallback tolerante a máscara. Só aqui —
    # é caminho frio dos dois lados (medido: 6 de 10.910 valores de `whatsapp`
    # têm caractere fora de [0-9+]).
    res = await tk_client.resolve(http, payload, digits_fallback=True)
    return _matches_from(res.data) if res.ok else []


def _matches_from(data) -> list[Match]:
    """``{matches:[...]}`` → ``[Match]``. Resposta ilegível vira lista vazia."""
    try:
        linhas = (data or {}).get("matches") or []
    except Exception:  # noqa: BLE001
        return []
    out: list[Match] = []
    for row in linhas:
        cid = str(row.get("contactId") or "")
        if not cid:
            continue
        out.append(Match(cid, str(row.get("slug") or ""),
                         str(row.get("value") or ""),
                         str(row.get("matchedBy") or "variant")))
    return out


async def resolve_mapped(http, *, phone: str | None, extras: dict | None = None,
                         contact_type: str = "whatsapp") -> list[Match]:
    """Tenta o TELEFONE e todo campo conectado que seja identificador no CDP.

    ``extras`` é ``{slug_no_trackify: valor_no_whatsbot}``, montado a partir dos
    mapeamentos ativos (ver ``field_map.identifier_hints``). Assim, conectar um
    campo na tela passa a valer também para ENCONTRAR o cadastro, e não só para
    copiar o valor depois — que era a pergunta natural de quem configura.
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

    return await resolve_candidates(http, candidatos)


async def resolve(http, *, phone: str | None = None, email: str | None = None,
                  cpf: str | None = None, telegram_id: str | None = None,
                  contact_type: str = "whatsapp") -> list[Match]:
    """Resolve o contato tentando os identificadores na prioridade do Trackify.

    Devolve lista porque o mesmo número pode estar em mais de um contato (medido:
    5 números com 2 cadastros) — quem chama decide se mostra um seletor ou se
    desiste.
    """
    candidatos: dict[str, str] = {}
    if email:
        candidatos[SLUG_EMAIL] = email
    if contact_type == "telegram":
        candidatos[SLUG_TELEGRAM] = (telegram_id or phone or "")
    elif phone:
        candidatos[SLUG_WHATSAPP] = phone
    if cpf:
        candidatos[SLUG_CPF] = cpf
    return await resolve_candidates(http, {k: v for k, v in candidatos.items() if v})


async def resolve_by_email(http, value: str | None) -> list[Match]:
    return await resolve_candidates(http, {SLUG_EMAIL: value or ""})


async def resolve_by_cpf(http, value: str | None) -> list[Match]:
    return await resolve_candidates(http, {SLUG_CPF: value or ""})


async def resolve_by_phone(http, value: str | None) -> list[Match]:
    return await resolve_candidates(http, {SLUG_WHATSAPP: value or ""})


def canonical_identity(*, phone: str | None, email: str | None = None,
                       contact_type: str = "whatsapp") -> dict[str, str]:
    """Identificadores a ENVIAR quando o contato não existe no CDP (criação).

    Só chaves com valor — a ingestão devolve 422 se nenhum identificador
    sobreviver, e mandar string vazia não ajuda ninguém. Puro, sem rede.
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
