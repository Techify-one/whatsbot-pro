# Plano 49 — Popup "vincular ao protocolo anterior vs. novo" ao reabrir logo após fechar (plugin `protocolos`)

> **Status:** IMPLEMENTADO (F1 backend + F2 frontend + F3 testes) · validação manual no browser pendente · **Data:** 2026-07-15 · **Escopo:** médio
> **Origem:** pedido do usuário ("quando um protocolo é aberto logo depois de fechar um para a mesma conversa, abrir um popup perguntando se faz parte do protocolo anterior ou é um novo — e fechar a conversa e o protocolo juntos"). Feature **não existe hoje** (confirmado: sem popup, sem query por "último fechado", sem merge). **Método:** investigação multi-agente + leitura direta do código (plugin + core), com `arquivo:linha` verificado em 1ª mão.
> Adiciona, **100% dentro do plugin `protocolos`** (core intocado), um popup que aparece ao abrir uma conversa cujo contato teve um protocolo **fechado há pouco** (janela configurável), perguntando ao operador: **(a)** faz parte do protocolo anterior (reabre o anterior e absorve o atendimento novo) ou **(b)** é um protocolo novo — mais um botão **(c)** "fechar conversa e protocolo juntos" reusando o encadeamento já existente.
> **Como usar este plano:** ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões do usuário / travadas (recomendações do plano — abrir só se o usuário discordar)

