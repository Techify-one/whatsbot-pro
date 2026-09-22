# RBAC e escopo de conversas

Permissão responde **o que** o usuário pode fazer; membresia de inbox/time responde
**em quais conversas**. Os dois gates são cumulativos. Uma API key resolve para o
mesmo usuário de uma sessão e, portanto, recebe exatamente a mesma decisão.

## Times

| Permissão | Efeito |
|---|---|
| `team.manage` | criar, editar, desativar e gerenciar membros |
| `conversation.team.assign` | atribuir quando origem e destino pertencem ao ator |
| `conversation.team.assign_any` | atribuir entre quaisquer times acessíveis pela inbox |
| `conversation.team.read_any` | ignorar a restrição de leitura do time |

`conversation.read_all` continua significando todas as inboxes e não substitui
`conversation.team.read_any`. Admin mantém o wildcard. O papel `gestor` recebe
`team.manage`, `conversation.team.assign` e `conversation.team.assign_any`, mas não
recebe leitura irrestrita de times privados.

## Ordem da policy

1. A inbox é validada primeiro e nunca é ampliada pelo time.
2. `open` não adiciona restrição.
3. `list_hidden` restringe coleções, mas preserva link direto e operações legadas.
4. `private` restringe coleção, detalhe, escrita, realtime e mídia.
5. Membro do time, responsável quando `visible_to_assignee=1`, ou usuário com
   `conversation.team.read_any` atravessa a cerca do time.

Negação de uma conversa privada retorna 404 para não confirmar sua existência.
Um time privado ativo precisa ter ao menos um membro ativo; desativar ou excluir o
último é bloqueado. Sem a exceção do responsável, o time não pode manter conversa
atribuída a usuário externo. Desativação preserva vínculo e policy; hard delete é
bloqueado enquanto houver conversa vinculada.

## Realtime (WebSocket)

`ConversationAccessScope.for_user` (HTTP) e `.for_users` (WS, plano 168 F3 —
mesma policy, em LOTE para todos os sockets conectados de uma vez, não um a um)
avaliam a MESMA regra acima; nunca divergem por caminho. Sem cache entre
eventos (regra do topo deste doc): revogar acesso vale no próximo evento, não
só no próximo login. Usuário desativado (`is_active=0`) recebe escopo que nega
toda conversa mesmo com socket já aberto — a desativação não fecha a conexão
por si só. Contrato completo do roteamento/descarte por evento:
[docs/PLUGIN_BUS.md](PLUGIN_BUS.md#audiência-de-conversa-no-websocket-plano-16601--168).

## Custo por requisição (HTTP) — plano 169 B2

`server/app.py` calcula `authz.effective_permissions(user)` **uma vez**, logo
após a identidade ser resolvida (sessão ou chave de API), e guarda em
`request.state.effective_permissions`. `_rbac_allows`/`visible_inbox_ids`/
`ConversationAccessScope.for_request` leem dali (`authz._has`) em vez de
consultar o banco a cada checagem — uma requisição que gatea uma rota de
plugin (`plugin_permission`/`core_permission`) **e** chama `visible_inbox_ids`
no corpo (padrão comum em rota de conversa) paga 1 consulta de permissão, não
2+. Sem regressão do "revogar vale na hora": o cache é **por requisição**,
nunca entre requisições — a próxima já resolve a identidade e o conjunto do
zero. Nunca sincronize com o cache de 60s do compare Argon2 (API_REST.md) nem
com a ausência de cache do WS acima — são três garantias independentes.

Derivação em `authz.effective_permissions` (evita reconsultar o que
`user_repo._with_roles` já carregou em `user["roles"]`/`user["permissions"]`/
`user["is_admin"]`/`user["custom_permissions"]`): admin ⇒ `{"*"}` sem consulta
(todo chamador só testa `"*" in perms`, nunca itera — enumerar cada permission
key mudaria zero decisão); custom ⇒ as concessões explícitas que `_with_roles`
já buscou, zero consultas; role comum ⇒ 1 consulta (`rbac_repo.
permissions_for_roles`, união pelas roles já conhecidas — não
`rbac_repo.user_permissions`, que re-buscaria as mesmas roles).

⚠️ **Não é presença, é tipo**: quem lê o cache (`authz._cached_permissions`)
exige `isinstance(valor, frozenset)`, não só "não é `None`". Um `Request` FAKE
(`MagicMock()`, usado por teste que chama `acheck` direto sem passar pelo
middleware) responde QUALQUER atributo com outro Mock truthy — sem a guarda de
tipo, `request.state.effective_permissions` num mock desses seria lido como
cache válido (lixo). `for_user`/`for_users` (WS) não usam nem precisam deste
cache — ver seção acima.
