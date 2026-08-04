"""Tradução de valores entre o WhatsBot e o Trackify. PURO: sem banco, sem rede.

O WhatsBot guarda atributo personalizado TIPADO no JSON do contato (número é
número, checkbox é bool); o Trackify guarda TUDO como string em
``contact_field_values.value`` (coluna NOT NULL). Este módulo é a fronteira
entre os dois vocabulários e é a única coisa que precisa ser testada para
confiar que um ida-e-volta não corrompe o dado.

Duas convenções de retorno, propositalmente DIFERENTES nas duas direções,
porque "limpar" se escreve diferente de cada lado:

* ``to_trackify`` → ``(texto, None)``. Texto ``""`` é a LIMPEZA — é o que o
  ``PUT /api/v1/contacts/:id`` interpreta como "apague a linha EAV". Erro é
  ``(None, "motivo")``.
* ``to_whatsbot`` → ``(valor, None)``. Valor ``None`` é a LIMPEZA — é o que
  ``custom_attribute_repo.set_values`` interpreta como "remova a chave". Erro
  também é ``(None, "motivo")``, então quem chama TEM que olhar o segundo termo,
  nunca só o primeiro.

Regra dura de anti-oscilação: ``normalize_for_hash`` aplica exatamente a mesma
normalização que ``to_trackify`` (strip). Se as duas divergirem, um valor com
espaço à direita vira "mudou" em todo ciclo e os dois lados ficam se escrevendo
para sempre.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from db.repositories.custom_attribute_validate import validate_value

# ── Vocabulários ─────────────────────────────────────────────────────────

# Verdadeiro/falso ACEITOS na volta. Fora desta lista o valor é RECUSADO, nunca
# assumido como falso: a checagem "in ('1','true','sim')" do core devolveria
# False para "talvez" e gravaria uma mentira em silêncio.
_TRUE_WORDS = {"1", "true", "yes", "y", "sim", "s", "on", "t", "verdadeiro"}
_FALSE_WORDS = {"0", "false", "no", "n", "nao", "não", "off", "f", "falso"}

_DATE_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_DATE_BR = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
# Agrupamento pt-BR: 1.500,50 — o CDP guarda valor monetário assim de verdade.
_PTBR_GROUPED = re.compile(r"^-?\d{1,3}(\.\d{3})+(,\d+)?$")
_MONEY_NOISE = re.compile(r"[\s  ]|R\$", re.IGNORECASE)

# Compatibilidade de tipo — CONSULTIVA. Um par "bad" salva com aviso vermelho:
# quem descobre o schema do CDP é o plugin, não quem o desenhou. A barreira que
# de fato protege é o ``tk_regex`` aplicado valor a valor, e essa não erra.
_COMPAT: dict[str, tuple[str, ...]] = {
    "text": ("text", "string", "textarea", "email", "phone", "url", "select"),
    "number": ("number", "integer", "decimal", "currency"),
    "date": ("date", "datetime"),
    "list": ("select", "text", "string"),
    "checkbox": ("boolean", "checkbox", "text", "string"),
    "link": ("url", "text", "string"),
}
_LOSSY: dict[str, tuple[str, ...]] = {
    "number": ("text", "string"),
    "list": ("text", "string"),
    "checkbox": ("text", "string"),
    "date": ("text", "string"),
}


# ── Normalização / hash ──────────────────────────────────────────────────

def normalize_for_hash(value: Any) -> str:
    """Forma canônica usada para comparar os dois lados.

    Tem que casar com o que ``to_trackify`` emite, senão a comparação acusa
    diferença eterna. ``None`` e ``""`` colapsam na MESMA string vazia: os dois
    significam "não tem valor", e distinguí-los aqui faria um contato sem o
    campo divergir para sempre de um contato com o campo apagado.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _fmt_number(float(value))
    return str(value).strip()


def hash_value(value: Any) -> str:
    """Hash curto da forma canônica. Guardamos hash, NUNCA o valor.

    ``plugin_trackify_field_state`` acompanha e-mail e CPF de toda a base; uma
    terceira cópia em claro do dado pessoal seria criada só para o motor
    comparar igualdade, o que não se justifica.
    """
    return hashlib.sha1(normalize_for_hash(value).encode("utf-8")).hexdigest()[:16]


def same(a: Any, b: Any) -> bool:
    return normalize_for_hash(a) == normalize_for_hash(b)


