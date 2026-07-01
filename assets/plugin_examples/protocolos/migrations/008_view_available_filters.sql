-- Filtros DISPONÍVEIS por visualização (aba) do Kanban. Define quais TIPOS de filtro a aba
-- expõe -- na barra de filtros ao vivo E na seção de pré-determinados do editor. É metadado
-- da VIEW (decidido por quem edita a visualização), não por-usuário.
--
-- JSON array de chaves: status | atendente | q | periodo | attr:<attribute_key> (um por
-- atributo de atendimento tipo list). NULL = TODOS os filtros disponíveis (compatibilidade com
-- as views existentes e default das novas -- inclui atributos criados no futuro).
--
-- SQL portavel: o migrator valida o prefixo plugin_protocolos_ e ADD COLUMN roda em
-- SQLite e Postgres. Nao usar ponto-e-virgula nos comentarios (o splitter quebra por ele).

ALTER TABLE plugin_protocolos_kanban_views ADD COLUMN available_filters TEXT;
