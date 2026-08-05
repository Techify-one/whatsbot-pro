-- Renomeia o vocabulário do plugin: RÉGUA passa a se chamar CONFIGURAÇÃO e PASSO passa a
-- se chamar RETORNO (tabelas, colunas e índices). Nenhum dado é perdido: são apenas
-- RENAMEs, então réguas/passos já criados viram configurações/retornos com o mesmo id.
-- As migrations 001/002 continuam criando os nomes antigos (histórico imutável) e esta
-- migration os renomeia logo em seguida — instalação nova e instalação antiga chegam ao
-- mesmo esquema. ATENÇÃO ao escrever comentários aqui: o migrator splita o arquivo por
-- ponto-e-vírgula, então NUNCA use esse caractere em comentário.

ALTER TABLE IF EXISTS plugin_retornos_reguas RENAME TO plugin_retornos_configuracoes;
ALTER TABLE IF EXISTS plugin_retornos_passos RENAME TO plugin_retornos_retornos;

ALTER TABLE plugin_retornos_retornos RENAME COLUMN regua_id TO configuracao_id;
ALTER TABLE plugin_retornos_mensagens RENAME COLUMN passo_id TO retorno_id;

ALTER TABLE plugin_retornos_controle RENAME COLUMN regua_id TO configuracao_id;
ALTER TABLE plugin_retornos_controle RENAME COLUMN passo_atual_id TO retorno_atual_id;
ALTER TABLE plugin_retornos_controle RENAME COLUMN passo_started_at TO retorno_started_at;
ALTER TABLE plugin_retornos_controle RENAME COLUMN tentativas_passo TO tentativas_retorno;

ALTER TABLE plugin_retornos_log RENAME COLUMN regua_id TO configuracao_id;
ALTER TABLE plugin_retornos_log RENAME COLUMN passo_id TO retorno_id;

ALTER INDEX IF EXISTS plugin_retornos_reguas_ordem
    RENAME TO plugin_retornos_configuracoes_ordem;
ALTER INDEX IF EXISTS plugin_retornos_passos_regua
    RENAME TO plugin_retornos_retornos_configuracao;
ALTER INDEX IF EXISTS plugin_retornos_mensagens_passo
    RENAME TO plugin_retornos_mensagens_retorno;
