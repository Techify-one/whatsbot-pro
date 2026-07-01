-- Campos EXTRAS dinâmicos do PROTOCOLO, em tabela PRÓPRIA (separável dos extras
-- da atendimento, que vivem em plugin_protocolos_campos_extras chaveada por atendimento).
-- Mesmo formato: UMA linha por (protocolo, definição), com um JSON auto-descritivo
-- {type, name, label, value}. Identidade = (protocolo_id, def_id) → upsert por def_id.
-- Apagar um rótulo extra do protocolo o some da UI mas a linha PERMANECE aqui —
-- recuperável só pelo banco, e recriar gera id novo, então o histórico antigo não volta.
--
-- SQL portável: o migrator valida o prefixo plugin_protocolos_ e traduz
-- INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL no Postgres.

CREATE TABLE IF NOT EXISTS plugin_protocolos_protocolo_extras (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    protocolo_id  INTEGER NOT NULL,             -- plugin_protocolos_protocolos.id
    def_id          TEXT    NOT NULL,             -- id estável da definição do rótulo extra
    payload         TEXT    NOT NULL DEFAULT '{}',-- JSON {type, name, label, value}
    created_at      DOUBLE PRECISION NOT NULL,
    updated_at      DOUBLE PRECISION NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS plugin_protocolos_proto_extras_uniq
    ON plugin_protocolos_protocolo_extras(protocolo_id, def_id);
CREATE INDEX IF NOT EXISTS plugin_protocolos_proto_extras_owner
    ON plugin_protocolos_protocolo_extras(protocolo_id);
