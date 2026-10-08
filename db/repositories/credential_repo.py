"""Repository for the AI tool credential vault (plano 177).

``ai_credentials`` guarda uma credencial por linha, com vários campos em JSONB
(``{campo: {"value": str, "secret": bool}}``). ``ai_credential_tools`` liga a
credencial às tools de código que podem recebê-la.

Regra dura: **nenhuma função de leitura "pública" devolve valor secreto**.
``list_meta``/``get_meta`` devolvem ``{key, secret, has_value}`` (e ``value`` só
quando o campo foi marcado ``secret=false``). O ÚNICO caminho que devolve valor é
:func:`for_tool`, de uso interno do host do runner isolado
(``agent.tool_isolation``) — nunca chame-o de uma rota.

Sem cifra (D3), sem tabela de histórico (D6) e sem FK para ``ai_tools`` (P4):
excluir e recriar uma tool é rotina e não pode levar o vínculo junto.
"""

from __future__ import annotations

import re
import time
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import select

from db.engine import get_engine
from db.repositories._mapping import coerce_json
from db.tables import ai_credential_tools, ai_credentials

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_FIELDS = 16
MAX_VALUE_BYTES = 4096


# ── Pure helpers (testáveis sem banco) ───────────────────────────────────────

def _is_secret(spec: Any) -> bool:
    """Campo sem a marca explícita ``secret: false`` é SECRETO (fail-safe)."""
    return not (isinstance(spec, dict) and spec.get("secret") is False)


def _fields_of(row: Any) -> dict:
    fields = coerce_json((row or {}).get("fields"), {})
    return fields if isinstance(fields, dict) else {}


def public_fields(fields: dict) -> list[dict]:
    """Forma pública dos campos — NUNCA o valor de um campo secreto."""
    out = []
    for key in sorted(fields):
        spec = fields[key] if isinstance(fields[key], dict) else {}
        secret = _is_secret(spec)
        value = spec.get("value") or ""
        item = {"key": key, "secret": secret, "has_value": value != ""}
        if not secret:
            item["value"] = value
        out.append(item)
    return out


def merge_fields(existing: dict, spec: Any) -> dict:
    """Aplica a lista ``[{key, secret?, value?}]`` sobre os campos atuais.

    * campo COM ``value`` não vazio → grava (cria ou troca);
    * campo SEM ``value`` (ou vazio) → mantém o atual — editar a URL não obriga a
      redigitar a chave;
    * campo ausente da lista → removido.

    Levanta ``ValueError`` com mensagem em português (a rota devolve 400).
    """
    if not isinstance(spec, list):
        raise ValueError("fields deve ser uma lista.")
    if not spec:
        raise ValueError("Informe ao menos um campo.")
    if len(spec) > MAX_FIELDS:
        raise ValueError(f"No máximo {MAX_FIELDS} campos por credencial.")
    merged: dict = {}
    for item in spec:
        if not isinstance(item, dict):
            raise ValueError("Cada campo deve ser um objeto {key, secret, value}.")
        key = item.get("key")
        if not isinstance(key, str) or not FIELD_RE.match(key):
            raise ValueError(
                f"Nome de campo inválido: {key!r}. Use minúsculas, números e _ "
                "(começando por letra, até 32).")
        if key in merged:
            raise ValueError(f"Campo repetido: {key}.")
        secret = item.get("secret", True)
        if not isinstance(secret, bool):
            raise ValueError(f"Campo {key}: 'secret' deve ser verdadeiro ou falso.")

        provided = item.get("value")
        if provided is not None and not isinstance(provided, str):
            raise ValueError(f"Campo {key}: o valor deve ser texto.")
        prev = existing.get(key) if isinstance(existing.get(key), dict) else None
        if provided:
            if len(provided.encode("utf-8")) > MAX_VALUE_BYTES:
                raise ValueError(f"Campo {key}: valor acima de {MAX_VALUE_BYTES} bytes.")
            value = provided
        elif prev is not None:
            value = prev.get("value") or ""
            # Segredo que vira público passaria a sair na listagem: o operador
            # tem de reinformar o valor, vendo o que está publicando.
            if _is_secret(prev) and not secret:
                raise ValueError(
                    f"Campo {key}: para torná-lo público, informe o valor de novo.")
        else:
            raise ValueError(f"Campo {key}: informe o valor.")
        merged[key] = {"value": value, "secret": secret}
    return merged


