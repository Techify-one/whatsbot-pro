-- Atendimentos plugin — schema inicial.
-- SQL portável: o migrator valida o prefixo plugin_atendimentos_, traduz
-- INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL no Postgres e roda statement a
-- statement (split em ';'). JSON é guardado como TEXT (json.loads/dumps na
-- logic.py) e timestamps como DOUBLE PRECISION (epoch float, paridade com o core).

-- Um atendimento agrupa MUITAS conversas de UM contato.
CREATE TABLE IF NOT EXISTS plugin_atendimentos_atendimentos (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id        INTEGER NOT NULL,             -- core contacts.id (sem FK cross-table: tabelas do plugin auto-contidas)
    contact_phone     TEXT    NOT NULL DEFAULT '',  -- snapshot p/ a coluna CLIENTE sem join ao core
    contact_name      TEXT    NOT NULL DEFAULT '',  -- snapshot do nome de exibição (atualizado a cada vínculo)
    status            TEXT    NOT NULL DEFAULT 'aberto',  -- 'aberto' | 'fechado' (ciclo próprio, independente da conversa)
    assignee_user_id  INTEGER,                      -- core users.id do atendente que fechou/possui (nullable)
    assignee_name     TEXT    NOT NULL DEFAULT '',  -- snapshot do nome do atendente (coluna ATENDENTE sem join)
    fields            TEXT    NOT NULL DEFAULT '{}',-- JSON: campos configuráveis do atendimento (motivo_abertura, resultado, tipo, ...)
    opened_at         DOUBLE PRECISION NOT NULL,
    closed_at         DOUBLE PRECISION,             -- NULL enquanto aberto (coluna DATA FECHAMENTO)
    created_at        DOUBLE PRECISION NOT NULL,
    updated_at        DOUBLE PRECISION NOT NULL
);

-- "No máximo 1 atendimento ABERTO por contato": índice único parcial (SQLite 3.8+ e Postgres).
-- O nome do índice DEVE começar com plugin_atendimentos_ (validado pelo migrator).
CREATE UNIQUE INDEX IF NOT EXISTS plugin_atendimentos_one_open_per_contact
    ON plugin_atendimentos_atendimentos(contact_id)
    WHERE status = 'aberto';

CREATE INDEX IF NOT EXISTS plugin_atendimentos_atend_contact
    ON plugin_atendimentos_atendimentos(contact_id);
CREATE INDEX IF NOT EXISTS plugin_atendimentos_atend_status
    ON plugin_atendimentos_atendimentos(status);

-- Vínculo explícito: cada conversa do core pertence a um atendimento. Carrega os
-- campos de resolução por-conversa ("adicionar as ligações das conversas nessa tabela").
CREATE TABLE IF NOT EXISTS plugin_atendimentos_conversas (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    atendimento_id    INTEGER NOT NULL,             -- plugin_atendimentos_atendimentos.id
    conversation_id   INTEGER NOT NULL,             -- core conversations.id
    contact_id        INTEGER NOT NULL,
    assignee_name     TEXT    NOT NULL DEFAULT '',  -- coluna ATENDENTE da sub-tabela CONVERSAS
    fields            TEXT    NOT NULL DEFAULT '{}',-- JSON: campos de resolução (resultado, observacao, ...)
    started_at        DOUBLE PRECISION NOT NULL,    -- coluna INÍCIO (criação do vínculo)
    ended_at          DOUBLE PRECISION,             -- coluna FIM (gravada ao resolver a conversa)
    created_at        DOUBLE PRECISION NOT NULL,
    updated_at        DOUBLE PRECISION NOT NULL
);

-- Um vínculo por conversa (idempotência do get-or-create).
CREATE UNIQUE INDEX IF NOT EXISTS plugin_atendimentos_conv_unique
    ON plugin_atendimentos_conversas(conversation_id);
CREATE INDEX IF NOT EXISTS plugin_atendimentos_conv_atend
    ON plugin_atendimentos_conversas(atendimento_id);
