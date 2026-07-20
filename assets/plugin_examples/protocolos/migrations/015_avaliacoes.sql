-- Avaliacoes de protocolo (round-trip da pagina externa de avaliacao).
-- UMA linha por FECHAMENTO/link enviado ao cliente: nasce no fechamento com o
-- id_protocol (chave da URL) mais o snapshot de atendente/contato/conversa/canal,
-- e recebe nota/sugestao quando o cliente responde pela pagina externa. E 1:N por
-- protocolo (reabrir e refechar gera um link novo). Portavel: o migrator valida o
-- prefixo plugin_protocolos_, traduz INTEGER PRIMARY KEY AUTOINCREMENT para SERIAL
-- e roda statement a statement. Timestamps DOUBLE PRECISION (epoch float).

CREATE TABLE IF NOT EXISTS plugin_protocolos_avaliacoes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    id_protocol       TEXT    NOT NULL,             -- chave publica da URL (codigo exibido)
    protocolo_id      INTEGER NOT NULL,             -- plugin_protocolos_protocolos.id
    contact_id        INTEGER NOT NULL,
    conversation_id   INTEGER,                      -- conversa mais recente no fechamento
    channel_id        TEXT    NOT NULL DEFAULT '',
    assignee_user_id  INTEGER,                      -- core users.id do atendente
    assignee_name     TEXT    NOT NULL DEFAULT '',  -- snapshot p/ o GET publico (ATENDENTE)
    contact_phone     TEXT    NOT NULL DEFAULT '',
    contact_name      TEXT    NOT NULL DEFAULT '',
    nota              INTEGER,                       -- NULL ate responder (1..5)
    sugestao          TEXT    NOT NULL DEFAULT '',
    answered_at       DOUBLE PRECISION,              -- NULL = pendente (gate de uso unico)
    answered_ip       TEXT    NOT NULL DEFAULT '',
    created_at        DOUBLE PRECISION NOT NULL,
    updated_at        DOUBLE PRECISION NOT NULL
);

-- id_protocol e a chave da URL publica de consulta/gravacao (lookup por ele).
CREATE UNIQUE INDEX IF NOT EXISTS plugin_protocolos_avaliacoes_idp
    ON plugin_protocolos_avaliacoes(id_protocol);
CREATE INDEX IF NOT EXISTS plugin_protocolos_avaliacoes_proto
    ON plugin_protocolos_avaliacoes(protocolo_id);
CREATE INDEX IF NOT EXISTS plugin_protocolos_avaliacoes_answered
    ON plugin_protocolos_avaliacoes(answered_at);
