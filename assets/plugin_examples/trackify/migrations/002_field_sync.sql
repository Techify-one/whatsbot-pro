-- Sincronização de campos do contato: atributos personalizados do WhatsBot
-- <-> campos personalizados do contato no Trackify.
--
-- Mesmas convenções do 001, todas obrigatórias:
--   * prefixo plugin_trackify_ em TODA tabela e TODO índice
--   * timestamps em DOUBLE PRECISION (epoch)
--   * sem FK cross-table (contact_id é snapshot do core)
--   * nada de DO $$ ... $$
-- E NUNCA um ponto-e-vírgula dentro de comentário: o migrador divide o arquivo
-- por ponto-e-vírgula de forma ingênua, inclusive dentro de comentário, e o
-- pedaço solto vira "syntax error" na subida do plugin.

-- ── Mapeamento (configuração) ────────────────────────────────────────────
-- Uma linha por par de campos. Mora em TABELA, não nas settings declarativas,
-- por três motivos: o PUT de settings do core é destrutivo (Settings(**body) +
-- model_dump inteiro) e um array aqui seria revertido por qualquer save de
-- outra seção. Os contadores são incrementados por três tasks de fundo e um
-- read-modify-write de JSON perderia atualização. E só o UNIQUE de banco fecha
-- a corrida entre duas abas abertas na mesma tela.
CREATE TABLE IF NOT EXISTS plugin_trackify_field_map (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    -- attribute = atributo personalizado (JSON), column = coluna nativa (name)
    wb_scope            TEXT NOT NULL DEFAULT 'attribute',
    wb_key              TEXT NOT NULL,
    tk_slug             TEXT NOT NULL,
    -- Retrato do catálogo do CDP no instante do save. Deixa a tela avisar "o
    -- campo virou identificador" ou "foi desativado" sem consultar o Nexus a
    -- cada render, e deixa o codec conhecer o alvo com o Nexus fora do ar.
    tk_field_id         TEXT NOT NULL DEFAULT '',
    tk_field_type       TEXT NOT NULL DEFAULT '',
    tk_regex            TEXT NOT NULL DEFAULT '',
    tk_is_identifier    INTEGER NOT NULL DEFAULT 0,
    -- to_trackify | to_whatsbot | both
    direction           TEXT NOT NULL DEFAULT 'to_trackify',
    -- whatsbot_wins | trackify_wins | hold
    conflict_policy     TEXT NOT NULL DEFAULT 'whatsbot_wins',
    -- O operador confirmou por escrito que quer gravar num identificador.
    ack_identifier      INTEGER NOT NULL DEFAULT 0,
    enabled             INTEGER NOT NULL DEFAULT 1,
    -- Salvo sem conseguir conferir o slug (CDP fora do ar). Recusar o save
    -- durante uma queda trancaria o operador para fora justo na hora de
    -- desligar um mapeamento ruim.
    unverified          INTEGER NOT NULL DEFAULT 0,
    position            INTEGER NOT NULL DEFAULT 0,
    -- Contadores. Escritos SEMPRE por UPDATE incremental, nunca por reescrita
    -- da linha inteira, para o save da tela e os workers não se sobreporem.
    pushed_ok           INTEGER NOT NULL DEFAULT 0,
    pushed_err          INTEGER NOT NULL DEFAULT 0,
    pulled_ok           INTEGER NOT NULL DEFAULT 0,
    pulled_rejected     INTEGER NOT NULL DEFAULT 0,
    conflicts           INTEGER NOT NULL DEFAULT 0,
    last_push_at        DOUBLE PRECISION,
    last_pull_at        DOUBLE PRECISION,
    last_error          TEXT NOT NULL DEFAULT '',
    created_at          DOUBLE PRECISION NOT NULL,
    updated_at          DOUBLE PRECISION NOT NULL
);

-- 1:1 nos DOIS lados, de propósito. Um leque (uma chave do WhatsBot para dois
-- campos do CDP) deixaria a volta ambígua: qual dos dois manda ao escrever de
-- volta? A regra vira invariante de BANCO e não só validação de tela.
CREATE UNIQUE INDEX IF NOT EXISTS plugin_trackify_field_map_wb
    ON plugin_trackify_field_map (wb_scope, wb_key);
CREATE UNIQUE INDEX IF NOT EXISTS plugin_trackify_field_map_tk
    ON plugin_trackify_field_map (tk_slug);

