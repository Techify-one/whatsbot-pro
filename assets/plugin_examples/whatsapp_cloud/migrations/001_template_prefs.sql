-- Preferências de template (plano 92 · D1).
--
-- Duas tabelas com semânticas DIFERENTES de propósito:
--   favorites  -> PESSOAL   (chave inclui user_id, cada atendente vê a sua)
--   archived   -> GLOBAL    (um marca e some para todos, sob permissão)
--
-- A lista de templates em si NÃO mora aqui: ela vem da Graph API a cada abertura
-- do modal, com cache de 5 min no provider. Aqui só guardamos a marcação local,
-- por isso a chave é o NOME do template, que é o identificador estável dentro de
-- uma WABA. Um template apagado na Meta deixa uma linha órfã inofensiva: nada a
-- reconcilia porque nada a lê sem cruzar com a lista viva.
--
-- Escopo por channel_id: o mesmo nome pode existir em WABAs diferentes.
-- ATENÇÃO ao migrator: ele divide o arquivo em ponto-e-vírgula ANTES de remover
-- comentários, então nenhum comentário aqui pode conter esse caractere.

CREATE TABLE IF NOT EXISTS plugin_whatsapp_cloud_template_favorites (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,             -- core users.id (sem FK: tabela de plugin é auto-contida)
    channel_id     TEXT    NOT NULL,
    template_name  TEXT    NOT NULL,
    created_at     DOUBLE PRECISION NOT NULL     -- epoch float, paridade com o core
);

CREATE UNIQUE INDEX IF NOT EXISTS plugin_whatsapp_cloud_template_fav_uniq
    ON plugin_whatsapp_cloud_template_favorites (user_id, channel_id, template_name);

CREATE INDEX IF NOT EXISTS plugin_whatsapp_cloud_template_fav_lookup
    ON plugin_whatsapp_cloud_template_favorites (user_id, channel_id);

CREATE TABLE IF NOT EXISTS plugin_whatsapp_cloud_template_archived (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id        TEXT    NOT NULL,
    template_name     TEXT    NOT NULL,
    archived_by       INTEGER,                      -- core users.id, NULL em instalação aberta
    archived_by_name  TEXT    NOT NULL DEFAULT '',  -- snapshot do nome, para a tela não precisar de join
    archived_at       DOUBLE PRECISION NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS plugin_whatsapp_cloud_template_arch_uniq
    ON plugin_whatsapp_cloud_template_archived (channel_id, template_name);
