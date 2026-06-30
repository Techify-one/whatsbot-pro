-- Campos EXTRAS dinâmicos da conversa-ciclo + rótulo fixo OBS.
--
-- Os rótulos fixos (Início/Fim/Atendente/ID + OBS) viram colunas, e os rótulos
-- EXTRAS (criados/removidos pelo operador) saem do blob `fields` e passam a viver
-- nesta tabela normalizada: UMA linha por (conversa-ciclo, definição), com um JSON
-- auto-descritivo {type, name, label, value}. Assim adicionar um tipo de campo novo
-- nunca muda o schema, e um rótulo deletado some da UI mas a linha PERMANECE aqui —
-- recuperável só pelo banco. Identidade da linha = (conversa_id, def_id): o def_id é
-- o id estável da definição, e recriar um rótulo gera id novo, então o histórico do
-- rótulo antigo nunca reaparece pela interface.
--
-- SQL portável: o migrator valida o prefixo plugin_atendimentos_, traduz
-- INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL no Postgres e roda statement a statement.

CREATE TABLE IF NOT EXISTS plugin_atendimentos_campos_extras (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    conversa_id   INTEGER NOT NULL,              -- plugin_atendimentos_conversas.id (ID_CONVERSA_ATENDIMENTO)
    def_id        TEXT    NOT NULL,              -- id estável da definição do rótulo extra
    payload       TEXT    NOT NULL DEFAULT '{}', -- JSON {type, name, label, value}
    created_at    DOUBLE PRECISION NOT NULL,
    updated_at    DOUBLE PRECISION NOT NULL
);

-- Uma linha por (conversa, definição): suporta o upsert por def_id.
CREATE UNIQUE INDEX IF NOT EXISTS plugin_atendimentos_campos_extras_uniq
    ON plugin_atendimentos_campos_extras(conversa_id, def_id);
CREATE INDEX IF NOT EXISTS plugin_atendimentos_campos_extras_conv
    ON plugin_atendimentos_campos_extras(conversa_id);

-- Rótulo fixo OBS vira coluna própria nas duas entidades (rótulo fixo ⇒ coluna fixa).
ALTER TABLE plugin_atendimentos_atendimentos ADD COLUMN obs TEXT NOT NULL DEFAULT '';
ALTER TABLE plugin_atendimentos_conversas    ADD COLUMN obs TEXT NOT NULL DEFAULT '';
