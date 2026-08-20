# Plano 134 — Sistema de API com chave por usuário

> **Status: IMPLEMENTADO** (2026-08-20). Este arquivo é o registro do plano
> COMO EXECUTADO — inclui as divergências encontradas na implementação, que são
> a parte útil de reler depois. O desenho e as decisões (D1–D7) são os da
> revisão 2 do plano original.

## O insight

A chave é apenas um **crachá** novo que resolve para o mesmo `request.state.user`
que uma sessão resolve. Feito isso no middleware, RBAC, auditoria (ator), escopo
por inbox e o gating de rotas de plugin **funcionam sem alteração** — a chave
"vira o usuário". `server/authz.py` não foi tocado.

A documentação de referência vive em **CLAUDE.md**, seção
"API para integrações externas — chave por usuário (`X-Api-Key`)". Este arquivo
guarda só o que não cabe lá: o que a implementação descobriu.

## O que a implementação encontrou que o plano não previa

### 1. As duas escritas "sem gate" da fase 0 JÁ estavam gateadas

O plano mandava gatear `POST /api/channels/{id}/reconnect` e `/logout` em
`channel.manage`. **Elas já estão**: os dois handlers delegam a
`_channel_session` ([server/routes/channels.py](../server/routes/channels.py)),
que chama `permission_denied(request, "channel.manage")` na primeira linha. A
auditoria que produziu o número do plano contou DECORADORES, e esses dois gates
estão no corpo do helper compartilhado.

**Lição para a próxima auditoria de permissões**: contar `require_permission` no
decorador subestima a cobertura sempre que houver um handler compartilhado.

### 2. `facebook_messenger` não existe neste checkout

Os 16 plugins instalados em `storages/plugins/` não incluem `facebook_messenger`
(nem `retornos`/`pagamentos`). O inventário do plano veio de outra instalação. As
"6 rotas sem gate" não existem aqui para serem corrigidas — se o plugin for
instalado, a correção é no repositório `whatsbot-pro-plugins`, não no core.

### 3. O formato da chave tinha um bug probabilístico

`wsk_live_<prefix>_<secret>` com `secrets.token_urlsafe` nos dois campos: o
alfabeto base64url **inclui `_`**, então um prefixo ou segredo sorteado com um
underscore produzia mais de quatro pedaços no `split("_")` e a chave era recusada
como malformada — de forma **aleatória, em ~1 de cada 3 chaves emitidas**.

Corrigido para `wsk_live_<prefix hex>.<secret>`: `.` não pertence ao alfabeto,
então o parsing é total. Travado por
`test_generate_key_survives_the_base64url_alphabet`, que roda 200 vezes de
propósito — o bug era probabilístico e um teste de uma execução passaria.

### 4. Argon2 por request é caro demais para uma integração

O plano mandava reusar o `CryptContext`/Argon2 de `server/auth.py`, e está certo
para o ARMAZENAMENTO. Mas verificar custa ~50-100ms **por chamada**, e uma
integração faz muitas chamadas com a mesma chave — vira gargalo e vetor de DoS.

Cacheamos só o resultado do **compare** por 60s. A **autorização não é cacheada**:
a linha é relida do banco a cada request, então revogar/expirar vale na hora.
Travado por `test_verify_cache_does_not_bypass_revocation`.

### 5. `inbox_guard` precisou ser um callable

O plano tratava o gate de inbox como algo que a rota faz antes de chamar o
serviço. Não dá: no handler original ele roda **depois do desvio de sandbox** e
antes de resolver o canal — um contato de sandbox nunca passou por ele. Checá-lo
antes mudaria o comportamento do painel. Por isso `send_text` recebe
`inbox_guard` como um callable e o invoca no ponto exato. **A ordem é contrato.**

### 6. As etiquetas de conversa também precisavam de serviço

O plano não mencionava. A primeira versão da fachada gravava a etiqueta direto no
repo e perdia os TRÊS efeitos que a rota do painel produz: o broadcast
`conversation_labels_changed` (o chat aberto não se atualizava), o evento
`conversation.labeled` (nenhum plugin sabia) e os cards no fio. Extraído para
`conversation_service.apply_labels`, consumido pelas duas rotas.

Mesmo tratamento para a escrita de CONTATO
([app/services/contact_service.py](../app/services/contact_service.py)), cujas
duas tolerâncias na validação de atributo (soft-deleted e chave herdada da
migração Chatwoot) não podiam ser reinventadas.

### 7. `conversation_upsert` não aceita payload parcial

