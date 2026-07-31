"""Settings declarativas do plugin ``trackify``.

Renderizadas pelo ``PluginSettingsForm`` e persistidas com prefixo
``plugin.trackify.<campo>`` em ``config``.

Os DEFAULTS aqui são a fonte da verdade — ``_config.setting`` repete os mesmos
defaults ao ler (o form só materializa valores quando o usuário salva).

⚠️ A CHAVE de ingestão (escrita) NÃO mora aqui: ``GET /api/plugins/{id}/settings``
devolve os valores em claro e o form genérico ignora ``format: password``. Ela é
gravada/lida por rota própria com sentinela ``"***"`` (padrão do plugin
``melhorias``). O DSN fica aqui seguindo o precedente do ``vendas_ia``, que já
guarda o MESMO segredo desta forma — divergir criaria duas convenções para a
mesma credencial.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Settings(BaseModel):
    # ── Leitura: conexão com o Nexus (read-only) ─────────────────────────
    nexus_dsn: str = Field(
        default="",
        title="DSN do Nexus (read-only)",
        description=(
            "String de conexão SQLAlchemy para o banco do Nexus (RBNexusDB), onde "
            "vivem as tabelas do Trackify. É o MESMO valor usado pelo plugin Vendas IA. "
            "O SSL é forçado (sslmode=require) e a transação é read-only no servidor. "
            "SEM este DSN o plugin fica em no-op: a tela mostra 'não configurado'. "
            "Nunca é logado."
        ),
        json_schema_extra={"format": "password"},
    )
    nexus_base_url: str = Field(
        default="",
        title="URL do Trackify (para 'Abrir no Trackify')",
        description=(
            "Base pública do módulo, sem barra no fim — ex.: https://SEU-NEXUS/trackify . "
            "Usada só para montar o link do contato. Vazio esconde o botão."
        ),
    )

    # ── Desempenho da leitura ────────────────────────────────────────────
    cache_ttl_seconds: int = Field(
        default=60, ge=0, le=3600,
        title="Cache da jornada (segundos)",
        description=(
            "Por quanto tempo a jornada de um contato fica em memória. 0 desliga o "
            "cache. Curto de propósito: o vendedor quer ver o dado de agora."
        ),
    )
    timeline_page_size: int = Field(
        default=25, ge=5, le=100,
        title="Eventos por página na linha do tempo",
    )
    statement_timeout_ms: int = Field(
        default=5000, ge=500, le=30000,
        title="Tempo máximo de cada consulta (ms)",
        description=(
            "Teto aplicado pelo servidor do Nexus. Protege um banco de PRODUÇÃO "
            "compartilhado de uma consulta ruim."
        ),
    )

    # ── Escrita (espelho para o CDP) — dormente até ser ligado ───────────
    mirror_enabled: bool = Field(
        default=False,
        title="Espelhar acontecimentos no Trackify",
        description=(
            "Quando ligado, conversas/protocolos/contatos viram eventos no CDP. "
            "Exige o canal e os mapeamentos criados no Trackify e a chave de ingestão "
            "configurada. Deixe DESLIGADO até concluir essa configuração — senão a "
            "fila só acumula erro."
        ),
    )
    mirror_dry_run: bool = Field(
        default=True,
        title="Modo seco (não envia de verdade)",
        description=(
            "Enfileira e monta o envelope, mas NÃO posta. Serve para conferir o que "
            "seria enviado antes de tocar no CDP de produção."
        ),
    )
    ingestion_url: str = Field(
        default="",
        title="URL de ingestão do Trackify",
        description=(
            "Ex.: https://SEU-NEXUS/trackify/api/v1/ingestion/whatsbot . "
            "O slug no fim é o do canal criado no Trackify."
        ),
    )
    rate_per_min: int = Field(
        default=40, ge=1, le=60,
        title="Envios por minuto",
        description=(
            "O Trackify limita a ingestão a 60/min POR IP (não por canal). 40 deixa "
            "33% de folga — nunca rode no teto."
        ),
    )
    max_age_days: int = Field(
        default=7, ge=1, le=90,
        title="Idade máxima na fila (dias)",
        description=(
            "Evento que não conseguiu ser entregue nesse prazo é descartado com "
            "contador, em vez de fazer a fila crescer para sempre."
        ),
    )
    mirror_contact_types: str = Field(
        default="whatsapp",
        title="Tipos de contato a espelhar",
        description=(
            "Lista separada por vírgula. Grupos NUNCA são espelhados. "
            "'outros' (ex.: Messenger) fica de fora por padrão porque o 'telefone' "
            "ali não é telefone."
        ),
    )
