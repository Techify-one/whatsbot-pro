-- Posse temporaria do atendente depois de resolver: "quem atendeu fica com a conversa
-- por N minutos, depois a IA reassume".
--
-- Uma linha por conversa com DEVOLUCAO PENDENTE. A varredura do lifecycle
-- (protocolos:ai_takeover_release) le as linhas vencidas e devolve a conversa a IA via
-- conversation_service.set_ai. Qualquer acao HUMANA dentro da janela (atendente responde,
-- reabre pelo painel, religa a IA na mao) APAGA a linha -- a devolucao deixa de acontecer
-- e o atendente fica com a conversa.
--
-- mode:
--   'owner' = a conversa foi fechada COM atendente e ele foi mantido (o core ja cala a IA
--             quando ha dono humano sem agente vinculado -- nao gravamos nada na conversa).
--   'muted' = a conversa foi fechada SEM atendente (a IA/automacao fechou). Nao ha dono a
--             segurar, entao a conversa recebe ai_active=0 durante a janela (a IA fica
--             calada e o selo mostra "IA OFF") e volta a 1 no vencimento.
-- owner_user_id: core users.id do dono no momento de armar (NULL no modo 'muted').
-- protocolo_id: protocolo dono da conversa quando conhecido (limpeza ao reabrir/religar).
-- reason: origem do armamento (auditoria) -- 'resolver' | 'finalizar'.
--
-- SQL portavel: o migrator valida o prefixo plugin_protocolos_ e traduz
-- INTEGER PRIMARY KEY -> chave primaria simples. Nao usar ponto-e-virgula nos comentarios
-- (o splitter quebra por ele).

CREATE TABLE IF NOT EXISTS plugin_protocolos_ai_holds (
    conversation_id INTEGER PRIMARY KEY,
    hold_until      DOUBLE PRECISION NOT NULL,
    mode            TEXT    NOT NULL DEFAULT 'owner',
    owner_user_id   INTEGER,
    protocolo_id    INTEGER,
    reason          TEXT    NOT NULL DEFAULT '',
    set_at          DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS plugin_protocolos_ai_holds_until
    ON plugin_protocolos_ai_holds(hold_until);

CREATE INDEX IF NOT EXISTS plugin_protocolos_ai_holds_proto
    ON plugin_protocolos_ai_holds(protocolo_id);
