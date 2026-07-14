-- Per-visitor session for the website widget (plano 46 sub-plano 05).
-- session_token is the opaque per-visitor id minted at GET config and stored in
-- the widget cookie/localStorage. It doubles as the conversation chat_id (the
-- analogue of "phone" for this provider), so the whole inbound funnel keys the
-- contact by (website_channel_id, session_token) with no core change.
-- identifier is filled only after a verified HMAC setUser (cross-device history).
-- All prefixed plugin_website_ as the migrator requires.
CREATE TABLE IF NOT EXISTS plugin_website_sessions (
    session_token   TEXT PRIMARY KEY,
    channel_id      TEXT NOT NULL,
    chat_id         TEXT NOT NULL,
    identifier      TEXT,
    hmac_verified   INTEGER NOT NULL DEFAULT 0,
    ip              TEXT,
    created_at      DOUBLE PRECISION NOT NULL DEFAULT 0,
    last_seen       DOUBLE PRECISION NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS plugin_website_sessions_channel_idx
    ON plugin_website_sessions (channel_id);