# ── Leitura pública (sem valor secreto) ──────────────────────────────────────

def _links_by_credential(conn) -> dict[str, list[str]]:
    rows = conn.execute(
        select(ai_credential_tools.c.credential_name, ai_credential_tools.c.tool_name)
        .order_by(ai_credential_tools.c.tool_name)).all()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(r.credential_name, []).append(r.tool_name)
    return out


def _meta(row: dict, tools: list[str]) -> dict:
    return {
        "name": row["name"],
        "description": row.get("description") or "",
        "fields": public_fields(_fields_of(row)),
        "tools": tools,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "updated_by": row.get("updated_by"),
        "updated_by_name": row.get("updated_by_name"),
    }


def list_meta() -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(ai_credentials).order_by(ai_credentials.c.name)).mappings().all()
        links = _links_by_credential(conn)
    return [_meta(dict(r), links.get(r["name"], [])) for r in rows]


def get_meta(name: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(ai_credentials).where(ai_credentials.c.name == name)
        ).mappings().first()
        if row is None:
            return None
        links = _links_by_credential(conn)
    return _meta(dict(row), links.get(name, []))


def exists(name: str) -> bool:
    with get_engine().connect() as conn:
        return conn.execute(
            select(ai_credentials.c.name).where(ai_credentials.c.name == name)
        ).first() is not None


def reveal_field(name: str, key: str) -> str | None:
    """Valor de UM campo da credencial, ou ``None`` se a credencial/campo não existe.

    ⚠️ Segundo caminho que devolve valor (o primeiro é :func:`for_tool`). Só a
    rota ``POST /api/ai/credentials/{name}/fields/{key}/reveal`` o chama — sob
    ``agent.credentials.manage``, só por sessão de painel e SEMPRE auditado
    (``credential.reveal``, com o nome do campo, nunca o valor). Devolve só o
    campo pedido: abrir a tela não despeja a credencial inteira.
    """
    with get_engine().connect() as conn:
        row = conn.execute(
            select(ai_credentials.c.fields).where(ai_credentials.c.name == name)
        ).first()
    if row is None:
        return None
    fields = coerce_json(row[0], {})
    spec = fields.get(key) if isinstance(fields, dict) else None
    if not isinstance(spec, dict):
        return None
    return spec.get("value") or ""


# ── Escrita ──────────────────────────────────────────────────────────────────

def upsert_credential(
    name: str,
    description: str,
    fields_spec: Any,
    *,
    actor_id: int | None = None,
    actor_name: str | None = None,
) -> tuple[dict, dict]:
    """Cria/edita a credencial. Devolve ``(meta, change)``.

    ``change`` descreve o que mudou SEM valores — é o que a rota manda à
    auditoria: ``{created, campos, definidos, removidos}``.
    """
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError(
            "Nome de credencial inválido. Use minúsculas, números e _ "
            "(começando por letra, até 64).")
    now = time.time()
    with get_engine().begin() as conn:
        row = conn.execute(
            select(ai_credentials).where(ai_credentials.c.name == name).with_for_update()
        ).mappings().first()
        existing = _fields_of(dict(row)) if row else {}
        merged = merge_fields(existing, fields_spec)

        provided = sorted(
            str(i.get("key")) for i in fields_spec
            if isinstance(i.get("value"), str) and i.get("value") != "")
        change = {
            "created": row is None,
            "campos": sorted(merged),
            "definidos": provided,
            "removidos": sorted(set(existing) - set(merged)),
        }
        values = {
            "description": (description or "").strip(),
            "fields": merged,
            "updated_at": now,
            "updated_by": actor_id,
            "updated_by_name": actor_name,
        }
        if row is None:
            conn.execute(ai_credentials.insert().values(
                name=name, created_at=now, **values))
        else:
            conn.execute(ai_credentials.update()
                         .where(ai_credentials.c.name == name).values(**values))
    return get_meta(name), change


