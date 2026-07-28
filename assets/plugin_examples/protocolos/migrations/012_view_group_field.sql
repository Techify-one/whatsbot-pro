-- Escopo do CAMPO DE PROTOCOLO usado no agrupamento do Kanban (group_by='pfield').
-- Valores: protocolo | atendimento. A CHAVE do campo reusa a coluna group_attr_key. NULL
-- (o default) nos demais group_by (status/atendente/data) e nas views legadas.
--
-- SQL portavel: o migrator valida o prefixo plugin_protocolos_ e ADD COLUMN roda em SQLite e
-- Postgres. Nao usar ponto-e-virgula nos comentarios (o splitter quebra por ele).

ALTER TABLE plugin_protocolos_kanban_views ADD COLUMN group_field_scope TEXT;
