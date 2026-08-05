"""Campos do Protocolos no construtor de regras do plugin `retornos`.

O `retornos` publica dois seams no bus de filtros e não conhece plugin nenhum por nome;
aqui é o lado de cá do contrato (ver `assets/plugin_examples/retornos/contrib.py`):

* ``filter.retornos.campos``   → declara os GRUPOS/CAMPOS que aparecem no select, sob o
  cabeçalho "Configurações (outros plugins)".
* ``filter.retornos.contexto`` → devolve ``{campo_id: valor}`` da conversa que está sendo
  avaliada, para o motor de regras comparar.

Dois grupos, porque o plugin tem dois ESCOPOS de campo configurável com chaves que se
repetem (``resultado`` existe nos dois): **Protocolos** (o protocolo do contato) e
**Protocolos · Atendimento** (o ciclo daquela conversa).

Protocolo de referência = o ABERTO do contato; **na falta dele, o último FECHADO** — é o
que permite a regra clássica de follow-up ("último protocolo com resultado 'Sem retorno'").
O campo ``protocolos.tem_aberto`` distingue os dois casos.

Tudo é defensivo: uma exceção aqui deixa o catálogo/contexto do `retornos` intacto (o bus
isola o filter e o valor passa adiante).
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from plugins.context import make_plugin_db

from . import logic

logger = logging.getLogger("plugin.protocolos")

GRUPO_PROTOCOLO = "protocolos"
GRUPO_ATENDIMENTO = "protocolos_atendimento"

# Prefixo dos campos CONFIGURÁVEIS (os criados pelo operador na tela de configuração).
PREFIXO_PROTOCOLO = "protocolos.campo."
PREFIXO_ATENDIMENTO = "protocolos.atend.campo."

# Chave da lista de atendentes em `metadata.opcoes` (própria, sem depender de como o
# `retornos` nomeia a lista dele).
OPCOES_ATENDENTES = "protocolos_atendentes"

_SIM_NAO = [{"value": "sim", "label": "Sim"}, {"value": "nao", "label": "Não"}]

_ABERTO_POR = [
    {"value": "contact", "label": "Contato"},
    {"value": "agent", "label": "Atendente"},
    {"value": "ia", "label": "IA"},
    {"value": "system", "label": "Sistema"},
]

# Tipo do campo configurável (protocolos) → tipo do catálogo de regras (retornos).
_TIPO_REGRA = {
    "select": "enum", "radio": "enum", "checkboxes": "multi-select",
    "checkbox": "enum", "number": "number", "date": "date",
    "text": "text", "textarea": "text",
}


# ── Catálogo ──────────────────────────────────────────────────────────────────

def _campo(cid: str, label: str, grupo: str, tipo: str, options=None) -> dict:
    campo = {"id": cid, "label": label, "grupo": grupo, "tipo": tipo}
    if options is not None:
        campo["options"] = options
    return campo


def _campos_configuraveis(scope: str, grupo: str, prefixo: str) -> list[dict]:
    """Um campo de regra por rótulo configurável do escopo (o "Atendente" fixo fica de
    fora — já é exposto como campo nativo, com o atendente EFETIVO)."""
    saida: list[dict] = []
    for d in logic.get_field_defs(scope):
        tipo_def = str(d.get("type") or "text")
        if tipo_def == "atendente":
            continue
        chave = str(d.get("key") or "")
        if not chave:
            continue
        tipo = _TIPO_REGRA.get(tipo_def, "text")
        options = None
        if tipo_def == "checkbox":
            options = list(_SIM_NAO)
        elif tipo_def in ("select", "radio", "checkboxes"):
            options = [str(o) for o in (d.get("options") or [])]
        saida.append(_campo(prefixo + chave, str(d.get("label") or chave), grupo, tipo,
                            options))
    return saida


def _atendentes() -> list[dict]:
    try:
        from db.repositories import user_repo
        return [{"value": str(u["id"]), "label": u.get("name") or u.get("email") or str(u["id"])}
                for u in (user_repo.list_all() or [])]
    except Exception:  # noqa: BLE001
        logger.debug("protocolos: lista de atendentes indisponível", exc_info=True)
        return []


def campos(ctx, meta: dict) -> dict:
    """``filter.retornos.campos`` — acrescenta os grupos/campos/opções do Protocolos."""
    try:
        meta.setdefault("grupos", []).append(
            {"id": GRUPO_PROTOCOLO, "label": "Protocolos", "plugin_id": logic.PLUGIN_ID})
        meta["grupos"].append(
            {"id": GRUPO_ATENDIMENTO, "label": "Protocolos · Atendimento",
             "plugin_id": logic.PLUGIN_ID})

        lista = meta.setdefault("campos", [])
        lista.extend([
            _campo("protocolos.tem_aberto", "Tem protocolo aberto", GRUPO_PROTOCOLO,
                   "enum", list(_SIM_NAO)),
            _campo("protocolos.status", "Status do protocolo", GRUPO_PROTOCOLO, "enum",
                   [{"value": "aberto", "label": "Aberto"},
                    {"value": "fechado", "label": "Fechado"}]),
            _campo("protocolos.atendente", "Atendente do protocolo", GRUPO_PROTOCOLO,
                   "select", OPCOES_ATENDENTES),
            _campo("protocolos.aberto_por", "Protocolo aberto por", GRUPO_PROTOCOLO,
                   "enum", list(_ABERTO_POR)),
            _campo("protocolos.horas_desde_abertura", "Horas desde a abertura do protocolo",
                   GRUPO_PROTOCOLO, "number"),
            _campo("protocolos.dias_desde_abertura", "Dias desde a abertura do protocolo",
                   GRUPO_PROTOCOLO, "number"),
            _campo("protocolos.horas_desde_fechamento",
                   "Horas desde o fechamento do protocolo", GRUPO_PROTOCOLO, "number"),
            _campo("protocolos.data_fechamento", "Dia de fechamento do protocolo",
                   GRUPO_PROTOCOLO, "date"),
            _campo("protocolos.n_atendimentos", "Nº de atendimentos do protocolo",
                   GRUPO_PROTOCOLO, "number"),
            _campo("protocolos.avaliacao_nota", "Nota da última avaliação (1 a 5)",
                   GRUPO_PROTOCOLO, "number"),
        ])
        lista.extend(_campos_configuraveis("protocolo", GRUPO_PROTOCOLO, PREFIXO_PROTOCOLO))

        lista.extend([
            _campo("protocolos.atend.status", "Situação do atendimento (ciclo)",
                   GRUPO_ATENDIMENTO, "enum",
                   [{"value": "em_andamento", "label": "Em andamento"},
                    {"value": "encerrado", "label": "Encerrado"}]),
            _campo("protocolos.atend.atendente", "Atendente do atendimento",
                   GRUPO_ATENDIMENTO, "select", OPCOES_ATENDENTES),
            _campo("protocolos.atend.aberto_por", "Atendimento aberto por",
                   GRUPO_ATENDIMENTO, "enum", list(_ABERTO_POR)),
        ])
        lista.extend(_campos_configuraveis("atendimento", GRUPO_ATENDIMENTO,
                                           PREFIXO_ATENDIMENTO))

        meta.setdefault("opcoes", {})[OPCOES_ATENDENTES] = _atendentes()
    except Exception:  # noqa: BLE001 — catálogo do retornos nunca quebra por causa daqui
        logger.warning("protocolos: falha ao contribuir campos ao retornos", exc_info=True)
    return meta


# ── Valores ───────────────────────────────────────────────────────────────────

def _nota_da_ultima_avaliacao(protocolo_id: int):
    with make_plugin_db() as conn:
        row = conn.execute(
            text("SELECT nota FROM plugin_protocolos_avaliacoes "
                 "WHERE protocolo_id = :pid AND answered_at IS NOT NULL "
                 "ORDER BY answered_at DESC, id DESC LIMIT 1"),
            {"pid": int(protocolo_id)},
        ).first()
    return row[0] if row else None


def _valores_configuraveis(entidade: dict, scope: str, prefixo: str) -> dict:
    """``fields`` já vem do ``_proto_dict``/``_atendimento_dict`` com os extras cuja
    definição AINDA existe (rótulo apagado não reaparece)."""
    valores = (entidade or {}).get("fields") or {}
    saida: dict = {}
    for d in logic.get_field_defs(scope):
        if str(d.get("type") or "") == "atendente":
            continue
        chave = str(d.get("key") or "")
        if chave:
            saida[prefixo + chave] = valores.get(chave)
    return saida


def _valores_protocolo(proto: dict | None, *, aberto: bool, now: float) -> dict:
    if not proto:
        # Contato sem protocolo nenhum: os campos ficam vazios (só casam com "está vazio").
        return {"protocolos.tem_aberto": "nao"}
    abertura = float(proto.get("opened_at") or 0.0)
    fechamento = proto.get("closed_at")
    ctx = {
        "protocolos.tem_aberto": "sim" if aberto else "nao",
        "protocolos.status": proto.get("status"),
        "protocolos.atendente": (None if proto.get("effective_assignee_user_id") is None
                                 else str(proto["effective_assignee_user_id"])),
        "protocolos.aberto_por": proto.get("opened_by_kind") or None,
        "protocolos.horas_desde_abertura": ((now - abertura) / 3600.0) if abertura else None,
        "protocolos.dias_desde_abertura": ((now - abertura) / 86400.0) if abertura else None,
        "protocolos.horas_desde_fechamento": ((now - float(fechamento)) / 3600.0
                                              if fechamento else None),
        # Epoch cru, igual aos campos `date` do core (`chatwoot.criado_em`): o motor
        # converte a data escolhida na UI pelo offset fixo da configuração.
        "protocolos.data_fechamento": (float(fechamento) if fechamento else None),
        "protocolos.n_atendimentos": logic._count_atendimentos_of_protocolo(int(proto["id"])),
        "protocolos.avaliacao_nota": _nota_da_ultima_avaliacao(int(proto["id"])),
    }
    ctx.update(_valores_configuraveis(proto, "protocolo", PREFIXO_PROTOCOLO))
    return ctx


def _valores_atendimento(ciclo: dict | None) -> dict:
    if not ciclo:
        return {}
    ctx = {
        "protocolos.atend.status": ("encerrado" if ciclo.get("ended_at") else "em_andamento"),
        "protocolos.atend.atendente": (None if ciclo.get("effective_assignee_user_id") is None
                                       else str(ciclo["effective_assignee_user_id"])),
        "protocolos.atend.aberto_por": ciclo.get("opened_by_kind") or None,
    }
    ctx.update(_valores_configuraveis(ciclo, "atendimento", PREFIXO_ATENDIMENTO))
    return ctx


def contexto(ctx, valores: dict) -> dict:
    """``filter.retornos.contexto`` — preenche os campos declarados em :func:`campos`."""
    try:
        extras = getattr(ctx, "extras", None) or {}
        contact_id = extras.get("contact_id")
        conversation_id = extras.get("conversation_id")
        now = float(extras.get("now") or logic.now())
        if contact_id is None:
            return valores

        proto = logic.get_open_protocolo_for_contact(int(contact_id))
        aberto = proto is not None
        if proto is None:
            proto = logic.get_last_closed_protocolo_for_contact(int(contact_id))
        valores.update(_valores_protocolo(proto, aberto=aberto, now=now))

        if conversation_id is not None:
            valores.update(_valores_atendimento(logic.get_latest_cycle(int(conversation_id))))
    except Exception:  # noqa: BLE001 — a avaliação segue sem os campos deste plugin
        logger.warning("protocolos: falha ao resolver o contexto do retornos", exc_info=True)
    return valores
