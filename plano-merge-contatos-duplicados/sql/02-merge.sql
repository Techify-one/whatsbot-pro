-- ============================================================================
-- 02-merge.sql  —  ESCRITA. Mescla os pares duplicados por variante BR.
--
--  >>> ESTE ARQUIVO TERMINA EM "ROLLBACK;" DE PROPOSITO. <<<
--  >>> Rodar assim primeiro (ENSAIO). Para valer, trocar a ULTIMA linha  <<<
--  >>> por "COMMIT;" — e so depois do preflight limpo + backup + aprovacao. <<<
--
--   ENSAIO : psql "$DSN" -f sql/02-merge.sql
--   COM PARES REPROVADOS:
--            psql "$DSN" -v reprovados="ARRAY[12277,13665]::int[]" -f sql/02-merge.sql
--
--  Informar QUALQUER um dos dois ids de um par (o de 12 ou o de 13 digitos)
--  exclui o par inteiro do merge.
--
--  Idempotente: par ja mesclado nao aparece mais na deteccao.
-- ============================================================================

\set ON_ERROR_STOP on

-- default quando -v reprovados nao foi passado
\if :{?reprovados}
\else
  \set reprovados 'ARRAY[]::int[]'
\endif

BEGIN;

-- ---------------------------------------------------------------------------
-- Pares a processar. Vencedor = a forma que o WhatsApp entrega naquele DDD.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE _merge_pares ON COMMIT DROP AS
WITH base AS (
    SELECT id, phone, length(phone) AS len, (substr(phone,3,2))::int AS ddd,
        CASE WHEN phone ~ '^55[0-9]{11}$' AND substr(phone,5,1)='9'
             THEN substr(phone,1,4)||substr(phone,6) ELSE phone END AS canon
    FROM contacts WHERE phone ~ '^55[0-9]{10,11}$'
),
dup AS (SELECT canon FROM base GROUP BY canon HAVING count(*)>1 AND count(DISTINCT len)>1),
par AS (
    SELECT
        CASE WHEN a.ddd <= 28 THEN b.id    ELSE a.id    END AS win,
        CASE WHEN a.ddd <= 28 THEN b.phone ELSE a.phone END AS win_phone,
        CASE WHEN a.ddd <= 28 THEN a.id    ELSE b.id    END AS lose,
        CASE WHEN a.ddd <= 28 THEN a.phone ELSE b.phone END AS lose_phone
    FROM (SELECT * FROM base WHERE len=12) a
    JOIN (SELECT * FROM base WHERE len=13) b ON b.canon = a.canon
    JOIN dup d ON d.canon = a.canon
)
SELECT * FROM par
WHERE win <> ALL (:reprovados)
  AND lose <> ALL (:reprovados);

-- ---------------------------------------------------------------------------
-- Alvos. Derivados do catalogo: tabela/coluna que nao existir simplesmente
-- nao entra (plugin desinstalado nao quebra o script).
-- contact_tags fica FORA: tem PK composta e e tratada a parte.
-- ---------------------------------------------------------------------------
CREATE TEMP TABLE _alvos_contact ON COMMIT DROP AS
SELECT table_name::text AS tabela, column_name::text AS coluna
FROM information_schema.columns
WHERE table_schema = 'public'
  AND column_name = 'contact_id'
  AND table_name IN (
        'messages',                          -- ON DELETE CASCADE
        'atendimentos',                      -- ON DELETE CASCADE
        'contact_inboxes',                   -- move a variante -> imuniza o numero
        'observations',
        'unread_msg_ids',
        'usage',
        'mentions',
        'plugin_protocolos_protocolos',      -- sem FK
        'plugin_protocolos_atendimentos',    -- sem FK
        'plugin_protocolos_avaliacoes',      -- sem FK
        'plugin_agendamento_retorno_items',  -- sem FK
        'plugin_melhorias_suggestions'       -- sem FK
  );

