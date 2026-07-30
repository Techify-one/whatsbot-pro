# Plano 93 — Atribuir a conversa JÁ atribui o protocolo (um clique, quatro superfícies)

> **Status:** PLANEJAMENTO · **Data:** 2026-07-30 · **Escopo:** pequeno/médio
> **Origem:** pedido do usuário ("Mudar botão de atribuir conversa para já atribuir atendimento e protocolo juntos com um clique"). **Método:** leitura do core (`app/services/conversation_service.py`, rotas e as 4 superfícies de atribuição do frontend) + do plugin instalado `storages/plugins/protocolos/` (1.21.0), com `arquivo:linha` verificado.
> Hoje o atendente atribui a **conversa** (menu de contexto da lista, botão do cabeçalho, painel do atendimento ou seleção múltipla) e, depois, precisa **abrir o protocolo e preencher o Atendente na mão** — a coluna ATENDENTE do Kanban/lista de protocolos só é escrita ao **resolver** ou ao **arrastar o card**. O plano fecha esse buraco reagindo ao evento de barramento `conversation.assigned` dentro do plugin `protocolos`, sem tocar em nenhuma das 4 superfícies de UI e sem `if provider ==`/`if plugin ==` no core.

---

## 0. Decisões do usuário / travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---|---|
| D1 ✅ | Atribuir a conversa deve atribuir **também o protocolo**, num clique | Nenhuma UI nova: a mudança é um **event handler** no plugin, reagindo à atribuição que já acontece |
| D2 ✅ | Vale para o **botão direito na lista de conversas** E para o **botão do cabeçalho da conversa aberta** | As duas superfícies (e mais duas que o usuário não citou) convergem no MESMO evento — ver §2 |
| D3 ✅ | O vínculo é "atendimento ↔ protocolo": quem assumiu a conversa vira o dono do protocolo | Escrita em `plugin_protocolos_protocolos.assignee_user_id/assignee_name` + no **ciclo aberto** daquela conversa |
| D4 ✅ (deste plano) | Zero alteração no core | O core já emite `conversation.assigned` com `assignee_user_id`; o plugin só passa a **assinar** o evento |

---

## 1. Resumo executivo

As **quatro** superfícies de atribuição do painel (menu de contexto, cabeçalho do chat, painel do atendimento e seleção múltipla) terminam em `app/services/conversation_service.py`, que emite **sempre** o evento de barramento `conversation.assigned` com o `assignee_user_id` resultante. O plugin `protocolos` **não assina esse evento** hoje ([events.py](../storages/plugins/protocolos/events.py) tem só 4 handlers: `message.saved`, `message.sent`, `conversation.deleted`, `app.startup`) — por isso o Atendente do protocolo fica vazio até alguém **resolver** o atendimento (popup) ou **arrastar** o card no Kanban por atendente.

A solução é **um handler novo** (`logic.on_conversation_assigned`) que, ao receber `conversation.assigned`, resolve o protocolo ABERTO daquele contato e grava o atendente — no protocolo e no ciclo aberto da conversa — **sem propagar de volta** para as outras conversas do contato (senão assumir UMA conversa roubaria todas as outras) e **sem** reemitir nada que volte ao mesmo handler.

---

## 2. Como funciona hoje (mapa)

### 2.1 As quatro superfícies convergem num único evento

