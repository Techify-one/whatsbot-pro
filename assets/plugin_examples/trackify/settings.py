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

    # ── Sincronização de campos do contato ───────────────────────────────
    # ⚠️ A SENHA da conta de serviço não mora aqui, pelo mesmo motivo da chave
    # de ingestão (ver o aviso no topo deste arquivo). Só o e-mail, que é
    # identificador público.
    field_sync_enabled: bool = Field(
        default=False,
        title="Sincronizar campos do contato",
        description=(
            "Liga a sincronização dos campos mapeados na aba 'Campos do contato'. "
            "Exige a conta de serviço configurada. Só vale para contatos já "
            "vinculados a um cadastro no Trackify — nunca cria contato lá."
        ),
    )
    field_sync_dry_run: bool = Field(
        default=True,
        title="Modo seco (não grava no Trackify)",
        description=(
            "Calcula o que seria escrito e registra, mas NÃO envia. Deixe ligado "
            "até conferir o resultado num contato pelo botão 'Simular'."
        ),
    )
    field_sync_pull_enabled: bool = Field(
        default=False,
        title="Trazer alterações do Trackify",
        description=(
            "Liga a leitura periódica do histórico de alterações do CDP. Necessário "
            "para as direções ← e ↔. O Trackify não tem webhook de saída, então a "
            "volta é sempre por consulta periódica."
        ),
    )
    field_sync_poll_seconds: int = Field(
        default=60, ge=15, le=3600,
        title="Intervalo da leitura (segundos)",
        description="De quanto em quanto tempo procuramos alterações feitas no Trackify.",
    )
    field_sync_rate_per_min: int = Field(
        default=15, ge=1, le=25,
        title="Gravações por minuto no Trackify",
        description=(
            "O Trackify limita as rotas de contato a 30/min POR IP — e esse limite é "
            "compartilhado com qualquer outra chamada que saia pelo mesmo IP. 15 "
            "deixa metade de folga."
        ),
    )
    field_sync_reconcile_minutes: int = Field(
        default=60, ge=15, le=1440,
        title="Varredura de conferência (minutos)",
        description=(
            "Passagem periódica que corrige o que evento e leitura não veem — "
            "importação por CSV, por exemplo, não emite evento nenhum."
        ),
    )
    service_email: str = Field(
        default="",
        title="E-mail da conta de serviço do Nexus",
        description=(
            "Usuário DEDICADO do Nexus com acesso ao Trackify. Precisa ser exclusivo "
            "desta integração: se uma pessoa usar essas credenciais para entrar na "
            "tela do Trackify, as edições dela serão ignoradas pela sincronização."
        ),
    )
    sync_api_base: str = Field(
        default="",
        title="URL da API do Trackify (opcional)",
        description=(
            "Ex.: https://SEU-NEXUS/trackify/api/v1 . Em branco, é deduzida da URL "
            "de ingestão e, na falta dela, da URL do Trackify."
        ),
    )
