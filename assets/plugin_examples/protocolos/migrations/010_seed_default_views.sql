-- Seed das abas padrao "Status" e "Atendente" como VIEWS REAIS (antes eram sinteticas no
-- frontend). Viram linhas normais editaveis/excluiveis, iguais as criadas pela interface.
-- scope team + owner_user_id NULL + sem ACL (visibility_* NULL) = legado "todos veem" e
-- editar/excluir exige manage_team_views (mesma regra das demais views de equipe).
-- position 0 e 1 as colocam a esquerda. created_at/updated_at sao epoch float LITERAL
-- (nao ha now-epoch portavel e o migrator nao traduz strftime/extract) -- update_kanban_view
-- sobrescreve updated_at no 1o edit. INSERT guardado por NOT EXISTS = idempotente e nao
-- duplica uma aba que o usuario ja tenha criado manualmente com esse nome/agrupamento.

INSERT INTO plugin_protocolos_kanban_views
    (name, scope, owner_user_id, group_by, group_attr_key, group_date_mode,
     filters, position, created_at, updated_at)
SELECT 'Status', 'team', NULL, 'status', NULL, NULL, '{}', 0, 1751328000, 1751328000
WHERE NOT EXISTS (
    SELECT 1 FROM plugin_protocolos_kanban_views
    WHERE group_by = 'status' AND scope = 'team' AND owner_user_id IS NULL AND name = 'Status'
);

INSERT INTO plugin_protocolos_kanban_views
    (name, scope, owner_user_id, group_by, group_attr_key, group_date_mode,
     filters, position, created_at, updated_at)
SELECT 'Atendente', 'team', NULL, 'atendente', NULL, NULL, '{}', 1, 1751328000, 1751328000
WHERE NOT EXISTS (
    SELECT 1 FROM plugin_protocolos_kanban_views
    WHERE group_by = 'atendente' AND scope = 'team' AND owner_user_id IS NULL AND name = 'Atendente'
);
