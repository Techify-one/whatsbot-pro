"""Campos contribuídos por OUTROS plugins ao construtor de regras.

O `retornos` não conhece plugin nenhum por nome: ele PUBLICA dois seams no bus de
filtros do core e apenas consome o que voltar. Um plugin ativo declara os próprios
campos; desativado, eles somem do select sozinhos (o bus derruba os filters no
restart) e uma condição que os cite volta a ser avaliada como campo desconhecido
(= falsa), sem quebrar a configuração salva.

* ``filter.retornos.campos`` — **catálogo**. Valor = ``{"grupos": [...], "campos": [...],
  "opcoes": {...}}``; o plugin ACRESCENTA os seus e devolve o dict::

      def campos(ctx, meta):
          meta["grupos"].append({"id": "protocolos", "label": "Protocolos",
                                 "plugin_id": "protocolos"})
          meta["campos"].append({"id": "protocolos.status", "label": "Status do protocolo",
                                 "grupo": "protocolos", "tipo": "enum",
                                 "options": [{"value": "aberto", "label": "Aberto"}]})
          return meta

* ``filter.retornos.contexto`` — **valores**. Valor = ``{campo_id: valor}`` (começa vazio);
  ``ctx.extras`` traz ``conversation``/``contact``/``conversation_id``/``contact_id``/
  ``phone``/``channel_id``/``now``. O plugin preenche os ids que declarou.

Os dois nomes NÃO estão em ``KNOWN_FILTERS`` do core (que cataloga só os filtros que o
CORE aplica) — o bus loga um WARNING informativo ao registrá-los, e é esperado: filtro
publicado por plugin é legal, ver ``plugins/events.py``.

Tudo aqui é **fail-open e defensivo**: plugin que devolve lixo é descartado campo a
campo, e qualquer erro deixa o catálogo/contexto do core intactos.
"""

from __future__ import annotations

import logging
import time

from . import catalog

logger = logging.getLogger("plugin.retornos")

FILTRO_CAMPOS = "filter.retornos.campos"
FILTRO_CONTEXTO = "filter.retornos.contexto"

# O catálogo externo é relido no máximo a cada TTL segundos (o ciclo do dispatcher roda
# a cada 60s e a UI força `force=True` ao abrir a tela).
TTL = 30.0

_cache: dict = {"ts": 0.0, "dados": {"grupos": [], "campos": [], "opcoes": {}}}


def _vazio() -> dict:
    return {"grupos": [], "campos": [], "opcoes": {}}


def _opcoes_lista(bruto) -> list[dict]:
    """``["a", {"value","label"}, …]`` → ``[{"value","label"}, …]`` (descarta o resto)."""
    saida: list[dict] = []
    for o in bruto or []:
        if isinstance(o, dict):
            valor = o.get("value")
            if valor is None:
                continue
            saida.append({"value": str(valor), "label": str(o.get("label") or valor)})
        elif isinstance(o, (str, int, float)):
            saida.append({"value": str(o), "label": str(o)})
    return saida


def _normalizar(bruto) -> dict:
    """Sanitiza o que os plugins devolveram — nada entra no catálogo sem passar por aqui.

    Regras: grupo não pode colidir com grupo do core; campo não pode colidir com campo do
    core (o core sempre ganha) nem ficar órfão de grupo; tipo desconhecido vira ``text``.
    """
    if not isinstance(bruto, dict):
        return _vazio()

    grupos: list[dict] = []
    campos: list[dict] = []
    opcoes: dict[str, list] = {}
    ids_grupos: set[str] = set()
    ids_campos: set[str] = set()

    for g in (bruto.get("grupos") or []):
        if not isinstance(g, dict):
            continue
        gid = str(g.get("id") or "").strip()
        if not gid or gid in catalog.GRUPOS or gid in ids_grupos:
            continue
        grupos.append({"id": gid, "label": str(g.get("label") or gid),
                       "origem": "plugin", "plugin_id": str(g.get("plugin_id") or "")})
        ids_grupos.add(gid)

    for c in (bruto.get("campos") or []):
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or "").strip()
        grupo = str(c.get("grupo") or "").strip()
        if not cid or cid in ids_campos or catalog.campo_core(cid) is not None:
            continue
        if grupo not in ids_grupos:
            logger.debug("retornos: campo externo %r descartado (grupo %r não declarado)",
                         cid, grupo)
            continue
        tipo = str(c.get("tipo") or "text")
        if tipo not in catalog.OPERADORES_POR_TIPO:
            tipo = "text"
        campo = {"id": cid, "label": str(c.get("label") or cid), "grupo": grupo,
                 "tipo": tipo, "origem": "plugin"}
        origem_opcoes = c.get("options")
        if isinstance(origem_opcoes, list):
            campo["options"] = _opcoes_lista(origem_opcoes)
        elif isinstance(origem_opcoes, str) and origem_opcoes.strip():
            campo["options"] = origem_opcoes.strip()  # chave em `opcoes`
        campos.append(campo)
        ids_campos.add(cid)

    for chave, lista in (bruto.get("opcoes") or {}).items():
        if isinstance(lista, list):
            opcoes[str(chave)] = _opcoes_lista(lista)

    return {"grupos": grupos, "campos": campos, "opcoes": opcoes}