Um `POST /api/v1/conversations/{id}/read` chegou a emitir
`conversation_upsert` com `{"id": conv_id}`. Esse evento carrega uma linha de
conversa **enriquecida** que o painel insere direto na sidebar — um `{id}`
parcial plantaria uma linha cega. A rota do painel não emite nada ali; a v1
passou a espelhá-la (marca lida + recibos pelo canal da conversa).

### 8. O `"*"` dos webhooks não podia significar "tudo"

O plano dizia "allowlist de eventos" sem fechar a semântica do curinga. Se `"*"`
significasse "qualquer coisa do barramento", um endpoint cadastrado hoje passaria
a receber, num upgrade do core, eventos que ninguém revisou — e `llm.after`
(que carrega o histórico da conversa e o prompt) sairia da instalação por
descuido. `"*"` cobre apenas o conjunto **curado** (`EXPORTABLE_EVENTS`); evento
de plugin precisa ser nomeado.

### 9. O refactor quase perdeu o `reason` do bloqueio de 24h

A suíte legada (`legacy_endpoints`) pegou: o handler antigo devolvia
`_err(msg, status=409, data={"reason": "session_window_closed"})`, e o
**compositor do painel LÊ essa chave** para decidir se oferece o fluxo de
template. A primeira versão da rota delegada devolvia só `message` + `status`, e
o `data` sumia — o 409 continuava certo, então o teste de status passava e só o
de `reason` acusava.

`send_text` passou a devolver um `data` opcional, preenchido **apenas** onde o
envelope legado o tinha. Os demais erros daquele handler nunca mandaram `data`, e
mandar agora mudaria a forma da resposta para clientes antigos — travado nos dois
sentidos por `test_panel_send_keeps_the_reason_payload` e
`test_panel_send_error_without_extra_keeps_the_legacy_shape`.

**Lição**: ao extrair um handler para serviço, o contrato não é só
"status + mensagem" — é o corpo INTEIRO, incluindo o extra que uma tela consome.

### 10. O contador de permissões da suíte legada é hardcoded

`legacy_endpoints` afirma "40 permissions" em quatro pontos. O catálogo foi para
**42** (`apikey.manage` + `webhook.manage`) e os quatro viraram 42. O
`_rp_count` (gestor 35 + atendente 5 = 40) **segue 40 de propósito**: é
exatamente o que prova que nenhuma das duas chaves novas entrou num papel.

### 11. O subscriber `*` não pode consultar o banco por evento

O handler roda em TODO evento, inclusive `message.received`. Cache de 5s dos
endpoints ativos, com invalidação explícita nas escritas e no auto-desligamento.

### 12. O guardrail multicanal pegou a fachada usando o resolvedor errado

`test_guardrail_no_new_channel_blind_resolvers` (plano 37 P4) reprovou
`server/routes/v1/messages.py`: o fallback "sem `conversation_id` nem
`channel_id`" usava `get_open_for_contact`, que é contact-scoped e **funde
canais** — exatamente o que o §8 do plano de API manda evitar. Com o mesmo número
atendido em duas caixas, a mensagem sairia pelo canal errado, em silêncio.

Trocado por uma escada explícita: uma conversa aberta ⇒ usa; nenhuma ⇒ deixa o
serviço abrir pelo caminho padrão; **mais de uma ⇒ 409 `ambiguous_target`
listando as opções**. Recusar é o único comportamento honesto — escolher por
conta própria é mandar mensagem de cliente pelo canal errado.

## Falhas PRÉ-EXISTENTES (não são desta entrega)

Medidas contra um worktree no `HEAD` (`7f711ad`), mesmo banco, mesmos plugins:

| Teste | Motivo |
|---|---|
| `test_audit_matrix_is_complete` | `AUDITABLE_EVENTS` tem 21 entradas e a lista `covered` do teste tem 12 — drift deixado pelos planos que adicionaram `channel.*` e `plugin.imported`/`deleted`. Os 9 faltantes são IDÊNTICOS no HEAD e aqui |
| `test_alembic_hygiene::test_linear_chain_single_parent_reaches_all_revisions` | a revisão de merge `0058_merge_p50_p57` tem dois pais |
| `test_alembic_hygiene::test_no_unexpected_duplicate_sequence_prefixes` | prefixos duplicados `0037/0042/0043/0046/0052` |
| `legacy_endpoints`: `descriptor whatsapp_cloud` e `cloud novo sem app_secret` | o `whatsapp_cloud` instalado (1.9.0) é anterior ao publicado (1.10.3) |

