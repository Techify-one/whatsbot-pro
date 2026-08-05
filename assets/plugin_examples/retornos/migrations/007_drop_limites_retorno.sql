-- Remove os limites POR RETORNO (espera, máximo de tentativas e prazo). Eram três números
-- pedidos no formulário de cada retorno para dizer o que as CONDIÇÕES já dizem melhor: quando
-- disparar é decisão da árvore de regras, avaliada no disparo com dados frescos. A espera fixa
-- ainda brigava com as condições (o retorno esperava X minutos e só então olhava a regra), e
-- tentativas/prazo eram infraestrutura do ciclo disfarçada de configuração de negócio.
-- O teto de segurança CONTINUA existindo, mas agora é GLOBAL (Settings do plugin
-- max_attempts_per_retorno e retorno_deadline_minutes), igual para todos os retornos, só para
-- que um agendamento cujas condições nunca batem não reavalie eternamente.
-- ATENÇÃO ao escrever comentários aqui: o migrator splita o arquivo por ponto-e-vírgula,
-- então NUNCA use esse caractere em comentário.

ALTER TABLE plugin_retornos_retornos DROP COLUMN IF EXISTS delay_min;
ALTER TABLE plugin_retornos_retornos DROP COLUMN IF EXISTS max_tentativas;
ALTER TABLE plugin_retornos_retornos DROP COLUMN IF EXISTS deadline_min;
