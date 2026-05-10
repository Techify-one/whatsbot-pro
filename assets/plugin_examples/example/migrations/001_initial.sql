CREATE TABLE IF NOT EXISTS plugin_example_pings (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT    NOT NULL,
    note  TEXT    NOT NULL DEFAULT '',
    ts    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS plugin_example_pings_phone_ts
    ON plugin_example_pings(phone, ts);
