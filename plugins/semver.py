"""Semver range parsing + matching for plugin manifests (plano 23 Fase C4).

Extracted verbatim from ``plugins.manifest`` so the WhatsBot-API-version
compatibility check (and the manifest's plain-semver validation) lives in one
place. ``manifest.py`` re-exports these names, so existing import paths keep
working.

Supports the restricted subset the manifest actually uses:

* ``_is_semver`` — a strict ``MAJOR.MINOR.PATCH`` (optional prerelease/build).
* ``parse_simple_semver`` — best-effort ``(major, minor, patch)`` tuple.
* ``check_api_compat`` — ``*`` | plain version | comma-separated comparators
  (``>=, <=, >, <, ==, !=``) such as ``">=1.0,<2.0"``.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# The running WhatsBot plugin API version — FONTE ÚNICA.
# ``manifest.WHATSBOT_API_VERSION`` é re-export por valor, então os dois módulos
# nunca discordam (travado por ``test_manifest_reexport_stays_in_sync``).
#
# Toda mudança na superfície declarada da API de plugins bumpa este número, e o
# número nunca viaja sem a prosa: a entrada correspondente vai em
# ``docs/PLUGIN_API_CHANGELOG.md``. O que conta como MAJOR/MINOR/PATCH está lá e
# no CLAUDE.md ("Versionamento da API de plugins"); quem faz valer é
# ``tests/contracts/test_plugin_api_surface.py``, que compara a superfície viva
# com ``tests/goldens/plugin_api_surface.json`` e SE RECUSA a regenerar o
# snapshot enquanto esta constante não tiver andado.
#
# ⚠️ MAJOR é tranche, não decisão de commit: os 36 manifests do parque declaram
# ``">=1.0,<2.0"``, então um ``2.0.0`` faria TODOS deixarem de carregar de uma
# vez (o loader retorna antes de registrar o plugin) — inclusive o ``gowa``
# bundled, que é o único canal auto-instalado.
#
# 1.2.0: ADITIVA — a costura de serviço plugin→plugin (``entry.services`` +
# ``uses_services``, ver plugins/services.py) e ``plugins.context.get_loop()``.
# Todo manifest do parque declara ``">=1.0,<2.0"`` e segue compatível. ⚠️ Um
# plugin que declare ``">=1.2"`` FALHA DURO num core anterior (o manifest levanta
# ⇒ load_error), então só declare quando o plugin for inútil sem os serviços.
#
# 1.3.0: ADITIVA — ``message.saved`` e ``message.sent`` passam a carregar
# ``channel_id`` e ``conversation_id`` (plano 123 F2). Antes disso o plugin só
# recebia ``phone`` e tinha de adivinhar a thread por telefone — o que, num
# contato atendido em dois canais (ou num par duplicado 12↔13 dígitos), escolhia
# a conversa errada. Campo ACRESCENTADO a payload existente: quem não lê não vê
# diferença. ``conversation_id`` pode vir ausente/``None`` onde o id não está no
# escopo do call site (retry, resposta da IA) — o consumidor tem de tolerar.
# 1.4.0: ADITIVA — ``screens[].width`` no manifest (``normal``/``wide``/``full``,
# só para screen ``config: true``). Campo OPCIONAL: screen que não o declara
# continua byte-idêntica, e o parser de um core anterior descarta a chave (o dict
# de screen é whitelist) ⇒ modal no tamanho de sempre. Não declare ``">=1.4"`` só
# por causa dele.
#
# 1.5.0: ADITIVA — ``ChannelCapabilities.ai_window_hours`` (default 0 = sem
# restrição, comportamento idêntico ao de antes) e o avaliador
# ``OutboundRouter.ai_window_open``. Declara a janela dentro da qual a IA do canal
# pode falar, que NÃO é derivável das outras duas: nos canais Meta o operador
# escreve por 7 dias com a tag HUMAN_AGENT enquanto o ``filters.py`` do plugin já
# calou a IA às 24h. Provider que não a declara não muda em nada. ⚠️ O plugin que
# a declarar deve fazê-lo condicionalmente (``dataclasses.fields``) se quiser
# continuar carregando num core anterior — passar o kwarg a um
# ``ChannelCapabilities`` sem o campo levanta ``TypeError`` no import.
WHATSBOT_API_VERSION = "1.6.0"

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+].*)?$")
_COMPARATOR_RE = re.compile(r"^(>=|<=|>|<|==|!=)\s*(\d+(?:\.\d+){0,2}(?:[-+].*)?)$")


def is_semver(value: str) -> bool:
    """True for a strict ``MAJOR.MINOR.PATCH`` (optional ``-pre``/``+build``)."""
    return bool(_SEMVER_RE.match(value))


def parse_simple_semver(value: str) -> tuple[int, int, int]:
    """Parse ``MAJOR.MINOR.PATCH`` ignoring prerelease/build.

    Missing components default to 0; non-numeric input degrades to ``(0, 0, 0)``.
    """
    core = re.split(r"[-+]", value, maxsplit=1)[0]
    parts = core.split(".")
    if len(parts) < 3:
        parts += ["0"] * (3 - len(parts))
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return 0, 0, 0


def check_api_compat(spec: str, current: str = WHATSBOT_API_VERSION) -> bool:
    """Check whether ``current`` satisfies the constraint expression ``spec``.

    Supports ``*``, plain version (``1.0.0``), and comma-separated comparators
    ``>=, <=, >, <, ==, !=`` such as ``">=1.0,<2.0"``.
    """
    spec = (spec or "*").strip()
    if spec in ("", "*"):
        return True
    cur = parse_simple_semver(current)
    # plain version → exact match on MAJOR.MINOR.PATCH
    if is_semver(spec):
        return cur == parse_simple_semver(spec)
    parts = [p.strip() for p in spec.split(",")]
    for part in parts:
        m = _COMPARATOR_RE.match(part)
        if not m:
            logger.warning("Unrecognized version constraint: %r", part)
            return False
        op, ver = m.group(1), m.group(2)
        target = parse_simple_semver(ver)
        if op == ">=" and not cur >= target: return False
        if op == "<=" and not cur <= target: return False
        if op == ">"  and not cur >  target: return False
        if op == "<"  and not cur <  target: return False
        if op == "==" and not cur == target: return False
        if op == "!=" and not cur != target: return False
    return True
