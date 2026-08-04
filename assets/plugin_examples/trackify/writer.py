"""Escrita de campos do contato no Trackify (``PUT /api/v1/contacts/:id``).

É o ÚNICO caminho com semântica completa. A ingestão (``POST /ingestion/...``,
usada pelo espelho de eventos) não serve aqui por duas razões estruturais:
campo identificador lá é "preenche-se-vazio" e nunca sobrescrito, e valor vazio
é recusado — ou seja, não dá para corrigir um e-mail nem para apagar um campo.

Duas armadilhas do outro lado que este módulo fecha:

* **Slug desconhecido é IGNORADO em silêncio** (``if (!cf) continue``): o PUT
  devolve 200 e nada foi gravado. Por isso conferimos a resposta campo a campo
  em vez de confiar no código HTTP — é a falha mais perigosa da feature e custa
  dez linhas fechar.
* **O ``ValidationPipe`` é ``forbidNonWhitelisted``**: qualquer chave fora de
  ``{status, tags, tagIds, fieldValues}`` derruba a requisição inteira com 400.
  Por isso o corpo é montado a partir de uma lista fixa, nunca repassado.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from . import _config

logger = logging.getLogger("plugins.trackify.writer")

PUT_TIMEOUT = 20.0

# Veredito por linha da fila.
OK = "ok"                 # gravou (e conferimos)
CONFLICT = "conflict"     # 409: outro cadastro é dono daquele identificador
UNLINKED = "unlinked"     # 404/403: contato sumiu ou foi fundido no CDP
BLOCKED = "blocked"       # 400/422: erro de configuração, não melhora com retry
THROTTLED = "throttled"   # 429
UNAUTHORIZED = "unauthorized"   # 401: sessão morreu
RETRY = "retry"           # 5xx / rede


@dataclass
class PutResult:
    verdict: str
    http_status: int = 0
    error: str = ""
    retry_after: float = 0.0
    landed: dict = field(default_factory=dict)   # slug -> valor confirmado
    dropped: list = field(default_factory=list)  # slugs que o CDP ignorou


async def put_contact(client, session, trackify_contact_id: str,
                      field_values: dict) -> PutResult:
    """Grava ``{slug: valor}``. ``""`` APAGA o campo, chave omitida não é tocada."""
    base = _config.api_base()
    if not (base and trackify_contact_id and field_values):
        return PutResult(BLOCKED, error="destino ou valores ausentes")

    url = f"{base}/contacts/{trackify_contact_id}"
    # Corpo montado a partir de chave fixa — ver ``forbidNonWhitelisted``.
    body = {"fieldValues": {str(k): str(v) for k, v in field_values.items()}}
    try:
        resp = await client.put(url, json=body,
                                headers={"Cookie": session.cookie},
                                timeout=PUT_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return PutResult(RETRY, error=type(e).__name__)

    code = resp.status_code
    # Qualquer 2xx. O NestJS escolhe o status pelo VERBO (PUT→200, POST→201) e o
    # `@ApiResponse` do controller documenta outro — exigir um número exato já
    # fez um login bem-sucedido ser lido como recusa. `_verify` lida sozinho com
    # um corpo ausente ou ilegível.
    if 200 <= code < 300:
        return _verify(resp, body["fieldValues"])
    if code == 401:
        return PutResult(UNAUTHORIZED, code, "sessão expirada")
    if code == 409:
        # NUNCA re-tentar: outro contato do CDP é dono daquele valor, e a décima
        # tentativa não muda isso. Precisa de decisão humana.
        return PutResult(CONFLICT, code, _reason(resp) or "identificador já usado por outro contato")
    if code in (403, 404):
        return PutResult(UNLINKED, code, "contato não encontrado no Trackify")
    if code == 429:
        return PutResult(THROTTLED, code, "limite de requisições",
                         retry_after=_retry_after(resp))
    if code in (400, 422):
        return PutResult(BLOCKED, code, _reason(resp) or "requisição recusada")
    return PutResult(RETRY, code, _reason(resp) or f"HTTP {code}")


def _verify(resp, sent: dict) -> PutResult:
    """Confere, campo a campo, o que de fato aterrissou.

    Sem isto contaríamos sucesso numa escrita que nunca aconteceu: um slug
    desativado ou renomeado no CDP é pulado em silêncio e o 200 vem igual.
    """
    try:
        payload = resp.json() or {}
        rows = payload.get("contactFieldValues") or []
        atual = {
            str((r.get("customField") or {}).get("slug") or ""): str(r.get("value") or "")
            for r in rows
        }
    except Exception:  # noqa: BLE001
        # Resposta ilegível: não afirmamos sucesso nem falha de gravação.
        return PutResult(RETRY, 200, "resposta do Trackify ilegível")

    landed, dropped = {}, []
    for slug, valor in sent.items():
        if valor == "":
            # Limpeza: sucesso é a AUSÊNCIA da linha (o PUT faz deleteMany).
            if slug in atual:
                dropped.append(slug)
            else:
                landed[slug] = ""
        elif atual.get(slug) == valor:
            landed[slug] = valor
        else:
            dropped.append(slug)

    if dropped:
        return PutResult(BLOCKED, 200, dropped=dropped, landed=landed,
                         error="o Trackify ignorou: " + ", ".join(sorted(dropped))
                               + " (campo desativado ou inexistente lá)")
    return PutResult(OK, 200, landed=landed)


def _reason(resp) -> str:
    try:
        msg = (resp.json() or {}).get("message")
    except Exception:  # noqa: BLE001
        return ""
    if isinstance(msg, list):
        msg = "; ".join(str(m) for m in msg)
    return str(msg or "")[:300]


def _retry_after(resp) -> float:
    try:
        return max(0.0, float(resp.headers.get("retry-after") or 0))
    except (TypeError, ValueError):
        return 0.0
