-- Plano 61: tipo da falha do executor, gravada como role=system.
-- Ate aqui a natureza da falha era adivinhada pelo TEXTO da mensagem, o que
-- confundia limite de uso com sessao expirada. O executor agora manda um kind
-- tipado e o gateway o persiste aqui, para a hidratacao do chat ler o tipo em
-- vez de chutar. Nullable de proposito: linhas legadas (401 ja gravados em
-- producao) continuam validas com NULL e caem na heuristica antiga.
-- Atencao: comentarios sem ponto-e-virgula (o migrator splita por ele).

ALTER TABLE plugin_melhorias_ai_messages ADD COLUMN failure_kind TEXT;
