-- ============================================================================
-- 01-preflight.sql  —  READ-ONLY. Invariantes que precisam valer ANTES do merge.
--
-- TODAS as colunas "*_deve_ser_zero" precisam voltar 0.
-- Qualquer valor diferente de zero INTERROMPE o processo.
--
--   psql "$DSN" -f sql/01-preflight.sql
--
-- Nota: o CTE e repetido em cada bloco de proposito — CREATE TEMP VIEW/TABLE
-- nao e permitido dentro de uma transacao READ ONLY, e o READ ONLY aqui e a
-- garantia de que este arquivo nunca escreve.
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN TRANSACTION READ ONLY;

-- ---------------------------------------------------------------------------
-- 1. Colisoes e integridade dos pares
-- ---------------------------------------------------------------------------
\echo '--- 1. colisoes e integridade ---'
WITH base AS (
    SELECT id, phone, is_group, length(phone) AS len, (substr(phone,3,2))::int AS ddd,
        CASE WHEN phone ~ '^55[0-9]{11}$' AND substr(phone,5,1)='9'
             THEN substr(phone,1,4)||substr(phone,6) ELSE phone END AS canon
    FROM contacts WHERE phone ~ '^55[0-9]{10,11}$'
),
dup AS (SELECT canon FROM base GROUP BY canon HAVING count(*)>1 AND count(DISTINCT len)>1),
p AS (
    SELECT CASE WHEN a.ddd <= 28 THEN b.id ELSE a.id END AS win,
           CASE WHEN a.ddd <= 28 THEN a.id ELSE b.id END AS lose
    FROM (SELECT * FROM base WHERE len=12) a
    JOIN (SELECT * FROM base WHERE len=13) b ON b.canon = a.canon
    JOIN dup d ON d.canon = a.canon
)
SELECT
    (SELECT count(*) FROM p)                                   AS pares_a_mesclar,

    -- mesma tag nos dois lados: tratado com ON CONFLICT, mas se aparecer,
    -- conferir antes (pode indicar etiquetagem manual dos dois lados)
    (SELECT count(*) FROM p
        JOIN contact_tags t1 ON t1.contact_id = p.win
        JOIN contact_tags t2 ON t2.contact_id = p.lose AND t2.tag_id = t1.tag_id)
        AS colisao_tags_informativo,

    -- mesmo (inbox_id, source_id) nos dois lados -> violaria uq_contact_inbox_inbox_source
    (SELECT count(*) FROM p
        JOIN contact_inboxes c1 ON c1.contact_id = p.win
        JOIN contact_inboxes c2 ON c2.contact_id = p.lose
            AND c2.inbox_id = c1.inbox_id AND c2.source_id = c1.source_id)
        AS colisao_contact_inbox_deve_ser_zero,

    -- contato participando de mais de um par (cadeia): o loop do merge nao
    -- trata cadeia; se houver, resolver manualmente antes
    (SELECT count(*) FROM (
        SELECT id FROM (SELECT win AS id FROM p UNION ALL SELECT lose FROM p) u
        GROUP BY id HAVING count(*) > 1) x)
        AS contato_em_varios_pares_deve_ser_zero,

    (SELECT count(*) FROM p WHERE win = lose)                  AS win_igual_lose_deve_ser_zero,

    (SELECT count(*) FROM p
        WHERE NOT EXISTS (SELECT 1 FROM contacts c WHERE c.id = p.win)
           OR NOT EXISTS (SELECT 1 FROM contacts c WHERE c.id = p.lose))
        AS contato_inexistente_deve_ser_zero,

    (SELECT count(*) FROM p JOIN contacts c ON c.id = p.win WHERE c.is_group = 1)
        AS grupo_no_conjunto_deve_ser_zero,

    -- BLOQUEADORES: indices unicos PARCIAIS que fazem o merge falhar.
    -- uq_atend_open_contact_inbox = UNIQUE(contact_id, inbox_id) WHERE status='open'
    -- Um contato NAO PODE ter duas conversas abertas no mesmo inbox. Se os dois
    -- lados tiverem conversa aberta no mesmo inbox, mover o perdedor viola o indice
    -- e ABORTA A TRANSACAO INTEIRA. Resolver uma das conversas pelo painel ANTES.
    (SELECT count(*) FROM p WHERE EXISTS (
        SELECT 1 FROM atendimentos a1
        JOIN atendimentos a2 ON a2.contact_id = p.lose AND a2.status='open'
                            AND a2.inbox_id = a1.inbox_id
        WHERE a1.contact_id = p.win AND a1.status='open'))
        AS BLOQUEIA_conversa_aberta_deve_ser_zero,

    -- plugin_protocolos_one_open_per_contact = UNIQUE(contact_id) WHERE status='aberto'
    -- Um contato NAO PODE ter dois protocolos abertos. Mesmo efeito.
    (SELECT count(*) FROM p
        WHERE EXISTS (SELECT 1 FROM plugin_protocolos_protocolos x
                      WHERE x.contact_id = p.win AND x.status='aberto')
          AND EXISTS (SELECT 1 FROM plugin_protocolos_protocolos y
                      WHERE y.contact_id = p.lose AND y.status='aberto'))
        AS BLOQUEIA_protocolo_aberto_deve_ser_zero;

