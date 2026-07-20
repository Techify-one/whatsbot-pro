# Plano 66 — Botão "Atribuir a mim" do header deve desligar a IA (e desatribuir religar)

## Problema

No header da conversa aberta, o botão **"Atribuir a mim"** atribui a conversa ao
operador mas **não desliga a IA** — a IA continua respondendo por cima do humano.

Já o mesmo gesto pela **lista de conversas** (clique com o botão direito →
"atribuir para mim/outros") atribui **E** desliga a IA. Comportamento inconsistente.

O usuário quer: clicar em "Atribuir a mim" no header deve **tirar a IA da conversa
E atribuir a conversa a mim**. E o inverso — remover a atribuição ("Atribuída a
você") deve **religar a IA**.

## Causa raiz

Os dois fluxos chamam endpoints diferentes:

| Fluxo | Chamada frontend | Endpoint | Efeito na IA |
|---|---|---|---|
| **Header** "Atribuir a mim" | `assignMeConversation(conv.id)` | `POST /assign-me` → `conversation_service.assign_me` | **Nenhum** — só `set_assignee` |
| **Header** "Atribuída a você" (remover) | `assignConversation(conv.id, null)` | `POST /assign` → `conversation_service.assign` | **Nenhum** — só `set_assignee` |
| **Lista** (right-click) "atribuir p/ mim" | `assignAgent(conv.id, {kind:'user', userId})` | `POST /assign-agent` → `assign_unified` → `_transfer` | Desliga: `ai_active=0` + contato `ai_enabled=0` |

- Header render/handlers: [ConversationHeaderActions.js:196-213](../web/static/js/components/contacts/ConversationHeaderActions.js#L196-L213)
  (linha 199 = remover; linha 205 = assumir).
- `assign_me` / `assign` só chamam `set_assignee`, nunca tocam a IA:
  [conversation_service.py:377-428](../app/services/conversation_service.py#L377-L428).
- `assign_unified` (kind `user`) passa `ai_active=0, mirror_contact_ai=False`:
  [conversation_service.py:445-448](../app/services/conversation_service.py#L445-L448).

## Decisão de abordagem

O serviço **`set_ai`** (rota `POST /api/atendimentos/{id}/ai`) já implementa
EXATAMENTE a política desejada, de forma atômica via `_transfer`
([conversation_service.py:498-525](../app/services/conversation_service.py#L498-L525)):

- **OFF** → desliga a IA, limpa o agente ativo, e **atribui a conversa a quem
  desligou** (`actor_id`; sem identidade de operador cai em não-atribuída).
- **ON** → religa a IA, re-vincula o agente padrão da inbox, limpa o assignee humano
  e remove a tag `transferido_atendente`.

Portanto: **os dois botões do header passam a chamar o toggle de IA da conversa**,
que já é a fonte única de verdade da transição de posse (plano 17). Não precisa de
endpoint novo nem de duas chamadas encadeadas.

Frontend já tem a função: `setConversationAi(id, active)` →
`POST /api/atendimentos/${id}/ai` ([api.js:560-562](../web/static/js/services/api.js#L560-L562)).

### Alternativa descartada

Manter `/assign-me` e disparar um segundo `setConversationAi(false)` em sequência —
rejeitada: dois round-trips, risco de dessincronizar, e `set_ai(OFF)` já atribui ao
actor sozinho (a 2ª chamada seria redundante).

## Mudanças

### 1. Frontend — [ConversationHeaderActions.js](../web/static/js/components/contacts/ConversationHeaderActions.js)

- **"Atribuir a mim"** (onClick linha 205): trocar
  `assignMeConversation(conv.id)` → `setConversationAi(conv.id, false)`.
  Resultado: IA OFF + conversa atribuída ao operador logado.
- **"Atribuída a você"** (onClick linha 199): trocar
  `assignConversation(conv.id, null)` → `setConversationAi(conv.id, true)`.
  Resultado: IA ON + assignee limpo + agente padrão re-vinculado + tag de
  transferência removida.
- Ajustar imports (remover `assignMeConversation`/`assignConversation` se não usados
  em mais nada no arquivo; adicionar `setConversationAi`).
- Atualizar o patch de estado local que o `run(...)` aplica, espelhando o retorno de
  `set_ai` (`ai_active`, `assignee_user_id`, `active_agent_key`) para o header e o
  badge "IA OFF"/"IA" refletirem na hora, sem esperar o WS.
- Rótulos/`title` dos botões: reavaliar o texto — "Atribuir a mim" agora significa
  "assumir e desligar IA"; "Atribuída a você" significa "devolver à IA". Manter os
  rótulos atuais (o usuário já os entende assim) mas ajustar os `title` para deixar
  claro o efeito na IA.

### 2. Backend

Nenhuma mudança de lógica. Endpoint `/ai` já existe e faz tudo
([conversations.py:516-535](../server/routes/conversations.py#L516-L535)).

**Ponto de atenção — permissão:** o botão do header é exibido sob
`can('conversation.assign')` ([ConversationHeaderActions.js:196](../web/static/js/components/contacts/ConversationHeaderActions.js#L196)),
mas a rota `/ai` exige `conversation.reply`
([conversations.py:518](../server/routes/conversations.py#L518)). Um operador com
`assign` mas sem `reply` veria o botão e tomaria 403. Decidir:
(a) trocar o gate de visibilidade do botão para `can('conversation.reply')` (ou
exigir ambas), ou (b) aceitar que assumir = poder responder (o mais comum). **Sugestão:**
exibir o botão quando `can('conversation.reply')` (quem assume vai responder).

### 3. Testes — `tests/`

- Assumir pelo header (`POST /ai {active:0}`): valida `ai_active=0` na conversa,
  `assignee_user_id == actor`, agente ativo limpo.
- Remover atribuição (`POST /ai {active:1}`): valida `ai_active=1`, `assignee_user_id
  == null`, agente padrão re-vinculado, tag `transferido_atendente` removida.
- (O caminho já é coberto em parte pelos testes de `/ai`; adicionar as asserções de
  assignee que faltam.)

## Riscos / observações

- Assumir agora desliga a IA também no **nível da conversa** (não do contato) — é a
  política do `set_ai`, ligeiramente diferente do right-click da lista, que desliga
  no contato também (`mirror_contact_ai=False`). Se quisermos paridade exata com a
  lista (desligar `ai_enabled` do contato), usar `/assign-agent` em vez de `/ai`.
  **Avaliar com o usuário** — para o caso relatado, o toggle por conversa resolve o
  sintoma (IA parar de responder aquela conversa). *(Recomendação: `/ai`, mais
  simples; se a IA voltar por outro gatilho de contato, revisitar.)*
- Religar via `set_ai(ON)` re-vincula o **agente padrão**, não o agente anterior
  (que foi limpo ao desligar). Comportamento esperado.
- Verificar se `set_ai(OFF)` sem identidade de operador (modo aberto sem RBAC) deixa
  a conversa não-atribuída em vez de "atribuída a mim" — nesse modo não há `actor_id`.
  Aceitável (é o comportamento documentado do serviço).

## Arquivos tocados

- `web/static/js/components/contacts/ConversationHeaderActions.js` (handlers + imports + titles + gate de permissão)
- `tests/` (asserções de assignee no toggle `/ai`)
- Backend: **nenhum** (reuso de `/ai` + `set_ai`)
