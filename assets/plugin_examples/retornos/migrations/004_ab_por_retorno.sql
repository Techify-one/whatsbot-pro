-- O teste A/B passa a ser do RETORNO, não de cada mensagem. Antes, cada mensagem tinha o
-- flag `testando` e só as marcadas entravam no rodízio. Agora o retorno inteiro tem
-- `ab_ativo`: ligado, TODAS as mensagens do retorno viram o pool (uma por disparo,
-- alternando pelo cursor `proxima_mensagem_index`) - desligado, todas saem na ordem.
-- Backfill: retorno que tinha ao menos uma mensagem em teste nasce com o A/B ligado, então
-- nenhuma configuração existente perde o rodízio. ATENÇÃO ao escrever comentários aqui: o
-- migrator splita o arquivo por ponto-e-vírgula, então NUNCA use esse caractere em comentário.

ALTER TABLE plugin_retornos_retornos
    ADD COLUMN IF NOT EXISTS ab_ativo INTEGER NOT NULL DEFAULT 0;

UPDATE plugin_retornos_retornos SET ab_ativo = 1
 WHERE id IN (SELECT retorno_id FROM plugin_retornos_mensagens WHERE testando = 1);

ALTER TABLE plugin_retornos_mensagens DROP COLUMN IF EXISTS testando;
