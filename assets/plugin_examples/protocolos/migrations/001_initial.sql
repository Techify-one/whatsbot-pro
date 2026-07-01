-- Protocolos plugin — schema inicial.
-- SQL portável: o migrator valida o prefixo plugin_protocolos_, traduz
-- INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL no Postgres e roda statement a
-- statement (split em ';'). JSON é guardado como TEXT (json.loads/dumps na
-- logic.py) e timestamps como DOUBLE PRECISION (epoch float, paridade com o core).

-- Um protocolo agrupa MUITAS atendimentos de UM contato.
CREATE TABLE IF NOT EXISTS plugin_protocolos_protocolos (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id        INTEGER NOT NULL,             -- core contacts.id (sem FK cross-table: tabelas do plugin auto-contidas)
    contact_phone     TEXT    NOT NULL DEFAULT '',  -- snapshot p/ a coluna CLIENTE sem join ao core
    contact_name      TEXT    NOT NULL DEFAULT '',  -- snapshot do nome de exibição (atualizado a cada vínculo)
    status            TEXT    NOT NULL DEFAULT 'aberto',  -- 'aberto' | 'fechado' (ciclo próprio, independente da atendimento)
    assignee_user_id  INTEGER,                      -- core users.id do atendente que fechou/possui (nullable)
    assignee_name     TEXT    NOT NULL DEFAULT '',  -- snapshot do nome do atendente (coluna ATENDENTE sem join)
    fields            TEXT    NOT NULL DEFAULT '{}',-- JSON: campos configuráveis do protocolo (motivo_abertura, resultado, tipo, ...)
    opened_at         DOUBLE PRECISION NOT NULL,
    closed_at         DOUBLE PRECISION,             -- NULL enquanto aberto (coluna DATA FECHAMENTO)
    created_at        DOUBLE PRECISION NOT NULL,
    updated_at        DOUBLE PRECISION NOT NULL
);

-- "No máximo 1 protocolo ABERTO por contato": índice único parcial (SQLite 3.8+ e Postgres).
-- O nome do índice DEVE começar com plugin_protocolos_ (validado pelo migrator).
CREATE UNIQUE INDEX IF NOT EXISTS plugin_protocolos_one_open_per_contact
    ON plugin_protocolos_protocolos(contact_id)
    WHERE status = 'aberto';

CREATE INDEX IF NOT EXISTS plugin_protocolos_proto_contact
    ON plugin_protocolos_protocolos(contact_id);
CREATE INDEX IF NOT EXISTS plugin_protocolos_proto_status
    ON plugin_protocolos_protocolos(status);

-- Vínculo explícito: cada atendimento do core pertence a um protocolo. Carrega os
-- campos de resolução por-atendimento ("adicionar as ligações das atendimentos nessa tabela").
CREATE TABLE IF NOT EXISTS plugin_protocolos_atendimentos (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    protocolo_id    INTEGER NOT NULL,             -- plugin_protocolos_protocolos.id
    conversation_id   INTEGER NOT NULL,             -- core conversations.id
    contact_id        INTEGER NOT NULL,
    assignee_name     TEXT    NOT NULL DEFAULT '',  -- coluna ATENDENTE da sub-tabela ATENDIMENTOS
    fields            TEXT    NOT NULL DEFAULT '{}',-- JSON: campos de resolução (resultado, observacao, ...)
    started_at        DOUBLE PRECISION NOT NULL,    -- coluna INÍCIO (criação do vínculo)
    ended_at          DOUBLE PRECISION,             -- coluna FIM (gravada ao resolver a atendimento)
    created_at        DOUBLE PRECISION NOT NULL,
    updated_at        DOUBLE PRECISION NOT NULL
);

-- Um vínculo por atendimento (idempotência do get-or-create).
CREATE UNIQUE INDEX IF NOT EXISTS plugin_protocolos_atend_unique
    ON plugin_protocolos_atendimentos(conversation_id);
CREATE INDEX IF NOT EXISTS plugin_protocolos_atend_proto
    ON plugin_protocolos_atendimentos(protocolo_id);