def _guardar(bruto) -> dict:
    """Normaliza, registra no catálogo (o motor precisa do TIPO) e cacheia."""
    dados = _normalizar(bruto)
    catalog.registrar_externos(dados["campos"])
    _cache["dados"] = dados
    _cache["ts"] = time.time()
    return dados


def _fresco(force: bool) -> bool:
    return not force and (time.time() - float(_cache.get("ts") or 0.0)) < TTL


def cache() -> dict:
    """Último snapshot conhecido (sem tocar no bus) — usado por quem não pode bloquear."""
    return _cache["dados"]


def snapshot(*, force: bool = False) -> dict:
    """Catálogo externo — caminho SÍNCRONO (thread do dispatcher/eventos).

    ``apply_filter_sync`` é inerte na thread do event loop; nesse caso devolve o último
    snapshot conhecido em vez de bloquear (as rotas usam :func:`asnapshot`).
    """
    if _fresco(force):
        return _cache["dados"]
    try:
        from plugins.events import apply_filter_sync
        return _guardar(apply_filter_sync(FILTRO_CAMPOS, _vazio(), {}))
    except Exception:  # noqa: BLE001 — catálogo externo nunca derruba o ciclo
        logger.debug("retornos: snapshot do catálogo externo falhou", exc_info=True)
        return _cache["dados"]


async def asnapshot(*, force: bool = False) -> dict:
    """Catálogo externo — caminho ASSÍNCRONO (rotas, na thread do event loop)."""
    if _fresco(force):
        return _cache["dados"]
    try:
        from plugins.events import apply_filter
        return _guardar(await apply_filter(FILTRO_CAMPOS, _vazio(), {}))
    except Exception:  # noqa: BLE001
        logger.debug("retornos: snapshot do catálogo externo falhou", exc_info=True)
        return _cache["dados"]


def _extras(*, conv: dict, contact: dict | None, configuracao: dict | None,
            controle: dict | None, now: float) -> dict:
    conv = conv or {}
    return {
        "conversation": dict(conv),
        "contact": dict(contact) if contact else None,
        "conversation_id": conv.get("id"),
        "contact_id": conv.get("contact_id") or (contact or {}).get("id"),
        "phone": conv.get("contact_phone") or (contact or {}).get("phone"),
        "channel_id": conv.get("channel_id"),
        "configuracao_id": (configuracao or {}).get("id"),
        "controle_id": (controle or {}).get("id"),
        "now": now,
    }


def _limpar(valores) -> dict:
    """Só aceita dict; descarta chaves reservadas (``__*``) e colisões com campo do core."""
    if not isinstance(valores, dict):
        return {}
    return {str(k): v for k, v in valores.items()
            if not str(k).startswith("__") and catalog.campo_core(str(k)) is None}


def contexto(*, conv: dict, contact: dict | None = None, configuracao: dict | None = None,
             controle: dict | None = None, now: float | None = None) -> dict:
    """Valores dos campos externos para UMA conversa — caminho SÍNCRONO."""
    agora = float(now if now is not None else time.time())
    snapshot()
    try:
        from plugins.events import apply_filter_sync
        return _limpar(apply_filter_sync(
            FILTRO_CONTEXTO, {},
            _extras(conv=conv, contact=contact, configuracao=configuracao,
                    controle=controle, now=agora)))
    except Exception:  # noqa: BLE001
        logger.debug("retornos: contexto externo falhou", exc_info=True)
        return {}


async def acontexto(*, conv: dict, contact: dict | None = None,
                    configuracao: dict | None = None, controle: dict | None = None,
                    now: float | None = None) -> dict:
    """Valores dos campos externos para UMA conversa — caminho ASSÍNCRONO (rotas)."""
    agora = float(now if now is not None else time.time())
    await asnapshot()
    try:
        from plugins.events import apply_filter
        return _limpar(await apply_filter(
            FILTRO_CONTEXTO, {},
            _extras(conv=conv, contact=contact, configuracao=configuracao,
                    controle=controle, now=agora)))
    except Exception:  # noqa: BLE001
        logger.debug("retornos: contexto externo falhou", exc_info=True)
        return {}
