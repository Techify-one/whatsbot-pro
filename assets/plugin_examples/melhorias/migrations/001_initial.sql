-- Plugin "melhorias" -- schema inicial.
-- SQL portavel: o migrator valida o prefixo plugin_melhorias_, traduz
-- INTEGER PRIMARY KEY AUTOINCREMENT em SERIAL no Postgres e roda statement a
-- statement (split no ponto-e-virgula). Timestamps como DOUBLE PRECISION
-- (epoch float, paridade com o core). Textos livres como TEXT.
-- IMPORTANTE: nunca usar ponto-e-virgula dentro de comentario (o splitter quebra nele).

-- Um pedido de sugestao de melhoria de UMA resposta da IA.
-- status: pendente (recem-solicitado) / aprovada (analise gerada) / recusada.
CREATE TABLE IF NOT EXISTS plugin_melhorias_suggestions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id         INTEGER NOT NULL,             -- core contacts.id (sem FK cross-table)
    contact_phone      TEXT    NOT NULL DEFAULT '',  -- snapshot p/ deep-link + display sem join
    contact_name       TEXT    NOT NULL DEFAULT '',  -- snapshot do nome de exibicao
    conversation_id    INTEGER,                      -- core conversations.id da resposta marcada (nullable = default/legado)
    message_db_id      INTEGER,                      -- core messages.id da resposta marcada (ancora ?message= do deep-link)
    message_ts         DOUBLE PRECISION,             -- ts da resposta marcada (janela de execucao na geracao)
    message_content    TEXT    NOT NULL DEFAULT '',  -- snapshot da resposta da IA (coluna "conteudo da mensagem")
    requester_user_id  INTEGER,                      -- core users.id de quem solicitou (nullable em legado/open)
    requester_name     TEXT    NOT NULL DEFAULT '',  -- snapshot do nome do solicitante
    feedback           TEXT    NOT NULL DEFAULT '',  -- nota do operador ("o que saiu errado")
    analysis           TEXT    NOT NULL DEFAULT '',  -- analise gerada pela IA (vazia ate aprovar)
    model              TEXT    NOT NULL DEFAULT '',  -- modelo usado na geracao (preenchido na aprovacao)
    status             TEXT    NOT NULL DEFAULT 'pendente',
    requested_at       DOUBLE PRECISION NOT NULL,    -- horario do pedido
    decided_at         DOUBLE PRECISION,             -- horario da decisao (aprovar/recusar) -- NULL enquanto pendente
    approver_user_id   INTEGER,                      -- core users.id de quem decidiu (nullable)
    approver_name      TEXT    NOT NULL DEFAULT '',  -- snapshot do nome de quem decidiu
    created_at         DOUBLE PRECISION NOT NULL,
    updated_at         DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS plugin_melhorias_sugg_status
    ON plugin_melhorias_suggestions(status);
CREATE INDEX IF NOT EXISTS plugin_melhorias_sugg_contact
    ON plugin_melhorias_suggestions(contact_id);
CREATE INDEX IF NOT EXISTS plugin_melhorias_sugg_requested
    ON plugin_melhorias_suggestions(requested_at);
CREATE INDEX IF NOT EXISTS plugin_melhorias_sugg_conv
    ON plugin_melhorias_suggestions(conversation_id);
