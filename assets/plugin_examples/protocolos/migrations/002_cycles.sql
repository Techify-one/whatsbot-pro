-- Atendimentos do protocolo passam a ser CICLOS (aberto→resolvido): uma atendimento
-- pode ter VÁRIAS linhas (cada ida-e-volta do cliente), então o índice único por
-- conversation_id sai. Mantém um índice comum para os lookups por atendimento.
-- DROP/CREATE INDEX são portáveis (SQLite + Postgres) e o nome respeita o prefixo.

DROP INDEX IF EXISTS plugin_protocolos_atend_unique;

CREATE INDEX IF NOT EXISTS plugin_protocolos_atend_atend
    ON plugin_protocolos_atendimentos(conversation_id);