Estas são as **recomendações** deste plano (o usuário pode sobrescrever qualquer uma; ver §7 Perguntas em aberto para o racional completo). Enquanto não houver contra-ordem, o executor segue por elas.

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | **Gatilho no FRONTEND**, via o evento de plugin `ui.conversation.opened` (já emitido pelo core) + endpoint de consulta. ✅ | **Zero mudança no core.** O plugin escuta `api.on('ui.conversation.opened', …)` e decide mostrar o popup. Nada bloqueia o pipeline síncrono do backend. |
| D2 | **"Logo após" = janela em minutos configurável** (default 30), medida por `closed_at`. ✅ | Nova config `plugin.protocolos.relink_window_minutes` + toggle `relink_prompt_enabled`. Sem popup em reengajamentos tardios (que são legitimamente atendimento novo). |
| D3 | **"Faz parte do anterior" = REABRIR o protocolo anterior** (reusa `reopen_protocolo`), movendo o(s) ciclo(s) do protocolo novo para ele e **descartando o protocolo novo transitório**. ✅ | Sem migration/coluna nova. Ordem obrigatória por causa do índice único (ver ⚠️ gotcha 1). |
| D4 | **"Fechar conversa e protocolo juntos" = 3º botão no popup** reusando o encadeamento `forceResolveAndClose` já existente. ✅ | Nenhuma rota nova para isso; reusa `/resolve` → `setConversationStatus('closed')` → `/protocolos/{id}/close`. |
| D5 | **Sem migration no MVP.** ✅ | Detecção usa `closed_at` + `contact_id`, que já existem ([001_initial.sql:18](../storages/plugins/protocolos/migrations/001_initial.sql#L18)). Migration 016 só se o usuário exigir cadeia auditável (P3 alternativa b). |
| D6 | Princípio do repo: nada em produção quebra ⇒ **aditivo**, sem stopgap; core intocado. | Toda mudança vive em `storages/plugins/protocolos/`. Restart do plugin após mexer em `entry.*`/settings. |

---

## 1. Resumo executivo

Hoje o protocolo novo nasce automaticamente e **em silêncio** num único chokepoint — [`ensure_protocolo_for_contact`](../storages/plugins/protocolos/logic.py#L597) — sempre que o contato manda mensagem (`on_inbound`), o operador/IA responde (`on_outbound`) ou uma conversa é resolvida, **desde que não haja protocolo aberto**. Não existe pergunta, nem noção de "acabou de fechar e o cliente voltou". A solução: um **popup no painel de chat** disparado quando o operador **abre uma conversa** ([ui.conversation.opened](../web/static/js/components/contacts/ContactDetail.js#L158)) cujo contato tem um protocolo **fechado dentro de uma janela** (default 30 min). O popup oferece 3 ações — **(a)** vincular ao anterior (reabre o anterior via [reopen_protocolo](../storages/plugins/protocolos/logic.py#L907), move os ciclos e descarta o novo), **(b)** manter como protocolo novo (dispensa), **(c)** fechar conversa+protocolo juntos (reusa [forceResolveAndClose](../storages/plugins/protocolos/static/protocolos_tab.js#L860)). Backend: 1 helper de consulta + 1 orquestrador `relink`, 2 rotas, 2 settings. **O core não muda** — `ui.conversation.opened`, `api.ui.openModal`, `api.services.setConversationStatus`, `api.on` e `api.http` já existem e são consumidos por outros plugins.

---

## 2. Como funciona hoje (mapa)

| Peça | Onde (`arquivo:linha`) | Comportamento atual |
|------|------------------------|---------------------|
| **Nasce protocolo novo (chokepoint único)** | [logic.py:597-630](../storages/plugins/protocolos/logic.py#L597) | Get-or-create race-safe. Se não há aberto, `INSERT status='aberto', opened_at` (612-618). Só quem criou (`created and announce_open`, 623) grava nota + emite `protocolo_opened` (624-629). |
| Seleciona **aberto** | [logic.py:573-581](../storages/plugins/protocolos/logic.py#L573) | `WHERE contact_id AND status='aberto'`. ⚠️ **Não há equivalente para `status='fechado'`** — precisa criar. |
| `on_inbound` (`message.saved`) | [logic.py:1947-1961](../storages/plugins/protocolos/logic.py#L1947) | Cliente voltou após fechado → cria protocolo NOVO + ciclo. |
| `resolve_atendimento` | [logic.py:1856-1918](../storages/plugins/protocolos/logic.py#L1856) | ⚠️ **Resolver a conversa AUTO-cria protocolo aberto** (`announce_open=True`, 1875-1877). |
| `close_protocolo` | [logic.py:851-904](../storages/plugins/protocolos/logic.py#L851) · rota [routes.py:171-199](../storages/plugins/protocolos/routes.py#L171) | `UPDATE status='fechado', closed_at=:ts` (889-890). ⚠️ **Bloqueia (HTTP 400) se há ciclo aberto resolvível** (862-867). Corpo opcional `{reactivate_ai}` — seam do "Finalizar atendimento" (174-198). |
| `reopen_protocolo` | [logic.py:907-926](../storages/plugins/protocolos/logic.py#L907) · rota [routes.py:202-207](../storages/plugins/protocolos/routes.py#L202) | ⚠️ **Recusa se `_select_open_protocolo` acha outro aberto** (913-915) / `IntegrityError` (924-925). `UPDATE status='aberto', closed_at=NULL` (920-921). **É o gancho de "vincular ao anterior".** |
| Ciclo (vínculo) | coluna `protocolo_id` em [001_initial.sql:38](../storages/plugins/protocolos/migrations/001_initial.sql#L38); único-por-conversa **removido** em [002_cycles.sql:6](../storages/plugins/protocolos/migrations/002_cycles.sql#L6) | Repontar ciclo p/ outro protocolo = 1 `UPDATE`; N ciclos por conversa são permitidos. |
| **Popup atual (só resolução)** | [extends.js:49-108](../storages/plugins/protocolos/static/extends.js#L49) | `filter.conversation.beforeResolve` (só no `status==='closed'`): `api.ui.openModal(ResolveForm)` (81-84), `null` aborta o fechar (85). |
| Funil core do popup | [resolveConversation.js:13-20](../web/static/js/utils/resolveConversation.js#L13) | Aplica `beforeResolve` **só no fechar**; reabertura/retorno do cliente **nunca** passa por aqui. |
| Molde do popup (multi-ação) | [resolve_form.js:133-188](../storages/plugins/protocolos/static/resolve_form.js#L133) | Overlay próprio; **2 botões de saída** ("Resolver" / "Resolver e ir") — precedente exato de popup com várias ações. |
| Modal host | [ModalHost.js:30-46](../web/static/js/plugins/ModalHost.js#L30) · exposto em [api.js:180](../web/static/js/plugins/api.js#L180) | `openModal(renderFn) → Promise`; `close(v)` resolve. |
| **Eventos UI p/ plugin** | [registry.js:55-57](../web/static/js/plugins/registry.js#L55) · emitidos em [ContactDetail.js:158-159](../web/static/js/components/contacts/ContactDetail.js#L158) e [useConversationSelection.js:84,97](../web/static/js/components/contacts/hooks/useConversationSelection.js#L97) | `ui.conversation.opened/selected/closed` `{conversationId, phone, channelId}`. Plugin assina via `api.on(name, fn)` ([api.js:171](../web/static/js/plugins/api.js#L171)). **É o gatilho do popup.** |
| Fechar conversa (core) | [conversation_service.set_status](../app/services/conversation_service.py#L139) | Fecha/reabre, aplica `before_status`, broadcast + card. **Não** usar `conversation_repo.set_status` cru (sem broadcast → dessincroniza UI). |
| **Encadeamento "fechar tudo" (já existe!)** | [protocolos_tab.js:860-895](../storages/plugins/protocolos/static/protocolos_tab.js#L860) | `forceResolveAndClose`: `/atendimentos/{conv}/resolve` → `api.services.setConversationStatus(conv,'closed')` → `/protocolos/{id}/close`. **Reusável no botão (c).** |
| Broadcast do plugin | [logic.py:2802-2804](../storages/plugins/protocolos/logic.py#L2802) | `plugin_protocolos_changed {contact_id, protocolo_id}`. Assinado só em [protocolos_tab.js:628-629](../storages/plugins/protocolos/static/protocolos_tab.js#L628) (aba Protocolos, não no chat). |
| Superfície `api` do plugin | [api.js:165-195](../web/static/js/plugins/api.js#L165) | `on` (171), `emit` (172), `http` (177), `ui.openModal` (180), `services` (192: `useWebSocket`, `hasPermission`, `setConversationStatus`, `getConversation`…). |

**⚠️ Gotchas que tornam algo obrigatório:**
1. **Índice único parcial "1 aberto por contato"** ([001_initial.sql:25-27](../storages/plugins/protocolos/migrations/001_initial.sql#L25)) — reabrir o anterior **exige** que não exista outro aberto. O fluxo de vínculo **tem** que descartar/mover o protocolo novo **antes** do `reopen_protocolo`, senão `IntegrityError`.
2. **`announce_open` idempotente** (623) — `reopen_protocolo` **não** passa por announce_open, então **não** grava nota nova. Um vínculo ao anterior deve emitir seu próprio aviso (system notice) se quiser marca no fio.
3. **Resolver conversa auto-cria protocolo** (1875-1877) — o cenário "fechou → cliente volta → operador resolve a conversa nova" já materializa o protocolo novo; a detecção por `closed_at` cobre isso (o anterior fechado ainda está na janela).
4. **`close_protocolo` bloqueia com ciclo aberto vivo** (862-867) — "fechar tudo" precisa resolver o ciclo antes (é o que `forceResolveAndClose` já faz).
5. **`ui.conversation.opened` dispara a cada mount** do ContactDetail — o popup precisa de um **guard de "já perguntei"** por (contato, protocolo anterior) para não repetir a cada troca de conversa.
6. **Restart do plugin** no toggle/`entry.*`/settings — mudanças só valem após relançamento pelo supervisor.

---

## 3. Inventário / análise

| # | Item | Ponto de mudança (`arquivo:linha`) | O que falta | Abordagem | Risco | Esforço |
|---|------|-----------------------------------|-------------|-----------|-------|---------|
| I1 | Consulta "último fechado na janela" | **novo** helper em [logic.py:573](../storages/plugins/protocolos/logic.py#L573) | Não existe query por `status='fechado'` | `get_last_closed_protocolo_for_contact(cid)` → `SELECT … WHERE status='fechado' ORDER BY closed_at DESC LIMIT 1` | Baixo | S |
| I2 | Orquestrador de vínculo | **novo** em [logic.py](../storages/plugins/protocolos/logic.py#L907) (perto de `reopen_protocolo`) | Não há `move/discard/merge` | `relink_to_previous(previous_id, current_open_id?)`: valida contato/estado → `UPDATE atendimentos.protocolo_id` → descarta o novo → `reopen_protocolo(prev)` → notice + `_broadcast_changed` | **Médio** | M |
| I3 | Descartar protocolo transitório | **novo** helper `_discard_protocolo(id)` | Não há `delete_protocolo` (só `delete_kanban_view`) | `DELETE FROM plugin_protocolos_protocolos WHERE id=:id AND status='aberto'` + limpa `plugin_protocolos_protocolo_extras` | Médio | S |
| I4 | Config (janela + toggle) | [settings.py:52](../storages/plugins/protocolos/settings.py#L52) | Não existe | `relink_prompt_enabled: bool=True` + `relink_window_minutes: int=30` (o `PluginSettingsForm` renderiza bool/int) | Baixo | S |
| I5 | Rota de sugestão | [routes.py:117-153](../storages/plugins/protocolos/routes.py#L117) (bloco Protocolos) | Não existe | `GET /contacts/{cid}/relink-suggestion` (gate `view`) → contrato §4 | Baixo | S |
| I6 | Rota de vínculo | [routes.py:202](../storages/plugins/protocolos/routes.py#L202) | Não existe | `POST /protocolos/{previous_id}/relink` (gate `edit`) → chama I2 | Baixo | S |
| I7 | Gatilho + popup no chat | [extends.js:35-109](../storages/plugins/protocolos/static/extends.js#L35) | Nenhum listener de `ui.conversation.opened` | `api.on('ui.conversation.opened', …)` → `GET suggestion` → `api.ui.openModal(RelinkModal)`; guard "já perguntei" (Set em memória por `prev_id`) | Médio | M |
| I8 | Componente do popup | **novo** `static/relink_modal.js` | Não existe | Molde de [resolve_form.js:133](../storages/plugins/protocolos/static/resolve_form.js#L133); 3 botões (a/b/c); `wa-*`/`.wa-field` (modo escuro) | Baixo | M |
| I9 | Botão "fechar tudo" | reusa [protocolos_tab.js:860](../storages/plugins/protocolos/static/protocolos_tab.js#L860) | Lógica está acoplada à aba | Extrair/replicar o encadeamento `forceResolveAndClose` p/ o popup (ou expor helper compartilhado) | Médio | M |
| I10 | Toggle na config UI | [config.js:75-76,374-376](../storages/plugins/protocolos/static/config.js#L374) (aba "Geral") | — | Espelha os toggles bool + input de minutos, PUT em `/general-config` (ou usa settings declarativas de I4) | Baixo | S |
| I11 | Testes backend | **novo** `tests/test_protocolos_popup.py` | Sem cobertura de `relink`/`reopen`/janela | Fixture `REAL_PLUGIN_EXAMPLES→storages/plugins` + `build_app(["gowa","protocolos"])` (molde [test_avaliacao_protocolo.py:30-32](../tests/test_avaliacao_protocolo.py#L30)) | Baixo | M |
| I12 | Teste puro (opcional) | **novo** `static/relink.test.js` | — | Extrair `decideRelink(prevClosedAt, now, windowSecs) → bool` puro; `node --test` (molde `constants.test.js`) | Baixo | S |

### Falsos positivos descartados

| Suspeita | Por que NÃO é o caminho |
|----------|-------------------------|
| Reusar `filter.conversation.beforeResolve` para o retorno do cliente | Só dispara no **fechar** ([resolveConversation.js:14](../web/static/js/utils/resolveConversation.js#L14)). Reabertura/retorno **nunca** passa por ele — serve só para o botão (c) "fechar tudo". |
| Reusar `skip_open` (regex "ignorar abertura") | É o **oposto**: suprime a abertura do protocolo ([logic.py:1951](../storages/plugins/protocolos/logic.py#L1951)). Ortogonal à feature. |
| Interceptar `on_inbound` para abrir o popup | `on_inbound` roda em `asyncio.to_thread`, sem UI e possivelmente sem operador olhando — não pode "perguntar". O popup é decisão de quem **abre a conversa** no painel. |
| `conversation_status_changed` para correlacionar o contato | Payload **não traz `contact_phone`** ([conversation_service.py:82-95](../app/services/conversation_service.py#L82)). O gatilho certo é `ui.conversation.opened` (traz `phone`). |
| `conversation_repo.set_status` direto para fechar | **Evitar** — sem broadcast/card, dessincroniza sidebar/kanban ([conversation_repo.py:604-626](../db/repositories/conversation_repo.py#L604)). Usar `conversation_service.set_status`. |
| Migration/coluna nova no MVP | `closed_at` + `contact_id` já bastam para detectar (D5). Só necessária se o produto exigir cadeia pai/filho auditável (P3-b). |
| Testar contra `assets/plugin_examples/protocolos` | Espelho **defasado** do código instalado. O teste tem que apontar para `storages/plugins/protocolos` via o monkeypatch `REAL_PLUGIN_EXAMPLES` ([test_avaliacao_protocolo.py:24-32](../tests/test_avaliacao_protocolo.py#L24)). |
| Adicionar seam/slot novo no core p/ o popup | Desnecessário: `ui.conversation.opened` + `api.ui.openModal` + `api.on` **já existem** e já são usados por plugins. Core intocado (D1). |

---

## 4. Contrato (fixo — backend e frontend podem ser feitos em paralelo contra ele)

```
GET /api/plugins/protocolos/contacts/{contact_id}/relink-suggestion        (gate: view)
200 { "ok": true, "data": {
        "suggest": true,                       # há protocolo fechado dentro da janela?
        "window_minutes": 30,
        "seconds_since_close": 412.7,          # ou null
        "previous":     { "id": 41, "closed_at": 1.7e9, "opened_at": 1.7e9,
                          "assignee_name": "Atendente", "atendimentos_count": 3 },   # ou null
        "current_open": { "id": 57, "opened_at": 1.7e9 }                        # protocolo novo já aberto, ou null
     }}

POST /api/plugins/protocolos/protocolos/{previous_id}/relink                 (gate: edit)
body: { "current_open_id": 57 }               # opcional: o protocolo novo a absorver (se já existir)
→ move ciclos do current_open p/ previous, descarta current_open, reopen(previous), emite notice
200 { "ok": true,  "data": { "protocolo": <previous reaberto> } }
409 { "ok": false, "error": "O protocolo anterior não pertence a este contato / não está fechado." }
404 { "ok": false, "error": "Protocolo não encontrado." }

# (b) "É protocolo novo": SEM chamada de backend — o front só dispensa o popup e registra o guard
#     (não reperguntar para este par contato/protocolo-anterior nesta sessão).
# (c) "Fechar conversa e protocolo juntos": SEM rota nova — reusa o encadeamento forceResolveAndClose:
#       POST /atendimentos/{conv}/resolve → api.services.setConversationStatus(conv,'closed') → POST /protocolos/{id}/close
```

Regras do `relink_to_previous(previous_id, current_open_id=None)` (pseudo, sem implementar):
1. `prev = get_protocolo(previous_id)`; se não existe → 404. Se `prev.status != 'fechado'` ou `prev.contact_id != contato` → 409.
2. Se `current_open_id`: valida `contact_id` igual e `status='aberto'`; **move os ciclos** `UPDATE plugin_protocolos_atendimentos SET protocolo_id=:prev WHERE protocolo_id=:current`; depois `_discard_protocolo(current_open_id)` (DELETE do protocolo + extras). ⚠️ **Ordem obrigatória**: descartar o novo **antes** de reabrir o anterior (índice único — gotcha 1).
3. `reopen_protocolo(previous_id)` (agora sem colisão) → `UPDATE status='aberto', closed_at=NULL`.
4. Emite aviso de sistema (novo tipo `protocolo_relinked`, ver P6) na conversa mais recente + `_broadcast_changed`.
5. Retorna o `previous` reaberto.

---

## 5. Fases / Roadmap

```
WAVE 0   F1(backend: logic+rotas+settings) · F2(frontend: gatilho+popup+config)   ← contrato §4 fixo ⇒ paralelos
                 │                                    │
                 └──────────────┬─────────────────────┘   (barreira: F3 precisa de F1[+F2])
WAVE 1                    F3(testes + verificação manual)
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando / dependências |
|------|------|-----------|-------|-------|------------------------------|
| 0 | F1 | Backend: `logic` (I1–I3) + rotas (I5–I6) + settings (I4) | 🟢 [contrato §4] | Médio | `curl` do §4 responde; `relink` reabre o anterior e some o novo |
| 0 | F2 | Frontend: gatilho `on(ui.conversation.opened)` (I7) + popup (I8–I9) + config (I10) | 🟢 [contrato §4; `[bloqueia: F3]`] | Médio | popup aparece ao abrir conversa com fechado recente; 3 botões funcionam |
| 1 | F3 | Testes (I11–I12) + verificação manual | 🔴 [depende de: F1, F2] | Baixo | suíte verde no Postgres + fluxo manual ok |

> 🟢 = despachar junto (F1 e F2 não compartilham arquivo; a fronteira é o contrato §4). 🔴 = sequencial (F3 só com F1+F2 prontos).

---

### Fase 1 — Backend: consulta + vínculo + settings 🟢
**Objetivo:** o backend detecta o "fechado recente" e executa o vínculo ao anterior de forma segura (índice único respeitado).
**Itens:**
1. `[paralelo]` **I1** `get_last_closed_protocolo_for_contact(contact_id)` ao lado de [_select_open_protocolo](../storages/plugins/protocolos/logic.py#L573) — `WHERE status='fechado' ORDER BY closed_at DESC LIMIT 1`, via `_proto_dict`.
2. `[sequencial]` **I3** `_discard_protocolo(id)` — `DELETE` da linha `aberto` + suas `plugin_protocolos_protocolo_extras`. (É protocolo recém-criado, tipicamente vazio.)
3. `[sequencial]` **I2** `relink_to_previous(previous_id, current_open_id=None)` seguindo as regras do §4 (⚠️ ordem: mover ciclos → descartar novo → `reopen_protocolo`). Emite `_broadcast_changed` + notice.
4. `[paralelo]` **I4** em [settings.py:52](../storages/plugins/protocolos/settings.py#L52): `relink_prompt_enabled: bool=True`, `relink_window_minutes: int=30`.
5. `[sequencial]` **I5/I6** rotas em [routes.py](../storages/plugins/protocolos/routes.py#L117): `GET /contacts/{cid}/relink-suggestion` (usa I1 + a janela da config) e `POST /protocolos/{previous_id}/relink` (usa I2). Formato `{ok,data|error}`, gates `view`/`edit`.

**Pronto quando:**
- `GET …/contacts/{cid}/relink-suggestion` com um protocolo fechado há < 30 min → `suggest:true` + `previous` preenchido; > 30 min → `suggest:false`.
- `POST …/protocolos/{prev}/relink {current_open_id}` → o anterior volta a `aberto`, o novo **some**, e os ciclos do novo agora apontam para o anterior (verificável por `GET /protocolos/{prev}` → `atendimentos`). Sem `IntegrityError`.
- Chamar `relink` com `previous` que não é do contato / não está fechado → **409**.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída
- **O que foi feito:** em [logic.py](../storages/plugins/protocolos/logic.py): `get_last_closed_protocolo_for_contact` (I1, `status='fechado'` ORDER BY `closed_at DESC NULLS LAST`), `_count_atendimentos_of_protocolo`, `_discard_protocolo` (I3, apaga extras + a linha `aberto`), `relink_to_previous(previous_id, current_open_id, actor)` (I2), `relink_suggestion_for_contact` (contrato §4), helpers `relink_prompt_enabled`/`relink_window_minutes` + as 2 chaves em `get_general_config`/`set_general_config`, formatter `_f_protocolo_relinked` + `register_notice("protocolo_relinked", …)` (P6). Em [routes.py](../storages/plugins/protocolos/routes.py): `GET /contacts/{cid}/relink-suggestion` (view) e `POST /protocolos/{previous_id}/relink` (edit).
- **Como foi feito / decisões:** ordem obrigatória em `relink_to_previous` respeitada (mover ciclos → `_discard_protocolo` → `reopen_protocolo`); erro mapeado 404 (“encontrado”) vs 409 (demais), como `reopen`. **Desvio de I4/I10:** as settings NÃO viraram declarativas (`settings.py`) — o plugin tem screen `config:true`, que substitui o `PluginSettingsForm`; então as 2 chaves foram para o mecanismo `/general-config` (prefixo real `plugin.protocolos.general_*`) e a UI foi para a aba Geral do `config.js` (I10 feito, I4 descartado). P6 implementado (aviso `protocolo_relinked` no grupo `protocolo_lifecycle`).
- **Problemas / pendências:** nenhuma. `set_general_config` grava as chaves novas só quando presentes (payload antigo não zera o default); `relink_window_minutes` clampado a 1..1440.
- **Verificação:** `py_compile` OK; coberto por `tests/test_protocolos_popup.py` (ver F3).

---

### Fase 2 — Frontend: gatilho + popup + config 🟢
**Objetivo:** ao abrir uma conversa com protocolo fechado recente, o operador vê o popup e escolhe a/b/c.
**Itens:**
1. `[sequencial]` **I7** em [extends.js](../storages/plugins/protocolos/static/extends.js#L35) `register(api)`: `api.on('ui.conversation.opened', async ({conversationId, phone}) => {…})` — resolve o `contact_id` (via `api.services.getConversation` ou o `phone`), chama `GET …/relink-suggestion`, e se `suggest` **e** ainda não perguntado (guard `Set` em memória por `previous.id`), abre o popup. Respeita o toggle `relink_prompt_enabled`.
2. `[paralelo]` **I8** novo `static/relink_modal.js` (molde de [resolve_form.js:133](../storages/plugins/protocolos/static/resolve_form.js#L133)): título "Este atendimento faz parte do protocolo anterior?", resumo do anterior (id, atendente, fechado há X), **3 botões**: **(a)** "Faz parte do anterior" → `api.http.post('/protocolos/{prev}/relink', {current_open_id})`; **(b)** "É um novo protocolo" → fecha o modal + marca o guard; **(c)** "Fechar conversa e protocolo" → encadeamento de I9. `wa-*`/`.wa-field` (modo escuro).
3. `[sequencial]` **I9** botão (c): reusar/replicar [forceResolveAndClose](../storages/plugins/protocolos/static/protocolos_tab.js#L860) — `/atendimentos/{conv}/resolve` → `api.services.setConversationStatus(conv,'closed')` → `/protocolos/{id}/close`. Após (a), deep-link opcional reusando `pushState('/protocolos?detail='+id)` + `popstate` (precedente [extends.js:105-106](../storages/plugins/protocolos/static/extends.js#L105)).
4. `[paralelo]` **I10** em [config.js](../storages/plugins/protocolos/static/config.js#L374) (aba Geral): toggle "Perguntar ao reabrir logo após fechar" + input de minutos (se optar por `/general-config`; se usar settings declarativas de I4, o `PluginSettingsForm` já renderiza — nesse caso pular I10).

**Pronto quando:** abrir uma conversa cujo contato fechou um protocolo há < 30 min mostra o popup; (a) reabre o anterior e o Kanban reflete (via `plugin_protocolos_changed`); (b) dispensa e não repergunta na sessão; (c) resolve+fecha a conversa e finaliza o protocolo. Legível no modo escuro.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída
- **O que foi feito:** novo [static/relink_modal.js](../storages/plugins/protocolos/static/relink_modal.js) (I8, `RelinkModal` — 3 botões a/b/c + `humanizeAgo`, estados busy/erro, `wa-*`/`.wa-field`). Em [extends.js](../storages/plugins/protocolos/static/extends.js): listener `api.on('ui.conversation.opened', …)` (I7) com guard `Set` por `previous.id`, resolução de `contact_id` via `getConversation`, `GET relink-suggestion`, `api.ui.openModal(RelinkModal)` e deep-link `/protocolos?detail=` no sucesso (a); helper de módulo `resolveAndCloseAll` (I9, replica `forceResolveAndClose`: `/atendimentos/{conv}/resolve` → `setConversationStatus('closed')` → `/protocolos/{pid}/close`). Em [config.js](../storages/plugins/protocolos/static/config.js): toggle “Perguntar ao reabrir logo após fechar” + input de minutos na aba Geral (I10) + `GENERAL_EMPTY` estendido.
- **Como foi feito / decisões:** o `ModalHost` mantém uma PILHA (`_modals[]`) → o `ResolveForm` do botão (c) empilha por cima do `RelinkModal` sem fechá-lo. Gate por `can('edit')` (mesmo do popup de resolver). Guard marcado ANTES de abrir → cancelar/dispensar também não repergunta na sessão (P7-a). `api.services.getConversation`/`setConversationStatus` estão no allowlist `PLUGIN_SERVICES` (não sensíveis).
- **Problemas / pendências:** nenhuma. Botão (c) é best-effort: erros voltam inline no modal via `{ok:false,error}`.
- **Verificação:** `node --check` OK nos 3 `.js`. Validação manual no browser (modo escuro) pendente na F3.

---

### Fase 3 — Testes + verificação manual 🔴 [depende de: F1, F2]
**Objetivo:** cobrir o backend do vínculo e validar o fluxo ponta a ponta.
**Itens:**
1. `[sequencial]` **I11** `tests/test_protocolos_popup.py` (fixture `REAL_PLUGIN_EXAMPLES→storages/plugins` + `build_app(["gowa","protocolos"])`): (a) sugestão dentro/fora da janela; (b) `relink` move ciclos + descarta o novo + reabre o anterior; (c) `relink` recusa (409) para protocolo de outro contato / não-fechado; (d) reforço do índice único (não fica com 2 abertos); (e) `close_protocolo` continua bloqueando com ciclo vivo. **Fecha lacuna**: hoje `reopen_protocolo`/`on_inbound` não têm teste.
2. `[paralelo]` **I12 (opcional)** extrair `decideRelink(prevClosedAt, now, windowSecs)` puro em `static/relink.js` + `static/relink.test.js` (`node --test`).
3. `[sequencial]` Rodar a suíte no Postgres de teste (`WHATSBOT_TEST_DB_URL`, banco com `test` no nome) e a verificação manual (`./linux_start.sh`): fechar um protocolo, mandar mensagem do cliente, abrir a conversa, ver o popup, testar a/b/c.

**Pronto quando:** `venv/bin/python -m pytest tests/test_protocolos_popup.py -q` **verde**; (se I12) `node --test storages/plugins/protocolos/static/relink.test.js` verde; fluxo manual ok; nenhum segredo na URL.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (backend) · ⏳ validação manual no browser pendente
- **O que foi feito:** novo [tests/test_protocolos_popup.py](../tests/test_protocolos_popup.py) (I11) — 6 testes: sugestão dentro/fora da janela + `previous` preenchido; toggle desligado ⇒ `suggest:false`; janela configurável (30→60); `relink` move ciclos + descarta o novo + reabre o anterior + **1 aberto** (índice único); `relink` sem `current_open_id` só reabre; recusas 409 (não-fechado / outro contato, sem efeito colateral) e 404 (inexistente). Fixture aponta para `storages/plugins` (monkeypatch `REAL_PLUGIN_EXAMPLES`) + `build_app(["gowa","protocolos"])`; seed por SQL direto nas tabelas do plugin.
- **Como foi feito / decisões:** I12 (teste puro `relink.test.js`) **descartado** — a decisão da janela é server-side (`relink_suggestion_for_contact`), não há `decideRelink` no cliente; o único puro do front (`humanizeAgo`) é trivial. Chaves de config no teste usam o prefixo real `plugin.protocolos.general_*`.
- **Problemas / pendências:** o banco de teste é COMPARTILHADO (`whatsbot_test`) e o plano 48 rodava pytest em paralelo → colisão `DROP SCHEMA` (a suíte caiu com “relation plugins does not exist” no meio). Contornado rodando num banco isolado `whatsbot_test2` (UTF8/TEMPLATE template0). Validação manual no browser (fechar protocolo → cliente volta → abrir conversa → popup a/b/c, modo escuro) ainda **pendente**.
- **Verificação:** `WHATSBOT_TEST_DB_URL=…/whatsbot_test2 venv/bin/python -m pytest tests/test_protocolos_popup.py -q` → **6 passed**. `node --check` OK nos 3 `.js`; `py_compile` OK no backend.

---

## 6. Riscos e cuidados

| Ponto | Risco | Mitigação |
|-------|-------|-----------|
| Índice único "1 aberto por contato" | Reabrir o anterior sem descartar o novo → `IntegrityError` | Ordem **obrigatória** em `relink_to_previous`: mover ciclos → `_discard_protocolo(novo)` → `reopen_protocolo(prev)` (contrato §4). Teste I11(d). |
| Descartar o protocolo novo | Deixar `plugin_protocolos_protocolo_extras` órfão | `_discard_protocolo` apaga a linha **e** os extras dela. Protocolo recém-criado normalmente já vem vazio. |
| Nota de abertura do novo | O novo já gravou "🔖 Protocolo aberto" (nota privada) antes de ser descartado → nota "fantasma" no fio | Aceitável no MVP (nota painel-only, informativa). Opcional: emitir aviso `protocolo_relinked` esclarecendo o vínculo (P6). |
| `ui.conversation.opened` a cada mount | Popup repetindo a cada troca de conversa | Guard em memória (`Set` por `previous.id`) + toggle `relink_prompt_enabled`; após `relink` o anterior deixa de estar "fechado" e a sugestão zera naturalmente. |
| Fechar conversa cru | `conversation_repo.set_status` não emite broadcast/card → UI dessincronizada | Botão (c) usa `api.services.setConversationStatus` (core), nunca o repo cru. |
| `close_protocolo` bloqueia com ciclo vivo | (c) falharia com 400 | Reusar `forceResolveAndClose` (resolve o ciclo **antes** do close) — já trata isso. |
| Concorrência (2 operadores) | Dois `relink`/close simultâneos | O `reopen` já é protegido por `IntegrityError`; `relink` é idempotente-ish (se o novo já sumiu, segue). Melhor esforço no MVP. |
| Modo escuro | `relink_modal.js` ilegível | `wa-*` + `.wa-field`; testar com `.dark` ligado (regra do CLAUDE.md). |
| Postgres-only | Suíte precisa do banco de teste | `WHATSBOT_TEST_DB_URL` com `test` no nome (trava de segurança). |
| Restart do plugin | Mudar `entry.*`/settings sem restart não aplica | `POST /api/plugins/restart` ou toggle; supervisor relança. |

---

## 7. Perguntas em aberto

- **P1 — Gatilho: frontend (`ui.conversation.opened`) ou backend (push WS)?** ✅ DECIDIDO (recomendação): **frontend** (D1). Contexto: `on_inbound` não tem UI e não pode perguntar; o popup é do operador que **abre** a conversa. Alternativa (b) push via WS novo no ramo `created` de `ensure_protocolo_for_contact` — útil para o popup aparecer **enquanto** o operador já está com o chat aberto e o cliente volta. **Recomendação:** frontend no MVP; adicionar o WS push depois como melhoria (o plugin já mantém `/ws` na aba Protocolos — [protocolos_tab.js:628](../storages/plugins/protocolos/static/protocolos_tab.js#L628)).
- **P2 — "Logo após" = janela configurável ou sempre-que-existe-fechado?** ✅ DECIDIDO: **janela em minutos** (default 30) via `closed_at` (D2). Alternativa: sempre que houver fechado recente sem novo aberto (mais ruidoso). **Recomendação:** janela; default conservador.
- **P3 — Semântica de "faz parte do anterior".** ✅ DECIDIDO: **(a) reabrir o mesmo anterior** + mover ciclos + descartar o novo (D3), sem migration. Alternativa **(b)** criar o novo marcado como filho (`parent_protocolo_id`) — preserva a cadeia auditável, mas exige **migration 016** (`ADD COLUMN parent_protocolo_id`, `linked_at`, índice `plugin_protocolos_proto_parent`; regras: prefixo `plugin_protocolos_`, sem `;` em comentário, `DOUBLE PRECISION`). **Recomendação:** (a) no MVP; migrar para (b) só se o produto exigir histórico de continuações.
- **P4 — "Fechar conversa e protocolo juntos" — ação separada?** ✅ DECIDIDO: **3º botão no popup** reusando `forceResolveAndClose` (D4). Alternativa: rota backend combinada `/finalizar-tudo` (atomicidade server-side). **Recomendação:** botão no front no MVP; rota combinada só se precisar de atomicidade.
- **P5 — Migration/coluna nova?** ✅ DECIDIDO: **não no MVP** (D5) — `closed_at`+`contact_id` bastam. Reabrir P5 apenas se P3 virar (b).
- **P6 — Aviso de sistema no fio ao vincular?** ⏸️ ADIADO (default: sim, leve). Contexto: `reopen_protocolo` não re-emite `protocolo_opened` (gotcha 2), então o vínculo fica sem marca no chat. Opções: (a) registrar um tipo `protocolo_relinked` ("🔗 Atendimento vinculado ao protocolo anterior") via `register_notice` no grupo `protocolo_lifecycle` ([logic.py:712-728](../storages/plugins/protocolos/logic.py#L712)); (b) nenhum aviso. **Recomendação:** (a) — barato e alinhado ao padrão existente de cards.
- **P7 — Guard de "não reperguntar": memória de sessão ou persistido?** ⏸️ ADIADO (default: memória). Contexto: `ui.conversation.opened` repete. Opções: (a) `Set` em memória por `previous.id` (some no reload); (b) persistir a decisão "é novo" numa coluna/config. **Recomendação:** (a) no MVP; após um reload a sugestão reaparece só enquanto o anterior estiver dentro da janela — aceitável.

---

## 8. Apêndice — arquivos-chave

**Backend (plugin)**
- [storages/plugins/protocolos/logic.py:573](../storages/plugins/protocolos/logic.py#L573) — `get_last_closed_protocolo_for_contact` (novo), `relink_to_previous` (novo), `_discard_protocolo` (novo); reuso de [reopen_protocolo:907](../storages/plugins/protocolos/logic.py#L907), [_broadcast_changed:2802](../storages/plugins/protocolos/logic.py#L2802).
- [storages/plugins/protocolos/routes.py:117](../storages/plugins/protocolos/routes.py#L117) — `GET /contacts/{cid}/relink-suggestion`, `POST /protocolos/{prev}/relink` (novas).
- [storages/plugins/protocolos/settings.py:52](../storages/plugins/protocolos/settings.py#L52) — `relink_prompt_enabled`, `relink_window_minutes` (novas).
- (P6) [storages/plugins/protocolos/logic.py:712](../storages/plugins/protocolos/logic.py#L712) — `register_notice('protocolo_relinked', …)`.

**Frontend (plugin)**
- [storages/plugins/protocolos/static/extends.js:35](../storages/plugins/protocolos/static/extends.js#L35) — `api.on('ui.conversation.opened', …)` + guard + `openModal`.
- `storages/plugins/protocolos/static/relink_modal.js` — **novo** (molde de [resolve_form.js:133](../storages/plugins/protocolos/static/resolve_form.js#L133)).
- [storages/plugins/protocolos/static/protocolos_tab.js:860](../storages/plugins/protocolos/static/protocolos_tab.js#L860) — `forceResolveAndClose` (reuso para o botão (c)).
- [storages/plugins/protocolos/static/config.js:374](../storages/plugins/protocolos/static/config.js#L374) — toggle+minutos (se via `/general-config`).

**Core (só leitura/consumo — NÃO alterar)**
- [web/static/js/components/contacts/ContactDetail.js:158](../web/static/js/components/contacts/ContactDetail.js#L158) — emite `ui.conversation.opened`.
- [web/static/js/plugins/api.js:165](../web/static/js/plugins/api.js#L165) — `on`/`http`/`ui.openModal`/`services`.
- [web/static/js/plugins/ModalHost.js:30](../web/static/js/plugins/ModalHost.js#L30) — `openModal`.
- [app/services/conversation_service.py:139](../app/services/conversation_service.py#L139) — `set_status` (via `setConversationStatus`).

**Testes**
- `tests/test_protocolos_popup.py` — **novo** (molde [test_avaliacao_protocolo.py:24-52](../tests/test_avaliacao_protocolo.py#L24)).
- `storages/plugins/protocolos/static/relink.test.js` — **novo, opcional** (`node --test`).

---

## 9. Checklist de verificação

- [ ] `GET …/relink-suggestion` → `suggest:true` dentro da janela, `false` fora.
- [ ] `POST …/protocolos/{prev}/relink` → anterior volta a `aberto`, novo **some**, ciclos repontados; **nunca** 2 abertos (índice único).
- [ ] `relink` de protocolo de outro contato / não-fechado → **409**.
- [ ] Popup aparece ao **abrir** a conversa com fechado recente; **não** aparece fora da janela nem com o toggle desligado.
- [ ] Botão (a) vincula e o Kanban/painel refletem ao vivo (`plugin_protocolos_changed`).
- [ ] Botão (b) dispensa e **não repergunta** na sessão.
- [ ] Botão (c) resolve o ciclo, fecha a conversa (via `setConversationStatus`) e finaliza o protocolo — sem cair no bloqueio de "ciclo aberto".
- [ ] `relink_modal.js` legível no **modo escuro** (`wa-*`/`.wa-field`).
- [ ] `venv/bin/python -m pytest tests/test_protocolos_popup.py -q` **verde** no Postgres (`WHATSBOT_TEST_DB_URL`).
- [ ] (Se I12) `node --test storages/plugins/protocolos/static/relink.test.js` **verde**.
- [ ] Nenhum segredo na URL; core **não** foi tocado; plugin reiniciado após settings/`entry.*`.
- [ ] Reload / voltar-avançar do navegador não quebra o fluxo do popup.
