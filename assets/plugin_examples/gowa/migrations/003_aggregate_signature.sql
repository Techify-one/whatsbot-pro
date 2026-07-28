-- Alerta AGREGADO: uma unica mensagem no Telegram lista TODAS as caixas GOWA que
-- estao desconectadas (e com o alerta ligado). O estado da mensagem agregada mora
-- numa linha reservada com channel_id = '__all__' desta mesma tabela.
--
-- down_signature guarda a assinatura (lista ordenada dos channel_id que estavam
-- desconectados no ultimo envio). Comparando a assinatura atual com a salva, o loop
-- reenvia a mensagem quando o CONJUNTO de caixas caidas muda (uma caiu a mais ou
-- uma voltou), alem do reenvio periodico pelo intervalo configurado.
-- NOTA: nao use ponto-e-virgula nos comentarios (o splitter do migrator quebra ali).

ALTER TABLE plugin_gowa_disconnect_alerts ADD COLUMN IF NOT EXISTS down_signature TEXT;
