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
    "nexus_base_url": "",
    "cache_ttl_seconds": 60,
    "timeline_page_size": 25,
    "product_identity_fields": "product_name,offer_name,product_id,offer_id",
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
    "sync_api_base": "",
    # Consentimento de marketing por clique em botão (espelho de settings.Settings).
    "consent_enabled": False,
    "consent_dry_run": True,
    "consent_field_slug": "optout_marketing",
    "consent_optout_value": "sim",
    "consent_optout_payload": "PARAR_PROMOS",
    # Cadastro automático no CDP (espelho de settings.Settings). Nasce LIGADO:
    # sem ele o descadastro de quem ainda não está no Trackify é registrado e
    # descartado, que era o bug que motivou a feature.
    "cdp_autocadastro_enabled": True,
    "cdp_autocadastro_dry_run": False,
    # Não declarada em settings.py de propósito (segredo, ver docstring de lá).
    # É a ÚNICA credencial do plugin: vale para ler, escrever e ingerir, conforme
    # os escopos concedidos na tela do Trackify.
    "sync_api_key": "",
    # Id da API key, lido do /api-keys/me. É a chave da supressão de eco:
    # descartamos do changelog o que foi escrito pelo ator `apikey:<id>`. NÃO é
    # configurado à mão — vem da resposta do próprio Trackify.
    "sync_api_key_id": "",
    # Motivo pelo qual a sincronização se AUTO-DESLIGOU. Enquanto preenchido, o
    # worker não puxa linha da fila.
    "sync_blocked_reason": "",
    # Último erro de autenticação, gravado já na PRIMEIRA falha. Separado do
    # anterior de propósito: precisa aparecer na tela na hora, mas uma falha
    # isolada de rede não pode parar a sincronização.
    "sync_last_auth_error": "",
    # Gerado uma vez no primeiro uso do espelho; entra no external_id para que
    # staging e produção nunca colidam no mesmo canal do Trackify.
    "install_id": "",
}


def setting(key: str, default=None):
    """Lê uma setting, caindo no default declarado e depois em ``default``.

    NUNCA levanta: ler config toca o banco do WhatsBot, que pode não estar
    inicializado (boot, harness de teste, script standalone), e os call sites
    prometem não explodir. Falha vira o default, não exceção.
    """
    fallback = DEFAULTS.get(key, default)
    try:
        return config_repo.get(PREFIX + key, fallback)
    except Exception:  # noqa: BLE001
        return fallback


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
    """Há API key utilizável? Gateia leitura E escrita — desde que o plugin
    deixou de abrir conexão no Postgres do CDP, não existe mais direção que
    funcione sem credencial."""
    return bool((setting("sync_api_key") or "").strip() and api_base())


def field_sync_ready() -> bool:
    return bool(setting("field_sync_enabled", False)) and credential_set()


# ── Consentimento de marketing ───────────────────────────────────────────

def consent_field_slug() -> str:
    """Slug do campo de descadastro no CDP.

    Tem de ser o MESMO que o módulo Campanhas lê (chave de configuração
    ``trackify_campo_optout`` lá, cujo padrão é ``optout_marketing``). Os dois
    lados apontando para slugs diferentes é a falha silenciosa mais provável
    desta integração: nada quebra, o valor é gravado, e o disparo continua.
    """
    return (setting("consent_field_slug") or "").strip() or "optout_marketing"


def consent_optout_value() -> str:
    """Valor gravado quando o contato pede para NÃO receber mais."""
    return str(setting("consent_optout_value", "sim") or "")


# Os códigos do payload e o gate de prontidão NÃO moram aqui: eles dependem do
# catálogo de ações (`actions.py`), e `actions` já importa este módulo — o
# caminho de volta seria ciclo. Use `actions.snapshot()` / `actions.ready()`.


def contact_link(contact_id: str) -> str:
    """Deep-link do contato no Trackify, ou ``""`` sem base configurada."""
    base = nexus_base_url()
    return f"{base}/contacts/{contact_id}" if (base and contact_id) else ""
