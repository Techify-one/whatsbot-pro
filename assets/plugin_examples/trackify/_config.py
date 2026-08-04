"""Leitura das settings do plugin (prefixo ``plugin.trackify.``).

Os DEFAULTS repetem os de ``settings.Settings`` porque o form só materializa
valores no ``config`` quando o usuário salva — antes disso, ler com o default
correto é responsabilidade do call site. Centralizado aqui para não divergir.
"""

from __future__ import annotations

from db.repositories import config_repo

PREFIX = "plugin.trackify."

# Espelho dos defaults de settings.Settings (fonte da verdade lá).
DEFAULTS: dict = {
    "nexus_dsn": "",
    "nexus_base_url": "",
    "cache_ttl_seconds": 60,
    "timeline_page_size": 25,
    "product_identity_fields": "product_name,offer_name,product_id,offer_id",
    "statement_timeout_ms": 5000,
    "mirror_enabled": False,
    "mirror_dry_run": True,
    "ingestion_url": "",
    "rate_per_min": 40,
    "max_age_days": 7,
    "mirror_contact_types": "whatsapp",
    # Sincronização de campos do contato (espelho de settings.Settings).
    "field_sync_enabled": False,
    "field_sync_dry_run": True,
    "field_sync_pull_enabled": False,
    "field_sync_poll_seconds": 60,
    "field_sync_rate_per_min": 15,
    "field_sync_reconcile_minutes": 60,
    "service_email": "",
    "sync_api_base": "",
    # Não declarados em settings.py de propósito (segredo, ver docstring de lá).
    "api_key": "",
    "service_password": "",
    # Id do usuário da conta de serviço, capturado no login. É a chave da
    # supressão de eco (descartamos do changelog o que foi escrito por nós), e
    # por isso NÃO é configurado à mão: vem do corpo da resposta de login.
    "sync_user_id": "",
    # Motivo pelo qual a sincronização se AUTO-DESLIGOU (3 falhas seguidas).
    # Enquanto preenchido, o worker não puxa linha da fila.
    "sync_blocked_reason": "",
    # Último erro de login, gravado já na PRIMEIRA falha. Separado do anterior de
    # propósito: precisa aparecer na tela na hora, mas uma falha isolada de rede
    # não pode parar a sincronização.
    "sync_last_login_error": "",
    # Gerado uma vez no primeiro uso do espelho; entra no external_id para que
    # staging e produção nunca colidam no mesmo canal do Trackify.
    "install_id": "",
}


def setting(key: str, default=None):
    """Lê uma setting, caindo no default declarado e depois em ``default``.

    NUNCA levanta: ``trackify_db.ping``/``schema_check`` prometem não levantar, e
    ler config toca o banco do WhatsBot (que pode não estar inicializado — boot,
    harness de teste, script standalone). Falha vira o default, não exceção.
    """
    fallback = DEFAULTS.get(key, default)
    try:
        return config_repo.get(PREFIX + key, fallback)
    except Exception:  # noqa: BLE001
        return fallback


def nexus_dsn() -> str:
    return (setting("nexus_dsn") or "").strip()


def nexus_base_url() -> str:
    return (setting("nexus_base_url") or "").strip().rstrip("/")


def timeline_page_size() -> int:
    try:
        v = int(setting("timeline_page_size", 25))
    except (TypeError, ValueError):
        return 25
    return min(max(v, 5), 100)


# Piso do que a aba Produtos consegue nomear. É o DEFAULT, não uma allow-list:
# a setting substitui a lista inteira, e o único caso em que este piso reaparece
# é o operador ter apagado o campo.
PRODUCT_IDENTITY_FIELDS = ("product_name", "offer_name", "product_id", "offer_id")


def product_identity_fields() -> list[str]:
    """Campos que nomeiam um produto, EM ORDEM (o 1º preenchido ganha).

    Configurável de propósito: cada gateway do CDP nomeia de um jeito (o ``ticto``
    preenche ``product_name``/``offer_name``, o ``pagarme`` só os ``*_id``), e um
    canal novo com um slug diferente não pode exigir release do plugin. Lista
    vazia/ilegível volta ao piso — nunca devolve vazio, que apagaria a aba.
    """
    bruto = setting("product_identity_fields", "")
    campos = [p.strip() for p in str(bruto or "").split(",") if p.strip()]
    # Sem duplicata e preservando a ORDEM digitada (dict mantém inserção).
    campos = list(dict.fromkeys(campos))
    return campos or list(PRODUCT_IDENTITY_FIELDS)


def api_base() -> str:
    """Base da API do Trackify (``.../api/v1``), com precedência explícita.

    Deduzir da URL de ingestão evita pedir ao operador uma segunda URL que ele
    já forneceu — o ``channelSlug()`` da tela já faz essa mesma dedução para
    descobrir o canal. Sem nenhuma das duas, cai na URL pública do módulo.
    """
    explicit = (setting("sync_api_base") or "").strip().rstrip("/")
    if explicit:
        return explicit
    ing = (setting("ingestion_url") or "").strip().rstrip("/")
    marker = "/ingestion/"
    if marker in ing:
        return ing[: ing.index(marker)]
    base = nexus_base_url()
    return f"{base}/api/v1" if base else ""


def credential_set() -> bool:
    """Há conta de serviço utilizável? Usado para gatear a escrita SEM tentar
    login — a rota de login do Nexus é limitada a 5 tentativas por minuto."""
    return bool((setting("service_email") or "").strip()
                and (setting("service_password") or "").strip()
                and api_base())


def field_sync_ready() -> bool:
    return bool(setting("field_sync_enabled", False)) and credential_set()


def contact_link(contact_id: str) -> str:
    """Deep-link do contato no Trackify, ou ``""`` sem base configurada."""
    base = nexus_base_url()
    return f"{base}/contacts/{contact_id}" if (base and contact_id) else ""
