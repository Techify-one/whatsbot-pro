-- Remove o gate de entrada da régua. Quem filtra quem recebe o follow-up são as condições
-- de cada PASSO (avaliadas na hora do disparo, com dados frescos) — o gate era um segundo
-- lugar para dizer a mesma coisa e a UI dele saiu. Entrada passa a ser: a primeira régua
-- ativa (por posicao) que aceite a conversa assume. ATENÇÃO ao escrever comentários aqui:
-- o migrator splita o arquivo por ponto-e-vírgula, então NUNCA use esse caractere.
ALTER TABLE plugin_retornos_reguas DROP COLUMN IF EXISTS filtros_entrada;
