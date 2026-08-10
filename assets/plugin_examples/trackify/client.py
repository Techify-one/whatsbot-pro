"""Cliente HTTP do Trackify — a ÚNICA porta de saída do plugin.

Antes desta versão o plugin falava com o CDP por dois caminhos, e nenhum deles era
de máquina: escrevia com **cookie de sessão de um usuário humano** (uma "conta de
serviço" cuja senha ficava no banco do WhatsBot) e lia abrindo uma **segunda
engine SQLAlchemy direto no Postgres de produção do Nexus**. O primeiro exigia um
usuário dedicado — se uma pessoa entrasse no Trackify com aquelas credenciais, as
edições dela viravam invisíveis para a sincronização. O segundo dava ao WhatsBot
credencial de banco do CRM e o prendia ao schema alheio.

Agora é só HTTP, autenticado por API key (``X-API-Key``) emitida na tela do
Trackify em Configurações → API Keys. A chave carrega escopos (``read``,
``contacts:write``, ``ingest``) e é revogável de lá.

Três decisões que este módulo carrega:

* **A chave nunca é logada.** Nem em ``logger``, nem em ``last_error``, nem no
  corpo de erro — a mesma higiene que a senha da conta de serviço exigia.
* **A taxonomia de veredito é do cliente, não do call site.** ``OK``,
  ``CONFLICT``, ``UNLINKED``, ``BLOCKED``, ``THROTTLED``, ``UNAUTHORIZED``,
  ``RETRY`` — quem chama decide o que fazer, mas não reinterpreta HTTP.
* **``UNAUTHORIZED`` não se resolve sozinho.** Com sessão, um 401 significava
  "cookie venceu" e valia uma re-tentativa. Com chave, significa chave inválida,
  revogada ou sem escopo: re-tentar é laço inútil contra o Nexus do cliente.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from . import _config

logger = logging.getLogger("plugins.trackify.client")

READ_TIMEOUT = 20.0
WRITE_TIMEOUT = 20.0

# Veredito por operação. Espelha o que o ``writer`` publicava, porque os call
# sites (fila de escrita, poller) já raciocinam nesses termos.
OK = "ok"
CONFLICT = "conflict"          # 409: outro cadastro é dono do identificador
UNLINKED = "unlinked"          # 404/403 no contato: sumiu ou foi fundido
BLOCKED = "blocked"            # 400/422/401/403: erro de configuração ou credencial
THROTTLED = "throttled"        # 429
UNAUTHORIZED = "unauthorized"  # 401/403 de credencial — subcaso de BLOCKED
RETRY = "retry"                # 5xx / rede


@dataclass
class Result:
    """Desfecho de uma chamada. ``data`` só é preenchido no sucesso."""

    verdict: str
    http_status: int = 0
    error: str = ""
    retry_after: float = 0.0
    data: Any = None
    landed: dict = field(default_factory=dict)
    dropped: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.verdict == OK


def api_key() -> str:
    return (_config.setting("sync_api_key") or "").strip()


def is_configured() -> bool:
    """Há credencial utilizável? Consultado ANTES de qualquer tentativa."""
    return bool(api_key() and _config.api_base())


def _headers() -> dict:
    return {"X-API-Key": api_key(), "Content-Type": "application/json"}


def _classify(status: int, resp) -> Result:
    """HTTP → veredito. Ponto único: dois call sites divergirem aqui seria bug."""
    if status == 401:
        return Result(UNAUTHORIZED, status,
                      "API key inválida, revogada ou expirada. Gere outra em "
                      "Configurações → API Keys no Trackify.")
    if status == 403:
        return Result(UNAUTHORIZED, status,
                      _reason(resp) or "A API key não tem permissão para esta operação.")
    if status == 409:
        return Result(CONFLICT, status,
                      _reason(resp) or "identificador já usado por outro contato")
    if status == 404:
        return Result(UNLINKED, status, "não encontrado no Trackify")
    if status == 429:
        return Result(THROTTLED, status, "limite de requisições",
                      retry_after=_retry_after(resp))
    if status in (400, 422):
        return Result(BLOCKED, status, _reason(resp) or "requisição recusada")
    return Result(RETRY, status, _reason(resp) or f"HTTP {status}")


async def _request(client, method: str, path: str, *, params=None, json=None,
                   timeout: float = READ_TIMEOUT) -> Result:
    base = _config.api_base()
    if not base:
        return Result(BLOCKED, error="URL da API do Trackify não configurada.")
    if not api_key():
        return Result(BLOCKED, error="API key do Trackify não configurada.")

    try:
        resp = await client.request(method, f"{base}{path}", params=params, json=json,
                                    headers=_headers(), timeout=timeout)
    except Exception as e:  # noqa: BLE001
        # Só o TIPO da exceção: a mensagem de um erro de request pode carregar a
        # URL, e URL com credencial embutida acontece.
        logger.warning("trackify: %s %s falhou (%s)", method, path, type(e).__name__)
        return Result(RETRY, error=type(e).__name__)

    if 200 <= resp.status_code < 300:
        return Result(OK, resp.status_code, data=_body(resp))
    return _classify(resp.status_code, resp)


# ── Leitura ──────────────────────────────────────────────────────────────

async def whoami(client) -> Result:
    """Identifica a chave. É o "testar acesso" — não escreve em contato nenhum.

    Um teste que gravasse no CRM do cliente para provar que funciona seria pior
    do que não ter teste.
    """
    return await _request(client, "GET", "/api-keys/me")


async def get_contact(client, trackify_contact_id: str) -> Result:
    return await _request(client, "GET", f"/contacts/{trackify_contact_id}")


async def resolve(client, identifiers: dict, *, limit: int = 10,
                  digits_fallback: bool = False) -> Result:
    """Resolve contatos por valores de identificador.

    POST e não GET porque identificador em query string vaza para o log de acesso
    do proxy. O servidor compara por igualdade EXATA — as variantes de telefone
    (DDI, nono dígito) são geradas aqui, em ``phone.py``, e enviadas juntas.

    ``digits_fallback`` liga a comparação só-dígitos do outro lado, que tolera
    máscara. É caminho FRIO: só vale a pena depois do exato voltar vazio, porque
    a consulta não usa o índice de identificador.
    """
    body: dict = {"identifiers": identifiers, "limit": limit}
    if digits_fallback:
        body["digitsFallback"] = True
    return await _request(client, "POST", "/contacts/resolve", json=body)


async def list_events(client, trackify_contact_id: str, *, limit: int = 25,
                      offset: int = 0, event_type: str | None = None,
                      event_types: list | None = None) -> Result:
    """Página da timeline. ``event_types`` (lista) tem precedência sobre o singular."""
    params: dict = {"limit": limit, "offset": offset}
    if event_types:
        params["eventTypes"] = ",".join(event_types)
    elif event_type:
        params["eventType"] = event_type
    return await _request(client, "GET", f"/contacts/{trackify_contact_id}/events",
                          params=params)


async def events_summary(client, trackify_contact_id: str) -> Result:
    return await _request(client, "GET",
                          f"/contacts/{trackify_contact_id}/events/summary")


async def purchases(client, trackify_contact_id: str, *, limit: int = 200,
                    state_types: list | None = None,
                    identity_fields: list | None = None) -> Result:
    params: dict = {"limit": limit}
    if state_types:
        params["stateTypes"] = ",".join(state_types)
    if identity_fields:
        params["identityFields"] = ",".join(identity_fields)
    return await _request(client, "GET", f"/contacts/{trackify_contact_id}/purchases",
                          params=params)


async def custom_fields(client) -> Result:
    """Catálogo de campos personalizados de contato do CDP."""
    return await _request(client, "GET", "/custom-fields")


async def changelog(client, *, since: float = 0.0, since_id: str = "",
                    limit: int = 500, field_slugs: list | None = None) -> Result:
    """Página do changelog a partir do cursor ``(since, since_id)``.

    O corpo devolve ``meta.serverEpoch`` (relógio do BANCO) — é com ele que o
    poller aplica a margem de segurança contra transações que abriram antes da
    leitura e commitaram depois com ``created_at`` abaixo do cursor.
    """
    params: dict = {"since": since, "limit": limit}
    if since_id:
        params["sinceId"] = since_id
    if field_slugs:
        params["fieldSlugs"] = ",".join(field_slugs)
    return await _request(client, "GET", "/contact-changelog", params=params)


# ── Escrita ──────────────────────────────────────────────────────────────

async def create_contact(client, field_values: dict, *, status: str = "lead") -> Result:
    """Cria um cadastro no CDP com ``{slug: valor}``. ``data.id`` traz o id novo.

    Só é chamada quando a resolução de identidade voltou **zero** casamentos —
    nunca em cima de ambiguidade. O 409 do outro lado (``validateUniqueness``) é
    a rede de segurança final: se outro cadastro já é dono de um dos
    identificadores, nada é criado e quem chama resolve de novo em vez de forkar.

    Contato no Trackify tem **apenas soft delete**: um cadastro criado por engano
    fica lá para sempre. Por isso quem chama filtra antes (grupo, id de sessão do
    widget, tipo de contato fora do escopo) e por isso existe o modo seco.
    """
    valores = {str(k): str(v) for k, v in (field_values or {}).items() if str(v or "").strip()}
    if not valores:
        return Result(BLOCKED, error="sem identificador para criar o cadastro")
    return await _request(client, "POST", "/contacts",
                          json={"status": status, "fieldValues": valores},
                          timeout=WRITE_TIMEOUT)


async def put_contact(client, trackify_contact_id: str, field_values: dict) -> Result:
    """Grava ``{slug: valor}``. ``""`` APAGA o campo, chave omitida não é tocada.

    Duas armadilhas do outro lado que esta função fecha:

    * **Slug desconhecido é IGNORADO em silêncio** (``if (!cf) continue``): o PUT
      devolve 200 e nada foi gravado. Por isso conferimos a resposta campo a
      campo em vez de confiar no código HTTP — é a falha mais perigosa da
      feature e custa dez linhas fechar.
    * **O ``ValidationPipe`` é ``forbidNonWhitelisted``**: qualquer chave fora de
      ``{status, tags, tagIds, fieldValues}`` derruba a requisição inteira com
      400. Por isso o corpo é montado a partir de uma lista fixa, nunca
      repassado.
    """
    if not (trackify_contact_id and field_values):
        return Result(BLOCKED, error="destino ou valores ausentes")

    enviados = {str(k): str(v) for k, v in field_values.items()}
    res = await _request(client, "PUT", f"/contacts/{trackify_contact_id}",
                         json={"fieldValues": enviados}, timeout=WRITE_TIMEOUT)
    if res.verdict != OK:
        return res
    return _verify(res, enviados)


def _verify(res: Result, sent: dict) -> Result:
    """Confere, campo a campo, o que de fato aterrissou.

    Sem isto contaríamos sucesso numa escrita que nunca aconteceu: um slug
    desativado ou renomeado no CDP é pulado em silêncio e o 200 vem igual.
    """
    try:
        payload = res.data or {}
        rows = payload.get("contactFieldValues") or []
        atual = {
            str((r.get("customField") or {}).get("slug") or ""): str(r.get("value") or "")
            for r in rows
        }
    except Exception:  # noqa: BLE001
        # Resposta ilegível: não afirmamos sucesso nem falha de gravação.
        return Result(RETRY, res.http_status, "resposta do Trackify ilegível")

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
        return Result(BLOCKED, res.http_status, dropped=dropped, landed=landed,
                      error="o Trackify ignorou: " + ", ".join(sorted(dropped))
                            + " (campo desativado ou inexistente lá)")
    return Result(OK, res.http_status, landed=landed, data=res.data)


# ── Utilitários de resposta ──────────────────────────────────────────────

def _body(resp):
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return None


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


# ── Cache leve de leitura ────────────────────────────────────────────────

_cache: dict[str, tuple[float, Any]] = {}


def _ttl() -> float:
    try:
        v = float(_config.setting("cache_ttl_seconds", 60))
        return v if v > 0 else 60.0
    except Exception:  # noqa: BLE001
        return 60.0


async def cached(key: str, fn):
    """Memoiza uma leitura por ``cache_ttl_seconds``.

    Importa mais do que importava com o DSN: abrir o modal de jornada dispara
    várias chamadas, e agora cada uma é uma ida à rede em vez de uma consulta
    local. Só resultados BEM-SUCEDIDOS entram — cachear um erro transitório o
    congelaria pelo TTL inteiro.
    """
    agora = time.time()
    hit = _cache.get(key)
    if hit and (agora - hit[0]) < _ttl():
        return hit[1]
    valor = await fn()
    if isinstance(valor, Result) and not valor.ok:
        return valor
    _cache[key] = (agora, valor)
    return valor


def invalidate_cache() -> None:
    _cache.clear()


def reset_for_tests() -> None:
    """Zera o estado de módulo. Só para a suíte — o cache é global."""
    _cache.clear()
