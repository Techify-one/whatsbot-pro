-- Estado dos alertas da conta Meta enviados ao Telegram (plano 84).
--
-- Uma linha por ALERTA (chave = "<channel_id>|<identidade do alerta>", ex.:
-- "wac_1|template:promo_julho (pt_BR)" ou "wac_1|failure:131049"). Guarda tres
-- coisas que o motor precisa lembrar entre eventos e entre restarts.
--
-- last_value e a ASSINATURA do aviso (o valor em si: "PAUSED", "GREEN->RED",
-- o codigo do erro). E ela que decide se a ocorrencia e nova ou repetida, o que
-- neutraliza a reentrega de rotina da Meta sem precisar de dedupe no core e
-- preserva o vaivem legitimo (GREEN para YELLOW e de volta para GREEN).
--
-- last_alert_ts + occurrences implementam a agregacao: repeticao identica dentro
-- da janela configurada apenas incrementa o contador e EDITA a mensagem ja
-- enviada (telegram_message_id), em vez de mandar outra. Sem isso, as 15 falhas
-- do mesmo codigo medidas em 2h47 na producao virariam 15 mensagens no grupo.
--
-- telegram_chat_id acompanha o message_id porque o destino pode mudar (grupo
-- promovido a supergrupo, ou troca manual do chat na tela): editar a mensagem
-- antiga exige o chat em que ela foi postada.
-- NOTA: nao use ponto-e-virgula nos comentarios, pois o splitter do migrator
-- quebra os statements nesse caractere.

CREATE TABLE IF NOT EXISTS plugin_whatsapp_cloud_alert_state (
    alert_key           TEXT PRIMARY KEY,
    last_value          TEXT,
    last_alert_ts       DOUBLE PRECISION,
    occurrences         INTEGER NOT NULL DEFAULT 0,
    telegram_chat_id    TEXT,
    telegram_message_id BIGINT
);
