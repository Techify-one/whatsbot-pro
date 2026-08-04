"""Volta: Trackify → WhatsBot (campos do contato).

O Trackify **não tem webhook de saída** para contato (o catálogo de webhooks
dele tem exatamente dois eventos, ambos de autenticação). A única forma de
saber que alguém editou um campo lá é consultar, e a fonte certa é
``contact_changelog``: é a única que registra LIMPEZA (``new_value`` nulo) e a
única que diz QUEM escreveu — o que é a supressão de eco desta feature.

Três detalhes que parecem mero cuidado e não são:

* **A supressão de eco é por VALOR, não por autor.** A versão anterior descartava
  no SQL toda linha escrita pelo ``user_id`` da conta de serviço. Parece exato e
  não é: se uma pessoa usar essa mesma conta para entrar na tela do Trackify — o
  caso normal quando alguém aponta a integração para o próprio usuário — as
  edições DELA ficam indistinguíveis das nossas por autor, e sumiam em silêncio.
  Agora a linha só é ignorada quando o valor bate com o que NÓS gravamos por
  último naquele par (``state.tk_hash``). Comparar com o valor ATUAL do WhatsBot
  não serviria: entre o nosso envio e a leitura o operador pode ter mudado o
  campo de novo, e a linha antiga ressuscitaria o valor velho.
* **A consulta é escopada aos contatos vinculados.** ``contact_changelog`` só
  tem índice em ``(contact_id, created_at DESC)`` — não há índice em
  ``created_at`` sozinho, então um "tudo que mudou desde T" global varre a
  tabela inteira do CRM de produção.
* **O cursor global com consulta fatiada precisa avançar para o MÍNIMO.** Ver
  ``merge_chunks``: avançar para o máximo perde linhas em silêncio.
* **Atraso de segurança de 5s, pelo relógio do CDP.** Uma transação aberta antes
  da nossa leitura pode commitar depois com ``created_at`` abaixo do cursor.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from plugins.context import make_plugin_db

from . import _config, field_codec, field_map, session, sync_state, trackify_db

logger = logging.getLogger("plugins.trackify.pull")

CURSOR = "changelog"
CHUNK = 200          # contatos por consulta (o ANY vira BitmapOr de índice)
PAGE = 500           # linhas de changelog por consulta
SAFETY_LAG_S = 5.0

_CHANGELOG_SQL = """
SELECT cl.id::text                       AS row_id,
       cl.contact_id::text               AS tk_contact_id,
       cl.custom_field_id::text          AS field_id,
       cf.slug                           AS slug,
       cl.new_value                      AS new_value,
       cl.source::text                   AS source,
       COALESCE(cl.user_id, '')          AS user_id,
       EXTRACT(EPOCH FROM cl.created_at) AS created_epoch
FROM contact_changelog cl
JOIN custom_fields cf ON cf.id = cl.custom_field_id
WHERE cl.contact_id = ANY(CAST(:ids AS uuid[]))
  AND cl.custom_field_id = ANY(CAST(:field_ids AS uuid[]))
  AND cl.created_at <= to_timestamp(:high_water)
  AND (cl.created_at > to_timestamp(:since_ts)
       OR (cl.created_at = to_timestamp(:since_ts) AND cl.id::text > :since_id))