def delete(name: str) -> int:
    """Apaga a credencial; os vínculos vão junto (``ON DELETE CASCADE``)."""
    with get_engine().begin() as conn:
        result = conn.execute(
            sa_delete(ai_credentials).where(ai_credentials.c.name == name))
    return result.rowcount or 0


def set_tool_links(credential_name: str, tool_names: list[str]) -> list[str] | None:
    """Substitui a lista de tools liberadas para a credencial.

    ``None`` se a credencial não existe. Não valida a tool (a rota exige
    ``kind='code'``): o vínculo é só um nome, de propósito (P4).
    """
    names = sorted({str(t) for t in tool_names})
    with get_engine().begin() as conn:
        if conn.execute(select(ai_credentials.c.name)
                        .where(ai_credentials.c.name == credential_name)).first() is None:
            return None
        conn.execute(sa_delete(ai_credential_tools)
                     .where(ai_credential_tools.c.credential_name == credential_name))
        if names:
            conn.execute(ai_credential_tools.insert(), [
                {"credential_name": credential_name, "tool_name": t} for t in names])
    return names


def set_credential_links(tool_name: str, credential_names: list[str]) -> list[str]:
    """Substitui as credenciais liberadas para a tool (a visão da tela da tool).

    Levanta ``ValueError`` se alguma credencial não existe.
    """
    names = sorted({str(c) for c in credential_names})
    with get_engine().begin() as conn:
        if names:
            found = {r[0] for r in conn.execute(
                select(ai_credentials.c.name).where(ai_credentials.c.name.in_(names)))}
            missing = [n for n in names if n not in found]
            if missing:
                raise ValueError(f"Credencial inexistente: {', '.join(missing)}.")
        conn.execute(sa_delete(ai_credential_tools)
                     .where(ai_credential_tools.c.tool_name == tool_name))
        if names:
            conn.execute(ai_credential_tools.insert(), [
                {"credential_name": c, "tool_name": tool_name} for c in names])
    return names


def links_for_tool(tool_name: str) -> list[str]:
    """Nomes (só nomes) das credenciais liberadas para a tool."""
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(ai_credential_tools.c.credential_name)
            .where(ai_credential_tools.c.tool_name == tool_name)
            .order_by(ai_credential_tools.c.credential_name)).all()
    return [r[0] for r in rows]


# ── Uso interno do host do runner isolado ────────────────────────────────────

def for_tool(tool_name: str) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Valores liberados para a tool + os valores a REDIGIR da saída.

    Devolve ``(credentials, secrets)``:

    * ``credentials`` = ``{credencial: {campo: valor}}`` — vai ao ``stdin`` do
      subprocesso, e só das credenciais ligadas a ESTA tool;
    * ``secrets`` = valores dos campos ``secret=true``, não vazios, para o
      ``scrub`` do host (D13: sem tamanho mínimo; vazio nunca entra).

    ⚠️ Único caminho que devolve valor secreto. Só o ``agent.tool_isolation`` o
    chama; nenhuma rota, log ou evento pode usá-lo.
    """
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(ai_credentials.c.name, ai_credentials.c.fields)
            .join(ai_credential_tools,
                  ai_credential_tools.c.credential_name == ai_credentials.c.name)
            .where(ai_credential_tools.c.tool_name == tool_name)
            .order_by(ai_credentials.c.name)).all()
    credentials: dict[str, dict[str, str]] = {}
    secrets: list[str] = []
    for r in rows:
        fields = coerce_json(r.fields, {})
        if not isinstance(fields, dict):
            continue
        bucket: dict[str, str] = {}
        for key, spec in fields.items():
            if not isinstance(spec, dict):
                continue
            value = spec.get("value") or ""
            bucket[key] = value
            if _is_secret(spec) and value != "":
                secrets.append(value)
        credentials[r.name] = bucket
    return credentials, secrets
