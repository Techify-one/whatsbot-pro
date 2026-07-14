# Correções da IA — defeitos encontrados nos testes (go-live dia 20)

Registro dos defeitos encontrados durante o roteiro de [testaria.md](testaria.md).

---

## Defeito #1 — Contato do Telegram salvo com JID de WhatsApp → contato/conversa FANTASMA no canal WhatsApp + IA responde no canal errado

- **Status:** 🔴 Aberto
- **Severidade:** ALTA (bloqueia go-live — vaza conversa entre canais e IA responde onde não devia)
- **Bloco do teste:** Multiusuário / canais (visibilidade + atribuição)

**Sintoma observado:**
Mesmo tendo mandado mensagem **só pelo Telegram**, o contato "Contato Teste"
(phone `<id_telegram>`) aparece **duplicado** na sidebar — uma entrada no canal
**Telegram** e outra no canal **WhatsApp** — como se tivesse recebido mensagem
nos dois. A IA respondeu na conversa do WhatsApp (que estava com IA ligada) ao
mesmo tempo em que a atividade acontecia no Telegram.

**Causa-raiz (confirmada no banco):**
O provider Telegram gravou o `contact_inbox.source_id` como
**`<id_telegram>@s.whatsapp.net`** — um JID no formato **WhatsApp** — em vez de uma
identidade nativa do Telegram. Como a identidade "parece" um contato WhatsApp, o
caminho compartilhado de mensagem/memória (que assume WhatsApp — ver
`agent/memory.py:167`, `suffix = "s.whatsapp.net"`, e `DEFAULT_CHANNEL_ID =
"default"`) materializou um **segundo `contact_inbox` no inbox do WhatsApp**
(id=1) para o MESMO contato, criou uma **conversa fantasma (atendimento #12)** no
canal `default` (GOWA) e, como essa conversa estava com `ai_active=1`, **a IA
respondeu ali**.

Evidência (banco):
```
contact_id=3 phone='<id_telegram>' name='~Teste'
  contact_inbox=3 inbox='Telegram'  source_id='<id_telegram>@s.whatsapp.net'  (16:48)
  contact_inbox=5 inbox='WhatsApp'  source_id='<id_telegram>@s.whatsapp.net'  (18:56) ← FANTASMA
  atendimento=3 inbox='Telegram'  ai_active=0
  atendimento=5 inbox='WhatsApp'  ai_active=1  ← IA respondeu aqui indevidamente
```
As respostas da IA no atendimento #12 (WhatsApp) saíram nos MESMOS segundos da
atividade do Telegram (18:57:24) — confirma o leak cross-channel.

**Comportamento esperado:**
- Contato que só falou pelo Telegram deve existir **apenas** no inbox do Telegram.
- O provider Telegram NÃO deve anexar `@s.whatsapp.net` — deve usar identidade
  nativa (chat_id/user_id do Telegram) como `source_id`.
- Nenhuma conversa nem resposta de IA deve ser criada no canal WhatsApp.

**Onde investigar / corrigir:**
- Provider Telegram: `storages/plugins/telegram/channels.py` (`parse_inbound`) —
  como `chat_id` vira `source_id`; algo está normalizando pra JID WhatsApp.
- Caminho de ingest/resolução do `contact_inbox` (quem escolhe o inbox e monta o
  `source_id`) — garantir que respeite o `channel_id` de origem, sem cair no
  `DEFAULT_CHANNEL_ID = "default"`.
- `agent/memory.py` — `ContactMemory` assume `@s.whatsapp.net` e canal `default`;
  precisa ser channel-aware pra não vazar pro GOWA.
- Relacionado à nota de memória "JID suffix gotcha — não anexar sufixo cego
  (causou conversas duplicadas)".

**Passos para reproduzir:**
1. Mandar mensagem SÓ pelo Telegram (contato novo).
2. Observar que aparece uma conversa duplicada também no canal WhatsApp.
3. Se a conversa do WhatsApp estiver com IA ligada, a IA responde nela.

**Correção:** ✅ Resolvido (plano 42 A1+A2).
- A forma do `source_id` virou responsabilidade do **PROVIDER**: novo classmethod
  `Channel.source_id_for(phone, is_group)` ([channels/base.py](../channels/base.py))
  com default **bare** (id nativo); `GOWAChannel` sobrescreve appendando
  `@s.whatsapp.net`/`@g.us`
  ([channels/providers/gowa_channel.py](../channels/providers/gowa_channel.py)) —
  byte-idêntico ao backfill 0013. `ContactMemory` resolve a forma pelo provider do
  seu `channel_id` (`_source_id()` em [agent/memory.py](../agent/memory.py), via o
  ChannelRegistry wired), com **fail-safe** ao sufixo WhatsApp quando o provider não
  resolve (registry não-wired/legado) — o GOWA de produção fica inalterado, zero
  regressão. Nenhum `if provider == "gowa"` no core (D3).
- Migration [0046_source_id_native](../db/alembic/versions/20260709_0046_source_id_native.py)
  re-âncora as linhas já gravadas: strip do sufixo WhatsApp em
  `contact_inboxes.source_id`/`source_jid` de inboxes de provider ≠ gowa,
  **consolidando colisões** (re-aponta `atendimentos.contact_inbox_id` p/ a linha
  canônica MIN(id) e apaga a duplicada, nessa ordem p/ não violar FK/unique). Down =
  no-op documentado.
- Testes: [tests/test_source_id_per_channel.py](../tests/test_source_id_per_channel.py)
  (GOWA→sufixo, não-GOWA→bare, fail-safe unwired→sufixo) +
  [tests/test_migration_0046_source_id.py](../tests/test_migration_0046_source_id.py)
  (strip + consolidação de colisão + GOWA intacto). Round-trip
  `upgrade→downgrade→upgrade` limpo no Postgres.

---

## PROVA DEFINITIVA do Defeito #1 (smoking gun)

Ao tentar responder na conversa fantasma #12 (logado como um atendente de
teste, membro SÓ do WhatsApp), o envio falhou com:

