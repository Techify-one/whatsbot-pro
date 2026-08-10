"""Cadastro automático do contato do WhatsBot no CDP.

Existe porque a resolução de identidade só sabe responder "quem é este contato"
— e a resposta mais comum num CDP jovem é "ninguém". Antes, todo caminho de
escrita (consentimento, sincronização de campos) simplesmente desistia: o
descadastro de quem não estava no Trackify **nunca era gravado**, e o motivo só
aparecia como texto num campo de erro que a tela não mostrava.

Duas entradas, um só caminho de criação:

* :func:`ensure` — sob demanda, no fio de quem precisa do vínculo AGORA (o
  clique no botão de descadastro). Cria e devolve o id na mesma chamada.
* :func:`cycle` — varredura periódica, chamada pela task supervisionada. Percorre
  a base em passagens curtas e cadastra quem ainda não tem vínculo. É ela que faz
  o **backfill** dos contatos anteriores a esta versão e cobre o contato criado
  pelo painel, que não gera mensagem nenhuma.

⚠️ **Contato no Trackify tem apenas soft delete.** Um cadastro criado por engano
fica no CRM do cliente para sempre. Por isso a elegibilidade é emprestada de
``mirror.eligible`` (mesma pergunta, mesma resposta: grupo não, id de sessão do
widget não, tipo de contato fora do escopo não) e por isso existe o modo seco.

⚠️ **Criar não é resolver ambiguidade.** Quando a consulta devolve mais de um
cadastro no identificador de topo, este módulo não cria coisa nenhuma — criar ali
produziria um terceiro duplicado em cima de dois que já precisam de mesclagem.
"""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import text

from plugins.context import make_plugin_db

from . import _config, field_map, identity, mirror, push
from . import client as tk_client

logger = logging.getLogger("plugins.trackify.enroll")

# Teto por passagem da varredura. O balde de requisições das rotas de contato do
# Trackify é 30/min POR IP e é COMPARTILHADO com a sincronização de campos e com
# a leitura do changelog — cadastrar em rajada roubaria a cota de quem já estava
# trabalhando. Uma passagem curta a cada minuto zera uma base de mil contatos em
# algumas horas, o que é adequado para um backfill que roda uma vez.
BATCH = 5
IDENTITY_TTL = push.IDENTITY_TTL


def enabled() -> bool:
    return bool(_config.setting("cdp_autocadastro_enabled", True))


def dry_run() -> bool:
    return bool(_config.setting("cdp_autocadastro_dry_run", False))


def ready() -> bool:
    """Ligado e com credencial. Sem chave não há o que tentar."""
    return enabled() and tk_client.is_configured()


# ── Criação (caminho único) ──────────────────────────────────────────────

async def create_for_contact(http, contact: dict) -> tuple[str, str]:
    """Cria o cadastro do contato no CDP. ``(tk_id, motivo_da_recusa)``.

    ``tk_id`` vazio vem sempre com um motivo em português, porque é ele que
    aparece na tela para o operador — "não deu" sem dizer o quê foi exatamente o
    buraco que esta versão veio fechar.
    """
    ok, why = mirror.eligible(contact)
    if not ok:
        return "", f"não é possível cadastrar no Trackify: {why}"

    ident = identity.canonical_identity(
        phone=contact.get("phone") or "",
        email=(contact.get("email") or "").strip(),
        contact_type=(contact.get("contact_type") or "whatsapp").lower())
    if not ident:
        return "", "contato sem identificador válido para cadastrar no Trackify"

    if dry_run():
        return "", f"[modo seco] cadastraria no Trackify com {sorted(ident)}"

    res = await tk_client.create_contact(http, ident)

    if res.verdict == tk_client.CONFLICT:
        # Outro cadastro já é dono de um destes identificadores. Nada foi criado
        # (o Trackify valida antes de inserir) — a leitura de agora acha o dono.
        tk_id, _porque = await push.linked_id_ex(
            http, str(contact.get("phone") or ""), contact)
        if tk_id:
            return tk_id, ""
        return "", f"o Trackify recusou o cadastro: {res.error}"

    if not res.ok:
        return "", f"não foi possível cadastrar no Trackify: {res.error}"

    novo = _id_from(res.data)
    if not novo:
        return "", "o Trackify aceitou o cadastro mas não devolveu o id"

    await asyncio.to_thread(_store_link, str(contact.get("phone") or ""), novo, ident)
    logger.info("trackify: contato %s cadastrado no CDP (%s)",
                contact.get("phone"), novo)
    return novo, ""


async def ensure(http, contact: dict) -> tuple[str, str]:
    """O vínculo do contato, cadastrando se ainda não houver um.

    Resolve primeiro: cadastrar sem olhar produziria duplicata a cada clique.
    ``ambiguo`` e ``erro_leitura`` NÃO viram criação — o primeiro precisa de
    mesclagem humana, o segundo de uma nova tentativa.
    """
    phone = str(contact.get("phone") or "")
    tk_id, porque = await push.linked_id_ex(http, phone, contact)
    if tk_id:
        return tk_id, ""
    if porque == "ambiguo":
        return "", "ambiguo"
    if porque == "erro_leitura":
        return "", "erro_leitura"

    if not ready():
        return "", ("contato não vinculado a um cadastro no Trackify — ligue o "
                    "cadastro automático ou marque o descadastro à mão no CDP")
    return await create_for_contact(http, contact)


