"""Sessão da conta de serviço no Nexus/Trackify.

As rotas de contato do Trackify aceitam **só cookie de sessão** — não há bearer,
não há API key (a chave de ingestão vale unicamente para ``/ingestion/<canal>``,
que não consegue sobrescrever identificador nem apagar valor). O único caminho
máquina é ``POST /auth/login`` e raspar o ``Set-Cookie``.

Três coisas que essa decisão obriga e que estão implementadas aqui:

* **A conta precisa ser DEDICADA.** O ``user.id`` devolvido no login é a chave da
  supressão de eco: descartamos do changelog do CDP tudo que foi escrito por
  ele. Se uma pessoa entrar na tela do Trackify com essas credenciais, as
  edições dela serão ignoradas pela sincronização.
* **A senha nunca é logada.** Nem em ``logger``, nem em ``last_error``, nem no
  corpo de erro — que na rota de login pode ecoar o que foi enviado.
* **Login não entra em laço.** A rota é limitada a 5/min, e um laço de login
  contra o Nexus do cliente é o pior resultado que esta feature pode produzir:
  backoff exponencial e auto-bloqueio após 3 falhas seguidas.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from . import _config

logger = logging.getLogger("plugins.trackify.session")

COOKIE_NAME = "trackify_session"
LOGIN_TIMEOUT = 15.0
# Renovação PROATIVA. Atenção à pegadinha do lado deles: o cookie sai com
# maxAge de 168h, mas a LINHA da sessão expira com SESSION_EXPIRY_HOURS||'24' —
# e é a linha que o guard valida. 12h fica com folga sob o piso de 24h.
SESSION_MAX_AGE = 12 * 3600.0
LOGIN_BACKOFF_BASE = 60.0
LOGIN_BACKOFF_CAP = 900.0
MAX_LOGIN_FAILURES = 3


@dataclass(frozen=True)
class Session:
    cookie: str        # "trackify_session=<token>"
    user_id: str       # chave da supressão de eco
    obtained_at: float


_lock = asyncio.Lock()
_current: Session | None = None
_cooldown_until = 0.0
_failures = 0


def is_configured() -> bool:
    """Há credencial utilizável? Consultado ANTES de qualquer tentativa: sem
    isto, uma instalação sem conta de serviço bateria na rota de login (que é
    limitada a 5/min) a cada ciclo do worker."""
    return _config.credential_set()


def blocked_reason() -> str:
    return (_config.setting("sync_blocked_reason") or "").strip()


def cached_user_id() -> str:
    """Id da conta de serviço persistido no último login.

    Existe para o poller aplicar a supressão de eco sem precisar de um login
    neste processo. Vazio significa "nunca logamos" — e nesse caso o poller NÃO
    roda, porque sem esta chave ele reimportaria as próprias escritas como se
    fossem edições humanas.
    """
    return (_config.setting("sync_user_id") or "").strip()


def _set_config(**pairs) -> None:
    from db.repositories import config_repo
    config_repo.set_many({_config.PREFIX + k: v for k, v in pairs.items()})


def invalidate() -> None:
    global _current
    _current = None


async def get(client, *, force: bool = False):
    """Sessão válida, logando se preciso. ``None`` quando não dá — nunca levanta."""
    global _current, _cooldown_until, _failures

    if not is_configured():
        return None
    async with _lock:
        now = time.time()
        if not force and _current and (now - _current.obtained_at) < SESSION_MAX_AGE:
            return _current
        if now < _cooldown_until or _failures >= MAX_LOGIN_FAILURES:
            return None

        sess = await _login(client)
        if sess is None:
            _failures += 1
            _cooldown_until = now + min(
                LOGIN_BACKOFF_BASE * (2 ** (_failures - 1)), LOGIN_BACKOFF_CAP)
            if _failures >= MAX_LOGIN_FAILURES:
                # Auto-bloqueio: senha errada não pode virar fila infinita nem
                # martelar a rota de login do cliente.
                await asyncio.to_thread(
                    _set_config,
                    sync_blocked_reason="Login recusado pelo Nexus 3 vezes seguidas. "
                                        "Confira o e-mail e a senha da conta de serviço.")
            return None

        _failures = 0
        _current = sess
        await asyncio.to_thread(_set_config, sync_user_id=sess.user_id,
                                sync_blocked_reason="", sync_last_login_error="")
        return sess


async def _login(client):
    base = _config.api_base()
    email = (_config.setting("service_email") or "").strip()
    password = (_config.setting("service_password") or "").strip()
    if not (base and email and password):
        return None
    try:
        resp = await client.post(f"{base}/auth/login",
                                 json={"email": email, "password": password},
                                 timeout=LOGIN_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        # Só o TIPO da exceção: a mensagem de um erro de request pode carregar
        # a URL, e URL com credencial embutida acontece.
        logger.warning("trackify: login falhou (%s)", type(e).__name__)
        return None

    if not login_ok(resp.status_code):
        # NUNCA o corpo: a resposta de erro do login ecoa o que foi enviado.
        logger.warning("trackify: login recusado (HTTP %s)", resp.status_code)
        await asyncio.to_thread(_set_config,
                                sync_last_login_error=login_error_message(resp.status_code))
        return None

    token = cookie_from(resp)
    if not token:
        logger.warning("trackify: login aceito mas sem cookie de sessão na resposta")
        return None
    uid = user_id_from(resp)
    if not uid:
        # Sem o id não há supressão de eco possível — melhor recusar a sessão do
        # que sincronizar sem conseguir reconhecer as próprias escritas.
        logger.warning("trackify: login sem 'user.id' no corpo — sessão descartada")
        return None
    return Session(cookie=f"{COOKIE_NAME}={token}", user_id=uid,
                   obtained_at=time.time())


def login_ok(status: int) -> bool:
    """Qualquer 2xx é sucesso.

    ⚠️ NÃO comparar com 200: a rota é ``@Post('login')`` SEM ``@HttpCode``, e o
    padrão do NestJS para POST é **201**. O ``@ApiResponse({status: 200})`` que
    está no controller é só documentação do Swagger e mente sobre o runtime —
    exigir 200 fazia um login BEM-SUCEDIDO ser descartado como recusa.
    """
    return 200 <= int(status) < 300


def login_error_message(status: int) -> str:
    """Traduz o status do login para algo acionável.

    Distinguir 401 de 5xx não é cosmético: mandar o operador conferir a senha
    quando o problema é do servidor faz ele trocar a senha e continuar quebrado.
    """
    if status == 401:
        return ("Credenciais recusadas pelo Nexus (401). Confira o e-mail e a senha "
                "da conta de serviço.")
    if status == 429:
        return "Muitas tentativas de login (429). A rota aceita 5 por minuto."
    if status in (400, 422):
        return f"O Nexus recusou os dados do login (HTTP {status})."
    if status >= 500:
        return (f"Erro interno do Trackify ao autenticar (HTTP {status}). A credencial "
                f"provavelmente está certa: uma senha errada volta como 401, e este erro "
                f"acontece DEPOIS da checagem de senha — ao criar a sessão. Verifique se "
                f"a tabela 'trackify_sessions' existe no banco do Trackify.")
    return f"Login recusado (HTTP {status})."


def last_login_error() -> str:
    return (_config.setting("sync_last_login_error") or "").strip()


def cookie_from(resp) -> str:
    """Extrai o token do cabeçalho ``set-cookie``.

    Lido do CABEÇALHO e não de ``resp.cookies``: o corpo do login devolve só o
    usuário, e o jar de um client não-persistente é fácil de ler errado.
    """
    try:
        raws = list(resp.headers.get_list("set-cookie"))
    except AttributeError:
        raw = resp.headers.get("set-cookie") or ""
        raws = [raw] if raw else []
    for raw in raws:
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE_NAME and v:
                return v
    return ""


def user_id_from(resp) -> str:
    try:
        return str(((resp.json() or {}).get("user") or {}).get("id") or "")
    except Exception:  # noqa: BLE001
        return ""


async def probe(client, *, email: str = "", password: str = "") -> dict:
    """Testa a credencial e devolve um veredito legível. Só faz LOGIN.

    Nunca escreve num contato real: um "teste" que gravasse no CRM do cliente
    para provar que funciona seria pior do que não ter teste nenhum.
    """
    base = _config.api_base()
    if not base:
        return {"ok": False, "message": "URL da API do Trackify não configurada."}

    if email or password:
        # Testa credenciais AINDA NÃO salvas, para o operador conferir antes de
        # gravar. Senha como sentinela cai na já guardada.
        password = password if password and password != "***" else (
            _config.setting("service_password") or "")
        email = email or (_config.setting("service_email") or "")
        if not (email and password):
            return {"ok": False, "message": "Informe e-mail e senha."}
        try:
            resp = await client.post(f"{base}/auth/login",
                                     json={"email": email.strip(),
                                           "password": password.strip()},
                                     timeout=LOGIN_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            return {"ok": False,
                    "message": f"Não foi possível falar com o Trackify ({type(e).__name__})."}
        if not login_ok(resp.status_code):
            return {"ok": False, "message": login_error_message(resp.status_code)}
        if not cookie_from(resp):
            return {"ok": False, "message": "Login aceito, mas sem cookie de sessão."}
        return {"ok": True, "message": "Login aceito.", "api_base": base,
                "user_id": user_id_from(resp)}

    if not is_configured():
        return {"ok": False, "message": "Conta de serviço não configurada."}
    sess = await get(client, force=True)
    if sess is None:
        return {"ok": False,
                "message": (blocked_reason() or last_login_error()
                            or "Não foi possível autenticar.")}
    return {"ok": True, "message": "Login aceito.", "api_base": base,
            "user_id": sess.user_id}


def reset_for_tests() -> None:
    """Zera o estado de módulo. Só para a suíte — o backoff e o contador de
    falhas são globais e vazariam de um teste para o outro."""
    global _current, _cooldown_until, _failures
    _current, _cooldown_until, _failures = None, 0.0, 0
