-- Guarda o ULTIMO numero conhecido do canal (o que estava conectado).
--
-- Enquanto o WhatsApp fica desconectado, o state.bot_phone em memoria pode ser
-- esvaziado (reconexoes/restart do GOWA), fazendo a mensagem de repeticao sair com
-- "Numero: --". Persistindo o ultimo numero aqui, o alerta sempre mostra o numero
-- que estava conectado, mesmo depois de varios ciclos de edicao.

ALTER TABLE plugin_gowa_disconnect_alerts ADD COLUMN IF NOT EXISTS last_phone TEXT;
