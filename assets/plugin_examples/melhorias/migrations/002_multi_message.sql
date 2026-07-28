-- Plano 51 (02 F6) - multi-selecao de mensagens por sugestao.
-- As colunas single (message_db_id / message_ts / message_content) permanecem
-- como ANCORA (1a mensagem, deep-link ?message= e busca legada). O conjunto
-- completo vai nesta tabela filha, na ordem da selecao (seq).
-- Atencao: comentarios sem ponto-e-virgula (o migrator splita por ele).

CREATE TABLE IF NOT EXISTS plugin_melhorias_suggestion_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    suggestion_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    message_db_id INTEGER,
    message_ts DOUBLE PRECISION,
    message_content TEXT NOT NULL DEFAULT '',
    media_type TEXT,
    media_path TEXT
);

CREATE INDEX IF NOT EXISTS plugin_melhorias_sugmsgs_idx
    ON plugin_melhorias_suggestion_messages (suggestion_id, seq);
