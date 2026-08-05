-- Remove o expediente da configuração. Era um ATALHO que dizia, num segundo lugar, o que as
-- condições de cada retorno já dizem melhor (hora/dia da semana são campos do catálogo de
-- regras, avaliados no disparo com dados frescos e com o mesmo fuso fixo da configuração).
-- Dois lugares para a mesma decisão só produzem conflito silencioso: a configuração parecia
-- ativa, as condições passavam e nada disparava porque o expediente estava fechado.
-- O fuso (tz_offset_hours) CONTINUA - ele é de quem avalia as regras de hora/data.
-- ATENÇÃO ao escrever comentários aqui: o migrator splita o arquivo por ponto-e-vírgula,
-- então NUNCA use esse caractere em comentário.

ALTER TABLE plugin_retornos_configuracoes DROP COLUMN IF EXISTS business_enabled;
ALTER TABLE plugin_retornos_configuracoes DROP COLUMN IF EXISTS business_start;
ALTER TABLE plugin_retornos_configuracoes DROP COLUMN IF EXISTS business_end;
ALTER TABLE plugin_retornos_configuracoes DROP COLUMN IF EXISTS day_mon;
ALTER TABLE plugin_retornos_configuracoes DROP COLUMN IF EXISTS day_tue;
ALTER TABLE plugin_retornos_configuracoes DROP COLUMN IF EXISTS day_wed;
ALTER TABLE plugin_retornos_configuracoes DROP COLUMN IF EXISTS day_thu;
ALTER TABLE plugin_retornos_configuracoes DROP COLUMN IF EXISTS day_fri;
ALTER TABLE plugin_retornos_configuracoes DROP COLUMN IF EXISTS day_sat;
ALTER TABLE plugin_retornos_configuracoes DROP COLUMN IF EXISTS day_sun;
