-- Um estado por (contato, AÇÃO), não mais um por contato.
--
-- O módulo Campanhas ficou agnóstico: cada botão de resposta rápida leva um
-- código livre, e é o plugin que diz o que cada código significa. Hoje só existe
-- a ação 'optout', mas a chave passa a suportar mais de uma ação por contato
-- desde já -- repivotar a chave primária depois, com linhas em produção, seria a
-- mesma cirurgia com risco maior.
--
-- Mesmas convenções do 001/002/003/004/005, todas obrigatórias:
--   * prefixo plugin_trackify_ em TODA tabela e TODO índice
--   * timestamps em DOUBLE PRECISION (epoch)
--   * sem FK cross-table (contact_id é snapshot do core)
--   * nada de DO $$ ... $$
-- E NUNCA um ponto-e-vírgula dentro de comentário: o migrador divide o arquivo
-- por ponto-e-vírgula de forma ingênua, inclusive dentro de comentário, e o
-- pedaço solto vira "syntax error" na subida do plugin.

-- O DEFAULT faz o backfill sem perda: toda linha existente É de descadastro,
-- porque 'optout' foi o único valor que `desired` alguma vez recebeu.
ALTER TABLE plugin_trackify_consent_state
  ADD COLUMN IF NOT EXISTS action TEXT NOT NULL DEFAULT 'optout';

-- E o DEFAULT sai logo em seguida: mantê-lo faria um INSERT futuro que
-- esquecesse a coluna virar descadastro em silêncio -- o pior erro possível
-- nesta tabela, porque ele para de mandar campanha para alguém que não pediu.
ALTER TABLE plugin_trackify_consent_state
  ALTER COLUMN action DROP DEFAULT;

-- `desired` era o significado do clique. O significado AGORA é a ação, e duas
-- colunas dizendo a mesma coisa divergem -- foi exatamente o caso do button_id
-- que a 004 limpou. O índice sai antes por explicitude (o DROP COLUMN o levaria
-- junto de qualquer forma).
DROP INDEX IF EXISTS plugin_trackify_consent_state_desired;

ALTER TABLE plugin_trackify_consent_state
  DROP COLUMN IF EXISTS desired;

-- `written_value` FICA, com o sentido alargado: o valor gravado quando a ação
-- escreve um campo só, e o JSON {slug: valor} quando escreve mais de um. Uma
-- coluna written_values JSONB é a saída limpa QUANDO existir uma ação
-- multi-campo de verdade -- hoje seria especulação.

-- Nome default que o Postgres deu à PK criada na 003 (contact_id PRIMARY KEY).
ALTER TABLE plugin_trackify_consent_state
  DROP CONSTRAINT IF EXISTS plugin_trackify_consent_state_pkey;

ALTER TABLE plugin_trackify_consent_state
  ADD CONSTRAINT plugin_trackify_consent_state_pkey PRIMARY KEY (contact_id, action);

-- Alvo da lista "descadastros que não chegaram ao Trackify": erro presente e
-- nada gravado ainda. Parcial porque a esmagadora maioria das linhas foi
-- entregue e nunca é lida por essa consulta.
CREATE INDEX IF NOT EXISTS plugin_trackify_consent_state_pendente
  ON plugin_trackify_consent_state (updated_at)
  WHERE written_at IS NULL;
