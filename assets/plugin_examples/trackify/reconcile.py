"""Varredura de conferência: cura o que evento e leitura não conseguem ver.

Três buracos ESTRUTURAIS que nenhum dos outros dois caminhos fecha:

* **Importação por CSV do WhatsBot não emite evento nenhum** — nem
  ``contact.updated``, nem nada. Uma planilha com 3 mil e-mails é invisível.
* **``ca_repo.set_values`` é um UPDATE puro**: qualquer caminho do core que
  escreva direto no repositório passa despercebido.
* **Buraco de leitura**: cursor perdido, plugin desligado por uma semana, CDP
  fora do ar durante a janela.

Dois limites que não são detalhe:

* **Teto de envios por ciclo.** Sem ele, a primeira execução sobre 5 mil
  contatos satura o orçamento de 30/min por horas E dispara 5 mil eventos na
  fila de automações do Trackify (toda escrita nossa vira um evento lá).
* **Cursor rotativo.** Uma passagem completa leva ``N/lote`` ciclos, de forma
  observável, em vez de tentar a base inteira de uma vez.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import text

from plugins.context import make_plugin_db

from . import _config, field_codec, field_map, pull, push, sync_core, sync_state

logger = logging.getLogger("plugins.trackify.reconcile")

CURSOR = "reconcile"
BATCH = 100          # contatos por ciclo
MAX_PUSHES = 20      # ver a discussão sobre o teto na docstring
MAX_PULLS = 200      # escrita local: sem custo de rede, teto bem mais alto
IDENTITY_TTL = push.IDENTITY_TTL


def _next_batch(after_phone: str) -> list[dict]:
    with make_plugin_db() as conn:
        rows = conn.execute(text(
            "SELECT phone, trackify_contact_id, resolved_at "
            "FROM plugin_trackify_identity "
            "WHERE trackify_contact_id <> '' AND phone > :after "
            "ORDER BY phone LIMIT :lim"),
            {"after": after_phone or "", "lim": BATCH}).mappings().all()
    return [dict(r) for r in rows]


async def cycle(http) -> dict:
    """Uma passagem parcial. Devolve um resumo para o log/telemetria."""
    resumo = {"conferidos": 0, "enfileirados": 0, "gravados": 0,
              "conflitos": 0, "volta": False}

    if not bool(_config.setting("field_sync_enabled", False)):
        return resumo

    maps = field_map.list_maps(enabled_only=True)
    if not maps:
        return resumo

    cur = sync_state.get_cursor(CURSOR)
    lote = _next_batch(str(cur.get("cursor_id") or ""))
    if not lote:
        # Fim da volta: recomeça do início e conta a passagem.
        passada = int(float(cur.get("cursor_ts") or 0)) + 1
        sync_state.set_cursor(CURSOR, passada, "", f"passada {passada} concluída")
        resumo["volta"] = True
        return resumo

    import asyncio

    from db.repositories import contact_repo

    definicoes = pull._definicoes()
    phones_avisar = set()
    for linha in lote:
        c = await asyncio.to_thread(contact_repo.get_by_phone, linha["phone"])
        if not c:
            continue
        contact_id = int(c["id"])
        resumo["conferidos"] += 1
        try:
            skip, tk_id, decisoes, _ = await push.decisions_for_contact(
                http, contact_id, maps)
        except Exception:  # noqa: BLE001 — um contato ruim não para a varredura
            logger.debug("trackify: conferência falhou para um contato", exc_info=True)
            continue
        if skip:
            continue

        # Uma consulta de estado por CONTATO, não por campo.
        estados = sync_state.load_for_contacts([contact_id])
        ja_enfileirado = False
        for d in decisoes:
            m, dec = d["map"], d["decision"]
            state = estados.get((m["id"], contact_id))
            # Valor já recusado pela validação do WhatsBot: pular, senão a
            # varredura redetecta a mesma divergência em todo ciclo.
            if state and state.get("rejected_hash") == dec.tk_hash:
                continue

            if dec.action == sync_core.PUSH:
                # Enfileira em vez de escrever: quem fala com o CDP é o worker,
                # que é onde mora o controle de ritmo. A fila é POR CONTATO, então
                # um enfileiramento cobre todos os campos dele — mas seguimos o
                # laço, senão um PULL num campo seguinte do MESMO contato nunca
                # seria aplicado.
                if not ja_enfileirado and resumo["enfileirados"] < MAX_PUSHES:
                    push.enqueue(contact_id, "conferência")
                    resumo["enfileirados"] += 1
                    ja_enfileirado = True
                continue
            if dec.action == sync_core.PULL and resumo["gravados"] < MAX_PULLS:
                if _aplicar_pull(contact_id, tk_id, m, d["tk"], definicoes):
                    resumo["gravados"] += 1
                    phones_avisar.add(linha["phone"])
            elif dec.action == sync_core.CONFLICT:
                field_map.bump(m["id"], "conflicts")
                sync_state.record(m["id"], contact_id, wb_hash=dec.wb_hash,
                                  tk_hash=dec.tk_hash, trackify_contact_id=tk_id,
                                  conflict=True, conflict_reason=dec.reason)
                resumo["conflitos"] += 1
            elif dec.action == sync_core.RESTAMP:
                sync_state.record(m["id"], contact_id, wb_hash=dec.wb_hash,
                                  tk_hash=dec.tk_hash, trackify_contact_id=tk_id)

    sync_state.set_cursor(CURSOR, float(cur.get("cursor_ts") or 0),
                          lote[-1]["phone"], "em andamento")
    pull.broadcast_refresh(phones_avisar)
    return resumo


def _aplicar_pull(contact_id: int, tk_id: str, m: dict, tk_value,
                  definicoes: dict) -> bool:
    """Escreve no WhatsBot um valor que só existe no CDP. Escrita local: sem
    orçamento de rede envolvido."""
    from db.repositories import custom_attribute_repo as ca_repo
    from db.tables import contacts as contacts_tbl

    definition = definicoes.get(m["wb_key"]) or {
        "attribute_key": m["wb_key"], "type": "text"}
    valor, err = field_codec.to_whatsbot(m, definition, tk_value)
    if err:
        field_map.bump(m["id"], "pulled_rejected", stamp="last_pull_at", error=err)
        sync_state.record(m["id"], contact_id,
                          wb_hash=field_codec.hash_value(
                              pull._wb_atual(contact_id, m, ca_repo, contacts_tbl)),
                          tk_hash=field_codec.hash_value(tk_value),
                          rejected_hash=field_codec.hash_value(tk_value),
                          trackify_contact_id=tk_id, error=err)
        return False

    if m["wb_scope"] == field_map.SCOPE_COLUMN:
        from db.repositories import contact_repo
        contact_repo.update(contact_id, **{m["wb_key"]: valor or ""})
    else:
        ca_repo.set_values(contacts_tbl, contact_id, {m["wb_key"]: valor})

    field_map.bump(m["id"], "pulled_ok", stamp="last_pull_at")
    sync_state.record(m["id"], contact_id,
                      wb_hash=field_codec.hash_value(valor),
                      tk_hash=field_codec.hash_value(valor),
                      trackify_contact_id=tk_id, pulled=True,
                      changed_at=time.time())
    return True