| # | Superfície | Componente | Chamada | Rota | Serviço | Evento emitido |
|---|---|---|---|---|---|---|
| S1 | Botão direito na lista de conversas | [ContextMenu.js:75](../web/static/js/components/contacts/ContextMenu.js#L75) `pickAssign` | `assignAgent(id, {kind})` | `POST /api/atendimentos/{id}/assign-agent` ([conversations.py:516](../server/routes/conversations.py#L516)) | `assign_unified` ([conversation_service.py:456](../app/services/conversation_service.py#L456)) | `conversation.assigned` ([:484](../app/services/conversation_service.py#L484)) |
| S2 | Botão "Atribuir a mim" do cabeçalho | [ConversationHeaderActions.js:205-219](../web/static/js/components/contacts/ConversationHeaderActions.js#L205-L219) | `setConversationAi(id, false)` | `POST /api/atendimentos/{id}/ai` ([conversations.py:561](../server/routes/conversations.py#L561)) | `set_ai` ([conversation_service.py:523](../app/services/conversation_service.py#L523)) | `conversation.assigned` ([:557](../app/services/conversation_service.py#L557)) |
| S3 | Painel do atendimento ("Agente atribuído" / "→ Atribuir a mim") | [AssigneePicker.js:76-90](../web/static/js/components/contacts/AssigneePicker.js#L76-L90) | `assignAgent` | `/assign-agent` | `assign_unified` | `conversation.assigned` |
| S4 | Seleção múltipla na sidebar | [useBulkSelection.js:105](../web/static/js/components/contacts/hooks/useBulkSelection.js#L105) `handleBulkAssign` | `assignAgent` (N chamadas) | `/assign-agent` | `assign_unified` | `conversation.assigned` (N×) |
| — | (compat) `POST /assign` direto | — | — | [conversations.py:463](../server/routes/conversations.py#L463) | `assign` ([:402](../app/services/conversation_service.py#L402)) | `conversation.assigned` **ou** `conversation.unassigned` ([:428](../app/services/conversation_service.py#L428)) |

⚠️ **Este é o achado que torna o plano pequeno:** não existem quatro correções — existe **uma**. Toda atribuição, venha de onde vier, passa por `conversation_service` e emite `conversation.assigned` com o payload de [`_broadcast`](../app/services/conversation_service.py#L84-L96):

```python
{"conversation_id", "display_id", "contact_id", "status",
 "assignee_user_id", "active_agent_key", "ai_active", "is_archived", "inbox_id", "ts"}
```

O evento está registrado no catálogo do barramento em [plugins/events.py:95](../plugins/events.py#L95).

### 2.2 O lado do protocolo

| Item | Onde | Estado |
|---|---|---|
| Coluna ATENDENTE do protocolo | `plugin_protocolos_protocolos.assignee_user_id` + `assignee_name` ([001_initial.sql](../storages/plugins/protocolos/migrations/001_initial.sql)) | Só escrita ao **resolver** ou ao **arrastar** o card |
| Atendente do ciclo (histórico) | `plugin_protocolos_atendimentos.assignee_user_id` (migration [005](../storages/plugins/protocolos/migrations/005_atendimento_assignee.sql)) + `assignee_name` | Só escrita em `resolve_atendimento` ([logic.py:2546-2554](../storages/plugins/protocolos/logic.py#L2546)) |
| Escrita direta do atendente | `logic.assign_protocolo` ([logic.py:1452](../storages/plugins/protocolos/logic.py#L1452)) | Existe — usada pelo `POST /protocolos/{id}/assign` ([routes.py:403](../storages/plugins/protocolos/routes.py#L403)), do drag-and-drop |
| Protocolo → conversas | `_propagate_assignee_to_conversations` ([logic.py:1425](../storages/plugins/protocolos/logic.py#L1425)) | **Já existe** — o caminho INVERSO ao deste plano |
| Handlers assinados | [events.py](../storages/plugins/protocolos/events.py) | `message.saved`, `message.sent`, `conversation.deleted`, `app.startup` — **nenhum de atribuição** |
| Seed do popup de resolução | [extends.js:204-215](../storages/plugins/protocolos/static/extends.js#L204-L215) | Já pré-preenche o rótulo "Atendente (nativo)" com o assignee ATUAL da conversa |

⚠️ **Gotcha que define a implementação:** `assign_protocolo` propaga por padrão para **todas** as conversas do protocolo (`propagate_to_conversations=True`, [logic.py:1473](../storages/plugins/protocolos/logic.py#L1473)). Chamar isso do handler faria "assumir a conversa X" **reatribuir silenciosamente todas as outras conversas** daquele contato. O handler **precisa** passar `propagate_to_conversations=False`.

⚠️ **Gotcha (bom) sobre loop:** `_propagate_assignee_to_conversations` usa `conversation_repo.set_assignee` + `plugins.context.broadcast`, que é **somente WebSocket** ([plugins/context.py:146-157](../plugins/context.py#L146-L157)) — **não** emite no barramento. Ou seja: o drag-and-drop do Kanban **não** dispara `conversation.assigned` e portanto **não** realimenta o handler novo. Sem risco de loop, sem necessidade de flag de reentrância.

---

## 3. Inventário do que fazer

| # | Item | Arquivo | O que falta | Risco | Esforço |
|---|---|---|---|---|---|
| I1 | Handler `on_conversation_assigned` | `storages/plugins/protocolos/logic.py` (novo, junto a [on_inbound:2599](../storages/plugins/protocolos/logic.py#L2599)) | Resolver contato → protocolo aberto → gravar atendente no protocolo **e** no ciclo aberto da conversa | Médio | M |
| I2 | Registrar o handler | [events.py](../storages/plugins/protocolos/events.py) | `"conversation.assigned": logic.on_conversation_assigned` (+ `conversation.unassigned` se P2 = sim) | Baixo | S |
| I3 | Setting de toggle | [settings.py](../storages/plugins/protocolos/settings.py) | `assign_follows_conversation: bool = True` (padrão LIGADO), no mesmo padrão de `auto_link` | Baixo | S |
| I4 | Escrita do ciclo aberto | `logic.py` (nova função `set_cycle_assignee`) | `UPDATE plugin_protocolos_atendimentos SET assignee_user_id/assignee_name WHERE id = <ciclo aberto>` | Baixo | S |
| I5 | Bump de versão + zip | [plugin.yaml](../storages/plugins/protocolos/plugin.yaml) `1.21.0` → `1.22.0` | Descrição + regravar o `.zip` no repo de plugins do Pro | Baixo | S |
| I6 | Testes | `tests/test_protocolos_assign_follows.py` (novo) | Cobrir as 4 superfícies via evento + os no-ops (IA, sem protocolo, toggle off) | Baixo | M |

### 3.1 Falsos positivos descartados

| "Problema" aparente | Por que NÃO é |
|---|---|
| "Precisa mudar os 4 componentes de UI" | Não — as 4 superfícies já convergem em `conversation_service` e emitem o MESMO evento (§2.1). Mexer na UI multiplicaria o bug por 4 |
| "Precisa de rota nova no core" | Não — `conversation.assigned` já existe e já carrega `assignee_user_id` + `contact_id` |
| "Vai dar loop protocolo → conversa → protocolo" | Não — a propagação do plugin não emite no barramento (§2.2), e o handler chama `assign_protocolo(..., propagate_to_conversations=False)` |
| "O popup de resolver precisa mudar para pré-preencher" | Não — [extends.js:204-215](../storages/plugins/protocolos/static/extends.js#L204-L215) já semeia o rótulo Atendente com o assignee da conversa |
| "Precisa de migration" | Não — as colunas `assignee_user_id`/`assignee_name` já existem nas duas tabelas (migrations 001 e 005) |
| "Precisa checar RBAC `plugin.protocolos.assign` no handler" | Não — o handler reage a uma ação **já autorizada** pelo core (`conversation.assign`). Gatear de novo faria a atribuição da conversa passar e a do protocolo falhar em silêncio, criando divergência |

---

## 4. Regra de negócio (o miolo do handler)

Um protocolo agrupa **muitas** conversas de **um** contato (inclusive de canais diferentes). Logo, o handler precisa de uma política explícita:

| Situação no evento | Ação no protocolo | Ação no ciclo aberto |
|---|---|---|
| `assignee_user_id` = **id de usuário** | Grava esse usuário como atendente (**última atribuição vence**) | Grava o mesmo usuário no ciclo aberto **daquela** conversa |
| `assignee_user_id` = `None` **e** `active_agent_key` preenchido (foi pra IA) | **Nada** (a IA não é "atendente" do protocolo) | Nada |
| `assignee_user_id` = `None` **e** sem agente (desatribuição) | Ver **P2** — recomendação: **nada** (o protocolo preserva o último dono) | Nada |
| Contato sem protocolo aberto | **Nada** — nunca ABRIR protocolo por atribuição (abrir é papel do `auto_link` em `message.saved`) | — |
| Contato é grupo | **Nada** (protocolos são 1:1, mesma regra de `_resolve_target` [logic.py:2578](../storages/plugins/protocolos/logic.py#L2578)) | — |
| Já está com esse mesmo atendente | **No-op** (não regrava, não faz broadcast — evita ruído no Kanban) | No-op |
| Setting `assign_follows_conversation` = off | **Nada** | Nada |

Pseudo-código verificado contra as funções existentes:

```python
def on_conversation_assigned(ctx, payload: dict) -> None:
    """conversation.assigned → espelha o atendente da conversa no protocolo aberto."""
    if not config_repo.get(f"plugin.{PLUGIN_ID}.assign_follows_conversation", True):
        return
    uid = payload.get("assignee_user_id")
    if uid is None:
        return                                   # IA ou desatribuição → P2
    contact_id = payload.get("contact_id")
    conversation_id = payload.get("conversation_id")
    if contact_id is None:
        return
    at = get_open_protocolo_for_contact(contact_id)      # logic.py:588 — NÃO cria
    if not at or at.get("assignee_user_id") == uid:
        return                                   # sem protocolo aberto, ou já é o dono
    u = user_repo.get(int(uid)) or {}
    name = str(u.get("name") or u.get("email") or "")
    assign_protocolo(at["id"], uid, assignee_name=name,
                     propagate_to_conversations=False)   # logic.py:1452 — ⚠️ False
    if conversation_id is not None:
        set_cycle_assignee(conversation_id, at["id"], uid, name)  # I4
```

`assign_protocolo` já faz `_broadcast_changed` ([logic.py:3693](../storages/plugins/protocolos/logic.py#L3693)) → o Kanban/lista atualizam ao vivo sem trabalho extra.

**Defensividade obrigatória** (padrão do plugin): o corpo inteiro vai dentro de `try/except Exception` com `logger.warning(..., exc_info=True)` — um protocolo que falhe **nunca** pode derrubar a atribuição da conversa, que já foi persistida pelo core antes do evento.

---

## 5. Fases

```
WAVE 0   F1(handler+regra) ─────────────────────────  🔴 sozinha (é o miolo)
              │ (barreira: F2 e F3 dependem da assinatura de F1)
WAVE 1   F2(registro+setting) · F3(testes)             🟢 juntas
              │
WAVE 2   F4(bump + zip + deploy)                       🔴 sozinha (release)
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | F1 | `logic.py`: `on_conversation_assigned` + `set_cycle_assignee` | 🔴 | Médio | As duas funções existem, com `propagate_to_conversations=False` e `try/except` |
| 1 | F2 | `events.py` + `settings.py` | 🟢 [depende de: F1] | Baixo | Handler registrado e toggle aparece em Configurar |
| 1 | F3 | `tests/test_protocolos_assign_follows.py` | 🟢 [depende de: F1] | Baixo | Verde na suíte contra o Postgres de teste |
| 2 | F4 | `plugin.yaml` 1.22.0 + `.zip` no repo de plugins do Pro + import em produção | 🔴 [depende de: F2, F3] | Baixo | Card mostra 1.22.0 e o comportamento vale em produção |

### F1 — Handler e regra (🔴)

1. Em `logic.py`, ao lado dos outros handlers (após `on_conversation_deleted`, [logic.py:2652](../storages/plugins/protocolos/logic.py#L2652)), criar `on_conversation_assigned` conforme §4.
2. Criar `set_cycle_assignee(conversation_id, protocolo_id, uid, name)`: busca o ciclo aberto com `get_open_cycle` ([logic.py:2446](../storages/plugins/protocolos/logic.py#L2446)); se não houver, **não cria** (abrir ciclo é papel do `message.saved`); `UPDATE plugin_protocolos_atendimentos SET assignee_user_id=:uid, assignee_name=:name, updated_at=:ts WHERE id=:id`.
3. Bind params **nomeados** (`:uid`) via `sqlalchemy.text()` — nunca `%s` (convenção do repo).

### F2 — Registro e toggle (🟢)

1. `events.py`: `"conversation.assigned": logic.on_conversation_assigned` (e `"conversation.unassigned"` só se P2 = limpar).
2. `settings.py`: `assign_follows_conversation: bool = Field(default=True, title="Atribuir a conversa também atribui o protocolo", description="Ao assumir/transferir um atendimento, o mesmo atendente vira o dono do protocolo aberto daquele contato. Desligue para manter o comportamento antigo (preencher na mão ao resolver).")`.

### F3 — Testes (🟢)

| Teste | Cenário | Esperado |
|---|---|---|
| `test_assign_agent_sets_protocolo_assignee` | `POST /assign-agent {kind:'user'}` com protocolo aberto | `assignee_user_id` do protocolo == usuário; ciclo aberto idem |
| `test_assign_via_ai_toggle_sets_assignee` | `POST /{id}/ai {active:false}` (o botão do cabeçalho, S2) | Mesma escrita |
| `test_assign_to_ai_does_not_touch_protocolo` | `{kind:'ai'}` | Protocolo intacto |
| `test_assign_does_not_steal_sibling_conversations` | Contato com 2 conversas; atribui só a 1 | A outra conversa mantém o `assignee_user_id` original (prova do `propagate=False`) |
| `test_assign_without_open_protocolo_is_noop` | Contato sem protocolo aberto | Nenhuma linha criada |
| `test_toggle_off_disables_mirror` | Setting off | Protocolo intacto |
| `test_bulk_assign_sets_all_protocolos` | S4 com 3 conversas de 3 contatos | 3 protocolos atualizados |

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| `assign_protocolo` propaga por padrão | Assumir 1 conversa reatribui TODAS as outras do contato | `propagate_to_conversations=False` — travado por `test_assign_does_not_steal_sibling_conversations` |
| Contato com várias conversas em canais diferentes | "Quem é o dono do protocolo?" fica ambíguo | Política explícita **última atribuição vence** (§4), documentada no card e no toggle |
| Handler lento no barramento | Atribuir a conversa ficar lento | O handler é sync e roda em `asyncio.to_thread` (fire-and-forget, [plugins/events.py:412](../plugins/events.py#L412)) — não bloqueia a rota |
| Exceção no handler | Atribuição "meio feita" | `try/except` amplo + `logger.warning`; a conversa **já** está persistida quando o evento sai |
| Seleção múltipla (S4) | N eventos ⇒ N updates no mesmo protocolo se as conversas forem do mesmo contato | O guard `at["assignee_user_id"] == uid` transforma as repetições em no-op |
| Auditoria | Ação de plugin sem trilha | Opcional: `audit("protocolos", "protocolo.assign", resource_type="channel"?)` — **não** neste plano; a ação de origem (`conversation.assigned`) já é auditada pelo core |
| Deploy | Produção roda o `.zip` importado, não `assets/` nem `storages/` do dev | Bump de versão + regravar o `.zip` no repo de plugins do Pro + `Importar (.zip)` na instância (ver memória "Plugin changes via zip") |
| Divergência de cópias | O mesmo plugin existe em 4 lugares com o mesmo número de versão | Comparar **conteúdo**, nunca só o número (memória "Onde vive o código de um plugin") |

---

## 7. Perguntas em aberto

**P1 — O ciclo (`plugin_protocolos_atendimentos`) deve receber o atendente também, ou só o protocolo?**
Contexto: o ciclo é por-conversa e alimenta a coluna ATENDENTE do histórico; hoje só é escrito ao resolver ([logic.py:2546](../storages/plugins/protocolos/logic.py#L2546)).
(a) Só o protocolo — mais simples, mas o histórico continua mostrando "—" até resolver.
(b) Protocolo **e** ciclo aberto — coerente com "vincular atendimento e protocolo".
**Recomendação: (b).** ⏸️ AGUARDANDO CONFIRMAÇÃO.

**P2 — Desatribuir a conversa deve limpar o atendente do protocolo?**
(a) Não limpar — o protocolo preserva o último dono (evita protocolo órfão no Kanban por atendente).
(b) Limpar — espelho perfeito da conversa.
**Recomendação: (a)**, e não assinar `conversation.unassigned`. ⏸️ AGUARDANDO CONFIRMAÇÃO.

**P3 — Transferir para OUTRO atendente (não "a mim") também troca o dono do protocolo?**
O evento é o mesmo, então por construção sim. **Recomendação: sim** (é o comportamento esperado de uma transferência). ⏸️ AGUARDANDO CONFIRMAÇÃO.

**P4 — Ação retroativa?**
Protocolos abertos hoje continuam sem atendente até a próxima atribuição. Um backfill (copiar `conversations.assignee_user_id` → protocolo aberto) é 1 UPDATE, mas escolhe arbitrariamente entre conversas concorrentes.
**Recomendação: não fazer backfill** — o comportamento se auto-corrige no próximo toque. ⏸️ AGUARDANDO CONFIRMAÇÃO.

---

## 8. Apêndice — arquivos-chave

**Plugin (único lugar que muda):**
- `storages/plugins/protocolos/logic.py` — `on_conversation_assigned` (novo), `set_cycle_assignee` (novo)
- `storages/plugins/protocolos/events.py` — registro do handler
- `storages/plugins/protocolos/settings.py` — `assign_follows_conversation`
- `storages/plugins/protocolos/plugin.yaml` — bump 1.21.0 → 1.22.0

**Core (só leitura — referência, NÃO muda):**
- `app/services/conversation_service.py:402,442,456,523` — os 4 caminhos de atribuição
- `app/services/conversation_service.py:84-97` — payload do evento
- `server/routes/conversations.py:463,484,516,561` — as rotas
- `plugins/events.py:95` — `conversation.assigned` no catálogo
- `plugins/context.py:146` — `broadcast` é WS-only (por que não há loop)

**Frontend (só leitura — referência, NÃO muda):**
- `web/static/js/components/contacts/ContextMenu.js:75`
- `web/static/js/components/contacts/ConversationHeaderActions.js:205-219`
- `web/static/js/components/contacts/AssigneePicker.js:76-90`
- `web/static/js/components/contacts/hooks/useBulkSelection.js:105`

**Testes:**
- `tests/test_protocolos_assign_follows.py` (novo)
