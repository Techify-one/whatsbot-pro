-- Espelho do atendimento para o Trackify (CDP): fila de saída + cache de identidade.
--
-- Convenções do repo, todas obrigatórias:
--   * prefixo plugin_trackify_ em toda tabela e índice
--   * timestamps em DOUBLE PRECISION (epoch, como o resto do WhatsBot)
--   * sem FK cross-table (contact_id é snapshot do core)
--   * nada de DO $$ ... $$
-- E NUNCA use ponto-e-vírgula dentro de comentário: o migrador divide o arquivo
-- por ponto-e-vírgula de forma ingênua, inclusive dentro de comentário, e o
-- pedaço solto vira "syntax error" na subida do plugin.

-- ── Fila de saída ────────────────────────────────────────────────────────
-- O handler de evento NUNCA posta: o barramento é fire-and-forget e engole
-- exceção, então um erro de rede viraria perda silenciosa de dado no CDP. O
-- handler só INSERE aqui. Quem entrega é a task supervisionada.
CREATE TABLE IF NOT EXISTS plugin_trackify_outbox (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Chave de dedup do Trackify, determinística do FATO (nunca uuid4 nem hora
    -- de envio): um retry que caiu depois do POST e antes da escrita local
    -- reenvia o MESMO id e o CDP faz short-circuit em vez de duplicar.
    external_id         TEXT NOT NULL,
    kind                TEXT NOT NULL,
    contact_id          INTEGER,
    phone               TEXT NOT NULL DEFAULT '',
    payload             TEXT NOT NULL DEFAULT '{}',
    -- Quando o fato ACONTECEU, não quando foi enviado: um backlog de 6 horas
    -- tem que aterrissar na timeline do CDP na ordem cronológica certa.
    occurred_at         DOUBLE PRECISION NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    attempts            INTEGER NOT NULL DEFAULT 0,
    next_attempt_at     DOUBLE PRECISION NOT NULL DEFAULT 0,
    last_error          TEXT NOT NULL DEFAULT '',
    last_http_status    INTEGER,
    locked_by           TEXT NOT NULL DEFAULT '',
    locked_at           DOUBLE PRECISION,
    trackify_contact_id TEXT NOT NULL DEFAULT '',
    trackify_event_id   TEXT NOT NULL DEFAULT '',
    created_at          DOUBLE PRECISION NOT NULL,
    updated_at          DOUBLE PRECISION NOT NULL
);

-- Idempotência LOCAL (a do Trackify é a segunda linha de defesa): o mesmo fato
-- enfileirado duas vezes — re-entrega do bus, replay, emit duplo — colapsa numa
-- linha só em vez de virar dois eventos no CDP.
CREATE UNIQUE INDEX IF NOT EXISTS plugin_trackify_outbox_external
    ON plugin_trackify_outbox (external_id);

-- A consulta do worker (claim).
CREATE INDEX IF NOT EXISTS plugin_trackify_outbox_due
    ON plugin_trackify_outbox (status, next_attempt_at);

-- Tela de diagnóstico + detector de buraco.
CREATE INDEX IF NOT EXISTS plugin_trackify_outbox_contact
    ON plugin_trackify_outbox (contact_id);

-- ── Cache de identidade ──────────────────────────────────────────────────
-- Guarda o valor EXATO que o Trackify tem para este telefone. É o que impede o
-- fork de contato: reenviamos a grafia que o CDP já conhece em vez do JID cru.
-- TTL curto de propósito — um merge no Trackify DELETA a grafia do perdedor, e
-- o cache envelheceria sem nenhum aviso.
CREATE TABLE IF NOT EXISTS plugin_trackify_identity (
    phone               TEXT PRIMARY KEY,
    trackify_contact_id TEXT NOT NULL DEFAULT '',
    matched_slug        TEXT NOT NULL DEFAULT '',
    exact_value         TEXT NOT NULL DEFAULT '',
    resolved_at         DOUBLE PRECISION NOT NULL
);
