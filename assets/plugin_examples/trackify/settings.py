"""Settings declarativas do plugin ``trackify``.

Renderizadas pelo ``PluginSettingsForm`` e persistidas com prefixo
``plugin.trackify.<campo>`` em ``config``.

Os DEFAULTS aqui são a fonte da verdade — ``_config.setting`` repete os mesmos
defaults ao ler (o form só materializa valores quando o usuário salva).

⚠️ **Nenhum SEGREDO mora aqui**: ``GET /api/plugins/{id}/settings`` devolve os
valores em claro e o form genérico ignora ``format: password``. A API key do
Trackify (``sync_api_key``) e a chave de ingestão do canal (``api_key``) são
gravadas/lidas por rota própria com sentinela ``"***"`` — padrão do plugin
``melhorias``.

O antigo ``nexus_dsn`` saiu daqui junto com a conexão direta ao Postgres do CDP:
o plugin fala com o Trackify exclusivamente por HTTP.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Settings(BaseModel):
    # ── Leitura ──────────────────────────────────────────────────────────
    #
    # Não há mais DSN: o plugin fala com o Trackify SÓ por HTTP, autenticado
    # pela API key (que é segredo e por isso não é declarada aqui — ver a nota
    # no fim do arquivo).
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
    product_identity_fields: str = Field(
        default="product_name,offer_name,product_id,offer_id",
        title="Campos que identificam o produto (em ordem)",
        description=(
            "Lista separada por vírgula dos campos de evento do CDP usados para "
            "nomear um produto na aba Produtos — o primeiro preenchido ganha. "
            "Existe porque cada gateway nomeia de um jeito: o ticto preenche "
            "product_name/offer_name e o pagarme só product_id/offer_id. Se um "
            "canal novo passar a usar um campo diferente, acrescente o slug dele "
            "aqui em vez de esperar uma atualização do plugin — a própria aba avisa "
            "quais campos as compras não identificadas trazem. Vazio volta ao padrão."
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
    # ⚠️ A API KEY não mora aqui, pelo mesmo motivo da chave
    # de ingestão (ver o aviso no topo deste arquivo). Só o e-mail, que é
    # identificador público.
    field_sync_enabled: bool = Field(
        default=False,
        title="Sincronizar campos do contato",
        description=(
            "Liga a sincronização dos campos mapeados na aba 'Campos do contato'. "
            "Exige a API key configurada. Só vale para contatos já "
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
    sync_api_base: str = Field(
        default="",
        title="URL da API do Trackify (opcional)",
        description=(
            "Ex.: https://SEU-NEXUS/trackify/api/v1 . Em branco, é deduzida da URL "
            "de ingestão e, na falta dela, da URL do Trackify."
        ),
    )

    # ── Consentimento de marketing por clique em botão ───────────────────
    # Só ESCALARES aqui. A lista de canais permitidos e o mapa de botões moram
    # em tabela (ver migrations/003_consent.sql): o PUT de settings do core é
    # destrutivo, e um array aqui seria zerado por qualquer save de outra aba.
    consent_enabled: bool = Field(
        default=False,
        title="Registrar descadastro por clique em botão",
        description=(
            "Quando o contato toca um botão de template num canal de WhatsApp "
            "oficial, grava o resultado no campo de descadastro do cadastro dele "
            "no Trackify. Só vale para canais marcados na aba, e só para contatos "
            "já vinculados a um cadastro lá — nunca cria contato no CDP."
        ),
    )
    consent_dry_run: bool = Field(
        default=True,
        title="Modo seco (não grava no Trackify)",
        description=(
            "Captura o clique e registra o que gravaria, mas NÃO envia. Deixe "
            "ligado até conferir, na lista de botões vistos, que o texto que "
            "chega da Meta é o que você espera."
        ),
    )
    consent_field_slug: str = Field(
        default="optout_marketing",
        title="Campo de descadastro no Trackify",
        description=(
            "Slug do campo personalizado de contato. Tem de ser EXATAMENTE o "
            "mesmo configurado no módulo Campanhas (chave trackify_campo_optout) "
            "— apontar para slugs diferentes não quebra nada e faz o disparo "
            "continuar indo para quem pediu para sair."
        ),
    )
    consent_optout_value: str = Field(
        default="sim",
        title="Valor gravado ao descadastrar",
        description=(
            "O Campanhas trata vazio, 'nao', 'não', 'n', 'no', 'false' e '0' como "
            "quem CONTINUA recebendo — qualquer outro valor bloqueia. Use 'sim', "
            "'true' ou a data do descadastro."
        ),
    )
    consent_optin_value: str = Field(
        default="",
        title="Valor gravado ao voltar a receber",
        description=(
            "Vazio APAGA o campo no Trackify, que é o estado mais limpo e o único "
            "que libera o contato sob qualquer leitura do contrato."
        ),
    )
    consent_rate_per_min: int = Field(
        default=10, ge=1, le=25,
        title="Gravações de consentimento por minuto",
        description=(
            "Divide com a sincronização de campos o mesmo balde de 30/min por IP "
            "das rotas de contato do Trackify. Mantenha a soma dos dois com folga."
        ),
    )
