-- Estado dos alertas de desconexao do canal GOWA (uma linha por canal).
--
-- O loop de alerta (alerts.py) usa esta tabela para lembrar tres coisas. Primeiro,
-- se o numero ja esteve conectado alguma vez (ever_connected), o que evita alarme
-- falso num numero nunca pareado ou no warm-up do boot. Segundo, desde quando caiu
-- (disconnected_since) e quando foi o ultimo aviso (last_alert_ts), para respeitar
-- a cadencia de 15 min. Terceiro, o message_id do Telegram, para EDITAR a mesma
-- mensagem a cada ciclo em vez de mandar uma nova ("substitui a notificacao").
-- Timestamps sao epoch float, sempre escritos pelo Python (nunca pelo SQL).
-- NOTA: nao use ponto-e-virgula nos comentarios, pois o splitter do migrator
-- quebra os statements nesse caractere.

CREATE TABLE IF NOT EXISTS plugin_gowa_disconnect_alerts (
    channel_id          TEXT PRIMARY KEY,
    ever_connected      BOOLEAN NOT NULL DEFAULT FALSE,
    disconnected_since  DOUBLE PRECISION,
    last_alert_ts       DOUBLE PRECISION,
    telegram_chat_id    TEXT,
    telegram_message_id BIGINT
);
