-- Conversas do atendimento passam a ser CICLOS (aberto→resolvido): uma conversa
-- pode ter VÁRIAS linhas (cada ida-e-volta do cliente), então o índice único por
-- conversation_id sai. Mantém um índice comum para os lookups por conversa.
-- DROP/CREATE INDEX são portáveis (SQLite + Postgres) e o nome respeita o prefixo.

DROP INDEX IF EXISTS plugin_atendimentos_conv_unique;

CREATE INDEX IF NOT EXISTS plugin_atendimentos_conv_conv
    ON plugin_atendimentos_conversas(conversation_id);
