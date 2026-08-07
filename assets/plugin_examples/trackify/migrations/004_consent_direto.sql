-- Descadastro por botão passa a ser um CÓDIGO único, gravado direto.
--
-- O que morre aqui, e por quê: as três tabelas de apoio existiam para o
-- operador ADIVINHAR qual string a Meta devolveria no clique (o template era
-- criado sem payload explícito, e a Meta ecoava o texto do botão). Agora o
-- módulo Campanhas manda um código combinado no payload, então:
--
--   * _button  -- a tabela de regras vira uma setting escalar
--     (consent_optout_payload)
--   * _channel -- a allow-list de canais some: o gate duro de provider
--     (whatsapp_cloud) já era quem decidia de verdade, e um contato que pede
--     para sair num número pediu para sair
--   * _seen    -- "botões vistos recentemente" era a ferramenta de adivinhação
--   * _outbox  -- a fila de gravação sai por decisão de produto (grava direto,
--     ver a docstring de consent.py, que registra o preço disso)
--
-- plugin_trackify_consent_state FICA: é a evidência do pedido (quem, quando,
-- qual payload, o que foi gravado) e agora também o único lugar onde uma falha
-- de entrega aparece.
--
-- Mesmas convenções do 001/002/003, todas obrigatórias:
--   * prefixo plugin_trackify_ em TODA tabela e TODO índice
--   * timestamps em DOUBLE PRECISION (epoch)
--   * nada de DO $$ ... $$
-- E NUNCA um ponto-e-vírgula dentro de comentário: o migrador divide o arquivo
-- por ponto-e-vírgula de forma ingênua, inclusive dentro de comentário, e o
-- pedaço solto vira "syntax error" na subida do plugin.

DROP TABLE IF EXISTS plugin_trackify_consent_button;

DROP TABLE IF EXISTS plugin_trackify_consent_channel;

DROP TABLE IF EXISTS plugin_trackify_consent_seen;

DROP TABLE IF EXISTS plugin_trackify_consent_outbox;

-- Quando foi a última ida ao Trackify por este contato. Separado de written_at
-- de propósito: written_at só existe quando DEU CERTO, e sem este carimbo uma
-- linha com last_error preenchido não diz se a falha foi agora ou no mês
-- passado.
ALTER TABLE plugin_trackify_consent_state
    ADD COLUMN IF NOT EXISTS last_attempt_at DOUBLE PRECISION;

-- button_id apontava para a regra que casou. Sem tabela de regras, a coluna
-- perde o sentido -- e deixá-la NOT NULL-less com lixo antigo faria uma leitura
-- futura acreditar num id que não existe mais.
ALTER TABLE plugin_trackify_consent_state
    DROP COLUMN IF EXISTS button_id;