-- Detalhe dos bloqueadores, para saber O QUE tratar antes do merge
\echo '--- 1b. detalhe: conversas abertas em conflito (tratar pelo painel) ---'
WITH base AS (
    SELECT id, phone, length(phone) AS len, (substr(phone,3,2))::int AS ddd,
        CASE WHEN phone ~ '^55[0-9]{11}$' AND substr(phone,5,1)='9'
             THEN substr(phone,1,4)||substr(phone,6) ELSE phone END AS canon
    FROM contacts WHERE phone ~ '^55[0-9]{10,11}$'
),
dup AS (SELECT canon FROM base GROUP BY canon HAVING count(*)>1 AND count(DISTINCT len)>1),
p AS (
    SELECT CASE WHEN a.ddd<=28 THEN b.id ELSE a.id END AS win,
           CASE WHEN a.ddd<=28 THEN a.id ELSE b.id END AS lose
    FROM (SELECT * FROM base WHERE len=12) a
    JOIN (SELECT * FROM base WHERE len=13) b ON b.canon=a.canon JOIN dup d ON d.canon=a.canon
)
SELECT p.win, p.lose, a1.inbox_id,
       a1.id AS conversa_do_vencedor, a2.id AS conversa_do_perdedor_RESOLVER,
       to_char(to_timestamp(a2.last_activity_at),'YYYY-MM-DD') AS perdedor_parado_desde
FROM p
JOIN atendimentos a1 ON a1.contact_id = p.win  AND a1.status='open'
JOIN atendimentos a2 ON a2.contact_id = p.lose AND a2.status='open'
                    AND a2.inbox_id = a1.inbox_id;

-- ---------------------------------------------------------------------------
-- 2. Orfaos JA existentes (NAO causados por este merge).
--    Medido em 2026-08-04: protocolos=36 ciclos=45 avaliacoes=15 agendamentos=3
--    melhorias=0 protocolos_phone=1 avaliacoes_phone=0
--    ANOTAR os valores de hoje: e este o baseline que o 03-verificacao.sql
--    compara. O criterio pos-merge NAO e zero, e "nao aumentou".
-- ---------------------------------------------------------------------------
\echo '--- 2. baseline de orfaos pre-existentes (ANOTAR) ---'
SELECT
    (SELECT count(*) FROM plugin_protocolos_protocolos p WHERE p.contact_id IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM contacts c WHERE c.id = p.contact_id))     AS orf_protocolos,
    (SELECT count(*) FROM plugin_protocolos_atendimentos p WHERE p.contact_id IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM contacts c WHERE c.id = p.contact_id))     AS orf_ciclos,
    (SELECT count(*) FROM plugin_protocolos_avaliacoes p WHERE p.contact_id IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM contacts c WHERE c.id = p.contact_id))     AS orf_avaliacoes,
    (SELECT count(*) FROM plugin_agendamento_retorno_items p WHERE p.contact_id IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM contacts c WHERE c.id = p.contact_id))     AS orf_agendamentos,
    (SELECT count(*) FROM plugin_melhorias_suggestions p WHERE p.contact_id IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM contacts c WHERE c.id = p.contact_id))     AS orf_melhorias,
    (SELECT count(*) FROM plugin_protocolos_protocolos p WHERE p.contact_phone IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM contacts c WHERE c.phone = p.contact_phone)) AS orf_protocolos_phone,
    (SELECT count(*) FROM plugin_protocolos_avaliacoes p WHERE p.contact_phone IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM contacts c WHERE c.phone = p.contact_phone)) AS orf_avaliacoes_phone;

-- ---------------------------------------------------------------------------
-- 3. Baseline de totais — NAO podem mudar com o merge (so "contacts" diminui).
--    ANOTAR: o 03-verificacao.sql compara contra estes numeros.
-- ---------------------------------------------------------------------------
\echo '--- 3. baseline de totais (ANOTAR) ---'
SELECT
    (SELECT count(*) FROM messages)                        AS total_mensagens,
    (SELECT count(*) FROM atendimentos)                    AS total_conversas,
    (SELECT count(*) FROM contact_inboxes)                 AS total_contact_inboxes,
    (SELECT count(*) FROM plugin_protocolos_protocolos)    AS total_protocolos,
    (SELECT count(*) FROM plugin_protocolos_atendimentos)  AS total_ciclos,
    (SELECT count(*) FROM plugin_protocolos_avaliacoes)    AS total_avaliacoes,
    (SELECT count(*) FROM contacts)                        AS total_contatos,
    (SELECT count(*) FROM (SELECT contact_id FROM contact_inboxes
        GROUP BY contact_id HAVING count(DISTINCT length(source_id)) > 1) z)
        AS contatos_ja_imunizados;

ROLLBACK;
