-- ============================================================================
-- 00-deteccao.sql  —  READ-ONLY. Lista e classifica os pares duplicados
--                     por variante BR (12 <-> 13 digitos).
--
-- Seguro de rodar a qualquer momento. Nao escreve nada.
--
--   psql "$DSN" -f sql/00-deteccao.sql
--   psql "$DSN" -At -F';' -f sql/00-deteccao.sql > pares-$(date +%Y%m%d).csv
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN TRANSACTION READ ONLY;

WITH base AS (
    -- forma canonica: remove o 9o digito do numero de 13 digitos
    SELECT
        id,
        phone,
        name,
        length(phone)                AS len,
        (substr(phone, 3, 2))::int   AS ddd,
        CASE
            WHEN phone ~ '^55[0-9]{11}$' AND substr(phone, 5, 1) = '9'
                THEN substr(phone, 1, 4) || substr(phone, 6)
            ELSE phone
        END AS canon
    FROM contacts
    WHERE phone ~ '^55[0-9]{10,11}$'
),
dup AS (
    SELECT canon
    FROM base
    GROUP BY canon
    HAVING count(*) > 1 AND count(DISTINCT len) > 1
),
par AS (
    SELECT
        a.canon,
        -- vencedor = a forma que o WhatsApp entrega naquele DDD
        CASE WHEN a.ddd <= 28 THEN b.id    ELSE a.id    END AS win,
        CASE WHEN a.ddd <= 28 THEN b.phone ELSE a.phone END AS win_phone,
        CASE WHEN a.ddd <= 28 THEN b.name  ELSE a.name  END AS win_name,
        CASE WHEN a.ddd <= 28 THEN a.id    ELSE b.id    END AS lose,
        CASE WHEN a.ddd <= 28 THEN a.phone ELSE b.phone END AS lose_phone,
        CASE WHEN a.ddd <= 28 THEN a.name  ELSE b.name  END AS lose_name,
        a.ddd,
        a.id AS id12, a.name AS n12,
        b.id AS id13, b.name AS n13,
        similarity(
            f_unaccent(lower(coalesce(a.name, ''))),
            f_unaccent(lower(coalesce(b.name, '')))
        ) AS sim
    FROM (SELECT * FROM base WHERE len = 12) a
    JOIN (SELECT * FROM base WHERE len = 13) b ON b.canon = a.canon
    JOIN dup d ON d.canon = a.canon
),
stat AS (
    SELECT
        p.*,
        (SELECT count(*) FROM messages     m WHERE m.contact_id = p.id12)                          AS msgs12,
        (SELECT count(*) FROM messages     m WHERE m.contact_id = p.id13)                          AS msgs13,
        (SELECT count(*) FROM messages     m WHERE m.contact_id = p.id12 AND m.role = 'user')      AS inbound12,
        (SELECT count(*) FROM messages     m WHERE m.contact_id = p.id13 AND m.role = 'user')      AS inbound13,
        (SELECT count(*) FROM atendimentos a WHERE a.contact_id = p.id12)                          AS convs12,
        (SELECT count(*) FROM atendimentos a WHERE a.contact_id = p.id13)                          AS convs13,
        (SELECT count(*) FROM atendimentos a WHERE a.contact_id = p.id12 AND a.status = 'open')    AS abertas12,
        (SELECT count(*) FROM atendimentos a WHERE a.contact_id = p.id13 AND a.status = 'open')    AS abertas13,
        (SELECT min(ts) FROM messages m WHERE m.contact_id = p.id12) AS t12a,
        (SELECT max(ts) FROM messages m WHERE m.contact_id = p.id12) AS t12b,
        (SELECT min(ts) FROM messages m WHERE m.contact_id = p.id13) AS t13a,
        (SELECT max(ts) FROM messages m WHERE m.contact_id = p.id13) AS t13b
    FROM par p
)
SELECT
    canon,
    ddd,
    win, win_phone, win_name,
    lose, lose_phone, lose_name,
    CASE
        WHEN f_unaccent(lower(coalesce(n12,''))) = f_unaccent(lower(coalesce(n13,'')))
             AND coalesce(n12,'') <> ''                                   THEN 'A'
        WHEN coalesce(sim, 0) >= 0.35                                     THEN 'B'
        WHEN n12 ~ '^[0-9]+$' OR n13 ~ '^[0-9]+$'
             OR coalesce(n12,'') = '' OR coalesce(n13,'') = ''            THEN 'C'
        ELSE 'D'
    END                                                                   AS classe,
    msgs12, msgs13, inbound12, inbound13,
    convs12, convs13, abertas12, abertas13,
    to_char(to_timestamp(t12a), 'YY-MM') || '>' || to_char(to_timestamp(t12b), 'YY-MM') AS per12,
    to_char(to_timestamp(t13a), 'YY-MM') || '>' || to_char(to_timestamp(t13b), 'YY-MM') AS per13,
    CASE WHEN t12b >= t13a AND t13b >= t12a THEN 'S' ELSE 'N' END         AS sobrepoe,
    -- sinal de possivel troca de titular: os 3 criterios juntos (ver 03-TRIAGEM.md)
    CASE
        WHEN NOT (t12b >= t13a AND t13b >= t12a)
             AND inbound12 > 0 AND inbound13 > 0
             AND coalesce(sim, 0) < 0.35
        THEN 'REVISAR'
        ELSE ''
    END                                                                   AS alerta
FROM stat
ORDER BY classe, canon;

-- ---------------------------------------------------------------------------
-- Resumo (esperado em 2026-08-04: 203 pares / A=62 B=66 C=9 D=66)
-- ---------------------------------------------------------------------------
WITH base AS (
    SELECT id, phone, name, length(phone) AS len,
        CASE WHEN phone ~ '^55[0-9]{11}$' AND substr(phone,5,1)='9'
             THEN substr(phone,1,4)||substr(phone,6) ELSE phone END AS canon
    FROM contacts WHERE phone ~ '^55[0-9]{10,11}$'
),
dup AS (SELECT canon FROM base GROUP BY canon HAVING count(*)>1 AND count(DISTINCT len)>1)
SELECT
    (SELECT count(*) FROM dup)                                    AS pares,
    (SELECT count(*) FROM base b JOIN dup d ON d.canon=b.canon)   AS contatos,
    (SELECT count(*) FROM messages WHERE contact_id IN
        (SELECT b.id FROM base b JOIN dup d ON d.canon=b.canon))  AS mensagens,
    (SELECT count(*) FROM atendimentos WHERE contact_id IN
        (SELECT b.id FROM base b JOIN dup d ON d.canon=b.canon))  AS conversas;

ROLLBACK;
