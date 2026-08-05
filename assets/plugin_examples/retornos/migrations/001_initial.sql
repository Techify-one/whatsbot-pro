-- Plugin `retornos` (plano 76) — 5 tabelas, todas com o prefixo plugin_retornos_ obrigatório.
-- Convenções do repo: epoch em DOUBLE PRECISION, árvores de regras como TEXT (JSON serializado
-- pelo repo.py — sem cast de dialeto), sem FK cross-table para o core (contact_id/conversation_id
-- são referências soltas + snapshots denormalizados). ATENÇÃO ao escrever comentários aqui: o
-- migrator splita o arquivo por ponto-e-vírgula, então NUNCA use esse caractere em comentário.

-- Régua = uma sequência de passos + o gate de entrada (filtros_entrada) que decide se uma
-- conversa entra nela. A ordem de avaliação entre réguas é `posicao` (menor primeiro).
CREATE TABLE IF NOT EXISTS plugin_retornos_reguas (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    nome                   TEXT    NOT NULL DEFAULT '',
    descricao              TEXT    NOT NULL DEFAULT '',
    posicao                INTEGER NOT NULL DEFAULT 0,
    ativo                  INTEGER NOT NULL DEFAULT 0,
    filtros_entrada        TEXT    NOT NULL DEFAULT '{"regras":[]}',
    on_reply               TEXT    NOT NULL DEFAULT 'reset',      -- reset|cancel (D8)
    cancel_on_resolve      INTEGER NOT NULL DEFAULT 1,
    cancel_on_assign_human INTEGER NOT NULL DEFAULT 1,
    cancel_on_ai_off       INTEGER NOT NULL DEFAULT 0,
    apply_to_groups        INTEGER NOT NULL DEFAULT 0,
    tz_offset_hours        DOUBLE PRECISION NOT NULL DEFAULT -3,  -- offset FIXO (D9)
    business_enabled       INTEGER NOT NULL DEFAULT 0,
    business_start         TEXT    NOT NULL DEFAULT '08:00',
    business_end           TEXT    NOT NULL DEFAULT '18:00',
    day_mon                INTEGER NOT NULL DEFAULT 1,
    day_tue                INTEGER NOT NULL DEFAULT 1,
    day_wed                INTEGER NOT NULL DEFAULT 1,
    day_thu                INTEGER NOT NULL DEFAULT 1,
    day_fri                INTEGER NOT NULL DEFAULT 1,
    day_sat                INTEGER NOT NULL DEFAULT 0,
    day_sun                INTEGER NOT NULL DEFAULT 0,
    created_at             DOUBLE PRECISION NOT NULL DEFAULT 0,
    updated_at             DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS plugin_retornos_reguas_ordem
    ON plugin_retornos_reguas(ativo, posicao);

-- Passo da sequência. `filtros` é a árvore recursiva avaliada NA HORA do disparo.
-- `delay_min` é o atraso mínimo (minutos) depois que o passo passa a ser o atual — o
-- agendamento final é max(delay, próximo instante temporal das regras).
CREATE TABLE IF NOT EXISTS plugin_retornos_passos (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    regua_id               INTEGER NOT NULL,
    ordem                  INTEGER NOT NULL DEFAULT 0,
    nome                   TEXT    NOT NULL DEFAULT '',
    filtros                TEXT    NOT NULL DEFAULT '{"regras":[]}',
    delay_min              INTEGER NOT NULL DEFAULT 0,
    proxima_mensagem_index INTEGER NOT NULL DEFAULT 0,            -- cursor do A/B
    max_tentativas         INTEGER NOT NULL DEFAULT 60,           -- D5
    deadline_min           INTEGER NOT NULL DEFAULT 4320,         -- D5 (3 dias)
    created_at             DOUBLE PRECISION NOT NULL DEFAULT 0,
    updated_at             DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS plugin_retornos_passos_regua
    ON plugin_retornos_passos(regua_id, ordem);

-- Mensagem de um passo. `testando=1` marca a mensagem como parte do pool de A/B
-- (uma por disparo, por rotação). `testando=0` sai sempre, na ordem.
CREATE TABLE IF NOT EXISTS plugin_retornos_mensagens (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    passo_id   INTEGER NOT NULL,
    ordem      INTEGER NOT NULL DEFAULT 0,
    tipo       TEXT    NOT NULL DEFAULT 'private_note',
    content    TEXT    NOT NULL DEFAULT '',
    media_path TEXT    NOT NULL DEFAULT '',
    media_url  TEXT    NOT NULL DEFAULT '',
    file_name  TEXT    NOT NULL DEFAULT '',
    testando   INTEGER NOT NULL DEFAULT 0,
    created_at DOUBLE PRECISION NOT NULL DEFAULT 0,
    updated_at DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS plugin_retornos_mensagens_passo
    ON plugin_retornos_mensagens(passo_id, ordem);

-- Controle 1:1 por conversa (uma régua por conversa — P3).
CREATE TABLE IF NOT EXISTS plugin_retornos_controle (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id   INTEGER NOT NULL,
    regua_id          INTEGER NOT NULL,
    contact_id        INTEGER,
    phone             TEXT    NOT NULL DEFAULT '',
    contact_name      TEXT    NOT NULL DEFAULT '',
    channel_id        TEXT    NOT NULL DEFAULT 'default',
    inbox_id          INTEGER,
    passo_atual_id    INTEGER,
    passo_started_at  DOUBLE PRECISION,
    disparos_enviados INTEGER NOT NULL DEFAULT 0,
    tentativas_passo  INTEGER NOT NULL DEFAULT 0,
    last_client_ts    DOUBLE PRECISION,
    next_at           DOUBLE PRECISION,
    processing        INTEGER NOT NULL DEFAULT 0,
    processing_since  DOUBLE PRECISION,
    status            TEXT    NOT NULL DEFAULT 'active',   -- active|completed|cancelled|expired
    last_error        TEXT,
    created_at        DOUBLE PRECISION NOT NULL DEFAULT 0,
    updated_at        DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS plugin_retornos_controle_conv
    ON plugin_retornos_controle(conversation_id);
CREATE INDEX IF NOT EXISTS plugin_retornos_controle_due
    ON plugin_retornos_controle(status, next_at);
CREATE INDEX IF NOT EXISTS plugin_retornos_controle_lock
    ON plugin_retornos_controle(processing);

-- Observabilidade (o que o monitor mostra e o que substitui os webhooks do Nexus).
CREATE TABLE IF NOT EXISTS plugin_retornos_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    evento          TEXT    NOT NULL DEFAULT '',
    nivel           TEXT    NOT NULL DEFAULT 'info',
    regua_id        INTEGER,
    controle_id     INTEGER,
    conversation_id INTEGER,
    passo_id        INTEGER,
    data            TEXT    NOT NULL DEFAULT '{}',
    ts              DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS plugin_retornos_log_ts
    ON plugin_retornos_log(ts);
CREATE INDEX IF NOT EXISTS plugin_retornos_log_conv
    ON plugin_retornos_log(conversation_id, ts);
