# Plano 37 — Correção da classe de bugs "keyed by contact em vez de canal" (multicanal): roteamento/estado por CANAL, não por contato

> **Status:** PLANEJAMENTO · **Data:** 2026-07-07 · **Escopo:** grande (≈18 defeitos confirmados em 4 clusters; backend + frontend; 1 decisão de modelo de dados). **Sem migration obrigatória** (a de Cluster D é opcional, decisão P1).
> **Origem:** bug relatado pelo usuário — mensagem privada / "IA lê" enviada numa conversa de **Telegram** teve a resposta da IA arquivada numa conversa **nova de WhatsApp** (#41) para o mesmo número. Investigação nesta sessão (auditoria multi-agente: 6 auditores em paralelo + 32 verificações adversariais, 2.8M tokens; 27 CONFIRMED / 4 PLAUSIBLE / 1 REJECTED) + varredura `grep` própria confirmaram que **não é um bug isolado, é uma classe** — só foi corrigida antes ponto-a-ponto, nunca de forma sistemática.
> **Método:** auditoria multi-agente + leitura dos arquivos reais + `grep` exaustivo dos resolvers channel-blind. Todo `arquivo:linha` abaixo foi **verificado nesta sessão**.
> **O quê/por quê:** o `contact` é **compartilhado entre canais** (tabela `contacts` keyed por `phone`, **sem** `channel_id`), mas dezenas de call sites resolvem a conversa/estado **só pelo contato** (`conversation_repo.get_open_for_contact` / `get_latest_for_contact`) ou **largam o `channel_id`** (caindo no default `"default"` = GOWA/WhatsApp) — mesmo tendo em mãos um `ContactMemory` já escopado no canal (com `inbox_id`). Resultado: uma ação num canal escreve/roteia na conversa de **outro** canal do mesmo número.
>
> **Como usar este plano:** ao executar cada fase, preencha o bloco **"Status de execução"** dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro. **Verde a cada fase.** **Caracterização (F0) ANTES** de mexer nos fluxos. **Um refactor por commit.** As waves marcam o que pode rodar em paralelo (🟢) e o que é sequencial/bloqueante (🔴). Decisões P1 (Cluster D) e P2 (toggle-ai) precisam ser resolvidas antes das fases que dependem delas.

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| **D1** ✅ (2026-07-07) | **Uma conversa é por-canal**; o `inbox_id` (derivado do `channel_id`) é a chave de escopo. O `contact`/`phone` é compartilhado entre canais **por design** (plano 11 D1/D2). | Toda resolução de conversa/estado num caminho sensível a canal deve usar `inbox_id`. As variantes `*_for_contact_inbox` **já existem** ([conversation_repo.py:192,209](../db/repositories/conversation_repo.py#L192)) — o fix é aplicá-las, não criá-las. |
| **D2** ✅ (2026-07-07) | **Fix aditivo e best-effort**: preservar o **fail-open** dos gates (um erro de resolução ou "sem conversa neste inbox" **nunca** silencia a IA — hoje `_conversation_ai_active` default `True`). Nenhuma mudança pode derrubar o turno. | Todo swap `get_open_for_contact(id)` → `get_open_for_contact_inbox(id, inbox_id)` mantém o mesmo tratamento de `None`. `inbox_id` sempre existe no `ContactMemory` ([memory.py:97](../agent/memory.py#L97)), então não há caminho sem valor. |
| **D3** ✅ (2026-07-07) | **Nada em produção depende do comportamento errado** ⇒ refactor direto, sem stopgap/flag. `name`/`agent_key`/tool name é identidade — **não renomear**. | Sem camada de compat. Os call sites passam o `channel_id`/`inbox_id` que já têm em mãos. |
| **D4** ✅ (2026-07-07) | Distinção de gravidade por **efeito**: sites nas **tools da IA e no motor** (Cluster A parcial) causam **outbound errado** (agente/handoff/atributo no canal errado) → alta prioridade; sites de **card painel-only** (error card, takeover, tag notice) são **cosméticos** (misfiling do card) → baixa; `TRANSFER_TAG` (Cluster D) é **modelo de dados** → decisão à parte. | A ordem das waves prioriza o outbound. Cards cosméticos podem ir junto (mesmo idioma de fix) mas não bloqueiam nada. |
| **Princípio fixo** | O padrão **já foi corrigido em alguns pontos e regrediu em outros** (ex.: o comentário em [messaging_service.py:472-478](../app/services/messaging_service.py#L472-L478) documenta a lição exata para o card `tool_call`, mas os broadcasts irmãos logo abaixo seguem channel-blind). O objetivo é aplicá-lo **sistematicamente** para não regredir de novo. | Fase de regressão (F-REG) trava o comportamento novo com testes multicanal; o checklist exige `grep` final de `get_open_for_contact(`/`get_latest_for_contact(` sem escopo nos caminhos sensíveis. |

---

## 1. Resumo executivo

O mesmo número pode existir em vários canais (ex.: um canal Telegram **e** o canal GOWA/WhatsApp `default`), cada um com sua conversa (`atendimentos.inbox_id`). O `contact` é único. Três formas do mesmo bug se repetem:

1. **Resolução channel-blind** — funções que pegam a "conversa aberta do contato" com `get_open_for_contact(contact_id)` / `get_latest_for_contact(contact_id)`, ignorando o `inbox_id` que o `ContactMemory` já carrega. Retorna a conversa **mais recente de QUALQUER canal** → tools de IA, motor e cards agem no canal errado. (Cluster A)
2. **`channel_id` largado no call site** — route handlers que chamam `aprocess_message` / `save_assistant_message` / `_get_contact` / `broadcast_tool_calls` **sem** `channel_id` → default `"default"` (WhatsApp). (Cluster B — é a causa direta da conversa #41 relatada.)
3. **Frontend omite `channel_id`** — o painel manda `conversation_id` mas não `channel_id` em alguns endpoints; ao **iniciar uma conversa nova** num canal não-default (sem `conversation_id` ainda), o backend `_channel_for` cai em `"default"`. (Cluster C)

Além disso, a trava de humano `TRANSFER_TAG` é gravada **no contato** (não na conversa), então transferir num canal silencia a IA no outro (Cluster D — modelo de dados).

Os consertos são majoritariamente **mecânicos e de baixo risco**: passar o `inbox_id`/`channel_id` que **já está disponível** e usar helpers que **já existem**. O único ponto de modelagem é o Cluster D.

---

## 2. Como funciona hoje (mapa) — `arquivo:linha` verificado

### 2.1 O modelo (por que o bug existe)
- `contacts` é keyed por `phone` UNIQUE, **sem** `channel_id` ([db/tables.py](../db/tables.py)) — 1 row de contato compartilhada entre canais.
- Uma conversa (`atendimentos`, alias `conversations`) tem `inbox_id`; **1 inbox por canal**. Índice único parcial `uq_atend_open_contact_inbox` garante **1 conversa aberta por (contato, inbox)**.
- `ContactMemory` é **escopado no canal**: `self.channel_id` e `self.inbox_id = resolve_inbox_id(channel_id)` no `__init__` ([memory.py:96-97](../agent/memory.py#L96)). `_get_contact(phone, *, channel_id="default")` ([handler.py:234](../agent/handler.py#L234)) constrói o `ContactMemory` daquele canal. **O `inbox_id` correto está sempre em mãos** onde há um `ContactMemory`/`ctx.contact`.

### 2.2 A infra correta que JÁ EXISTE (o fix reusa, não inventa)
| Helper/param | Onde | Status |
|---|---|---|
| `get_open_for_contact_inbox(contact_id, inbox_id)` · `get_latest_for_contact_inbox(...)` | [conversation_repo.py:192,209](../db/repositories/conversation_repo.py#L192) | ✅ existem, prontos |
| `broadcast_tool_calls(..., channel_id=…)` | [messaging_service.py:448](../app/services/messaging_service.py#L448) — o path inbound **já passa** `channel_id` ([:844](../app/services/messaging_service.py#L844),[:1009](../app/services/messaging_service.py#L1009)) | ✅ aceita o kwarg |
| `emit_for_contact(..., inbox_id=…)` → `resolve_conversation_for_contact(cid, inbox_id)` (troca p/ variantes `*_inbox` quando informado) | [system_notices.py:378-418](../server/system_notices.py#L378) | ✅ aceita e encaminha |
| `_scopeFields(conversationId, channelId)` (monta `conversation_id`+`channel_id` no body) | [api.js](../web/static/js/services/api.js) — usado por `sendText`/`sendDocument`/`sendPrivateAudio` | ✅ padrão pronto |
| `_channel_for(phone, conversation_id, channel_id)` (resolve canal; fallback `"default"`) | [contacts.py:130-146](../server/routes/contacts.py#L130) | ✅ já aceita `channel_id` como 3º arg |

**Conclusão:** o esforço é threading + swap, não construção de infra. As **exceções** (que exigem trabalho real) são: `ensure_ai_agent` (não tem param de inbox), `update_last_user_message_content`/`get_last_user_message` (contact-global), o modelo do `TRANSFER_TAG`, e a fiação nova no frontend (`sendPrivateMessage`/`sendPresence`).

### 2.3 A cadeia da falha relatada (conversa #41)
`_run_private_ai` ([contacts.py:1018](../server/routes/contacts.py#L1018)) resolve o canal certo para o **envio** ([:1100](../server/routes/contacts.py#L1100)) e para a **nota privada** ([:1077](../server/routes/contacts.py#L1077)), mas NÃO para: `aprocess_message` ([:1030](../server/routes/contacts.py#L1030), sem `channel_id` → lê contexto do WhatsApp), `broadcast_tool_calls` ([:1045](../server/routes/contacts.py#L1045), sem `channel_id`) e `save_assistant_message` ([:1124](../server/routes/contacts.py#L1124), sem `channel_id` → **cria/arquiva a resposta na conversa WhatsApp = #41**). A mensagem sai pelo Telegram, mas a cópia persistida vai pro WhatsApp.

### 2.4 Falsos positivos descartados
| Hipótese | Veredito | Razão (verificada) |
|---|---|---|
| `server/routes/sandbox.py:62` — `aprocess_message`/`save_assistant_message` sem `channel_id` | ❌ **Rejeitado** (verificador) | Sandbox é harness de **debug single-inbox**, sem seletor de canal e **sem envio real**; default `"default"` é o escopo pretendido. Latente só se o sandbox virar channel-aware. Fora de escopo (opcional: pinar `"default"` explícito por clareza). |
| Tags / pin / unread / `has_unread_mention` serem por-contato | ❌ **Rejeitado** | **Por design** (plano 01 — "tags são por contato"). Não é bug. O único efeito colateral é **cosmético** (âncora do card de notice de tag no canal errado — Cluster A, LOW). |
| `_broadcast_tool_calls` salvar o card `tool_call` no canal errado | ❌ **Já corrigido** | O **save** do card já é inbox-aware ([messaging_service.py:472-482](../app/services/messaging_service.py#L472)). Só os **broadcasts** irmãos (515/555) regrediram — esses sim entram no Cluster A. |
| `custom_attributes`/JSONB nativo estarem vulneráveis | ❌ Fora de escopo | Outro tema (plano 34). Este plano não toca em serialização. |

---

## 3. Inventário dos defeitos (18, deduplicados) — `arquivo:linha` verificado

**Idioma do fix (Cluster A):** trocar `conversation_repo.get_open_for_contact(<cid>)` por `conversation_repo.get_open_for_contact_inbox(<cid>, <inbox_id>)` (idem `get_latest_*`), onde `<inbox_id>` = `ctx.contact.inbox_id` / `contact.inbox_id` / `self.inbox_id`. **Idioma do fix (Cluster B):** passar `channel_id=<resolvido>` no call site. **Idioma (Cluster C):** encaminhar `channelId` via `_scopeFields`/body.

### Cluster A — Resolução channel-blind (o `inbox_id` está disponível e é ignorado)
| # | Local | Sev | Efeito num contato multicanal | Risco | Esforço |
|---|---|---|---|---|---|
| A1 | [transfer_to_human.py:66](../agent/tools/transfer_to_human.py#L66) | **HIGH** | Despausa/desatribui a conversa do **canal errado**; o canal que ia pro humano fica com IA ativa | baixo | S |
| A2 | [transferir_agente.py:80](../agent/tools/transferir_agente.py#L80) | **HIGH** | Handoff de agente aplicado na conversa do outro canal; validação de router avalia o canal errado | baixo | S |
| A3 | [set_custom_attribute.py:92](../agent/tools/set_custom_attribute.py#L92) | MED | Atributo `scope=conversation` gravado na conversa do outro canal | baixo | S |
| A4 | [agent_factory.py:213](../agent/agent_factory.py#L213) (`resolve_active_agent_key`) | **HIGH**¹ | Escolhe o agente que responde a partir da conversa/inbox do outro canal | baixo | S |
| A5 | [memory.py:543](../agent/memory.py#L543) (`_custom_attr_lines('conversation')`) | MED | Injeta atributos de conversa do outro canal no system prompt | baixo | S |
| A6 | [conversation_repo.py:515](../db/repositories/conversation_repo.py#L515) (`ensure_ai_agent` usa `get_latest_for_contact`) + caller [handler.py:361](../agent/handler.py#L361) | **HIGH** | Carimba `active_agent_key` + broadcast `conversation_assigned` em conversa de outro canal — inclusive **fechada** (`get_latest`, não `get_open`) | médio | M |
| A7 | [handler.py:322](../agent/handler.py#L322) (`_emit_resolution_error`) | LOW | Card de erro painel-only no fio errado | baixo | S |
| A8 | [messaging_service.py:515 e 555](../app/services/messaging_service.py#L515) (broadcasts pós-tool-call) | MED | `conversation_updated`/`conversation_assigned` apontam a conversa errada (o **save** já é inbox-aware; só os broadcasts regrediram) | baixo | S |
| A9 | [messaging_service.py:714](../app/services/messaging_service.py#L714) (`maybe_emit_ai_takeover`) | MED¹ | Card "IA assumiu" + dedupe `has_event` no canal errado | baixo | S |
| A10 | [messaging_service.py:1154](../app/services/messaging_service.py#L1154) (`_conversation_ai_active`) | **HIGH**¹ | Gate da IA lê `ai_active`/`assignee` do canal errado → IA muda no canal errado | médio | S |
| A11 | [tags.py:157-160](../server/routes/tags.py#L157) | LOW | Card `tag_added/removed` ancorado no canal errado (a tag em si é contact-global **por design**) | baixo | S |
| A12 | [conversations.py:667-675](../server/routes/conversations.py#L667) (`GET /contacts/{phone}/conversation`) | LATENTE | Endpoint resolve por contato → pode devolver o canal errado no header | baixo | S · **ver P3** |

¹ Verificador marcou **PLAUSIBLE**: só se manifesta com atividade **concorrente** nos dois canais dentro da janela de batch (~3s); no fluxo sequencial o `last_activity` do canal atual "mascara" o bug. O fix é o mesmo e barato — corrigir mesmo assim (remove a fragilidade de corrida).

### Cluster B — `channel_id` largado em route handlers (default → `"default"` = WhatsApp)
| # | Local | Sev | Efeito | Risco | Esforço |
|---|---|---|---|---|---|
| B1 | `_run_private_ai`: [aprocess_message@1030](../server/routes/contacts.py#L1030) + [broadcast_tool_calls@1045](../server/routes/contacts.py#L1045) + [save_assistant_message@1124](../server/routes/contacts.py#L1124) | **HIGH** | **A causa da conversa #41 relatada**: contexto lido do WhatsApp; cards de tool + resposta arquivados na conversa WhatsApp | baixo | M |
| B2 | `send_private_audio`: [_get_contact@1286](../server/routes/contacts.py#L1286) (nota) + [@1313](../server/routes/contacts.py#L1313) (transcrição) | **HIGH** | Nota de áudio privada + card de transcrição misfilam pro WhatsApp; WS `new_message` sem `channel_id` | baixo | S |
| B3 | toggle-ai: [route@1624](../server/routes/contacts.py#L1624) → [toggle_contact_ai@485](../app/services/conversation_service.py#L485) chama [emit_for_contact **sem** `inbox_id`@507](../app/services/conversation_service.py#L507) | MED | Card `ai_on/ai_off` + espelho `ai_active` caem num canal arbitrário do contato | médio | M · **ver P2** |
| B4 | [channel_webhook.py:231](../server/routes/channel_webhook.py#L231) (`group_participants` system_notice) | MED | Único branch do loop que salva sem `ev.channel_id` (todos os outros passam) → card de roster no canal errado | baixo | S |
| B5 | [handler.py:420](../agent/handler.py#L420) (`update_last_user_message_content`) → [message_repo.get_last_user_message](../db/repositories/message_repo.py) contact-global | LOW | Corrida de transcrição: transcrição escrita na última msg do **outro** canal | médio | M |

### Cluster C — Frontend omite `channel_id` (backend cai em `_channel_for` → `"default"`)
| # | Local | Sev | Efeito | Risco | Esforço |
|---|---|---|---|---|---|
| C1 | [sendPrivateMessage@api.js:264](../web/static/js/services/api.js#L264) (não encaminha `channelId`) + [useComposer.js:177](../web/static/js/components/contacts/hooks/useComposer.js#L177) (não passa `channelId`) | **HIGH** | Ao **iniciar** conversa nova num canal não-default (`conversation_id=null`), a nota privada **e** a rodada de IA misfilam pro WhatsApp — **reproduz o bug puramente pelo frontend**. (O path de áudio privado já passa `channelId` — a assimetria é o tell.) | baixo | S |
| C2 | [sendPresence@api.js:341](../web/static/js/services/api.js#L341) (sem param `channelId`) + [useComposer.js:132/136/142/162](../web/static/js/components/contacts/hooks/useComposer.js#L132) + endpoint presence | LOW | Indicador "digitando…" emitido no WhatsApp ao compor conversa nova de outro canal | baixo | S |

### Cluster D — Modelo de dados: `TRANSFER_TAG` é contact-global (mais fundo que trocar resolver)
| # | Local | Sev | Efeito | Risco | Esforço |
|---|---|---|---|---|---|
| D1 | grava: [transfer_to_human.py:60](../agent/tools/transfer_to_human.py#L60) (`ctx.contact.add_tag(TRANSFER_TAG)`) · lê: [messaging_service.py:1161](../app/services/messaging_service.py#L1161) · limpa: [conversation_service.py:263-279](../app/services/conversation_service.py#L263) | **HIGH** | A trava `transferido_atendente` mora em `contact_tags` (contact-global): **transferir num canal silencia a IA no outro**; reabrir/religar num canal "destransfere" o outro | alto | L · **ver P1** |

---

## 4. Infra habilitadora (o pouco que precisa mudar de assinatura)

Quase tudo reusa a §2.2. Os únicos ajustes de **assinatura/infra**:

| Item | Mudança | Fase |
|---|---|---|
| `ensure_ai_agent(contact_id, agent_key)` | Adicionar `inbox_id: int` e trocar `get_latest_for_contact` → `get_latest_for_contact_inbox` internamente ([conversation_repo.py:505-526](../db/repositories/conversation_repo.py#L505)). Caller [handler.py:361](../agent/handler.py#L361) passa `contact.inbox_id`. | FA6 |
| `update_last_user_message_content(phone, new_content)` | Adicionar `channel_id` e escopar `get_last_user_message` por conversa/inbox (novo filtro `conversation_id`/`inbox_id` em [message_repo](../db/repositories/message_repo.py)). Caller [messaging_service.py:957](../app/services/messaging_service.py#L957) passa `channel_id`. | FB5 |
| `toggle_contact_ai(...)` | Aceitar e encaminhar `inbox_id` (ou `channel_id`) para `emit_for_contact(inbox_id=…)` + decidir semântica do espelho `ai_active` (P2). Route [contacts.py:1643](../server/routes/contacts.py#L1643) resolve o inbox e passa. | FB3 |
| `sendPrivateMessage(phone, text, opts)` | Encaminhar `opts.channelId` → `body.channel_id` (espelhar `sendPrivateAudio`, [api.js:326](../web/static/js/services/api.js#L326)). | FC1 |
| `sendPresence(phone, action, conversationId, channelId)` | Novo 4º arg → `body.channel_id`; backend presence endpoint usa `_channel_for(phone, conv_id, channel_id)`. | FC2 |
| **(P1)** `TRANSFER_TAG` | Se P1 = mover para estado por-conversa: nova coluna/flag em `atendimentos` (migration) OU atributo de conversa; ler/limpar por conversa. Se P1 = manter tag mas escopar checagem: gate lê a conversa do inbox e só bloqueia se **aquela** conversa está transferida. | FD1 |

Nenhum outro `Table`/migration é obrigatório (exceto a de D1, se P1 escolher a coluna).

---

## 5. Fases / Roadmap

### Diagrama de dependências (waves)

```
WAVE 0   F0(caracterização multicanal)                         🔴 barreira — ANTES de tudo
            │ (decidir P1 e P2 aqui também)
            ▼
WAVE 1   FA1 · FA2 · FA3 · FA4 · FA5 · FA6                     🟢 Cluster A (paralelo por ARQUIVO)
   (arquivos disjuntos: tools/, agent_factory+memory, handler+repo, messaging_service, tags, conversations)
            │
WAVE 2   FB1 → FB2 · FB4 · FB5                                 Cluster B
   FB1,FB2,FB3(=toggle) tocam contacts.py → SEQUENCIAIS entre si (🔴); FB4,FB5 paralelos (🟢)
            │  FB3 [depende de: P2]
WAVE 3   FC1 · FC2                                             🟢 Cluster C (frontend; FC2 toca contacts.py → coordenar c/ Wave 2)
            │
WAVE 4   FD1                                                  🔴 Cluster D [depende de: P1]  (pode iniciar em paralelo à Wave 1 se P1 já decidido)
            │
WAVE 5   F-REG (regressão multicanal — inverte F0)            🔴 depois de todas
```

### Tabela de fases

| Wave | Fase | Workstream / arquivo(s) | 🟢/🔴 | Risco | Pronto quando (resumo) |
|---|---|---|---|---|---|
| 0 | **F0** Caracterização multicanal | `tests/` | 🔴 barreira | baixo | testes reproduzem o misfiling (private-AI, transfer, gate) com 2 canais p/ o mesmo phone |
| 1 | **FA1** Tools de IA | `agent/tools/{transfer_to_human,transferir_agente,set_custom_attribute}.py` | 🟢 | baixo | tools resolvem via `ctx.contact.inbox_id` |
| 1 | **FA2** Motor/prompt | `agent/agent_factory.py`, `agent/memory.py` | 🟢 | baixo | agente e atributos do prompt vêm do canal do turno |
| 1 | **FA3** Card de erro + broadcasts | `agent/handler.py` (322), `app/services/messaging_service.py` (515/555/714/1154) | 🟢 `[coordena handler.py c/ FA6/FB5]` | médio | cards/gate ancoram no inbox correto; gate mantém fail-open |
| 1 | **FA5** Âncoras de notice/endpoint | `server/routes/tags.py`, `server/routes/conversations.py` | 🟢 | baixo | notice de tag ancora no inbox; GET conversation por P3 |
| 1 | **FA6** `ensure_ai_agent` inbox-scoped | `db/repositories/conversation_repo.py`, `agent/handler.py` (361) | 🟢 `[coordena handler.py]` | médio | `ensure_ai_agent(contact_id, agent_key, inbox_id)`; caller passa `contact.inbox_id` |
| 2 | **FB1** private-ai + private-audio | `server/routes/contacts.py` (1030/1045/1124/1286/1313) | 🔴 (mesmo arquivo) | baixo | resposta/nota/transcrição/tool-cards da IA privada ficam no canal de origem |
| 2 | **FB3** toggle-ai inbox-aware | `server/routes/contacts.py` (1643), `app/services/conversation_service.py` | 🔴 `[depende de: P2; após FB1]` | médio | card `ai_on/ai_off` + `ai_active` no canal correto (ou todos, por P2) |
| 2 | **FB4** webhook group_participants | `server/routes/channel_webhook.py` (231) | 🟢 | baixo | roster card salvo com `ev.channel_id` |
| 2 | **FB5** transcrição sem corrida | `agent/handler.py` (420), `db/repositories/message_repo.py` | 🟢 `[coordena handler.py]` | médio | transcrição escrita na msg da conversa do canal |
| 3 | **FC1** private-message channelId | `web/.../services/api.js`, `web/.../hooks/useComposer.js` | 🟢 | baixo | conversa nova em canal não-default não misfila |
| 3 | **FC2** presence channelId | `api.js`, `useComposer.js`, `server/routes/contacts.py` (presence) | 🔴 `[toca contacts.py — após FB1/FB3]` | baixo | "digitando…" no canal certo |
| 4 | **FD1** trava de transferência por-conversa | `agent/tools/transfer_to_human.py`, `messaging_service.py`, `conversation_service.py` (+migration se P1-coluna) | 🔴 `[depende de: P1]` | alto | transferir num canal não silencia o outro |
| 5 | **F-REG** Regressão multicanal | `tests/` | 🔴 `[depende de: todas]` | baixo | F0 invertido; suíte verde no Postgres |

---

### Fase F0 — Caracterização multicanal (barreira, ANTES de consertar)
**Objetivo:** provar o comportamento errado com testes que hoje passam (documentam o misfiling) e que F-REG vai inverter. Decidir P1 e P2.

**Itens:**
- `[sequencial]` Helper de teste: semear **um mesmo phone com DOIS canais/inboxes** (ex.: `default` = GOWA + um canal `telegram-test`), cada um com conversa aberta. Reusar o setup de canais dos testes existentes (ver `tests/` que criam canais/inbox).
- `[paralelo]` Teste **B1 (private-AI)**: chamar o fluxo `_run_private_ai` (via endpoint `/private-message` com `conversation_id` do canal Telegram e `ai_read=true`) e assertar o **estado ATUAL**: a resposta/assistant é gravada na conversa do inbox **`default`** (WhatsApp), não na do Telegram. Marcar `# F0: caracteriza o bug; F-REG inverte`.
- `[paralelo]` Teste **A10 (gate)**: com uma conversa `default` marcada `ai_active=0`/assignee e uma conversa Telegram `ai_active=1`, `_conversation_ai_active(contact_telegram)` retorna **False** hoje (lê o canal errado).
- `[paralelo]` Teste **A1/A2 (tools)**: rodar `transfer_to_human`/`transferir_agente` com `ctx.contact` do Telegram e assertar que a conversa **`default`** foi a mutada (quando ela é a mais recente).
- `[sequencial]` Registrar em prosa o repro do usuário (nota privada no Telegram → resposta na conversa nova de WhatsApp #41).

**Pronto quando:** os testes rodam **verdes descrevendo o misfiling** (capturam o baseline). P1 e P2 decididos (§7).

#### Status de execução — Fase F0
**Estado:** ✅ Concluída
- **O que foi feito:** Novo `tests/test_multichannel_routing.py` com helper `_seed_two_inboxes` (mesmo phone em 2 inboxes: `default` GOWA + um canal cloud, `default` forçado a ser o mais recente) e 4 testes de caracterização que passam descrevendo o bug: `test_f0_gate_ai_reads_wrong_channel` (A10 — gate lê o canal errado, retorna False), `test_f0_transfer_to_human_mutates_wrong_channel` (A1 — muta a conversa default), `test_f0_transferir_agente_stamps_wrong_channel` (A2 — carimba `active_agent_key` no default), `test_f0_private_ai_saves_reply_to_wrong_channel` (B1 — `save_assistant_message` recebe `channel_id="default"`, a conversa #41).
- **Como foi feito / decisões:** Reusei `_mk_channel_inbox` e `build_app`/`fake_agent_reply` (padrão de `test_improve_conversation_scope.py`). O canal cloud não é registrado como instância viva no app de teste (`whatsapp_cloud not loaded`), então o envio real retorna `ok=False` sem rede — o save mesmo assim ocorre no default (bug capturado). B1 usa spy em `save_assistant_message` + poll da task async do `create_task`. **P1 = P1-a** (estado por-conversa, sem migration nova — decidido com o usuário). **P2 = por-conversa** (desligar a IA numa conversa NÃO reflete na outra do mesmo número; o toggle vai operar no `ai_active` da conversa do canal, não no flag global do contato). **P3** = channel-aware quando o painel já tem `conversation_id`, com fallback legado. **P4** = sim (guardrail grep em F-REG).
- **Problemas / pendências:** A conversa do canal não-default já nasce com `active_agent_key='default'` (herança do inbox), então a asserção A2 foi relaxada para o invariante real ("o alvo caiu no canal default, e o Telegram NÃO recebeu o alvo"). Os demais sites de Cluster A (A3–A9, A11) e Cluster B/C/D ganham cobertura forward em F-REG.
- **Verificação:** `venv/bin/python -m pytest tests/test_multichannel_routing.py -q` → 4 passed.

---

### Fase FA1 — Tools de IA (transfer_to_human / transferir_agente / set_custom_attribute)
**Objetivo:** as 3 tools resolvem a conversa do **canal do turno**, não a mais recente de qualquer canal.

**Itens:**
- `[paralelo]` [transfer_to_human.py:66](../agent/tools/transfer_to_human.py#L66): `conv = conversation_repo.get_open_for_contact_inbox(ctx.contact.id, ctx.contact.inbox_id)`. (`ctx.contact` é `ContactMemory` — [context.py:260](../plugins/context.py#L260) — logo tem `.inbox_id`.)
- `[paralelo]` [transferir_agente.py:80](../agent/tools/transferir_agente.py#L80): idem. A validação de router (allowlist/`is_router`) passa a avaliar o `active_agent_key` do canal certo.
- `[paralelo]` [set_custom_attribute.py:92](../agent/tools/set_custom_attribute.py#L92): idem — `scope=conversation` grava na conversa do canal.
- ⚠️ Preservar o tratamento de `None` (se não houver conversa aberta naquele inbox, comportamento igual ao de hoje quando `get_open_for_contact` retorna `None`).

**Pronto quando:** teste A1/A2 de F0 invertido — com `ctx.contact` do Telegram, a mutação recai na conversa **do Telegram**; suíte de tools/routing verde (`test_agent_routing`, `test_routing_engine`, `test_spoke_router_enforcement`).

#### Status de execução — Fase FA1
**Estado:** ✅ Concluída
- **O que foi feito:** As 3 tools ([transfer_to_human.py:66](../agent/tools/transfer_to_human.py#L66), [transferir_agente.py:80](../agent/tools/transferir_agente.py#L80), [set_custom_attribute.py:92](../agent/tools/set_custom_attribute.py#L92)) trocaram `get_open_for_contact(ctx.contact.id)` por `get_open_for_contact_scoped(ctx.contact)`. Asserções A1/A2 do F0 invertidas para o canal correto.
- **Como foi feito / decisões:** Adicionei um helper central `conversation_repo.get_open_for_contact_scoped(contact)` em vez do idioma literal do plano (`get_open_for_contact_inbox(id, inbox_id)`). Motivo: os test doubles existentes (`test_spoke_router_enforcement`, `test_transfer_broadcast`, `test_router_prompt_description`, `test_agent_routing`, `test_human_gate`) montam `ctx.contact`/`contact` como `SimpleNamespace`/`FakeContact` **sem** `inbox_id` — passar `ctx.contact.inbox_id` direto levantaria `AttributeError` e quebraria a suíte inteira. O helper usa `getattr(contact,"inbox_id",None)`: com inbox_id (todo `ContactMemory` real tem — [memory.py:97](../agent/memory.py#L97)) resolve por-inbox; sem ele cai no resolver channel-blind (D2 fail-open, byte-idêntico ao legado). É o **único ponto sancionado** a chamar `get_open_for_contact` como fallback (o guardrail P4 allow-lista só ele). Vou reusar esse helper em FA2/FA3.
- **Problemas / pendências:** Nenhuma. O mesmo helper cobre gate/factory/memory nas próximas fases.
- **Verificação:** `pytest tests/test_multichannel_routing.py tests/test_spoke_router_enforcement.py tests/test_transfer_broadcast.py tests/test_router_prompt_description.py tests/test_routing_motivo.py -q` → 25 passed; `python tests/test_agent_routing.py` → 29 passed.

---

### Fase FA2 — Motor / prompt (agent_factory + memory)
**Objetivo:** o agente que responde e os atributos de conversa injetados no prompt vêm do **canal do turno**.

**Itens:**
- `[paralelo]` [agent_factory.py:213](../agent/agent_factory.py#L213) (`resolve_active_agent_key`): `conv = conversation_repo.get_open_for_contact_inbox(cid, getattr(contact, "inbox_id", None))`. Com `inbox_id` ausente (defensivo), cair no comportamento default (retornar `None` → default agent). Assim `conv["inbox_id"]` (usado em [:220](../agent/agent_factory.py#L220) p/ `inbox.default_agent_key`) é o do canal certo.
- `[paralelo]` [memory.py:543](../agent/memory.py#L543) (`_custom_attr_lines`): `conv = conversation_repo.get_open_for_contact_inbox(self.id, self.inbox_id)` — `self.inbox_id` já existe no `ContactMemory`.

**Pronto quando:** com 2 canais bindados a agentes/atributos distintos, um turno num canal usa o agente/atributos **daquele** canal; `test_model_factory`/`test_dynamic_registry` verdes.

#### Status de execução — Fase FA2
**Estado:** ✅ Concluída
- **O que foi feito:** [agent_factory.py:215](../agent/agent_factory.py#L215) `resolve_active_agent_key` usa `get_open_for_contact_scoped(contact)`; [memory.py:543](../agent/memory.py#L543) `_custom_attr_lines` usa `get_open_for_contact_inbox(self.id, self.inbox_id)` (o `ContactMemory` sempre tem `inbox_id`). Novo teste forward `test_a4_resolve_agent_from_turn_channel`.
- **Como foi feito / decisões:** `agent_factory` recebe um `contact`-like (pode ser double) → helper scoped com fail-open. `memory` opera sobre `self` (ContactMemory garantido) → `_inbox` direto, mais explícito. Nenhuma mudança de assinatura.
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `python tests/test_model_factory.py` → 24 passed; `python tests/test_dynamic_registry.py` → 6 passed; `pytest tests/test_multichannel_routing.py` → 5 passed.

---

### Fase FA3 — Card de erro + broadcasts pós-tool-call + gate de IA
**Objetivo:** cards painel-only e o gate de IA ancoram/leem o inbox correto. **Fail-open preservado.**

**Itens:**
- `[sequencial]` [handler.py:322](../agent/handler.py#L322) (`_emit_resolution_error`): `get_open_for_contact_inbox(contact.id, getattr(contact,"inbox_id",None))`.
- `[sequencial]` [messaging_service.py:515](../app/services/messaging_service.py#L515) e [:555](../app/services/messaging_service.py#L555): trocar por `get_open_for_contact_inbox(contact.id, contact.inbox_id)` (o `save` do card já é inbox-aware — alinhar os broadcasts a ele; ver o comentário-lição em [:472-478](../app/services/messaging_service.py#L472)).
- `[sequencial]` [messaging_service.py:714](../app/services/messaging_service.py#L714) (`maybe_emit_ai_takeover`): idem — card + dedupe `has_event` no canal que respondeu.
- `[sequencial]` [messaging_service.py:1154](../app/services/messaging_service.py#L1154) (`_conversation_ai_active`): `get_open_for_contact_inbox(contact.id, contact.inbox_id)`. ⚠️ **Manter o fail-open**: `conv=None` continua caindo na checagem de `TRANSFER_TAG` e retornando `True` no `except`. (A linha 1161 `TRANSFER_TAG` é Cluster D — não mexer aqui.)
- ⚠️ `agent/handler.py` também é tocado por **FA6** (361) e **FB5** (420) — **coordenar** (mesmo arquivo): idealmente FA3+FA6+FB5 num mesmo owner/commit sequenciado, ou aplicar em ordem para evitar conflito.

**Pronto quando:** teste A10 (gate) de F0 invertido — `_conversation_ai_active(contact_telegram)` reflete a conversa **do Telegram**; cards de erro/takeover/atributo caem no fio correto; suíte de mensagens/caracterização verde.

#### Status de execução — Fase FA3
**Estado:** ✅ Concluída
- **O que foi feito:** 5 swaps para `get_open_for_contact_scoped(contact)`: [handler.py:322](../agent/handler.py#L322) (`_emit_resolution_error`), messaging_service em `broadcast_tool_calls` (conversa-scope attr broadcast + o `conversation_assigned` pós-transfer), `maybe_emit_ai_takeover` (dedupe `has_event`) e o gate `_conversation_ai_active`. Asserção A10 do F0 invertida (gate reflete o canal do turno + o caso inverso).
- **Como foi feito / decisões:** No gate mantive o fail-open explícito: `conv=None` cai na checagem de `TRANSFER_TAG` (linha intacta — Cluster D fica pra FD1) e retorna True no `except`. Todos os `contact` aqui são `ContactMemory` reais (têm `inbox_id`); o helper scoped é usado por uniformidade. `handler.py` é compartilhado com FA6/FB5 — apliquei em ordem (FA3 primeiro, linhas disjuntas 322/361/420).
- **Problemas / pendências:** Rodar as 3 characterization juntas falha por contaminação de estado cross-file (engine process-global — documentado na memória "pytest tests/ não roda inteiro"); cada arquivo isolado passa. Não é regressão desta fase.
- **Verificação:** `pytest tests/test_human_gate.py tests/test_multichannel_routing.py tests/test_tool_call_broadcast.py tests/test_transfer_broadcast.py` → 16 passed. Isolados: `test_webhook_characterization` 26 passed, `test_agent_turn_characterization` 5 passed, `test_lifecycle_characterization` 6 passed/1 skipped.

---

### Fase FA5 — Âncoras de notice de tag + endpoint GET conversation
**Objetivo:** cards de notice e o header por-phone respeitam o canal (dentro do que faz sentido).

**Itens:**
- `[paralelo]` [tags.py:157-160](../server/routes/tags.py#L157): a tag é contact-global (mantém), mas a **âncora do card** deve preferir o inbox do painel. Como a rota de tags só tem `phone` (sem `conversation_id`), decidir por P3: (a) manter contact-scoped (card cosmético) ou (b) o painel passar `conversation_id`/`channel_id` no PUT de tags e usar `emit_conversation_notice(conversation_id=…)` direto.
- `[paralelo]` [conversations.py:667-675](../server/routes/conversations.py#L667) (`GET /contacts/{phone}/conversation`): por P3 — aceitar `channel_id`/`inbox_id` opcional e resolver via `*_inbox`; sem ele, manter o legado (endpoint por-phone).

**Pronto quando:** conforme P3. Se P3 = manter legado, esta fase é só documentação (marcar como intencional). Suíte de tags/conversations verde.

#### Status de execução — Fase FA5
**Estado:** ✅ Concluída (P3 = channel-aware quando o painel tem `conversation_id`/`channel_id`, fallback legado)
- **O que foi feito:** [tags.py](../server/routes/tags.py) `set_contact_tags`: a âncora do card `tag_added/removed` prefere `body["conversation_id"]` (validando posse do contato) antes do legado `get_open/get_latest_for_contact`. [conversations.py](../server/routes/conversations.py) `GET /contacts/{phone}/atendimento`: novos query params opcionais `conversation_id` (resolve direto, com posse + `_inbox_hidden`) e `channel_id` (escopa via `inbox_repo.get_by_channel` → `get_open/latest_for_contact_inbox`); sem nenhum, mantém o legado por-phone.
- **Como foi feito / decisões:** Tags são contact-global por design (plano 01) — só a **âncora** do aviso ficou channel-aware. Mudanças 100% aditivas (params opcionais/None), o front atual que não manda os campos cai no legado byte-a-byte.
- **Problemas / pendências:** O frontend ainda não passa esses campos (é o gancho pra quando o painel multicanal quiser precisão do card/header — fora do escopo desta fase, sem impacto de alto valor per P3/D4). Nenhuma pendência bloqueante.
- **Verificação:** `python tests/test_endpoints.py` → 1086 passed; `pytest tests/endpoints/test_conversation_events_c0.py` → 10 passed.

---

### Fase FA6 — `ensure_ai_agent` inbox-scoped
**Objetivo:** a atribuição do agente de IA nunca carimba/rouba a conversa de outro canal (nem uma **fechada**).

**Itens:**
- `[sequencial]` [conversation_repo.py:505-526](../db/repositories/conversation_repo.py#L505): assinatura `ensure_ai_agent(contact_id, agent_key, inbox_id)`; internamente trocar [`get_latest_for_contact`:515](../db/repositories/conversation_repo.py#L515) por `get_open_for_contact_inbox(contact_id, inbox_id)` (⚠️ preferir **open**, não latest — evita mexer em conversa **fechada** de outro canal; hoje o `if conv.status != "open": return None` já barra, mas resolver pelo open é mais direto e correto).
- `[sequencial]` [handler.py:361](../agent/handler.py#L361): passar `contact.inbox_id`.
- ⚠️ `handler.py` compartilhado com FA3/FB5 — coordenar.

**Pronto quando:** com 2 conversas abertas do contato, uma resposta da IA num canal carimba `active_agent_key` **só** na conversa daquele canal; `test_agent_routing` verde.

#### Status de execução — Fase FA6
**Estado:** ✅ Concluída
- **O que foi feito:** [conversation_repo.py](../db/repositories/conversation_repo.py) `ensure_ai_agent(contact_id, agent_key, inbox_id=None)`: com `inbox_id` usa `get_open_for_contact_inbox` (só a conversa ABERTA do canal), em vez de `get_latest_for_contact` (que incluía fechadas de qualquer canal). [handler.py:361](../agent/handler.py#L361) passa `getattr(contact,"inbox_id",None)`. Teste forward `test_a6_ensure_ai_agent_scoped_to_inbox`.
- **Como foi feito / decisões:** `inbox_id` opcional (default None) → fail-open pro resolver legado (D2), mas o único caller real (ContactMemory) sempre passa. Resolver por **open** também elimina o risco de carimbar uma conversa fechada (o `if status != "open"` vira redundante, mantido como cinto).
- **Problemas / pendências:** Nenhuma.
- **Verificação:** `pytest tests/test_multichannel_routing.py tests/test_transfer_broadcast.py` → 8 passed; `python tests/test_agent_routing.py` → 29 passed.

---

### Fase FB1 — `_run_private_ai` + `send_private_audio` (contacts.py)
**Objetivo:** a IA privada (texto e áudio) lê o contexto e arquiva resposta/nota/transcrição/tool-cards **no canal de origem**. **Corrige a conversa #41 relatada.**

**Itens:**
- `[sequencial]` No topo de `_run_private_ai` resolver uma vez: `note_channel = _channel_for(phone, conversation_id)` (já usado em [:1077](../server/routes/contacts.py#L1077)/[:1100](../server/routes/contacts.py#L1100)) e usá-lo em:
  - [aprocess_message@1030](../server/routes/contacts.py#L1030): `channel_id=note_channel`.
  - [broadcast_tool_calls@1045](../server/routes/contacts.py#L1045): `channel_id=note_channel` (o kwarg **já existe** — [messaging_service.py:448](../app/services/messaging_service.py#L448)).
  - [save_assistant_message@1124](../server/routes/contacts.py#L1124): `channel_id=note_channel`; incluir `"channel_id": channel_id` no broadcast `new_message` [@1139](../server/routes/contacts.py#L1139).
- `[sequencial]` `send_private_audio`: usar o `resolved_channel` já computado ([:1242](../server/routes/contacts.py#L1242)) em `_get_contact(phone, channel_id=resolved_channel)` nas duas closures ([:1286](../server/routes/contacts.py#L1286) nota, [:1313](../server/routes/contacts.py#L1313) transcrição) e adicionar `"channel_id": resolved_channel` aos broadcasts [@1307](../server/routes/contacts.py#L1307)/[@1317](../server/routes/contacts.py#L1317).

**Pronto quando:** teste B1 de F0 invertido — a resposta/nota/transcrição da IA privada iniciada no Telegram fica **na conversa do Telegram**; nenhuma conversa WhatsApp fantasma é criada; broadcasts carregam `channel_id`.

#### Status de execução — Fase FB1
**Estado:** ✅ Concluída (corrige a conversa #41 relatada)
- **O que foi feito:** `_run_private_ai` resolve `run_channel = _channel_for(phone, conversation_id)` uma vez e o passa a `aprocess_message(channel_id=)`, `broadcast_tool_calls(channel_id=)` e `save_assistant_message(channel_id=)`; o broadcast `new_message` já carregava `channel_id`. `send_private_audio`: `_get_contact(..., channel_id=resolved_channel)` na nota e na transcrição + `"channel_id": resolved_channel` nos dois broadcasts. Asserção B1 do F0 invertida (resposta + contexto no Telegram; **0** assistants na conversa default).
- **Como foi feito / decisões:** Reusei o `resolved_channel`/`_channel_for` que os call sites de envio JÁ computavam; a assimetria (envio certo, save errado) era a causa raiz. Nenhuma mudança de assinatura (os kwargs já existiam).
- **Problemas / pendências:** Nenhuma. FC1 (frontend) fecha o outro lado (o painel precisa mandar `channelId` ao INICIAR conversa nova sem `conversation_id`).
- **Verificação:** `pytest tests/test_multichannel_routing.py` → 6 passed; `python tests/test_endpoints.py` → 1086 passed.

---

### Fase FB3 — toggle-ai inbox-aware (contacts.py + conversation_service)
**Objetivo:** ligar/desligar IA do contato ancora o card + espelha `ai_active` no canal certo (ou em todos, conforme P2). **Depende de P2.**

**Itens:**
- `[sequencial]` [contacts.py:1643](../server/routes/contacts.py#L1643): a rota resolve o inbox do painel (aceitar `conversation_id`/`channel_id` no body ou derivar) e passar a `toggle_contact_ai`.
- `[sequencial]` [conversation_service.py:507](../app/services/conversation_service.py#L507): `emit_for_contact(..., inbox_id=<resolvido>)` (o param **já existe** — [system_notices.py:400](../server/system_notices.py#L400)); o espelho `set_ai_active` recai na conversa retornada.
- **Semântica (P2):** (a) ancorar no canal aberto do painel (card+flip só naquele canal) **ou** (b) espelhar em **todas** as conversas abertas do contato (toggle é contact-global). Implementar conforme decisão.
- ⚠️ `contacts.py` compartilhado com FB1/FC2 — sequenciar.

**Pronto quando:** operador no fio do Telegram desliga a IA → card + `ai_active` no Telegram (ou em todos, por P2), **não** num canal arbitrário; `characterization` de toggle-ai verde.

#### Status de execução — Fase FB3
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase FB4 — Webhook group_participants (channel_webhook.py)
**Objetivo:** o card de mudança de roster de grupo é salvo no canal que recebeu o evento.

**Itens:**
- `[paralelo]` [channel_webhook.py:231](../server/routes/channel_webhook.py#L231): `agent_handler._get_contact(chat_id, channel_id=ev.channel_id)` e incluir `"channel_id": ev.channel_id` no broadcast `new_message` [@235](../server/routes/channel_webhook.py#L235). (`ev.channel_id` já é usado no `emit_with_filter` irmão [@239](../server/routes/channel_webhook.py#L239) — só alinhar.)

**Pronto quando:** join/leave/promote/demote num canal não-default grava o card na conversa daquele canal; teste de webhook de grupo verde.

#### Status de execução — Fase FB4
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase FB5 — Transcrição sem corrida cross-canal (handler + message_repo)
**Objetivo:** a transcrição de áudio/imagem/doc é escrita na última msg **da conversa do canal**, não na globalmente mais recente.

**Itens:**
- `[sequencial]` [message_repo.get_last_user_message](../db/repositories/message_repo.py) (usada em [handler.py:420-425](../agent/handler.py#L420)): aceitar um filtro `conversation_id` (ou `inbox_id`) e escopar o `SELECT` da última `role='user'`.
- `[sequencial]` [handler.py:420](../agent/handler.py#L420) (`update_last_user_message_content`): aceitar `channel_id`; resolver a conversa do canal e passar `conversation_id` ao repo.
- `[sequencial]` Caller [messaging_service.py:957](../app/services/messaging_service.py#L957): passar `channel_id` (disponível em `_run_one_cycle`).
- ⚠️ `handler.py` compartilhado — coordenar com FA3/FA6.

**Pronto quando:** teste multicanal — áudio no Telegram + texto no GOWA na janela de transcrição → a transcrição atualiza a msg **do Telegram**; suíte de caracterização de áudio verde.

#### Status de execução — Fase FB5
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase FC1 — Frontend: `sendPrivateMessage` encaminha `channelId`
**Objetivo:** iniciar conversa nova num canal não-default pela nota privada / "IA lê" não misfila pro WhatsApp.

**Itens:**
- `[paralelo]` [api.js:264-270](../web/static/js/services/api.js#L264): adicionar `if (opts.channelId != null) body.channel_id = opts.channelId;` (espelhar `sendPrivateAudio` [:326](../web/static/js/services/api.js#L326)).
- `[paralelo]` [useComposer.js:177-181](../web/static/js/components/contacts/hooks/useComposer.js#L177): passar `channelId` no objeto de opts (o hook já recebe `channelId` — [:40](../web/static/js/components/contacts/hooks/useComposer.js#L40)).
- O backend já aceita `channel_id` no `/private-message` (`_channel_for(phone, conv_id, channel_id)`) — só a fiação do frontend falta.

**Pronto quando:** manual — abrir conversa nova de um canal não-default (sem thread prévia), enviar nota privada com "IA lê" → nota e resposta ficam **naquele canal**. `node --test` dos módulos puros de frontend (se houver) verde.

#### Status de execução — Fase FC1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase FC2 — Frontend: `sendPresence` encaminha `channelId`
**Objetivo:** o indicador "digitando…" vai para o canal que o operador está compondo.

**Itens:**
- `[paralelo]` [api.js:341-345](../web/static/js/services/api.js#L341): assinatura `sendPresence(phone, action, conversationId, channelId)`; `if (channelId != null) body.channel_id = channelId;`.
- `[paralelo]` [useComposer.js:100/132/136/142/162](../web/static/js/components/contacts/hooks/useComposer.js#L132): passar `channelId` nas chamadas.
- `[sequencial]` Backend presence endpoint (contacts.py): `_channel_for(phone, body.get("conversation_id"), body.get("channel_id"))`. ⚠️ `contacts.py` — sequenciar após FB1/FB3.

**Pronto quando:** manual — compor conversa nova de canal não-default → presence emitido nesse canal, não no WhatsApp.

#### Status de execução — Fase FC2
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase FD1 — Trava de transferência por-conversa (Cluster D) · [depende de P1]
**Objetivo:** transferir para humano num canal **não** silencia/reativa a IA no outro canal do mesmo número.

**Itens (conforme P1):**
- **Opção P1-a (recomendada — estado por-conversa):** deixar de usar `TRANSFER_TAG` como trava. A transferência passa a marcar a **conversa** (a `transfer_to_human` já chama `assign_agent(active_agent_key=None, ai_active=0)` na conversa — o gate `_conversation_ai_active` já cobre "assignee humano sem agente"/`ai_active=0` **por-conversa** via FA3). Avaliar se a tag `transferido_atendente` ainda é necessária como sinal visual (mantê-la como **rótulo** contact-global é ok, desde que **NÃO** seja lida como trava de IA). Remover a checagem de tag do gate ([messaging_service.py:1161](../app/services/messaging_service.py#L1161)) OU torná-la inbox-aware.
- **Opção P1-b (mínima — escopar a checagem):** manter a tag, mas o gate só bloqueia se **a conversa daquele inbox** está de fato transferida (assignee/`ai_active=0`), e `_clear_transfer_tag` ([conversation_service.py:263](../app/services/conversation_service.py#L263)) não afeta o gate do outro canal.
- Ajustar [transfer_to_human.py:60](../agent/tools/transfer_to_human.py#L60) (grava a tag) conforme a opção.
- Se P1-a exigir coluna: migration Alembic aditiva em `atendimentos` (sem batch-mode, Postgres).

**Pronto quando:** teste multicanal — `transfer_to_human` no Telegram deixa a IA do WhatsApp **respondendo normalmente**; reabrir/religar num canal não mexe no outro; suíte de transfer/gate verde.

#### Status de execução — Fase FD1
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

### Fase F-REG — Regressão multicanal (inverte F0)
**Objetivo:** travar o comportamento novo para não regredir de novo.

**Itens:**
- `[sequencial]` Inverter todos os testes de F0 (private-AI, gate, tools) para assertar o **canal correto**.
- `[paralelo]` Cobrir os demais sites por cluster (private-audio, ensure_ai_agent, takeover, broadcasts, webhook group, transcrição, toggle-ai, transfer trap).
- `[paralelo]` Regressão do caminho **single-channel** (o comum): comportamento idêntico ao de hoje (o inbound toca o próprio inbox → resolução coincide).
- `[paralelo]` **Guardrail anti-regressão**: um teste/checagem que faz `grep` de `get_open_for_contact(`/`get_latest_for_contact(` (sem `_inbox`) nos caminhos sensíveis e falha se um novo aparecer fora da allow-list intencional (sandbox, endpoints por-phone aprovados em P3).

**Pronto quando:** `venv/bin/python -m pytest tests/endpoints -q` + os scripts standalone relevantes verdes no Postgres de teste; novos testes multicanal cobrem os 4 clusters.

#### Status de execução — Fase F-REG
**Estado:** ⬜ Não iniciada
- **O que foi feito:** _(...)_
- **Como foi feito / decisões:** _(...)_
- **Problemas / pendências:** _(...)_
- **Verificação:** _(...)_

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|---|---|---|
| Swap `get_open_for_contact` → `*_inbox` no gate (A10) | `*_inbox` retorna `None` onde o legado achava conversa → mudar o gate | **Fail-open (D2)**: `conv=None` mantém o caminho existente (cai na checagem de tag, retorna `True` no `except`). Nunca silencia por resolução. |
| `handler.py` tocado por FA3+FA6+FB5 | Conflito de merge / edições sobrepostas | Coordenar como **um owner sequencial** (ou aplicar em ordem FA3→FA6→FB5). Um refactor por commit. |
| `contacts.py` tocado por FB1+FB3+FC2 | idem | Sequenciar as fases desse arquivo (FB1 primeiro). |
| `ensure_ai_agent` `latest`→`open` (A6) | Mudança sutil de semântica (latest incluía fechadas) | O código já barra `status != "open"`; resolver por `open` é equivalente e mais correto. Cobrir com teste. |
| toggle-ai semântica (P2) | Escolher "canal atual" quebra a expectativa "toggle é do contato" | Decidir P2 **antes** de FB3; documentar. Opção (b) espelha em todas as conversas (preserva "contact-global"). |
| `TRANSFER_TAG` (P1) | Mudar a trava pode afetar fluxo de transferência single-channel | FD1 depois de FA3 (o gate por-conversa já cobre assignee/`ai_active`); caracterizar single-channel antes; migration **aditiva** se coluna. |
| Postgres-only / PgBouncer | Migration de D1 (se P1-coluna) sob transaction-mode | Roda no boot via `alembic upgrade head`, fora do pool de request; `ALTER TABLE` aditivo. |
| Frontend "conversa nova" | `channelId` null quando o painel realmente não sabe o canal | O inbox picker define `selectedChannelId` ao iniciar conversa nova (é justamente o caso do bug); onde não houver, backend mantém fallback `"default"` (comportamento legado). |
| Regressão de evento/broadcast | Adicionar `channel_id` a payloads WS pode confundir consumidores antigos | `channel_id` é aditivo no payload; o frontend já filtra por ele em outros eventos. Sem remover campos. |
| Plugins | Handlers de `message.*`/filters dependem de `channel_id`? | Mudanças são aditivas; nenhum contrato de evento é removido. Não tocar em `storages/plugins/`. |

---

## 7. Perguntas em aberto

- **P1** — **Modelo da trava de transferência (`TRANSFER_TAG`)**: mover para estado **por-conversa** (a) ou manter a tag mas escopar a checagem por inbox (b)? · ⏸️ **A DECIDIR (F0).** Contexto: hoje `transfer_to_human` já seta `assignee/ai_active=0` **na conversa** (por-conversa), e o gate por-conversa (pós-FA3) já cobriria o bloqueio; a tag vira redundante como trava. (a) **[recomendado]** remover a tag do gate e confiar no estado por-conversa (a tag pode continuar como rótulo visual) — elimina a classe; (b) mínimo, escopar a leitura da tag. Recomendação: **(a)**.
- **P2** — **Semântica do toggle-ai (contato) num contexto multicanal**: (a) ancorar card+`ai_active` no canal aberto do painel, ou (b) espelhar em **todas** as conversas abertas do contato? · ⏸️ **A DECIDIR (F0).** O toggle é conceitualmente **do contato** (flip `contact.ai_enabled`), então (b) é o mais coerente com a intenção; (a) é mais simples e "surpreende menos" o operador que agiu num fio. Recomendação: **(b)** (espelhar em todas), com o card emitido em cada conversa aberta (ou ao menos no fio ativo). Decidir antes de FB3.
- **P3** — **Endpoints por-phone** (`GET /contacts/{phone}/conversation` [A12] e `PUT /contacts/{phone}/tags` [A11]): torná-los channel-aware (aceitar `channel_id`/`conversation_id`) ou aceitar como **legado por-contato** (card cosmético)? · ⏸️ **A DECIDIR.** Impacto real é baixo (header/notice cosmético). Recomendação: **channel-aware quando o painel já tem o `conversation_id`** (passa a existir), com fallback legado. Não bloqueia os clusters de alto impacto.
- **P4** — **Guardrail anti-regressão**: teste que proíbe novos `get_open_for_contact(`/`get_latest_for_contact(` sem escopo nos caminhos sensíveis — vale a pena? · ⏸️ **A DECIDIR (F-REG).** Recomendação: **sim** (um teste de `grep` com allow-list) — é exatamente o que faltou para o padrão não regredir de novo. Adiar só se a allow-list ficar difícil de manter.

---

## 8. Checklist de verificação

- [ ] `venv/bin/python -m pytest tests/endpoints -q` **verde no Postgres de teste** (`WHATSBOT_TEST_DB_URL`) após cada fase; scripts standalone de agente/routing rodados individualmente (ver memória "pytest tests/ não roda inteiro").
- [ ] F0 captura o misfiling (private-AI, gate, tools) **antes** de qualquer mudança; F-REG inverte todos.
- [ ] **Reprodução do bug relatado**: nota privada / "IA lê" numa conversa Telegram → resposta fica **na conversa Telegram**, sem criar conversa WhatsApp fantasma (#41).
- [ ] Cluster A: todos os `get_open_for_contact`/`get_latest_for_contact` em caminho sensível usam a variante `*_inbox` com o `inbox_id` do `ContactMemory`/`ctx.contact`; gate mantém **fail-open**.
- [ ] Cluster B: `_run_private_ai`, `send_private_audio`, toggle-ai, webhook group e transcrição threadam `channel_id`; broadcasts `new_message` carregam `channel_id`.
- [ ] Cluster C: `sendPrivateMessage`/`sendPresence` encaminham `channelId`; conversa nova em canal não-default não misfila.
- [ ] Cluster D: `transfer_to_human` num canal não silencia/reativa a IA no outro (conforme P1); single-channel inalterado.
- [ ] `ensure_ai_agent` só carimba a conversa do inbox do turno; nunca uma conversa fechada de outro canal.
- [ ] Caminho **single-channel** (comum) inalterado — comportamento idêntico ao de hoje.
- [ ] Migration (se P1-coluna) `upgrade`/`downgrade` round-trip verde; **sem** segredo em URL/log.
- [ ] Guardrail anti-regressão (P4) verde, se adotado.
- [ ] Um refactor por commit; cada fase com seu bloco "Status de execução" preenchido.

---

## 9. Apêndice — arquivos-chave (por camada)

**Data layer / repos:**
- [db/repositories/conversation_repo.py](../db/repositories/conversation_repo.py) — helpers `*_inbox` (:192,:209), `ensure_ai_agent` (:505-526). **FA6.**
- [db/repositories/message_repo.py](../db/repositories/message_repo.py) — `get_last_user_message` (escopar por conversa). **FB5.**

**Agente / motor:**
- [agent/memory.py](../agent/memory.py) — `ContactMemory.inbox_id` (:97), `_custom_attr_lines` (:543). **FA2.**
- [agent/handler.py](../agent/handler.py) — `_get_contact` (:234), `_emit_resolution_error` (:322), `_ensure_conversation_agent` (:361), `update_last_user_message_content` (:420). **FA3/FA6/FB5 — coordenar.**
- [agent/agent_factory.py](../agent/agent_factory.py) — `resolve_active_agent_key` (:204-222). **FA2.**
- [agent/tools/transfer_to_human.py](../agent/tools/transfer_to_human.py) (:66, TRANSFER_TAG :12/:60), [transferir_agente.py](../agent/tools/transferir_agente.py) (:80), [set_custom_attribute.py](../agent/tools/set_custom_attribute.py) (:92). **FA1 / FD1.**

**Serviços:**
- [app/services/messaging_service.py](../app/services/messaging_service.py) — `broadcast_tool_calls` (:448, aceita `channel_id`), broadcasts (:515,:555,:714), `_conversation_ai_active` (:1154, TRANSFER_TAG :1161), caller de transcrição (:957). **FA3/FB5/FD1.**
- [app/services/conversation_service.py](../app/services/conversation_service.py) — `toggle_contact_ai` (:485-515), `_clear_transfer_tag` (:263-279). **FB3/FD1.**
- [server/system_notices.py](../server/system_notices.py) — `resolve_conversation_for_contact` (:378, aceita `inbox_id`), `emit_for_contact` (:400). **infra pronta.**

**Rotas:**
- [server/routes/contacts.py](../server/routes/contacts.py) — `_channel_for` (:130-146), `_run_private_ai` (:1018-1147), `send_private_audio` (:1242-1320), toggle-ai (:1624-1645), presence endpoint. **FB1/FB3/FC2.**
- [server/routes/channel_webhook.py](../server/routes/channel_webhook.py) — `group_participants` (:231-239). **FB4.**
- [server/routes/tags.py](../server/routes/tags.py) (:157-160), [server/routes/conversations.py](../server/routes/conversations.py) (:667-675). **FA5 / P3.**

**Frontend:**
- [web/static/js/services/api.js](../web/static/js/services/api.js) — `sendPrivateMessage` (:264), `sendPrivateAudio` (:326, referência correta), `sendPresence` (:341), `_scopeFields`. **FC1/FC2.**
- [web/static/js/components/contacts/hooks/useComposer.js](../web/static/js/components/contacts/hooks/useComposer.js) — presence (:100/:132/:136/:142/:162), `sendPrivateMessage` (:177), params `conversationId`/`channelId` (:40). **FC1/FC2.**

**Fora de escopo (falsos positivos):** [server/routes/sandbox.py](../server/routes/sandbox.py) (:62, single-inbox intencional); tags/pin/unread por-contato (design plano 01).