# ── Varredura ────────────────────────────────────────────────────────────

def pending(limit: int = BATCH) -> list[dict]:
    """Contatos sem vínculo fresco no CDP, do mais recente para o mais antigo.

    O mais recente primeiro de propósito: é quem está em atendimento agora, e é
    dele que um clique em botão pode chegar nos próximos minutos. O backfill
    histórico vai atrás, no tempo ocioso.
    """
    corte = time.time() - IDENTITY_TTL
    with make_plugin_db() as conn:
        rows = conn.execute(text(
            "SELECT c.id, c.phone, c.name, c.email, c.contact_type, c.is_group "
            "  FROM contacts c "
            "  LEFT JOIN plugin_trackify_identity i ON i.phone = c.phone "
            " WHERE c.is_group = 0 "
            "   AND COALESCE(c.phone, '') <> '' "
            "   AND (i.phone IS NULL "
            "        OR COALESCE(i.trackify_contact_id, '') = '' "
            "        OR COALESCE(i.resolved_at, 0) < :corte) "
            " ORDER BY c.id DESC LIMIT :lim"),
            {"corte": corte, "lim": max(1, int(limit or BATCH))}).mappings().all()
    return [dict(r) for r in rows]


async def cycle(http) -> dict:
    """Uma passagem. ``{vistos, cadastrados, pulados, ambiguos}``."""
    resumo = {"vistos": 0, "cadastrados": 0, "pulados": 0, "ambiguos": 0}
    if not ready() or push.cooldown_remaining() > 0:
        return resumo

    linhas = await asyncio.to_thread(pending, BATCH)
    if not linhas:
        return resumo

    gap = 60.0 / max(1, min(int(_config.setting("field_sync_rate_per_min", 15) or 15), 25))
    for i, contato in enumerate(linhas):
        resumo["vistos"] += 1
        ok, _why = mirror.eligible(contato)
        if not ok:
            resumo["pulados"] += 1
            continue
        tk_id, motivo = await ensure(http, contato)
        if tk_id:
            resumo["cadastrados"] += 1
        elif motivo == "ambiguo":
            resumo["ambiguos"] += 1
        else:
            resumo["pulados"] += 1
        if i < len(linhas) - 1:
            await asyncio.sleep(gap)
    return resumo


# ── Utilitários ──────────────────────────────────────────────────────────

def _id_from(data) -> str:
    """O id do contato na resposta do POST, seja ela envelopada ou crua."""
    try:
        corpo = data or {}
        if isinstance(corpo, dict):
            interno = corpo.get("data")
            if isinstance(interno, dict) and interno.get("id"):
                return str(interno["id"])
            if corpo.get("id"):
                return str(corpo["id"])
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _store_link(phone: str, tk_id: str, ident: dict) -> None:
    """Grava o vínculo recém-criado no mesmo cache que a resolução usa.

    Sem isto o próximo clique consultaria o CDP de novo — e, pior, a varredura
    trataria o contato como pendente para sempre, recadastrando em laço.
    """
    if not (phone and tk_id):
        return
    slug = next(iter(ident), "")
    try:
        with make_plugin_db() as conn:
            conn.execute(text(
                "INSERT INTO plugin_trackify_identity "
                "(phone, trackify_contact_id, matched_slug, exact_value, resolved_at) "
                "VALUES (:p, :cid, :slug, :val, :ts) "
                "ON CONFLICT (phone) DO UPDATE SET "
                " trackify_contact_id = EXCLUDED.trackify_contact_id, "
                " matched_slug = EXCLUDED.matched_slug, "
                " exact_value = EXCLUDED.exact_value, "
                " resolved_at = EXCLUDED.resolved_at"),
                {"p": phone, "cid": tk_id, "slug": slug,
                 "val": str(ident.get(slug) or ""), "ts": time.time()})
    except Exception:  # noqa: BLE001 — o cadastro existe; o cache se refaz sozinho
        logger.debug("trackify: falha ao guardar o vínculo recém-criado", exc_info=True)


async def describe_ambiguity(http, contact: dict) -> str:
    """Texto acionável para o operador quando há mais de um cadastro no topo.

    Nomeia os cadastros e o identificador que os empatou — sem isso a tela diria
    "ambíguo" e o operador não teria por onde começar. É uma consulta a mais, só
    no caminho de erro.
    """
    base = ("mais de um cadastro no Trackify casa com este contato — "
            "mescle os duplicados no CDP e reprocese o clique")
    try:
        res = await identity.resolve_mapped_ex(
            http, phone=str(contact.get("phone") or ""),
            extras=await asyncio.to_thread(field_map.identifier_hints, contact),
            contact_type=(contact.get("contact_type") or "whatsapp").lower())
        if not res.ok or not res.matches:
            return base
        topo = res.matches[0].slug
        ids = [m.contact_id for m in res.matches if m.slug == topo]
        return (f"{base}. Empataram por '{topo}': " + ", ".join(ids))
    except Exception:  # noqa: BLE001 — descrever o erro não pode virar outro
        return base
