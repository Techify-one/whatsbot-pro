-- Visibilidade granular da visualização (aba) do Kanban: quem PODE VER, por GRUPOS de
-- permissão (roles) e por USUÁRIOS individuais, com exclusão. Substitui a escolha binária
-- Pessoal/Equipe do editor por uma ACL (as duas dimensões operam juntas).
--
-- visibility_roles         JSON array de role.key (grupos que veem). NULL/[] = sem grupo.
-- visibility_users_include JSON array de users.id que SEMPRE veem (soma com os grupos).
-- visibility_users_exclude JSON array de users.id que NUNCA veem (mesmo que um grupo permita).
--
-- Regra (em logic._view_visible): o CRIADOR (owner_user_id) e quem tem papel 'admin' veem
-- SEMPRE. Sem grupos e sem incluídos numa view de EQUIPE = legado "todos veem" (compat).
-- scope continua existindo (personal|team) e é DERIVADO: há grupo/incluído -> team.
--
-- SQL portavel: o migrator valida o prefixo plugin_protocolos_ e ADD COLUMN roda em
-- SQLite e Postgres. Nao usar ponto-e-virgula nos comentarios (o splitter quebra por ele).

ALTER TABLE plugin_protocolos_kanban_views ADD COLUMN visibility_roles TEXT;
ALTER TABLE plugin_protocolos_kanban_views ADD COLUMN visibility_users_include TEXT;
ALTER TABLE plugin_protocolos_kanban_views ADD COLUMN visibility_users_exclude TEXT;
