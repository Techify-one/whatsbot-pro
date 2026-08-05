"""Volta: Trackify → WhatsBot (campos do contato).

O Trackify **não tem webhook de saída** para contato (o catálogo de webhooks dele
tem exatamente dois eventos, ambos de autenticação). A única forma de saber que
alguém editou um campo lá é consultar, e a fonte certa é ``contact_changelog``: é
a única que registra LIMPEZA (``new_value`` nulo) e a única que diz QUEM escreveu.

⚠️ **A consulta migrou para ``GET /contact-changelog``.** Com isso três
complicações inteiras deixaram de existir aqui:

* o **fatiamento por contato** (``CHUNK``) e a regra de avançar o cursor para o
  MÍNIMO entre os pedaços truncados — eles existiam só porque não havia índice em
  ``created_at`` sozinho, e um "tudo que mudou desde T" global varria a tabela de
  auditoria do CRM. O índice agora existe do lado do Trackify e o cursor keyset
  ``(created_at, id)`` é responsabilidade do servidor;
* o **relógio do CDP** consultado à parte — ele volta no corpo da resposta;
* a **supressão de eco por heurística de valor.** Antes, a escrita da integração
  chegava assinada pelo usuário da conta de serviço, indistinguível de uma pessoa
  usando as mesmas credenciais; por isso a linha só era descartada quando o valor
  batia com o último que gravamos. Agora a escrita por API key tem procedência
  própria (``source='api'``, ator ``apikey:<id>``), então reconhecer a própria
  escrita é exato. A comparação por hash **continua** como segunda camada barata:
  ela pega o caso de uma linha de ingestion/merge/import carregando o valor que
  nós mesmos acabamos de escrever.

O atraso de segurança de 5s continua, e continua medido pelo relógio do BANCO:
uma transação aberta antes da nossa leitura pode commitar depois com
``created_at`` abaixo do cursor.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from plugins.context import make_plugin_db

from . import _config, field_codec, field_map, sync_state
from . import client as tk_client

logger = logging.getLogger("plugins.trackify.pull")

CURSOR = "changelog"
PAGE = 500           # linhas de changelog por página
SAFETY_LAG_S = 5.0


def linked_contacts() -> dict:
    """``{trackify_contact_id: (telefone, contact_id)}`` dos contatos vinculados."""
    from db.repositories import contact_repo

    with make_plugin_db() as conn:
        rows = conn.execute(text(
            "SELECT phone, trackify_contact_id FROM plugin_trackify_identity "
            "WHERE trackify_contact_id <> ''")).mappings().all()
    out = {}
    for r in rows:
        c = contact_repo.get_by_phone(r["phone"])
        if c:
            out[str(r["trackify_contact_id"])] = (r["phone"], int(c["id"]))
    return out


def _normalize_row(row: dict) -> dict:
    """Linha do ``GET /contact-changelog`` nas chaves que ``apply_row`` já usa.

    Adaptar na BORDA e não espalhar ``row.get("customFieldId")`` pelo módulo é o
    que mantém ``apply_row`` (e os testes dele) intocados pela troca de SQL por
    HTTP.
    """
    return {
        "row_id": str(row.get("id") or ""),
        "tk_contact_id": str(row.get("contactId") or ""),
        "field_id": str(row.get("customFieldId") or ""),
        "slug": row.get("slug"),
        "new_value": row.get("newValue"),
        "source": row.get("source"),
        "user_id": str(row.get("userId") or ""),
        "created_epoch": float(row.get("createdEpoch") or 0.0),
    }


async def fetch_changes(http, since: tuple, field_slugs: list) -> dict:
    """Uma página do changelog a partir do cursor. Nunca levanta.

    Devolve ``{rows, next, truncated, server_epoch}``. O ``server_epoch`` é o
    relógio do BANCO do CDP — nunca o da máquina do WhatsBot: são servidores
    diferentes e o desvio entre eles viraria buraco ou repetição no cursor.
    """
    res = await tk_client.changelog(http, since=since[0], since_id=since[1],
                                    limit=PAGE, field_slugs=field_slugs)
    if not res.ok:
        logger.debug("trackify: changelog indisponível (%s)", res.error)
        return {"rows": [], "next": since, "truncated": False, "server_epoch": 0.0}

    corpo = res.data or {}
    meta = corpo.get("meta") or {}
    return {
        "rows": [_normalize_row(r) for r in (corpo.get("data") or [])],
        "next": (float(meta.get("nextSince") or since[0]),
                 str(meta.get("nextSinceId") or since[1])),
        "truncated": bool(meta.get("truncated")),
        "server_epoch": float(meta.get("serverEpoch") or 0.0),
    }


# ── Aplicação ────────────────────────────────────────────────────────────

def _e_nosso_eco(row: dict, estado: dict, self_actor: str) -> bool:
    """A linha é a nossa própria escrita voltando?

    Basta o ATOR bater: uma linha assinada por ``apikey:<nossa chave>`` só pode
    ter saído daqui. Com o cookie de sessão isso não era verdade — a escrita da
    integração e a edição de uma pessoa usando as mesmas credenciais chegavam com
    o mesmo ``user_id``, e por isso a versão anterior precisava comparar o valor.

    A comparação por valor continua como SEGUNDA camada, e não é redundante: ela
    pega a linha de ingestion/merge/import que carrega o valor que nós mesmos
    acabamos de escrever — essa não tem o nosso ator.
    """
    autor = str(row.get("userId") or row.get("user_id") or "")
    if self_actor and autor == self_actor:
        return True
    nosso = estado.get("tk_hash")
    if not nosso:
        return False
    return field_codec.hash_value(row.get("new_value")) == nosso


def apply_row(row: dict, maps_by_field: dict, vinculados: dict,
              definicoes: dict, estados: dict, self_user: str) -> tuple[str, str]:
    """Aplica UMA linha do changelog. Devolve ``(desfecho, telefone)``."""
    from db.repositories import custom_attribute_repo as ca_repo
    from db.tables import contacts as contacts_tbl

    m = maps_by_field.get(row["field_id"])
    if not m:
        return "sem_mapeamento", ""
    alvo = vinculados.get(row["tk_contact_id"])
    if not alvo:
        return "nao_vinculado", ""
    phone, contact_id = alvo

    estado = estados.get((m["id"], contact_id)) or {}
    if _e_nosso_eco(row, estado, self_user):
        return "eco", phone

    definition = definicoes.get(m["wb_key"]) or {
        "attribute_key": m["wb_key"], "type": "text"}
    bruto = row.get("new_value")
    valor, err = field_codec.to_whatsbot(m, definition, bruto)
    if err:
        # NÃO grava e NÃO re-tenta: guardar o hash do valor recusado é o que
        # impede a varredura de redetectar a mesma divergência para sempre.
        field_map.bump(m["id"], "pulled_rejected", stamp="last_pull_at", error=err)
        sync_state.record(m["id"], contact_id,
                          wb_hash=field_codec.hash_value(
                              _wb_atual(contact_id, m, ca_repo, contacts_tbl)),
                          tk_hash=field_codec.hash_value(bruto),
                          rejected_hash=field_codec.hash_value(bruto),
                          trackify_contact_id=row["tk_contact_id"], error=err)
        logger.warning("trackify: valor do CDP recusado para '%s': %s", m["wb_key"], err)
        return "recusado", phone

    atual = _wb_atual(contact_id, m, ca_repo, contacts_tbl)
    if field_codec.same(atual, valor):
        # Guarda L2: pega o que o filtro por autor não pega — linha de
        # ingestion/merge/import carregando o valor que nós mesmos escrevemos.
        sync_state.record(m["id"], contact_id,
                          wb_hash=field_codec.hash_value(atual),
                          tk_hash=field_codec.hash_value(valor),
                          trackify_contact_id=row["tk_contact_id"])
        return "ja_igual", phone

    if m["wb_scope"] == field_map.SCOPE_COLUMN:
        from db.repositories import contact_repo
        contact_repo.update(contact_id, **{m["wb_key"]: valor or ""})
    else:
        ca_repo.set_values(contacts_tbl, contact_id, {m["wb_key"]: valor})

    field_map.bump(m["id"], "pulled_ok", stamp="last_pull_at")
    sync_state.record(m["id"], contact_id,
                      wb_hash=field_codec.hash_value(valor),
                      tk_hash=field_codec.hash_value(valor),
                      trackify_contact_id=row["tk_contact_id"],
                      pulled=True, changed_at=float(row["created_epoch"]))
    return "gravado", phone


def _wb_atual(contact_id: int, m: dict, ca_repo, contacts_tbl):
    if m["wb_scope"] == field_map.SCOPE_COLUMN:
        from db.repositories import contact_repo
        c = contact_repo.get(contact_id) or {}
        return c.get(m["wb_key"])
    return ca_repo.get_values(contacts_tbl, contact_id).get(m["wb_key"])


def broadcast_refresh(phones: set) -> None:
    """Atualiza o painel aberto.

    Emite o broadcast de WebSocket ``contact_info_updated``, o MESMO que a tool
    da IA emite — e **não** o evento de barramento ``contact.updated``. Aquele
    significa "passou pela rota do painel", e forjá-lo dispararia o handler de
    todos os outros plugins, inclusive o nosso espelho de eventos, que mandaria
    ao CDP um evento sobre uma mudança que VEIO do CDP.
    """
    from db.repositories import contact_repo
    from plugins.context import broadcast

    for phone in phones:
        try:
            full = contact_repo.get_full_contact(phone) or {}
            broadcast("contact_info_updated",
                      {"phone": phone, "info": full.get("info") or {}})
        except Exception:  # noqa: BLE001
            logger.debug("trackify: falha ao avisar o painel", exc_info=True)


# ── Ciclo ────────────────────────────────────────────────────────────────

async def cycle(http) -> dict:
    """Um ciclo de leitura. Devolve um resumo para o log/telemetria."""
    resumo = {"lidas": 0, "gravadas": 0, "recusadas": 0, "ecos": 0,
              "truncado": False}

    if not bool(_config.setting("field_sync_pull_enabled", False)):
        return resumo
    if not tk_client.is_configured():
        return resumo

    # Ator das NOSSAS escritas no changelog do CDP. Vazio não impede o ciclo — a
    # supressão por valor (2ª camada) segue valendo —, mas deixa a 1ª camada
    # inativa, então vale uma chamada para aprendê-lo.
    self_actor = await _garantir_ator(http)

    maps = [m for m in field_map.list_maps(enabled_only=True)
            if m["direction"] in ("to_whatsbot", "both") and m["tk_field_id"]]
    if not maps:
        return resumo

    vinculados = linked_contacts()
    if not vinculados:
        return resumo

    cur = sync_state.get_cursor(CURSOR)
    since = (float(cur.get("cursor_ts") or 0.0), str(cur.get("cursor_id") or ""))

    slugs = [m["tk_slug"] for m in maps if m.get("tk_slug")]
    pagina = await fetch_changes(http, since, slugs)
    server_epoch = pagina["server_epoch"]
    if not server_epoch:
        return resumo

    # Atraso de segurança: uma transação aberta antes da leitura pode commitar
    # depois com `created_at` abaixo do cursor. Descartamos a borda e a
    # relemos no próximo ciclo.
    high_water = server_epoch - SAFETY_LAG_S
    linhas = [r for r in pagina["rows"] if r["created_epoch"] <= high_water]
    truncado = pagina["truncated"]

    resumo["lidas"] = len(linhas)
    resumo["truncado"] = truncado
    if not linhas:
        # Mesmo sem nada aplicável o cursor avança até a marca de segurança,
        # senão a janela consultada cresce sem parar numa base parada.
        if not truncado:
            sync_state.set_cursor(CURSOR, high_water, "", "sem novidades")
        return resumo

    maps_by_field = {m["tk_field_id"]: m for m in maps}
    definicoes = _definicoes()
    # Uma consulta de estado para o lote todo — é a memória da 2ª camada de
    # supressão de eco.
    estados = sync_state.load_for_contacts([cid for _, cid in vinculados.values()])
    phones = set()
    for row in linhas:
        try:
            desfecho, phone = apply_row(row, maps_by_field, vinculados, definicoes,
                                        estados, self_actor)
        except Exception:  # noqa: BLE001 — uma linha ruim não pode parar o ciclo
            logger.warning("trackify: falha ao aplicar alteração do CDP", exc_info=True)
            continue
        if desfecho == "gravado":
            resumo["gravadas"] += 1
            phones.add(phone)
        elif desfecho == "recusado":
            resumo["recusadas"] += 1
        elif desfecho == "eco":
            resumo["ecos"] += 1

    # O cursor avança para a última linha CONSUMIDA, não para a última recebida:
    # o que foi cortado pelo atraso de segurança precisa ser relido.
    ultimo = linhas[-1]
    sync_state.set_cursor(CURSOR, ultimo["created_epoch"], ultimo["row_id"],
                          "truncado" if truncado else "")
    # Um broadcast por telefone, não por linha.
    broadcast_refresh(phones)
    return resumo


def _self_actor() -> str:
    """Ator com que as nossas escritas aparecem no changelog do CDP."""
    key_id = (_config.setting("sync_api_key_id") or "").strip()
    return f"apikey:{key_id}" if key_id else ""


async def _garantir_ator(http) -> str:
    """Descobre e persiste o id da API key quando ainda não o conhecemos.

    Sem isto, o id só era gravado por quem clicasse em "Testar acesso" na tela —
    quem colasse a chave e salvasse ficava para sempre sem a 1ª camada de
    supressão de eco, e com um aviso na tela que nunca sumia.

    Custa UMA chamada, e só enquanto o id é desconhecido. Falha não interrompe o
    ciclo: sem o ator a leitura continua funcionando pela comparação de valor.
    """
    import asyncio

    atual = _self_actor()
    if atual:
        return atual

    res = await tk_client.whoami(http)
    if not res.ok:
        return ""
    key_id = str((res.data or {}).get("id") or "")
    if not key_id:
        return ""

    def _gravar():
        from db.repositories import config_repo
        config_repo.set_many({_config.PREFIX + "sync_api_key_id": key_id})

    await asyncio.to_thread(_gravar)
    logger.info("trackify: id da API key aprendido (%s)", key_id)
    return f"apikey:{key_id}"


def _definicoes() -> dict:
    from db.repositories import custom_attribute_repo as ca_repo
    try:
        return {d["attribute_key"]: d for d in ca_repo.list_definitions("contact")}
    except Exception:  # noqa: BLE001
        return {}
