-- Pausa entre as mensagens POR RETORNO. Antes, a pausa era só global (a setting
-- `delay_between_messages_seconds`, no modal Configurar do plugin) e valia igual para todos
-- os retornos de todas as configurações. Agora cada retorno pode ter a própria pausa, em
-- SEGUNDOS, aplicada entre as mensagens daquele retorno no momento do disparo.
-- A coluna é ANULÁVEL de propósito: NULL significa "herda a pausa global", que é o estado de
-- toda linha existente — nenhuma configuração muda de comportamento com esta migration.
-- ATENÇÃO ao escrever comentários aqui: o migrator splita o arquivo por ponto-e-vírgula,
-- então NUNCA use esse caractere em comentário.

ALTER TABLE plugin_retornos_retornos
    ADD COLUMN IF NOT EXISTS delay_mensagens_seg INTEGER;
