-- Ordem das colunas do Kanban por visualizacao (EQUIPE) e por usuario (PESSOAL).
-- JSON array de column ids na ordem desejada. NULL = ordem padrao (a de buildGrouping).
-- Ids desconhecidos/faltando sao tratados no frontend (aparecem no fim, na ordem natural).
--
-- column_order          vive em plugin_protocolos_kanban_views  (default da EQUIPE, gated por
--                       manage_team_views ao salvar via PUT /kanban-views).
-- personal_column_order vive em plugin_protocolos_user_view_prefs (preferencia do PROPRIO
--                       usuario, salva via PUT /kanban-views/{vid}/my-pref).
--
-- SQL portavel: o migrator valida o prefixo plugin_protocolos_ e ADD COLUMN roda em SQLite e
-- Postgres. Nao usar ponto-e-virgula nos comentarios (o splitter quebra por ele).

ALTER TABLE plugin_protocolos_kanban_views ADD COLUMN column_order TEXT;
ALTER TABLE plugin_protocolos_user_view_prefs ADD COLUMN personal_column_order TEXT;
