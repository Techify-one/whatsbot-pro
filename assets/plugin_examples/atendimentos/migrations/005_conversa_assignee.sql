-- Atendente que RESOLVEU a conversa-ciclo. Alem do assignee_name (snapshot do nome) que
-- ja existia, registra o id do usuario (capturado do current_user no /resolve) para o
-- historico mostrar o agente em todas as telas, igual ao atendimento.
-- SQL portavel: o migrator valida o prefixo plugin_atendimentos_ e ADD COLUMN roda em
-- SQLite e Postgres. Nao usar ponto-e-virgula nos comentarios (o splitter quebra por ele).

ALTER TABLE plugin_atendimentos_conversas ADD COLUMN assignee_user_id INTEGER;
