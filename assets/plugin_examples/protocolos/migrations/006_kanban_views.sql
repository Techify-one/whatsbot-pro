-- Visualizacoes personalizadas do Kanban de Protocolos (abas de "Agrupar por").
--
-- Cada linha e uma aba do kanban: define o AGRUPAMENTO (status/atendente/data/atributo),
-- os FILTROS pre-determinados (JSON) e o ESCOPO (pessoal=so o criador ve, equipe=todos
-- veem). Criar/editar visualizacao de EQUIPE exige a permissao plugin.protocolos.
-- manage_team_views (gate na rota). As abas built-in Status e Atendente NAO vivem aqui
-- (sao virtuais no frontend) -- esta tabela guarda apenas as visualizacoes criadas pelo
-- usuario. group_attr_key so e usado quando group_by='attr' (attribute_key de atendimento,
-- type=list). group_date_mode so e usado quando group_by='data' (dia|faixas|mes).
--
-- SQL portavel: o migrator valida o prefixo plugin_protocolos_, traduz
-- INTEGER PRIMARY KEY AUTOINCREMENT -> SERIAL no Postgres e roda statement a statement.
-- Nao usar ponto-e-virgula nos comentarios (o splitter quebra por ele).

CREATE TABLE IF NOT EXISTS plugin_protocolos_kanban_views (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL DEFAULT '',         -- rotulo da aba
    scope           TEXT    NOT NULL DEFAULT 'personal', -- 'personal' | 'team'
    owner_user_id   INTEGER,                             -- users.id do criador (NULL em legado/open)
    group_by        TEXT    NOT NULL DEFAULT 'status',   -- 'status' | 'atendente' | 'data' | 'attr'
    group_attr_key  TEXT,                                -- so quando group_by='attr'
    group_date_mode TEXT,                                -- so quando group_by='data': dia|faixas|mes
    filters         TEXT    NOT NULL DEFAULT '{}',       -- JSON dos filtros pre-determinados
    position        INTEGER NOT NULL DEFAULT 0,          -- ordem das abas custom
    created_at      DOUBLE PRECISION NOT NULL,
    updated_at      DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS plugin_protocolos_kv_scope
    ON plugin_protocolos_kanban_views(scope);
CREATE INDEX IF NOT EXISTS plugin_protocolos_kv_owner
    ON plugin_protocolos_kanban_views(owner_user_id);
CREATE INDEX IF NOT EXISTS plugin_protocolos_kv_pos
    ON plugin_protocolos_kanban_views(position);