ORDER BY cl.created_at, cl.id
LIMIT :lim
"""


# ── Cursor fatiado (puro) ────────────────────────────────────────────────

def merge_chunks(chunks: list[dict], since: tuple) -> tuple[list, tuple, bool]:
    """Junta os pedaços e decide até onde o cursor pode avançar.

    O cursor é GLOBAL mas a consulta é fatiada por contato. Avançar para o
    máximo de todos os pedaços PERDE LINHAS: se o pedaço A voltou até T=100 e o
    pedaço B bateu no LIMIT em T=40, tudo entre 40 e 100 do pedaço B nunca mais
    seria olhado. A regra correta é avançar para o mínimo, entre os pedaços que
    truncaram, da última linha devolvida por cada um.

    Devolve ``(linhas_a_aplicar, novo_cursor, houve_truncagem)``.
    """
    def key(r):
        return (float(r["created_epoch"]), str(r["row_id"]))

    todas = [r for c in chunks for r in c["rows"]]
    if not todas:
        return [], since, False

    truncados = [c for c in chunks if c["truncated"] and c["rows"]]
    if truncados:
        frontier = min(key(c["rows"][-1]) for c in truncados)
    else:
        frontier = max(key(r) for r in todas)

    manter = sorted((r for r in todas if key(r) <= frontier), key=key)
    return manter, frontier, bool(truncados)


# ── Leitura ──────────────────────────────────────────────────────────────

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


def cdp_now() -> float:
    """Relógio do CDP. Nunca o da máquina do WhatsBot: são servidores
    diferentes e o desvio entre eles viraria buraco ou repetição no cursor."""
    rows = trackify_db.run_read("SELECT EXTRACT(EPOCH FROM now()) AS agora", {})
    return float(rows[0]["agora"]) if rows else 0.0


def fetch_changes(contact_ids: list, field_ids: list, since: tuple,
                  high_water: float) -> list[dict]:
    """Lê o changelog em pedaços de ``CHUNK`` contatos. Devolve os pedaços crus."""
    chunks = []
    for i in range(0, len(contact_ids), CHUNK):
        lote = contact_ids[i:i + CHUNK]
        rows = trackify_db.run_read(_CHANGELOG_SQL, {
            "ids": lote, "field_ids": field_ids,
            "high_water": high_water,
            "since_ts": since[0], "since_id": since[1], "lim": PAGE,
        })
        rows = [dict(r) for r in rows]
        chunks.append({"rows": rows, "truncated": len(rows) >= PAGE})
    return chunks


# ── Aplicação ────────────────────────────────────────────────────────────

def _e_nosso_eco(row: dict, estado: dict, self_user: str) -> bool:
    """A linha é a nossa própria escrita voltando?

    Duas condições, e as DUAS são necessárias: escrita pela conta de serviço E
    com o valor exato que registramos no último envio daquele par. Só a primeira
    condição (o filtro por autor da versão anterior) engolia a edição de um
    humano que usasse a mesma conta para entrar no Trackify. Só a segunda seria
    frouxa demais: um humano pode reescrever o mesmo valor.
    """
    if not self_user or str(row.get("user_id") or "") != self_user:
        return False
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

def cycle() -> dict:
    """Um ciclo de leitura. Bloqueante; devolve um resumo para o log/telemetria."""
    resumo = {"lidas": 0, "gravadas": 0, "recusadas": 0, "ecos": 0,
              "conta_compartilhada": 0, "truncado": False}

    if not bool(_config.setting("field_sync_pull_enabled", False)):
        return resumo
    if not trackify_db.is_configured():
        return resumo

    self_user = session.cached_user_id()
    if not self_user:
        # Sem o id da conta de serviço não há supressão de eco: rodar assim
        # reimportaria as nossas próprias escritas como se fossem edições
        # humanas. Um ciclo de espera é melhor que um laço de escrita.
        logger.debug("trackify: pull adiado — conta de serviço ainda não autenticou")
        return resumo

    maps = [m for m in field_map.list_maps(enabled_only=True)
            if m["direction"] in ("to_whatsbot", "both") and m["tk_field_id"]]
    if not maps:
        return resumo

    vinculados = linked_contacts()
    if not vinculados:
        return resumo

    agora = cdp_now()
    if not agora:
        return resumo
    high_water = agora - SAFETY_LAG_S

    cur = sync_state.get_cursor(CURSOR)
    since = (float(cur.get("cursor_ts") or 0.0), str(cur.get("cursor_id") or ""))

    chunks = fetch_changes(list(vinculados.keys()),
                           [m["tk_field_id"] for m in maps],
                           since, high_water)
    linhas, novo_cursor, truncado = merge_chunks(chunks, since)
    resumo["lidas"] = len(linhas)
    resumo["truncado"] = truncado
    if not linhas:
        # Mesmo sem nada novo o cursor avança até a marca de segurança, senão a
        # janela consultada cresce sem parar numa base parada.
        sync_state.set_cursor(CURSOR, high_water, "", "sem novidades")
        return resumo

    maps_by_field = {m["tk_field_id"]: m for m in maps}
    definicoes = _definicoes()
    # Uma consulta de estado para o lote todo — ela é a memória que distingue o
    # nosso eco da edição de um humano que usa a MESMA conta.
    estados = sync_state.load_for_contacts([cid for _, cid in vinculados.values()])
    phones = set()
    conta_compartilhada = 0
    for row in linhas:
        try:
            desfecho, phone = apply_row(row, maps_by_field, vinculados, definicoes,
                                        estados, self_user)
        except Exception:  # noqa: BLE001 — uma linha ruim não pode parar o ciclo
            logger.warning("trackify: falha ao aplicar alteração do CDP", exc_info=True)
            continue
        if desfecho == "gravado":
            resumo["gravadas"] += 1
            phones.add(phone)
            if str(row.get("user_id") or "") == self_user:
                # Escrita da conta de serviço com valor diferente do nosso: foi
                # uma PESSOA usando as mesmas credenciais.
                conta_compartilhada += 1
        elif desfecho == "recusado":
            resumo["recusadas"] += 1
        elif desfecho == "eco":
            resumo["ecos"] += 1

    if conta_compartilhada:
        resumo["conta_compartilhada"] = conta_compartilhada
        logger.warning(
            "trackify: %d alteração(ões) no Trackify foram feitas pela CONTA DE "
            "SERVIÇO. Use um usuário dedicado à integração — com a conta "
            "compartilhada, distinguir a edição de uma pessoa da nossa própria "
            "escrita depende só do valor gravado.", conta_compartilhada)

    sync_state.set_cursor(CURSOR, novo_cursor[0], novo_cursor[1],
                          "truncado" if truncado else "")
    # Um broadcast por telefone, não por linha.
    broadcast_refresh(phones)
    return resumo


def _definicoes() -> dict:
    from db.repositories import custom_attribute_repo as ca_repo
    try:
        return {d["attribute_key"]: d for d in ca_repo.list_definitions("contact")}
    except Exception:  # noqa: BLE001
        return {}