⚠️ As **células** de `test_audit_characterization` são FLAKY por ordem (o próprio
arquivo documenta a robustez contra o banco compartilhado do processo): numa
execução falharam `config_changed`/`tool_override_changed`, na seguinte
`contact_updated`/`contact_ai_toggled`/`plugin_settings_changed`, e numa execução
limpa **todas passaram**. Não persiga uma célula isolada sem repetir a corrida.

## Ordem executada

| # | Fase | Situação |
|---|---|---|
| 0 | Guardrails pré-existentes | **N/A** — ver achados 1 e 2 |
| 1 | Dados (`api_keys`, `audit_log.api_key_id`, repo) | feito — migração `0064_api_keys` |
| 2 | Lógica da chave (`server/api_keys.py`) | feito — formato corrigido (achado 3) |
| 3 | Gestão + `apikey.manage` + guardrails §4 | feito |
| 4 | Porteiro (middleware + ator + registro) | feito |
| 5 | Refactor do envio de texto (`send_text`) | feito — R-txt, + 5 resolvedores |
| 6 | Fachada `/api/v1` (4 domínios) | feito — + `openapi.json` autenticado |
| 7 | UI de chaves | feito — tela "API e Webhooks", rota `/api-keys` |
| 8 | Webhooks de saída | feito — migração `0065_outbound_webhooks` |

## Verificação

Os 12 itens da §9 do plano viraram testes:
[tests/core/test_api_keys_unit.py](../tests/core/test_api_keys_unit.py),
[tests/core/test_webhook_dispatcher_unit.py](../tests/core/test_webhook_dispatcher_unit.py),
[tests/integration/test_api_key_auth.py](../tests/integration/test_api_key_auth.py),
[tests/integration/test_v1_facade.py](../tests/integration/test_v1_facade.py),
[tests/integration/test_outbound_webhooks.py](../tests/integration/test_outbound_webhooks.py).

⚠️ O `whatsbot_test` é disputado com outra máquina e o schema some no meio da
suíte (`relation "tool_overrides" does not exist` é falha AMBIENTAL). Rode contra
`whatsbot_test_api` — ver a nota em CLAUDE.md sobre bancos de teste.

## Adendo (2026-08-20) — `PATCH` de etiqueta de conversa e de atributo

A primeira entrega deu à v1 apenas criar/listar/apagar nesses dois recursos. O
buraco não era cosmético: **recriar não é equivalente a editar**. A identidade da
etiqueta é o `id` (renomear preserva os vínculos com as conversas já etiquetadas)
e a do atributo é o `attribute_key` (os valores gravados nas entidades são
indexados por ela) — apagar-e-recriar perde os dois.

- `PATCH /api/v1/conversation-labels/{id}` — `name`/`color` parciais, cheque de
  nome duplicado (409), 404 quando ausente.
- `PATCH /api/v1/custom-attributes/{id}` — campos editáveis apenas.
  `attribute_key`/`type`/`applies_to` são IGNORADOS num atributo comum (o repo só
  aceita a allowlist) e viram **400** num `is_system`, espelhando o painel.

⚠️ **Achado 13 — o registro de etiquetas não fazia broadcast na v1.** As rotas de
criar/apagar etiqueta de conversa da fachada nunca emitiram
`conversation_labels_registry_changed`; a rota do painel emite em TODA escrita. O
efeito era silencioso: uma etiqueta criada pela API só aparecia na paleta do
operador depois de recarregar a tela. Corrigido junto (helper
`_broadcast_label_registry`, chamado em create/patch/delete) e travado por
`test_conversation_label_writes_broadcast_the_registry`, que conta os três
broadcasts. É a MESMA classe do achado 6 — extrair/reescrever uma rota e perder
os efeitos colaterais que a tela consome.

## O que ficou de fora (deliberado)

- **Escopo por chave** (D3): a coluna `api_keys.scopes` existe nullable e sem
  uso. O ponto de enxerto, se um dia voltar a fazer sentido, é `_rbac_allows` em
  `server/authz.py` — que precisaria receber o `request` (hoje `check`/`acheck`
  já o recebem; só `_rbac_allows` não).
- **Administração na v1** (D6): usuários, papéis, configuração, motor de IA,
  auditoria, plugins e **escrita** de canal continuam exclusivos do painel.
- **`/ws` para integração**: continua exclusivo de sessão. O push para integrador
  é a fase 8 (webhooks), não o socket.
- **Enviar MÍDIA pela v1**: `MessagingService.send_media` existe (R14) e a
  extração já está feita, mas a rota v1 não foi criada — a fachada envia só
  texto.
- **`users.is_service`**: o usuário dedicado da integração aparece no seletor de
  atribuição (`GET /api/channels/assignable-users`). Ruído aceito no MVP; a
  coluna seria aditiva depois.