-- Telefone denormalizado (nao e FK; precisa de UPDATE explicito).
-- Fora de proposito: plugin_debug_bus_records (dado efemero de debug) e
-- as colunas source_id de janela_72h/vendas_ia (guardam a variante usada na
-- origem, que continua valida apos o merge).
CREATE TEMP TABLE _alvos_phone ON COMMIT DROP AS
SELECT table_name::text AS tabela, column_name::text AS coluna
FROM information_schema.columns
WHERE table_schema = 'public' AND (
      (table_name = 'plugin_protocolos_protocolos'     AND column_name = 'contact_phone')
   OR (table_name = 'plugin_protocolos_avaliacoes'     AND column_name = 'contact_phone')
   OR (table_name = 'plugin_melhorias_suggestions'     AND column_name = 'contact_phone')
   OR (table_name = 'plugin_agendamento_retorno_items' AND column_name = 'phone')
   OR (table_name = 'plugin_janela_72h_windows'        AND column_name = 'phone')
   OR (table_name = 'plugin_vendas_ia_ad_leads'        AND column_name = 'phone')
   OR (table_name = 'executions'                       AND column_name = 'phone')
);

\echo '--- alvos por contact_id ---'
SELECT tabela, coluna FROM _alvos_contact ORDER BY tabela;
\echo '--- alvos por telefone denormalizado ---'
SELECT tabela, coluna FROM _alvos_phone ORDER BY tabela;
\echo '--- pares a processar ---'
SELECT count(*) AS pares FROM _merge_pares;

-- ---------------------------------------------------------------------------
-- Merge
-- ---------------------------------------------------------------------------
DO $mrg$
DECLARE
    r           record;
    a           record;
    n_pares     int    := 0;
    n_removidos int    := 0;
    n_lin       bigint;
    total_lin   bigint := 0;
    sobrou      bigint;
    bloqueados  bigint;
