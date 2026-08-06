# Plano 107 — O Kanban/lista do protocolos atualiza ao vivo, e plugin ganha um jeito suportado de escutar o /ws

> **Status:** ✅ **EXECUTADO** (2026-08-06) — F0–F7 feitas; F8 adiada (P1·a) e F7·3 dispensada com justificativa. Falta a **validação manual com dois operadores** e a resposta do **P5**. · **Data do plano:** 2026-08-05 · **Escopo:** médio (quase tudo no plugin; zero migration)
> **Origem:** relato do usuário — (a) campo novo criado na configuração do `protocolos` não aparece para quem já está com a tela de "Resolver atendimento" aberta; (b) lead novo não aparece no Kanban de quem está com a tela aberta; (c) mover um card não reflete na tela de outro operador. **Método:** investigação em 7 lentes paralelas com verificação adversarial + leitura em 1ª mão do código (`arquivo:linha`), incluindo a comparação das TRÊS cópias do plugin por CONTEÚDO (não por número de versão).
> Os três casos têm causas **diferentes**, e nenhuma delas é "falta de infraestrutura de WebSocket". O motor existe e é bom. O que falta são **elos** — um transporte autenticado no cliente, dois produtores de evento que não emitem, e uma revalidação de catálogo. O caso (c) está a **um import** de funcionar: o backend já emite corretamente nos 5 gestos de arrastar.
>
> **Como usar este plano**: ao executar cada fase, preencha o "Status de execução" dela ANTES de passar para a próxima — nunca avance deixando a anterior sem registro.

---

## 0. Decisões travadas (não reabrir)

