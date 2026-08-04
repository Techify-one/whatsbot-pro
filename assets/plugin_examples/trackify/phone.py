"""Formas de um telefone brasileiro para casar com o CDP (Trackify).

PURO — sem banco, sem rede, sem settings. É o módulo mais testável do plugin e
o que decide se a tela acha o contato (leitura) e se a ingestão gruda no contato
certo em vez de forkar um novo (escrita).

O problema, medido em produção (plano 94, F0):

    WhatsBot ``contacts.phone``   →  majoritariamente 12 dígitos, SEM ``+``
                                     (``5513991198852`` — formato do JID do WhatsApp)
    Trackify ``whatsapp``         →  90% com ``+`` e 13 dígitos
                                     (``+5513991198852``); só 0,05% mascarados;
                                     e há cadastro em forma NACIONAL
                                     (``6492973092``), vindo de formulário ou
                                     planilha, sem o código do país

Duas responsabilidades DIFERENTES, e confundi-las quebra o CDP:

* :func:`lookup_candidates` — para BUSCAR. Sobregerar é inofensivo: um candidato
  a mais só não casa nada. Usa ``br_phone_variants`` do core.
* :func:`canonical_e164` — para GRAVAR quando o contato não existe no Trackify.
  Aqui sobregerar é ERRO: restaurar o 9º dígito num telefone FIXO inventa um
  número que não existe e crava esse lixo no CDP para sempre (contato do
  Trackify só tem soft delete). Por isso o 9 só volta em número MÓVEL.
"""

from __future__ import annotations

import re

# Reuso do core (plano 23 Fase E2) — a regra do 9º dígito já vive lá e é a mesma
# que o ``contact_repo`` usa para casar contato. Divergir criaria dois conceitos
# de "mesmo número" dentro do próprio WhatsBot.
from channels.br_phone import br_phone_variants

# Sufixos de JID (Evolution/Baileys/GOWA). ``contacts.phone`` normalmente já vem
# só com dígitos, mas payload de evento nem sempre — defensivo de graça.
_JID_AT = "@"

_DIGITS_RE = re.compile(r"\D")


def digits_only(value: str | None) -> str:
    """Só os dígitos de ``value`` (descarta o sufixo de JID, máscara, ``+``)."""
    if not value:
        return ""
    v = str(value)
    if _JID_AT in v:
        v = v[: v.index(_JID_AT)]
    return _DIGITS_RE.sub("", v)


def looks_like_phone(value: str | None) -> bool:
    """``False`` para o que NÃO é telefone, por mais dígitos que tenha.

    O ``contacts.phone`` do WhatsBot é um identificador GENÉRICO por canal, não
    necessariamente um telefone: o canal ``website`` grava id de sessão
    (``wsess_f0qD7g7e3TmKcZwG…``) e um JID de grupo grava ``120363…@g.us``.
    Sem este guard, ``digits_only`` extrairia os dígitos soltos de um id de
    sessão e sairia procurando (ou pior, na escrita, CRIANDO) um contato no CDP
    a partir de lixo — e contato no Trackify só tem soft delete.
    """
    if not value:
        return False
    v = str(value)
    if v.lower().endswith("@g.us"):        # grupo não é pessoa
        return False
    if _JID_AT in v:
        v = v[: v.index(_JID_AT)]
    # Telefone só tem dígitos e pontuação de telefone. Letra ou '_' reprova.
    if not re.fullmatch(r"[\d+()\s.\-]+", v or ""):
        return False
    d = _DIGITS_RE.sub("", v)
    return 8 <= len(d) <= 15               # E.164 vai até 15


def is_br_mobile(d: str) -> bool:
    """``True`` quando ``d`` (só dígitos) é um CELULAR brasileiro.

    Móvel no Brasil tem 9 dígitos locais começando em 9 (ou 8 dígitos começando
    em 6-9, na grafia antiga sem o nono). Fixo começa em 2-5 — e é exatamente o
    caso em que NÃO se pode inserir o 9.
    """
    if not d.startswith("55"):
        return False
    local = d[4:]  # 55 + DDD(2) + resto
    if len(local) == 9:
        return local[0] == "9"
    if len(local) == 8:
        return local[0] in "6789"
    return False


def lookup_candidates(phone: str | None) -> list[str]:
    """Todas as grafias a testar em ``contact_field_values.value``.

    Devolve dígitos e ``+dígitos`` para cada variante do core, em ordem estável
    e sem repetição. Feito para ``value = ANY(:cands)``, que usa o índice
    ``idx_cfv_identifier_lookup`` — uma consulta com ``regexp_replace`` nos dois
    lados não usaria (medido: 0,085 ms com índice).

    Lista vazia quando não há dígitos: o caller deve tratar como "não procurar",
    nunca como "procurar tudo".
    """
    if not looks_like_phone(phone):
        return []
    d = digits_only(phone)
    if not d:
        return []
    out: list[str] = []

    def add(form: str) -> None:
        if form and form not in out:
            out.append(form)

    for variant in br_phone_variants(d):
        add(variant)
        add("+" + variant)
        # Forma NACIONAL, sem o código do país. ``br_phone_variants`` só alterna
        # o 9º dígito e mantém o 55 sempre — mas o CDP recebe número de
        # formulário e de planilha, onde "6492973092" (DDD + 8 dígitos, sem o 55)
        # é comum. Sem esta variante, esse cadastro nunca casava.
        if variant.startswith("55") and len(variant) in (12, 13):
            # ⚠️ SEM o "+": "+6492973092" é um número VÁLIDO da Nova Zelândia
            # (+64), e casar com ele grudaria os dados do cliente no cadastro de
            # outra pessoa. A forma nacional só faz sentido crua.
            add(variant[2:])
    return out


def canonical_e164(phone: str | None) -> str:
    """Forma canônica para CRIAR o contato no Trackify: ``+`` + dígitos, E.164.

    Escolhida por medição: 90% dos ``whatsapp`` do CDP já estão com ``+``, então
    é a grafia que maximiza a chance de uma fonte futura (formulário, anúncio,
    digitação humana) cair na MESMA linha em vez de forkar.

    O 9º dígito só é restaurado em número **móvel** — ``br_phone_variants``
    insere o 9 em qualquer número de 12 dígitos com prefixo 55, o que num fixo
    produziria um número inexistente.

    Devolve ``""`` quando o valor não é um telefone (ver :func:`looks_like_phone`)
    — é o que impede um id de sessão do widget de virar contato no CDP.
    """
    if not looks_like_phone(phone):
        return ""
    d = digits_only(phone)
    if not d:
        return ""
    if d.startswith("55") and len(d) == 12 and is_br_mobile(d):
        d = d[:4] + "9" + d[4:]
    return "+" + d