BEGIN
    -- ---------------------------------------------------------------------
    -- GUARD 1: uq_atend_open_contact_inbox = UNIQUE(contact_id, inbox_id)
    -- WHERE status='open'. Dois lados com conversa aberta no MESMO inbox
    -- violariam o indice e derrubariam a transacao inteira (os 203 pares).
    -- Resolver a conversa do lado perdedor pelo painel ANTES (Passo 3.1).
    -- ---------------------------------------------------------------------
    SELECT count(*) INTO bloqueados
    FROM _merge_pares p
    WHERE EXISTS (
        SELECT 1 FROM atendimentos a1
        JOIN atendimentos a2 ON a2.contact_id = p.lose AND a2.status='open'
                            AND a2.inbox_id = a1.inbox_id
        WHERE a1.contact_id = p.win AND a1.status='open');
    IF bloqueados > 0 THEN
        RAISE EXCEPTION
            'ABORTADO: % par(es) com conversa ABERTA nos dois lados no mesmo inbox. '
            'Resolver a conversa do lado perdedor pelo painel antes (ver bloco 1b do preflight).',
            bloqueados;
    END IF;

    -- ---------------------------------------------------------------------
    -- GUARD 2: plugin_protocolos_one_open_per_contact = UNIQUE(contact_id)
    -- WHERE status='aberto'. Mesmo efeito.
    -- ---------------------------------------------------------------------
    IF to_regclass('public.plugin_protocolos_protocolos') IS NOT NULL THEN
        SELECT count(*) INTO bloqueados
        FROM _merge_pares p
        WHERE EXISTS (SELECT 1 FROM plugin_protocolos_protocolos x
                      WHERE x.contact_id = p.win  AND x.status='aberto')
          AND EXISTS (SELECT 1 FROM plugin_protocolos_protocolos y
                      WHERE y.contact_id = p.lose AND y.status='aberto');
        IF bloqueados > 0 THEN
            RAISE EXCEPTION
                'ABORTADO: % par(es) com protocolo ABERTO nos dois lados. '
                'Fechar o protocolo do lado perdedor pelo painel antes.', bloqueados;
        END IF;
    END IF;

    FOR r IN SELECT * FROM _merge_pares ORDER BY win LOOP

        -- 1) tags: PK (contact_id, tag_id) — copia sem duplicar, depois limpa
        INSERT INTO contact_tags (contact_id, tag_id)
        SELECT r.win, ct.tag_id FROM contact_tags ct WHERE ct.contact_id = r.lose
        ON CONFLICT DO NOTHING;
        DELETE FROM contact_tags WHERE contact_id = r.lose;

        -- 2) tudo que referencia contact_id
        FOR a IN SELECT * FROM _alvos_contact LOOP
            EXECUTE format('UPDATE %I SET %I = $1 WHERE %I = $2', a.tabela, a.coluna, a.coluna)
                USING r.win, r.lose;
            GET DIAGNOSTICS n_lin = ROW_COUNT;
            total_lin := total_lin + n_lin;
        END LOOP;

        -- 3) telefone denormalizado
        FOR a IN SELECT * FROM _alvos_phone LOOP
            EXECUTE format('UPDATE %I SET %I = $1 WHERE %I = $2', a.tabela, a.coluna, a.coluna)
                USING r.win_phone, r.lose_phone;
            GET DIAGNOSTICS n_lin = ROW_COUNT;
            total_lin := total_lin + n_lin;
        END LOOP;

        -- 4) consolida o cadastro: campo vazio no vencedor herda o do perdedor.
        --    is_pinned / is_archived / ai_enabled / contact_type ficam do vencedor.
        UPDATE contacts w SET
            name              = COALESCE(NULLIF(btrim(coalesce(w.name,'')), ''), l.name),
            email             = COALESCE(NULLIF(btrim(coalesce(w.email,'')), ''), l.email),
            profession        = COALESCE(NULLIF(btrim(coalesce(w.profession,'')), ''), l.profession),
            company           = COALESCE(NULLIF(btrim(coalesce(w.company,'')), ''), l.company),
            address           = COALESCE(NULLIF(btrim(coalesce(w.address,'')), ''), l.address),
            unread_count      = COALESCE(w.unread_count, 0) + COALESCE(l.unread_count, 0),
            has_unread_mention= GREATEST(COALESCE(w.has_unread_mention,0), COALESCE(l.has_unread_mention,0)),
            -- o do vencedor prevalece em caso de chave repetida
            custom_attributes = COALESCE(l.custom_attributes, '{}'::jsonb)
                                || COALESCE(w.custom_attributes, '{}'::jsonb),
            updated_at        = extract(epoch FROM now())
        FROM contacts l
        WHERE w.id = r.win AND l.id = r.lose;

        -- 5) GUARDA: messages/atendimentos sao ON DELETE CASCADE. Se sobrou
        --    qualquer dependente, o DELETE abaixo apagaria historico em silencio.
        sobrou := 0;
        FOR a IN SELECT * FROM _alvos_contact LOOP
            EXECUTE format('SELECT count(*) FROM %I WHERE %I = $1', a.tabela, a.coluna)
                INTO n_lin USING r.lose;
            sobrou := sobrou + n_lin;
        END LOOP;
        SELECT sobrou + count(*) INTO sobrou FROM contact_tags WHERE contact_id = r.lose;

        IF sobrou > 0 THEN
            RAISE EXCEPTION
                'ABORTADO no par win=% lose=%: sobraram % dependente(s). Nada foi commitado.',
                r.win, r.lose, sobrou;
        END IF;

        DELETE FROM contacts WHERE id = r.lose;
        GET DIAGNOSTICS n_lin = ROW_COUNT;
        n_removidos := n_removidos + n_lin;

        n_pares := n_pares + 1;
    END LOOP;

    RAISE NOTICE '=== pares_processados=%  contatos_removidos=%  linhas_remapeadas=% ===',
                 n_pares, n_removidos, total_lin;

    IF n_pares <> n_removidos THEN
        RAISE EXCEPTION 'ABORTADO: processados % mas removidos % — inconsistente.',
                        n_pares, n_removidos;
    END IF;
END
$mrg$;

-- ---------------------------------------------------------------------------
-- Conferencia dentro da mesma transacao (vale para o ensaio tambem)
-- ---------------------------------------------------------------------------
\echo '--- pares remanescentes (esperado: 0, ou apenas os reprovados) ---'
WITH base AS (
    SELECT id, phone, length(phone) AS len,
        CASE WHEN phone ~ '^55[0-9]{11}$' AND substr(phone,5,1)='9'
             THEN substr(phone,1,4)||substr(phone,6) ELSE phone END AS canon
    FROM contacts WHERE phone ~ '^55[0-9]{10,11}$'
)
SELECT count(*) AS pares_remanescentes
FROM (SELECT canon FROM base GROUP BY canon
      HAVING count(*)>1 AND count(DISTINCT len)>1) x;

\echo '--- contatos que agora carregam as DUAS variantes (imunizados) ---'
SELECT count(*) AS contatos_com_ambas_variantes
FROM (SELECT contact_id FROM contact_inboxes
      GROUP BY contact_id HAVING count(DISTINCT length(source_id)) > 1) y;

-- ============================================================================
--  TROCAR PARA "COMMIT;" SOMENTE APOS: backup fresco + preflight limpo +
--  ensaio conferido + aprovacao do usuario na hora.
-- ============================================================================
ROLLBACK;
