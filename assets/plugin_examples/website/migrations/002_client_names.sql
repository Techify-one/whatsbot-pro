-- Sequential "Cliente N" naming for website visitors (per channel).
--
-- Contacts born from the widget used to show the raw session token (wsess_…) as
-- their name. Instead we label each visitor "{nome do canal} - Cliente {N}", with
-- N a per-channel counter that starts at 1 and grows as new visitors message.
--
-- client_seq is the stable per-channel visitor number, assigned to a session on
-- its FIRST inbound message (so the numbering follows real conversations, not
-- widget loads that never send anything). Persisting it on the session keeps the
-- contact's name stable across its later messages.
ALTER TABLE plugin_website_sessions
    ADD COLUMN IF NOT EXISTS client_seq INTEGER;

-- Race-safe per-channel monotonic counter (bumped atomically via
-- INSERT ... ON CONFLICT DO UPDATE RETURNING). next_seq is the last number handed
-- out for the channel; the first visitor gets 1.
CREATE TABLE IF NOT EXISTS plugin_website_counters (
    channel_id  TEXT PRIMARY KEY,
    next_seq    INTEGER NOT NULL DEFAULT 0
);