> **Falha ao enviar mensagem: Erro da API do WhatsApp (HTTP 400):
> Phone `<id_telegram>@s.whatsapp.net` is not on whatsapp**

Ou seja: o **id do Telegram** `<id_telegram>` foi convertido em **JID de WhatsApp**
`<id_telegram>@s.whatsapp.net` e o sistema tentou **entregar pelo GOWA/WhatsApp**.
O WhatsApp rejeitou porque o número não existe lá (é id de Telegram). Confirma
que a identidade do Telegram vazou para o pipeline WhatsApp.

Além disso, confirma que **NÃO é cache**: mesmo após limpar cache e re-login, o
atendente WhatsApp continua vendo a conversa (porque o dado fantasma existe de
verdade no inbox do WhatsApp).

**Ponto de criação (banco):** o `contact_inbox` fantasma (#5) e o atendimento #12
foram criados às 18:56:50 — mesmo instante da 1ª resposta da IA. Ou seja, o
**caminho de resposta/entrega da IA** resolveu o contato pelo canal errado
(`DEFAULT_CHANNEL_ID = "default"` / `@s.whatsapp.net` em `agent/memory.py`) e
materializou o gêmeo WhatsApp.

---

## Defeito #2 — Usuário escopado a UM canal enxerga conversa de OUTRO canal na sidebar (CONSEQUÊNCIA do #1)

- **Status:** 🟠 Confirmado como efeito do #1 (NÃO é cache — validado com re-login)
- **Severidade:** Alta se for isolamento real; média se for só o contato fantasma
- **Bloco do teste:** Multiusuário / visibilidade

**Sintoma observado:**
Um usuário membro **apenas do canal WhatsApp** (role `atendente`, sem
`conversation.read_all`) consegue **ver/abrir** a conversa do contato "Contato Teste"
que é do **Telegram** (`/conversations/3`). Ao tentar responder, aparece
**"Permissão negada"** (o gate de ESCRITA funciona), mas a **leitura vazou**.

**Hipótese principal (liga ao #1):**
Como o Defeito #1 criou um `contact_inbox` FANTASMA do contato no inbox do
WhatsApp (id=1), o contato passa a ser legitimamente "membro" do inbox do
WhatsApp aos olhos do filtro `contact_repo.list_contacts(..., inbox_ids)`. Ou
seja: o filtro de visibilidade está funcionando, mas o **dado duplicado** faz o
contato aparecer pra quem só deveria ver WhatsApp. Ao clicar, a UI abre a
conversa mais recente do contato (a do Telegram, atendimento #3), e a ESCRITA é
corretamente barrada ("Permissão negada").

**A investigar (separar cache × dado × isolamento):**
- Confirmar que, com sessão nova (sem cache) e SEM o contato fantasma, o usuário
  WhatsApp NÃO vê mais o contato do Telegram. Se sumir → era o Defeito #1.
- Se AINDA aparecer sem o fantasma → há furo de isolamento de LEITURA
  independente (o GET single-conversation deveria dar 404 por scoping —
  `server/routes/conversations.py:195-202`).
- Usuário reportou estar em **guia anônima** e suspeitar de **cache** — validar
  com hard refresh / re-login.

**Comportamento esperado:**
Usuário membro só do WhatsApp não deve ver, abrir nem carregar mensagens de
conversas de inbox do qual não é membro (nem por contato, nem por URL direta).

**Correção:** ✅ Resolvido como CONSEQUÊNCIA do #1 (plano 42 WS B). Confirmado que o
backend JÁ aplica `visible_inbox_ids` nos 4 pontos de leitura
([server/authz.py](../server/authz.py), rotas em
[server/routes/conversations.py](../server/routes/conversations.py)) — **não havia
furo de isolamento independente**. O leak era 100% o `contact_inbox` FANTASMA do #1
(agora prevenido por A1 e limpo por A2). Travado contra regressão por
[tests/test_conversation_read_isolation.py](../tests/test_conversation_read_isolation.py):
um usuário membro só do inbox A recebe **404** no GET de conversa/mensagens do inbox B
e não vê B na lista de conversas (mas VÊ a própria — controle que prova ser scoping,
não negação total).

---

## Observações de console (registro — 08/07, sessão de um atendente de teste do WhatsApp)

Erros vistos no DevTools durante o teste (para histórico):

| Endpoint | Status | Interpretação | Ação |
|----------|--------|---------------|------|
| `/api/users` | **403** (várias) | **Esperado** — `atendente` não tem permissão de gerenciar usuários; o RBAC está barrando certo. O ruído é a UI CHAMAR o endpoint mesmo sem permissão. | Cosmético. Ideal: frontend não chamar `/api/users` quando o usuário não tem a permissão (esconder a chamada, não só a tela). Baixa prioridade. |
| `/api/balance` | **502** (várias) | Gateway error ao consultar saldo (proxy Techify `/credits`). Pode ser transitório (proxy fora) OU erro real do balance_monitor. | ✅ Resolvido (plano 42 WS C). Diagnóstico C0: o proxy Techify está **UP e rápido** (~250ms) — era hiccup transitório / cache frio no boot (nenhuma LLM call ainda populou o cache). O endpoint agora **degrada p/ 200 `available:false`** em vez de 502 quando o proxy está fora e sem cache ([server/routes/config.py](../server/routes/config.py)); o cache é primado no boot (fire-and-forget, [server/balance_monitor.py](../server/balance_monitor.py) `prime_cache` + [server/app.py](../server/app.py)); o frontend tolera `available:false` sem abrir modal ([App.js](../web/static/js/components/shell/App.js)). O 400 de "sem api_key" permanece. Testes: [tests/test_balance_degradation.py](../tests/test_balance_degradation.py). |
| `/api/contacts/<id_telegram>/send` | **500** | Envio na conversa FANTASMA do WhatsApp (Defeito #1). O backend estoura 500 ao tentar mandar pro `<id_telegram>@s.whatsapp.net` (não existe no WhatsApp). | Parte do **Defeito #1**. Bônus: o erro deveria virar um 4xx tratado, não 500 cru. |

> Nota: o **500** e o **403** são consequências esperadas do cenário de teste
> (contato fantasma + atendente sem permissão de users). O **502 do /api/balance**
> é o único item novo aqui que merece investigação independente.
