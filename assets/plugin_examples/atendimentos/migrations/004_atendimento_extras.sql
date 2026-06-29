-- Campos EXTRAS dinâmicos do ATENDIMENTO, em tabela PRÓPRIA (separável dos extras
-- da conversa, que vivem em plugin_atendimentos_campos_extras chaveada por conversa).
-- Mesmo formato: UMA linha por (atendimento, definição), com um JSON auto-descritivo
-- {type, name, label, value}. Identidade = (atendimento_id, def_id) → upsert por def_id.
-- Apagar um rótulo extra do atendimento o some da UI mas a linha PERMANECE aqui —
-- recuperável só pelo banco, e recriar gera id novo, então o histórico antigo não volta.
--
-- SQL portável: o migrator valida o prefixo plugin_atendimentos_ e traduz
-- INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL no Postgres.

CREATE TABLE IF NOT EXISTS plugin_atendimentos_atendimento_extras (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    atendimento_id  INTEGER NOT NULL,             -- plugin_atendimentos_atendimentos.id
    def_id          TEXT    NOT NULL,             -- id estável da definição do rótulo extra
    payload         TEXT    NOT NULL DEFAULT '{}',-- JSON {type, name, label, value}
    created_at      DOUBLE PRECISION NOT NULL,
    updated_at      DOUBLE PRECISION NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS plugin_atendimentos_atend_extras_uniq
    ON plugin_atendimentos_atendimento_extras(atendimento_id, def_id);
CREATE INDEX IF NOT EXISTS plugin_atendimentos_atend_extras_owner
    ON plugin_atendimentos_atendimento_extras(atendimento_id);
