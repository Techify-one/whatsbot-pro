-- Quem ABRIU o protocolo e cada atendimento (ciclo). O "quem FECHOU" reutiliza o
-- assignee ja existente (assignee_user_id/assignee_name) por decisao do produto, entao
-- so precisamos das colunas de ABERTURA aqui.
--
-- opened_by_kind: 'contact' | 'agent' | 'ia' | 'system' (origem da abertura).
--   'contact' = mensagem recebida do cliente (grava name='Contato').
--   'agent'   = envio/acao de um atendente logado (name = nome do atendente).
--   'ia'      = resposta automatica da IA (name='IA').
-- opened_by_user_id: core users.id quando kind='agent' (nullable nos demais).
-- opened_by_name: snapshot p/ exibir sem join ('Contato'/'IA'/nome do atendente).
-- Registros antigos ficam com '' (a UI mostra '—'), sem backfill.
--
-- SQL portavel: o migrator valida o prefixo plugin_protocolos_ e ADD COLUMN roda em
-- SQLite e Postgres. Nao usar ponto-e-virgula nos comentarios (o splitter quebra por ele).

ALTER TABLE plugin_protocolos_protocolos ADD COLUMN opened_by_kind TEXT NOT NULL DEFAULT '';
ALTER TABLE plugin_protocolos_protocolos ADD COLUMN opened_by_user_id INTEGER;
ALTER TABLE plugin_protocolos_protocolos ADD COLUMN opened_by_name TEXT NOT NULL DEFAULT '';

ALTER TABLE plugin_protocolos_atendimentos ADD COLUMN opened_by_kind TEXT NOT NULL DEFAULT '';
ALTER TABLE plugin_protocolos_atendimentos ADD COLUMN opened_by_user_id INTEGER;
ALTER TABLE plugin_protocolos_atendimentos ADD COLUMN opened_by_name TEXT NOT NULL DEFAULT '';
