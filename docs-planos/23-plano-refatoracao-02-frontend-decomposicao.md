# Plano 23 · Sub-plano 02 — Frontend: decomposição (sem build step)

> Parte do [Plano 23 — Mestre](23-plano-refatoracao-00-mestre.md). Arquitetura-alvo frontend: ver §2.5 no mestre.

## Fases (workstream D)

### WAVE 0

#### Fase D0 — Helpers front puros + allowlist congelada + type-check opt-in 🟢
- **Objetivo:** R1/R2/R12/R13 + `jsconfig.json` (checkJs OFF global, `// @ts-check` por arquivo novo). **CRÍTICO:** **congelar `PLUGIN_SERVICES` allowlist ANTES** de qualquer split de `api.js` (grandfathering o que `atendimentos` importa) + check (jsconfig) de que JS de plugin só importa paths allowlisted.
- **Arquivos:** `utils/phone.js`, `services/{httpClient,messages,conversationPatch}.js`, `plugins/api.js` (allowlist), `jsconfig.json`, CI non-blocking. Re-export de fachada em `api.js` p/ não quebrar imports.
- **Risco:** baixo.

### WAVE 4 — Frontend: decomposição (médio — rede = checkJs opt-in + node --test em puros)

#### Fase D1 — Slots novos + emissão `ui.*` + allowlist (priority já existe) 🔴
- **Objetivo:** §4.1/§3.4 — adicionar **2** slots novos nos pontos de render (`sidebar.row.badges`, `chat.header.banner`); fazer o core **chamar `registry.emit`** nos `ui.conversation.*`; trocar `...coreApi` por allowlist (já **congelada** em D0). **Documentar** `overrideRoute`+`ModalHost` como contratos. **Aditivo** = não quebra o atendimento.
- **Risco:** baixo-médio.

#### Fase D2 — Decompor `Contacts.js` (1849) 🔴
- **Objetivo:** 7 hooks + `services/conversationRows.js` (puro); container fino. **Documentar** como `overrideRoute('attendances')` interage com o novo boundary (compõe vs. sobrepõe a rota do chat) — ou quebra o plugin silenciosamente.
- **Caracterização antes:** `node --test` de `conversationRows` (`buildRows`/`clauseMatches`) + smoke de WS handlers.
- **Risco:** médio.

#### Fase D3 — Decompor `ContactDetail.js` (1754) 🔴
- **Objetivo:** `useComposer`/`useAudioRecorder`/`useMediaUpload`/`useTokenAutocomplete`/`useMessageActions` + `MessageBubble`/`SystemMessageCard` (data-driven, corrige cores cruas tipo `#fef2f2` → `wa-*`)/`MediaContent`/`Composer`.
- **Atenção:** compositor/optimistic-send/@menções é o **mais sensível** e **não há runner de comportamento** (só `node --test` em lógica pura). **Diff mínimo por commit; é a última e mais conservadora.**
- **Risco:** médio.

#### Fase D4 — `ChannelsManager.js` (1447) + `app.js` (906) 🟢
- **Objetivo:** `components/channels/*`; `screenRegistry` declarativo + `ScreenRouter`/`GearMenu`/`AuthGate`; `wsBus.js` singleton (N sockets→1, inventário backend de WS em §1.4). Posicionar `SetupWizard.js`, `LowBalanceModal`, subtree `ai/` (mesmo que "sem mudança").
- **Risco:** médio.
