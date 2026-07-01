-- Preferencia POR-USUARIO e POR-VISUALIZACAO dos filtros pre-determinados do Kanban.
--
-- Ao entrar numa aba (visualizacao), cada usuario escolhe se aplica os filtros da EQUIPE
-- (os compartilhados, na coluna filters de plugin_protocolos_kanban_views, definidos por
-- quem tem manage_team_views) ou os filtros PESSOAIS dele. use_personal=0 (default) usa os
-- da equipe -- comportamento atual inalterado ate o usuario optar pelos pessoais.
-- personal_filters guarda o mesmo shape JSON de kanban_views.filters
-- ({status, q, assignee_user_id, date:{preset,from,to}, attrs:{key:val}}).
--
-- Uma linha por (user_id, view_id) -- o UPSERT casa no indice unico. Ao apagar a
-- visualizacao, as linhas de preferencia dela tambem caem (limpeza no delete_kanban_view,
-- sem FK cross-table -- tabelas do plugin auto-contidas).
--
-- SQL portavel: o migrator valida o prefixo plugin_protocolos_, traduz
-- INTEGER PRIMARY KEY AUTOINCREMENT -> SERIAL no Postgres e roda statement a statement.
-- Booleano e guardado como INTEGER (0/1) -- nao existe BOOLEAN portavel no projeto.
-- Nao usar ponto-e-virgula nos comentarios (o splitter quebra por ele).

CREATE TABLE IF NOT EXISTS plugin_protocolos_user_view_prefs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,                 -- core users.id do dono da preferencia
    view_id          INTEGER NOT NULL,                 -- plugin_protocolos_kanban_views.id
    use_personal     INTEGER NOT NULL DEFAULT 0,       -- 0 = filtros da EQUIPE | 1 = PESSOAIS
    personal_filters TEXT    NOT NULL DEFAULT '{}',    -- JSON dos filtros pessoais desta aba
    created_at       DOUBLE PRECISION NOT NULL,
    updated_at       DOUBLE PRECISION NOT NULL
);

-- Uma preferencia por (usuario, visualizacao): chave do UPSERT (ON CONFLICT).
CREATE UNIQUE INDEX IF NOT EXISTS plugin_protocolos_uvp_uniq
    ON plugin_protocolos_user_view_prefs(user_id, view_id);

-- Lookup por usuario ao anexar a preferencia na listagem de visualizacoes.
CREATE INDEX IF NOT EXISTS plugin_protocolos_uvp_user
    ON plugin_protocolos_user_view_prefs(user_id);

-- Lookup por visualizacao ao limpar preferencias no delete da visualizacao.
CREATE INDEX IF NOT EXISTS plugin_protocolos_uvp_view
    ON plugin_protocolos_user_view_prefs(view_id);
