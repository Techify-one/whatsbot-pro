-- Janela de datas + granularidade para o agrupamento por Data no modo "personalizado".
-- Quando group_by='data' e group_date_mode='personalizado': group_date_from/group_date_to
-- delimitam a janela (YYYY-MM-DD) e group_date_grain define o tamanho do bucket (dia|semana|mes).
-- NULL nos demais modos de data (faixas|dia|semana|mes) e nos outros group_by.
--
-- SQL portavel: o migrator valida o prefixo plugin_protocolos_ e ADD COLUMN roda em SQLite e
-- Postgres. Nao usar ponto-e-virgula nos comentarios (o splitter quebra por ele).

ALTER TABLE plugin_protocolos_kanban_views ADD COLUMN group_date_from TEXT;
ALTER TABLE plugin_protocolos_kanban_views ADD COLUMN group_date_to TEXT;
ALTER TABLE plugin_protocolos_kanban_views ADD COLUMN group_date_grain TEXT;