-- ── Estado por (contato, mapeamento) ─────────────────────────────────────
-- HASH, nunca valor: esta tabela acompanha e-mail e CPF de toda a base, e uma
-- terceira cópia em claro do dado pessoal seria criada só para o motor
-- comparar igualdade.
-- Convenção: '' = observado e vazio (fato conhecido), NULL = nunca observado
-- (fato desconhecido). Só essa diferença separa "foi apagado lá" de "nunca
-- sincronizamos" -- e é ela que impede a primeira execução de sair apagando
-- campo no CRM do cliente.
CREATE TABLE IF NOT EXISTS plugin_trackify_field_state (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    map_id              INTEGER NOT NULL,
    contact_id          INTEGER NOT NULL,
    trackify_contact_id TEXT NOT NULL DEFAULT '',
    wb_hash             TEXT,
    tk_hash             TEXT,
    -- Último valor do CDP recusado pela validação do WhatsBot. Sem isto a
    -- varredura redetecta a MESMA divergência para sempre.
    rejected_hash       TEXT,
    conflict            INTEGER NOT NULL DEFAULT 0,
    conflict_reason     TEXT NOT NULL DEFAULT '',
    wb_changed_at       DOUBLE PRECISION,
    tk_changed_at       DOUBLE PRECISION,
    last_push_at        DOUBLE PRECISION,
    last_pull_at        DOUBLE PRECISION,
    last_error          TEXT NOT NULL DEFAULT '',
    created_at          DOUBLE PRECISION NOT NULL,
    updated_at          DOUBLE PRECISION NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS plugin_trackify_field_state_key
    ON plugin_trackify_field_state (map_id, contact_id);
CREATE INDEX IF NOT EXISTS plugin_trackify_field_state_contact
    ON plugin_trackify_field_state (contact_id);
-- A lista "Conflitos" da tela.
CREATE INDEX IF NOT EXISTS plugin_trackify_field_state_conflict
    ON plugin_trackify_field_state (conflict, updated_at);

-- ── Fila de push (convergência, não fato) ────────────────────────────────
-- Diferente da fila do 001 de propósito. Lá cada linha é um FATO imutável com
-- external_id de dedup e ordem cronológica que importa. Aqui a linha guarda só
-- o CONTATO SUJO: o worker recalcula o estado desejado na hora de enviar. Com
-- isso cinco edições em dez segundos colapsam num PUT só, um payload velho é
-- impossível, e o evento, o botão "sincronizar agora" e a varredura viram
-- literalmente a mesma função.
CREATE TABLE IF NOT EXISTS plugin_trackify_field_outbox (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id          INTEGER NOT NULL,
    reason              TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL DEFAULT 'pending',
    attempts            INTEGER NOT NULL DEFAULT 0,
    next_attempt_at     DOUBLE PRECISION NOT NULL DEFAULT 0,
    last_error          TEXT NOT NULL DEFAULT '',
    last_http_status    INTEGER,
    locked_by           TEXT NOT NULL DEFAULT '',
    locked_at           DOUBLE PRECISION,
    enqueued_at         DOUBLE PRECISION NOT NULL,
    created_at          DOUBLE PRECISION NOT NULL,
    updated_at          DOUBLE PRECISION NOT NULL
);

-- Índice único PARCIAL: no máximo UMA linha pendente por contato. É o que faz
-- a coalescência. Parcial (e não total) porque uma linha já em 'sending' não
-- pode impedir o enfileiramento da rodada seguinte.
CREATE UNIQUE INDEX IF NOT EXISTS plugin_trackify_field_outbox_pending
    ON plugin_trackify_field_outbox (contact_id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS plugin_trackify_field_outbox_due
    ON plugin_trackify_field_outbox (status, next_attempt_at);

-- ── Cursores (poller e varredura) ────────────────────────────────────────
-- O poller lê o changelog do CDP por cursor keyset (created_at, id). O id é um
-- uuid: a ordem dele é arbitrária, mas é TOTAL e ESTÁVEL, que é tudo o que um
-- desempate de keyset precisa.
CREATE TABLE IF NOT EXISTS plugin_trackify_sync_cursor (
    name        TEXT PRIMARY KEY,
    cursor_ts   DOUBLE PRECISION NOT NULL DEFAULT 0,
    cursor_id   TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',
    updated_at  DOUBLE PRECISION NOT NULL
);

-- ── Busca reversa de identidade ──────────────────────────────────────────
-- O poller vai de trackify_contact_id para telefone a cada ciclo, e a tabela
-- do 001 só tem PK por telefone.
CREATE INDEX IF NOT EXISTS plugin_trackify_identity_tk
    ON plugin_trackify_identity (trackify_contact_id);
