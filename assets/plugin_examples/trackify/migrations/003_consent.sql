-- Consentimento de marketing por clique em botão de template.
--
-- O contato toca um botão de um template disparado pelo módulo Campanhas, o
-- clique chega neste WhatsBot como mensagem interativa, e o resultado vira um
-- valor no campo de descadastro do cadastro dele no CDP. É esse campo que o
-- Campanhas lê para decidir quem entra na lista de disparo -- ou seja, o campo
-- é o ÚNICO elo entre os dois sistemas.
--
-- Mesmas convenções do 001 e do 002, todas obrigatórias:
--   * prefixo plugin_trackify_ em TODA tabela e TODO índice
--   * timestamps em DOUBLE PRECISION (epoch)
--   * sem FK cross-table (contact_id e channel_id são snapshots do core)
--   * nada de DO $$ ... $$
-- E NUNCA um ponto-e-vírgula dentro de comentário: o migrador divide o arquivo
-- por ponto-e-vírgula de forma ingênua, inclusive dentro de comentário, e o
-- pedaço solto vira "syntax error" na subida do plugin.

-- ── Regras de botão (configuração) ───────────────────────────────────────
-- O que cada botão SIGNIFICA. Mora em TABELA pelo mesmo motivo do field_map do
-- 002: o PUT de settings do core é destrutivo (Settings(**body) + model_dump
-- inteiro), então um array em settings seria revertido por qualquer save de
-- outra aba. Os contadores são incrementais. E só o UNIQUE de banco fecha a
-- corrida entre duas abas abertas na mesma tela.
--
-- A CHAVE DE CASAMENTO é match_norm, não o nome do template. O objeto que a
-- Meta manda no clique não carrega nome de template nem índice de botão -- o
-- único elo seria o wamid da mensagem original, que exigiria ter registrado o
-- envio, e quem envia é o Campanhas, fora daqui. template_name e
-- template_category são DOCUMENTAIS: dizem de onde a regra veio na tela e
-- guardam a decisão de só aceitar regra vinda de template MARKETING.
CREATE TABLE IF NOT EXISTS plugin_trackify_consent_button (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    -- '' = regra global (vale para qualquer canal permitido). Um channel_id
    -- preenchido tem PRECEDÊNCIA sobre a global -- é o que permite políticas
    -- diferentes por número numa instalação multi-número.
    channel_id          TEXT NOT NULL DEFAULT '',
    -- any | payload | text  (em 'any' o payload tem precedência sobre o rótulo)
    match_field         TEXT NOT NULL DEFAULT 'any',
    -- Como o operador digitou/importou. Só exibição.
    match_value         TEXT NOT NULL,
    -- Forma canônica -- é ISSO que casa em runtime.
    match_norm          TEXT NOT NULL,
    -- optout | optin | ignore
    meaning             TEXT NOT NULL,
    -- Proveniência (documental): de qual template da Meta o botão foi importado.
    template_name       TEXT NOT NULL DEFAULT '',
    template_category   TEXT NOT NULL DEFAULT '',
    enabled             INTEGER NOT NULL DEFAULT 1,
    position            INTEGER NOT NULL DEFAULT 0,
    -- Contadores. Sempre por UPDATE incremental, nunca reescrevendo a linha.
    hits                INTEGER NOT NULL DEFAULT 0,
    last_hit_at         DOUBLE PRECISION,
    created_at          DOUBLE PRECISION NOT NULL,
    updated_at          DOUBLE PRECISION NOT NULL
);

-- Uma regra por (canal, token). A global e a do canal coexistem de propósito:
-- são chaves diferentes, e a resolução escolhe a mais específica.
CREATE UNIQUE INDEX IF NOT EXISTS plugin_trackify_consent_button_key
    ON plugin_trackify_consent_button (channel_id, match_norm);
CREATE INDEX IF NOT EXISTS plugin_trackify_consent_button_active
    ON plugin_trackify_consent_button (enabled, position);

-- ── Canais permitidos (configuração) ─────────────────────────────────────
-- Allow-list explícita, FAIL-CLOSED: nenhum canal marcado = nada é enfileirado.
-- Escrever no CRM do cliente por default silencioso é pior do que uma tela que
-- pede um clique. O gate duro de provider ('whatsapp_cloud') é aplicado em
-- código -- esta tabela é o recorte de QUAIS canais oficiais participam.
CREATE TABLE IF NOT EXISTS plugin_trackify_consent_channel (
    channel_id  TEXT PRIMARY KEY,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  DOUBLE PRECISION NOT NULL,
    updated_at  DOUBLE PRECISION NOT NULL
);

