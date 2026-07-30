-- Atendente PROVISORIO: espelho do atendente NATIVO da conversa do core
-- (atendimentos.assignee_user_id) no protocolo e no ciclo aberto.
--
-- Para que serve: o supervisor precisa achar os protocolos/atendimentos ABERTOS que ainda
-- NAO tem atendente DEFINITIVO salvo (o rotulo Atendente do formulario de Resolver/
-- Finalizar, que vive em assignee_user_id) mas cuja CONVERSA ja esta atribuida a alguem.
--
-- O atendente EFETIVO exibido no Kanban/lista passa a ser COALESCE(definitivo, provisorio).
-- O provisorio NUNCA entra em _effective_values/_missing_required nem na semeadura do
-- formulario -- ele e so leitura, filtro e agrupamento.
--
-- provisional_assignee_user_id: core users.id (NULL = conversa sem dono humano).
-- provisional_assignee_name:    snapshot do nome, p/ exibir sem join.
-- provisional_set_at:           epoch da ultima sincronizacao (auditoria/depuracao).
--
-- SQL portavel: o migrator valida o prefixo plugin_protocolos_ e ADD COLUMN roda em
-- Postgres. Nao usar ponto-e-virgula nos comentarios (o splitter quebra por ele).

ALTER TABLE plugin_protocolos_protocolos ADD COLUMN provisional_assignee_user_id INTEGER;
ALTER TABLE plugin_protocolos_protocolos ADD COLUMN provisional_assignee_name TEXT NOT NULL DEFAULT '';
ALTER TABLE plugin_protocolos_protocolos ADD COLUMN provisional_set_at DOUBLE PRECISION;

ALTER TABLE plugin_protocolos_atendimentos ADD COLUMN provisional_assignee_user_id INTEGER;
ALTER TABLE plugin_protocolos_atendimentos ADD COLUMN provisional_assignee_name TEXT NOT NULL DEFAULT '';
ALTER TABLE plugin_protocolos_atendimentos ADD COLUMN provisional_set_at DOUBLE PRECISION;

-- Filtro "Vinculo do atendente" = so provisorios (definitivo nulo, provisorio presente).
CREATE INDEX IF NOT EXISTS plugin_protocolos_proto_prov
    ON plugin_protocolos_protocolos(provisional_assignee_user_id);

CREATE INDEX IF NOT EXISTS plugin_protocolos_atend_prov
    ON plugin_protocolos_atendimentos(provisional_assignee_user_id);

-- Indice de EXPRESSAO do atendente EFETIVO: o filtro nativo "Atendente" passa a buscar por
-- COALESCE(definitivo, provisorio) IN (...), entao o indice simples em assignee_user_id
-- deixaria de ser usado. COALESCE de colunas e imutavel, logo indexavel.
CREATE INDEX IF NOT EXISTS plugin_protocolos_proto_eff_assignee
    ON plugin_protocolos_protocolos((COALESCE(assignee_user_id, provisional_assignee_user_id)));

-- BACKFILL 1 -- ciclos ABERTOS: carimba o dono atual da conversa vinculada.
-- Idempotente pelo IS DISTINCT FROM (re-rodar nao escreve nada).
UPDATE plugin_protocolos_atendimentos AS pa
   SET provisional_assignee_user_id = a.assignee_user_id,
       provisional_assignee_name    = COALESCE(NULLIF(u.name, ''), u.email, ''),
       provisional_set_at           = pa.updated_at
  FROM atendimentos AS a
  LEFT JOIN users AS u ON u.id = a.assignee_user_id
 WHERE a.id = pa.conversation_id
   AND pa.ended_at IS NULL
   AND a.assignee_user_id IS NOT NULL
   AND pa.provisional_assignee_user_id IS DISTINCT FROM a.assignee_user_id;

-- BACKFILL 2 -- protocolos ABERTOS: mesma regra, pela conversa do ciclo MAIS RECENTE
-- (mesma definicao de "conversa mais recente" do corte de arquivados em _build_list_where).
UPDATE plugin_protocolos_protocolos AS p
   SET provisional_assignee_user_id = a.assignee_user_id,
       provisional_assignee_name    = COALESCE(NULLIF(u.name, ''), u.email, ''),
       provisional_set_at           = p.updated_at
  FROM plugin_protocolos_atendimentos AS pa
  JOIN atendimentos AS a ON a.id = pa.conversation_id
  LEFT JOIN users AS u ON u.id = a.assignee_user_id
 WHERE pa.protocolo_id = p.id
   AND p.status = 'aberto'
   AND a.assignee_user_id IS NOT NULL
   AND pa.id = (SELECT p2.id FROM plugin_protocolos_atendimentos p2
                 WHERE p2.protocolo_id = p.id AND p2.conversation_id IS NOT NULL
                 ORDER BY p2.started_at DESC, p2.id DESC LIMIT 1)
   AND p.provisional_assignee_user_id IS DISTINCT FROM a.assignee_user_id;
