-- ============================================================================
-- 03-verificacao.sql  —  READ-ONLY. Prova que o merge fechou.
--
--   psql "$DSN" -f sql/03-verificacao.sql
--
-- Comparar a secao "totais" com o baseline anotado no 01-preflight.sql:
--   messages / atendimentos / contact_inboxes / protocolos / ciclos / avaliacoes
--   NAO PODEM TER MUDADO. O merge troca o dono das linhas, nunca as apaga.
--   Apenas "contacts" diminui — exatamente o numero de pares mesclados.
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN TRANSACTION READ ONLY;

-- ---------------------------------------------------------------------------
-- 1. Nao sobrou par (fora os reprovados na triagem)
-- ---------------------------------------------------------------------------
\echo '--- 1. pares remanescentes ---'
WITH base AS (
    SELECT id, phone, name, length(phone) AS len,
        CASE WHEN phone ~ '^55[0-9]{11}$' AND substr(phone,5,1)='9'
             THEN substr(phone,1,4)||substr(phone,6) ELSE phone END AS canon
    FROM contacts WHERE phone ~ '^55[0-9]{10,11}$'
),
dup AS (SELECT canon FROM base GROUP BY canon HAVING count(*)>1 AND count(DISTINCT len)>1)
SELECT b.canon, b.id, b.phone, b.name
FROM base b JOIN dup d ON d.canon = b.canon
ORDER BY b.canon, b.len;

-- ---------------------------------------------------------------------------
-- 2. Orfaos — as tabelas de plugin nao tem FK, entao esta e a unica rede.
--
--    ATENCAO: JA EXISTEM orfaos anteriores a este merge (medidos em 2026-08-04):
--       protocolos=36  ciclos=45  avaliacoes=15  agendamentos=3  melhorias=0
--    O criterio NAO e "zero": e "igual ao baseline medido no 01-preflight.sql".
--    Qualquer numero ACIMA do baseline foi causado pelo merge -> investigar.
-- ---------------------------------------------------------------------------
\echo '--- 2. orfaos por contact_id (comparar com o baseline do preflight) ---'
SELECT
    (SELECT count(*) FROM plugin_protocolos_protocolos p WHERE p.contact_id IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM contacts c WHERE c.id = p.contact_id))     AS protocolos,
    (SELECT count(*) FROM plugin_protocolos_atendimentos p WHERE p.contact_id IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM contacts c WHERE c.id = p.contact_id))     AS ciclos,
    (SELECT count(*) FROM plugin_protocolos_avaliacoes p WHERE p.contact_id IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM contacts c WHERE c.id = p.contact_id))     AS avaliacoes,
    (SELECT count(*) FROM plugin_agendamento_retorno_items p WHERE p.contact_id IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM contacts c WHERE c.id = p.contact_id))     AS agendamentos,
    (SELECT count(*) FROM plugin_melhorias_suggestions p WHERE p.contact_id IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM contacts c WHERE c.id = p.contact_id))     AS melhorias;

-- ---------------------------------------------------------------------------
-- 3. Telefone denormalizado sem contato correspondente
--    Baseline 2026-08-04: protocolos_phone=1  avaliacoes_phone=0  agendamentos_phone=0
-- ---------------------------------------------------------------------------
\echo '--- 3. telefone denormalizado orfao (comparar com o baseline) ---'
SELECT
    (SELECT count(*) FROM plugin_protocolos_protocolos p WHERE p.contact_phone IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM contacts c WHERE c.phone = p.contact_phone)) AS protocolos_phone,
    (SELECT count(*) FROM plugin_protocolos_avaliacoes p WHERE p.contact_phone IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM contacts c WHERE c.phone = p.contact_phone)) AS avaliacoes_phone,
    (SELECT count(*) FROM plugin_agendamento_retorno_items p WHERE p.phone IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM contacts c WHERE c.phone = p.phone))         AS agendamentos_phone;

-- ---------------------------------------------------------------------------
-- 4. Totais — comparar com o baseline do preflight
-- ---------------------------------------------------------------------------
\echo '--- 4. totais (so contacts pode ter diminuido) ---'
SELECT
    (SELECT count(*) FROM messages)                        AS total_mensagens,
    (SELECT count(*) FROM atendimentos)                    AS total_conversas,
    (SELECT count(*) FROM contact_inboxes)                 AS total_contact_inboxes,
    (SELECT count(*) FROM plugin_protocolos_protocolos)    AS total_protocolos,
    (SELECT count(*) FROM plugin_protocolos_atendimentos)  AS total_ciclos,
    (SELECT count(*) FROM plugin_protocolos_avaliacoes)    AS total_avaliacoes,
    (SELECT count(*) FROM contacts)                        AS total_contatos;

-- ---------------------------------------------------------------------------
-- 5. Imunizacao: contatos que carregam as duas variantes de source_id.
--    Antes do merge eram 33. Depois, 33 + pares mesclados.
-- ---------------------------------------------------------------------------
\echo '--- 5. contatos imunizados (duas variantes em contact_inboxes) ---'
SELECT count(*) AS contatos_com_ambas_variantes
FROM (SELECT contact_id FROM contact_inboxes
      GROUP BY contact_id HAVING count(DISTINCT length(source_id)) > 1) x;

-- ---------------------------------------------------------------------------
-- 6. Conversas abertas duplicadas no mesmo contato — precisam de tratamento
--    manual pelo painel (Passo 7 do 02-PLANO-merge.md)
-- ---------------------------------------------------------------------------
\echo '--- 6. contatos com mais de uma conversa aberta ---'
SELECT a.contact_id, c.phone, c.name, count(*) AS abertas,
       array_agg(a.id ORDER BY a.id) AS conversas
FROM atendimentos a
JOIN contacts c ON c.id = a.contact_id
WHERE a.status = 'open'
GROUP BY a.contact_id, c.phone, c.name
HAVING count(*) > 1
ORDER BY count(*) DESC, a.contact_id;

-- ---------------------------------------------------------------------------
-- 7. Amostra para conferencia visual no painel
-- ---------------------------------------------------------------------------
\echo '--- 7. amostra: contatos mesclados com historico consolidado ---'
SELECT c.id, c.phone, c.name,
       (SELECT count(*) FROM messages m WHERE m.contact_id = c.id)     AS msgs,
       (SELECT count(*) FROM atendimentos a WHERE a.contact_id = c.id) AS convs,
       (SELECT count(*) FROM contact_inboxes ci WHERE ci.contact_id = c.id) AS variantes
FROM contacts c
WHERE c.id IN (SELECT contact_id FROM contact_inboxes
               GROUP BY contact_id HAVING count(DISTINCT length(source_id)) > 1)
ORDER BY msgs DESC
LIMIT 15;

ROLLBACK;
