-- Filtros FAVORITOS por visualização (aba) do Kanban: subconjunto de available_filters que
-- aparece SEMPRE na barra de filtros, sem poder ser recolhido pelo botão "Filtros". É metadado
-- da VIEW (decidido por quem edita a visualização, no painel "Configurar filtros da aba"), não
-- por-usuário -- mesmo modelo de available_filters.
--
-- JSON array de chaves (mesmo esquema de available_filters): status | atendente | q | periodo |
-- canal | pf:<scope>:<key> | cattr:<key>. NULL/[] = NENHUM favorito (barra começa expandida).
-- Um favorito precisa estar disponível (favorite_filters e' um subconjunto de available_filters).
--
-- SQL portavel: o migrator valida o prefixo plugin_protocolos_ e ADD COLUMN roda em SQLite e
-- Postgres. Nao usar ponto-e-virgula nos comentarios (o splitter quebra por ele).

ALTER TABLE plugin_protocolos_kanban_views ADD COLUMN favorite_filters TEXT;