def _fmt_number(v: float) -> str:
    """Decimal simples, ponto, SEM notação científica.

    ``str(1e16)`` e ``"%g"`` produzem ``1e+16``; o ``Number(trimmed)`` do lado
    deles engole isso com perda e o valor volta diferente do que saiu — o
    ida-e-volta quebra justo nos números grandes.
    """
    if v != v or v in (float("inf"), float("-inf")):  # NaN / infinito
        return ""
    if float(v).is_integer() and abs(v) < 1e15:
        return str(int(v))
    s = f"{v:.10f}".rstrip("0").rstrip(".")
    return s or "0"


# ── WhatsBot → Trackify ──────────────────────────────────────────────────

def to_trackify(mapping: dict, definition: dict, value: Any) -> tuple[str | None, str | None]:
    """Codifica um valor do WhatsBot como a string que o Trackify guarda.

    ``("", None)`` é limpeza legítima. ``(None, msg)`` é recusa.
    """
    wb_type = (definition.get("type") or "text").lower()

    if value is None:
        out = ""
    elif wb_type == "checkbox":
        out = "true" if bool(value) else "false"
    elif wb_type == "number":
        try:
            out = _fmt_number(float(value))
        except (TypeError, ValueError):
            return None, "valor não é um número"
        if not out:
            return None, "número inválido (NaN/infinito)"
    else:
        # text / link / date / list — o core já validou a FORMA na escrita
        # (data casa AAAA-MM-DD, link começa com http, opção pertence à lista),
        # então aqui é só serializar.
        out = str(value).strip()

    if out and (mapping.get("tk_regex") or ""):
        try:
            if not re.search(mapping["tk_regex"], out):
                tipo = mapping.get("tk_field_type") or "campo"
                return None, f"valor não casa com o formato do tipo '{tipo}' no Trackify"
        except re.error:
            pass  # regex torta no CDP não pode barrar a escrita
    return out, None


# ── Trackify → WhatsBot ──────────────────────────────────────────────────

def to_whatsbot(mapping: dict, definition: dict, raw: Any) -> tuple[Any, str | None]:
    """Interpreta a string do Trackify como valor tipado do WhatsBot.

    ``(None, None)`` é limpeza legítima. ``(None, msg)`` é recusa — quem chama
    NÃO pode tratar os dois casos igual.
    """
    if raw is None:
        return None, None
    s = str(raw).strip()
    if not s:
        return None, None

    wb_type = (definition.get("type") or "text").lower()
    pre: Any = s

    if wb_type == "checkbox":
        low = s.lower()
        if low in _TRUE_WORDS:
            pre = True
        elif low in _FALSE_WORDS:
            pre = False
        else:
            return None, f"'{s}' não é verdadeiro nem falso"

    elif wb_type == "number":
        cleaned = _MONEY_NOISE.sub("", s)
        if _PTBR_GROUPED.match(cleaned):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        pre = cleaned  # o resto (vírgula decimal simples) o core já resolve

    elif wb_type == "date":
        pre, err = _parse_date(s)
        if err:
            return None, err

    normalized, err = validate_value(definition, pre)
    if err:
        return None, err
    return normalized, None


def _parse_date(s: str) -> tuple[str | None, str | None]:
    """Aceita AAAA-MM-DD, dd/mm/aaaa e ISO-8601 com hora.

    ``dd/mm/aaaa`` não é hipótese: o próprio plugin já documenta esse formato
    como valor REAL lido da produção do CDP. Do ISO com hora tomamos a parte da
    data **como veio** (UTC na prática, já que o CDP serializa em UTC) — decisão
    explícita: um ``2026-08-03T23:30:00Z`` vira ``2026-08-03``, que é dia 4 para
    quem lê em horário de Brasília.
    """
    m = _DATE_ISO.match(s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", None
    m = _DATE_BR.match(s)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}", None
    return None, f"'{s}' não é uma data reconhecível"


# ── Compatibilidade (consultiva, avaliada ao configurar) ─────────────────

def compat(wb_type: str, tk_field_type: str | None) -> str:
    """``ok`` | ``lossy`` | ``bad`` | ``unknown``.

    ``unknown`` quando o tipo do lado do CDP não resolveu — a tela NÃO deve
    desenhar aviso nenhum nesse caso. Gritar por causa de um join que falhou
    ensina o operador a ignorar os avisos que importam.
    """
    wb = (wb_type or "text").lower()
    tk = (tk_field_type or "").strip().lower()
    if not tk:
        return "unknown"
    if tk in _LOSSY.get(wb, ()):
        return "lossy"
    if tk in _COMPAT.get(wb, ()):
        return "ok"
    return "bad"