| # | Decisão | Consequência no plano |
|---|---------|------------------------|
| D1 | ✅ **Transporte = `wsBus` do core, nunca socket cru.** O plugin importa `subscribe` por URL absoluta (`/static/js/services/wsBus.js`) | É **precedente provado no repo**: `melhorias` ([panel.js:14](../storages/plugins/melhorias/static/panel.js#L14), [chat.js:14](../storages/plugins/melhorias/static/chat.js#L14)) e `retornos` ([retornos.js:12](../assets/plugin_examples/retornos/static/retornos.js#L12)) já fazem isso, e o comentário do `melhorias` documenta ter passado por **este mesmo bug**. O CLAUDE.md autoriza plugin importar utilitário do core por URL absoluta. Ganha token, heartbeat 25/40s, reconexão e socket único de graça |
| D2 | ✅ **A correção dos 3 casos NÃO toca o core.** Tudo em `storages/plugins/protocolos/` | Aplica a regra de decisão do CLAUDE.md ("tudo que puder ir para o plugin vai só para o plugin"). O seam formal em `PLUGIN_SERVICES` é fase **posterior e opcional** (F7), não pré-requisito |
| D3 | ✅ **A entrega é `reload()` grosso, não patch cirúrgico de card** | Quem arrasta **já paga esse reload hoje** ([protocolos_tab.js:1257](../storages/plugins/protocolos/static/protocolos_tab.js#L1257) — `applyDrop` não tem atualização otimista). Estender aos outros não é regressão, é a experiência atual para mais gente. O refinamento é a F8, opcional |
| D4 | ✅ **Debounce com jitter é obrigatório na MESMA fase que liga o transporte** | `bump_generation()` invalida o índice do Kanban em **todas as réplicas** ([kanban_index.py:85-100](../storages/plugins/protocolos/kanban_index.py#L85)); N operadores refazendo o índice ao mesmo tempo reconstroem uma varredura de até **20.000** protocolos (`DEFAULT_SCAN_CAP`) N vezes. Ligar sem debounce troca um bug de UX por um de carga |
| D5 | ✅ **Emitir na CRIAÇÃO do protocolo, nunca por mensagem** | O código já avisa: o broadcast de `stamp_provisional_assignee` é condicional a `changed` justamente porque "bumpar a geração do índice a cada mensagem derrubaria o cache do Kanban de todas as réplicas sem motivo" ([logic.py:1921-1929](../storages/plugins/protocolos/logic.py#L1921)). O emit novo entra **dentro do ramo de INSERT**, não no corpo do get-or-create |
| D6 | ✅ **Fail-open: o botão "Atualizar" e o F5 continuam sendo a rede de segurança** | Nada neste plano pode deixar a tela pior do que hoje. Evento que não chega ⇒ comportamento atual (manual), nunca tela travada ou vazia |
| D7 | ✅ **Não introduzir `performer` no payload agora** | Atribuir a ação ao autor é o §7.2 item 16 do [plano 44](44-avaliacao-realtime-websocket-vs-chatwoot.md) e pertence a ele. Aqui o autor recarrega junto com os demais — que é exatamente o que ele já faz hoje |
| D8 | ✅ **Independente do [plano 90](90-plano-escopo-do-websocket-por-canal.md)** (escopo do WS por canal) | O 90 trata de vazamento de PII entre operadores; é ortogonal e **não é pré-requisito**. Quando for executado, `plugin_protocolos_changed` cai no fail-open do D3 dele ("evento não classificado ⇒ entrega"), então este plano não cria dívida para aquele |

---

## 1. Resumo executivo

O `protocolos` **já tem** quase todo o tempo real construído: `_broadcast_changed()` ([logic.py:5050-5060](../storages/plugins/protocolos/logic.py#L5050)) invalida o índice do Kanban e emite `plugin_protocolos_changed` a partir de **16 pontos**, cobrindo os 5 gestos de arrastar; e o Kanban **já escuta** esse evento para chamar `reload()` + `loadViews()` ([protocolos_tab.js:894](../storages/plugins/protocolos/static/protocolos_tab.js#L894)).

A corrente quebra em três lugares independentes:

1. **O transporte** — o Kanban abre `new WebSocket('/ws')` **sem `?token=`** ([:893](../storages/plugins/protocolos/static/protocolos_tab.js#L893)) e o servidor fecha com **4401** assim que existe ≥1 usuário ([websocket.py:23-31](../server/routes/websocket.py#L23)). Falha silenciosa e **permanente** (não há `onerror`, `onclose` nem reconexão). **Medido ao vivo** durante a investigação, contra o servidor local com 7 usuários no banco: sem token ⇒ `CLOSED code=4401`; com token de sessão ⇒ conexão aberta recebendo frames. Isso sozinho explica o caso **(c)** por inteiro.
2. **Dois produtores falhos** — salvar a definição de um campo **não emite nada**; e o nascimento de um lead emite **por efeito colateral, só quando a conversa já tem dono** (§2.3 — corrigido na verificação adversarial; a leitura ingênua "não emite nada" está errada e levaria a um emit duplicado).
3. **Um catálogo congelado** — as definições de campo são carregadas uma vez no mount e nunca revalidadas.

A solução é ligar o transporte pelo caminho que outros dois plugins já usam, acrescentar os dois emits que faltam, revalidar o catálogo — e proteger o refetch com debounce, guarda de arrasto e guarda de modal aberto.

---

## 2. Como funciona hoje (mapa)

### 2.1 O motor (sadio, não mexer)

| Peça | Estado | Locator |
|---|---|---|
| `plugins.context.broadcast` | ✅ thread-safe via `run_coroutine_threadsafe`, nunca levanta | [plugins/context.py:146-157](../plugins/context.py#L146) |
| `ConnectionManager.broadcast` | ✅ serializa 1×, `gather` com timeout 5s por socket, poda half-open | [server/state.py:68-87](../server/state.py#L68) |
| `wsBus` do cliente | ✅ singleton por aba, token, heartbeat 25/40s, reconexão 3s, resync | [wsBus.js:67-72,82,151](../web/static/js/services/wsBus.js#L67) |
| `_broadcast_changed` do plugin | ✅ bump da geração **antes** do emit (o refetch já vê a geração nova, em todas as réplicas) | [logic.py:5050-5060](../storages/plugins/protocolos/logic.py#L5050) |

⚠️ **O custo já foi medido** (instrumentação do app real, webhook → resposta da IA): **10 broadcasts / 5,0 KB por mensagem inbound** (11 na primeira da conversa), dos quais 77% são três `conversation_upsert` de ~1,3 KB. O tráfego que este plano acrescenta é de ordem muito menor — mas o *refetch* que ele dispara não é (ver D4).

### 2.2 Caso (c) — mover card: o backend está 100% pronto

| Gesto no Kanban | Rota | Função | Emite? |
|---|---|---|---|
| coluna de campo de opção ("frio"→"morno") | `POST /protocolos/{id}/set-field` | `set_protocolo_field` | ✅ [logic.py:3014](../storages/plugins/protocolos/logic.py#L3014) |
| coluna de atendente (→ Gabriel) | `POST /protocolos/{id}/assign` | `assign_protocolo` | ✅ [logic.py:2021](../storages/plugins/protocolos/logic.py#L2021) |
| coluna de sub-agente de IA | `POST /protocolos/{id}/assign-ai` | `assign_protocolo_ai` | ✅ [logic.py:2081](../storages/plugins/protocolos/logic.py#L2081) |
| coluna Fechado / Aberto | `close` / `reopen` | `close_protocolo` / `reopen_protocolo` | ✅ [:1329](../storages/plugins/protocolos/logic.py#L1329), [:1371](../storages/plugins/protocolos/logic.py#L1371) |

✅ **5 de 5.** E como o **agrupamento roda no servidor** ([grouping.py](../storages/plugins/protocolos/grouping.py) — `status` / `atendente` / `data` / `pfield`), o cliente não precisa de nenhuma lógica nova de reposicionamento: ele refaz o fetch e o card volta na coluna certa, qualquer que seja o agrupamento.

😖 **Ironia útil:** mover um card **já atualiza ao vivo** a tela de *Atendimentos* dos outros operadores — `assign_protocolo` também emite `conversation_assigned` ([logic.py:1983](../storages/plugins/protocolos/logic.py#L1983)), e esse consumidor está no barramento **autenticado** do core. Só o Kanban, que usa o socket cru, fica de fora. Metade do tempo real já funciona; a metade quebrada é exatamente a que passa pelo transporte errado.

### 2.3 Caso (b) — lead novo: o emissor é **indireto, condicional e acidental**

> ⚠️ **Esta seção foi corrigida na verificação adversarial.** A leitura inicial ("o nascimento não emite nada") é **falsa** — cinco verificadores independentes a refutaram, e a refutação está certa. Um plano baseado nela acrescentaria um emit duplicado.

O lead novo nasce do inbound: `message.saved` → `on_inbound` ([logic.py:3315](../storages/plugins/protocolos/logic.py#L3315)) → `ensure_protocolo_for_contact` ([logic.py:973-1018](../storages/plugins/protocolos/logic.py#L973)). Essa função de fato **não** chama `_broadcast_changed`. Mas `on_inbound` **não termina ali**: na linha seguinte ([:3319](../storages/plugins/protocolos/logic.py#L3319)) ele chama `_sync_provisional_from_conv` ([logic.py:1932-1951](../storages/plugins/protocolos/logic.py#L1932)), que compara o provisório em memória e, divergindo, chama `stamp_provisional_assignee` — cujo `_broadcast_changed` é **condicional a `changed`** ([logic.py:1926-1928](../storages/plugins/protocolos/logic.py#L1926)).

Num protocolo recém-criado os campos `provisional_*` são NULL, então:

| Conversa que originou o lead | `changed` | Kanban dos outros é avisado? |
|---|---|---|
| já tem atendente humano **ou** sub-agente de IA carimbado | ✅ True | ✅ sim — por efeito colateral |
| nasce **sem** atendente e **sem** agente (IA desligada no canal/conversa — ver "Nascimento com IA desligada" no CLAUDE.md) | ❌ False | ❌ **não** |

⚠️ **É por isso que o sintoma é intermitente** — não é "nunca aparece", é "aparece quando a conversa já tinha dono". O conserto **não** é acrescentar um emit ingênuo: no caso comum isso emitiria **duas vezes** por lead novo (dois bumps de geração ⇒ o índice do Kanban derrubado duas vezes em todas as réplicas). Ver F3 e R12.

⚠️ O `broadcast("new_message", …)` disparado por perto ([logic.py:1073](../storages/plugins/protocolos/logic.py#L1073)) é o **balão do chat**, não o Kanban. Fácil de confundir ao ler.

⚠️ `open_new_protocolo` (criação **manual**, [logic.py:1614](../storages/plugins/protocolos/logic.py#L1614)) tem emit **próprio e determinístico** — é o contraste que mostra o que falta no caminho automático.

### 2.4 Caso (a) — campo novo: DUAS causas, e uma assimetria que confunde

| Ponto | Estado | Locator |
|---|---|---|
| `PUT /field-defs` grava e retorna — **nenhum broadcast, nenhum bump de geração** | ❌ | [routes.py:635-644](../storages/plugins/protocolos/routes.py#L635) → [logic.py:421-459](../storages/plugins/protocolos/logic.py#L421) |
| `loadMeta` carrega as defs **uma vez no mount**; deps `[apiBase, getJson]`, efeito com deps `[loadMeta, canView]` — `reloadTick` **não** está lá | ❌ | [protocolos_tab.js:652-677](../storages/plugins/protocolos/static/protocolos_tab.js#L652) |
| O handler de WS chama `reload()` + `loadViews()`, **nunca `loadMeta()`** | ❌ | [:894](../storages/plugins/protocolos/static/protocolos_tab.js#L894) |
| Popup "Resolver" aberto pelo **CHAT** busca as defs frescas a **cada clique** | ✅ | [extends.js:94,226](../storages/plugins/protocolos/static/extends.js#L94) |
| Popup "Resolver" aberto pelo **KANBAN** consome o snapshot de `loadMeta` | ❌ | [:1160-1162](../storages/plugins/protocolos/static/protocolos_tab.js#L1160) |

⚠️ **É essa assimetria que faz o sintoma parecer aleatório** ("às vezes atualiza"): depende de por onde o operador abriu o popup. Ver **P5** — qual das duas superfícies o usuário viu ainda não foi confirmado com ele.

✅ **O conserto não precisa inventar nada:** o botão **Atualizar** da própria toolbar já faz exatamente o par certo — `onClick={() => { loadMeta(); reload(); }}` ([:1536](../storages/plugins/protocolos/static/protocolos_tab.js#L1536)). O handler de WS só chama a metade (`reload()`). A F4 é espelhar o botão que já existe.

⚠️ **Efeito colateral no Kanban:** as colunas de um agrupamento por campo de opção saem de `d["options"]` da def ([grouping.py:283-307](../storages/plugins/protocolos/grouping.py#L283)), assada dentro do índice cacheado por `(view, filtros, geração, tz)`. Como o `PUT /field-defs` não bumpa a geração, **acrescentar uma opção nova não cria a coluna nova** até o TTL de 30s expirar.

### 2.5 O mesmo defeito de transporte em mais dois lugares

| Onde | Escuta | Situação |
|---|---|---|
| [agendamento_retorno/ScheduleTabs.js:131](../storages/plugins/agendamento_retorno/static/ScheduleTabs.js#L131) | `agendamento_retorno_changed` / `_tick` | socket cru, morto |
| **[core]** [ToolsUnified.js:112-121](../web/static/js/components/ai/ToolsUnified.js#L112) | `tools_changed` | socket cru, morto — e é o **único consumidor** desse evento no cliente inteiro |

---

## 3. Inventário do trabalho

| # | Item | Onde | O que falta | Risco | Esforço |
|---|---|---|---|---|---|
| 1 | Transporte do Kanban/lista | [protocolos_tab.js:889-897](../storages/plugins/protocolos/static/protocolos_tab.js#L889) | trocar socket cru por `subscribe` do `wsBus` | baixo | S |
| 2 | Debounce + jitter no refetch | idem | coalescer rajadas; jitter contra thundering herd (D4) | médio | S |
| 3 | Guarda de arrasto | `dragRef` já existe ([:1361](../storages/plugins/protocolos/static/protocolos_tab.js#L1361)) | não recarregar com card em voo | baixo | S |
| 4 | Guarda de modal aberto | `detail` state ([:1025-1044](../storages/plugins/protocolos/static/protocolos_tab.js#L1025)) | adiar o refetch enquanto há detalhe/popup aberto | médio | S |
| 5 | Emit **determinístico** no nascimento | [logic.py:973-1018](../storages/plugins/protocolos/logic.py#L973) + [:1926-1928](../storages/plugins/protocolos/logic.py#L1926) | hoje emite por efeito colateral, **só quando a conversa já tem dono** (§2.3); tornar explícito **sem duplicar** (D5 + R12) | médio | M |
| 6 | Emit ao salvar definição de campo | [logic.py:421-459](../storages/plugins/protocolos/logic.py#L421) | `_broadcast_changed(None, None)` (bumpa a geração e cria a coluna nova) | baixo | S |
| 7 | Revalidar `loadMeta` no evento | [protocolos_tab.js:652-677](../storages/plugins/protocolos/static/protocolos_tab.js#L652) | incluir no handler de WS | baixo | S |
| 8 | `agendamento_retorno` + `ToolsUnified` | §2.5 | mesmo conserto de transporte | baixo | S |
| 9 | Cache `_assignableUsers` | [resolve_form.js:27-41](../storages/plugins/protocolos/static/resolve_form.js#L27) | cache de módulo **nunca invalidado** — atendente novo/desativado só aparece no F5 | baixo | S |
| 10 | Seam `subscribe` em `PLUGIN_SERVICES` | [api.js:229-233](../web/static/js/plugins/api.js#L229) | expor (bump MINOR) para o próximo plugin não repetir o erro | baixo | M |
| 11 | Refetch sem resetar paginação | [:771-776](../storages/plugins/protocolos/static/protocolos_tab.js#L771) → [KanbanColumn:476](../storages/plugins/protocolos/static/protocolos_tab.js#L476) | opcional (F8) | alto | L |

### 3.1 Falsos positivos descartados

| Suspeita | Por que NÃO é |
|---|---|
| "Cache das definições de campo no `resolve_form.js`" | Era o suspeito nº 1 e **não se confirmou**. O cache de módulo que existe ali é de **atendentes** (`_assignableUsers`), não de campos — bug adjacente, dimensão diferente (item 9) |
| "Precisa do plano 90 (escopo do WS) antes" | Ortogonal — o 90 trata de vazamento de PII entre operadores. Evento de plugin é fail-open lá (D3 do 90). **Não é pré-requisito nem cria dívida** |
| "Precisa de Redis / multi-réplica" | Não. `bump_generation()` já é cross-réplica porque a geração vive no `config` ([kanban_index.py:85-100](../storages/plugins/protocolos/kanban_index.py#L85)) |
| "O `on`/`emit` do registry de plugin serve para isso" | Não — é barramento **puramente cliente, in-process** ([registry.js:205-215](../web/static/js/plugins/registry.js#L205)), sem relação com o `/ws` |
| "É plugin desatualizado; reinstalar resolve" | Não. As **três** cópias têm o mesmo código: instalada `:893`, fonte de dev `../whatsbot-pro-plugins/plugins/protocolos/src/static/protocolos_tab.js:893`, espelho `assets/plugin_examples/protocolos/static/protocolos_tab.js:829`. Comparado por CONTEÚDO |
| "O `reload()` não existe / o Kanban não tem realtime" | Existe e está correto desde sempre — só nunca recebeu um evento |
| **"O lead novo não emite evento nenhum"** | **Refutado na verificação adversarial** (5 verificadores independentes). `on_inbound` continua **uma linha depois** de `ensure_protocolo_for_contact` e alcança `_broadcast_changed` transitivamente. O problema real é ser **condicional** (§2.3). Agir sobre a leitura errada produziria emit duplo (R12) |

---

## 4. Fases / Roadmap

```
WAVE 0   F0 (caracterização: provar que o evento não chega)          🔴 base
              │
              ├─────────────────┬─────────────────┐
WAVE 1   F1 (transporte+guards) F2 (emit field-defs) F3 (emit lead novo)   🟢 todas paralelas
         └── entrega o caso (c) sozinha
              │                 │
              └────────┬────────┘
WAVE 2   F4 (revalidar loadMeta)          F5 (outros 2 consumidores mortos)  🟢
         └── fecha o caso (a)   [depende de: F1, F2]        [independente]
              │
WAVE 3   F6 (cache de atendentes) · F7 (seam PLUGIN_SERVICES) · F8 (paginação)  🟢 opcionais
```

| Wave | Fase | Workstream | 🟢/🔴 | Risco | Pronto quando |
|---|---|---|---|---|---|
| 0 | F0 | caracterização | 🔴 [bloqueia: tudo] | baixo | Teste prova que o socket cru leva 4401 e que `plugin_protocolos_changed` não chega |
| 1 | F1 | transporte + debounce + guardas | 🟢 | médio | **Caso (c) funciona**: dois navegadores, arrastar num reflete no outro |
| 1 | F2 | emit em `set_field_defs` | 🟢 | baixo | Salvar campo emite 1 evento e bumpa a geração |
| 1 | F3 | emit determinístico no nascimento | 🟢 | médio | **Caso (b)**: lead novo aparece **em todos os cenários**, com **exatamente 1** evento; 2ª mensagem ⇒ 0 |
| 2 | F4 | revalidar catálogo de campos | 🟢 [depende de: F1, F2] | baixo | **Caso (a)**: campo novo aparece no popup aberto pelo Kanban |
| 2 | F5 | `agendamento_retorno` + `ToolsUnified` | 🟢 | baixo | As duas telas voltam a atualizar sozinhas |
| 3 | F6 | cache `_assignableUsers` | 🟢 | baixo | Atendente novo aparece sem F5 |
| 3 | F7 | seam `subscribe` em `PLUGIN_SERVICES` | 🟢 | baixo | Plugin novo escuta evento próprio sem importar caminho interno |
| 3 | F8 | refetch sem resetar paginação | 🟢 | alto | Coluna rolada não volta ao topo (**opcional** — ver P1) |

**Disciplina:** caracterização **antes** (F0); verde a cada fase; **um refactor por commit**; o plugin é editado em `../whatsbot-pro-plugins/plugins/protocolos/src/` e **instalado localmente antes de publicar** (ver R7).

---

### Fase 0 — Caracterização (🔴 base)

**Objetivo:** provar o defeito antes de consertá-lo, para que o "pronto quando" das fases seguintes não seja opinião.

**Itens**
1. `[sequencial]` Teste que abre `/ws` **sem** `?token=` num app com ≥1 usuário e afirma o `close(4401)` ([websocket.py:23-31](../server/routes/websocket.py#L23)). Trava a premissa central.
2. `[paralelo]` Teste que dispara `set_protocolo_field` e captura os broadcasts, afirmando que `plugin_protocolos_changed` **é emitido** (o produtor do caso (c) já está certo — congelar isso evita regressão).
3. `[paralelo]` Teste que roda o caminho de inbound (`message.saved` → `on_inbound`) **contando** os `plugin_protocolos_changed` em **três** cenários: lead novo com conversa que já tem dono (hoje: 1, por efeito colateral), lead novo com conversa sem dono (hoje: **0** — é o bug), e 2ª mensagem do mesmo contato (hoje: 0). ⚠️ Contar, nunca só verificar presença — é o que protege contra o emit duplo da F3 (R12).
4. `[paralelo]` Teste que chama `PUT /field-defs` e afirma que hoje **nenhum** evento sai — caracterização do caso (a).

**Pronto quando:** os 4 testes passam **descrevendo o comportamento atual** (3 deles afirmando a ausência). Nas fases seguintes, os de ausência são **invertidos** — é o sinal de que o conserto pegou.

#### Status de execução — Fase 0
**Estado:** ✅ Concluída (2026-08-06)
- **O que foi feito:** novo arquivo `whatsbot-pro-plugins/plugins/protocolos/tests/python/test_realtime_broadcasts.py` (6 testes). O item 1 (4401 sem token) **já existia** e não foi reescrito: está em [tests/core/legacy/legacy_endpoints.py:5801-5809](../tests/core/legacy/legacy_endpoints.py#L5801), afirmando o código de close exato (`== 4401`, não "alguma exceção").
- **Como foi feito / decisões:** captura por `monkeypatch.setattr(logic, "broadcast", …)` — é o nome que `logic` importou para o próprio namespace e por onde `_broadcast_changed` sai. **Contagem, nunca presença**, como o plano exige: um `>= 1` passaria igual antes e depois e não veria o emit duplo.
- **Problemas / pendências:** nenhum.
- **Verificação:** rodada de caracterização = **3 falhas, 163 passes**, exatamente o previsto:

  | Teste | Antes | Leitura |
  |---|---|---|
  | mover card emite 1 | ✅ | produtor do caso (c) já estava certo — congelado |
  | lead novo **com** dono ⇒ 1 | ✅ | **confirma a correção adversarial**: o caminho JÁ emitia por efeito colateral |
  | lead novo **sem** dono ⇒ 1 | ❌ 0 eventos | o bug do caso (b) |
  | 2ª mensagem ⇒ 0 | ✅ | o D5 já valia |
  | criação com `broadcast_changed=False` ⇒ 0 | ❌ kwarg inexistente | contrato da F3 |
  | salvar field-defs emite | ❌ 0 eventos | o bug do caso (a) |

---

### Fase 1 — Transporte autenticado + debounce + guardas (🟢, entrega o caso (c) sozinha)

**Objetivo:** o evento que já sai do servidor passar a chegar na tela, sem trocar um problema de UX por um de carga.

**Contexto:** o alvo é [protocolos_tab.js:889-897](../storages/plugins/protocolos/static/protocolos_tab.js#L889). O padrão a copiar é literalmente o do `melhorias` ([panel.js:236-239](../storages/plugins/melhorias/static/panel.js#L236)), cujo comentário já registra o diagnóstico: *"Via wsBus do core (conexão única AUTENTICADA — WebSocket cru sem `?token=` é fechado com 4401 sob RBAC e a lista só atualizava no F5)"*.

**Itens**
1. `[sequencial]` Trocar o `new WebSocket(...)` por `import { subscribe } from '/static/js/services/wsBus.js'` + `useEffect(() => subscribe({ plugin_protocolos_changed: onChanged }), [...])`. O `subscribe` devolve a função de unsubscribe — o efeito a retorna direto, como nos precedentes.
2. `[sequencial]` **Debounce com jitter** (D4): coalescer as rajadas numa única execução. Sugestão: janela de ~1,5–3s **mais** um jitter aleatório por cliente, para que N operadores não reconstruam o índice no mesmo instante. *A confirmar na execução: a janela que não deixa a tela lenta nem derruba o cache repetidamente.*
3. `[sequencial]` **Guarda de arrasto**: enquanto `dragRef.current` estiver preenchido ([:1361](../storages/plugins/protocolos/static/protocolos_tab.js#L1361)), adiar o refetch — recarregar embaixo do card em voo é a pior regressão possível deste plano (R2).
4. `[sequencial]` **Guarda de modal**: com detalhe/popup aberto ([:1025-1044](../storages/plugins/protocolos/static/protocolos_tab.js#L1025)), adiar o refetch e executá-lo ao fechar (R3). Ver P2 para a alternativa de aviso passivo.
5. `[paralelo]` Aplicar o mesmo transporte na **lista** (mesmo componente, mesmo `reloadTick`).

**Pronto quando (observável):** dois navegadores logados com operadores diferentes, ambos no Kanban agrupado por **campo de opção**. Arrastar um card de "frio" para "morno" num deles ⇒ em poucos segundos o outro vê o card na coluna nova, **sem F5**. Repetir com agrupamento por **atendente** (arrastar para o Gabriel). Repetir com **Fechado**. E o **controle negativo**: com um card sendo arrastado, um evento que chega **não** puxa o tapete.

#### Status de execução — Fase 1
**Estado:** ✅ Concluída (2026-08-06)
- **O que foi feito:** `protocolos/src/static/protocolos_tab.js` — `import { subscribe as subscribeWs } from '/static/js/services/wsBus.js'`; as constantes `WS_REFRESH_MS`/`WS_REFRESH_JITTER_MS` (1500 + até 1500 ms); o bloco de tempo real substituindo o socket cru; o par de refs `wsTimerRef`/`wsPendingRef`/`wsScheduleRef`/`detailOpenRef`/`modalDepthRef`.
- **Como foi feito / decisões:**
  - **guarda de arrasto** cobre `dragRef` **e** `colDragRef` (arrastar o TÍTULO de uma coluna reordena a view — o plano só citava o card). Arrasto em voo **reagenda**, não descarta: dura segundos;
  - **guarda de modal** virou UM envelope em volta do `api.ui.openModal` (`modalDepthRef`), em vez de flags espalhadas. Cobre os 5 call sites da tela — inclusive o popup "Resolver atendimento" aberto pelo Kanban, que é justamente o do caso (a) — e cobre de graça qualquer modal futuro. Modal aberto **adia** e o `finally` executa o pendente ao fechar; o detalhe (estado local `detail`) tem o efeito gêmeo;
  - `loadMeta().catch(() => {})`: a função não trata erro próprio (o `openDetail` já documenta isso), e sem o catch um 403 vira unhandled rejection a cada evento.
- **Problemas / pendências:** nenhum.
- **Verificação:** `node --input-type=module --check` verde; 56 testes puros de JS do plugin verdes. **Validação manual em dois navegadores ainda não feita** — é o item que exige dois operadores distintos (checklist).

---

### Fase 2 — `set_field_defs` emite (🟢, paralela)

**Objetivo:** salvar a definição de um campo passar a invalidar o que depende dela.

**Itens**
1. `[sequencial]` Ao final de `logic.set_field_defs` ([logic.py:421-459](../storages/plugins/protocolos/logic.py#L421)), chamar `_broadcast_changed(None, None)` — que **bumpa a geração** e emite. O bump é o que resolve o efeito colateral do §2.4: a coluna nova de um campo de opção passa a existir imediatamente, sem esperar o TTL de 30s.
2. `[paralelo]` Conferir que o `PUT` continua auditado ([routes.py:635-644](../storages/plugins/protocolos/routes.py#L635)) — o emit não substitui a auditoria.

**Pronto quando:** o teste de caracterização F0·4 é **invertido** (agora afirma que o evento sai); e acrescentar uma opção ao campo de agrupamento faz a coluna nova aparecer no Kanban de outro operador.

#### Status de execução — Fase 2
**Estado:** ✅ Concluída (2026-08-06)
- **O que foi feito:** `_broadcast_changed(None, None)` ao final de `logic.set_field_defs`.
- **Como foi feito / decisões:** achado no caminho — `set_field_defs` tem **dois chamadores internos** além da rota (`_maybe_backfill` e `_ca_backfill`, migrações de boot atrás de flag no `config`). Não foram suprimidos: rodam **uma vez na vida** da instalação e o `broadcast` é fire-and-forget. Documentado no comentário para quem passar por ali não achar que é vazamento no caminho quente.
- **Problemas / pendências:** nenhum.
- **Verificação:** `test_salvar_definicao_de_campo_emite` invertido de vermelho para verde — afirma o evento **e** que `kanban_index.generation()` subiu (é o bump que cria a coluna nova sem esperar o TTL de 30s).

---

### Fase 3 — Tornar o aviso de lead novo **determinístico** (🟢, paralela — a fase mais delicada)

**Objetivo:** protocolo nascido do inbound aparecer no Kanban **sempre**, e não só quando a conversa já tinha dono (§2.3).

⚠️ **Dois cuidados definem esta fase:**
- **(D5)** `ensure_protocolo_for_contact` é um **get-or-create chamado a cada mensagem**. Emitir no corpo da função emitiria **por mensagem**, derrubando o cache do Kanban de todas as réplicas o tempo todo — o código já documenta esse raciocínio em [logic.py:1921-1929](../storages/plugins/protocolos/logic.py#L1921).
- **(R12)** No caso comum o caminho **já emite** via `_sync_provisional_from_conv`. Um emit novo sem coordenação produz **dois** eventos e **dois** bumps de geração por lead novo.

**Itens**
1. `[sequencial]` Emitir **só no ramo que de fato inseriu** ([logic.py:973-1018](../storages/plugins/protocolos/logic.py#L973)), depois do commit e do `_seed_default_extras`.
2. `[sequencial]` **Evitar o emit duplo.** `stamp_provisional_assignee` já aceita `broadcast_changed` ([logic.py:1883](../storages/plugins/protocolos/logic.py#L1883)) — o parâmetro existe exatamente para isso. Opções: (a) o caminho de criação passa `broadcast_changed=False` no `_sync_provisional_from_conv` subsequente, ficando com o emit explícito do item 1 como fonte única; (b) o item 1 emite apenas quando o sync **não** vai emitir. **Recomendação: (a)** — deixa a fonte única no nascimento, que é o fato que o Kanban precisa saber.
3. `[paralelo]` Caminho gêmeo `on_outbound` ([logic.py:3342](../storages/plugins/protocolos/logic.py#L3342)) — mesmo tratamento.
4. `[paralelo]` `ensure_open_cycle` ([logic.py:3121](../storages/plugins/protocolos/logic.py#L3121)): ciclo novo dentro de protocolo existente muda o que o Kanban mostra? *A confirmar (P3) — cada emit a mais é cache derrubado em todas as réplicas.*

**Pronto quando (observável, com os dois controles):**
- lead novo cuja conversa **não tem** atendente nem agente ⇒ **1** evento e o card aparece (é o caso que hoje falha);
- lead novo cuja conversa **já tem** agente/atendente ⇒ **exatamente 1** evento, não 2 (o controle de não-duplicação, R12);
- **segunda** mensagem do MESMO contato ⇒ **0** eventos (o controle de D5);
- o teste de caracterização F0·3, reescrito para medir a **contagem** de eventos por cenário (não a mera ausência), fica verde nos três.

#### Status de execução — Fase 3
**Estado:** ✅ Concluída (2026-08-06)
- **O que foi feito:** `logic.py` — `ensure_protocolo_ex(...) -> (protocolo, created)` com o emit **dentro do ramo que inseriu**, depois do `_seed_default_extras` e da nota; `ensure_protocolo_for_contact` virou um wrapper fino que devolve só o dict (nenhum chamador quebrou); `_sync_provisional_from_conv` ganhou `broadcast_changed`; `on_inbound`/`on_outbound` passam `broadcast_changed=not created`.
- **Como foi feito / decisões:**
  - **`_ex` em vez de out-param**: segue o precedente do próprio core (`conversation_repo.resolve_for_contact_ex`, que sinaliza `created`/`reopened`). A alternativa de carimbar uma chave `_created` no dict devolvido vazaria para a resposta da API;
  - **antiduplicação (R12) pela opção (a) do plano** — o nascimento é a fonte única. Três chamadores passaram a `broadcast_changed=False`: `open_new_protocolo` e `resolve_atendimento`, que **já emitem** ao final da própria ação, e `_ca_backfill`;
  - ⚠️ **`_ca_backfill` era um risco que o plano não tinha catalogado**: ele cria protocolo **em laço** sobre todas as conversas com atributos legados. Com o emit no ramo de INSERT e sem supressão, o primeiro boot de uma instalação grande dispararia **milhares** de broadcasts e de `bump_generation()` — exatamente o R1, só que no boot. Tem teste próprio.
- **Problemas / pendências:** **P3 respondido: `ensure_open_cycle` NÃO emite.** Um ciclo novo dentro de um protocolo já aberto não muda a linha do protocolo (`status` continua `aberto`) nem a coluna dele em nenhum dos 4 agrupamentos; o que muda de fato (o provisório) já é coberto pelo sync. Emitir ali seria cache derrubado em todas as réplicas sem nada mudar na tela. **P6 respondido: sim, `POST /contacts/{id}/protocolo/ensure` emite** — de graça, porque o emit ficou dentro do ramo de INSERT, como o plano previa.
- **Verificação:** os três controles contados, verdes: sem dono ⇒ **1**, com dono ⇒ **1 (não 2)**, 2ª mensagem ⇒ **0**; mais o controle do laço de backfill ⇒ **0**. 166 testes do plugin verdes, sem regressão nos 163 anteriores.

---

### Fase 4 — Revalidar o catálogo de campos (🟢, depende de F1 + F2)

**Objetivo:** fechar o caso (a) — o campo novo aparecer para quem já está com a aba aberta.

**Itens**
1. `[sequencial]` No handler de WS da F1, chamar também `loadMeta()` — hoje ele só chama `reload()` + `loadViews()` ([:894](../storages/plugins/protocolos/static/protocolos_tab.js#L894)). É o que revalida `cols`, `atendDefs`, `atendResolveDefs` e `contactAttrDefs`. **O alvo é a paridade com o botão Atualizar** ([:1536](../storages/plugins/protocolos/static/protocolos_tab.js#L1536)), que já faz `loadMeta(); reload();` — o handler de WS ficou com metade do par.
2. `[paralelo]` Como `loadMeta` é 4 GETs em paralelo, ele entra **no mesmo debounce** da F1 — não pode escapar do coalescing (R1).
3. `[paralelo]` Confirmar que o popup aberto pelo **chat** continua buscando fresco ([extends.js:94,226](../storages/plugins/protocolos/static/extends.js#L94)) — não regredir a superfície que já funciona.

**Pronto quando:** operador A com a aba Protocolos aberta; operador B cria um campo novo na configuração do plugin; A abre "Resolver atendimento" **pelo Kanban** e o campo está lá, sem F5. E abrindo **pelo chat**, idem (controle positivo da superfície que já funcionava).

#### Status de execução — Fase 4
**Estado:** ✅ Concluída (2026-08-06) — com uma ressalva de escopo (ver P5)
- **O que foi feito:** o handler de WS passou a chamar `loadMeta()` além de `reload()`/`loadViews()` — a paridade com o botão **Atualizar**. Dentro do mesmo debounce da F1, como o item 2 pedia (os 4 GETs de `loadMeta` não podiam escapar do coalescing).
- **Como foi feito / decisões:** o popup aberto pelo **chat** ([extends.js](../storages/plugins/protocolos/static/extends.js)) não foi tocado — ele já refaz o GET a cada clique, e o plano pede não regredir a superfície que funcionava.
- **Problemas / pendências:** ⚠️ **P5 continua em aberto e não bloqueou a execução.** O conserto vale para o popup aberto pelo **Kanban**, que é o que consome o snapshot de `loadMeta`. Se o que o usuário viu foi o popup do **chat**, este conserto não o alcança e falta uma causa — provavelmente o espelho `custom_attribute_definitions` do core, invalidado só por um `window.dispatchEvent` que não cruza aba nem operador ([CustomAttributesManager.js:64](../web/static/js/components/CustomAttributesManager.js#L64)). Executei a F4 mesmo assim porque ela é correta nos dois casos: no pior deles fica incompleta, nunca errada.
- **Verificação:** sintaxe verde. Validação manual (dois operadores) pendente.

---

### Fase 5 — Os outros dois consumidores mortos (🟢, independente)

**Objetivo:** o mesmo defeito existe em mais dois lugares, um deles **no core**. Consertar junto, enquanto o diagnóstico está fresco.

**Itens**
1. `[paralelo]` [agendamento_retorno/ScheduleTabs.js:131](../storages/plugins/agendamento_retorno/static/ScheduleTabs.js#L131) → `subscribe` do `wsBus` (mesmo padrão da F1).
2. `[paralelo]` **[core]** [ToolsUnified.js:112-121](../web/static/js/components/ai/ToolsUnified.js#L112) → usar o `wsBus` por import normal (é core, não precisa de URL absoluta). É o **único** consumidor de `tools_changed` no cliente ([tools.py:70](../server/routes/tools.py#L70)).

**Pronto quando:** editar uma tool numa aba atualiza a tela `/tools` de outra aba; e a lista do `agendamento_retorno` atualiza sozinha.

#### Status de execução — Fase 5
**Estado:** ✅ Concluída (2026-08-06) — **três** consumidores, não dois
- **O que foi feito:** `agendamento_retorno/static/ScheduleTabs.js` e **[core]** `web/static/js/components/ai/ToolsUnified.js` migrados para o `wsBus`. **Achado novo:** uma varredura por `new WebSocket(` encontrou um **terceiro** — `lembretes/static/lembretes.js` — com o mesmo bug; migrado junto (o `onConnect`/`onDisconnect` do bus preserva o indicador "conectado" que a tela mostra, e que estava permanentemente apagado).
- **Como foi feito / decisões:** o `new WebSocket` de [website/static/widget.js:192](../storages/plugins/website/static/widget.js#L192) foi conferido e **deixado como está**: é o widget embarcado no site do cliente, que fala com o endpoint público do canal com token próprio — não é o barramento do painel.
- **Problemas / pendências:** `lembretes` não está instalado em `storages/plugins/`; a correção existe na fonte e no próximo zip.
- **Verificação:** sintaxe verde nos três; varredura final não deixa nenhum `new WebSocket('/ws')` executável no core nem nos plugins instalados (só comentários citando o antipadrão).

---

### Fase 6 — Cache de atendentes nunca invalidado (🟢, opcional)

**Objetivo:** bug adjacente da mesma classe, achado no caminho.

`let _assignableUsers = null` ([resolve_form.js:27-41](../storages/plugins/protocolos/static/resolve_form.js#L27)) é populado no primeiro `AttendantSelect` montado e **nunca** invalidado (`if (_assignableUsers) return undefined;`) — vale por toda a vida da página. Atendente criado ou desativado depois não aparece/não some até um F5.

**Itens**
1. `[sequencial]` Dar TTL ao cache **ou** invalidá-lo no mesmo handler de WS da F1.

**Pronto quando:** criar um usuário atendente e vê-lo no seletor do popup sem recarregar a página.

#### Status de execução — Fase 6
**Estado:** ✅ Concluída (2026-08-06)
- **O que foi feito:** `resolve_form.js` — `ASSIGNABLE_TTL_MS = 60000` + `_assignableAt`, e o novo export `invalidateAssignableUsers()`, chamado pelo handler de WS da F1.
- **Como foi feito / decisões:** o plano oferecia "TTL **ou** invalidação"; ficaram **os dois**, porque resolvem coisas diferentes. Criar um usuário no core não emite `plugin_protocolos_changed` — só o TTL cobre esse caso. E o TTL de 60 s é lento demais para quem acabou de mexer no protocolo — a invalidação por evento cobre esse. Enquanto a busca não volta, o seletor continua mostrando a lista antiga em vez de piscar vazio.
- **Problemas / pendências:** nenhum.
- **Verificação:** sintaxe verde; testes puros do plugin verdes.

---

### Fase 7 — Seam `subscribe` em `PLUGIN_SERVICES` (🟢, estrutural, opcional)

**Objetivo:** que o **próximo** plugin não repita o erro. Hoje não existe caminho suportado: `api.services` expõe apenas `useWebSocket`, cujo mapa de eventos é **fixo nos nomes do core** ([useWebSocket.js:10-58](../web/static/js/hooks/useWebSocket.js#L10)) — um plugin literalmente **não consegue escutar o evento que ele mesmo emite**. Foi essa ausência que produziu os três sockets crus.

**Itens**
1. `[sequencial]` Expor `subscribe` do `wsBus` em `buildPluginApi` ([api.js:229-233](../web/static/js/plugins/api.js#L229)) — adição **MINOR** (`PLUGIN_SERVICES_VERSION` 2.0 → 2.1), nunca remoção.
2. `[paralelo]` Documentar no CLAUDE.md (seção de plugins) que socket cru é proibido e por quê.
3. `[paralelo]` Migrar `protocolos`, `melhorias`, `retornos` e `agendamento_retorno` para o seam oficial — com **feature detection** (`api.services.subscribe || import(...)`), já que um plugin novo pode rodar em core anterior.

⚠️ Só **depois** da F1: o import por URL absoluta já resolve os casos do usuário (D2), e esta fase é higiene de plataforma, não pré-requisito.

**Pronto quando:** um plugin declarando `plugin_services_version: ">=2.1,<3.0"` escuta um evento próprio sem importar caminho interno do core; e os plugins migrados continuam funcionando num core sem o seam.

#### Status de execução — Fase 7
**Estado:** ✅ Itens 1 e 2 concluídos (2026-08-06) · **item 3 deliberadamente não executado**
- **O que foi feito:** `web/static/js/plugins/api.js` — `subscribe` exposto em `api.services`; `PLUGIN_SERVICES_VERSION` 2.0 → **2.1** e `'2.1'` acrescentado a `SUPPORTED_PLUGIN_SERVICES_VERSIONS` (adição pura, nada removido, MINOR conforme o contrato do arquivo). CLAUDE.md: parágrafo novo em "Frontend dinâmico" proibindo o socket cru **com o porquê** (o 4401 silencioso), mais duas linhas nas convenções obrigatórias.
- **Como foi feito / decisões:** `subscribe` entra pelos `extras` do `buildAllowedServices`, ao lado de `useWebSocket` — não pela allowlist derivada de `coreApi`, de onde não vem (mora em `services/wsBus.js`). A negociação de superfície do repo é **por MAJOR** (`buildVersionedServiceSurface` só ramifica em `.split('.')[0]`), então 2.1 se comporta como 2.0 e o range `>=2.0,<3.0` de um manifesto existente passa a resolver para `2.1` sem nada mudar para ele.
- **Problemas / pendências:** **item 3 (migrar os 4 plugins para `api.services.subscribe` com feature detection) NÃO foi feito, de propósito.** Os plugins já importam `subscribe` estaticamente de `/static/js/services/wsBus.js` — o **mesmo objeto de função** que `api.services.subscribe` devolve, do mesmo módulo. Um `api.services.subscribe || subscribeWs` seria cerimônia: sugeriria um caminho alternativo que não existe e que nunca é exercitado. O seam vale para plugin **novo**, que é onde o plano o justifica ("que o próximo plugin não repita o erro"); e o import por URL absoluta continua explicitamente autorizado pelo CLAUDE.md. Reverter essa decisão custa 2 linhas por plugin.
- **Verificação:** `node --test web/static/js/plugins/*.test.js` — 8 verdes (nenhum teste fixava a versão em `'2.0'`; os de `versionCompat` usam listas locais).

---

### Fase 8 — Refetch sem resetar a paginação (🟢, opcional — ver P1)

**Objetivo:** tirar o desconforto do `reload()` grosso.

**Contexto:** `reloadTick` entra no `resetKey` ([:771-776](../storages/plugins/protocolos/static/protocolos_tab.js#L771)) que cada coluna repassa ao seu `useInfiniteScroll` ([KanbanColumn:476](../storages/plugins/protocolos/static/protocolos_tab.js#L476)) — um reload **joga todas as colunas de volta à primeira página**. Quem rolou 200 cards volta ao topo.

**A peça que torna isso viável:** `grouping.columnIdOf(row)` é uma **função pura já existente no cliente**, válida para todos os agrupamentos. Dá para buscar **só o protocolo afetado** (o payload traz `protocolo_id`) e reposicioná-lo, sem refetch geral. Os totais de coluna vêm do índice do servidor e precisariam de ajuste ou de um refetch barato só de contagens.

**Itens**
1. `[sequencial]` Patch local do card via `columnIdOf`, com fallback para `reload()` quando o protocolo não estiver na janela carregada ou não casar os filtros ativos.
2. `[paralelo]` Decidir o tratamento dos totais de cabeçalho.

**Pronto quando:** com uma coluna rolada até o fim, um evento de outro operador move o card sem que a coluna volte ao topo.

#### Status de execução — Fase 8
**Estado:** ⏸️ **Não executada de propósito** — é a recomendação (a) do P1: medir o incômodo com a F1 em uso real antes de pagar a única fase de risco alto do plano. O `reload()` grosso é o que quem arrasta já vive hoje.
- **O que foi feito:** _(preencher ao executar — arquivos/funções que mudaram)_
- **Como foi feito / decisões:** _(escolhas tomadas e o porquê; desvios do plano)_
- **Problemas / pendências:** _(o que deu errado, o que ficou para depois, o que precisa de decisão)_
- **Verificação:** _(testes rodados + resultado verde/vermelho; validação manual)_

---

## 5. Riscos e cuidados

| # | Ponto | Risco | Mitigação |
|---|---|---|---|
| R1 | **Thundering herd** | `bump_generation` invalida o índice em **todas as réplicas**; N operadores refazem uma varredura de até 20.000 protocolos ao mesmo tempo ([kanban_index.py:32-45](../storages/plugins/protocolos/kanban_index.py#L32)) | Debounce **com jitter por cliente** (D4), na mesma fase que liga o transporte. Nunca ligar F1 sem o item 2 dela |
| R2 | **Recarregar embaixo do arrasto** | Card em voo desaparece/salta — pior que o bug original | Guarda de `dragRef` (F1·3). É controle negativo obrigatório no "pronto quando" |
| R3 | **Formulário aberto sendo descartado** | Refetch por baixo do popup "Resolver" perde o que o operador digitou | Guarda de modal (F1·4); executar o refetch adiado ao fechar. Ver P2 |
| R4 | **Paginação resetada** | Coluna rolada volta ao topo a cada evento | Aceito na F1 (é o que quem arrasta já vive hoje, [:1257](../storages/plugins/protocolos/static/protocolos_tab.js#L1257)); F8 refina |
| R5 | **Emitir por mensagem** | Derrubaria o cache do Kanban continuamente — o código já alerta contra isso | D5: emit **dentro do ramo de INSERT**; controle negativo explícito no "pronto quando" da F3 |
| R6 | **Eco para o próprio autor** | Quem arrastou recarrega junto | Aceito (D7) — é o comportamento atual. `performer` pertence ao [plano 44](44-avaliacao-realtime-websocket-vs-chatwoot.md) §7.2·16 |
| R7 | **Três cópias do plugin** | Editar a errada entrega a versão errada ao cliente | Editar em `../whatsbot-pro-plugins/plugins/protocolos/src/`, **instalar em `storages/plugins/` e testar antes de publicar**, e só então gerar o zip. As três hoje têm o mesmo bug (§3.1) |
| R8 | **Plano 90 futuro** | Um escopo de WS mal calibrado poderia calar o evento do plugin | Fail-open é decisão travada lá (D3 do 90): evento não classificado **entrega**. Registrar `plugin_protocolos_changed` como esperado nessa categoria |
| R9 | **Bump de `plugin_services_version`** | F7 mexe numa superfície versionada | Adição = MINOR (2.0 → 2.1); **nunca** remover nome existente. Plugin migrado precisa de feature detection para rodar em core anterior |
| R10 | **Falha silenciosa** | O socket cru engole todo erro ([:892-895](../storages/plugins/protocolos/static/protocolos_tab.js#L892)): sem `onerror`, sem `onclose`, sem log. **O código funcionava quando foi escrito** — quebrou em `605be2d` (2026-07-15, "fecha o gate da API/WS quando existe ≥1 usuário", plano 48 F0), e ninguém percebeu porque a única evidência seria um frame de close que ninguém observa | O `wsBus` já loga e reconecta. **Não reintroduzir `try/catch` mudo** em volta do `subscribe` — foi o silêncio, mais que o bug, que custou os meses |
| R11 | **Modo escuro** | Se a F1/F8 acrescentar indicador visual ("atualizando…", "há novidades") | Classes `wa-*`, testado nos dois temas |
| R12 | **Emit duplo no lead novo** | O caminho de criação **já emite** por efeito colateral quando a conversa tem dono (§2.3). Um emit novo sem coordenação ⇒ 2 eventos e 2 bumps de geração por lead | F3·2: usar o parâmetro `broadcast_changed` que `stamp_provisional_assignee` já expõe ([logic.py:1883](../storages/plugins/protocolos/logic.py#L1883)). Controle de contagem no "pronto quando" da F3 — **contar** eventos, não só verificar presença |

---

## 6. Perguntas em aberto

**P1 — A F8 (refetch cirúrgico) vale a pena?**
⏸️ **ADIADO — decidir depois da F1 em uso real.** O `reload()` grosso é o que quem arrasta já vive hoje, e pode ser suficiente. Opções: (a) medir o incômodo com a F1 em produção por alguns dias e só então decidir; (b) fazer a F8 junto, assumindo complexidade alta antes de saber se é necessária. **Recomendação: (a)** — F8 é a única fase de risco alto do plano, e a evidência para justificá-la ainda não existe.

**P2 — Com modal/popup aberto: adiar o refetch ou avisar passivamente?**
⏸️ **DECISÃO DE PRODUTO.** (a) Adiar em silêncio e executar ao fechar — simples, mas o operador decide sobre dados velhos; (b) mostrar um aviso discreto ("há alterações novas — atualizar") e deixar o clique com ele. **Recomendação: (a) na F1** (é o comportamento menos surpreendente), com (b) como refinamento se aparecer queixa.

**P3 — `ensure_open_cycle` deve emitir?**
✅ **RESPONDIDO na F3: não.** Um ciclo novo dentro de um protocolo já aberto não muda a linha do protocolo (o `status` continua `aberto`) nem a coluna dele em nenhum dos 4 agrupamentos. O que de fato muda — o atendente provisório — já é coberto pelo sync logo em seguida. Emitir ali seria derrubar o cache de todas as réplicas sem nada mudar na tela (R1).

**P5 — Qual dos dois popups "Resolver atendimento" o usuário viu? ⚠️ Confirmar ANTES da F4.**
⏸️ **PERGUNTA AO USUÁRIO — é o único ponto do plano que depende de informação que não está no código.** Existem duas superfícies (§2.4): pelo **Kanban** (usa o snapshot de `loadMeta` — o plano explica e conserta) e pelo **chat** (refaz o `GET` a cada clique — **não deveria** reproduzir o sintoma). Se o relato for do popup do **chat**, falta uma causa: provavelmente o espelho no core (`custom_attribute_definitions`), cuja invalidação hoje é um `window.dispatchEvent('whatsbot:custom-attributes-changed')` ([CustomAttributesManager.js:64](../web/static/js/components/CustomAttributesManager.js#L64)) — **evento de janela, que nunca cruza para outra aba nem para outro operador**. **Recomendação:** perguntar antes de executar a F4; se for o do chat, acrescentar uma fase para esse espelho.

**P6 — `POST /contacts/{id}/protocolo/ensure` também deve emitir?**
✅ **RESPONDIDO na F3: sim, e saiu de graça** — como previsto, o emit ficou dentro do ramo de INSERT de `ensure_protocolo_ex`, então a rota ([routes.py:258-275](../storages/plugins/protocolos/routes.py#L258)) passou a avisar sem uma linha própria.

**P4 — A F7 (seam oficial) entra nesta rodada ou vira plano próprio?**
⏸️ **ADIADO.** Ela não é pré-requisito de nada aqui (D2) e mexe numa superfície versionada do core. Opções: (a) executar junto, aproveitando o contexto; (b) plano separado de higiene de plataforma, junto com a atualização do CLAUDE.md. **Recomendação: (a)** — é pequena, e a cada plugin novo o custo de não ter o seam se repete.

---

## 7. Checklist de verificação

Por fase:
- [x] F0: os testes de caracterização descreveram o comportamento **atual** (3 vermelhos, exatamente os previstos)
- [x] F1: automatizado verde. ⬜ **manual**: caso (c) nos 3 agrupamentos + controle negativo do arrasto
- [x] F2: caracterização invertida; o teste afirma o evento **e** o bump da geração
- [x] F3: os **três** cenários contados verdes — sem dono ⇒ 1 · com dono ⇒ **1 (não 2)** · 2ª mensagem ⇒ 0 — mais o laço de backfill ⇒ 0
- [x] F4: código verde. ⬜ **manual**, e ver **P5** (a superfície do chat pode ter causa própria)
- [x] F5: três consumidores migrados (o `lembretes` era um achado novo). ⬜ **manual** entre abas
- [x] F6: TTL de 60 s + invalidação por evento
- [x] F7: `subscribe` exposto, versão 2.1, CLAUDE.md atualizado. Item 3 (migrar plugins existentes) dispensado com justificativa

Transversal:
- [x] Suíte do core verde no Postgres de teste (`WHATSBOT_TEST_DB_URL`) — `venv/bin/python -m pytest`
- [x] Runner do plugin verde: `python3 scripts/test_plugins.py protocolos` — **166 passes**
- [x] `node --test` verde: 8 (core `plugins/`) + 56 (puros do `protocolos`)
- [x] **Nenhuma migration** (este plano não tocou o schema)
- [x] Nenhum segredo na URL — o `?token=` do `wsBus` é o mecanismo **existente**; este plano não o ampliou (o achado #6 do [registro 45](45-registro-bugs-riscos-realtime.md) segue sendo plano à parte)
- [x] Modo escuro: nada de indicador visual novo foi acrescentado, então não há superfície a testar
- [x] Plugins sincronizados para `storages/plugins/` (`protocolos` 1.28.0, `agendamento_retorno` 1.5.0) — **é a cópia que roda**
- [x] Bump de versão nos `plugin.yaml`
- [ ] Zip republicado no `whatsbot-pro-plugins` (`scripts/build_plugins.py`) — **depois** da validação manual
- [ ] **Dois navegadores, dois operadores distintos** — a validação manual dos 3 casos não pode ser feita em duas abas do mesmo login
- [ ] **P5 respondido** pelo usuário (qual popup "Resolver" estava desatualizado)

---

## 8. Apêndice — arquivos-chave

**Plugin `protocolos`** (fonte em `../whatsbot-pro-plugins/plugins/protocolos/src/`)
- [static/protocolos_tab.js](../storages/plugins/protocolos/static/protocolos_tab.js) — §889-897 (transporte), §652-677 (`loadMeta`), §706-707 + §771-776 (`reloadTick`/`resetKey`), §1218-1257 (`applyDrop`)
- [logic.py](../storages/plugins/protocolos/logic.py) — §973-1018 (nascimento), §421-459 (`set_field_defs`), §5050-5060 (`_broadcast_changed`), §1921-1929 (o alerta de custo)
- [static/resolve_form.js](../storages/plugins/protocolos/static/resolve_form.js) — §27-41 (cache de atendentes)
- [kanban_index.py](../storages/plugins/protocolos/kanban_index.py) · [grouping.py](../storages/plugins/protocolos/grouping.py) — lidos, não alterados (entender o custo do bump)

**Outros plugins**
- [agendamento_retorno/static/ScheduleTabs.js](../storages/plugins/agendamento_retorno/static/ScheduleTabs.js) — §131
- [melhorias/static/panel.js](../storages/plugins/melhorias/static/panel.js) — §14, §236-239 — **o padrão a copiar**

**Core** (só F5 e F7)
- [web/static/js/components/ai/ToolsUnified.js](../web/static/js/components/ai/ToolsUnified.js) — §112-121
- [web/static/js/plugins/api.js](../web/static/js/plugins/api.js) — §229-233 (F7)
- [web/static/js/services/wsBus.js](../web/static/js/services/wsBus.js) · [web/static/js/hooks/useWebSocket.js](../web/static/js/hooks/useWebSocket.js) — lidos, não alterados

**Registros relacionados**
- [44 — avaliação realtime vs Chatwoot](44-avaliacao-realtime-websocket-vs-chatwoot.md) — o motor e por que ele é sadio
- [45 — registro de bugs realtime](45-registro-bugs-riscos-realtime.md) — achado #6 (token na URL) fica fora
- [90 — escopo do WS por canal](90-plano-escopo-do-websocket-por-canal.md) — **P4 daquele plano já havia registrado os 3 sockets sem token**, como "higiene à parte". Este plano é onde essa nota de rodapé vira o trabalho