-- ── Estado desejado por contato ──────────────────────────────────────────
-- O que o último clique daquele contato quer que o CDP diga, mais a
-- proveniência (qual canal, qual botão, qual mensagem) -- sem ela, um
-- descadastro contestado não tem como ser auditado.
--
-- Convenção herdada do field_state: written_value NULL = nunca gravamos,
-- '' = gravamos o APAGAMENTO. Só essa diferença separa "ainda não sincronizou"
-- de "sincronizou e o valor certo é vazio".
CREATE TABLE IF NOT EXISTS plugin_trackify_consent_state (
    contact_id          INTEGER PRIMARY KEY,
    -- optout | optin
    desired             TEXT NOT NULL DEFAULT '',
    channel_id          TEXT NOT NULL DEFAULT '',
    -- O clique cru, para auditoria.
    raw_payload         TEXT NOT NULL DEFAULT '',
    raw_text            TEXT NOT NULL DEFAULT '',
    msg_id              TEXT NOT NULL DEFAULT '',
    -- Regra que casou. Sem FK, por regra do repo.
    button_id           INTEGER,
    clicked_at          DOUBLE PRECISION NOT NULL,
    clicks              INTEGER NOT NULL DEFAULT 0,
    written_value       TEXT,
    written_at          DOUBLE PRECISION,
    trackify_contact_id TEXT NOT NULL DEFAULT '',
    last_error          TEXT NOT NULL DEFAULT '',
    created_at          DOUBLE PRECISION NOT NULL,
    updated_at          DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS plugin_trackify_consent_state_desired
    ON plugin_trackify_consent_state (desired, updated_at);

-- ── Fila de entrega ──────────────────────────────────────────────────────
-- Cópia estrutural da fila do 002, inclusive o índice único parcial: o handler
-- do barramento NUNCA posta (o bus é fire-and-forget e engole exceção, então
-- uma falha de rede dentro do handler seria perda silenciosa de um dado com
-- peso legal). O handler só marca o contato, e a task supervisionada entrega
-- recalculando o valor na hora.
--
-- Fila PRÓPRIA e não a do field sync: aquela é dirigida pelo mapeamento
-- atributo-do-WhatsBot <-> slug-do-CDP, e o campo de descadastro não tem
-- contraparte no WhatsBot. Reusá-la arrastaria junto o motor de conflito e o
-- poller de volta -- que poderia SOBRESCREVER um descadastro com o valor
-- antigo do CDP.
CREATE TABLE IF NOT EXISTS plugin_trackify_consent_outbox (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id          INTEGER NOT NULL,
    reason              TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'pending',
    attempts            INTEGER NOT NULL DEFAULT 0,
    next_attempt_at     DOUBLE PRECISION NOT NULL DEFAULT 0,
    last_error          TEXT NOT NULL DEFAULT '',
    last_http_status    INTEGER,
    locked_by           TEXT NOT NULL DEFAULT '',
    locked_at           DOUBLE PRECISION,
    enqueued_at         DOUBLE PRECISION NOT NULL,
    created_at          DOUBLE PRECISION NOT NULL,
    updated_at          DOUBLE PRECISION NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS plugin_trackify_consent_outbox_pending
    ON plugin_trackify_consent_outbox (contact_id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS plugin_trackify_consent_outbox_due
    ON plugin_trackify_consent_outbox (status, next_attempt_at);

-- ── Botões vistos e ainda sem significado ────────────────────────────────
-- É o que torna a configuração possível sem adivinhação. A Meta ecoa o TEXTO
-- do botão como payload quando o template não define um payload explícito, e o
-- template é criado do lado do Campanhas -- então o operador não tem como saber
-- de antemão qual string vai chegar. Esta tabela registra todo clique
-- reconhecível que não casou regra, com contador e data, e a tela oferece
-- "usar como regra" em um clique.
--
-- Gravada mesmo com o consentimento desligado ou em modo seco, de propósito:
-- sem ela o operador não teria material para configurar.
CREATE TABLE IF NOT EXISTS plugin_trackify_consent_seen (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id      TEXT NOT NULL DEFAULT '',
    match_norm      TEXT NOT NULL,
    raw_payload     TEXT NOT NULL DEFAULT '',
    raw_text        TEXT NOT NULL DEFAULT '',
    -- button | button_reply | list_reply | outro
    shape           TEXT NOT NULL DEFAULT '',
    hits            INTEGER NOT NULL DEFAULT 0,
    first_seen_at   DOUBLE PRECISION NOT NULL,
    last_seen_at    DOUBLE PRECISION NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS plugin_trackify_consent_seen_key
    ON plugin_trackify_consent_seen (channel_id, match_norm);
CREATE INDEX IF NOT EXISTS plugin_trackify_consent_seen_recent
    ON plugin_trackify_consent_seen (last_seen_at);
