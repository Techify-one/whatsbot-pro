-- Plano 51 (02 F5) - chat agentico: conversas, mensagens e aprovacoes.
-- Molde do schema do ai-server do nexus (RelatoriosAi*), prefixado.
-- conversations.id e approvals.id sao TEXT (uuid gerado em Python / approval_id
-- do executor). messages e append-only (so user/assistant persistidos pelo
-- executor, tool-calls vao por streaming).
-- Atencao: comentarios sem ponto-e-virgula (o migrator splita por ele).

CREATE TABLE IF NOT EXISTS plugin_melhorias_ai_conversations (
    id TEXT PRIMARY KEY,
    suggestion_id INTEGER NOT NULL,
    user_id INTEGER,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    model TEXT NOT NULL DEFAULT '',
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    completed_at DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS plugin_melhorias_aiconv_sug_idx
    ON plugin_melhorias_ai_conversations (suggestion_id);

CREATE INDEX IF NOT EXISTS plugin_melhorias_aiconv_status_idx
    ON plugin_melhorias_ai_conversations (status);

CREATE TABLE IF NOT EXISTS plugin_melhorias_ai_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    tool_name TEXT,
    tool_input TEXT,
    tool_result TEXT,
    token_usage TEXT,
    created_at DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS plugin_melhorias_aimsgs_idx
    ON plugin_melhorias_ai_messages (conversation_id, id);

CREATE TABLE IF NOT EXISTS plugin_melhorias_ai_approvals (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    tool_input TEXT,
    summary TEXT,
    approved INTEGER,
    reason TEXT,
    decided_by INTEGER,
    decided_at DOUBLE PRECISION,
    created_at DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS plugin_melhorias_aiappr_idx
    ON plugin_melhorias_ai_approvals (conversation_id);
