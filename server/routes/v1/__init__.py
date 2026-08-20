"""Fachada ``/api/v1`` — a superfície ESTÁVEL para integrações externas.

Quatro domínios (D6 do plano): contatos+busca, mensagens, conversas+filtros e
catálogo (etiquetas, atributos, canais/inboxes em leitura). Administração
(usuários, papéis, configuração, motor de IA, auditoria, plugins, escrita de
canal) fica **fora** desta versão.

A autenticação NÃO acontece aqui: a chave ``X-Api-Key`` é resolvida no middleware
de ``server/app.py`` para o mesmo ``request.state.user`` que uma sessão de painel
resolve, então RBAC, escopo por inbox e auditoria valem sem nada de novo. Uma
sessão de painel também abre estas rotas — são a mesma identidade.

O OpenAPI (``/docs``) é gerado pelo FastAPI a partir das assinaturas e docstrings
daqui; não há documentação escrita à mão para manter em dia.
"""

from fastapi import Request
from fastapi.openapi.utils import get_openapi

from server.routes.v1 import catalog, contacts, conversations, messages
from server.routes.v1._common import V1_PREFIX, install_v1_handlers

V1_VERSION = "1.0.0"


def register_routes(app, deps) -> None:
    install_v1_handlers(app)
    contacts.register_routes(app, deps)
    messages.register_routes(app, deps)
    conversations.register_routes(app, deps)
    catalog.register_routes(app, deps)

    _cache: dict = {}

    @app.get(f"{V1_PREFIX}/openapi.json", include_in_schema=False)
    async def v1_openapi(request: Request):
        """Esquema OpenAPI **só das rotas ``/api/v1``** — pronto para codegen.

        Fica sob ``/api/*`` de propósito: o middleware o protege como qualquer
        outra rota, então uma chave válida (ou uma sessão de painel) o lê e um
        anônimo não mapeia a superfície da instalação. É a alternativa sempre
        disponível ao ``/docs`` global, que segue atrás de
        ``WHATSBOT_ENABLE_DOCS`` e exporia a API inteira do painel.
        """
        if "schema" not in _cache:
            full = get_openapi(
                title="WhatsBot-Pro — API v1",
                version=V1_VERSION,
                description=(
                    "API para integrações externas. Autentique com o cabeçalho "
                    "`X-Api-Key: wsk_live_...`; a chave age como o USUÁRIO dono, "
                    "então as permissões e o escopo de caixa de entrada dele valem "
                    "em cada rota."
                ),
                routes=app.routes,
            )
            paths = {p: v for p, v in (full.get("paths") or {}).items()
                     if p.startswith(V1_PREFIX)}
            full["paths"] = paths
            full["components"] = full.get("components") or {}
            full["components"]["securitySchemes"] = {
                "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-Api-Key"}
            }
            full["security"] = [{"ApiKeyAuth": []}]
            _cache["schema"] = full
        return _cache["schema"]
