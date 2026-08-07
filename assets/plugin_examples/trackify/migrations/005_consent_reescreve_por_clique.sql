-- Dedupe da gravação passa a ser por CLIQUE, não por valor.
--
-- A guarda antiga pulava o HTTP sempre que o valor gravado batia com o
-- desejado. Isso tratava o registro local como prova do estado remoto: apagado
-- o campo no CDP (correção manual, merge de contatos, outro sistema), nenhum
-- clique novo era gravado de novo -- em silêncio, e para sempre.
--
-- Guardando qual clique produziu a gravação, a reentrega do MESMO clique
-- continua sendo no-op (é ela que protege o orçamento de 30/min) e um clique
-- NOVO sempre reescreve.
ALTER TABLE plugin_trackify_consent_state
  ADD COLUMN IF NOT EXISTS written_msg_id TEXT;

-- Linhas anteriores a esta migração não sabem de qual clique vieram. Deixá-las
-- com o msg_id atual as faria parecer já entregues e o primeiro clique depois
-- do upgrade seria engolido -- NULL faz a guarda liberar a escrita, que é o
-- lado seguro do erro.
