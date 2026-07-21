-- Marca "já decidido no popup de continuidade" por protocolo.
-- Quando o atendente decide fechar tudo (ou o fluxo já resolveu de outra forma), o
-- protocolo fechado anterior ganha relink_reviewed=1 e para de sugerir/adiar a abertura
-- de um novo protocolo para este re-engajamento. Portavel SQLite+Postgres, prefixado.
-- Nao usar ponto-e-virgula nos comentarios (o splitter quebra por ele).
ALTER TABLE plugin_protocolos_protocolos ADD COLUMN relink_reviewed INTEGER DEFAULT 0
